"""Account-aware weekly rebalancer unit tests (pure hand-written oracles).

Every expected order is authored by hand against the U0/I1a contract: the
day's order is exactly ``frozen target - realized account holdings``, held
names absent from the target book still sell (stale-residual retry), sells are
T+1-clamped and lot-floored, buys are lot-floored and never cash-clamped,
sells precede buys, and symbols ascend within each side.
"""

from __future__ import annotations

from datetime import date, timedelta

from stock_quant.backtest.account import Account
from stock_quant.backtest.models import BUY, SELL, Order
from stock_quant.backtest.rebalancer import AccountAwareWeeklyRebalancer

_A = "600001.SH"
_B = "600002.SH"
_C = "600003.SH"

_D0 = date(2024, 1, 2)
_DAYS = [_D0 + timedelta(days=offset) for offset in range(10)]


def _calendar():
    from stock_quant.data_model.calendar import TradingCalendar

    return TradingCalendar.from_open_days(_DAYS)


def _account(lots: list[tuple[str, date, int]], cash: object = 1_000_000) -> Account:
    """An account pre-loaded with lots (zero-cost credits, T+1 stamped)."""
    account = Account(cash, calendar=_calendar())
    for index, (symbol, buy_date, quantity) in enumerate(lots):
        account.increase_position(
            symbol, quantity, buy_date=buy_date, fill_id=f"a{index}"
        )
    return account


def _rebalance(targets_by_day) -> AccountAwareWeeklyRebalancer:
    return AccountAwareWeeklyRebalancer(targets_by_day)


def _order(
    order_id: str, side: str, symbol: str, quantity: int,
    signal_date: date = _D0,
) -> Order:
    return Order(
        order_id=order_id,
        side=side,
        symbol=symbol,
        quantity=quantity,
        note=f"weekly_rebalance:{signal_date.isoformat()}",
    )


# A lot bought on D0 is T+1 available from D1 onward.
_D1 = _DAYS[1]
_D2 = _DAYS[2]


def test_exiting_name_absent_from_targets_still_produces_its_sell():
    """U0/I1a: last week held 1000, this week the target book is empty, the
    account actually holds 700 -> the sell is exactly the realized residual,
    not an idealized-book difference."""
    rebalancer = _rebalance({_D2: (_D0, {})})
    orders = rebalancer.orders_for(_D2, _account([(_A, _D0, 700)]))
    assert orders == (_order("r000001", SELL, _A, 700),)


def test_odd_lot_residual_sells_whole_lots_and_leaves_the_remainder():
    """CA odd residual: 150 held, target 0 -> sell 100, the 50-share
    remainder cannot be sold whole-lot and stays stale by design."""
    rebalancer = _rebalance({_D2: (_D0, {})})
    orders = rebalancer.orders_for(_D2, _account([(_A, _D0, 150)]))
    assert orders == (_order("r000001", SELL, _A, 100),)


def test_excess_above_a_nonzero_target_is_also_lot_floored():
    rebalancer = _rebalance({_D2: (_D0, {_A: 100})})
    orders = rebalancer.orders_for(_D2, _account([(_A, _D0, 250)]))
    assert orders == (_order("r000001", SELL, _A, 100),)


def test_t1_locked_excess_waits_and_retries_on_the_next_rebalance_day():
    """M0/I1a stale-retry: the W2 sell is impossible (the lot is T+1 locked),
    so W2 submits nothing; W3 -- target still 0, holdings still 700 -- submits
    SELL 700 again.  The retry is keyed on the account, not on any target
    change."""
    rebalancer = _rebalance(
        {
            _D1: (_D0, {}),  # W2: target 0
            _D2: (_D1, {}),  # W3: target still 0
        }
    )
    # Held since D1 -> not sellable on D1, sellable from D2.
    account = _account([(_A, _D1, 700)])
    assert rebalancer.orders_for(_D1, account) == ()
    assert rebalancer.orders_for(_D2, account) == (
        _order("r000001", SELL, _A, 700, signal_date=_D1),
    )


def test_sell_quantity_is_clamped_to_the_t1_sellable_part():
    """300 excess but only 100 T+1 available (the rest bought today) -> sell
    100 now; the locked 200 re-enters next rebalance day."""
    rebalancer = _rebalance({_D1: (_D0, {}), _D2: (_D1, {})})
    # 100 shares from D0 are sellable on D1; the 200 bought on D1 unlock on D2.
    account = _account([(_A, _D0, 100), (_A, _D1, 200)])
    assert rebalancer.orders_for(_D1, account) == (
        _order("r000001", SELL, _A, 100, signal_date=_D0),
    )
    assert rebalancer.orders_for(_D2, account) == (
        _order("r000002", SELL, _A, 300, signal_date=_D1),
    )


def test_shortfall_top_up_buys_whole_lots_of_the_difference():
    rebalancer = _rebalance({_D2: (_D0, {_A: 800})})
    orders = rebalancer.orders_for(_D2, _account([(_A, _D0, 500)]))
    assert orders == (_order("r000001", BUY, _A, 300),)


def test_buy_difference_floors_to_lots_without_a_cash_pre_clamp():
    """Target 250, held 0 -> submit the 200-share lot difference even with a
    zero cash balance; affordability is the executor's partial-fill job."""
    rebalancer = _rebalance({_D2: (_D0, {_A: 250})})
    orders = rebalancer.orders_for(_D2, _account([], cash=0))
    assert orders == (_order("r000001", BUY, _A, 200),)


def test_sub_lot_target_difference_produces_no_buy():
    rebalancer = _rebalance({_D2: (_D0, {_A: 150})})
    assert rebalancer.orders_for(_D2, _account([(_A, _D0, 100)])) == ()


def test_sells_precede_buys_and_symbols_ascend_within_each_side():
    rebalancer = _rebalance(
        {_D2: (_D0, {_A: 0, _B: 400, _C: 100})}
    )
    account = _account([(_A, _D0, 300), (_B, _D0, 100)])
    orders = rebalancer.orders_for(_D2, account)
    assert orders == (
        _order("r000001", SELL, _A, 300),
        _order("r000002", BUY, _B, 300),
        _order("r000003", BUY, _C, 100),
    )


def test_each_symbol_gets_at_most_one_side():
    rebalancer = _rebalance({_D2: (_D0, {_A: 300})})
    account = _account([(_A, _D0, 500)])
    orders = rebalancer.orders_for(_D2, account)
    assert orders == (_order("r000001", SELL, _A, 200),)
    assert all(order.symbol != _A or order.side == SELL for order in orders)


def test_non_rebalance_day_returns_no_orders():
    rebalancer = _rebalance({_D2: (_D0, {_A: 100})})
    assert rebalancer.orders_for(_DAYS[5], _account([(_A, _D0, 900)])) == ()


def test_held_names_at_target_produce_nothing_and_order_is_deterministic():
    targets = {_D2: (_D0, {_A: 700, _B: 100})}
    account = _account([(_A, _D0, 700)])
    first = _rebalance(targets).orders_for(_D2, account)
    second = _rebalance(targets).orders_for(_D2, account)
    assert first == second == (_order("r000001", BUY, _B, 100),)


def test_signal_date_attribution_is_frozen_per_execution_day():
    rebalancer = _rebalance({_D2: (_D1, {_A: 100})})
    assert rebalancer.signal_date_by_execution_day == {_D2: _D1}
