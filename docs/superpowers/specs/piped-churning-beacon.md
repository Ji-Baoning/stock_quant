# 账户对账式调仓：消除理想账本 / stale residual 造成的伪成本非单调与虚假未成交

## Context
用户报告两缺陷：(1) report 中 full_cost 期末权益(119,368)> commission_tax(115,142)，疑似违反成本单调；(2) ~33% 订单未成交，疑似交易前审核失效。

**根因**（已定位，非执行层）：`_produce_portfolio` 用**理想化 previous_book**（=上期目标）对本周目标书净额出单，`orders.parquet` **从不观察已实现成交** → 部分买入/整卖被拒后实际持仓<理想、计划却按理想继续；离场股目标归 0 后不再产生卖单 → **永久 stale residual**，其 P&L 随情景不同 → 伪成本非单调；虚假"超理想"卖单主导巨量未成交。

**决策（用户已确认）**：账户对账式周调仓（方案 A）+ **删除顶层 orders.parquet**。三成本情景各以**自身已实现持仓/现金**对照同一张**情景无关的目标书**出单；提交订单**情景相关**（成本→现金→可买股数，§522 被真正实现）。无未来函数不变：订单=f(冻结信号日目标, 执行日开盘前已实现账户态)。

**准确目标**：消除由理想 previous_book / stale residual **虚构持仓差额**导致的伪成本非单调与虚假未成交。**不是**"任何路径下成本越高期末权益必越低"——成本路径差异致少买暴涨股时权益可能更高，属真实合理结果（I4）。

**两处引擎 seam**（review 确定，非假 Order 冒充元数据）：
- `BacktestRequest` 增可选 `order_provider`：provider 模式逐日按账户态出单执行。
- provider 模式**不再用 schedule/假 OrderDay 提供元数据**——schedule 仅保留给字节级不变的 schedule 模式；CA 覆盖与可能持仓改由独立 `possible_held_symbols`（窗口级安全超集）+ **executor 提交时逐单 readiness** 承担（M0）。执行器 `ExecutionSimulator.execute` 本就**无条件先卖后买、与输入顺序无关**（execution.py:161-169）；缺行/坏 bar 由 `_static_reason` 逐单裁决（:204-238），权威数据恒 INFO。

## 设计不变量（review 三轮确定——U0/M0/I1a/I1–I4 必进 spec）
- **U0 符号全集**：某调仓日遍历 `symbols = sorted({lot.symbol for lot in account.lots} | set(targets))`，缺失目标 `targets.get(sym, 0)` 显式视为 0。**离场股不在当期 target 但真实持仓非零 → 仍产出卖单**；只遍历 targets 会把原 bug 原样保留。
- **M0 Provider metadata completeness**：对任何调仓日 d，provider 实际可提交的每个 symbol 在 d 日都具备执行所需的 market/readiness 元数据。可提交符号集**不能用 `target_d − target_{d−1}` 推导**——stale residual retry 与目标变化无关（W2 卖 A 被拒、W3 目标仍 0 → W3 仍须能再卖 A，见回归测试）。故 provider 模式 readiness 归 **executor 提交时逐单**（`_static_reason`：缺行→可审计 suspended_or_unknown 拒单；权威数据恒在场），可能持仓为**窗口级超集**（runner 由各期目标并集推导，含所有曾持有故可残留的名称，供 CA 覆盖/trust，不需逐日）。
- **I1a 无陈旧计划残差（无条件，本次真正修复的不变量）**：每期交易意图仅由 `frozen_target − 执行日开盘前真实账户持仓` 决定，**不依赖任何历史理想目标书**。此前未成交的差额只要持仓仍与之不符，就重新进入本期 deterministic 计算（W2 卖 A 失败 → W3 目标 0 仍再卖 A）。
- **I1 情景间持仓收敛（条件式）**：仅当各情景在相关调仓日均**有能力完整执行相同目标差额**（现金全程充足、无不可交易/lot/CA 残余、有弥补漏买所需后续调仓机会）时收敛到同一 executable target；否则允许真实现金约束造成持仓不同（1000 vs 900）。
- **I2 目标收敛（条件式，per-scenario）**：在"持仓可整手（无 CA 奇数残余）、可执行、现金足以完成末次补足、有后续调仓机会"的符号上 `final holdings == executable(final target)`；CA 奇数残余（target=0、持仓=150 → 卖 `floor(150//100)*100=100` 留 50，整手卖约束 require_lot_quantity 禁 <100 卖）与现金不足/末周未执行不适用。CA-free、现金全程充足的 fixture 上退化为无条件 `== 末次 target`。
- **I3 生成正确性（契约）**：`submitted_d == deterministic_rebalance(frozen_target_d, 执行日开盘前账户态)`。`plan := submitted` 后 reconciliation 只证明 submitted→execution 记账完备性，**不再证明生成正确性**。独立性来自：①重平衡器纯单元测试手写 expected order（oracle）；②引擎测试 wrapper 在 provider 调用瞬间抓 `account.state()`，断言 engine submitted == provider 返回（保真，**非同一实现重算自己**）。不靠事后 fills 反推开盘前状态。
- **I4 权益单调 = 业务验收、非普遍定理**：严格单调仅在上界 fixture（无 CA、卖单不挡、每情景现金足以完整执行每期目标差额 ⇒ 成交量价一致、仅费差）成立并作回归断言；真实 run 若仍 fc>ct 或 fc>zero，先诊断**剩余持仓 P&L（用 I1 排除）/现金约束/末期末收敛**，再判断是否真实成本路径效应，不直接判 rebalancer 错。

## 改动（按序，源码）

### 阶段 1 — 引擎 seam（order_provider + possible_held）+ 重平衡器
1. `src/stock_quant/backtest/models.py`：`BacktestRequest`(:140-157) 增两个尾字段
   `order_provider: OrderProvider | None = None`、`possible_held_symbols: frozenset[str] | None = None`（schedule 模式沿用旧推导，忽略新字段，全量现有构造零改动）。别名避免运行时解析循环：`OrderProvider = Callable[[date, "Account"], Sequence[Order]]`——RHS 引号前向引用（typing 构造时转 ForwardRef，不 import Account；`Sequence`/`Order`/`date` models.py 已具），`TYPE_CHECKING` 引 Account 仅供类型检查器。勿写裸 `Account` + 仅 TYPE_CHECKING。
   **模式判据 = `order_provider is None`（旧 schedule 模式）vs `is not None`（provider 模式）——绝不用 schedule 空否判模式**（旧 schedule 模式合法存在 `schedule=()`，语义必须逐字不变）。`__post_init__` 加：`order_provider is not None and possible_held_symbols is None → ValueError`（provider 模式把 None 当**配置错误**硬拒，不静默退化——该字段承担 CA coverage/market-universe contract，静默接受会让 M0 变成"调用方记得传就成立"）。
2. `src/stock_quant/backtest/engine.py` `run()`(:198-213)：`if request.order_provider is not None:` 每窗口日 `orders = list(request.order_provider(day, account))` 汇入同一提交+执行尾（executor 内部先卖后买，execution.py:161-169，扁平列表安全）；else 现路径**逐字节不变**。`_Market.possible_held_symbols`(:578-585) 按**模式判据**分支（不是 schedule 空否）：
   - `order_provider is None` → **原 schedule union 逻辑逐字不动**（含 `schedule=()` → 空 union，与旧行为完全一致）；
   - `order_provider is not None` → `request.possible_held_symbols`（非 None，已由 __post_init__ 保证）。
   CA 覆盖/trust 消费者不变，窗口仍由 bars 推导(:571)。**provider 模式无静态逐日预检**——readiness 由 executor `_static_reason` 提交时逐单裁决（缺行→suspended_or_unknown 可审计拒单，:210-212；权威数据恒在场，无假否决面）。
3. **新文件** `src/stock_quant/backtest/rebalancer.py`：`AccountAwareWeeklyRebalancer(map[execution_date, (signal_date, targets)])`，`orders_for(day, account)`：
   - 非调仓日返回 `()`；持 `_seq`；符号排序保确定性；每符号只发单边。
   - **U0**：`symbols = sorted({lot.symbol for lot in account.lots} | set(targets))`；`held = account.position_quantity(sym)`（含 T+1 锁定，account.py:148）；`target = targets.get(sym, 0)`。
     `held > target` → 卖 `floor(min(held−target, account.sellable_quantity(sym, day)) // 100) * 100`（T+1 钳制 account.py:140；CA 奇数残余超额留 stale，见 I2）；
     `held < target` → 买 `floor((target−held) // 100) * 100`。
   - **返回序 = SELL 全部在前、BUY 全部在后，side 内 symbol 升序**（`*sells_sorted, *buys_sorted`）：side 内输入序即 executor 保留的资金优先级（execution.py:161-169），确定性使台账与 buy 分单优先级显式可复现。**买量不预钳现金**——目标差额非整手时仅提交向下取整后的整手差额，余量由执行器部分成交自愈。

### 阶段 2 — runner 产出端（理想账本退役，无假脊柱）
4. `src/stock_quant/research/runner.py` `_produce_portfolio`(:1115-1231)：删净额段（previous_book 初值 :1136、卖 :1161-1178、买 :1180-1197、orders.parquet 写 :1226、哈希 :1230）。保留 signals/target_positions 写与哈希，返回仅两者。
5. 由窗口内各期 target_positions 推导 **`possible_held_symbols = 各期目标符号并集`**（M0 安全超集：含曾持有故可 stale-retry 的名称；不含当期目标也已入场）。**不构建任何 OrderDay/假订单脊柱**——provider 模式的日期由重平衡器的 execution_date 键驱动、逐日 readiness 由 executor 承担。

### 阶段 3 — runner 回测脊（每情景订单 + 对账）
6. `runner.py` `_produce_backtest`(:1242-1327)：每情景独立 `AccountAwareWeeklyRebalancer`，`BacktestRequest(order_provider=…, possible_held_symbols=全局并集, schedule=())` → 引擎逐日按该情景账户态出单；submitted/fills/rejections/equity 写同现(:1283-1297)。schedule 模式路径与 `_order_schedule`(:1345-1382) 原样保留（黄金测试）。
7. 每情景对账：不读 orders.parquet（`_read_order_ledger` :1334-1343 退役）。内存 plan = submitted + execution_date + signal_date。`reconcile_orders`(reconcile.py:72) **签名/行为零改**；身份断言经此构造恒真——仅证明记账完备性（filled+rejected==planned、reason 守规、唯一性、missing_open/quality_error breach 守卫），生成正确性由 I3 承担。canonical 拷贝清单(:1304-1326)去 orders.parquet；`_derive_cash_ledger`(:1441-1499)沿用。
8. `src/stock_quant/research/models.py` `REQUIRED_ARTIFACTS`(:193-211) 删 `"orders.parquet"`(:203)。

### 阶段 4 — 规范 + 本地工具
9. `docs/superpowers/specs/2026-09-06-fixed-signal-day-orders-design.md`：写入 U0/M0/I1a/I1–I4（§170 退役；§168 残余语义改"下期自愈补足"；§1 意图源改 target_positions + 每情景 submitted；P0.3 重定位；§7 metrics 口径=该情景实际提交；reconciliation 职责边界见 I3；metadata 口径见 M0）。Context 措辞对齐"消除伪成本非单调、非普遍单调保证"。
10. `project/execution_diagnostics.py`(:188-197)：不再读 run/orders.parquet；改自各情景 order_diffs 符号并集建 tape，守卫查 order_diffs 列。（仅本地活树，不入库。）

## 测试
- **新 tests/unit/test_rebalancer.py**（纯单元，Account+calendar 手工构造 lots，**expected order 手写 oracle**）：
  - **离场股回归（最核心）**：上周 A=1000、本周 target 空、实际 A=700 → `SELL A 700`；上周 A=150 且 target 0（CA 奇数）→ 卖 100 留 50。
  - **stale-retry（M0/I1a 回归）**：W2 目标 A=0、卖 A 被拒仍持 700 → **W3 目标仍 0** 时再次产出 `SELL A 700`（跨周补单，非仅一次）。
  - T+1 钳制（新买 lot 当日不可卖→下周自愈）；重入/再平衡补足；每符号单边；**返回序 SELL 全前于 BUY、side 内 symbol 升序**；买量：**目标差额非整手时仅提交向下取整后的整手差额，不按现金预钳数量**。
- **test_backtest_engine.py provider-mode**（80 天 fixture，复用 `_reference_run` 范式；fixture 满足 I1 前提：CA-free、卖单不挡、每情景现金足以完整执行每期目标差额，使 I1/I2 确定性成立）：
  - 部分成交/整卖被拒后下期补足/卖出恰为残差（I1a）；**stale-retry 引擎级**：W2 卖 A 失败（构造拦单）→ W3 再提交 SELL A 且该日 bar 元数据完整覆盖（M0 成立，executor 正常裁决）；期末持仓==末次目标（I2 无条件式）。
  - **先卖后买融资**：构造现金不足单买目标但同日卖出足够 → BUY 无 avoidable `insufficient_cash`（经 provider 模式验证 executor 语义）。
  - **I3 引擎保真**：测试 wrapper 在 provider 调用瞬间 `captured[day] = account.state()`，断言 engine submitted == provider 返回 orders。
  - **模式判据两个廉价用例**：①`order_provider=None` + `schedule=()` → 保持旧空 schedule 行为（possible_held == 空 union，非 None，不触 ValueError）；②`order_provider` 非 None + `possible_held_symbols=None` → 明确 ValueError（配置错误）。
  - schedule 模式黄金账本(:729-819)与引擎级 P0.3 `test_changing_only_an_execution_day_open…`(:950-978) **不改、必须仍绿**（schedule 路径逐字节不变；新字段默认 None，判据分支对旧构造恒走原逻辑）。
- **test_research_runner.py**：artifact 集相等(:445/:463/:474-490)经常量自动适配；:493-541（601318 周卖跌停）改**理由存在式**；:611-636 沿用。
- **test_end_to_end.py** 工件契约(:70-122)：删 :99 顶层 orders.parquet 读、:111-120 提交==账本/跨情景一致断言。替换：artifact 集自动断言(:74) + 每情景 order_diffs 内部自洽 + **I1a/I1/I2/I4 验收**：读各情景 fills+equity 推期末持仓互等且==末次 target（fixture 满足 I1 前提故 I1/I2 无条件触发）；期末权益 `zero ≥ ct ≥ fc`（I4，fixture 上界条件严格成立）。
- metrics/performance/ledger 单测不变（fills schema 未动）；report html 沿用每情景 order_diffs 键。

## 验证（按序）
- pytest 分阶段：rebalancer 单测 → engine → research_runner + end_to_end → 全量回归；`ruff check` 改动文件。
- **真实数据端到端验收**：清旧 run 目录（续跑摘要不含代码版本 → 陈旧陷阱）→ 重跑 `project/` 的 momentum_60d（~348 周调仓）。核对：①**I1a**：无 stale 残差——提交单均为账户可实现量级，未成交坍缩至真实原因（涨跌停/停牌/真实现金约束买不足）且理由可审计；②**I4** 业务验收：期末权益 `fc ≤ ct ≤ zero`（若破坏，先排除持仓残差混淆（I1），再归因现金约束路径或末期末收敛）；③**I2** 在合格子集（无奇数残余/现金足/有后续调仓机会）上对齐末次 target；④**I1** 若情景持仓仍不等，属真实现金约束下合理分叉（§522），报告如实展示每股数。
- 若结果符合预期，用新口径 `research report` 出新 HTML 供 owner 目检。

## 不改 / 边界
- fills/rejections/equity/submitted/cash_ledger/order_diffs/metrics/report **schema 与键名不变**（仅语义=该情景实际提交）；`reconcile_orders`、execution（先卖后买已在 executor，不动）、corporate_action、costs、schedule 模式与引擎级 P0.3 不变。新字段默认 `None/()` → schedule 模式构造零改动。
- 整手卖约束禁 <100 股卖 → CA 奇数残余超额留 stale（I2 排除之）；提交订单集跨情景不再相等是 A 方案本意；成本情景持仓/权益差异若为真实路径效应，是预期而非缺陷。
- **CA 仅同 ticker（已核实 corporate_actions.py）**：分红 `credit_cash` + 拆并股 `increase_position(symbol)`(:90/:130)，配股/合并/转股一律 `UnsupportedCorporateAction`(:103-107) 到不了账户，无换 ticker 字段 → `possible_held_symbols = 各期目标并集` 对 M0 完备（曾持有 ⇒ 曾 target>0 ⇒ ∈ 并集）。**边界**：若未来 CA 支持 ticker replacement/分拆新标的，须把 CA 可生成 symbol 并入 possible_held_symbols，否则 M0 断。
- 不动：registry 默认实验字典序怪癖、owner 的 .env.example/RUNBOOK、git 提交（仅按需）。
