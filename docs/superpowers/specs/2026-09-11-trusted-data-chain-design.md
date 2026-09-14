# 可信数据链路（验收记录 + 公司行为）设计

## 背景与问题

`data/acceptance/` 为空。当前数据集 `af5799ae` 是离线重建产物
（`build_config = {}`，见 `project/rebuild_offline_real_dataset.py` 与
`project/extend_history_offline.py`），而 `real-data-v1` 验收要求
`build_config.origin == "data_update"` 且 `raw_snapshots[]` 非空
（`_check_source_roles`，`src/stock_quant/research/acceptance/checks.py:386`），
**`origin=bootstrap` 在结构上永远不可能通过**。

正式通路 `research run` 恒定以 RESEARCH 模式运行、无旁路开关
（`src/stock_quant/cli.py:156`，`trust_mode: DataTrustMode = DataTrustMode.RESEARCH`），
因此当前环境下该命令根本跑不通。唯一可走的是
`backtest … --engineering`（`src/stock_quant/cli.py:600`），
即前一份诊断报告 `docs/operations/2026-09-11-momentum60d-extended-diagnostic.md`
所用的通路。

### 本轮新增实测发现（决定本方案形态）

已发布数据集的日线是**残缺的**：

| 检查 | 实测结果 |
| --- | --- |
| raw 快照 `0d5497f05ee2` 中 `000001.SZ` | **1371 行**，恰好等于 2021-01-04..2026-08-28 的**全部** 1371 个交易日 |
| 同一数据集同期 `000001.SZ` | 少 **169 行** |
| 全池 raw 有、数据集无的 `(symbol, date)` | **4,823 对**，2021–2026 逐年均匀分布、周一至周五均匀（非节假日/非停牌） |
| 数据集行是否 ⊆ raw | 是（差集 2742 = 2 个基准指数 × 1371，基准走 `index_history` 另一通路） |
| 被丢弃行在 raw 中是否异常 | 否，无任何 NaN，价格/量/额齐全 |
| `_missing_row_failures(...)` 全窗口（2015-01-01..2026-08-28） | **5,327 个无法解释缺口** |

`_ACCEPTED_MISSING_CODES`（`src/stock_quant/research/acceptance/checks.py:111`）
只接受 `not_listed` / `delisted` / `non_trading_day`，**停牌同样判 FAIL**。

**结论：`date_window_completeness`（`checks.py:273`）必然 FAIL，
`real-data-v1` 验收记录发不出来。** 问题 3 不是"补一个签字流程"，
而是卡在数据本身上。

成因判断：`af5799ae` 不绑定任何 raw 快照（离线重建），无法从数据集反查
其数据来源；raw 目录里那份完整快照来自后来的**重新抓取**，从未用于重建数据集。
数据集 2021+ 的行来自更早一次不完整的抓取。
反向的好消息：**raw 证明 tushare 单次抓取本身是完整的**，
所以真实 `data update` 大概率能拿到齐全日线——但这是推断，必须实测。

## 本方案在总盘中的位置

7 项待解决问题按"外部依赖的有无 + 能否独立验收"拆成 3 个方案
（决策记录见末节）：

| 方案 | 覆盖问题 | 外部依赖 |
| --- | --- | --- |
| 一 | 1 时点股票池可用化、6 退市边界 | 无 |
| **二（本设计）** | **3 验收记录、4 公司行为** | **重（需联网取数）** |
| 三 | 5 成本口径、7 稳定性与挑战结论 | 无，但依赖方案一、二 |

## 目标

1. 产出一次**真实 `data update`** 的数据集：
   `build_config.origin == "data_update"`，绑定 `raw_snapshots[]`。
2. 让 8 项自动检查全部 PASS，重点是 `date_window_completeness` 与
   `corporate_action_evidence`。
3. 完成 9 项人工核证，发布 `real-data-v1` **ACCEPTED** 记录。
4. 用该记录跑通正式通路 `python -m stock_quant research run`。

## 非目标

- **不解决官方证据缺口（问题 2）**，见下节；本轮不做任何动作。
- 不做无偏股票池。
- 不改 `security_master`；不改动已封存的 `custom_csi300_ic` 正典。
- **不消除幸存者偏差。** 验收记录只证明**数据完整**，不证明
  **池子无偏**；30 只仍是人工挑选的、基本在市的白马。报告必须把这两件事
  分开陈述。

## 问题 2 的处置：显式记录为外部阻塞

实测（2026-09-11）：

| 通路 | 结果 |
| --- | --- |
| csindex 官方 API | HTTP 200 + `{"code":"500","msg":"服务器异常"}` |
| csindex `index-basic-info` | HTTP 404 |
| tushare `index_weight` | 无权限 |

因此 2017-02 官方调仓公告拿不到 → `SH600549 厦门钨业` 的 2017-02-13 纳入
无法用 Tier-A 证据坐实 → `custom_csi300_ic` 不能升格为 canonical `csi300`。
**本方案只在报告中留痕，不做补救动作。**

## 前置约束：与方案一的顺序

`data update` **原样携带** CURRENT 数据集的 `universe_membership`
（`src/stock_quant/data_pipeline.py:1002` 起的 `_read_baseline`，注释明确
"an update never rewrites them"；携带发生在 `_read_baseline` 读表处）。

**所以方案一必须先执行**：它把裁剪后的 28 只 / 31 行成员表发布为 CURRENT。
若顺序颠倒，更新会把 1221 行未裁剪表带进新数据集，
使方案一的时点过滤在新数据集上失效。

另：方案二更新后数据集版本改变，方案一的实验需针对新版本重跑
（其规格用 `dataset_version: CURRENT`，无需改文件）。

## 组件

### ① 真实全窗口数据更新

```bash
cd project
python -m stock_quant data update \
    --start 2015-01-01 --end 2026-08-28 --root .
```

窗口取 **2015-01-01 起**，理由是它同时解决两件事：与现有 11.6 年回测窗口
一致；且公司行为覆盖窗口**就等于这次请求窗口**——
`_refresh_corporate_actions`（`src/stock_quant/data_pipeline.py:1284`）
用请求的 `start`/`end` 构造每一条 coverage 行，
所以一次更新即可同时解决"无验收证据"与"公司行为只覆盖 2021+"。

产出：新数据集版本；`build_config.origin = data_update`；
`raw_snapshots[]` 绑定本次抓取；`corporate_action_coverage` 覆盖全窗口。

**必需源约束**（`_REQUIRED_ROLE`，`data_pipeline.py:159`）：
`tushare` 与 `akshare` 必需，`baostock` 可选（已停用不影响自动检查）。

### ② 第一关：缺口实测（实测为准）

在新数据集上复算
`_missing_row_failures(daily, master, grid)`（`checks.py:691`），
窗口 2015-01-01..2026-08-28。

- 返回空 → 继续；
- **非空 → 就地停止本方案**，报告缺口规模与分布，重新设计。
  （这是设计好的出口，不是失败。）

### ③ 第二关：公司行为覆盖实测

对新数据集的 `corporate_action_coverage` 调用
`evaluate_corporate_action_trust(coverage, symbols, start, end)`
（`src/stock_quant/research/trust.py:85`），`trusted` 必须为 `True`。
不为真时按 `symbol × reason` 定位（`SOURCE_FETCH_FAILED` /
`SOURCE_CONFLICT` / `COVERAGE_INCOMPLETE` 等的处置不同），再决定补抓或复核。

### ④ 自动检查预演

把 8 项自动检查（`AUTOMATED_CHECK_CODES`，`acceptance/models.py:76`）
对着新数据集全跑一遍，确认 PASS，避免"准备清单后才发现挂"。

### ⑤ 人工核证证据包 + 草稿清单

`MANUAL_CHECK_CODES`（`acceptance/models.py:87`）共 9 项，
逐项生成**可核验证据**（行数、抽样对照、URL、哈希、扫描结果），
并写成 `data/acceptance/<version>-checklist.yml` 草稿。

已知难点：`cross_source_price_sample` 需要第二价格源，
而 baostock 已停（可选源，不挡自动检查）。
需在证据包内用 akshare 侧价格或公开源补出对照，否则该项无法签字。

### ⑥ owner 审阅并签署发布

你以 `operator_id` 发布
（`publish_checklist`，`src/stock_quant/research/acceptance/service.py:108`）。
**签字是人的行为，我不代为认定。**

### ⑦ 正式通路验证

```bash
cd project
python -m stock_quant research run \
    --spec configs/experiments/momentum_60d_pit_tradable.yml --root .
```

该规格由方案一产出，携带 `universe_definition: custom_csi300_ic_tradable`。
该命令必须先通过数据验收门禁与 `universe_acceptance`，才算问题 3 解决。

### ⑧ 报告

`docs/operations/2026-09-11-trusted-data-chain.md`：
记录新旧数据集对比（含 4,823 行缺口的实测证据）、
8+9 项验收结果、ACCEPTED 记录 id，
并明确区分**「数据已 ACCEPTED」**与**「池仍存幸存者偏差」**。

## 已知风险

1. **2015–2020 公司行为**：cninfo/东财可能不返回该区间数据，或跨源冲突，
   导致覆盖读 `UNTRUSTED`、第二关挂。这是本方案最大的未知。
2. **tushare 限速/配额**：长窗口抓取中途失败会产生
   `CODE_SOURCE_FETCH_FAILED` FATAL（`_dispatch(..., required=True)`），
   更新直接不出数据集。失败后需重跑，跑前须确认配额。
3. **缺口复现**：若真实更新后缺口仍在，方案二就地停止（见 ②）。
4. **人工核证的第二价格源缺失**：见 ⑤。
5. **akshare 必需源**：公司行为逐标的最佳努力抓取失败只产生 WARNING，
   不会把 akshare 状态变红；但 **基准指数抓取**（`_fetch_benchmarks`，
   `data_pipeline.py:1127`）失败会，`required_reason_code_not_ok`
   会让 `source_role_health` FAIL。

## 验收标准

1. 新数据集 `build_config.origin == "data_update"` 且 `raw_snapshots[]` 非空；
2. `_missing_row_failures` 在 2015-01-01..2026-08-28 上返回 **0**；
3. `evaluate_corporate_action_trust` 对 30 只在同窗口返回 **trusted=True**；
4. 8 项自动检查**全 PASS**；
5. `data/acceptance/` 出现一条 **ACCEPTED** 记录；
6. `python -m stock_quant research run` 通过验收门禁（不再是 `--engineering`）；
7. 报告明确区分「数据已 ACCEPTED」与「池仍存幸存者偏差」。

## 决策记录

- owner 于 2026-09-11 确认：7 项问题拆成 3 个方案，切分依据是
  "外部依赖的有无 + 能否独立验收"。
- owner 于 2026-09-11 确认：方案二只做问题 3+4，问题 2 因外部阻塞
  （csindex 500/404、`index_weight` 无权限）单列，不在本方案范围内。
- owner 于 2026-09-11 确认：验收窗口取 **2015-01-01 起全窗口**
  （而非收缩到 2021+），以与现有 11.6 年回测窗口一致。
- owner 于 2026-09-11 确认：本轮实测出的数据完整性缺陷
  （数据集日线丢 4,823 行）**作为方案二第一关**，以实测为准；
  若真实更新后缺口复现则就地停止、另立方案。
- owner 于 2026-09-11 确认：9 项人工核证由 **AI 出证据包与草稿清单、
  owner 审阅签署**；`operator_id` 的签字不由 AI 代为完成。
- 说明：本方案原被描述为"取数 + 签字"两步，实测后改为
  "取数 → 两道实测关卡 → 自动检查 → 人工签字 → 正式跑通"。
