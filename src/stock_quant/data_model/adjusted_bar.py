"""Point-in-time total-return bars from unadjusted closes (Task 1).

``build_adjusted_bars`` derives the research-only ``adjusted_bar`` table from
the canonical unadjusted ``daily_bar`` closes plus the canonical corporate
action, quarantine and coverage frames.  Only *implemented*, complete and
point-in-time cash-dividend / bonus / capitalization events enter the
recursion; every untrusted transition becomes an ERROR break that re-anchors
the series instead of silently falling back to the unadjusted close.

Recursion per symbol, with ``P`` the unadjusted close, ``d`` the ex-date cash
dividend per share, ``b`` the bonus ratio and ``c`` the capitalization ratio::

    TR_t = TR_{t-1} * (P_t * (1 + b_t + c_t) + d_t) / P_{t-1}

Events act for the first time on their ``ex_date``, so a newly discovered
future action can never change any row before that date.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Sequence

import pandas as pd

from stock_quant.data_model.schemas import ADJUSTED_BAR_COLUMNS

ADJUSTMENT_NAME = "internal_total_return_v1"
QUALITY_INFO = "INFO"
QUALITY_ERROR = "ERROR"


def build_adjusted_bars(
    daily_bar: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    quarantined_actions: pd.DataFrame,
    coverage: pd.DataFrame,
    *,
    symbols: Sequence[str],
) -> pd.DataFrame:
    """Build the total-return series for ``symbols`` from canonical inputs."""
    rows = daily_bar[daily_bar["symbol"].isin(set(symbols))].copy()
    if rows.empty:
        return pd.DataFrame(columns=ADJUSTED_BAR_COLUMNS)
    return _build_all_symbols(rows, corporate_actions, quarantined_actions, coverage)


def _build_all_symbols(
    rows: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    quarantined_actions: pd.DataFrame,
    coverage: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for symbol in sorted(set(rows["symbol"])):
        bars = rows[rows["symbol"] == symbol]
        trading_days = sorted(
            day
            for day in (_as_date(value) for value in bars["trade_date"])
            if day is not None
        )
        actions, breaks = _accepted_actions_and_breaks(
            corporate_actions, quarantined_actions, coverage, symbol, trading_days
        )
        records.extend(_build_symbol(bars, actions, breaks))
    frame = pd.DataFrame(records, columns=ADJUSTED_BAR_COLUMNS)
    return frame.sort_values(["trade_date", "symbol"], kind="stable").reset_index(
        drop=True
    )


def _accepted_actions_and_breaks(
    actions: pd.DataFrame,
    quarantined: pd.DataFrame,
    coverage: pd.DataFrame,
    symbol: str,
    trading_days: Sequence[date],
) -> tuple[pd.DataFrame, dict[date, str]]:
    """Split a symbol's actions into accepted events and trust breaks.

    Only ``implemented``, complete, supported and point-in-time events are
    accepted; anything else, any quarantined row, and any UNTRUSTED coverage
    window becomes an ERROR break on its ``ex_date`` / covered trading day.
    """
    accepted: list[dict] = []
    breaks: dict[date, str] = {}
    for item in _records_for(actions, symbol):
        ex_date = _as_date(item.get("ex_date"))
        announcement = _as_date(item.get("announcement_date"))
        if str(item.get("status")) != "implemented":
            breaks[ex_date] = "corporate_action_not_implemented"
        elif ex_date is None or announcement is None or announcement > ex_date:
            if ex_date is not None:
                breaks[ex_date] = "corporate_action_not_point_in_time"
        elif _zero(item.get("rights_issue_ratio")) > 0:
            breaks[ex_date] = "unsupported_corporate_action"
        else:
            accepted.append(item)
    _merge_quarantine_breaks(breaks, quarantined, symbol)
    _overlay_untrusted_coverage(breaks, coverage, symbol, trading_days)
    return pd.DataFrame(accepted), breaks


def _merge_quarantine_breaks(
    breaks: dict[date, str],
    quarantined: pd.DataFrame,
    symbol: str,
) -> None:
    """Merge a symbol's quarantined rows into ``breaks`` by ``ex_date``.

    Several quarantined rows may share one ``ex_date``.  To stay independent
    of the input row order, the lexicographically smallest ``reason`` wins;
    a quarantine day always overrides a same-day accepted-loop break reason.
    """
    reasons: dict[date, str] = {}
    for item in _records_for(quarantined, symbol):
        ex_date = _as_date(item.get("ex_date"))
        if ex_date is None:
            continue
        reason = str(item.get("reason") or "corporate_action_untrusted")
        current = reasons.get(ex_date)
        if current is None or reason < current:
            reasons[ex_date] = reason
    breaks.update(reasons)


def _overlay_untrusted_coverage(
    breaks: dict[date, str],
    coverage: pd.DataFrame,
    symbol: str,
    trading_days: Sequence[date],
) -> None:
    """Mark every trading day inside an UNTRUSTED coverage window as a break.

    Exact quarantine reasons already recorded on their ``ex_date`` win over
    this window-level reason (``setdefault`` never overwrites).
    """
    if coverage.empty:
        return
    for item in _records_for(coverage, symbol):
        if str(item.get("status")) != "UNTRUSTED":
            continue
        start = _as_date(item.get("window_start"))
        end = _as_date(item.get("window_end"))
        for day in trading_days:
            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue
            breaks.setdefault(day, "corporate_action_coverage_untrusted")


def _records_for(frame: pd.DataFrame, symbol: str) -> list[dict]:
    """The frame's records for one symbol (empty frames yield nothing)."""
    if frame.empty or "symbol" not in frame.columns:
        return []
    return frame[frame["symbol"] == symbol].to_dict("records")


def _zero(value: object) -> float:
    """Coerce a corporate-action fact to ``float`` with missing values at 0.

    Canonical frames store missing facts as float64 ``NaN`` (truthy!), so the
    ``value or 0`` idiom would poison the recursion with ``NaN``.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(number) else number


def _build_symbol(
    bars: pd.DataFrame, actions: pd.DataFrame, breaks: dict[date, str]
) -> list[dict[str, object]]:
    previous_close = None
    total_return = None
    records: list[dict[str, object]] = []
    action_by_day = _actions_by_ex_date(actions)
    for row in bars.sort_values("trade_date", kind="stable").to_dict("records"):
        day = _as_date(row["trade_date"])
        raw_close = float(row["close"])
        reason = breaks.get(day, "")
        day_actions = action_by_day.get(day, ())
        ids: list[str] = []
        if previous_close is None or reason:
            total_return = raw_close
        else:
            cash = sum(_zero(item["cash_dividend_per_share"]) for item in day_actions)
            share_ratio = sum(
                _zero(item["bonus_share_ratio"]) + _zero(item["capitalization_ratio"])
                for item in day_actions
            )
            total_return *= (raw_close * (1.0 + share_ratio) + cash) / previous_close
            ids = sorted(
                {action_id_of(str(item["symbol"]), day) for item in day_actions}
            )
        records.append(
            {
                "trade_date": day,
                "symbol": str(row["symbol"]),
                "source": str(row["source"]),
                "adjustment": ADJUSTMENT_NAME,
                "raw_close": raw_close,
                "adjusted_close": float(total_return),
                "adjustment_factor": float(total_return) / raw_close,
                "quality_severity": QUALITY_ERROR if reason else QUALITY_INFO,
                "invalid_reason": reason,
                "applied_action_ids": json.dumps(
                    ids, ensure_ascii=False, separators=(",", ":")
                ),
            }
        )
        previous_close = raw_close
    return records


def action_id_of(symbol: str, ex_date: date) -> str:
    """The deterministic action id ``{symbol}#{ex_date:%Y-%m-%d}``."""
    return f"{symbol}#{ex_date.isoformat()}"


def _actions_by_ex_date(actions: pd.DataFrame) -> dict[date, list[dict]]:
    """Group accepted action records by their ``ex_date``."""
    grouped: dict[date, list[dict]] = {}
    if actions.empty:
        return grouped
    for item in actions.to_dict("records"):
        ex_date = _as_date(item.get("ex_date"))
        if ex_date is not None:
            grouped.setdefault(ex_date, []).append(item)
    return grouped


def _as_date(value: object) -> date | None:
    """Normalize a frame cell to ``datetime.date`` (``None`` when missing)."""
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    return stamp.date()
