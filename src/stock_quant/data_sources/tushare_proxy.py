"""GET transport for the shared Tushare-compatible aggregation front.

The proxy fronts Tushare Pro data with one GET endpoint per API
(``<base>/daily?ts_code=...``) authenticated by an ``X-API-Key`` header; it
does not speak the official SDK's POST protocol.  ``TushareProxyClient``
mirrors the small SDK surface the adapters consume (``daily``,
``index_daily``, ``stock_basic``) and adds the guards its shared-server
nature demands (each observed live on 2026-09-12):

- responses truncate silently at ~6000 rows, so date ranges are fetched in
  bounded windows and concatenated;
- the upstream pool intermittently answers ``upstream_pool_exhausted`` or
  stalls mid-body, so transient failures retry with backoff;
- some endpoints apply ``start_date``/``end_date`` differently from the
  official API (observed: ``dividend`` filtered to empty, ``suspend_d``
  losing rows when given ``suspend_type``), so symbol-scoped reads pass
  simple parameters only and post-filter the returned rows client-side.

It is a transport for Tushare-format data, not an evidence source: frames
keep the tushare layout (``vol`` lots, ``amount`` thousand-yuan) so the
existing normalization contract is unchanged, while raw-store snapshots pin
this transport through the fetch metadata.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Literal, Mapping, cast

import pandas as pd
import requests

from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    ServerError,
)

_TRANSIENT_MARKERS = (
    "pool",
    "exhausted",
    "timeout",
    "timed out",
    "busy",
    "unavailable",
    "overload",
)
_AUTH_MARKERS = ("token", "api key", "api_key", "auth", "unauthorized", "forbidden")
_TRANSIENT_HTTP_STATUS = (429, 500, 502, 503, 504)
_BACKOFF_SECONDS = (2, 6, 12, 20, 30)
_MAX_ATTEMPTS = len(_BACKOFF_SECONDS) + 1
# A per-symbol daily response gains ~250 rows per year, so five-year windows
# stay two orders of magnitude below the ~6000-row response truncation.
_WINDOW_YEARS = 5
_WINDOW_PAUSE_SECONDS = 0.2
#: Interfaces the code itself pins, so their names need no runtime check.
_NAMED_ENDPOINTS = ("daily", "index_daily", "stock_basic")
#: The catalog table carries no TTL of its own; 21600 is the value the
#: overwhelming majority (181/298) of the individual interfaces declare.
_CATALOG_TTL_SECONDS = 21600


def _date_windows(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Split an inclusive ``YYYYMMDD`` range into bounded calendar chunks."""
    start = pd.Timestamp(str(start_date))
    end = pd.Timestamp(str(end_date))
    windows: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        boundary = (
            pd.Timestamp(year=cursor.year + _WINDOW_YEARS, month=1, day=1)
            - pd.Timedelta(days=1)
        )
        chunk_end = min(end, boundary)
        windows.append(
            (cursor.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"))
        )
        cursor = chunk_end + pd.Timedelta(days=1)
    return windows


def _api_error(label: str, body: object) -> Exception:
    if isinstance(body, dict):
        message = str(body.get("error") or body.get("msg") or body)[:200]
    else:
        message = str(body)[:200]
    lowered = message.lower()
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return AuthenticationError(f"proxy rejected {label}: {message}")
    if any(marker in lowered for marker in _TRANSIENT_MARKERS):
        return ServerError(f"proxy {label} transient upstream failure: {message}")
    code = body.get("code") if isinstance(body, dict) else None
    return ContractError(f"proxy {label} non-zero code={code}: {message}")


def _parse_catalog(label: str, body: object) -> Mapping[str, object]:
    if not isinstance(body, dict) or "interfaces" not in body:
        raise _api_error(label, body)
    return body


def _parse_interface(label: str, body: object) -> Mapping[str, object]:
    if not isinstance(body, dict) or "name" not in body:
        raise _api_error(label, body)
    return body


def _parse_upstreams(label: str, body: object) -> Mapping[str, object]:
    if not isinstance(body, dict) or "results" not in body:
        raise _api_error(label, body)
    return body


def _cache_ttl(body: Mapping[str, object]) -> float:
    """The interface's own declared cache TTL, else the catalog default."""
    try:
        ttl = float(body.get("cache_ttl"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(_CATALOG_TTL_SECONDS)
    return ttl if ttl > 0 else float(_CATALOG_TTL_SECONDS)


def _lower_headers(response: requests.Response) -> dict[str, str]:
    headers = getattr(response, "headers", None) or {}
    return {str(key).lower(): str(value) for key, value in dict(headers).items()}


def _cache_label(headers: Mapping[str, str]) -> str:
    cache = headers.get("x-cache", "")
    layer = headers.get("x-cache-layer", "")
    return "/".join(part for part in (cache, layer) if part)


class TushareProxyClient:
    """One authenticated GET session against the aggregation front."""

    sdk_version = "tushare_proxy-1.0"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_attempts = min(max_retries + 1, _MAX_ATTEMPTS)
        self._sleeper = sleeper
        self._clock = clock
        self._session = session or requests.Session()
        self._session.headers["X-API-Key"] = api_key
        # Metadata lives one level above the data plane:
        # ``<root>/pro/{endpoint}`` for data, ``<root>/capabilities`` for shape.
        self._root = self.base_url.rsplit("/", 1)[0]
        self._catalog: tuple[float, pd.DataFrame] | None = None
        self._interface_cache: dict[str, tuple[float, Mapping[str, object]]] = {}
        #: Audit trail of the most recent generic `query()` call.  Named reads
        #: (daily/index_daily/stock_basic) never write it; check that the last
        #: call was `query()` before reading it.
        self.last_query_metadata: dict[str, object] | None = None

    @classmethod
    def from_env(
        cls,
        *,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> TushareProxyClient | None:
        """Build the client from ``TUSHARE_PROXY_URL``/``TUSHARE_PROXY_KEY``.

        Returns ``None`` when either variable is unset or blank, leaving the
        adapter on the official SDK path.
        """
        base_url = os.environ.get("TUSHARE_PROXY_URL", "").strip()
        api_key = os.environ.get("TUSHARE_PROXY_KEY", "").strip()
        if not base_url or not api_key:
            return None
        return cls(
            base_url,
            api_key,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            session=session,
            sleeper=sleeper,
            clock=clock,
        )

    @property
    def host(self) -> str:
        """The bare host of the configured base URL (audit metadata only)."""
        without_scheme = self.base_url.split("//", 1)[-1]
        return without_scheme.split("/", 1)[0]

    # ------------------------------------------------------------------ #
    # SDK-mirroring surface (the methods TushareSource calls)             #
    # ------------------------------------------------------------------ #

    def daily(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        **_ignored: object,
    ) -> pd.DataFrame:
        frame, _ = self._paged(
            "daily", ts_code=ts_code, start_date=start_date, end_date=end_date
        )
        return frame

    def index_daily(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        **_ignored: object,
    ) -> pd.DataFrame:
        frame, _ = self._paged(
            "index_daily", ts_code=ts_code, start_date=start_date, end_date=end_date
        )
        return frame

    def stock_basic(
        self,
        exchange: str = "",
        list_status: str = "L",
        fields: str | None = None,
        **_ignored: object,
    ) -> pd.DataFrame:
        """One whole-market master read (no windowing; generous read bound)."""
        params: dict[str, object] = {
            "exchange": exchange,
            "list_status": list_status,
        }
        if fields:
            params["fields"] = fields
        # The whole-market body was observed to exceed a 30s read under load.
        return self._query(
            "stock_basic",
            read_timeout=max(self.timeout_seconds, 90),
            **params,
        )

    def query(
        self,
        endpoint: str,
        *,
        verify_capability: Literal["live", "none"] = "live",
        **params: object,
    ) -> pd.DataFrame:
        """One generic read from the catalog.

        ``verify_capability="live"`` pre-flights the interface's declared
        shape and fails fast on a deterministic error.  **It is a shape check
        only.**  Passing it says nothing about whether the data is correct or
        attributable: the catalog has been falsified once already (it declared
        60 req/min per IP where the headers said 200), and
        ``fallback_on_empty`` describes *another source answering in this
        one's place*.  Pre-flight reads the interface's shape, never the
        data's provenance.
        """
        if endpoint in _NAMED_ENDPOINTS:
            verify_capability = "none"
        checked: bool | None = None
        if verify_capability == "live":
            checked = self._verify_capability(endpoint, params)
        start_date = params.pop("start_date", None)
        end_date = params.pop("end_date", None)
        log: list[dict[str, str]] = []
        frame, windows = self._paged(
            endpoint, start_date=start_date, end_date=end_date, log=log, **params
        )
        self.last_query_metadata = {
            "endpoint": endpoint,
            "capability_checked": checked,
            "windows": [tuple(window) for window in windows],
            "rows": int(len(frame)),
            "request_ids": [
                entry["request_id"] for entry in log if entry["request_id"]
            ],
            "cache": [entry["cache"] for entry in log if entry["cache"]],
        }
        return frame

    def _verify_capability(self, endpoint: str, params: Mapping[str, object]) -> bool:
        """Check the declared shape before spending a data request.

        Returns ``True`` when the pre-flight passed and ``False`` when it
        could not be fetched at all (**fail-open**) -- the server's
        ``allow_unregistered_apis: false`` is the real gatekeeper, and a
        metadata hiccup must not block a data read.  Deterministic rejections
        raise instead: a disabled interface, a non-GET interface, or
        unsatisfied ``required`` / ``required_any``.
        """
        try:
            capability = self.capability(endpoint)
        except ServerError:
            return False
        if capability.get("enabled") is False:
            raise ContractError(f"proxy interface {endpoint} is disabled")
        methods = [str(method).upper() for method in (capability.get("methods") or [])]
        if "GET" not in methods:
            raise ContractError(
                f"proxy interface {endpoint} does not allow GET: "
                f"{capability.get('methods')}"
            )
        missing = [
            str(name)
            for name in (capability.get("required") or [])
            if str(name) not in params
        ]
        if missing:
            raise ContractError(
                f"proxy interface {endpoint} requires: {', '.join(missing)}"
            )
        required_any = capability.get("required_any") or []
        if required_any:
            groups = [[str(name) for name in group] for group in required_any]
            if not any(all(name in params for name in group) for group in groups):
                alternatives = " | ".join(",".join(group) for group in groups)
                raise ContractError(
                    f"proxy interface {endpoint} requires one of: {alternatives}"
                )
        return True

    # ------------------------------------------------------------------ #
    # Capability discovery (metadata plane)                               #
    # ------------------------------------------------------------------ #

    def capabilities(self, *, refresh: bool = False) -> pd.DataFrame:
        """The interface catalog, one row per declared interface (298 rows).

        This is the service's **declaration**, not a guarantee: the same
        catalog declared 60 requests/min per IP where the live response
        headers said 200, and reports ``enabled=true`` for interfaces that
        return zero rows.
        """
        now = self._clock()
        if not refresh and self._catalog is not None and now < self._catalog[0]:
            return self._catalog[1]
        body = cast(
            Mapping[str, object],
            self._request(f"{self._root}/capabilities", _parse_catalog),
        )
        frame = pd.DataFrame(body.get("interfaces") or [])
        self._catalog = (now + _CATALOG_TTL_SECONDS, frame)
        return frame

    def capability(self, name: str) -> Mapping[str, object]:
        """One interface's declared shape, cached for its own ``cache_ttl``."""
        now = self._clock()
        cached = self._interface_cache.get(name)
        if cached is not None and now < cached[0]:
            return cached[1]
        body = cast(
            Mapping[str, object],
            self._request(f"{self._root}/capabilities/{name}", _parse_interface),
        )
        self._interface_cache[name] = (now + _cache_ttl(body), body)
        return body

    def upstreams(self, name: str) -> Mapping[str, object]:
        """Which upstreams the proxy *reports* for an interface.

        The list is not the answering set: probed on 2026-09-12, all six
        upstreams reported ``suspend_d`` unsupported while the data endpoint
        served it.  Treat this as a diagnostic, never as attribution.
        """
        return cast(
            Mapping[str, object],
            self._request(f"{self._root}/upstreams/probe/{name}", _parse_upstreams),
        )

    # ------------------------------------------------------------------ #
    # Transport                                                           #
    # ------------------------------------------------------------------ #

    def _paged(
        self,
        endpoint: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        log: list[dict[str, str]] | None = None,
        **params: object,
    ) -> tuple[pd.DataFrame, list[tuple[str | None, str | None]]]:
        """Fetch bounded date windows and enforce the range client-side."""
        if start_date and end_date:
            windows: list[tuple[str | None, str | None]] = _date_windows(
                start_date, end_date
            )
        else:
            windows = [(start_date, end_date)]
        frames = []
        for window_start, window_end in windows:
            frames.append(
                self._query(
                    endpoint,
                    start_date=window_start,
                    end_date=window_end,
                    log=log,
                    **params,
                )
            )
            if len(windows) > 1:
                self._sleeper(_WINDOW_PAUSE_SECONDS)
        if not frames:
            return pd.DataFrame(), windows
        frame = pd.concat(frames, ignore_index=True)
        if frame.empty or not (start_date and end_date):
            return frame, windows
        # Client-side range enforcement: date parameters are not uniformly
        # honoured upstream, and validate_supplier_frame enforces the range.
        # Endpoints outside the bar family have no ``trade_date`` at all; for
        # those the requested range is passed through rather than enforced.
        if "trade_date" not in frame.columns:
            return frame, windows
        dates = frame["trade_date"].astype(str)
        within = (dates >= str(start_date)) & (dates <= str(end_date))
        return frame[within].reset_index(drop=True), windows

    def _query(
        self,
        endpoint: str,
        *,
        read_timeout: int | None = None,
        log: list[dict[str, str]] | None = None,
        **params: object,
    ) -> pd.DataFrame:
        """One data read; retry, throttling and recording live in `_request`."""
        return cast(
            pd.DataFrame,
            self._request(
                f"{self.base_url}/{endpoint}",
                self._parse,
                read_timeout=read_timeout,
                log=log,
                **params,
            ),
        )

    def _request(
        self,
        url: str,
        parse: Callable[[str, object], object],
        *,
        read_timeout: int | None = None,
        log: list[dict[str, str]] | None = None,
        **params: object,
    ) -> object:
        """One GET with retry. ``parse(label, body)`` converts the JSON body.

        Every response -- including transient ones -- is handed to the
        recorder, so a failure still leaves its request id behind.
        """
        payload = {key: value for key, value in params.items() if value is not None}
        last_error: ServerError | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self._session.get(
                    url,
                    params=payload,
                    timeout=(10, read_timeout or self.timeout_seconds),
                )
            except requests.exceptions.RequestException as error:
                last_error = ServerError(f"proxy {url} transport failure: {error}")
            else:
                self._record(log, response)
                if response.status_code in _TRANSIENT_HTTP_STATUS:
                    last_error = ServerError(f"proxy {url} HTTP {response.status_code}")
                elif response.status_code != 200:
                    raise ContractError(
                        f"proxy {url} HTTP {response.status_code}: "
                        f"{response.text[:120]}"
                    )
                else:
                    try:
                        body = response.json()
                    except ValueError:
                        # Transient upstream bodies (pool exhaustion,
                        # non-JSON gateway pages) participate in the backoff.
                        last_error = ServerError(
                            f"proxy {url} returned a non-JSON body"
                        )
                    else:
                        try:
                            return parse(url, body)
                        except ServerError as error:
                            last_error = error
            if attempt < self.max_attempts - 1:
                self._sleeper(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
        assert last_error is not None
        raise last_error

    def _record(
        self, log: list[dict[str, str]] | None, response: requests.Response
    ) -> None:
        """Keep the only after-the-fact handle on an un-attributable response.

        Nothing in the body or headers names the answering upstream, so the
        request id is what an operator can quote back to the proxy operator,
        and the cache flags are the only clue whether the body was served
        fresh or replayed.
        """
        if log is None:
            return
        headers = _lower_headers(response)
        log.append(
            {
                "request_id": headers.get("x-request-id", ""),
                "cache": _cache_label(headers),
            }
        )

    def _parse(self, label: str, body: object) -> pd.DataFrame:
        if isinstance(body, dict) and body.get("code") == 0:
            data = body.get("data") or {}
            return pd.DataFrame(data.get("items", []), columns=data.get("fields", []))
        raise _api_error(label, body)
