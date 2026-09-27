"""Call ledger rendering and persistence (spec D5.5)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.data_model.call_ledger import render_call_ledger, write_call_ledger
from stock_quant.data_pipeline import (
    _CONFIGURED_SOURCES,
    DataPipeline,
    SourceStatus,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
        "transport": {},
    }
    assert rows["baostock"] == {
        "calls": 7,
        "endpoints": {},
        "reused": {},
        "transport": {},
    }


def test_render_reused_defaults_to_empty_shape():
    rows = render_call_ledger({"tushare": _ScalarCalls()}, reused=None)
    assert rows["tushare"] == {
        "calls": 7,
        "endpoints": {},
        "reused": {},
        "transport": {},
    }


def test_write_ledger(tmp_path):
    path = write_call_ledger(
        tmp_path, "run-abc", {"tushare": {"calls": 3, "endpoints": {"daily": 3}}}
    )
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tushare"]["calls"] == 3


def test_transport_renders_attempted_sessions_and_code_queries():
    """Attempted operations, not successes: retries and bisection count too."""
    rows = render_call_ledger(
        {"xingyao": _ScalarCalls()},
        transport={"xingyao": {"daily": {"sessions": 3, "code_queries": 4}}},
    )
    assert rows["xingyao"]["transport"] == {"daily": {"sessions": 3, "code_queries": 4}}


def test_transport_defaults_to_empty_and_sorts_endpoints():
    rows = render_call_ledger(
        {"xingyao": _ScalarCalls()},
        transport={
            "xingyao": {
                "backward_factor": {"sessions": 1, "code_queries": 1},
                "daily": {"sessions": 1, "code_queries": 1},
            }
        },
    )
    assert list(rows["xingyao"]["transport"]) == ["backward_factor", "daily"]
    assert render_call_ledger({"xingyao": _ScalarCalls()})["xingyao"]["transport"] == {}


def _project(tmp_path) -> Path:
    root = tmp_path / "project"
    configs = root / "configs"
    configs.mkdir(parents=True)
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
    ):
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)
    return root


def _ok_statuses() -> dict:
    return {
        name: SourceStatus(name, False, True, reason_code="ok")
        for name in _CONFIGURED_SOURCES
    }


def test_the_result_funnel_persists_the_ledger_on_every_termination(tmp_path):
    """Success, quality block and source failure all leave a ledger (spec §4)."""
    root = _project(tmp_path)
    pipeline = DataPipeline(root, sources={})
    pipeline._transport_counts = {
        "xingyao": {"daily": {"sessions": 1, "code_queries": 1}}
    }

    pipeline._result([], None, "data_update_abc123", None, _ok_statuses(), [])

    path = root / "data" / "runs" / "data_update_abc123" / "call_ledger.json"
    payload = json.loads(path.read_text())
    assert payload["xingyao"]["transport"] == {
        "daily": {"sessions": 1, "code_queries": 1}
    }


def test_a_result_without_a_run_id_writes_nothing(tmp_path):
    root = _project(tmp_path)
    DataPipeline(root, sources={})._result([], None, None, None, _ok_statuses(), [])
    assert not (root / "data" / "runs").exists()
