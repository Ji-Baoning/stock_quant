"""Materialize suspension bars proven by the primary source's own pre_close chain.

Tushare's ``daily`` response usually omits suspended securities, so a listed
symbol has no row on open days it did not trade.  The same response carries
``pre_close``, the exchange's previous-close reference for every present row.
When the row after a gap chains back to the last close before it, no trade
can have happened on the days in between: the gap is a *proven* suspension,
and its bars are carried deterministically (volume zero, price held at the
last reference).  A chain break that no accepted corporate action explains is
real data loss and blocks publication -- this is the guard that turns
``date_window_completeness`` into an honest completeness proof instead of a
license to backfill whatever is missing.

The same supplier sometimes returns the suspended session as a *row* instead
of omitting it: open/high/low are zero, volume and amount are zero, and
``close`` is carried equal to ``pre_close``.  Such a row is the very object the
chain proof above materializes, so ``canonicalize_supplier_suspensions``
rewrites it to that canonical shape rather than letting it reach the value
checks as a zero-priced bar.  Its narrow signature is what keeps the rewrite
honest: any row carrying a trade, or whose close disagrees with its own
``pre_close`` reference, is left untouched for the value checks to reject.

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

#: Provenance of every bar this module emits: a session the supplier itself
#: reported as untraded (or omitted), carried at its own reference price.
SUSPENSION_SOURCE = "tushare_suspend"

#: Columns the supplier's no-trade signature is read from.
_RAW_PRICE_COLUMNS = ("open", "high", "low")
_RAW_REQUIRED_COLUMNS = ("close", "pre_close", "amount")


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
        run_actions = _actions_in_run(
            actions, symbol, first, _as_date(nxt["trade_date"])
        )
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
                    "source": SUSPENSION_SOURCE,
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

    return _frame_from_records(records), issues


def _frame_from_records(records: list[dict[str, object]]) -> pd.DataFrame:
    """Build a canonical daily frame from suspension-bar records."""
    frame = pd.DataFrame(records, columns=DAILY_COLUMNS)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["volume"] = frame["volume"].astype("int64")
    for column in ("open", "high", "low", "close", "amount"):
        frame[column] = frame[column].astype("float64")
    frame["ingested_at"] = pd.to_datetime(frame["ingested_at"], utc=True)
    return frame


def canonicalize_supplier_suspensions(
    symbol: str,
    valid: pd.DataFrame,
    raw: pd.DataFrame,
    *,
    ingested_at: object,
) -> tuple[pd.DataFrame, list[QualityIssue]]:
    """Rewrite the supplier's own no-trade rows into canonical suspension bars.

    A row the supplier returned for a session with no trade -- zero
    open/high/low, zero volume and amount, and ``close`` carried equal to
    ``pre_close`` -- is not a traded bar and must not reach the value checks as
    a zero-priced one.  It is replaced in place by the same carry the chain
    proof would have materialized: the held reference becomes the bar's OHLC,
    and the row is relabelled ``SUSPENSION_SOURCE``.

    ``valid`` is the symbol's already-normalized daily frame; the returned
    frame preserves its row order and replaces only the matching keys.  A frame
    missing any column the signature needs is returned unchanged, so suppliers
    that omit suspended sessions (every offline stub) are unaffected.
    """
    issues: list[QualityIssue] = []
    if valid is None or valid.empty:
        return valid, issues
    date_column = next(
        (name for name in ("trade_date", "date") if name in raw.columns), None
    )
    volume_column = next(
        (name for name in ("volume", "vol") if name in raw.columns), None
    )
    if date_column is None or volume_column is None:
        return valid, issues
    if any(name not in raw.columns for name in _RAW_REQUIRED_COLUMNS):
        return valid, issues
    if any(name not in raw.columns for name in _RAW_PRICE_COLUMNS):
        return valid, issues

    numbers = {
        name: pd.to_numeric(raw[name], errors="coerce")
        for name in (*_RAW_PRICE_COLUMNS, *_RAW_REQUIRED_COLUMNS)
    }
    volume = pd.to_numeric(raw[volume_column], errors="coerce")
    dates = pd.to_datetime(raw[date_column].astype(str), errors="coerce")
    no_trade = (
        (numbers["open"] == 0)
        & (numbers["high"] == 0)
        & (numbers["low"] == 0)
        & (volume == 0)
        & (numbers["amount"] == 0)
        & (numbers["close"] > 0)
        & (numbers["pre_close"] > 0)
        & ((numbers["close"] - numbers["pre_close"]).abs() <= _TOLERANCE)
    )
    if not bool(no_trade.any()):
        return valid, issues

    replacements: dict[pd.Timestamp, dict[str, object]] = {}
    for position in raw.index[no_trade]:
        trade_date = dates.loc[position]
        if pd.isna(trade_date):
            continue
        replacements[pd.Timestamp(trade_date)] = {
            "trade_date": pd.Timestamp(trade_date),
            "symbol": symbol,
            "open": float(numbers["close"].loc[position]),
            "high": float(numbers["close"].loc[position]),
            "low": float(numbers["close"].loc[position]),
            "close": float(numbers["close"].loc[position]),
            "volume": 0,
            "amount": 0.0,
            "adjustment": "unadjusted",
            "source": SUSPENSION_SOURCE,
            "ingested_at": ingested_at,
        }
    if not replacements:
        return valid, issues

    records = [
        replacements.get(pd.Timestamp(row["trade_date"]), row)
        for row in valid.to_dict("records")
    ]
    replaced = sorted(replacements)
    first, last = replaced[0].date(), replaced[-1].date()
    issues.append(
        QualityIssue(
            severity=Severity.INFO,
            code=CODE_SUSPENSION_ROW,
            table="daily_bar",
            symbol=symbol,
            trade_date=first,
            details={
                "kind": "supplier_no_trade",
                "run": f"{first.isoformat()}..{last.isoformat()}",
                "days": len(replaced),
            },
        )
    )
    return _frame_from_records(records), issues


def _actions_in_run(
    actions: pd.DataFrame, symbol: str, first: date, through: date
) -> list[dict[str, object]]:
    """Accepted actions of ``symbol`` that explain the run starting at ``first``.

    ``through`` is the *resumption* day, one past the run's last absent day --
    not the last absent day itself.  An A-share subscription halts the stock
    through the payment period and sets the ex-date on the resumption day, so
    the action that adjusts that day's reference price sits exactly one day
    beyond the gap.  Stopping at the last absent day would leave every such halt
    reading as an unexplained price break.
    """
    if actions is None or actions.empty or "ex_date" not in actions.columns:
        return []
    matched: list[dict[str, object]] = []
    for record in actions.to_dict("records"):
        if "symbol" in record and str(record.get("symbol")) != symbol:
            continue
        if "status" in record and str(record.get("status")) != "implemented":
            continue
        ex_date = parse_trade_date(record.get("ex_date"))
        if ex_date is not None and first <= ex_date <= through:
            matched.append(record)
    return matched
