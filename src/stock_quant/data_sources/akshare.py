"""AKShare adapter for supplier-native reference and cross-check data."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    DataRequest,
    FetchResult,
    _utc_timestamp,
    default_request_timeout,
    request_key,
    request_metadata,
    translate_supplier_error,
    validate_supplier_frame,
)

#: The upstream vendor behind each AKShare endpoint this adapter can reach.
#: Provenance needs the answering *vendor* rather than the akshare wrapper,
#: because two vendors can return byte-identical frames (design §2.3) -- and
#: ``index_history`` really does switch between them at runtime.
_UPSTREAM_VENDOR = {
    "akshare.stock_zh_index_daily_em": "eastmoney",
    "akshare.stock_zh_index_hist_em": "eastmoney",
    "akshare.stock_zh_index_daily": "sina",
    "akshare.stock_zh_index_daily_tx": "tencent",
    "akshare.stock_info_a_code_name": "eastmoney",
    "akshare.stock_dividend_cninfo": "cninfo",
    "akshare.stock_fhps_detail_em": "eastmoney",
    "akshare.stock_fhps_detail_ths": "ths",
    "akshare.stock_allotment_cninfo": "cninfo",
}


def _transport_id(supplier_endpoint: str) -> str:
    """The interface that answered, as a path-safe id (design §2.3).

    The vendor alone is **not** enough.  ``stock_zh_index_daily_em`` and
    ``stock_zh_index_hist_em`` are two different EastMoney interfaces serving
    the same logical ``index_history`` endpoint, and ``_index_history``'s
    fallback chain can switch between them run to run.  If both were labelled
    ``eastmoney``, two byte-identical frames would land on one
    content-addressed path and the second save would silently keep the first
    one's ``supplier_endpoint`` -- precisely the collision the transport layer
    exists to prevent.  So the id carries the vendor *and* the interface.

    An endpoint with no vendor mapping names itself under an ``akshare``
    prefix rather than sharing a constant.
    """
    slug = supplier_endpoint.rsplit(".", 1)[-1].replace("_", "-")
    vendor = _UPSTREAM_VENDOR.get(supplier_endpoint, "akshare")
    return f"{vendor}.{slug}"


class AkShareSource:
    """Expose the AKShare endpoints needed by the raw-source boundary."""

    name = "akshare"
    _INDEX_FALLBACKS = (
        ("akshare.stock_zh_index_daily", "stock_zh_index_daily"),
        ("akshare.stock_zh_index_daily_tx", "stock_zh_index_daily_tx"),
    )
    _INDEX_SYMBOL_COLUMNS = ("代码", "code", "symbol", "ts_code")
    _INDEX_DATE_COLUMNS = ("日期", "date", "trade_date", "公告日期")

    def __init__(self, config: SourceConfig, client: Any | None = None) -> None:
        self.config = config
        if client is None:
            import akshare as ak

            client = ak
            self._sdk_version = getattr(ak, "__version__", "unknown")
        else:
            self._sdk_version = getattr(client, "__version__", "unknown")
        self._client = client

    def fetch(self, request: DataRequest) -> FetchResult:
        handlers: dict[
            str, tuple[Callable[[DataRequest], pd.DataFrame], str, bool, bool]
        ] = {
            "index_history": (
                self._index_history,
                "",
                True,
                True,
            ),
            "stock_metadata": (
                self._stock_metadata,
                "akshare.stock_info_a_code_name",
                False,
                False,
            ),
            "cninfo_corporate_actions": (
                self._cninfo_corporate_actions,
                "akshare.stock_dividend_cninfo",
                False,
                False,
            ),
            "eastmoney_corporate_actions": (
                self._eastmoney_corporate_actions,
                "akshare.stock_fhps_detail_em",
                False,
                False,
            ),
            "rights_issue_corporate_actions": (
                self._rights_issue_corporate_actions,
                "akshare.stock_allotment_cninfo",
                False,
                False,
            ),
        }
        if request.endpoint not in handlers:
            raise ValueError(f"unsupported AKShare endpoint: {request.endpoint}")
        if len(request.symbols) != 1:
            raise ValueError("AKShare requests require exactly one symbol")
        handler, supplier_endpoint, date_required, require_symbol = handlers[
            request.endpoint
        ]
        if request.endpoint == "index_history":
            require_symbol = not self._uses_current_index_history_api()
        request_timestamp = _utc_timestamp()
        try:
            # AKShare passes no ``timeout`` to ``requests`` at all -- its
            # ``stock_zh_index_daily_em`` is a bare module-level call -- so
            # without this an unanswered upstream holds the caller forever.
            # That is not hypothetical: it cost the 2026-09-17 rebuild seven
            # hours.  Bounding here rather than only in ``fetch_with_retry``
            # matters because this adapter is also driven directly (the
            # ``project/`` probes), and ``config`` is what the caller
            # configured for exactly this.
            with default_request_timeout(self.config.timeout_seconds):
                if request.endpoint == "index_history":
                    frame, supplier_endpoint = self._index_history(request)
                else:
                    frame = handler(request)
                    supplier_endpoint = frame.attrs.get(
                        "supplier_endpoint", supplier_endpoint
                    )
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from None
        response_timestamp = _utc_timestamp()
        validate_supplier_frame(
            frame,
            request,
            symbol_columns=("代码", "code", "symbol", "ts_code"),
            date_columns=("日期", "date", "trade_date", "公告日期"),
            require_date=date_required,
            require_symbol=require_symbol,
            allow_empty=request.endpoint
            in {
                "cninfo_corporate_actions",
                "eastmoney_corporate_actions",
                "rights_issue_corporate_actions",
            },
        )
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(
                request,
                supplier_endpoint,
                self._sdk_version,
                transport_id=_transport_id(supplier_endpoint),
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
        )

    def _index_history(self, request: DataRequest) -> tuple[pd.DataFrame, str]:
        """Fetch index daily bars, falling back eastmoney -> sina -> tencent.

        Returns the raw frame of the first candidate that satisfies the
        contract together with the endpoint that produced it.
        """
        symbol = _eastmoney_index_symbol(request.symbols[0])
        start_date = request.start_date.strftime("%Y%m%d")
        end_date = request.end_date.strftime("%Y%m%d")
        candidates: list[tuple[str, Callable[[], pd.DataFrame], bool]] = []
        if self._uses_current_index_history_api():
            candidates.append(
                (
                    "akshare.stock_zh_index_daily_em",
                    lambda: self._client.stock_zh_index_daily_em(
                        symbol=symbol, start_date=start_date, end_date=end_date
                    ),
                    False,
                )
            )
            if not symbol.startswith(("bj", "csi")):
                for endpoint, method in self._INDEX_FALLBACKS:
                    if hasattr(self._client, method):
                        fallback = getattr(self._client, method)
                        candidates.append(
                            (endpoint, lambda fn=fallback: fn(symbol=symbol), True)
                        )
        else:
            candidates.append(
                (
                    "akshare.stock_zh_index_hist_em",
                    lambda: self._client.stock_zh_index_hist_em(
                        symbol=request.symbols[0],
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust="",
                    ),
                    False,
                )
            )
        return self._first_valid_index(request, candidates)

    def _first_valid_index(
        self,
        request: DataRequest,
        candidates: list[tuple[str, Callable[[], pd.DataFrame], bool]],
    ) -> tuple[pd.DataFrame, str]:
        """Return the first candidate that satisfies the raw index contract."""
        first_error: Exception | None = None
        for endpoint, call, full_history in candidates:
            try:
                frame = call()
                if full_history:
                    frame = _clip_to_window(frame, request)
                validate_supplier_frame(
                    frame,
                    request,
                    symbol_columns=self._INDEX_SYMBOL_COLUMNS,
                    date_columns=self._INDEX_DATE_COLUMNS,
                    require_symbol=False,
                    require_date=True,
                )
            except Exception as error:  # noqa: BLE001 - fallback boundary
                if first_error is None:
                    first_error = error
                continue
            return frame, endpoint
        assert first_error is not None
        raise first_error

    def _uses_current_index_history_api(self) -> bool:
        return hasattr(self._client, "stock_zh_index_daily_em")

    def _stock_metadata(self, request: DataRequest) -> pd.DataFrame:
        return self._client.stock_info_a_code_name()

    def _cninfo_corporate_actions(self, request: DataRequest) -> pd.DataFrame:
        symbol = request.symbols[0].split(".", maxsplit=1)[0]
        try:
            return self._client.stock_dividend_cninfo(symbol=symbol)
        except KeyError as error:
            if str(error) != "'实施方案公告日期'":
                raise
            return pd.DataFrame()

    def _eastmoney_corporate_actions(self, request: DataRequest) -> pd.DataFrame:
        symbol = request.symbols[0].split(".", maxsplit=1)[0]
        try:
            return self._client.stock_fhps_detail_em(symbol=symbol)
        except TypeError as error:
            if "'NoneType' object is not subscriptable" not in str(error):
                raise
            frame = self._client.stock_fhps_detail_ths(symbol=symbol)
            frame.attrs["supplier_endpoint"] = "akshare.stock_fhps_detail_ths"
            return frame

    def _rights_issue_corporate_actions(self, request: DataRequest) -> pd.DataFrame:
        """CNINFO's allotment frame: the only source of subscription facts.

        A symbol with no subscription answers an empty frame rather than
        raising, so no benign-error translation is needed here as it is for the
        two dividend interfaces.
        """
        symbol = request.symbols[0].split(".", maxsplit=1)[0]
        return self._client.stock_allotment_cninfo(
            symbol=symbol,
            start_date=request.start_date.strftime("%Y%m%d"),
            end_date=request.end_date.strftime("%Y%m%d"),
        )


def _eastmoney_index_symbol(symbol: str) -> str:
    """Add the market prefix required by AKShare's current EM endpoint."""
    symbol = symbol.split(".", maxsplit=1)[0]
    if symbol.startswith(("sh", "sz", "csi")):
        return symbol
    return f"sz{symbol}" if symbol.startswith("399") else f"sh{symbol}"


def _clip_to_window(frame: pd.DataFrame, request: DataRequest) -> pd.DataFrame:
    """Restrict a full-history frame to the requested dates without renaming."""
    date_column = next(
        (column for column in ("日期", "date", "trade_date") if column in frame),
        None,
    )
    if date_column is None or frame.empty:
        return frame
    raw = frame[date_column].astype(str)
    if raw.str.fullmatch(r"\d{8}").all():
        dates = pd.to_datetime(raw, format="%Y%m%d", errors="coerce")
    else:
        dates = pd.to_datetime(raw, errors="coerce")
    start = pd.Timestamp(request.start_date)
    end = pd.Timestamp(request.end_date)
    return frame.loc[(dates >= start) & (dates <= end)]
