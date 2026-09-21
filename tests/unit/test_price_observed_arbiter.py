"""The price-observation settlement rule (spec D2/D5).

Every test authors its observation inline: the rule is pure, the channel that
obtains observations is injected, and nothing here touches a network or a wall
clock.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stock_quant.data_model.corporate_actions import ConflictTerms
from stock_quant.data_sources.price_observed import (
    PriceObservation,
    PriceObservedArbiter,
    expected_factor,
    settle,
)

_DAY = date(2023, 7, 17)
_PREV = date(2023, 7, 14)


def terms(
    *,
    symbol="600188.SH",
    cash_per_ten="0.5",
    bonus_per_ten="0.0",
    capitalization_per_ten="0.0",
    rights_per_ten="0.0",
    rights_price_per_share="0.0",
) -> ConflictTerms:
    """One side's stated terms, per ten shares as the suppliers state them."""
    return ConflictTerms(
        symbol=symbol,
        ex_date=_DAY,
        cash_per_ten=Decimal(cash_per_ten),
        bonus_per_ten=Decimal(bonus_per_ten),
        capitalization_per_ten=Decimal(capitalization_per_ten),
        rights_per_ten=Decimal(rights_per_ten),
        rights_price_per_share=Decimal(rights_price_per_share),
    )


def test_expected_factor_is_the_exchange_formula():
    """(prev_close - cash) / (prev_close * (1 + bonus + cap)), per share."""
    priced = terms(cash_per_ten="4.3", bonus_per_ten="5.0")

    assert expected_factor(priced, 30.0) == (30.0 - 0.43) / (30.0 * 1.5)


def test_settles_the_side_the_reference_price_corroborates():
    """cninfo implies 0.98, eastmoney 0.95, and the market applied 0.98.

    At prev_close 10.0 one tick is exactly 0.001, so cninfo sits 0 ticks from
    the observation and eastmoney 30 -- inside the winner tolerance, far
    outside it, and clear of the loser bar.
    """
    cninfo = terms(cash_per_ten="2.0")  # 0.20/share -> (10 - 0.2) / 10
    eastmoney = terms(cash_per_ten="5.0")  # 0.50/share -> (10 - 0.5) / 10
    observation = PriceObservation(
        prev_close=10.0, prev_close_date=_PREV, pre_close=9.80
    )

    settlement = settle(cninfo, eastmoney, observation)

    assert settlement is not None
    assert settlement.side == "cninfo"
    assert settlement.cninfo_ticks == pytest.approx(0.0)
    assert settlement.eastmoney_ticks == pytest.approx(30.0)


def test_two_sides_implying_one_factor_are_never_settled():
    """002269.SZ 2015-05-12: same total, different split.

    cninfo states 10送6转9 and eastmoney 10送5转10; both scale by 1.5, so the
    market's own factor -- 0.396 here, which both sides agree on -- cannot
    separate them.  This is the channel's principled ceiling, not an
    implementation gap.
    """
    cninfo = terms(
        symbol="002269.SZ",
        cash_per_ten="1.0",
        bonus_per_ten="6.0",
        capitalization_per_ten="9.0",
    )
    eastmoney = terms(
        symbol="002269.SZ",
        cash_per_ten="1.0",
        bonus_per_ten="5.0",
        capitalization_per_ten="10.0",
    )
    observation = PriceObservation(
        prev_close=10.0, prev_close_date=_PREV, pre_close=3.96
    )

    assert expected_factor(cninfo, 10.0) == expected_factor(eastmoney, 10.0)
    assert settle(cninfo, eastmoney, observation) is None


def test_a_loser_inside_the_bar_is_not_settled():
    """The near-miss form: the loser sits 1.4 ticks away, under the bar.

    Both sides lie close to the observation, so neither is distinguishable --
    the machine says so rather than picking the nearer one (spec D5).
    """
    cninfo = terms(cash_per_ten="0.84")  # expected 0.9958
    eastmoney = terms(cash_per_ten="0.70")  # expected 0.9965
    observation = PriceObservation(
        prev_close=20.0, prev_close_date=_PREV, pre_close=19.916
    )

    assert settle(cninfo, eastmoney, observation) is None


def test_a_winner_outside_the_tolerance_is_not_settled():
    """The far form: nobody matches the observation, so nobody is chosen."""
    cninfo = terms(cash_per_ten="0.84")
    eastmoney = terms(cash_per_ten="0.70")
    observation = PriceObservation(
        prev_close=20.0, prev_close_date=_PREV, pre_close=19.0
    )

    assert settle(cninfo, eastmoney, observation) is None


def test_an_absent_channel_settles_nothing():
    """The fail-closed answer, not a default side."""
    arbiter = PriceObservedArbiter(lambda symbol, ex_date: None)

    assert arbiter.arbitrate(terms(), terms(cash_per_ten="5.0")) is None


def test_a_side_with_subscription_terms_is_refused():
    """The calibrated formula models no 配股 term, so it judges none."""
    cninfo = terms(rights_per_ten="3.0", rights_price_per_share="1.0")

    settlement = settle(
        cninfo,
        terms(cash_per_ten="5.0"),
        PriceObservation(prev_close=10.0, prev_close_date=_PREV, pre_close=9.80),
    )

    assert settlement is None
