"""Strict walk-forward policy models: the frozen fixed-calendar contract.

``WalkForwardPolicy`` and ``StabilityPolicy`` are strict (``extra="forbid"``),
frozen Pydantic models whose every field is a ``Literal``: the first-phase
walk-forward discipline is one approved contract, not a tunable configuration
surface.  Overlapping OOS folds, a different warmup, or any invented field is
a validation error, and the models hash deterministically under canonical JSON
so the frozen policies can enter the experiment identity.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    WalkForwardPolicy,
    canonical_sha256,
)


def test_default_policy_is_the_approved_fixed_calendar_contract():
    policy = WalkForwardPolicy()
    assert policy.mode == "fixed_calendar_oos_v1"
    assert (policy.warmup_years, policy.warmup_unit) == (3, "calendar_years")
    assert policy.min_warmup_trading_days == 756
    assert policy.required_stable_history_days_before_s == 60
    assert (policy.oos_months, policy.step_months) == (12, 12)
    assert policy.account_reset is True
    assert policy.global_drawdown_aggregation == "forbidden"


def test_policy_rejects_overlapping_oos_folds():
    with pytest.raises(ValidationError, match="step_months"):
        WalkForwardPolicy(oos_months=12, step_months=6)


def test_policy_rejects_any_mode_other_than_the_fixed_calendar():
    with pytest.raises(ValidationError, match="mode"):
        WalkForwardPolicy(mode="rolling_window_v9")


def test_policy_rejects_extra_fields():
    with pytest.raises(ValidationError, match="extra"):
        WalkForwardPolicy(unused_bonus_field=1)


def test_policy_is_frozen_and_cannot_be_mutated():
    policy = WalkForwardPolicy()
    with pytest.raises(ValidationError):
        policy.step_months = 6  # type: ignore[misc]


def test_walk_forward_policy_hashes_deterministically():
    first = canonical_sha256(WalkForwardPolicy().model_dump(mode="json"))
    second = canonical_sha256(WalkForwardPolicy().model_dump(mode="json"))
    assert first == second
    assert len(first) == 64


def test_default_stability_policy_is_the_frozen_stability_v1_contract():
    policy = StabilityPolicy()
    assert policy.policy_version == "stability-v1"
    assert policy.minimum_executed_folds == 5
    assert policy.minimum_positive_fold_ratio == 0.60
    assert policy.worst_fold_calendar_return_floor == -0.10
    assert policy.annualization_sessions == 252
    assert policy.risk_free_rate == 0.0


def test_stability_policy_rejects_renamed_or_extra_fields():
    with pytest.raises(ValidationError, match="extra"):
        StabilityPolicy(minimum_positive_ratio=0.9)


def test_canonical_sha256_is_order_and_whitespace_insensitive():
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})
    assert canonical_sha256([{"x": 1}]) != canonical_sha256([{"x": 2}])
