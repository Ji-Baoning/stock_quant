# 659 池数据层剩余缺陷修复 · 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉 `docs/superpowers/specs/2026-09-15-data-layer-residual-defects-design.md` 认定的三个数据层缺陷（D1 窗口起点、D2 停牌锚点抓取、D3 隔离证据窗口口径），只改代码、测试与文档，不产出新数据集版本。

**Architecture:** D2 在 `update()` 里、`_fetch_primary_stock` 之后加一层**只供证明用**的窗口前锚点抓取（`_deepen_head_anchors`）：候选符号向过去分段探测，直到找到一根 bar 或退到该股自己的 `list_date`；抓到的帧只并入 `raw_daily_frames`（`_materialize_suspensions` 的证明输入），永不进入发布路径。D3 在 `data_model/corporate_actions.py` 加一个纯函数判定某条隔离记录能否影响某窗口，只在覆盖度调用点用它收窄传给 `_coverage_verdict` 的映射；发布隔离表仍全量。D1 只改 `RUNBOOK.md` 的命令参数。

**Tech Stack:** Python 3 / pandas / pytest / duckdb（只读探查用 `python3`，`python3` 有 duckdb，`.venv/bin/python` 没有）。

## Global Constraints

- 本轮**不**运行任何联网或出版命令：不跑 `data update` / `data validate`，不动 `project/data/**`，`CURRENT` 不变。
- 不修改停牌证明规则（`suspensions.py` 一行不动）、不修改 `_check_date_window`、不扩充 `_ACCEPTED_MISSING_CODES`、不弱化任何门禁（invariant 3）。
- 不把窗口前 bar 发布进 `daily_bar`（invariant 1、2）。
- 已发布的隔离表仍是全量行（invariant 3：证据不隐藏）。
- 用户即时指令 > 安全与平台指令 > 路径规则 > 根协议层 > 架构不变量 > ADR > 功能规格 > 运维记录 > 历史资料。
- 跑测试用具名文件（`pytest tests/unit/test_suspensions.py -q`），**不跑裸 `pytest`**；integration 全量约 18.5 分钟。
- 每个 commit 只 `git add` 该任务明确列出的文件，**绝不** `git add -A`：工作树里有大量与本任务无关的在途修改（`PROJECT_MEMORY.md`、`README.md`、`project/**` …），invariant 9 禁止把它们带进来。
- commit message 末尾加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

---

## 文件结构

| 文件 | 责任 | 本计划的改动 |
| --- | --- | --- |
| `src/stock_quant/data_model/corporate_actions.py` | 公司行为标准化 / 对账 / 窗口口径 | 新增 `quarantine_row_out_of_window_reason` 与私有 `_row_date`（Task 1） |
| `src/stock_quant/data_quality/models.py` | 质量码词汇表 | 新增 `CODE_QUARANTINE_OUT_OF_WINDOW`（Task 2） |
| `src/stock_quant/data_pipeline.py` | 抓取编排 / 覆盖度判定 | 新增 `_deepen_head_anchors` + 调用点（Task 3）；覆盖度调用点收窄 + `_window_relevant_quarantine`（Task 2） |
| `tests/unit/test_corporate_action_normalize.py` | 公司行为标准化单测 | 追加判定矩阵单测（Task 1） |
| `tests/integration/test_data_pipeline.py` | 更新编排集成测试 | 追加 D3 端到端用例（Task 2） |
| `tests/unit/test_suspensions.py` | 停牌证明单测 + 更新端到端 | 追加 D2 端到端用例；`_SuspendStub` 加旋钮与 `calls` 日志（Task 3） |
| `RUNBOOK.md` | 运维程序 | D1 起点改 `2015-01-05`（Task 4） |
| `docs/adr/006-corporate-action-window-scope.md` | 决策记录 | 新建（Task 4） |
| `docs/adr/DECISIONS_INDEX.md` | 决策索引 | 登记 006（Task 4） |
| `docs/operations/2026-09-14-blocking-gap-root-cause.md` | dated 证据 | 追加本轮实测切分（Task 4） |
| `docs/superpowers/specs/2026-09-15-data-layer-residual-defects-design.md` | 本设计的规格 | §5.4 签名由 `-> bool` 改为 `-> str | None`（Task 1，见下） |

**规格偏差（已在本计划中修正，需同步回规格）**：规格 §5.4 写 `quarantine_row_in_window(row, start, end) -> bool`，但同一节又要求 INFO 的 `details={"branch": <分支名>}`。bool 返回值无法给出分支名，调用方只能把判定重算一遍。故本计划让纯函数返回**被排除的分支名**（`str | None`），一处实现同时供「是否相关」与「为何被排除」使用。

---

## Task 1: D3 判定纯函数

**Files:**
- Modify: `src/stock_quant/data_model/corporate_actions.py`（imports；新增常量与函数，紧接 `filter_corporate_actions_to_window` 之后，即 275 行后）
- Test: `tests/unit/test_corporate_action_normalize.py`（追加，与既有 `filter_corporate_actions_to_window` 用例同处）
- Modify: `docs/superpowers/specs/2026-09-15-data-layer-residual-defects-design.md`（§5.4 签名一行）

**Interfaces:**
- Consumes: 无（纯函数，不依赖本计划其它任务）
- Produces:
  - `EXCLUSION_EX_DATE_OUT_OF_WINDOW = "ex_date_out_of_window"`
  - `EXCLUSION_RECORD_DATE_OUT_OF_WINDOW = "record_date_out_of_window"`
  - `EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED = "announcement_pre_window_implemented"`
  - `quarantine_row_out_of_window_reason(row: Mapping[str, object], start: date, end: date) -> str | None`
    —— 返回被排除的分支名，返回 `None` 表示该行与窗口相关（必须保留）

- [ ] **Step 1: 先改规格 §5.4 的签名，使计划与规格一致**

把这一行（规格 207 行）：

```
- 新增纯函数 `quarantine_row_in_window(row, start, end) -> bool`，实现 §5.2 的
  三分支。就近放在 `data_model/corporate_actions.py`（与被取代的
  `filter_corporate_actions_to_window` 同处一层、同一关注点）。
```

改成：

```
- 新增纯函数 `quarantine_row_out_of_window_reason(row, start, end) -> str | None`，
  实现 §5.2 的判定：返回**被排除的分支名**（`ex_date_out_of_window` /
  `record_date_out_of_window` / `announcement_pre_window_implemented`），
  返回 `None` 表示该行与窗口相关。返回分支名而非 bool，是因为本节还要把 `branch`
  写进 INFO 的 details —— 一处实现同时供两个用途。就近放在
  `data_model/corporate_actions.py`（与被取代的 `filter_corporate_actions_to_window`
  同处一层、同一关注点）。
```

- [ ] **Step 2: 写失败测试**

追加到 `tests/unit/test_corporate_action_normalize.py` 末尾。先把文件头的 import 块补上新增的两个名字（`EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED`、`EXCLUSION_EX_DATE_OUT_OF_WINDOW`、`EXCLUSION_RECORD_DATE_OUT_OF_WINDOW`、`quarantine_row_out_of_window_reason`），保持字母序（`filter_corporate_actions_to_window` 之后、`normalize_corporate_actions` 之前；`EXCLUSION_*` 常量排在 `REASON_*` 之前……本文件现有 import 只按字母序排列，`EXCLUSION` 在 `REASON` 前，照字母序插入即可）。

```python
# --------------------------------------------------------------------------- #
# Window scope of a quarantined row (ADR-006): an event can only matter to a
# window it falls in, and the first *known* date decides which window that is.
# --------------------------------------------------------------------------- #

_WINDOW_START = datetime.date(2015, 1, 5)
_WINDOW_END = datetime.date(2016, 12, 30)


def _quarantine_row(**overrides):
    """One canonical quarantine row; dates default to ``None``."""
    row = {
        "symbol": "600000.SH",
        "announcement_date": None,
        "record_date": None,
        "ex_date": None,
        "status": "implemented",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        # 1. ex_date known: the transition itself is outside the window.
        (
            _quarantine_row(ex_date=datetime.date(2014, 12, 31)),
            EXCLUSION_EX_DATE_OUT_OF_WINDOW,
        ),
        (
            _quarantine_row(ex_date=datetime.date(2017, 1, 3)),
            EXCLUSION_EX_DATE_OUT_OF_WINDOW,
        ),
        (
            _quarantine_row(ex_date=datetime.date(2015, 1, 5)),
            None,
        ),
        (
            _quarantine_row(ex_date=datetime.date(2016, 12, 30)),
            None,
        ),
        # ex_date wins over a record_date that would say otherwise.
        (
            _quarantine_row(
                ex_date=datetime.date(2015, 6, 1),
                record_date=datetime.date(2014, 6, 1),
            ),
            None,
        ),
        # 2. no ex_date: the record date decides (its own transition follows it).
        (
            _quarantine_row(record_date=datetime.date(2014, 12, 31)),
            EXCLUSION_RECORD_DATE_OUT_OF_WINDOW,
        ),
        (
            _quarantine_row(record_date=datetime.date(2017, 1, 3)),
            EXCLUSION_RECORD_DATE_OUT_OF_WINDOW,
        ),
        (
            _quarantine_row(record_date=datetime.date(2015, 1, 5)),
            None,
        ),
        # 3. neither dated fact: a pre-window announcement on an implemented
        #    record cannot affect the window.
        (
            _quarantine_row(announcement_date=datetime.date(1998, 6, 1)),
            EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED,
        ),
        #    ... but only when the record claims to be implemented.
        (
            _quarantine_row(
                announcement_date=datetime.date(1998, 6, 1),
                status="not_implemented",
            ),
            None,
        ),
        #    An announcement *after* the window keeps the row: its ex-date may
        #    still land inside it.
        (
            _quarantine_row(announcement_date=datetime.date(2017, 1, 3)),
            None,
        ),
        (
            _quarantine_row(announcement_date=datetime.date(2015, 6, 1)),
            None,
        ),
        # 4. no known date at all: fail closed.
        (_quarantine_row(), None),
    ],
)
def test_quarantine_row_out_of_window_reason_decides_by_first_known_date(
    row, expected
):
    assert (
        quarantine_row_out_of_window_reason(row, _WINDOW_START, _WINDOW_END)
        == expected
    )


def test_quarantine_row_out_of_window_reason_reads_pandas_date_cells():
    """A published quarantine table hands back ``Timestamp`` / ``NaT`` cells."""
    row = _quarantine_row(
        ex_date=pd.NaT,
        record_date=pd.NaT,
        announcement_date=pd.Timestamp("1998-06-01"),
    )
    assert (
        quarantine_row_out_of_window_reason(row, _WINDOW_START, _WINDOW_END)
        == EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED
    )
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/unit/test_corporate_action_normalize.py -q -k out_of_window`
Expected: collection error / `ImportError: cannot import name 'quarantine_row_out_of_window_reason'`

- [ ] **Step 4: 实现**

在 `corporate_actions.py` 的 imports 里，把 `from typing import Any` 改成 `from typing import Any, Mapping`，并新增 `from datetime import date`（放在 `import re` 之前，`from __future__` 之后）。

然后在 `filter_corporate_actions_to_window`（275 行 `return frame.loc[keep].reset_index(drop=True)`）之后插入：

```python
#: Why a quarantined row cannot affect a window.  Each label names the rule
#: that excluded it, so the audit trail records whether the exclusion rests on
#: a dated fact (a derivation) or on the pre-window announcement rule for an
#: implemented record (a conditional relaxation -- see ADR-006).
EXCLUSION_EX_DATE_OUT_OF_WINDOW = "ex_date_out_of_window"
EXCLUSION_RECORD_DATE_OUT_OF_WINDOW = "record_date_out_of_window"
EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED = (
    "announcement_pre_window_implemented"
)


def quarantine_row_out_of_window_reason(
    row: Mapping[str, object], start: date, end: date
) -> str | None:
    """Why a quarantined row cannot affect ``[start, end]``; ``None`` if it can.

    A quarantine row is evidence about one event, and an event can only matter
    to a window it falls in.  This answers the *window* question the coverage
    verdict asks; it never edits or hides the row itself.  The first date the
    row actually knows decides:

    1. ``ex_date`` known -- an ex-date outside the window is a transition
       outside it.  This is a derivation.
    2. ``record_date`` known -- also a derivation, because a completed
       settlement's ex-date is never earlier than its record date (measured on
       the accepted facts: 6,872/6,872, lag 1-13 days, no negatives).  A
       record date after the window implies an ex-date after it too.
    3. no ex_date and no record_date, but an ``announcement_date`` before
       ``start`` on an ``implemented`` record -- excluded.  This *relaxes* the
       policy ``filter_corporate_actions_to_window`` states (an implemented
       record with a missing ex-date is kept so reconciliation can flag it) and
       is recorded in ``docs/adr/006-corporate-action-window-scope.md``.  An
       announcement *after* the window does not qualify: the event it announces
       may still settle inside the window.
    4. no known date at all -- kept (fail closed).
    """
    ex_date = _row_date(row.get("ex_date"))
    if ex_date is not None:
        return None if start <= ex_date <= end else EXCLUSION_EX_DATE_OUT_OF_WINDOW
    record_date = _row_date(row.get("record_date"))
    if record_date is not None:
        if start <= record_date <= end:
            return None
        return EXCLUSION_RECORD_DATE_OUT_OF_WINDOW
    announcement_date = _row_date(row.get("announcement_date"))
    if (
        announcement_date is not None
        and announcement_date < start
        and str(row.get("status")) == STATUS_IMPLEMENTED
    ):
        return EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED
    return None


def _row_date(value: object) -> date | None:
    """Coerce a canonical row's date cell (``date``/``Timestamp``/``NaT``)."""
    if value is None:
        return None
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return None
    return timestamp.date()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/unit/test_corporate_action_normalize.py -q`
Expected: PASS（含既有的 `filter_corporate_actions_to_window` 用例）

- [ ] **Step 6: Commit**

```bash
cd ~/work/program/stock
git add src/stock_quant/data_model/corporate_actions.py tests/unit/test_corporate_action_normalize.py docs/superpowers/specs/2026-09-15-data-layer-residual-defects-design.md
git commit -m "feat: decide a quarantined corporate action's window relevance

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 2: D3 覆盖度接线

**Files:**
- Modify: `src/stock_quant/data_quality/models.py`（停牌码块之后新增一个码）
- Modify: `src/stock_quant/data_pipeline.py`（imports；`_refresh_corporate_actions` 的覆盖度调用点 1709-1721；新增模块级 `_window_relevant_quarantine`；`_coverage_verdict` 文档字符串）
- Test: `tests/integration/test_data_pipeline.py`（追加 stub 帧 + 源 + 端到端用例）

**Interfaces:**
- Consumes: Task 1 的 `quarantine_row_out_of_window_reason(row, start, end) -> str | None`
- Produces: `CODE_QUARANTINE_OUT_OF_WINDOW = "quarantine_out_of_window"`（INFO，不阻塞发布）

- [ ] **Step 1: 新增质量码**

在 `src/stock_quant/data_quality/models.py` 的停牌码块（`CODE_UNEXPLAINED_PRIMARY_GAP` 之后、`# Missing-row classification labels` 之前）插入：

```python
# A quarantined corporate action whose every known date lies outside a window
# cannot affect that window's series, so it no longer marks the symbol/window
# UNTRUSTED.  The exclusion is INFO-only audit trail -- the published
# quarantine table still carries every row -- and its ``branch`` detail names
# the rule that excluded it (ADR-006).
CODE_QUARANTINE_OUT_OF_WINDOW = "quarantine_out_of_window"
```

`PUBLICATION_BLOCKING_CODES`（`data_quality/gates.py:39`）**不改**：INFO 码不进白名单，门禁不因它阻断。

- [ ] **Step 2: 写失败测试**

在 `tests/integration/test_data_pipeline.py` 里，`_eastmoney_cash_out_of_window` 之后（528 行 `_out_of_window_cash_sources` 之前）追加：

```python
def _cninfo_cash_plus_stale_plan(symbol: str) -> pd.DataFrame:
    """The cross-confirmable cash dividend plus a stale implemented plan.

    The appended row is a 1998 plan the supplier marks ``实施`` but reports with
    no ex-date and no record date.  It cannot be booked (``_standardize_source``
    keys candidates on ``(symbol, ex_date)``), and it survives
    ``filter_corporate_actions_to_window`` on purpose -- that filter keeps an
    implemented record whose ex-date is missing so reconciliation can flag it
    -- so it lands in the quarantine table as ``incomplete``.
    """
    code = symbol.split(".")[0]
    stale = pd.DataFrame(
        [
            {
                "证券代码": code,
                "证券简称": "placeholder",
                "公告日期": "1998-06-01",
                "股权登记日": "",
                "除权除息日": "",
                "派息(税前)(元/10股)": 1.0,
                "送股(股/10股)": 0.0,
                "转增(股/10股)": 0.0,
                "进度": "实施",
                "方案": "10派1元(含税)",
            }
        ],
        columns=_ACTION_CNINFO_COLUMNS,
    )
    # The dividend row is the same one ``_cninfo_single_cash`` proves books at
    # 0.46/share; composing keeps the two fixtures from drifting apart.
    return pd.concat(
        [_cninfo_single_cash(symbol), stale], ignore_index=True
    )


def _stale_pre_window_plan_sources() -> dict[str, DataSource]:
    """``600036.SH`` holds an in-window accepted dividend AND a 1998 implemented
    plan with no ex-date; every other symbol answers no events."""
    source = StubAdapter(
        "akshare",
        action_frames={
            "600036.SH": {
                "cninfo_corporate_actions": _cninfo_cash_plus_stale_plan(
                    "600036.SH"
                ),
                "eastmoney_corporate_actions": _eastmoney_cash("600036.SH"),
            }
        },
    )
    return _all_stubs(akshare=source)
```

并把该文件头部现有的 import 块

```python
from stock_quant.data_quality.models import (
    CODE_NONPOSITIVE_PRICE,
    QualityReport,
    Severity,
)
```

改成

```python
from stock_quant.data_quality.models import (
    CODE_NONPOSITIVE_PRICE,
    CODE_QUARANTINE_OUT_OF_WINDOW,
    QualityReport,
    Severity,
)
```

（`Severity`、`pytest`、`pd`、`DatasetReader` 该文件已 import，无需新增。）

然后在 `test_update_events_only_outside_window_read_verified_empty`（848 行）之后追加用例：

```python
def test_update_ignores_quarantine_rows_whose_dates_predate_the_window(project):
    """A quarantine row that cannot affect the window must not mark it UNTRUSTED.

    ``600036.SH`` books an in-window dividend while carrying a 1998 implemented
    plan the supplier reports without any date.  That stale record has nothing
    to say about 2021-11, so the window reads VERIFIED -- yet it stays in the
    published quarantine table (evidence is not hidden) and the exclusion
    leaves an INFO trace naming the rule that dropped it (ADR-006).
    """
    result = DataPipeline(
        project.root, sources=_stale_pre_window_plan_sources()
    ).update(_request())
    assert result.dataset_ref is not None, result.quality_report

    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        facts = context.read("corporate_action")
        coverage = context.read("corporate_action_coverage")
        quarantine = context.read("corporate_action_quarantine")

    # The in-window dividend still books and the window reads VERIFIED.
    booked = facts.loc[facts["symbol"] == "600036.SH"]
    assert len(booked) == 1
    row = coverage.loc[coverage["symbol"] == "600036.SH"].iloc[0]
    assert row["status"] == "VERIFIED"
    assert pd.isna(row["reason"])

    # Evidence is not hidden: the stale plan is still published.
    stale = quarantine.loc[quarantine["symbol"] == "600036.SH"]
    assert len(stale) == 1
    assert stale.iloc[0]["reason"] == "incomplete"

    # ... and the suppression left an auditable trace.
    trace = [
        issue
        for issue in result.quality_report.issues
        if issue.code == CODE_QUARANTINE_OUT_OF_WINDOW
    ]
    assert len(trace) == 1
    assert trace[0].severity is Severity.INFO
    assert trace[0].symbol == "600036.SH"
    assert trace[0].details["rows"] == 1
    assert trace[0].details["branch"] == "announcement_pre_window_implemented"
```

（`Severity` 已在该文件的 `stock_quant.data_quality.models` import 块里。）

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/integration/test_data_pipeline.py -q -k quarantine_rows_whose_dates`
Expected: FAIL —— `ImportError`（`CODE_QUARANTINE_OUT_OF_WINDOW` 已在 Step 1 加，故此时是 `assert row["status"] == "VERIFIED"` 失败：实际 `UNTRUSTED`）

- [ ] **Step 4: 实现接线**

4a. `data_pipeline.py` 的 import：在从 `stock_quant.data_quality.models` 导入的质量码列表中加入 `CODE_QUARANTINE_OUT_OF_WINDOW`；在从 `stock_quant.data_model.corporate_actions` 的导入列表中加入 `quarantine_row_out_of_window_reason`。

4b. 覆盖度调用点（1706-1721 行）改为：

```python
        merged_quarantine = _merge_corporate_action_quarantine(
            current_quarantine, quarantined
        )
        coverage = coverage_frame(
            [
                _coverage_record_for(
                    symbol,
                    start,
                    end,
                    outcomes_by_symbol[symbol],
                    _accepted_symbols(accepted),
                    _quarantine_reasons_by_symbol(
                        _window_relevant_quarantine(
                            quarantined, start, end, issues
                        )
                    ),
                )
                for symbol in symbols
            ]
        )
```

4c. 在 `_quarantine_reasons_by_symbol`（2331 行）之前插入：

```python
def _window_relevant_quarantine(
    quarantined: pd.DataFrame,
    start: date,
    end: date,
    issues: list[QualityIssue],
) -> pd.DataFrame:
    """Drop quarantined rows that provably cannot affect ``[start, end]``.

    The coverage verdict answers a question about *this window*, so a row whose
    every known date lies outside it is evidence about another period and must
    not mark the window UNTRUSTED (ADR-006).  Only the coverage input is
    narrowed: ``merged_quarantine`` -- the published table -- keeps every row,
    and each exclusion is recorded as an INFO issue grouped by symbol and
    branch so a suppressed decision leaves a trace.
    """
    if quarantined.empty or "symbol" not in quarantined.columns:
        return quarantined
    kept: list[bool] = []
    excluded: dict[tuple[str, str], int] = {}
    for record in quarantined.to_dict("records"):
        branch = quarantine_row_out_of_window_reason(record, start, end)
        kept.append(branch is None)
        if branch is not None:
            key = (str(record["symbol"]), branch)
            excluded[key] = excluded.get(key, 0) + 1
    for (symbol, branch), rows in sorted(excluded.items()):
        issues.append(
            _issue(
                Severity.INFO,
                CODE_QUARANTINE_OUT_OF_WINDOW,
                table=TABLE_CORPORATE_ACTION_QUARANTINE,
                symbol=symbol,
                details={
                    "rows": rows,
                    "branch": branch,
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                },
            )
        )
    return quarantined.iloc[[index for index, keep in enumerate(kept) if keep]]
```

（`TABLE_CORPORATE_ACTION_QUARANTINE` 已在 `data_pipeline.py:194` 定义。）

4d. `_coverage_verdict`（2264 行）的文档字符串第二段改为：

```
    ``VERIFIED`` requires a fully accounted window: every requested endpoint
    answered, at least one returned events, and the symbol holds an accepted
    reconciled fact with *no relevant* quarantine.  A quarantined event *that
    can affect this window* (cross-source conflict / unsupported action /
    incomplete record) makes the window ``UNTRUSTED`` even when a sibling event
    for the same symbol/window was accepted, so a conflicting or unbooked event
    can never be masked by an accepted row while the coverage reads
    ``VERIFIED``.  A row whose every known date lies outside the window is
    excluded by ``_window_relevant_quarantine`` before this decision
    (ADR-006).
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/integration/test_data_pipeline.py -q -k "corporate_action or quarantine or coverage or verified or untrusted or action"`
Expected: PASS（尤其 `test_update_marks_mixed_accepted_and_unsupported_window_untrusted`、`test_update_marks_clean_cross_confirmed_cash_dividend_verified`、`test_update_events_only_outside_window_read_verified_empty` 必须仍为 PASS —— 它们的隔离记录都带窗口内 `ex_date`，不受收窄影响）

- [ ] **Step 6: 跑完整文件确认无回归**

Run: `pytest tests/integration/test_data_pipeline.py -q`
Expected: PASS（新增 1 例）

- [ ] **Step 7: Commit**

```bash
cd ~/work/program/stock
git add src/stock_quant/data_quality/models.py src/stock_quant/data_pipeline.py tests/integration/test_data_pipeline.py
git commit -m "fix: scope corporate-action coverage to the quarantined rows a window can see

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 3: D2 窗口前锚点抓取

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（新增模块常量 `_ANCHOR_PROBE_DAYS` 与两个模块级小助手；新增方法 `_deepen_head_anchors`；`update()` 在 `_fetch_primary_stock` 之后调用）
- Test: `tests/unit/test_suspensions.py`（`_SuspendStub` 加旋钮与 `calls`；追加两个端到端用例）

**Interfaces:**
- Consumes: 无
- Produces: 私有方法 `DataPipeline._deepen_head_anchors(enabled, symbols, master, calendar_open, start, end, issues, statuses, raw_snapshots, raw_daily_frames) -> None`

- [ ] **Step 1: 给 `_SuspendStub` 加旋钮与调用日志**

`tests/unit/test_suspensions.py` 文件头 `from dataclasses import dataclass` 改为 `from dataclasses import dataclass, field`。把 `_SuspendStub`（443-461 行）改成：

```python
#: ``stock_basic`` 的上市日 stub 复用的固定值。
_STUB_LIST_DATE = date(1991, 1, 2)


@dataclass(frozen=True)
class _SuspendStub:
    """Stubs whose primary daily carries pre_close and one suspended name."""

    name: str
    #: Per-symbol absent days that *open* the window (a head-suspended name).
    head_gap_days: dict[str, frozenset[date]] = field(default_factory=dict)
    #: Symbols with no bar at all before the window opens.
    bare_before_window: frozenset[str] = frozenset()
    #: Every request this stub answered, as (endpoint, symbol, start, end).
    calls: list[tuple[str, str | None, date, date]] = field(default_factory=list)

    def fetch(self, request: DataRequest) -> FetchResult:
        self.calls.append(
            (
                request.endpoint,
                request.symbols[0] if request.symbols else None,
                request.start_date,
                request.end_date,
            )
        )
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=self._frame(request),
            metadata={
                "source": self.name,
                "response_timestamp": "2021-12-01T00:00:00Z",
                "transport_id": self.name,
            },
        )
```

`_frame` 的 `stock_basic` 分支里 `"list_date": "19910102"` 改成 `"list_date": _STUB_LIST_DATE.strftime("%Y%m%d")`（同值，消除魔法串）。

`_frame` 的日线兜底分支（511-516 行）改成：

```python
        symbol = request.symbols[0]
        absent = self.head_gap_days.get(symbol, frozenset())
        days = [
            day
            for day in _weekdays(request.start_date, request.end_date)
            if not (symbol == _GAPPY_SYMBOL and day in _GAP_DAYS)
            and day not in absent
        ]
        if symbol in self.bare_before_window:
            days = [day for day in days if day >= _WINDOW_START]
```

- [ ] **Step 2: 写失败测试**

在 `tests/unit/test_suspensions.py` 末尾追加。先加常量（放在 `_NO_TRADE_DAYS` 附近）：

```python
#: A name whose suspension run opens the window: absent from the window's first
#: open day onwards, so the run has no ``before`` bar inside the requested range.
_HEAD_GAP_SYMBOL = "601318.SH"
_HEAD_GAP_DAYS = frozenset(date(2021, 11, day) for day in (1, 2, 3, 4, 5, 8, 9))
#: The same shape, but the symbol has never traded before the window opens.
_BARE_HEAD_GAP_SYMBOL = "601398.SH"
```

再追加用例：

```python
def _fixture_root(tmp_path) -> Path:
    """A fresh synthetic project bootstrapped from the repository templates."""
    root = tmp_path / "project"
    root.mkdir()
    configs = root / "configs"
    configs.mkdir()
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
    ):
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)
    return root


def _probes(stub: _SuspendStub) -> list[tuple[str, str | None, date, date]]:
    """Requests the stub answered with a start before the window opened."""
    return [
        call
        for call in stub.calls
        if call[0] == "daily" and call[2] < _WINDOW_START
    ]


def test_update_anchors_a_suspension_run_that_opens_the_window(tmp_path):
    """A run with no bar inside the window is proven by the symbol's own
    pre-window bar, fetched for proof only and never published."""
    root = _fixture_root(tmp_path)
    tushare = _SuspendStub("tushare", head_gap_days={_HEAD_GAP_SYMBOL: _HEAD_GAP_DAYS})
    stubs = {
        "tushare": tushare,
        "akshare": _SuspendStub("akshare"),
        "baostock": _SuspendStub("baostock"),
    }
    result = DataPipeline(root, sources=stubs).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )

    assert result.dataset_ref is not None, result.quality_report
    codes = {issue.code for issue in result.quality_report.issues}
    assert CODE_SUSPENSION_RUN_UNVERIFIED not in codes

    with DatasetReader(root).open(result.dataset_ref.version) as dataset:
        daily = dataset.read("daily_bar")
    carried = daily[
        (daily["symbol"] == _HEAD_GAP_SYMBOL)
        & (daily["trade_date"] == pd.Timestamp("2021-11-01"))
    ]
    assert len(carried) == 1
    row = carried.iloc[0]
    assert row["source"] == "tushare_suspend"
    assert row["volume"] == 0
    assert row["close"] == 55.0

    # The proof input stays proof input: no pre-window bar is published.
    assert daily["trade_date"].min() >= pd.Timestamp(_WINDOW_START)
    # Only the candidate was probed -- the mid-window gap name already has a bar
    # on the window's first open day, so its run never needs an outside anchor.
    assert [call[1] for call in _probes(tushare)] == [_HEAD_GAP_SYMBOL]


def test_update_leaves_a_head_run_with_no_pre_window_history_unproven(tmp_path):
    """A symbol that never traded before the window has no anchor to find, so
    its head run stays an honest gap instead of a materialized bar."""
    root = _fixture_root(tmp_path)
    tushare = _SuspendStub(
        "tushare",
        head_gap_days={_BARE_HEAD_GAP_SYMBOL: _HEAD_GAP_DAYS},
        bare_before_window=frozenset({_BARE_HEAD_GAP_SYMBOL}),
    )
    stubs = {
        "tushare": tushare,
        "akshare": _SuspendStub("akshare"),
        "baostock": _SuspendStub("baostock"),
    }
    result = DataPipeline(root, sources=stubs).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )

    assert result.dataset_ref is not None, result.quality_report
    codes = {issue.code for issue in result.quality_report.issues}
    assert CODE_SUSPENSION_RUN_UNVERIFIED in codes

    with DatasetReader(root).open(result.dataset_ref.version) as dataset:
        daily = dataset.read("daily_bar")
    assert daily[
        (daily["symbol"] == _BARE_HEAD_GAP_SYMBOL)
        & (daily["source"] == "tushare_suspend")
    ].empty

    # The probe stepped back more than once and stopped at the listing date:
    # the depth is the symbol's own history, not a guessed constant.
    probes = _probes(tushare)
    assert [call[1] for call in probes] == [_BARE_HEAD_GAP_SYMBOL] * len(probes)
    assert len(probes) >= 2
    assert min(call[2] for call in probes) == _STUB_LIST_DATE
```

该文件已 import `Path`、`shutil`、`date`、`timedelta`、`pd`、`DatasetReader`、`bootstrap_dataset`、`DataUpdateRequest`、`CODE_SUSPENSION_RUN_UNVERIFIED`，本步不需新增 import。

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/unit/test_suspensions.py -q -k "anchors_a_suspension_run or no_pre_window_history"`
Expected: 第 1 例 FAIL（`CODE_SUSPENSION_RUN_UNVERIFIED in codes` 且 11-01 无 `tushare_suspend` 行）；第 2 例的 `len(probes) >= 2` FAIL（`probes == []`）

- [ ] **Step 4: 实现**

4a. `data_pipeline.py` 里，紧邻 `_issue` 之外的模块级助手区（`_as_date` 附近）加入常量与助手：

```python
#: Page size of the proof-only pre-window probe.  A *stride*, never a boundary:
#: the probe keeps stepping back until it finds a bar or reaches the symbol's
#: own ``list_date``, so how far back it looks is decided by the data.  The
#: stride exists only to stay under the supplier's per-response row cap.
_ANCHOR_PROBE_DAYS = 3650


def _carries_proof_chain(raw: pd.DataFrame | None) -> bool:
    """Whether a raw daily response can prove a suspension run at all.

    Mirrors ``_materialize_suspensions``' own guard: without ``pre_close``
    there is nothing to chain a gap against, so such a symbol is not worth a
    pre-window probe either.
    """
    if raw is None or "close" not in raw.columns or "pre_close" not in raw.columns:
        return False
    return any(name in raw.columns for name in ("trade_date", "date"))


def _has_bar_on(raw: pd.DataFrame, day: date) -> bool:
    """Whether a raw daily response already carries a row on ``day``."""
    if raw.empty:
        return False
    column = "trade_date" if "trade_date" in raw.columns else "date"
    days = pd.to_datetime(raw[column].astype(str), errors="coerce").dt.date
    return bool((days == day).any())
```

4b. 新增方法，放在 `_fetch_primary_stock` 与 `_materialize_suspensions` 之间（1222 行 `return False` 之后、`def _materialize_suspensions` 之前）：

```python
    def _deepen_head_anchors(
        self,
        enabled,
        symbols,
        master,
        calendar_open,
        start,
        end,
        issues,
        statuses,
        raw_snapshots,
        raw_daily_frames,
    ) -> None:
        """Fetch a proof-only pre-window anchor for head-suspended symbols.

        A suspension run that *opens* the window has no ``before`` bar inside
        the requested range, so ``suspension_rows`` cannot prove it and the run
        stays an unproven gap.  The bar that proves it exists -- it is simply
        outside the range the primary fetch asked for.  For the symbols whose
        window opens inside a run this walks backwards from ``start`` until it
        finds a bar or reaches the symbol's own ``list_date``; the frame it
        finds is appended to ``raw_daily_frames[symbol]``, which is
        ``_materialize_suspensions``' proof input.

        Proof input only: the deepened frame is never normalized, never enters
        ``primary_rows``/``primary_dates``, and so never reaches the published
        ``daily_bar``.  A failed probe is not fatal -- the run degrades to the
        pre-existing behaviour, an unproven head run that stays an honest gap.
        """
        if "tushare" not in enabled or not raw_daily_frames:
            return
        first_open_day = next(
            (
                day
                for day in calendar_open
                if isinstance(day, date) and start <= day <= end
            ),
            None,
        )
        if first_open_day is None:
            return
        source = self._adapter_or_fail("tushare", statuses)
        if source is None:
            return
        listing = {
            str(row["symbol"]): _as_date(row.get("list_date"))
            for row in master.to_dict("records")
        }
        for symbol in symbols:
            raw = raw_daily_frames.get(symbol)
            if not _carries_proof_chain(raw) or _has_bar_on(raw, first_open_day):
                continue
            list_date = listing.get(symbol)
            if list_date is None or list_date >= start:
                continue
            chunk_end = start - timedelta(days=1)
            while chunk_end >= list_date:
                chunk_start = max(
                    list_date, chunk_end - timedelta(days=_ANCHOR_PROBE_DAYS - 1)
                )
                result = self._dispatch(
                    "tushare", source, "daily", symbol, chunk_start, chunk_end,
                    {"adjustment": "unadjusted"}, required=False, issues=issues,
                )
                if result is None:
                    break
                raw_snapshots.append(self._record_raw(result))
                if not result.frame.empty:
                    raw_daily_frames[symbol] = pd.concat(
                        [result.frame, raw], ignore_index=True
                    )
                    break
                chunk_end = chunk_start - timedelta(days=1)
```

4c. `update()` 里，`_fetch_primary_stock` 的非 fatal 分支之后（710 行 `)` 之后、`# ---- required benchmark history` 之前）插入：

```python
        # ---- proof-only pre-window anchors ------------------------------ #
        # Runs before _materialize_suspensions reads the raw frames, and after
        # the primary fetch whose responses it deepens.
        self._deepen_head_anchors(
            enabled,
            equity_symbols,
            master,
            calendar_open,
            start,
            end,
            issues,
            statuses,
            raw_snapshots,
            raw_daily_frames,
        )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/unit/test_suspensions.py -q`
Expected: PASS（新增 2 例 + 既有 31 例；既有两个端到端用例无候选，故行为不变）

- [ ] **Step 6: 跑集成文件确认无回归**

Run: `pytest tests/integration/test_data_pipeline.py -q`
Expected: PASS。`StubAdapter` 的日线帧**不带** `pre_close`，`_carries_proof_chain` 为假 → 零候选、零追加抓取，所有既有用例行为不变。

- [ ] **Step 7: Commit**

```bash
cd ~/work/program/stock
git add src/stock_quant/data_pipeline.py tests/unit/test_suspensions.py
git commit -m "fix: anchor suspension runs that open the window with a proof-only deep fetch

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Task 4: 文档（D1 命令 + ADR-006 + 索引 + 运维记录）

**Files:**
- Modify: `RUNBOOK.md`（76 行的命令）
- Create: `docs/adr/006-corporate-action-window-scope.md`
- Modify: `docs/adr/DECISIONS_INDEX.md`（表格新增一行）
- Modify: `docs/operations/2026-09-14-blocking-gap-root-cause.md`（末尾追加一节）

**Interfaces:**
- Consumes: Task 1-3 的实现（ADR 的 Evidence 一节指向它们的测试文件）
- Produces: 无代码接口

- [ ] **Step 1: 改 RUNBOOK 的更新命令**

把 `RUNBOOK.md` 的这一行：

```bash
setsid nohup python -m stock_quant data update --start 2015-01-01 --root project \
    > /tmp/update.log 2>&1 < /dev/null &
```

改成：

```bash
setsid nohup python -m stock_quant data update --start 2015-01-05 --root project \
    > /tmp/update.log 2>&1 < /dev/null &
```

并在该命令行下面的说明列表里追加一条（与既有 `- ` 条目同层级、同风格）：

```markdown
- **`--start` 必须落在日历的开市日上**：`2015-01-05` 是当前
  `trading_calendar` 证据里的第一个开市日。写成 `2015-01-01` 不会拉坏数据，但会让
  验收的 `date_window_completeness` 以 `window_not_calendar_complete` 记一条
  FAIL —— 声明窗口比日历证据更早。CLI/build **不**对它做硬校验（理由见
  [ADR-006](docs/adr/006-corporate-action-window-scope.md) 之外的
  `docs/superpowers/specs/2026-09-15-data-layer-residual-defects-design.md` §3）。
```

- [ ] **Step 2: 写 ADR-006**

新建 `docs/adr/006-corporate-action-window-scope.md`：

```markdown
---
status: accepted
date: 2026-09-15
decision: A quarantined corporate action counts against a symbol/window only when one of its known dates falls inside that window; a pre-window announcement on an implemented record is excluded, which supersedes the filter's "keep an implemented record with a missing ex-date" stance for that case.
affects:
  - src/stock_quant/data_model/corporate_actions.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_quality/models.py
---

# 006 — Corporate-action window scope

## Context

`_coverage_verdict` writes one coverage row per `(symbol, window)` and answers
that row's question with the symbol's *whole* quarantine history. The two
scopes disagree: a 1998 plan the supplier reports without any date is not an
event in 2015-2026, yet it marked that entire window `UNTRUSTED`.

The repository already disagreed with itself about this. The break layer,
`adjusted_bar._merge_quarantine_breaks`, skips a row whose `ex_date` is `None`
(`continue`) — a dateless record is not a transition and cannot break any
window. The coverage layer meanwhile painted the whole `[start, end]` as a
break. Aligning the two is a consistency fix, not a relaxation: the same
evidence now decides both layers.

Measured on the published dataset (`CURRENT = 1d6e43b4…`, 2026-09-14): 71
symbols read `UNTRUSTED`, and 46 of them are poisoned *only* by records whose
every known date lies before 2015. 45 of those 46 hold dated in-window actions
in the published `corporate_action` table, i.e. the supplier does report their
in-window implementations as separate, dated rows.

## Decision

`quarantine_row_out_of_window_reason(row, start, end)` decides, in order, on
the first date the row actually knows:

1. **`ex_date` known** — inside the window it counts, outside it does not.
   A derivation: the ex-date *is* the transition.
2. **`record_date` known** (no ex-date) — the same. Also a derivation on this
   data, because a completed settlement's ex-date is never earlier than its
   record date (measured: 6,872 / 6,872 accepted facts carry both dates, lag
   1-13 days, zero negatives), so a record date outside the window places the
   ex-date outside it too.
3. **Neither, but an `announcement_date` before `start` on a record whose
   `status` is `implemented`** — excluded. This *is* a conditional relaxation
   and it supersedes the stance written in
   `filter_corporate_actions_to_window`'s docstring ("an implemented record
   with a missing ex-date remains for reconciliation to flag as a genuine
   defect"). An announcement *after* the window does **not** qualify: the
   event it announces may still settle inside the window.
4. **No known date at all** — kept. Fail closed.

The filter narrows only the coverage verdict's input. The published
`corporate_action_quarantine` table still carries every row, and each excluded
row leaves an INFO `quarantine_out_of_window` issue naming its symbol, window
and branch.

## Consequences

- A window's coverage verdict now answers a question about that window, and the
  break layer and the coverage layer agree on what a dateless record means.
- The exclusion is auditable but **quiet**: the INFO issue is not in
  `PUBLICATION_BLOCKING_CODES`, so a suppressed record does not block a publish.
  Branch 3's risk below is therefore the one thing about this decision that a
  reader must not miss.
- Symbols that hold no accepted in-window fact still read `FACTS_INCOMPLETE`
  after the exclusion — 2 of the 46 flip only as far as that, which is the
  correct outcome, not a regression.

## Rejected alternatives

- **Mark the window `UNTRUSTED` for any historical quarantine row.** The
  current behaviour. It makes 46 symbols permanently unverifiable for reasons
  that have nothing to do with the window, and it contradicts the break layer.
- **Exclude any record whose *every* date lies outside the window.** Broader
  than the decision and wrong in one direction: a row announced after the
  window can still settle inside it, and excluding it would hide a transition
  inside the window.
- **Exclude branch 3 silently (no ADR, no INFO issue).** It would be the one
  place in this change where a gate-visible outcome is dropped without a
  trace, which invariant 3 forbids.
- **Resolve the stale records at the source instead.** Correct in principle and
  out of scope: it needs supplier-side facts or a signed review, which is data
  work, not a code change.

## Risk this decision accepts

Branch 3 is the only place this change *widens* what may pass. If a stale plan
really did settle inside the window while the supplier's dated record for it is
missing, excluding the dateless row leaves the series clean and **unmarked** —
exactly the silent substitution invariant 5 forbids. Two things bound the risk
without eliminating it: a dateless row can never enter the adjustment recursion
(`_standardize_source` keys candidates on `(symbol, ex_date)`), so it cannot
mask an in-window transition it is not itself part of; and 45 of the 46 symbols
carry dated in-window actions in the published table, so the supplier reports
their in-window settlements separately. The residual risk is recorded here
rather than assumed away.

## Evidence

`tests/unit/test_corporate_action_normalize.py`
(`test_quarantine_row_out_of_window_reason_decides_by_first_known_date`,
`test_quarantine_row_out_of_window_reason_reads_pandas_date_cells`),
`tests/integration/test_data_pipeline.py`
(`test_update_ignores_quarantine_rows_whose_dates_predate_the_window`).
Measured footing and the 46 / 25 split:
`docs/operations/2026-09-14-blocking-gap-root-cause.md`. Design:
`docs/superpowers/specs/2026-09-15-data-layer-residual-defects-design.md` §5.
```

- [ ] **Step 3: 登记进索引**

`docs/adr/DECISIONS_INDEX.md` 的表格末尾（005 那行之后）追加：

```markdown
| [006 Corporate-action window scope](006-corporate-action-window-scope.md) | accepted | 2026-09-15 | `src/stock_quant/data_model/corporate_actions.py`, `src/stock_quant/data_pipeline.py`, `src/stock_quant/data_quality/models.py` | quarantine, coverage, window scope, ADR-006, supersedes a filter policy | You change how a quarantined corporate action counts against a symbol/window, or the coverage verdict. |
```

- [ ] **Step 4: 追加运维记录**

在 `docs/operations/2026-09-14-blocking-gap-root-cause.md` 末尾追加：

```markdown

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
```

- [ ] **Step 5: 跑治理检查**

Run: `pytest tests/unit/test_context_governance_docs.py -v`
Expected: PASS（新 ADR 的 frontmatter、索引登记与运维文档均被该套件扫描）

- [ ] **Step 6: Commit**

```bash
cd ~/work/program/stock
git add RUNBOOK.md docs/adr/006-corporate-action-window-scope.md docs/adr/DECISIONS_INDEX.md docs/operations/2026-09-14-blocking-gap-root-cause.md
git commit -m "docs: fix the update window start and record the coverage window scope decision

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 完成前的收尾（人工核对，不提交）

- [ ] `ruff check` 对四个被改的源文件通过（本仓库从不要求 repo-wide `ruff format --check`）：
  `ruff check src/stock_quant/data_pipeline.py src/stock_quant/data_model/corporate_actions.py src/stock_quant/data_quality/models.py tests/unit/test_suspensions.py tests/integration/test_data_pipeline.py tests/unit/test_corporate_action_normalize.py`
- [ ] `git status --short` 里除本计划列出的文件外，没有别的改动被 `git add` 进任何一次提交。
- [ ] `git log --oneline -4` 是四个本计划的提交，工作树里无关的在途修改（`PROJECT_MEMORY.md`、`README.md`、`project/**` …）仍在工作树里、未被触碰。
- [ ] 未运行任何联网/出版命令；`project/data/standardized/CURRENT` 未变（`cat project/data/standardized/CURRENT` 仍是 `1d6e43b4…`）。

## 本轮之后仍待你决定的事

- 是否把本轮的设计与计划文档本身提交（规格 `docs/superpowers/specs/2026-09-15-…-design.md`
  与本计划 —— Task 1 的 Step 6 只提交了规格中 §5.4 的那一处改动，两份文档的其余部分
  仍是未跟踪/未提交状态）。
- 两处过期文档（`docs/operations/2026-09-14-wf-oos-stage-result-and-diagnostics.md`
  与 `PROJECT_MEMORY.md` §8.4 仍称 `CURRENT = 1709eddb…`）的更正属独立一笔，需另行授权。
- 发布一个新数据集版本、在新版本上重跑 9 项验收以实测 §0 的预期效果 —— 需你显式批准。
