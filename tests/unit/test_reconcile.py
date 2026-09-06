"""Reconciliation of a frozen planned-order ledger against one scenario's
execution ledgers (Goal #3 fixed signal-day orders)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.backtest.models import (
    REASON_INSUFFICIENT_CASH,
    REASON_INSUFFICIENT_SELLABLE_QUANTITY,
    REASON_SUSPENDED_OR_UNKNOWN,
)
from stock_quant.data_model.trading_rules import REASON_BUY_AT_UPPER_LIMIT
from stock_quant.research.reconcile import (
    ORDER_DIFF_COLUMNS,
    STATUS_FILLED,
    STATUS_PARTIAL,
    STATUS_REJECTED,
    reconcile_orders,
)

D1 = date(2020, 1, 3)
D2 = date(2020, 1, 6)
D22 = date(2020, 2, 3)
D40 = date(2020, 2, 28)


def _plan(rows: list[dict]) -> pd.DataFrame:
    # An empty plan must still carry the six ledger columns, so the frame is
    # always built with an explicit schema (reconcile_orders requires them).
    frame = pd.DataFrame(
        rows,
        columns=["signal_date", "execution_date", "order_id", "side", "symbol",
                 "quantity"],
    )
    frame["signal_date"] = frame["signal_date"].map(date.fromisoformat)
    frame["execution_date"] = frame["execution_date"].map(date.fromisoformat)
    return frame.sort_values(["execution_date", "order_id"]).reset_index(drop=True)


def _submitted(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["trade_date", "order_id", "side", "symbol", "quantity"]
        )
    frame = pd.DataFrame(rows)
    frame["trade_date"] = frame["trade_date"].map(date.fromisoformat)
    return frame


def _fills(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["trade_date", "order_id", "quantity"])
    return pd.DataFrame(rows)


def _rejections(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["trade_date", "order_id", "requested_quantity",
                     "filled_quantity", "rejected_quantity", "reason"]
        )
    frame = pd.DataFrame(rows)
    for column in ("requested_quantity", "filled_quantity", "rejected_quantity"):
        frame[column] = frame[column].astype("int64")
    return frame


# A plan with every outcome: FILLED (o105), PARTIAL (o101), full REJECTED
# (o102/o103/o104).
def _default_plan() -> pd.DataFrame:
    return _plan(
        [
            {"signal_date": "2020-01-02", "execution_date": "2020-01-03",
             "order_id": "o101", "side": "BUY", "symbol": "600001.SH",
             "quantity": 200},
            {"signal_date": "2020-01-02", "execution_date": "2020-01-03",
             "order_id": "o102", "side": "SELL", "symbol": "600001.SH",
             "quantity": 200},
            {"signal_date": "2020-01-02", "execution_date": "2020-01-06",
             "order_id": "o105", "side": "BUY", "symbol": "600002.SH",
             "quantity": 100},
            {"signal_date": "2020-02-02", "execution_date": "2020-02-03",
             "order_id": "o104", "side": "BUY", "symbol": "600003.SH",
             "quantity": 100},
            {"signal_date": "2020-02-27", "execution_date": "2020-02-28",
             "order_id": "o103", "side": "BUY", "symbol": "600007.SH",
             "quantity": 100},
        ]
    )


def _default_submitted() -> pd.DataFrame:
    return _submitted(
        [
            {"trade_date": "2020-01-03", "order_id": "o101", "side": "BUY",
             "symbol": "600001.SH", "quantity": 200},
            {"trade_date": "2020-01-03", "order_id": "o102", "side": "SELL",
             "symbol": "600001.SH", "quantity": 200},
            {"trade_date": "2020-01-06", "order_id": "o105", "side": "BUY",
             "symbol": "600002.SH", "quantity": 100},
            {"trade_date": "2020-02-03", "order_id": "o104", "side": "BUY",
             "symbol": "600003.SH", "quantity": 100},
            {"trade_date": "2020-02-28", "order_id": "o103", "side": "BUY",
             "symbol": "600007.SH", "quantity": 100},
        ]
    )


def _default_fills() -> pd.DataFrame:
    return _fills(
        [
            {"trade_date": D1, "order_id": "o101", "quantity": 100},
            {"trade_date": D2, "order_id": "o105", "quantity": 100},
        ]
    )


def _default_rejections() -> pd.DataFrame:
    return _rejections(
        [
            {"trade_date": D1, "order_id": "o101", "requested_quantity": 200,
             "filled_quantity": 100, "rejected_quantity": 100,
             "reason": REASON_INSUFFICIENT_CASH},
            {"trade_date": D1, "order_id": "o102", "requested_quantity": 200,
             "filled_quantity": 0, "rejected_quantity": 200,
             "reason": REASON_INSUFFICIENT_SELLABLE_QUANTITY},
            {"trade_date": D22, "order_id": "o104", "requested_quantity": 100,
             "filled_quantity": 0, "rejected_quantity": 100,
             "reason": REASON_SUSPENDED_OR_UNKNOWN},
            {"trade_date": D40, "order_id": "o103", "requested_quantity": 100,
             "filled_quantity": 0, "rejected_quantity": 100,
             "reason": REASON_BUY_AT_UPPER_LIMIT},
        ]
    )


def _default_diffs() -> pd.DataFrame:
    # Diff rows are ordered by (execution_date, order_id) like the reconcile
    # output: o101 (partial) precedes o102 (rejected) on execution_date D1.
    return pd.DataFrame(
        [
            {"signal_date": date(2020, 1, 2), "execution_date": D1,
             "order_id": "o101", "side": "BUY", "symbol": "600001.SH",
             "planned_quantity": 200, "filled_quantity": 100,
             "rejected_quantity": 100, "reason": REASON_INSUFFICIENT_CASH,
             "status": STATUS_PARTIAL},
            {"signal_date": date(2020, 1, 2), "execution_date": D1,
             "order_id": "o102", "side": "SELL", "symbol": "600001.SH",
             "planned_quantity": 200, "filled_quantity": 0,
             "rejected_quantity": 200, "reason": REASON_INSUFFICIENT_SELLABLE_QUANTITY,
             "status": STATUS_REJECTED},
            {"signal_date": date(2020, 1, 2), "execution_date": D2,
             "order_id": "o105", "side": "BUY", "symbol": "600002.SH",
             "planned_quantity": 100, "filled_quantity": 100,
             "rejected_quantity": 0, "reason": "", "status": STATUS_FILLED},
            {"signal_date": date(2020, 2, 2), "execution_date": D22,
             "order_id": "o104", "side": "BUY", "symbol": "600003.SH",
             "planned_quantity": 100, "filled_quantity": 0,
             "rejected_quantity": 100, "reason": REASON_SUSPENDED_OR_UNKNOWN,
             "status": STATUS_REJECTED},
            {"signal_date": date(2020, 2, 27), "execution_date": D40,
             "order_id": "o103", "side": "BUY", "symbol": "600007.SH",
             "planned_quantity": 100, "filled_quantity": 0,
             "rejected_quantity": 100, "reason": REASON_BUY_AT_UPPER_LIMIT,
             "status": STATUS_REJECTED},
        ],
        columns=list(ORDER_DIFF_COLUMNS),
    ).astype(
        {
            "planned_quantity": "int64",
            "filled_quantity": "int64",
            "rejected_quantity": "int64",
        }
    )


def test_reconcile_derives_statuses_reasons_and_row_identity():
    diffs = reconcile_orders(
        plan=_default_plan(),
        submitted=_default_submitted(),
        fills=_default_fills(),
        rejections=_default_rejections(),
    )
    assert list(diffs.columns) == list(ORDER_DIFF_COLUMNS)
    assert diffs[["status", "order_id", "reason"]].values.tolist() == [
        [STATUS_PARTIAL, "o101", REASON_INSUFFICIENT_CASH],
        [STATUS_REJECTED, "o102", REASON_INSUFFICIENT_SELLABLE_QUANTITY],
        [STATUS_FILLED, "o105", ""],
        [STATUS_REJECTED, "o104", REASON_SUSPENDED_OR_UNKNOWN],
        [STATUS_REJECTED, "o103", REASON_BUY_AT_UPPER_LIMIT],
    ]
    # Row invariant: filled + rejected == planned on every row.
    assert (diffs["filled_quantity"] + diffs["rejected_quantity"]
            == diffs["planned_quantity"]).all()


def test_reconcile_keeps_signal_and_execution_dates_from_the_plan():
    diffs = reconcile_orders(
        plan=_default_plan(),
        submitted=_default_submitted(),
        fills=_default_fills(),
        rejections=_default_rejections(),
    )
    assert list(diffs["execution_date"]) == [D1, D1, D2, D22, D40]
    assert list(diffs["signal_date"]) == [
        date(2020, 1, 2), date(2020, 1, 2), date(2020, 1, 2),
        date(2020, 2, 2), date(2020, 2, 27),
    ]


def test_reconcile_rejects_an_empty_plan_with_columns_preserved():
    empty_plan = _plan([])
    diffs = reconcile_orders(
        plan=empty_plan,
        submitted=_submitted([]),
        fills=_fills([]),
        rejections=_rejections([]),
    )
    assert diffs.empty
    assert list(diffs.columns) == list(ORDER_DIFF_COLUMNS)


def test_reconcile_raises_when_submitted_diverges_from_the_plan_order_id():
    plan = _default_plan()
    submitted = _default_submitted()
    # Drop o101 from the submitted set: the ID chain must break loudly.
    submitted = submitted[submitted["order_id"] != "o101"].reset_index(drop=True)
    with pytest.raises(ValueError, match="order_id sets differ"):
        reconcile_orders(
            plan=plan,
            submitted=submitted,
            fills=_default_fills(),
            rejections=_default_rejections(),
        )


def test_reconcile_raises_when_a_submitted_order_disagrees_field_wise():
    submitted = _default_submitted()
    submitted.loc[submitted["order_id"] == "o101", "quantity"] = 300
    with pytest.raises(ValueError, match="disagrees with the plan"):
        reconcile_orders(
            plan=_default_plan(),
            submitted=submitted,
            fills=_default_fills(),
            rejections=_default_rejections(),
        )


def test_reconcile_raises_on_an_unknown_rejection_reason():
    rejections = _default_rejections().copy()
    rejections.loc[rejections["order_id"] == "o103", "reason"] = "surprise_reason"
    with pytest.raises(ValueError, match="unexpected rejection reason"):
        reconcile_orders(
            plan=_default_plan(),
            submitted=_default_submitted(),
            fills=_default_fills(),
            rejections=rejections,
        )


def test_reconcile_raises_on_a_fill_for_an_unknown_order():
    fills = _default_fills()
    fills = pd.concat(
        [fills, _fills([{"trade_date": D1, "order_id": "o999", "quantity": 100}])],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="missing from the plan"):
        reconcile_orders(
            plan=_default_plan(),
            submitted=_default_submitted(),
            fills=fills,
            rejections=_default_rejections(),
        )


def test_reconcile_raises_on_duplicate_plan_order_ids():
    plan = _default_plan()
    dup = plan.iloc[[0]]
    plan = pd.concat([plan, dup], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate order_ids"):
        reconcile_orders(
            plan=plan,
            submitted=_default_submitted(),
            fills=_default_fills(),
            rejections=_default_rejections(),
        )
