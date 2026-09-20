"""Offline, read-only acceptance checks over pinned dataset evidence.

This module carries the two offline check layers formal Research and the
operator workflow consume:

- The ``real-data-v1`` **automated checks** (:func:`run_automated_checks`)
  recompute every policy verdict from the pinned dataset evidence alone: the
  dataset manifest and the table bytes it declares, ``quality_report.json``,
  the sanitized ``build_config`` provenance and the raw snapshots it binds.
  Nothing is mutated, ``CURRENT`` is never consulted and no network access
  happens.
- The mandatory **``index_membership_evidence`` run-gate check**
  (:func:`evaluate_index_membership_evidence`) turns the pure membership
  validator (:func:`stock_quant.data_quality.raw_checks.validate_membership_facts`)
  plus a pinned :class:`~stock_quant.research.universe.UniverseDefinition`
  into the mandatory :class:`AcceptanceResult` the runner's
  ``universe_acceptance`` preflight stage enforces, plus the safe table
  reader that turns a missing membership table into an explicit failure.

Failure details are deterministic and redacted: the automated checks carry
stable reason codes, counts, symbols and dates only -- never exception text,
local paths or timestamps -- so identical evidence always produces identical
:class:`CheckResult` tuples.  A failing check reports ``details["code"]``
(the alphabetically first distinct reason code, so a single-fault failure
names that fault directly, e.g. ``table_hash_mismatch``) next to the full
sorted ``details["failures"]`` rows of ``[reason_code, subject]``.  The
membership gate's failure details are the four deterministic
:class:`AcceptanceResult` detail keys only.

Automated date-window semantics: the window spans the acceptance anchor
(``build_config.full_history_acceptance_start``, ADR-011; legacy fallback
``requested_start_date``) through ``build_config.resolved_end_date``;
it must sit inside the ``trading_calendar`` open-day span and end on an open
day, both configured benchmark symbols must have a bar on every open day of
the window, and every missing listed-security bar must be explained by the
security-master listing facts (before ``list_date``, after ``delist_date``,
or a non-trading day) via :func:`classify_missing_row`.  Suspensions and
unexplained gaps are flagged, never silently accepted.

The membership gate fails (``status=FAIL``) on:

- a missing or empty ``universe_membership`` table
  (``UNIVERSE_MEMBERSHIP_TABLE_MISSING``);
- any ``FATAL`` validator issue: missing evidence, unknown symbols, interval
  overlap, announcement look-ahead, empty master intersections, unproven
  delisting endpoints, coverage gaps or member-count mismatches
  (the validator's own stable codes);
- facts whose content hash differs from the definition's pinned
  ``membership_table_sha256`` (``UNIVERSE_DEFINITION_HASH_MISMATCH``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import duckdb
import numpy as np
import pandas as pd

from stock_quant.config import load_project_config
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.calendar_coverage import (
    validate_build_calendar_evidence,
)
from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS, DatasetReader
from stock_quant.data_model.security_master import (
    missing_master_coverage_symbols,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    SecurityMasterBoundary,
    membership_content_hash,
)
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
from stock_quant.data_quality.raw_checks import (
    TABLE_UNIVERSE_MEMBERSHIP,
    MembershipSizeException,
    classify_missing_row,
    validate_membership_facts,
)
from stock_quant.data_sources.raw_store import RawSnapshotEvidence, RawStore
from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    AcceptanceResult,
    AcceptanceStatus,
    CheckResult,
    CheckStatus,
    RawSnapshotBinding,
)
from stock_quant.research.trust import evaluate_corporate_action_trust
from stock_quant.research.universe import UniverseDefinition

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

#: Stable codes of the ``index_membership_evidence`` run-gate check.
CODE_INDEX_MEMBERSHIP_EVIDENCE = "index_membership_evidence"
CODE_TABLE_MISSING = "UNIVERSE_MEMBERSHIP_TABLE_MISSING"
CODE_DEFINITION_HASH_MISMATCH = "UNIVERSE_DEFINITION_HASH_MISMATCH"


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
        "calendar_coverage_evidence": _check_calendar_coverage,
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
    config = load_project_config(value.project_root)
    tiers = {
        name: contract.tier for name, contract in config.data_contracts.items()
    }
    decision = evaluate_publication(report, table_tiers=tiers)
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

    The window spans the acceptance anchor (``full_history_acceptance_start``,
    ADR-011; legacy fallback ``requested_start_date``) through
    ``resolved_end_date`` and must sit inside the ``trading_calendar``
    open-day span and end on an open day; both configured benchmarks need a
    bar on every open day; and every missing listed-security bar must be
    explained by the security master's listing facts (before ``list_date``,
    after ``delist_date``) -- suspensions and unexplained gaps are flagged,
    never accepted.
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


def _check_calendar_coverage(value: AcceptanceCheckInput) -> CheckResult:
    """Require every published calendar day to name its source, and no seed
    inside the version-bound full-history window.

    The criterion is read from the dataset's own ``build_config`` (the
    ``full_history_acceptance_start`` fixed at publish time), never recomputed
    from the current ``configs/universes`` tree: a later definition change must
    not retroactively re-judge an already-published version.
    """
    evidence = dataset_evidence(value)
    build = _build_config(evidence.manifest)
    with DatasetReader(value.project_root).open(value.dataset_version) as (
        context
    ):
        calendar = context.read("trading_calendar")
    violations = validate_build_calendar_evidence(
        build, open_days=_open_days(calendar)
    )
    failures = [
        [code, _calendar_subject(details)] for code, details in violations
    ]
    return _result("calendar_coverage_evidence", failures)


def _calendar_subject(details: Mapping[str, Any]) -> str:
    """A deterministic ``key=value`` subject for one calendar violation.

    Mirrors the other checks' ``[reason_code, subject]`` failure rows: only
    stable string detail values are rendered, so identical evidence always
    yields identical rows and no path or exception text can leak.
    """
    parts = [
        f"{key}={value}"
        for key, value in sorted(details.items())
        if isinstance(value, str)
    ]
    return " ".join(parts) if parts else "trading_calendar"


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
# The mandatory index_membership_evidence run-gate check
# --------------------------------------------------------------------------- #


def read_membership_table(context: Any) -> pd.DataFrame | None:
    """Read ``universe_membership`` from a dataset context, or ``None``.

    ``context`` is any object exposing ``tables`` and ``read`` (the pinned
    :class:`~stock_quant.data_model.dataset.DatasetContext`); a dataset that
    predates the membership table yields ``None`` so the caller can fail on a
    missing table instead of an exception.
    """
    if TABLE_UNIVERSE_MEMBERSHIP not in getattr(context, "tables", ()):
        return None
    return context.read(TABLE_UNIVERSE_MEMBERSHIP)


def evaluate_index_membership_evidence(
    frame: pd.DataFrame | None,
    *,
    definition: UniverseDefinition,
    calendar: TradingCalendar | Sequence,
    expected_sizes: Mapping[str, int],
    master: Mapping[str, SecurityMasterBoundary] | None = None,
    exceptions: Sequence[MembershipSizeException | Mapping[str, Any]]
    | None = None,
) -> AcceptanceResult:
    """Produce the mandatory ``index_membership_evidence`` result.

    ``frame`` is the dataset's ``universe_membership`` table (``None`` when
    the dataset lacks it). The definition must pin exactly the facts it
    evaluates: the table's content hash is compared against
    ``definition.membership_table_sha256`` after the pure validator found no
    fatal issue, so a mismatched definition or tampered facts both fail.
    """
    if frame is None or frame.empty:
        return _failed(
            summary=(
                "universe_membership table is missing from the pinned dataset"
            ),
            error_codes=(CODE_TABLE_MISSING,),
            hashes=_definition_hashes(definition),
        )
    issues = validate_membership_facts(
        frame,
        calendar=calendar,
        expected_sizes=expected_sizes,
        master=master,
        exceptions=exceptions,
    )
    fatal_codes = sorted(
        {issue.code for issue in issues if issue.severity is Severity.FATAL}
    )
    if fatal_codes:
        return _failed(
            summary="membership facts rejected by the acceptance validator",
            error_codes=tuple(fatal_codes),
            hashes=_definition_hashes(definition),
        )
    facts = _facts_from_frame(frame)
    if facts is None:
        return _failed(
            summary="membership rows violate the fact contract",
            error_codes=("UNIVERSE_FACT_CONTRACT_VIOLATION",),
            hashes=_definition_hashes(definition),
        )
    table_hash = membership_content_hash(facts)
    hashes = _definition_hashes(definition)
    hashes["membership_table_sha256"] = table_hash
    if table_hash != definition.membership_table_sha256:
        return _failed(
            summary=(
                "membership table hash does not match the pinned universe "
                "definition"
            ),
            error_codes=(CODE_DEFINITION_HASH_MISMATCH,),
            hashes={
                **hashes,
                "pinned_membership_table_sha256": (
                    definition.membership_table_sha256
                ),
            },
        )
    return AcceptanceResult(
        code=CODE_INDEX_MEMBERSHIP_EVIDENCE,
        status=AcceptanceStatus.PASS,
        summary="membership evidence, coverage and cardinality accepted",
        details={
            "coverage": {
                "universe_id": definition.universe_id,
                "coverage_start": definition.coverage_start.isoformat(),
                "coverage_end": definition.coverage_end.isoformat(),
            },
            "counts": {
                "fact_rows": int(len(frame)),
                "distinct_symbols": int(frame["symbol"].nunique()),
                "expected_sizes": {
                    universe: int(size)
                    for universe, size in sorted(expected_sizes.items())
                },
            },
            "hashes": hashes,
            "error_codes": [],
        },
    )


def _failed(
    *,
    summary: str,
    error_codes: tuple[str, ...],
    hashes: dict[str, str],
) -> AcceptanceResult:
    return AcceptanceResult(
        code=CODE_INDEX_MEMBERSHIP_EVIDENCE,
        status=AcceptanceStatus.FAIL,
        summary=summary,
        details={
            "hashes": hashes,
            "error_codes": sorted(set(error_codes)),
        },
    )


def _definition_hashes(definition: UniverseDefinition) -> dict[str, str]:
    return {
        "universe_id": definition.universe_id,
        "definition_version": definition.version,
        "rules_version": definition.rules_version,
        "pinned_membership_table_sha256": definition.membership_table_sha256,
    }


def _facts_from_frame(frame: pd.DataFrame) -> list[MembershipFact] | None:
    """Rebuild validated facts from table rows, or ``None`` on violation."""
    facts: list[MembershipFact] = []
    for record in frame.to_dict("records"):
        payload = dict(record)
        for column in ("raw_effective_from", "raw_effective_to",
                       "announcement_date"):
            value = payload.get(column)
            payload[column] = (
                None
                if value is None or pd.isna(value)
                else pd.Timestamp(value).date()
            )
        try:
            facts.append(MembershipFact.model_validate(payload))
        except Exception:  # noqa: BLE001 - any contract breach fails the gate
            return None
    return facts


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


class FullHistoryAcceptanceStartMissing(ValueError):
    """The build names neither acceptance anchor nor legacy requested start.

    A ``ValueError`` subclass on purpose: ``run_automated_checks`` catches
    it into a FAIL result (never a crash), while its dedicated type lets
    ``evidence_window`` distinguish it from a malformed window (spec §0-12).
    """


def _window(build: Mapping[str, Any]) -> tuple[date, date]:
    """The review window: acceptance obligation through resolved end (ADR-011).

    Anchored to ``full_history_acceptance_start`` alone (never min'd with the
    requested start -- a min'd window before the first open day fails by
    construction).  Falls back to ``requested_start_date`` for legacy and
    non-data_update origins; a build with neither is un-reviewable by design
    (bootstrap) and raises :class:`FullHistoryAcceptanceStartMissing`.  The
    anchor check precedes the end check: a bootstrap manifest carries neither
    window start *nor* ``resolved_end_date``, and the dedicated reason must
    win (spec §0-12).
    """
    start = build.get("full_history_acceptance_start")
    if not isinstance(start, str) or not start:
        start = build.get("requested_start_date")
    if not isinstance(start, str) or not start:
        raise FullHistoryAcceptanceStartMissing(
            "build_config carries neither full_history_acceptance_start nor "
            "requested_start_date"
        )
    end = build.get("resolved_end_date")
    if not isinstance(end, str):
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
