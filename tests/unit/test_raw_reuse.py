"""Reusing the stored answer to an identical request (design 2026-09-25 §2.2).

The raw tree *is* the coverage state: what a round may skip is derived from
the snapshots already on disk, re-verified at the moment of use, never from a
separate state file.  These tests pin the admission rules that make that safe.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from stock_quant.data_sources import raw_store
from stock_quant.data_sources.base import (
    DataRequest,
    FetchResult,
    request_key,
    request_metadata,
)
from stock_quant.data_sources.raw_store import RawSnapshotEvidence, RawStore, _sha256_file

#: One fixed answering transport: these tests are about which *answer* may be
#: reused, not about attribution, which ``test_raw_store.py`` already covers.
TRANSPORT = "jiaoch.top"
SUPPLIER_ENDPOINT = "tushare_relay.jiaoch.top.daily"
_START = date(2026, 9, 1)
_END = date(2026, 9, 30)


def _request(symbol: str = "000001.SZ") -> DataRequest:
    return DataRequest(
        "daily", (symbol,), _START, _END, {"adjustment": "unadjusted"}
    )


def _frame(closes: tuple[float, ...] = (10.0, 11.0)) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * len(closes),
            "trade_date": [f"2026-09-0{day}" for day in range(1, len(closes) + 1)],
            "close": list(closes),
        }
    )


def _save(
    store: RawStore,
    frame: pd.DataFrame,
    *,
    request: DataRequest | None = None,
    response_timestamp: str = "2026-09-30T01:00:00+00:00",
    source: str = "tushare",
    transport_id: str = TRANSPORT,
) -> None:
    """Persist one answer through the real metadata and path contracts."""
    request = request or _request()
    store.save(
        FetchResult(
            source=source,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(
                request,
                SUPPLIER_ENDPOINT,
                "1.4.24",
                transport_id=transport_id,
                request_timestamp="2026-09-30T00:59:00+00:00",
                response_timestamp=response_timestamp,
            ),
        )
    )


def _resolve(store: RawStore, request: DataRequest | None = None, **kwargs):
    request = request or _request()
    return store.resolve_reusable(
        "tushare", request.endpoint, request, **kwargs
    )


def test_resolve_reusable_returns_the_answer_saved_for_the_same_request(tmp_path):
    store = RawStore(tmp_path)
    _save(store, _frame())

    found = _resolve(store)

    assert found is not None
    snapshot, frame = found
    assert frame["close"].tolist() == [10.0, 11.0]
    assert snapshot.sha256 == json.loads(
        (snapshot.path / "manifest.json").read_text()
    )["file_sha256"]
    # The snapshot must be re-bindable as published evidence, exactly like a
    # freshly fetched one: the downstream build appends it unchanged.
    assert RawSnapshotEvidence.from_snapshot(snapshot).request_key == request_key(
        _request()
    )


def test_resolve_reusable_returns_none_for_an_unstored_request(tmp_path):
    assert _resolve(RawStore(tmp_path)) is None


def test_resolve_reusable_prefers_the_newest_response_over_an_older_one(tmp_path):
    """A supplier revision under one request key leaves two snapshots."""
    store = RawStore(tmp_path)
    _save(store, _frame((10.0, 11.0)), response_timestamp="2026-09-30T01:00:00+00:00")
    _save(store, _frame((10.0, 12.0)), response_timestamp="2026-09-30T05:00:00+00:00")

    found = _resolve(store)

    assert found is not None
    assert found[1]["close"].tolist() == [10.0, 12.0]


def test_resolve_reusable_refuses_when_the_newest_answer_was_tampered(tmp_path):
    """A tampered newest candidate fails closed instead of serving the older one.

    Falling back to the superseded observation would book a tamper signal as
    a successful reuse; asking the supplier again is the honest move.
    """
    store = RawStore(tmp_path)
    _save(store, _frame((10.0, 11.0)), response_timestamp="2026-09-30T01:00:00+00:00")
    _save(store, _frame((10.0, 12.0)), response_timestamp="2026-09-30T05:00:00+00:00")
    newest = max(
        (tmp_path / "data" / "raw" / "tushare" / "daily").glob(f"*/{request_key(_request())}/*"),
        key=lambda path: path.stat().st_mtime,
    )
    (newest / "data.parquet").write_bytes(b"tampered")

    assert _resolve(store) is None


def test_resolve_reusable_refuses_a_request_whose_parameters_differ(tmp_path):
    """A snapshot whose stored parameters name another request is not ours."""
    store = RawStore(tmp_path)
    request = _request()
    _save(store, _frame(), request=request)

    other = DataRequest(
        "daily", ("000001.SZ",), _START, date(2026, 10, 31), {"adjustment": "unadjusted"}
    )
    # Same request key directory, parameters that no longer agree with it.
    directory = next((tmp_path / "data" / "raw" / "tushare" / "daily").glob("*/" + request_key(request)))
    manifest_path = next(directory.glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    manifest["request_parameters"] = {
        "symbols": ["000001.SZ"],
        "start_date": _START.isoformat(),
        "end_date": other.end_date.isoformat(),
        "params": {"adjustment": "unadjusted"},
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    assert _resolve(store) is None


def test_resolve_reusable_ignores_a_manifest_without_request_parameters(tmp_path):
    """The lazy-arbitration channels write manifests with no parameters at all."""
    store = RawStore(tmp_path)
    request = _request()
    _save(store, _frame(), request=request)
    manifest_path = next(
        (
            tmp_path / "data" / "raw" / "tushare" / "daily"
        ).glob(f"*/{request_key(request)}/*/manifest.json")
    )
    manifest = json.loads(manifest_path.read_text())
    manifest["request_parameters"] = {}
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    assert _resolve(store) is None


def test_resolve_reusable_never_reuses_an_empty_answer_unless_allowed(tmp_path):
    """An empty response is an absence, not an answer (ADR-009)."""
    store = RawStore(tmp_path)
    _save(store, pd.DataFrame({"ts_code": [], "close": []}))

    assert _resolve(store) is None
    found = _resolve(store, allow_empty=True)
    assert found is not None
    assert found[1].empty


def test_resolve_reusable_round_trips_the_frame_values(tmp_path):
    store = RawStore(tmp_path)
    frame = pd.DataFrame(
        {"close": [10.5, 11.25], "volume": [100, 200]},
        index=pd.Index([7, 9], name="row"),
    )
    _save(store, frame)

    found = _resolve(store)

    assert found is not None
    pd.testing.assert_frame_equal(found[1], frame)


def test_resolve_reusable_rebuilds_the_observation_from_the_manifest(tmp_path):
    """The reused metadata is the stored one, so ``ingested_at`` stays honest."""
    store = RawStore(tmp_path)
    _save(store, _frame(), response_timestamp="2026-09-30T05:00:00+00:00")

    found = _resolve(store)

    assert found is not None
    metadata = found[0].manifest["metadata"]
    assert metadata["response_timestamp"] == "2026-09-30T05:00:00+00:00"
    assert metadata["transport_id"] == TRANSPORT
    assert metadata["supplier_endpoint"] == SUPPLIER_ENDPOINT


def test_resolve_reusable_ignores_the_legacy_four_segment_layout(tmp_path):
    """Snapshots predating transport tracking are never reused, only read."""
    store = RawStore(tmp_path)
    request = _request()
    _save(store, _frame())
    source = tmp_path / "data" / "raw" / "tushare" / "daily"
    snapshot_dir = next(source.glob(f"*/{request_key(request)}/*"))
    legacy_request_dir = source / request_key(request)
    legacy_request_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.rename(legacy_request_dir / snapshot_dir.name)
    manifest_path = legacy_request_dir / snapshot_dir.name / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("transport_id")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")

    assert _resolve(store) is None


#: The admission constant is the first gate: only the channels whose window is
#: narrowed by the incremental plan may replay a stored answer (design §2.3).
_ADMITTED = {
    ("tushare", "daily"): True,
    ("baostock", "daily"): True,
    ("akshare", "index_history"): True,
    ("tushare", "trade_cal"): False,
    ("tushare", "stock_basic"): False,
    ("tushare", "tdx_xdxr"): False,
    ("tushare", "adjust_factor"): False,
    ("akshare", "cninfo_corporate_actions"): False,
    ("akshare", "eastmoney_corporate_actions"): False,
    ("akshare", "rights_issue_corporate_actions"): False,
}


@pytest.mark.parametrize(("channel", "admitted"), sorted(_ADMITTED.items()))
def test_only_the_admitted_channels_may_reuse_a_stored_answer(
    tmp_path, channel, admitted
):
    source, endpoint = channel
    store = RawStore(tmp_path)
    request = DataRequest(endpoint, ("000001.SZ",), _START, _END, {})
    _save(store, _frame(), request=request, source=source)

    found = store.resolve_reusable(source, endpoint, request)

    assert (found is not None) is admitted


def test_the_admitted_channel_set_is_the_declared_one():
    """The constant is the policy; widening it is a decision, not a refactor."""
    assert raw_store.REUSABLE_CHANNELS == frozenset(
        channel for channel, admitted in _ADMITTED.items() if admitted
    )


def test_the_admitted_set_covers_exactly_the_reuse_call_sites():
    """Every admitted channel is one of the four wired call sites (design §2.4).

    A channel added here without a call site, or a call site wired to a
    channel that is not admitted, means the policy and the wiring drifted.
    """
    wired = {
        ("tushare", "daily"),  # _fetch_primary_stock, _deepen_head_anchors
        ("baostock", "daily"),  # _fetch_validation_daily
        ("akshare", "index_history"),  # _fetch_benchmarks
    }
    assert raw_store.REUSABLE_CHANNELS == frozenset(wired)
