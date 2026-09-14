"""Terminal failure and policy-bound stability evaluation (the only verdict).

``evaluate_stability`` applies the frozen :class:`~stock_quant.research.
walk_forward.policy.StabilityPolicy` in one strict precedence order:

1. **FAILED (terminal, ``stability_conclusion=None``):** any integrity
   failure code, any ``failed_preflight`` fold outcome, any declared cost
   scenario missing from the produced metrics, or any incomplete scenario
   artifact.  A fold/system integrity failure can never be downgraded to an
   evidence judgement, so a FAILED research run can never become INCONCLUSIVE.
2. **COMPLETED / INCONCLUSIVE:** the research process is valid but the
   evidence is insufficient -- fewer executed folds than
   ``minimum_executed_folds``, or any legal ``skipped_not_tradeable`` fold.
   INCONCLUSIVE means exactly that: valid process, insufficient evidence; it
   is never a statement about strategy quality.
3. **COMPLETED / STABLE or UNSTABLE:** every declared cost scenario is
   evaluated independently -- positive-fold ratio at least
   ``minimum_positive_fold_ratio`` *and* worst fold calendar return strictly
   greater than ``worst_fold_calendar_return_floor`` -- and the final
   conclusion is the conjunction over all scenarios.  No scenario can be
   selected as "primary"; a single failing locked scenario yields UNSTABLE.

A non-null conclusion always carries the recomputed
``stability_policy_hash``; the model refuses a formal verdict without it.
"""

from __future__ import annotations

from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, model_validator

from stock_quant.research.models import RunStatus
from stock_quant.research.walk_forward.metrics import ScenarioMetrics
from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    canonical_sha256,
)
from stock_quant.research.walk_forward.schedule import (
    FoldOutcome,
    FoldOutcomeStatus,
)

#: The stability verdict vocabulary.  ``None`` (a null conclusion) belongs
#: only to a FAILED research run.
StabilityConclusion = Literal["STABLE", "UNSTABLE", "INCONCLUSIVE"]


class ScenarioStabilityResult(BaseModel):
    """One declared scenario's inputs, thresholds and verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario: str
    executed_fold_count: int
    positive_fold_count: int
    positive_fold_ratio: float | None
    worst_fold_calendar_return: float | None
    minimum_positive_fold_ratio: float
    worst_fold_calendar_return_floor: float
    policy_version: str
    passed: bool
    reasons: tuple[str, ...] = ()


class StabilityEvaluation(BaseModel):
    """The single source of a walk-forward run's status and conclusion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    research_status: RunStatus
    stability_conclusion: StabilityConclusion | None = None
    #: The recomputed canonical hash of the applied ``StabilityPolicy``;
    #: mandatory whenever ``stability_conclusion`` is non-null, and a formal
    #: conclusion can never be validated without it.
    stability_policy_hash: str | None = None
    stability_policy_version: str | None = None
    integrity_failures: tuple[str, ...] = ()
    skipped_fold_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    scenario_results: tuple[ScenarioStabilityResult, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "StabilityEvaluation":
        if self.stability_conclusion is not None:
            if not (self.stability_policy_hash or "").strip():
                raise ValueError(
                    "a formal stability conclusion requires the recomputed "
                    "stability_policy_hash; an unhashed verdict is never a "
                    "formal result"
                )
            if self.research_status is not RunStatus.COMPLETED:
                raise ValueError(
                    "a stability conclusion can only exist on a COMPLETED "
                    f"research run, got {self.research_status.value}"
                )
        if self.research_status is RunStatus.FAILED and (
            self.stability_conclusion is not None
        ):
            raise ValueError(
                "a FAILED research run has stability_conclusion=null; the "
                "terminal failure can never become INCONCLUSIVE or otherwise "
                "carry a stability verdict"
            )
        return self


def evaluate_stability(
    *,
    policy: StabilityPolicy,
    declared_scenarios: Sequence[str] = (),
    scenario_metrics: Sequence[ScenarioMetrics],
    outcomes: Sequence[FoldOutcome],
    integrity_failures: Sequence[str] = (),
) -> StabilityEvaluation:
    """Decide the research status and stability conclusion (see module docs)."""
    policy_hash = canonical_sha256(policy.model_dump(mode="json"))
    failures = list(dict.fromkeys(integrity_failures))
    reasons: list[str] = []

    # -- 1. Terminal failure: integrity first, never a performance verdict ----
    failed_preflight = sorted(
        outcome.fold_id
        for outcome in outcomes
        if outcome.status is FoldOutcomeStatus.FAILED_PREFLIGHT
    )
    metrics_scenarios = {metric.scenario for metric in scenario_metrics}
    missing_scenarios = [
        scenario for scenario in declared_scenarios if scenario not in metrics_scenarios
    ]
    incomplete = sorted(
        metric.fold_id
        for metric in scenario_metrics
        if not metric.artifact_complete
    )
    if failures or failed_preflight or missing_scenarios or incomplete:
        if failures:
            reasons.append(f"integrity failures: {sorted(set(failures))}")
        if failed_preflight:
            reasons.append(
                f"failed preflight folds: {failed_preflight}"
            )
        if missing_scenarios:
            reasons.append(
                f"declared cost scenarios missing from the metrics: "
                f"{sorted(missing_scenarios)}"
            )
        if incomplete:
            reasons.append(f"incomplete scenario artifacts: {incomplete}")
        return StabilityEvaluation(
            research_status=RunStatus.FAILED,
            stability_conclusion=None,
            stability_policy_hash=None,
            stability_policy_version=policy.policy_version,
            integrity_failures=tuple(sorted(set(failures))),
            reasons=tuple(reasons),
        )

    # -- 2. Valid process, insufficient evidence -------------------------------
    executed_ids = sorted(
        outcome.fold_id
        for outcome in outcomes
        if outcome.status is FoldOutcomeStatus.EXECUTED
    )
    skipped_ids = sorted(
        outcome.fold_id
        for outcome in outcomes
        if outcome.status is FoldOutcomeStatus.SKIPPED_NOT_TRADEABLE
    )
    if skipped_ids:
        reasons.append(
            "legally skipped (not tradeable) folds: "
            f"{skipped_ids}; the evidence set is incomplete"
        )
    if len(executed_ids) < policy.minimum_executed_folds:
        reasons.append(
            f"executed folds {len(executed_ids)} < minimum "
            f"{policy.minimum_executed_folds}"
        )
    if skipped_ids or len(executed_ids) < policy.minimum_executed_folds:
        return StabilityEvaluation(
            research_status=RunStatus.COMPLETED,
            stability_conclusion="INCONCLUSIVE",
            stability_policy_hash=policy_hash,
            stability_policy_version=policy.policy_version,
            skipped_fold_ids=tuple(skipped_ids),
            reasons=tuple(reasons),
        )

    # -- 3. Every declared scenario independently; STABLE is the conjunction --
    scenarios = list(declared_scenarios) or sorted(metrics_scenarios)
    results: list[ScenarioStabilityResult] = []
    for scenario in scenarios:
        folds = [
            metric for metric in scenario_metrics if metric.scenario == scenario
        ]
        returns = [
            float(metric.fold_calendar_return)
            for metric in folds
            if metric.fold_calendar_return is not None
        ]
        positive = sum(calendar_return > 0 for calendar_return in returns)
        ratio = positive / len(returns) if returns else None
        worst = min(returns) if returns else None
        scenario_reasons: list[str] = []
        ratio_passed = (
            ratio is not None and ratio >= policy.minimum_positive_fold_ratio
        )
        floor_passed = (
            worst is not None and worst > policy.worst_fold_calendar_return_floor
        )
        if not ratio_passed:
            scenario_reasons.append(
                f"positive fold ratio {ratio!r} < "
                f"{policy.minimum_positive_fold_ratio}"
            )
        if not floor_passed:
            scenario_reasons.append(
                f"worst fold calendar return {worst!r} <= "
                f"{policy.worst_fold_calendar_return_floor}"
            )
        results.append(
            ScenarioStabilityResult(
                scenario=scenario,
                executed_fold_count=len(folds),
                positive_fold_count=positive,
                positive_fold_ratio=ratio,
                worst_fold_calendar_return=worst,
                minimum_positive_fold_ratio=policy.minimum_positive_fold_ratio,
                worst_fold_calendar_return_floor=(
                    policy.worst_fold_calendar_return_floor
                ),
                policy_version=policy.policy_version,
                passed=ratio_passed and floor_passed,
                reasons=tuple(scenario_reasons),
            )
        )
    all_passed = bool(results) and all(result.passed for result in results)
    if not all_passed:
        reasons.append(
            "unstable scenarios: "
            + ", ".join(
                result.scenario
                for result in results
                if not result.passed
            )
        )
    return StabilityEvaluation(
        research_status=RunStatus.COMPLETED,
        stability_conclusion="STABLE" if all_passed else "UNSTABLE",
        stability_policy_hash=policy_hash,
        stability_policy_version=policy.policy_version,
        skipped_fold_ids=tuple(skipped_ids),
        reasons=tuple(reasons),
        scenario_results=tuple(results),
    )
