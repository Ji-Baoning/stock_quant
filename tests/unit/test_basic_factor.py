"""basic_factor normalization, coverage rows and the trust filter (§7.2/7.3)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from stock_quant.data_model.basic_factor import (
    MARKET_CAP_UNIT_FACTOR,
    TURNOVER_RATE_UNIT_DIVISOR,
    build_basic_factor_coverage,
    normalize_basic_factor,
    untrusted_coverage_rows,
)
from stock_quant.data_model.universe_membership import SecurityMasterBoundary

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
