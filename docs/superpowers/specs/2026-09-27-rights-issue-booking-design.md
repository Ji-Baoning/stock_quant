# 持有期回测的配股入账口径 · 设计

- 日期：2026-09-27
- 状态：**复核通过（2026-10-03 owner 裁定：立即实施，与 web 决策层 S0 并行、
  第一优先——它同时是首个正式实验发布与 web 策略层实例验证的前置）**
- 上游：
  [2026-09-03-phase-one-quant-system-design.md](2026-09-03-phase-one-quant-system-design.md)
  §500、§683（配股明确不支持）、
  [ADR-006](../../adr/006-corporate-action-window-scope.md)、
  [ADR-012](../../adr/012-corporate-action-exemption-reads-the-classification.md)
- 关系：本设计**取代** phase-one 设计规格中"配股一律不支持"的实现边界，新增
  ADR-023（编号说明见 §7.1）；不改变数据侧的公司行为仲裁、覆盖判定或验收门槛，
  不改变 execution/cost 语义，不改变 `possible_held_symbols` 的窗口级超集契约。

## 0. 摘要

| 编号 | 现状 | 决策 | 结果 |
| --- | --- | --- | --- |
| C1 | 配股无条件下令中止 run（准备性检查 + 入账两处） | 按全额参与口径入账 | 窗口内 20 条配股不再挡死运行 |
| C2 | 账户只有 `credit_cash`，无扣款原语 | 新增 `Account.debit_cash` | 现金非负不变量不被削弱 |
| C3 | 现金不足以全额认购时无规定 | 按可用现金部分认购，未认购部分计弃配并留痕 | 不透支、不强制卖出、可审计 |
| C4 | 账本只有 `cash_credited` / `shares_added` | 增 `cash_paid`、`rights_entitlement_shares`、`rights_subscribed_shares` | 缴款、应配、认购、放弃四数可独立核对 |

本设计的会计假设与因子侧已有的 total-return 递推一致（§1.3）：**全额参与**，
资金不足时按可用现金部分认购。执行成本与成交仍是 execution 的关注点；本设计
只在公司行为入账路径上补一个此前被显式拒绝的事件类型。

## 1. 已核实的现状

### 1.1 阻塞点

2026-09-27 的工程模式全窗口运行
（`backtest momentum_60d --root project --engineering`，run
`run_02d25d2a101a087176ae5f7f5536e4523b00659e6986512f845e68d56e01d781`）
在 stage `backtest` 失败，唯一原因是：

```
corporate action 000970.SZ#2022-02-24 is a rights issue,
which a holding-period backtest cannot book
```

拒绝出现在两处，文案相同：

- 准备性检查
  [engine.py `_check_actions` → `_require_bookable`](../../../src/stock_quant/backtest/engine.py#L580)，
  遍历 `possible_held_symbols`（"任何订单可能触及"的窗口级超集）；
- 入账路径
  [corporate_actions.py `apply_corporate_action`](../../../src/stock_quant/backtest/corporate_actions.py#L103)，
  在真正持有该证券时触发。

该行为是**已文档化的实现边界**，不是缺陷：phase-one 设计规格 §500 要求
"持仓遇到不支持或冲突行为时停止运行，不静默忽略"，§683 把配股列为
out of scope，并由
[test_backtest_engine.py `test_readiness_rejects_a_rights_issue_on_a_held_name`](../../../tests/integration/test_backtest_engine.py#L994)
钉住。本设计取代的正是这一条边界；其余的停止条件（跨源事实冲突、非
`implemented`、缺股权登记日/除权日）全部保留。

### 1.2 数据事实（CURRENT `99f8ff28…`，只读测量）

| 项 | 测量结果 |
| --- | --- |
| 公司行为总行数 | 6 973（650 只证券） |
| `rights_issue_ratio > 0` | 36 行，**全部** `status=implemented` |
| 配股价 | 36/36 有正 `rights_issue_price`（0 缺失、0 为零） |
| 配股与送转/资本公积/现金分红共现 | **0 行** |
| 配股除权日范围 | 2016-01-07 … 2023-06-27 |
| 规格窗口内（2020-01-01 … 2026-08-27）配股 | **20 行** |
| 其余不可入账类别 | 0：`ex_date` 无空值、`record_date` 无空值、无重复 `(symbol, ex_date)`、无同键异事实组 |

结论：配股是当前数据集上**唯一**残留的中止类别。放行配股之后，准备性检查
在本数据集上不应再产生任何中止项；这也是本设计可验收的前提（§9）。

11 个已发布数据集版本都带有这 36 行，每一份已提交规格的窗口都与其中若干条相交，
因此不存在"换个版本或缩窗口就能绕开"的路径——绕过只会把问题从这一版推到下一版。

### 1.3 因子侧已有口径

[adjusted_bar.py](../../../src/stock_quant/data_model/adjusted_bar.py#L1) 的
total-return 递推为

```
TR_t = TR_{t-1} · (P_t · (1 + b + c + r) + d − r · s) / P_{t-1}
```

在理论除权价 TERP = `(P_{t-1} + r · s) / (1 + r)` 处权益守恒。这等价于
**假定配股全额参与**：股东按比例缴款 `r · s` 换取 `r` 股。本设计的入账口径
沿用同一假设，使回测的权益路径与因子侧的收益基准不产生口径分裂。

（该递推自身的口径登记仍属"尚未记录"，见 §7.2；本设计只是与之一致，不改它。）

### 1.4 准备性检查的真实语义

[`possible_held_symbols`](../../../src/stock_quant/backtest/engine.py#L617)
是**窗口级可能持有超集**：schedule 模式下取冻结计划触及的 symbol 并集，
provider 模式下读调用方声明的窗口级超集。它是公司行为的覆盖域，不是实际持仓
集合。因此现状会出现真实的假阳性：窗口内的 20 件配股中，有 5 件落在**曾进入目标
组合**的证券上，而这 5 件**没有一件真正落在除权日的目标持仓上**（例如
`000970.SZ` 首次进入目标组合是 2022-07-22，晚于其 2022-02-24 除权日），但整条
run 仍被挡死。

本设计保留该超集语义（它是既有契约，收窄它需要预跑一次持仓重放），改为让配股
成为**可入账**事件：真正持有时落账，未持有时入账路径本来就是 no-op。

## 2. 会计口径（已定）

对一条 `rights_issue_ratio = r > 0`、配股价 `s > 0` 的已实现配股，在除权日
`ex_date`：

| 量 | 定义 | 取整 |
| --- | --- | --- |
| 应配股数 `entitlement` | `round(除权日持仓股数 × r)` | 整数，`ROUND_HALF_UP` |
| 可认购现金 `budget` | 除权日**开盘前**账户现金（`_apply_actions` 当日先于任何成交） | — |
| 认购股数 `subscribed` | `min(entitlement, floor(budget / s))` | 向下取整，保证不超支 |
| 缴款 `cash_paid` | `subscribed × s` | `CENT`，`ROUND_HALF_UP` |
| 弃配股数 | `entitlement − subscribed` | 整数（导出量，不单独入账本） |

三条硬约束：

1. **全额参与是默认**：只要现金允许就认购全部应配股数；只有现金不足才部分认购。
2. **现金永不为负**：`subscribed` 的 floor 定义保证 `budget − cash_paid ≥ 0`。
   不设透支原语、不触发强制卖出、不预留缓冲。
3. **弃配必须留痕**：`entitlement − subscribed > 0` 时写入账本，使"约束是否
   真正binding、binding 了多久、在哪些事件上"在首次全窗口运行后即可测量。

取整规则与既有 `_bonus_shares` 同规（整数 `ROUND_HALF_UP`），避免同一除权日
出现两种股数口径。

同一条事件若同时带现金分红与配股（当前数据集共现数为 0，属理论情形），次序为
**先入分红、后算预算**：分红现金参与除权日开盘点前的可用现金，因此可提高认购
能力。这与 §3 的入账次序一致，且对"全额参与优先"的假设没有例外。

`subscribed = 0`（现金不足一手配股价）时仍然落一条账本记录：事件发生了、持仓
应得、我们没认购——这是决策证据，不是可省略的噪声。

## 3. 入账路径

[`apply_corporate_action`](../../../src/stock_quant/backtest/corporate_actions.py#L71)
的既有次序与守卫全部保留，仅在"送转股之后"追加配股一步：

1. 未持有该证券 → `None`（no-op，不变）；
2. `status != implemented` → `UnsupportedCorporateAction`（不变）；
3. 缺 `record_date` / `ex_date` → `UnsupportedCorporateAction`（不变）；
4. **`r > 0` 且配股价缺失或 `≤ 0` → `UnsupportedCorporateAction`**，文案改为
   "配股缺少有效的配股价，无法计算缴款"（fail-closed 不丢：无法计算成本的事件
   仍然中止 run，不猜价、不按零价入账）；
5. action_id 已入账 → `None`（幂等，不变）；
6. 现金分红 → `credit_cash`（不变）；
7. 送转/资本公积 → `increase_position`（不变）；
8. **配股**：先 `debit_cash(cash_paid)`，再 `increase_position(symbol, subscribed,
   buy_date=ex_date, fill_id=action_id)`；`subscribed = 0` 时跳过两者，但仍落账本。

股份的 T+1 可用日沿用 `increase_position` 在 `buy_date=ex_date` 上的既有规则，
与送转股一致；认购所得股份与送转股在可用性上没有差别。

## 4. 账户原语

[account.py](../../../src/stock_quant/backtest/account.py#L182) 新增一个与
`credit_cash` 对称的扣款原语：

```python
def debit_cash(self, amount: object, *, note: str = "") -> None:
    """Debit cash for a non-exchange corporate-action payment (e.g. a rights
    subscription).  Refuses a non-positive amount and any debit that would
    draw the balance below zero."""
```

- `amount ≤ 0` → `ValueError`（与 `credit_cash` 的 `<= 0` 校验对称）；
- `amount > self.cash` → `CashShortfallError`，**不做部分扣款**（部分认购是在
  §2 算好股数之后的一次整额扣款，原语本身保持"全或无"）；
- 成功时追加一条 `kind="debit"` 的现金账本记录，`amount` 与 `balance` 均为
  正语义（`balance` 是扣后余额）。

现金账本的 `kind` 目前有 `initial` / `credit` / `fill`，新增 `debit` 是纯追加，
既有读取方（`_derive_cash_ledger` 汇总、报表现金段）不受影响。

## 5. 准备性检查

[`_require_bookable`](../../../src/stock_quant/backtest/engine.py#L560) 的配股
分支由"一律拒绝"改为"必须能给价"：

```python
if row.get("rights_issue_ratio", 0) > 0 and not _positive(row["rights_issue_price"]):
    raise UnsupportedCorporateAction(
        f"corporate action {action_id} is a rights issue without a usable "
        "subscription price"
    )
```

保留不动：跨源事实冲突、非 `implemented`、缺日期、以及 `unkeyed_actions`
（缺除权日无法定键）在可能持有集合上仍然中止。`_validate_bars` 的 docstring
里"non-rights"的措辞同步更新，并写明**检查域是窗口级可能持有超集、配股现已
可入账**：超集的作用是保证任何可能被持有的名字其事件都可判读，而不是要求
每个事件都实际发生。

## 6. 账本、重放与报表

### 6.1 账本字段

[`CorporateActionLedgerEntry`](../../../src/stock_quant/backtest/models.py#L359)
新增三个原子字段（各自独立取值，弃配为导出量）：

| 字段 | 类型 | 非配股事件 | 不变量 |
| --- | --- | --- | --- |
| `cash_paid` | `Decimal` | `0` | `≥ 0`，CENT 量化 |
| `rights_entitlement_shares` | `int` | `0` | `≥ 0` |
| `rights_subscribed_shares` | `int` | `0` | `≥ 0`，`≤ entitlement` |

`cash_credited` 保持原义（现金分红入账，非负）；`shares_added` 保持
"本次事件新增的全部股数"，docstring 由"送转/资本公积"扩展为"送转、资本公积与
配股认购之和"。`ACTION_LEDGER_COLUMNS`
（[engine.py:93](../../../src/stock_quant/backtest/engine.py#L93)）与
`_action_ledger_frame` 追加同名列，`shares_added` 继续强制 `int64`，三个新列
在空表上也必须存在（`DataFrame(rows, columns=...)` 已保证）。

### 6.2 重放对账（必改的耦合点）

[runner.py `_derive_cash_ledger`](../../../src/stock_quant/research/runner.py#L3312)
会按 `seq` 重放 `action_ledger.parquet` 重建现金账本，并与场景期末现金按
`1e-3` 对账。它目前只处理 `cash_credited` 与 `shares_added`，因此必须同步：

该分支现有形状是

```python
amount = Decimal(str(record.get("cash_credited") or 0))
shares = int(record.get("shares_added") or 0)
action_id = str(record.get("action_id") or "")
note = str(record.get("note") or "") or f"corporate action {day}"
if amount > 0:
    account.credit_cash(amount, note=note)
if shares > 0 and action_id:
    account.increase_position(...)
```

在 `credit_cash` 与 `increase_position` 之间插入缴款，并在读取 `cash_paid` 时
做一次自校验：

```python
paid = Decimal(str(record.get("cash_paid") or 0))
subscribed = int(record.get("rights_subscribed_shares") or 0)
if subscribed > shares:
    raise ValueError(
        f"action ledger row {action_id} subscribes {subscribed} shares but "
        f"adds only {shares}"
    )
if paid > 0:
    account.debit_cash(paid, note=note)
```

（`shares_added` 已含认购股数，故 `increase_position` 一行不变。）

读取一律走 `.get(...) or 0`：旧 run 的 parquet 没有新列，读取端必须仍能重放
（旧 run 不含配股入账，因此对账结果不变）。`subscribed` 仅用于自校验断言
（`shares_added ≥ subscribed`），不参与金额推导。

### 6.3 报表

[reporting/html.py `_action_rows`](../../../src/stock_quant/reporting/html.py#L738)
在公司行为流水表追加四列——**认购缴款 / 应配股数 / 认购股数 / 放弃股数**，
取值来自三个新字段（放弃 = 应配 − 认购，缺列时为 `—`）。既有"现金入账"列
继续显示 `cash_credited`，缴款不与之混列，因此报表不会出现语义不明的负数。

## 7. 治理

### 7.1 新增 ADR-023

新建 `docs/adr/023-rights-issue-booking.md`（accepted），记录：全额参与口径与
`adjusted_bar` 递推的一致性、按可用现金部分认购、弃配留痕、`debit_cash` 的
非负不变量、以及"其余复杂行为（吸收合并、换股、非 `implemented`、跨源冲突、
缺关键日期）继续 fail-closed"的边界。同时在
[DECISIONS_INDEX.md](../../adr/DECISIONS_INDEX.md) 登记。

**编号说明**：树内 `020` 被两个 ADR 同时占用（
`020-batched-validation-channel.md` 与
`020-suspension-proof-grid-is-the-fetch-window-and-its-seam.md`）。
`021`、`022` 已被在途设计
[2026-09-27-raw-snapshot-reuse-recovery-design.md](2026-09-27-raw-snapshot-reuse-recovery-design.md)
§6.1 预留给"proof-grid 改为 021、该设计自身登记为 022"。本设计不触碰该编号
冲突，取 **023**；索引中 021/022 留空由那份设计补齐。

> 勘误（2026-10-03）：登记时 023（suspension carry-forward）与 024（membership
> slice hash）已被占用，本设计实际登记为 **ADR-025**；上文关于 021/022 预留的
> 说明已由索引现状（021 常驻查询面、022 panda graft 均已登记）解决。

### 7.2 不改动但需标注

`adjusted_bar` 的 total-return 口径仍挂在索引的"尚未记录"清单里。本 ADR 只
*引用*该口径作为一致性依据，不宣称已登记它；把该口径本身补成 ADR 是独立事项。

### 7.3 phase-one 设计规格的修订点

- §500 的"配股、吸收合并和换股等复杂行为标记 `unsupported_corporate_action`"
  改为：配股按 ADR-023 入账，其余复杂行为继续停止运行不静默忽略；
- §683 的"配股 out of scope"改为指向 ADR-023。

规格与实现不得长期互相矛盾；修订只动这两处边界表述，不重写规格其余部分。

## 8. 测试

### 8.1 单元（`tests/unit/test_corporate_action_accounting.py`）

1. 全额认购：现金充足 → `subscribed == entitlement`，现金减少恰好 `subscribed × s`，
   持仓增加 `subscribed`，账本三字段与弃配为 0；
2. 部分认购：现金只够一部分 → 只认购能被现金覆盖的整股数，弃配 = 应配 − 认购，
   扣款后现金 ≥ 0 且 `< s`（再多一股就超支）；
3. 现金不足一手 → `subscribed == 0`，账本仍落一条，现金分文不动；
4. 配股价缺失或为 0（`r > 0`）→ 抛 `UnsupportedCorporateAction`；
5. 未持有 → no-op（返回 `None`，账本不增行）；
6. 幂等：重复应用同一 action → 第二次返回 `None`，现金与持仓不变；
7. T+1 可用日：认购所得股份在 `ex_date` 当日不可卖、次一开放日可卖；
8. 权益守恒（**合成价格**用例，不用真实行情）：令除权日收盘价恰好等于理论除权价
   TERP = `(P_{t-1} + r · s) / (1 + r)`，则全额认购并缴款后，`ex_date` 当日总权益
   与除权前一日按 `P_{t-1}` 的估值精确相等 —— 这正是 `adjusted_bar` 递推所依据的
   恒等式，用真实收盘价断言会引入市场噪声，故必须自行造价；
9. `debit_cash` 自身：非正额抛 `ValueError`，超额抛 `CashShortfallError`，成功时
   现金账本追加 `kind="debit"` 记录。

### 8.2 集成（`tests/integration/test_backtest_engine.py`）

- 替换 `test_readiness_rejects_a_rights_issue_on_a_held_name`：同一条配股事件
  改为通过准备性检查、并在持有该证券的场景里落账（沿用该测试的 fixture 形状，
  断言账本行与现金变化）；
- 新增：`r > 0` 但无有效配股价时准备性检查仍然中止（"缺价 fail-closed"）；
- 新增：配股落在可能持有超集内但账户未持有 → run 正常完成、`action_ledger` 无该行
  （钉住假阳性已消除）；
- 冲突/非 implemented/缺日期三类拒绝各保留至少一个既有覆盖，确认未被本次改动削弱。

列集合是"精确相等"断言的既有测试必须同步放宽或补列，否则会因新增列假红：
`test_backtest_engine.py` 的 `assert_frame_equal` 黄金账本（约 :741）与
`test_golden_corporate_action_ledger_records_one_entry_per_action_id`（约 :753，
逐条比对 `to_dict("records")`）。两处都按新列补全期望值，不删除断言。

### 8.3 回归

`tests/integration/test_reports.py` 的账本渲染测试需补新列（表头与一行的
缴款/应配/认购/放弃），并保留一个"旧 parquet 无新列"的兼容断言（缺列渲染 `—`
而非抛错）。

## 9. 验收与范围

验收顺序：

```bash
pytest tests/unit/test_corporate_action_accounting.py tests/integration/test_backtest_engine.py -q
pytest tests/integration/test_reports.py -q
# 全窗口工程诊断（落 data/runs/debug），再出报告
python -m stock_quant backtest momentum_60d --root project --engineering
python -m stock_quant report build --root project --debug
```

通过的判据：全窗口运行不再因公司行为中止；`action_ledger` 中每条配股行三字段
自洽（`subscribed ≤ entitlement`，`cash_paid == subscribed × s`，弃配可导出）；
`_derive_cash_ledger` 对账通过；报告的公司行为表显示缴款与弃配。报告仍带
`UNTRUSTED` + ENGINEERING 横幅（工程模式不因本设计变得更可信）。

**明确不做**：

- 不实现配股权的二级市场交易或弃配罚没；
- 不实现强制卖出、透支或任何现金缓冲；
- 不改变数据侧（仲裁、覆盖、验收闸门、`corporate_action.parquet` 的任何列）；
- 不改变 `possible_held_symbols` 的窗口级超集契约；
- 不重蹈"批量确认"路线（`2026-09-13-acceptance-standing-worksheets-design.md`
  §非目标）；
- 不 mechanise 交易所日历 / 交易规则 / 跨源价格三项人工验收。

## 10. 风险与边界

| 风险 | 处理 |
| --- | --- |
| 部分认购引入"本可全额参与"的回测偏差 | 弃配逐事件留痕，首次全窗口运行后即可测量 binding 频率；若某个场景普遍 binding，是资金规则问题（例如权重满仓），不是本口径问题 |
| 账本新增列影响已发布 run 的可读性 | 读取端一律 `.get()`，缺列为 0 / `—`；旧 run 无配股入账，重放对账不变 |
| 超集检查被误读为"已放宽" | §5 明确：只放行可给价的配股，其余中止条件一个不少，并在 docstring 与 ADR-023 写明检查域语义 |
| 与因子侧口径分裂 | §1.3 的一致性论证写进 ADR-023；TERP 守恒有单元断言（§8.1.8） |
