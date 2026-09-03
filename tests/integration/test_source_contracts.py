"""Offline recorded-response tests for supplier-specific raw contracts."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

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

    def daily(self, **_: str) -> pd.DataFrame:
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
