# 星耀数智接替 baostock：校验源、因子通道与备援候选 · 设计

- 日期：2026-09-26
- 状态：**owner 已复核（2026-09-26），按"新增独立车道 + 移除 baostock 准入"修订；
  决策层已落 `docs/adr/016-xingyao-baostock-succession.md`（status: proposed，实施完成后转 accepted）**
- 上游：[2026-09-12-data-source-role-division-design.md](2026-09-12-data-source-role-division-design.md)、
  [2026-09-25-raw-snapshot-reuse-design.md](2026-09-25-raw-snapshot-reuse-design.md)（ADR-015）
- 数据源质量评估：[2026-09-25-xingyao-data-quality-evaluation.md](../../research/2026-09-25-xingyao-data-quality-evaluation.md)
- 关系：本设计**接替 baostock 的全部运行时角色**（owner 指令 2026-09-26：baostock 服务
  不可用，默认禁用，星耀接替），同时保留星耀作为 daily_bar 备选主源候选的定位

## 0. 摘要

| 批次 | 内容 | 改动层次 | 效果 |
| --- | --- | --- | --- |
| 1 | 星耀适配器（daily）接替 baostock 校验车道；baostock 车道休眠 | 适配器 + pipeline 车道 + 登记 | `primary_source_missing` 翻转能力**有条件**恢复（取决于 Phase 0 停牌日行形态，§4.1）；每轮增量窗口流量 ≈1-2MB（配额口径待 §4.4 校准） |
| 2 | 星耀因子通道接替 baostock adjust_factor（ADR-009 懒通道） | 独立因子模块 + 懒通道接线 | 缺席 ex-date 分类恢复第二 price-event 通道 |
| 治理 | ADR-016 + REUSABLE_CHANNELS **替换** baostock 条目 + RUNBOOK | 配置 + ADR | 接替决策可追溯 |

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

星耀数智经 2026-09-25 全量质量评估（13 项检查 11 过，行情/财务/收益率与独立来源
逐日一致），具备接替资格；其 `get_backward_factor` 为日频全历史累计复权因子序列，
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
  全量代码表佐证），分钟/快照通道独立存在。**volume/amount 的单位（股 vs 手）评估报告
  从未对外部源校验**——(1.0, 1.0) 是待测假设，列 §4.5。
- 复权因子：`get_backward_factor` 返回**每符号全历史日频**累计后复权因子 DataFrame，
  相邻行变化即除权事件——`factor_event_dates` 的天然等价物。（注意：不要引用"8733 行
  实测"，评估报告里的 8733 是**交易日历**天数，不是因子序列行数。）
- 已知口径坑位（适配器必须吸收）：沪深代码表封装层 -76 故障（枚举走 tgw 原生
  `QueryCodeTable`，本期适配器不需要）；K线日期在列不在索引。
- 成本标定：约 81 字节/行（评估报告 §一）；校验车道每轮增量窗口 ≈1-2 万行 ≈1-2MB。
  **"周配额 1GB、占比 <1%"这一整句建在未定的配额基数上**——评估报告里 23% 与 0.04%
  两个数字相差约 500 倍（§4.4），故本节不给结论数，只保留"每轮 1-2MB"这个由帧长直接
  算出的量。
- 待实测（Phase 0）：**星耀停牌日的行形态**（零量行或缺行）——决定 validation 行在停牌日
  的翻转能力（§4.1）；以及**成交量单位**（§4.5）。

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
- 错误映射：tgw -76 / 超时 → `ServerError`（瞬时，参与重试）；登录失败 →
  `AuthenticationError`（不重试）；`translate_supplier_error` 现有关键词不覆盖的
  tgw 错误文本在适配器内先行翻译。
- `transport_id = "xingyao-broker-tcp"`（对齐 base.py 的 transport 防塌缩要求）。

**车道接线（`data_pipeline.py`）**：

- **新增独立 xingyao 校验车道**（owner 2026-09-26 定调："新增独立车道 + 移除 baostock
  准入"）：新车道 `required=False, reuse=True`，与 baostock 车道语义逐项一致
  （同窗口、同符号集、同存在性消费）。
- baostock 校验车道**休眠**：`sources.yml` 里 `enabled: false` 使其永不执行；其调用点
  保留但改为 `reuse=False`——休眠车道不得占用准入额度（§5）。代价：恢复 baostock 车道
  仍是改配置，但其复用语义需重新准入，不会自动随配置回归。
- `_CONFIGURED_SOURCES` 追加 `"xingyao"`，`_REQUIRED_ROLE["xingyao"] = False`——**漏写
  `_REQUIRED_ROLE` 会在 `_REQUIRED_ROLE[name]` 处直接 KeyError**；`baostock` 保留在注册表。
- `_build_source` 加 `xingyao` 分支，否则 `raise ValueError`（[:2843-2856](../../../src/stock_quant/data_pipeline.py#L2843-L2856)）。
- **`project/drift_audit.py` 必须同步扩展**：`_source_for`（[:81-88](../../../project/drift_audit.py#L81-L88)）
  只认 tushare/akshare/baostock，新前缀抛 `ValueError`，而该异常被 [:130-145](../../../project/drift_audit.py#L130-L145)
  的 `except Exception` 吞成 `"fetch_failed"`——不计入漂移计数、不影响退出码。不改则
  D5.4 这个补偿控制对星耀通道**静默为空**：看起来通过了，实际什么都没审。

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

### 3.2 批次 2：因子通道接替（ADR-009 邻域，独立成批）

**新增 `src/stock_quant/data_sources/xingyao_factor.py`**：

- `fetch_factor_event_dates(symbol) -> list[date]`：`get_backward_factor` 全序列拉取
  （每符号一次、进程内缓存），相邻行因子变化 → 事件日期列表——与
  `factor_event_dates` 同返回形状；自带登录（复用 xingyao.py 的登录工具）。
- 快照以 `source="xingyao", endpoint="backward_factor"` 落 raw（对齐
  baostock_factor.py 的 snapshot_result 模式）。
- **幅度同样不丢弃不使用**：本批次只接替"日期"语义；相邻比值作为价格因子属
  ADR-013 域的潜在增强，超出本 spec 范围（见 §6）。

**懒通道接线（`data_pipeline.py`）**：

- `_LazyFactorChannel` 的实现切到 xingyao 因子模块；baostock 实现同 §3.1 日频车道口径
  **保留代码但不再被调用**（休眠而非删除，理由同 §6）；失败降级语义逐项照抄
  （WARNING + `_failed` + `None`）。
- 分类标签：新记录 `channels=(("tdx",…),("xingyao",…))`；**历史已发布记录中的
  `"baostock"` 标签保留原样**（不可变发布，不改写历史）。

### 3.3 备选主源候选（本 spec 只声明，不接线）

- 星耀保持 daily_bar **备援候选**定位：relay 故障时的切换 =
  启用星耀 → 增量窗口补抓验证 → `data_contracts.daily_bar.primary_transport` 改为
  `xingyao:<kind>`（届时随切换 ADR 向 `TRANSPORT_KINDS` 新增 `broker` kind，
  data_contracts.py:33）→ 双变量保险丝仿 `TUSHARE_ALLOW_OFFICIAL_PUBLISH` 模式。
- **本期不做**：不改 `data_contracts.py`、不建运行时自动 failover、不做代码表 endpoint
  （备援切换需要枚举能力时再加，走 tgw 原生 `QueryCodeTable`）。
- 扶正为常驻主源的附加条件（ADR-016 记录）：退市股历史覆盖 ✓、历史深度 ✓、
  连续 N 周校验车道无 ERROR 级 `close_difference`。

## 4. Phase 0 前置实测（实施时第一批，结果决定细节口径）

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

## 5. 与 ADR-015 的关系

- **准入表修订**：`REUSABLE_CHANNELS` **以 `("xingyao","daily")` 替换 `("baostock","daily")`**。
  条目数仍为 3，因此 ADR-015 Decision 第 1 条的准入不变量（"准入集恰好等于 `reuse=True`
  调用点集"）继续成立、断言无需放宽——这正是选"替换"而非"并存"的理由。baostock 休眠
  车道若恢复，其复用语义须重新准入（记入 ADR-016），不随配置开关自动回归。
- **ADR-015 修订方式：加指针，不改正文**（不静默重写历史）。ADR-015 顶部加一段
  **Amendment (ADR-016)** 说明：Decision 1 的通道枚举以 xingyao 替换 baostock（车道
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
  不重试）、惰性 import 缺失 → optional 降级语义、transport_id 固定值。
- **`tests/unit/test_xingyao_factor.py`**：相邻因子变化 → 事件日期提取（含首行、无变化、
  单行序列）；快照落盘形状（source/endpoint）。
- **`tests/fixtures/xingyao_daily.csv`** + `tests/integration/test_source_contracts.py`
  离线契约，对齐既有源 fixture 模式（假客户端 + 原生列断言，参照 `baostock_daily.csv`
  与 `test_*_returns_recorded_native_columns`）。此文件是纯增量，不破坏既有断言。
- **`tests/external/`**：实时契约（仅 `pytest -m external`，需 `AD_*` 凭据）。

### 7.1 既有断言：必须改动的是七处（落在四个测试文件；推翻初稿的"既有断言不动"）

初稿称"唯一例外是 `REUSABLE_CHANNELS` 防回归断言"，**该说法不成立**。新增一个已注册源会
连带打破一切断言"全部源 ok"的测试，因为 `source_status` / `build_config.source_status`
遍历 `_CONFIGURED_SOURCES` 全体。逐处如下：

| 断言 | 断因 | 处理 |
| --- | --- | --- |
| `tests/integration/test_pipeline_fetch_coverage.py::test_update_writes_call_ledger`（精确字典含三源零值行） | baostock 不再被构造 → 退出账本；xingyao 进入 | 测试项目加 xingyao 桩，字典按实际参与源生成 |
| `tests/integration/test_data_pipeline.py::test_update_with_explicit_end_publishes_merged_dataset`（`all(status.ok)`） | `_CONFIGURED_SOURCES` 新增 xingyao → `ok=False` | 同上（桩化 xingyao） |
| `tests/integration/test_data_pipeline.py::test_successful_update_binds_sanitized_build_evidence`（`reason_code == "ok"`） | 同上（经 `_source_evidence`） | 同上 |
| `tests/integration/test_raw_snapshot_reuse.py::test_a_retry_round_reuses_the_stored_prefix`（`raw_snapshot_reuse` 精确字典） | baostock 键消失 / xingyao 键出现 | 同步字典 |
| 同测试 `ledger["baostock"]["reused"]` | 键不存在 → KeyError | 改指 xingyao |
| `tests/integration/test_data_pipeline.py::test_optional_validation_failure_still_publishes` | 注入的是 baostock 桩，车道已换 → 不再产生 `CODE_OPTIONAL_SOURCE_FAILURE` | 桩改指 xingyao（可选源失败的角色随车道转移） |
| `tests/unit/test_raw_reuse.py` 两条准入断言（`_ADMITTED` 表 + "恰好等于调用点集"） | 见 §5 | baostock 翻 `False`、xingyao 入册；**不变量不变** |

另需同步的测试侧 helper（初稿清单未列）：`tests/integration/test_pipeline_fetch_coverage.py`
的 `_all_stubs()` / `_sources()`、`tests/integration/conftest.py` 的 `_fixture_sources_yaml`。
`templates/project-config/sources.yml` 也必须加 xingyao 条目——否则夹具项目里星耀无处构造。

### 7.2 新增集成测试

- validation 车道接替：stub xingyao 源 → `validation_present` 翻转恢复
  （`primary_source_missing` 分类回归）；星耀停牌日形态按 §4.1 结论分别断言。
- 因子懒通道失败降级（WARNING + 缺席通道）。
- 账本与 `raw_snapshot_reuse` 形状：xingyao 行出现、baostock 行消失。**注意**：账本的
  `calls`/`endpoints` 恒为 0（`src/` 内没有任何适配器定义 `calls`，`render_call_ledger`
  走 `getattr(source, "calls", 0)` 兜底），因此只能断言**行的存在/缺席**与 `reused` 段，
  不能断言调用数——初稿"账本形状含 xingyao calls"的说法本身就是错的。

## 8. 验证命令

```bash
pytest tests/unit/test_xingyao_source.py tests/unit/test_xingyao_factor.py -q
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
懒通道）、`project/drift_audit.py`（`_source_for` 加 xingyao 分支，否则 D5.4 静默失效）、
`src/stock_quant/data_model/normalize.py`、`src/stock_quant/data_quality/raw_checks.py`、
`src/stock_quant/data_sources/raw_store.py`（准入常量**替换**）、
`project/configs/sources.yml` 与 `templates/project-config/sources.yml`（后者必须加
xingyao 条目，否则夹具项目无处构造）、`requirements.txt`、`environment.yml`、
`RUNBOOK.md`（私有包安装、external 说明、baostock 禁用/恢复程序）、
`docs/adr/DECISIONS_INDEX.md`、ADR-015 顶部的 Amendment 指针（+ ADR-016 本体），
以及 §7.1 列出的七处既有断言与测试 helper（`_all_stubs`/`_sources`/`_fixture_sources_yaml`）。

**不碰**：`cli.py`、`price_observed.py`、ADR-013 链、公司行动/成分/停牌证据链、
`data_contracts.py`、已发布数据与既有测试语义（§7.1 所列六处除外）。
