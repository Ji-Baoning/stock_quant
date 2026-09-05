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
normalizing, merges the fresh canonical bars/corporate actions over the existing
immutable dataset (security master and trading calendar are carried unchanged),
renders the shared quality report, and publishes **only** when the neutral gate
passes and no ``FATAL`` pipeline issue exists.  A blocked or failed update
returns ``dataset_ref is None`` while remaining diagnosable through
``quality_report`` / ``source_status`` / ``raw_snapshots``.

Live end-date discovery (``--end`` omitted) reuses the previously published
dataset as the coverage evidence for ``resolve_latest_complete_date``; an
explicit ``--end`` bypasses discovery but the merged window still runs the full
quality checks.  This is engineering scaffolding for the MVP, not evidence of
alpha -- nothing here is investment advice.
"""

from __future__ import annotations

import time as _sleep_module
import uuid
from dataclasses import dataclass
from datetime import date
from datetime import datetime as _datetime
from datetime import time as dt_time
from typing import Any, Mapping

import pandas as pd

from stock_quant.config import SourceConfig, load_project_config
from stock_quant.data_model.calendar import TradingCalendar
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
    normalize_corporate_actions,
)
from stock_quant.data_model.dataset import (
    DatasetNotFoundError,
    DatasetPublisher,
    DatasetReader,
    PublicationBlocked,
)
from stock_quant.data_model.normalize import normalize_daily
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    DAILY_COLUMNS,
    DAILY_SCHEMA,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import (
    TABLE_CORPORATE_ACTION,
    QualityIssue,
    QualityReport,
    Severity,
)
from stock_quant.data_quality.raw_checks import (
    check_daily_values,
    check_primary_key_conflicts,
    check_provenance,
    check_schema,
    classify_missing_row,
)
from stock_quant.data_sources.base import (
    AuthenticationError,
    DataRequest,
    DataSource,
    RetryPolicy,
    fetch_with_retry,
    translate_supplier_error,
)
from stock_quant.data_sources.raw_store import RawStore

# --------------------------------------------------------------------------- #
# Pipeline-level issue codes (kept out of the shared neutral-gate vocabulary:
# they are gate conditions owned by this module, never by the dataset gate).
# --------------------------------------------------------------------------- #

CODE_SOURCE_FETCH_FAILED = "source_fetch_failed"
CODE_REQUIRED_SOURCE_DISABLED = "required_source_disabled"
CODE_NO_CURRENT_DATASET = "no_current_dataset"
CODE_DATA_DISCOVERY_UNRESOLVED = "data_discovery_unresolved"
CODE_OPTIONAL_SOURCE_FAILURE = "optional_source_failure"
CODE_UNIVERSE_MASTER_MISMATCH = "universe_master_mismatch"

_CONFIGURED_SOURCES = ("tushare", "akshare", "baostock")
_REQUIRED_ROLE = {"tushare": True, "akshare": True, "baostock": False}


# --------------------------------------------------------------------------- #
# Public value types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceStatus:
    """Outcome of one configured source within an update."""

    source: str
    required: bool
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class SourceCoverage:
    """Latest *contiguous* complete open day each data role has reached.

    ``stock_primary`` is the required primary stock daily series; ``benchmarks``
    maps each benchmark symbol to its latest complete open day; ``validation``
    maps each *non-primary* source actually present (``akshare`` benchmark
    frames and any ``baostock`` optional validation series) to its latest
    complete open day.
    """

    stock_primary: date | None
    benchmarks: Mapping[str, date]
    validation: Mapping[str, date]


@dataclass(frozen=True)
class DataUpdateRequest:
    """Idempotent request for one data update run.

    ``end_date`` ``None`` triggers latest-complete-date discovery over the
    currently published dataset; ``sources`` restricts to a subset of the
    configured names.
    """

    start_date: date | None = None
    end_date: date | None = None
    sources: tuple[str, ...] | None = None


@dataclass(frozen=True)
class DataUpdateResult:
    """One update's outcome: always diagnosable, published only when gated.

    ``resolved_end_is_fallback`` records whether ``resolved_end_date`` was
    reached by walking back from the nominal latest complete open day (design
    spec §14: "条件不满足时回退到上一个确认完整交易日并在报告注明").  It is
    ``False`` when the end date was either chosen explicitly (``--end``) or is
    the nominal date with full coverage.
    """

    quality_report: QualityReport
    dataset_ref: Any
    run_id: str
    resolved_end_date: date | None
    source_status: tuple[SourceStatus, ...]
    raw_snapshots: tuple[str, ...]
    resolved_end_is_fallback: bool = False


def _nominal_candidate(
    calendar: TradingCalendar, publication_time: dt_time
) -> date | None:
    """The newest open day an operator could call complete given only the clock.

    Today when today is an open day and ``publication_time`` has already passed;
    otherwise the newest open day strictly before today.  A non-trading today
    never counts, so discovery always walks to a real open day.  ``None`` for an
    empty calendar.
    """
    open_days = calendar.open_days
    if not open_days:
        return None
    now = _datetime.now()
    today = now.date()
    if calendar.is_trading_day(today):
        if now.time() >= publication_time:
            return today
        return _previous_open_day(open_days, today)
    return _previous_open_day(open_days, today)


def resolve_latest_complete_date(
    status: SourceCoverage,
    calendar: TradingCalendar,
    publication_time: dt_time,
) -> date | None:
    """Return the newest open day every required data role is complete through.

    A day ``d`` is *complete* when the primary stock series, every benchmark
    symbol and at least one validation source each reach ``d``.  Walking starts
    at the nominal candidate (see :func:`_nominal_candidate`); when the nominal
    day is not complete the walk continues backwards over confirmed open days.
    Returns ``None`` when no open day in the calendar satisfies the
    requirements.
    """
    open_days = calendar.open_days
    if not open_days:
        return None
    first_candidate = _nominal_candidate(calendar, publication_time)
    if first_candidate is None:
        return None
    candidates = [
        day for day in reversed(open_days) if day <= first_candidate
    ]
    for day in candidates:
        if _day_complete(status, day):
            return day
    return None


def _previous_open_day(open_days: tuple[date, ...], day: date) -> date | None:
    for candidate in reversed(open_days):
        if candidate < day:
            return candidate
    return None


def _day_complete(status: SourceCoverage, day: date) -> bool:
    if status.stock_primary is None or status.stock_primary < day:
        return False
    if not status.benchmarks:
        return False
    if any(latest is None or latest < day for latest in status.benchmarks.values()):
        return False
    if not status.validation:
        return False
    return any(
        latest is not None and latest >= day
        for latest in status.validation.values()
    )


# --------------------------------------------------------------------------- #
# DataPipeline
# --------------------------------------------------------------------------- #


class DataPipeline:
    """Fetch, normalise, quality-check and publish one data update."""

    def __init__(
        self,
        project_root,
        *,
        config_root=None,
        sources: Mapping[str, DataSource] | None = None,
        sleeper=_sleep_module.sleep,
    ) -> None:
        self._project_root = type(project_root)(project_root)
        self._config_root = (
            type(project_root)(config_root) if config_root is not None
            else self._project_root
        )
        self._overrides = dict(sources or {})
        self._sleeper = sleeper
        self._project_config = load_project_config(self._config_root)
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
        issues.extend(self._universe_master_issues(master))
        return QualityReport(issues=tuple(issues))

    def update(self, request: DataUpdateRequest) -> DataUpdateResult:
        """Run one gated, raw-preserving data update."""
        run_id = f"data_update_{uuid.uuid4().hex[:12]}"
        statuses: dict[str, SourceStatus] = {}
        issues: list[QualityIssue] = []
        raw_snapshots: list[str] = []

        enabled = self._enabled_names(request)
        for name in _CONFIGURED_SOURCES:
            statuses[name] = SourceStatus(
                source=name,
                required=_REQUIRED_ROLE[name],
                ok=False,
                reason="not_run",
            )

        # ---- the carried, immutable baseline --------------------------- #
        baseline = self._read_baseline(issues)
        if baseline is None:
            statuses = {name: status for name, status in statuses.items()}
            return self._result(
                issues, None, run_id, None, statuses, raw_snapshots
            )
        master, calendar_open, current_daily, current_ca = baseline
        issues.extend(self._universe_master_issues(master))

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

        end = request.end_date
        resolved_end: date | None = end
        end_fallback = False
        if end is None:
            resolved_end, end_fallback = self._discover_end(
                calendar_open, current_daily
            )
            if resolved_end is None:
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_DATA_DISCOVERY_UNRESOLVED,
                        details={
                            "message": (
                                "no latest complete date could be resolved; pass "
                                "an explicit --end or update the calendar first"
                            )
                        },
                    )
                )
                return self._result(
                    issues, None, run_id, None, statuses, raw_snapshots
                )
            end = resolved_end
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

        # ---- required primary stock daily ------------------------------- #
        equity_symbols = _equity_symbols(master)
        primary_rows: list[pd.DataFrame] = []
        primary_dates: set[date] = set()
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
        )
        if fatal:
            return self._result(
                issues,
                None,
                run_id,
                end,
                statuses,
                raw_snapshots,
                resolved_end_is_fallback=end_fallback,
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
                resolved_end_is_fallback=end_fallback,
            )

        # ---- best-effort corporate actions ------------------------------ #
        corporate_action = current_ca
        coverage = coverage_frame([])
        if "akshare" in enabled:
            corporate_action, coverage = self._refresh_corporate_actions(
                enabled,
                equity_symbols,
                start,
                end,
                issues,
                statuses,
                raw_snapshots,
                current_ca,
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
                resolved_end_is_fallback=end_fallback,
            )

        # ---- publish ---------------------------------------------------- #
        tables = {
            "daily_bar": new_daily,
            "security_master": master[list(SECURITY_MASTER_COLUMNS)],
            "corporate_action": corporate_action[
                list(CORPORATE_ACTION_COLUMNS)
            ],
            "corporate_action_coverage": coverage,
            "trading_calendar": _calendar_frame(calendar_open)[
                list(TRADING_CALENDAR_COLUMNS)
            ],
        }
        try:
            dataset_ref = DatasetPublisher(self._project_root).publish(
                tables, report, build_config={"run_id": run_id}
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
                resolved_end_is_fallback=end_fallback,
            )
        return self._result(
            issues,
            dataset_ref,
            run_id,
            end,
            statuses,
            raw_snapshots,
            resolved_end_is_fallback=end_fallback,
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
            self._config_root / "configs" / "universe.yml"
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
        """The carried master/calendar/current tables, or None + fatal issue."""
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
        with reader.open(ref.version) as context:
            master = context.read("security_master")
            calendar_frame = context.read("trading_calendar")
            daily = context.read("daily_bar")
            ca = context.read(TABLE_CORPORATE_ACTION)
        open_days = tuple(
            sorted(
                day.date()
                for day, flag in zip(
                    calendar_frame["calendar_date"],
                    calendar_frame["is_trading_day"],
                )
                if bool(flag)
            )
        )
        return master, open_days, daily, ca

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
                )
                return True
            raw_snapshots.append(self._record_raw(result))
            clean = normalize_daily(
                result.frame, "tushare", _ingest_time(result.metadata)
            )
            primary_rows.append(clean.valid)
            primary_dates.update(clean.valid["trade_date"].dt.date)
        statuses["tushare"] = SourceStatus("tushare", True, True)
        return False

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
                    "akshare", True, False, reason=str(error)
                )
                return True
            benchmark_rows.append(clean)
            benchmark_dates.update(clean["trade_date"].dt.date)
        statuses["akshare"] = SourceStatus("akshare", True, True)
        return False

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
    ):
        """Reconcile cninfo/eastmoney per held security, best-effort.

        Returns ``(facts, coverage)``: the merged canonical corporate-action
        facts and one evidence row per symbol/window recording every endpoint's
        outcome plus the content hash of each successful raw snapshot.  Empty
        facts are never trusted by default -- only an explicit successful
        no-event answer from *every* requested endpoint yields ``VERIFIED_EMPTY``,
        and any endpoint failure leaves the window ``UNTRUSTED``.
        """
        source = self._overrides.get("akshare") or self._build_lazy("akshare")
        if source is None:
            return current_ca, coverage_frame([])
        cninfo_frames: list[pd.DataFrame] = []
        eastmoney_frames: list[pd.DataFrame] = []
        outcomes_by_symbol: dict[str, dict[str, dict[str, object]]] = {}
        for symbol in symbols:
            symbol_outcomes: dict[str, dict[str, object]] = {}
            for endpoint, sink in (
                ("cninfo_corporate_actions", cninfo_frames),
                ("eastmoney_corporate_actions", eastmoney_frames),
            ):
                try:
                    result = self._fetch_one(
                        source,
                        DataRequest(endpoint, (symbol,), start, end, {}),
                    )
                    snapshot_sha256 = self._record_raw(result)
                    raw_snapshots.append(snapshot_sha256)
                    symbol_outcomes[endpoint] = {
                        "ok": True,
                        "empty": bool(result.frame.empty),
                        "snapshot_sha256": snapshot_sha256,
                        "checked_at": _ingest_time(result.metadata),
                    }
                    if not result.frame.empty:
                        sink.append(result.frame)
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
        accepted, quarantined = self._reconcile_action_frames(
            cninfo_frames, eastmoney_frames, issues
        )
        coverage = coverage_frame(
            [
                _coverage_record_for(
                    symbol,
                    start,
                    end,
                    outcomes_by_symbol[symbol],
                    _accepted_symbols(accepted),
                    _quarantine_reasons_by_symbol(quarantined),
                )
                for symbol in symbols
            ]
        )
        if accepted.empty:
            return current_ca, coverage
        canonical = _corporate_actions_canonical(accepted)
        return _merge_corporate_actions(current_ca, canonical), coverage

    def _reconcile_action_frames(self, cninfo_frames, eastmoney_frames, issues):
        """Reconcile collected supplier frames; empty defaults on failure."""
        try:
            reconciled = normalize_corporate_actions(
                _concat(cninfo_frames), _concat(eastmoney_frames)
            )
            return reconciled.accepted, reconciled.quarantined
        except Exception as error:  # noqa: BLE001
            issues.append(
                _issue(
                    Severity.WARNING,
                    CODE_OPTIONAL_SOURCE_FAILURE,
                    details={
                        "source": "akshare",
                        "message": f"corporate-action reconciliation: {error}",
                    },
                )
            )
            return pd.DataFrame(), pd.DataFrame()

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
            )
        else:
            statuses["baostock"] = SourceStatus("baostock", False, True)

    def _adapter_or_fail(self, name, statuses):
        try:
            return self._source(name)
        except Exception as error:  # noqa: BLE001
            statuses[name] = SourceStatus(
                source=name,
                required=True,
                ok=False,
                reason=str(error),
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
        )
        return fetch_with_retry(source, request, policy, sleeper=self._sleeper)

    def _record_raw(self, result) -> str:
        snapshot = self._raw_store.save(result)
        return snapshot.sha256

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

    def _discover_end(
        self, calendar_open, current_daily
    ) -> tuple[date | None, bool]:
        """Resolve the latest complete open day over the *carried* dataset.

        Coverage is built honestly from the per-row ``source`` tag actually
        saved in ``current_daily`` -- never by copying one source's coverage
        into another source's slot.  The primary series is the ``tushare``
        equity coverage; the *validation* map is fed by every non-primary frame
        present (``akshare`` benchmark rows plus any ``baostock`` rows).  Returns
        ``(resolved_end, is_fallback)`` where ``is_fallback`` is True when the
        resolved day was walked back below the nominal candidate.
        """
        if not calendar_open:
            return None, False
        calendar = TradingCalendar.from_open_days(calendar_open)
        benchmark_symbols = tuple(self._project_config.benchmark_symbols)
        by_symbol = {symbol: set() for symbol in benchmark_symbols}
        tushare_dates: set[date] = set()
        akshare_dates: set[date] = set()
        baostock_dates: set[date] = set()
        for record in current_daily.to_dict("records"):
            symbol = str(record["symbol"])
            day = _as_date(record["trade_date"])
            if symbol in by_symbol:
                by_symbol[symbol].add(day)
            source = str(record.get("source", ""))
            if source == "akshare":
                akshare_dates.add(day)
            elif source == "baostock":
                baostock_dates.add(day)
            elif source == "tushare":
                tushare_dates.add(day)
        if not tushare_dates or any(
            not dates for dates in by_symbol.values()
        ):
            return None, False
        validation: dict[str, date] = {}
        if akshare_dates:
            validation["akshare"] = max(akshare_dates)
        if baostock_dates:
            validation["baostock"] = max(baostock_dates)
        coverage = SourceCoverage(
            stock_primary=max(tushare_dates),
            benchmarks={s: max(dates) for s, dates in by_symbol.items()},
            validation=validation,
        )
        publication_time = self._project_config.publication_time
        resolved = resolve_latest_complete_date(
            coverage, calendar, publication_time
        )
        nominal = _nominal_candidate(calendar, publication_time)
        is_fallback = (
            resolved is not None and nominal is not None and resolved != nominal
        )
        return resolved, is_fallback

    def _result(
        self,
        issues,
        dataset_ref,
        run_id,
        resolved_end,
        statuses,
        raw_snapshots,
        *,
        resolved_end_is_fallback: bool = False,
    ) -> DataUpdateResult:
        return DataUpdateResult(
            quality_report=QualityReport(issues=tuple(issues)),
            dataset_ref=dataset_ref,
            run_id=run_id,
            resolved_end_date=resolved_end,
            source_status=tuple(
                statuses[name] for name in _CONFIGURED_SOURCES
            ),
            raw_snapshots=tuple(raw_snapshots),
            resolved_end_is_fallback=resolved_end_is_fallback,
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
    """Decide one symbol/window's status from its endpoint fetch outcomes."""
    if any(not outcome["ok"] for outcome in outcomes.values()):
        return CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_FETCH_FAILED
    if not any(not outcome["empty"] for outcome in outcomes.values()):
        return CoverageStatus.VERIFIED_EMPTY, None
    if has_accepted:
        return CoverageStatus.VERIFIED, None
    return (
        CoverageStatus.UNTRUSTED,
        _coverage_reason_for_quarantine(quarantine_reasons),
    )


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
    return pd.Timestamp.now(tz="UTC")


def _accepted_symbols(accepted: pd.DataFrame) -> frozenset[str]:
    if accepted.empty or "symbol" not in accepted.columns:
        return frozenset()
    return frozenset(str(value) for value in accepted["symbol"])


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


def _calendar_frame(open_days: tuple[date, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(open_days),
            "is_trading_day": [True] * len(open_days),
        }
    )


def _as_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


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
