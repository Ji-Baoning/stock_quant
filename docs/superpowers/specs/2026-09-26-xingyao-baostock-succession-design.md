# 星耀数智接替 baostock：校验源、因子通道与备援候选 · 设计

- 日期：2026-09-26
- 状态：**owner 已复核（2026-09-26），按"新增独立车道 + 移除 baostock 准入"修订；
  决策层已落 `docs/adr/016-xingyao-baostock-succession.md`（已转 accepted，2026-09-26）。
  实施与 Phase 0 六项探针均已完成（2026-09-27），结论按回写清单落在本 spec
  §2.1/§2.4/§3.1/§3.3 与 ADR-016 decision 11；xingyao 以 `enabled: false` 出厂
  （批量通道为其启用前置，ADR-016）**
- 上游：[2026-09-12-data-source-role-division-design.md](2026-09-12-data-source-role-division-design.md)、
  [2026-09-25-raw-snapshot-reuse-design.md](2026-09-25-raw-snapshot-reuse-design.md)（ADR-015）
- 数据源质量评估：[2026-09-25-xingyao-data-quality-evaluation.md](../../research/2026-09-25-xingyao-data-quality-evaluation.md)
- 关系：本设计**接替 baostock 的全部运行时角色**（owner 指令 2026-09-26：baostock 服务
  不可用，默认禁用，星耀接替），同时保留星耀作为 daily_bar 备选主源候选的定位

## 0. 摘要

| 批次 | 内容 | 改动层次 | 效果 |
| --- | --- | --- | --- |
| 1 | 星耀适配器（daily）接替 baostock 校验车道 | 适配器 + pipeline 车道 + 登记 | 翻转只覆盖**非停牌形态**的主源缺席：Phase 0 实测停牌日 = 缺行，停牌日分类保持 `unknown_or_suspended`（§2.1）；配额口径已实测钉死，车道数据本体每轮 ≈0.8–1.8% 周配额（§2.4） |
| 2 | 星耀因子通道接替 baostock adjust_factor（ADR-009 懒通道），随后才休眠 baostock 全部车道 | 独立因子模块 + 懒通道接线 + 配置切换 | 缺席 ex-date 分类连续保有第二 price-event 通道，不出现过渡空窗 |
| 治理 | ADR-016 + REUSABLE_CHANNELS **替换** baostock 条目 + RUNBOOK | 配置 + ADR | 接替决策可追溯 |

批次 1/2 是开发与验收边界，**不是可分开上线的发布边界**。两批代码与
`baostock.enabled: false`、复用准入替换必须在同一发布中原子落地；在批次 2 通过前，
baostock 保持启用，以免现有 `_build_factor_channel` 因共享配置开关而提前消失。
（已按此原子落地：2026-09-27 发布同时切换 `baostock.enabled: false`、替换准入并转正
ADR-016 与 ADR-015 修订。**xingyao 本身以 `enabled: false` 出厂**——逐标的 worker
契约的会话固定开销使全宇宙校验单轮成本落在 ≈0.8%–167% 周配额区间（F 未定点值），
批量通道是 `enabled: true` 的前置条件（§3.1、ADR-016 decision 11）。）

另保留：星耀为 **daily_bar 备选主源候选**（relay 故障时按程序切换；本期不执行切换）。

## 1. 问题

baostock 数据服务不可用（owner 报告 2026-09-26；历史记录：2026-09-05 不可达、
2026-09-19 短暂恢复，见 [sources.yml](../../../project/configs/sources.yml) baostock 注释）。
其在本项目登记了三项运行时角色，全部需要接替或显式退役：

1. **校验车道**（`_fetch_validation_daily`）：每轮更新对 universe 权益符号按增量窗口拉
   不复权日线，validation 行的唯一消费是**缺失分类翻转**（§2.2）。
2. **ADR-009 因子通道**（`_LazyFactorChannel` → `factor_event_dates`）：仅当出现
   `incomplete` 且无 ex_date 的新隔离行时懒查询，为缺席除权日分类提供第二 price-event
   通道（§2.3）。
3. **sources.yml 注释宣称的停牌证据与 compare 参与**——经核查**两者均不成立**（§2.1、
   §2.2），随本次接替一并修正注释，避免继续误导。

星耀数智经 2026-09-25 全量质量评估（14 项检查：11 过、2 警告、1 失败；行情/财务/
收益率与独立来源逐日一致），具备接替资格；其 `get_backward_factor` 为日频全历史累计复权因子序列，
与 baostock `adjustFactor` 同语义且更完整。

## 2. 事实基础（2026-09-26 逐条核实，含对既有注释的两处证伪）

### 2.1 "唯一停牌证据"说法不成立

- `tradestatus` 在整个 `src/` 中仅出现 1 处：
  [baostock.py:44](../../../src/stock_quant/data_sources/baostock.py#L44) 的字段声明，
  适配器与 normalize 均不解释它，行按普通行情处理。
- 真实停牌证据全部来自 tushare 自身：`suspension_rows` 用主源 `pre_close` 链物化
  `source="tushare_suspend"` 行（[suspensions.py:71-215](../../../src/stock_quant/data_model/suspensions.py#L71-L215)）；
  `canonicalize_supplier_suspensions` 识别零 OHLC/零量行（[:229-326](../../../src/stock_quant/data_model/suspensions.py#L229-L326)）。
- baostock 行在本项目的**唯一可观察语义**是缺失分类翻转：主源缺行日若 validation 有行，
  分类从 `unknown_or_suspended` 翻为 `primary_source_missing`
  （[raw_checks.py:227-230](../../../src/stock_quant/data_quality/raw_checks.py#L227-L230)）。
  两类均 WARNING，均不在 `PUBLICATION_BLOCKING_CODES`（[gates.py:42-64](../../../src/stock_quant/data_quality/gates.py#L42-L64)）。
- baostock 完全下线的退化：缺失分类退为 `unknown_or_suspended`（WARNING，不阻塞）；
  `unexplained_primary_gap` **不会变多**（只由 tushare pre_close 链算出）。
- **Phase 0 实测（2026-09-27，[探针记录](../../operations/2026-09-26-xingyao-phase0-probes.md) §二）**：
  xingyao 的停牌日也是**缺行**（601238.SH 九个停牌日全部缺行；全市场旁证 44/26 个
  "标的-日"均为已发布侧的零量停牌行）。因此即使星耀启用，停牌日的缺失分类仍为
  `unknown_or_suspended`——主源（tushare 零量停牌行）与星耀（缺行）的形态差异落在
  存在性比对层；与 baostock（文档口径为 `tradestatus=0` 行）的等价性主张相应收窄
  （ADR-016 decision 11）。

### 2.2 validation 行只做存在性判定，从不进 compare

- `_fetch_validation_daily` 实际位于
  [data_pipeline.py:2535-2580](../../../src/stock_quant/data_pipeline.py#L2535-L2580)：
  符号集 = 本轮 universe 权益符号（非全市场），窗口 = 与主抓相同的**增量窗口**，
  params `{"adjustment":"unadjusted"}`，`required=False`，**`reuse=True`**（ADR-015）。
- validation 行不进发布表（`_merge_daily` 只合并 primary+benchmark，[:2746-2764](../../../src/stock_quant/data_pipeline.py#L2746-L2764)），
  唯一消费是 `validation_present = key in validation_dates` 布尔
  （[:2796-2803](../../../src/stock_quant/data_pipeline.py#L2796-L2803)，集合由 `_symbol_dates` [:2880-2885](../../../src/stock_quant/data_pipeline.py#L2880-L2885) 折叠）。
- **证伪**：`compare_daily_sources`（[compare.py:43-107](../../../src/stock_quant/data_quality/compare.py#L43-L107)）的唯一生产消费点在
  [external_inputs.py:451](../../../src/stock_quant/research/acceptance/external_inputs.py#L451)，
  输入是**已发布 daily_bar 的 price_sample**——baostock validation 行从不喂给它。
  sources.yml:21-22 "an outage degrades `compare_daily_sources`" 与代码不符，本次修正注释。
- daily 被跳过的轮次（`daily_skipped`）完全不抓 validation（[:1091-1102](../../../src/stock_quant/data_pipeline.py#L1091-L1102)）。

### 2.3 因子通道属 ADR-009，不在 ADR-013 仲裁链内

- ADR-013 链（`_build_action_arbiter`，[:2377-2400](../../../src/stock_quant/data_pipeline.py#L2377-L2400)）按序问
  TDX → price_observed（tushare pre_close）；**baostock 不在其中**。
- baostock 因子通道是 **ADR-009**（缺席 ex-date 分类）的第二 price-event 通道
  （[baostock_factor.py:1-15](../../../src/stock_quant/data_sources/baostock_factor.py#L1-L15)）：
  `_LazyFactorChannel`（类定义 [:3670-3729](../../../src/stock_quant/data_pipeline.py#L3670-L3729)，其中 `__call__` :3698-3729；构造 [:2412](../../../src/stock_quant/data_pipeline.py#L2412)）
  进程内缓存、逐符号懒查询、失败降级 = `CODE_OPTIONAL_SOURCE_FAILURE` WARNING +
  记入 `_failed` + 返回 `None`（缺席通道 asserts nothing，fail-closed）。
- 消费点两处：`_record_absent_ex_date_classifications`（[:3505-3580](../../../src/stock_quant/data_pipeline.py#L3505-L3580)，
  `channels=(("tdx",…),("baostock",…))`）与 `_demoted_reasons_by_symbol`（ADR-012，[:3086-3133](../../../src/stock_quant/data_pipeline.py#L3086-L3133)）。
- `factor_event_dates`（[baostock_factor.py:106-124](../../../src/stock_quant/data_sources/baostock_factor.py#L106-L124)）
  只返回**日期**（相邻 adjustFactor 变化），丢弃幅度；快照以
  `source="baostock", endpoint="adjust_factor"` 落 raw（[:39-41, 127-148](../../../src/stock_quant/data_sources/baostock_factor.py#L127-L148)）。

### 2.4 星耀侧能力（2026-09-25 实测，评估报告为准）

- 日K：`query_kline` 不复权，日期在 `kline_time` 列；沪深北覆盖（评估时 7 标的抽样 +
  全量代码表佐证），分钟/快照通道独立存在。**volume/amount 的单位已实测（Phase 0
  探针 6）：股/元，`_UNIT_FACTORS["xingyao"] = (1, 1)` 由假设转为实测结论**——4 标的
  逐日 volume 整数完全相等、amount 在显示精度下相等（逐日 ≤±0.07 元，浮点/舍入量级），
  tushare relay 交叉核对逐字段相等（[探针记录](../../operations/2026-09-26-xingyao-phase0-probes.md) §七）。
  §4.5 的单位门闭合。
- 复权因子：`get_backward_factor([symbol], is_local=False)` 返回以交易日为索引、symbol
  为列的**宽表**；实测 8733 行是该返回帧按完整交易日历对齐后的行数，因而既是日历长度，
  也是返回帧行数，不能直接理解为该证券有 8733 个有效因子观测。上市前/退市后的单元格
  可能为空；事件提取必须先选择目标 symbol 列、清除空值并排序，再比较相邻有效因子。
- 已知口径坑位（适配器必须吸收）：沪深代码表封装层 -76 故障（枚举走 tgw 原生
  `QueryCodeTable`，本期适配器不需要）；K线日期在列不在索引。
- 成本标定（**Phase 0 探针 5 已定口径**）：计数器在登录 logon json（`UsedWeekFlow`/
  `TotalWeekFlow`，`TotalWeekFlow=1e9` 实测），**1 计数单位 = 1GB 线上流量**，不是存储
  字节数——评估报告里 23% 与 0.04% 的约 500 倍差即源于此（0.23 是线上流量计数，0.04%
  是存储字节估算）。系数：会话固定开销 F < 0.0025 单位/会话【实测界，真值在计数器粒度下
  不可分辨】；日 K 行线上成本 ≈170–390 B/行【推断界，不可跨端点套用——评估报告的
  81 B/行属代码表端点】。校验车道数据本体每轮 ≈0.008–0.018 单位 ≈ 周配额 0.8–1.8%；
  **做配额预算一律用计数器读数，不用存储字节估算**（[探针记录](../../operations/2026-09-26-xingyao-phase0-probes.md) §六）。
- 原"待实测（Phase 0）"两项均已有实测结论（2026-09-27，六项探针全部完成、无 BLOCKED）：
  停牌日行形态 = **缺行**（停牌日分类保持 `unknown_or_suspended`，§2.1）；成交量单位 =
  **(1, 1)**（见上）。结果与证据见[探针记录](../../operations/2026-09-26-xingyao-phase0-probes.md)。

### 2.5 复用与登记的既有接线（ADR-015 落地状态）

- `_dispatch` 已带 `reuse`/`allow_empty` 关键字（[data_pipeline.py:2608](../../../src/stock_quant/data_pipeline.py#L2608)）；
  `RawStore.resolve_reusable` 以 `REUSABLE_CHANNELS` 为第一道门
  （[raw_store.py:35-41, 205](../../../src/stock_quant/data_sources/raw_store.py#L35-L41)）。
- 现准入：`("tushare","daily")`、`("baostock","daily")`、`("akshare","index_history")`；
  本设计**以 `("xingyao","daily")` 替换 baostock 条目**（§3.1、§5）。
- ADR-015 正文两处 baostock 字面需连带修订：Context 段的 "659 symbols" 论据句、
  Decision 第 1 条的通道枚举（"exactly the four `_dispatch` lanes"）。
- 登记点：`_CONFIGURED_SOURCES`/`_REQUIRED_ROLE`（[data_pipeline.py:261-262](../../../src/stock_quant/data_pipeline.py#L261-L262)）、
  `_build_source`（[:2843-2856](../../../src/stock_quant/data_pipeline.py#L2843-L2856)，须加 `if name == "xingyao"` 分支）、
  `_UNIT_FACTORS`（[normalize.py:38-44](../../../src/stock_quant/data_model/normalize.py#L38-L44)）、
  `KNOWN_SUPPLIERS`（[raw_checks.py:61](../../../src/stock_quant/data_quality/raw_checks.py#L61)）。

## 3. 设计

### 3.1 批次 1：校验车道接替（每轮更新路径）

**新增 `src/stock_quant/data_sources/xingyao.py`**（baostock 模式）：

- `name = "xingyao"`；构造函数内惰性 `import tgw, AmazingData`（包缺失 → 与 baostock
  相同的源不可用语义：optional 路径 WARNING，不阻塞）；凭据只读环境变量
  `AD_USERNAME/AD_PASSWORD/AD_HOST/AD_PORT`（延续 `.env`，凭据不入库不变量）。
- endpoint 仅 `daily`：`query_kline` 不复权、per-symbol 窗口请求、日期取 `kline_time`
  列、params `{"adjustment":"unadjusted"}`；复用 `validate_supplier_frame` 与
  `fetch_with_retry`。
- **硬超时边界**：`fetch_with_retry` 的 `default_request_timeout` 只覆盖 `requests`，不能
  约束 tgw 的 broker TCP/回调等待；不得把线程或错误文本映射冒充调用超时。所有登录、
  `query_kline` 与 `get_backward_factor` 实时调用必须运行在可终止的子进程边界内，父进程
  最多等待 `timeout_seconds`，到期终止并回收子进程、抛 `ServerError`，再由
  `fetch_with_retry` 决定是否重试。子进程异常只回传脱敏的类型/消息，不回传环境变量。
- 错误映射：tgw -76 / 上述父进程超时 → `ServerError`（瞬时，参与重试）；登录失败 →
  `AuthenticationError`（不重试）；`translate_supplier_error` 现有关键词不覆盖的
  tgw 错误文本在适配器内先行翻译。
- `transport_id = "xingyao-broker-tcp"`（对齐 base.py 的 transport 防塌缩要求）。

**车道接线（`data_pipeline.py`）**：

- **新增独立 xingyao 校验车道**（owner 2026-09-26 定调："新增独立车道 + 移除 baostock
  准入"）：新车道 `required=False, reuse=True`，与 baostock 车道语义逐项一致
  （同窗口、同符号集、同存在性消费）。
- baostock 校验车道在**批次 1/2 同一发布的最后一步**休眠：`sources.yml` 里
  `enabled: false` 使其永不执行；其调用点保留但改为 `reuse=False`——休眠车道不得占用
  准入额度（§5）。不得先发布这个配置变更，否则同一个开关会让尚未被星耀接替的
  ADR-009 因子通道提前消失。代价：恢复 baostock 车道仍是改配置，但其复用语义需重新
  准入，不会自动随配置回归。
- `_CONFIGURED_SOURCES` 追加 `"xingyao"`，`_REQUIRED_ROLE["xingyao"] = False`——**漏写
  `_REQUIRED_ROLE` 会在 `_REQUIRED_ROLE[name]` 处直接 KeyError**；`baostock` 保留在注册表。
- `_build_source` 加 `xingyao` 分支，否则 `raise ValueError`（[:2843-2856](../../../src/stock_quant/data_pipeline.py#L2843-L2856)）。
- **`project/drift_audit.py` 必须按 `(source, endpoint)` 扩展**，不能只给 `_source_for`
  增加 xingyao 前缀：`("xingyao", "daily")` 用日线适配器重取；
  `("xingyao", "backward_factor")` 用 `xingyao_factor.py` 的专用重取入口，并按原
  `DataRequest` 重建同形状快照。未知 endpoint、快照不可验证和实时重取失败均计入
  `audit_failures`，使进程退出非零；`drifted` 与 `audit_failures` 在报告中分列，避免
  “0 drifted”掩盖“0 successfully audited”。当前仅按 source 映射且吞成 `fetch_failed`
  的路径见 [drift_audit.py:81-145](../../../project/drift_audit.py#L81-L145)。

- **逐标的接线的成本界限（Phase 0 探针 5 回写）**：`XingyaoSource` 契约是逐标的分发——
  每次 fetch = 一个新 worker = 一次 login + 一次完整交易日历。用实测界重算一轮全宇宙
  （661 标的）校验：会话开销 661 × F（F < 0.0025 单位/会话【实测界】）→ [0, 1.65) 单位；
  数据本体仅 ≈0.008–0.018 单位；**单轮合计 ≈0.8%–167% 周配额，最坏情形不可行**，且
  661 次登录握手本身即每轮 ≈45–60 分钟的纯等待（界定探针 5 会话实测 27.5 s 含 worker
  启动，外推）。定性结论与 F 无关：逐标的接线把同一次会话固定开销花 661 遍，批量通道
  只花 1–2 遍（SDK `query_kline` 原生接受代码列表，Phase 0 即以 1000 只/批抓取）。
  **因此星耀以 `enabled: false` 出厂；批量专用通道（或缩小每轮标的集、降低轮频）是
  扶正星耀或全宇宙跑校验车道前的先决条件**（ADR-016 decision 11；系数与区间见
  [探针记录](../../operations/2026-09-26-xingyao-phase0-probes.md) §六）。

**登记（每处几行）**：

- `normalize.py`：`_UNIT_FACTORS["xingyao"] = (1.0, 1.0)`。
- `raw_checks.py`：`KNOWN_SUPPLIERS` 追加 `"xingyao"`。
- `raw_store.py`：`REUSABLE_CHANNELS` **以 `("xingyao", "daily")` 替换 `("baostock", "daily")`**
  （allow_empty=False；空帧不可复用）。**替换而非并存**，是为了保住 ADR-015 的准入
  不变量"准入集恰好等于 `reuse=True` 调用点集"——休眠车道不占额度，无需放宽断言。
  **本 spec 同时推翻上一稿"星耀通道永不复用"的立场**：校验车道是每轮常驻车道，接替即
  继承其全部语义；备援切换场景下 request_key 精确匹配 + 星耀 raw 树隔离 + 哈希重验 +
  篡改回实时（ADR-015 §2.2 第 4 条）保证复用不产生陈旧误判。
- `project/configs/sources.yml`：`xingyao: enabled: true, timeout_seconds: 30,
  max_retries: 2` + 注释块（角色、口径坑位、81 字节/行标定、启用/禁用语义）；
  `baostock: enabled: false` + 注释（owner 报告 2026-09-26 服务不可用；禁用期间缺失
  分类退为 `unknown_or_suspended`——WARNING 不阻塞；恢复车道 = 改回 true，但复用语义
  需重新准入）。**顺带修正两处失实注释**：:21-22 的 `compare_daily_sources` 参与、
  :24-25 的 "supplier's own suspension rows (`tradestatus=0`) … the only suspension
  evidence this project has"——后者已被 §2.1 证伪，§1 承诺的是两处而非一处。
- `requirements.txt` / `environment.yml`：注释段说明私有包安装（PyPI 无包）。**只写包名
  与前置条件，不写路径**——`dist_wheels/` 在本仓库**不存在**，真实 wheel 位于供应商的
  `tools/xysz/xysz/xysz_tools/`，且已由提交 `10be36885` 明确排除出仓库，wheel 由操作者
  自备。前置 `tables`(PyTables)：缺失时复权因子等接口直接 ImportError。不进默认依赖。

批量通道已落地，见 ADR-020。

### 3.2 批次 2：因子通道接替（ADR-009 邻域，独立开发验收、与批次 1 原子发布）

**新增 `src/stock_quant/data_sources/xingyao_factor.py`**：

- `fetch_factor_event_dates(symbol) -> list[date]`：调用
  `get_backward_factor([symbol], is_local=False)`（每符号一次、进程内缓存），要求返回
  DataFrame 且精确包含目标 symbol 列；索引必须可解析为日期。选择该列后转为有限数值，
  丢弃空值，按日期稳定升序；重复日期若值冲突则 `ContractError`，相同则折叠。第一个
  **有效**值只作基线，之后每次精确数值变化的日期才是事件——与现有
  `factor_event_dates` 的 fail-closed 语义相同。空帧、无目标列、无有效值均返回缺席通道，
  不得断言“无事件”。
- 快照以 `source="xingyao", endpoint="backward_factor"` 落 raw（对齐
  baostock_factor.py 的 snapshot_result 模式），保存供应商返回的**单 symbol 原生宽表及
  日期索引**，不把派生事件列表冒充原始响应。快照 metadata 必须带可重建的
  `request_parameters`，供漂移审计按同一请求重取。
- 模块同时提供最小 `XingyaoFactorSource`（`name = "xingyao"`，实现
  `fetch(DataRequest) -> FetchResult`，且只接受 `endpoint="backward_factor"` 与单一
  symbol）。它只把 `fetch_factor_frame`/`snapshot_result` 包成现有 `DataSource` 契约，
  专供 `drift_audit` 按原请求重取同形状快照；不进入 `_CONFIGURED_SOURCES`，也不取代
  daily 的 `XingyaoSource`。
- **幅度同样不丢弃不使用**：本批次只接替"日期"语义；相邻比值作为价格因子属
  ADR-013 域的潜在增强，超出本 spec 范围（见 §6）。

**懒通道接线（`data_pipeline.py`）**：

- `_LazyFactorChannel` 的实现切到 xingyao 因子模块；baostock 实现同 §3.1 日频车道口径
  **保留代码但不再被调用**（休眠而非删除，理由同 §6）；失败降级语义逐项照抄
  （WARNING + `_failed` + `None`）。
- 分类标签：新记录 `channels=(("tdx",…),("xingyao",…))`；**历史已发布记录中的
  `"baostock"` 标签保留原样**（不可变发布，不改写历史）。
- 发布顺序：只有本节单元/集成测试通过且 xingyao 因子通道已接线后，才在同一提交/发布中
  把 `baostock.enabled` 改为 false 并替换 ADR-015 准入；仓库不得存在“baostock 已禁用、
  xingyao factor 尚未接线”的可发布中间状态。

### 3.3 备选主源候选（本 spec 只声明，不接线）

- 星耀保持 daily_bar **备援候选**定位：relay 故障时的切换 =
  启用星耀 → 增量窗口补抓验证 → `data_contracts.daily_bar.primary_transport` 改为
  `xingyao:<kind>`（届时随切换 ADR 向 `TRANSPORT_KINDS` 新增 `broker` kind，
  data_contracts.py:33）→ 双变量保险丝仿 `TUSHARE_ALLOW_OFFICIAL_PUBLISH` 模式。
- **本期不做**：不改 `data_contracts.py`、不建运行时自动 failover、不做代码表 endpoint
  （备援切换需要枚举能力时再加，走 tgw 原生 `QueryCodeTable`）。
- 扶正为常驻主源的附加条件（ADR-016 记录）：退市股历史覆盖、历史深度、
  连续 N 周校验车道无 ERROR 级 `close_difference`。**Phase 0 实测（探针 4）落界**：
  深度起点 **2013-01-04**（2013 前该账号/权限档无数据；是否产品级限制无法从本侧判别，
  已如实记录）；`full_history_acceptance_start` = 2015-01-05 的验收锚可满足（深度比锚点
  多约 2 年）；2013 后退市股实测覆盖至退市（601558.SH、300372.SZ），2013 前退市
  （如 000003.SZ）无覆盖——由深度边界完全解释。前两项为**带边界的满足**，深度约束随
  ADR-016 decision 11 记录。

## 4. Phase 0 前置实测（实施时第一批，结果决定细节口径）

> **已完成（2026-09-27 实测，六项全部完成、无 BLOCKED）。** 结果、系数与 sha256 证据见
> [Phase 0 实测记录](../../operations/2026-09-26-xingyao-phase0-probes.md)；
> 判定分流已回写本 spec（§2.1/§2.4/§3.1/§3.3）与 ADR-016（decision 11）。以下为
> 实施时的探针定义，留档不改。

1. **星耀停牌日行形态**：找近期停牌股（`get_history_stock_status` 检索 + 日K对照），
   确认停牌日是**零量行**还是**缺行**。零量行 → validation 行照常提供存在性翻转
   （与 baostock 等价）；缺行 → 停牌日分类保持 `unknown_or_suspended`（弱于 baostock，
   WARNING 级可接受，差异写入 ADR-016）。同时确认零量行能否通过 `normalize_daily`
   （若被行级拒绝规则拒绝，需在 spec 修订中决定是否透传）。
2. **全市场窗口比对**：`tgw.QueryCodeTable()` 枚举 A 股，近 60 交易日 + 抽样历史窗口，
   按现行发布数据集以 `compare_daily_sources` 阈值（收盘差 >0.20% = ERROR）比对，
   产出 sha256 证据文件（备援资格证据）。
3. **退市股历史覆盖**（如 000003.SZ）与**历史深度**（对照 `full_history_acceptance_start`
   抽 2005/2015 窗口）——扶正条件的证据基线。
4. 流量预算：本轮全部实测 ≤30% 周配额；报告写入 `docs/operations/`。**配额口径本身要先
   定**：评估报告 [§一:23](../../research/2026-09-25-xingyao-data-quality-evaluation.md#L23)
   记"累计消耗约 0.23 单位（≈23%）"，[:107](../../research/2026-09-25-xingyao-data-quality-evaluation.md#L107)
   记"本次全量探针消耗约 0.04%"，相差约 500 倍；本设计 §0 的"每轮增量 ≈1-2MB"与本节
   "≤30%"分别取自两端。Phase 0 须以一次记录起止计数的可复现实测定准，否则"周 <1%"这句话
   没有依据。
5. **成交量/成交额单位的外部校验**：评估报告只交叉校验收盘价、财务、收益率，**从未校验
   volume/amount 单位**。(1.0, 1.0) 是假设而非结论。取 ≥3 只标的的区间累计成交量与独立
   来源比对，确认是股还是手，据此定 `_UNIT_FACTORS["xingyao"]`。（批次 1 校验行不进发布
   表，故不阻塞批次 1；但 §3.3 扶正为主源前必须闭合。）
6. **硬超时探针**：用不会回调的假 tgw 调用验证父进程在 `timeout_seconds` 后终止并回收
   worker，返回可重试的 `ServerError`；再用一次真实小窗口调用验证正常响应不会遗留子进程。
   此项不通过不得启用 xingyao 默认车道。

## 5. 与 ADR-015 的关系

- **准入表修订**：`REUSABLE_CHANNELS` **以 `("xingyao","daily")` 替换 `("baostock","daily")`**。
  条目数仍为 3，因此 ADR-015 Decision 第 1 条的准入不变量（"准入集恰好等于 `reuse=True`
  调用点集"）继续成立、断言无需放宽——这正是选"替换"而非"并存"的理由。baostock 休眠
  车道若恢复，其复用语义须重新准入（记入 ADR-016），不随配置开关自动回归。
- **ADR-015 修订方式：先加 pending 指针，实施后生效**（不静默重写历史）。ADR-016 仍为
  `proposed` 或代码尚未原子落地时，ADR-015 顶部只能写 **Pending amendment**，并明确
  当前有效准入仍是 baostock；实施完成且 ADR-016 转 `accepted` 后，再把它改为
  **Amendment (ADR-016)**，说明 Decision 1 的通道枚举以 xingyao 替换 baostock（车道
  总数不变）；Context 段 [:25-26](../../adr/015-raw-snapshot-reuse-for-eligible-channels.md#L25-L26)
  的成本论据 "the baostock bounded retries alone cost hours at 659 symbols" 应改读为
  tushare daily 车道（659 只的成本形状不变）。ADR-015 的 `status` 保持 `accepted`——
  结论未变，只是通道参数被替代，故**修订而非取代**。
- **可见性继承**：xingyao 校验车道走 `_dispatch(reuse=True)`，自动获得账本 `reused`
  段与 `build_config.raw_snapshot_reuse` 计数（增量窗口通常实时、同日重跑命中复用），
  无需新增可观测机制。
- **验收复验约束**：xingyao raw 快照同受 ADR-015 §2.7 三步删除程序约束；RUNBOOK
  星耀条目引用该程序，不重复。
- **防回归断言**：`tests/unit/test_raw_reuse.py` 的两条断言（`_ADMITTED` 声明表、
  "准入集恰好等于 `reuse=True` 调用点集"）随替换同步更新——`("baostock","daily")` 翻为
  不可复用，`("xingyao","daily")` 转为可复用且与在册调用点一致。**不变量本身不变**，
  这正是替换方案的收益。

## 6. 边界（明确不做）

- 不建任何新发布表；validation 行维持"存在性 only"语义，不进 compare、不进发布表。
- ADR-013 仲裁链不动（TDX + price_observed 维持）；星耀因子**幅度**不接入任何裁定。
- 不做运行时自动 failover；不做 `data_contracts.py` 的 broker kind；不做代码表/日历 endpoint。
- 分钟K/30秒快照/财务三表/两融/国债收益率等新数据类型 → 数据类型扩展专项（二期），
  届时按同一验收治理。
- 不删除 baostock 适配器与 baostock_factor.py，但**不得再称它为"可再启用车道"**：替换后
  其日频车道休眠、批次 2 后因子通道亦由星耀接替，存活角色只剩两项——历史快照的证据再
  验证（`drift_audit`、验收复验）与一条需要动代码/配置的再启用路径。本 spec 不把
  "服务恢复即自动回归"写成承诺。
- 不动公司行动/成分/停牌证据链（tushare pre_close 链与 CSI 官方快照）。

## 7. 测试

**新增**：

- **`tests/unit/test_xingyao_source.py`**：离线假体测帧翻译（`kline_time` 日期列、单位因子
  取自 §4.5 结论）、错误映射（-76→ServerError 可重试、登录失败→AuthenticationError
  不重试）、惰性 import 缺失 → optional 降级语义、transport_id 固定值；另用永不返回的
  假调用证明子进程硬超时、终止与回收。
- **`tests/unit/test_xingyao_factor.py`**：宽表目标列选择、日期索引排序、前后 NaN、内部 NaN、
  重复日期相同值折叠/冲突拒绝、首个有效值、无变化、单行序列；快照保留原生宽表索引并带
  可重建 request metadata。
- **`tests/unit/test_drift_audit.py`**：按 endpoint 分派 xingyao daily/backward_factor；未知
  endpoint、不可验证快照和 fetch failure 均增加 `audit_failures` 并使退出码非零。
- **`tests/fixtures/xingyao_daily.csv`** + `tests/integration/test_source_contracts.py`
  离线契约，对齐既有源 fixture 模式（假客户端 + 原生列断言，参照 `baostock_daily.csv`
  与 `test_*_returns_recorded_native_columns`）。此文件是纯增量，不破坏既有断言。
- **`tests/external/`**：实时契约（仅 `pytest -m external`，需 `AD_*` 凭据）。

### 7.1 既有断言：夹具与生产一致地停跑 baostock 车道

`tests/integration/conftest.py::_fixture_sources_yaml` 原先把模板中的**每个 supplier** 强制
`enabled = true`，理由是夹具要覆盖所有可选源、不继承生产模板的运维开关。**决定 A
（2026-09-26）取消这一豁免**：baostock 的 supplier 不可用、两个真实角色均已在同一发布中
由 xingyao 承担（decision 2/6），继续默认启用它会让每个普通夹具测试都执行一条项目不再
运行的车道，并在调用账本里留下一条生产不会产生的 baostock 行——夹具要复现的是运行形态，
不是一段历史。因此夹具显式把 `baostock.enabled` 置为 `False`，与生产一致。

**休眠车道不删。** 需要覆盖它的测试自行 `write_sources(baostock=True)` 显式打开。代价照实
记下：休眠调用点——包括其 `reuse=False` 接线——不再被默认测试路径覆盖，改由一条专门测试
盯住（`test_the_dormant_baostock_lane_still_works_when_a_test_asks_for_it`，它同时断言该源的
`reused == 0`，把 decision 4 的准入排除钉在行为上）。这是显式的覆盖，不是默认的覆盖。

需要改动的既有形态：

- **四个位置的 helper**：三个测试文件中的 `_all_stubs()` / `_sources()` 加 xingyao 桩，否则
  管线会尝试构造私有真适配器，使 xingyao status 为 unavailable；`write_sources` 增加
  `xingyao` 形参并把 `baostock` 默认值改为 `False`——它原先只写 tushare/akshare/baostock
  三段、**没有 xingyao**，任何调用它的测试都会静默关掉星耀车道。补桩后 `all(status.ok)`
  与 `reason_code == "ok"` 两处断言原文不动。
- `test_update_writes_call_ledger` 的精确字典新增 xingyao 并**删去 baostock**：账本只登记
  真正 dispatch 过的源。
- `test_a_retry_round_reuses_the_stored_prefix` 的 `raw_snapshot_reuse` 精确字典新增 xingyao、
  删去 baostock；`ledger["baostock"]["reused"] == {}` 会 KeyError，改为对 xingyao 断言同一
  事实（该源 dispatch 了但没有可复用的快照）。
- `test_optional_validation_failure_still_publishes` 的**行为契约保留、制造者换源**：它验证
  “可选源失败仍不阻塞发布”，原先靠失败的 baostock 桩制造失败；现在 baostock 不跑，改注入
  失败的 xingyao 桩——它才是当前持有校验车道的源。
- `tests/unit/test_raw_reuse.py` 的 `_ADMITTED` 与“准入集恰好等于调用点集”两条断言同步
  替换：baostock 为不可复用、xingyao 为可复用，不变量不变。
- `test_disabled_baostock_is_never_constructed_or_fetched` **不动**：它自己
  `write_sources(baostock=False)`，与新默认值一致，请求只点名 baostock 使启用集收窄为空。

`templates/project-config/sources.yml` 须增加 xingyao 段，并把 baostock 段改为
`enabled: false`——模板是新项目的脚手架默认值，带着一个已停用的 supplier 出厂只会让每个
新项目重演这次清理。夹具不依赖模板的开关语义（它显式设定该值），两处都要有。

### 7.2 新增集成测试

- validation 车道接替：stub xingyao 源 → `validation_present` 翻转恢复
  （`primary_source_missing` 分类回归）；星耀停牌日形态按 §4.1 结论分别断言。
- 因子懒通道失败降级（WARNING + 缺席通道）。
- 账本与 `raw_snapshot_reuse` 形状：夹具默认只有 xingyao 一行（baostock 未 dispatch），
  xingyao 记录可复用车道计数与实时 `fetched`。**注意**：账本的
  `calls`/`endpoints` 恒为 0（`src/` 内没有任何适配器定义 `calls`，`render_call_ledger`
  走 `getattr(source, "calls", 0)` 兜底），因此只能断言**行的存在/缺席**与 `reused` 段，
  不能断言调用数——初稿"账本形状含 xingyao calls"的说法本身就是错的。
- 休眠车道的显式覆盖（§7.1）：`write_sources(baostock=True)` 打开后该源 dispatch、账本出现
  baostock 行且 `reused == 0`。这条测试是 `reuse=False` 接线的唯一守卫。

## 8. 验证命令

```bash
pytest tests/unit/test_xingyao_source.py tests/unit/test_xingyao_factor.py tests/unit/test_drift_audit.py -q
pytest tests/integration/test_source_contracts.py tests/integration/test_data_pipeline.py -q
pytest tests/unit/test_raw_reuse.py tests/unit/test_context_governance_docs.py -q
pytest tests/integration/test_pipeline_fetch_coverage.py tests/integration/test_raw_snapshot_reuse.py -q
pytest -m external        # 需网络 + AD_* 凭据
# ADR-016 落笔后
python tools/check_context_governance.py --root .
```

## 9. 文件清单

**新增**：`src/stock_quant/data_sources/xingyao.py`、
`src/stock_quant/data_sources/xingyao_factor.py`（批次 2）、
`tests/unit/test_xingyao_source.py`、`tests/unit/test_xingyao_factor.py`、
`tests/fixtures/xingyao_daily.csv`、`tests/external/test_xingyao_live.py`、
`docs/adr/016-xingyao-baostock-succession.md`。

**改动**：`src/stock_quant/data_pipeline.py`（注册表 + `_build_source` + 新校验车道 +
懒通道）、`project/drift_audit.py`（按 source+endpoint 分派并让未完成审计退出非零）、
`src/stock_quant/data_model/normalize.py`、`src/stock_quant/data_quality/raw_checks.py`、
`src/stock_quant/data_sources/raw_store.py`（准入常量**替换**）、
`project/configs/sources.yml` 与 `templates/project-config/sources.yml`（后者必须加
xingyao 条目、并把 baostock 段改为 `enabled: false`）、`requirements.txt`、
`environment.yml`、`RUNBOOK.md`（私有包安装、external 说明、baostock 禁用/恢复程序）、
`docs/adr/DECISIONS_INDEX.md`、ADR-015 顶部的 Amendment 指针（+ ADR-016 本体），
以及 `tests/unit/test_drift_audit.py`、§7.1 列出的精确字典、准入断言与测试 helper
（`_all_stubs`/`_sources`/`write_sources`/`_fixture_sources_yaml`）。

**不碰**：`cli.py`、`price_observed.py`、ADR-013 链、公司行动/成分/停牌证据链、
`data_contracts.py`、已发布数据与既有测试语义（§7.1 所列精确形状除外）。
