# Panda 嫁接 · G0 决策正名与 §7.5 覆盖证据模型实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地总规格的 G0 文档包（许可证核验记录 + ADR-022）与 §7.5 覆盖证据模型扩展（新 not_fetched 理由词汇、前缀/尾段分区校验、preflight 窗口判定、每表必记录与 manifest 自身集合判定、源不可用 carry 语义），使历史起点晚于验收锚点的新表可以在不放宽任何门禁的前提下发布并被正式研究消费。

**Architecture:** 词汇与分区规则全部落在 `data_model/fetch_coverage.py` 的纯函数层（`FetchSegment` + `validate_table_fetch_coverage` + 新增 `table_unsupported_window_tables`），runner 预检与验收检查只消费这些函数，不各自重译规则；"当前注册表齐全"从验收期检查（`_check_required_tables`）移到发布期门禁——装在 **`DatasetPublisher.publish`**（`data_model/dataset.py`），因为仓库有 13 个离线入口绕过 `data_pipeline.update()` 直接调它；`pipeline_contract_version` 保持 `1`，旧 manifest 按旧形态兼容读取。

**Tech Stack:** Python 3.12（`/home/ji/miniconda3/envs/sq312/bin/python`）、pandas、pytest。无新依赖。

**Spec:** [docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md](../specs/2026-09-29-panda-data-loop-grafting-design.md) §5.1/§5.2/§7.5/§7.3 尾段/§7.4/§11。本计划是总路线图的第一阶段计划（P0 部分 + P2a 全部）。

## Global Constraints

- 解释器 `/home/ji/miniconda3/envs/sq312/bin/python`（pandas 3.0.5）；跑点名测试文件，不跑裸 `pytest`（integration 全量 ≈18.5 分钟）。
- **`pipeline_contract_version` 保持 `1`**（spec §7.3）：新形态走"加 key、旧形态兼容读取、处置方式是重发布"，不升版。
- **不弱化任何门禁**（spec §7.5 末段）：不得通过放宽 `STANDARDIZED_SCHEMAS`、跳过验收或伪造覆盖让检查通过；被拒发布仍是记录在案的证据。
- **连续性游标必须从 `anchor_start` 起**：`fetch_coverage.py:162` 的现网语义是 `cursor = anchor_start`。改成从 `ordered[0].window_start` 起，会让"历史起点晚于锚点、却没申明 `history_begins_after_anchor` 前缀"的表静默通过——那正是本次改动最该拦住的一类（见 Task 4 Step 3 的 `_contiguity_violations`）。
- 每个行为变更先写失败测试、观察其按预期理由失败，再做最小修复（tests.md）。
- 凭据零容忍：不进代码、配置、日志、fixture、报告、运维记录。
- 本期零新供应商、零新凭据（spec 附录 A）；本计划全部离线，无联网步骤。
- 保护在途 WIP：工作区已修改的 `src/stock_quant/cli.py`、`src/stock_quant/reporting/html.py`、`templates/experiment.html.j2`、`RUNBOOK.md`、两份 spec 与四个测试文件——不覆盖、不回退、不暂存；只 `git add` 本任务文件。
- ADR 编号：`docs/adr/` 现有两个 020 文件（`020-batched-validation-channel.md` 与 `020-suspension-proof-grid-is-the-fetch-window-and-its-seam.md`，编号冲突属实），而 **021 是空号**。本计划采用 **022**（跳过 021）；追加索引前先把这两个事实报告给 owner，若 owner 要求顺号改用 021，全文替换 022→021 并在提交信息里注明。022 若已被占用，以 `docs/adr/DECISIONS_INDEX.md` 的下一空闲编号替代。
- 治理行宽：ADR 文件 ≤ 400 行；提交信息英文，结尾 `Co-Authored-By: Claude Code <no-reply@anthropic.com>`。

## 开工前必须知道的实现形态

1. `src/stock_quant/data_model/fetch_coverage.py` 现状（本计划 Task 3/4/5 的改造对象）：
   - `FetchSegment(table, kind, window_start, window_end, reason=None)`，`kind ∈ {fetched, carried, not_fetched}`；只有 `not_fetched` 可带 `reason`，理由词汇今天只有 `operator_explicit_window`。
   - `validate_table_fetch_coverage(payload, *, anchor_start, published_end)` 返回 `(code, details)` 列表；现有规则：至少一张表；段在窗口内；`fetched/carried` 段按**日历日**连续铺满 `[anchor_start, published_end]`（cursor 用 `window_end + timedelta(days=1)` 推进）；带 `not_fetched` 的表走"全有或全无"分支——任一 `fetched/carried` 混入即 `not_fetched_mixed_with_fetch`，缺理由即 `not_fetched_reason_missing`，然后 `continue`（跳过的表按设计不覆盖任何区间）。
   - 测试风格见 `tests/unit/test_fetch_coverage.py`：模块级 `ANCHOR = date(2021, 1, 4)`、`END = date(2026, 8, 28)`、`_payload()` 帮手，断言用违规码列表。
2. `not_fetched_input_tables`（fetch_coverage.py:77）已被 `research/runner.py:1220` 的 `_preflight_table_tiers` 消费：RESEARCH 模式给 `table_not_fetched` 违规，ENGINEERING 豁免并打 RESEARCH-ONLY 标签。本计划的窗口判定走同一条路（Task 5），不另开预检。
3. `_check_required_tables`（acceptance/checks.py:277）今天用**当前代码**的 `STANDARDIZED_SCHEMAS` 减 manifest 的 tables；`_check_table_fetch_coverage`（checks.py:440）只校验**被记录的**表——"漏记即无证"正是 spec §7.5.5 要堵的洞。
4. `data_pipeline.py:1641` `_read_baseline` 已能 carry 整表与 coverage 帧；`:1695` 的 quarantine 空表先例（预迁移数据集无该表时播 canonical 空 frame 继续更新）就是 spec §7.5.4 引用的"空表先例"。
5. 两套词汇不得互串（spec §7.5.2）：本计划新增的是 **fetch-segment 理由**（`history_begins_after_anchor`、`source_disabled`、`source_unavailable`）；coverage 表的 `CoverageReason.SOURCE_FETCH_FAILED`（`data_model/corporate_action_coverage.py`）是另一套，不得互相顶替。
6. `history_begins_after_anchor` 的段是**唯一前缀**：`[acceptance_start, supported_start - 1]`，与后续首个 `fetched/carried` 段日历日连续；`source_disabled`/`source_unavailable` 是**尾段**：只能出现在末尾且 `window_end == published_end`（spec §7.5.1/2/4、§11"新表源不可用"行）。
7. **`ExperimentSpec` 的位置与窗口字段名**（Task 5 接线直接依赖，别找错文件）：类定义在 `src/stock_quant/research/spec.py:154`，**不在** `research/models.py`（该文件存在但是另一回事）。运行窗口是嵌套的 `DateRange`：`spec.date_range.start_date` / `spec.date_range.end_date`（spec.py:176；`DateRange` 定义在 spec.py:75）。**不存在** `spec.start_date` / `spec.end_date`。
8. runner 预检点（`research/runner.py:1220-1268` 的 `_preflight_table_tiers`）已有 `manifest`、`input_tables`、`mode` 局部变量可直接接线；该预检在 `runner.py:821` 执行，**早于** `_walk_forward_pipeline`（:836）里的折计划构建。
9. `DatasetPublisher.publish`（`data_model/dataset.py:100-110`）本身就在调 `evaluate_publication(report, table_tiers=...)`，不通过即 `raise PublicationBlocked`；`QualityReport` 是 frozen dataclass、`issues` 是 tuple。因此 Task 6 的发布期门禁接在这里可以一次性覆盖**全部**发布路径（`data update` + 13 个离线 `project/*.py`），代价是要重建一个新的 `QualityReport` 而不是原地 append。

## 文件结构

**新增**

| 文件 | 职责 |
| --- | --- |
| `docs/operations/2026-10-<date>-panda-license-verification.md` | §5.1 许可证核验记录（dated evidence） |
| `docs/adr/022-panda-graft-source-scope-and-basic-factor.md` | ADR-022（含 §7.5 词汇裁定） |

**修改**

| 文件 | 改动 |
| --- | --- |
| `src/stock_quant/data_model/fetch_coverage.py` | 三个新理由常量；`FetchSegment` 接受；分区校验改写（先位置后对齐）；`table_unsupported_window_tables` |
| `src/stock_quant/research/runner.py` | `_preflight_table_tiers` 增加窗口判定违规 `table_history_start_after_window` |
| `src/stock_quant/research/acceptance/checks.py` | `_check_table_fetch_coverage` 每表必记录；`_check_required_tables` 改按 manifest 自身集合 |
| `src/stock_quant/data_model/dataset.py` | **发布期"当前注册表齐全"门禁（`DatasetPublisher.publish`）**——覆盖 update 与全部离线发布入口 |
| `src/stock_quant/data_quality/models.py`、`gates.py` | 新码 `missing_registered_table`、`table_emptied_by_fetch` 及两个阻断码集登记 |
| `src/stock_quant/data_pipeline.py` | 源不可用 carry/尾段语义；清空已有表→本轮阻断 |
| `docs/adr/DECISIONS_INDEX.md` | 只追加 022 一行 |
| `tests/unit/test_fetch_coverage.py` | 词汇、分区规则与"晚起无前缀仍是 gap"回归用例 |
| `tests/unit/test_acceptance_checks.py` | 两项验收检查的新语义用例 |
| `tests/unit/test_dataset_publisher.py` | 发布期注册表门禁用例（无则新建） |
| `tests/unit/test_table_tier_preflight.py` | 窗口判定用例 |
| `tests/integration/test_data_pipeline.py` | carry/尾段、清空事故与 `data validate` 用例 |

---

### Task 1: 许可证核验记录

**Files:**
- Create: `docs/operations/2026-10-<date>-panda-license-verification.md`

**Interfaces:**
- Produces: 带当日 commit/hash 的许可证取证记录，ADR-022（Task 2）引用它的路径。

- [ ] **Step 1: 实查 pandaAI 侧许可证状态**

Run: `cd /home/ji/work/program/pandaAI && git rev-parse HEAD && find . -maxdepth 3 -iname "LICENSE*" -not -path "./.git/*"`
对根仓、`panda_quantflow`、`panda-data`、`panda-data-skill` 逐一记录 HEAD hash 与 LICENSE 文件有无/类型。预期与 spec §5.1 的旧结论一致（AGPL-3.0 / 无许可证），**以当日实查为准**，不复制规格旧文。

- [ ] **Step 2: 写核验记录**

```markdown
# Panda 侧许可证核验（Panda 数据闭环嫁接 · Phase 0）

- 日期：<date>
- 核验人：operator + 实施agent
- 核验对象与结果（当日实查，含 HEAD hash）：
  - pandaAI 根仓：<hash>，<LICENSE 状态>
  - panda_quantflow：<hash>，<LICENSE 状态>
  - panda-data：<hash>，<LICENSE 状态>
  - panda-data-skill：<hash>，<LICENSE 状态>

## 裁定

- 选择 **clean-room 重写**（spec §5.1）。
- 允许记录：端点名、字段名、单位、输入输出样例、观察到的行为。
- 禁止复制：函数体、注释、异常文案、前端 bundle、模板、测试 fixture。
- 行为与字段语义优先引用供应商公开文档；Panda 代码只用于验证已观察到的兼容行为。
- 新实现的评审必须能仅凭规格、供应商公开文档和 Stock 测试解释其来源。
```

- [ ] **Step 3: 治理校验并提交**

Run: `python -m pytest tests/unit/test_context_governance_docs.py -q` → Expected: PASS

```bash
git add docs/operations/2026-10-<date>-panda-license-verification.md
git commit -m "docs(operations): record the panda-side license verification evidence"
```

---

### Task 2: ADR-022（源范围裁定、单候选裁决与 §7.5 词汇）

**Files:**
- Create: `docs/adr/022-panda-graft-source-scope-and-basic-factor.md`
- Modify: `docs/adr/DECISIONS_INDEX.md`（只追加一行）

**Interfaces:**
- Consumes: Task 1 的核验记录路径。
- Produces: Task 3 引入的三个理由常量、Task 6 的每表必记录/manifest 自身集合语义、`pipeline_contract_version=1` 裁定的 ADR 依据。

- [ ] **Step 1: 先报告 ADR-020 编号冲突**

`ls docs/adr/020-*` 显示两个文件；同时确认 **021 是空号**（`ls docs/adr/021-*` 无结果）。把这两点报告 owner 并确认 022 仍空闲；若 owner 要求顺号，按 Global Constraints 全文替换 022→021。

- [ ] **Step 2: 写 ADR 正文**

```markdown
# ADR-022: Panda 嫁接的源范围裁定与 basic_factor 契约

日期：2026-10-<date>
状态：accepted
相关：spec 2026-09-29-panda-data-loop-grafting-design（附录 A/B、§6、§7）；许可证核验
docs/operations/2026-10-<date>-panda-license-verification.md

## 背景

pandaAI 的数据闭环能力要嫁接进 Stock。其数据源三路（RiceQuant/xtquant/tqsdk）
在本机均不可得（无账号、无许可、无客户端，见附录 A 取证），而 Stock 的治理边界
（单一写入链、内容寻址发布、fail-closed）不允许为吸收能力而引入第二套真相。

## 决策

1. **本期零新供应商**：全部取数走既有 Tushare（relay/官方 transport）、akshare、
   星耀通道；tdx 仍只作公司行为仲裁器。RiceQuant 等源的再进入条件是门禁
   （spec 附录 B），不是待办。
2. **多指数成分单路径**：Tushare `index_weight` 月度快照，attested-boundary
   近似契约；`announcement_date` = 快照日 = `raw_effective_from`，不得声称
   官方公告日或精确生效日；差分生成的加入/退出记 `snapshot_observed_change`。
3. **`basic_factor` 只存 Stock 尚无唯一事实来源的字段**：`market_cap`/
   `turnover_rate`（单位换算以探针实测为准，本 ADR 成文时未实测，探针结论以
   dated 补注回写本节）；不复制 `daily_bar` 的任何行情列；不提供 Panda 十列
   宽表。初始 tier `research_only`；升 `anchored` 须另有独立锚点证据。
4. **单候选裁决**：本期只有 `daily_basic` 一个候选，不是"比较后胜出"；任一
   裁决项（transport 可绑定、窗口覆盖、配额、单位实测）失败即不发布该表。
5. **§7.5 覆盖理由词汇**：fetch-segment 理由新增 `history_begins_after_anchor`
   （唯一前缀 `[acceptance_start, supported_start-1]`，不得出现在中间/尾部）、
   `source_disabled`（本次未启用）、`source_unavailable`（启用但不可达，尾段）；
   与 coverage 表的 `CoverageReason.SOURCE_FETCH_FAILED` 是两套词汇，不得互串。
   preflight 新增 `table_history_start_after_window`：运行窗口早于表支持起点或
   落入未支持段时 RESEARCH run fail closed，ENGINEERING 豁免并标注。
6. **`build_config.table_lineage` 引入**（table → transport → raw snapshot 证据行），
   发布时与 `primary_transport` 声明核对；旧 manifest（无该键）按旧形态兼容读取。
7. **`pipeline_contract_version` 保持 `1`**：`required_table_coverage` 改按 manifest
   自身表集合判定，"当前注册表齐全"移到**发布期门禁**（`DatasetPublisher.publish`，
   `missing_registered_table`，FATAL）；升 contract version 会让每个已发布数据集在
   `source_role_health` 上失败，等于把问题搬家并放大。
   **本条的语义变更必须记录在案**：`required_table_coverage` 这个码名不变，含义从
   "对当前注册表判缺表"变成"对 manifest 自身记录集判"。同一版本在新旧代码下复审
   会得到同一码名的不同结论。门禁装在发布器（而不是 `data_pipeline.update()`）
   是因为仓库有 13 个离线发布入口直接调 `publish`，只装 `update()` 会留一条
   13 条路宽的口子。
8. **`basic_factor_coverage`**：`core`、`coverage_shape=none`；词汇复用
   `corporate_action_coverage` 的 `CoverageStatus/CoverageReason`；
   `VERIFIED_EMPTY` 必须有 security master 上市/退市证据支撑（对稠密因子事实，
   "应有而无"是 `UNTRUSTED(FACTS_INCOMPLETE)`，不是可信空值）。
9. **不进星耀验证 lane**：`basic_factor` 与既有 CSI300 官方事实不进入星耀验证
   lane 或 ADR-013/014 仲裁排序。

## 后果

- 探针结论（单位、空值、可得性时点、节奏）以 dated operations evidence 回写
  本 ADR 的补注节；与假设不符时实现随探针调整。
- **预热窗口内的截断本期不判**：`table_history_start_after_window` 的判定窗口取
  `ExperimentSpec.date_range`，不含 walk-forward 的 `warmup_calendar_start`
  （预检早于折计划构建，前移会改变既有失败归属）。P2c 落地 `basic_factor` 时必须
  把预热起点并入判定窗口并补失败测试。
- 发布器新增 FATAL `missing_registered_table` 与 `table_emptied_by_fetch` 后，
  既有 13 个离线发布入口一并受约束；核对发现"只发一部分表"的脚本须补表或由 owner
  另行裁定，不得放宽门禁。
- 未来第二个源获准再进入时，本 ADR 的单候选裁决即失效，须重新多源裁决。
```

- [ ] **Step 3: 追加索引行**

读 `docs/adr/DECISIONS_INDEX.md` 尾部，按现有行格式**只追加**：

```markdown
| [022](022-panda-graft-source-scope-and-basic-factor.md) | panda 嫁接源范围裁定与 basic_factor 契约 | 2026-10-<date> | accepted |
```

- [ ] **Step 4: 治理校验并提交**

Run: `python -m pytest tests/unit/test_context_governance_docs.py -q` → Expected: PASS

```bash
git add docs/adr/022-panda-graft-source-scope-and-basic-factor.md docs/adr/DECISIONS_INDEX.md
git commit -m "docs(adr): adopt ADR-022 panda graft source scope and coverage vocabulary"
```

---

### Task 3: fetch-segment 新理由词汇

**Files:**
- Modify: `src/stock_quant/data_model/fetch_coverage.py:20-24`（理由词汇区）、`:42-53`（`FetchSegment.__post_init__`）
- Test: `tests/unit/test_fetch_coverage.py`

**Interfaces:**
- Produces（后续任务与 P2c 依赖的精确名字）:
  - `NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR = "history_begins_after_anchor"`
  - `NOT_FETCHED_SOURCE_DISABLED = "source_disabled"`
  - `NOT_FETCHED_SOURCE_UNAVAILABLE = "source_unavailable"`
  - `FetchSegment(table, kind, window_start, window_end, reason=None)` 接受上述三者为 `not_fetched` 理由；未知理由仍 `ValueError`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/unit/test_fetch_coverage.py`；文件已 import `FetchSegment`、`KIND_NOT_FETCHED`、`pytest`、`date`）

```python
def test_history_begins_after_anchor_is_a_valid_reason():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
    )

    segment = FetchSegment(
        "basic_factor",
        KIND_NOT_FETCHED,
        date(2021, 1, 4),
        date(2023, 12, 29),
        reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
    )
    assert segment.reason == "history_begins_after_anchor"


def test_source_unavailable_is_a_valid_reason():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_SOURCE_UNAVAILABLE,
    )

    segment = FetchSegment(
        "daily_bar",
        KIND_NOT_FETCHED,
        date(2026, 8, 28),
        date(2026, 8, 28),
        reason=NOT_FETCHED_SOURCE_UNAVAILABLE,
    )
    assert segment.reason == "source_unavailable"


def test_source_disabled_is_a_valid_reason():
    from stock_quant.data_model.fetch_coverage import NOT_FETCHED_SOURCE_DISABLED

    segment = FetchSegment(
        "daily_bar",
        KIND_NOT_FETCHED,
        date(2026, 8, 28),
        date(2026, 8, 28),
        reason=NOT_FETCHED_SOURCE_DISABLED,
    )
    assert segment.reason == "source_disabled"


def test_an_unknown_reason_is_still_rejected():
    with pytest.raises(ValueError, match="unknown not_fetched reason"):
        FetchSegment(
            "daily_bar",
            KIND_NOT_FETCHED,
            date(2026, 8, 28),
            date(2026, 8, 28),
            reason="supplier_mood",
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_fetch_coverage.py -q -k "history_begins or source_"`
Expected: FAIL — `ImportError: cannot import name 'NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR'`

- [ ] **Step 3: 最小实现**

`src/stock_quant/data_model/fetch_coverage.py`，把 `:20-24` 的词汇区替换为：

```python
#: An explicit --start/--end window deviated from the table's contract fetch
#: window (spec D5.2): the table skipped fetching this round, whole-table.
NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW = "operator_explicit_window"

#: The table's own history starts later than the acceptance anchor (spec
#: §7.5.1): the ONLY legal prefix shape, spanning exactly
#: [acceptance_start, supported_start - 1].
NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR = "history_begins_after_anchor"

#: The source was not enabled this round (spec §7.5.2) -- a tail segment.
NOT_FETCHED_SOURCE_DISABLED = "source_disabled"

#: The source was enabled but unreachable this round (spec §7.5.2) -- a tail
#: segment; the carried baseline keeps every fact before it (spec §7.5.4).
NOT_FETCHED_SOURCE_UNAVAILABLE = "source_unavailable"

_REASONS = frozenset(
    {
        NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
        NOT_FETCHED_SOURCE_DISABLED,
        NOT_FETCHED_SOURCE_UNAVAILABLE,
    }
)
```

`__post_init__` 无需改动（`:52` 的 `reason not in _REASONS` 检查自动放行新词）。

- [ ] **Step 4: 跑测试确认通过，并跑邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_fetch_coverage.py -q`
Expected: PASS（新增 4 项 + 既有 7 项全绿——新词今天只在构造层放行，`validate` 的分区规则是 Task 4 的事，既有 `not_fetched` 用例仍走旧分支不受影响）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_model/fetch_coverage.py tests/unit/test_fetch_coverage.py
git commit -m "feat(coverage): add history-begins and source-unavailable fetch reasons"
```

---

### Task 4: 分区校验——history 唯一前缀、source 尾段

**Files:**
- Modify: `src/stock_quant/data_model/fetch_coverage.py:100-179`（`validate_table_fetch_coverage` 整体替换）
- Test: `tests/unit/test_fetch_coverage.py`

**Interfaces:**
- Consumes: Task 3 的三个理由常量。
- Produces: `validate_table_fetch_coverage` 的新语义（Task 5/6 与 P2c 依赖）——违规码集合 = 既有四码（`table_fetch_coverage_missing`、`table_fetch_coverage_malformed`、`fetch_coverage_out_of_window`、`fetch_coverage_gap`、`not_fetched_mixed_with_fetch`、`not_fetched_reason_missing`）**加一个** `not_fetched_prefix_misaligned`。

- [ ] **Step 1: 写失败测试**（追加；`KIND_CARRIED`、`KIND_FETCHED` 文件已 import）

```python
def _zoned_payload(prefix_end, middle_kind, middle_start, tail=None):
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
    )

    segments = [
        FetchSegment(
            "basic_factor", KIND_NOT_FETCHED, ANCHOR, prefix_end,
            reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
        ),
        FetchSegment("basic_factor", middle_kind, middle_start, END),
    ]
    if tail is not None:
        segments.append(tail)
    return to_build_config_payload({"basic_factor": segments})


def test_history_prefix_then_fetched_passes():
    payload = _zoned_payload(
        date(2023, 12, 29), KIND_FETCHED, date(2023, 12, 30)
    )
    assert validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END
    ) == []


def test_history_prefix_must_start_at_the_anchor():
    payload = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED,
                    date(2021, 6, 1), date(2023, 12, 29),
                    reason="history_begins_after_anchor",
                ),
                FetchSegment("basic_factor", KIND_FETCHED, date(2023, 12, 30), END),
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "not_fetched_prefix_misaligned" in codes


def test_history_reason_in_the_middle_is_rejected():
    payload = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment("basic_factor", KIND_FETCHED, ANCHOR, date(2024, 1, 5)),
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED,
                    date(2024, 1, 8), date(2024, 1, 12),
                    reason="history_begins_after_anchor",
                ),
                FetchSegment("basic_factor", KIND_FETCHED, date(2024, 1, 15), END),
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "not_fetched_mixed_with_fetch" in codes


def test_history_reason_in_the_tail_is_rejected():
    payload = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment("basic_factor", KIND_FETCHED, ANCHOR, date(2024, 1, 5)),
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED,
                    date(2024, 1, 8), END,
                    reason="history_begins_after_anchor",
                ),
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "not_fetched_mixed_with_fetch" in codes


def test_source_unavailable_tail_after_carried_passes():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
        NOT_FETCHED_SOURCE_UNAVAILABLE,
    )

    payload = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED, ANCHOR, date(2023, 12, 29),
                    reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
                ),
                FetchSegment(
                    "basic_factor", KIND_CARRIED, date(2023, 12, 30), END
                ),
            ],
            "daily_bar": [
                FetchSegment(
                    "daily_bar", KIND_CARRIED, ANCHOR, date(2026, 8, 27)
                ),
                FetchSegment(
                    "daily_bar", KIND_NOT_FETCHED, END, END,
                    reason=NOT_FETCHED_SOURCE_UNAVAILABLE,
                ),
            ],
        }
    )
    assert validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END
    ) == []


def test_source_unavailable_in_the_middle_is_rejected():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_SOURCE_UNAVAILABLE,
    )

    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_FETCHED, ANCHOR, date(2024, 1, 5)),
                FetchSegment(
                    "daily_bar", KIND_NOT_FETCHED,
                    date(2024, 1, 8), date(2024, 1, 12),
                    reason=NOT_FETCHED_SOURCE_UNAVAILABLE,
                ),
                FetchSegment("daily_bar", KIND_FETCHED, date(2024, 1, 15), END),
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "not_fetched_mixed_with_fetch" in codes


def test_operator_window_stays_whole_table():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment(
                    "daily_bar", KIND_NOT_FETCHED, ANCHOR, END,
                    reason="operator_explicit_window",
                ),
            ]
        }
    )
    assert validate_table_fetch_coverage(
        payload, anchor_start=ANCHOR, published_end=END
    ) == []
```

（再加一个**回归用例**，锁住 `_contiguity_violations` 的 `anchor_start` 游标——"晚起但没申明前缀"的表必须依旧报 gap；把游标改回 `ordered[0].window_start` 会让它变绿，同时漏洞复现。）

```python
def test_a_table_starting_late_without_a_prefix_is_still_a_gap():
    payload = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_FETCHED, date(2021, 4, 14), END)
            ]
        }
    )
    codes = [
        code
        for code, _ in validate_table_fetch_coverage(
            payload, anchor_start=ANCHOR, published_end=END
        )
    ]
    assert "fetch_coverage_gap" in codes
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_fetch_coverage.py -q -k "prefix or middle or tail or operator_window"`
Expected: `test_history_prefix_then_fetched_passes` FAIL — `not_fetched_mixed_with_fetch`（现状把任何 not_fetched+fetched 混排拒掉）；其余新用例按同类原因 FAIL。

- [ ] **Step 3: 整体替换 `validate_table_fetch_coverage`**

```python
def validate_table_fetch_coverage(
    payload: object,
    *,
    anchor_start: date,
    published_end: date,
) -> list[tuple[str, dict]]:
    """Structural violations of a recorded ``table_fetch_coverage`` payload.

    Rules (spec §7.5.1/2/4): at least one table recorded; segments parse and
    sit inside [anchor_start, published_end].  A table with no ``not_fetched``
    segment must cover the whole span contiguously (calendar days).
    ``not_fetched`` layouts, by reason:

    - ``operator_explicit_window``: whole-table skip, nothing else mixes in
      (spec D5.2, unchanged).
    - ``history_begins_after_anchor``: the unique prefix -- exactly one
      segment, starting at ``anchor_start`` and ending the day before the
      first fetched/carried segment.
    - ``source_disabled`` / ``source_unavailable``: tail segments -- after
      every fetched/carried (or history-prefix) segment, running to
      ``published_end``.

    Placement is judged before alignment: a ``not_fetched`` segment in the
    wrong zone (a second history segment, a history segment that is not first,
    or an unavailable segment that does not form a contiguous run reaching
    ``published_end``) is ``not_fetched_mixed_with_fetch``.  A correctly placed
    history prefix whose ``window_start`` is not ``anchor_start`` is
    ``not_fetched_prefix_misaligned``.  The fetched/carried segments must still
    tile ``[anchor_start, published_end]`` contiguously, with the prefix and
    tail zones excluded from that coverage.
    """
    if not isinstance(payload, dict) or not payload:
        return [("table_fetch_coverage_missing", {})]
    violations: list[tuple[str, dict]] = []
    for table, raw_segments in sorted(payload.items()):
        if not isinstance(raw_segments, list):
            violations.append(("table_fetch_coverage_malformed", {"table": table}))
            continue
        try:
            segments = [
                FetchSegment(
                    table=str(segment["table"]),
                    kind=str(segment["kind"]),
                    window_start=date.fromisoformat(str(segment["window_start"])),
                    window_end=date.fromisoformat(str(segment["window_end"])),
                    reason=(
                        None
                        if segment.get("reason") is None
                        else str(segment["reason"])
                    ),
                )
                for segment in raw_segments
            ]
        except (KeyError, ValueError, TypeError) as error:
            violations.append(
                (
                    "table_fetch_coverage_malformed",
                    {"table": table, "error_code": type(error).__name__},
                )
            )
            continue
        ordered = sorted(segments, key=lambda item: item.window_start)
        if any(
            segment.window_start < anchor_start
            or segment.window_end > published_end
            for segment in ordered
        ):
            violations.append(("fetch_coverage_out_of_window", {"table": table}))
        not_fetched = [s for s in ordered if s.kind == KIND_NOT_FETCHED]
        if any(s.reason is None for s in not_fetched):
            violations.append(("not_fetched_reason_missing", {"table": table}))
            continue
        if not not_fetched:
            violations.extend(
                _contiguity_violations(table, ordered, anchor_start, published_end)
            )
            continue
        reasons = {s.reason for s in not_fetched}
        if reasons == {NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW}:
            if any(s.kind != KIND_NOT_FETCHED for s in ordered):
                violations.append(
                    ("not_fetched_mixed_with_fetch", {"table": table})
                )
            continue  # a skipped table covers nothing by design
        if any(
            s.reason == NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW for s in not_fetched
        ):
            violations.append(("not_fetched_mixed_with_fetch", {"table": table}))
            continue
        history = [
            s
            for s in not_fetched
            if s.reason == NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR
        ]
        unavailable = [
            s
            for s in not_fetched
            if s.reason in (NOT_FETCHED_SOURCE_DISABLED, NOT_FETCHED_SOURCE_UNAVAILABLE)
        ]
        # Zone placement first (spec §7.5.1/2): at most one history segment,
        # and it must sit in first position; the unavailable segments must form
        # a contiguous run that reaches published_end.  Anything else is an
        # interleaving, not a history prefix.
        if len(history) > 1 or (history and ordered[0] is not history[0]):
            violations.append(("not_fetched_mixed_with_fetch", {"table": table}))
            continue
        if unavailable:
            zone_start = next(
                index
                for index, segment in enumerate(ordered)
                if segment is unavailable[0]
            )
            zone = ordered[zone_start:]
            if any(segment.kind != KIND_NOT_FETCHED for segment in zone) or (
                zone[-1].window_end != published_end
            ):
                violations.append(
                    ("not_fetched_mixed_with_fetch", {"table": table})
                )
                continue
        # Anchor alignment comes second: a correctly placed prefix must still
        # start exactly at the acceptance anchor (spec §7.5.1).
        if history and history[0].window_start != anchor_start:
            violations.append(("not_fetched_prefix_misaligned", {"table": table}))
            continue
        # The fetched/carried middle must still be contiguous from the anchor;
        # the prefix/tail not_fetched segments advance the cursor but are never
        # themselves coverage.
        cursor = anchor_start
        for segment in ordered:
            if segment.kind == KIND_NOT_FETCHED:
                cursor = max(cursor, segment.window_end + timedelta(days=1))
                continue
            if segment.window_start > cursor:
                violations.append(
                    (
                        "fetch_coverage_gap",
                        {"table": table, "gap_start": cursor.isoformat()},
                    )
                )
            cursor = max(cursor, segment.window_end + timedelta(days=1))
        if cursor <= published_end and not unavailable:
            violations.append(
                (
                    "fetch_coverage_gap",
                    {"table": table, "gap_start": cursor.isoformat()},
                )
            )
    return violations


def _contiguity_violations(
    table: str,
    ordered: Sequence[FetchSegment],
    anchor_start: date,
    published_end: date,
) -> list[tuple[str, dict]]:
    """The legacy whole-span walk for tables without not_fetched segments.

    The cursor starts at ``anchor_start``, not at the first segment's own
    start: a table that simply begins late, without declaring a
    ``history_begins_after_anchor`` prefix, must still fail with
    ``fetch_coverage_gap`` (spec §7.5.1; ``fetch_coverage.py``'s pre-change
    semantics).
    """
    violations: list[tuple[str, dict]] = []
    cursor = anchor_start
    for segment in ordered:
        if segment.window_start > cursor:
            violations.append(
                ("fetch_coverage_gap", {"table": table, "gap_start": cursor.isoformat()})
            )
        cursor = max(cursor, segment.window_end + timedelta(days=1))
    if cursor <= published_end:
        violations.append(
            ("fetch_coverage_gap", {"table": table, "gap_start": cursor.isoformat()})
        )
    return violations
```

注意分区判定是**先位置、后对齐**：`ordered[0] is not history[0]` 与 `zone` 检查先跑，所以中间或尾部的 history 段落 `not_fetched_mixed_with_fetch`，只有位置正确但起点不等于 `anchor_start` 的才落 `not_fetched_prefix_misaligned`——Task 4 的两个用例正是按这个语义断言的。`zone_start` 用 `is` 身份查找而非 `list.index`（后者走 dataclass 值相等，两个取值相同的段会取错下标）。`Sequence` 已在文件 import。

- [ ] **Step 4: 跑测试确认通过，并跑既有消费者**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_fetch_coverage.py tests/unit/test_acceptance_checks.py tests/integration/test_window_anchor_regression.py -q`
Expected: PASS（既有 7 项 fetch_coverage 用例语义不变）。

**注意这条预期不足以证明没有放宽**：2026-10-01 实测，既有 7 项用例在"游标从 `ordered[0].window_start` 起"的**错误**版本下同样全绿——它们只覆盖"两段之间的空洞"（`test_gap_between_carried_and_fetched_fails`，`KIND_CARRIED` 到 `2026-08-25` + `KIND_FETCHED` 从 `2026-08-28`），不覆盖"整表晚起"。守住这件事的只有新增的 `test_a_table_starting_late_without_a_prefix_is_still_a_gap`；本步必须确认它在场且为绿。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_model/fetch_coverage.py tests/unit/test_fetch_coverage.py
git commit -m "feat(coverage): zone-validate history prefixes and source-unavailable tails"
```

---

### Task 5: 窗口判定 `table_history_start_after_window`（纯函数 + runner 接线）

**Files:**
- Modify: `src/stock_quant/data_model/fetch_coverage.py`（文件末尾追加 `table_unsupported_window_tables`）
- Modify: `src/stock_quant/research/runner.py:1220-1255`（`_preflight_table_tiers` 内接线）
- Test: `tests/unit/test_fetch_coverage.py`（纯函数）、`tests/unit/test_table_tier_preflight.py`（违规路由）

**Interfaces:**
- Consumes: Task 4 的分区语义。
- Produces: `table_unsupported_window_tables(build_config, input_tables, window_start: date, window_end: date) -> list[str]`；runner 预检新违规码 `"table_history_start_after_window"`（RESEARCH 拒绝；ENGINEERING 豁免并计入 RESEARCH-ONLY 标签族）。

- [ ] **Step 1: 确认窗口字段名（已核实，照用即可）**

`ExperimentSpec` 在 `src/stock_quant/research/spec.py:154`，**不在** `research/models.py`（那是另一个文件，别找错）。运行窗口是嵌套的 `DateRange`：`spec.date_range.start_date` / `spec.date_range.end_date`——**不存在** `spec.start_date`。复核命令：

Run: `grep -n "class ExperimentSpec" -A 25 src/stock_quant/research/spec.py && grep -n "class DateRange" -A 8 src/stock_quant/research/spec.py`

- [ ] **Step 2: 写纯函数的失败测试**（追加到 `tests/unit/test_fetch_coverage.py`）

```python
def test_a_window_before_supported_start_is_unsupported():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
        table_unsupported_window_tables,
    )

    build = to_build_config_payload(
        {
            "basic_factor": [
                FetchSegment(
                    "basic_factor", KIND_NOT_FETCHED, ANCHOR, date(2023, 12, 29),
                    reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
                ),
                FetchSegment("basic_factor", KIND_FETCHED, date(2023, 12, 30), END),
            ]
        }
    )
    assert table_unsupported_window_tables(
        build, ["basic_factor"], date(2022, 1, 4), date(2022, 6, 30)
    ) == ["basic_factor"]
    assert table_unsupported_window_tables(
        build, ["basic_factor"], date(2024, 1, 4), date(2024, 6, 30)
    ) == []


def test_a_window_reaching_into_an_unavailable_tail_is_unsupported():
    from stock_quant.data_model.fetch_coverage import (
        NOT_FETCHED_SOURCE_UNAVAILABLE,
        table_unsupported_window_tables,
    )

    build = to_build_config_payload(
        {
            "daily_bar": [
                FetchSegment("daily_bar", KIND_FETCHED, ANCHOR, date(2026, 8, 27)),
                FetchSegment(
                    "daily_bar", KIND_NOT_FETCHED, END, END,
                    reason=NOT_FETCHED_SOURCE_UNAVAILABLE,
                ),
            ]
        }
    )
    assert table_unsupported_window_tables(
        build, ["daily_bar"], date(2026, 1, 1), END
    ) == ["daily_bar"]
    assert table_unsupported_window_tables(
        build, ["daily_bar"], date(2026, 1, 1), date(2026, 8, 27)
    ) == []
```

- [ ] **Step 3: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_fetch_coverage.py -q -k "unsupported"`
Expected: FAIL — `ImportError: cannot import name 'table_unsupported_window_tables'`

- [ ] **Step 4: 实现纯函数**（fetch_coverage.py 末尾）

```python
def table_unsupported_window_tables(
    build_config: Mapping[str, object],
    input_tables: Sequence[str],
    window_start: date,
    window_end: date,
) -> list[str]:
    """Input tables whose not-fetched zones intersect the run window.

    The not-fetched zones are the ``history_begins_after_anchor`` prefix
    (supported_start = the first fetched/carried segment's window_start) and
    any ``source_disabled``/``source_unavailable`` tail.  A run window that
    reaches into either is preflight-rejected (spec §7.5.3,
    ``table_history_start_after_window``); ENGINEERING keeps its exemption.
    """
    coverage = build_config.get("table_fetch_coverage", {})
    if not isinstance(coverage, Mapping):
        return []
    unsupported: list[str] = []
    for table in input_tables:
        segments = coverage.get(table, [])
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, Mapping):
                continue
            reason = segment.get("reason")
            if reason not in (
                NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
                NOT_FETCHED_SOURCE_DISABLED,
                NOT_FETCHED_SOURCE_UNAVAILABLE,
            ):
                continue
            start = date.fromisoformat(str(segment["window_start"]))
            end = date.fromisoformat(str(segment["window_end"]))
            if window_start <= end and window_end >= start:
                unsupported.append(table)
                break
    return unsupported
```

（前缀与尾段的判定统一为"窗口与该 not_fetched 段相交"——前缀语义由 Task 4 保证该段只能覆盖锚前区间，这里无需重算 supported_start。）

- [ ] **Step 5: runner 接线的失败测试**

`tests/unit/test_table_tier_preflight.py` 已有 `table_tier_violations` 的纯函数测试；本步在 `tests/integration/test_table_tier_preflight.py`（若无此集成文件则新建，fixture 沿用 `tests/integration/conftest.py` 的 `build_fixture_project`）追加：构造带 `history_begins_after_anchor` 前缀的 pinned manifest，RESEARCH run 以 `table_history_start_after_window` 失败；窗口落在支持段的 run 正常放行；ENGINEERING run 豁免且 preflight summary 带 RESEARCH-ONLY 标签。断言形态照抄该文件既有的 NOT_FETCHED 用例（`table_not_fetched`），只换违规码与 manifest 构造。

- [ ] **Step 6: runner 接线**

`src/stock_quant/research/runner.py`，在 `:1220` `not_fetched = not_fetched_input_tables(...)` 之后（以实读行号为准）：

```python
        unsupported = table_unsupported_window_tables(
            manifest.get("build_config", {}),
            input_tables,
            spec.date_range.start_date,
            spec.date_range.end_date,
        )
```

在 `:1231` `if not_fetched and not engineering:` 块之后加同型分支：

```python
        if unsupported and not engineering:
            violations.append("table_history_start_after_window")
```

并把 `engineering_exempt` 的码族（`:1248-1255`）扩为包含 `"table_history_start_after_window"`，summary 增加 `"unsupported_window_tables": list(unsupported)`。import 区（`:74` 旁）加 `table_unsupported_window_tables`。

**已知限制（必须成文，不许留空）**：判定窗口取 `spec.date_range`，**不含 warm-up**。walk-forward 有独立的预热窗口（`walk_forward/schedule.py:84-86`、`:310-327` 的 `warmup_calendar_start`/`warmup_calendar_end`，早于该折的 `calendar_start`），而本预检在 `runner.py:821` 执行，**早于** `_walk_forward_pipeline`（`:836`）构建折计划。要把窗口前移到预热起点就得在预检里调 `_materialize_walk_forward_schedule`，那会让"区间内不含完整 12 个月 OOS 折"从 pipeline 阶段的错误提前到 preflight 阶段，改变既有失败归属——本期不做。改为在 preflight summary 里显式记录 `"check_window": [start, end]` 与 `"warmup_excluded": True`，并在 ADR-022 的"后果"节写明：**预热窗口内的截断本期不判**。P2c 落地 `basic_factor` 时必须把预热起点并入判定窗口并补失败测试——这是 P2c 计划的必做项，不能默认继承。

- [ ] **Step 7: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_fetch_coverage.py tests/unit/test_table_tier_preflight.py tests/integration/test_table_tier_preflight.py tests/integration/test_research_runner.py -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add src/stock_quant/data_model/fetch_coverage.py src/stock_quant/research/runner.py tests/unit/test_fetch_coverage.py tests/unit/test_table_tier_preflight.py tests/integration/test_table_tier_preflight.py
git commit -m "feat(preflight): reject research windows that reach unsupported history zones"
```

---

### Task 6: 每表必记录 + `required_table_coverage` 按 manifest 自身集合

**Files:**
- Modify: `src/stock_quant/research/acceptance/checks.py:274-283`（`_check_required_tables`）、`:440-466`（`_check_table_fetch_coverage`）
- Modify: `src/stock_quant/data_model/dataset.py:100-110`（`DatasetPublisher.publish`：发布期"当前注册表齐全"门禁**装在这里**，见 Step 1 的理由）
- Modify: `src/stock_quant/data_quality/models.py`（新码词汇）、`src/stock_quant/data_quality/gates.py`（两处码集）
- Test: `tests/unit/test_acceptance_checks.py`、`tests/unit/test_dataset_publisher.py`（若无则新建；或并入既有的发布器测试文件）、`tests/integration/test_data_pipeline.py`

**Interfaces:**
- Consumes: `validate_table_fetch_coverage`（Task 4）、`_tables_mapping`/`dataset_evidence`（checks.py 既有）。
- Produces: 新违规码 `table_fetch_coverage_table_unrecorded`（验收检查：有 `table_fetch_coverage` 键的 manifest 中，每张已发布表必须有记录）；`_check_required_tables` 改为要求 manifest 自身记录的表集合（`tables` ∪ coverage 键 ∪ lineage 键）全部在 `tables` 中，**不再**与当前 `STANDARDIZED_SCHEMAS` 比较；发布期新增 FATAL `missing_registered_table`，由 **`DatasetPublisher.publish`** 施加（当前注册表必须齐全——只对新发布生效，历史版本复审不再缺表失败）。

- [ ] **Step 1: 读现状并确认门禁位置**

Run: `sed -n '440,470p' src/stock_quant/research/acceptance/checks.py && sed -n '99,112p' src/stock_quant/data_model/dataset.py && grep -rn "\.publish(" --include=*.py src/ project/ | grep -v test`

**为什么装在发布器而不是 `data_pipeline.update()`**：`publish()` 本身已经在调 `evaluate_publication(report, table_tiers=...)`，不通过即 `raise PublicationBlocked`（`dataset.py:105-108`）；而仓库里有 **13 个离线发布入口**直接调 `DatasetPublisher.publish`（`project/collect_index_weight_membership.py`、`project/collect_csi300_official.py`、`project/rebuild_offline_real_dataset.py`、`project/collect_sina_membership.py`、`project/extend_history_offline.py`、`bootstrap.py` 等），它们全部绕过 `update()`。门禁只装在 `update()` 里，等于删掉验收侧检查后留一个 13 条路宽的口子。

**装之前必须先核对这些离线入口**：13 个脚本里有若干是"只发一部分表"的（如 `project/trim_universe_membership.py`、`project/extend_master_to_membership.py`、`project/bootstrap_seed.py`）。新门禁一旦生效会直接打断它们。先跑一次现状核对（或逐个读它们的 `publish(tables, ...)` 装配点），把"确实会少表"的脚本列出来报告 owner 再决定是补表还是加豁免理由；**不得**为了让它们过而把门禁降成 WARNING。

- [ ] **Step 2: 写失败测试**

`tests/unit/test_acceptance_checks.py`（该文件已有构造 `AcceptanceCheckInput` 与最小 dataset 的既有 fixture/帮手——`_input`/`project` 一族，照抄其构造方式）追加三个用例：

```python
def test_a_published_table_without_a_coverage_record_fails(project):
    # 在 project 的 pinned dataset manifest 中，把 build_config.table_fetch_coverage
    # 的某个表键删掉（该表仍在 tables 中）→
    # table_fetch_coverage_evidence FAIL，失败行含该表名


def test_required_tables_no_longer_judge_by_the_current_registry(project):
    # 在 STANDARDIZED_SCHEMAS 外给 manifest 增一张表（或删一张当前注册表）→
    # required_table_coverage 不再因此 FAIL（旧语义会 missing_required_table）


def test_publishing_without_every_registered_table_is_fatal(tmp_path):
    # 构造缺一张 STANDARDIZED_SCHEMAS 表的发布 → PublicationBlocked，
    # 质量报告含 missing_registered_table（FATAL）
```

（前两个用例写在 `tests/unit/test_acceptance_checks.py`，fixture 细节以该文件既有用例为准——先读最近一个 `_check_required_tables`/`_check_table_fetch_coverage` 相关用例，复用其 manifest 改写手法。**第三个用例写进发布器测试**（`tests/unit/test_dataset_publisher.py`，无则新建）：直接调 `DatasetPublisher(tmp_path).publish({...少一张表...}, QualityReport())`，断言抛 `PublicationBlocked` 且 `missing_registered_table` 出现在被写出的 `quality_report.json` 里。测试体照既有断言风格写全，不留省略。）

- [ ] **Step 3: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_acceptance_checks.py tests/unit/test_dataset_publisher.py -q -k "coverage_record or current_registry or registered_table"`
Expected: FAIL — 新码不存在 / 旧语义仍按当前注册表判缺表 / 发布器缺该门禁。

- [ ] **Step 4: 实现**

`_check_required_tables`（checks.py）改为：

```python
def _check_required_tables(value: AcceptanceCheckInput) -> CheckResult:
    """Require the manifest's own recorded tables to all be present.

    The "current registry must be complete" obligation moved to the publish
    gate (spec §7.5.6): re-reviewing an old version must not fail because a
    later release registered new tables.  Here the manifest is judged only
    against the table set it records itself.
    """
    evidence = dataset_evidence(value)
    tables = _tables_mapping(evidence.manifest.get("tables"))
    build = evidence.manifest.get("build_config")
    recorded = set(tables)
    if isinstance(build, Mapping):
        coverage = build.get("table_fetch_coverage")
        if isinstance(coverage, Mapping):
            recorded |= {str(name) for name in coverage}
        lineage = build.get("table_lineage")
        if isinstance(lineage, Mapping):
            recorded |= {str(name) for name in lineage}
    missing = sorted(recorded - set(tables))
    failures = [["missing_required_table", name] for name in missing]
    return _result("required_table_coverage", failures)
```

`_check_table_fetch_coverage`（:440）在 `violations = validate_table_fetch_coverage(...)` 之后追加（coverage 键存在时才判）：

```python
    coverage = build.get("table_fetch_coverage")
    if isinstance(coverage, dict):
        published = set(_tables_mapping(evidence.manifest.get("tables")))
        for name in sorted(published - set(coverage)):
            failures.append(["table_fetch_coverage_table_unrecorded", name])
```

（注意是 `evidence.manifest`——该函数里**没有** `manifest` 这个局部名；`build` 由 `_build_config(evidence.manifest)` 得到，`failures` 就是函数末尾喂 `_result` 的那个列表。）

`data_quality/models.py` 词汇区加 `CODE_MISSING_REGISTERED_TABLE = "missing_registered_table"`，并照 `CODE_UNREGISTERED_TABLE` 的先例登进 `gates.py` 的 `PUBLICATION_BLOCKING_CODES` 与 `GLOBAL_PROCESS_CODES` 两个集合（`gates.py:55`/`:72`）。

`data_model/dataset.py` 的 `DatasetPublisher.publish`：在 `decision = evaluate_publication(...)` **之前**把注册表完整性并进待评估的报告（`QualityReport` 是 frozen、`issues` 是 tuple，只能重建）：

```python
    def publish(self, tables, report, *, build_config=None, table_tiers=None):
        """Gate, stage and atomically publish one immutable dataset version."""
        report = _with_registered_table_issues(report, tables)
        decision = evaluate_publication(report, table_tiers=table_tiers)
        ...
```

```python
def _with_registered_table_issues(
    report: QualityReport, tables: Mapping[str, object]
) -> QualityReport:
    """Fold ``missing_registered_table`` into the report being gated.

    Registry completeness (spec §7.5.6) is a *publication* obligation, not a
    review obligation: it is enforced here so that every caller -- ``data
    update`` and the offline ``project/*.py`` publishers alike -- is covered,
    and so that re-reviewing an old version never fails because a later
    release registered a new table.
    """
    missing = tuple(
        _issue(Severity.FATAL, CODE_MISSING_REGISTERED_TABLE, table=name)
        for name in sorted(set(STANDARDIZED_SCHEMAS) - set(tables))
    )
    if not missing:
        return report
    return QualityReport(issues=(*report.issues, *missing))
```

（`dataset.py` 已经 import 了 `STANDARDIZED_SCHEMAS`、`QualityReport`、`evaluate_publication`；`Severity`/`_issue` 若不在本文件，用一个本地小工厂构造 `QualityIssue`，别为它把 `data_pipeline._issue` 引进来。）

- [ ] **Step 5: 跑测试确认通过 + 邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_acceptance_checks.py tests/unit/test_dataset_publisher.py tests/unit/test_tiered_publication_gate.py tests/unit/test_quality_checks.py tests/integration/test_data_pipeline.py -q`
Expected: PASS（`test_quality_checks.py` 若有按字面集比较 `GLOBAL_PROCESS_CODES`/阻断集合的断言，按失败信息把新码补进期望集合，不改成子集断言。发布器测试全绿是本步重点——若 13 个离线入口里有"只发一部分表"的脚本，它们的既有集成测试会在这里变红，按 Step 1 的指示报告 owner，不要放宽门禁）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/research/acceptance/checks.py src/stock_quant/data_model/dataset.py src/stock_quant/data_quality/models.py src/stock_quant/data_quality/gates.py tests/unit/test_acceptance_checks.py tests/unit/test_dataset_publisher.py tests/integration/test_data_pipeline.py
git commit -m "feat(gates): record coverage per published table and judge manifests by their own tables"
```

---

### Task 7: 源不可用的 carry 与尾段语义

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（表级 fetch 失败→段装配点；先按 Step 1 定位）
- Test: `tests/integration/test_data_pipeline.py`

**Interfaces:**
- Consumes: Task 3/4 的 `source_unavailable` 尾段形态；`_read_baseline`（data_pipeline.py:1641，已 carry 整表与 coverage 帧）。
- Produces: 表级语义（spec §7.5.4）——已注册表的源本轮整体不可用且**已有 baseline**：发布 carried 表（旧事实一字不动）+ 追加 `[下一日, published_end]` 的 `source_unavailable` 尾段；**无 baseline 首次发布**：允许空 canonical frame（quarantine 空表先例）；任何路径都不得清空已有表。

- [ ] **Step 1: 定位段装配点**

Run: `grep -n "to_build_config_payload\|table_fetch_coverage" src/stock_quant/data_pipeline.py | head`
找到 carried/fetched 段的写入处与表帧的装配处，记下函数名与行号。

- [ ] **Step 2: 写失败测试**（`tests/integration/test_data_pipeline.py`，fixture 沿用该文件既有的 stub 源与 project 帮手）

四个用例（照该文件既有 `test_update_*` 的构造手法写全）：

```python
def test_an_unavailable_source_keeps_the_baseline_table_and_marks_the_tail():
    # 1) 用 stub 源发布一个含目标表的小版本；
    # 2) 换成整体抛 TransientSourceError 的 stub 再 update；
    # 断言：新版本中该表行数与旧版本完全一致（未清空）；
    # build_config.table_fetch_coverage[表] 的末段是 not_fetched /
    # source_unavailable，window_end == published_end；前段是 carried。


def test_a_first_publish_with_an_unavailable_source_may_be_empty():
    # 无 baseline 的 project，注册表源整体不可用 → 允许空 canonical frame
    # 发布；coverage 段整窗 not_fetched/source_unavailable。


def test_clearing_an_existing_table_never_publishes():
    # 构造"源成功返回但目标表被清成空帧"的畸形轮 → 该轮失败（门禁拒绝），
    # CURRENT 保持旧版本；质量报告含 FATAL
    # table_emptied_by_fetch（表名在 details/table 上）。


def test_a_source_unavailable_tail_version_passes_data_validate():
    # 承用例一：它发布出的那个版本，`data validate --root <fixture>` 必须通过。
    # 新形态的 table_fetch_coverage 要真的能被验收检查
    # （table_fetch_coverage_evidence → validate_table_fetch_coverage）吃下，
    # 不能只在纯函数层"通过"。走 CLI runner，手法照
    # tests/integration/test_acceptance_cli.py 既有用例。
```

- [ ] **Step 3: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_data_pipeline.py -q -k "unavailable or clearing"`
Expected: FAIL — 现状源失败直接走整轮失败/或表被清空，无 carried+尾段形态。

- [ ] **Step 4: 实现**（以 Step 1 定位点为准；行为规格如下，代码贴既有段装配风格）

表帧装配处：源整体失败的已注册表，若 `_read_baseline` 读到该表的既有帧 → 直接采用 baseline 帧（equivalent to carried），并在段装配时写入 `FetchSegment(表, KIND_CARRIED, anchor, last_covered)` + `FetchSegment(表, KIND_NOT_FETCHED, last_covered+1, published_end, reason=NOT_FETCHED_SOURCE_UNAVAILABLE)`；baseline 无该表 → 空 canonical frame + 整窗 `source_unavailable` 段。已注册表"源成功但空/被清"且 baseline 非空 → 按 §7.5.4"直接阻断本轮"：新增 FATAL 码 `table_emptied_by_fetch`，按 Task 6 的 `missing_registered_table` 先例登进 `data_quality/models.py` 与 `gates.py` 的 `PUBLICATION_BLOCKING_CODES`、`GLOBAL_PROCESS_CODES` 两个集合（`coverage_downgraded` 不适用——那是表级结构问题的降级路径，不是清空事故）。

- [ ] **Step 5: 跑测试确认通过 + 回归**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_data_pipeline.py tests/unit/test_fetch_coverage.py tests/unit/test_acceptance_checks.py tests/unit/test_quality_checks.py -q`
Expected: PASS（用例四走的是 CLI + 验收检查这条路，是"新形态真能落库"的唯一证据；它红说明 `validate_table_fetch_coverage` 的新分区规则还没被验收侧正确消费。）

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/data_pipeline.py src/stock_quant/data_quality/models.py src/stock_quant/data_quality/gates.py tests/integration/test_data_pipeline.py
git commit -m "feat(pipeline): carry baseline tables through source outages with marked tails"
```

---

### Task 8: 批次收口（§7.4 对应条目勾稽 + 回归）

**Files:** 无新改动（只读核对与全量相关套件）。

- [ ] **Step 1: 对照 spec §7.4 勾稽本批完成条件**

逐条核对（本计划覆盖的子集）：

| spec §7.4 条目 | 由谁交付 |
| --- | --- |
| "`not_fetched` 前缀规则有失败测试：中间或尾部的 `history_begins_after_anchor`、以及混用 `source_unavailable` 的段都必须被拒" | **Task 4**（本计划完整交付） |
| "引用早于其支持起点的窗口的 RESEARCH run 被稳定拒绝，引用支持窗口的 run 正常放行" | **Task 5**（本计划完整交付） |
| "……新表能发布并通过 `data validate`" | **只有 `source_unavailable` 尾段这一半**由 Task 7 用例四交付。**`history_begins_after_anchor` 前缀这一半本计划不交付**——写前缀的是 `basic_factor` 的发布路径，属 P2c。Task 8 不得声称已覆盖这一半（原稿此处是过度声明）。 |
| "注册新表后，旧 dataset 版本的复审不再缺表失败；`required_table_coverage` 对无 `table_lineage` 的旧 manifest 按旧形态兼容读取" | **Task 6**——补一条无 `table_lineage` 键的旧 manifest 复审用例（Task 6 未覆盖） |

- [ ] **Step 2: 相关套件全绿**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_fetch_coverage.py tests/unit/test_acceptance_checks.py tests/unit/test_table_tier_preflight.py tests/unit/test_tiered_publication_gate.py tests/unit/test_quality_checks.py tests/integration/test_table_tier_preflight.py tests/integration/test_data_pipeline.py tests/integration/test_window_anchor_regression.py tests/unit/test_context_governance_docs.py -q`
Expected: PASS

- [ ] **Step 3: 向 owner 汇报批次完成与两个待裁定点**

汇报内容：本批 landed 清单；§7.0.10 提交协议二选一（阻塞 P2b 计划）；P1 探针联网授权（阻塞 P1 计划）。

---

## Self-Review 记录

- 规格覆盖：§7.5.1（Task 4 前缀规则+失败测试）、§7.5.2（Task 3 词汇）、§7.5.3（Task 5）、§7.5.4（Task 7）、§7.5.5（Task 6 前半）、§7.5.6（Task 6 后半 + 发布期门禁）、§5.1（Task 1）、§5.2 的 ADR-022 部分（Task 2；ADR-021/023 属 P3/P2b 阶段计划）、§7.3 尾段 pipeline_contract_version 与 table_lineage 的**判定语义**（Task 6；`table_lineage` 键的写入方在 P2c 随 `basic_factor` 落地，本计划只落读取兼容）。§7.4 勾稽在 Task 8。
- 类型一致性：`NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR`/`NOT_FETCHED_SOURCE_DISABLED`/`NOT_FETCHED_SOURCE_UNAVAILABLE`/`table_unsupported_window_tables`/`not_fetched_prefix_misaligned`/`table_fetch_coverage_table_unrecorded`/`missing_registered_table`/`table_history_start_after_window` 各任务间引用一致。
- 已知留白（有意的）：
  - Task 7 的 pipeline 段装配点按 superpowers 惯例以"先读再接"给出定位命令，不是占位符。
  - **warm-up 不判**：Task 5 的判定窗口取 `date_range`，不含 walk-forward 预热窗口（理由与归属见 Task 5 Step 6 与 ADR-022"后果"节）。这是成文的、有交付方的限制，不是遗漏。
  - **`history_begins_after_anchor` 前缀的写入方不在本计划**：validator 接受它、Task 5 拒绝落在其中的窗口，但没有任何任务写出这种段——那是 P2c 随 `basic_factor` 落地的事。§7.4 的"能发布并通过 `data validate`"因此只被 Task 7 的尾段用例覆盖了一半。
- 复核记录（2026-10-01）：Task 4 的 7 个用例曾对 Task 4 的实现原样执行，发现 2 个断言与实现冲突、`_contiguity_violations` 存在放宽、`unavailable` 顺序守卫恒为假——三处均已在本稿修正，并补上"晚起无前缀仍是 gap"的回归用例。
