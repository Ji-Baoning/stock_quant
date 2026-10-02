"""The whitelisted table-preview endpoint and its query time budget.

Hard bounds (spec §8.2): the table must come from the version's manifest,
columns from the canonical schema whitelist, values travel as bound
parameters, ``limit`` defaults to 100 and caps at 500, and every query runs
under a wall-clock budget that is independent of the row limit (Datasette's
``--sql-time-limit-ms`` is the prior art). No arbitrary SQL, no file paths,
no downloads.
"""

from __future__ import annotations

import math
import re
import threading
from datetime import date, datetime
from typing import Annotated, Any

import duckdb
import pandas as pd
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, JsonValue

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.service.datasets import PinnedDataset, pinned_dataset
from stock_quant.service.errors import (
    QueryTimeBudgetExceeded,
    UnknownColumn,
    UnknownTable,
    UnsupportedFilter,
)
from stock_quant.service.security import quote_identifier

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TableArguments(BaseModel):
    """The parsed request parameters, echoed verbatim (spec §8.2)."""

    requested_version: str
    table: str
    columns: list[str]
    symbol: str | None = None
    trade_date: str | None = None
    offset: int = 0
    limit: int = DEFAULT_LIMIT


class TablePreviewResponse(BaseModel):
    dataset_version: str
    requested_version: str
    table: str
    arguments: TableArguments
    columns: list[str]
    rows: list[dict[str, JsonValue]]


def run_with_budget(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    params: tuple[Any, ...],
    budget_seconds: float,
) -> pd.DataFrame:
    """Run one query under a wall-clock budget, independent of the row limit.

    A daemon ``Timer`` interrupts the running query through
    ``DuckDBPyConnection.interrupt()``; an interrupted query surfaces as the
    stable ``query_time_budget_exceeded`` error, never as a raw DuckDB
    failure. The flag is set *before* interrupting so the except-branch can
    tell an intentional interruption from any other DuckDB error.
    """
    if budget_seconds <= 0:
        raise QueryTimeBudgetExceeded(
            f"query budget is {budget_seconds}s; refusing to run the query"
        )
    exceeded = threading.Event()

    def _interrupt() -> None:
        exceeded.set()
        try:
            connection.interrupt()
        except RuntimeError:
            pass  # the query already finished; nothing to interrupt

    timer = threading.Timer(budget_seconds, _interrupt)
    timer.daemon = True
    timer.start()
    try:
        return connection.execute(sql, list(params)).df()
    except duckdb.Error as error:
        if exceeded.is_set():
            raise QueryTimeBudgetExceeded(
                f"query exceeded its {budget_seconds}s time budget"
            ) from error
        raise
    finally:
        timer.cancel()


def _json_safe(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        # DuckDB hands DATE columns back as tz-naive midnight timestamps;
        # echo them in their date-only form while real (tz-aware) instants
        # keep their full timestamp rendering.
        if value.tz is None and value == value.normalize():
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (ValueError, AttributeError):
            return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


router = APIRouter(prefix="/api/v1", tags=["tables"])


@router.get("/datasets/{version}/tables/{table}", response_model=TablePreviewResponse)
def preview_table(
    pinned: Annotated[PinnedDataset, Depends(pinned_dataset)],
    table: str,
    request: Request,
    columns: str | None = Query(
        default=None, description="comma-separated canonical columns"
    ),
    symbol: str | None = Query(default=None),
    trade_date: str | None = Query(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> TablePreviewResponse:
    schema = STANDARDIZED_SCHEMAS.get(table)
    if (
        table not in pinned.context.tables
        or schema is None
        or not _TABLE_NAME_RE.fullmatch(table)
    ):
        raise UnknownTable(
            f"dataset {pinned.dataset_version} has no table {table!r}"
        )
    allowed = [field.name for field in schema]
    if columns is None:
        selected = list(allowed)
    else:
        selected = [name.strip() for name in columns.split(",") if name.strip()]
        unknown = [name for name in selected if name not in allowed]
        if unknown:
            raise UnknownColumn(
                f"table {table} has no column(s) {unknown}; whitelist: {allowed}"
            )
        if not selected:
            raise UnknownColumn("columns parameter selected no columns")
    clauses: list[str] = []
    params: list[Any] = []
    if symbol is not None:
        if "symbol" not in allowed:
            raise UnsupportedFilter(f"table {table} has no symbol column to filter")
        clauses.append(f"{quote_identifier('symbol')} = ?")
        params.append(symbol)
    if trade_date is not None:
        if "trade_date" not in allowed:
            raise UnsupportedFilter(
                f"table {table} has no trade_date column to filter"
            )
        clauses.append(f"CAST(? AS DATE) = {quote_identifier('trade_date')}")
        params.append(trade_date)
    sql = (
        "SELECT "
        + ", ".join(quote_identifier(name) for name in selected)
        + " FROM "
        + quote_identifier(table)
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY " + ", ".join(quote_identifier(name) for name in selected[:2])
    sql += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    frame = run_with_budget(
        pinned.context.connection,
        sql,
        tuple(params),
        float(request.app.state.query_budget_seconds),
    )
    rows = [
        {name: _json_safe(value) for name, value in zip(frame.columns, record)}
        for record in frame.itertuples(index=False, name=None)
    ]
    return TablePreviewResponse(
        dataset_version=pinned.dataset_version,
        requested_version=pinned.requested_version,
        table=table,
        arguments=TableArguments(
            requested_version=pinned.requested_version,
            table=table,
            columns=selected,
            symbol=symbol,
            trade_date=trade_date,
            offset=offset,
            limit=limit,
        ),
        columns=list(frame.columns),
        rows=rows,
    )
