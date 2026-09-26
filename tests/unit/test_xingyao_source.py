"""The xingyao daily adapter: native columns in, a typed frame out."""

from __future__ import annotations

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
    ServerError,
)
from stock_quant.data_sources.xingyao import XingyaoSource

_START = __import__("datetime").date(2024, 1, 2)
_END = __import__("datetime").date(2024, 1, 5)


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    """Every case but the credential one assumes configured credentials.

    The presence check runs in the parent (the adapter refuses to start a
    worker it knows will fail), so without this the whole file would fail for
    the one reason it is not testing.
    """
    monkeypatch.setenv("AD_USERNAME", "test-user")
    monkeypatch.setenv("AD_PASSWORD", "test-secret")


class FakeKline:
    """Stand-in for the SDK's kline result: a frame with native columns."""

    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows if rows is not None else [
            {"kline_time": "2024-01-02", "code": "000001.SZ", "open": 10.0,
             "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1000.0,
             "amount": 10500.0},
            {"kline_time": "2024-01-03", "code": "000001.SZ", "open": 10.5,
             "high": 11.5, "low": 10.0, "close": 11.0, "volume": 1200.0,
             "amount": 13200.0},
        ]

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


class FakeSdk:
    def __init__(self, *, login_error: Exception | None = None,
                 kline_error: Exception | None = None) -> None:
        self.login_error = login_error
        self.kline_error = kline_error
        self.logins = 0

    def login(self, **_):
        self.logins += 1
        if self.login_error is not None:
            raise self.login_error
        return object()

    def query_kline(self, **_):
        if self.kline_error is not None:
            raise self.kline_error
        return FakeKline()


def _request(endpoint: str = "daily", symbols: tuple[str, ...] = ("000001.SZ",)):
    return DataRequest(endpoint, symbols, _START, _END, {"adjustment": "unadjusted"})


def _source(sdk: FakeSdk) -> XingyaoSource:
    return XingyaoSource(SourceConfig(), client=sdk)


def test_the_daily_frame_keeps_its_native_columns_with_a_date_column():
    result = _source(FakeSdk()).fetch(_request())
    assert list(result.frame.columns) == [
        "kline_time", "code", "open", "high", "low", "close", "volume", "amount",
    ]
    assert result.frame["kline_time"].tolist() == ["2024-01-02", "2024-01-03"]


def test_the_result_carries_its_source_endpoint_and_transport_identity():
    result = _source(FakeSdk()).fetch(_request())
    assert result.source == "xingyao"
    assert result.endpoint == "daily"
    assert result.metadata["transport_id"] == "xingyao-broker-tcp"
    assert result.metadata["supplier_endpoint"] == "xingyao.query_kline"
    parameters = __import__("json").loads(result.metadata["request_parameters"])
    assert parameters["symbols"] == ["000001.SZ"]
    assert parameters["params"] == {"adjustment": "unadjusted"}


def test_only_the_daily_endpoint_is_served():
    with pytest.raises(ValueError, match="daily"):
        _source(FakeSdk()).fetch(_request(endpoint="backward_factor"))


def test_exactly_one_symbol_per_request():
    with pytest.raises(ValueError, match="one symbol"):
        _source(FakeSdk()).fetch(_request(symbols=("000001.SZ", "600000.SH")))


def test_a_login_failure_is_permanent_and_not_retried():
    source = _source(FakeSdk(login_error=RuntimeError("用户名或密码错误")))
    with pytest.raises(AuthenticationError):
        source.fetch(_request())


def test_a_broker_side_failure_is_transient():
    source = _source(FakeSdk(kline_error=RuntimeError("tgw error -76")))
    with pytest.raises(ServerError):
        source.fetch(_request())


def test_an_empty_frame_is_a_contract_error_not_an_empty_answer():
    """Silence must not be booked as an answer (ADR-009's stance, ADR-015 d4)."""
    with pytest.raises(ContractError):
        _source(_EmptySdk()).fetch(_request())


class _EmptySdk(FakeSdk):
    def query_kline(self, **_):
        return FakeKline(rows=[])


def test_a_frame_without_the_date_column_is_a_contract_error():
    class _NoDate(FakeSdk):
        def query_kline(self, **_):
            return FakeKline(rows=[{"code": "000001.SZ", "close": 10.5}])

    with pytest.raises(ContractError):
        _source(_NoDate()).fetch(_request())


def test_a_row_outside_the_requested_window_is_a_contract_error():
    """An out-of-window row means the request was not honoured as written."""

    class _OutOfWindow(FakeSdk):
        def query_kline(self, **_):
            return FakeKline(
                rows=[
                    {
                        "kline_time": "2023-12-01",
                        "code": "000001.SZ",
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1.0,
                        "amount": 1.0,
                    }
                ]
            )

    with pytest.raises(ContractError):
        _source(_OutOfWindow()).fetch(_request())


def test_missing_credentials_are_permanent_and_start_no_worker(monkeypatch):
    monkeypatch.delenv("AD_USERNAME", raising=False)
    monkeypatch.delenv("AD_PASSWORD", raising=False)
    sdk = FakeSdk()
    with pytest.raises(AuthenticationError, match="AD_USERNAME"):
        _source(sdk).fetch(_request())
    assert sdk.logins == 0, "a worker was started for a call that cannot succeed"


def test_a_failed_login_never_reproduces_what_it_was_given():
    source = _source(FakeSdk(login_error=RuntimeError("密码错误")))
    with pytest.raises(AuthenticationError) as caught:
        source.fetch(_request())
    assert "test-secret" not in str(caught.value)
