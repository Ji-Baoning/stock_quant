"""Unit tests for the two hard gates a real update must clear."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

from probe_dataset_gates import (  # noqa: E402
    bar_gate,
    corporate_action_gate,
    gap_details,
    window_open_days,
)

DAYS = [date(2015, 1, 5), date(2015, 1, 6), date(2015, 1, 7), date(2015, 1, 8)]


def _calendar(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {"calendar_date": days, "is_trading_day": [True] * len(days)}
    )


def _master(rows: list[tuple[str, date]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol for symbol, _ in rows],
            "list_date": [listed for _, listed in rows],
            "delist_date": [pd.NaT] * len(rows),
        }
    )


def _daily(pairs: list[tuple[str, date]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol for symbol, _ in pairs],
            "trade_date": [pd.Timestamp(day) for _, day in pairs],
        }
    )


def test_window_open_days_keeps_only_the_window() -> None:
    calendar = _calendar(DAYS + [date(2014, 12, 31), date(2016, 1, 1)])
    assert window_open_days(
        calendar, date(2015, 1, 6), date(2015, 1, 7)
    ) == [date(2015, 1, 6), date(2015, 1, 7)]


def test_gap_details_accepts_a_not_listed_gap() -> None:
    """A bar before the listing date is an accepted classification."""
    master = _master([("000001.SZ", date(2015, 1, 7))])
    daily = _daily([("000001.SZ", day) for day in DAYS[2:]])
    assert gap_details(daily, master, _calendar(DAYS), DAYS[0], DAYS[-1]) == []


def test_gap_details_flags_a_suspended_gap() -> None:
    """A listed name missing an open day is unexplained and must be flagged."""
    master = _master([("000001.SZ", date(2015, 1, 5))])
    daily = _daily([("000001.SZ", day) for day in DAYS if day != DAYS[1]])
    gaps = gap_details(daily, master, _calendar(DAYS), DAYS[0], DAYS[-1])
    assert [(gap.symbol, gap.trade_date, gap.code) for gap in gaps] == [
        ("000001.SZ", DAYS[1], "unknown_or_suspended")
    ]


def test_bar_gate_passes_on_a_complete_grid() -> None:
    master = _master([("000001.SZ", date(2015, 1, 5))])
    daily = _daily([("000001.SZ", day) for day in DAYS])
    verdict = bar_gate(daily, master, _calendar(DAYS), DAYS[0], DAYS[-1])
    assert verdict.passed is True
    assert verdict.total == 0
    assert verdict.sample == ()


def test_bar_gate_counts_agree_with_the_acceptance_checker() -> None:
    """The recount must never disagree with the gate it previews."""
    master = _master([("000001.SZ", date(2015, 1, 5)), ("600519.SH", date(2015, 1, 5))])
    daily = _daily(
        [
            ("000001.SZ", DAYS[0]),
            ("000001.SZ", DAYS[2]),
            ("600519.SH", DAYS[0]),
        ]
    )
    verdict = bar_gate(daily, master, _calendar(DAYS), DAYS[0], DAYS[-1])
    assert verdict.passed is False
    assert verdict.total == 5
    assert len(verdict.sample) == 5


def test_corporate_action_gate_reports_untrusted_symbols() -> None:
    trusted, reasons = corporate_action_gate(
        pd.DataFrame(), ("000001.SZ",), DAYS[0], DAYS[-1]
    )
    assert trusted is False
    assert reasons == (("000001.SZ", "SOURCE_NOT_REQUESTED"),)
