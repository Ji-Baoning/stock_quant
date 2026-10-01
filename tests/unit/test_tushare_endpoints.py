"""Endpoint and probe-shape tests for the P1 tushare endpoint extension."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import ContractError, DataRequest
from stock_quant.data_sources.tushare import TushareSource

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


class StubClient:
    sdk_version = "stub-1"

    def __init__(self, frame):
        self.frame, self.calls = frame, []

    def daily_basic(self, **params):
        self.calls.append(params)
        return self.frame


class ProxyShapeStub:
    sdk_version = "stub-proxy-1"

    def __init__(self, frame):
        self.frame, self.calls = frame, []

    def query(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return self.frame


def _daily_basic_frame(rows):
    return pd.DataFrame(rows, columns=["ts_code", "trade_date",
                                       "total_mv", "turnover_rate"])


def _truncated(frame):
    frame.attrs["truncated"] = True
    return frame


def _daily_basic_request(day=date(2026, 9, 1)):
    return DataRequest("daily_basic", (), day, day, {})


def test_daily_basic_passes_the_raw_frame_through_without_zero_fill():
    frame = _daily_basic_frame([["000001.SZ", "20260901", "372000.0", "0.53"],
                                ["600000.SH", "20260901", None, None]])
    stub = StubClient(frame)
    result = TushareSource(SourceConfig(), client=stub).fetch(_daily_basic_request())
    assert stub.calls == [{"trade_date": "20260901",
                           "fields": "ts_code,trade_date,total_mv,turnover_rate"}]
    assert result.frame is frame  # raw evidence; no normalization, no zero fill
    assert result.frame["total_mv"].isna().tolist() == [False, True]
    assert result.metadata["supplier_endpoint"] == "tushare.pro.daily_basic"


def test_daily_basic_falls_back_to_the_proxys_verified_query():
    stub = ProxyShapeStub(_daily_basic_frame([["000001.SZ", "20260901", "1", "0.5"]]))
    TushareSource(SourceConfig(), client=stub).fetch(_daily_basic_request())
    assert stub.calls == [("daily_basic", {"trade_date": "20260901",
                           "fields": "ts_code,trade_date,total_mv,turnover_rate"})]


@pytest.mark.parametrize("frame,match", [
    (_daily_basic_frame([]), "empty response"),
    (pd.DataFrame([["000001.SZ", "1.0", "0.5"]],
                  columns=["ts_code", "total_mv", "turnover_rate"]),
     "missing columns: trade_date"),  # 缺日期键
    (pd.DataFrame([["000001.SZ", "20260901", "0.5"]],
                  columns=["ts_code", "trade_date", "turnover_rate"]),
     "missing columns: total_mv"),  # schema drift: value column renamed away
    (_daily_basic_frame([["000001.SZ", "20260901", "1", "0.5"],
                         ["000001.SZ", "20260901", "1", "0.5"]]),
     "duplicate primary-key"),
    (_truncated(_daily_basic_frame([["000001.SZ", "20260901", "1", "0.5"]])),
     "truncated"),
    (_daily_basic_frame([["000001.SZ", "20260910", "1", "0.5"]]),
     "outside the requested date range"),
])
def test_daily_basic_classifies_contract_failures(frame, match):
    with pytest.raises(ContractError, match=match):
        TushareSource(SourceConfig(), client=StubClient(frame)).fetch(
            _daily_basic_request())


def test_daily_basic_rejects_symbol_scoped_and_multi_day_windows():
    source = TushareSource(
        SourceConfig(), client=StubClient(
            _daily_basic_frame([["000001.SZ", "20260901", "1", "0.5"]])))
    with pytest.raises(ValueError, match="whole-market per-day"):
        source.fetch(DataRequest("daily_basic", ("000001.SZ",),
                                 date(2026, 9, 1), date(2026, 9, 1), {}))
    with pytest.raises(ValueError, match="exactly one trade_date"):
        source.fetch(DataRequest("daily_basic", (), date(2026, 9, 1),
                                 date(2026, 9, 2), {}))


def test_daily_basic_conversion_uses_the_frozen_constants_and_keeps_nulls():
    from stock_quant.data_model.basic_factor_normalize import (
        TOTAL_MV_TO_YUAN_MULTIPLIER,
        TURNOVER_RATE_TO_RATIO_DIVISOR,
        daily_basic_to_basic_factor_rows,
    )
    rows = daily_basic_to_basic_factor_rows(_daily_basic_frame(
        [["000001.SZ", "20260901", "372000.0", "0.53"],
         ["600000.SH", "20260901", None, None]]))
    assert rows["market_cap"].tolist()[0] == pytest.approx(
        372000.0 * TOTAL_MV_TO_YUAN_MULTIPLIER)
    assert rows["turnover_rate"].tolist()[0] == pytest.approx(
        0.53 / TURNOVER_RATE_TO_RATIO_DIVISOR)
    assert rows["market_cap"].isna().tolist() == [False, True]  # never 0


class IndexWeightStub:
    sdk_version = "stub-1"

    def __init__(self, frames):
        self._frames, self.calls = list(frames), []

    def index_weight(self, **params):
        self.calls.append(params)
        return self._frames.pop(0)


def _weight_rows(rows):
    return pd.DataFrame(rows, columns=["index_code", "con_code",
                                       "trade_date", "weight"])


def _weight_request(end=date(2026, 2, 28)):
    return DataRequest("index_weight", ("399300.SZ",), date(2026, 1, 1), end, {})


def test_index_weight_shards_by_calendar_month_and_digests_each_snapshot():
    january = _weight_rows([["399300.SZ", "000001.SZ", "20260130", "0.32"]])
    stub = IndexWeightStub([
        january,
        _weight_rows([["399300.SZ", "000001.SZ", "20260227", "0.33"]])])
    result = TushareSource(SourceConfig(), client=stub).fetch(_weight_request())
    assert stub.calls == [
        {"index_code": "399300.SZ", "start_date": "20260101",
         "end_date": "20260131"},
        {"index_code": "399300.SZ", "start_date": "20260201",
         "end_date": "20260231"}]
    assert len(result.frame) == 2
    digests = json.loads(result.metadata["index_weight_snapshots"])
    assert [d["month"] for d in digests] == ["202601", "202602"]
    assert digests[0]["sha256"] == hashlib.sha256(
        january.to_csv(index=False).encode("utf-8")).hexdigest()


def test_an_empty_month_is_recorded_not_interpreted():
    january = _weight_rows([])  # empty month WITH columns: kept, rows=0
    stub = IndexWeightStub([
        january, _weight_rows([["399300.SZ", "000001.SZ", "20260227", "0.33"]])])
    result = TushareSource(SourceConfig(), client=stub).fetch(_weight_request())
    digests = json.loads(result.metadata["index_weight_snapshots"])
    assert digests[0]["rows"] == 0
    assert digests[0]["sha256"] == hashlib.sha256(
        january.to_csv(index=False).encode("utf-8")).hexdigest()
    assert len(result.frame) == 1  # no membership invented for the empty month


def test_an_all_empty_window_is_a_contract_failure():
    stub = IndexWeightStub([_weight_rows([]), _weight_rows([])])
    with pytest.raises(ContractError, match="empty response"):
        TushareSource(SourceConfig(), client=stub).fetch(_weight_request())


def test_index_weight_rejects_codes_outside_the_probe_frozen_table():
    request = DataRequest("index_weight", ("999999.SZ",),
                          date(2026, 1, 1), date(2026, 1, 31), {})
    with pytest.raises(ValueError, match="probe-frozen index table"):
        TushareSource(SourceConfig(), client=IndexWeightStub([])).fetch(request)


@pytest.mark.parametrize("january,match", [
    (_weight_rows([["399300.SZ", "000001.SZ", "20260130", "0.3"],
                   ["399300.SZ", "000001.SZ", "20260130", "0.3"]]),
     "duplicate primary-key"),
    (_weight_rows([["399300.SZ", "000001.SZ", "20260302", "0.3"]]),
     "outside the requested date range"),
    (pd.DataFrame([["000001.SZ", "20260130", "0.3"]],
                  columns=["con_code", "trade_date", "weight"]),
     "missing columns: index_code"),  # 缺指数
])
def test_index_weight_classifies_contract_failures(january, match):
    request = DataRequest("index_weight", ("399300.SZ",),
                          date(2026, 1, 1), date(2026, 1, 31), {})
    with pytest.raises(ContractError, match=match):
        TushareSource(SourceConfig(),
                      client=IndexWeightStub([january])).fetch(request)


def _relay_by_day_reference() -> dict:
    from stock_quant.data_model.basic_factor_normalize import PROBE_EVIDENCE_JSON
    document = json.loads(
        (REPO_ROOT / PROBE_EVIDENCE_JSON).read_text(encoding="utf-8"))
    assert document["_status"] == "measured", "probe evidence not measured yet"
    readings = [reading for reading in
                document["probes"]["daily_basic"]["relay"]["readings"]
                if "magnitude_reference" in reading]
    assert readings, "relay by-day reading carries no magnitude_reference"
    return readings[0]["magnitude_reference"]


def test_frozen_readings_match_the_measured_evidence():
    from stock_quant.data_model.basic_factor_normalize import (
        EVIDENCE_REFERENCE_READINGS,
    )
    reference = _relay_by_day_reference()
    assert [dict(r) for r in EVIDENCE_REFERENCE_READINGS] == [{
        "symbol": reference["symbol"], "trade_date": reference["trade_date"],
        "raw_total_mv": reference["raw_total_mv"],
        "raw_turnover_rate": reference["raw_turnover_rate"]}]


def test_magnitude_reference_converts_into_plausible_bands():
    from stock_quant.data_model.basic_factor_normalize import (
        EVIDENCE_REFERENCE_READINGS,
        daily_basic_to_basic_factor_rows,
    )
    assert EVIDENCE_REFERENCE_READINGS
    rows = daily_basic_to_basic_factor_rows(pd.DataFrame(
        [[r["symbol"], r["trade_date"],
          r["raw_total_mv"], r["raw_turnover_rate"]]
         for r in EVIDENCE_REFERENCE_READINGS],
        columns=["ts_code", "trade_date", "total_mv", "turnover_rate"]))
    for value in rows["market_cap"]:  # yuan, large-cap order of magnitude
        assert 1e10 <= value <= 1e12
    for value in rows["turnover_rate"]:  # dimensionless ratio, not percent
        assert 1e-5 <= value <= 0.2
