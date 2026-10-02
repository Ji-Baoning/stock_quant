# Panda 数据闭环能力嫁接 Stock · 架构设计

- 日期：2026-09-29
- 最近修订：2026-09-30（二轮审阅后新增 §7.5 新表覆盖证据硬前置；同日按 owner 裁定
  收紧数据源范围：RiceQuant 账号/许可与 pandaAI 其余数据源均不可得，本期只用 Stock
  既有数据源，RiceQuant 等源的再进入条件移入附录 B；裁定不提供 Panda 兼容宽表格式，
  只保留因子—行情联接一致性检查，理由见 §7.2；同日按第三轮审阅修正四处以既有代码
  为依据的硬约束：`required_table_coverage` 改按 manifest 自身表集合判定且不升
  `pipeline_contract_version`（§7.3/§7.5.6）、membership refresh 增加 prepare/commit/
  recovery 协议（§7.0.10）、`not_fetched` 前缀约束与源不可用词汇（§7.5.1/2）、
  `basic_factor_coverage` 的 `VERIFIED_EMPTY` 语义收紧（§7.3）；2026-10-01 按第四轮
  外部参考审阅吸收成熟实践：provenance 列不进版本哈希（§2.2/§7.1/§7.2）、`daily_basic`
  可得性时点改为探针门禁（§6.4/§7.2）、flock 锁语义与孤儿判定机制（§9.2）、表查询
  时间预算与请求参数回显（§8.2）、§7.0.10 记录 Iceberg 式单指针备选、§10 扩充 Web
  功能设计（更新/展示/使用流程））
- 状态：**待 owner 复核**
- 上游分析：`/home/ji/work/program/pandaAI/docs/panda数据闭环迁移stock可行性分析.md`
- 数据源范围（owner 裁定 2026-09-30）：**不引入任何新供应商**。本期全部取数只走
  Stock 既有通道——Tushare（relay / 官方 transport）、akshare、星耀数智，tdx 仍只作
  公司行为仲裁器。任何需要新账号、新许可或额外付费的源都不在本期范围内；附录 B 记录
  它们的再进入条件，避免把该判断当成永久结论。
- 目标：在不引入第二套数据真相、不削弱 Stock 治理边界的前提下，吸收 Panda 的定时
  更新能力、数据管理交互模式，以及**可在既有源上兑现**的字段语义与成分事实路径。
- 交付形态：本文件是跨阶段的总规格。每个实施阶段必须有独立计划、独立验收和
  可运行交付物；不得把六个阶段合成一次大改。

## 0. 决策摘要

采用**嫁接**，不采用代码或平台整体搬运。

```text
Tushare 端点扩展 ──┐
PIT 成分事实 ──────┼─> Stock 原始证据 → 规范化 → 质量门禁 → 内容寻址发布
基础因子字段语义 ──┘                                  │
                                                       v
                              DatasetReader / DuckDB 只读版本查询
                                                       │
                          ┌────────────────────────────┴────────────┐
                          v                                         v
                    FastAPI 查询面                         本机操作面 / 调度器
                    （只读、钉版本）                       （仅触发 data update）
                          │                                         │
                          └────────────────┬────────────────────────┘
                                           v
                                      Vue 数据门户
```

核心裁决：

1. Stock 的 Parquet/DuckDB、原始证据、质量门禁、内容寻址发布和人工验收链是
   唯一数据底座；不引入 MongoDB，不实现双写。
2. Panda 代码不进入 Stock。只吸收端点、字段和单位等事实性知识，所有实现均在
   Stock 的接口和测试约束下 clean-room 重写。
3. **本期不引入新供应商**（owner 裁定 2026-09-30）。多指数成分走既有 Tushare
   `index_weight`（relay 与共享 GET proxy 都能透传，csi300 lineage 已有先例），
   市值/换手率走既有 `daily_basic`；二者都实现为 Stock 通道内的端点扩展，而不是新
   数据源注册。RiceQuant、xtquant、tqsdk 等 pandaAI 源在本期不可得，不进入实现；
   再进入条件见附录 B。既有 CSI 官方成分事实不被自动覆盖。
4. `basic_factor` 只存 Stock 尚无唯一事实来源的字段。OHLCV 和 `amount` 继续只由
   `daily_bar` 提供；不提供 Panda 的十列宽表格式（§7.2），需要时由消费方自行联接转换。
5. `universe_membership` 在进入多指数并存前升级为按 `universe_id` 切片钉哈希；旧的
   schema-v1 整表哈希语义保持可重放，不静默改判。
6. 常驻查询面全部为 GET。更新触发属于独立操作面，默认关闭且只允许环回地址；
   查询面和调度器都不能直接写数据集。
7. 自动化止于“发布一个通过门禁的新数据集版本”。人工验收、正式研究解锁和失败
   证据清理永不自动化。
8. 新表（如 `basic_factor`）只有在覆盖证据、切片哈希和作用域都能在既有门禁下显式
   表达时才发布；不得为让新表落地而放宽、跳过或临时改写既有覆盖门（见 §7.5）。
9. 多指数成分沿用 `index_weight` 的**月度快照**语义：池 id 用 `custom_` 前缀
   （canonical id 的逐日基数校验无法被月度快照满足）、“attested-boundary”约定，并把
   “最多约一个月滞后 + `announcement_date` 取快照边界”作为成文近似写进
   `rules_version` 与证据，不得声称为官方公告（§7.1）。

## 1. 背景与已经核实的仓库事实

### 1.1 Stock 当前边界

- `DataSource` 正式协议只有 `name` 和 `fetch(request) -> FetchResult`；ADR-020 另以
  可选鸭子类型定义 `fetch_batch`。运行时源注册、必选角色和 lane 装配仍由
  `data_pipeline.py` 的封闭结构控制。
- `data update` 已是幂等入口；取数失败或质量门禁拒绝时不改 `CURRENT`。
- `DatasetPublisher` 是唯一发布器。版本由表记录和规范化 `build_config` 共同哈希，
  发布目录不可变，`CURRENT` 原子替换。
- `DatasetReader.open(version)` 只读打开指定版本；正式消费者在运行开始后不得继续
  跟随 `CURRENT`。
- `configs/sources.yml:data_contracts` 对每张 `data_update` 发布表强制声明 tier、
  transport、冲突语义、PIT、coverage 和增量策略；未声明表以
  `unregistered_table` 失败关闭。
- `universe_membership` 已有不可变事实模型、快照/文档哈希和研究前置门禁，不需要
  另造指数成分字符串列；但 schema-v1 的定义钉住整张表哈希，当前一次只能让与整表
  完全一致的一条 lineage 通过研究门禁，多 `universe_id` 并存必须先升级哈希作用域。
- 正式研究验收包含 10 项自动检查和 9 项人工检查。9 项人工结果必须由 operator
  签署并绑定证据；调度进程无权生成 PASS。
- `overview.md` 的 “no server, no scheduler, no database” 是当前架构事实，而非
  已采纳 ADR 的永久禁令。引入服务与调度必须用 ADR 正名，并在同一变更更新事实层。

### 1.2 Panda 可吸收与不可吸收的部分

| Panda 能力 | 本设计处理 |
| --- | --- |
| Tushare 因子线的字段选择与单位换算（`daily_basic` 的 `total_mv`/`turnover_rate`） | 作为行为输入，按 Stock 契约 clean-room 重写；单位换算的**唯一效力来源是探针实测**，供应商文档与 panda 代码只提供待验证假设 |
| 指数成分的“日期化成分事实”思路 | 沿用 Stock 既有 `index_weight` 路线产出候选 `universe_membership` 事实，不塞回行情行 |
| `factor_base` 的市值、换手率表设计 | 只借其字段来源，落为去重后的 `basic_factor` canonical 表；不沿用其列布局或单位口径（§7.2） |
| RiceQuant / xtquant / tqsdk 三路源 | 本期不可得，不实现；端点与字段知识留作附录 B 的再进入材料 |
| APScheduler 的“到点触发”思想 | 重写为只启动 Stock CLI 的调度外壳 |
| 数据源配置、回补、进度、统计的交互模式 | 用新 API 和新 Vue 源码重建 |
| MongoDB、upsert、全局连接单例 | 不迁移 |
| `panda_data` Mongo 读取 SDK | 不迁移，由 `DatasetReader` 替代 |
| 混淆后的 Vite dist | 不复制、不反编译复用，只保留功能需求 |
| xtquant、分钟/tick、panda_factor、AI 助手 | 本轮明确排除 |

### 1.3 一手核验结论

本规格复核了 pandaAI 的 cleaner、工具函数、因子 cleaner、调度器，以及 Stock 自己的
`index_weight` 采集脚本，而非只依赖上游报告。

**Stock 侧（本期实现的直接依据）：**

- `index_weight` 是官方成分权重**快照**接口，一行一条“快照日 × 成分”，实际节奏为
  **月度**；relay 与共享 GET proxy 都能透传（2026-09-13 权限核实），现有
  `custom_csi300_tw_tradable` lineage（766 行、source `tushare_index_weight`、
  `raw_effective_from` 2014-12-01 起）就是这条路线产出的。
- 该路线使用 “attested-boundary” 约定：成员保持到最后一次仍被列出的快照的前一日，
  新进成分在其首次出现快照日生效。月度快照因此给月中调整带来**最多约一个月的滞后**，
  属已记录近似而非精确生效日。
- 该 lineage 的行内 `announcement_date` **全部等于** `raw_effective_from`（766/766），
  即“首次被观测到 = 生效”，这是快照边界约定的一部分，不是官方公告日。
- 现有 lineage 用 `custom_` 池 id，而不是 canonical `csi300`：canonical id 要求每个
  交易日恰好 300 名成员，月度快照的滞后无法满足该基数校验。
- 适配器当前**没有**实现 `index_weight` 与 `daily_basic` 端点：两者仍由
  `project/collect_index_weight_membership.py` 这类离线脚本直连 transport 完成，
  `tushare_proxy` 的具名端点集合只有 `daily`/`index_daily`/`stock_basic`。

**pandaAI 侧（本期只作字段与行为参考；其数据源不可得，见附录 B）：**

- panda 的因子线本就是 Tushare：`daily_basic(trade_date, fields=['ts_code',
  'turnover_rate','total_mv'])`，把 `total_mv` 记为 `market_cap`、`turnover_rate` 记为
  `turnover`，并对 `market_cap` 乘 10,000（万元→元）、对 `amount` 乘 1,000（千元→元，
  取自 `daily`）。字段语义与本期要用的端点一致，单位换算须以供应商文档确认并实测。
- panda 的三条因子路径**输入格式不一致**，`factor_base` 只是“同名列”而非同一契约：
  Tushare 路径对缺失行是 left merge（保留 NaN），RiceQuant 路径显式 `fillna(0)`，
  xtquant 路径 `market_cap = close × TotalVolume` 属另一口径；三者都把
  `turnover_rate` 改名 `turnover` 且维持**百分数**（Tushare 不除以 100，xtquant 自行
  ×100），而 Stock 的 `turnover_rate` 是**比例**。因此本设计既不复制 OHLCV 成宽表，
  也不承诺复现这套列契约（§7.2）；两个项目今天互不消费对方的格式。
- panda 所谓的多源是 `DATAHUBSOURCE` 单选，不存在多源并发验证或投票；RiceQuant
  成分请求失败时异常分支写入无关的 `self.components`，成员集合仍为 `None`，随后全部
  证券被标成 `000` 并继续 upsert——这是本规格禁止把空响应当成功的直接反例。
- RiceQuant `index_components` 只有“日期 → 当日成员集合”，无公告日/发布日元数据；
  panda 的代码转换按数字前缀推断市场、未知值返回 `UNKNOWN` 继续流转。
- panda scheduler 使用日期化 job id、`replace_existing=True`，失败只记日志，没有
  跨进程单飞或持久任务状态；APScheduler 3.x 的单进程默认 `max_instances=1` 不能解决
  多进程/重启边界。Stock 不继承这些语义。

## 2. 目标、成功判据与非目标

### 2.1 目标

1. 既有 Tushare 通道内的两个新端点（`index_weight` 多指数、`daily_basic`）能以可审计、
   可复现、凭据不落盘的方式进入 Stock 原始证据链。
2. CSI500/1000 的成分事实可生成证据绑定的候选事实与 `universe_membership` 切片；其
   月度快照的滞后与可得性近似必须成文标注，不得冒充官方公告或精确生效日。
3. `basic_factor` 作为新 canonical 表发布，且不会复制 `daily_bar` 已拥有的事实。
4. 用户能从 Web 查看版本、表、质量报告、验收状态和已生成报告，所有数据展示都
   明确绑定版本哈希。
5. 用户能在本机操作面触发一次更新并查看持久化状态/日志；调度器能以相同语义
   定时触发更新。
6. 新能力不改变正式研究的人工验收前置条件。

### 2.2 总体验收判据

- 同一组原始响应重复导入，产生相同表内容和相同 dataset version。“相同表内容”指
  业务列；`ingested_at`/`collected_at` 等 provenance 列不进入身份哈希，重放同一
  原始响应也不更新已落盘的采集时点（稳定性规则见 §7.1/§7.2）。
- 源不可用、超时、返回残缺集合或 schema 漂移时，新版本不伪装成功，`CURRENT`
  保持不变，失败证据可追踪。
- Web 响应包含 `dataset_version`；一次请求解析版本后不再读 `CURRENT`。
- 更新并发请求只有一个能进入执行态，其他请求明确返回 conflict，不产生双发布。
- 调度器成功只能产生新数据集候选；没有任何路径能自动写人工 PASS、
  `CURRENT_ACCEPTED` 或启动正式 `research run`。
- 发布后的既有版本目录在服务运行、更新触发和失败恢复后字节不变。

### 2.3 非目标

- 不引入需要新账号、新许可或额外付费的数据源（RiceQuant、xtquant、tqsdk 等）。这是
  2026-09-30 的范围裁定，不是对它们价值的判断；再进入条件见附录 B。
- 不兼容 Panda 的 Mongo collection 或 `panda_data` Python API。
- 不实现通用任意 SQL HTTP 端点。
- 不做远程多租户、用户系统、权限管理或公网部署。
- 不在 UI 中编辑因子、配置、验收结论或研究规格。
- 不接入 xtquant 或其他需要本地客户端/额外凭据的通道，不引入分钟/tick，不迁移 Panda
  因子引擎或 AI 功能。
- 不借本项目重构整个 `data_pipeline.py`；仅提取新 lane 所需的窄接口。
- 不回算、改写或重新解释已发布数据集、验收记录、schema-v1 universe definition 或
  实验的身份；schema-v2 只为新 definition 产生新身份。
- 不为让新表或新指数落地而放宽、跳过或临时改写既有覆盖/验收门；覆盖语义的扩展必须
  是显式、可测试的模型变更（§7.5），而不是把 `NOT_FETCHED` 或空响应当成正常覆盖。

## 3. 方案选择

### 3.1 采用：分层嫁接

新源和新表进入 Stock 的既有发布链；查询服务只读版本；操作面和调度器都通过 CLI
触发同一条更新路径。该方案最大化复用既有治理，并让各阶段可独立停留和回滚。

### 3.2 拒绝：Panda 服务栈并排运行

MongoDB 与 Parquet 会形成两套 latest、两套修正历史和两套读取语义。任何同步方案
最终都需要定义一方为权威；既然 Stock 已提供更强的不可变证据链，双底座只增加
不一致窗口。

### 3.3 拒绝：复制 Panda cleaner、scheduler 或 dist

该路线同时引入 AGPL/无许可证风险、Mongo 写入假设、吞异常行为和不可维护的前端
产物。即使短期可运行，也无法满足 Stock 的 provenance、fail-closed 和版本绑定。

## 4. 不可协商的架构约束

1. **单一写入链**：任何 canonical 数据都只能经 `DatasetPublisher.publish` 发布；
   API、调度器、adapter 不得直接修改 `data/standardized` 或 `CURRENT`。
2. **原始证据优先**：normalize 前必须先保存供应商原始响应及无凭据的请求元数据。
3. **失败留证**：adapter、更新任务和调度触发失败必须保留稳定错误码、请求范围和日志；
   不得把空响应当作成功。
4. **钉住版本**：每个读取请求解析一次明确 version；`CURRENT` 只是请求入口别名，
   响应必须回显解析出的哈希。
5. **无自动验收**：操作面只允许 `data update`，不得暴露 acceptance confirm/publish、
   research run 或删除接口。
6. **凭据隔离**：供应商凭据仅从环境读取，不进入配置、日志、异常、原始 manifest、
   job request 或前端响应。本期不新增凭据；未来新增源必须沿用该约束。
7. **无 Panda 运行时依赖**：Stock 不 import Panda 包，不复制其源码或构建产物。
8. **源范围**：本期扩展只落在既有 Tushare/akshare/星耀通道的端点与新表上，不新增
   供应商名，也不改变 tdx 只作仲裁器的角色。

## 5. Phase 0：决策、许可与架构正名

### 5.1 许可证门禁

实施前保存一次带 commit/hash 的许可证核验记录：

- PandaAI 根仓与 panda_quantflow 当前可见许可证为 AGPL-3.0；
- panda-data 子目录未发现可授予复制权的许可证；独立带 `.git` 的
  `panda-data-skill` 同样未发现许可证；
- 选择 **clean-room 重写**；
- 允许记录端点名、字段名、单位、输入输出样例和观察到的行为；
- 禁止复制函数体、注释、异常文案、前端 bundle、模板和测试 fixture；
- 行为与字段语义优先引用供应商公开文档；Panda 代码只用于验证已观察到的兼容行为。
- 新实现的评审必须能仅凭本规格、供应商公开文档和 Stock 测试解释其来源。
- 本期不再移植任何 pandaAI 数据源适配器（其源不可得），借用面收窄为“既有 Tushare
  端点的字段/单位语义”与“调度、查询面、门户的交互模式”；门禁不变，且 Stock 侧已有
  `index_weight` 采集脚本可独立复核，不依赖 panda 代码来解释行为。

若 owner 改选 AGPL 或“仅内部使用”，必须先形成单独书面决策；本规格其余部分不自动
改变许可证策略。本节是工程门禁，不构成法律意见。

### 5.2 ADR

- ADR-021：允许本地常驻查询面和调度触发器，确认它们不拥有数据写语义，并规定
  环回绑定、版本钉住与人工验收边界。
- ADR-022：记录本期数据源范围裁定（附录 A）与各源再进入条件（附录 B）、`basic_factor`
  的 `research_only` 初始 tier、成分事实的单路径（Tushare `index_weight` 月度快照）
  与 attested-boundary 近似契约、`announcement_date`/`raw_effective_from` 语义、
  `snapshot_observed_change` reason，以及不从 `daily_bar` 复制价格列的单一真相决策。
  它还要记录 §6.4 探针实测的单位与空值语义、单候选裁决（§7.3）及其失效条件，并明确
  `basic_factor` 与既有 CSI300 官方事实不进入星耀验证 lane 或 ADR-013/014 仲裁排序。
  该 ADR 同时写定 §7.5 的覆盖理由词汇（含 `history_begins_after_anchor` 的前缀约束与
  `source_disabled`/`source_unavailable`）与 preflight 窗口判定、`build_config.table_lineage`
  的引入，并**明确 `pipeline_contract_version` 保持为 `1`**（理由见 §7.3），以及
  `basic_factor_coverage` 的状态/理由词汇。背景节可引用同类系统的既有实践作设计出处
  （Qlib PIT 的“值 + 可得时间”双时间戳、CRSP 的生效区间方法论、ArcticDB 的 `as_of`
  版本读、LEAN 的按日期符号映射与“存原始、消费端换算”），只作引用，不引入依赖。
- 独立的 membership-hash ADR：引入 schema-v2 的 `universe_id` 切片哈希、
  `coverage_segments`/gap 语义、旧 v1 兼容读取、不可变定义版本注册表、定义重钉流程，
  以及 mixed-lineage 发布的迁移顺序。这是 G2 的硬前置，不塞入 ADR-022 的附带段落。
- 落 ADR 时先检查编号是否仍空闲；若发生并行占用，以索引中的下一个空闲编号替代，
  不改已有 ADR。

### 5.3 事实文档同步

服务或调度代码首次合入时，同一变更必须更新：

- `docs/architecture/overview.md`：从“只有 CLI”改为“CLI 是领域写入口，另有本地只读
  查询面与触发外壳”；仍明确无数据库。
- `docs/architecture/module-map.md`：加入 query service、operation runner、scheduler、
  web 的职责与禁止依赖方向。
- `docs/architecture/data-flow.md`：加入服务读路径和自动更新路径，明确验收不自动化。
- `RUNBOOK.md`：加入启动、停止、锁冲突、失败检查和恢复步骤。

### 5.4 依赖边界

- `duckdb` 从仅存在于 `environment.yml` 改为 `pyproject.toml` 核心依赖，因为
  `DatasetReader` 在正常运行路径直接 import 它。
- `fastapi`、`uvicorn` 放入 `service` optional extra；核心 CLI 安装不被迫安装服务栈。
- Vue/Vite 依赖只存在于独立前端 `package.json`，不进入 Python 依赖。
- 首版选择 systemd timer，不引入 `apscheduler`。
- 本期**不新增数据源依赖**：不引入 `rqdatac`、`xtquant`、`tqsdk` 等包，也不为它们预留
  extra。若将来某个源获准再进入（附录 B），其安装方式按 tgw 的 operator-installed 模式
  先在 RUNBOOK 固定版本与完整性核验方法，核心安装不得因缺包失败。

## 6. Phase 1：既有 Tushare 通道的端点扩展

### 6.1 范围与角色

本期**不注册新源**。两个端点都在既有 `TushareSource`（auto transport：relay 优先、
官方为退路）内实现，`transport_id`/`supplier_endpoint` 继续按现有证据规则记录：

- `index_weight`：为 CSI500/1000 产出候选成分快照。CSI300 已有产出（`399300.SZ`
  系列、月度快照），本期不重做、不覆盖。
- `daily_basic`：为 `basic_factor` 提供 `total_mv` 与 `turnover_rate`。**不**取
  `amount`/OHLCV——那些已由 `daily_bar` 拥有。
- 不改变 star（星耀）验证 lane、ADR-013/014 仲裁顺序或任何必选源角色；tdx 仍只作
  公司行为仲裁器。未来若要加入新源，必须另立 ADR，并定义冲突、缺源与票数不足时的
  确定性裁决；ADR-022 不预留隐式入口。

### 6.2 端点合同

- 端点实现必须走既有 raw snapshot → normalize 路径；normalize 是纯函数，不做 I/O。
- 请求参数固定为窗口或日期词汇（`index_weight(index_code, start_date, end_date)`、
  `daily_basic(trade_date)`），按月/按日窗口分页；不得退化为逐 symbol 打散请求。
- `index_weight` 沿用现有按月分片方式（`project/collect_index_weight_membership.py`
  的既有做法），把它提升为可复用的端点实现；该脚本可作为薄封装保留，但不再是唯一
  入口。指数代码沿用现有 lineage 的编码族（`399300.SZ` 是已用的 CSI300 代码），
  CSI500/1000 的具体代码由探针确认后固定，不得按数字前缀猜；panda 既有实现用的是
  `399300.SZ`/`000905.SH`/`000852.SH`，可作**探针起点**（背景线索，不是依据），其
  “取上月最后交易日窗口”的取法同样只作参考。
- `daily_basic` 的空值与单位必须显式：`total_mv`（万元→元，×10000）和
  `turnover_rate`（百分比→比例，÷100）在探针实测确认前不得写死；缺失保持 null，
  禁止填 0。
- 请求集合与返回集合经过 `validate_supplier_frame`；缺行、多余行、重复主键、空响应
  和截断都显式分类，**空响应绝不解释为“该日无成分/无因子”**。
- `fetch_batch`（ADR-020 的可选鸭子类型）不实现、也不需要：这两个端点本身就是窗口
  批量化接口，不存在逐 symbol 批量语义。
- proxy 的具名端点集合与 capability 校验默认值不得手改以绕过校验；新端点是否加入
  具名集合由探针结果与 capability 校验决定。

### 6.3 规范化

- `ts_code` → canonical symbol 沿用既有 tushare 归一实现，不新写映射、不按数字前缀
  猜交易所。
- `index_weight` 快照行 → 成分事实的 `raw_effective_from`/`raw_effective_to`/
  `announcement_date` 生成规则由 §7.1 定义；本阶段只要求产出可复现的中间产物，并为
  每份快照记录 sha256。
- `daily_basic` → `basic_factor` 行（§7.2 schema）；不复制任何行情列。
- 指数成分**不**写进行情行或 `basic_factor`：成分只以 `universe_membership` 事实
  存在（这是 panda 把成分塞进每行的做法的直接纠正）。

### 6.4 完成条件

探针结论进入 dated operations evidence，不写成永久架构事实：

- **`index_weight` 探针**：CSI500/1000 的可用性、历史窗口起点、实际快照节奏（月度/
  季度/不规则）、单快照行数与权重列形态、空响应形态，以及 relay 与共享 proxy 两个
  transport 的可达性、差异与配额。
- **`daily_basic` 探针**：历史窗口起点、按日与按区间两种取法、`total_mv`/
  `turnover_rate` 的原生单位与空值形态、与 `daily_bar` 同日 symbol 集合的差异
  （停牌/退市/新上市）、配额与失败形态，以及**可得性时点**（T 日数据当日何时可查、
  实际更新节奏）——§7.2 的可得性语义以该探针结论为准，探针未确认前不得写成契约
  事实。
- **数量级断言**：以已知证券/日期断言换算后的市值（元）与换手率（比例）量级，并把这
  些断言写进测试；一律不得沿用 panda 的 ×10000/÷100 而不实测。
- **节奏结论必须可判定**：探针要给出“应有观测节奏”及其实测依据；若节奏不稳定，
  §7.1 的漏采规则按 `unknown` 处理（要求人工确认），不得猜。
- 契约测试必须注入空响应、缺日期键、缺指数、重复主键、截断与 schema 漂移，并断言
  不会产生“全市场均非成员”或“全 0 市值”的成功结果。
- 既有 `project/collect_index_weight_membership.py` 的历史 lineage 重建回归保持通过。

## 7. Phase 2：PIT 成分与 `basic_factor`

### 7.0 多 lineage 的硬前置

当前 schema-v1 `UniverseDefinition.membership_table_sha256` 钉住整张
`universe_membership` 表，acceptance 和 runner 都不按 `universe_id` 过滤；因此把
csi300/500/1000 混入同一表会让所有现有定义发生 hash mismatch。Phase 2 在写入第二个
`universe_id` 前必须先完成独立 ADR 和以下兼容迁移：

1. `UniverseDefinition` 新增 schema-v2：固定
   `membership_hash_scope: universe_id`，使 `membership_table_sha256` 只哈希
   `frame[frame.universe_id == definition.universe_id]` 的 canonical facts；并新增
   `coverage_segments`（连续观测段列表）与 gap 列表（每段带 reason 与证据哈希），
   使“连续 coverage”和漏采成为定义内容而不是日志——`version` 是 canonical JSON 的
   SHA-256，段与 gap 因此自动进入实验身份。现有 `coverage_start`/`coverage_end`
   保持为段包络，必须等于各段最小起点与最大终点；段重叠、事实落在段外、gap 与段
   不互补的定义拒绝加载。
2. `evaluate_index_membership_evidence` 在 schema-v2 下先选择目标 slice，再对该 slice
   做 schema、事实、coverage、cardinality 和 hash 检查；其中 coverage 指 slice 内事实
   必须落在定义 `coverage_segments` 内且不落入任何 gap；空 slice 明确失败。runner
   传给 `_facts_from_membership_frame`、`resolve_memberships` 和 `UniverseResolver` 的也
   必须是同一 slice。不得出现“按 slice 验收、按整表运行”的分叉。slice 化只改变*哪个*
   universe 的哈希被比对：`data update`/`data validate` 仍必须对整表跑 schema 与事实
   校验，其他 universe 的坏行不因无人运行它而逃过校验。
3. schema-v1 保持原有整表哈希行为，只用于旧 dataset/definition 重放；不得把 v1
   悄悄解释成 slice hash。
4. 建立 `configs/universes/versions/<definition_version>.yml` 不可变注册表，并在同名
   发布中把现有 v1 定义按版本归档，保证旧冻结运行可解析。顶层
   `configs/universes/*.yml` **仍是完整的 `UniverseDefinition`**：现有装载器把每个
   顶层 `*.yml` 按完整定义解析，任何校验失败都会阻断发布，因此不得引入缺少
   `universe_id`、`rules_version`、`membership_table_sha256`、`coverage_start`、
   `coverage_end`、`evidence_summary_sha256` 的指针文件。显式 `universe_version` 从
   注册表解析并复核内容哈希；只有使用 `CURRENT` 的规格才解析顶层文件。
5. mixed-lineage 首次发布前，所有仍启用且**在目标数据集中存在非空 slice** 的顶层
   定义一次性升级到 v2 并重算各自 slice hash。定义 version 必然变化；已发布实验保持
   不变，使用 `CURRENT` 的源规格下一次冻结新版本，显式旧 version 继续走注册表。
6. 在目标数据集中没有 slice 的启用定义不得计算空 slice 哈希冒充 v2：它们保持 v1
   语义，并在同一变更中移出顶层扫描目录（`archive/` 或 `versions/`），理由与去向写进
   变更记录。移出或停用定义会抬高 `acceptance_start = min(coverage_start)`，因此只
   允许在剩余定义的最小起点不变时进行；不得借清理之名缩小全库验收窗口。
7. `data update` 仍只 carry 已发布 membership 整表。新增/替换 lineage 只能走显式
   membership refresh：本阶段补充正式
   `python -m stock_quant data index-membership publish`，它读取 prepare 产物、按
   `universe_id` 替换目标 slice、保留其他 slice、重跑全部门禁并发布新 dataset。
   不再依赖 `project/collect_*.py` 脚本作为长期正式入口。
8. 同一次 refresh 必须完成定义重钉：slice 被替换后，钉住该 slice 的哈希以及 §7.1 的
   连续 coverage 段都会变化，因此同一步要写出新的 schema-v2 定义内容，并把新 version
   追加进注册表；否则该 universe 的下一次运行会以 hash mismatch 失败。重钉产生新
   version，既有冻结规格继续按注册表重放。
9. `configs/universes/*.yml` 仍非递归扫描且禁止重复 `universe_id`。新增定义的
   `coverage_start` 不得早于当前 `acceptance_start`；若确需提前，必须在同一发布补齐
   从新起点开始的 calendar、行情、主数据、coverage 和人工验收义务，不能仅靠一张
   membership 表悄悄加宽全局窗口。
10. **refresh 的崩溃一致性协议**。一次 refresh 要同时动三样东西：新 dataset 版本、
    顶层 definition 文件（`configs/universes/*.yml`）与注册表追加项（`versions/`）。
    今天 `DatasetPublisher.publish` 发布目录后立即替换 `CURRENT`，而 definition 在
    dataset 目录之外，**三者无法原子替换**：任何顺序下崩溃都会留下"新 `CURRENT` + 旧
    definition"或"旧 `CURRENT` + 新 definition"，而 `index_membership_evidence` 与
    runner 的 `spec.universe_version == definition.version` 会让它变成持续性 hash
    mismatch（不是更新期间的短暂失败）。因此 refresh 必须实现 prepare/commit/recovery：

    1. **prepare**：生成新 dataset 版本与新的 definition 内容，但都不提升为当前；
       注册表先追加（注册表本身是 append-only、可安全先行）。
    2. **commit**：只做两件不可分割的可见性提升——替换 `CURRENT`，再替换顶层
       definition 文件；两次替换都是单文件 `os.replace`，各自原子。
    3. **recovery**：把“已提交的 generation”写成可恢复状态（如
       `data/.membership_generation.json`，记录 dataset version + definition version +
       各自的哈希）。任何 refresh 入口与常驻服务启动时先校验该状态与磁盘实际一致：
       不一致或缺失时**不猜**，报稳定错误码并给出修复命令
       （`data index-membership recover --to <generation>`），由 operator 显式选择回滚到
       哪一代。
    4. 崩溃注入测试必须覆盖：prepare 后中断、`CURRENT` 替换后中断、definition 替换后
       中断，三种情况都必须能被 recovery 收敛到一个自洽 generation，且期间不产生任何
       被误当作可用的中间态。

    提交顺序上先 `CURRENT` 后 definition 的风险更低（旧 dataset 与新 definition 的失配
    在下次 refresh 时自愈），但两种顺序都必须由上述状态机覆盖，不得依赖“操作者不会在
    中间崩溃“。dataset 版本是内容寻址的，重跑 refresh 幂等；注册表保留旧 definition
    内容，因此回滚是文件替换而非重建——这一点必须写进 RUNBOOK 的恢复步骤。

    **备选：Iceberg 式单指针（供 owner 裁决；不与上文协议混用）**。把
    `data/.membership_generation.json` 从恢复辅助升格为唯一权威指针：generation 清单
    点名 dataset version 与 definition version，读取路径先解析 generation（单文件
    `os.replace`，天然原子），`CURRENT` 与顶层 definition 文件降级为 refresh 在替换
    generation 后尽力重写的派生缓存。这把“两次可见性提升”化简为一次原子替换
    （Apache Iceberg 的 catalog 指针即此模式），三个中断点退化为“派生缓存落后于
    generation”的自愈状态；代价是读取路径多一跳，且派生缓存必须整写完整定义以满足
    §7.0.4 的装载器约束。若采纳，本节 commit/recovery 状态机随之简化；不采纳则维持
    上文双替换协议。

### 7.1 成分事实路径

成分只走既有 Tushare `index_weight`（月度权重快照），不进入每日行情更新。它给出
“快照日 × 成分（含权重）”，没有指数公司公告元数据，因此本节规则是一份**成文的近似
契约**：

```text
raw Tushare index_weight response（按月分片，逐份 sha256）
  -> offline prepare（代码、snapshot_date、来源规范化）
  -> 快照序列 → attested-boundary 事实（成员保持到最后一次列出它的快照前一日）
       -> 与既有事实/既有 lineage 的差异报告 -> operator 显式 publish
       -> 新 dataset version 携带 universe_membership slice（§7.0.7/§7.0.8）
```

规则：

- 映射：CSI300 沿用既有代码族（`399300.SZ`）；CSI500/1000 的代码由 Phase 1 探针确认
  后固定，不得按数字前缀猜。池 id 用 `custom_csi500_tw*`/`custom_csi1000_tw*`，沿用
  既有 `custom_csi300_tw*` 的 `_tw` 语义：`custom_` 前缀跳过逐日基数校验——canonical
  id 要求每个交易日恰好 N 名成员，而月度快照的滞后无法满足该约束（既有 `tw` 系列正
  是同一原因）。每个新池必须在配置里写清 `rules_version` 与近似说明。
- 每条事实必须符合现有 `MembershipFact`，保留原始有效区间、可得日期、source、
  无凭据 URL、snapshot SHA-256 和 source-document SHA-256。
- 可得性语义必须诚实：`announcement_date` 取“该边界被快照证实的快照日”，与
  `raw_effective_from` 同日，属 attested-boundary 约定的既定近似（现有 csi300
  lineage 766/766 行即此形态）。该近似必须写进 `rules_version` 与证据摘要，并在 UI
  显示；不得描述为官方公告日，也不得据它声称精确生效日。
- 滞后上界必须成文：快照节奏为月度时，月中调整可能被记到快照边界，误差上界约一个
  快照周期。差异报告与 UI 必须显示该上界；对生效日精度敏感的研究必须在规格里显式
  接受该近似，不能默认它不存在。
- **漏采规则**：任一应有观测缺失、异常、缺日期键或空集合，都写
  `membership_observation_gap` 证据并中断该 universe 的连续 coverage。gap 两端集合
  不得直接差分，不猜“前一交易日”或“采集日”是精确移除日；下一次成功观测只可开启
  新候选 coverage segment。gap 不能只停在日志里：每次 refresh 必须把连续段与 gap
  写进新的 schema-v2 定义（`coverage_segments` + 带证据哈希的 gap，见 §7.0.1/§7.0.8），
  否则 `version` 绑不住它、preflight 也无从判定。研究窗口跨越 gap 时 preflight 必须
  失败（稳定错误码 `universe_gap_in_window`），除非另有日期化外部证据闭合该 gap。
  “应有节奏”取自 §6.4 探针结论；探针判为不稳定时该 universe 的节奏记为 `unknown`，
  此时不得据快照差分生成候选，必须有 operator 人工确认，不得按最乐观假设猜一个节奏。
- **历史快照路径**：过去月份的 `index_weight` 快照可以补采。每份快照是一个自证边界日
  期的供应商文档，由 snapshot SHA-256 与无凭据 URL 绑定，因此补采事实与当日采集的
  事实证据强度相同，同样适用上一条的 attested-boundary 近似。但采集时点必须单独记录
  为 `collected_at`：**不得**把脚本运行当刻写成 `announcement_date`，也不得据补采主张
  比快照周期更精确的生效或公告时点。`collected_at` 是 provenance 列，不参与 dataset
  version 与 definition version 的身份哈希；同一快照首次落盘后 `collected_at` 固定，
  重放不更新——否则重复导入同一组原始响应会产出新版本，违反 §2.2 的确定性判据。
- `announcement_date` 只能来自快照文档本身（即该快照已证实的边界日），且必须与对应
  `trade_date` 逐位对应；与 `raw_effective_from` 同值是这条约定的结果，不是可以反向
  构造的许可证。任何为通过门禁而编造的可得日期都使该事实不得发布。
- 由相邻快照差分生成的加入/退出不能伪称 `regular_rebalance`、`correction` 或
  `delisting`。ADR-022 新增严格 reason `snapshot_observed_change`，表示“变化由相邻
  供应商快照观测得到，经济原因未知”；初始采集日的开放区间使用
  `initial_constituent`，该区间以后被观测为结束时，新数据集中的闭合事实改记
  `snapshot_observed_change`，以满足既有“initial 必须 active”的模型约束。
- CSI300 与既有官方事实重叠时生成差异报告，不自动选边、不覆盖原行；既有
  `regular_rebalance` 行不被新池复用，也不因新快照路径而被重写。冲突需 operator 处理
  并留下新证据；没有“新路径必然更准”的隐式规则。
- CSI500/1000 只有在 requested research window 完全落入其可证明的 coverage window
  后才可用于正式研究，并仍须通过现有 universe preflight 和 real-data acceptance。
  PIT 能力不等于官方权威，UI 必须显示实际 `source` 与 coverage start。

### 7.2 `basic_factor` canonical schema

表主键为 `(trade_date, symbol)`，初版只含：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `trade_date` | date32, non-null | 事实对应交易日 |
| `symbol` | string, non-null | Stock canonical symbol |
| `market_cap` | float64, nullable | 当日总市值，人民币元；源缺失保持 null，禁止填 0 |
| `turnover_rate` | float64, nullable | 无量纲比例，`0.01` 表示 1%；源值按实测单位显式换算 |
| `source` | string, non-null | 唯一主源，本期固定 `tushare`（`daily_basic`） |
| `ingested_at` | UTC timestamp, non-null | 采集时刻，不参与业务时点 |

单位换算必须在 §6.4 探针中实测确认后才写入实现，不得按名称或文档推断。当前待确认的
假设（仅作探针靶子）：`total_mv` 以万元计价，故 `market_cap = total_mv * 10000`；
`turnover_rate` 以百分数返回，故 `turnover_rate = turnover_rate_raw / 100`。探针结论
与假设不符时以探针为准，并回写本节。

**身份哈希与 provenance 列**：dataset version 哈希只覆盖业务列（`trade_date`、
`symbol`、`market_cap`、`turnover_rate`、`source`）；`ingested_at` 不进入哈希，重放
同一原始响应不改变已落盘值。这与“runtime 时间戳不得进入身份哈希”的既有 invariant
一致。

**可得性语义**：表契约必须写明“T 日因子值最早可得时点”（预期为 T 日收盘后，具体以
§6.4 探针实测为准，未确认前不得写成契约事实；Qlib PIT 的“值 + 可得时间”双时间戳是
该语义的既有实践）。它是下文联接一致性检查的 PIT 前提：等值联接只在“两表同日事实
同日可得”时成立；将来若有披露滞后的因子（如财报），必须改按可得时间联接，不得
静默沿用等值联接。

不存 `open/high/low/close/volume/amount`，也不从 `daily_bar` 复制任何价格列填充本表。

**因子—行情联接一致性**：同一 dataset version 内 `daily_bar` 与 `basic_factor` 按
`(trade_date, symbol)` 必须一对一：任一侧主键重复、或联接产生扩行，都是质量错误；
两侧的行集合差异（如停牌、退市、新上市）必须落在 `basic_factor_coverage` 证据里，
不得靠沉默的 left join 掩盖。这条检查与 Panda 无关，是 Stock 自己的表间一致性要求。

**不提供 Panda 兼容格式**。本设计不定义“十列宽表”契约（列名、顺序、dtype、日期编码、
`turnover` 单位、行集合都不固定），也不把它放进 API 列白名单或测试。理由：两个项目
今天都不消费对方的格式（panda 只读自己的 Mongo，Stock 也不依赖 panda 的产物），而钉死
该契约会把**第二套日期编码（`YYYYMMDD` 字符串）和反向的换手率单位（百分数）**合法化进
同一个仓库，正是 §0 裁决 1/4 要消灭的“两份数据真相”。如确有消费方将来需要 panda 口径，
在消费侧写一次性转换即可：联接上式两表，`turnover_rate * 100` 得百分数，
`trade_date` 转 `YYYYMMDD` 字符串，并按需剔除 `.BJ` 行；该转换不进核心契约。

### 7.3 主源裁决、契约与门禁

本期只有 `daily_basic` 一个候选，ADR-022 不再做多源比较，而是记录**单候选裁决**：
候选必须能绑定真实 transport/upstream、覆盖目标历史窗口、在可接受配额内稳定返回，
并已实测市值/换手率单位与空值语义；任一项失败即本阶段不发布 `basic_factor`，不得降级
单位或 provenance 要求来凑表。该裁决必须连同 §6.4 探针结论、owner 的 2026-09-30 源范围
裁定（附录 A）与其他源再进入条件（附录 B）一起写进 ADR-022，使后来的读者知道这不是
“比较后胜出”，而是“唯一可用候选”。

主源在一个 dataset version 内唯一；换主源是新 ADR、新 raw lineage 和新 dataset
version。若将来有第二个源获准再进入，本节的单候选裁决即告失效，必须重新走多源比较，
并且只有能证明上游 lineage 独立时才可作 anchor——仅“两个入口数值相同”不构成独立性。

- `STANDARDIZED_SCHEMAS` 注册 `basic_factor` 与 `basic_factor_coverage`，`tables`
  装配、fixture、manifest 和 validate 路径同步更新。注册即强制：`table_fetch_coverage_evidence`
  要求被记录的表从验收锚点起连续铺满；因此新表必须先完成 §7.5 的覆盖证据模型扩展。
  tier（`research_only`）不影响发布存在性，只影响研究入口是否放行该表。
- **`required_table_coverage` 改为按 manifest 自身的表集合判定**（§7.5.6），"当前注册表
  必须齐全"留在发布期门禁。理由是它今天用**当前代码**的 `STANDARDIZED_SCHEMAS` 去比任意
  历史 manifest，注册新表后旧版本会缺表失败；而冻结实验的重放读的是**已落盘的**验收记录
  （`AcceptanceRegistry.select`），不重跑检查，真正被破坏的是"对旧版本重新验收/复审"。
  不得用 `pipeline_contract_version` 升版来绕：它被有意钉在 `1`，升版会让**每一个**已发布
  数据集在 `source_role_health` 上失败（见 `calendar_coverage.py` 的成文说明），等于把问题
  搬家并放大。仓库既有的演进模式是“版本号不动、加 key 区分形态、旧形态兼容读取、处置方式
  是重发布“，本阶段照此办理。
- 本期无独立 anchor，tier 定为 `research_only`。升为 `anchored` 必须另有独立来源证据
  与专项验证，不允许单源自证。
- `primary_transport` 写 `tushare:relay`（当前实际链路；若探针显示只有直连可用则写
  实际胜出的那个，并在 ADR-022 记录依据）。
- `incremental=last_covered_plus_1`。无 anchor 时 `conflict=block`。
  `coverage_shape=per_symbol_window`，并新增
  `basic_factor_coverage` canonical 表记录每个 symbol/window 的 fetched、missing、
  failed 状态。anchor-conflict 词汇本期保留但无 anchor 可写入，属于预留而非常态。
- `basic_factor_coverage` 自身声明为 `core`、`coverage_shape=none`，primary transport
  与 `basic_factor` 相同；缺少 coverage 表、或 coverage 与事实表不一致（键集合、计数、
  窗口）时整轮阻断，避免用缺失的“证明表”给业务表降级放行。行为粒度为 symbol×window
  行，词汇复用 `corporate_action_coverage` 的 `CoverageStatus`/`CoverageReason`，不新造
  状态码：

  | 情形 | 落码 |
  | --- | --- |
  | 事实完整有效 | `VERIFIED` |
  | 请求成功，但**应有而无**（已上市且在开放交易日的 symbol 缺行或缺必需字段） | `UNTRUSTED(FACTS_INCOMPLETE)` |
  | 请求窗口或标的集合不完整（缺段、缺 symbol、被截断） | `UNTRUSTED(COVERAGE_INCOMPLETE)` |
  | 请求失败 | `UNTRUSTED(SOURCE_FETCH_FAILED)` |
  | 多源冲突（本期无 anchor，预留） | `UNTRUSTED(SOURCE_CONFLICT)` |
  | 能证明"不适用"（未上市、退市后、非交易日） | `VERIFIED_EMPTY` |

  `VERIFIED_EMPTY` 在现有代码里的定义是"每个被请求端点都成功且**没有事件**"
  （`corporate_action_coverage`），那是为**稀疏事件**设计的语义；对按交易日稠密存在的
  因子事实，把“应有而无”标成可信空值会穿过本表自己的过滤被正式研究放行。因此
  `basic_factor_coverage` 使用 `VERIFIED_EMPTY` 必须有 security master 的上市/退市证据
  支撑，与 `date_window_completeness` 用上市日期解释缺失行的既有做法一致。
  上述理由码均已存在，不需要新造。

  下游过滤只许判 `status == "UNTRUSTED"`，不得用 `status != "VERIFIED"`
  （`VERIFIED_EMPTY` 是“确认无事实”，不是失败）。UNTRUSTED 行本身是被发布、进入验收的
  证据，不在发布时阻断，但必须让引用该表的正式研究 fail closed。
- 当前运行时只真正消费 `tier`、`incremental` 和 `pit`；`primary_transport`、
  `anchors`、`conflict`、`coverage_shape` 目前主要是解析期声明。本阶段不得把声明当成
  已有保障，必须新增并补齐：在 `build_config` 中**新增** `table_lineage`
  （table → transport → raw snapshot/端点的证据行；现有 `raw_snapshots` 只按
  source×endpoint×transport 记录，不含表归属），发布时核对它与 `primary_transport`
  声明一致；按 `coverage_shape` 要求 coverage 表；`conflict=block` 时 anchor 冲突路径
  本期不被触发，但 `coverage_downgraded` 的写入点必须一并落地并有失败测试。
  `table_fetch_coverage` 同时改为要求**每张已发布表**都有
  记录（今天只校验被记录的表，漏记即无证），并作为上一条“manifest 自身表集合”的
  权威来源。新增 `table_lineage` 键会改变此后每次 build 的 dataset version 哈希，
  但**不升** `pipeline_contract_version`：没有该键的旧 manifest 按旧形态兼容读取，
  其处置方式是重发布而非常驻豁免。这些检查必须有失败测试。
- 历史起点晚于验收锚点的新表按 §7.5 处理；不得为凑覆盖而回填不可证明的历史，也不得
  把 `NOT_FETCHED` 当作正常覆盖。
- 新增表级检查至少覆盖 schema、主键唯一、有限非负市值、有限非负换手率、日历内日期、
  security master 符号、请求覆盖和联接一对一。
- 将 tier 升为 `anchored` 或 `core` 必须另有独立锚点证据和新 ADR；不得在 UI 或运行
  参数中临时升档。

### 7.4 完成条件

- 真实小窗口从 raw snapshot 走到新 dataset version，`data validate` 通过。
- 相同输入重跑版本哈希不变；单个源事实变化产生新版本，旧版本未修改。
- `basic_factor` 缺失保留 null；"应有而无"落 `UNTRUSTED(FACTS_INCOMPLETE)` 并让正式研究
  fail closed，不出现 Panda 的 fill-zero，也不出现被标成可信空值的空洞。
- 一个含至少两个 `universe_id` 的 fixture 能让各自 schema-v2 definition 通过 slice
  hash/preflight；schema-v1 仍按整表语义重放，二者没有隐式 fallback。
- 使用显式旧 `universe_version` 的 fixture 能从 definitions registry 重放；使用
  `CURRENT` 的规格明确冻结到新的 v2 definition version。
- 每条成分事实的 `announcement_date` 都能追到一份具名快照（`trade_date` 与之逐位对应、
  哈希已绑），且 UI/证据摘要显式标注 attested-boundary 近似与滞后上界；补采事实的
  `collected_at` 不等于 `announcement_date`。任何冲突都不静默发布，失败报告保留。
- `basic_factor` 的单位换算有探针记录支撑：探针结论与 §7.2 假设不符时，实现随探针调整
  而非随命名猜测。
- `daily_bar` × `basic_factor` 联接一对一有失败测试：重复主键、联接扩行、行集合差异
  （未落 `basic_factor_coverage`）都必须让该轮失败，而不是被 left join 静默吸收。
- §7.5 的覆盖扩展有失败测试：历史起点晚于锚点的新表能发布并通过 `data validate`；
  引用早于其支持起点的窗口的 RESEARCH run 被稳定拒绝，引用支持窗口的 run 正常放行。
- gap 可复现：注入一次漏采后，新定义版本的 `coverage_segments` 反映中断，跨越 gap 的
  窗口以 `universe_gap_in_window` 失败，且不产生任何精确加入/移除边界。
- 同一次 refresh 完成重钉：替换 slice 后定义哈希同步更新、新 version 进入注册表，该
  universe 的下一次运行通过，旧 version 仍可从注册表重放。
- refresh 崩溃注入测试（§7.0.10）三种中断点各一次，都由 recovery 收敛到自洽
  generation，且 `data index-membership recover --to <generation>` 可用。
- 注册新表后，旧 dataset 版本的复审不再缺表失败；`required_table_coverage` 对无
  `table_lineage` 的旧 manifest 按旧形态兼容读取（§7.5.6）。
- `not_fetched` 前缀规则有失败测试：中间或尾部的 `history_begins_after_anchor`、
  以及混用 `source_unavailable` 的段都必须被拒（§7.5.1/2）。

### 7.5 新表进入覆盖证据的硬前置

新表会同时撞上三处既有门禁：发布器拒绝没有 canonical schema 的表；
`required_table_coverage` 今天用**当前代码**的 `STANDARDIZED_SCHEMAS` 去比任意历史
manifest；`table_fetch_coverage_evidence` 要求被记录的表从验收锚点
（`acceptance_start`，当前 2015-01-05）起连续铺满到发布日。而 `not_fetched` 是整表
全有或全无（`not_fetched_mixed_with_fetch`）、理由词汇只有 `operator_explicit_window`，
且任何带 NOT_FETCHED 段的表都会让引用它的 RESEARCH run 在 preflight 被拒。综合结果
是：历史起点晚于锚点的新表既无法通过验收，也无法被正式研究使用。

注册 `basic_factor`/`basic_factor_coverage` 之前，必须在同一 ADR 中落地以下最小扩展，
并配失败测试：

1. 覆盖理由词汇新增“表的历史起点晚于锚点”这一类（如 `history_begins_after_anchor`），
   但**必须严格限定为唯一前缀**：该段从 `acceptance_start` 开始、精确结束于
   `supported_start - 1`，不得出现在中间或尾部，且与 calendar/contract 窗口对齐
   （现有 `fetch_coverage_out_of_window` 校验继续生效）。理由是现有校验有一条
   `not_fetched_mixed_with_fetch`，**根本不允许** not_fetched 与 fetched/carried 同表
   共存，所以“允许交错”这句话必须在同一次改动里显式改写它，而不是顺带放宽——否则
   任意中间漏采都能伪装成“历史起点”。该理由必须绑定表级声明或探针证据。
2. 同一个词汇表还要能表达"源不可用"：至少 `source_disabled`、`source_unavailable`
   （区分“本次未启用”与“启用了但不可达”）。注意这是 **fetch-segment 词汇**，与
   `CoverageReason.SOURCE_FETCH_FAILED`（coverage 表词汇）是两套，不得互相顶替。
   只加“历史起点”理由不足以表达源故障，这两类必须一起落地。
3. preflight 增加窗口判定：运行窗口早于该表支持起点或落在未支持段内时，RESEARCH run
   fail closed（稳定错误码如 `table_history_start_after_window`）；窗口完全落在支持
   范围内时正常放行。ENGINEERING 保持既有豁免语义并标注。
4. 源被禁用或不可用时，**只有首次发布且不存在 baseline** 才允许以空 canonical frame
   参与发布（沿用 quarantine 空表先例）；**已有历史版本时必须 carry 已有事实**并只把
   新的尾段标为不可用（`_read_baseline` 已具备 carry 已有表与 coverage 帧的能力），
   或直接阻断本轮发布。清空已有表会让既有事实消失，那是比缺表更严重的事故。
5. `table_fetch_coverage_evidence` 增加“每张已发布表都必须有记录”的校验，堵住“不记录
   即通过”的路径。
6. `required_table_coverage` 改为按 manifest 自身记录的表集合判定（§7.3），"当前注册表
   必须齐全“移到发布期门禁。这样注册新表不会让已发布版本在复审时缺表失败，也不需要
   动 `pipeline_contract_version`。

扩展必须显式、可测试、可回滚；不得通过放宽 `STANDARDIZED_SCHEMAS`、跳过验收或伪造
覆盖来让第一张新表落地。

## 8. Phase 3：只读 FastAPI 查询面

### 8.1 进程边界

新增查询服务只依赖 project root、`DatasetReader`、acceptance registry 和已生成报告。
它不 import `DataPipeline`，不持有 publisher，不暴露 POST/PUT/PATCH/DELETE。

默认只绑定 `127.0.0.1`。公网部署、认证、TLS 和多用户隔离属于另一份规格；当前进程
若配置为非环回地址必须启动失败。

### 8.2 API v1

| 方法与路径 | 行为 |
| --- | --- |
| `GET /api/v1/health` | 返回服务状态和 project-root 指纹，不返回绝对路径 |
| `GET /api/v1/datasets` | 版本列表、是否 CURRENT、发布时间证据、表计数、质量摘要、正式验收状态 |
| `GET /api/v1/datasets/{version}` | manifest、表元数据、质量摘要、验收摘要；不返回凭据或原始 payload |
| `GET /api/v1/datasets/{version}/quality` | 持久化质量问题的分页只读视图 |
| `GET /api/v1/datasets/{version}/tables/{table}` | 表预览；列、日期、symbol、offset、limit 均受白名单约束 |
| `GET /api/v1/experiments` | 已发布实验及其钉住的数据集版本 |
| `GET /api/v1/experiments/{id}/report` | 只返回已存在的自包含 HTML；不在请求中重建报告 |

约束：

- `{version}` 接受完整 64 位哈希或 `current`。`current` 在请求入口解析一次，响应中的
  `dataset_version` 永远是完整哈希。响应同时回显本次请求的解析参数（表、列、过滤
  条件、分页），使前端与日志可完整重建视图（OpenBB OBBject 信封的 `arguments`
  字段即此先例）。
- `limit` 默认 100、最大 500；不得提供任意 SQL、任意文件路径或 DuckDB 文件下载。
- 每次表查询带固定时间预算（默认量级 5 秒），超时返回稳定错误码（如
  `query_time_budget_exceeded`）；时间预算与行数 limit 相互独立（Datasette 的
  `--sql-time-limit-ms` 即此先例），防止病态过滤拖垮服务。
- table 必须来自该版本 manifest，列必须来自 schema；值过滤使用参数绑定，标识符用
  既有安全引用规则。
- 每次请求独立打开/关闭只读 context；不得跨请求缓存 `CURRENT` 的连接。
- acceptance 摘要至少区分 `ACCEPTED`、`REJECTED`、`PENDING_CONFIRMATION`、
  `UNVERIFIED`，并分别返回“存在有效 accepted record”和“最近一次 verdict”，避免
  新的 rejected 记录遮蔽仍可验证的 accepted record。

### 8.3 完成条件

- contract tests 固定 OpenAPI 响应模型、分页、404/409/422 和版本回显。
- 并发读同一不可变版本与一次独立 `data update` 同时进行时，读取请求始终看到旧的
  完整版本或新请求解析后的完整版本，不看到半发布状态。
- 路径穿越、SQL 注入、超大 limit、未知表/列和损坏 manifest 均失败关闭。

## 9. Phase 4：操作面、单飞锁与调度器

### 9.1 统一 UpdateRunner

UI 与调度器共享一个 runner，但 runner 不调用 `DataPipeline` Python API。它以参数数组
启动：

```text
python -m stock_quant data update --root <resolved-root> [--start ...] [--end ...]
```

不得使用 shell 字符串拼接。允许参数只有 start、end、sources 和
disclosure-lookback-days，且按 CLI 同一类型约束验证。

新增正式入口：

```text
python -m stock_quant operations update --root <resolved-root> [允许的 update 参数]
```

该命令同步调用 UpdateRunner、创建 job 目录、等待内部 `data update` 子进程结束并以
同一成功/失败码退出。`data update` 必须为“已有运行中”给出稳定的机器可读信号（专用
非零退出码，或固定 token `update_already_running`），UpdateRunner 据此把 job 记为
FAILED/`update_already_running`，不得靠匹配自由文本猜测。systemd timer 只调用这个外层
入口；不得直接调用 `data update`，否则 Web 看不到定时任务记录。手工诊断仍可直接调用
`data update`，但同样受 project lock 保护。

### 9.2 单飞与持久化状态

- `data update` CLI 自身取得 project-local advisory lock，覆盖手工 CLI、Web 与调度
  三个入口；锁粒度是一个 project root。锁实现为 `flock(2)` 语义：锁的生存期即持有
  进程的生存期，进程崩溃由内核自动释放，不存在需要清理的 stale lock，恢复路径因此
  不涉及锁文件删除。锁文件放在 project root 内（如 `data/.locks/update.lock`）。
- 已有运行时返回 HTTP/CLI conflict，不排队，不杀死当前任务。
- 每个操作任务写入 `data/service/jobs/<job_id>/`：不可变 `request.json`、追加式
  `stdout.log`/`stderr.log`、原子替换的 `status.json`。
- 状态词汇固定为 `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED_BY_SHUTDOWN`。存活
  判定机制固定为：runner 以固定间隔把 pid 与 heartbeat 时间戳原子写入 `status.json`
  （另记 boot_id 防 PID 复用），服务自检把心跳超过阈值（默认量级 5 分钟）的 RUNNING
  改记 `FAILED`，原因码为 `orphaned_process`，不删除目录；不得以“目录存在”或
  “日志静默”推断存活（Dagster 的 run monitoring 心跳即此先例）。
- job id 不进入 dataset version；成功状态记录 CLI 输出的 run id 和 dataset version。

### 9.3 操作 API

操作面是独立 router/process，默认禁用；启用时仍只绑定环回地址：

| 方法与路径 | 行为 |
| --- | --- |
| `POST /api/v1/update-jobs` | 校验并启动一次 `operations update`；已运行返回 409（与 §9.1 同一冲突码） |
| `GET /api/v1/update-jobs` | 返回持久化任务摘要 |
| `GET /api/v1/update-jobs/{id}` | 返回状态与脱敏日志尾部 |

不提供取消、重试到成功、删除日志、验收或研究端点。一次失败后的再次运行必须由新的
显式请求或下一次计划触发产生新 job id。

### 9.4 调度

- 首版固定采用 **systemd timer**，不同时维护 APScheduler。timer 的
  `ExecStart` 只能调用 §9.1 的 `operations update`，由 UpdateRunner 产生持久 job；
  systemd 负责进程监督，project-local CLI 锁负责跨手工/Web/timer 的最终单飞。
- timer 使用固定 unit 名 `stock-quant-data-update@<project-id>.timer`，时区按宿主机
  `Asia/Shanghai` 配置；`Persistent=false`，停机期间错过的触发记在 systemd 日志，
  不在恢复后补跑任务风暴。
- 一次 timer 触发遇到现有锁时，UpdateRunner 创建一个最终状态为 FAILED、原因码为
  `update_already_running` 的 job，随后非零退出；这样冲突在 Web 与 journal 两处可见。
- 调度失败通知读取 job 的最终状态，不解析质量问题来决定“忽略后继续”。
- 调度器不得调用 acceptance、research、report build 或清理命令。

## 10. Phase 5：Vue 数据门户

### 10.1 页面

1. **版本面板**：dataset version、CURRENT 标记、发布时间证据、表、质量结论、有效
   acceptance 与最近 verdict。
2. **数据预览**：选择明确版本和表，分页、列筛选、日期/symbol 过滤；页面顶栏持续
   展示完整或可复制的版本哈希。
3. **更新任务**：在操作面启用时提交允许参数，轮询持久化 job，展示脱敏日志和最终
   dataset version；禁用时页面只读并说明原因。
4. **报告链接**：列出实验、其 dataset version 和已有静态报告；不在线重算。

### 10.2 展示纪律

- 不使用“数据正常”代替真实状态；明确显示 `ACCEPTED/UNVERIFIED/REJECTED`。
- CURRENT 是指针标记，不是可信等级。
- 表格每次翻页都携带同一 resolved version；CURRENT 变化时提示有新版本，不自动
  切换当前页面。
- 错误展示稳定 error code 与安全摘要，不显示绝对路径、环境、token 或完整堆栈。
- 不从 Panda dist 复制 CSS、组件、文案或图片；新建 Vue 3 + Vite 源码和测试。

### 10.3 功能流程

门户服务两类流程：operator 的数据运营与研究的版本消费。交互只取成熟系统的设计
形态作参照（Datasette 的只读表浏览与查询时间预算、Dagster/mlflow 的 run + 日志
视图、OpenBB 的响应信封），不引入其代码或依赖。

**页面地图**：所有页面共享顶栏——project-root 指纹、当前页面解析的完整版本哈希、
CURRENT 标记；导航固定为「版本面板 → 数据预览 → 质量/覆盖证据 → 更新任务 →
报告」。

**数据更新流程**（操作面启用时；禁用时更新页只读，说明原因并给出等价 CLI 命令）：

```text
更新页填写允许参数（start/end/sources/lookback；默认空 = 常规增量）
  -> POST /api/v1/update-jobs -> job 详情页（QUEUED/RUNNING 轮询，脱敏日志尾部）
  -> 终态：
     SUCCEEDED     显示 run id 与新 dataset version（完整哈希），链接版本详情；
                   版本面板出现新版本；正在浏览的页面不自动切换，仅提示“有新版本”
     FAILED        显示稳定错误码与质量原因摘要；不自动重试，再次更新 = 新 job
     409 conflict  显示“已有任务运行中”并链接到该 job（§9.1 的 update_already_running）
```

**结果展示流程**：版本面板列出全部版本与 CURRENT 标记 → 版本详情展示表计数、质量
摘要与验收状态（`ACCEPTED`/`UNVERIFIED`/`REJECTED`/`PENDING_CONFIRMATION`，按 §8.2
把“存在有效 accepted record”与“最近一次 verdict”分开显示）→ 数据预览按钉住版本
分页浏览 → 质量/覆盖证据页展示 UNTRUSTED 行、coverage gap 与 attested-boundary
近似标注（§7.1 的滞后上界在此可见）→ 报告页链接既有静态 HTML，不在线重算。

**使用流程**：

- operator 例程：查版本面板 → 触发/查看一次更新 → 审质量与覆盖证据 → 人工验收
  （acceptance confirm 仍在 CLI，见下）→ 门户刷新显示 ACCEPTED。
- 研究消费例程：选版本 → 核对验收状态与 universe coverage/gap 标注 → 打开实验
  报告；研究运行（`research run`）不进 Web。

**不在 Web 里的动作**：acceptance confirm/publish、research run、失败证据清理。
门户对这些只显示“下一步去哪”（指向 RUNBOOK 的等价命令），不提供按钮——这是
§4 第 5 条约束在交互层的直接表达。

### 10.4 完成条件

- 浏览器端测试覆盖版本切换提示、UNVERIFIED 展示、分页保持版本、更新 409、更新失败
  日志、报告不存在和操作面关闭。
- 更新流程四种终态（SUCCEEDED/FAILED/409/操作面关闭）各有浏览器端断言：成功终态
  显示完整 dataset version 并链接版本详情，失败终态显示稳定错误码。
- 版本详情页对验收四态的“有效 accepted record 与最近 verdict 分开显示”有浏览器端
  断言。
- 端到端测试从触发更新到看到新版本，但不会生成 acceptance PASS 或自动运行研究。

## 11. 安全、故障与恢复语义

| 场景 | 必须结果 |
| --- | --- |
| 新供应商被误启用（本期不可得） | 不出现按源名分支的代码路径；按附录 B 的门禁拒绝，凭据缺失时无秘密回显，既有必选 lane 按原规则执行 |
| Tushare 端点超时/限额耗尽 | 单次请求失败按既有错误分类记账，raw/任务证据保留，不接受部分帧或截断结果 |
| 空响应被误读 | `index_weight` 空结果不得解释为“无成分”；`daily_basic` 空结果不得填 0 |
| `basic_factor` schema/coverage 失败 | 依据实际 tier 和新增 coverage 执行逻辑降级或阻断；不得仅凭声明字段假定已处理；覆盖行按 `status == "UNTRUSTED"` 判定 |
| 成分应有观测漏采 | 写 `membership_observation_gap`，中断该 universe coverage 并写入新定义的 `coverage_segments`/gap；不跨 gap 猜边界 |
| membership refresh 中途崩溃 | 恢复状态与磁盘不一致时不猜：报稳定错误码，由 `data index-membership recover --to <generation>` 收敛到自洽 generation（§7.0.10） |
| 新表源不可用 | 首次发布才允许空 canonical frame；已有 baseline 时 carry 已有事实并把新尾段标为不可用，或阻断本轮，绝不静默清表 |
| 运行窗口早于新表支持起点 | RESEARCH run preflight 拒绝（非零退出、错误码可见），不缩窗口、不伪造覆盖 |
| timer/CLI 撞上已持有锁 | 内层 CLI 返回稳定冲突码，外层 job 记 FAILED/`update_already_running` |
| 成分事实与既有事实冲突 | 候选不自动发布，输出差异证据 |
| 发布门禁拒绝 | job FAILED，`CURRENT` 不变，质量原因可从 CLI/job 查看 |
| 服务读期间 CURRENT 改变 | 当前请求继续读已解析版本；下一请求才可解析新 CURRENT |
| 两次更新并发 | 首个持锁，第二个 409/非零退出；不排队、不双写 |
| 服务/调度器重启 | 已完成 job 可读；孤儿 RUNNING 转 FAILED，不删日志 |
| acceptance 未签 | UI 显示 UNVERIFIED/PENDING；正式研究继续被既有门禁拒绝 |

## 12. 测试与验证策略

每阶段遵循测试先行，并至少包含：

- **单元**：adapter 转换、单位、错误分类、schema、coverage、slice hash、schema-v2 定义的
  `coverage_segments`/gap 自洽性、新表覆盖理由与窗口判定、API 参数、状态机和 systemd
  命令渲染。
- **契约**：供应商 stub、OpenAPI、data_contracts、manifest、acceptance 摘要。
- **集成**：raw → normalize → gate → publish；版本读取；CLI 子进程；单飞锁。
- **并发**：多 reader + 一个 writer；两个 writer；进程重启后的 orphan recovery。
- **安全**：secret scan、日志脱敏、路径穿越、SQL 注入、非环回绑定拒绝。
- **外部 smoke**：只在显式 external marker 下运行，使用最小日期/标的范围，不把真实
  凭据或市场数据提交到仓库。
- **失败测试**（先观察其在错误理由上失败）：`table_lineage` 与 `primary_transport`
  不符、已发布表漏记覆盖证据、跨越 gap 的窗口预检、早于新表起点的窗口预检、
  `not_fetched` 前缀/混用规则、`daily_bar` × `basic_factor` 联接重复/扩行、refresh
  三点崩溃注入与 recovery、锁冲突码到 job 原因的映射。
- **回归**：现有 publication、universe preflight、acceptance 和 research-run gate
  测试必须保持通过。

## 13. 阶段门与交付顺序

| Gate | 可开始条件 | 退出条件 |
| --- | --- | --- |
| G0 决策 | 本规格获批 | 许可记录 + ADR-021/022 + membership-hash ADR 获批 |
| G1 源接入 | G0 | 既有 Tushare 通道的 `index_weight`/`daily_basic` 探针结论成文（可用性、节奏、窗口、单位、空值、配额），端点合同回归测试通过 |
| G2 数据扩展 | G1；schema-v2 slice hash、definition registry 与 §7.5 覆盖证据扩展已落地 | PIT 候选流程和 `basic_factor` 全链路通过，mixed-lineage 真实版本可验证 |
| G3 查询服务 | G0；可与 G1/G2 并行 | GET API、安全与并发读验证通过 |
| G4 操作/调度 | G3 的状态模型已冻结 | 单飞锁、持久 job、一次计划触发通过 |
| G5 Web | G3；更新页另依赖 G4 | 四页 E2E 通过，版本/验收展示纪律与 §10.3 功能流程满足 |

G1/G2 与 G3 可由不同分支并行；G4 依赖 G3 的公共响应模型，G5 不得先于 API 契约
冻结。加入 schema-v2 slice hash、definition registry 和 coverage 执行点后，估算调整为
**8–12 周单人**；clean-room 成本已包含在区间内。若 membership-hash 迁移独立先行，
其余阶段仍可按原边界分别交付。

## 14. 迁移完成的定义

本轮只有在以下条件全部满足时才称为完成：

1. Stock 仓库不含 Panda 源码、bundle、Mongo 依赖或 Panda runtime import。
2. 所有新数据全部经过 raw provenance、canonical schema、data contract、质量门禁和
   内容寻址发布，且来源限定在 Stock 既有数据源内；无新增供应商、凭据或许可。
3. `basic_factor` 没有复制任何行情字段，也不存在被固化的 Panda 兼容宽表格式；
   `daily_bar` 与 `basic_factor` 的一对一联接一致性有测试（§7.2）。
4. `basic_factor` 主源按 ADR-022 的单候选裁决确定为唯一可用源；一个版本不混写不同
   主源，单位、transport 与 coverage 均有运行时证据（`table_lineage`、coverage 表与
   `table_fetch_coverage` 三处可核对）；升为 `anchored` 前不得声称有独立锚点。
5. CSI300/500/1000 成分事实能追到原始快照、快照边界日与 `collected_at`；多个
   `universe_id` 在同一表中分别钉 slice hash，漏采写进定义 `coverage_segments`、跨越
   gap 的窗口在 preflight 失败，冲突不会被静默跨越或覆盖；attested-boundary 近似与
   滞后上界在 UI 与证据摘要中可见。
6. 查询、操作、调度和 Web 都无法绕过人工验收或启动正式研究。
7. 所有 UI 数据和报告都能追到 dataset version；CURRENT 变化不会改变已打开视图。
8. 架构、ADR、RUNBOOK、依赖声明和测试与实际运行形态一致。
9. 新表落地不靠放宽门禁：历史起点晚于验收锚点的表能发布、能在其支持窗口内被正式研究
   使用，锚点前的窗口被稳定拒绝，且注册新表不使已发布版本在复审时缺表失败
   （§7.5 的失败测试全绿）。
10. membership refresh 有崩溃一致性协议与恢复命令（§7.0.10），三种中断点的注入测试
    通过；任何时刻磁盘上都不存在被当作可用的失配 dataset/definition 组合。

达到这些条件后，Stock 获得的是“可信数据底座上的产品化入口”，而不是一套叠加在
旁边的 Panda 副本。

## 附录 A：本期数据源范围裁定（2026-09-30，owner 裁定的证据记录）

本节记录“本期只有 Stock 既有数据源可用”这一事实的取证方式与边界。它是**日期化的
运维证据**，不是永久结论；事实变化时按附录 B 重新评估。

| 源 | 本期状态 | 取证方式 |
| --- | --- | --- |
| Stock 既有 Tushare 通道（relay / 共享 GET proxy） | **可用，本期唯一实现对象** | 既有 adapter、`project/configs/sources.yml`、探针脚本与 766 行 `custom_csi300_tw_tradable` lineage |
| Tushare 官方直连 | 视配置而定 | 已注册端点中本规格所需者为 `index_weight`、`daily_basic`；两者是否已进 `_NAMED_ENDPOINTS` 由 Phase 1 探针确认 |
| RiceQuant (`rqdatac`) | **不可得** | 本机未安装 SDK、无账号、无有效许可；未做过任何真实调用，因此没有任何 RiceQuant 数据进入本仓库 |
| xtquant / tqsdk | **不可得** | 未安装、未配置、无账号；需本地客户端或额外凭据 |

裁定内容：

1. 本期**不引入任何新供应商**，不新增凭据、许可或付费合同；所有新事实必须来自 Stock
   既有数据源。
2. 因此本规格中“多源比较”“锚点独立性”“供应商交叉验证”等设计在本期**没有可执行的
   对象**，只能作为再进入时的条件保留（附录 B），不得在实现中以空转代码或占位分支
   假装已具备。
3. 本次裁定**不评价**这些源的数据质量或商业价值；它只说明当前可获取性。历史材料里
   关于它们的评估结论仍属背景资料，不构成本期实现依据。
4. 凭据纪律不变：仓库、配置、日志、fixture、报告与本文档中都不得出现任何凭据、token
   或许可密钥；本文档只记录“有无”和环境事实。

## 附录 B：外部源的再进入条件

以下条件是**门禁**，不是待办清单；未全部满足时不得写入实现、不得为其预留 extra、
不得在代码里按源名分支。任何一项达成后，需先形成书面决策（更新附录 A 的证据行 +
新 ADR），再回到 §7.3 重新做多源裁决。

### B.1 RiceQuant（`rqdatac`）

- **获取许可**：owner 持有有效账号与许可，并确认允许本仓库用途（含内部研究/生产）。
- **明确凭据注入方式**：沿用既有凭据注入模式（环境变量/operator 安装），凭据不入库、
  不入配置模板。
- **实测取证**：用最小窗口探针实测端点可用性、字段、单位、空值语义、历史起点与配额，
  结论成文；未实测前不得引用任何历史评估数字作为实现依据。
- **独立性判定**：若要作 anchor，必须能证明其上游 lineage 与 Tushare 独立；仅数值相同
  不构成独立性。
- **许可证门禁**：其 SDK/数据许可条款须通过 §5.1 的许可证记录流程。

### B.2 xtquant

- 需要本地行情客户端常驻，属**新的运行形态**，必须单独规格评估进程边界、故障语义与
  安全影响；不得作为“顺手加个源”接入。

### B.3 tqsdk

- 需额外账号/付费与实时行情通道；再进入前须明确它相对既有 Tushare 通道的**不可替代
  性**，否则不引入（避免为已有能力付新成本）。

### B.4 通用再进入流程

任一源满足自身条件后，按顺序执行：

1. 更新附录 A 的对应证据行（日期、取证方式、结论），旧结论保留可追溯。
2. 形成新 ADR：说明为何现在引入、tier 判定、是否作 anchor、凭据与许可记录、回滚方式。
3. 回到 §7.3 重新裁决主源；单候选结论自动失效，`basic_factor` 需要新 dataset version
   与新 ADR，而不是原地换源。
4. 安装方式按 §5.4 的 operator-installed 模式：RUNBOOK 固定版本与完整性核验方法，
   核心安装不得因缺包失败。
