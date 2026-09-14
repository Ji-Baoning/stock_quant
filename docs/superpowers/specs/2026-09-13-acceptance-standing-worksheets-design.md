# 验收佐证工作表与统一确认设计

## 目标

为九项人工检查提供统一的「工作表 + 确认」主线，使审核者的默认动作是**确认**、只在必要时修改：

- `data acceptance prepare` 生成九份候选工作表（绑定版本与证据），人工行仍一律
  `PENDING_CONFIRMATION`；
- `data acceptance confirm` 是**唯一**能把人工行变为 `PASS` / `FAIL` 的写入口，
  `--supersede` 是它唯一的更正入口（新开修订，不覆盖旧文件）；
- 三项外部佐证（`OPERATOR_ONLY_CODES`）获得机器可执行的输入契约与**确认强度**，
  没有外部输入时程序只能给出 `OPERATOR_ATTESTED`，操作员无法自行升级。

## 非目标

- 不自动把任何人工行标记为 `PASS`；`prepare` 只产出 `PENDING_CONFIRMATION`。
- 不触碰自动行（`AUTOMATED_CHECK_CODES` 九项）；`confirm` 会读取它们以完成绑定校验，
  但**永不改写**。
- 不改动 `data/acceptances/` 注册表格式、`publish` 与 `verify_acceptance_bindings`
  的语义（仍要求九项人工行全 `PASS` 且每份 evidence 可验证）。
- 不引入新的价格数据源，不为 `cross_source_price_sample` 制造强度。
- 不提供批量确认（`--all`、`--mechanisable`、任何多 code 一次确认）。逐条确认是刻意的：
  六项机械项各自签的是**不同的**候选证据（行数/来源、缺失分类、公司行动、基准、证券
  主表、密钥扫描），合并会重新引入"整包签字但不知具体签了什么"。批量正是人工审阅退化
  为橡皮图章的路径。
- 不提供删除、移动或改写已签修订的入口——包括"清空同版本签署"。更正只能靠
  `--supersede` 追加新修订。
- 签名与结论**不进入** `summary` / `details`。这两个字段不在任何哈希之内
  （`publish_checklist` 只校验 `status` 与 `evidence`；`ALLOWED_DETAIL_KEYS` 约束的
  是另一个模型，人工行的 `details` 是自由格式），因此不承担防篡改职责。

## 术语（钉死歧义）

- **自动行**：`automated_checks`，九项 `AUTOMATED_CHECK_CODES`。
- **人工行**：`manual_checks`，九项 `MANUAL_CHECK_CODES` = `MECHANISABLE_CODES`
  六项 + `OPERATOR_ONLY_CODES` 三项。**六项机械项属于人工行**，不是自动行。
- **签署**：把人工行从 `PENDING_CONFIRMATION` 变为 `PASS` 或 `FAIL`。
- **确认强度**：工作表记录的、由程序判定的确认依据等级。
- **签署者（逐项）**：某次 `confirm` 的 `--operator`，写在该次签署块里。九项**可以各有
  不同的签署者**，它就是"谁对这一项负责"。
- **清单 / 记录的 `operator_id`（顶层）**：`prepare` 的**发起与归档者**，不是每项签署者。
  它是容器级字段，`confirm` 与 `--supersede` **都不得改写**；`publish_checklist` 直接取
  它写进 `AcceptanceRecord.operator_id`，而 `acceptance_id` 覆盖该字段。**更正顶层
  `operator_id` 的入口是 `prepare --force --operator <正确 ID>`**——它本来就是该字段的
  写入者。重新发布会在只追加的注册表里留下第二条记录，旧记录不被改写；因为
  `acceptance_id` 覆盖 `operator_id`，两条记录的 id 不同，`CURRENT_ACCEPTED` 按
  `(created_at, acceptance_id)` 取最新的一条，研究运行会用到更正后的那条。注册表格式与
  `select` 语义都不需要改动。

## `prepare` 与已签修订：非破坏性恢复

`prepare` 可对同一版本重跑（恢复丢失或损坏的清单、排障）。**它永不删除、覆盖或移动
任何已签修订**——那些文件是已发布记录的证据引用目标，改动它们会让历史 `ACCEPTED`
记录永久失效。

**只读预检先于任何写入**：`prepare` 在写证据包、工作表或清单之前，先扫描该版本的工作表
目录判定是否存在生效修订。预检必须发生在任何写盘之前，否则"事后报错、但证据包已被
替换"仍是一次状态漂移。

- **默认**：发现任一 code 存在生效修订（链头）→ 在任何写入前失败，错误码
  `signed_worksheets_present`。**显式拒绝优于隐式重建**——静默把签署重置为 Pending 是
  操作员最难发现的一类损失。
- **`--force`（非破坏性恢复）**：不覆盖任何修订，也不产出全 Pending 清单。在验证**所有**
  链头修订及其绑定（修订文件哈希、`dataset_manifest_sha256`、`window`、候选证据路径与
  哈希）之后，从新建的 Pending 基线中**逐项恢复**各行的 `status` 与 evidence（工作表
  修订 + 外部输入，与 `confirm` 当初写入的集合一致），重建清单。有生效修订的 code
  **不重建**它的未签文件；若残留（上次 `confirm` 中途崩溃）则清理——未签文件不被任何
  记录引用，删除它不违反不可变性。
- 任一修订、候选证据、manifest 或配置绑定漂移 → 失败 `signed_worksheet_drift`，
  **不写任何文件**。
- **未签署**的 `<code>.md` 刷新程序区、保留人工区；不存在则新建。这是**无生效修订时的
  默认路径**（含首次 `prepare`），与 `--force` 无关。

`--force` 有恢复价值（清单丢失或损坏），但**不是清除按钮**：它只把已签修订的结论**搬回**
清单，不改变任何结论。**要改变一个已签结论，唯一的入口是 `confirm --supersede`**——
新开一份修订取代它，旧修订原样留在链上（见「工作表修订链」）。**没有删除入口**：任何
代码路径都不能删掉一份已签修订或一份外部输入。

签名状态的权威来源是**工作表修订链**，不是清单：清单可被重新生成，修订只追加不可变。

## 不变式：写路径唯一性

1. 清单 YAML 人工行的 `status` 只有两个写入者：`prepare`（写 `PENDING_CONFIRMATION`
   与机械项证据引用）与 `confirm`（唯一翻成 `PASS` / `FAIL` 的地方）。
2. 清单落盘收敛到单一 `_write_checklist_atomic(path, checklist)`，`prepare` 与
   `confirm` 共用；`confirm` 的清单写入与工作表写入必须是同一逻辑事务：工作表先
   原子就位并重算哈希，再用该哈希原子写清单。
3. 只有两个函数能产出含非 Pending 人工行的清单：`build_checklist`（九项全 Pending）
   与 `apply_confirmation`（只翻**点名的一个** code）。其余任何代码路径都不得构造
   已签署行。
4. `apply_confirmation` 拒绝：自动行、未点名的人工行、增删或重排行、任何容器级字段
   （`schema_version` / `policy_version` / `dataset_version` / 两个哈希 /
   `prepared_at` / `operator_id` / `raw_snapshot_evidence`）。`--supersede` 是它的一个
   模式，**仍然只翻点名的那一个 code**，不是第二个写入口。
   顶层的 `operator_id` 在拒绝之列是有意的：它是 `prepare` 的发起者，不是签署者
   （见「术语」）。把它开放给 `confirm` 会让"这一条是谁签的"和"这份清单是谁发起的"
   混成一个字段，还会连带改掉 `acceptance_id`。
5. 写盘只增不删，唯一例外是**未签文件** `<code>.md`（不被任何记录引用）。已签修订
   `<code>/<hash>.md` 与外部输入 `acceptance-external-inputs/<hash>/<name>` 既不改也
   不删——没有任何代码路径能删除它们。

第 3、4 条配一条回归测试：`confirm` 一轮往返后，除点名行外所有行**逐字节不变**。

## 撞车修正（本节结论优先于直觉写法）

### 一、多步签署的三态豁免

`confirm` 先按清单的 `dataset_version` 重建并校验自动项、manifest 哈希与绑定字段
（复用 `_binding_reasons` 口径），再仅变更点名 code。但"校验其余人工行"**不得**要求
它们等于重建结果——重建结果九项全 Pending，而先签的行已是 `PASS`，那会让第二个 code
永远签不动。

其余人工行**满足任一条即接受**：

1. 与重建结果逐字节一致（尚未签署）；**或**
2. 已签署（`PASS` / `FAIL`），且其 `evidence` 引用当场重新校验通过（路径在根内、
   哈希相符），且指向该 code **当前生效的修订**（绑定到本版本
   `dataset_manifest_sha256`）。

第 2 条同时封住"手工把 Pending 改成 PASS 再确认其它项"的绕过：没有对应生效修订即拒绝。

### 二、工作表修订链：一版本一 code 一条链

一个 `(dataset_version, code)` 对应一条**不可变的修订链**：

- **未签工作表**唯一且可变：`<version>/<code>.md`。`prepare` 刷新其程序区、保留人工区。
  它**永不被清单引用**（清单引用的工作表必然已签），因此可以就地刷新。
- **已签修订**只追加不可变：`<version>/<code>/<sha256>.md`。文件名即该文件自身的
  SHA-256，一经写出**永不修改、永不移动、永不删除**。
- 清单**只绑定当前生效修订**（链头）。

`confirm` 把未签工作表签成一份**新修订**：人工区 = 未签工作表的人工区逐字节 + 新签署块，
写出 `<code>/<sha256>.md`，随后删掉该 code 的未签文件。

`confirm --supersede` 在同一版本内**新开一份修订**取代当前生效修订：新文件记录
`supersedes: {path, sha256}` 指向被取代的修订，**不碰旧文件**。这是更正**该项签署内容**
的唯一途径：结论文字写错、`--operator` 填错、`PASS`/`FAIL` 判反、事后发现判据不成立。
重发一个数据版本修不了审计模型的问题，也不该为一行文字重拉数据。

它能改的**仅限于**这一项：签署块的签署者与结论、该项的决策、以及程序区随之重算的
比对结果与强度。**顶层 `operator_id` 不在其中**——那是 `prepare` 的发起者，要更正得走
`prepare --force --operator <正确 ID>`（见「术语」）。`--supersede` 也不能改自动行、
不能改其它人工行、不能改容器字段，理由同不变量 4。

**更正不是抹掉旧块**：人工区只追加，旧签署块原样留在链上（它是历史）。生效的是链头那份
修订的**最后一个**签署块；一个 code 的结论 = 该 code 链头修订的末块 `decision`。

**为什么不能覆盖 `<code>.md`**：历史 `acceptance` 记录引用的是它被签署那一刻的**路径与
哈希**。把新的已签内容写回同一路径，旧记录当场失效；把旧文件改名挪走同样失效（原路径
消失）。所以凡是签了名的路径必须是内容寻址且不可变的。这条规则可以一句话核对：
**`<code>.md` 一定未签，`<code>/<hash>.md` 一定已签。**

**`supersedes` 与 `previous_signed` 是两件事**，不要混：前者是同版本内的取代关系
（更正），后者是跨数据集版本的结转基线（增量队列的参照）。两者都只记路径与哈希，不记
人写的结论文字。

这条是硬要求，不是偏好：`verify_acceptance_bindings` 在**每次 research run** 都会重新
校验人工证据引用，改写一份已签修订会让所有指向它的已发布 `ACCEPTED` 记录当场失效。
因此**一切被引用的产物只追加、不可变**（工作表修订与外部输入同此规则）。被 supersede
的修订不再生效，但**仍是历史证据**，仍必须能通过校验。

结转使人工面收敛到增量：`trading_rule_effective_dates` 的判据是
`configs/trading_rules.yml` 的 sha256 未变（规则是静态事实，签一次长期复用）；
`exchange_calendar_sample` 只在版本窗口延伸时把**新增边界日**放进待确认队列；
`cross_source_price_sample` 每轮重新生成（数据变则结论变）。

**新版本的未签工作表怎么来的**：`prepare` 新建 `<code>.md` 时，**人工区从
`previous_signed` 修订的人工区逐字节复制**（内容结转，不是文件引用），程序区按新版本
重算。这与"复用旧文件"是两回事——旧修订仍然只属于旧版本，新文件从头到尾绑定新版本。

**"上一次已签署"如何选取**：数据集版本是内容哈希，**目录名排序无时间含义**；同版本内
的次序则由**修订链**给出（`supersedes` 是因果序，不是时钟序）：沿链回退到最近的 `PASS`
祖先。跨版本没有链可依——`confirmed_at` 是工具写的墙上时钟，可用但不是因果序——因此取
**其它版本中 `confirmed_at` 最大的 `PASS` 修订**，并把这个选择写进 `previous_signed`
（路径 + 哈希）供审计；出现并列最大值即 fail closed，不猜。

选错的代价方向是安全的：基线取得越旧，队列越大（更保守），绝不会让队列变小而漏审。
`FAIL` 是历史线索，不是可信的结转基线，拿它当基线会把"上一次拒绝了这项"误读成
"上一次确认过这项"。无 `PASS` 前置修订即视为首次签署（队列 = 全部候选内容）。

链头唯一性由程序校验：出现多个没有后继的修订（分叉），或 `supersedes` 指向缺失、哈希
不符的修订 → 失败 `revision_chain_invalid`，不猜、不重建。

结转能成立，是因为证据包重建是**字节确定**的（`prepare` 对同一版本重复构建产出完全
相同的文件与哈希），所以重跑 `prepare` 不会让已签修订钉住的候选证据哈希失配。

### 三、自动行与人工行是两套术语

`confirm` 不得触碰 `automated_checks`；六个 `MECHANISABLE_CODES` 是**人工行**，正是要被
翻成 `PASS` 的对象。实现与文档一律使用"自动行 / 人工行"两个词，禁止再用"机械项"指代
两者之一。

## 工作表

### 位置

```
<root>/data/acceptance-worksheets/<dataset_version>/<code>.md            # 未签（可变，prepare 刷新）
<root>/data/acceptance-worksheets/<dataset_version>/<code>/<sha256>.md   # 已签修订（不可变，只追加）
```

清单的 `evidence` 只指向第二类路径。第一类路径**永不出现在任何清单或 acceptance 记录
里**，所以刷新它不违反不可变性。

**必须**位于版本证据包之外。`evidence._swap_in` 替换的是整个
`data/acceptance-evidence/<version>/` 目录（旧包 rename 让位 → 新包就位 → `rmtree`
删旧包），因此放进该目录的一切——包括已签修订——都会被下一次 `prepare` 删除，
**同版本重跑 `prepare` 也会删**。

工作表与外部输入都落在被 gitignore 的 `data/` 下，与证据包同命运：备份
`data/acceptances/` 时必须一并保留，否则后续 `research run` 会在人工证据校验上失败。
此约束补进 RUNBOOK 阶段 4.5。

### 结构

程序区与人工区用成对 marker 分隔，四个 marker 常量写死在模块里：

```
<!-- ws:program:begin -->
... 程序生成，每次刷新整体重写 ...
<!-- ws:program:end -->
<!-- ws:human:begin -->
... 人工区，工具只追加，永不改写既有内容 ...
<!-- ws:human:end -->
```

程序区有两个写入者（`prepare`、`confirm`），二者都只在程序区内整体重写。人工区的**内容**
只有一个写入者（`confirm` 的追加）：`prepare` 与 `--supersede` 只做**逐字节复制**，不改
一个字符。程序区**不可手改**——`confirm` 刷新时会整体重写，手改的内容不会保留。

`confirm` 在**任何写入之前**校验，任一条不满足即拒绝写入并给出稳定错误码（fail
closed，不猜、不重建）：

- 每种 marker 恰好出现一次；
- 顺序为 `program:begin < program:end < human:begin < human:end`；
- 人工区缺失 → 拒绝，**不自动创建**（创建即猜测）；
- 出现未知 marker、重复 marker、顺序颠倒 → 拒绝。

### 程序区内容

共用绑定头（每份都有）：

- `code`、`dataset_version`、`dataset_manifest_sha256`、`window`（取自
  `checks._window(manifest["build_config"])`，与自动检查同口径）；
- `generated_at`；
- `candidate_evidence`：路径 + sha256（六项机械项指向 `prepare` 生成的证据文件；
  三项外部项指向本工作表所依据的候选内容）；
- `previous_signed`：**跨版本**结转基线——上一版同 code 最近的 `PASS` 修订的
  `dataset_version`、签署时间、操作员及其文件 sha256（首次签署时为空）；
- `supersedes`：**同版本内**被取代的修订的项目相对路径 + sha256（首次签署时为 null）。
  只记路径与哈希，不记结论文字。

`OPERATOR_ONLY_CODES` 追加：

- `external_input`：**项目内落盘后**的相对路径
  （`data/acceptance-external-inputs/<sha256>/<original-name>`）+ sha256 + `source_url`
  + `retrieved_at`。操作员提交时的原始本地路径**不写入工作表**——那种路径发布后无从
  复核，见「外部输入的落盘」；
- `comparison`：程序比对结果（逐项差异、未覆盖项计数）；
- `strength`：程序判定的确认强度；
- 待确认队列（review queue）。

### 待确认队列

队列是"本次必须人工过目的项"，由程序算出。**候选内容**指该 code 在版本侧可机器枚举
的待核项：日历项 = 窗口内全部开市日；规则项 = `configs/trading_rules.yml` 的全部规则
行；跨源项 = 该版本的价格抽样比对集合。

- 六项 `MECHANISABLE_CODES`：**恒为空**。证据由 `prepare` 生成并绑定，无外部输入，
  因此这六项恒可一条命令确认。
- 三项 `OPERATOR_ONLY_CODES` 首次签署（`previous_signed` 为空）：队列 = 全部候选内容。
- 三项后续签署：队列 = 相对 `previous_signed` 修订**新增/变更**的项（新增边界日、变更
  的规则行、未覆盖的官方摘录条目）。`cross_source_price_sample` 因数据变则结论变，
  队列每轮为全部候选内容。

- **`--supersede`：队列 = 全部候选内容，不做结转。**supersede 是更正路径，不是便捷路径；
  若它只改结论文字就能翻转，签名的意义就没了。操作员必须重新过目一遍并重新
  `--acknowledge`，且 `--conclusion` 在 supersede 时**必填**（说明为何取代前一份签署）。

队列非空时 `confirm` 必须收到 `--acknowledge <N>` 且 `N` 等于队列长度，否则拒绝。

### 确认强度

闭集两个值：`EXTERNAL_CORROBORATED`、`OPERATOR_ATTESTED`。**由程序判定，不是入参**——
操作员无法把 `OPERATOR_ATTESTED` 声称成 `EXTERNAL_CORROBORATED`。

| code | 强度 | 成立条件 |
| --- | --- | --- |
| 六项 `MECHANISABLE_CODES` | `OPERATOR_ATTESTED` | 恒定。证据由 `prepare` 生成并绑定，操作员声明已阅读 |
| `trading_rule_effective_dates` | `EXTERNAL_CORROBORATED` | 官方结构化摘录**逐条**覆盖 `configs/trading_rules.yml` 的每一条（板块、状态、生效日、涨跌幅） |
| 同上，摘录未覆盖部分配置行 | `OPERATOR_ATTESTED` | 未覆盖行进入待确认队列，绝不静默通过 |
| `exchange_calendar_sample` | `EXTERNAL_CORROBORATED` | 官方日历（**双列格式**）与数据集逐日双向比较差异为空 |
| 同上，仅单列开市日 | `OPERATOR_ATTESTED` | 单列无法区分"官方休市"与"操作员漏填" |
| `cross_source_price_sample` | `OPERATOR_ATTESTED` | 恒定（见下） |

### 外部输入的落盘

外部输入**不只记录一个任意本地路径**——那样发布后只校验工作表哈希，原始官方摘录被删除
或替换时，所谓 `EXTERNAL_CORROBORATED` 无从复核。

`confirm` 收到 `--external-input <FILE>` 时：

1. 把该文件复制为项目内**内容寻址的追加式文件**：
   `data/acceptance-external-inputs/<sha256>/<original-name>`；
2. 工作表程序区写入这个**项目内相对路径 + sha256 + `source_url` + `retrieved_at`**；
3. 人工行的 `evidence` **同时引用两份文件**：工作表本身，以及该外部输入。

同一内容重复提交是幂等的（路径相同）；不同内容落在不同路径，永不覆盖。
`publish` 与 `verify_acceptance_bindings` 会校验每一份引用，因此外部输入的缺失或替换
与工作表被改动一样，都会被检出。

### 逐 code 输入契约

统一规则：外部输入由操作员提供、**只追加不可变**（每次改动是新文件，不是编辑旧文件），
工作表钉住其 sha256；输入被换掉 → `confirm` 拒绝。

- **`exchange_calendar_sample`**：复用既有 `--calendar-csv` 格式（ISO 日期每行一条，
  `#` 注释，首个 token 生效）。严格档要求第二列 `1|0` 表示开市/休市。比对器对版本窗口
  逐日双向比较：数据集说开市而官方未列、官方列了而数据集无。
- **`cross_source_price_sample`**：比对器复用既有
  `data_quality.compare.ComparisonThresholds` 语义（绝对 0.01 / 相对 0.05% / 收盘
  0.20%），要求该版本存在 **≥2 个独立日线源**。当前版本日线仅来自 tushare（raw 快照
  清单可证），比对器**以稳定原因拒绝运行**，工作表结论固定写明"本版本为单一价格源，
  无法进行跨源比对"。该行仍可被确认，但强度只能是 `OPERATOR_ATTESTED`——不得伪装成
  通过了跨源验证。
- **`trading_rule_effective_dates`**：输入为操作员提供的官方结构化摘录（每条：板块、
  状态、生效日、涨跌幅、来源 URL）。比对器与配置逐条精确比对；官方摘录允许覆盖子集。

## 命令

```bash
# 生成/刷新九份工作表：由 prepare 一并完成，不新增入口
python -m stock_quant data acceptance prepare --version <V> \
  --operator <ID> --output checklist.yml [--force] --root .

# 确认（或拒绝）一项人工行；每次一个 code
python -m stock_quant data acceptance confirm --checklist checklist.yml \
  --code <CODE> --operator <ID> [--external-input <FILE>] \
  [--acknowledge <N>] [--fail] [--supersede] \
  [--conclusion <TEXT> | --conclusion-file <FILE>] --root .
```

`confirm` 按顺序执行：

1. 读清单，取其 `dataset_version`；按该版本重建并校验绑定（自动项、manifest 与
   质量报告哈希、`raw_snapshot_evidence`、其余人工行的三态豁免）。
2. 取点名 code 的**生效修订**（链头）与未签文件：
   - **普通 `confirm`**：目标行必须仍是 `PENDING_CONFIRMATION`，且该 code **无生效
     修订**；否则拒绝并报 `already_signed`——包括带 `--external-input` 的再次确认。
     没有这条，"人工区只追加、已签修订不改写"会被第二次确认破坏。若因此撞上
     `already_signed` 而清单里该行其实是 PENDING（上次 `confirm` 在落修订与写清单之间
     中断），那是恢复场景不是确认场景：走 `prepare --force`，不要反复重试 `confirm`。
   - **`--supersede`**：目标行必须**已签署**，且清单 evidence 指向的修订就是当前链头，
     否则拒绝并报 `nothing_to_supersede`（无前置签署可取代时不该走这条路径，应当用普通
     `confirm`）。被取代修订的字节必须与清单钉住的 sha256 相符，否则拒绝并报
     `superseded_revision_drift`——改过的旧修订不能当基线。
   - 两种路径都先校验链：分叉或 `supersedes` 指向缺失/哈希不符 → `revision_chain_invalid`。
3. 若给了 `--external-input`：按「外部输入的落盘」复制并钉住，重算程序区（比对结果与
   强度）。人工区逐字节保留。
4. 计算待确认队列（`--supersede` 恒为全部候选内容）；非空时必须 `--acknowledge <N>`
   且等于队列长度，否则拒绝。
5. 校验 marker（fail closed）。
6. 构造**新修订**：程序区整体重算（含 `supersedes` 指针），人工区 = 基线人工区逐字节
   + 新签署块（签署者 `operator_id`、`confirmed_at`、`decision`（`PASS` / `FAIL`）、
   `conclusion`、`strength`）。基线人工区来自未签文件（普通 `confirm`）或被取代的
   生效修订（`--supersede`）。签署块里的 `operator_id` 取自本次 `--operator`，**与清单
   顶层的同名容器字段无关**，两者恰好同名不代表同一件事。
7. 写新修订到 `<code>/<sha256>.md` → 校验文件名与文件实际 sha256 相符 → 删除该 code 的
   未签文件 → 用该 sha256、外部输入路径与哈希以及项目相对路径原子写清单（`status`、
   `evidence` = 工作表修订（+ 外部输入））。

第 7 步的顺序不得颠倒：先落修订、再删未签文件、最后改清单。这样任何时刻崩溃都不会出现
"清单引用了不存在的文件"；最坏情况是修订已就位而清单未更新，此时 `prepare --force` 能
把它恢复回来。反过来先改清单就会出现"status 已 `PASS`、哈希仍是 Pending 时的值"的中间
态——`publish` 与 `verify_acceptance_bindings` 都会重算哈希并判为
`evidence_hash_changed`。

`--fail` 复用同一条路径（写修订 + 挂证据 + `status=FAIL`）：`publish` 对 `FAIL` 行
同样要求证据齐备，拒绝也必须有可审计依据，而不是留下一句空的
`operator review required`。

`--conclusion` 在 `--fail` 与 `--supersede` 时**必填**（拒绝必须说明理由；取代一份已签
结论必须留下取代理由）；普通 `PASS` 时可省略，此时签署块写入稳定模板句。
`--conclusion` 与 `--conclusion-file` 互斥，后者用于较长的核对记录。

`--supersede` 与 `--fail` 可以组合（把已签 `PASS` 改成 `FAIL`，或反之），这正是它的用途
之一：签完才发现判据不成立时，不必为了改正而重发数据版本。

## 模块边界

新增 `src/stock_quant/research/acceptance/worksheet.py`：

- 常量：`WORKSHEET_DIRNAME = "acceptance-worksheets"`、
  `EXTERNAL_INPUT_DIRNAME = "acceptance-external-inputs"`、四个 marker 常量、
  `CONFIRMATION_STRENGTHS`、`WORKSHEET_ERROR_CATEGORIES`（含
  `signed_worksheets_present` / `signed_worksheet_drift` / `already_signed` /
  `nothing_to_supersede` / `superseded_revision_drift` / `revision_chain_invalid`）；
- 纯函数：九个 code 的程序区生成器（六项机械项共用封面生成器、三项外部项各一个）、
  `review_queue(...)`、`append_signature(...)`、`verify_markers(...)`、
  `revision_chain(...)`（列出某 code 的全部修订并按 `supersedes` 定链头）、
  `effective_revision(...)`、`previous_signed(...)`（沿链取最近 `PASS` 祖先）；
- 写入者：`confirm(...)`（唯一翻人工行 `status` 的函数，`--supersede` 是它的分支而非
  另一个写入口）与 `apply_confirmation(...)`（`confirm` 的纯内核，供测试直接驱动）。

`service.py` 增加共享的 `_write_checklist_atomic`；`prepare_checklist` 在写出证据包后、
写出清单前生成九份工作表。`build_checklist` 保持纯函数，不落盘。

CLI 在 `data acceptance` 组下新增 `confirm` 子命令。

## 审核者体验

拿到 `prepare` 产物后，操作员对每个 code：

1. 打开工作表，看**待确认队列**。队列空 → 直接确认；非空 → 只看那几项，必要时修改。
2. 外部项需要新证据时，提供外部输入文件（新文件），程序比对并给出强度。
3. 确认或拒绝：`confirm --code <C> --operator <ID> --acknowledge <N>`，或 `--fail`。
4. 任何一项不可信就保持未确认或设 `FAIL`；`publish` 会记录 `REJECTED`，而不是伪造
   `ACCEPTED`。
5. 某一项签错了（结论文字、`--operator` 填错、判据看错）：`confirm --supersede --code <C>
   ...` 追加一份新修订，旧修订原样留在链上。被取代的旧记录仍然可验证，只是不再生效。
6. 清单顶层的 `operator_id`（`prepare` 的发起者）填错了：重跑
   `prepare --force --operator <正确 ID>` 重建清单，再 `publish`。`confirm` 无权改它。

系统能验证文件、哈希、版本绑定、marker 与状态；它**不能**技术上证明人类确实阅读并
理解了文件。把状态改为 `PASS` 是审核者的可追责声明。

## 测试要求

- `prepare` 后九项人工行仍全为 `PENDING_CONFIRMATION`：九份未签工作表存在，且**没有任何
  已签修订**。
- 工作表位于 `data/acceptance-worksheets/<V>/` 内，**不在**
  `data/acceptance-evidence/<V>/` 内；已签修订落在 `<code>/<sha256>.md`；同版本重跑
  `prepare` 后已签修订字节不变。
- 任意有效窗口都生成九份工作表；不依赖固定日期、固定项目路径或 `effective_start_date`。
- `confirm` 一轮往返后，除点名行外所有行逐字节不变；自动行不变；行序不变。
- 手工把未签行的 `PENDING_CONFIRMATION` 改成 `PASS` 后确认其它 code → 拒绝。
- 改动工作表任一字节、替换候选证据文件、或用绑定到另一 manifest 哈希的工作表确认
  → 拒绝。
- marker 缺失、重复、顺序颠倒、出现未知 marker → 拒绝写入，且不产生半写文件。
- 窗口延伸时待确认队列只含增量项；`--acknowledge` 缺失或不等于队列长度 → 拒绝。
- 强度不可由入参决定：无外部输入恒为 `OPERATOR_ATTESTED`；单列日历文件只能
  `OPERATOR_ATTESTED`；双列且双向差异为空才得 `EXTERNAL_CORROBORATED`。
- 单一价格源版本的 `cross_source_price_sample` 工作表结论固定写明无独立第二源，
  强度恒为 `OPERATOR_ATTESTED`。
- 签署后 `publish`：九项全签且证据可验证 → `ACCEPTED`；任一项未签 → `REJECTED`，
  原因稳定包含该 code。
- 已发布版本在窗口延伸后重新 `prepare` + `confirm`，**旧版本**记录仍能被
  `verify_acceptance_bindings` 校验通过。
- 只读路径（`verify_acceptance_bindings`、`data acceptance show`）不写任何文件。
- 同版本重跑 `prepare`：已签修订逐字节不变，证据包、工作表、清单**均未被写入**（只读
  预检先于任何写入）；任一 code 存在生效修订时默认失败 `signed_worksheets_present`。
- `prepare --force`：不覆盖任何修订；清单中已签行的 `status` 与 evidence 与 `confirm`
  当初写入的**逐字节一致**；未签行仍为 `PENDING_CONFIRMATION`；残留的未签文件被清理。
- 已签修订被改动、候选证据被替换、或 `dataset_manifest_sha256` 不再匹配 →
  `prepare --force` 失败 `signed_worksheet_drift`，**不写任何文件**。
- 对已签行再次 `confirm`（含带 `--external-input`）→ 拒绝 `already_signed`；不产生新修订
  文件，已有修订与清单字节不变。
- `--external-input` 的文件被复制到 `data/acceptance-external-inputs/<sha256>/<name>`；
  人工行 evidence 同时引用工作表与外部输入；删除或替换外部输入后 `publish` /
  `verify_acceptance_bindings` 失败。
- `previous_signed` 沿链只取最近的 `PASS` 祖先：前置只有 `FAIL` 时视为首次签署（队列 =
  全部候选内容）。
- 六项 `MECHANISABLE_CODES` 的待确认队列恒为空，可无 `--acknowledge` 直接确认。
- `--fail` 缺 `--conclusion` → 拒绝；`--conclusion` 与 `--conclusion-file` 同时给出 →
  拒绝。
- **修订链**：`confirm` 写出的文件名为其自身 SHA-256；`<code>.md` 在签署后被删除；
  未签路径永不出现在任何清单或 acceptance 记录里。
- **`--supersede`**：旧修订文件字节不变、路径不变；新修订的 `supersedes` 指向它；
  清单改指新修订；**旧版本已发布的 `ACCEPTED` 记录仍能通过 `verify_acceptance_bindings`**。
- `--supersede` 在无前置签署（目标行仍 PENDING）时 → `nothing_to_supersede`；清单 evidence
  指向的修订被改动 → `superseded_revision_drift`，两种情况都**不写任何文件**。
- 人为制造分叉（两份修订都不被 `supersedes` 指向）或让 `supersedes` 指向哈希不符的路径
  → `revision_chain_invalid`，不猜链头。
- `--supersede` 的队列恒为全部候选内容（不做结转），缺 `--acknowledge` 或 `--conclusion`
  → 拒绝；`--supersede` 只翻点名的那一个 code，其余行逐字节不变。
- 被 supersede 的 `PASS` 修订不再作为当前生效工作表，但仍是历史证据：跨版本结转选
  `PASS` 时**只认链头**，不把已被取代的祖先当成现行基线。
- **顶层 `operator_id` 与逐项签署者分离**：九项用不同的 `--operator` 确认后，清单顶层
  `operator_id` 仍逐字节等于 `prepare --operator` 的值；`publish` 出的
  `AcceptanceRecord.operator_id` 与之一致，而不等于最后一项的签署者。
- `confirm` 与 `confirm --supersede` 都改不动顶层 `operator_id`（它属容器级字段，被
  不变量 4 拒绝）；`prepare --force --operator <新值>` 能改，且已签行的 `status` 与
  evidence 仍逐字节保留。
