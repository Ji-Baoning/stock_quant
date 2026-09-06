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
    ServerError,
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

    def stock_basic(self, **_: str) -> pd.DataFrame:
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

    def stock_info_a_code_name(self) -> pd.DataFrame:
        if isinstance(self.frame, Exception):
            raise self.frame
        return self.frame


class CurrentAkShareClient:
    """Minimal shape of the current AKShare index-history API."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.arguments: dict[str, str] | None = None

    def stock_zh_index_daily_em(self, **kwargs: str) -> pd.DataFrame:
        self.arguments = kwargs
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


def test_tushare_stock_basic_returns_whole_market_native_columns(monkeypatch):
    """stock_basic is a whole-market reference: empty symbols, native columns."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    frame = pd.read_csv(FIXTURES / "tushare_stock_basic.csv", dtype={"list_date": str})
    request = DataRequest("stock_basic", (), date(2020, 1, 1), date(2020, 1, 2))

    result = TushareSource(SourceConfig(), TushareClient(frame)).fetch(request)

    assert result.endpoint == "stock_basic"
    assert result.frame.columns.tolist() == [
        "ts_code", "name", "list_date", "delist_date", "list_status"
    ]
    assert result.metadata["supplier_endpoint"] == "tushare.pro.stock_basic"


def test_tushare_stock_basic_rejects_symbol_scoped_request(monkeypatch):
    """A symbol-scoped stock_basic request is a caller bug, not a valid query."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    frame = pd.read_csv(FIXTURES / "tushare_stock_basic.csv", dtype={"list_date": str})
    client = TushareClient(frame)
    request = DataRequest("stock_basic", ("600000.SH",), date(2020, 1, 1), date(2020, 1, 2))

    with pytest.raises(ValueError, match="whole-market"):
        TushareSource(SourceConfig(), client).fetch(request)


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


def test_akshare_uses_current_index_history_api_with_market_prefix():
    """Current AKShare expects an Eastmoney market-prefixed index symbol."""
    client = CurrentAkShareClient(pd.read_csv(FIXTURES / "akshare_index_history.csv"))

    result = AkShareSource(SourceConfig(), client).fetch(
        _request("index_history", "000300.SH")
    )

    assert client.arguments == {
        "symbol": "sh000300",
        "start_date": "20200101",
        "end_date": "20200102",
    }
    assert result.metadata["supplier_endpoint"] == "akshare.stock_zh_index_daily_em"


def test_current_akshare_index_history_does_not_require_a_symbol_column():
    """The current endpoint identifies the index in the request, not its frame."""
    frame = pd.DataFrame(
        {
            "date": ["2020-01-01", "2020-01-02"],
            "open": [1.0, 2.0],
            "high": [2.0, 3.0],
            "low": [0.5, 1.5],
            "close": [1.5, 2.5],
        }
    )

    result = AkShareSource(SourceConfig(), CurrentAkShareClient(frame)).fetch(
        _request("index_history", "000300")
    )

    assert result.frame.equals(frame)


def test_akshare_returns_recorded_stock_metadata_without_symbol_set_equality():
    """A universe endpoint is validated for shape, not per-symbol set equality."""
    frame = pd.read_csv(
        FIXTURES / "akshare_stock_metadata.csv", dtype={"code": str}
    )

    result = AkShareSource(SourceConfig(), AkShareClient(frame)).fetch(
        _request("stock_metadata", "000001")
    )

    assert result.endpoint == "stock_metadata"
    assert result.frame.columns.tolist() == ["code", "name"]
    assert result.frame["code"].tolist() == ["000001", "600000"]


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


def test_tushare_maps_chinese_daily_permission_denial_to_authentication_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    source = TushareSource(
        SourceConfig(), TushareClient(Exception("抱歉，您没有接口(daily)访问权限"))
    )

    with pytest.raises(AuthenticationError):
        source.fetch(_request("daily", "000001.SZ"))


def test_baostock_maps_chinese_network_error_to_server_error():
    source = BaoStockSource(
        SourceConfig(), BaoStockClient(RuntimeError("网络接收错误"))
    )

    with pytest.raises(ServerError):
        source.fetch(_request("daily", "sz.000001"))


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


def _index_history_frame(dates: list[str]) -> pd.DataFrame:
    """A minimal current-API index frame keyed on the English date column."""
    count = len(dates)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [float(i) for i in range(count)],
            "high": [float(i) for i in range(count)],
            "low": [float(i) for i in range(count)],
            "close": [float(i) for i in range(count)],
        }
    )


class ScriptedIndexClient:
    """Current-API akshare client whose index endpoints respond independently."""

    def __init__(
        self,
        em: pd.DataFrame | Exception,
        daily: pd.DataFrame | Exception,
        tx: pd.DataFrame | Exception,
    ) -> None:
        self._responses = {"em": em, "daily": daily, "tx": tx}
        self.calls: list[str] = []

    def _run(self, name: str) -> pd.DataFrame:
        self.calls.append(name)
        outcome = self._responses[name]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def stock_zh_index_daily_em(self, **_: str) -> pd.DataFrame:
        return self._run("em")

    def stock_zh_index_daily(self, symbol: str = "") -> pd.DataFrame:
        return self._run("daily")

    def stock_zh_index_daily_tx(self, symbol: str = "") -> pd.DataFrame:
        return self._run("tx")


def test_akshare_index_history_uses_eastmoney_when_it_responds():
    client = ScriptedIndexClient(
        em=_index_history_frame(["2020-01-01", "2020-01-02"]),
        daily=_index_history_frame(["2019-01-01"]),
        tx=_index_history_frame(["2019-01-01"]),
    )

    result = AkShareSource(SourceConfig(), client).fetch(
        _request("index_history", "000300")
    )

    assert client.calls == ["em"]
    assert result.metadata["supplier_endpoint"] == "akshare.stock_zh_index_daily_em"


def test_akshare_index_history_falls_back_to_sina_when_eastmoney_fails():
    client = ScriptedIndexClient(
        em=RuntimeError("connection reset by eastmoney"),
        daily=_index_history_frame(
            ["2019-12-31", "2020-01-01", "2020-01-02", "2020-01-03"]
        ),
        tx=_index_history_frame(["2019-01-01"]),
    )

    result = AkShareSource(SourceConfig(), client).fetch(
        _request("index_history", "000300")
    )

    assert client.calls == ["em", "daily"]
    assert result.metadata["supplier_endpoint"] == "akshare.stock_zh_index_daily"
    assert result.frame["date"].tolist() == ["2020-01-01", "2020-01-02"]


def test_akshare_index_history_falls_back_when_eastmoney_returns_empty():
    client = ScriptedIndexClient(
        em=pd.DataFrame(),
        daily=_index_history_frame(["2020-01-01", "2020-01-02"]),
        tx=_index_history_frame(["2019-01-01"]),
    )

    result = AkShareSource(SourceConfig(), client).fetch(
        _request("index_history", "000300")
    )

    assert client.calls == ["em", "daily"]
    assert result.metadata["supplier_endpoint"] == "akshare.stock_zh_index_daily"


def test_akshare_index_history_uses_tencent_only_as_last_resort():
    client = ScriptedIndexClient(
        em=RuntimeError("connection reset by eastmoney"),
        daily=RuntimeError("sina rate limited this caller"),
        tx=_index_history_frame(["2020-01-01", "2020-01-02"]),
    )

    result = AkShareSource(SourceConfig(), client).fetch(
        _request("index_history", "000300")
    )

    assert client.calls == ["em", "daily", "tx"]
    assert result.metadata["supplier_endpoint"] == "akshare.stock_zh_index_daily_tx"


def test_akshare_index_history_surfaces_eastmoney_error_when_all_fallbacks_fail():
    client = ScriptedIndexClient(
        em=RuntimeError("connection aborted"),
        daily=RuntimeError("connection aborted"),
        tx=RuntimeError("connection aborted"),
    )

    with pytest.raises(ServerError):
        AkShareSource(SourceConfig(), client).fetch(
            _request("index_history", "000300")
        )


def test_akshare_index_history_does_not_try_fallbacks_for_csi_symbols():
    client = ScriptedIndexClient(
        em=RuntimeError("connection aborted"),
        daily=_index_history_frame(["2020-01-01", "2020-01-02"]),
        tx=_index_history_frame(["2020-01-01", "2020-01-02"]),
    )

    with pytest.raises(ServerError):
        AkShareSource(SourceConfig(), client).fetch(
            _request("index_history", "csi931151")
        )
    assert client.calls == ["em"]
