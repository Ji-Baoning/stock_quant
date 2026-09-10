"""Pure identity matching, fold/scenario pairing and challenge evaluation.

No filesystem, no runtime state: this module compares the *published*
identity views and metric sets of two walk-forward experiments against one
immutable :class:`~stock_quant.research.strategy_challenge.models.
ChallengeDeclaration`.

``assert_comparable_manifests`` is the terminal identity gate: the baseline
must be the declared equal-weight experiment (``top_n_equal_weight``), the
challenger the declared buffered one (``buffered_risk_weighted``) bound to
its stability policy hash, and both sides must match each other *and* the
declaration exactly on the data-environment snapshot, the complete universe
identity block, the fold schedule hash, the factor/strategy input hash
(which deliberately excludes the portfolio-rule hash), the initial equity,
the rebalance frequency and the ordered cost scenarios.  Only the portfolio
construction rule may differ.

``pair_fold_metrics`` demands an exact one-to-one answer of executed
``fold_id + cost_scenario`` keys: any missing or duplicate pair is an
integrity error.  Legal market-wide skips never enter the metric frames, so
a fold skipped by *both* experiments simply produces no pair and is
preserved as evidence insufficiency by the evaluation instead of corrupting
the pairing.

``evaluate_challenge`` applies the frozen conclusion order:

1. registry binding, identity or pairing error, or a challenger Walk-Forward
   ``FAILED`` -> ``FAILED`` with a null conclusion;
2. fewer than five unconsumed executed folds, any legal market-wide skip, an
   undefined required metric, or a challenger Walk-Forward ``INCONCLUSIVE``
   -> ``INCONCLUSIVE_RESEARCH_ONLY``;
3. challenger Walk-Forward ``UNSTABLE`` -> ``REJECTED``;
4. ``STABLE`` plus every threshold cell true in *every* declared cost
   scenario -> ``PROMOTED``; any failed cell -> ``REJECTED`` with every
   failed cell preserved.  No preferred scenario exists.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

from stock_quant.research.strategy_challenge.models import (
    ChallengeDeclaration,
    ChallengeResult,
    MetricCell,
    ScenarioComparisonResult,
    StrategyComparisonPolicy,
    canonical_challenge_sha256,
    compute_challenge_id,
    holdout_consumption_key,
)
from stock_quant.research.strategy_challenge.registry import HoldoutConsumption

#: The only legal baseline and challenger portfolio construction rules.
BASELINE_RULE_NAME = "top_n_equal_weight"
CHALLENGER_RULE_NAME = "buffered_risk_weighted"

#: Required identity keys of the challenge manifest view of one experiment.
MANIFEST_IDENTITY_KEYS = (
    "experiment_id",
    "status",
    "stability_conclusion",
    "stability_policy_hash",
    "data_environment_snapshot_sha256",
    "strategy_snapshot_sha256",
    "fold_schedule_sha256",
    "universe_definition",
    "factor_signal_hash",
    "initial_cash",
    "rebalance_frequency",
    "cost_scenarios",
    "portfolio_rule_name",
    "executed_fold_ids",
    "skipped_fold_ids",
)

#: Per-fold metric columns of a paired metric frame (challenger and baseline).
FOLD_METRIC_COLUMNS = (
    "fold_calendar_return",
    "abs_max_drawdown",
    "turnover",
    "explicit_cost_ratio",
    "reject_rate",
    "invested_exposure",
)

_AGGREGATE_COLUMNS = ("aggregate_sharpe", "aggregate_annualized_return")

_THRESHOLD_METRICS = (
    "aggregate_sharpe_delta",
    "aggregate_annualized_return_delta",
    "positive_fold_ratio_delta",
    "worst_fold_calendar_return_delta",
    "median_abs_max_drawdown_delta",
    "median_turnover_ratio",
    "median_explicit_cost_ratio_delta",
    "median_reject_rate_delta",
    "median_invested_exposure",
)

_PAIR_DELTA_COLUMNS = {
    "fold_calendar_return": "fold_calendar_return_delta",
    "abs_max_drawdown": "abs_max_drawdown_delta",
    "turnover": "turnover_ratio",
    "explicit_cost_ratio": "explicit_cost_ratio_delta",
    "reject_rate": "reject_rate_delta",
    "invested_exposure": "invested_exposure_delta",
}

_ERROR_CODES = {
    "identity": "IDENTITY_MISMATCH",
    "manifest": "MANIFEST_INVALID",
    "pairing": "PAIRING_INCOMPLETE",
    "registry": "REGISTRY_BINDING_MISMATCH",
}


class ChallengeIntegrityError(ValueError):
    """A terminal identity, manifest, pairing or registry-binding error."""


@dataclass(frozen=True)
class ScenarioMetricSet:
    """The paired-ready metric records of one published experiment.

    ``folds`` carries one row per executed ``(fold_id, cost_scenario)`` pair
    with every :data:`FOLD_METRIC_COLUMNS` value (drawdown already absolute);
    ``aggregates`` carries one row per declared scenario with the
    concatenated-OOS ``aggregate_sharpe`` and ``aggregate_annualized_return``.
    """

    folds: pd.DataFrame
    aggregates: pd.DataFrame


# --------------------------------------------------------------------------- #
# Identity matching
# --------------------------------------------------------------------------- #


def assert_comparable_manifests(
    baseline: Mapping,
    challenger: Mapping,
    declaration: ChallengeDeclaration,
) -> None:
    """Raise :class:`ChallengeIntegrityError` unless the pair is comparable.

    See the module docstring for the exact equality set.  The error message
    always names the offending identity field so the FAILED result can point
    at it without leaking any runtime detail.
    """
    for name, manifest in (("baseline", baseline), ("challenger", challenger)):
        if not isinstance(manifest, Mapping):
            raise ChallengeIntegrityError(
                f"the {name} manifest view must be a mapping, got "
                f"{type(manifest).__name__}"
            )
        missing = [key for key in MANIFEST_IDENTITY_KEYS if key not in manifest]
        if missing:
            raise ChallengeIntegrityError(
                f"the {name} manifest view is missing identity keys {missing}"
            )
    if baseline["experiment_id"] != declaration.baseline_experiment_id:
        raise ChallengeIntegrityError(
            "baseline_experiment_id mismatch: the declaration pins "
            f"{declaration.baseline_experiment_id!r} but the published "
            f"baseline is {baseline['experiment_id']!r}"
        )
    if challenger["strategy_snapshot_sha256"] != (
        declaration.challenger_strategy_hash
    ):
        raise ChallengeIntegrityError(
            "challenger_strategy_hash mismatch: the declaration pins "
            f"{declaration.challenger_strategy_hash!r} but the published "
            "challenger strategy snapshot is "
            f"{challenger['strategy_snapshot_sha256']!r}"
        )
    if baseline["portfolio_rule_name"] != BASELINE_RULE_NAME:
        raise ChallengeIntegrityError(
            "the baseline must be the "
            f"{BASELINE_RULE_NAME} rule, got "
            f"{baseline['portfolio_rule_name']!r}; only portfolio "
            "construction rules may differ inside a challenge"
        )
    if challenger["portfolio_rule_name"] != CHALLENGER_RULE_NAME:
        raise ChallengeIntegrityError(
            f"the challenger must be the {CHALLENGER_RULE_NAME} rule, got "
            f"{challenger['portfolio_rule_name']!r}; only portfolio "
            "construction rules may differ inside a challenge"
        )
    _assert_conclusion_binding(challenger)
    equality_fields = (
        "data_environment_snapshot_sha256",
        "universe_definition",
        "fold_schedule_sha256",
        "factor_signal_hash",
        "initial_cash",
        "rebalance_frequency",
        "cost_scenarios",
    )
    for field in equality_fields:
        if baseline[field] != challenger[field]:
            raise ChallengeIntegrityError(
                f"{field} differs between baseline and challenger "
                f"({baseline[field]!r} != {challenger[field]!r}); a one-time "
                "comparison requires identical inputs except the portfolio "
                "construction rule"
            )
        if field == "universe_definition":
            declared = declaration.universe_definition.model_dump(mode="json")
            if baseline[field] != declared:
                raise ChallengeIntegrityError(
                    f"universe_definition differs from the declaration "
                    f"({baseline[field]!r} != {declared!r})"
                )
        if field == "fold_schedule_sha256":
            if baseline[field] != declaration.fold_schedule_hash:
                raise ChallengeIntegrityError(
                    "fold_schedule_sha256 differs from the declaration "
                    f"({baseline[field]!r} != "
                    f"{declaration.fold_schedule_hash!r})"
                )


def _assert_conclusion_binding(challenger: Mapping) -> None:
    """A formal challenger verdict must carry its stability policy hash."""
    conclusion = challenger["stability_conclusion"]
    if conclusion is None:
        return
    if not isinstance(conclusion, str) or conclusion not in (
        "STABLE",
        "UNSTABLE",
        "INCONCLUSIVE",
    ):
        raise ChallengeIntegrityError(
            f"challenger stability_conclusion {conclusion!r} is outside the "
            "walk-forward verdict vocabulary"
        )
    policy_hash = challenger["stability_policy_hash"]
    if (
        not isinstance(policy_hash, str)
        or len(policy_hash) != 64
        or any(character not in "0123456789abcdef" for character in policy_hash)
    ):
        raise ChallengeIntegrityError(
            "the challenger stability conclusion must be bound to its "
            f"stability_policy_hash, got {policy_hash!r}; an unhashed "
            "verdict is never a formal result"
        )


# --------------------------------------------------------------------------- #
# Pairing
# --------------------------------------------------------------------------- #


def pair_fold_metrics(
    baseline: pd.DataFrame,
    challenger: pd.DataFrame,
    policy: StrategyComparisonPolicy,
) -> pd.DataFrame:
    """Pair executed ``fold_id + cost_scenario`` rows one-to-one.

    Missing or duplicate pairs are integrity errors.  The returned frame
    carries the identity keys, both sides' values and a delta (or the
    baseline-relative ratio for turnover) per policy metric; legal
    market-wide skips never appear here because metric frames only ever
    contain executed folds.
    """
    del policy  # the pairing is threshold-free; thresholds join later
    base_keys = _pair_keys(baseline, "baseline")
    challenger_keys = _pair_keys(challenger, "challenger")
    missing = sorted(set(base_keys) - set(challenger_keys))
    extra = sorted(set(challenger_keys) - set(base_keys))
    if missing or extra:
        details = []
        if missing:
            details.append(
                f"missing pair(s) in the challenger metric frame: {missing}"
            )
        if extra:
            details.append(
                f"missing pair(s) in the baseline metric frame: {extra}"
            )
        raise ChallengeIntegrityError(
            "baseline and challenger fold/scenario pairs differ: "
            + "; ".join(details)
        )
    base_rows = baseline.set_index(["fold_id", "scenario"]).sort_index()
    challenger_rows = challenger.set_index(["fold_id", "scenario"]).sort_index()
    records = []
    for key in base_rows.index:
        base_row = base_rows.loc[key]
        challenger_row = challenger_rows.loc[key]
        record = {"fold_id": key[0], "scenario": key[1]}
        for metric in FOLD_METRIC_COLUMNS:
            record[f"baseline_{metric}"] = _finite_or_none(base_row[metric])
            record[f"challenger_{metric}"] = _finite_or_none(
                challenger_row[metric]
            )
        for metric, delta_column in _PAIR_DELTA_COLUMNS.items():
            base_value = record[f"baseline_{metric}"]
            challenger_value = record[f"challenger_{metric}"]
            if base_value is None or challenger_value is None:
                record[delta_column] = None
            elif metric == "turnover":
                record[delta_column] = (
                    challenger_value / base_value if base_value != 0 else None
                )
            else:
                record[delta_column] = challenger_value - base_value
        records.append(record)
    return pd.DataFrame(records)


def merge_threshold_columns(
    pairs: pd.DataFrame, result: "object"
) -> pd.DataFrame:
    """Attach each policy metric's value, threshold and flag per scenario.

    The audit Parquet must carry, for every policy metric, the scenario-level
    computed value beside its frozen threshold expression and its pass flag,
    so no hidden failed threshold can exist in the published evidence.
    """
    if not isinstance(result, ChallengeResult):
        raise TypeError(
            "merge_threshold_columns expects a ChallengeResult, got "
            f"{type(result).__name__}"
        )
    merged = pairs.copy()
    for scenario in result.scenario_results:
        mask = merged["scenario"] == scenario.scenario
        for cell in scenario.cells:
            merged.loc[mask, cell.metric] = cell.delta
            merged.loc[mask, f"{cell.metric}_threshold"] = cell.threshold
            merged.loc[mask, f"{cell.metric}_passed"] = cell.passed
    return merged


def _pair_keys(frame: pd.DataFrame, side: str) -> list[tuple[str, str]]:
    _require_frame(frame, side)
    keys = [
        (str(row["fold_id"]), str(row["scenario"]))
        for row in frame.to_dict("records")
    ]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ChallengeIntegrityError(
            f"duplicate pair(s) in the {side} metric frame: {duplicates}"
        )
    return keys


def _require_frame(frame: pd.DataFrame, side: str) -> None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ChallengeIntegrityError(
            f"the {side} metric frame is empty; no executed fold pair exists"
        )
    required = ["fold_id", "scenario", *FOLD_METRIC_COLUMNS]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ChallengeIntegrityError(
            f"the {side} metric frame is missing columns {missing}"
        )


def _require_metric_set(metrics: ScenarioMetricSet, side: str,
                        declared_scenarios: Sequence[str]) -> None:
    if not isinstance(metrics, ScenarioMetricSet):
        raise ChallengeIntegrityError(
            f"the {side} metrics must be a ScenarioMetricSet, got "
            f"{type(metrics).__name__}"
        )
    _require_frame(metrics.folds, side)
    aggregates = metrics.aggregates
    required = ["scenario", *_AGGREGATE_COLUMNS]
    missing = [column for column in required if column not in aggregates.columns]
    if missing:
        raise ChallengeIntegrityError(
            f"the {side} aggregate frame is missing columns {missing}"
        )
    keys = [str(value) for value in aggregates["scenario"]]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ChallengeIntegrityError(
            f"duplicate aggregate row(s) for scenarios {duplicates}"
        )
    absent = sorted(set(declared_scenarios) - set(keys))
    if absent:
        raise ChallengeIntegrityError(
            f"the {side} aggregate frame lacks declared scenario(s) {absent}"
        )


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def evaluate_challenge(
    *,
    declaration: ChallengeDeclaration,
    consumption: HoldoutConsumption,
    baseline_manifest: Mapping,
    challenger_manifest: Mapping,
    baseline_metrics: ScenarioMetricSet,
    challenger_metrics: ScenarioMetricSet,
) -> "ChallengeResult":
    """Evaluate one consumed challenge in the frozen conclusion order."""
    challenge_id = compute_challenge_id(declaration)
    header = {
        "challenge_id": challenge_id,
        "strategy_family": declaration.strategy_family,
        "baseline_experiment_id": str(baseline_manifest.get("experiment_id")),
        "challenger_experiment_id": str(
            challenger_manifest.get("experiment_id")
        ),
        "challenger_stability_conclusion": _text_or_none(
            challenger_manifest.get("stability_conclusion")
        ),
        "comparison_policy_hash": declaration.comparison_policy_hash,
    }

    def failed(code: str, reason: str) -> ChallengeResult:
        return ChallengeResult.model_validate(
            header | {"status": "FAILED", "conclusion": None,
                      "error_code": code, "reasons": (reason,)}
        )

    try:
        _assert_consumption_binding(declaration, consumption, challenge_id)
    except ChallengeIntegrityError as error:
        return failed(_ERROR_CODES["registry"], str(error))
    try:
        assert_comparable_manifests(
            baseline_manifest, challenger_manifest, declaration
        )
    except ChallengeIntegrityError as error:
        return failed(_ERROR_CODES["identity"], str(error))
    declared_scenarios = list(baseline_manifest["cost_scenarios"])
    try:
        _require_metric_set(baseline_metrics, "baseline", declared_scenarios)
        _require_metric_set(challenger_metrics, "challenger",
                            declared_scenarios)
        pairs = pair_fold_metrics(
            baseline_metrics.folds, challenger_metrics.folds,
            declaration.comparison_policy,
        )
        _assert_pairs_match_executed_folds(
            pairs, baseline_manifest, challenger_manifest
        )
    except ChallengeIntegrityError as error:
        return failed(_ERROR_CODES["pairing"], str(error))

    header = header | {
        "executed_fold_count": int(pairs["fold_id"].nunique()),
        "declared_scenario_count": len(declared_scenarios),
        "skipped_fold_ids": tuple(
            sorted(
                set(baseline_manifest["skipped_fold_ids"])
                | set(challenger_manifest["skipped_fold_ids"])
            )
        ),
    }

    # -- 1. A challenger walk-forward FAILED run is terminal ----------------
    challenger_status = challenger_manifest["status"]
    if challenger_status == "FAILED":
        return failed(
            _ERROR_CODES["manifest"],
            "the challenger walk-forward run is FAILED; a failed run can "
            "never carry a comparison verdict",
        )
    if challenger_status != "COMPLETED":
        return failed(
            _ERROR_CODES["manifest"],
            f"challenger research status {challenger_status!r} is outside "
            "the walk-forward status vocabulary",
        )
    challenger_conclusion = challenger_manifest["stability_conclusion"]
    if challenger_conclusion is None:
        return failed(
            _ERROR_CODES["manifest"],
            "the completed challenger walk-forward run carries no stability "
            "conclusion; the comparison has no stability binding",
        )

    # -- 2. Evaluate every threshold cell (preserved in every outcome) ------
    policy = declaration.comparison_policy
    scenario_results, undefined = _evaluate_scenario_cells(
        policy=policy,
        pairs=pairs,
        baseline_aggregates=_aggregates_by_scenario(baseline_metrics),
        challenger_aggregates=_aggregates_by_scenario(challenger_metrics),
        declared_scenarios=declared_scenarios,
    )

    # -- 3. Insufficiency first: folds, legal skips, undefined metrics ------
    insufficient: list[str] = []
    if header["executed_fold_count"] < policy.minimum_unconsumed_executed_folds:
        insufficient.append(
            f"executed folds {header['executed_fold_count']} < "
            f"{policy.minimum_unconsumed_executed_folds} unconsumed folds"
        )
    if header["skipped_fold_ids"]:
        insufficient.append(
            "legal market-wide skipped fold(s) "
            f"{list(header['skipped_fold_ids'])}: the evidence set is "
            "incomplete"
        )
    if undefined:
        insufficient.extend(undefined)
    if insufficient or challenger_conclusion == "INCONCLUSIVE":
        if challenger_conclusion == "INCONCLUSIVE":
            insufficient.insert(
                0,
                "the challenger walk-forward conclusion is INCONCLUSIVE",
            )
        return ChallengeResult.model_validate(
            header | {
                "status": "COMPLETED",
                "conclusion": "INCONCLUSIVE_RESEARCH_ONLY",
                "scenario_results": tuple(scenario_results),
                "reasons": tuple(insufficient),
            }
        )

    # -- 4. Challenger UNSTABLE is a rejection, never a promotion -----------
    failed_scenarios = tuple(
        scenario.scenario for scenario in scenario_results if not scenario.passed
    )
    reasons: list[str] = []
    if challenger_conclusion == "UNSTABLE":
        reasons.append(
            "the challenger walk-forward conclusion is UNSTABLE under its "
            f"frozen stability policy ({challenger_manifest['stability_policy_hash']})"
        )
    if failed_scenarios:
        reasons.append(
            "failed threshold cell(s) in scenario(s) " + ", ".join(failed_scenarios)
        )
    conclusion = (
        "PROMOTED"
        if challenger_conclusion == "STABLE" and not failed_scenarios
        else "REJECTED"
    )
    return ChallengeResult.model_validate(
        header | {
            "status": "COMPLETED",
            "conclusion": conclusion,
            "scenario_results": tuple(scenario_results),
            "failed_scenarios": failed_scenarios,
            "reasons": tuple(reasons),
        }
    )


def _assert_consumption_binding(
    declaration: ChallengeDeclaration,
    consumption: HoldoutConsumption,
    challenge_id: str,
) -> None:
    """The consumption record must be exactly this declaration's own."""
    if not isinstance(consumption, HoldoutConsumption):
        raise ChallengeIntegrityError(
            "the holdout consumption must be a HoldoutConsumption record, "
            f"got {type(consumption).__name__}"
        )
    expected = {
        "consumption_key": holdout_consumption_key(
            declaration.strategy_family, declaration.fold_schedule_hash
        ),
        "challenge_id": challenge_id,
        "strategy_family": declaration.strategy_family,
        "fold_schedule_hash": declaration.fold_schedule_hash,
        "declaration_sha256": canonical_challenge_sha256(
            declaration.model_dump(mode="json")
        ),
        "comparison_policy_hash": declaration.comparison_policy_hash,
        "baseline_experiment_id": declaration.baseline_experiment_id,
        "challenger_strategy_hash": declaration.challenger_strategy_hash,
    }
    mismatches = sorted(
        field for field, value in expected.items()
        if getattr(consumption, field) != value
    )
    if consumption.universe_definition != declaration.universe_definition:
        mismatches.append("universe_definition")
    if consumption.status != "consumed":
        mismatches.append("status")
    if mismatches:
        raise ChallengeIntegrityError(
            "the holdout consumption record disagrees with the declaration "
            f"on {mismatches}; the challenge is not the registered consumer "
            "of this holdout"
        )


def _assert_pairs_match_executed_folds(
    pairs: pd.DataFrame,
    baseline_manifest: Mapping,
    challenger_manifest: Mapping,
) -> None:
    executed_baseline = set(map(str, baseline_manifest["executed_fold_ids"]))
    executed_challenger = set(
        map(str, challenger_manifest["executed_fold_ids"])
    )
    paired = set(map(str, pairs["fold_id"].unique()))
    for name, executed in (
        ("baseline", executed_baseline),
        ("challenger", executed_challenger),
    ):
        if paired != executed:
            missing = sorted(executed - paired)
            unexpected = sorted(paired - executed)
            raise ChallengeIntegrityError(
                f"paired folds do not match the {name} executed folds "
                f"(missing {missing}, unexpected {unexpected}); only "
                "executed folds may pair"
            )


def _aggregates_by_scenario(
    metrics: ScenarioMetricSet,
) -> dict[str, dict[str, float | None]]:
    return {
        str(row["scenario"]): {
            "aggregate_sharpe": _finite_or_none(row["aggregate_sharpe"]),
            "aggregate_annualized_return": _finite_or_none(
                row["aggregate_annualized_return"]
            ),
        }
        for row in metrics.aggregates.to_dict("records")
    }


def _evaluate_scenario_cells(
    *,
    policy: StrategyComparisonPolicy,
    pairs: pd.DataFrame,
    baseline_aggregates: Mapping[str, Mapping[str, float | None]],
    challenger_aggregates: Mapping[str, Mapping[str, float | None]],
    declared_scenarios: Sequence[str],
) -> tuple[list[ScenarioComparisonResult], list[str]]:
    """Every declared scenario's nine threshold cells (never a subset)."""
    results: list[ScenarioComparisonResult] = []
    undefined: list[str] = []
    for scenario in declared_scenarios:
        scenario_pairs = pairs[pairs["scenario"] == scenario]
        executed = len(scenario_pairs)
        cells: list[MetricCell] = []

        def cell(metric: str, baseline_value, challenger_value, delta,
                 threshold: str, passed: bool | None) -> None:
            cells.append(
                MetricCell(
                    metric=metric,
                    baseline=_finite_or_none(baseline_value),
                    challenger=_finite_or_none(challenger_value),
                    delta=_finite_or_none(delta),
                    threshold=threshold,
                    passed=bool(passed),
                )
            )

        def undefined_metric(metric: str) -> None:
            undefined.append(
                f"scenario {scenario!r}: required metric {metric} is "
                "undefined for this evidence set"
            )

        # -- aggregate-level cells -----------------------------------------
        base_aggregate = baseline_aggregates[scenario]
        challenger_aggregate = challenger_aggregates[scenario]
        for metric, floor_field in (
            ("aggregate_sharpe", "aggregate_sharpe_delta_floor"),
            ("aggregate_annualized_return",
             "aggregate_annualized_return_delta_floor"),
        ):
            base_value = base_aggregate[metric]
            challenger_value = challenger_aggregate[metric]
            floor = getattr(policy, floor_field)
            delta = (
                None
                if base_value is None or challenger_value is None
                else challenger_value - base_value
            )
            cell_name = f"{metric}_delta"
            if delta is None:
                cell(cell_name, base_value, challenger_value, delta,
                     f">= {floor}", False)
                undefined_metric(cell_name)
                continue
            cell(
                cell_name, base_value, challenger_value, delta,
                f">= {floor}", delta >= float(floor),
            )
        ratio_pairs = (
            ("positive_fold_ratio_delta", "positive_fold_ratio",
             "fold_calendar_return", policy.positive_fold_ratio_delta_floor),
            ("worst_fold_calendar_return_delta", "worst_fold_calendar_return",
             "fold_calendar_return",
             policy.worst_fold_calendar_return_delta_floor),
        )
        for metric, statistic_name, source, floor in ratio_pairs:
            base_values = _defined(scenario_pairs[f"baseline_{source}"])
            challenger_values = _defined(
                scenario_pairs[f"challenger_{source}"]
            )
            base_stat = _statistic(statistic_name, base_values)
            challenger_stat = _statistic(statistic_name, challenger_values)
            delta = (
                None
                if base_stat is None or challenger_stat is None
                else challenger_stat - base_stat
            )
            if delta is None:
                cell(metric, base_stat, challenger_stat, delta,
                     f">= {floor}", False)
                undefined_metric(metric)
                continue
            cell(metric, base_stat, challenger_stat, delta,
                 f">= {floor}", delta >= float(floor))

        # -- median-level cells ---------------------------------------------
        median_cells = (
            ("median_abs_max_drawdown_delta", "abs_max_drawdown",
             policy.median_abs_max_drawdown_delta_ceiling, "ceiling"),
            ("median_explicit_cost_ratio_delta", "explicit_cost_ratio",
             policy.median_explicit_cost_ratio_delta_ceiling, "ceiling"),
            ("median_reject_rate_delta", "reject_rate",
             policy.median_reject_rate_delta_ceiling, "ceiling"),
        )
        for metric, source, threshold, direction in median_cells:
            base_stat = _median(_defined(scenario_pairs[f"baseline_{source}"]))
            challenger_stat = _median(
                _defined(scenario_pairs[f"challenger_{source}"])
            )
            delta = (
                None
                if base_stat is None or challenger_stat is None
                else challenger_stat - base_stat
            )
            if delta is None:
                cell(metric, base_stat, challenger_stat, delta,
                     f"<= {threshold}", False)
                undefined_metric(metric)
                continue
            cell(metric, base_stat, challenger_stat, delta,
                 f"<= {threshold}", delta <= float(threshold))

        base_turnover = _median(_defined(scenario_pairs["baseline_turnover"]))
        challenger_turnover = _median(
            _defined(scenario_pairs["challenger_turnover"])
        )
        ratio = (
            None
            if not base_turnover or challenger_turnover is None
            else challenger_turnover / base_turnover
        )
        if ratio is None:
            cell("median_turnover_ratio", base_turnover, challenger_turnover,
                 None, f"<= {policy.median_turnover_ratio_ceiling}", False)
            undefined_metric("median_turnover_ratio (zero baseline turnover)")
        else:
            cell("median_turnover_ratio", base_turnover, challenger_turnover,
                 ratio, f"<= {policy.median_turnover_ratio_ceiling}",
                 ratio <= float(policy.median_turnover_ratio_ceiling))

        challenger_exposure = _median(
            _defined(scenario_pairs["challenger_invested_exposure"])
        )
        if challenger_exposure is None:
            cell("median_invested_exposure", None, None, None,
                 f">= {policy.median_invested_exposure_floor}", False)
            undefined_metric("median_invested_exposure")
        else:
            cell("median_invested_exposure", None, challenger_exposure,
                 challenger_exposure,
                 f">= {policy.median_invested_exposure_floor}",
                 challenger_exposure
                 >= float(policy.median_invested_exposure_floor))

        results.append(
            ScenarioComparisonResult(
                scenario=scenario,
                executed_fold_count=executed,
                passed=all(cell.passed for cell in cells),
                cells=tuple(cells),
            )
        )
    return results, undefined


# --------------------------------------------------------------------------- #
# Small numeric helpers
# --------------------------------------------------------------------------- #


def _defined(column: pd.Series) -> list[float]:
    values = []
    for value in column:
        number = _finite_or_none(value)
        if number is not None:
            values.append(number)
    return values


def _finite_or_none(value: object) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _statistic(name: str, values: Sequence[float]) -> float | None:
    if not values:
        return None
    if name == "positive_fold_ratio":
        return sum(value > 0 for value in values) / len(values)
    if name == "worst_fold_calendar_return":
        return min(values)
    raise ValueError(f"unknown statistic {name!r}")


def _text_or_none(value: object) -> str | None:
    return None if value is None else str(value)
