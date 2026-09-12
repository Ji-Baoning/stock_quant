"""The evidence chain must prove which transport answered (design §2.2/§2.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from stock_quant.data_pipeline import (
    DATASET_BUILD_CONTRACT_VERSION,
    _raw_snapshot_evidence_rows,
)
from stock_quant.data_sources.base import FetchResult
from stock_quant.data_sources.raw_store import (
    RawSnapshotEvidence,
    RawStore,
    _sha256_file,
)
from stock_quant.research.acceptance.models import RawSnapshotBinding

#: What each host's snapshot must be labelled, per design §1.1.  The fixture
#: looks the label up rather than deriving it from the transport id: the two
#: are independent, and conflating them is how the official host would end up
#: wearing a relay label.
LABEL_FOR_HOST = {
    "api.waditu.com": "tushare.pro.daily",
    "jiaoch.top": "tushare_relay.jiaoch.top.daily",
}


def _result_for(frame, host, *, request_key="rk-1"):
    """A fetch result for one host, labelled the way the adapter would."""
    return FetchResult(
        source="tushare",
        endpoint="daily",
        request_key=request_key,
        frame=frame,
        metadata={
            "source": "tushare",
            "sdk_version": "1.4.24",
            "supplier_endpoint": LABEL_FOR_HOST[host],
            "transport_id": host,
        },
    )


def _resolve_label(store_root: Path, row: dict) -> str:
    """Walk build_config row -> validated binding -> manifest -> transport label."""
    binding = RawSnapshotBinding.model_validate(row)
    snapshot = RawStore(store_root).verify_evidence(
        RawSnapshotEvidence(**binding.model_dump())
    )
    return str(snapshot.manifest["supplier_endpoint"])


def test_evidence_rows_carry_every_transport_and_the_labels_resolve(tmp_path):
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    # Identical bytes, two transports, one request key: the case the whole
    # design exists for.  Each keeps its own label -- the relay gets the relay
    # label, and the official path stays ``tushare.pro.*``.
    snapshots = [
        store.save(_result_for(frame, "api.waditu.com")),
        store.save(_result_for(frame, "jiaoch.top")),
    ]
    rows = _raw_snapshot_evidence_rows(snapshots)
    assert len(rows) == 2
    assert {row["transport_id"] for row in rows} == {"api.waditu.com", "jiaoch.top"}
    assert all(row["request_key"] == "rk-1" for row in rows)

    labels = {row["transport_id"]: _resolve_label(tmp_path, row) for row in rows}
    assert labels == {
        "api.waditu.com": "tushare.pro.daily",
        "jiaoch.top": "tushare_relay.jiaoch.top.daily",
    }


def test_request_key_stays_a_pure_idempotency_key(tmp_path):
    # Transport identity must not leak into the request key, or "the same
    # request" would stop meaning one thing.
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    official = store.save(_result_for(frame, "api.waditu.com"))
    relay = store.save(_result_for(frame, "jiaoch.top"))
    assert official.manifest["request_key"] == relay.manifest["request_key"] == "rk-1"
    assert official.sha256 == relay.sha256


def test_a_five_field_row_resolves_the_legacy_layout(tmp_path):
    """A pre-transport build_config row must keep resolving to its snapshot."""
    store = RawStore(tmp_path)
    frame = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    snapshot = store.save(_result_for(frame, "jiaoch.top"))

    legacy_dir = tmp_path / "data" / "raw" / "tushare" / "daily" / "rk-1"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    target = legacy_dir / snapshot.sha256
    snapshot.path.rename(target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("transport_id")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    row = {
        "source": "tushare",
        "endpoint": "daily",
        "request_key": "rk-1",
        "file_sha256": snapshot.sha256,
        "manifest_sha256": _sha256_file(manifest_path),
    }
    assert len(row) == 5
    assert _resolve_label(tmp_path, row) == "tushare_relay.jiaoch.top.daily"


def test_build_config_keeps_pipeline_contract_version_one():
    # Bumping it would fail acceptance for every already-published dataset:
    # checks.py compares this field for equality.
    assert DATASET_BUILD_CONTRACT_VERSION == 1
