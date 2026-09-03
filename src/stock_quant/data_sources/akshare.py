"""AKShare adapter for supplier-native reference and cross-check data."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    DataRequest,
    FetchResult,
    request_key,
    request_metadata,
    translate_supplier_error,
    validate_supplier_frame,
)


class AkShareSource:
    """Expose the AKShare endpoints needed by the raw-source boundary."""

    name = "akshare"

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
        handlers: dict[str, tuple[Callable[[DataRequest], pd.DataFrame], str, bool]] = {
            "index_history": (
                self._index_history,
                "akshare.stock_zh_index_hist_em",
                True,
            ),
            "stock_metadata": (
                self._stock_metadata,
                "akshare.stock_info_a_code_name",
                False,
            ),
            "cninfo_corporate_actions": (
                self._cninfo_corporate_actions,
                "akshare.stock_fhps_detail_cninfo",
                False,
            ),
            "eastmoney_corporate_actions": (
                self._eastmoney_corporate_actions,
                "akshare.stock_fhps_detail_em",
                False,
            ),
        }
        if request.endpoint not in handlers:
            raise ValueError(f"unsupported AKShare endpoint: {request.endpoint}")
        if len(request.symbols) != 1:
            raise ValueError("AKShare requests require exactly one symbol")
        handler, supplier_endpoint, date_required = handlers[request.endpoint]
        try:
            frame = handler(request)
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from error
        validate_supplier_frame(
            frame,
            request,
            symbol_columns=("代码", "code", "symbol", "ts_code"),
            date_columns=("日期", "date", "trade_date", "公告日期"),
            require_date=date_required,
        )
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(request, supplier_endpoint, self._sdk_version),
        )

    def _index_history(self, request: DataRequest) -> pd.DataFrame:
        return self._client.stock_zh_index_hist_em(
            symbol=request.symbols[0],
            period="daily",
            start_date=request.start_date.strftime("%Y%m%d"),
            end_date=request.end_date.strftime("%Y%m%d"),
            adjust="",
        )

    def _stock_metadata(self, request: DataRequest) -> pd.DataFrame:
        return self._client.stock_info_a_code_name()

    def _cninfo_corporate_actions(self, request: DataRequest) -> pd.DataFrame:
        return self._client.stock_fhps_detail_cninfo(symbol=request.symbols[0])

    def _eastmoney_corporate_actions(self, request: DataRequest) -> pd.DataFrame:
        return self._client.stock_fhps_detail_em(symbol=request.symbols[0])
