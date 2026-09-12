"""Tushare Pro adapter for unadjusted stock daily bars."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    ContractError,
    DataRequest,
    FetchResult,
    _utc_timestamp,
    request_key,
    request_metadata,
    translate_supplier_error,
    validate_supplier_frame,
)
from stock_quant.data_sources.tushare_proxy import TushareProxyClient
from stock_quant.data_sources.tushare_relay import TushareRelayClient
from stock_quant.data_sources.tushare_transport import (
    _STUB_HOST,
    OFFICIAL,
    PROXY,
    RELAY,
    TushareTransport,
    client_host,
    resolve_transport,
)


class TushareSource:
    """Fetch raw Tushare ``daily`` responses without column normalization.

    The transport is resolved once, explicitly, at construction (see
    :mod:`stock_quant.data_sources.tushare_transport`): a published build must
    name it and may only name the relay, while development and diagnostic
    callers opt into the ``relay -> official`` auto-order with
    ``allow_auto_transport=True``.  The request client is always an object
    that really has ``daily`` / ``index_daily`` / ``stock_basic``; provenance
    comes from ``self.transport``, never from the client's type -- an official
    session and a relay session are the same class, and only the base URL
    tells them apart.
    """

    name = "tushare"

    def __init__(
        self,
        config: SourceConfig,
        client: Any | None = None,
        *,
        transport: TushareTransport | None = None,
        sdk: Any | None = None,
        allow_auto_transport: bool = False,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        if transport is None:
            transport = (
                resolve_transport(
                    config,
                    allow_auto_transport=allow_auto_transport,
                    sdk=sdk,
                    environ=environ,
                )
                if client is None
                else injected_transport(client)
            )
        self._transport = transport
        self._client = transport.client
        self._sdk_version = transport.sdk_version

    @property
    def transport(self) -> TushareTransport:
        """The resolved transport, for diagnostics and tests."""
        return self._transport

    def _supplier_endpoint(self, endpoint: str) -> str:
        return self._transport.supplier_endpoint(endpoint)

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint == "stock_basic":
            return self._fetch_stock_basic(request)
        if request.endpoint in ("daily", "index_daily"):
            return self._fetch_symbol_series(request)
        raise ValueError(
            "TushareSource supports only the daily, index_daily, and "
            "stock_basic endpoints"
        )

    def _fetch_symbol_series(self, request: DataRequest) -> FetchResult:
        """Fetch one symbol-scoped unadjusted series (stock or index daily)."""
        if len(request.symbols) != 1:
            raise ValueError(
                f"Tushare {request.endpoint} requests require exactly one symbol"
            )
        if request.params.get("adjustment", "unadjusted") != "unadjusted":
            raise ValueError("Tushare daily data is available only unadjusted")
        request_timestamp = _utc_timestamp()
        try:
            frame = getattr(self._client, request.endpoint)(
                ts_code=request.symbols[0],
                start_date=request.start_date.strftime("%Y%m%d"),
                end_date=request.end_date.strftime("%Y%m%d"),
            )
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from None
        response_timestamp = _utc_timestamp()
        self._validate(frame, request)
        metadata = request_metadata(
            request,
            self._supplier_endpoint(request.endpoint),
            self._sdk_version,
            transport_id=self._transport.transport_id,
            request_timestamp=request_timestamp,
            response_timestamp=response_timestamp,
        )
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=metadata,
        )

    def _fetch_stock_basic(self, request: DataRequest) -> FetchResult:
        """Fetch one whole-market security-master reference snapshot.

        ``stock_basic`` is a single whole-market request: ``request.symbols``
        must be empty (it is never issued per symbol) and there is no
        window/date-range semantics on the endpoint.
        """
        if request.symbols:
            raise ValueError(
                "Tushare stock_basic is a whole-market request, not a "
                "symbol-scoped query"
            )
        request_timestamp = _utc_timestamp()
        try:
            frame = self._client.stock_basic(
                fields="ts_code,name,exchange,list_date,delist_date,list_status"
            )
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from None
        response_timestamp = _utc_timestamp()
        self._validate_stock_basic(frame)
        metadata = request_metadata(
            request,
            self._supplier_endpoint("stock_basic"),
            self._sdk_version,
            transport_id=self._transport.transport_id,
            request_timestamp=request_timestamp,
            response_timestamp=response_timestamp,
        )
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=metadata,
        )

    @staticmethod
    def _validate_stock_basic(frame: pd.DataFrame) -> None:
        """Validate the whole-market shape without date/symbol-set semantics."""
        if not isinstance(frame, pd.DataFrame):
            raise ContractError("supplier response is not a pandas DataFrame")
        if frame.empty:
            raise ContractError("supplier returned an empty response")
        required = ("ts_code", "name", "list_date", "delist_date", "list_status")
        missing = [name for name in required if name not in frame.columns]
        if missing:
            raise ContractError(
                "supplier response is missing columns: " + ", ".join(missing)
            )
        if frame["ts_code"].isna().any() or frame["list_status"].isna().any():
            raise ContractError("supplier response has a blank identity column")

    @staticmethod
    def _validate(frame: pd.DataFrame, request: DataRequest) -> None:
        try:
            validate_supplier_frame(
                frame,
                request,
                symbol_columns=("ts_code",),
                date_columns=("trade_date",),
            )
        except ContractError:
            raise


def injected_transport(client: Any) -> TushareTransport:
    """Describe a caller-injected request client (tests and local stubs).

    Injection is not a published path: the caller hands over the object, so a
    stub may have no URL to derive an identity from.  A client that declares a
    reachable ``host`` (the relay and proxy clients both do) is taken at its
    word; a stub that does not is labelled by its kind, and those kind labels
    (``proxy`` / ``relay`` / the official host) are documented as stub-only --
    ``resolve_transport`` never produces them.

    The two wrappers do not expose the same surface, so the *request client*
    is unwrapped per kind: ``TushareProxyClient`` answers the named endpoints
    itself, while ``TushareRelayClient`` only has ``query`` -- its named
    methods live on the SDK session it holds, which is why ``.api`` is used.
    """
    sdk_version = getattr(client, "sdk_version", None) or getattr(
        client, "__version__", "unknown"
    )
    if isinstance(client, TushareProxyClient):
        kind = PROXY
        session = client
    elif isinstance(client, TushareRelayClient):
        kind = RELAY
        session = client.api
    else:
        kind = OFFICIAL
        session = client
    host = client_host(client) or _STUB_HOST[kind]
    return TushareTransport(
        kind=kind, client=session, sdk_version=sdk_version, host=host
    )
