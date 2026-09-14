"""The 2-percentage-point rebalance band and its exact boundary.

``apply_rebalance_band`` decides one symbol's reconciliation: a *continuing*
position (in both the previous and current common member sets with a positive
target weight) is rebalanced only when its weight difference reaches the
absolute band -- a difference strictly below 2 percentage points is
suppressed as ``within_rebalance_band`` and exactly 2 percentage points
rebalances.  New entries and zero-weight exits never use the band: they
always reconcile, and a difference below one lot emits ``below_one_lot`` with
no order.  Suppression is a recorded decision, never a silent drop.

All tests are offline and in-memory.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from stock_quant.portfolio.buffered_models import BufferedRiskWeightedPolicy
from stock_quant.portfolio.rebalance_band import (
    REASON_AT_TARGET,
    REASON_BELOW_ONE_LOT,
    REASON_ENTERING,
    REASON_EXITING,
    REASON_OUTSIDE_BAND,
    REASON_WITHIN_BAND,
    RebalanceDecision,
    apply_rebalance_band,
)

POLICY = BufferedRiskWeightedPolicy()


def test_difference_below_two_percent_is_suppressed():
    decision = apply_rebalance_band(symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.131"), target_weight=Decimal("0.150"),
        current_quantity=800, target_quantity=900, policy=POLICY)
    assert not decision.should_order
    assert decision.reason == "within_rebalance_band"


def test_exactly_two_percent_rebalances():
    decision = apply_rebalance_band(symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.130"), target_weight=Decimal("0.150"),
        current_quantity=800, target_quantity=900, policy=POLICY)
    assert decision.should_order


def test_entry_and_exit_never_use_band():
    assert apply_rebalance_band(symbol="000001.SZ", is_continuing=False,
        current_weight=Decimal("0"), target_weight=Decimal("0.01"),
        current_quantity=0, target_quantity=100, policy=POLICY).should_order
    assert apply_rebalance_band(symbol="000002.SZ", is_continuing=False,
        current_weight=Decimal("0.02"), target_weight=Decimal("0"),
        current_quantity=100, target_quantity=0, policy=POLICY).should_order


def test_band_boundary_is_strictly_below_the_absolute_band():
    inside = apply_rebalance_band(
        symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.1300001"), target_weight=Decimal("0.15"),
        current_quantity=800, target_quantity=900, policy=POLICY,
    )
    assert not inside.should_order
    assert inside.reason == REASON_WITHIN_BAND
    outside = apply_rebalance_band(
        symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.13"), target_weight=Decimal("0.15"),
        current_quantity=800, target_quantity=900, policy=POLICY,
    )
    assert outside.should_order
    assert outside.reason == REASON_OUTSIDE_BAND


def test_negative_weight_difference_uses_the_absolute_band():
    decision = apply_rebalance_band(
        symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.20"), target_weight=Decimal("0.181"),
        current_quantity=1000, target_quantity=900, policy=POLICY,
    )
    assert not decision.should_order
    assert decision.reason == REASON_WITHIN_BAND
    assert decision.weight_difference == Decimal("-0.019")


def test_continuing_entry_below_one_lot_is_suppressed():
    decision = apply_rebalance_band(
        symbol="600000.SH", is_continuing=True,
        current_weight=Decimal("0.10"), target_weight=Decimal("0.13"),
        current_quantity=1000, target_quantity=1080, policy=POLICY,
    )
    assert decision.weight_difference == Decimal("0.03")
    assert not decision.should_order
    assert decision.reason == REASON_BELOW_ONE_LOT


def test_entry_below_one_lot_never_orders():
    decision = apply_rebalance_band(
        symbol="600000.SH", is_continuing=False,
        current_weight=Decimal("0"), target_weight=Decimal("0.0001"),
        current_quantity=0, target_quantity=50, policy=POLICY,
    )
    assert not decision.should_order
    assert decision.reason == REASON_BELOW_ONE_LOT


def test_exit_below_one_lot_is_suppressed():
    decision = apply_rebalance_band(
        symbol="600000.SH", is_continuing=False,
        current_weight=Decimal("0.001"), target_weight=Decimal("0"),
        current_quantity=50, target_quantity=0, policy=POLICY,
    )
    assert not decision.should_order
    assert decision.reason == REASON_BELOW_ONE_LOT


def test_entry_order_is_a_buy_and_exit_order_is_a_sell():
    entry = apply_rebalance_band(
        symbol="600000.SH", is_continuing=False,
        current_weight=Decimal("0"), target_weight=Decimal("0.15"),
        current_quantity=0, target_quantity=1500, policy=POLICY,
    )
    assert entry.should_order
    assert entry.order_side == "BUY"
    assert entry.order_quantity == 1500
    assert entry.reason == REASON_ENTERING
    sell = apply_rebalance_band(
        symbol="600000.SH", is_continuing=False,
        current_weight=Decimal("0.15"), target_weight=Decimal("0"),
        current_quantity=1500, target_quantity=0, policy=POLICY,
    )
    assert sell.should_order
    assert sell.order_side == "SELL"
    assert sell.order_quantity == 1500
    assert sell.reason == REASON_EXITING


def test_zero_difference_records_within_band_or_at_target():
    continuing = apply_rebalance_band(
        symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.15"), target_weight=Decimal("0.15"),
        current_quantity=1500, target_quantity=1500, policy=POLICY,
    )
    assert not continuing.should_order
    assert continuing.reason == REASON_WITHIN_BAND
    # a non-continuing row with nothing to reconcile is recorded at target
    settled = apply_rebalance_band(
        symbol="000002.SZ", is_continuing=False,
        current_weight=Decimal("0"), target_weight=Decimal("0"),
        current_quantity=0, target_quantity=0, policy=POLICY,
    )
    assert not settled.should_order
    assert settled.reason == REASON_AT_TARGET


def test_decision_is_frozen_and_defaults_are_scenario_free():
    decision = apply_rebalance_band(
        symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.13"), target_weight=Decimal("0.15"),
        current_quantity=800, target_quantity=900, policy=POLICY,
    )
    assert isinstance(decision, RebalanceDecision)
    with pytest.raises(Exception):
        decision.reason = "mutated"  # type: ignore[misc]
    # a bare band decision carries no scenario dates; the rebalancer stamps
    # them when it records the row
    assert decision.signal_date is None and decision.execution_day is None


def test_dates_are_stamped_without_mutating_the_original():
    decision = apply_rebalance_band(
        symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.13"), target_weight=Decimal("0.15"),
        current_quantity=800, target_quantity=900, policy=POLICY,
    )
    stamped = replace(
        decision, signal_date=date(2021, 6, 1), execution_day=date(2021, 6, 2)
    )
    assert stamped.signal_date == date(2021, 6, 1)
    assert decision.signal_date is None
    assert stamped.should_order
