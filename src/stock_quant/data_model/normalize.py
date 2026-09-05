"""Deterministic normalization of supplier-native daily frames.

``normalize_daily`` converts one supplier's raw daily frame (Task 2) into the
canonical daily columns published by ``DAILY_COLUMNS``. Rows that cannot be
parsed or normalized are rejected with their original values and a ``reason``;
identical duplicate rows collapse with an audit record; duplicate keys whose
content differs are deliberately kept for the quality layer to reject.
"""

from __future__ import annotations

import json

import pandas as pd

from stock_quant.data_model.clean import (
    REASON_INVALID_NUMBER,
    REASON_INVALID_SYMBOL,
    REASON_INVALID_TRADE_DATE,
    REASON_INVALID_VOLUME,
    RULE_DUPLICATE_ROW_COLLAPSE,
    CleanResult,
    parse_number,
    parse_trade_date,
)
from stock_quant.data_model.schemas import AUDIT_COLUMNS, DAILY_COLUMNS
from stock_quant.data_model.symbols import SymbolNormalizationError, normalize_symbol

_SYMBOL_COLUMNS = ("code", "ts_code", "symbol")
_DATE_COLUMNS = ("date", "trade_date")
_VOLUME_COLUMNS = ("volume", "vol")
_NUMERIC_COLUMNS = ("open", "high", "low", "close", "amount")

# Canonical units are shares for volume and yuan for amount. Suppliers whose
# documented raw units differ (for example Tushare reporting volume in lots and
# amount in thousand-yuan) declare a deterministic scale factor here.
_UNIT_FACTORS = {
    "tushare": (100, 1000),
    "baostock": (1, 1),
}

_UNADJUSTED = "unadjusted"


def normalize_daily(
    frame: pd.DataFrame, source: str, ingested_at: object
) -> CleanResult:
    """Normalize one supplier-native daily frame into canonical daily bars."""
    if source not in _UNIT_FACTORS:
        raise ValueError(f"no documented daily layout for source {source!r}")
    _require_daily_columns(frame)
    volume_factor, amount_factor = _UNIT_FACTORS[source]
    timestamp = _as_utc(ingested_at)

    symbol_column = _first_present(frame, _SYMBOL_COLUMNS)
    date_column = _first_present(frame, _DATE_COLUMNS)
    volume_column = _first_present(frame, _VOLUME_COLUMNS)

    valid_records: list[dict[str, object]] = []
    rejected_indices: list[object] = []
    rejected_reasons: list[str] = []

    for index, row in frame.iterrows():
        try:
            symbol = normalize_symbol(row[symbol_column], source)
        except SymbolNormalizationError:
            rejected_indices.append(index)
            rejected_reasons.append(REASON_INVALID_SYMBOL)
            continue

        trade_date = parse_trade_date(row[date_column])
        if trade_date is None:
            rejected_indices.append(index)
            rejected_reasons.append(REASON_INVALID_TRADE_DATE)
            continue

        numbers: dict[str, float] = {}
        invalid = False
        for column in _NUMERIC_COLUMNS:
            parsed = parse_number(row[column])
            if parsed is None:
                rejected_indices.append(index)
                rejected_reasons.append(REASON_INVALID_NUMBER)
                invalid = True
                break
            numbers[column] = parsed
        if invalid:
            continue

        raw_volume = parse_number(row[volume_column])
        if raw_volume is None:
            rejected_indices.append(index)
            rejected_reasons.append(REASON_INVALID_VOLUME)
            continue
        scaled_volume = raw_volume * volume_factor
        if not scaled_volume.is_integer():
            rejected_indices.append(index)
            rejected_reasons.append(REASON_INVALID_VOLUME)
            continue
        volume = int(scaled_volume)

        valid_records.append(
            {
                "trade_date": trade_date,
                "symbol": symbol,
                "open": numbers["open"],
                "high": numbers["high"],
                "low": numbers["low"],
                "close": numbers["close"],
                "volume": volume,
                "amount": numbers["amount"] * amount_factor,
                "adjustment": _UNADJUSTED,
                "source": source,
                "ingested_at": timestamp,
            }
        )

    valid = _finalize_daily_frame(valid_records)
    valid, audit_rows = _collapse_identical_duplicates(valid, source, timestamp)
    audit = pd.DataFrame(audit_rows, columns=AUDIT_COLUMNS)
    rejected = _finalize_rejected_frame(frame, rejected_indices, rejected_reasons)
    return CleanResult(valid=valid, rejected=rejected, audit=audit)


def _require_daily_columns(frame: pd.DataFrame) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    symbol_column = _first_present(frame, _SYMBOL_COLUMNS)
    date_column = _first_present(frame, _DATE_COLUMNS)
    volume_column = _first_present(frame, _VOLUME_COLUMNS)
    present = set(frame.columns)
    missing = [
        name
        for name, found in (
            ("symbol", symbol_column is not None and symbol_column in present),
            ("date", date_column is not None and date_column in present),
            ("volume", volume_column is not None and volume_column in present),
            *((column, column in present) for column in _NUMERIC_COLUMNS),
        )
        if not found
    ]
    if missing:
        names = ", ".join(missing)
        raise ValueError(f"frame is missing required daily columns: {names}")


def _finalize_daily_frame(records: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(records, columns=DAILY_COLUMNS)
    for column in ("open", "high", "low", "close", "amount"):
        frame[column] = frame[column].astype("float64")
    frame["volume"] = frame["volume"].astype("int64")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["ingested_at"] = pd.to_datetime(frame["ingested_at"], utc=True)
    return frame


def _collapse_identical_duplicates(
    frame: pd.DataFrame, source: str, timestamp: pd.Timestamp
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    duplicates = frame.duplicated(keep="first")
    if not duplicates.any():
        return frame, []
    dropped = frame.loc[duplicates]
    audit_rows = []
    for _, row in dropped.iterrows():
        audit_rows.append(
            {
                "source": source,
                "symbol": row["symbol"],
                "trade_date": row["trade_date"].date(),
                "rule": RULE_DUPLICATE_ROW_COLLAPSE,
                "action": "collapsed",
                "old_value": "",
                "new_value": _serialize(row),
                "ingested_at": timestamp,
            }
        )
    kept = frame.loc[~duplicates].copy().reset_index(drop=True)
    return kept, audit_rows


def _finalize_rejected_frame(
    frame: pd.DataFrame,
    rejected_indices: list[object],
    rejected_reasons: list[str],
) -> pd.DataFrame:
    rejected = frame.loc[rejected_indices].copy()
    rejected["reason"] = rejected_reasons
    return rejected


def _serialize(row: pd.Series) -> str:
    return json.dumps(row.to_dict(), sort_keys=True, default=str)


def _first_present(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in frame.columns), None)


def _as_utc(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")
