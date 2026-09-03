"""Tushare Pro adapter for unadjusted stock daily bars."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    DataRequest,
    FetchResult,
    _utc_timestamp,
    request_key,
    request_metadata,
    translate_supplier_error,
    validate_supplier_frame,
)


class TushareSource:
    """Fetch raw Tushare `daily` responses without column normalization."""

    name = "tushare"

    def __init__(self, config: SourceConfig, client: Any | None = None) -> None:
        self.config = config
        token = os.environ["TUSHARE_TOKEN"]
        if client is None:
            import tushare as ts

            try:
                client = ts.pro_api(token)
            except Exception:
                raise AuthenticationError(
                    "Tushare client initialization failed"
                ) from None
            self._sdk_version = getattr(ts, "__version__", "unknown")
        else:
            self._sdk_version = getattr(client, "__version__", "unknown")
        self._client = client

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint != "daily":
            raise ValueError(
                "TushareSource supports only the unadjusted daily endpoint"
            )
        if len(request.symbols) != 1:
            raise ValueError("Tushare daily requests require exactly one symbol")
        if request.params.get("adjustment", "unadjusted") != "unadjusted":
            raise ValueError("Tushare daily data is available only unadjusted")
        request_timestamp = _utc_timestamp()
        try:
            frame = self._client.daily(
                ts_code=request.symbols[0],
                start_date=request.start_date.strftime("%Y%m%d"),
                end_date=request.end_date.strftime("%Y%m%d"),
            )
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from error
        response_timestamp = _utc_timestamp()
        self._validate(frame, request)
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(
                request,
                "tushare.pro.daily",
                self._sdk_version,
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
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
