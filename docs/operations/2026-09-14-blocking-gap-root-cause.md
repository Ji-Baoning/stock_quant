# 扩池阻断缺口：根因定位

- 运行时间：2026-09-14
- 窗口：2015-01-05..2026-08-28
- 前置证据：[2026-09-14-expansion-gap-risk-probe.md](2026-09-14-expansion-gap-risk-probe.md)
  （抽样 60 只 → 5 个阻断项，外推 629 只预计 52.4 个，95% 上界 96.4）
- 本报告只读：未发布数据集、未写 raw 快照、未改 `src/`、`CURRENT` 前后未动

## 结论

owner 批准的 Stage B 前提——「**零 src 改动**，扩 `universe.yml` 到 659，
今天就能把 [扩池 → data update → 发布 → validate] 跑通」——**不成立**。
探针量到的 5 个阻断项已全部定位到三处缺陷，其中两处（占 4/5）不在
`universe.yml` 所在的层面，改配置文件无法触及：

| # | 缺陷 | 层面 | 与 5 个阻断项的关系 |
|---|---|---|---|
| D1 | `parse_trade_date` 对 `pd.NaT` 返回 `NaT` 而非 `None` | src，1 行 | 10/60 抽样标的因此失去**全部**公司行为；其中 000656.SZ 直接变成阻断项（1/5） |
| D2 | **配股**没有任何来源产出 `rights_issue_ratio`/`rights_issue_price` | src，入口缺失 | 3/5（002202.SZ、600008.SH、600089.SH） |
| D3 | 停牌证明规则把复牌日的除权日排除在解释范围外，且容差是**绝对** 0.005 元 | src，规则 | 1/5（600654.SH） |

三处都不是配置问题。Stage B 要发布，必须先动 `src/`。

**后续追加（同日，实跑推进后）**：D1–D3 修完后全量重放又暴露出 **D4**（供应商把
停牌日当**行**返回，绕过停牌证明）与 **D5**（被隔离的 2017 特别分红被读成
`unexplained_primary_gap`），仍是数据层的 src 缺陷，修完 `ERROR` 由 29 归零。
但**即使数据层全干净，这一轮仍然没有发布**，原因换成了两处与数据无关的地方：

| # | 缺陷 | 层面 |
|---|---|---|
| D4 | 供应商停牌行被值检查拦成 `invalid_ohlc` + `nonpositive_price` | src，数据 |
| D5 | 被隔离动作让 601088.SH 的停牌读成 `unexplained_primary_gap` | src，数据 |
| **D6** | `configs/universes/` 里重复 `universe_id` → 发布前 FATAL | **配置（运行途中被我写入）** |
| **D7** | 发布路径要求显式 `TUSHARE_TRANSPORT=relay`，而它不在 `.env` 里 | **启动环境** |

D6/D7 都**不会**在数据里留下任何痕迹，`data validate` 也照不到它们
（`validate()` 不读判据目录、不建 transport）。这是「数据层零 ERROR 仍然不发布」
的全部原因。

## D1 · `parse_trade_date` 把 NaT 当成合法日期

### 机制

[clean.py:42-61](../../src/stock_quant/data_model/clean.py#L42-L61) 的守卫顺序：

```python
if value is None:      return None
if isinstance(value, datetime): return value.date()   # ← NaT 在这里被截走
if isinstance(value, date):     return value
if pd.isna(value):     return None                    # ← 永远轮不到 NaT
```

`pd.NaT` **是 `datetime` 的实例**，而 `pd.NaT.date()` 又原样返回 `NaT`：

```
isinstance(NaT, datetime) = True
NaT.date()                = NaT (NaTType)
parse_trade_date(NaT)     = NaT      ← 文档承诺 date | None，实际漏出 NaT
parse_trade_date(None)    = None
parse_trade_date(nan)     = None
```

于是 `_reject_reason` 的 `is None` 守卫（[corporate_actions.py:410-415](../../src/stock_quant/data_model/corporate_actions.py#L410-L415)）
判不出 `NaT`，事件带着 `NaT` 的 `ex_date` 进入候选集，键排序时
`for key in sorted(keys)`（[corporate_actions.py:227](../../src/stock_quant/data_model/corporate_actions.py#L227)）
在 NaT 与 `datetime.date` 之间比较，抛 `TypeError`；同源同日两行都落在
`NaT` 键上时，则改抛 `ValueError: ... more than one implemented supported
action for <symbol> on NaT`。

**10/60 个对账失败（9 个 TypeError + 1 个 ValueError）是同一个成因。**

### 影响面

生产在 `_reconcile_action_frames`（[data_pipeline.py:1686-1704](../../src/stock_quant/data_pipeline.py#L1686-L1704)）
里逐只 `try/except`，异常降级为 WARNING，返回空 DataFrame —— 该标的**失去
全部公司行为**（含它本来正常的那些年份），而不是只丢那一行。

### 修复与验证

把缺失值守卫提到日期分支之前即可。运行时打补丁实测（未落盘）：

| symbol | 修复前 | 修复后 |
|---|---|---|
| 000408.SZ | TypeError | accepted=8，ex 2017-11-14…2026-04-17 |
| 000538.SZ | TypeError | accepted=14，ex 2015-07-22…2026-04-30 |
| 000656.SZ | ValueError | accepted=7，ex 2015-04-29…2021-07-09 |
| 000709.SZ | TypeError | accepted=10 |
| 000895.SZ | TypeError | accepted=19 |
| 002001.SZ | ValueError | accepted=14 |
| 002608.SZ | TypeError | accepted=5 |
| 600039.SH | TypeError | accepted=14 |
| 600703.SH | TypeError | accepted=11 |
| 600760.SH | TypeError | accepted=10 |

恢复出的除权日节奏正常（每年一次，集中在 6–7 月分红季）。

**回归安全**：4 只本来正常的标的（002202.SZ、600008.SH、600089.SH、600654.SH）
修复前后 accepted 行数与 ex_date 列表**逐位相同** —— 该改动对合法输入是
no-op，只在 `NaT` 上改变行为。`parse_trade_date` 的 5 处调用方
（`normalize.py`、`suspensions.py`、`corporate_actions.py`、`calendar.py`、
`trade_calendar_facts.py`）全部按 `date | None` 消费；`tests/` 下没有任何
测试直接引用它，也没有测试依赖 NaT 透传。

**单独修 D1 即可清掉 000656.SZ 的阻断缺口**：恢复出的 2017-05-26 除权日
落在其 2017-05-05..2017-07-04 停牌期内，该缺口由 `unexplained_primary_gap`
转为已证明。

## D2 · 配股：入口完全缺失

### 证据

三个阻断缺口与 cninfo 配股记录**逐日吻合**，缺口区间就是配股缴款停牌期，
除权基准日落在复牌日：

| 标的 | 缺口 | cninfo 配股记录 | 理论 pre_close | relay 实际 pre_close |
|---|---|---|---|---|
| 002202.SZ | 2019-03-21..28 | 停牌 2019-03-21..28，10配1.9@7.02，除权 2019-03-29 | (16.05+0.19×7.02)/1.19 = **14.608** | **14.62** |
| 600008.SH | 2020-09-21..28 | 停牌 2020-09-21..28，10配3.0@2.29，除权 2020-09-29 | (3.15+0.3×2.29)/1.3 = **2.951** | **2.95** |
| 600089.SH | 2017-06-01..08 | 停牌 2017-06-01..08，10配1.5627@7.17，除权 2017-06-09 | (9.66+0.15627×7.17)/1.15627 = **9.3234** | **9.32** |

（`ak.stock_allotment_cninfo`，`AKSHARE` 现有版本已提供该接口。）

两个分红端点**都不覆盖配股**：cninfo/eastmoney 的 `除权除息日 ±45 天` 窗口内
一行都没有（近窗行数 0）。src 里 `配股` 只出现在一处：

```
src/stock_quant/data_model/corporate_actions.py:56
_UNSUPPORTED_KEYWORDS = ("配股", "配售", "吸收合并", "换股")
```

### 下游的真实状态（关键，决定了修复量级）

`rights_issue_ratio`/`rights_issue_price` 字段**已经存在**（schema 列、
`RECONCILED_COLUMNS`），但三处消费端的立场各不相同：

- [corporate_actions.py:475-476](../../src/stock_quant/data_model/corporate_actions.py#L475-L476)：
  `_parse_event` 把它们**硬编码为 None**，没有任何来源去填。
- [backtest/engine.py:580,753-784](../../src/stock_quant/backtest/engine.py#L580)：
  **真处理**配股（认购现金 + 股本变动）——回测层已就绪。
- [adjusted_bar.py:96-97](../../src/stock_quant/data_model/adjusted_bar.py#L96-L97)：
  是**守卫而非特性**。复权递推式是
  `TR_t = TR_{t-1} · (P_t·(1+b+c) + d) / P_{t-1}`（[adjusted_bar.py:13](../../src/stock_quant/data_model/adjusted_bar.py#L13)），
  式子里没有配股项，所以任何 `rights_issue_ratio > 0` 的事件都被记为
  `unsupported_corporate_action` 断裂、序列重锚（`quality_severity=ERROR`）。

**因此 D2 不是「加个接口」**：把配股灌进来，复权序列会在每个配股除权日
产出 ERROR 行；要么给复权递推式加配股项（`(P_t·(1+b+c+r) + d − r·price)/P_{t-1}`），
要么接受每个配股除权日一条 ERROR 行。再加上第三个端点会让
`_coverage_verdict` 的「每个被请求端点都必须成功」多一个失败面
（[data_pipeline.py:2204](../../src/stock_quant/data_pipeline.py#L2204)）。

这三件事（来源、复权语义、信任/覆盖语义）是**设计决策**，不是补丁。

## D3 · 停牌证明规则：复牌日的除权日不算数

[suspensions.py:130-148](../../src/stock_quant/data_model/suspensions.py#L130-L148)：

```python
run_actions = _actions_in_run(actions, symbol, first, last)   # first..last = 停牌缺口
if not ex_dates and abs(next_pre - prev_close) > _TOLERANCE:  # 绝对 0.005 元
    → unexplained_primary_gap (ERROR, 阻断)
```

两个边界：

1. `_actions_in_run` 要求 `first <= ex_date <= last`
   （[suspensions.py:218](../../src/stock_quant/data_model/suspensions.py#L218)），
   而配股/转增的除权日就在**复牌日**——正是它调整了被比较的那个
   `next_pre`。600654.SH 实测：`ex_inside_run=False`、`ex_on_resumption=True`、
   缺口 2022-12-22 被阻断，而它的转增除权日正是复牌日 2022-12-23。
2. `_TOLERANCE = 0.005` 用在 `abs(next_pre - prev_close)` 上，是**绝对**元。
   对 2.86 元的 600654.SH，0.01 元的偏差 = 0.35%，超过绝对阈值却低于常见
   的相对 0.5%。

修 1 即可让 600654.SH 转证（除权日非空时根本不再看容差）。但注意：现行规则
接受「缺口内有任一除权日」是**不校验幅度**的——放宽到复牌日会扩大这个口子
（一个碰巧落在缺口内的除权日可以掩盖一次真正的数据丢失）。要放宽，就该同时
校验观测比值与事件隐含复权因子是否相符。这是一个口径决策。

## D4 · 供应商把停牌日当作**行**返回，而不是省略它

### 机制

`suspension_rows` 的证明建立在一个假设上：停牌日**不在** `daily` 响应里。
供应商还有第二种表示法——把该日作为一行返回：`open=high=low=0`、`vol=0`、
`amount=0`、`close` 结转等于自己的 `pre_close`。

这一行在链里是**存在**的，所以缺席证明根本看不到它；同时它会一路走到数值检查，
被 `check_daily_values` 同时记两条：
`invalid_ohlc`（三价为零）与 `nonpositive_price`（收盘价来源非正）。
两条都在 `PUBLICATION_BLOCKING_CODES` 里
（[gates.py:39-57](../../src/stock_quant/data_quality/gates.py#L39-L57)）。

### 全量证据（659 只，非抽样）

对 659 只 daily 原始响应逐一扫描该签名：**14 行 / 5 只**，全部落在 2026 年，
全部 `vol=0`、`amount=0`、`close==pre_close`。

| 标的 | 行数 | 日期跨度 | 结转价 |
|---|---|---|---|
| 600717.SH | 7 | 20260610..20260618 | 4.31 → 4.21 |
| 688072.SH | 2 | 20260629..20260710 | 832.00 |
| 000793.SZ | 2 | 20260618..20260730 | 2.63 → 2.19 |
| 000008.SZ | 2 | 20260707..20260710 | 2.36 |
| 000656.SZ | 1 | 20260701 | 1.07 |

600717.SH 是最完整的样本，原始 `daily` 链（未加工）：

| trade_date | open | high | low | close | pre_close | vol |
|---|---|---|---|---|---|---|
| 20260608 | 4.40 | 4.43 | 4.27 | **4.31** | 4.43 | 322883.55 |
| 20260610 | 0 | 0 | 0 | 4.31 | 4.31 | 0 |
| … | 0 | 0 | 0 | 4.31 | 4.31 | 0 |
| 20260617 | 0 | 0 | 0 | 4.31 | 4.31 | 0 |
| 20260618 | 0 | 0 | 0 | **4.21** | 4.21 | 0 |
| 20260623 | 4.30 | 4.58 | 4.21 | 4.37 | **4.21** | 945776.88 |

**这 14 行正是本模块自己会 materialize 的同一对象**，不是新信息：

1. `suspensions.py:184` 的
   `price = prev_close if ex_dates and day < ex_dates[0] else next_pre`
   对「除权日之前的日结转旧收盘、从除权日起结转新参考价」的规定，逐字产出
   `4.31 … 4.31, 4.21`。
2. 那两个价格也不是供应商的自由取值。该标的已接受的动作是
   `除权日 2026-06-18`、`10派1.02元`（cninfo 与 eastmoney 两源一致）：
   `4.31 − 0.102 = 4.208 ≈ 4.21`。供应商的 06-18 行携带的正是这条分红的
   除权参考价。
3. 688072.SH 的 832.00 在停牌区间的两侧都被持有（06-26 收 832.00，
   07-13 的 `pre_close` 也是 832.00），同一条链证据。

### 修复

新增 `canonicalize_supplier_suspensions`
（[suspensions.py:229-326](../../src/stock_quant/data_model/suspensions.py#L229-L326)），
在 `_fetch_primary_stock` 里紧接 `normalize_daily` 调用，**原始响应不动**。

签名要求**全部**成立才改写：
`open==high==low==0` ∧ `vol==0` ∧ `amount==0` ∧ `close>0` ∧ `pre_close>0`
∧ `|close − pre_close| <= 0.005`。命中即就地替换为该模块的标准停牌 bar
（OHLC = 结转价、`volume=0`、`amount=0.0`、`adjustment="unadjusted"`、
`source="tushare_suspend"`），并发一条 `suspension_row_materialized` INFO
（`details.kind = "supplier_no_trade"`）。

**签名之外的一律不动**：带成交量的行、或收盘价与自己 `pre_close` 不符的行，
仍然原样交给数值检查去阻断——这一步只做规范化，不替供应商掩饰缺陷。
缺任一签名列的响应（全部离线 stub）整帧原样返回。

### 为什么是「就地规范」而不是「删掉这些行」

删行会移动链的锚点。以 688072.SH 为例：删掉 0629 与 0710 两行后，缺席区间
从 `0630..0709` 扩成 `0629..0710`，`prev` 从 0629 行退到 0626 行、`next` 从
0710 行进到 0713 行——**门的通过与否开始取决于这些新锚点是否被已接受动作
覆盖**，也就是把停牌证明耦合到了另一个数据源的完整性上。就地规范则完全
不动链，只把「供应商已经写下来的行」翻译成本模块的词汇。

## D5 · 被隔离的 2017 特别分红，让 601088.SH 的三个月停牌读成数据丢失

### 机制

601088.SH（中国神华）2017-06-05..2017-08-31 停牌，2017-09-01 复牌。链上
停牌前最后收盘 **22.29**，复牌日的 `pre_close` **19.32**，差 **2.97**。

这个缺口理论上由一条除权日解释：`ex_date = 2017-07-10`。但两源在对账时冲突：

- **cninfo** 在该除权日有**两条**记录——2016 年度分红 `10派4.6元` 与特别分红
  `10派25.1元`，`_combine_same_day_events` 合并后 = **2.97 元/股**；
- **eastmoney** 只有年度分红一条（0.46）。

真冲突 → 对账隔离（`cross_source_conflict`）→ 已接受动作里没有这个除权日 →
`suspension_rows` 判 `unexplained_primary_gap`（ERROR，阻断）。

**价格序列独立佐证 cninfo 的口径**：`22.29 − 19.32 = 2.97`，与该源的
合并值逐位相符，而与 eastmoney 的 0.46 相差 2.51。

### 修复（复用既有机制，不新造通道）

`configs/corporate_action_reviews.yml` 增一条 review，与已有
`300750.SZ#2024-04-30` 同形（cninfo 同日两条、eastmoney 只有年度分红）：

```yaml
- symbol: 601088.SH
  ex_date: 2017-07-10
  selected_source: cninfo
  record_date: 2017-07-07
  cash_dividend_per_share: 2.97
```

`apply_corporate_action_reviews` 会**逐字段校验**这条 review 与隔离行是否
相符（`record_date` / `cash_dividend_per_share` / `bonus_share_ratio` /
`capitalization_ratio`），不符即 `ValueError`——放宽的只是「选哪一源」，
不是「数值可以不核对」。

## D6 · 发布被自己的工作区阻断：`configs/universes/` 里重复的 `universe_id`

D1–D5 全部修好之后，`data update` 仍然整轮不发布：

```
run_id=data_update_bcb9112d2504
ERROR=0 FATAL=1 INFO=1532 WARNING=1466
source tushare: ok
source akshare: ok
source baostock: not_ok
FAILED: publication gate did not pass; dataset unchanged
```

`ERROR=0` 说明数据层已经干净（与离线重放的预测逐位一致）。剩下的**唯一** FATAL
不在数据里，也不在任何 fetch 路径上——这正是它难以定位的原因：报出的三个源状态里
`tushare`/`akshare` 都是 `ok`，而 `update()` 里绝大多数 FATAL 分支都会把对应源的
状态翻成 `not_ok`。

**机制**。`update()` 末尾、真正发布之前才读一次宇宙定义目录
（原 `data_pipeline.py:826`）：

```python
criterion = load_universe_coverage_criterion(root / "configs" / "universes")
```

`load_universe_coverage_criterion`（`research/universe.py:310-354`）扫描该目录下
每个 `*.yml`，按**文件内部的 `universe_id` 字段**去重，重复即抛
`UniverseCoverageError`，被 `update()` 转成 FATAL `universe_definition_invalid`。
而 `configs/universes/` 下当时同时存在两个都声明
`universe_id: custom_csi300_tw_tradable` 的文件——现行定义，以及当天为
「短解锁路径」复制出来的 `custom_csi300_tw_tradable_28.yml`。

**责任归属**：`custom_csi300_tw_tradable_28.yml` 的 mtime 是 `03:42:28`，
而出问题的这轮 `data update` 直到 `03:46:11` 才结束。也就是说**该文件是在这轮
运行途中被写进被扫描目录的**——这轮 fetch 没有任何问题，是运行期间的工作区变更
毒化了它的收尾。这条同时是纪律教训：**运行期间不得改动 `configs/` 与 `src/`**。

**修复**。两个同名定义不可能共存：

| 方案 | 结果 |
|---|---|
| 给 `_28` 换一个 `universe_id` | 它的 `version` 随之改变，不再逐位复现 `c211b85c…`；而该文件自己的注释写明「任何其它值说明重建错了、不得使用」 |
| 加 `enabled: false` | **无效**。判据加载器会剥掉 `enabled`（`universe.py:334-336`），但 `load_universe_definition` 把整份文档交给 `extra="forbid"` 的模型（`universe.py:357-367`），所以同一个文件在 research run 里会加载失败 |
| 把 `_28` **移出**被扫描目录 | 采用 |

现行定义必须保留原 `universe_id`（`momentum_60d_wf_tw_baseline.yml:32` 与
`momentum_60d_pit_official.yml:28` 按名引用它），所以走人的只能是 `_28`。
它被**逐字节**移到 `project/configs/universes/archive/`（sha256
`2fc9c811…` 前后一致），换入步骤与版本不变量记在
[archive/README.md](../../project/configs/universes/archive/README.md)。
移出后判据加载恢复：4 个定义、`acceptance_start=2015-01-05`、无重复。

**顺带修掉两个「看得见」的问题**（都不改变判据本身，只改变失败时机与可观测性）：

1. **判据改为在任何 fetch 之前读取**（`data_pipeline.py`，`update()` 开头）。
   它本来就只用于发布时写 `build_config`，提前读只是让「配置坏了」在**几秒**内
   失败，而不是烧完整个窗口的配额之后才失败——上面这轮就是后者。
   没有测试钉住原顺序（`CODE_UNIVERSE_DEFINITION_INVALID` 无测试引用），
   唯一的调用点即此处。
2. **发布被拒时打印阻断项本身**（`cli.py`，`_echo_blocking_issues`）。
   此前 CLI 只回显 `_report_summary` 的严重度计数，`QualityIssue` 的
   code/symbol/table/details 全部丢弃，而质量报告在失败轮次里**不落盘**
   （`quality_report.json` 只由发布器为已发布版本写出）。这就是本轮 FATAL
   必须靠离线复现才能看见的原因。现在四个拒绝分支都会逐行打印阻断项
   （按报告自身的确定性序列化，最多 20 行，其余折叠）。

## D7 · 发布路径要求显式 `TUSHARE_TRANSPORT=relay`，而它不在 `.env` 里

修好 D6 之后重跑，20 秒即失败（fail-fast 生效），新加的阻断项打印立刻给出原因：

```
blocking issue: severity=FATAL code=source_fetch_failed table=data_update
  details={"endpoint": "trade_cal",
           "message": "cannot initialise source 'tushare': TUSHARE_TRANSPORT must be
                       set explicitly for a published build (expected 'relay');
                       this path never falls back",
           "source": "tushare"}
```

`.env`（仓库根，6 个键）里**没有** `TUSHARE_TRANSPORT`：
`TUSHARE_PROXY_URL` / `TUSHARE_PROXY_KEY` / `BASIC_RDS_KEY` / `TUSHARE_TOKEN` /
`TUSHARE_RELAY_URL` / `TUSHARE_RELAY_KEY`。而且 `src/` 与 `project/` 里没有任何
`load_dotenv`——`.env` 不会被程序自己读，必须由启动方 export。发布路径
（非 diagnostic）刻意不做自动兜底（`tushare_transport.py:42-54`），
所以必须显式 `export TUSHARE_TRANSPORT=relay`。

启动方式（两者缺一不可，已在实跑中验证 transport 解析为
`kind=relay host=jiaoch.top`）：

```sh
cd /home/ji/work/program/stock
set -a; . ./.env; set +a
export TUSHARE_TRANSPORT=relay
setsid nohup /home/ji/miniconda3/envs/py310/bin/python -m stock_quant \
    data update --start 2015-01-01 --root project > /tmp/update_659_v3.log 2>&1 &
```

`--end` 不传：`update()` 会用已发布日历的最后一个开市日解析，实测得
`resolved_end_date=2026-08-28`（`project.yml` 的 `end_date: 2026-08-30` 不是
开市日，传它反而改变窗口）。

## 全量枚举（回答上文「尚未回答」第一条）

离线重放（`/tmp/replay_issues.py`，只读不可变 raw store，复用生产函数：
`normalize_daily` / 生产对账链 / `suspension_rows` /
`canonicalize_supplier_suspensions` / `check_daily_values` / `check_schema` /
`check_primary_key_conflicts` / `check_provenance` / `_membership_issues` /
`_universe_master_issues` / `master_bar_boundary_issues` / 三条 review），
窗口 `2015-01-01..2026-08-28`、659 只：

| 轮次 | ERROR | 构成 |
|---|---|---|
| 修复前 | **29** | `invalid_ohlc` 14 + `nonpositive_price` 14 + `unexplained_primary_gap` 1 |
| D4 + D5 后 | **0** | —— |

修复前的 29 与真实 `data update` 报出的 29 **逐码相同**，这是重放可信的判据。
修复后 `by_code = {suspension_row_materialized: 1528, suspension_run_unverified: 37}`，
`by_severity = {INFO: 1528, WARNING: 37}`，已接受动作 6872 条 / 649 只，
对账失败 0。

**两处必须更正的先前结论：**

1. 早先用同一脚本报出的「全 659 只零 `unexplained_primary_gap`」是**无效结论**。
   当时的开市日网格取自 `trade_cal` manifest 的 `start_date == "2015-01-01"`，
   而生产请求的是 halo 区间，存下来的 `request_parameters.start_date` 是
   **`"2015-01-04"`**；过滤后网格为空，`suspension_rows` 对每只标的都在
   `if ... not open_days` 处提前返回，**全部证明被跳过**。改成直接调用生产的
   `parse_trade_cal_frame`（`halo_start=2014-12-31`）+ `materialize_open_days`
   后，网格 2833 个开市日，结论才成立。
2. 相应地，D5 是**修正网格之后才暴露**的——空网格版本看不见它，这正是
   「证明被跳过」与「没有缺口」长得一模一样的原因。

修正后 D1 类与 D3 类在全量上均为 0：12 年窗口、2833 个开市日、659 只标的里
**恰好一个** `unexplained_primary_gap`，即 D5（被隔离动作），不是 D1/D3。

## 为什么不能直接跑 `data update`

`data update` 是**全有或全无**：`update()` 只要报告里存在任一 FATAL，或
`evaluate_publication` 命中 `PUBLICATION_BLOCKING_CODES`
（[gates.py:39-57](../../src/stock_quant/data_quality/gates.py#L39-L57)，
含 `CODE_UNEXPLAINED_PRIMARY_GAP`），就整轮不发布、`CURRENT` 不动。

按探针外推，629 只新增标的预计产生 **52.4 个**阻断项（95% 上界 96.4）。
D2 一项就占抽样阻断项的 3/5，且配股在 12 年窗口内是**必然重复**的事件类别
（见下方的全量枚举）。也就是说：不修 src 直接跑，约 1 小时的 `data update`
几乎必然一个字节都不发布。

## 配股全量枚举

`ak.stock_allotment_cninfo` 对 629 只标的逐一枚举（`master=684`、在市 `659`、
减去现行 `universe.yml` 的 30 只 = `629`，与探针口径一致）。0 次调用失败。

| 项 | 值 |
|---|---|
| 目标标的 | 629 |
| 窗口内配股记录 | **36** |
| 涉及标的 | **32**（5.1%） |
| 其中除权日**落在停牌期内**（现行规则已接受） | 1 |
| 其中除权日**落在停牌期外**（现行规则拒绝 → 阻断） | **35** |

**关键结构**：36 个记录里 35 个的除权基准日就落在**停牌截止日的次日**，
只有 1 个（600256.SH，2018-03-28）落在停牌期内。也就是说
「除权基准日 = 复牌日」是 A 股配股的**常规**时间线，不是边界情况 ——
**D3 不是补漏，D2 和 D3 必须一起修**：只修 D2，这 31 只的缺口照样被规则拒绝；
只修 D3，这 31 只根本没有配股事实可认。任一单独修都留下全部 31 个阻断项。

31/629 = 4.9% 与探针的抽样估计互相印证：探针 5 个阻断项中 3 个是配股
（60%），外推 52.4 个 x 60% ≈ 31 个。

## 尚未回答

- ~~**阻断项的全量计数**~~ **已补测**：见上文「全量枚举」——659 只、
  2833 个开市日，修复前 29 个 ERROR（D4 28 + D5 1），D4/D5 后为 0。
  早先基于空网格的版本已作废。
- **仍未被重放覆盖的检查**：`check_adjusted_bar_lineage`（四个 adjusted_bar
  码在其中是 FATAL）、adjusted_bar 的 schema 与主键检查、`_read_baseline`、
  `_calendar_issues`、`_master_coverage_consistency_issues`、以及发布器的
  universe 覆盖判据。这几项只有真实 `data update` 会走到——这正是必须实跑
  一次而不能只看重放的原因。
- **D2 若只灌数据不改复权**：每个配股除权日会在 `adjusted_bar` 留下一条
  ERROR 行（`unsupported_corporate_action`，序列重锚），但**不触发出版门**
  ——`check_adjusted_bar_lineage` 只校验「已列出的 action id 是否已知」，
  不校验「已接受的动作是否被应用」（[data_pipeline.py:2388-2394](../../src/stock_quant/data_pipeline.py#L2388-L2394)）。
  这条不对等本身值得单独确认。
- **覆盖语义**：新增端点后 `_coverage_verdict` 的失败面扩大（每个被请求端点
  都必须成功），629 只 × 3 端点的调用量下失败概率未测。

## 复现

```bash
# 配股枚举（只读；输出逐只 JSONL 检查点 + 汇总）
python /tmp/scan_allotment.py 3193eaaa15d526c0c65a5f2bde7269394481f78f872a67ac735b78dc9f70f13a \
    /tmp/stock-probe/allotment_scan.json
```

D1 的验证：`tests/unit/test_clean.py` 与
`tests/unit/test_corporate_action_normalize.py` 的两个 NaT 用例在修复前
复现 `TypeError: Cannot compare NaT with datetime.date object`
（`corporate_actions.py:227`），修复后通过；12 只真实标的实跑见上表。

D4 的全量证据（只读，扫已存 raw，零出站）：
`/tmp/d4_evidence.py`（14 行 / 5 只的签名扫描）与
`/tmp/d4_chain.py`（原始链上下文）。单元与端到端覆盖在
`tests/unit/test_suspensions.py`：签名命中改写、带成交量不动、
收盘与自己 `pre_close` 不符不动、缺签名列整帧不动，以及一次
`data update` 端到端（000651.SZ 的停牌日以 `tushare_suspend` + volume 0 落库，
次日仍为 `tushare` + volume 100000）。

D5 的证据：`/tmp/lookup_action.py`（按 `request_key` 取回两源的原始分红表，
cninfo 2017-07-10 两条、eastmoney 一条）。

重放全程：`/tmp/replay_issues.py`（只读，`VERSION` 指向被阻断轮的
`1709eddb…`）。

## 补记（2026-09-15）：验收两项 FAIL 的切分实测

只读测得，用于 `docs/superpowers/specs/2026-09-15-data-layer-residual-defects-design.md`
的范围划定；本节只记录数字，结论在设计与 ADR-006。

- 已发布 `corporate_action_quarantine` 118 行的窗口归属（任一已知日期落在
  `[2015-01-01, 2026-08-28]` 即算窗口内）：窗口前 60 行 / 49 符号（32 行带
  `record_date` 2000–2010，28 行只有 `announcement_date` 1996–2010；全部
  `status=implemented`、`reason=incomplete`、`ex_date` 为空）；窗口内 58 行 / 25
  符号（50 行 `cross_source_conflict` 带 `ex_date`，8 行 `incomplete` 无 `ex_date`）。
  两段符号数 49 + 25 去重叠 = 71，与验收 `corporate_action_evidence` 报的符号数
  逐一对上；其中**仅被窗口前记录拉黑**的 = 46 只。
- 三档代价（去重叠后）：`ex_date`/`record_date` 在窗口前 25 只（其中 1 只
  `000629.SZ` 窗口内无任何已接受事实）；仅 `announcement_date` 在窗口前且
  `implemented` 21 只（其中 1 只 `000503.SZ`）；合计 46 只。裁掉后仍未翻转为
  `VERIFIED` 的 2 只 = 上述 2 只，停在 `FACTS_INCOMPLETE`。
- 推导前提：已发布 `corporate_action` 6,872 行**全部**同时带 `ex_date` 与
  `record_date`，`ex_date - record_date` 为 1–13 天、零负数（min 1、p50 1、
  p99 5、max 13）。故「`ex_date ≥ record_date`」在本项目数据上是可依赖的推导。
- 停牌侧：验收 `date_window_completeness` 的 1,437 条 `unexplained_missing_row`
  跨 29 只，全部落在 `2015-01-05..2015-12-07`，每一条都是起点正好是窗口首个开市日
  的停牌 run（`suspensions.py:128` 拒绝对没有 `before` 锚点的 run 落地）。
- 窗口归属的 `record_date` 档不是纯推导：实测只支持 `ex_date ≥ record_date`
  （lag 1–13 天），故窗口前方向按 `_EX_DATE_LAG_MAX_DAYS = 13` 留余量——比
  `start` 早不足 13 天的 `record_date` 一律保留（fail closed），只有超出余量的
  才排除。本数据集上 32 行窗口前 `record_date` 全在 2000–2010，远在余量之外，故
  排除结果与上文数字一致。

