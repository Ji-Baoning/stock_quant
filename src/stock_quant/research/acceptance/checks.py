"""Offline, read-only automated acceptance checks (``real-data-v1``).

The checker recomputes every verdict in ``AUTOMATED_CHECK_CODES`` from the
pinned dataset evidence alone: the dataset manifest and the table bytes it
declares, ``quality_report.json``, the sanitized ``build_config`` provenance
and the raw snapshots it binds.  Nothing is mutated, ``CURRENT`` is never
consulted and no network access happens.

Failure details are deterministic and redacted: they carry stable reason
codes, counts, symbols and dates only -- never exception text, local paths or
timestamps -- so identical evidence always produces identical
:class:`CheckResult` tuples.  A failing check reports ``details["code"]`` (the
alphabetically first distinct reason code, so a single-fault failure names
that fault directly, e.g. ``table_hash_mismatch``) next to the full sorted
``details["failures"]`` rows of ``[reason_code, subject]``.

Date-window semantics: the window spans ``build_config.requested_start_date``
through ``build_config.resolved_end_date``; it must sit inside the
``trading_calendar`` open-day span and end on an open day, both configured
benchmark symbols must have a bar on every open day of the window, and every
missing listed-security bar must be explained by the security-master listing
facts (before ``list_date``, after ``delist_date``, or a non-trading day) via
:func:`classify_missing_row`.  Suspensions and unexplained gaps are flagged,
never silently accepted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import duckdb
import numpy as np
import pandas as pd

from stock_quant.config import load_project_config
from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS, DatasetReader
from stock_quant.data_model.security_master import (
    missing_master_coverage_symbols,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import (
    DATASET_BUILD_CONTRACT_VERSION,
    DataPipeline,
)
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import (
    MISSING_DELISTED,
    MISSING_NON_TRADING_DAY,
    MISSING_NOT_LISTED,
    Severity,
)
from stock_quant.data_quality.raw_checks import classify_missing_row
from stock_quant.data_sources.raw_store import RawSnapshotEvidence, RawStore
from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    CheckResult,
    CheckStatus,
    RawSnapshotBinding,
)
from stock_quant.research.trust import evaluate_corporate_action_trust

_MANIFEST_NAME = "dataset_manifest.json"
_QUALITY_REPORT_NAME = "quality_report.json"

#: Missing-bar classifications a dataset may carry without failing: a bar may
#: be absent before listing, after delisting, or on a non-trading day.  A
#: suspension or any other gap fails ``date_window_completeness``.
_ACCEPTED_MISSING_CODES = frozenset(
    {MISSING_NOT_LISTED, MISSING_DELISTED, MISSING_NON_TRADING_DAY}
)

#: How many unexplained ``(symbol@date)`` samples one failure may list.
_MAX_SAMPLES = 20


@dataclass(frozen=True)
class AcceptanceCheckInput:
    """The pinned dataset version one checker run inspects (read-only)."""

    project_root: Path
    dataset_version: str


@dataclass(frozen=True)
class DatasetEvidence:
    """Everything the offline checks read about one dataset version."""

    dataset_path: Path
    manifest: dict[str, object]
    dataset_manifest_sha256: str
    quality_report_sha256: str
    raw_snapshot_evidence: tuple[RawSnapshotBinding, ...]


def dataset_evidence(value: AcceptanceCheckInput) -> DatasetEvidence:
    """Load and pre-verify the dataset evidence for ``value`` (read-only).

    Verifies that the manifest names the requested version, that every
    declared table path stays inside the dataset directory, hashes the
    manifest and quality report bytes, and parses the sanitized raw-snapshot
    bindings out of ``build_config``.
    """
    dataset_path = (
        value.project_root / "data" / "standardized" / value.dataset_version
    )
    manifest_path = dataset_path / _MANIFEST_NAME
    quality_path = dataset_path / _QUALITY_REPORT_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("dataset manifest is not a mapping")
    if manifest.get("dataset_version") != value.dataset_version:
        raise ValueError("dataset manifest version mismatch")
    _reject_escaping_table_paths(manifest, dataset_path)
    build = manifest.get("build_config")
    raw_rows = build.get("raw_snapshots", []) if isinstance(build, dict) else []
    if not isinstance(raw_rows, list):
        raise ValueError("dataset build raw_snapshots is malformed")
    bindings = tuple(
        RawSnapshotBinding.model_validate(row) for row in raw_rows
    )
    return DatasetEvidence(
        dataset_path=dataset_path,
        manifest=manifest,
        dataset_manifest_sha256=_sha256_file(manifest_path),
        quality_report_sha256=_sha256_file(quality_path),
        raw_snapshot_evidence=bindings,
    )


def run_automated_checks(value: AcceptanceCheckInput) -> tuple[CheckResult, ...]:
    """Run every ``real-data-v1`` automated check, in policy order.

    Each check is exception-safe: expected environmental failures
    (``OSError``, ``ValueError``, ``KeyError``, ``json.JSONDecodeError`` and
    DuckDB's ``duckdb.Error`` -- a tampered Parquet file makes even a cold
    catalog bind fail with ``duckdb.InvalidInputException``, which is not a
    ``ValueError``) become ``FAIL`` results whose details carry only the
    exception class name -- never ``str(error)``, because supplier paths or
    secrets can appear there.  Unexpected programming exceptions are not
    caught.
    """
    functions = {
        "dataset_manifest_integrity": _check_dataset_manifest,
        "quality_report_integrity": _check_quality_report,
        "required_table_coverage": _check_required_tables,
        "date_window_completeness": _check_date_window,
        "security_master_evidence": _check_security_master,
        "corporate_action_evidence": _check_corporate_actions,
        "raw_snapshot_traceability": _check_raw_snapshots,
        "source_role_health": _check_source_roles,
    }
    results = []
    for code in AUTOMATED_CHECK_CODES:
        try:
            results.append(functions[code](value))
        except (
            OSError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
            duckdb.Error,
        ) as error:
            results.append(
                CheckResult(
                    code=code,
                    status=CheckStatus.FAIL,
                    summary=f"{code} could not be verified",
                    details={"error_code": type(error).__name__},
                )
            )
    return tuple(results)


# --------------------------------------------------------------------------- #
# Integrity checks
# --------------------------------------------------------------------------- #


def _check_dataset_manifest(value: AcceptanceCheckInput) -> CheckResult:
    """Recompute every declared table's presence, hash and row count."""
    evidence = dataset_evidence(value)
    tables = _tables_mapping(evidence.manifest.get("tables"))
    failures: list[list[str]] = []
    for name in sorted(tables):
        declared = tables[name]
        path = evidence.dataset_path / str(declared.get("path", ""))
        if not path.is_file():
            failures.append(["table_missing", name])
            continue
        if _sha256_file(path) != str(declared.get("sha256", "")):
            failures.append(["table_hash_mismatch", name])
            continue
        if len(pd.read_parquet(path)) != int(declared.get("row_count", -1)):
            failures.append(["row_count_mismatch", name])
    return _result("dataset_manifest_integrity", failures)


def _check_quality_report(value: AcceptanceCheckInput) -> CheckResult:
    """Re-run ``DataPipeline.validate`` and the neutral publication gate."""
    dataset_evidence(value)
    report = DataPipeline(value.project_root).validate(value.dataset_version)
    decision = evaluate_publication(report)
    failures: list[list[str]] = [
        ["gate_blocked", reason] for reason in decision.reasons
    ]
    if any(issue.severity is Severity.FATAL for issue in report.issues):
        failures.append(["fatal_issue", "quality_report"])
    return _result("quality_report_integrity", failures)


# --------------------------------------------------------------------------- #
# Semantic checks
# --------------------------------------------------------------------------- #


def _check_required_tables(value: AcceptanceCheckInput) -> CheckResult:
    """Require every table registered by the current schema contract."""
    evidence = dataset_evidence(value)
    tables = _tables_mapping(evidence.manifest.get("tables"))
    missing = sorted(set(STANDARDIZED_SCHEMAS) - set(tables))
    failures = [["missing_required_table", name] for name in missing]
    return _result("required_table_coverage", failures)


def _check_date_window(value: AcceptanceCheckInput) -> CheckResult:
    """Require the resolved window fully covered by calendar and bars.

    The window spans ``requested_start_date`` through ``resolved_end_date``
    and must sit inside the ``trading_calendar`` open-day span and end on an
    open day; both configured benchmarks need a bar on every open day; and
    every missing listed-security bar must be explained by the security
    master's listing facts (before ``list_date``, after ``delist_date``) --
    suspensions and unexplained gaps are flagged, never accepted.
    """
    evidence = dataset_evidence(value)
    build = _build_config(evidence.manifest)
    if build is None:
        return _result(
            "date_window_completeness",
            [["dataset_build_evidence_missing", "build_config"]],
        )
    start, end = _window(build)
    with DatasetReader(value.project_root).open(value.dataset_version) as (
        context
    ):
        calendar = context.read("trading_calendar")
        daily = context.read("daily_bar")
        master = context.read("security_master")
    open_days = _open_days(calendar)
    failures: list[list[str]] = []
    if not open_days:
        return _result(
            "date_window_completeness",
            [["empty_trading_calendar", "trading_calendar"]],
        )
    if start < open_days[0] or end > open_days[-1] or end not in set(open_days):
        failures.append(
            ["window_not_calendar_complete", f"{start.isoformat()}..{end.isoformat()}"]
        )
    grid = [day for day in open_days if start <= day <= end]
    if not grid:
        return _result(
            "date_window_completeness",
            [["empty_window_grid", f"{start.isoformat()}..{end.isoformat()}"]],
        )
    failures.extend(_benchmark_failures(value, daily, grid))
    failures.extend(_missing_row_failures(daily, master, grid))
    return _result("date_window_completeness", failures)


def _check_security_master(value: AcceptanceCheckInput) -> CheckResult:
    """Require one master-coverage evidence row per universe symbol."""
    dataset_evidence(value)  # Fail fast on unreadable/escaping evidence.
    universe = _universe(value.project_root)
    with DatasetReader(value.project_root).open(value.dataset_version) as (
        context
    ):
        coverage = _optional_table(context, "security_master_coverage")
    missing = missing_master_coverage_symbols(universe.symbols, coverage)
    failures = [["missing_master_coverage", symbol] for symbol in missing]
    return _result("security_master_evidence", failures)


def _check_corporate_actions(value: AcceptanceCheckInput) -> CheckResult:
    """Require trusted corporate-action coverage over the resolved window."""
    evidence = dataset_evidence(value)
    build = _build_config(evidence.manifest)
    if build is None:
        return _result(
            "corporate_action_evidence",
            [["dataset_build_evidence_missing", "build_config"]],
        )
    start, end = _window(build)
    universe = _universe(value.project_root)
    with DatasetReader(value.project_root).open(value.dataset_version) as (
        context
    ):
        coverage = _optional_table(context, "corporate_action_coverage")
    decision = evaluate_corporate_action_trust(
        coverage, universe.symbols, start, end
    )
    failures = [[reason.code, reason.symbol] for reason in decision.reasons]
    return _result("corporate_action_evidence", failures)


def _check_raw_snapshots(value: AcceptanceCheckInput) -> CheckResult:
    """Re-verify every bound raw snapshot; empty evidence cannot pass."""
    evidence = dataset_evidence(value)
    build = _build_config(evidence.manifest)
    if build is None:
        return _result(
            "raw_snapshot_traceability",
            [["dataset_build_evidence_missing", "build_config"]],
        )
    failures: list[list[str]] = []
    if not evidence.raw_snapshot_evidence:
        failures.append(["raw_snapshot_evidence_missing", "build_config"])
    failures.extend(_unbound_required_sources(build, evidence))
    store = RawStore(value.project_root)
    for binding in sorted(
        evidence.raw_snapshot_evidence,
        key=lambda row: (
            row.source, row.endpoint, row.request_key, row.file_sha256
        ),
    ):
        try:
            store.verify_evidence(RawSnapshotEvidence(**binding.model_dump()))
        except (OSError, ValueError):
            failures.append(
                [
                    "snapshot_unverifiable",
                    f"{binding.source}:{binding.request_key}",
                ]
            )
    return _result("raw_snapshot_traceability", failures)


def _check_source_roles(value: AcceptanceCheckInput) -> CheckResult:
    """Require a real data-update build with every required source healthy."""
    evidence = dataset_evidence(value)
    build = _build_config(evidence.manifest)
    if build is None:
        return _result(
            "source_role_health",
            [["dataset_build_evidence_missing", "build_config"]],
        )
    failures: list[list[str]] = []
    origin = build.get("origin")
    if origin != "data_update":
        failures.append(["origin_not_data_update", str(origin)])
    contract = build.get("pipeline_contract_version")
    if contract != DATASET_BUILD_CONTRACT_VERSION:
        failures.append(["pipeline_contract_version_mismatch", str(contract)])
    statuses = build.get("source_status")
    if not isinstance(statuses, list):
        raise ValueError("dataset build source_status is malformed")
    for row in statuses:
        if not isinstance(row, dict):
            raise ValueError("dataset build source_status is malformed")
        if row.get("required") is not True:
            continue
        source = str(row.get("source"))
        if row.get("ok") is not True:
            failures.append(["required_source_not_ok", source])
        elif row.get("reason_code") != "ok":
            failures.append(["required_reason_code_not_ok", source])
    return _result("source_role_health", failures)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _result(code: str, failures: list[list[str]]) -> CheckResult:
    """The deterministic check verdict for a list of failure rows."""
    if not failures:
        return CheckResult(
            code=code,
            status=CheckStatus.PASS,
            summary=f"{code} passed",
        )
    rows = sorted(failures)
    reasons = sorted({row[0] for row in rows})
    return CheckResult(
        code=code,
        status=CheckStatus.FAIL,
        summary=f"{code} failed",
        details={"code": reasons[0], "failures": rows},
    )


def _reject_escaping_table_paths(
    manifest: Mapping[str, Any], dataset_path: Path
) -> None:
    """Reject any declared table path that escapes the dataset directory."""
    resolved_root = dataset_path.resolve()
    for name, declared in sorted(_tables_mapping(manifest.get("tables")).items()):
        relative = declared.get("path")
        if not isinstance(relative, str):
            raise ValueError("dataset manifest table path is malformed")
        candidate = (dataset_path / relative).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise ValueError(
                "dataset manifest table path escapes the dataset directory"
            )


def _tables_mapping(tables: object) -> dict[str, Mapping[str, Any]]:
    """The manifest ``tables`` section, or a malformed-manifest error."""
    if not isinstance(tables, dict) or not all(
        isinstance(row, dict) for row in tables.values()
    ):
        raise ValueError("dataset manifest tables section is malformed")
    return tables


def _build_config(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    """The sanitized build evidence section, or ``None`` when absent."""
    build = manifest.get("build_config")
    return build if isinstance(build, dict) else None


def _window(build: Mapping[str, Any]) -> tuple[date, date]:
    """The requested-start through resolved-end window of one build."""
    start = build.get("requested_start_date")
    end = build.get("resolved_end_date")
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError("dataset build window is missing")
    return date.fromisoformat(start), date.fromisoformat(end)


def _universe(project_root: Path) -> Universe:
    """The configured fixed engineering universe of one project."""
    return Universe.from_yaml(project_root / "configs" / "universe.yml")


def _optional_table(context: Any, name: str) -> pd.DataFrame | None:
    """Read one evidence table, or ``None`` when the dataset omits it."""
    if name not in context.tables:
        return None
    return context.read(name)


def _open_days(calendar: pd.DataFrame) -> list[date]:
    """The sorted open days a ``trading_calendar`` table confirms."""
    days = {
        _as_date(row.get("calendar_date"))
        for row in calendar.to_dict("records")
        if _is_open(row.get("is_trading_day"))
    }
    return sorted(day for day in days if day is not None)


def _is_open(value: object) -> bool:
    """Total coercion of one ``is_trading_day`` cell to an open/closed flag.

    Only a genuine boolean ``True`` counts as open.  ``bool(pd.NA)`` raises
    ``TypeError`` and ``bool(np.nan)`` is truthy, so ``None``/``pd.NA``/
    ``np.nan``/``False`` must all be treated as closed; ordinary boolean
    columns behave exactly as before.
    """
    if value is True:
        return True
    return isinstance(value, np.bool_) and bool(value)


def _benchmark_failures(
    value: AcceptanceCheckInput, daily: pd.DataFrame, grid: list[date]
) -> list[list[str]]:
    """Failures for configured benchmarks missing any open day of the grid."""
    benchmarks = tuple(
        load_project_config(value.project_root).benchmark_symbols
    )
    dates_by_symbol: dict[str, set[date]] = {}
    for row in daily.to_dict("records"):
        symbol = str(row.get("symbol"))
        day = _as_date(row.get("trade_date"))
        if day is not None:
            dates_by_symbol.setdefault(symbol, set()).add(day)
    failures: list[list[str]] = []
    for symbol in benchmarks:
        covered = dates_by_symbol.get(symbol, set())
        if any(day not in covered for day in grid):
            failures.append(["benchmark_incomplete", symbol])
    return failures


def _missing_row_failures(
    daily: pd.DataFrame, master: pd.DataFrame, grid: list[date]
) -> list[list[str]]:
    """Flag missing listed-security bars the master facts cannot explain.

    Reuses :func:`classify_missing_row` in its design-spec precedence; only
    ``not_listed``, ``delisted`` and ``non_trading_day`` are accepted
    classifications, so a suspension or any unexplained gap is flagged.
    """
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
    unexplained: list[str] = []
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
            if code not in _ACCEPTED_MISSING_CODES:
                unexplained.append(f"{symbol}@{day.isoformat()}")
    if not unexplained:
        return []
    failures = [["unexplained_missing_rows", str(len(unexplained))]]
    failures.extend(
        ["unexplained_missing_row", sample]
        for sample in sorted(unexplained)[:_MAX_SAMPLES]
    )
    return failures


def _unbound_required_sources(
    build: Mapping[str, Any], evidence: DatasetEvidence
) -> list[list[str]]:
    """Required-and-ok sources that carry no raw-snapshot binding."""
    statuses = build.get("source_status")
    if not isinstance(statuses, list):
        raise ValueError("dataset build source_status is malformed")
    bound = {row.source for row in evidence.raw_snapshot_evidence}
    failures: list[list[str]] = []
    for row in statuses:
        if not isinstance(row, dict):
            raise ValueError("dataset build source_status is malformed")
        healthy = row.get("required") is True and row.get("ok") is True
        if healthy and row.get("source") not in bound:
            failures.append(["required_source_unbound", str(row.get("source"))])
    return failures


def _as_date(value: object) -> date | None:
    """Normalize a calendar/bar cell (Timestamp/date/NaT) to a plain date."""
    if value is None:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    return timestamp.date()


def _sha256_file(path: Path) -> str:
    """The lowercase hex SHA-256 of one file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
