# 缓冲式风险加权动量策略设计

## 目标

在不修改 `momentum_60d` 信号定义的前提下，把当前“每周 Top 10 等权”组合升级为可审计的缓冲式风险加权组合。首期目标是在所有预锁定成本情景下改善 OOS 风险调整后稳定性并降低换手，而不是追求最高收益率。

本设计只改变组合成员保留、风险权重和小额再平衡规则；不增加新因子、不做市场择时、不调整总杠杆、不自动搜索参数，也不依据结果选择调仓频率或成本情景。

## 研究边界与固定参数

`momentum_60d` 的版本、60 日收益定义、因子质量规则和时点股票池过滤顺序保持不变。首期策略参数固定为：

```text
target_count: 10
entry_rank: 10
hold_rank: 15
risk_lookback_days: 60
min_risk_observations: 40
volatility_floor_annualized: 0.10
max_single_weight: 0.15
rebalance_band_absolute: 0.02
gross_exposure: 1.00
long_only: true
leverage: false
weight_quantum: 0.000000000001
```

所有参数、排序规则和数值算法版本进入 `portfolio_rule_version`、策略快照和实验 ID。首期只有这一组预注册参数，不在 Walk-Forward fold 之间或查看结果后调整。

## 排名与资格

每个信号日按既有顺序先确定时点股票池，再计算因子有效性。所有同分使用规范 `symbol` 完整字符串的 Unicode/ASCII 字典序升序，例如 `000001.SZ < 000002.SZ < 600000.SH`；不拆分数字、不按交易所分组、不继承供应商行序。

组合构建保存两种排名：

1. `raw_momentum_rank`：在时点股票池内所有因子有效证券中，按动量降序、symbol 升序得到。
2. `risk_eligible_rank`：删除风险输入无效的证券后，保持上述排序重新连续编号。

`entry_rank=10` 和 `hold_rank=15` 均应用于 `risk_eligible_rank`。因此风险无效的前十证券不会占用名额，后续有效证券会向前补位；新进入证券仍必须属于风险有效集合的前十，不允许无限向排名尾部寻找。审计表必须同时保存两种排名和风险淘汰原因。

## 风险估计

风险输入使用与动量相同、已冻结的时点化总收益价格。信号日 `T` 的窗口是截至 `T` 的最近 60 个已确认市场交易日，包含 `T`，且不得读取 `T` 后数据。

- 正常收盘日以复权收盘计算日收益。
- 有可信停牌证据时，按已有 stale mark-to-market 规则以前值计价，该市场交易日日收益为 0；复牌变化在复牌日一次体现。
- 不明原因缺价、无前值、质量 ERROR 或停牌证据不足使该证券风险输入无效。
- 窗口内至少需要 40 个真实有效收盘观察；可信停牌前值日参与 60 日收益路径，但不计入这 40 个真实观察。
- 年化波动率为日收益样本标准差（`ddof=1`）乘 `sqrt(252)`；低于 10% 时按 10% 计算。

风险计算必须输出窗口起止日、真实观察数、停牌前值日数、无效原因、原始和应用下限后的年化波动率。资格只由 60 日窗口内的 `real_close_observations >= 40` 判定，不直接以连续停牌天数或 ST 状态判定；因此长时间停牌只有在真实观察数降到 40 以下或停牌证据不可信时才使风险输入无效。新上市、刚调入或长时间停牌证券可以因风险历史不足暂时无法入选；这不是全 fold 完整性失败，也不得在 `universe_membership` 中增加或改写标记。ST 是否排除只能由已冻结的因子/交易条件政策显式决定，不属于成分资格规则。

## 成员缓冲

Fold 第一次调仓没有历史目标，直接从 `risk_eligible_rank <= 10` 选择，最多十只。后续调仓：

1. 从上一期目标成员中，保留仍属于时点股票池、因子有效、风险有效且 `risk_eligible_rank <= 15` 的证券；
2. 按当期 `risk_eligible_rank` 升序从前十候选中补足空位；
3. 最多十只，候选不足时不降低门槛，剩余资本留作现金。

上一期状态只继承冻结目标成员代码，不继承目标数量、实际持仓、现金或成交状态。某一成本情景的拒单不能改变下一期成员选择。Fold 之间连目标成员状态也完全重置。

跌出股票池、因子失效、风险失效或排名低于 15 的上一期目标成员，在当期目标中权重归零。这是组合策略明确给出的退出目标；成分事实层本身仍只是资格记录，不直接生成卖单。

## 风险权重

对最终目标成员：

```text
risk_score_i = 1 / max(annualized_volatility_i, 0.10)
raw_weight_i = risk_score_i / sum(risk_score)
```

随后以确定性的 capped-simplex 算法分配。理论目标总暴露为 `min(1.00, 目标成员数 × 0.15)`：把超过 15% 的证券固定为 15%，剩余目标暴露按未封顶证券的 `risk_score` 比例重新分配，直至无证券超限或全部封顶。若成员数量不足以在上限内承载 100%，未分配权重留作现金，不提高上限、不使用杠杆。

为得到跨平台稳定序列，所有权重以十进制定点数计算，并向下量化到 `1e-12`。量化后的可分配余数按 symbol 字符串升序，每次增加一个 `1e-12`，跳过已到 15% 上限的证券，直到达到本次可实现的目标总暴露；不能分配的余数记为现金。手数取整在权重求解后由既有执行准备层完成，不反向修改理论权重。

每个调仓日保存 `portfolio_construction.parquet`，至少记录 symbol、两种排名、成员状态（保留/新入/退出/风险无效）、风险观察、波动率、risk score、封顶前后权重、量化余数、现金残余和规则版本。

## 统一目标与场景账户

所有成本情景共享完全相同的信号、目标成员与理论目标权重。每个情景根据自己的信号日账户权益和持仓，独立把统一权重转换成目标股数并对账，因此订单、成交、现金和实际仓位可以分化。

成员缓冲的上一期状态始终来自统一目标成员，而非任何场景的实际成交持仓。这样成本、拒单或现金差异不会把下一期策略变成另一个成员选择规则。

## 调仓带宽

对继续持有且当期仍有正目标权重的证券，使用绝对 2 个百分点带宽：

```text
abs(current_weight - target_weight) < 0.02  => 不生成调整订单
```

`current_weight` 使用该成本情景在信号日收盘后的账户权益及当日可审计收盘价计算。订单仍在下一确认交易日按既有规则执行，不读取未来开盘价。新进入证券和目标权重归零的退出证券不适用带宽；它们必须进入正常账户对账。恰好等于 2% 时需要调仓。

低于一手的数量差不下单。所有带宽或手数抑制必须记录 symbol、当前/目标权重、权重差、当前/目标数量和稳定原因 `within_rebalance_band` 或 `below_one_lot`，不能静默消失。

报告分别展示成员变化产生的换手、连续持仓再平衡换手、带宽抑制金额和手数抑制金额，以解释换手变化来源。

## 一次性样本外挑战

策略优化会消耗样本外证据。任何正式比较必须在读取挑战者结果前不可变发布：

```text
baseline_experiment_id
challenger_strategy_hash
comparison_policy_hash
fold_schedule_hash
universe_id
universe_version
membership_table_sha256
evidence_summary_sha256
declared_before_run_at
strategy_family
```

这四个股票池字段组成不可拆分的 `universe_definition` 身份块，并与其余预声明字段共同进入 `challenge_id`。挑战者与基线使用相同数据、验收、时点股票池、fold 日历、初始资金、`momentum_60d` 信号及成本情景；只允许组合构建规则不同。任一股票池身份字段不同都属于不可配对的研究身份错误，不能作为本设计的一次性策略比较；跨股票池研究必须另立预注册方案。

开始正式挑战时，注册表仍以 `strategy_family + fold_schedule_hash` 为消费键原子消费 holdout，并在消费记录中固定完整 `universe_definition` 身份块。股票池身份参与 `challenge_id` 和幂等恢复校验，但不扩展消费键；否则更换股票池版本会错误地产生新的“未消费”槽位。即使挑战失败、进程崩溃或结论被拒绝，该区间仍为 `consumed`。只有相同 `challenge_id` 及全部相同哈希可以幂等恢复；参数、输入或股票池定义变化会产生新 ID，不能复用已消费区间作为未见样本外，也不能通过更换股票池版本把相同历史重新声明为未见数据。

该机制只证明项目流程没有在结果之后改写本次挑战者，不声称研究者从未在系统外看过历史。失败后的新版本可把已消费区间用于开发诊断，但正式晋级必须等待新的未消费历史 fold 或未来数据。少于五个未消费完整 fold 时，挑战最多得到 `INCONCLUSIVE_RESEARCH_ONLY`。

## 比较政策

`StrategyComparisonPolicy` 和其哈希在挑战前固定。比较按相同 `fold_id + cost_scenario` 成对进行；缺失或重复配对是完整性失败。挑战者整体 Walk-Forward 稳定性必须先为 STABLE，然后对 `ExperimentSpec.cost_scenarios` 中每个预锁定情景分别要求：

```text
aggregate_sharpe_delta >= 0.10
aggregate_annualized_return_delta >= -0.02
positive_fold_ratio >= baseline
worst_fold_calendar_return_delta >= -0.02
median_abs_max_drawdown <= baseline
median_turnover <= baseline * 0.85
median_explicit_cost_ratio <= baseline
median_reject_rate_delta <= 0.02
median_invested_exposure >= 0.90
```

`daily_invested_exposure = market_value / net_equity_after_cost`（仅限本设计的 long-only 组合）；每 fold 先取所有 OOS 开市日的算术平均，再以各 fold 平均暴露的中位数作为 `median_invested_exposure`。`median_abs_max_drawdown` 同理是各 fold 的 `abs(per_fold_max_drawdown)` 中位数。所有 delta 均为同一情景下挑战者减基线；收益类的 `-0.02` 表示最多落后 2 个百分点，而不是相对百分比。

所有场景取合取结果，不指定可事后替换的主情景。指标不可定义、未消费 fold 少于五或存在合法市场级跳过时为证据不足，不能晋级。`zero_cost` 若被声明，也作为独立情景参加比较；同路径显式成本指标仍遵循 Walk-Forward 规格，不能用零成本重跑替代。

挑战结论为：

- `PROMOTED`：研究完整，至少五个未消费 fold，且所有场景通过全部门槛；它只表示策略研究晋级，不表示自动部署或投资许可。
- `REJECTED`：研究完整但任一门槛失败；展示全部失败项，不自动生成新参数。
- `INCONCLUSIVE_RESEARCH_ONLY`：研究有效但 fold、合法跳过或指标观察不足；不表示接近晋级或接近拒绝。
- `FAILED`：身份、数据、日历、账本、消费登记或配对完整性错误；比较结论为 null。

## 组件与产物

```text
portfolio/risk_estimation.py
portfolio/buffered_risk_weight.py
portfolio/rebalance_band.py
research/strategy_challenge/models.py
research/strategy_challenge/registry.py
research/strategy_challenge/compare.py
research/strategy_challenge/reporting.py
```

正式产物至少包括：

```text
strategy_challenge.json
holdout_consumption.json
challenger_portfolio_construction.parquet
paired_fold_metrics.parquet
strategy_comparison.json
strategy_comparison_report.html
```

`strategy_challenge.json`、`holdout_consumption.json`、holdout registry 和 `strategy_comparison.json` 都必须保存完整 `universe_definition` 身份块。`paired_fold_metrics.parquet` 保存每个配对的基线值、挑战者值、差值、阈值和逐项通过状态。比较 JSON 固定两侧实验 ID、三类快照、schedule hash、政策 hash、holdout 消费记录和结论。注册表使用锁和原子发布，保证同一作用域内的 holdout 只能有一个正式消费者；已有记录不可覆盖或删除。

## 错误处理与验收

风险输入无效只淘汰对应候选并留下原因；数据哈希、时点股票池、schedule、账本、配对或注册表完整性错误使正式挑战 FAILED。正常停牌、涨跌停、现金不足、整手约束与拒单是策略结果，进入指标而非系统失败。

离线测试必须覆盖：字符串排序、双重排名、缓冲保留/补位/退出、风险无效、停牌前值、40/60 日边界、无前视、capped-simplex 上限和现金残余、`1e-12` 量化、统一目标/场景账户分化、2% 等号边界、新入/退出不受带宽、fold 重置，以及换手原因拆分。

挑战测试必须覆盖：原子消费、并发冲突、崩溃后仍消费、同 ID 恢复、修改任一哈希不可恢复、修改任一股票池身份字段会改变 `challenge_id` 并阻止配对、换股票池版本不能重置相同历史的未见状态、少于五 fold、合法跳过、缺失配对、全场景合取、任一门槛失败、FAILED 结论为空，以及报告不能隐藏失败项或提出下一组参数。

## 非目标

本次不修改动量信号、不加入多因子、不做行业中性、市值中性、市场择时、动态总仓位、参数网格搜索、贝叶斯优化、跨 fold 学习、实盘部署或自动交易。
