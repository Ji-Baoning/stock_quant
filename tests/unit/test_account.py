"""T+1 lot accounting and append-only ledgers (Task 9).

``Account.apply_fill`` is the single state-transition of the cash ledger, so
replaying the identical ordered fill log onto a fresh ``Account`` reproduces
byte-identical state.  A buy opens a ``PositionLot`` whose ``available_date``
is the next trading day; the account decides sellability purely by
``available_date <= fill.trade_date``, so a same-day buy is never sellable.
Duplicate ``fill_id`` and cash overdrafts are guarded with raises.
"""

from datetime import date
from decimal import Decimal
from itertools import count

import pytest

from stock_quant.backtest.account import (
    Account,
    CashShortfallError,
    DuplicateFillError,
    InsufficientSellableQuantity,
)
from stock_quant.backtest.models import BUY, SELL, Fill
from stock_quant.data_model.calendar import TradingCalendar

# --------------------------------------------------------------------------- #
# Deterministic offline fixtures
# --------------------------------------------------------------------------- #

_OPEN_DAYS = (
    date(2020, 1, 2),  # D0
    date(2020, 1, 3),  # D1 (next trading day after D0)
    date(2020, 1, 6),  # D2 (next trading day after D1)
    date(2020, 1, 7),
    date(2020, 1, 8),
)

_fill_seq = count(1)


def fill(
    side: str,
    symbol: str,
    quantity: int,
    trade_date: date,
    *,
    price: str = "10.00",
    commission: str = "5.00",
    stamp: str = "0.00",
    fill_id: str | None = None,
) -> Fill:
    n = next(_fill_seq)
    return Fill(
        fill_id=fill_id or f"F{n}",
        order_id=f"o{n}",
        trade_date=trade_date,
        side=side,
        symbol=symbol,
        quantity=quantity,
        price=Decimal(price),
        commission=Decimal(commission),
        stamp_tax=Decimal(stamp),
    )


@pytest.fixture
def calendar() -> TradingCalendar:
    return TradingCalendar.from_open_days(_OPEN_DAYS)


@pytest.fixture
def account(calendar: TradingCalendar) -> Account:
    return Account(Decimal("100000"), calendar=calendar)


def _buy(
    account: Account,
    symbol: str,
    quantity: int,
    trade_date: date,
    *,
    price: str = "10.00",
) -> None:
    account.apply_fill(fill(BUY, symbol, quantity, trade_date, price=price))


# --------------------------------------------------------------------------- #
# T+1 sellability
# --------------------------------------------------------------------------- #


def test_same_day_lot_is_not_sellable(account: Account):
    account.apply_fill(fill(BUY, "600000.SH", 100, date(2020, 1, 2)))
    with pytest.raises(InsufficientSellableQuantity):
        account.apply_fill(fill(SELL, "600000.SH", 100, date(2020, 1, 2)))


def test_lot_becomes_sellable_on_the_next_trading_day(account: Account):
    account.apply_fill(fill(BUY, "600000.SH", 100, date(2020, 1, 2)))
    lot = account.lots[0]
    assert lot.available_date == date(2020, 1, 3)
    assert account.sellable_quantity("600000.SH", date(2020, 1, 3)) == 100
    account.apply_fill(fill(SELL, "600000.SH", 100, date(2020, 1, 3)))
    assert account.position_quantity("600000.SH") == 0


def test_t_plus_one_steps_over_the_weekend(account: Account):
    # Friday 2020-01-03 buy is sellable only from Monday 2020-01-06.
    account.apply_fill(fill(BUY, "600000.SH", 100, date(2020, 1, 3)))
    with pytest.raises(InsufficientSellableQuantity):
        account.apply_fill(fill(SELL, "600000.SH", 100, date(2020, 1, 3)))
    account.apply_fill(fill(SELL, "600000.SH", 100, date(2020, 1, 6)))
    assert account.position_quantity("600000.SH") == 0


def test_buy_on_the_last_calendar_day_has_no_available_date(account: Account):
    account.apply_fill(fill(BUY, "600000.SH", 100, date(2020, 1, 8)))
    assert account.lots[0].available_date is None
    assert account.sellable_quantity("600000.SH", date(2020, 1, 8)) == 0


def test_sell_requires_sellable_lots_even_when_total_position_is_larger(
    account: Account,
):
    account.apply_fill(fill(BUY, "600000.SH", 100, date(2020, 1, 2)))
    account.apply_fill(fill(BUY, "600000.SH", 100, date(2020, 1, 3)))
    # Both lots are held, but only the D0 lot is sellable on D1.
    assert account.position_quantity("600000.SH") == 200
    assert account.sellable_quantity("600000.SH", date(2020, 1, 3)) == 100
    with pytest.raises(InsufficientSellableQuantity):
        account.apply_fill(fill(SELL, "600000.SH", 200, date(2020, 1, 3)))


def test_sells_consume_lots_fifo(account: Account):
    _buy(account, "600000.SH", 100, date(2020, 1, 2), price="10.00")  # lot A
    _buy(account, "600000.SH", 100, date(2020, 1, 2), price="20.00")  # lot B
    account.apply_fill(fill(SELL, "600000.SH", 100, date(2020, 1, 3), price="11.00"))
    remaining = account.lots[0]
    assert remaining.buy_date == date(2020, 1, 2)
    assert remaining.cost_basis == Decimal("2005.00")  # FIFO kept the newer lot


def test_partial_sell_splits_a_lot_and_preserves_cost_basis(account: Account):
    account.apply_fill(fill(BUY, "600000.SH", 300, date(2020, 1, 2), price="10.00"))
    assert account.lots[0].cost_basis == Decimal("3005.00")
    account.apply_fill(fill(SELL, "600000.SH", 100, date(2020, 1, 3), price="11.00"))
    leftover = account.lots[0]
    assert leftover.quantity == 200
    assert leftover.available_date == date(2020, 1, 3)
    # 100/300 of the acquisition cost (including commission) was consumed.
    assert leftover.cost_basis == pytest.approx(
        Decimal("2003.333333333333333333333333")
    )


# --------------------------------------------------------------------------- #
# Guards: duplicate fill_id and no overdraft
# --------------------------------------------------------------------------- #


def test_duplicate_fill_id_is_rejected_and_leaves_state_unchanged(
    account: Account,
):
    first = fill(BUY, "600000.SH", 100, date(2020, 1, 2), fill_id="dup")
    account.apply_fill(first)
    with pytest.raises(DuplicateFillError):
        account.apply_fill(first)
    assert len(account.fill_ledger) == 1
    assert account.cash == Decimal("98995.00")


def test_cash_shortfall_is_rejected_atomically(calendar: TradingCalendar):
    account = Account(Decimal("1000"), calendar=calendar)
    with pytest.raises(CashShortfallError):
        account.apply_fill(
            fill(BUY, "600000.SH", 200, date(2020, 1, 2), price="10.00")
        )
    assert account.cash == Decimal("1000.00")
    assert account.fill_ledger == ()


# --------------------------------------------------------------------------- #
# Append-only ledgers
# --------------------------------------------------------------------------- #


def test_ledgers_are_append_only_and_reconcile_to_state(account: Account):
    account.apply_fill(fill(BUY, "600000.SH", 200, date(2020, 1, 2)))
    account.apply_fill(fill(SELL, "600000.SH", 200, date(2020, 1, 3)))
    assert len(account.cash_ledger) == 3  # initial + buy + sell
    assert account.cash_ledger[0].balance == Decimal("100000.00")
    assert account.cash_ledger[-1].balance == account.cash
    assert len(account.fill_ledger) == 2
    assert len(account.position_ledger) == 2  # one OPEN and one CONSUME
    opened = account.position_ledger[0]
    consumed = account.position_ledger[1]
    assert (opened.kind, opened.quantity) == ("OPEN", 200)
    assert (consumed.kind, consumed.quantity) == ("CONSUME", -200)
    assert account.position_quantity("600000.SH") == 0
    assert account.lots == ()


def test_record_order_appends_order_events(account: Account):
    from stock_quant.backtest.models import Order

    account.record_order(
        Order(order_id="o1", side=BUY, symbol="600000.SH", quantity=100)
    )
    assert len(account.order_ledger) == 1
    assert account.order_ledger[0].order.order_id == "o1"


# --------------------------------------------------------------------------- #
# Deterministic replay
# --------------------------------------------------------------------------- #


def _ordered_fill_log():
    return [
        fill(BUY, "600000.SH", 100, date(2020, 1, 2), price="10.00"),
        fill(BUY, "600000.SH", 100, date(2020, 1, 2), price="10.00"),
        fill(BUY, "600001.SH", 100, date(2020, 1, 2), price="20.00"),
        fill(SELL, "600000.SH", 100, date(2020, 1, 3), price="11.00"),
        fill(BUY, "600000.SH", 100, date(2020, 1, 3), price="12.00"),
        fill(SELL, "600000.SH", 200, date(2020, 1, 6), price="13.00"),
    ]


def test_applying_the_identical_fill_log_reproduces_byte_identical_state(
    calendar: TradingCalendar,
):
    first = Account(Decimal("100000"), calendar=calendar)
    second = Account(Decimal("100000"), calendar=calendar)
    for f in _ordered_fill_log():
        first.apply_fill(f)
        second.apply_fill(f)
    assert first.state() == second.state()
    assert repr(first.state()) == repr(second.state())
    # Sanity: the replayed log left the account exactly as expected.
    assert first.cash == Decimal("98470.00")
    assert first.position_quantity("600000.SH") == 0
    assert first.position_quantity("600001.SH") == 100
