# Panda 数据闭环能力嫁接 Stock · 架构设计

- 日期：2026-09-29
- 状态：**待 owner 复核**
- 上游分析：`/home/ji/work/program/pandaAI/docs/panda数据闭环迁移stock可行性分析.md`
- 目标：在不引入第二套数据真相、不削弱 Stock 治理边界的前提下，吸收
  Panda 独有的数据源知识、定时更新能力和数据管理交互模式。
- 交付形态：本文件是跨阶段的总规格。每个实施阶段必须有独立计划、独立验收和
  可运行交付物；不得把六个阶段合成一次大改。

## 0. 决策摘要

采用**嫁接**，不采用代码或平台整体搬运。

```text
RiceQuant 接入知识 ─┐
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
3. RiceQuant 首批只承担可替换的供应通道：日线交叉验证、基础因子和候选 PIT
   成分事实；不在首批替换 Tushare 主日线，也不自动覆盖既有 CSI 官方事实。
4. `basic_factor` 只存 Stock 尚无唯一事实来源的字段。OHLCV 和 `amount` 继续只由
   `daily_bar` 提供；Panda 的十列宽表通过版本内联接视图获得，避免两份行情真相。
5. 常驻查询面全部为 GET。更新触发属于独立操作面，默认关闭且只允许环回地址；
   查询面和调度器都不能直接写数据集。
6. 自动化止于“发布一个通过门禁的新数据集版本”。人工验收、正式研究解锁和失败
   证据清理永不自动化。

## 1. 背景与已经核实的仓库事实

### 1.1 Stock 当前边界

- `DataSource` 协议只有 `name` 和 `fetch(request) -> FetchResult`，但运行时源注册、
  必选角色和 lane 装配仍由 `data_pipeline.py` 的封闭结构控制。
- `data update` 已是幂等入口；取数失败或质量门禁拒绝时不改 `CURRENT`。
- `DatasetPublisher` 是唯一发布器。版本由表记录和规范化 `build_config` 共同哈希，
  发布目录不可变，`CURRENT` 原子替换。
- `DatasetReader.open(version)` 只读打开指定版本；正式消费者在运行开始后不得继续
  跟随 `CURRENT`。
- `configs/sources.yml:data_contracts` 对每张 `data_update` 发布表强制声明 tier、
  transport、冲突语义、PIT、coverage 和增量策略；未声明表以
  `unregistered_table` 失败关闭。
- `universe_membership` 已有不可变事实模型、快照/文档哈希和研究前置门禁，不需要
  另造指数成分字符串列。
- 正式研究验收包含 10 项自动检查和 9 项人工检查。9 项人工结果必须由 operator
  签署并绑定证据；调度进程无权生成 PASS。
- `overview.md` 的 “no server, no scheduler, no database” 是当前架构事实，而非
  已采纳 ADR 的永久禁令。引入服务与调度必须用 ADR 正名，并在同一变更更新事实层。

### 1.2 Panda 可吸收与不可吸收的部分

| Panda 能力 | 本设计处理 |
| --- | --- |
| RiceQuant 端点、字段选择、代码转换和单位语义 | 作为行为输入，clean-room 重写为 Stock adapter/lane |
| `index_components` 的日期化成分数据 | 转为候选 `universe_membership` 事实，不塞回行情行 |
| `factor_base` 的市值、换手率查询知识 | 转为去重后的 `basic_factor` canonical 表 |
| APScheduler 的“到点触发”思想 | 重写为只启动 Stock CLI 的调度外壳 |
| 数据源配置、回补、进度、统计的交互模式 | 用新 API 和新 Vue 源码重建 |
| MongoDB、upsert、全局连接单例 | 不迁移 |
| `panda_data` Mongo 读取 SDK | 不迁移，由 `DatasetReader` 替代 |
| 混淆后的 Vite dist | 不复制、不反编译复用，只保留功能需求 |
| xtquant、分钟/tick、panda_factor、AI 助手 | 本轮明确排除 |

## 2. 目标、成功判据与非目标

### 2.1 目标

1. RiceQuant 能以可审计、可超时、凭据不落盘的方式进入 Stock 原始证据链。
2. CSI300/500/1000 的 RiceQuant 日期化成分可生成证据绑定的候选 PIT 事实，并经
   显式审核后进入 `universe_membership`。
3. `basic_factor` 作为新 canonical 表发布，且不会复制 `daily_bar` 已拥有的事实。
4. 用户能从 Web 查看版本、表、质量报告、验收状态和已生成报告，所有数据展示都
   明确绑定版本哈希。
5. 用户能在本机操作面触发一次更新并查看持久化状态/日志；调度器能以相同语义
   定时触发更新。
6. 新能力不改变正式研究的人工验收前置条件。

### 2.2 总体验收判据

- 同一组 RiceQuant 原始响应重复导入，产生相同表内容和相同 dataset version。
- RiceQuant 不可用、超时、返回残缺集合或 schema 漂移时，新版本不伪装成功，
  `CURRENT` 保持不变，失败证据可追踪。
- Web 响应包含 `dataset_version`；一次请求解析版本后不再读 `CURRENT`。
- 更新并发请求只有一个能进入执行态，其他请求明确返回 conflict，不产生双发布。
- 调度器成功只能产生新数据集候选；没有任何路径能自动写人工 PASS、
  `CURRENT_ACCEPTED` 或启动正式 `research run`。
- 发布后的既有版本目录在服务运行、更新触发和失败恢复后字节不变。

### 2.3 非目标

- 不兼容 Panda 的 Mongo collection 或 `panda_data` Python API。
- 不实现通用任意 SQL HTTP 端点。
- 不做远程多租户、用户系统、权限管理或公网部署。
- 不在 UI 中编辑因子、配置、验收结论或研究规格。
- 不接入 xtquant，不引入分钟/tick，不迁移 Panda 因子引擎或 AI 功能。
- 不借本项目重构整个 `data_pipeline.py`；仅提取新 lane 所需的窄接口。
- 不改变已发布数据集、验收记录或实验的身份算法。

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
2. **原始证据优先**：normalize 前必须先保存 RiceQuant 原始响应及无凭据的请求元数据。
3. **失败留证**：adapter、更新任务和调度触发失败必须保留稳定错误码、请求范围和日志；
   不得把空响应当作成功。
4. **钉住版本**：每个读取请求解析一次明确 version；`CURRENT` 只是请求入口别名，
   响应必须回显解析出的哈希。
5. **无自动验收**：操作面只允许 `data update`，不得暴露 acceptance confirm/publish、
   research run 或删除接口。
6. **凭据隔离**：RiceQuant 凭据仅从环境读取，不进入配置、日志、异常、原始 manifest、
   job request 或前端响应。
7. **无 Panda 运行时依赖**：Stock 不 import Panda 包，不复制其源码或构建产物。

## 5. Phase 0：决策、许可与架构正名

### 5.1 许可证门禁

实施前保存一次带 commit/hash 的许可证核验记录：

- PandaAI 根仓与 panda_quantflow 当前可见许可证为 AGPL-3.0；
- panda-data 子目录未发现可授予复制权的许可证；
- 选择 **clean-room 重写**；
- 允许记录端点名、字段名、单位、输入输出样例和观察到的行为；
- 禁止复制函数体、注释、异常文案、前端 bundle、模板和测试 fixture；
- 新实现的评审必须能仅凭本规格、供应商公开文档和 Stock 测试解释其来源。

若 owner 改选 AGPL 或“仅内部使用”，必须先形成单独书面决策；本规格其余部分不自动
改变许可证策略。本节是工程门禁，不构成法律意见。

### 5.2 ADR

- ADR-021：允许本地常驻查询面和调度触发器，确认它们不拥有数据写语义，并规定
  环回绑定、版本钉住与人工验收边界。
- ADR-022：记录 RiceQuant 的角色、`basic_factor` 初始 tier、成员事实的审核路径，
  以及不复制 OHLCV 的单一真相决策。
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

## 6. Phase 1：RiceQuant adapter 与 lane

### 6.1 角色

注册源名为 `ricequant`，首批 `required=false`：

- `daily`：作为未复权日线验证源，不替换主日线；
- `basic_factor`：提供市值和换手率原始字段；
- `index_components`：提供 CSI300/500/1000 日期化候选事实；
- `security_master` 只在为符号规范化或请求覆盖提供必要元数据时调用，不成为 Stock
  security master 的自动替换源。

### 6.2 Adapter 合同

- 实现既有 `DataSource`，可选实现 `fetch_batch`；请求 endpoint 必须是上述固定词汇。
- SDK 初始化和每次外部调用均在可终止子进程中执行；超时终止整个子进程，不留下
  后台线程或半份成功帧。
- 凭据环境名固定为 `RQDATAC_USERNAME`、`RQDATAC_PASSWORD`；缺一即
  `optional_source_unavailable`，且错误文本不得包含值。
- 日线必须请求未复权口径；调整后价格不得进入 `daily_bar`。
- `transport_id` 至少区分供应商、SDK 版本和 endpoint，但不得包含账号、主机私有
  参数或 token。
- 请求集合与返回集合经过 `validate_supplier_frame`；缺符号、额外符号、重复主键、
  空批次和截断均显式分类。
- 原始响应先落 raw store，再 normalize；normalize 是纯函数，不进行 I/O。

### 6.3 规范化

- RiceQuant `XSHG/XSHE` 标识转 Stock 的 `SH/SZ` canonical symbol；未知市场失败关闭，
  不静默过滤，北京市场必须通过能力探针后才声明支持。
- 日线单位统一到现有 `DAILY_SCHEMA`：成交量为股、成交额为人民币元、日期为
  `date32`，并保留 source/ingested_at。
- `limit_up`、`limit_down` 和名称变化不在本阶段扩充 `daily_bar` schema；需要使用时
  另立表规格，不塞进自由文本 metadata。

### 6.4 完成条件

- stub 契约测试覆盖初始化失败、超时、空响应、部分响应、重复行、单位转换、代码
  转换、凭据脱敏和原始快照绑定。
- 集成测试证明禁用/不可用 RiceQuant 不阻断既有必选源；启用且返回错误时其 lane
  状态可见，不制造可信行。
- 一次小窗口外部 smoke 记录调用量、墙钟、返回上限、北京市场覆盖和单位实测；这些
  数值进入 dated operations evidence，不写成永久架构事实。

## 7. Phase 2：PIT 成分与 `basic_factor`

### 7.1 成分事实路径

RiceQuant `index_components` 不直接进入每日行情更新。流程为：

```text
raw RiceQuant response
  -> offline prepare（代码、日期、区间、来源规范化）
  -> membership candidate + evidence manifest
  -> 重叠日抽样/冲突报告
  -> operator 显式 publish
  -> 新 dataset version 携带 universe_membership
```

规则：

- 映射固定为 `000300.XSHG -> csi300`、`000905.XSHG -> csi500`、
  `000852.XSHG -> csi1000`。
- 每条事实必须符合现有 `MembershipFact`，保留原始有效区间、公告/可得日期、source、
  无凭据 URL、snapshot SHA-256 和 source-document SHA-256。
- API 只给每日集合而不给事件公告时，`announcement_date` 只能取“供应商声明该集合
  对外可得的日期”，不能倒填指数生效日；二者无法证明时该批候选拒绝发布。
- CSI300 与既有官方事实重叠时生成差异报告，不自动选边、不覆盖原行。冲突需 operator
  处理并留下新证据；没有“优先 RiceQuant”的隐式规则。
- CSI500/1000 在首次正式使用前仍须通过现有 universe preflight 和 real-data
  acceptance。PIT 能力不等于官方权威，UI 必须显示实际 `source`。

### 7.2 `basic_factor` canonical schema

表主键为 `(trade_date, symbol)`，初版只含：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `trade_date` | date32, non-null | 事实对应交易日 |
| `symbol` | string, non-null | Stock canonical symbol |
| `market_cap` | float64, nullable | 当日总市值，人民币元；源缺失保持 null，禁止填 0 |
| `turnover_rate` | float64, nullable | 无量纲比例，`0.01` 表示 1%；源值按实测单位显式换算 |
| `source` | string, non-null | `ricequant` |
| `ingested_at` | UTC timestamp, non-null | 采集时刻，不参与业务时点 |

不存 `open/high/low/close/volume/amount`。兼容 Panda 的十列视图由同一 dataset version
中的 `daily_bar` 与 `basic_factor` 按 `(trade_date, symbol)` 一对一联接得到；任一侧重复
或联接扩行均为质量错误。

### 7.3 契约与门禁

- `STANDARDIZED_SCHEMAS` 注册 `basic_factor`，`tables` 装配、fixture、manifest 和
  validate 路径同步更新。
- 初始 `data_contracts` tier 固定为 `research_only`：市值和换手率在首批没有独立
  锚点，不能靠单源自证为正式研究输入。
- `primary_transport=ricequant:official` 仅表示直连 RiceQuant SDK 的 transport 类别，
  不表示交易所/指数公司官方背书；ADR-022 必须写清这一词义。
- `conflict=downgrade`、`coverage_shape=per_symbol_window`、
  `incremental=last_covered_plus_1`。
- 新增表级检查至少覆盖 schema、主键唯一、有限非负市值、有限非负换手率、日历内日期、
  security master 符号、请求覆盖和联接一对一。
- 将 tier 升为 `anchored` 或 `core` 必须另有独立锚点证据和新 ADR；不得在 UI 或运行
  参数中临时升档。

### 7.4 完成条件

- 真实小窗口从 raw snapshot 走到新 dataset version，`data validate` 通过。
- 相同输入重跑版本哈希不变；单个源事实变化产生新版本，旧版本未修改。
- `basic_factor` 缺失保留 null 并降级/阻断于声明的门禁，不出现 Panda 的 fill-zero。
- 候选成员事实存在冲突或无法证明可得日期时不发布，失败报告保留。

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
  `dataset_version` 永远是完整哈希。
- `limit` 默认 100、最大 500；不得提供任意 SQL、任意文件路径或 DuckDB 文件下载。
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

### 9.2 单飞与持久化状态

- `data update` CLI 自身取得 project-local advisory lock，覆盖手工 CLI、Web 与调度
  三个入口；锁粒度是一个 project root。
- 已有运行时返回 HTTP/CLI conflict，不排队，不杀死当前任务。
- 每个操作任务写入 `data/service/jobs/<job_id>/`：不可变 `request.json`、追加式
  `stdout.log`/`stderr.log`、原子替换的 `status.json`。
- 状态词汇固定为 `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED_BY_SHUTDOWN`；服务
  重启发现 RUNNING 但无存活子进程时改记 `FAILED`，原因码为 `orphaned_process`，
  不删除目录。
- job id 不进入 dataset version；成功状态记录 CLI 输出的 run id 和 dataset version。

### 9.3 操作 API

操作面是独立 router/process，默认禁用；启用时仍只绑定环回地址：

| 方法与路径 | 行为 |
| --- | --- |
| `POST /api/v1/update-jobs` | 校验并启动一次 `data update`；已运行返回 409 |
| `GET /api/v1/update-jobs` | 返回持久化任务摘要 |
| `GET /api/v1/update-jobs/{id}` | 返回状态与脱敏日志尾部 |

不提供取消、重试到成功、删除日志、验收或研究端点。一次失败后的再次运行必须由新的
显式请求或下一次计划触发产生新 job id。

### 9.4 调度

- 首版采用 APScheduler 或 systemd timer 均可，但调度层只负责调用 UpdateRunner；
  选择必须在 ADR-021 落一项，不得同时维护两套生产调度。
- 若采用仓库内调度器，配置只有时区、cron、enabled 和允许的 update 参数；时区固定
  默认 `Asia/Shanghai`，配置校验失败则进程不启动。
- `max_instances=1`，missed run 只记录一次 MISSED，不补跑成任务风暴。
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

### 10.3 完成条件

- 浏览器端测试覆盖版本切换提示、UNVERIFIED 展示、分页保持版本、更新 409、更新失败
  日志、报告不存在和操作面关闭。
- 端到端测试从触发更新到看到新版本，但不会生成 acceptance PASS 或自动运行研究。

## 11. 安全、故障与恢复语义

| 场景 | 必须结果 |
| --- | --- |
| RiceQuant 凭据缺失 | optional source unavailable；无秘密回显；既有必选 lane 按原规则执行 |
| SDK 超时/崩溃 | 子进程终止，raw/任务证据保留，不接受部分帧 |
| `basic_factor` schema/coverage 失败 | 按 research_only 契约降级并阻止正式消费；FATAL 仍阻断整轮 |
| 成分事实与既有事实冲突 | 候选不自动发布，输出差异证据 |
| 发布门禁拒绝 | job FAILED，`CURRENT` 不变，质量原因可从 CLI/job 查看 |
| 服务读期间 CURRENT 改变 | 当前请求继续读已解析版本；下一请求才可解析新 CURRENT |
| 两次更新并发 | 首个持锁，第二个 409/非零退出；不排队、不双写 |
| 服务/调度器重启 | 已完成 job 可读；孤儿 RUNNING 转 FAILED，不删日志 |
| acceptance 未签 | UI 显示 UNVERIFIED/PENDING；正式研究继续被既有门禁拒绝 |

## 12. 测试与验证策略

每阶段遵循测试先行，并至少包含：

- **单元**：adapter 转换、单位、错误分类、schema、coverage、API 参数、状态机、cron。
- **契约**：供应商 stub、OpenAPI、data_contracts、manifest、acceptance 摘要。
- **集成**：raw → normalize → gate → publish；版本读取；CLI 子进程；单飞锁。
- **并发**：多 reader + 一个 writer；两个 writer；进程重启后的 orphan recovery。
- **安全**：secret scan、日志脱敏、路径穿越、SQL 注入、非环回绑定拒绝。
- **外部 smoke**：只在显式 external marker 下运行，使用最小日期/标的范围，不把真实
  凭据或市场数据提交到仓库。
- **回归**：现有 publication、universe preflight、acceptance 和 research-run gate
  测试必须保持通过。

## 13. 阶段门与交付顺序

| Gate | 可开始条件 | 退出条件 |
| --- | --- | --- |
| G0 决策 | 本规格获批 | 许可记录 + ADR-021/022 获批 |
| G1 源接入 | G0 | RiceQuant adapter/lane 测试与小窗口 smoke 通过 |
| G2 数据扩展 | G1 | PIT 候选流程和 basic_factor 全链路通过，真实版本可验证 |
| G3 查询服务 | G0；可与 G1/G2 并行 | GET API、安全与并发读验证通过 |
| G4 操作/调度 | G3 的状态模型已冻结 | 单飞锁、持久 job、一次计划触发通过 |
| G5 Web | G3；更新页另依赖 G4 | 四页 E2E 通过，版本/验收展示纪律满足 |

G1/G2 与 G3 可由不同分支并行；G4 依赖 G3 的公共响应模型，G5 不得先于 API 契约
冻结。估算保持 **6–10 周单人**，clean-room 成本已包含在区间内。

## 14. 迁移完成的定义

本轮只有在以下条件全部满足时才称为完成：

1. Stock 仓库不含 Panda 源码、bundle、Mongo 依赖或 Panda runtime import。
2. RiceQuant 数据全部经过 raw provenance、canonical schema、data contract、质量门禁
   和内容寻址发布。
3. `basic_factor` 没有复制行情字段，Panda 兼容宽表只是一条版本内只读联接。
4. CSI300/500/1000 成分事实能追到原始快照和可得日期；冲突不会静默覆盖。
5. 查询、操作、调度和 Web 都无法绕过人工验收或启动正式研究。
6. 所有 UI 数据和报告都能追到 dataset version；CURRENT 变化不会改变已打开视图。
7. 架构、ADR、RUNBOOK、依赖声明和测试与实际运行形态一致。

达到这些条件后，Stock 获得的是“可信数据底座上的产品化入口”，而不是一套叠加在
旁边的 Panda 副本。
