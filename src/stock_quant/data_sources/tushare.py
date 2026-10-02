"""Tushare Pro adapter for unadjusted stock daily bars."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Mapping

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    ContractError,
    DataRequest,
    EMPTY_RESPONSE_MESSAGE,
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

#: The exchanges a published trading calendar must agree on.
_TRADE_CAL_EXCHANGES = ("SSE", "SZSE")

#: The native columns a ``trade_cal`` response must carry.
_TRADE_CAL_COLUMNS = ("cal_date", "is_open", "pretrade_date")

#: The daily_basic fields one basic_factor snapshot needs, and the native
#: columns a ``daily_basic`` response must carry (identity + value columns;
#: amount/OHLCV are ``daily_bar`` facts, spec §6.1).
_DAILY_BASIC_FIELDS = "ts_code,trade_date,total_mv,turnover_rate"
_DAILY_BASIC_COLUMNS = ("ts_code", "trade_date", "total_mv", "turnover_rate")

#: Index codes the index_weight endpoint may be asked for, frozen from the
#: §6.4 probe evidence (docs/operations/2026-10-01-endpoint-probe-evidence.
#: evidence.json, ``_status=measured``): 399300.SZ is lineage-proven (766-row
#: custom_csi300_tw_tradable); the CSI500/1000 entries are the probe-confirmed
#: codes -- never guessed from digit prefixes (spec §6.2).
INDEX_WEIGHT_INDEX_CODES = ("399300.SZ", "000905.SH", "000852.SH")

#: The native columns an ``index_weight`` monthly snapshot must carry.
_INDEX_WEIGHT_COLUMNS = ("index_code", "con_code", "trade_date", "weight")


def _month_shards(start: date, end: date) -> list[tuple[str, str, str]]:
    """(YYYYMM, YYYYMM01, YYYYMM31) month shards, lineage-shaped."""
    shards = []
    cursor = pd.Period(start.strftime("%Y%m"), freq="M")
    last = pd.Period(end.strftime("%Y%m"), freq="M")
    while cursor <= last:
        yearmonth = str(cursor).replace("-", "")
        shards.append((yearmonth, f"{yearmonth}01", f"{yearmonth}31"))
        cursor += 1
    return shards


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
        if request.endpoint == "trade_cal":
            return self._fetch_trade_cal(request)
        if request.endpoint in ("daily", "index_daily"):
            return self._fetch_symbol_series(request)
        if request.endpoint == "daily_basic":
            return self._fetch_daily_basic(request)
        if request.endpoint == "index_weight":
            return self._fetch_index_weight(request)
        raise ValueError(
            "TushareSource supports only the daily, daily_basic, index_daily, "
            "index_weight, stock_basic, and trade_cal endpoints"
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
            raise ContractError(EMPTY_RESPONSE_MESSAGE)
        required = ("ts_code", "name", "list_date", "delist_date", "list_status")
        missing = [name for name in required if name not in frame.columns]
        if missing:
            raise ContractError(
                "supplier response is missing columns: " + ", ".join(missing)
            )
        if frame["ts_code"].isna().any() or frame["list_status"].isna().any():
            raise ContractError("supplier response has a blank identity column")

    def _fetch_trade_cal(self, request: DataRequest) -> FetchResult:
        """Fetch one exchange's calendar for the requested date range.

        ``trade_cal`` is a per-exchange request: ``request.symbols`` must be
        empty and ``params["exchange"]`` must be one of SSE / SZSE.  The
        exchange is part of the request key, so the two exchanges of one
        refresh are two independent raw snapshots.  Values are validated
        later, by ``trade_calendar_facts.parse_trade_cal_frame``; this method
        only enforces the endpoint contract.
        """
        if request.symbols:
            raise ValueError(
                "Tushare trade_cal is a whole-exchange request, not a "
                "symbol-scoped query"
            )
        exchange = request.params.get("exchange")
        if exchange not in _TRADE_CAL_EXCHANGES:
            raise ValueError(
                "Tushare trade_cal requires an exchange of "
                f"{' or '.join(_TRADE_CAL_EXCHANGES)}, got {exchange!r}"
            )
        client_endpoint = getattr(self._client, "trade_cal", None)
        if client_endpoint is None:
            raise ValueError(
                f"tushare transport {self._transport.transport_id} has no "
                "trade_cal endpoint"
            )
        request_timestamp = _utc_timestamp()
        try:
            frame = client_endpoint(
                exchange=exchange,
                start_date=request.start_date.strftime("%Y%m%d"),
                end_date=request.end_date.strftime("%Y%m%d"),
            )
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from None
        response_timestamp = _utc_timestamp()
        self._validate_trade_cal(frame)
        metadata = request_metadata(
            request,
            self._supplier_endpoint("trade_cal"),
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
    def _validate_trade_cal(frame: pd.DataFrame) -> None:
        """Validate the native shape; day-set and value checks come later."""
        if not isinstance(frame, pd.DataFrame):
            raise ContractError("supplier response is not a pandas DataFrame")
        if frame.empty:
            raise ContractError("supplier returned an empty trade_cal response")
        missing = [name for name in _TRADE_CAL_COLUMNS if name not in frame.columns]
        if missing:
            raise ContractError(
                "supplier trade_cal response is missing columns: "
                + ", ".join(missing)
            )

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

    def _client_read(self, endpoint: str, **params: object) -> pd.DataFrame:
        """Named method when the transport has one, else the proxy's query()."""
        method = getattr(self._client, endpoint, None)
        if method is not None:
            return method(**params)
        query = getattr(self._client, "query", None)
        if query is None:
            raise ValueError(
                f"tushare transport {self._transport.transport_id} has no "
                f"{endpoint} endpoint"
            )
        return query(endpoint, **params)

    def _fetch_daily_basic(self, request: DataRequest) -> FetchResult:
        """One whole-market daily_basic snapshot for a single trade date.

        Per-day vocabulary (spec §6.2): ``symbols`` empty, window exactly one
        day, callers paginate by day.  Only identity + total_mv/turnover_rate
        are requested -- amount/OHLCV are ``daily_bar`` facts (spec §6.1).
        """
        if request.symbols:
            raise ValueError(
                "Tushare daily_basic is a whole-market per-day request, not a "
                "symbol-scoped query"
            )
        if request.start_date != request.end_date:
            raise ValueError(
                "Tushare daily_basic requests exactly one trade_date "
                "(start_date must equal end_date)"
            )
        request_timestamp = _utc_timestamp()
        try:
            frame = self._client_read(
                "daily_basic",
                trade_date=request.start_date.strftime("%Y%m%d"),
                fields=_DAILY_BASIC_FIELDS,
            )
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from None
        response_timestamp = _utc_timestamp()
        self._validate_daily_basic(frame, request)
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

    @staticmethod
    def _validate_daily_basic(frame: pd.DataFrame, request: DataRequest) -> None:
        """Classify the daily_basic contract; an empty day is never success."""
        if not isinstance(frame, pd.DataFrame):
            raise ContractError("supplier response is not a pandas DataFrame")
        missing = [name for name in _DAILY_BASIC_COLUMNS if name not in frame.columns]
        if missing:
            raise ContractError(
                "supplier daily_basic response is missing columns: "
                + ", ".join(missing)
            )
        duplicated = frame.duplicated(subset=["ts_code", "trade_date"]).sum()
        if duplicated:
            raise ContractError(
                f"supplier daily_basic response has {int(duplicated)} "
                "duplicate primary-key rows"
            )
        validate_supplier_frame(
            frame,
            request,
            symbol_columns=("ts_code",),
            date_columns=("trade_date",),
            require_symbol=False,
        )

    def _fetch_index_weight(self, request: DataRequest) -> FetchResult:
        """Month-sharded index_weight snapshots, one sha256 per month.

        Sharding mirrors the lineage script's proven shape (spec §6.2).  An
        empty month is recorded as a rows=0 shard, never interpreted as "no
        constituents that day"; an all-empty window fails the contract.
        """
        if len(request.symbols) != 1:
            raise ValueError("Tushare index_weight requests require exactly "
                             "one index code")
        index_code = request.symbols[0]
        if index_code not in INDEX_WEIGHT_INDEX_CODES:
            raise ValueError(
                f"index code {index_code!r} is not in the probe-frozen index "
                f"table ({', '.join(INDEX_WEIGHT_INDEX_CODES)}); extend the "
                "table from probe evidence, never by guessing")
        request_timestamp = _utc_timestamp()
        frames: list[pd.DataFrame] = []
        digests: list[dict[str, object]] = []
        for yearmonth, month_start, month_end in _month_shards(
                request.start_date, request.end_date):
            try:
                frame = self._client_read(
                    "index_weight", index_code=index_code,
                    start_date=month_start, end_date=month_end)
            except Exception as error:
                translated = translate_supplier_error(error)
                if translated is error:
                    raise
                raise translated from None
            period = pd.Period(yearmonth, freq="M")
            frame = self._empty_month_as_shard(frame)
            self._validate_index_weight(frame, DataRequest(
                request.endpoint, request.symbols,
                max(period.start_time.date(), request.start_date),
                min(period.end_time.date(), request.end_date),
                dict(request.params)))
            frames.append(frame)
            digests.append({
                "month": yearmonth, "rows": int(len(frame)),
                "sha256": hashlib.sha256(
                    frame.to_csv(index=False).encode("utf-8")).hexdigest()})
        combined = pd.concat(frames, ignore_index=True)
        validate_supplier_frame(  # all-empty windows fail here
            combined, request, symbol_columns=("index_code",),
            date_columns=("trade_date",))
        metadata = request_metadata(
            request, self._supplier_endpoint("index_weight"),
            self._sdk_version, transport_id=self._transport.transport_id,
            request_timestamp=request_timestamp,
            response_timestamp=_utc_timestamp())
        metadata["index_weight_snapshots"] = json.dumps(digests, sort_keys=True)
        return FetchResult(source=self.name, endpoint=request.endpoint,
                           request_key=request_key(request), frame=combined,
                           metadata=metadata)

    @staticmethod
    def _empty_month_as_shard(frame: pd.DataFrame | None) -> pd.DataFrame:
        """Absorb the supplier's empty-month shapes exactly as the lineage script did.

        ``collect_index_weight_membership.py:133-137`` substituted an empty
        4-column frame whenever a month's response was ``None`` or a
        column-less empty frame -- the shape a month predating the index's
        coverage comes back as.  Keeping that rule here makes such a month a
        recorded rows=0 shard instead of failing the whole window; a
        columns-bearing empty frame already passes ``_validate_index_weight``.
        """
        if frame is None or (frame.empty and not len(frame.columns)):
            return pd.DataFrame(columns=list(_INDEX_WEIGHT_COLUMNS))
        return frame

    @staticmethod
    def _validate_index_weight(frame: pd.DataFrame, request: DataRequest) -> None:
        """Per-month shape; empty months pass through as recorded evidence."""
        if not isinstance(frame, pd.DataFrame):
            raise ContractError("supplier response is not a pandas DataFrame")
        missing = [c for c in _INDEX_WEIGHT_COLUMNS if c not in frame.columns]
        if missing:
            raise ContractError("supplier index_weight response is missing "
                                "columns: " + ", ".join(missing))
        if frame.empty:
            return
        duplicated = frame.duplicated(subset=["con_code", "trade_date"]).sum()
        if duplicated:
            raise ContractError(
                f"supplier index_weight response has {int(duplicated)} "
                "duplicate primary-key rows")
        validate_supplier_frame(frame, request, symbol_columns=("index_code",),
                                date_columns=("trade_date",))


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
