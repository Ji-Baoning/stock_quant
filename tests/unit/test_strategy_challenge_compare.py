"""Identity, pairing and conclusion-policy tests for the challenge evaluation.

``assert_comparable_manifests`` is the terminal identity gate (universe
block, schedule, factor-signal, initial equity, rebalance frequency, ordered
cost scenarios, rule names and the challenger stability binding);
``pair_fold_metrics`` demands an exact one-to-one answer of executed
``fold_id + cost_scenario`` keys; ``evaluate_challenge`` applies the frozen
conclusion order -- FAILED (null conclusion), INCONCLUSIVE (insufficient
evidence or undefined metric), UNSTABLE to REJECTED, and all-scenario
conjunction for PROMOTED -- while preserving every failed threshold cell.
All inputs are synthetic, offline and deterministic.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_quant.research.strategy_challenge.compare import (
    ChallengeIntegrityError,
    ScenarioMetricSet,
    assert_comparable_manifests,
    evaluate_challenge,
    merge_threshold_columns,
    pair_fold_metrics,
)
from stock_quant.research.strategy_challenge.models import (
    ChallengeDeclaration,
    StrategyComparisonPolicy,
    canonical_challenge_sha256,
    compute_challenge_id,
    holdout_consumption_key,
)
from stock_quant.research.strategy_challenge.registry import HoldoutConsumption

SCENARIOS = ("zero_cost", "full_cost")
FOLDS = tuple(f"fold-{index}" for index in range(5))


def valid_declaration() -> ChallengeDeclaration:
    """One valid challenge declaration over syntactically valid identities."""
    from datetime import datetime as _dt

    policy = StrategyComparisonPolicy()
    return ChallengeDeclaration.model_validate({
        "identity_scheme_version": "strategy-challenge-v1",
        "strategy_family": "momentum_60d",
        "baseline_experiment_id": "1a" * 32,
        "challenger_strategy_hash": "2b" * 32,
        "fold_schedule_hash": "3c" * 32,
        "universe_definition": {
            "universe_id": "csi300",
            "universe_version": "a" * 64,
            "membership_table_sha256": "b" * 64,
            "evidence_summary_sha256": "c" * 64,
        },
        "comparison_policy": policy.model_dump(mode="json"),
        "comparison_policy_hash": canonical_challenge_sha256(
            policy.model_dump(mode="json")
        ),
        "declared_before_run_at": _dt(2026, 9, 9, tzinfo=timezone.utc)
        .isoformat(),
    })


@pytest.fixture
def declaration() -> ChallengeDeclaration:
    return valid_declaration()


def universe_block(declaration: ChallengeDeclaration) -> dict:
    return declaration.universe_definition.model_dump(mode="json")


def baseline_manifest(declaration: ChallengeDeclaration) -> dict:
    """The challenge identity view of a published equal-weight experiment."""
    return {
        "experiment_id": declaration.baseline_experiment_id,
        "status": "COMPLETED",
        "stability_conclusion": "STABLE",
        "stability_policy_hash": "aa" * 32,
        "data_environment_snapshot_sha256": "dd" * 32,
        "strategy_snapshot_sha256": "11" * 32,
        "experiment_snapshot_sha256": "22" * 32,
        "fold_schedule_sha256": declaration.fold_schedule_hash,
        "universe_definition": universe_block(declaration),
        "factor_signal_hash": "cc" * 32,
        "initial_cash": 1_000_000.0,
        "rebalance_frequency": "weekly",
        "cost_scenarios": list(SCENARIOS),
        "portfolio_rule_name": "top_n_equal_weight",
        "executed_fold_ids": list(FOLDS),
        "skipped_fold_ids": [],
    }


def challenger_manifest(
    declaration: ChallengeDeclaration, *, stability: str = "STABLE"
) -> dict:
    """The challenge identity view of a published buffered experiment."""
    return {
        "experiment_id": "9b" * 32,
        "status": "COMPLETED",
        "stability_conclusion": stability,
        "stability_policy_hash": "bb" * 32,
        "data_environment_snapshot_sha256": "dd" * 32,
        "strategy_snapshot_sha256": declaration.challenger_strategy_hash,
        "experiment_snapshot_sha256": "33" * 32,
        "fold_schedule_sha256": declaration.fold_schedule_hash,
        "universe_definition": universe_block(declaration),
        "factor_signal_hash": "cc" * 32,
        "initial_cash": 1_000_000.0,
        "rebalance_frequency": "weekly",
        "cost_scenarios": list(SCENARIOS),
        "portfolio_rule_name": "buffered_risk_weighted",
        "executed_fold_ids": list(FOLDS),
        "skipped_fold_ids": [],
    }


@pytest.fixture
def manifests(declaration) -> tuple[dict, dict]:
    return baseline_manifest(declaration), challenger_manifest(declaration)


def fold_rows(
    side: str, executed_folds, *, failing_scenario: str | None = None
) -> list[dict]:
    is_challenger = side == "challenger"
    rows = []
    for index, fold in enumerate(executed_folds):
        for scenario in SCENARIOS:
            sharpe_delta_fail = (
                is_challenger and scenario == failing_scenario
            )
            rows.append({
                "fold_id": fold,
                "scenario": scenario,
                "fold_calendar_return": (0.03 if is_challenger else 0.02)
                + 0.001 * index,
                "abs_max_drawdown": 0.08 if is_challenger else 0.10,
                "turnover": 60_000.0 if is_challenger else 100_000.0,
                "explicit_cost_ratio": 0.0008 if is_challenger else 0.001,
                "reject_rate": 0.005 if is_challenger else 0.01,
                "invested_exposure": 0.97 if is_challenger else 0.95,
                "_aggregate_sharpe": (
                    0.2 if sharpe_delta_fail else (0.80 if is_challenger else 0.50)
                ),
                "_aggregate_annualized_return": (
                    0.08 if is_challenger else 0.05
                ),
            })
    return rows


def metrics_frame(side: str, executed_folds, **kwargs) -> pd.DataFrame:
    return pd.DataFrame(fold_rows(side, executed_folds, **kwargs))


def metric_set(side: str, executed_folds=FOLDS, **kwargs) -> ScenarioMetricSet:
    frame = metrics_frame(side, executed_folds, **kwargs)
    aggregates = pd.DataFrame([
        {
            "scenario": scenario,
            "aggregate_sharpe": frame[
                (frame["scenario"] == scenario)
            ]["_aggregate_sharpe"].iloc[0],
            "aggregate_annualized_return": frame[
                (frame["scenario"] == scenario)
            ]["_aggregate_annualized_return"].iloc[0],
        }
        for scenario in SCENARIOS
    ])
    return ScenarioMetricSet(
        folds=frame.drop(columns=["_aggregate_sharpe",
                                  "_aggregate_annualized_return"]),
        aggregates=aggregates,
    )


@pytest.fixture
def complete_metrics():
    return SimpleNamespace(
        baseline=metrics_frame("baseline", FOLDS),
        challenger=metrics_frame("challenger", FOLDS),
    )


def consumption_for(declaration: ChallengeDeclaration) -> HoldoutConsumption:
    return HoldoutConsumption(
        consumption_key=holdout_consumption_key(
            declaration.strategy_family, declaration.fold_schedule_hash
        ),
        challenge_id=compute_challenge_id(declaration),
        strategy_family=declaration.strategy_family,
        fold_schedule_hash=declaration.fold_schedule_hash,
        declaration_sha256=canonical_challenge_sha256(
            declaration.model_dump(mode="json")
        ),
        comparison_policy_hash=declaration.comparison_policy_hash,
        baseline_experiment_id=declaration.baseline_experiment_id,
        challenger_strategy_hash=declaration.challenger_strategy_hash,
        universe_definition=declaration.universe_definition,
        consumed_at=datetime(2026, 9, 9, tzinfo=timezone.utc).isoformat(),
    )


@pytest.fixture
def challenge_inputs(declaration, manifests, complete_metrics):
    def build(
        *,
        executed_folds: int = 5,
        one_failing_scenario: bool = False,
        challenger_stability: str = "STABLE",
        corrupt_pair: bool = False,
        skipped_folds: int = 0,
        undefined_sharpe: bool = False,
    ) -> dict:
        baseline, challenger = deepcopy(manifests)
        folds = FOLDS[:executed_folds]
        skips = [f"skipped-{index}" for index in range(skipped_folds)]
        baseline["executed_fold_ids"] = list(folds)
        challenger["executed_fold_ids"] = list(folds)
        baseline["skipped_fold_ids"] = list(skips)
        challenger["skipped_fold_ids"] = list(skips)
        challenger["stability_conclusion"] = challenger_stability
        baseline_metrics = metric_set("baseline", folds)
        challenger_metrics = metric_set(
            "challenger",
            folds,
            failing_scenario="full_cost" if one_failing_scenario else None,
        )
        if undefined_sharpe:
            challenger_metrics.aggregates.loc[
                challenger_metrics.aggregates["scenario"] == "full_cost",
                "aggregate_sharpe",
            ] = None
        challenger_folds = challenger_metrics.folds
        if corrupt_pair:
            challenger_metrics = SimpleNamespace(
                folds=pd.concat([challenger_folds, challenger_folds.iloc[[-1]]],
                                ignore_index=True),
                aggregates=challenger_metrics.aggregates,
            )
        return {
            "declaration": declaration,
            "consumption": consumption_for(declaration),
            "baseline_manifest": baseline,
            "challenger_manifest": challenger,
            "baseline_metrics": baseline_metrics,
            "challenger_metrics": challenger_metrics,
        }

    return build


# --------------------------------------------------------------------------- #
# Identity matching
# --------------------------------------------------------------------------- #


def test_universe_mismatch_is_terminal_integrity_error(declaration, manifests):
    baseline, challenger = manifests
    challenger["universe_definition"]["evidence_summary_sha256"] = "f" * 64
    with pytest.raises(ChallengeIntegrityError, match="universe_definition"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_manifest_universe_must_equal_declaration(declaration, manifests):
    baseline, challenger = manifests
    challenger["universe_definition"] = universe_block(declaration)
    baseline["universe_definition"] = {
        **universe_block(declaration),
        "universe_version": "0" * 64,
    }
    with pytest.raises(ChallengeIntegrityError, match="universe_definition"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_fold_schedule_mismatch_is_integrity_error(declaration, manifests):
    baseline, challenger = manifests
    challenger["fold_schedule_sha256"] = "e" * 64
    with pytest.raises(ChallengeIntegrityError, match="fold_schedule"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_factor_signal_mismatch_is_integrity_error(declaration, manifests):
    baseline, challenger = manifests
    challenger["factor_signal_hash"] = "e" * 64
    with pytest.raises(ChallengeIntegrityError, match="factor_signal_hash"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_initial_equity_mismatch_is_integrity_error(declaration, manifests):
    baseline, challenger = manifests
    challenger["initial_cash"] = 2_000_000.0
    with pytest.raises(ChallengeIntegrityError, match="initial_cash"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_cost_scenario_order_is_identity(declaration, manifests):
    baseline, challenger = manifests
    challenger["cost_scenarios"] = list(reversed(challenger["cost_scenarios"]))
    with pytest.raises(ChallengeIntegrityError, match="cost_scenarios"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_rule_names_are_enforced(declaration, manifests):
    baseline, challenger = manifests
    challenger["portfolio_rule_name"] = "top_n_equal_weight"
    with pytest.raises(ChallengeIntegrityError, match="buffered_risk_weighted"):
        assert_comparable_manifests(baseline, challenger, declaration)
    challenger["portfolio_rule_name"] = "buffered_risk_weighted"
    baseline["portfolio_rule_name"] = "buffered_risk_weighted"
    with pytest.raises(ChallengeIntegrityError, match="top_n_equal_weight"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_baseline_experiment_id_is_bound(declaration, manifests):
    baseline, challenger = manifests
    baseline["experiment_id"] = "9" * 64
    with pytest.raises(ChallengeIntegrityError, match="baseline_experiment_id"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_challenger_strategy_hash_is_bound(declaration, manifests):
    baseline, challenger = manifests
    challenger["strategy_snapshot_sha256"] = "9" * 64
    with pytest.raises(ChallengeIntegrityError,
                       match="challenger_strategy_hash"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_challenger_conclusion_requires_stability_policy_hash(
    declaration, manifests
):
    baseline, challenger = manifests
    challenger["stability_policy_hash"] = None
    with pytest.raises(ChallengeIntegrityError, match="stability_policy_hash"):
        assert_comparable_manifests(baseline, challenger, declaration)


# --------------------------------------------------------------------------- #
# Pairing
# --------------------------------------------------------------------------- #


def test_missing_fold_scenario_pair_fails(declaration, complete_metrics):
    challenger = complete_metrics.challenger.iloc[:-1]
    with pytest.raises(ChallengeIntegrityError, match="missing pair"):
        pair_fold_metrics(
            complete_metrics.baseline, challenger, declaration.comparison_policy
        )


def test_duplicate_fold_scenario_pair_fails(declaration, complete_metrics):
    challenger = pd.concat(
        [complete_metrics.challenger, complete_metrics.challenger.iloc[[-1]]],
        ignore_index=True,
    )
    with pytest.raises(ChallengeIntegrityError, match="duplicate pair"):
        pair_fold_metrics(
            complete_metrics.baseline, challenger, declaration.comparison_policy
        )


def test_unexecuted_challenger_fold_is_a_missing_pair(declaration,
                                                      complete_metrics):
    challenger_extra = pd.concat(
        [complete_metrics.challenger,
         pd.DataFrame([{
             "fold_id": "fold-extra", "scenario": "zero_cost",
             "fold_calendar_return": 0.0, "abs_max_drawdown": 0.0,
             "turnover": 1.0, "explicit_cost_ratio": 0.0,
             "reject_rate": 0.0, "invested_exposure": 1.0,
         }])],
        ignore_index=True,
    )
    with pytest.raises(ChallengeIntegrityError, match="missing pair"):
        pair_fold_metrics(
            complete_metrics.baseline, challenger_extra,
            declaration.comparison_policy,
        )


def test_paired_frame_carries_values_deltas_thresholds_and_flags(
    declaration, complete_metrics
):
    pairs = pair_fold_metrics(
        complete_metrics.baseline, complete_metrics.challenger,
        declaration.comparison_policy,
    )
    assert len(pairs) == len(FOLDS) * len(SCENARIOS)
    assert list(pairs.columns[:2]) == ["fold_id", "scenario"]
    row = pairs.iloc[0]
    assert row["baseline_turnover"] == 100_000.0
    assert row["challenger_turnover"] == 60_000.0
    assert row["turnover_ratio"] == pytest.approx(0.60)
    assert row["fold_calendar_return_delta"] == pytest.approx(0.01)


def test_merge_threshold_columns_attach_every_policy_metric(
    declaration, challenge_inputs
):
    inputs = challenge_inputs()
    result = evaluate_challenge(**inputs)
    pairs = pair_fold_metrics(
        inputs["baseline_metrics"].folds,
        inputs["challenger_metrics"].folds,
        declaration.comparison_policy,
    )
    merged = merge_threshold_columns(pairs, result)
    for metric in (
        "aggregate_sharpe_delta",
        "aggregate_annualized_return_delta",
        "positive_fold_ratio_delta",
        "worst_fold_calendar_return_delta",
        "median_abs_max_drawdown_delta",
        "median_turnover_ratio",
        "median_explicit_cost_ratio_delta",
        "median_reject_rate_delta",
        "median_invested_exposure",
    ):
        assert f"{metric}_threshold" in merged.columns
        assert f"{metric}_passed" in merged.columns
    assert bool(merged["aggregate_sharpe_delta_passed"].all())


# --------------------------------------------------------------------------- #
# Conclusion policy
# --------------------------------------------------------------------------- #


def test_all_scenarios_must_pass_for_promotion(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(one_failing_scenario=True))
    assert result.conclusion == "REJECTED"
    assert "full_cost" in result.failed_scenarios
    failed_cells = [
        cell.metric
        for scenario in result.scenario_results
        if scenario.scenario == "full_cost"
        for cell in scenario.cells
        if not cell.passed
    ]
    assert "aggregate_sharpe_delta" in failed_cells


def test_four_valid_folds_is_inconclusive(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(executed_folds=4))
    assert result.conclusion == "INCONCLUSIVE_RESEARCH_ONLY"
    assert result.status == "COMPLETED"


def test_nonstable_challenger_cannot_be_promoted(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(challenger_stability="UNSTABLE"))
    assert result.conclusion == "REJECTED"


def test_inconclusive_challenger_keeps_challenge_inconclusive(challenge_inputs):
    result = evaluate_challenge(
        **challenge_inputs(challenger_stability="INCONCLUSIVE")
    )
    assert result.conclusion == "INCONCLUSIVE_RESEARCH_ONLY"


def test_integrity_failure_has_null_comparison_conclusion(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(corrupt_pair=True))
    assert result.status == "FAILED"
    assert result.conclusion is None


def test_legal_market_wide_skip_is_inconclusive(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(skipped_folds=1))
    assert result.conclusion == "INCONCLUSIVE_RESEARCH_ONLY"
    assert result.skipped_fold_ids == ("skipped-0",)


def test_undefined_required_metric_is_inconclusive(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(undefined_sharpe=True))
    assert result.conclusion == "INCONCLUSIVE_RESEARCH_ONLY"
    assert any("aggregate_sharpe" in reason for reason in result.reasons)


def test_default_evidence_promotes_with_every_cell_passed(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs())
    assert result.status == "COMPLETED"
    assert result.conclusion == "PROMOTED"
    assert result.failed_scenarios == ()
    assert result.executed_fold_count == 5
    assert len(result.scenario_results) == len(SCENARIOS)
    for scenario in result.scenario_results:
        assert scenario.passed
        assert all(cell.passed for cell in scenario.cells)
        assert len(scenario.cells) == 9


def test_consumption_binding_mismatch_is_terminal(challenge_inputs,
                                                  declaration):
    inputs = challenge_inputs()
    other = valid_declaration().model_copy(update={
        "declared_before_run_at": datetime(2026, 9, 10, tzinfo=timezone.utc)
    })
    assert other != declaration
    inputs["consumption"] = consumption_for(other)
    result = evaluate_challenge(**inputs)
    assert result.status == "FAILED"
    assert result.conclusion is None
    assert result.error_code == "REGISTRY_BINDING_MISMATCH"


def test_challenger_walk_forward_failure_is_terminal(challenge_inputs):
    inputs = challenge_inputs()
    inputs["challenger_manifest"]["status"] = "FAILED"
    inputs["challenger_manifest"]["stability_conclusion"] = None
    result = evaluate_challenge(**inputs)
    assert result.status == "FAILED"
    assert result.conclusion is None
