"""Acceptance contract tests for the mandatory membership-evidence gate.

Pins the required-result vocabulary, the strict determinism rules of
:class:`AcceptanceResult`, the loud :func:`enforce_required_results` failure
and the offline ``index_membership_evidence`` evaluation: it must fail on a
missing table, on any fatal validator issue, and on a definition hash
mismatch, and its pass details must carry only coverage, counts, hashes and
error codes.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from pydantic import ValidationError

from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.universe_membership import (
    membership_content_hash,
    membership_frame,
)
from stock_quant.research.acceptance import (
    CODE_DEFINITION_HASH_MISMATCH,
    CODE_TABLE_MISSING,
    REQUIRED_ACCEPTANCE_RESULTS,
    AcceptanceGateError,
    AcceptanceResult,
    AcceptanceStatus,
    enforce_required_results,
    evaluate_index_membership_evidence,
    read_membership_table,
)
from stock_quant.research.universe import UniverseDefinition


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _fact_payload(symbol: str) -> dict[str, object]:
    return {
        "universe_id": "csi300",
        "symbol": symbol,
        "raw_effective_from": date(2019, 1, 1),
        "raw_effective_to": None,
        "announcement_date": date(2018, 12, 17),
        "status": "active",
        "reason": "initial_constituent",
        "source": "csi_index_announcement",
        "source_url": "https://www.csindex.com.cn/announcement.pdf",
        "snapshot_sha256": "a1" * 32,
        "source_document_sha256": "b2" * 32,
    }


_FACT_PAYLOADS = [_fact_payload("600000.SH"), _fact_payload("000001.SZ")]
_EXPECTED_SIZES = {"csi300": 2}


@pytest.fixture
def facts() -> pd.DataFrame:
    return membership_frame(_FACT_PAYLOADS)


@pytest.fixture
def definition() -> UniverseDefinition:
    return UniverseDefinition(
        universe_id="csi300",
        rules_version="official-rules-v1",
        membership_table_sha256=membership_content_hash(_FACT_PAYLOADS),
        coverage_start=date(2019, 1, 1),
        coverage_end=date(2021, 12, 31),
        evidence_summary_sha256="e5" * 32,
    )


@pytest.fixture
def calendar() -> TradingCalendar:
    return TradingCalendar.from_open_days(
        tuple(_weekdays(date(2019, 1, 1), date(2021, 12, 31)))
    )


def _evaluate(facts, definition, calendar):
    return evaluate_index_membership_evidence(
        facts,
        definition=definition,
        calendar=calendar,
        expected_sizes=_EXPECTED_SIZES,
    )


# --------------------------------------------------------------------------- #
# Required-result vocabulary and strict result contract
# --------------------------------------------------------------------------- #


def test_index_membership_evidence_is_mandatory():
    assert REQUIRED_ACCEPTANCE_RESULTS == ("index_membership_evidence",)


def test_acceptance_result_rejects_unknown_code_and_extra_fields():
    with pytest.raises(ValidationError):
        AcceptanceResult(code="made_up", status=AcceptanceStatus.PASS,
                         summary="ok")
    with pytest.raises(ValidationError):
        AcceptanceResult(
            code="index_membership_evidence",
            status=AcceptanceStatus.PASS,
            summary="ok",
            extra="not allowed",
        )
    with pytest.raises(ValidationError):
        AcceptanceResult(code="index_membership_evidence",
                         status=AcceptanceStatus.PASS, summary="")


def test_acceptance_details_carry_only_deterministic_keys():
    with pytest.raises(ValidationError, match="non-deterministic"):
        AcceptanceResult(
            code="index_membership_evidence",
            status=AcceptanceStatus.FAIL,
            summary="blocked",
            details={"rows": [{"symbol": "600000.SH"}]},
        )


def test_error_codes_must_be_sorted_and_unique():
    with pytest.raises(ValidationError, match="sorted"):
        AcceptanceResult(
            code="index_membership_evidence",
            status=AcceptanceStatus.FAIL,
            summary="blocked",
            details={"error_codes": ["B_CODE", "A_CODE", "A_CODE"]},
        )


def test_enforce_required_results_passes_on_synthetic_pass():
    result = AcceptanceResult(
        code="index_membership_evidence",
        status=AcceptanceStatus.PASS,
        summary="accepted",
    )
    assert enforce_required_results([result]) is None


def test_enforce_required_results_fails_loudly_on_missing_or_failed():
    with pytest.raises(AcceptanceGateError, match="missing"):
        enforce_required_results([])
    failed = AcceptanceResult(
        code="index_membership_evidence",
        status=AcceptanceStatus.FAIL,
        summary="blocked",
        details={"error_codes": ["UNIVERSE_EVIDENCE_MISSING"]},
    )
    with pytest.raises(AcceptanceGateError, match="failing"):
        enforce_required_results([failed])


# --------------------------------------------------------------------------- #
# index_membership_evidence evaluation
# --------------------------------------------------------------------------- #


def test_missing_membership_table_fails_explicitly(facts, definition, calendar):
    result = _evaluate(None, definition, calendar)
    assert result.status is AcceptanceStatus.FAIL
    assert CODE_TABLE_MISSING in result.details["error_codes"]
    result = _evaluate(facts.iloc[:0], definition, calendar)
    assert result.status is AcceptanceStatus.FAIL
    assert CODE_TABLE_MISSING in result.details["error_codes"]


def test_missing_evidence_fails_with_validator_codes(
    facts, definition, calendar
):
    facts.loc[0, "snapshot_sha256"] = ""
    result = _evaluate(facts, definition, calendar)
    assert result.status is AcceptanceStatus.FAIL
    assert "UNIVERSE_EVIDENCE_MISSING" in result.details["error_codes"]


def test_cardinality_mismatch_fails_the_gate(facts, definition, calendar):
    result = evaluate_index_membership_evidence(
        facts.iloc[:-1],
        definition=definition,
        calendar=calendar,
        expected_sizes=_EXPECTED_SIZES,
    )
    assert result.status is AcceptanceStatus.FAIL
    assert "UNIVERSE_MEMBER_COUNT_MISMATCH" in result.details["error_codes"]


def test_definition_hash_mismatch_fails_the_gate(
    facts, definition, calendar
):
    tampered = definition.model_copy(
        update={"membership_table_sha256": "f" * 64}
    )
    result = _evaluate(facts, tampered, calendar)
    assert result.status is AcceptanceStatus.FAIL
    assert (
        CODE_DEFINITION_HASH_MISMATCH in result.details["error_codes"]
    )


def test_pass_result_details_are_deterministic_and_pinned(
    facts, definition, calendar
):
    first = _evaluate(facts, definition, calendar)
    second = _evaluate(facts, definition, calendar)
    assert first == second
    assert first.status is AcceptanceStatus.PASS
    assert first.details["coverage"] == {
        "universe_id": "csi300",
        "coverage_start": "2019-01-01",
        "coverage_end": "2021-12-31",
    }
    assert first.details["counts"]["fact_rows"] == 2
    assert first.details["counts"]["distinct_symbols"] == 2
    assert first.details["hashes"]["membership_table_sha256"] == (
        membership_content_hash(_FACT_PAYLOADS)
    )
    assert first.details["hashes"]["definition_version"] == definition.version
    assert first.details["error_codes"] == []


def test_read_membership_table_returns_none_when_absent(facts):
    class _Context:
        tables = ()

        def read(self, name):
            raise AssertionError("a missing table is never read")

    assert read_membership_table(_Context()) is None

    class _PresentContext:
        tables = ("universe_membership",)

        def read(self, name):
            return facts

    assert read_membership_table(_PresentContext()) is facts
