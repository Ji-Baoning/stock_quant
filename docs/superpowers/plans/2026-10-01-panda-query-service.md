# Panda 嫁接 · P3 只读 FastAPI 查询面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地总规格 §8 的本地只读查询面——`src/stock_quant/service/` 只读 FastAPI 应用（health / datasets 列表·详情·quality / experiments 列表·report / 表预览），外加它的 ADR-021 正名、依赖边界（duckdb 入核心 + `service` extra）、契约与安全测试、并发读验证与事实文档三件套同步。

**Architecture:** 服务是已发布 artifacts 之上的纯只读视图：每个请求在入口解析一次版本（`current` 或完整 64 位哈希），独立打开/关闭一个 `DatasetReader` 只读 context，响应回显解析出的完整哈希与本次请求参数；不 import `DataPipeline`、不持有 publisher、全 GET、默认且仅环回绑定。表预览走"manifest 表 + schema 列白名单 + 参数绑定 + limit 上限 + 独立时间预算"的硬界，不提供任意 SQL / 任意文件路径 / 文件下载。acceptance 摘要四态（ACCEPTED/REJECTED/PENDING_CONFIRMATION/UNVERIFIED），"存在有效 accepted record"与"最近 verdict"分开显示。

**Tech Stack:** Python 3.12（`/home/ji/miniconda3/envs/sq312/bin/python`）、FastAPI + uvicorn（新 `service` optional extra，本计划 Task 2 安装）、DuckDB 1.5.5（只读连接，由 `DatasetReader` 提供）、pydantic v2、pytest（`fastapi.testclient.TestClient`，不起端口）。

**Spec:** [docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md](../specs/2026-09-29-panda-data-loop-grafting-design.md) §2.2/§2.3/§4/§5.2/§5.3/§5.4/§8 全部/§11 相关行。本计划是[总路线图](2026-10-01-panda-data-loop-grafting-implementation.md) P3 批次（Task 22–25）的阶段计划；只依赖 P0/G0 的 ADR-021（任务卡在本计划 Task 1，但**成文时点在 G0**），不依赖 P1/P2。

## Global Constraints

- 解释器 `/home/ji/miniconda3/envs/sq312/bin/python`（duckdb 1.5.5、httpx 0.28.1 已在环境；fastapi/uvicorn 由 Task 2 安装）；跑**点名测试文件**，不跑裸 `pytest`（integration 全量 ≈18.5 分钟）。
- **服务测试不真起端口**：一律 `fastapi.testclient.TestClient`；仓库中唯一的 `uvicorn.run` 调用在 `service/__main__.py` 的 `serve()`，测试用 monkeypatch 替换它，任何测试步骤都不得真监听端口。
- **无认证 / 无公网 / 无 TLS / 无多租户**（spec §2.3 非目标）；服务默认且只绑定 `127.0.0.1`（`localhost`/`::1` 可），配置为非环回地址必须启动失败（spec §8.1）。
- **服务不 import `DataPipeline`、不持有 publisher、全 GET**（spec §8.1）。由此产生一条硬设计裁定（见"开工前"第 4 条）：服务对 acceptance/experiment 的读取**直接读盘上 JSON**，不经 `research.acceptance` Python 包——该包 `__init__` 会传递 import `stock_quant.data_pipeline`。
- ADR 编号：`docs/adr/` 现存**两个 020**（`020-batched-validation-channel.md` 与 `020-suspension-proof-grid-is-the-fetch-window-and-its-seam.md`，编号冲突属实）——Task 1 Step 1 先把该冲突报告 owner；**021 空闲、022 已被 panda 覆盖证据计划占用**，本计划采用 **021**。若开工时 021 被并行占用，以 `docs/adr/DECISIONS_INDEX.md` 的下一空闲编号替代（spec §5.2），不改已有 ADR。
- 每个行为变更先写失败测试、观察其按预期理由失败，再做最小修复（tests.md）。**Task 5 的安全/契约电池与 Task 6 的并发验证是"钉住"性质**：若实现（Task 3/4）已封洞，它们可能首跑即绿——这是预期确认，不是失败；但任一红即视为实现缺口，当场修复，不得放宽断言。
- **安装 service extra 需从 PyPI 拉 wheel（联网）**：Task 2 动手前向 owner 报备（沿 2026-09-27 批量通道计划 Task 10 惯例）；离线失败即停并报告，不得改用来源不明的 wheel。
- 凭据零容忍：不进代码、配置、日志、fixture、报告、运维记录；服务响应不回显绝对路径（health 只给指纹，spec §8.2）。
- 保护在途 WIP：工作区已修改的 `src/stock_quant/cli.py`、`src/stock_quant/reporting/html.py`、`src/stock_quant/reporting/templates/experiment.html.j2`、`RUNBOOK.md`、两份 spec（`docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md`、`docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md`）与在途测试文件（开工时以 `git status` 为准）——不覆盖、不回退、不暂存、不重排；本计划只 `git add` 自己的文件。
- 提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；ADR ≤ 400 行；**事实层三件套（overview/module-map/data-flow）在本批次（服务代码首次合入）内同步更新**（spec §5.3，Task 7 执行；overview 的 DuckDB 依赖行因 Task 2 的事实变更随 Task 2 同步）。
- `RUNBOOK.md` 的服务启动/停止条目按路线图归 **P4 Task 29**（且 RUNBOOK.md 当前是在途 WIP，本计划不触碰）——这是成文留白，不是遗漏（见 Self-Review）。

## 开工前必须知道的实现形态

1. **`DatasetReader` 是查询面的唯一数据依赖**（`src/stock_quant/data_model/dataset.py`）：
   - `DatasetReader(project_root).open(version) -> DatasetContext`（`:174`）：version 必须是 `data/standardized/` 下的目录名（64 位哈希）；目录缺 manifest 时 `DatasetNotFoundError`；manifest 是**损坏 JSON 时 `open` 直接抛 `json.JSONDecodeError`**（`:187`）——服务的 fail-closed 语义从这里来。
   - `DatasetContext`（`:199`）是 context manager：`.tables`（tuple，manifest 表名）、`.manifest`（已解析 dict）、`.connection`（只读 DuckDB）、`.read(table)`/`.query(sql, params)`、`.close()`。DuckDB catalog 建在 OS 临时目录（`:365` `_catalog_path`），版本目录保持 Parquet-only。
   - `CURRENT` 是 `data/standardized/CURRENT` 文本文件，发布时原子替换（`:157`）。`DatasetReader` **没有** `current()` 方法（那是 `DatasetPublisher` 的，`:142`）——服务解析 `current` 就是读这个文件，不引入 publisher。
2. **安全引用规则已存在**：`dataset.py:373` `_sql_identifier`（双引号翻倍）。服务复用它，值一律走参数绑定（`?` 占位符）。
3. **acceptance 注册表的盘上形态**（`research/acceptance/registry.py`）：`data/acceptances/<dataset_version>/<acceptance_id>/acceptance.json`；`list()` 按 `(created_at, acceptance_id)` 升序（`:147`）；`select()` 的"有效 accepted"判定 = `decision == ACCEPTED` 且 `policy_version == "real-data-v1"` 且全部 automated/manual checks PASS（`:167-179`）——服务在原始 JSON 上镜像这个谓词。`PENDING_CONFIRMATION` 词汇来自 `ManualCheckStatus`（`acceptance/models.py:144`）；"已 prepare 未签署"的盘上痕迹是 `data/acceptance-worksheets/<version>/`（`acceptance/worksheet.py:87` `WORKSHEET_DIRNAME = "acceptance-worksheets"`）。
4. **import 陷阱（本计划最重要的形态事实）**：`stock_quant/research/acceptance/__init__.py` 顶层 import `checks`，而 `checks.py:83` `from stock_quant.data_pipeline import ...`——**import 该包的任何子模块都会把 `stock_quant.data_pipeline` 拉进 `sys.modules`**。因此服务不得 import `research.acceptance`（哪怕只为 pydantic 模型或 `POLICY_VERSION` 常量），改为：直读 `data/acceptances/**/acceptance.json`，policy 字面量 `"real-data-v1"` 以注释指回 `acceptance/models.py:53`。同理 experiments 直读 `data/experiments/<id>/experiment_manifest.json` 与 `report.html`（写入方 `research/runner.py:2077`；目录名 = `experiment_id` = 64 位哈希）。已验证干净的依赖：`stock_quant.data_model.dataset` 与 `stock_quant.project_root` 都不传递引入 data_pipeline。
5. **`quality_report.json` 形态**（`data_quality/models.py:136-151` `to_dict`）：`{"issues": [{severity, code, table, symbol, trade_date, details}], "by_severity": {...}, "by_code": {...}}`，issues 已按 (severity rank, code, table, symbol, trade_date) 排序。`within_tolerance` 是 WARNING 且**不在** `PUBLICATION_BLOCKING_CODES`（`data_quality/gates.py:42-64`）——fixture 用它造带内容且能通过发布门的质量报告。
6. **pyproject 现状**（`pyproject.toml:11-20`）：`duckdb` 不在 `dependencies`（只在 `environment.yml`），且**没有** `[project.optional-dependencies]` 段；spec §5.4 要求 duckdb 入核心（`DatasetReader` 正常路径 import 它）+ 新增 `service` extra（fastapi、uvicorn）。
7. **root 解析校验**（`project_root.py:13-17`）：root 必须存在且带 `configs/project.yml`、`configs/sources.yml`、`configs/costs.yml`；服务复用 `resolve_project_root`（ADR-005，无 fallback）。
8. **canonical schema 白名单**（`data_model/dataset.py`——**不是** `schemas.py`）：`STANDARDIZED_SCHEMAS` 定义在 `dataset.py:54` 覆盖 9 张表（`schemas.py` 只有各单表 schema，从这里 import 会 `ImportError`）；`daily_bar` 有 `symbol`/`trade_date` 列，`trading_calendar` 没有（它的日期列叫 `calendar_date`）——"过滤参数受白名单约束"的判定依据。
9. **测试目录现状**：`tests/{unit,integration,external,smoke,fixtures}`，**无 `tests/service/`**（本计划新建）；仓库惯例是同目录 `from conftest import X  # noqa: E402`（见 `tests/integration/test_cli.py:18`）。pytest 配置 `pythonpath=["src"]`、`addopts` 排除 external/smoke。
10. **治理测试要求**（`tests/unit/test_context_governance_docs.py` + `tools/check_context_governance.py`）：每个 ADR 带 frontmatter 字段 `status`/`date`/`decision`/`affects`、被 `DECISIONS_INDEX.md` 相对链接索引、单文件 ≤400 行；索引表列序 `| ADR | Status | Date | Affected paths | Keywords | Read when |`。
11. **环境事实**：sq312 无 fastapi/uvicorn（Task 2 装）；httpx 0.28.1 已在（TestClient 前置）；duckdb 1.5.5（`DuckDBPyConnection.interrupt()` 可用，时间预算靠它）。
12. **事实文档现状**（Task 7 的改写对象）：`docs/architecture/overview.md:24`"One local, single-process CLI — no server, no scheduler, no database"与 `:49` DuckDB 行"Not a dependency of `pyproject.toml`"；`module-map.md` 无 service 行；`data-flow.md` 无服务读路径。
13. **发布原子性**（Task 6 验证的既有性质）：版本目录整体 `os.replace`（`dataset.py:134`）+ `CURRENT` 原子替换（`:161`）；读者钉住已打开版本的绝对 Parquet 路径，永不再读 `CURRENT`（`dataset.py:11-15` 模块 docstring）。

## 文件结构

**新增**

| 文件 | 职责 |
| --- | --- |
| `docs/adr/021-resident-query-surface-and-scheduler.md` | ADR-021：常驻只读查询面与调度正名（Task 1） |
| `src/stock_quant/service/__init__.py` | 包出口：`create_app` |
| `src/stock_quant/service/errors.py` | 稳定错误词汇（`ServiceError` 族）与错误信封模型——独立成模块避免 app↔router 循环 import |
| `src/stock_quant/service/security.py` | 环回绑定校验（`validate_bind_host`）+ 复用既有标识符引用规则 |
| `src/stock_quant/service/datasets.py` | 版本一次性解析（`pinned_dataset` 依赖）、datasets 列表/详情/quality 端点、acceptance 四态摘要 |
| `src/stock_quant/service/experiments.py` | experiments 列表 + report 只返回既有 HTML |
| `src/stock_quant/service/tables.py` | 表预览端点：白名单、参数绑定、limit 上限、时间预算 |
| `src/stock_quant/service/app.py` | app 工厂、错误处理器、health |
| `src/stock_quant/service/__main__.py` | `python -m stock_quant.service` 入口：先校验环回再 uvicorn |
| `tests/service/conftest.py` | 离线 fixture：真实 `DatasetPublisher` 发布的 9 表项目 + 实验与 acceptance 记录构造器 |
| `tests/service/test_dependency_boundary.py` | pyproject 依赖边界（Task 2） |
| `tests/service/test_app_datasets.py` | 骨架行为：health/datasets/quality/acceptance 四态/experiments/版本回显（Task 3） |
| `tests/service/test_service_boundary.py` | GET-only、无 data_pipeline import、环回拒绝（Task 3） |
| `tests/service/test_tables.py` | 表预览：回显、过滤、limit、时间预算、每请求独立 context（Task 4） |
| `tests/service/test_contract_security.py` | OpenAPI 契约冻结 + 409 信封 + 安全电池（Task 5） |
| `tests/service/test_concurrency.py` | 多 reader + 一路发布：不见半发布状态（Task 6） |

**修改**

| 文件 | 改动 |
| --- | --- |
| `pyproject.toml` | `duckdb>=1` 入核心依赖；新增 `[project.optional-dependencies] service`（fastapi、uvicorn）（Task 2） |
| `docs/adr/DECISIONS_INDEX.md` | 只追加 021 一行（Task 1） |
| `src/stock_quant/service/app.py` | Task 4 挂入 tables router（两行） |
| `docs/architecture/overview.md` | Task 2 改 DuckDB 依赖行；Task 7 改写 Runtime shape（Task 7） |
| `docs/architecture/module-map.md` | service 包职责行 + 依赖方向 + Before-you-modify 行（Task 7） |
| `docs/architecture/data-flow.md` | 新增 §7 服务读路径；失败语义表加一行（Task 7） |

---

### Task 1: ADR-021（常驻只读查询面与调度正名）——**在 G0 执行，不在本批**

> **时点（owner 2026-10-01 裁定，见总路线图"G0 与批次顺序的张力"）**：spec §13 把 ADR-021 列进 G0 的退出条件，而 G3 的可开始条件又是 G0，若本任务在 P3 批内执行就构成循环。裁定取 (a)：**ADR-021 在 G0/P0 批内单独成文**（纯文档、无代码依赖），本任务卡即为该文档的任务文本。
>
> 对本批的含义：**Task 1 不再在 P3 开工时产出 ADR，而是引用一份已经存在的 ADR-021**——开工第一步改为核对 `docs/adr/021-*.md` 已成文、编号未冲突（若并行占用则按 Step 1 换号），并承接 ADR 正文里写明、但落地在 P3/P4 的义务（尤其"no server"事实层的同变更更新，见 Task 7）。下面的 Step 1–3 全部保留，作为 G0 期成文与提交时的执行步骤。

**Files:**
- Create: `docs/adr/021-resident-query-surface-and-scheduler.md`
- Modify: `docs/adr/DECISIONS_INDEX.md`（只追加一行）

**Interfaces:**
- Consumes: spec §5.2/§8.1/§11；ADR-001/002/005 的既有裁定。
- Produces: Task 3–7 全部边界行为的 ADR 依据；P4（操作面/调度器）与 P5（门户）复用本 ADR 的"不拥有写语义 + 环回 + 人工验收边界"；`update_already_running` 409 词汇的预告。

- [ ] **Step 1: 报告 ADR-020 编号冲突并确认 021 空闲**

Run: `ls /home/ji/work/program/stock/docs/adr/020-*.md /home/ji/work/program/stock/docs/adr/021-*.md 2>&1`
Expected: 两个 020 文件 + "无法访问 021"（021 空闲；022 已被占用）。把两点（020 冲突属实、021/022 占用状态）报告 owner；若 owner 另行指定编号，本任务与 Task 7 的所有 `021` 与文件名同步替换。

- [ ] **Step 2: 写 ADR 正文**（frontmatter 四字段是治理测试硬要求；≤400 行）

```markdown
---
status: accepted
date: 2026-10-01
decision: "A local, resident read-only query surface and a scheduler trigger shell are allowed as further entry points beside the CLI, under four hard boundaries: they own no data-write semantics (the single write chain stays DatasetPublisher.publish, reached only through the CLI), they bind loopback only and fail startup on any non-loopback configuration, every read request pins its dataset version exactly once at the entry and echoes the resolved full hash in every response, and the human acceptance boundary is not automatable — no service endpoint may write an acceptance verdict, publish a dataset, promote CURRENT_ACCEPTED or start a formal research run. Design lineage (cited as provenance only, no dependency introduced): Qlib PIT's value-plus-available-at dual timestamps, ArcticDB's as_of version reads, Datasette's read-only served databases with --sql-time-limit-ms, and OpenBB's arguments-echo response envelope."
affects:
  - src/stock_quant/service/**
  - docs/architecture/overview.md
  - docs/architecture/module-map.md
  - docs/architecture/data-flow.md
---

# ADR-021: 本地常驻只读查询面与调度触发器

日期：2026-10-01
状态：accepted
相关：spec 2026-09-29-panda-data-loop-grafting-design §2.3/§4/§5.2/§5.3/§8/§9/§11；
ADR-001（内容寻址发布）、ADR-002（real-data 验收）、ADR-005（显式 project root）

## 背景

`docs/architecture/overview.md` 至今把运行形态记为"只有 CLI——no server, no
scheduler, no database"。panda 嫁接要给本地用户一个查看版本、表、质量报告、
验收状态与已生成报告的入口（spec §8），随后还有操作面与定时触发器（spec §9）。
这三者都是常驻进程，直接冲击上述事实层表述，所以在任何服务代码合入前先正名。

同一问题在同类系统已有成熟先例（仅作设计出处，不引入依赖）：

- **Qlib PIT** 的"值 + 可得时间"双时间戳：读取端永远按数据可得时点取值，
  服务只是该语义之上的只读视图；
- **ArcticDB** 的 `as_of` 版本读：客户端钉一个版本号做快照读，写路径独立演进，
  读写不共享指针语义；
- **Datasette** 把 SQLite 只读地 serve 出来，并用 `--sql-time-limit-ms` 独立于
  行数限制控制查询时长；**OpenBB** 的 OBBject 信封回显请求参数，使响应可独立复现。

## 决策

1. **允许本地常驻只读查询面**：只读已发布 artifacts（dataset 版本、质量报告、
   验收记录、已生成实验报告）。它不 import `DataPipeline`、不持有 publisher、
   不暴露 POST/PUT/PATCH/DELETE（spec §8.1）。
2. **允许调度触发器**（P4 预告）：触发只经操作外壳以参数数组调 `data update`
   CLI，不拥有任何数据写语义；单一写入链仍是 `DatasetPublisher.publish`
   （ADR-001），服务与调度器都不是写入方。
3. **环回绑定**：查询面默认且只绑定 `127.0.0.1`（`localhost`/`::1` 可）；配置为
   非环回地址必须启动失败。认证、TLS、公网与多租户是非目标（spec §2.3），
   属另一份规格。
4. **版本钉住**：每个读取请求在入口解析一次明确版本（完整 64 位哈希或
   `current`），响应中的 `dataset_version` 永远是完整哈希，并回显本次请求的
   解析参数（表、列、过滤、分页）；一次请求中不再读 `CURRENT`，发布途中落在
   旧完整版本上（spec §4.4、§11"服务读期间 CURRENT 改变"）。
5. **人工验收边界不可自动化**：服务不得暴露任何能写 acceptance PASS、提升
   `CURRENT_ACCEPTED`、发布数据集或启动正式 `research run` 的端点；验收摘要
   只读展示四态（ACCEPTED/REJECTED/PENDING_CONFIRMATION/UNVERIFIED），
   "存在有效 accepted record"与"最近一次 verdict"分开显示，新的 rejected
   记录不得遮蔽仍可验证的 accepted record（spec §8.2、§11"acceptance 未签"）。
6. **表预览的只读硬界**：表必须来自该版本 manifest、列来自 schema 白名单、
   值过滤参数绑定、`limit` 默认 100 最大 500、查询时间预算（默认量级 5 秒，
   超时稳定错误码 `query_time_budget_exceeded`）与行数上限相互独立；不提供
   任意 SQL、任意文件路径或数据文件下载（spec §8.2）。
7. **事实层同变更更新义务**（spec §5.3）：服务或调度代码首次合入的同一变更
   必须更新 `docs/architecture/overview.md`（"只有 CLI"改写）、
   `module-map.md`（职责与禁止依赖方向）、`data-flow.md`（服务读路径、验收
   不自动化）。该挂钩由 P3 阶段计划的事实文档任务执行。

## 后果

- `pyproject.toml` 增设 `service` optional extra（fastapi、uvicorn）；核心 CLI
  安装不装服务栈；`duckdb` 同时提升为核心依赖，因为 `DatasetReader` 在正常
  路径直接 import 它（spec §5.4）。
- P4 操作面与 P5 门户都经本查询面/其响应契约取数，不直连数据层；409 冲突
  信封词汇（`update_already_running`）在 P3 的错误契约中先行冻结。
- 无认证/无 TLS 的环回边界是**有意的**；把它推向公网需要一份新规格与本 ADR
  的 supersede，而不是改配置。
- 服务对 acceptance/experiment 的读取走盘上 JSON 而非 `research.acceptance`
  Python 包（该包 `__init__` 传递 import `data_pipeline`，与本 ADR 第 1 条
  冲突）；若未来 acceptance 包拆出轻量读取面，可改为复用其模型。
```

- [ ] **Step 3: 追加索引行**（读 `docs/adr/DECISIONS_INDEX.md` 尾部，按现有六列格式**只追加**）

```markdown
| [021 The resident read-only query surface](021-resident-query-surface-and-scheduler.md) | accepted | 2026-10-01 | `src/stock_quant/service/**`, `docs/architecture/overview.md`, `docs/architecture/module-map.md`, `docs/architecture/data-flow.md` | resident query service, read-only, GET only, loopback bind, version pinning, echo arguments, no DataPipeline, no publisher, manual acceptance boundary, query time budget, Qlib PIT, ArcticDB as_of, Datasette | You add a resident process that reads published artefacts or triggers updates, change the service's bind, version-pinning or acceptance-display boundaries, or relax which components may write data. |
```

- [ ] **Step 4: 治理校验并提交**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_context_governance_docs.py -q` → Expected: PASS（frontmatter 四字段、索引相对链接、≤400 行全部满足）

```bash
git add docs/adr/021-resident-query-surface-and-scheduler.md docs/adr/DECISIONS_INDEX.md
git commit -m "docs(adr): adopt ADR-021 resident read-only query surface and scheduler

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: 依赖边界（duckdb 入核心 + service extra）

**Files:**
- Modify: `pyproject.toml:11-20`（dependencies 与新增 optional-dependencies 段）
- Modify: `docs/architecture/overview.md:49`（DuckDB 行——本任务改变的事实，同一提交同步）
- Test: `tests/service/test_dependency_boundary.py`（新建，连同 `tests/service/` 目录）

**Interfaces:**
- Consumes: spec §5.4。
- Produces: `service` extra（`pip install -e '.[service]'` 装 fastapi≥0.115、uvicorn≥0.30）；核心 `dependencies` 含 `duckdb>=1`。Task 3 起的测试依赖本任务装好的 fastapi。

- [ ] **Step 1: 写失败测试**

创建 `tests/service/test_dependency_boundary.py`：

```python
"""The dependency boundary: duckdb is core, the service stack is an extra."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _project_table() -> dict:
    with PYPROJECT.open("rb") as stream:
        return tomllib.load(stream)["project"]


def _dependency_names(dependencies: list[str]) -> set[str]:
    names = set()
    for entry in dependencies:
        match = re.match(r"[A-Za-z0-9_.-]+", entry.strip())
        assert match is not None, entry
        names.add(match.group(0).lower())
    return names


def test_duckdb_is_a_core_dependency() -> None:
    assert "duckdb" in _dependency_names(_project_table()["dependencies"])


def test_the_service_extra_is_exactly_fastapi_and_uvicorn() -> None:
    extras = _project_table()["optional-dependencies"]["service"]
    assert _dependency_names(extras) == {"fastapi", "uvicorn"}


def test_the_core_install_pulls_no_service_stack() -> None:
    core = _dependency_names(_project_table()["dependencies"])
    assert not core & {"fastapi", "uvicorn", "starlette"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/service/test_dependency_boundary.py -q`
Expected: FAIL — `test_duckdb_is_a_core_dependency` AssertionError（duckdb 不在 dependencies）；其余两项 KeyError `optional-dependencies`。

- [ ] **Step 3: 修改 pyproject 并安装 extra**

`pyproject.toml` 的 `[project]` 段替换为（duckdb 加进核心；新增 optional-dependencies）：

```toml
[project]
name = "stock-quant"
version = "0.1.0"
description = "Reproducible engineering MVP for A-share quantitative research"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "Jinja2>=3",
    "numpy>=1.22",
    "pandas>=3",
    "pydantic>=2",
    "plotly>=5",
    "pyarrow>=14",
    "PyYAML>=6",
    "typer>=0.9",
    "duckdb>=1",
]

[project.optional-dependencies]
# The read-only query service stack (spec §5.4, ADR-021): the core CLI
# install must not pull it in; install with `pip install -e '.[service]'`.
service = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
]
```

安装（联网，先按 Global Constraints 报备 owner）：

```bash
/home/ji/miniconda3/envs/sq312/bin/python -m pip install -e '/home/ji/work/program/stock[service]'
```
Expected: 成功；`/home/ji/miniconda3/envs/sq312/bin/python -c "import fastapi, uvicorn; print(fastapi.__version__)"` 打印 ≥0.115。若网络不可用：停下报告 owner，不得换源。

核心安装不含服务栈的验证（§5.4）：

```bash
/home/ji/miniconda3/envs/sq312/bin/python -m pip install --dry-run --quiet '/home/ji/work/program/stock' 2>&1 | grep -iE 'fastapi|uvicorn' || echo "core install clean"
```
Expected: `core install clean`。

- [ ] **Step 4: 同步 overview.md 的 DuckDB 行**

`docs/architecture/overview.md:49`（Core technologies 表）该行替换为：

```markdown
| DuckDB | Read-only query layer over a dataset version's Parquet tables. A core dependency of `pyproject.toml` (`DatasetReader` imports it on its normal path); `environment.yml` pins the conda environment. |
```

- [ ] **Step 5: 跑测试确认通过并提交**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/service/test_dependency_boundary.py tests/unit/test_context_governance_docs.py -q` → Expected: PASS

```bash
git add pyproject.toml docs/architecture/overview.md tests/service/test_dependency_boundary.py
git commit -m "build(deps): move duckdb to core dependencies and add the service extra

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 服务骨架（health / datasets / quality / experiments / 环回）

**Files:**
- Create: `src/stock_quant/service/__init__.py`、`errors.py`、`security.py`、`datasets.py`、`experiments.py`、`app.py`、`__main__.py`
- Test: `tests/service/conftest.py`、`tests/service/test_app_datasets.py`、`tests/service/test_service_boundary.py`

**Interfaces:**
- Consumes: Task 2 的 fastapi 环境；`DatasetReader`/`STANDARDIZED_SCHEMAS`（`data_model.dataset`）；`resolve_project_root`。
- Produces（Task 4/5/6 依赖的精确名字）:
  - `create_app(project_root: str | Path, *, query_budget_seconds: float = DEFAULT_QUERY_BUDGET_SECONDS) -> FastAPI`（`service/app.py`；`DEFAULT_QUERY_BUDGET_SECONDS = 5.0`）
  - `register_error_handlers(app: FastAPI) -> None`（Task 5 的 409 契约测试复用）
  - `ServiceError` 族与 `ErrorBody`/`ErrorResponse`（`service/errors.py`）：`DatasetUnresolvable`(404 `dataset_not_found`)、`NoCurrentDataset`(404 `no_current_dataset`)、`UnknownTable`(404 `unknown_table`)、`UnknownExperiment`(404 `experiment_not_found`)、`ReportNotFound`(404 `report_not_found`)、`UnknownColumn`(422 `unknown_column`)、`UnsupportedFilter`(422 `unsupported_filter`)、`QueryTimeBudgetExceeded`(422 `query_time_budget_exceeded`)、`ManifestUnreadable`(500 `dataset_manifest_unreadable`)、`QualityReportUnreadable`(500 `quality_report_unreadable`)、`AcceptanceRecordUnreadable`(500 `acceptance_record_unreadable`)、`ExperimentManifestUnreadable`(500 `experiment_manifest_unreadable`)、`ServiceConflict`(409 `update_already_running`)
  - `PinnedDataset(requested_version, dataset_version, context)`、`pinned_dataset` 依赖、`VersionPath`、`read_json_or_fail(path, error_class, what)`、`resolve_requested_version(project_root, requested) -> str`（`service/datasets.py`）
  - `validate_bind_host(host) -> str` / `NonLoopbackBindRejected`、`quote_identifier`（`service/security.py`）
  - `serve(project_root, *, host, port, query_budget_seconds)`（`service/__main__.py`）
  - tests 侧：`tests/service/conftest.py` 的 `write_configs`、`canonical_tables`、`publish_version(root, symbols) -> str`、`publish_experiment(root, dataset_version, experiment_id, *, with_report)`、`publish_acceptance_record(root, version, *, decision, created_at, reasons, policy_version)`、`sha256_of`、常量 `SYMBOLS`/`DATES`、fixture `service_project`/`current_version`/`client`。

- [ ] **Step 1: 写 conftest 与失败测试**

创建 `tests/service/conftest.py`：

```python
"""Offline fixtures for the read-only query-service tests.

One fixture project per test: template configs, a 9-table dataset published
through the production :class:`DatasetPublisher`, one published experiment
with a static report, and helpers for real content-addressed acceptance
records -- all under ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.data_quality.models import QualityIssue, QualityReport, Severity
from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    MANUAL_CHECK_CODES,
    POLICY_VERSION,
    AcceptanceDecision,
    AcceptanceRecord,
    CheckResult,
    CheckStatus,
    ManualCheckResult,
    ManualCheckStatus,
    compute_acceptance_id,
)
from stock_quant.research.acceptance.registry import AcceptanceRegistry
from stock_quant.service.app import create_app

_TEMPLATE_CONFIG = Path(__file__).resolve().parents[2] / "templates" / "project-config"

SYMBOLS = ("600000.SH", "600004.SH", "600006.SH")
DATES = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))
INGESTED_AT = datetime(2026, 1, 9, 1, 2, 3, tzinfo=timezone.utc)


def write_configs(root: Path) -> None:
    """Materialise the three config files a valid project root requires."""
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in ("project.yml", "sources.yml", "costs.yml"):
        (config_dir / name).write_text(
            (_TEMPLATE_CONFIG / name).read_text(encoding="utf-8"), encoding="utf-8"
        )


def canonical_frame(table: str, rows: list[dict]) -> pd.DataFrame:
    """A frame whose columns match the canonical schema order exactly."""
    columns = [field.name for field in STANDARDIZED_SCHEMAS[table]]
    return pd.DataFrame(rows, columns=columns)


def bar_rows(symbols: tuple[str, ...]) -> list[dict]:
    return [
        {
            "trade_date": trade_date,
            "symbol": symbol,
            "open": 10.0 + index,
            "high": 11.0 + index,
            "low": 9.0 + index,
            "close": 10.5 + index,
            "volume": 1_000 + index,
            "amount": 10_500.0 + index,
            "adjustment": "none",
            "source": "fixture",
            "ingested_at": INGESTED_AT,
        }
        for trade_date in DATES
        for index, symbol in enumerate(symbols)
    ]


def canonical_tables(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """All nine canonical tables, so the fixture survives any future
    publish-time registry-completeness gate."""
    bars = bar_rows(symbols)
    return {
        "daily_bar": canonical_frame("daily_bar", bars),
        "adjusted_bar": canonical_frame(
            "adjusted_bar",
            [
                {
                    "trade_date": row["trade_date"],
                    "symbol": row["symbol"],
                    "source": row["source"],
                    "adjustment": row["adjustment"],
                    "raw_close": row["close"],
                    "adjusted_close": row["close"],
                    "adjustment_factor": 1.0,
                    "quality_severity": "INFO",
                    "invalid_reason": None,
                    "applied_action_ids": "[]",
                }
                for row in bars
            ],
        ),
        "security_master": canonical_frame(
            "security_master",
            [
                {
                    "symbol": symbol,
                    "name": f"fixture {symbol}",
                    "exchange": "SSE",
                    "board": "main",
                    "list_date": date(2020, 1, 2),
                    "delist_date": None,
                    "list_status": "L",
                }
                for symbol in symbols
            ],
        ),
        "security_master_coverage": canonical_frame(
            "security_master_coverage",
            [
                {
                    "symbol": symbol,
                    "list_date": date(2020, 1, 2),
                    "delist_date": None,
                    "list_status": "L",
                    "source": "fixture",
                    "snapshot_sha256": "0" * 64,
                    "sdk_version": "fixture",
                    "checked_at": INGESTED_AT,
                }
                for symbol in symbols
            ],
        ),
        "corporate_action": canonical_frame("corporate_action", []),
        "corporate_action_quarantine": canonical_frame(
            "corporate_action_quarantine", []
        ),
        "corporate_action_coverage": canonical_frame("corporate_action_coverage", []),
        "trading_calendar": canonical_frame(
            "trading_calendar",
            [
                {"calendar_date": trade_date, "is_trading_day": True}
                for trade_date in DATES
            ],
        ),
        "universe_membership": canonical_frame("universe_membership", []),
    }


def fixture_quality_report() -> QualityReport:
    """Three non-blocking WARNING issues so the quality view has content."""
    return QualityReport(
        issues=tuple(
            QualityIssue(
                severity=Severity.WARNING,
                code="within_tolerance",
                table="daily_bar",
                symbol=symbol,
                trade_date=DATES[0],
                details={"diff": 0.01},
            )
            for symbol in SYMBOLS
        )
    )


def publish_version(root: Path, symbols: tuple[str, ...]) -> str:
    return DatasetPublisher(root).publish(
        canonical_tables(symbols), fixture_quality_report()
    ).version


def publish_experiment(
    root: Path,
    dataset_version: str,
    experiment_id: str = "e" * 64,
    *,
    with_report: bool = True,
) -> Path:
    directory = root / "data" / "experiments" / experiment_id
    directory.mkdir(parents=True)
    (directory / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "status": "ACCEPTED",
                "dataset_version": dataset_version,
                "universe_version": "u" * 64,
                "evaluation_reason": None,
            }
        ),
        encoding="utf-8",
    )
    if with_report:
        (directory / "report.html").write_text(
            "<html><body>fixture report</body></html>", encoding="utf-8"
        )
    return directory


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_acceptance_record(
    root: Path,
    version: str,
    *,
    decision: str,
    created_at: datetime,
    reasons: tuple[str, ...] = (),
    policy_version: str = POLICY_VERSION,
) -> None:
    """Publish one real, content-addressed acceptance record for ``version``.

    Tests may import the heavy acceptance package; the *service* may not
    (its ``__init__`` transitively imports ``stock_quant.data_pipeline``).
    """
    version_dir = root / "data" / "standardized" / version
    draft = AcceptanceRecord(
        policy_version=policy_version,
        acceptance_id="0" * 64,
        dataset_version=version,
        dataset_manifest_sha256=sha256_of(version_dir / "dataset_manifest.json"),
        quality_report_sha256=sha256_of(version_dir / "quality_report.json"),
        created_at=created_at,
        operator_id="fixture-operator",
        automated_checks=tuple(
            CheckResult(code=code, status=CheckStatus.PASS, summary="fixture pass")
            for code in AUTOMATED_CHECK_CODES
        ),
        manual_checks=tuple(
            ManualCheckResult(
                code=code, status=ManualCheckStatus.PASS, summary="fixture pass"
            )
            for code in MANUAL_CHECK_CODES
        ),
        raw_snapshot_evidence=(),
        decision=AcceptanceDecision(decision),
        reasons=reasons,
    )
    record = draft.model_copy(update={"acceptance_id": compute_acceptance_id(draft)})
    AcceptanceRegistry(root).publish(record)


@pytest.fixture()
def service_project(tmp_path: Path) -> Path:
    write_configs(tmp_path)
    version = publish_version(tmp_path, SYMBOLS)
    publish_experiment(tmp_path, version)
    return tmp_path


@pytest.fixture()
def current_version(service_project: Path) -> str:
    return (service_project / "data" / "standardized" / "CURRENT").read_text(
        encoding="utf-8"
    ).strip()


@pytest.fixture()
def client(service_project: Path) -> TestClient:
    return TestClient(create_app(service_project))
```

创建 `tests/service/test_app_datasets.py`：

```python
"""Skeleton behaviour: health, datasets, quality, acceptance states,
experiments and the once-per-request version resolution."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import SYMBOLS, publish_acceptance_record, write_configs  # noqa: E402
from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.service.app import create_app

_HEX64 = re.compile(r"[0-9a-f]{64}")


def test_health_reports_a_fingerprint_not_a_path(
    client: TestClient, service_project: Path
) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert re.fullmatch(r"[0-9a-f]{16}", body["project_root_fingerprint"])
    assert str(service_project) not in json.dumps(body)


def test_datasets_list_reports_current_tables_quality_and_acceptance(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/datasets").json()
    assert body["current"] == current_version
    entry = next(
        item for item in body["datasets"] if item["dataset_version"] == current_version
    )
    assert entry["is_current"] is True
    assert entry["table_count"] == len(STANDARDIZED_SCHEMAS)
    assert entry["quality"]["by_severity"]["WARNING"] == len(SYMBOLS)
    assert entry["acceptance"] == {
        "state": "UNVERIFIED",
        "has_valid_accepted_record": False,
        "latest_verdict": None,
        "record_count": 0,
    }


def test_dataset_detail_resolves_current_once_and_echoes_the_full_hash(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/datasets/current").json()
    assert body["dataset_version"] == current_version
    assert _HEX64.fullmatch(body["dataset_version"])
    assert body["requested_version"] == "current"
    assert {table["name"] for table in body["tables"]} == set(STANDARDIZED_SCHEMAS)
    assert body["manifest"]["dataset_version"] == current_version
    assert body["acceptance"]["state"] == "UNVERIFIED"


def test_a_project_without_publications_lists_nothing(tmp_path: Path) -> None:
    write_configs(tmp_path)
    body = TestClient(create_app(tmp_path)).get("/api/v1/datasets").json()
    assert body == {"datasets": [], "current": None}


def test_current_without_a_current_file_is_a_stable_404(tmp_path: Path) -> None:
    write_configs(tmp_path)
    response = TestClient(create_app(tmp_path)).get("/api/v1/datasets/current")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_current_dataset"


def test_unknown_full_hash_version_is_a_stable_404(client: TestClient) -> None:
    response = client.get("/api/v1/datasets/" + "f" * 64)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"


def test_malformed_version_is_a_stable_422(client: TestClient) -> None:
    response = client.get("/api/v1/datasets/not-a-hash")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_quality_view_paginates_and_echoes_its_arguments(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/datasets/current/quality?offset=1&limit=2").json()
    assert body["dataset_version"] == current_version
    assert body["requested_version"] == "current"
    assert body["offset"] == 1
    assert body["limit"] == 2
    assert body["total"] == len(SYMBOLS)
    assert [issue["symbol"] for issue in body["issues"]] == [SYMBOLS[1], SYMBOLS[2]]


def test_a_later_rejected_record_never_masks_a_valid_accepted_record(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    publish_acceptance_record(
        service_project,
        current_version,
        decision="ACCEPTED",
        created_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    publish_acceptance_record(
        service_project,
        current_version,
        decision="REJECTED",
        created_at=datetime(2026, 1, 11, tzinfo=timezone.utc),
        reasons=("fixture rejection",),
    )
    body = client.get("/api/v1/datasets/current").json()["acceptance"]
    assert body["state"] == "ACCEPTED"
    assert body["has_valid_accepted_record"] is True
    assert body["latest_verdict"] == "REJECTED"
    assert body["record_count"] == 2


def test_a_rejection_without_any_accepted_record_is_rejected(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    publish_acceptance_record(
        service_project,
        current_version,
        decision="REJECTED",
        created_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
        reasons=("fixture rejection",),
    )
    body = client.get("/api/v1/datasets/current").json()["acceptance"]
    assert body == {
        "state": "REJECTED",
        "has_valid_accepted_record": False,
        "latest_verdict": "REJECTED",
        "record_count": 1,
    }


def test_prepared_worksheets_without_any_record_are_pending_confirmation(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    (service_project / "data" / "acceptance-worksheets" / current_version).mkdir(
        parents=True
    )
    body = client.get("/api/v1/datasets/current").json()["acceptance"]
    assert body == {
        "state": "PENDING_CONFIRMATION",
        "has_valid_accepted_record": False,
        "latest_verdict": None,
        "record_count": 0,
    }


def test_experiments_list_and_report_of_an_existing_experiment(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/experiments").json()
    assert body["experiments"][0]["experiment_id"] == "e" * 64
    assert body["experiments"][0]["dataset_version"] == current_version
    response = client.get(f"/api/v1/experiments/{'e' * 64}/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "fixture report" in response.text


def test_a_missing_experiment_or_report_is_a_stable_404(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    from conftest import publish_experiment  # noqa: E402

    publish_experiment(service_project, current_version, "a" * 64, with_report=False)
    without_report = client.get(f"/api/v1/experiments/{'a' * 64}/report")
    assert without_report.status_code == 404
    assert without_report.json()["error"]["code"] == "report_not_found"
    missing = client.get(f"/api/v1/experiments/{'b' * 64}/report")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "experiment_not_found"
```

创建 `tests/service/test_service_boundary.py`：

```python
"""The service's architectural boundary: GET-only, no data pipeline, loopback."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from stock_quant.service.app import create_app
from stock_quant.service.security import NonLoopbackBindRejected, validate_bind_host


def test_every_route_is_get_only(service_project: Path) -> None:
    app = create_app(service_project)
    for route in app.routes:
        methods = set(getattr(route, "methods", set())) - {"HEAD"}
        assert methods <= {"GET"}, f"{route.path} declares {methods}"


def test_a_write_verb_on_a_defined_path_is_405(client: TestClient) -> None:
    response = client.post("/api/v1/datasets")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_importing_the_service_never_pulls_in_the_data_pipeline() -> None:
    probe = (
        "import sys, stock_quant.service; "
        "print('stock_quant.data_pipeline' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def test_the_service_refuses_non_loopback_hosts() -> None:
    for host in ("0.0.0.0", "192.168.1.10", "example.com"):
        with pytest.raises(NonLoopbackBindRejected):
            validate_bind_host(host)


def test_the_service_accepts_loopback_hosts() -> None:
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    assert validate_bind_host("localhost") == "localhost"
    assert validate_bind_host("::1") == "::1"


def test_serve_validates_the_host_before_uvicorn_starts(
    service_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stock_quant.service.__main__ as service_main

    started: list[tuple] = []
    monkeypatch.setattr(
        service_main,
        "uvicorn",
        SimpleNamespace(run=lambda *args, **kwargs: started.append((args, kwargs))),
    )
    with pytest.raises(NonLoopbackBindRejected):
        service_main.serve(
            service_project, host="0.0.0.0", port=8321, query_budget_seconds=5.0
        )
    assert started == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/service/test_app_datasets.py tests/service/test_service_boundary.py -q`
Expected: FAIL（collection error）— `ModuleNotFoundError: No module named 'stock_quant.service'`。

- [ ] **Step 3: 实现服务骨架**

创建 `src/stock_quant/service/errors.py`：

```python
"""The service's stable error vocabulary and the shared error envelope.

Independent module so routers and the app factory can both import it
without a cycle. Every failure fails closed through :class:`ErrorResponse`
with a stable ``code`` (spec §8.3).
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    dataset_version: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ServiceError(Exception):
    """Base of the stable failure vocabulary; carries its HTTP status."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DatasetUnresolvable(ServiceError):
    status_code = 404
    code = "dataset_not_found"


class NoCurrentDataset(ServiceError):
    status_code = 404
    code = "no_current_dataset"


class UnknownTable(ServiceError):
    status_code = 404
    code = "unknown_table"


class UnknownExperiment(ServiceError):
    status_code = 404
    code = "experiment_not_found"


class ReportNotFound(ServiceError):
    status_code = 404
    code = "report_not_found"


class UnknownColumn(ServiceError):
    status_code = 422
    code = "unknown_column"


class UnsupportedFilter(ServiceError):
    status_code = 422
    code = "unsupported_filter"


class QueryTimeBudgetExceeded(ServiceError):
    status_code = 422
    code = "query_time_budget_exceeded"


class ManifestUnreadable(ServiceError):
    status_code = 500
    code = "dataset_manifest_unreadable"


class QualityReportUnreadable(ServiceError):
    status_code = 500
    code = "quality_report_unreadable"


class AcceptanceRecordUnreadable(ServiceError):
    status_code = 500
    code = "acceptance_record_unreadable"


class ExperimentManifestUnreadable(ServiceError):
    status_code = 500
    code = "experiment_manifest_unreadable"


class ServiceConflict(ServiceError):
    """Reserved: P4's update-jobs endpoint raises this with 409 (spec §9.3).

    The 409 envelope shape is contracted here so the read-only surface and
    the later operations API share one error vocabulary from day one.
    """

    status_code = 409
    code = "update_already_running"
```

创建 `src/stock_quant/service/security.py`：

```python
"""Bind-host validation and the SQL identifier rule for the query service.

The service binds loopback only (spec §8.1, ADR-021): a non-loopback host
must fail startup. Identifiers reaching DuckDB are quoted with the
repository's existing double-quote doubling rule; values only ever travel
as bound parameters.
"""

from __future__ import annotations

import ipaddress
import socket

# The repository's one quoting rule for SQL identifiers (dataset.py:373).
# Imported rather than reimplemented so the rule cannot drift.
from stock_quant.data_model.dataset import _sql_identifier as quote_identifier

__all__ = ["NonLoopbackBindRejected", "quote_identifier", "validate_bind_host"]


class NonLoopbackBindRejected(RuntimeError):
    """The configured bind host is not loopback; the service refuses to start."""


def validate_bind_host(host: str) -> str:
    """Return ``host`` unchanged when it is a loopback address or name.

    Resolution failures (an unresolvable name, an offline machine) reject
    the host: an unknown host is never assumed loopback.
    """
    candidates = {host.strip().lower()}
    try:
        for info in socket.getaddrinfo(host.strip(), None):
            candidates.add(str(ipaddress.ip_address(info[4][0])))
    except (socket.gaierror, ValueError):
        pass
    loopback = {"127.0.0.1", "::1", "localhost"}
    if not candidates & loopback:
        raise NonLoopbackBindRejected(
            f"refusing to bind non-loopback host {host!r}; the read-only query "
            "service is local-only by design (spec §8.1, ADR-021)"
        )
    return host
```

创建 `src/stock_quant/service/datasets.py`：

```python
"""Dataset, quality and acceptance read endpoints (spec §8.2).

Acceptance and experiment data are read from their on-disk stores directly:
importing ``stock_quant.research.acceptance`` executes the package
``__init__``, which imports ``stock_quant.data_pipeline`` -- a dependency
the service is forbidden from taking (spec §8.1, ADR-021).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path as PathParam, Query, Request
from pydantic import BaseModel

from stock_quant.data_model.dataset import (
    DatasetContext,
    DatasetNotFoundError,
    DatasetReader,
)
from stock_quant.service.errors import (
    AcceptanceRecordUnreadable,
    DatasetUnresolvable,
    ManifestUnreadable,
    NoCurrentDataset,
    QualityReportUnreadable,
    ServiceError,
)

#: The acceptance policy a "valid accepted record" must carry. Duplicated as
#: a literal because the models module is unreachable without importing the
#: data pipeline (see module docstring). Source of truth:
#: ``research/acceptance/models.py::POLICY_VERSION``.
_ACCEPTANCE_POLICY_VERSION = "real-data-v1"
_CURRENT_NAME = "CURRENT"
_MANIFEST_NAME = "dataset_manifest.json"
_QUALITY_REPORT_NAME = "quality_report.json"
_VERSION_PATTERN = r"^(current|[0-9a-f]{64})$"
_VERSION_RE = re.compile(_VERSION_PATTERN)
_VERSION_DIR_RE = re.compile(r"^[0-9a-f]{64}$")
_WORKSHEET_DIRNAME = "acceptance-worksheets"

VersionPath = Annotated[str, PathParam(pattern=_VERSION_PATTERN)]


@dataclass(frozen=True)
class PinnedDataset:
    """One request's once-resolved dataset version and its read-only context."""

    requested_version: str
    dataset_version: str
    context: DatasetContext


class AcceptanceSummary(BaseModel):
    state: Literal["ACCEPTED", "REJECTED", "PENDING_CONFIRMATION", "UNVERIFIED"]
    has_valid_accepted_record: bool
    latest_verdict: str | None
    record_count: int


class QualitySummary(BaseModel):
    by_severity: dict[str, int]


class DatasetSummary(BaseModel):
    dataset_version: str
    is_current: bool
    created_at: str | None
    table_count: int
    quality: QualitySummary
    acceptance: AcceptanceSummary


class DatasetsListResponse(BaseModel):
    current: str | None
    datasets: list[DatasetSummary]


class TableMeta(BaseModel):
    name: str
    row_count: int
    schema_version: str


class DatasetDetailResponse(BaseModel):
    dataset_version: str
    requested_version: str
    manifest: dict[str, Any]
    tables: list[TableMeta]
    quality: QualitySummary
    acceptance: AcceptanceSummary


class QualityIssueView(BaseModel):
    severity: str | None = None
    code: str | None = None
    table: str | None = None
    symbol: str | None = None
    trade_date: str | None = None
    details: dict[str, Any] | None = None


class QualityViewResponse(BaseModel):
    dataset_version: str
    requested_version: str
    offset: int
    limit: int
    total: int
    issues: list[QualityIssueView]


def read_json_or_fail(path: Path, error_class: type[ServiceError], what: str) -> Any:
    """Parse one stored JSON file, failing closed through ``error_class``."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as cause:
        raise error_class(f"{what} ({path.name}) is unreadable") from cause


def resolve_requested_version(project_root: Path, requested: str) -> str:
    """Resolve ``current`` once at the request entry; full hashes pass through.

    This is the only place a request consults ``CURRENT`` (spec §4.4): after
    resolution the request is pinned to one immutable version.
    """
    if requested != "current":
        if not _VERSION_DIR_RE.fullmatch(requested):
            raise DatasetUnresolvable(f"version {requested!r} is not a full hash")
        return requested
    current_file = project_root / "data" / "standardized" / _CURRENT_NAME
    if not current_file.is_file():
        raise NoCurrentDataset(
            "no CURRENT file under data/standardized; no dataset has been published"
        )
    version = current_file.read_text(encoding="utf-8").strip()
    if not _VERSION_DIR_RE.fullmatch(version):
        raise NoCurrentDataset(f"CURRENT does not name a full version hash: {version!r}")
    return version


def pinned_dataset(version: VersionPath, request: Request) -> Iterator[PinnedDataset]:
    """Open one read-only context per request; never cached across requests."""
    root = Path(request.app.state.project_root)
    resolved = resolve_requested_version(root, version)
    request.state.resolved_version = resolved
    try:
        context = DatasetReader(root).open(resolved)
    except DatasetNotFoundError as error:
        raise DatasetUnresolvable(str(error)) from error
    except json.JSONDecodeError as error:
        raise ManifestUnreadable(
            f"dataset manifest of {resolved} is not valid JSON"
        ) from error
    with context:
        yield PinnedDataset(
            requested_version=version, dataset_version=resolved, context=context
        )


def _current_version_or_none(root: Path) -> str | None:
    try:
        return resolve_requested_version(root, "current")
    except ServiceError:
        return None


def load_quality_payload(project_root: Path, version: str) -> dict[str, Any]:
    path = (
        project_root
        / "data"
        / "standardized"
        / version
        / _QUALITY_REPORT_NAME
    )
    payload = read_json_or_fail(path, QualityReportUnreadable, "quality_report.json")
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise QualityReportUnreadable("quality_report.json is not a persisted report")
    return payload


def _acceptance_records(project_root: Path, version: str) -> list[dict[str, Any]]:
    """Every stored record for a version, oldest first; fail closed."""
    directory = project_root / "data" / "acceptances" / version
    if not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        payload = read_json_or_fail(
            child / "acceptance.json", AcceptanceRecordUnreadable, "acceptance record"
        )
        if not isinstance(payload, dict) or "decision" not in payload:
            raise AcceptanceRecordUnreadable(
                f"acceptance record {child.name} carries no decision"
            )
        records.append(payload)
    records.sort(
        key=lambda row: (str(row.get("created_at", "")), str(row.get("acceptance_id", "")))
    )
    return records


def _is_valid_accepted(row: dict[str, Any]) -> bool:
    """Mirror of ``AcceptanceRegistry.select``'s validity filter, on raw JSON."""
    if row.get("decision") != "ACCEPTED":
        return False
    if row.get("policy_version") != _ACCEPTANCE_POLICY_VERSION:
        return False
    automated = row.get("automated_checks")
    manual = row.get("manual_checks")
    if not isinstance(automated, list) or not isinstance(manual, list):
        return False
    return all(item.get("status") == "PASS" for item in automated) and all(
        item.get("status") == "PASS" for item in manual
    )


def acceptance_summary(project_root: Path, version: str) -> AcceptanceSummary:
    """Four states, with the valid-accepted flag and latest verdict separate.

    A newer REJECTED record never masks a still-valid ACCEPTED record (spec
    §8.2). PENDING_CONFIRMATION means an acceptance cycle has been prepared
    (unsigned worksheets under ``data/acceptance-worksheets/<version>/``) but
    no record satisfying the current policy exists -- which is also where a
    record ACCEPTED only under a superseded policy lands: it can no longer
    satisfy the Research gate and must be re-confirmed.
    """
    records = _acceptance_records(project_root, version)
    has_valid = any(_is_valid_accepted(row) for row in records)
    latest_verdict = str(records[-1]["decision"]) if records else None
    if has_valid:
        state: str = "ACCEPTED"
    elif latest_verdict == "REJECTED":
        state = "REJECTED"
    elif (project_root / "data" / _WORKSHEET_DIRNAME / version).is_dir():
        state = "PENDING_CONFIRMATION"
    else:
        state = "UNVERIFIED"
    return AcceptanceSummary(
        state=state,  # type: ignore[arg-type]
        has_valid_accepted_record=has_valid,
        latest_verdict=latest_verdict,
        record_count=len(records),
    )


router = APIRouter(prefix="/api/v1", tags=["datasets"])


@router.get("/datasets", response_model=DatasetsListResponse)
def list_datasets(request: Request) -> DatasetsListResponse:
    root = Path(request.app.state.project_root)
    standardized = root / "data" / "standardized"
    current = _current_version_or_none(root)
    entries: list[DatasetSummary] = []
    children = sorted(standardized.iterdir()) if standardized.is_dir() else []
    for child in children:
        if not child.is_dir() or not _VERSION_DIR_RE.fullmatch(child.name):
            continue
        manifest = read_json_or_fail(
            child / _MANIFEST_NAME, ManifestUnreadable, "dataset_manifest.json"
        )
        if not isinstance(manifest, dict):
            raise ManifestUnreadable(
                f"dataset manifest of {child.name} is not a JSON object"
            )
        quality = load_quality_payload(root, child.name)
        tables = manifest.get("tables")
        entries.append(
            DatasetSummary(
                dataset_version=child.name,
                is_current=child.name == current,
                created_at=str(manifest.get("created_at")) if manifest.get("created_at") else None,
                table_count=len(tables) if isinstance(tables, dict) else 0,
                quality=QualitySummary(by_severity=dict(quality.get("by_severity") or {})),
                acceptance=acceptance_summary(root, child.name),
            )
        )
    return DatasetsListResponse(current=current, datasets=entries)


@router.get("/datasets/{version}", response_model=DatasetDetailResponse)
def dataset_detail(
    pinned: Annotated[PinnedDataset, Depends(pinned_dataset)],
    request: Request,
) -> DatasetDetailResponse:
    root = Path(request.app.state.project_root)
    manifest = pinned.context.manifest
    tables = manifest.get("tables")
    table_meta = (
        [
            TableMeta(
                name=name,
                row_count=int(entry.get("row_count", 0)),
                schema_version=str(entry.get("schema_version", "")),
            )
            for name, entry in sorted(tables.items())
            if isinstance(entry, dict)
        ]
        if isinstance(tables, dict)
        else []
    )
    quality = load_quality_payload(root, pinned.dataset_version)
    return DatasetDetailResponse(
        dataset_version=pinned.dataset_version,
        requested_version=pinned.requested_version,
        manifest=dict(manifest),
        tables=table_meta,
        quality=QualitySummary(by_severity=dict(quality.get("by_severity") or {})),
        acceptance=acceptance_summary(root, pinned.dataset_version),
    )


@router.get("/datasets/{version}/quality", response_model=QualityViewResponse)
def dataset_quality(
    pinned: Annotated[PinnedDataset, Depends(pinned_dataset)],
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> QualityViewResponse:
    root = Path(request.app.state.project_root)
    payload = load_quality_payload(root, pinned.dataset_version)
    issues = payload["issues"]
    window = [issue for issue in issues[offset : offset + limit] if isinstance(issue, dict)]
    return QualityViewResponse(
        dataset_version=pinned.dataset_version,
        requested_version=pinned.requested_version,
        offset=offset,
        limit=limit,
        total=len(issues),
        issues=[
            QualityIssueView(**{key: issue.get(key) for key in QualityIssueView.model_fields})
            for issue in window
        ],
    )
```

创建 `src/stock_quant/service/experiments.py`：

```python
"""Experiment listing and the read-only report endpoint (spec §8.2).

Reads ``data/experiments/<experiment_id>/`` directly (see the datasets
module docstring for why the research package is not imported). The report
endpoint only serves an already-generated self-contained HTML file; it
never rebuilds a report in-request.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Path as PathParam, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from stock_quant.service.datasets import read_json_or_fail
from stock_quant.service.errors import (
    ExperimentManifestUnreadable,
    ReportNotFound,
    UnknownExperiment,
)

_EXPERIMENT_ID_PATTERN = r"^[0-9a-f]{64}$"
_EXPERIMENT_ID_RE = re.compile(_EXPERIMENT_ID_PATTERN)
_MANIFEST_NAME = "experiment_manifest.json"
_REPORT_NAME = "report.html"


class ExperimentSummary(BaseModel):
    experiment_id: str
    status: str | None = None
    dataset_version: str | None = None
    universe_version: str | None = None
    evaluation_reason: str | None = None


class ExperimentsListResponse(BaseModel):
    experiments: list[ExperimentSummary]


def _experiments_root(request: Request) -> Path:
    return Path(request.app.state.project_root) / "data" / "experiments"


def _read_experiment_manifest(directory: Path) -> dict[str, Any]:
    payload = read_json_or_fail(
        directory / _MANIFEST_NAME,
        ExperimentManifestUnreadable,
        "experiment_manifest.json",
    )
    if not isinstance(payload, dict):
        raise ExperimentManifestUnreadable(
            f"experiment manifest of {directory.name} is not a JSON object"
        )
    return payload


router = APIRouter(prefix="/api/v1", tags=["experiments"])


@router.get("/experiments", response_model=ExperimentsListResponse)
def list_experiments(request: Request) -> ExperimentsListResponse:
    root = _experiments_root(request)
    summaries: list[ExperimentSummary] = []
    children = sorted(root.iterdir()) if root.is_dir() else []
    for child in children:
        if not child.is_dir() or not _EXPERIMENT_ID_RE.fullmatch(child.name):
            continue
        manifest = _read_experiment_manifest(child)
        summaries.append(
            ExperimentSummary(
                experiment_id=child.name,
                status=manifest.get("status"),
                dataset_version=manifest.get("dataset_version"),
                universe_version=manifest.get("universe_version"),
                evaluation_reason=manifest.get("evaluation_reason"),
            )
        )
    return ExperimentsListResponse(experiments=summaries)


@router.get("/experiments/{experiment_id}/report")
def experiment_report(
    experiment_id: Annotated[str, PathParam(pattern=_EXPERIMENT_ID_PATTERN)],
    request: Request,
) -> FileResponse:
    """Serve the existing self-contained ``report.html``; never rebuild it."""
    root = _experiments_root(request).resolve()
    directory = (root / experiment_id).resolve()
    if directory.parent != root or not directory.is_dir():
        raise UnknownExperiment(f"no published experiment {experiment_id!r}")
    report = directory / _REPORT_NAME
    if not report.is_file():
        raise ReportNotFound(f"experiment {experiment_id} has no report.html")
    return FileResponse(report, media_type="text/html")
```

创建 `src/stock_quant/service/app.py`：

```python
"""The FastAPI application factory, shared error envelope and health route.

The query service is local, read-only and loopback-bound (spec §8.1,
ADR-021): it resolves one explicit dataset version per request, echoes the
resolved full hash in every response and never exposes a write verb.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from stock_quant.project_root import resolve_project_root
from stock_quant.service import datasets, experiments
from stock_quant.service.errors import ErrorBody, ErrorResponse, ServiceError

#: Default per-request query budget in seconds (spec §8.2: "默认量级 5 秒").
DEFAULT_QUERY_BUDGET_SECONDS = 5.0

API_PREFIX = "/api/v1"


class HealthResponse(BaseModel):
    status: Literal["ok"]
    project_root_fingerprint: str


def project_root_fingerprint(root: Path) -> str:
    """A stable, non-revealing fingerprint of the resolved project root."""
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]


def register_error_handlers(app: FastAPI) -> None:
    """Fail every error through the one envelope, echoing the resolved version."""

    @app.exception_handler(ServiceError)
    def _service_error(request: Request, error: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(
                error=ErrorBody(
                    code=error.code,
                    message=error.message,
                    dataset_version=getattr(request.state, "resolved_version", None),
                )
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    def _invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = "; ".join(str(item) for item in error.errors()[:3])
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error=ErrorBody(
                    code="invalid_request",
                    message=f"request parameters failed validation: {details}",
                    dataset_version=getattr(request.state, "resolved_version", None),
                )
            ).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    def _http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            error.status_code, "http_error"
        )
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(
                error=ErrorBody(code=code, message=str(error.detail))
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    def _unhandled(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorBody(
                    code="internal_error",
                    message=f"unhandled {type(error).__name__}",
                )
            ).model_dump(),
        )


def create_app(
    project_root: str | Path,
    *,
    query_budget_seconds: float = DEFAULT_QUERY_BUDGET_SECONDS,
) -> FastAPI:
    """Build the read-only query app for one validated project root."""
    root = resolve_project_root(project_root)
    app = FastAPI(
        title="stock-quant read-only query service",
        version="1.0.0",
        description=(
            "Local, loopback-only, GET-only views over published dataset "
            "versions, acceptance records and experiment reports (ADR-021)."
        ),
    )
    app.state.project_root = root
    app.state.query_budget_seconds = query_budget_seconds
    app.include_router(datasets.router)
    app.include_router(experiments.router)
    register_error_handlers(app)

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse, tags=["service"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok", project_root_fingerprint=project_root_fingerprint(root)
        )

    return app
```

创建 `src/stock_quant/service/__init__.py`：

```python
"""The local, read-only FastAPI query surface over published artefacts.

The service never imports the data pipeline and holds no publisher: it
reads immutable dataset versions, the acceptance registry's on-disk store
and generated experiment reports (spec §8.1, ADR-021). All routes are GET.
"""

from stock_quant.service.app import create_app

__all__ = ["create_app"]
```

创建 `src/stock_quant/service/__main__.py`：

```python
"""Loopback-only entrypoint: ``python -m stock_quant.service --root <ROOT>``."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from stock_quant.project_root import resolve_project_root
from stock_quant.service.app import DEFAULT_QUERY_BUDGET_SECONDS, create_app
from stock_quant.service.security import validate_bind_host


def serve(
    project_root: Path, *, host: str, port: int, query_budget_seconds: float
) -> None:
    """Validate the bind host first, then run uvicorn (never on non-loopback)."""
    validate_bind_host(host)
    app = create_app(project_root, query_budget_seconds=query_budget_seconds)
    uvicorn.run(app, host=host, port=port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m stock_quant.service",
        description="Local read-only query service (loopback only, ADR-021).",
    )
    parser.add_argument("--root", default=".", help="Project root (no fallback).")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback bind host.")
    parser.add_argument("--port", type=int, default=8321)
    parser.add_argument(
        "--query-budget-seconds",
        type=float,
        default=DEFAULT_QUERY_BUDGET_SECONDS,
        help="Per-request query time budget (independent of the row limit).",
    )
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    serve(
        root,
        host=args.host,
        port=args.port,
        query_budget_seconds=args.query_budget_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/service/test_app_datasets.py tests/service/test_service_boundary.py -q`
Expected: PASS（20 项全绿）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/service/ tests/service/conftest.py tests/service/test_app_datasets.py tests/service/test_service_boundary.py
git commit -m "feat(service): read-only dataset, acceptance and experiment endpoints

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 表预览端点（白名单、参数绑定、limit 上限、时间预算）

**Files:**
- Create: `src/stock_quant/service/tables.py`
- Modify: `src/stock_quant/service/app.py`（挂入 tables router，两行）
- Test: `tests/service/test_tables.py`

**Interfaces:**
- Consumes: Task 3 的 `PinnedDataset`/`pinned_dataset`/`VersionPath`（`service/datasets.py`）、`quote_identifier`（`service/security.py`）、`ServiceError` 族（`service/errors.py`）、`STANDARDIZED_SCHEMAS`（`data_model.dataset`）。
- Produces: `GET /api/v1/datasets/{version}/tables/{table}` → `TablePreviewResponse`；`run_with_budget(connection, sql, params, budget_seconds) -> pd.DataFrame`；`QueryTimeBudgetExceeded`（已定义于 Task 3 的 errors.py）；`DEFAULT_LIMIT = 100`、`MAX_LIMIT = 500`。

- [ ] **Step 1: 写失败测试**

创建 `tests/service/test_tables.py`：

```python
"""The whitelisted table preview: echo, filters, caps, budget, per-request
context (spec §8.2)."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from conftest import DATES, SYMBOLS  # noqa: E402
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.service.app import create_app
from stock_quant.service.tables import QueryTimeBudgetExceeded, run_with_budget

_DAILY_COLUMNS = [field.name for field in STANDARDIZED_SCHEMAS["daily_bar"]]


def test_default_preview_echoes_the_full_hash_and_arguments(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/datasets/current/tables/daily_bar").json()
    assert body["dataset_version"] == current_version
    assert re.fullmatch(r"[0-9a-f]{64}", body["dataset_version"])
    assert body["table"] == "daily_bar"
    assert body["arguments"] == {
        "requested_version": "current",
        "table": "daily_bar",
        "columns": _DAILY_COLUMNS,
        "symbol": None,
        "trade_date": None,
        "offset": 0,
        "limit": 100,
    }
    assert body["columns"] == _DAILY_COLUMNS
    assert len(body["rows"]) == len(DATES) * len(SYMBOLS)
    assert {row["symbol"] for row in body["rows"]} == set(SYMBOLS)
    assert all(isinstance(row["trade_date"], str) for row in body["rows"])


def test_a_column_subset_selects_whitelisted_columns(client: TestClient) -> None:
    body = client.get(
        "/api/v1/datasets/current/tables/daily_bar?columns=symbol,close"
    ).json()
    assert body["columns"] == ["symbol", "close"]
    assert body["arguments"]["columns"] == ["symbol", "close"]
    assert set(body["rows"][0]) == {"symbol", "close"}


def test_an_unknown_column_is_a_stable_422(client: TestClient) -> None:
    response = client.get(
        "/api/v1/datasets/current/tables/daily_bar?columns=symbol,password"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_column"


def test_symbol_and_date_filters_are_bound_parameters(
    client: TestClient,
) -> None:
    body = client.get(
        "/api/v1/datasets/current/tables/daily_bar",
        params={"symbol": SYMBOLS[0], "trade_date": DATES[0].isoformat()},
    ).json()
    assert body["arguments"]["symbol"] == SYMBOLS[0]
    assert body["arguments"]["trade_date"] == DATES[0].isoformat()
    assert [row["symbol"] for row in body["rows"]] == [SYMBOLS[0]]
    assert [row["trade_date"] for row in body["rows"]] == [DATES[0].isoformat()]


def test_an_injection_payload_in_a_value_filter_is_just_a_value(
    client: TestClient,
) -> None:
    payload = "'; DROP TABLE daily_bar; --"
    body = client.get(
        "/api/v1/datasets/current/tables/daily_bar", params={"symbol": payload}
    ).json()
    assert body["rows"] == []
    still_there = client.get("/api/v1/datasets/current/tables/daily_bar?limit=1")
    assert still_there.status_code == 200


def test_a_filter_on_a_table_without_that_column_is_a_stable_422(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/datasets/current/tables/trading_calendar?trade_date=2026-01-05"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_filter"


def test_limit_is_capped_and_offset_pages(client: TestClient) -> None:
    too_large = client.get("/api/v1/datasets/current/tables/daily_bar?limit=501")
    assert too_large.status_code == 422
    zero = client.get("/api/v1/datasets/current/tables/daily_bar?limit=0")
    assert zero.status_code == 422
    page_one = client.get(
        "/api/v1/datasets/current/tables/daily_bar?limit=2&offset=0"
    ).json()
    page_two = client.get(
        "/api/v1/datasets/current/tables/daily_bar?limit=2&offset=2"
    ).json()
    whole = client.get("/api/v1/datasets/current/tables/daily_bar?limit=4").json()
    assert page_one["rows"] != page_two["rows"]
    assert page_one["rows"] + page_two["rows"] == whole["rows"]


def test_an_unknown_or_traversal_table_name_is_a_stable_404(
    client: TestClient,
) -> None:
    for table in ("no_such_table", "Daily_Bar", "daily_bar%22%20--"):
        response = client.get(f"/api/v1/datasets/current/tables/{table}")
        assert response.status_code == 404, table
        assert response.json()["error"]["code"] == "unknown_table"


def test_a_zero_budget_refuses_to_run_the_query(service_project) -> None:
    client = TestClient(create_app(service_project, query_budget_seconds=0.0))
    response = client.get("/api/v1/datasets/current/tables/daily_bar")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "query_time_budget_exceeded"
    assert response.json()["error"]["dataset_version"]  # echo survives the failure


def test_a_slow_query_is_interrupted_at_the_budget(
    service_project, current_version: str
) -> None:
    """The budget is real interruption, not a row cap: a pure-compute query
    over ``range()`` takes seconds at this size and must be cut at 0.5s."""
    reader = DatasetReader(service_project)
    with reader.open(current_version) as context:
        with pytest.raises(QueryTimeBudgetExceeded):
            run_with_budget(
                context.connection,
                "SELECT count(*) FROM range(5000000000)",
                (),
                0.5,
            )


def test_each_request_opens_and_closes_its_own_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    original = DatasetReader.open

    def counting_open(self: DatasetReader, version: str):
        opened.append(version)
        return original(self, version)

    monkeypatch.setattr(DatasetReader, "open", counting_open)
    client.get("/api/v1/datasets/current/tables/daily_bar?limit=1")
    client.get("/api/v1/datasets/current/tables/daily_bar?limit=1")
    assert len(opened) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/service/test_tables.py -q`
Expected: FAIL（collection error）— `ModuleNotFoundError: No module named 'stock_quant.service.tables'`。

- [ ] **Step 3: 实现 `tables.py` 并挂入 app**

创建 `src/stock_quant/service/tables.py`：

```python
"""The whitelisted table-preview endpoint and its query time budget.

Hard bounds (spec §8.2): the table must come from the version's manifest,
columns from the canonical schema whitelist, values travel as bound
parameters, ``limit`` defaults to 100 and caps at 500, and every query runs
under a wall-clock budget that is independent of the row limit (Datasette's
``--sql-time-limit-ms`` is the prior art). No arbitrary SQL, no file paths,
no downloads.
"""

from __future__ import annotations

import math
import re
import threading
from datetime import date, datetime
from typing import Annotated, Any

import duckdb
import pandas as pd
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, JsonValue

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.service.datasets import PinnedDataset, pinned_dataset
from stock_quant.service.errors import (
    QueryTimeBudgetExceeded,
    UnknownColumn,
    UnknownTable,
    UnsupportedFilter,
)
from stock_quant.service.security import quote_identifier

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TableArguments(BaseModel):
    """The parsed request parameters, echoed verbatim (spec §8.2)."""

    requested_version: str
    table: str
    columns: list[str]
    symbol: str | None = None
    trade_date: str | None = None
    offset: int = 0
    limit: int = DEFAULT_LIMIT


class TablePreviewResponse(BaseModel):
    dataset_version: str
    table: str
    arguments: TableArguments
    columns: list[str]
    rows: list[dict[str, JsonValue]]


def run_with_budget(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    params: tuple[Any, ...],
    budget_seconds: float,
) -> pd.DataFrame:
    """Run one query under a wall-clock budget, independent of the row limit.

    A daemon ``Timer`` interrupts the running query through
    ``DuckDBPyConnection.interrupt()``; an interrupted query surfaces as the
    stable ``query_time_budget_exceeded`` error, never as a raw DuckDB
    failure. The flag is set *before* interrupting so the except-branch can
    tell an intentional interruption from any other DuckDB error.
    """
    if budget_seconds <= 0:
        raise QueryTimeBudgetExceeded(
            f"query budget is {budget_seconds}s; refusing to run the query"
        )
    exceeded = threading.Event()

    def _interrupt() -> None:
        exceeded.set()
        try:
            connection.interrupt()
        except RuntimeError:
            pass  # the query already finished; nothing to interrupt

    timer = threading.Timer(budget_seconds, _interrupt)
    timer.daemon = True
    timer.start()
    try:
        return connection.execute(sql, list(params)).df()
    except duckdb.Error as error:
        if exceeded.is_set():
            raise QueryTimeBudgetExceeded(
                f"query exceeded its {budget_seconds}s time budget"
            ) from error
        raise
    finally:
        timer.cancel()


def _json_safe(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (ValueError, AttributeError):
            return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


router = APIRouter(prefix="/api/v1", tags=["tables"])


@router.get("/datasets/{version}/tables/{table}", response_model=TablePreviewResponse)
def preview_table(
    pinned: Annotated[PinnedDataset, Depends(pinned_dataset)],
    table: str,
    request: Request,
    columns: str | None = Query(
        default=None, description="comma-separated canonical columns"
    ),
    symbol: str | None = Query(default=None),
    trade_date: str | None = Query(
        default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> TablePreviewResponse:
    schema = STANDARDIZED_SCHEMAS.get(table)
    if (
        table not in pinned.context.tables
        or schema is None
        or not _TABLE_NAME_RE.fullmatch(table)
    ):
        raise UnknownTable(
            f"dataset {pinned.dataset_version} has no table {table!r}"
        )
    allowed = [field.name for field in schema]
    if columns is None:
        selected = list(allowed)
    else:
        selected = [name.strip() for name in columns.split(",") if name.strip()]
        unknown = [name for name in selected if name not in allowed]
        if unknown:
            raise UnknownColumn(
                f"table {table} has no column(s) {unknown}; whitelist: {allowed}"
            )
        if not selected:
            raise UnknownColumn("columns parameter selected no columns")
    clauses: list[str] = []
    params: list[Any] = []
    if symbol is not None:
        if "symbol" not in allowed:
            raise UnsupportedFilter(f"table {table} has no symbol column to filter")
        clauses.append(f"{quote_identifier('symbol')} = ?")
        params.append(symbol)
    if trade_date is not None:
        if "trade_date" not in allowed:
            raise UnsupportedFilter(
                f"table {table} has no trade_date column to filter"
            )
        clauses.append(f"CAST(? AS DATE) = {quote_identifier('trade_date')}")
        params.append(trade_date)
    sql = (
        "SELECT "
        + ", ".join(quote_identifier(name) for name in selected)
        + " FROM "
        + quote_identifier(table)
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY " + ", ".join(quote_identifier(name) for name in selected[:2])
    sql += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    frame = run_with_budget(
        pinned.context.connection,
        sql,
        tuple(params),
        float(request.app.state.query_budget_seconds),
    )
    rows = [
        {name: _json_safe(value) for name, value in zip(frame.columns, record)}
        for record in frame.itertuples(index=False, name=None)
    ]
    return TablePreviewResponse(
        dataset_version=pinned.dataset_version,
        table=table,
        arguments=TableArguments(
            requested_version=pinned.requested_version,
            table=table,
            columns=selected,
            symbol=symbol,
            trade_date=trade_date,
            offset=offset,
            limit=limit,
        ),
        columns=list(frame.columns),
        rows=rows,
    )
```

`src/stock_quant/service/app.py` 两处修改——import 行：

```python
from stock_quant.service import datasets, experiments, tables
```

以及 `create_app` 中 router 挂载处（`experiments` 之后加一行）：

```python
    app.include_router(datasets.router)
    app.include_router(experiments.router)
    app.include_router(tables.router)
```

- [ ] **Step 4: 跑测试确认通过（含 Task 3 邻居）**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/service/ -q`
Expected: PASS（Task 3 + Task 4 全部用例；`test_a_slow_query_is_interrupted_at_the_budget` 约 0.5–1s）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/service/tables.py src/stock_quant/service/app.py tests/service/test_tables.py
git commit -m "feat(service): whitelisted table preview with a query time budget

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: 契约测试与安全测试（OpenAPI / 分页 / 404·409·422 / 失败关闭）

**Files:**
- Test: `tests/service/test_contract_security.py`（新建；无实现改动——本任务是契约钉住）

**Interfaces:**
- Consumes: Task 3/4 的全部端点与错误词汇；`register_error_handlers`、`ServiceConflict`。
- Produces: §8.3 三条完成条件中前两条的测试证据（契约固定 + 失败关闭）。

- [ ] **Step 1: 写契约与安全电池**

创建 `tests/service/test_contract_security.py`：

```python
"""OpenAPI contract freeze and the fail-closed security battery (spec §8.3).

This file pins what Tasks 3/4 built: any red here is an implementation
gap -- fix the service, never the assertion.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import SYMBOLS  # noqa: E402
from stock_quant.service.app import create_app, register_error_handlers
from stock_quant.service.errors import ServiceConflict

EXPECTED_PATHS = {
    "/api/v1/health",
    "/api/v1/datasets",
    "/api/v1/datasets/{version}",
    "/api/v1/datasets/{version}/quality",
    "/api/v1/datasets/{version}/tables/{table}",
    "/api/v1/experiments",
    "/api/v1/experiments/{experiment_id}/report",
}

EXPECTED_SCHEMAS = {
    "HealthResponse",
    "DatasetsListResponse",
    "DatasetDetailResponse",
    "QualityViewResponse",
    "TablePreviewResponse",
    "ExperimentsListResponse",
    "ErrorResponse",
}


def test_openapi_freezes_the_api_v1_surface(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == EXPECTED_PATHS
    for path, operations in schema["paths"].items():
        assert set(operations) <= {"get"}, path
        assert "requestBody" not in operations.get("get", {})
    assert EXPECTED_SCHEMAS <= set(schema["components"]["schemas"])


def test_the_409_conflict_envelope_is_contracted(service_project: Path) -> None:
    """P3 has no write verb to conflict; the 409 envelope P4 will raise is
    frozen here so both surfaces share one error vocabulary (spec §8.3)."""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/_probe")
    def _probe() -> dict:
        raise ServiceConflict("another update holds the lock")

    response = TestClient(app).get("/_probe")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "update_already_running"


def test_every_versioned_endpoint_echoes_the_resolved_full_hash(
    client: TestClient, current_version: str
) -> None:
    for path in (
        "/api/v1/datasets/current",
        "/api/v1/datasets/current/quality?limit=1",
        "/api/v1/datasets/current/tables/daily_bar?limit=1",
    ):
        body = client.get(path).json()
        assert body["dataset_version"] == current_version, path
        assert body["requested_version"] == "current", path


def test_pagination_arguments_are_echoed(client: TestClient) -> None:
    quality = client.get("/api/v1/datasets/current/quality?offset=2&limit=1").json()
    assert (quality["offset"], quality["limit"]) == (2, 1)
    preview = client.get(
        "/api/v1/datasets/current/tables/daily_bar?offset=3&limit=2"
    ).json()
    assert preview["arguments"]["offset"] == 3
    assert preview["arguments"]["limit"] == 2


def test_the_404_vocabulary_is_stable(client: TestClient) -> None:
    cases = {
        "/api/v1/datasets/" + "f" * 64: "dataset_not_found",
        "/api/v1/datasets/current/tables/no_such_table": "unknown_table",
        f"/api/v1/experiments/{'b' * 64}/report": "experiment_not_found",
    }
    for path, code in cases.items():
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == code, path


def test_the_422_vocabulary_is_stable(client: TestClient) -> None:
    cases = {
        "/api/v1/datasets/short": "invalid_request",
        "/api/v1/datasets/current/tables/daily_bar?limit=9999": "invalid_request",
        "/api/v1/datasets/current/tables/daily_bar?columns=nope": "unknown_column",
        "/api/v1/datasets/current/tables/trading_calendar?symbol=600000.SH": (
            "unsupported_filter"
        ),
    }
    for path, code in cases.items():
        response = client.get(path)
        assert response.status_code == 422, path
        assert response.json()["error"]["code"] == code, path


def test_path_traversal_cannot_escape_the_project(client: TestClient) -> None:
    for path in (
        "/api/v1/datasets/current/tables/..%2F..%2Fetc%2Fpasswd",
        "/api/v1/experiments/..%2F..%2Fetc%2Fpasswd/report",
        "/api/v1/experiments/%2e%2e%2e/report",
    ):
        response = client.get(path)
        assert response.status_code in (404, 422), path
        assert response.json()["error"]["code"] in {
            "unknown_table",
            "invalid_request",
            "not_found",
        }, path


def test_sql_injection_is_rejected_in_identifiers_and_inert_in_values(
    client: TestClient,
) -> None:
    identifier = client.get(
        "/api/v1/datasets/current/tables/daily_bar",
        params={"columns": 'symbol"; DROP TABLE daily_bar; --'},
    )
    assert identifier.status_code == 422
    assert identifier.json()["error"]["code"] == "unknown_column"
    value = client.get(
        "/api/v1/datasets/current/tables/daily_bar",
        params={"symbol": "600000.SH' OR '1'='1"},
    )
    assert value.status_code == 200
    assert value.json()["rows"] == []


def test_a_corrupt_dataset_manifest_fails_closed(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    manifest = (
        service_project / "data" / "standardized" / current_version
        / "dataset_manifest.json"
    )
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text("{ this is not json", encoding="utf-8")
    try:
        for path in (
            f"/api/v1/datasets/{current_version}",
            f"/api/v1/datasets/{current_version}/tables/daily_bar",
        ):
            response = client.get(path)
            assert response.status_code == 500, path
            assert response.json()["error"]["code"] == "dataset_manifest_unreadable"
    finally:
        manifest.write_text(original, encoding="utf-8")


def test_a_corrupt_quality_report_fails_closed(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    report = (
        service_project / "data" / "standardized" / current_version
        / "quality_report.json"
    )
    original = report.read_text(encoding="utf-8")
    report.write_text("[", encoding="utf-8")
    try:
        response = client.get("/api/v1/datasets/current/quality")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "quality_report_unreadable"
    finally:
        report.write_text(original, encoding="utf-8")


def test_a_corrupt_acceptance_record_fails_closed(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    directory = (
        service_project / "data" / "acceptances" / current_version / ("c" * 64)
    )
    directory.mkdir(parents=True)
    (directory / "acceptance.json").write_text("not json", encoding="utf-8")
    response = client.get("/api/v1/datasets/current")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "acceptance_record_unreadable"


def test_a_corrupt_experiment_manifest_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    manifest = (
        service_project / "data" / "experiments" / ("e" * 64)
        / "experiment_manifest.json"
    )
    manifest.write_text("[", encoding="utf-8")
    response = client.get("/api/v1/experiments")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "experiment_manifest_unreadable"


def test_responses_never_leak_absolute_paths(
    client: TestClient, service_project: Path
) -> None:
    for path in (
        "/api/v1/health",
        "/api/v1/datasets",
        "/api/v1/datasets/current",
        "/api/v1/datasets/current/quality",
        "/api/v1/datasets/current/tables/daily_bar",
    ):
        body = client.get(path).text
        assert str(service_project) not in body, path
        assert "/home/" not in body, path
```

- [ ] **Step 2: 跑电池并处置结果**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/service/test_contract_security.py -q`
Expected: PASS（钉住性质——Task 3/4 已封洞）。**任一 FAIL 都是安全/契约缺口**：按失败信息修 `src/stock_quant/service/` 对应模块（例如 OpenAPI 多出的路径说明挂了多余 router），不放宽断言、不删用例；修复后重跑本文件直到全绿。

- [ ] **Step 3: 提交**

```bash
git add tests/service/test_contract_security.py
git commit -m "test(service): freeze the openapi contract and the fail-closed battery

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: 并发读——不见半发布状态（§8.3）

**Files:**
- Test: `tests/service/test_concurrency.py`（新建）

**Interfaces:**
- Consumes: conftest 的 `write_configs`/`publish_version`/`SYMBOLS`/`DATES`；`DatasetReader`（预热 catalog）；Task 3 的 `create_app`。
- Produces: §8.3 第二条完成条件的测试证据：多 reader 并发读 + 一路独立 data update（这里用 `DatasetPublisher.publish` 直发——与 `data update` 的同一写入链终点）同时进行时，读取请求始终看到旧完整版本或新请求解析后的完整版本。

- [ ] **Step 1: 写并发测试**

创建 `tests/service/test_concurrency.py`：

```python
"""Concurrency evidence for spec §8.3: many readers plus one independent
publication -- every read sees one complete version, never a half-published
tree, and a hash-pinned request outlives a CURRENT change."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import DATES, SYMBOLS, publish_version, write_configs  # noqa: E402
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.service.app import create_app

_EXTRA_SYMBOL = "600008.SH"


def test_concurrent_readers_see_only_complete_versions(tmp_path: Path) -> None:
    write_configs(tmp_path)
    version_one = publish_version(tmp_path, SYMBOLS)
    # Warm the per-version DuckDB catalog so reader threads never race the
    # catalog *build*; that is an existing DatasetReader property, not the
    # publication-atomicity property under test here.
    reader = DatasetReader(tmp_path)
    reader.open(version_one).close()

    app = create_app(tmp_path)
    stop = threading.Event()
    failures: list[str] = []
    observed: set[str] = set()

    def read_loop() -> None:
        client = TestClient(app)
        while not stop.is_set():
            response = client.get("/api/v1/datasets/current/tables/daily_bar?limit=500")
            if response.status_code != 200:
                failures.append(f"{response.status_code}: {response.text[:200]}")
                return
            body = response.json()
            observed.add(body["dataset_version"])
            symbols = {row["symbol"] for row in body["rows"]}
            if body["dataset_version"] == version_one:
                complete = symbols == set(SYMBOLS) and len(body["rows"]) == len(
                    SYMBOLS
                ) * len(DATES)
            else:
                complete = symbols <= set(SYMBOLS) | {_EXTRA_SYMBOL}
            if not complete:
                failures.append(
                    f"partial view of {body['dataset_version']}: {sorted(symbols)}"
                )
                return

    threads = [threading.Thread(target=read_loop) for _ in range(8)]
    for thread in threads:
        thread.start()
    try:
        version_two = publish_version(tmp_path, SYMBOLS + (_EXTRA_SYMBOL,))
        reader.open(version_two).close()  # warm the new catalog mid-flight
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "a reader thread hung"

    assert failures == []
    assert observed <= {version_one, version_two}
    # Once publication has settled, a fresh request must resolve the new version.
    final = TestClient(app).get(
        "/api/v1/datasets/current/tables/daily_bar?limit=500"
    ).json()
    assert final["dataset_version"] == version_two
    assert {row["symbol"] for row in final["rows"]} == set(SYMBOLS) | {_EXTRA_SYMBOL}


def test_a_hash_pinned_request_outlives_a_current_change(tmp_path: Path) -> None:
    write_configs(tmp_path)
    version_one = publish_version(tmp_path, SYMBOLS)
    version_two = publish_version(tmp_path, SYMBOLS + (_EXTRA_SYMBOL,))
    client = TestClient(create_app(tmp_path))
    pinned = client.get(
        f"/api/v1/datasets/{version_one}/tables/daily_bar?limit=500"
    ).json()
    assert pinned["dataset_version"] == version_one
    assert {row["symbol"] for row in pinned["rows"]} == set(SYMBOLS)
    current = client.get("/api/v1/datasets/current/tables/daily_bar?limit=500").json()
    assert current["dataset_version"] == version_two
    assert {row["symbol"] for row in current["rows"]} == set(SYMBOLS) | {_EXTRA_SYMBOL}
```

- [ ] **Step 2: 跑测试并处置结果**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/service/test_concurrency.py -q`
Expected: PASS（发布原子性是既有性质：版本目录整体 `os.replace`、`CURRENT` 原子替换、读者钉住绝对 Parquet 路径——`dataset.py:11-15/134/161`）。**若 FAIL**：这是真实的半发布可见性缺陷，按失败样本（status 非 200 / symbol 集不完整 / 观察到第三种版本）定位 `DatasetPublisher.publish` 与 `resolve_requested_version` 的窗口，修复后重跑；不得放宽断言（不得改成"多数请求正确"）。

- [ ] **Step 3: 提交**

```bash
git add tests/service/test_concurrency.py
git commit -m "test(service): concurrent readers never observe a half-published version

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: 事实文档三件套同步 + 批次收口（§5.3）

**Files:**
- Modify: `docs/architecture/overview.md`（Runtime shape 节 + Component relationship 尾注；DuckDB 行已在 Task 2 同步）
- Modify: `docs/architecture/module-map.md`（包职责表 + 依赖方向 + Before-you-modify 表）
- Modify: `docs/architecture/data-flow.md`（新增 §7 服务读路径；失败语义表加一行）

**Interfaces:**
- Consumes: Task 1 的 ADR-021（"no server"事实层的同变更更新挂钩）；Task 3/4 落地的服务形态。
- Produces: spec §5.3 的三件套同步（RUNBOOK 条目按路线图归 P4 Task 29，见 Global Constraints 留白）；G3 门核对。

- [ ] **Step 1: 改写 overview.md 的 Runtime shape**

`docs/architecture/overview.md` 的 `## Runtime shape` 节（"One local, single-process CLI …"起至代码块结束）整体替换为：

```markdown
## Runtime shape

Local, single-process entry points — still no database, no remote
deployment, no hidden global state:

1. The CLI remains the **domain write entry**: every
   `python -m stock_quant <group> <command>` resolves a project root
   explicitly and is manual and idempotent.

```text
stock_quant.cli (Typer)
  └── bootstrap / services per command group
        ├── data       → data_pipeline, data_sources, data_model, data_quality
        ├── research   → research pipeline (freeze → universe → factors → portfolio → backtest)
        ├── backtest   → engineering-only single-window replay
        └── report     → reporting from already-published artefacts
```

2. A local **read-only query service** (`python -m stock_quant.service`) serves
   GET-only views over already-published artefacts. It binds `127.0.0.1` only
   (a non-loopback configuration fails startup), imports neither the data
   pipeline nor a publisher, and pins one dataset version per request, echoing
   the resolved full hash in every response (ADR-021).

All state lives on disk under the given project root (`data/raw`,
`data/standardized`, `data/acceptances`, `data/runs`, `data/experiments`).
There is no fallback root; see `invariants.md`.
```

并在 `## Component relationship` 代码块之后、`Reproducibility is the property…` 段之前插入一段：

```markdown
`service` (the read-only query surface) sits outside this chain: it reads
published versions through `data_model.dataset.DatasetReader` plus the
acceptance and experiment stores directly, and nothing imports it. Its
boundaries — loopback bind, GET-only, version pinning, no automation of
acceptance — are ADR-021's decision, not this map's assertion.
```

- [ ] **Step 2: module-map.md 加 service 行**

`docs/architecture/module-map.md` 包职责表（`module-level` 行之前）追加一行：

```markdown
| `stock_quant.service` | The local, loopback-only, GET-only FastAPI query surface over published dataset versions, acceptance records, experiments and generated reports (`app`, `datasets`, `tables`, `experiments`, `security`). | Any write path: it imports neither the data pipeline nor a publisher, exposes no POST/PUT/PATCH/DELETE, and never automates acceptance (ADR-021). |
```

依赖方向代码块（`reporting    -> analytics, data_quality` 行之后）追加：

```text
service      -> data_model, project_root
```

并在该代码块后的说明段末尾补一句：`service` reads the acceptance and
experiment stores from disk rather than importing `research` (whose
acceptance package transitively imports the pipeline); nothing imports
`service`."（中英混排按文件现状——该文件为英文，用英文句子：）

```markdown
`service` reads the acceptance and experiment stores from disk rather than
importing `research` (whose acceptance package transitively imports the
pipeline); nothing imports `service`.
```

`## Before you modify` 表追加一行：

```markdown
| The query service or its bind/version/acceptance boundaries | `docs/adr/021-resident-query-surface-and-scheduler.md`, `docs/architecture/data-flow.md` |
```

- [ ] **Step 3: data-flow.md 加服务读路径**

`docs/architecture/data-flow.md` 在 `## 6. Reporting` 节之后、`## Failure semantics` 之前插入：

```markdown
## 7. Read-only query surface

`python -m stock_quant.service --root <ROOT>` serves GET-only views over
already-published artefacts (ADR-021). Every data request resolves its
version exactly once — `current` or a full hash at the entry — and every
response echoes the resolved full `dataset_version` plus the request's
parsed arguments; a request never re-reads `CURRENT` mid-flight, so a
publication that lands during a request leaves it on the old complete
version. The service opens one read-only `DatasetReader` context per
request, binds `127.0.0.1` only (non-loopback configuration fails
startup), imports neither the data pipeline nor a publisher, and offers no
arbitrary SQL, file paths or downloads; table previews are bounded by the
version manifest's tables, the canonical schema whitelist, `limit ≤ 500`
and an independent query time budget. Acceptance is not automated here:
the service only displays the four read-only summary states
(`ACCEPTED`/`REJECTED`/`PENDING_CONFIRMATION`/`UNVERIFIED`), and no
endpoint can write an acceptance verdict, publish a dataset or start a
research run.
```

失败语义表（`| Failure | What survives |` 表内）追加一行：

```markdown
| Publication while the service reads | The in-flight request keeps its resolved version; the next request may resolve the new one; no reader ever sees a half-published tree |
```

- [ ] **Step 4: 治理校验 + 全套回归**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_context_governance_docs.py tests/service/ -q`
Expected: PASS（三件套行数仍 ≤400、链接有效；服务套件全绿）。再抽查既有读侧邻居未受影响：
Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_dataset_publisher.py tests/integration/test_reports.py -q`
Expected: PASS（本计划未改 dataset.py / reporting；任何红先查是否误碰在途 WIP）。

- [ ] **Step 5: 提交与批次收口**

```bash
git add docs/architecture/overview.md docs/architecture/module-map.md docs/architecture/data-flow.md
git commit -m "docs(architecture): record the read-only query surface in the fact layer

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

对照 spec §8.3 / G3 门逐条勾稽并向 owner 汇报：契约测试固定 OpenAPI 响应模型、分页、404/409/422 与版本回显（Task 5）；并发读与半发布不可见（Task 6）；路径穿越、SQL 注入、超大 limit、未知表/列、损坏 manifest 全部失败关闭（Task 5）；事实文档三件套同一批次更新（本任务，§5.3）。汇报时附两个成文留白：RUNBOOK 服务条目归 P4；409 信封已契约化但触发方在 P4。

---

## Self-Review 记录

- **规格覆盖（§8 逐条）**：
  - §8.1 进程边界（只依赖 root/DatasetReader/acceptance registry 与已生成报告；不 import DataPipeline、不持有 publisher、无写动词；默认 127.0.0.1、非环回启动失败）→ Task 3（`test_service_boundary.py` 全部用例 + `validate_bind_host`/`serve`）。
  - §8.2 API v1 七条路径逐条 → health（Task 3）、datasets 列表/详情/quality（Task 3）、tables（Task 4）、experiments 列表/report（Task 3，FileResponse 只返回既有 HTML 不重建）；`{version}` 双形态 + 入口解析一次 + 响应永远完整哈希（`pinned_dataset`/`resolve_requested_version`，Task 3 测试 + Task 5 回显断言）；参数回显（每响应 `arguments`/`requested_version`/分页字段，Task 4/5）；limit 默认 100 最大 500（Task 4）；时间预算默认 5 秒、`query_time_budget_exceeded` 与行数独立（Task 4 `run_with_budget` + 零预算与真中断两用例）；表来自 manifest、列来自 schema 白名单、值参数绑定、标识符复用 `_sql_identifier`（Task 4）；不提供任意 SQL/文件路径/文件下载（无该类端点，Task 5 OpenAPI 冻结断言"路径集合恰为七条"）；每请求独立开/关只读 context、不跨请求缓存 CURRENT（`pinned_dataset` 生成器 + Task 4 open 计数用例）；acceptance 四态且"有效 accepted"与"最近 verdict"分开、新 REJECTED 不遮蔽（Task 3 三用例）。
  - §8.3 三条完成条件 → 契约测试（Task 5）、并发读（Task 6）、失败关闭电池（Task 5）。
  - §5.2 ADR-021（含 Qlib PIT/ArcticDB as_of/Datasette 引用不引依赖、环回、版本钉住、人工验收边界、调度触发器预告、"no server"事实层挂钩）→ Task 1。§5.3 事实文档三件套同一变更 → Task 7（RUNBOOK 归 P4，成文留白）。§5.4 依赖边界 → Task 2（duckdb 入核心因 `DatasetReader` 正常路径 import；service extra 不进核心；`--dry-run` 验证核心安装不含服务栈；Vue/apscheduler/systemd 不在本期范围）。
  - §2.2 相关行（"Web 响应包含 dataset_version；一次请求解析版本后不再读 CURRENT"；"发布后既有版本目录字节不变"）→ Task 3 回显 + Task 6。§4 不可协商约束第 4 条（钉住版本）→ 同上；第 1/5 条（单一写入链、无自动验收）→ 服务零写动词 + 摘要只读（Task 3/5）。§11 相关行（"服务读期间 CURRENT 改变"、"acceptance 未签 → UNVERIFIED/PENDING"）→ Task 6 第二用例 + Task 3 PENDING_CONFIRMATION 用例。
- **类型一致性**：`PinnedDataset(requested_version, dataset_version, context)`、`pinned_dataset`、`VersionPath`、`read_json_or_fail`、`resolve_requested_version`、`run_with_budget(connection, sql, params, budget_seconds)`、`QueryTimeBudgetExceeded`、`quote_identifier`、`validate_bind_host`、`serve(project_root, *, host, port, query_budget_seconds)`、`create_app(project_root, *, query_budget_seconds=DEFAULT_QUERY_BUDGET_SECONDS)`、错误码字符串（`dataset_not_found` 等 14 个）在 Task 3/4/5/6 的引用与定义逐一核对一致；conftest 的 `publish_version`/`publish_acceptance_record` 签名与 Task 5/6 调用点一致（`publish_experiment` 的 `with_report` 关键字参数在 Task 3 两处调用一致）。
- **已知留白（有意的，各有归属）**：
  - **409 触发方不在 P3**：读服务没有写动词可冲突；§8.3 的"409"以冻结 409 错误信封（`ServiceConflict` + `register_error_handlers` 契约测试，Task 5）满足，真正抛出方是 P4 的 `POST /api/v1/update-jobs`。
  - **RUNBOOK 服务条目归 P4 Task 29**（且 RUNBOOK.md 是在途 WIP，本计划不碰）；§5.3 的 RUNBOOK 半条由 P4 交付。
  - **服务直读 acceptance/experiment JSON 而非复用 pydantic 模型**：因 `research/acceptance/__init__` 传递 import `data_pipeline`（"开工前"第 4 条）；`_is_valid_accepted` 是 `AcceptanceRegistry.select` 谓词的显式镜像，若两侧语义分叉以 registry 为准并补测试。
  - **`environment.yml` 未动**：spec §5.4 只要求 duckdb 进 pyproject；conda 环境继续由 environment.yml 钉住（overview 的 DuckDB 行已改写为两者并述）。
  - **datasets 列表不分页**：§8.2 只要求 quality 与表预览分页；版本数是项目生命周期量级，列表回显 `current` 字段即可，若将来膨胀由 P5 门户再裁。
  - **Task 5/6 属钉住性质**：首跑即绿是预期确认（Global Constraints 已声明）；任何红按实现缺口处置。
- **复核记录（2026-10-01）**：编写中修正了三处自身缺陷——(1) 最初设计让服务 import `AcceptanceRegistry`，实测发现 acceptance 包 `__init__` 传递引入 `data_pipeline`，改为直读盘上 JSON 并补子进程级治理测试；(2) `validate_bind_host("localhost")` 需解析后判环回，否则拒绝合法主机，改为候选集合并集判定；(3) 表预览对 `..` 裸段不必依赖客户端不规范化，测试改用 `%2F`/`%22` 编码形态并放宽到 404/422 皆可、按错误码断言。
