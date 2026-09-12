"""Unit tests for the controlled generic read surface of the proxy client."""

from __future__ import annotations

from stock_quant.data_sources.tushare_proxy import TushareProxyClient

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
