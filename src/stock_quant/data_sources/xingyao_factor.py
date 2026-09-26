"""星耀数智 backward-adjustment factor: ADR-009's second price-event channel.

``get_backward_factor([symbol], is_local=False)`` answers a *wide* frame: the
index is the trading calendar and the columns are the requested symbols.  A
row is therefore not an observation of the symbol -- the calendar length is
the row count, and the cells outside the symbol's listed life are empty.

The event rule is baostock's, unchanged (ADR-009 decision 3): the first
*valid* value is the baseline, and an event is a row whose value differs from
the previous valid value.  Anything unreadable -- an empty frame, a missing
symbol column, no valid value at all -- is a contract break or an absent
channel, never "no events": silence must not assert an absence.

The live call goes through ``run_isolated`` like the daily lane: the broker
TCP SDK (``AmazingData``) has a callback thread and no timeout of its own, so
only a killable child process bounds it.  Credentials are read from the
``AD_*`` variables inside the worker and never travel back in an error
message, a metadata record or a log line.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources._isolated import run_isolated
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
    FetchResult,
    ServerError,
    request_key,
    request_metadata,
)
from stock_quant.data_sources.xingyao import _looks_like_authentication

#: Endpoint namespace for the raw snapshots: ``raw/xingyao/backward_factor/**``.
BACKWARD_FACTOR_ENDPOINT = "backward_factor"

#: The factor series is read over the whole listed history: a probe date is a
#: quarantined row's announcement date and predates any update window.
SERIES_START = date(1990, 12, 19)

TRANSPORT_ID = "xingyao-broker-tcp"


def factor_event_dates(frame: pd.DataFrame | None, symbol: str) -> list[date]:
    """The symbol's price-event dates; raises when the frame cannot answer.

    An absent channel is the caller's decision, not this function's: it either
    returns dates or raises ``ContractError``.
    """
    if frame is None or frame.empty:
        raise ContractError("xingyao returned no factor frame")
    if symbol not in frame.columns:
        raise ContractError(f"xingyao factor frame has no column for {symbol!r}")
    series = _ordered_series(frame[symbol])
    events: list[date] = []
    previous: float | None = None
    for day, value in _deduplicate(series).items():
        if previous is not None and value != previous:
            events.append(day)
        previous = value
    return events


def fetch_factor_frame(
    symbol: str, *, timeout_seconds: float, end: date | None = None
) -> pd.DataFrame:
    """One worker, one symbol, the supplier's wide frame as it answered it.

    Split from the event extraction on purpose: the frame is what must be
    snapshotted, and extraction can raise ``ContractError`` on a frame that is
    still the honest record of what the supplier said.  Fusing the two would
    discard raw evidence exactly when it is most interesting.
    """
    return run_isolated(
        _fetch_backward_factor,
        timeout_seconds=timeout_seconds,
        symbol=symbol,
        end=(end or date.today()).isoformat(),
    )


def fetch_factor_event_dates(
    symbol: str, *, timeout_seconds: float, end: date | None = None
) -> list[date]:
    """One worker, one symbol, one event list (fail-closed on any error)."""
    return factor_event_dates(
        fetch_factor_frame(symbol, timeout_seconds=timeout_seconds, end=end), symbol
    )


def snapshot_result(symbol: str, frame: pd.DataFrame, *, end: date) -> FetchResult:
    """The snapshot for one symbol's series: the supplier's wide frame, as-is.

    The stored bytes must be what the supplier answered -- the derived event
    list is not a response and must never stand in for one.  The metadata
    carries a rebuildable request so the drift audit can re-ask the same
    question.
    """
    request = DataRequest(BACKWARD_FACTOR_ENDPOINT, (symbol,), SERIES_START, end)
    metadata = request_metadata(
        request,
        "xingyao.get_backward_factor",
        "unknown",
        transport_id=TRANSPORT_ID,
    )
    return FetchResult(
        source="xingyao",
        endpoint=BACKWARD_FACTOR_ENDPOINT,
        request_key=request_key(request),
        frame=frame,
        metadata=metadata,
    )


def _ordered_series(column: pd.Series) -> pd.Series:
    """Dates parsed, values numeric, ascending -- the comparison's preconditions."""
    numeric = pd.to_numeric(column, errors="coerce")
    parsed = pd.to_datetime(pd.Index(column.index), errors="coerce")
    frame = pd.DataFrame({"date": parsed, "value": numeric})
    frame = frame[frame["date"].notna()]
    return frame.set_index("date")["value"].sort_index(kind="stable")


def _deduplicate(series: pd.Series) -> dict[date, float]:
    """One value per calendar day; a conflicting duplicate is a contract break."""
    out: dict[date, float] = {}
    for timestamp, value in series.items():
        day = timestamp.date()
        if pd.isna(value):
            continue
        number = float(value)
        seen = out.get(day)
        if seen is not None and seen != number:
            raise ContractError(f"xingyao factor conflicts on {day.isoformat()}")
        out[day] = number
    return out


def _fetch_backward_factor(*, symbol: str, end: str) -> pd.DataFrame:
    """The worker body: login, one wide-table query, logout.

    The verified real-SDK surface, mirroring the daily lane's ``_RealClient``:
    ``ad.login`` wants an int port and answers with a truthy flag -- falsy is
    a credential refusal -- and an empty ``AD_HOST``/``AD_PORT`` is refused
    before any connection is attempted.  ``end`` is kept so the worker's
    question matches the recorded request's shape; the factor series itself is
    always read over the whole listed history.
    """
    import os

    import AmazingData as ad

    host = os.environ.get("AD_HOST", "")
    port = os.environ.get("AD_PORT", "")
    if not host or not port:
        raise AuthenticationError(
            "xingyao requires AD_HOST/AD_PORT to be configured"
        )
    try:
        port_number = int(port)
    except (TypeError, ValueError):
        raise AuthenticationError("xingyao requires a numeric AD_PORT") from None
    logged_in = False
    try:
        if not ad.login(
            username=os.environ["AD_USERNAME"],
            password=os.environ["AD_PASSWORD"],
            host=host,
            port=port_number,
        ):
            raise AuthenticationError("xingyao rejected the credentials")
        logged_in = True
        response = ad.BaseData().get_backward_factor([symbol], is_local=False)
        return _to_frame(response)
    except (AuthenticationError, ContractError):
        raise
    except Exception as error:  # noqa: BLE001 - map, never leak SDK text upward
        if _looks_like_authentication(error):
            raise AuthenticationError("xingyao rejected the credentials") from None
        raise ServerError(
            f"xingyao factor request failed ({type(error).__name__})"
        ) from None
    finally:
        if logged_in:
            try:
                ad.logout()
            except Exception:  # noqa: BLE001 - a logout failure costs nothing
                pass


def _to_frame(response: Any) -> pd.DataFrame:
    if isinstance(response, pd.DataFrame):
        return response
    if hasattr(response, "to_frame"):
        return response.to_frame()
    raise ContractError("xingyao returned an unreadable factor response")


class XingyaoFactorSource:
    """``backward_factor`` as a ``DataSource``, so the drift audit can re-ask it.

    The recorded question is reproduced through ``snapshot_result`` itself --
    the same function that recorded it -- so the audit's request key and
    parameters match the snapshot's by construction rather than by a second
    implementation of the same convention.
    """

    name = "xingyao"

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint != BACKWARD_FACTOR_ENDPOINT:
            raise ValueError("XingyaoFactorSource serves only backward_factor")
        if len(request.symbols) != 1:
            raise ValueError("xingyao factor requests require exactly one symbol")
        symbol = request.symbols[0]
        frame = fetch_factor_frame(
            symbol,
            timeout_seconds=float(self.config.timeout_seconds),
            end=request.end_date,
        )
        return snapshot_result(symbol, frame, end=request.end_date)
