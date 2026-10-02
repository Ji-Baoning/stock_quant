# Panda 数据闭环嫁接 · P5 Web 门户实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从零新建 `web/`（Vue 3 + Vite + vitest + Playwright，独立 `package.json`），交付规格 §10 的五页数据门户——共享顶栏（project-root 指纹、当前页面解析的完整版本哈希、CURRENT 标记）、版本面板与版本详情、数据预览、质量/覆盖证据、更新任务与报告页——全部数据绑定 `dataset_version`，展示纪律执行 §10.2，功能流程与完成条件逐条落 §10.3/§10.4。

**Architecture:** 前端是纯只读消费者：所有数据经一个类型化 API client 模块（`web/src/api/`）取自 §8.2/§9.3 的端点，端点表是契约权威；每个读取视图用 `resolveAndPin(client, requested)` 在入口解析一次版本并钉住响应回显的完整哈希（§4 第 4 条），翻页/过滤只携带钉住的哈希；CURRENT 变化只产生顶栏提示，不自动切换页面。更新任务页以 `GET /api/v1/update-jobs` 探测操作面（200=启用，**503=默认禁用**→只读+等价 CLI），四终态各成视图。测试全部离线 mock：组件测试注入 fake `ApiClient`，E2E 用 Playwright `page.route` mock `/api/v1/**`。

**Tech Stack:** Node v24.0.0 + npm 11.3.0（2026-09-30 本机实查；pnpm 11.24.0 在机但本计划统一用 npm）。Vue 3.5.43、vue-router 5.3.1（已实查其导出含 `createRouter`/`createWebHashHistory`/`RouterLink`/`RouterView`）、Vite 8.3.1、vitest 5.0.3、@vue/test-utils 2.5.1、happy-dom 20.14.5、TypeScript 7.0.2、vue-tsc 3.3.11、@playwright/test 1.63.0。全部精确锁版写入 `web/package.json`，不进任何 Python 依赖。

**Spec:** [docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md](../specs/2026-09-29-panda-data-loop-grafting-design.md) §10（全部，含 §10.3 功能流程与 §10.4 完成条件）、§8.2（API 端点与约束——前端消费的契约权威）、§9.3（操作 API）、§11 相关行。本计划是总路线图 [2026-10-01-panda-data-loop-grafting-implementation.md](2026-10-01-panda-data-loop-grafting-implementation.md) 的 P5 阶段细案（Task 30–33 任务卡的展开；G5 门）。

## Global Constraints

- **工具链（2026-09-30 实查）**：node `v24.0.0`、npm `11.3.0`；所有命令在 `/home/ji/work/program/stock/web` 下执行。开工 Step 先复查 `node --version && npm --version`，与上述不符先报告 owner。
- **前端独立包**：只新建 `web/` 目录与 `web/package.json`；不改 `pyproject.toml`、`requirements.txt` 或任何 Python 文件。依赖全部精确锁版（无 `^`/`~`），`package-lock.json` 一并入仓。
- **本计划只创建 `web/` 下的新文件**，不修改 `web/` 之外的任何文件（src/、tests/、configs/、docs/ 既有文件一概不动）。
- **测试全部离线 mock**：vitest 注入 fake `ApiClient`；Playwright 用 `page.route` mock `/api/v1/**`，不启动、不请求真实后端，不触真实数据与真实凭据。`npm install` 与 `npx playwright install chromium` 需联网一次（依赖与浏览器下载，不是数据联网）。
- **§10.2 展示纪律（逐条，违反即缺陷）**：
  - 不用"数据正常"代替真实状态——页面文案只出现"门禁通过/门禁阻断（N 项）/质量问题 N 条"、验收四态、稳定错误码、加载中/获取失败等真实状态词。
  - CURRENT 是指针标记，不是可信等级——徽标带 `title="CURRENT 是指针标记，不是可信等级"`，且只表示"指针当前指向"。
  - 表格每次翻页都携带同一 resolved version；CURRENT 变化只提示"有新版本"，不自动切换当前页面。
  - 错误只展示稳定 error code 与安全摘要，不显示绝对路径、环境、token 或完整堆栈——`toDisplayError()` 是唯一错误渲染通道（只输出 `code` + `safeMessage`），任何组件不得渲染 `error.stack`。
  - 不从 Panda dist 复制任何 CSS、组件、文案或图片——全新手写源码；样式是一个手写最小 `styles.css`，无 UI 框架。
- **所有 API 数据绑定 `dataset_version`**：每个读取视图经 `resolveAndPin` 解析一次（请求 `current` 或完整哈希），以响应回显的完整 64 位哈希为准钉住；后续请求只带完整哈希；回显不一致时显示稳定码 `version_echo_mismatch` 并清空数据区。
- **凭据零容忍**：token 不进代码、配置、fixture、日志、截图；mock 数据一律用 `"a".repeat(64)` / `"b".repeat(64)` 类假哈希与假指纹。
- **保护在途 WIP（2026-10-01 实查 `git status`）**：已修改 `RUNBOOK.md`、`docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md`、`src/stock_quant/cli.py`、`src/stock_quant/data_model/fetch_coverage.py`、`src/stock_quant/reporting/html.py`、`src/stock_quant/reporting/templates/experiment.html.j2`、`src/stock_quant/research/runner.py`、`tests/integration/test_cli.py`、`tests/integration/test_reports.py`、`tests/integration/test_table_tier_preflight.py`、`tests/unit/test_fetch_coverage.py`、`tests/unit/test_table_tier_preflight.py`，另有未跟踪的 `docs/superpowers/plans/2026-10-01-panda-*.md` 与 `docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md`——不覆盖、不回退、不暂存、不重排；开工时重跑 `git status` 更新此清单；每次提交只 `git add` `web/` 下本任务文件。
- 每任务至少一个独立提交，提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- G5 门：本计划全程离线 mock，可在 P3/P4 未落地时执行；但**真后端联调不在本计划内**，G5 出口核对（真实服务上四页可用）需另列 owner 授权的验证步骤。

## 开工前必须知道的实现形态

1. **仓库现状（2026-09-30 实查，2026-10-01 更新）**：`web/` 不存在（全新前端）；`src/stock_quant/service/` 与 `src/stock_quant/operations/` 均不存在——但 **P3/P4 的阶段计划已成文并冻结了响应模型**（[panda-query-service](2026-10-01-panda-query-service.md)、[panda-operations-scheduler](2026-10-01-panda-operations-scheduler.md)），下面"P5 消费契约 pin"一节已逐字段对齐它们，不再是猜测。Task 1 Step 1 仍须强制对账（若 P3/P4 代码已落地，以运行中的实际响应为准就地修正 `web/src/api/types.ts`）；**分歧时改前端不改服务**。
2. **验收四态词汇**（`src/stock_quant/research/acceptance/models.py:138-153`）：manual verdict 有 `ACCEPTED`/`REJECTED`/`PENDING_CONFIRMATION`；`UNVERIFIED` 是"无任何 verdict 记录"的展示态（§8.2 要求四态可区分，且"存在有效 accepted record"与"最近一次 verdict"分开返回，新的 rejected 不得遮蔽仍可验证的 accepted record）。
3. **发布门禁形态**（`src/stock_quant/data_quality/gates.py:92` `evaluate_publication` → `GateDecision(passed, reasons)`）：注意 §8.2 的 `QualitySummary` 在 P3 冻结模型里**只有 `by_severity: Record<string, number>`**（pin I1），并无 `passed`/`blocking_reasons`/`issue_count`——门禁通过与否读 `acceptance.state`（pin I1），阻断明细读质量分页（pin I3）。前端不得自造 `passed` 字段。
4. **job 状态词汇**（§9.2）：`QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED_BY_SHUTDOWN`；409 稳定码 `update_already_running`（§9.1）；孤儿判定在后端（心跳 + boot_id），前端只消费终态。
5. **attested-boundary 滞后上界**（§7.1）：`announcement_date` = 证实该边界的快照日（与 `raw_effective_from` 同日），不是官方公告日；快照节奏为月度时滞后上界约一个快照周期——证据页必须原样展示该上界。
6. **路线图偏差（有意，成文）**：总路线图 P5 文件结构把浏览器测试写在 `tests/web/`；本阶段细案改为全部自包含在 `web/` 包内（`web/tests/` vitest + `web/e2e/` Playwright），因为 vitest/Playwright 必须由 `web/package.json` 的 npm scripts 驱动，Python 侧测试目录无法运行它们。这是任务卡细化，不是范围变更。
7. **vue-router 5 API（已实查 5.3.1 tarball 导出）**：`createRouter`/`createWebHashHistory`/`RouterLink`/`RouterView` 与 v4 同名同形，本计划的路由代码即按此写。选 hash 路由使静态托管无需服务端重写。
8. **dev 代理必须同时转发两个服务**（pin I12；owner 2026-10-01 裁定）：只读服务（默认 `127.0.0.1:8321`）与操作服务（默认 `127.0.0.1:8642`）是**两个进程、两个端口**，门户必须由同一个源分别转发**两条路径前缀**——dev 用 `vite.config.ts` 的两条 proxy 规则，生产用反向代理做同样的拆分。`STOCK_API_TARGET` 单变量只能指向一个源，**不足以覆盖两个服务**，不得据此推断"一个变量就够"。
   **两条规则的顺序不可互换**：两个服务都自挂 `/api/v1`（只读面是 `datasets`/`experiments`/`health`，操作面是 `update-jobs`），路径段不重叠但**前缀重叠**，因此先放更具体的 `/api/v1/update-jobs` → 8642，再放兜底的 `/api` → 8321；Vite 按 proxy 键的插入顺序取首个匹配（`for (const context in proxies)` 命中即用），具体的在前才不会被兜底吞掉。若实现时发现该顺序在某些 Vite 版本上不生效，改用独立前缀 + `rewrite` 去掉前缀（`/ops-api` → 8642 并 `rewrite: p => p.replace(/^\/ops-api/, "/api")`），但这需要 `web/src/api/client.ts` 分两个 base URL——**属于本计划的备选，须先报 owner**。E2E 全程 `page.route` 拦截，代理不会被触达；因此代理配置**不在 E2E 覆盖范围内**，改它不会让 E2E 变红。

## P5 消费契约 pin（对 §8.2/§9.3 的绑定解释）

端点表本身逐字来自 §8.2/§9.3；响应模型是 P5 侧 pin（P3/P4 冻结响应模型后，Task 1 Step 1 逐字段对账，差异修正落在 `web/src/api/types.ts`，并记进提交信息）：

**下面每个字段名都已按 P3/P4 冻结的响应模型逐条核对**（2026-10-01 重写；先前的 P5 侧猜测名 `current_version`/`published_at_evidence`/`quality_summary`/`next_offset`/扁平 `{code,message}`/`date_start`+`date_end`/404-禁用 全部作废）。**P3/P4 是契约权威，P5 是消费者**；Task 1 Step 1 的对账若再发现差异，改 `web/src/api/types.ts` 而不是改服务。

| # | pin | 内容 |
| --- | --- | --- |
| I1 | 数据集列表 | `GET /api/v1/datasets` → `{current: string\|null, datasets: DatasetSummary[]}`；`DatasetSummary {dataset_version, is_current, created_at: string\|null, table_count, quality: {by_severity: Record<string, number>}, acceptance: {state, has_valid_accepted_record, latest_verdict: string\|null, record_count}}`。**没有** `current_version`、`published_at_evidence`、`quality_summary` 这些名字；"当前版本"读 `current`（顶栏）或条目自己的 `is_current` |
| I2 | 数据集详情 | `GET /api/v1/datasets/{v}` → `{dataset_version, requested_version, manifest, tables: TableMeta[], quality, acceptance}`；`TableMeta {name, row_count, schema_version}`——表清单是**对象数组**，不是字符串数组。**覆盖段不在响应顶层**：`table_fetch_coverage` 在完整的 `manifest` 里，读 `manifest.build_config.table_fetch_coverage`（`data_pipeline.py:629-630`、`dataset.py:286`）；每段形如 `{table, kind∈{fetched,carried,not_fetched}, window_start, window_end, reason?}`（`fetch_coverage.py:87-96`）——`reason` 为 `None` 时该键**整个省略**，不是 `null` |
| I3 | 质量分页 | `GET /api/v1/datasets/{v}/quality?offset&limit` → `{dataset_version, requested_version, offset, limit, total, issues: QualityIssueView[]}`；P3 的 `QualityIssueView {severity: str\|None, code: str\|None, table: str\|None, symbol: str\|None, trade_date: str\|None, details: dict\|None}`（TS 侧本地别名 `QualityIssueRecord`，字段逐字相同）。**没有 `summary` 字段**——摘要要前端从 `code`+`details` 拼，或直接展示 `code`。**分页游标字段是 `total` 不是 `next_offset`**——前端用 `offset + issues.length < total` 判有无下一页 |
| I4 | 表预览参数与回显 | `GET /api/v1/datasets/{v}/tables/{table}?columns&symbol&trade_date&offset&limit`（limit 默认 100、最大 500，§8.2）；`columns` 为逗号分隔白名单。**日期过滤是单值 `trade_date`（ISO 日），没有 `date_start`/`date_end` 区间**——P3 的 `TableArguments` 只有 `trade_date: string\|null`，多传的区间参数会被 FastAPI 静默忽略。响应 `{dataset_version, table, arguments: TableArguments, columns: string[], rows: Record<string, JsonValue>[]}`；`arguments` 就是 §8.2 点名的回显字段（OpenBB 先例），**不叫 `request`** |
| I5 | 实验列表 | `GET /api/v1/experiments` → `{experiments: ExperimentSummary[]}`（**对象不是裸数组**）；`ExperimentSummary {experiment_id, status: string\|null, dataset_version: string\|null, universe_version: string\|null, evaluation_reason: string\|null}` |
| I6 | 报告可用性 | 可用性由前端逐实验 `GET /api/v1/experiments/{id}/report` 探测（200 = 有既有静态 HTML；404 = `experiment_not_found`/`report_not_found`）；前端不在请求中重建报告 |
| I7 | 健康探针 | `GET /api/v1/health` → `{status: "ok", project_root_fingerprint: string}`（16 位十六进制，故意不含绝对路径；前端只显示/比对，不反解） |
| I8 | 错误信封 | **所有**非 2xx 响应体是**嵌套** `{"error": {code, message?, ...}}`——**不是**扁平的 `{code, message}`。P3 的 `ErrorBody`/`ErrorResponse` 与 P4 的 `JSONResponse` 同形。信封缺失/不可解析时前端记 `http_<status>` |
| I9 | 操作面开关信号 | `GET /api/v1/update-jobs` → **200**（启用）或 **503** `{"error":{"code":"operations_disabled"}}`（默认禁用）。**不是 404**——操作面的路由任何时候都注册着，禁用是拒绝服务而不是路由不存在；前端必须按 `status === 503` 切只读视图 |
| I10 | 任务列表与详情 | 列表 → `{"jobs": UpdateJobSummary[]}`（**外层是对象，不是裸数组**）；`UpdateJobSummary {job_id, status, created_at, updated_at, run_id: string\|null, dataset_version: string\|null}`。详情 `GET /api/v1/update-jobs/{id}` → 上述六个字段 + `{pid: number\|null, boot_id: string\|null, heartbeat_at: string\|null, exit_code: number\|null, failure_reason: string\|null, failure_detail: string\|null, stdout_tail: string, stderr_tail: string}`——**日志是已脱敏的字符串，不是 `logs_tail: string[]`**；失败原因字段叫 `failure_reason`，**不叫 `error_code`**。未知 id → 404 `{"error":{"code":"job_not_found"}}` |
| I11 | 启动任务 | `POST /api/v1/update-jobs` → **201** `{job_id, status}`（**不是完整 job 对象**，详情要另取）；参数非法 → 422 `{"error":{"code":"invalid_parameter", parameter, reason}}`；已有活跃 job → 409 `{"error":{"code":"update_already_running", job_id}}`——**`job_id` 嵌在 `error` 里**，不是顶层字段 |
| I12 | 服务端口与单源 | 只读服务默认 `127.0.0.1:8321`（`python -m stock_quant.service`），操作服务默认 `127.0.0.1:8642`（`python -m stock_quant.operations.serve`）。二者是**两个进程、两个端口**。**owner 裁定（2026-10-01）：门户必须同时转发两条前缀**——dev 用 `vite.config.ts` 的两条 proxy 规则，生产用反向代理做同样的拆分；`STOCK_API_TARGET` 只能指向一个源，**不足以覆盖两个服务**。两服务同挂 `/api/v1`、路径段不重叠（只读 `datasets`/`experiments`/`health`，操作 `update-jobs`），因此**具体规则必须排在兜底规则之前**（见"开工前必须知道的实现形态"第 8 条），实现形态固定在 Task 1 的 `vite.config.ts` 里 |

**I4 的日期过滤（owner 已裁定，2026-10-01）**：P3 实现的是单值 `trade_date`（spec §8.2 的表预览参数表只列了分区/列/行数级的过滤，未点名区间），而数据门户的"按区间看成分/行情"是最自然的诉求。曾列出的两条路：(a) 门户只提供单日过滤，区间浏览用多次单日请求或后续单独提需求；(b) 现在就给 P3 加 `date_start`/`date_end`（`TableArguments` 加两个可空字段，SQL 侧加 `>=`/`<=` 子句）。

**裁定：(a)。** 门户本批只提供**单日 `trade_date` 过滤**，不做区间输入控件；区间浏览**推迟到真实使用提出诉求时**再按 (b) 走 P3 的响应模型变更（届时须同步 `web/src/api/types.ts` 与 `TablePreviewParams`）。理由：P3/P4 是契约权威、P5 是纯消费者，为一个尚无使用证据的诉求改已冻结的响应模型，会让 P3 的契约复核与测试全部返工；而 (a) 的代价只是"看成分变化要连点两次日期"。**本节与 §10.1 的页面描述、`DataPreviewPage.vue` 的 `filters` 形态（`{trade_date, symbol}`）都按 (a) 执行，不存在待裁定的分支。**

## 文件结构（全部新增，均在 `web/` 内）

| 文件 | 职责 |
| --- | --- |
| `web/package.json`、`web/package-lock.json` | 独立包；依赖精确锁版；scripts：dev/build/test/typecheck/e2e |
| `web/tsconfig.json`、`web/vite.config.ts`、`web/playwright.config.ts`、`web/index.html`、`web/.gitignore` | 工具链配置（vitest 用 happy-dom；Playwright 拉起 vite dev server） |
| `web/src/main.ts`、`web/src/App.vue`、`web/src/styles.css` | 入口、应用壳（提供 API client + 顶栏 + RouterView）、手写最小样式 |
| `web/src/router.ts` | 六路由 + `NAV_ITEMS`（§10.3 固定导航序） |
| `web/src/api/types.ts` | 消费契约的全部 TS 类型（pin 表的实现物） |
| `web/src/api/client.ts` | `ApiClient` 接口、`createApiClient`、`ApiError`、`conflictJobId`、provide/inject |
| `web/src/api/errors.ts` | `toDisplayError`——唯一错误渲染通道（稳定码 + 安全摘要） |
| `web/src/api/quality.ts` | 质量摘要派生（`blockingIssueCount`/`totalIssueCount`/`isBlockingSeverity`）——契约无 `passed`/`blocking_reasons`，门禁状态读 `acceptance.state` |
| `web/src/stores/version.ts` | 版本 pin 状态（resolved/current/指纹）+ `resolveAndPin` + `hasNewerCurrent` |
| `web/src/components/AppTopBar.vue` | 指纹、已解析完整哈希（可复制）、CURRENT 徽标、新版本提示、固定导航 |
| `web/src/pages/VersionsPage.vue` | 版本面板：列表 + CURRENT 标记 + 发布时间证据 + 表计数 + 质量摘要 + 验收分栏 |
| `web/src/pages/VersionDetailPage.vue` | 版本详情：回显完整哈希、表元数据、质量摘要、验收两栏分开、质量问题 |
| `web/src/pages/DataPreviewPage.vue` | 钉版本表预览：表/列/日期/symbol 过滤、分页、回显校验 |
| `web/src/pages/CoverageEvidencePage.vue` | 覆盖段（含 not_fetched 理由）、UNTRUSTED 行、质量问题、attested-boundary 上界 |
| `web/src/pages/UpdateJobsPage.vue` | 操作面探测、允许参数提交、轮询、四终态、409 链接、禁用只读 + 等 CLI |
| `web/src/pages/ReportsPage.vue` | 实验列表 + dataset version + 静态报告链接/缺失态 + "不在 Web"说明 |
| `web/tests/helpers.ts` | fake client、mount 帮手、固定 mock fixtures（假哈希） |
| `web/tests/*.spec.ts`（8 个） | 逐页/逐模块组件测试（vitest） |
| `web/e2e/portal.spec.ts` | §10.4 逐条 E2E（Playwright，全 mock） |

---

### Task 1: 前端骨架——工具链、应用壳、路由与 API client

**Files:**
- Create: `web/package.json`、`web/tsconfig.json`、`web/vite.config.ts`、`web/index.html`、`web/.gitignore`、`web/src/main.ts`、`web/src/App.vue`、`web/src/styles.css`、`web/src/router.ts`、`web/src/components/AppTopBar.vue`、`web/src/pages/`（六个页面）、`web/src/api/types.ts`、`web/src/api/client.ts`、`web/src/api/errors.ts`、`web/src/api/quality.ts`、`web/src/stores/version.ts`、`web/tests/helpers.ts`、`web/tests/app-shell.spec.ts`、`web/tests/api-client.spec.ts`、`web/tests/version-store.spec.ts`、`web/tests/top-bar.spec.ts`

**Interfaces:**
- Produces（后续任务全部依赖，精确名字）:
  - `ApiClient` 方法：`health()`、`listDatasets()`、`getDataset(version: string)`、`listQualityIssues(version, offset, limit)`、`previewTable(version, table, params)`、`listExperiments()`、`probeExperimentReport(experimentId)`、`listUpdateJobs()`、`getUpdateJob(jobId)`、`startUpdateJob(request)`。
  - `createApiClient(options?: { baseUrl?: string; fetchImpl?: typeof fetch }): ApiClient`；`class ApiError { status; code; safeMessage; body }`；`conflictJobId(error): string | null`；`apiClientKey`（InjectionKey）；`useApiClient(): ApiClient`。
  - `resolveAndPin(client, requestedVersion): Promise<DatasetDetailResponse>`（钉住回显哈希）；`versionPinState`（reactive：`resolvedVersion`/`currentVersion`/`projectFingerprint`）；`setCurrentVersion(v)`、`setProjectFingerprint(v)`、`clearResolvedVersion()`、`hasNewerCurrent`（computed）。
  - `toDisplayError(error): { code: string; message: string }`。
  - 路由：`/versions`、`/versions/:version`、`/preview`、`/evidence`、`/jobs`、`/reports`；`NAV_ITEMS`；`createPortalRouter()`。

- [ ] **Step 1: 契约对账（先读再接，强制）**

Run: `node --version && npm --version` → Expected: `v24.0.0` / `11.3.0`（不符先报告 owner）。
Run: `ls /home/ji/work/program/stock/src/stock_quant/service /home/ji/work/program/stock/src/stock_quant/operations 2>&1`

- 两个目录都不存在（2026-09-30 实查）：按本计划的 `web/src/api/types.ts` 与 pin 表 I1–I12 开发，无需改动。
- 任一目录已存在（P3/P4 已落地）：逐字段核对其响应模型/契约测试 fixture 与本计划 `types.ts` 的差异，以实际实现为准**就地修正下文 Step 5 的类型代码再写入**，并在该步提交信息里注明修正点（例：`align table preview echo field name with service contract`）。对账只许改 `web/src/api/types.ts` 与 `client.ts` 的字段名/形态，不许改端点表（端点表以 §8.2/§9.3 为准）。

- [ ] **Step 2: 脚手架（本步无行为测试，构建通过即验证）**

创建 `web/.gitignore`：

```
node_modules/
dist/
test-results/
playwright-report/
```

创建 `web/package.json`（依赖精确锁版，2026-09-30 npm registry 实查版本）：

```json
{
  "name": "stock-quant-web-portal",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "engines": { "node": ">=24.0.0" },
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "vue-tsc --noEmit",
    "e2e": "playwright test"
  },
  "dependencies": {
    "vue": "3.5.43",
    "vue-router": "5.3.1"
  },
  "devDependencies": {
    "@playwright/test": "1.63.0",
    "@vitejs/plugin-vue": "6.0.9",
    "@vue/test-utils": "2.5.1",
    "happy-dom": "20.14.5",
    "typescript": "7.0.2",
    "vite": "8.3.1",
    "vitest": "5.0.3",
    "vue-tsc": "3.3.11"
  }
}
```

创建 `web/tsconfig.json`：

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "verbatimModuleSyntax": true,
    "isolatedModules": true
  },
  "include": [
    "src/**/*.ts",
    "src/**/*.vue",
    "tests/**/*.ts",
    "e2e/**/*.ts",
    "vite.config.ts",
    "playwright.config.ts"
  ]
}
```

创建 `web/vite.config.ts`：

```ts
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

// pin I12：两个服务、两个端口。只读面（datasets/experiments/health）与操作面
// （update-jobs）都自挂 /api/v1，路径段不重叠但前缀重叠，所以必须两条规则，
// 且更具体的 update-jobs 在前——Vite 按 proxy 键的插入顺序取首个匹配。
const READ_API_TARGET = process.env.STOCK_API_TARGET ?? "http://127.0.0.1:8321";
const OPS_API_TARGET = process.env.STOCK_OPS_API_TARGET ?? "http://127.0.0.1:8642";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api/v1/update-jobs": {
        target: OPS_API_TARGET,
        changeOrigin: false,
      },
      // STOCK_API_TARGET 只能指向一个源（pin I12）：默认指向只读服务，
      // 操作服务另有 STOCK_OPS_API_TARGET，二者不可合并成一个变量。
      "/api": {
        target: READ_API_TARGET,
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "happy-dom",
    include: ["tests/**/*.spec.ts"],
  },
});
```

创建 `web/index.html`：

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Stock Quant 数据门户</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

创建 `web/src/styles.css`（手写最小样式，不引入任何 UI 框架）：

```css
:root { font-family: system-ui, sans-serif; color: #1b1f24; background: #f7f8fa; }
* { box-sizing: border-box; }
body { margin: 0; }
code { font-family: ui-monospace, monospace; font-size: 0.9em; background: #eef0f3; padding: 0 4px; border-radius: 3px; word-break: break-all; }
pre { background: #101418; color: #d7dde3; padding: 12px; overflow-x: auto; border-radius: 6px; }
.top-bar { display: flex; flex-wrap: wrap; gap: 12px 18px; align-items: center; padding: 10px 16px; background: #ffffff; border-bottom: 1px solid #d9dee4; }
.brand { font-weight: 700; }
.nav a { margin-right: 12px; color: #2459a8; text-decoration: none; }
.nav a.router-link-active { font-weight: 700; text-decoration: underline; }
.fingerprint, .resolved { font-size: 0.9em; color: #4a5158; }
.badge { display: inline-block; margin-left: 6px; padding: 1px 8px; border: 1px solid #2a7de1; border-radius: 10px; color: #2a7de1; font-size: 0.8em; }
.hint { margin: 8px 16px; padding: 8px 12px; background: #fff6e0; border: 1px solid #e5c36b; border-radius: 6px; }
.page { padding: 16px 24px; max-width: 1200px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 20px; background: #fff; }
th, td { border: 1px solid #d9dee4; padding: 6px 10px; text-align: left; font-size: 0.92em; vertical-align: top; }
th { background: #eef0f3; }
.error { color: #b3261e; background: #fdecea; border: 1px solid #f0b4ae; padding: 8px 12px; border-radius: 6px; }
.warn { color: #8a5b00; }
.acceptance-columns { display: flex; gap: 24px; }
.acceptance-columns > div { border: 1px solid #d9dee4; padding: 8px 16px; background: #fff; }
form label { display: inline-block; margin: 4px 14px 4px 0; font-size: 0.92em; }
form input, form select { margin-left: 6px; }
fieldset { border: 1px solid #d9dee4; margin: 10px 0; }
.copy { margin-left: 6px; }
button { cursor: pointer; }
blockquote { margin: 8px 0; padding: 8px 14px; border-left: 4px solid #2a7de1; background: #fff; }
```

创建最小应用壳 `web/src/main.ts`：

```ts
import { createApp } from "vue";
import App from "./App.vue";
import "./styles.css";

createApp(App).mount("#app");
```

`web/src/App.vue`（Step 4 会替换为最终形态）：

```vue
<script setup lang="ts"></script>

<template>
  <div class="app">
    <main class="page">
      <h1>Stock Quant 数据门户</h1>
      <p data-testid="scaffold">骨架搭建中</p>
    </main>
  </div>
</template>
```

Run: `cd /home/ji/work/program/stock/web && npm install && npm run build`
Expected: 依赖安装成功、`vite build` PASS（exit 0）。

```bash
git add web/.gitignore web/package.json web/package-lock.json web/tsconfig.json web/vite.config.ts web/index.html web/src/main.ts web/src/App.vue web/src/styles.css
git commit -m "chore(web): scaffold vite vue toolchain with pinned deps

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

- [ ] **Step 3: 写失败测试——应用壳导航（§10.3 页面地图）**

创建 `web/tests/helpers.ts`（后续所有组件测试共用；本步先创建，`fakeClient`/fixtures 在 Step 5 补齐后追加——注意：本步文件只需 `mountPage` 与 `createPortalRouter` 相关导入可编译即可，为避免引用未建模块，本步的 helpers.ts 只含下面这段，Step 5 再整体替换为完整版）：

```ts
import { mount, type VueWrapper } from "@vue/test-utils";
import type { Component } from "vue";
import { createPortalRouter } from "../src/router";

export function mountPage(component: Component, client: unknown): VueWrapper {
  return mount(component, {
    global: { plugins: [createPortalRouter()] },
  });
}
```

创建 `web/tests/app-shell.spec.ts`：

```ts
import { describe, expect, it } from "vitest";
import App from "../src/App.vue";
import { NAV_ITEMS } from "../src/router";
import { mountPage } from "./helpers";

describe("应用骨架", () => {
  it("顶栏按 §10.3 固定顺序渲染五个导航入口", async () => {
    const wrapper = mountPage(App, undefined);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const links = wrapper.get('[data-testid="main-nav"]').findAll("a");
    expect(links.map((link) => link.text())).toEqual(NAV_ITEMS.map((item) => item.label));
    expect(links.map((link) => link.attributes("href"))).toEqual(
      NAV_ITEMS.map((item) => `#${item.path}`),
    );
  });
});
```

- [ ] **Step 4: 跑测试确认失败**

Run: `cd /home/ji/work/program/stock/web && npm test -- tests/app-shell.spec.ts`
Expected: FAIL — `Cannot find module '../src/router'`（或 `Unable to locate [data-testid="main-nav"]`，取决于模块解析顺序）。这是预期失败理由：导航尚未实现。

- [ ] **Step 5: 实现路由、六个页面桩、顶栏（本周期导航版）与应用壳**

创建 `web/src/router.ts`：

```ts
import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";
import VersionsPage from "./pages/VersionsPage.vue";
import VersionDetailPage from "./pages/VersionDetailPage.vue";
import DataPreviewPage from "./pages/DataPreviewPage.vue";
import CoverageEvidencePage from "./pages/CoverageEvidencePage.vue";
import UpdateJobsPage from "./pages/UpdateJobsPage.vue";
import ReportsPage from "./pages/ReportsPage.vue";

/** §10.3 页面地图：导航固定为 版本面板 → 数据预览 → 质量/覆盖证据 → 更新任务 → 报告。 */
export const NAV_ITEMS = [
  { label: "版本面板", path: "/versions" },
  { label: "数据预览", path: "/preview" },
  { label: "质量/覆盖证据", path: "/evidence" },
  { label: "更新任务", path: "/jobs" },
  { label: "报告", path: "/reports" },
] as const;

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/versions" },
  { path: "/versions", name: "versions", component: VersionsPage },
  { path: "/versions/:version", name: "version-detail", component: VersionDetailPage },
  { path: "/preview", name: "preview", component: DataPreviewPage },
  { path: "/evidence", name: "evidence", component: CoverageEvidencePage },
  { path: "/jobs", name: "jobs", component: UpdateJobsPage },
  { path: "/reports", name: "reports", component: ReportsPage },
];

export function createPortalRouter() {
  return createRouter({ history: createWebHashHistory(), routes });
}

export const router = createPortalRouter();
```

创建六个页面桩（内容完全一致，仅 `<h1>` 文本不同，按下表；Task 2–4 逐个以失败测试替换为真实页面）：

| 文件 | `<h1>` 文本 |
| --- | --- |
| `web/src/pages/VersionsPage.vue` | `版本面板` |
| `web/src/pages/VersionDetailPage.vue` | `版本详情` |
| `web/src/pages/DataPreviewPage.vue` | `数据预览` |
| `web/src/pages/CoverageEvidencePage.vue` | `质量/覆盖证据` |
| `web/src/pages/UpdateJobsPage.vue` | `更新任务` |
| `web/src/pages/ReportsPage.vue` | `报告` |

桩文件模板（以 `DataPreviewPage.vue` 为例）：

```vue
<template>
  <section>
    <h1>数据预览</h1>
    <p data-testid="page-pending">本页由后续任务实现</p>
  </section>
</template>
```

创建 `web/src/components/AppTopBar.vue`（本周期只有导航；Step 8 扩展为完整版）：

```vue
<script setup lang="ts">
import { NAV_ITEMS } from "../router";
</script>

<template>
  <header class="top-bar" data-testid="top-bar">
    <span class="brand">Stock Quant 数据门户</span>
    <nav class="nav" data-testid="main-nav">
      <RouterLink v-for="item in NAV_ITEMS" :key="item.path" :to="item.path">{{ item.label }}</RouterLink>
    </nav>
  </header>
</template>
```

`web/src/main.ts` 替换为最终形态：

```ts
import { createApp } from "vue";
import App from "./App.vue";
import { router } from "./router";
import "./styles.css";

createApp(App).use(router).mount("#app");
```

`web/src/App.vue` 替换为：

```vue
<script setup lang="ts">
import AppTopBar from "./components/AppTopBar.vue";
</script>

<template>
  <div class="app">
    <AppTopBar />
    <main class="page"><RouterView /></main>
  </div>
</template>
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd /home/ji/work/program/stock/web && npm test -- tests/app-shell.spec.ts && npm run build`
Expected: PASS（导航五链接顺序与 href 全对）；build PASS。

- [ ] **Step 7: 提交**

```bash
git add web/src/router.ts web/src/main.ts web/src/App.vue web/src/components/AppTopBar.vue web/src/pages web/tests/helpers.ts web/tests/app-shell.spec.ts
git commit -m "feat(web): app shell with fixed five-page navigation

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

- [ ] **Step 8: 写失败测试——API client（契约 pin I1–I11）**

`web/tests/helpers.ts` 整体替换为完整版（fake client + mock fixtures；`mountAt` 供带路由参数的页面用）：

```ts
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import type { Component } from "vue";
import { apiClientKey, type ApiClient } from "../src/api/client";
import { createPortalRouter } from "../src/router";
import type {
  DatasetDetailResponse,
  DatasetListResponse,
  ExperimentsResponse,
  HealthResponse,
  QualityListResponse,
  TablePreviewResponse,
  UpdateJob,
  UpdateJobSummary,
} from "../src/api/types";

export function fakeClient(overrides: Partial<ApiClient> = {}): ApiClient {
  const unstubbed = async (): Promise<never> => {
    throw new Error("fake client: endpoint not stubbed");
  };
  const base: ApiClient = {
    health: unstubbed,
    listDatasets: unstubbed,
    getDataset: unstubbed,
    listQualityIssues: unstubbed,
    previewTable: unstubbed,
    listExperiments: unstubbed,
    probeExperimentReport: unstubbed,
    listUpdateJobs: unstubbed,
    getUpdateJob: unstubbed,
    startUpdateJob: unstubbed,
  };
  return Object.assign(base, overrides);
}

export function mountPage(component: Component, client: ApiClient): VueWrapper {
  return mount(component, {
    global: {
      plugins: [createPortalRouter()],
      provide: { [apiClientKey as symbol]: client },
    },
  });
}

export async function mountAt(
  component: Component,
  client: ApiClient,
  path: string,
): Promise<VueWrapper> {
  const router = createPortalRouter();
  await router.push(path);
  return mount(component, {
    global: { plugins: [router], provide: { [apiClientKey as symbol]: client } },
  });
}

export { flushPromises };

/** 假哈希（非真实数据；凭据零容忍）。 */
export const HASH_A = "a".repeat(64);
export const HASH_B = "b".repeat(64);

export function healthResponse(): HealthResponse {
  // 16 位十六进制，与 P3 `project_root_fingerprint` 的形态一致（pin I7）。
  return { status: "ok", project_root_fingerprint: "0123456789abcdef" };
}

export function datasetListResponse(
  current: string = HASH_A,
  versions: string[] = [HASH_A, HASH_B],
): DatasetListResponse {
  return {
    current,
    datasets: versions.map((version) => ({
      dataset_version: version,
      is_current: version === current,
      created_at: "2026-10-01T08:00:00+08:00",
      table_count: 4,
      quality: {
        by_severity: version === HASH_A ? {} : { FATAL: 1, WARNING: 1 },
      },
      acceptance: {
        state: version === HASH_A ? "ACCEPTED" : "UNVERIFIED",
        has_valid_accepted_record: version === HASH_A,
        latest_verdict: version === HASH_A ? "ACCEPTED" : null,
        record_count: version === HASH_A ? 1 : 0,
      },
    })),
  };
}

/** 覆盖段在 manifest 里（pin I2），不在响应顶层。 */
function buildConfig(): Record<string, unknown> {
  return {
    baseline_version: null,
    table_fetch_coverage: {
      daily_bar: [
        {
          table: "daily_bar",
          kind: "fetched",
          window_start: "2026-09-01",
          window_end: "2026-09-30",
        },
      ],
      basic_factor: [
        {
          table: "basic_factor",
          kind: "carried",
          window_start: "2026-09-01",
          window_end: "2026-09-29",
        },
        {
          table: "basic_factor",
          kind: "not_fetched",
          window_start: "2026-09-30",
          window_end: "2026-09-30",
          // reason 非空时才出现；None 时整个键省略（fetch_coverage.py）。
          reason: "source_unavailable",
        },
      ],
    },
  };
}

export function datasetDetailResponse(version: string = HASH_A): DatasetDetailResponse {
  return {
    dataset_version: version,
    requested_version: version,
    manifest: { build_config: buildConfig() },
    tables: [
      { name: "daily_bar", row_count: 120, schema_version: "1" },
      { name: "basic_factor", row_count: 118, schema_version: "1" },
      { name: "basic_factor_coverage", row_count: 4, schema_version: "1" },
      { name: "daily_bar_coverage", row_count: 2, schema_version: "1" },
    ],
    quality: { by_severity: { WARNING: 1 } },
    acceptance: {
      state: "ACCEPTED",
      has_valid_accepted_record: true,
      latest_verdict: "REJECTED",
      record_count: 2,
    },
  };
}

export function qualityListResponse(version: string = HASH_A): QualityListResponse {
  return {
    dataset_version: version,
    requested_version: version,
    offset: 0,
    limit: 100,
    total: 2,
    issues: [
      {
        severity: "FATAL",
        code: "fetch_coverage_gap",
        table: "basic_factor",
        symbol: null,
        trade_date: "2026-09-30",
        details: { gap_start: "2026-09-30" },
      },
      {
        severity: "WARNING",
        code: "schema_mismatch",
        table: null,
        symbol: null,
        trade_date: null,
        details: null,
      },
    ],
  };
}

export function previewResponse(
  version: string = HASH_A,
  offset: number = 0,
  rowCount: number = 100,
): TablePreviewResponse {
  return {
    dataset_version: version,
    table: "daily_bar",
    arguments: {
      requested_version: version,
      table: "daily_bar",
      columns: ["trade_date", "symbol", "close"],
      symbol: null,
      trade_date: null,
      offset,
      limit: 100,
    },
    columns: ["trade_date", "symbol", "close"],
    rows: Array.from({ length: rowCount }, (_, index) => ({
      trade_date: `2026-09-${String(((offset + index) % 30) + 1).padStart(2, "0")}`,
      symbol: "000001.SZ",
      close: offset + index,
    })),
  };
}

export function coveragePreviewResponse(version: string = HASH_A): TablePreviewResponse {
  return {
    dataset_version: version,
    table: "corporate_action_coverage",
    arguments: {
      requested_version: version,
      table: "corporate_action_coverage",
      columns: ["trade_date", "symbol", "status", "reason"],
      symbol: null,
      trade_date: null,
      offset: 0,
      limit: 500,
    },
    columns: ["trade_date", "symbol", "status", "reason"],
    rows: [
      { trade_date: "2026-09-28", symbol: "600000.SH", status: "UNTRUSTED", reason: "FACTS_INCOMPLETE" },
      { trade_date: "2026-09-29", symbol: "600000.SH", status: "VERIFIED", reason: "" },
    ],
  };
}

export function verifiedOnlyCoveragePreview(version: string = HASH_A): TablePreviewResponse {
  const response = coveragePreviewResponse(version);
  return { ...response, rows: response.rows.filter((row) => row.status !== "UNTRUSTED") };
}

export function updateJobSummary(overrides: Partial<UpdateJobSummary> = {}): UpdateJobSummary {
  return {
    job_id: "job-0001",
    status: "RUNNING",
    created_at: "2026-10-01T08:00:00+08:00",
    updated_at: "2026-10-01T08:01:00+08:00",
    run_id: null,
    dataset_version: null,
    ...overrides,
  };
}

export function updateJob(overrides: Partial<UpdateJob> = {}): UpdateJob {
  return {
    ...updateJobSummary(),
    pid: 4321,
    boot_id: "2fdf3cf0-97bf-4896-bd6f-1a5b8841637d",
    heartbeat_at: "2026-10-01T08:01:00+08:00",
    exit_code: null,
    failure_reason: null,
    failure_detail: null,
    // 已脱敏的字符串（pin I10），不是 string[]。
    stdout_tail: "2026-10-01T08:00:01+08:00 update started",
    stderr_tail: "",
    ...overrides,
  };
}

export function experimentsResponse(): ExperimentsResponse {
  // pin I5：ExperimentSummary 的五个字段一个都不少（可空字段显式给 null）。
  return {
    experiments: [
      {
        experiment_id: "exp-2026q3",
        status: "SUCCEEDED",
        dataset_version: HASH_A,
        universe_version: "tw",
        evaluation_reason: null,
      },
      {
        experiment_id: "exp-2026q2",
        status: null,
        dataset_version: HASH_B,
        universe_version: null,
        evaluation_reason: "no_report",
      },
    ],
  };
}
```

创建 `web/tests/api-client.spec.ts`：

```ts
import { describe, expect, it, vi } from "vitest";
import { ApiError, conflictJobId, createApiClient } from "../src/api/client";
import { HASH_A, HASH_B, datasetDetailResponse, previewResponse, updateJob } from "./helpers";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("api client（pin I1–I11）", () => {
  it("GET /api/v1/datasets 返回版本列表并解析 acceptance 字段", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, {
      current: HASH_A,
      datasets: [
        {
          dataset_version: HASH_A,
          is_current: true,
          created_at: "2026-10-01T08:00:00+08:00",
          table_count: 1,
          quality: { by_severity: {} },
          acceptance: {
            state: "ACCEPTED",
            has_valid_accepted_record: true,
            latest_verdict: "ACCEPTED",
            record_count: 1,
          },
        },
      ],
    }));
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const response = await client.listDatasets();
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/datasets", undefined);
    expect(response.current).toBe(HASH_A);
    expect(response.datasets[0].acceptance.latest_verdict).toBe("ACCEPTED");
  });

  it("请求 current 别名；dataset_version 回显永远是完整哈希", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, datasetDetailResponse()));
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const detail = await client.getDataset("current");
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/datasets/current", undefined);
    expect(detail.dataset_version).toBe(HASH_A);
  });

  it("表预览按 pin I4 组装查询串（单值 trade_date，无区间），并回显请求参数", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, previewResponse()));
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    await client.previewTable(HASH_A, "daily_bar", {
      columns: ["trade_date", "close"],
      trade_date: "2026-09-30",
      symbol: "000001.SZ",
      offset: 100,
      limit: 100,
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      `/api/v1/datasets/${HASH_A}/tables/daily_bar?columns=trade_date%2Cclose&symbol=000001.SZ&trade_date=2026-09-30&offset=100&limit=100`,
      undefined,
    );
  });

  it("非 2xx 抛 ApiError：优先取信封稳定码，缺失时用 http_<status>", async () => {
    const withEnvelope = createApiClient({
      fetchImpl: vi.fn(async () =>
        jsonResponse(400, { error: { code: "query_time_budget_exceeded", message: "查询超时" } }),
      ) as unknown as typeof fetch,
    });
    await expect(withEnvelope.previewTable(HASH_A, "daily_bar", {
      columns: null, trade_date: null, symbol: null, offset: 0, limit: 100,
    })).rejects.toMatchObject({ status: 400, code: "query_time_budget_exceeded", safeMessage: "查询超时" });

    const bare = createApiClient({
      fetchImpl: vi.fn(async () => jsonResponse(500, "oops")) as unknown as typeof fetch,
    });
    await expect(bare.listDatasets()).rejects.toBeInstanceOf(ApiError);
    await expect(bare.listDatasets()).rejects.toMatchObject({ code: "http_500" });
  });

  it("409 冲突：conflictJobId 提取运行中 job id（pin I11，job_id 嵌在 error 里）", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(409, { error: { code: "update_already_running", job_id: "job-0001" } }),
    );
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const attempt = client.startUpdateJob({
      start: null, end: null, sources: null, disclosure_lookback_days: null,
    });
    await expect(attempt).rejects.toBeInstanceOf(ApiError);
    try {
      await client.startUpdateJob({ start: null, end: null, sources: null, disclosure_lookback_days: null });
    } catch (error) {
      expect(conflictJobId(error)).toBe("job-0001");
    }
    expect(conflictJobId(new Error("x"))).toBeNull();
  });

  it("POST /api/v1/update-jobs 携带 JSON 请求体；GET job 返回单对象", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/update-jobs" && init?.method === "POST") {
        expect(init.headers).toMatchObject({ "content-type": "application/json" });
        expect(JSON.parse(String(init.body))).toEqual({
          start: null, end: null, sources: null, disclosure_lookback_days: null,
        });
        // pin I11：201 只回 `{job_id, status}`，不是完整 job 对象。
        return jsonResponse(201, { job_id: "job-0002", status: "QUEUED" });
      }
      return jsonResponse(200, updateJob({ job_id: "job-0002" }));
    });
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const created = await client.startUpdateJob({ start: null, end: null, sources: null, disclosure_lookback_days: null });
    expect(created.status).toBe("QUEUED");
    const fetched = await client.getUpdateJob("job-0002");
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/update-jobs/job-0002", undefined);
    expect(fetched.job_id).toBe("job-0002");
  });

  it("报告探测 200/404（pin I6）", async () => {
    const ok = createApiClient({
      fetchImpl: vi.fn(async () => jsonResponse(200, "<html></html>")) as unknown as typeof fetch,
    });
    const missing = createApiClient({
      fetchImpl: vi.fn(async () =>
        jsonResponse(404, { error: { code: "report_not_found", message: "" } }),
      ) as unknown as typeof fetch,
    });
    expect(await ok.probeExperimentReport("exp-1")).toBe(true);
    expect(await missing.probeExperimentReport("exp-2")).toBe(false);
  });

  it("操作面 503 信号可被上层用 ApiError.status 判别（pin I9）", async () => {
    const disabled = createApiClient({
      fetchImpl: vi.fn(async () =>
        jsonResponse(503, { error: { code: "operations_disabled" } }),
      ) as unknown as typeof fetch,
    });
    try {
      await disabled.listUpdateJobs();
      expect.unreachable("listUpdateJobs should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(503);
      expect((error as ApiError).code).toBe("operations_disabled");
    }
  });
});
```

- [ ] **Step 9: 跑测试确认失败**

Run: `cd /home/ji/work/program/stock/web && npm test -- tests/api-client.spec.ts`
Expected: FAIL — `Cannot find module '../src/api/client'`。

- [ ] **Step 10: 实现 types.ts、client.ts、errors.ts**

创建 `web/src/api/types.ts`（若 Step 1 对账出字段差异，以对账结论修正后再写入）：

```ts
/** §8.2 验收摘要：四态 verdict 与"存在有效 accepted record"分开回显。 */
export type AcceptanceVerdict =
  | "ACCEPTED"
  | "REJECTED"
  | "PENDING_CONFIRMATION"
  | "UNVERIFIED";

export interface AcceptanceSummary {
  state: AcceptanceVerdict;
  has_valid_accepted_record: boolean;
  latest_verdict: string | null;
  record_count: number;
}

/** pin I1：只有 severity 计数，没有 passed/blocking_reasons/issue_count。 */
export interface QualitySummary {
  by_severity: Record<string, number>;
}

export interface DatasetSummary {
  dataset_version: string;
  is_current: boolean;
  created_at: string | null;
  table_count: number;
  quality: QualitySummary;
  acceptance: AcceptanceSummary;
}

export interface DatasetListResponse {
  /** 当前版本（完整哈希或 null）；顶栏读这个。 */
  current: string | null;
  datasets: DatasetSummary[];
}

/** pin I2：表清单是对象数组。 */
export interface TableMeta {
  name: string;
  row_count: number;
  schema_version: string;
}

export interface DatasetDetailResponse {
  /** 永远是完整 64 位哈希（请求 current 时为解析结果回显）。 */
  dataset_version: string;
  requested_version: string;
  manifest: Record<string, unknown>;
  tables: TableMeta[];
  quality: QualitySummary;
  acceptance: AcceptanceSummary;
}

/**
 * pin I2：覆盖段的形状（`data_model/fetch_coverage.py:87-96`）。它在完整的
 * `manifest.build_config.table_fetch_coverage` 里，**不在详情响应顶层**；
 * `reason` 仅在 `kind === "not_fetched"` 时出现（为 None 时该键整个省略）。
 */
export interface FetchCoverageSegmentRecord {
  table: string;
  kind: "fetched" | "carried" | "not_fetched";
  window_start: string;
  window_end: string;
  reason?: string;
}

export interface QualityIssueRecord {
  severity: string | null;
  code: string | null;
  table: string | null;
  symbol: string | null;
  trade_date: string | null;
  details: Record<string, unknown> | null;
}

/** pin I3：分页游标是 total，不是 next_offset。 */
export interface QualityListResponse {
  dataset_version: string;
  requested_version: string;
  offset: number;
  limit: number;
  total: number;
  issues: QualityIssueRecord[];
}

/** pin I4：回显字段名就是 `arguments`（§8.2 / OpenBB 先例），日期过滤是单值。 */
export interface TableArguments {
  requested_version: string;
  table: string;
  columns: string[];
  symbol: string | null;
  trade_date: string | null;
  offset: number;
  limit: number;
}

export interface TablePreviewResponse {
  dataset_version: string;
  table: string;
  arguments: TableArguments;
  columns: string[];
  rows: Record<string, string | number | null>[];
}

/** 前端发起的表预览参数（服务端回显字段 requested_version/table 由 client 补）。 */
export interface TablePreviewParams {
  columns: string[] | null;
  symbol: string | null;
  /** pin I4：单值日期过滤；没有区间。 */
  trade_date: string | null;
  offset: number;
  limit: number;
}

/** pin I5：列表项字段比 DatasetSummary 少。 */
export interface ExperimentSummary {
  experiment_id: string;
  status: string | null;
  dataset_version: string | null;
  universe_version: string | null;
  evaluation_reason: string | null;
}

export interface ExperimentsResponse {
  experiments: ExperimentSummary[];
}

export type UpdateJobStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED_BY_SHUTDOWN";

/** §9.1 允许参数：start/end/sources/disclosure-lookback-days；默认空 = 常规增量。 */
export interface UpdateJobRequest {
  start: string | null;
  end: string | null;
  sources: string[] | null;
  disclosure_lookback_days: number | null;
}

/** pin I10：列表项——只有这六个字段。 */
export interface UpdateJobSummary {
  job_id: string;
  status: UpdateJobStatus;
  created_at: string;
  updated_at: string;
  run_id: string | null;
  dataset_version: string | null;
}

/** pin I10：详情在摘要之上加进程/心跳/退出与已脱敏的日志**字符串**。 */
export interface UpdateJob extends UpdateJobSummary {
  pid: number | null;
  boot_id: string | null;
  heartbeat_at: string | null;
  exit_code: number | null;
  failure_reason: string | null;
  failure_detail: string | null;
  stdout_tail: string;
  stderr_tail: string;
}

/** pin I10：列表外层是对象。 */
export interface UpdateJobsResponse {
  jobs: UpdateJobSummary[];
}

/** pin I11：201 只回两个字段，详情另取。 */
export interface UpdateJobCreated {
  job_id: string;
  status: UpdateJobStatus;
}

/** pin I11：409 的 `error.job_id`。 */
export interface UpdateConflictBody {
  code: "update_already_running";
  job_id: string;
}

/** pin I7 / I8：健康探针与嵌套错误信封。 */
export interface HealthResponse {
  /** P3 冻结为 `Literal["ok"]`；不要放宽成 string。 */
  status: "ok";
  /** 16 位十六进制；故意不含绝对路径，前端只显示/比对，不反解。 */
  project_root_fingerprint: string;
}

export interface ErrorBody {
  code: string;
  message?: string;
  [key: string]: unknown;
}

export interface ErrorResponse {
  error: ErrorBody;
}
```

创建 `web/src/api/client.ts`：

```ts
import { inject, provide, type InjectionKey } from "vue";
import type {
  DatasetDetailResponse,
  DatasetListResponse,
  ExperimentsResponse,
  HealthResponse,
  QualityListResponse,
  TablePreviewParams,
  TablePreviewResponse,
  UpdateConflictBody,
  UpdateJob,
  UpdateJobCreated,
  UpdateJobRequest,
  UpdateJobsResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly safeMessage: string,
    readonly body: unknown = null,
  ) {
    super(`[${status}] ${code}: ${safeMessage}`);
    this.name = "ApiError";
  }
}

export interface ApiClient {
  health(): Promise<HealthResponse>;
  listDatasets(): Promise<DatasetListResponse>;
  getDataset(version: string): Promise<DatasetDetailResponse>;
  listQualityIssues(
    version: string,
    offset: number,
    limit: number,
  ): Promise<QualityListResponse>;
  previewTable(
    version: string,
    table: string,
    params: TablePreviewParams,
  ): Promise<TablePreviewResponse>;
  listExperiments(): Promise<ExperimentsResponse>;
  probeExperimentReport(experimentId: string): Promise<boolean>;
  listUpdateJobs(): Promise<UpdateJobsResponse>;
  getUpdateJob(jobId: string): Promise<UpdateJob>;
  startUpdateJob(request: UpdateJobRequest): Promise<UpdateJobCreated>;
}

export const apiClientKey: InjectionKey<ApiClient> = Symbol("stock-web-api-client");

export function provideApiClient(client: ApiClient) {
  provide(apiClientKey, client);
}

export function useApiClient(): ApiClient {
  const client = inject(apiClientKey);
  if (!client) {
    throw new Error("api client not provided");
  }
  return client;
}

/**
 * pin I8：**所有**非 2xx 的错误体是**嵌套**的 `{error: {code, message?, ...}}`，
 * 不是扁平的 `{code, message}`。信封缺失或不可解析时退到 `http_<status>`。
 */
interface ErrorEnvelope {
  error?: { code?: unknown; message?: unknown };
}

export function createApiClient(
  options: { baseUrl?: string; fetchImpl?: typeof fetch } = {},
): ApiClient {
  const baseUrl = options.baseUrl ?? "";
  const doFetch = options.fetchImpl ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init));

  async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await doFetch(`${baseUrl}${path}`, init);
    if (!response.ok) {
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      const envelope = (body ?? {}) as ErrorEnvelope;
      const detail = envelope.error;
      const code =
        detail && typeof detail.code === "string"
          ? detail.code
          : `http_${response.status}`;
      const message =
        detail && typeof detail.message === "string" ? detail.message : "";
      throw new ApiError(response.status, code, message, body);
    }
    return (await response.json()) as T;
  }

  function query(params: Record<string, string | number | null>): string {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== null && value !== "") {
        search.set(key, String(value));
      }
    }
    const text = search.toString();
    return text === "" ? "" : `?${text}`;
  }

  return {
    health() {
      return requestJson<HealthResponse>("/api/v1/health");
    },
    listDatasets() {
      return requestJson<DatasetListResponse>("/api/v1/datasets");
    },
    getDataset(version: string) {
      return requestJson<DatasetDetailResponse>(`/api/v1/datasets/${version}`);
    },
    listQualityIssues(version: string, offset: number, limit: number) {
      return requestJson<QualityListResponse>(
        `/api/v1/datasets/${version}/quality${query({ offset, limit })}`,
      );
    },
    previewTable(version: string, table: string, params: TablePreviewParams) {
      return requestJson<TablePreviewResponse>(
        `/api/v1/datasets/${version}/tables/${encodeURIComponent(table)}${query({
          columns: params.columns === null ? null : params.columns.join(","),
          symbol: params.symbol,
          // pin I4：日期过滤是单值 trade_date，没有 date_start/date_end 区间。
          trade_date: params.trade_date,
          offset: params.offset,
          limit: params.limit,
        })}`,
      );
    },
    listExperiments() {
      return requestJson<ExperimentsResponse>("/api/v1/experiments");
    },
    async probeExperimentReport(experimentId: string) {
      const response = await doFetch(
        `${baseUrl}/api/v1/experiments/${encodeURIComponent(experimentId)}/report`,
      );
      return response.ok;
    },
    async listUpdateJobs() {
      return requestJson<UpdateJobsResponse>("/api/v1/update-jobs");
    },
    getUpdateJob(jobId: string) {
      return requestJson<UpdateJob>(`/api/v1/update-jobs/${encodeURIComponent(jobId)}`);
    },
    startUpdateJob(request: UpdateJobRequest) {
      // pin I11：201 只回 `{job_id, status}`，完整 job 详情要另取。
      return requestJson<UpdateJobCreated>("/api/v1/update-jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      });
    },
  };
}

/** §10.3 409 终态：从冲突错误体提取运行中 job id（pin I11：`job_id` 嵌在 `error` 里）。 */
export function conflictJobId(error: unknown): string | null {
  if (error instanceof ApiError && error.status === 409) {
    const body = error.body as { error?: UpdateConflictBody } | null;
    const detail = body === null || typeof body !== "object" ? null : body.error;
    if (detail && typeof detail.job_id === "string") {
      return detail.job_id;
    }
  }
  return null;
}
```

创建 `web/src/api/errors.ts`：

```ts
import { ApiError } from "./client";

export interface DisplayError {
  code: string;
  message: string;
}

/** 展示纪律（§10.2）：只输出稳定 error code 与安全摘要；不渲染堆栈/路径/环境。 */
export function toDisplayError(error: unknown): DisplayError {
  if (error instanceof ApiError) {
    return { code: error.code, message: error.safeMessage };
  }
  return { code: "unknown_error", message: "请求失败，无更多信息" };
}
```

创建 `web/src/api/quality.ts`（质量摘要派生的唯一入口；severity 词汇来自 `src/stock_quant/data_quality/models.py` 的 `Severity`）：

```ts
import type { QualitySummary } from "./types";

/**
 * 阻断级 severity。P3 的 `QualitySummary` 只有 `by_severity: Record<string, number>`，
 * 没有 `passed`/`blocking_reasons`/`issue_count`——门禁是否通过读 `acceptance.state`，
 * 阻断项数与本页条数都从这张计数表派生（`data_quality/models.py` 的 Severity：
 * INFO/WARNING/ERROR/FATAL）。
 */
export const BLOCKING_SEVERITIES = ["ERROR", "FATAL"] as const;

export function isBlockingSeverity(severity: string | null): boolean {
  return severity !== null && (BLOCKING_SEVERITIES as readonly string[]).includes(severity);
}

/** 阻断级质量问题条数（不是"门禁通过的判定"——那读 acceptance.state）。 */
export function blockingIssueCount(quality: QualitySummary): number {
  return BLOCKING_SEVERITIES.reduce(
    (total, severity) => total + (quality.by_severity[severity] ?? 0),
    0,
  );
}

export function totalIssueCount(quality: QualitySummary): number {
  return Object.values(quality.by_severity).reduce((total, count) => total + count, 0);
}
```

- [ ] **Step 11: 跑测试确认通过**

Run: `cd /home/ji/work/program/stock/web && npm test -- tests/api-client.spec.ts && npm run typecheck`
Expected: PASS。

- [ ] **Step 12: 提交**

```bash
git add web/src/api web/tests/helpers.ts web/tests/api-client.spec.ts
git commit -m "feat(web): typed api client for v1 read and update-job endpoints

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

- [ ] **Step 13: 写失败测试——版本 pin store 与完整顶栏**

创建 `web/tests/version-store.spec.ts`：

```ts
import { describe, expect, it } from "vitest";
import {
  clearResolvedVersion,
  hasNewerCurrent,
  resolveAndPin,
  setCurrentVersion,
  versionPinState,
} from "../src/stores/version";
import { datasetDetailResponse, fakeClient, HASH_A, HASH_B } from "./helpers";

function resetStore() {
  versionPinState.resolvedVersion = null;
  versionPinState.currentVersion = null;
  versionPinState.projectFingerprint = null;
}

describe("版本 pin store", () => {
  it("resolveAndPin 以响应回显的完整哈希为准钉住（请求 current）", async () => {
    resetStore();
    const client = fakeClient({ getDataset: async () => datasetDetailResponse(HASH_A) });
    const detail = await resolveAndPin(client, "current");
    expect(detail.dataset_version).toBe(HASH_A);
    expect(versionPinState.resolvedVersion).toBe(HASH_A);
  });

  it("hasNewerCurrent：resolved 与 current 不同才提示", async () => {
    resetStore();
    const client = fakeClient({ getDataset: async () => datasetDetailResponse(HASH_A) });
    await resolveAndPin(client, HASH_A);
    expect(hasNewerCurrent.value).toBe(false);
    setCurrentVersion(HASH_B);
    expect(hasNewerCurrent.value).toBe(true);
    expect(versionPinState.resolvedVersion).toBe(HASH_A); // 不自动切换
    setCurrentVersion(HASH_A);
    expect(hasNewerCurrent.value).toBe(false);
    clearResolvedVersion();
    expect(hasNewerCurrent.value).toBe(false);
  });
});
```

创建 `web/tests/top-bar.spec.ts`：

```ts
import { describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import AppTopBar from "../src/components/AppTopBar.vue";
import { apiClientKey } from "../src/api/client";
import { createPortalRouter } from "../src/router";
import {
  datasetListResponse,
  fakeClient,
  healthResponse,
  HASH_A,
  HASH_B,
} from "./helpers";
import {
  resolveAndPin,
  setCurrentVersion,
  setProjectFingerprint,
  versionPinState,
} from "../src/stores/version";

function resetStore() {
  versionPinState.resolvedVersion = null;
  versionPinState.currentVersion = null;
  versionPinState.projectFingerprint = null;
}

function mountTopBar(client: ReturnType<typeof fakeClient>) {
  return mount(AppTopBar, {
    global: {
      plugins: [createPortalRouter()],
      provide: { [apiClientKey as symbol]: client },
    },
  });
}

describe("共享顶栏（§10.3 页面地图）", () => {
  it("展示 project-root 指纹；未解析版本显示真实状态'未解析'", async () => {
    resetStore();
    const client = fakeClient({
      health: async () => healthResponse(),
      listDatasets: async () => datasetListResponse(),
    });
    const wrapper = mountTopBar(client);
    await flushPromises();
    expect(wrapper.get('[data-testid="project-fingerprint"]').text()).toContain("0123456789abcdef");
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain("未解析");
    expect(wrapper.find('[data-testid="current-badge"]').exists()).toBe(false);
  });

  it("展示解析出的完整哈希（可复制）与 CURRENT 指针徽标", async () => {
    resetStore();
    const client = fakeClient({
      health: async () => healthResponse(),
      listDatasets: async () => datasetListResponse(HASH_A),
      getDataset: async () => (await import("./helpers")).datasetDetailResponse(HASH_A),
    });
    const wrapper = mountTopBar(client);
    await flushPromises();
    await resolveAndPin(client, "current");
    setCurrentVersion(HASH_A);
    await flushPromises();
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain(HASH_A);
    expect(wrapper.get('[data-testid="current-badge"]').attributes("title")).toBe(
      "CURRENT 是指针标记，不是可信等级",
    );
    const writeText = vi.fn(async () => undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    await wrapper.get('[data-testid="copy-version"]').trigger("click");
    expect(writeText).toHaveBeenCalledWith(HASH_A);
  });

  it("刷新 CURRENT 后若有新版本：只提示，不自动切换", async () => {
    resetStore();
    let current = HASH_A;
    const client = fakeClient({
      health: async () => healthResponse(),
      listDatasets: async () => datasetListResponse(current),
      getDataset: async () => (await import("./helpers")).datasetDetailResponse(HASH_A),
    });
    const wrapper = mountTopBar(client);
    await flushPromises();
    await resolveAndPin(client, "current");
    expect(wrapper.find('[data-testid="new-version-hint"]').exists()).toBe(false);
    current = HASH_B;
    await wrapper.get('[data-testid="refresh-current"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="new-version-hint"]').text()).toContain("有新版本");
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain(HASH_A);
  });

  it("health 失败时显示真实状态'获取失败'，不显示成功文案", async () => {
    resetStore();
    const client = fakeClient({
      health: async () => {
        throw new Error("down");
      },
      listDatasets: async () => datasetListResponse(),
    });
    const wrapper = mountTopBar(client);
    await flushPromises();
    expect(wrapper.get('[data-testid="project-fingerprint"]').text()).toContain("获取失败");
  });
});
```

- [ ] **Step 14: 跑测试确认失败**

Run: `cd /home/ji/work/program/stock/web && npm test -- tests/version-store.spec.ts tests/top-bar.spec.ts`
Expected: FAIL — `Cannot find module '../src/stores/version'`；top-bar 用例在 store 建好后继续失败于 `Unable to locate [data-testid="copy-version"]`（顶栏还是导航版）。

- [ ] **Step 15: 实现 store 与完整顶栏**

创建 `web/src/stores/version.ts`：

```ts
import { computed, reactive } from "vue";
import type { ApiClient } from "../api/client";
import type { DatasetDetailResponse } from "../api/types";

/** 全门户共享的版本 pin 状态（§4 第 4 条：每请求解析一次，回显完整哈希）。 */
export const versionPinState = reactive({
  resolvedVersion: null as string | null,
  currentVersion: null as string | null,
  projectFingerprint: null as string | null,
});

/** 是否出现比当前页面解析结果更新的 CURRENT 指针（只提示，不自动切换）。 */
export const hasNewerCurrent = computed(
  () =>
    versionPinState.resolvedVersion !== null &&
    versionPinState.currentVersion !== null &&
    versionPinState.resolvedVersion !== versionPinState.currentVersion,
);

export function setCurrentVersion(version: string | null) {
  versionPinState.currentVersion = version;
}

export function setProjectFingerprint(fingerprint: string | null) {
  versionPinState.projectFingerprint = fingerprint;
}

export function clearResolvedVersion() {
  versionPinState.resolvedVersion = null;
}

/** 以请求别名（current 或完整哈希）解析一次并钉住回显的完整哈希。 */
export async function resolveAndPin(
  client: ApiClient,
  requestedVersion: string,
): Promise<DatasetDetailResponse> {
  const detail = await client.getDataset(requestedVersion);
  versionPinState.resolvedVersion = detail.dataset_version;
  return detail;
}
```

`web/src/components/AppTopBar.vue` 整体替换为完整版：

```vue
<script setup lang="ts">
import { onMounted } from "vue";
import { useApiClient } from "../api/client";
import { NAV_ITEMS } from "../router";
import {
  hasNewerCurrent,
  setCurrentVersion,
  setProjectFingerprint,
  versionPinState,
} from "../stores/version";

const client = useApiClient();

async function refreshCurrent() {
  try {
    const list = await client.listDatasets();
    setCurrentVersion(list.current);
  } catch {
    setCurrentVersion(null);
  }
}

async function copyResolvedVersion() {
  const value = versionPinState.resolvedVersion;
  if (value !== null && navigator.clipboard) {
    await navigator.clipboard.writeText(value);
  }
}

onMounted(async () => {
  try {
    const health = await client.health();
    setProjectFingerprint(health.project_root_fingerprint);
  } catch {
    setProjectFingerprint(null);
  }
  await refreshCurrent();
});
</script>

<template>
  <header class="top-bar" data-testid="top-bar">
    <span class="brand">Stock Quant 数据门户</span>
    <nav class="nav" data-testid="main-nav">
      <RouterLink v-for="item in NAV_ITEMS" :key="item.path" :to="item.path">{{ item.label }}</RouterLink>
    </nav>
    <span class="fingerprint" data-testid="project-fingerprint">
      项目指纹：<code>{{ versionPinState.projectFingerprint ?? "获取失败" }}</code>
    </span>
    <span class="resolved" data-testid="resolved-version">
      已解析版本：
      <code v-if="versionPinState.resolvedVersion !== null">{{ versionPinState.resolvedVersion }}</code>
      <span v-else>未解析</span>
      <button
        type="button"
        class="copy"
        data-testid="copy-version"
        :disabled="versionPinState.resolvedVersion === null"
        @click="copyResolvedVersion"
      >
        复制
      </button>
      <span
        v-if="
          versionPinState.resolvedVersion !== null &&
          versionPinState.resolvedVersion === versionPinState.currentVersion
        "
        class="badge"
        title="CURRENT 是指针标记，不是可信等级"
        data-testid="current-badge"
      >
        CURRENT
      </span>
      <button type="button" data-testid="refresh-current" @click="refreshCurrent">
        刷新 CURRENT
      </button>
    </span>
  </header>
  <p v-if="hasNewerCurrent" class="hint" data-testid="new-version-hint">
    有新版本：<code>{{ versionPinState.currentVersion }}</code>
    ；当前页面保持 <code>{{ versionPinState.resolvedVersion }}</code>
    ，不自动切换（<RouterLink to="/versions">查看版本面板</RouterLink>）
  </p>
</template>
```

`web/src/App.vue` 整体替换为最终形态（可注入 client 便于测试）：

```vue
<script setup lang="ts">
import { createApiClient, provideApiClient, type ApiClient } from "./api/client";
import AppTopBar from "./components/AppTopBar.vue";

const props = defineProps<{ client?: ApiClient }>();
provideApiClient(props.client ?? createApiClient());
</script>

<template>
  <div class="app">
    <AppTopBar />
    <main class="page"><RouterView /></main>
  </div>
</template>
```

- [ ] **Step 16: 跑测试确认通过（全部 Task 1 套件）**

Run: `cd /home/ji/work/program/stock/web && npm test && npm run typecheck && npm run build`
Expected: 全部 PASS。

- [ ] **Step 17: 提交**

```bash
git add web/src/stores web/src/components/AppTopBar.vue web/src/App.vue web/tests/version-store.spec.ts web/tests/top-bar.spec.ts
git commit -m "feat(web): version pin store and shared top bar with current-pointer hint

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: 版本面板 + 版本详情页

**Files:**
- Modify: `web/src/pages/VersionsPage.vue`（替换桩）、`web/src/pages/VersionDetailPage.vue`（替换桩）
- Test: `web/tests/versions-page.spec.ts`、`web/tests/version-detail-page.spec.ts`

**Interfaces:**
- Consumes: Task 1 的 `ApiClient`、`useApiClient`、`toDisplayError`、`resolveAndPin`、`setCurrentVersion`、`versionPinState`。
- Produces: 版本面板（`/versions`）与版本详情（`/versions/:version`，`version` 接受完整哈希或 `current`）；testid：`dataset-list`/`dataset-row`/`current-mark`/`accepted-record`/`latest-verdict`（面板），`resolved-echo`/`full-version`/`accepted-record-block`/`latest-verdict-block`/`latest-verdict-value`/`table-meta`/`quality-summary`/`blocking-reasons`/`quality-issues`（详情）——Task 5 E2E 依赖这些名字。

- [ ] **Step 1: 写失败测试**

创建 `web/tests/versions-page.spec.ts`：

```ts
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { ApiError } from "../src/api/client";
import VersionsPage from "../src/pages/VersionsPage.vue";
import { datasetListResponse, fakeClient, HASH_A, mountPage } from "./helpers";
import type { DatasetListResponse } from "../src/api/types";

describe("版本面板（§10.1 页面 1）", () => {
  it("列出全部版本：CURRENT 标记、发布时间证据、表计数、质量摘要真实展示", async () => {
    const client = fakeClient({ listDatasets: async () => datasetListResponse() });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    const rows = wrapper.findAll('[data-testid="dataset-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].find('[data-testid="current-mark"]').exists()).toBe(true);
    expect(rows[1].find('[data-testid="current-mark"]').exists()).toBe(false);
    expect(rows[0].text()).toContain("门禁通过");
    expect(rows[0].text()).toContain("质量问题 0 条");
    expect(rows[1].text()).toContain("门禁阻断（1 项）");
    expect(rows[0].text()).toContain("2026-10-01T08:00:00+08:00 job job-0001");
    expect(rows[0].text()).toContain("3");
  });

  it("验收四态按'存在有效 accepted record'与'最近 verdict'分栏显示（REJECTED 不遮蔽 accepted）", async () => {
    const list: DatasetListResponse = datasetListResponse();
    list.datasets[0].acceptance = {
      state: "ACCEPTED",
      has_valid_accepted_record: true,
      latest_verdict: "REJECTED",
      record_count: 2,
    };
    list.datasets[1].acceptance = {
      state: "PENDING_CONFIRMATION",
      has_valid_accepted_record: false,
      latest_verdict: "PENDING_CONFIRMATION",
      record_count: 0,
    };
    const client = fakeClient({ listDatasets: async () => list });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    const rows = wrapper.findAll('[data-testid="dataset-row"]');
    expect(rows[0].get('[data-testid="accepted-record"]').text()).toBe("是");
    expect(rows[0].get('[data-testid="latest-verdict"]').text()).toContain("REJECTED");
    expect(rows[1].get('[data-testid="accepted-record"]').text()).toBe("否");
    expect(rows[1].get('[data-testid="latest-verdict"]').text()).toContain("PENDING_CONFIRMATION");
  });

  it("UNVERIFIED（无 verdict）如实显示为 UNVERIFIED，不显示成功文案", async () => {
    const list = datasetListResponse();
    list.datasets[1].acceptance = {
      state: "UNVERIFIED",
      has_valid_accepted_record: false,
      latest_verdict: null,
      record_count: 0,
    };
    const client = fakeClient({ listDatasets: async () => list });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    expect(wrapper.findAll('[data-testid="dataset-row"]')[1].get('[data-testid="latest-verdict"]').text()).toContain("UNVERIFIED");
  });

  it("列表加载失败显示稳定错误码，不显示堆栈或路径", async () => {
    const client = fakeClient({
      listDatasets: async () => {
        throw new ApiError(503, "service_unavailable", "查询面不可用");
      },
    });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    const text = wrapper.get('[data-testid="error"]').text();
    expect(text).toContain("service_unavailable");
    expect(text).not.toMatch(/\/home\/|\.py|stack/i);
  });

  it("每行链接到以完整哈希为参数的版本详情", async () => {
    const client = fakeClient({ listDatasets: async () => datasetListResponse() });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    const href = wrapper.findAll('[data-testid="dataset-row"]')[0].find("a").attributes("href");
    expect(href).toBe(`#/versions/${HASH_A}`);
  });
});
```

创建 `web/tests/version-detail-page.spec.ts`：

```ts
import { describe, expect, it, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";
import VersionDetailPage from "../src/pages/VersionDetailPage.vue";
import {
  datasetDetailResponse,
  fakeClient,
  HASH_A,
  mountAt,
  qualityListResponse,
} from "./helpers";

describe("版本详情页（§10.3 结果展示流程）", () => {
  it("请求 current 在入口解析一次：页面展示回显的完整哈希", async () => {
    const getDataset = vi.fn(async () => datasetDetailResponse(HASH_A));
    const client = fakeClient({
      getDataset,
      listQualityIssues: async () => qualityListResponse(HASH_A),
    });
    const wrapper = await mountAt(VersionDetailPage, client, "/versions/current");
    await flushPromises();
    expect(getDataset).toHaveBeenCalledWith("current");
    expect(wrapper.get('[data-testid="full-version"]').text()).toBe(HASH_A);
    expect(client.listQualityIssues).toBeDefined();
  });

  it("验收状态：'有效 accepted record'与'最近 verdict'分开显示，REJECTED 不遮蔽 accepted", async () => {
    const detail = datasetDetailResponse(HASH_A);
    // 契约里只有 state/has_valid_accepted_record/latest_verdict/record_count，
    // 没有 latest_verdict_at——时间戳不在这条响应里。
    detail.acceptance = {
      state: "ACCEPTED",
      has_valid_accepted_record: true,
      latest_verdict: "REJECTED",
      record_count: 2,
    };
    const client = fakeClient({
      getDataset: async () => detail,
      listQualityIssues: async () => qualityListResponse(HASH_A),
    });
    const wrapper = await mountAt(VersionDetailPage, client, `/versions/${HASH_A}`);
    await flushPromises();
    expect(wrapper.get('[data-testid="accepted-record-block"]').text()).toContain("存在");
    expect(wrapper.get('[data-testid="latest-verdict-value"]').text()).toContain("REJECTED");
  });

  it("表计数、质量摘要与质量问题真实展示（含阻断形态）", async () => {
    const detail = datasetDetailResponse(HASH_A);
    // 门禁状态读 acceptance.state；阻断项数与质量条数从 by_severity 派生
    // （契约没有 passed/blocking_reasons/issue_count 三个字段）。
    detail.acceptance = { ...detail.acceptance, state: "REJECTED" };
    detail.quality = { by_severity: { FATAL: 1, WARNING: 1 } };
    const client = fakeClient({
      getDataset: async () => detail,
      listQualityIssues: async () => qualityListResponse(HASH_A),
    });
    const wrapper = await mountAt(VersionDetailPage, client, `/versions/${HASH_A}`);
    await flushPromises();
    expect(wrapper.get('[data-testid="quality-summary"]').text()).toContain("门禁阻断（1 项）");
    expect(wrapper.get('[data-testid="quality-summary"]').text()).toContain("质量问题 2 条");
    // 阻断明细只能从质量问题列表拿（按 severity 过滤），不从摘要拿。
    expect(wrapper.get('[data-testid="blocking-issues"]').text()).toContain("fetch_coverage_gap");
    expect(wrapper.findAll('[data-testid="quality-issues"] tbody tr')).toHaveLength(2);
    expect(wrapper.get('[data-testid="table-meta"]').text()).toContain("daily_bar");
    expect(wrapper.get('[data-testid="table-meta"]').text()).toContain("120");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/ji/work/program/stock/web && npm test -- tests/versions-page.spec.ts tests/version-detail-page.spec.ts`
Expected: FAIL — 两个页面仍是桩：`Unable to locate [data-testid="dataset-list"]` / `[data-testid="full-version"]`。

- [ ] **Step 3: 实现两个页面**

`web/src/pages/VersionsPage.vue` 整体替换：

```vue
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import { setCurrentVersion } from "../stores/version";
import { blockingIssueCount, totalIssueCount } from "../api/quality";
import type { DatasetSummary } from "../api/types";

const client = useApiClient();
const datasets = ref<DatasetSummary[]>([]);
const loaded = ref(false);
const error = ref<DisplayError | null>(null);

onMounted(async () => {
  try {
    const response = await client.listDatasets();
    datasets.value = response.datasets;
    setCurrentVersion(response.current);
    loaded.value = true;
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});
</script>

<template>
  <section>
    <h1>版本面板</h1>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <p v-else-if="!loaded" data-testid="loading">加载中</p>
    <table v-else data-testid="dataset-list">
      <thead>
        <tr>
          <th>dataset version</th>
          <th>CURRENT</th>
          <th>创建时间</th>
          <th>表计数</th>
          <th>质量摘要</th>
          <th>有效 accepted record</th>
          <th>最近 verdict</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in datasets" :key="item.dataset_version" data-testid="dataset-row">
          <td>
            <RouterLink :to="`/versions/${item.dataset_version}`">
              <code>{{ item.dataset_version }}</code>
            </RouterLink>
          </td>
          <td>
            <span
              v-if="item.is_current"
              class="badge"
              title="CURRENT 是指针标记，不是可信等级"
              data-testid="current-mark"
            >
              CURRENT
            </span>
            <span v-else>—</span>
          </td>
          <td>{{ item.created_at ?? "—" }}</td>
          <td>{{ item.table_count }}</td>
          <td data-testid="quality-summary">
            <span v-if="item.acceptance.state === 'ACCEPTED'">门禁通过</span>
            <span v-else class="warn">门禁阻断（{{ blockingIssueCount(item.quality) }} 项）</span>
            <span>；质量问题 {{ totalIssueCount(item.quality) }} 条</span>
          </td>
          <td data-testid="accepted-record">
            {{ item.acceptance.has_valid_accepted_record ? "是" : "否" }}
          </td>
          <td data-testid="latest-verdict">
            {{ item.acceptance.latest_verdict ?? item.acceptance.state }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
```

`web/src/pages/VersionDetailPage.vue` 整体替换：

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import {
  blockingIssueCount,
  totalIssueCount,
  isBlockingSeverity,
} from "../api/quality";
import { resolveAndPin, versionPinState } from "../stores/version";
import type { DatasetDetailResponse, QualityIssueRecord } from "../api/types";

const route = useRoute();
const client = useApiClient();
const requestedVersion = String(route.params.version ?? "current");
const detail = ref<DatasetDetailResponse | null>(null);
const issues = ref<QualityIssueRecord[]>([]);
const error = ref<DisplayError | null>(null);

// 门禁状态读 acceptance.state；两项计数从 quality.by_severity 派生——契约里
// 没有 passed/blocking_reasons/issue_count 可用。
const blockingCount = computed(() =>
  detail.value === null ? 0 : blockingIssueCount(detail.value.quality),
);
const totalCount = computed(() =>
  detail.value === null ? 0 : totalIssueCount(detail.value.quality),
);
// 阻断明细只能从质量问题列表按 severity 过滤（本页只取前 100 条）。
const blockingIssues = computed(() =>
  issues.value.filter((issue) => isBlockingSeverity(issue.severity)),
);

onMounted(async () => {
  try {
    detail.value = await resolveAndPin(client, requestedVersion);
    const quality = await client.listQualityIssues(detail.value.dataset_version, 0, 100);
    issues.value = quality.issues;
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});
</script>

<template>
  <section>
    <h1>版本详情</h1>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <template v-else-if="detail !== null">
      <p data-testid="resolved-echo">
        已解析版本（请求 <code>{{ requestedVersion }}</code>）：
        <code data-testid="full-version">{{ detail.dataset_version }}</code>
        <span
          v-if="versionPinState.currentVersion === detail.dataset_version"
          class="badge"
          title="CURRENT 是指针标记，不是可信等级"
        >
          CURRENT
        </span>
      </p>
      <!-- 详情响应里没有 created_at，发布时间证据只在列表页（DatasetSummary.created_at）。 -->
      <h2>表</h2>
      <table data-testid="table-meta">
        <thead>
          <tr><th>表</th><th>行数</th><th>schema 版本</th></tr>
        </thead>
        <tbody>
          <!-- detail.tables 是 TableMeta 对象数组，不是映射。 -->
          <tr v-for="meta in detail.tables" :key="meta.name">
            <td>{{ meta.name }}</td>
            <td>{{ meta.row_count }}</td>
            <td>{{ meta.schema_version }}</td>
          </tr>
        </tbody>
      </table>
      <h2>质量摘要</h2>
      <p data-testid="quality-summary">
        <span v-if="detail.acceptance.state === 'ACCEPTED'">门禁通过</span>
        <span v-else class="warn">门禁阻断（{{ blockingCount }} 项）</span>
        ；质量问题 {{ totalCount }} 条
      </p>
      <!-- 阻断明细来自质量问题列表（按 severity 过滤），摘要里没有 blocking_reasons。 -->
      <ul v-if="blockingIssues.length > 0" data-testid="blocking-issues">
        <li v-for="issue in blockingIssues" :key="`${issue.code}-${issue.table}-${issue.trade_date}`">
          {{ issue.severity }} {{ issue.code }}
        </li>
      </ul>
      <h2>验收状态（分开显示）</h2>
      <div class="acceptance-columns">
        <div data-testid="accepted-record-block">
          <h3>有效 accepted record</h3>
          <p>{{ detail.acceptance.has_valid_accepted_record ? "存在" : "不存在" }}</p>
        </div>
        <div data-testid="latest-verdict-block">
          <h3>最近 verdict</h3>
          <p data-testid="latest-verdict-value">
            {{ detail.acceptance.latest_verdict }}
            <span v-if="detail.acceptance.latest_verdict_at !== null">
              （{{ detail.acceptance.latest_verdict_at }}）
            </span>
          </p>
        </div>
      </div>
      <h2>质量问题（前 100 条）</h2>
      <table data-testid="quality-issues">
        <thead>
          <tr><th>code</th><th>severity</th><th>表</th><th>日期</th><th>details</th></tr>
        </thead>
        <tbody>
          <!-- 契约没有 summary 字段（pin I3）；details 是安全的结构化载荷。 -->
          <tr v-for="(issue, index) in issues" :key="index">
            <td>{{ issue.code }}</td>
            <td>{{ issue.severity }}</td>
            <td>{{ issue.table ?? "—" }}</td>
            <td>{{ issue.trade_date ?? "—" }}</td>
            <td>{{ issue.details === null ? "—" : JSON.stringify(issue.details) }}</td>
          </tr>
        </tbody>
      </table>
    </template>
    <p v-else data-testid="loading">加载中</p>
  </section>
</template>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/ji/work/program/stock/web && npm test && npm run typecheck`
Expected: PASS（含 Task 1 全部既有用例——顶栏用例不受影响，未新增页面断言）。

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/VersionsPage.vue web/src/pages/VersionDetailPage.vue web/tests/versions-page.spec.ts web/tests/version-detail-page.spec.ts
git commit -m "feat(web): dataset versions panel and detail with split acceptance display

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 数据预览页 + 质量/覆盖证据页

**Files:**
- Modify: `web/src/pages/DataPreviewPage.vue`（替换桩）、`web/src/pages/CoverageEvidencePage.vue`（替换桩）
- Test: `web/tests/data-preview-page.spec.ts`、`web/tests/coverage-evidence-page.spec.ts`

**Interfaces:**
- Consumes: Task 1 的 client/store/`resolveAndPin`/`versionPinState`/`hasNewerCurrent`（顶栏提示）、Task 2 的 detail 响应形态。
- Produces: 预览页 testid `resolved-version`/`preview-controls`/`version-select`/`table-select`/`filter-trade-date`/`filter-symbol`/`apply-filters`/`prev-page`/`next-page`/`pager`/`preview-table`/`error`；证据页 testid `coverage-segments`/`untrusted-<table>`/`untrusted-empty-<table>`/`attested-boundary-note`/`issue-list`——Task 5 E2E 依赖。

- [ ] **Step 1: 写失败测试**

创建 `web/tests/data-preview-page.spec.ts`：

```ts
import { describe, expect, it, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";
import DataPreviewPage from "../src/pages/DataPreviewPage.vue";
import {
  datasetDetailResponse,
  datasetListResponse,
  fakeClient,
  HASH_A,
  mountPage,
  previewResponse,
} from "./helpers";
import type { TablePreviewParams } from "../src/api/types";

describe("数据预览页（§10.1 页面 2 / §10.2 分页纪律）", () => {
  it("默认解析 current 并钉住完整哈希；翻页携带同一 resolved version 与递增 offset", async () => {
    const previewTable = vi.fn(
      async (version: string, _table: string, params: TablePreviewParams) =>
        previewResponse(version, params.offset),
    );
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable,
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    expect(previewTable.mock.calls[0]![0]).toBe(HASH_A);
    await wrapper.get('[data-testid="next-page"]').trigger("click");
    await flushPromises();
    expect(previewTable.mock.calls[1]![0]).toBe(HASH_A);
    expect(previewTable.mock.calls[1]![2].offset).toBe(100);
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain(HASH_A);
  });

  it("CURRENT 变化只提示不自动切换：翻页仍用旧 resolved 哈希", async () => {
    let current = HASH_A;
    const previewTable = vi.fn(
      async (version: string, _table: string, params: TablePreviewParams) =>
        previewResponse(version, params.offset),
    );
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(current),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable,
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    current = "b".repeat(64);
    await wrapper.get('[data-testid="next-page"]').trigger("click");
    await flushPromises();
    expect(previewTable.mock.calls.at(-1)![0]).toBe(HASH_A);
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain(HASH_A);
  });

  it("列筛选与单日/symbol 过滤进入请求参数（pin I4：只有单值 trade_date）", async () => {
    const previewTable = vi.fn(
      async (version: string, _table: string, params: TablePreviewParams) =>
        previewResponse(version, params.offset),
    );
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable,
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    await wrapper.get('[data-testid="filter-trade-date"]').setValue("2026-09-30");
    await wrapper.get('[data-testid="filter-symbol"]').setValue("000001.SZ");
    const checkbox = wrapper
      .findAll('fieldset input[type="checkbox"]')
      .find((input) => input.attributes("value") === "close");
    await checkbox!.setValue(true);
    await wrapper.get('[data-testid="apply-filters"]').trigger("submit");
    await flushPromises();
    const params = previewTable.mock.calls.at(-1)![2];
    expect(params.columns).toEqual(["close"]);
    expect(params.trade_date).toBe("2026-09-30");
    expect(params.symbol).toBe("000001.SZ");
    expect(params.offset).toBe(0);
  });

  it("第一页禁用上一页；返回行数少于 limit 时禁用下一页（响应无 total，只能按行数判）", async () => {
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable: async (version, _table, params) => previewResponse(version, params.offset, 40),
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    expect(wrapper.get('[data-testid="prev-page"]').attributes("disabled")).toBeDefined();
    // 契约的表预览响应没有 total 字段——"有没有下一页"只能靠 `rows.length < limit`。
    expect(wrapper.get('[data-testid="next-page"]').attributes("disabled")).toBeDefined();
  });

  it("响应回显的 dataset_version 与请求不一致：显示稳定码 version_echo_mismatch 并清空数据", async () => {
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable: async () => previewResponse("c".repeat(64)),
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    expect(wrapper.get('[data-testid="error"]').text()).toContain("version_echo_mismatch");
    expect(wrapper.find('[data-testid="preview-table"]').exists()).toBe(false);
  });
});
```

创建 `web/tests/coverage-evidence-page.spec.ts`：

```ts
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import CoverageEvidencePage from "../src/pages/CoverageEvidencePage.vue";
import {
  coveragePreviewResponse,
  datasetDetailResponse,
  datasetListResponse,
  fakeClient,
  HASH_A,
  mountPage,
  previewResponse,
  qualityListResponse,
  verifiedOnlyCoveragePreview,
} from "./helpers";

function evidenceClient() {
  return fakeClient({
    listDatasets: async () => datasetListResponse(),
    getDataset: async () => datasetDetailResponse(HASH_A),
    listQualityIssues: async () => qualityListResponse(HASH_A),
    previewTable: async (version: string, table: string) => {
      if (table === "basic_factor_coverage") return coveragePreviewResponse(version);
      if (table === "daily_bar_coverage") return verifiedOnlyCoveragePreview(version);
      return previewResponse(version);
    },
  });
}

describe("质量/覆盖证据页（§10.3 证据展示）", () => {
  it("展示覆盖段：kind、窗口与 not_fetched 尾段理由（source_unavailable 可见）", async () => {
    const wrapper = mountPage(CoverageEvidencePage, evidenceClient());
    await flushPromises();
    const segments = wrapper.get('[data-testid="coverage-segments"]').text();
    expect(segments).toContain("basic_factor");
    expect(segments).toContain("carried");
    expect(segments).toContain("not_fetched");
    expect(segments).toContain("source_unavailable");
    expect(segments).toContain("2026-09-30");
  });

  it("UNTRUSTED 行按 status == 'UNTRUSTED' 判定展示；无 UNTRUSTED 的表如实显示", async () => {
    const wrapper = mountPage(CoverageEvidencePage, evidenceClient());
    await flushPromises();
    const untrusted = wrapper.get('[data-testid="untrusted-basic_factor_coverage"]').text();
    expect(untrusted).toContain("UNTRUSTED");
    expect(untrusted).toContain("600000.SH");
    expect(untrusted).not.toContain("VERIFIED");
    expect(wrapper.get('[data-testid="untrusted-empty-daily_bar_coverage"]').text()).toContain(
      "无 UNTRUSTED 行",
    );
  });

  it("质量问题列表展示（coverage gap 码可见）", async () => {
    const wrapper = mountPage(CoverageEvidencePage, evidenceClient());
    await flushPromises();
    expect(wrapper.get('[data-testid="issue-list"]').text()).toContain("fetch_coverage_gap");
  });

  it("attested-boundary 近似与滞后上界可见（§7.1）", async () => {
    const wrapper = mountPage(CoverageEvidencePage, evidenceClient());
    await flushPromises();
    const note = wrapper.get('[data-testid="attested-boundary-note"]').text();
    expect(note).toContain("attested-boundary");
    expect(note).toContain("滞后上界");
    expect(note).toContain("不是官方公告日");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/ji/work/program/stock/web && npm test -- tests/data-preview-page.spec.ts tests/coverage-evidence-page.spec.ts`
Expected: FAIL — 两个页面仍是桩：`Unable to locate [data-testid="preview-table"]` / `[data-testid="coverage-segments"]`。

- [ ] **Step 3: 实现两个页面**

`web/src/pages/DataPreviewPage.vue` 整体替换：

```vue
<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import { resolveAndPin, versionPinState } from "../stores/version";
import type { DatasetSummary, TablePreviewResponse } from "../api/types";

const client = useApiClient();
const LIMIT = 100;

const requestedVersion = ref("current");
const datasetOptions = ref<DatasetSummary[]>([]);
const resolvedVersion = ref<string | null>(null);
const tableOptions = ref<string[]>([]);
const selectedTable = ref<string | null>(null);
const availableColumns = ref<string[]>([]);
const selectedColumns = reactive(new Set<string>());
const filters = reactive({ trade_date: "", symbol: "" });
const offset = ref(0);
const preview = ref<TablePreviewResponse | null>(null);
const error = ref<DisplayError | null>(null);
const loading = ref(false);

function nullIfEmpty(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

async function resolveVersion() {
  error.value = null;
  const detail = await resolveAndPin(client, requestedVersion.value);
  resolvedVersion.value = detail.dataset_version;
  // detail.tables 是 TableMeta 对象数组（pin I2），取 name 而不是对象键。
  tableOptions.value = detail.tables.map((meta) => meta.name).sort();
  selectedTable.value = tableOptions.value[0] ?? null;
  selectedColumns.clear();
  availableColumns.value = [];
  offset.value = 0;
  preview.value = null;
}

async function loadPreview() {
  if (resolvedVersion.value === null || selectedTable.value === null) return;
  loading.value = true;
  error.value = null;
  try {
    const response = await client.previewTable(resolvedVersion.value, selectedTable.value, {
      columns: selectedColumns.size === 0 ? null : [...selectedColumns].sort(),
      // pin I4：单值 trade_date，没有区间参数。
      trade_date: nullIfEmpty(filters.trade_date),
      symbol: nullIfEmpty(filters.symbol),
      offset: offset.value,
      limit: LIMIT,
    });
    if (response.dataset_version !== resolvedVersion.value) {
      error.value = {
        code: "version_echo_mismatch",
        message: "响应回显的 dataset_version 与请求不一致",
      };
      preview.value = null;
      return;
    }
    preview.value = response;
    availableColumns.value = response.columns;
  } catch (cause) {
    error.value = toDisplayError(cause);
    preview.value = null;
  } finally {
    loading.value = false;
  }
}

function toggleColumn(column: string) {
  if (selectedColumns.has(column)) {
    selectedColumns.delete(column);
  } else {
    selectedColumns.add(column);
  }
}

onMounted(async () => {
  try {
    datasetOptions.value = (await client.listDatasets()).datasets;
  } catch {
    // 版本选择器退化为仅 current；页面自身的加载错误仍会在下方如实显示
  }
  try {
    await resolveVersion();
    await loadPreview();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});

async function onVersionChange() {
  try {
    await resolveVersion();
    await loadPreview();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
}

async function onTableChange() {
  offset.value = 0;
  await loadPreview();
}

async function applyFilters() {
  offset.value = 0;
  await loadPreview();
}

async function previousPage() {
  offset.value = Math.max(0, offset.value - LIMIT);
  await loadPreview();
}

async function nextPage() {
  offset.value = offset.value + LIMIT;
  await loadPreview();
}
</script>

<template>
  <section>
    <h1>数据预览</h1>
    <p v-if="resolvedVersion !== null" data-testid="resolved-echo">
      已解析版本：<code data-testid="resolved-version">{{ resolvedVersion }}</code>
      <span
        v-if="resolvedVersion === versionPinState.currentVersion"
        class="badge"
        title="CURRENT 是指针标记，不是可信等级"
      >
        CURRENT
      </span>
    </p>
    <p v-else-if="error === null" data-testid="loading">加载中</p>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>

    <form v-if="resolvedVersion !== null" data-testid="preview-controls" @submit.prevent="applyFilters">
      <label>
        版本
        <select v-model="requestedVersion" data-testid="version-select" @change="onVersionChange">
          <option value="current">current（入口别名）</option>
          <option
            v-for="option in datasetOptions"
            :key="option.dataset_version"
            :value="option.dataset_version"
          >
            {{ option.dataset_version }}
          </option>
        </select>
      </label>
      <label>
        表
        <select v-model="selectedTable" data-testid="table-select" @change="onTableChange">
          <option v-for="table in tableOptions" :key="table" :value="table">{{ table }}</option>
        </select>
      </label>
      <!-- pin I4：服务端只有单值 trade_date，所以这里只提供一个日期输入。 -->
      <label>交易日 <input type="date" v-model="filters.trade_date" data-testid="filter-trade-date" /></label>
      <label>symbol <input type="text" v-model="filters.symbol" data-testid="filter-symbol" /></label>
      <button type="submit" data-testid="apply-filters">查询</button>
    </form>

    <fieldset v-if="availableColumns.length > 0">
      <legend>列筛选（不选 = 全部白名单列）</legend>
      <label v-for="column in availableColumns" :key="column">
        <input
          type="checkbox"
          :value="column"
          :checked="selectedColumns.has(column)"
          @change="toggleColumn(column)"
        />
        {{ column }}
      </label>
    </fieldset>

    <p v-if="preview !== null" data-testid="pager">
      行 {{ offset }}–{{ offset + preview.rows.length }}（limit {{ LIMIT }}）
      <button type="button" data-testid="prev-page" :disabled="offset === 0" @click="previousPage">
        上一页
      </button>
      <!-- 表预览响应没有 total（pin I4）；"还有下一页"只能按 rows.length 判。 -->
      <button
        type="button"
        data-testid="next-page"
        :disabled="preview.rows.length < LIMIT"
        @click="nextPage"
      >
        下一页
      </button>
      <span v-if="loading">加载中…</span>
    </p>

    <table v-if="preview !== null" data-testid="preview-table">
      <thead>
        <tr><th v-for="column in preview.columns" :key="column">{{ column }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in preview.rows" :key="index">
          <td v-for="column in preview.columns" :key="column">{{ row[column] }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
```

（`applyFilters` 已在上面的 script 里定义为"先重置 offset 再查询"，form 的 `@submit.prevent` 直接绑它。）

`web/src/pages/CoverageEvidencePage.vue` 整体替换：

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import { resolveAndPin, versionPinState } from "../stores/version";
import type {
  DatasetDetailResponse,
  DatasetSummary,
  FetchCoverageSegmentRecord,
  QualityIssueRecord,
  TablePreviewResponse,
} from "../api/types";

const client = useApiClient();

const requestedVersion = ref("current");
const datasetOptions = ref<DatasetSummary[]>([]);
const detail = ref<DatasetDetailResponse | null>(null);
const issues = ref<QualityIssueRecord[]>([]);
const untrustedRows = ref<Record<string, Record<string, string | number | null>[]>>({});
const error = ref<DisplayError | null>(null);

/** §7.1：滞后上界必须成文并在 UI 可见。 */
const ATTESTED_BOUNDARY_NOTE =
  "attested-boundary 近似：announcement_date = 证实该边界的快照日（与 raw_effective_from 同日），"
  + "不是官方公告日；快照节奏为月度时，月中调整可能被记到快照边界，滞后上界约一个快照周期。"
  + "对生效日精度敏感的研究必须显式接受该近似。";

interface SegmentRow {
  table: string;
  segment: FetchCoverageSegmentRecord;
}

const segments = computed<SegmentRow[]>(() => {
  if (detail.value === null) return [];
  const rows: SegmentRow[] = [];
  for (const [table, tableSegments] of Object.entries(coverageByTable.value)) {
    for (const segment of tableSegments) {
      rows.push({ table, segment });
    }
  }
  return rows;
});

/** pin I2：覆盖段在 manifest.build_config 里，不在响应顶层。 */
const coverageByTable = computed<Record<string, FetchCoverageSegmentRecord[]>>(() => {
  const manifest = detail.value?.manifest as
    | { build_config?: { table_fetch_coverage?: Record<string, FetchCoverageSegmentRecord[]> } }
    | undefined;
  return manifest?.build_config?.table_fetch_coverage ?? {};
});

const coverageTables = computed<string[]>(() => {
  if (detail.value === null) return [];
  // detail.tables 是 TableMeta 对象数组（pin I2）。
  return detail.value.tables
    .map((meta) => meta.name)
    .filter((name) => name.endsWith("_coverage"))
    .sort();
});

async function loadEvidence() {
  error.value = null;
  const resolved = await resolveAndPin(client, requestedVersion.value);
  detail.value = resolved;
  issues.value = (await client.listQualityIssues(resolved.dataset_version, 0, 100)).issues;
  const collected: Record<string, Record<string, string | number | null>[]> = {};
  for (const table of coverageTables.value) {
    const preview: TablePreviewResponse = await client.previewTable(
      resolved.dataset_version,
      table,
      { columns: null, symbol: null, trade_date: null, offset: 0, limit: 500 },
    );
    if (preview.dataset_version !== resolved.dataset_version) {
      throw new Error("version_echo_mismatch");
    }
    collected[table] = preview.rows.filter((row) => row.status === "UNTRUSTED");
  }
  untrustedRows.value = collected;
}

onMounted(async () => {
  try {
    datasetOptions.value = (await client.listDatasets()).datasets;
  } catch {
    // 选择器退化为仅 current；错误在下方如实显示
  }
  try {
    await loadEvidence();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});

async function onVersionChange() {
  try {
    await loadEvidence();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
}
</script>

<template>
  <section>
    <h1>质量/覆盖证据</h1>
    <p v-if="detail !== null" data-testid="resolved-echo">
      已解析版本：<code data-testid="resolved-version">{{ detail.dataset_version }}</code>
      <span
        v-if="detail.dataset_version === versionPinState.currentVersion"
        class="badge"
        title="CURRENT 是指针标记，不是可信等级"
      >
        CURRENT
      </span>
    </p>
    <p v-else-if="error === null" data-testid="loading">加载中</p>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>

    <form v-if="detail !== null" @submit.prevent="onVersionChange">
      <label>
        版本
        <select v-model="requestedVersion" data-testid="version-select" @change="onVersionChange">
          <option value="current">current（入口别名）</option>
          <option
            v-for="option in datasetOptions"
            :key="option.dataset_version"
            :value="option.dataset_version"
          >
            {{ option.dataset_version }}
          </option>
        </select>
      </label>
    </form>

    <h2>覆盖段（table_fetch_coverage）</h2>
    <table v-if="detail !== null" data-testid="coverage-segments">
      <thead>
        <tr><th>表</th><th>窗口</th><th>kind</th><th>理由</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in segments" :key="`${row.table}-${row.segment.window_start}`">
          <td>{{ row.table }}</td>
          <td>{{ row.segment.window_start }} → {{ row.segment.window_end }}</td>
          <td>{{ row.segment.kind }}</td>
          <td>
            <span v-if="row.segment.reason !== null" class="warn">{{ row.segment.reason }}</span>
            <span v-else>—</span>
          </td>
        </tr>
      </tbody>
    </table>

    <h2>UNTRUSTED 行（按 status == "UNTRUSTED" 判定）</h2>
    <div
      v-for="table in coverageTables"
      :key="table"
      :data-testid="`untrusted-${table}`"
    >
      <h3>{{ table }}</h3>
      <table v-if="(untrustedRows[table] ?? []).length > 0">
        <thead>
          <tr><th>trade_date</th><th>symbol</th><th>status</th><th>reason</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in untrustedRows[table]" :key="index">
            <td>{{ row.trade_date }}</td>
            <td>{{ row.symbol }}</td>
            <td>{{ row.status }}</td>
            <td>{{ row.reason }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else :data-testid="`untrusted-empty-${table}`">无 UNTRUSTED 行</p>
    </div>

    <h2>质量问题</h2>
    <table v-if="detail !== null" data-testid="issue-list">
      <thead>
        <tr><th>code</th><th>severity</th><th>表</th><th>日期</th><th>details</th></tr>
      </thead>
      <tbody>
        <!-- 契约没有 summary 字段（pin I3）。 -->
        <tr v-for="(issue, index) in issues" :key="index">
          <td>{{ issue.code }}</td>
          <td>{{ issue.severity }}</td>
          <td>{{ issue.table ?? "—" }}</td>
          <td>{{ issue.trade_date ?? "—" }}</td>
          <td>{{ issue.details === null ? "—" : JSON.stringify(issue.details) }}</td>
        </tr>
      </tbody>
    </table>

    <h2>attested-boundary 近似标注</h2>
    <blockquote data-testid="attested-boundary-note">{{ ATTESTED_BOUNDARY_NOTE }}</blockquote>
  </section>
</template>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/ji/work/program/stock/web && npm test && npm run typecheck`
Expected: PASS。若"列筛选与日期/symbol 过滤"用例失败于 `previewTable.mock.calls.at(-1)![2].offset` 不为 0，确认 `apply-filters` 的 submit 走的是重置 offset 的 `applyFilters()`（本步给出的最终代码已按此接线）。

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/DataPreviewPage.vue web/src/pages/CoverageEvidencePage.vue web/tests/data-preview-page.spec.ts web/tests/coverage-evidence-page.spec.ts
git commit -m "feat(web): version-pinned table preview and coverage evidence pages

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 更新任务页 + 报告页

**Files:**
- Modify: `web/src/pages/UpdateJobsPage.vue`（替换桩）、`web/src/pages/ReportsPage.vue`（替换桩）
- Test: `web/tests/update-jobs-page.spec.ts`、`web/tests/reports-page.spec.ts`

**Interfaces:**
- Consumes: Task 1 的 `ApiClient`/`ApiError`/`conflictJobId`/`toDisplayError`；§9.1 允许参数（start/end/sources/disclosure-lookback-days）。
- Produces: 更新页 testid `probing`/`operations-disabled`/`equivalent-cli`/`update-form`/`submit-update`/`submit-error`/`conflict`/`conflict-job-link`/`job-detail`/`job-status`/`job-succeeded`/`job-run-id`/`job-dataset-version`/`job-version-link`/`job-failed`/`job-failure-reason`/`job-stdout`/`job-stderr`/`job-cancelled`/`job-list`；报告页 testid `experiment-list`/`experiment-row`/`report-link`/`report-missing`/`not-in-web-note`——Task 5 E2E 依赖。

- [ ] **Step 1: 写失败测试**

创建 `web/tests/update-jobs-page.spec.ts`（fake timers 注意事项：`@vue/test-utils` 的 `flushPromises` 走宏任务，与 fake timers 冲突；本文件统一用 `settle()` 推进微任务+定时器）：

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import { mount } from "@vue/test-utils";
import { ApiError, apiClientKey } from "../src/api/client";
import { createPortalRouter } from "../src/router";
import UpdateJobsPage from "../src/pages/UpdateJobsPage.vue";
import { fakeClient, HASH_B, updateJob, updateJobSummary } from "./helpers";
import type { ApiClient } from "../src/api/client";
import type { UpdateJobRequest } from "../src/api/types";

function mountJobsPage(client: ApiClient) {
  return mount(UpdateJobsPage, {
    global: {
      plugins: [createPortalRouter()],
      provide: { [apiClientKey as symbol]: client },
    },
  });
}

async function settle(milliseconds: number) {
  await vi.advanceTimersByTimeAsync(milliseconds);
  await nextTick();
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("更新任务页（§10.3 数据更新流程 / §10.4 四终态）", () => {
  it("操作面关闭（503）：页面只读、说明原因并给出等价 CLI 命令", async () => {
    const client = fakeClient({
      listUpdateJobs: async () => {
        // pin I9：禁用是 503 operations_disabled，不是 404——路由始终注册着。
        throw new ApiError(503, "operations_disabled", "");
      },
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    expect(wrapper.get('[data-testid="operations-disabled"]').text()).toContain("操作面未启用");
    expect(wrapper.get('[data-testid="equivalent-cli"]').text()).toContain(
      "python -m stock_quant operations update --root",
    );
    expect(wrapper.find('[data-testid="update-form"]').exists()).toBe(false);
  });

  it("提交允许参数（默认空 = 常规增量）并轮询到 SUCCEEDED：显示 run id 与完整 dataset version 并链接版本详情", async () => {
    const queue = [
      updateJob({ status: "RUNNING" }),
      updateJob({ status: "SUCCEEDED", run_id: "run-77", dataset_version: HASH_B }),
    ];
    const startUpdateJob = vi.fn(async (request: UpdateJobRequest) => {
      expect(request).toEqual({
        start: null,
        end: null,
        sources: null,
        disclosure_lookback_days: null,
      });
      // pin I11：201 只回 {job_id, status}。
      return { job_id: "job-0001", status: "QUEUED" as const };
    });
    const getUpdateJob = vi.fn(async () => queue.shift() ?? queueFallback());
    function queueFallback() {
      return updateJob({ status: "SUCCEEDED", run_id: "run-77", dataset_version: HASH_B });
    }
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [] }),
      startUpdateJob,
      getUpdateJob,
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(1500);
    await settle(1500);
    expect(wrapper.get('[data-testid="job-succeeded"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="job-run-id"]').text()).toBe("run-77");
    expect(wrapper.get('[data-testid="job-dataset-version"]').text()).toBe(HASH_B);
    expect(wrapper.get('[data-testid="job-version-link"]').attributes("href")).toBe(
      `#/versions/${HASH_B}`,
    );
    expect(startUpdateJob).toHaveBeenCalledTimes(1);
  });

  it("FAILED 终态：显示稳定失败原因、安全摘要与脱敏日志；不自动重试", async () => {
    const queue = [
      updateJob({ status: "RUNNING" }),
      updateJob({
        status: "FAILED",
        // pin I10：失败原因叫 failure_reason（不是 error_code）；
        // 日志是已脱敏的字符串（不是 logs_tail: string[]）。
        failure_reason: "missing_registered_table",
        failure_detail: "缺表 daily_bar",
        stdout_tail: "2026-10-01T08:00:01+08:00 update started\n2026-10-01T08:04:59+08:00 gate blocked",
        stderr_tail: "",
      }),
    ];
    const startUpdateJob = vi.fn(async () => ({ job_id: "job-0001", status: "QUEUED" as const }));
    const getUpdateJob = vi.fn(async () =>
      queue.shift() ?? updateJob({ status: "FAILED", failure_reason: "missing_registered_table" }),
    );
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [] }),
      startUpdateJob,
      getUpdateJob,
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(1500);
    await settle(1500);
    expect(wrapper.get('[data-testid="job-failed"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="job-failure-reason"]').text()).toBe("missing_registered_table");
    expect(wrapper.get('[data-testid="job-failed"]').text()).toContain("缺表 daily_bar");
    expect(wrapper.get('[data-testid="job-stdout"]').text()).toContain("gate blocked");
    expect(wrapper.get('[data-testid="job-failed"]').text()).toContain("不自动重试");
    await settle(1500);
    await settle(1500);
    expect(startUpdateJob).toHaveBeenCalledTimes(1); // 不自动重试：无新 POST
  });

  it("409 终态：显示'已有任务运行中'并链接到该 job", async () => {
    const startUpdateJob = vi.fn(async () => {
      // pin I11：job_id 嵌在 error 里，不是顶层字段。
      throw new ApiError(409, "update_already_running", "", {
        error: { code: "update_already_running", job_id: "job-0001" },
      });
    });
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [updateJobSummary({ job_id: "job-0001", status: "RUNNING" })] }),
      startUpdateJob,
      getUpdateJob: async () => updateJob({ job_id: "job-0001", status: "RUNNING" }),
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(0);
    expect(wrapper.get('[data-testid="conflict"]').text()).toContain("已有任务运行中");
    expect(wrapper.get('[data-testid="conflict-job-link"]').text()).toBe("job-0001");
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("job-0001");
  });

  it("CANCELLED_BY_SHUTDOWN 终态可渲染", async () => {
    const queue = [
      updateJob({ status: "RUNNING" }),
      updateJob({ status: "CANCELLED_BY_SHUTDOWN" }),
    ];
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [] }),
      startUpdateJob: async () => ({ job_id: "job-0001", status: "QUEUED" as const }),
      getUpdateJob: async () => queue.shift() ?? updateJob({ status: "CANCELLED_BY_SHUTDOWN" }),
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(1500);
    await settle(1500);
    expect(wrapper.get('[data-testid="job-cancelled"]').exists()).toBe(true);
  });

  it("lookback 非法输入被前端拒绝并显示稳定码，不发起请求", async () => {
    const startUpdateJob = vi.fn(async () => ({ job_id: "job-0001", status: "QUEUED" as const }));
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [] }),
      startUpdateJob,
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('input[name="lookback"]').setValue("-3");
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(0);
    expect(wrapper.get('[data-testid="submit-error"]').text()).toContain("invalid_lookback_days");
    expect(startUpdateJob).not.toHaveBeenCalled();
  });
});
```

创建 `web/tests/reports-page.spec.ts`：

```ts
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { ApiError } from "../src/api/client";
import ReportsPage from "../src/pages/ReportsPage.vue";
import { experimentsResponse, fakeClient, HASH_A, mountPage } from "./helpers";

describe("报告页（§10.1 页面 4：链接既有静态报告，不在线重算）", () => {
  it("列出实验与其 dataset version；报告存在给链接、缺失显示'报告不存在'", async () => {
    const client = fakeClient({
      listExperiments: async () => ({
        // pin I5：五个字段一个不少。
        experiments: [
          { experiment_id: "exp-2026q3", status: "SUCCEEDED", dataset_version: HASH_A, universe_version: "tw", evaluation_reason: null },
          { experiment_id: "exp-2026q2", status: null, dataset_version: HASH_A, universe_version: null, evaluation_reason: null },
        ],
      }),
      probeExperimentReport: async (experimentId: string) => experimentId === "exp-2026q3",
    });
    const wrapper = mountPage(ReportsPage, client);
    await flushPromises();
    const rows = wrapper.findAll('[data-testid="experiment-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].get('[data-testid="report-link"]').attributes("href")).toBe(
      "/api/v1/experiments/exp-2026q3/report",
    );
    expect(rows[1].get('[data-testid="report-missing"]').text()).toContain("报告不存在");
    expect(rows[0].text()).toContain(HASH_A);
  });

  it("'不在 Web 里的动作'说明可见（指向 RUNBOOK 等价命令，无按钮）", async () => {
    const client = fakeClient({
      listExperiments: async () => experimentsResponse(),
      probeExperimentReport: async () => true,
    });
    const wrapper = mountPage(ReportsPage, client);
    await flushPromises();
    const note = wrapper.get('[data-testid="not-in-web-note"]').text();
    expect(note).toContain("acceptance confirm");
    expect(note).toContain("research run");
    expect(note).toContain("RUNBOOK");
  });

  it("列表失败显示稳定错误码", async () => {
    const client = fakeClient({
      listExperiments: async () => {
        throw new ApiError(503, "service_unavailable", "");
      },
    });
    const wrapper = mountPage(ReportsPage, client);
    await flushPromises();
    expect(wrapper.get('[data-testid="error"]').text()).toContain("service_unavailable");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /home/ji/work/program/stock/web && npm test -- tests/update-jobs-page.spec.ts tests/reports-page.spec.ts`
Expected: FAIL — 页面仍是桩：`Unable to locate [data-testid="operations-disabled"]` / `[data-testid="experiment-list"]`。

- [ ] **Step 3: 实现两个页面**

`web/src/pages/UpdateJobsPage.vue` 整体替换：

```vue
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { ApiError, conflictJobId, useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { UpdateJob, UpdateJobRequest } from "../api/types";

const client = useApiClient();

type OperationsMode = "probing" | "enabled" | "disabled";
const mode = ref<OperationsMode>("probing");
const probeError = ref<DisplayError | null>(null);

const jobs = ref<UpdateJob[]>([]);
const selectedJob = ref<UpdateJob | null>(null);
const conflictJobIdValue = ref<string | null>(null);
const submitError = ref<DisplayError | null>(null);
const submitting = ref(false);

const form = ref({ start: "", end: "", sources: "", lookback: "" });

const POLL_INTERVAL_MS = 1500;
const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELLED_BY_SHUTDOWN"]);
let pollTimer: ReturnType<typeof setInterval> | null = null;

/** §10.3：操作面禁用时给出等价 CLI 命令（operations update 是 Web 的等价入口，§9.1）。 */
const EQUIVALENT_CLI =
  "python -m stock_quant operations update --root <project-root> " +
  "[--start YYYY-MM-DD] [--end YYYY-MM-DD] [--sources s1,s2] [--disclosure-lookback-days N]";

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollJob(jobId: string) {
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const job = await client.getUpdateJob(jobId);
      selectedJob.value = job;
      if (TERMINAL_STATUSES.has(job.status)) {
        stopPolling();
      }
    } catch (cause) {
      stopPolling();
      submitError.value = toDisplayError(cause);
    }
  }, POLL_INTERVAL_MS);
}

async function selectJob(jobId: string) {
  try {
    const job = await client.getUpdateJob(jobId);
    selectedJob.value = job;
    if (!TERMINAL_STATUSES.has(job.status)) {
      await pollJob(jobId);
    }
  } catch (cause) {
    submitError.value = toDisplayError(cause);
  }
}

function buildRequest(): UpdateJobRequest {
  const lookbackText = form.value.lookback.trim();
  const lookback = lookbackText === "" ? null : Number(lookbackText);
  if (lookback !== null && (!Number.isInteger(lookback) || lookback < 0)) {
    throw new Error("invalid_lookback_days");
  }
  const sources =
    form.value.sources.trim() === ""
      ? null
      : form.value.sources
          .split(",")
          .map((item) => item.trim())
          .filter((item) => item !== "");
  return {
    start: form.value.start === "" ? null : form.value.start,
    end: form.value.end === "" ? null : form.value.end,
    sources,
    disclosure_lookback_days: lookback,
  };
}

async function submit() {
  submitError.value = null;
  conflictJobIdValue.value = null;
  let request: UpdateJobRequest;
  try {
    request = buildRequest();
  } catch {
    submitError.value = {
      code: "invalid_lookback_days",
      message: "lookback 必须是非负整数（留空 = 常规增量）",
    };
    return;
  }
  submitting.value = true;
  try {
    const created = await client.startUpdateJob(request);
    // pin I11：201 只回 `{job_id, status}`——完整 job 必须另取一次，
    // 不能把响应体当成 UpdateJob 塞进列表。
    await selectJob(created.job_id);
    await refreshJobs();
  } catch (cause) {
    const runningJobId = conflictJobId(cause);
    if (runningJobId !== null) {
      conflictJobIdValue.value = runningJobId;
      await selectJob(runningJobId);
    } else {
      submitError.value = toDisplayError(cause);
    }
  } finally {
    submitting.value = false;
  }
}

async function refreshJobs() {
  // pin I10：列表响应的外层是对象 `{jobs: [...]}`，不是裸数组。
  jobs.value = (await client.listUpdateJobs()).jobs;
}

onMounted(async () => {
  try {
    await refreshJobs();
    mode.value = "enabled";
  } catch (cause) {
    // pin I9：禁用是 503 operations_disabled（路由始终注册着，不是 404）。
    if (cause instanceof ApiError && cause.status === 503) {
      mode.value = "disabled";
    } else {
      mode.value = "enabled";
      probeError.value = toDisplayError(cause);
    }
  }
});
onUnmounted(stopPolling);
</script>

<template>
  <section>
    <h1>更新任务</h1>
    <p v-if="mode === 'probing'" data-testid="probing">探测操作面状态中</p>

    <div v-else-if="mode === 'disabled'" data-testid="operations-disabled">
      <h2>操作面未启用</h2>
      <p>
        操作 API 默认禁用（独立 router/process；启用后仍只绑定环回地址）。
        本页面只读，不提供提交入口。
      </p>
      <p>等价 CLI 命令（默认空参数 = 常规增量）：</p>
      <pre><code data-testid="equivalent-cli">{{ EQUIVALENT_CLI }}</code></pre>
    </div>

    <template v-else>
      <p v-if="probeError !== null" class="error" data-testid="probe-error">
        错误 {{ probeError.code }}：{{ probeError.message === "" ? "无安全摘要" : probeError.message }}
      </p>

      <form data-testid="update-form" @submit.prevent="submit">
        <label>start <input type="date" v-model="form.start" name="start" /></label>
        <label>end <input type="date" v-model="form.end" name="end" /></label>
        <label>
          sources
          <input type="text" v-model="form.sources" name="sources" placeholder="逗号分隔；留空 = 全部" />
        </label>
        <label>
          lookback
          <input type="number" v-model="form.lookback" name="lookback" min="0" step="1" placeholder="留空 = 常规增量" />
        </label>
        <button type="submit" data-testid="submit-update" :disabled="submitting">
          提交更新（默认空 = 常规增量）
        </button>
      </form>

      <p v-if="submitError !== null" class="error" data-testid="submit-error">
        错误 {{ submitError.code }}：{{ submitError.message === "" ? "无安全摘要" : submitError.message }}
      </p>

      <p v-if="conflictJobIdValue !== null" class="warn" data-testid="conflict">
        已有任务运行中（update_already_running）：
        <button type="button" data-testid="conflict-job-link" @click="selectJob(conflictJobIdValue)">
          {{ conflictJobIdValue }}
        </button>
      </p>

      <div v-if="selectedJob !== null" class="job-detail" data-testid="job-detail">
        <h2>任务 {{ selectedJob.job_id }}</h2>
        <p data-testid="job-status">状态：{{ selectedJob.status }}</p>

        <div v-if="selectedJob.status === 'SUCCEEDED'" data-testid="job-succeeded">
          <p>run id：<code data-testid="job-run-id">{{ selectedJob.run_id ?? "—" }}</code></p>
          <p>
            dataset version：<code data-testid="job-dataset-version">{{ selectedJob.dataset_version ?? "—" }}</code>
            <RouterLink
              v-if="selectedJob.dataset_version !== null"
              :to="`/versions/${selectedJob.dataset_version}`"
              data-testid="job-version-link"
            >
              版本详情
            </RouterLink>
          </p>
        </div>

        <div v-else-if="selectedJob.status === 'FAILED'" data-testid="job-failed">
          <p>稳定失败原因：<code data-testid="job-failure-reason">{{ selectedJob.failure_reason ?? "—" }}</code></p>
          <p>安全摘要：{{ selectedJob.failure_detail ?? "无安全摘要" }}</p>
          <p>不自动重试：如需再次更新，请提交新任务（将产生新 job id）。</p>
        </div>

        <div v-else-if="selectedJob.status === 'CANCELLED_BY_SHUTDOWN'" data-testid="job-cancelled">
          <p>任务被停机取消；日志保留可查。</p>
        </div>

        <h3>日志尾部（服务端已脱敏的字符串）</h3>
        <pre data-testid="job-stdout"><code>{{ selectedJob.stdout_tail }}</code></pre>
        <pre v-if="selectedJob.stderr_tail !== ''" data-testid="job-stderr"><code>{{ selectedJob.stderr_tail }}</code></pre>
      </div>

      <h2>持久化任务</h2>
      <table data-testid="job-list">
        <thead>
          <tr><th>job id</th><th>状态</th><th>创建</th><th>更新</th></tr>
        </thead>
        <tbody>
          <tr v-for="job in jobs" :key="job.job_id">
            <td><button type="button" @click="selectJob(job.job_id)">{{ job.job_id }}</button></td>
            <td>{{ job.status }}</td>
            <td>{{ job.created_at }}</td>
            <td>{{ job.updated_at }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>
```

`web/src/pages/ReportsPage.vue` 整体替换：

```vue
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { ExperimentSummary } from "../api/types";

const client = useApiClient();
const experiments = ref<ExperimentSummary[]>([]);
const reportAvailable = ref<Record<string, boolean>>({});
const loaded = ref(false);
const error = ref<DisplayError | null>(null);

/** §10.3"不在 Web 里的动作"：只显示下一步去哪，不提供按钮。 */
const NOT_IN_WEB_NOTE =
  "acceptance confirm/publish、research run 与失败证据清理不在 Web 中执行；"
  + "本页只链接既有静态报告，不在线重算。等价命令见 RUNBOOK。";

onMounted(async () => {
  try {
    experiments.value = (await client.listExperiments()).experiments;
    for (const experiment of experiments.value) {
      reportAvailable.value[experiment.experiment_id] =
        await client.probeExperimentReport(experiment.experiment_id);
    }
    loaded.value = true;
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});
</script>

<template>
  <section>
    <h1>报告</h1>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <p v-else-if="!loaded" data-testid="loading">加载中</p>
    <template v-else>
      <table data-testid="experiment-list">
        <thead>
          <tr><th>实验</th><th>dataset version</th><th>报告</th></tr>
        </thead>
        <tbody>
          <tr v-for="experiment in experiments" :key="experiment.experiment_id" data-testid="experiment-row">
            <td>{{ experiment.experiment_id }}</td>
            <td><code>{{ experiment.dataset_version }}</code></td>
            <td>
              <a
                v-if="reportAvailable[experiment.experiment_id]"
                :href="`/api/v1/experiments/${experiment.experiment_id}/report`"
                target="_blank"
                rel="noopener"
                data-testid="report-link"
              >
                打开静态报告
              </a>
              <span v-else data-testid="report-missing">
                报告不存在：尚无已生成的静态报告（报告由 CLI 生成，不在 Web 重算）
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <p data-testid="not-in-web-note">{{ NOT_IN_WEB_NOTE }}</p>
    </template>
  </section>
</template>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /home/ji/work/program/stock/web && npm test && npm run typecheck`
Expected: PASS（六个用例全绿；若 `lookback` 非法用例失败于未拦截，确认 `buildRequest()` 在 submit 前置抛错路径）。

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/UpdateJobsPage.vue web/src/pages/ReportsPage.vue web/tests/update-jobs-page.spec.ts web/tests/reports-page.spec.ts
git commit -m "feat(web): update-jobs page with four terminal states and static reports page

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: 浏览器端测试与 E2E（§10.4 逐条）

**Files:**
- Create: `web/playwright.config.ts`、`web/e2e/portal.spec.ts`

**Interfaces:**
- Consumes: Task 1–4 的全部页面、testid 与顶栏行为。
- Produces: §10.4 全部浏览器端断言（版本切换提示、UNVERIFIED 展示、分页保持版本、更新 409、更新失败日志、报告不存在、操作面关闭、四终态、验收四态分栏、E2E 主流程"不生成 acceptance PASS、不自动运行研究"）。

- [ ] **Step 1: 安装 Playwright 浏览器（联网一次，仅浏览器下载）**

Run: `cd /home/ji/work/program/stock/web && npx playwright install chromium`
Expected: chromium 下载安装成功。

- [ ] **Step 2: 创建 Playwright 配置**

创建 `web/playwright.config.ts`：

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: { baseURL: "http://127.0.0.1:5173" },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
```

- [ ] **Step 3: 写 E2E（全部走 mock 后端；先写测试再确认其当前失败或通过——E2E 对已实现页面是验收测试，预期直接通过；若任何一条红，说明对应组件缺陷，修复组件而非测试）**

创建 `web/e2e/portal.spec.ts`：

```ts
import { expect, test, type Page, type Route } from "@playwright/test";
import type { UpdateJobStatus } from "../src/api/types";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

interface MockJob {
  job_id: string;
  status: UpdateJobStatus;
  created_at: string;
  updated_at: string;
  pid: number | null;
  boot_id: string | null;
  heartbeat_at: string | null;
  exit_code: number | null;
  failure_reason: string | null;
  failure_detail: string | null;
  stdout_tail: string;
  stderr_tail: string;
  run_id: string | null;
  dataset_version: string | null;
}

interface MockState {
  current: string;
  operationsEnabled: boolean;
  jobs: MockJob[];
  postCount: number;
  conflictOnce: boolean;
  terminal: "SUCCEEDED" | "FAILED" | "CANCELLED_BY_SHUTDOWN";
  requests: { method: string; path: string }[];
  reports: Record<string, boolean>;
}

function state(overrides: Partial<MockState> = {}): MockState {
  return {
    current: HASH_A,
    operationsEnabled: true,
    jobs: [],
    postCount: 0,
    conflictOnce: false,
    terminal: "SUCCEEDED",
    requests: [],
    reports: { "exp-2026q3": true, "exp-2026q2": false },
    ...overrides,
  };
}

function seedJob(jobId: string, status: UpdateJobStatus): MockJob {
  return {
    job_id: jobId,
    status,
    created_at: "2026-10-01T07:00:00+08:00",
    updated_at: "2026-10-01T07:01:00+08:00",
    pid: 4321,
    boot_id: "2fdf3cf0-97bf-4896-bd6f-1a5b8841637d",
    heartbeat_at: "2026-10-01T07:01:00+08:00",
    exit_code: null,
    failure_reason: null,
    failure_detail: null,
    stdout_tail: "2026-10-01T07:00:01+08:00 update started",
    stderr_tail: "",
    run_id: null,
    dataset_version: null,
  };
}

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function detailBody() {
  // 与 P3 的 DatasetDetailResponse 逐字段同形：tables 是对象数组、覆盖段在
  // manifest.build_config 里、没有 is_current/published_at_evidence/quality_summary。
  return {
    dataset_version: HASH_A,
    requested_version: HASH_A,
    manifest: {
      build_config: {
        table_fetch_coverage: {
          daily_bar: [
            { table: "daily_bar", kind: "fetched", window_start: "2026-09-01", window_end: "2026-09-30" },
          ],
        },
      },
    },
    tables: [
      { name: "daily_bar", row_count: 120, schema_version: "1" },
      { name: "daily_bar_coverage", row_count: 2, schema_version: "1" },
    ],
    quality: { by_severity: { FATAL: 1 } },
    acceptance: {
      state: "ACCEPTED",
      has_valid_accepted_record: true,
      latest_verdict: "REJECTED",
      record_count: 2,
    },
  };
}

async function installMockBackend(page: Page, mock: MockState) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    mock.requests.push({ method: request.method(), path });

    if (path === "/api/v1/health") {
      return json(route, 200, { status: "ok", project_root_fingerprint: "0123456789abcdef" });
    }
    if (path === "/api/v1/datasets") {
      return json(route, 200, {
        current: mock.current,
        datasets: [HASH_A, HASH_B].map((version) => ({
          dataset_version: version,
          is_current: version === mock.current,
          created_at: "2026-10-01T08:00:00+08:00",
          table_count: 2,
          quality: { by_severity: version === HASH_A ? { FATAL: 1 } : {} },
          acceptance:
            version === HASH_A
              ? { state: "ACCEPTED", has_valid_accepted_record: true, latest_verdict: "ACCEPTED", record_count: 1 }
              : { state: "UNVERIFIED", has_valid_accepted_record: false, latest_verdict: null, record_count: 0 },
        })),
      });
    }
    if (path === "/api/v1/datasets/current" || path === `/api/v1/datasets/${HASH_A}`) {
      return json(route, 200, detailBody());
    }
    if (path === `/api/v1/datasets/${HASH_A}/quality`) {
      return json(route, 200, {
        dataset_version: HASH_A,
        requested_version: HASH_A,
        offset: 0,
        limit: 100,
        total: 1,
        issues: [
          {
            severity: "FATAL",
            code: "fetch_coverage_gap",
            table: "basic_factor",
            symbol: null,
            trade_date: "2026-09-30",
            details: { gap_start: "2026-09-30" },
          },
        ],
      });
    }
    if (path.startsWith(`/api/v1/datasets/${HASH_A}/tables/`)) {
      return json(route, 200, {
        dataset_version: HASH_A,
        table: "daily_bar",
        arguments: {
          requested_version: HASH_A,
          table: "daily_bar",
          columns: ["trade_date", "symbol", "close"],
          symbol: null,
          trade_date: null,
          offset: 0,
          limit: 100,
        },
        columns: ["trade_date", "symbol", "close"],
        rows: Array.from({ length: 100 }, (_, index) => ({
          trade_date: "2026-09-28",
          symbol: "000001.SZ",
          close: index,
        })),
      });
    }
    if (path === "/api/v1/experiments") {
      return json(route, 200, {
        experiments: [
          { experiment_id: "exp-2026q3", status: "SUCCEEDED", dataset_version: HASH_A, universe_version: "tw", evaluation_reason: null },
          { experiment_id: "exp-2026q2", status: null, dataset_version: HASH_A, universe_version: null, evaluation_reason: null },
        ],
      });
    }
    const report = /^\/api\/v1\/experiments\/([^/]+)\/report$/.exec(path);
    if (report) {
      if (mock.reports[report[1]!] === true) {
        return route.fulfill({
          status: 200,
          contentType: "text/html",
          body: "<html><body>static report</body></html>",
        });
      }
      return json(route, 404, { error: { code: "report_not_found", message: "该实验尚无已生成的静态报告" } });
    }
    if (path === "/api/v1/update-jobs" && request.method() === "GET") {
      if (!mock.operationsEnabled) {
        // pin I9：禁用是 503 operations_disabled，不是 404。
        return json(route, 503, { error: { code: "operations_disabled" } });
      }
      // pin I10：列表外层是对象。
      return json(route, 200, { jobs: mock.jobs.map(toSummary) });
    }
    if (path === "/api/v1/update-jobs" && request.method() === "POST") {
      mock.postCount += 1;
      if (mock.conflictOnce) {
        mock.conflictOnce = false;
        // pin I11：job_id 嵌在 error 里。
        return json(route, 409, {
          error: { code: "update_already_running", job_id: mock.jobs[0]!.job_id },
        });
      }
      const job = seedJob(`job-${mock.postCount}`, "QUEUED");
      mock.jobs.unshift(job);
      // pin I11：201 只回两个字段，详情要另取。
      return json(route, 201, { job_id: job.job_id, status: job.status });
    }
    const jobMatch = /^\/api\/v1\/update-jobs\/(.+)$/.exec(path);
    if (jobMatch) {
      const job = mock.jobs.find((item) => item.job_id === jobMatch[1]);
      if (job === undefined) {
        return json(route, 404, { error: { code: "job_not_found", job_id: jobMatch[1] } });
      }
      if (job.status === "QUEUED") {
        job.status = "RUNNING";
      } else if (job.status === "RUNNING") {
        job.status = mock.terminal;
        job.updated_at = "2026-10-01T08:05:00+08:00";
        job.stdout_tail = `${job.stdout_tail}\n2026-10-01T08:04:59+08:00 update finished`;
        if (mock.terminal === "SUCCEEDED") {
          job.run_id = "run-e2e";
          job.dataset_version = HASH_B;
          mock.current = HASH_B;
        } else if (mock.terminal === "FAILED") {
          job.failure_reason = "missing_registered_table";
          job.failure_detail = "缺表 daily_bar";
        }
      }
      return json(route, 200, job);
    }
    return json(route, 404, { error: { code: "route_not_found", path } });
  });
}

/** pin I10：列表只回摘要六字段。 */
function toSummary(job: MockJob) {
  return {
    job_id: job.job_id,
    status: job.status,
    created_at: job.created_at,
    updated_at: job.updated_at,
    run_id: job.run_id,
    dataset_version: job.dataset_version,
  };
}

test("§10.4 版本切换提示 + 分页保持版本", async ({ page }) => {
  const mock = state();
  await installMockBackend(page, mock);
  await page.goto("/#/preview");
  await expect(page.getByTestId("preview-table")).toBeVisible();
  const tableRequests = () =>
    mock.requests.filter((item) => item.path.startsWith(`/api/v1/datasets/${HASH_A}/tables/`));
  expect(tableRequests().length).toBeGreaterThan(0);

  await page.getByTestId("next-page").click();
  await expect(page.getByTestId("preview-table")).toBeVisible();
  expect(tableRequests().length).toBe(2);

  mock.current = HASH_B;
  await page.getByTestId("refresh-current").click();
  await expect(page.getByTestId("new-version-hint")).toBeVisible();
  await expect(page.getByTestId("resolved-version")).toContainText(HASH_A);
  expect(
    tableRequests().every((item) => item.path.startsWith(`/api/v1/datasets/${HASH_A}/tables/`)),
  ).toBe(true);
});

test("§10.4 UNVERIFIED 展示（版本面板）", async ({ page }) => {
  const mock = state();
  await installMockBackend(page, mock);
  await page.goto("/#/versions");
  const rows = page.getByTestId("dataset-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(1).getByTestId("latest-verdict")).toContainText("UNVERIFIED");
  await expect(rows.nth(1).getByTestId("accepted-record")).toHaveText("否");
  await expect(rows.nth(0).getByTestId("accepted-record")).toHaveText("是");
  await expect(rows.nth(0).getByTestId("current-mark")).toBeVisible();
});

test("§10.4 验收四态分栏显示（版本详情：有效 accepted record 与最近 verdict 分开）", async ({ page }) => {
  await installMockBackend(page, state());
  await page.goto("/#/versions/current");
  await expect(page.getByTestId("accepted-record-block")).toContainText("存在");
  await expect(page.getByTestId("latest-verdict-block")).toContainText("REJECTED");
  await expect(page.getByTestId("full-version")).toHaveText(HASH_A);
});

test("§10.4 更新 409：显示'已有任务运行中'并链接该 job", async ({ page }) => {
  const mock = state({ conflictOnce: true });
  mock.jobs.push(seedJob("job-0", "RUNNING"));
  await installMockBackend(page, mock);
  await page.goto("/#/jobs");
  await expect(page.getByTestId("update-form")).toBeVisible();
  await page.getByTestId("submit-update").click();
  await expect(page.getByTestId("conflict")).toBeVisible();
  await expect(page.getByTestId("conflict")).toContainText("job-0");
  await expect(page.getByTestId("job-detail")).toContainText("job-0");
});

test("§10.4 更新失败日志：稳定失败原因 + 脱敏日志尾部", async ({ page }) => {
  const mock = state({ terminal: "FAILED" });
  await installMockBackend(page, mock);
  await page.goto("/#/jobs");
  await page.getByTestId("submit-update").click();
  await expect(page.getByTestId("job-failed")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("job-failure-reason")).toHaveText("missing_registered_table");
  await expect(page.getByTestId("job-failed")).toContainText("缺表 daily_bar");
  await expect(page.getByTestId("job-stdout")).toContainText("update finished");
});

test("§10.4 操作面关闭：只读 + 等价 CLI 命令", async ({ page }) => {
  await installMockBackend(page, state({ operationsEnabled: false }));
  await page.goto("/#/jobs");
  await expect(page.getByTestId("operations-disabled")).toBeVisible();
  await expect(page.getByTestId("equivalent-cli")).toContainText(
    "python -m stock_quant operations update --root",
  );
  await expect(page.getByTestId("update-form")).toHaveCount(0);
});

test("§10.4 报告不存在：缺失态与既有链接并存", async ({ page }) => {
  await installMockBackend(page, state());
  await page.goto("/#/reports");
  const rows = page.getByTestId("experiment-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0).getByTestId("report-link")).toHaveAttribute(
    "href",
    "/api/v1/experiments/exp-2026q3/report",
  );
  await expect(rows.nth(1).getByTestId("report-missing")).toContainText("报告不存在");
});

test("§10.4 E2E 主流程：触发更新 → 看到新版本；成功终态显示完整 dataset version 并链接版本详情；不生成 acceptance PASS、不自动运行研究", async ({ page }) => {
  const mock = state({ terminal: "SUCCEEDED" });
  await installMockBackend(page, mock);
  await page.goto("/#/jobs");
  await page.getByTestId("submit-update").click();
  await expect(page.getByTestId("job-succeeded")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("job-run-id")).toHaveText("run-e2e");
  await expect(page.getByTestId("job-dataset-version")).toHaveText(HASH_B);
  await expect(page.getByTestId("job-version-link")).toHaveAttribute("href", `#/versions/${HASH_B}`);

  await page.getByRole("link", { name: "版本面板" }).click();
  await expect(page.getByTestId("dataset-list")).toBeVisible();
  const newRow = page.getByTestId("dataset-row").filter({ hasText: HASH_B });
  await expect(newRow.getByTestId("current-mark")).toBeVisible();

  // 不生成 acceptance PASS、不自动运行研究：除一次 update POST 外全部只读 GET；
  // 门户不发起任何 acceptance/research 请求（§14 第 6 条在交互层的表达）。
  expect(mock.postCount).toBe(1);
  expect(mock.requests.filter((item) => item.method !== "GET")).toEqual([
    { method: "POST", path: "/api/v1/update-jobs" },
  ]);
  expect(
    mock.requests.some(
      (item) => item.path.includes("acceptance") || item.path.includes("research"),
    ),
  ).toBe(false);
});
```

- [ ] **Step 4: 跑 E2E 确认通过**

Run: `cd /home/ji/work/program/stock/web && npm run e2e`
Expected: 8 条全 PASS。任何一条失败都指向对应组件缺陷——修组件（Task 1–4 的文件），不改断言（断言原文是 §10.4）。

- [ ] **Step 5: 跑全量组件测试与类型检查**

Run: `cd /home/ji/work/program/stock/web && npm test && npm run typecheck && npm run build`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add web/playwright.config.ts web/e2e/portal.spec.ts
git commit -m "test(web): browser e2e covering spec 10.4 assertions against mocked backend

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: 批次收口（§10.4 勾稽 + 全量回归 + 汇报）

**Files:** 无新改动（只读核对与全量套件）。

- [ ] **Step 1: 对照 spec §10.4 逐条勾稽**

| §10.4 条目 | 浏览器端断言（组件测试） | E2E |
| --- | --- | --- |
| 版本切换提示 | `top-bar.spec.ts`（有新版本只提示）+ `data-preview-page.spec.ts`（CURRENT 变化仍用旧 resolved） | `portal.spec.ts` §10.4 版本切换提示 + 分页保持版本 |
| UNVERIFIED 展示 | `versions-page.spec.ts` UNVERIFIED 用例 | `portal.spec.ts` UNVERIFIED 展示 |
| 分页保持版本 | `data-preview-page.spec.ts` 翻页携带同一 resolved version | 同"版本切换提示"一条 |
| 更新 409 | `update-jobs-page.spec.ts` 409 用例 | `portal.spec.ts` 更新 409 |
| 更新失败日志 | `update-jobs-page.spec.ts` FAILED 用例（failure_reason + 日志） | `portal.spec.ts` 更新失败日志 |
| 报告不存在 | `reports-page.spec.ts` 缺失态用例 | `portal.spec.ts` 报告不存在 |
| 操作面关闭 | `update-jobs-page.spec.ts` 503 只读 + CLI | `portal.spec.ts` 操作面关闭 |
| 四终态断言（成功显完整 dataset version 并链接版本详情；失败显稳定错误码） | `update-jobs-page.spec.ts` SUCCEEDED/FAILED/409/CANCELLED + 关闭 | 主流程（SUCCEEDED）、更新失败日志（FAILED）、409、关闭四条 |
| 验收四态"有效 accepted record 与最近 verdict 分开显示" | `version-detail-page.spec.ts` 分栏用例 + `versions-page.spec.ts` 分栏用例 | `portal.spec.ts` 验收四态分栏 |
| E2E 从触发更新到看到新版本，不生成 acceptance PASS、不自动运行研究 | —（E2E 专属） | `portal.spec.ts` E2E 主流程（POST 计数 + 无 acceptance/research 请求断言） |

- [ ] **Step 2: 全量套件**

Run: `cd /home/ji/work/program/stock/web && npm run typecheck && npm test && npm run e2e && npm run build`
Expected: 全部 PASS。

- [ ] **Step 3: 工作区核对**

Run: `cd /home/ji/work/program/stock && git status --porcelain | grep -v "^?? web/" | grep -v "web/"`
Expected: 只显示开工前已存在的在途 WIP 与未跟踪文档（Global Constraints 清单里的文件），没有任何 `web/` 之外文件被本计划改动；`web/` 下无 `node_modules/`、`dist/`、`test-results/`、`playwright-report/` 入暂存区（被 `.gitignore` 排除）。

- [ ] **Step 4: 向 owner 汇报批次完成**

汇报内容：
1. P5 landed 清单（六提交：脚手架 / 应用壳 / API client / 版本 pin 与顶栏 / 版本页 / 预览与证据页 / 更新与报告页 / E2E）。
2. **契约对账状态**（Task 1 Step 1 的结论）：P3/P4 未落地时按 pin I1–I12 开发（**但本计划的 pin 表已按 P3/P4 冻结的响应模型写成**，所谓"未落地"只影响 Task 1 Step 1 的对账对象，不影响 types.ts 内容）；已落地时列出逐字段差异与修正。G5 出口的真后端联调（真实查询面 + 操作面上四页可用）不在本计划内，需另行安排。
3. 总路线图 P5 任务卡（Task 30–33）与本计划 Task 1–5 的对应关系，及"浏览器测试自包含在 web/ 包内"的路线图偏差说明（见"开工前必须知道的实现形态"第 6 条）。

---

## Self-Review 记录

- **规格覆盖（§10.1–10.4 逐条勾稽）**：
  - §10.1 页面 1（版本面板：dataset version、CURRENT 标记、发布时间证据（契约里是 `DatasetSummary.created_at`）、表计数、质量计数与验收状态、有效 acceptance 与最近 verdict）→ Task 2；页面 2（数据预览：选明确版本和表、分页、列筛选、日期/symbol 过滤、顶栏持续展示完整/可复制版本哈希）→ Task 3（顶栏哈希与复制在 Task 1 Step 15）；页面 3（更新任务）→ Task 4；页面 4（报告链接）→ Task 4；"质量/覆盖证据"页（§10.3 页面地图五页导航之一）→ Task 3。版本详情页是 §10.3 结果展示流程对页面 1 的展开，非额外页面。
  - §10.2 展示纪律逐条 → Global Constraints（成文）+ 落点：不用"数据正常"（所有页面真实状态词，Task 2/4 错误用例断言不出现路径/堆栈）；CURRENT 是指针标记（徽标 title、fixture 注释）；翻页携带同一 resolved version（Task 3 用例 + Task 5 E2E）；CURRENT 变化只提示（顶栏 hint + Task 5 E2E）；稳定错误码与安全摘要（`toDisplayError` 唯一通道 + `version_echo_mismatch`）；不复制 Panda dist（全新手写源码，Global Constraints）。
  - §10.3 功能流流程：页面地图（顶栏三要素 + 固定导航）→ Task 1；数据更新流程四分支（SUCCEEDED/FAILED/409/禁用只读 + 等价 CLI）→ Task 4 + Task 5；结果展示流程（版本面板 → 详情四态分栏 → 钉版本预览 → 证据页 UNTRUSTED/coverage gap/attested-boundary 滞后上界 → 报告页不在线重算）→ Task 2/3/4；使用流程与"不在 Web 里的动作"（只显示下一步去哪，无按钮）→ Task 4 报告页 `not-in-web-note`。
  - §10.4 十项完成条件 → Task 6 Step 1 的勾稽表（每条有组件测试 + E2E 双落点，E2E 主流程含"不生成 acceptance PASS、不自动运行研究"的请求级断言）。
  - 消费契约：§8.2 七端点全部由 client 覆盖（health/datasets/dataset/quality/tables/experiments/report），`{version}` 接受完整哈希或 `current`、回显完整哈希、请求参数回显、limit 默认 100/最大 500（预览 100、证据页 coverage 预览 500）、acceptance 四态分栏——分别落在 Task 1 Step 10、Task 2、Task 3；§9.3 三端点（POST/GET list/GET detail）+ 不提供取消/重试（Task 4 只提交新 job）落在 Task 1/4。
  - §11 相关行：「服务读期间 CURRENT 改变」→ Task 3 钉版本；「两次更新并发→409 不排队」→ Task 4/5；「acceptance 未签→UNVERIFIED/PENDING」→ Task 2；「发布门禁拒绝→job FAILED、CURRENT 不变、质量原因可查」→ Task 4 FAILED 终态。
- **类型一致性**：`ApiClient` 十方法名在 client/types/helpers/页面间一致；`UpdateJob`/`UpdateJobRequest`/`UpdateJobStatus` 字段与 mock fixture（helpers `updateJob`、e2e `seedJob`）一致；`DatasetDetailResponse.dataset_version`（回显权威）被 `resolveAndPin`/预览回显校验/E2E `full-version` 断言一致引用；testid 名在页面模板与 spec/E2E 间逐字一致（Task Interfaces 块已列全）。
- **已知留白（成文，有归属）**：
  - P3/P4 的响应模型**已冻结**，`web/src/api/types.ts` 是按它们逐字段写成的 P5 侧 pin（I1–I12）；Task 1 Step 1 仍强制对账（以运行中的实际响应为准），端点表本身不改动（§8.2/§9.3 权威）。留白在于：`QualitySummary` 只有 `by_severity`，门禁结论改读 `acceptance.state`（落点 `web/src/api/quality.ts`）；表预览响应既无 `total` 也无日期区间，所以"是否有下一页"按 `rows.length < limit` 判；日期过滤已按 owner 裁定取 (a)——**只做单日 `trade_date`，区间浏览不做**（见 pin 表下方）。
  - 真实后端联调不在本计划（G5 出口核对的一部分，需 owner 另行安排）；本计划全部离线 mock，因此可在 P3/P4 之前执行。
  - 页面桩是 Task 1 的脚手架，Task 2–4 逐个以失败测试替换，最终交付不含桩（Task 6 Step 1 勾稽表可反查无 `page-pending` 残留；`npm run build` 通过且各页 spec 全绿即证明）。
  - `npm install` 与 `npx playwright install chromium` 需联网一次（依赖/浏览器下载），与"测试离线 mock"（不连真实后端、不触真实数据）不冲突。
  - mock 数据全部使用 `"a".repeat(64)` 类假哈希与假指纹；测试与 E2E 不含任何真实凭据、真实版本哈希或绝对路径（Task 2 错误用例显式断言无 `/home/` 路径）。
