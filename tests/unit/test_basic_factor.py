"""basic_factor normalization, coverage rows and the trust filter (§7.2/7.3)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from stock_quant.data_model.basic_factor import (
    MARKET_CAP_UNIT_FACTOR,
    TURNOVER_RATE_UNIT_DIVISOR,
    basic_factor_join_issues,
    build_basic_factor_coverage,
    normalize_basic_factor,
    untrusted_coverage_rows,
)
from stock_quant.data_model.fetch_coverage import (
    KIND_NOT_FETCHED,
    NOT_FETCHED_SOURCE_DISABLED,
)
from stock_quant.data_model.universe_membership import SecurityMasterBoundary
from stock_quant.data_pipeline import (
    _basic_factor_fetch_segments,
    _snapshot_transport,
)
from stock_quant.data_sources.raw_store import RawSnapshot

INGESTED = pd.Timestamp("2026-10-01T08:00:00Z")


def _raw(**overrides):
    row = {
        "ts_code": "600000.SH", "trade_date": "20260930",
        "total_mv": 15000_0000.0, "turnover_rate": 1.25,
        # OHLCV/amount 不该被复制：
        "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
        "amount": 999.0, "vol": 100,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_units_come_from_the_probe_and_nulls_stay_null():
    frame = normalize_basic_factor(_raw(), ingested_at=INGESTED)
    assert frame["market_cap"].tolist() == [15000_0000.0 * MARKET_CAP_UNIT_FACTOR]
    assert frame["turnover_rate"].tolist() == [1.25 / TURNOVER_RATE_UNIT_DIVISOR]
    assert frame.columns.tolist() == [
        "trade_date", "symbol", "market_cap", "turnover_rate",
        "source", "ingested_at",
    ]
    assert frame["source"].tolist() == ["tushare"]
    gap = normalize_basic_factor(
        _raw(total_mv=float("nan"), turnover_rate=None), ingested_at=INGESTED
    )
    assert gap["market_cap"].isna().all() and gap["turnover_rate"].isna().all()


def test_missing_factor_row_lands_untrusted_facts_incomplete():
    bar = pd.DataFrame(
        {"trade_date": [date(2026, 9, 29), date(2026, 9, 30)],
         "symbol": ["600000.SH", "600000.SH"]}
    )
    factor = normalize_basic_factor(
        _raw(trade_date="20260929"), ingested_at=INGESTED
    )
    coverage = build_basic_factor_coverage(
        window_start=date(2026, 9, 29), window_end=date(2026, 9, 30),
        factor=factor, daily_bar=bar, master={}, checked_at=INGESTED,
    )
    assert coverage.iloc[0]["status"] == "UNTRUSTED"
    assert coverage.iloc[0]["reason"] == "FACTS_INCOMPLETE"
    assert len(untrusted_coverage_rows(coverage)) == 1


def test_verified_empty_requires_master_evidence_and_filter_is_equality():
    empty = pd.DataFrame(columns=["trade_date", "symbol"])
    master = {"600000.SH": SecurityMasterBoundary(
        "600000.SH", list_date=date(2026, 10, 1)
    )}
    coverage = build_basic_factor_coverage(
        window_start=date(2026, 9, 29), window_end=date(2026, 9, 30),
        factor=empty, daily_bar=empty, master=master, checked_at=INGESTED,
    )
    assert coverage.iloc[0]["status"] == "VERIFIED_EMPTY"
    assert untrusted_coverage_rows(coverage).empty
    mixed = pd.DataFrame({"status": ["UNTRUSTED", "VERIFIED", "VERIFIED_EMPTY"]})
    # 只许 == "UNTRUSTED"；!= "VERIFIED" 会把 VERIFIED_EMPTY 误判（§7.3）
    assert untrusted_coverage_rows(mixed)["status"].tolist() == ["UNTRUSTED"]


def test_no_anchor_whole_window_disabled_record_falls_back_to_window_start():
    """无 configs/universes 的工程没有 acceptance anchor（P2c Task 2 回归）。

    anchor=None + 基线无该表（covered=None）+ 本轮无可答事实
    （supported_start=None）时，wired lane 必须落回 Task 1 的整窗
    not_fetched/source_disabled 记录，窗口起点用回退锚（请求窗起点），
    而不是在段构造时抛 TypeError。
    """
    start, end = date(2026, 9, 1), date(2026, 9, 30)
    segments = _basic_factor_fetch_segments(
        "basic_factor",
        covered=None,
        anchor=None,
        end=end,
        supported_start=None,
        fetched_start=start,
        start=start,
    )
    assert [(s.kind, s.window_start, s.window_end, s.reason) for s in segments] == [
        (KIND_NOT_FETCHED, start, end, NOT_FETCHED_SOURCE_DISABLED)
    ]


def test_anchored_whole_window_disabled_record_keeps_the_anchor():
    """有锚工程的行为不变：整窗 disabled 记录仍从 acceptance anchor 起步。"""
    anchor, end = date(2026, 9, 15), date(2026, 9, 30)
    segments = _basic_factor_fetch_segments(
        "basic_factor",
        covered=None,
        anchor=anchor,
        end=end,
        supported_start=None,
        fetched_start=anchor,
        start=date(2026, 9, 1),
    )
    assert [(s.kind, s.window_start, s.window_end, s.reason) for s in segments] == [
        (KIND_NOT_FETCHED, anchor, end, NOT_FETCHED_SOURCE_DISABLED)
    ]


def _bar():
    return pd.DataFrame(
        {"trade_date": [date(2026, 9, 29), date(2026, 9, 30)],
         "symbol": ["600000.SH", "600000.SH"]}
    )


def _factor():
    return pd.DataFrame({"trade_date": [date(2026, 9, 29)],
                         "symbol": ["600000.SH"]})


def _covering_coverage(symbol="600000.SH"):
    return pd.DataFrame(
        [{"symbol": symbol, "window_start": date(2026, 9, 1),
          "window_end": date(2026, 9, 30), "status": "UNTRUSTED"}]
    )


def test_duplicate_key_and_join_expansion_fail_the_round():
    dup_bar = pd.DataFrame(
        {"trade_date": [date(2026, 9, 29), date(2026, 9, 29)],
         "symbol": ["600000.SH", "600000.SH"]}
    )
    issues = basic_factor_join_issues(dup_bar, _factor(), _covering_coverage())
    codes = [i.code for i in issues]
    assert "basic_factor_join_duplicate" in codes
    # 2 x 1 同键内联接得 2 行 > len(factor) == 1：扩行可见
    assert "basic_factor_join_expansion" in codes


def test_row_set_diff_without_a_coverage_row_fails():
    issues = basic_factor_join_issues(
        _bar(), _factor(), _covering_coverage(symbol="000001.SZ"))
    missing = [i for i in issues
               if i.code == "basic_factor_coverage_row_missing"]
    assert missing and missing[0].symbol == "600000.SH"
    assert missing[0].trade_date == date(2026, 9, 30)


def test_covered_diff_and_clean_join_produce_no_issue():
    assert basic_factor_join_issues(
        _bar(), _factor(), _covering_coverage()) == []


def test_supplier_label_precedes_stub_normalization():
    """钉死 _snapshot_transport 的优先级不变量：规则①恒先于规则②。

    证据行同时带审计标签（supplier_endpoint="tushare_proxy.<endpoint>"）
    与标准 stub 形态（transport_id == source）时，kind 必须由标签判为
    proxy，不得被 transport_id==source 的 stub 归一改判成 relay。真实
    transport 恒携带审计标签，规则②只触达标准 stub 形态——这是函数的
    长期成立条件，不是实现顺序巧合。
    """
    snapshot = RawSnapshot(
        path=Path("data/raw/tushare/daily_basic/tushare/probe"),
        sha256="0" * 64,
        manifest={
            "source": "tushare",
            "endpoint": "daily_basic",
            "transport_id": "tushare",
            "supplier_endpoint": "tushare_proxy.daily_basic",
            "request_key": "probe",
        },
    )
    transport = _snapshot_transport(snapshot)
    assert transport == "tushare:proxy"
    assert transport != "tushare:relay"
