"""Benchmark-confirmed trading calendar over a set of open days (Task 5).

The open-day set is the source of truth: unit tests author it offline as a
recorded fixture, and production verifies it against the two benchmarks later
(design spec §14). The calendar never guesses an open day; ``next_trading_day``
raises once the request steps past the last known open day instead of
fabricating a future date.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime
from typing import Iterable

from stock_quant.data_model.clean import parse_trade_date


class CalendarBoundaryError(ValueError):
    """A calendar query steps past the last known open day."""


def _to_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = parse_trade_date(value)
    if parsed is None:
        raise TypeError(f"cannot interpret {value!r} as a trading date")
    return parsed


class TradingCalendar:
    """An immutable, sorted, deduplicated set of confirmed open days."""

    __slots__ = ("_open_days",)

    def __init__(self, open_days: tuple[date, ...]) -> None:
        normalized = tuple(sorted({_to_date(day) for day in open_days}))
        self._open_days = normalized

    @classmethod
    def from_open_days(cls, open_days: Iterable[object]) -> "TradingCalendar":
        return cls(open_days=tuple(open_days))

    @property
    def open_days(self) -> tuple[date, ...]:
        return self._open_days

    @property
    def first_open_day(self) -> date:
        if not self._open_days:
            raise CalendarBoundaryError("calendar has no open days")
        return self._open_days[0]

    @property
    def last_open_day(self) -> date:
        if not self._open_days:
            raise CalendarBoundaryError("calendar has no open days")
        return self._open_days[-1]

    def is_trading_day(self, day: object) -> bool:
        try:
            converted = _to_date(day)
        except TypeError:
            return False
        return converted in self._open_days

    def next_trading_day(self, day: object) -> date:
        """Return the first open day strictly after ``day``.

        Raises :class:`CalendarBoundaryError` when no open day follows, so a
        caller can never invent a trading day past the confirmed calendar.
        """
        anchor = _to_date(day)
        index = bisect_right(self._open_days, anchor)
        if index >= len(self._open_days):
            raise CalendarBoundaryError(
                f"no open trading day after {anchor.isoformat()} in this calendar"
            )
        return self._open_days[index]

    def last_trading_day_each_week(
        self, start: object, end: object
    ) -> tuple[date, ...]:
        """Final open day of each ISO week with open days in ``[start, end]``.

        Only open days on or before ``end`` are considered for each week, so a
        truncated window never looks ahead to a later day in the same week.
        """
        first = _to_date(start)
        last = _to_date(end)
        if last < first:
            raise ValueError("range end must not precede range start")
        week_groups: dict[tuple[int, int], list[date]] = {}
        for day in self._open_days:
            if first <= day <= last:
                iso = day.isocalendar()[:2]
                week_groups.setdefault(iso, []).append(day)
        return tuple(days[-1] for _, days in sorted(week_groups.items()))
