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
