"""Unit tests for the Tushare-compatible GET proxy transport."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_model.normalize import normalize_daily
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
    ServerError,
)
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.data_sources.tushare_proxy import (
    TushareProxyClient,
    _date_windows,
)

BASE_URL = "https://proxy.example/tushare/pro"


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records GETs and answers from a queue of responses/exceptions."""

    def __init__(self, outcomes):
        self.headers: dict[str, str] = {}
        self.calls: list[dict] = []
        self._outcomes = list(outcomes)

    def get(self, url, params=None, timeout=None):
        self.calls.append(
            {"url": url, "params": dict(params or {}), "timeout": timeout}
        )
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _payload(items, fields):
    return {"code": 0, "data": {"fields": fields, "items": items}}


def _daily_payload(rows):
    return _payload(
        [[r[0], r[1], "1.0", "2.0", "0.9", "1.5", "0.9", "100.0"] for r in rows],
        ["ts_code", "trade_date", "open", "high", "low", "close",
         "pre_close", "vol"],
    )


def _ok(rows):
    return FakeResponse(body=_daily_payload(rows))


def _client(outcomes, **kwargs):
    sleeper = kwargs.pop("sleeper", lambda _: None)
    session = kwargs.pop("session", None) or FakeSession(outcomes)
    client = TushareProxyClient(
        BASE_URL,
        "key-123",
        session=session,
        sleeper=sleeper,
        **kwargs,
    )
    return client, session


def test_daily_passes_simple_params_and_key_header():
    frame = _daily_payload([("000001.SZ", "20260901")])
    client, session = _client([FakeResponse(body=frame)])
    result = client.daily(ts_code="000001.SZ", start_date="20260901",
                          end_date="20260912")
    assert list(result["trade_date"]) == ["20260901"]
    assert session.calls[0]["url"] == f"{BASE_URL}/daily"
    assert session.calls[0]["params"] == {
        "ts_code": "000001.SZ",
        "start_date": "20260901",
        "end_date": "20260912",
    }
    assert session.headers["X-API-Key"] == "key-123"


def test_daily_splits_long_ranges_into_bounded_windows():
    windows = _date_windows("20150101", "20260828")
    # One in-range row per window; the queued payloads align with the tiling.
    rows = [("000001.SZ", f"{window[0][:4]}0601") for window in windows]
    client, session = _client([_ok([row]) for row in rows])
    result = client.daily(ts_code="000001.SZ", start_date="20150101",
                          end_date="20260828")
    assert len(session.calls) == len(windows)
    assert len(result) == len(rows)
    # Windows tile the range without gaps or overlaps.
    assert session.calls[0]["params"]["start_date"] == "20150101"
    assert session.calls[-1]["params"]["end_date"] == "20260828"
    for earlier, later in zip(session.calls, session.calls[1:]):
        assert earlier["params"]["end_date"] < later["params"]["start_date"]


def test_date_windows_respect_five_year_bound():
    windows = _date_windows("20150101", "20260828")
    assert windows == [
        ("20150101", "20191231"),
        ("20200101", "20241231"),
        ("20250101", "20260828"),
    ]
    assert _date_windows("20200101", "20201231") == [("20200101", "20201231")]


def test_pool_exhaustion_retries_with_backoff_then_succeeds():
    sleeps: list[float] = []
    client, session = _client(
        [
            FakeResponse(body={"ok": False, "error": "upstream_pool_exhausted"}),
            _ok([("000001.SZ", "20260901")]),
        ],
        sleeper=sleeps.append,
    )
    result = client.daily(ts_code="000001.SZ", start_date="20260901",
                          end_date="20260912")
    assert len(result) == 1
    assert len(session.calls) == 2
    assert sleeps == [2]


def test_transient_failures_exhaust_attempts_then_raise_server_error():
    sleeps: list[float] = []
    client, session = _client(
        [FakeResponse(body={"ok": False, "error": "upstream_pool_exhausted"})]
        * 5,
        max_retries=4,
        sleeper=sleeps.append,
    )
    with pytest.raises(ServerError):
        client.daily(ts_code="000001.SZ", start_date="20260901",
                     end_date="20260912")
    assert len(session.calls) == 5
    assert sleeps == [2, 6, 12, 20]


def test_auth_failure_raises_without_retry():
    client, session = _client(
        [FakeResponse(body={"code": -2001, "msg": "token does not exist"})]
    )
    with pytest.raises(AuthenticationError):
        client.daily(ts_code="000001.SZ", start_date="20260901",
                     end_date="20260912")
    assert len(session.calls) == 1


def test_contract_failure_raises_without_retry():
    client, session = _client(
        [FakeResponse(body={"code": -1, "msg": "bad parameters"})]
    )
    with pytest.raises(ContractError):
        client.query("daily", ts_code="000001.SZ")
    assert len(session.calls) == 1


def test_http_500_and_non_json_body_are_transient():
    client, session = _client(
        [
            FakeResponse(status_code=502, text="bad gateway"),
            FakeResponse(text="<html>gateway</html>"),
            _ok([("000001.SZ", "20260901")]),
        ],
    )
    result = client.daily(ts_code="000001.SZ", start_date="20260901",
                          end_date="20260912")
    assert len(result) == 1
    assert len(session.calls) == 3


def test_stock_basic_uses_generous_read_timeout():
    payload = _payload(
        [["000001.SZ", "平安银行", "19910403"]],
        ["ts_code", "name", "list_date"],
    )
    client, session = _client([FakeResponse(body=payload)], timeout_seconds=30)
    result = client.stock_basic(fields="ts_code,name,list_date")
    assert list(result["ts_code"]) == ["000001.SZ"]
    assert session.calls[0]["timeout"] == (10, 90)


def test_paged_result_is_filtered_to_the_requested_range():
    rows = [("000001.SZ", "20191231"), ("000001.SZ", "20200102")]
    client, _ = _client([_ok(rows)])
    result = client.daily(ts_code="000001.SZ", start_date="20200101",
                          end_date="20201231")
    assert list(result["trade_date"]) == ["20200102"]


def test_from_env_requires_both_variables(monkeypatch):
    monkeypatch.delenv("TUSHARE_PROXY_URL", raising=False)
    monkeypatch.delenv("TUSHARE_PROXY_KEY", raising=False)
    assert TushareProxyClient.from_env() is None
    monkeypatch.setenv("TUSHARE_PROXY_URL", BASE_URL)
    assert TushareProxyClient.from_env() is None
    monkeypatch.setenv("TUSHARE_PROXY_KEY", "key-123")
    client = TushareProxyClient.from_env()
    assert client is not None and client.host == "proxy.example"


class ProxyStub(TushareProxyClient):
    """A client-shaped stub: subclasses pass the transport isinstance check."""

    sdk_version = "tushare_proxy-1.0"

    def __init__(self, frame):
        self._frame = frame

    def daily(self, ts_code=None, start_date=None, end_date=None):
        return self._frame


def _daily_frame(rows):
    """The DataFrame a successful daily read parses into."""
    fields = ["ts_code", "trade_date", "open", "high", "low", "close",
              "pre_close", "vol"]
    items = [
        [r[0], r[1], "1.0", "2.0", "0.9", "1.5", "0.9", "100.0"] for r in rows
    ]
    return pd.DataFrame(items, columns=fields)


def _proxy_client(frame):
    return TushareProxyClient(
        BASE_URL,
        "key-123",
        session=FakeSession([FakeResponse(body=_daily_payload(frame))]),
        sleeper=lambda _: None,
    )


def test_tushare_source_labels_proxy_transport():
    source = TushareSource(
        SourceConfig(), client=_proxy_client([("600000.SH", "20260901")])
    )
    request = DataRequest(
        "daily", ("600000.SH",), date(2026, 9, 1), date(2026, 9, 12), {}
    )
    result = source.fetch(request)
    assert result.metadata["supplier_endpoint"] == "tushare_proxy.daily"
    assert result.metadata["sdk_version"].startswith("tushare_proxy")


def test_tushare_source_env_selects_proxy_without_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_PROXY_URL", BASE_URL)
    monkeypatch.setenv("TUSHARE_PROXY_KEY", "key-123")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    stub = ProxyStub(_daily_frame([("600000.SH", "20260901")]))
    monkeypatch.setattr(
        TushareProxyClient, "from_env", classmethod(lambda cls, **kw: stub)
    )
    source = TushareSource(SourceConfig())
    request = DataRequest(
        "daily", ("600000.SH",), date(2026, 9, 1), date(2026, 9, 12), {}
    )
    assert source.fetch(request).metadata["supplier_endpoint"] == (
        "tushare_proxy.daily"
    )


def test_tushare_source_keeps_official_labels_for_sdk_client():
    class FakeSDK:
        __version__ = "1.4.29"

        def daily(self, ts_code=None, start_date=None, end_date=None):
            return _daily_frame([("600000.SH", "20260901")])

    source = TushareSource(SourceConfig(), client=FakeSDK())
    request = DataRequest(
        "daily", ("600000.SH",), date(2026, 9, 1), date(2026, 9, 12), {}
    )
    result = source.fetch(request)
    assert result.metadata["supplier_endpoint"] == "tushare.pro.daily"
    assert result.metadata["sdk_version"] == "1.4.29"


def test_tushare_source_fetches_index_daily():
    frame = _payload(
        [["000300.SH", "20260911", "4510.15"]],
        ["ts_code", "trade_date", "close"],
    )
    client, session = _client([FakeResponse(body=frame)])
    source = TushareSource(SourceConfig(), client=client)
    request = DataRequest(
        "index_daily", ("000300.SH",), date(2026, 9, 1), date(2026, 9, 12), {}
    )
    result = source.fetch(request)
    assert result.metadata["supplier_endpoint"] == "tushare_proxy.index_daily"
    assert len(result.frame) == 1
    assert session.calls[0]["url"] == f"{BASE_URL}/index_daily"


def test_normalize_daily_accepts_proxy_label_with_tushare_units():
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260901"],
            "open": [1.0],
            "high": [2.0],
            "low": [0.9],
            "close": [1.5],
            "vol": [1263029],
            "amount": [973220.5],
        }
    )
    clean = normalize_daily(frame, "tushare_proxy", pd.Timestamp.now(tz="UTC"))
    assert len(clean.valid) == 1
    assert clean.valid.iloc[0]["volume"] == 126302900
    assert clean.valid.iloc[0]["amount"] == 973220500.0
