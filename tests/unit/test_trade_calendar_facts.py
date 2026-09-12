"""Raw Tushare trade_cal facts: halo day sets, cross-exchange, continuity."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stock_quant.data_model.trade_calendar_facts import (
    CODE_CALENDAR_EXCHANGE_MISMATCH,
    CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN,
    CODE_CALENDAR_RAW_INVALID,
    TradeCalRow,
    TradeCalendarFactError,
    check_exchange_agreement,
    check_pretrade_continuity,
    materialize_open_days,
    parse_trade_cal_frame,
)

HALO_START = date(2024, 1, 1)
HALO_END = date(2024, 1, 7)
#: Mon 1 .. Sun 7 January 2024; 1-5 are the open days.
OPEN = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))
CLOSED = (date(2024, 1, 6), date(2024, 1, 7))


def _previous_open(day: date) -> str:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.strftime("%Y%m%d")


def _frame(**overrides):
    """One row per halo natural day, Friday-to-Monday aware."""
    rows = []
    for offset in range((HALO_END - HALO_START).days + 1):
        day = HALO_START + timedelta(days=offset)
        row = {
            "exchange": "SSE",
            "cal_date": day.strftime("%Y%m%d"),
            "is_open": 1 if day.weekday() < 5 else 0,
            "pretrade_date": _previous_open(day),
        }
        row.update(overrides.get(day.isoformat(), {}))
        rows.append(row)
    return pd.DataFrame(rows)


def _codes(violations) -> list[str]:
    return [code for code, _ in violations]


def _facts(frame, exchange="SSE"):
    return parse_trade_cal_frame(
        frame, exchange=exchange, halo_start=HALO_START, halo_end=HALO_END
    )


def test_parse_accepts_a_complete_halo_and_exposes_open_days():
    facts = _facts(_frame())
    assert facts.exchange == "SSE"
    assert len(facts.rows) == 7
    assert facts.open_days == OPEN
    assert facts.rows[-1].pretrade_date == date(2024, 1, 5)


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"empty": True}, "empty_response"),
        ({"drop": ["is_open"]}, "missing_columns"),
        ({"bad_cal_date": True}, "unparsable_cal_date"),
        ({"bad_pretrade_date": True}, "unparsable_pretrade_date"),
        ({"blank_pretrade_date": True}, "unparsable_pretrade_date"),
        ({"is_open": "2"}, "invalid_is_open"),
    ],
)
def test_parse_rejects_malformed_supplier_values(kwargs, reason):
    if kwargs.pop("empty", False):
        frame = pd.DataFrame(
            columns=["cal_date", "is_open", "pretrade_date"]
        )
    else:
        frame = _frame()
        if "drop" in kwargs:
            frame = frame.drop(columns=kwargs.pop("drop"))
        if kwargs.pop("bad_cal_date", False):
            frame.loc[0, "cal_date"] = "2024-01-01"
        if kwargs.pop("bad_pretrade_date", False):
            frame.loc[1, "pretrade_date"] = "20231229-"
        if kwargs.pop("blank_pretrade_date", False):
            frame.loc[1, "pretrade_date"] = ""
        if "is_open" in kwargs:
            frame["is_open"] = kwargs.pop("is_open")
    with pytest.raises(TradeCalendarFactError) as error:
        _facts(frame)
    assert _codes(error.value.violations) == [CODE_CALENDAR_RAW_INVALID]
    assert error.value.violations[0][1]["reason"] == reason
    assert error.value.violations[0][1]["exchange"] == "SSE"


def test_parse_reports_a_missing_halo_day_before_any_cross_exchange_check():
    """A short day set is reported as missing, never as an exchange mismatch."""
    frame = _frame().drop(index=2)
    with pytest.raises(TradeCalendarFactError) as error:
        _facts(frame)
    assert error.value.violations[0][1] == {
        "exchange": "SSE",
        "reason": "missing_date",
        "calendar_date": "2024-01-03",
    }


def test_parse_reports_a_duplicated_halo_day():
    frame = pd.concat([_frame(), _frame().iloc[[2]]], ignore_index=True)
    with pytest.raises(TradeCalendarFactError) as error:
        _facts(frame)
    assert error.value.violations[0][1]["reason"] == "duplicate_date"


def test_exchange_agreement_reports_the_disagreeing_days():
    sse = _facts(_frame())
    szse_frame = _frame()
    szse_frame.loc[4, "is_open"] = 0
    szse_frame.loc[5, "pretrade_date"] = "20240118"
    szse = _facts(szse_frame, exchange="SZSE")
    violations = check_exchange_agreement(sse, szse)
    assert _codes(violations) == [CODE_CALENDAR_EXCHANGE_MISMATCH]
    assert violations[0][1] == {
        "field": "is_open",
        "dates": ["2024-01-05"],
        "exchanges": ["SSE", "SZSE"],
    }


def test_materialize_replaces_only_the_window_open_days():
    facts = _facts(_frame())
    current = (
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    )
    today = date(2024, 1, 3)
    back = date(2024, 1, 5)
    merged = materialize_open_days(
        current, facts=facts, start=today, end=back
    )
    assert merged == (
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    )


def test_materialize_drops_a_stale_window_day_absent_from_the_supplier():
    facts = _facts(_frame())
    window = (date(2024, 1, 1), date(2024, 1, 2))
    merged = materialize_open_days(
        (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)),
        facts=facts,
        start=window[0],
        end=window[1],
    )
    assert merged == window + (date(2024, 1, 3),)


def test_continuity_accepts_a_chain_that_matches_the_merged_table():
    facts = _facts(_frame())
    merged = facts.open_days
    result = check_pretrade_continuity(
        facts.rows, merged, start=HALO_START, end=date(2024, 1, 5)
    )
    assert result.violations == ()
    assert result.allowed_pre_coverage == ()


def test_continuity_reports_a_window_row_that_skipped_an_open_day():
    facts = _facts(_frame())
    merged = tuple(day for day in facts.open_days if day != date(2024, 1, 4))
    result = check_pretrade_continuity(
        facts.rows, merged, start=HALO_START, end=date(2024, 1, 5)
    )
    assert _codes(result.violations) == [CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN]
    assert result.violations[0][1] == {
        "calendar_date": "2024-01-05",
        "pretrade_date": "2024-01-04",
        "expected": "2024-01-03",
    }


def test_continuity_checks_the_end_plus_one_halo_row_against_the_merged_table():
    """The ``end + 1`` row is compared to the merged table, not the response.

    The window is the single Monday 2024-01-08 and the merged table has lost
    Friday 2024-01-05; Monday still claims Friday as its pretrade day, which is
    exactly the "a deleted window day is still referenced by a neighbouring
    row" failure the rule exists to catch.
    """
    facts = _facts(_frame())
    monday = TradeCalRow(date(2024, 1, 8), False, date(2024, 1, 5))
    result = check_pretrade_continuity(
        facts.rows + (monday,),
        (date(2024, 1, 4), date(2024, 1, 8)),
        start=date(2024, 1, 8),
        end=date(2024, 1, 8),
    )
    assert _codes(result.violations) == [CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN]
    assert result.violations[0][1] == {
        "calendar_date": "2024-01-08",
        "pretrade_date": "2024-01-05",
        "expected": "2024-01-04",
    }


def test_continuity_accepts_a_window_that_starts_inside_existing_coverage():
    """A first row whose pretrade day is an earlier merged row is a real chain."""
    facts = _facts(_frame())
    result = check_pretrade_continuity(
        facts.rows, facts.open_days, start=date(2024, 1, 2), end=date(2024, 1, 5)
    )
    assert result.violations == ()
    assert result.allowed_pre_coverage == ()


def test_continuity_returns_the_pre_coverage_boundary_explicitly():
    """Only a pretrade day strictly before the window *and* no earlier merged
    row at all is a boundary -- and it is reported, never passed over."""
    facts = _facts(_frame())
    result = check_pretrade_continuity(
        (facts.rows[1],), (), start=date(2024, 1, 2), end=date(2024, 1, 5)
    )
    assert result.violations == ()
    assert result.allowed_pre_coverage == (date(2024, 1, 2),)


def test_continuity_reports_a_row_whose_pretrade_is_not_before_the_window():
    """No earlier merged row plus a pretrade day inside the window is a break."""
    row = TradeCalRow(date(2024, 1, 2), True, date(2024, 1, 2))
    result = check_pretrade_continuity(
        (row,), (), start=date(2024, 1, 2), end=date(2024, 1, 5)
    )
    assert _codes(result.violations) == [CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN]
    assert result.violations[0][1] == {
        "calendar_date": "2024-01-02",
        "pretrade_date": "2024-01-02",
        "expected": None,
    }
