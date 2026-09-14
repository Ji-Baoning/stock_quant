# 样本外滚动验证与回测稳定性设计

## 目标

在既有可信数据、时点化股票池和固定策略研究之上，建设可审计的固定日历 Walk-Forward 样本外验证。首期只验证预先冻结的策略参数能否在连续、独立的年度 OOS 区间稳定表现；不做自动参数寻优、频率择优、成本情景择优或事后删除表现不佳的 fold。

这解决“单一区间回测的结果是否跨年度稳定”的问题，不证明策略具有未来收益，也不构建自适应选参、机器学习调参、资金分配或自动交易系统。

## 研究纪律

1. 策略参数、调仓频率、成本情景、股票池、数据和判定规则在运行前冻结并进入实验身份。
2. 每个 OOS fold 使用独立账户和独立持仓；前一 fold 的现金、成交、持仓和权益绝不延续到下一 fold。
3. 预热仅为计算因子历史，绝不产生订单、成交或绩效；总报告只使用不重叠的 OOS 日收益。
4. 任何已执行 fold 都不得因收益、回撤、换手、拒单率、流动性或市场表现差而从日历、报告或稳定性判定中删除。
5. 成分资格、因子质量和交易可执行性保持分层：先时点股票池，再因子过滤，最后交易条件；Walk-Forward 不得绕过既有可信性门禁。

## 策略、实验与数据快照

实验冻结三份不可变快照及其规范 JSON SHA-256；完整载荷和三个哈希均写入实验 ID 输入、run manifest 和 Walk-Forward 产物。

```text
strategy_snapshot
  strategy_version
  factor_versions
  portfolio_rule_version
  rebalance_frequency
  parameters_hash

experiment_snapshot
  universe_version
  corporate_action_version
  cost_scenarios
  walk_forward_policy
  stability_policy

data_environment_snapshot
  price_version
  fundamental_version
  calendar_version
```

各版本是固定数据集中对应表或证据的内容哈希，而不是供应商显示名称。策略未消费基本面时，`fundamental_version` 必须使用固定值 `NOT_USED`；不能为了填充快照而引入无关数据依赖。`corporate_action_version` 同理指向公司行为事实及其覆盖证据的组合内容哈希。

修改因子或组合参数必须改变 `strategy_snapshot`；修改费用、滑点或 Walk-Forward 规则必须改变 `experiment_snapshot`；修订行情、基本面或日历必须改变 `data_environment_snapshot`。日志路径、报告目录、机器名、进程号、运行时间戳、并行 worker 数不进入上述快照或实验 ID。

首期禁止将 `rebalance_frequency` 作为事后择优变量，也禁止选择最佳成本情景作为正式结论。若未来做频率或参数比较，必须在新的、明确冻结的实验矩阵中进行，并报告所有预先声明的组合。

## WalkForwardPolicy 与日历

首期策略固定为：

```text
mode: fixed_calendar_oos_v1
calendar_anchor: jan_1
warmup_years: 3
warmup_unit: calendar_years
min_warmup_trading_days: 756
required_stable_history_days_before_s: 60
oos_months: 12
step_months: 12
account_reset: true
aggregate: oos_only
return_aggregation: concatenate_oos_daily_returns
drawdown_aggregation: per_fold_only
global_drawdown_aggregation: forbidden
per_fold_drawdown: required
require_full_oos_periods: true
partial_boundary_policy: record_not_evaluated
fold_status_policy: strict_market_calendar_v1
```

`ExperimentSpec.date_range` 是请求评估范围，不包含预热。对每一个完整自然年候选区间，`calendar_start`/`calendar_end` 是自然日边界；`first_trading_day` 为锚点当日或其后的首个确认开市日，`last_trading_day` 为下一锚点前最后一个确认开市日。预热区间为 `[calendar_start - 3 calendar years, calendar_start)`，并且在 `first_trading_day` 前必须至少存在 756 个确认交易日及策略所需最慢因子的 60 个稳定历史日。这里的“60 个稳定历史日”是数据与因子引擎的窗口能力要求：一个在整个窗口内持续上市、持续属于股票池且数据完整的证券，必须能在首个 OOS 日产生因子；它不要求新上市或刚调入的每只证券都已有 60 日历史。单证券仍由因子自身的上市天数、缺失值与质量规则决定何时获得信号，不能因此让整个 fold 失败。

范围首尾未形成完整 12 个月 OOS 区间的日期必须记录为 `not_evaluated_boundary`，连同原因写入日历；它们不是 `skipped`，不进入 OOS 聚合。Runner 在任何回测前物化并哈希 `fold_schedule.json`；后续不得基于收益、数据质量或市场表现重切、删除、补齐或重排日历。

`fold_schedule.json` 必须保留所有原计划 fold，即使其后来 preflight 失败。每行至少记录 fold ID、自然/交易日边界、预热边界、成员快照计划和计划时已知的处置。它在回测前写入并哈希，之后绝不修改，因此运行后状态不能回写到该文件。

运行结果另存为不可变的 `fold_outcomes.json`，按 `fold_id` 一一引用 schedule 中的每个计划 fold，记录 `executed`、`failed_preflight` 或 `skipped_not_tradeable` 及原因；范围边界行在 schedule 中固定为 `not_evaluated_boundary`，不产生执行结果。这样失败 fold 同时永久存在于原始计划与结果账本中，且不会为更新状态而破坏预先冻结的 schedule 哈希。`walk_forward_manifest.json` 同时固定 `fold_schedule_sha256` 和 `fold_outcomes_sha256`。

`skipped_not_tradeable` 只有在版本化政策中存在市场级证据，且交易所日历或市场级禁交易证据覆盖整个 OOS 区间时才允许。此时 schedule 仍保留自然日边界，`first_trading_day` 与 `last_trading_day` 为 null，outcome 引用市场级证据并标记跳过。没有开市日但缺少该证据属于 FAILED，而不是合法跳过。个股停牌、数据缺失、股票池缩小、因子异常、执行拒单、回撤或收益差不能触发跳过。

## Fold 执行与产物

对 OOS `[S,E]`：`[S-3年,S)` 只供预热；`[S,E]` 用独立初始资金、账户、持仓和完整交易/成本/公司行为规则执行。每个 executed fold 持久化：

```text
folds/<fold_id>/fold_manifest.json
folds/<fold_id>/signals.parquet
folds/<fold_id>/orders.parquet
folds/<fold_id>/fills.parquet
folds/<fold_id>/equity.parquet
folds/<fold_id>/daily_returns.parquet
folds/<fold_id>/metrics.json
```

运行根目录另有 `walk_forward_manifest.json`、`fold_schedule.json`、`fold_outcomes.json` 和 `stability_report.json`。每份 fold manifest 固定三类快照哈希、数据/验收/股票池版本、预热和交易边界、交易日数、逐日成员快照、状态以及脱敏失败原因。

若任一 fold 在冻结、日历、预热、数据、验收、股票池或执行完整性检查中失败，整个正式研究状态为 `FAILED`，`stability_conclusion=null`，不生成正式稳定性结论，也绝不允许降级或改写为 `INCONCLUSIVE`。该 fold 仍存在于 schedule，并在 outcome 账本中记为 `failed_preflight`，但不进入 OOS 聚合。正常的停牌、涨跌停、资金不足或不可成交订单是需要统计的策略/市场结果，不是执行完整性失败；只有账本缺失、日期越界、输入哈希不符、数量对账失败等系统完整性错误才导致 FAILED。

## 指标口径

设 `R` 为所有 `executed` fold 按真实交易日升序拼接的唯一有效 OOS 组合日收益。每个确认开市日必须恰有一条组合权益与收益记录；个股停牌使用既有 stale mark-to-market 规则，不能删除该市场交易日。非交易日、fold 间空白、预热日和 `not_evaluated_boundary` 不得进入 `R`；确认开市日缺失组合收益属于完整性失败，不能通过减少样本数掩盖。每个 fold 的首日收益以固定 `initial_equity` 为前值，其后以同 fold 前一交易日的 `net_equity_after_cost` 为前值。`N = len(executed_oos_daily_returns)`，报告必须给出 `oos_return_observations=N` 与 `annualization_observations=N`。

```text
aggregate_return = product(1 + r for r in R) - 1
annualized_return = product(1 + r for r in R) ** (252 / N) - 1
annualized_volatility = sample_std(R, ddof=1) * sqrt(252)
sharpe_zero_rf = mean(R) / sample_std(R, ddof=1) * sqrt(252)
fold_calendar_return = product(1 + r for r in fold_R) - 1
```

不足两个有效日、或标准差为零时，波动和 Sharpe 为 `undefined`，不得伪装为零。日收益日期不得重叠；重叠是正式研究失败。

`per_fold_max_drawdown` 必须只用该 fold 独立账户的 `equity.parquet.net_equity_after_cost` 列，以逐日 mark-to-market 计算：

```text
drawdown_t = net_equity_after_cost_t / running_max(net_equity_after_cost)_t - 1
per_fold_max_drawdown = min(drawdown_t)
```

`stability_report.json` 严禁包含跨 fold 拼接收益计算出的最大回撤、Calmar 或任何假装连续账户的路径指标。它必须逐 fold 报告最大回撤及其分布统计。

每个 fold、每个预锁定成本情景报告 `gross_return_before_explicit_cost`、`net_return`、`explicit_cost_drag`、`slippage_impact`、`total_explicit_cost`、`initial_equity`、`explicit_cost_ratio`、`turnover`、`reject_rate` 和 `slippage_estimate`。其中：

- `gross_return_before_explicit_cost` 使用同一订单、成交数量和成交价格的账本重放，仅剔除佣金、税费等显式费用；它不声称零滑点。
- `explicit_cost_drag` 是该同路径重放收益与净收益之差。
- `slippage_impact` 以同一成交数量、实际成交价及政策冻结的参考价逐笔计算，不能改变成交集合。
- `explicit_cost_ratio = total_explicit_cost / initial_equity`；分母始终为本 fold 固定期初权益，报告同时保留分子与分母。
- `reject_rate = orders_with_any_rejected_quantity / submitted_orders`，按唯一 `order_id` 计数，部分成交且有剩余拒绝数量的订单进入分子；没有提交订单时为 `undefined`。另报 `fully_rejected_order_count`、`partially_filled_order_count` 与 `unfilled_quantity_rate = sum(rejected_quantity) / sum(requested_quantity)`，分母为零时同样为 `undefined`。
- 现有 `zero_cost` 若保留，是会改变现金路径的独立反事实回测情景，不等于同成交路径的 `gross_return_before_explicit_cost`，也不得用它计算 `explicit_cost_drag`。

## 稳定性判定

`StabilityPolicy` 是版本化、哈希化配置，不是隐藏代码常量。首期默认规则：

```text
FAILED:
  任一预热、数据、可信性、股票池或执行完整性检查失败。

INCONCLUSIVE:
  研究有效但 executed fold 少于 5；
  或存在合法 skipped_not_tradeable fold。

STABLE:
  executed fold 至少 5；
  所有预锁定成本情景均完整执行；
  且每个成本情景各自的正收益 fold 比率至少 60%；
  且每个成本情景各自的最差 fold_calendar_return 大于 -10%。

UNSTABLE:
  非 FAILED、非 INCONCLUSIVE，且不满足 STABLE。
```

`INCONCLUSIVE` 只表示研究过程有效但当前证据不足，不表示策略无效、接近 UNSTABLE 或接近 STABLE。每个成本情景均完整展示，不能被择优。稳定性条件逐个应用于 `ExperimentSpec.cost_scenarios` 中全部预锁定情景，最终结论取合取结果；不允许指定“主要情景”绕过较差情景。判定是研究门禁提示，不是自动选参或投资决策。

`stability_report.json` 必须含 `stability_policy_hash`，以及判定使用的原始 fold 指标、阈值、状态和原因。没有该哈希的稳定性结论不得作为正式结论。

`UNSTABLE` 只评价已完整执行且研究过程有效的 fold 表现；它不能承接任何完整性错误。若任一声明的成本情景缺失、未完成或产物不完整，结果是 FAILED，而不是 UNSTABLE。

## 组件边界

- `research/walk_forward/policy.py`：严格政策/枚举、规范化和哈希。
- `research/walk_forward/schedule.py`：固定日历和交易日边界，生成不可变 schedule 与独立 outcome 账本。
- `research/walk_forward/snapshots.py`：三类快照与身份载荷。
- `research/walk_forward/runner.py`：单 fold 隔离执行及产物管理。
- `research/walk_forward/metrics.py`：无重叠收益、逐 fold 风险与同路径成本指标。
- `research/walk_forward/evaluation.py`：唯一的状态/稳定性判定实现。
- `research/runner.py`：只编排冻结、日历、fold runner 和总报告；不做选参或原始数据修补。

## 验收

离线合成测试必须证明：相同冻结输入稳定生成相同 schedule、fold ID、快照哈希和结论；影响身份的字段改变会改变实验 ID，而日志路径、机器名、时间戳和 worker 数不改变 ID；OOS 重叠、跨 fold 账户延续、边界日混入聚合、表现差触发跳过和缺失 policy hash 都被拒绝。

测试还必须证明：失败 fold 仍留在 schedule 且 schedule 哈希不变、运行结果只写 outcome 账本；FAILED 无结论且不可转换为 INCONCLUSIVE；少于五个 executed fold 必为 INCONCLUSIVE；合法市场级跳过可审计；个股停牌不删除组合收益日；首日收益以前值 `initial_equity` 计算；全局回撤字段不存在；逐 fold 回撤基于 `net_equity_after_cost`；成本指标不改变成交路径；全部预锁定成本情景逐一参加判定；所有正式结果能追溯至三类快照、数据验收、时点股票池与政策哈希。

## 非目标

本次不做自动超参数选择、Walk-Forward 训练期优化、滚动参数更新、跨 fold 资金复利、全局回撤、动态仓位分配、实时交易或策略收益承诺。
