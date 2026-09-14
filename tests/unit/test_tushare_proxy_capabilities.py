"""Unit tests for the controlled generic read surface of the proxy client."""

from __future__ import annotations

import pytest

from stock_quant.data_sources.base import ContractError, ServerError
from stock_quant.data_sources.tushare_proxy import (
    _FALLBACK_MIN_INTERVAL_SECONDS,
    TushareProxyClient,
)

BASE_URL = "https://proxy.example/tushare/pro"
ROOT = "https://proxy.example/tushare"


class FakeResponse:
    def __init__(self, body=None, status_code=200, text="", headers=None):
        self.status_code = status_code
        self._body = body
        self.text = text
        self.headers = dict(headers or {})

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


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _interface(**overrides):
    body = {
        "name": "suspend_d",
        "category": "股票",
        "cache_ttl": 21600,
        "provider": "tushare",
        "required": [],
        "required_any": [["ts_code"], ["trade_date"], ["start_date"]],
        "max_limit": 100000,
        "description": "每日停复牌信息",
        "enabled": True,
        "methods": ["GET"],
        "fallback_on_empty": True,
        "probe_supported": True,
        "probe_example": "/tushare/pro/suspend_d?__probe=1",
        "local_data_available": None,
        "local_latest_time": None,
        "requires_params": True,
    }
    body.update(overrides)
    return body


def _daily_body(rows=(("000001.SZ", "20260901"),)):
    return {
        "code": 0,
        "data": {
            "fields": [
                "ts_code",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "vol",
            ],
            "items": [
                [r[0], r[1], "1.0", "2.0", "0.9", "1.5", "0.9", "100.0"] for r in rows
            ],
        },
    }


def _client(outcomes, **kwargs):
    clock = kwargs.pop("clock", None) or FakeClock()
    sleeps = kwargs.pop("sleeps", None)
    sleeper = sleeps.append if sleeps is not None else (lambda _: None)
    session = FakeSession(outcomes)
    client = TushareProxyClient(
        BASE_URL,
        "key-123",
        session=session,
        sleeper=sleeper,
        clock=clock,
        **kwargs,
    )
    return client, session, clock


def test_capability_cache_hit_is_served_without_a_request():
    client, session, _ = _client([FakeResponse(body=_interface())])
    first = client.capability("suspend_d")
    second = client.capability("suspend_d")
    assert first == second
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == f"{ROOT}/capabilities/suspend_d"


def test_capability_cache_expires_after_its_own_ttl():
    outcomes = [
        FakeResponse(body=_interface(cache_ttl=300)),
        FakeResponse(body=_interface(cache_ttl=300)),
    ]
    client, session, clock = _client(outcomes)
    client.capability("suspend_d")
    clock.advance(299)
    client.capability("suspend_d")
    assert len(session.calls) == 1
    clock.advance(2)
    client.capability("suspend_d")
    assert len(session.calls) == 2


def test_capabilities_reads_the_catalog_table_and_caches_it():
    catalog = {
        "count": 2,
        "probe": {},
        "interfaces": [_interface(name="daily"), _interface(name="suspend_d")],
    }
    client, session, clock = _client([FakeResponse(body=catalog)])
    frame = client.capabilities()
    assert list(frame["name"]) == ["daily", "suspend_d"]
    clock.advance(21600 - 1)
    client.capabilities()
    assert len(session.calls) == 1
    assert session.calls[0]["url"] == f"{ROOT}/capabilities"


def test_upstreams_reads_the_probe_chain():
    chain = {
        "api_name": "suspend_d",
        "results": [
            {"name": "tickflow", "ok": False, "error": "unsupported_api", "rows": 0},
            {"name": "relay", "ok": False, "error": "fallback_error", "rows": 0},
        ],
    }
    client, session, _ = _client([FakeResponse(body=chain)])
    result = client.upstreams("suspend_d")
    assert [entry["name"] for entry in result["results"]] == ["tickflow", "relay"]
    assert session.calls[0]["url"] == f"{ROOT}/upstreams/probe/suspend_d"


def test_paged_read_still_filters_to_the_requested_range():
    rows = (("000001.SZ", "20191231"), ("000001.SZ", "20200102"))
    client, _, _ = _client([FakeResponse(body=_daily_body(rows))])
    frame = client.daily(
        ts_code="000001.SZ", start_date="20200101", end_date="20201231"
    )
    assert list(frame["trade_date"]) == ["20200102"]


def test_range_filter_is_skipped_when_the_frame_has_no_trade_date():
    body = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "com_name"],
            "items": [["600000.SH", "浦发银行"]],
        },
    }
    client, _, _ = _client([FakeResponse(body=body)])
    frame = client.query(
        "stock_company",
        verify_capability="none",
        ts_code="600000.SH",
        start_date="20200101",
        end_date="20201231",
    )
    assert list(frame["com_name"]) == ["浦发银行"]


def test_disabled_interface_fails_fast_without_a_data_request():
    client, session, _ = _client([FakeResponse(body=_interface(enabled=False))])
    with pytest.raises(ContractError):
        client.query("suspend_d", ts_code="000333.SZ")
    assert [call["url"] for call in session.calls] == [f"{ROOT}/capabilities/suspend_d"]


def test_required_any_must_be_satisfied():
    body = _interface(required_any=[["ts_code"], ["trade_date"]])
    client, session, _ = _client([FakeResponse(body=body)])
    with pytest.raises(ContractError):
        client.query("suspend_d", foo="bar")
    assert [call["url"] for call in session.calls] == [f"{ROOT}/capabilities/suspend_d"]


def test_required_must_all_be_satisfied():
    body = _interface(required=["ts_code"], required_any=[])
    client, session, _ = _client([FakeResponse(body=body)])
    with pytest.raises(ContractError):
        client.query("suspend_d", start_date="20200101")
    assert [call["url"] for call in session.calls] == [f"{ROOT}/capabilities/suspend_d"]


def test_unregistered_interface_surfaces_contract_error():
    unknown = FakeResponse(status_code=404, text='{"ok":false,"error":"unknown_api"}')
    client, _, _ = _client([unknown])
    with pytest.raises(ContractError):
        client.query("no_such_endpoint_xyz")


def test_preflight_transient_failure_fails_open_and_records_it():
    served = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "trade_date"],
            "items": [["000333.SZ", "20160518"]],
        },
    }
    client, session, _ = _client(
        [FakeResponse(status_code=503, text="busy"), FakeResponse(body=served)],
        max_retries=0,
    )
    frame = client.query("suspend_d", ts_code="000333.SZ")
    assert len(frame) == 1
    assert client.last_query_metadata["capability_checked"] is False
    assert session.calls[-1]["url"] == f"{BASE_URL}/suspend_d"


def test_named_endpoints_skip_preflight():
    client, session, _ = _client([FakeResponse(body=_daily_body())])
    client.daily(ts_code="000001.SZ", start_date="20260901", end_date="20260912")
    assert [call["url"] for call in session.calls] == [f"{BASE_URL}/daily"]


def test_query_on_a_named_endpoint_also_skips_preflight():
    client, session, _ = _client([FakeResponse(body=_daily_body())])
    client.query(
        "daily", ts_code="000001.SZ", start_date="20260901", end_date="20260912"
    )
    assert [call["url"] for call in session.calls] == [f"{BASE_URL}/daily"]


def test_verify_capability_none_records_unchecked():
    served = {"code": 0, "data": {"fields": ["ts_code"], "items": [["000333.SZ"]]}}
    client, session, _ = _client([FakeResponse(body=served)])
    client.query("suspend_d", verify_capability="none", ts_code="000333.SZ")
    assert [call["url"] for call in session.calls] == [f"{BASE_URL}/suspend_d"]
    assert client.last_query_metadata["capability_checked"] is None


def test_write_only_interfaces_are_never_read():
    body = _interface(name="p_save", methods=["POST"])
    client, session, _ = _client([FakeResponse(body=body)])
    with pytest.raises(ContractError):
        client.query("p_save", ts_code="000001.SZ")
    assert all("/pro/p_save" not in call["url"] for call in session.calls)


def test_capability_checked_is_true_on_a_passed_live_preflight():
    served = {"code": 0, "data": {"fields": ["ts_code"], "items": [["000333.SZ"]]}}
    client, session, _ = _client(
        [FakeResponse(body=_interface()), FakeResponse(body=served)]
    )
    client.query("suspend_d", ts_code="000333.SZ")
    assert client.last_query_metadata["capability_checked"] is True
    assert [call["url"] for call in session.calls] == [
        f"{ROOT}/capabilities/suspend_d",
        f"{BASE_URL}/suspend_d",
    ]


def test_query_records_request_ids_and_cache_layers():
    headers = {"x-request-id": "abc-123", "x-cache": "HIT", "x-cache-layer": "redis"}
    client, _, _ = _client([FakeResponse(body=_daily_body(), headers=headers)])
    client.query(
        "daily", ts_code="000001.SZ", start_date="20260901", end_date="20260912"
    )
    metadata = client.last_query_metadata
    assert metadata["endpoint"] == "daily"
    assert metadata["request_ids"] == ["abc-123"]
    assert metadata["cache"] == ["HIT/redis"]
    assert metadata["windows"] == [("20260901", "20260912")]
    assert metadata["rows"] == 1


def test_query_records_one_entry_per_outbound_window():
    headers = {"x-request-id": "abc-123", "x-cache": "MISS"}
    client, _, _ = _client([FakeResponse(body=_daily_body(), headers=headers)] * 3)
    client.query(
        "daily", ts_code="000001.SZ", start_date="20150101", end_date="20260828"
    )
    metadata = client.last_query_metadata
    assert metadata["windows"] == [
        ("20150101", "20191231"),
        ("20200101", "20241231"),
        ("20250101", "20260828"),
    ]
    assert metadata["request_ids"] == ["abc-123", "abc-123", "abc-123"]
    assert metadata["cache"] == ["MISS", "MISS", "MISS"]


def test_named_reads_never_write_query_metadata():
    client, _, _ = _client([FakeResponse(body=_daily_body())] * 2)
    assert client.last_query_metadata is None
    client.daily(ts_code="000001.SZ", start_date="20260901", end_date="20260912")
    assert client.last_query_metadata is None
    client.index_daily(ts_code="000300.SH", start_date="20260901", end_date="20260912")
    assert client.last_query_metadata is None


def test_failed_query_leaves_the_previous_metadata_untouched():
    headers = {"x-request-id": "req-1", "x-cache": "MISS"}
    client, _, _ = _client(
        [
            FakeResponse(
                body=_daily_body(), headers={"x-request-id": "ok", "x-cache": "HIT"}
            ),
            FakeResponse(status_code=500, text="oops", headers=headers),
        ],
        max_retries=0,
    )
    client.query(
        "daily", ts_code="000001.SZ", start_date="20260901", end_date="20260912"
    )
    good = client.last_query_metadata
    with pytest.raises(ServerError):
        client.query("suspend_d", verify_capability="none", ts_code="000333.SZ")
    assert client.last_query_metadata is good


# --------------------------------------------------------------------------- #
# Throttling: the server's rate-limit headers, never the catalog declaration.  #
# --------------------------------------------------------------------------- #


def _read(client):
    return client.query(
        "daily", ts_code="000001.SZ", start_date="20260901", end_date="20260912"
    )


def test_low_remaining_throttles_the_next_request():
    sleeps = []
    outcomes = [
        FakeResponse(body=_daily_body(), headers={"x-ratelimit-ip-remaining": "3"}),
        FakeResponse(body=_daily_body()),
    ]
    client, _, _ = _client(outcomes, sleeps=sleeps)
    _read(client)
    _read(client)
    assert sleeps == [_FALLBACK_MIN_INTERVAL_SECONDS]


def test_high_remaining_does_not_throttle():
    sleeps = []
    outcomes = [
        FakeResponse(body=_daily_body(), headers={"x-ratelimit-ip-remaining": "199"}),
        FakeResponse(body=_daily_body()),
    ]
    client, _, _ = _client(outcomes, sleeps=sleeps)
    _read(client)
    _read(client)
    assert sleeps == []


def test_missing_headers_fall_back_once_a_limit_has_been_seen():
    sleeps = []
    outcomes = [
        FakeResponse(body=_daily_body(), headers={"x-ratelimit-ip-remaining": "199"}),
        FakeResponse(body=_daily_body()),  # no headers at all
        FakeResponse(body=_daily_body()),
    ]
    client, _, _ = _client(outcomes, sleeps=sleeps)
    _read(client)
    _read(client)
    _read(client)
    assert sleeps == [_FALLBACK_MIN_INTERVAL_SECONDS]


def test_retry_after_takes_priority_over_remaining():
    sleeps = []
    outcomes = [
        FakeResponse(
            body=_daily_body(),
            headers={"Retry-After": "5", "x-ratelimit-ip-remaining": "199"},
        ),
        FakeResponse(body=_daily_body()),
    ]
    client, _, _ = _client(outcomes, sleeps=sleeps)
    _read(client)
    _read(client)
    assert sleeps == [5.0]


def test_requests_without_any_rate_limit_header_are_never_throttled():
    sleeps = []
    client, _, _ = _client([FakeResponse(body=_daily_body())] * 3, sleeps=sleeps)
    _read(client)
    _read(client)
    _read(client)
    assert sleeps == []


# --------------------------------------------------------------------------- #
# Provenance: what named reads and transient attempts leave (or do not leave)  #
# in the audit trail.                                                          #
# --------------------------------------------------------------------------- #


def test_stock_basic_never_writes_query_metadata():
    payload = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "name"],
            "items": [["000001.SZ", "平安银行"]],
        },
    }
    client, session, _ = _client([FakeResponse(body=payload)])
    client.stock_basic(fields="ts_code,name")
    assert client.last_query_metadata is None
    assert session.calls[0]["url"] == f"{BASE_URL}/stock_basic"


def test_a_transient_attempt_still_leaves_its_request_id_in_the_trail():
    sleeps: list[float] = []
    client, session, _ = _client(
        [
            FakeResponse(
                body={"ok": False, "error": "upstream_pool_exhausted"},
                headers={"x-request-id": "first", "x-cache": "MISS"},
            ),
            FakeResponse(body=_daily_body(), headers={"x-request-id": "second"}),
        ],
        sleeps=sleeps,
    )
    client.query(
        "daily", ts_code="000001.SZ", start_date="20260901", end_date="20260912"
    )
    metadata = client.last_query_metadata
    assert metadata["request_ids"] == ["first", "second"]
