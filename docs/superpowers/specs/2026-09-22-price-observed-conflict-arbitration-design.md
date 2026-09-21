# 价格观测仲裁：跨源冲突的第四条证据通道（2026-09-22）

状态：**待 owner 复核**。本文定义公司行为跨源冲突的机械化裁定边界，是
[ADR-007](../../adr/007-corporate-action-third-party-arbitration.md)（第三方仲裁）
与 [ADR-009](../../adr/009-corporate-action-absent-ex-date.md)（价格事件通道）的
直接延伸，并复用 [ADR-012](../../adr/012-corporate-action-exemption-reads-the-classification.md)
已接线的 baostock 因子通道。所有代码事实于 2026-09-22 在本分支逐条核实；所有
语料测量基于已发布且不可变的 `e732b191…`。

## 1. 问题

公司行为的人工负荷分布在三处，只有一处是可机械化的：

| 层面 | 规模 | 可机械化？ |
| --- | --- | --- |
| 隔离表 `corporate_action_quarantine` | 128 行 / 75 只符号 | **部分是**——本规格的目标 |
| 覆盖裁定 `corporate_action_coverage` 的 `UNTRUSTED` | 62 行（38 行退化行已修，21 行 2015–2020 陈账，3 行真问题） | **否**——见 §5 |
| 验收清单人工检查 | 9 项（6 项 `MECHANISABLE_CODES`，3 项 `OPERATOR_ONLY_CODES`） | 否——本规格不涉及 |

隔离表 128 行的构成：`incomplete` 66 行（多数无 ex_date，属 ADR-009 域）、
`cross_source_conflict` 50 行（**= 25 个真实冲突**，每个冲突两侧各一行）、
`non_distributive_restructuring` 12 行（ADR-008 已豁免）。

**本规格只处理那 25 个跨源冲突。** 它们是目前唯一"有明确正确答案、却因为缺少
裁定权威而卡住"的一类。

## 2. 事实基础（2026-09-22 核实）

**仲裁协议的既有形状。**

- `CorporateActionArbiter` 是一个 `Protocol`，只有
  `arbitrate(cninfo, eastmoney) -> str | None` 一个方法，返回 `None` 即维持隔离
  （[corporate_actions.py:180-192](../../../src/stock_quant/data_model/corporate_actions.py#L180-L192)）。
- `_GuardedArbiter` 提供两条护栏：已被 owner 复核的 `(symbol, ex_date)` 永不仲裁；
  仲裁器抛出的任何异常降级为"不仲裁"并记录原因
  （[data_pipeline.py:3509-3551](../../../src/stock_quant/data_pipeline.py#L3509-L3551)）。
- 仲裁在人工复核**之前**执行（[data_pipeline.py:2174](../../../src/stock_quant/data_pipeline.py#L2174)
  早于 [:2196](../../../src/stock_quant/data_pipeline.py#L2196)），所以人工裁定始终保有最终否决权。

**价格证据已存在且非循环。**

- `tushare/daily` 原始快照 **3703 个**，含 `close` 与 `pre_close` 两列；合并去重后
  1,732,474 行、659 只符号、跨度 2005-01-07..2026-09-18。`pre_close` 在除权日是
  **交易所公布的除权除息参考价**。
- 该证据不经过 `corporate_action`，因此**不循环**——这与 `adjusted_bar` 的
  `adjustment_factor` 不同，后者由本项目已入账的行为派生，用它判定未入账的冲突
  是自证。
- baostock `adjust_factor` 系列含 `backAdjustFactor` 累计因子；相邻两行之比即该次
  除权的价格因子。`factor_event_dates()` 目前**丢弃了这个幅度**，只返回日期
  （[baostock_factor.py:106-124](../../../src/stock_quant/data_sources/baostock_factor.py#L106-L124)）。

**人工复核通道已存在但几乎未被使用。**

- `project/configs/corporate_action_reviews.yml` 现有 **3 条**记录；
  `apply_corporate_action_reviews` 会逐字段校验后记为 `<side>+reviewed`
  （[corporate_actions.py:536-590](../../../src/stock_quant/data_model/corporate_actions.py#L536-L590)）。
- 一侧数值变化即抛错（fail-closed），不会静默复用陈旧复核。
- 作者成本高：每条要手填 8 个字段并自行查数。这是它只被用 3 次的原因。

### 2.1 语料测量：25 个冲突的实测因子对照

对每个冲突取 `obs = pre_close(ex_date) / close(前一交易日)`，两侧各按
`(prev_close − cash) / (prev_close × (1 + bonus + cap))` 算预期因子，再按 D2/D4
的规则判定（`W=2`、`L=3`，见 §3）：

| 判定 | 条数 | 说明 |
| --- | --- | --- |
| **D2 价格定案** | **16** | 全部指向 cninfo |
| **D4 精度合并** | **2** | 300124（差 1e-7）、301308（差 6.7e-8） |
| 维持隔离 | 7 | 见 §5 |
| 合计 | 25 | |

留隔离的 7 条及其理由（以最小报价单位 `tick = 0.01 / prev_close` 计）：

| symbol | ex_date | 维持理由 |
| --- | --- | --- |
| 002269.SZ | 2015-05-12 | 两侧总额相同、拆分不同，价格因子无法分离 |
| 600900.SH | 2016-07-19 | 赢方自身偏离 4.0 tick，超出赢方容差 |
| 600989.SH | 2021-05-20 | 败方仅偏离 1.5 tick，不可分辨 |
| 600989.SH | 2022-05-12 | 败方仅偏离 1.5 tick |
| 600989.SH | 2022-12-27 | 败方仅偏离 1.8 tick |
| 600989.SH | 2025-05-13 | 两侧预期因子完全相同，无分离度 |
| 601966.SH | 2025-07-10 | 败方仅偏离 1.0 tick |

**16 次定案全部指向 cninfo、0 次指向 eastmoney**，这与人类已写下的 rationale 一致
（"cninfo 报年度+特别股息，eastmoney 只报年度"），即 eastmoney 的 feed 不完整。
但这一**方向性偏斜本身必须在 ADR 中显式记录并监控**——一个永远选同一边的仲裁器
不是仲裁器，是盖章。见 §6。

**参数稳健性**：`L=2` 与 `L=3` 给出完全相同的结果（16/2/7），`W=1` 与 `W=2` 亦然；
到 `L=4` 才降为 15/2/8，`L=5` 降为 12/2/11。参数坐落在平台上而非刀锋上，这正是
可选它的理由。

## 3. 决策

### D1 —— 价格观测作为一个仲裁权威，而非新协议

新增 `PriceObservedArbiter`，**实现既有的 `CorporateActionArbiter` 协议**。
不新增协议、不新增隔离原因、不新增 `confirmed_by` 形状（沿用
`<origin>+<authority>` 形式，取值为 `<side>+price_observed`）。

接线：TDX 先试，返回 `None` 时再试价格观测，组合为一个 arbiter 交给现有
`_GuardedArbiter`。两条护栏（已复核不仲裁、异常降级）**自动继承**，无需重写。

### D2 —— 判定规则：两个独立阈值

设 `d_side = |obs − expected_side|`，`tick = 0.01 / prev_close`（前收与参考价各为
2 位小数，`0.01` 是报价最小变动，故 `tick` 是该标的下因子的一次舍入当量）：

| 条件 | 结果 |
| --- | --- |
| 两侧 `expected` 相同（差 < 1e-12） | 不作裁定（属 §5 或 D4 的范畴） |
| `d_X ≤ W × tick` **且** `d_other > L × tick` | 定案给 X |
| 其余 | 返回 `None`，维持隔离 |

**取 `W = 2`、`L = 3`。** 两个阈值必须彼此独立，不可退化为 `tol × safety` 的单一
尺度：赢方容差回答"一侧是否与观测一致"（允许前收与参考价两次舍入），败方界限回答
"另一侧是否已可分辨"（其隐含参考价须相差至少 L 个最小变动）。共用一个尺度会让
低价股被过度放宽——4.96 元的 601828 在单一尺度下因 `tol × 4 = 8 tick` 而误判为
不可分辨，分离阈值下它正确定案。

`tick` 随价格自适应：3.66 元的 600025 得 `tick ≈ 2.7e-3`，505.8 元的 301308 得
`tick ≈ 2e-5`。

### D3 —— 定案必须可从存储字节重算

每次定案记录：`prev_close`、`pre_close`、两侧预期因子、采用侧、原始快照哈希。
沿用 ADR-012 的 `adjust_factor` 快照先例，arbiter 读的 tushare 日线走同一
content-addressed store（`_LazyFactorChannel` 的既有惰性形状，见
[data_pipeline.py:3447-3506](../../../src/stock_quant/data_pipeline.py#L3447-L3506)）。
不可用 → 断言为零 → 维持隔离（fail-closed）。

### D4 —— float32 往返误差不作为冲突

`_same_facts` 现用精确浮点相等比对
（[corporate_actions.py:754-763](../../../src/stock_quant/data_model/corporate_actions.py#L754-L763)）。
加一个**由 float32 精度证明的相对容差**（相对 ε ≈ 1.2e-7）。这确凿合并
**2 条**：300124（`capitalization_ratio` 0.999878 vs 0.9998781）、
301308（`cash` 0.990744266 vs 0.9907442）。

**不接受更宽的容差**：600989 2025-05-13 的两侧相差 1.66e-5，比 float32 精度大
两个数量级，且价格通道对两侧给出**完全相同**的预期因子（均 0.974051），无法佐证
任何一方。合并它等于替 owner 判断"这个差异不重要"——那正是本项目保留给人的决定。
该条维持隔离。

### D5 —— 分离度不足时不动

若败方偏离不足 `L × tick`（两侧不可分辨），或赢方自身偏离已超 `W × tick`
（两侧都不像），则**即使其中一侧看似更接近也不裁定**。这是 §2.1 中 7 条维持的
来源，是本规则刻意的保守方向；D2 的边界情形一律归入此条，而非就近取一侧。

## 4. 明确不做的事

- **不改 `UNTRUSTED` 的最终裁定。** 那是"这个缺口能不能接受"的业务判断，机器
  没有资格代替 owner。21 行 2015–2020 陈账需要一次重判运行（窗口选择是另一个
  决策），3 行真问题需要人看。本规格不触碰。
- **不扩宽 `_NON_BLOCKING_QUARANTINE_REASONS`。** ADR-012 决策 4 的"deny-list 形式
  不变"继续有效；`incomplete` 与承诺补偿维持阻断（ADR-009 决策 6）。
- **不处理 002269 那类"总额一致、拆分不同"。** 价格因子在数学上无法分离拆分比例
  （cninfo 10送6转9 与 eastmoney 10送5转10 总额同为 1.5），ADR-012 已记录 TDX
  同样无法分离。这是该通道的**原理性上限**，不是实现缺陷。留给人工复核通道。
- **不做复核工作表工具化（方案 B）。** 它是本规格的兜底，但在 D1–D5 落地并重新
  测量残差之前，无法知道它需要覆盖多少条。推迟。

## 5. 残差与兜底

D1–D5 落地后，25 个冲突的预期分布：

- **18 条机械化**（D2 的 16 + D4 的 2）
- **7 条留人工**，全部列于 §2.1。它们的共同形态是**机械规则在此处无法给出可信
  答案**：一类是原理性上限（002269 的拆分），其余是**两个 feed 都贴近实测值**——
  败方偏离不足 2 tick，看不出谁更完整。

这 7 条改由既有的人工复核通道（`project/configs/corporate_action_reviews.yml`，
3 条在用）处理；是否为其做工具化（方案 B）在重新测量后再定。

## 6. 风险与接受的代价

- **独立性弱于 TDX。** tushare 的 `pre_close` 是交易所参考价，与 cninfo 同源于
  发行人公告。它是**独立的验证路径**（不经我们入账、不可被我们的数据伪造），但
  **不是独立第三方意见**。ADR 必须写明这一点，否则未来会把"价格通道印证了
  cninfo"误读为"两个独立源都支持 cninfo"。这是本规格最重要的一条限制。
- **方向性偏斜。** 16/16 指向 cninfo。若后续语料仍为 100% 单向，说明该通道在
  区分"两个 feed 谁更完整"而非"谁更正确"。ADR 应要求记录每次定案的方向分布，
  使其可被审计发现。
- **`tick` 是与供应商报价精度的耦合。** 供应商改用更高精度报价，或复权因子算法
  变更，都会移动 `tick` 的适用性。D3 的留痕是为此准备的补偿控制。
- **停牌跨越除权日的形态未验证。** 本次 25 条无一涉及停牌跨越；601088 那条人类
  复核涉及停牌，其 rationale 用的是"停牌前最后收盘 vs 复牌参考价"。该形态下
  `pre_close` 的语义需要单独确认，**未确认前应 fail-closed**（即 `prev_close` 不是
  紧邻交易日的收盘时，不裁定）。
- **重跑才能生效。** 当前数据集不可变；规则自下一次 `data update` 起适用。

## 7. 验收标准

1. `PriceObservedArbiter` 满足 `CorporateActionArbiter` 协议，并通过既有
   `_GuardedArbiter` 接线，不改协议定义。
2. 单元测试覆盖：定案、两侧相同、分离不足、通道不可用、`prev_close` 非紧邻交易日
   （停牌形态）五个分支。全部 in-memory 确定性，无墙钟。
3. 语料回放：对 `e732b191…` 的 25 个冲突重放，**定案 16、合并 2、维持 7**，
   与 §2.1 的两张表逐条一致（含每一侧的 tick 偏离量）。
4. 每次定案可从原始快照字节重算，且 `_check_raw_snapshots` 覆盖其快照。
5. 全量测试套件通过。
6. 新 ADR（ADR-013）落盘并进 `DECISIONS_INDEX.md`。

## 8. 证据附录

**测量命令可复现**：tushare 原始快照 `project/data/raw/tushare/daily/*/*/*/data.parquet`
（3703 个）合并去重 → 按 `(symbol, ex_date)` 取前收与参考价 → 对
`e732b191…/corporate_action_quarantine.parquet` 中 `reason == cross_source_conflict`
的 50 行分组比对。

**关键样本**（全部 25 条见测量输出）：

| symbol | ex_date | observed | cninfo | eastmoney | 判定 |
| --- | --- | --- | --- | --- | --- |
| 600188.SH | 2023-07-17 | 0.581931 | 0.582029 | 0.606240 | → cninfo |
| 600188.SH | 2021-07-23 | 0.944444 | 0.944444 | 0.966667 | → cninfo |
| 601898.SH | 2024-08-20 | 0.959320 | 0.958950 | 0.967308 | → cninfo |
| 600188.SH | 2022-07-14 | 0.944352 | 0.944352 | 0.955481 | → cninfo |
| 600900.SH | 2016-07-19 | 0.972582 | 0.969535 | 0.990140 | 维持（赢方偏离 4.0 tick） |
| 002269.SZ | 2015-05-12 | 0.398198 | 0.398198 | 0.398198 | 维持（拆分，见 §4） |
| 300124.SZ | 2016-05-18 | 0.493162 | 0.493193 | 0.493193 | 合并（D4） |

**既有的人类复核先例**：601088.SH 2017-07-10 的 rationale 独立地用"停牌前最后
收盘 22.29 − 复牌参考价 19.32 = 2.97"证实了 cninfo 一侧
（`project/configs/corporate_action_reviews.yml`）。本规格把这个人类已经在做的
推理机械化。
