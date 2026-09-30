---
status: accepted
date: 2026-10-01
decision: "The panda graft enters with zero new suppliers — every fetch stays on the existing Tushare (relay/official transport), akshare and xingyao channels with tdx as corporate-action arbiter only, and re-entry of RiceQuant-class sources is a gate (spec appendix B), not a backlog item. Multi-index membership takes one path: Tushare `index_weight` monthly snapshots under an attested-boundary approximation contract where `announcement_date` = snapshot day = `raw_effective_from` and diffed joins/leaves record `snapshot_observed_change`. `basic_factor` is a single-candidate (daily_basic only), research_only contract storing only the fields Stock has no other source of truth for (`market_cap`, `turnover_rate`, units pending probe evidence), never copying `daily_bar` quote columns and never shipping the Panda ten-column wide table; any failed verdict item (transport binding, window coverage, quota, measured units) means the table is not published. The §7.5 fetch-segment reason vocabulary gains `history_begins_after_anchor` (unique prefix), `source_disabled` and `source_unavailable`, kept strictly separate from the coverage table's `CoverageReason.SOURCE_FETCH_FAILED`; preflight gains `table_history_start_after_window` (RESEARCH fail closed, ENGINEERING exempted and annotated, judged on `ExperimentSpec.date_range` without the walk-forward warmup for now). Publication gains `build_config.table_lineage` cross-checked against `primary_transport`, and the registry-completeness check moves to a FATAL `missing_registered_table` gate at `DatasetPublisher.publish` while `pipeline_contract_version` stays `1` and `required_table_coverage` is re-scoped to the manifest's own table set — a recorded semantic change of an unchanged code name. `basic_factor_coverage` is `core` with `coverage_shape=none`, reusing the `corporate_action_coverage` `CoverageStatus/CoverageReason` vocabulary, where `VERIFIED_EMPTY` needs security-master listing/delisting evidence. `basic_factor` and existing CSI300 official facts do not enter the xingyao validation lane or ADR-013/014 arbitration ordering."
affects:
  - src/stock_quant/data_model/dataset.py
  - src/stock_quant/data_model/corporate_action_coverage.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/research/acceptance/checks.py
---

# ADR-022: Panda 嫁接的源范围裁定与 basic_factor 契约

日期:2026-10-01
状态:accepted
相关:spec 2026-09-29-panda-data-loop-grafting-design(附录 A/B、§6、§7);许可证核验
docs/operations/2026-10-01-panda-license-verification.md

## 背景

pandaAI 的数据闭环能力要嫁接进 Stock。其数据源三路(RiceQuant/xtquant/tqsdk)
在本机均不可得(无账号、无许可、无客户端,见附录 A 取证),而 Stock 的治理边界
(单一写入链、内容寻址发布、fail-closed)不允许为吸收能力而引入第二套真相。

## 决策

1. **本期零新供应商**:全部取数走既有 Tushare(relay/官方 transport)、akshare、
   星耀通道;tdx 仍只作公司行为仲裁器。RiceQuant 等源的再进入条件是门禁
   (spec 附录 B),不是待办。
2. **多指数成分单路径**:Tushare `index_weight` 月度快照,attested-boundary
   近似契约;`announcement_date` = 快照日 = `raw_effective_from`,不得声称
   官方公告日或精确生效日;差分生成的加入/退出记 `snapshot_observed_change`。
3. **`basic_factor` 只存 Stock 尚无唯一事实来源的字段**:`market_cap`/
   `turnover_rate`(单位换算以探针实测为准,本 ADR 成文时未实测,探针结论以
   dated 补注回写本节);不复制 `daily_bar` 的任何行情列;不提供 Panda 十列
   宽表。初始 tier `research_only`;升 `anchored` 须另有独立锚点证据。
4. **单候选裁决**:本期只有 `daily_basic` 一个候选,不是"比较后胜出";任一
   裁决项(transport 可绑定、窗口覆盖、配额、单位实测)失败即不发布该表。
5. **§7.5 覆盖理由词汇**:fetch-segment 理由新增 `history_begins_after_anchor`
   (唯一前缀 `[acceptance_start, supported_start-1]`,不得出现在中间/尾部)、
   `source_disabled`(本次未启用)、`source_unavailable`(启用但不可达,尾段);
   与 coverage 表的 `CoverageReason.SOURCE_FETCH_FAILED` 是两套词汇,不得互串。
   preflight 新增 `table_history_start_after_window`:运行窗口早于表支持起点或
   落入未支持段时 RESEARCH run fail closed,ENGINEERING 豁免并标注。
6. **`build_config.table_lineage` 引入**(table → transport → raw snapshot 证据行),
   发布时与 `primary_transport` 声明核对;旧 manifest(无该键)按旧形态兼容读取。
7. **`pipeline_contract_version` 保持 `1`**:`required_table_coverage` 改按 manifest
   自身表集合判定,"当前注册表齐全"移到**发布期门禁**(`DatasetPublisher.publish`,
   `missing_registered_table`,FATAL);升 contract version 会让每个已发布数据集在
   `source_role_health` 上失败,等于把问题搬家并放大。
   **本条的语义变更必须记录在案**:`required_table_coverage` 这个码名不变,含义从
   "对当前注册表判缺表"变成"对 manifest 自身记录集判"。同一版本在新旧代码下复审
   会得到同一码名的不同结论。门禁装在发布器(而不是 `data_pipeline.update()`)
   是因为仓库有 13 个离线发布入口直接调 `publish`,只装 `update()` 会留一条
   13 条路宽的口子。
8. **`basic_factor_coverage`**:`core`、`coverage_shape=none`;词汇复用
   `corporate_action_coverage` 的 `CoverageStatus/CoverageReason`;
   `VERIFIED_EMPTY` 必须有 security master 上市/退市证据支撑(对稠密因子事实,
   "应有而无"是 `UNTRUSTED(FACTS_INCOMPLETE)`,不是可信空值)。
9. **不进星耀验证 lane**:`basic_factor` 与既有 CSI300 官方事实不进入星耀验证
   lane 或 ADR-013/014 仲裁排序。

## 后果

- 探针结论(单位、空值、可得性时点、节奏)以 dated operations evidence 回写
  本 ADR 的补注节;与假设不符时实现随探针调整。
- **预热窗口内的截断本期不判**:`table_history_start_after_window` 的判定窗口取
  `ExperimentSpec.date_range`,不含 walk-forward 的 `warmup_calendar_start`
  (预检早于折计划构建,前移会改变既有失败归属)。P2c 落地 `basic_factor` 时必须
  把预热起点并入判定窗口并补失败测试。
- 发布器新增 FATAL `missing_registered_table` 与 `table_emptied_by_fetch` 后,
  既有 13 个离线发布入口一并受约束;核对发现"只发一部分表"的脚本须补表或由 owner
  另行裁定,不得放宽门禁。
- 未来第二个源获准再进入时,本 ADR 的单候选裁决即失效,须重新多源裁决。
