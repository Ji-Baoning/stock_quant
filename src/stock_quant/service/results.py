"""Read-only aggregation over an experiment's published JSON artifacts (v3 §7.2).

The service never recomputes anything: it reads the files a published
experiment actually carries and reports absent artifacts as ``null``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
import yaml
from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel

from stock_quant.service.datasets import read_json_or_fail
from stock_quant.service.errors import (
    ArtifactNotFound,
    ExperimentManifestUnreadable,
    FoldNotFound,
    ResultsNotFound,
    UnknownExperiment,
)

_EXPERIMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ExperimentResultsResponse(BaseModel):
    experiment_id: str
    manifest: dict[str, Any]
    metrics: dict[str, Any] | None = None
    stability_report: dict[str, Any] | None = None


_EQUITY_COLUMNS = (
    "trade_date",
    "initial_equity",
    "cash",
    "market_value",
    "net_equity_after_cost",
)


class FoldEquityResponse(BaseModel):
    experiment_id: str
    fold_id: str
    scenario: str | None
    rows: list[dict[str, Any]]


def _experiment_dir(request: Request, experiment_id: str) -> Path:
    root = (Path(request.app.state.project_root) / "data" / "experiments").resolve()
    directory = (root / experiment_id).resolve()
    if directory.parent != root or not directory.is_dir():
        raise UnknownExperiment(f"no published experiment {experiment_id!r}")
    return directory


def _read_optional_json(directory: Path, name: str) -> dict[str, Any] | None:
    """Read one published JSON artifact; ``None`` when it was never published.

    A file that exists but cannot be read is *not* "absent": it fails closed
    with the same error envelope the manifest read uses, so a corrupt
    artifact never silently degrades into a "missing" claim in the UI.
    """
    path = directory / name
    if not path.is_file():
        return None
    payload = read_json_or_fail(path, ExperimentManifestUnreadable, name)
    return payload if isinstance(payload, dict) else None


router = APIRouter(prefix="/api/v1", tags=["experiment-results"])


class AggregateRow(BaseModel):
    """One ``walk_forward.scenario_aggregates`` entry, verbatim -- the fields
    mirror ``AggregateOOSMetrics`` plus the scenario name; nothing is
    recomputed."""

    scenario: str
    aggregate_return: float | None = None
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    sharpe_zero_rf: float | None = None
    oos_return_observations: int | None = None
    annualization_observations: int | None = None


class DisplayExtremes(BaseModel):
    """Presentation-level extremes over one scenario's per-fold metrics --
    max/mean of already-published atomic values, never a recomputation."""

    max_per_fold_drawdown: float | None = None
    max_reject_rate: float | None = None
    mean_turnover: float | None = None


class ExperimentSummaryRow(BaseModel):
    experiment_id: str
    status: str | None = None
    dataset_version: str | None = None
    universe_version: str | None = None
    evaluation_reason: str | None = None
    hypothesis: str | None = None
    stability_conclusion: str | None = None
    stability_policy_hash: str | None = None
    research_status: str | None = None
    canonical_scenario: str | None = None
    aggregates: list[AggregateRow] | None = None
    display_extremes: DisplayExtremes | None = None
    run_started_at: str | None = None


class ExperimentSummariesResponse(BaseModel):
    summaries: list[ExperimentSummaryRow]


_AGGREGATE_FIELDS = (
    "aggregate_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe_zero_rf",
    "oos_return_observations",
    "annualization_observations",
)


def _hypothesis_of(directory: Path) -> str | None:
    """The ``hypothesis`` key of the published ``experiment_spec.yml``.

    A missing file, an unparseable one, or a missing/blank key all read as
    ``None``: a legacy experiment simply carries no hypothesis row.
    """
    path = directory / "experiment_spec.yml"
    if not path.is_file():
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("hypothesis")
    return str(value) if isinstance(value, str) and value.strip() else None


def _run_started_at(directory: Path) -> str | None:
    """The ``started_at`` string of the published ``run_manifest.json``.

    Tolerant by contract: the artifacts published today carry only
    ``{run_id, status}``, so the absent file or the absent key both read as
    ``None`` -- a future artifact that adds the field flows through verbatim
    (string passthrough, never parsed or reformatted).
    """
    run_manifest = _read_optional_json(directory, "run_manifest.json")
    if run_manifest is None:
        return None
    value = run_manifest.get("started_at")
    return value if isinstance(value, str) else None


def _display_extremes(directory: Path, canonical: str | None) -> DisplayExtremes | None:
    """Display-level max/mean over the canonical scenario's ``fold_metrics``
    rows of the published stability report (max per-fold drawdown, max
    reject rate, mean turnover); no report or no matching rows -> ``None``."""
    if canonical is None:
        return None
    report = _read_optional_json(directory, "stability_report.json")
    if report is None:
        return None
    records = [
        record
        for record in report.get("fold_metrics", [])
        if record.get("scenario") == canonical
    ]
    if not records:
        return None
    drawdowns = [
        record["per_fold_max_drawdown"]
        for record in records
        if record.get("per_fold_max_drawdown") is not None
    ]
    rejects = [
        record["reject_rate"]
        for record in records
        if record.get("reject_rate") is not None
    ]
    turnovers = [
        record["turnover"] for record in records if record.get("turnover") is not None
    ]
    return DisplayExtremes(
        max_per_fold_drawdown=max(drawdowns, default=None),
        max_reject_rate=max(rejects, default=None),
        mean_turnover=(sum(turnovers) / len(turnovers)) if turnovers else None,
    )


@router.get("/experiments/summaries", response_model=ExperimentSummariesResponse)
def experiment_summaries(request: Request) -> ExperimentSummariesResponse:
    """One list-level call: every published experiment as a summary row.

    Declared before the ``/{experiment_id}/...`` routes so ``summaries`` is
    never captured as an experiment id. Each row reads only what the
    experiment actually published; absent artifacts are ``null``.
    """
    root = Path(request.app.state.project_root) / "data" / "experiments"
    rows: list[ExperimentSummaryRow] = []
    children = sorted(root.iterdir()) if root.is_dir() else []
    for child in children:
        if not child.is_dir() or not _EXPERIMENT_ID_RE.fullmatch(child.name):
            continue
        manifest = _read_optional_json(child, "experiment_manifest.json") or {}
        metrics = _read_optional_json(child, "metrics.json")
        walk_forward = (metrics or {}).get("walk_forward") or {}
        canonical = _canonical_scenario(child, manifest)
        aggregates = [
            AggregateRow(
                scenario=str(entry.get("scenario")),
                **{key: entry.get(key) for key in _AGGREGATE_FIELDS},
            )
            for entry in walk_forward.get("scenario_aggregates", [])
            if isinstance(entry, dict)
        ] or None
        rows.append(
            ExperimentSummaryRow(
                experiment_id=child.name,
                status=manifest.get("status"),
                dataset_version=manifest.get("dataset_version"),
                universe_version=manifest.get("universe_version"),
                evaluation_reason=manifest.get("evaluation_reason"),
                hypothesis=_hypothesis_of(child),
                stability_conclusion=walk_forward.get("stability_conclusion"),
                stability_policy_hash=walk_forward.get("stability_policy_hash"),
                research_status=walk_forward.get("research_status"),
                canonical_scenario=canonical,
                aggregates=aggregates,
                display_extremes=_display_extremes(child, canonical),
                run_started_at=_run_started_at(child),
            )
        )
    return ExperimentSummariesResponse(summaries=rows)


@router.get(
    "/experiments/{experiment_id}/results",
    response_model=ExperimentResultsResponse,
)
def experiment_results(
    experiment_id: Annotated[str, PathParam(pattern=_EXPERIMENT_ID_RE.pattern)],
    request: Request,
) -> ExperimentResultsResponse:
    directory = _experiment_dir(request, experiment_id)
    manifest = _read_optional_json(directory, "experiment_manifest.json")
    if manifest is None:
        raise ResultsNotFound(
            f"experiment {experiment_id!r} has no readable experiment_manifest.json"
        )
    return ExperimentResultsResponse(
        experiment_id=experiment_id,
        manifest=manifest,
        metrics=_read_optional_json(directory, "metrics.json"),
        stability_report=_read_optional_json(directory, "stability_report.json"),
    )


def _canonical_scenario(directory: Path, manifest: dict[str, Any]) -> str | None:
    """The default scenario: ``full_cost`` wins, else the last declared one."""
    metrics = _read_optional_json(directory, "metrics.json")
    scenarios: list[str] = []
    if metrics is not None:
        spec = metrics.get("meta", {}).get("spec", {})
        scenarios = [str(name) for name in spec.get("cost_scenarios", [])]
    if "full_cost" in scenarios:
        return "full_cost"
    return scenarios[-1] if scenarios else None


def _json_safe(value: Any) -> Any:
    """One parquet cell -> a JSON-serialisable scalar (ISO dates, NaN->null)."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


@router.get(
    "/experiments/{experiment_id}/folds/{fold_id}/equity",
    response_model=FoldEquityResponse,
)
def fold_equity(
    experiment_id: Annotated[str, PathParam(pattern=_EXPERIMENT_ID_RE.pattern)],
    fold_id: Annotated[str, PathParam(pattern=r"^[0-9a-f]{64}$")],
    request: Request,
    scenario: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_]+$")] = None,
) -> FoldEquityResponse:
    """Serve one fold's daily equity path -- but only a file the manifest's
    declared artifact graph actually claims, with the hash re-verified."""
    directory = _experiment_dir(request, experiment_id)
    manifest = _read_optional_json(directory, "experiment_manifest.json") or {}
    declared: dict[str, str] = {
        str(key): str(value) for key, value in (manifest.get("artifacts") or {}).items()
    }
    chosen = (
        scenario if scenario is not None else _canonical_scenario(directory, manifest)
    )
    if chosen is None:
        raise ArtifactNotFound(
            f"experiment {experiment_id!r} declares no cost scenario to serve"
        )
    relative = f"folds/{fold_id}/backtest/{chosen}/equity.parquet"
    if relative not in declared:
        # Falling back to the canonical fold file must also stay inside the
        # declared graph: an undeclared file is never served.
        relative = f"folds/{fold_id}/equity.parquet"
        if relative not in declared:
            if not any(key.startswith(f"folds/{fold_id}/") for key in declared):
                raise FoldNotFound(f"experiment has no declared fold {fold_id!r}")
            raise ArtifactNotFound(
                f"fold {fold_id!r} has no declared equity artifact for {chosen!r}"
            )
    # Belt and braces: ``relative`` can only come from a declared key, but the
    # resolved path must still land inside this fold's directory (``fold_id``
    # and ``chosen`` already passed the 64-hex / [A-Za-z0-9_]+ path regexes).
    path = (directory / relative).resolve()
    fold_root = (directory / "folds" / fold_id).resolve()
    if fold_root not in path.parents:
        raise ArtifactNotFound(f"refusing a path outside fold {fold_id!r}")
    if not path.is_file():
        raise ArtifactNotFound(f"declared artifact {relative} is missing on disk")
    if hashlib.sha256(path.read_bytes()).hexdigest() != declared[relative]:
        raise ArtifactNotFound(f"declared hash mismatch for {relative}")
    frame = pd.read_parquet(path)
    rows = [
        {key: _json_safe(record[key]) for key in _EQUITY_COLUMNS if key in record}
        for record in frame.to_dict("records")
    ]
    served_scenario = chosen if f"backtest/{chosen}" in relative else None
    return FoldEquityResponse(
        experiment_id=experiment_id,
        fold_id=fold_id,
        scenario=served_scenario,
        rows=rows,
    )
