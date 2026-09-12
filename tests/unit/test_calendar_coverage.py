"""Calendar coverage spans: split, replace, merge, and evidence validation."""

from __future__ import annotations

from datetime import date

import pytest

from stock_quant.data_model.calendar_coverage import (
    CODE_BOOTSTRAP_SEED_IN_FULL_HISTORY,
    CODE_CALENDAR_COVERAGE_GAP,
    CODE_CALENDAR_COVERAGE_INVALID,
    CODE_CALENDAR_COVERAGE_MISSING,
    CODE_CALENDAR_COVERAGE_OVERLAP,
    CODE_CALENDAR_COVERAGE_UNSORTED,
    CODE_CALENDAR_UNCOVERED,
    CODE_FULL_HISTORY_START_MISSING,
    CODE_REMOVED_FALLBACK_FIELD_PRESENT,
    SOURCE_BOOTSTRAP_SEED,
    SOURCE_TUSHARE_RELAY,
    CalendarCoverageError,
    coverage_from_payload,
    coverage_payload,
    full_history_violations,
    merge_window,
    seed_span,
    span_payload,
    span_violations,
    supplier_span,
    validate_build_calendar_evidence,
)


def _codes(violations) -> list[str]:
    return [code for code, _ in violations]


def _relay(start: str, end: str, *, sse=("a" * 64,), szse=("b" * 64,)):
    return supplier_span(
        date.fromisoformat(start),
        date.fromisoformat(end),
        source=SOURCE_TUSHARE_RELAY,
        snapshots_by_exchange={"SSE": list(sse), "SZSE": list(szse)},
    )


def _seed(start: str, end: str):
    return seed_span(date.fromisoformat(start), date.fromisoformat(end))


def _days(start: str, end: str) -> tuple[date, ...]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return tuple(
        date.fromordinal(day)
        for day in range(first.toordinal(), last.toordinal() + 1)
    )


def test_seed_span_payload_has_no_snapshot_field():
    payload = span_payload(_seed("2020-01-01", "2020-01-31"))
    assert payload == {
        "start_date": "2020-01-01",
        "end_date": "2020-01-31",
        "source": SOURCE_BOOTSTRAP_SEED,
    }


def test_supplier_span_sorts_and_dedups_snapshot_hashes():
    span = _relay(
        "2020-01-01",
        "2020-01-31",
        sse=("b" * 64, "a" * 64, "a" * 64),
        szse=("d" * 64, "c" * 64),
    )
    assert span.snapshot_sha256s == {
        "SSE": ("a" * 64, "b" * 64),
        "SZSE": ("c" * 64, "d" * 64),
    }
    assert span_payload(span)["snapshot_sha256s"] == {
        "SSE": ["a" * 64, "b" * 64],
        "SZSE": ["c" * 64, "d" * 64],
    }


def test_coverage_payload_round_trips():
    spans = (_seed("2020-01-01", "2020-01-09"), _relay("2020-01-10", "2020-01-31"))
    assert coverage_from_payload(coverage_payload(spans)) == spans


@pytest.mark.parametrize(
    "payload",
    [
        [
            {
                "start_date": "2020-01-31",
                "end_date": "2020-01-01",
                "source": SOURCE_TUSHARE_RELAY,
            }
        ],
        [
            {
                "start_date": "2020-01-01",
                "end_date": "2020-01-31",
                "source": "unknown_source",
            }
        ],
        [{"start_date": "2020-01-01", "end_date": "2020-01-31"}],
        [
            {
                "start_date": "nope",
                "end_date": "2020-01-31",
                "source": SOURCE_BOOTSTRAP_SEED,
            }
        ],
        ["not-a-mapping"],
    ],
)
def test_coverage_from_payload_rejects_malformed_spans(payload):
    with pytest.raises(CalendarCoverageError) as error:
        coverage_from_payload(payload)
    assert _codes(error.value.violations) == [CODE_CALENDAR_COVERAGE_INVALID]


def test_supplier_span_rejects_a_non_supplier_source_and_incomplete_hashes():
    """A seed never carries hashes; a supplier span must carry both exchanges."""
    with pytest.raises(ValueError, match="seed span carries no snapshot hashes"):
        supplier_span(
            date(2020, 1, 1),
            date(2020, 1, 31),
            source=SOURCE_BOOTSTRAP_SEED,
            snapshots_by_exchange={"SSE": ["a" * 64]},
        )
    with pytest.raises(ValueError, match="SSE, SZSE"):
        supplier_span(
            date(2020, 1, 1),
            date(2020, 1, 31),
            source=SOURCE_TUSHARE_RELAY,
            snapshots_by_exchange={"SSE": ["a" * 64]},
        )


def test_span_violations_report_unsorted_overlap_and_gap():
    unsorted = (_relay("2020-02-01", "2020-02-28"), _relay("2020-01-01", "2020-01-31"))
    assert _codes(span_violations(unsorted)) == [CODE_CALENDAR_COVERAGE_UNSORTED]
    overlap = (_relay("2020-01-01", "2020-01-31"), _relay("2020-01-31", "2020-02-28"))
    assert _codes(span_violations(overlap)) == [CODE_CALENDAR_COVERAGE_OVERLAP]
    gap = (_relay("2020-01-01", "2020-01-31"), _relay("2020-02-02", "2020-02-28"))
    assert _codes(span_violations(gap)) == [CODE_CALENDAR_COVERAGE_GAP]


def test_merge_window_splits_the_seed_span_around_the_window():
    spans = (_seed("2020-01-01", "2020-01-31"),)
    merged = merge_window(
        spans,
        start=date(2020, 1, 10),
        end=date(2020, 1, 20),
        replacement=_relay("2020-01-10", "2020-01-20"),
        open_days=_days("2020-01-02", "2020-01-30"),
    )
    assert [(s.start_date, s.end_date, s.source) for s in merged] == [
        (date(2020, 1, 1), date(2020, 1, 9), SOURCE_BOOTSTRAP_SEED),
        (date(2020, 1, 10), date(2020, 1, 20), SOURCE_TUSHARE_RELAY),
        (date(2020, 1, 21), date(2020, 1, 31), SOURCE_BOOTSTRAP_SEED),
    ]


def test_merge_window_merges_adjacent_same_source_spans_and_unions_hashes():
    spans = (_relay("2020-01-01", "2020-01-09", sse=("a" * 64,)),)
    merged = merge_window(
        spans,
        start=date(2020, 1, 10),
        end=date(2020, 1, 20),
        replacement=_relay("2020-01-10", "2020-01-20", sse=("b" * 64,)),
        open_days=_days("2020-01-02", "2020-01-20"),
    )
    assert len(merged) == 1
    assert merged[0].start_date == date(2020, 1, 1)
    assert merged[0].end_date == date(2020, 1, 20)
    assert merged[0].snapshot_sha256s["SSE"] == ("a" * 64, "b" * 64)


def test_merge_window_never_merges_seed_with_a_supplier_span():
    spans = (_seed("2020-01-01", "2020-01-09"),)
    merged = merge_window(
        spans,
        start=date(2020, 1, 10),
        end=date(2020, 1, 20),
        replacement=_relay("2020-01-10", "2020-01-20"),
        open_days=_days("2020-01-02", "2020-01-20"),
    )
    assert [(s.source, s.start_date, s.end_date) for s in merged] == [
        (SOURCE_BOOTSTRAP_SEED, date(2020, 1, 1), date(2020, 1, 9)),
        (SOURCE_TUSHARE_RELAY, date(2020, 1, 10), date(2020, 1, 20)),
    ]


def test_merge_window_raises_on_a_gap_the_window_does_not_touch():
    spans = (_seed("2020-01-01", "2020-01-31"),)
    with pytest.raises(CalendarCoverageError) as error:
        merge_window(
            spans,
            start=date(2020, 6, 1),
            end=date(2020, 6, 30),
            replacement=_relay("2020-06-01", "2020-06-30"),
            open_days=_days("2020-01-02", "2020-06-30"),
        )
    assert _codes(error.value.violations) == [CODE_CALENDAR_COVERAGE_GAP]

def test_merge_window_raises_when_the_calendar_range_is_not_covered():
    with pytest.raises(CalendarCoverageError) as error:
        merge_window(
            (),
            start=date(2020, 1, 10),
            end=date(2020, 1, 20),
            replacement=_relay("2020-01-10", "2020-01-20"),
            open_days=_days("2019-12-02", "2021-01-04"),
        )
    assert _codes(error.value.violations) == [CODE_CALENDAR_UNCOVERED]
    assert error.value.violations[0][1] == {
        "first_open_day": "2019-12-02",
        "last_open_day": "2021-01-04",
        "coverage_start": "2020-01-10",
        "coverage_end": "2020-01-20",
    }


def test_validate_build_calendar_evidence_reads_legacy_manifests_compatibly():
    legacy = {"origin": "data_update", "resolved_end_is_fallback": True}
    violations = validate_build_calendar_evidence(
        legacy, open_days=_days("2020-01-02", "2020-01-03")
    )
    assert _codes(violations) == [CODE_CALENDAR_COVERAGE_MISSING]


def test_validate_build_calendar_evidence_rejects_the_removed_field_on_new_manifests():
    build = {
        "calendar_coverage": coverage_payload([_relay("2020-01-01", "2020-01-31")]),
        "full_history_acceptance_start": "2020-01-01",
        "resolved_end_is_fallback": False,
    }
    violations = validate_build_calendar_evidence(
        build, open_days=_days("2020-01-02", "2020-01-30")
    )
    assert _codes(violations) == [CODE_REMOVED_FALLBACK_FIELD_PRESENT]


def test_validate_build_calendar_evidence_reports_a_missing_acceptance_start():
    build = {
        "calendar_coverage": coverage_payload([_relay("2020-01-01", "2020-01-31")])
    }
    violations = validate_build_calendar_evidence(
        build, open_days=_days("2020-01-02", "2020-01-30")
    )
    assert _codes(violations) == [CODE_FULL_HISTORY_START_MISSING]
    assert violations[0][1] == {"reason": "no_enabled_universe_definition"}


def test_validate_build_calendar_evidence_passes_a_bound_version():
    build = {
        "calendar_coverage": coverage_payload([_relay("2020-01-01", "2020-01-31")]),
        "full_history_acceptance_start": "2020-01-01",
    }
    assert validate_build_calendar_evidence(
        build, open_days=_days("2020-01-02", "2020-01-30")
    ) == ()


def test_full_history_violations_flag_only_the_seed_inside_the_accepted_range():
    spans = (_seed("2020-01-01", "2020-01-09"), _relay("2020-01-10", "2020-01-31"))
    violations = full_history_violations(
        spans,
        acceptance_start=date(2020, 1, 5),
        calendar_last_open=date(2020, 1, 30),
    )
    assert _codes(violations) == [CODE_BOOTSTRAP_SEED_IN_FULL_HISTORY]
    assert violations[0][1] == {
        "start_date": "2020-01-01",
        "end_date": "2020-01-09",
        "full_history_acceptance_start": "2020-01-05",
        "last_calendar_date": "2020-01-30",
    }


def test_full_history_violations_allow_a_seed_that_ends_before_the_start():
    spans = (_seed("2019-01-01", "2019-12-31"), _relay("2020-01-01", "2020-01-31"))
    assert full_history_violations(
        spans,
        acceptance_start=date(2020, 1, 5),
        calendar_last_open=date(2020, 1, 30),
    ) == ()
