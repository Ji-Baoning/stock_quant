"""The frozen buffered risk-weighted portfolio policy and its identity.

``BufferedRiskWeightedPolicy`` is the pre-registered construction contract:
its defaults are the only approved first-run parameters, every field is part
of the canonical policy JSON that feeds ``portfolio_rule_version``,
``parameters_hash``, the strategy snapshot and therefore the experiment id.
Later explicit parameter changes are permitted only as *new* strategy
identities -- they can never mutate a registered run.  The pure target types
(``PortfolioConstructionResult`` / ``WeightTargetPeriod``) carry members,
decimal weights and signal-close prices only: never quantities, account
values or scenario fields.

All tests are offline and in-memory.
"""

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from stock_quant.portfolio.buffered_models import (
    MEMBER_STATUS_ENTERED,
    MEMBER_STATUS_EXITED,
    MEMBER_STATUS_NOT_SELECTED,
    MEMBER_STATUS_RETAINED,
    MEMBER_STATUS_RISK_INVALID,
    PORTFOLIO_CONSTRUCTION_COLUMNS,
    REBALANCE_DECISION_COLUMNS,
    BufferedRiskWeightedPolicy,
    PortfolioConstructionResult,
    WeightTargetPeriod,
)
from stock_quant.research.spec import (
    BufferedRiskWeightedPortfolioRule,
    EqualWeightPortfolioRule,
    load_experiment_spec,
)
from stock_quant.research.walk_forward.policy import canonical_sha256

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
_EXAMPLE_SPEC = f"{_REPO_ROOT}/templates/project-config/experiments/momentum_60d.yml"

_FROZEN_POLICY_DUMP = {
    "target_count": 10,
    "entry_rank": 10,
    "hold_rank": 15,
    "risk_lookback_days": 60,
    "min_risk_observations": 40,
    "volatility_floor_annualized": Decimal("0.10"),
    "max_single_weight": Decimal("0.15"),
    "rebalance_band_absolute": Decimal("0.02"),
    "gross_exposure": Decimal("1.00"),
    "long_only": True,
    "leverage": False,
    "weight_quantum": Decimal("0.000000000001"),
}


# ---------------------------------------------------------------------------
# The frozen policy contract
# ---------------------------------------------------------------------------


def test_buffered_policy_defaults_are_frozen_contract():
    rule = BufferedRiskWeightedPortfolioRule(name="buffered_risk_weighted")
    assert rule.policy().model_dump() == _FROZEN_POLICY_DUMP


def test_default_policy_matches_the_frozen_contract():
    assert BufferedRiskWeightedPolicy().model_dump() == _FROZEN_POLICY_DUMP


def test_policy_is_frozen_and_forbids_unknown_fields():
    policy = BufferedRiskWeightedPolicy()
    with pytest.raises(ValidationError):
        BufferedRiskWeightedPolicy(unexpected_field=1)
    # Pydantic v2 frozen models raise ValidationError on any assignment.
    with pytest.raises(ValidationError):
        policy.target_count = 11  # type: ignore[misc]


def test_buffered_rule_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        BufferedRiskWeightedPortfolioRule(top_n=10)


@pytest.mark.parametrize(
    "override",
    [
        {"long_only": False},
        {"leverage": True},
        {"entry_rank": 16},  # entry_rank > hold_rank
        {"target_count": 9},  # target_count != entry_rank
        {"min_risk_observations": 61},  # > risk_lookback_days
        {"volatility_floor_annualized": Decimal("0")},
        {"max_single_weight": Decimal("-0.15")},
        {"rebalance_band_absolute": Decimal("-1")},
        {"gross_exposure": Decimal("0.00")},
        {"weight_quantum": Decimal("0")},
        {"risk_lookback_days": 1},
        {"min_risk_observations": 1},
        {"target_count": 0},
    ],
)
def test_policy_rejects_contract_breaches(override):
    with pytest.raises(ValidationError):
        BufferedRiskWeightedPolicy(**override)


def test_policy_rejects_non_finite_decimals():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            BufferedRiskWeightedPolicy(gross_exposure=Decimal(str(bad)))


def test_policy_permits_explicit_contract_consistent_parameters():
    # Later explicit parameter changes are new strategy identities, not
    # mutations: the model accepts a consistent alternative registration.
    policy = BufferedRiskWeightedPolicy(
        target_count=8, entry_rank=8, hold_rank=12, risk_lookback_days=40,
        min_risk_observations=20,
    )
    assert policy.target_count == 8 and policy.hold_rank == 12


# ---------------------------------------------------------------------------
# Experiment identity: the canonical policy content is hashed
# ---------------------------------------------------------------------------


def test_buffered_rule_is_part_of_the_discriminated_union():
    assert EqualWeightPortfolioRule().name == "top_n_equal_weight"
    rule = BufferedRiskWeightedPortfolioRule()
    assert rule.name == "buffered_risk_weighted"
    # name is the discriminator: each member keeps its own literal.


def test_buffered_policy_serialization_is_deterministic_canonical_json():
    rule = BufferedRiskWeightedPortfolioRule()
    rebuilt = BufferedRiskWeightedPortfolioRule.model_validate(
        rule.model_dump(mode="json")
    )
    assert canonical_sha256(rule.model_dump(mode="json")) == canonical_sha256(
        rebuilt.model_dump(mode="json")
    )


def test_formal_yaml_selects_the_buffered_rule_with_approved_defaults():
    spec = load_experiment_spec(_EXAMPLE_SPEC)
    rule = spec.portfolio_rule
    assert isinstance(rule, BufferedRiskWeightedPortfolioRule)
    assert rule.policy().model_dump() == _FROZEN_POLICY_DUMP


def test_equal_weight_rule_survives_yaml_round_trip():
    document = {
        "hypothesis": "engineering baseline",
        "factor_versions": {"momentum_60d": "2.0.0"},
        "dataset_version": "d" * 64,
        "universe_version": "u" * 64,
        "date_range": {
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
        },
        "train_validation_holdout_policy": "not_applicable_engineering_mvp",
        "preprocessing": {"winsorization": "none", "standardization": "none"},
        "portfolio_rule": {"name": "top_n_equal_weight", "top_n": 10,
                           "lot_size": 100},
        "cost_scenarios": ["full_cost"],
        "random_seed": 42,
    }
    from stock_quant.research.spec import ExperimentSpec

    spec = ExperimentSpec.model_validate(document)
    assert isinstance(spec.portfolio_rule, EqualWeightPortfolioRule)
    assert spec.portfolio_rule.top_n == 10


# ---------------------------------------------------------------------------
# Pure construction target types (no account, execution or scenario data)
# ---------------------------------------------------------------------------


def _construction_result(**overrides) -> PortfolioConstructionResult:
    members = ("000001.SZ", "000002.SZ")
    weights = {"000001.SZ": Decimal("0.50"), "000002.SZ": Decimal("0.50")}
    audit = pd.DataFrame(
        [
            {
                "signal_date": date(2021, 1, 4),
                "symbol": symbol,
                "previous_target_member": False,
                "raw_momentum_rank": rank,
                "risk_eligible_rank": rank,
                "member_status": MEMBER_STATUS_ENTERED,
                "member_reason": "initial_entry",
                "window_start": date(2020, 10, 1),
                "window_end": date(2021, 1, 4),
                "real_close_observations": 60,
                "suspension_carry_days": 0,
                "risk_is_valid": True,
                "risk_invalid_reason": "",
                "raw_annualized_volatility": Decimal("0.20"),
                "applied_annualized_volatility": Decimal("0.20"),
                "risk_score": Decimal("5.0"),
                "raw_weight": Decimal("0.50"),
                "capped_weight": Decimal("0.50"),
                "target_weight": weights[symbol],
                "cash_weight": Decimal("0.00"),
                "portfolio_rule_version": "ab" * 32,
            }
            for rank, symbol in enumerate(members, start=1)
        ],
        columns=list(PORTFOLIO_CONSTRUCTION_COLUMNS),
    )
    payload = {
        "signal_date": date(2021, 1, 4),
        "target_members": members,
        "target_weights": weights,
        "cash_weight": Decimal("0.00"),
        "audit_frame": audit,
    }
    payload.update(overrides)
    return PortfolioConstructionResult(**payload)


def test_construction_result_is_immutable_and_scenario_free():
    result = _construction_result()
    assert result.target_members == ("000001.SZ", "000002.SZ")
    assert result.cash_weight == Decimal("0.00")
    with pytest.raises(FrozenInstanceError):
        result.cash_weight = Decimal("0.5")  # type: ignore[misc]
    declared_fields = set(PortfolioConstructionResult.__dataclass_fields__)
    assert declared_fields == {
        "signal_date", "target_members", "target_weights", "cash_weight",
        "audit_frame",
    }


def test_construction_result_requires_exact_audit_columns():
    audit = _construction_result().audit_frame.drop(columns=["risk_score"])
    with pytest.raises(ValueError, match="audit"):
        _construction_result(audit_frame=audit)


def test_construction_result_weights_must_sum_to_gross_exposure():
    with pytest.raises(ValueError, match="gross"):
        _construction_result(
            target_weights={
                "000001.SZ": Decimal("0.40"),
                "000002.SZ": Decimal("0.40"),
            }
        )


def test_construction_result_rejects_member_weight_mismatch():
    with pytest.raises(ValueError, match="member"):
        _construction_result(
            target_members=("000001.SZ",),
            target_weights={
                "000001.SZ": Decimal("0.50"),
                "000002.SZ": Decimal("0.50"),
            },
        )


def test_weight_target_period_carries_no_execution_day_data():
    period = WeightTargetPeriod(
        signal_date=date(2021, 1, 4),
        target_weights={"000001.SZ": Decimal("0.60"),
                        "000002.SZ": Decimal("0.40")},
        net_equity_prices={"000001.SZ": Decimal("10.00"),
                           "000002.SZ": Decimal("20.00")},
        previous_members=frozenset(),
        current_members=frozenset({"000001.SZ", "000002.SZ"}),
    )
    declared_fields = set(WeightTargetPeriod.__dataclass_fields__)
    assert declared_fields == {
        "signal_date", "target_weights", "net_equity_prices",
        "previous_members", "current_members",
    }
    with pytest.raises(FrozenInstanceError):
        period.signal_date = date(2021, 1, 5)  # type: ignore[misc]
    # the fixed price map is the signal-close valuation basis only
    assert period.net_equity_prices["000001.SZ"] == Decimal("10.00")


def test_weight_target_period_rejects_member_weight_mismatch():
    with pytest.raises(ValueError, match="member"):
        WeightTargetPeriod(
            signal_date=date(2021, 1, 4),
            target_weights={"000001.SZ": Decimal("0.60")},
            net_equity_prices={"000001.SZ": Decimal("10.00")},
            previous_members=frozenset({"000001.SZ"}),
            current_members=frozenset({"000001.SZ", "000002.SZ"}),
        )


def test_weight_target_period_rejects_nonpositive_prices():
    with pytest.raises(ValueError, match="price"):
        WeightTargetPeriod(
            signal_date=date(2021, 1, 4),
            target_weights={"000001.SZ": Decimal("1.00")},
            net_equity_prices={"000001.SZ": Decimal("0")},
            previous_members=frozenset(),
            current_members=frozenset({"000001.SZ"}),
        )


# ---------------------------------------------------------------------------
# Ordered audit-column constants
# ---------------------------------------------------------------------------


def test_audit_column_constants_are_ordered_and_complete():
    assert PORTFOLIO_CONSTRUCTION_COLUMNS == (
        "signal_date",
        "symbol",
        "previous_target_member",
        "raw_momentum_rank",
        "risk_eligible_rank",
        "member_status",
        "member_reason",
        "window_start",
        "window_end",
        "real_close_observations",
        "suspension_carry_days",
        "risk_is_valid",
        "risk_invalid_reason",
        "raw_annualized_volatility",
        "applied_annualized_volatility",
        "risk_score",
        "raw_weight",
        "capped_weight",
        "target_weight",
        "cash_weight",
        "portfolio_rule_version",
    )
    assert REBALANCE_DECISION_COLUMNS == (
        "signal_date",
        "execution_day",
        "symbol",
        "is_continuing",
        "current_weight",
        "target_weight",
        "weight_difference",
        "current_quantity",
        "target_quantity",
        "order_side",
        "order_quantity",
        "signal_close_equity",
        "reason",
    )


def test_member_status_vocabulary_is_stable():
    assert (
        MEMBER_STATUS_RETAINED == "retained"
        and MEMBER_STATUS_ENTERED == "entered"
        and MEMBER_STATUS_EXITED == "exited"
        and MEMBER_STATUS_NOT_SELECTED == "not_selected"
        and MEMBER_STATUS_RISK_INVALID == "risk_invalid"
    )
