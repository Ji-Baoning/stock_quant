# 固定信号日订单（纯意图计划账本重放）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把研究运行改为固定信号日订单——`orders.parquet` 成为权威计划账本，引擎以固定 `schedule`（`OrderDay`）逐笔交给 `ExecutionSimulator`，执行日只产生成交/部分成交/拒单，并按稳定 `order_id` 对账出 `order_diffs`、以新 metrics/报告键呈现计划-执行差异；删除 09-05 的执行日可执行性投影。

**Architecture:** 先独立新增 `reconcile` 模块（纯函数 + 单测，无依赖）；再把 runner 切换到计划账本重放（`orders.parquet` 补 `signal_date`、`_read_order_schedule`、每情景写 `submitted_orders` + `order_diffs`、analytics 换新键、测试更新），此时引擎仍保留投影死代码（不影响任何运行、测试全绿）；随后一次性删除引擎投影路径与 `rebalance.py`，替换投影测试为纯意图重放测试并补 P0.3 验收测试；最后改造 rich report 的"执行偏离诊断"列与模板。

**Tech Stack:** Python 3.11+，pandas，pyarrow（parquet），ruff，pytest。

## Global Constraints

- 仓库门禁：`ruff check src tests` 干净；**绝不**要求全仓 `ruff format --check` PASS（仓库基线 format-dirty，见 ruff-format-baseline memory），也不得为凑格式重排无关遗留文件——只格式化本特性改动行。
- 术语契约（spec §术语）：计划订单/计划账本 = `orders.parquet`；提交/执行订单 = `submitted_orders.parquet`；成交 = fills；拒单/部分成交 = rejections；差异 = `order_diffs.parquet`。**删除** `executable_target` 与 `pre-trade adjustment` 概念；代码/报告不再出现"可执行数量 / 调仓前约束"。
- reason 值原文透传，不重映射；运行时可达订单级 reason 集合（spec §5）= `{suspended_or_unknown, insufficient_cash, insufficient_sellable_quantity, uncovered_rule, buy_at_upper_limit, sell_at_lower_limit}`。`missing_open`/`missing_pre_close`/`quality_error` 是 run 级 veto，永不进入拒单流水。
- **避免冗余测试**：拒单流水 `reason` 非空由 `RejectedOrder.__post_init__`（models.py:256）硬保证，**不新增**相关测试；执行器逐 reason 行为已在 `tests/unit/test_execution.py` 单测，引擎层不再重测执行器内部。
- `research/models.py` 的 `REQUIRED_ARTIFACTS` 只含实验根文件（含 `orders.parquet`），不含 `backtest/{scenario}/` 下文件；每情景产物清单在 `runner._produce_backtest` 的 outputs dict（即 run manifest 工件清单）——替换在那里做，**不改 models.py**。
- 提交/代码标识符用英文；工作区为 worktree `…/.worktrees/phase-one-quant-system`，分支 `feature/phase-one-quant-system`，当前 head `0b3d7a1`。
- `orders.parquet` 的 `signal_date`/`execution_date` 在 runner 读取处统一 `_as_date` 归一到 `date` 对象后再进 engine/reconcile，避免 parquet 往返的 datetime 类型干扰。

---

### Task 1: `reconcile` 纯函数模块（计划↔提交↔成交↔拒单对账）

**Files:**
- Create: `src/stock_quant/research/reconcile.py`
- Create: `tests/unit/test_reconcile.py`

**Interfaces:**
- Consumes: `pandas.DataFrame`；engine 提交/成交/拒单 frame（`submitted_orders`/`fills`/`rejections` 列见 spec §4）；`orders.parquet` 计划 frame。
- Produces: `reconcile.reconcile_orders(*, plan, submitted, fills, rejections) -> pd.DataFrame`（order_diffs frame，列见 `ORDER_DIFF_COLUMNS`）；常量 `ORDER_DIFF_COLUMNS`、`ORDER_LEVEL_REASONS`、`STATUS_FILLED = "FILLED"`、`STATUS_REJECTED = "REJECTED"`、`STATUS_PARTIAL = "PARTIAL"`。Task 3 的 runner 与 cli analytics 依赖这些名字。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_reconcile.py`：

```python
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
    # Empty guard mirrors _fills/_rejections: pd.DataFrame([]) has no
    # columns, so _submitted([]) would KeyError before reconcile runs.
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/unit/test_reconcile.py -v`
Expected: FAIL（`ModuleNotFoundError`/`ImportError`：`stock_quant.research.reconcile` 不存在）。

- [ ] **Step 3: 实现 `reconcile.py`**

创建 `src/stock_quant/research/reconcile.py`：

```python
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

_PLAN_COLUMNS = ("signal_date", "execution_date", "order_id", "side", "symbol", "quantity")
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
                raise ValueError(f"unexpected rejection reason {reason!r} on {order_id}")
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
            raise ValueError(f"order {order_id} produced neither a fill nor a rejection")
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
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/unit/test_reconcile.py -v && ruff check src/stock_quant/research/reconcile.py tests/unit/test_reconcile.py`
Expected: 全 PASS；ruff 无输出。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/research/reconcile.py tests/unit/test_reconcile.py
git commit -m "feat: reconcile planned-order ledger into per-order diffs"
```

---

### Task 2: Runner 切换到固定计划账本重放 + 引擎 schedule 提交收集（加法）

**Files:**
- Modify: `src/stock_quant/backtest/engine.py`（加法：schedule 路径逐笔收集 submitted；投影路径不动）
- Modify: `src/stock_quant/research/runner.py`
- Modify: `src/stock_quant/cli.py`（`_ExperimentAnalytics.compute` 镜像新键）
- Modify: `tests/integration/test_backtest_engine.py`（新增 golden submitted 引擎测试）
- Modify: `tests/integration/test_research_runner.py`
- Modify: `tests/integration/test_end_to_end.py`

**Interfaces:**
- Consumes: Task 1 的 `reconcile.reconcile_orders`；engine 的固定 `schedule`（`OrderDay`）路径（本任务为其补 submitted 收集）；`target_schedule`/投影仍是死代码，Task 3 删除。
- Produces: runner 每情景产物 `backtest/{scenario}/{fills,rejections,action_ledger,daily_equity,submitted_orders,order_diffs}.parquet`，**不再写** `rebalance_adjustments.parquet`、`executable_targets.parquet`；`orders.parquet` 列 = `signal_date, execution_date, order_id, side, symbol, quantity`；metrics.json `scenarios[<name>]` 新键（spec §7）替换 pretrade 三键，两处 analytics 键字节一致（cli 追加 `performance`）；引擎 schedule 路径 `submitted_orders` == 计划账本。

本任务为一次原子切换：引擎 schedule 收集 + 删产物写 + analytics 读新文件必须在同一提交，否则 `_produce_report`/reconcile 崩溃。

- [ ] **Step 1: 更新 `_produce_portfolio`：`orders.parquet` 补 `signal_date`**

在 `runner.py` `_produce_portfolio`（约 1148 与 1166 两个 `order_rows.append({...})` 块）每个 dict 增加 `"signal_date": signal,` 首键；并把 1196-1197 行替换为（列序内联；**空账本也带全六列**，下游 `_read_order_ledger`/reconcile 依赖该 schema 而非行数）：

```python
        order_frame = pd.DataFrame(
            order_rows,
            columns=["signal_date", "execution_date", "order_id", "side",
                     "symbol", "quantity"],
        )
        order_frame.to_parquet(self._run_dir / "orders.parquet", index=False)
```

- [ ] **Step 2: 引擎 schedule 路径先补提交收集（加法，红→绿）**

Runner 本任务切到 schedule 重放后，reconcile 要求 `submitted_orders` 与计划账本逐笔一致，但引擎当前只在 target 投影路径收集 submitted（`_collect_projection`），schedule 路径的 `submitted_orders` 恒空。此处先做**纯加法**：在 schedule 分支逐笔收集；target 投影路径与 `TargetDay` 原样保留（下一个任务才删），因此 `test_target_day_projects_...` 保持通过。

**2a. 写失败测试（引擎 golden submitted 恒等，spec §3）**——在 `tests/integration/test_backtest_engine.py` 中，把 line 37 从 engine 的 import 追加 `SUBMITTED_ORDER_COLUMNS`（保留 `TargetDay`）：

```python
from stock_quant.backtest.engine import (
    BacktestEngine,
    BacktestRequest,
    OrderDay,
    ReadinessError,
    SUBMITTED_ORDER_COLUMNS,
    TargetDay,
)
```

并新加一个测试：

```python
def test_golden_submitted_orders_match_the_plan_schedule():
    """Spec section 3: on the fixed schedule the submitted set equals the plan
    ledger order-for-order (id is the join key, fields confirm)."""
    result = BacktestEngine().run(_request("full_cost"))
    rows = []
    for order_day in _MARKET.schedule:
        for order in list(order_day.sells) + list(order_day.buys):
            rows.append(
                {
                    "trade_date": order_day.trade_date,
                    "order_id": order.order_id,
                    "side": order.side,
                    "symbol": order.symbol,
                    "quantity": order.quantity,
                }
            )
    expected = pd.DataFrame(rows, columns=list(SUBMITTED_ORDER_COLUMNS))
    expected["quantity"] = expected["quantity"].astype("int64")
    assert_frame_equal(result.submitted_orders, expected)
    assert result.submitted_orders["order_id"].is_unique
```

Run: `pytest tests/integration/test_backtest_engine.py::test_golden_submitted_orders_match_the_plan_schedule -v`
Expected: FAIL（schedule 路径 submitted 为空 → 不等）。

**2b. 实现**——在 `engine.py` `_collect`（405 行附近）旁新增静态方法：

```python
    @staticmethod
    def _collect_submitted(
        day: date,
        orders: Sequence[Order],
        submitted_orders: list[dict],
    ) -> None:
        """One audit row per plan order submitted on ``day`` (spec section 2)."""
        submitted_orders.extend(
            {
                "trade_date": day,
                "order_id": order.order_id,
                "side": order.side,
                "symbol": order.symbol,
                "quantity": order.quantity,
            }
            for order in orders
        )
```

并把 `run()` 的 `else:`（schedule）分支改为构建 orders 后立即逐笔收集（**只改 else 分支**；target 分支维持 `_collect_projection`，二者互不重复）：

```python
            else:
                sells, buys = market.schedule_by(day)
                orders = list(sells) + list(buys)
                # One submitted record per plan order (spec section 2): the
                # schedule path emits the raw plan as the audit trail.  The
                # target projection path keeps its own collection until the
                # execution-day projection is removed in the next task.
                self._collect_submitted(day, orders, submitted_orders)
```

Run: `pytest tests/integration/test_backtest_engine.py::test_golden_submitted_orders_match_the_plan_schedule tests/integration/test_backtest_engine.py::test_target_day_projects_cash_and_static_market_constraints_before_execution -v`
Expected: golden 测试 PASS；既有投影测试 PASS（schedule 空 day 的收集为空，不影响 target 断言）。

- [ ] **Step 3: 替换 runner 的 schedule 构造与 `_load_market` 类型**

把 import 行 43 改为：

```python
from stock_quant.backtest.engine import BacktestEngine, BacktestRequest, OrderDay
```

在模块 import 区加 reconcile import（`BacktestRequest` 之下；两处 analytics 复用 `STATUS_*`，避免状态串各自硬编码）：

```python
from stock_quant.research.reconcile import (
    STATUS_FILLED,
    STATUS_PARTIAL,
    STATUS_REJECTED,
    reconcile_orders,
)
```

把 `_read_target_schedule` 方法（约 1304-1330）整体替换为两个方法：

```python
    def _read_order_ledger(self) -> pd.DataFrame:
        """The frozen planned-order ledger, normalised for the engine."""
        orders = pd.read_parquet(self._run_dir / "orders.parquet")
        if orders.empty:
            return orders
        orders["signal_date"] = orders["signal_date"].map(_as_date)
        orders["execution_date"] = orders["execution_date"].map(_as_date)
        return orders.sort_values(
            ["execution_date", "order_id"]
        ).reset_index(drop=True)

    @staticmethod
    def _order_schedule(plan: pd.DataFrame) -> tuple[OrderDay, ...]:
        """Group the plan ledger into engine ``OrderDay`` rows (pure intent).

        Every plan order keeps its frozen ``order_id``; no execution-day
        information is consulted (the plan is already sized at signal close).
        """
        by_day: dict[date, list[dict]] = {}
        for record in plan.to_dict("records"):
            by_day.setdefault(record["execution_date"], []).append(record)
        order_days: list[OrderDay] = []
        for execution_day in sorted(by_day):
            rows = by_day[execution_day]
            ordered = sorted(rows, key=lambda record: record["order_id"])
            sells = tuple(
                Order(
                    order_id=str(record["order_id"]),
                    side=SELL,
                    symbol=str(record["symbol"]),
                    quantity=int(record["quantity"]),
                )
                for record in ordered
                if record["side"] == SELL
            )
            buys = tuple(
                Order(
                    order_id=str(record["order_id"]),
                    side=BUY,
                    symbol=str(record["symbol"]),
                    quantity=int(record["quantity"]),
                )
                for record in ordered
                if record["side"] == BUY
            )
            order_days.append(
                OrderDay(trade_date=execution_day, sells=sells, buys=buys)
            )
        return tuple(order_days)
```

把 `_load_market` 签名（约 1332）类型从 `schedule: Sequence[TargetDay]` 改为 `schedule: Sequence[OrderDay]`（函数体不变，只读 `order_day.trade_date`）。

- [ ] **Step 4: 重写 `_produce_backtest`：固定 schedule 重放 + order_diffs 落盘**

把 `_produce_backtest` 方法（约 1213-1297）整体替换为：

```python
    def _produce_backtest(
        self, state: RunState, frozen: ExperimentSpec
    ) -> dict[str, str]:
        """Replay the frozen plan ledger through each scenario's live account."""
        # The corporate-action gate runs first, before the plan ledger is read
        # or the market/engine is built, so a RESEARCH run with untrusted
        # evidence never spends time on a backtest it will not keep.
        self._enforce_research_trust(state, frozen)
        plan = self._read_order_ledger()
        schedule = self._order_schedule(plan)
        market = self._load_market(frozen, schedule)
        outputs: dict[str, str] = {}
        scenario_names = list(frozen.cost_scenarios)
        for scenario in scenario_names:
            directory = self._run_dir / "backtest" / scenario
            directory.mkdir(parents=True, exist_ok=True)
            request = BacktestRequest(
                dataset_version=frozen.dataset_version,
                initial_cash=self._project_config.initial_cash,
                calendar=market.calendar,
                rule_book=self._rule_book,
                cost_model=CostModel.from_config(
                    self._project_config.costs, scenario
                ),
                bars=market.bars,
                corporate_actions=market.corporate_actions,
                schedule=schedule,
                benchmark_symbols=tuple(self._project_config.benchmark_symbols),
                benchmarks=market.benchmarks,
            )
            result = BacktestEngine().run(request)
            result.fills.to_parquet(directory / "fills.parquet", index=False)
            result.rejections.to_parquet(
                directory / "rejections.parquet", index=False
            )
            result.action_ledger.to_parquet(
                directory / "action_ledger.parquet", index=False
            )
            result.daily_equity.to_parquet(
                directory / "daily_equity.parquet", index=False
            )
            result.submitted_orders.to_parquet(
                directory / "submitted_orders.parquet", index=False
            )
            diffs = reconcile_orders(
                plan=plan,
                submitted=result.submitted_orders,
                fills=result.fills,
                rejections=result.rejections,
            )
            diffs.to_parquet(directory / "order_diffs.parquet", index=False)
            for relative in (
                "fills.parquet",
                "rejections.parquet",
                "action_ledger.parquet",
                "daily_equity.parquet",
                "submitted_orders.parquet",
                "order_diffs.parquet",
            ):
                outputs[f"backtest/{scenario}/{relative}"] = _sha256_file(
                    directory / relative
                )
        canonical = self._canonical_scenario(scenario_names)
        scenario_dir = self._run_dir / "backtest" / canonical
        canonical_mapping = {
            "fills.parquet": scenario_dir / "fills.parquet",
            "daily_equity.parquet": scenario_dir / "daily_equity.parquet",
            "corporate_action_ledger.parquet":
                scenario_dir / "action_ledger.parquet",
        }
        for artifact_name, source in canonical_mapping.items():
            destination = self._run_dir / artifact_name
            shutil.copyfile(source, destination)
            outputs[artifact_name] = _sha256_file(destination)
        cash_ledger = self._derive_cash_ledger(
            pd.read_parquet(scenario_dir / "fills.parquet"),
            pd.read_parquet(scenario_dir / "action_ledger.parquet"),
            float(self._project_config.initial_cash),
            market.calendar,
            scenario_dir / "daily_equity.parquet",
        )
        cash_ledger.to_parquet(self._run_dir / "cash_ledger.parquet", index=False)
        outputs["cash_ledger.parquet"] = _sha256_file(
            self._run_dir / "cash_ledger.parquet"
        )
        return outputs
```

- [ ] **Step 5: 替换 runner `_DefaultAnalytics`：以 order_diffs 计计划账本层指标**

把 `_DefaultAnalytics`（196-267 行整类含 docstring）替换为：

```python
class _DefaultAnalytics:
    """Per-scenario summary over the completed backtest ledgers.

    Each scenario records how the frozen plan diverged from what actually
    executed.  ``order_diffs.parquet`` is the per-``order_id`` plan-ledger view
    (counts and quantities), while ``rejections.parquet`` keeps the flow-level
    view (one row per rejection record).  ``plan_diverged`` is true exactly
    when ``unfilled_quantity > 0``, so a run whose plan was not fully executed
    is self-auditing from ``metrics.json`` alone.
    """

    def compute(self, metrics_input: AnalyticsInput) -> dict[str, object]:
        scenarios: dict[str, object] = {}
        for scenario in metrics_input.scenarios:
            directory = metrics_input.run_dir / "backtest" / scenario
            equity = pd.read_parquet(directory / "daily_equity.parquet")
            fills = pd.read_parquet(directory / "fills.parquet")
            rejections = pd.read_parquet(directory / "rejections.parquet")
            diffs = pd.read_parquet(directory / "order_diffs.parquet")
            start = float(equity["total_equity"].iloc[0])
            end = float(equity["total_equity"].iloc[-1])
            n_rejections = int(len(rejections))
            planned_order_count = int(len(diffs))
            planned_quantity = (
                int(diffs["planned_quantity"].sum()) if planned_order_count else 0
            )
            filled_quantity = (
                int(diffs["filled_quantity"].sum()) if planned_order_count else 0
            )
            unfilled_quantity = planned_quantity - filled_quantity
            status_counts = (
                diffs["status"].value_counts().to_dict() if planned_order_count else {}
            )
            filled_order_count = int(status_counts.get(STATUS_FILLED, 0))
            partial_order_count = int(status_counts.get(STATUS_PARTIAL, 0))
            rejected_order_count = int(status_counts.get(STATUS_REJECTED, 0))
            unfilled = (
                diffs[diffs["status"] != STATUS_FILLED] if planned_order_count else diffs
            )
            unfilled_reason_counts = {
                str(reason): int(count)
                for reason, count in (
                    unfilled["reason"].value_counts().items()
                    if planned_order_count and len(unfilled)
                    else []
                )
            }
            scenarios[scenario] = {
                "periods": int(len(equity)),
                "start_date": _date_text(equity["trade_date"].iloc[0]),
                "end_date": _date_text(equity["trade_date"].iloc[-1]),
                "start_equity": round(start, 2),
                "end_equity": round(end, 2),
                "total_return": round(end / start - 1.0, 8) if start > 0 else None,
                "end_cash": round(float(equity["cash"].iloc[-1]), 2),
                "n_fills": int(len(fills)),
                "commission": _round2(float(fills["commission"].sum()))
                if len(fills)
                else 0.0,
                "stamp_tax": _round2(float(fills["stamp_tax"].sum()))
                if len(fills)
                else 0.0,
                # Flow level (rejections.parquet, one row per rejection record).
                "n_rejections": n_rejections,
                "rejected_quantity": int(rejections["rejected_quantity"].sum())
                if n_rejections
                else 0,
                "rejections_by_reason": {
                    str(reason): int(count)
                    for reason, count in (
                        rejections["reason"].value_counts().items()
                        if n_rejections
                        else []
                    )
                },
                # Order level (order_diffs.parquet, one row per planned order).
                "planned_order_count": planned_order_count,
                "planned_quantity": planned_quantity,
                "filled_quantity": filled_quantity,
                "unfilled_quantity": unfilled_quantity,
                "filled_order_count": filled_order_count,
                "partial_order_count": partial_order_count,
                "rejected_order_count": rejected_order_count,
                "unfilled_reason_counts": unfilled_reason_counts,
                "plan_diverged": bool(unfilled_quantity > 0),
            }
        return {"scenarios": scenarios}
```

- [ ] **Step 6: 镜像到 cli `_ExperimentAnalytics`**

把 `cli.py` `_ExperimentAnalytics.compute` 中读取与 `summary` 组装替换为与 Step 5 完全一致的逻辑（读 `order_diffs.parquet` 替代 `rebalance_adjustments.parquet`，去掉 pretrade 三键，加计划账本层键），并保留 `"performance": compute_metrics(equity, fills, benchmark).to_dict(),`。两处 `summary` dict 的键必须与 Step 5 逐键一致（spec §7：cli 追加 `performance`）。`cli.py` 模块顶部加 reconcile import——**只引 `STATUS_FILLED, STATUS_PARTIAL, STATUS_REJECTED`**（cli 不调用 `reconcile_orders`，引它会 F401；`reconcile_orders` 仅在 runner 的 `_produce_backtest` 使用）。

即：把现读 `rebalance_adjustments.parquet` 的语句删除，改为 `diffs = pd.read_parquet(directory / "order_diffs.parquet")`；删除 `n_pretrade_adjustments` 局部；把 pretrade 三键块替换为 Step 5 中从 `"planned_order_count"` 到 `"plan_diverged"` 的完整键块（保留 `"performance"` 在最后）。上面的行号（如 compute 561-627、读 573-575、pretrade 键块 607-621）是**当前 cli.py 原始文件**的定位，且本步要先加模块顶部 import——行号会因 +3 漂移，一律**以文本/符号定位**，勿按行号盲删。删除 import 区不再使用的名字（若 `adjustments`/`rebalance_adjustments` 后无引用，ruff 会提示未使用变量——以 `ruff check` 结果为准清理）。

- [ ] **Step 7: 更新 `tests/integration/test_research_runner.py`**

把 `test_published_metrics_record_unreachable_orders`（493-521 行）整体替换为（纯意图下，跌停锁住的周度减仓 SELL 在执行日整单拒单 `sell_at_lower_limit`，不再有 pretrade 削减；并断言 spec §7 的指标对账恒等）：

```python
def test_published_metrics_record_execution_rejected_orders(tmp_path):
    # 601318.SH stays ranked in the weekly top-10 (its signal-day closes are
    # untouched) but its Monday execution-day bar opens below the lower price
    # limit, so every weekly trim-sell of it is submitted as planned and then
    # rejected by the executor -- the pure-intent replay keeps the plan intact
    # and records the sell-at-lower-limit rejection in the ledgers.
    symbol = "601318.SH"
    project_root = tmp_path / "project"
    _publish_synthetic_dataset(project_root, limit_locked_symbols=(symbol,))
    runner = ResearchRunner(project_root, config_root=_REPO_ROOT)
    experiment = runner.run(_SPEC)
    assert experiment.manifest.status in ("ACCEPTED", "REJECTED")
    metrics = json.loads(
        (experiment.path / "metrics.json").read_text(encoding="utf-8")
    )
    scenarios = metrics["scenarios"]
    assert isinstance(scenarios, dict) and scenarios
    for summary in scenarios.values():
        # The pre-trade concepts are gone; the divergence is a rejection.
        assert "n_pretrade_adjustments" not in summary
        assert "pretrade_adjustments_by_reason" not in summary
        assert int(summary["n_rejections"]) > 0
        assert REASON_SELL_AT_LOWER_LIMIT in summary["rejections_by_reason"]
        assert REASON_SELL_AT_LOWER_LIMIT in summary["unfilled_reason_counts"]
        # Spec section 7 reconciliation invariants.
        planned_order_count = int(summary["planned_order_count"])
        assert planned_order_count > 0
        assert (
            int(summary["filled_order_count"])
            + int(summary["partial_order_count"])
            + int(summary["rejected_order_count"])
        ) == planned_order_count
        assert (
            int(summary["filled_quantity"]) + int(summary["unfilled_quantity"])
        ) == int(summary["planned_quantity"])
        assert int(summary["unfilled_quantity"]) == int(
            summary["rejected_quantity"]
        )
        assert int(summary["n_rejections"]) == (
            int(summary["partial_order_count"])
            + int(summary["rejected_order_count"])
        )
        assert sum(
            int(count) for count in summary["unfilled_reason_counts"].values()
        ) == (
            int(summary["partial_order_count"])
            + int(summary["rejected_order_count"])
        )
        assert summary["plan_diverged"] is (int(summary["unfilled_quantity"]) > 0)
```

- [ ] **Step 8: 更新 `tests/integration/test_end_to_end.py`**

(1) 确保文件顶部 `import pandas as pd`（缺则补）。
(2) 93-99 行 scenario 产物断言块，把两旧文件改为 order_diffs、并断言旧文件消失：

```python
    run_id = metrics["meta"]["run_id"]
    plan = pd.read_parquet(outcome.path / "orders.parquet")
    submitted_frames: list[pd.DataFrame] = []
    for scenario in scenarios:
        scenario_dir = (
            Path(fixture_root.root) / "data" / "runs" / run_id / "backtest" / scenario
        )
        submitted = pd.read_parquet(scenario_dir / "submitted_orders.parquet")
        submitted_frames.append(submitted)
        assert (scenario_dir / "order_diffs.parquet").is_file()
        assert not (scenario_dir / "rebalance_adjustments.parquet").exists()
        assert not (scenario_dir / "executable_targets.parquet").exists()
        # The submitted set is exactly the frozen plan ledger.
        sub = submitted[["order_id", "side", "symbol", "quantity"]].sort_values(
            "order_id"
        ).reset_index(drop=True)
        planned = plan[["order_id", "side", "symbol", "quantity"]].sort_values(
            "order_id"
        ).reset_index(drop=True)
        assert sub.equals(planned)
    # Submitted orders are scenario-independent (spec section 6).
    for frame in submitted_frames[1:]:
        assert submitted_frames[0].equals(frame)
```

(3) 85-91 行 scenario summary 循环（`for summary in scenarios.values()`）内追加新键断言：

```python
        assert "n_pretrade_adjustments" not in summary
        assert "planned_order_count" in summary
        assert "filled_order_count" in summary
        assert "unfilled_reason_counts" in summary
```

- [ ] **Step 9: 运行验证**

Run: `ruff check src/stock_quant/backtest/engine.py src/stock_quant/research/runner.py src/stock_quant/cli.py tests/integration/test_backtest_engine.py tests/integration/test_research_runner.py tests/integration/test_end_to_end.py && pytest tests/unit/test_reconcile.py tests/integration/test_backtest_engine.py tests/integration/test_research_runner.py tests/integration/test_end_to_end.py -q`

Expected: 全 PASS——含新增的 `test_golden_submitted_orders_match_the_plan_schedule` 与既有投影测试 `test_target_day_...`（engine 本任务只做加法、投影路径原样）。如 suite 有其它引用 pretrade/旧产物的失败，以 `grep -rn "n_pretrade\|rebalance_adjustments\|executable_targets\|pretrade_adjustments" tests/` 定位修正到新语义。

- [ ] **Step 10: 提交**

```bash
git add src/stock_quant/backtest/engine.py src/stock_quant/research/runner.py src/stock_quant/cli.py tests/integration/test_backtest_engine.py tests/integration/test_research_runner.py tests/integration/test_end_to_end.py
git commit -m "feat: replay frozen planned-order ledger through the fixed schedule"
```

---

### Task 3: 删除执行日投影（引擎、`rebalance.py`、投影测试替换 + P0.3 验收）

**Files:**
- Modify: `src/stock_quant/backtest/engine.py`
- Modify: `tests/integration/test_backtest_engine.py`
- Delete: `src/stock_quant/backtest/rebalance.py`
- Delete: `tests/unit/test_rebalance.py`

**Interfaces:**
- Consumes: Task 1 `reconcile`（engine 不直接用）；Task 2 后 runner 不再 import `TargetDay`/`target_schedule`。
- Produces: `BacktestRequest.schedule` 唯一订单输入；`BacktestResult` 只含 `fills/rejections/action_ledger/daily_equity/submitted_orders`；固定 schedule 路径收集 submitted（每订单一提交记录）；`rebalance.py` 整文件删除。

- [ ] **Step 1: 写测试（先替换投影测试、补纯意图 + P0.3 验收）**

在 `tests/integration/test_backtest_engine.py`：

(1) Task 2 已把 engine import 追加为 `SUBMITTED_ORDER_COLUMNS`；本步再删 `TargetDay`（本任务删除该符号后测试不得再引用它）。最终为：

```python
from stock_quant.backtest.engine import (
    BacktestEngine,
    BacktestRequest,
    OrderDay,
    ReadinessError,
    SUBMITTED_ORDER_COLUMNS,
)
```

(2) 把投影测试 `test_target_day_projects_cash_and_static_market_constraints_before_execution`（以函数名为准定位）整体删除，替换为两个新测试（§3 的 golden submitted 引擎测试已在 Task 2 落地，此处不重复）。这两个测试锁定纯意图重放的语义：引擎把原始计划逐笔提交、把现金不足/超卖/停牌/涨停交执行器裁决为部分成交与拒单，且改执行日开盘价只改该单结局。

```python
def test_pure_intent_plan_replay_records_fills_partials_and_rejections():
    """The engine submits the raw plan (no pre-sizing) and the executor turns
    affordability / price-limit / suspension into fills and rejections."""
    request = BacktestRequest(
        dataset_version=_DATASET_VERSION,
        initial_cash=1500,
        calendar=TradingCalendar.from_open_days(_MARKET.days),
        rule_book=TradingRuleBook.from_yaml(
            _REPO_ROOT / "configs" / "trading_rules.yml"
        ),
        cost_model=CostModel(_cost_rate(_SCENARIOS["full_cost"])),
        bars=_MARKET.bars,
        corporate_actions=_MARKET.corporate_actions,
        benchmarks=_MARKET.benchmarks,
        schedule=(
            OrderDay(
                trade_date=_DAYS[1],
                sells=(Order("o102", SELL, ALPHA, 200),),
                buys=(Order("o101", BUY, ALPHA, 200),),
            ),
            OrderDay(
                trade_date=_DAYS[22],
                buys=(Order("o104", BUY, GAMMA, 100),),
            ),
            OrderDay(
                trade_date=_DAYS[40],
                buys=(Order("o103", BUY, ETA, 100),),
            ),
        ),
    )

    result = BacktestEngine().run(request)

    # The whole raw plan was submitted: nothing was trimmed or re-sized by
    # execution-day information.
    submitted = sorted(
        (row.order_id, row.side, row.symbol, int(row.quantity))
        for row in result.submitted_orders.itertuples()
    )
    assert submitted == sorted(
        [
            ("o101", BUY, ALPHA, 200),
            ("o102", SELL, ALPHA, 200),
            ("o103", BUY, ETA, 100),
            ("o104", BUY, GAMMA, 100),
        ]
    )
    # o101 is cash-partial (one whole lot fits), everything else rejected.
    assert [(row.order_id, int(row.quantity)) for row in result.fills.itertuples()] == [
        ("o101", 100)
    ]
    assert {
        (row.order_id, row.reason, int(row.rejected_quantity))
        for row in result.rejections.itertuples()
    } == {
        ("o101", "insufficient_cash", 100),
        ("o102", "insufficient_sellable_quantity", 200),
        ("o103", REASON_BUY_AT_UPPER_LIMIT, 100),
        ("o104", "suspended_or_unknown", 100),
    }


def test_changing_only_an_execution_day_open_changes_only_that_orders_outcome():
    """P0.3 acceptance: perturb one execution-day open (ETA day 40 to below its
    upper limit) and only that order's fill/rejection outcome changes; the
    submitted order set -- the frozen plan -- is byte-identical."""
    variant = _read_fixture("bars.parquet").copy()
    mask = (variant["symbol"] == ETA) & (variant["trade_date"] == _DAYS[40])
    variant.loc[mask, "open"] = 10.9  # under the 11.0 upper limit
    base = BacktestEngine().run(_request_with(schedule=_MARKET.schedule))
    changed = BacktestEngine().run(
        _request_with(bars=variant, schedule=_MARKET.schedule)
    )
    assert_frame_equal(base.submitted_orders, changed.submitted_orders)
    # Everything except order o11 is identical across both runs.
    assert_frame_equal(
        base.fills[base.fills.order_id != "o11"].reset_index(drop=True),
        changed.fills[changed.fills.order_id != "o11"].reset_index(drop=True),
    )
    assert_frame_equal(
        base.rejections[base.rejections.order_id != "o11"].reset_index(drop=True),
        changed.rejections[changed.rejections.order_id != "o11"].reset_index(
            drop=True
        ),
    )
    # o11: upper-limit rejection in the base, a fill in the changed run.
    assert list(base.rejections[base.rejections.order_id == "o11"].reason) == [
        REASON_BUY_AT_UPPER_LIMIT
    ]
    assert changed.rejections[changed.rejections.order_id == "o11"].empty
    assert list(changed.fills[changed.fills.order_id == "o11"].quantity) == [100]
```

- [ ] **Step 2: 运行确认两测试通过（作为删除前语义锁）**

Run: `pytest tests/integration/test_backtest_engine.py -k 'pure_intent or changing_only' -v`
Expected: PASS——两测试走 Task 2 已落地的 schedule 提交收集，纯 schedule 路径不依赖 target 代码，删除前即绿；若红则说明 Task 2 引擎加法未正确落地，先回查。真正检验删除的是一步删除后 Step 5 的整文件套件（含 golden 与其它 schedule 测试）。

- [ ] **Step 3: 引擎删除投影路径（按符号定位，勿依赖 Task 2 前行号）**

Task 2 已在 `run()` 的 schedule `else:` 分支逐笔收集 submitted。本步把 target 投影的所有代码与数据成员删净，使 `run()` 只剩固定 schedule 一条路径。`src/stock_quant/backtest/engine.py` 逐项删除——**删除一律以符号 / 唯一文本定位**（Task 2 已在该文件新增约 16 行，此前行号已漂移）：

1. 删除 import：`from stock_quant.backtest.rebalance import (RebalanceAdjustment, project_rebalance)`。
2. 删除常量 `REBALANCE_ADJUSTMENT_COLUMNS` 与 `EXECUTABLE_TARGET_COLUMNS`。
3. 删除 dataclass `TargetDay`。
4. `BacktestRequest`：删除 `target_schedule` 字段及其 `__post_init__` 中与 `schedule` 互斥的 XOR guard（`__post_init__` 若无剩余校验则整体删除）。
5. `BacktestResult`：删除 `rebalance_adjustments`、`executable_targets` 两个字段。
6. `run()`：删除 day 循环内 target 相关的一切——`target = market.target_on(day)` 行、target 分支（`_projection_frame` → `project_rebalance` → `_collect_projection` 调用链与 target 结果列表局部）、schedule `else:` 分支——把原 target 分支 + schedule 分支 + 紧随的共享 `if orders:` 执行块合并为固定 schedule 唯一路径（`_apply_actions`、`run()` 顶部列表初始化、返回 `BacktestResult(...)` 等其余保持原样）：

```python
            sells, buys = market.schedule_by(day)
            orders = list(sells) + list(buys)
            if orders:
                # One submitted record per plan order, before execute (spec
                # section 2): the audit trail is the frozen plan itself.
                self._collect_submitted(day, orders, submitted_orders)
                frame = self._execution_frame(
                    day, orders, market, last_close
                )
                result = simulator.execute(orders, frame, account, day)
                self._collect(result, day, fills, rejections)
```

   `run()` 返回的 `BacktestResult(...)` 只保留五个既有字段，其中 submitted 用 `self._submitted_orders_frame(submitted_orders)`。
7. `_collect_submitted` 已由 Task 2 新增（位于 `_collect` 旁）——本步**不重复添加**、原样保留；其上一条代码块里 `if orders:` 块首的调用即唯一调用点。
8. 删除方法：`_projection_frame`、`_collect_projection`、`_rebalance_adjustments_frame`、`_executable_targets_frame`。
9. `_Market.__init__`：删除 `target_by_day` 的索引两行；`possible_held_symbols` 只留 schedule 推导；删除 `target_on` 方法与模块级 `_target_schedule_index` 函数：

```python
    @property
    def possible_held_symbols(self) -> set[str]:
        """Symbols any schedule order touches (the potentially-held set)."""
        return {
            order.symbol
            for orders in self.schedule_by_day.values()
            for order in orders
        }
```

删除后快速自查：`grep -n "target_schedule\|TargetDay\|project_rebalance\|_projection_frame\|_collect_projection\|rebalance_adjustments\|executable_targets\|target_on\|_target_schedule_index" src/stock_quant/backtest/engine.py` 应无命中（`rebalance` 字样的注释残留亦清除）。

- [ ] **Step 4: 删除 `rebalance.py` 及其单测**

```bash
git rm src/stock_quant/backtest/rebalance.py tests/unit/test_rebalance.py
```

删除后 `grep -rn "project_rebalance\|RebalanceAdjustment\|rebalance" src/ tests/ --include=*.py` 只应命中与本特性无关的字符串（`reconcile`/文件名无关）。runner 已无任何 import。

- [ ] **Step 5: 运行验证**

Run: `ruff check src/stock_quant/backtest/engine.py tests/integration/test_backtest_engine.py && pytest tests/integration/test_backtest_engine.py -q`
Expected: 全 PASS（含替换后的三个新测试；`test_synthetic_market_...`、golden、readiness 等原有引擎测试全部保留且通过）。

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "refactor: drop execution-day projection; replay frozen plans only"
```

---

### Task 4: rich report "执行偏离诊断" 列与模板改造（新 execution_summary schema）

**Files:**
- Modify: `src/stock_quant/reporting/html.py`（`_execution_rows`）
- Modify: `src/stock_quant/reporting/templates/experiment.html.j2`
- Modify: `tests/integration/test_reports.py`

**Interfaces:**
- Consumes: `execution_diagnostics.json` 的**新 schema**（spec §8，本地 `project/execution_diagnostics.py` 合并后跟进；rich report 只按键渲染，未跟进前列降级"无"）。本任务只改渲染层与 fabricate 测试输入，不要求 src 写出该 json。
- Produces: 渲染层以订单级 `unfilled_reason_counts` + `planned/filled/partial/rejected_order_count` 呈现，删除"调仓前约束原因 / 执行拒绝原因"两列。

- [ ] **Step 1: 写失败测试（更新渲染测试输入与断言）**

在 `tests/integration/test_reports.py` 替换 `test_experiment_html_renders_execution_divergence_summary`（428-464 行）的 `execution_summary` dict 与断言：

```python
def test_experiment_html_renders_execution_divergence_summary(tmp_path):
    report = _experiment_input()
    scenario = report.scenarios[0]
    report = ExperimentReportInput(
        **{
            **report.__dict__,
            "scenarios": (
                ExperimentScenario(
                    **{
                        **scenario.__dict__,
                        "execution_summary": {
                            "planned_order_count": 20,
                            "filled_order_count": 15,
                            "partial_order_count": 2,
                            "rejected_order_count": 3,
                            "planned_gross_notional": 100000.0,
                            "actual_gross_notional": 85000.0,
                            "unfilled_notional": 15000.0,
                            "execution_deviation_ratio": 0.15,
                            "unfilled_reason_counts": {
                                "insufficient_cash": 2,
                                "suspended_or_unknown": 3,
                            },
                            "end_cash": 12000.0,
                            "cash_ratio": 0.3,
                            "stale_asset_ratio": 0.02,
                        },
                    }
                ),
            ),
        }
    )

    html = render_experiment_report(report, tmp_path / "report.html").read_text(
        encoding="utf-8"
    )

    assert "执行偏离诊断" in html
    assert "未成交原因" in html
    assert "部分成交数" in html
    assert "拒单数" in html
    assert "15.00%" in html
    assert "insufficient_cash: 2" in html
    assert "suspended_or_unknown: 3" in html
    assert "调仓前约束" not in html
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/integration/test_reports.py -k execution_divergence -v`
Expected: FAIL（新列/新键未渲染或旧列仍出现）。

- [ ] **Step 3: 重写 `_execution_rows`**

`src/stock_quant/reporting/html.py` `_execution_rows`（607-630）整体替换：

```python
def _execution_rows(summary: dict[str, object] | None) -> list[list[str]]:
    """Format the persisted execution-drift summary for one cost scenario.

    ``summary`` is the execution_diagnostics.json object written under the Goal
    #3 schema (spec section 8): order-level counts plus notional priced at the
    signal-day close, with unfilled reasons counted by reason.  Missing keys
    degrade gracefully to "无"/blank until the local writer is updated.
    """
    if not summary:
        return []
    reasons = summary.get("unfilled_reason_counts", {})
    reason_text = "、".join(
        f"{reason}: {count}" for reason, count in sorted(dict(reasons).items())
    ) or "无"
    return [
        [
            _int_text(summary.get("planned_order_count")),
            _int_text(summary.get("filled_order_count")),
            _int_text(summary.get("partial_order_count")),
            _int_text(summary.get("rejected_order_count")),
            _money(summary.get("planned_gross_notional")),
            _money(summary.get("actual_gross_notional")),
            _money(summary.get("unfilled_notional")),
            _pct(summary.get("execution_deviation_ratio")),
            reason_text,
            _money(summary.get("end_cash")),
            _pct(summary.get("stale_asset_ratio")),
        ]
    ]
```

- [ ] **Step 4: 更新模板表头与空态 colspan**

`src/stock_quant/reporting/templates/experiment.html.j2` 121 行 `<thead>` 替换为 11 列表头；126 行 `colspan="8"` 改 `colspan="11"`、文案改"无执行偏离诊断数据"：

```html
      <thead><tr><th>计划订单数</th><th>成交订单数</th><th>部分成交数</th><th>拒单数</th><th>计划成交额（元）</th><th>实际成交额（元）</th><th>未成交额（元）</th><th>执行偏离率</th><th>未成交原因</th><th>期末现金（元）</th><th>停牌资产比例</th></tr></thead>
```

```html
          <tr><td class="empty" colspan="11">无执行偏离诊断数据</td></tr>
```

- [ ] **Step 5: 运行验证**

Run: `ruff check src/stock_quant/reporting/html.py tests/integration/test_reports.py && pytest tests/integration/test_reports.py -q`
Expected: PASS。另跑 `pytest tests/integration/test_end_to_end.py -q` 确认渲染链路未回归（该文件不渲染 rich report，应保持通过）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/reporting/html.py src/stock_quant/reporting/templates/experiment.html.j2 tests/integration/test_reports.py
git commit -m "feat: rich report renders order-level execution divergence"
```

---

## 收尾（本计划任务之外）

全部任务完成后运行完整验证：`pytest -q && ruff check src tests`（期望全绿；`ruff format --check` 不做门禁）。合并后由你在主 checkout 更新本地 `project/execution_diagnostics.py`（未跟踪、worktree 不含）按 spec §8 新 schema 写 `execution_diagnostics.json`/`.parquet`；跟进前 rich report 相关列降级为"无"。spec §8 明确该本地脚本不在 git、非本分支职责。
