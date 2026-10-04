"""Benchmark closes from one pinned dataset version (v3 §7.2).

The benchmark is not a separate table: it is ``daily_bar`` rows whose symbol
is a benchmark symbol (default 000300.SH -- PRIMARY_BENCHMARK_SYMBOL's value,
restated locally so the service keeps importing neither research nor
analytics, spec §8.1/ADR-021).  The window parameters belong to this
dedicated endpoint only; the table-preview contract (pin I4) is untouched.

Reads go through the same pinned-connection query path as the table preview:
the table must come from the version's manifest, values travel as bound
parameters, the row count is capped, and every query runs under the shared
wall-clock budget (``tables.run_with_budget``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.service.datasets import PinnedDataset, pinned_dataset
from stock_quant.service.errors import UnknownTable
from stock_quant.service.security import quote_identifier
from stock_quant.service.tables import _json_safe, run_with_budget

#: Mirrors ``analytics.performance.PRIMARY_BENCHMARK_SYMBOL`` (no import).
DEFAULT_BENCHMARK_SYMBOL = "000300.SH"
_BENCHMARK_TABLE = "daily_bar"
_MAX_ROWS = 8000


class BenchmarkRow(BaseModel):
    trade_date: str
    close: float


class BenchmarkResponse(BaseModel):
    dataset_version: str
    requested_version: str
    symbol: str
    rows: list[BenchmarkRow]


router = APIRouter(prefix="/api/v1", tags=["benchmark"])


@router.get("/datasets/{version}/benchmark", response_model=BenchmarkResponse)
def benchmark_closes(
    pinned: Annotated[PinnedDataset, Depends(pinned_dataset)],
    request: Request,
    symbol: Annotated[
        str, Query(pattern=r"^[0-9]{6}\.(SH|SZ)$")
    ] = DEFAULT_BENCHMARK_SYMBOL,
    start: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    end: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
) -> BenchmarkResponse:
    schema = STANDARDIZED_SCHEMAS.get(_BENCHMARK_TABLE)
    if _BENCHMARK_TABLE not in pinned.context.tables or schema is None:
        raise UnknownTable(
            f"dataset {pinned.dataset_version} has no table {_BENCHMARK_TABLE!r}"
        )
    conditions = [f"{quote_identifier('symbol')} = ?"]
    params: list[Any] = [symbol]
    if start is not None:
        conditions.append(f"{quote_identifier('trade_date')} >= CAST(? AS DATE)")
        params.append(start)
    if end is not None:
        conditions.append(f"{quote_identifier('trade_date')} <= CAST(? AS DATE)")
        params.append(end)
    sql = (
        f"SELECT {quote_identifier('trade_date')}, {quote_identifier('close')}"
        f" FROM {quote_identifier(_BENCHMARK_TABLE)}"
        " WHERE " + " AND ".join(conditions)
        + f" ORDER BY {quote_identifier('trade_date')} LIMIT ?"
    )
    params.append(_MAX_ROWS)
    frame = run_with_budget(
        pinned.context.connection,
        sql,
        tuple(params),
        float(request.app.state.query_budget_seconds),
    )
    rows = [
        BenchmarkRow(trade_date=str(_json_safe(trade_date)), close=float(close))
        for trade_date, close in frame.itertuples(index=False, name=None)
    ]
    return BenchmarkResponse(
        dataset_version=pinned.dataset_version,
        requested_version=pinned.requested_version,
        symbol=symbol,
        rows=rows,
    )
