# 数据类型拓展的架构优化设计（2026-09-19）

状态：**已定稿**（owner 终审 + 两轮收口（§6 与 F1–F4）+ 第六轮三处补丁通过，2026-09-19；
三轮修正台账见 §0，B1 前生效的收口决策见 §6），进入实施计划。本文是
[docs/research/2026-09-19-data-supply-capability-report.md](../../research/2026-09-19-data-supply-capability-report.md)
的架构后续，并已吸收 owner 同日审核的五点修正（§0）。所有代码事实均于 2026-09-19
在本分支逐条核实。

## 0. 相对前稿（评审讨论稿）的修正对照

| # | 前稿说法 | 修正后 | 依据 |
| --- | --- | --- | --- |
| 1 | 阻断码 14 个 | **15 个**（[gates.py:39-57](../../../src/stock_quant/data_quality/gates.py#L39-L57)） | 逐项点数 |
| 2 | 「每次 data update 都是全窗口重取」 | 仅**默认路径**成立；`--start` 已暴露（[cli.py:305](../../../src/stock_quant/cli.py#L305)），`data_pipeline.py:652` 为 `request.start_date or project_config.start_date` | 实读 |
| 3 | R1 先行、R2 后补 | **D2 契约形状先行或与 D1 同批**：降级表要能被消费端拦住，前提是每张外围表都有 coverage 证据形状——那正是 D2 的产出；否则出现「已降级但无人能查」的窗口 | owner 裁决 |
| 4 | 档位两档（核心/外围） | **三档，按证据强度分，不按业务重要性分**：核心（阻断）／外围-有锚（降级，VERIFIED 可消费）／外围-无锚（RESEARCH-ONLY，无论 coverage 多干净不得进正式研究）。两档会让无锚表靠自己的 coverage 自证，违背证据纪律 | owner 裁决 |
| 5 | R5 增量窗口「省成本」未计代价 | 增量化**静默取消一层再验证**：全窗重取时供应商回溯篡改会让新版本字节不同（可见）；改增量后窗外漂移检测消失。必须配**周期性全窗口漂移审计**（月/季）作补偿控制 | owner 裁决 |
| 6 | 新开 `configs/data_contracts.yml` | **不新开配置面**：sources.yml 已在承载同类区分（proxy "Data source only - never an evidence source"、tdx 仲裁者非源、baostock 角色可选）。两份 sources.yml 均在工作区改动中，正是合并时机。一个面，不是两个 | owner 裁决 |

### 第二轮修正（owner 复核，同日）

| # | 前稿说法 | 修正后 | 依据 |
| --- | --- | --- | --- |
| 7 | 「没有任何检查消费这个区别」 | **不成立**：`_window` 消费 `requested_start_date`，驱动 `date_window_completeness` 与证据包窗口；`evidence_window` 刻意排除 `effective_start_date`（请求早于数据起点必须表现为缺失行，不得静默缩小审查范围） | checks.py:669-675、evidence.py:116-132 |
| 8 | manifest 只讨论两个窗口字段 | 存在**第三时钟** `full_history_acceptance_start`（发布时固定、不按当前配置回溯重判）；验收审查义务应锚在它上 | checks.py:394-397；1d6e43b4… 生产实证 |
| 9 | D5.2「不加新记录，加消费者」 | **既有消费者换锚点 + 表级覆盖入 manifest**；D5.1 与 `_window` 存在两条失败路径的未处理冲突，换锚（min(requested, acceptance_start)）提前到 B1 | 三已发布版本验算行为不变 |
| 10 | 多处行为未定义 | 未声明表 fail-closed（D1）；契约五要素加发布时校验执行点（D2）；`primary` 改传输引用非源名（D2）；D3 兜底 WARN 落点与同键多行裁决；D4 截断守卫默认 fail-closed | owner 复核 |

### 第三轮修正（owner 终审，同日）

| # | 前稿说法 | 修正后 | 依据 |
| --- | --- | --- | --- |
| 11 | D5.1 换锚 `min(requested, acceptance_start)` | **撤回**（第二轮第 9 条提案有误，本规格照抄了它）。min 会把窗口放宽到日历证据之前，checks.py:308-311 对 start < 首个开市日按构造记 `window_not_calendar_complete` FAIL——min 不是「更宽即更严」，是保证失败的窗口；「三例行为不变」判据本身错误，1d6e43b4（requested=2015-01-01 < 2015-01-05）恰是必然验收失败的版本。**正确锚点：`full_history_acceptance_start` 单独作锚，不取 min** | checks.py:308-311、RUNBOOK.md:80-84 |
| 12 | 「换锚同时闭合 null 路径」（无限定） | 限定 **data_update 来源**。bootstrap manifest（bootstrap.py:74-83）仅 4 键、`full_history_acceptance_start=None` 且无 requested/resolved 键，仍不可验收（设计如此）；checks.py 捕获表不含 TypeError，把锚套在 None 上是硬崩溃不是 FAIL，锚缺失须显式 `full_history_acceptance_start_missing` | bootstrap.py:74-83、checks.py:190、208-214 |
| 13 | D2 五要素有执行点、无落地配套 | 两处缺口：发布时校验上线前必须**回填全部在册表声明**（否则下次 `data update` 全挂 `unregistered_table`）；校验作用域窄化为 `origin: data_update`（bootstrap 经同一 `publish` 路径） | owner 终审 |
| 14 | `primary_transport: tushare:relay` 自由文本 | 取值必须引用代码规范 token（`RELAY="relay"` / `OFFICIAL="official"` / `PROXY="proxy"`，env `TUSHARE_TRANSPORT`），不造并行词汇表 | tushare_transport.py:46-48 |

## 1. 事实基础（2026-09-19 逐条核实）

**门禁层。**

- `PUBLICATION_BLOCKING_CODES` 是扁平 `frozenset`，共 **15** 个码；
  `evaluate_publication`（[gates.py:72-81](../../../src/stock_quant/data_quality/gates.py#L72-L81)）
  只判 `item.code`，从不读 `item.table`。
- `QualityIssue` 自带 `table` 字段（[models.py:90-100](../../../src/stock_quant/data_quality/models.py#L90-L100)），
  `_describe` 已在用它拼消息。**分组维度已存在于 issue 模型**——D1 改的是判定谓词，
  不是数据模型。

**消费端门禁（D1 复用的既有路径，已全线接线）。**

- [trust.py:40](../../../src/stock_quant/research/trust.py#L40)
  `_TRUSTED_STATUSES = {VERIFIED, VERIFIED_EMPTY}`；凡非此二者必产生 reason，
  `trusted = not reasons`（trust.py:116 逐行消费）。
- 接线三处：research runner 的 coverage 消费（runner.py:1819、runner.py:2072）与
  验收检查（acceptance/checks.py:351）。

**降级机制与门禁确无调用关系。**

- `_coverage_verdict`（[data_pipeline.py:2443](../../../src/stock_quant/data_pipeline.py#L2443)）
  只返回 `(status, reason)` 元组、不产生 `QualityIssue`，因此 `UNTRUSTED` 不进
  阻断码表。UNTRUSTED 不阻断发布：5 只科创板标的覆盖窗口 UNTRUSTED 仍发布的
  先例已实证（记录于 [PROJECT_MEMORY.md §8.4](../../PROJECT_MEMORY.md)
  2026-09-14 段：659 只回补中 eastmoney 对 5 只科创板标的取数失败，该窗口
  记 UNTRUSTED、发布继续）。

**缩窗更新与证据面（第二轮修正后的准确表述）。**

manifest 携带**三个窗口时钟**，消费关系如下：

| 字段 | 语义 | 消费方 |
| --- | --- | --- |
| `requested_start_date` | 显式 `--start`，缺省 null | `_window`（checks.py:669-675）→ `date_window_completeness` 与 `evidence_window` 证据包 |
| `effective_start_date` | request 或 `project_config.start_date` | 无人消费（evidence.py:116-132 docstring 明写排除） |
| `full_history_acceptance_start` | universe coverage_start，发布时固定、不按当前配置回溯重判 | `_check_calendar_coverage`（checks.py:394-397） |

- 缩窗与全窗在 manifest 层可区分（build_config 参与版本哈希），且**已被
  消费**：审查窗口锚定 `requested_start_date`，`evidence_window` 刻意不用
  `effective_start_date`——请求早于数据起点时必须表现为缺失行，不得静默
  缩小被审查范围。1d6e43b4… 的 requested=2015-01-01 早于
  full_history_acceptance_start=2015-01-05，正是该情形的生产实证。
- 真正的缺口：审查窗口锚在**取数请求**而非**验收义务**
  （`full_history_acceptance_start`）上——D5.1 增量化落地即触发两条失败
  路径（见 D5 第 1、2 条）；requested 为 null 时 `_window` 直接抛
  ValueError（checks.py:208-222 捕获为 FAIL，证据包 `window_missing`）。
  三个已发布 manifest 的 requested 全部非空，故今天潜在、非必现。
- 基线结转（`_read_baseline`，data_pipeline.py:584-585、1102-）+
  `merge_window`（calendar span 与基线合并，data_pipeline.py:1587）使窗外
  数据**不再被重取**（供应商侧漂移不可检测）；合并表的内部一致性校验每轮
  仍在跑——「不再被重取」不是「无校验」。
- 附带不一致（换锚后自然消解）：`evidence_window` 的 docstring 把「请求早于
  数据起点表现为缺失行」表述为特性，但同一 `_window` 在 checks.py:308-311
  的实现是硬 FAIL（`window_not_calendar_complete`）——「表现为缺失行」在
  当前代码里并不存在，文档与代码精神不一致。锚定
  `full_history_acceptance_start` 后两者才对齐。

**核验按类型一次性定制的实证。**

- ADR-007 / 008（`008-corporate-action-non-distributive-events.md`）/
  009（`009-corporate-action-absent-ex-date.md`，工作区未跟踪）是**连续三份
  ADR 处理同一类数据（公司行为）的核验**——每类新数据从零设计，这就是 A4
  的成本证据。
- 工作区状态：`project/configs/sources.yml` 与 `templates/project-config/sources.yml`
  均在修改中；`docs/adr/DECISIONS_INDEX.md` 在修改中（008/009 入索引）。
  D2 落地必须与这些在途改动协调，**不得覆盖或回退**。

## 2. 设计决策

### D1 · 三档证据门禁（原 R1，扩展为三档）

**档位定义（按证据强度，禁止按业务重要性）：**

| 档 | 语义 | 发布时 | 消费时 |
| --- | --- | --- | --- |
| `core` | 现有 15 码任一命中即整轮不发布（现状不变） | 硬阻断 | 无需额外门禁 |
| `anchored` | 有声明过的独立锚点；结构问题降级为该表 coverage `UNTRUSTED`（照 `corporate_action_coverage` 形状），发布继续 | 降级 | **消费端门禁**：复用 `trust.py` 的 `_TRUSTED_STATUSES` 形状，research run 引用 UNTRUSTED 表的因子在预检失败 |
| `research_only` | 无独立锚点（如当前行业分类：csindex API 500、深交所直连超时） | 同 anchored | **无论 coverage 多干净，恒不可进正式研究**；只能被工程诊断规格引用，报告标注 RESEARCH-ONLY |

**治理约束（owner 两条裁决）：**

1. **必须走 ADR**：R1 重新划分「哪个码阻断谁」，仓库规则明写不得弱化
   publication gate，静默改动不合法。ADR 编号与结论待实施时定稿（建议
   ADR-010「evidence-tiered publication gating」）。
2. **档位来自声明，不来自判断**：档位写进 sources.yml 的表级声明；
   `gates.py` 的判定谓词从 `item.code` 变为 `(item.code, item.table)` 查
   声明映射。开发者即席决定档位 = 仓库明确不设的「绕过开关」，禁止。
   档位变更 = 配置变更 + 留痕，与 ADR 同批。
3. **未声明表 fail-closed**：谓词读到未在注册表声明的表时按核心档处理
   （阻断）——「忘记声明」不得等价于拿到绕过开关。与 D2 的发布时声明
   校验是同一处代码的两个方向。

**落地成本核（比前稿更低）**：分组维度（`table`）已在 issue 模型里；消费端
门禁已存在、已接线、已被测试覆盖——D1 只新增「anchored/research_only 表的
coverage 证据形状」（由 D2 提供）与谓词改造。

### D2 · 接入契约注册：扩展 sources.yml，不开新配置面（原 R2）

在 sources.yml 增加**表级数据契约**段（与现有源级角色声明同层），每类新数据
必须回答五件事，缺一不得接入：

```yaml
data_contracts:
  - table: income            # 例：财务利润表
    tier: anchored           # D1 档位声明处
    primary_transport: tushare:relay  # 传输引用（源:形态），不是源名
    anchors:                 # 独立锚点，上游谱系必须不同于主源
      - akshare_cninfo_announcement   # 抽样对巨潮公告日
    conflict: downgrade      # 冲突语义：block / downgrade / arbitrate
    pit:
      as_of_field: f_ann_date
      fallback: ann_date     # 兜底并留 WARN
      fact_row_policy: max_report_type_v1   # report_type 事实行裁决，版本化
    coverage_shape: per_symbol_window       # D4 描述符引用
    incremental: disclosure_calendar   # 每表取数窗策略（A3/F3 承重字段）
```

- **发布时校验是「缺一不得接入」的执行点**：发布器逐表校验
  `data_contracts` 声明存在，缺声明 = FATAL（`unregistered_table`）。
  散文约束没有执行点等于没有约束；与 D1 的未声明 fail-closed 同一处
  实现。**作用域 = `origin: data_update`**（第三轮修正 13）：bootstrap 经
  同一 `DatasetPublisher.publish` 发布且 build_config 仅 4 键，校验若在
  publish 内无差别执行则 bootstrap 直接破——窄化到 data_update 来源
  （或给 bootstrap 一份最小声明集，实施时二选一，默认前者）。
- **B0 必须回填全部在册表声明**（第三轮修正 13）：校验上线时，现有
  daily_bar / trading_calendar / security_master / corporate_action /
  universe_membership / corporate_action_quarantine / adjusted_bar 及各
  coverage 表若未声明，下次 `data update` 即全挂 `unregistered_table`。
  回填清单是 B0 的显式交付物，不是「首版锚点矩阵」的隐含部分。**回填必须
  含各存量表的 `incremental:` 初值（第五轮 F3）**——日频表
  `last_covered_plus_1`，其余按实际节奏——否则 B2 消费时该字段为空，
  A3 的窗口计算无从起算。
- **`primary_transport` 是传输引用不是源名**：取值引用代码规范 token——
  `RELAY="relay"` / `OFFICIAL="official"` / `PROXY="proxy"`
  （tushare_transport.py:46-48，env `TUSHARE_TRANSPORT`），不造并行词汇表
  （与「不开第二个配置面」同族风险）；不进 `_CONFIGURED_SOURCES` /
  `_REQUIRED_ROLE`（sources.yml 既有注释明写不得向该表加名）；独立锚
  另立于 `anchors`。
- **report_type 事实行裁决进注册表**（owner 对 D3 的约束 3）：不做代码常量。
  首版裁决规则按实测证据起草（单期 2 行 = `end_type`/`report_type` 变体），
  规则名带版本。
- **锚点矩阵首版**（接 D1 档位）：
  - 财务三表 / fina_indicator → 巨潮公告日抽样（akshare cninfo 车道已有）→ `anchored`
  - suspend_d → 内部价格链推导互证（`suspensions.py` 既有逻辑）→ `anchored`
  - adj_factor → `internal_total_return_v1` 比对 → `anchored`（只作校验证据，
    唯一口径不变）
  - 申万行业（index_classify / index_member）→ `research_only` 维持，
    **升档验证已开题**（2026-09-19 补充实测）：
    - 时点原料可得：`index_member` 按单票返回 1991 年起多段成员资格
      （`in_date`/`out_date`），2021-12-13 版本切换边界事件可见；按行业
      代码拉取可行（801010.SI 一次 549 条含历史成员），全市场约
      31 L1 + 134 L2 + 346 L3 次调用——D4 成本模型补记第三种键控形态
      `index_keyed`。
    - 缺口一：`index_classify` 仅回 SW2021 树（31 个 L1），SW2013 返回
      0 行——2021-12 之前的历史只有旧代码成员关系、无名称映射；跨版本
      沿用代码的语义需抽样核验后才可写进 `fact_row_policy`。
    - 缺口二（转机）：申万研究官网 swsresearch.com 本机 HTTP 200 可达
      （此前从未测过该渠道）——官方分类文件若可下载，走 `index_weight`
      同款快照存证路线即可升 `anchored`；csindex 行业分类导出实测 404，
      深交所直连仍超时。
  - daily_basic / moneyflow / margin_detail / top_list 等 → 待逐类评估，
    默认 `research_only` 起步
- **合并协调**：两份 sources.yml 在途改动落地后本段才合入；同文件编辑避免
  与在途变更冲突。

### D3 · 财务 PIT 消费层（原 R3，加三条 owner 约束）

- **存储**：披露事实行只存一份（`ts_code, end_date, f_ann_date, ann_date,
  report_type, 指标列`），重述按披露日追加、不回改历史行（与 ADR-001
  内容寻址一致：追加即新版本）。
- **访问器 `as_of(symbol, field, as_of_date)` 三条硬约束**：
  1. **钉住版本的纯函数**：dataset_version 由调用方显式传入或随冻结规格解析，
     **绝不在内部读 CURRENT**——否则可复现性静默失效；
  2. **落在研究/消费层**（`research/` 下），不进 `data_model/`——数据模型层
     不应知道研究侧的时点语义；
  3. 事实行选择按 D2 注册表的 `fact_row_policy`，`f_ann_date <= as_of_date`
     取最新，缺失用 `ann_date` 兜底并留 WARN。
- **兜底 WARN 与同键多行的裁决**：兜底 WARN 落 run quality report（issue
  带 symbol 与报告期，可审计）；同一 `(f_ann_date, end_date, report_type)`
  多行时——值完全相同 → 去重留一条；值不同 → FAIL 进人工裁定（复用
  reviews.yml 语义），不静默选边。
- **测试**：前视防护专项——断言 `as_of` 对 `end_date <= as_of < f_ann_date` 的
  行不可见；report_type 变体只取事实行。

### D4 · 端点描述符驱动的接入套件（原 R4）

每端点声明：参数形态（`date_keyed` 全市场单调用 / `symbol_keyed` 逐只 /
必填约束）、窗口切片要求（6,000 行静默截断守卫）、重试类别（TLS EOF 进
白名单）、字段投影、预期节奏。框架生成：帧校验、截断守卫、provenance、
契约测试骨架。手写只剩 normalize 与核验逻辑。本次实测的传输怪癖（截断、
TLS EOF、`forecast`/财务三表必填标的、`stock_basic` 空参数只回 L）全部
落为声明，不再靠 docstring 传承。

**截断守卫行为**：检测到 ~6,000 行静默截断时默认 fail-closed（该端点本轮
取数失败、留证）；自动切片仅当端点描述符显式声明（`auto_slice: true`），
且切片边界留档进 provenance——「无静默」纪律不允许隐式切片。

成本依据（实测）：日期键控端点单次调用服务全市场（daily_basic/stk_limit/
moneyflow/adj_factor 单日 5,547–7,570 行）；财务三表与 forecast/express
经 relay 只接受标的键控（684 只 × 4 表全历史 ≈ 2,700+ 次调用）。描述符使
调用成本在接入时即成为显式声明。

### D5 · 更新经济学：增量窗口 + 调用账本 + 漂移审计（原 R5，补代价与口子闭合）

1. **验收窗口换锚（D5.1 的硬前置，B1 落地）**：`_window`（checks.py:669-675）
   与 `evidence_window`（evidence.py:116-132）的起点锚定
   `full_history_acceptance_start` **单独作锚，不取 min**（第三轮修正 11）。
   候选序列：

   ```text
   review_start = full_history_acceptance_start   # 非空 str（data_update 来源恒有）
                else requested_start_date          # legacy / 其他 origin 回退
                else raise full_history_acceptance_start_missing
                #   （不再是裸 ValueError；bootstrap 来源按设计不可验收）
   ```

   - **bootstrap 显式排除**：bootstrap manifest（bootstrap.py:74-83）仅 4 键、
     两字段皆无/为 null，仍不可验收——设计如此（验收起点由首个真正扫描
     定义的 data update 绑定，RUNBOOK 阶段 2 即此状态）。实现者不得把锚套
     在 None 上：checks.py:208-214 捕获表不含 TypeError（:190 docstring 明写
     编程异常不捕），硬崩溃不是 FAIL。
   - **正确验收判据（可断言的行为变更）**：1d6e43b4…（requested=2015-01-01
     早于首个开市日 2015-01-05）的 `date_window_completeness` 由 FAIL 转
     PASS；01c74bee… / d490c637…（requested=acceptance_start=2015-01-05）
     行为不变。
   - **退役 RUNBOOK.md:80-84 的坑**：`--start 2015-01-01` 发布的版本将审查
     2015-01-05 起并通过，操作员不必再记「--start 必须落开市日」；该
     RUNBOOK 条目随 B1 更新。
   - 按构造落在日历证据范围内（acceptance_start 即覆盖起点），与
     `_check_calendar_coverage` 统一——同一 manifest 的两个检查首次用同一
     时钟；「取数窗口 ≠ 审查窗口」首次可表达；增量更新下每次验收复扫全
     历史 bar——部分实现第 4 条的漂移审计（内部一致性面）。
2. **增量默认窗与显式窗口语义**：默认窗口的可计算定义见 §6 A3（CLI
   窗口 = `[min(各表契约增量起点), 最新已发布开市日]`；增量起点依托
   `calendar_coverage` 的 span 形状泛化为表级取数覆盖记录）。**显式
   `--start` 的语义（第五轮 F1 裁决）**：既不覆盖、也不收窄任何表的契约
   取数窗；显式窗口早于某表契约起点时，该表本轮**跳过取数**，表级覆盖
   记录显式记 `NOT_FETCHED`（原因 `operator_explicit_window`）——跳过即
   留痕，与「无静默」纪律同族。**显式 `--end` 对称裁决（第六轮）**：早于
某表契约终点（契约终点恒为最新已发布开市日）→ 该表同样跳过 +
`NOT_FETCHED`（同一原因码 `operator_explicit_window`）；等于或晚于契约
终点则惰性留档。**RUNBOOK 阶段 3「全区间再跑」命令（约 :102）随 B2
退役、指向离线战役路径**——该命令在 F1/F2 之下会全部跳过、只留
NOT_FETCHED，按旧 RUNBOOK 操作将得到空转版本。两个被否决的候选留档：覆盖式缩窗（使
   A3 形同虚设、两条已否决的失败路径重开）、纯审计上界（「缩窗仍可用」
   无实际语义）。**前置：第 1 条已落地**——换锚同时消除两条失败路径：
   (i) 派生起点作为 `--start` 传入时，审查窗口不再跟随 requested 缩小
   （锚在验收义务上）；(ii) 派生起点只内部生效时，requested=null 由
   `full_history_acceptance_start` 兜住。未换锚而先增量化，则 (i) 审查
   窗口与证据包随每次更新静默缩小（evidence_window docstring 本要防的
   事）、(ii) `_window` ValueError → 验收恒 FAIL——B1 → B2 的顺序依赖
   即由此而来。
3. **表级 fetch-coverage 入 manifest**：记录「本版本哪些历史段是本轮重取、
   哪些是基线结转」，验收与 research 预检可消费。机制概括（修正）：**
   既有消费者换锚点 + 表级覆盖入 manifest**——窗口记录早已存在并被消费，
   缺的是把审查义务从取数请求换锚到验收义务，以及表级粒度的覆盖证据。
**`NOT_FETCHED` 的消费语义（第六轮裁决）**：不阻断发布（数据由基线
结转，验收复扫全历史兜底）；但引用含 `NOT_FETCHED` 覆盖段的表的
research run 预检失败——该版本不是完整取数版本，研究应钉全量更新的
版本（与 fail-closed 同族）。
4. **周期性全窗口漂移审计（补偿控制）**：月或季一次对全部已发布表做全窗
   重取比对（目的=漂移检测，非例行更新）；供应商回溯篡改在此暴露。审计是
   独立命令，结果落 `docs/operations/` 日期化记录，发现漂移 = 新证据版本
   发布 + 事件记录，绝不就地改历史。与第 1 条互补：换锚让**每次验收**
   复扫全历史 bar（内部一致性），审计让**供应商侧字节漂移**（外部漂移）
   有必然被看见的时机——覆盖的漂移类型不同，互为加强。
5. **调用账本**：每轮 update 落端点 × 次数 × 配额消耗，容量规划可计算。
6. **大历史回补仍走离线战役模式**（`extend_history_offline.py` 先例），
   不混进例行更新。

### D6 · 通道韧性金丝雀（原 R6，加两条约束）

月度对官方直连 `api.waditu.com`（现有 token）实测「无 relay 时哪些端点仍
可得」，扩展 `verify_update_readiness.py`。约束：结果落 `docs/operations/`
日期化记录；**禁止任何自动切换到降级路径**——降级永远是操作者显式决定，
relay 故障时既有 fail-stop 语义（不发布、CURRENT 不动）就是安全网。
**凭证红线**：运维记录脱敏——不落 token/key/URL 凭证段，只落端点名、参数
形状与结果（本仓库对凭证进日志零容忍）。

## 3. 落地批次（修正后顺序）

| 批 | 内容 | 前置 |
| --- | --- | --- |
| B0 | D2 契约形状（sources.yml 扩展 + 首版锚点矩阵 + 档位声明 + **回填全部在册表声明**） | 在途 sources.yml 改动先落地 |
| B1 | D1 三档门禁 + ADR-010 + 谓词改造（含未声明表 fail-closed）+ 消费端接线扩展 + 因子 inputs 声明（A2/F4，因子层首次改动）+ **D5.1 `_window` 换锚（验收层语义，D5.1 硬前置）** | **与 B0 同批或其后**（owner 排序裁决） |
| B2 | D5 增量窗口 + 表级 fetch-coverage + 漂移审计 + 调用账本 | B1（档位决定增量语义参与方；**换锚必须已落地**，否则 B2 内部存在未写明的顺序依赖） |
| B3 | D4 描述符套件；D3 PIT 访问器 | B0（注册表提供 fact_row_policy） |
| 并行 | D6 金丝雀 | 无 |

## 4. 非目标（反建议，维持前稿，均有 ADR/实测锚）

- 不拆分数据集版本域（ADR-001 完整可复现环境）；量化触发线：例行更新超
  2 小时或表数超 12 再议。
- 不为多样性加同上游源（2026-09-12 实测：同源不可互证）。
- relay 只作传输，不作任何事实的独立锚（锚=巨潮等官方渠道）。
- 不做冲突自动修正（reviews.yml 人工裁定先例；ADR-007「第三票只仲裁不替代」）。

## 5. 验收判据（括号为可测批次）

1. 【B1】一张 `anchored` 表注入**表级**结构坏行：发布不阻断、该表
   coverage=UNTRUSTED、引用它的 research run 预检失败、引用它的工程规格
   可跑且报告标注（码类按 §6 A1 矩阵：全局进程码仍阻断）。
2. 【B1】一张 `research_only` 表 coverage 全 VERIFIED：正式 research run
   仍拒绝消费（执行点见 §6 A2）。
3. 【B1】档位只经 sources.yml + ADR 变更可改；代码内无档位常量——测试
   形态为**静态扫描**：断言 `src/` 无 tier 字面量，档位值仅经 sources.yml
   加载路径进入运行时（负断言只可如此构造）。
4. 【B3】`as_of` 前视防护测试通过；访问器无任何 CURRENT 读取（静态断言）。
5. 【B2】缩窗 update 发布的版本，其 manifest 可被验收读取出「本轮重取段
   vs 基线结转段」；漂移审计（§6 B4 机制）对人工注入的历史快照篡改能
   报警。
6. 【B3】新端点接入只写：描述符 + normalize + 核验逻辑（无手写帧校验/
   截断守卫）。
7. 【B1】1d6e43b4… 情形的回归：以 `--start 2015-01-01`（早于首个开市日）
   发布的版本，`date_window_completeness` 不再记
   `window_not_calendar_complete`（RUNBOOK.md:80-84 的坑退役）；
   01c74bee… / d490c637… 情形（requested=acceptance_start）行为不变。
8. 【B1】data_update 来源默认路径（不带 `--start`，requested=null）发布的
   版本可被验收（null 路径闭合）；**bootstrap 对照**：bootstrap-only
   数据集仍不可验收——预期 FAIL
   （`full_history_acceptance_start_missing`），非崩溃。
9. 【B1】两层 fail-closed：发布时未声明表 → `unregistered_table` FATAL
   （主路径，D2 执行点）；门禁层 `(code, table)` 谓词对未声明表按核心档
   阻断（defence-in-depth——主路径拦截后该场景不可达，测试以单元构造
   触发）。

## 6. 收口决策（B1 前生效）

第四轮终审认定的四个 A 级设计空洞与四个 B 级完整性缺口，均为「实现者会
当场发明答案」的决策缺失。按本规格自己的标准——「没有执行点等于没有
约束」——在此逐条裁决；C 级三项（判据批次标注、判据 3 测试形态、D6
凭证红线）已直接并入 §5 / D6。第五轮收口（F1–F4）已并入 A2 / A3 / D2 /
D5 的相应裁决，§6 B2 总结句的过度声称同轮修正。第六轮终审补丁：显式
`--end` 对称裁决并入 D5 第 2 条与 A3；`NOT_FETCHED` 消费语义并入 D5
第 3 条；B1 批次范围补因子层改动（F4）。

### A1 · D1 的 (code × tier) 首版矩阵

15 个阻断码不同质，分两类裁决：

| 类别 | 码 | 语义 |
| --- | --- | --- |
| **全局进程码**（恒阻断，不分档） | `REPORT_GENERATION_FAILED`、`QUARANTINE_MISSING_REASON` | 前者是发布报告本身没生成、后者是隔离审计不变量被破坏——与具体表无关，发生在 anchored 表上降级毫无意义 |
| **表级码**（按 tier 分流） | 其余 13 个：schema/PK/OHLC/价格量额×4、来源、复权口径、adjusted_bar lineage×4、主源缺口 | core 阻断；anchored 与 research_only 降级为该表 coverage `UNTRUSTED` + 消费端拦截 |

实现顺序：谓词先查全局码集合，再查 `(code, table)` 档位映射。

### A2 · research_only 的消费端执行点与档位语义

- **执行点**：research runner 预检（因子输入解析处）读 sources.yml 注册表
  的 tier 字段；`research_only` → 预检 FAIL（`table_tier_research_only`）；
  工程诊断路径（`backtest --engineering`）除外并在报告标注。
- **前置元数据（第五轮 F4）**：因子→表声明——`Factor` 协议（base.py:73；
  现有 `required_fields` 是字段级非表级，无静态表声明）补
  `inputs: tuple[str, ...]`（本因子读取的表名）；预检按 run 的因子集合
  汇总输入表、再查各表 tier。**未声明 `inputs` 的因子在规格加载即失败**
  （fail-closed，与未声明表同族）。**显式禁止数据集级一刀切**：不得因
  钉住数据集含任何 research_only 表而拒绝整个 run——否则行业表进数据集
  那天，全部动量 run 被无关表连带拒绝。
- **档位是运行时策略，不进实验冻结**：tier 不写入冻结规格、不参与实验身份
  哈希。语义推论（显式裁决，防两种跑偏）：表从 anchored 降为 research_only
  后，已冻结/已接受的实验**不受影响、不回溯重判**（与 checks.py:394-397
  「发布时固定」纪律同构）；新的 research run 立即被拒。升档反向同理，
  只对新 run 生效。

### A3 · CLI 窗口与每表取数窗的关系

CLI `--start/--end` 是**最小公共窗口**，不是各表取数窗。**可计算定义
（第五轮 F2）**：默认无显式窗口时，CLI 窗口 =
`[min(各表契约增量起点), 最新已发布开市日]`；**结束端默认恒为最新已发布
开市日**（与 `--end` 可省略的现行语义一致；显式 `--end` 按 F1 对称
裁决，见 D5 第 2 条）。每表实际取数窗由契约段
`incremental:` 字段决定（D2 模式的一部分，第五轮 F3 补入示例），首版
三种：`last_covered_plus_1`（日频行情）、`disclosure_calendar`（财务：
披露季/公告日驱动，滞后可数月不重拉）、`change_driven_full`（行业成员：
变更驱动低频全量）。并集/交集两候选均否决——前者财务表天天无效重拉，
后者行情表丢增量。**显式窗口的语义裁决见 D5 第 2 条（F1：跳过 +
`NOT_FETCHED` 留痕，不覆盖契约窗）。**

### A4 · D4 与存量适配器的边界 + 业务账

- **边界：只服务新端点。** 存量 tushare/akshare/baostock 适配器承载全部
  已发布数据，不做包裹式改造；迁移列为独立后续，触发线与 §4 同款
  （新表数 > 3 或存量适配器需大改时再议）。
- **业务账**：D4 是六个 D 里最贵的，回报到第 2–3 张新表才显现；**第一张
  新表（行业或财务单表）手写接入**，其手写产物同时充当 D4 生成器的对照
  基准。

### B1 · 存量表档位赋值

七张主表（daily_bar、trading_calendar、security_master、corporate_action、
universe_membership、corporate_action_quarantine、adjusted_bar）与全部
coverage 表**一律 core**。B0 回填即按此赋值，无现场拍板空间。

### B2 · 与供给报告 B1–B7 的追溯线

| 报告阻塞项 | 规格内归属 | 状态 |
| --- | --- | --- |
| B1 验收门（最后人工项） | 不由六个 D 解锁：AkShare 东财日线接入（D2 契约 + 手写）+ 操作者 publish | 维持（报告 P0） |
| B2 停牌证明缺口 | suspend_d 新端点（D2 契约 + 手写/D4）；证据语义设计仍需独立立项 | 部分解锁 |
| B3 quarantine 残余 | relay dividend 第三票属 ADR-007 仲裁扩展，未立项 | 维持 |
| B4 幸存者偏差 | 数据可得（报告实测）；扩池战役设计稿待写 | 维持 |
| B5 master 契约 | universe 契约决策，不属 data_contracts 面 | 维持 |
| B6 官方通道降级 | 现状不变（巨潮 + 上交所可用） | 维持 |
| B7 日线校验单点 | 与 B1 共用东财日线接入 | 部分解锁 |

**显式结论：规格不解锁任何 B1–B7 的直接阻塞**；它降低的是这些战役的
接入成本与质量风险。行动归属：B1/B7 在报告 P0；B2/B4 部分在 P0/P1 且
设计单独立项（B2 仅存证在 P0，B4 为 P1 第 7 项）；B3/B5 无行动项（B3
未立项，B5 属 universe 契约决策、不在数据供给清单）；B6 维持现状无需
解锁。

### B3 · D5.1 换锚的 ADR 归属

单列 **ADR-011「验收审查窗口锚定验收义务」**，与 ADR-010（档位门禁）
同批 B1 落地、不同主题不同记录。换锚是验收层 gate 语义变更，不并入 010、
不静默。

### B4 · 漂移审计的机制与预算

- **机制**：重取原始快照、与 manifest `build_config.raw_snapshots` 已
  记录的哈希逐条比对——复用既有证据结构，不做「重新派生再比行」（更贵
  且引入派生噪声）。命中漂移 = 新证据版本发布 + 事件记录（D5 第 4 条
  原文不变）。
- **预算**：一次全窗重取 ≈ 一次完整 update 的调用量（659 只约 2,600 次
  调用 / 小时级），是 D5 增量化省下成本的**固定回流项**；据此定**季度**
  （月度回流占比过高）。
