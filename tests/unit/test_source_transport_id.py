"""Every adapter must name the party that answered (design §2.3)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.akshare import AkShareSource, _transport_id
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest, request_metadata

REQUEST = DataRequest("daily", ("000001.SZ",), date(2026, 9, 1), date(2026, 9, 2))


def test_request_metadata_carries_the_transport_id():
    metadata = request_metadata(
        REQUEST,
        "tushare_relay.jiaoch.top.daily",
        "1.4.24",
        transport_id="jiaoch.top",
    )
    assert metadata["transport_id"] == "jiaoch.top"


def test_request_metadata_has_no_default_transport_id():
    # A silent fallback value would let two upstreams collapse into one
    # content-addressed path, so the parameter is mandatory.
    with pytest.raises(TypeError):
        request_metadata(REQUEST, "tushare.pro.daily", "1.4.24")


def test_akshare_id_names_both_the_vendor_and_the_interface():
    assert (
        _transport_id("akshare.stock_zh_index_daily_em")
        == "eastmoney.stock-zh-index-daily-em"
    )
    assert (
        _transport_id("akshare.stock_zh_index_hist_em")
        == "eastmoney.stock-zh-index-hist-em"
    )
    assert _transport_id("akshare.stock_zh_index_daily") == "sina.stock-zh-index-daily"
    assert (
        _transport_id("akshare.stock_zh_index_daily_tx")
        == "tencent.stock-zh-index-daily-tx"
    )
    assert _transport_id("akshare.stock_fhps_detail_ths") == "ths.stock-fhps-detail-ths"
    assert (
        _transport_id("akshare.stock_dividend_cninfo") == "cninfo.stock-dividend-cninfo"
    )


def test_two_interfaces_behind_one_vendor_never_collide():
    # Both serve the same logical ``index_history`` request from EastMoney; if
    # the fallback chain switches between them, byte-identical frames must
    # still land on two paths, each keeping its own label.
    daily = _transport_id("akshare.stock_zh_index_daily_em")
    hist = _transport_id("akshare.stock_zh_index_hist_em")
    assert daily != hist
    assert daily.startswith("eastmoney.") and hist.startswith("eastmoney.")


def test_akshare_unmapped_endpoint_still_gets_its_own_distinct_id():
    # A shared constant would be exactly the collision this design exists to
    # prevent, so an unmapped endpoint names itself.
    got = _transport_id("akshare.stock_zh_a_hist")
    assert got == "akshare.stock-zh-a-hist"
    assert got not in {"", "unknown", "akshare"}
    assert got != _transport_id("akshare.stock_zh_a_hist_tx")


class FakeAkClient:
    """Answers whichever ``index_history`` candidate is asked first.

    Every candidate returns the same frame, so the test does not depend on
    the order ``_INDEX_FALLBACKS`` happens to be in -- it asserts that the
    transport id agrees with whichever endpoint actually answered.
    """

    __version__ = "ak-1.0"

    def _frame(self):
        return pd.DataFrame(
            {"date": ["2026-09-01", "2026-09-02"], "close": [4000.0, 4010.0]}
        )

    def stock_zh_index_daily_em(self, **kwargs):
        return self._frame()

    def stock_zh_index_daily(self, **kwargs):
        return self._frame()

    def stock_zh_index_daily_tx(self, **kwargs):
        return self._frame()


def test_akshare_transport_id_agrees_with_the_endpoint_that_answered():
    source = AkShareSource(SourceConfig(), FakeAkClient())
    request = DataRequest(
        "index_history", ("000300.SH",), date(2026, 9, 1), date(2026, 9, 2)
    )
    result = source.fetch(request)
    endpoint = result.metadata["supplier_endpoint"]
    # The pairing is what matters: the id must name the interface that really
    # answered, never a fixed constant.
    assert result.metadata["transport_id"] == _transport_id(endpoint)
    vendor = result.metadata["transport_id"].split(".", 1)[0]
    assert vendor in {"eastmoney", "sina", "tencent"}


class FakeBaoSession:
    """A session whose ``login`` answers OK and whose query returns a frame."""

    __version__ = "bs-1.0"

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def login(self):
        return type("R", (), {"error_code": "0", "error_msg": ""})()

    def logout(self):
        return None

    def query_history_k_data_plus(self, *args, **kwargs):
        # BaoStockSource._to_frame passes a DataFrame straight through.
        return self._frame


def test_baostock_transport_id_names_the_supplier_itself():
    frame = pd.DataFrame(
        {
            "date": ["2026-09-01"],
            "code": ["sh.600000"],
            "open": ["1.0"],
            "high": ["2.0"],
            "low": ["0.9"],
            "close": ["1.5"],
            "preclose": ["0.9"],
            "volume": ["100"],
            "amount": ["150"],
            "pctChg": ["0.1"],
            "tradestatus": ["1"],
        }
    )
    source = BaoStockSource(SourceConfig(), FakeBaoSession(frame))
    request = DataRequest("daily", ("sh.600000",), date(2026, 9, 1), date(2026, 9, 2))
    result = source.fetch(request)
    assert result.metadata["transport_id"] == "baostock"
    assert result.metadata["supplier_endpoint"] == "baostock.query_history_k_data_plus"
