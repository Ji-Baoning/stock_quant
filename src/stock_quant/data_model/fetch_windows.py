"""Per-table fetch-window planning (spec A3 / D5.2, F1/F2).

The CLI --start/--end is the *minimal common window*, never a per-table
fetch window.  Each table's fetch window comes from its contract's
``incremental`` strategy; an explicit window that deviates from a table's
contract window makes that table skip fetching this round, recorded as
NOT_FETCHED with the operator_explicit_window reason (F1: an explicit
window neither overrides nor narrows a contract window).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from stock_quant.data_contracts import (
    INCREMENTAL_CHANGE_DRIVEN_FULL,
    INCREMENTAL_DISCLOSURE_CALENDAR,
    INCREMENTAL_LAST_COVERED_PLUS_1,
    DataContract,
)
from stock_quant.data_model.fetch_coverage import (
    KIND_FETCHED,
    KIND_NOT_FETCHED,
    NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
)


@dataclass(frozen=True)
class FetchWindowPlan:
    """One table's fetch decision for this update round."""

    table: str
    kind: str
    window_start: date | None
    window_end: date | None
    reason: str | None = None


def _contract_start(
    contract: DataContract,
    baseline_covered: tuple[date, date] | None,
    *,
    anchor_start: date,
    latest_open_day: date,
    lookback_days: int,
) -> date:
    """The planned fetch start for one table's ``incremental`` strategy."""
    strategy = contract.incremental
    if strategy == INCREMENTAL_LAST_COVERED_PLUS_1:
        if baseline_covered is None:
            return anchor_start
        return baseline_covered[1] + timedelta(days=1)
    if strategy == INCREMENTAL_DISCLOSURE_CALENDAR:
        return max(anchor_start, latest_open_day - timedelta(days=lookback_days))
    if strategy == INCREMENTAL_CHANGE_DRIVEN_FULL:
        return anchor_start
    raise ValueError(f"unknown incremental strategy {strategy!r}")


def plan_table_fetch_windows(
    contracts: Mapping[str, DataContract],
    baseline_covered: Mapping[str, tuple[date, date]],
    *,
    request_start: date | None,
    request_end: date | None,
    anchor_start: date,
    latest_open_day: date,
    lookback_days: int = 90,
) -> dict[str, FetchWindowPlan]:
    """Fetch plans for every declared table under the current CLI window."""
    plans: dict[str, FetchWindowPlan] = {}
    for table, contract in sorted(contracts.items()):
        start = _contract_start(
            contract,
            baseline_covered.get(table),
            anchor_start=anchor_start,
            latest_open_day=latest_open_day,
            lookback_days=lookback_days,
        )
        if request_start is not None and request_start != start:
            # Earlier than the contract start = the explicit window "overrides"
            # it; later = it "narrows" it; F1 forbids both, so skip the table.
            plans[table] = FetchWindowPlan(
                table,
                KIND_NOT_FETCHED,
                None,
                None,
                reason=NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
            )
            continue
        if request_end is not None and request_end < latest_open_day:
            plans[table] = FetchWindowPlan(
                table,
                KIND_NOT_FETCHED,
                None,
                None,
                reason=NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
            )
            continue
        plans[table] = FetchWindowPlan(table, KIND_FETCHED, start, latest_open_day)
    return plans
