# 659 池数据层剩余缺陷修复 · 设计

- 日期：2026-09-15
- 状态：设计待实现
- 上游记录：[2026-09-14-blocking-gap-root-cause.md](../../operations/2026-09-14-blocking-gap-root-cause.md)

## 0. 摘要

现行数据集 `CURRENT = 1d6e43b4…`（2026-09-14 04:55 发布）已通过发布门禁（报告
`ERROR: 0 / FATAL: 0`），但 real-data-v1 的 9 项验收自动检查仍是 **7 PASS / 2 FAIL**。
本设计修掉这两项 FAIL 背后的三个数据层缺陷，**只改代码与测试**，不发布数据集。

| 编号 | 缺陷 | 改动层次 | 预期效果 |
| --- | --- | --- | --- |
| D1 | build 请求起点 `2015-01-01` 早于日历首个开市日 `2015-01-05` | 运维参数 + RUNBOOK | 消掉 `window_not_calendar_complete` |
| D2 | 抓取起点=窗口起点，窗口左端停牌 run 拿不到证明锚点 | 抓取层（追加只供证明用的左抓） | 1437 条缺口归零，或只剩「窗口前从未交易」的真残余 |
| D3 | 覆盖度判定的不信任口径是「该符号历史上是否有过隔离记录」，而它写进的是「某窗口」的行 | 覆盖度层（窗口裁剪） | UNTRUSTED 71 只 → **27 只** |

预期效果均为**推算**，必须以新数据集版本上的实测为准（见 §9）。

## 1. 实测证据（2026-09-14 只读测得）

数据集侧：

- `project/data/standardized/CURRENT` = `1d6e43b4…`；master 659 行，`daily_bar`
  1,707,800 行，`adjusted_bar` 1,702,134 行，`corporate_action` 6,872 行（649
  符号），`corporate_action_quarantine` **118 行**。
- 数据集 `quality_report.json`：`ERROR: 0, FATAL: 0`。
- `adjusted_bar` 实际带 **195,401 行 `quality_severity=ERROR`**
  （`corporate_action_coverage_untrusted` 195,376 + `cross_source_conflict` 25），
  全部属于那 71 只不信任符号 —— 发布门禁不统计派生表断点，所以报告看不见。

验收侧（对 CURRENT 重跑 `run_automated_checks`）：

- `date_window_completeness` FAIL：1437 条 `unexplained_missing_row`，跨 **29 只**，
  全部落在 `2015-01-05..2015-12-07`，每一条都是**起点正好是窗口首个开市日**的
  停牌 run（`suspensions.py:128` 拒绝对没有 `before` 锚点的 run 落地与证明）；
  另有 `window_not_calendar_complete 2015-01-01..2026-08-28`。
- `corporate_action_evidence` FAIL：**71 只** = 54 `FACTS_INCOMPLETE`
  + 17 `SOURCE_CONFLICT`。

隔离表 118 行的窗口归属（按 `ex_date`/`record_date`/`announcement_date`
任一落在 `[2015-01-01, 2026-08-28]` 判窗口内）：

| 分段 | 行数 | 符号数 | 明细 |
| --- | --- | --- | --- |
| 全部已知日期都在窗口前 | 60 | 49 | 32 行有 `record_date`（2000–2010）；28 行只有 `announcement_date`（1996–2010）；全部 `status=implemented`、`reason=incomplete`、`ex_date` 为空 |
| 有窗口内日期 | 58 | 25 | 50 行 `cross_source_conflict`（均带 `ex_date`）+ 8 行 `incomplete`（`ex_date` 空，公告 2015–2026） |

两段符号数 49 + 25 = 74，去重叠后 = **71**，与验收报告的符号数逐一对上。
其中「仅被窗口前记录拉黑」的符号 = **46** 只。

推导前提实测（用数据集自己的已接受事实）：`corporate_action` 6,872 行**全部**
同时有 `ex_date` 与 `record_date`，`ex_date - record_date` 为 **1–13 天，
零负数**（min 1，p50 1，p99 5，max 13）。故「`ex_date ≥ record_date`」在本项目
数据上是可依赖的推导，不是惯例假设。

三档的代价实测：

| 档 | 依赖 | 符号数（去重叠后） | 其中窗口内无任何已接受事实 |
| --- | --- | --- | --- |
| `ex_date` / `record_date` 已知且在窗口前 | 推导 | 25 | 1（`000629.SZ`） |
| 仅 `announcement_date` 在窗口前 + `implemented` | 条件放宽 | 21 | 1（`000503.SZ`） |
| 合计（= 仅被窗口前记录拉黑者） | | 46 | 2 |

窗口前那段里，46 只中的 **45 只**在已发布 `corporate_action` 表中都有带期的
窗口内行动 —— 即供应商对这些股票窗口内的实施是会带 `除权除息日` 报出来的。
这是旁证（不是证明），用于评估第三档的风险，见 §5.3。

## 2. 范围与不变量

本轮**只**改 `src/stock_quant/**`、`tests/**` 与下述文档。明确不做：

- 不运行 `data update` 或任何联网/出版命令；不产出新数据集版本；不动 `CURRENT`
  与任何已发布版本（invariant 2）。
- 不修改证明规则本身（`suspensions.py` 的 `before`/`after`/`pre_close` 链式校验
  一行不动）、不修改 `_check_date_window`、不扩充 `_ACCEPTED_MISSING_CODES`、
  不弱化任何发布/验收门禁（invariant 3）。
- 发布表内容与声明窗口保持一致：`daily_bar` 不得出现早于声明窗口首个开市日的行。
- 证据不隐藏（invariant 3）：隔离表仍全量发布；被抑制的记录另留可审计痕迹。

## 3. D1 · 窗口起点对齐到日历首个开市日

**做法**：只改运维参数与文档 —— 下次更新固定使用 `--start 2015-01-05`，写进
`RUNBOOK.md`。**不新增校验。**

**理由**：

- 这是调用参数错，不是代码缺陷。参数写对即可，代码本身没有可修的 bug。
- 验收阶段已有 `window_not_calendar_complete` 如实报告（
  `checks.py:308` 的 `start < open_days[0]`），在 build 阶段再报一次是重复，
  且会把「人敲错了参数」上升为数据管道的硬约束。
- 新增该校验会引入一条与日历起点耦合的维护项：数据源日后补上更早的开市日，
  校验会误报并需要跟着改。

**附带收益（非本设计的动机，但值得记录）**：这一改同时消除了一个真实的不一致
—— 现行数据集声明窗口从 `2015-01-01` 起，而 `custom_csi300_tw_tradable` 宇宙
定义的 `coverage_start` 是 `2015-01-05`。对齐后二者一致，验收的完整性格网也与
冻结定义的覆盖起点重合。

**若日后要加护栏**：语义正确的位置是 CLI 层（`cli.py` 解析 `--start` 时），
而不是 build 阶段。本设计不加。

## 4. D2 · 停牌证明的锚点抓取

### 4.1 机制

不引入任何「猜出来的深度常数」。锚点的正确边界是**每只股票自己的上市历史**：
「这只股票在窗口前是否交易过」唯一正确的边界就是它有没有窗口前的 bar。

**候选判定**（在第一遍抓取之后、无需额外知识即可确定）：

> 候选 = 该股在**窗口内首个开市日**于自己的原始帧中没有行的股票。

该日不早于其 `list_date` 时即该股的 grid 首日。窗口开始后才上市的股票也会落进
候选集，但这无害且无需特判：其 `[list_date, start)` 为空，直接跳过、不发起调用。

候选判定所需的两项输入在调用点都已就绪：`calendar_open`（`update()` 于
`data_pipeline.py:661-674` 取得）与 `master`（`:677-683` 取得），二者都在
`_fetch_primary_stock`（`:690`）之前；`list_date` 取自 master。

**追加抓取**（仅对候选，且仅在有意义时）：范围 `[list_date, start)`，
`start` = build 的 requested start date。若该范围为空（窗口开始后才上市）则跳过，
不发起调用。探针区间 `[chunk_start, start-1]` 与主抓取区间 `[start, end]` 严格
不相交，故实现只做 `pd.concat`、不去重，拼接后写入
`raw_daily_frames[symbol]` —— 那是 `_materialize_suspensions` 的证明输入。

**发布路径不受影响**：拼接帧**不进入 `primary_rows` / `primary_dates`**，也不经过
`normalize_daily`。因此发布 `daily_bar` 中不会出现任何早于窗口首个开市日的行，
无需任何裁剪逻辑（裁剪逻辑本身就是一类「误发布」bug 的来源，本设计不引入它）。

**`_materialize_suspensions` 一行不改**：它的 `chain` 取自 `raw_daily_frames`
（现在自动含窗口前锚点），`window` 仍由日历在 `[start, end]` 内筛出。

**抓取形状**：实现只须满足结果要求「能确定窗口前最后一根 bar 是否存在」。
若供应商单次行数上限使一次抓取读不全，可分段或自适应加深；**不得**引入一个固定
的猜测深度作为边界。已知风险：tushare `daily` 单次约 6000 行上限，1990 年代上市
的股票抓 `[list_date, start)` 会顶到边，故实现需按需分段。

**证据**：追加抓取的响应同样经 `self._record_raw(result)` 记入原始快照
（`request_key` 因起点不同而不同，自然区分）。验收侧
`raw_snapshot_traceability` 只按内容哈希校验存在性，不对快照做窗口范围断言，
故不会因此失败。

### 4.2 残余

拼接后仍无窗口前 bar 的候选，意味着该股在窗口前**从未交易过**（长期停牌/退市
边缘），此时缺口是真实事实：保持 `suspension_run_unverified` WARNING，不落地
bar，缺口诚实地留在验收里。实现后须核对残余清单，**不假设 1437 条必然归零**。

## 5. D3 · 隔离证据的窗口口径

### 5.1 缺陷定性

`_coverage_verdict`（`data_pipeline.py:2264`）回答的是「该符号历史上是否有过隔离
记录」，而它写进的是「某符号 / 某窗口」的覆盖度行 —— 两个口径不一致。

本仓库内部本来就有这个矛盾：**break 层** `_merge_quarantine_breaks`
（`adjusted_bar.py:133`）遇到 `ex_date is None` 直接 `continue`，即「无日期的记录
不构成 transition，不能跨断任何窗口」；**覆盖度层** `_overlay_untrusted_coverage`
却据此把整条 `[start, end]` 全标成断点 —— 46 只符号因此背上整窗 ERROR。对齐两层
口径是**一致性修复**，不是放宽门禁。

### 5.2 判定规则（三分支，按顺序）

```
若 ex_date 已知:
    在 [start, end] 内 → 相关；否则 → 不相关          # 推导
否则若 record_date 已知:
    在 [start, end] 内 → 相关；否则 → 不相关          # 推导：ex_date ≥ record_date
否则若 announcement_date 已知:
    announcement_date < start 且 status == STATUS_IMPLEMENTED → 不相关  # 条件放宽（§5.3）
    否则 → 相关
否则:                                                  # 无任何已知日期
    → 相关                                             # fail-closed
```

第三分支的 `announcement_date < start` 是「公告在窗口前」；**不**使用「窗口外」
这种更宽的措辞，因为注释在窗口之后（`announcement_date > end`）的记录，其
`ex_date` 同样可能落在窗口内，必须保持相关。

### 5.3 第三分支是条件放宽，不是推导 —— 必须在 ADR 里点名

第三分支**确实推翻了**既有政策。`filter_corporate_actions_to_window`
（`corporate_actions.py:248`）的文档字符串写着：

> An implemented record with a missing ex-date remains for reconciliation to
> flag as a genuine defect.

即「已实施但缺 `ex_date` 的记录故意保留」。本设计对**公告在窗口前**的那部分予
以裁剪，属于超驰该政策，因此必须新增一条 ADR 明确记载此事，不得悄悄改（ADR 内
容见 §7）。

支持该放宽的旁证：

- 这类记录**无法进入复权递归**：`key = (symbol, ex_date)`（`corporate_actions.py:431`）
  使无 `ex_date` 的记录永远进不了 candidate；`_merge_quarantine_breaks` 也跳过它。
  因此它无法遮蔽窗口内的任何东西，只能说明供应商对那条陈旧记录缺字段。
- 46 只中 45 只在已发布 `corporate_action` 表中都有**带期的窗口内行动**，说明这些
  股票窗口内的实施是会作为另一条带期记录被对账的；无期的方案记录不是同一条事件。

已知风险（必须在 ADR 中记载）：若某条陈旧方案**真的**在窗口内除权而供应商记录缺失，
裁掉之后序列会变干净且**无任何标记** —— 正是 invariant 5 最忌讳的静默。该风险的
量级由上述旁证约束，但不由其消除；这是本轮唯一一处主动放宽，代价与理由都记在 ADR。

### 5.4 落点

- 新增纯函数 `quarantine_row_out_of_window_reason(row, start, end) -> str | None`，
  实现 §5.2 的判定：返回**被排除的分支名**（`ex_date_out_of_window` /
  `record_date_out_of_window` / `announcement_pre_window_implemented`），
  返回 `None` 表示该行与窗口相关。返回分支名而非 bool，是因为本节还要把 `branch`
  写进 INFO 的 details —— 一处实现同时供两个用途。就近放在
  `data_model/corporate_actions.py`（与被取代的 `filter_corporate_actions_to_window`
  同处一层、同一关注点）。
- 仅在覆盖度调用点（`data_pipeline.py:1709-1721`）用它过滤传给
  `_coverage_verdict` 的那一份映射。`merged_quarantine` 与最终发布的隔离表**仍是
  全量 118 行**（invariant 3：证据不隐藏）。
- 被排除的条数以 **INFO** `QualityIssue` 记入质量报告，使被抑制的决定留下可审计
  痕迹：在 `data_quality/models.py` 新增
  `CODE_QUARANTINE_OUT_OF_WINDOW = "quarantine_out_of_window"`，按
  `(symbol, window)` 聚合，`details={"rows": n, "branch": <分支名>}`。
  该调用点已有 `issues` 在作用域（`update()` 于 `:748` 传入）。severity 为
  INFO，不阻塞发布。
- 更新 `_coverage_verdict` 的文档字符串：现措辞（「符号有任何隔离记录即
  UNTRUSTED」）与窗口口径不符，需改成「窗口相关的隔离记录」并指向 §5.2 的规则。

### 5.5 预期效果（待验证）

- UNTRUSTED 71 → **27**。翻转的 44 只 = 24（严格档）+ 20（条件档）；
  未翻转的 27 只 = 25 只带窗口内证据者 + `000629.SZ` / `000503.SZ`
  （窗口内没有任何已接受事实，仍停在 `FACTS_INCOMPLETE`，这是正确结果）。
- `adjusted_bar` 的 `corporate_action_coverage_untrusted` ERROR 行预计减少约
  44/71（≈12 万行），`cross_source_conflict` 的 25 行不受影响。

## 6. 测试

按 `.claude/rules/tests.md`：先用具名测试文件，不跑裸 `pytest`。

D1：

- 无代码改动，故无新增单元测试；以 RUNBOOK 的命令文本为交付物（若日后在 CLI 层
  加护栏，须另附一条拒绝测试）。

D2（`tests/unit/test_suspensions.py` 与 `tests/integration/test_data_pipeline.py`）：

- 候选且其上市历史内有窗口前 bar（符号上市日 stub 为 1991-01-02；探针逐块回退，
  深度以该股上市历史为界，不设固定常数）→ 该停牌 run 落地为 `tushare_suspend`
  bar，价格取自那根 bar 的 `close`；证明不再报 `suspension_run_unverified`。
- 候选但在整个上市历史内都没有窗口前 bar → 仍报 `suspension_run_unverified`，
  不落地任何 bar。
- 非候选（窗口首日有 bar）→ **不发起**追加抓取（以调用计数断言），即候选判定确实
  收窄了抓取范围。
- 发布断言：无论上述哪种情形，发布 `daily_bar` 中该股都不含任何早于窗口首个开市
  日的行。

D3（`tests/unit/test_corporate_action_normalize.py` 的
`quarantine_row_out_of_window_reason` 与
`tests/integration/test_data_pipeline.py` 的
`test_update_ignores_quarantine_rows_whose_dates_predate_the_window`）：

- 分支矩阵：`ex_date` 在窗口内 / `ex_date` 在窗口外 / `record_date` 在窗口内 /
  `record_date` 在窗口前且 `ex_date` 空 / 仅 `announcement_date` 在窗口前且
  `implemented` / 仅 `announcement_date` 在窗口前且**非** `implemented` /
  `announcement_date` 在窗口**后** / 完全无日期（fail-closed）。
- 集成断言：过滤前后**发布的隔离表行数不变**（证据不隐藏）。
- 集成断言：仅被窗口前记录拉黑的符号，coverage 行 `status` 由 `UNTRUSTED` 变为
  `VERIFIED`，`reason` 为空；且 INFO 计数与实际被裁条数一致。

## 7. 文档

- **ADR-006**（`docs/adr/006-corporate-action-window-scope.md`）：记载 §5 的口径
  决策，明确点名它取代 `filter_corporate_actions_to_window` 文档字符串中
  「implemented with missing ex-date remains」的立场，并记载 §5.3 的风险与旁证。
  按 `DECISIONS_INDEX.md` 的约定登记，`status: accepted`。
- `RUNBOOK.md`：把下次更新的命令固定为 `--start 2015-01-05`，并写清理由（日历首个
  开市日即窗口起点；起点早于日历证据会被 `window_not_calendar_complete` 拒绝）。
- `docs/operations/2026-09-14-blocking-gap-root-cause.md`：追加一节记录本轮的实测
  切分（46 / 25、三档代价、`ex_date ≥ record_date` 前提的 6872/6872 实测）。

**不在本轮**（需另行授权）：`2026-09-14-wf-oos-stage-result-and-diagnostics.md` 与
`PROJECT_MEMORY.md` §8.4 仍称 `CURRENT = 1709eddb…`，而实测该版本的 `daily_bar`
只有 32 个符号；这两处的更正属于独立的一笔。

## 8. 明确不做

- 不加 D1 的 build 阶段校验（§3）。
- 不改停牌证明规则、不改 `_ACCEPTED_MISSING_CODES`、不改验收检查本身。
- 不把窗口前 bar 发布进 `daily_bar`（这会造出「声明窗口 ≠ 实际内容」的错位，并让
  因子 warmup 吃到未受审查的数据 —— 见 §2 与 invariant 1）。
- 不把数据集声明窗口前移（会凭空造出约 90 个开市日 × 659 只的覆盖义务，并让已冻结
  规格的日期范围与已签署证据失配）。
- 不处理 25 只带窗口内证据的符号（8 只窗口内缺 `ex_date` + 17 只跨源冲突）：那是
  真实的数据侧工作，需要到源侧补事实或走 review 签字，不属于代码改动。

## 9. 完成判据（本轮）

1. §6 的具名测试全部通过；`ruff check` 对改动文件通过（本仓库从不要求 repo-wide
   `ruff format --check` 通过）。
2. §7 的三处文档落盘，ADR-006 已在 `DECISIONS_INDEX.md` 登记。
3. 工作树中除本设计涉及的文件外，无其它改动被带入。
4. **不**产出新数据集版本；`CURRENT` 不变。§0 的预期效果留待你批准发布后，在新
   版本上以重跑的 9 项验收实测验证。
