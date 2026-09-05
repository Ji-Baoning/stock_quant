"""Corporate-action trust mode and evidence gate for research runs (Task 2).

Formal research must not turn unadjusted prices into claimed total returns
while corporate-action evidence is missing: a symbol/window is only trusted
when the pinned ``corporate_action_coverage`` table records an explicit
``VERIFIED`` or ``VERIFIED_EMPTY`` verdict over the whole execution window.
This module owns that vocabulary:

- :class:`DataTrustMode` -- ``research`` (the formal default, gates before any
  backtest) and ``engineering`` (runs diagnostics but is never accepted as a
  trusted performance claim).
- :func:`evaluate_corporate_action_trust` -- the deterministic decision over
  one coverage frame, one universe and one execution window.
- :class:`CorporateActionTrustDecision` / :class:`CorporateActionTrustReason`
  -- the structured, serialisable verdict and per-holding reasons that the
  frozen spec, run manifest and metrics persist.

Empty evidence is never silently trusted: a dataset with no coverage rows, or
a holding with no row, reads ``SOURCE_NOT_REQUESTED``; a holding whose trusted
rows leave a gap reads ``COVERAGE_INCOMPLETE``; an overlapping ``UNTRUSTED``
row carries its own stable reason.  Reasons are sorted by symbol then code so
identical inputs always render identical bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Iterable, Mapping

import pandas as pd

from stock_quant.data_model.corporate_action_coverage import (
    CoverageReason,
    CoverageStatus,
)

#: Statuses that count as trusted evidence for a symbol/window.
_TRUSTED_STATUSES = frozenset(
    {CoverageStatus.VERIFIED.value, CoverageStatus.VERIFIED_EMPTY.value}
)

_SYMBOL_COLUMN = "symbol"
_START_COLUMN = "window_start"
_END_COLUMN = "window_end"
_STATUS_COLUMN = "status"
_REASON_COLUMN = "reason"


class DataTrustMode(str, Enum):
    """The evidence bar a research run applies to its pinned inputs."""

    RESEARCH = "research"
    ENGINEERING = "engineering"


@dataclass(frozen=True)
class CorporateActionTrustReason:
    """One auditable reason a possible holding is not trusted."""

    code: str
    symbol: str

    def to_dict(self) -> dict[str, str]:
        """A JSON-ready ``{"code", "symbol"}`` row for manifests/metrics."""
        return {"code": self.code, "symbol": self.symbol}


@dataclass(frozen=True)
class CorporateActionTrustDecision:
    """The gate verdict: whether every holding is trusted and, if not, why."""

    trusted: bool
    reasons: tuple[CorporateActionTrustReason, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """A JSON-ready ``{"trusted", "reasons"}`` mapping."""
        return {
            "trusted": self.trusted,
            "reasons": [reason.to_dict() for reason in self.reasons],
        }


def evaluate_corporate_action_trust(
    coverage: pd.DataFrame | None,
    symbols: Iterable[str],
    window_start: date,
    window_end: date,
) -> CorporateActionTrustDecision:
    """Decide whether every ``symbol`` has trusted coverage over the window.

    A holding is trusted only when one or more ``VERIFIED``/``VERIFIED_EMPTY``
    coverage rows tile ``[window_start, window_end]`` and no overlapping row is
    ``UNTRUSTED``.  ``coverage`` may be ``None`` or an empty frame (an older
    dataset that predates the coverage-evidence table), in which case every
    holding reads ``SOURCE_NOT_REQUESTED``.
    """
    start = _as_date(window_start)
    end = _as_date(window_end)
    rows_by_symbol = _rows_by_symbol(coverage)
    reasons: list[CorporateActionTrustReason] = []
    for symbol in sorted({str(item) for item in symbols}):
        rows = rows_by_symbol.get(symbol) if rows_by_symbol else None
        if not rows:
            reasons.append(_reason(CoverageReason.SOURCE_NOT_REQUESTED, symbol))
            continue
        trusted_windows: list[tuple[date, date]] = []
        for row in rows:
            row_start = _as_date(row.get(_START_COLUMN))
            row_end = _as_date(row.get(_END_COLUMN))
            if row_start is None or row_end is None:
                continue
            if row_end < start or row_start > end:
                continue
            if _status_text(row) in _TRUSTED_STATUSES:
                trusted_windows.append(
                    (_max_date(row_start, start), _min_date(row_end, end))
                )
            else:
                reasons.append(_reason(_row_code(row), symbol))
        if not _has_reason_for(symbol, reasons) and not _covers(
            trusted_windows, start, end
        ):
            reasons.append(_reason(CoverageReason.COVERAGE_INCOMPLETE, symbol))
    reasons.sort(key=lambda item: (item.symbol, item.code))
    return CorporateActionTrustDecision(
        trusted=not reasons, reasons=tuple(reasons)
    )


def _rows_by_symbol(
    coverage: pd.DataFrame | None,
) -> dict[str, list[dict]]:
    """Index coverage rows by symbol; a missing/empty frame reads no evidence."""
    if coverage is None or not isinstance(coverage, pd.DataFrame):
        return {}
    if coverage.empty or _SYMBOL_COLUMN not in coverage.columns:
        return {}
    grouped: dict[str, list[dict]] = {}
    for record in coverage.to_dict("records"):
        grouped.setdefault(str(record.get(_SYMBOL_COLUMN)), []).append(record)
    return grouped


def _reason(
    code: CoverageReason | str, symbol: str
) -> CorporateActionTrustReason:
    return CorporateActionTrustReason(code=_code_text(code), symbol=symbol)


def _status_text(row: Mapping[str, object]) -> str:
    value = row.get(_STATUS_COLUMN)
    return "" if value is None else str(value)


def _row_code(row: Mapping[str, object]) -> str:
    """The stable code of one overlapping UNTRUSTED coverage row.

    A pipeline coverage row always carries its reason; an arbitrary row with an
    UNTRUSTED status and no usable reason is a gap in the evidence and reads
    ``COVERAGE_INCOMPLETE``.
    """
    value = row.get(_REASON_COLUMN)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if value is not None and not _is_na(value):
        text = str(value).strip()
        if text and text.lower() != "none":
            return text
    return CoverageReason.COVERAGE_INCOMPLETE.value


def _code_text(value: CoverageReason | str) -> str:
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _is_na(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _has_reason_for(
    symbol: str, reasons: list[CorporateActionTrustReason]
) -> bool:
    return any(reason.symbol == symbol for reason in reasons)


def _covers(
    intervals: list[tuple[date, date]], start: date, end: date
) -> bool:
    """True when the trusted intervals leave no uncovered day in the window.

    Windows are clipped to the requested range and may be adjacent (the
    previous window's ``window_end`` is the day before the next
    ``window_start``), so rows that tile a window without overlapping are
    trusted.
    """
    if not intervals:
        return False
    covered_until = start - timedelta(days=1)
    for lo, hi in sorted(intervals):
        if lo > covered_until + timedelta(days=1):
            return False
        if hi > covered_until:
            covered_until = hi
    return covered_until >= end


def _as_date(value: object) -> date | None:
    """Normalize a coverage window cell (date/Timestamp/NaT) to a plain date."""
    if value is None or _is_na(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def _min_date(left: date, right: date) -> date:
    return left if left < right else right


def _max_date(left: date, right: date) -> date:
    return left if left > right else right
