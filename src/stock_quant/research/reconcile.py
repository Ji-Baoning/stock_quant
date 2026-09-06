"""Planned-order ledger reconciliation (Goal #3 fixed signal-day orders).

``reconcile_orders`` is the single place that turns one scenario's execution
ledgers back into a per-``order_id`` plan-to-fill/rejection diff.  It first
enforces the stable-ID-chain invariants (the submitted set equals the frozen
plan ledger and agrees field-for-field on every order, spec section 3), then
left-joins the plan against the scenario's fills and rejections so every
planned order gets one ``order_diffs`` row whose quantities always sum back to
the plan.
"""

from __future__ import annotations

import pandas as pd

from stock_quant.backtest.models import (
    REASON_INSUFFICIENT_CASH,
    REASON_INSUFFICIENT_SELLABLE_QUANTITY,
    REASON_SUSPENDED_OR_UNKNOWN,
    REASON_UNCOVERED_RULE,
)
from stock_quant.data_model.trading_rules import (
    REASON_BUY_AT_UPPER_LIMIT,
    REASON_SELL_AT_LOWER_LIMIT,
)

#: Order-level reasons reachable in a rejection stream when a frozen plan is
#: replayed through the engine (spec section 5).  The run-level veto reasons
#: (``missing_open`` / ``missing_pre_close`` / ``quality_error``) never reach a
#: rejection record inside a completed backtest; meeting one here is a contract
#: breach and reconciles to a hard error.
ORDER_LEVEL_REASONS = frozenset(
    {
        REASON_SUSPENDED_OR_UNKNOWN,
        REASON_INSUFFICIENT_CASH,
        REASON_INSUFFICIENT_SELLABLE_QUANTITY,
        REASON_UNCOVERED_RULE,
        REASON_BUY_AT_UPPER_LIMIT,
        REASON_SELL_AT_LOWER_LIMIT,
    }
)

STATUS_FILLED = "FILLED"
STATUS_REJECTED = "REJECTED"
STATUS_PARTIAL = "PARTIAL"

#: Canonical order_diffs columns (spec section 4).
ORDER_DIFF_COLUMNS = (
    "signal_date",
    "execution_date",
    "order_id",
    "side",
    "symbol",
    "planned_quantity",
    "filled_quantity",
    "rejected_quantity",
    "reason",
    "status",
)

_PLAN_COLUMNS = (
    "signal_date",
    "execution_date",
    "order_id",
    "side",
    "symbol",
    "quantity",
)
_SUBMITTED_COLUMNS = ("trade_date", "order_id", "side", "symbol", "quantity")


def reconcile_orders(
    *,
    plan: pd.DataFrame,
    submitted: pd.DataFrame,
    fills: pd.DataFrame,
    rejections: pd.DataFrame,
) -> pd.DataFrame:
    """One scenario's plan-ledger diff, invariant-checked against submission.

    ``plan`` is the frozen ``orders.parquet`` ledger with ``signal_date`` and
    ``execution_date`` already normalised to ``date`` objects (columns per
    ``_PLAN_COLUMNS``); ``submitted`` / ``fills`` / ``rejections`` are the
    engine's frames for the same scenario.  Raises ``ValueError`` when the
    stable-ID-chain invariants (spec section 3) or the row identity
    ``filled + rejected == planned`` (spec section 4) are broken.
    """
    _require_columns(plan, _PLAN_COLUMNS, "plan")
    _require_columns(submitted, _SUBMITTED_COLUMNS, "submitted")
    _require_columns(fills, ("order_id", "quantity"), "fills")
    _require_columns(
        rejections, ("order_id", "rejected_quantity", "reason"), "rejections"
    )

    plan_ids = list(plan["order_id"])
    if len(set(plan_ids)) != len(plan_ids):
        raise ValueError("planned-order ledger contains duplicate order_ids")
    _assert_submitted_identity(plan, submitted)

    filled_by_id: dict[str, int] = {}
    if len(fills):
        filled_by_id = {
            order_id: int(rows["quantity"].sum())
            for order_id, rows in fills.groupby("order_id")
        }
    rejected_by_id: dict[str, tuple[int, str]] = {}
    if len(rejections):
        for order_id, rows in rejections.groupby("order_id"):
            if len(rows) != 1:
                raise ValueError(f"order {order_id} has multiple rejection records")
            row = rows.iloc[0]
            reason = str(row["reason"])
            if reason not in ORDER_LEVEL_REASONS:
                raise ValueError(
                    f"unexpected rejection reason {reason!r} on {order_id}"
                )
            rejected_by_id[order_id] = (int(row["rejected_quantity"]), reason)

    known = set(plan_ids)
    foreign = (set(filled_by_id) | set(rejected_by_id)) - known
    if foreign:
        raise ValueError(
            "fills/rejections reference orders missing from the plan: "
            + ", ".join(sorted(foreign))
        )

    plan_by_id = {row["order_id"]: row for row in plan.to_dict("records")}
    rows: list[dict] = []
    for record in sorted(
        plan_by_id.values(), key=lambda r: (r["execution_date"], r["order_id"])
    ):
        order_id = str(record["order_id"])
        planned = int(record["quantity"])
        filled = int(filled_by_id.get(order_id, 0))
        rejected_quantity, reason = rejected_by_id.get(order_id, (0, ""))
        if filled + rejected_quantity != planned:
            raise ValueError(
                f"order {order_id}: filled {filled} + rejected {rejected_quantity} "
                f"!= planned {planned}"
            )
        if filled == 0 and rejected_quantity == 0:
            raise ValueError(
                f"order {order_id} produced neither a fill nor a rejection"
            )
        if filled == planned:
            status = STATUS_FILLED
        elif filled == 0:
            status = STATUS_REJECTED
        else:
            status = STATUS_PARTIAL
        rows.append(
            {
                "signal_date": record["signal_date"],
                "execution_date": record["execution_date"],
                "order_id": order_id,
                "side": str(record["side"]),
                "symbol": str(record["symbol"]),
                "planned_quantity": planned,
                "filled_quantity": filled,
                "rejected_quantity": rejected_quantity,
                "reason": "" if status == STATUS_FILLED else reason,
                "status": status,
            }
        )
    frame = pd.DataFrame(rows, columns=list(ORDER_DIFF_COLUMNS))
    return frame.astype(
        {
            "planned_quantity": "int64",
            "filled_quantity": "int64",
            "rejected_quantity": "int64",
        }
    )


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{name} frame must include columns {list(missing)}")


def _assert_submitted_identity(plan: pd.DataFrame, submitted: pd.DataFrame) -> None:
    """Spec section 3: same order_id set, no duplicates, fields agree per id."""
    submitted_ids = list(submitted["order_id"])
    if len(set(submitted_ids)) != len(submitted_ids):
        raise ValueError("submitted_orders contains duplicate order_ids")
    plan_ids = set(plan["order_id"])
    if set(submitted_ids) != plan_ids:
        missing = sorted(plan_ids - set(submitted_ids))
        extra = sorted(set(submitted_ids) - plan_ids)
        raise ValueError(
            f"submitted/plan order_id sets differ; missing {missing}, extra {extra}"
        )
    submitted_by_id = {row["order_id"]: row for row in submitted.to_dict("records")}
    for record in plan.to_dict("records"):
        order_id = str(record["order_id"])
        row = submitted_by_id[order_id]
        if (
            str(row["side"]) != str(record["side"])
            or str(row["symbol"]) != str(record["symbol"])
            or int(row["quantity"]) != int(record["quantity"])
            or row["trade_date"] != record["execution_date"]
        ):
            raise ValueError(f"submitted order {order_id} disagrees with the plan")
