import hashlib
import json
from dataclasses import replace

import pandas as pd
import pytest

from stock_quant.data_sources.base import FetchResult
from stock_quant.data_sources.raw_store import RawSnapshotEvidence, RawStore


def test_raw_store_is_content_addressed_and_refuses_conflicting_overwrite(tmp_path):
    """A supplier revision cannot mutate the snapshot it supersedes."""
    store = RawStore(tmp_path)
    result = FetchResult(
        source="tushare",
        endpoint="daily",
        request_key="abc",
        frame=pd.DataFrame({"x": [1]}),
        metadata={},
    )

    first = store.save(result)
    second = store.save(result)

    assert first.path == second.path
    assert first.sha256 == second.sha256

    changed = FetchResult(
        source="tushare",
        endpoint="daily",
        request_key="abc",
        frame=pd.DataFrame({"x": [2]}),
        metadata={},
    )
    revision = store.save(changed)

    assert revision.path != first.path
    assert pd.read_parquet(first.path / "data.parquet").x.tolist() == [1]


def test_raw_store_writes_a_redacted_audit_manifest(tmp_path):
    """Raw persistence must not allow an API token into the audit trail."""
    store = RawStore(tmp_path)
    snapshot = store.save(
        FetchResult(
            source="tushare",
            endpoint="daily",
            request_key="request-1",
            frame=pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]}),
            metadata={
                "request_parameters": {"symbol": "000001.SZ", "token": "secret-value"},
                "request_timestamp": "2026-09-03T10:00:00+00:00",
                "response_timestamp": "2026-09-03T10:00:01+00:00",
                "sdk_version": "1.2.3",
            },
        )
    )

    manifest = snapshot.manifest

    assert manifest["row_count"] == 1
    assert manifest["schema"] == {"ts_code": "object", "close": "float64"}
    assert manifest["redacted"] is True
    assert manifest["request_parameters"]["token"] == "[REDACTED]"
    assert "secret-value" not in (snapshot.path / "manifest.json").read_text()


def test_raw_store_records_independent_response_and_file_hashes(tmp_path):
    """The supplier response digest must not be a second name for the file digest."""
    snapshot = RawStore(tmp_path).save(
        FetchResult(
            source="akshare",
            endpoint="index_history",
            request_key="hashes",
            frame=pd.DataFrame({"日期": ["2020-01-01"], "收盘": [4010.0]}),
            metadata={},
        )
    )

    assert snapshot.manifest["response_sha256"] != snapshot.manifest["file_sha256"]


def test_raw_store_reuses_the_manifest_persisted_with_an_identical_snapshot(tmp_path):
    """A duplicate save must report the immutable manifest that is actually on disk."""
    store = RawStore(tmp_path)
    first = store.save(
        FetchResult(
            source="tushare",
            endpoint="daily",
            request_key="same-content",
            frame=pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]}),
            metadata={"request_timestamp": "2026-09-03T10:00:00+00:00"},
        )
    )
    second = store.save(
        FetchResult(
            source="tushare",
            endpoint="daily",
            request_key="same-content",
            frame=pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]}),
            metadata={"request_timestamp": "2026-09-03T10:00:01+00:00"},
        )
    )

    on_disk = json.loads((second.path / "manifest.json").read_text())
    assert second.path == first.path
    assert second.manifest == on_disk
    assert second.manifest["request_timestamp"] == "2026-09-03T10:00:00+00:00"


def test_verify_evidence_returns_exact_snapshot(tmp_path):
    """A saved snapshot is re-resolvable from its sanitized evidence alone."""
    store = RawStore(tmp_path)
    saved = store.save(
        FetchResult(
            source="tushare",
            endpoint="daily",
            request_key="abc",
            frame=pd.DataFrame({"x": [1]}),
            metadata={},
        )
    )
    evidence = RawSnapshotEvidence.from_snapshot(saved)

    found = store.verify_evidence(evidence)

    assert found.path == saved.path
    assert found.sha256 == saved.sha256
    assert found.manifest["file_sha256"] == saved.sha256


def test_verify_evidence_rejects_invalid_path_component(tmp_path):
    """Evidence identifiers can never escape the raw-store tree."""
    store = RawStore(tmp_path)
    saved = store.save(
        FetchResult(
            source="tushare",
            endpoint="daily",
            request_key="safe",
            frame=pd.DataFrame({"x": [1]}),
            metadata={},
        )
    )
    evidence = RawSnapshotEvidence.from_snapshot(saved)
    escaped = replace(evidence, request_key="../escape")

    with pytest.raises(ValueError, match="request key"):
        store.verify_evidence(escaped)


def test_verify_evidence_rejects_valid_json_non_mapping_manifest(tmp_path):
    """A non-object manifest fails closed, never with AttributeError.

    The manifest hash in the evidence matches the tampered ``[]`` bytes, so
    only the mapping guard can reject it: ``json.loads`` happily returns a
    list and the field comparison would blow up with ``AttributeError``.
    """
    store = RawStore(tmp_path)
    saved = store.save(
        FetchResult(
            source="tushare",
            endpoint="daily",
            request_key="non-mapping",
            frame=pd.DataFrame({"x": [1]}),
            metadata={},
        )
    )
    manifest_path = saved.path / "manifest.json"
    manifest_path.write_text("[]", encoding="utf-8")
    evidence = RawSnapshotEvidence(
        source="tushare",
        endpoint="daily",
        request_key="non-mapping",
        file_sha256=saved.sha256,
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(ValueError, match="not a mapping"):
        store.verify_evidence(evidence)
