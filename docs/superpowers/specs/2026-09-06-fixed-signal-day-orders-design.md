# 固定信号日订单设计（移除执行日信息驱动的重算）

## 目标

把研究运行改为"信号日收盘后固定订单方向与数量，执行日只按当日开盘行情模拟成交 / 部分成交 / 拒单"，并将计划、提交、成交与拒绝订单以稳定 ID 关联、逐笔报告差异与原因。本设计**取代** `2026-09-05-executable-rebalance-design.md` 的执行日可执行性投影核心（P0.3 为其逆命题）。

## 根因

当前系统存在两层订单：`orders.parquet`（信号日净调仓计划，scenario-independent，但从未被回测消费）与执行日投影层——`BacktestRequest.target_schedule` 使引擎在每个执行日调用 `project_rebalance(...)`，用**当日**开盘价 / 停牌 / 涨跌停 / 真实账户，把理想目标重新缩减为可提交订单（`submitted_orders` / `rebalance_adjustments` / `executable_targets`）。该投影正是 P0.3 禁止的"据执行日信息预先删单、改量、重优化"。

`ExecutionSimulator` 已完整具备执行日应有的行为：成交、现金不足部分成交、超卖拒单、停牌缺行拒单、涨跌停锁板拒单，且 `Fill`/`RejectedOrder` 携带计划 `order_id`。因此执行日**不需要任何预计算**——把冻结计划直接交给模拟器即可。

## 核心不变量

信号日输出（计划订单账本 `orders.parquet`）只是信号日可见输入的纯函数：理想目标、上一期目标簿 `previous_book`、信号日收盘价、交易日历。执行日开盘价 / 停牌 / 可交易状态**永不回算订单**，只经 `ExecutionSimulator` 决定逐笔 `成交 / 部分成交 / 拒单` 并记入成交/拒绝账本。

**验收（P0.3）**：改动任一执行日的开盘价或可交易状态，不改变信号日生成的订单；执行结果仅在成交/拒绝账本中变化。

## 术语

| 术语（中文） | 英文标识 | 含义 | 产物 |
|---|---|---|---|
| 计划订单 / 计划订单账本 | planned order / planned-order ledger | 信号日收盘后冻结的净调仓订单（方向 + 数量 + 稳定 ID），scenario-independent、不可变 | `orders.parquet` |
| 提交订单 / 执行订单 | submitted order | 执行日实际交给模拟器的订单；纯意图下与计划**一一对应** | `submitted_orders.parquet` |
| 成交 | filled order | 模拟器成交明细 | `fills.parquet` |
| 拒单 / 部分成交 | rejected order / partial fill | 执行日市场 / 账户现实 | `rejections.parquet` |
| 订单级差异对账 | order diff | 计划↔提交↔成交↔拒单按 `order_id` 对账的逐笔差异表 | `order_diffs.parquet` |

命名规则：产物与列名统一 `planned_*`（计划）、`submitted`、`filled_*`、`rejected_*`、`unfilled_*`；**删除** `executable_target`（可执行目标）与 `pre-trade adjustment`（调仓前约束）两个 09-05 概念；报告中不再出现"可执行数量 / 调仓前约束"标签。

## 架构

信号日（portfolio 阶段）产出冻结计划账本 → 引擎以固定 `schedule`（`OrderDay` 序列）重放、把计划逐笔交给 `ExecutionSimulator` → 执行结果落成交/拒单账本 → 按稳定 ID 对账出 `order_diffs` → metrics / report / rich report 呈现计划-执行差异。成本情景共用**同一份**计划，差异只体现在成交/拒单（费率→现金可买量→部分成交）。

### 1. 计划订单账本（orders.parquet 转正）

`_produce_portfolio` 现有的净调仓计算即纯意图账本，逻辑不变，仅每行补一列 `signal_date`（订单级信号归属）。列：`signal_date, execution_date, order_id, side, symbol, quantity`。它从"仅写入 + 哈希"变为"回测消费的权威输入"，仍为独立不可变产物（`REQUIRED_ARTIFACTS` 保留，不改名）。

### 2. 引擎（backtest/engine.py, models.py）

- `BacktestRequest` 删除 `target_schedule`；`TargetDay` 删除；`BacktestResult` 删除 `rebalance_adjustments`、`executable_targets` 及对应列常量、`_rebalance_adjustments_frame`、`_executable_targets_frame`。
- `run()` 去掉 target 分支（`market.target_on` / `project_rebalance` / `_projection_frame` / `_collect_projection`），统一走固定 `schedule`：`sells, buys = market.schedule_by(day); orders = sells + buys`。
- **submitted_orders 收集粒度**：引擎在每个开盘日把订单列表交给 `simulator.execute` **之前**，对列表内每一笔 Order 追加一条 submitted 记录（列 `SUBMITTED_ORDER_COLUMNS = (trade_date, order_id, side, symbol, quantity)`）。粒度 = **一笔提交一条**（submit 事件），非一天一行聚合。`order_id` 全窗口全局唯一、每单只在自身 `execution_date` 被执行一次，故**天然无重复，不依赖运行时去重**。
- `_Market` 去掉 `target_by_day` / `target_on`；`possible_held_symbols` 仅由 schedule 得出。
- 就绪校验 `_check_order_days` **不改**：计划订单执行日缺行 → `suspended_or_unknown` 可审计拒单；ERROR 质量栏 / 无可用开盘 / 无前收 → run 级 veto（数据完整性护栏，非执行日市场事件）。
- `ExecutionSimulator` **完全不动**。
- `rebalance.py` 的执行日投影函数（`project_rebalance` 等）删除（引擎为唯一消费方）。

### 3. 稳定 ID 链与对账不变量

稳定 ID 链：`orders.parquet.order_id` → `submitted_orders.order_id` → `fills.order_id` / `rejections.order_id`，由引擎提交计划原订单、模拟器原样保留 order_id 保证。以不变量断言固化（**断言顺序固定为：先 ID、后字段**）：

1. `submitted_orders.order_id` 无重复，且其集合 == `orders.parquet.order_id` 集合（ID 是主键，作为连接键）。
2. 对**每个** `order_id`，`submitted_orders` 的 `(side, symbol, quantity)` 与计划账本一致（三元组只是字段级确认，不是主键）。
3. `fills.order_id` ∪ `rejections.order_id` ⊆ 计划 `order_id` 集合；窗口内每笔计划订单至少产生一条 fill 或 rejection 记录（引擎窗口 == 计划执行日跨度）。

### 4. 订单级差异对账 order_diffs.parquet（新产物）

`reconcile` 纯函数按 `order_id` 将计划左连（fills 求和 `filled_quantity`）+（rejections 的 `rejected_quantity` / `reason`）→ 每情景一个 `order_diffs.parquet`。

列与 dtype：

| 列 | dtype | 说明 |
|---|---|---|
| `signal_date` | date | 信号日（计划账本注入） |
| `execution_date` | date | 执行日（== 计划 execution_date == submitted.trade_date） |
| `order_id` | str | 稳定 ID，贯穿 计划→提交→成交/拒单 |
| `side` | str（BUY/SELL） | |
| `symbol` | str | |
| `planned_quantity` | int | 计划量（== submitted.quantity） |
| `filled_quantity` | int | fills 按 order_id 求和；未成交为 0 |
| `rejected_quantity` | int | rejections 按 order_id 的 rejected_quantity；全额成交为 0 |
| `reason` | str | 见 §5；FILLED 为 `""` |
| `status` | str | `FILLED`（filled == planned）/ `REJECTED`（filled == 0 且拒单全额）/ `PARTIAL`（0 < filled < planned） |

对账恒等：`filled_quantity + rejected_quantity == planned_quantity`（逐行）。

### 5. reason 枚举（值原文透传，不重映射）

执行层 reason 为既有常量，字面值照抄。运行时域（计划订单经纯意图重放可到达的逐单结果）+ 文档域：

| 值 | 常量（文件） | 订单级可达性 |
|---|---|---|
| `""` | —（全额成交 FILLED） | ✅ 常态 |
| `insufficient_cash` | `REASON_INSUFFICIENT_CASH`（models:36） | ✅ REJECTED 或 PARTIAL |
| `insufficient_sellable_quantity` | `REASON_INSUFFICIENT_SELLABLE_QUANTITY`（models:37） | ✅ REJECTED |
| `suspended_or_unknown` | `REASON_SUSPENDED_OR_UNKNOWN`（models:34） | ✅ REJECTED |
| `uncovered_rule` | `REASON_UNCOVERED_RULE`（models:38） | ✅ REJECTED |
| `buy_at_upper_limit` | `REASON_BUY_AT_UPPER_LIMIT`（trading_rules:27） | ✅ REJECTED |
| `sell_at_lower_limit` | `REASON_SELL_AT_LOWER_LIMIT`（trading_rules:28） | ✅ REJECTED |
| `missing_open` | `REASON_MISSING_OPEN`（models:32） | ⚠️ 就绪 veto，run 级终止 |
| `missing_pre_close` | `REASON_MISSING_PRE_CLOSE`（models:33） | ⚠️ 就绪 veto |
| `quality_error` | `REASON_QUALITY_ERROR`（models:35） | ⚠️ 就绪 veto |

`order_diffs.reason`：FILLED 为 `""`；REJECTED / PARTIAL 取该订单 rejections 记录的 reason 原文。reconcile 对 rejections 流水里的 reason **原文透传**；遇枚举外字符串即违约（防御未来新增原因）。

**空串规则**：`unfilled_reason_counts` 不含 `""` 键（只统计未足额成交订单的原因）；拒单流水 `rejections.parquet` 的 `reason` 必有值（执行层既有保证），`rejections_by_reason` 出现 `""` 属违约。此规则为文档契约 + reconcile 内防御即可，**不新增冗余测试**（不重复既有执行层对 reason 非空的测试）。

### 6. Runner（research/runner.py）

- `_read_target_schedule()` → `_read_order_schedule()`：读 `orders.parquet` 按 `execution_date` 分组构造 `OrderDay(trade_date, sells, buys)`（保留 `order_id`、`note`），所有成本情景共用同一 `schedule`。
- `_produce_backtest`：每情景写 `fills / rejections / action_ledger / daily_equity / submitted_orders / order_diffs`；**不再写** `rebalance_adjustments.parquet`、`executable_targets.parquet`。
- `reconcile` 函数与 `order_diffs` 写入；`models.py` REQUIRED_ARTIFACTS / manifest 工件清单同步替换上述两文件名为 `order_diffs.parquet`（含哈希与每情景产物集合）。

### 7. metrics.json / report / CLI 全链改造

`runner._DefaultAnalytics`（运行时写 metrics.json）与 `cli._ExperimentAnalytics`（report_build 重建）**两处**以相同新键替换 pretrade 三键。metrics.json `scenarios[<name>]` 完整嵌套结构（类型化）：

```jsonc
{
  "periods": "int",
  "start_date": "str ISO date", "end_date": "str ISO date",
  "start_equity": "float", "end_equity": "float",
  "total_return": "float|null", "end_cash": "float",
  "n_fills": "int", "commission": "float", "stamp_tax": "float",
  "n_rejections": "int", "rejected_quantity": "int",          // 拒单流水层（rejections.parquet 逐行）
  "rejections_by_reason": {"<reason str>": "int"},            // 不含 ""；见 §5
  "planned_order_count": "int", "planned_quantity": "int",    // 计划账本层
  "filled_quantity": "int",
  "unfilled_quantity": "int",                                  // == planned_quantity - filled_quantity（恒等）
  "filled_order_count": "int", "partial_order_count": "int",
  "rejected_order_count": "int",
  "unfilled_reason_counts": {"<reason str>": "int"},           // 订单级：REJECTED / PARTIAL 的原因分布；不含 ""
  "plan_diverged": "bool",                                     // unfilled_quantity > 0
  "performance": { /* cli._ExperimentAnalytics 追加：PerformanceMetrics.to_dict() 现有嵌套，原样保留 */ }
}
```

对账不变式（metrics/report 层测试断言）：`filled_order_count + partial_order_count + rejected_order_count == planned_order_count`；`filled_quantity + unfilled_quantity == planned_quantity`；`unfilled_quantity == rejected_quantity`（每单恰执行一次，订单级与流水级一致）；`sum(unfilled_reason_counts) == partial_order_count + rejected_order_count`。`rejections_by_reason`（流水层，语义不变）与 `unfilled_reason_counts`（订单层）数值重合，文档注明视角差异。

report.html（runner `_DefaultReport`）与 rich report（`reporting/html.py` `_execution_rows` + `templates/experiment.html.j2`）按新键渲染；rich report"执行偏离诊断"表把 `调仓前约束原因` + `执行拒绝原因` 合并为订单级 `未成交原因`（读 `unfilled_reason_counts`），并加 partial/rejected 计数列；保留"summary 缺失 → 空降级"。

### 8. execution_diagnostics.json（本地 project 后续跟进，非提交）

`project/` 为未跟踪的本地分析工作区（`execution_diagnostics.py` 不在 git；worktree 不含 `project/`）。分支自洽；合并后由你在主 checkout 本地更新该脚本，按**本 spec 定义的新 schema** 写 `execution_diagnostics.json` / `.parquet`（rich report 据此渲染；未跟进前相关列降级为"无"）。每情景扁平对象（类型化）：

```jsonc
{
  "scenario": "str",
  "planned_gross_notional": "float",   // 按 signal_price（信号日收盘价）计价：sum(planned_quantity * signal_price)
  "actual_gross_notional": "float",    // 按成交价计价：sum(filled_quantity * fill price)
  "unfilled_notional": "float",        // sum((planned_quantity - filled_quantity) * signal_price)
  "execution_deviation_ratio": "float",// unfilled / planned_gross（=0 保护）
  "planned_order_count": "int", "filled_order_count": "int",
  "partial_order_count": "int", "rejected_order_count": "int",
  "unfilled_reason_counts": {"<reason str>": "int"},   // 不含 ""
  "end_cash": "float", "cash_ratio": "float", "stale_asset_ratio": "float"
}
```

**计价价格约定（全 spec 统一）**：所有 `planned_*` / `unfilled_notional` 一律锚定 `signal_price`（信号日收盘价）——固定信号日设计下计划订单的价值在信号日确定；`actual_*` 一律用执行日成交价。

### 9. 删除清单（09-05 执行日投影）

`TargetDay` / `BacktestRequest.target_schedule` / 引擎 target 分支 / `project_rebalance` / `_projection_frame` / `_collect_projection` / `rebalance_adjustments` / `executable_targets`（含列常量、frame 构造、每情景产物、metrics pretrade 键、rich report"调仓前约束"列）全部删除；09-05 中"同一理想目标在不同成本情景可产生不同可执行买入量"的验收**废除**。

## 测试与验收

- 引擎重放单元测试：现金不足→部分成交 + 余量拒单；卖超可卖→拒单；停牌缺行→拒单；涨停买→拒单、跌停卖→拒单；并断言引擎**无**执行日重算路径。
- §3 稳定 ID 链与对账不变量测试（断言顺序固定：order_id 集合相等 → 逐 ID 断言字段一致）。避免冗余：`""` 不出现在拒单流水由执行层既有保证覆盖，不重复测试。
- reconcile 单测：status 派生、reason 透传、逐行恒等、枚举外字符串违约。
- 集成（test_end_to_end 等）：每情景产物含 `order_diffs`、不含两旧文件；`submitted_orders` 跨情景一致且等于计划；metrics/report 新键齐全、pretrade 键消失；rich report 新列渲染。
- **P0.3 验收测试**：同一输入构造两份 run，仅改某执行日某标的开盘价 / 停牌 / 涨跌停 ⇒ `signals/targets/orders.parquet` 与 `submitted_orders` 哈希一致，`fills/rejections` 只在触及该标的的订单上不同。
- 质量约束沿用仓库基线：`ruff check` 干净；套件全绿；不 gate 全仓 `ruff format`。

## 有意的行为后果

1. 若某情景因前期买盘部分成交/拒单（如涨停周）或公司行为改份额导致**实际持仓 < 计划卖出量**，该 SELL 被整单拒单（`insufficient_sellable_quantity`），**不**在执行日截断到可卖量（截断 = 执行日改量，P0.3 禁止）；残余持仓留待下期信号，记入 `order_diffs` / `plan_diverged`。
2. 计划内订单若撞 ERROR 质量栏 / 无开盘 / 无前收，run 由就绪校验整体 veto（数据完整性护栏，维持现状）。
3. 成本情景差异**只**体现在成交/拒单结果；提交订单集合严格一致。
