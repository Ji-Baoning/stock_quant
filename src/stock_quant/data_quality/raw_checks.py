"""Row-level raw checks over canonical frames and missing-row classification.

Cleaning already rejects rows it cannot represent, so these checks guard the
rows that survive into standardized tables: positive prices, non-negative
volume/amount, legal OHLC relationships, unique primary keys, explicit source
provenance and a documented adjustment basis. Missing rows are classified in a
fixed order (design spec §13.3): not listed, delisted, non-trading day,
all-source missing/unknown-or-suspended, primary-only missing, unexplained.

The module also owns the pure point-in-time membership validator
(:func:`validate_membership_facts`) behind the mandatory
``index_membership_evidence`` acceptance result. It reads only the immutable
``universe_membership`` raw facts plus the caller's calendar, expected member
counts, security-master boundaries and official size-exception records; it
never mutates data, consults factors or touches the network, and every
rejection is ``FATAL`` so formal Research must stop before factor work.
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.universe_membership import (
    CANONICAL_UNIVERSE_IDS,
    SecurityMasterBoundary,
)
from stock_quant.data_quality.models import (
    CODE_DUPLICATE_CONFLICT,
    CODE_INVALID_OHLC,
    CODE_NEGATIVE_AMOUNT,
    CODE_NEGATIVE_VOLUME,
    CODE_NONPOSITIVE_PRICE,
    CODE_QUARANTINE_MISSING_REASON,
    CODE_SCHEMA_MISMATCH,
    CODE_UNKNOWN_ADJUSTMENT,
    CODE_UNKNOWN_SOURCE,
    MISSING_DELISTED,
    MISSING_NON_TRADING_DAY,
    MISSING_NOT_LISTED,
    MISSING_PRIMARY_SOURCE,
    MISSING_UNEXPLAINED,
    MISSING_UNKNOWN_OR_SUSPENDED,
    TABLE_DAILY_BAR,
    QualityIssue,
    Severity,
)

KNOWN_SUPPLIERS = frozenset({"tushare", "akshare", "baostock"})
DOCUMENTED_ADJUSTMENTS = frozenset({"unadjusted"})

_PRICE_COLUMNS = ("open", "high", "low", "close")


def check_daily_values(
    frame: pd.DataFrame, *, table: str = TABLE_DAILY_BAR
) -> list[QualityIssue]:
    """Return errors for non-positive prices, negative volume/amount, bad OHLC."""
    issues: list[QualityIssue] = []
    for _, row in frame.iterrows():
        symbol = _row_symbol(row)
        trade_date = _row_date(row)
        issues.extend(_value_issues(row, symbol, trade_date, table))
    return issues


def check_primary_key_conflicts(
    frame: pd.DataFrame,
    key: Sequence[str] = ("symbol", "trade_date"),
    *,
    table: str = TABLE_DAILY_BAR,
) -> list[QualityIssue]:
    """Report primary keys that occur more than once in a canonical table."""
    duplicated = frame.duplicated(subset=list(key), keep=False)
    if not duplicated.any():
        return []
    issues: list[QualityIssue] = []
    duplicate_rows = frame.loc[duplicated]
    for _, group in duplicate_rows.groupby(list(key), sort=False):
        if len(group) < 2:
            continue
        first = group.iloc[0]
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_DUPLICATE_CONFLICT,
                table=table,
                symbol=_row_symbol(first),
                trade_date=_row_date(first),
                details={"row_count": int(len(group))},
            )
        )
    return issues


def check_schema(
    frame: pd.DataFrame,
    schema,
    *,
    table: str,
) -> list[QualityIssue]:
    """Report a canonical-frame that does not match its declared Arrow schema."""
    expected = [field.name for field in schema]
    present = list(frame.columns)
    if present == expected:
        return []
    return [
        QualityIssue(
            severity=Severity.ERROR,
            code=CODE_SCHEMA_MISMATCH,
            table=table,
            details={
                "expected": expected,
                "present": present,
                "missing": [name for name in expected if name not in present],
                "extra": [name for name in present if name not in expected],
            },
        )
    ]


def check_provenance(
    frame: pd.DataFrame,
    *,
    table: str = TABLE_DAILY_BAR,
    known_sources: frozenset[str] = KNOWN_SUPPLIERS,
    documented_adjustments: frozenset[str] = DOCUMENTED_ADJUSTMENTS,
) -> list[QualityIssue]:
    """Return errors when a row's source or adjustment basis is not documented."""
    issues: list[QualityIssue] = []
    for _, row in frame.iterrows():
        source = row.get("source")
        adjustment = row.get("adjustment")
        symbol = _row_symbol(row)
        trade_date = _row_date(row)
        if source not in known_sources:
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    code=CODE_UNKNOWN_SOURCE,
                    table=table,
                    symbol=symbol,
                    trade_date=trade_date,
                    details={"source": None if pd.isna(source) else source},
                )
            )
        if adjustment not in documented_adjustments:
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    code=CODE_UNKNOWN_ADJUSTMENT,
                    table=table,
                    symbol=symbol,
                    trade_date=trade_date,
                    details={"adjustment": None if pd.isna(adjustment) else adjustment},
                )
            )
    return issues


def check_quarantine_reasons(
    frame: pd.DataFrame, *, table: str = "quarantine"
) -> list[QualityIssue]:
    """Return errors for isolated records that carry no cleaning reason."""
    if not isinstance(frame, pd.DataFrame):
        return [
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_QUARANTINE_MISSING_REASON,
                table=table,
                details={"reason": "quarantine records are not a frame"},
            )
        ]
    if "reason" not in frame.columns:
        return [
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_QUARANTINE_MISSING_REASON,
                table=table,
                details={"reason_column_missing": True},
            )
        ]
    issues: list[QualityIssue] = []
    for index, row in frame.iterrows():
        if _blank(row.get("reason")):
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    code=CODE_QUARANTINE_MISSING_REASON,
                    table=table,
                    symbol=_row_symbol(row),
                    trade_date=_row_date(row),
                    details={"row": _json_scalar(index)},
                )
            )
    return issues


def classify_missing_row(
    *,
    trade_date: date,
    list_date: date | None = None,
    delist_date: date | None = None,
    is_trading_day: bool = True,
    primary_present: bool = False,
    validation_present: bool = False,
) -> str:
    """Classify an absent (symbol, date) bar using the design-spec precedence."""
    if list_date is not None and trade_date < list_date:
        return MISSING_NOT_LISTED
    if delist_date is not None and trade_date > delist_date:
        return MISSING_DELISTED
    if not is_trading_day:
        return MISSING_NON_TRADING_DAY
    if not primary_present and not validation_present:
        return MISSING_UNKNOWN_OR_SUSPENDED
    if not primary_present:
        return MISSING_PRIMARY_SOURCE
    return MISSING_UNEXPLAINED


# --------------------------------------------------------------------------- #
# Point-in-time index membership validation (universe_membership raw facts)
# --------------------------------------------------------------------------- #

TABLE_UNIVERSE_MEMBERSHIP = "universe_membership"

UNIVERSE_SCHEMA_MISMATCH = "UNIVERSE_SCHEMA_MISMATCH"
UNIVERSE_EVIDENCE_MISSING = "UNIVERSE_EVIDENCE_MISSING"
UNIVERSE_UNKNOWN_SYMBOL = "UNIVERSE_UNKNOWN_SYMBOL"
UNIVERSE_INTERVAL_CONFLICT = "UNIVERSE_INTERVAL_CONFLICT"
UNIVERSE_INTERVAL_OVERLAP = "UNIVERSE_INTERVAL_OVERLAP"
UNIVERSE_ANNOUNCEMENT_AFTER_USE = "UNIVERSE_ANNOUNCEMENT_AFTER_USE"
UNIVERSE_MASTER_INTERSECTION_EMPTY = "UNIVERSE_MASTER_INTERSECTION_EMPTY"
UNIVERSE_DELISTING_ENDPOINT_UNPROVEN = "UNIVERSE_DELISTING_ENDPOINT_UNPROVEN"
UNIVERSE_COVERAGE_GAP = "UNIVERSE_COVERAGE_GAP"
UNIVERSE_MEMBER_COUNT_MISMATCH = "UNIVERSE_MEMBER_COUNT_MISMATCH"
UNIVERSE_EXCEPTION_CONFLICT = "UNIVERSE_EXCEPTION_CONFLICT"

UNIVERSE_MEMBER_COLUMNS = (
    "universe_id",
    "symbol",
    "raw_effective_from",
    "raw_effective_to",
    "announcement_date",
    "status",
    "reason",
    "source",
    "source_url",
    "snapshot_sha256",
    "source_document_sha256",
)

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CANONICAL_MEMBER_SYMBOL = re.compile(r"\d{6}\.(?:SH|SZ|BJ)")

_TERMINATION_REASONS = frozenset({"delisting", "merger_or_reorganization"})
_KNOWN_REASONS = frozenset(
    {
        "initial_constituent",
        "regular_rebalance",
        "temporary_adjustment",
        "delisting",
        "merger_or_reorganization",
        "correction",
    }
)
_KNOWN_STATUSES = frozenset({"active", "removed"})


class MembershipSizeException(BaseModel):
    """One immutable, hash-backed official exception to a member count.

    The design spec allows officially sanctioned temporary cardinality
    deviations (for example an announced partial adjustment window) only when
    they carry the governing rules version and verifiable exception evidence.
    The record is frozen, strictly validated and content-hashed, so a formal
    run can pin the exact exception set it was accepted under and any later
    mutation yields a different hash.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_id: str
    interval_start: date
    interval_end: date
    allowed_size: int = Field(ge=1)
    reason: str = Field(min_length=1)
    rules_version: str = Field(min_length=1)
    evidence_sha256: str

    @field_validator("universe_id")
    @classmethod
    def _universe_id(cls, value: str) -> str:
        if value not in CANONICAL_UNIVERSE_IDS and not re.fullmatch(
            r"custom_[a-z0-9_]+", value
        ):
            raise ValueError(
                f"universe_id must be a canonical index or custom_<slug>: "
                f"{value!r}"
            )
        return value

    @field_validator("reason", "rules_version")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError(f"must be nonblank trimmed text: {value!r}")
        return value

    @field_validator("evidence_sha256")
    @classmethod
    def _evidence_hash(cls, value: str) -> str:
        if not _SHA256_HEX.fullmatch(value):
            raise ValueError(
                f"evidence_sha256 must be 64 lowercase hex characters: {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _check_interval(self) -> "MembershipSizeException":
        if self.interval_start > self.interval_end:
            raise ValueError(
                "interval_start must not follow interval_end: "
                f"{self.interval_start} > {self.interval_end}"
            )
        return self

    @property
    def content_hash(self) -> str:
        """SHA-256 over the canonical JSON rendering of this record."""
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_membership_facts(
    frame: pd.DataFrame,
    *,
    calendar: TradingCalendar | Sequence[date],
    expected_sizes: Mapping[str, int],
    master: Mapping[str, SecurityMasterBoundary] | None = None,
    exceptions: Sequence[MembershipSizeException | Mapping[str, Any]]
    | None = None,
) -> list[QualityIssue]:
    """Pure, offline validation of one ``universe_membership`` raw table.

    Returns every rejection as a ``FATAL`` :class:`QualityIssue`; an empty
    list means the facts are acceptable. Checked, in order: canonical schema,
    per-row evidence (snapshot/document hashes, source and auditable URL),
    unknown symbols (canonical master/calendar conventions, and presence in
    ``master`` when supplied), interval/status/reason consistency, interval
    overlap, announcement look-ahead (``announcement_date > start``), empty
    security-master intersections and unproven delisting endpoints (both only
    when ``master`` is supplied), then per-universe daily member counts from
    ``expected_sizes`` over the calendar.

    Cardinality is verified on every trading day from the universe's first
    claimed start through the calendar's last open day, using stable intervals
    between membership change points. A day passes either against the expected
    size or, when covered by an immutable official
    :class:`MembershipSizeException`, against that exception's allowed size.
    Overlapping exception records for one universe are themselves a conflict.
    Days with zero members raise ``UNIVERSE_COVERAGE_GAP`` instead of a count
    mismatch, so a universe that stops being populated fails loudly.

    ``calendar`` accepts a :class:`TradingCalendar` or any sequence of open
    days. No check mutates data or consults factor/market state.
    """
    issues: list[QualityIssue] = []
    if list(frame.columns) != list(UNIVERSE_MEMBER_COLUMNS):
        return [
            QualityIssue(
                severity=Severity.FATAL,
                code=UNIVERSE_SCHEMA_MISMATCH,
                table=TABLE_UNIVERSE_MEMBERSHIP,
                details={
                    "expected": list(UNIVERSE_MEMBER_COLUMNS),
                    "present": list(frame.columns),
                },
            )
        ]
    rows = [_parsed_membership_row(record) for record in frame.to_dict("records")]
    issues.extend(_evidence_issues(rows))
    issues.extend(_symbol_issues(rows, master))
    issues.extend(_interval_issues(rows))
    issues.extend(_overlap_issues(rows))
    issues.extend(_announcement_issues(rows))
    issues.extend(_master_intersection_issues(rows, master))
    issues.extend(
        _cardinality_issues(rows, _open_days(calendar), expected_sizes, exceptions)
    )
    return sorted(
        issues,
        key=lambda issue: (
            issue.severity.rank,
            issue.code,
            issue.symbol or "",
            json.dumps(issue.details, sort_keys=True, default=str),
        ),
    )


def _open_days(
    calendar: TradingCalendar | Sequence[date],
) -> tuple[date, ...]:
    if isinstance(calendar, TradingCalendar):
        return calendar.open_days
    return tuple(sorted({day for day in calendar}))


def _blank_cell(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def _cell_text(value: Any) -> str | None:
    if _blank_cell(value):
        return None
    return str(value).strip()


def _cell_date(value: Any) -> date | None:
    if _blank_cell(value):
        return None
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        return None
    return timestamp.date()


def _parsed_membership_row(record: Mapping[str, Any]) -> dict[str, Any]:
    """Parse one raw row once so every check sees the same view."""
    end_cell = record.get("raw_effective_to")
    return {
        "universe_id": _cell_text(record.get("universe_id")),
        "symbol": _cell_text(record.get("symbol")),
        "start": _cell_date(record.get("raw_effective_from")),
        "end": _cell_date(end_cell),
        "end_present": not _blank_cell(end_cell),
        "announcement": _cell_date(record.get("announcement_date")),
        "status": _cell_text(record.get("status")),
        "reason": _cell_text(record.get("reason")),
        "snapshot_sha256": _cell_text(record.get("snapshot_sha256")),
        "source_document_sha256": _cell_text(
            record.get("source_document_sha256")
        ),
        "source": _cell_text(record.get("source")),
        "source_url": _cell_text(record.get("source_url")),
    }


def _evidence_issues(rows: list[dict[str, Any]]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for row in rows:
        fields: list[str] = []
        for field in ("snapshot_sha256", "source_document_sha256"):
            value = row[field]
            if value is None or not _SHA256_HEX.fullmatch(value):
                fields.append(field)
        for field in ("source", "source_url"):
            if row[field] is None:
                fields.append(field)
        if fields:
            issues.append(
                QualityIssue(
                    severity=Severity.FATAL,
                    code=UNIVERSE_EVIDENCE_MISSING,
                    table=TABLE_UNIVERSE_MEMBERSHIP,
                    symbol=row["symbol"],
                    details={"fields": fields},
                )
            )
    return issues


def _symbol_issues(
    rows: list[dict[str, Any]],
    master: Mapping[str, SecurityMasterBoundary] | None,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for row in rows:
        symbol = row["symbol"]
        if symbol is None or not _CANONICAL_MEMBER_SYMBOL.fullmatch(symbol):
            reason = "non_canonical_symbol"
        elif master is not None and symbol not in master:
            reason = "absent_from_security_master"
        else:
            continue
        issues.append(
            QualityIssue(
                severity=Severity.FATAL,
                code=UNIVERSE_UNKNOWN_SYMBOL,
                table=TABLE_UNIVERSE_MEMBERSHIP,
                symbol=symbol,
                details={"reason": reason},
            )
        )
    return issues


def _interval_issues(rows: list[dict[str, Any]]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for row in rows:
        problems: list[str] = []
        if row["start"] is None:
            problems.append("raw_effective_from_missing")
        if row["announcement"] is None:
            problems.append("announcement_date_missing")
        if row["end_present"] and row["end"] is None:
            problems.append("raw_effective_to_unparsable")
        status = row["status"]
        if status not in _KNOWN_STATUSES:
            problems.append("unknown_status")
        elif status == "active" and row["end_present"]:
            problems.append("active_fact_cannot_end")
        elif status == "removed" and not row["end_present"]:
            problems.append("removed_fact_requires_end")
        reason = row["reason"]
        if reason not in _KNOWN_REASONS:
            problems.append("unknown_reason")
        elif reason in _TERMINATION_REASONS and status != "removed":
            problems.append("termination_reason_requires_removed_status")
        elif reason == "initial_constituent" and status != "active":
            problems.append("initial_constituent_requires_active_status")
        if (
            row["start"] is not None
            and row["end"] is not None
            and row["end"] < row["start"]
        ):
            problems.append("end_precedes_start")
        if problems:
            issues.append(
                QualityIssue(
                    severity=Severity.FATAL,
                    code=UNIVERSE_INTERVAL_CONFLICT,
                    table=TABLE_UNIVERSE_MEMBERSHIP,
                    symbol=row["symbol"],
                    details={"problems": problems},
                )
            )
    return issues


def _overlap_issues(rows: list[dict[str, Any]]) -> list[QualityIssue]:
    by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row["start"] is None:
            continue
        by_identity.setdefault(
            (row["universe_id"] or "", row["symbol"] or ""), []
        ).append(row)
    issues: list[QualityIssue] = []
    for (universe_id, symbol), group in sorted(by_identity.items()):
        ordered = sorted(group, key=lambda row: row["start"])
        for previous, following in zip(ordered, ordered[1:]):
            if previous["end"] is None or previous["end"] >= following["start"]:
                issues.append(
                    QualityIssue(
                        severity=Severity.FATAL,
                        code=UNIVERSE_INTERVAL_OVERLAP,
                        table=TABLE_UNIVERSE_MEMBERSHIP,
                        symbol=symbol,
                        details={
                            "universe_id": universe_id,
                            "first": _interval_text(previous),
                            "second": _interval_text(following),
                        },
                    )
                )
    return issues


def _interval_text(row: dict[str, Any]) -> str:
    end = row["end"].isoformat() if row["end"] is not None else "open"
    return f"[{row['start'].isoformat()}, {end}]"


def _announcement_issues(rows: list[dict[str, Any]]) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for row in rows:
        if row["start"] is None or row["announcement"] is None:
            continue
        if row["announcement"] > row["start"]:
            issues.append(
                QualityIssue(
                    severity=Severity.FATAL,
                    code=UNIVERSE_ANNOUNCEMENT_AFTER_USE,
                    table=TABLE_UNIVERSE_MEMBERSHIP,
                    symbol=row["symbol"],
                    details={
                        "universe_id": row["universe_id"],
                        "announcement_date": row["announcement"].isoformat(),
                        "raw_effective_from": row["start"].isoformat(),
                    },
                )
            )
    return issues


def _master_intersection_issues(
    rows: list[dict[str, Any]],
    master: Mapping[str, SecurityMasterBoundary] | None,
) -> list[QualityIssue]:
    """Mirror ``resolve_memberships`` per row without rewriting the facts."""
    if master is None:
        return []
    issues: list[QualityIssue] = []
    for row in rows:
        symbol = row["symbol"]
        if (
            row["start"] is None
            or symbol is None
            or not _CANONICAL_MEMBER_SYMBOL.fullmatch(symbol)
        ):
            continue
        boundary = master.get(symbol)
        if boundary is None:
            continue  # already reported as an unknown symbol
        if (
            row["reason"] in _TERMINATION_REASONS
            and row["status"] == "removed"
            and boundary.last_tradable_date is None
        ):
            issues.append(
                QualityIssue(
                    severity=Severity.FATAL,
                    code=UNIVERSE_DELISTING_ENDPOINT_UNPROVEN,
                    table=TABLE_UNIVERSE_MEMBERSHIP,
                    symbol=symbol,
                    details={
                        "universe_id": row["universe_id"],
                        "reason": "delisting removal without a proven "
                        "last_tradable_date",
                    },
                )
            )
            continue
        effective_from = row["start"]
        if boundary.list_date is not None:
            effective_from = max(effective_from, boundary.list_date)
        effective_to = row["end"]
        if boundary.last_tradable_date is not None and (
            effective_to is None or boundary.last_tradable_date < effective_to
        ):
            effective_to = boundary.last_tradable_date
        if effective_to is not None and effective_from > effective_to:
            issues.append(
                QualityIssue(
                    severity=Severity.FATAL,
                    code=UNIVERSE_MASTER_INTERSECTION_EMPTY,
                    table=TABLE_UNIVERSE_MEMBERSHIP,
                    symbol=symbol,
                    details={
                        "universe_id": row["universe_id"],
                        "raw_interval": _interval_text(row),
                        "master_list_date": boundary.list_date.isoformat()
                        if boundary.list_date is not None
                        else None,
                        "master_last_tradable_date": (
                            boundary.last_tradable_date.isoformat()
                            if boundary.last_tradable_date is not None
                            else None
                        ),
                    },
                )
            )
    return issues


def _validated_exceptions(
    exceptions: Sequence[MembershipSizeException | Mapping[str, Any]] | None,
) -> list[MembershipSizeException]:
    validated: list[MembershipSizeException] = []
    for item in exceptions or ():
        if isinstance(item, MembershipSizeException):
            validated.append(item)
        else:
            validated.append(MembershipSizeException.model_validate(dict(item)))
    return validated


def _cardinality_issues(
    rows: list[dict[str, Any]],
    open_days: tuple[date, ...],
    expected_sizes: Mapping[str, int],
    exceptions: Sequence[MembershipSizeException | Mapping[str, Any]] | None,
) -> list[QualityIssue]:
    if not open_days or not expected_sizes:
        return []
    exception_records = _validated_exceptions(exceptions)
    issues: list[QualityIssue] = []
    for universe_id in sorted(expected_sizes):
        expected = int(expected_sizes[universe_id])
        universe_rows = [
            row
            for row in rows
            if row["universe_id"] == universe_id and row["start"] is not None
        ]
        if not universe_rows:
            issues.append(
                QualityIssue(
                    severity=Severity.FATAL,
                    code=UNIVERSE_COVERAGE_GAP,
                    table=TABLE_UNIVERSE_MEMBERSHIP,
                    symbol=None,
                    details={
                        "universe_id": universe_id,
                        "expected": expected,
                        "message": "no membership facts in table",
                    },
                )
            )
            continue
        universe_exceptions = [
            item for item in exception_records if item.universe_id == universe_id
        ]
        issues.extend(_exception_conflict_issues(universe_id, universe_exceptions))
        first_start = min(row["start"] for row in universe_rows)
        grid = [day for day in open_days if day >= first_start]
        issues.extend(
            _daily_count_issues(
                universe_id, expected, universe_rows, universe_exceptions, grid
            )
        )
    return issues


def _exception_conflict_issues(
    universe_id: str,
    exceptions: list[MembershipSizeException],
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    ordered = sorted(exceptions, key=lambda item: item.interval_start)
    for previous, following in zip(ordered, ordered[1:]):
        if previous.interval_end >= following.interval_start:
            issues.append(
                QualityIssue(
                    severity=Severity.FATAL,
                    code=UNIVERSE_EXCEPTION_CONFLICT,
                    table=TABLE_UNIVERSE_MEMBERSHIP,
                    symbol=None,
                    details={
                        "universe_id": universe_id,
                        "first": _exception_text(previous),
                        "second": _exception_text(following),
                    },
                )
            )
    return issues


def _exception_text(exception: MembershipSizeException) -> str:
    return (
        f"[{exception.interval_start.isoformat()}, "
        f"{exception.interval_end.isoformat()}] allowed="
        f"{exception.allowed_size} evidence={exception.evidence_sha256}"
    )


def _daily_count_issues(
    universe_id: str,
    expected: int,
    rows: list[dict[str, Any]],
    exceptions: list[MembershipSizeException],
    grid: list[date],
) -> list[QualityIssue]:
    """Verify the member count over stable segments of the trading-day grid.

    Membership only changes at interval starts, the day after interval ends
    and exception boundaries, so the count is computed once per maximal
    stable segment and compared against the expected size (or the covering
    exception's allowed size). Contiguous mismatching segments merge into one
    issue so identical facts always render identical issues.
    """
    if not grid:
        return []
    boundaries: set[date] = {grid[0]}
    for row in rows:
        boundaries.add(row["start"])
        if row["end"] is not None:
            boundaries.add(row["end"] + timedelta(days=1))
    for exception in exceptions:
        boundaries.add(exception.interval_start)
        boundaries.add(exception.interval_end + timedelta(days=1))
    end_sentinel = grid[-1] + timedelta(days=1)
    points = sorted(
        point
        for point in boundaries
        if grid[0] <= point <= end_sentinel
    )
    if points[-1] != end_sentinel:
        points.append(end_sentinel)
    issues: list[QualityIssue] = []
    run: dict[str, Any] | None = None
    for start, stop in zip(points, points[1:]):
        first_index = bisect_left(grid, start)
        last_index = bisect_right(grid, stop - timedelta(days=1)) - 1
        if first_index > last_index:
            continue
        probe = grid[first_index]
        last_day = grid[last_index]
        count = sum(
            1
            for row in rows
            if row["start"] <= probe
            and (row["end"] is None or probe <= row["end"])
        )
        limit = expected
        for exception in exceptions:
            if (
                exception.interval_start <= probe
                and probe <= exception.interval_end
            ):
                limit = exception.allowed_size
                break
        days_in_segment = last_index - first_index + 1
        if count != limit:
            if run is not None and run["limit"] == limit and run["actual"] == count:
                run["days"] += days_in_segment
                run["to"] = last_day
            else:
                if run is not None:
                    issues.append(_count_issue(universe_id, run))
                run = {
                    "limit": limit,
                    "actual": count,
                    "days": days_in_segment,
                    "from": probe,
                    "to": last_day,
                }
        elif run is not None:
            issues.append(_count_issue(universe_id, run))
            run = None
    if run is not None:
        issues.append(_count_issue(universe_id, run))
    return issues


def _count_issue(
    universe_id: str, run: Mapping[str, Any]
) -> QualityIssue:
    gap = run["actual"] == 0
    return QualityIssue(
        severity=Severity.FATAL,
        code=UNIVERSE_COVERAGE_GAP if gap else UNIVERSE_MEMBER_COUNT_MISMATCH,
        table=TABLE_UNIVERSE_MEMBERSHIP,
        symbol=None,
        details={
            "universe_id": universe_id,
            "expected": run["limit"],
            "actual": run["actual"],
            "trading_days": run["days"],
            "from": run["from"].isoformat(),
            "to": run["to"].isoformat(),
        },
    )


def _value_issues(
    row: pd.Series,
    symbol: str | None,
    trade_date: date | None,
    table: str,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    nonpositive = [
        {"field": name, "value": _number(row.get(name))}
        for name in _PRICE_COLUMNS
        if _number(row.get(name)) <= 0
    ]
    if nonpositive:
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_NONPOSITIVE_PRICE,
                table=table,
                symbol=symbol,
                trade_date=trade_date,
                details={"fields": nonpositive},
            )
        )
    volume = _number(row.get("volume"))
    if volume < 0:
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_NEGATIVE_VOLUME,
                table=table,
                symbol=symbol,
                trade_date=trade_date,
                details={"value": volume},
            )
        )
    amount = _number(row.get("amount"))
    if amount < 0:
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_NEGATIVE_AMOUNT,
                table=table,
                symbol=symbol,
                trade_date=trade_date,
                details={"value": amount},
            )
        )
    if not _valid_ohlc(row):
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_INVALID_OHLC,
                table=table,
                symbol=symbol,
                trade_date=trade_date,
                details={
                    name: _number(row.get(name)) for name in _PRICE_COLUMNS
                },
            )
        )
    return issues


def _valid_ohlc(row: pd.Series) -> bool:
    low = _number(row.get("low"))
    high = _number(row.get("high"))
    open_ = _number(row.get("open"))
    close = _number(row.get("close"))
    return low <= open_ <= high and low <= close <= high


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number


def _row_symbol(row: pd.Series) -> str | None:
    value = row.get("symbol")
    if pd.isna(value):
        return None
    return str(value)


def _row_date(row: pd.Series) -> date | None:
    value = row.get("trade_date")
    if value is None or pd.isna(value):
        return None
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        return None
    return timestamp.date()


def _blank(value: Any) -> bool:
    if value is None or pd.isna(value):
        return True
    return str(value).strip() == ""


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (int, float, bool, str)) or value is None:
        return value
    return str(value)
