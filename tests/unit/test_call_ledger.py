"""Call ledger rendering and persistence (spec D5.5)."""

from __future__ import annotations

import json

from stock_quant.data_model.call_ledger import render_call_ledger, write_call_ledger


class _ListCalls:
    calls = [
        {"endpoint": "daily", "rows": 200},
        {"endpoint": "daily", "rows": 300},
        {"endpoint": "trade_cal"},
    ]


class _ScalarCalls:
    calls = 7


def test_render_list_shaped_calls():
    rows = render_call_ledger({"tushare": _ListCalls()})
    assert rows["tushare"]["calls"] == 3
    assert rows["tushare"]["endpoints"] == {"daily": 2, "trade_cal": 1}


def test_render_scalar_calls():
    rows = render_call_ledger({"baostock": _ScalarCalls()})
    assert rows["baostock"]["calls"] == 7
    assert rows["baostock"]["endpoints"] == {}


def test_write_ledger(tmp_path):
    path = write_call_ledger(
        tmp_path, "run-abc", {"tushare": {"calls": 3, "endpoints": {"daily": 3}}}
    )
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tushare"]["calls"] == 3
