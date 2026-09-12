"""Unit tests for the offline SDK-protocol relay client (no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from stock_quant.data_sources.tushare_relay import TushareRelayClient

RELAY_URL = "https://relay.example/"


class FakeApi:
    """Records the SDK calls the client delegates to."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def query(self, endpoint, **params):
        self.calls.append((endpoint, dict(params)))
        return pd.DataFrame({"endpoint": [endpoint]})


class FakeSdk:
    """A stand-in for the ``tushare`` module; ``pro_api`` records its args."""

    __version__ = "fake-sdk-9.9"

    def __init__(self) -> None:
        self.api = FakeApi()
        self.init_args: tuple | None = None

    def pro_api(self, token="", timeout=30):
        self.init_args = (token, timeout)
        return self.api


def test_from_env_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("TUSHARE_RELAY_URL", raising=False)
    monkeypatch.delenv("TUSHARE_RELAY_KEY", raising=False)
    assert TushareRelayClient.from_env(sdk=FakeSdk()) is None


@pytest.mark.parametrize(
    "url,key",
    [("https://relay.example/", ""), ("", "secret"), ("   ", "secret")],
)
def test_from_env_returns_none_when_blank(monkeypatch, url, key):
    monkeypatch.setenv("TUSHARE_RELAY_URL", url)
    monkeypatch.setenv("TUSHARE_RELAY_KEY", key)
    assert TushareRelayClient.from_env(sdk=FakeSdk()) is None


def test_from_env_builds_client_and_points_sdk_at_relay(monkeypatch):
    monkeypatch.setenv("TUSHARE_RELAY_URL", RELAY_URL)
    monkeypatch.setenv("TUSHARE_RELAY_KEY", "secret")
    sdk = FakeSdk()
    client = TushareRelayClient.from_env(sdk=sdk, timeout_seconds=17)
    assert client is not None
    assert sdk.init_args == ("secret", 17)
    # The relay is reached by overriding the SDK's own base URL -- the only
    # integration point a third-party SDK-protocol relay offers.
    assert getattr(sdk.api, "_DataApi__http_url") == RELAY_URL


def test_constructor_rejects_blank_base_url_and_token():
    with pytest.raises(ValueError):
        TushareRelayClient("   ", "secret", sdk=FakeSdk())
    with pytest.raises(ValueError):
        TushareRelayClient(RELAY_URL, "", sdk=FakeSdk())


def test_query_delegates_and_host_is_audit_only():
    sdk = FakeSdk()
    client = TushareRelayClient(RELAY_URL, "secret", sdk=sdk)
    frame = client.query("index_weight", index_code="000300.SH")
    assert sdk.api.calls == [("index_weight", {"index_code": "000300.SH"})]
    assert list(frame.columns) == ["endpoint"]
    assert client.host == "relay.example"
    assert client.sdk_version == "fake-sdk-9.9"


def test_base_url_is_preserved_verbatim_for_the_sdk():
    # The SDK POSTs to this string as-is; normalizing it would break the
    # relay's routing.
    sdk = FakeSdk()
    TushareRelayClient("https://relay.example/", "secret", sdk=sdk)
    assert getattr(sdk.api, "_DataApi__http_url") == "https://relay.example/"


def test_api_exposes_the_session_that_was_rewritten():
    sdk = FakeSdk()
    client = TushareRelayClient(RELAY_URL, "secret", sdk=sdk)
    assert client.api is sdk.api
    assert getattr(client.api, "_DataApi__http_url") == RELAY_URL
