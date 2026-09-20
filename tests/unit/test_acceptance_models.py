"""Acceptance contract tests for the mandatory membership-evidence gate.

Pins the required-result vocabulary, the strict determinism rules of
:class:`AcceptanceResult`, the loud :func:`enforce_required_results` failure
and the offline ``index_membership_evidence`` evaluation: it must fail on a
missing table, on any fatal validator issue, and on a definition hash
mismatch, and its pass details must carry only coverage, counts, hashes and
error codes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

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
from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    MANUAL_CHECK_CODES,
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
    AcceptanceChecklist,
    AcceptanceRecord,
    CheckResult,
    CheckStatus,
    ManualCheckResult,
    ManualCheckStatus,
    canonical_record_json,
    compute_acceptance_id,
)
from stock_quant.research.acceptance.registry import AcceptanceRegistry
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


def _check_payloads(codes: tuple[str, ...]) -> list[dict[str, str]]:
    """One PASS payload per ``code`` with a deterministic summary."""
    return [
        {"code": code, "status": "PASS", "summary": f"{code} passed"}
        for code in codes
    ]


def _snapshot_payload() -> dict[str, str]:
    """One raw-snapshot binding payload with placeholder hashes."""
    return {
        "source": "tushare",
        "endpoint": "daily",
        "request_key": "20260907",
        "file_sha256": "d" * 64,
        "manifest_sha256": "e" * 64,
    }


def _record_payload() -> dict[str, object]:
    """A valid ACCEPTED record payload whose id is a placeholder hash."""
    return {
        "schema_version": 1,
        "policy_version": "real-data-v1",
        "acceptance_id": "0" * 64,
        "dataset_version": "a" * 64,
        "dataset_manifest_sha256": "b" * 64,
        "quality_report_sha256": "c" * 64,
        "created_at": "2026-09-08T12:00:00+00:00",
        "operator_id": "operator-a",
        "automated_checks": _check_payloads(AUTOMATED_CHECK_CODES),
        "manual_checks": _check_payloads(MANUAL_CHECK_CODES),
        "raw_snapshot_evidence": [_snapshot_payload()],
        "decision": "ACCEPTED",
        "reasons": [],
    }


@pytest.fixture
def checklist_payload() -> dict[str, object]:
    """A valid checklist payload: every policy check present and PASS."""
    return {
        "schema_version": 1,
        "policy_version": "real-data-v1",
        "dataset_version": "a" * 64,
        "dataset_manifest_sha256": "b" * 64,
        "quality_report_sha256": "c" * 64,
        "prepared_at": "2026-09-08T00:00:00+00:00",
        "operator_id": "operator-a",
        "automated_checks": _check_payloads(AUTOMATED_CHECK_CODES),
        "manual_checks": _check_payloads(MANUAL_CHECK_CODES),
        "raw_snapshot_evidence": [_snapshot_payload()],
    }


@pytest.fixture
def record() -> AcceptanceRecord:
    """A valid ACCEPTED record (its acceptance_id is a placeholder hash)."""
    return AcceptanceRecord.model_validate(_record_payload())


# --------------------------------------------------------------------------- #
# Fixed policy vocabulary and strict field rules
# --------------------------------------------------------------------------- #


def test_policy_check_codes_are_fixed():
    assert AUTOMATED_CHECK_CODES == (
        "dataset_manifest_integrity",
        "quality_report_integrity",
        "required_table_coverage",
        "date_window_completeness",
        "security_master_evidence",
        "corporate_action_evidence",
        "raw_snapshot_traceability",
        "calendar_coverage_evidence",
        "table_fetch_coverage_evidence",
        "source_role_health",
    )
    assert MANUAL_CHECK_CODES == (
        "exchange_calendar_sample",
        "source_row_count_sample",
        "missing_reason_sample",
        "cross_source_price_sample",
        "corporate_action_sample",
        "benchmark_sample",
        "trading_rule_effective_dates",
        "security_master_sample",
        "secret_scan",
    )


def test_check_result_rejects_empty_summary_and_unknown_fields():
    with pytest.raises(ValidationError):
        CheckResult(code="x", status=CheckStatus.PASS, summary="")
    with pytest.raises(ValidationError):
        CheckResult(code="x", status=CheckStatus.PASS, summary="ok", extra=True)


def test_manual_status_adds_only_the_pending_state():
    """The manual vocabulary is the automated one plus one unconfirmed state."""
    assert [status.value for status in ManualCheckStatus] == [
        "PENDING_CONFIRMATION",
        "PASS",
        "FAIL",
    ]
    assert ManualCheckStatus.PASS.value == CheckStatus.PASS.value
    assert ManualCheckStatus.FAIL.value == CheckStatus.FAIL.value


def test_manual_result_renders_the_legacy_check_result_payload():
    """The manual row's canonical JSON is unchanged, so record ids stay stable.

    ``acceptance_id`` is the SHA-256 of the canonical payload, and a status is
    rendered by its ``value``: as long as the two models carry the same fields
    and the two PASS/FAIL values agree, a record published before this change
    keeps its id bit-for-bit.
    """
    assert list(ManualCheckResult.model_fields) == list(CheckResult.model_fields)
    manual = ManualCheckResult(code="secret_scan", status="PASS", summary="ok")
    legacy = CheckResult(code="secret_scan", status="PASS", summary="ok")
    assert manual.model_dump(mode="json") == legacy.model_dump(mode="json")


def test_automated_check_rejects_the_pending_state():
    with pytest.raises(ValidationError):
        CheckResult(code="x", status="PENDING_CONFIRMATION", summary="ok")
    with pytest.raises(ValidationError):
        ManualCheckResult(code="x", status="UNKNOWN", summary="ok")


def test_manual_row_classification_tiles_the_policy_vocabulary():
    assert set(MECHANISABLE_CODES) | set(OPERATOR_ONLY_CODES) == set(
        MANUAL_CHECK_CODES
    )
    assert set(MECHANISABLE_CODES) & set(OPERATOR_ONLY_CODES) == set()


def test_accepted_record_cannot_carry_a_pending_manual_row():
    payload = _record_payload()
    payload["manual_checks"][0]["status"] = "PENDING_CONFIRMATION"
    with pytest.raises(ValidationError, match="ACCEPTED requires"):
        AcceptanceRecord.model_validate(payload)


def test_checklist_requires_every_manual_code_once(checklist_payload):
    checklist_payload["manual_checks"] = checklist_payload["manual_checks"][:-1]
    with pytest.raises(ValidationError, match="manual check codes"):
        AcceptanceChecklist.model_validate(checklist_payload)


def test_acceptance_record_rejects_inconsistent_decisions():
    failing = _record_payload()
    failing["manual_checks"][0]["status"] = "FAIL"
    with pytest.raises(ValidationError, match="ACCEPTED requires"):
        AcceptanceRecord.model_validate(failing)
    rejected = _record_payload()
    rejected["decision"] = "REJECTED"
    with pytest.raises(ValidationError, match="REJECTED requires"):
        AcceptanceRecord.model_validate(rejected)


def test_checklist_and_record_parse_utc_datetimes(checklist_payload, record):
    assert checklist_payload["prepared_at"] == "2026-09-08T00:00:00+00:00"
    checklist = AcceptanceChecklist.model_validate(checklist_payload)
    assert checklist.prepared_at.tzinfo is not None
    assert checklist.prepared_at.utcoffset() == timedelta(0)
    assert record.created_at.tzinfo is not None
    assert record.created_at.utcoffset() == timedelta(0)


def test_checklist_and_record_reject_naive_datetimes(checklist_payload):
    """Aware timestamps only: a naive ``prepared_at``/``created_at`` fails."""
    checklist_payload["prepared_at"] = datetime(2026, 9, 8)
    with pytest.raises(ValidationError):
        AcceptanceChecklist.model_validate(checklist_payload)
    record_payload = _record_payload()
    record_payload["created_at"] = "2026-09-08T12:00:00"
    with pytest.raises(ValidationError):
        AcceptanceRecord.model_validate(record_payload)


# --------------------------------------------------------------------------- #
# Canonical content identity
# --------------------------------------------------------------------------- #


def test_acceptance_identity_is_canonical(record):
    first = compute_acceptance_id(
        record.model_copy(update={"acceptance_id": "0" * 64})
    )
    reordered = record.model_copy(
        update={"automated_checks": tuple(reversed(record.automated_checks))}
    )
    second = compute_acceptance_id(
        reordered.model_copy(update={"acceptance_id": "f" * 64})
    )
    assert first == second


def test_created_at_and_operator_change_identity(record):
    assert compute_acceptance_id(record) != compute_acceptance_id(
        record.model_copy(update={"operator_id": "second-operator"})
    )
    assert compute_acceptance_id(record) != compute_acceptance_id(
        record.model_copy(
            update={"created_at": datetime(2026, 9, 9, tzinfo=timezone.utc)}
        )
    )


def test_canonical_record_json_roundtrips_and_requires_matching_id(record):
    consistent = record.model_copy(
        update={"acceptance_id": compute_acceptance_id(record)}
    )
    text = canonical_record_json(consistent)
    assert text.endswith("\n")
    assert AcceptanceRecord.model_validate_json(text) == consistent
    with pytest.raises(ValueError, match="acceptance_id"):
        canonical_record_json(record)


def _legacy_canonical_payload(record: AcceptanceRecord) -> dict[str, object]:
    """The canonical payload exactly as the pre-``transport_id`` code built it.

    Reconstructed from the raw field values, not by calling the production
    :func:`_canonical_payload`: every evidence row loses the ``transport_id``
    key (the field did not exist) and rows sort by their four-component
    location key.  ``acceptance_id`` is retained; callers drop it to hash.
    """
    payload = record.model_dump(mode="json")
    payload["automated_checks"] = sorted(
        payload["automated_checks"], key=lambda row: row["code"]
    )
    payload["manual_checks"] = sorted(
        payload["manual_checks"], key=lambda row: row["code"]
    )
    payload["raw_snapshot_evidence"] = sorted(
        (
            {key: value for key, value in row.items() if key != "transport_id"}
            for row in payload["raw_snapshot_evidence"]
        ),
        key=lambda row: (
            row["source"],
            row["endpoint"],
            row["request_key"],
            row["file_sha256"],
        ),
    )
    return payload


def _legacy_acceptance_id(record: AcceptanceRecord) -> str:
    """The pre-``transport_id`` ``acceptance_id``, an explicit test oracle."""
    payload = _legacy_canonical_payload(record)
    payload.pop("acceptance_id", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_a_pre_change_record_still_verifies_and_transport_still_bears_identity(
    record, tmp_path
):
    """A record published before ``transport_id`` keeps its ``acceptance_id``.

    The other identity tests are self-consistent (they hash with the new
    model), so only an independent legacy oracle can pin read compatibility.
    A pre-change ``acceptance.json`` has no ``transport_id`` key at all, so the
    test writes those exact bytes and requires ``get`` to still verify the id.
    """
    legacy_id = _legacy_acceptance_id(record)
    assert record.raw_snapshot_evidence[0].transport_id is None
    assert compute_acceptance_id(record) == legacy_id

    published = record.model_copy(update={"acceptance_id": legacy_id})
    # The pre-change ``canonical_record_json`` dumped the record as-is (no
    # array re-sorting); only the ``transport_id`` key did not exist.  Those
    # exact bytes are what a pre-change producer wrote.
    legacy_text = record.model_dump(mode="json")
    legacy_text["acceptance_id"] = legacy_id
    legacy_text["raw_snapshot_evidence"] = [
        {key: value for key, value in row.items() if key != "transport_id"}
        for row in legacy_text["raw_snapshot_evidence"]
    ]
    path = (
        tmp_path
        / "data"
        / "acceptances"
        / published.dataset_version
        / legacy_id
        / "acceptance.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            legacy_text,
            sort_keys=True,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    assert (
        AcceptanceRegistry(tmp_path).get(published.dataset_version, legacy_id)
        == published
    )

    current = record.model_copy(
        update={
            "raw_snapshot_evidence": (
                record.raw_snapshot_evidence[0].model_copy(
                    update={"transport_id": "jiaoch.example"}
                ),
            )
        }
    )
    assert compute_acceptance_id(current) != legacy_id
