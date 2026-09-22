"""Common contracts and retry behaviour for market-data suppliers."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterator, Protocol

import pandas as pd


@dataclass(frozen=True)
class DataRequest:
    endpoint: str
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchResult:
    source: str
    endpoint: str
    request_key: str
    frame: pd.DataFrame
    metadata: dict[str, str]


class DataSource(Protocol):
    name: str

    def fetch(self, request: DataRequest) -> FetchResult: ...


class TransientSourceError(RuntimeError):
    """An upstream failure for which another attempt may be useful."""


class RateLimitError(TransientSourceError):
    """The supplier has temporarily limited this caller."""


class ServerError(TransientSourceError):
    """The supplier returned a temporary server-side failure."""


class BaoStockSessionExpiredError(TransientSourceError):
    """BaoStock rejected a request because its session has expired."""


class AuthenticationError(RuntimeError):
    """Credentials are missing, invalid, or not permitted."""


class ContractError(RuntimeError):
    """A supplier response does not satisfy its documented raw contract."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    maximum_wait_seconds: int = 30
    call_timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if not 0 <= self.maximum_wait_seconds <= 30:
            raise ValueError("maximum_wait_seconds must be between 0 and 30")
        if not 1 <= self.call_timeout_seconds <= 120:
            raise ValueError("call_timeout_seconds must be between 1 and 120")


def request_key(request: DataRequest) -> str:
    """Return a stable idempotency key without altering the request itself."""
    payload = {
        "endpoint": request.endpoint,
        "symbols": request.symbols,
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "params": request.params,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def request_metadata(
    request: DataRequest,
    supplier_endpoint: str,
    sdk_version: str,
    *,
    transport_id: str,
    request_timestamp: str | None = None,
    response_timestamp: str | None = None,
) -> dict[str, str]:
    """Build audit metadata while keeping tokens out of supplier results.

    ``transport_id`` is the path-safe identity of the party that actually
    answered -- the base-URL host for tushare, the winning upstream for
    akshare, the supplier's own name for baostock (design §2.3).  It has no
    default on purpose: a fallback value would let two different upstreams
    collapse into one content-addressed path and silently keep each other's
    ``supplier_endpoint``.
    """
    parameters = {
        "symbols": list(request.symbols),
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "params": request.params,
    }
    return {
        "request_parameters": json.dumps(parameters, sort_keys=True),
        "request_timestamp": request_timestamp or _utc_timestamp(),
        "response_timestamp": response_timestamp or _utc_timestamp(),
        "supplier_endpoint": supplier_endpoint,
        "sdk_version": sdk_version,
        "transport_id": transport_id,
    }


@contextmanager
def default_request_timeout(seconds: int) -> Iterator[None]:
    """Give supplier HTTP calls made in this block a default timeout.

    The SDKs these adapters drive do not pass one: akshare 1.18.23's
    ``stock_zh_index_daily_em`` is a bare module-level ``requests.get``, and a
    peer that completes the handshake and then goes silent therefore blocks the
    run with no bound at all.  One such call cost the 2026-09-17 rebuild seven
    hours, and ``config.timeout_seconds`` could not have helped, because nothing
    passed it down to the SDK.

    Two other mechanisms were measured against a silent loopback peer on
    2026-09-17 and rejected.  A process socket default
    (``socket.setdefaulttimeout``) bounds ``urllib.request`` but not
    ``requests``, whose adapter re-sets the socket timeout explicitly.  Patching
    ``HTTPAdapter.send`` never applies, because ``requests`` always passes
    ``timeout`` to it as an explicit keyword -- present, so a default cannot
    fill it.  Filling the absent keyword one level up, at ``Session.request``,
    bounds both GET and POST (measured 3.00s against a 3s bound).  It is a
    default, not an override: a transport that passes its own ``timeout`` keeps
    its own, which is how the tushare proxy keeps its longer read timeout.

    This reaches ``requests``-based SDKs only.  A transport that speaks raw TCP
    does not go through it -- ``baostock`` is the one such adapter here, and it
    stays as unbounded as it was; a socket default would be the mechanism for
    it, which is a separate change.

    Process-wide for the duration of the block, and restored on exit; fetches
    are sequential, so it is not used concurrently.
    """
    import requests

    original = requests.sessions.Session.request

    def with_default_timeout(self, *args, **kwargs):
        kwargs.setdefault("timeout", seconds)
        return original(self, *args, **kwargs)

    requests.sessions.Session.request = with_default_timeout
    try:
        yield
    finally:
        requests.sessions.Session.request = original


def fetch_with_retry(
    source: DataSource,
    request: DataRequest,
    policy: RetryPolicy,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """Fetch once for permanent failures and retry only transient supplier errors.

    Each attempt runs under :func:`default_request_timeout`, so a supplier that
    accepts the connection and then answers nothing cannot hold the run: expiry
    surfaces as a ``ServerError`` and takes the transient-error/retry path,
    which is also what keeps the optional-source WARNING path reachable.
    ``policy.maximum_wait_seconds`` bounds the wait *between* attempts;
    ``policy.call_timeout_seconds`` bounds each attempt's I/O.
    """
    for attempt in range(1, policy.max_attempts + 1):
        failure: Exception
        try:
            with default_request_timeout(policy.call_timeout_seconds):
                return source.fetch(request)
        except TimeoutError as expiry:
            failure = ServerError(
                f"supplier call exceeded the {policy.call_timeout_seconds}s timeout"
            )
            failure.__cause__ = expiry
        except TransientSourceError as error:
            failure = error
        if attempt == policy.max_attempts:
            raise failure
        wait_seconds = min(attempt, policy.maximum_wait_seconds)
        if wait_seconds:
            sleeper(wait_seconds)
    raise AssertionError("retry loop must return or raise")


def validate_supplier_frame(
    frame: pd.DataFrame,
    request: DataRequest,
    *,
    symbol_columns: tuple[str, ...],
    date_columns: tuple[str, ...],
    require_symbol: bool = True,
    require_date: bool = True,
    allow_empty: bool = False,
) -> None:
    """Validate a response without renaming, filtering, or coercing its columns."""
    if not isinstance(frame, pd.DataFrame):
        raise ContractError("supplier response is not a pandas DataFrame")
    if frame.empty and not allow_empty:
        raise ContractError("supplier returned an empty response")
    if frame.attrs.get("truncated"):
        raise ContractError("supplier marked its response as truncated")

    symbol_column = _first_present(frame, symbol_columns)
    if require_symbol:
        if symbol_column is None:
            raise ContractError("supplier response has no symbol column")
        returned_symbols = {
            _comparison_symbol(value) for value in frame[symbol_column].dropna()
        }
        requested_symbols = {_comparison_symbol(symbol) for symbol in request.symbols}
        if requested_symbols != returned_symbols:
            raise ContractError(
                "supplier response does not contain each requested symbol"
            )

    date_column = _first_present(frame, date_columns)
    if require_date and date_column is None:
        raise ContractError("supplier response has no date column")
    if date_column is not None:
        raw_dates = frame[date_column].astype(str)
        if raw_dates.str.fullmatch(r"\d{8}").all():
            dates = pd.to_datetime(raw_dates, format="%Y%m%d", errors="coerce")
        else:
            dates = pd.to_datetime(raw_dates, errors="coerce")
        if dates.isna().any():
            raise ContractError("supplier response has an invalid date")
        start = pd.Timestamp(request.start_date)
        end = pd.Timestamp(request.end_date)
        if ((dates < start) | (dates > end)).any():
            raise ContractError(
                "supplier response falls outside the requested date range"
            )


def translate_supplier_error(error: Exception, *, baostock: bool = False) -> Exception:
    """Map known supplier failures while preserving permanent programming errors."""
    if isinstance(error, (TypeError, ValueError)):
        return error
    if isinstance(
        error,
        (
            AuthenticationError,
            BaoStockSessionExpiredError,
            ContractError,
            RateLimitError,
            ServerError,
            TransientSourceError,
        ),
    ):
        return error
    message = str(error).lower()
    if any(
        word in message
        for word in ("token", "auth", "permission", "unauthorized", "权限", "没有接口")
    ):
        return AuthenticationError("supplier authentication failed")
    if baostock and any(word in message for word in ("not login", "session", "login")):
        return BaoStockSessionExpiredError("BaoStock session expired")
    if any(word in message for word in ("rate limit", "too many", "429")):
        return RateLimitError("supplier rate limit exceeded")
    server_markers = (
        "timeout",
        "temporar",
        "connection",
        "prematurely",
        "incomplete",
        "disconnected",
        "500",
        "502",
        "503",
        "网络",
        "连接失败",
        "接收错误",
    )
    if any(word in message for word in server_markers):
        return ServerError("supplier server failure")
    return error


def _first_present(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in frame.columns), None)


def _comparison_symbol(value: object) -> str:
    symbol = str(value)
    if symbol.isdigit() and len(symbol) <= 6:
        return symbol.zfill(6)
    return symbol


def host_of(url: str) -> str:
    """The bare host of a base URL, or ``""`` when there is none.

    Transport provenance is derived from the URL a client will actually
    reach, never from the client's type, so this is the single place that
    answers "which host is this?".  It is deliberately lenient: an
    unparseable or empty URL yields ``""`` and the caller decides whether
    that is fatal.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    without_scheme = text.split("//", 1)[-1]
    return without_scheme.split("/", 1)[0].split("?", 1)[0].strip()


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
