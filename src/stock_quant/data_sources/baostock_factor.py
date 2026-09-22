"""BaoStock adjustment-factor series: ADR-009's second price-event channel.

ADR-009 decision 3 admits two channels for the market axis of an absent
ex-date classification -- TDX category-1 records and baostock's adjustment-
factor series.  This module owns the second: one login, one
``query_adjust_factor`` per symbol over the supplier's full history, and the
derivation of the series' price-event dates.

A row of the series alone is not an event: the supplier emits no-change rows
as a systemic artifact (ADR-009's 2019-07-19 example), so
:func:`factor_event_dates` reports only the dates where the cumulative factor
differs from the previous row.  The first row is the baseline and never an
event, so a probe before the first change asserts nothing -- the fail-closed
direction ADR-009 decision 2 fixes for every channel.
"""

from __future__ import annotations

import contextlib
import io
import socket
from datetime import date
from typing import Any, Sequence

import pandas as pd

from stock_quant.data_sources.base import (
    DataRequest,
    FetchResult,
    request_key,
    translate_supplier_error,
)
from stock_quant.data_sources.baostock import BaoStockSource

#: The series is read over the supplier's full listed history: a probe date is
#: a quarantined row's announcement date, which predates any update window.
SERIES_START = date(1990, 12, 19)

#: Endpoint namespace the raw snapshots are stored under, parallel to the
#: daily lane's ``baostock/daily``: ``raw/baostock/adjust_factor/<key>``.
ADJUST_FACTOR_ENDPOINT = "adjust_factor"

_DATE_COLUMN = "dividOperateDate"
_FACTOR_COLUMN = "adjustFactor"


def bs_code(symbol: str) -> str:
    """The supplier's dotted code (``sz.300124``) for a canonical symbol."""
    code, _, suffix = symbol.rpartition(".")
    if not code or suffix.upper() not in ("SH", "SZ"):
        raise ValueError(f"baostock has no code for {symbol!r}")
    return f"{suffix.lower()}.{code}"


def fetch_adjust_factor_frames(
    symbols: Sequence[str],
    *,
    timeout: float = 30.0,
    end: date | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch each symbol's cumulative adjustment-factor series.

    One login serves every symbol; each frame carries the supplier's native
    columns.  Raises on channel failure; a single symbol's error propagates,
    leaving that symbol's channel view unknown -- the fail-closed direction.
    """
    import baostock as bs

    frames: dict[str, pd.DataFrame] = {}
    query_end = (end or date.today()).isoformat()
    logged_in = False
    captured = io.StringIO()
    previous_timeout = socket.getdefaulttimeout()
    try:
        # Same SDK hazard as the daily lane: raw sockets with no timeout of
        # their own, and failures swallowed into stdout markers.
        socket.setdefaulttimeout(timeout)
        with contextlib.redirect_stdout(captured):
            login = bs.login()
            logged_in = True
            BaoStockSource._raise_for_response(login)
            for symbol in symbols:
                code = bs_code(symbol)
                response = bs.query_adjust_factor(
                    code=code, start_date=SERIES_START.isoformat(), end_date=query_end
                )
                BaoStockSource._raise_for_response(response)
                frames[symbol] = BaoStockSource._to_frame(response)
            BaoStockSource._raise_for_swallowed_transport_failure(captured)
    except Exception as error:
        translated = translate_supplier_error(error, baostock=True)
        if translated is error:
            raise
        raise translated from None
    finally:
        if logged_in:
            # Logout runs while the timeout still binds: its recv uses the
            # same flaky server, and after the restore it would be the one
            # unbounded read in the whole fetch.
            with contextlib.redirect_stdout(captured):
                bs.logout()
        socket.setdefaulttimeout(previous_timeout)
    return frames


def factor_event_dates(frame: pd.DataFrame | None) -> list[date]:
    """The series' price-event dates: where the cumulative factor changes.

    A no-change row is the artifact, not the event; the first row is the
    baseline.  An empty or missing frame answers an empty list, which
    ``market_view`` reads as asserting nothing.
    """
    if frame is None or frame.empty:
        return []
    ordered = frame.sort_values(_DATE_COLUMN, kind="stable")
    events: list[date] = []
    previous: float | None = None
    for row in ordered.to_dict("records"):
        value = float(row[_FACTOR_COLUMN])
        day = _as_date(row[_DATE_COLUMN])
        if previous is not None and value != previous and day is not None:
            events.append(day)
        previous = value
    return events


def snapshot_result(
    symbol: str,
    frame: pd.DataFrame,
    *,
    end: date,
) -> FetchResult:
    """The snapshot record for one symbol's series, in the raw-store layout.

    Minimal metadata by the arbiter's precedent: the lane is evidence for a
    classification, not a source, and the content hash is what audit reads.
    """
    request = DataRequest(ADJUST_FACTOR_ENDPOINT, (symbol,), SERIES_START, end)
    return FetchResult(
        source="baostock",
        endpoint=ADJUST_FACTOR_ENDPOINT,
        request_key=request_key(request),
        frame=frame,
        metadata={
            "transport_id": "baostock",
            "supplier_endpoint": "baostock.query_adjust_factor",
        },
    )


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None
