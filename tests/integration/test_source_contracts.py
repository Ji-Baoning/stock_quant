"""Offline recorded-response tests for supplier-specific raw contracts."""

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import stock_quant.data_sources.tushare as tushare_adapter
from stock_quant.config import SourceConfig
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
)
from stock_quant.data_sources.tushare import TushareSource

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _request(endpoint: str, symbol: str) -> DataRequest:
    return DataRequest(
        endpoint=endpoint,
        symbols=(symbol,),
        start_date=date(2020, 1, 1),
        end_date=date(2020, 1, 2),
    )


class TushareClient:
    def __init__(self, frame: pd.DataFrame | Exception) -> None:
        self.frame = frame
        self.calls = 0

    def daily(self, **_: str) -> pd.DataFrame:
        self.calls += 1
        if isinstance(self.frame, Exception):
            raise self.frame
        return self.frame


class AkShareClient:
    def __init__(self, frame: pd.DataFrame | Exception) -> None:
        self.frame = frame

    def stock_zh_index_hist_em(self, **_: str) -> pd.DataFrame:
        if isinstance(self.frame, Exception):
            raise self.frame
        return self.frame


class BaoResponse:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.error_code = "0"
        self.fields = list(frame.columns)
        self._rows = frame.astype(str).values.tolist()
        self._index = 0

    def next(self) -> bool:
        if self._index == len(self._rows):
            return False
        self._index += 1
        return True

    def get_row_data(self) -> list[str]:
        return self._rows[self._index - 1]


class BaoStockClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.logins = 0
        self.logouts = 0

    def login(self) -> SimpleNamespace:
        self.logins += 1
        return SimpleNamespace(error_code="0", error_msg="")

    def logout(self) -> None:
        self.logouts += 1

    def query_history_k_data_plus(self, *_: str, **__: str) -> object:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_tushare_returns_recorded_native_columns_without_token_logging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """The raw boundary must preserve Tushare naming and hide credentials."""
    monkeypatch.setenv("TUSHARE_TOKEN", "real-token")
    frame = pd.read_csv(FIXTURES / "tushare_daily.csv")

    result = TushareSource(SourceConfig(), TushareClient(frame)).fetch(
        _request("daily", "000001.SZ")
    )

    assert result.frame.columns.tolist() == ["ts_code", "trade_date", "open", "close"]
    assert "real-token" not in caplog.text


def test_tushare_rejects_adjusted_daily_request_without_calling_supplier(
    monkeypatch: pytest.MonkeyPatch,
):
    """A raw daily endpoint cannot claim a forward-adjusted response."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    client = TushareClient(pd.read_csv(FIXTURES / "tushare_daily.csv"))
    request = _request("daily", "000001.SZ")
    request.params["adjustment"] = "forward"

    with pytest.raises(ValueError, match="unadjusted"):
        TushareSource(SourceConfig(), client).fetch(request)
    assert client.calls == 0


def test_tushare_records_distinct_request_and_response_timestamps(
    monkeypatch: pytest.MonkeyPatch,
):
    """Audit metadata must measure the supplier call rather than fabricate it."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    timestamps = iter(("2026-09-03T10:00:00+00:00", "2026-09-03T10:00:02+00:00"))
    monkeypatch.setattr(
        tushare_adapter, "_utc_timestamp", lambda: next(timestamps), raising=False
    )

    result = TushareSource(
        SourceConfig(), TushareClient(pd.read_csv(FIXTURES / "tushare_daily.csv"))
    ).fetch(_request("daily", "000001.SZ"))

    assert result.metadata["request_timestamp"] == "2026-09-03T10:00:00+00:00"
    assert result.metadata["response_timestamp"] == "2026-09-03T10:00:02+00:00"


def test_tushare_constructor_redacts_token_when_sdk_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """An SDK setup error must not retain or expose the caller's credential."""
    token = "super-secret-token"
    monkeypatch.setenv("TUSHARE_TOKEN", token)

    def pro_api(_: str) -> None:
        raise RuntimeError(f"SDK rejected {token}")

    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_api=pro_api))

    with pytest.raises(AuthenticationError) as raised:
        TushareSource(SourceConfig())
    assert token not in str(raised.value)
    assert raised.value.__cause__ is None


def test_akshare_and_baostock_return_recorded_native_columns():
    """Recorded fixtures prove the adapters retain each supplier's schema."""
    ak_frame = pd.read_csv(FIXTURES / "akshare_index_history.csv")
    bao_frame = pd.read_csv(FIXTURES / "baostock_daily.csv")
    bao_client = BaoStockClient(BaoResponse(bao_frame))

    ak_result = AkShareSource(SourceConfig(), AkShareClient(ak_frame)).fetch(
        _request("index_history", "000300")
    )
    bao_result = BaoStockSource(SourceConfig(), bao_client).fetch(
        _request("daily", "sz.000001")
    )

    assert ak_result.frame.columns.tolist() == ["日期", "代码", "开盘", "收盘"]
    assert bao_result.frame.columns.tolist() == ["date", "code", "open", "close"]
    assert (bao_client.logins, bao_client.logouts) == (1, 1)


def _source_for(
    supplier: str, frame_or_error: pd.DataFrame | Exception | object, monkeypatch
):
    if supplier == "tushare":
        monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
        return TushareSource(SourceConfig(), TushareClient(frame_or_error))
    if supplier == "akshare":
        return AkShareSource(SourceConfig(), AkShareClient(frame_or_error))
    return BaoStockSource(SourceConfig(), BaoStockClient(frame_or_error))


@pytest.mark.parametrize(
    ("supplier", "endpoint", "symbol"),
    [
        ("tushare", "daily", "000001.SZ"),
        ("akshare", "index_history", "000300"),
        ("baostock", "daily", "sz.000001"),
    ],
)
def test_adapters_map_authentication_response_to_authentication_error(
    supplier: str, endpoint: str, symbol: str, monkeypatch: pytest.MonkeyPatch
):
    """Invalid credentials are permanent failures for every source boundary."""
    source = _source_for(supplier, RuntimeError("invalid token"), monkeypatch)

    with pytest.raises(AuthenticationError):
        source.fetch(_request(endpoint, symbol))


@pytest.mark.parametrize(
    ("supplier", "endpoint", "symbol", "response"),
    [
        ("tushare", "daily", "000001.SZ", pd.DataFrame()),
        ("akshare", "index_history", "000300", pd.DataFrame()),
        ("baostock", "daily", "sz.000001", BaoResponse(pd.DataFrame())),
    ],
)
def test_adapters_map_empty_response_to_contract_error(
    supplier: str,
    endpoint: str,
    symbol: str,
    response: object,
    monkeypatch: pytest.MonkeyPatch,
):
    """An empty recorded response cannot silently become an empty data set."""
    source = _source_for(supplier, response, monkeypatch)

    with pytest.raises(ContractError):
        source.fetch(_request(endpoint, symbol))


@pytest.mark.parametrize(
    ("supplier", "endpoint", "symbol", "response"),
    [
        (
            "tushare",
            "daily",
            "000001.SZ",
            pd.DataFrame({"ts_code": ["000001.SZ"]}),
        ),
        (
            "akshare",
            "index_history",
            "000300",
            pd.DataFrame({"代码": ["000300"]}),
        ),
        ("baostock", "daily", "sz.000001", SimpleNamespace(error_code="0")),
    ],
)
def test_adapters_map_truncated_response_to_contract_error(
    supplier: str,
    endpoint: str,
    symbol: str,
    response: object,
    monkeypatch: pytest.MonkeyPatch,
):
    """Missing required supplier fields are a contract breach, not usable data."""
    source = _source_for(supplier, response, monkeypatch)

    with pytest.raises(ContractError):
        source.fetch(_request(endpoint, symbol))


@pytest.mark.parametrize(
    ("supplier", "endpoint", "symbol", "response"),
    [
        (
            "tushare",
            "daily",
            "000001.SZ",
            pd.DataFrame({"ts_code": ["000002.SZ"], "trade_date": ["20200101"]}),
        ),
        (
            "akshare",
            "index_history",
            "000300",
            pd.DataFrame({"代码": ["000905"], "日期": ["2020-01-01"]}),
        ),
        (
            "baostock",
            "daily",
            "sz.000001",
            BaoResponse(pd.DataFrame({"code": ["sz.000002"], "date": ["2020-01-01"]})),
        ),
    ],
)
def test_adapters_map_symbol_mismatch_to_contract_error(
    supplier: str,
    endpoint: str,
    symbol: str,
    response: object,
    monkeypatch: pytest.MonkeyPatch,
):
    """A response for the wrong instrument must remain quarantined at the boundary."""
    source = _source_for(supplier, response, monkeypatch)

    with pytest.raises(ContractError):
        source.fetch(_request(endpoint, symbol))


def test_tushare_rejects_response_contaminated_with_an_unrequested_symbol(
    monkeypatch: pytest.MonkeyPatch,
):
    """A requested symbol plus unrelated rows is not a valid raw response."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "trade_date": ["20200101", "20200101"],
        }
    )

    with pytest.raises(ContractError, match="each requested symbol"):
        TushareSource(SourceConfig(), TushareClient(frame)).fetch(
            _request("daily", "000001.SZ")
        )


@pytest.mark.parametrize(
    ("supplier", "endpoint", "symbol", "response"),
    [
        (
            "tushare",
            "daily",
            "000001.SZ",
            pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20200103"]}),
        ),
        (
            "akshare",
            "index_history",
            "000300",
            pd.DataFrame({"代码": ["000300"], "日期": ["2020-01-03"]}),
        ),
        (
            "baostock",
            "daily",
            "sz.000001",
            BaoResponse(pd.DataFrame({"code": ["sz.000001"], "date": ["2020-01-03"]})),
        ),
    ],
)
def test_adapters_map_date_range_mismatch_to_contract_error(
    supplier: str,
    endpoint: str,
    symbol: str,
    response: object,
    monkeypatch: pytest.MonkeyPatch,
):
    """Out-of-range supplier data cannot pass the immutable raw contract."""
    source = _source_for(supplier, response, monkeypatch)

    with pytest.raises(ContractError):
        source.fetch(_request(endpoint, symbol))
