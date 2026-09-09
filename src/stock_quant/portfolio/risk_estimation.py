"""Trusted 60-session risk estimation for the buffered portfolio rule.

``estimate_risk`` derives each requested symbol's volatility input from the
final ``policy.risk_lookback_days`` confirmed market sessions ending at the
signal date -- never reading a session after it.  Within that window:

- a *real close* is a row with a finite positive ``adjusted_close``, a
  non-ERROR ``quality_severity`` and no ``missing_reason``;
- a ``suspended_verified`` row carries the previous close forward at a
  positive carried ``adjusted_close``: its path return is exactly zero and it
  never increments the real-close count;
- any other gap -- a missing row, an unknown or unverified missing reason, a
  quality ERROR, a non-positive close, or a suspension with no prior trusted
  close -- invalidates that symbol's risk input with the stable reason
  ``untrusted_missing_observation``;
- a window with no untrusted observation but fewer than
  ``policy.min_risk_observations`` real closes is invalid with
  ``insufficient_real_close_observations``.

Rows after the signal date are rejected from the window outright: a past
signal's risk is immune to the future's arrival, so appending future prices
can never change an already-computed estimate.  The annualized volatility is
the sample standard deviation (``ddof=1``) of
the window's daily path returns times ``sqrt(252)``; the raw value is
preserved and only the applied value passes through the policy's volatility
floor.  Duplicate ``(symbol, trade_date)`` rows, a signal date outside the
confirmed sessions and a malformed
input frame are hard :class:`RiskInputError` failures -- the caller's data
contract is broken, which is different from a symbol whose risk input is
untrusted.  Output rows follow the full symbol string order, never the
supplier row order.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import date
from decimal import Decimal
from typing import Sequence

import numpy as np
import pandas as pd

from stock_quant.portfolio.buffered_models import BufferedRiskWeightedPolicy

#: The exact, ordered columns of every risk-estimate frame.
RISK_ESTIMATE_COLUMNS = (
    "symbol",
    "window_start",
    "window_end",
    "real_close_observations",
    "suspension_carry_days",
    "risk_is_valid",
    "risk_invalid_reason",
    "raw_annualized_volatility",
    "applied_annualized_volatility",
)

#: The exact, ordered input columns ``estimate_risk`` accepts.
RISK_INPUT_COLUMNS = (
    "trade_date",
    "symbol",
    "adjusted_close",
    "quality_severity",
    "missing_reason",
)

#: Stable invalid reasons.  ``untrusted_missing_observation`` covers every
#: untrustable window row: an unknown gap, an unverified missing reason, a
#: quality ERROR, a non-positive close, or a suspension without a prior
#: trusted close.
RISK_INVALID_UNTRUSTED_MISSING = "untrusted_missing_observation"
RISK_INVALID_INSUFFICIENT_REAL_CLOSES = "insufficient_real_close_observations"

#: The only trusted carry evidence: the verified-suspension missing reason
#: whose row carries the previous close forward.
MISSING_REASON_SUSPENDED_VERIFIED = "suspended_verified"

#: Quality severity that marks a row untrusted (never a risk input).
_QUALITY_ERROR = "ERROR"

_TRADING_DAYS_PER_YEAR = 252


class RiskInputError(ValueError):
    """A hard risk-input contract breach (duplicates, future rows, schema)."""


def estimate_risk(
    *,
    signal_date: date,
    symbols: Sequence[str],
    observations: pd.DataFrame,
    market_sessions: Sequence[date],
    policy: BufferedRiskWeightedPolicy,
) -> pd.DataFrame:
    """One risk-estimate row per requested symbol, in symbol order.

    ``observations`` must carry exactly :data:`RISK_INPUT_COLUMNS` and at most
    one row per ``(symbol, trade_date)``; no row may sit after
    ``signal_date``.  ``market_sessions`` are the confirmed trading sessions;
    the window is their final ``policy.risk_lookback_days`` sessions ending at
    ``signal_date``.
    """
    _validate_input_frame(observations)
    requested = [str(symbol) for symbol in symbols]
    if len(set(requested)) != len(requested):
        raise RiskInputError(
            f"duplicate requested symbols: {requested}"
        )
    session_list = sorted({day for day in (_as_date(d) for d in market_sessions)})
    signal = _as_date(signal_date)
    position = bisect_left(session_list, signal)
    if position == len(session_list) or session_list[position] != signal:
        raise RiskInputError(
            f"signal_date {signal.isoformat()} is not a confirmed market session"
        )
    window = tuple(session_list[max(0, position + 1 - policy.risk_lookback_days):
                               position + 1])
    if not window or window[-1] != signal:
        raise RiskInputError(
            f"signal_date {signal.isoformat()} is not a confirmed market session"
        )
    if window[0] > signal:
        raise RiskInputError("the risk window cannot start after the signal date")

    rows = observations.copy()
    rows["trade_date"] = [_as_date(day) for day in rows["trade_date"]]
    rows = rows[rows["symbol"].astype(str).isin(set(requested))]
    # No-lookahead: rows after the signal date are excluded outright.  A
    # past signal's risk must be immune to the future's arrival, so future
    # rows are rejected from the window rather than allowed (or required)
    # to change the estimate.
    rows = rows[rows["trade_date"] <= signal]
    duplicated = rows[rows.duplicated(subset=["symbol", "trade_date"])]
    if not duplicated.empty:
        first = duplicated.iloc[0]
        raise RiskInputError(
            "observations contain duplicate symbol/date rows: "
            f"{first['symbol']} on {first['trade_date'].isoformat()}"
        )
    by_symbol_day: dict[str, dict[date, dict]] = {}
    for row in rows.to_dict("records"):
        by_symbol_day.setdefault(str(row["symbol"]), {})[row["trade_date"]] = row

    records: list[dict] = []
    for symbol in sorted(requested):
        records.append(
            _estimate_symbol(
                symbol=symbol,
                signal_date=signal,
                window=window,
                by_day=by_symbol_day.get(symbol, {}),
                policy=policy,
            )
        )
    return pd.DataFrame(records, columns=list(RISK_ESTIMATE_COLUMNS))


def _estimate_symbol(
    *,
    symbol: str,
    signal_date: date,
    window: tuple[date, ...],
    by_day: dict[date, dict],
    policy: BufferedRiskWeightedPolicy,
) -> dict:
    """The trusted 60-session risk path for one symbol (no lookahead)."""
    record: dict = {
        "symbol": symbol,
        "window_start": window[0],
        "window_end": window[-1],
        "real_close_observations": 0,
        "suspension_carry_days": 0,
        "risk_is_valid": False,
        "risk_invalid_reason": "",
        "raw_annualized_volatility": float("nan"),
        "applied_annualized_volatility": float("nan"),
    }
    path_returns: list[float] = []
    real_closes = 0
    carry_days = 0
    previous_close: float | None = None
    for day in window:
        row = by_day.get(day)
        if row is None:
            record["risk_invalid_reason"] = RISK_INVALID_UNTRUSTED_MISSING
            return record
        missing_reason = _missing_reason_of(row)
        close = _close_of(row)
        if missing_reason is None:
            if _severity_of(row) == _QUALITY_ERROR or close is None:
                record["risk_invalid_reason"] = RISK_INVALID_UNTRUSTED_MISSING
                return record
            if previous_close is not None:
                path_returns.append(close / previous_close - 1.0)
            previous_close = close
            real_closes += 1
            continue
        if missing_reason != MISSING_REASON_SUSPENDED_VERIFIED:
            record["risk_invalid_reason"] = RISK_INVALID_UNTRUSTED_MISSING
            return record
        # A verified suspension carries the previous close forward: zero
        # path return, never a real observation, and it needs a prior
        # trusted close inside the window to be meaningful.
        if close is None or previous_close is None:
            record["risk_invalid_reason"] = RISK_INVALID_UNTRUSTED_MISSING
            return record
        path_returns.append(0.0)
        carry_days += 1
    if real_closes < policy.min_risk_observations:
        record["real_close_observations"] = real_closes
        record["suspension_carry_days"] = carry_days
        record["risk_invalid_reason"] = RISK_INVALID_INSUFFICIENT_REAL_CLOSES
        return record
    if len(path_returns) < 2:
        record["real_close_observations"] = real_closes
        record["suspension_carry_days"] = carry_days
        record["risk_invalid_reason"] = RISK_INVALID_INSUFFICIENT_REAL_CLOSES
        return record
    raw_vol = Decimal(str(np.std(path_returns, ddof=1) * np.sqrt(252)))
    applied_vol = max(raw_vol, policy.volatility_floor_annualized)
    record["real_close_observations"] = real_closes
    record["suspension_carry_days"] = carry_days
    record["risk_is_valid"] = True
    record["raw_annualized_volatility"] = float(raw_vol)
    record["applied_annualized_volatility"] = float(applied_vol)
    return record


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _validate_input_frame(observations: pd.DataFrame) -> None:
    if not isinstance(observations, pd.DataFrame):
        raise RiskInputError(
            "observations must be a pandas DataFrame, got "
            f"{type(observations).__name__}"
        )
    if list(observations.columns) != list(RISK_INPUT_COLUMNS):
        raise RiskInputError(
            "risk input columns must be exactly "
            f"{list(RISK_INPUT_COLUMNS)} in order; got "
            f"{list(observations.columns)}"
        )


def _as_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    stamp = pd.Timestamp(value)
    if stamp is pd.NaT:
        raise RiskInputError(f"cannot interpret {value!r} as a date")
    return stamp.date()


def _close_of(row: dict) -> float | None:
    value = row["adjusted_close"]
    try:
        if pd.isna(value):
            return None
    except TypeError:  # pragma: no cover - exotic scalar
        return None
    close = float(value)
    if not np.isfinite(close) or close <= 0.0:
        return None
    return close


def _severity_of(row: dict) -> str:
    value = row.get("quality_severity")
    return "" if value is None or pd.isna(value) else str(value)


def _missing_reason_of(row: dict) -> str | None:
    value = row.get("missing_reason")
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:  # pragma: no cover - exotic scalar
        return None
    text = str(value).strip()
    return text or None
