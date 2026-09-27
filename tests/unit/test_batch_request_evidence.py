"""Batch transmission provenance (spec §5, ADR-020 D8)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from stock_quant.data_model.batch_evidence import (
    OUTCOME_EMPTY,
    OUTCOME_OK,
    OUTCOME_REFUSED,
    BatchOutcomeRecord,
    BatchRequestEvidence,
    batch_id_for,
    batch_request_parameters,
)
from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore

_START = date(2024, 1, 2)
_END = date(2024, 1, 5)
_PARAMS = batch_request_parameters(
    "daily", ("000001.SZ", "600000.SH"), _START, _END, {"adjustment": "unadjusted"}
)


def _evidence(**overrides) -> BatchRequestEvidence:
    fields = {
        "source": "xingyao",
        "endpoint": "daily",
        "transport_id": "xingyao-broker-tcp",
        "batch_id": batch_id_for(_PARAMS),
        "batch_request_parameters": _PARAMS,
        "request_timestamp": "2024-01-05T09:00:00+00:00",
        "response_timestamp": "2024-01-05T09:00:07+00:00",
        "outcomes": (
            BatchOutcomeRecord("000001.SZ", "a" * 64, OUTCOME_OK, "b" * 64),
            BatchOutcomeRecord("600000.SH", "c" * 64, OUTCOME_REFUSED, None, "no key"),
        ),
    }
    fields.update(overrides)
    return BatchRequestEvidence(**fields)


def test_the_recorded_request_is_the_whole_ordered_chunk():
    """The batch shape, not just the per-symbol shapes, must be rebuildable."""
    assert json.loads(_evidence().batch_request_parameters) == {
        "endpoint": "daily",
        "symbols": ["000001.SZ", "600000.SH"],
        "start_date": "2024-01-02",
        "end_date": "2024-01-05",
        "params": {"adjustment": "unadjusted"},
    }


def test_the_batch_id_is_the_canonical_hash_of_its_parameters():
    evidence = _evidence()
    assert evidence.batch_id == batch_id_for(evidence.batch_request_parameters)


def test_the_evidence_hash_covers_the_outcomes():
    """A different outcome vector is different evidence, not the same record."""
    first = _evidence()
    second = _evidence(
        outcomes=(
            BatchOutcomeRecord("000001.SZ", "a" * 64, OUTCOME_OK, "b" * 64),
            BatchOutcomeRecord("600000.SH", "c" * 64, OUTCOME_EMPTY, "d" * 64),
        )
    )
    assert first.sha256 != second.sha256


def test_a_refused_outcome_carries_no_snapshot_identity():
    refused = [o for o in _evidence().outcomes if o.outcome == OUTCOME_REFUSED][0]
    assert refused.snapshot_file_sha256 is None
    assert refused.message


def test_an_ok_outcome_must_carry_a_snapshot_identity():
    with pytest.raises(ValueError, match="snapshot identity"):
        BatchOutcomeRecord("000001.SZ", "a" * 64, OUTCOME_OK, None)


def test_saving_twice_writes_one_content_addressed_record(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    first = store.save(_evidence())
    second = store.save(_evidence())
    assert first == second
    assert first.name == f"{_evidence().sha256}.json"
    assert json.loads(first.read_text())["batch_id"] == _evidence().batch_id


def test_the_registry_is_not_the_raw_snapshot_tree(tmp_path):
    """Batch evidence must not live under data/raw: RawStore owns that tree."""
    path = BatchEvidenceStore(tmp_path).save(_evidence())
    relative = path.relative_to(tmp_path)
    assert relative.parts[0] == "data"
    assert relative.parts[1] == "raw_batch_requests"


def test_loading_a_missing_record_is_none_not_an_error(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    assert (
        store.load(
            "xingyao", "daily", "xingyao-broker-tcp", _evidence().batch_id, "f" * 64
        )
        is None
    )


def test_a_loaded_record_round_trips_its_outcomes(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    loaded = store.load(
        "xingyao", "daily", "xingyao-broker-tcp", _evidence().batch_id, _evidence().sha256
    )
    assert loaded is not None
    assert loaded.outcomes == _evidence().outcomes


def test_a_tampered_record_is_refused(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    path = store.save(_evidence())
    payload = json.loads(path.read_text())
    payload["outcomes"][0]["symbol"] = "999999.SZ"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="hash mismatch"):
        store.load(
            "xingyao",
            "daily",
            "xingyao-broker-tcp",
            _evidence().batch_id,
            _evidence().sha256,
        )


def test_a_record_can_be_loaded_by_its_hash_alone(tmp_path):
    """The drift audit binds hashes, not paths: it must resolve them."""
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    loaded = store.load_by_sha(_evidence().sha256)
    assert loaded is not None
    assert loaded.batch_id == _evidence().batch_id
    assert store.load_by_sha("f" * 64) is None


def test_a_snapshot_can_be_traced_back_to_the_batch_that_produced_it(tmp_path):
    """A reused snapshot must carry its batch provenance back (spec §5)."""
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    found = store.lookup_by_snapshot("xingyao", "daily", "a" * 64, "b" * 64)
    assert found is not None
    assert found.batch_id == _evidence().batch_id


def test_a_snapshot_from_no_batch_has_no_record(tmp_path):
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    assert store.lookup_by_snapshot("xingyao", "daily", "0" * 64, "0" * 64) is None


def test_a_snapshot_from_another_endpoint_has_no_record(tmp_path):
    """The trace-back is scoped: a same-key snapshot elsewhere is a different read."""
    store = BatchEvidenceStore(tmp_path)
    store.save(_evidence())
    assert (
        store.lookup_by_snapshot("xingyao", "backward_factor", "a" * 64, "b" * 64)
        is None
    )
