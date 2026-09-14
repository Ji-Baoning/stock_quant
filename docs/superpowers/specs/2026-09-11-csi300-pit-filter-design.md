# 时点成分过滤可用化设计

## 背景与问题

`custom_csi300_ic` 股票池在 2026-09-10/11 建成并冻结：数据集 `af5799ae` 的
`universe_membership` 携带 1221 条 facts、948 只唯一标的，覆盖
2015-01-05 至 2026-08-28；冻结定义见
`project/configs/universes/custom_csi300_ic.yml`（提交 `cd80982`）。

**但它驱动不了任何实验。** 实测：

- 数据集的 `universe_membership` 有 948 只标的，而 `security_master` 只有 **30** 只；
- 预检 `evaluate_index_membership_evidence(..., master=…)`
  （`src/stock_quant/research/acceptance/checks.py:436`）经
  `_symbol_issues`（`src/stock_quant/data_quality/raw_checks.py:497`）把
  **不在 `security_master` 里的成分标的判为 FATAL `UNIVERSE_UNKNOWN_SYMBOL`**；
- 实测缺口 **920 只**，且这道门禁**没有旁路开关**，`research`/`backtest` 都在
  `universe_acceptance` 阶段、任何因子计算之前终止。

过滤器本身是好的：30 只 master 中有 **28 只**在 CSI300 成分表里出现过
（只有 `300001.SZ`、`688001.SH` 从未入选）。卡死的纯粹是那 920 只在
`security_master` 里没有对应行的额外标的。

## 本方案在总盘中的位置

7 项待解决问题按"外部依赖的有无 + 能否独立验收"拆成 3 个方案（决策记录见末节）：

| 方案 | 覆盖问题 | 外部依赖 |
| --- | --- | --- |
| **一（本设计）** | 1 时点股票池可用化、6 退市边界 | 无 |
| 二 | 2 官方证据、3 验收记录、4 公司行为 | 重 |
| 三 | 5 成本口径、7 稳定性与挑战结论 | 无，但依赖方案一、二 |

本方案**不做无偏股票池**。真正无偏的 CSI300 时点池需要约 918 只标的、
2015 年起的日线行情，而本地一条都没有（见"选型"）。那是方案二的取数范围。

## 目标

1. 在当前 30 只人工池上，让"该信号日这只股票**当时是不是** CSI300 成分"
   真正参与选股，消除池内前视偏差。
2. 用合成夹具验证 `after_delist_date` 边界机制确实会拦，补上问题 6 的机制覆盖。

## 非目标

- **不做无偏股票池。** 裁到 28 只不消除幸存者偏差——这 30 只是人工挑的、
  基本还在市的白马。产出物一律命名为 `custom_csi300_ic_tradable`，
  **不得自称 CSI300**，不得占用 canonical `csi300`。
- 不改动已封存的 `custom_csi300_ic` 正典（1221 条 facts 是忠实记录，
  要留给未来的路线 A）。
- 不改 `security_master`。
- 不解决官方证据缺口（问题 2）、验收记录缺失（问题 3）、
  公司行为覆盖（问题 4）、成本口径（问题 5）、稳定性结论（问题 7）。

## 选型

### 被排除的路线

| 路线 | 排除理由 |
| --- | --- |
| 扩 `security_master` 到 948 只（不发行情） | 920 只没有行情，因子在空数据上空转；且本地 `raw/tushare/stock_basic` 只有 `list_status=L`（`delist_date` 全空），拿不到退市名 |
| 在定义里加白名单 | 门禁比对的是数据集**整张 `universe_membership` 表**的内容哈希（`checks.py:436`），定义端无法收窄 |
| 换一个不校验 master 的通路 | 不存在。`_preflight_universe` 在预检阶段无条件传入 `master` |

### 采用的路线

**裁 `universe_membership` 表并重发数据集。** 两个前提条件都满足：

- 符号校验是**逐行**的（`_symbol_issues(rows, master)`），裁掉的行不再被校验；
- 基数校验只对 canonical id 生效（`_CANONICAL_UNIVERSE_SIZES`），
  `custom_*` 跳过，所以 28 只不会撞上"必须恰好 300"的约束。

### 本地数据现状（说明为什么无偏池不在此方案内）

| 本地已有 | 实际覆盖 |
| --- | --- |
| `raw/tushare/stock_basic` | 5560 行，`list_status` 全为 `L`，`delist_date` 全空 |
| `raw/tushare/daily` | 60 个文件，仅 **30 只**，且只从 **2021-01-04** 起 |
| 数据集 `daily_bar` | **32 只**标的 |
| 数据集 `security_master` | **30** 只，`delist_date` 全空 |

## 组件

### ① `project/trim_universe_membership.py`（新增）

形态对齐已有的 `project/rebuild_offline_real_dataset.py`：

1. `DatasetReader` 打开当前数据集，读 `security_master` 与 `universe_membership`；
2. 按 `security_master.symbol` 过滤 `universe_membership` 的行；
3. `DatasetPublisher.publish(tables, QualityReport())` 重发；
4. 打印新 `dataset_version`，并写出
   `project/configs/universes/custom_csi300_ic_tradable.yml`。

关键点：**逐行过滤保留全部证据字段**（`source_document_sha256`、
`snapshot_sha256`、`source_url` 等），证据链不断。定义文件的字段处置：

| 字段 | 取值 |
| --- | --- |
| `universe_id` | `custom_csi300_ic_tradable`（匹配 `custom_[a-z0-9_]+`）；**facts 行的 `universe_id` 必须同步重贴**，理由见下 |
| `schema_version` | 沿用 `1` |
| `rules_version` | 原值追加 `+tradable` 后缀 |
| `membership_table_sha256` | 过滤后 facts 的 `membership_content_hash` |
| `evidence_summary_sha256` | 沿用原值（快照未被改动） |
| `coverage_start` / `coverage_end` | **沿用 `build_csi300_universe.py` 的既有语义**：取数据集 `daily_bar` 的 `trade_date` min/max，而非从 facts 推导 |

裁剪结果预期为 **28 只唯一标的 / 31 行**（同一标的可能有多段区间），
所以验收断言的是**唯一 symbol 数**，不是行数。

**为什么必须重贴 `universe_id`：** `UniverseResolver.__init__`
（`src/stock_quant/research/universe.py:180`）逐条比对 resolved fact 的
`universe_id` 与定义的 `universe_id`，不一致直接 `ValueError`
（实测消息：`resolved row belongs to universe_id 'custom_csi300_ic', not
the definition's 'custom_csi300_ic_tradable'`）。只过滤行不重贴，实验必然在
预检阶段崩掉。重贴只改这一个身份列，证据字段全部保留；
`membership_table_sha256` 因此变为
`756db257b6a13846c4452fd2e7504c54636705ca4f6acb4b712d092b549a4255`。

### ② 实验规格（新增）

以 `project/configs/experiments/momentum_60d_offline_real_extended.yml`
为蓝本，增加 `universe_definition: custom_csi300_ic_tradable`。
`runner.py:_config_hashes` 会把 `configs/universes/<name>.yml` 纳入实验身份，
因此这是一个**新的实验 id**，既有结果（`ab378f9d…`）不受影响。

### ③ 合成夹具单测（新增，`tests/unit/`）

构造带 `list_date` / `delist_date` 的假 master 与越界 bar，断言
`after_delist_date` 触发。这是**机制验证**，不依赖外部数据——
当前 master 的 `delist_date` 全空，这条分支在真实数据上永远不会触发。

### ④ 一次 ENGINEERING 运行 + 报告

`backtest momentum_60d --engineering`。正式通路 `research run` 仍被
无 ACCEPTED 验收记录的问题挡住（问题 3，属方案二）。

## 已知风险

裁剪后，那 28 只里**任何一条 `status=removed` 且 `reason` 属于终止类**的记录，
若对应 master 行的 `last_tradable_date` 为 `None`，会撞上
`raw_checks.py:640-657` 的 FATAL `UNIVERSE_DELISTING_ENDPOINT_UNPROVEN`。

判断是**大概率不触发**——这 28 只现在都在市（`list_status=L`），
被调出 CSI300 属于常规调仓而非退市，`reason` 应为 `regular_rebalance`。
但这是推测，必须实现时实测。**若触发，本方案就地停止并重新设计**，
所以这是实现的第一步要验证的事。

## 验收标准

1. 新数据集发布成功，其 `universe_membership` 的**唯一 symbol 数 = 28**；
2. `backtest momentum_60d --engineering` 跑通，`factor_metadata.json` 里
   每个信号日的成员快照哈希非空；
3. 过滤确实在裁，两条断言同时成立：
   - `300001.SZ` 与 `688001.SH`（master 在册、从未入选 CSI300）
     在**全部信号日的候选集里都不出现**；
   - 至少存在一个信号日，其候选集 ≠ 全部 28 只（成员集确实随时间变化，
     而不是恰好恒等于全集）；
4. 合成夹具测试通过，`after_delist_date` 被断言触发；
5. 报告的结论段落明确标注"池内时点化，仍存在幸存者偏差"。

## 决策记录

- owner 于 2026-09-11 确认：7 项问题**拆成 3 个方案**而非 1 个或 5 个，
  切分依据是"外部依赖的有无 + 能否独立验收"。
- owner 于 2026-09-11 确认：方案一目标为**机制打通**，不做无偏池；
  无偏池所需的取数并入方案二。
- owner 于 2026-09-11 确认：退市边界以**合成夹具单测**覆盖，
  不以纳入真实退市股的方式引入外部依赖。
- 前述"外部依赖的有无"的初判一度把方案一标为完全无依赖，后经实测更正：
  路线 A（无偏池）需要约 918 只、2015 年起的日线，属外部依赖重，已移出本方案。
