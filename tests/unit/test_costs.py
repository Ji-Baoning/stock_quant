"""Dated A-share fees, slippage and per-fill cost quote tests (Task 9).

``CostModel`` prices a fill from the unadjusted execution open: buys trade at
``open*(1+slippage)`` and sells at ``open*(1-slippage)``, commission is
``max(notional*rate, minimum)`` per fill and stamp tax applies to sells only.
Fees and cash amounts are ``Decimal`` so money accounting is exact to the
cent; the active rate is the latest whose ``effective_from`` is not after the
trade date.  All three scenarios of ``configs/costs.yml`` (zero-cost,
commission/tax and full-cost) are exercised offline.
"""

from datetime import date
from decimal import Decimal

import pytest

from stock_quant.backtest.costs import CostModel
from stock_quant.backtest.models import BUY, SELL, FeeBreakdown
from pathlib import Path

from stock_quant.config import CostConfig, CostRate, CostScenario, load_project_config

# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def rate(
    commission: float = 0.0,
    minimum: float = 0.0,
    stamp: float = 0.0,
    slippage: float = 0.0,
    effective_from: date = date(2020, 1, 1),
) -> CostRate:
    return CostRate(
        effective_from=effective_from,
        commission_rate=commission,
        minimum_commission=minimum,
        stamp_tax_sell_rate=stamp,
        slippage_rate=slippage,
    )


def _cost_config() -> CostConfig:
    """Mirror of ``configs/costs.yml`` (stamp tax split at the 2023-08-28 cut)."""

    def commission_tax_rates() -> list[CostRate]:
        return [
            rate(effective_from=date(2015, 1, 1), commission=0.0003,
                 minimum=5.0, stamp=0.001, slippage=0.0),
            rate(effective_from=date(2023, 8, 28), commission=0.0003,
                 minimum=5.0, stamp=0.0005, slippage=0.0),
        ]

    def full_cost_rates() -> list[CostRate]:
        return [
            rate(effective_from=date(2015, 1, 1), commission=0.0003,
                 minimum=5.0, stamp=0.001, slippage=0.001),
            rate(effective_from=date(2023, 8, 28), commission=0.0003,
                 minimum=5.0, stamp=0.0005, slippage=0.001),
        ]

    return CostConfig(
        scenarios=[
            CostScenario(
                name="zero_cost",
                rates=[rate(effective_from=date(2015, 1, 1))],
            ),
            CostScenario(name="commission_tax", rates=commission_tax_rates()),
            CostScenario(name="full_cost", rates=full_cost_rates()),
        ]
    )


# --------------------------------------------------------------------------- #
# Minimum commission and sell-only stamp tax
# --------------------------------------------------------------------------- #


def test_minimum_commission_and_sell_stamp_tax():
    model = CostModel(
        rate(commission=0.0003, minimum=5, stamp=0.0005, slippage=0.001)
    )
    buy = model.calculate(
        side=BUY, quantity=100, raw_price=10, trade_date=date(2020, 1, 2)
    )
    sell = model.calculate(
        side=SELL, quantity=100, raw_price=10, trade_date=date(2020, 1, 2)
    )
    assert buy.commission == 5 and buy.stamp_tax == 0
    assert sell.commission == 5 and sell.stamp_tax == pytest.approx(0.5)


def test_commission_scales_above_the_minimum():
    model = CostModel(
        rate(commission=0.0003, minimum=5, stamp=0.0005, slippage=0.001)
    )
    buy = model.calculate(
        side=BUY, quantity=10000, raw_price=10, trade_date=date(2020, 1, 2)
    )
    # price = 10.01, gross = 100100.00, commission = 30.03 > 5.
    assert buy.commission == Decimal("30.03")
    assert buy.stamp_tax == 0


# --------------------------------------------------------------------------- #
# Slippage pricing
# --------------------------------------------------------------------------- #


def test_buy_prices_at_open_plus_slippage_and_sell_at_open_minus_slippage():
    model = CostModel(rate(slippage=0.001))
    trade_date = date(2020, 1, 2)
    buy = model.calculate(BUY, 100, Decimal("10.00"), trade_date)
    sell = model.calculate(SELL, 100, Decimal("10.00"), trade_date)
    assert buy.price == Decimal("10.01")
    assert sell.price == Decimal("9.99")


def test_zero_slippage_keeps_the_unadjusted_open():
    model = CostModel(rate(slippage=0.0))
    buy = model.calculate(BUY, 100, Decimal("10.00"), date(2020, 1, 2))
    sell = model.calculate(SELL, 100, Decimal("10.00"), date(2020, 1, 2))
    assert buy.price == Decimal("10.00")
    assert sell.price == Decimal("10.00")


# --------------------------------------------------------------------------- #
# Effective-dated rates
# --------------------------------------------------------------------------- #


def test_picks_the_latest_rate_whose_effective_date_is_not_after_trade_date():
    model = CostModel(
        [
            rate(commission=0.001, minimum=0, effective_from=date(2020, 1, 1)),
            rate(commission=0.002, minimum=0, effective_from=date(2020, 6, 1)),
        ]
    )
    before = model.calculate(BUY, 100, Decimal("10.00"), date(2020, 3, 1))
    after = model.calculate(BUY, 100, Decimal("10.00"), date(2020, 6, 2))
    assert before.commission == Decimal("1.00")
    assert after.commission == Decimal("2.00")


def test_no_rate_effective_on_the_trade_date_is_rejected():
    model = CostModel(rate(effective_from=date(2020, 1, 1)))
    with pytest.raises(ValueError, match="effective"):
        model.calculate(BUY, 100, Decimal("10.00"), date(2019, 12, 31))


# --------------------------------------------------------------------------- #
# Scenario selection and the three cost parameterisations
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("scenario_name", "buy_price", "buy_commission", "sell_commission", "sell_stamp"),
    [
        (
            "zero_cost",
            Decimal("10.00"),
            Decimal("0.00"),
            Decimal("0.00"),
            Decimal("0.00"),
        ),
        (
            "commission_tax",
            Decimal("10.00"),
            Decimal("5.00"),
            Decimal("5.00"),
            Decimal("1.00"),
        ),
        (
            "full_cost",
            Decimal("10.01"),
            Decimal("5.00"),
            Decimal("5.00"),
            Decimal("1.00"),
        ),
    ],
)
def test_three_cost_scenarios_price_and_tax_exactly(
    scenario_name, buy_price, buy_commission, sell_commission, sell_stamp
):
    model = CostModel.from_config(_cost_config(), scenario_name)
    trade_date = date(2020, 1, 2)
    buy = model.calculate(BUY, 100, Decimal("10.00"), trade_date)
    sell = model.calculate(SELL, 100, Decimal("10.00"), trade_date)
    assert buy.price == buy_price
    assert buy.commission == buy_commission
    # Sell price is not asserted here: slippage is sell-side asymmetric and is
    # covered by its own dedicated test below.
    assert sell.commission == sell_commission
    assert sell.stamp_tax == sell_stamp


def test_full_cost_prices_the_reduced_stamp_tax_from_the_cut_day():
    """From 2023-08-28 the sell stamp tax is 0.5 per mille, not 1."""
    model = CostModel.from_config(_cost_config(), "full_cost")
    cut_day = model.calculate(SELL, 100, Decimal("10.00"), date(2023, 8, 28))
    day_before = model.calculate(SELL, 100, Decimal("10.00"), date(2023, 8, 27))
    assert cut_day.stamp_tax == Decimal("0.50")
    assert day_before.stamp_tax == Decimal("1.00")


def test_from_config_unknown_scenario_raises():
    with pytest.raises(ValueError, match="commission_tax"):
        CostModel.from_config(_cost_config(), "no_such_scenario")


def test_calculate_returns_an_immutable_fee_breakdown():
    model = CostModel(rate(commission=0.0003, minimum=5, stamp=0.0005))
    quote = model.calculate(SELL, 100, Decimal("10.00"), date(2020, 1, 2))
    assert isinstance(quote, FeeBreakdown)
    assert quote.gross == quote.price * 100
    assert quote.commission == Decimal("5.00")
    assert quote.stamp_tax == Decimal("0.50")
    with pytest.raises(AttributeError):  # frozen dataclass is immutable
        quote.commission = Decimal("0")  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# The production configs/costs.yml itself
# --------------------------------------------------------------------------- #

#: ``tests/unit/test_costs.py`` -> repo root -> ``project/``.
_PROJECT_ROOT = Path(__file__).resolve().parents[2] / "project"


def test_production_costs_yml_splits_stamp_tax_at_the_2023_cut():
    """The real costs.yml carries the official stamp-tax history.

    Stamp tax was 1 per mille on sells until 2023-08-27 and 0.5 per mille from
    2023-08-28.  A dataset window starting 2015 must therefore price the two
    regimes from one dated schedule, and the boundary day itself must already
    select the reduced rate.
    """
    config = load_project_config(_PROJECT_ROOT)
    for scenario in ("commission_tax", "full_cost"):
        model = CostModel.from_config(config.costs, scenario)
        assert model.stamp_tax_rate(date(2015, 1, 5)) == Decimal("0.001")
        assert model.stamp_tax_rate(date(2023, 8, 25)) == Decimal("0.001")
        assert model.stamp_tax_rate(date(2023, 8, 28)) == Decimal("0.0005")
        assert model.stamp_tax_rate(date(2026, 1, 5)) == Decimal("0.0005")


def test_production_costs_yml_zero_scenario_is_date_invariant():
    """``zero_cost`` is all-zero on every date in the window."""
    config = load_project_config(_PROJECT_ROOT)
    model = CostModel.from_config(config.costs, "zero_cost")
    for day in (date(2015, 1, 5), date(2023, 8, 28), date(2026, 1, 5)):
        assert model.commission_rate(day) == Decimal("0")
        assert model.minimum_commission(day) == Decimal("0")
        assert model.stamp_tax_rate(day) == Decimal("0")
        assert model.slippage(day) == Decimal("0")


def test_production_costs_yml_rejects_dates_before_the_window():
    """No rate is effective before the schedule starts; it fails loudly."""
    config = load_project_config(_PROJECT_ROOT)
    model = CostModel.from_config(config.costs, "full_cost")
    with pytest.raises(ValueError, match="no cost rate is effective"):
        model.stamp_tax_rate(date(2014, 12, 31))
