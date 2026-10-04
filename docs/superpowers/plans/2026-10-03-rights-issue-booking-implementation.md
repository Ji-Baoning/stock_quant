# 配股入账（rights-issue booking）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按已复核设计把已实现配股从"无条件中止 run"改为"全额参与口径入账"：`Account.debit_cash` 原语、账本三个新原子字段、`apply_corporate_action` 的缴款/认购/弃配入账、准备性检查只放行"可给价"的配股、`_derive_cash_ledger` 重放同步、报表四列、ADR-025 治理登记——使全窗口工程诊断不再因公司行为中止，为首个正式实验发布扫清唯一障碍（owner 裁定 6：第一优先）。

**Architecture:** 全部改动落在既有入账路径上，不新增模块：数据侧（仲裁、覆盖、验收闸门、`corporate_action.parquet`）零改动；`possible_held_symbols` 窗口级超集契约不动；会计口径 = 设计 §2（应配 `ROUND_HALF_UP`、认购 `floor(预算/配股价)`、缴款 CENT 量化、同事件先分红后算预算、`subscribed = 0` 仍落账本）；其余 fail-closed 条件（跨源冲突、非 implemented、缺日期、缺配股价）一个不少。

**Tech Stack:** 纯 Python 既有栈（pytest/pandas/Decimal）；不新增任何依赖；不动 `web/`。

**Spec:** [docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md](../specs/2026-09-27-rights-issue-booking-design.md)（已复核通过，2026-10-03 owner 裁定立即实施）§2–§9 全部。**一处事实修正**：设计 §7.1 写"新 ADR 取 023"，但 023（suspension carry-forward，2026-10-02）与 024（membership slice hash，2026-10-02）现已被占用——本计划取 **ADR-025**（见 Task 7；设计文档加一行勘误）。

## Global Constraints

- **会计硬约束（设计 §2，违反即缺陷）**：全额参与默认；现金永不为负（floor 认购 + `debit_cash` 全或无）；弃配逐事件留痕（`entitlement − subscribed > 0` 可从账本导出）；取整 = 应配 `ROUND_HALF_UP` 整数、缴款 CENT `ROUND_HALF_UP`（与 `_bonus_shares` 同规）。
- **fail-closed 边界不回撤**：跨源事实冲突、非 `implemented`、缺 `record_date`/`ex_date`、`unkeyed_actions`、**`r > 0` 且配股价缺失或 `≤ 0`** 五类继续中止；不猜价、不按零价入账。
- **数据侧零改动**：不改 `src/stock_quant/data_model/corporate_actions.py`（数据侧仲裁）、不改 `corporate_action.parquet` 任何列、不改覆盖判定与验收门槛。
- **旧 run 兼容**：所有读取端一律 `.get(...) or 0` / `.get()`（缺列 → 0 或 `—`）；旧 parquet 无新列必须仍能重放且对账结果不变。
- **明确不做（设计 §9）**：配股权二级交易、弃配罚没、强制卖出、透支、现金缓冲、批量确认路线、机械化三项人工验收。
- 工程模式产物仍带 UNTRUSTED + ENGINEERING 横幅——本设计不提高其可信等级。
- 每任务至少一个独立提交，提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`；开工先跑 `git status` 保护在途 WIP；只 `git add` 本任务文件。
- Python 测试命令在仓库根执行（conda env `stock-quant`；`environment.yml` 现为 **python=3.12 + pandas>=3**，2026-09-26 起，别再按 3.10 约定跑——pandas 3 的 dtype/空值行为与 2.x 不同，黄金帧断言必须以该解释器为准）。

## 开工前必须知道的实现形态（2026-10-03 实查）

1. **改动点精确位置**：
   - `src/stock_quant/backtest/account.py`：`credit_cash` 在 :182（新原语紧跟其后）；`CashShortfallError` 已存在（:44）；`_append_cash(kind, amount, balance, note)` 已存在。
   - `src/stock_quant/backtest/corporate_actions.py`：无条件拒绝在 :103-107；入账构造在 :137；模块 docstring :14-17 提到 rights 一律 raise（需同步改写）。
   - `src/stock_quant/backtest/models.py`：`CorporateActionLedgerEntry` 在 :359，`__post_init__` 已校验 `cash_credited`/`shares_added`；`CashLedgerEntry` 的 `kind` 现有 `initial`/`credit`/`fill`。
   - `src/stock_quant/backtest/engine.py`：`ACTION_LEDGER_COLUMNS` :93；`_action_ledger_frame` :450（`DataFrame(rows, columns=...)` 已保证空表列存在）；`_require_bookable` 配股分支 :580-584；`_validate_bars` docstring :485-496（"non-rights" 措辞在 :492）。
   - `src/stock_quant/research/runner.py`：`_derive_cash_ledger` :3382，重放分支在读到这段（`amount`/`shares`/`action_id`/`note` 四个局部量之后、`if amount > 0: credit_cash` 之前插入缴款）。
   - `src/stock_quant/reporting/html.py`：`_action_rows` :739；`_money(None)`/`_int_text(None)` 天然渲染 `"—"`——旧 parquet 缺列兼容不需要额外分支。
2. **既有测试事实**：`tests/unit/test_corporate_action_accounting.py` 有 `_account(initial_cash, lots)` 与 `implemented_action(..., rights_ratio=…)` 帮助函数（`rights_issue_price` 恒为 `None`，新测试直接 `action["rights_issue_price"] = …` 覆盖）；`tests/integration/test_backtest_engine.py:994` 是要替换的拒绝测试（IOTA 在 `_MARKET.days[52]` 的除权日、当时确被持有）；黄金账本两处（`assert_frame_equal` 约 :735-742 与逐条 `to_dict("records")` 约 :753 起）随新列补期望；fixture 中 KAPPA（600010.SH，2020-03-05 除权）是"从未持有"的名字（:262 注释）。
3. **`_derive_cash_ledger` 不使用 `self`**（只消费参数）——测试用 `ResearchRunner.__new__(ResearchRunner)` 绕过重型构造器直调。
4. **TERP 守恒用例的造价选择**（设计 §8.1.8 要求合成价格）：取 `P=9.10`、`r=0.3`、`s=13.00` → `TERP=(9.10+3.90)/1.3=10.00` 精确有限小数，100 股 → 应配 30、缴款 390.00，权益差恰为 0（Decimal 精确相等，无舍入噪声）。
5. **ADR 编号**：023/024 已占用（见 Spec 修正），新 ADR = **025**；`DECISIONS_INDEX.md` 是"newest concerns first"表格，行格式 `[NNN 标题](NNN-slug.md) | accepted | 日期 | 路径 | 关键词 | Read when`。
6. **phase-one 规格**：§500 原文"配股、吸收合并和换股等复杂行为标记\`unsupported_corporate_action\`…"；§683（"明确不做"清单）含"配股、合并、换股等复杂公司行为；"一行。两处按设计 §7.3 修订。

## 文件结构

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `src/stock_quant/backtest/account.py` | 修改 | `debit_cash` 原语 |
| `src/stock_quant/backtest/models.py` | 修改 | `CorporateActionLedgerEntry` 三新字段 + 不变量 |
| `src/stock_quant/backtest/engine.py` | 修改 | 列契约 + `_action_ledger_frame` + `_require_bookable` + docstring |
| `src/stock_quant/backtest/corporate_actions.py` | 修改 | 配股入账主体 + 模块 docstring |
| `src/stock_quant/research/runner.py` | 修改 | `_derive_cash_ledger` 重放缴款 |
| `src/stock_quant/reporting/html.py` | 修改 | `_action_rows` 四列 |
| `tests/unit/test_corporate_action_accounting.py` | 修改 | 单元测试（替换 + 新增） |
| `tests/integration/test_backtest_engine.py` | 修改 | 替换 :994 测试、新增三测试、黄金期望补列 |
| `tests/integration/test_research_runner.py` | 修改 | 新增重放测试 |
| `tests/integration/test_reports.py` | 修改 | 账本渲染补列 + 旧列兼容断言 |
| `docs/adr/025-rights-issue-booking.md` | 新建 | ADR-025 |
| `docs/adr/DECISIONS_INDEX.md` | 修改 | 登记 025 |
| `docs/superpowers/specs/2026-09-03-phase-one-quant-system-design.md` | 修改 | §500/§683 两处边界表述 |
| `docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md` | 修改 | §7.1 编号勘误一行 |

---

### Task 1: `Account.debit_cash` 扣款原语

**Files:**
- Modify: `src/stock_quant/backtest/account.py:189`（紧跟 `credit_cash` 之后）
- Test: `tests/unit/test_corporate_action_accounting.py`（追加）

**Interfaces:**
- Produces: `Account.debit_cash(amount: object, *, note: str = "") -> None`；`amount ≤ 0` → `ValueError`；`amount > cash` → `CashShortfallError`（**全或无**，不做部分扣款）；成功追加 `kind="debit"` 现金账本记录（`amount`/`balance` 均正语义，`balance` 为扣后余额）。Task 3 消费。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_corporate_action_accounting.py` 追加（import 区补 `from stock_quant.backtest.account import CashShortfallError`）：

```python
# --------------------------------------------------------------------------- #
# Rights-issue debit primitive (ADR-025)
# --------------------------------------------------------------------------- #


def test_debit_cash_appends_a_debit_ledger_entry(account_with_record_date_holding):
    account = account_with_record_date_holding
    account.debit_cash(Decimal("100.00"), note="rights 600000.SH")
    assert account.cash == Decimal("9900.00")
    entry = account.cash_ledger[-1]
    assert entry.kind == "debit"
    assert entry.amount == Decimal("100.00")
    assert entry.balance == Decimal("9900.00")


def test_debit_cash_refuses_non_positive_amount(account_with_record_date_holding):
    with pytest.raises(ValueError):
        account_with_record_date_holding.debit_cash(0)
    with pytest.raises(ValueError):
        account_with_record_date_holding.debit_cash(Decimal("-1.00"))


def test_debit_cash_refuses_overdraft_all_or_nothing(account_with_record_date_holding):
    account = account_with_record_date_holding
    with pytest.raises(CashShortfallError):
        account.debit_cash(Decimal("10000.01"))
    assert account.cash == Decimal("10000.00")  # 全或无：失败不改变余额
    assert all(entry.kind != "debit" for entry in account.cash_ledger)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/unit/test_corporate_action_accounting.py -q -k debit_cash`
Expected: FAIL——`AttributeError: 'Account' object has no attribute 'debit_cash'`。

- [ ] **Step 3: 实现原语**

在 `account.py` 的 `credit_cash` 方法之后插入：

```python
    def debit_cash(self, amount: object, *, note: str = "") -> None:
        """Debit cash for a non-exchange corporate-action payment (e.g. a
        rights subscription).  Refuses a non-positive amount and any debit
        that would draw the balance below zero; the debit is all-or-nothing."""
        cash = as_decimal(amount)
        if cash <= 0:
            raise ValueError(f"debit amount must be positive: {cash}")
        if cash > self._cash:
            raise CashShortfallError(
                f"debit {cash} exceeds cash balance {self._cash}"
            )
        balance = self._cash - cash
        self._cash = balance
        self._append_cash("debit", cash, balance, note or "cash debit")
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/unit/test_corporate_action_accounting.py tests/unit/test_account.py -q`
Expected: PASS（含既有 account 测试全绿）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/backtest/account.py tests/unit/test_corporate_action_accounting.py
git commit -m "feat(backtest): add Account.debit_cash all-or-nothing primitive

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: 账本三新原子字段与列契约

**Files:**
- Modify: `src/stock_quant/backtest/models.py:359`（`CorporateActionLedgerEntry`）
- Modify: `src/stock_quant/backtest/engine.py:93`（`ACTION_LEDGER_COLUMNS`）、`:450`（`_action_ledger_frame`）
- Test: `tests/unit/test_corporate_action_accounting.py`（追加）
- Test: `tests/integration/test_backtest_engine.py`（黄金期望补列）

**Interfaces:**
- Produces: `CorporateActionLedgerEntry.cash_paid: Decimal = Decimal("0")`、`.rights_entitlement_shares: int = 0`、`.rights_subscribed_shares: int = 0`（带默认值：既有构造点 `corporate_actions.py:137` 在 Task 3 之前无需改动即可通过）；`__post_init__` 新增不变量：`cash_paid ≥ 0`、两股数字段为非负 int 且 `0 ≤ subscribed ≤ entitlement`。列序：`seq, action_id, symbol, ex_date, record_date, cash_credited, shares_added, cash_paid, rights_entitlement_shares, rights_subscribed_shares, note`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_corporate_action_accounting.py` 追加（import 区补 `CorporateActionLedgerEntry`、`from stock_quant.backtest.engine import ACTION_LEDGER_COLUMNS, BacktestEngine`）：

```python
def test_ledger_entry_new_fields_default_to_zero():
    entry = CorporateActionLedgerEntry(
        seq=0,
        action_id="600000.SH#2020-01-07",
        symbol="600000.SH",
        ex_date=date(2020, 1, 7),
        record_date=date(2020, 1, 6),
        cash_credited=Decimal("10.00"),
        shares_added=0,
    )
    assert entry.cash_paid == Decimal("0")
    assert entry.rights_entitlement_shares == 0
    assert entry.rights_subscribed_shares == 0


def test_ledger_entry_rejects_subscribed_above_entitlement():
    with pytest.raises(ValueError):
        CorporateActionLedgerEntry(
            seq=0,
            action_id="600000.SH#2020-01-07",
            symbol="600000.SH",
            ex_date=date(2020, 1, 7),
            record_date=date(2020, 1, 6),
            cash_credited=Decimal("0"),
            shares_added=30,
            cash_paid=Decimal("390.00"),
            rights_entitlement_shares=30,
            rights_subscribed_shares=31,
        )


def test_action_ledger_frame_carries_new_columns_even_when_empty():
    frame = BacktestEngine._action_ledger_frame([])
    assert list(frame.columns) == list(ACTION_LEDGER_COLUMNS)
    for column in ("cash_paid", "rights_entitlement_shares", "rights_subscribed_shares"):
        assert column in frame.columns
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/unit/test_corporate_action_accounting.py -q -k "ledger_entry or carries_new_columns"`
Expected: FAIL——`TypeError: unexpected keyword argument 'cash_paid'` / 列缺失。

- [ ] **Step 3: 实现字段与列**

`models.py`：`CorporateActionLedgerEntry` 的 `shares_added: int` 之后、`note: str = ""` 之前追加三个带默认值字段；docstring 的 `shares_added` 描述扩为"送转、资本公积与配股认购之和"；`__post_init__` 末尾追加：

```python
        if self.cash_paid < 0:
            raise ValueError(f"cash_paid must be non-negative: {self.cash_paid}")
        for name in ("rights_entitlement_shares", "rights_subscribed_shares"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {value!r}")
            if value < 0:
                raise ValueError(f"{name} must be non-negative: {value}")
        if self.rights_subscribed_shares > self.rights_entitlement_shares:
            raise ValueError(
                f"rights_subscribed_shares must not exceed entitlement "
                f"({self.rights_entitlement_shares}): {self.rights_subscribed_shares}"
            )
```

`engine.py` `ACTION_LEDGER_COLUMNS` 在 `"shares_added"` 与 `"note"` 之间插入三列；`_action_ledger_frame` 的 row dict 对应追加：

```python
                "cash_paid": float(entry.cash_paid),
                "rights_entitlement_shares": int(entry.rights_entitlement_shares),
                "rights_subscribed_shares": int(entry.rights_subscribed_shares),
```

- [ ] **Step 4: 黄金账本期望补列（不删断言）**

`tests/integration/test_backtest_engine.py`：`_reference_run` 的 action-ledger 黄金帧构造处为每行补 `"cash_paid": 0.0, "rights_entitlement_shares": 0, "rights_subscribed_shares": 0`（fixture 无配股事件，值全 0）；`test_golden_corporate_action_ledger_records_one_entry_per_action_id` 的每个期望 dict 同样补这三个键（`0.0`/`0`/`0`）。

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/unit/test_corporate_action_accounting.py tests/integration/test_backtest_engine.py -q`
Expected: PASS（黄金断言按新列补全后逐字节相等；本任务未改任何行为，fixture 无配股）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/backtest/models.py src/stock_quant/backtest/engine.py tests/unit/test_corporate_action_accounting.py tests/integration/test_backtest_engine.py
git commit -m "feat(backtest): add rights-issue atomic fields to the action ledger

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: `apply_corporate_action` 配股入账

**Files:**
- Modify: `src/stock_quant/backtest/corporate_actions.py`（:103-107 拒绝分支、:109-148 入账主体、模块 docstring）
- Test: `tests/unit/test_corporate_action_accounting.py`（替换 `test_rights_issue_on_a_held_name_raises_unsupported` 为缺价新测试 + 新增行为测试）

**Interfaces:**
- Consumes: Task 1 `debit_cash`、Task 2 三字段。
- Produces: 入账语义（设计 §2/§3）：`r > 0` 且配股价缺失/`≤ 0` → `UnsupportedCorporateAction`（新文案）；否则在分红入账**之后**按当时现金预算计算 `subscribed = min(entitlement, floor(budget / s))`，`subscribed > 0` 时先 `debit_cash(cash_paid)` 再 `increase_position(symbol, subscribed, buy_date=ex_date, fill_id=action_id)`；`shares_added` = 送转 + 认购；`subscribed = 0` 仍落账本；应配基数 = 事件前持仓（与 `adjusted_bar` 递推的 `P_{t-1}` 基数一致）。

- [ ] **Step 1: 写失败测试**

替换 `test_rights_issue_on_a_held_name_raises_unsupported` 并追加（import 区补 `ROUND_FLOOR`：`from decimal import ROUND_FLOOR, ROUND_HALF_UP`）：

```python
def test_a_priced_rights_issue_without_a_usable_price_raises_unsupported(
    account_with_record_date_holding,
):
    action = implemented_action(
        rights_ratio=0.3,
        cash_per_share=0.0,
        bonus_ratio=0.0,
    )
    # rights_issue_price 保持 None：r>0 且无有效价 → fail closed（ADR-025）
    with pytest.raises(UnsupportedCorporateAction, match="without a usable"):
        apply_corporate_action(account_with_record_date_holding, action)
    assert account_with_record_date_holding.action_ledger == ()


def test_zero_subscription_price_with_ratio_raises_unsupported(
    account_with_record_date_holding,
):
    action = implemented_action(rights_ratio=0.3, cash_per_share=0.0, bonus_ratio=0.0)
    action["rights_issue_price"] = 0.0
    with pytest.raises(UnsupportedCorporateAction, match="without a usable"):
        apply_corporate_action(account_with_record_date_holding, action)


def _rights_action(ratio: float, price: float) -> dict:
    action = implemented_action(rights_ratio=ratio, cash_per_share=0.0, bonus_ratio=0.0)
    action["rights_issue_price"] = price
    return action


def test_full_rights_subscription_conserves_equity_at_terp(
    account_with_record_date_holding,
):
    # 合成价格（§8.1.8）：P=9.10、r=0.3、s=13.00 → TERP=10.00 精确有限小数。
    p_prev, price = Decimal("9.10"), Decimal("13.00")
    account = account_with_record_date_holding  # 100 股、现金 10000.00
    apply_corporate_action(account, _rights_action(0.3, 13.0))
    entry = account.action_ledger[-1]
    assert entry.rights_entitlement_shares == 30
    assert entry.rights_subscribed_shares == 30
    assert entry.cash_paid == Decimal("390.00")
    assert entry.shares_added == 30
    terp = (p_prev + Decimal("0.3") * price) / (Decimal("1.3"))
    assert terp == Decimal("10.00")
    pre = Decimal("10000.00") + Decimal(100) * p_prev
    post = account.cash + Decimal(account.position_quantity("600000.SH")) * terp
    assert post == pre


def test_partial_subscription_bounded_by_available_cash():
    account = _account("200", lots=[("600000.SH", 100, date(2020, 1, 2))])
    apply_corporate_action(account, _rights_action(0.3, 13.0))
    entry = account.action_ledger[-1]
    assert entry.rights_entitlement_shares == 30
    assert entry.rights_subscribed_shares == 15  # floor(200/13)
    assert entry.cash_paid == Decimal("195.00")
    assert account.cash == Decimal("5.00")  # 再多一股就超支
    assert account.position_quantity("600000.SH") == 115


def test_zero_subscription_still_records_the_entry():
    account = _account("0", lots=[("600000.SH", 100, date(2020, 1, 2))])
    apply_corporate_action(account, _rights_action(0.3, 13.0))
    entry = account.action_ledger[-1]
    assert entry.rights_entitlement_shares == 30
    assert entry.rights_subscribed_shares == 0
    assert entry.cash_paid == Decimal("0")
    assert account.cash == Decimal("0")
    assert account.position_quantity("600000.SH") == 100


def test_subscription_shares_follow_the_t_plus_one_rule():
    account = _account("10000", lots=[("600000.SH", 100, date(2020, 1, 2))])
    apply_corporate_action(account, _rights_action(0.3, 13.0))
    # ex_date=2020-01-07；_OPEN_DAYS 的次一开放日是 2020-01-08。
    open_entry = account.position_ledger[-1]
    assert open_entry.kind == "OPEN"
    assert open_entry.quantity == 30
    assert open_entry.available_date == date(2020, 1, 8)


def test_same_event_dividend_credits_before_subscription_budget():
    # 分红先入账、后算预算：现金分红提高认购能力（§2 共现次序）。
    account = _account("300", lots=[("600000.SH", 100, date(2020, 1, 2))])
    action = _rights_action(0.3, 13.0)
    action["cash_dividend_per_share"] = 1.0  # 100 股 → 分红 100 → 预算 400
    apply_corporate_action(account, action)
    entry = account.action_ledger[-1]
    assert entry.cash_credited == Decimal("100.00")
    assert entry.rights_subscribed_shares == 30  # floor(400/13)=30=应配 → 全额
    assert account.cash == Decimal("10.00")


def test_idempotent_reapplication_of_a_priced_rights_issue():
    account = _account("10000", lots=[("600000.SH", 100, date(2020, 1, 2))])
    action = _rights_action(0.3, 13.0)
    first = apply_corporate_action(account, action)
    cash_after_first = account.cash
    assert first is not None
    assert apply_corporate_action(account, action) is None
    assert account.cash == cash_after_first
    assert len(account.action_ledger) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/unit/test_corporate_action_accounting.py -q -k "rights or subscription"`
Expected: FAIL——现状对一切 `r>0` 抛 `UnsupportedCorporateAction`（TERP/部分认购/零认购用例全红）。

- [ ] **Step 3: 实现入账**

`corporate_actions.py` 三处修改：

(a) `from decimal import ROUND_FLOOR, ROUND_HALF_UP, …`（import 区补 `ROUND_FLOOR`）。

(b) :103-107 的拒绝分支替换为：

```python
    rights_ratio = _ratio(_field(action, "rights_issue_ratio"))
    rights_price = _ratio(_field(action, "rights_issue_price"))
    if rights_ratio > 0 and rights_price <= 0:
        raise UnsupportedCorporateAction(
            f"corporate action {action_id} is a rights issue without a usable "
            "subscription price"
        )
```

(c) 入账主体（原 :109-148）改为——分红/送转计算之间捕获事件前持仓，分红入账后算预算：

```python
    cash_per_share = _ratio(_field(action, "cash_dividend_per_share"))
    bonus = _ratio(_field(action, "bonus_share_ratio"))
    capitalization = _ratio(_field(action, "capitalization_ratio"))

    if any(booked.action_id == action_id for booked in account.action_ledger):
        return None

    held_before = account.position_quantity(symbol)
    cash_amount = _dividend_cash(account, symbol, record_date, cash_per_share)
    bonus_shares = _bonus_shares(account, symbol, bonus, capitalization)

    # Rights subscription (ADR-025): full participation bounded by the cash
    # available on the ex-date after any same-event dividend credit.
    entitlement = subscribed = 0
    cash_paid = Decimal("0")
    if rights_ratio > 0:
        entitlement = int(
            (Decimal(held_before) * rights_ratio).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        affordable = int(
            (account.cash / rights_price).to_integral_value(rounding=ROUND_FLOOR)
        )
        subscribed = min(entitlement, affordable)
        if subscribed > 0:
            cash_paid = (Decimal(subscribed) * rights_price).quantize(
                CENT, rounding=ROUND_HALF_UP
            )

    note = _text(_field(action, "source")) or "corporate_action"
    if cash_amount > 0:
        account.credit_cash(cash_amount, note=f"dividend {symbol}")
    if bonus_shares > 0:
        account.increase_position(
            symbol,
            bonus_shares,
            buy_date=ex_date,
            fill_id=action_id,
            note=f"{bonus + capitalization} ratio credit",
        )
    if subscribed > 0:
        account.debit_cash(cash_paid, note=f"rights subscription {symbol}")
        account.increase_position(
            symbol,
            subscribed,
            buy_date=ex_date,
            fill_id=action_id,
            note=f"rights {rights_ratio} ratio subscription",
        )
    shares_added = bonus_shares + subscribed
    entry = CorporateActionLedgerEntry(
        seq=len(account.action_ledger),
        action_id=action_id,
        symbol=symbol,
        ex_date=ex_date,
        record_date=record_date,
        cash_credited=cash_amount,
        shares_added=shares_added,
        cash_paid=cash_paid,
        rights_entitlement_shares=entitlement,
        rights_subscribed_shares=subscribed,
        note=note,
    )
    account.record_corporate_action(entry)
    return entry
```

(d) 模块 docstring 第 14-17 行的权利股句子改为：

```python
Rights issues with a usable subscription price book at full participation
(bounded by available cash; the shortfall is recorded); mergers, conversions,
incomplete or cross-source-conflicted actions and unpriced rights issues
touching a *held* name raise :class:`UnsupportedCorporateAction` and abort
that scenario; actions for names the account does not hold never mutate it.
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/unit/test_corporate_action_accounting.py -q`
Expected: PASS（新 9 个用例 + 既有全部用例——no-op、not_implemented、缺日期等 fail-closed 用例不变绿则回查顺序）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/backtest/corporate_actions.py tests/unit/test_corporate_action_accounting.py
git commit -m "feat(backtest): book implemented rights issues at full participation

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 准备性检查放行"可给价"配股

**Files:**
- Modify: `src/stock_quant/backtest/engine.py`（`_require_bookable` :580-584、`_validate_bars` docstring :492）
- Test: `tests/integration/test_backtest_engine.py`（替换 :994 测试 + 新增两个）

**Interfaces:**
- Produces: `_require_bookable` 对配股的唯一要求 = "有可用配股价"；`_validate_bars` docstring 写明检查域是**窗口级可能持有超集**、配股现已可入账。冲突/非 implemented/缺日期/`unkeyed_actions` 分支逐字不动。

- [ ] **Step 1: 写失败测试**

替换 `test_readiness_rejects_a_rights_issue_on_a_held_name`（:994）为以下三个测试：

```python
def test_a_priced_rights_issue_on_a_held_name_books_instead_of_aborting():
    actions = _read_fixture("corporate_actions.parquet").copy()
    row = actions[(actions.symbol == IOTA) & (actions.ex_date == _MARKET.days[52])]
    actions.loc[row.index[0], "rights_issue_ratio"] = 0.3
    actions.loc[row.index[0], "rights_issue_price"] = 4.0
    result = BacktestEngine().run(_request_with(actions=actions))
    booked = result.action_ledger[
        result.action_ledger.action_id == f"{IOTA}#{_MARKET.days[52].isoformat()}"
    ]
    assert len(booked) == 1
    entry = booked.iloc[0]
    assert 0 <= entry["rights_subscribed_shares"] <= entry["rights_entitlement_shares"]
    assert entry["cash_paid"] == pytest.approx(
        entry["rights_subscribed_shares"] * 4.0
    )


def test_readiness_still_rejects_an_unpriced_rights_issue_on_a_held_name():
    actions = _read_fixture("corporate_actions.parquet").copy()
    row = actions[(actions.symbol == IOTA) & (actions.ex_date == _MARKET.days[52])]
    actions.loc[row.index[0], "rights_issue_ratio"] = 0.3  # price 保持缺失
    with pytest.raises(UnsupportedCorporateAction, match="without a usable"):
        BacktestEngine().run(_request_with(actions=actions))


def test_a_priced_rights_issue_in_the_superset_but_not_held_is_a_no_op():
    # KAPPA 在默认 schedule 之外；在除权日之后插入一笔 KAPPA 买入把它拉进
    # 可能持有超集，但除权日当天账户并不持有 → 检查放行、账本无该行。
    actions = _read_fixture("corporate_actions.parquet").copy()
    kappa_ex = date(2020, 3, 5)
    row = actions[(actions.symbol == KAPPA) & (actions.ex_date == kappa_ex)]
    actions.loc[row.index[0], "rights_issue_ratio"] = 0.3
    actions.loc[row.index[0], "rights_issue_price"] = 4.0
    first_after = next(day for day in _MARKET.days if day > kappa_ex)
    schedule = (
        _MARKET.schedule[:1]
        + (OrderDay(trade_date=first_after, sells=(), buys=(Order("rk1", BUY, KAPPA, 100),)),)
        + _MARKET.schedule[1:]
    )
    result = BacktestEngine().run(_request_with(actions=actions, schedule=schedule))
    assert f"{KAPPA}#{kappa_ex.isoformat()}" not in set(result.action_ledger.action_id)
```

（若既有测试文件中 `OrderDay` 的构造签名不同——以文件内 `test_a_scheduled_sell_funds_a_same_day_buy` 的现行用法为准对齐字段名。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/integration/test_backtest_engine.py -q -k "rights_issue or superset"`
Expected: 第一个 FAIL（现状 readiness 仍拒绝 priced 配股）；第三个可能已 PASS（no-op 路径在 readiness 拒绝前不可达——以红灯为准记录实际）。

- [ ] **Step 3: 实现检查放行**

`_require_bookable` 的配股分支（:580-584）替换为：

```python
        if row.get("rights_issue_ratio", 0) > 0:
            price = row.get("rights_issue_price")
            if price is None or float(price) <= 0:
                raise UnsupportedCorporateAction(
                    f"corporate action {action_id} is a rights issue without "
                    "a usable subscription price"
                )
```

`_validate_bars` docstring 的 "…must all be implemented, complete, non-rights and mutually consistent…" 改为：

```python
        accepted corporate actions touching any possibly-held symbol (the
        window-level superset, not the actual holdings) must all be
        implemented, complete, mutually consistent and priced-if-a-rights-
        issue; rights issues with a usable subscription price now book at
        full participation (ADR-025).  Any failure raises and no scenario is
        started.
```

- [ ] **Step 4: 运行确认通过（含全文件回归）**

Run: `pytest tests/integration/test_backtest_engine.py -q`
Expected: PASS——替换后的三测试全绿；冲突/非 implemented/缺日期拒绝用例（既有）保持绿（fail-closed 未回撤的证明）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/backtest/engine.py tests/integration/test_backtest_engine.py
git commit -m "feat(backtest): readiness admits priced rights issues on the superset

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: `_derive_cash_ledger` 重放缴款

**Files:**
- Modify: `src/stock_quant/research/runner.py`（`_derive_cash_ledger` 重放分支，约 :3417-3429）
- Test: `tests/integration/test_research_runner.py`（追加）

**Interfaces:**
- Produces: 重放分支在 `credit_cash` 与 `increase_position` 之间插入缴款；`cash_paid` 读取加自校验（`subscribed > shares_added` → `ValueError`）；全部读取 `.get(...) or 0`（旧 parquet 无新列 → 0 → 行为不变）。

- [ ] **Step 1: 写失败测试**

`tests/integration/test_research_runner.py` 追加（文件头部 import 区按需补 `date`/`Decimal`/`pd`/`pytest`/`TradingCalendar`/`ResearchRunner`——该文件已有大部分）：

```python
def test_derive_cash_ledger_replays_a_rights_subscription_payment(tmp_path):
    from decimal import Decimal

    from stock_quant.backtest.account import Account  # noqa: F401 - 语义锚点
    from stock_quant.research.runner import ResearchRunner

    calendar = TradingCalendar.from_open_days((date(2020, 1, 6), date(2020, 1, 7)))
    actions = pd.DataFrame(
        [
            {
                "seq": 0,
                "action_id": "600000.SH#2020-01-07",
                "symbol": "600000.SH",
                "ex_date": date(2020, 1, 7),
                "record_date": date(2020, 1, 6),
                "cash_credited": 0.0,
                "shares_added": 30,
                "cash_paid": 390.0,
                "rights_entitlement_shares": 30,
                "rights_subscribed_shares": 30,
                "note": "cninfo",
            }
        ]
    )
    equity = pd.DataFrame({"trade_date": [date(2020, 1, 6), date(2020, 1, 7)],
                           "cash": [1000.0, 610.0]})
    equity_path = tmp_path / "equity.parquet"
    equity.to_parquet(equity_path, index=False)

    runner = ResearchRunner.__new__(ResearchRunner)  # 方法不使用 self
    ledger = runner._derive_cash_ledger(
        fills=pd.DataFrame(), actions=actions,
        initial_cash=1000.0, calendar=calendar, equity_path=equity_path,
    )
    kinds = list(ledger.kind)
    assert kinds == ["initial", "debit"]
    assert ledger.amount.iloc[-1] == pytest.approx(390.0)
    assert ledger.balance.iloc[-1] == pytest.approx(610.0)


def test_derive_cash_ledger_reads_old_parquets_without_the_new_columns(tmp_path):
    from stock_quant.research.runner import ResearchRunner

    calendar = TradingCalendar.from_open_days((date(2020, 1, 6), date(2020, 1, 7)))
    actions = pd.DataFrame(
        [
            {
                "seq": 0,
                "action_id": "600000.SH#2020-01-07",
                "symbol": "600000.SH",
                "ex_date": date(2020, 1, 7),
                "record_date": date(2020, 1, 6),
                "cash_credited": 40.0,
                "shares_added": 20,
                "note": "cninfo",
            }
        ]
    )
    equity = pd.DataFrame({"trade_date": [date(2020, 1, 6), date(2020, 1, 7)],
                           "cash": [1000.0, 1040.0]})
    equity_path = tmp_path / "equity.parquet"
    equity.to_parquet(equity_path, index=False)

    runner = ResearchRunner.__new__(ResearchRunner)
    ledger = runner._derive_cash_ledger(
        fills=pd.DataFrame(), actions=actions,
        initial_cash=1000.0, calendar=calendar, equity_path=equity_path,
    )
    # 旧 run 无新列：读取端 .get() or 0，重放与对账结果不变。
    assert list(ledger.kind) == ["initial", "credit"]
    assert ledger.balance.iloc[-1] == pytest.approx(1040.0)


def test_derive_cash_ledger_refuses_inconsistent_subscription(tmp_path):
    from stock_quant.research.runner import ResearchRunner

    calendar = TradingCalendar.from_open_days((date(2020, 1, 6), date(2020, 1, 7)))
    actions = pd.DataFrame(
        [
            {
                "seq": 0,
                "action_id": "600000.SH#2020-01-07",
                "symbol": "600000.SH",
                "ex_date": date(2020, 1, 7),
                "record_date": date(2020, 1, 6),
                "cash_credited": 0.0,
                "shares_added": 10,  # < subscribed：账本行自相矛盾
                "cash_paid": 390.0,
                "rights_entitlement_shares": 30,
                "rights_subscribed_shares": 30,
                "note": "cninfo",
            }
        ]
    )
    equity = pd.DataFrame({"trade_date": [date(2020, 1, 7)], "cash": [610.0]})
    equity_path = tmp_path / "equity.parquet"
    equity.to_parquet(equity_path, index=False)

    runner = ResearchRunner.__new__(ResearchRunner)
    with pytest.raises(ValueError, match="subscribes"):
        runner._derive_cash_ledger(
            fills=pd.DataFrame(), actions=actions,
            initial_cash=1000.0, calendar=calendar, equity_path=equity_path,
        )
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/integration/test_research_runner.py -q -k derive_cash_ledger`
Expected: 第一个 FAIL——重放缺缴款，期末余额 1000 ≠ 610 对账抛错（或 kinds 断言失败）；第三个 FAIL（无自校验）。

- [ ] **Step 3: 实现重放缴款**

`_derive_cash_ledger` 的重放分支改为（`note` 行之后、`if amount > 0` 之前插入；`increase_position` 一行不变）：

```python
                paid = Decimal(str(record.get("cash_paid") or 0))
                subscribed = int(record.get("rights_subscribed_shares") or 0)
                if subscribed > shares:
                    raise ValueError(
                        f"action ledger row {action_id} subscribes {subscribed} "
                        f"shares but adds only {shares}"
                    )
                if amount > 0:
                    account.credit_cash(amount, note=note)
                if paid > 0:
                    account.debit_cash(paid, note=note)
                if shares > 0 and action_id:
                    account.increase_position(
                        str(record["symbol"]), shares, buy_date=day,
                        fill_id=action_id, note=note,
                    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/integration/test_research_runner.py -q`
Expected: PASS（新增三用例 + 该文件全部既有用例——无配股的旧路径行为不变）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/research/runner.py tests/integration/test_research_runner.py
git commit -m "feat(research): replay rights-subscription payments in the cash ledger

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: 报表四列（认购缴款/应配/认购/放弃）

**Files:**
- Modify: `src/stock_quant/reporting/html.py:739`（`_action_rows`）
- Test: `tests/integration/test_reports.py`（fixture 补列 + 旧列兼容断言）

**Interfaces:**
- Produces: 公司行为流水表列序 = 序号、事件ID、证券代码、除权除息日、股权登记日、现金入账、送转股数、**认购缴款、应配股数、认购股数、放弃股数**、说明；放弃 = `应配 − 认购`（导出量）；缺列（旧 parquet）经 `.get()` → None → 既有 `_money`/`_int_text` 渲染 `"—"`。

- [ ] **Step 1: 写失败测试**

`tests/integration/test_reports.py`：`_action_ledger()` fixture（:175）的**第一条**记录追加三键 `"cash_paid": 390.0, "rights_entitlement_shares": 30, "rights_subscribed_shares": 30`（第二条保持无新键——同时充当"旧 parquet 行"兼容样本）；新增断言测试：

```python
def test_action_ledger_table_shows_rights_columns_and_dash_for_legacy_rows():
    html = _render_report_with(action_ledger=_action_ledger())  # 以文件内现行渲染入口为准
    assert "认购缴款" in html
    assert "应配股数" in html
    assert "认购股数" in html
    assert "放弃股数" in html
    assert "390.00" in html       # 缴款
    assert "30" in html           # 应配/认购
    assert "0" in html            # 放弃 = 30 − 30（同一条记录行内）
```

（`_render_report_with` 若不存在，按该文件既有渲染测试的调用方式内联——入口名以文件内现行 helper 为准，断言不变。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/integration/test_reports.py -q -k rights_columns`
Expected: FAIL——表头无"认购缴款"。

- [ ] **Step 3: 实现四列**

`html.py` `_action_rows` 的 `columns` 在"送转股数"之后插入 `"认购缴款", "应配股数", "认购股数", "放弃股数"`；`row_of` 对应位置插入：

```python
            _money(record.get("cash_paid")),
            _int_text(record.get("rights_entitlement_shares")),
            _int_text(record.get("rights_subscribed_shares")),
            (
                str(
                    int(record["rights_entitlement_shares"])
                    - int(record["rights_subscribed_shares"])
                )
                if "rights_entitlement_shares" in record
                and "rights_subscribed_shares" in record
                else "—"
            ),
```

（旧 parquet 缺列 → 前三列 `.get()` 得 None → `_money`/`_int_text` 渲染 `"—"`；放弃列显式判列存在。）

- [ ] **Step 4: 运行确认通过（含该文件回归）**

Run: `pytest tests/integration/test_reports.py -q`
Expected: PASS（既有"公司行为流水"断言 :426 仍绿；第二条 legacy 记录渲染 `"—"` 不抛错）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/reporting/html.py tests/integration/test_reports.py
git commit -m "feat(report): show rights subscription, entitlement, and waiver columns

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: ADR-025 与治理登记

**Files:**
- Create: `docs/adr/025-rights-issue-booking.md`
- Modify: `docs/adr/DECISIONS_INDEX.md`（表格新行，newest concerns first 顶部）
- Modify: `docs/superpowers/specs/2026-09-03-phase-one-quant-system-design.md`（§500、§683 两处）
- Modify: `docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md`（§7.1 编号勘误一行）

**Interfaces:**
- Produces: ADR-025（accepted）；编号修正记录（设计写 023，实际 025，理由：023/024 已于 2026-10-02 被占用）。

- [ ] **Step 1: 写 ADR-025**

```markdown
# 025. Rights issues book at full participation

- Status: accepted
- Date: 2026-10-03
- Spec: docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md（编号
  说明：设计 §7.1 原取 023，登记时 023/024 已被占用，顺延为 025）

## Context

持有期回测对已实现配股无条件中止（准备性检查与入账两处），当前数据集窗口内
20 条配股因此挡死全窗口运行；`adjusted_bar` 的 total-return 递推却已隐含
"全额参与"假设——因子侧与回测侧口径分裂。

## Decision

1. 已实现、有可用配股价（`rights_issue_price > 0`）的配股在除权日按**全额参与**
   口径入账：应配 `round(持仓 × r, HALF_UP)`；认购 `min(应配, floor(当日预算/配股价))`；
   缴款 CENT 量化；同一事件的现金分红先入账、后算预算。
2. 现金不足时按可用现金部分认购，弃配逐事件留痕（账本三原子字段
   `cash_paid` / `rights_entitlement_shares` / `rights_subscribed_shares`，
   弃配为导出量）。
3. `Account.debit_cash` 是全或无原语：非正额 `ValueError`、超额
   `CashShortfallError`，现金非负不变量不被削弱。
4. TERP 权益守恒（与 `adjusted_bar` 递推一致）有单元断言钉住（合成价格用例）。

## Rejected alternatives

- 透支 / 强制卖出 / 现金缓冲预留：引入未定义的资金规则，超出本决策。
- 按零价或估算价入账无法给价事件：无法计算成本的事件仍 fail-closed 中止。
- 收窄 `possible_held_symbols` 窗口级超集：那是既有契约（预跑持仓重放才能收窄）。

## Consequences

其余复杂行为（吸收合并、换股、非 `implemented`、跨源冲突、缺关键日期、无价
配股）继续 fail-closed；重放端 `.get() or 0` 保持旧 run 可读；弃配 binding 频率
在首次全窗口运行后即可测量（若某场景普遍 binding，是资金规则问题，非本口径）。
`adjusted_bar` 递推口径本身的 ADR 登记仍是独立未决事项（见 DECISIONS_INDEX
"Not yet recorded"）。
```

- [ ] **Step 2: 登记索引**

`DECISIONS_INDEX.md` 表格首行（`| ADR | Status | …`表头之后）插入：

```markdown
| [025 Rights issues book at full participation](025-rights-issue-booking.md) | accepted | 2026-10-03 | `src/stock_quant/backtest/corporate_actions.py`, `src/stock_quant/backtest/account.py`, `src/stock_quant/backtest/models.py`, `src/stock_quant/backtest/engine.py`, `src/stock_quant/research/runner.py`, `src/stock_quant/reporting/html.py` | rights issue, 配股, full participation, partial subscription, debit_cash, cash_paid, entitlement, waiver, TERP, fail closed, superset check | You change how a rights issue is booked, refused, replayed or reported, or the cash non-negativity primitive it relies on. |
```

- [ ] **Step 3: 修订 phase-one 规格两处**

§500 的"配股、吸收合并和换股等复杂行为标记`unsupported_corporate_action`；持仓遇到不支持或冲突行为时停止运行，不静默忽略。"改为：

```markdown
配股按 [ADR-025](../../adr/025-rights-issue-booking.md) 以全额参与口径入账（现金不足部分认购、弃配留痕）；吸收合并和换股等其余复杂行为标记`unsupported_corporate_action`，持仓遇到不支持或冲突行为时停止运行，不静默忽略。
```

§683 的"- 配股、合并、换股等复杂公司行为；"一行改为：

```markdown
- 合并、换股等复杂公司行为（配股入账口径见 [ADR-025](../../adr/025-rights-issue-booking.md)）；
```

（相对路径按该文件到 `docs/adr/` 的实际层级核对后写。）

- [ ] **Step 4: 设计文档勘误**

`2026-09-27-rights-issue-booking-design.md` §7.1 的"取 **023**"段末尾追加一行：

```markdown
> 勘误（2026-10-03）：登记时 023（suspension carry-forward）与 024（membership
> slice hash）已被占用，本设计实际登记为 **ADR-025**；上文关于 021/022 预留的
> 说明已由索引现状（021 常驻查询面、022 panda graft 均已登记）解决。
```

- [ ] **Step 5: 提交**

```bash
git add docs/adr/025-rights-issue-booking.md docs/adr/DECISIONS_INDEX.md docs/superpowers/specs/2026-09-03-phase-one-quant-system-design.md docs/superpowers/specs/2026-09-27-rights-issue-booking-design.md
git commit -m "docs(adr): record ADR-025 rights-issue booking at full participation

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: 全窗口验证与收口

**Files:**
- 无代码改动（验证任务；发现偏差回对应任务修）

**Interfaces:**
- Consumes: Task 1–7 全部。
- Produces: 设计 §9 验收证据。

- [ ] **Step 1: 全量测试**

Run: `pytest tests/unit/test_corporate_action_accounting.py tests/unit/test_account.py tests/integration/test_backtest_engine.py tests/integration/test_research_runner.py tests/integration/test_reports.py -q`
Expected: 全部 PASS。

- [ ] **Step 2: 更广回归（公司行为相关面）**

Run: `pytest tests/unit tests/integration -q -k "corporate or account or backtest or walk_forward"`
Expected: PASS（数据侧公司行为测试不受影响——数据侧零改动的证明）。

- [ ] **Step 3: 全窗口工程诊断（设计 §9 判据）**

Run: `python -m stock_quant backtest momentum_60d --root project --engineering`
Expected: 不再出现 `UnsupportedCorporateAction ... rights issue`；run 到达 COMPLETED（此前卡在 backtest 阶段：`000970.SZ#2022-02-24` 等 20 条窗口内配股全部放行）。

Run: `python -m stock_quant report build --root project --debug`
Expected: 报告生成；公司行为流水表含"认购缴款/应配股数/认购股数/放弃股数"四列；报告仍带 UNTRUSTED + ENGINEERING 横幅。

- [ ] **Step 4: 账本自洽勾稽（设计 §9）**

对生成的 `data/runs/<run_id>/backtest/<scenario>/action_ledger.parquet`（或 debug run 对应路径）核对每条配股行：`0 ≤ subscribed ≤ entitlement`、`cash_paid == subscribed × rights_issue_price`（CENT 内）、弃配 = `entitlement − subscribed` 可导出。用一次性只读脚本核对并把计数写进提交信息（窗口内应恰有 20 条除权落在可能持有集内、其中真正落账的条数以实际持仓为准）。

- [ ] **Step 5: 收口提交（如有勾稽修正）**

```bash
git add -A src tests docs
git commit -m "chore(backtest): close out rights-issue booking verification

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

（无修正则不造空提交。）

---

## Self-Review 记录（2026-10-03）

**1. 规格覆盖（设计 §2–§9 → 任务）**

| 设计条目 | 任务 |
| --- | --- |
| §2 会计口径（全额参与/floor/弃配留痕/取整/共现次序） | Task 3 |
| §3 入账路径（缺价 fail-closed、先分红后预算、subscribed=0 落账） | Task 3 |
| §4 `debit_cash` 原语 | Task 1 |
| §5 准备性检查放行 + docstring 语义 | Task 4 |
| §6.1 账本三字段 + 列契约（空表列存在） | Task 2 |
| §6.2 重放对账（`.get() or 0`、自校验） | Task 5 |
| §6.3 报表四列 + 旧 parquet 兼容 | Task 6 |
| §7 ADR / 规格 §500 §683 修订（编号修正 023→025） | Task 7 |
| §8 全部测试（§8.1 九条、§8.2 四条、§8.3 兼容） | Task 1/2/3/4/5/6 |
| §9 验收命令与判据 | Task 8 |
| §9"明确不做"清单 | Global Constraints 钉死 |

**2. 占位符扫描**：两处"以文件内现行用法为准"的核对指令（Task 4 的 `OrderDay` 字段名、Task 6 的渲染入口名）是对既有测试文件内既定模式的对齐指令，非未定义引用；其余步骤代码完整。

**3. 类型一致性**：`debit_cash(amount, *, note)`（Task 1 定义、Task 3/5 消费）；账本字段名 `cash_paid`/`rights_entitlement_shares`/`rights_subscribed_shares` 在 models/engine/runner/html/tests 六处逐字一致；错误文案 `"is a rights issue without a usable subscription price"`（corporate_actions 与 engine 两处、测试 `match="without a usable"` 一致）。

**4. 已知风险**：Task 4 的 superset 测试通过"除权日后插入 KAPPA 买入"把 KAPPA 拉进可能持有集——若 `_MARKET.schedule[:1]` 的首日早于该买入日导致重排异常，改用 `test_a_scheduled_sell_funds_a_same_day_buy` 的同款拼装方式（`schedule[:2] + (OrderDay,) + schedule[3:]`）并把 `first_after` 落在 days[8] 之后即可，语义不变。
