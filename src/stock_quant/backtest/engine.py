"""Chronological, event-driven portfolio backtest engine (Task 10).

``BacktestEngine.run`` replays one account day by day over a confirmed trading
calendar.  For every open date it (1) books the implemented corporate actions
whose ex-date is that day -- crediting pre-tax cash against the record-date
holding and crediting bonus/capitalization shares -- (2) executes the scheduled
sells then buys for that date through a single :class:`ExecutionSimulator`
(so fill ids ``F{seq:06d}`` run uninterrupted across the account's whole
lifetime and same-day sell proceeds fund buys), and (3) at the close values the
account against that date's unadjusted closes, carrying the last valid close
for any held name with no valid bar that day and reporting stale days and the
stale share of equity.  Carried prices are valuation-only and never feed fills.

The engine is a pure reader over already-fixed inputs -- one exact
``dataset_version``, the confirmed calendar, the rule book, one cost model,
unadjusted daily bars, accepted corporate actions, benchmark closes and a
precomputed order schedule.  Before the account is touched it runs
backtest-readiness checks (benchmark coverage over the whole window, a clean
executable bar with a prior close for every scheduled order, and held-period
corporate actions all implemented and mutually consistent) and refuses the
scenario with an explanatory error when any check fails, so no partially
mutated account is ever produced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

import pandas as pd

from stock_quant.backtest.account import Account
from stock_quant.backtest.corporate_actions import (
    UnsupportedCorporateAction,
    action_id_of,
    apply_corporate_action,
)
from stock_quant.backtest.costs import CostModel
from stock_quant.backtest.execution import (
    EXECUTION_BAR_REQUIRED_COLUMNS,
    ExecutionSimulator,
)
from stock_quant.backtest.models import (
    BUY,
    SELL,
    CorporateActionLedgerEntry,
    ExecutionResult,
    Order,
    as_decimal,
)
from stock_quant.backtest.rebalance import (
    RebalanceAdjustment,
    project_rebalance,
)
from stock_quant.backtest.valuation import AccountValuation, value_account
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.trading_rules import TradingRuleBook

#: Frame columns of the produced ledgers, in their canonical order.
FILL_COLUMNS = (
    "trade_date",
    "fill_id",
    "order_id",
    "side",
    "symbol",
    "quantity",
    "price",
    "commission",
    "stamp_tax",
)
REJECTION_COLUMNS = (
    "trade_date",
    "order_id",
    "side",
    "symbol",
    "requested_quantity",
    "filled_quantity",
    "rejected_quantity",
    "reason",
)
ACTION_LEDGER_COLUMNS = (
    "seq",
    "action_id",
    "symbol",
    "ex_date",
    "record_date",
    "cash_credited",
    "shares_added",
    "note",
)
EQUITY_COLUMNS = (
    "trade_date",
    "cash",
    "market_value",
    "total_equity",
    "stale_market_value",
    "stale_days",
)
SUBMITTED_ORDER_COLUMNS = ("trade_date", "order_id", "side", "symbol", "quantity")
REBALANCE_ADJUSTMENT_COLUMNS = (
    "trade_date",
    "side",
    "symbol",
    "requested_quantity",
    "executable_quantity",
    "rejected_quantity",
    "reason",
)
EXECUTABLE_TARGET_COLUMNS = ("trade_date", "symbol", "target_quantity")

#: Bar columns the engine needs from the caller's daily market frame.
BAR_REQUIRED_COLUMNS = ("symbol", "trade_date", "open", "close", "quality_severity")
_BAR_OPTIONAL_COLUMNS = ("status", "listed_sessions")

#: Benchmark frame columns (the two confirmed index series).
BENCHMARK_COLUMNS = ("symbol", "trade_date", "close")

_STATUS_IMPLEMENTED = "implemented"
_VALID_STATUS = "INFO"


class ReadinessError(ValueError):
    """A backtest-readiness check failed before any account was mutated."""


# --------------------------------------------------------------------------- #
# Request / result records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OrderDay:
    """The orders scheduled for one open date (sells run before buys)."""

    trade_date: date
    sells: tuple[Order, ...] = ()
    buys: tuple[Order, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.trade_date, date):
            raise TypeError(
                f"trade_date must be a date, got {self.trade_date!r}"
            )
        if any(order.side != SELL for order in self.sells):
            raise ValueError(f"{self.trade_date}: sells must all be SELL orders")
        if any(order.side != BUY for order in self.buys):
            raise ValueError(f"{self.trade_date}: buys must all be BUY orders")


@dataclass(frozen=True)
class TargetDay:
    """Ideal target quantities to project against the live account at one open."""

    trade_date: date
    target_quantities: Mapping[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.trade_date, date):
            raise TypeError(f"trade_date must be a date, got {self.trade_date!r}")
        targets = dict(self.target_quantities)
        for symbol, quantity in targets.items():
            if not isinstance(symbol, str) or not symbol:
                raise ValueError("target symbols must be non-empty strings")
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity < 0
            ):
                raise ValueError("target quantities must be non-negative integers")
        object.__setattr__(self, "target_quantities", targets)


@dataclass(frozen=True)
class BacktestRequest:
    """Everything the engine needs for one replay -- all inputs are fixed."""

    dataset_version: str
    initial_cash: object
    calendar: TradingCalendar
    rule_book: TradingRuleBook
    cost_model: CostModel
    bars: pd.DataFrame
    corporate_actions: pd.DataFrame
    schedule: tuple[OrderDay, ...] = ()
    target_schedule: tuple[TargetDay, ...] = ()
    benchmark_symbols: tuple[str, ...] = ("000300.SH", "000905.SH")
    benchmarks: pd.DataFrame = field(default_factory=pd.DataFrame)

    def __post_init__(self) -> None:
        if not self.dataset_version:
            raise ValueError("dataset_version must be non-empty")
        if self.schedule and self.target_schedule:
            raise ValueError("schedule and target_schedule cannot both be populated")


@dataclass(frozen=True)
class BacktestResult:
    """The full chronological ledger of one completed replay."""

    fills: pd.DataFrame
    rejections: pd.DataFrame
    action_ledger: pd.DataFrame
    daily_equity: pd.DataFrame
    submitted_orders: pd.DataFrame
    rebalance_adjustments: pd.DataFrame
    executable_targets: pd.DataFrame


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class BacktestEngine:
    """Replays one account chronologically over fixed inputs."""

    def run(self, request: BacktestRequest) -> BacktestResult:
        if not isinstance(request, BacktestRequest):
            raise TypeError(
                f"request must be a BacktestRequest, got {type(request).__name__}"
            )
        market = _Market(request)
        self._validate_bars(market)
        account = Account(request.initial_cash, calendar=request.calendar)
        simulator = ExecutionSimulator(
            cost_model=request.cost_model, rule_book=request.rule_book
        )
        fills: list[dict] = []
        rejections: list[dict] = []
        equity: list[dict] = []
        submitted_orders: list[dict] = []
        rebalance_adjustments: list[dict] = []
        executable_targets: list[dict] = []

        last_close: dict[str, Decimal] = {}
        stale: dict[str, int] = {}

        for day in market.window:
            self._apply_actions(account, market, day)
            target = market.target_on(day)
            if target is not None:
                symbols = set(target) | {lot.symbol for lot in account.lots}
                frame = self._projection_frame(day, symbols, market, last_close)
                projection = project_rebalance(
                    target, account, frame, day, request.cost_model, request.rule_book
                )
                orders = list(projection.sells) + list(projection.buys)
                self._collect_projection(
                    day, orders, projection.adjustments,
                    projection.executable_target_quantities, submitted_orders,
                    rebalance_adjustments, executable_targets,
                )
            else:
                sells, buys = market.schedule_by(day)
                orders = list(sells) + list(buys)
                # One submitted record per plan order (spec section 2): the
                # schedule path emits the raw plan as the audit trail.  The
                # target projection path keeps its own collection until the
                # execution-day projection is removed in the next task.
                self._collect_submitted(day, orders, submitted_orders)
            if orders:
                frame = self._execution_frame(
                    day, orders, market, last_close
                )
                result = simulator.execute(orders, frame, account, day)
                self._collect(result, day, fills, rejections)
            self._advance_marks(day, market, last_close, stale)
            valuation = self._value(account, day, market, last_close, stale)
            self._collect_equity(valuation, day, equity)

        return BacktestResult(
            fills=self._fills_frame(fills),
            rejections=self._rejections_frame(rejections),
            action_ledger=self._action_ledger_frame(account.action_ledger),
            daily_equity=self._equity_frame(equity),
            submitted_orders=self._submitted_orders_frame(submitted_orders),
            rebalance_adjustments=self._rebalance_adjustments_frame(
                rebalance_adjustments
            ),
            executable_targets=self._executable_targets_frame(executable_targets),
        )

    # ------------------------------------------------------------------ #
    # Per-day phases
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_actions(
        account: Account, market: "_Market", day: date
    ) -> None:
        """Book every implemented action whose ex-date is ``day`` (before open)."""
        for action in market.actions_on(day):
            apply_corporate_action(account, action)

    @staticmethod
    def _execution_frame(
        day: date,
        orders: Sequence[Order],
        market: "_Market",
        last_close: Mapping[str, Decimal],
    ) -> pd.DataFrame:
        """The day's executable-bar frame: one clean unadjusted row per order."""
        records: list[dict] = []
        day_rows = market.day_rows.get(day, {})
        for order in orders:
            row = day_rows.get(order.symbol)
            if row is None:
                continue  # absent row == suspended/unknown to the executor
            record = {
                "symbol": order.symbol,
                "open": row.get("open"),
                "pre_close": last_close.get(order.symbol),
                "quality_severity": row.get("quality_severity") or _VALID_STATUS,
            }
            for column in _BAR_OPTIONAL_COLUMNS:
                if column in row:
                    record[column] = row[column]
            records.append(record)
        if not records:
            return pd.DataFrame(
                columns=list(EXECUTION_BAR_REQUIRED_COLUMNS + _BAR_OPTIONAL_COLUMNS)
            )
        return pd.DataFrame(records)

    @staticmethod
    def _projection_frame(
        day: date,
        symbols: set[str],
        market: "_Market",
        last_close: Mapping[str, Decimal],
    ) -> pd.DataFrame:
        """Visible execution-date bars for pre-trade target projection."""
        records: list[dict] = []
        optional_columns: set[str] = set()
        for symbol in sorted(symbols):
            row = market.day_rows.get(day, {}).get(symbol)
            if row is None:
                continue
            record = {
                "symbol": symbol,
                "open": row.get("open"),
                "pre_close": last_close.get(symbol),
                "quality_severity": row.get("quality_severity") or _VALID_STATUS,
            }
            for column in _BAR_OPTIONAL_COLUMNS:
                if column in row:
                    record[column] = row[column]
                    optional_columns.add(column)
            records.append(record)
        columns = list(EXECUTION_BAR_REQUIRED_COLUMNS)
        columns += [
            column
            for column in _BAR_OPTIONAL_COLUMNS
            if column in optional_columns
        ]
        return pd.DataFrame(records, columns=columns)

    @staticmethod
    def _advance_marks(
        day: date,
        market: "_Market",
        last_close: dict[str, Decimal],
        stale: dict[str, int],
    ) -> None:
        """Fold ``day``'s valid closes into the carried-close/stale state."""
        today_valid = {
            symbol: as_decimal(row["close"])
            for symbol, row in market.day_rows.get(day, {}).items()
            if _valid_close(row)
        }
        for symbol in set(last_close) | set(today_valid):
            if symbol in today_valid:
                last_close[symbol] = today_valid[symbol]
                stale[symbol] = 0
            else:  # held or watched symbol with no valid close today
                stale[symbol] = stale.get(symbol, 0) + 1

    @staticmethod
    def _value(
        account: Account,
        day: date,
        market: "_Market",
        last_close: Mapping[str, Decimal],
        stale: Mapping[str, int],
    ) -> AccountValuation:
        """Value at ``day``'s unadjusted closes, carrying names with no bar."""
        today_valid = {
            symbol
            for symbol, row in market.day_rows.get(day, {}).items()
            if _valid_close(row)
        }
        closes: dict[str, Decimal] = {}
        carried: dict[str, tuple[Decimal, int]] = {}
        for symbol in {lot.symbol for lot in account.lots}:
            if symbol in today_valid:
                closes[symbol] = last_close[symbol]
            elif symbol in last_close:
                carried[symbol] = (last_close[symbol], stale[symbol])
        return value_account(
            account, closes=closes, carried=carried, trade_date=day
        )

    # ------------------------------------------------------------------ #
    # Ledger collection and frame assembly
    # ------------------------------------------------------------------ #

    @staticmethod
    def _collect(
        result: ExecutionResult,
        day: date,
        fills: list[dict],
        rejections: list[dict],
    ) -> None:
        for fill in result.fills:
            fills.append(
                {
                    "trade_date": day,
                    "fill_id": fill.fill_id,
                    "order_id": fill.order_id,
                    "side": fill.side,
                    "symbol": fill.symbol,
                    "quantity": fill.quantity,
                    "price": float(fill.price),
                    "commission": float(fill.commission),
                    "stamp_tax": float(fill.stamp_tax),
                }
            )
        for rejected in result.rejections:
            rejections.append(
                {
                    "trade_date": day,
                    "order_id": rejected.order_id,
                    "side": rejected.side,
                    "symbol": rejected.symbol,
                    "requested_quantity": rejected.requested_quantity,
                    "filled_quantity": rejected.filled_quantity,
                    "rejected_quantity": rejected.rejected_quantity,
                    "reason": rejected.reason,
                }
            )

    @staticmethod
    def _collect_submitted(
        day: date,
        orders: Sequence[Order],
        submitted_orders: list[dict],
    ) -> None:
        """One audit row per plan order submitted on ``day`` (spec section 2)."""
        submitted_orders.extend(
            {
                "trade_date": day,
                "order_id": order.order_id,
                "side": order.side,
                "symbol": order.symbol,
                "quantity": order.quantity,
            }
            for order in orders
        )

    @staticmethod
    def _collect_equity(
        valuation: AccountValuation, day: date, equity: list[dict]
    ) -> None:
        equity.append(
            {
                "trade_date": day,
                "cash": float(valuation.cash),
                "market_value": float(valuation.market_value),
                "total_equity": float(valuation.total_equity),
                "stale_market_value": float(valuation.stale_market_value),
                "stale_days": int(valuation.stale_days),
            }
        )

    @staticmethod
    def _collect_projection(
        day: date,
        orders: Sequence[Order],
        adjustments: Sequence[RebalanceAdjustment],
        executable_target_quantities: Mapping[str, int],
        submitted_orders: list[dict],
        rebalance_adjustments: list[dict],
        executable_targets: list[dict],
    ) -> None:
        submitted_orders.extend(
            {
                "trade_date": day,
                "order_id": order.order_id,
                "side": order.side,
                "symbol": order.symbol,
                "quantity": order.quantity,
            }
            for order in orders
        )
        rebalance_adjustments.extend(
            {
                "trade_date": adjustment.trade_date,
                "side": adjustment.side,
                "symbol": adjustment.symbol,
                "requested_quantity": adjustment.requested_quantity,
                "executable_quantity": adjustment.executable_quantity,
                "rejected_quantity": adjustment.rejected_quantity,
                "reason": adjustment.reason,
            }
            for adjustment in adjustments
        )
        executable_targets.extend(
            {
                "trade_date": day,
                "symbol": symbol,
                "target_quantity": quantity,
            }
            for symbol, quantity in sorted(executable_target_quantities.items())
        )

    @staticmethod
    def _fills_frame(rows: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=list(FILL_COLUMNS))
        for column in ("quantity",):
            frame[column] = frame[column].astype("int64")
        return frame

    @staticmethod
    def _rejections_frame(rows: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=list(REJECTION_COLUMNS))
        for column in (
            "requested_quantity",
            "filled_quantity",
            "rejected_quantity",
        ):
            frame[column] = frame[column].astype("int64")
        return frame

    @staticmethod
    def _action_ledger_frame(
        entries: Iterable[CorporateActionLedgerEntry],
    ) -> pd.DataFrame:
        rows = [
            {
                "seq": entry.seq,
                "action_id": entry.action_id,
                "symbol": entry.symbol,
                "ex_date": entry.ex_date,
                "record_date": entry.record_date,
                "cash_credited": float(entry.cash_credited),
                "shares_added": int(entry.shares_added),
                "note": entry.note,
            }
            for entry in entries
        ]
        frame = pd.DataFrame(rows, columns=list(ACTION_LEDGER_COLUMNS))
        frame["shares_added"] = frame["shares_added"].astype("int64")
        return frame

    @staticmethod
    def _equity_frame(rows: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=list(EQUITY_COLUMNS))
        frame["stale_days"] = frame["stale_days"].astype("int64")
        return frame

    @staticmethod
    def _submitted_orders_frame(rows: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=list(SUBMITTED_ORDER_COLUMNS))
        return frame.astype({"quantity": "int64"})

    @staticmethod
    def _rebalance_adjustments_frame(rows: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=list(REBALANCE_ADJUSTMENT_COLUMNS))
        return frame.astype(
            {
                "requested_quantity": "int64",
                "executable_quantity": "int64",
                "rejected_quantity": "int64",
            }
        )

    @staticmethod
    def _executable_targets_frame(rows: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=list(EXECUTABLE_TARGET_COLUMNS))
        return frame.astype({"target_quantity": "int64"})

    # ------------------------------------------------------------------ #
    # Readiness: benchmark coverage and executable clean bars
    # ------------------------------------------------------------------ #

    def _validate_bars(self, market: "_Market") -> None:
        """Readiness checks that run before any account mutation.

        Benchmark series must have a valid close on every open day of the
        window; every scheduled order must fall on an open day whose symbol has
        a clean executable bar (a prior valid close and a usable open); and the
        accepted corporate actions touching any possibly-held symbol must all be
        implemented, complete, non-rights and mutually consistent.  Any failure
        raises and no scenario is started.
        """
        self._check_benchmarks(market)
        self._check_order_days(market)
        self._check_actions(market)

    def _check_benchmarks(self, market: "_Market") -> None:
        if not market.request.benchmark_symbols:
            return
        for symbol in market.request.benchmark_symbols:
            closes = market.benchmark_closes.get(symbol, {})
            missing = [
                day for day in market.window if day not in closes or not closes[day]
            ]
            if missing:
                raise ReadinessError(
                    f"benchmark {symbol} lacks a valid close on open day(s) "
                    + ", ".join(day.isoformat() for day in missing[:5])
                )

    def _check_order_days(self, market: "_Market") -> None:
        # Simulate the carried-close state read-only to give every scheduled
        # order a real prior close, without touching the account.
        last_close: dict[str, Decimal] = {}
        for day in market.window:
            for order in market.orders_on(day):
                row = market.day_rows.get(day, {}).get(order.symbol)
                if row is None:
                    # The executor records this as suspended_or_unknown; it is
                    # a normal, auditable order rejection rather than a run
                    # readiness failure.
                    continue
                if _is_error_severity(row.get("quality_severity")):
                    raise ReadinessError(
                        f"order {order.order_id} for {order.symbol} on "
                        f"{day.isoformat()} runs against an ERROR-quality bar"
                    )
                if not _positive(row.get("open")):
                    raise ReadinessError(
                        f"order {order.order_id} for {order.symbol} on "
                        f"{day.isoformat()} has no usable open price"
                    )
                if order.symbol not in last_close:
                    raise ReadinessError(
                        f"order {order.order_id} for {order.symbol} on "
                        f"{day.isoformat()} has no prior close for the "
                        "price-limit band"
                    )
            for symbol, row in market.day_rows.get(day, {}).items():
                if _valid_close(row):
                    last_close[symbol] = as_decimal(row["close"])

    def _check_actions(self, market: "_Market") -> None:
        possible = market.possible_held_symbols
        for action_id, rows in market.actions_by_id.items():
            symbol = rows[0]["symbol"]
            if symbol not in possible:
                continue
            self._require_bookable(action_id, rows)
        for row in market.unkeyed_actions:
            if row.get("symbol") in possible:
                raise UnsupportedCorporateAction(
                    f"corporate action for {row.get('symbol')} is incomplete "
                    "(missing its ex-date, so it cannot be keyed)"
                )

    @staticmethod
    def _require_bookable(action_id: str, rows: Sequence[dict]) -> None:
        """One possibly-held action id must be implemented and consistent."""
        facts = {_action_facts(row) for row in rows}
        if len(facts) > 1:
            raise UnsupportedCorporateAction(
                f"corporate action {action_id} has conflicting facts across "
                f"sources ({len(rows)} rows); holding-period backtests "
                "cannot reconcile them"
            )
        row = rows[0]
        if row.get("status") != _STATUS_IMPLEMENTED:
            raise UnsupportedCorporateAction(
                f"corporate action {action_id} is "
                f"{row.get('status')!r}, not implemented"
            )
        if row.get("record_date") is None or row.get("ex_date") is None:
            raise UnsupportedCorporateAction(
                f"corporate action {action_id} is incomplete "
                "(missing record/ex date)"
            )
        if row.get("rights_issue_ratio", 0) > 0:
            raise UnsupportedCorporateAction(
                f"corporate action {action_id} is a rights issue, which a "
                "holding-period backtest cannot book"
            )


# --------------------------------------------------------------------------- #
# Market index over the fixed input frames
# --------------------------------------------------------------------------- #


class _Market:
    """Normalized, deterministic views over a request's market frames."""

    def __init__(self, request: BacktestRequest) -> None:
        self.request = request
        bars = _normalize_bars(request.bars)
        self.day_rows = _index_day_rows(bars)
        bar_days = sorted(self.day_rows)
        open_days = request.calendar.open_days
        first = bar_days[0] if bar_days else None
        last = bar_days[-1] if bar_days else None
        if first is None:
            raise ReadinessError("bars cover no open day")
        if first not in open_days or last not in open_days:
            raise ReadinessError(
                "bar window must lie inside the calendar's open days "
                f"({first}..{last})"
            )
        self.window = tuple(day for day in open_days if first <= day <= last)
        self.schedule_by_day = _schedule_index(request.schedule, self.window)
        self.target_by_day = _target_schedule_index(
            request.target_schedule, self.window
        )
        self.actions_by_id, self.unkeyed_actions = _action_index(
            request.corporate_actions
        )
        self.benchmark_closes = _benchmark_index(request.benchmarks)

    @property
    def possible_held_symbols(self) -> set[str]:
        """Symbols any schedule order touches (the potentially-held set)."""
        return {
            order.symbol
            for orders in self.schedule_by_day.values()
            for order in orders
        } | {
            symbol
            for targets in self.target_by_day.values()
            for symbol in targets
        }

    def actions_on(self, day: date) -> tuple[dict, ...]:
        """Implemented actions whose ex-date is ``day``, deduplicated."""
        return tuple(
            rows[0]
            for rows in self.actions_by_id.values()
            if rows[0]["ex_date"] == day
        )

    def orders_on(self, day: date) -> tuple[Order, ...]:
        return self.schedule_by_day.get(day, ())

    def target_on(self, day: date) -> Mapping[str, int] | None:
        return self.target_by_day.get(day)

    def schedule_by(self, day: date) -> tuple[tuple[Order, ...], tuple[Order, ...]]:
        """The day's sells and buys as separate order tuples."""
        sells: list[Order] = []
        buys: list[Order] = []
        for order in self.schedule_by_day.get(day, ()):
            (buys if order.side == BUY else sells).append(order)
        return tuple(sells), tuple(buys)


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(bars, pd.DataFrame):
        raise TypeError(f"bars must be a DataFrame, got {type(bars).__name__}")
    missing = [column for column in BAR_REQUIRED_COLUMNS if column not in bars.columns]
    if missing:
        raise ReadinessError(
            f"bars must include columns {list(BAR_REQUIRED_COLUMNS)}; "
            f"missing {missing}"
        )
    kept = [column for column in BAR_REQUIRED_COLUMNS if column in bars.columns]
    for column in _BAR_OPTIONAL_COLUMNS:
        if column in bars.columns:
            kept.append(column)
    return bars[kept].copy()


def _index_day_rows(bars: pd.DataFrame) -> dict[date, dict[str, dict]]:
    rows: dict[date, dict[str, dict]] = {}
    for record in bars.to_dict("records"):
        day = _as_date(record["trade_date"])
        rows.setdefault(day, {})[record["symbol"]] = record
    return rows


def _schedule_index(
    schedule: tuple[OrderDay, ...], window: tuple[date, ...]
) -> dict[date, tuple[Order, ...]]:
    open_days = set(window)
    by_day: dict[date, list[Order]] = {}
    for order_day in schedule:
        day = _as_date(order_day.trade_date)
        if day not in open_days:
            raise ReadinessError(
                f"orders scheduled on {day.isoformat()}, which is not an open "
                "day of the window"
            )
        by_day.setdefault(day, []).extend(
            list(order_day.sells) + list(order_day.buys)
        )
    return {
        day: tuple(orders) for day, orders in sorted(by_day.items())
    }


def _target_schedule_index(
    schedule: tuple[TargetDay, ...], window: tuple[date, ...]
) -> dict[date, Mapping[str, int]]:
    open_days = set(window)
    indexed: dict[date, Mapping[str, int]] = {}
    for target_day in schedule:
        if target_day.trade_date not in open_days:
            raise ReadinessError(
                f"targets scheduled on {target_day.trade_date.isoformat()}, which is "
                "not an open day of the window"
            )
        if target_day.trade_date in indexed:
            raise ReadinessError(
                "multiple target books scheduled on "
                f"{target_day.trade_date.isoformat()}"
            )
        indexed[target_day.trade_date] = target_day.target_quantities
    return indexed


def _action_index(
    corporate_actions: pd.DataFrame,
) -> tuple[dict[str, list[dict]], tuple[dict, ...]]:
    """Index accepted action rows by action id; return unkeyable rows separately.

    Duplicate rows sharing one action id and identical facts collapse to a
    single canonical row; distinct facts under one action id are kept apart so
    the readiness check can raise on the cross-source conflict.
    """
    if corporate_actions is None or corporate_actions.empty:
        return {}, ()
    rows = [_normalize_action(row) for _, row in corporate_actions.iterrows()]
    by_id: dict[str, list[dict]] = {}
    unkeyed: list[dict] = []
    for row in rows:
        if row["ex_date"] is None:
            unkeyed.append(row)  # incompleteness check flags it if held
            continue
        by_id.setdefault(action_id_of(row["symbol"], row["ex_date"]), []).append(row)
    ordered: dict[str, list[dict]] = {}
    for action_id, group in sorted(by_id.items()):
        uniq: list[dict] = []
        seen_keys: set[tuple] = set()
        for row in group:
            key = _fact_key(row)
            if key in seen_keys:
                continue  # identical duplicate: keep the first canonical row
            seen_keys.add(key)
            uniq.append(row)
        ordered[action_id] = uniq
    return ordered, tuple(unkeyed)


def _normalize_action(row: Mapping[str, object]) -> dict:
    symbol = _text(row.get("symbol"))
    if not symbol:
        raise ReadinessError("corporate action row is missing its symbol")
    return {
        "symbol": symbol,
        "announcement_date": _as_date(row.get("announcement_date")),
        "record_date": _as_date(row.get("record_date")),
        "ex_date": _as_date(row.get("ex_date")),
        "cash_dividend_per_share": _positive_or_zero(
            row.get("cash_dividend_per_share")
        ),
        "bonus_share_ratio": _positive_or_zero(row.get("bonus_share_ratio")),
        "capitalization_ratio": _positive_or_zero(row.get("capitalization_ratio")),
        "rights_issue_ratio": _positive_or_zero(row.get("rights_issue_ratio")),
        "rights_issue_price": row.get("rights_issue_price"),
        "source": _text(row.get("source")),
        "status": _text(row.get("status")) or "not_implemented",
    }


def _fact_key(row: dict) -> tuple:
    """The identity of one accepted action row's facts (source-blind)."""
    return (
        row["symbol"],
        row["record_date"],
        row["ex_date"],
        row["cash_dividend_per_share"],
        row["bonus_share_ratio"],
        row["capitalization_ratio"],
        row["rights_issue_ratio"],
        row["rights_issue_price"],
        row["status"],
    )


def _action_facts(row: dict) -> tuple:
    """Facts compared across sources for the same action id."""
    return (
        row["record_date"],
        row["ex_date"],
        row["cash_dividend_per_share"],
        row["bonus_share_ratio"],
        row["capitalization_ratio"],
        row["rights_issue_ratio"],
        row["rights_issue_price"],
        row["status"],
    )


def _benchmark_index(
    benchmarks: pd.DataFrame,
) -> dict[str, dict[date, Decimal | None]]:
    index: dict[str, dict[date, Decimal | None]] = {}
    if benchmarks is None or benchmarks.empty:
        return index
    for record in benchmarks.to_dict("records"):
        symbol = record.get("symbol")
        day = _as_date(record.get("trade_date"))
        index.setdefault(str(symbol), {})[day] = _positive_or_zero(
            record.get("close")
        )
    return index


# --------------------------------------------------------------------------- #
# Value helpers
# --------------------------------------------------------------------------- #


def _valid_close(row: Mapping[str, object]) -> bool:
    """True when the row carries a usable close (not error quality, positive)."""
    if _is_error_severity(row.get("quality_severity")):
        return False
    return _positive(row.get("close"))


def _positive(value: object) -> bool:
    if value is None:
        return False
    try:
        if isinstance(value, float) and math.isnan(value):
            return False
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    try:
        return as_decimal(value) > 0
    except (ValueError, TypeError):
        return False


def _positive_or_zero(value: object) -> Decimal:
    """A ratio/amount as a non-negative ``Decimal`` (empty/NaN -> zero)."""
    if value is None:
        return Decimal("0")
    try:
        if isinstance(value, float) and math.isnan(value):
            return Decimal("0")
        if pd.isna(value):
            return Decimal("0")
    except (TypeError, ValueError):
        pass
    if isinstance(value, Decimal):
        converted = value
    else:
        try:
            converted = Decimal(str(value))
        except Exception:
            return Decimal("0")
    if converted < 0:
        raise ReadinessError(f"negative corporate-action amount/ratio: {value}")
    return converted


def _is_error_severity(value: object) -> bool:
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
    return text in ("ERROR", "FATAL")


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    raise TypeError(f"cannot interpret {value!r} as a trading date")


def _text(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None
