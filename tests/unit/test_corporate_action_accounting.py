"""Corporate-action bookkeeping: ex-date cash and bonus/capitalization (Task 10).

``apply_corporate_action`` books an *implemented, supported* corporate action
onto an account that holds the name on the action's ``ex_date``: on the ex-date
before the open it credits the pre-tax cash dividend using the holding
quantity on the ``record_date`` (lots whose ``buy_date <= record_date``) and
increases the share count by the bonus/capitalization ratios.  Applying the
same ``action_id`` twice is a no-op (one ledger entry), while rights issues,
mergers, conversions, incomplete or cross-source-conflicted actions touching a
held name raise :class:`UnsupportedCorporateAction`.
"""

from datetime import date
from decimal import Decimal
from itertools import count

import pytest

from stock_quant.backtest.account import Account
from stock_quant.backtest.corporate_actions import (
    UnsupportedCorporateAction,
    action_id_of,
    apply_corporate_action,
)
from stock_quant.backtest.models import BUY, Fill
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
    date(2020, 1, 13),
    date(2020, 1, 14),
)

_fill_seq = count(1)


def _buy_fill(
    symbol: str,
    quantity: int,
    trade_date: date,
    *,
    price: str = "10.00",
    commission: str = "5.00",
) -> Fill:
    n = next(_fill_seq)
    return Fill(
        fill_id=f"F{n}",
        order_id=f"o{n}",
        trade_date=trade_date,
        side=BUY,
        symbol=symbol,
        quantity=quantity,
        price=Decimal(price),
        commission=Decimal(commission),
        stamp_tax=Decimal("0.00"),
    )


def _account(
    initial_cash: str,
    *,
    lots: list[tuple[str, int, date]] | None = None,
) -> Account:
    """An account whose buy lots leave ``cash`` at ``initial_cash``.

    Each ``(symbol, quantity, buy_date)`` lot costs ``10.00 * quantity + 5.00``
    commission, so the account is funded with that cost added to the desired
    ending cash before the buys are applied.
    """
    calendar = TradingCalendar.from_open_days(_OPEN_DAYS)
    total_cost = sum(1000 + 5 for _ in (lots or []))
    account = Account(Decimal(initial_cash) + Decimal(total_cost), calendar=calendar)
    for symbol, quantity, buy_date in lots or []:
        account.apply_fill(_buy_fill(symbol, quantity, buy_date))
    return account


def implemented_action(
    *,
    symbol: str = "600000.SH",
    cash_per_share: float = 0.1,
    bonus_ratio: float = 0.2,
    capitalization_ratio: float = 0.0,
    rights_ratio: float = 0.0,
    record_date: date = date(2020, 1, 6),
    ex_date: date = date(2020, 1, 7),
) -> dict:
    """A canonical, implemented corporate-action row as the engine receives it."""
    return {
        "symbol": symbol,
        "announcement_date": date(2020, 1, 3),
        "record_date": record_date,
        "ex_date": ex_date,
        "cash_dividend_per_share": cash_per_share,
        "bonus_share_ratio": bonus_ratio,
        "capitalization_ratio": capitalization_ratio,
        "rights_issue_ratio": rights_ratio if rights_ratio else None,
        "rights_issue_price": None,
        "status": "implemented",
    }


@pytest.fixture
def account_with_record_date_holding() -> Account:
    """Holds 100 shares bought 2020-01-02 with ``cash == 10000``.

    The buy lot's ``buy_date`` precedes the fixture action's ``record_date``,
    so the whole 100-share holding qualifies for the cash dividend.
    """
    return _account("10000", lots=[("600000.SH", 100, date(2020, 1, 2))])


# --------------------------------------------------------------------------- #
# Ex-date cash and bonus are booked once
# --------------------------------------------------------------------------- #


def test_ex_date_cash_and_bonus_are_applied_once(account_with_record_date_holding):
    action = implemented_action(cash_per_share=0.1, bonus_ratio=0.2)
    apply_corporate_action(account_with_record_date_holding, action)
    apply_corporate_action(account_with_record_date_holding, action)
    assert account_with_record_date_holding.cash == pytest.approx(10000 + 10)
    assert account_with_record_date_holding.position_quantity("600000.SH") == 120
    assert len(account_with_record_date_holding.action_ledger) == 1


def test_action_ledger_records_the_single_booked_entry(
    account_with_record_date_holding,
):
    apply_corporate_action(
        account_with_record_date_holding,
        implemented_action(cash_per_share=0.5, bonus_ratio=0.2),
    )
    (entry,) = account_with_record_date_holding.action_ledger
    assert entry.cash_credited == Decimal("50.00")
    assert entry.shares_added == 20
    assert entry.symbol == "600000.SH"
    assert entry.record_date == date(2020, 1, 6)
    assert entry.ex_date == date(2020, 1, 7)
    assert entry.action_id == "600000.SH#2020-01-07"


def test_capitalization_only_increases_the_share_count(
    account_with_record_date_holding,
):
    action = implemented_action(
        cash_per_share=0.0, bonus_ratio=0.0, capitalization_ratio=0.5
    )
    apply_corporate_action(account_with_record_date_holding, action)
    assert account_with_record_date_holding.cash == Decimal("10000.00")
    assert account_with_record_date_holding.position_quantity("600000.SH") == 150
    (entry,) = account_with_record_date_holding.action_ledger
    assert entry.cash_credited == Decimal("0.00")
    assert entry.shares_added == 50


def test_cash_dividend_uses_the_record_date_holding_quantity():
    # Two lots: one bought before and one bought exactly on the record date;
    # a third lot is absent. 200 shares qualify for the 0.10/share dividend.
    account = _account(
        "100000",
        lots=[
            ("600000.SH", 100, date(2020, 1, 2)),
            ("600000.SH", 100, date(2020, 1, 6)),
        ],
    )
    apply_corporate_action(
        account,
        implemented_action(
            cash_per_share=0.1,
            bonus_ratio=0.0,
            record_date=date(2020, 1, 7),
            ex_date=date(2020, 1, 8),
        ),
    )
    assert account.cash == pytest.approx(100000 + 20)
    assert account.position_quantity("600000.SH") == 200


# --------------------------------------------------------------------------- #
# Unsupported / absent / conflicted actions
# --------------------------------------------------------------------------- #


def test_rights_issue_on_a_held_name_raises_unsupported(
    account_with_record_date_holding,
):
    action = implemented_action(
        rights_ratio=0.3,
        cash_per_share=0.0,
        bonus_ratio=0.0,
    )
    with pytest.raises(UnsupportedCorporateAction):
        apply_corporate_action(account_with_record_date_holding, action)
    assert account_with_record_date_holding.action_ledger == ()


def test_not_implemented_action_on_a_held_name_raises_unsupported(
    account_with_record_date_holding,
):
    action = implemented_action(cash_per_share=0.1, bonus_ratio=0.0)
    action["status"] = "not_implemented"
    with pytest.raises(UnsupportedCorporateAction):
        apply_corporate_action(account_with_record_date_holding, action)


def test_action_for_a_name_not_held_is_a_no_op(account_with_record_date_holding):
    account = _account("10000", lots=[("600001.SH", 100, date(2020, 1, 2))])
    before = account.cash
    result = apply_corporate_action(account, implemented_action(symbol="600000.SH"))
    assert result is None
    assert account.cash == before
    assert account.action_ledger == ()


def test_a_repeat_action_id_is_always_a_no_op_keeping_the_first_booking(
    account_with_record_date_holding,
):
    # The action id is (symbol, ex_date); the second application is refused
    # even though its row facts differ, because upstream normalization would
    # have quarantined a conflicting second source for the same key.
    apply_corporate_action(
        account_with_record_date_holding, implemented_action(cash_per_share=0.1)
    )
    assert (
        apply_corporate_action(
            account_with_record_date_holding, implemented_action(cash_per_share=0.2)
        )
        is None
    )
    (entry,) = account_with_record_date_holding.action_ledger
    assert entry.cash_credited == Decimal("10.00")


def test_action_id_is_deterministic():
    assert action_id_of("600000.SH", date(2020, 1, 7)) == "600000.SH#2020-01-07"
