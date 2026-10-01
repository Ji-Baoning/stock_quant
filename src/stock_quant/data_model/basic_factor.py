"""daily_basic -> basic_factor normalization and coverage evidence (§7.2/7.3).

Pure functions only (§6.3): no I/O, no wall-clock reads.  Unit constants are
frozen from the P1 probe evidence and cite it; the probe is the only
authority (§6.4).  Coverage vocabulary is reused verbatim from
corporate_action_coverage -- no new status codes (§7.3).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from stock_quant.data_model.clean import parse_trade_date
from stock_quant.data_model.corporate_action_coverage import (
    OUTCOME_SUCCESS_EVENTS,
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_model.schemas import BASIC_FACTOR_COLUMNS
from stock_quant.data_model.symbols import (
    SymbolNormalizationError,
    normalize_symbol,
)
from stock_quant.data_model.universe_membership import SecurityMasterBoundary
from stock_quant.data_quality.models import (
    CODE_BASIC_FACTOR_COVERAGE_ROW_MISSING,
    CODE_BASIC_FACTOR_JOIN_DUPLICATE,
    CODE_BASIC_FACTOR_JOIN_EXPANSION,
    QualityIssue,
    Severity,
)

#: FROZEN FROM PROBE EVIDENCE (spec §6.4):
#: docs/operations/2026-10-01-endpoint-probe-evidence.md -- daily_basic
#: returns total_mv in wan-yuan (×10000 -> yuan) and turnover_rate in percent
#: (÷100 -> fraction).  The probe wins over any naming-based guess; do not
#: edit without new dated evidence.
MARKET_CAP_UNIT_FACTOR = 10_000.0
TURNOVER_RATE_UNIT_DIVISOR = 100.0

BASIC_FACTOR_SOURCE = "tushare"
JOIN_KEY = ("trade_date", "symbol")


def normalize_basic_factor(
    raw: pd.DataFrame, *, ingested_at: pd.Timestamp
) -> pd.DataFrame:
    """One daily_basic response frame -> canonical basic_factor rows.

    Only the four evidence columns are read (OHLCV/amount never enter);
    missing stays NaN (null at publish) -- fill-zero is forbidden (§7.2).
    Unparseable rows drop here and resurface as FACTS_INCOMPLETE via the
    coverage builder's key-set comparison.
    """
    records: list[dict[str, Any]] = []
    for row in raw.to_dict("records"):
        trade_date = parse_trade_date(row.get("trade_date"))
        try:
            symbol = normalize_symbol(row.get("ts_code"), "tushare")
        except SymbolNormalizationError:
            symbol = None
        if trade_date is None or symbol is None:
            continue
        records.append({
            "trade_date": trade_date,
            "symbol": symbol,
            "market_cap": _scaled(row.get("total_mv"), MARKET_CAP_UNIT_FACTOR),
            "turnover_rate": _scaled(
                row.get("turnover_rate"), 1.0 / TURNOVER_RATE_UNIT_DIVISOR),
            "source": BASIC_FACTOR_SOURCE,
            "ingested_at": ingested_at,
        })
    frame = pd.DataFrame(records, columns=BASIC_FACTOR_COLUMNS)
    return frame.sort_values(list(JOIN_KEY), kind="stable").reset_index(drop=True)


def build_basic_factor_coverage(
    *,
    window_start: date,
    window_end: date,
    factor: pd.DataFrame,
    daily_bar: pd.DataFrame,
    master: Mapping[str, SecurityMasterBoundary],
    failed_days: Sequence[date] = (),
    request_days_missing: bool = False,
    checked_at: object = None,
) -> pd.DataFrame:
    """Per symbol x window coverage rows (§7.3 decision table).

    ``daily_bar`` is the observable expected set (bar-present days);
    ``master`` is the ONLY evidence that may justify VERIFIED_EMPTY
    (not-listed / delisted), matching date_window_completeness practice.
    """
    bar_days = pd.to_datetime(daily_bar["trade_date"], errors="coerce")
    bar = daily_bar[
        (bar_days >= pd.Timestamp(window_start))
        & (bar_days <= pd.Timestamp(window_end))
    ]
    expected = _days_by_symbol(bar)
    factor_days = _days_by_symbol(factor)
    rows = []
    for symbol in sorted(set(expected) | set(factor_days) | set(master)):
        status, reason = _coverage_status(
            expected=expected.get(symbol, set()),
            present=factor_days.get(symbol, set()),
            boundary=master.get(symbol),
            window_start=window_start,
            window_end=window_end,
            failed=bool(failed_days),
            request_days_missing=request_days_missing,
        )
        rows.append(coverage_record(
            symbol, window_start, window_end, status, reason,
            sources=[{"endpoint": "daily_basic",
                      "outcome": OUTCOME_SUCCESS_EVENTS}],
            checked_at=checked_at,
        ))
    return coverage_frame(rows)


def _days_by_symbol(frame: pd.DataFrame) -> dict[str, set[date]]:
    days: dict[str, set[date]] = {}
    for record in frame.to_dict("records"):
        day = pd.Timestamp(record["trade_date"]).date()
        days.setdefault(str(record["symbol"]), set()).add(day)
    return days


def _coverage_status(
    *, expected, present, boundary, window_start, window_end,
    failed, request_days_missing,
):
    if failed:
        return CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_FETCH_FAILED
    if request_days_missing:
        return CoverageStatus.UNTRUSTED, CoverageReason.COVERAGE_INCOMPLETE
    if expected - present:
        return CoverageStatus.UNTRUSTED, CoverageReason.FACTS_INCOMPLETE
    if not expected and boundary is not None and (
        boundary.list_date is not None and boundary.list_date > window_end
        or boundary.last_tradable_date is not None
        and boundary.last_tradable_date < window_start
    ):
        return CoverageStatus.VERIFIED_EMPTY, None
    return CoverageStatus.VERIFIED, None


def untrusted_coverage_rows(coverage: pd.DataFrame) -> pd.DataFrame:
    """The ONLY legal downstream trust filter (§7.3): equality against
    ``UNTRUSTED`` -- never ``!= VERIFIED``, which mis-scores VERIFIED_EMPTY
    ("confirmed no facts") as a failure."""
    return coverage[coverage["status"] == CoverageStatus.UNTRUSTED.value]


def _scaled(value: Any, factor: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if pd.isna(number):
        return float("nan")
    return number * factor


def basic_factor_join_issues(
    daily_bar: pd.DataFrame,
    basic_factor: pd.DataFrame,
    coverage: pd.DataFrame,
) -> list[QualityIssue]:
    """One-to-one (trade_date, symbol) between daily_bar and basic_factor.

    §7.2: duplicated keys on either side, a merge that expands rows, or a
    daily-bar row with no basic_factor row and no covering UNTRUSTED
    coverage row are quality errors -- never silently absorbed by a left
    join.  All three are cross-table invariants: FATAL global-process codes
    no tier may waive.

    The row-set difference is judged on the ``daily_bar`` side only:
    basic_factor days without a daily_bar row are the coverage decision
    table's own domain (factor support legitimately precedes the bar
    baseline's first session and a VERIFIED row blesses it), while a bar
    day lacking its factor row is exactly the silent loss the left join
    would otherwise absorb.
    """
    issues: list[QualityIssue] = []
    key = list(JOIN_KEY)
    for side, frame in (("daily_bar", daily_bar),
                        ("basic_factor", basic_factor)):
        count = int(frame.duplicated(subset=key).sum())
        if count:
            issues.append(QualityIssue(
                severity=Severity.FATAL,
                code=CODE_BASIC_FACTOR_JOIN_DUPLICATE,
                table="basic_factor",
                details={"side": side, "duplicate_rows": count},
            ))
    # A carried frame reads back ``datetime64`` while a normalized frame
    # holds ``date`` objects; pandas refuses a direct mixed-dtype key merge,
    # so both sides are key-normalized first (row counts are unchanged).
    merged = _key_normalized(daily_bar).merge(
        _key_normalized(basic_factor), on=key, how="inner"
    )
    if len(merged) > len(daily_bar) or len(merged) > len(basic_factor):
        issues.append(QualityIssue(
            severity=Severity.FATAL,
            code=CODE_BASIC_FACTOR_JOIN_EXPANSION,
            table="basic_factor",
            details={"merged_rows": int(len(merged)),
                     "daily_bar_rows": int(len(daily_bar)),
                     "basic_factor_rows": int(len(basic_factor))},
        ))
    untrusted = untrusted_coverage_rows(coverage)
    windows = {
        (str(r["symbol"]), pd.Timestamp(r["window_start"]).date(),
         pd.Timestamp(r["window_end"]).date())
        for r in untrusted.to_dict("records")
    }
    for symbol, day in sorted(_key_set(daily_bar) - _key_set(basic_factor)):
        if not any(s == symbol and lo <= day <= hi for s, lo, hi in windows):
            issues.append(QualityIssue(
                severity=Severity.FATAL,
                code=CODE_BASIC_FACTOR_COVERAGE_ROW_MISSING,
                table="basic_factor",
                symbol=symbol,
                trade_date=day,
                details={"side": "daily_bar_only"},
            ))
    return issues


def _key_set(frame: pd.DataFrame) -> set:
    return {
        (str(r["symbol"]), pd.Timestamp(r["trade_date"]).date())
        for r in frame.to_dict("records")
    }


def _key_normalized(frame: pd.DataFrame) -> pd.DataFrame:
    """Both join sides on one ``trade_date`` dtype (datetime64)."""
    return frame.assign(trade_date=pd.to_datetime(frame["trade_date"]))
