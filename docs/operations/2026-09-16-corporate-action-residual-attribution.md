# 公司行为残留归属清单：把验收 FAIL 记成"已解释的余额"

- 日期：2026-09-16
- 数据集：`CURRENT = d490c63784d3…`（2026-09-16 重建后），窗口 2015-01-05..2026-08-28
- 决定依据：[ADR-006](../adr/006-corporate-action-window-scope.md)（status: accepted，2026-09-15）
- 前置证据：[2026-09-14-blocking-gap-root-cause.md](2026-09-14-blocking-gap-root-cause.md)
- 测量方式：本次逐符号复算已发布数据集的 `corporate_action_coverage` 与
  `corporate_action_quarantine`，口径见 §4
- 性质：工程证据记录 + 归属台账。**不是**验收签署记录 —— `project/data/acceptances/`
  当前为空，尚无 ACCEPTED 验收；按
  [2026-09-11-trusted-data-chain.md](2026-09-11-trusted-data-chain.md)，
  **签字是 owner 的行为，本记录不代为认定。**

> **后续进展（2026-09-17，另一次重建）：** [ADR-007](../adr/007-corporate-action-third-party-arbitration.md)
> 的裁决器已在 `sources.yml` 显式启用（`tdx.enabled: true`）后重跑一次全窗口更新，
> `CURRENT` 随之由 `d490c63784d3…` 移至 **`01c74bee15b4…`**
> （`run_id = data_update_49caeff55f62`）。本文 §3 的预测（C 栏 16 只转 `VERIFIED`、
> 验收 FAIL 27→11）**已被这次重建证实**，实测见 §3 末的「2026-09-17 实测」。
> 本文正文其余部分保持 2026-09-16 当时的事实与判断，不做改写：
> `d490c63784d3…` 是本文写作时的 `CURRENT`，现在仍是一个可复算的历史版本。
> 重建发布后 `tdx.enabled` 已回 `false`（ADR-007 的默认值）。
> **本记录仍不是签署记录**：A/B 两栏的签字依旧没有发生。

## 结论

自动化验收的 `corporate_action_evidence` 单项 FAIL，触发 27 条
`[reason.code, symbol]`（生产者：`src/stock_quant/research/acceptance/checks.py:336-355`
→ `evaluate_corporate_action_trust`）。

**FAIL 计数不变，本记录不放松任何门禁**，只把这 27 条按"是否已有归属／决策依据"
分成三栏，使每次验收读到的 FAIL 从"未知待查"变成"已知、有归属、有出处"。

| 栏 | 只数 | 归属 | 出处 |
| --- | --- | --- | --- |
| **A 已有决策依据** | 2 | ADR-006 fail-closed 残留 | ADR-006 §Consequences 明文裁定 |
| **B 有归属、待签字** | 9 | 源侧天花板（戊 8 + 丙 1） | 本文 §2 实测 |
| **C 待裁决** | 16 | 裁决规则已立（ADR-007），尚未重建生效 | ADR-007 + 本文 §3 实测 |

`27 = 2 + 9 + 16`；`17 SOURCE_CONFLICT = 16(C) + 1(B丙)`；`10 FACTS_INCOMPLETE = 2(A) + 8(B戊)`。

> 上表是 **`d490c63784d3…`** 的读数。2026-09-17 的重建发布后，C 栏 16 只全部
> 离开 `UNTRUSTED`，同一个口径读作 `27 → 11`（`= 2(A) + 8(B戊) + 1(B丙)`）；
> A、B 两栏一只未变。见 §3 末「2026-09-17 实测」。

> **A 与 B 不可合并称为"已裁定"。** 只有 A 的 2 只背后有一条已接受的 ADR；
> B 的 9 只目前只是"知道为什么"，尚无签字认定。把两者并称，会把一个尚未发生的
> 决定记成已发生。

## 1. A 栏 —— ADR-006 fail-closed 残留（2 只）

| 符号 | 隔离行 | 窗口规则分支 | 判决 |
| --- | --- | --- | --- |
| `000503.SZ` | 1 行：ann=1996-06-19，无 record_date、无 ex_date，implemented | 分支 3（公告在窗口前 + 已实施）→ 排除 | `FACTS_INCOMPLETE` |
| `000629.SZ` | 1 行：rec=2005-11-03，无 ex_date，implemented | 分支 2（登记日早于窗口 13 天以上）→ 排除 | `FACTS_INCOMPLETE` |

两只的全部隔离行都被窗口规则排除，因此 `quarantine_reasons` 为空；同时
`filter_corporate_actions_to_window` 仍保留这类"无 ex_date 的 implemented 行"
（`corporate_actions.py:275`，为让对账能标记它），故 endpoint outcome 读作
`success_with_events`、`empty=False`。两者叠加，判决落到
`data_pipeline.py:2392` 的兜底。

**这不是缺陷。** ADR-006 §Consequences 已预判这 2 只：

> Symbols that hold no accepted in-window fact still read `FACTS_INCOMPLETE` after
> the exclusion — 2 of the 46 flip only as far as that, which is the correct
> outcome, not a regression.

理由是该 ADR 自陈的残留风险：分支 3 是**政策放宽**而非证明（那行连 `ex_date`
都没有）。若让这一格读 `VERIFIED_EMPTY`，等于把"未能确证"写成"已确证为空"，
正是 ADR-006 拒绝的静默替换。**A 栏的代价就是 ADR 有意选择的对价，不应作为
待修项处理。**

## 2. B 栏 —— 源侧天花板（9 只 = 戊 8 + 丙 1）

这 9 只的隔离行**有**窗口内日期，是被窗口规则**保留**的，判 UNTRUSTED 正确。

### 戊：缺 `ex_date`，登记日在窗口内（8 只）

| 符号 | announcement_date | record_date | ex_date | status |
| --- | --- | --- | --- | --- |
| `000656.SZ` | 2025-09-09 | 2025-09-11 | 缺 | implemented |
| `000792.SZ` | 2020-03-25 | 2020-03-30 | 缺 | implemented |
| `000793.SZ` | 2026-06-12 | 2026-06-18 | 缺 | implemented |
| `002131.SZ` | 2017-12-08 | 2017-12-13 | 缺 | implemented |
| `002157.SZ` | 2023-12-02 | 2023-12-07 | 缺 | implemented |
| `002310.SZ` | 2024-12-24 | 2024-12-27 | 缺 | implemented |
| `002608.SZ` | 2016-12-22 | 2016-12-27 | 缺 | implemented |
| `600733.SH` | 2018-08-20 | 2018-02-01 | 缺 | implemented |

登记日在窗口内、除权日缺失 —— 这是一个**可能已在窗口内落地但无法入账**的事件，
`_reject_reason` 返回 `REASON_INCOMPLETE`，窗口规则分支 2 保留。判 UNTRUSTED 正确。
（`000656.SZ`、`000793.SZ` 另各带 1–2 条窗口前的老行，均被分支 2/3 排除，不影响判决。）

### 丙：送股／转增拆分分歧（1 只）

`002269.SZ` 除权日 2015-05-12：cninfo 送股 6.0 + 转增 9.0；eastmoney 送股 5.0 + 转股 10.0。
两者合计同为 15.0（每股 1.5），对 `(1 + b + c)` 经济上等价，但 `_same_facts`
逐字段精确比较，故判 `SOURCE_CONFLICT`。

#### 候选规则（未采纳，仅登记）

**经济等价即接受** —— 当两源只在 `bonus`/`capitalization` 的**拆分**上分歧、
而 `1 + bonus + capitalization` 相等时，视为一致并改判接受。

- **状态**：候选。**未立 ADR、未改代码**；丙类此刻仍需签字或另找源。
- **收益面**：仅 `002269.SZ` 一只。经济上确实无差别 —— 复权因子只依赖
  `1 + b + c`，拆分方式不影响任何下游计算。
- **代价**：要放宽 `_same_facts` 的逐字段精确语义，而该语义正是甲/乙/丁三类的
  判定基础。引入"部分字段可折算"的概念后，必须明确哪些字段可折算、折算容差
  多大，否则会把甲类（漏报一条同日事件）也一并放行。
- **若推进**：需新 ADR，并覆盖 `_same_facts` 的既有测试。

## 3. C 栏 —— 待裁决（16 只，待 TDX 接入 ADR）

| 类 | 只数／事件数 | 分歧性质 | TDX 实测裁决 |
| --- | --- | --- | --- |
| 甲 | 11 只／14 事件 | eastmoney 漏报同日一条事件 | **14/14 等于 cninfo 合计** |
| 乙 | 3 只／8 事件 | 金额实质分歧 | **8/8 等于 cninfo** |
| 丁 | 2 只／2 事件 | 1e-6 级数字差 | **2/2 逐位仲裁，各倒向一家** |

符号与事件数：

- 甲（14）：`002352.SZ`(1)、`002709.SZ`(1)、`600188.SH`(3)、`600600.SH`(1)、
  `600803.SH`(2)、`601601.SH`(1)、`601808.SH`(1)、`601828.SH`(1)、`601898.SH`(1)、
  `601966.SH`(1)、`603259.SH`(1)
- 乙（8）：`600025.SH`(1)、`600900.SH`(1)、`600989.SH`(6)
- 丁（2）：`300124.SZ`(1)、`301308.SZ`(1)

丁类两例证明 TDX 不是任何一方的复读机（`300124.SZ` 2016-05-18 倒向 cninfo、
`301308.SZ` 2026-06-02 倒向 eastmoney），因此裁决不能用"cninfo 优先"简化。
TDX 不能救的只有 B 栏的 9 只（戊类缺 `announcement_date`/`record_date`/`status`，
`_reject_reason` 仍返回 `REASON_INCOMPLETE`）。

**裁决规则已立，但尚未生效。** [ADR-007](../adr/007-corporate-action-third-party-arbitration.md)
（status: accepted，2026-09-16，经 owner 确认）已把 TDX 定为第三票裁决器并确立了比较口径；
裁决器**默认关闭**（`sources.yml` 的 `tdx.enabled: false`），且 `CURRENT`
（`d490c63784d3…`）是启用之前发布的版本。因此 C 栏的 16 只**在当前数据集里
仍读作 `UNTRUSTED`** —— 规则落地与数据落地是两件事，本记录不把后者写成已发生。
启用后的预期结果（16 只转 `VERIFIED`、验收 FAIL 27→11）是 ADR-007 的预测，
需由一次显式启用的重建来证实，而非由本记录认定。

### 2026-09-17 实测（上段的「尚未生效」已被取代）

一次显式启用 `tdx.enabled: true` 的全窗口重建已跑完并发布，
`CURRENT = 01c74bee15b4…`（`run_id = data_update_49caeff55f62`）。从盘上按 §4 口径复算：

| 读数 | `d490c63784d3…` | `01c74bee15b4…` |
| --- | --- | --- |
| `UNTRUSTED` | 27 | **11** |
| ├ `SOURCE_CONFLICT` | 17 | **1** |
| └ `FACTS_INCOMPLETE` | 10 | 10 |
| `VERIFIED` | 625 | **641** |
| `VERIFIED_EMPTY` | 7 | 7 |
| `corporate_action` 行数 | 6872 | **6896** |

- **C 栏 16 只全部转 `VERIFIED`**，与本文 §3 的预测集合逐只相同；
  丙类的 `002269.SZ` 仍 `UNTRUSTED`（裁决器不予裁决），A 栏 2 只、B 栏 8 只戊类
  一只未变 —— 即 `11 = 2(A) + 1(B丙) + 8(B戊)`，本文 §1/§2 的归属未变。
- 验收 `corporate_action_evidence` 的失败项由 **27 条降至 11 条**；同一批检查里
  其余 8 项在两个版本上均为 PASS（8 PASS / 1 FAIL）。**没有放松任何门禁**：
  这个 11 是新发布数据集自己的读数，不是把 27 判成了通过。
- 落账证据：新增 24 行 `source` 值 —— `cninfo+tdx` 23 行、`eastmoney+tdx` 1 行，
  正好对应甲 14 / 乙 8 / 丁 2 个事件。丁类两例方向相反
  （`300124.SZ` 2016-05-18 倒向 cninfo、`301308.SZ` 2026-06-02 倒向 eastmoney），
  与 §3 的复算一致，也再次说明裁决不能简化为「cninfo 优先」。
- 重建发布后 `tdx.enabled` 已回 `false`（ADR-007 的默认值），
  `tests/unit/test_tdx_arbiter.py` 的 `test_the_shipped_config_keeps_the_arbiter_off` 复通过。

**以上是测量结果，不是签字。** A、B 两栏的认定仍留给 owner。

**复算已复核预测。** 用生产裁决器 `TdxXdxrArbiter` 逐条重跑上表的 25 个事件
（输入是已发布 quarantine 行，TDX 侧为 2026-09-16 实拉），判词分布为
**cninfo 23 / eastmoney 1 / 不予裁决 1**，与 ADR-007 Context 的预测逐项相符；
倒向 eastmoney 的是 `301308.SZ` 2026-06-02，不予裁决的是 `002269.SZ` 2015-05-12。
逐事件的两侧原始值、TDX 值与判词见
[2026-09-16-corporate-action-arbitration-worklist.csv](2026-09-16-corporate-action-arbitration-worklist.csv)（25 行 × 17 只）。

> **取值口径**：该 CSV 的 per-10 数值取自 `ConflictTerms` 的精确 `Decimal`，
> 不是浮点近似 —— 丁类的判据就在第 7 位有效数字上（`301308.SZ` 的
> `9.907442660` vs `9.9074420`），四舍五入到 6 位会把它抹掉。

## 4. 复算方法

```python
import pandas as pd
V = "d490c63784d3365d3306329c74f4ec40423bb2a5776ec6be379ff91650172c9d"
P = f"project/data/standardized/{V}"
cov = pd.read_parquet(f"{P}/corporate_action_coverage.parquet")
q   = pd.read_parquet(f"{P}/corporate_action_quarantine.parquet")
u = cov[cov.status == "UNTRUSTED"]          # 必须用 == "UNTRUSTED"
u.groupby("reason").size()
# => FACTS_INCOMPLETE 10 / SOURCE_CONFLICT 17
```

> **换一个版本只需换 `V`。** 2026-09-17 的重建版是
> `V = "01c74bee15b54cf521c81a46021e37bb4354a51a44898b97dc7ddde18e245ee5"`，
> 同一段代码同口径读作 `FACTS_INCOMPLETE 10 / SOURCE_CONFLICT 1`。
> 两个版本都在盘上，谁都改不了谁 —— 这正是内容寻址要的效果。

**口径陷阱**：不要用 `status != "VERIFIED"` 计数 —— 那会多算 7 只
`VERIFIED_EMPTY`（可信的"无事件"），得到 34 而非 27。

## 5. 本记录未做的事

- 未改任何 `src/`、配置或数据；未重新发布数据集；未改 ADR-006（已接受的决策
  以新 ADR 取代，不做静默改写）
- 未放松任何门禁：`corporate_action_evidence` 仍为 FAIL，计数仍为 27
  （2026-09-17 起该计数读作 11 —— 那是**新发布的一个数据集**，不是本记录或
  任何门禁放宽的结果；本记录始终只读）
- 未代签：A/B 两栏的签字与 C 栏的裁决均留给 owner
