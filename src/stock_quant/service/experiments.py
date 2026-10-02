"""Experiment listing and the read-only report endpoint (spec §8.2).

Reads ``data/experiments/<experiment_id>/`` directly (see the datasets
module docstring for why the research package is not imported). The report
endpoint only serves an already-generated self-contained HTML file; it
never rebuilds a report in-request.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel

from stock_quant.service.datasets import read_json_or_fail
from stock_quant.service.errors import (
    ExperimentManifestUnreadable,
    ReportNotFound,
    UnknownExperiment,
)

_EXPERIMENT_ID_PATTERN = r"^[0-9a-f]{64}$"
_EXPERIMENT_ID_RE = re.compile(_EXPERIMENT_ID_PATTERN)
_MANIFEST_NAME = "experiment_manifest.json"
_REPORT_NAME = "report.html"


class ExperimentSummary(BaseModel):
    experiment_id: str
    status: str | None = None
    dataset_version: str | None = None
    universe_version: str | None = None
    evaluation_reason: str | None = None


class ExperimentsListResponse(BaseModel):
    experiments: list[ExperimentSummary]


def _experiments_root(request: Request) -> Path:
    return Path(request.app.state.project_root) / "data" / "experiments"


def _read_experiment_manifest(directory: Path) -> dict[str, Any]:
    payload = read_json_or_fail(
        directory / _MANIFEST_NAME,
        ExperimentManifestUnreadable,
        "experiment_manifest.json",
    )
    if not isinstance(payload, dict):
        raise ExperimentManifestUnreadable(
            f"experiment manifest of {directory.name} is not a JSON object"
        )
    return payload


router = APIRouter(prefix="/api/v1", tags=["experiments"])


@router.get("/experiments", response_model=ExperimentsListResponse)
def list_experiments(request: Request) -> ExperimentsListResponse:
    root = _experiments_root(request)
    summaries: list[ExperimentSummary] = []
    children = sorted(root.iterdir()) if root.is_dir() else []
    for child in children:
        if not child.is_dir() or not _EXPERIMENT_ID_RE.fullmatch(child.name):
            continue
        manifest = _read_experiment_manifest(child)
        summaries.append(
            ExperimentSummary(
                experiment_id=child.name,
                status=manifest.get("status"),
                dataset_version=manifest.get("dataset_version"),
                universe_version=manifest.get("universe_version"),
                evaluation_reason=manifest.get("evaluation_reason"),
            )
        )
    return ExperimentsListResponse(experiments=summaries)


@router.get("/experiments/{experiment_id}/report")
def experiment_report(
    experiment_id: Annotated[str, PathParam(pattern=_EXPERIMENT_ID_PATTERN)],
    request: Request,
) -> FileResponse:
    """Serve the existing self-contained ``report.html``; never rebuild it."""
    root = _experiments_root(request).resolve()
    directory = (root / experiment_id).resolve()
    if directory.parent != root or not directory.is_dir():
        raise UnknownExperiment(f"no published experiment {experiment_id!r}")
    report = directory / _REPORT_NAME
    if not report.is_file():
        raise ReportNotFound(f"experiment {experiment_id} has no report.html")
    return FileResponse(report, media_type="text/html")
