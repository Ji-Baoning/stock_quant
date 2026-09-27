# 原始快照复用的强制实时、审计隔离与保留式恢复 · 设计

- 日期：2026-09-27
- 状态：**待 owner 复核**
- 上游：[2026-09-25-raw-snapshot-reuse-design.md](2026-09-25-raw-snapshot-reuse-design.md)、
  [ADR-015](../../adr/015-raw-snapshot-reuse-for-eligible-channels.md)
- 关系：本设计扩展 ADR-015 的运行控制与故障恢复，并纠正 drift audit 会写入可复用
  raw 命名空间的未记录副作用；不改变增量窗口、通道准入、空帧策略或发布门槛

## 0. 摘要

现有复用机制的定位是正确的：它让失败续跑和同窗口重建可以少问一次供应商，而不是
让前进式更新逃避新窗口抓取。本设计不改变这一点，只补三个边界：

| 编号 | 现有问题 | 决策 | 结果 |
| --- | --- | --- | --- |
| R1 | 没有非破坏性的强制实时入口 | `data update --reuse-policy {prefer,live}`，默认 `prefer` | 可重新询问同一窗口而不删除历史证据 |
| R2 | drift audit 把重取结果写回 `data/raw`，会静默预热后续复用 | 审计只做临时序列化、指纹比较与报告，不写 canonical raw tree | 审计观测不会进入发布输入 |
| R3 | 最新候选损坏后复用永久拒绝，正常恢复依赖删除整个 request key | 精确隔离坏候选、实时重取、保存时自愈同哈希碰撞 | 不回退旧 digest，也不销毁同 key 的其余证据 |
| R4 | 复用模式与恢复动作不够可见 | 调用账本、build config 与运维记录显式记载 | 能区分“命中复用”“主动实时”“拒绝后恢复” |

本设计新增 ADR-022（编号前置修复见 §6.1）。ADR-015 的核心仍成立：raw 树继续是复用覆盖事实的唯一来源，
只取最新候选且绝不回退更旧 digest；本设计不增加 mutable reuse head 或第二份状态表。

## 1. 已核实的现状

### 1.1 复用的真实作用域

更新窗口由**已发布基线**与操作者的 `--end` 决定，而不是由重试当天决定。失败轮不
发布，基线与发布时钟都不前进，因此隔天重试同一 `--end` 仍会产生相同请求键并命中
复用。前进式更新产生新窗口、新请求键，恒定 miss；这是 ADR-015 所说的
“ask this again more cheaply”，不是缺陷。

当前获准复用的通道是 `tushare/daily`、`xingyao/daily` 与
`akshare/index_history`。`xingyao` 出厂禁用，所以默认运行面主要是主车道、
head-anchor 探针和基准指数。日历、证券主档、公司行为及懒仲裁通道继续实时。

### 1.2 恢复缺口

[raw_store.py](../../../src/stock_quant/data_sources/raw_store.py) 的
`resolve_reusable` 按 manifest 的 `response_timestamp` 选择最新候选，只复验该
候选；失败即拒绝，且有意不回退旧 digest。这个 fail-closed 选择正确，但当前拒绝只
返回 `None`，调用方无法区分普通 miss、空帧策略拒绝和物理损坏，也无法安全处置坏
候选。

此外，`RawStore.save` 遇到目标内容哈希目录已存在时会直接采用已有 manifest，不先
验证已有目录。若最新候选因截断等原因损坏，而实时重取恰好得到同一内容哈希，坏目录
仍会挡住新证据安装。因此仅增加 `--live` 不能完成恢复。

该碰撞有两种已核实的具体后果，应作为 ADR-022 的直接动机：

- manifest 可读但 `data.parquet` 被截断时，`save` 返回指向坏目录的 snapshot。本轮
  仍使用内存中的正确 `result.frame` 完成计算，但发布证据绑定坏目录；该版本以后会以
  `raw data hash mismatch` 永久验收失败。
- manifest 本身损坏时，既有目录分支的 `json.loads` 异常会穿透 `save`；而
  `_record_raw(result)` 位于 `_dispatch` 的网络重试 `try` 之外，因此一次已经成功、
  已经消耗配额的抓取会让整轮直接异常退出。

当前 [RUNBOOK.md](../../../RUNBOOK.md) 给出的失效手段是删除整个
`<source>/<endpoint>/<transport_id>/<request_key>/`。该目录也可能被已发布版本的
`build_config.raw_snapshots` 引用；删除会使历史版本的验收复验变成
`snapshot_unverifiable`。缓存失效与审计证据销毁因此被不必要地绑在一起。

### 1.3 drift audit 的跨机制副作用

[project/drift_audit.py](../../../project/drift_audit.py) 从已存 manifest 重建相同
`DataRequest`，实时抓取后调用 `RawStore.save`。新结果因而进入同一 request key 下的
canonical raw 命名空间，并带有较新的 `response_timestamp`。后续同窗口更新会把这
份审计观测当作最新可复用答案。

ADR-015 所称“drift audit 独立于 reuse”目前只保证审计不**读取**兄弟 digest，未保证
它不**写入**兄弟 digest。审计的职责是重取、比较和写 dated operations record；静默
改变下一轮发布输入不属于该合约。

## 2. 设计原则

1. **强制实时不等于不留证据。** `live` 跳过复用消费，但成功抓取仍按正常规则保存
   raw snapshot，并可被之后的 `prefer` 轮使用。
2. **拒绝最新候选后绝不回退。** 无论拒绝原因是什么，同一次解析都不使用更旧 digest；
   需要答案时转实时抓取。
3. **只隔离已证明无效的精确目录。** 空帧策略拒绝不是损坏，不移动；物理或结构校验
   已失败的候选才离开 canonical raw tree。
4. **审计观测不成为发布候选。** drift audit 使用与正式保存相同的序列化和哈希实现，
   但生命周期止于临时目录和审计报告。
5. **raw 树仍是唯一复用状态。** quarantine 是取证保留区，不参与候选发现，不维护
   “当前指针”。

## 3. D1：显式 reuse policy

### 3.1 CLI 与领域参数

`stock-quant data update` 新增：

```text
--reuse-policy {prefer,live}
```

- 默认 `prefer`：保持 ADR-015 现状。获准通道先解析可复用候选，命中则零网络调用；
  miss 或 refusal 才实时抓取。
- `live`：所有原本获准复用的调用点都不消费已有快照，直接实时抓取；非获准通道的
  行为不变。
- 不增加 `--no-reuse`、`--refresh` 等同义开关；`live` 是唯一强制实时拼写。
- 参数进入 update request / pipeline options，禁止在四个 `_dispatch` 调用点继续
  各自硬编码最终策略。调用点只声明该通道是否 eligible 及 `allow_empty`；运行级策略
  由 `_dispatch` 统一组合。

`live` 的含义是“不把旧候选当作本轮答案”，不是“不检查存储完整性”。保存前仍执行
§5 的碰撞校验；联网前必须对该 request key 做完整性扫描并隔离坏候选，但不得复用
扫描中发现的有效候选。

`live` 只能重问 pipeline 根据已发布基线、`--end` 和各表策略算出的**当前契约窗口**，
不能把任意历史起止日变成可抓窗口。若操作者给出的显式窗口偏离契约，F1 仍按现状写
`NOT_FETCHED` / `operator_explicit_window` 并跳过；`--reuse-policy live` 不覆盖、缩窄
或绕过该判定。需要重判更深历史时应使用对应表已经定义的窗口旋钮，而不是把 `live`
当作历史回填接口。

### 3.2 运行语义

| 情形 | `prefer` | `live` |
| --- | --- | --- |
| 无候选 | 实时抓取并保存 | 实时抓取并保存 |
| 最新候选有效 | 复用 | 忽略其内容，实时抓取并保存 |
| 最新候选为空且调用点不允许空 | 不移动，记 policy refusal，实时抓取 | 不消费候选，实时抓取 |
| 最新候选物理/结构损坏 | 隔离精确候选，实时抓取并保存 | 不消费；联网前完整性扫描先隔离，保存时再做同哈希碰撞校验 |
| 实时抓取失败 | 沿用当前 required/optional 失败语义 | 同左；不得回退缓存 |

`live` 不承诺供应商返回不同字节。若新观测与既有有效候选内容相同，内容寻址保存可以
去重，但本轮账本仍记作 `fetched` 而不是 `reused`，因为网络调用确实发生了。

### 3.3 可观察性契约

复用策略必须进入以下两处：

- `call_ledger.json`：升级为有显式版本的顶层 envelope，固定包含
  `schema_version: 2`、`reuse_policy`、`sources`。`sources` 下每个 source 继续完整保留
  ADR-020 已接受的 `calls`、`endpoints`、`reused`、`transport`；其中 `transport` 的
  `sessions` / `code_queries` 语义与计数时机不变。本设计只在 source row 新增
  `reuse_refusals` / `recovered`。账本**现状已经在每个终态写出**，本设计不改变写入
  时机，只把裸 `write_text` 改成同目录临时文件 + `os.replace` 的原子写；
- 发布版本 `dataset_manifest.json` 的 `build_config`：新增独立键
  `raw_snapshot_reuse_policy`，现有 `raw_snapshot_reuse` 计数结构不改。

本数据更新流程目前没有独立 run manifest；本设计不为一个字段新建重复的运行清单。
`call_ledger.json` 是 run-level artifact，负责记录未发布失败轮；build config 负责记录
成功发布轮。两者以同一个 `run_id` 关联。

选择独立 build-config 键而不是把 `mode` 混入 `raw_snapshot_reuse`，是为了保持现有
`{source: {endpoint: counts}}` 契约同构。该新键属于 provenance，参与版本身份；即使
所有计数为零也必须存在。

建议的 refusal/recovery 稳定码由 ADR-022 冻结，至少包括：

- `empty_not_reusable`：策略拒绝，不隔离；
- `manifest_unreadable`、`request_identity_mismatch`、
  `evidence_verification_failed`：结构或物理拒绝，可隔离；
- `quarantine_failed`：隔离失败，禁止把同一坏候选当作成功恢复。

日志与账本只记录相对路径、内容哈希和脱敏错误类别，不写供应商凭据或原始请求秘密。

## 4. D2：drift audit 对 canonical raw tree 只读

### 4.1 指纹接口

从 `RawStore.save` 抽出单一的序列化/指纹原语，例如：

```python
@dataclass(frozen=True)
class RawFingerprint:
    file_sha256: str
    response_sha256: str
    row_count: int
    columns: tuple[str, ...]

def fingerprint(self, result: FetchResult) -> RawFingerprint:
    """Serialize exactly as save() does, but leave no canonical raw artifact."""
```

具体命名可在实现时贴合现有代码，但必须满足：

1. `fingerprint` 与 `save` 共用同一序列化函数、列顺序、parquet 参数及
   `response_sha256` 算法，不能复制两套近似逻辑。
2. 中间文件位于受控临时目录，成功、失败和中断后都清理。
3. 不创建、修改或删除 `data/raw/**` 与 `data/raw_quarantine/**`。
4. drift 分类继续使用当前等价的内容身份；不得因为改成临时指纹而降低比较严格度。

### 4.2 审计流程

drift audit 的新流程固定为：

```text
读取已发布证据 → 重建请求 → 实时 fetch → 临时 fingerprint
    → 与已发布 fingerprint 比较 → 写 dated operations record
```

它不调用 `RawStore.save`，也不提供“顺便写入 raw”的开关。若操作者希望新观测成为可
发布输入，应显式运行 `data update --reuse-policy live`；审计与取数由两个命令边界
区分。

审计报告继续包含新旧哈希、分类和请求身份，并新增 `raw_store_mutated: false` 的稳定
字段，防止未来无意恢复副作用。该字段是报告事实，不是通过运行后猜测得出。

## 5. D3：结构化解析、精确隔离与保存自愈

### 5.1 解析结果不是 `None` 二义性

`RawStore.resolve_reusable` 改为返回封闭结果类型，语义等价于：

```python
ReuseHit(snapshot, frame)
ReuseMiss()
ReuseRefused(refusals: tuple[RejectedCandidate, ...])
```

- `Hit`：最新候选通过 `verify_evidence` 及调用点空帧策略。
- `Miss`：没有适用的 modern-layout 候选；legacy 四段布局仍不参与复用。
- `Refused`：存在最新候选或无法排序的结构损坏项，且本轮不能安全采用已有答案。每条
  `RejectedCandidate` 都带精确候选身份、`reason_code`、`quarantine_eligible` 和
  脱敏 detail，不能只给布尔值。

解析保持 ADR-015 的 newest-only 规则：对可排序候选选中最新一个后即终止。若它被
拒绝，本轮直接进入实时路径，不尝试较旧 digest。

候选发现中无法解析 manifest 的目录不能凭 `response_timestamp` 参与“最新”排序。
它们作为结构损坏项单独报告并可隔离；在同次请求中，即使还发现一个有效旧候选，只要
存在无法确定时序的结构损坏项，也不得据此静默认定旧候选为最新答案，应实时抓取。

### 5.2 隔离布局与原子性

隔离单位是精确的 `file_sha256` 候选目录，不是整个 request-key 目录。目标布局为：

```text
data/raw_quarantine/
  <source>/<endpoint>/<transport_id>/<request_key>/<file_sha256>/<quarantine_id>/
    snapshot/          # 原候选目录原样保留
    quarantine.json    # 检测与搬移记录
```

`quarantine_id` 由 UTC 时间与随机后缀组成，只用于避免碰撞，不参与复用选择。
`quarantine.json` 至少记录原相对路径、候选身份、稳定 reason code、检测时间、run id、
校验器错误类别和预定动作；不得复制凭据。隔离是否完成以同目录下 `snapshot/` 是否
存在为准，预写记录本身不宣称 rename 已成功。

本项目的更新/发布运维契约禁止并发改动同一数据根，supplier fetch 也按顺序执行；本
设计依赖该既有单写者约束，不为被禁止的双写场景增加锁、事务或 TOCTOU 回滚协议。
在同一项目数据根内使用原子 rename，流程是：

1. 完整校验证明候选无效；
2. 创建唯一 quarantine 目标，并先原子写入 `quarantine.json`，记录检测事实、原路径与
   预定目标；
3. 将精确候选目录原子 rename 到该目标的 `snapshot/`；
4. 仅在 rename 成功后进入实时抓取/保存。若 rename 失败，预写记录可以保留为失败
   尝试，canonical 候选不动。

若隔离失败，不删除、不覆盖，也不继续假装恢复成功。该请求产生
`quarantine_failed`；required/optional 的传播沿用该通道现有失败等级。实现必须防止
源目录在 rename 前已经消失这一廉价竞态：此时重新解析当前 canonical 状态，不盲目
覆盖目标，也不尝试把任何目录搬回。除此之外不承诺支持两个更新进程并发写同一数据根。

quarantine 不让坏证据“重新有效”。若某已发布版本引用该候选，它在隔离前已经不能
通过 `verify_evidence`；隔离后的 ops 记录只保留取证链，不尝试改写历史 manifest，
历史版本仍应验收失败。

### 5.3 哪些拒绝可以隔离

| 拒绝 | 隔离 | 理由 |
| --- | --- | --- |
| 空帧且 `allow_empty=False` | 否 | 字节与 manifest 可以完全有效，只是不满足复用策略 |
| manifest 无法解析/缺失必要身份 | 是 | 不能证明属于任何可复用请求 |
| manifest 请求身份与所在 request key / 本次请求矛盾 | 是 | canonical 路径与自描述证据不一致 |
| 双哈希、parquet 可读性或 `verify_evidence` 失败 | 是 | 已证明物理证据不可验证 |
| 仅仅比另一个 digest 更旧 | 否 | 它是历史观测，不是损坏 |

任何新增 reason code 都必须显式归入“policy”或“integrity”类别；默认类别为不隔离，
避免把未知业务拒绝升级成磁盘搬移。

### 5.4 `RawStore.save` 的碰撞自愈

保存不能再把“目标目录存在”当作“目标证据有效”。当计算出的 canonical
`file_sha256` 目录已存在时：

1. 用将要保存的 request identity 对既有目录执行完整 `verify_evidence`；
2. 验证通过才可内容寻址去重并返回已有 snapshot；
3. 验证失败则按 §5.2 隔离该精确目录；
4. 重新从临时产物原子安装新目录；
5. 安装后再次验证，再向调用方返回 snapshot。

该规则覆盖最危险的同哈希恢复：磁盘截断只改变文件现状，不改变目录名，实时重取原始
内容会计算出相同目录名。先隔离再安装才能真正自愈。

`live` 路径还必须处理“坏候选是另一 digest 且时间戳异常靠后”的情况：联网前调用只做
完整性检查、不返回复用答案的 `inspect_candidates`，并在**本轮**隔离发现的 integrity
refusal；扫描到的有效兄弟 digest 保留原位。同哈希碰撞仍只能在抓取内容算出哈希后由
`save` 处理。禁止把完整性扫描推迟到保存后或下一轮。

## 6. D4：治理与运维

### 6.1 ADR 编号修复与 ADR-022

当前树中 `020-batched-validation-channel.md` 与
`020-suspension-proof-grid-is-the-fetch-window-and-its-seam.md` 同时以 accepted 状态占用
020，索引也有两条 020。001–020 已无其他空号，所以不能在保留该冲突的同时让本设计
继续占 021。注册本设计前必须先做纯治理编号修复：

1. `020-batched-validation-channel.md` 保留 020，因为其上游已接受 spec 明确预登记
   “新增 ADR-020”，且本设计还要引用它的账本契约；
2. suspension proof-grid ADR 改为 021，同时更新标题、文件名、索引及树内引用；
3. 本设计登记为 `docs/adr/022-raw-reuse-control-and-recovery.md`。

编号修复不改变两个既有 ADR 的正文决定或 accepted 状态，必须单独通过链接与索引检查。
ADR-022 必须记录：

- `prefer/live` 的语义、默认值与可观察性；
- drift audit 不写 canonical raw tree；
- refusal 分类、精确 quarantine、保存碰撞自愈；
- newest-only / no-fallback 继续有效；
- quarantine 是取证区而非第二份复用状态；
- 对 ADR-015 decision 3 的另一项显式取代：现状会把 unreadable manifest 静默
  `continue`，新规则把“无法确定时序的结构损坏项”视为污染整个 request key，本轮不
  采用任何旧候选、隔离坏项并实时抓取；
- 被否决项：回退旧 digest、删除整个 request key 作为常规恢复、mutable reuse head、
  drift audit 可选写盘开关、把 `live` 解释为不留 raw 证据。

ADR-022 对 ADR-015 是**部分取代**：取代“删除是唯一失效手段”、drift audit 可向同一
raw namespace 写新观测的既有结果，以及“unreadable candidate 不是证据、可静默跳过”
的候选发现语义；保留通道准入、精确请求匹配、最新候选、空帧策略、证据重验与不回退
原则。ADR-015 只追加指向 ADR-022 的 amendment note，不重写其历史正文。

### 6.2 与 ADR-020 批量设计的叠加关系

本设计建立在 ADR-020 之上，不与它并列定义另一套账本或审计 schema：

- call-ledger v2 的 `sources.<name>.transport` 原样承载 ADR-020 的 `sessions` /
  `code_queries`；`reuse_policy` 是 envelope 元数据，`reuse_refusals` / `recovered` 是
  每源新增计数。所有既有调用方、读取器、fixtures 与精确相等测试都必须迁移到 envelope，
  不能只改 `call_ledger.py`。
- drift audit 仍按 ADR-020 以原批次形状 replay `BatchRequestEvidence`，但 replay 结果只
  走本设计的临时 fingerprint：既不 `RawStore.save`，也不新增
  `BatchRequestEvidence`。原批次输入形状保留，审计输出与证据存储隔离。
- 批量 lane 继续执行“逐请求先复用、miss 才入批”；`live` 只令所有 eligible 请求成为
  联网候选，不改变批大小、二分、empty/refused 映射或 transport 计数。

### 6.3 RUNBOOK

运行手册把同窗口重新询问的首选动作改为：

```bash
stock-quant data update ... --reuse-policy live
```

删除整个 request-key 目录从常规恢复路径移除。RUNBOOK 另增 quarantine 检查步骤：

- 根据 run id / request key 查看 `quarantine.json`；
- 保留 `raw_quarantine`，不得把其中目录手工复制回 canonical raw tree；
- 需要发布新观测时运行 `--reuse-policy live`；
- 若隔离失败，先处理权限、磁盘空间或意外的第二写者，再重跑，不以删除规避；
- 历史版本若已引用坏候选，明确记录它仍不可复验，不能用新快照改写旧 manifest。

手工删除只作为极端、显式批准的证据处置，继续要求先查发布绑定并写 dated operations
record；它不再是“让缓存失效”的普通办法。

## 7. 接线范围

预计修改：

- `src/stock_quant/cli.py`：解析 `--reuse-policy`；
- `src/stock_quant/data_pipeline.py`：传递运行策略、消费结构化解析结果、计数并落
  provenance；
- `src/stock_quant/data_sources/raw_store.py`：统一序列化/指纹、结构化解析、完整性
  扫描、quarantine、保存碰撞校验；
- `src/stock_quant/data_model/call_ledger.py`：在 ADR-020 的 transport row 之上增加 v2
  envelope 并原子写；同步修改全部仓库内读取器、fixtures 和精确形状断言；
- `project/drift_audit.py`：改用临时 fingerprint，不调用 `save`；
- `RUNBOOK.md`、`docs/architecture/data-flow.md`、ADR-015 amendment note、ADR-022、
  ADR-020/021 编号修复及 `DECISIONS_INDEX.md`；
- 上述行为的单元、集成与治理测试。

未来批量校验通道必须消费同一个 pipeline-level reuse policy，并保持“先复用后批量联网”
的算法；本设计不实现批量抓取，也不改变
[2026-09-27-batched-validation-channel-design.md](2026-09-27-batched-validation-channel-design.md)
的批量边界。

## 8. 验收标准

### 8.1 reuse policy

1. 不传参数时行为与当前 `prefer` 一致，已有成功快照可命中且不联网。
2. `live` 面对有效候选仍发起网络调用，成功结果保存；账本记 `fetched`，不记
   `reused`。
3. `live` 抓取失败时不回退已有候选。
4. 成功轮的 call ledger 与 build config、失败轮的 call ledger 都能无歧义证明本轮
   策略；默认值也落盘。
5. 非 eligible 通道行为和调用次数不因该参数改变。

### 8.2 drift audit

1. 对同一 fixture 审计前后递归比较 `data/raw` 与 `data/raw_quarantine`，路径集合和
   文件哈希完全不变。
2. 审计仍能对 unchanged / changed / unavailable 产生现有等价分类。
3. 审计抓到的新字节不会被随后 `prefer` 更新发现为候选。
4. 临时序列化失败与正常结束都不遗留临时文件；报告写
   `raw_store_mutated: false`。

### 8.3 恢复与 no-fallback

1. 最新候选损坏、旧候选有效时，本轮绝不回退旧候选，而是隔离最新候选并实时抓取。
2. 空帧策略拒绝产生 `empty_not_reusable`，但候选不被隔离。
3. 坏候选被移动到 forensic quarantine，其他 digest 保留；记录可从新路径追溯原路径。
4. 隔离后实时抓取成为下一轮可复用的最新有效候选。
5. 坏目录与新抓结果同 `file_sha256` 时，保存能够隔离坏目录并安装、复验新目录。
6. `live` 面对另一 digest 的未来时间戳坏候选时，必须在**本轮联网前**隔离它；不得把
   拒绝或额外实时抓取推迟到下一轮 `prefer`。
7. quarantine 失败时不删除、不覆盖、不回退，并产生稳定失败码。
8. 源目录在 rename 前消失时会重新解析，不覆盖 quarantine 目标；双更新进程并发写
   同一数据根仍是明确不支持的运维违规。

### 8.4 治理与回归

- ADR frontmatter、索引、链接与行数通过 context-governance 检查；
- CLI help / parser、raw store、call ledger、drift audit、pipeline 的最近测试通过；
- 全套测试通过后方可宣称实施完成；
- 若治理测试仍被无关的 ADR-019 YAML WIP 阻断，实施记录必须区分本变更结果与既有
  blocker，不得修改 ADR-019 来掩盖失败。

## 9. 迁移与兼容

- 默认策略是 `prefer`，现有命令行为不变。
- 不迁移、不重写已有 raw snapshots 或 manifests；旧快照首次被考虑复用时按新解析器
  检查。
- `call_ledger.json` 升为 schema v2，但保持现有“每个终态都写”的时机；仓库内读取器
  必须同时接受现有无版本 sources map 与 v2 envelope，写入器只写 v2。v2 中
  `sources.*.transport` 完整保留 ADR-020 字段。历史 run 保持原样。
- `build_config.raw_snapshot_reuse` 原形状不变，只增并列的
  `raw_snapshot_reuse_policy`；历史版本缺键按 `prefer` 的 legacy 事实解释，但不得回写。
- quarantine 无需预建；首次发生 integrity refusal 时创建。
- drift audit 的只读变化立即生效，不为旧审计产出的 sibling digest 做自动清理；它们
  已是合法保存的历史观测，仍按 newest-only 规则参与选择。

## 10. 明确不做

- 不改变增量窗口计算、published calendar 时钟或请求键算法。
- 不扩大 `REUSABLE_CHANNELS`，不改变各调用点 `allow_empty`。
- 不回退到更旧 digest，不建立“已知好版本”指针或 mutable reuse head。
- 不把 quarantine 当成发布证据路径，不让验收去 quarantine 搜索缺失快照。
- 不自动修复或改写已发布 dataset manifest。
- 不在 drift audit 增加写盘模式。
- 不删除现有历史 sibling digest，也不做 raw 树垃圾回收。
- 不在本设计中统计历史 run 的实际命中率；那是独立的只读运营分析。

## 11. 实施顺序约束

0. 在可访问的真实项目数据根上只读统计 `data/runs/*/call_ledger.json` 的 `reused`；报告
   总 run 数、含复用 run 数、按 source/endpoint 的命中数与无法解析的 legacy 账本数。
   ADR-022 接受前必须附该结果；若没有可访问的数据根，则明确记为 evidence unavailable，
   由 owner 决定是否豁免，不得把“未找到”写成“命中率为 0”。该统计不修改任何数据。
1. 修复 ADR-020 重号，再落 ADR-022 与结构化 refusal 类型，并以测试冻结 no-fallback。
2. 抽取共享序列化/指纹原语，改造 drift audit，证明 raw tree 零变更且保留 ADR-020
   batch replay 形状。
3. 实现精确 quarantine 与 `RawStore.save` 碰撞自愈，覆盖同哈希边界；不实现并发事务。
4. 接入 pipeline-level reuse policy 与 CLI，再在 ADR-020 账本字段之上升级
   envelope/provenance。
5. 更新 RUNBOOK、架构文档和治理索引，运行最近测试与全套测试。

该顺序避免出现“CLI 已承诺可强制实时，但同哈希坏目录仍无法自愈”的中间状态。
