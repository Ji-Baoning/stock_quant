"""Dataset, quality and acceptance read endpoints (spec §8.2).

Acceptance and experiment data are read from their on-disk stores directly:
importing ``stock_quant.research.acceptance`` executes the package
``__init__``, which imports ``stock_quant.data_pipeline`` -- a dependency
the service is forbidden from taking (spec §8.1, ADR-021).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel

from stock_quant.data_model.dataset import (
    DatasetContext,
    DatasetNotFoundError,
    DatasetReader,
)
from stock_quant.service.errors import (
    AcceptanceRecordUnreadable,
    DatasetUnresolvable,
    ManifestUnreadable,
    NoCurrentDataset,
    QualityReportUnreadable,
    ServiceError,
)

#: The acceptance policy a "valid accepted record" must carry. Duplicated as
#: a literal because the models module is unreachable without importing the
#: data pipeline (see module docstring). Source of truth:
#: ``research/acceptance/models.py::POLICY_VERSION``.
_ACCEPTANCE_POLICY_VERSION = "real-data-v1"
_CURRENT_NAME = "CURRENT"
_MANIFEST_NAME = "dataset_manifest.json"
_QUALITY_REPORT_NAME = "quality_report.json"
_VERSION_PATTERN = r"^(current|[0-9a-f]{64})$"
_VERSION_RE = re.compile(_VERSION_PATTERN)
_VERSION_DIR_RE = re.compile(r"^[0-9a-f]{64}$")
_WORKSHEET_DIRNAME = "acceptance-worksheets"

VersionPath = Annotated[str, PathParam(pattern=_VERSION_PATTERN)]


@dataclass(frozen=True)
class PinnedDataset:
    """One request's once-resolved dataset version and its read-only context."""

    requested_version: str
    dataset_version: str
    context: DatasetContext


class AcceptanceSummary(BaseModel):
    state: Literal["ACCEPTED", "REJECTED", "PENDING_CONFIRMATION", "UNVERIFIED"]
    has_valid_accepted_record: bool
    latest_verdict: str | None
    record_count: int


class QualitySummary(BaseModel):
    by_severity: dict[str, int]


class DatasetSummary(BaseModel):
    dataset_version: str
    is_current: bool
    created_at: str | None
    table_count: int
    quality: QualitySummary
    acceptance: AcceptanceSummary


class DatasetsListResponse(BaseModel):
    current: str | None
    datasets: list[DatasetSummary]


class TableMeta(BaseModel):
    name: str
    row_count: int
    schema_version: str


class DatasetDetailResponse(BaseModel):
    dataset_version: str
    requested_version: str
    manifest: dict[str, Any]
    tables: list[TableMeta]
    quality: QualitySummary
    acceptance: AcceptanceSummary


class QualityIssueView(BaseModel):
    severity: str | None = None
    code: str | None = None
    table: str | None = None
    symbol: str | None = None
    trade_date: str | None = None
    details: dict[str, Any] | None = None


class QualityViewResponse(BaseModel):
    dataset_version: str
    requested_version: str
    offset: int
    limit: int
    total: int
    issues: list[QualityIssueView]


def read_json_or_fail(path: Path, error_class: type[ServiceError], what: str) -> Any:
    """Parse one stored JSON file, failing closed through ``error_class``."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as cause:
        raise error_class(f"{what} ({path.name}) is unreadable") from cause


def resolve_requested_version(project_root: Path, requested: str) -> str:
    """Resolve ``current`` once at the request entry; full hashes pass through.

    This is the only place a request consults ``CURRENT`` (spec §4.4): after
    resolution the request is pinned to one immutable version.
    """
    if requested != "current":
        if not _VERSION_DIR_RE.fullmatch(requested):
            raise DatasetUnresolvable(f"version {requested!r} is not a full hash")
        return requested
    current_file = project_root / "data" / "standardized" / _CURRENT_NAME
    if not current_file.is_file():
        raise NoCurrentDataset(
            "no CURRENT file under data/standardized; no dataset has been published"
        )
    version = current_file.read_text(encoding="utf-8").strip()
    if not _VERSION_DIR_RE.fullmatch(version):
        raise NoCurrentDataset(
            f"CURRENT does not name a full version hash: {version!r}"
        )
    return version


def pinned_dataset(version: VersionPath, request: Request) -> Iterator[PinnedDataset]:
    """Open one read-only context per request; never cached across requests."""
    root = Path(request.app.state.project_root)
    resolved = resolve_requested_version(root, version)
    request.state.resolved_version = resolved
    try:
        context = DatasetReader(root).open(resolved)
    except DatasetNotFoundError as error:
        raise DatasetUnresolvable(str(error)) from error
    except json.JSONDecodeError as error:
        raise ManifestUnreadable(
            f"dataset manifest of {resolved} is not valid JSON"
        ) from error
    with context:
        yield PinnedDataset(
            requested_version=version, dataset_version=resolved, context=context
        )


def _current_version_or_none(root: Path) -> str | None:
    try:
        return resolve_requested_version(root, "current")
    except ServiceError:
        return None


def load_quality_payload(project_root: Path, version: str) -> dict[str, Any]:
    path = (
        project_root
        / "data"
        / "standardized"
        / version
        / _QUALITY_REPORT_NAME
    )
    payload = read_json_or_fail(path, QualityReportUnreadable, "quality_report.json")
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise QualityReportUnreadable("quality_report.json is not a persisted report")
    return payload


def _acceptance_records(project_root: Path, version: str) -> list[dict[str, Any]]:
    """Every stored record for a version, oldest first; fail closed."""
    directory = project_root / "data" / "acceptances" / version
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        payload = read_json_or_fail(
            child / "acceptance.json", AcceptanceRecordUnreadable, "acceptance record"
        )
        if not isinstance(payload, dict) or "decision" not in payload:
            raise AcceptanceRecordUnreadable(
                f"acceptance record {child.name} carries no decision"
            )
        records.append(payload)
    records.sort(
        key=lambda row: (
            str(row.get("created_at", "")),
            str(row.get("acceptance_id", "")),
        )
    )
    return records


def _is_valid_accepted(row: dict[str, Any]) -> bool:
    """Mirror of ``AcceptanceRegistry.select``'s validity filter, on raw JSON."""
    if row.get("decision") != "ACCEPTED":
        return False
    if row.get("policy_version") != _ACCEPTANCE_POLICY_VERSION:
        return False
    automated = row.get("automated_checks")
    manual = row.get("manual_checks")
    if not isinstance(automated, list) or not isinstance(manual, list):
        return False
    return all(item.get("status") == "PASS" for item in automated) and all(
        item.get("status") == "PASS" for item in manual
    )


def acceptance_summary(project_root: Path, version: str) -> AcceptanceSummary:
    """Four states, with the valid-accepted flag and latest verdict separate.

    A newer REJECTED record never masks a still-valid ACCEPTED record (spec
    §8.2). PENDING_CONFIRMATION means an acceptance cycle has been prepared
    (unsigned worksheets under ``data/acceptance-worksheets/<version>/``) but
    no record satisfying the current policy exists -- which is also where a
    record ACCEPTED only under a superseded policy lands: it can no longer
    satisfy the Research gate and must be re-confirmed.
    """
    records = _acceptance_records(project_root, version)
    has_valid = any(_is_valid_accepted(row) for row in records)
    latest_verdict = str(records[-1]["decision"]) if records else None
    if has_valid:
        state: str = "ACCEPTED"
    elif latest_verdict == "REJECTED":
        state = "REJECTED"
    elif (project_root / "data" / _WORKSHEET_DIRNAME / version).is_dir():
        state = "PENDING_CONFIRMATION"
    else:
        state = "UNVERIFIED"
    return AcceptanceSummary(
        state=state,  # type: ignore[arg-type]
        has_valid_accepted_record=has_valid,
        latest_verdict=latest_verdict,
        record_count=len(records),
    )


router = APIRouter(prefix="/api/v1", tags=["datasets"])


@router.get("/datasets", response_model=DatasetsListResponse)
def list_datasets(request: Request) -> DatasetsListResponse:
    root = Path(request.app.state.project_root)
    standardized = root / "data" / "standardized"
    current = _current_version_or_none(root)
    entries: list[DatasetSummary] = []
    children = sorted(standardized.iterdir()) if standardized.is_dir() else []
    for child in children:
        if not child.is_dir() or not _VERSION_DIR_RE.fullmatch(child.name):
            continue
        manifest = read_json_or_fail(
            child / _MANIFEST_NAME, ManifestUnreadable, "dataset_manifest.json"
        )
        if not isinstance(manifest, dict):
            raise ManifestUnreadable(
                f"dataset manifest of {child.name} is not a JSON object"
            )
        quality = load_quality_payload(root, child.name)
        tables = manifest.get("tables")
        entries.append(
            DatasetSummary(
                dataset_version=child.name,
                is_current=child.name == current,
                created_at=(
                    str(manifest.get("created_at"))
                    if manifest.get("created_at")
                    else None
                ),
                table_count=len(tables) if isinstance(tables, dict) else 0,
                quality=QualitySummary(
                    by_severity=dict(quality.get("by_severity") or {})
                ),
                acceptance=acceptance_summary(root, child.name),
            )
        )
    return DatasetsListResponse(current=current, datasets=entries)


@router.get("/datasets/{version}", response_model=DatasetDetailResponse)
def dataset_detail(
    pinned: Annotated[PinnedDataset, Depends(pinned_dataset)],
    request: Request,
) -> DatasetDetailResponse:
    root = Path(request.app.state.project_root)
    manifest = pinned.context.manifest
    tables = manifest.get("tables")
    table_meta = (
        [
            TableMeta(
                name=name,
                row_count=int(entry.get("row_count", 0)),
                schema_version=str(entry.get("schema_version", "")),
            )
            for name, entry in sorted(tables.items())
            if isinstance(entry, dict)
        ]
        if isinstance(tables, dict)
        else []
    )
    quality = load_quality_payload(root, pinned.dataset_version)
    return DatasetDetailResponse(
        dataset_version=pinned.dataset_version,
        requested_version=pinned.requested_version,
        manifest=dict(manifest),
        tables=table_meta,
        quality=QualitySummary(
            by_severity=dict(quality.get("by_severity") or {})
        ),
        acceptance=acceptance_summary(root, pinned.dataset_version),
    )


@router.get("/datasets/{version}/quality", response_model=QualityViewResponse)
def dataset_quality(
    pinned: Annotated[PinnedDataset, Depends(pinned_dataset)],
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> QualityViewResponse:
    root = Path(request.app.state.project_root)
    payload = load_quality_payload(root, pinned.dataset_version)
    issues = payload["issues"]
    window = [
        issue for issue in issues[offset : offset + limit] if isinstance(issue, dict)
    ]
    return QualityViewResponse(
        dataset_version=pinned.dataset_version,
        requested_version=pinned.requested_version,
        offset=offset,
        limit=limit,
        total=len(issues),
        issues=[
            QualityIssueView(
                **{key: issue.get(key) for key in QualityIssueView.model_fields}
            )
            for issue in window
        ],
    )
