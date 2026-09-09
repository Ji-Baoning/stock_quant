"""Acceptance registry integration tests (real-data-v1).

The tests pin the immutable on-disk layout of the content-addressed
acceptance registry: atomic publication, idempotent replay, identity
conflicts, ACCEPTED/REJECTED history, strict integrity reads, stable
selection and concurrent publication.  Everything runs offline on
synthetic records under ``tmp_path``.
"""

import threading

import pytest

from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    CURRENT_ACCEPTED,
    MANUAL_CHECK_CODES,
    AcceptanceRecord,
    compute_acceptance_id,
)
from stock_quant.research.acceptance.registry import (
    AcceptanceIdentityConflict,
    AcceptanceIntegrityError,
    AcceptanceRegistry,
    NoValidAcceptance,
)

#: The synthetic dataset version every fixture record binds.
_DATASET_VERSION = "a" * 64


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


def _record_payload(**overrides: object) -> dict[str, object]:
    """A valid ACCEPTED record payload whose id is a placeholder hash."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "policy_version": "real-data-v1",
        "acceptance_id": "0" * 64,
        "dataset_version": _DATASET_VERSION,
        "dataset_manifest_sha256": "b" * 64,
        "quality_report_sha256": "c" * 64,
        "created_at": "2026-09-03T12:00:00+00:00",
        "operator_id": "operator-a",
        "automated_checks": _check_payloads(AUTOMATED_CHECK_CODES),
        "manual_checks": _check_payloads(MANUAL_CHECK_CODES),
        "raw_snapshot_evidence": [_snapshot_payload()],
        "decision": "ACCEPTED",
        "reasons": [],
    }
    payload.update(overrides)
    return payload


def _self_consistent(**overrides: object) -> AcceptanceRecord:
    """A record whose acceptance_id is the hash of its own payload."""
    provisional = AcceptanceRecord.model_validate(_record_payload(**overrides))
    return provisional.model_copy(
        update={"acceptance_id": compute_acceptance_id(provisional)}
    )


@pytest.fixture
def accepted_record() -> AcceptanceRecord:
    """A self-consistent ACCEPTED record under the current policy."""
    return _self_consistent()


@pytest.fixture
def rejected_record() -> AcceptanceRecord:
    """A self-consistent REJECTED record published before the accepted one."""
    failing = _check_payloads(AUTOMATED_CHECK_CODES)
    failing[0]["status"] = "FAIL"
    return _self_consistent(
        created_at="2026-09-01T12:00:00+00:00",
        automated_checks=failing,
        decision="REJECTED",
        reasons=["automated_dataset_manifest_integrity_failed"],
    )


@pytest.fixture
def old_policy_record() -> AcceptanceRecord:
    """A self-consistent ACCEPTED record under an expired policy version."""
    return _self_consistent(
        created_at="2026-09-02T12:00:00+00:00",
        policy_version="real-data-v0",
    )


def test_publish_writes_content_addressed_record(tmp_path, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    published = registry.publish(accepted_record)
    path = (
        tmp_path / "data" / "acceptances" / accepted_record.dataset_version
        / accepted_record.acceptance_id / "acceptance.json"
    )
    assert path.is_file()
    assert registry.get(
        accepted_record.dataset_version, accepted_record.acceptance_id
    ) == published


def test_identical_publish_is_idempotent(tmp_path, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    first = registry.publish(accepted_record)
    before = registry.path_for(first).read_bytes()
    second = registry.publish(accepted_record)
    assert second == first
    assert registry.path_for(second).read_bytes() == before


def test_publish_conflicting_content_under_same_id_fails(
    tmp_path, accepted_record
):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(accepted_record)
    path = registry.path_for(accepted_record)
    path.write_text(path.read_text().replace("operator-a", "operator-b"))
    with pytest.raises(AcceptanceIdentityConflict):
        registry.publish(accepted_record)
    assert "operator-b" in path.read_text()


def test_history_keeps_rejected_and_accepted_records(
    tmp_path, rejected_record, accepted_record
):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(rejected_record)
    registry.publish(accepted_record)
    assert [
        row.decision.value for row in registry.list(accepted_record.dataset_version)
    ] == ["REJECTED", "ACCEPTED"]


def test_list_returns_empty_tuple_for_unknown_version(tmp_path):
    assert AcceptanceRegistry(tmp_path).list("f" * 64) == ()


def test_select_current_accepted_ignores_rejected_and_old_policy(
    tmp_path, rejected_record, old_policy_record, accepted_record
):
    registry = AcceptanceRegistry(tmp_path)
    for record in (rejected_record, old_policy_record, accepted_record):
        registry.publish(record)
    assert registry.select(
        accepted_record.dataset_version, CURRENT_ACCEPTED
    ).acceptance_id == accepted_record.acceptance_id
    assert (
        registry.select(
            accepted_record.dataset_version,
            CURRENT_ACCEPTED,
            policy_version="real-data-v0",
        ).acceptance_id
        == old_policy_record.acceptance_id
    )


def test_select_explicit_id_returns_requested_record(
    tmp_path, rejected_record, accepted_record
):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(rejected_record)
    registry.publish(accepted_record)
    selected = registry.select(
        accepted_record.dataset_version, accepted_record.acceptance_id
    )
    assert selected == accepted_record


def test_select_rejected_record_is_not_valid(
    tmp_path, rejected_record, accepted_record
):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(rejected_record)
    registry.publish(accepted_record)
    with pytest.raises(NoValidAcceptance):
        registry.select(
            accepted_record.dataset_version, rejected_record.acceptance_id
        )


def test_get_detects_tampered_record(tmp_path, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(accepted_record)
    path = registry.path_for(accepted_record)
    path.write_text(path.read_text().replace("operator-a", "operator-b"))
    with pytest.raises(AcceptanceIntegrityError):
        registry.get(accepted_record.dataset_version, accepted_record.acceptance_id)


def test_get_detects_truncated_json(tmp_path, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(accepted_record)
    path = registry.path_for(accepted_record)
    payload = path.read_text()
    path.write_text(payload[: len(payload) // 2])
    with pytest.raises(AcceptanceIntegrityError):
        registry.get(_DATASET_VERSION, accepted_record.acceptance_id)


def test_get_detects_binary_garbage(tmp_path, accepted_record):
    """A record file that is not valid UTF-8 is corrupt, never a crash."""
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(accepted_record)
    registry.path_for(accepted_record).write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(AcceptanceIntegrityError):
        registry.get(_DATASET_VERSION, accepted_record.acceptance_id)


def test_get_detects_decision_flip_without_reasons(tmp_path, accepted_record):
    registry = AcceptanceRegistry(tmp_path)
    registry.publish(accepted_record)
    path = registry.path_for(accepted_record)
    path.write_text(
        path.read_text().replace('"decision": "ACCEPTED"', '"decision": "REJECTED"')
    )
    with pytest.raises(AcceptanceIntegrityError):
        registry.get(_DATASET_VERSION, accepted_record.acceptance_id)


def test_get_rejects_accepted_record_missing_policy_checks(tmp_path):
    """A hand-crafted ACCEPTED record cannot dodge checks via truncation."""
    registry = AcceptanceRegistry(tmp_path)
    truncated = _self_consistent(
        manual_checks=_check_payloads(MANUAL_CHECK_CODES[:1])
    )
    registry.publish(truncated)
    with pytest.raises(AcceptanceIntegrityError):
        registry.get(_DATASET_VERSION, truncated.acceptance_id)


def test_concurrent_publish_of_same_record_is_atomic(tmp_path, accepted_record):
    """Same-record publication races leave exactly one intact final record."""
    for attempt in range(6):
        root = tmp_path / f"attempt-{attempt}"
        registry = AcceptanceRegistry(root)
        published: list[str] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def publish() -> None:
            try:
                barrier.wait(timeout=5)
                published.append(
                    registry.publish(accepted_record).acceptance_id
                )
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=publish) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        assert published == [accepted_record.acceptance_id] * 2
        version_dir = root / "data" / "acceptances" / _DATASET_VERSION
        assert [child.name for child in version_dir.iterdir()] == [
            accepted_record.acceptance_id
        ]
        assert (
            registry.get(_DATASET_VERSION, accepted_record.acceptance_id)
            == accepted_record
        )
