"""星耀数智 (AmazingData broker SDK) adapter for raw daily bars.

Every live call runs in a killable child process: the SDK is a broker TCP
client with a callback thread and no timeout of its own, so the parent can
only bound it by owning a process (see ``_isolated``).  The parent imports the
SDK to fail fast when the private wheel is absent -- the optional-source
degradation baostock documents -- and never calls it in-process.

Credentials are read from ``AD_USERNAME``/``AD_PASSWORD``/``AD_HOST``/
``AD_PORT`` inside the worker: they must not appear in a metadata record, an
error message that reaches the quality report, or a snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_model.batch_evidence import (
    batch_id_for,
    batch_request_parameters,
)
from stock_quant.data_sources._isolated import run_isolated
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
    FetchResult,
    ServerError,
    _utc_timestamp,
    request_key,
    request_metadata,
    validate_supplier_frame,
)

#: The transport identity of the party that actually answers.  A broker
#: session is not the same transport as the supplier's HTTP front, so the two
#: must not collapse into one content-addressed path (base.request_metadata).
TRANSPORT_ID = "xingyao-broker-tcp"

#: The SDK's native date column; the daily frame keeps its own columns and
#: normalization renames them downstream, exactly as baostock's ``date`` does.
DATE_COLUMN = "kline_time"

_REQUIRED_ENVIRONMENT = ("AD_USERNAME", "AD_PASSWORD")


@dataclass(frozen=True)
class UnreadableFrame:
    """A per-code answer the worker could not turn into a frame.

    Conversion, not judgement: the child must not decide that a code is
    refused (spec §4 layer 2 gives that verdict to the parent), but a value
    that is not a frame and has no ``to_frame`` cannot cross the process
    boundary either.  It travels as this marker and is mapped to ``refused``
    in the parent.
    """

    message: str


@dataclass(frozen=True)
class BatchOutcome:
    """One requested symbol's outcome from a multi-code call.

    ``result`` is the adapter-assembled ``FetchResult`` for ``ok``/``empty``
    (its ``request_key`` and metadata are the symbol's own, even though one
    call served the whole chunk), and ``None`` for ``refused`` -- a refusal
    has no supplier object, so no snapshot may stand in for one.
    """

    symbol: str
    status: str
    result: FetchResult | None = None
    message: str = ""


@dataclass(frozen=True)
class BatchTransmission:
    """One actual multi-code call: what it asked, and when it answered."""

    symbols: tuple[str, ...]
    request_parameters: str
    batch_id: str
    transport_id: str
    request_timestamp: str
    response_timestamp: str


@dataclass(frozen=True)
class BatchResult:
    """Outcomes aligned with the requests, plus the transmissions behind them."""

    outcomes: tuple[BatchOutcome, ...]
    transmissions: tuple[BatchTransmission, ...]


class XingyaoSource:
    """Frames come from a broker session bounded by a killable worker."""

    name = "xingyao"

    def __init__(self, config: SourceConfig, client: Any | None = None) -> None:
        self.config = config
        if client is None:
            client = _RealClient()  # imports the wheel; absent => unavailable
            self._sdk_version = client.sdk_version
        else:
            self._sdk_version = getattr(client, "__version__", "fake")
        self._client = client

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint != "daily":
            raise ValueError("XingyaoSource serves only the daily endpoint")
        if len(request.symbols) != 1:
            raise ValueError("xingyao daily requests take exactly one symbol")
        _require_credentials()
        request_timestamp = _utc_timestamp()
        frames = run_isolated(
            _fetch_kline_batch,
            timeout_seconds=float(self.config.timeout_seconds),
            client=self._client,
            symbols=[request.symbols[0]],
            start=request.start_date,
            end=request.end_date,
        )
        response_timestamp = _utc_timestamp()
        frame = _frame_for(frames, request.symbols[0])
        # The single-request path keeps its strictness: silence is not an
        # answer here (ADR-009).  The relaxed zero-row rule belongs to the
        # lane's batch outcomes, not to this path (ADR-020 D2).
        validate_supplier_frame(
            frame,
            request,
            symbol_columns=("code",),
            date_columns=(DATE_COLUMN,),
        )
        return self._fetch_result(request, frame, request_timestamp, response_timestamp)

    def fetch_batch(
        self,
        requests: Sequence[DataRequest],
        *,
        on_attempt: Callable[[int], None] | None = None,
    ) -> BatchResult:
        """One multi-code call for the whole chunk; three states per symbol.

        Preconditions (the lane guarantees them): one endpoint, one window,
        exactly one symbol per request.  The returned outcomes are aligned
        with ``requests`` positionally, which is what lets the reuse partition
        hand back only the misses.
        """
        if not requests:
            return BatchResult(outcomes=(), transmissions=())
        if {request.endpoint for request in requests} != {"daily"}:
            raise ValueError("XingyaoSource serves only the daily endpoint")
        if any(len(request.symbols) != 1 for request in requests):
            raise ValueError("xingyao daily requests take exactly one symbol")
        symbols = tuple(request.symbols[0] for request in requests)
        if len({(request.start_date, request.end_date) for request in requests}) != 1:
            raise ValueError("xingyao batch requests take exactly one window")
        _require_credentials()
        if on_attempt is not None:
            on_attempt(len(symbols))
        request_timestamp = _utc_timestamp()
        # The lane guarantees a frozen batch pair; a direct caller falls back
        # to the endpoint's own bound rather than crashing on an unset pair.
        timeout_seconds = float(
            self.config.batch_timeout_seconds or self.config.timeout_seconds
        )
        frames = run_isolated(
            _fetch_kline_batch,
            timeout_seconds=timeout_seconds,
            client=self._client,
            symbols=list(symbols),
            start=requests[0].start_date,
            end=requests[0].end_date,
        )
        response_timestamp = _utc_timestamp()
        outcomes = tuple(
            self._batch_outcome(request, frames, request_timestamp, response_timestamp)
            for request in requests
        )
        parameters = batch_request_parameters(
            "daily",
            symbols,
            requests[0].start_date,
            requests[0].end_date,
            dict(requests[0].params),
        )
        return BatchResult(
            outcomes=outcomes,
            transmissions=(
                BatchTransmission(
                    symbols=symbols,
                    request_parameters=parameters,
                    batch_id=batch_id_for(parameters),
                    transport_id=TRANSPORT_ID,
                    request_timestamp=request_timestamp,
                    response_timestamp=response_timestamp,
                ),
            ),
        )

    def _fetch_result(
        self,
        request: DataRequest,
        frame: pd.DataFrame,
        requested_at: str,
        answered_at: str,
    ) -> FetchResult:
        """The per-symbol evidence record, identical in both paths."""
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(
                request,
                "xingyao.query_kline",
                self._sdk_version,
                transport_id=TRANSPORT_ID,
                request_timestamp=requested_at,
                response_timestamp=answered_at,
            ),
        )

    def _batch_outcome(
        self,
        request: DataRequest,
        frames: Mapping[str, Any],
        requested_at: str,
        answered_at: str,
    ) -> BatchOutcome:
        """The parent's per-symbol verdict: ok, empty, or refused."""
        symbol = request.symbols[0]
        if symbol not in frames:
            # Absent key: truncation, a silently dropped code and a supplier
            # omission are indistinguishable here, so this is fail-closed.
            return BatchOutcome(
                symbol=symbol,
                status="refused",
                message="supplier answer carried no frame for this code",
            )
        value = frames[symbol]
        if isinstance(value, UnreadableFrame):
            return BatchOutcome(symbol=symbol, status="refused", message=value.message)
        try:
            frame = _to_frame(value)
            validate_supplier_frame(
                frame,
                request,
                symbol_columns=("code",),
                date_columns=(DATE_COLUMN,),
                allow_empty=True,
            )
        except ContractError as error:
            return BatchOutcome(symbol=symbol, status="refused", message=str(error))
        result = self._fetch_result(request, frame, requested_at, answered_at)
        return BatchOutcome(
            symbol=symbol,
            status="empty" if frame.empty else "ok",
            result=result,
        )


def _require_credentials() -> None:
    """Fail with a permanent error before a worker is ever started."""
    import os

    missing = [name for name in _REQUIRED_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        raise AuthenticationError(
            f"xingyao requires {'/'.join(missing)} to be configured"
        )


def _fetch_kline_batch(
    *, client: Any, symbols: list[str], start, end
) -> dict[str, Any]:
    """The worker body: one login, one multi-code query, one logout.

    Returns the supplier's answer in a transportable shape, keyed by code.
    Nothing per-symbol is judged here: a multi-code call has no per-code
    failure to report, and a shape that is not a mapping at all is a
    *permanent* fault of the whole call (spec §4 layer 1: no retry, no
    bisection), not a verdict about any code.
    """
    logged_in = False
    try:
        if not client.login(**_credentials()):
            raise AuthenticationError("xingyao rejected the credentials")
        logged_in = True
        response = client.query_kline(
            symbols=list(symbols), begin_date=start, end_date=end
        )
        if not isinstance(response, Mapping):
            raise ContractError("xingyao returned an unreadable kline response")
        return {code: _transportable_frame(value) for code, value in response.items()}
    except (AuthenticationError, ContractError):
        raise
    except Exception as error:  # noqa: BLE001 - map, never leak SDK text upward
        if _looks_like_authentication(error):
            raise AuthenticationError("xingyao rejected the credentials") from None
        raise ServerError(
            f"xingyao kline request failed ({type(error).__name__})"
        ) from None
    finally:
        if logged_in:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - a logout failure costs nothing
                pass


def _transportable_frame(value: Any) -> Any:
    """Turn one code's answer into something that can cross the process boundary.

    A DataFrame travels as-is; anything with ``to_frame`` is converted and
    otherwise the value is marked unreadable.  The columns are never touched:
    what the parent validates must be what the supplier answered.
    """
    if isinstance(value, pd.DataFrame):
        return value
    to_frame = getattr(value, "to_frame", None)
    if to_frame is None:
        return UnreadableFrame("the supplier's answer for this code is not a frame")
    try:
        return to_frame()
    except Exception as error:  # noqa: BLE001 - the parent decides what this means
        return UnreadableFrame(
            f"the supplier's answer could not be read as a frame "
            f"({type(error).__name__})"
        )


def _frame_for(frames: Mapping[str, Any], symbol: str) -> pd.DataFrame:
    """The one symbol's frame on the strict (single-request) path."""
    if symbol not in frames:
        raise ContractError(f"xingyao returned no kline frame for {symbol!r}")
    value = frames[symbol]
    if isinstance(value, UnreadableFrame):
        raise ContractError(value.message)
    return _to_frame(value)


def _credentials() -> dict[str, str]:
    """Read the ``AD_*`` variables; called only inside the worker."""
    import os

    return {
        "username": os.environ["AD_USERNAME"],
        "password": os.environ["AD_PASSWORD"],
        "host": os.environ.get("AD_HOST", ""),
        "port": os.environ.get("AD_PORT", ""),
    }


def _looks_like_authentication(error: Exception) -> bool:
    message = str(error).lower()
    return any(
        word in message
        for word in (
            "login", "loginid", "password", "auth",
            "认证", "登录", "密码", "权限",
        )
    )


def _to_frame(response: Any) -> pd.DataFrame:
    """The SDK's answer as a frame; a shape we cannot read is a contract break."""
    if isinstance(response, pd.DataFrame):
        frame = response
    elif hasattr(response, "to_frame"):
        frame = response.to_frame()
    else:
        raise ContractError("xingyao returned an unreadable kline response")
    if DATE_COLUMN not in frame.columns:
        raise ContractError(f"xingyao kline frame lacks the {DATE_COLUMN!r} column")
    return frame


class _RealClient:
    """The installed SDK behind the three-call surface; used with no fake.

    Everything the real wheel needs lives here and runs inside the worker:
    ``login`` answers with a truthy flag instead of raising and wants an int
    port, the kline sequence builds a ``MarketData`` over the trading
    calendar and takes ``YYYYMMDD`` ints with an explicit day period (the SDK
    answers minute bars otherwise), and the kline answer is a dict keyed by
    code.  Constructing this object imports the wheel, so an absent wheel
    fails ``XingyaoSource`` construction -- the optional-source degradation
    baostock documents.  No connection is made here: the calendar and session
    are built lazily, inside the worker.
    """

    def __init__(self) -> None:
        import AmazingData as ad  # the private wheel; absent => unavailable

        self._ad = ad
        self._market: Any | None = None
        self.sdk_version = str(getattr(ad, "__version__", "unknown"))

    def login(self, *, username: str, password: str, host: str, port: str) -> Any:
        if not host or not port:
            raise AuthenticationError(
                "xingyao requires AD_HOST/AD_PORT to be configured"
            )
        try:
            port_number = int(port)
        except (TypeError, ValueError):
            raise AuthenticationError("xingyao requires a numeric AD_PORT") from None
        return self._ad.login(
            username=username, password=password, host=host, port=port_number
        )

    def query_kline(self, *, symbols, begin_date, end_date) -> dict[str, Any]:
        ad = self._ad
        if self._market is None:
            base = ad.BaseData()
            self._market = ad.MarketData(base.get_calendar())
        result = self._market.query_kline(
            list(symbols),
            begin_date=int(begin_date.strftime("%Y%m%d")),
            end_date=int(end_date.strftime("%Y%m%d")),
            period=ad.constant.Period.day.value,
            is_local=False,
        )
        if not isinstance(result, dict):
            raise ContractError("xingyao returned an unreadable kline response")
        return result

    def logout(self) -> None:
        self._ad.logout()
