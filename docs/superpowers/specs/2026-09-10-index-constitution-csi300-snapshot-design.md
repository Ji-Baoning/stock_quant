# index-constitution csi300 静态快照与冻结股票池设计

## 背景与问题

正式 Research 需要按信号日解析的 `csi300` 历史成分，但当前所有获取渠道都断了：

- **中证指数官方**（`csindex-home/search/search-content`）被 Aliyun WAF 拦截，`collect_csi300_official.py` 的重试退避也过不去；已落盘的公告存档只有 24 份，且只覆盖 2005-04 至 2006-12，附件 Excel 未下载。
- **新浪历史成分表**（`collect_sina_membership.py`）抓全了 7 页，但产出的 `csi300.parquet` 只有 243 行、范围仅 2005-04-08 至 2009-07-01、`status` 全为 `removed`、当前成分为 0 只。该表是"历史成分"（只列已剔除股票），不是完整成分表。
- **tushare `index_weight`** 需要 2000 积分档 token，已提交的 token 调不动。

`unliftedq/index-constitution`（PyPI `index-constitution==1.0.0`，MIT）把 `csi300` 全历史**打包进 wheel**，读取不联网，绕开上述全部网络问题。实测覆盖 2005-04-08 至 2026-06-12、1225 条纳入区间、949 个唯一代码、当前 300 只。

## 目标

把 `index-constitution` 的 `csi300` 数据冻结为可复现的静态快照，并在其上建立一条可审计的构建链，产出一个能进入正式 Research 的冻结股票池定义。

## 非目标

- **不迁移项目到 Python 3.12 / pandas 3**（见"运行时隔离"）。
- 不建设 `csi500` 等其他指数的接入。
- 不修复 `index-constitution` 上游数据，也不向上游提 issue。
- 不追求把每一段偏离都修到恰好 300——只承诺"逐条裁定、有证据的改、无证据的显式标注"。

## 与既有设计的冲突与取舍（重要）

`docs/superpowers/specs/2026-09-09-point-in-time-index-universe-design.md` 的"摄取、验收和错误语义"一节明确规定：

> 优先使用中证指数公司可核验公告/下载快照作为最终证据。开源 `index-constitution` 类项目可用于采集或交叉核对，但不能作为无官方证据时的唯一正式依据。

本设计在官方渠道不可用的前提下，选择以该开源数据为主干。这是**对既有原则的一次显式偏离**，取舍如下：

1. **产出明确标注来源。** `source` 为 `index_constitution`；`rules_version` 为 `index-constitution-<包版本>+repairs-<8hex>`（详见"溯源 schema 与哈希绑定"）。
2. **能用官方证据裁定的行，提升到官方证据等级。** 已落盘的官方公告正文（`csi_index_announcements/*.json`）含完整的调入调出名单，属于可核验的一手证据。
3. **不冒充 canonical。** 只要无法逐日恰好 300，就落 `custom_csi300_ic`，**不占用 `csi300` 这个 canonical id**。自定义池按既有 spec 要求携带组成规则、来源与内容版本。
4. **canonical `csi300` 仍以官方证据为准。** 官方渠道恢复后，本快照可退回交叉核对角色。

> 决策记录：owner 于 2026-09-10 确认采纳本方向，并确认既有 experiment 产物可随时清理。

## 数据落地结构

每次导出一个**带日期的独立目录**，写入后不可变：

```
data/raw/csi/index_constitution/2026-09-10/
  csi300_history.csv      # ic 原始：symbol,name,opt-in,opt-out（1225 行，原样不改）
  csi300_latest.csv       # ic 原始：symbol,name,opt-in（300 行，原样不改）
  cn_events.csv           # ic 原始：代码/名称变更事件（审计用）
  manifest.json           # 源包名+版本、三个 CSV 的 SHA-256、导出环境、导出日期
  repairs.csv             # 我们的裁定修正，非原始数据
  evidence_summary.json   # 证据摘要：manifest 哈希 + repairs 哈希
  adjudication_report.md  # 裁定报告，给人看
```

`data/` 整个被 `.gitignore`，因此这与现有 `sina_history_component/`、`csi_index_announcements/` 一致：**证据落盘、不入库**。

**目录按导出日期隔离，不原地覆盖。** 上游发布新版本时新建一个日期目录，旧快照的哈希永远可回溯，历史构建保持可复现。`repairs.csv` 也随快照走——它针对的是该版本上游数据的具体缺陷，上游一变就需重新裁定；需要沿用旧裁定时显式复制并在报告中注明。

原始 CSV 一字不改；所有修正集中在同目录的 `repairs.csv`，使"哪些是上游数据、哪些是我们的改动、依据是什么"始终可分辨。

构建脚本用**显式** `--snapshot-dir` 参数指定快照，**不做隐式"取最新"**——隐式解析会让同一份实验在不同时间产生不同结果。

## 导出与运行时隔离

`index-constitution` 的 `.pkl` 是用 pandas 3.0 的 `StringDtype` 序列化的，pandas 2.x 读取直接抛 `NotImplementedError`。实测：

| 环境 | 结果 |
| --- | --- |
| py3.10 + pandas 2.3.3 | 失败 |
| py3.11 + pandas 3.0.5 | 正常 |

而本仓库 `pyproject.toml` 是 `requires-python >= 3.10`、`pandas>=2`。

在 py3.12 + pandas 3.0.5 下的实测兼容性：

| 套件 | 结果 | 耗时 |
| --- | --- | --- |
| 单元测试 | 767 通过 / 2 失败 | 26s |
| 集成测试 | 289 全部通过 | 18m30s |

两处失败均为 dtype 默认值变更（`datetime64[ns]`→`[s]`、`object`→`str`）。**包括 `test_stable_membership_rerun_reproduces_the_same_snapshot_map`、`test_same_facts_freeze_the_same_definition_across_datasets` 在内的哈希与确定性测试全部通过**，说明该代码库对 pandas 3 的兼容性良好，迁移代价实为**低-中**而非不可承受。

本设计仍选择隔离而非迁移，理由是：迁移是一次独立的基础设施变更，混入本任务会模糊变更边界；且冻结快照本身有独立价值（上游随时可能变更）。将来若迁移，dtype 应显式钉死而不是放宽断言。

因此采用运行时隔离：

- `project/collect_index_constitution.py` **只在隔离解释器**（py3.11+ / pandas 3）跑一次，导出 CSV 并写 manifest。
- 主环境（py3.10 / pandas 2.x）**永远不 import index_constitution**，只 `pd.read_csv`。
- 脚本启动自检 pandas 主版本 < 3 时立刻报错退出，并提示应使用的解释器。
- `manifest.json` 记录 `index-constitution` 版本、Python 版本、pandas 版本、导出时间，保证快照可复现。

## 修复表与裁定流程

### 已知偏离

逐日（7736 个自然日）统计成员数，偏离恰好 300 的区段共 4 段、2602 天：

| 区间 | 天数 | 只数 | 观测到的触发 |
| --- | --- | --- | --- |
| 2006-08-12 ~ 2007-04-29 | 261 | 301 | SH601006 大秦铁路单独调入，配对剔除缺失 |
| 2008-06-14 ~ 2009-12-31 | 566 | 301 | 该日 19 进 20 出，但 2 条剔除行（SH600501、SH600786）`opt-in` 缺失 → 有效剔除 18 → 净 +1 |
| 2012-01-01 ~ 2014-07-09 | 921 | 301 | 该日 24 进 24 出，但 SH600312 `opt-in` 缺失 → 有效剔除 23 → 净 +1 |
| 2017-02-13 ~ 2019-06-16 | 854 | 299 | SH600005 武钢股份因被宝钢吸收合并单独剔除，无补入 |
| 2019-06-17 起 | — | 回到 300 | SH600549 `opt-in` 缺失使其剔除不生效 → 净 +1，恰好补回上一段的 −1 |

**4 行 `opt-in` 缺失**（SH600312 平高电气、SH600501 航天晨光、SH600549 厦门钨业、SH600786 东方锅炉）中，有 3 行的缺失直接影响其剔除日的计数，是上表两段偏离的直接成因；SH600786 另有一行区间完整（2005-07-01 → 2008-03-20），缺失的那行是重复行。

**注意：上表是"观测到的触发"，不是"已确定的修法"。** 以 SH600312 为例，`opt-in` 该补一个更早的日期、还是该整行丢弃，取决于它当时到底是不是成分股——两种改法对计数的影响不同。这正是裁定流程要解决的问题，不能凭推理填。`repairs.csv` 里只允许出现裁定完成的结论。

### 裁定证据发现

**官方公告正文含完整调入调出名单**，不需要附件 Excel。例如 `85.json`（2006-08-02 发布）正文：

> 鉴于大秦铁路（601006）总市值居沪深证券市场前10名，符合大市值IPO快速进入指数规则的条件，中证指数有限公司决定自 **8月15日** 起…沪深300指数 **调入 大秦铁路 601006，调出 000780 草原兴发**

但该存档只覆盖到 2006-12，四段偏离中只有第一段有官方离线证据。

**第一段的修复不是单点编辑。** 官方说 000780 于 2006-08-15 调出，而 ic 中 `SZ000780` 现名"平庄能源"（改名），是**单区间 2005-04-08 → 2013-12-16**。若只补一笔 2006-08-15 的剔除，2013-12-16 那个调样点就会凭空 -1，**±1 只是被挪到别处**。修复必须配合全局重校验。

**新浪与 ic 系统性冲突**，不能"以新浪为准"：

| 股票 | ic | 新浪 |
| --- | --- | --- |
| 600296 兰州铝业 | 剔除 2007-04-30 | 剔除 2007-04-27 |
| 600357 承德钒钛 | 剔除 2014-07-10 | 剔除 2009-12-28 |
| 600501 航天晨光 | 纳入 缺失 / 剔除 2008-06-14 | 纳入 2007-04-30 / 剔除 2008-06-30 |
| 600786 东方锅炉 | 纳入 缺失 / 剔除 2008-06-14 | 纳入 2005-07-01 / 剔除 2008-03-17 |

新浪表本身残缺（20 年应有约 900 次剔除，实际只有 261 行），故只能作旁证。

### 修复表契约

`repairs.csv` 列：

```
symbol, action, field, old_value, new_value, evidence_tier, evidence_source, evidence_detail
```

- `action` ∈ `set_field`（改日期）/ `insert_row`（补一整行）/ `drop_row`（删冗余行）
- `evidence_tier`：`A` = 官方公告正文（权威）；`B` = 新浪历史表（聚合商，仅旁证）
- 写进表里的每一条都必须有证据；**未裁定的争议不进表**

### 裁定报告

`adjudication_report.md` 逐个偏离区间列出候选解释、各来源原值、采纳与否及理由。给人看，不参与构建。

### 硬约束

- 每次改动后**重跑全局逐日校验**；局部看似修好、实际把 ±1 挪到别处，必须被抓住。
- 构建后仍有偏离时：`csi300` 直接拒绝，落 `custom_csi300_ic`，并打印全部偏离天数。
- `repairs.csv` 每条都能追溯到具体证据文件。

## 溯源 schema 与哈希绑定

`rules_version` 保持**字符串标签**，结构化信息全部外置。这是被现有契约逼出来的：`UniverseDefinition.rules_version` 的校验是"非空、无首尾空白的字符串"，且它进入定义的 canonical JSON、进而进入 `universe_version`。改成结构化对象要动 pydantic 模型及全部消费方，属于本任务范围外。

分工如下：

| 信息 | 落在哪里 |
| --- | --- |
| 修复集指纹 | `rules_version` 标签尾部，形如 `index-constitution-1.0.0+repairs-3f9a2c1b`（repairs.csv 哈希前 8 位） |
| 包版本、Python/pandas 版本、导出日期 | `manifest.json` |
| 三个上游 CSV 的 SHA-256 | `manifest.json` |
| 每行的 `snapshot_sha256` | `csi300_history.csv` 的 SHA-256 |
| 每行的 `source_document_sha256` | `evidence_summary.json` 的 SHA-256 |
| 定义里的 `evidence_summary_sha256` | 同上，`evidence_summary.json` 的 SHA-256 |

`evidence_summary.json` 是一份小文档，列出 `source`、`source_url`、`manifest.json` 的哈希、`repairs.csv` 的哈希。它同时充当每行的 `source_document_sha256` 和定义的 `evidence_summary_sha256`，语义一致。

**这样做的关键效果：`rules_version` 尾部随 repairs 内容变化**，于是任何一条修复的增删改都会改变 `universe_version`，修复集被间接钉死。否则 `repairs.csv` 不受任何哈希覆盖，改一条修复而定义不变，是当前设计里的一处漏洞。

各信息的归属是刻意的：能进标签的只有修复集指纹（因为它是"规则"的一部分）；环境与导出细节属于可复现性元数据，进 manifest；逐行证据绑定进 parquet 列。

## 构建、校验与发布

两个脚本，职责分开。

**`project/collect_index_constitution.py`（隔离环境跑一次）**

拆成两层以便测试：

- `export_frames(history, latest, events, out_dir) -> manifest`：纯函数，只吃 DataFrame、写 CSV、算哈希。
- `main()`：只负责 `import index_constitution` 取数，再调 `export_frames`。

**`project/build_csi300_universe.py`（主环境跑）**

1. **先验快照**：入参为显式 `--snapshot-dir`。用 `manifest.json` 记的 SHA-256 校验三个 CSV，用 `evidence_summary.json` 校验 `manifest.json` 与 `repairs.csv` 的哈希，任一对不上直接拒绝构建。
2. **读历史帧 + 应用 `repairs.csv`**，得到"生效帧"。
3. **映射到 repo schema**：

   | ic 字段 | repo 字段 | 说明 |
   | --- | --- | --- |
   | `SZ000001` | `000001.SZ` | 代码格式反转 |
   | `opt-in` | `raw_effective_from` | |
   | `opt-out` | `raw_effective_to` | 空值 → null（仍未观察到移除） |
   | — | `announcement_date` | 取 `raw_effective_from` |
   | — | `status` | opt-out 为空 → `active`，否则 `removed` |
   | — | `reason` | 基准期（from = 2005-04-08）且 active → `initial_constituent`；其余 → `regular_rebalance` |
   | — | `source` | `index_constitution` |
   | — | `source_url` | 包主页 URL |
   | — | `snapshot_sha256` | `csi300_history.csv` 的 SHA-256 |
   | — | `source_document_sha256` | `evidence_summary.json` 的 SHA-256 |

   **`rules_version` 不进 parquet。** `MembershipFact` 是 `extra="forbid"`，字段固定为上述 11 列，没有 `rules_version` 槽位；它只存在于 `UniverseDefinition`（即 `configs/universes/<id>.yml`）里。附录中的归属表已按此更正。

   映射受 `MembershipFact` 的硬校验约束，实现时必须遵守：`status` 与 `raw_effective_to` 是否为空必须一致；`initial_constituent` 必须是 active；`delisting` / `merger_or_reorganization` 必须是 removed。同一 `universe_id`/`symbol` 的区间不得重叠——**实测 ic 数据无重叠**（949 个 symbol、1225 段区间，226 个 symbol 有多段合法的重复纳入），可直接映射。

   **`opt-in` 缺失的行必须显式处理**：这类行没有 `raw_effective_from`，无法映射成 fact。构建时若遇到未被 `repairs.csv` 覆盖的缺失行，必须报错退出，不得静默丢弃——静默丢弃正是"某个剔除不生效"这类缺陷的藏身之处。

   `announcement_date` 取生效日的含义是"生效当天才可见"，而真实公告通常提前约两周。方向是**宁可晚知、不可早知**，对回测安全（不引入前视），但确为近似，必须写入 `rules_version` 与报告。

   这个近似的具体影响是：**在生效日之前的那两周里，回测会认为该股票还不属于成分**，而这可能低估早期的纳入。对动量/反转类信号会产生细微偏差。偏差方向是"晚知"而非"早知"，因此**不会产生"使用了未来信息"的严重错误**，属于可接受近似。报告里必须写明是"可能低估早期纳入"，而不是"高估"——方向写反会误导后续对结果的解读。

4. **逐日校验**：用已发布 dataset 的交易日历统计每天成员数，偏离天数写进报告。
5. `prepare_membership_file(...)` → 出 membership parquet。
6. republish dataset（加 `universe_membership` 表）。沿用现有 `collect_*.py` 惯例；owner 已确认既有 experiment 产物可随时清理。
7. 出 `configs/universes/<id>.yml`，带真实 hash。

## 测试

结构性约束：**测试跑在主环境（pandas 2.x），不得 import `index_constitution`**。这正是把 `export_frames` 拆成纯函数的原因——测试喂合成 DataFrame 验证导出逻辑，`import index_constitution` 只留在 `main()` 里。

- **导出器**：三个 CSV 列名/行数与输入一致；`manifest.json` 的三个 SHA-256 与实际文件字节一致；pandas 主版本 < 3 时 `main()` 拒绝运行。
- **修复表应用**：`set_field` / `insert_row` / `drop_row` 各自生效；未知 symbol、未知 action、未知字段 → 报错（不静默忽略）；空 `repairs.csv` → 生效帧与原始帧逐行相等。
- **快照完整性**：manifest 哈希对不上 → 构建拒绝；`evidence_summary.json` 校验失败（例如 `repairs.csv` 被改过）→ 同样拒绝。
- **修复集指纹**：改动 `repairs.csv` 任一行 → `rules_version` 尾部变化 → `universe_version` 变化。
- **逐日校验**：构造 301 只输入 → 判定偏离、canonical `csi300` 被拒并落 `custom_csi300_ic`；构造恰好 300 → 通过。
- **确定性**：同一输入构建两次 → `membership_table_sha256` 相同。
- **端到端（离线 fixture）**：小 fixture 快照 + 修复表 → 出 parquet，行数与预期一致；不碰真实大数据、不联网。

测试沿用 repo 惯例：临时构造数据，不读模板 YAML。

## 风险与未决

1. **可能修不到恰好 300。** 官方离线证据只覆盖第一段；其余三段（尤其 2012-01-01 和 2017-02-13）没有权威依据。若最终仍有偏离，产出为 `custom_csi300_ic`，这由本设计预设，不算失败。
2. **`announcement_date` 是近似。** 生效日可见 ≈ 最坏情况下晚知两周，影响是**可能低估早期纳入**（不是高估）。规则版本中显式记录，报告中写明方向。
3. **聚合商来源的独立性弱。** `index-constitution` 自称来源为 csindex 官方公告，但未逐条给出公告日期。这既是它不能充当 canonical 依据的原因，也是官方渠道恢复后必须复核的原因。
4. **上游数据变更。** 快照冻结后上游若发布新版本，新建一个 `data/raw/csi/index_constitution/<新日期>/` 目录，**绝不原地覆盖**旧的同名文件——否则旧快照的哈希找不回来，历史构建不可复现。新目录里的 `repairs.csv` 需重新裁定；沿用旧裁定时显式复制并在 `adjudication_report.md` 中注明来源目录。
