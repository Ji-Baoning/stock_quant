"""PIT (point-in-time) fact access for disclosure-dated tables (spec D3).

Hard constraints: the version is pinned by the caller — ``as_of`` takes an
opened :class:`DatasetContext` for an exact version and NEVER reads CURRENT
(criterion 4); the module lives under ``research/``, not ``data_model/``;
fact-row selection follows the D2 registry's ``pit.fact_row_policy``.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_quant.data_contracts import (
    FACT_ROW_POLICY_MAX_REPORT_TYPE_V1,
    DataContract,
)


class PitConflictError(RuntimeError):
    """Same (as_of, end_date, report_type) carries conflicting values.

    Routes to manual adjudication (reviews.yml semantics) — never a silent
    side pick (spec D3).
    """


def as_of(
    context,
    table: str,
    symbol: str,
    field: str,
    as_of_date: date,
    *,
    contract: DataContract,
) -> tuple[object | None, list[dict]]:
    """Pinned-version PIT read; ``(value, warnings)`` for one fact field.

    ``context`` is an opened ``DatasetContext`` for an exact immutable
    version — the caller pins it, so a later publish can never change what
    this read sees (structural no-look-ahead, criterion 4).
    """
    frame = context.read(table)
    return _fact_row(frame, symbol, field, as_of_date, contract=contract)


def _fact_row(
    frame: pd.DataFrame,
    symbol: str,
    field: str,
    as_of_date: date,
    *,
    contract: DataContract,
) -> tuple[object | None, list[dict]]:
    pit = contract.pit
    if pit is None:
        raise ValueError(f"table {contract.table!r} has no pit contract")
    rows = frame[frame["symbol"].astype(str) == symbol]
    if rows.empty:
        return None, []
    point = pd.Timestamp(as_of_date)
    # Rows whose as_of date is unparseable never become visible (fail-closed).
    key_field = pit.as_of_field
    key_dates = pd.to_datetime(rows[key_field], errors="coerce")
    visible = rows[key_dates <= point]
    warnings: list[dict] = []
    used_fallback = False
    if visible.empty:
        used_fallback = True
        key_field = pit.fallback
        key_dates = pd.to_datetime(rows[key_field], errors="coerce")
        visible = rows[key_dates <= point]
    if visible.empty:
        return None, []
    visible_dates = key_dates.loc[visible.index]
    latest = visible_dates.max()
    candidates = visible[visible_dates == latest]
    if used_fallback:
        warnings.append(
            {
                "code": "pit_fallback",
                "symbol": symbol,
                "report_period": str(latest.date()),
            }
        )
    if len(candidates) == 1:
        return _value(candidates.iloc[0], field), warnings
    # Same (as_of, end_date, report_type) key: identical values deduplicate,
    # conflicting values fail into manual adjudication (spec D3).
    keyed = candidates.groupby(["end_date", "report_type"], dropna=False)
    winners = []
    for _, group in keyed:
        distinct = group[field].dropna().unique()
        if len(distinct) > 1:
            raise PitConflictError(
                f"{contract.table} {symbol} {field} conflicts for "
                f"(as_of={latest.date()}, end_date, report_type)"
            )
        winners.append(group.iloc[0])
    if len(winners) == 1:
        return _value(winners[0], field), warnings
    winner = _apply_fact_row_policy(pd.DataFrame(winners), field, pit.fact_row_policy)
    return _value(winner, field), warnings


def _value(row, field: str):
    value = row.get(field)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def _apply_fact_row_policy(candidates: pd.DataFrame, field: str, policy: str):
    """Versioned fact-row ruling (spec D2, owner 约束 3)."""
    if policy == FACT_ROW_POLICY_MAX_REPORT_TYPE_V1:
        ordered = candidates.sort_values(
            "report_type", key=lambda series: series.astype(str)
        )
        return ordered.iloc[-1]
    raise ValueError(f"unknown fact_row_policy {policy!r}")
