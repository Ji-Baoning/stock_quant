# 校验与仲裁通路的批量会话通道 · 设计

- 日期：2026-09-27
- 状态：**待 owner 复核（修订 4：已吸收三轮评审 15 项发现，见 §9/§10/§11；未决项只剩探针 2 的
  实测结果，见 §4）**
- 上游：[2026-09-26-xingyao-baostock-succession-design.md](2026-09-26-xingyao-baostock-succession-design.md)、
  [2026-09-25-raw-snapshot-reuse-design.md](2026-09-25-raw-snapshot-reuse-design.md)（ADR-015）
- 决策层：拟新增 `docs/adr/020-batched-validation-channel.md`（决策条目见 §7）
- 运行证据：[2026-09-26-xingyao-phase0-probes.md](../../operations/2026-09-26-xingyao-phase0-probes.md)（Phase 0 六项探针）
- 关系：本设计**满足** ADR-016 decision 11 的启用前置，**不**启用 xingyao。`enabled: true`
  由单独的启用改动决定。

## 0. 摘要

两条"每请求一次会话"的活车道改为批量：

| 车道 | 现状 | 改后 | 每轮会话数 | 每轮 code 查询数 |
| --- | --- | --- | --- | --- |
| xingyao 校验车道 | 每标的 fork 一个 worker：login + 整份日历（8,733 行）+ query + logout | 每**片**一个 worker：一次 login、一次日历、**一次多 code 查询** | 661 → 1 | 661 → 1 |
| xingyao 因子通道 | 同上，逐标的懒取 | 按候选池分片多 code 取回 | N → `ceil(N/factor_batch_size)`（当前目标 1） | 同左 |
| tdx 仲裁器 | 每个待仲裁标的重建一次 client 会话 | 一轮一次会话（预取），懒取兜底 | N → 1 | **N → N** |

xingyao 两条车道都让会话数与查询数按分片数同步下降，而不是只降会话数：ADR-016 已记录
`query_kline` **原生接受 code 列表**，Phase 0 探针本身就是每批 1000 只取回的
（[ADR-016](../../adr/016-xingyao-baostock-succession.md) §decision 11）。本设计因此把"一份请求
= 一次多 code 调用"作为 xingyao 的批量边界。日线当前 661 只确定以 1 片为目标；因子通道是否
也是 1 片由自己的端点探针冻结，不从日线结果推断。

**tdx 的收益只在会话数上**：`fetch_xdxr_frames` 复用一个 client 会话，但内部仍逐个 symbol 调
`get_xdxr`（[tdx.py](../../../src/stock_quant/data_sources/tdx.py)）。它的适配器层**没有**多 code
查询能力，所以候选超集**会真实增加 TDX 查询数**（超集 ⊇ 实际被消费的标的），只换掉会话建立
成本。这条如实记入 §7 D5，不并入"调用数下降"的说法。

不变的：`DataRequest`、`request_key`、`RawSnapshot` 路径布局、`REUSABLE_CHANNELS`、
`_isolated.py` 的"父进程可杀子进程"边界、`_dispatch` 的"先复用、后联网"契约。
**一个标的 = 一次逻辑请求 = 一份证据（有行是证据，明确的空响应也是证据；缺 key 不是证据，
是告警）**是本次设计的核心不变量；同时，每份逐标的证据都绑定实际多-code传输请求的完整形状，
不能把批量调用伪装成 N 次独立调用。

不在范围内：tushare 主车道（无会话成本；其"窗口驱动"与"按日整市场"形状相反）、akshare
公司行为车道（披露接口本身逐标的，无批量可做）、失效 baostock 车道的删除。

## 1. 事实基础

- **会话固定开销 F 是 per-request 常数。** `XingyaoSource.fetch` 每次走 `run_isolated` fork
  子进程；`_fetch_kline` 在子进程内 login、构造 `MarketData(base.get_calendar())`、查询、
  logout。父进程按设计**永不调用 SDK**，所以 `_RealClient._market` 在父进程恒为 `None`，
  每个子进程必然重建一次日历——该开销在现有设计下不可摊薄。
- **Phase 0 已实测**：单会话固定开销 F < 0.0025 单位（计数器粒度 0.01 单位 = 10MB），真值
  不可分辨；逐标的接线使全宇宙单轮落在 ≈0.8%–167% 周配额区间，时间上每轮 45–60 分钟纯等待。
- **SDK 原生支持多 code 批量**：`_RealClient.query_kline` 已经把 code 作为**列表**传给
  `self._market.query_kline(...)` 并读回以 code 为键的 dict；Phase 0 探针按 1000 只/批取回。
- **日线与因子是不同端点**：Phase 0 的 1000-code 实测只钉住 `query_kline`；因子通道调用
  `get_backward_factor(code_list)`，不能把日线端点的 code 上限、耗时和失败形态直接套给它。
  两个端点分别探测、分别配置（§5/§6）。
- **`daily_bar` 的增量策略是 `last_covered_plus_1`**，窗口每轮前进，`request_key` 每轮全新，
  ADR-015 的 raw 复用在增量轮上必然 miss；复用在批量改造前后效力一致，但**重建/重跑同一窗口
  时仍会命中**，所以批量路径必须保留复用契约（§5）。
- **tdx 拥有批量能力却未被批量调用**：`fetch_xdxr_frames(symbols: Sequence[str])` 内一个
  `async with client` 覆盖整个序列，唯一调用点传的是 `[symbol]`。
- **tdx 与因子通道的消费发生在逐标的 reconciliation 之内**：`_build_action_arbiter` 与
  `_build_price_basis_settler` 在 reconcile 循环**之前**构建，而 `arbiter`/`price_basis` 在
  循环**之内**按标的消费。所以候选集只能从 reconcile 的**输入帧**推出（§3）。
- **停牌在星耀侧是"无行"**（ADR-016 decision 11，601238.SH 九个停牌日一行不返）。全宇宙批量
  里必然含若干"本窗口合法无数据"的标的，空响应语义必须先定。**"无行"的形态是"帧空"还是
  "答里没有这个 code"，Phase 0 未区分**——列为探针项（§6）。
- **空响应当前在所有通道都是 `ContractError`**：`validate_supplier_frame` 的 `allow_empty`
  默认 False，`TushareSource._validate` 等一律不放开。所以 D2 的"空响应 = 答案"是**新语义**，
  不是沿用现状；相应地，"逐请求空证据"也是新增的证据形式（§4）。

## 2. 已确认的范围决策（owner，2026-09-27）

| # | 决策 | 取值 |
| --- | --- | --- |
| 1 | 范围 | xingyao 校验车道 + 因子通道 + tdx 仲裁器；主车道 tushare 与 akshare 不动 |
| 2 | 空响应语义 | **算答案**（显式空证据），不计失败、不压 status。**边界**：本设计的 `empty` 仅指"供应商返回了零行对象"；"答中无该 code"默认 fail-closed 归 `refused`（§4），该边界若与探针 2 实测冲突须回到 owner |
| 3 | 批量粒度 | 分片批量，**片大一些**；片大小以 SDK 原生上限（1000 code）为默认上限 |
| 4 | 启用边界 | **只落通道**，`xingyao.enabled` 保持 `false`；启用另开一次改动 |

## 3. 批量边界与分片

**xingyao：一片 = 一次 worker = 一次多 code 查询。**

- worker 收 `symbols: list[str]`：一次 login → 一次日历 → **一次 `query_kline(codes, begin,
  end)`** → 一次 logout。返回以 code 为键的 dict，父进程扇出成逐标的证据（§4）。
- `XingyaoSource.fetch`（单标的，测试与 `project/` 探针在用）**委托给同一个 worker**，传长度
  1 的列表。不新增第二条实现路径。
- 片划分：`_fetch_validation_daily` 把 `equity_symbols` 按 `batch_size` 切片；默认上限取 SDK
  原生 1000。**当前 661 只全宇宙 = 1 片 = 1 会话 = 1 次查询**；宇宙超过 1000 时才分片。
- 因子通道同样收 code 列表，与 tdx 共用同一个候选收集阶段（见下），但使用自己实测冻结的
  `factor_batch_size` / `factor_batch_timeout_seconds`；候选数超过上限时独立分片。

**候选收集阶段：tdx 与因子通道共用一次预扫描。**

把候选集从"reconcile 产出"改为"reconcile 的**输入**"，消除循环依赖：

- 时机：逐标的公司行为抓取循环结束之后、`_build_action_arbiter` **之前**。此时
  `frames_by_symbol`（cninfo/eastmoney/rights 三端点的原始帧）已齐备，正是 reconcile 的输入。
- 候选集 = 在该窗口内**任一 CA 端点有行**的 symbol 并集。这是"会被消费的标的"的保守超集
  （真正的冲突要 reconcile 之后才知道），代价是有界过取；tdx 与因子通道都是一次会话，
  超集不增加会话数，只增加该次会话内的 code 数。
- 一次预扫描喂两条通道：`_TdxArbiter.prefetch(symbols)` 与因子通道的 `prefetch(symbols)`。
- **兜底**：两者的 `frame_for(symbol)` 懒取路径都保留。集合算漏时只是多一次会话，不会少取
  证据（fail-open 行为、fail-closed 证据）。
- 集合为空时不建立任何会话，保住 `sources.yml` 注释里"a clean round makes none"的性质。
- 被否决的更精确方案：两阶段 reconcile（先用候选集取数、再产出）会改变 reconcile 的结构与
  既有证据顺序，超出本设计范围；保守超集的小代价换结构不动。

## 4. 逐标的证据与失败语义

**生产者是适配器父进程，不是子进程。** 子进程只回**批次原始 dict**（以及会话级错误），它不做
逐标的判断——一次多 code 调用不会产生"某 code 抛错"，dict 就是供应商的答案。逐标的三态由
**适配器父进程**（`fetch_batch`，非 fork 出的 worker）产出：拆 dict、判定缺席、逐 code 套用
`validate_supplier_frame`。

**三种子情形，且不合成任何帧。**

| 子情形 | 车道判定 | 父进程动作 | validation 行 | 该源 status |
| --- | --- | --- | --- | --- |
| 该 code 的帧**有行** | `ok` | 落快照（供应商原对象）+ normalize | 有 | 不受影响 |
| 该 code 的帧**存在但零行** | `empty` | **落该帧本身**（供应商原对象），不落数据行 | 无 | 不受影响（算答案） |
| **答中无该 code** | `refused` | 逐标的告警，带 `symbol` | 无 | `partial_fetch_failure` |
| **整片失败**（会话/批级） | — | 只写**片级**记录 | 无 | `ok=False`，`reason_code="batch_fetch_failure"` |

`empty` **只在"供应商确实返回了一个零行对象"时成立**，所以落盘的是供应商的原对象，是真
raw snapshot，不需要、也不允许合成帧——上一版为"缺 key"构造零行帧的做法已删除：那种帧的列
来自别的标的或适配器声明，不是供应商返回的东西，后续 `project/drift_audit.py` 用单标的请求
重取时也无从复现。

**"答中无该 code"默认 `refused`（fail-closed）。** 缺席的原因不可辨：截断、非法 code 被静默
丢弃、供应商漏答，都可能。上一版用"调用成功即视为对该 code 的回答"来论证映射为 `empty`，
该论证不成立——它确实不改变 `validation_present`（分类不受影响），但会改变告警、
`SourceStatus.ok` 与 `reason_code`，那是运行健康判断，不是审计措辞。所以默认取 fail-closed
方向，只有供应商契约或可区分的返回标志能证明"缺 key = 合法无数据"时才改判 `empty`。

**这个默认值与决策 2 的边界必须讲清**（也是本设计唯一一处可能需要回到 owner 的地方）：

- 决策 2 说"某标的在本窗口合法无数据"是答案。**常见的停牌场景并不落在空帧上**：窗口里既有
  交易日又有停牌日时，返回的是含交易日行的非空帧，即 `ok`——停牌日只是该标的行集里的缺口，
  这是既有正常行为，不需要任何空语义。
- 真正为空的是**窗口内一个交易日都没有**的标的（长期停牌、退市、新上市）。这类标的在星耀侧
  的返回形态，Phase 0 **没有区分**是"零行帧"还是"缺 key"（§1）。
- 探针 2（§6）就是这一条：窗口内只有停牌日时，dict 里是"有 key、帧空"还是"无 key"。
  **若实测为"有 key、帧空"**，决策 2 完整落地，无额外噪音。**若实测为"无 key"**，则本设计
  会把每个长期停牌标的每轮记为逐标的 `refused`（`partial_fetch_failure`），这是决策 2 想要
  避免的噪音——此时**必须回到 owner**：要么接受该噪音，要么为"缺 key"引入一个需要供应商契约
  支撑的独立状态。设计不替 owner 做这个选择。
- **`empty` 分支的必要性也是探针 2 的输出**：若探针显示该供应商在任何情形下都不返回零行
  对象（停牌一律表现为缺 key），那么实现里就不该留 `empty` 分支——不推测性地保留一个实测
  不可达的状态。反之若探针看到零行对象，`empty` 必须有，且按上表落供应商原对象。

**失败分三层，不压成一层。**

1. **会话/批级失败**（login 被拒、会话中途死掉、整次查询超时）→ **整片失败**。失败先按类型
   分流：`AuthenticationError`、配置错误、批答整体非 dict 等永久/整体契约错误立即终止，**不重试、
   不二分**；`RateLimitError` 只按既有退避策略重试同一片，耗尽后终止，**不靠拆小规避限流**；
   `TimeoutError` / `ServerError` 先按既有策略重试同一片，耗尽后才进入第 3 条的二分。任何批级
   失败都**不记录片内已成功标的的部分证据**——归因必须是"这一片没问成"，不能变成"某些标的
   成功、某些失败"。失败记录按**片**给出（批次标识、片大小、标的范围），不按标的拆。
2. **片内单标的拒答**：批答成功，而该 code 的帧形状破约（列不齐、dtype 无法归一）。判定者是
   **适配器父进程**（§4 开头），该标的逐标的告警并计入 `partial_fetch_failure`，其余照常。
   "答中无该 code"按 §4 默认也走这一层（fail-closed）。
3. **片级失败后的二分重试是重试策略，不是归因策略**：只有同片既有重试耗尽后的
   `TimeoutError` / `ServerError` 才二分（最多 2 次：1 → 2 → 4 片）。认证、配置、整体契约和
   限流失败不二分。**任何终止片级的失败都只产生片级记录，绝不产生逐标的 `refused`。**
   逐标的 `refused` 的唯一来源是第 2 层，即"批答成功、该标的被点名拒答"。这样既不会把
   一个慢 code 的代价记到同片其他标的上，也不与第 1 层冲突。
4. **终止的片级失败必须给该源一个稳定标签**：片（含二分后的子片）最终失败时，本次运行里
   **该源的 `SourceStatus.ok = False`**，`reason_code = "batch_fetch_failure"`，`reason` 带批次
   标识、片大小与标的范围。这是词汇新增（§4 末：可观测契约变化）：没有它，一个整片失败的源
   会以"零标的、零告警"的形态安静通过——它今天不可能出现（逐标的下每个失败都落到具体标的
   上），批量路径下必然出现。`batch_fetch_failure` 与逐标的的 `partial_fetch_failure` 并列，
   两者都非阻塞（与既有 `partial_fetch_failure` 同级）。
5. **零行帧的校验规则单独放宽**：`validate_supplier_frame` 的"返回标的集合 == 请求标的集合"
   检查对零行帧必然失败（`returned_symbols` 为空集，[base.py:229-236](../../../src/stock_quant/data_sources/base.py)），
   而 `allow_empty=True` 只跳过 `frame.empty` 那一关，跳不过集合相等那一关。所以空帧的校验
   改为：仍要求 supplier frame、未标记 `truncated`、必需的 symbol/date 列存在，但显式跳过
   "列值推出的标的集合相等"与日期值范围检查（零行没有值可检查）；非空帧走原检查不变。这条
   是 `allow_empty=True` 的明确语义补全，并必须在 ADR-020 里写明为什么集合相等对空帧无意义。
6. 重试幂等（同一 request key → 同一内容寻址路径），不会写重。

**空响应证据：逐请求落快照，但永不参与复用。**

这是**新语义**（§1 末条：空响应当前处处是 `ContractError`），按 ADR-015 的立场分两层：

- **落证据**：`empty` 子情形（供应商确实返回了零行对象）写一份该请求的 raw snapshot——与
  `ok` 走同一个 `RawStore.save`，同一路径布局
  （`<source>/<endpoint>/<transport_id>/<request_key>/<sha>`）。落进去的是**供应商的原对象**
  （§4：不合成帧），所以"某标的在本窗口被明确答以无行"是**可逐标的复核**的，不再只有聚合
  计数，也不需要额外的 `empty_origin` 标记：`empty` 一律指"有对象、零行"，`refused` 一律指
  "没拿到对象"，两者靠**有无快照**就能区分。缺 key 因而不落任何快照，它落的是逐标的告警。
- **拒绝复用**：ADR-009 的 "empty is absence, not an answer" 管的是**复用资格**，这条不变。
  `resolve_reusable` 的 `allow_empty` 默认 False，读到的空帧本来就不会被服务回来——复用层的
  既有行为已经实现了这条禁令，无需新增机制。
- 谓词分工写进 ADR-020 D2：**复用资格看 ADR-009（空帧不可回放），失败记账看本设计（明确的
  空响应是答案，缺席不是）**。

**账本：保留逐请求计数，另加会话/查询计数。**

`_count_fetch` 现有的逐请求 `{reused, fetched}` 是**构建证据**，不改语义（一次复用命中就是
一次 `reused`，一次联网取回就是一次 `fetched`）。批量带来的新维度另开独立计数器：
`sessions`（一次 supplier client/session 建立 attempt）与 `code_queries`（供应商实际执行的
code-bearing 查询次数；xingyao 一次多-code调用计 1，TDX 每个 `get_xdxr(symbol)` 计 1）。一轮的期望读数：
`sessions` 661 → 1，`code_queries` 661 → 1，`fetched` 仍按标的计但实际联网标的数因复用而
可能更少。三个数分开报，避免把"证据计数"和"成本计数"混成一个数。

新计数器由**父进程**持有，按 source × endpoint 累加“已尝试的传输操作”，而不是从子进程对象
读取：每次准备启动 worker/client attempt 时先记账，所以成功、超时、重试和二分调用都不会漏。
xingyao 每次批 worker attempt 记 `sessions += 1, code_queries += 1`；TDX 一次预取记
`sessions += 1, code_queries += len(symbols)`，懒取兜底每次各加 1。账本 JSON 保留既有顶层
`calls` / `endpoints` / `reused`，新增：

```json
"transport": {
  "daily": {"sessions": 1, "code_queries": 1},
  "backward_factor": {"sessions": 1, "code_queries": 1}
}
```

`call_ledger.json` 改为在一次 update 的统一终止路径写出：成功发布、质量阻塞、源失败都持久化，
从而失败重试消耗也不会从账本消失；同一 run 只写一次最终累计值。该账本仍是运行证据，不进入
数据集身份；逐请求 `{reused, fetched}` 继续进入既有 build evidence，二者不混用。

**不变的部分**：每个 `ok`/`empty` 标的仍产生自己的 raw snapshot、自己的 `request_key`
（单标的形状）和 `request_metadata`；`refused` 没有 raw 对象，但由逐标的告警与
`BatchRequestEvidence` outcome 留痕。`REUSABLE_CHANNELS` 中 `("xingyao","daily")` 的复用资格
与匹配规则原样保留。

**可观测契约的变化**：`SourceStatus` 的 reason/reason_code 词汇扩充（片级失败 vs 逐标的
失败）。既有"all sources ok"类断言与按"每标的=一次调用"写死的计数断言需按新语义更新，不得
放宽。

## 5. 接口、配置与超时耦合

**适配器接口：新增能力，不改 Protocol。**

- `XingyaoSource.fetch_batch(requests: Sequence[DataRequest]) -> list[_BatchOutcome]`，前提约束：
  同一 endpoint、同一窗口、每请求恰好一个标的。返回列表与入参请求**逐位对齐**——这是复用
  分片（见下）能安全剔除命中项的前提。
- `FetchResult` 的组装（`request_key`、`request_metadata`、`request_timestamp`、
  `response_timestamp`）留在适配器内——SDK 版本与时间戳只有适配器知道，这是它与今天的
  `fetch` 逐字相同的那段代码。**注意**：多 code 调用下逐标的响应时间戳来自同一次调用，
  设计如实记录：`request_timestamp` = 调用发出时刻，`response_timestamp` = 调用返回时刻，
  同一片内一致。
- **逻辑请求与真实传输请求分层记录**：既有逐标的 `request_parameters` / `request_key` / raw
  路径保持不变，继续服务 reuse；每次成功批答另生成一个不可变 `BatchRequestEvidence`：完整有序
  `batch_request_parameters = {endpoint, symbols, start_date, end_date, params}`、由其 canonical
  JSON 派生的 `batch_id`、请求/响应时间戳，以及每个 symbol 的
  `{request_key, outcome, snapshot_file_sha256|null}`。缺 key/破约的 `refused` 也因此留在批次事实中，
  但没有伪造 raw snapshot。
- `BatchRequestEvidence` **不能只塞进逐标的 raw manifest**：`RawStore.save` 会按
  `request_key/file_sha256` 复用已经存在的快照目录；若相同帧先由单标的请求保存，后来的批次
  metadata 会被旧 manifest 吞掉。为保持 raw snapshot 目录不可变，批次证据写到独立的内容寻址
  registry：`data/raw_batch_requests/<source>/<endpoint>/<transport_id>/<batch_id>/<evidence_sha>.json`。
  数据集 `build_config.batch_request_evidence` 绑定本轮使用的证据哈希；成功发布与失败 run 都在
  自己的证据记录中引用它。复用一个带批次来源的 snapshot 时，解析器按 snapshot identity
  （source/endpoint/transport/request_key/file_sha256）带回已绑定的批次证据；旧的单请求 snapshot
  没有映射时保持 legacy 单请求语义。
- `project/drift_audit.py` 按版本绑定的 `BatchRequestEvidence.batch_id` 聚合同一次批答，以完整
  `batch_request_parameters` **重放原批次一次**，再按 code 拆回逐标的比较。新批次证据不得降级
  成单标的重问，因为缺 key、截断和返回形状可能依赖批次组成。二分后的成功子片各自生成实际
  子片的证据，不沿用失败父片的 `batch_id`。
- **不扩 `DataSource` Protocol**。车道用鸭子类型探测可选能力
  （`getattr(source, "fetch_batch", None)`），与既有的 `getattr(arbiter, "frame_for", None)`
  一致——tushare/akshare 一行不改。
- **帧校验按标的应用，且生产者是父进程**：子进程只回批次原始 dict 与会话级错误，`_BatchOutcome`
  由**适配器父进程**（`fetch_batch`）产出，`validate_supplier_frame` 的判据因此只留在父进程
  一处，不下放、不复制。逐 code 调本地帧（**不联网**），按 §4 的三种子情形映射：帧有行且过检
  → `ok`；帧零行 → `empty`（仍校必需列和 truncated 标志，**跳过标的集合相等/日期值检查**，因为
  `allow_empty=True` 单独用是过不去的）；帧破约 → `refused`；dict 里没有该 code → `refused`
  （fail-closed，除非有可区分的供应商契约依据）。

**批量路径必须保留 ADR-015 的"先复用、后联网"契约。**

`_dispatch` 今天逐请求做三件事：查 reusable snapshot（命中即用、计 `reused`）、对被拒候选出
`reuse_candidate_rejected` 告警、只对 miss 发网络请求。批量路径逐片复现同一顺序：

```
for chunk in chunks(symbols, batch_size):
    pending, hits = [], []
    for symbol in chunk:
        resolved = raw_store.resolve_reusable(name, "daily", req(symbol))
        if resolved: hits.append(...)                     # 计 reused，不联网
        else:
            if raw_store.has_candidate(name, "daily", req(symbol)):
                issues.append(reuse_candidate_rejected)   # 被拒候选照旧可见
            pending.append(symbol)
    if pending:
        outcomes = source.fetch_batch([req(s) for s in pending])   # 只带 miss
```

要点：**只有 miss 进批量**，命中项不产生任何网络请求；`_BatchOutcome` 与 `pending` 逐位对齐，
所以扇出时不会错位。复用命中的结果与联网结果走同一条下游（normalize、证据落盘）。

**配置：两个端点各有一对批量字段。**

`SourceConfig`（`extra="forbid"`，加字段不需要改 yml，但两份 yml 要补注释）：

- `batch_size: int`、`batch_timeout_seconds: int` —— `query_kline` 校验车道的片大小与进程界。
- `factor_batch_size: int`、`factor_batch_timeout_seconds: int` ——
  `get_backward_factor` 因子通道的片大小与进程界。

两端点分别由 §6 探针冻结，不互相外推。这样日线已实测的 1000-code 能力不会被一个更窄的因子
上限拖小，因子端也不会误用日线的超时。每一对内部必须同时配置；只给 size 或只给 timeout 是
配置错误。候选池超过 `factor_batch_size` 时因子通道分片，收益写成
`ceil(candidate_count / factor_batch_size)` 个会话/查询，而不是无条件声称为 1。

为什么不复用 `timeout_seconds`：后者的仓库语义是**一次供应商调用**的界
（`default_request_timeout`、`RetryPolicy.call_timeout_seconds` 都这么用），现在还被当作
xingyao worker 的进程界。片含 1000 只后，同一个数要么小得必然超时，要么被放大成千倍。耦合
写成显式规则，由探针给数：

```
batch_timeout_seconds ≥ 实测「query_kline 全片调用」耗时 × 安全系数
factor_batch_timeout_seconds ≥ 实测「get_backward_factor 全片调用」耗时 × 安全系数
```

**超时/临时服务错误后二分重试（重试策略，非归因策略）。** 当前片的 `TimeoutError` /
`ServerError` 按既有策略重试耗尽后 → 二分，最多 2 次（1 → 2 → 4 片）；永久错误与限流不二分。
二分到底仍失败的片按 §4 第 1/3 层记为**片级失败**：只写片级记录，
**该源 `ok=False` 且 `reason_code="batch_fetch_failure"`**，其标的**不产生逐标的 `refused`**。
效果：一个慢 code 只多花一次二分重试的调用，不会把同片 300 个标的标成拒答；代价只出现在
故障路径（1 → 2 → 4 次调用），常态路径仍是一次。同一轮若同时有片级失败与逐标的 `refused`，**`reason_code` 取片级**
（`batch_fetch_failure`，更重），两种计数都写进自由散文 `reason`——`SourceStatus` 每源只有
**一个** `reason_code` 字段，且它是唯一进入数据证据的稳定码（[data_pipeline.py:292-303](../../../src/stock_quant/data_pipeline.py)），
所以不能两个码并列，也不新增字段。

**不变量**：`DataRequest`、`request_key`、`RawSnapshot` 路径布局、`REUSABLE_CHANNELS`、
`_isolated.py` 的进程边界——全部不变。

## 6. 测试、探针与验收

**单元测试**

| 文件 | 断言的可观察行为 |
| --- | --- |
| `tests/unit/test_xingyao_source.py` | 假 SDK 上 N 个 code → login 恰好 1 次、日历构造恰好 1 次、**`query_kline` 恰好 1 次**、logout 恰好 1 次；逐 code 三态映射由**适配器父进程**产出（有行→`ok`、有 key 零行→`empty`、**无 key→`refused`**）；某 code 帧破约不中断该片；`fetch(request)` 与 `fetch_batch([request])` 走同一 worker；返回的 `BatchRequestEvidence` 可重建实际 code 列表且 `batch_id` 等于其 canonical hash |
| `tests/unit/test_xingyao_batch_lane.py`（新） | 切片数与 `batch_size` 一致；**复用命中不进批量**（部分命中时联网请求只含 miss，且命中项计 `reused`）；被拒候选仍出 `reuse_candidate_rejected`；`empty` 落**供应商原对象**快照（不合成帧）但不进复用（`resolve_reusable` 对该请求返回 None）且不压 status；`refused` 出逐标的告警且 status=`partial_fetch_failure`；**片级失败只出一条片级记录、不产生逐标的 refuseds，且该源 `ok=False`、`reason_code="batch_fetch_failure"`**；片级与逐标的失败同轮时 `reason_code` 取片级；认证/整体契约/限流失败不二分，超时/`ServerError` 重试耗尽后才二分；三个计数器（reused/fetched、sessions、code_queries）包含失败 attempt 的读数 |
| `tests/integration/test_source_contracts.py`（加例） | **零行帧的校验规则**：`validate_supplier_frame` 对零行帧仍要求必需的 symbol/date 列且拒绝 truncated，**跳过**标的集合相等/日期值检查；同一函数对非空帧仍执行既有检查（不允许被放宽）|
| `tests/unit/test_xingyao_factor.py` | 因子通道按 `factor_batch_size` 对候选池分片，每片一次多-code调用；使用 `factor_batch_timeout_seconds`；`frame_for` 懒取兜底不变；失败缓存语义不变 |
| `tests/unit/test_tdx_arbiter.py` | 候选池预取只进入 client 一次；**空池不建立会话**；预取漏标的时 `frame_for` 懒取兜底仍工作 |
| `tests/unit/test_raw_reuse.py` | **取证不变式**：同一窗口下批次写入的某标的快照，其路径与单标的写入逐字节相同，且 `resolve_reusable` 对该单标的请求命中；空快照写入后 `resolve_reusable` 仍拒绝服务它 |
| `tests/unit/test_batch_request_evidence.py`（新） | 批次证据使用独立内容寻址 registry；同一逐标的 parquet 先单取、后批取时不会因 raw manifest 去重丢失批次 provenance；outcomes 完整绑定 answered/empty/refused 与 snapshot identity；复用时可带回已绑定批次证据 |
| `tests/unit/test_drift_audit.py` | 新批次证据按 `batch_id` 聚合并以完整 `batch_request_parameters` 重放原批次一次；拆分结果逐标的比较；旧快照继续走单请求兼容路径 |
| `tests/unit/test_call_ledger.py` | `transport` 按 source × endpoint 渲染；成功、超时、重试、二分都统计 attempted sessions/queries；失败 update 也持久化最终 ledger；既有 `calls/endpoints/reused` 形状不变 |

最后一条是本次改动最重要的回归护栏：把"批量只改变一次会话服务多少标的，不改变一个标的=
一次请求=一份证据"钉成可执行断言。

表中 `empty`/`refused` 相关的用例按**探针 2 冻结后的映射**写（§8）：探针显示供应商不产生
零行对象时，`empty` 分支连同其用例一并删除，而不是留一条不可达的断言。

**必须新增的真实探针**（`project/`，在冻结默认值之前跑；`xingyao.enabled: false` 下也能跑，
它直接驱动适配器、不经车道）

1. **两个端点分别测上限**：`query_kline` 以 ADR-016 记录的 1000 为起点上下试探，直到 SDK 或
   服务端拒绝（缺键、报错、截断）→ 决定 `batch_size`；`get_backward_factor(code_list)` 独立
   递增候选数并检查列完整性/截断/错误 → 决定 `factor_batch_size`。一个端点的上限不得外推给另一个。
2. **本设计唯一可能回到 owner 的探针**——"答中缺席"的形态：取一个**窗口内一个交易日都没有**
   的标的（长期停牌/退市/新上市；用 601238.SH 的 2026-09-14..09-24 之类纯停牌窗口，注意
   ADR-016 已实测星耀对停牌日返"无行"，所以这个样本命中率高），观察多 code 调用的返回是
   "有该 key、帧空"还是"无该 key"。
   - **"有 key、帧空"** → 决策 2 完整落地，`empty` 走 §4 的落快照路径，无逐标的告警。冻结。
   - **"无 key"** → §4 的 fail-closed 默认会让每个这类标的每轮记一次逐标的 `refused`
     （`partial_fetch_failure`）。这与决策 2 想避免的噪音直接冲突，**必须带着实测样本回到
     owner**：要么接受该噪音，要么为"缺 key = 合法无数据"引入一个需要供应商契约支撑的独立
     状态（届时它是本设计之外的一处新增语义）。
   - 顺带记录：窗口内**既有交易日又有停牌日**的标的返回什么（预期是非空帧、`ok`，用来确认
     "常见停牌不落在空语义上"，不需要新语义）。
3. **两个端点分别测全片耗时**：`query_kline` 决定 `batch_timeout_seconds`；
   `get_backward_factor` 决定 `factor_batch_timeout_seconds`，各自乘安全系数。
4. 一轮真实会话数与三个计数器：日线 `sessions`/`code_queries` 应从 661 降到 1；因子按实际
   `candidate_count` 与 `factor_batch_size` 验证为 `ceil(candidate_count/factor_batch_size)`；故障
   注入同时验证 attempted counters 包含重试与二分调用。

探针结论落 `docs/operations/`，沿用 Phase 0 那份的记法（含 `.evidence.json`）。

**验收**

```bash
pytest tests/unit/test_xingyao_source.py tests/unit/test_xingyao_factor.py -q
pytest tests/unit/test_tdx_arbiter.py tests/unit/test_raw_reuse.py tests/unit/test_raw_store.py tests/unit/test_batch_request_evidence.py -q
pytest tests/unit/test_isolated_call.py tests/unit/test_xingyao_batch_lane.py tests/unit/test_call_ledger.py -q
pytest tests/unit/test_drift_audit.py -q
pytest tests/integration/test_pipeline_fetch_coverage.py tests/integration/test_source_contracts.py -q
```

按决策 4，**一次真实更新不在本次验收范围内**——`enabled` 仍为 false，通道落地即止。真实更新
的验收属于翻开关那次改动。

## 7. 文档与 ADR 归属

**新增 ADR-020（`docs/adr/020-batched-validation-channel.md`）**

| 决策 | 内容 |
| --- | --- |
| D1 | 批量边界：一次会话 + **一次多 code 调用**服务一片；一个标的=一次逻辑请求=一份证据是不变量，且证据必须绑定可重建的真实批次请求 |
| D2 | 空响应语义：`empty` **仅指"供应商返回了零行对象"**（落该对象为逐请求快照、不计失败、不压 status、**不进复用**）；**"答中无该 code"默认 fail-closed 归 `refused`**，除非供应商契约或可区分标志能证明"缺 key = 合法无数据"；与 ADR-009 分属两层——后者管复用资格（不变），本决定管失败记账（新增） |
| D3 | 失败三层：批级失败按片记账、绝不伪造逐标的拒答，且终止片级失败使该源 `ok=False`、`reason_code="batch_fetch_failure"`；逐标的 `refused` 只来自批答成功下的点名拒答或缺失 key；只有同片重试耗尽后的 timeout/`ServerError` 可二分，认证、配置、整体契约与限流失败不二分 |
| D7 | 零行帧的校验放宽：`validate_supplier_frame` 对零行帧仍要求必需 symbol/date 列且拒绝 truncated，跳过无值可判的标的集合相等/日期值检查；非空帧检查不变 |
| D4 | 门禁关系：本 ADR 满足 ADR-016 decision 11 的前置；`enabled: true` 仍由单独的启用改动决定 |
| D5 | tdx 与因子通道应用同一原则：候选池预取 + 懒取兜底，空池不建立会话；候选集取自 reconcile 的**输入**帧（无循环依赖）。**代价如实记录**：tdx 适配器层无多 code 查询能力（`_fetch_xdxr` 内 `for symbol in symbols: await getter(symbol)`），预取只把**会话数** N→1，code 查询数仍是 N，且候选超集使其**真实增加**（超集 ⊇ 实际被消费的标的）；xingyao 因子端点独立探测/配置，收益为分片数而非无条件为 1 |
| D6 | 批量路径保留 ADR-015 的"先复用、后联网"契约：命中项不进批量；父进程按 attempted operations 记录 `sessions`/`code_queries`，失败、重试、二分均计入，并在所有 update 终态持久化 ledger；不改逐请求计数语义 |
| D8 | 双层请求 provenance：逐标的 `request_parameters`/`request_key` 保持逻辑请求；独立内容寻址的 `BatchRequestEvidence` 记录完整有序的真实传输请求及逐标的 outcome/snapshot identity，避免 raw manifest 去重吞掉批次 metadata；版本绑定其证据哈希，drift audit 按批次原形重放，新证据不得降级成单标的重问 |

**ADR-016 只加注记，不改写 decision 11。** 该决策未被推翻，其条件被满足了。按仓库规则
（replace a decision with a superseding ADR, not a silent rewrite），在 ADR-016 内加一条指向
ADR-020 的注记："批量通道前置已落地（ADR-020）；启用仍是单独动作，decision 11 的门禁在启用
动作上继续有效。"

**必须同步的既有文档**

- [RUNBOOK.md](../../../RUNBOOK.md) 的"xingyao 启用程序（前置：批量通道）"→ 前置标注已落地；
  `enabled` 的值不动。
- RUNBOOK 中"四个逐符号车道"的说法要修正（xingyao 车道不再是逐符号请求）。
- [data-flow.md](../../architecture/data-flow.md) 的 "each per-symbol request on the four
  `_dispatch` lanes" 补批量形状一句。
- `docs/adr/DECISIONS_INDEX.md` 加 020 行（`tools/check_context_governance.py` 校验索引里的
  ADR 链接可达，漏了会红）。
- `project/configs/sources.yml` 与 `templates/project-config/` 两份：xingyao 段补日线/因子两对
  批量字段的
  注释 + "批量通道已落地、启用待单独改动"。
- `project/drift_audit.py`：读取版本绑定的 `BatchRequestEvidence`，按 `batch_id` 聚合重放；旧快照的单请求
  路径保持兼容。
- 新增批次证据模型/存储：`BatchRequestEvidence` 写入 `data/raw_batch_requests/` 的独立内容寻址
  registry，并由 build evidence / failed run 绑定；不得写进或追加到既有 raw snapshot 目录。
- 接替 spec §3.1 加一行回写指向 ADR-020。
- 新 operations 记录：四项探针实测 + 本次改动证据。

## 8. 待冻结的常量

以下五项由 §6 的探针实测后冻结：

| 常量 | 确定方式 | 是否可能回到 owner |
| --- | --- | --- |
| `batch_size` 默认值 | 探针 1 实测的单次 code 上限（ADR-016 记 1000，需本环境复核） | 否 |
| `batch_timeout_seconds` 默认值 | 探针 3 实测的全片调用耗时 × 安全系数 | 否 |
| `factor_batch_size` 默认值 | 探针 1 对 `get_backward_factor` 独立实测的单次 code 上限 | 否 |
| `factor_batch_timeout_seconds` 默认值 | 探针 3 对因子全片调用独立实测的耗时 × 安全系数 | 否 |
| §4 的空/缺席映射与 `empty` 分支是否保留 | 探针 2 实测："有 key、帧空"→保留 `empty`；"无 key"→ §4 的 fail-closed 默认生效，需 owner 裁定噪音 | **是**（唯一一项） |

## 9. 评审回应（修订 2）

| 发现 | 处置 |
| --- | --- |
| P1-1 批量边界与门禁矛盾 | **成立**，且比评审所述更硬：ADR-016 已写明 `query_kline` 原生收 code 列表、探针每批取 1000 只。已改为**一片一次多 code 调用**（§3/§5），会话数与查询数都降到 1；探针与"满足前置"的结论按此重建 |
| P1-2 tdx 预取循环依赖 | **成立**。已改为从 reconcile 的**输入帧**（`frames_by_symbol`）推候选超集，时机在 `_build_action_arbiter` 之前（§3）。两阶段 reconcile 作为备选被记录并否决 |
| P1-3 二分终止违反两套语义 | **成立**。已定：终止片的失败**只产生片级记录，绝不产生逐标的 refused**；逐标的 refused 的唯一来源是"批答成功下的点名拒答"（§4 第 3 层/§5） |
| P1-4 空响应无可复核证据 | **结论成立，前提需更正**：当前空响应在**所有**通道都是 `ContractError`（`allow_empty` 默认 False），并非"`_dispatch` 也会保存空帧"。已改为**逐请求落空快照 + 拒绝复用**（§4），并如实标注这是新语义。**修订 3 收窄**：落快照只适用于"供应商返回了零行对象"，缺 key 不落快照（§10）|
| P1-5 批量路径丢失 reuse 契约 | **成立**。已补逐片的"先复用、后联网"分片算法（§5），命中项不进批量；账本保留逐请求 `{reused, fetched}` 语义，另设 `sessions`/`code_queries` 计数 |
| P2 因子通道缺需求收集阶段 | **成立**。因子通道与 tdx 共用同一次候选池预扫描（§3），并如实记录其代价：从"只抓真正被分类的标的"变为"抓候选超集" |

## 10. 评审回应（修订 3）

第二轮评审 5 项 P1 + 1 项措辞。总判断"批量边界、候选池和 reuse 算法已基本成立，最需要先定的
是缺 key 的证据模型"被采纳：本轮改法选了比评审建议更省的一条——**把缺 key 默认设为
fail-closed（`refused`）**，一次消掉两个问题（合成帧无从复现、空证据无逐标的可复核性），
`empty` 从此只来自"供应商确实返回了零行对象"。

| 发现 | 处置 |
| --- | --- |
| P1-A `allow_empty=True` 仍过不了标的集合相等检查 | **成立**（[base.py:220-236](../../../src/stock_quant/data_sources/base.py)：`allow_empty` 只跳过 `frame.empty`；零行帧的 `returned_symbols` 是空集，必然不等）。已在 §4 第 5 条单列零行帧的校验规则（仍校必需列/truncated，跳过无值可判的集合相等/日期值检查），并进 ADR-020 D7 |
| P1-B 合成零行帧不是 raw snapshot、drift audit 无从复现 | **成立**。已删除合成路径：`empty` 只指"供应商返回了零行对象"，落盘的是**该原对象**；缺 key 不落快照、只出逐标的告警（§4）|
| P1-C "缺 key = 空、安全性只是审计措辞"的论证不成立 | **成立，且是本轮的核心改动**。该论证错在只看 `validation_present`：缺 key 改判确实不动分类，但会改告警、`SourceStatus.ok` 与 `reason_code`——那是运行健康判断。默认已改为 fail-closed 的 `refused`（§4），并把与决策 2 的边界、以及探针 2 可能需要回到 owner 的分支写清（§2/§4/§6）|
| P1-D tdx `code_queries: N → 1` 错误 | **成立**。已核实 `_fetch_xdxr` 内是 `for symbol in symbols: await getter(symbol)`：会话 N→1，查询 N→N，且候选超集使其真实增加。§0 表已改 `**N → N**` 并加说明段，§7 D5 如实记录（不并入"调用数下降"的说法）|
| P1-E 终止片级失败未绑定稳定 `reason_code` | **成立**。已定：该源 `ok=False`、`reason_code="batch_fetch_failure"`（§4 第 4 条/§5）；同轮两种失败并存时 `reason_code` 取片级，两种计数写进 `reason` 散文——`SourceStatus` 每源只有一个 `reason_code` 字段且它是唯一进数据证据的稳定码，故不并列、不新增字段 |
| 措辞：`_BatchOutcome` 的生产者 | **成立**。已改为**适配器父进程**产出（子进程只回批次原始 dict 与会话级错误）——一次多 code 调用不会产生"某 code 抛错"，逐标的判断本就不该在子进程里（§4 开头）|

## 11. 评审回应（修订 4）

第三轮评审 2 项 P1 + 2 项 P2，均在不改变 owner 四项范围决策的前提下收口：

| 发现 | 处置 |
| --- | --- |
| P1-F 实际批次请求不可重建 | **成立**。逐标的逻辑 `request_parameters` 保持不变，另建独立内容寻址的 `BatchRequestEvidence`，记录完整有序 `batch_request_parameters`、逐标的 outcome 与 snapshot identity，并由 canonical hash 派生 `batch_id`；独立 registry 避免 `RawStore` 的同字节 manifest 去重吞掉批次 provenance，drift audit 对新证据按原批次聚合重放（§5、ADR-020 D8） |
| P1-G 因子端点误用日线批量参数 | **成立**。`get_backward_factor` 增加独立上限/耗时探针和 `factor_batch_size` / `factor_batch_timeout_seconds` 配置；因子收益按实际分片数表达，不再无条件写 1（§0/§5/§6/§8） |
| P2-H 永久失败也会二分 | **成立**。认证、配置、批答整体契约错误立即终止；限流只同片退避；只有 timeout/`ServerError` 同片重试耗尽后才二分（§4/§5、ADR-020 D3） |
| P2-I 成本计数器缺持久化口径 | **成立**。计数器改由父进程按 attempted operations 累加，含失败、重试、二分；call ledger 新增按 endpoint 的 `transport` 段，并在成功/阻塞/失败的统一终止路径写一次，仍不进入数据集身份（§4、ADR-020 D6） |
