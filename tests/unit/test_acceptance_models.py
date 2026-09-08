"""Acceptance contract unit tests (real-data-v1).

The tests pin the fixed policy check-code vocabulary, the strict field rules
of every contract model, the exact-coverage requirement for automated and
manual check codes, the decision cross-validation and the canonical content
identity of an acceptance record.  Everything runs offline on plain payloads.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    MANUAL_CHECK_CODES,
    AcceptanceChecklist,
    AcceptanceRecord,
    CheckResult,
    CheckStatus,
    canonical_record_json,
    compute_acceptance_id,
)


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
