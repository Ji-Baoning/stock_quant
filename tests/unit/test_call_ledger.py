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


def test_render_always_carries_the_reused_section():
    """Every row carries ``reused`` (ADR-015), empty when nothing was reused.

    The section lists per source x endpoint how many requests were served
    from stored raw snapshots; an absent payload renders empty mappings so
    the ledger shape is stable across rounds and reused requests stay
    distinguishable from quota-consuming ones.
    """
    rows = render_call_ledger(
        {
            "tushare": _ListCalls(),
            "baostock": _ScalarCalls(),
        },
        reused={"tushare": {"daily": 12}},
    )
    assert rows["tushare"] == {
        "calls": 3,
        "endpoints": {"daily": 2, "trade_cal": 1},
        "reused": {"daily": 12},
    }
    assert rows["baostock"] == {
        "calls": 7,
        "endpoints": {},
        "reused": {},
    }


def test_render_reused_defaults_to_empty_shape():
    rows = render_call_ledger({"tushare": _ScalarCalls()}, reused=None)
    assert rows["tushare"] == {"calls": 7, "endpoints": {}, "reused": {}}


def test_write_ledger(tmp_path):
    path = write_call_ledger(
        tmp_path, "run-abc", {"tushare": {"calls": 3, "endpoints": {"daily": 3}}}
    )
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tushare"]["calls"] == 3
