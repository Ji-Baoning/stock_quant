# 量化实验报告 —— 时点成分过滤可用化（custom_csi300_ic_tradable）ENGINEERING 诊断

**执行日：** 2026-09-11
**代码版本：** `bcc60e0`（分支 `feasibility_enhancement`；实验快照 `code_commit = 3ac2547`）
**实验 id：** `2b165500b6aebd4121badbbd81e983f5e2627751c897b0085b4b551cbdbbd1ce`
**运行目录：** `project/data/runs/debug/2b165500…`
**对照基线：** `ab378f9d…`（同策略、无 `universe_definition`、旧数据集，见
`docs/operations/2026-09-11-momentum60d-extended-diagnostic.md`）
**规格：** `project/configs/experiments/momentum_60d_pit_tradable.yml`

> **信任等级：UNTRUSTED。** 本文全部数字是**工程诊断**，不是可信绩效声明，
> 不构成投资建议，也不得作为任何策略有效性的证据。理由见 §1；
> 幸存者偏差仍然存在，见 §2。

---

## 0. 结论摘要

| 项 | 值 |
| --- | --- |
| 规格 | `configs/experiments/momentum_60d_pit_tradable.yml` |
| 数据集 | `38c52358…`（由 `af5799ae…` 裁剪重发，`daily_bar` 逐字节未变） |
| 时点池 | `custom_csi300_ic_tradable`，定义版本 `6405c180e810c2c1…` |
| 窗口 | 2015-01-09 ~ 2026-08-24，2,825 个交易日，595 个信号日 |
| 信号 | 周频 momentum_60d，等权 Top-10，`lot_size=100`，初始资金 100,000 |
| 逐日快照 | 595 个信号日的成员快照全部非空，信号日成员数 **min 14 / max 28** |
| 预检门禁 | `evaluate_index_membership_evidence(..., master=…)` 0 FATAL，`enforce_required_results` 复核通过 |
| 基准情景（`full_cost`） | 累计 **+81.64%**（基线 +81.29%），年化 5.47%，波动 10.10%，最大回撤 −24.68% |

**一句话：** 冻结的 CSI300 成分事实终于能驱动实验了——预检通过、595 个
信号日逐日快照非空、成员数逐年从 14 涨到 28 证明过滤真的在按信号日裁；
对照未过滤基线，三情景累计收益变化都在 ±1.4 个百分点内，但**这不是绩效
结论**（UNTRUSTED，幸存者偏差仍在），它验收的是**链路可用**。

---

## 1. 信任等级：UNTRUSTED，与 `status=REJECTED` 的关系

1. **无 ACCEPTED 数据验收记录。** 本次运行 `data_acceptance.status = UNVERIFIED`
   （`acceptance_id = null`），和基线运行一样。数据验收链是独立方案，
   不在本工作范围内；因此本运行只能走 ENGINEERING 诊断通路
   `backtest momentum_60d … --engineering`。
2. **`experiment_manifest.json` 的 `status = REJECTED` 是这一语义的体现，
   不是时点过滤失败。** 裁决理由（`evaluation_reason`）与基线完全同源：
   corporate action trust 下 **30/30 只可能持仓**在 2015-01-09 ~ 2026-08-24
   全窗口 `COVERAGE_INCOMPLETE`，公司行为覆盖不足使数据集不可信。
   这与 UNTRUSTED 一致，属于 ENGINEERING 诊断的预期裁决；本次改动
   （换 `universe_membership` 表）与该裁决无关。
3. **universe 预检本身是通过的。** 用真实 `security_master` 边界调用
   `evaluate_index_membership_evidence(..., master=boundaries)` 得 0 issue、
   0 FATAL，并用生产执行器 `enforce_required_results` 复核通过。对照 §5 的
   背景：未裁剪的 948 只池子会在这道门禁上被逐符号判 FATAL
   `UNIVERSE_UNKNOWN_SYMBOL` 且无旁路——**本次运行跑通本身就是"可用化"的验收**。

---

## 2. 幸存者偏差仍在：这不是无偏 CSI300

**明确声明：本工作不消除幸存者偏差。**

- 池子是**人工挑选、至今仍在市**的 30 只（裁剪后 28 只）。它们 2015 年初
  就被选进了观察池，而挑选的依据包含了后见之明。
- 本工作裁掉的是**池内前视偏差**：此前若把全部 948 只成分事实喂给选股器，
  信号日会选中"当时还不是成分、但数据集里恰好有行情"的标的；时点过滤把
  每个信号日的候选收窄到**当时确实是 CSI300 成分**的标的上。
- 因此 `custom_csi300_ic_tradable` **不得被当作 CSI300**，任何基于它的
  回测也不能宣称"CSI300 增强"。无偏池需要约 918 只额外标的自 2015 年起
  的行情，属外部数据获取，是另一方案的事。
- 定义文件头部已永久写明这一告诫（`NOT an unbiased CSI300 universe …
  survivorship bias remains`）；派生池占用 `custom_` 前缀、不占用 canonical
  `csi300`，且有单测护栏。

---

## 3. membership 裁剪：前后对照

| 项 | 裁剪前（`custom_csi300_ic`） | 裁剪后（`custom_csi300_ic_tradable`） |
| --- | --- | --- |
| membership 行数 | 1,221 | **31** |
| 唯一标的数 | 948 | **28** |
| `status` 分布 | — | 28 `active` / 3 `removed` |
| `reason` 分布 | — | 25 `regular_rebalance` / 6 `initial_constituent` |

- 裁剪规则：逐行取 membership 与 `security_master`（30 只）的**符号交集**，
  并把每行的 `universe_id` 重贴为 `custom_csi300_ic_tradable`
  （`UniverseResolver` 要求 resolved fact 携带定义自身的 `universe_id`）。
- `custom_csi300_ic` 的定义文件与 facts **原样保留**——那是忠实记录，
  留给未来的无偏池工作。
- master 30 只中有 2 只**从未入选** CSI300，被排除在 28 只之外：
  **`300001.SZ`、`688001.SH`**（master 在册、无任何成分事实行，排除原因
  是"master 在册但从未入选 CSI300"，不是数据缺失）。
- 3 条 `removed` 行的 reason 全部是 `regular_rebalance`，不含终止类事件，
  规格标记的风险 `UNIVERSE_DELISTING_ENDPOINT_UNPROVEN` 实测不触发。

---

## 4. 新数据集版本、定义与 hash 链

| 项 | 值 |
| --- | --- |
| 旧数据集版本 | `af5799ae4e62f94210e6751473fed8e14e38252fd03613baff4f6bb3af4a6b70` |
| 新数据集版本 | `38c5235886030d8f0ce83c011ce0684046c02e82da4ab6c9c5c4b47995a435cf`（CURRENT 已推进） |
| `membership_table_sha256` | `756db257b6a13846c4452fd2e7504c54636705ca4f6acb4b712d092b549a4255`（裁剪 + 重贴 `universe_id` 后） |
| 定义文件 | `project/configs/universes/custom_csi300_ic_tradable.yml` |
| definition version | `6405c180e810c2c1…`（manifest 记录全值 `6405c180e810c2c11ca3ce34d362aea7e5d022ddfb71e1a7b059e677faca55f4`） |
| `rules_version` | `index_constitution-1.0.0+repairs-07e2f18d+tradable` |
| coverage | 2015-01-05 ~ 2026-08-28（取自 `daily_bar` 实际覆盖） |
| `evidence_summary_sha256` | 继承自基础定义（`cd16f00f…`），证据链不断 |

- **`daily_bar` 逐字节未变。** 裁剪只替换 `universe_membership` 表，行情、
  复权口径、交易日历都不动；数据集版本改变纯粹是**内容寻址**对表内容变化
  的如实反应，不是"换了一批行情"。
- **`universe_version: CURRENT` 的含义**：与 `dataset_version: CURRENT` 一样，
  它只是冻结前的占位符——runner 在计算实验身份前把它**一次性**解析成定义的
  内容寻址版本并钉进冻结规格与 manifest（本次即 `6405c180…`），落盘的规格
  永远不含 `CURRENT`。此后定义文件再变，也不会悄悄改变已发布实验的身份。
- 基线规格 `momentum_60d_offline_real_extended.yml` 无 `universe_definition`、
  不读 membership 表，且其 `daily_bar` 未变，因此基线结果不受本次重发影响。

---

## 5. 过滤语义证据：成员数确实随信号日变化

- 语义探针（按信号日解析成员数）：
  `2015-06-01: 14`、`2018-06-01: 17`、`2020-06-01: 18`、`2022-06-01: 26`、
  `2024-06-01: 26`、`2026-06-01: 28`——逐年从 14 涨到 28，说明过滤在按
  信号日裁，而不是恒等于全集。
- `300001.SZ`、`688001.SH` 在所有采样日均不在成员中。
- 端到端运行把这一点钉进了产物：`metrics.json` 的
  `meta.universe_daily_snapshots`（信号日 → 成员快照 sha256）与
  `meta.universe_daily_member_counts` 覆盖 **595 个信号日、全部非空**，
  信号日成员数 **min 14 / max 28**。基线运行的 `meta.universe` 为空对象，
  没有这份逐日证据。

---

## 6. 与未过滤基线 `ab378f9d…` 的三情景对照

两侧窗口完全相同（2015-01-09 ~ 2026-08-24，2,825 个交易日，初始资金
100,000，基准 000300.SH 同为 +28.66%），差异**只来自候选池收窄**。
下表均为 `metrics.json` 实测值，格式为 基线 → 本次：

| 指标 | `zero_cost` | `commission_tax` | `full_cost` |
| --- | --- | --- | --- |
| 期末权益 | 204,024.30 → 202,657.36 | 192,067.29 → 191,630.48 | 181,286.83 → **181,644.88** |
| 累计收益 | +104.02% → +102.66% | +92.07% → +91.63% | +81.29% → **+81.64%** |
| 年化收益 | 6.57% → 6.51% | 6.00% → 5.98% | 5.45% → 5.47% |
| 年化波动 | 10.89% → 9.73% | 11.11% → 9.92% | 11.33% → 10.10% |
| 最大回撤 | −20.05% → −23.59% | −20.76% → −24.19% | −21.34% → −24.68% |
| 成交笔数 `n_fills` | 1,852 → 1,714 | 1,852 → 1,714 | 1,852 → 1,714 |
| 佣金 | 0.00 → 0.00 | 9,260.00 → 8,570.00 | 9,260.00 → 8,570.00 |
| 印花税 | 0.00 → 0.00 | 2,697.01 → 2,456.88 | 2,694.47 → 2,454.48 |
| 滑点估计 | 0.00 → 0.00 | 0.00 → 0.00 | 10,783.00 → 9,988.00 |
| 累计换手（`turnover-v1`） | 35.19 → 32.38 | 36.57 → 33.55 | 37.86 → 34.64 |
| 期末现金比 | 64.49% → 66.99% | 62.28% → 65.10% | 60.04% → 63.18% |

**组合构成与成交路径的变化**（`metrics.json` 实际记录的字段，三情景一致）：

- 计划订单 2,040 → 1,905 单，成交 1,852 → 1,714 单（−138），成交量
  717,100 → 678,100 股；
- 拒单 188 → 191 单：`suspended_or_unknown` 179 → 182，
  `buy_at_upper_limit` 6 → 6，`sell_at_lower_limit` 3 → 3；
- 累计换手下降约 3.2 倍（`full_cost` 37.86 → 34.64，分子 5,383,857 →
  4,902,757），期末现金比上升约 3.1pp——候选收窄后，部分信号日的 Top-10
  更难凑满，场内资金占比更低。
- metrics.json 未记录"平均持仓只数"这类组合构成统计，本表不做该维度的
  断言；逐日成员数证据见 §5。

**方向与幅度，如实读法：**

- 累计收益变化都在 **±1.4 个百分点**内，且**方向随成本情景不同而不同**
  （`zero_cost`、`commission_tax` 略降，`full_cost` 略升）。这不构成
  "过滤改善/恶化策略"的结论——UNTRUSTED 数字不允许这种读法。
- **`zero_cost` 也变了，这是预期内的**：时点过滤收窄了每个信号日的候选池，
  动量排名的输入集合随之改变，目标组合与成交路径整体改变，权益路径自然
  不同——这与成本口径无关，任何情景下都会变。对照的意义在于确认变化
  来自候选收窄本身，而不是成本假设。
- 波动在三个情景下一致下降（约 −1.2pp），但最大回撤一致**加深**
  （约 −3.3pp）；在现金占比更高的前提下回撤反而更深，说明路径效应
  （选中了哪些标的）主导，而不是仓位效应。

---

## 7. `after_delist_date` 边界：真实数据不触发，靠合成夹具覆盖

- 真实 `security_master` 的 30 只 `delist_date` **全为空**，回测中
  `after_delist_date` 分支不会被真实数据触发——这不代表机制不存在。
- 为此把实现抽成模块级纯函数 `master_bar_boundary_issues`
  （`src/stock_quant/data_pipeline.py`），用合成夹具在
  `tests/unit/test_master_bar_boundaries.py` 直接单测（commit `e7995aa`），
  既有 `before_list_date` 语义回归保留。5 个用例覆盖的行为：

| 用例 | 覆盖行为 |
| --- | --- |
| `test_bar_after_delist_date_is_flagged` | 退市日之后的 bar 报 WARNING，`boundary=after_delist_date` 并携带 `delist_date` |
| `test_bar_before_list_date_is_flagged` | 回归：上市日之前的 bar 仍按原语义报 `before_list_date` |
| `test_bar_on_the_boundaries_is_clean` | 闭区间：恰为 `list_date` / `delist_date` 当日的 bar 不算越界 |
| `test_symbol_without_bounds_is_skipped` | master 行缺两侧边界的标的完全不参与判定 |
| `test_empty_daily_returns_nothing` | 空行情表直接返回空，不误报 |

- 该检查是 WARNING 级：不阻断发布，只暴露数据源与 master 事实的矛盾；
  缺行归 `classify_missing_row` 管，不在此判定。

---

## 8. 可复现命令与实验 id

```bash
# 1) 发布裁剪子集与新定义（内容寻址；会推进数据集 CURRENT，daily_bar 不变；
#    需要 PYTHONPATH=src 或已安装 stock_quant 包）
cd project
python trim_universe_membership.py
# => dataset_version=38c52358…
#    membership_table_sha256=756db257…
#    definition=configs/universes/custom_csi300_ic_tradable.yml

# 2) 时点过滤回测（ENGINEERING 诊断通路）
cd project && PYTHONPATH=src python -m stock_quant backtest momentum_60d \
    --spec configs/experiments/momentum_60d_pit_tradable.yml \
    --engineering --root .
# => experiment_id=2b165500…  trust=UNTRUSTED
```

| 实验 id | 规格 | 数据集 | 池 |
| --- | --- | --- | --- |
| `ab378f9d23509b5d2415a81ac7aa36eb070cf0293487346071b3e5d52f04606d` | `momentum_60d_offline_real_extended.yml` | `af5799ae…` | `configs/universe.yml` 30 只，无时点过滤 |
| `2b165500b6aebd4121badbbd81e983f5e2627751c897b0085b4b551cbdbbd1ce` | `momentum_60d_pit_tradable.yml` | `38c52358…` | `custom_csi300_ic_tradable`，时点过滤 |

实验 id 内容寻址：同一代码 + 同一数据集 + 同一规格 → 同一 id。

---

## 9. 后续工作

1. **正式 RESEARCH 运行依赖数据验收链**（另一方案）：`data/acceptance/`
   出现 ACCEPTED 记录、公司行为覆盖补全之后，才可能产出可信绩效数字；
   本文全部对照只是工程诊断。
2. **walk-forward 稳定性结论依赖本方案产出**（另一计划）：时点过滤打通后，
   滚动窗口检验才有无池内前视偏差的候选序列可用。
3. `custom_csi300_ic` 的 948 只忠实记录保持原样，留给无偏 CSI300 池的
   外部数据获取工作。

---

## 附：本轮代码变更

| commit | 内容 |
| --- | --- |
| `e7995aa` | test: `master_bar_boundary_issues` 纯函数 + 合成夹具单测 |
| `362c203` | feat: membership 裁剪纯函数（`project/trim_universe_membership.py`） |
| `3ac2547` | feat: 发布裁剪子集数据集与 `custom_csi300_ic_tradable` 定义 |
| `bcc60e0` | feat: 时点过滤实验规格 `momentum_60d_pit_tradable.yml` |

（`project/data/` 整体被 `.gitignore`，本文引用的运行产物均为本地证据。）
