---
status: accepted
date: 2026-10-02
decision: "A local, resident read-only query surface and a scheduler trigger shell are allowed as further entry points beside the CLI, under four hard boundaries: they own no data-write semantics (the single write chain stays DatasetPublisher.publish, reached only through the CLI), they bind loopback only and fail startup on any non-loopback configuration, every read request pins its dataset version exactly once at the entry and echoes the resolved full hash in every response, and the human acceptance boundary is not automatable — no service endpoint may write an acceptance verdict, publish a dataset, promote CURRENT_ACCEPTED or start a formal research run. Design lineage (cited as provenance only, no dependency introduced): Qlib PIT's value-plus-available-at dual timestamps, ArcticDB's as_of version reads, Datasette's read-only served databases with --sql-time-limit-ms, and OpenBB's arguments-echo response envelope."
affects:
  - src/stock_quant/service/**
  - docs/architecture/overview.md
  - docs/architecture/module-map.md
  - docs/architecture/data-flow.md
---

# ADR-021: 本地常驻只读查询面与调度触发器

日期：2026-10-02
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
