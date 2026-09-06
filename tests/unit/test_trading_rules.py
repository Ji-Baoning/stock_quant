"""Effective-dated price-limit and IPO no-limit rule tests (Task 5).

The rule book reads ``configs/trading_rules.yml``; no rule is fetched from the
network. All prices and ratios are compared as ``Decimal`` values.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from stock_quant.data_model.trading_rules import (
    REASON_BUY_AT_UPPER_LIMIT,
    REASON_SELL_AT_LOWER_LIMIT,
    TradingRuleBook,
    UncoveredRuleError,
)

ROOT = Path(__file__).resolve().parents[2]
RULES_YAML = ROOT / "configs" / "trading_rules.yml"


@pytest.fixture
def rule_book() -> TradingRuleBook:
    return TradingRuleBook.from_yaml(RULES_YAML)


def test_price_limit_schedule_is_effective_dated(rule_book: TradingRuleBook):
    chinext = rule_book.limit_rate("300001.SZ", date(2020, 8, 21), status="NORMAL")
    assert chinext == Decimal("0.10")
    chinext = rule_book.limit_rate("300001.SZ", date(2020, 8, 24), status="NORMAL")
    assert chinext == Decimal("0.20")
    st = rule_book.limit_rate("600000.SH", date(2020, 8, 24), status="ST")
    assert st == Decimal("0.05")


def test_ordinary_main_board_rate_is_ten_percent(rule_book: TradingRuleBook):
    for symbol in ("600000.SH", "601398.SH", "603288.SH", "000001.SZ", "002594.SZ"):
        rate = rule_book.limit_rate(symbol, date(2021, 5, 10), status="NORMAL")
        assert rate == Decimal("0.10")
        rate = rule_book.limit_rate(symbol, date(2023, 6, 1), status="NORMAL")
        assert rate == Decimal("0.10")


def test_star_rate_is_twenty_percent_since_launch(rule_book: TradingRuleBook):
    star = rule_book.limit_rate("688001.SH", date(2020, 8, 24), status="NORMAL")
    assert star == Decimal("0.20")
    star = rule_book.limit_rate("688981.SH", date(2021, 1, 4), status="NORMAL")
    assert star == Decimal("0.20")


def test_st_star_rate_applies_to_both_main_boards(rule_book: TradingRuleBook):
    st = rule_book.limit_rate("000001.SZ", date(2020, 8, 24), status="ST")
    assert st == Decimal("0.05")
    st = rule_book.limit_rate("600000.SH", date(2020, 8, 24), status="*ST")
    assert st == Decimal("0.05")
    st = rule_book.limit_rate("002594.SZ", date(2020, 8, 24), status="ST")
    assert st == Decimal("0.05")


def test_uncovered_status_blocks_instead_of_assuming_a_rate(
    rule_book: TradingRuleBook,
):
    # STAR/ChiNext carry no main-board-style ST rule in the config.
    with pytest.raises(UncoveredRuleError):
        rule_book.limit_rate("688001.SH", date(2020, 8, 24), status="ST")
    with pytest.raises(UncoveredRuleError):
        rule_book.limit_rate("300001.SZ", date(2020, 8, 24), status="ST")


def test_uncovered_date_blocks_when_before_the_rule_became_effective(
    rule_book: TradingRuleBook,
):
    with pytest.raises(UncoveredRuleError):
        rule_book.limit_rate("600000.SH", date(1997, 1, 2), status="ST")


def test_unrecognized_symbol_blocks(rule_book: TradingRuleBook):
    with pytest.raises(UncoveredRuleError):
        rule_book.limit_rate("999999.SH", date(2020, 8, 24), status="NORMAL")


def test_no_limit_first_sessions_are_effective_dated(rule_book: TradingRuleBook):
    # STAR: registration-based regime from launch, first five sessions.
    assert rule_book.no_limit_first_sessions("688001.SH", date(2020, 1, 2)) == 5
    # ChiNext: only from the 2020-08-24 registration reform.
    assert rule_book.no_limit_first_sessions("300001.SZ", date(2020, 8, 21)) == 0
    assert rule_book.no_limit_first_sessions("300001.SZ", date(2020, 8, 24)) == 5
    # Main boards: registration regime from 2023-04-10.
    assert rule_book.no_limit_first_sessions("600000.SH", date(2023, 4, 9)) == 0
    assert rule_book.no_limit_first_sessions("600000.SH", date(2023, 4, 10)) == 5


def test_limit_prices_round_to_a_cent_half_up(rule_book: TradingRuleBook):
    # ST 5% band on a 2.50 reference exercises the .005 rounding edge.
    limits = rule_book.price_limits(
        "600000.SH",
        date(2020, 8, 24),
        Decimal("2.50"),
        status="ST",
    )
    assert limits.upper == Decimal("2.63")
    assert limits.lower == Decimal("2.38")


def test_limit_prices_use_exchange_decimal_rounding_for_normal_boards(
    rule_book: TradingRuleBook,
):
    limits = rule_book.price_limits(
        "000001.SZ",
        date(2021, 5, 10),
        Decimal("10.00"),
        status="NORMAL",
    )
    assert limits.upper == Decimal("11.00")
    assert limits.lower == Decimal("9.00")


def test_chinext_twenty_percent_limit_prices(rule_book: TradingRuleBook):
    limits = rule_book.price_limits(
        "300001.SZ", date(2020, 8, 24), Decimal("5.00"), status="NORMAL"
    )
    assert limits.upper == Decimal("6.00")
    assert limits.lower == Decimal("4.00")


def test_ipo_first_sessions_have_no_daily_limit(rule_book: TradingRuleBook):
    within_window = rule_book.price_limits(
        "688001.SH",
        date(2019, 7, 22),
        Decimal("50.00"),
        status="NORMAL",
        listed_sessions=1,
    )
    assert within_window.no_limit
    assert within_window.upper is None
    assert within_window.lower is None
    fifth = rule_book.price_limits(
        "688001.SH", date(2019, 7, 22), Decimal("50.00"), listed_sessions=5
    )
    assert fifth.no_limit
    # From the sixth session the 20% STAR band applies again.
    seasoned = rule_book.price_limits(
        "688001.SH", date(2019, 7, 22), Decimal("50.00"), listed_sessions=6
    )
    assert not seasoned.no_limit
    assert seasoned.upper == Decimal("60.00")
    assert seasoned.lower == Decimal("40.00")


def test_main_board_ipo_window_only_under_registration_regime(
    rule_book: TradingRuleBook,
):
    # Pre-2023 main-board listings have a limit from their very first session.
    limits = rule_book.price_limits(
        "600000.SH",
        date(2020, 8, 24),
        Decimal("10.00"),
        status="NORMAL",
        listed_sessions=1,
    )
    assert not limits.no_limit
    assert limits.upper == Decimal("11.00")


def test_buy_at_upper_limit_and_sell_at_lower_limit_are_rejected(
    rule_book: TradingRuleBook,
):
    limits = rule_book.price_limits(
        "000001.SZ", date(2021, 5, 10), Decimal("10.00"), status="NORMAL"
    )
    assert limits.block_reason("buy", Decimal("11.00")) == REASON_BUY_AT_UPPER_LIMIT
    assert limits.block_reason("buy", Decimal("10.99")) is None
    assert limits.block_reason("sell", Decimal("9.00")) == REASON_SELL_AT_LOWER_LIMIT
    assert limits.block_reason("sell", Decimal("9.01")) is None


def test_no_limit_window_never_blocks_an_order(rule_book: TradingRuleBook):
    within_window = rule_book.price_limits(
        "688001.SH",
        date(2019, 7, 22),
        Decimal("50.00"),
        listed_sessions=1,
    )
    assert within_window.block_reason("buy", Decimal("1000.00")) is None
    assert within_window.block_reason("sell", Decimal("0.01")) is None
