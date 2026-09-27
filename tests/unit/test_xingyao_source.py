"""The xingyao daily adapter: native columns in, a typed frame out."""

from __future__ import annotations

import json
from datetime import date

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
    """Stand-in for the SDK: one multi-code answer, keyed by code.

    The answer travels back from a forked child, so a fake that merely
    *records* what it was asked cannot be read back by these tests -- the
    append happens in the child.  What the tests assert on is the answer's
    content, which the worker does return.
    """

    def __init__(
        self,
        *,
        login_error: Exception | None = None,
        kline_error: Exception | None = None,
    ) -> None:
        self.login_error = login_error
        self.kline_error = kline_error
        self.logins = 0

    def login(self, **_):
        self.logins += 1
        if self.login_error is not None:
            raise self.login_error
        return object()

    def query_kline(self, *, symbols=None, **_):
        if self.kline_error is not None:
            raise self.kline_error
        return {
            code: FakeKline(rows=[{**row, "code": code} for row in FakeKline().rows])
            for code in list(symbols or [])
        }


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
    def query_kline(self, *, symbols=None, **_):
        # The real SDK's zero-row answer keeps the columns; a frame built
        # from no rows alone would not, and the parent would refuse it.
        return {code: FakeKline().to_frame().iloc[:0] for code in symbols}


def test_a_frame_without_the_date_column_is_a_contract_error():
    class _NoDate(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return {
                code: FakeKline(rows=[{"code": code, "close": 10.5}])
                for code in symbols
            }

    with pytest.raises(ContractError):
        _source(_NoDate()).fetch(_request())


def test_a_row_outside_the_requested_window_is_a_contract_error():
    """An out-of-window row means the request was not honoured as written."""

    class _OutOfWindow(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return {
                code: FakeKline(
                    rows=[
                        {
                            "kline_time": "2023-12-01",
                            "code": code,
                            "open": 1.0,
                            "high": 1.0,
                            "low": 1.0,
                            "close": 1.0,
                            "volume": 1.0,
                            "amount": 1.0,
                        }
                    ]
                )
                for code in symbols
            }

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


def _batch_request(symbols):
    return [
        DataRequest("daily", (symbol,), _START, _END, {"adjustment": "unadjusted"})
        for symbol in symbols
    ]


def test_one_batch_call_answers_every_requested_symbol():
    """One login, one calendar, one query -- however many codes (ADR-020 D1)."""
    result = _source(FakeSdk()).fetch_batch(_batch_request(["000001.SZ", "600000.SH"]))

    assert [outcome.symbol for outcome in result.outcomes] == [
        "000001.SZ",
        "600000.SH",
    ]
    assert all(outcome.status == "ok" for outcome in result.outcomes)
    assert len(result.transmissions) == 1
    assert json.loads(result.transmissions[0].request_parameters)["symbols"] == [
        "000001.SZ",
        "600000.SH",
    ]


def test_each_ok_outcome_carries_its_own_fetch_result():
    """request_key and metadata stay per symbol even when one call served both."""
    result = _source(FakeSdk()).fetch_batch(_batch_request(["000001.SZ", "600000.SH"]))
    first, second = (outcome.result for outcome in result.outcomes)

    assert first.request_key != second.request_key
    assert first.metadata["transport_id"] == "xingyao-broker-tcp"
    assert first.metadata["supplier_endpoint"] == "xingyao.query_kline"
    assert first.metadata["request_timestamp"] == second.metadata["request_timestamp"]
    assert first.frame["code"].tolist() == ["000001.SZ", "000001.SZ"]
    assert second.frame["code"].tolist() == ["600000.SH", "600000.SH"]


def test_a_supplier_object_with_no_rows_is_an_empty_answer():
    """The zero-row object exists, so it is an answer -- and is snapshotted."""
    result = _source(_EmptySdk()).fetch_batch(_batch_request(["000001.SZ"]))

    assert result.outcomes[0].status == "empty"
    assert result.outcomes[0].result is not None
    assert result.outcomes[0].result.frame.empty


def test_a_code_absent_from_the_answer_is_refused_not_empty():
    """Fail-closed: an absent key has an unknowable cause (ADR-020 D2)."""

    class _Partial(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return {"000001.SZ": FakeKline()}

    result = _source(_Partial()).fetch_batch(_batch_request(["000001.SZ", "600000.SH"]))

    assert [outcome.status for outcome in result.outcomes] == ["ok", "refused"]
    assert result.outcomes[1].result is None
    assert result.outcomes[1].message


def test_a_contract_break_in_one_code_refuses_only_that_code():
    """Layer 2: one bad frame must not take the chunk down with it."""

    class _OneBad(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return {
                "000001.SZ": FakeKline(),
                "600000.SH": FakeKline(rows=[{"code": "600000.SH", "close": 1.0}]),
            }

    result = _source(_OneBad()).fetch_batch(_batch_request(["000001.SZ", "600000.SH"]))

    assert [outcome.status for outcome in result.outcomes] == ["ok", "refused"]


def test_an_answer_that_is_not_a_mapping_terminates_without_retry():
    """A whole-call contract break is permanent, not transient (spec §4 layer 1)."""

    class _NotAMapping(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return object()

    with pytest.raises(ContractError):
        _source(_NotAMapping()).fetch_batch(_batch_request(["000001.SZ"]))


def test_a_value_that_is_not_a_frame_refuses_that_code_in_the_parent():
    """The worker never judges a code; it only makes the value transportable."""

    class _Unreadable(FakeSdk):
        def query_kline(self, *, symbols=None, **_):
            return {"000001.SZ": object()}

    result = _source(_Unreadable()).fetch_batch(_batch_request(["000001.SZ"]))

    assert result.outcomes[0].status == "refused"
    assert result.outcomes[0].result is None


def test_batch_and_single_request_judge_an_empty_answer_differently():
    """ADR-020 D2's boundary: the lane may call a zero-row object an answer,
    the single-request path may not."""
    with pytest.raises(ContractError):
        _source(_EmptySdk()).fetch(_request())
    result = _source(_EmptySdk()).fetch_batch(_batch_request(["000001.SZ"]))
    assert result.outcomes[0].status == "empty"


def test_each_batch_call_reports_one_attempt_with_its_code_count():
    """The parent counts attempts: a child-side counter never comes back."""
    attempts: list[int] = []
    _source(FakeSdk()).fetch_batch(
        _batch_request(["000001.SZ", "600000.SH"]), on_attempt=attempts.append
    )
    assert attempts == [2]


def test_a_batch_request_that_is_not_one_window_is_refused():
    with pytest.raises(ValueError, match="exactly one window"):
        _source(FakeSdk()).fetch_batch(
            [
                DataRequest("daily", ("000001.SZ",), _START, _END, {}),
                DataRequest("daily", ("600000.SH",), _START, date(2024, 1, 6), {}),
            ]
        )
