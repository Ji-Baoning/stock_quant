"""Build one dataset version's mechanisable acceptance evidence pack.

Six of the nine manual checks are facts this project can gather
deterministically from a pinned version -- row-count relationships,
missing-bar classifications, sampled corporate-action facts, benchmark
coverage, security-master rows and a credential scan over the pack's own
output.  The other three need corroboration this project cannot produce (an
official exchange calendar, a second price source, official rule effective
dates); they stay :data:`OPERATOR_ONLY_CODES` for the operator.

The pack is version-bound and content-addressed: it is staged beside its
final location and swapped in whole, so a failed rebuild never destroys the
previously published pack.  Nothing here writes a standardized version, the
raw store or ``CURRENT`` -- the only write is
``data/acceptance-evidence/<version>/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence
from uuid import uuid4

import pandas as pd

from stock_quant.config import load_project_config
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_quality.raw_checks import classify_missing_row
from stock_quant.research.acceptance.checks import (
    _ACCEPTED_MISSING_CODES,
    _open_days,
    _window,
)
from stock_quant.research.acceptance.models import (
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
    EvidenceReference,
)

__all__ = [
    "EVIDENCE_DIRNAME",
    "EVIDENCE_FAILURE_CATEGORIES",
    "EVIDENCE_FILENAMES",
    "MECHANISABLE_CODES",
    "OPERATOR_ONLY_CODES",
    "EvidenceBuildError",
    "EvidenceFile",
    "benchmark_evidence",
    "build_mechanisable_evidence",
    "corporate_action_evidence",
    "evidence_window",
    "missing_reason_evidence",
    "secret_scan_evidence",
    "security_master_evidence",
    "source_row_count_evidence",
]

_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(tushare_token|api[_-]?key|secret|password|passwd|authorization)"
    r"\s*[:=]\s*\S+"
)

#: Directory (under the project root) holding one evidence pack per version.
EVIDENCE_DIRNAME = "acceptance-evidence"

#: The artifact each mechanisable manual check cites.
EVIDENCE_FILENAMES = {
    "source_row_count_sample": "source_row_counts.json",
    "missing_reason_sample": "missing_reasons.json",
    "corporate_action_sample": "corporate_action_sample.csv",
    "benchmark_sample": "benchmark_coverage.json",
    "security_master_sample": "security_master_sample.csv",
    "secret_scan": "secret_scan.json",
}

#: The stable failure categories a checklist row may summarise.
EVIDENCE_FAILURE_CATEGORIES = (
    "window_missing",
    "dataset_unreadable",
    "evidence_write_failed",
)


class EvidenceBuildError(RuntimeError):
    """The evidence pack could not be built.

    ``category`` is one of :data:`EVIDENCE_FAILURE_CATEGORIES` and is the only
    thing a checklist row ever says about the failure, so a persisted row
    stays deterministic and free of exception text.
    """

    def __init__(self, category: str) -> None:
        if category not in EVIDENCE_FAILURE_CATEGORIES:
            raise ValueError(f"unknown evidence failure category {category!r}")
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class EvidenceFile:
    """One evidence artifact: its name under the pack and its bytes."""

    name: str
    text: str


def evidence_window(manifest: Mapping[str, object]) -> tuple[date, date]:
    """The window every mechanisable evidence file is computed over.

    Deliberately the *same* window the automated ``date_window_completeness``
    check uses -- requested start through resolved end (``checks._window``).
    It is not ``build_config.effective_start_date``: the evidence a human
    signs off on and the automated verdict must describe one window, and a
    request that started before the data does has to show up as absent bars
    rather than silently shrink what was reviewed.
    """
    build = manifest.get("build_config")
    if not isinstance(build, dict):
        raise EvidenceBuildError("window_missing")
    try:
        return _window(build)
    except (TypeError, ValueError) as error:
        raise EvidenceBuildError("window_missing") from error


def build_mechanisable_evidence(
    project_root: Path, dataset_version: str
) -> dict[str, EvidenceReference]:
    """Build the six-file evidence pack for one version and return its refs.

    The returned mapping is keyed by manual check code in
    :data:`MECHANISABLE_CODES` order; every reference is a project-relative
    path whose SHA-256 pins the staged bytes.  Raises
    :class:`EvidenceBuildError` without touching an existing pack when
    anything fails.
    """
    root = Path(project_root).resolve()
    manifest = _load_manifest(root, dataset_version)
    start, end = evidence_window(manifest)
    files = _evidence_files(root, manifest, dataset_version, start, end)
    return _write_pack(root, dataset_version, files)


def _load_manifest(root: Path, dataset_version: str) -> dict[str, object]:
    """The version's manifest JSON, or a stable build failure."""
    path = (
        root
        / "data"
        / "standardized"
        / dataset_version
        / "dataset_manifest.json"
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceBuildError("dataset_unreadable") from error
    if not isinstance(payload, dict):
        raise EvidenceBuildError("dataset_unreadable")
    return payload


def _evidence_files(
    root: Path,
    manifest: Mapping[str, object],
    dataset_version: str,
    start: date,
    end: date,
) -> tuple[EvidenceFile, ...]:
    """The six artifacts, built in memory from the pinned version.

    Every input problem reads the same way out of here -- an unreadable table,
    a malformed project config, or a manifest field a constructor cannot parse
    -- as :class:`EvidenceBuildError("dataset_unreadable")`.  The guard spans
    the whole build, not only the reads, so no plain ``ValueError``/``KeyError``
    from a constructor escapes to abort ``data acceptance prepare`` before the
    checklist is written; the caller degrades to unconfirmed rows instead.
    """
    try:
        with DatasetReader(root).open(dataset_version) as dataset:
            daily = dataset.read("daily_bar")
            master = dataset.read("security_master")
            calendar = dataset.read("trading_calendar")
            corporate_action = dataset.read("corporate_action")
        build = manifest.get("build_config")
        snapshots = (
            list(build.get("raw_snapshots", []))
            if isinstance(build, dict)
            else []
        )
        benchmarks = tuple(load_project_config(root).benchmark_symbols)
        open_days = [
            day for day in _open_days(calendar) if start <= day <= end
        ]
        written = [
            source_row_count_evidence(
                manifest, snapshots, trading_days=len(open_days)
            ),
            missing_reason_evidence(daily, master, calendar, start, end),
            security_master_evidence(master),
            benchmark_evidence(daily, calendar, benchmarks, start, end),
            corporate_action_evidence(corporate_action),
        ]
        return (*written, secret_scan_evidence(written))
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise EvidenceBuildError("dataset_unreadable") from error


def _pack_dir(root: Path, dataset_version: str) -> Path:
    """The published pack location for one version."""
    return root / "data" / EVIDENCE_DIRNAME / dataset_version


def _write_pack(
    root: Path, dataset_version: str, files: Sequence[EvidenceFile]
) -> dict[str, EvidenceReference]:
    """Stage, hash and swap in one pack; never leave a half-written pack."""
    pack = _pack_dir(root, dataset_version)
    pack.parent.mkdir(parents=True, exist_ok=True)
    staging = pack.parent / f".{dataset_version}.{uuid4().hex}.tmp"
    try:
        staging.mkdir()
        _stage_files(staging, files)
        references = {
            code: EvidenceReference(
                kind="local",
                reference=_reference(root, pack, code),
                sha256=_sha256_file(staging / EVIDENCE_FILENAMES[code]),
                summary=(
                    f"{EVIDENCE_FILENAMES[code]} generated by "
                    "data acceptance prepare"
                ),
            )
            for code in MECHANISABLE_CODES
        }
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise EvidenceBuildError("evidence_write_failed") from error
    _swap_in(staging, pack)
    return references


def _stage_files(staging: Path, files: Sequence[EvidenceFile]) -> None:
    """Write every artifact into the staging directory."""
    for file in files:
        (staging / file.name).write_text(file.text, encoding="utf-8")


def _swap_in(staging: Path, pack: Path) -> None:
    """Replace ``pack`` with ``staging``, keeping the old pack on failure.

    POSIX cannot atomically replace a non-empty directory -- ``os.replace``
    raises ``ENOTEMPTY`` -- so the old pack is renamed aside first and deleted
    only once the new one is in place.  A crash between the two renames leaves
    one complete pack on disk, never a half-written one.
    """
    retired = pack.with_name(f".{pack.name}.{uuid4().hex}.old")
    try:
        if pack.exists():
            os.replace(pack, retired)
        os.replace(staging, pack)
    except OSError as error:
        if not pack.exists() and retired.exists():
            os.replace(retired, pack)
        shutil.rmtree(staging, ignore_errors=True)
        raise EvidenceBuildError("evidence_write_failed") from error
    shutil.rmtree(retired, ignore_errors=True)


def _reference(root: Path, pack: Path, code: str) -> str:
    """The project-relative path of one artifact once the pack is in place."""
    return (pack / EVIDENCE_FILENAMES[code]).relative_to(root).as_posix()


def _sha256_file(path: Path) -> str:
    """The lowercase hex SHA-256 of one file's bytes."""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _as_date(value: object) -> date | None:
    """Coerce one frame cell to a ``date``; missing/NaT reads as ``None``."""
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _columns(frame: pd.DataFrame, wanted: Sequence[str]) -> list[str]:
    return [name for name in wanted if name in frame.columns]


def source_row_count_evidence(
    manifest: Mapping[str, object],
    snapshots: Sequence[Mapping[str, object]],
    *,
    trading_days: int,
) -> EvidenceFile:
    """Row counts of every published table beside every bound raw snapshot.

    Both arguments are the manifest JSON as parsed, not the pydantic models
    the acceptance checker wraps it in: ``build_config.raw_snapshots`` is
    written as a list of mappings.
    """
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("dataset manifest has no tables mapping")
    counts = {
        str(name): int(entry.get("row_count", -1))
        for name, entry in sorted(tables.items())
        if isinstance(entry, dict)
    }
    bindings = sorted(
        (
            {
                "source": str(row.get("source")),
                "endpoint": str(row.get("endpoint")),
                "request_key": str(row.get("request_key")),
                "file_sha256": str(row.get("file_sha256")),
            }
            for row in snapshots
        ),
        key=lambda item: (
            item["source"],
            item["endpoint"],
            item["request_key"],
        ),
    )
    return EvidenceFile(
        "source_row_counts.json",
        _json(
            {
                "trading_days": int(trading_days),
                "tables": counts,
                "raw_snapshots": bindings,
            }
        ),
    )


def missing_reason_evidence(
    daily: pd.DataFrame,
    master: pd.DataFrame,
    calendar: pd.DataFrame,
    start: date,
    end: date,
) -> EvidenceFile:
    """Classification counts for every absent bar over the window."""
    facts = {
        str(row.get("symbol")): (
            _as_date(row.get("list_date")),
            _as_date(row.get("delist_date")),
        )
        for row in master.to_dict("records")
    }
    present = {
        (str(row.get("symbol")), _as_date(row.get("trade_date")))
        for row in daily.to_dict("records")
    }
    grid = [day for day in _open_days(calendar) if start <= day <= end]
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}
    for symbol in sorted(facts):
        list_date, delist_date = facts[symbol]
        for day in grid:
            if (symbol, day) in present:
                continue
            code = classify_missing_row(
                trade_date=day,
                list_date=list_date,
                delist_date=delist_date,
                is_trading_day=True,
                primary_present=False,
                validation_present=False,
            )
            counts[code] = counts.get(code, 0) + 1
            samples.setdefault(code, f"{symbol}@{day.isoformat()}")
    window = {"start": start.isoformat(), "end": end.isoformat()}
    return EvidenceFile(
        "missing_reasons.json",
        _json(
            {
                "window": window,
                "accepted_codes": sorted(_ACCEPTED_MISSING_CODES),
                "counts": counts,
                "first_sample": samples,
            }
        ),
    )


def security_master_evidence(
    master: pd.DataFrame, *, limit: int = 30
) -> EvidenceFile:
    """A deterministic sample of security-master listing facts."""
    wanted = _columns(
        master, ("symbol", "list_date", "delist_date", "status", "source")
    )
    frame = master.sort_values("symbol", kind="stable").head(limit)
    return EvidenceFile(
        "security_master_sample.csv", frame[wanted].to_csv(index=False)
    )


def benchmark_evidence(
    daily: pd.DataFrame,
    calendar: pd.DataFrame,
    benchmarks: Sequence[str],
    start: date,
    end: date,
) -> EvidenceFile:
    """Per-benchmark coverage of the window's open days."""
    grid = [day for day in _open_days(calendar) if start <= day <= end]
    payload: dict[str, object] = {}
    for symbol in benchmarks:
        rows = daily[daily["symbol"] == symbol]
        dates = {_as_date(value) for value in rows["trade_date"]}
        payload[str(symbol)] = {
            "rows": int(len(rows)),
            "first": min(dates).isoformat() if dates else None,
            "last": max(dates).isoformat() if dates else None,
            "open_days": len(grid),
            "missing_open_days": len([day for day in grid if day not in dates]),
        }
    window = {"start": start.isoformat(), "end": end.isoformat()}
    return EvidenceFile(
        "benchmark_coverage.json",
        _json({"window": window, "coverage": payload}),
    )


def corporate_action_evidence(
    corporate_action: pd.DataFrame, *, limit: int = 20
) -> EvidenceFile:
    """A deterministic sample of the reconciled corporate-action facts."""
    wanted = _columns(
        corporate_action,
        (
            "symbol",
            "ex_date",
            "record_date",
            "cash_dividend_per_share",
            "bonus_share_ratio",
            "capitalization_ratio",
            "source",
            "status",
        ),
    )
    frame = corporate_action.sort_values(
        ["symbol", "ex_date"], kind="stable"
    ).head(limit)
    return EvidenceFile(
        "corporate_action_sample.csv", frame[wanted].to_csv(index=False)
    )


def secret_scan_evidence(
    files: Sequence[EvidenceFile], *, limit: int = 50
) -> EvidenceFile:
    """Scan the pack's own generated files for credential-looking lines.

    The scanned names are the *final* pack names, never the staging
    directory's, so the report's bytes -- and therefore its SHA-256 -- do not
    depend on where the pack happened to be staged.  ``secret_scan.json`` is
    the one artifact that cannot contain its own hash and is therefore not in
    its own scan set.
    """
    hits: list[dict[str, object]] = []
    for file in files:
        for number, line in enumerate(file.text.splitlines(), start=1):
            if _CREDENTIAL_PATTERN.search(line):
                hits.append({"path": file.name, "line": number})
        if len(hits) >= limit:
            break
    return EvidenceFile(
        "secret_scan.json",
        _json(
            {
                "scanned": [file.name for file in files],
                "hits": hits[:limit],
            }
        ),
    )
