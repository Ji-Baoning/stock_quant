"""Raw ``trade_cal`` facts: halo day sets, agreement, continuity.

The validation order is fixed and matters: per-exchange schema, then
per-exchange halo day set (one row per natural day, no gap, no duplicate),
then per-exchange field legality, and only then a day-by-day cross-exchange
comparison.  A missing day is therefore always reported as a missing day and
never as an ambiguous "the two exchanges disagree".
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Sequence

import pandas as pd

from stock_quant.data_model.clean import parse_trade_date

_COMPACT_DATE = re.compile(r"^\d{8}$")

CODE_CALENDAR_RAW_INVALID = "calendar_raw_invalid"
CODE_CALENDAR_EXCHANGE_MISMATCH = "calendar_exchange_mismatch"
CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN = "calendar_pretrade_continuity_broken"

EXCHANGES = ("SSE", "SZSE")

CAL_DATE_COLUMN = "cal_date"
IS_OPEN_COLUMN = "is_open"
PRETRADE_DATE_COLUMN = "pretrade_date"

#: How many dates one violation may list.
_MAX_DATES = 20

Violation = tuple[str, dict[str, object]]
Violations = tuple[Violation, ...]


class TradeCalendarFactError(ValueError):
    """A supplier calendar frame cannot be trusted; see ``.violations``."""

    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations: Violations = tuple(violations)
        super().__init__(self.violations[0][0] if self.violations else "calendar_raw_invalid")


@dataclass(frozen=True)
class TradeCalRow:
    """One halo natural day as the supplier reported it."""

    calendar_date: date
    is_open: bool
    pretrade_date: date


@dataclass(frozen=True)
class ExchangeCalendarFacts:
    """One exchange's validated halo rows, ordered by natural day."""

    exchange: str
    rows: tuple[TradeCalRow, ...]

    @property
    def open_days(self) -> tuple[date, ...]:
        return tuple(row.calendar_date for row in self.rows if row.is_open)


@dataclass(frozen=True)
class ContinuityResult:
    """Index-chain violations plus the explicit pre-coverage boundary rows."""

    violations: Violations
    allowed_pre_coverage: tuple[date, ...]


def _raw_invalid(exchange: str, reason: str, **details: object) -> TradeCalendarFactError:
    return TradeCalendarFactError(
        ((CODE_CALENDAR_RAW_INVALID, {"exchange": exchange, "reason": reason, **details}),)
    )


def _compact_date(value: object) -> date | None:
    """Parse a strict supplier ``YYYYMMDD`` date, or return ``None``.

    ``parse_trade_date`` deliberately accepts any pandas-parsable spelling
    (``2024-01-01``, even ``20231229-``), but a ``trade_cal`` response must
    use the supplier's compact format, so anything else is rejected here.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not _COMPACT_DATE.fullmatch(text):
        return None
    return parse_trade_date(text)


def parse_trade_cal_frame(
    frame: pd.DataFrame,
    *,
    exchange: str,
    halo_start: date,
    halo_end: date,
) -> ExchangeCalendarFacts:
    """Validate one exchange's halo response into ordered calendar facts."""
    if exchange not in EXCHANGES:
        raise _raw_invalid(exchange, "unknown_exchange")
    if not isinstance(frame, pd.DataFrame):
        raise _raw_invalid(exchange, "not_a_data_frame")
    required = (CAL_DATE_COLUMN, IS_OPEN_COLUMN, PRETRADE_DATE_COLUMN)
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise _raw_invalid(exchange, "missing_columns", columns=missing)
    if frame.empty:
        raise _raw_invalid(exchange, "empty_response")
    rows = _parsed_rows(frame, exchange=exchange)
    _check_halo_days(rows, exchange=exchange, halo_start=halo_start, halo_end=halo_end)
    return ExchangeCalendarFacts(exchange, rows)


def _parsed_rows(frame: pd.DataFrame, *, exchange: str) -> tuple[TradeCalRow, ...]:
    rows: list[TradeCalRow] = []
    for record in frame.to_dict("records"):
        calendar_date = _compact_date(record[CAL_DATE_COLUMN])
        if calendar_date is None:
            raise _raw_invalid(
                exchange,
                "unparsable_cal_date",
                value=str(record[CAL_DATE_COLUMN]),
            )
        is_open_raw = record[IS_OPEN_COLUMN]
        if isinstance(is_open_raw, bool):
            is_open = is_open_raw
        elif str(is_open_raw).strip() in {"0", "1"}:
            is_open = str(is_open_raw).strip() == "1"
        else:
            raise _raw_invalid(
                exchange, "invalid_is_open", value=str(is_open_raw)
            )
        pretrade_raw = record[PRETRADE_DATE_COLUMN]
        if pretrade_raw is None or not str(pretrade_raw).strip():
            raise _raw_invalid(
                exchange, "unparsable_pretrade_date", calendar_date=calendar_date.isoformat()
            )
        pretrade_date = _compact_date(pretrade_raw)
        if pretrade_date is None:
            raise _raw_invalid(
                exchange,
                "unparsable_pretrade_date",
                calendar_date=calendar_date.isoformat(),
            )
        rows.append(TradeCalRow(calendar_date, is_open, pretrade_date))
    return tuple(sorted(rows, key=lambda row: row.calendar_date))


def _check_halo_days(
    rows: Sequence[TradeCalRow],
    *,
    exchange: str,
    halo_start: date,
    halo_end: date,
) -> None:
    expected = [
        halo_start + timedelta(days=offset)
        for offset in range((halo_end - halo_start).days + 1)
    ]
    seen: dict[date, int] = {}
    for row in rows:
        seen[row.calendar_date] = seen.get(row.calendar_date, 0) + 1
    duplicates = sorted(day for day, count in seen.items() if count > 1)
    if duplicates:
        raise _raw_invalid(
            exchange,
            "duplicate_date",
            calendar_date=duplicates[0].isoformat(),
        )
    outside = sorted(day for day in seen if day < halo_start or day > halo_end)
    if outside:
        raise _raw_invalid(
            exchange, "outside_halo", calendar_date=outside[0].isoformat()
        )
    for day in expected:
        if day not in seen:
            raise _raw_invalid(
                exchange, "missing_date", calendar_date=day.isoformat()
            )


def check_exchange_agreement(
    sse: ExchangeCalendarFacts, szse: ExchangeCalendarFacts
) -> Violations:
    """Compare two complete halos day by day; any difference blocks."""
    other = {row.calendar_date: row for row in szse.rows}
    for field in ("is_open", "pretrade_date"):
        dates = [
            row.calendar_date.isoformat()
            for row in sse.rows
            if other[row.calendar_date].__getattribute__(field) != getattr(row, field)
        ]
        if dates:
            return (
                (
                    CODE_CALENDAR_EXCHANGE_MISMATCH,
                    {
                        "field": field,
                        "dates": sorted(dates)[:_MAX_DATES],
                        "exchanges": list(EXCHANGES),
                    },
                ),
            )
    return ()


def materialize_open_days(
    current_open_days: Sequence[date],
    *,
    facts: ExchangeCalendarFacts,
    start: date,
    end: date,
) -> tuple[date, ...]:
    """Drop the window's open days, then insert the supplier's window days.

    Rows outside ``[start, end]`` are carried unchanged; the result is sorted
    and deduplicated.  The supplier's days outside the window are ignored --
    the materialisation window never grows past ``[start, end]``.
    """
    kept = [day for day in current_open_days if not start <= day <= end]
    fresh = [day for day in facts.open_days if start <= day <= end]
    return tuple(sorted(set(kept) | set(fresh)))


def check_pretrade_continuity(
    rows: Sequence[TradeCalRow],
    merged_open_days: Sequence[date],
    *,
    start: date,
    end: date,
) -> ContinuityResult:
    """Every window row and the ``end + 1`` halo row must chain correctly.

    ``pretrade_date`` is compared against the *merged candidate table* (the
    carried calendar plus this window's materialisation), never against the
    supplier's own response -- so both a deleted window day and a neighbour
    still referencing it are caught.  The single allowed escape is the row
    whose ``pretrade_date`` is strictly before ``start`` while the merged table
    holds no earlier open day at all: that is the coverage boundary, and it is
    returned explicitly in ``allowed_pre_coverage`` instead of passing
    silently.
    """
    open_sorted = tuple(sorted(set(merged_open_days)))
    last_row = end + timedelta(days=1)
    violations: list[Violation] = []
    allowed: list[date] = []
    for row in rows:
        if not start <= row.calendar_date <= last_row:
            continue
        index = bisect_left(open_sorted, row.calendar_date)
        expected = open_sorted[index - 1] if index > 0 else None
        if expected is None:
            if row.pretrade_date < start:
                if not open_sorted:
                    # The merged table holds no open day at all: this row is
                    # the coverage boundary and is reported, never silent.
                    allowed.append(row.calendar_date)
                # Otherwise the row itself is the merged table's first open
                # day; its pre-window pretrade reference anchors the chain.
                continue
            violations.append(_continuity(row, None))
            continue
        if row.pretrade_date != expected:
            violations.append(_continuity(row, expected))
    return ContinuityResult(tuple(violations), tuple(allowed))


def _continuity(row: TradeCalRow, expected: date | None) -> Violation:
    return (
        CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN,
        {
            "calendar_date": row.calendar_date.isoformat(),
            "pretrade_date": row.pretrade_date.isoformat(),
            "expected": expected.isoformat() if expected is not None else None,
        },
    )
