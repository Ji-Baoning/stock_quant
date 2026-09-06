"""Trading-calendar behaviour over an offline open-day fixture (Task 5).

The open-day set is benchmark-confirmed open days. Unit tests never touch a
network or a supplier; this recorded fixture is the source of truth the module
reasons over. Weeks with no recorded open day are simply absent from the
fixture and produce no weekly signal date.
"""

from datetime import date

import pytest

from stock_quant.data_model.calendar import (
    CalendarBoundaryError,
    TradingCalendar,
)

# Recorded open days (author as offline fixture). Mon-Fri weeks in Aug-Sep 2020
# were real trading weeks without a Chinese public holiday, and the National-Day
# week of Mon 2020-09-28 .. Wed 2020-09-30 closed early for the 2020 holiday.
_OPEN_DAYS = (
    date(2020, 8, 17),
    date(2020, 8, 18),
    date(2020, 8, 19),
    date(2020, 8, 20),
    date(2020, 8, 21),
    date(2020, 8, 24),
    date(2020, 8, 25),
    date(2020, 8, 26),
    date(2020, 8, 27),
    date(2020, 8, 28),
    date(2020, 9, 28),
    date(2020, 9, 29),
    date(2020, 9, 30),
)


@pytest.fixture
def calendar() -> TradingCalendar:
    return TradingCalendar.from_open_days(_OPEN_DAYS)


def test_calendar_exposes_the_recorded_open_days(calendar: TradingCalendar):
    assert calendar.open_days == _OPEN_DAYS
    assert calendar.first_open_day == date(2020, 8, 17)
    assert calendar.last_open_day == date(2020, 9, 30)


def test_last_trading_day_each_week_returns_final_open_day_of_each_iso_week(
    calendar: TradingCalendar,
):
    result = calendar.last_trading_day_each_week(date(2020, 8, 17), date(2020, 9, 30))
    assert result == (
        date(2020, 8, 21),
        date(2020, 8, 28),
        date(2020, 9, 30),
    )


def test_last_trading_day_each_week_never_looks_past_the_range_end(
    calendar: TradingCalendar,
):
    # Friday 2020-08-28 is outside the window, so each open week must end at the
    # latest open day on or before ``end`` (no-lookahead property).
    result = calendar.last_trading_day_each_week(date(2020, 8, 24), date(2020, 8, 27))
    assert result == (date(2020, 8, 27),)


def test_last_trading_day_each_week_skips_weeks_without_open_days(
    calendar: TradingCalendar,
):
    empty = calendar.last_trading_day_each_week(date(2020, 8, 22), date(2020, 8, 23))
    assert empty == ()
    empty = calendar.last_trading_day_each_week(date(2020, 9, 1), date(2020, 9, 27))
    assert empty == ()


def test_next_trading_day_steps_over_weekends_and_recorded_closed_days(
    calendar: TradingCalendar,
):
    assert calendar.next_trading_day(date(2020, 8, 21)) == date(2020, 8, 24)
    assert calendar.next_trading_day(date(2020, 8, 22)) == date(2020, 8, 24)
    assert calendar.next_trading_day(date(2020, 8, 23)) == date(2020, 8, 24)
    # No open days are recorded between 2020-08-28 and 2020-09-28.
    assert calendar.next_trading_day(date(2020, 8, 28)) == date(2020, 9, 28)


def test_next_trading_day_raises_past_the_calendar_boundary(
    calendar: TradingCalendar,
):
    with pytest.raises(CalendarBoundaryError):
        calendar.next_trading_day(date(2020, 9, 30))
    with pytest.raises(CalendarBoundaryError):
        calendar.next_trading_day(date(2020, 10, 9))
    with pytest.raises(CalendarBoundaryError):
        calendar.next_trading_day(date(2020, 12, 31))


def test_is_trading_day_reports_recorded_open_days_only(calendar: TradingCalendar):
    assert calendar.is_trading_day(date(2020, 8, 24))
    assert calendar.is_trading_day(date(2020, 9, 30))
    assert not calendar.is_trading_day(date(2020, 8, 23))
    assert not calendar.is_trading_day(date(2020, 10, 1))


def test_from_open_days_normalizes_order_and_duplicates():
    scrambled = list(reversed(_OPEN_DAYS)) + [_OPEN_DAYS[0]]
    calendar = TradingCalendar.from_open_days(scrambled)
    assert calendar.open_days == _OPEN_DAYS
