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
- 不保留任何会把人工项自动标为 `PASS` 的证据生成脚本（现有脚本删除，见「模块边界」）。
- **已知缺口**：不新增对操作员手工编辑后清单的凭据扫描。`secret_scan` 只覆盖
  本次生成的证据包内容；操作员写入 `summary` / `details` 的文本由审核者与
  publish 的引用净化各自把关，本期不改变这一点。

## 状态机

人工检查允许三种状态：

- `PENDING_CONFIRMATION`：尚未完成审核；可能已有自动生成的本地证据，也可能仍
  等待操作员补证据。
- `PASS`：审核者已审阅对应证据并明确确认该项结论。
- `FAIL`：审核者发现矛盾、证据不足或拒绝该项。

状态由独立的类型承载，不复用自动检查的枚举：新增 `ManualCheckStatus`
（`PENDING_CONFIRMATION | PASS | FAIL`）与 `ManualCheckResult`
（字段与 `CheckResult` 完全相同，`status` 类型不同）。自动检查继续使用二值
`CheckStatus`，`CheckResult` 仍拒绝 `PENDING_CONFIRMATION`。

`ManualCheckStatus.PASS` / `FAIL` 的取值必须与 `CheckStatus` 的同名取值逐字相同：
`acceptance_id` 是规范负载的 SHA-256，状态按取值渲染，因此只要两个模型的字段集合
一致、同名取值一致，本次改动前发布的记录 `acceptance_id` 逐位不变。

人工状态在三处被比较，本次改动必须同时覆盖，缺一处都会让记录静默失效：

1. `AcceptanceRecord.validate_decision`——ACCEPTED 要求全部人工行为
   `ManualCheckStatus.PASS`；
2. `AcceptanceRegistry.select`——`CURRENT_ACCEPTED` 解析同样要求人工行
   `ManualCheckStatus.PASS`；
3. `_manual_check_reasons`——Pending 与 Fail 分别产出稳定 reason。

另有一处相关改动但不是状态比较：`_sanitized_manual_checks` 不读取 `status`，只净化
`check.evidence`；本次仅更正该函数人工行的类型注解，不涉及状态判断。

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
2. 从该版本 manifest 的 `build_config.requested_start_date` /
   `resolved_end_date` 读取窗口，与自动检查 `date_window_completeness` 用的
   `checks._window()` **完全同口径**；绝不使用固定日期、当前日期、脚本自身目录，
   也不使用 `effective_start_date`（请求早于数据时必须体现为缺失 bar，而不是悄悄
   缩小人工审阅的窗口）。缺窗口时以稳定类别失败。
3. 在 `<root>/data/acceptance-evidence/<VERSION>/` 生成六份确定性证据文件；每份
   内容只来自该版本的标准化表、manifest 与绑定 raw evidence。凭据扫描的对象是该
   证据包自身的五份已生成文件（`secret_scan.json` 无法包含自己的哈希，故不在自身
   扫描集内），不包含操作员之后编辑的清单——证据包不能钉住钉住它的那个文件。证据
   文件内的文件名一律用最终包内名，绝不出现暂存目录名，否则哈希会随暂存路径漂移。
4. 对每份已成功写入的文件计算 SHA-256，将项目相对路径、哈希和摘要写入对应
   `PENDING_CONFIRMATION` 行；三项外部佐证行保留空 evidence。
5. 写出用户指定的 checklist。任何证据生成失败都不得创建“带虚假证据”的行：六项
   保持 Pending、evidence 为空，摘要说明生成失败的稳定错误类别（闭集：
   `window_missing` / `dataset_unreadable` / `evidence_write_failed`），清单照常
   写出，使 publish 得到 REJECTED 而不是 ACCEPTED。

证据包以同级暂存目录先写全、再逐个重算 SHA-256、最后整体换入。POSIX 无法用
`os.replace` 替换非空目录（`ENOTEMPTY`），因此换入分两步：旧包先改名让位，新包
就位后再删旧包；两步之间崩溃留下的仍是**一份完整的包**，绝不会是半包。任何失败
都不得覆盖已有完整证据包。

`build_checklist()` 保持纯函数（只重算自动检查、生成九项 Pending 人工行、不落
盘），`prepare_checklist()` 才负责生成证据包与写出清单。`publish_checklist` 与
`verify_acceptance_bindings` 的重算基准必须走纯函数：后者由 Research 预检调用，
一旦它顺手重写证据文件，随后的哈希校验就会把自己刚写的内容当成“未被篡改”，篡改
检测将形同虚设。

`prepare` 不请求供应商、不修改标准化数据、不修改 raw store 或 `CURRENT`。

## 模块边界

新增 `stock_quant.research.acceptance.evidence`，承载证据算法与写入协调：

- 从指定版本读取所需表、manifest 与窗口；
- 生成行数、缺失原因、公司行为、基准、security master 与 secret scan 六类证据；
- 写入证据包并返回已验证的 `EvidenceReference`。

对外接口是 `build_mechanisable_evidence(project_root, dataset_version)`，返回以人工
检查 code 为键、顺序同 `MECHANISABLE_CODES` 的引用映射；纯构造函数（六类证据各
一个）与常量 `EVIDENCE_FILENAMES`、`EVIDENCE_FAILURE_CATEGORIES`、
`EvidenceBuildError` 同在该模块。

人工检查的词表与分类放在模型层：`MANUAL_CHECK_CODES` 旁边新增
`MECHANISABLE_CODES`（六项可机械取证）与 `OPERATOR_ONLY_CODES`（三项需外部佐证），
二者必须恰好铺满 `MANUAL_CHECK_CODES` 且互不相交。`evidence` 从模型层读取这两个
词表，不自己重复定义。

`project/build_acceptance_evidence.py` 及其单测**删除**：它的 `apply_evidence` 会把
命中行直接写成 `PASS`，与「不自动将任何人工检查标记为 `PASS`」直接冲突；把它保留
成兼容包装器只会留下第二条写路径，让同一条漏洞重新长回来。它承担的固定 `ROOT`、
固定历史窗口与独立 checklist 回填职责一并消失，验收证据只有
`data acceptance prepare` 一条入口。

`prepare_checklist()` 负责协调上述模块并创建人工行，`build_checklist()` 是它下面
的纯函数内核，供 publish / verify 复算。模型层为人工状态新增
`PENDING_CONFIRMATION`，自动检查模型仍拒绝该值。所有持久化的 acceptance record
继续由 publish 路径校验；旧记录只含 `PASS | FAIL`，保持可读且 `acceptance_id`
逐位不变，无需迁移。

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
- 证据窗口与自动检查同口径：`requested_start_date != effective_start_date` 时取前者，
  且证据文件记录的窗口等于 `checks._window(manifest["build_config"])`。
- 同一版本重复构建证据包字节完全一致（含暂存路径不影响产物内容）。
- `prepare` 的九项人工检查初始均为 `PENDING_CONFIRMATION`；六项有本地可验证
  evidence，三项没有 evidence 且摘要要求外部佐证。
- evidence reference 均为项目内相对路径，SHA-256 与写入字节一致；生成过程不触碰
  标准化版本、raw store 或 `CURRENT`。
- 证据生成失败时不留下半包或伪造 evidence：保留上一份完整包，六行保持 Pending 与空
  evidence，摘要只含稳定失败类别。
- 自动检查仍拒绝 `PENDING_CONFIRMATION`；`ManualCheckResult` 与 `CheckResult` 字段
  集合一致，`PASS` / `FAIL` 取值逐字相同，故旧记录 `acceptance_id` 逐位不变。
- `PENDING_CONFIRMATION` 不能发布 ACCEPTED，且 REJECTED 原因稳定地包含相应代码。
- `CURRENT_ACCEPTED` 解析与 `validate_decision` 都必须认新枚举：人工行全 PASS 的
  ACCEPTED 记录仍可选、可发布。
- 只读校验路径（`verify_acceptance_bindings`）不写任何文件，篡改证据后仍能被检出。
- 全部人工项由审核者改为 PASS、每项都有可验证 evidence 后可发布 ACCEPTED。
- 既有仅含 PASS/FAIL 的 acceptance record 仍可读取与选择。
