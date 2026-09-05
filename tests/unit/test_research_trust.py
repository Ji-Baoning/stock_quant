"""Corporate-action trust evaluation for research runs (trust-gate Task 2).

``evaluate_corporate_action_trust`` decides whether every possible holding of
one frozen run has full trusted evidence across the complete execution window.
Empty facts are trusted only through explicit ``VERIFIED_EMPTY`` rows; a
dataset with no coverage evidence at all reads ``SOURCE_NOT_REQUESTED``, a
symbol whose trusted rows leave a gap reads ``COVERAGE_INCOMPLETE``, and an
overlapping ``UNTRUSTED`` row carries its own stable reason.  Every test here
is in-memory and deterministic (no wall clock anywhere).
"""

from __future__ import annotations

from datetime import date

from stock_quant.data_model.corporate_action_coverage import (
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.research.trust import (
    DataTrustMode,
    evaluate_corporate_action_trust,
)


def verified(symbol: str, window_start: date, window_end: date) -> dict:
    """One VERIFIED coverage row over ``symbol``'s ``[window_start, window_end]``."""
    return coverage_record(symbol, window_start, window_end,
                           CoverageStatus.VERIFIED)


def _empty(
    symbol: str, window_start: date, window_end: date
) -> dict:
    """One VERIFIED_EMPTY coverage row (explicit successful no-event check)."""
    return coverage_record(symbol, window_start, window_end,
                           CoverageStatus.VERIFIED_EMPTY)


def test_trust_requires_full_verified_coverage():
    decision = evaluate_corporate_action_trust(
        coverage=coverage_frame(
            [verified("600000.SH", date(2024, 1, 1), date(2024, 6, 30))]
        ),
        symbols={"600000.SH"}, window_start=date(2024, 1, 1),
        window_end=date(2024, 12, 31),
    )
    assert not decision.trusted
    assert decision.reasons[0].code == "COVERAGE_INCOMPLETE"


def test_trust_accepts_verified_empty_spanning_the_window():
    decision = evaluate_corporate_action_trust(
        coverage=coverage_frame(
            [_empty("600000.SH", date(2024, 1, 1), date(2024, 12, 31))]
        ),
        symbols={"600000.SH"}, window_start=date(2024, 1, 1),
        window_end=date(2024, 12, 31),
    )
    assert decision.trusted
    assert decision.reasons == ()


def test_trust_accepts_verified_rows_that_tile_the_window():
    decision = evaluate_corporate_action_trust(
        coverage=coverage_frame(
            [
                verified("600000.SH", date(2024, 1, 1), date(2024, 6, 30)),
                verified("600000.SH", date(2024, 7, 1), date(2024, 12, 31)),
            ]
        ),
        symbols={"600000.SH"}, window_start=date(2024, 1, 1),
        window_end=date(2024, 12, 31),
    )
    assert decision.trusted


def test_trust_rejects_an_overlapping_untrusted_row_with_its_reason():
    decision = evaluate_corporate_action_trust(
        coverage=coverage_frame(
            [
                coverage_record(
                    "600000.SH", date(2024, 1, 1), date(2024, 12, 31),
                    CoverageStatus.UNTRUSTED,
                    CoverageReason.SOURCE_FETCH_FAILED,
                )
            ]
        ),
        symbols={"600000.SH"}, window_start=date(2024, 1, 1),
        window_end=date(2024, 12, 31),
    )
    assert not decision.trusted
    assert decision.reasons[0].code == "SOURCE_FETCH_FAILED"


def test_trust_rejects_every_symbol_when_no_coverage_evidence_exists():
    decision = evaluate_corporate_action_trust(
        coverage=coverage_frame([]),
        symbols={"600000.SH", "601398.SH"},
        window_start=date(2024, 1, 1), window_end=date(2024, 12, 31),
    )
    assert not decision.trusted
    assert [reason.code for reason in decision.reasons] == [
        "SOURCE_NOT_REQUESTED",
        "SOURCE_NOT_REQUESTED",
    ]


def test_trust_treats_absent_coverage_table_as_source_not_requested():
    decision = evaluate_corporate_action_trust(
        coverage=None,
        symbols={"600000.SH"},
        window_start=date(2024, 1, 1), window_end=date(2024, 12, 31),
    )
    assert not decision.trusted
    assert decision.reasons[0].code == "SOURCE_NOT_REQUESTED"
    assert decision.reasons[0].symbol == "600000.SH"


def test_trust_reasons_are_per_symbol_deterministic_and_sorted():
    decision = evaluate_corporate_action_trust(
        coverage=coverage_frame(
            [
                verified("601398.SH", date(2024, 1, 1), date(2024, 6, 30)),
                coverage_record(
                    "600000.SH", date(2024, 1, 1), date(2024, 12, 31),
                    CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_CONFLICT,
                ),
            ]
        ),
        symbols={"600000.SH", "601398.SH"},
        window_start=date(2024, 1, 1), window_end=date(2024, 12, 31),
    )
    assert not decision.trusted
    # 600000.SH is UNTRUSTED; 601398.SH has only half-window coverage.
    assert [(r.symbol, r.code) for r in decision.reasons] == [
        ("600000.SH", "SOURCE_CONFLICT"),
        ("601398.SH", "COVERAGE_INCOMPLETE"),
    ]


def test_trust_mode_names_are_the_frozen_storage_values():
    assert DataTrustMode.RESEARCH.value == "research"
    assert DataTrustMode.ENGINEERING.value == "engineering"
    assert DataTrustMode.ENGINEERING != DataTrustMode.RESEARCH
