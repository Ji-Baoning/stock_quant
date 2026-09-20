"""Quality severity, issues, reports and the shared issue-code vocabulary.

Severity follows design-spec §13: ``INFO`` records explicable differences,
``WARNING`` highlights suspicious-but-unconfirmed problems, ``ERROR`` marks
locally unusable data and ``FATAL`` marks a whole batch as untrusted. The
publication gate blocks on issue *codes* in the gate's domain rather than on
severity alone, so strategy-neutral publishability stays separate from the
backtest-readiness concerns that later tasks evaluate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

# ---- Severity --------------------------------------------------------------

_SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "ERROR": 2, "FATAL": 3}


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self.value]


# ---- Issue codes (shared vocabulary for the quality layer) -----------------

CODE_NONPOSITIVE_PRICE = "nonpositive_price"
CODE_NEGATIVE_VOLUME = "negative_volume"
CODE_NEGATIVE_AMOUNT = "negative_amount"
CODE_INVALID_OHLC = "invalid_ohlc"
CODE_DUPLICATE_CONFLICT = "duplicate_conflict"
CODE_SCHEMA_MISMATCH = "schema_mismatch"
CODE_UNKNOWN_SOURCE = "unknown_source"
CODE_UNKNOWN_ADJUSTMENT = "unknown_adjustment"
CODE_QUARANTINE_MISSING_REASON = "quarantine_missing_reason"
CODE_REPORT_GENERATION_FAILED = "report_generation_failed"
CODE_ADJUSTMENT_BASIS_MISMATCH = "adjustment_basis_mismatch"
CODE_UNIT_MISMATCH = "unit_mismatch"
CODE_WITHIN_TOLERANCE = "within_tolerance"
CODE_PRICE_DIFFERENCE = "price_difference"
CODE_CLOSE_DIFFERENCE = "close_difference"

# Adjusted-bar lineage codes: a published ``adjusted_bar`` that cannot be
# reconciled row-by-row against ``daily_bar`` and the canonical corporate
# actions is untrusted end to end (all four block publication).
CODE_ADJUSTED_BAR_MISSING_RAW = "adjusted_bar_missing_raw"
CODE_ADJUSTED_BAR_RAW_CLOSE_MISMATCH = "adjusted_bar_raw_close_mismatch"
CODE_ADJUSTED_BAR_WRONG_BASIS = "adjusted_bar_wrong_basis"
CODE_ADJUSTED_BAR_UNKNOWN_ACTION = "adjusted_bar_unknown_action"

# Suspension bars materialized from the primary source's own pre_close chain:
# a proven run is an audited INFO; a run with no anchor row is an honest
# WARNING; a chain break no accepted action explains is real data loss and
# blocks publication (all gap rows must be proven, never assumed).
CODE_SUSPENSION_ROW = "suspension_row_materialized"
CODE_SUSPENSION_RUN_UNVERIFIED = "suspension_run_unverified"
CODE_UNEXPLAINED_PRIMARY_GAP = "unexplained_primary_gap"

# A quarantined corporate action whose every known date lies outside a window
# cannot affect that window's series, so it no longer marks the symbol/window
# UNTRUSTED.  The exclusion is INFO-only audit trail -- the published
# quarantine table still carries every row -- and its ``branch`` detail names
# the rule that excluded it (ADR-006).
CODE_QUARANTINE_OUT_OF_WINDOW = "quarantine_out_of_window"

# A published table without a data_contracts declaration (spec D2): the
# publish path rejects it before staging, so any report carrying this code
# names a dataset that must never reach the immutable store.
CODE_UNREGISTERED_TABLE = "unregistered_table"

# Downgrade evidence (spec D1): a non-core table carrying a table-level
# blocking code publishes with this WARNING record instead of blocking.
# It is deliberately NOT in PUBLICATION_BLOCKING_CODES.
CODE_COVERAGE_DOWNGRADED = "coverage_downgraded"

# ADR-009 evidence: a newly quarantined row with an absent ex-date carries
# its two-axis classification in the quality report.  Informational only --
# no rule reads it to move a verdict; that is a separate decision.
CODE_ABSENT_EX_DATE_CLASSIFIED = "absent_ex_date_classified"

# Missing-row classification labels, in classification precedence order.
MISSING_NOT_LISTED = "not_listed"
MISSING_DELISTED = "delisted"
MISSING_NON_TRADING_DAY = "non_trading_day"
MISSING_UNKNOWN_OR_SUSPENDED = "unknown_or_suspended"
MISSING_PRIMARY_SOURCE = "primary_source_missing"
MISSING_UNEXPLAINED = "unexplained"

# Canonical standardized table names (design spec §11).
TABLE_DAILY_BAR = "daily_bar"
TABLE_SECURITY_MASTER = "security_master"
TABLE_CORPORATE_ACTION = "corporate_action"
TABLE_TRADING_CALENDAR = "trading_calendar"


@dataclass(frozen=True)
class QualityIssue:
    """One quality anomaly or status observation about a table region."""

    severity: Severity
    code: str
    table: str
    symbol: str | None = None
    trade_date: date | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityReport:
    """An immutable, orderable collection of quality issues for one build."""

    issues: tuple[QualityIssue, ...] = ()

    def by_severity(self) -> dict[str, int]:
        counts = {severity.value: 0 for severity in Severity}
        for item in self.issues:
            counts[item.severity.value] += 1
        return counts

    def by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.issues:
            counts[item.code] = counts.get(item.code, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(
            self.issues,
            key=lambda item: (
                item.severity.rank,
                item.code,
                item.table,
                item.symbol or "",
                item.trade_date.isoformat() if item.trade_date is not None else "",
            ),
        )
        return {
            "issues": [_issue_dict(item) for item in ordered],
            "by_severity": self.by_severity(),
            "by_code": self.by_code(),
        }


def _issue_dict(item: QualityIssue) -> dict[str, Any]:
    return {
        "severity": item.severity.value,
        "code": item.code,
        "table": item.table,
        "symbol": item.symbol,
        "trade_date": (
            item.trade_date.isoformat() if item.trade_date is not None else None
        ),
        "details": _json_safe(item.details),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def issue_dict_dumps(report: QualityReport) -> str:
    """Serialise a report deterministically for ``quality_report.json``."""
    body = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)
    return body + "\n"
