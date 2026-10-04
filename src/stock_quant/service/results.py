"""Read-only aggregation over an experiment's published JSON artifacts (v3 §7.2).

The service never recomputes anything: it reads the files a published
experiment actually carries and reports absent artifacts as ``null``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from pydantic import BaseModel

from stock_quant.service.datasets import read_json_or_fail
from stock_quant.service.errors import (
    ExperimentManifestUnreadable,
    ResultsNotFound,
    UnknownExperiment,
)

_EXPERIMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ExperimentResultsResponse(BaseModel):
    experiment_id: str
    manifest: dict[str, Any]
    metrics: dict[str, Any] | None = None
    stability_report: dict[str, Any] | None = None


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
