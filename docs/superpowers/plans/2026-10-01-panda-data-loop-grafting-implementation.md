# Panda 数据闭环嫁接 Stock · 总路线图与阶段计划索引

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

按 writing-plans 的 Scope Check 与规格"每阶段独立计划"的裁定，本文件是**总路线图**（批次结构、owner 决策点、全局约束与批次任务卡）；七个批次各有一份全码阶段计划，**均已编写完毕**：

| 阶段计划 | 覆盖批次 | 状态 |
| --- | --- | --- |
| [2026-10-01-panda-coverage-evidence-model.md](2026-10-01-panda-coverage-evidence-model.md) | P0 部分（许可记录 + ADR-022）+ P2a（§7.5 覆盖证据模型） | P0 部分**已完成**（均已提交）；P2a **已开工**（工作区有在途改动） |
| [2026-10-01-panda-endpoints.md](2026-10-01-panda-endpoints.md) | P1（探针 + 两端点） | 已成文；联网探针步骤阻塞于 owner 授权 |
| [2026-10-01-panda-membership-slice-hash.md](2026-10-01-panda-membership-slice-hash.md) | P2b（schema-v2 + refresh） | 已成文；Task 0（ADR-023）按 (a) 裁定**在 G0 成文**，§7.0.10 协议裁定随之上移为 G0 输入 |
| [2026-10-01-panda-basic-factor.md](2026-10-01-panda-basic-factor.md) | P2c | 已成文；硬前置为 P2a 合码 + P1 探针结论 |
| [2026-10-01-panda-query-service.md](2026-10-01-panda-query-service.md) | P3 | 已成文；ADR-021 的任务卡在其中，但按 (a) 裁定**在 G0 成文**；可与 P1/P2 并行 |
| [2026-10-01-panda-operations-scheduler.md](2026-10-01-panda-operations-scheduler.md) | P4（操作面、单飞锁与调度器） | 已成文，可执行 |
| [2026-10-01-panda-web-portal.md](2026-10-01-panda-web-portal.md) | P5 | 已成文；契约 pin 表已按 P3/P4 冻结的响应模型逐字段写成（更新页另依赖 P4 运行） |

**Goal:** 按 [总规格](../specs/2026-09-29-panda-data-loop-grafting-design.md)（2026-10-01 修订）落地六阶段能力：既有 Tushare 通道的两个新端点、PIT 成分事实与 `basic_factor`、只读查询面、操作面/调度器与 Vue 门户——不引入新供应商、不引入第二套数据真相、不弱化任何既有门禁。

**Architecture:** 嫁接而非搬运：所有新数据走 Stock 既有 raw snapshot → normalize → 质量门禁 → 内容寻址发布链；查询面只读钉版本；操作面与调度器只经 `operations update` 外壳触发同一条 `data update` 路径。多 `universe_id` 并存以 schema-v2 切片哈希为硬前置；新表进入以 §7.5 覆盖证据模型扩展为硬前置；服务/调度的架构正名以三份 ADR 为硬前置——ADR-022 已 accepted（[022](../../adr/022-panda-graft-source-scope-and-basic-factor.md)，commit `873b2f77d`），ADR-021 与 membership-hash ADR（ADR-023）按 owner 2026-10-01 裁定同在 **G0** 成文（任务卡分别在 [panda-query-service](2026-10-01-panda-query-service.md) Task 1 与 [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) Task 0，执行时点前移到 G0；见下表下方注）。

**Tech Stack:** Python 3.12（sq312 conda env）、pandas、pyarrow、duckdb、pydantic v2、pytest；P3 起新增 `service` optional extra（fastapi、uvicorn）；P5 起 Vue 3 + Vite（独立 `package.json`，不进 Python 依赖）。无新数据源依赖。

**Spec:** [docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md](../specs/2026-09-29-panda-data-loop-grafting-design.md)（本计划的条目都带 § 引用，实现时以规格原文为准）

## Global Constraints

- **批序**：P0 →（P1 探针 ∥ P2a 覆盖模型 ∥ P2b schema-v2 基础）→ P2c → P3 → P4 → P5。P3 只依赖 P0，可与 P1/P2 并行；P4 依赖 P3 状态模型冻结；P5 依赖 P3（更新页另依赖 P4）。规格 §13 的门（G0–G5）按批核对。
- **阶段独立交付**（规格开篇裁定）：七个批次的阶段计划均已成文（见上表）。开工前若上游批次已落地、导致所属阶段计划的细节漂移，先在同一分支更新该阶段计划再动手；不得把六个阶段合成一次大改。
- **本文件与阶段计划的分工**：本文件只保留批次结构、owner 决策点、全局约束与**历史序号**；某批次一旦有成文阶段计划，其步骤、文件清单、命令与测试**以该阶段计划为准**，本文件对应任务卡仅为批次意图与序号锚点（阶段计划的 Self-Review 按此序号回指）。
- **文件名引用**：本文件的 Task 编号与各阶段计划的 Task 编号**互不通用**（本文件有编号空洞：1/3/5 已随内容并入阶段计划）。跨文件引用任务时必须写全"文件名 + Task N"。
- 解释器 `/home/ji/miniconda3/envs/sq312/bin/python`；跑**点名测试文件**，不跑裸 `pytest`（integration 全量约 18.5 分钟）。
- **测试先行**：每个行为变更先写失败测试、观察其按预期理由失败，再做最小修复（tests.md）。
- **联网一律需 owner 明确授权**：P1 探针、以及任何真实更新/真实发布都不例外（沿 2026-09-27 批量通道计划的 Task 10 惯例）。离线任务用 stub/fixture。
- **本期零新供应商、零新凭据**（附录 A 裁定）：不为 rqdatac/xtquant/tqsdk 预留 extra 或分支；tdx 仍只作公司行为仲裁器。
- **凭据红线**：token 只从环境读；不进代码、配置、日志、fixture、manifest、job request、前端响应、探针运维记录。
- **已发布版本不可变；门禁不得弱化**：被拒发布仍是记录在案的证据；不为让新表落地放宽 `STANDARDIZED_SCHEMAS`、跳过验收或伪造覆盖（§7.5）。
- **`pipeline_contract_version` 保持 `1`**：新形态走"加 key、旧形态兼容读取、处置方式是重发布"（§7.3）。
- **保护在途 WIP**：工作区当前有**已开工的 P2a 改动**（`src/stock_quant/data_model/fetch_coverage.py`、`src/stock_quant/research/runner.py`、`tests/unit/test_fetch_coverage.py`、`tests/unit/test_table_tier_preflight.py`、`tests/integration/test_table_tier_preflight.py`），以及既有的 `src/stock_quant/cli.py`、`src/stock_quant/reporting/html.py`、`templates/experiment.html.j2`、`RUNBOOK.md`、两份 spec、`tests/integration/test_cli.py`、`tests/integration/test_reports.py`——不覆盖、不回退、不暂存、不重排；各阶段计划只 `git add` 自己的文件。
- ADR 编号：`docs/adr/` 现有**两个 020**（batched-validation-channel 与 suspension-proof-grid，两者都已在索引中登记、未重编号）——这是既有冲突，追加 021/022/023 不受影响，但**新增 ADR 落笔前仍须复查 `docs/adr/DECISIONS_INDEX.md` 是否已被并行占用**，被占用则以下一个空闲编号替代（§5.2）。当前占用：**022 已由本计划的 ADR-022 占用**（accepted），021 归 P3 阶段计划 Task 1、023 归 P2b 阶段计划 Task 0（两者在本次核对时仍空闲）——按 (a) 裁定，这两份 ADR 的**成文时点在 G0**，任务卡留在各自阶段计划里。
- 每任务独立提交，提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；治理行宽：ADR ≤ 400 行；新增 `project/*.py` 必须同步 `project/SCRIPTS.md`。

## 两个必须先由 owner 裁定的决策（阻塞点）

1. **§7.0.10 提交协议二选一**（按 (a) 裁定**阻塞 G0**，进而阻塞 [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) **Task 0 与 Task 4**）：双替换 + prepare/commit/recovery 状态机，还是 Iceberg 式单指针（generation 文件为唯一权威指针，`CURRENT`/顶层定义降级为派生缓存）。两者不得混用；该计划的 ADR 草稿已把两份协议模板（A/B）就地写好，裁定后删除未选段即得终稿。**取裁定的时点已随 (a) 从 P2b 上移到 G0**——ADR-023 是 G0 的退出条件，协议不定稿则 G0 不闭合（见下表下方注）。
2. **P1 探针的联网授权**（阻塞 [panda-endpoints](2026-10-01-panda-endpoints.md) **Task 1 Step 2**）：授权范围（日期窗口、指数代码、请求数上限）成文后才可运行探针；该计划已把联网步骤单列，离线写作部分可先做。

## 开工前必须知道的实现形态（规格已核实的仓库事实）

1. 适配器当前**没有** `index_weight`/`daily_basic` 端点；`tushare_proxy` 具名端点集合只有 `daily`/`index_daily`/`stock_basic`（§1.3）。新端点是否加入具名集合由探针结果与 capability 校验决定，**不得手改校验默认值绕过**（§6.2）。
2. `index_weight` 是**月度快照**：一行一条"快照日 × 成分"，attested-boundary 约定（成员保持到最后一次列出它的快照前一日）；`announcement_date` = 快照日 = `raw_effective_from`（现有 766 行 lineage 100% 此形态）；池 id 必须 `custom_` 前缀（canonical id 的逐日基数校验无法被月度快照满足）（§1.3/§7.1）。
3. schema-v1 `UniverseDefinition` 在 `src/stock_quant/research/universe.py:99`（**不是** `research/models.py`），它的 `membership_table_sha256`（`:114`）钉**整表**哈希，逐位比较在 `:209`——在 schema-v2 切片哈希落地前写入第二个 `universe_id` 会让所有现有定义 hash mismatch（§7.0）。
4. `basic_factor` 只存 `total_mv`/`turnover_rate` 衍生列；`amount`/OHLCV 由 `daily_bar` 独有；不提供 Panda 十列宽表（§7.2）。单位换算（×10000/÷100 只是探针靶子）在探针实测前不得写死。
5. provenance 列（`ingested_at`/`collected_at`）**不进**身份哈希，首次落盘后固定、重放不更新（§2.2/§7.1/§7.2）。
6. `configs/universes/*.yml` 顶层文件是**完整定义**（装载器按完整定义解析，指针文件会阻断发布）；已有 `archive/` 目录先例（§7.0.4/9）。
7. 查询面全 GET、默认环回、非环回配置启动失败；操作面独立、默认禁用；调度用 systemd timer，不用 APScheduler（§8/§9）。锁 = `flock(2)` 语义（崩溃自动释放，无 stale lock）；孤儿判定 = `status.json` 心跳 pid + boot_id（§9.2，2026-10-01 增补）。
8. `UnTRUSTED` 过滤只许 `status == "UNTRUSTED"`，不得 `status != "VERIFIED"`（`VERIFIED_EMPTY` 是确认无事实，§7.3）。
9. **两个服务、两个端口、同一段前缀**（§8.1/§9.3；owner 2026-10-01 裁定）：只读面默认 `127.0.0.1:8321`、操作面默认 `127.0.0.1:8642`，默认都只绑环回；**两者都自挂 `/api/v1`**（只读 `datasets`/`experiments`/`health`，操作 `update-jobs`），路径段不重叠、前缀重叠。门户（P5）必须由**同一个源**转发两条前缀才不会撞单源策略——dev 用两条 proxy 规则且更具体的 `/api/v1/update-jobs` 排在兜底 `/api` 之前，生产用反向代理做同样的拆分；`STOCK_API_TARGET` 单个变量覆盖不了两个服务。形态见 [panda-web-portal](2026-10-01-panda-web-portal.md) pin I12。

## 文件结构（按批次）

> 每个批次的**权威文件清单在它自己的阶段计划里**（每份计划都有"文件结构"一节）。本节只做导航与要点提示，不复制清单——复制必然漂移。

**P0（全部文档）**：许可核验记录与 ADR-022 **已落盘并提交**（`docs/operations/2026-10-01-panda-license-verification.md`、`docs/adr/022-panda-graft-source-scope-and-basic-factor.md` 及索引行）。ADR-021 与 membership-hash ADR 的文件清单分别见 [panda-query-service](2026-10-01-panda-query-service.md) Task 1 与 [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) Task 0。

**P1**：见 [panda-endpoints](2026-10-01-panda-endpoints.md)「文件结构」。（要点：`project/probe_index_weight_daily_basic.py` 与探针 evidence 文件为新增；端点一律实现在 `data_sources/tushare.py`，`tushare_proxy.py` 的具名集合**默认零改动**、要改则独立提交；单位与指数代码常量冻结在新增的 `data_model/basic_factor_normalize.py`。）

**P2a（§7.5 覆盖模型）**：见 [panda-coverage-evidence-model](2026-10-01-panda-coverage-evidence-model.md)「文件结构」。（要点：`data_model/fetch_coverage.py` 词汇、分区校验与 `table_unsupported_window_tables`；`research/runner.py` preflight 窗口判定；`research/acceptance/checks.py` 每表必记录；**`data_model/dataset.py` 的 `DatasetPublisher.publish` 发布期注册表门禁**；`data_quality/models.py` + `gates.py` 新码登记；`data_pipeline.py` 源不可用 carry。**本批次已有在途改动**，动手前先核对工作区。）

**P2b（schema-v2）**：见 [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md)「文件结构」。（要点：定义层改 **`src/stock_quant/research/universe.py`**——**不是** `research/models.py`；新增 `data_model/membership_refresh.py`；注册表建在 `project/configs/universes/versions/`；crash-injection 测试为 `tests/integration/` 下的新文件。）

**P2c（basic_factor）**：见 [panda-basic-factor](2026-10-01-panda-basic-factor.md)「文件结构」。（要点：新增 `data_model/basic_factor.py` 承载纯函数；两表注册在 `data_model/schemas.py` + `dataset.py`；契约**只追加**进 `project/configs/sources.yml` 与 `templates/project-config/sources.yml`；`data_quality/models.py`+`gates.py`、`research/acceptance/models.py`+`checks.py` 各有新码与新检查。）

**P3**：见 [panda-query-service](2026-10-01-panda-query-service.md)「文件结构」。（要点：新包 `src/stock_quant/service/` = `app.py`/`datasets.py`/`experiments.py`/`tables.py`/`security.py`/`errors.py`/`__main__.py`；`pyproject.toml` 把 `duckdb` 移入核心依赖并新增 `service` extra；`tests/service/` 新建。）

**P4**：见 [panda-operations-scheduler](2026-10-01-panda-operations-scheduler.md)「文件结构」。（要点：新包 `src/stock_quant/operations/` = `update_lock.py`/`jobs.py`/`runner.py`/`api.py`/`serve.py`/`systemd_units.py`；`cli.py` 只做增量接线；`systemd/` 提交两个模板 unit；`RUNBOOK.md` 只追加新节。）

**P5**：见 [panda-web-portal](2026-10-01-panda-web-portal.md)「文件结构」。（要点：全部新增、都在 `web/` 内；组件测试在 `web/tests/`、E2E 在 `web/e2e/`——**不是**顶层 `tests/web/`。）

---

## 批次任务卡（历史序号锚点）

> **以下任务卡已被各批次的阶段计划取代**（见开头表格）：步骤、文件清单、命令与测试一律以阶段计划为准。任务卡保留两个用途——记录批次意图，以及为各阶段计划 Self-Review 里的"路线图 Task N–M"回指提供序号锚点。**路线图编号与阶段计划编号互不通用**，跨文件引用必须写全"文件名 + Task N"。
>
> 覆盖关系（非严格一一对应）：
>
> | 路线图任务卡 | 阶段计划任务 |
> | --- | --- |
> | Task 2 | [panda-query-service](2026-10-01-panda-query-service.md) Task 1 |
> | Task 4 | [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) Task 0 |
> | Task 6–9 | [panda-endpoints](2026-10-01-panda-endpoints.md) Task 1–4 |
> | Task 10–13 | [panda-coverage-evidence-model](2026-10-01-panda-coverage-evidence-model.md) Task 3–8（拆分方式与卡片不同） |
> | Task 14–18 | [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) Task 1–5 |
> | Task 19–21 | [panda-basic-factor](2026-10-01-panda-basic-factor.md) Task 1–5 |
> | Task 22–25 | [panda-query-service](2026-10-01-panda-query-service.md) Task 2–7（Task 1 是 ADR-021，卡片里没有） |
> | Task 26–29 | [panda-operations-scheduler](2026-10-01-panda-operations-scheduler.md) Task 1–6 |
> | Task 30–33 | [panda-web-portal](2026-10-01-panda-web-portal.md) Task 1–6 |

---

## Batch P0：决策、许可与架构正名（G0；纯文档，无代码）

> 许可证核验记录与 ADR-022 **已落盘并提交**（`3b2772446` / `873b2f77d`），执行细节见 [阶段计划 1](2026-10-01-panda-coverage-evidence-model.md) Task 1/2。余下两份 ADR（ADR-021、ADR-023）按"G0 与批次顺序的张力"的 **(a) 裁定属于本批交付**：它们是纯文档、无代码依赖，**执行时点就在 G0**，任务文本落在各自阶段计划里（ADR-021 → [panda-query-service](2026-10-01-panda-query-service.md) Task 1；ADR-023 → [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) Task 0）。下面的序号与意图仅作索引，**执行以阶段计划的任务卡为准**；两处阶段计划里的对应任务不再各自产出 ADR，只做引用与落地承接。

### Task 2：ADR-021（常驻查询面与调度正名）→ 在 G0 执行；任务文本见 [panda-query-service](2026-10-01-panda-query-service.md) Task 1

**Files:** Create `docs/adr/021-resident-query-surface-and-scheduler.md`；Modify `docs/adr/DECISIONS_INDEX.md`（只追加一行）

**Interfaces:** 产出：允许本地常驻只读查询面与调度触发器的决策；它们不拥有数据写语义；环回绑定、版本钉住（解析一次、回显完整哈希）、人工验收边界不可自动化（§5.2）。

- [ ] Step 1: 读 `docs/adr/009-*.md` 头部格式；正文覆盖：进程边界（查询面不 import DataPipeline、不持有 publisher、无 POST/PUT/PATCH/DELETE）、操作面默认禁用且只环回、调度只调 `operations update`、"no server"事实层的同变更更新义务（`docs/architecture/overview.md` 等，落在 P3/P4 首次合码时执行，本 ADR 写明该挂钩）。
- [ ] Step 2: 背景/后果里引用 Qlib PIT、ArcticDB `as_of` 版本读、Datasette 只读服务作为既有实践出处（不引入依赖）。
- [ ] Step 3: 追加索引行 + 治理测试 + 提交。

### Task 4：membership-hash ADR（schema-v2 切片哈希；G2 硬前置）→ 在 G0 执行；任务文本见 [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) Task 0

**Files:** Create `docs/adr/023-universe-membership-slice-hash.md`；Modify `DECISIONS_INDEX.md`

**Interfaces:** 产出：`membership_hash_scope: universe_id`、`coverage_segments`/gap（带 reason 与证据哈希）进入定义内容、`version` = canonical JSON SHA-256；schema-v1 整表语义保留用于重放；不可变定义版本注册表与重钉流程；mixed-lineage 迁移顺序（§7.0.5/6）；refresh 崩溃一致性协议——**owner 在 G0 裁定的那一种**（双替换状态机或 Iceberg 式单指针，spec §7.0.10，ADR 内删未选段）。

- [ ] Step 1: **向 owner 取 §7.0.10 的协议裁定**（双替换状态机 / Iceberg 式单指针二选一）。这是 G0 的输入而非 P2b 的开工门——未裁定时本 ADR 只有两套待选模板、无法定稿，**停在 G0**，也不得开工 P2b 的 refresh 任务。
- [ ] Step 2: 成文（删未选段）+ 追加索引 + 治理测试 + 提交。

---

## Batch P1：既有 Tushare 通道的端点扩展（G1）

### Task 6：探针脚本与结论成文（联网需授权）

**Files:** Create `project/probe_index_weight_daily_basic.py`（进 `project/SCRIPTS.md`）；Create `docs/operations/2026-10-<date>-endpoint-probe-evidence.md`
**Interfaces:** Consumes relay/共享 GET proxy 既有 transport。Produces dated 探针结论，冻结：CSI500/1000 代码族、快照节奏、历史窗口起点、`total_mv`/`turnover_rate` 原生单位与空值形态、**可得性时点**（T 日数据当日何时可查）、两 transport 差异与配额、失败形态（§6.4）。

- [ ] Step 1: 写脚本（离线可 `--dry-run` 渲染请求形状）；沿用 `probe_batch_channel.py` 的结构：最小窗口、无凭据落盘、只记参数形状与结果摘要。
- [ ] Step 2: **owner 授权后**联网跑探针；结论（含节奏是否可判定的明确判断：不稳定则记 `unknown`，§6.4）写入 dated evidence；单位换算假设若与 ×10000/÷100 不符，回写 spec §7.2。
- [ ] Step 3: 数量级断言素材（已知证券/日期的市值元、换手率比例）记入 evidence，供 Task 9 写测试。

### Task 7：`daily_basic` 端点

**Files:** Modify `src/stock_quant/data_sources/tushare.py`、`tushare_proxy.py`；Test `tests/unit/test_tushare_endpoints.py`（新）
**Interfaces:** Produces `daily_basic(trade_date)` 按日/按区间分页端点；`_NAMED_ENDPOINTS` 是否纳入由探针结论 + capability 校验决定；`validate_supplier_frame` 全覆盖（缺行/多余/重复主键/空响应/截断显式分类，空响应绝不当"无因子"）。

- [ ] Step 1: 失败测试先行：stub 供应商帧 → 正常转换；注入空响应、缺日期键、重复主键、截断、schema 漂移 → 稳定错误分类，且**不产生"全 0 市值"的成功结果**（§6.4）。
- [ ] Step 2: 单位换算按探针冻结值实现；缺失保持 null，禁止填 0（§7.2）。
- [ ] Step 3: 确认失败→实现→确认通过→点名邻居（`tests/unit/test_tushare_proxy*.py`）→提交。

### Task 8：`index_weight` 端点（提升既有脚本）

**Files:** Modify `src/stock_quant/data_sources/tushare.py`；Modify `project/collect_index_weight_membership.py`（降级为薄封装）；Test `tests/unit/test_tushare_endpoints.py`
**Interfaces:** Produces 按月分片的 `index_weight(index_code, start, end)` 端点；每份快照记 sha256；请求参数固定窗口词汇，不逐 symbol 打散（§6.2）。

- [ ] Step 1: 失败测试：月度分片形状、快照 sha 绑定、空响应≠"该日无成分"、指数代码来自探针冻结表（写死常量并注明来源，禁止数字前缀猜测）。
- [ ] Step 2: 实现后跑既有 lineage 重建回归（`tests/` 中 collect 脚本相关用例保持通过，§6.4 末条）→提交。

### Task 9：单位与量级断言测试

**Files:** Test `tests/unit/test_tushare_endpoints.py`（追加）
**Interfaces:** Consumes Task 6 evidence。

- [ ] Step 1: 以已知证券/日期写换算后市值（元）与换手率（比例）的量级断言；一律不得沿用 panda 的换算而不实测（§6.4）。

---

## Batch P2a：§7.5 覆盖证据模型扩展（纯离线，可与 P1 并行）

### Task 10：fetch-segment 词汇与前缀规则

**Files:** Modify `src/stock_quant/data_model/fetch_coverage.py`；Test `tests/unit/test_fetch_coverage.py`
**Interfaces:** Produces `history_begins_after_anchor`（唯一前缀：`acceptance_start` → `supported_start - 1`，不得出现在中间/尾部，与 calendar/contract 窗口对齐）、`source_disabled`、`source_unavailable`（fetch-segment 词汇，与 coverage 表的 `SOURCE_FETCH_FAILED` 是两套，不得互相顶替，§7.5.1/2）。

- [ ] Step 1: 失败测试：中间/尾部的 `history_begins_after_anchor` 被拒；混入 `source_unavailable` 的段被拒；纯前缀 + 对齐窗口通过；`operator_explicit_window` 既有语义不变。
- [ ] Step 2: 同一改动里**显式改写** `not_fetched_mixed_with_fetch`（只放行"唯一前缀"这一形态），不顺带放宽；失败测试先行。

### Task 11：preflight 窗口判定

**Files:** Modify `src/stock_quant/research/acceptance/checks.py`；Test `tests/unit/test_acceptance_checks.py`、`tests/integration/test_table_tier_preflight.py`
**Interfaces:** Produces 稳定错误码 `table_history_start_after_window`：RESEARCH run 窗口早于表支持起点或落入未支持段 → fail closed；窗口完全在支持内 → 放行；ENGINEERING 保持豁免并标注（§7.5.3）。

- [ ] Step 1: 失败测试（先观察在错误理由上失败）→ 实现 → 通过 → 提交。

### Task 12：源不可用时的 baseline 语义

**Files:** Modify `src/stock_quant/data_pipeline.py`；Test `tests/integration/test_data_pipeline.py`
**Interfaces:** Consumes `_read_baseline`（已具备 carry 能力）。Produces：首次发布且无 baseline 才允许空 canonical frame（沿用 quarantine 空表先例）；已有历史必须 carry 已有事实、只把新尾段标不可用，或整轮阻断；绝不静默清表（§7.5.4）。

- [ ] Step 1: 失败测试：有 baseline 时源不可用 → 旧事实仍在 + 新尾段 `source_unavailable`；无 baseline 首次 → 空表可发布；清空已有表 → 该轮失败。

### Task 13：每表必记录 + manifest 自身表集合判定

**Files:** Modify `src/stock_quant/research/acceptance/checks.py`、`src/stock_quant/data_model/dataset.py`；Test 上述两文件
**Interfaces:** Produces：`table_fetch_coverage_evidence` 要求**每张已发布表**有记录（堵"不记录即通过"）；`required_table_coverage` 按 manifest 自身的表集合判定，"当前注册表齐全"移到发布期门禁；无 `table_lineage` 的旧 manifest 按旧形态兼容读取，**不升** `pipeline_contract_version`（§7.3/§7.5.5/6）。

- [ ] Step 1: 失败测试：已发布表漏记覆盖证据 → 失败；注册新表后旧 dataset 版本复审不缺表失败；旧 manifest（无新 key）兼容读取。

---

## Batch P2b：schema-v2 切片哈希与 membership refresh（G2 硬前置）

### Task 14：`UniverseDefinition` schema-v2

**Files:** Modify `src/stock_quant/research/universe.py`（**不是** `research/models.py`）；Test `tests/unit/test_membership_definition_v2.py`（新，命名以 [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) 为准）
**Interfaces:** Produces：`membership_hash_scope: universe_id`、`coverage_segments` + gap 列表（每段带 reason 与证据哈希）、`version` = canonical JSON SHA-256；段/gap 互补性与包络校验（`coverage_start/end` = 段包络；段重叠、事实落段外、gap 与段不互补 → 拒绝加载）；v1 整表语义原样保留、无隐式 fallback（§7.0.1/3）。

- [ ] Step 1: 失败测试覆盖：两 `universe_id` 同表各自通过 slice hash；v1 按 v1 重放；坏定义拒绝加载。
- [ ] Step 2: 实现 → 通过 → 提交。

### Task 15：验收与 runner 的切片化

**Files:** Modify `src/stock_quant/research/acceptance/checks.py`（`evaluate_index_membership_evidence`）、`src/stock_quant/research/runner.py`、`src/stock_quant/research/universe.py`；Test 同上 + `tests/integration/test_research_runner.py`
**Interfaces:** Produces：先选 slice 再对 slice 做 schema/事实/coverage/cardinality/hash 检查；空 slice 明确失败；runner 传给 `_facts_from_membership_frame`/`resolve_memberships`/`UniverseResolver` 的是同一 slice；`data update`/`data validate` 仍对**整表**跑 schema 与事实校验（§7.0.2）。

- [ ] Step 1: 失败测试：注入"按 slice 验收、按整表运行"的分叉形态必须被拒。

### Task 16：定义版本注册表与 v1 归档

**Files:** Create `project/configs/universes/versions/`（注册表，含首个 v2 定义）；Modify 装载器（`research/registry.py` 或 universe 装载处——以实际为准）；Test 同上
**Interfaces:** Produces：显式 `universe_version` 从注册表解析并复核内容哈希；`CURRENT` 规格冻结到新 v2 version；顶层 `*.yml` 仍是完整定义、非递归扫描、`universe_id` 不得重复；无 slice 的启用定义不计算空 slice 哈希冒充 v2（保持 v1 并移入 `archive/`，只许在剩余定义最小 `coverage_start` 不变时进行，§7.0.4/5/6/9）。

### Task 17：membership refresh 与崩溃一致性

**Files:** Modify `src/stock_quant/cli.py`（`data index-membership publish` / `recover`）；Create `src/stock_quant/data_model/membership_refresh.py`（或按实际归位）；Test `tests/integration/test_membership_refresh.py`（新）
**Interfaces:** Produces：prepare（新 dataset + 新定义内容，均不提升；注册表先追加）→ commit（按 owner 裁定的协议：双 `os.replace` 或单 generation 指针）→ recovery（`data/.membership_generation.json` 一致性校验，不一致**不猜**、报稳定错误码、`recover --to <generation>` 由 operator 显式选择）；同次 refresh 完成定义重钉（§7.0.7/8/10）。

- [ ] Step 1: 失败测试：prepare 后中断、`CURRENT` 替换后中断、definition 替换后中断（或单指针下的派生缓存落后）三种注入各收敛到自洽 generation，期间无可被误用的中间态；重跑幂等；回滚 = 文件替换。
- [ ] Step 2: RUNBOOK 恢复步骤随本任务写入。

### Task 18：attested-boundary 事实生成与差异报告

**Files:** Modify `src/stock_quant/data_model/index_membership_import.py`（或新模块）；Test 单元层
**Interfaces:** Produces：快照序列 → `MembershipFact`（`raw_effective_from/to`、`announcement_date` = 快照日、`snapshot_observed_change`/`initial_constituent` reason、`collected_at` provenance 不进哈希且首落固定）；漏采 → `membership_observation_gap` 证据 + 中断连续 coverage + 下次成功观测只开新段（不猜边界）；节奏 `unknown` 时必须人工确认；与既有 CSI300 官方事实重叠只出差异报告，不自动选边/覆盖（§7.1）。

- [ ] Step 1: 失败测试：注入漏采后 `coverage_segments` 反映中断、跨 gap 窗口以 `universe_gap_in_window` 失败、无任何精确加入/移除边界；`collected_at ≠ announcement_date`；补采不更新已落盘 `collected_at`。

---

## Batch P2c：`basic_factor` 全链路（G2 出口）

### Task 19：schema 注册与 data_contracts

**Files:** Modify `src/stock_quant/data_model/schemas.py`、`dataset.py`（装配/fixture/manifest/validate）；Modify `project/configs/sources.yml` + `templates/project-config/sources.yml`（**只追加**两表契约）；Test `tests/unit/test_basic_factor.py`
**Interfaces:** Produces：`basic_factor`（PK `(trade_date, symbol)`，列见 §7.2 表；业务列进哈希、`ingested_at` 不进）与 `basic_factor_coverage`（`core`、`coverage_shape=none`、词汇复用 `corporate_action_coverage` 的 `CoverageStatus/Reason`）注册；契约：`research_only`、`tushare:relay`（探针若示直连胜出则写实际值并回注 ADR-022）、`incremental=last_covered_plus_1`、`conflict=block`、`coverage_shape=per_symbol_window`；`VERIFIED_EMPTY` 必须有上市/退市证据支撑（§7.3）。

- [ ] Step 1: 失败测试：两表契约解析、schema/主键/有限非负市值/有限非负换手率/日历内日期/security master 符号校验。

### Task 20：normalize 与联接一致性

**Files:** Modify `src/stock_quant/data_pipeline.py`、normalize 模块；Test `tests/integration/test_basic_factor_publish.py`（新）
**Interfaces:** Produces：`daily_basic` → `basic_factor` 行（null 保持 null）；`daily_bar` × `basic_factor` 按 `(trade_date, symbol)` 一对一：任一侧重复主键、联接扩行 → 质量错误；行集合差异必须落在 `basic_factor_coverage`（§7.2）。

- [ ] Step 1: 失败测试（重复主键/扩行/差异未落 coverage 三形态都必须让该轮失败，不被 left join 吸收）→ 实现 → 真实小窗口 raw → 新 dataset version → `data validate` 通过 → 相同输入重跑哈希不变、单源事实变化出新版本且旧版本不动（§7.4）。

### Task 21：`table_lineage` 与发布期核对

**Files:** Modify `src/stock_quant/data_model/dataset.py`、`data_pipeline.py`；Test 同上
**Interfaces:** Produces：`build_config.table_lineage`（table → transport → raw snapshot 证据行）；发布时与 `primary_transport` 声明核对；`coverage_shape` 要求 coverage 表；`coverage_downgraded` 写入点落地；旧 manifest 兼容读取（§7.3）。

- [ ] Step 1: 失败测试：lineage 与声明不符 → 失败；缺 coverage 表或与事实表不一致 → 整轮阻断。

---

## Batch P3：只读查询面（G3；可与 P1/P2 并行，只依赖 P0）

> 阶段细案已成文：[panda-query-service](2026-10-01-panda-query-service.md)。其 Task 1 是 ADR-021 的任务卡，但按 (a) 裁定**该 ADR 在 G0 成文**，本批的 Task 1 降级为引用与落地承接（不再产出 ADR）；Task 5 冻结 OpenAPI 契约，**G4/G5 依赖的公共契约要到 Task 5 之后才算冻结**。

### Task 22：service 骨架与数据集端点

**Files:** Create `src/stock_quant/service/`（`app.py`/`datasets.py`）；Modify `pyproject.toml`（`duckdb` 入核心依赖；`service` extra：fastapi/uvicorn）；Test `tests/service/`
**Interfaces:** Produces：`GET /api/v1/health|datasets|datasets/{version}|datasets/{version}/quality|experiments|experiments/{id}/report`；`{version}` 接受完整哈希或 `current`（入口解析一次，回显完整哈希 + 本次请求解析参数）；acceptance 摘要四态且"存在有效 accepted record"与"最近 verdict"分开；非环回配置启动失败；不 import `DataPipeline`、无写方法（§8）。

- [ ] Step 1: 失败测试（OpenAPI 契约、版本回显、404/422、非环回拒绝）→ 实现 → 提交。

### Task 23：表预览端点与查询安全

**Files:** Create `src/stock_quant/service/tables.py`、`security.py`；Test 追加
**Interfaces:** Produces：`GET /datasets/{version}/tables/{table}`：表来自 manifest、列来自 schema 白名单、值过滤参数绑定、`limit` 默认 100/最大 500、**查询时间预算**（默认量级 5s，超时稳定错误码 `query_time_budget_exceeded`）、每请求独立开/关只读 context（§8.2）。

- [ ] Step 1: 失败测试：路径穿越、SQL 注入、超大 limit、未知表/列、损坏 manifest 全部失败关闭。

### Task 24：并发读与半发布不可见

**Files:** Test `tests/service/test_concurrency.py`
**Interfaces:** 验证 §8.3：多 reader 并发读不可变版本 + 一路 `data update` 同时进行时，读方只见旧完整版本或解析后的新完整版本。

### Task 25：事实文档同步（服务面）

**Files:** Modify `docs/architecture/overview.md`、`module-map.md`、`data-flow.md`（§5.3：同一变更更新三份事实层）。

---

## Batch P4：操作面、单飞锁与调度器（G4）

### Task 26：`operations update` 与 UpdateRunner

**Files:** Create `src/stock_quant/operations/runner.py`；Modify `src/stock_quant/cli.py`（`operations update` 入口 + `data update` 冲突信号：专用非零退出码或固定 token `update_already_running`）；Test 单元 + `tests/integration/test_cli.py`
**Interfaces:** Produces：以参数数组启动 `python -m stock_quant data update --root ...`（无 shell 拼接；允许参数仅 start/end/sources/disclosure-lookback-days，按 CLI 同型约束校验）；project-local `flock` 锁（`data/.locks/update.lock`，崩溃内核自动释放）（§9.1/9.2）。

- [ ] Step 1: 失败测试：锁冲突 → 稳定冲突码 → job 记 FAILED/`update_already_running`（不得匹配自由文本）。

### Task 27：job 持久化与孤儿检测

**Files:** Create `src/stock_quant/operations/jobs.py`；Test 追加
**Interfaces:** Produces：`data/service/jobs/<job_id>/`（不可变 `request.json`、追加式 stdout/stderr log、原子替换 `status.json`）；状态词汇 `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED_BY_SHUTDOWN`；心跳 pid + timestamp + boot_id 原子写入，阈值（量级 5 分钟）判孤儿 → FAILED/`orphaned_process`，不删目录；job id 不进 dataset version（§9.2）。

### Task 28：操作 API

**Files:** Create `src/stock_quant/operations/api.py`；Test `tests/service/`
**Interfaces:** Produces：独立 router、默认禁用、启用仍只环回：`POST /api/v1/update-jobs`（409 与 CLI 同一冲突码）、`GET /api/v1/update-jobs[/{id}]`（脱敏日志尾部）；不提供取消/重试到成功/删除/验收/研究端点（§9.3）。

### Task 29：systemd timer 与 RUNBOOK

**Files:** Create `systemd/stock-quant-data-update@.service`、`...@.timer`；Modify `RUNBOOK.md`
**Interfaces:** Produces：timer 只调 `operations update`；固定 unit 名 `stock-quant-data-update@<project-id>.timer`；宿主 `Asia/Shanghai`；`Persistent=false`（停机错过不补跑风暴）；冲突 → journal 可见（§9.4）。RUNBOOK 增加启动/停止、锁冲突、失败检查、membership recover 步骤（§5.3）。

- [ ] Step 1: unit 渲染的单元测试（不真的安装）；一次计划触发的真实验证需 owner 授权，单列一步。

---

## Batch P5：Vue 数据门户（G5）

> 阶段细案已成文：[panda-web-portal](2026-10-01-panda-web-portal.md)。页面地图、更新流程四终态、结果展示流程与"不在 Web 里的动作"以 spec §10.1–10.4 为验收原文；该计划的「P5 消费契约 pin」一节即对 §8.2/§9.3 端点的绑定解释，**已逐字段对齐 [panda-query-service](2026-10-01-panda-query-service.md)／[panda-operations-scheduler](2026-10-01-panda-operations-scheduler.md) 冻结的响应模型**；P3/P4 代码落地后仍须以运行中的实际响应复核（前端是消费者，差异改 `web/src/api/types.ts`）。

### Task 30：前端骨架与版本面板

**Files:** Create `web/`（Vue 3 + Vite + 测试）；不复制 Panda dist 的任何 CSS/组件/文案/图片（§10.2）。
**Interfaces:** Produces：共享顶栏（project 指纹 + 当前解析版本哈希 + CURRENT 标记）、版本面板（表计数/质量摘要/验收四态分栏显示）。

### Task 31：数据预览与质量/覆盖证据页

**Interfaces:** Produces：钉版本分页（翻页携带同一 resolved version；CURRENT 变化只提示不自动切换）；UNTRUSTED 行、coverage gap、attested-boundary 滞后上界可见。

### Task 32：更新任务页与报告页

**Interfaces:** Produces：允许参数提交 → 轮询 job（脱敏日志尾部）→ 四终态展示（SUCCEEDED 显完整哈希并链接版本详情；FAILED 显稳定错误码；409 链接运行中 job；操作面关闭只读 + 等价 CLI 命令）；报告页链接既有静态 HTML。

### Task 33：浏览器端测试与 E2E

**Interfaces:** 验证 §10.4 全部断言：版本切换提示、UNVERIFIED 展示、分页保持版本、更新 409、失败日志、报告不存在、操作面关闭、四终态断言；E2E 从触发更新到看到新版本，且**不**生成 acceptance PASS、不自动运行研究（§14）。

---

## 阶段门核对表（与 §13 对齐）

| 批次完成 | 对应门 | 出口核对 |
| --- | --- | --- |
| P0 | G0 | 许可记录 ✅ + ADR-022 ✅；**ADR-021 与 ADR-023 按 (a) 裁定属本批纯文档交付，尚未成文，故 G0 尚未闭合**（见下表下方注）。ADR-023 的成文以 §7.0.10 协议裁定为前置输入——该裁定须在 G0 内完成，不再下推到 P2b |
| P1 | G1 | 探针结论成文（含节奏可判定性与可得性时点）；端点合同回归测试通过；lineage 重建回归保持绿 |
| P2a+b+c | G2 | §7.4 全部完成条件逐条核对（含崩溃注入、gap 复现、重钉、联接一对一失败测试、旧版本复审不缺表） |
| P3 | G3 | GET API、安全与并发读验证通过；事实文档三件套同步 |
| P4 | G4 | 单飞锁、持久 job、一次计划触发通过（真实验证需授权） |
| P5 | G5 | 四页 E2E + §10.3 功能流程 + §10.4 全部断言 |

> **G0 与批次顺序的张力（owner 已裁定，2026-10-01）**：spec §13 把三份 ADR 都列进 G0 的退出条件，而 G3 的可开始条件又是 G0；若 ADR 由它所属批次的阶段计划产出，就成立"要开 P3，必须先有 ADR-021；而 ADR-021 由 P3 的第一个任务产出"的循环。曾列出的两种收敛方式：
>
> - **(a) 把 ADR 提到 G0 单独成文**（纯文档、无代码依赖，阶段计划里的对应任务随之降级为引用）。代价最小，不动 spec。
> - **(b) 修订 spec §13 的门表**：ADR-021 的出口移到 G3、ADR-023 移到 G2，G0 只留许可记录 + ADR-022。这改的是验收定义，须按"先改规格再改代码"的规矩带修订记录。
>
> **裁定：(a)。** 采用时对原提案作一处必要的扩大：§13 的 G0 退出条件点名的**三份 ADR**里，除 ADR-021 外还有 membership-hash ADR（ADR-023）——它原先挂在 P2b 阶段计划的 Task 0，同属 G2 批次，与 ADR-021 构成同一循环。只把 ADR-021 提到 G0 会让循环在 ADR-023 上半途保留，因此**两份都提到 G0 单独成文**。执行口径：
>
> - ADR-021 与 ADR-023 的**成文动作发生在 G0/P0 批内**，均为纯文档交付，不依赖任何代码。任务卡与成文模板见 [panda-query-service](2026-10-01-panda-query-service.md) Task 1 与 [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) Task 0——这两处保留完整任务文本，但**执行时点从各自批次前移到 G0**。
> - [panda-query-service](2026-10-01-panda-query-service.md) Task 1 与 [panda-membership-slice-hash](2026-10-01-panda-membership-slice-hash.md) Task 0（两者是**阶段计划**内的编号，与本文件的 Task 14+ 不是一套）因此**降级为引用**：它们不再产出 ADR，只在自己批次开工时核对 ADR 已成文、编号未冲突，并承接 ADR 里写明的落地义务（ADR-021 的"no server"事实层同变更更新义务；ADR-023 的 `COMMIT_PROTOCOL` 单值）。
> - spec §13 的门表**不改**，G0 的退出条件仍是原来那三份 ADR——(a) 正是让实现去对齐规格，而不是反过来。
> - **ADR-023 的成文叠加 §7.0.10 协议裁定这个前置**（双替换状态机 / Iceberg 式单指针二选一，ADR 内删未选段）。该裁定原先被写成 P2b 的开工门；在 (a) 之下它**上移为 G0 的输入**：裁定未下时 ADR-023 依 spec §7.0.10 只有两套待选模板、无法定稿，G0 必然挂起。这是既有的设计阻塞，不是本表的缺陷，但它现在落在 G0 的关键路径上，不能再当作后续批次的事。

## 完成定义

以 spec §14 的 10 条为准逐条核对；任一条不满足即本计划未完成。规格与实现漂移时，先改规格（带修订记录）再改代码。
