# 可信数据链路：真实数据更新与验收关卡实测

> **信任等级：UNTRUSTED。链路状态：bar 门禁根因已修复（8,813 → 714），剩余缺口为真实停牌日；
> 公司行为冲突待 owner 复核。仍未产出 ACCEPTED 记录。**
> 本文曾按方案二（`docs/superpowers/plans/2026-09-11-trusted-data-chain.md`）Task 4 Step 4 的
> 设计内停止出口记录首次失败；随后定位到管线浮点 bug 并修复（`d4d710b`），重跑更新后缺口大幅收敛。
> 按惯例仍须声明：**股票池是人工挑选、仍在市的 30 只（裁剪后 28 只），幸存者偏差仍在**；
> 本文档不构成任何数据质量或策略有效性的可信声明。

## 0. 结论摘要

- 真实 `data update`（2015-01-01 .. 2026-08-28，tushare + akshare）两次成功发布：
  首跑 `5464dd8f…`，修复后重跑 `6699be53…`（两次均 0 FATAL、`resolved_end_date` 非回退）。
- 首跑后 `bar_gate unexplained=8813`。排查定位到**管线浮点 bug**：`normalize_daily` 用
  `float.is_integer()` 校验「手→股」换算，IEEE754 表示误差使约 11.5% 的正常两位小手数
  （如 `1263029.64 × 100 = 126302963.99999999`）被判非整数、整行丢弃。修复（`d4d710b`，
  Decimal 精确换算）后重跑，缺口降至 **714**。
- 剩余 714 条全部是**真实停牌日**：连续多日区块、集中于 2015 股灾停牌潮（396）与
  2016 重组季（202），2017 年起骤降。tushare `daily` 对停牌证券不返回行，验收口径
  （设计如此）把停牌记为不可解释缺失。**需要 owner 决策**：停牌回补建模或验收口径修订。
- 公司行为关 `601318.SH SOURCE_CONFLICT`（2018-06-07 除息，cninfo 报 1.2、eastmoney 报
  1.0，两行已入隔离区）——按既有 `corporate_action_reviews.yml` 流程由 owner 裁定。
- 8 项自动检查 6 PASS / 2 FAIL（`date_window_completeness` 对应停牌缺口、
  `corporate_action_evidence` 对应上述冲突）。Task 5/6 未执行，`data/acceptance/` 为空。

## 1. 更新前后

| 项 | 首跑前基线（方案一裁剪版） | 首跑 `5464dd8f…` | 修复后重跑 `6699be53…` |
| --- | --- | --- | --- |
| `daily_bar` 行数 | 72,764 | 69,278 | **77,377**（equity 67,098 → 71,711） |
| `adjusted_bar` 行数 | 67,098 | 63,612 | **71,711** |
| `corporate_action_quarantine` | 0 | 2 | 2 |
| `trading_calendar` | 2,833 | 2,833 | 2,833 |
| `universe_membership` | 31 行 / 28 只（原样携带） | 同左 | 同左 |

前置检查（`project/verify_update_readiness.py`）在更新前输出 `ready=true`：
tushare.daily 探针 2,833 行、cninfo 31 行、eastmoney 28 行；基线即裁剪版 `38c52358…`。

## 2. 第一关实测：8,813 → 714（根因与残留）

### 2.1 已修复：管线浮点 bug（`d4d710b`）

首跑缺口 8,813 条的**主因不是数据语义**，而是 `normalize_daily`（`src/stock_quant/data_model/normalize.py`）
的量纲换算：tushare 的 `vol`（手，两位小数）乘以 100 换成股后，用 `float.is_integer()` 判整，
IEEE754 表示误差使大量正常值失败——

```
1263029.64 * 100 = 126302963.99999999   # is_integer() == False，整行被判 invalid_volume
```

证据链：tushare 对 000001.SZ 全窗口单次调用返回完整 2,833 行（含 2015-01-14，成交 1.263 亿股）；
该帧规范化后 valid 2,508 / rejected 325，被拒原因全部 `invalid_volume`，与发布表缺失的 325 天逐位吻合。
修复为 `Decimal(str(vol)) × factor` 精确换算（回归测试锁定），重跑后该标的 2,833/0。
全池缺口 8,813 → 714，找回 8,099 行。

### 2.2 残留：714 条真实停牌日，需要 owner 决策

重跑后的缺口全部为 `unknown_or_suspended`，分布呈**连续区块**、与 A 股停牌史吻合：

- 分年：2015: 396（股灾停牌潮）、2016: 202（重组季）、2017: 52、2018: 29、2019: 19、
  2025: 16，2020–2024 及 2026 为 0。
- 分标的（16/30 只命中）：`000651.SZ` 157、`601919.SH` 143、`300001.SZ` 70、
  `000858.SZ` 60、`300059.SZ` 56、`000333.SZ` 53（其 2016-05-18..05-31、2018-09-10..09-18
  区块分别对应要约收购库卡、换股吸收小天鹅的停牌窗口）等。

tushare `daily` 对停牌证券不返回行；验收口径（`_ACCEPTED_MISSING_CODES` 不含停牌，
spec 注释明示 "A suspension … fails date_window_completeness"）把停牌记为不可解释。
**停牌证据的独立来源当前全部受阻（2026-09-11 实测）**：

1. tushare `suspend_d`：本 token **无访问权限**（需更高积分档位）；
2. baostock（原生返回停牌日行，`tradestatus=0`）：服务器 2026-09-05 起停机，
   `sources.yml` 已禁用；
3. akshare `stock_tfp_em`（东财停复牌）：实测**忽略历史日期参数**——查询 2016-05-19
   返回的实为近期记录（美的条目显示的是 2019-05 停牌），对 2015–2016 停牌潮无覆盖。

因此「以独立停牌事实为证据的回补」暂时无法实施。出路（owner 决策）：

1. **升级 tushare 积分**解锁 `suspend_d`，或等待 baostock 复机，再按推荐方案
   物化 `volume=0`、前收平推、来源如实标注的停牌 bar（保持完整网格强保证）；
2. **修订验收口径**：允许「经第二来源证实的停牌」作为可接受缺失分类。
   保持数据纯净，但永久弱化完整性保证，且属 spec 级变更。

两条路都须先成文（新方案）再实施，不得为过检临场放宽。

### 2.3 已修复：缺失日警告的成员判断 bug（`85c25c6`）

两次发布的 `quality_report` 均带 56,660 条 `unknown_or_suspended` WARNING，其中大量
对应实际已发布的行（如 `000001.SZ 2015-01-05`）。离线复现定位到根因：`update()` 把
主源覆盖集合 `primary_dates` 填成**裸日期**，而 `_missing_issues` 用
`(symbol, date)` **元组**做成员判断——永远不匹配，导致每只上市标的的每个开市日
都被误警（20 只 pre-2015 标的 × 2,833 网格日 = 56,660，与实测逐位吻合）。
修复为 `(symbol, date)` 对（回归测试锁定），下次更新起警告数应回落到真实缺口量级
（约 714）。该 WARNING 不参与任何门禁判定，此前未阻塞本链路。

## 3. 第二关实测：601318.SH 冲突待 owner 裁定

`corporate_action_gate trusted=false reasons=1`：

- `601318.SH SOURCE_CONFLICT`：2018-06-07 除息，cninfo 报每股现金股利 **1.2**、
  eastmoney 报 **1.0**，两行按设计进入 `corporate_action_quarantine`
  （`reason=cross_source_conflict`）。
- 其余 29 只干净：`VERIFIED` 27、`VERIFIED_EMPTY` 2、`UNTRUSTED` 仅此 1 只。

处置：在 `project/configs/corporate_action_reviews.yml` 追加一条与 300750.SZ 先例同构的
复核条目（`symbol` / `ex_date` / `selected_source` / 各字段值 / `rationale` 引官方公告），
重跑更新后隔离区清空。**裁定必须以巨潮官方公告为准，AI 不代为认定。**

## 4. 8 项自动检查逐项状态（重跑后）

| 检查 | 状态 | 说明 |
| --- | --- | --- |
| `dataset_manifest_integrity` | PASS | |
| `quality_report_integrity` | PASS | |
| `required_table_coverage` | PASS | |
| `date_window_completeness` | **FAIL** | 对应 714 条真实停牌缺口 |
| `security_master_evidence` | PASS | |
| `corporate_action_evidence` | **FAIL** | 对应 601318.SH 冲突 |
| `raw_snapshot_traceability` | PASS | raw 快照逐字节可溯 |
| `source_role_health` | PASS | |

`verdict passed=false`（两处 FAIL 均有明确处置路径，见上）。

## 5. 验收记录与正式通路：未执行

- **无 ACCEPTED（或 REJECTED）记录**：`data/acceptance/` 为空；Task 5 未运行——
  关卡探针的设计目的就是「绝不对无法过检的数据集准备清单」。
- **正式通路 `research run` 未验证**：它依赖 ACCEPTED 记录，留待两道关卡收口后执行。
- 重申：即使后续全部通过，验收记录也只证明**数据完整**，不证明股票池无偏
  （30 只人工池、幸存者偏差仍在），更不证明任何策略有效。

## 6. 外部阻塞留痕（承接既有记录）

canonical `csi300` 成分事实的外部获取仍被阻塞（问题 2，承接方案二既有实测）：
csindex 官方端点 500/404、`index_weight` 接口无权限。本次未改变该状态；
时点过滤仍由 `custom_csi300_ic_tradable`（方案一产物）承担，它**不是**无偏 CSI300。

## 7. 对下游的重要提醒

`dataset_version: CURRENT` 现指向 `6699be53…`，其 `daily_bar` 已比
`ab378f9d…` 基线所用数据**多出约 4,600 行停牌日/补发行情**
（equity 67,098 → 71,711）。此后任何与旧基线的对照实验若出现 `zero_cost` 情景漂移，
先核对这里的数据集代际差异，而不是当作成本或代码变化；需要精确对照旧数据时，
把规格的 `dataset_version` 显式钉在对应哈希上。

## 8. 可复现命令

```
# 前置检查（只读）
PYTHONPATH=src python project/verify_update_readiness.py
# 真实更新（联网，消耗 tushare 配额；TUSHARE_TOKEN 须在环境）
python -m stock_quant data update --start 2015-01-01 --end 2026-08-28 --root .
# 两道关卡 + 8 项自动检查预演（只读）
PYTHONPATH=src python project/probe_dataset_gates.py
```

本轮提交：`2e29867`（前置检查工具）、`eb0137f`（关卡探针）、
`1fc0a1b`（证据包工具）、`d4d710b`（vol 换算浮点修复 + 回归测试）、
`85c25c6`（primary 覆盖集合改 (symbol, date) 对 + 回归测试）、
`69522b4`（601318.SH 复核采信 cninfo，owner 裁定）、
`03a2ddb` / `61d1898`（本文两次版本）。

运维留痕：tushare `stock_basic` 在本 token 上限流 1 次/分钟，连续触发会升级为
1 次/小时，且**每日配额仅 5 次**（2026-09-11 实测，超限次日重置）；重跑更新须与其余
调用保持足够间隔，一天内重跑次数有限。失败时 `CURRENT` 不变，可安全重试；
CLI 只回显错误计数，失败明细需进程内检查 `result.source_status` 与 FATAL issues。
601318.SH 的 cninfo 复核条目（`69522b4`）已入库，**尚未被任何已发布数据集应用**——
下次成功的 `data update` 会把它带进公司行为调和，届时该标的应转 `VERIFIED`、
隔离区清空、`corporate_action_gate` 收口。
