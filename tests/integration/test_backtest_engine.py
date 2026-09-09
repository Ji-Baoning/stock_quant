"""Chronological portfolio backtest engine golden ledgers (Task 10).

A deterministic 10-name / 80-session synthetic market is authored offline and
persisted to ``tests/fixtures/synthetic_market/*.parquet``: normal trading, an
ex-date cash dividend + bonus, a later capitalization, a cash dividend with a
duplicate (cross-source identical) action row, a dividend on a name never held,
a multi-session suspension, a new listing, an illegal-OHLC quality-ERROR bar, an
open-price limit-up buy rejection, an unaffordable high-price partial fill, and
a full 80-day confirmed calendar with both benchmark series present.

``BacktestEngine.run`` is replayed once per cost scenario (zero_cost,
commission_tax, full_cost) on three completely separate accounts and every
produced ledger -- fills, rejections, corporate-action entries and daily
equity -- is compared byte-for-byte with an independently computed golden
frame, so costs propagate into later cash and the partial-fill quantities stay
scenario-dependent in exactly the way the fee model dictates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from stock_quant.backtest.corporate_actions import UnsupportedCorporateAction
from stock_quant.backtest.costs import CostModel
from stock_quant.backtest.engine import (
    SUBMITTED_ORDER_COLUMNS,
    BacktestEngine,
    BacktestRequest,
    OrderDay,
    ReadinessError,
)
from stock_quant.backtest.models import BUY, SELL, Order
from stock_quant.backtest.rebalancer import AccountAwareWeeklyRebalancer
from stock_quant.backtest.weight_rebalancer import WeightTargetRebalancer
from stock_quant.config import CostRate
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.trading_rules import (
    REASON_BUY_AT_UPPER_LIMIT,
    REASON_SELL_AT_LOWER_LIMIT,
    TradingRuleBook,
)
from stock_quant.portfolio.buffered_models import WeightTargetPeriod

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_market"
_REPO_ROOT = Path(__file__).resolve().parents[2]

CENT = Decimal("0.01")
_HALF_UP = ROUND_HALF_UP
_INITIAL_CASH = Decimal("400000.00")

# The ten synthetic A-share names (all main-board 600xxx).
ALPHA = "600001.SH"
BETA = "600002.SH"
GAMMA = "600003.SH"
DELTA = "600004.SH"
EPSILON = "600005.SH"
ZETA = "600006.SH"
ETA = "600007.SH"
THETA = "600008.SH"
IOTA = "600009.SH"
KAPPA = "600010.SH"

_SYMBOLS = (
    ALPHA,
    BETA,
    GAMMA,
    DELTA,
    EPSILON,
    ZETA,
    ETA,
    THETA,
    IOTA,
    KAPPA,
)

# --------------------------------------------------------------------------- #
# Cost scenarios (must mirror configs/costs.yml)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Spec:
    name: str
    commission_rate: Decimal
    minimum_commission: Decimal
    stamp_tax_sell_rate: Decimal
    slippage_rate: Decimal


def _rate(
    commission_rate: str,
    minimum_commission: str,
    stamp_tax_sell_rate: str,
    slippage_rate: str,
) -> _Spec:
    return _Spec(
        name="",
        commission_rate=Decimal(commission_rate),
        minimum_commission=Decimal(minimum_commission),
        stamp_tax_sell_rate=Decimal(stamp_tax_sell_rate),
        slippage_rate=Decimal(slippage_rate),
    )


_SCENARIOS: dict[str, _Spec] = {
    "zero_cost": _rate("0", "0", "0", "0"),
    "commission_tax": _rate("0.0003", "5", "0.0005", "0"),
    "full_cost": _rate("0.0003", "5", "0.0005", "0.001"),
}

_DATASET_VERSION = "synthetic-v1"


def _cost_rate(spec: _Spec) -> CostRate:
    return CostRate(
        effective_from=date(2020, 1, 1),
        commission_rate=float(spec.commission_rate),
        minimum_commission=float(spec.minimum_commission),
        stamp_tax_sell_rate=float(spec.stamp_tax_sell_rate),
        slippage_rate=float(spec.slippage_rate),
    )


# --------------------------------------------------------------------------- #
# The deterministic synthetic market
# --------------------------------------------------------------------------- #


def _business_days(start: date, count: int) -> list[date]:
    """The first ``count`` weekdays from ``start`` (an A-share-ish calendar)."""
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


@dataclass(frozen=True)
class _SyntheticMarket:
    days: tuple[date, ...]
    bars: pd.DataFrame
    corporate_actions: pd.DataFrame
    benchmarks: pd.DataFrame
    schedule: tuple[OrderDay, ...]


def _price(symbol: str, session: int) -> float | None:
    """The session's open/close for ``symbol`` (None when it does not trade).

    Ex-date prices already fall to the ex-rights reference so total equity is
    conserved by the booked cash/share credits.  GAMMA halts sessions 21..25;
    EPSILON lists on session 30.
    """
    if symbol == ALPHA:
        if session < 6:
            return 10.0
        if session < 60:
            return 8.0
        return 6.4
    if symbol == BETA:
        return 10.0
    if symbol == GAMMA:
        if 21 <= session <= 25:
            return None
        return 10.0 if session <= 20 else 11.0
    if symbol == DELTA:
        return 10.0 if session < 14 else 9.0
    if symbol == EPSILON:
        return None if session < 30 else 10.0
    if symbol == ZETA:
        return 10.0
    if symbol == ETA:
        return 10.0 if session <= 39 else 11.0
    if symbol == THETA:
        return 3000.0
    if symbol == IOTA:
        return 10.0 if session < 52 else 9.8
    if symbol == KAPPA:
        return 10.0
    raise AssertionError(symbol)


def _quality(symbol: str, session: int) -> str:
    """The bar's quality annotation; ZETA session 35 is illegal (ERROR)."""
    if symbol == ZETA and session == 35:
        return "ERROR"
    return "INFO"


def _build_bars(days: list[date]) -> pd.DataFrame:
    rows: list[dict] = []
    for session, trade_date in enumerate(days):
        for symbol in _SYMBOLS:
            price = _price(symbol, session)
            if price is None:
                continue
            rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0,
                    "amount": 0.0,
                    "adjustment": "unadjusted",
                    "source": "synthetic",
                    "quality_severity": _quality(symbol, session),
                    "status": "NORMAL",
                }
            )
    return pd.DataFrame(rows)


def _build_actions(days: list[date]) -> pd.DataFrame:
    """Accepted corporate actions; IOTA's dividend row is duplicated exactly."""

    def row(
        symbol: str,
        *,
        record: int,
        ex: int,
        cash_per_share: float = 0.0,
        bonus_ratio: float = 0.0,
        capitalization_ratio: float = 0.0,
        source: str = "cninfo",
    ) -> dict:
        return {
            "symbol": symbol,
            "announcement_date": days[max(0, record - 2)],
            "record_date": days[record],
            "ex_date": days[ex],
            "cash_dividend_per_share": cash_per_share,
            "bonus_share_ratio": bonus_ratio,
            "capitalization_ratio": capitalization_ratio,
            "rights_issue_ratio": None,
            "rights_issue_price": None,
            "source": source,
            "status": "implemented",
        }

    rows = [
        # ALPHA: ex-6 cash 0.40/share + 20% bonus (price 10.00 -> 8.00).
        row(ALPHA, record=4, ex=6, cash_per_share=0.4, bonus_ratio=0.2),
        # DELTA: ex-14 cash 1.00/share (price 10.00 -> 9.00).
        row(DELTA, record=12, ex=14, cash_per_share=1.0),
        # IOTA: ex-52 cash 0.20/share, duplicated by a second source.
        row(IOTA, record=50, ex=52, cash_per_share=0.2),
        row(IOTA, record=50, ex=52, cash_per_share=0.2, source="eastmoney"),
        # ALPHA: ex-60 25% capitalization (price 8.00 -> 6.40, 120 -> 150).
        row(ALPHA, record=58, ex=60, capitalization_ratio=0.25),
        # KAPPA: a dividend on a name the account never holds (no-op).
        row(KAPPA, record=43, ex=45, cash_per_share=0.3),
    ]
    return pd.DataFrame(rows)


def _build_benchmarks(days: list[date]) -> pd.DataFrame:
    rows = []
    for session, trade_date in enumerate(days):
        rows.append({"symbol": "000300.SH", "trade_date": trade_date, "close": 4000.0})
        rows.append({"symbol": "000905.SH", "trade_date": trade_date, "close": 2000.0})
    return pd.DataFrame(rows)


def _build_schedule() -> tuple[OrderDay, ...]:
    """S1 buys seven names, S2 buys THETA (partial), S8 sells BETA, S31 lists
    EPSILON then buys it, S40 buys ETA into a limit-up rejection."""

    def day(session: int) -> date:
        return _DAYS[session]

    def order(order_id: str, side: str, symbol: str, quantity: int) -> Order:
        return Order(order_id=order_id, side=side, symbol=symbol, quantity=quantity)

    first_buys = (
        (ALPHA, 100),
        (BETA, 100),
        (GAMMA, 100),
        (DELTA, 100),
        (ZETA, 100),
        (ETA, 100),
        (IOTA, 100),
    )
    order_days = [
        OrderDay(
            trade_date=day(1),
            buys=tuple(
                order(f"o{index}", BUY, symbol, quantity)
                for index, (symbol, quantity) in enumerate(first_buys, start=1)
            ),
        ),
        OrderDay(
            trade_date=day(2),
            buys=(order("o8", BUY, THETA, 400),),
        ),
        OrderDay(
            trade_date=day(8),
            sells=(order("o9", SELL, BETA, 100),),
        ),
        OrderDay(
            trade_date=day(31),
            buys=(order("o10", BUY, EPSILON, 100),),
        ),
        OrderDay(
            trade_date=day(40),
            buys=(order("o11", BUY, ETA, 100),),
        ),
    ]
    return tuple(order_days)


_DAYS = _business_days(date(2020, 1, 2), 80)
_MARKET = _SyntheticMarket(
    days=tuple(_DAYS),
    bars=_build_bars(_DAYS),
    corporate_actions=_build_actions(_DAYS),
    benchmarks=_build_benchmarks(_DAYS),
    schedule=_build_schedule(),
)


def _write_fixture(name: str, frame: pd.DataFrame) -> None:
    """Persist a deterministic fixture once so the repo owns the golden bytes."""
    _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = _FIXTURE_DIR / name
    if not path.exists():
        frame.to_parquet(path, index=False)


_write_fixture("bars.parquet", _MARKET.bars)
_write_fixture("corporate_actions.parquet", _MARKET.corporate_actions)
_write_fixture("benchmarks.parquet", _MARKET.benchmarks)


def _read_fixture(name: str) -> pd.DataFrame:
    return pd.read_parquet(_FIXTURE_DIR / name)


def _request(scenario: str) -> BacktestRequest:
    spec = _SCENARIOS[scenario]
    calendar = TradingCalendar.from_open_days(_MARKET.days)
    rule_book = TradingRuleBook.from_yaml(
        _REPO_ROOT / "configs" / "trading_rules.yml"
    )
    return BacktestRequest(
        dataset_version=_DATASET_VERSION,
        initial_cash=_INITIAL_CASH,
        calendar=calendar,
        rule_book=rule_book,
        cost_model=CostModel(_cost_rate(spec)),
        bars=_read_fixture("bars.parquet"),
        corporate_actions=_read_fixture("corporate_actions.parquet"),
        benchmarks=_read_fixture("benchmarks.parquet"),
        schedule=_MARKET.schedule,
    )


# --------------------------------------------------------------------------- #
# An independent reference simulation
# --------------------------------------------------------------------------- #


def _round2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=_HALF_UP)


def _quote(spec: _Spec, side: str, quantity: int, raw_open: Decimal):
    factor = Decimal("1") + spec.slippage_rate
    if side == SELL:
        factor = Decimal("1") - spec.slippage_rate
    price = _round2(raw_open * factor)
    gross = price * quantity
    commission = max(
        gross * spec.commission_rate, spec.minimum_commission
    ).quantize(CENT, rounding=_HALF_UP)
    stamp_tax = Decimal("0")
    if side == SELL:
        stamp_tax = _round2(gross * spec.stamp_tax_sell_rate)
    return price, gross, commission, stamp_tax


def _dividend(quantity: int, per_share: Decimal) -> Decimal:
    return _round2(Decimal(quantity) * per_share)


def _bonus_shares(held: int, bonus: Decimal, capitalization: Decimal) -> int:
    return int(
        (Decimal(held) * (bonus + capitalization)).to_integral_value(
            rounding=_HALF_UP
        )
    )


def _reference_run(spec: _Spec, market: _SyntheticMarket) -> dict[str, pd.DataFrame]:
    """Replay the scenario with Decimal money; the golden the engine must equal."""
    days = market.days
    session_of = {day: index for index, day in enumerate(days)}

    # ---- corporate actions, keyed by ex session --------------------------- #
    # Identical duplicate rows from a second source collapse to one event,
    # exactly as the engine's unique-action-id booking is a no-op.
    actions_by_session: dict[int, list[tuple]] = {}
    seen: set[tuple] = set()
    for record in market.corporate_actions.to_dict("records"):
        ex = session_of[record["ex_date"]]
        record_session = session_of[record["record_date"]]
        facts = (
            record["symbol"],
            record_session,
            ex,
            Decimal(str(record["cash_dividend_per_share"] or 0)),
            Decimal(str(record["bonus_share_ratio"] or 0)),
            Decimal(str(record["capitalization_ratio"] or 0)),
        )
        if facts in seen:
            continue
        seen.add(facts)
        actions_by_session.setdefault(ex, []).append(
            (
                facts[0],
                facts[1],
                facts[3],
                facts[4],
                facts[5],
                str(record["source"]),
            )
        )

    # ---- schedule by session --------------------------------------------- #
    orders_by_session: dict[int, list[Order]] = {}
    for order_day in market.schedule:
        orders_by_session.setdefault(session_of[order_day.trade_date], []).extend(
            list(order_day.sells) + list(order_day.buys)
        )

    cash = _INITIAL_CASH
    lots: list[dict] = []  # {symbol, buy, qty, avail}
    fills: list[dict] = []
    rejections: list[dict] = []
    action_entries: list[dict] = []
    equity_rows: list[dict] = []
    fill_seq = 0

    last_close: dict[str, Decimal] = {}
    last_valid: dict[str, int] = {}

    def quantity(symbol: str) -> int:
        return sum(lot["qty"] for lot in lots if lot["symbol"] == symbol)

    def sellable(symbol: str, day_session: int) -> int:
        return sum(
            lot["qty"]
            for lot in lots
            if lot["symbol"] == symbol and lot["avail"] is not None
            and lot["avail"] <= day_session
        )

    def consume(symbol: str, take: int, day_session: int) -> None:
        remaining = take
        for lot in lots:
            if lot["symbol"] != symbol:
                continue
            if lot["avail"] is not None and lot["avail"] <= day_session:
                used = min(lot["qty"], remaining)
                lot["qty"] -= used
                remaining -= used
                if remaining <= 0:
                    break
        assert remaining == 0, "reference sell under-consumed a lot"

    def record_fill(order: Order, day_session: int, price: Decimal,
                    quantity_: int, commission: Decimal,
                    stamp_tax: Decimal, reference_price: Decimal) -> None:
        nonlocal fill_seq
        fill_seq += 1
        fills.append(
            {
                "trade_date": days[day_session],
                "fill_id": f"F{fill_seq:06d}",
                "order_id": order.order_id,
                "side": order.side,
                "symbol": order.symbol,
                "quantity": quantity_,
                "price": float(price),
                "commission": float(commission),
                "stamp_tax": float(stamp_tax),
                "reference_price": float(reference_price),
            }
        )

    def record_rejection(order: Order, day_session: int, reason: str,
                         filled: int = 0) -> None:
        rejections.append(
            {
                "trade_date": days[day_session],
                "order_id": order.order_id,
                "side": order.side,
                "symbol": order.symbol,
                "requested_quantity": order.quantity,
                "filled_quantity": filled,
                "rejected_quantity": order.quantity - filled,
                "reason": reason,
            }
        )

    def execute_order(order: Order, day_session: int) -> None:
        nonlocal cash
        open_value = Decimal(str(_price(order.symbol, day_session)))
        price, gross, commission, stamp_tax = _quote(
            spec, order.side, order.quantity, open_value
        )
        pre_close = last_close.get(order.symbol)
        # Conservative price-limit band around the prior unadjusted close.
        if pre_close is not None:
            upper = _round2(pre_close * Decimal("1.10"))
            lower = _round2(pre_close * Decimal("0.90"))
            blocked = (
                order.side == BUY and price >= upper
            ) or (
                order.side == SELL and price <= lower
            )
            if blocked:
                reason = (
                    REASON_BUY_AT_UPPER_LIMIT if order.side == BUY
                    else "sell_at_lower_limit"
                )
                record_rejection(order, day_session, reason)
                return
        if order.side == SELL:
            if sellable(order.symbol, day_session) < order.quantity:
                record_rejection(order, day_session,
                                 "insufficient_sellable_quantity")
                return
            cash += gross - commission - stamp_tax
            consume(order.symbol, order.quantity, day_session)
            record_fill(order, day_session, price, order.quantity,
                        commission, stamp_tax, _round2(open_value))
            return
        # A buy is filled at the largest affordable whole 100-share lot.
        affordable = 0
        for candidate in range(order.quantity, 0, -100):
            quote_price, quote_gross, quote_commission, _ = _quote(
                spec, BUY, candidate, open_value
            )
            if quote_gross + quote_commission <= cash:
                affordable = candidate
                break
        if affordable == 0:
            record_rejection(order, day_session, "insufficient_cash")
            return
        quote_price, quote_gross, quote_commission, _ = _quote(
            spec, BUY, affordable, open_value
        )
        cash -= quote_gross + quote_commission
        lots.append(
            {
                "symbol": order.symbol,
                "buy": day_session,
                "qty": affordable,
                "avail": day_session + 1 if day_session + 1 < len(days) else None,
            }
        )
        record_fill(order, day_session, quote_price, affordable,
                    quote_commission, Decimal("0"), _round2(open_value))
        if affordable < order.quantity:
            record_rejection(order, day_session, "insufficient_cash",
                             filled=affordable)

    # ---- the chronological day loop -------------------------------------- #
    for session in range(len(days)):
        for (
            symbol,
            record_session,
            per_share,
            bonus,
            cap,
            source,
        ) in actions_by_session.get(session, []):
            if quantity(symbol) == 0:
                continue
            held_on_record = sum(
                lot["qty"]
                for lot in lots
                if lot["symbol"] == symbol and lot["buy"] <= record_session
            )
            credited = _dividend(held_on_record, per_share)
            added = _bonus_shares(quantity(symbol), bonus, cap)
            if credited > 0:
                cash += credited
            if added > 0:
                lots.append(
                    {
                        "symbol": symbol,
                        "buy": session,
                        "qty": added,
                        "avail": session + 1
                        if session + 1 < len(days)
                        else None,
                    }
                )
            if credited > 0 or added > 0:
                action_entries.append(
                    {
                        "seq": len(action_entries),
                        "action_id": f"{symbol}#{days[session].isoformat()}",
                        "symbol": symbol,
                        "ex_date": days[session],
                        "record_date": days[record_session],
                        "cash_credited": float(credited),
                        "shares_added": added,
                        "note": source,
                    }
                )
        for order in orders_by_session.get(session, []):
            execute_order(order, session)

        # Fold today's valid closes into the carry state, then value.
        for symbol in _SYMBOLS:
            price = _price(symbol, session)
            if price is not None and _quality(symbol, session) == "INFO":
                last_close[symbol] = Decimal(str(price))
                last_valid[symbol] = session

        market_value = Decimal("0")
        stale_market_value = Decimal("0")
        stale_days = 0
        for symbol in _SYMBOLS:
            held = quantity(symbol)
            if held == 0:
                continue
            close_today = _price(symbol, session)
            if close_today is not None and _quality(symbol, session) == "INFO":
                price = Decimal(str(close_today))
                stale = 0
            else:
                price = last_close[symbol]
                stale = session - last_valid[symbol]
            value = price * held
            market_value += value
            if stale > 0:
                stale_market_value += value
                stale_days = max(stale_days, stale)
        equity_rows.append(
            {
                "trade_date": days[session],
                "cash": float(cash),
                "market_value": float(market_value),
                "total_equity": float(cash + market_value),
                "stale_market_value": float(stale_market_value),
                "stale_days": stale_days,
            }
        )

    frames = {
        "fills": pd.DataFrame(
            fills,
            columns=[
                "trade_date", "fill_id", "order_id", "side", "symbol",
                "quantity", "price", "commission", "stamp_tax",
                "reference_price",
            ],
        ),
        "rejections": pd.DataFrame(
            rejections,
            columns=[
                "trade_date", "order_id", "side", "symbol",
                "requested_quantity", "filled_quantity", "rejected_quantity",
                "reason",
            ],
        ),
        "action_ledger": pd.DataFrame(
            action_entries,
            columns=[
                "seq", "action_id", "symbol", "ex_date", "record_date",
                "cash_credited", "shares_added", "note",
            ],
        ),
        "daily_equity": pd.DataFrame(
            equity_rows,
            columns=[
                "trade_date", "cash", "market_value", "total_equity",
                "stale_market_value", "stale_days",
            ],
        ),
    }
    for frame in frames.values():
        for column in ("quantity", "requested_quantity", "filled_quantity",
                       "rejected_quantity", "seq", "shares_added", "stale_days"):
            if column in frame.columns:
                frame[column] = frame[column].astype("int64")
    return frames


# --------------------------------------------------------------------------- #
# Fixture integrity
# --------------------------------------------------------------------------- #


def test_committed_fixtures_match_the_synthetic_market_builder():
    for name, frame in (
        ("bars.parquet", _MARKET.bars),
        ("corporate_actions.parquet", _MARKET.corporate_actions),
        ("benchmarks.parquet", _MARKET.benchmarks),
    ):
        assert_frame_equal(_read_fixture(name), frame)


def test_synthetic_market_covers_eighty_open_days_and_ten_names():
    assert len(_MARKET.days) == 80
    assert len(_MARKET.bars) == 765
    assert _MARKET.bars.trade_date.nunique() == 80
    assert _MARKET.bars.symbol.nunique() == 10
    assert _MARKET.benchmarks.symbol.nunique() == 2
    assert _MARKET.benchmarks.trade_date.nunique() == 80


# --------------------------------------------------------------------------- #
# Golden engine ledgers per scenario
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("scenario", ["zero_cost", "commission_tax", "full_cost"])
def test_backtest_golden_ledgers_match(scenario: str) -> None:
    request = _request(scenario)
    result = BacktestEngine().run(request)

    expected = _reference_run(_SCENARIOS[scenario], _MARKET)
    assert_frame_equal(result.fills, expected["fills"])
    assert_frame_equal(result.rejections, expected["rejections"])
    assert_frame_equal(result.action_ledger, expected["action_ledger"])
    assert_frame_equal(result.daily_equity, expected["daily_equity"])


def test_one_execution_simulator_runs_across_the_whole_account_lifetime():
    request = _request("full_cost")
    result = BacktestEngine().run(request)
    assert list(result.fills.fill_id) == [
        f"F{index:06d}" for index in range(1, len(result.fills) + 1)
    ]


def test_golden_corporate_action_ledger_records_one_entry_per_action_id():
    request = _request("full_cost")
    result = BacktestEngine().run(request)
    action_id = lambda session, symbol: (  # noqa: E731 - readable inline helper
        f"{symbol}#{_MARKET.days[session].isoformat()}"
    )
    assert result.action_ledger.to_dict("records") == [
        {
            "seq": 0,
            "action_id": action_id(6, ALPHA),
            "symbol": ALPHA,
            "ex_date": _MARKET.days[6],
            "record_date": _MARKET.days[4],
            "cash_credited": 40.0,
            "shares_added": 20,
            "note": "cninfo",
        },
        {
            "seq": 1,
            "action_id": action_id(14, DELTA),
            "symbol": DELTA,
            "ex_date": _MARKET.days[14],
            "record_date": _MARKET.days[12],
            "cash_credited": 100.0,
            "shares_added": 0,
            "note": "cninfo",
        },
        {
            "seq": 2,
            "action_id": action_id(52, IOTA),
            "symbol": IOTA,
            "ex_date": _MARKET.days[52],
            "record_date": _MARKET.days[50],
            "cash_credited": 20.0,
            "shares_added": 0,
            "note": "cninfo",
        },
        {
            "seq": 3,
            "action_id": action_id(60, ALPHA),
            "symbol": ALPHA,
            "ex_date": _MARKET.days[60],
            "record_date": _MARKET.days[58],
            "cash_credited": 0.0,
            "shares_added": 30,
            "note": "cninfo",
        },
    ]


# Independent hand cross-checks that catch a systematically shared wrong path.
def test_final_equity_matches_hand_computed_totals_per_scenario():
    for scenario, final_equity in (
        ("zero_cost", Decimal("400200.00")),
        ("commission_tax", Decimal("400064.50")),
        ("full_cost", Decimal("399755.41")),
    ):
        result = BacktestEngine().run(_request(scenario))
        assert result.daily_equity.total_equity.iloc[-1] == pytest.approx(
            float(final_equity), abs=1e-6
        )


def test_final_cash_and_holding_quantities_are_exact():
    result = BacktestEngine().run(_request("full_cost"))
    last = result.daily_equity.iloc[-1]
    assert last.cash == pytest.approx(92715.41)
    assert last.total_equity == pytest.approx(399755.41)
    # ALPHA 100 -> +20 bonus (ex 6) -> +30 capitalization (ex 60) = 150.
    assert list(result.fills[result.fills.symbol == ALPHA].quantity) == [100]
    assert result.daily_equity[result.daily_equity.stale_days > 0].stale_days.max() == 5


# --------------------------------------------------------------------------- #
# Readiness failures abort before any account is mutated
# --------------------------------------------------------------------------- #


def _request_with(bars=None, actions=None, benchmarks=None,
                  schedule=None) -> BacktestRequest:
    spec = _SCENARIOS["full_cost"]
    calendar = TradingCalendar.from_open_days(_MARKET.days)
    return BacktestRequest(
        dataset_version=_DATASET_VERSION,
        initial_cash=_INITIAL_CASH,
        calendar=calendar,
        rule_book=TradingRuleBook.from_yaml(_REPO_ROOT / "configs"
                                            / "trading_rules.yml"),
        cost_model=CostModel(_cost_rate(spec)),
        bars=_read_fixture("bars.parquet") if bars is None else bars,
        corporate_actions=(_read_fixture("corporate_actions.parquet")
                           if actions is None else actions),
        benchmarks=(_read_fixture("benchmarks.parquet")
                    if benchmarks is None else benchmarks),
        schedule=_MARKET.schedule if schedule is None else schedule,
    )


def test_readiness_rejects_an_incomplete_benchmark_coverage():
    benchmarks = _read_fixture("benchmarks.parquet").copy()
    benchmarks = benchmarks[benchmarks.trade_date != _MARKET.days[40]]
    with pytest.raises(ReadinessError):
        BacktestEngine().run(_request_with(benchmarks=benchmarks))


def test_execution_records_rejection_for_an_order_on_a_suspended_bar():
    bars = _read_fixture("bars.parquet").copy()
    # GAMMA trades sessions 21..25 to no bar already; instead force a scheduled
    # order date's bar away to simulate an execution-day suspension.
    bars = bars[bars.trade_date != _MARKET.days[1]]  # no bars on buy day S1
    result = BacktestEngine().run(_request_with(bars=bars))
    assert any(
        rejection["reason"] == "suspended_or_unknown"
        for rejection in result.rejections.to_dict("records")
    )


def test_golden_submitted_orders_match_the_plan_schedule():
    """Spec section 3: on the fixed schedule the submitted set equals the plan
    ledger order-for-order (id is the join key, fields confirm)."""
    result = BacktestEngine().run(_request("full_cost"))
    rows = []
    for order_day in _MARKET.schedule:
        for order in list(order_day.sells) + list(order_day.buys):
            rows.append(
                {
                    "trade_date": order_day.trade_date,
                    "order_id": order.order_id,
                    "side": order.side,
                    "symbol": order.symbol,
                    "quantity": order.quantity,
                }
            )
    expected = pd.DataFrame(rows, columns=list(SUBMITTED_ORDER_COLUMNS))
    expected["quantity"] = expected["quantity"].astype("int64")
    assert_frame_equal(result.submitted_orders, expected)
    assert result.submitted_orders["order_id"].is_unique


def test_pure_intent_plan_replay_records_fills_partials_and_rejections():
    """The engine submits the raw plan (no pre-sizing) and the executor turns
    affordability / price-limit / suspension into fills and rejections."""
    request = BacktestRequest(
        dataset_version=_DATASET_VERSION,
        initial_cash=1500,
        calendar=TradingCalendar.from_open_days(_MARKET.days),
        rule_book=TradingRuleBook.from_yaml(
            _REPO_ROOT / "configs" / "trading_rules.yml"
        ),
        cost_model=CostModel(_cost_rate(_SCENARIOS["full_cost"])),
        bars=_MARKET.bars,
        corporate_actions=_MARKET.corporate_actions,
        benchmarks=_MARKET.benchmarks,
        schedule=(
            OrderDay(
                trade_date=_DAYS[1],
                sells=(Order("o102", SELL, ALPHA, 200),),
                buys=(Order("o101", BUY, ALPHA, 200),),
            ),
            OrderDay(
                trade_date=_DAYS[22],
                buys=(Order("o104", BUY, GAMMA, 100),),
            ),
            OrderDay(
                trade_date=_DAYS[40],
                buys=(Order("o103", BUY, ETA, 100),),
            ),
        ),
    )

    result = BacktestEngine().run(request)

    # The whole raw plan was submitted: nothing was trimmed or re-sized by
    # execution-day information.
    submitted = sorted(
        (row.order_id, row.side, row.symbol, int(row.quantity))
        for row in result.submitted_orders.itertuples()
    )
    assert submitted == sorted(
        [
            ("o101", BUY, ALPHA, 200),
            ("o102", SELL, ALPHA, 200),
            ("o103", BUY, ETA, 100),
            ("o104", BUY, GAMMA, 100),
        ]
    )
    # o101 is cash-partial (one whole lot fits), everything else rejected.
    assert [(row.order_id, int(row.quantity)) for row in result.fills.itertuples()] == [
        ("o101", 100)
    ]
    assert {
        (row.order_id, row.reason, int(row.rejected_quantity))
        for row in result.rejections.itertuples()
    } == {
        ("o101", "insufficient_cash", 100),
        ("o102", "insufficient_sellable_quantity", 200),
        ("o103", REASON_BUY_AT_UPPER_LIMIT, 100),
        ("o104", "suspended_or_unknown", 100),
    }


def test_changing_only_an_execution_day_open_changes_only_that_orders_outcome():
    """P0.3 acceptance: perturb one execution-day open (ETA day 40 to below its
    upper limit) and only that order's fill/rejection outcome changes; the
    submitted order set -- the frozen plan -- is byte-identical."""
    variant = _read_fixture("bars.parquet").copy()
    mask = (variant["symbol"] == ETA) & (variant["trade_date"] == _DAYS[40])
    variant.loc[mask, "open"] = 10.9  # under the 11.0 upper limit
    base = BacktestEngine().run(_request_with(schedule=_MARKET.schedule))
    changed = BacktestEngine().run(
        _request_with(bars=variant, schedule=_MARKET.schedule)
    )
    assert_frame_equal(base.submitted_orders, changed.submitted_orders)
    # Everything except order o11 is identical across both runs.
    assert_frame_equal(
        base.fills[base.fills.order_id != "o11"].reset_index(drop=True),
        changed.fills[changed.fills.order_id != "o11"].reset_index(drop=True),
    )
    assert_frame_equal(
        base.rejections[base.rejections.order_id != "o11"].reset_index(drop=True),
        changed.rejections[changed.rejections.order_id != "o11"].reset_index(
            drop=True
        ),
    )
    # o11: upper-limit rejection in the base, a fill in the changed run.
    assert list(base.rejections[base.rejections.order_id == "o11"].reason) == [
        REASON_BUY_AT_UPPER_LIMIT
    ]
    assert changed.rejections[changed.rejections.order_id == "o11"].empty
    assert list(changed.fills[changed.fills.order_id == "o11"].quantity) == [100]


def test_readiness_rejects_a_cross_source_conflict_on_a_held_name():
    actions = _read_fixture("corporate_actions.parquet").copy()
    dup = actions[actions.symbol == IOTA].copy()
    dup["cash_dividend_per_share"] = 0.9  # conflict with the other IOTA row
    actions = pd.concat([actions, dup], ignore_index=True)
    with pytest.raises(UnsupportedCorporateAction):
        BacktestEngine().run(_request_with(actions=actions))


def test_readiness_rejects_a_rights_issue_on_a_held_name():
    actions = _read_fixture("corporate_actions.parquet").copy()
    row = actions[(actions.symbol == IOTA) & (actions.ex_date == _MARKET.days[52])]
    actions.loc[row.index[0], "rights_issue_ratio"] = 0.3
    with pytest.raises(UnsupportedCorporateAction):
        BacktestEngine().run(_request_with(actions=actions))


def test_a_scheduled_sell_funds_a_same_day_buy_within_one_execution():
    # Full-cost S1 buys leave 392958.00. Adding a same-day sell of BETA (a
    # holding already T+1 available) plus a follow-on KAPPA buy exercises the
    # executor's sell-before-buy rule: the sell's proceeds fund the buy in the
    # same execution session and the fill ids stay strictly increasing.
    schedule = (
        _MARKET.schedule[:2]
        + (
            OrderDay(
                trade_date=_MARKET.days[8],
                sells=(Order("o12", SELL, BETA, 100),),
                buys=(Order("o13", BUY, KAPPA, 100),),
            ),
        )
        + _MARKET.schedule[3:]
    )
    result = BacktestEngine().run(_request_with(schedule=schedule))
    session_fills = result.fills[result.fills.trade_date == _MARKET.days[8]]
    assert list(session_fills.side) == ["SELL", "BUY"]
    assert list(session_fills.symbol) == [BETA, KAPPA]
    assert session_fills.fill_id.iloc[0] < session_fills.fill_id.iloc[1]


def test_ex_date_cash_and_share_credits_conserve_total_equity():
    # Each ex-date books cash/shares exactly against the unadjusted price drop,
    # so total equity is unchanged across the ex-date close (no trades that day).
    request = _request("full_cost")
    result = BacktestEngine().run(request)
    total = result.daily_equity.total_equity
    for boundary in (5, 13, 51, 59):  # closes just before each ex-date
        assert total.iloc[boundary] == pytest.approx(total.iloc[boundary + 1])


def test_carried_prices_are_valuation_only_and_never_fill_orders():
    # A sell of GAMMA on its resume day S26 (open 11.00) must fill at the fresh
    # slippage-adjusted open, never at the carried 10.00 valuation price.
    schedule = _MARKET.schedule + (
        OrderDay(
            trade_date=_MARKET.days[26],
            sells=(Order("o12", SELL, GAMMA, 100),),
        ),
    )
    result = BacktestEngine().run(_request_with(schedule=schedule))
    fill = result.fills[result.fills.order_id == "o12"].iloc[0]
    assert fill.price == pytest.approx(10.99)  # 11.00 * 0.999, rounded to a cent
    equity = result.daily_equity
    assert equity[equity.trade_date == _MARKET.days[25]].stale_days.iloc[0] == 5
    assert equity[equity.trade_date == _MARKET.days[25]].stale_market_value.iloc[0] == (
        100.0 * 10.0
    )


# --------------------------------------------------------------------------- #
# Provider mode: account-aware rebalance orders from the realized account
# --------------------------------------------------------------------------- #


# A corporate-action-free subset of the synthetic market (the I1 premise:
# flat or plainly-moving prices, no bonus/split credits, sells unblocked).
_PROVIDER_SYMBOLS = (BETA, DELTA, ETA, KAPPA)

_S1, _S8, _S14, _S15, _S41 = 1, 8, 14, 15, 41
_D1, _D8, _D14, _D15, _D41 = (_DAYS[s] for s in (_S1, _S8, _S14, _S15, _S41))


def _provider_bars() -> pd.DataFrame:
    rows: list[dict] = []
    for session, trade_date in enumerate(_DAYS):
        for symbol in _PROVIDER_SYMBOLS:
            price = _price(symbol, session)
            if price is None:
                continue
            rows.append(
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "open": price,
                    "close": price,
                    "quality_severity": "INFO",
                    "status": "NORMAL",
                }
            )
    return pd.DataFrame(rows)


def _provider_request(
    scenario: str,
    targets_by_day: dict,
    provider=None,
    initial_cash: Decimal = _INITIAL_CASH,
) -> BacktestRequest:
    spec = _SCENARIOS[scenario]
    rebalancer = AccountAwareWeeklyRebalancer(targets_by_day)
    return BacktestRequest(
        dataset_version=_DATASET_VERSION,
        initial_cash=initial_cash,
        calendar=TradingCalendar.from_open_days(_MARKET.days),
        rule_book=TradingRuleBook.from_yaml(
            _REPO_ROOT / "configs" / "trading_rules.yml"
        ),
        cost_model=CostModel(_cost_rate(spec)),
        bars=_provider_bars(),
        corporate_actions=pd.DataFrame(),
        benchmarks=_read_fixture("benchmarks.parquet"),
        schedule=(),
        order_provider=(
            rebalancer.orders_for if provider is None else provider
        ),
        possible_held_symbols=frozenset(_PROVIDER_SYMBOLS),
    )


def _final_holdings(result) -> dict[str, int]:
    fills = result.fills
    bought = fills[fills.side == BUY].groupby("symbol")["quantity"].sum()
    sold = fills[fills.side == SELL].groupby("symbol")["quantity"].sum()
    return bought.subtract(sold, fill_value=0).astype(int).to_dict()


def test_provider_mode_stale_residual_sell_is_resubmitted_and_fills():
    """I1a/M0 engine-level: the W14 DELTA exit sell is price-limit blocked, so
    W15 -- target still 0, holdings still 100 -- submits SELL DELTA again; the
    W15 bar carries full execution metadata (prior close + open) and the
    executor adjudicates the resubmission normally."""
    targets = {
        _D1: (_DAYS[0], {BETA: 200, DELTA: 100}),
        _D14: (_DAYS[13], {BETA: 200, DELTA: 0}),
        _D15: (_DAYS[14], {BETA: 200, DELTA: 0}),
    }
    result = BacktestEngine().run(
        _provider_request("zero_cost", targets)
    )

    submitted = result.submitted_orders
    delta_sells = submitted[
        (submitted.symbol == DELTA) & (submitted.side == SELL)
    ]
    assert list(delta_sells.trade_date) == [_D14, _D15]
    assert list(delta_sells.side) == [SELL, SELL]
    assert list(delta_sells.quantity) == [100, 100]
    # W14: sell 9.00 against a 10.00 prior close sits at the lower limit.
    blocked = result.rejections[result.rejections.symbol == DELTA]
    assert list(blocked.reason) == [REASON_SELL_AT_LOWER_LIMIT]
    assert list(blocked.trade_date) == [_D14]
    # W15: prior close 9.00, sell 9.00 is inside the band and fills.
    retry_fill = result.fills[
        (result.fills.symbol == DELTA) & (result.fills.side == SELL)
    ]
    assert list(retry_fill.trade_date) == [_D15]
    assert list(retry_fill.quantity) == [100]
    assert _final_holdings(result) == {BETA: 200, DELTA: 0}


def test_provider_mode_partial_fill_residual_is_recomputed_and_tops_up():
    """I1a: with cash for only one lot, W1's BUY 200 partially fills (100) and
    the remainder is rejected; W2's exit sells exactly the realized residual
    (100, not the ideal 200); W3's re-entry completes and final holdings equal
    the final target (I2 on this cash-sufficient fixture)."""
    targets = {
        _D1: (_DAYS[0], {BETA: 200}),
        _D8: (_DAYS[7], {BETA: 0}),
        _D15: (_DAYS[14], {BETA: 100}),
    }
    result = BacktestEngine().run(
        _provider_request("full_cost", targets, initial_cash=Decimal("1500"))
    )

    buys = result.fills[result.fills.side == BUY]
    assert list(buys.loc[buys.trade_date == _D1, "quantity"]) == [100]
    sells = result.fills[result.fills.side == SELL]
    assert list(sells.loc[sells.trade_date == _D8, "quantity"]) == [100]
    assert list(buys.loc[buys.trade_date == _D15, "quantity"]) == [100]
    # The W2 exit was sized from the realized account (100 held), not from any
    # idealized previous book (200 target of W1).
    residual_sell = result.submitted_orders[
        (result.submitted_orders.side == SELL)
        & (result.submitted_orders.trade_date == _D8)
    ]
    assert list(residual_sell.quantity) == [100]
    assert _final_holdings(result) == {BETA: 100}


def test_provider_mode_same_day_sell_funds_the_buy():
    """The rebalancer returns sells before buys and the executor keeps
    sell-before-buy, so a buy the cash alone cannot afford still fills in
    full behind the same-day exit -- no avoidable insufficient_cash."""
    targets = {
        _D1: (_DAYS[0], {DELTA: 100}),
        _D8: (_DAYS[7], {DELTA: 0, BETA: 100}),
    }
    result = BacktestEngine().run(
        _provider_request("full_cost", targets, initial_cash=Decimal("1500"))
    )
    day_fills = result.fills[result.fills.trade_date == _D8]
    assert list(day_fills.side) == [SELL, BUY]
    assert list(day_fills.symbol) == [DELTA, BETA]
    assert list(day_fills.quantity) == [100, 100]
    assert not (
        result.rejections.reason == "insufficient_cash"
    ).any()
    assert _final_holdings(result) == {BETA: 100, DELTA: 0}


def test_provider_fidelity_engine_submits_exactly_what_the_provider_returns():
    """I3: a wrapper that captures the account at the provider-call instant
    proves the engine submits the provider's orders verbatim and that the
    provider saw the realized (post-partial-fill) account, not an idealized
    book -- fidelity, not the engine re-deriving its own orders."""
    targets = {
        _D1: (_DAYS[0], {BETA: 200}),
        _D8: (_DAYS[7], {BETA: 0}),
        _D15: (_DAYS[14], {BETA: 100}),
    }
    captured: dict = {}
    returned: dict = {}
    inner = AccountAwareWeeklyRebalancer(targets)

    def provider(day, account):
        captured[day] = (
            float(account.cash),
            {
                symbol: account.position_quantity(symbol)
                for symbol in _PROVIDER_SYMBOLS
            },
        )
        orders = inner.orders_for(day, account)
        returned[day] = orders
        return orders

    result = BacktestEngine().run(
        _provider_request("full_cost", targets, provider=provider,
                          initial_cash=Decimal("1500"))
    )

    rows = [
        {
            "trade_date": day,
            "order_id": order.order_id,
            "side": order.side,
            "symbol": order.symbol,
            "quantity": order.quantity,
        }
        for day, orders in sorted(returned.items())
        for order in orders
    ]
    expected = pd.DataFrame(rows, columns=list(SUBMITTED_ORDER_COLUMNS))
    expected = expected.astype({"quantity": "int64"})
    assert_frame_equal(result.submitted_orders, expected)
    # The W1 call saw the empty account; the W2 call saw the realized one-lot
    # holding left by the partial fill.
    assert captured[_D1] == (1500.0, {BETA: 0, DELTA: 0, ETA: 0, KAPPA: 0})
    assert captured[_D8][1][BETA] == 100


def test_provider_mode_holdings_converge_across_scenarios_and_match_target():
    """I1/I2/I4 on an upper-bound fixture (CA-free, sells unblocked, cash
    ample): every scenario fully executes the same target differences, so
    final holdings are identical and equal the final target book, and final
    equity is ordered zero >= commission_tax >= full_cost."""
    targets = {
        _D1: (_DAYS[0], {BETA: 200, DELTA: 100, KAPPA: 100}),
        _D15: (_DAYS[14], {BETA: 200, DELTA: 0, KAPPA: 100}),
        _D41: (_DAYS[40], {BETA: 300, DELTA: 0, KAPPA: 100}),
    }
    final_equity: dict[str, float] = {}
    for scenario in ("zero_cost", "commission_tax", "full_cost"):
        result = BacktestEngine().run(_provider_request(scenario, targets))
        holdings = _final_holdings(result)
        assert holdings == {BETA: 300, DELTA: 0, KAPPA: 100}
        final_equity[scenario] = float(result.daily_equity.total_equity.iloc[-1])
    assert final_equity["zero_cost"] >= final_equity["commission_tax"]
    assert final_equity["commission_tax"] >= final_equity["full_cost"]


def test_empty_schedule_without_provider_keeps_the_legacy_behavior():
    """Mode-judgment cheap case 1: order_provider=None with the legitimate
    empty schedule stays a schedule-mode request (empty possible-held union,
    no provider validation error) and replays an orderless window."""
    request = BacktestRequest(
        dataset_version=_DATASET_VERSION,
        initial_cash=_INITIAL_CASH,
        calendar=TradingCalendar.from_open_days(_MARKET.days),
        rule_book=TradingRuleBook.from_yaml(
            _REPO_ROOT / "configs" / "trading_rules.yml"
        ),
        cost_model=CostModel(_cost_rate(_SCENARIOS["full_cost"])),
        bars=_provider_bars(),
        corporate_actions=pd.DataFrame(),
        benchmarks=_read_fixture("benchmarks.parquet"),
        schedule=(),
    )
    result = BacktestEngine().run(request)
    assert result.submitted_orders.empty
    assert result.fills.empty
    assert len(result.daily_equity) == 80


def test_provider_without_possible_held_symbols_is_a_config_error():
    """Mode-judgment cheap case 2: provider mode with possible_held_symbols
    left as None is rejected at construction -- it carries the CA-coverage
    universe, so silently accepting None would break that contract."""
    rebalancer = AccountAwareWeeklyRebalancer(
        {_D1: (_DAYS[0], {BETA: 100})}
    )
    with pytest.raises(ValueError, match="possible_held_symbols"):
        BacktestRequest(
            dataset_version=_DATASET_VERSION,
            initial_cash=_INITIAL_CASH,
            calendar=TradingCalendar.from_open_days(_MARKET.days),
            rule_book=TradingRuleBook.from_yaml(
                _REPO_ROOT / "configs" / "trading_rules.yml"
            ),
            cost_model=CostModel(_cost_rate(_SCENARIOS["full_cost"])),
            bars=_provider_bars(),
            corporate_actions=pd.DataFrame(),
            benchmarks=_read_fixture("benchmarks.parquet"),
            schedule=(),
            order_provider=rebalancer.orders_for,
        )


# --------------------------------------------------------------------------- #
# Buffered weight-target provider: execution-data isolation (buffered plan)
# ---------------------------------------------------------------------------


def _buffered_weight_provider_request(
    bars: pd.DataFrame,
    *,
    signal_session: int,
    execution_session: int,
) -> BacktestRequest:
    """A provider-mode request driven by one frozen ``WeightTargetPeriod``.

    The period fixes the signal-close valuation prices (the bars' closes on
    the signal session) and 50/50 target weights over BETA/DELTA; the
    rebalancer may read nothing else -- in particular no execution open.
    """
    signal_day = _DAYS[signal_session]
    execution_day = _DAYS[execution_session]
    prices = {BETA: Decimal("10"), DELTA: Decimal("10")}
    period = WeightTargetPeriod(
        signal_date=signal_day,
        target_weights={BETA: Decimal("0.5"), DELTA: Decimal("0.5")},
        net_equity_prices=prices,
        previous_members=frozenset(),
        current_members=frozenset(prices),
    )
    rebalancer = WeightTargetRebalancer({execution_day: period}, lot_size=100)
    return BacktestRequest(
        dataset_version=_DATASET_VERSION,
        initial_cash=_INITIAL_CASH,
        calendar=TradingCalendar.from_open_days(_MARKET.days),
        rule_book=TradingRuleBook.from_yaml(
            _REPO_ROOT / "configs" / "trading_rules.yml"
        ),
        cost_model=CostModel(_cost_rate(_SCENARIOS["zero_cost"])),
        bars=bars,
        corporate_actions=pd.DataFrame(),
        benchmarks=_read_fixture("benchmarks.parquet"),
        schedule=(),
        order_provider=rebalancer.orders_for,
        possible_held_symbols=frozenset(prices),
    )


def _bars_with_open(session: int, symbol: str, value: Decimal) -> pd.DataFrame:
    """The provider bars with one execution-day open replaced."""
    bars = _provider_bars().copy()
    mask = (bars["symbol"] == symbol) & (bars["trade_date"] == _DAYS[session])
    assert mask.any(), "the mutated bar must exist"
    bars.loc[mask, "open"] = float(value)
    return bars


def test_execution_open_change_does_not_change_submitted_orders():
    # plan signature: two replays differing ONLY in one execution open must
    # submit identical orders (the provider cannot read execution data) while
    # the realized fills diverge.
    base = _buffered_weight_provider_request(
        _provider_bars(), signal_session=10, execution_session=11
    )
    mutated = _buffered_weight_provider_request(
        _bars_with_open(11, DELTA, Decimal("12")),
        signal_session=10,
        execution_session=11,
    )
    left = BacktestEngine().run(base)
    right = BacktestEngine().run(mutated)
    assert_frame_equal(left.submitted_orders, right.submitted_orders)
    assert not left.fills.equals(right.fills)
    # the identical plan is the 50/50 lot-floored book over 400,000
    assert sorted(left.submitted_orders["symbol"].tolist()) == [BETA, DELTA]
    buys = left.submitted_orders[left.submitted_orders["side"] == BUY]
    assert set(buys["quantity"]) == {20000}


def test_buffered_period_prices_stay_the_signal_closes():
    # mutating the EXECUTION-day close must not move the submitted plan
    # either: only the signal-session close enters the period's price map.
    bars = _provider_bars().copy()
    mask = (bars["symbol"] == DELTA) & (bars["trade_date"] == _DAYS[11])
    bars.loc[mask, "close"] = 99.0
    left = BacktestEngine().run(
        _buffered_weight_provider_request(
            _provider_bars(), signal_session=10, execution_session=11
        )
    )
    right = BacktestEngine().run(
        _buffered_weight_provider_request(
            bars, signal_session=10, execution_session=11
        )
    )
    assert_frame_equal(left.submitted_orders, right.submitted_orders)
