"""A3/F1/F2: per-table fetch windows from contracts + explicit CLI window."""

from __future__ import annotations

from datetime import date, timedelta

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.data_model.fetch_coverage import (
    KIND_NOT_FETCHED,
    NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
)
from stock_quant.data_model.fetch_windows import FetchWindowPlan, plan_table_fetch_windows

ANCHOR = date(2015, 1, 5)
LATEST = date(2026, 8, 28)


def _contracts():
    return parse_data_contracts(
        [
            {
                "table": "daily_bar",
                "tier": "core",
                "primary_transport": "tushare:relay",
                "anchors": [],
                "conflict": "downgrade",
                "pit": None,
                "coverage_shape": "none",
                "incremental": "last_covered_plus_1",
            },
            {
                "table": "income",
                "tier": "anchored",
                "primary_transport": "tushare:relay",
                "anchors": ["akshare_cninfo_announcement"],
                "conflict": "downgrade",
                "pit": None,
                "coverage_shape": "per_symbol_window",
                "incremental": "disclosure_calendar",
            },
        ]
    )


def test_last_covered_plus_1_starts_after_baseline():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=None,
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    assert plans["daily_bar"].window_start == date(2026, 8, 28)
    assert plans["daily_bar"].window_end == LATEST
    assert plans["daily_bar"].kind != KIND_NOT_FETCHED


def test_disclosure_calendar_looks_back():
    plans = plan_table_fetch_windows(
        _contracts(),
        {},
        request_start=None,
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
        lookback_days=90,
    )
    assert plans["income"].window_start == LATEST - timedelta(days=90)


def test_explicit_start_deviating_skips_with_operator_reason():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=date(2021, 1, 1),  # earlier than the contract window
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    plan = plans["daily_bar"]
    assert plan.kind == KIND_NOT_FETCHED
    assert plan.reason == NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW


def test_explicit_start_narrowing_skips_too():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        # strictly later than the planned start 2026-08-28: narrowing
        request_start=date(2026, 8, 29),
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    # F1: an explicit window neither overrides nor narrows a contract window;
    # any deviation from the planned start skips the table.
    assert plans["daily_bar"].kind == KIND_NOT_FETCHED


def test_explicit_end_earlier_than_latest_skips():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=None,
        request_end=date(2026, 8, 27),
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    assert plans["daily_bar"].kind == KIND_NOT_FETCHED
    assert plans["daily_bar"].reason == NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW


def test_matching_explicit_window_fetches():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=date(2026, 8, 28),
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    assert plans["daily_bar"].window_start == date(2026, 8, 28)


def test_plan_carries_table_and_reason_shape():
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=date(2021, 1, 1),
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
    )
    plan = plans["daily_bar"]
    assert isinstance(plan, FetchWindowPlan)
    assert plan.table == "daily_bar"
    assert plan.window_start is None
    assert plan.window_end is None


def test_disclosure_lookback_override_widens_only_disclosure_tables():
    """The deep-reconcile knob touches the disclosure strategy alone.

    A verdict-changing rebuild needs the corporate-action tables re-judged
    over the whole listed window; widening every strategy instead would
    re-fetch the daily history for no evidential gain.
    """
    plans = plan_table_fetch_windows(
        _contracts(),
        {"daily_bar": (date(2026, 8, 27), date(2026, 8, 27))},
        request_start=None,
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
        disclosure_lookback_days=5000,
    )
    assert plans["income"].window_start == ANCHOR
    # The other strategies keep their own windows: last_covered_plus_1 still
    # continues from the baseline, not from the widened disclosure lookback.
    assert plans["daily_bar"].window_start == date(2026, 8, 28)


def test_disclosure_lookback_override_reaches_the_baseline_span():
    """The re-ask floors at the baseline's own covered start, not the anchor.

    The default update anchor (``project.yml``'s start_date) can sit years
    after the published evidence begins; capping the deep reconcile at it
    would leave every carried verdict older than the anchor standing -- the
    carried window-1 conflicts and refusals the deep reconcile exists to
    re-judge.  The floor is the span the baseline itself covers: re-judge
    everything you publish, nothing you do not.
    """
    plans = plan_table_fetch_windows(
        _contracts(),
        {
            "daily_bar": (date(2026, 8, 27), date(2026, 8, 27)),
            # The baseline's disclosure evidence begins before the anchor.
            "income": (date(2015, 1, 5), LATEST),
        },
        request_start=None,
        request_end=None,
        anchor_start=date(2021, 1, 1),
        latest_open_day=LATEST,
        disclosure_lookback_days=5000,
    )
    assert plans["income"].window_start == date(2015, 1, 5)


def test_disclosure_lookback_override_never_passes_the_baseline_span():
    """A lookback wider than the published evidence stops at that evidence.

    Reaching before the baseline span would fetch and judge history the
    project never published evidence for; the floor is the covered start.
    """
    plans = plan_table_fetch_windows(
        _contracts(),
        {"income": (date(2018, 6, 1), LATEST)},
        request_start=None,
        request_end=None,
        anchor_start=date(2021, 1, 1),
        latest_open_day=LATEST,
        disclosure_lookback_days=20000,
    )
    assert plans["income"].window_start == date(2018, 6, 1)


def test_disclosure_lookback_override_without_baseline_stays_at_the_anchor():
    """A baseline without recorded spans keeps the anchor as the floor."""
    plans = plan_table_fetch_windows(
        _contracts(),
        {},
        request_start=None,
        request_end=None,
        anchor_start=date(2021, 1, 1),
        latest_open_day=LATEST,
        disclosure_lookback_days=5000,
    )
    assert plans["income"].window_start == date(2021, 1, 1)


def test_default_disclosure_lookback_ignores_the_baseline_floor():
    """Without the override, ordinary updates keep the exact old behaviour."""
    plans = plan_table_fetch_windows(
        _contracts(),
        {"income": (date(2015, 1, 5), LATEST)},
        request_start=None,
        request_end=None,
        anchor_start=date(2021, 1, 1),
        latest_open_day=LATEST,
        lookback_days=90,
    )
    assert plans["income"].window_start == LATEST - timedelta(days=90)


def test_disclosure_lookback_override_with_no_explicit_window_fetches():
    """The override composes with the F1 rule: no --start, no deviation.

    The operator runs the deep reconcile with the knob alone; pairing it with
    an explicit --start would skip the very tables the knob exists to widen.
    """
    plans = plan_table_fetch_windows(
        _contracts(),
        {},
        request_start=None,
        request_end=None,
        anchor_start=ANCHOR,
        latest_open_day=LATEST,
        disclosure_lookback_days=5000,
    )
    assert plans["income"].kind != KIND_NOT_FETCHED
    assert plans["income"].window_start == ANCHOR
    assert plans["income"].window_end == LATEST
