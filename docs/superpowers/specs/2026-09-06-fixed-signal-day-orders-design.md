# 固定信号日订单设计（移除执行日信息驱动的重算）

> **更新（2026-09-08，账户对账式调仓）**：本设计经评审后修订为**账户对账式周调仓**（方案 A）。冻结信号日目标书（`target_positions.parquet`）仍是唯一订单意图源，但**顶层计划订单账本 `orders.parquet` 退役**：每个成本情景由引擎在执行日以**自身已实现持仓/现金**对照同一张**情景无关**的目标书逐日生成订单（`BacktestRequest.order_provider` + `AccountAwareWeeklyRebalancer`）。无未来函数不变：订单 = f(冻结信号日目标, 执行日开盘前已实现账户态)，执行日行情只经 `ExecutionSimulator` 决定成交/部分成交/拒单。
>
> **准确目标**：消除由理想化 previous_book / stale residual **虚构持仓差额**导致的伪成本非单调与虚假未成交。**不是**"任何路径下成本越高期末权益必越低"——成本路径差异致少买暴涨股时权益可能更高，属真实合理结果（I4）。
>
> ### 设计不变量（本节为规范正文，源码实现以此为准）
>
> - **U0 符号全集**：某调仓日遍历 `symbols = sorted({lot.symbol for lot in account.lots} | set(targets))`，缺失目标 `targets.get(sym, 0)` 显式视为 0。**离场股不在当期 target 但真实持仓非零 → 仍产出卖单**；只遍历 targets 会把原 bug 原样保留。
> - **M0 Provider metadata completeness**：对任何调仓日 d，provider 实际可提交的每个 symbol 在 d 日都具备执行所需的 market/readiness 元数据。可提交符号集**不能用 `target_d − target_{d−1}` 推导**——stale residual retry 与目标变化无关（W2 卖 A 被拒、W3 目标仍 0 → W3 仍须能再卖 A）。故 provider 模式 readiness 归 **executor 提交时逐单**（`_static_reason`：缺行 → 可审计 `suspended_or_unknown` 拒单；权威数据恒在场），可能持仓为**窗口级超集** `possible_held_symbols`（= 各期目标符号并集；曾持有 ⇒ 曾 target>0 ⇒ ∈ 并集，因 CA 仅同 ticker），供 CA 覆盖/trust 消费，不需逐日。
> - **I1a 无陈旧计划残差（无条件，本次真正修复的不变量）**：每期交易意图仅由 `frozen_target − 执行日开盘前真实账户持仓` 决定，不依赖任何历史理想目标书。此前未成交的差额只要持仓仍与之不符，就重新进入本期 deterministic 计算（W2 卖 A 失败 → W3 目标 0 仍再卖 A）。
> - **I1 情景间持仓收敛（条件式）**：仅当各情景在相关调仓日均有能力完整执行相同目标差额（现金全程充足、无不可交易/lot/CA 残余、有弥补漏买所需后续调仓机会）时收敛到同一 executable target；否则允许真实现金约束造成持仓不同。
> - **I2 目标收敛（条件式，per-scenario）**：在"持仓可整手（无 CA 奇数残余）、可执行、现金足以完成末次补足、有后续调仓机会"的符号上 `final holdings == executable(final target)`；CA 奇数残余（target=0、持仓=150 → 卖 100 留 50，整手卖约束禁 <100 卖）与现金不足/末周未执行不适用。CA-free、现金全程充足的 fixture 上退化为无条件 `== 末次 target`。
> - **I3 生成正确性（契约）**：`submitted_d == deterministic_rebalance(frozen_target_d, 执行日开盘前账户态)`。`plan := submitted` 后 reconciliation 只证明 submitted→execution 记账完备性（filled+rejected==submitted、reason 守规、唯一性、missing_open/quality_error breach 守卫），**不再证明生成正确性**。独立性来自：①重平衡器纯单元测试手写 expected order（oracle）；②引擎测试 wrapper 在 provider 调用瞬间抓 `account.state()`，断言 engine submitted == provider 返回（保真，非同一实现重算自己）。
> - **I4 权益单调 = 业务验收、非普遍定理**：严格单调仅在上界 fixture（无 CA、卖单不挡、每情景现金足以完整执行每期目标差额 ⇒ 成交量价一致、仅费差）成立并作回归断言；真实 run 若仍 fc>ct 或 fc>zero，先诊断剩余持仓 P&L（用 I1 排除）/现金约束/末期末收敛，再判断是否真实成本路径效应，不直接判 rebalancer 错。

## 目标

把研究运行改为"信号日收盘后固定订单方向与数量，执行日只按当日开盘行情模拟成交 / 部分成交 / 拒单"，并将计划、提交、成交与拒绝订单以稳定 ID 关联、逐笔报告差异与原因。本设计**取代** `2026-09-05-executable-rebalance-design.md` 的执行日可执行性投影核心（P0.3 为其逆命题）。

## 根因

当前系统存在两层订单：`orders.parquet`（信号日净调仓计划，scenario-independent，但从未被回测消费）与执行日投影层——`BacktestRequest.target_schedule` 使引擎在每个执行日调用 `project_rebalance(...)`，用**当日**开盘价 / 停牌 / 涨跌停 / 真实账户，把理想目标重新缩减为可提交订单（`submitted_orders` / `rebalance_adjustments` / `executable_targets`）。该投影正是 P0.3 禁止的"据执行日信息预先删单、改量、重优化"。

`ExecutionSimulator` 已完整具备执行日应有的行为：成交、现金不足部分成交、超卖拒单、停牌缺行拒单、涨跌停锁板拒单，且 `Fill`/`RejectedOrder` 携带计划 `order_id`。因此执行日**不需要任何预计算**——把冻结计划直接交给模拟器即可。

## 核心不变量

信号日输出只是信号日可见输入的纯函数：冻结目标书 `target_positions.parquet`（理想目标、信号日收盘价、交易日历）。执行日开盘价 / 停牌 / 可交易状态**永不回算订单**，只经 `ExecutionSimulator` 决定逐笔 `成交 / 部分成交 / 拒单` 并记入成交/拒绝账本。

**意图源（账户对账式，2026-09-08 起）**：订单意图 = `target_positions`（情景无关）+ 每情景 submitted（引擎 provider 模式以该情景已实现账户态逐日生成）。顶层 `orders.parquet` 不再存在；`submitted = f(冻结目标, 已实现账户)`，见 I1a/I3。

**验收（P0.3，重定位至 schedule 模式）**：改动任一执行日的开盘价或可交易状态，不改变**冻结计划**（schedule 模式的 `submitted_orders`，黄金测试保持）；执行结果仅在成交/拒绝账本中变化。Provider 模式下已实现成交本就反馈到下期订单（I1a 账户对账的本意），故 P0.3 的"订单对执行日行情完全不变"仅对 schedule 模式成立；provider 模式的对应不变式是：**订单生成只读冻结目标与账户态，从不读执行日行情**（重平衡器无价格输入）。

## 术语

| 术语（中文） | 英文标识 | 含义 | 产物 |
|---|---|---|---|
| ~~计划订单 / 计划订单账本~~ | ~~planned order / planned-order ledger~~ | **已退役（2026-09-08）**：顶层信号日净调仓订单账本被账户对账式调仓取代，意图源改为 `target_positions.parquet` | ~~`orders.parquet`~~ |
| 提交订单 / 执行订单 | submitted order | 执行日实际交给模拟器的订单；**每情景由 provider 以自身账户态生成，跨情景不再相等（方案 A 本意）** | `submitted_orders.parquet` |
| 成交 | filled order | 模拟器成交明细 | `fills.parquet` |
| 拒单 / 部分成交 | rejected order / partial fill | 执行日市场 / 账户现实 | `rejections.parquet` |
| 订单级差异对账 | order diff | 计划↔提交↔成交↔拒单按 `order_id` 对账的逐笔差异表 | `order_diffs.parquet` |

命名规则：产物与列名统一 `planned_*`（计划）、`submitted`、`filled_*`、`rejected_*`、`unfilled_*`；**删除** `executable_target`（可执行目标）与 `pre-trade adjustment`（调仓前约束）两个 09-05 概念；报告中不再出现"可执行数量 / 调仓前约束"标签。

## 架构

信号日（portfolio 阶段）产出冻结目标书 → 回测阶段每情景各建一个 `AccountAwareWeeklyRebalancer`，引擎（provider 模式）逐日以该情景账户态生成订单、把订单逐笔交给 `ExecutionSimulator` → 执行结果落成交/拒单账本 → `plan := submitted` 按稳定 ID 对账出 `order_diffs` → metrics / report / rich report 呈现提交-执行差异。成本情景共用**同一张目标书**，但提交订单**情景相关**（成本→现金→可买股数被真正实现）；差异体现在提交、成交与拒单三层。schedule 模式（引擎黄金测试、`BacktestRequest.schedule`）原样保留，两种模式以 `order_provider is None` 判别。

### 1. 冻结目标书（orders.parquet 已退役；意图源 = target_positions + 每情景 submitted）

`_produce_portfolio` 只产出 `signals.parquet` 与 `target_positions.parquet`（冻结目标书），**不再生成任何订单**。订单由回测阶段每情景的 `AccountAwareWeeklyRebalancer` 以"冻结目标 − 已实现持仓"逐日生成（I1a），生成规则见文首不变量 U0 与数量规则（买不预钳现金、卖 T+1 钳制 + 整手向下取整）。`REQUIRED_ARTIFACTS` 已移除 `orders.parquet`。

### 2. 引擎（backtest/engine.py, models.py）

- **（2026-09-08 增）provider seam**：`BacktestRequest` 增可选 `order_provider: Callable[[date, Account], Sequence[Order]]` 与 `possible_held_symbols: frozenset[str] | None`；模式判据 = `order_provider is None`（schedule 模式，`schedule=()` 合法）vs `is not None`（provider 模式逐日 `orders = order_provider(day, account)`，汇入同一提交+执行尾）。provider 模式 `possible_held_symbols=None` 是配置错误（ValueError），见 M0。
- **（2026-09-08 增）metadata 口径见 M0**：`_Market.possible_held_symbols` 按模式判据分支——schedule 模式保持 schedule 并集（含 `schedule=()` → 空并集）；provider 模式取 `request.possible_held_symbols`（窗口级超集）。CA 覆盖/trust 消费者不变；provider 模式无静态逐日预检，readiness 由 executor `_static_reason` 提交时逐单裁决。
- `BacktestRequest` 删除 `target_schedule`；`TargetDay` 删除；`BacktestResult` 删除 `rebalance_adjustments`、`executable_targets` 及对应列常量、`_rebalance_adjustments_frame`、`_executable_targets_frame`。
- `run()` 去掉 target 分支（`market.target_on` / `project_rebalance` / `_projection_frame` / `_collect_projection`），统一走固定 `schedule`：`sells, buys = market.schedule_by(day); orders = sells + buys`。
- **submitted_orders 收集粒度**：引擎在每个开盘日把订单列表交给 `simulator.execute` **之前**，对列表内每一笔 Order 追加一条 submitted 记录（列 `SUBMITTED_ORDER_COLUMNS = (trade_date, order_id, side, symbol, quantity)`）。粒度 = **一笔提交一条**（submit 事件），非一天一行聚合。`order_id` 全窗口全局唯一、每单只在自身 `execution_date` 被执行一次，故**天然无重复，不依赖运行时去重**。
- `_Market` 去掉 `target_by_day` / `target_on`；`possible_held_symbols` 仅由 schedule 得出。
- 就绪校验 `_check_order_days` **不改**：计划订单执行日缺行 → `suspended_or_unknown` 可审计拒单；ERROR 质量栏 / 无可用开盘 / 无前收 → run 级 veto（数据完整性护栏，非执行日市场事件）。
- `ExecutionSimulator` **完全不动**。
- `rebalance.py` 的执行日投影函数（`project_rebalance` 等）删除（引擎为唯一消费方）。

### 3. 稳定 ID 链与对账不变量

稳定 ID 链（账户对账式）：provider 生成的 `order_id` → `submitted_orders.order_id` → `fills.order_id` / `rejections.order_id`，由引擎提交原订单、模拟器原样保留 order_id 保证。schedule 模式下计划账本仍是 ID 链起点；provider 模式下 `plan := submitted`，身份断言（下 1、2）经此构造恒真，**仅证明记账完备性**——生成正确性由 I3 承担（重平衡器 oracle 单测 + 引擎保真测试）。断言顺序固定为：先 ID、后字段：

1. `submitted_orders.order_id` 无重复，且其集合 == plan（schedule 模式 = `orders.parquet`；provider 模式 = submitted 自身）集合（ID 是主键，作为连接键）。
2. 对**每个** `order_id`，`submitted_orders` 的 `(side, symbol, quantity)` 与 plan 一致（三元组只是字段级确认，不是主键）。
3. `fills.order_id` ∪ `rejections.order_id` ⊆ plan `order_id` 集合；窗口内每笔提交订单至少产生一条 fill 或 rejection 记录（引擎窗口 == 计划执行日跨度）。

### 4. 订单级差异对账 order_diffs.parquet（新产物）

`reconcile` 纯函数按 `order_id` 将 plan（schedule 模式 = 冻结计划账本；provider 模式 = 该情景 submitted，见 I3）左连（fills 求和 `filled_quantity`）+（rejections 的 `rejected_quantity` / `reason`）→ 每情景一个 `order_diffs.parquet`。

列与 dtype：

| 列 | dtype | 说明 |
|---|---|---|
| `signal_date` | date | 信号日（schedule 模式由计划账本注入；provider 模式由冻结目标书注入） |
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

- **（2026-09-08 改）**`_produce_portfolio` 只写 `signals.parquet` + `target_positions.parquet`（理想账本净额段与 `orders.parquet` 删除）；`_produce_backtest` 每情景独立 `AccountAwareWeeklyRebalancer`，`BacktestRequest(order_provider=…, possible_held_symbols=各期目标符号并集, schedule=())` 逐日按该情景账户态出单；`_read_order_ledger` 退役，内存 plan = submitted（I3）。`_order_schedule` 与 schedule 模式路径原样保留（引擎黄金测试）。
- 每情景写 `fills / rejections / action_ledger / daily_equity / submitted_orders / order_diffs`；**不再写** `rebalance_adjustments.parquet`、`executable_targets.parquet`。
- `reconcile` 函数与 `order_diffs` 写入；`models.py` REQUIRED_ARTIFACTS / manifest 工件清单同步：删除 `orders.parquet`。

### 7. metrics.json / report / CLI 全链改造

（2026-09-08 口径更新）`planned_*` / `plan_diverged` 等订单级键的口径 = **该情景实际提交**（provider 模式 submitted；不再是跨情景同一张计划账本）。`runner._DefaultAnalytics`（运行时写 metrics.json）与 `cli._ExperimentAnalytics`（report_build 重建）**两处**以相同新键替换 pretrade 三键。metrics.json `scenarios[<name>]` 完整嵌套结构（类型化）：

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
- **（2026-09-08 增）rebalancer 纯单元测试**：手写 expected order（oracle），覆盖 U0 离场股卖单、CA 奇数残余整手截断、T+1 钳制 + 下期自愈、stale-retry 跨周补单、每符号单边、SELL 前置/符号升序、买量整手且不预钳现金。
- **（2026-09-08 增）引擎 provider-mode 测试**：部分成交/整卖被拒后下期补足/卖出恰为残差（I1a）；stale-retry 引擎级（W2 拦单 → W3 再提交且当日元数据完整，M0）；先卖后买融资；I3 保真（wrapper 抓调用瞬间 `account.state()`，断言 submitted == provider 返回）；模式判据两用例（`schedule=()` 无 provider 照常跑；provider 缺 `possible_held_symbols` → ValueError）。schedule 模式黄金账本与 P0.3 测试不改、必须仍绿。
- §3 稳定 ID 链与对账不变量测试（断言顺序固定：order_id 集合相等 → 逐 ID 断言字段一致）。避免冗余：`""` 不出现在拒单流水由执行层既有保证覆盖，不重复测试。
- reconcile 单测：status 派生、reason 透传、逐行恒等、枚举外字符串违约。
- 集成（test_end_to_end 等）：每情景产物含 `order_diffs`、不含两旧文件与 `orders.parquet`；order_diffs 记账完备（filled+rejected==submitted）；**I1a/I1/I2/I4 验收**（上界 fixture 上期末持仓互等且 == 末次 target、期末权益 zero ≥ ct ≥ fc）；metrics/report 新键齐全、pretrade 键消失；rich report 新列渲染。
- **P0.3 验收测试（重定位至 schedule 模式）**：同一输入构造两份 run，仅改某执行日某标的开盘价 / 停牌 / 涨跌停 ⇒ schedule 模式 `submitted_orders` 与 `signals/targets` 哈希一致，`fills/rejections` 只在触及该标的的订单上不同。（provider 模式的对应不变式：订单生成只读冻结目标与账户态，从不读执行日行情；已实现成交反馈下期订单属 I1a 本意。）
- 质量约束沿用仓库基线：`ruff check` 干净；套件全绿；不 gate 全仓 `ruff format`。

## 有意的行为后果

1. 若某情景因前期买盘部分成交/拒单（如涨停周）或公司行为改份额导致**实际持仓 < 目标差额**，该 SELL/BUY 按账户可实现量级提交并被拒（如 `insufficient_sellable_quantity`）/部分成交，**不**在执行日截断到可交易量（截断 = 执行日改量，P0.3 精神禁止）；**残余差额在下期调仓日由账户对账自愈补足**（I1a：只要持仓仍与冻结目标不符，就重新进入下期 deterministic 计算），并记入 `order_diffs` / `plan_diverged`。CA 奇数残余（<100 股）除外——整手卖约束无法处置，留 stale（I2 排除）。
2. 计划内订单若撞 ERROR 质量栏 / 无开盘 / 无前收，run 由就绪校验整体 veto（数据完整性护栏，维持现状；provider 模式缺行则由 executor 逐单记 `suspended_or_unknown` 可审计拒单，见 M0）。
3. ~~成本情景差异只体现在成交/拒单结果；提交订单集合严格一致。~~ **已退役（2026-09-08）**：提交订单集跨情景不再相等是账户对账式调仓（方案 A）的本意——成本→现金→可买股数被真正实现（§522）；成本情景持仓/权益差异若为真实路径效应，是预期而非缺陷（I4）。
