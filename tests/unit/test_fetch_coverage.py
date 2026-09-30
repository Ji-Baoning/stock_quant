"""Per-table fetch coverage model and manifest validation (spec D5.3)."""

from __future__ import annotations

from datetime import date

import pytest

from stock_quant.data_model.fetch_coverage import (
    KIND_CARRIED,
    KIND_FETCHED,
    KIND_NOT_FETCHED,
    NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
    FetchSegment,
    to_build_config_payload,
    validate_table_fetch_coverage,
)

ANCHOR = date(2021, 1, 4)
END = date(2026, 8, 28)


def _payload():
    return to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_CARRIED, date(2021, 1, 4), date(2026, 8, 27)),
                FetchSegment("daily_bar", KIND_FETCHED, date(2026, 8, 28), date(2026, 8, 28)),
            ]
        }
    )


def test_payload_roundtrip():
    payload = _payload()
    assert payload["daily_bar"][0]["kind"] == KIND_CARRIED
    assert payload["daily_bar"][1]["window_start"] == "2026-08-28"


def test_contiguous_coverage_passes():
    assert validate_table_fetch_coverage(_payload(), anchor_start=ANCHOR,
                                         published_end=END) == []


def test_missing_table_fails():
    violations = validate_table_fetch_coverage({}, anchor_start=ANCHOR,
                                               published_end=END)
    assert [code for code, _ in violations] == ["table_fetch_coverage_missing"]


def test_gap_between_carried_and_fetched_fails():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_CARRIED, date(2021, 1, 4), date(2026, 8, 25)),
                FetchSegment("daily_bar", KIND_FETCHED, date(2026, 8, 28), date(2026, 8, 28)),
            ]
        }
    )
    codes = [code for code, _ in validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END)]
    assert "fetch_coverage_gap" in codes


def test_not_fetched_requires_operator_reason():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_NOT_FETCHED, date(2021, 1, 4), date(2026, 8, 28)),
            ]
        }
    )
    codes = [code for code, _ in validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END)]
    assert "not_fetched_reason_missing" in codes


def test_not_fetched_with_operator_reason_passes():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_NOT_FETCHED, date(2021, 1, 4),
                             date(2026, 8, 28),
                             reason=NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW),
            ]
        }
    )
    assert validate_table_fetch_coverage(payload, anchor_start=ANCHOR,
                                         published_end=END) == []


def test_unknown_kind_fails():
    with pytest.raises(ValueError):
        FetchSegment("daily_bar", "fetched_sometimes", date(2021, 1, 4), date(2026, 8, 28))


def test_history_begins_after_anchor_is_a_valid_reason():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
    )

    segment = FetchSegment(
        "basic_factor",
        KIND_NOT_FETCHED,
        date(2021, 1, 4),
        date(2023, 12, 29),
        reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
    )
    assert segment.reason == "history_begins_after_anchor"


def test_source_unavailable_is_a_valid_reason():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_SOURCE_UNAVAILABLE,
    )

    segment = FetchSegment(
        "daily_bar",
        KIND_NOT_FETCHED,
        date(2026, 8, 28),
        date(2026, 8, 28),
        reason=NOT_FETCHED_SOURCE_UNAVAILABLE,
    )
    assert segment.reason == "source_unavailable"


def test_source_disabled_is_a_valid_reason():
    from stock_quant.data_model.fetch_coverage import NOT_FETCHED_SOURCE_DISABLED

    segment = FetchSegment(
        "daily_bar",
        KIND_NOT_FETCHED,
        date(2026, 8, 28),
        date(2026, 8, 28),
        reason=NOT_FETCHED_SOURCE_DISABLED,
    )
    assert segment.reason == "source_disabled"


def test_an_unknown_reason_is_still_rejected():
    with pytest.raises(ValueError, match="unknown not_fetched reason"):
        FetchSegment(
            "daily_bar",
            KIND_NOT_FETCHED,
            date(2026, 8, 28),
            date(2026, 8, 28),
            reason="supplier_mood",
        )
