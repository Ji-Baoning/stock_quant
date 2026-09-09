"""Terminal failure and policy-bound stability evaluation.

``evaluate_stability`` is the only place a walk-forward run's status and
stability conclusion are decided, in strict precedence order: any integrity
failure, any ``failed_preflight`` outcome, any missing declared cost scenario
or any incomplete scenario artifact makes the research ``FAILED`` with a null
conclusion (never INCONCLUSIVE); any legal ``skipped_not_tradeable`` fold or
fewer than five executed folds leaves the research COMPLETED but
``INCONCLUSIVE``; otherwise every declared cost scenario is evaluated
independently against the frozen ``stability-v1`` thresholds and STABLE is
the conjunction of all of them.  A non-null conclusion always carries the
recomputed ``stability_policy_hash`` -- without it a stability verdict can
never be a formal result.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stock_quant.research.models import RunStatus
from stock_quant.research.walk_forward.evaluation import (
    StabilityEvaluation,
    evaluate_stability,
)
from stock_quant.research.walk_forward.metrics import ScenarioMetrics
from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    canonical_sha256,
)
from stock_quant.research.walk_forward.schedule import FoldOutcome


@pytest.fixture
def policy() -> StabilityPolicy:
    return StabilityPolicy()


def passing_scenario(scenario: str, fold_id: str = "f" * 64) -> ScenarioMetrics:
    return ScenarioMetrics(
        fold_id=fold_id,
        scenario=scenario,
        observation_count=244,
        fold_calendar_return=0.05,
        per_fold_max_drawdown=-0.03,
        reject_rate=0.0,
        submitted_order_count=10,
    )


def failing_scenario(scenario: str, fold_id: str = "f" * 64) -> ScenarioMetrics:
    return ScenarioMetrics(
        fold_id=fold_id,
        scenario=scenario,
        observation_count=244,
        fold_calendar_return=-0.50,
        per_fold_max_drawdown=-0.55,
        reject_rate=0.0,
        submitted_order_count=10,
    )


def executed_outcomes(count: int) -> tuple:
    return tuple(
        FoldOutcome(fold_id=f"fold-{index:04d}", status="executed")
        for index in range(count)
    )


@pytest.fixture
def passing_metrics() -> tuple:
    return tuple(
        passing_scenario("full_cost", fold_id=f"fold-{index:04d}")
        for index in range(5)
    )


# ---------------------------------------------------------------------------
# Plan steps: precedence tests
# ---------------------------------------------------------------------------


def test_failure_is_terminal_and_has_no_conclusion(policy, passing_metrics):
    value = evaluate_stability(
        policy=policy,
        scenario_metrics=passing_metrics,
        outcomes=executed_outcomes(5),
        integrity_failures=("DATA_GAP",),
    )
    assert value.research_status == "FAILED"
    assert value.stability_conclusion is None


def test_less_than_five_valid_folds_is_inconclusive(policy, passing_metrics):
    value = evaluate_stability(
        policy=policy,
        scenario_metrics=passing_metrics[:4],
        outcomes=executed_outcomes(4),
        integrity_failures=(),
    )
    assert value.stability_conclusion == "INCONCLUSIVE"
    assert value.research_status == RunStatus.COMPLETED


# ---------------------------------------------------------------------------
# Adversarial scenario tests
# ---------------------------------------------------------------------------


def test_one_bad_locked_cost_scenario_makes_result_unstable(policy):
    value = evaluate_stability(
        policy=policy,
        scenario_metrics=(
            passing_scenario("zero_cost", fold_id="fold-0000"),
            failing_scenario("full_cost", fold_id="fold-0000"),
        ),
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    assert value.stability_conclusion == "UNSTABLE"


def test_missing_policy_hash_cannot_validate_formal_result(policy):
    evaluation = evaluate_stability(
        policy=policy,
        declared_scenarios=("full_cost",),
        scenario_metrics=tuple(
            passing_scenario("full_cost", fold_id=f"fold-{index:04d}")
            for index in range(5)
        ),
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    assert evaluation.stability_conclusion == "STABLE"
    payload = evaluation.model_dump(mode="json")
    payload["stability_policy_hash"] = None
    with pytest.raises(ValidationError):
        StabilityEvaluation.model_validate(payload)


# ---------------------------------------------------------------------------
# Precedence details and threshold arithmetic
# ---------------------------------------------------------------------------


def test_failed_preflight_outcome_is_terminal(policy, passing_metrics):
    outcomes = executed_outcomes(5) + (
        FoldOutcome(
            fold_id="fold-bad", status="failed_preflight", reason_code="WARMUP_SHORT"
        ),
    )
    value = evaluate_stability(
        policy=policy,
        scenario_metrics=passing_metrics,
        outcomes=outcomes,
        integrity_failures=(),
    )
    assert value.research_status == "FAILED"
    assert value.stability_conclusion is None


def test_missing_declared_scenario_is_terminal(policy, passing_metrics):
    value = evaluate_stability(
        policy=policy,
        declared_scenarios=("full_cost", "zero_cost"),
        scenario_metrics=passing_metrics,
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    assert value.research_status == "FAILED"
    assert value.stability_conclusion is None


def test_incomplete_scenario_artifact_is_terminal(policy, passing_metrics):
    incomplete = passing_metrics[0].model_copy(update={"artifact_complete": False})
    value = evaluate_stability(
        policy=policy,
        scenario_metrics=(incomplete, *passing_metrics[1:]),
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    assert value.research_status == "FAILED"
    assert value.stability_conclusion is None


def test_legal_market_skip_is_inconclusive_not_failed(policy, passing_metrics):
    outcomes = executed_outcomes(5) + (
        FoldOutcome(
            fold_id="fold-skip",
            status="skipped_not_tradeable",
            reason_code="MARKET_WIDE_CLOSURE",
        ),
    )
    value = evaluate_stability(
        policy=policy,
        scenario_metrics=passing_metrics,
        outcomes=outcomes,
        integrity_failures=(),
    )
    assert value.research_status == "COMPLETED"
    assert value.stability_conclusion == "INCONCLUSIVE"
    assert "fold-skip" in value.skipped_fold_ids


def test_all_scenarios_passing_is_stable_with_policy_hash(policy, passing_metrics):
    scenarios = tuple(
        passing_scenario(name, fold_id=f"fold-{index:04d}")
        for index in range(5)
        for name in ("full_cost", "zero_cost")
    )
    value = evaluate_stability(
        policy=policy,
        declared_scenarios=("full_cost", "zero_cost"),
        scenario_metrics=scenarios,
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    assert value.research_status == "COMPLETED"
    assert value.stability_conclusion == "STABLE"
    assert value.stability_policy_hash == canonical_sha256(
        policy.model_dump(mode="json")
    )


def test_worst_fold_at_the_floor_fails_the_scenario(policy):
    scenarios = (
        passing_scenario("full_cost", fold_id="fold-0000"),
        ScenarioMetrics(
            fold_id="fold-0001",
            scenario="full_cost",
            fold_calendar_return=-0.10,
        ),
    )
    value = evaluate_stability(
        policy=policy,
        scenario_metrics=scenarios,
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    # strictly greater than -0.10: a fold exactly at the floor fails it
    assert value.stability_conclusion == "UNSTABLE"
    result = value.scenario_results[0]
    assert result.worst_fold_calendar_return == pytest.approx(-0.10)
    assert not result.passed


def test_positive_fold_ratio_threshold_boundary(policy):
    def metrics_with_negatives(count: int) -> tuple:
        rows = []
        for index in range(5):
            calendar_return = -0.05 if index < count else 0.05
            rows.append(
                passing_scenario(
                    "full_cost", fold_id=f"fold-{index:04d}"
                ).model_copy(update={"fold_calendar_return": calendar_return})
            )
        return tuple(rows)

    # 2 of 5 positive = 0.40 < 0.60 -> UNSTABLE; 3 of 5 = 0.60 -> passes ratio
    unstable = evaluate_stability(
        policy=policy,
        scenario_metrics=metrics_with_negatives(3),
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    assert unstable.stability_conclusion == "UNSTABLE"
    stable_ratio = evaluate_stability(
        policy=policy,
        scenario_metrics=metrics_with_negatives(2),
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    assert stable_ratio.stability_conclusion == "STABLE"


def test_scenario_results_carry_threshold_inputs(policy):
    value = evaluate_stability(
        policy=policy,
        declared_scenarios=("full_cost",),
        scenario_metrics=tuple(
            passing_scenario("full_cost", fold_id=f"fold-{index:04d}")
            for index in range(5)
        ),
        outcomes=executed_outcomes(5),
        integrity_failures=(),
    )
    result = value.scenario_results[0]
    assert result.scenario == "full_cost"
    assert result.executed_fold_count == 5
    assert result.positive_fold_count == 5
    assert result.positive_fold_ratio == pytest.approx(1.0)
    assert result.worst_fold_calendar_return == pytest.approx(0.05)
    assert result.passed
    assert result.policy_version == "stability-v1"
