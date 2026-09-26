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
from stock_quant.data_contracts import TIER_BLOCKS_PUBLICATION
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
from stock_quant.data_model.call_ledger import (
    render_call_ledger,
    write_call_ledger,
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
    REASON_INCOMPLETE,
    REASON_NON_DISTRIBUTIVE_RESTRUCTURING,
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
from stock_quant.data_model.ex_date_classification import (
    MARKET_ADJUSTMENT_OBSERVED,
    classify_ex_date,
)
from stock_quant.data_model.fetch_coverage import (
    KIND_CARRIED,
    KIND_FETCHED,
    KIND_NOT_FETCHED,
    FetchSegment,
    to_build_config_payload,
)
from stock_quant.data_model.fetch_windows import (
    FetchWindowPlan,
    plan_table_fetch_windows,
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
from stock_quant.data_quality.gates import (
    TABLE_LEVEL_BLOCKING_CODES,
    evaluate_publication,
)
from stock_quant.data_quality.models import (
    CODE_ABSENT_EX_DATE_CLASSIFIED,
    CODE_ADJUSTED_BAR_MISSING_RAW,
    CODE_ADJUSTED_BAR_RAW_CLOSE_MISMATCH,
    CODE_ADJUSTED_BAR_UNKNOWN_ACTION,
    CODE_ADJUSTED_BAR_WRONG_BASIS,
    CODE_COVERAGE_DOWNGRADED,
    CODE_QUARANTINE_OUT_OF_WINDOW,
    CODE_UNREGISTERED_TABLE,
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
    FetchResult,
    RetryPolicy,
    fetch_with_retry,
    request_key,
    translate_supplier_error,
)
from stock_quant.data_sources.baostock_factor import (
    factor_event_dates,
    fetch_adjust_factor_frames,
    snapshot_result,
)
from stock_quant.data_sources.raw_store import (
    RawSnapshot,
    RawSnapshotEvidence,
    RawStore,
)
from stock_quant.data_sources.price_observed import (
    DAILY_ENDPOINT,
    TUSHARE_SOURCE,
    LazyDailyPriceChannel,
    PriceObservedArbiter,
)
from stock_quant.data_sources.tdx import (
    ARBITER_NAME,
    XDXR_CATEGORY_DISTRIBUTION,
    XDXR_ENDPOINT,
    TdxXdxrArbiter,
    fetch_xdxr_frames,
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
CODE_PRICE_OBSERVED_SETTLEMENT = "price_observed_settlement"
CODE_UNIVERSE_MASTER_MISMATCH = "universe_master_mismatch"
CODE_MASTER_SNAPSHOT_INCOMPLETE = "master_snapshot_incomplete"
CODE_MASTER_BAR_BOUNDARY = "master_bar_boundary"
CODE_MASTER_COVERAGE_MISMATCH = "master_coverage_mismatch"
CODE_UNIVERSE_DEFINITION_INVALID = "universe_definition_invalid"
CODE_ADJUSTED_BAR_MISSING_FROM_DATASET = "adjusted_bar_missing_from_dataset"
CODE_REUSE_CANDIDATE_REJECTED = "reuse_candidate_rejected"

#: The ``build_config`` key naming the baseline version this update carried
#: forward from.  Always written -- an explicit ``null`` (no baseline) keeps
#: "missing key = legacy contract" the single meaning it already has for the
#: calendar-evidence keys.
BASELINE_VERSION_KEY = "baseline_version"

#: Canonical standardized table holding untrusted corporate-action rows.
TABLE_CORPORATE_ACTION_QUARANTINE = "corporate_action_quarantine"

#: Canonical standardized table holding corporate-action evidence rows.
TABLE_CORPORATE_ACTION_COVERAGE = "corporate_action_coverage"

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


def _reuse_evidence(
    counts: Mapping[str, Mapping[str, Mapping[str, int]]],
) -> dict[str, dict[str, dict[str, int]]]:
    """Sanitized reuse counters per source x endpoint, deterministically ordered.

    Build evidence, not runtime telemetry: endpoint names and integers only,
    sorted, so identical fetch behaviour renders an identical
    ``build_config.raw_snapshot_reuse`` (ADR-015).
    """
    return {
        source: {
            endpoint: {
                "reused": int(row.get("reused", 0)),
                "fetched": int(row.get("fetched", 0)),
            }
            for endpoint, row in sorted(endpoints.items())
        }
        for source, endpoints in sorted(counts.items())
    }


def _reused_ledger_payload(
    counts: Mapping[str, Mapping[str, Mapping[str, int]]],
) -> dict[str, dict[str, int]]:
    """The per source x endpoint reused-request counts the ledger renders.

    Endpoints that dispatched but reused nothing stay out, so a non-empty
    entry always means quota actually saved.
    """
    return {
        source: {
            endpoint: int(row.get("reused", 0))
            for endpoint, row in sorted(endpoints.items())
            if int(row.get("reused", 0)) > 0
        }
        for source, endpoints in counts.items()
    }


def _recorded_fetch_spans(payload: object) -> dict[str, tuple[date, date]]:
    """The per-table span the baseline's fetch coverage recorded.

    Each table's span is the union of its ``fetched``/``carried`` segments;
    ``not_fetched`` segments cover nothing by design.  A baseline that
    predates the coverage record (or a table it skipped) yields no span, so
    ``last_covered_plus_1`` planning continues from the window anchor and an
    explicit full-window update re-fetches its window exactly as before the
    record existed.
    """
    if not isinstance(payload, Mapping):
        return {}
    spans: dict[str, tuple[date, date]] = {}
    for table, segments in payload.items():
        if not isinstance(segments, list):
            continue
        low = high = None
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            if segment.get("kind") == KIND_NOT_FETCHED:
                continue
            try:
                start = date.fromisoformat(str(segment["window_start"]))
                end = date.fromisoformat(str(segment["window_end"]))
            except (KeyError, ValueError, TypeError):
                continue
            low = start if low is None else min(low, start)
            high = end if high is None else max(high, end)
        if low is not None and high is not None and low <= high:
            spans[str(table)] = (low, high)
    return spans


def _baseline_covered_window(
    build: Mapping[str, Any] | None,
) -> tuple[date, date] | None:
    """The evidence window a baseline without recorded spans itself covers.

    A baseline published before per-table fetch coverage was recorded (spec
    D5.3) carries no ``recorded_spans``, yet its own review window --
    ``full_history_acceptance_start`` through ``resolved_end_date`` -- is
    real carried evidence for every table.  Recording it as the fallback
    ``covered`` span keeps the next update's segments chained from the
    acceptance anchor; without it the segments start at the fetch window and
    the offline check reads a coverage gap where the data is in fact
    complete.
    """
    if not isinstance(build, Mapping):
        return None
    start_value = build.get("full_history_acceptance_start")
    end_value = build.get("resolved_end_date")
    if not isinstance(start_value, str) or not isinstance(end_value, str):
        return None
    try:
        start = date.fromisoformat(start_value)
        end = date.fromisoformat(end_value)
    except ValueError:
        return None
    return (start, end) if start <= end else None


def _merge_carried_coverage(
    carried: pd.DataFrame | None, refreshed: pd.DataFrame, *, fetch_start: date
) -> pd.DataFrame:
    """Carry baseline coverage rows that predate a refreshed sub-window.

    A fetched refresh rebuilds the coverage verdict for its own contract
    window only; the baseline's rows for the history before it remain valid
    evidence and must publish beside it, clipped to end just before the
    fetched window so the two eras tile without overlapping verdicts (spec
    D5.2).  Rows the baseline recorded inside the fetched window are
    superseded by the fresh verdict and dropped.
    """
    if carried is None or carried.empty or "window_end" not in carried.columns:
        return refreshed
    carried_end = pd.to_datetime(carried["window_end"])
    boundary = pd.Timestamp(fetch_start)
    before = carried[carried_end < boundary]
    overlap = carried[carried_end >= boundary]
    if "window_start" in carried.columns:
        # A row the fetched window provably covers from its own start onward is
        # superseded wholesale; clipping its end instead would publish a row
        # whose window_start follows its window_end, which the trust gate reads
        # as an UNTRUSTED window no interval supports.  Supersession is dropped
        # only when proven: an unreadable start keeps the clip-and-publish path.
        inside = pd.to_datetime(overlap["window_start"]) >= boundary
        overlap = overlap[~inside]
    overlap = overlap.copy()
    if not overlap.empty:
        overlap["window_end"] = boundary - pd.Timedelta(days=1)
    return pd.concat([before, overlap, refreshed], ignore_index=True)


def _plan_skips_fetch(plan: object, end: date) -> bool:
    """True when one table's plan leaves nothing to fetch this round.

    Either the contract window was skipped by the operator's explicit window
    (``not_fetched``, F1), or the plan starts past the resolved end because
    the baseline already covers the whole request (nothing left to fetch).
    """
    if not isinstance(plan, FetchWindowPlan):
        return False
    if plan.kind == KIND_NOT_FETCHED or plan.window_start is None:
        return True
    return plan.window_start > end


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
    table_fetch_coverage: Mapping[str, object] | None = None,
    baseline_version: str | None = None,
    raw_snapshot_reuse: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The exact sanitized payload hashed into a published dataset version.

    Carries hashes, identifiers, dates and stable status codes only -- never
    exception text, URLs, local paths or reason prose -- so the dataset
    version is bound to the raw evidence it was built from.  The calendar
    evidence keys (``calendar_coverage`` and siblings) are the shape marker
    for the build-config contract: ``DATASET_BUILD_CONTRACT_VERSION`` stays
    ``1`` on purpose, so a manifest without ``calendar_coverage`` is a legacy
    payload read compatibly but never accepted as fully evidenced.  When
    supplied, ``table_fetch_coverage`` records which history segments this
    build re-fetched, carried from the baseline, or skipped (spec D5.3).
    ``baseline_version`` is always written -- ``null`` when the build had no
    baseline -- so "missing key" keeps its legacy-contract meaning; it names
    the immutable version this build carried its tables forward from, closing
    the carried-segment chain to that manifest.  When supplied,
    ``raw_snapshot_reuse`` carries the per source x endpoint {reused,
    fetched} counters of the lanes that may serve stored snapshots back
    (ADR-015) as deterministic build evidence.
    """
    config = {
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
        BASELINE_VERSION_KEY: baseline_version,
    }
    if table_fetch_coverage:
        config["table_fetch_coverage"] = dict(table_fetch_coverage)
    if raw_snapshot_reuse:
        config["raw_snapshot_reuse"] = dict(raw_snapshot_reuse)
    return config


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
        # Source instances actually used by the running update (name ->
        # instance), reset at the start of each ``update()``; the call ledger
        # renders its per-source accounting from this at the end of the run.
        self._sources_used: dict[str, DataSource] = {}
        # Per source x endpoint {reused, fetched} counters for the lanes that
        # consult the raw store before fetching (ADR-015), reset with
        # ``_sources_used``; they feed ``build_config.raw_snapshot_reuse``
        # and the ledger's ``reused`` section.
        self._reuse_counts: dict[str, dict[str, dict[str, int]]] = {}

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
        self._sources_used = {}
        self._reuse_counts = {}
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
            fetch_coverage,
            baseline_version,
        ) = baseline
        recorded_spans, carried_master_coverage, carried_ca_coverage, (
            baseline_covered
        ) = fetch_coverage
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

        # ---- per-table fetch windows (spec A3 / D5.2) ------------------- #
        # The validation/merge window above stays exactly as computed -- the
        # fetch calls below follow each table's contract strategy instead.
        # ``last_covered_plus_1`` continues from the span the baseline
        # recorded in its own fetch coverage; a baseline that predates the
        # record continues from the window anchor, so an explicit full-window
        # update re-fetches its window as before the record existed.
        plans = plan_table_fetch_windows(
            self._project_config.data_contracts,
            baseline_covered=recorded_spans,
            request_start=request.start_date,
            request_end=request.end_date,
            anchor_start=start,
            latest_open_day=end,
        )

        # ---- required trading-calendar refresh --------------------------- #
        # Runs before every other fetch so a calendar failure blocks the run
        # before any window data is pulled, and so the whole update publishes
        # as one atomic unit with its calendar evidence.  A skipped contract
        # window carries the published calendar and its spans unchanged.
        calendar_plan = plans.get("trading_calendar")
        if _plan_skips_fetch(calendar_plan, end):
            calendar_open, calendar_spans = published_open_days, baseline_spans
        else:
            refreshed = self._refresh_calendar(
                (
                    calendar_plan.window_start
                    if calendar_plan is not None
                    else start
                ),
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
        master_plan = plans.get("security_master")
        if _plan_skips_fetch(master_plan, end):
            master_coverage = carried_master_coverage
        else:
            master, master_coverage = self._refresh_security_master(
                (
                    master_plan.window_start
                    if master_plan is not None
                    else start
                ),
                end,
                issues,
                statuses,
                raw_snapshots,
                master,
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
        daily_plan = plans.get("daily_bar")
        daily_skipped = _plan_skips_fetch(daily_plan, end)
        daily_start = (
            daily_plan.window_start
            if daily_plan is not None and daily_plan.window_start is not None
            else start
        )
        if not daily_skipped:
            fatal = self._fetch_primary_stock(
                enabled,
                equity_symbols,
                daily_start,
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

            # ---- proof-only pre-window anchors -------------------------- #
            # Runs before _materialize_suspensions reads the raw frames, and
            # after the primary fetch whose responses it deepens.
            self._deepen_head_anchors(
                enabled,
                equity_symbols,
                master,
                calendar_open,
                daily_start,
                end,
                issues,
                statuses,
                raw_snapshots,
                raw_daily_frames,
            )

            # ---- required benchmark history ----------------------------- #
            benchmark_symbols = tuple(self._project_config.benchmark_symbols)
            benchmark_rows: list[pd.DataFrame] = []
            benchmark_dates: set[date] = set()
            fatal = self._fetch_benchmarks(
                enabled,
                benchmark_symbols,
                daily_start,
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
        else:
            # Nothing to fetch: the baseline daily table is carried untouched
            # and no benchmark lane runs, so ``_missing_issues`` sees an empty
            # grid and never re-judges carried history (spec D5.2).
            benchmark_symbols = tuple(self._project_config.benchmark_symbols)
            benchmark_rows: list[pd.DataFrame] = []
            benchmark_dates: set[date] = set()

        # ---- best-effort corporate actions ------------------------------ #
        corporate_action = current_ca
        corporate_action_quarantine = current_quarantine
        coverage = coverage_frame([])
        if "akshare" in enabled:
            ca_plan = plans.get("corporate_action")
            if _plan_skips_fetch(ca_plan, end):
                # The disclosure-calendar contract window was skipped: the
                # baseline's coverage evidence stands in for the skipped
                # refresh so the publish never loses it (spec D5.2).
                coverage = carried_ca_coverage
            else:
                corporate_action, coverage, corporate_action_quarantine = (
                    self._refresh_corporate_actions(
                        enabled,
                        equity_symbols,
                        (
                            ca_plan.window_start
                            if ca_plan is not None
                            else start
                        ),
                        end,
                        issues,
                        statuses,
                        raw_snapshots,
                        current_ca,
                        current_quarantine,
                        calendar_open,
                    )
                )
                # The refresh re-judges only its own contract window; the
                # baseline's coverage rows for the history before it stay
                # valid evidence and publish beside it, clipped to the day
                # before the fetched window (spec D5.2).
                coverage = _merge_carried_coverage(
                    carried_ca_coverage,
                    coverage,
                    fetch_start=(
                        ca_plan.window_start if ca_plan is not None else start
                    ),
                )

        # ---- optional validation daily ---------------------------------- #
        validation_rows: list[pd.DataFrame] = []
        if "baostock" in enabled and not daily_skipped:
            self._fetch_validation_daily(
                enabled,
                equity_symbols,
                daily_start,
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
        if daily_skipped:
            # Carried forward untouched: the fetch window was empty, so no
            # baseline row inside the request window is replaced.
            new_daily = current_daily
        else:
            new_daily = self._merge_daily(
                current_daily,
                primary_rows,
                benchmark_rows,
                equity_symbols,
                benchmark_symbols,
                daily_start,
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

        # Publish-path contract gate (spec D2): scoped to data_update by
        # construction — bootstrap publishes through DatasetPublisher
        # directly and never reaches update().
        issues.extend(
            _contract_issues(tables, self._project_config.data_contracts)
        )

        # Per-table fetch-coverage evidence (spec D5.3): ``fetched`` segments
        # are the contract windows actually requested this round, ``carried``
        # segments chain the span the baseline recorded, and a table whose
        # contract window the operator's explicit window skipped records the
        # skip with its reason.  Segments are scoped to the review window's
        # acceptance anchor, matching the offline check's window (ADR-011).
        acceptance_anchor = criterion.acceptance_start
        fetch_segments: dict[str, list[FetchSegment]] = {}
        for table, plan in sorted(plans.items()):
            if plan.kind == KIND_NOT_FETCHED:
                low = (
                    max(start, acceptance_anchor)
                    if acceptance_anchor is not None
                    else start
                )
                fetch_segments[table] = [
                    FetchSegment(
                        table,
                        KIND_NOT_FETCHED,
                        min(low, end),
                        end,
                        reason=plan.reason,
                    )
                ]
                continue
            covered = recorded_spans.get(table)
            if covered is None:
                # A baseline that predates recorded spans still covers its own
                # review window; chain from it so the segments tile the
                # acceptance obligation (spec D5.3).
                covered = baseline_covered
            if _plan_skips_fetch(plan, end):
                if covered is not None:
                    low = (
                        max(covered[0], acceptance_anchor)
                        if acceptance_anchor is not None
                        else covered[0]
                    )
                    high = min(covered[1], end)
                    if low <= high:
                        fetch_segments[table] = [
                            FetchSegment(table, KIND_CARRIED, low, high)
                        ]
                continue
            segments: list[FetchSegment] = []
            if covered is not None and covered[0] < plan.window_start:
                low = (
                    max(covered[0], acceptance_anchor)
                    if acceptance_anchor is not None
                    else covered[0]
                )
                high = min(
                    covered[1], plan.window_start - timedelta(days=1)
                )
                if low <= high:
                    segments.append(
                        FetchSegment(table, KIND_CARRIED, low, high)
                    )
            fetched_start = plan.window_start
            if acceptance_anchor is not None:
                fetched_start = max(fetched_start, acceptance_anchor)
            if fetched_start <= end:
                segments.append(
                    FetchSegment(table, KIND_FETCHED, fetched_start, end)
                )
            if segments:
                fetch_segments[table] = segments

        report = QualityReport(issues=tuple(issues))
        # Declared tiers are computed once and reused by the gate, the
        # downgrade pass and the publish call (spec D1 / ADR-010).
        table_tiers = {
            name: contract.tier
            for name, contract in self._project_config.data_contracts.items()
        }
        decision = evaluate_publication(report, table_tiers=table_tiers)
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

        # Downgrade evidence (spec D1): a declared non-core table's table-level
        # blocking issues publish as WARNING coverage_downgraded records
        # instead of blocking; the gate above already blocked core and
        # undeclared tables, so this only extends the passing report.
        downgrades = _downgrade_issues(
            report.issues, self._project_config.data_contracts
        )
        if downgrades:
            issues.extend(downgrades)
            report = QualityReport(issues=tuple(issues))

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
                    table_fetch_coverage=to_build_config_payload(
                        fetch_segments
                    ),
                    baseline_version=baseline_version,
                    raw_snapshot_reuse=_reuse_evidence(self._reuse_counts),
                ),
                table_tiers=table_tiers,
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
        # Call ledger (spec D5.5): after a successful publish, persist the
        # per-source endpoint x count accounting for this update run under
        # ``data/runs/<run_id>/call_ledger.json``.  Only parameter shapes and
        # endpoint names are ever recorded -- never credentials.  The
        # ``reused`` section (ADR-015) lists the requests served from stored
        # snapshots, which consumed no supplier quota.
        write_call_ledger(
            self._project_root,
            run_id,
            render_call_ledger(
                self._active_sources(),
                reused=_reused_ledger_payload(self._reuse_counts),
            ),
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
            self._sources_used[name] = override
            return override
        config = self._project_config.sources[name]
        try:
            source = _build_source(name, config)
        except KeyError as error:
            raise AuthenticationError(
                f"source {name!r} requires {error.args[0]} to be configured"
            ) from None
        except Exception as error:  # noqa: BLE001 - surface cleanly to the CLI
            raise AuthenticationError(
                f"cannot initialise source {name!r}: {translate_supplier_error(error)}"
            ) from None
        self._sources_used[name] = source
        return source

    def _active_sources(self) -> dict[str, DataSource]:
        """The source instances this update actually constructed or used.

        Every access funnels through :meth:`_source` (overrides included), so
        this is exactly the set the fetch stage touched; the call ledger is
        rendered from it after a successful publish (spec D5.5).
        """
        return dict(self._sources_used)

    def _read_baseline(self, issues: list[QualityIssue]):
        """The carried master/calendar/daily/action/membership/quarantine state.

        Returns ``(master, open_days, daily, ca, membership, quarantine,
        baseline_spans, fetch_coverage, baseline_version)`` where
        ``fetch_coverage`` is the pair of (recorded per-table spans, carried
        evidence frames) and ``baseline_version`` names the immutable version
        the carried tables came from (the ``build_config.baseline_version``
        the next manifest publishes so carried segments stay traceable), or
        ``None`` after a FATAL issue when no dataset exists yet.  The
        immutable ``universe_membership`` raw table is carried too when the
        baseline dataset already has one (older datasets simply update
        without it), and a pre-migration dataset without a
        ``corporate_action_quarantine`` table yields an empty canonical frame
        so it stays updatable.  The carried ``security_master_coverage`` /
        ``corporate_action_coverage`` frames stand in for a skipped refresh
        so a carried-forward publish never loses its evidence (spec D5.2).
        ``baseline_spans`` parses the manifest's ``calendar_coverage``
        evidence; a legacy manifest without the key yields no spans and the
        publish-time span gate then requires this window to cover the whole
        published calendar range.
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
            master_coverage = (
                context.read("security_master_coverage")
                if "security_master_coverage" in context.tables
                else None
            )
            ca_coverage = (
                context.read(TABLE_CORPORATE_ACTION_COVERAGE)
                if TABLE_CORPORATE_ACTION_COVERAGE in context.tables
                else None
            )
        open_days = _calendar_open_days(calendar_frame)
        build = manifest.get("build_config") if isinstance(manifest, Mapping) else None
        raw_spans = build.get(COVERAGE_KEY, []) if isinstance(build, Mapping) else []
        recorded_fetch = (
            build.get("table_fetch_coverage")
            if isinstance(build, Mapping)
            else None
        )
        try:
            spans = coverage_from_payload(raw_spans)
        except CalendarCoverageError as error:
            issues.extend(_calendar_issues(error.violations))
            return None
        fetch_coverage = (
            _recorded_fetch_spans(recorded_fetch),
            master_coverage
            if master_coverage is not None
            else master_coverage_frame([]),
            ca_coverage if ca_coverage is not None else coverage_frame([]),
            _baseline_covered_window(build),
        )
        return (
            master,
            open_days,
            daily,
            ca,
            membership,
            quarantine,
            spans,
            fetch_coverage,
            str(ref.version),
        )

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
            dispatched = self._dispatch(
                "tushare", source, "daily", symbol, start, end,
                {"adjustment": "unadjusted"}, required=True, issues=issues,
                reuse=True,
            )
            if dispatched is None:
                statuses["tushare"] = SourceStatus(
                    "tushare", True, False,
                    reason=f"required fetch failed for {symbol}",
                    reason_code="source_fetch_failed",
                )
                return True
            result, snapshot = dispatched
            raw_snapshots.append(snapshot)
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
                dispatched = self._dispatch(
                    "tushare", source, "daily", symbol, chunk_start, chunk_end,
                    {"adjustment": "unadjusted"}, required=False, issues=issues,
                    reuse=True, allow_empty=True,
                )
                if dispatched is None:
                    break
                result, snapshot = dispatched
                raw_snapshots.append(snapshot)
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
            dispatched = self._dispatch(
                "akshare", source, "index_history", symbol, start, end, {},
                required=True, issues=issues, reuse=True,
            )
            if dispatched is None:
                statuses["akshare"] = SourceStatus(
                    "akshare", True, False,
                    reason=f"required fetch failed for {symbol}",
                    reason_code="source_fetch_failed",
                )
                return True
            result, snapshot = dispatched
            raw_snapshots.append(snapshot)
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
        open_days,
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
        arbiter = self._build_action_arbiter(
            symbols, start, end, issues, raw_snapshots, open_days
        )
        factor_channel = self._build_factor_channel(end, issues, raw_snapshots)
        accepted_frames: list[pd.DataFrame] = []
        quarantined_frames: list[pd.DataFrame] = []
        for symbol in symbols:
            accepted, quarantined = self._reconcile_action_frames(
                frames_by_symbol[symbol]["cninfo"],
                frames_by_symbol[symbol]["eastmoney"],
                frames_by_symbol[symbol][RIGHTS_SOURCE],
                issues,
                arbiter,
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
        # ADR-009 decision 1: an ex-date-less row is classified before any
        # rule acts on it.  Recording the classification in the quality
        # report makes it auditable without moving a verdict (ADR-009
        # consequences) -- only this round's newly quarantined rows are
        # classified; carried history predates the record.
        _record_absent_ex_date_classifications(
            quarantined, arbiter, start, end, issues, factor_channel=factor_channel
        )
        # Narrow only the coverage input, once: the exclusion records an INFO
        # trace, so it must not re-run inside the per-symbol comprehension or
        # each symbol would append a duplicate audit row for the same exclusion.
        relevant_quarantine = _window_relevant_quarantine(
            quarantined, start, end, issues
        )
        demoted_by_symbol = _demoted_reasons_by_symbol(
            relevant_quarantine, arbiter, factor_channel
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
                    demoted_reasons=demoted_by_symbol.get(symbol),
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

    def _build_action_arbiter(
        self, symbols, start, end, issues, raw_snapshots, open_days
    ):
        """Build the ADR-007 conflict arbiter, or ``None`` when none is on.

        A chain of best-effort opinions, consulted in order and each asked only
        about a ``(symbol, ex_date)`` the two official sources disagree on.  A
        member is not a source: neither is in ``CORPORATE_ACTION_ENDPOINTS``,
        neither adds a name to ``_CONFIGURED_SOURCES``, and both yield to any
        conflict an owner has signed off on (see ``_GuardedArbiter``).

        Two lanes, gated separately and composing freely:

        * the TDX third-opinion lane (ADR-007), off unless ``sources.yml``
          enables ``tdx`` (the shipped default), and
        * the exchange reference-price lane (ADR-013), off unless ``tushare``
          is enabled -- the segment the lane's ``daily`` endpoint belongs to.

        Either lane may be absent without disabling the other: an ``None`` here
        means both are off, which is the state a run that arbitrates nothing
        produces and the reason the gate moved off TDX alone.

        Both channels are touched lazily: only a symbol with an actual
        cross-source disagreement costs a fetch, so a clean round makes no
        arbiter call at all.  Their responses are written as raw snapshots
        through the same content-addressed store as every supplier, so what
        arbitrated a rebuild is reproducible from bytes rather than from a
        later answer.  Failing to obtain them leaves that symbol's conflicts
        quarantined -- the state a disabled arbiter produces -- and is
        recorded as a warning.
        """
        reviewed = {
            (review.symbol, review.ex_date)
            for review in self._project_config.corporate_action_reviews
            if start <= review.ex_date <= end
        }
        arbiters: list[object] = []
        config = self._project_config.sources.get(ARBITER_NAME)
        if config is not None and config.enabled:
            arbiters.append(
                _LazyActionArbiter(
                    config,
                    start,
                    end,
                    issues=issues,
                    raw_snapshots=raw_snapshots,
                    record_raw=self._record_raw,
                )
            )
        price = self._build_price_channel(issues, raw_snapshots, open_days)
        if price is not None:
            channel, report_settlement = price
            arbiters.append(
                PriceObservedArbiter(channel.observe, record=report_settlement)
            )
        if not arbiters:
            return None
        return _GuardedArbiter(
            FirstAnsweringArbiter(arbiters, issues), issues, reviewed
        )

    def _build_factor_channel(self, end: date, issues, raw_snapshots):
        """Build the ADR-009 baostock channel, or ``None`` when it is off.

        The channel serves only the absent-ex-date classification and stays
        fail-closed: a disabled baostock asserts nothing rather than letting a
        window conclude an absence from silence.
        """
        config = self._project_config.sources.get("baostock")
        if config is None or not config.enabled:
            return None
        return _LazyFactorChannel(
            config,
            end,
            issues=issues,
            raw_snapshots=raw_snapshots,
            record_raw=self._record_raw,
        )

    def _build_price_channel(self, issues, raw_snapshots, open_days):
        """Build the ADR-013 reference-price lane, or ``None`` when it is off.

        Gated on the existing ``tushare`` segment rather than one of its own:
        the lane reads that source's ``daily`` endpoint and adds no name to
        ``_CONFIGURED_SOURCES``.  Its responses go through ``self._source`` so a
        test override replaces them exactly as it replaces the source's bars.

        Returns the channel with the reporter that turns a settlement into the
        run's own quality issue -- the issue vocabulary lives in this module,
        and the settlement rule must not import it.
        """

        def report_failure(symbol, error):
            issues.append(
                _issue(
                    Severity.WARNING,
                    CODE_OPTIONAL_SOURCE_FAILURE,
                    details={
                        "source": TUSHARE_SOURCE,
                        "endpoint": DAILY_ENDPOINT,
                        "symbol": symbol,
                        "message": str(error),
                    },
                )
            )

        def report_settlement(cninfo, eastmoney, settlement, observation):
            issues.append(_price_settlement_issue(cninfo, settlement, observation))

        config = self._project_config.sources.get(TUSHARE_SOURCE)
        if config is None or not config.enabled:
            return None

        def fetch_daily(symbol, start, ex_date):
            return self._source(TUSHARE_SOURCE).fetch(
                DataRequest(DAILY_ENDPOINT, (symbol,), start, ex_date)
            )

        return (
            LazyDailyPriceChannel(
                fetch_daily,
                open_days,
                on_failure=report_failure,
                raw_snapshots=raw_snapshots,
                record_raw=self._record_raw,
            ),
            report_settlement,
        )

    def _reconcile_action_frames(
        self,
        cninfo_frames,
        eastmoney_frames,
        allotment_frames,
        issues,
        arbiter=None,
    ):
        """Reconcile collected supplier frames; empty defaults on failure.

        The two dividend lanes are reconciled against each other; the allotment
        lane is normalized on its own because a subscription is reported by one
        source only, so there is nothing to cross-confirm it against.  The two
        lanes fail independently: a malformed dividend frame must not also
        discard the symbol's subscriptions, and vice versa.

        ``arbiter`` (ADR-007) is consulted only on a cross-source disagreement.
        It is passed already guarded: see ``_GuardedArbiter``.
        """
        try:
            reconciled = normalize_corporate_actions(
                _concat(cninfo_frames), _concat(eastmoney_frames), arbiter=arbiter
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
            dispatched = self._dispatch(
                "baostock", source, "daily", symbol, start, end,
                {"adjustment": "unadjusted"}, required=False, issues=issues,
                reuse=True,
            )
            if dispatched is None:
                failures += 1
                continue
            result, snapshot = dispatched
            raw_snapshots.append(snapshot)
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
        reuse: bool = False,
        allow_empty: bool = False,
    ):
        """One per-symbol request: ``(result, snapshot)`` or ``None``.

        With ``reuse`` the raw store is consulted first (ADR-015): an exact
        match on endpoint x symbol x window x params whose stored bytes still
        verify is answered from disk and never reaches ``fetch_with_retry``.
        A stored candidate that exists but is refused -- tampered bytes, a
        foreign request shape, an empty frame -- is a visible
        ``reuse_candidate_rejected`` warning before the live request runs
        under the normal retry policy.  The snapshot is returned alongside
        the result so the caller records the exact evidence this answer came
        from, whether it was fetched live or read back.

        Eligibility is enforced inside ``RawStore.resolve_reusable`` via
        ``REUSABLE_CHANNELS``; passing ``reuse=True`` only asks for the
        lookup, it cannot widen the channel set.
        """
        config: SourceConfig = self._project_config.sources.get(
            name, SourceConfig()
        )
        policy = RetryPolicy(
            max_attempts=min(config.max_retries + 1, 3),
            maximum_wait_seconds=min(config.timeout_seconds, 30),
            call_timeout_seconds=config.timeout_seconds,
        )
        request = DataRequest(endpoint, (symbol,), start, end, params)
        if reuse:
            resolved = self._raw_store.resolve_reusable(
                name, endpoint, request, allow_empty=allow_empty
            )
            if resolved is not None:
                snapshot, frame = resolved
                self._count_fetch(name, endpoint, "reused")
                return (
                    FetchResult(
                        source=name,
                        endpoint=endpoint,
                        request_key=request_key(request),
                        frame=frame,
                        metadata=dict(snapshot.manifest.get("metadata") or {}),
                    ),
                    snapshot,
                )
            if issues is not None and self._raw_store.has_candidate(
                name, endpoint, request
            ):
                issues.append(
                    _issue(
                        Severity.WARNING,
                        CODE_REUSE_CANDIDATE_REJECTED,
                        symbol=symbol,
                        details={"source": name, "endpoint": endpoint},
                    )
                )
        try:
            result = fetch_with_retry(
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
        self._count_fetch(name, endpoint, "fetched")
        return result, self._record_raw(result)

    def _count_fetch(self, name: str, endpoint: str, kind: str) -> None:
        """Count one dispatched request as ``reused`` or ``fetched`` (ADR-015)."""
        row = self._reuse_counts.setdefault(name, {}).setdefault(
            endpoint, {"reused": 0, "fetched": 0}
        )
        row[kind] += 1

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
    demoted_reasons: set[str] | None = None,
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
        demoted_reasons=demoted_reasons,
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


#: Quarantine reasons that are *evidence about a correctly reported event*
#: rather than an unaccounted gap.  A refused 重整转增 (ADR-008) says the
#: supplier described a real, complete event that is not a price event, so it
#: leaves the window as accounted-for as if no event had been reported at all.
#: Every other reason -- and any reason not named here -- still withholds trust,
#: so a reason added later fails closed.
#:
#: The exemption is evidence-conditional (ADR-012): it rests on the reported
#: event being no price event, so a row it would exempt stops exempting --
#: demoted back to blocking for its symbol -- when an admissible ADR-009
#: channel observes a market adjustment at the row's probe date.
_NON_BLOCKING_QUARANTINE_REASONS = frozenset({REASON_NON_DISTRIBUTIVE_RESTRUCTURING})


def _demoted_reasons_by_symbol(
    relevant_quarantine: pd.DataFrame,
    arbiter: object | None,
    factor_channel: "_LazyFactorChannel | None",
) -> dict[str, set[str]]:
    """Deny-listed reasons that a row's classification strips the exemption from.

    ADR-012, per ADR-009 decision 4's requirement that any rule moving a
    verdict name the classification it relies on: the demoting classification
    is ``adjustment_observed`` on the market axis -- a stated supplier ex-date
    is probed there, an absent one at its announcement anchor -- read from
    the two admissible channels (TDX category-1 records, baostock's factor
    series), both lazily fetched and cached.  A row whose market axis reads
    bracketed-empty or unknown keeps the exemption: absence asserted from
    silence is the old ADR-008 ground, and unknown stays fail-closed.
    """
    if relevant_quarantine is None or relevant_quarantine.empty:
        return {}
    frame_for = getattr(arbiter, "frame_for", None) if arbiter is not None else None
    demoted: dict[str, set[str]] = {}
    for record in relevant_quarantine.to_dict("records"):
        reason = str(record.get("reason") or "")
        if reason not in _NON_BLOCKING_QUARANTINE_REASONS:
            continue
        symbol = str(record["symbol"])
        ex_date = _as_date(record.get("ex_date"))
        probe = ex_date or _as_date(record.get("announcement_date"))
        if probe is None:
            continue
        tdx_dates: list[date] = []
        if frame_for is not None:
            frame = frame_for(symbol)
            if frame is not None and not frame.empty and "category" in frame.columns:
                distribution = frame[frame["category"] == XDXR_CATEGORY_DISTRIBUTION]
                tdx_dates = [
                    day
                    for day in (_as_date(value) for value in distribution["date"])
                    if day is not None
                ]
        baostock_dates = factor_channel(symbol) if factor_channel else None
        classification = classify_ex_date(
            supplier_ex_date=ex_date,
            probe_date=probe,
            channels=(("tdx", tdx_dates), ("baostock", baostock_dates)),
        )
        if classification.market == MARKET_ADJUSTMENT_OBSERVED:
            demoted.setdefault(symbol, set()).add(reason)
    return demoted


def _coverage_verdict(
    outcomes: dict[str, dict[str, object]],
    *,
    has_accepted: bool,
    quarantine_reasons: set[str] | None,
    demoted_reasons: set[str] | None = None,
) -> tuple[CoverageStatus, CoverageReason | None]:
    """Decide one symbol/window's status from its endpoint fetch outcomes.

    ``VERIFIED`` requires a fully accounted window: every requested endpoint
    answered, at least one returned events, and the symbol holds an accepted
    reconciled fact with no *blocking* quarantine.  A quarantined event *that
    can affect this window* (cross-source conflict / unsupported action /
    incomplete record) makes the window ``UNTRUSTED`` even when a sibling event
    for the same symbol/window was accepted, so a conflicting or unbooked event
    can never be masked by an accepted row while the coverage reads
    ``VERIFIED``.  The reasons listed in ``_NON_BLOCKING_QUARANTINE_REASONS``
    are excluded from that rule -- except where ``demoted_reasons`` names them
    for this symbol (ADR-012: an observed market adjustment at the row's probe
    date strips the exemption).  A row whose every known date lies outside the
    window is excluded by ``_window_relevant_quarantine`` before this decision
    (ADR-006).
    """
    if any(not outcome["ok"] for outcome in outcomes.values()):
        return CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_FETCH_FAILED
    if not any(not outcome["empty"] for outcome in outcomes.values()):
        return CoverageStatus.VERIFIED_EMPTY, None
    if quarantine_reasons:
        blocking = quarantine_reasons - _NON_BLOCKING_QUARANTINE_REASONS
        blocking |= quarantine_reasons & (demoted_reasons or set())
    else:
        blocking = set()
    if blocking:
        return (
            CoverageStatus.UNTRUSTED,
            _coverage_reason_for_quarantine(blocking),
        )
    if has_accepted:
        return CoverageStatus.VERIFIED, None
    # ADR-006's principle carried to its end (ADR-012): the endpoints answered,
    # no quarantined row can affect this window, and nothing was accepted -- so
    # every event any supplier reported is either refused non-price evidence or
    # provably about another period.  "Nothing happened here" is positively
    # supported, not an unaccounted gap.
    return CoverageStatus.VERIFIED_EMPTY, None


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


def _warn_arbiter_failure(issues, symbol, error) -> None:
    """Record the conflict arbiter's failure as an optional-source warning.

    Shaped like the endpoint-failure warnings this sits beside: the symbol
    travels in ``details`` so both arrive under one code with one layout.
    """
    issues.append(
        _issue(
            Severity.WARNING,
            CODE_OPTIONAL_SOURCE_FAILURE,
            details={
                "source": ARBITER_NAME,
                "endpoint": XDXR_ENDPOINT,
                "symbol": symbol,
                "message": str(error),
            },
        )
    )


def _record_absent_ex_date_classifications(
    quarantined: pd.DataFrame,
    arbiter: object | None,
    start: date,
    end: date,
    issues: list[QualityIssue],
    factor_channel: "_LazyFactorChannel | None" = None,
) -> None:
    """Record the ADR-009 classification of newly quarantined ex-date-less rows.

    Decision 1 requires the classification to exist before the date-
    completeness rule acts; recording it in the quality report makes it
    auditable without moving any verdict (ADR-009 consequences: no behaviour
    changes).  Only rows this round newly quarantined are classified --
    carried history predates the record -- and the probe date is the row's
    announcement date, the best-known anchor when the supplier states no
    ex-date.  The admissible channels (ADR-009 decision 3): TDX category-1
    records via the lazy arbiter's frames, and baostock's adjustment-factor
    series via ``factor_channel``; an absent channel asserts nothing, so an
    unbracketed row reads ``unknown`` and stays blocking (decision 2).
    """
    if quarantined is None or quarantined.empty:
        return
    frame_for = getattr(arbiter, "frame_for", None) if arbiter is not None else None
    if frame_for is None and factor_channel is None:
        return
    channels_consulted = [
        name
        for name, channel in (
            ("tdx", frame_for),
            ("baostock", factor_channel),
        )
        if channel is not None
    ]
    for record in quarantined.to_dict("records"):
        if record.get("reason") != REASON_INCOMPLETE:
            continue
        if record.get("ex_date") is not None:
            continue
        raw_announcement = record.get("announcement_date")
        try:
            announcement = pd.Timestamp(raw_announcement).date()
        except (TypeError, ValueError):
            continue
        if not (start <= announcement <= end):
            continue
        symbol = str(record["symbol"])
        tdx_dates: list[date] = []
        if frame_for is not None:
            frame = frame_for(symbol)
            if frame is not None and not frame.empty and "category" in frame.columns:
                distribution = frame[frame["category"] == XDXR_CATEGORY_DISTRIBUTION]
                tdx_dates = [
                    day
                    for day in (_as_date(value) for value in distribution["date"])
                    if day is not None
                ]
        baostock_dates = factor_channel(symbol) if factor_channel else None
        classification = classify_ex_date(
            supplier_ex_date=None,
            probe_date=announcement,
            channels=(("tdx", tdx_dates), ("baostock", baostock_dates)),
        )
        issues.append(
            _issue(
                Severity.INFO,
                CODE_ABSENT_EX_DATE_CLASSIFIED,
                table=TABLE_CORPORATE_ACTION_QUARANTINE,
                symbol=symbol,
                details={
                    "classification": classification.code,
                    "probe_date": announcement.isoformat(),
                    "channels": channels_consulted,
                },
            )
        )


class _LazyActionArbiter:
    """Fetch the TDX opinion for a disputed symbol on first need.

    The arbiter is consulted only on a cross-source disagreement, so a clean
    round never touches the TDX channel at all.  A symbol's xdxr frame is
    fetched the first time one of its conflicts needs arbitration, recorded
    as a raw snapshot through the same content-addressed store as every
    supplier, and cached for the symbol's later keys.  Anything the channel
    raises degrades to "no arbitration" for that symbol -- the same outcome a
    disabled arbiter produces -- is recorded as a warning, and is not retried
    within the run.
    """

    def __init__(
        self,
        config: SourceConfig,
        start: date,
        end: date,
        *,
        issues: list[QualityIssue],
        raw_snapshots: list[RawSnapshot],
        record_raw: Any,
    ) -> None:
        self._config = config
        self._start = start
        self._end = end
        self._issues = issues
        self._raw_snapshots = raw_snapshots
        self._record_raw = record_raw
        self._frames: dict[str, pd.DataFrame] = {}
        self._failed: set[str] = set()
        self.name = ARBITER_NAME

    def frame_for(self, symbol: str) -> pd.DataFrame | None:
        """The symbol's cached xdxr frame, fetching it on first need.

        Shares the fetch/fail cache with :meth:`arbitrate`: a symbol whose
        channel read already failed is not retried, and ``None`` means no
        frame is available (an absent channel asserts nothing, ADR-009).
        """
        if symbol in self._frames:
            frame = self._frames[symbol]
            if frame is None or frame.empty:
                return None
            return frame
        if symbol in self._failed:
            return None
        try:
            frames = fetch_xdxr_frames(
                [symbol], timeout=float(self._config.timeout_seconds)
            )
        except Exception as error:  # noqa: BLE001 - best-effort third opinion
            _warn_arbiter_failure(self._issues, symbol, error)
            self._failed.add(symbol)
            return None
        frame = frames.get(symbol)
        self._frames[symbol] = frame if frame is not None else pd.DataFrame()
        if frame is not None and not frame.empty:
            self._raw_snapshots.append(
                self._record_raw(
                    FetchResult(
                        source=ARBITER_NAME,
                        endpoint=XDXR_ENDPOINT,
                        request_key=request_key(
                            DataRequest(
                                XDXR_ENDPOINT, (symbol,), self._start, self._end
                            )
                        ),
                        frame=frame,
                        metadata={"transport_id": ARBITER_NAME},
                    )
                )
            )
        frame = self._frames[symbol]
        if frame is None or frame.empty:
            return None
        return frame

    def arbitrate(self, cninfo: Any, eastmoney: Any) -> str | None:
        symbol = str(cninfo.symbol)
        frame = self.frame_for(symbol)
        if frame is None:
            return None
        arbiter = TdxXdxrArbiter.from_frames({symbol: frame})
        return arbiter.arbitrate(cninfo, eastmoney)


class _LazyFactorChannel:
    """baostock's adjustment-factor series, fetched on first need (ADR-009).

    The second admissible price-event channel of the absent-ex-date
    classification: consulted only for a row the recorder classifies, cached
    per symbol, and written as a raw snapshot through the same
    content-addressed store as every supplier.  Anything the channel raises
    degrades to an absent channel -- it asserts nothing, the fail-closed
    direction -- recorded as a warning and not retried within the run.
    """

    def __init__(
        self,
        config: SourceConfig,
        end: date,
        *,
        issues: list[QualityIssue],
        raw_snapshots: list[RawSnapshot],
        record_raw: Any,
    ) -> None:
        self._config = config
        self._end = end
        self._issues = issues
        self._raw_snapshots = raw_snapshots
        self._record_raw = record_raw
        self._events: dict[str, list[date]] = {}
        self._failed: set[str] = set()

    def __call__(self, symbol: str) -> list[date] | None:
        if symbol in self._events:
            return self._events[symbol]
        if symbol in self._failed:
            return None
        try:
            frames = fetch_adjust_factor_frames(
                [symbol], timeout=float(self._config.timeout_seconds), end=self._end
            )
        except Exception as error:  # noqa: BLE001 - best-effort evidence channel
            self._issues.append(
                _issue(
                    Severity.WARNING,
                    CODE_OPTIONAL_SOURCE_FAILURE,
                    details={
                        "source": "baostock",
                        "endpoint": "adjust_factor",
                        "symbol": symbol,
                        "message": str(error),
                    },
                )
            )
            self._failed.add(symbol)
            return None
        frame = frames.get(symbol)
        if frame is not None and not frame.empty:
            self._raw_snapshots.append(
                self._record_raw(snapshot_result(symbol, frame, end=self._end))
            )
        events = factor_event_dates(frame)
        self._events[symbol] = events
        return events


class FirstAnsweringArbiter:
    """Ask each conflict arbiter in turn; the first to name a side books it.

    Order is policy, not preference: TDX is a genuinely independent third
    opinion (ADR-007) while the price lane is an independent verification path
    that shares the issuer's announcement as its origin with CNINFO (ADR-013),
    so the stronger evidence is asked first and only a refusal falls through.

    ``name`` always holds the name of the arbiter that last answered, so the
    ``<side>+<authority>`` label ``_arbitrated_event`` builds names who decided
    rather than who was asked first.  That is why ``_GuardedArbiter.name`` has
    to read through instead of copying at construction.

    A lane that raises is reported and skipped: it asserted nothing, which says
    nothing about the lanes behind it.  With every lane down the answer is
    ``None``, so the conflict keeps its quarantine.
    """

    def __init__(self, arbiters, issues) -> None:
        self._arbiters = tuple(arbiters)
        if not self._arbiters:
            raise ValueError("an arbiter chain needs at least one arbiter")
        self._issues = issues
        self.name = self._arbiters[0].name

    def arbitrate(self, cninfo, eastmoney):
        for arbiter in self._arbiters:
            try:
                side = arbiter.arbitrate(cninfo, eastmoney)
            except Exception as error:  # noqa: BLE001 - best-effort opinion
                _warn_arbiter_failure(self._issues, cninfo.symbol, error)
                continue
            if side is not None:
                self.name = arbiter.name
                return side
        return None

    def frame_for(self, symbol: str):
        """The first inner arbiter holding a frame for ``symbol``.

        ADR-009's classification consults the same lazily fetched frames the
        arbitration uses, so the chain forwards the hook rather than hiding it.
        """
        for arbiter in self._arbiters:
            frame_for = getattr(arbiter, "frame_for", None)
            if frame_for is None:
                continue
            frame = frame_for(symbol)
            if frame is not None:
                return frame
        return None


class _GuardedArbiter:
    """An arbiter that yields to a signed review and cannot lose a lane.

    Both rules keep an automated third opinion strictly weaker than the paths
    it sits beside:

    * A ``(symbol, ex_date)`` an owner has explicitly reviewed is never
      arbitrated.  ``apply_corporate_action_reviews`` resolves a conflict by
      requiring exactly one quarantined row for the reviewed source and
      *raises* when it finds none -- and ``_reconcile_action_frames`` turns that
      raise into an empty dividend lane for the symbol.  A conflict arbitrated
      first would be booked, leave the review nothing to match, and cost the
      symbol every fact it had.
    * Anything the arbiter raises degrades to "no arbitration" for that key --
      the same outcome a disabled arbiter produces -- and records why.
    """

    def __init__(self, arbiter, issues, reviewed=frozenset()) -> None:
        self._arbiter = arbiter
        self._issues = issues
        self._reviewed = reviewed

    @property
    def name(self):
        """The inner arbiter's current name.

        Read through rather than copied at construction: a first-answering
        chain renames itself to whichever lane decided, and
        ``_arbitrated_event`` reads ``name`` *after* ``arbitrate`` to build the
        ``<side>+<authority>`` label.
        """
        return self._arbiter.name

    def frame_for(self, symbol: str):
        """The inner arbiter's raw frame for ``symbol``, when it has one.

        ADR-009's classification records consult the same lazily fetched
        frames the arbitration uses; an inner arbiter without frame access
        (a test double, a plain ``TdxXdxrArbiter``) asserts nothing.
        """
        frame_for = getattr(self._arbiter, "frame_for", None)
        if frame_for is None:
            return None
        return frame_for(symbol)

    def arbitrate(self, cninfo, eastmoney):
        if (cninfo.symbol, cninfo.ex_date) in self._reviewed:
            return None
        try:
            return self._arbiter.arbitrate(cninfo, eastmoney)
        except Exception as error:  # noqa: BLE001 - best-effort third opinion
            _warn_arbiter_failure(self._issues, cninfo.symbol, error)
            return None


def _contract_issues(
    tables: Mapping[str, object], contracts: Mapping[str, object]
) -> list[QualityIssue]:
    """FATAL ``unregistered_table`` for every table missing a D2 declaration."""
    issues: list[QualityIssue] = []
    for name in sorted(tables):
        if name not in contracts:
            issues.append(
                _issue(Severity.FATAL, CODE_UNREGISTERED_TABLE, table=name)
            )
    return issues


def _tier_of(contract: object) -> str | None:
    """The declared tier of one contract entry.

    Accepts either a D2 ``DataContract`` (the config-loaded shape the pipeline
    holds) or a plain tier string (the mapping shape the publish gate takes),
    so one helper serves both callers.
    """
    if isinstance(contract, str):
        return contract
    tier = getattr(contract, "tier", None)
    return tier if isinstance(tier, str) else None


def _downgrade_issues(
    issues: Sequence[QualityIssue], contracts: Mapping[str, object]
) -> list[QualityIssue]:
    """UNTRUSTED coverage records for non-core tables with blocking codes.

    Per spec D1 the record follows the corporate_action_coverage shape:
    per-symbol-window coverage evidence.  A table-level issue without a
    symbol covers the whole table (``symbols=None``, window ``None``);
    symbol-scoped issues carry the same scoping the source issue has.  Only
    ``TABLE_LEVEL_BLOCKING_CODES`` downgrade: global process codes always
    block, a missing declaration blocks fail-closed, and a core table blocks
    instead of downgrading.
    """
    records: list[QualityIssue] = []
    for item in issues:
        if item.code not in TABLE_LEVEL_BLOCKING_CODES:
            continue
        tier = _tier_of(contracts.get(item.table))
        if tier is None or TIER_BLOCKS_PUBLICATION.get(tier, True):
            continue
        records.append(
            _issue(
                Severity.WARNING,
                CODE_COVERAGE_DOWNGRADED,
                table=item.table,
                symbol=item.symbol,
                details={
                    "status": "UNTRUSTED",
                    "reason_codes": [item.code],
                    "symbols": None if item.symbol is None else [item.symbol],
                    "window_start": (
                        None
                        if item.trade_date is None
                        else item.trade_date.isoformat()
                    ),
                    "window_end": (
                        None
                        if item.trade_date is None
                        else item.trade_date.isoformat()
                    ),
                },
            )
        )
    return records


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


def _price_settlement_issue(cninfo, settlement, observation) -> QualityIssue:
    """The INFO trace of one price-observed settlement (spec D3).

    The label names the winner; this names the arithmetic that chose it and the
    stored bytes the reference price came from, so a settlement can be
    recomputed from the raw snapshot rather than taken on trust.
    """
    return _issue(
        Severity.INFO,
        CODE_PRICE_OBSERVED_SETTLEMENT,
        symbol=cninfo.symbol,
        trade_date=cninfo.ex_date,
        details={
            **settlement.to_details(),
            "symbol": cninfo.symbol,
            "ex_date": cninfo.ex_date.isoformat(),
            "snapshot_sha256": observation.snapshot_sha256,
        },
    )
