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
