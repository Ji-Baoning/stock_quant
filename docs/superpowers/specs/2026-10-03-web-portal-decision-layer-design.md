# Web 门户决策层重设计（策略展示 · 视觉系统 · 结论优先）

- 日期：2026-10-03（同日：v2 按 owner 审核修订；v3 落 owner 八条裁定定稿）
- 状态：**已裁定定稿（v3），可作 S0/S1 实施依据**；未实施
- 范围：`web/` 前端、只读查询服务（`src/stock_quant/service/`）的新只读端点；
  **不新增发布产物、不改发布契约、不改 ADR-021 只读边界、不改 P5 契约 pin I1–I12 的既有字段**
- 关联：`docs/superpowers/plans/2026-10-01-panda-web-portal.md`（现有门户权威规格）、
  `docs/superpowers/specs/2026-09-09-walk-forward-oos-stability-design.md`（逐折展示纪律）、
  ADR-021（常驻查询面）

## 修订记录

- **v3（2026-10-03）**：owner 八条裁定入档（§10 改为裁定记录，无待裁项）：
  S3b 由"待证据"改为**方向性否决**（信任模型理由）；`/reports` 确认下线并入 S1；
  `data/reports/` 确认不入只读服务（信任边界）；ECharts 定案；暗色只留 token 双套；
  挑战裁决最终形态为详情页区块；rights-issue 立即实施、与 S0 并行、第一优先。
  排期按 owner 关键路径重构（§8）：组件库为 S1 硬依赖先行，四页换皮移出关键
  路径，决策台 ①③ 块提前到 rights-issue 落地前交付。复验事实入档：
  `service/tables.py:20` 已 `import pandas as pd`，新端点不扩大依赖面；
  "契约 ≠ 实例"缺口（无已发布实验可供目视核对）由首个正式实验发布闭合。
- **v2（2026-10-03）**：按 owner 审核报告修订。P0-1：纠正"发布并被 web 服务的
  report.html 是 Plotly 富报告"的错误——它是 `_DefaultReport` 迷你表（runner.py:523），
  富报告是 `report build` 写入 `data/reports/` 的展示副本（cli.py:1101-1106）。
  P0-2：纠正"正式实验有单窗口连续回放净值"的错误——正式 walk-forward 实验
  `scenarios=()`（cli.py:1168 注释："A walk-forward experiment plots no scenario
  curves"），净值序列的真实形态是**逐折、逐成本情景**的 `folds/<fold_id>/backtest/
  <scenario>/equity.parquet`，且它**本就是已声明发布产物**（models.py
  `FOLD_ARTIFACTS`/`WALK_FORWARD_ROOT_ARTIFACTS`）。P1：`PerformanceMetrics` 为
  21 字段；区分两套指标口径（单窗口 `PerformanceMetrics` vs walk-forward
  `ScenarioMetrics`）；`display_name` 需改 `ExperimentManifest` 契约（无
  `model_config`，默认 `extra="ignore"`），改为读已发布的 `experiment_spec.yml`
  里的 `hypothesis`。P2：放弃 `results.json` 新产物（会被 `_assert_artifact_tree`
  的"no extra and no missing files"判为完整性错误，属发布契约变更），改为
  **零新产物、只加只读端点**；`/reports` 下线列入待裁定。§10 补问 7、8。
- **v1（2026-10-03）**：初稿。

---

## 1. 背景与问题

Owner 对现有 web 门户（P5 产物）的判定：

1. **美观不足**——视觉上是裸表格 + 26 行手写全局 CSS（`web/src/styles.css` 全文
   26 行，无设计 token、无组件体系、无图表）。
2. **策略展示不足**——没有策略列表的指标列、没有策略注册入口、策略结果只有一个
   "打开静态 HTML 报告"的外链（`ReportsPage.vue` 68 行，本质是链接农场）。
3. **（存疑）没有单个/多个股票/基准的展示**——owner 不确定是否需要。

深层问题一句话：**门户现在是"数据运维面板"，不是"研究决策台"。** 决策者要看的
结论层——稳定性判定、挑战裁决、有没有需要人工处置的事——在 web 上完全不存在。

现状事实（重设计的出发点，v2 已逐条对仓库核验）：

| 事实 | 出处 |
| --- | --- |
| 前端零图表库，运行时依赖仅 vue + vue-router | `web/package.json` |
| `ExperimentSummary` 只有 5 个字段（id/status/dataset/universe/evaluation_reason），无任何指标 | pin I5（`plans/2026-10-01-panda-web-portal.md:43`） |
| **结论层已经是结构化发布产物**：`experiment_manifest.json` 带 `stability_conclusion` / `stability_policy_hash` / `fold_schedule_sha256` / `fold_outcomes_sha256` / `evaluation_reason` / `artifacts`（sha256 映射） | `src/stock_quant/research/registry.py:91` |
| walk-forward 指标口径是 `ScenarioMetrics`（`fold_calendar_return` / `sharpe_zero_rf` / `per_fold_max_drawdown` / `explicit_cost_drag` / `reject_rate` / `turnover`…，聚合块 `aggregate_return` / `annualized_return` / `sharpe_zero_rf`），随 `stability_report.json` 与 `folds/<fold_id>/metrics.json` 发布 | `src/stock_quant/research/walk_forward/metrics.py:113,162` |
| **逐折净值序列是已声明发布产物**：`folds/<fold_id>/equity.parquet`（`trade_date`/`initial_equity`/`net_equity_after_cost`）与 `folds/<fold_id>/backtest/<scenario>/equity.parquet`（`cash`/`market_value`/`net_equity_after_cost`），"publishes for every declared cost scenario of every executed fold" | `src/stock_quant/research/models.py:262,270`；写入 `walk_forward/runner.py:1026` |
| 发布的 `report.html` 是 `_DefaultReport` **迷你表**（scenario/start/end/periods/end_equity/total_return + evaluation 两行），无图；富 Plotly 报告是 `report build` 写 `data/reports/<id>.html` 的展示副本，**刻意**与发布产物路径分开命名 | `src/stock_quant/research/runner.py:523`；`src/stock_quant/cli.py:1101-1106` |
| 正式 walk-forward 实验**没有**逐情景回测树与情景曲线：`scenarios=()`，"A walk-forward experiment plots no scenario curves"；富报告对它也只画基准线 | `src/stock_quant/cli.py:1168-1176` |
| 单窗口口径 `PerformanceMetrics` 共 **21 字段**（含基准 000300.SH 总收益与超额、成本三件套、现金/滞留占比） | `src/stock_quant/analytics/performance.py:116` |
| `data/experiments/` 尚不存在——还没有任何已发布实验，报告页实际是空的 | `project/data/` 实况；唯一 research run（`run_02d25d2a…`）在 backtest 阶段因配股记账失败（日志：`UnsupportedCorporateAction: … rights issue`） |
| 服务是 GET-only、环回、逐请求 pin 版本、错误信封统一 | ADR-021、pin I8 |
| 发布产物树是闭集契约：manifest 声明图之外**多一个文件都是完整性错误**（"no extra and no missing files"） | `src/stock_quant/research/registry.py:433` `_assert_artifact_tree` |

结论（v2 修正后）：**缺的不是数据，也不是结构化结论——结论、指标、逐折净值
全部已在已发布产物里；缺的是一个读它们的只读端点层和一个展示层。** 这比 v1
的判断更有利：S1 可以做到零新产物、零发布契约变更。

---

## 2. 参考平台调研

调研对象按"对本项目的参考价值"排序，每个给出：它展示什么 → 借鉴什么 → 明确不借鉴什么。

### 2.1 WorldQuant BRAIN（alpha 注册表）——对"策略列表"最有价值

**它展示什么**：My Alphas 页是一个注册表清单，每行是一个信号：核心指标列直接
印在列表上（Sharpe、Fitness、Turnover、Returns），每行带**资格判定状态**——
是否通过提交检查（Sharpe>1.25、Fitness>1.0、换手落在区间内、与已有 alpha 的
相关性检查），通过/不通过/已提交的状态工作流一目了然。列表即筛选器：
看一眼就知道哪些 alpha 值得投入下一步。

**借鉴**：**列表列即门槛**。策略列表不只列 id，而是把"判定结论 + 3~5 个关键指标
+ 挑战/资格状态"直接作为列。这与本仓库的语义高度同构——预注册挑战（baseline vs
challenger）+ 稳定性政策（STABLE/UNSTABLE/INCONCLUSIVE）就是 BRAIN 检查的
本仓库版本。列表页应当让 owner 不点开任何一行就能完成"哪些策略值得继续"的初筛。

**不借鉴**：众包/外包属性、IS/OOS 双栏对比（本仓库另有 walk-forward 语义）。

### 2.2 pyfolio / QuantStats（tearsheet）——对"策略详情页"最有价值

**它展示什么**：业界标准的绩效证据文档（tearsheet）：累计收益 vs 基准、水下回撤图
（drawdown underwater）、滚动 Sharpe/Beta/波动、月度收益热力图、Top 回撤期表
（起止/谷底/恢复/时长）、30+ 指标与基准并排成对出现的两列表。核心思想：
**每个指标都和基准成对出现**，结论不靠单边数字。

**借鉴**：详情页骨架 = 结论横幅 → 指标卡 → 曲线图 → 逐折明细表 →
持仓/执行证据 → 溯源条。指标卡"与基准并排"保留为设计意图（本仓库 walk-forward
口径下以"成本前/成本后成对"与"逐折分布"承担同一职责，见 §5.3b）。

**不借鉴**：跨期拼接的全局资金曲线。**本仓库明确禁止**（walk-forward 设计
`2026-09-09` 第 121 行：`stability_report.json` 严禁包含跨 fold 拼接收益的回撤/
Calmar/路径指标）。v2 进一步明确：正式实验**不存在**任何单窗口全期策略净值
（`scenarios=()`），所以详情页的曲线只能是**逐折净值**（见 §5.3c）。

### 2.3 QuantConnect（云平台回测结果页）

**它展示什么**：回测列表（每行带 Sharpe/年化/回撤）；结果页分页签：统计网格
（Sharpe/Sortino/Alpha/Beta/信息比率/跟踪误差/胜率/盈亏比/费用）、净值 vs 基准、
回撤、滚动统计、月度收益表、敞口与持仓；另有独立的"Report"（章节化 HTML）。

**借鉴**：应用内结果页 + 完整静态报告并存的双层结构——本仓库的对应物是：
web 策略页（新）+ `report build` 富报告（既有 CLI 展示副本），两者**刻意分工**
（发布产物路径不被工程诊断污染），web 设计要尊重这个分工而不是消灭它。

**不借鉴**：云 IDE/实盘交易界面；滚动统计暂缓（见 §6.3 远期）。

### 2.4 聚宽 / 米筐 / BigQuant（A股平台）

**借鉴**：指标卡布局（大数字 + 小标签的 KPI 网格）；净值与基准同图对比；
**红涨绿跌**颜色惯例；基准默认 000300.SH（`PRIMARY_BENCHMARK_SYMBOL` 已如此）。

**不借鉴**：在线写策略的 IDE；模拟盘/实盘跟随。

### 2.5 Portfolio Visualizer（组合分析）

**借鉴**：Top 回撤期表（每次回撤的起点/谷底/恢复点/时长/深度）——把"最大回撤"从
一个数字变成可核查的事件列表。远期增强（依赖引擎侧新增逐段回撤产物，本期不做）。

### 2.6 MLflow（实验追踪）

**借鉴**："多实验对比"视角。本仓库实验量级小，列表排序 + 详情对比已够用；
对比页列入远期 backlog。

### 2.7 调研总结：三个共同模式

1. **列表带结论**：清单页直接印判定状态与关键指标（BRAIN、QuantConnect）。
2. **详情是证据文档**：tearsheet 结构，指标成对、图表分层（pyfolio/聚宽）。
3. **状态工作流**：注册 → 运行 → 判定 → 采纳/淘汰，每个状态有明确视觉语义。

本仓库三个模式的原材料**已全部存在于已发布产物**（manifest 结论、stability
指标、逐折净值、状态机），且 v2 核验确认它们都在 `_assert_artifact_tree` 的
声明图内——web 缺的只是端点与展示。

---

## 3. 设计原则

1. **结论优先（verdict-first）**：每个页面第一屏回答一个决策问题。策略详情页第一眼
   是稳定性结论，不是原始数据表。
2. **证据可溯**：页面上每个结论数字可追到产物哈希（`experiment_manifest.artifacts`
   的 sha256 声明图、`stability_policy_hash`、三个快照哈希），延续既有记账。
3. **零新产物、零计算**：S1 不新增任何发布产物（发布树是闭集契约）；所有指标与
   结论逐字来自已发布 JSON/Parquet。前端只允许对**单条已发布净值序列**做归一化
   与逐点回撤这类"同一序列的另一种画法"的渲染变换；禁止跨序列计算、拼接与
   指标派生。与 pin I6"前端不重建报告"同一条纪律。
4. **术语即项目术语**：STABLE/UNSTABLE/INCONCLUSIVE、门禁、accepted record、
   UNTRUSTED——沿用现有词汇，不发明"推荐/强势/利好"类投资建议语言。
5. **遵守既有裁定与禁令**：pin I1–I12 全部不动（只新增端点）；逐折展示纪律
   （禁止跨折拼接曲线）是图表设计规则；单日表预览（pin I4 裁定 a）不变——
   基准曲线用**新的专用端点**取数，不改预览端点的参数契约。
6. **空态诚实**：注册表为空时明说"尚无已发布实验"并给出到达路径；产物图里
   没有的东西，页面显示"未发布该产物"，不造数据。

---

## 4. 信息架构重组

现状导航（顶栏平铺 5 项）：版本面板 / 数据预览 / 质量证据 / 更新任务 / 报告。

新导航（左侧边栏分组，顶栏保留全局状态）：

```
┌ 顶栏：品牌 · 项目指纹 · resolved version + CURRENT + 刷新提示（语义不变）
├ 概览
│   └ 决策台                    /            （新增）
├ 策略
│   ├ 策略列表                  /strategies   （新增，吸收 /reports）
│   ├ 策略详情                  /strategies/:id （新增）
│   └ 注册台                    /strategies/register （S3）
├ 数据
│   ├ 版本面板                  /versions     （改版）
│   ├ 数据预览                  /preview      （改版）
│   └ 质量与覆盖证据            /evidence     （改版）
└ 运维
    └ 更新任务                  /jobs         （改版）
```

- **`/reports` 确认下线（owner 裁定 7，2026-10-03），并入 S1 执行**：它现在交付的
  是迷你摘要表（见 §1），是误导而非入口。下线是对 P5 §10.1 冻结页面清单的修改，
  以本裁定为准（P5 计划文档保留为历史记录）。报告可用性探测（pin I6）语义不变，
  迁入策略详情页作为报告入口动作；ReportsPage 的既有测试断言随 S1 迁移。
- 顶栏的版本 pin / CURRENT 语义、复制哈希、新版本提示等行为全部保留。
- 侧边栏在窄屏折叠为顶栏下拉（app-shell 测试更新分组断言）。

---

## 5. 页面设计

### 5.1 决策台 `/`（新增；v3 拆期：块①③ 先行交付，块② 随首个正式实验）

回答决策者每天的三个问题，自上而下三块：

**① 数据现在可信吗？**——CURRENT 版本卡：创建时间、门禁结论（读 acceptance.state，
沿用现有四态拆分语义）、有效 accepted record、质量问题计数、"有新版本未切换"提示。
数据来自既有 `GET /api/v1/datasets`，无新端点。

**② 策略结论是什么？**——最新已发布实验卡：结论徽章（STABLE 绿 / UNSTABLE 红 /
INCONCLUSIVE 黄）、一句话裁决摘要（`evaluation_reason` + 通过折数/总折数 +
`stability_policy_hash` 前 8 位）、成本后收益与成本拖累（`net_return` /
`explicit_cost_drag`）。无实验时空态：明示 `data/experiments` 为空，并给出路径
（注册台 → CLI research run → 发布）。

**③ 需要我做什么？**——行动列表，每项一行、带跳转与 RUNBOOK 链接：
- 存在 PENDING_CONFIRMATION 的验收（链接到版本详情；**确认操作仍不在 web**，
  行动项给出 `acceptance confirm` 的等价 CLI，沿用现有边界）；
- 最近失败的 research run（读 run 目录 `.stages.json` 的终态——注意这是运维读，
  归操作面/本地产物，端点归属见 §7.2 注）；
- 运行中的 update job（复用现有 jobs 数据）；
- 无行动项时显示"无待办"，而不是空白。

决策台主要块由前端组合既有端点构成；"失败 run"块若需新端点则降级为可选块。

### 5.2 策略列表 `/strategies`（新增，S1；BRAIN 模型）

一行 = 一个已发布实验。列设计（v2：全部字段来自已发布产物，指标列是
**walk-forward `ScenarioMetrics` 口径**，不是单窗口 `PerformanceMetrics`）：

| 列 | 内容 | 来源（已发布产物） |
| --- | --- | --- |
| 策略 | `hypothesis` 截断 + experiment_id 前 8 位（悬浮全哈希+复制） | `experiment_spec.yml`（根产物，`spec.py:159`）；id 来自 manifest |
| 结论 | 徽章 STABLE / UNSTABLE / INCONCLUSIVE（INCONCLUSIVE 附 `evaluation_reason`） | `experiment_manifest.json: stability_conclusion` |
| 成本后收益 | `net_return`（canonical 情景聚合），红涨绿跌 | `stability_report.json` 聚合块 |
| 成本拖累 | `explicit_cost_drag` | 同上 |
| Sharpe（零无风险） | 聚合 `sharpe_zero_rf` | 同上 |
| 回撤（逐折） | `per_fold_max_drawdown` 的最差值（标注"逐折最差"） | 逐折 metrics / stability 分布统计 |
| 拒单率 / 换手 | `reject_rate` / `turnover` | 同上 |
| 数据版本 | 短哈希，链接到版本详情 | manifest `dataset_version` |
| 状态 | ACCEPTED / REJECTED | manifest `status` |

- 具体列名在实施 Task 1 与 `stability_report.json` 实际序列化结构逐字段对账
  （沿用 P5"契约是权威、web 是消费者"的对账纪律）；上表字段名以
  `walk_forward/metrics.py:113,162` 的既有命名为准，不新造。
- 支持按结论/状态过滤、按指标排序（纯前端，注册表量级小）。
- **挑战裁决不进列表列，最终形态为详情页区块**（owner 裁定 5，2026-10-03：
  一次性配对比较，独立页是过度建设）。消费挑战产物需要新只读端点与一次性持有集
  （holdout）消费状态的展示语义，作为 S1 之后的增量另行立项；S1 不触碰挑战产物。
- **空态是一级场景**：见 §5.1 ②。
- 数据来源：新增聚合端点 `GET /api/v1/experiments/summaries`（§7.2），
  一次取回全部行，避免逐行 N+1；pin I5 的原端点原样保留。

### 5.3 策略详情 `/strategies/:id`（新增，S1；tearsheet 骨架 + 仓库纪律）

**页面按 manifest.artifacts 声明图自适应**：正式 walk-forward 实验与遗留单窗口
实验的产物集不同，页面渲染声明图里**实际存在**的部分，缺的显示"未发布该产物"。

**a. 结论横幅**——`stability_conclusion` 徽章 + `evaluation_reason` +
通过折数/总折数 + 政策摘要与 `stability_policy_hash`；数据验收绑定
（`data_acceptance_id`）、三个快照哈希、UNTRUSTED/工程验证信任语义沿用。

**b. 指标卡网格（按实验形态分两套，不混口径）**——
- **walk-forward（正式）**：`ScenarioMetrics` 口径——canonical 情景聚合
  （`aggregate_return` / `annualized_return` / `annualized_volatility` /
  `sharpe_zero_rf` / `oos_return_observations`）+ 成本对（`gross_return_before_
  explicit_cost` vs `net_return` vs `explicit_cost_drag`）+ 逐折分布
  （`per_fold_max_drawdown` 最差/中位、`reject_rate`、`turnover`、
  `explicit_cost_ratio`）。字段逐字来自 stability_report / 逐折 metrics.json，
  前端不派生（归一化展示除外）。
- **单窗口/工程（若声明图含逐情景回测树）**：`PerformanceMetrics` 21 字段口径，
  与基准成对（`benchmark_total_return` / `benchmark_excess_return`）。
  v1"指标卡与 PerformanceMetrics 一一对应"的验收仅适用于这一形态。

**c. 图表区（ECharts；v2 重设计：正式实验只有逐折曲线）**——
- **逐折净值**：折选择器（fold 下拉/翻页）× 情景页签；每折画
  `net_equity_after_cost`（以 `initial_equity` 归一化，属渲染变换）+
  同窗基准收盘（§7.2 基准端点，灰色虚线同图）。
- **逐折水下回撤**：同折同情景，逐点回撤渲染。
- **逐折分布条形图**：每折 `fold_calendar_return` / `per_fold_max_drawdown` /
  `sharpe_zero_rf`，失败折打叉并注 `reject_rate`。
- **单窗口形态（仅工程/遗留）**：`daily_equity.parquet` 单窗口净值 + 水下回撤。
- **设计禁令（不变，且 v2 后更严）**：不拼接任何跨折曲线；正式实验不渲染
  "策略全期净值"（该数据不存在）；曲线只来自已发布 Parquet 的原样序列。

**d. 明细表区**——逐折表（fold_id / 窗口 / 状态 / `fold_calendar_return` /
`per_fold_max_drawdown` / `reject_rate` / `turnover` / `explicit_cost_ratio` /
`sharpe_zero_rf`）、成本情景对比表、`portfolio_construction.parquet` 的期末
组合快照（head N，原样列）。

**e. 溯源条**——experiment_id / dataset_version / universe_version（+
universe_id / membership_table_sha256）/ code_commit / 三快照 sha256 /
`fold_schedule_sha256` / `fold_outcomes_sha256` / `stability_policy_hash` /
`artifacts` 声明图计数，mono 短哈希 + 复制。

**f. 报告入口（v2 修正语义）**——"打开已发布报告"链接 pin I6 端点，**如实标注
这是摘要表**（`_DefaultReport`：情景行 + evaluation 状态，无图）；富报告
（Plotly tearsheet）是 `report build` 的 CLI 展示副本（`data/reports/<id>.html`），
**不入 web 服务（owner 裁定 3，2026-10-03）**——`cli.py:1100` 的注释明说富报告
刻意路径分离，为的就是"工程诊断绝不能被从已发布产物的路径上读出来"；把它接进
只读服务等于亲手拆掉这道信任隔离。页面给出等价 CLI 命令而不是假链接。

### 5.4 注册台 `/strategies/register`（S3，两阶段）

"注册"在本仓库语境 = 登记假设与参数、冻结 spec、发起可复现 run。

**S3a（无写面，纯前端生成）**：模板选择 → 参数表单（必填校验对齐
`research/spec.py` 冻结字段，含 hypothesis 非空）→ 实时生成可复制的
`python -m stock_quant research run …` 完整命令 + 等价 spec YAML 预览。
执行仍在 CLI；不可能绕过 CLI 的幂等与验收门禁。

**S3b（已否决，owner 裁定 2，2026-10-03——方向性否决，不是"等证据"）**：操作服务
**不**新增 `POST /api/v1/research-jobs`，未来也不再有此待裁项。理由（owner 原文
要义）：`research run` 是长时、发布、持锁的操作，本仓库的信任模型就是"人对一条
冻结命令负责"——加一个浏览器按钮去启动发布型 run，牺牲的正是这个可审计性，
而换来的便利目前没有任何使用证据支撑。S3a（生成命令 + YAML 预览，零契约成本）
保留：它本身就在强化"人跑冻结命令"。

### 5.5 股票/基准展示（owner 问题三）——建议：基准进策略页，个股终端缓做

- **基准对比立即做（owner 裁定 1：接受推荐，2026-10-03）**：基准（000300.SH）以
  "逐折同图对比 + 专用基准端点"的形式
  进策略详情页（v2：数据走 §7.2 新基准端点读已发布数据集版本，不依赖任何
  单窗口策略净值——P0-2 修复后此路成立）。
- **独立个股/多股终端（K线、多股对比）本期不做**，理由：
  1. pin I4 裁定 (a)：表预览只有单日 `trade_date` 过滤，画个股时间序列要改 P3
     冻结契约，owner 已明确"推迟到真实使用提出诉求"；
  2. 决策者的缺口在"策略层结论"，不在个股行情；
  3. K 线终端要正确处理复权与除权标注，工程量大且与"决策证据"目标无关。
- **折中（若 owner 仍想要）**："标的档案页"（单日快照 + 该 symbol 质量问题 +
  覆盖段 + 成分归属，全部现有数据）；或走 P3 变更解除 I4(a) 后做个股曲线。
- **universe 浏览器优先于个股终端**：成分快照（per signal day 成员与快照哈希）
  是本仓库特有的 PIT 证据，列为 S4 候选。

### 5.6 既有四页改版（S0）

信息与行为不变（语义、testid、诚实原则全部保留），只做视觉迁移：卡片化布局、
语义徽章、表格排序/粘性表头、加载骨架、空态组件。更新任务页仅换皮。
版本详情页的 `details` 原始 JSON 改为键值化展示（仍是只读透传）。

---

## 6. 视觉设计系统

### 6.1 设计 token（`web/src/styles/tokens.css`，CSS 自定义属性）

- **色板**：中性 8 级 + 主色（品牌蓝系）+ 语义色：pass（绿）/ block（红）/
  pending（黄）/ unstable（红）/ inconclusive（黄）/ 涨跌（**红涨绿跌**）/
  benchmark（灰）。
- 双套主题变量（light 默认；dark 预留结构，本期不做切换——§10 问 4）。
- 间距 4px 基阶；圆角与阴影两档；正文 14、KPI 大数 28/36，数值统一
  `font-variant-numeric: tabular-nums`。
- 字体：中文系统栈 + 等宽（哈希/代码/数值列）。

### 6.2 组件清单（`web/src/components/`）

`Card`、`KpiCard`（数值+标签+对照小字）、`StateBadge`（四态验收/三态结论/
任务状态，统一语义映射表）、`DataTable`、`ChartCard`、`EmptyState`
（标题+原因+下一步动作链接）、`ProvenanceStrip`、`Skeleton`。

风格取向：**专业终端的信息密度 + 现代 SaaS 的层级与留白**，不做营销风装饰。

### 6.3 图表规范（ECharts 按需引入）

- 引入：`echarts/core` + Line/Bar + Canvas 按需注册，gzip 后约 100KB 量级，
  是唯一新增运行时依赖（不引 Vue-ECharts，薄封装 `LineChart.vue`/`BarChart.vue`）。
- **ECharts 定案（owner 裁定 3，2026-10-03）**：服务路径上本就没有 Plotly，无
  "统一"可言；富报告刻意不进 web 服务路径，两者数据同源（已发布产物），不存在
  "两个真相"。代价（两处图表实现并存）已裁定接受。
- 规范：红涨绿跌；基准灰虚线；水下回撤绿面积朝下；失败折打叉；数值轴统一
  右对齐（% / bp）；色盲友好（涨跌另有正负号/位置冗余）。
- 远期（不承诺）：月度收益热力图、滚动 Sharpe/Beta、Top 回撤期表。

---

## 7. 数据与契约方案（v2：零新产物，只加只读端点）

### 7.1 原则：不新增发布产物

v1 的 `results.json` sidecar 方案**作废**。理由（审核确认）：发布产物树是闭集契约
（`_assert_artifact_tree`："no extra and no missing files"，registry.py:433），
新增产物必须进 `artifacts` 声明图与哈希记账，属发布契约变更；且 manifest/
metrics/stability/逐折 metrics 已覆盖结论与指标，sidecar 会成为第三份可漂移副本。
唯一 v1 想要而发布产物里没有的东西——**图表序列**——核验后发现也已发布
（`folds/<fold_id>/backtest/<scenario>/equity.parquet`，models.py:270 注释明说
逐情景日净值是给策略挑战保留的可审计产物）。

### 7.2 新增只读端点（GET-only、环回、错误信封 pin I8；发布产物原样读）

| 端点 | 读什么（已发布产物） | 说明 |
| --- | --- | --- |
| `GET /api/v1/experiments/{id}/results` | `experiment_manifest.json` + `metrics.json` + `stability_report.json` + 逐折 `metrics.json` 的**服务端只读聚合**（先例：`/api/v1/datasets` 聚合 manifest） | 详情页主数据；manifest 缺某产物（如非 walk-forward 无 stability_report）时对应键为 null，前端显"未发布" |
| `GET /api/v1/experiments/{id}/folds/{fold_id}/equity?scenario=` | `folds/{fold_id}/backtest/{scenario}/equity.parquet`（canonical 情景默认） | 逐折净值序列 JSON 化（原样行读，同表预览模式）；fold/scenario 必须在 manifest `artifacts` 声明图内，否则 404 |
| `GET /api/v1/experiments/summaries` | 各实验 manifest + stability 聚合 | 列表页一次取回；无 stability 的实验给 null 并标"无 walk-forward 结论" |
| `GET /api/v1/datasets/{v}/benchmark?start&end` | 该已发布数据集版本的基准表行（000300.SH 收盘） | 基准同图用；**新专用端点带窗口参数**，不改 pin I4(a) 的表预览契约 |

不变的：`/api/v1/experiments`（pin I5）、报告探测（pin I6）、错误信封（pin I8）、
GET-only 与环回（ADR-021）、双服务拆分（pin I12）、发布契约与产物树（零改动）。

实施纪律：Task 1 先与 `stability_report.json` / `metrics.json` / 逐折
`metrics.json` 的实际序列化结构逐字段对账（P5 的"契约是权威、web 是消费者"），
差异修 `types.ts` 不修产物。

依赖面核验（owner 复验，2026-10-03）：`service/tables.py:20` 已
`import pandas as pd`——只读服务本来就在读 parquet（数据集表预览），新端点不
扩大依赖面；ADR-021 的"服务不得引入 research/data_pipeline 依赖"边界不受影响
（新端点只读已发布产物，不 import research）。

### 7.3 展示名与假设的来源（v2 修正）

`hypothesis` 从**已发布**的 `experiment_spec.yml`（根产物）读取，零契约变更。
`display_name` 如需引入，必须给 `ExperimentManifest` 增字段（现模型无
`model_config`，pydantic 默认 `extra="ignore"`，写进去读不到）——属发布产物
契约变更，S1 不做，列入 backlog。experiment_id 不受影响（id 来自
`ExperimentIdentity.of(spec, snapshots)`，与 manifest 字段无关——审核已确认）。

### 7.4 前端渲染变换的边界

允许：对单条已发布净值序列做归一化（除以 `initial_equity`）与逐点回撤——
这是同一序列的另一种画法。禁止：跨序列计算、跨折拼接、任何新指标派生
（回撤/Sharpe 等数字一律读已发布 metrics）。

---

## 8. 落地排期（v3：按 owner 关键路径重构，2026-10-03）

**关键路径**：

```
S0a 组件库（tokens + DataTable/StateBadge/EmptyState/KpiCard + 布局壳）
   ├─→ S2①③ 决策台前半（立刻有价值：不依赖 rights-issue）
   └─（换皮随后补，不占关键路径）→ S0b 四页迁移

rights-issue（第一优先，与 S0 并行）→ 首个正式实验发布
   ├─→ S1 策略层（契约→实例验证闭合）→ S2② 决策台后半（策略结论卡）
   └─→ 挑战裁决区块（详情页区块，增量立项）→ S3a 注册台 → S4（默认缓）
```

排期要点（owner 裁定展开）：

- **组件库是 S1 的硬依赖，四页换皮不是**——先建 tokens 与核心组件，换皮随后补。
- **决策台拆两半**：块①（数据可信吗）③（需要我做什么）零新后端、只聚合现有
  datasets/jobs，是整套设计里唯一在 rights-issue 落地前就能产生价值的页面，
  紧随组件库交付；块②（策略结论卡）等首个正式实验。
- **rights-issue 立即、与 S0 并行、第一优先**：它是唯一的排期答案——其余都是
  范围选择不改时间线，只有它决定"S1 能不能被真正验证"。如实保留 owner 的
  限定——**契约 ≠ 实例**：逐折产物闭集是契约层核验（`FOLD_ARTIFACTS` /
  场景产物集 / `_is_admissible` 三处一致），但 `data/experiments` 为空、没有
  任何一个已发布实验的 `fold_manifest.json` 可供目视核对；这条缺口只能由第一个
  成功的正式实验闭合，端点的"只服务声明图内文件、越界 404"护栏为此预设。

| 期 | 内容 | 验收标准（关键项） |
| --- | --- | --- |
| **S0a 组件库（先行）** | tokens + DataTable/StateBadge/EmptyState/KpiCard + 侧边栏布局壳 | 组件级 vitest；无端点/契约变更；无新增运行时依赖（ECharts 并入 S1） |
| **S2①③ 决策台前半** | `/` 块①（CURRENT 数据可信卡）+ 块③（行动列表） | 空态/无待办态可测；行动项每条有跳转；不出现 web 内验收操作 |
| **S0b 四页换皮（随后补）** | 版本/预览/证据/任务页迁移到组件库 | 既有 vitest/e2e 断言语义全部通过（testid 不删） |
| **S1 策略层（核心）** | §7.2 四个新 GET 端点；策略列表/详情页（含图表）；**`/reports` 下线（裁定 7）** | **零发布产物变更**（diff 证明 `research/` 产物契约无改动）；端点响应与已发布 JSON 逐字段对账测试；fold equity 端点仅服务声明图内文件（越界 404 测试）；walk-forward 页面**无任何跨折拼接曲线、无"全期策略净值"图**（测试断言）；指标卡字段与 `ScenarioMetrics`（WF）/`PerformanceMetrics`（单窗口）各自一一对应（快照测试，两口径不混）；ReportsPage 断言迁移完成；pin I5/I6/I8 原测试不变绿 |
| **S2② 决策台后半** | `/` 块②（最新实验结论卡） | 无实验空态引导；结论徽章/指标来自端点聚合 |
| **挑战区块（增量）** | 详情页挑战裁决区块（裁定 5 形态） | 消费端点立项时定验收；S1 不触碰挑战产物 |
| **S3a 注册台** | 命令 + spec YAML 生成器 | 生成命令与手工 CLI 等价（参数对拍测试）；**无任何写面（裁定 2）** |
| **S4 股票/基准（默认缓）** | universe 浏览器 / 标的档案页 / （解除 I4(a) 后）个股曲线 | 待真实使用诉求另立设计小节 |

S1 上线初期策略页将长期处于空态——空态引导（注册台/RUNBOOK 路径）是一级场景，
S3a 的实际价值因此不低。

---

## 9. 与既有决策/不变量的关系（v2 修正表述）

| 既有决策 | 本设计 |
| --- | --- |
| pin I1–I12（P5 契约） | 全部不动；`types.ts` 只增不改；新端点独立成组 |
| pin I4 裁定 (a) 单日预览 | 不动；基准曲线走新专用端点，不改预览参数契约 |
| pin I6 前端不重建报告 | 延伸为 §7.4 的"零计算/零派生"边界 |
| ADR-021 只读面 | 不动；四个新端点全部 GET、环回、只读聚合已发布产物 |
| **发布产物闭集契约**（`REQUIRED_ARTIFACTS`/`_assert_artifact_tree`/fold 声明集） | **零改动**——v2 方案不新增任何产物（v1 的 results.json 方案因此作废） |
| P5 §10.1 页面清单 | `/reports` 下线已裁定（裁定 7，2026-10-03）并入 S1；以本裁定为准，P5 计划保留为历史记录 |
| 发布面新增写端点（research-jobs 类） | **方向性否决**（裁定 2）：发布型 run 不进浏览器按钮，"人对一条冻结命令负责"是信任模型本体 |
| `data/reports/` 富报告 | **不入只读服务**（裁定 3）：保持"工程诊断不可从发布产物路径读出"的刻意隔离 |
| walk-forward 逐折纪律 | 图表级禁令：禁止跨折拼接；正式实验无全期策略净值（数据本就不存在） |
| "工程证据非投资结论" | 信任语义进结论区第一屏 |
| 现有页面测试语义 | S0 只换皮不换语义；testid 保留 |

---

## 10. Owner 裁定记录（2026-10-03，八条，无待裁项）

| # | 问题 | 裁定 | 理由（owner 原文要义） |
| --- | --- | --- | --- |
| 1 | 个股/基准 | 接受推荐：基准进策略页，个股终端缓做 | 基准行端点已在 v2 内；个股要改 pin I4(a)，且决策缺口在策略层不在行情层 |
| 2 | S3b 操作面 POST | **方向性否决**——不是"等证据"，是不做 | 写面侵蚀的不只是边界，是"人跑冻结命令"这个信任设计本身 |
| 3 | 图表库 / `data/reports/` | ECharts；`data/reports/` **不入**只读服务 | 服务路径上本来就没有 Plotly，无"统一"可言；服务诊断 HTML 会打破刻意的信任隔离 |
| 4 | 暗色模式 | 只留 token 双套，不做切换 | 零使用证据，切换是纯测试面积 |
| 5 | 挑战页 | 详情页区块，不独立页 | 一次性配对比较，独立页是过度建设 |
| 6 | rights-issue | **立即，与 S0 并行，第一优先** | 同时卡住首个正式实验与 S1 的契约→实例验证闭合；这是唯一的排期答案，其余七问是范围选择不改时间线 |
| 7 | `/reports` 下线 | 确认下线，并入 S1 | 它现在交付的是迷你表（P0-1），是误导而非入口 |
| 8 | 放弃 `results.json` | 确认放弃 | 已发布产物已含全部证据；新增即改闭集契约 |

问 6 的份量（owner 展开要义）：契约层已核验（`FOLD_ARTIFACTS` / 场景产物集 /
`_is_admissible` 三处一致 + 注释承诺），实例层为空——`data/experiments` 没有
任何一个 `fold_manifest.json` 可供目视核对。**契约 ≠ 实例**，这条缺口只能由
第一个成功的正式实验闭合。因此它不是"要不要做"，是"什么时候做"：立即。

## 参考资料

- WorldQuant BRAIN（alpha 注册表与提交检查）：support.worldquantbrain.com
- QuantConnect 回测结果与报告文档：www.quantconnect.com/docs/v2/our-platform/reports/backtest-reports
- pyfolio tearsheet：github.com/quantopian/pyfolio
- QuantStats（指标并排基准的 HTML 报告）：github.com/ranaroussi/quantstats
- 聚宽（A股回测结果页惯例）：www.joinquant.com
- Portfolio Visualizer（回撤期表/滚动收益）：www.portfoliovisualizer.com
- MLflow（实验对比视角）：mlflow.org
