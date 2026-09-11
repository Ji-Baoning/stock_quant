"""Canonical daily-bar normalization, cleaning, and audit tests."""

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from stock_quant.data_model.normalize import normalize_daily
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_SCHEMA,
    DAILY_COLUMNS,
    DAILY_SCHEMA,
    SECURITY_MASTER_SCHEMA,
    TRADING_CALENDAR_SCHEMA,
)

RAW_FIXTURES = Path(__file__).parents[1] / "fixtures" / "raw"
INGESTED_AT = pd.Timestamp("2020-01-06", tz="UTC")


def _baostock_frame(**overrides):
    rows = {
        "date": ["2020-01-02", "2020-01-03"],
        "code": ["sh.600000", "sh.600000"],
        "open": [10.0, 10.2],
        "high": [11.0, 11.2],
        "low": [9.0, 9.2],
        "close": [10.5, 10.8],
        "volume": [100, 120],
        "amount": [1050.0, 1296.0],
    }
    rows.update(overrides)
    return pd.DataFrame(rows)


def test_daily_normalization_converts_units_and_audits_drops():
    raw = pd.DataFrame(
        {
            "date": ["2020-01-02", "bad"],
            "code": ["sh.600000", "sh.600000"],
            "open": [10, 10],
            "high": [11, 11],
            "low": [9, 9],
            "close": [10.5, 10.5],
            "volume": [100, 100],
            "amount": [1050, 1050],
        }
    )
    result = normalize_daily(
        raw, "baostock", pd.Timestamp("2020-01-03", tz="UTC")
    )

    assert result.valid.iloc[0].to_dict()["symbol"] == "600000.SH"
    assert result.valid.iloc[0].to_dict()["volume"] == 100
    assert result.rejected.iloc[0]["reason"] == "invalid_trade_date"


def test_daily_output_has_exact_canonical_column_order_and_dtypes():
    result = normalize_daily(_baostock_frame(), "baostock", INGESTED_AT)

    assert result.rejected.empty
    assert result.audit.empty
    assert result.valid.columns.tolist() == DAILY_COLUMNS
    assert str(result.valid["trade_date"].dtype) == "datetime64[ns]"
    assert str(result.valid["ingested_at"].dtype) == "datetime64[ns, UTC]"
    assert result.valid["volume"].dtype == "int64"
    assert result.valid["close"].dtype == "float64"
    assert result.valid["amount"].dtype == "float64"


def test_daily_rows_carry_source_and_unadjusted_marker():
    result = normalize_daily(_baostock_frame(), "baostock", INGESTED_AT)
    row = result.valid.iloc[0].to_dict()

    assert row["source"] == "baostock"
    assert row["adjustment"] == "unadjusted"
    assert row["ingested_at"] == INGESTED_AT
    assert row["trade_date"] == pd.Timestamp("2020-01-02")


def test_normalize_baostock_raw_fixture():
    raw = pd.read_parquet(RAW_FIXTURES / "baostock_daily.parquet")
    result = normalize_daily(raw, "baostock", INGESTED_AT)

    assert result.rejected.empty
    assert result.valid["symbol"].tolist() == ["600000.SH"] * 3
    assert result.valid["volume"].tolist() == [100, 120, 110]
    assert result.valid["close"].tolist() == [10.5, 10.8, 11.0]


def test_normalize_handles_baostock_all_string_frame_like_real_response():
    raw = pd.DataFrame(
        {
            "date": ["2020-01-02"],
            "code": ["sh.600000"],
            "open": ["10.0"],
            "high": ["11.0"],
            "low": ["9.0"],
            "close": ["10.5"],
            "volume": ["100"],
            "amount": ["1050.0"],
        }
    )
    result = normalize_daily(raw, "baostock", INGESTED_AT)

    assert result.rejected.empty
    assert result.valid.iloc[0]["volume"] == 100
    assert result.valid.iloc[0]["close"] == 10.5


def test_normalize_tushare_converts_lots_and_thousand_yuan_units():
    raw = pd.read_parquet(RAW_FIXTURES / "tushare_daily.parquet")
    result = normalize_daily(raw, "tushare", INGESTED_AT)
    row = result.valid.iloc[0].to_dict()

    assert row["symbol"] == "000001.SZ"
    assert row["volume"] == 10000
    assert row["amount"] == pytest.approx(10500.0)
    assert row["adjustment"] == "unadjusted"
    assert row["source"] == "tushare"


def test_duplicate_identical_rows_collapse_with_audit_record():
    raw = _baostock_frame(
        date=["2020-01-02", "2020-01-02"],
        open=[10.0, 10.0],
        high=[11.0, 11.0],
        low=[9.0, 9.0],
        close=[10.5, 10.5],
        volume=[100, 100],
        amount=[1050.0, 1050.0],
    )
    result = normalize_daily(raw, "baostock", INGESTED_AT)

    assert len(result.valid) == 1
    assert len(result.audit) == 1
    audit = result.audit.iloc[0].to_dict()
    assert audit["rule"] == "duplicate_row_collapse"
    assert audit["symbol"] == "600000.SH"
    assert audit["source"] == "baostock"


def test_duplicate_keys_with_different_content_remain_for_quality():
    raw = _baostock_frame(
        date=["2020-01-02", "2020-01-02"],
        close=[10.5, 10.6],
    )
    result = normalize_daily(raw, "baostock", INGESTED_AT)

    assert len(result.valid) == 2
    assert result.rejected.empty
    assert result.audit.empty
    assert result.valid["close"].tolist() == [10.5, 10.6]


def test_unrecognizable_symbol_is_rejected_with_reason():
    raw = _baostock_frame(code=["sh.600000", "garbage"])
    result = normalize_daily(raw, "baostock", INGESTED_AT)

    assert len(result.valid) == 1
    assert result.rejected.iloc[0]["reason"] == "invalid_symbol"
    assert result.rejected.iloc[0]["code"] == "garbage"


def test_non_numeric_price_rejects_row_but_keeps_original_value():
    raw = _baostock_frame(date=["2020-01-02", "2020-01-03"], close=[10.5, "abc"])
    result = normalize_daily(raw, "baostock", INGESTED_AT)

    assert len(result.valid) == 1
    assert result.rejected.iloc[0]["reason"] == "invalid_number"
    assert result.rejected.iloc[0]["close"] == "abc"


def test_fractional_volume_is_rejected_as_not_representable():
    raw = _baostock_frame(volume=[100, 100.5])
    result = normalize_daily(raw, "baostock", INGESTED_AT)

    assert len(result.valid) == 1
    assert result.rejected.iloc[0]["reason"] == "invalid_volume"
    assert result.rejected.iloc[0]["volume"] == 100.5


def test_tushare_two_decimal_lots_survive_the_share_scaling():
    """Regression: 1263029.64 lots x 100 is 126302963.99999999 in float64.

    The share scaling used to run ``float.is_integer()`` on that product, saw
    the representation artifact and rejected the row as ``invalid_volume`` --
    silently dropping about 11% of real trading days from every published
    dataset and failing the acceptance completeness gate with 8,813 gaps.
    """
    raw = pd.DataFrame(
        {
            "trade_date": ["20150114"],
            "ts_code": ["000001.SZ"],
            "open": [14.78],
            "high": [15.2],
            "low": [14.7],
            "close": [14.81],
            "vol": [1263029.64],
            "amount": [1889296.679],
        }
    )
    result = normalize_daily(raw, "tushare", INGESTED_AT)

    assert result.rejected.empty
    assert len(result.valid) == 1
    assert result.valid.iloc[0].to_dict()["volume"] == 126302964


def test_tushare_fractional_share_volume_is_still_rejected():
    """Lots that scale to fractional shares remain unrepresentable."""
    raw = pd.DataFrame(
        {
            "trade_date": ["20150114"],
            "ts_code": ["000001.SZ"],
            "open": [14.78],
            "high": [15.2],
            "low": [14.7],
            "close": [14.81],
            "vol": [1263029.644],
            "amount": [1889296.679],
        }
    )
    result = normalize_daily(raw, "tushare", INGESTED_AT)

    assert len(result.valid) == 0
    assert result.rejected.iloc[0]["reason"] == "invalid_volume"
    assert result.rejected.iloc[0]["vol"] == 1263029.644


def test_empty_daily_frame_yields_typed_empty_clean_result():
    raw = pd.DataFrame(
        columns=["date", "code", "open", "high", "low", "close", "volume", "amount"]
    )
    result = normalize_daily(raw, "baostock", INGESTED_AT)

    assert result.valid.empty
    assert result.rejected.empty
    assert result.audit.empty
    assert result.valid.columns.tolist() == DAILY_COLUMNS
    assert result.valid["volume"].dtype == "int64"


def test_unknown_source_is_rejected_without_guessing_units():
    with pytest.raises(ValueError, match="source"):
        normalize_daily(_baostock_frame(), "unknown", INGESTED_AT)


def test_frame_missing_required_daily_column_is_rejected():
    raw = _baostock_frame().drop(columns=["amount"])
    with pytest.raises(ValueError, match="amount"):
        normalize_daily(raw, "baostock", INGESTED_AT)


def test_daily_schema_fields_match_canonical_columns_in_order():
    assert [field.name for field in DAILY_SCHEMA] == DAILY_COLUMNS


def test_daily_schema_declares_stable_types():
    assert DAILY_SCHEMA.field("trade_date").type == pa.date32()
    assert DAILY_SCHEMA.field("volume").type == pa.int64()
    assert DAILY_SCHEMA.field("close").type == pa.float64()
    assert DAILY_SCHEMA.field("symbol").type == pa.string()
    assert DAILY_SCHEMA.field("ingested_at").type == pa.timestamp("us", tz="UTC")


def test_corporate_action_schema_exposes_required_fields():
    names = {field.name for field in CORPORATE_ACTION_SCHEMA}
    assert {
        "symbol",
        "announcement_date",
        "record_date",
        "ex_date",
        "cash_dividend_per_share",
        "bonus_share_ratio",
        "capitalization_ratio",
        "rights_issue_ratio",
        "rights_issue_price",
        "source",
        "status",
    } <= names
    assert CORPORATE_ACTION_SCHEMA.field("ex_date").type == pa.date32()
    assert CORPORATE_ACTION_SCHEMA.field("symbol").type == pa.string()


def test_security_master_schema_exposes_listing_fields():
    names = {field.name for field in SECURITY_MASTER_SCHEMA}
    assert {"symbol", "name", "exchange", "board", "list_date", "delist_date"} <= names
    assert SECURITY_MASTER_SCHEMA.field("symbol").type == pa.string()
    assert SECURITY_MASTER_SCHEMA.field("list_date").type == pa.date32()


def test_trading_calendar_schema_exposes_calendar_day_and_open_flag():
    names = {field.name for field in TRADING_CALENDAR_SCHEMA}
    assert {"calendar_date", "is_trading_day"} <= names
    assert TRADING_CALENDAR_SCHEMA.field("calendar_date").type == pa.date32()
    assert TRADING_CALENDAR_SCHEMA.field("is_trading_day").type == pa.bool_()
