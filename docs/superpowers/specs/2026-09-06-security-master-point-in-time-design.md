# 证券主数据时点化与校验（security master point-in-time）设计

## 目标

用**真实可追溯**的上市日期、退市日期、证券状态和板块信息替换 `security_master`
中 bootstrap 写入的统一占位上市日期；让上市天数、因子上市期限制、涨跌停规则和回测
可交易性全部以真实主数据为准，并使「事实可追溯」成为正式研究发布的硬前提。

## 现状与根因

`classify_missing_row`（未上市/退市/非交易日/停牌或未知/主源缺失的区分）、
120 交易日次新门禁（`momentum._MIN_LISTED_DAYS` + runner 的
`listed_trading_days`）、universe↔`security_master` 一致性检查都已正确实现。
真正的缺口是：

- `bootstrap` 给 `security_master` 的每个标的统一写入 `SYNTHETIC_LIST_DATE =
  date(2018, 1, 2)`，`delist_date` 为空；120 日门禁因此对全部 30 只样本恒通过。
- `data update` 把 `security_master` **原样 carry**，从不刷新真实上市/退市日期。
- `security_master` 无 `list_status` 列；无任何来源快照可证明某标的的
  `list_date`/`delist_date` 来自何处。

本设计不新建因子、选股或回测语义；只把主数据从「占位」升级为「真实 + 可追溯 +
发布前校验」，并让正式研究在证据缺失时拒绝运行。

## 范围与非目标

范围：tushare `stock_basic` 端点接入与真实快照、`data update` 必需刷新、
`security_master` 增列 `list_status`、新证据表 `security_master_coverage`、
冻结校验与质量报告接线。

本期不实现：

- akshare / baostock 对主数据的交叉核验源（留待 P1 项 #4 独立价格与来源核验）；
- ST/风险警示的**历史时点序列**（`list_status` 只表达上市中/退市/暂停的快照状态；
  涨跌停规则所需的 ST 时点解析不属本项目，维持现状的 effective-dated 配置）；
- 全市场股票池扩展（仍是 30 只固定工程样本 + 两个基准）。

## 核心模型

### `security_master`（既有表，增一列）

现有列 `symbol, name, exchange, board, list_date, delist_date` 保留。新增：

- `list_status`：`L`（上市中）/ `D`（退市）/ `P`（暂停上市）/ `NOT_APPLIED`（尚未
  应用真实事实，仅 bootstrap 空种子使用）。

`name / exchange / board` 仍以 `configs/universe.yml` 的 `selected_as_of` 快照
标签为准——它们是股票池标签，是**校验对象**，不由供应商实时口径替换。

### `security_master_coverage`（新证据表，最小列）

每条记录对应**一个 universe 标的**，在 `data update` 成功用 tushare `stock_basic`
刷新后写入。列：

- `symbol`
- `list_date` / `delist_date`：应用后的真实日期（上市中标的 `delist_date` 为空）。
- `list_status`：应用后的状态。
- `source`：证据来源（本期恒为 `tushare.stock_basic`）。
- `snapshot_sha256`：来源原始快照内容哈希。
- `sdk_version`：供应商 SDK 版本。
- `checked_at`：核验时间（UTC）。

**行存在即 VERIFIED**：本表不含 `status`/`reason` 列。表只在 `data update`
成功 reconcile 后随数据集原子发布——某标的有一行，即证明该标的的上市/退市事实
已由一次真实快照确认；空表或旧数据集缺表，即视为该标的不可信。这避免为单一
事实源复制 #1 的 `VERIFIED/VERIFIED_EMPTY/UNTRUSTED` 词汇。

## 数据流

1. tushare 适配器新增 `stock_basic` 端点：一次全市场参考快照（`ts_code`,
   `name`, `exchange`, `market`, `list_date`, `delist_date`, `list_status`）。
   该端点是全市场参考，天然单次请求，不为 30 个标的各打一次。
2. `data update` 将 stock_basic 设为**必需步骤**（tushare 已是必需角色）：
   抓取 → 原始快照连同哈希、SDK 版本、抓取时间写入 `raw_store` →
   按 `configs/universe.yml` 的标的过滤并映射字段 → 刷新 `security_master` 的
   `list_date / delist_date / list_status`，并生成 `security_master_coverage` 行。
3. **失败语义**：stock_basic 抓取失败，或返回结果缺任一 universe 标的 →
   FATAL → 本次 update 阻断发布（沿用现有「门禁失败不 publish」的原子语义）。
   这保证：任何携带真实行情的已发布数据集，必然经历过一次成功的 update，
   因而必然带真实主数据与 `security_master_coverage`。
4. bootstrap 空种子：`list_date=NaT`、`delist_date=NaT`、
   `list_status="NOT_APPLIED"`，coverage 表为空（或缺表）。该种子无 daily_bar，
   且 research 冻结必拒——占位日期不再伪装成真实日期，也永远到不了可信发布边界。

## 冻结校验（research 门禁）

复用 #1 建立的 `DataTrustMode`（research/engineering）词汇，不新建模式：

- **research 冻结时**：universe 内每个标的在固定数据集的
  `security_master_coverage` 中都必须存在一行；缺任一行（空表 / 旧数据集 /
  bootstrap 占位种子）→ 拒绝在回测前运行，拒绝理由为逐标的稳定错误码
  （如 `SOURCE_NOT_REQUESTED`）。
- **engineering 模式**：同一数据集照常作为诊断运行，评价恒为 UNTRUSTED，
  清单 REJECTED、报告不可展示为可信绩效（与 #1 行为一致）。
- 校验只需查「行存在与否」，**无需检测占位哨兵日期**的逻辑。

## 校验矩阵（沿用现有 issue 词汇）

1. **集合一致（已有，保留）**：`configs/universe.yml` 与 `security_master` 的
   symbol 集合一致，不一致 FATAL。
2. **事实 vs 行情边界（新增）**：每标的 `list_date` 不晚于该标的 `daily_bar`
   首日、`delist_date` 不早于末日；行情行早于 `list_date` 或晚于
   `delist_date` 属异常。缺失行情沿用 `classify_missing_row` 的
   `not_listed / delisted` 分支，保证区分正确。
3. **validate 一致性（新增）**：`security_master` 与 `security_master_coverage`
   逐标的的日期/状态一致，coverage 表存在。

## 错误处理与可审计性

- stock_basic 抓取失败 / 返回缺标的 / 字段不完整 → 稳定错误码的 FATAL issue，
  update 不发布。
- research 冻结缺 coverage 行 → 稳定错误码 + 逐标的清单，写入运行失败原因。
- 证据哈希、SDK 版本、checked_at 进入数据集产物，可审计回供应商原始快照。

## 测试与验收（避免冗余）

验收（对应 TODO #2）：

- `security_master` 每个标的的上市/退市事实可追溯：真实数据核对 coverage 表逐行
  可回溯到 tushare stock_basic 快照哈希；fixture/合成数据证明「缺行 → freeze 拒绝」。
- 新股在真实上市满 120 个交易日之前不进入因子候选集：**只加一个** fixture「新股」
  （`list_date` 距今不足 120 交易日），断言其被 `seasoning_below_120` 排除；
  不重复测试已有的门禁逻辑。

最小测试集：

- 单元：tushare `stock_basic` 适配器用 fixture 原始帧做契约测试；
  freeze 校验「空 coverage → 拒绝」与「有行 → 接受」各一。
- 集成：`data update` 用真实日期刷新 master 并发布 coverage；stock_basic 失败
  阻断发布；bootstrap 空种子 `list_status="NOT_APPLIED"` 且无 coverage 行。
- fixture 涟漪：conftest + smoke 的 fixture builder 仿照 `_coverage_table` 为每个
  universe 标的生成一行 `security_master_coverage`；`broken=True` 变体保持空表以测
  freeze 拒绝。全部沿用现有 helper，不新增并列工具。

## 成功标准

任何被正式研究接受的实验，其固定数据集的 `security_master` 都携带来自 tushare
`stock_basic` 真实快照、可逐标的回溯哈希的上市/退市事实；占位统一日期不再出现在
任何已发布数据集中；新股在真实上市满 120 交易日之前不进入因子候选集。
