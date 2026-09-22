import hashlib
import json
from dataclasses import replace
from typing import Any

import pandas as pd
import pytest

from stock_quant.data_sources.base import FetchResult
from stock_quant.data_sources.raw_store import (
    RESERVED_TRANSPORT_ID,
    RawSnapshotEvidence,
    RawStore,
    _sha256_file,
)

#: Every store write must name its answering transport (design §2.3).  These
#: tests are about addressing, redaction and re-resolution, not provenance,
#: so they all use one fixed transport and let the interesting field vary.
TRANSPORT = "api.waditu.com"
_OMIT = object()


def _meta(**extra: Any) -> dict[str, Any]:
    return {"transport_id": TRANSPORT, **extra}


def test_raw_store_is_content_addressed_and_refuses_conflicting_overwrite(tmp_path):
    """A supplier revision cannot mutate the snapshot it supersedes."""
    store = RawStore(tmp_path)
    result = FetchResult(
        source="tushare",
        endpoint="daily",
        request_key="abc",
        frame=pd.DataFrame({"x": [1]}),
        metadata=_meta(),
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
        metadata=_meta(),
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
            metadata=_meta(
                request_parameters={"symbol": "000001.SZ", "token": "secret-value"},
                request_timestamp="2026-09-03T10:00:00+00:00",
                response_timestamp="2026-09-03T10:00:01+00:00",
                sdk_version="1.2.3",
            ),
        )
    )

    manifest = snapshot.manifest

    assert manifest["row_count"] == 1
    # The manifest records the observed dtypes, and pandas 3.0 infers its new
    # `str` dtype for a string column rather than `object`.
    assert manifest["schema"] == {"ts_code": "str", "close": "float64"}
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
            metadata=_meta(),
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
            metadata=_meta(request_timestamp="2026-09-03T10:00:00+00:00"),
        )
    )
    second = store.save(
        FetchResult(
            source="tushare",
            endpoint="daily",
            request_key="same-content",
            frame=pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]}),
            metadata=_meta(request_timestamp="2026-09-03T10:00:01+00:00"),
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
            metadata=_meta(),
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
            metadata=_meta(),
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
            metadata=_meta(),
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
        transport_id=TRANSPORT,
    )

    with pytest.raises(ValueError, match="not a mapping"):
        store.verify_evidence(evidence)


#: design §1.1: the label names the *answering* path, so the official host keeps
#: ``tushare.pro.*`` and only the relay's host wears the relay prefix.  Deriving
#: the label from the transport id instead would paint the official host as a
#: relay -- the very attribution error this layer exists to prevent.
LABEL_FOR_HOST = {
    "api.waditu.com": "tushare.pro",
    "jiaoch.top": "tushare_relay.jiaoch.top",
}


def _transport_result(
    frame: pd.DataFrame,
    *,
    transport_id: object = _OMIT,
    request_key: str = "rk-1",
    endpoint: str = "daily",
) -> FetchResult:
    """A fetch result whose provenance fields are set (or deliberately not)."""
    metadata: dict[str, Any] = {"source": "tushare", "sdk_version": "1.4.24"}
    if transport_id is not _OMIT:
        metadata["transport_id"] = transport_id
        # Only the hosts this fixture knows get a label.  The deliberately
        # invalid transport ids below (blank, reserved, ``../etc``) are refused
        # by ``save`` before the label could matter, so they get none.
        if transport_id in LABEL_FOR_HOST:
            metadata["supplier_endpoint"] = f"{LABEL_FOR_HOST[transport_id]}.{endpoint}"
    return FetchResult(
        source="tushare",
        endpoint=endpoint,
        request_key=request_key,
        frame=frame,
        metadata=metadata,
    )


def test_two_transports_with_identical_bytes_coexist(tmp_path):
    """Byte-identical snapshots from different transports are two snapshots."""
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    official = store.save(_transport_result(frame, transport_id="api.waditu.com"))
    relay = store.save(_transport_result(frame, transport_id="jiaoch.top"))

    assert official.sha256 == relay.sha256  # same bytes...
    assert official.path != relay.path  # ...two snapshots
    assert official.path.parent.parent.name == "api.waditu.com"
    assert relay.path.parent.parent.name == "jiaoch.top"
    # The labels differ, and neither is derived from the other's identity.
    assert official.manifest["supplier_endpoint"] == "tushare.pro.daily"
    assert relay.manifest["supplier_endpoint"] == "tushare_relay.jiaoch.top.daily"


def test_the_relay_label_survives_an_existing_official_snapshot(tmp_path):
    """The reuse branch must not silently keep the first transport's label.

    This is the bug the extra path layer exists to fix: the manifest is
    content-addressed, so without a transport layer the second save found the
    first snapshot and returned its ``tushare.pro.daily``.
    """
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    store.save(_transport_result(frame, transport_id="api.waditu.com"))
    relay = store.save(_transport_result(frame, transport_id="jiaoch.top"))
    assert relay.manifest["supplier_endpoint"] == "tushare_relay.jiaoch.top.daily"


def test_save_refuses_a_missing_transport_id(tmp_path):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError, match="transport_id"):
        store.save(_transport_result(pd.DataFrame({"a": [1]})))


@pytest.mark.parametrize("value", [None, "", "   ", RESERVED_TRANSPORT_ID])
def test_save_refuses_a_blank_or_reserved_transport_id(tmp_path, value):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(_transport_result(pd.DataFrame({"a": [1]}), transport_id=value))


def test_the_reserved_directory_never_appears_on_disk(tmp_path):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(
            _transport_result(
                pd.DataFrame({"a": [1]}), transport_id=RESERVED_TRANSPORT_ID
            )
        )
    assert not (tmp_path / "data" / "raw" / "tushare" / "daily" / "unknown").exists()


def test_a_transport_id_cannot_escape_the_store(tmp_path):
    store = RawStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(_transport_result(pd.DataFrame({"a": [1]}), transport_id="../etc"))


def test_evidence_round_trips_through_the_transport_path(tmp_path):
    store = RawStore(tmp_path)
    snapshot = store.save(
        _transport_result(pd.DataFrame({"a": [1]}), transport_id="jiaoch.top")
    )
    evidence = RawSnapshotEvidence.from_snapshot(snapshot)
    assert evidence.transport_id == "jiaoch.top"
    assert store.verify_evidence(evidence).sha256 == snapshot.sha256


def test_legacy_five_field_evidence_still_resolves_the_old_layout(tmp_path):
    """Bindings written before transport tracking must keep resolving.

    ``DATASET_BUILD_CONTRACT_VERSION`` stays 1, so already-published datasets
    are read, not rejected: their snapshots sit on the four-segment path and
    their manifests carry no ``transport_id``.
    """
    store = RawStore(tmp_path)
    snapshot = store.save(
        _transport_result(
            pd.DataFrame({"a": [1]}), transport_id="jiaoch.top", request_key="rk-legacy"
        )
    )

    legacy_dir = tmp_path / "data" / "raw" / "tushare" / "daily" / "rk-legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    target = legacy_dir / snapshot.sha256
    snapshot.path.rename(target)

    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest.pop("transport_id") == "jiaoch.top"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    evidence = RawSnapshotEvidence(
        source="tushare",
        endpoint="daily",
        request_key="rk-legacy",
        file_sha256=snapshot.sha256,
        manifest_sha256=_sha256_file(manifest_path),
    )
    assert evidence.transport_id is None
    assert store.verify_evidence(evidence).sha256 == snapshot.sha256
