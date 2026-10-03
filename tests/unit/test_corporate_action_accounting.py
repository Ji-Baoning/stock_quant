"""Corporate-action bookkeeping: ex-date cash and bonus/capitalization (Task 10).

``apply_corporate_action`` books an *implemented, supported* corporate action
onto an account that holds the name on the action's ``ex_date``: on the ex-date
before the open it credits the pre-tax cash dividend using the holding
quantity on the ``record_date`` (lots whose ``buy_date <= record_date``) and
increases the share count by the bonus/capitalization ratios.  Applying the
same ``action_id`` twice is a no-op (one ledger entry).  Priced rights issues
book at full participation (ADR-025), while unpriced rights issues, mergers,
conversions, incomplete or cross-source-conflicted actions touching a held
name raise :class:`UnsupportedCorporateAction`.
"""

from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from itertools import count

import pytest

from stock_quant.backtest.account import Account, CashShortfallError
from stock_quant.backtest.corporate_actions import (
    UnsupportedCorporateAction,
    action_id_of,
    apply_corporate_action,
)
from stock_quant.backtest.engine import ACTION_LEDGER_COLUMNS, BacktestEngine
from stock_quant.backtest.models import BUY, CorporateActionLedgerEntry, Fill
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


def test_a_priced_rights_issue_without_a_usable_price_raises_unsupported(
    account_with_record_date_holding,
):
    action = implemented_action(
        rights_ratio=0.3,
        cash_per_share=0.0,
        bonus_ratio=0.0,
    )
    # rights_issue_price 保持 None：r>0 且无有效价 → fail closed（ADR-025）
    with pytest.raises(UnsupportedCorporateAction, match="without a usable"):
        apply_corporate_action(account_with_record_date_holding, action)
    assert account_with_record_date_holding.action_ledger == ()


def test_zero_subscription_price_with_ratio_raises_unsupported(
    account_with_record_date_holding,
):
    action = implemented_action(rights_ratio=0.3, cash_per_share=0.0, bonus_ratio=0.0)
    action["rights_issue_price"] = 0.0
    with pytest.raises(UnsupportedCorporateAction, match="without a usable"):
        apply_corporate_action(account_with_record_date_holding, action)


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


# --------------------------------------------------------------------------- #
# Rights-issue subscription booking (ADR-025)
# --------------------------------------------------------------------------- #


def _rights_action(ratio: float, price: float) -> dict:
    action = implemented_action(rights_ratio=ratio, cash_per_share=0.0, bonus_ratio=0.0)
    action["rights_issue_price"] = price
    return action


def test_full_rights_subscription_conserves_equity_at_terp(
    account_with_record_date_holding,
):
    # 合成价格（§8.1.8）：P=9.10、r=0.3、s=13.00 → TERP=10.00 精确有限小数。
    p_prev, price = Decimal("9.10"), Decimal("13.00")
    account = account_with_record_date_holding  # 100 股、现金 10000.00
    apply_corporate_action(account, _rights_action(0.3, 13.0))
    entry = account.action_ledger[-1]
    assert entry.rights_entitlement_shares == 30
    assert entry.rights_subscribed_shares == 30
    assert entry.cash_paid == Decimal("390.00")
    assert entry.shares_added == 30
    terp = (p_prev + Decimal("0.3") * price) / (Decimal("1.3"))
    assert terp == Decimal("10.00")
    pre = Decimal("10000.00") + Decimal(100) * p_prev
    post = account.cash + Decimal(account.position_quantity("600000.SH")) * terp
    assert post == pre


def test_partial_subscription_bounded_by_available_cash():
    account = _account("200", lots=[("600000.SH", 100, date(2020, 1, 2))])
    apply_corporate_action(account, _rights_action(0.3, 13.0))
    entry = account.action_ledger[-1]
    assert entry.rights_entitlement_shares == 30
    assert entry.rights_subscribed_shares == 15  # floor(200/13)
    assert entry.cash_paid == Decimal("195.00")
    assert account.cash == Decimal("5.00")  # 再多一股就超支
    assert account.position_quantity("600000.SH") == 115


def test_zero_subscription_still_records_the_entry():
    account = _account("0", lots=[("600000.SH", 100, date(2020, 1, 2))])
    apply_corporate_action(account, _rights_action(0.3, 13.0))
    entry = account.action_ledger[-1]
    assert entry.rights_entitlement_shares == 30
    assert entry.rights_subscribed_shares == 0
    assert entry.cash_paid == Decimal("0")
    assert account.cash == Decimal("0")
    assert account.position_quantity("600000.SH") == 100


def test_subscription_shares_follow_the_t_plus_one_rule():
    account = _account("10000", lots=[("600000.SH", 100, date(2020, 1, 2))])
    apply_corporate_action(account, _rights_action(0.3, 13.0))
    # ex_date=2020-01-07；_OPEN_DAYS 的次一开放日是 2020-01-08。
    open_entry = account.position_ledger[-1]
    assert open_entry.kind == "OPEN"
    assert open_entry.quantity == 30
    assert open_entry.available_date == date(2020, 1, 8)


def test_same_event_dividend_credits_before_subscription_budget():
    # 分红先入账、后算预算：现金分红提高认购能力（§2 共现次序）。
    account = _account("300", lots=[("600000.SH", 100, date(2020, 1, 2))])
    action = _rights_action(0.3, 13.0)
    action["cash_dividend_per_share"] = 1.0  # 100 股 → 分红 100 → 预算 400
    apply_corporate_action(account, action)
    entry = account.action_ledger[-1]
    assert entry.cash_credited == Decimal("100.00")
    assert entry.rights_subscribed_shares == 30  # floor(400/13)=30=应配 → 全额
    assert account.cash == Decimal("10.00")


def test_idempotent_reapplication_of_a_priced_rights_issue():
    account = _account("10000", lots=[("600000.SH", 100, date(2020, 1, 2))])
    action = _rights_action(0.3, 13.0)
    first = apply_corporate_action(account, action)
    cash_after_first = account.cash
    assert first is not None
    assert apply_corporate_action(account, action) is None
    assert account.cash == cash_after_first
    assert len(account.action_ledger) == 1


# --------------------------------------------------------------------------- #
# Rights-issue debit primitive (ADR-025)
# --------------------------------------------------------------------------- #


def test_debit_cash_appends_a_debit_ledger_entry(account_with_record_date_holding):
    account = account_with_record_date_holding
    account.debit_cash(Decimal("100.00"), note="rights 600000.SH")
    assert account.cash == Decimal("9900.00")
    entry = account.cash_ledger[-1]
    assert entry.kind == "debit"
    assert entry.amount == Decimal("100.00")
    assert entry.balance == Decimal("9900.00")


def test_debit_cash_refuses_non_positive_amount(account_with_record_date_holding):
    with pytest.raises(ValueError):
        account_with_record_date_holding.debit_cash(0)
    with pytest.raises(ValueError):
        account_with_record_date_holding.debit_cash(Decimal("-1.00"))


def test_debit_cash_refuses_overdraft_all_or_nothing(account_with_record_date_holding):
    account = account_with_record_date_holding
    with pytest.raises(CashShortfallError):
        account.debit_cash(Decimal("10000.01"))
    assert account.cash == Decimal("10000.00")  # 全或无：失败不改变余额
    assert all(entry.kind != "debit" for entry in account.cash_ledger)


# --------------------------------------------------------------------------- #
# Ledger entry atomic fields and engine column contract (Task 2)
# --------------------------------------------------------------------------- #


def test_ledger_entry_new_fields_default_to_zero():
    entry = CorporateActionLedgerEntry(
        seq=0,
        action_id="600000.SH#2020-01-07",
        symbol="600000.SH",
        ex_date=date(2020, 1, 7),
        record_date=date(2020, 1, 6),
        cash_credited=Decimal("10.00"),
        shares_added=0,
    )
    assert entry.cash_paid == Decimal("0")
    assert entry.rights_entitlement_shares == 0
    assert entry.rights_subscribed_shares == 0


def test_ledger_entry_rejects_subscribed_above_entitlement():
    with pytest.raises(ValueError):
        CorporateActionLedgerEntry(
            seq=0,
            action_id="600000.SH#2020-01-07",
            symbol="600000.SH",
            ex_date=date(2020, 1, 7),
            record_date=date(2020, 1, 6),
            cash_credited=Decimal("0"),
            shares_added=30,
            cash_paid=Decimal("390.00"),
            rights_entitlement_shares=30,
            rights_subscribed_shares=31,
        )


def test_action_ledger_frame_carries_new_columns_even_when_empty():
    frame = BacktestEngine._action_ledger_frame([])
    assert list(frame.columns) == list(ACTION_LEDGER_COLUMNS)
    for column in (
        "cash_paid",
        "rights_entitlement_shares",
        "rights_subscribed_shares",
    ):
        assert column in frame.columns
