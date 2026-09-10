# 可信数据链路：真实数据更新与验收关卡实测

> **信任等级：UNTRUSTED。链路状态：在关卡处按设计停止（BLOCKED），未产出 ACCEPTED 记录。**
> 本次验收流程未完成：数据完整性两道关卡未通过，属于方案二（`docs/superpowers/plans/2026-09-11-trusted-data-chain.md`）
> Task 4 Step 4 的**设计内停止出口**——记录缺口分布、就地停止、留待重新设计。
> 按惯例仍须声明：**股票池是人工挑选、仍在市的 30 只（裁剪后 28 只），幸存者偏差仍在**；
> 本文档不构成任何数据质量或策略有效性的可信声明。

## 0. 结论摘要

- 真实 `data update`（2015-01-01 .. 2026-08-28，tushare + akshare）成功发布新数据集
  `5464dd8f…`：质量门禁 0 FATAL（56660 条 WARNING），`resolved_end_date=2026-08-28` 非回退。
- 但验收两道关卡**未通过**：日线缺口关 `unexplained=8813`（全部 `unknown_or_suspended`），
  公司行为关 `601318.SH SOURCE_CONFLICT`（2 行已入隔离区）。8 项自动检查 6 PASS / 2 FAIL。
- 依方案二预设的停止出口：**不裁剪窗口、不放宽口径**，就地停止；Task 5（验收清单）、
  Task 6（正式通路 `research run`）**未执行**，`data/acceptance/` 下无任何记录。
- 需要的新机制（后续方案）：停牌日回补/豁免的正式建模；601318.SH 分红冲突的人工复核。

## 1. 更新前后

| 项 | 更新前（方案一裁剪版） | 更新后 |
| --- | --- | --- |
| 数据集版本 | `38c5235886030d8f0ce83c011ce0684046c02e82da4ab6c9c5c4b47995a435cf` | `5464dd8f70186a7ba4384f47efd72da9873c20a81f0fb45f32385b0e7b2026ec` |
| `daily_bar` 行数 | 72,764 | 69,278（**少 3,486**） |
| `corporate_action` 行数 | 511 | 511 |
| `corporate_action_quarantine` 行数 | 0 | **2** |
| `trading_calendar` 行数 | 2,833 | 2,833 |
| `universe_membership` | 31 行 / 28 只（裁剪版，原样携带） | 同左 |

前置检查（`project/verify_update_readiness.py`）在更新前输出 `ready=true`：
tushare.daily 探针 2,833 行、cninfo 31 行、eastmoney 28 行；基线即裁剪版 `38c52358…`
（方案一先行约束满足）。原始离线基线 `af5799ae…` 已被方案一的新版本取代。

更新命令与结果：`python -m stock_quant data update --start 2015-01-01 --end 2026-08-28 --root .`
→ `PASS`，`ERROR=0 FATAL=0 INFO=0 WARNING=56660`，`source tushare: ok`、`source akshare: ok`
（baostock 未启用，非必需角色）。

## 2. 缺口实测（第一关：FAIL）

计划撰写时旧离线数据集 `af5799ae…` 的实测缺口：`_missing_row_failures` = **5,327**，
其中 2021 年以来缺失 4,823 对 `(symbol, date)`。

新数据集 `5464dd8f…` 在同一窗口（2,833 个开市日 × 30 只）上的复测：

- `bar_gate passed=false unexplained=8813`，全部归类 `unknown_or_suspended`（无一进入
  `not_listed` / `delisted` / `non_trading_day` 等可接受类）。
- 分年分布：2015: 864，2016: 695，2017: 614，2018: 601，2019: 670，2020: 740，
  2021: 788，2022: 825，2023: 852，2024: 847，2025: 800，2026: 517。
- 分标的：30 只**全部**命中，最少 101（688506.SH）、最多 460（601919.SH），
  每只约占其窗口开市日的 4%–16%。
- 样本（前 5 条）：`000001.SZ 2015-01-14`、`000001.SZ 2015-01-22`、
  `000001.SZ 2015-01-27`、`000001.SZ 2015-02-12`、`000001.SZ 2015-02-25`。

**成因判断（供重新设计，不是本次的修复）**：新库 `daily_bar` 比旧离线库**少 3,486 行**，
缺口在 30 只上普遍、在 12 个年度上均匀出现，且全部为 `unknown_or_suspended`——
与「tushare `daily` 接口不返回停牌日行、而旧离线管线携带了这些行」的假设一致。
旧离线库同样不达标（5,327），说明**两条路径都缺一个停牌日的正式建模**：
要么回补（如 `suspend_d` 类来源），要么在验收口径中给停牌日一条可豁免的分类。
方案二明文禁止为过检而裁窗或放宽口径，此项留待新方案。

## 3. 公司行为实测（第二关：FAIL）

`corporate_action_gate trusted=false reasons=1`：

- `601318.SH SOURCE_CONFLICT`：cninfo 与 eastmoney 对同一事件给出不同每股现金股利——
  2018-06-07 除息，cninfo 报 **1.2**、eastmoney 报 **1.0**，两行均已按设计进入
  `corporate_action_quarantine`（`reason=cross_source_conflict`），等待人工复核。
- 覆盖面其余 29 只全部干净：`VERIFIED` 27 只、`VERIFIED_EMPTY` 2 只、`UNTRUSTED` 仅此 1 只。

处置（方案二预设路径）：走 `project/configs/corporate_action_reviews.yml` 人工复核流程，
由 owner 依官方公告裁定后重跑更新。**AI 不代为裁定。**

## 4. 8 项自动检查逐项状态

| 检查 | 状态 | 说明 |
| --- | --- | --- |
| `dataset_manifest_integrity` | PASS | |
| `quality_report_integrity` | PASS | |
| `required_table_coverage` | PASS | |
| `date_window_completeness` | **FAIL** | 与第一关缺口同源 |
| `security_master_evidence` | PASS | |
| `corporate_action_evidence` | **FAIL** | 与第二关冲突同源 |
| `raw_snapshot_traceability` | PASS | raw 快照逐字节可溯 |
| `source_role_health` | PASS | 必需供应端（tushare/akshare）健康 |

`verdict passed=false`（`project/probe_dataset_gates.py`，退出码 1）。

## 5. 验收记录与正式通路：未执行

- **无 ACCEPTED（或 REJECTED）记录**：`data/acceptance/` 为空；Task 5 未运行——
  关卡探针的设计目的就是「绝不对无法过检的数据集准备清单」。
- **正式通路 `research run` 未验证**：它依赖 ACCEPTED 记录，留待关卡修复后与 Task 5/6 一并执行。
- 重申：即使后续全部通过，验收记录也只证明**数据完整**，不证明股票池无偏
  （30 只人工池、幸存者偏差仍在），更不证明任何策略有效。

## 6. 外部阻塞留痕（承接既有记录）

canonical `csi300` 成分事实的外部获取仍被阻塞（问题 2，承接方案二的既有实测）：
csindex 官方端点 500/404、`index_weight` 接口无权限。本次未改变该状态；
时点过滤仍由 `custom_csi300_ic_tradable`（方案一产物）承担，它**不是**无偏 CSI300。

## 7. 对下游的重要提醒

`dataset_version: CURRENT` 的实验规格（如 `momentum_60d_offline_real_extended.yml`）
此后会跑到 `5464dd8f…` 上，而其 `daily_bar` 与 `ab378f9d…` 基线所用数据**不同**
（少了停牌日行）。任何与既有基线的对照实验若出现 `zero_cost` 情景漂移，应先核对
这里记录的数据集代际差异，而不是当作成本或代码变化。需要对照旧数据时，把规格的
`dataset_version` 显式钉在对应哈希上。

## 8. 可复现命令

```
# 前置检查（只读）
PYTHONPATH=src python project/verify_update_readiness.py
# 真实更新（联网，消耗 tushare 配额；TUSHARE_TOKEN 须在环境）
python -m stock_quant data update --start 2015-01-01 --end 2026-08-28 --root .
# 两道关卡 + 8 项自动检查预演（只读）
PYTHONPATH=src python project/probe_dataset_gates.py
```

本轮新增提交：`2e29867`（前置检查工具）、`eb0137f`（关卡探针）、
`1fc0a1b`（证据包工具，供关卡修复后的 Task 5 使用）。
