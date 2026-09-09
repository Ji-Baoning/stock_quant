# 真实数据验收注册表设计

## 目标

把当前仅存在于运行手册中的真实数据验收清单，升级为可执行、不可变、可追溯的验收注册表。正式 Research 运行只能消费已经通过当前验收规则的数据集版本，并在实验产物中固定记录所使用的 `acceptance_id`。

本设计解决“某条回测能否证明其数据版本经过真实来源验收”的问题，不证明供应商数据绝对正确，也不替代后续的时点化股票池、样本外验证或实盘准入。

## 核心决策

采用独立于标准数据集的不可变验收注册表：

```text
data/acceptances/<dataset_version>/<acceptance_id>/acceptance.json
```

验收记录引用固定数据集及其证据哈希，不修改数据集本身。这样避免“为写入验收结论而重新发布数据集、重新发布又产生新版本”的循环。通过、失败和被后续规则淘汰的历史记录均保留，不覆盖、不删除。

Research 模式要求目标 `dataset_version` 至少存在一份：

- 结论为 `ACCEPTED`；
- 数据集清单与质量报告哈希仍匹配；
- 验收规则版本等于当前要求版本；
- 所有自动检查通过；
- 所有必需人工检查明确为 `PASS`。

门禁从结论与规则版本匹配的候选中按 `created_at` 取最近一条并整体复核上述条件；数据集与证据不可变，任一哈希不匹配都直接判为门禁失败，而不是改选更早的记录。

Engineering 模式可在无有效验收记录时继续诊断，但实验评价保持 `UNTRUSTED`，不能发布为正式绩效结论。

## 验收记录模型

第一版规则标识固定为 `real-data-v1`。`acceptance.json` 至少包含：

```text
schema_version
policy_version
acceptance_id
dataset_version
dataset_manifest_sha256
quality_report_sha256
created_at
operator_id
automated_checks[]
manual_checks[]
raw_snapshot_evidence[]
decision
reasons[]
```

### 身份与不可变性

`acceptance_id` 是验收有效载荷的 SHA-256 内容哈希。计算身份时排除 `acceptance_id` 本身，其余字段全部进入规范 JSON；键排序、UTF-8、固定分隔符和稳定数组排序保证相同内容产生相同身份。

`operator_id` 是操作者提供的本地标识，仅用于审计，不是密码学签名。`created_at` 使用带时区的 UTC 时间，由发布命令在判定时生成；准备阶段的时间戳只留在清单里，不进入记录身份。相同数据由同一操作者在不同时间重复验收，会产生不同记录并全部保留。

发布使用临时目录加同文件系统原子重命名。若同一 `acceptance_id` 已存在且内容相同，操作幂等成功；若内容不同，视为身份冲突并失败。

### 自动检查

自动检查项使用稳定代码、`PASS|FAIL` 状态、摘要和结构化详情。`real-data-v1` 至少包含：

1. `dataset_manifest_integrity`：数据集清单存在，所有声明的表文件、行数及 SHA-256 匹配。
2. `quality_report_integrity`：质量报告存在、哈希固定，重新执行 `DataPipeline.validate(version)` 不产生 FATAL 或发布阻断项。
3. `required_table_coverage`：必需表完整；启用时包括 `adjusted_bar` 与公司行为隔离表。
4. `date_window_completeness`：目标区间、最新完整交易日及回退标记可解释，股票与两个基准覆盖满足规则。
5. `security_master_evidence`：股票池每个标的都有匹配的证券主数据覆盖证据，事实与行情边界一致。
6. `corporate_action_evidence`：股票池和验收窗口均有可信公司行为覆盖；事实、隔离项与连续价格引用可以对账。
7. `raw_snapshot_traceability`：数据集构建引用的每个供应商结果都能追溯到原始快照哈希，文件内容仍匹配。
8. `source_role_health`：所有必需数据角色成功；可选角色失败会保留解释，但不得被伪装成成功。

自动检查只读取指定的不可变数据集及其原始证据，不解析 `CURRENT`，也不联网。自动检查的摘要与结构化详情必须确定性：不得包含时间戳、运行耗时或本机绝对路径，否则相同证据会产生不同的内容身份。

### 数据集构建证据

现有数据集清单只保存表哈希和 `run_id`，不足以证明真实来源请求。数据更新发布时必须把以下脱敏构建证据写入 `dataset_manifest.json` 的规范化 `build_config`，并纳入 `dataset_version`：

```text
origin=data_update
pipeline_contract_version
requested_start_date
requested_end_date
resolved_end_date
resolved_end_is_fallback
source_status[]
raw_snapshots[]
```

`source_status` 只保存来源名、是否必需、成功状态和稳定原因代码；不得保存 Token、请求响应或异常堆栈。每条 `raw_snapshots` 保存 `source`、`endpoint`、`request_key`、`file_sha256` 和 `manifest_sha256`，按这些字段稳定排序并去重。验收时必须在 `data/raw/<source>/<endpoint>/<request_key>/<file_sha256>/` 精确找到对应快照，同时验证数据文件、清单文件和清单内来源字段；不能只按文件哈希搜索，因为多个空响应可能具有相同文件内容。验收记录的 `raw_snapshot_evidence[]` 保存同一组五字段快照绑定，与 `build_config.raw_snapshots` 完全对账；发布时逐条重新定位快照并复核数据与清单两个哈希，不一致即 FAIL。Bootstrap 数据集标记 `origin=bootstrap`，不能通过 `real-data-v1` 的真实来源验收。

旧数据集缺少这些构建证据时，自动检查明确返回 `dataset_build_evidence_missing`，不能通过补写旁路文件升级为已验收版本；必须重新运行数据更新发布新版本。

### 人工检查

人工项目使用稳定代码、`PASS|FAIL`、说明和证据引用。所有项目必须显式填写，缺失、空字符串或其他状态均不能发布：

1. `exchange_calendar_sample`：抽查目标窗口的开市/休市日与官方交易所日历一致。
2. `source_row_count_sample`：抽查标准表、原始快照和预期交易日数的行数关系。
3. `missing_reason_sample`：停牌、上市前、退市后及主源缺失等分类符合事实。
4. `cross_source_price_sample`：抽查最大跨源差异，记录解释或失败结论。
5. `corporate_action_sample`：逐条核对选定分红送转事件与公告，包含紧凑配股措辞等已知边界。
6. `benchmark_sample`：两个基准的日期与数值覆盖合理。
7. `trading_rule_effective_dates`：涨跌停、ST 和板块规则的生效日期经官方来源复核。
8. `security_master_sample`：上市、退市、状态和来源快照相符。
9. `secret_scan`：验收产物和准备提交的文件未包含凭证或供应商敏感载荷。

证据引用保存为项目根目录下的相对路径或外部公开来源标识，并同时保存证据摘要 SHA-256。验收目录不复制市场数据、供应商响应、凭证或个人敏感信息。

## 命令与数据流

### 准备清单

```bash
python -m stock_quant data acceptance prepare \
  --version <DATASET_VERSION> \
  --operator <OPERATOR_ID> \
  --output <CHECKLIST.yml> \
  --root <PROJECT_ROOT>
```

命令固定读取显式版本，运行全部自动检查，并生成待填写 YAML。自动检查失败时仍生成清单，便于诊断；清单记录失败原因，但不能被发布为 ACCEPTED。

### 发布验收记录

```bash
python -m stock_quant data acceptance publish \
  --checklist <CHECKLIST.yml> \
  --root <PROJECT_ROOT>
```

发布时不信任准备阶段的自动结果，而是重新执行自动检查并重新计算数据集、质量报告、原始快照和人工证据哈希。若检查后任何绑定内容变化、清单版本不匹配、人工项目未完成或任一项目 FAIL，发布一份 `REJECTED` 记录；命令以非零状态退出并打印 `acceptance_id` 与原因。全部通过则发布 `ACCEPTED` 记录并以零状态退出。

失败记录同样先原子持久化，再返回非零状态，确保错误可审计。CLI 输出不得打印原始供应商载荷、凭证或完整本机绝对路径。

### 查询验收历史

```bash
python -m stock_quant data acceptance show \
  --version <DATASET_VERSION> \
  --root <PROJECT_ROOT>
```

命令按 `created_at`、`acceptance_id` 稳定排序，显示全部记录、结论、规则版本和失败原因。不存在记录时明确返回“未验收”，不把缺失解释为通过。任一记录损坏或内容哈希不匹配时，命令直接失败并指明损坏的记录，不跳过、也不把损坏解释为通过。

## Research 门禁

实验规格新增 `data_acceptance_id`。可编辑规格可写 `CURRENT_ACCEPTED`，也可显式固定一个验收 ID；正式冻结规格不得保留占位符。Research Runner 在解析 `dataset_version` 后、计算实验身份前解析验收记录：

1. 查找该版本的验收记录，不读取其他数据版本的记录。
2. 验证记录内容哈希与当前 `policy_version`，并复用同一套自动检查实现重新执行，以复核数据集清单哈希、质量报告哈希、原始快照和本地人工证据哈希仍然匹配；人工抽查本身不重新执行，Runner 也不重复实现检查逻辑。
3. 对 `CURRENT_ACCEPTED`，先按结论为 `ACCEPTED` 且规则版本匹配过滤候选，再按 `created_at`、`acceptance_id` 稳定取最近一条并执行第 2 步的整体复核；复核失败即门禁失败，不回退更早的记录——数据集不可变，任何哈希漂移都意味着证据被篡改或衰减。显式 ID 则只验证该记录，不自动替换。
4. 把解析出的具体 ID 写回冻结规格。`data_acceptance_id` 参与实验 ID 与运行输入摘要计算，防止同一实验身份因验收记录变化而产生不同产物。
5. 将 `acceptance_id`、`policy_version`、操作者和验收时间写入 run manifest、`metrics.json`、实验 manifest 与 HTML 报告。

没有有效记录、只有 REJECTED 记录、证据被修改或规则版本过期时，Research 在任何因子/组合/回测计算前失败。由于此时尚未形成实验 ID，系统在 `data/runs/preflight_acceptance_<uuid>/run_manifest.json` 写入独立的 FAILED preflight 记录：`experiment_id=null`、固定的数据版本、`failed_stage=acceptance` 和脱敏原因；该目录不进入实验注册表。

Engineering 模式允许 `data_acceptance_id` 为空，并在产物中把验收状态记为 `UNVERIFIED`（这是数据验收状态标签，不是实验评价值）；即使显式引用有效 ACCEPTED 记录，实验评价也维持其 diagnostic-only 的 `UNTRUSTED` 规则。

## 组件边界

- `research/acceptance/models.py`：Pydantic 验收记录、检查项和清单模型，只定义契约与规范序列化。
- `research/acceptance/checks.py`：纯读取的自动检查器，不负责 CLI、发布或 Research 编排。
- `research/acceptance/registry.py`：内容寻址、原子发布、读取、完整性验证和有效记录选择。
- `research/acceptance/service.py`：准备清单、发布判定、供 Research 门禁复用的验收绑定复核及 CLI 用例编排。
- `cli.py`：参数解析和面向操作者的安全输出，不承载验收规则。
- `research/spec.py`：声明并冻结 `data_acceptance_id`，确保它进入实验身份。
- `research/runner.py`：只调用注册表门禁并固定选中的验收身份，不重复自动检查实现。

这些组件不得修改标准数据集、`CURRENT`、原始快照或实验注册表。

## 错误与兼容性

- 旧数据集仍可读取、校验和在 Engineering 模式诊断，但默认不存在有效验收记录；缺少构建证据时不能接受。
- 自动检查异常转换为稳定的 FAIL 代码与脱敏说明；编程错误仍使运行失败，不能伪装成业务拒绝。
- 人工证据路径不存在、越出项目根目录或内容哈希变化时，该检查为 FAIL。
- 验收规则升级必须使用新的 `policy_version`；旧记录永久保留，但不满足新 Research 门禁。
- 不提供“跳过某项”“强制接受”或修改已发布记录的开关。
- 一份 ACCEPTED 记录只证明当时指定规则及证据均通过，不构成投资建议或实盘许可。

## 测试与验收

单元测试覆盖：模型严格校验、规范 JSON、内容身份、清单完整性、自动/人工状态聚合、路径约束、哈希变化、规则版本过期及秘密脱敏。

注册表集成测试覆盖：原子发布、幂等重放、身份冲突、ACCEPTED/REJECTED 历史共存、稳定选择、文件损坏和并发发布。

CLI 集成测试覆盖：准备清单、成功发布、失败记录先落盘后非零退出、查询历史、损坏记录导致查询失败、安全输出及明确版本要求。所有测试使用合成数据且默认离线。

Research 集成测试覆盖：

- 无验收记录时在任何阶段执行前失败，并留下 preflight 失败记录；
- 只有 REJECTED、规则过期或哈希不匹配时失败；
- 有有效 ACCEPTED 记录时完成并把相同 `acceptance_id` 写入全部正式产物；
- Engineering 模式可诊断但评价仍为 UNTRUSTED；
- 同一冻结数据集和验收记录重复运行生成一致实验身份与审计字段。

功能验收标准：任取一份正式实验，可以从其 manifest/metrics 找到唯一 `acceptance_id`，从验收记录验证固定数据集与证据哈希，并从每个人工项目追到明确结论和证据；破坏任一绑定证据后，新的 Research 运行必须在计算前拒绝。

## 非目标与后续

本次不建设多人审批、数字签名、远程验收服务、市场数据版权归档或自动抓取官方网页。个人项目第一版只记录本地 `operator_id` 与证据哈希；若未来进入团队或受监管环境，再扩展签名与角色权限。

该子项目完成后，回测可信性的下一规划项是时点化股票池与历史成分边界。
