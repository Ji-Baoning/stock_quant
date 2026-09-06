"""Opt-in live supplier contract tests (operator-owned, never the default).

These tests call the real public adapters over the network with tiny recent
windows and assert the raw-frame contract each source documents (design spec
§28.3): a non-empty frame, the required columns present, and numeric OHLC
values.  They are marked ``external`` and are deselected by the default pytest
marker expression, so an ordinary ``pytest`` run never touches the network or
reads a token, and this module imports cleanly with no supplier SDK import and
no ``os.environ`` access at module scope.

An operator who has installed the supplier SDKs and exported their own rotated
``TUSHARE_TOKEN`` runs ``pytest -m external -v``.  When a supplier's live schema
or SDK function has drifted from what the adapter expects, it surfaces here as
a failure that is reconciled against ``docs/operations/phase-one-validation.md``
(e.g. AKShare EM index frames historically carry no symbol column and recent
akshare versions renamed the EM index endpoint); a contract violation is a
supplier-fidelity finding, not proof that the market data is wrong.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from importlib.util import find_spec

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.tushare import TushareSource

_TUSHARE_SYMBOL = "600000.SH"
_INDEX_SYMBOL = "000300"
_BAOSTOCK_SYMBOL = "sh.600000"

_OHLC = ("open", "high", "low", "close")


def _recent_window() -> tuple[date, date]:
    """Two to four trading sessions ending at the newest weekday before today."""
    end = date.today() - timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    return end - timedelta(days=10), end


def _assert_raw_contract(frame: pd.DataFrame, required: set[str]) -> None:
    """Assert the raw-frame contract: non-empty, required columns, numeric OHLC."""
    assert not frame.empty, "live supplier response is empty"
    missing = sorted(required - set(frame.columns))
    assert not missing, f"live supplier response is missing columns: {missing}"
    for column in sorted(required & set(_OHLC)):
        values = pd.to_numeric(frame[column], errors="coerce")
        assert not values.isna().all(), f"live {column!r} has no numeric values"


@pytest.mark.external
@pytest.mark.skipif(
    find_spec("tushare") is None or not os.getenv("TUSHARE_TOKEN"),
    reason="TUSHARE_TOKEN is required",
)
def test_tushare_daily_live_contract() -> None:
    start, end = _recent_window()
    request = DataRequest("daily", (_TUSHARE_SYMBOL,), start, end, {})
    result = TushareSource(SourceConfig()).fetch(request)
    _assert_raw_contract(
        result.frame, {"ts_code", "trade_date", *set(_OHLC)}
    )


@pytest.mark.external
@pytest.mark.skipif(
    find_spec("tushare") is None or not os.getenv("TUSHARE_TOKEN"),
    reason="TUSHARE_TOKEN is required",
)
def test_tushare_stock_basic_live_contract() -> None:
    """The live whole-market reference carries the master facts columns."""
    start, end = _recent_window()
    request = DataRequest("stock_basic", (), start, end, {})
    result = TushareSource(SourceConfig()).fetch(request)
    assert not result.frame.empty
    required = {"ts_code", "name", "list_date", "delist_date", "list_status"}
    missing = sorted(required - set(result.frame.columns))
    assert not missing, f"live stock_basic response is missing columns: {missing}"


@pytest.mark.external
@pytest.mark.skipif(find_spec("akshare") is None, reason="akshare is not installed")
def test_akshare_index_history_live_contract() -> None:
    start, end = _recent_window()
    request = DataRequest("index_history", (_INDEX_SYMBOL,), start, end, {})
    result = AkShareSource(SourceConfig()).fetch(request)
    if result.metadata["supplier_endpoint"].endswith("daily_em"):
        required = {"date", "open", "high", "low", "close"}
    else:
        required = {"日期", "开盘", "最高", "最低", "收盘"}
    _assert_raw_contract(result.frame, required)


@pytest.mark.external
@pytest.mark.skipif(find_spec("baostock") is None, reason="baostock is not installed")
def test_baostock_daily_live_contract() -> None:
    start, end = _recent_window()
    request = DataRequest("daily", (_BAOSTOCK_SYMBOL,), start, end, {})
    result = BaoStockSource(SourceConfig()).fetch(request)
    _assert_raw_contract(
        result.frame, {"date", "code", *set(_OHLC)}
    )
