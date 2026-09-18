"""Offline-servable data update/validate pipeline over real suppliers.

Task 13 wires an operator-facing ``data update`` / ``data validate`` flow on top
of the immutable dataset publisher, the raw-store, the supplier adapters and the
shared quality vocabulary.  The pipeline is unit-tested entirely offline with
stub ``DataSource`` adapters (the constructor ``sources`` override mapping), so
no test ever imports a supplier SDK, touches a network or reads a token.

Role model (kept deliberately small and explicit):

- ``tushare`` -- the *required primary* stock daily source (unadjusted bars for
  every security listed in the carried ``security_master``).
- ``akshare`` -- the *required reference* source: one ``index_history`` request
  per configured benchmark plus best-effort corporate-action reconciliation
  (``cninfo`` / ``eastmoney``).  Corporate actions are *best-effort*: an empty
  or absent response is a legitimate empty table, and a fetch failure is a
  ``WARNING`` that never blocks.
- ``baostock`` -- the *optional validation* daily source (unadjusted).  A
  failure is a ``WARNING`` (``optional_source_failure``); the run still
  publishes.

``DataPipeline.update`` always fetches into the immutable raw-store *before*
normalizing, refreshes the trading calendar for the window from the required
relay ``trade_cal`` responses, merges the fresh canonical bars/corporate
actions over the existing immutable dataset (the security master carries over
with refreshed listing facts, and the immutable ``universe_membership`` table
is carried unchanged), renders the shared quality report, and publishes
**only** when the neutral gate passes and no ``FATAL`` pipeline issue exists.
A blocked or failed update
returns ``dataset_ref is None`` while remaining diagnosable through
``quality_report`` / ``source_status`` / ``raw_snapshots``.

When ``--end`` is omitted the end date is the newest open day of the
*published* ``trading_calendar`` -- never a wall-clock heuristic; an empty
published calendar is a FATAL that asks the operator for an explicit ``--end``.
An explicit ``--end`` bypasses resolution but the merged window still runs the
full quality checks.  This is engineering scaffolding for the MVP, not evidence
of alpha -- nothing here is investment advice.
"""

from __future__ import annotations

import json
import time as _sleep_module
import uuid
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from stock_quant.config import SourceConfig, load_project_config
from stock_quant.data_model.adjusted_bar import (
    ADJUSTMENT_NAME,
    action_id_of,
    build_adjusted_bars,
)
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.calendar_coverage import (
    ACCEPTANCE_START_KEY,
    COVERAGE_KEY,
    COVERAGE_WARNING_CODES,
    DEFINITION_HASHES_KEY,
    SKIPPED_DEFINITIONS_KEY,
    SOURCE_TUSHARE_RELAY,
    CalendarCoverageError,
    CalendarCoverageSpan,
    coverage_from_payload,
    coverage_payload,
    merge_window,
    supplier_span,
    validate_build_calendar_evidence,
)
from stock_quant.data_model.corporate_action_coverage import (
    OUTCOME_FAILED,
    OUTCOME_SUCCESS_EMPTY,
    OUTCOME_SUCCESS_EVENTS,
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_model.corporate_actions import (
    REASON_CROSS_SOURCE_CONFLICT,
    REASON_UNSUPPORTED_CORPORATE_ACTION,
    RECONCILED_COLUMNS,
    RIGHTS_SOURCE,
    CorporateActionResult,
    apply_corporate_action_reviews,
    filter_corporate_actions_to_window,
    normalize_corporate_actions,
    normalize_rights_issue_actions,
    prepare_allotment_rights_frame,
    prepare_cninfo_dividend_frame,
    prepare_eastmoney_dividend_frame,
    quarantine_row_out_of_window_reason,
)
from stock_quant.data_model.dataset import (
    DatasetNotFoundError,
    DatasetPublisher,
    DatasetReader,
    PublicationBlocked,
)
from stock_quant.data_model.normalize import normalize_daily
from stock_quant.data_model.schemas import (
    ADJUSTED_BAR_SCHEMA,
    CORPORATE_ACTION_COLUMNS,
    CORPORATE_ACTION_QUARANTINE_COLUMNS,
    DAILY_COLUMNS,
    DAILY_SCHEMA,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
    UNIVERSE_MEMBERSHIP_COLUMNS,
)
from stock_quant.data_model.security_master import (
    MASTER_SOURCE_STOCK_BASIC,
    ListStatus,
    master_coverage_frame,
    master_coverage_record,
)
from stock_quant.data_model.suspensions import (
    canonicalize_supplier_suspensions,
    suspension_rows,
)
from stock_quant.data_model.trade_calendar_facts import (
    EXCHANGES as CALENDAR_EXCHANGES,
)
from stock_quant.data_model.trade_calendar_facts import (
    ExchangeCalendarFacts,
    TradeCalendarFactError,
    check_exchange_agreement,
    check_pretrade_continuity,
    materialize_open_days,
    parse_trade_cal_frame,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import (
    CODE_ADJUSTED_BAR_MISSING_RAW,
    CODE_ADJUSTED_BAR_RAW_CLOSE_MISMATCH,
    CODE_ADJUSTED_BAR_UNKNOWN_ACTION,
    CODE_ADJUSTED_BAR_WRONG_BASIS,
    CODE_QUARANTINE_OUT_OF_WINDOW,
    TABLE_CORPORATE_ACTION,
    QualityIssue,
    QualityReport,
    Severity,
)
from stock_quant.data_quality.raw_checks import (
    TABLE_UNIVERSE_MEMBERSHIP,
    check_daily_values,
    check_primary_key_conflicts,
    check_provenance,
    check_schema,
    classify_missing_row,
    validate_membership_facts,
)
from stock_quant.data_sources.base import (
    AuthenticationError,
    DataRequest,
    DataSource,
    RetryPolicy,
    fetch_with_retry,
    translate_supplier_error,
)
from stock_quant.data_sources.raw_store import (
    RawSnapshot,
    RawSnapshotEvidence,
    RawStore,
)
from stock_quant.project_root import resolve_project_root
from stock_quant.research.universe import (
    UniverseCoverageError,
    load_universe_coverage_criterion,
)

# --------------------------------------------------------------------------- #
# Pipeline-level issue codes (kept out of the shared neutral-gate vocabulary:
# they are gate conditions owned by this module, never by the dataset gate).
# --------------------------------------------------------------------------- #

CODE_SOURCE_FETCH_FAILED = "source_fetch_failed"
CODE_REQUIRED_SOURCE_DISABLED = "required_source_disabled"
CODE_NO_CURRENT_DATASET = "no_current_dataset"
CODE_CALENDAR_EMPTY_NO_END = "calendar_empty_requires_explicit_end"
CODE_OPTIONAL_SOURCE_FAILURE = "optional_source_failure"
CODE_UNIVERSE_MASTER_MISMATCH = "universe_master_mismatch"
CODE_MASTER_SNAPSHOT_INCOMPLETE = "master_snapshot_incomplete"
CODE_MASTER_BAR_BOUNDARY = "master_bar_boundary"
CODE_MASTER_COVERAGE_MISMATCH = "master_coverage_mismatch"
CODE_UNIVERSE_DEFINITION_INVALID = "universe_definition_invalid"
CODE_ADJUSTED_BAR_MISSING_FROM_DATASET = "adjusted_bar_missing_from_dataset"

#: Canonical standardized table holding untrusted corporate-action rows.
TABLE_CORPORATE_ACTION_QUARANTINE = "corporate_action_quarantine"

#: Contract version of the sanitized ``build_config`` this pipeline writes into
#: every published dataset manifest (and which is hashed into the version id).
DATASET_BUILD_CONTRACT_VERSION = 1

_CONFIGURED_SOURCES = ("tushare", "akshare", "baostock")
_REQUIRED_ROLE = {"tushare": True, "akshare": True, "baostock": False}

#: Corporate-action interfaces requested per symbol, as ``(endpoint, bucket)``.
#: Every one of them must answer before a window may read ``VERIFIED``, so the
#: list is the single place that decides how many ways a window can fail.
CORPORATE_ACTION_ENDPOINTS = (
    ("cninfo_corporate_actions", "cninfo"),
    ("eastmoney_corporate_actions", "eastmoney"),
    ("rights_issue_corporate_actions", RIGHTS_SOURCE),
)

#: Adapter that turns each endpoint's supplier layout into the reconciliation
#: layout.  A mapping rather than an if/else chain: an interface added later
#: must name its own adapter instead of silently inheriting another's.
CORPORATE_ACTION_PREPARERS = {
    "cninfo_corporate_actions": prepare_cninfo_dividend_frame,
    "eastmoney_corporate_actions": prepare_eastmoney_dividend_frame,
    "rights_issue_corporate_actions": prepare_allotment_rights_frame,
}


# --------------------------------------------------------------------------- #
# Public value types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceStatus:
    """Outcome of one configured source within an update.

    ``reason`` is free operator-facing prose; ``reason_code`` is the stable,
    sanitized code that (and only that) travels into dataset build evidence.
    """

    source: str
    required: bool
    ok: bool
    reason: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class DataUpdateRequest:
    """Idempotent request for one data update run.

    ``end_date`` ``None`` resolves the end to the newest open day of the
    published ``trading_calendar``; ``sources`` restricts to a subset of the
    configured names.
    """

    start_date: date | None = None
    end_date: date | None = None
    sources: tuple[str, ...] | None = None


@dataclass(frozen=True)
class DataUpdateResult:
    """One update's outcome: always diagnosable, published only when gated."""

    quality_report: QualityReport
    dataset_ref: Any
    run_id: str
    resolved_end_date: date | None
    source_status: tuple[SourceStatus, ...]
    raw_snapshots: tuple[str, ...]


def _source_evidence(
    statuses: Mapping[str, SourceStatus],
) -> list[dict[str, object]]:
    """Sanitized per-source rows: identifiers and stable codes only.

    ``reason`` prose never enters build evidence -- a missing code falls back
    to ``ok`` / ``unspecified_failure`` so the row stays diagnosable without
    leaking exception text.  Rows are sorted by source so identical builds
    render identical evidence.
    """
    return [
        {
            "source": status.source,
            "required": status.required,
            "ok": status.ok,
            "reason_code": status.reason_code
            or ("ok" if status.ok else "unspecified_failure"),
        }
        for status in sorted(statuses.values(), key=lambda row: row.source)
    ]


def _raw_snapshot_evidence_rows(
    snapshots: Sequence[RawSnapshot],
) -> list[dict[str, str]]:
    """Sanitized raw-snapshot rows, deduplicated and deterministically sorted."""
    unique: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for snapshot in snapshots:
        evidence = RawSnapshotEvidence.from_snapshot(snapshot)
        row = asdict(evidence)
        key = (
            evidence.source,
            evidence.endpoint,
            evidence.transport_id or "",
            evidence.request_key,
            evidence.file_sha256,
        )
        unique[key] = row
    return [unique[key] for key in sorted(unique)]


def dataset_build_config(
    *,
    run_id: str,
    request: DataUpdateRequest,
    effective_start_date: date,
    resolved_end_date: date,
    statuses: Mapping[str, SourceStatus],
    raw_snapshots: Sequence[RawSnapshot],
    calendar_spans: Sequence[CalendarCoverageSpan],
    acceptance_start: date | None,
    definition_hashes: Mapping[str, str],
    skipped_definitions: Sequence[str],
) -> dict[str, object]:
    """The exact sanitized payload hashed into a published dataset version.

    Carries hashes, identifiers, dates and stable status codes only -- never
    exception text, URLs, local paths or reason prose -- so the dataset
    version is bound to the raw evidence it was built from.  The calendar
    evidence keys (``calendar_coverage`` and siblings) are the shape marker
    for the build-config contract: ``DATASET_BUILD_CONTRACT_VERSION`` stays
    ``1`` on purpose, so a manifest without ``calendar_coverage`` is a legacy
    payload read compatibly but never accepted as fully evidenced.
    """
    return {
        "origin": "data_update",
        "pipeline_contract_version": DATASET_BUILD_CONTRACT_VERSION,
        "run_id": run_id,
        "requested_start_date": (
            request.start_date.isoformat() if request.start_date else None
        ),
        "effective_start_date": effective_start_date.isoformat(),
        "requested_end_date": (
            request.end_date.isoformat() if request.end_date else None
        ),
        "resolved_end_date": resolved_end_date.isoformat(),
        "source_status": _source_evidence(statuses),
        "raw_snapshots": _raw_snapshot_evidence_rows(raw_snapshots),
        COVERAGE_KEY: coverage_payload(calendar_spans),
        ACCEPTANCE_START_KEY: (
            acceptance_start.isoformat() if acceptance_start is not None else None
        ),
        DEFINITION_HASHES_KEY: dict(definition_hashes),
        SKIPPED_DEFINITIONS_KEY: list(skipped_definitions),
    }


def _calendar_open_days(calendar_frame: pd.DataFrame) -> tuple[date, ...]:
    """The published open days of one ``trading_calendar`` frame, sorted."""
    return tuple(
        sorted(
            day.date()
            for day, flag in zip(
                calendar_frame["calendar_date"],
                calendar_frame["is_trading_day"],
            )
            if bool(flag)
        )
    )


def _calendar_issues(
    violations: Sequence[tuple[str, Mapping[str, object]]],
) -> list[QualityIssue]:
    """Pipeline issues for calendar violations (one shared severity map)."""
    return [
        _issue(
            Severity.WARNING if code in COVERAGE_WARNING_CODES else Severity.FATAL,
            code,
            table="trading_calendar",
            details=dict(details),
        )
        for code, details in violations
    ]


def master_bar_boundary_issues(
    master: pd.DataFrame, daily: pd.DataFrame
) -> list[QualityIssue]:
    """WARNING when a bar row lies outside its symbol's listing window.

    A bar before ``list_date`` (or after ``delist_date``) contradicts the
    refreshed master facts.  Missing rows are *not* this check's job -- they
    are classified separately by ``classify_missing_row``.  A WARNING never
    blocks publication; it surfaces a source-vs-master disagreement.
    """
    if daily is None or daily.empty:
        return []
    bounds = {
        str(row["symbol"]): (_as_date(row["list_date"]),
                             _as_date(row["delist_date"]))
        for row in master.to_dict("records")
    }
    issues: list[QualityIssue] = []
    for record in daily.to_dict("records"):
        symbol = str(record["symbol"])
        list_date, delist_date = bounds.get(symbol, (None, None))
        if list_date is None and delist_date is None:
            continue
        trade_date = _as_date(record["trade_date"])
        if trade_date is None:
            continue
        if list_date is not None and trade_date < list_date:
            boundary = "before_list_date"
        elif delist_date is not None and trade_date > delist_date:
            boundary = "after_delist_date"
        else:
            continue
        issues.append(
            _issue(
                Severity.WARNING,
                CODE_MASTER_BAR_BOUNDARY,
                symbol=symbol,
                trade_date=trade_date,
                table="daily_bar",
                details={
                    "boundary": boundary,
                    "list_date": list_date.isoformat()
                    if list_date is not None else None,
                    "delist_date": delist_date.isoformat()
                    if delist_date is not None else None,
                },
            )
        )
    return issues


# --------------------------------------------------------------------------- #
# DataPipeline
# --------------------------------------------------------------------------- #


class DataPipeline:
    """Fetch, normalise, quality-check and publish one data update."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        sources: Mapping[str, DataSource] | None = None,
        sleeper=_sleep_module.sleep,
    ) -> None:
        # One validated root: config, dataset and raw-store paths all derive
        # from it, and an invalid root fails here -- before any source, raw
        # store, staging or network path is ever reached.
        self._project_root = resolve_project_root(project_root)
        self._overrides = dict(sources or {})
        self._sleeper = sleeper
        self._project_config = load_project_config(self._project_root)
        self._raw_store = RawStore(self._project_root)

    # -- public surface --------------------------------------------------- #

    def validate(self, version: str | None = None) -> QualityReport:
        """Re-run the shared quality checks over a published dataset version.

        ``version`` defaults to the ``CURRENT`` dataset.  The report carries the
        same issue vocabulary as a publish-time report and is gate-evaluable,
        but never publishes anything.
        """
        publisher = DatasetPublisher(self._project_root)
        if version is None:
            version = publisher.current().version
        issues: list[QualityIssue] = []
        reader = DatasetReader(self._project_root)
        with reader.open(version) as context:
            daily = context.read("daily_bar")
            issues.extend(
                check_schema(daily, DAILY_SCHEMA, table="daily_bar")
            )
            issues.extend(
                check_primary_key_conflicts(daily, table="daily_bar")
            )
            issues.extend(check_daily_values(daily, table="daily_bar"))
            issues.extend(check_provenance(daily, table="daily_bar"))
            master = context.read("security_master")
            calendar_frame = context.read("trading_calendar")
            build = (
                context.manifest.get("build_config")
                if isinstance(context.manifest, Mapping)
                else None
            )
            membership = None
            if TABLE_UNIVERSE_MEMBERSHIP in context.tables:
                membership = context.read(TABLE_UNIVERSE_MEMBERSHIP)
            coverage = None
            if "security_master_coverage" in context.tables:
                coverage = context.read("security_master_coverage")
            missing_tables = [
                name
                for name in (
                    TABLE_CORPORATE_ACTION,
                    "adjusted_bar",
                    TABLE_CORPORATE_ACTION_QUARANTINE,
                )
                if name not in context.tables
            ]
            if missing_tables:
                # Fail closed: the publisher accepts table subsets silently, so
                # a legacy (or trimmed) dataset without these canonical tables
                # must never validate clean -- and reading a missing table
                # would otherwise crash with a bare ValueError.
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_ADJUSTED_BAR_MISSING_FROM_DATASET,
                        table="adjusted_bar",
                        details={
                            "message": (
                                "published dataset is missing required "
                                "canonical tables; run a full data update to "
                                "republish"
                            ),
                            "missing_tables": missing_tables,
                        },
                    )
                )
            else:
                corporate_actions = context.read(TABLE_CORPORATE_ACTION)
                adjusted = context.read("adjusted_bar")
                issues.extend(
                    check_schema(
                        adjusted, ADJUSTED_BAR_SCHEMA, table="adjusted_bar"
                    )
                )
                issues.extend(
                    check_primary_key_conflicts(
                        adjusted, table="adjusted_bar"
                    )
                )
                issues.extend(
                    check_adjusted_bar_lineage(
                        adjusted, daily, corporate_actions
                    )
                )
        issues.extend(self._universe_master_issues(master))
        issues.extend(self._membership_issues(membership, calendar_frame))
        issues.extend(self._master_bar_boundary_issues(master, daily))
        issues.extend(self._master_coverage_consistency_issues(master, coverage))
        issues.extend(
            _calendar_issues(
                validate_build_calendar_evidence(
                    build, open_days=_calendar_open_days(calendar_frame)
                )
            )
        )
        return QualityReport(issues=tuple(issues))

    def update(self, request: DataUpdateRequest) -> DataUpdateResult:
        """Run one gated, raw-preserving data update."""
        run_id = f"data_update_{uuid.uuid4().hex[:12]}"
        statuses: dict[str, SourceStatus] = {}
        issues: list[QualityIssue] = []
        raw_snapshots: list[RawSnapshot] = []

        enabled = self._enabled_names(request)
        for name in _CONFIGURED_SOURCES:
            statuses[name] = SourceStatus(
                source=name,
                required=_REQUIRED_ROLE[name],
                ok=False,
                reason="not_run",
                reason_code="not_run",
            )

        # ---- the carried, immutable baseline --------------------------- #
        baseline = self._read_baseline(issues)
        if baseline is None:
            statuses = {name: status for name, status in statuses.items()}
            return self._result(
                issues, None, run_id, None, statuses, raw_snapshots
            )
        (
            master,
            published_open_days,
            current_daily,
            current_ca,
            membership,
            current_quarantine,
            baseline_spans,
        ) = baseline
        issues.extend(self._universe_master_issues(master))

        # ---- universe definitions: a config fault, read before any fetch -- #
        # The criterion is computed from ``configs/universes`` and is only *used*
        # at publish time, but it is read here so a broken definition directory
        # fails in seconds instead of after the whole fetch-and-ingest window.
        try:
            criterion = load_universe_coverage_criterion(
                self._project_root / "configs" / "universes"
            )
        except UniverseCoverageError as error:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_UNIVERSE_DEFINITION_INVALID,
                    details={"message": str(error)},
                )
            )
            return self._result(issues, None, run_id, None, statuses, raw_snapshots)

        # ---- required-source availability gate -------------------------- #
        required_blocked = self._require_available(enabled, issues, statuses)
        if required_blocked:
            return self._result(
                issues,
                None,
                run_id,
                None,
                statuses,
                raw_snapshots,
            )

        # ---- end resolution: the published calendar is the only clock ----- #
        end = request.end_date
        if end is None:
            if not published_open_days:
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_CALENDAR_EMPTY_NO_END,
                        details={
                            "message": (
                                "the published trading_calendar has no open day; "
                                "pass an explicit --end"
                            )
                        },
                    )
                )
                return self._result(
                    issues, None, run_id, None, statuses, raw_snapshots
                )
            end = published_open_days[-1]
        start = request.start_date or self._project_config.start_date
        if end < start:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_SOURCE_FETCH_FAILED,
                    details={
                        "message": "request end_date precedes start_date",
                        "source": "request",
                    },
                )
            )
            return self._result(
                issues, None, run_id, end, statuses, raw_snapshots
            )

        # ---- required trading-calendar refresh --------------------------- #
        # Runs before every other fetch so a calendar failure blocks the run
        # before any window data is pulled, and so the whole update publishes
        # as one atomic unit with its calendar evidence.
        refreshed = self._refresh_calendar(
            start,
            end,
            published_open_days,
            issues,
            statuses,
            raw_snapshots,
            baseline_spans,
        )
        if refreshed is None:
            return self._result(
                issues, None, run_id, end, statuses, raw_snapshots
            )
        calendar_open, calendar_spans = refreshed

        # ---- required security-master reference (tushare stock_basic) ----- #
        master, master_coverage = self._refresh_security_master(
            start, end, issues, statuses, raw_snapshots, master,
        )
        if master is None:
            return self._result(
                issues, None, run_id, end, statuses, raw_snapshots,
            )

        # ---- required primary stock daily ------------------------------- #
        equity_symbols = _equity_symbols(master)
        primary_rows: list[pd.DataFrame] = []
        primary_dates: set[tuple[str, date]] = set()
        raw_daily_frames: dict[str, pd.DataFrame] = {}
        fatal = self._fetch_primary_stock(
            enabled,
            equity_symbols,
            start,
            end,
            issues,
            statuses,
            raw_snapshots,
            primary_rows,
            primary_dates,
            raw_daily_frames,
        )
        if fatal:
            return self._result(
                issues,
                None,
                run_id,
                end,
                statuses,
                raw_snapshots,
            )

        # ---- proof-only pre-window anchors ------------------------------ #
        # Runs before _materialize_suspensions reads the raw frames, and after
        # the primary fetch whose responses it deepens.
        self._deepen_head_anchors(
            enabled,
            equity_symbols,
            master,
            calendar_open,
            start,
            end,
            issues,
            statuses,
            raw_snapshots,
            raw_daily_frames,
        )

        # ---- required benchmark history --------------------------------- #
        benchmark_symbols = tuple(self._project_config.benchmark_symbols)
        benchmark_rows: list[pd.DataFrame] = []
        benchmark_dates: set[date] = set()
        fatal = self._fetch_benchmarks(
            enabled,
            benchmark_symbols,
            start,
            end,
            issues,
            statuses,
            raw_snapshots,
            benchmark_rows,
            benchmark_dates,
        )
        if fatal:
            return self._result(
                issues,
                None,
                run_id,
                end,
                statuses,
                raw_snapshots,
            )

        # ---- best-effort corporate actions ------------------------------ #
        corporate_action = current_ca
        corporate_action_quarantine = current_quarantine
        coverage = coverage_frame([])
        if "akshare" in enabled:
            corporate_action, coverage, corporate_action_quarantine = (
                self._refresh_corporate_actions(
                    enabled,
                    equity_symbols,
                    start,
                    end,
                    issues,
                    statuses,
                    raw_snapshots,
                    current_ca,
                    current_quarantine,
                )
            )

        # ---- optional validation daily ---------------------------------- #
        validation_rows: list[pd.DataFrame] = []
        if "baostock" in enabled:
            self._fetch_validation_daily(
                enabled,
                equity_symbols,
                start,
                end,
                issues,
                statuses,
                raw_snapshots,
                validation_rows,
            )

        # ---- quality report over the merged canonical daily ------------- #
        ingested = pd.Timestamp.now(tz="UTC")
        self._materialize_suspensions(
            raw_daily_frames,
            equity_symbols,
            master,
            calendar_open,
            start,
            end,
            corporate_action,
            primary_rows,
            primary_dates,
            issues,
            ingested,
        )
        new_daily = self._merge_daily(
            current_daily,
            primary_rows,
            benchmark_rows,
            equity_symbols,
            benchmark_symbols,
            start,
            end,
            ingested,
        )
        issues.extend(check_schema(new_daily, DAILY_SCHEMA, table="daily_bar"))
        issues.extend(check_primary_key_conflicts(new_daily, table="daily_bar"))
        issues.extend(check_daily_values(new_daily, table="daily_bar"))
        issues.extend(check_provenance(new_daily, table="daily_bar"))
        issues.extend(
            self._missing_issues(
                equity_symbols,
                master,
                start,
                end,
                benchmark_dates,
                primary_dates,
                _symbol_dates(validation_rows),
                ingested,
            )
        )
        issues.extend(self._master_bar_boundary_issues(master, new_daily))

        # ---- total-return bars over the merged canonical daily ---------- #
        adjusted = build_adjusted_bars(
            new_daily,
            corporate_action,
            corporate_action_quarantine,
            coverage,
            symbols=equity_symbols,
        )
        issues.extend(
            check_schema(adjusted, ADJUSTED_BAR_SCHEMA, table="adjusted_bar")
        )
        issues.extend(
            check_primary_key_conflicts(adjusted, table="adjusted_bar")
        )

        report = QualityReport(issues=tuple(issues))
        decision = evaluate_publication(report)
        fatal_present = any(
            item.severity is Severity.FATAL for item in report.issues
        )
        if not decision.passed or fatal_present:
            return self._result(
                issues,
                None,
                run_id,
                end,
                statuses,
                raw_snapshots,
            )

        # ---- publish ---------------------------------------------------- #
        tables = {
            "daily_bar": new_daily,
            "adjusted_bar": adjusted,
            "security_master": master[list(SECURITY_MASTER_COLUMNS)],
            "security_master_coverage": master_coverage,
            "corporate_action": corporate_action[
                list(CORPORATE_ACTION_COLUMNS)
            ],
            "corporate_action_quarantine": corporate_action_quarantine[
                list(CORPORATE_ACTION_QUARANTINE_COLUMNS)
            ],
            "corporate_action_coverage": coverage,
            "trading_calendar": _calendar_frame(calendar_open)[
                list(TRADING_CALENDAR_COLUMNS)
            ],
        }
        if membership is not None:
            # Membership facts are immutable qualification records: an update
            # never rewrites them, it carries the registered raw table
            # unchanged so every published dataset keeps one auditable
            # membership version (appends happen only through the explicit
            # membership refresh workflow).
            tables[TABLE_UNIVERSE_MEMBERSHIP] = membership[
                list(UNIVERSE_MEMBERSHIP_COLUMNS)
            ]
        try:
            dataset_ref = DatasetPublisher(self._project_root).publish(
                tables,
                report,
                build_config=dataset_build_config(
                    run_id=run_id,
                    request=request,
                    effective_start_date=start,
                    resolved_end_date=end,
                    statuses=statuses,
                    raw_snapshots=raw_snapshots,
                    calendar_spans=calendar_spans,
                    acceptance_start=criterion.acceptance_start,
                    definition_hashes=criterion.definition_hashes,
                    skipped_definitions=criterion.skipped,
                ),
            )
        except PublicationBlocked as error:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_SOURCE_FETCH_FAILED,
                    details={"message": str(error)},
                )
            )
            return self._result(
                issues,
                None,
                run_id,
                end,
                statuses,
                raw_snapshots,
            )
        return self._result(
            issues,
            dataset_ref,
            run_id,
            end,
            statuses,
            raw_snapshots,
        )

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _enabled_names(self, request: DataUpdateRequest) -> frozenset[str]:
        enabled = {
            name
            for name, config in self._project_config.sources.items()
            if config.enabled
        }
        if request.sources is not None:
            allowed = set(request.sources)
            enabled = {name for name in enabled if name in allowed}
        return frozenset(enabled)

    def _universe_master_issues(
        self, master: pd.DataFrame
    ) -> list[QualityIssue]:
        """Compare ``configs/universe.yml`` with the pinned ``security_master``.

        The universe names the engineering universe that must be carried by the
        published dataset; a dataset whose ``security_master`` disagrees cannot
        be validated or advanced.  A disagreement is a ``FATAL`` coded issue so
        it both surfaces in ``data validate`` and blocks ``data update``
        publication.
        """
        universe = Universe.from_yaml(
            self._project_root / "configs" / "universe.yml"
        )
        master_symbols = set(str(value) for value in master["symbol"])
        universe_symbols = set(universe.symbols)
        missing = sorted(universe_symbols - master_symbols)
        extra = sorted(master_symbols - universe_symbols)
        if not missing and not extra:
            return []
        return [
            _issue(
                Severity.FATAL,
                CODE_UNIVERSE_MASTER_MISMATCH,
                table="security_master",
                details={
                    "message": (
                        "pinned security_master disagrees with "
                        "configs/universe.yml"
                    ),
                    "universe_symbol_count": len(universe_symbols),
                    "security_master_symbol_count": len(master_symbols),
                    "missing_from_security_master": missing,
                    "extra_in_security_master": extra,
                },
            )
        ]

    def _master_bar_boundary_issues(
        self, master: pd.DataFrame, daily: pd.DataFrame
    ) -> list[QualityIssue]:
        """WARNING when a bar row lies outside its symbol's listing window.

        A bar before ``list_date`` (or after ``delist_date``) contradicts the
        refreshed master facts.  Missing rows are *not* this check's job -- they
        are classified separately by ``classify_missing_row``.  A WARNING never
        blocks publication; it surfaces a source-vs-master disagreement.
        """
        return master_bar_boundary_issues(master, daily)

    def _master_coverage_consistency_issues(
        self, master: pd.DataFrame, coverage: pd.DataFrame | None
    ) -> list[QualityIssue]:
        """Validate-only FATAL when evidence is absent or disagrees with master.

        Never runs inside ``update()``: the refresh builds the coverage rows
        from the very master it publishes, so a mismatch there is impossible;
        the check guards the *stored* dataset against drift, tampering or an
        older schema.
        """
        covered = _covered_symbols(coverage)
        missing = sorted(
            {str(row["symbol"]) for row in master.to_dict("records")} - covered
        )
        if missing:
            return [
                _issue(
                    Severity.FATAL,
                    CODE_MASTER_COVERAGE_MISMATCH,
                    table="security_master",
                    details={
                        "message": (
                            "security_master_coverage is missing rows for "
                            "pinned symbols"
                        ),
                        "missing": missing,
                    },
                )
            ]
        facts = {
            str(row["symbol"]): row
            for row in coverage.to_dict("records")
        }
        disagree: list[str] = []
        for record in master.to_dict("records"):
            symbol = str(record["symbol"])
            fact = facts[symbol]
            if (
                _as_date(record["list_date"]) != _as_date(fact["list_date"])
                or _as_date(record["delist_date"])
                != _as_date(fact["delist_date"])
                or str(record["list_status"]) != str(fact["list_status"])
            ):
                disagree.append(symbol)
        if not disagree:
            return []
        return [
            _issue(
                Severity.FATAL,
                CODE_MASTER_COVERAGE_MISMATCH,
                table="security_master",
                details={
                    "message": "security_master disagrees with its coverage "
                    "evidence",
                    "symbols": sorted(disagree),
                },
            )
        ]

    def _membership_issues(
        self,
        membership: pd.DataFrame | None,
        calendar_frame: pd.DataFrame,
    ) -> list[QualityIssue]:
        """Audit the carried ``universe_membership`` table, when present.

        Row-level fact checks run through ``validate_membership_facts`` with
        an empty ``expected_sizes`` mapping: evidence, symbols, intervals,
        overlap, announcement visibility and (with no master snapshot in this
        context) convention checks only. Cardinality acceptance belongs to the
        research-facing membership acceptance gate, which pins expected sizes,
        master boundaries and official exceptions. Datasets predating the
        membership table carry no rows and therefore raise nothing here.
        """
        if membership is None:
            return []
        open_days = _calendar_open_days(calendar_frame)
        return validate_membership_facts(
            membership,
            calendar=TradingCalendar.from_open_days(open_days),
            expected_sizes={},
        )

    def _source(self, name: str) -> DataSource:
        override = self._overrides.get(name)
        if override is not None:
            return override
        config = self._project_config.sources[name]
        try:
            return _build_source(name, config)
        except KeyError as error:
            raise AuthenticationError(
                f"source {name!r} requires {error.args[0]} to be configured"
            ) from None
        except Exception as error:  # noqa: BLE001 - surface cleanly to the CLI
            raise AuthenticationError(
                f"cannot initialise source {name!r}: {translate_supplier_error(error)}"
            ) from None

    def _read_baseline(self, issues: list[QualityIssue]):
        """The carried master/calendar/daily/action/membership/quarantine state.

        Returns ``(master, open_days, daily, ca, membership, quarantine,
        baseline_spans)``, or ``None`` after a FATAL issue when no dataset
        exists yet.  The immutable ``universe_membership`` raw table is
        carried too when the baseline dataset already has one (older datasets
        simply update without it), and a pre-migration dataset without a
        ``corporate_action_quarantine`` table yields an empty canonical frame
        so it stays updatable.  ``baseline_spans`` parses the manifest's
        ``calendar_coverage`` evidence; a legacy manifest without the key
        yields no spans and the publish-time span gate then requires this
        window to cover the whole published calendar range.
        """
        try:
            ref = DatasetPublisher(self._project_root).current()
        except DatasetNotFoundError:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_NO_CURRENT_DATASET,
                    details={
                        "message": (
                            "no published dataset to update; a dataset carrying "
                            "security_master and trading_calendar must exist first"
                        )
                    },
                )
            )
            return None
        reader = DatasetReader(self._project_root)
        manifest: Mapping[str, Any] | None = None
        with reader.open(ref.version) as context:
            manifest = context.manifest
            master = context.read("security_master")
            calendar_frame = context.read("trading_calendar")
            daily = context.read("daily_bar")
            ca = context.read(TABLE_CORPORATE_ACTION)
            membership = (
                context.read(TABLE_UNIVERSE_MEMBERSHIP)
                if TABLE_UNIVERSE_MEMBERSHIP in context.tables
                else None
            )
            if TABLE_CORPORATE_ACTION_QUARANTINE in context.tables:
                quarantine = context.read(TABLE_CORPORATE_ACTION_QUARANTINE)
            else:
                # A pre-migration dataset carries no quarantine table; update
                # it by seeding the canonical empty frame instead of failing.
                quarantine = pd.DataFrame(
                    columns=CORPORATE_ACTION_QUARANTINE_COLUMNS
                )
        open_days = _calendar_open_days(calendar_frame)
        build = manifest.get("build_config") if isinstance(manifest, Mapping) else None
        raw_spans = build.get(COVERAGE_KEY, []) if isinstance(build, Mapping) else []
        try:
            spans = coverage_from_payload(raw_spans)
        except CalendarCoverageError as error:
            issues.extend(_calendar_issues(error.violations))
            return None
        return master, open_days, daily, ca, membership, quarantine, spans

    def _require_available(
        self,
        enabled: frozenset[str],
        issues: list[QualityIssue],
        statuses: dict[str, SourceStatus],
    ) -> bool:
        """Mark unavailable required sources; True when any is missing."""
        blocked = False
        for name in _CONFIGURED_SOURCES:
            if _REQUIRED_ROLE[name] and name not in enabled:
                blocked = True
                statuses[name] = SourceStatus(
                    source=name,
                    required=True,
                    ok=False,
                    reason=f"required source {name!r} is not enabled in config",
                    reason_code="required_source_disabled",
                )
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_REQUIRED_SOURCE_DISABLED,
                        symbol=None,
                        details={"source": name},
                    )
                )
        return blocked

    def _fetch_primary_stock(
        self,
        enabled,
        symbols,
        start,
        end,
        issues,
        statuses,
        raw_snapshots,
        primary_rows,
        primary_dates,
        raw_daily_frames,
    ) -> bool:
        if "tushare" not in enabled:
            return False
        source = self._adapter_or_fail("tushare", statuses)
        if source is None:
            return True
        for symbol in symbols:
            result = self._dispatch(
                "tushare", source, "daily", symbol, start, end,
                {"adjustment": "unadjusted"}, required=True, issues=issues,
            )
            if result is None:
                statuses["tushare"] = SourceStatus(
                    "tushare", True, False,
                    reason=f"required fetch failed for {symbol}",
                    reason_code="source_fetch_failed",
                )
                return True
            raw_snapshots.append(self._record_raw(result))
            # The raw response (pre_close in particular) is the suspension
            # evidence consumed later by _materialize_suspensions.
            raw_daily_frames[symbol] = result.frame
            clean = normalize_daily(
                result.frame, "tushare", _ingest_time(result.metadata)
            )
            # The supplier sometimes returns a suspended session as a row with
            # zero open/high/low, zero volume and a carried close.  Canonicalize
            # it to the suspension bar it is, before the value checks see it as
            # a zero-priced bar; the raw response keeps the original evidence.
            valid, suspension_issues = canonicalize_supplier_suspensions(
                symbol,
                clean.valid,
                result.frame,
                ingested_at=_ingest_time(result.metadata),
            )
            issues.extend(suspension_issues)
            primary_rows.append(valid)
            # (symbol, date) pairs: _missing_issues tests tuple membership, so
            # bare dates here would warn on every open day of every symbol.
            primary_dates.update(
                zip(valid["symbol"], valid["trade_date"].dt.date)
            )
        statuses["tushare"] = SourceStatus(
            "tushare", True, True, reason_code="ok"
        )
        return False

    def _deepen_head_anchors(
        self,
        enabled,
        symbols,
        master,
        calendar_open,
        start,
        end,
        issues,
        statuses,
        raw_snapshots,
        raw_daily_frames,
    ) -> None:
        """Fetch a proof-only pre-window anchor for head-suspended symbols.

        A suspension run that *opens* the window has no ``before`` bar inside
        the requested range, so ``suspension_rows`` cannot prove it and the run
        stays an unproven gap.  The bar that proves it exists -- it is simply
        outside the range the primary fetch asked for.  For the symbols whose
        window opens inside a run this walks backwards from ``start`` until it
        finds a bar or reaches the symbol's own ``list_date``; the frame it
        finds is appended to ``raw_daily_frames[symbol]``, which is
        ``_materialize_suspensions``' proof input.

        Proof input only: the deepened frame is never normalized, never enters
        ``primary_rows``/``primary_dates``, and so never reaches the published
        ``daily_bar``.  A failed probe is not fatal -- the run degrades to the
        pre-existing behaviour, an unproven head run that stays an honest gap.
        """
        if "tushare" not in enabled or not raw_daily_frames:
            return
        first_open_day = next(
            (
                day
                for day in calendar_open
                if isinstance(day, date) and start <= day <= end
            ),
            None,
        )
        if first_open_day is None:
            return
        source = self._adapter_or_fail("tushare", statuses)
        if source is None:
            return
        listing = {
            str(row["symbol"]): _as_date(row.get("list_date"))
            for row in master.to_dict("records")
        }
        for symbol in symbols:
            raw = raw_daily_frames.get(symbol)
            if not _carries_proof_chain(raw) or _has_bar_on(raw, first_open_day):
                continue
            list_date = listing.get(symbol)
            if list_date is None or list_date >= start:
                continue
            chunk_end = start - timedelta(days=1)
            while chunk_end >= list_date:
                chunk_start = max(
                    list_date, chunk_end - timedelta(days=_ANCHOR_PROBE_DAYS - 1)
                )
                result = self._dispatch(
                    "tushare", source, "daily", symbol, chunk_start, chunk_end,
                    {"adjustment": "unadjusted"}, required=False, issues=issues,
                )
                if result is None:
                    break
                raw_snapshots.append(self._record_raw(result))
                if not result.frame.empty:
                    raw_daily_frames[symbol] = pd.concat(
                        [result.frame, raw], ignore_index=True
                    )
                    break
                chunk_end = chunk_start - timedelta(days=1)

    def _materialize_suspensions(
        self,
        raw_daily_frames,
        equity_symbols,
        master,
        calendar_open,
        start,
        end,
        corporate_action,
        primary_rows,
        primary_dates,
        issues,
        ingested,
    ) -> None:
        """Append proven suspension bars for every equity symbol in place.

        The raw tushare ``daily`` response proves each absent open day: when
        the next present row's ``pre_close`` chains to the last close, the
        missing days were suspended, not lost.  See
        ``data_model/suspensions.py`` for the proof rules; chain frames from
        responses without ``pre_close`` (offline stubs) are skipped silently.
        """
        listing = {
            str(row["symbol"]): (
                _as_date(row.get("list_date")),
                _as_date(row.get("delist_date")),
            )
            for row in master.to_dict("records")
        }
        window = [
            day
            for day in calendar_open
            if isinstance(day, date) and start <= day <= end
        ]
        for symbol in equity_symbols:
            raw = raw_daily_frames.get(symbol)
            if raw is None or "pre_close" not in raw.columns:
                continue
            date_column = next(
                (name for name in ("trade_date", "date") if name in raw.columns),
                None,
            )
            if date_column is None or "close" not in raw.columns:
                continue
            chain = pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(raw[date_column].astype(str)),
                    "close": pd.to_numeric(raw["close"]),
                    "pre_close": pd.to_numeric(raw["pre_close"]),
                }
            )
            list_date, delist_date = listing.get(symbol, (None, None))
            rows, suspension_issues = suspension_rows(
                symbol,
                chain,
                window,
                list_date=list_date,
                delist_date=delist_date,
                actions=corporate_action,
                ingested_at=ingested,
            )
            if not rows.empty:
                primary_rows.append(rows)
                primary_dates.update(
                    zip(rows["symbol"], rows["trade_date"].dt.date)
                )
            issues.extend(suspension_issues)

    def _fetch_benchmarks(
        self,
        enabled,
        symbols,
        start,
        end,
        issues,
        statuses,
        raw_snapshots,
        benchmark_rows,
        benchmark_dates,
    ) -> bool:
        if "akshare" not in enabled:
            return False
        source = self._adapter_or_fail("akshare", statuses)
        if source is None:
            return True
        for symbol in symbols:
            result = self._dispatch(
                "akshare", source, "index_history", symbol, start, end, {},
                required=True, issues=issues,
            )
            if result is None:
                statuses["akshare"] = SourceStatus(
                    "akshare", True, False,
                    reason=f"required fetch failed for {symbol}",
                    reason_code="source_fetch_failed",
                )
                return True
            raw_snapshots.append(self._record_raw(result))
            try:
                clean = _normalize_index(result.frame, symbol, result.metadata)
            except Exception as error:  # noqa: BLE001 - required reference role
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_SOURCE_FETCH_FAILED,
                        details={
                            "source": "akshare",
                            "endpoint": "index_history",
                            "symbol": symbol,
                            "message": str(error),
                        },
                    )
                )
                statuses["akshare"] = SourceStatus(
                    "akshare", True, False, reason=str(error),
                    reason_code="source_fetch_failed",
                )
                return True
            benchmark_rows.append(clean)
            benchmark_dates.update(clean["trade_date"].dt.date)
        statuses["akshare"] = SourceStatus(
            "akshare", True, True, reason_code="ok"
        )
        return False

    def _refresh_calendar(
        self,
        start,
        end,
        published_open_days,
        issues,
        statuses,
        raw_snapshots,
        baseline_spans,
    ) -> tuple[tuple[date, ...], tuple[CalendarCoverageSpan, ...]] | None:
        """Fetch, validate and materialise this window's trading calendar.

        Both exchanges are requested over the validation halo ``[start - 1,
        end + 1]`` and saved to the raw store as ordinary responses (also on
        the failure path).  Values are validated per exchange, then compared
        day by day, then used to replace the window's open days and to check
        the ``pretrade_date`` chain over the merged candidate table.  Returns
        ``(open_days, spans)`` or ``None`` after a FATAL issue: a raised
        calendar never publishes, never writes a partial span, and never
        reuses a previous run's raw response (the supplier is re-requested on
        every run).
        """
        source = self._adapter_or_fail("tushare", statuses)
        if source is None:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_SOURCE_FETCH_FAILED,
                    details={
                        "source": "tushare",
                        "endpoint": "trade_cal",
                        "message": (
                            statuses["tushare"].reason or "adapter unavailable"
                        ),
                    },
                )
            )
            return None
        halo_start = start - timedelta(days=1)
        halo_end = end + timedelta(days=1)
        config = self._project_config.sources.get("tushare", SourceConfig())
        policy = RetryPolicy(
            max_attempts=min(config.max_retries + 1, 3),
            maximum_wait_seconds=min(config.timeout_seconds, 30),
            call_timeout_seconds=config.timeout_seconds,
        )
        facts: dict[str, ExchangeCalendarFacts] = {}
        hashes: dict[str, list[str]] = {
            exchange: [] for exchange in CALENDAR_EXCHANGES
        }
        for exchange in CALENDAR_EXCHANGES:
            request = DataRequest(
                "trade_cal", (), halo_start, halo_end, {"exchange": exchange}
            )
            try:
                result = fetch_with_retry(
                    source, request, policy, sleeper=self._sleeper
                )
            except Exception as error:  # noqa: BLE001 - required calendar
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_SOURCE_FETCH_FAILED,
                        details={
                            "source": "tushare",
                            "endpoint": "trade_cal",
                            "exchange": exchange,
                            "message": str(translate_supplier_error(error)),
                        },
                    )
                )
                statuses["tushare"] = SourceStatus(
                    "tushare", True, False,
                    reason=f"trade_cal fetch failed: {error}",
                    reason_code="source_fetch_failed",
                )
                return None
            snapshot = self._record_raw(result)
            raw_snapshots.append(snapshot)
            hashes[exchange].append(snapshot.sha256)
            try:
                facts[exchange] = parse_trade_cal_frame(
                    result.frame,
                    exchange=exchange,
                    halo_start=halo_start,
                    halo_end=halo_end,
                )
            except TradeCalendarFactError as error:
                issues.extend(_calendar_issues(error.violations))
                statuses["tushare"] = SourceStatus(
                    "tushare", True, False,
                    reason=f"trade_cal {exchange} raw validation failed",
                    reason_code="partial_fetch_failure",
                )
                return None
        violations = check_exchange_agreement(facts["SSE"], facts["SZSE"])
        if violations:
            issues.extend(_calendar_issues(violations))
            statuses["tushare"] = SourceStatus(
                "tushare", True, False,
                reason="SSE and SZSE calendars disagree",
                reason_code="partial_fetch_failure",
            )
            return None
        open_days = materialize_open_days(
            published_open_days, facts=facts["SSE"], start=start, end=end
        )
        continuity = check_pretrade_continuity(
            facts["SSE"].rows, open_days, start=start, end=end
        )
        if continuity.violations:
            issues.extend(_calendar_issues(continuity.violations))
            statuses["tushare"] = SourceStatus(
                "tushare", True, False,
                reason="calendar pretrade chain is broken",
                reason_code="partial_fetch_failure",
            )
            return None
        for boundary in continuity.allowed_pre_coverage:
            issues.append(
                _issue(
                    Severity.INFO,
                    "calendar_pre_coverage_boundary",
                    trade_date=boundary,
                    details={"calendar_date": boundary.isoformat()},
                )
            )
        # SOURCE_TUSHARE_RELAY is the only allowed span source: this pipeline
        # has no official break-glass branch.  If break-glass ever ships, the
        # span source must be decided by the transport's ``kind``, never
        # filled in freely by the caller.
        replacement = supplier_span(
            start,
            end,
            source=SOURCE_TUSHARE_RELAY,
            snapshots_by_exchange=hashes,
        )
        try:
            spans = merge_window(
                baseline_spans,
                start=start,
                end=end,
                replacement=replacement,
                open_days=open_days,
            )
        except CalendarCoverageError as error:
            issues.extend(_calendar_issues(error.violations))
            return None
        statuses["tushare"] = SourceStatus("tushare", True, True, reason_code="ok")
        return open_days, spans

    def _refresh_security_master(
        self, start, end, issues, statuses, raw_snapshots, master,
    ):
        """Apply the required tushare stock_basic whole-market snapshot.

        Runs only after ``_require_available`` has already blocked when tushare
        is unavailable, so a ``None`` source here can only be an adapter
        construction failure (already recorded as a source status by
        ``_adapter_or_fail``, with no issue).

        Refreshes only the listing facts (``list_date`` / ``delist_date`` /
        ``list_status``); the universe labels stay from ``configs/universe.yml``
        as carried by ``master``.  Returns ``(refreshed_master, coverage)`` on
        success, or ``(None, None)`` after a FATAL issue (transport failure or
        a snapshot missing a universe symbol), which blocks publication.
        """
        source = self._adapter_or_fail("tushare", statuses)
        if source is None:
            # Adapter construction failure (e.g. a missing TUSHARE_TOKEN):
            # ``_adapter_or_fail`` records the source status but no issue, so
            # surface the same stable FATAL the daily path would have produced.
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_SOURCE_FETCH_FAILED,
                    details={
                        "source": "tushare",
                        "endpoint": "stock_basic",
                        "message": (
                            statuses["tushare"].reason or "adapter unavailable"
                        ),
                    },
                )
            )
            return None, None
        config = self._project_config.sources.get("tushare", SourceConfig())
        policy = RetryPolicy(
            max_attempts=min(config.max_retries + 1, 3),
            maximum_wait_seconds=min(config.timeout_seconds, 30),
            call_timeout_seconds=config.timeout_seconds,
        )
        try:
            result = fetch_with_retry(
                source,
                DataRequest("stock_basic", (), start, end, {}),
                policy,
                sleeper=self._sleeper,
            )
        except Exception as error:  # noqa: BLE001 - required role
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_SOURCE_FETCH_FAILED,
                    details={
                        "source": "tushare",
                        "endpoint": "stock_basic",
                        "message": str(translate_supplier_error(error)),
                    },
                )
            )
            statuses["tushare"] = SourceStatus(
                "tushare", True, False,
                reason=f"stock_basic fetch failed: {error}",
                reason_code="source_fetch_failed",
            )
            return None, None
        snapshot = self._record_raw(result)
        raw_snapshots.append(snapshot)
        applied = _apply_stock_basic(
            master,
            result.frame,
            snapshot.sha256,
            sdk_version=str(result.metadata.get("sdk_version", "unknown")),
            checked_at=_ingest_time(result.metadata),
        )
        if applied is None:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_MASTER_SNAPSHOT_INCOMPLETE,
                    details={
                        "message": (
                            "tushare stock_basic snapshot cannot account for "
                            "every universe symbol"
                        ),
                        "source": "tushare",
                        "endpoint": "stock_basic",
                    },
                )
            )
            statuses["tushare"] = SourceStatus(
                "tushare", True, False,
                reason="stock_basic snapshot incomplete",
                reason_code="partial_fetch_failure",
            )
            return None, None
        statuses["tushare"] = SourceStatus(
            "tushare", True, True, reason_code="ok"
        )
        return applied

    def _refresh_corporate_actions(
        self,
        enabled,
        symbols,
        start,
        end,
        issues,
        statuses,
        raw_snapshots,
        current_ca,
        current_quarantine,
    ):
        """Reconcile the corporate-action interfaces per held security.

        Three interfaces are requested: CNINFO and Eastmoney for
        distributions, and CNINFO's allotment interface for subscriptions,
        which neither dividend interface reports at all (see
        ``CORPORATE_ACTION_ENDPOINTS``).

        Returns ``(facts, coverage, quarantine)``: the merged canonical
        corporate-action facts, one evidence row per symbol/window recording
        every endpoint's outcome plus the content hash of each successful raw
        snapshot, and the merged quarantine rows (carried history plus this
        update's reconciled-and-reviewed untrusted events).  Empty facts are
        never trusted by default -- only an explicit successful no-event
        answer from *every* requested endpoint yields ``VERIFIED_EMPTY``, and
        any endpoint failure leaves the window ``UNTRUSTED``.
        """
        source = self._overrides.get("akshare") or self._build_lazy("akshare")
        if source is None:
            return current_ca, coverage_frame([]), current_quarantine
        frames_by_symbol: dict[str, dict[str, list[pd.DataFrame]]] = {}
        outcomes_by_symbol: dict[str, dict[str, dict[str, object]]] = {}
        for symbol in symbols:
            frames_by_symbol[symbol] = {
                "cninfo": [],
                "eastmoney": [],
                RIGHTS_SOURCE: [],
            }
            symbol_outcomes: dict[str, dict[str, object]] = {}
            for endpoint, source_name in CORPORATE_ACTION_ENDPOINTS:
                try:
                    result = self._fetch_one(
                        source,
                        DataRequest(endpoint, (symbol,), start, end, {}),
                    )
                    snapshot = self._record_raw(result)
                    raw_snapshots.append(snapshot)
                    frame = result.frame
                    if not frame.empty:
                        frame = CORPORATE_ACTION_PREPARERS[endpoint](frame, symbol)
                        frame = filter_corporate_actions_to_window(frame, start, end)
                        frames_by_symbol[symbol][source_name].append(frame)
                    symbol_outcomes[endpoint] = {
                        "ok": True,
                        "empty": bool(frame.empty),
                        "snapshot_sha256": snapshot.sha256,
                        "checked_at": _ingest_time(result.metadata),
                    }
                except Exception as error:  # noqa: BLE001 - best-effort role
                    symbol_outcomes[endpoint] = {
                        "ok": False,
                        "empty": True,
                        "snapshot_sha256": None,
                        "checked_at": None,
                    }
                    issues.append(
                        _issue(
                            Severity.WARNING,
                            CODE_OPTIONAL_SOURCE_FAILURE,
                            details={
                                "source": "akshare",
                                "endpoint": endpoint,
                                "symbol": symbol,
                                "message": str(error),
                            },
                        )
                    )
            outcomes_by_symbol[symbol] = symbol_outcomes
            if "akshare" not in self._overrides:
                self._sleeper(1)
        accepted_frames: list[pd.DataFrame] = []
        quarantined_frames: list[pd.DataFrame] = []
        for symbol in symbols:
            accepted, quarantined = self._reconcile_action_frames(
                frames_by_symbol[symbol]["cninfo"],
                frames_by_symbol[symbol]["eastmoney"],
                frames_by_symbol[symbol][RIGHTS_SOURCE],
                issues,
            )
            accepted_frames.append(accepted)
            quarantined_frames.append(quarantined)
        accepted = _concat(accepted_frames)
        if accepted is None:
            accepted = pd.DataFrame(columns=RECONCILED_COLUMNS)
        quarantined = _concat(quarantined_frames)
        if quarantined is None:
            quarantined = pd.DataFrame()
        reviewed = apply_corporate_action_reviews(
            CorporateActionResult(accepted=accepted, quarantined=quarantined),
            [
                review.model_dump()
                for review in self._project_config.corporate_action_reviews
                if start <= review.ex_date <= end
            ],
        )
        accepted = reviewed.accepted
        quarantined = reviewed.quarantined
        merged_quarantine = _merge_corporate_action_quarantine(
            current_quarantine, quarantined
        )
        # Narrow only the coverage input, once: the exclusion records an INFO
        # trace, so it must not re-run inside the per-symbol comprehension or
        # each symbol would append a duplicate audit row for the same exclusion.
        relevant_quarantine = _window_relevant_quarantine(
            quarantined, start, end, issues
        )
        coverage = coverage_frame(
            [
                _coverage_record_for(
                    symbol,
                    start,
                    end,
                    outcomes_by_symbol[symbol],
                    _accepted_symbols(accepted),
                    _quarantine_reasons_by_symbol(relevant_quarantine),
                )
                for symbol in symbols
            ]
        )
        if accepted.empty:
            return current_ca, coverage, merged_quarantine
        canonical = _corporate_actions_canonical(accepted)
        return (
            _merge_corporate_actions(current_ca, canonical),
            coverage,
            merged_quarantine,
        )

    def _reconcile_action_frames(
        self, cninfo_frames, eastmoney_frames, allotment_frames, issues
    ):
        """Reconcile collected supplier frames; empty defaults on failure.

        The two dividend lanes are reconciled against each other; the allotment
        lane is normalized on its own because a subscription is reported by one
        source only, so there is nothing to cross-confirm it against.  The two
        lanes fail independently: a malformed dividend frame must not also
        discard the symbol's subscriptions, and vice versa.
        """
        try:
            reconciled = normalize_corporate_actions(
                _concat(cninfo_frames), _concat(eastmoney_frames)
            )
        except Exception as error:  # noqa: BLE001
            self._warn_action_lane_failure(issues, "dividend", error)
            reconciled = CorporateActionResult(
                accepted=pd.DataFrame(), quarantined=pd.DataFrame()
            )
        try:
            rights = normalize_rights_issue_actions(_concat(allotment_frames))
        except Exception as error:  # noqa: BLE001
            self._warn_action_lane_failure(issues, "rights-issue", error)
            rights = CorporateActionResult(
                accepted=pd.DataFrame(), quarantined=pd.DataFrame()
            )
        return (
            self._stack_action_frames([reconciled.accepted, rights.accepted]),
            self._stack_action_frames([reconciled.quarantined, rights.quarantined]),
        )

    @staticmethod
    def _warn_action_lane_failure(issues, lane, error) -> None:
        """Record one corporate-action lane's failure as an optional-source warning."""
        issues.append(
            _issue(
                Severity.WARNING,
                CODE_OPTIONAL_SOURCE_FAILURE,
                details={
                    "source": "akshare",
                    "message": f"{lane} corporate-action reconciliation: {error}",
                },
            )
        )

    @staticmethod
    def _stack_action_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
        stacked = _concat(frames)
        return pd.DataFrame() if stacked is None else stacked

    def _build_lazy(self, name):
        try:
            return self._source(name)
        except Exception:  # noqa: BLE001
            return None

    def _fetch_validation_daily(
        self,
        enabled,
        symbols,
        start,
        end,
        issues,
        statuses,
        raw_snapshots,
        validation_rows,
    ) -> None:
        source = self._adapter_or_warn("baostock", issues)
        if source is None:
            statuses["baostock"] = SourceStatus(
                "baostock", False, False,
                reason="optional source unavailable",
                reason_code="optional_source_unavailable",
            )
            return
        failures = 0
        for symbol in symbols:
            result = self._dispatch(
                "baostock", source, "daily", symbol, start, end,
                {"adjustment": "unadjusted"}, required=False, issues=issues,
            )
            if result is None:
                failures += 1
                continue
            raw_snapshots.append(self._record_raw(result))
            validation_rows.append(
                normalize_daily(
                    result.frame, "baostock", _ingest_time(result.metadata)
                ).valid
            )
        if failures:
            statuses["baostock"] = SourceStatus(
                "baostock", False, False,
                reason=f"{failures} of {len(symbols)} validation requests failed",
                reason_code="partial_fetch_failure",
            )
        else:
            statuses["baostock"] = SourceStatus(
                "baostock", False, True, reason_code="ok"
            )

    def _adapter_or_fail(self, name, statuses):
        try:
            return self._source(name)
        except Exception as error:  # noqa: BLE001
            statuses[name] = SourceStatus(
                source=name,
                required=True,
                ok=False,
                reason=str(error),
                reason_code="source_unavailable",
            )
            return None

    def _adapter_or_warn(self, name, issues):
        try:
            return self._source(name)
        except Exception as error:  # noqa: BLE001
            issues.append(
                _issue(
                    Severity.WARNING,
                    CODE_OPTIONAL_SOURCE_FAILURE,
                    details={"source": name, "message": str(error)},
                )
            )
            return None

    def _dispatch(
        self,
        name,
        source,
        endpoint,
        symbol,
        start,
        end,
        params,
        *,
        required: bool,
        issues=None,
    ):
        config: SourceConfig = self._project_config.sources.get(
            name, SourceConfig()
        )
        policy = RetryPolicy(
            max_attempts=min(config.max_retries + 1, 3),
            maximum_wait_seconds=min(config.timeout_seconds, 30),
            call_timeout_seconds=config.timeout_seconds,
        )
        request = DataRequest(endpoint, (symbol,), start, end, params)
        try:
            return fetch_with_retry(
                source, request, policy, sleeper=self._sleeper
            )
        except Exception as error:  # noqa: BLE001
            message = str(translate_supplier_error(error))
            if required:
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_SOURCE_FETCH_FAILED,
                        details={
                            "source": name,
                            "endpoint": endpoint,
                            "symbol": symbol,
                            "message": message,
                        },
                    )
                )
                return None
            if issues is not None:
                issues.append(
                    _issue(
                        Severity.WARNING,
                        CODE_OPTIONAL_SOURCE_FAILURE,
                        details={
                            "source": name,
                            "endpoint": endpoint,
                            "symbol": symbol,
                            "message": message,
                        },
                    )
                )
            return None

    def _fetch_one(self, source, request):
        config = self._project_config.sources.get(
            "akshare", SourceConfig()
        )
        policy = RetryPolicy(
            max_attempts=min(config.max_retries + 1, 3),
            maximum_wait_seconds=min(config.timeout_seconds, 30),
            call_timeout_seconds=config.timeout_seconds,
        )
        return fetch_with_retry(source, request, policy, sleeper=self._sleeper)

    def _record_raw(self, result) -> RawSnapshot:
        """Persist one adapter response and hand back the full raw snapshot.

        The public ``DataUpdateResult.raw_snapshots`` contract stays a tuple of
        content hashes -- the conversion to ``snapshot.sha256`` happens inside
        ``_result``, so no caller ever receives local paths or manifests.
        """
        return self._raw_store.save(result)

    def _merge_daily(
        self,
        current,
        primary_rows,
        benchmark_rows,
        equity_symbols,
        benchmark_symbols,
        start,
        end,
        ingested,
    ) -> pd.DataFrame:
        replaced = set(equity_symbols) | set(benchmark_symbols)
        traded = current["trade_date"]
        overlap = (
            (traded >= pd.Timestamp(start))
            & (traded <= pd.Timestamp(end))
            & (current["symbol"].isin(replaced))
        )
        kept = current.loc[~overlap].copy()
        fresh = _concat(primary_rows + benchmark_rows)
        if fresh is None:
            merged = kept
        else:
            merged = pd.concat([kept, fresh], ignore_index=True)
        return _coerce_daily(merged, ingested)

    def _missing_issues(
        self,
        equity_symbols,
        master,
        start,
        end,
        benchmark_dates,
        primary_dates,
        validation_dates,
        ingested,
    ) -> list[QualityIssue]:
        grid = sorted(day for day in benchmark_dates if start <= day <= end)
        if not grid:
            return []
        details = {
            str(row["symbol"]): (_as_date(row["list_date"]),
                                 _as_date(row["delist_date"]))
            for row in master.to_dict("records")
        }
        issues: list[QualityIssue] = []
        for symbol in equity_symbols:
            list_date, delist_date = details.get(symbol, (None, None))
            if list_date is not None and list_date > grid[0]:
                continue
            if delist_date is not None and delist_date < grid[-1]:
                continue
            for day in grid:
                key = (symbol, day)
                if key in primary_dates:
                    continue
                code = classify_missing_row(
                    trade_date=day,
                    list_date=list_date,
                    delist_date=delist_date,
                    is_trading_day=True,
                    primary_present=False,
                    validation_present=key in validation_dates,
                )
                issues.append(
                    _issue(
                        Severity.WARNING,
                        code,
                        symbol=symbol,
                        trade_date=day,
                        table="daily_bar",
                    )
                )
        return issues

    def _result(
        self,
        issues,
        dataset_ref,
        run_id,
        resolved_end,
        statuses,
        raw_snapshots,
    ) -> DataUpdateResult:
        return DataUpdateResult(
            quality_report=QualityReport(issues=tuple(issues)),
            dataset_ref=dataset_ref,
            run_id=run_id,
            resolved_end_date=resolved_end,
            source_status=tuple(
                statuses[name] for name in _CONFIGURED_SOURCES
            ),
            raw_snapshots=tuple(
                snapshot.sha256 for snapshot in raw_snapshots
            ),
        )


# --------------------------------------------------------------------------- #
# Builders / canonicalisers local to the pipeline
# --------------------------------------------------------------------------- #


def _build_source(name: str, config: SourceConfig) -> DataSource:
    if name == "tushare":
        from stock_quant.data_sources.tushare import TushareSource

        return TushareSource(config, client=None)
    if name == "akshare":
        from stock_quant.data_sources.akshare import AkShareSource

        return AkShareSource(config, client=None)
    if name == "baostock":
        from stock_quant.data_sources.baostock import BaoStockSource

        return BaoStockSource(config, client=None)
    raise ValueError(f"unknown configured source: {name}")


def _ingest_time(metadata: object) -> object:
    """A deterministic ingest timestamp from adapter metadata when present."""
    if isinstance(metadata, dict):
        value = metadata.get("response_timestamp")
        if value is not None:
            return pd.Timestamp(value)
    return pd.Timestamp.now(tz="UTC")


def _equity_symbols(master: pd.DataFrame) -> list[str]:
    symbols = [str(row["symbol"]) for row in master.to_dict("records")]
    return sorted(symbols)


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame | None:
    frames = [frame for frame in frames if frame is not None and not frame.empty]
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def _symbol_dates(frames: list[pd.DataFrame]) -> set[tuple[str, date]]:
    out: set[tuple[str, date]] = set()
    for frame in frames:
        for record in frame.to_dict("records"):
            out.add((str(record["symbol"]), _as_date(record["trade_date"])))
    return out


def _normalize_index(
    frame: pd.DataFrame, symbol: str, metadata: object
) -> pd.DataFrame:
    """Canonicalise one AKShare index_history response into daily columns."""
    columns = {
        "日期": "trade_date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
    }
    if "date" in frame.columns:
        columns = {
            "date": "trade_date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "amount": "amount",
        }
    present = {name: column for name, column in columns.items()
               if name in frame.columns}
    optional = {"成交额", "amount"}
    missing = [
        name for name in columns if name not in present and name not in optional
    ]
    if missing:
        raise ValueError(
            f"index_history response is missing columns: {', '.join(missing)}"
        )
    rows: list[dict[str, object]] = []
    ingested = _ingest_time(metadata)
    source_columns = {target: source for source, target in present.items()}
    volume_multiplier = 1 if "date" in frame.columns else 100
    for record in frame.to_dict("records"):
        rows.append(
            {
                "trade_date": pd.Timestamp(record[source_columns["trade_date"]]),
                "symbol": symbol,
                "open": float(record[source_columns["open"]]),
                "high": float(record[source_columns["high"]]),
                "low": float(record[source_columns["low"]]),
                "close": float(record[source_columns["close"]]),
                "volume": float(record[source_columns["volume"]]) * volume_multiplier,
                "amount": (
                    float(record[source_columns["amount"]])
                    if "amount" in source_columns
                    else 0.0
                ),
                "adjustment": "unadjusted",
                "source": "akshare",
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def _coerce_daily(frame: pd.DataFrame, ingested) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)
    out = frame[list(DAILY_COLUMNS)].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    for column in ("open", "high", "low", "close", "amount"):
        out[column] = out[column].astype("float64")
    out["volume"] = out["volume"].astype("int64")
    out["ingested_at"] = pd.to_datetime(out["ingested_at"], utc=True)
    return out


def _apply_stock_basic(master, raw, snapshot_sha256, *, sdk_version, checked_at):
    """Map one stock_basic snapshot onto master rows and build coverage rows.

    Only listing facts are refreshed; ``name``/``exchange``/``board`` stay from
    the universe labels carried by ``master``.  Returns ``(refreshed_master,
    coverage)`` or ``None`` when the snapshot cannot account for every master
    symbol (a FATAL condition the caller records).
    """
    facts: dict[str, dict[str, object]] = {}
    for record in raw.to_dict("records"):
        ts_code = str(record.get("ts_code", "")).strip()
        list_date = _stock_basic_date(record.get("list_date"))
        status = str(record.get("list_status", "")).strip()
        if not ts_code or list_date is None or status not in {
            ListStatus.L.value,
            ListStatus.D.value,
            ListStatus.P.value,
        }:
            continue
        facts[ts_code] = {
            "list_date": list_date,
            "delist_date": _stock_basic_date(record.get("delist_date")),
            "list_status": status,
        }
    refreshed: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    missing: list[str] = []
    for record in master.to_dict("records"):
        symbol = str(record["symbol"])
        fact = facts.get(symbol)
        if fact is None:
            missing.append(symbol)
            continue
        row = dict(record)
        row["list_date"] = pd.Timestamp(fact["list_date"])
        row["delist_date"] = (
            pd.Timestamp(fact["delist_date"])
            if fact["delist_date"] is not None
            else pd.NaT
        )
        row["list_status"] = fact["list_status"]
        refreshed.append(row)
        coverage_rows.append(
            master_coverage_record(
                symbol,
                list_date=fact["list_date"],
                delist_date=fact["delist_date"],
                list_status=fact["list_status"],
                source=MASTER_SOURCE_STOCK_BASIC,
                snapshot_sha256=snapshot_sha256,
                sdk_version=sdk_version,
                checked_at=checked_at,
            )
        )
    if missing:
        return None
    frame = pd.DataFrame(refreshed, columns=list(SECURITY_MASTER_COLUMNS))
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    frame["delist_date"] = pd.to_datetime(frame["delist_date"], errors="coerce")
    return frame, master_coverage_frame(coverage_rows)


def _stock_basic_date(value):
    """Parse a stock_basic date cell (YYYYMMDD str / NaT / None) to ``date``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nat", "nan", "none", ""}:
        return None
    timestamp = pd.to_datetime(text, errors="coerce")
    if pd.isna(timestamp):
        return None
    return timestamp.date()


def _coverage_record_for(
    symbol: str,
    start: date,
    end: date,
    outcomes: dict[str, dict[str, object]],
    accepted_symbols: frozenset[str],
    quarantine_reasons: Mapping[str, set[str]],
) -> dict[str, object]:
    """Render one symbol/window's evidence row from its endpoint outcomes."""
    sources = [
        {"endpoint": endpoint, "outcome": _endpoint_outcome_label(outcome)}
        for endpoint, outcome in outcomes.items()
    ]
    snapshot_hashes = {
        endpoint: outcome["snapshot_sha256"]
        for endpoint, outcome in outcomes.items()
        if outcome["snapshot_sha256"] is not None
    }
    status, reason = _coverage_verdict(
        outcomes,
        has_accepted=symbol in accepted_symbols,
        quarantine_reasons=quarantine_reasons.get(symbol),
    )
    return coverage_record(
        symbol,
        start,
        end,
        status,
        reason,
        sources=sources,
        snapshot_hashes=snapshot_hashes,
        checked_at=_coverage_checked_at(outcomes),
    )


def _coverage_verdict(
    outcomes: dict[str, dict[str, object]],
    *,
    has_accepted: bool,
    quarantine_reasons: set[str] | None,
) -> tuple[CoverageStatus, CoverageReason | None]:
    """Decide one symbol/window's status from its endpoint fetch outcomes.

    ``VERIFIED`` requires a fully accounted window: every requested endpoint
    answered, at least one returned events, and the symbol holds an accepted
    reconciled fact with *no relevant* quarantine.  A quarantined event *that
    can affect this window* (cross-source conflict / unsupported action /
    incomplete record) makes the window ``UNTRUSTED`` even when a sibling event
    for the same symbol/window was accepted, so a conflicting or unbooked event
    can never be masked by an accepted row while the coverage reads
    ``VERIFIED``.  A row whose every known date lies outside the window is
    excluded by ``_window_relevant_quarantine`` before this decision
    (ADR-006).
    """
    if any(not outcome["ok"] for outcome in outcomes.values()):
        return CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_FETCH_FAILED
    if not any(not outcome["empty"] for outcome in outcomes.values()):
        return CoverageStatus.VERIFIED_EMPTY, None
    if quarantine_reasons:
        return (
            CoverageStatus.UNTRUSTED,
            _coverage_reason_for_quarantine(quarantine_reasons),
        )
    if has_accepted:
        return CoverageStatus.VERIFIED, None
    return CoverageStatus.UNTRUSTED, CoverageReason.FACTS_INCOMPLETE


def _coverage_reason_for_quarantine(
    reasons: set[str] | None,
) -> CoverageReason:
    if reasons is None:
        return CoverageReason.FACTS_INCOMPLETE
    if REASON_CROSS_SOURCE_CONFLICT in reasons:
        return CoverageReason.SOURCE_CONFLICT
    if REASON_UNSUPPORTED_CORPORATE_ACTION in reasons:
        return CoverageReason.UNSUPPORTED_ACTION
    return CoverageReason.FACTS_INCOMPLETE


def _endpoint_outcome_label(outcome: dict[str, object]) -> str:
    if not outcome["ok"]:
        return OUTCOME_FAILED
    if outcome["empty"]:
        return OUTCOME_SUCCESS_EMPTY
    return OUTCOME_SUCCESS_EVENTS


def _coverage_checked_at(outcomes: dict[str, dict[str, object]]):
    candidates = [
        outcome["checked_at"]
        for outcome in outcomes.values()
        if outcome["checked_at"] is not None
    ]
    if candidates:
        return candidates[-1]
    return None


def _accepted_symbols(accepted: pd.DataFrame) -> frozenset[str]:
    if accepted.empty or "symbol" not in accepted.columns:
        return frozenset()
    return frozenset(str(value) for value in accepted["symbol"])


def _window_relevant_quarantine(
    quarantined: pd.DataFrame,
    start: date,
    end: date,
    issues: list[QualityIssue],
) -> pd.DataFrame:
    """Drop quarantined rows that provably cannot affect ``[start, end]``.

    The coverage verdict answers a question about *this window*, so a row whose
    every known date lies outside it is evidence about another period and must
    not mark the window UNTRUSTED (ADR-006).  Only the coverage input is
    narrowed: ``merged_quarantine`` -- the published table -- keeps every row,
    and each exclusion is recorded as an INFO issue grouped by symbol and
    branch so a suppressed decision leaves a trace.
    """
    if quarantined.empty or "symbol" not in quarantined.columns:
        return quarantined
    kept: list[bool] = []
    excluded: dict[tuple[str, str], int] = {}
    for record in quarantined.to_dict("records"):
        branch = quarantine_row_out_of_window_reason(record, start, end)
        kept.append(branch is None)
        if branch is not None:
            key = (str(record["symbol"]), branch)
            excluded[key] = excluded.get(key, 0) + 1
    for (symbol, branch), rows in sorted(excluded.items()):
        issues.append(
            _issue(
                Severity.INFO,
                CODE_QUARANTINE_OUT_OF_WINDOW,
                table=TABLE_CORPORATE_ACTION_QUARANTINE,
                symbol=symbol,
                details={
                    "rows": rows,
                    "branch": branch,
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                },
            )
        )
    return quarantined.iloc[[index for index, keep in enumerate(kept) if keep]]


def _quarantine_reasons_by_symbol(
    quarantined: pd.DataFrame,
) -> dict[str, set[str]]:
    reasons: dict[str, set[str]] = {}
    if quarantined.empty or "symbol" not in quarantined.columns:
        return reasons
    for record in quarantined.to_dict("records"):
        symbol = str(record["symbol"])
        value = record.get("reason")
        reasons.setdefault(symbol, set()).add(str(value) if value else "")
    return reasons


def _corporate_actions_canonical(accepted: pd.DataFrame) -> pd.DataFrame:
    columns = list(RECONCILED_COLUMNS)
    source = accepted["confirmed_by"] if "confirmed_by" in accepted.columns \
        else "cninfo+eastmoney"
    canonical = accepted[columns[:-1]].copy()
    canonical["source"] = source
    canonical["status"] = "implemented"
    return canonical[list(CORPORATE_ACTION_COLUMNS)].reset_index(drop=True)


def _merge_corporate_actions(
    current: pd.DataFrame, fresh: pd.DataFrame
) -> pd.DataFrame:
    if current.empty:
        return fresh
    key = ["symbol", "ex_date"]
    existing = current[list(CORPORATE_ACTION_COLUMNS)]
    kept = existing.loc[
        ~existing.set_index(key).index.isin(fresh.set_index(key).index)
    ]
    merged = pd.concat([kept, fresh], ignore_index=True)
    return merged[list(CORPORATE_ACTION_COLUMNS)]


def _merge_corporate_action_quarantine(
    current: pd.DataFrame, fresh: pd.DataFrame
) -> pd.DataFrame:
    """Carry prior quarantine rows and merge this update's reviewed rows.

    Rows key on ``(symbol, ex_date, confirmed_by, reason)``; dates and the
    string key columns are normalised first so carried (parquet ``NaT``/``None``)
    and freshly reconciled (object ``NaN``) frames key identically instead of
    colliding on dtype.  A fresh row replaces a carried row with the same key
    and the merged frame is ordered by the key columns, so identical inputs
    publish byte-identical quarantine tables.
    """
    current = _normalised_quarantine(current)
    fresh = _normalised_quarantine(fresh)
    if current.empty:
        return _sorted_quarantine(fresh)
    if fresh.empty:
        return _sorted_quarantine(current)
    key = ["symbol", "ex_date", "confirmed_by", "reason"]
    kept = current.loc[
        ~current.set_index(key).index.isin(fresh.set_index(key).index)
    ]
    merged = pd.concat([kept, fresh], ignore_index=True)
    return _sorted_quarantine(merged)


def _normalised_quarantine(frame: pd.DataFrame | None) -> pd.DataFrame:
    """The canonical quarantine frame with merge-safe key dtypes.

    ``ex_date`` becomes a timezone-free datetime and ``symbol`` /
    ``confirmed_by`` / ``reason`` become plain ``str`` so the merge key never
    compares ``NaN`` against ``NaT`` or ``None``.
    """
    columns = list(CORPORATE_ACTION_QUARANTINE_COLUMNS)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    out = frame[columns].copy()
    out["ex_date"] = pd.to_datetime(out["ex_date"], errors="coerce")
    for column in ("symbol", "confirmed_by", "reason"):
        out[column] = out[column].fillna("").astype(str)
    return out


def _sorted_quarantine(frame: pd.DataFrame) -> pd.DataFrame:
    """Deterministic quarantine ordering by the merge-key columns."""
    if frame.empty:
        return frame
    return frame.sort_values(
        ["symbol", "ex_date", "confirmed_by", "reason"], kind="stable"
    ).reset_index(drop=True)


def check_adjusted_bar_lineage(
    adjusted: pd.DataFrame,
    daily: pd.DataFrame,
    corporate_actions: pd.DataFrame,
) -> list[QualityIssue]:
    """FATAL lineage breaks between the total-return and raw close series.

    Every ``adjusted_bar`` row must reference an existing ``daily_bar`` close
    at the identical value, carry the single internal adjustment basis, and
    list only action ids derivable from the canonical corporate-action facts.
    A breach means the published series cannot be audited back to its inputs
    and must never reach (or keep) a dataset version.
    """
    issues: list[QualityIssue] = []
    if adjusted is None or adjusted.empty:
        return issues
    raw_closes: dict[tuple[str, date], float] = {}
    for record in daily.to_dict("records"):
        day = _as_date(record["trade_date"])
        if day is not None:
            raw_closes[(str(record["symbol"]), day)] = float(record["close"])
    known_action_ids = {
        action_id_of(str(record["symbol"]), _as_date(record["ex_date"]))
        for record in corporate_actions.to_dict("records")
        if _as_date(record.get("ex_date")) is not None
    }
    for row in adjusted.to_dict("records"):
        symbol = str(row["symbol"])
        day = _as_date(row["trade_date"])
        key = (symbol, day)
        if day is None or key not in raw_closes:
            issues.append(
                _adjusted_issue(CODE_ADJUSTED_BAR_MISSING_RAW, symbol, day)
            )
        elif float(row["raw_close"]) != raw_closes[key]:
            issues.append(
                _adjusted_issue(
                    CODE_ADJUSTED_BAR_RAW_CLOSE_MISMATCH, symbol, day
                )
            )
        if str(row["adjustment"]) != ADJUSTMENT_NAME:
            issues.append(
                _adjusted_issue(CODE_ADJUSTED_BAR_WRONG_BASIS, symbol, day)
            )
        for action_id in _applied_action_ids(row):
            if action_id not in known_action_ids:
                issues.append(
                    _adjusted_issue(
                        CODE_ADJUSTED_BAR_UNKNOWN_ACTION, symbol, day
                    )
                )
    return issues


def _applied_action_ids(row: dict) -> list[str]:
    """The JSON-encoded ``applied_action_ids`` list, tolerating blank cells."""
    raw = row.get("applied_action_ids")
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        decoded = json.loads(raw)
    except ValueError:
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


def _adjusted_issue(
    code: str, symbol: str, day: date | None
) -> QualityIssue:
    return _issue(
        Severity.FATAL,
        code,
        symbol=symbol,
        trade_date=day,
        table="adjusted_bar",
        details={
            "message": (
                f"adjusted_bar lineage check {code!r} failed for {symbol} at "
                f"{day.isoformat() if day is not None else 'an unknown date'}"
            ),
        },
    )


def _calendar_frame(open_days: tuple[date, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(open_days),
            "is_trading_day": [True] * len(open_days),
        }
    )


def _covered_symbols(coverage: pd.DataFrame | None) -> set[str]:
    if coverage is None or not isinstance(coverage, pd.DataFrame):
        return set()
    return set(str(value) for value in coverage.get("symbol", []))


def _as_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


#: Page size of the proof-only pre-window probe.  A *stride*, never a boundary:
#: the probe keeps stepping back until it finds a bar or reaches the symbol's
#: own ``list_date``, so how far back it looks is decided by the data.  The
#: stride exists only to stay under the supplier's per-response row cap.
_ANCHOR_PROBE_DAYS = 3650


def _carries_proof_chain(raw: pd.DataFrame | None) -> bool:
    """Whether a raw daily response can prove a suspension run at all.

    Mirrors ``_materialize_suspensions``' own guard: without ``pre_close``
    there is nothing to chain a gap against, so such a symbol is not worth a
    pre-window probe either.
    """
    if raw is None or "close" not in raw.columns or "pre_close" not in raw.columns:
        return False
    return any(name in raw.columns for name in ("trade_date", "date"))


def _has_bar_on(raw: pd.DataFrame, day: date) -> bool:
    """Whether a raw daily response already carries a row on ``day``."""
    if raw.empty:
        return False
    column = "trade_date" if "trade_date" in raw.columns else "date"
    days = pd.to_datetime(raw[column].astype(str), errors="coerce").dt.date
    return bool((days == day).any())


def _issue(
    severity,
    code,
    *,
    symbol=None,
    trade_date=None,
    table="data_update",
    details=None,
) -> QualityIssue:
    return QualityIssue(
        severity=severity,
        code=code,
        table=table,
        symbol=symbol,
        trade_date=trade_date,
        details=dict(details or {}),
    )
