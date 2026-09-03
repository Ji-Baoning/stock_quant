"""Close-of-day valuation: unadjusted closes and carried stale closes (Task 10).

``value_account`` prices every holding at its unadjusted close on the
valuation date.  A name with no bar -- or no *valid* close -- on that date is
valued at its last valid unadjusted close for VALUATION ONLY: the valuation is
flagged stale with the number of sessions the price has been carried and the
stale share of assets, and a carried price is never used to fill an order.
"""

from datetime import date
from decimal import Decimal
from itertools import count

import pytest

from stock_quant.backtest.account import Account
from stock_quant.backtest.models import BUY, Fill
from stock_quant.backtest.valuation import (
    HoldingValuation,
    value_account,
)
from stock_quant.data_model.calendar import TradingCalendar

# --------------------------------------------------------------------------- #
# Deterministic offline fixtures
# --------------------------------------------------------------------------- #

_OPEN_DAYS = (
    date(2020, 1, 2),
    date(2020, 1, 3),
    date(2020, 1, 6),
    date(2020, 1, 7),
    date(2020, 1, 8),
    date(2020, 1, 9),
    date(2020, 1, 10),
)

_fill_seq = count(1)


def _buy(symbol: str, quantity: int, trade_date: date, *, price: str = "10.00") -> Fill:
    n = next(_fill_seq)
    return Fill(
        fill_id=f"F{n}",
        order_id=f"o{n}",
        trade_date=trade_date,
        side=BUY,
        symbol=symbol,
        quantity=quantity,
        price=Decimal(price),
        commission=Decimal("5.00"),
        stamp_tax=Decimal("0.00"),
    )


def _account_with_100_shares(symbol: str = "600000.SH") -> Account:
    calendar = TradingCalendar.from_open_days(_OPEN_DAYS)
    account = Account(Decimal("11005.00"), calendar=calendar)
    account.apply_fill(_buy(symbol, 100, date(2020, 1, 2)))
    return account


@pytest.fixture
def account_with_100_shares() -> Account:
    return _account_with_100_shares()


def _multi_symbol_account() -> Account:
    calendar = TradingCalendar.from_open_days(_OPEN_DAYS)
    account = Account(Decimal("23020.00"), calendar=calendar)
    account.apply_fill(_buy("600001.SH", 100, date(2020, 1, 2)))
    account.apply_fill(_buy("600002.SH", 100, date(2020, 1, 2), price="20.00"))
    return account
# --------------------------------------------------------------------------- #
# Fresh closes and carried stale closes
# --------------------------------------------------------------------------- #


def test_fresh_close_values_holdings_at_the_unadjusted_close(account_with_100_shares):
    value = value_account(
        account_with_100_shares,
        closes={"600000.SH": Decimal("10.00")},
        carried={},
    )
    (holding,) = value.holdings
    assert isinstance(holding, HoldingValuation)
    assert holding.market_value == Decimal("1000.00")
    assert holding.stale_days == 0
    assert value.market_value == Decimal("1000.00")
    assert not value.stale_valuation


def test_suspended_holding_uses_last_close_only_for_valuation(account_with_100_shares):
    value = value_account(
        account_with_100_shares,
        closes={},
        carried={"600000.SH": (Decimal("10.00"), 3)},
    )
    assert value.market_value == 1000
    assert value.stale_valuation and value.stale_days == 3
    (holding,) = value.holdings
    assert holding.price == Decimal("10.00")
    assert holding.is_stale


def test_equity_adds_cash_to_market_value(account_with_100_shares):
    value = value_account(
        account_with_100_shares,
        closes={"600000.SH": Decimal("12.00")},
        carried={},
    )
    assert value.market_value == Decimal("1200.00")
    # cash == 10000 after the 1005.00 buy from 11005.00
    assert value.cash == Decimal("10000.00")
    assert value.total_equity == Decimal("11200.00")


def test_multiple_holdings_aggregate_and_report_the_stale_share():
    account = _multi_symbol_account()
    value = value_account(
        account,
        closes={"600001.SH": Decimal("10.00")},
        carried={"600002.SH": (Decimal("20.00"), 5)},
    )
    assert value.market_value == Decimal("3000.00")
    assert value.stale_market_value == Decimal("2000.00")
    assert value.stale_days == 5
    assert value.stale_valuation
    # Holdings are ordered by symbol for deterministic output.
    assert [h.symbol for h in value.holdings] == ["600001.SH", "600002.SH"]
    # Equity is exactly account cash plus the mark-to-market of all holdings.
    assert value.cash == account.cash
    assert value.total_equity == account.cash + Decimal("3000.00")
    assert value.stale_ratio == pytest.approx(
        Decimal("2000.00") / value.total_equity
    )


def test_held_name_with_no_price_raises(account_with_100_shares):
    with pytest.raises(ValueError, match="600000.SH"):
        value_account(account_with_100_shares, closes={}, carried={})
