"""BaoStock adapter for raw daily bars with explicit adjustment selection."""

from __future__ import annotations

import contextlib
import io
import socket
from typing import Any

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    ContractError,
    DataRequest,
    FetchResult,
    ServerError,
    _utc_timestamp,
    request_key,
    request_metadata,
    translate_supplier_error,
    validate_supplier_frame,
)

#: The 0.9.3 SDK speaks raw TCP with no timeout of its own, and its
#: ``socketutil.send_msg`` swallows every socket exception: it prints one of
#: these strings to stdout and returns ``None``.  A swallowed failure during
#: pagination makes ``ResultData.next()`` return ``False`` as a normal
#: exhaustion, so the only observable signal is the print -- without scanning
#: for it, a mid-window stall would silently truncate the frame.
_SDK_TRANSPORT_FAILURE_MARKERS = (
    "服务器连接失败",  # socketutil.connect could not reach :10030
    "接收数据异常",  # socketutil.send_msg send/recv raised
    "you don't login",  # send_msg without a usable socket
    "当前页面编号不正确",  # pagination could not request the next page
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
        captured = io.StringIO()
        previous_timeout = socket.getdefaulttimeout()
        try:
            # The SDK's raw sockets never set a timeout, so a stalled server
            # would block the run forever (the 2026-09-19 update hung 40+min in
            # the login recv).  config.timeout_seconds is the documented knob;
            # bound the process default for the duration of the call and
            # restore it after.  Fetches are sequential, so nothing else races
            # on the default (same reasoning as default_request_timeout in
            # base.py, whose requests-bound mechanism cannot reach this SDK).
            socket.setdefaulttimeout(self.config.timeout_seconds)
            with contextlib.redirect_stdout(captured):
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
                self._raise_for_swallowed_transport_failure(captured)
        except Exception as error:
            translated = translate_supplier_error(error, baostock=True)
            if translated is error:
                raise
            raise translated from None
        finally:
            if logged_in:
                # A logout failure is harmless: every fetch opens a fresh
                # socket, so the next login cannot inherit the broken one.
                # Logout still runs while the timeout binds -- its recv uses
                # the same flaky server, and once the default is restored it
                # would be the one unbounded read in the fetch (the
                # 2026-09-21 stall hung inside exactly this recv).
                with contextlib.redirect_stdout(captured):
                    self._client.logout()
            socket.setdefaulttimeout(previous_timeout)

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
    def _raise_for_swallowed_transport_failure(captured: io.StringIO) -> None:
        output = captured.getvalue()
        marker = next(
            (marker for marker in _SDK_TRANSPORT_FAILURE_MARKERS if marker in output),
            None,
        )
        if marker is not None:
            raise ServerError(
                "BaoStock SDK swallowed a transport failure "
                f"({marker!r}); the fetch must not return a partial frame"
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
