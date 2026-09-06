"""Execution simulation over execution-date bars into a T+1 account (Task 9).

The simulator is the order/execution core the weekly backtest engine drives.
It pre-checks every ``Order`` and records an exact-reason rejection for each
bar or rule problem, then executes accepted sells before buys (so same-day
sell proceeds fund buys).  A buy whose full target quantity is not affordable
is filled at the largest whole-100-share-lot quantity that fits and its
unaffordable remainder is rejected for cash -- never a partial lot, never an
overdraft, never a silent skip, and target ranking is never recomputed: the
engine's order list *is* the precomputed priority order.

Execution bars are the caller's documented contract.  One unadjusted bar per
symbol on the execution date is required with columns
``EXECUTION_BAR_REQUIRED_COLUMNS``; an absent row for a symbol is treated as
suspended/unknown.  Optional columns ``status`` (default ``"NORMAL"``) and
``listed_sessions`` (default ``None``) are read when present and forwarded to
the price-limit book.  No future price is ever inspected.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Mapping

import pandas as pd

from stock_quant.backtest.account import Account
from stock_quant.backtest.costs import CostModel
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
    ExecutionResult,
    FeeBreakdown,
    Fill,
    Order,
    RejectedOrder,
    as_decimal,
)
from stock_quant.data_model.trading_rules import TradingRuleBook, UncoveredRuleError
from stock_quant.data_quality.models import Severity

#: Required execution-date bar columns. ``pre_close`` is the previous session's
#: unadjusted close, used only to lock the price-limit band; the engine (Task
#: 10) attaches it. ``quality_severity`` is the established annotation column.
EXECUTION_BAR_REQUIRED_COLUMNS = ("symbol", "open", "pre_close", "quality_severity")

#: Optional bar columns recognised when present.
EXECUTION_BAR_OPTIONAL_COLUMNS = ("status", "listed_sessions")


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return as_decimal(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return int(value)


def _is_error_severity(value: object) -> bool:
    """True when the bar's ``quality_severity`` is ERROR or worse (FATAL)."""
    if isinstance(value, Severity):
        return value.rank >= Severity.ERROR.rank
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    text = str(value).strip().upper()
    if not text:
        return False
    try:
        return Severity(text).rank >= Severity.ERROR.rank
    except ValueError:
        return False


class ExecutionSimulator:
    """Drives an ``Account`` through an ordered order list against one day's bars."""

    def __init__(self, *, cost_model: CostModel, rule_book: TradingRuleBook) -> None:
        self._cost = cost_model
        self._rules = rule_book
        self._fill_seq = 0

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def execute(
        self,
        orders: list[Order] | tuple[Order, ...],
        bars: pd.DataFrame,
        account: Account,
        trade_date: date,
    ) -> ExecutionResult:
        """Execute ``orders`` on ``trade_date``, mutating ``account``.

        Every submitted order is recorded on the account (audit), then each
        order is pre-checked and rejected with an exact reason when its bar or
        the rule book blocks it; accepted sells run before accepted buys.
        """
        if not isinstance(bars, pd.DataFrame):
            raise TypeError(
                f"bars must be a pandas DataFrame, got {type(bars).__name__}"
            )
        self._validate_bars(bars)
        if not isinstance(account, Account):
            raise TypeError(
                f"account must be an Account, got {type(account).__name__}"
            )
        rows = self._rows_by_symbol(bars)
        fills: list[Fill] = []
        rejections: list[RejectedOrder] = []

        orders = list(orders)
        for order in orders:
            account.record_order(order)

        accepted: list[Order] = []
        for order in orders:
            reason = self._static_reason(order, rows, trade_date)
            if reason is not None:
                rejections.append(
                    RejectedOrder(
                        order_id=order.order_id,
                        side=order.side,
                        symbol=order.symbol,
                        requested_quantity=order.quantity,
                        reason=reason,
                        filled_quantity=0,
                    )
                )
            else:
                accepted.append(order)

        # Every accepted sell runs before any accepted buy regardless of input
        # order, so same-day sell proceeds fund buys; within each side the input
        # order (the engine's priority rank) is preserved.
        for order in accepted:
            if order.side == SELL:
                self._execute_sell(order, rows, account, fills, rejections, trade_date)
        for order in accepted:
            if order.side != SELL:  # side is BUY (Order validates the enum)
                self._execute_buy(order, rows, account, fills, rejections, trade_date)

        return ExecutionResult(
            trade_date=trade_date,
            fills=tuple(fills),
            rejections=tuple(rejections),
        )

    # ------------------------------------------------------------------ #
    # Bar-contract validation
    # ------------------------------------------------------------------ #

    def _validate_bars(self, bars: pd.DataFrame) -> None:
        missing = [
            column
            for column in EXECUTION_BAR_REQUIRED_COLUMNS
            if column not in bars.columns
        ]
        if missing:
            raise ValueError(
                "execution bars must include columns "
                f"{list(EXECUTION_BAR_REQUIRED_COLUMNS)}; missing {missing}"
            )
        symbols = list(bars["symbol"])
        if len(set(symbols)) != len(symbols):
            raise ValueError("execution bars must have at most one row per symbol")

    @staticmethod
    def _rows_by_symbol(bars: pd.DataFrame) -> Mapping[str, Mapping[str, object]]:
        return {row["symbol"]: row for row in bars.to_dict("records")}

    # ------------------------------------------------------------------ #
    # Static pre-checks (bar and rule problems, decided before any trade)
    # ------------------------------------------------------------------ #

    def _static_reason(
        self,
        order: Order,
        rows: Mapping[str, Mapping[str, object]],
        trade_date: date,
    ) -> str | None:
        row = rows.get(order.symbol)
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
        listed_sessions = _optional_int(row.get("listed_sessions"))
        try:
            limits = self._rules.price_limits(
                order.symbol,
                trade_date,
                pre_close,
                status=status,
                listed_sessions=listed_sessions,
            )
        except UncoveredRuleError:
            return REASON_UNCOVERED_RULE
        price = self._cost.fill_price(order.side, open_price, trade_date)
        # block_reason encodes the A-share conservative policy (reject a buy
        # at/above the upper limit, a sell at/below the lower limit).
        return limits.block_reason(order.side.lower(), price)

    # ------------------------------------------------------------------ #
    # Sell then buy execution
    # ------------------------------------------------------------------ #

    def _execute_sell(
        self,
        order: Order,
        rows: Mapping[str, Mapping[str, object]],
        account: Account,
        fills: list[Fill],
        rejections: list[RejectedOrder],
        trade_date: date,
    ) -> None:
        if account.sellable_quantity(order.symbol, trade_date) < order.quantity:
            rejections.append(
                RejectedOrder(
                    order_id=order.order_id,
                    side=order.side,
                    symbol=order.symbol,
                    requested_quantity=order.quantity,
                    reason=REASON_INSUFFICIENT_SELLABLE_QUANTITY,
                    filled_quantity=0,
                )
            )
            return
        open_price = _optional_decimal(rows[order.symbol]["open"])
        quote = self._cost.calculate(SELL, order.quantity, open_price, trade_date)
        self._apply(order, quote, account, fills, trade_date)

    def _execute_buy(
        self,
        order: Order,
        rows: Mapping[str, Mapping[str, object]],
        account: Account,
        fills: list[Fill],
        rejections: list[RejectedOrder],
        trade_date: date,
    ) -> None:
        open_price = _optional_decimal(rows[order.symbol]["open"])
        cash = account.cash
        affordable = self._largest_affordable_lot_quantity(
            order, open_price, cash, trade_date
        )
        if affordable == 0:
            rejections.append(
                RejectedOrder(
                    order_id=order.order_id,
                    side=BUY,
                    symbol=order.symbol,
                    requested_quantity=order.quantity,
                    reason=REASON_INSUFFICIENT_CASH,
                    filled_quantity=0,
                )
            )
            return
        quote = self._cost.calculate(BUY, affordable, open_price, trade_date)
        self._apply(order, quote, account, fills, trade_date)
        if affordable < order.quantity:
            rejections.append(
                RejectedOrder(
                    order_id=order.order_id,
                    side=BUY,
                    symbol=order.symbol,
                    requested_quantity=order.quantity,
                    reason=REASON_INSUFFICIENT_CASH,
                    filled_quantity=affordable,
                )
            )

    def _largest_affordable_lot_quantity(
        self, order: Order, open_price: object, cash: Decimal, trade_date: date
    ) -> int:
        """Largest whole-lot fill whose total cost fits ``cash`` (or 0).

        Cost is monotonic in quantity (each extra 100-share lot adds positive
        price and at least a floor commission), so a downward scan from the
        order quantity finds the optimum without re-ranking or leverage.
        """
        for quantity in range(order.quantity, 0, -LOT_SIZE):
            quote = self._cost.calculate(BUY, quantity, open_price, trade_date)
            if quote.gross + quote.commission <= cash:
                return quantity
        return 0

    def _apply(
        self,
        order: Order,
        quote: FeeBreakdown,
        account: Account,
        fills: list[Fill],
        trade_date: date,
    ) -> None:
        self._fill_seq += 1
        fill = Fill(
            fill_id=f"F{self._fill_seq:06d}",
            order_id=order.order_id,
            trade_date=trade_date,
            side=order.side,
            symbol=order.symbol,
            quantity=quote.quantity,
            price=quote.price,
            commission=quote.commission,
            stamp_tax=quote.stamp_tax,
        )
        account.apply_fill(fill)
        fills.append(fill)
