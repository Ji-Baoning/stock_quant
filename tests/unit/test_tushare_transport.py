"""Unit tests for tushare transport selection and provenance (no network)."""

from __future__ import annotations

import logging
from datetime import date
from functools import partial

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    AuthenticationError,
    DataRequest,
    host_of,
)
from stock_quant.data_sources.tushare import TushareSource, injected_transport
from stock_quant.data_sources.tushare_proxy import TushareProxyClient
from stock_quant.data_sources.tushare_relay import TushareRelayClient
from stock_quant.data_sources.tushare_transport import (
    OFFICIAL,
    OFFICIAL_HOST,
    OFFICIAL_PUBLISH_ENV,
    PROXY,
    RELAY,
    TRANSPORT_ENV,
    build_transport,
    resolve_transport,
)

RELAY_URL = "https://jiaoch.example/"
PROXY_URL = "https://proxy.example/tushare/pro"

RELAY_ENV = {"TUSHARE_RELAY_URL": RELAY_URL, "TUSHARE_RELAY_KEY": "relay-key"}
PROXY_ENV = {"TUSHARE_PROXY_URL": PROXY_URL, "TUSHARE_PROXY_KEY": "proxy-key"}
OFFICIAL_ENV = {"TUSHARE_TOKEN": "official-token"}
BREAK_GLASS_ENV = dict(OFFICIAL_ENV, **{OFFICIAL_PUBLISH_ENV: "1"})


class FakeApi:
    """A stand-in for the official ``DataApi`` returned by ``pro_api``.

    It reproduces the one behaviour that makes §1.1 work: the real ``DataApi``
    defines ``__getattr__`` returning ``partial(self.query, name)``, so a
    session answers named calls exactly like the official one.
    """

    def __init__(self, base_url: str) -> None:
        setattr(self, "_DataApi__http_url", base_url)

    def query(self, endpoint, **params):
        return pd.DataFrame({"endpoint": [endpoint]})

    def __getattr__(self, name):
        return partial(self.query, name)


class FakeSdk:
    __version__ = "fake-sdk-9.9"

    def __init__(self, base_url: str = f"http://{OFFICIAL_HOST}/dataapi") -> None:
        self.base_url = base_url
        self.tokens: list[str] = []
        self.timeouts: list[int] = []

    def pro_api(self, token="", timeout=30):
        self.tokens.append(token)
        self.timeouts.append(timeout)
        return FakeApi(self.base_url)


def _config() -> SourceConfig:
    return SourceConfig()


def test_host_of_handles_scheme_port_and_path():
    assert host_of("http://api.waditu.com/dataapi") == "api.waditu.com"
    assert host_of("https://jiaoch.example/") == "jiaoch.example"
    assert host_of("https://proxy.example/tushare/pro") == "proxy.example"
    assert host_of("") == ""


def test_relay_transport_reports_its_own_host_and_endpoint():
    transport = resolve_transport(
        _config(), environ=dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY}), sdk=FakeSdk()
    )
    assert transport.kind == RELAY
    assert transport.host == "jiaoch.example"
    assert transport.transport_id == "jiaoch.example"
    assert transport.supplier_endpoint("daily") == "tushare_relay.jiaoch.example.daily"


def test_relay_credentials_come_from_the_passed_environ(monkeypatch):
    # from_env() reads the process environment; the pipeline path must not, or
    # ``environ=`` would be decorative and this test impossible.
    monkeypatch.setenv("TUSHARE_RELAY_URL", "https://wrong.example/")
    monkeypatch.setenv("TUSHARE_RELAY_KEY", "wrong-key")
    transport = build_transport(RELAY, _config(), environ=RELAY_ENV, sdk=FakeSdk())
    assert transport.host == "jiaoch.example"


def test_official_transport_keeps_the_tushare_pro_label():
    transport = build_transport(
        OFFICIAL, _config(), environ=OFFICIAL_ENV, sdk=FakeSdk()
    )
    assert transport.kind == OFFICIAL
    assert transport.host == OFFICIAL_HOST
    assert transport.transport_id == OFFICIAL_HOST
    assert transport.supplier_endpoint("daily") == "tushare.pro.daily"
    assert transport.transport_id != "unknown"


def test_official_transport_refuses_to_label_a_rewritten_base_url():
    # The whole point: a session whose base URL is not the official host can
    # never be recorded as tushare.pro.*, because the label is derived from
    # the URL actually reached and not from the client's type.
    sdk = FakeSdk(base_url="https://somewhere.example/dataapi")
    with pytest.raises(AuthenticationError) as raised:
        build_transport(OFFICIAL, _config(), environ=OFFICIAL_ENV, sdk=sdk)
    assert OFFICIAL_HOST in str(raised.value)


def test_proxy_transport_keeps_its_label_and_uses_its_host_as_id():
    transport = build_transport(PROXY, _config(), environ=PROXY_ENV)
    assert transport.kind == PROXY
    assert transport.host == "proxy.example"
    assert transport.transport_id == "proxy.example"
    assert transport.supplier_endpoint("daily") == "tushare_proxy.daily"


def test_published_builds_fail_without_an_explicit_transport(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("TUSHARE_RELAY_KEY", "relay-key")
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(_config(), sdk=FakeSdk())
    assert TRANSPORT_ENV in str(raised.value)


def test_published_builds_reject_the_proxy():
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(_config(), environ=dict(PROXY_ENV, **{TRANSPORT_ENV: PROXY}))
    assert PROXY in str(raised.value)


def test_published_builds_reject_official_without_break_glass():
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(
            _config(), environ=dict(OFFICIAL_ENV, **{TRANSPORT_ENV: OFFICIAL})
        )
    assert OFFICIAL_PUBLISH_ENV in str(raised.value)


def test_break_glass_releases_official_for_a_published_build(caplog):
    # Acceptance 2 names both places an emergency official publish must show
    # up: the run log and the supplier endpoint.
    log = "stock_quant.data_sources.tushare_transport"
    with caplog.at_level(logging.INFO, logger=log):
        transport = resolve_transport(
            _config(),
            environ=dict(BREAK_GLASS_ENV, **{TRANSPORT_ENV: OFFICIAL}),
            sdk=FakeSdk(),
        )
    assert transport.kind == OFFICIAL
    assert transport.supplier_endpoint("daily") == "tushare.pro.daily"
    assert "kind=official" in caplog.text
    assert OFFICIAL_HOST in caplog.text


def test_unknown_transport_value_fails_without_falling_back():
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(
            _config(), environ=dict(RELAY_ENV, **{TRANSPORT_ENV: "relayy"})
        )
    assert "relayy" in str(raised.value)


@pytest.mark.parametrize(
    "env",
    [
        {TRANSPORT_ENV: RELAY},
        {TRANSPORT_ENV: RELAY, "TUSHARE_RELAY_URL": RELAY_URL},
        {TRANSPORT_ENV: RELAY, "TUSHARE_RELAY_KEY": "relay-key"},
    ],
)
def test_half_configured_relay_fails_without_falling_back(env):
    # A *half*-configured transport is an operator error, not an absence: it
    # must not quietly become a different transport.
    with pytest.raises(AuthenticationError):
        resolve_transport(_config(), environ=env, sdk=FakeSdk())


def test_relay_initialization_failure_does_not_fall_back_to_official():
    class ExplodingSdk(FakeSdk):
        def pro_api(self, token="", timeout=30):
            # A real SDK failure can echo what it was handed; the translation
            # must not let that reach the caller.
            raise RuntimeError(f"relay handshake failed for {token}")

    env = dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY, "TUSHARE_TOKEN": "official-token"})
    with pytest.raises(AuthenticationError) as raised:
        resolve_transport(_config(), environ=env, sdk=ExplodingSdk())
    assert "relay-key" not in str(raised.value)
    assert raised.value.__cause__ is None
    # The host is not a secret and is what makes the failure diagnosable.
    assert "jiaoch.example" in str(raised.value)


def test_auto_order_prefers_the_relay_when_both_are_configured():
    env = dict(RELAY_ENV, **OFFICIAL_ENV)
    transport = resolve_transport(
        _config(), allow_auto_transport=True, environ=env, sdk=FakeSdk()
    )
    assert transport.kind == RELAY


def test_auto_order_falls_back_to_official_and_never_to_the_proxy():
    # The proxy answers with fallback semantics; no automatic path may pick it
    # up behind the operator's back, even in development mode.
    env = dict(PROXY_ENV, **OFFICIAL_ENV)
    transport = resolve_transport(
        _config(), allow_auto_transport=True, environ=env, sdk=FakeSdk()
    )
    assert transport.kind == OFFICIAL


def test_auto_order_fails_when_nothing_is_configured():
    with pytest.raises(AuthenticationError):
        resolve_transport(
            _config(), allow_auto_transport=True, environ={}, sdk=FakeSdk()
        )


def test_auto_transport_never_consults_the_process_environment(monkeypatch):
    monkeypatch.setenv(TRANSPORT_ENV, PROXY)
    monkeypatch.setenv("TUSHARE_PROXY_URL", PROXY_URL)
    monkeypatch.setenv("TUSHARE_PROXY_KEY", "proxy-key")
    transport = resolve_transport(
        _config(), environ=dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY}), sdk=FakeSdk()
    )
    assert transport.kind == RELAY


def _daily_frame(ts_code: str, trade_date: str) -> pd.DataFrame:
    """A frame that satisfies the adapter's own contract validation."""
    return pd.DataFrame(
        {"ts_code": [ts_code], "trade_date": [trade_date], "close": [10.0]}
    )


class PlainClient:
    """A test double with the named methods and neither host nor sdk_version."""

    __version__ = "stub-1.0"

    def daily(self, ts_code=None, start_date=None, end_date=None):
        return _daily_frame(ts_code, start_date)


class RelayApi:
    """The official ``DataApi`` surface, answering a ``daily`` request.

    ``validate_supplier_frame`` requires the returned symbol set to equal the
    requested set and every ``trade_date`` to fall inside the window, so the
    fake has to answer with the requested code and a date inside the window.
    """

    def __init__(self, base_url: str) -> None:
        setattr(self, "_DataApi__http_url", base_url)

    def query(self, endpoint, **params):
        return _daily_frame(params.get("ts_code", ""), params.get("start_date", ""))

    def __getattr__(self, name):
        return partial(self.query, name)


class RelaySdk:
    __version__ = "fake-sdk-9.9"

    def pro_api(self, token="", timeout=30):
        return RelayApi(RELAY_URL)


def _request(endpoint: str = "daily", symbol: str = "000001.SZ") -> DataRequest:
    return DataRequest(endpoint, (symbol,), date(2026, 9, 1), date(2026, 9, 2))


def test_source_uses_the_resolved_transport_label_and_id():
    source = TushareSource(
        _config(),
        transport=resolve_transport(
            _config(),
            environ=dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY}),
            sdk=RelaySdk(),
        ),
    )
    result = source.fetch(_request())
    assert result.metadata["supplier_endpoint"] == "tushare_relay.jiaoch.example.daily"
    assert result.metadata["transport_id"] == "jiaoch.example"
    assert source.transport.kind == RELAY


def test_the_relay_session_is_the_official_data_api_surface():
    # §1.1: the relay's request client IS the official DataApi with its base
    # URL rewritten, so the very same ``daily(...)`` call answers -- only the
    # provenance differs.
    source = TushareSource(
        _config(),
        transport=resolve_transport(
            _config(),
            environ=dict(RELAY_ENV, **{TRANSPORT_ENV: RELAY}),
            sdk=RelaySdk(),
        ),
    )
    result = source.fetch(_request())
    assert list(result.frame.columns) == ["ts_code", "trade_date", "close"]
    assert result.frame["ts_code"].tolist() == ["000001.SZ"]
    assert result.frame["trade_date"].tolist() == ["20260901"]


def test_source_requires_an_explicit_transport_on_the_published_path(monkeypatch):
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)
    with pytest.raises(AuthenticationError) as raised:
        TushareSource(_config(), sdk=FakeSdk())
    assert TRANSPORT_ENV in str(raised.value)


def test_injected_client_is_described_without_isinstance_guessing():
    transport = injected_transport(PlainClient())
    assert transport.kind == OFFICIAL
    assert transport.transport_id == OFFICIAL_HOST
    assert transport.sdk_version == "stub-1.0"


def test_injected_proxy_client_keeps_its_own_host():
    transport = injected_transport(TushareProxyClient(PROXY_URL, "key"))
    assert transport.kind == PROXY
    assert transport.transport_id == "proxy.example"


def test_injected_relay_client_keeps_its_own_host():
    transport = injected_transport(TushareRelayClient(RELAY_URL, "key", sdk=FakeSdk()))
    assert transport.kind == RELAY
    assert transport.transport_id == "jiaoch.example"


def test_an_injected_relay_client_actually_answers_a_fetch():
    # ``TushareRelayClient`` only exposes ``query``; the adapter calls
    # ``daily(...)``.  Describing the wrapper without unwrapping ``.api``
    # would leave an AttributeError at fetch time, so this test exercises the
    # path end to end rather than only inspecting the descriptor.
    source = TushareSource(
        _config(), client=TushareRelayClient(RELAY_URL, "key", sdk=RelaySdk())
    )
    result = source.fetch(_request())
    assert result.frame["ts_code"].tolist() == ["000001.SZ"]
    assert source.transport.kind == RELAY
    assert source.transport.transport_id == "jiaoch.example"
    assert result.metadata["supplier_endpoint"] == "tushare_relay.jiaoch.example.daily"
