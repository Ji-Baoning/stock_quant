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


def _zoned_payload(prefix_end, middle_kind, middle_start, tail=None):
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
    )

    segments = [
        FetchSegment(
            "basic_factor", KIND_NOT_FETCHED, ANCHOR, prefix_end,
            reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
        ),
        FetchSegment("basic_factor", middle_kind, middle_start, END),
    ]
    if tail is not None:
        segments.append(tail)
    return to_build_config_payload({"basic_factor": segments})


def test_history_prefix_then_fetched_passes():
    payload = _zoned_payload(
        date(2023, 12, 29), KIND_FETCHED, date(2023, 12, 30)
    )
    assert validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END
    ) == []


def test_history_prefix_must_start_at_the_anchor():
    payload = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED,
                    date(2021, 6, 1), date(2023, 12, 29),
                    reason="history_begins_after_anchor",
                ),
                FetchSegment("basic_factor", KIND_FETCHED, date(2023, 12, 30), END),
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "not_fetched_prefix_misaligned" in codes


def test_history_reason_in_the_middle_is_rejected():
    payload = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment("basic_factor", KIND_FETCHED, ANCHOR, date(2024, 1, 5)),
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED,
                    date(2024, 1, 8), date(2024, 1, 12),
                    reason="history_begins_after_anchor",
                ),
                FetchSegment("basic_factor", KIND_FETCHED, date(2024, 1, 15), END),
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "not_fetched_mixed_with_fetch" in codes


def test_history_reason_in_the_tail_is_rejected():
    payload = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment("basic_factor", KIND_FETCHED, ANCHOR, date(2024, 1, 5)),
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED,
                    date(2024, 1, 8), END,
                    reason="history_begins_after_anchor",
                ),
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "not_fetched_mixed_with_fetch" in codes


def test_source_unavailable_tail_after_carried_passes():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
        NOT_FETCHED_SOURCE_UNAVAILABLE,
    )

    payload = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED, ANCHOR, date(2023, 12, 29),
                    reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
                ),
                FetchSegment(
                    "basic_factor", KIND_CARRIED, date(2023, 12, 30), END
                ),
            ],
            "daily_bar": [
                FetchSegment(
                    "daily_bar", KIND_CARRIED, ANCHOR, date(2026, 8, 27)
                ),
                FetchSegment(
                    "daily_bar", KIND_NOT_FETCHED, END, END,
                    reason=NOT_FETCHED_SOURCE_UNAVAILABLE,
                ),
            ],
        }
    )
    assert validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END
    ) == []


def test_source_unavailable_in_the_middle_is_rejected():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_SOURCE_UNAVAILABLE,
    )

    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_FETCHED, ANCHOR, date(2024, 1, 5)),
                FetchSegment(
                    "daily_bar", KIND_NOT_FETCHED,
                    date(2024, 1, 8), date(2024, 1, 12),
                    reason=NOT_FETCHED_SOURCE_UNAVAILABLE,
                ),
                FetchSegment("daily_bar", KIND_FETCHED, date(2024, 1, 15), END),
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "not_fetched_mixed_with_fetch" in codes


def test_operator_window_stays_whole_table():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment(
                    "daily_bar", KIND_NOT_FETCHED, ANCHOR, END,
                    reason="operator_explicit_window",
                ),
            ]
        }
    )
    assert validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END
    ) == []


def test_a_window_before_supported_start_is_unsupported():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
        table_unsupported_window_tables,
    )

    build = {
        "table_fetch_coverage": to_build_config_payload(
            {
                "basic_factor": [
                    FetchSegment(
                        "basic_factor", KIND_NOT_FETCHED, ANCHOR, date(2023, 12, 29),
                        reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
                    ),
                    FetchSegment("basic_factor", KIND_FETCHED, date(2023, 12, 30), END),
                ]
            }
        )
    }
    assert table_unsupported_window_tables(
        build, ["basic_factor"], date(2022, 1, 4), date(2022, 6, 30)
    ) == ["basic_factor"]
    assert table_unsupported_window_tables(
        build, ["basic_factor"], date(2024, 1, 4), date(2024, 6, 30)
    ) == []


def test_a_window_reaching_into_an_unavailable_tail_is_unsupported():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_SOURCE_UNAVAILABLE,
        table_unsupported_window_tables,
    )

    build = {
        "table_fetch_coverage": to_build_config_payload(
            {
                "daily_bar": [
                    FetchSegment("daily_bar", KIND_FETCHED, ANCHOR, date(2026, 8, 27)),
                    FetchSegment(
                        "daily_bar", KIND_NOT_FETCHED, END, END,
                        reason=NOT_FETCHED_SOURCE_UNAVAILABLE,
                    ),
                ]
            }
        )
    }
    assert table_unsupported_window_tables(
        build, ["daily_bar"], date(2026, 1, 1), END
    ) == ["daily_bar"]
    assert table_unsupported_window_tables(
        build, ["daily_bar"], date(2026, 1, 1), date(2026, 8, 27)
    ) == []


def test_a_table_starting_late_without_a_prefix_is_still_a_gap():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_FETCHED, date(2021, 4, 14), END)
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "fetch_coverage_gap" in codes
