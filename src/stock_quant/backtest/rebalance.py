"""Project ideal target quantities onto executable orders for one open.

The projector is a read-only pre-trade step.  It uses the real account state,
the execution-date bars and the active cost/rule models to remove orders the
executor can already know will fail.  Sells are limited to T+1 sellable lots;
their estimated net proceeds then fund buys in deterministic symbol order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Mapping

import pandas as pd

from stock_quant.backtest.account import Account
from stock_quant.backtest.costs import CostModel
from stock_quant.backtest.execution import (
    EXECUTION_BAR_REQUIRED_COLUMNS,
    _is_error_severity,
    _optional_decimal,
    _optional_int,
)
from stock_quant.backtest.models import (
    BUY,
    LOT_SIZE,
    REASON_INSUFFICIENT_CASH,
    REASON_INSUFFICIENT_SELLABLE_QUANTITY,
    REASON_MISSING_OPEN,
    REASON_MISSING_PRE_CLOSE,
    REASON_QUALITY_ERROR,
    REASON_SUSPENDED_OR_UNKNOWN,
    REASON_UNCOVERED_RULE,
    SELL,
    Order,
    require_side,
)
from stock_quant.data_model.trading_rules import TradingRuleBook, UncoveredRuleError

REASON_LOT_ROUNDING = "lot_rounding"


@dataclass(frozen=True)
class RebalanceAdjustment:
    """One ideal order reduced or removed before it reaches the executor."""

    trade_date: date
    side: str
    symbol: str
    requested_quantity: int
    executable_quantity: int
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.trade_date, date):
            raise TypeError(f"trade_date must be a date, got {self.trade_date!r}")
        require_side(self.side)
        if not self.symbol or not self.reason:
            raise ValueError("symbol and reason must be non-empty")
        if self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be positive")
        if not 0 <= self.executable_quantity <= self.requested_quantity:
            raise ValueError(
                "executable_quantity must lie in [0, requested_quantity]"
            )

    @property
    def rejected_quantity(self) -> int:
        return self.requested_quantity - self.executable_quantity

    @property
    def submitted_quantity(self) -> int:
        """Alias used when serialising the pre-trade audit ledger."""
        return self.executable_quantity


@dataclass(frozen=True)
class RebalanceProjection:
    """Immutable executable orders, adjustments and resulting target book."""

    sells: tuple[Order, ...]
    buys: tuple[Order, ...]
    adjustments: tuple[RebalanceAdjustment, ...]
    executable_target_quantities: Mapping[str, int]

    def __post_init__(self) -> None:
        sells = tuple(self.sells)
        buys = tuple(self.buys)
        adjustments = tuple(self.adjustments)
        if any(order.side != SELL for order in sells):
            raise ValueError("sells must contain only SELL orders")
        if any(order.side != BUY for order in buys):
            raise ValueError("buys must contain only BUY orders")
        frozen_targets = MappingProxyType(
            dict(sorted(self.executable_target_quantities.items()))
        )
        object.__setattr__(self, "sells", sells)
        object.__setattr__(self, "buys", buys)
        object.__setattr__(self, "adjustments", adjustments)
        object.__setattr__(self, "executable_target_quantities", frozen_targets)


def project_rebalance(
    target_quantities: Mapping[str, int],
    account: Account,
    bars: pd.DataFrame,
    trade_date: date,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
) -> RebalanceProjection:
    """Return the executable part of an ideal target book for ``trade_date``.

    Only the supplied execution-date bars are read.  The account is never
    mutated: sell proceeds and buy costs are quoted into a local cash budget.
    """
    _validate_inputs(target_quantities, account, bars, trade_date)
    day_bars = _execution_date_bars(bars, trade_date)
    rows = _rows_by_symbol(day_bars)
    targets = dict(target_quantities)
    current = _position_quantities(account)
    executable_targets = {
        symbol: current.get(symbol, 0)
        for symbol in sorted(set(current) | set(targets))
    }

    sells: list[Order] = []
    buys: list[Order] = []
    adjustments: list[RebalanceAdjustment] = []
    cash_budget = account.cash

    for symbol in sorted(executable_targets):
        requested = current.get(symbol, 0) - targets.get(symbol, 0)
        if requested <= 0:
            continue
        reason = _static_reason(
            SELL, symbol, rows, trade_date, cost_model, rule_book
        )
        if reason is not None:
            adjustments.append(
                _adjustment(trade_date, SELL, symbol, requested, 0, reason)
            )
            continue

        requested_lots = _whole_lots(requested)
        sellable_lots = _whole_lots(account.sellable_quantity(symbol, trade_date))
        executable = min(requested_lots, sellable_lots)
        if executable:
            order = _order(trade_date, SELL, symbol, executable)
            sells.append(order)
            quote = cost_model.calculate(
                SELL, executable, rows[symbol]["open"], trade_date
            )
            cash_budget += quote.gross - quote.commission - quote.stamp_tax
            executable_targets[symbol] -= executable
        if executable < requested:
            reason = (
                REASON_INSUFFICIENT_SELLABLE_QUANTITY
                if sellable_lots < requested_lots
                else REASON_LOT_ROUNDING
            )
            adjustments.append(
                _adjustment(
                    trade_date, SELL, symbol, requested, executable, reason
                )
            )

    for symbol in sorted(executable_targets):
        requested = targets.get(symbol, 0) - current.get(symbol, 0)
        if requested <= 0:
            continue
        reason = _static_reason(
            BUY, symbol, rows, trade_date, cost_model, rule_book
        )
        if reason is not None:
            adjustments.append(
                _adjustment(trade_date, BUY, symbol, requested, 0, reason)
            )
            continue

        requested_lots = _whole_lots(requested)
        executable = _largest_affordable_quantity(
            requested_lots,
            rows[symbol]["open"],
            cash_budget,
            trade_date,
            cost_model,
        )
        if executable:
            order = _order(trade_date, BUY, symbol, executable)
            buys.append(order)
            quote = cost_model.calculate(
                BUY, executable, rows[symbol]["open"], trade_date
            )
            cash_budget -= quote.gross + quote.commission
            executable_targets[symbol] += executable
        if executable < requested:
            reason = (
                REASON_INSUFFICIENT_CASH
                if executable < requested_lots
                else REASON_LOT_ROUNDING
            )
            adjustments.append(
                _adjustment(trade_date, BUY, symbol, requested, executable, reason)
            )

    return RebalanceProjection(
        sells=tuple(sells),
        buys=tuple(buys),
        adjustments=tuple(adjustments),
        executable_target_quantities=executable_targets,
    )


def _validate_inputs(
    target_quantities: Mapping[str, int],
    account: Account,
    bars: pd.DataFrame,
    trade_date: date,
) -> None:
    if not isinstance(target_quantities, Mapping):
        raise TypeError("target_quantities must be a mapping")
    if not isinstance(account, Account):
        raise TypeError(f"account must be an Account, got {type(account).__name__}")
    if not isinstance(bars, pd.DataFrame):
        raise TypeError(f"bars must be a pandas DataFrame, got {type(bars).__name__}")
    if not isinstance(trade_date, date):
        raise TypeError(f"trade_date must be a date, got {trade_date!r}")
    for symbol, quantity in target_quantities.items():
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"target symbol must be non-empty, got {symbol!r}")
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise TypeError(
                f"target quantity for {symbol} must be an int, got {quantity!r}"
            )
        if quantity < 0:
            raise ValueError(
                f"target quantity for {symbol} must be non-negative: {quantity}"
            )


def _execution_date_bars(bars: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    """Select only ``trade_date`` when callers supply a multi-day frame."""
    if "trade_date" not in bars.columns:
        return bars
    bar_dates = pd.to_datetime(bars["trade_date"]).dt.date
    return bars.loc[bar_dates == trade_date]


def _rows_by_symbol(bars: pd.DataFrame) -> dict[str, Mapping[str, object]]:
    missing = [
        column for column in EXECUTION_BAR_REQUIRED_COLUMNS if column not in bars
    ]
    if missing:
        raise ValueError(
            "execution bars must include columns "
            f"{list(EXECUTION_BAR_REQUIRED_COLUMNS)}; missing {missing}"
        )
    symbols = list(bars["symbol"])
    if len(symbols) != len(set(symbols)):
        raise ValueError("execution bars must have at most one row per symbol")
    return {str(row["symbol"]): row for row in bars.to_dict("records")}


def _position_quantities(account: Account) -> dict[str, int]:
    quantities: dict[str, int] = {}
    for lot in account.lots:
        quantities[lot.symbol] = quantities.get(lot.symbol, 0) + lot.quantity
    return quantities


def _static_reason(
    side: str,
    symbol: str,
    rows: Mapping[str, Mapping[str, object]],
    trade_date: date,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
) -> str | None:
    """Mirror the executor's execution-date bar and price-limit pre-checks."""
    row = rows.get(symbol)
    if row is None:
        return REASON_SUSPENDED_OR_UNKNOWN
    open_price = _optional_decimal(row.get("open"))
    pre_close = _optional_decimal(row.get("pre_close"))
    if open_price is None or open_price <= 0:
        return REASON_MISSING_OPEN
    if pre_close is None or pre_close <= 0:
        return REASON_MISSING_PRE_CLOSE
    if _is_error_severity(row.get("quality_severity")):
        return REASON_QUALITY_ERROR
    status = row.get("status")
    status = "NORMAL" if status is None else str(status)
    if status.upper() == "SUSPENDED":
        return REASON_SUSPENDED_OR_UNKNOWN
    listed_sessions = _optional_int(row.get("listed_sessions"))
    try:
        limits = rule_book.price_limits(
            symbol,
            trade_date,
            pre_close,
            status=status,
            listed_sessions=listed_sessions,
        )
    except UncoveredRuleError:
        return REASON_UNCOVERED_RULE
    fill_price = cost_model.fill_price(side, open_price, trade_date)
    return limits.block_reason(side.lower(), fill_price)


def _largest_affordable_quantity(
    requested: int,
    open_price: object,
    cash_budget,
    trade_date: date,
    cost_model: CostModel,
) -> int:
    for quantity in range(requested, 0, -LOT_SIZE):
        quote = cost_model.calculate(BUY, quantity, open_price, trade_date)
        if quote.gross + quote.commission <= cash_budget:
            return quantity
    return 0


def _whole_lots(quantity: int) -> int:
    return quantity // LOT_SIZE * LOT_SIZE


def _order(trade_date: date, side: str, symbol: str, quantity: int) -> Order:
    return Order(
        order_id=(
            f"rebalance-{trade_date.strftime('%Y%m%d')}-{side.lower()}-{symbol}"
        ),
        side=side,
        symbol=symbol,
        quantity=quantity,
        note="executable_rebalance",
    )


def _adjustment(
    trade_date: date,
    side: str,
    symbol: str,
    requested: int,
    executable: int,
    reason: str,
) -> RebalanceAdjustment:
    return RebalanceAdjustment(
        trade_date=trade_date,
        side=side,
        symbol=symbol,
        requested_quantity=requested,
        executable_quantity=executable,
        reason=reason,
    )
