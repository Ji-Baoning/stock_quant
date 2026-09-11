"""Materialize suspension bars proven by the primary source's own pre_close chain.

Tushare's ``daily`` response omits suspended securities, so a listed symbol
has no row on open days it did not trade.  The same response carries
``pre_close``, the exchange's previous-close reference for every present row.
When the row after a gap chains back to the last close before it, no trade
can have happened on the days in between: the gap is a *proven* suspension,
and its bars are carried deterministically (volume zero, price held at the
last reference).  A chain break that no accepted corporate action explains is
real data loss and blocks publication -- this is the guard that turns
``date_window_completeness`` into an honest completeness proof instead of a
license to backfill whatever is missing.

Materialized rows are labelled ``source="tushare_suspend"`` so they remain
distinguishable from raw supplier responses everywhere downstream.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from stock_quant.data_model.clean import parse_trade_date
from stock_quant.data_model.schemas import DAILY_COLUMNS
from stock_quant.data_quality.models import (
    CODE_SUSPENSION_ROW,
    CODE_SUSPENSION_RUN_UNVERIFIED,
    CODE_UNEXPLAINED_PRIMARY_GAP,
    QualityIssue,
    Severity,
)

#: A two-decimal price read back from the supplier may differ from the anchor
#: by at most half a cent of representation/rounding noise.
_TOLERANCE = 0.005


def _as_date(value: object) -> date | None:
    """Coerce one frame cell to a ``date``; missing/NaT reads as ``None``."""
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _empty_rows() -> pd.DataFrame:
    frame = pd.DataFrame(columns=DAILY_COLUMNS)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["volume"] = frame["volume"].astype("int64")
    return frame


def suspension_rows(
    symbol: str,
    chain: pd.DataFrame,
    open_days: Sequence[date],
    *,
    list_date: date | None,
    delist_date: date | None,
    actions: pd.DataFrame,
    ingested_at: object,
) -> tuple[pd.DataFrame, list[QualityIssue]]:
    """Prove each absent-day run and materialize the suspension bars it owes.

    ``chain`` is one symbol's primary-source response reduced to
    ``trade_date``/``close``/``pre_close``; ``actions`` are the symbol's
    accepted (reconciled) corporate actions.  Runs at the head or tail of the
    window have no anchoring row on one side, so they are reported as
    unverified and left for the acceptance gate to fail honestly.
    """
    issues: list[QualityIssue] = []
    if chain is None or chain.empty or not open_days:
        return _empty_rows(), issues
    chain = chain.sort_values("trade_date").reset_index(drop=True)
    if "pre_close" not in chain.columns:
        chain = chain.assign(pre_close=pd.NA)

    grid = sorted(
        day
        for day in (_as_date(value) for value in open_days)
        if day is not None
        and (list_date is None or day >= list_date)
        and (delist_date is None or day <= delist_date)
    )
    has_reference = chain["pre_close"].notna() & chain["close"].notna()
    dates = [_as_date(value) for value in chain["trade_date"]]
    position = {day: index for index, day in enumerate(dates) if day is not None}
    absent = [day for day in grid if day not in position]
    if not absent:
        return _empty_rows(), issues

    grid_index = {day: index for index, day in enumerate(grid)}
    runs: list[list[date]] = []
    run: list[date] = []
    for day in absent:
        if run and grid_index[day] == grid_index[run[-1]] + 1:
            run.append(day)
        else:
            if run:
                runs.append(run)
            run = [day]
    if run:
        runs.append(run)

    records: list[dict[str, object]] = []
    for days in runs:
        first, last = days[0], days[-1]
        before = [index for day, index in position.items() if day < first]
        after = [index for day, index in position.items() if day > last]
        if not before or not after:
            issues.append(
                QualityIssue(
                    severity=Severity.WARNING,
                    code=CODE_SUSPENSION_RUN_UNVERIFIED,
                    table="daily_bar",
                    symbol=symbol,
                    trade_date=first,
                    details={"run": f"{first.isoformat()}..{last.isoformat()}"},
                )
            )
            continue
        prev = chain.iloc[max(before)]
        next_index = min(after)
        nxt = chain.iloc[next_index]
        prev_close = float(prev["close"])
        next_pre = (
            float(nxt["pre_close"]) if bool(has_reference.iloc[next_index]) else None
        )
        run_actions = _actions_in_run(actions, symbol, first, last)
        ex_dates = sorted(
            ex
            for ex in (_as_date(row.get("ex_date")) for row in run_actions)
            if ex is not None
        )
        if next_pre is None:
            issues.append(
                QualityIssue(
                    severity=Severity.WARNING,
                    code=CODE_SUSPENSION_RUN_UNVERIFIED,
                    table="daily_bar",
                    symbol=symbol,
                    trade_date=first,
                    details={"run": f"{first.isoformat()}..{last.isoformat()}"},
                )
            )
            continue
        if not ex_dates and abs(next_pre - prev_close) > _TOLERANCE:
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    code=CODE_UNEXPLAINED_PRIMARY_GAP,
                    table="daily_bar",
                    symbol=symbol,
                    trade_date=first,
                    details={
                        "run": f"{first.isoformat()}..{last.isoformat()}",
                        "prev_close": prev_close,
                        "next_pre_close": next_pre,
                    },
                )
            )
            continue
        for day in days:
            price = prev_close if ex_dates and day < ex_dates[0] else next_pre
            records.append(
                {
                    "trade_date": pd.Timestamp(day),
                    "symbol": symbol,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0,
                    "amount": 0.0,
                    "adjustment": "unadjusted",
                    "source": "tushare_suspend",
                    "ingested_at": ingested_at,
                }
            )
        issues.append(
            QualityIssue(
                severity=Severity.INFO,
                code=CODE_SUSPENSION_ROW,
                table="daily_bar",
                symbol=symbol,
                trade_date=first,
                details={
                    "run": f"{first.isoformat()}..{last.isoformat()}",
                    "days": len(days),
                    "action_ex_dates": [ex.isoformat() for ex in ex_dates],
                },
            )
        )

    frame = pd.DataFrame(records, columns=DAILY_COLUMNS)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["volume"] = frame["volume"].astype("int64")
    for column in ("open", "high", "low", "close", "amount"):
        frame[column] = frame[column].astype("float64")
    frame["ingested_at"] = pd.to_datetime(frame["ingested_at"], utc=True)
    return frame, issues


def _actions_in_run(
    actions: pd.DataFrame, symbol: str, first: date, last: date
) -> list[dict[str, object]]:
    """Accepted actions of ``symbol`` whose ex_date falls inside the run."""
    if actions is None or actions.empty or "ex_date" not in actions.columns:
        return []
    matched: list[dict[str, object]] = []
    for record in actions.to_dict("records"):
        if "symbol" in record and str(record.get("symbol")) != symbol:
            continue
        if "status" in record and str(record.get("status")) != "implemented":
            continue
        ex_date = parse_trade_date(record.get("ex_date"))
        if ex_date is not None and first <= ex_date <= last:
            matched.append(record)
    return matched
