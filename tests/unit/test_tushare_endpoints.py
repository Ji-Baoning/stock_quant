"""Endpoint and probe-shape tests for the P1 tushare endpoint extension."""

from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "project" / "probe_index_weight_daily_basic.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe_index_weight_daily_basic", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_probe_render_is_offline_and_lists_every_request_shape(capsys):
    module = _load_probe()
    module.render(types.SimpleNamespace(
        index_codes=module.DEFAULT_INDEX_CODES,
        reference_symbol=module.DEFAULT_REFERENCE_SYMBOL))
    shapes = json.loads(capsys.readouterr().out)
    endpoints = {e["endpoint"] for group in shapes.values() for e in group}
    assert endpoints == {"index_weight", "daily_basic"}
    assert all(e["transport"] in ("relay", "proxy")
               for group in shapes.values() for e in group)
    assert "000905.SH" in json.dumps(shapes)  # probe starting point, not evidence
