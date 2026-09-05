"""Pre-trade projection of ideal targets onto executable A-share orders."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from stock_quant.backtest.account import Account
from stock_quant.backtest.costs import CostModel
from stock_quant.backtest.models import (
    BUY,
    REASON_INSUFFICIENT_CASH,
    REASON_INSUFFICIENT_SELLABLE_QUANTITY,
    REASON_SUSPENDED_OR_UNKNOWN,
    SELL,
    Fill,
)
from stock_quant.backtest.rebalance import project_rebalance
from stock_quant.config import CostRate
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.trading_rules import (
    REASON_BUY_AT_UPPER_LIMIT,
    REASON_SELL_AT_LOWER_LIMIT,
    TradingRuleBook,
)

D0 = date(2020, 1, 2)
TRADE_DATE = date(2020, 1, 3)

_PRICE_RULES_YAML = """\
price_tick: "0.01"
price_limits:
  - boards: [sh_main, sz_main]
    status: NORMAL
    effective_from: 1996-12-16
    rate: "0.10"
no_limit_first_sessions: []
"""


@pytest.fixture
def calendar() -> TradingCalendar:
    return TradingCalendar.from_open_days(
        (D0, TRADE_DATE, date(2020, 1, 6))
    )


@pytest.fixture
def cost_model() -> CostModel:
    return CostModel(
        CostRate(
            effective_from=date(2020, 1, 1),
            commission_rate=0.0003,
            minimum_commission=5.0,
            stamp_tax_sell_rate=0.0005,
            slippage_rate=0.001,
        )
    )


@pytest.fixture
def rule_book(tmp_path) -> TradingRuleBook:
    path = tmp_path / "trading_rules.yml"
    path.write_text(_PRICE_RULES_YAML, encoding="utf-8")
    return TradingRuleBook.from_yaml(path)


def _bars(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _bar(symbol: str, open_price: float, pre_close: float) -> dict:
    return {
        "symbol": symbol,
        "open": open_price,
        "pre_close": pre_close,
        "quality_severity": "INFO",
    }


def _buy_fill(
    symbol: str,
    quantity: int,
    trade_date: date,
    *,
    fill_id: str,
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=f"order-{fill_id}",
        trade_date=trade_date,
        side=BUY,
        symbol=symbol,
        quantity=quantity,
        price=Decimal("10.00"),
        commission=Decimal("0.00"),
        stamp_tax=Decimal("0.00"),
    )


def test_sell_is_truncated_to_the_account_sellable_quantity(
    calendar: TradingCalendar,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
):
    account = Account(Decimal("10000"), calendar=calendar)
    account.apply_fill(_buy_fill("600000.SH", 100, D0, fill_id="old"))
    account.apply_fill(
        _buy_fill("600000.SH", 100, TRADE_DATE, fill_id="same-day")
    )
    state_before = account.state()

    projection = project_rebalance(
        {"600000.SH": 0},
        account,
        _bars(_bar("600000.SH", 10.0, 10.0)),
        TRADE_DATE,
        cost_model,
        rule_book,
    )

    assert [(order.symbol, order.quantity) for order in projection.sells] == [
        ("600000.SH", 100)
    ]
    assert projection.buys == ()
    assert dict(projection.executable_target_quantities) == {"600000.SH": 100}
    assert [
        (
            item.symbol,
            item.side,
            item.requested_quantity,
            item.executable_quantity,
            item.reason,
        )
        for item in projection.adjustments
    ] == [
        (
            "600000.SH",
            SELL,
            200,
            100,
            REASON_INSUFFICIENT_SELLABLE_QUANTITY,
        )
    ]
    assert account.state() == state_before


def test_buy_is_reduced_to_the_largest_fee_inclusive_whole_lot(
    calendar: TradingCalendar,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
):
    account = Account(Decimal("1500"), calendar=calendar)

    projection = project_rebalance(
        {"600000.SH": 200},
        account,
        _bars(_bar("600000.SH", 10.0, 10.0)),
        TRADE_DATE,
        cost_model,
        rule_book,
    )

    # One lot costs 100 * 10.01 + 5 commission = 1006.00; two cost 2007.00.
    assert [(order.symbol, order.quantity) for order in projection.buys] == [
        ("600000.SH", 100)
    ]
    assert dict(projection.executable_target_quantities) == {"600000.SH": 100}
    adjustment = projection.adjustments[0]
    assert (
        adjustment.requested_quantity,
        adjustment.executable_quantity,
        adjustment.reason,
    ) == (200, 100, REASON_INSUFFICIENT_CASH)


def test_missing_and_price_locked_bars_are_filtered_before_submission(
    calendar: TradingCalendar,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
):
    account = Account(Decimal("10000"), calendar=calendar)
    account.apply_fill(_buy_fill("600001.SH", 100, D0, fill_id="held"))

    projection = project_rebalance(
        {
            "600000.SH": 100,  # opens at the upper limit
            "600001.SH": 0,  # opens at the lower limit
            "600009.SH": 100,  # no execution-date row: suspended/unknown
        },
        account,
        _bars(
            _bar("600000.SH", 11.0, 10.0),
            _bar("600001.SH", 9.0, 10.0),
        ),
        TRADE_DATE,
        cost_model,
        rule_book,
    )

    assert projection.sells == ()
    assert projection.buys == ()
    assert dict(projection.executable_target_quantities) == {
        "600000.SH": 0,
        "600001.SH": 100,
        "600009.SH": 0,
    }
    assert {(item.symbol, item.reason) for item in projection.adjustments} == {
        ("600000.SH", REASON_BUY_AT_UPPER_LIMIT),
        ("600001.SH", REASON_SELL_AT_LOWER_LIMIT),
        ("600009.SH", REASON_SUSPENDED_OR_UNKNOWN),
    }


def test_explicitly_suspended_bar_is_audited_as_suspended_or_unknown(
    calendar: TradingCalendar,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
):
    """A status flag must not be misreported as missing rule coverage."""
    account = Account(Decimal("10000"), calendar=calendar)
    suspended = _bar("600000.SH", 10.0, 10.0)
    suspended["status"] = "SUSPENDED"

    projection = project_rebalance(
        {"600000.SH": 100},
        account,
        _bars(suspended),
        TRADE_DATE,
        cost_model,
        rule_book,
    )

    assert projection.buys == ()
    assert [(item.symbol, item.reason) for item in projection.adjustments] == [
        ("600000.SH", REASON_SUSPENDED_OR_UNKNOWN)
    ]


def test_net_sell_proceeds_fund_fee_inclusive_buy_budget(
    calendar: TradingCalendar,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
):
    """Projected buys may use sell proceeds only after sell fees and tax."""
    account = Account(Decimal("1000"), calendar=calendar)
    account.apply_fill(_buy_fill("600001.SH", 100, D0, fill_id="held"))

    projection = project_rebalance(
        {"600001.SH": 0, "600000.SH": 100},
        account,
        _bars(
            _bar("600001.SH", 10.0, 10.0),
            _bar("600000.SH", 9.85, 10.0),
        ),
        TRADE_DATE,
        cost_model,
        rule_book,
    )

    assert [(order.symbol, order.quantity) for order in projection.sells] == [
        ("600001.SH", 100)
    ]
    assert [(order.symbol, order.quantity) for order in projection.buys] == [
        ("600000.SH", 100)
    ]
    assert projection.adjustments == ()


def test_explicit_suspended_status_is_audited_as_suspended_or_unknown(
    calendar: TradingCalendar,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
):
    account = Account(Decimal("10000"), calendar=calendar)
    suspended = _bar("600000.SH", 10.0, 10.0)
    suspended["status"] = "SUSPENDED"

    projection = project_rebalance(
        {"600000.SH": 100},
        account,
        _bars(suspended),
        TRADE_DATE,
        cost_model,
        rule_book,
    )

    assert projection.sells == ()
    assert projection.buys == ()
    assert len(projection.adjustments) == 1
    assert projection.adjustments[0].reason == REASON_SUSPENDED_OR_UNKNOWN


def test_net_sell_proceeds_fund_buys_after_sell_fees(
    calendar: TradingCalendar,
    cost_model: CostModel,
    rule_book: TradingRuleBook,
):
    account = Account(Decimal("2011"), calendar=calendar)
    account.apply_fill(_buy_fill("600000.SH", 100, D0, fill_id="funding"))
    assert account.cash == Decimal("1011.00")

    projection = project_rebalance(
        {"600000.SH": 0, "600001.SH": 300},
        account,
        _bars(
            _bar("600000.SH", 20.0, 20.0),
            _bar("600001.SH", 10.0, 10.0),
        ),
        TRADE_DATE,
        cost_model,
        rule_book,
    )

    # Sell quote: 100 * 19.98 - 5.00 commission - 1.00 stamp tax = 1992.00.
    # Budget becomes 3003.00: enough for a 200-share buy costing 2007.00,
    # but not 300 shares costing 3008.00.  Using gross proceeds would wrongly
    # make all 300 shares appear affordable.
    assert [(order.symbol, order.quantity) for order in projection.sells] == [
        ("600000.SH", 100)
    ]
    assert [(order.symbol, order.quantity) for order in projection.buys] == [
        ("600001.SH", 200)
    ]
    adjustment = projection.adjustments[0]
    assert (
        adjustment.symbol,
        adjustment.requested_quantity,
        adjustment.executable_quantity,
        adjustment.reason,
    ) == ("600001.SH", 300, 200, REASON_INSUFFICIENT_CASH)
