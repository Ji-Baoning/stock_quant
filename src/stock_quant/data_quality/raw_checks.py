"""Row-level raw checks over canonical frames and missing-row classification.

Cleaning already rejects rows it cannot represent, so these checks guard the
rows that survive into standardized tables: positive prices, non-negative
volume/amount, legal OHLC relationships, unique primary keys, explicit source
provenance and a documented adjustment basis. Missing rows are classified in a
fixed order (design spec §13.3): not listed, delisted, non-trading day,
all-source missing/unknown-or-suspended, primary-only missing, unexplained.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

import pandas as pd

from stock_quant.data_quality.models import (
    CODE_DUPLICATE_CONFLICT,
    CODE_INVALID_OHLC,
    CODE_NEGATIVE_AMOUNT,
    CODE_NEGATIVE_VOLUME,
    CODE_NONPOSITIVE_PRICE,
    CODE_QUARANTINE_MISSING_REASON,
    CODE_SCHEMA_MISMATCH,
    CODE_UNKNOWN_ADJUSTMENT,
    CODE_UNKNOWN_SOURCE,
    MISSING_DELISTED,
    MISSING_NON_TRADING_DAY,
    MISSING_NOT_LISTED,
    MISSING_PRIMARY_SOURCE,
    MISSING_UNEXPLAINED,
    MISSING_UNKNOWN_OR_SUSPENDED,
    TABLE_DAILY_BAR,
    QualityIssue,
    Severity,
)

KNOWN_SUPPLIERS = frozenset({"tushare", "akshare", "baostock"})
DOCUMENTED_ADJUSTMENTS = frozenset({"unadjusted"})

_PRICE_COLUMNS = ("open", "high", "low", "close")


def check_daily_values(
    frame: pd.DataFrame, *, table: str = TABLE_DAILY_BAR
) -> list[QualityIssue]:
    """Return errors for non-positive prices, negative volume/amount, bad OHLC."""
    issues: list[QualityIssue] = []
    for _, row in frame.iterrows():
        symbol = _row_symbol(row)
        trade_date = _row_date(row)
        issues.extend(_value_issues(row, symbol, trade_date, table))
    return issues


def check_primary_key_conflicts(
    frame: pd.DataFrame,
    key: Sequence[str] = ("symbol", "trade_date"),
    *,
    table: str = TABLE_DAILY_BAR,
) -> list[QualityIssue]:
    """Report primary keys that occur more than once in a canonical table."""
    duplicated = frame.duplicated(subset=list(key), keep=False)
    if not duplicated.any():
        return []
    issues: list[QualityIssue] = []
    duplicate_rows = frame.loc[duplicated]
    for _, group in duplicate_rows.groupby(list(key), sort=False):
        if len(group) < 2:
            continue
        first = group.iloc[0]
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_DUPLICATE_CONFLICT,
                table=table,
                symbol=_row_symbol(first),
                trade_date=_row_date(first),
                details={"row_count": int(len(group))},
            )
        )
    return issues


def check_schema(
    frame: pd.DataFrame,
    schema,
    *,
    table: str,
) -> list[QualityIssue]:
    """Report a canonical-frame that does not match its declared Arrow schema."""
    expected = [field.name for field in schema]
    present = list(frame.columns)
    if present == expected:
        return []
    return [
        QualityIssue(
            severity=Severity.ERROR,
            code=CODE_SCHEMA_MISMATCH,
            table=table,
            details={
                "expected": expected,
                "present": present,
                "missing": [name for name in expected if name not in present],
                "extra": [name for name in present if name not in expected],
            },
        )
    ]


def check_provenance(
    frame: pd.DataFrame,
    *,
    table: str = TABLE_DAILY_BAR,
    known_sources: frozenset[str] = KNOWN_SUPPLIERS,
    documented_adjustments: frozenset[str] = DOCUMENTED_ADJUSTMENTS,
) -> list[QualityIssue]:
    """Return errors when a row's source or adjustment basis is not documented."""
    issues: list[QualityIssue] = []
    for _, row in frame.iterrows():
        source = row.get("source")
        adjustment = row.get("adjustment")
        symbol = _row_symbol(row)
        trade_date = _row_date(row)
        if source not in known_sources:
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    code=CODE_UNKNOWN_SOURCE,
                    table=table,
                    symbol=symbol,
                    trade_date=trade_date,
                    details={"source": None if pd.isna(source) else source},
                )
            )
        if adjustment not in documented_adjustments:
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    code=CODE_UNKNOWN_ADJUSTMENT,
                    table=table,
                    symbol=symbol,
                    trade_date=trade_date,
                    details={"adjustment": None if pd.isna(adjustment) else adjustment},
                )
            )
    return issues


def check_quarantine_reasons(
    frame: pd.DataFrame, *, table: str = "quarantine"
) -> list[QualityIssue]:
    """Return errors for isolated records that carry no cleaning reason."""
    if not isinstance(frame, pd.DataFrame):
        return [
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_QUARANTINE_MISSING_REASON,
                table=table,
                details={"reason": "quarantine records are not a frame"},
            )
        ]
    if "reason" not in frame.columns:
        return [
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_QUARANTINE_MISSING_REASON,
                table=table,
                details={"reason_column_missing": True},
            )
        ]
    issues: list[QualityIssue] = []
    for index, row in frame.iterrows():
        if _blank(row.get("reason")):
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    code=CODE_QUARANTINE_MISSING_REASON,
                    table=table,
                    symbol=_row_symbol(row),
                    trade_date=_row_date(row),
                    details={"row": _json_scalar(index)},
                )
            )
    return issues


def classify_missing_row(
    *,
    trade_date: date,
    list_date: date | None = None,
    delist_date: date | None = None,
    is_trading_day: bool = True,
    primary_present: bool = False,
    validation_present: bool = False,
) -> str:
    """Classify an absent (symbol, date) bar using the design-spec precedence."""
    if list_date is not None and trade_date < list_date:
        return MISSING_NOT_LISTED
    if delist_date is not None and trade_date > delist_date:
        return MISSING_DELISTED
    if not is_trading_day:
        return MISSING_NON_TRADING_DAY
    if not primary_present and not validation_present:
        return MISSING_UNKNOWN_OR_SUSPENDED
    if not primary_present:
        return MISSING_PRIMARY_SOURCE
    return MISSING_UNEXPLAINED


def _value_issues(
    row: pd.Series,
    symbol: str | None,
    trade_date: date | None,
    table: str,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    nonpositive = [
        {"field": name, "value": _number(row.get(name))}
        for name in _PRICE_COLUMNS
        if _number(row.get(name)) <= 0
    ]
    if nonpositive:
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_NONPOSITIVE_PRICE,
                table=table,
                symbol=symbol,
                trade_date=trade_date,
                details={"fields": nonpositive},
            )
        )
    volume = _number(row.get("volume"))
    if volume < 0:
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_NEGATIVE_VOLUME,
                table=table,
                symbol=symbol,
                trade_date=trade_date,
                details={"value": volume},
            )
        )
    amount = _number(row.get("amount"))
    if amount < 0:
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_NEGATIVE_AMOUNT,
                table=table,
                symbol=symbol,
                trade_date=trade_date,
                details={"value": amount},
            )
        )
    if not _valid_ohlc(row):
        issues.append(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_INVALID_OHLC,
                table=table,
                symbol=symbol,
                trade_date=trade_date,
                details={
                    name: _number(row.get(name)) for name in _PRICE_COLUMNS
                },
            )
        )
    return issues


def _valid_ohlc(row: pd.Series) -> bool:
    low = _number(row.get("low"))
    high = _number(row.get("high"))
    open_ = _number(row.get("open"))
    close = _number(row.get("close"))
    return low <= open_ <= high and low <= close <= high


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number


def _row_symbol(row: pd.Series) -> str | None:
    value = row.get("symbol")
    if pd.isna(value):
        return None
    return str(value)


def _row_date(row: pd.Series) -> date | None:
    value = row.get("trade_date")
    if value is None or pd.isna(value):
        return None
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        return None
    return timestamp.date()


def _blank(value: Any) -> bool:
    if value is None or pd.isna(value):
        return True
    return str(value).strip() == ""


def _json_scalar(value: Any) -> Any:
    if isinstance(value, (int, float, bool, str)) or value is None:
        return value
    return str(value)
