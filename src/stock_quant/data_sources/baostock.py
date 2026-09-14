"""BaoStock adapter for raw daily bars with explicit adjustment selection."""

from __future__ import annotations

from typing import Any

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


class BaoStockSource:
    """Open a BaoStock session per request and retain its native columns."""

    name = "baostock"
    _ADJUSTMENT_FLAGS = {"backward": "1", "forward": "2", "unadjusted": "3"}
    _FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg,tradestatus"

    def __init__(self, config: SourceConfig, client: Any | None = None) -> None:
        self.config = config
        if client is None:
            import baostock as bs

            client = bs
            self._sdk_version = getattr(bs, "__version__", "unknown")
        else:
            self._sdk_version = getattr(client, "__version__", "unknown")
        self._client = client

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint != "daily":
            raise ValueError("BaoStockSource supports only the daily endpoint")
        if len(request.symbols) != 1:
            raise ValueError("BaoStock daily requests require exactly one symbol")
        adjustment = request.params.get("adjustment", "unadjusted")
        if adjustment not in self._ADJUSTMENT_FLAGS:
            raise ValueError("adjustment must be unadjusted, forward, or backward")

        logged_in = False
        request_timestamp = _utc_timestamp()
        try:
            login = self._client.login()
            logged_in = True
            self._raise_for_response(login)
            response = self._client.query_history_k_data_plus(
                request.symbols[0],
                self._FIELDS,
                start_date=request.start_date.isoformat(),
                end_date=request.end_date.isoformat(),
                frequency="d",
                adjustflag=self._ADJUSTMENT_FLAGS[adjustment],
            )
            self._raise_for_response(response)
            frame = self._to_frame(response)
        except Exception as error:
            translated = translate_supplier_error(error, baostock=True)
            if translated is error:
                raise
            raise translated from None
        finally:
            if logged_in:
                self._client.logout()

        response_timestamp = _utc_timestamp()
        validate_supplier_frame(
            frame,
            request,
            symbol_columns=("code",),
            date_columns=("date",),
        )
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(
                request,
                "baostock.query_history_k_data_plus",
                self._sdk_version,
                transport_id="baostock",
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
        )

    @staticmethod
    def _raise_for_response(response: Any) -> None:
        if str(getattr(response, "error_code", "0")) != "0":
            message = getattr(response, "error_msg", "BaoStock request failed")
            raise RuntimeError(message)

    @staticmethod
    def _to_frame(response: Any) -> pd.DataFrame:
        if isinstance(response, pd.DataFrame):
            return response
        if not hasattr(response, "fields") or not hasattr(response, "next"):
            raise ContractError("BaoStock returned a truncated response")
        rows = []
        while response.next():
            rows.append(response.get_row_data())
        return pd.DataFrame(rows, columns=response.fields)
