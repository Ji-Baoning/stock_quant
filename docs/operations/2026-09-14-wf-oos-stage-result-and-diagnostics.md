# Walk-Forward OOS 正式运行阶段结果 + 解释性诊断（2026-09-14）

- 触发方式：定时任务（用户 2026-09-14 预约）
- 任务：(1) 用正式 `walk_forward_oos_v1` 跑 ≥5 个年度 OOS fold、三成本情景，
  无论成败都保留阶段结果；(2) 补齐解释性诊断（IC/分层/暴露/集中度/流动性/
  延迟成交/成本归因），定位亏损成因。
- 本文所有绩效相关数字均来自 **UNTRUSTED 工程诊断**（28 只人工挑选样本池，
  幸存者偏差未消除），不构成可信绩效或投资建议。

## 结论（TL;DR）

1. **正式 walk_forward_oos_v1 运行被验收门禁阻塞**（阶段结果 = FAILED at
   `acceptance` preflight），且本次把阻塞原因定位到了**一个操作者动作**：
   现行数据集 `1709eddb…` 在全成分行情/公司行为回补落地前**不可能**通过
   验收自动检查；而 `b0e36345…`（30 名池）的验收清单已由操作者确认 8/9，
   只差 `cross_source_price_sample` 一项签字 + publish。短路径的全部机械
   准备（含恢复 b0e36345 绑定的宇宙定义文件）本次已完成并验证。
2. **解释性诊断已补齐**（新脚本 `project/explanatory_diagnostics.py`，产物
   `data/runs/debug/explanatory_diagnostics_46f8b74e/`）。对亏损年份的归因
   判断：**信号失效是主因、市场方向（多头 beta）定基调；集中度、流动性、
   执行路径都不是原因**；成本是稳定但次要的拖累。详见 §3。

## 1. 正式 walk_forward_oos_v1：阶段结果与阻塞定性

### 1.1 运行记录

规格：新建 `project/configs/experiments/momentum_60d_wf_tw_baseline.yml`
（派生自 `momentum_60d_wf_real_baseline.yml`，仅把已失效的宇宙绑定
`custom_csi300_ic_tradable`（无任何已发布数据集携带其钉住哈希）改为现行
冻结定义 `custom_csi300_tw_tradable`；组合规则/成本情景/日期范围未动）。
请求范围 2020-01-01..2026-08-28 → 2020..2025 六个完整年度 fold + 2026
部分年度记 `not_evaluated_boundary`；三成本情景 `zero_cost /
commission_tax / full_cost`。

| # | 绑定 | 命令结果 | 审计产物 |
|---|---|---|---|
| 1 | `dataset_version: CURRENT`（=1709eddb…，宇宙预检**通过**，universe_version `1d6a8c8f…` 已冻结） | FAILED at `acceptance`: `NoValidAcceptance` | `data/runs/preflight_acceptance_64986d7247674c8497e10e3651dd1d15/` |
| 2 | 对照：钉死已验收的 `52648bdc…` | FAILED at `universe_acceptance`: `index_membership_evidence`（成员表为空，冻结定义无法匹配） | 同类 preflight 审计 |
| 3 | 探测：钉死 `b0e36345…` + 恢复的定义 `custom_csi300_tw_tradable_28`（universe_version 逐位复现 `c211b85c…`） | FAILED at `acceptance`: `NoValidAcceptance`（**唯一**剩余阻塞） | 同类 preflight 审计 |

`research run` 恒为 RESEARCH 模式（无绕过开关），walk-forward 的 fold
预检又无条件要求绑定当前数据集的 ACCEPTED 验收审计（engineering 无豁免），
因此三条路径都在任何回测计算之前停止——没有产生任何 fold 结果，也没有
消耗任何 holdout（`research challenge` 未运行）。

### 1.2 为什么阻塞、阻塞在哪一层

- **验收注册表按数据集版本绑定、不可继承**。唯一 ACCEPTED 记录
  （`4526e772…`，2026-09-13，operator ji-baoning）绑定在 `52648bdc…` 上，
  而该版本成员表为空，永远无法驱动带时点宇宙的 walk-forward（运行 #2）。
- **`1709eddb…`（现行 CURRENT，657 成员）当前不可能验收**。本次
  `data acceptance prepare` 生成的 `acceptance-1709eddb.yml` 显示自动检查
  两项 FAIL：
  - `date_window_completeness`：~629 只成员无行情 → 全窗口
    `unexplained_missing_row`；
  - `corporate_action_evidence`：同样这批成员的公司行为证据
    `SOURCE_NOT_REQUESTED`。
  这两项与 [2026-09-14-blocking-gap-root-cause.md](2026-09-14-blocking-gap-root-cause.md)
  定性的扩池战役（D1–D5 src 缺陷 + ~654 只增量回补）是同一件事——回补落地
  前该版本发不出 ACCEPTED。
- 三个数据集（`52648bdc…` / `b0e36345…` / `1709eddb…`）的行情与公司行为
  表**逐字节相同**（daily_bar / adjusted_bar / corporate_action* /
  trading_calendar 的 parquet 哈希全部一致），差异只在 master（30 vs 659）
  与成员表。已验收 `52648bdc…` 上操作者确认过的 8 项行情侧人工核验，对
  `b0e36345…` 的同名检查在内容上完全成立（worksheet 也如此引用
  `previous_signed`）。

### 1.3 短路径：距正式 walk-forward 运行只差一个操作者动作

已完成的机械准备（本次新增/验证）：

- `project/configs/universes/archive/custom_csi300_tw_tradable_28.yml`：恢复
  `b0e36345…` 绑定的冻结定义（`membership_table_sha256 a6eff805…`），已
  验证其 `universe_version` 逐位等于 `c211b85c…`（运行 `46f8b74e…` 冻结
  过的值）。该定义与现行 `custom_csi300_tw_tradable.yml`（钉 `1709eddb…`
  的 766 行表）共用同一个 `universe_id`，两者同住顶层会让每个发布都以
  `duplicate universe_id` 失败，故 parked 于 `archive/`；使用前按
  `archive/README.md` 的两步交换程序换入顶层（换入时 `version` 必须等于
  `c211b85c…`，否则不得使用）。
- `project/acceptance-1709eddb.yml` + worksheets（`data/acceptance-worksheets/
  1709eddb…/`）：为长路径备好，等回补战役落地后操作者直接走确认线。
- 探测运行 #3 证明：数据集 + 定义 + 宇宙预检全部就绪，唯一缺口是验收记录。

操作者剩余步骤（**人工项，本次未代签**——`confirm` 是操作者签名线，代签
即伪造验收证据）：

```bash
cd ~/work/program/stock/project
# 1) 复核 standing worksheet（54 条价格样本，无同日双源配对的说明已在文中）
#    data/acceptance-worksheets/b0e36345…/cross_source_price_sample.md
python -m stock_quant data acceptance confirm \
    --checklist acceptance-b0e36345.yml --code cross_source_price_sample \
    --operator ji-baoning --acknowledge 54
# 2) 发布（自动检查与哈希当场重算）
python -m stock_quant data acceptance publish \
    --checklist acceptance-b0e36345.yml --root .
# 3) 跑正式 walk-forward（规格模板：/tmp/wf_b0e36345_probe.yml 的内容，
#    建议落为 configs/experiments/momentum_60d_wf_b0e36345_baseline.yml）
#    dataset_version: b0e36345…  universe_definition: custom_csi300_tw_tradable_28
python -m stock_quant research run --spec <该规格> --root .
```

注：`momentum_60d_pit_official.yml` / `momentum_60d_pit_tradable.yml` 引用的
`custom_csi300_ic_tradable` 与 `momentum_60d_wf_real_baseline.yml` /
`..._challenger.yml` 同样指向已无数据集匹配的定义，重跑前需要同样的
宇宙绑定更正（新规格文件已示范做法）。

## 2. 诊断对象与方法

- 对象：`data/runs/run_46f8b74e…/`（`momentum_60d_pit_official.yml` 的
  `backtest --engineering` 诊断，2015-01..2026-08，28 只 PIT 过滤池，等权
  Top-10，周频，三成本情景；full_cost 终值 +112.8%）。
- 工具：新脚本 `project/explanatory_diagnostics.py`（只读数据集与运行
  产物；写 `data/runs/debug/explanatory_diagnostics_46f8b74e/
  {explanatory_diagnostics.json, summary.md}`）。
- 口径：IC 视界 = 信号日收盘 → 5 个交易日后复权收盘（与调仓周期一致）；
  分层为等权五分位；板块用 master 的 board（**数据集无行业分类**）；
  无股本数据 → 以 20 日成交额分位作流动性/规模代理；滑点按成交价 vs
  滑点前参考价逐笔计量。

## 3. 诊断结果与归因判断

### 3.1 亏损年份的定位：信号失效 + 多头市场暴露，不是组合/执行问题

full_cost 年度收益：**2015 -12.3%、2018 -13.6%、2022 -14.5%、2026YTD
-5.7%**（其余年份为正；整体 +112.8%）。这些亏损年份的全部证据链：

| 年 | IC 均值 | 正IC率 | Q5-Q1（bp/期） | 对沪深300 beta | 判读 |
|---|---|---|---|---|---|
| 2015 | **-0.135** | 37% | **-267** | 0.35 | 入池即遇股灾：最高动量股单期 -194bp，反转最烈 |
| 2018 | -0.021 | 49% | -38 | 0.69 | 市场下跌为主（beta），信号小幅负贡献 |
| 2022 | **-0.096** | 36% | **-113** | 0.32 | 动量反转年，信号负贡献显著 |
| 2026YTD | -0.050 | 41% | -64 | 0.27 | 同 2022 模式，幅度较轻 |

对照盈利年份：2020 IC +0.131、Q5-Q1 +186bp/期；2017/2019/2024 IC 为正。
**结论：亏损年份 = 市场方向（多头仓位，beta 0.3–1.0）定基调 + 恰好在
这些年份信号失效（IC 转负、高分位跑输低分位）放大损失。** 这正是
walk-forward 年度 fold 要正式检验的命题——单窗口连续账户的年度切分只是
预览，fold 语义（独立账户、固定初始资金）仍待正式运行。

排除项（证据）：

- **持仓集中度**：等权结构下 HHI 恒 ≈0.100、有效 N≈10、Top3≈30%，
  2019 年后因 PIT 过滤后有效候选不足 10 只略升至 HHI 0.106–0.108——
  无集中度风险。
- **流动性/容量**：成交参与率（成交额/当日成交额）p50=0.0007%、
  p99=0.063%、max=0.31%，>5% 为 0 笔；全程 **0 天停牌锁定估值**
  （stale_market_value 恒 0）。100 万规模账户对这批大盘股无容量约束。
- **执行路径**：11.7 年共 59 笔拒单（insufficient_cash 49、涨停买不进 6、
  跌停卖不出 4），按“拒单后 5 日不利变动 × 参考名义”计量的不利影响合计
  约 +5.3 万（涨停买不进 +6.5 万是唯一显著项；现金不足拒单平均反而
  -1.1 万 = 偶然避损）。相对 +112.8% 的总收益，执行不是成因。

### 3.2 成本归因（次要但稳定的拖累）

累计（2015-07..2026-08，初始 100 万）：zero→full 权益差 **24.2 万**，其中
滑点 13.1 万 > 印花税 5.7 万 > 佣金 4.9 万，路径分歧残差 ≈0.6 万（三情景
成交集合有轻微分化：3948 vs 3956 笔，plan_diverged=True）。每笔滑点均值
10.2bp、最大 20bp，与 `configs/costs.yml` 的 0.1% 双边申报假设一致——
即滑点是**配置假设**而非实测价差，正式结论前需按 RUNBOOK 提示重审。
年化成本拖累 0.6–1.6 万/年（约占年均换手名义的 12bp/边），不解释任何
单一亏损年份的量级。

### 3.3 暴露画像（风格与板块）

- 组合动量分位（相对同日候选池）0.72→0.84 单调上升——策略本身就是
  动量暴露，且后期池内动量 dispersion 加大；波动分位 ~0.51–0.67、成交额
  分位 ~0.51–0.62，无系统性高波动/低流动性倾斜。
- 板块漂移：主板占比从 2015 年 94% 降至 2026 年 ~64%（科创板 2019 年后
  升至 ~28%）——**这是 28 只池的构成结果，不是策略选择**，正式结论需
  全成分池重做。
- 现金残余：2015 年 48%（池子预热），2019–2023 年 5–7%（PIT 过滤后
  有效候选不足 10），是后期 beta 偏低的直接原因。

## 4. 产物与复现

```bash
# 正式 walk-forward（预期：acceptance 阶段 FAILED，审计见 data/runs/）
python -m stock_quant research run \
    --spec configs/experiments/momentum_60d_wf_tw_baseline.yml --root .
# 验收准备（已完成；publish 需操作者先完成人工确认）
python -m stock_quant data acceptance show \
    --version 1709eddb6249ce24bc8516662bb0e72c27718ab00e7594ee30961cad906e783a --root .
# 解释性诊断
python explanatory_diagnostics.py \
    --run run_46f8b74e9fb890e8dab8f4f2590db0766bd4a7780515fdd0d291c1afb8cb42c6 --root .
```

新增文件：`project/explanatory_diagnostics.py`、
`project/configs/experiments/momentum_60d_wf_tw_baseline.yml`、
`project/configs/universes/archive/`（parked 定义 + README 交换程序）、
`project/acceptance-1709eddb.yml`、
`project/data/runs/debug/explanatory_diagnostics_46f8b74e/`。
数据集、CURRENT 指针、既有实验产物均未改动。
