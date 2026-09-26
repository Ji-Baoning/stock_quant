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
        frame = run_isolated(
            _fetch_kline,
            timeout_seconds=float(self.config.timeout_seconds),
            client=self._client,
            symbol=request.symbols[0],
            start=request.start_date,
            end=request.end_date,
        )
        response_timestamp = _utc_timestamp()
        validate_supplier_frame(
            frame,
            request,
            symbol_columns=("code",),
            date_columns=(DATE_COLUMN,),
        )
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
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
        )


def _require_credentials() -> None:
    """Fail with a permanent error before a worker is ever started."""
    import os

    missing = [name for name in _REQUIRED_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        raise AuthenticationError(
            f"xingyao requires {'/'.join(missing)} to be configured"
        )


def _fetch_kline(*, client: Any, symbol: str, start, end) -> pd.DataFrame:
    """The worker body: login, one unadjusted daily window, logout.

    The client (real wrapper or test fake) speaks a three-call surface:
    ``login(**credentials)``, ``query_kline(symbol=..., begin_date=...,
    end_date=...)`` and ``logout()``.  Raises typed failures so the parent
    never has to read SDK text: a login refusal is permanent, everything else
    is transient.  The frame is returned as the SDK produced it -- this
    function never renames or filters columns.
    """
    logged_in = False
    try:
        if not client.login(**_credentials()):
            raise AuthenticationError("xingyao rejected the credentials")
        logged_in = True
        response = client.query_kline(symbol=symbol, begin_date=start, end_date=end)
        return _to_frame(response)
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

    def query_kline(self, *, symbol: str, begin_date, end_date) -> pd.DataFrame:
        ad = self._ad
        if self._market is None:
            base = ad.BaseData()
            self._market = ad.MarketData(base.get_calendar())
        result = self._market.query_kline(
            [symbol],
            begin_date=int(begin_date.strftime("%Y%m%d")),
            end_date=int(end_date.strftime("%Y%m%d")),
            period=ad.constant.Period.day.value,
            is_local=False,
        )
        if not isinstance(result, dict) or symbol not in result:
            raise ContractError(f"xingyao returned no kline frame for {symbol!r}")
        return result[symbol]

    def logout(self) -> None:
        self._ad.logout()
