"""Unit tests for tushare transport selection and provenance (no network)."""

from __future__ import annotations

import logging
from functools import partial

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import AuthenticationError, host_of
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
