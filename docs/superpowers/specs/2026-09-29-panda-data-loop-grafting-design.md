# Panda 数据闭环能力嫁接 Stock · 架构设计

- 日期：2026-09-29
- 最近修订：2026-09-30
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
3. RiceQuant 首批承担候选 PIT 成分事实，并作为 `basic_factor` 主源候选；主源必须与
   现有 Tushare `daily_basic` 通道完成可达性、溯源、单位和成本比较后按 §7.3 的规则
   选定。RiceQuant 日线只用于显式离线 drift audit，不加入星耀验证 lane 或公司行为
   仲裁链，不替换 Tushare 主日线，也不自动覆盖既有 CSI 官方事实。
4. `basic_factor` 只存 Stock 尚无唯一事实来源的字段。OHLCV 和 `amount` 继续只由
   `daily_bar` 提供；Panda 的十列宽表通过版本内联接视图获得，避免两份行情真相。
5. `universe_membership` 在进入多指数并存前升级为按 `universe_id` 切片钉哈希；旧的
   schema-v1 整表哈希语义保持可重放，不静默改判。
6. 常驻查询面全部为 GET。更新触发属于独立操作面，默认关闭且只允许环回地址；
   查询面和调度器都不能直接写数据集。
7. 自动化止于“发布一个通过门禁的新数据集版本”。人工验收、正式研究解锁和失败
   证据清理永不自动化。

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
| RiceQuant 端点、字段选择、代码转换和单位语义 | 作为行为输入，clean-room 重写为 Stock adapter/lane |
| `index_components` 的日期化成分数据 | 转为候选 `universe_membership` 事实，不塞回行情行 |
| `factor_base` 的市值、换手率查询知识 | 转为去重后的 `basic_factor` canonical 表 |
| APScheduler 的“到点触发”思想 | 重写为只启动 Stock CLI 的调度外壳 |
| 数据源配置、回补、进度、统计的交互模式 | 用新 API 和新 Vue 源码重建 |
| MongoDB、upsert、全局连接单例 | 不迁移 |
| `panda_data` Mongo 读取 SDK | 不迁移，由 `DatasetReader` 替代 |
| 混淆后的 Vite dist | 不复制、不反编译复用，只保留功能需求 |
| xtquant、分钟/tick、panda_factor、AI 助手 | 本轮明确排除 |

### 1.3 Panda 侧一手核验结论

本规格复核了 Panda 的 RiceQuant cleaner、工具函数、因子 cleaner 和调度器，而非只
依赖上游报告。影响设计的事实是：

- `index_components` 在 Panda 中只被当作“日期 -> 当日成员集合”读取，没有公告日、
  发布日或其他可得性元数据。
- 成分请求失败时，Panda 的异常分支写入无关的 `self.components`，三个实际成员集合
  仍为 `None`，随后全部证券被标成 `000` 并可继续 upsert。这是本规格禁止把空响应
  当成功的直接反例。
- Panda 所谓多源是 `DATAHUBSOURCE` 在 RiceQuant/Tushare 间单选，不存在多源并发
  验证或投票。因此 RiceQuant 与 Stock 既有星耀验证 lane 的关系必须由 Stock 自己
  裁决，不能声称沿用 Panda 行为。
- RiceQuant `factor_base` 复制 OHLCV、把市值/换手率缺失填 0，并对 RiceQuant 原值
  不做单位换算；Tushare cleaner 则分别对成交额和市值乘 1,000/10,000。Panda 的同一
  collection 因而没有单一可证明的跨源单位语义。
- Panda scheduler 使用日期化 job id、`replace_existing=True`，失败只记日志且没有
  跨进程单飞或持久任务状态。APScheduler 3.x 的单进程默认 `max_instances=1` 不能解决
  多进程/重启边界；Stock 不继承这些语义。
- Panda 的代码转换以数字前缀推断市场，未知值返回 `UNKNOWN` 继续流转；Stock 必须
  从 RiceQuant `order_book_id` 的完整后缀映射并对未知后缀失败关闭。

## 2. 目标、成功判据与非目标

### 2.1 目标

1. RiceQuant 能以可审计、可超时、凭据不落盘的方式进入 Stock 原始证据链。
2. CSI300/500/1000 的 RiceQuant 日期化成分可生成证据绑定的候选事实；从采集日起
   向前积累的快照可形成 PIT 区间，历史回填只有在补足当时可得性证据后才能进入正式
   `universe_membership`。
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
- 不回算、改写或重新解释已发布数据集、验收记录、schema-v1 universe definition 或
  实验的身份；schema-v2 只为新 definition 产生新身份。

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
- panda-data 子目录未发现可授予复制权的许可证；独立带 `.git` 的
  `panda-data-skill` 同样未发现许可证；
- 选择 **clean-room 重写**；
- 允许记录端点名、字段名、单位、输入输出样例和观察到的行为；
- 禁止复制函数体、注释、异常文案、前端 bundle、模板和测试 fixture；
- 行为与字段语义优先引用供应商公开文档；Panda 代码只用于验证已观察到的兼容行为。
- 新实现的评审必须能仅凭本规格、供应商公开文档和 Stock 测试解释其来源。

若 owner 改选 AGPL 或“仅内部使用”，必须先形成单独书面决策；本规格其余部分不自动
改变许可证策略。本节是工程门禁，不构成法律意见。

### 5.2 ADR

- ADR-021：允许本地常驻查询面和调度触发器，确认它们不拥有数据写语义，并规定
  环回绑定、版本钉住与人工验收边界。
- ADR-022：记录 RiceQuant 的角色、`basic_factor` 初始 tier、成员事实的双路径审核、
  `announcement_date`/`raw_effective_from` 语义、snapshot 差分 reason，以及不复制
  OHLCV 的单一真相决策；同时明确 RiceQuant 日线不加入星耀验证 lane 或 ADR-013/014
  仲裁排序。它还必须记录 RiceQuant 与当前 Tushare transport 的基础因子比较及最终
  主源/锚点裁决。
- 独立的 membership-hash ADR：引入 schema-v2 的 `universe_id` 切片哈希、旧 v1
  兼容读取、不可变定义版本注册表，以及 mixed-lineage 发布的迁移顺序。这是 G2 的
  硬前置，不塞入 ADR-022 的附带段落。
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
- `rqdatac` 先在 Phase 1 探针中核实可安装来源与再分发条件：若可从允许的包索引安装，
  放入 `ricequant` optional extra；否则沿用 tgw 的 operator-installed 模式，在 RUNBOOK
  固定版本与完整性核验方法，核心安装不得因缺包失败。

## 6. Phase 1：RiceQuant adapter 与 lane

### 6.1 角色

注册源名为 `ricequant`，首批 `required=false`：

- `basic_factor`：提供市值和换手率原始字段，作为主源候选或独立锚点；
- `index_components`：提供 CSI300/500/1000 日期化候选事实；
- `daily`：仅供显式离线 drift audit 和单位/覆盖能力探针，不进入 `data update` 的
  生产验证 lane，不参与投票或仲裁；
- `security_master` 只在为符号规范化或请求覆盖提供必要元数据时调用，不成为 Stock
  security master 的自动替换源。

这一角色划分不修改 ADR-016 的星耀验证职责，也不修改 ADR-013/014 的仲裁顺序。
未来若要把 RiceQuant 日线加入生产验证，必须另立 ADR，并定义三源冲突、缺源和票数
不足时的确定性裁决；ADR-022 不预留隐式入口。

### 6.2 Adapter 合同

- 实现既有 `DataSource`；首版**不实现** ADR-020 的可选 `fetch_batch`。若能力探针证明
  必须批量化，另按 ADR-020 的 BatchResult、逐 symbol evidence、部分失败与二分规则
  扩展，不以 RiceQuant 特例另造批量语义。请求 endpoint 必须是上述固定词汇。
- SDK 初始化和每次外部调用复用 `stock_quant.data_sources._isolated.run_isolated`；同步
  把该通用模块中仅描述 tgw/`AD_*` 的 docstring 改成供应商中立表述。超时终止整个
  子进程，不留下后台线程或半份成功帧，不复制一份 RiceQuant 专属隔离器。
- 凭据环境名固定为 `RQDATAC_USERNAME`、`RQDATAC_PASSWORD`；缺一即
  `optional_source_unavailable`，且错误文本不得包含值。
- 日线必须请求未复权口径；调整后价格不得进入 `daily_bar`。
- `transport_id` 至少区分供应商、SDK 版本和 endpoint，但不得包含账号、主机私有
  参数或 token。
- 请求集合与返回集合经过 `validate_supplier_frame`；缺符号、额外符号、重复主键、
  空批次和截断均显式分类。
- 原始响应先落 raw store，再 normalize；normalize 是纯函数，不进行 I/O。

### 6.3 规范化

- RiceQuant `XSHG/XSHE` 标识转 Stock 的 `SH/SZ` canonical symbol；只能按完整
  `order_book_id` 后缀映射，禁止按六位代码前缀猜交易所。未知市场失败关闭，不返回
  `UNKNOWN` 占位继续流转；北京市场必须通过能力探针后才声明支持。
- 日线单位统一到现有 `DAILY_SCHEMA`：成交量为股、成交额为人民币元、日期为
  `date32`，并保留 source/ingested_at。
- `limit_up`、`limit_down` 和名称变化不在本阶段扩充 `daily_bar` schema；需要使用时
  另立表规格，不塞进自由文本 metadata。

### 6.4 完成条件

- stub 契约测试覆盖初始化失败、超时、空响应、部分响应、重复行、单位转换、代码
  转换、凭据脱敏和原始快照绑定。
- 集成测试证明禁用/不可用 RiceQuant 不阻断既有必选源；启用且返回错误时其 lane
  状态可见，不制造可信行。
- 一次小窗口外部 smoke 记录调用量、墙钟、返回上限、北京市场覆盖，并分别实测
  `get_factor(market_cap)` 与 `get_turnover_rate(today)` 的原生单位和空值形态；转换
  后以已知证券/日期的数量级断言单位。这些数值进入 dated operations evidence，不
  写成永久架构事实。
- 同一 smoke 对当前配置的 Tushare transport 调用 `daily_basic`，记录可达性、真实
  transport/upstream 可归因性、配额、历史窗口、`total_mv`/`turnover_rate` 单位、空值
  和与 RiceQuant 同日同证券差异。两个入口若不能证明上游独立，不得计作互相锚定。
- 成分能力探针必须查明：供应商公开文档或其他端点是否提供公告日/调整生效日；
  `index_components(start,end)` 的键是每个交易日、每次变更日还是其他稀疏节奏。只有
  探针和公开文档都没有更强元数据时，§7.1 才采用 collection-date 向前积累语义。
- 契约测试必须注入 `index_components` 整体异常、单指数缺键、日期缺键和空成员集合，
  并断言不会产生“全市场均非成员”的成功结果。
- 契约测试增加漏采：按探针确定的应有观测节奏缺任一期时，不得静默跨 gap 推导精确
  加入或移除边界。

## 7. Phase 2：PIT 成分与 `basic_factor`

### 7.0 多 lineage 的硬前置

当前 schema-v1 `UniverseDefinition.membership_table_sha256` 钉住整张
`universe_membership` 表，acceptance 和 runner 都不按 `universe_id` 过滤；因此把
csi300/500/1000 混入同一表会让所有现有定义发生 hash mismatch。Phase 2 在写入第二个
`universe_id` 前必须先完成独立 ADR 和以下兼容迁移：

1. `UniverseDefinition` 新增 schema-v2，固定
   `membership_hash_scope: universe_id`；`membership_table_sha256` 只哈希
   `frame[frame.universe_id == definition.universe_id]` 的 canonical facts。
2. `evaluate_index_membership_evidence` 在 schema-v2 下先选择目标 slice，再对该 slice
   做 schema、事实、coverage、cardinality 和 hash 检查；空 slice 明确失败。runner
   传给 `_facts_from_membership_frame`、`resolve_memberships` 和 `UniverseResolver` 的也
   必须是同一 slice。不得出现“按 slice 验收、按整表运行”的分叉。
3. schema-v1 保持原有整表哈希行为，只用于旧 dataset/definition 重放；不得把 v1
   悄悄解释成 slice hash。
4. 建立 `configs/universes/versions/<definition_version>.yml` 不可变注册表。正式 spec
   使用显式 `universe_version` 时从注册表解析并复核内容哈希；`CURRENT` 才读取顶层
   可变指针文件。迁移前先把现有 v1 定义按版本归档，保证旧冻结运行可解析。
5. mixed-lineage 首次发布前，所有仍启用且需要在新数据集上运行的顶层定义一次性升级
   到 v2 并重算各自 slice hash。定义 version 必然变化；已发布实验保持不变，使用
   `CURRENT` 的源规格下一次冻结新版本，显式旧 version 继续走注册表。
6. `data update` 仍只 carry 已发布 membership 整表。新增/替换 lineage 只能走显式
   membership refresh：本阶段补充正式
   `python -m stock_quant data index-membership publish`，它读取 prepare 产物、按
   `universe_id` 替换目标 slice、保留其他 slice、重跑全部门禁并发布新 dataset。
   不再依赖 `project/collect_*.py` 脚本作为长期正式入口。
7. `configs/universes/*.yml` 仍非递归扫描且禁止重复 `universe_id`。新增定义的
   `coverage_start` 不得早于当前 `acceptance_start`；若确需提前，必须在同一发布补齐
   从新起点开始的 calendar、行情、主数据、coverage 和人工验收义务，不能仅靠一张
   membership 表悄悄加宽全局窗口。

### 7.1 成分事实路径

RiceQuant `index_components` 不直接进入每日行情更新。它只有每日成员集合，没有公告
元数据，因此分成两条证据强度不同的路径：

```text
raw RiceQuant response
  -> offline prepare（代码、snapshot_date、collection_date、来源规范化）
  -> historical candidate ──缺当时可得性证据──> 差异/诊断用途，不可正式发布
  -> forward snapshot ──从 collection_date 起积累──> PIT interval candidate
       -> 重叠日抽样/冲突报告 -> operator 显式 publish
       -> 新 dataset version 携带 universe_membership
```

规则：

- 映射固定为 `000300.XSHG -> csi300`、`000905.XSHG -> csi500`、
  `000852.XSHG -> csi1000`。
- 每条事实必须符合现有 `MembershipFact`，保留原始有效区间、可得日期、source、
  无凭据 URL、snapshot SHA-256 和 source-document SHA-256。
- RiceQuant 没有指数公司的独立公告文档时，`source_document_sha256` 绑定本次采集的
  canonical evidence manifest（请求参数、SDK 版本、collection time、response hash），
  不得伪装成官方公告哈希；该 manifest 只证明“何时采到什么”，不提高来源权威等级。
- **向前采集路径**：`announcement_date = collection_date`；首次见到成员时
  `raw_effective_from = collection_date`。只有按 Phase 1 探针确认的应有节奏连续取得
  相邻观测，才能用后一观测推导变化；不得用供应商返回的更早 `snapshot_date` 回填
  `raw_effective_from`，否则会触发且理应触发 `UNIVERSE_ANNOUNCEMENT_AFTER_USE`。
  这条路径从系统开始采集之日起积累可用 PIT，不承诺补出此前历史。
- **漏采规则**：任一应有观测缺失、异常、缺日期键或空集合，都写
  `membership_observation_gap` 证据并中断该 universe 的连续 coverage。gap 两端集合
  不得直接差分，不猜“前一交易日”或“采集日”是精确移除日；下一次成功观测只可开启
  新候选 coverage segment。研究窗口跨越 gap 时 preflight 必须失败，除非另有日期化
  外部证据闭合该 gap。
- **历史回填路径**：过去日期的每日成员集合可以生成带 raw snapshot 的 candidate 和
  差异报告，但 collection date 晚于 effective date 时不得进入正式 membership 表。
  只有补到能证明该成员集合在当时已经公开可得的独立材料，才能把材料日期写为
  `announcement_date` 并进入 operator publish。
- 禁止为了让历史候选通过门禁而把 `announcement_date` 伪造为 effective date；
  collection date 是采集时点证据，不是历史公告证据。
- 由相邻快照差分生成的加入/退出不能伪称 `regular_rebalance`、`correction` 或
  `delisting`。ADR-022 新增严格 reason `snapshot_observed_change`，表示“变化由相邻
  供应商快照观测得到，经济原因未知”；初始采集日的开放区间使用
  `initial_constituent`，该区间以后被观测为结束时，新数据集中的闭合事实改记
  `snapshot_observed_change`，以满足既有“initial 必须 active”的模型约束。
- CSI300 与既有官方事实重叠时生成差异报告，不自动选边、不覆盖原行。冲突需 operator
  处理并留下新证据；没有“优先 RiceQuant”的隐式规则。
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
| `source` | string, non-null | ADR-022 选定的唯一主源：`tushare` 或 `ricequant` |
| `ingested_at` | UTC timestamp, non-null | 采集时刻，不参与业务时点 |

不存 `open/high/low/close/volume/amount`。兼容 Panda 的十列视图由同一 dataset version
中的 `daily_bar` 与 `basic_factor` 按 `(trade_date, symbol)` 一对一联接得到；任一侧重复
或联接扩行均为质量错误。

### 7.3 主源裁决、契约与门禁

`basic_factor` 不预设 RiceQuant 必然胜出。ADR-022 读取 Phase 1 的同窗探针，并按以下
确定性规则选择：

1. 候选必须能绑定真实 transport/upstream、覆盖目标历史窗口、在可接受配额内稳定
   返回，并已实测市值/换手率单位与空值语义；任一项失败即无资格作主源。
2. 当前配置的 Tushare transport 与 RiceQuant 都合格时，优先 Tushare 作主源以复用
   既有 adapter、凭据与调用账本；只有能证明上游 lineage 独立时，RiceQuant 才作为
   anchor。仅“两个入口数值相同”不构成独立性。
3. 只有 RiceQuant 合格时，RiceQuant 作主源且表保持 `research_only`；只有 Tushare
   合格时同理。两者都不合格则本阶段不发布 `basic_factor`，不得降级单位或 provenance
   要求来凑表。
4. 主源在一个 dataset version 内唯一；anchor 只产生比较/coverage 证据，不把自己的
   行混入 canonical 表。换主源是新 ADR、新 raw lineage 和新 dataset version。

- `STANDARDIZED_SCHEMAS` 注册 `basic_factor`，`tables` 装配、fixture、manifest 和
  validate 路径同步更新。
- 无可证明独立 anchor 时 tier 为 `research_only`；有独立 anchor 且比较规则通过专项
  验证时才可在 ADR-022 中定为 `anchored`，不允许单源自证。
- `primary_transport` 写入实际胜出的 transport，例如 `ricequant:official` 或
  `tushare:relay`。这里的 `official` 只表示直连供应商 SDK，不表示交易所/指数公司
  官方背书。
- `incremental=last_covered_plus_1`。有 anchor 时 `conflict=downgrade`；无 anchor 时
  `conflict=block`。`coverage_shape=per_symbol_window`，并新增
  `basic_factor_coverage` canonical 表记录每个 symbol/window 的 fetched、missing、
  failed 与 anchor-conflict 状态。
- `basic_factor_coverage` 自身声明为 `core`、`coverage_shape=none`，primary transport
  与 `basic_factor` 相同；缺少 coverage 表或 coverage 与事实表不一致时整轮阻断，避免
  用缺失的“证明表”给业务表降级放行。
- 当前运行时只真正消费 `tier`、`incremental` 和 `pit`；`primary_transport`、
  `anchors`、`conflict`、`coverage_shape` 目前主要是解析期声明。本阶段不得把声明当成
  已有保障，必须补齐：发布时核对 `build_config.table_lineage.basic_factor` 与
  `primary_transport`/raw bindings；按 `coverage_shape` 要求 coverage 表；按
  `conflict` 把独立 anchor 冲突写成 `coverage_downgraded`。这些检查必须有失败测试。
- 新增表级检查至少覆盖 schema、主键唯一、有限非负市值、有限非负换手率、日历内日期、
  security master 符号、请求覆盖和联接一对一。
- 将 tier 升为 `anchored` 或 `core` 必须另有独立锚点证据和新 ADR；不得在 UI 或运行
  参数中临时升档。

### 7.4 完成条件

- 真实小窗口从 raw snapshot 走到新 dataset version，`data validate` 通过。
- 相同输入重跑版本哈希不变；单个源事实变化产生新版本，旧版本未修改。
- `basic_factor` 缺失保留 null 并降级/阻断于声明的门禁，不出现 Panda 的 fill-zero。
- 一个含至少两个 `universe_id` 的 fixture 能让各自 schema-v2 definition 通过 slice
  hash/preflight；schema-v1 仍按整表语义重放，二者没有隐式 fallback。
- 使用显式旧 `universe_version` 的 fixture 能从 definitions registry 重放；使用
  `CURRENT` 的规格明确冻结到新的 v2 definition version。
- 历史候选无法证明当时可得日期时只保留为诊断证据；向前采集事实用采集日作
  announcement/effective 下界。任何冲突都不静默发布，失败报告保留。

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

新增正式入口：

```text
python -m stock_quant operations update --root <resolved-root> [允许的 update 参数]
```

该命令同步调用 UpdateRunner、创建 job 目录、等待内部 `data update` 子进程结束并以
同一成功/失败码退出。systemd timer 只调用这个外层入口；不得直接调用 `data update`，
否则 Web 看不到定时任务记录。手工诊断仍可直接调用 `data update`，但同样受 project
lock 保护。

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

### 10.3 完成条件

- 浏览器端测试覆盖版本切换提示、UNVERIFIED 展示、分页保持版本、更新 409、更新失败
  日志、报告不存在和操作面关闭。
- 端到端测试从触发更新到看到新版本，但不会生成 acceptance PASS 或自动运行研究。

## 11. 安全、故障与恢复语义

| 场景 | 必须结果 |
| --- | --- |
| RiceQuant 凭据缺失 | optional source unavailable；无秘密回显；既有必选 lane 按原规则执行 |
| SDK 超时/崩溃 | 子进程终止，raw/任务证据保留，不接受部分帧 |
| `basic_factor` schema/coverage 失败 | 依据实际 tier 和新增 coverage 执行逻辑降级或阻断；不得仅凭声明字段假定已处理 |
| 成分应有观测漏采 | 写 `membership_observation_gap`，中断该 universe coverage，不跨 gap 猜边界 |
| 成分事实与既有事实冲突 | 候选不自动发布，输出差异证据 |
| 发布门禁拒绝 | job FAILED，`CURRENT` 不变，质量原因可从 CLI/job 查看 |
| 服务读期间 CURRENT 改变 | 当前请求继续读已解析版本；下一请求才可解析新 CURRENT |
| 两次更新并发 | 首个持锁，第二个 409/非零退出；不排队、不双写 |
| 服务/调度器重启 | 已完成 job 可读；孤儿 RUNNING 转 FAILED，不删日志 |
| acceptance 未签 | UI 显示 UNVERIFIED/PENDING；正式研究继续被既有门禁拒绝 |

## 12. 测试与验证策略

每阶段遵循测试先行，并至少包含：

- **单元**：adapter 转换、单位、错误分类、schema、coverage、slice hash、API 参数、
  状态机和 systemd 命令渲染。
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
| G0 决策 | 本规格获批 | 许可记录 + ADR-021/022 + membership-hash ADR 获批 |
| G1 源接入 | G0 | RiceQuant adapter/lane 测试与小窗口 smoke 通过 |
| G2 数据扩展 | G1；schema-v2 slice hash 与 definition registry 已落地 | PIT 候选流程和 basic_factor 全链路通过，mixed-lineage 真实版本可验证 |
| G3 查询服务 | G0；可与 G1/G2 并行 | GET API、安全与并发读验证通过 |
| G4 操作/调度 | G3 的状态模型已冻结 | 单飞锁、持久 job、一次计划触发通过 |
| G5 Web | G3；更新页另依赖 G4 | 四页 E2E 通过，版本/验收展示纪律满足 |

G1/G2 与 G3 可由不同分支并行；G4 依赖 G3 的公共响应模型，G5 不得先于 API 契约
冻结。加入 schema-v2 slice hash、definition registry 和 coverage 执行点后，估算调整为
**8–12 周单人**；clean-room 成本已包含在区间内。若 membership-hash 迁移独立先行，
其余阶段仍可按原边界分别交付。

## 14. 迁移完成的定义

本轮只有在以下条件全部满足时才称为完成：

1. Stock 仓库不含 Panda 源码、bundle、Mongo 依赖或 Panda runtime import。
2. RiceQuant 数据全部经过 raw provenance、canonical schema、data contract、质量门禁
   和内容寻址发布。
3. `basic_factor` 没有复制行情字段，Panda 兼容宽表只是一条版本内只读联接。
4. `basic_factor` 主源由 ADR-022 的双源探针规则选出；一个版本不混写不同主源，单位、
   transport、coverage 和 anchor 冲突均有运行时证据。
5. CSI300/500/1000 成分事实能追到原始快照和可得日期；多个 `universe_id` 在同一表中
   分别钉 slice hash，漏采与冲突不会被静默跨越或覆盖。
6. 查询、操作、调度和 Web 都无法绕过人工验收或启动正式研究。
7. 所有 UI 数据和报告都能追到 dataset version；CURRENT 变化不会改变已打开视图。
8. 架构、ADR、RUNBOOK、依赖声明和测试与实际运行形态一致。

达到这些条件后，Stock 获得的是“可信数据底座上的产品化入口”，而不是一套叠加在
旁边的 Panda 副本。
