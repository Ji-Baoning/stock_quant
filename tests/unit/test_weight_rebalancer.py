"""Scenario-local whole-lot sizing from the common weight target.

``WeightTargetRebalancer`` converts one frozen ``WeightTargetPeriod`` into a
scenario's orders from that scenario's *signal-close* account state: the
account is valued only with the period's ``net_equity_prices`` (the
signal-close map plus approved carried marks), target quantities are the
lot-floor of ``target_weight * signal_close_equity / signal_price``, and the
band applies only to continuing positions.  The rebalancer never reads an
execution open, suspension, limit or any later price -- those facts live
solely in ``ExecutionSimulator`` -- so two scenarios sharing the common
weights may diverge in quantities and orders while identical execution data
can never change the submitted plan.

All tests are offline and in-memory.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_quant.backtest.account import Account
from stock_quant.backtest.models import BUY, SELL, Fill
from stock_quant.backtest.weight_rebalancer import WeightTargetRebalancer
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.portfolio.buffered_models import (
    REBALANCE_DECISION_COLUMNS,
    WeightTargetPeriod,
)
from stock_quant.portfolio.rebalance_band import REASON_WITHIN_BAND

SIGNAL_DATE = date(2021, 6, 1)
EXECUTION_DAY = date(2021, 6, 2)


def _calendar() -> TradingCalendar:
    days: list[date] = []
    current = date(2021, 1, 4)
    while len(days) < 120:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return TradingCalendar.from_open_days(days)


def _period(
    *,
    target_weights: dict[str, Decimal] | None = None,
    prices: dict[str, Decimal] | None = None,
    previous: tuple[str, ...] = ("000001.SZ", "000002.SZ"),
    current: tuple[str, ...] = ("000001.SZ", "000002.SZ"),
) -> WeightTargetPeriod:
    return WeightTargetPeriod(
        signal_date=SIGNAL_DATE,
        target_weights=target_weights
        or {"000001.SZ": Decimal("0.6"), "000002.SZ": Decimal("0.4")},
        net_equity_prices=prices
        or {"000001.SZ": Decimal("10"), "000002.SZ": Decimal("20")},
        previous_members=frozenset(previous),
        current_members=frozenset(current),
    )


def _account(cash: int, positions: dict[str, int]) -> Account:
    """An account holding ``positions`` bought at the signal close minus one."""
    calendar = _calendar()
    account = Account(Decimal(cash), calendar=calendar)
    buy_day = SIGNAL_DATE - timedelta(days=3)
    for index, (symbol, quantity) in enumerate(sorted(positions.items()), start=1):
        price = Decimal("10") if symbol == "000001.SZ" else Decimal("20")
        account.apply_fill(
            Fill(
                fill_id=f"seed{index}",
                order_id=f"seed-order{index}",
                trade_date=buy_day,
                side=BUY,
                symbol=symbol,
                quantity=quantity,
                price=price,
                commission=Decimal("0"),
                stamp_tax=Decimal("0"),
            )
        )
    assert account.position_quantity("000001.SZ") == positions.get(
        "000001.SZ", 0
    )
    return account


def _periods(period: WeightTargetPeriod) -> dict[date, WeightTargetPeriod]:
    return {EXECUTION_DAY: period}


# ---------------------------------------------------------------------------
# Order generation
# ---------------------------------------------------------------------------


def test_non_rebalance_days_produce_no_orders():
    rebalancer = WeightTargetRebalancer(_periods(_period()), lot_size=100)
    empty_account = _account(1_000_000, {})
    assert rebalancer.orders_for(SIGNAL_DATE, empty_account) == ()
    assert rebalancer.orders_for(EXECUTION_DAY + timedelta(days=1),
                                 empty_account) == ()


def test_signal_dates_map_by_execution_day():
    rebalancer = WeightTargetRebalancer(_periods(_period()), lot_size=100)
    assert rebalancer.signal_date_by_execution_day == {
        EXECUTION_DAY: SIGNAL_DATE
    }


def test_first_rebalance_buys_the_lot_floored_target_quantities():
    rebalancer = WeightTargetRebalancer(_periods(_period()), lot_size=100)
    account = _account(1_000_000, {})
    orders = rebalancer.orders_for(EXECUTION_DAY, account)
    # signal-close equity is exactly 1,000,000: 000001.SZ targets
    # floor(0.6 * 1,000,000 / 10 / 100) * 100 = 60,000 shares, 000002.SZ
    # floor(0.4 * 1,000,000 / 20 / 100) * 100 = 20,000 shares
    assert [(order.side, order.symbol, order.quantity) for order in orders] == [
        (BUY, "000001.SZ", 60000),
        (BUY, "000002.SZ", 20000),
    ]
    assert all(order.note.endswith(SIGNAL_DATE.isoformat()) for order in orders)


def test_sells_precede_buys_and_symbols_ascend():
    period = WeightTargetPeriod(
        signal_date=SIGNAL_DATE,
        target_weights={
            "000002.SZ": Decimal("0.4"),
            "000001.SZ": Decimal("0.2"),
            "000003.SZ": Decimal("0.4"),
        },
        net_equity_prices={
            "000001.SZ": Decimal("10"),
            "000002.SZ": Decimal("20"),
            "000003.SZ": Decimal("5"),
        },
        previous_members=frozenset({"000001.SZ", "000002.SZ"}),
        current_members=frozenset({"000001.SZ", "000002.SZ", "000003.SZ"}),
    )
    rebalancer = WeightTargetRebalancer({EXECUTION_DAY: period}, lot_size=100)
    account = _account(240_000, {"000001.SZ": 6000, "000002.SZ": 4000})
    orders = rebalancer.orders_for(EXECUTION_DAY, account)
    # equity = 100,000 cash + 60,000 + 80,000 = 240,000
    # 000001.SZ: weight 0.25 -> target 0.2 (4,800 shares) is a 5pp swing,
    # outside the band -> sell 1,200
    # 000002.SZ: weight 1/3 -> target 0.4 (4,800 shares) -> buy 800
    # 000003.SZ: entry -> buy floor(0.4 * 240,000 / 5) = 19,200 shares
    assert [order.side for order in orders] == [SELL, BUY, BUY]
    plan = [(order.side, order.symbol, order.quantity) for order in orders]
    assert plan == [
        (SELL, "000001.SZ", 1200),
        (BUY, "000002.SZ", 800),
        (BUY, "000003.SZ", 19200),
    ]


def test_continuing_position_within_band_emits_no_order():
    period = WeightTargetPeriod(
        signal_date=SIGNAL_DATE,
        target_weights={
            "000001.SZ": Decimal("0.5"),
            "000002.SZ": Decimal("0.10"),
        },
        net_equity_prices={
            "000001.SZ": Decimal("10"),
            "000002.SZ": Decimal("20"),
        },
        previous_members=frozenset({"000001.SZ", "000002.SZ"}),
        current_members=frozenset({"000001.SZ", "000002.SZ"}),
    )
    rebalancer = WeightTargetRebalancer({EXECUTION_DAY: period}, lot_size=100)
    # initial 170,000 seeds 70,000 of holdings, leaving 100,000 cash;
    # signal-close equity = 100,000 + 50,000 + 20,000 = 170,000
    # 000001.SZ: weight 50,000/170,000 vs target 0.5 -> 20.6pp swing ->
    # outside the band -> buy floor(0.5 * 170,000 / 10) - 5,000 = 3,500
    # 000002.SZ: weight 20,000/170,000 = 11.8% vs target 10% -> 1.8pp swing,
    # strictly inside the 2pp band -> suppressed with a recorded decision
    account = _account(170_000, {"000001.SZ": 5000, "000002.SZ": 1000})
    orders = rebalancer.orders_for(EXECUTION_DAY, account)
    assert [(order.side, order.symbol, order.quantity) for order in orders] == [
        (BUY, "000001.SZ", 3500)
    ]
    decisions = rebalancer.decision_frame()
    suppressed = decisions[decisions["symbol"] == "000002.SZ"].iloc[0]
    assert suppressed.reason == REASON_WITHIN_BAND
    assert suppressed.order_quantity == 0
    assert bool(suppressed["is_continuing"])


def test_same_common_weights_allow_scenario_quantities_to_diverge():
    periods = _periods(_period())
    left = _account(1_000_000, {})
    right = _account(50_000, {})
    rebalancer_a = WeightTargetRebalancer(periods, lot_size=100)
    rebalancer_b = WeightTargetRebalancer(periods, lot_size=100)
    orders_a = rebalancer_a.orders_for(EXECUTION_DAY, left)
    orders_b = rebalancer_b.orders_for(EXECUTION_DAY, right)
    assert orders_a != orders_b
    # the left account (equity 1,000,000) buys 60,000 and 20,000 shares; the
    # right account (equity 50,000) buys 3,000 and 1,000 -- identical common
    # weights, scenario-local quantities
    assert [(o.side, o.symbol, o.quantity) for o in orders_a] == [
        (BUY, "000001.SZ", 60000),
        (BUY, "000002.SZ", 20000),
    ]
    assert [(o.side, o.symbol, o.quantity) for o in orders_b] == [
        (BUY, "000001.SZ", 3000),
        (BUY, "000002.SZ", 1000),
    ]


def test_exit_of_a_previous_member_reconciles_without_the_band():
    period = WeightTargetPeriod(
        signal_date=SIGNAL_DATE,
        target_weights={"000002.SZ": Decimal("1.0")},
        net_equity_prices={
            "000001.SZ": Decimal("10"),
            "000002.SZ": Decimal("20"),
        },
        previous_members=frozenset({"000001.SZ", "000002.SZ"}),
        current_members=frozenset({"000002.SZ"}),
    )
    rebalancer = WeightTargetRebalancer({EXECUTION_DAY: period}, lot_size=100)
    account = _account(20_000, {"000001.SZ": 1000})
    orders = rebalancer.orders_for(EXECUTION_DAY, account)
    # equity = 10,000 cash + 10,000 held = 20,000; 000001.SZ exits with a
    # full sell (it left the current member set), 000002.SZ enters with
    # floor(20,000 / 20) = 1,000 shares
    assert [(o.side, o.symbol, o.quantity) for o in orders] == [
        (SELL, "000001.SZ", 1000),
        (BUY, "000002.SZ", 1000),
    ]
    decisions = rebalancer.decision_frame()
    exited = decisions[decisions["symbol"] == "000001.SZ"].iloc[0]
    assert not exited.is_continuing
    assert exited.target_weight == Decimal("0")


def test_t_plus_one_lots_wait_for_a_later_rebalance():
    period = _period()
    rebalancer = WeightTargetRebalancer({EXECUTION_DAY: period}, lot_size=100)
    account = Account(Decimal(100_000), calendar=_calendar())
    account.apply_fill(
        Fill(
            fill_id="today", order_id="today-order",
            trade_date=EXECUTION_DAY, side=BUY, symbol="000001.SZ",
            quantity=10_000, price=Decimal("10"),
            commission=Decimal("0"), stamp_tax=Decimal("0"),
        )
    )
    orders = rebalancer.orders_for(EXECUTION_DAY, account)
    # equity = 100,000: the 000001.SZ plan sells down to 6,000 shares but the
    # whole position is T+1 locked on the execution day, so no sellable lot
    # exists and only the 000002.SZ buy (2,000 shares) submits
    assert [(order.side, order.symbol, order.quantity) for order in orders] == [
        (BUY, "000002.SZ", 2000)
    ]
    decisions = rebalancer.decision_frame()
    held = decisions[decisions["symbol"] == "000001.SZ"].iloc[0]
    assert held.order_quantity == 0
    assert held.current_quantity == 10_000
    assert held.target_quantity == 6_000


# ---------------------------------------------------------------------------
# Decision audit
# ---------------------------------------------------------------------------


def test_decision_frame_carries_the_exact_ordered_columns():
    rebalancer = WeightTargetRebalancer(_periods(_period()), lot_size=100)
    assert rebalancer.decision_frame().empty
    rebalancer.orders_for(EXECUTION_DAY, _account(1_000_000, {}))
    frame = rebalancer.decision_frame()
    assert list(frame.columns) == list(REBALANCE_DECISION_COLUMNS)
    assert (frame["signal_date"] == SIGNAL_DATE).all()
    assert (frame["execution_day"] == EXECUTION_DAY).all()
    assert frame["symbol"].tolist() == ["000001.SZ", "000002.SZ"]
    assert (frame.loc[frame["order_quantity"] > 0, "order_side"] != "").all()


def test_orders_are_deterministic_across_rebalancer_instances():
    periods = _periods(_period())
    account = _account(92_000, {"000001.SZ": 800})
    first = WeightTargetRebalancer(dict(periods), lot_size=100)
    second = WeightTargetRebalancer(dict(periods), lot_size=100)
    left = first.orders_for(EXECUTION_DAY, account)
    right = second.orders_for(EXECUTION_DAY, account)
    assert [(o.side, o.symbol, o.quantity) for o in left] == [
        (o.side, o.symbol, o.quantity) for o in right
    ]
    assert [o.order_id for o in left] == [o.order_id for o in right]


def test_unknown_price_for_a_held_symbol_is_an_error():
    period = WeightTargetPeriod(
        signal_date=SIGNAL_DATE,
        target_weights={"000001.SZ": Decimal("1.0")},
        net_equity_prices={"000001.SZ": Decimal("10")},
        previous_members=frozenset(),
        current_members=frozenset({"000001.SZ"}),
    )
    rebalancer = WeightTargetRebalancer({EXECUTION_DAY: period}, lot_size=100)
    account = _account(2_000, {"000002.SZ": 100})
    with pytest.raises(ValueError, match="price"):
        rebalancer.orders_for(EXECUTION_DAY, account)
