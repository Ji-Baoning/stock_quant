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

from collections.abc import Mapping, Sequence
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
    deduplicated = _deduplicate(series)
    if not deduplicated:
        # A column with no valid value at all -- an unlisted or mistaken
        # symbol's all-empty cells -- cannot answer either: returning []
        # would be silence asserting an absence.
        raise ContractError(
            f"xingyao factor column for {symbol!r} carries no valid values"
        )
    events: list[date] = []
    previous: float | None = None
    for day, value in deduplicated.items():
        if previous is not None and value != previous:
            events.append(day)
        previous = value
    return events


def factor_ratio_at(
    frame: pd.DataFrame | None, symbol: str, ex_date: date
) -> float | None:
    """The cumulative factor's step across ``ex_date``, or ``None``.

    The ratio the last value at or before the ex-date carries against its
    predecessor -- the same computation ADR-009's event rule performs, kept
    as a value because the ADR-019 settler corroborates the exchange's
    reference price against it.  A frame that cannot answer, a symbol the
    frame does not carry, or an ex-date at the series' very start answers
    ``None``: the corroboration leg is skipped, never guessed.
    """
    if frame is None or frame.empty:
        return None
    if symbol not in frame.columns:
        return None
    series = _ordered_series(frame[symbol])
    deduplicated = _deduplicate(series)
    at_or_before = [day for day in deduplicated if day <= ex_date]
    if len(at_or_before) < 2:
        return None
    latest, previous = at_or_before[-1], at_or_before[-2]
    base = deduplicated[previous]
    if base == 0:
        return None
    return deduplicated[latest] / base


def fetch_factor_frames(
    symbols: Sequence[str], *, timeout_seconds: float, end: date | None = None
) -> dict[str, pd.DataFrame]:
    """One worker for a whole chunk of factor series.

    The stored bytes stay per symbol (the supplier's own wide frame for that
    code); this only changes how many sessions it takes to obtain them.  A
    code the answer did not carry is simply absent from the mapping -- the
    caller must not invent a frame for it.
    """
    return run_isolated(
        _fetch_backward_factor_batch,
        timeout_seconds=timeout_seconds,
        symbols=list(symbols),
        end=(end or date.today()).isoformat(),
    )


def fetch_factor_frame(
    symbol: str, *, timeout_seconds: float, end: date | None = None
) -> pd.DataFrame:
    """One worker, one symbol: the strict path keeps ADR-009's stance."""
    frames = fetch_factor_frames([symbol], timeout_seconds=timeout_seconds, end=end)
    frame = frames.get(symbol)
    if frame is None:
        raise ContractError(f"xingyao returned no factor frame for {symbol!r}")
    return frame


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


def _login_and_import():
    """The factor workers' shared credential check, SDK import and login.

    Extracted verbatim from ``_fetch_backward_factor`` (the error texts are
    pinned): ``ad.login`` wants an int port and answers with a truthy flag --
    falsy is a credential refusal.  Missing or empty ``AD_USERNAME``/
    ``AD_PASSWORD`` or ``AD_HOST``/``AD_PORT`` is refused before the SDK is
    even imported or any connection is attempted, so every credential failure
    is the same permanent error no matter which variable is at fault.  The
    module is returned only after the login has succeeded, so a caller's
    ``finally`` can always log out.
    """
    import os

    username = os.environ.get("AD_USERNAME", "")
    password = os.environ.get("AD_PASSWORD", "")
    if not username or not password:
        raise AuthenticationError(
            "xingyao requires AD_USERNAME/AD_PASSWORD to be configured"
        )
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

    import AmazingData as ad

    if not ad.login(
        username=username,
        password=password,
        host=host,
        port=port_number,
    ):
        raise AuthenticationError("xingyao rejected the credentials")
    return ad


def _fetch_backward_factor(*, symbol: str, end: str) -> pd.DataFrame:
    """The worker body: login, one wide-table query, logout.

    The factor series is read over the whole listed history and then clipped
    to ``end`` (owner ruling 2026-09-26): the raw answer's calendar-length
    index grows every trading day, and the stored bytes must be a function of
    the recorded request for the drift audit's re-ask to compare hashes.
    """
    ad = _login_and_import()
    try:
        response = ad.BaseData().get_backward_factor([symbol], is_local=False)
        # Owner ruling 2026-09-26: clip to the recorded end (bytes follow the request).
        return _clip_to_end(_to_frame(response), end)
    except (AuthenticationError, ContractError):
        raise
    except Exception as error:  # noqa: BLE001 - map, never leak SDK text upward
        if _looks_like_authentication(error):
            raise AuthenticationError("xingyao rejected the credentials") from None
        raise ServerError(
            f"xingyao factor request failed ({type(error).__name__})"
        ) from None
    finally:
        try:
            ad.logout()
        except Exception:  # noqa: BLE001 - a logout failure costs nothing
            pass


def _fetch_backward_factor_batch(
    *, symbols: list[str], end: str
) -> dict[str, pd.DataFrame]:
    """The batch worker body: one login, one multi-code query, one logout.

    Same credential and error discipline as the single-symbol worker.  Every
    frame is clipped to the recorded ``end`` so its bytes remain a function of
    the request (owner ruling 2026-09-26).
    """
    ad = _login_and_import()
    try:
        response = ad.BaseData().get_backward_factor(list(symbols), is_local=False)
        if not isinstance(response, Mapping):
            raise ContractError("xingyao returned an unreadable factor response")
        return {
            code: _clip_to_end(_to_frame(frame), end)
            for code, frame in response.items()
        }
    except (AuthenticationError, ContractError):
        raise
    except Exception as error:  # noqa: BLE001 - map, never leak SDK text upward
        if _looks_like_authentication(error):
            raise AuthenticationError("xingyao rejected the credentials") from None
        raise ServerError(
            f"xingyao factor request failed ({type(error).__name__})"
        ) from None
    finally:
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


def _clip_to_end(frame: pd.DataFrame, end: str | date) -> pd.DataFrame:
    """The rows whose index date is on or before ``end``; the clip is inclusive.

    An unparseable index cell coerces to NaT and falls out -- the downstream
    contract guards, not this helper, own what an unreadable index means.
    """
    parsed = pd.to_datetime(pd.Index(frame.index), errors="coerce")
    return frame.loc[parsed <= pd.to_datetime(end)]


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
