"""The complete, self-contained HTML strategy-comparison report.

The renderer turns the immutable challenge inputs (declaration, holdout
consumption), both experiments' identity views, the evaluation result and
the paired fold/scenario metric frame into one offline HTML page.  It is
complete by contract: the declaration time and hash, the full four-field
universe identity, the schedule and policy hashes, the consumption
key/status, both experiment ids and all three snapshot hashes per side,
every fold/scenario pair, every policy metric's threshold and verdict, the
failed items and the nullable conclusion all appear.  The report never
recommends another parameter set -- a rejected challenge ends in a rejection,
never in tuning advice.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Mapping

import pandas as pd
from jinja2 import Environment, FileSystemLoader

from stock_quant.research.strategy_challenge.models import (
    ChallengeDeclaration,
    ChallengeResult,
)
from stock_quant.research.strategy_challenge.registry import HoldoutConsumption

_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[2] / "reporting" / "templates"
)
_TEMPLATE_NAME = "strategy_challenge.html.j2"

#: The policy metrics in their frozen evaluation order.
METRIC_LABELS = (
    ("aggregate_sharpe_delta", "aggregate Sharpe delta"),
    ("aggregate_annualized_return_delta", "annualized return delta"),
    ("positive_fold_ratio_delta", "positive fold ratio delta"),
    ("worst_fold_calendar_return_delta", "worst fold return delta"),
    ("median_abs_max_drawdown_delta", "median |max drawdown| delta"),
    ("median_turnover_ratio", "median turnover ratio"),
    ("median_explicit_cost_ratio_delta", "median explicit cost delta"),
    ("median_reject_rate_delta", "median reject rate delta"),
    ("median_invested_exposure", "median invested exposure"),
)


def render_strategy_challenge_report(
    *,
    declaration: ChallengeDeclaration,
    consumption: HoldoutConsumption,
    result: ChallengeResult,
    baseline_manifest: Mapping,
    challenger_manifest: Mapping,
    pairs: pd.DataFrame,
) -> str:
    """Render the complete offline comparison report to an HTML string."""
    page = {
        "challenge_id": result.challenge_id,
        "status": result.status,
        "conclusion": result.conclusion,
        "strategy_family": declaration.strategy_family,
        "declared_before_run_at": declaration.declared_before_run_at.isoformat(),
        "declaration_sha256": _declaration_sha256(declaration),
        "universe": [
            {"field": field, "value": value}
            for field, value in declaration.universe_definition.model_dump(
                mode="json"
            ).items()
        ],
        "comparison_policy_hash": declaration.comparison_policy_hash,
        "policy": [
            {"name": name, "value": str(value)}
            for name, value in declaration.comparison_policy.model_dump(
                mode="json"
            ).items()
        ],
        "fold_schedule_hash": declaration.fold_schedule_hash,
        "consumption": {
            "key": consumption.consumption_key,
            "status": consumption.status,
            "consumed_at": consumption.consumed_at,
            "challenge_id": consumption.challenge_id,
            "declaration_sha256": consumption.declaration_sha256,
        },
        "baseline": _experiment_view(baseline_manifest),
        "challenger": _experiment_view(challenger_manifest),
        "scenarios": _scenario_views(result),
        "failed_cells": [
            {"scenario": scenario.scenario, "metric": cell.metric}
            for scenario in result.scenario_results
            for cell in scenario.cells
            if not cell.passed
        ],
        "failed_scenarios": list(result.failed_scenarios),
        "executed_fold_count": result.executed_fold_count,
        "pair_count": int(len(pairs)),
        "pair_rows": _pair_rows(pairs),
        "skipped_fold_ids": list(result.skipped_fold_ids),
        "reasons": list(result.reasons),
        "error_code": result.error_code,
    }
    return _environment().get_template(_TEMPLATE_NAME).render(page=page)


def _experiment_view(manifest: Mapping) -> dict:
    return {
        "experiment_id": str(manifest["experiment_id"]),
        "rule": str(manifest["portfolio_rule_name"]),
        "stability_conclusion": manifest["stability_conclusion"],
        "snapshots": [
            {"field": "strategy_snapshot_sha256",
             "value": str(manifest["strategy_snapshot_sha256"])},
            {"field": "experiment_snapshot_sha256",
             "value": str(manifest["experiment_snapshot_sha256"])},
            {"field": "data_environment_snapshot_sha256",
             "value": str(manifest["data_environment_snapshot_sha256"])},
        ],
        "initial_cash": float(manifest["initial_cash"]),
        "rebalance_frequency": str(manifest["rebalance_frequency"]),
        "cost_scenarios": [str(s) for s in manifest["cost_scenarios"]],
    }


def _scenario_views(result: ChallengeResult) -> list[dict]:
    views = []
    for scenario in result.scenario_results:
        cells = []
        for cell in scenario.cells:
            cells.append({
                "metric": cell.metric,
                "baseline": _number(cell.baseline),
                "challenger": _number(cell.challenger),
                "delta": _number(cell.delta),
                "threshold": cell.threshold,
                "passed": cell.passed,
            })
        views.append({
            "scenario": scenario.scenario,
            "passed": scenario.passed,
            "executed_fold_count": scenario.executed_fold_count,
            "cells": cells,
        })
    return views


def _pair_rows(pairs: pd.DataFrame) -> list[dict]:
    rows = []
    for record in pairs.to_dict("records"):
        row = {}
        for key, value in record.items():
            if isinstance(value, float) and math.isnan(value):
                row[key] = None
            elif hasattr(value, "item"):
                row[key] = value.item()
            else:
                row[key] = value
        rows.append(row)
    return rows


def _number(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.6g}"


def _declaration_sha256(declaration: ChallengeDeclaration) -> str:
    from stock_quant.research.strategy_challenge.models import (
        canonical_challenge_sha256,
    )

    return canonical_challenge_sha256(declaration.model_dump(mode="json"))


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
