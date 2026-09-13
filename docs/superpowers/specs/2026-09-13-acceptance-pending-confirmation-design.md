# 验收证据待确认状态设计

## 目标

将可确定性生成的验收证据融入唯一的
`python -m stock_quant data acceptance prepare` 主线，并用
`PENDING_CONFIRMATION` 明确区分“证据已生成”与“审核者已确认”。这减少人工收集
文件的负担，同时不把自动生成误当作人工审阅。

## 非目标

- 不自动将任何人工检查标记为 `PASS`。
- 不移除三项必须由外部来源佐证的人工检查。
- 不修改已发布验收记录，也不重新解释旧记录。
- 不改变自动检查的二值 `PASS | FAIL` 语义。

## 状态机

人工检查允许三种状态：

- `PENDING_CONFIRMATION`：尚未完成审核；可能已有自动生成的本地证据，也可能仍
  等待操作员补证据。
- `PASS`：审核者已审阅对应证据并明确确认该项结论。
- `FAIL`：审核者发现矛盾、证据不足或拒绝该项。

`data acceptance prepare` 创建的九项人工检查一律是
`PENDING_CONFIRMATION`。其中六项带有自动生成且可校验的证据引用；另外三项的
`evidence` 为空，摘要明确要求外部佐证：

- `exchange_calendar_sample`
- `cross_source_price_sample`
- `trading_rule_effective_dates`

下列六项生成项目相对路径、SHA-256 和摘要，但状态仍保持
`PENDING_CONFIRMATION`：

- `source_row_count_sample`
- `missing_reason_sample`
- `corporate_action_sample`
- `benchmark_sample`
- `security_master_sample`
- `secret_scan`

审核者只能在审阅证据后将项目改为 `PASS`；可补充允许的确定性 `details`，也可添加
更多证据引用。`data acceptance publish` 仅在九项均为 `PASS` 且每份 evidence 可
验证时发布 `ACCEPTED`。任一 `PENDING_CONFIRMATION` 或 `FAIL` 都使本次发布为
不可变的 `REJECTED`，原因必须精确标出未完成或失败的检查代码。

## `prepare` 主线

以下命令是生成 checklist 与可机械证据的唯一操作入口：

```bash
python -m stock_quant data acceptance prepare --version <VERSION> \
  --operator <OPERATOR_ID> --output checklist.yml --root .
```

它按固定顺序执行：

1. 读取并验证 `<root>/data/standardized/<VERSION>` 的 manifest、质量报告和
   build evidence；重跑既有九项自动检查。
2. 从该版本 manifest 的 `effective_start_date` / `resolved_end_date` 读取窗口；
   绝不使用固定日期、当前日期或脚本自身目录。
3. 在 `<root>/data/acceptance-evidence/<VERSION>/` 生成六份确定性证据文件；每份
   内容只来自该版本的标准化表、manifest、绑定 raw evidence 和本次 checklist。
4. 对每份已成功写入的文件计算 SHA-256，将项目相对路径、哈希和摘要写入对应
   `PENDING_CONFIRMATION` 行；三项外部佐证行保留空 evidence。
5. 在证据包完整写入后写出用户指定的 checklist。任何证据生成失败都不得创建
   “带虚假证据”的行：该行保持 Pending、evidence 为空，摘要说明生成失败的稳定
   错误类别。

证据包写入使用临时同级目录后原子替换；失败不得覆盖已有完整证据包或 checklist。
`prepare` 不请求供应商、不修改标准化数据、不修改 raw store 或 `CURRENT`。

## 模块边界

新增 `stock_quant.research.acceptance.evidence`，承载版本无关的纯函数和写入协调：

- 从数据版本读取所需表、manifest 与窗口；
- 生成行数、缺失原因、公司行为、基准、security master 与 secret scan 六类证据；
- 写入 evidence pack 并返回已验证的 `EvidenceReference`。

现有 `project/build_acceptance_evidence.py` 的可复用算法迁入该模块；删除其固定
`ROOT`、固定历史窗口和独立 checklist 回填职责。它不再是验收主线入口；如保留，
只能是调用主模块的兼容包装器，且必须接受 `--root` 与 `--version`。

`prepare_checklist()` 负责协调上述模块并创建人工行。模型层为人工状态新增
`PENDING_CONFIRMATION`，自动检查模型仍拒绝该值。所有持久化的 acceptance record
继续由 publish 路径校验；旧记录只含 `PASS | FAIL`，保持可读且无需迁移。

## 审核者体验

审核者拿到 checklist 后：

1. 阅读六份已生成的项目内证据，确认后将各自状态改为 `PASS`。
2. 为三项外部佐证创建项目内小型审计摘要，记录抽样范围、外部来源、比对结论和
   下载/摘录内容的哈希，然后将相应行改为 `PASS`。
3. 任一不可信或未完成项目保持 `PENDING_CONFIRMATION` 或设为 `FAIL`，随后运行
   publish 会记录 `REJECTED`，而不是伪造 ACCEPTED。

系统能验证文件、哈希、版本绑定和状态；它不能技术上证明人类确实阅读并理解了文件。
将状态改为 `PASS` 是审核者的可追责声明。

## 测试要求

- 任意有效数据窗口都生成六份证据；不依赖 2015 或固定项目路径。
- `prepare` 的九项人工检查初始均为 `PENDING_CONFIRMATION`；六项有本地可验证
  evidence，三项没有 evidence 且摘要要求外部佐证。
- evidence reference 均为项目内相对路径，SHA-256 与写入字节一致；生成过程不触碰
  标准化版本、raw store 或 `CURRENT`。
- 证据生成失败时不留下半包或伪造 evidence。
- `PENDING_CONFIRMATION` 不能发布 ACCEPTED，且 REJECTED 原因稳定地包含相应代码。
- 全部人工项由审核者改为 PASS、每项都有可验证 evidence 后可发布 ACCEPTED。
- 既有仅含 PASS/FAIL 的 acceptance record 仍可读取与选择。
