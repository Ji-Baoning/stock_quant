import pandas as pd

from stock_quant.data_sources.base import FetchResult
from stock_quant.data_sources.raw_store import RawStore


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
