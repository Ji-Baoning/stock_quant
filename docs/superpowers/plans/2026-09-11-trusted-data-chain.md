# 可信数据链路实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出一个真实 `data update` 的数据集，让 `real-data-v1` 的 8 项自动检查全 PASS，备好 9 项人工核证证据，由 owner 发布 ACCEPTED 记录，并跑通正式通路 `research run`。

**Architecture:** 先写三个只读的 `project/` 工具（更新前置检查、两道关卡探针、人工核证证据包），再用它们护航一次真实的 2015–2026 全窗口 `data update`，最后走验收注册表并跑正式通路。工具全部复用 `stock_quant` 里的权威检查函数，不重新实现判定逻辑。

**Tech Stack:** Python 3.10、pandas、typer、pydantic、pytest、ruff。

## Global Constraints

- **验收窗口固定为 2015-01-01 至 2026-08-28**（与现有 11.6 年回测窗口一致）。
- 股票池是 `project/configs/universe.yml` 的 **30 只**；CURRENT 数据集的 `universe_membership` 必须是方案一裁剪后的 `custom_csi300_ic_tradable`（**28 只唯一 symbol / 31 行**）。
- **不得生成 canonical `csi300`**；不得改 `security_master`；不得改动已封存的 `custom_csi300_ic` 正典（1221 条 facts）。
- **数据集不可变，只能发布新版本。**
- 报告必须标注信任等级，且**必须写明幸存者偏差仍在**；验收记录只证明数据完整，不证明池子无偏。
- **禁止跑全量测试。禁止跑 `tests/integration`。** 只跑本计划点名的测试文件。
- `project/data/` 整个被 `.gitignore`；证据与数据集都只作为本地证据存在。
- 所有命令在 `project/` 下、以 `--root .` 执行（仓库既有工作流）。
- 代码与标识符用英文，注释与文档用中文（仓库既有风格：docstring 用英文）。

## 运行环境注意

写本计划时实测：`prepare_checklist`（`acceptance/service.py:72`）在**只有 3 GB 可用内存**
的机器上被 OOM killer 杀掉（exit 137）。该函数会遍历证据表并对 raw 快照重算哈希。

对本计划的影响：Task 3 的 `main()` 与 Task 5 的 `acceptance prepare` 都会加载全表。
**这不是本计划的缺陷**（`research run` 与 `backtest` 在同等数据上跑得通），
但执行 Task 3 与 Task 5 前应确认无其他大内存进程，且两步**分进程执行**而不是塞进一个脚本。
好消息是 `publish` 之前全部是只读的，OOM 后重跑不产生任何已发布状态。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `project/verify_update_readiness.py`（新增） | 更新前置检查：基线成员表、token、三个供应端点可达性 |
| `project/probe_dataset_gates.py`（新增） | 两道关卡：日线缺口、公司行为覆盖；附带 8 项自动检查预演 |
| `project/build_acceptance_evidence.py`（新增） | 生成 6 项可脚本化的人工核证证据，并回填清单草稿 |
| `tests/unit/test_verify_update_readiness.py`（新增） | 前置检查纯函数单测 |
| `tests/unit/test_probe_dataset_gates.py`（新增） | 关卡探针纯函数单测 |
| `tests/unit/test_build_acceptance_evidence.py`（新增） | 证据构建与清单回填单测 |
| `docs/operations/2026-09-11-trusted-data-chain.md`（新增） | 最终报告 |

三个工具都是**只读**的：不写 raw store、不发布数据集、不动 CURRENT 指针。唯二的写操作是
`build_acceptance_evidence.py` 写 `data/acceptance-evidence/<version>/` 下的证据文件与
owner 指定的清单 YAML，以及 owner 执行 `data acceptance publish` 时对验收注册表的追加写。

---

## 已实测的前提

| 事实 | 值 |
| --- | --- |
| 当前数据集 | `af5799ae4e62f94210e6751473fed8e14e38252fd03613baff4f6bb3af4a6b70` |
| 该数据集 `_missing_row_failures` 缺口 | **5,327**（`date_window_completeness` 必 FAIL） |
| 该数据集日线相对 raw 的缺失 | 2021+ 共 **4,823** 对 `(symbol, date)` |
| raw 快照 `0d5497f05ee2` 的 `000001.SZ` | 1371 行 = 2021-01-04..2026-08-28 的**全部**交易日 |
| `_REQUIRED_ROLE` | `{"tushare": True, "akshare": True, "baostock": False}` |
| 人工核证 | 9 项，见 `MANUAL_CHECK_CODES`（`src/stock_quant/research/acceptance/models.py:87`） |
| `external` 证据的校验方式 | sha256 必须等于 `summary` 字符串自身的 sha256（不联网抓取） |
| `local` 证据的校验方式 | 项目根内的相对路径，文件必须存在且 sha256 相符 |

---

## Task 1: 更新前置检查工具

**Files:**
- Create: `project/verify_update_readiness.py`
- Test: `tests/unit/test_verify_update_readiness.py`

**Interfaces:**
- Consumes: 无（本计划第一个任务）
- Produces: `ReadinessIssue`（`code: str`、`message: str`）、
  `baseline_issues(membership, *, universe_id, expected_symbols) -> list[ReadinessIssue]`、
  `token_issue(environ=None) -> ReadinessIssue | None`、
  `probe_endpoint(fetch, endpoint, symbol, start, end) -> tuple[ReadinessIssue | None, int]`、
  `report(issues, probes) -> str`、`main() -> int`。
  模块常量 `UPDATE_START`、`UPDATE_END`、`TRADABLE_UNIVERSE_ID`、
  `EXPECTED_TRADABLE_SYMBOLS`、`PROBE_SYMBOL`。

- [ ] **Step 1: 写失败的测试**

创建 `tests/unit/test_verify_update_readiness.py`：

```python
"""Unit tests for the pre-update readiness probe."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

from verify_update_readiness import (  # noqa: E402
    EXPECTED_TRADABLE_SYMBOLS,
    TRADABLE_UNIVERSE_ID,
    ReadinessIssue,
    baseline_issues,
    probe_endpoint,
    report,
    token_issue,
)


def _membership(universe_id: str, symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"universe_id": [universe_id] * len(symbols), "symbol": symbols}
    )


def test_baseline_issues_passes_for_the_trimmed_membership() -> None:
    symbols = [f"{index:06d}.SZ" for index in range(EXPECTED_TRADABLE_SYMBOLS)]
    assert baseline_issues(_membership(TRADABLE_UNIVERSE_ID, symbols)) == []


def test_baseline_issues_flags_an_untrimmed_universe_id() -> None:
    symbols = [f"{index:06d}.SZ" for index in range(EXPECTED_TRADABLE_SYMBOLS)]
    issues = baseline_issues(_membership("custom_csi300_ic", symbols))
    assert [issue.code for issue in issues] == [
        "BASELINE_UNIVERSE_ID_MISMATCH"
    ]


def test_baseline_issues_flags_a_wrong_symbol_count() -> None:
    issues = baseline_issues(_membership(TRADABLE_UNIVERSE_ID, ["000001.SZ"]))
    assert [issue.code for issue in issues] == [
        "BASELINE_SYMBOL_COUNT_MISMATCH"
    ]


def test_baseline_issues_flags_an_empty_membership() -> None:
    assert baseline_issues(None) == [
        ReadinessIssue(
            "BASELINE_MEMBERSHIP_EMPTY",
            "CURRENT dataset carries no universe_membership rows; "
            "run the plan-one trim first",
        )
    ]
    assert baseline_issues(pd.DataFrame({"universe_id": [], "symbol": []})) == (
        baseline_issues(None)
    )


def test_token_issue_flags_a_missing_token() -> None:
    assert token_issue({}) is not None
    assert token_issue({"TUSHARE_TOKEN": "   "}) is not None
    assert token_issue({"TUSHARE_TOKEN": "abc"}) is None


def test_probe_endpoint_reports_a_raising_supplier() -> None:
    def boom(endpoint, symbol, start, end):
        raise RuntimeError("endpoint down")

    issue, count = probe_endpoint(
        boom, "tushare.daily", "600519.SH", date(2015, 1, 1), date(2026, 8, 28)
    )
    assert isinstance(issue, ReadinessIssue)
    assert issue.code == "SOURCE_PROBE_FAILED"
    assert "RuntimeError" in issue.message
    assert count == 0


def test_probe_endpoint_accepts_an_empty_frame() -> None:
    def empty(endpoint, symbol, start, end):
        return pd.DataFrame()

    issue, count = probe_endpoint(
        empty,
        "cninfo_corporate_actions",
        "600519.SH",
        date(2015, 1, 1),
        date(2026, 8, 28),
    )
    assert issue is None
    assert count == 0


def test_report_marks_not_ready_when_issues_exist() -> None:
    text = report([ReadinessIssue("X", "boom")], {"tushare.daily": 3})
    assert "ready=false" in text
    assert "probe tushare.daily rows=3" in text
    assert "issue X boom" in text
    assert "ready=true" in report([], {})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_verify_update_readiness.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'verify_update_readiness'`

- [ ] **Step 3: 写最小实现**

创建 `project/verify_update_readiness.py`：

```python
"""Preflight the full-window data update before spending the network budget.

The 2015-2026 update walks 30 equity names plus two benchmarks through two
required suppliers and only publishes when every required fetch answered.  A
stale baseline (plan one not yet published), a missing token or a dead
endpoint would otherwise surface halfway through that run.  This script is
read-only: it never writes the raw store, never publishes and never advances
CURRENT.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.tushare import TushareSource

ROOT = Path(__file__).resolve().parent
UPDATE_START = date(2015, 1, 1)
UPDATE_END = date(2026, 8, 28)
TRADABLE_UNIVERSE_ID = "custom_csi300_ic_tradable"
EXPECTED_TRADABLE_SYMBOLS = 28
PROBE_SYMBOL = "600519.SH"


@dataclass(frozen=True)
class ReadinessIssue:
    """One precondition the update must satisfy before it is worth starting."""

    code: str
    message: str


def baseline_issues(
    membership: pd.DataFrame | None,
    *,
    universe_id: str = TRADABLE_UNIVERSE_ID,
    expected_symbols: int = EXPECTED_TRADABLE_SYMBOLS,
) -> list[ReadinessIssue]:
    """Require plan one's trimmed membership to already be CURRENT.

    ``data update`` carries ``universe_membership`` verbatim, so updating
    before the trim is published would freeze the untrimmed 1221-row table
    into the new dataset.
    """
    if membership is None or membership.empty:
        return [
            ReadinessIssue(
                "BASELINE_MEMBERSHIP_EMPTY",
                "CURRENT dataset carries no universe_membership rows; "
                "run the plan-one trim first",
            )
        ]
    ids = sorted({str(value) for value in membership["universe_id"]})
    issues: list[ReadinessIssue] = []
    if ids != [universe_id]:
        issues.append(
            ReadinessIssue(
                "BASELINE_UNIVERSE_ID_MISMATCH",
                f"CURRENT membership universe_id={ids}, expected "
                f"[{universe_id!r}]; run the plan-one trim first",
            )
        )
    found = int(membership["symbol"].nunique())
    if found != expected_symbols:
        issues.append(
            ReadinessIssue(
                "BASELINE_SYMBOL_COUNT_MISMATCH",
                f"CURRENT membership has {found} unique symbols, "
                f"expected {expected_symbols}",
            )
        )
    return issues


def token_issue(
    environ: Mapping[str, str] | None = None,
) -> ReadinessIssue | None:
    """Require a non-empty ``TUSHARE_TOKEN`` before any fetch is attempted."""
    source = os.environ if environ is None else environ
    if not str(source.get("TUSHARE_TOKEN", "")).strip():
        return ReadinessIssue(
            "TUSHARE_TOKEN_MISSING", "TUSHARE_TOKEN is unset or empty"
        )
    return None


def probe_endpoint(
    fetch: Callable[[str, str, date, date], pd.DataFrame],
    endpoint: str,
    symbol: str,
    start: date,
    end: date,
) -> tuple[ReadinessIssue | None, int]:
    """Probe one supplier endpoint once; return ``(issue, row_count)``.

    A supplier that answers with an empty frame is reachable, which is all a
    probe claims.  Whether 2015-2020 really carries events is the update's
    finding, not the probe's.
    """
    try:
        frame = fetch(endpoint, symbol, start, end)
    except Exception as error:  # noqa: BLE001 - a probe reports, never raises
        return (
            ReadinessIssue(
                "SOURCE_PROBE_FAILED",
                f"{endpoint} {symbol} {start}..{end}: "
                f"{type(error).__name__}: {error}",
            ),
            0,
        )
    return None, len(frame)


def report(issues: list[ReadinessIssue], probes: dict[str, int]) -> str:
    """Render the operator-facing verdict; ``ready`` only when issue-free."""
    lines = [f"ready={str(not issues).lower()}"]
    for name, count in sorted(probes.items()):
        lines.append(f"probe {name} rows={count}")
    for issue in issues:
        lines.append(f"issue {issue.code} {issue.message}")
    return "\n".join(lines)


def _fetch(source: object, endpoint: str, symbol: str, start: date, end: date):
    return source.fetch(DataRequest(endpoint, (symbol,), start, end, {})).frame


def main() -> int:
    publisher = DatasetPublisher(ROOT)
    version = publisher.current().version
    print(f"current dataset={version}")
    with DatasetReader(ROOT).open(version) as dataset:
        membership = (
            dataset.read("universe_membership")
            if "universe_membership" in dataset.tables
            else None
        )
    issues = baseline_issues(membership)
    missing_token = token_issue()
    if missing_token is not None:
        issues.append(missing_token)
    probes: dict[str, int] = {}
    if missing_token is None:
        sources = {
            "tushare.daily": (TushareSource(SourceConfig()), "daily"),
            "cninfo_corporate_actions": (
                AkShareSource(SourceConfig()),
                "cninfo_corporate_actions",
            ),
            "eastmoney_corporate_actions": (
                AkShareSource(SourceConfig()),
                "eastmoney_corporate_actions",
            ),
        }
        for name, (source, endpoint) in sources.items():
            issue, count = probe_endpoint(
                partial(_fetch, source),
                endpoint,
                PROBE_SYMBOL,
                UPDATE_START,
                UPDATE_END,
            )
            probes[name] = count
            if issue is not None:
                issues.append(issue)
    print(report(issues, probes))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/unit/test_verify_update_readiness.py -q`
Expected: `8 passed`

- [ ] **Step 5: lint 与提交**

```bash
ruff check project/verify_update_readiness.py tests/unit/test_verify_update_readiness.py
git add project/verify_update_readiness.py tests/unit/test_verify_update_readiness.py
git commit -m "feat: preflight the full-window data update"
```

---

## Task 2: 两道关卡探针

**Files:**
- Create: `project/probe_dataset_gates.py`
- Test: `tests/unit/test_probe_dataset_gates.py`

**Interfaces:**
- Consumes: 无（与 Task 1 平行，互不依赖）
- Produces: `Gap`（`symbol: str`、`trade_date: date`、`code: str`）、
  `GateVerdict`（`passed: bool`、`total: int`、`sample: tuple[Gap, ...]`）、
  `window_open_days(calendar, start, end) -> list[date]`、
  `gap_details(daily, master, calendar, start, end) -> list[Gap]`、
  `bar_gate(daily, master, calendar, start, end) -> GateVerdict`、
  `corporate_action_gate(coverage, symbols, start, end) -> tuple[bool, tuple[tuple[str, str], ...]]`、
  `main() -> int`。

- [ ] **Step 1: 写失败的测试**

创建 `tests/unit/test_probe_dataset_gates.py`：

```python
"""Unit tests for the two hard gates a real update must clear."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

from probe_dataset_gates import (  # noqa: E402
    bar_gate,
    corporate_action_gate,
    gap_details,
    window_open_days,
)

DAYS = [date(2015, 1, 5), date(2015, 1, 6), date(2015, 1, 7), date(2015, 1, 8)]


def _calendar(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {"calendar_date": days, "is_trading_day": [True] * len(days)}
    )


def _master(rows: list[tuple[str, date]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol for symbol, _ in rows],
            "list_date": [listed for _, listed in rows],
            "delist_date": [pd.NaT] * len(rows),
        }
    )


def _daily(pairs: list[tuple[str, date]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol for symbol, _ in pairs],
            "trade_date": [pd.Timestamp(day) for _, day in pairs],
        }
    )


def test_window_open_days_keeps_only_the_window() -> None:
    calendar = _calendar(DAYS + [date(2014, 12, 31), date(2016, 1, 1)])
    assert window_open_days(
        calendar, date(2015, 1, 6), date(2015, 1, 7)
    ) == [date(2015, 1, 6), date(2015, 1, 7)]


def test_gap_details_accepts_a_not_listed_gap() -> None:
    """A bar before the listing date is an accepted classification."""
    master = _master([("000001.SZ", date(2015, 1, 7))])
    daily = _daily([("000001.SZ", day) for day in DAYS[2:]])
    assert gap_details(daily, master, _calendar(DAYS), DAYS[0], DAYS[-1]) == []


def test_gap_details_flags_a_suspended_gap() -> None:
    """A listed name missing an open day is unexplained and must be flagged."""
    master = _master([("000001.SZ", date(2015, 1, 5))])
    daily = _daily([("000001.SZ", day) for day in DAYS if day != DAYS[1]])
    gaps = gap_details(daily, master, _calendar(DAYS), DAYS[0], DAYS[-1])
    assert [(gap.symbol, gap.trade_date, gap.code) for gap in gaps] == [
        ("000001.SZ", DAYS[1], "unknown_or_suspended")
    ]


def test_bar_gate_passes_on_a_complete_grid() -> None:
    master = _master([("000001.SZ", date(2015, 1, 5))])
    daily = _daily([("000001.SZ", day) for day in DAYS])
    verdict = bar_gate(daily, master, _calendar(DAYS), DAYS[0], DAYS[-1])
    assert verdict.passed is True
    assert verdict.total == 0
    assert verdict.sample == ()


def test_bar_gate_counts_agree_with_the_acceptance_checker() -> None:
    """The recount must never disagree with the gate it previews."""
    master = _master([("000001.SZ", date(2015, 1, 5)), ("600519.SH", date(2015, 1, 5))])
    daily = _daily(
        [
            ("000001.SZ", DAYS[0]),
            ("000001.SZ", DAYS[2]),
            ("600519.SH", DAYS[0]),
        ]
    )
    verdict = bar_gate(daily, master, _calendar(DAYS), DAYS[0], DAYS[-1])
    assert verdict.passed is False
    assert verdict.total == 5
    assert len(verdict.sample) == 5


def test_corporate_action_gate_reports_untrusted_symbols() -> None:
    trusted, reasons = corporate_action_gate(
        pd.DataFrame(), ("000001.SZ",), DAYS[0], DAYS[-1]
    )
    assert trusted is False
    assert reasons == (("000001.SZ", "SOURCE_NOT_REQUESTED"),)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_probe_dataset_gates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'probe_dataset_gates'`

- [ ] **Step 3: 写最小实现**

创建 `project/probe_dataset_gates.py`：

```python
"""Evaluate the two hard gates a real update must clear before acceptance.

Gate one: no unexplained missing bars over the window -- the check the
current dataset fails by 5,327 rows.  Gate two: trusted corporate-action
coverage tiling the window.  The script also previews the eight automated
acceptance checks, so a checklist is never prepared against a dataset that
cannot pass.  Read-only.

Both gates read the window from the same place the acceptance checker does
(``build_config.requested_start_date``/``resolved_end_date``), so a probe can
never silently disagree with the gate it previews; the spec window below is an
equality assertion, not the source of truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe import Universe
from stock_quant.data_quality.raw_checks import classify_missing_row
from stock_quant.research.acceptance.checks import (
    _ACCEPTED_MISSING_CODES,
    AcceptanceCheckInput,
    _missing_row_failures,
    _open_days,
    _window,
    run_automated_checks,
)
from stock_quant.research.trust import evaluate_corporate_action_trust

ROOT = Path(__file__).resolve().parent
#: The window the update must be requested with; asserted, never assumed.
WINDOW_START = date(2015, 1, 1)
WINDOW_END = date(2026, 8, 28)


@dataclass(frozen=True)
class Gap:
    """One missing bar the acceptance gate would reject, with its class."""

    symbol: str
    trade_date: date
    code: str


@dataclass(frozen=True)
class GateVerdict:
    """Gate one's verdict: whether any gap remains, and a bounded sample."""

    passed: bool
    total: int
    sample: tuple[Gap, ...]


def _as_date(value: object) -> date | None:
    """Coerce one frame cell to a ``date``; missing/NaT reads as ``None``."""
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def window_open_days(
    calendar: pd.DataFrame, start: date, end: date
) -> list[date]:
    """The open days inside ``[start, end]``, ascending."""
    return [day for day in _open_days(calendar) if start <= day <= end]


def gap_details(
    daily: pd.DataFrame,
    master: pd.DataFrame,
    calendar: pd.DataFrame,
    start: date,
    end: date,
) -> list[Gap]:
    """Every missing bar the acceptance gate would reject, with its class.

    Reuses the acceptance checker's own classification vocabulary so this
    tool can never accept a bar the gate would reject.
    """
    facts = {
        str(row.get("symbol")): (
            _as_date(row.get("list_date")),
            _as_date(row.get("delist_date")),
        )
        for row in master.to_dict("records")
    }
    present = {
        (str(row.get("symbol")), _as_date(row.get("trade_date")))
        for row in daily.to_dict("records")
    }
    gaps: list[Gap] = []
    for symbol in sorted(facts):
        list_date, delist_date = facts[symbol]
        for day in window_open_days(calendar, start, end):
            if (symbol, day) in present:
                continue
            code = classify_missing_row(
                trade_date=day,
                list_date=list_date,
                delist_date=delist_date,
                is_trading_day=True,
                primary_present=False,
                validation_present=False,
            )
            if code not in _ACCEPTED_MISSING_CODES:
                gaps.append(Gap(symbol, day, code))
    return gaps


def bar_gate(
    daily: pd.DataFrame,
    master: pd.DataFrame,
    calendar: pd.DataFrame,
    start: date,
    end: date,
) -> GateVerdict:
    """Gate one, cross-checked against the acceptance checker's own count.

    The detail list is recomputed here so a failing dataset can be reported
    per symbol and per year; the total is then asserted equal to what
    ``_missing_row_failures`` counts, so this preview can never drift from
    the gate it previews.
    """
    details = gap_details(daily, master, calendar, start, end)
    reported = _missing_row_failures(
        daily, master, window_open_days(calendar, start, end)
    )
    total = next(
        (
            int(row[1])
            for row in reported
            if row[0] == "unexplained_missing_rows"
        ),
        0,
    )
    if total != len(details):
        raise AssertionError(
            f"gap recount {len(details)} disagrees with the acceptance "
            f"checker's {total}"
        )
    return GateVerdict(
        passed=not details, total=len(details), sample=tuple(details[:20])
    )


def corporate_action_gate(
    coverage: pd.DataFrame | None,
    symbols: tuple[str, ...],
    start: date,
    end: date,
) -> tuple[bool, tuple[tuple[str, str], ...]]:
    """Gate two: every universe symbol has trusted coverage tiling the window."""
    decision = evaluate_corporate_action_trust(coverage, symbols, start, end)
    return decision.trusted, tuple(
        (reason.symbol, reason.code) for reason in decision.reasons
    )


def main() -> int:
    symbols = Universe.from_yaml(ROOT / "configs" / "universe.yml").symbols
    version = DatasetPublisher(ROOT).current().version
    manifest = json.loads(
        (
            ROOT / "data" / "standardized" / version / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )
    build = manifest.get("build_config")
    if not isinstance(build, dict) or "requested_start_date" not in build:
        print(
            f"dataset={version} carries no data-update build window "
            "(offline rebuild); nothing to gate"
        )
        return 1
    start, end = _window(build)
    if (start, end) != (WINDOW_START, WINDOW_END):
        print(
            f"build window {start}..{end} != spec window "
            f"{WINDOW_START}..{WINDOW_END}"
        )
        return 1
    print(
        f"dataset={version} window={start}..{end} universe_symbols={len(symbols)}"
    )
    with DatasetReader(ROOT).open(version) as dataset:
        daily = dataset.read("daily_bar")
        master = dataset.read("security_master")
        calendar = dataset.read("trading_calendar")
        coverage = (
            dataset.read("corporate_action_coverage")
            if "corporate_action_coverage" in dataset.tables
            else None
        )
    bars = bar_gate(daily, master, calendar, start, end)
    print(f"bar_gate passed={str(bars.passed).lower()} unexplained={bars.total}")
    for gap in bars.sample:
        print(f"  gap {gap.symbol} {gap.trade_date.isoformat()} {gap.code}")
    trusted, reasons = corporate_action_gate(coverage, symbols, start, end)
    print(
        f"corporate_action_gate trusted={str(trusted).lower()} "
        f"reasons={len(reasons)}"
    )
    for symbol, code in reasons[:20]:
        print(f"  coverage {symbol} {code}")
    checks = run_automated_checks(AcceptanceCheckInput(ROOT, version))
    for check in checks:
        print(f"check {check.code} {check.status.value} {check.summary}")
    failed = [
        check.code for check in checks if check.status.value == "FAIL"
    ]
    passed = bars.passed and trusted and not failed
    print(f"verdict passed={str(passed).lower()}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/unit/test_probe_dataset_gates.py -q`
Expected: `6 passed`

> 若 `test_bar_gate_counts_agree_with_the_acceptance_checker` 的期望值 5 与实际不符，
> 说明 `_missing_row_failures` 的计数口径与 `gap_details` 有出入——
> **停下来核对，不要改断言去迁就实现**。两者必须逐条一致，这是本工具的全部价值。

- [ ] **Step 5: lint 与提交**

```bash
ruff check project/probe_dataset_gates.py tests/unit/test_probe_dataset_gates.py
git add project/probe_dataset_gates.py tests/unit/test_probe_dataset_gates.py
git commit -m "feat: probe the bar-completeness and corporate-action gates"
```

---

## Task 3: 人工核证证据包

**Files:**
- Create: `project/build_acceptance_evidence.py`
- Test: `tests/unit/test_build_acceptance_evidence.py`

**Interfaces:**
- Consumes: 无
- Produces: `EvidenceFile`（`name: str`、`text: str`）、
  `source_row_count_evidence(manifest, snapshots, *, trading_days) -> EvidenceFile`
  （`manifest` 与 `snapshots` 都是 **manifest JSON 反序列化后的 mapping**，
  不是 pydantic 模型——`build_config.raw_snapshots` 落盘时是 dict 列表）、
  `missing_reason_evidence(daily, master, calendar, start, end) -> EvidenceFile`、
  `security_master_evidence(master, *, limit=30) -> EvidenceFile`、
  `benchmark_evidence(daily, calendar, benchmarks, start, end) -> EvidenceFile`、
  `corporate_action_evidence(corporate_action, *, limit=20) -> EvidenceFile`、
  `secret_scan_evidence(paths, *, root, limit=50) -> EvidenceFile`、
  `apply_evidence(checklist, *, root, evidence_root, names) -> dict`、
  `main(version, checklist_path) -> int`。
  模块常量 `MECHANISABLE`、`OPERATOR_ONLY`、`EVIDENCE_DIRNAME`。

**背景：** 9 项人工核证里 6 项可由脚本确定性取证，3 项需要本项目无法自产的外部佐证
（官方交易所日历、第二价格源、官方交易规则生效日），后三项保持 FAIL 占位留给 owner。

- [ ] **Step 1: 写失败的测试**

创建 `tests/unit/test_build_acceptance_evidence.py`：

```python
"""Unit tests for the scripted acceptance evidence pack."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

from build_acceptance_evidence import (  # noqa: E402
    MECHANISABLE,
    OPERATOR_ONLY,
    apply_evidence,
    benchmark_evidence,
    corporate_action_evidence,
    missing_reason_evidence,
    secret_scan_evidence,
    security_master_evidence,
    source_row_count_evidence,
)
from stock_quant.research.acceptance.models import MANUAL_CHECK_CODES  # noqa: E402

DAYS = [date(2015, 1, 5), date(2015, 1, 6), date(2015, 1, 7)]


def _calendar(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {"calendar_date": days, "is_trading_day": [True] * len(days)}
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_mechanisable_and_operator_only_tile_the_policy_vocabulary() -> None:
    assert set(MECHANISABLE) | set(OPERATOR_ONLY) == set(MANUAL_CHECK_CODES)
    assert set(MECHANISABLE) & set(OPERATOR_ONLY) == set()


def test_source_row_count_evidence_lists_tables_and_snapshots() -> None:
    manifest = {"tables": {"daily_bar": {"row_count": 7}}}
    empty = source_row_count_evidence(manifest, (), trading_days=3)
    assert empty.name == "source_row_counts.json"
    payload = json.loads(empty.text)
    assert payload["tables"] == {"daily_bar": 7}
    assert payload["trading_days"] == 3
    assert payload["raw_snapshots"] == []


def test_source_row_count_evidence_reads_manifest_snapshot_mappings() -> None:
    """Snapshots arrive as the manifest's dicts, never as model objects."""
    manifest = {"tables": {"daily_bar": {"row_count": 7}}}
    snapshot = {
        "source": "tushare",
        "endpoint": "daily",
        "request_key": "600519.SH",
        "file_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
    }
    payload = json.loads(
        source_row_count_evidence(manifest, [snapshot], trading_days=3).text
    )
    assert payload["raw_snapshots"] == [
        {
            "source": "tushare",
            "endpoint": "daily",
            "request_key": "600519.SH",
            "file_sha256": "a" * 64,
        }
    ]


def test_missing_reason_evidence_counts_accepted_classifications() -> None:
    master = pd.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "list_date": [pd.Timestamp("2015-01-06")],
            "delist_date": [pd.NaT],
        }
    )
    daily = pd.DataFrame(
        {
            "symbol": ["000001.SZ", "000001.SZ"],
            "trade_date": [pd.Timestamp(DAYS[1]), pd.Timestamp(DAYS[2])],
        }
    )
    payload = json.loads(
        missing_reason_evidence(
            daily, master, _calendar(DAYS), DAYS[0], DAYS[-1]
        ).text
    )
    assert payload["counts"] == {"not_listed": 1}
    assert payload["first_sample"] == {"not_listed": "000001.SZ@2015-01-05"}


def test_security_master_evidence_is_symbol_sorted_csv() -> None:
    master = pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "list_date": [pd.Timestamp("2001-08-27"), pd.Timestamp("1991-04-03")],
            "delist_date": [pd.NaT, pd.NaT],
        }
    )
    text = security_master_evidence(master).text
    assert text.splitlines()[0] == "symbol,list_date,delist_date"
    assert text.splitlines()[1].startswith("000001.SZ")


def test_benchmark_evidence_counts_covered_open_days() -> None:
    daily = pd.DataFrame(
        {
            "symbol": ["000300.SH", "000300.SH"],
            "trade_date": [pd.Timestamp(DAYS[0]), pd.Timestamp(DAYS[2])],
        }
    )
    payload = json.loads(
        benchmark_evidence(
            daily, _calendar(DAYS), ("000300.SH",), DAYS[0], DAYS[-1]
        ).text
    )
    assert payload["000300.SH"]["rows"] == 2
    assert payload["000300.SH"]["open_days"] == 3
    assert payload["000300.SH"]["missing_open_days"] == 1


def test_corporate_action_evidence_samples_facts_in_order() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "ex_date": [pd.Timestamp("2024-04-30"), pd.Timestamp("2021-06-11")],
            "status": ["implemented", "implemented"],
        }
    )
    text = corporate_action_evidence(frame).text
    assert text.splitlines()[1].startswith("000001.SZ")


def test_secret_scan_flags_a_credential_like_line(tmp_path: Path) -> None:
    (tmp_path / "clean.txt").write_text("nothing to see\n", encoding="utf-8")
    (tmp_path / "leaky.txt").write_text(
        "TUSHARE_TOKEN=abcdef\n", encoding="utf-8"
    )
    payload = json.loads(
        secret_scan_evidence(
            [tmp_path / "clean.txt", tmp_path / "leaky.txt"], root=tmp_path
        ).text
    )
    assert payload["scanned"] == 2
    assert payload["hits"] == [{"path": "leaky.txt", "line": 1}]


def test_apply_evidence_passes_scripted_rows_and_leaves_the_rest(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    (evidence_root / "pack.json").write_text("{}\n", encoding="utf-8")
    checklist = {
        "manual_checks": [
            {"code": "secret_scan", "status": "FAIL", "summary": "x"},
            {"code": "benchmark_sample", "status": "FAIL", "summary": "x"},
        ]
    }
    patched = apply_evidence(
        checklist,
        root=tmp_path,
        evidence_root=evidence_root,
        names={"secret_scan": "pack.json"},
    )
    rows = {row["code"]: row for row in patched["manual_checks"]}
    assert rows["benchmark_sample"]["status"] == "FAIL"
    assert rows["secret_scan"]["status"] == "PASS"
    reference = rows["secret_scan"]["evidence"][0]
    assert reference["kind"] == "local"
    assert reference["reference"] == "evidence/pack.json"
    assert reference["sha256"] == _sha256("{}\n")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_build_acceptance_evidence.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_acceptance_evidence'`

- [ ] **Step 3: 写最小实现**

创建 `project/build_acceptance_evidence.py`：

```python
"""Build the local evidence pack for the operator's acceptance checklist.

Six of the nine manual checks are facts a script can gather deterministically:
row-count relationships, missing-bar classifications, sampled corporate-action
facts, benchmark coverage, security-master rows and a credential scan.  The
other three need corroboration this project cannot produce by itself (an
official exchange calendar, a second price source, official rule effective
dates); they stay FAIL placeholders for the operator to complete.

Nothing here writes a dataset, the raw store or CURRENT.  The only writes are
evidence files under ``data/acceptance-evidence/<version>/`` and the checklist
YAML the operator named.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd
import yaml

from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_quality.raw_checks import classify_missing_row
from stock_quant.research.acceptance.checks import (
    _ACCEPTED_MISSING_CODES,
    _open_days,
    _window,
)

ROOT = Path(__file__).resolve().parent
#: The window the update must be requested with; asserted, never assumed.
WINDOW_START = date(2015, 1, 1)
WINDOW_END = date(2026, 8, 28)
EVIDENCE_DIRNAME = "acceptance-evidence"

#: Manual checks a script can evidence on its own.
MECHANISABLE = (
    "benchmark_sample",
    "corporate_action_sample",
    "missing_reason_sample",
    "security_master_sample",
    "secret_scan",
    "source_row_count_sample",
)
#: Manual checks that need corroboration this project cannot produce.
OPERATOR_ONLY = (
    "exchange_calendar_sample",
    "cross_source_price_sample",
    "trading_rule_effective_dates",
)

_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(tushare_token|api[_-]?key|secret|password|passwd|authorization)"
    r"\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class EvidenceFile:
    """One evidence artifact: its name under the pack and its bytes."""

    name: str
    text: str


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _as_date(value: object) -> date | None:
    """Coerce one frame cell to a ``date``; missing/NaT reads as ``None``."""
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _columns(frame: pd.DataFrame, wanted: Sequence[str]) -> list[str]:
    return [name for name in wanted if name in frame.columns]


def source_row_count_evidence(
    manifest: Mapping[str, object],
    snapshots: Sequence[Mapping[str, object]],
    *,
    trading_days: int,
) -> EvidenceFile:
    """Row counts of every published table beside every bound raw snapshot.

    Both arguments are the manifest JSON as parsed, not the pydantic models
    the acceptance checker wraps it in: ``build_config.raw_snapshots`` is
    written as a list of mappings.
    """
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("dataset manifest has no tables mapping")
    counts = {
        str(name): int(entry.get("row_count", -1))
        for name, entry in sorted(tables.items())
        if isinstance(entry, dict)
    }
    bindings = sorted(
        (
            {
                "source": str(row.get("source")),
                "endpoint": str(row.get("endpoint")),
                "request_key": str(row.get("request_key")),
                "file_sha256": str(row.get("file_sha256")),
            }
            for row in snapshots
        ),
        key=lambda item: (
            item["source"],
            item["endpoint"],
            item["request_key"],
        ),
    )
    return EvidenceFile(
        "source_row_counts.json",
        _json(
            {
                "trading_days": int(trading_days),
                "tables": counts,
                "raw_snapshots": bindings,
            }
        ),
    )


def missing_reason_evidence(
    daily: pd.DataFrame,
    master: pd.DataFrame,
    calendar: pd.DataFrame,
    start: date,
    end: date,
) -> EvidenceFile:
    """Classification counts for every absent bar over the window."""
    facts = {
        str(row.get("symbol")): (
            _as_date(row.get("list_date")),
            _as_date(row.get("delist_date")),
        )
        for row in master.to_dict("records")
    }
    present = {
        (str(row.get("symbol")), _as_date(row.get("trade_date")))
        for row in daily.to_dict("records")
    }
    grid = [day for day in _open_days(calendar) if start <= day <= end]
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}
    for symbol in sorted(facts):
        list_date, delist_date = facts[symbol]
        for day in grid:
            if (symbol, day) in present:
                continue
            code = classify_missing_row(
                trade_date=day,
                list_date=list_date,
                delist_date=delist_date,
                is_trading_day=True,
                primary_present=False,
                validation_present=False,
            )
            counts[code] = counts.get(code, 0) + 1
            samples.setdefault(code, f"{symbol}@{day.isoformat()}")
    return EvidenceFile(
        "missing_reasons.json",
        _json(
            {
                "accepted_codes": sorted(_ACCEPTED_MISSING_CODES),
                "counts": counts,
                "first_sample": samples,
            }
        ),
    )


def security_master_evidence(
    master: pd.DataFrame, *, limit: int = 30
) -> EvidenceFile:
    """A deterministic sample of security-master listing facts."""
    wanted = _columns(
        master, ("symbol", "list_date", "delist_date", "status", "source")
    )
    frame = master.sort_values("symbol", kind="stable").head(limit)
    return EvidenceFile(
        "security_master_sample.csv", frame[wanted].to_csv(index=False)
    )


def benchmark_evidence(
    daily: pd.DataFrame,
    calendar: pd.DataFrame,
    benchmarks: Sequence[str],
    start: date,
    end: date,
) -> EvidenceFile:
    """Per-benchmark coverage of the window's open days."""
    grid = [day for day in _open_days(calendar) if start <= day <= end]
    payload: dict[str, object] = {}
    for symbol in benchmarks:
        rows = daily[daily["symbol"] == symbol]
        dates = {_as_date(value) for value in rows["trade_date"]}
        payload[str(symbol)] = {
            "rows": int(len(rows)),
            "first": min(dates).isoformat() if dates else None,
            "last": max(dates).isoformat() if dates else None,
            "open_days": len(grid),
            "missing_open_days": len([day for day in grid if day not in dates]),
        }
    return EvidenceFile("benchmark_coverage.json", _json(payload))


def corporate_action_evidence(
    corporate_action: pd.DataFrame, *, limit: int = 20
) -> EvidenceFile:
    """A deterministic sample of the reconciled corporate-action facts."""
    wanted = _columns(
        corporate_action,
        (
            "symbol",
            "ex_date",
            "record_date",
            "cash_dividend_per_share",
            "bonus_share_ratio",
            "capitalization_ratio",
            "source",
            "status",
        ),
    )
    frame = corporate_action.sort_values(
        ["symbol", "ex_date"], kind="stable"
    ).head(limit)
    return EvidenceFile(
        "corporate_action_sample.csv", frame[wanted].to_csv(index=False)
    )


def secret_scan_evidence(
    paths: Sequence[Path], *, root: Path, limit: int = 50
) -> EvidenceFile:
    """Scan the acceptance artifacts for credential-looking lines."""
    hits: list[dict[str, object]] = []
    for path in sorted(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _CREDENTIAL_PATTERN.search(line):
                hits.append(
                    {"path": str(path.relative_to(root)), "line": number}
                )
        if len(hits) >= limit:
            break
    return EvidenceFile(
        "secret_scan.json",
        _json({"scanned": len(paths), "hits": hits[:limit]}),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def apply_evidence(
    checklist: Mapping[str, object],
    *,
    root: Path,
    evidence_root: Path,
    names: Mapping[str, str],
) -> dict:
    """Turn the mechanisable manual rows into verified PASS rows.

    ``names`` maps a manual check code to the evidence file it cites; every
    other manual row is left exactly as ``acceptance prepare`` wrote it, so a
    checklist that is not yet publishable says so plainly.
    """
    manual: list[dict] = []
    for row in checklist["manual_checks"]:  # type: ignore[index]
        name = names.get(str(row["code"]))
        if name is None:
            manual.append(dict(row))
            continue
        path = evidence_root / name
        manual.append(
            {
                "code": str(row["code"]),
                "status": "PASS",
                "summary": f"{row['code']}: scripted evidence {name}",
                "details": {},
                "evidence": [
                    {
                        "kind": "local",
                        "reference": str(path.relative_to(root)),
                        "sha256": _sha256_file(path),
                        "summary": (
                            f"{name} generated by "
                            "project/build_acceptance_evidence.py"
                        ),
                    }
                ],
            }
        )
    patched = dict(checklist)
    patched["manual_checks"] = manual
    return patched


def main(version: str, checklist_path: Path) -> int:
    evidence_root = ROOT / "data" / EVIDENCE_DIRNAME / version
    evidence_root.mkdir(parents=True, exist_ok=True)
    with DatasetReader(ROOT).open(version) as dataset:
        daily = dataset.read("daily_bar")
        master = dataset.read("security_master")
        calendar = dataset.read("trading_calendar")
        corporate_action = dataset.read("corporate_action")
    manifest = json.loads(
        (
            ROOT / "data" / "standardized" / version / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )
    build = manifest.get("build_config")
    if not isinstance(build, dict) or "requested_start_date" not in build:
        raise SystemExit(f"dataset {version} carries no data-update build window")
    start, end = _window(build)
    if (start, end) != (WINDOW_START, WINDOW_END):
        raise SystemExit(
            f"build window {start}..{end} != spec "
            f"{WINDOW_START}..{WINDOW_END}"
        )
    # The manifest stores these as plain mappings, not ``RawSnapshotBinding``.
    snapshots = list(build.get("raw_snapshots", []))
    open_days = [day for day in _open_days(calendar) if start <= day <= end]
    project = yaml.safe_load((ROOT / "configs" / "project.yml").read_text())
    benchmarks = tuple(str(s) for s in project["benchmark_symbols"])
    written = [
        source_row_count_evidence(
            manifest, snapshots, trading_days=len(open_days)
        ),
        missing_reason_evidence(daily, master, calendar, start, end),
        security_master_evidence(master),
        benchmark_evidence(daily, calendar, benchmarks, start, end),
        corporate_action_evidence(corporate_action),
    ]
    for file in written:
        (evidence_root / file.name).write_text(file.text, encoding="utf-8")
    scanned = [evidence_root / file.name for file in written]
    scanned.append(checklist_path)
    scan = secret_scan_evidence(scanned, root=ROOT)
    (evidence_root / scan.name).write_text(scan.text, encoding="utf-8")
    names = {
        "source_row_count_sample": "source_row_counts.json",
        "missing_reason_sample": "missing_reasons.json",
        "security_master_sample": "security_master_sample.csv",
        "benchmark_sample": "benchmark_coverage.json",
        "corporate_action_sample": "corporate_action_sample.csv",
        "secret_scan": "secret_scan.json",
    }
    checklist = yaml.safe_load(checklist_path.read_text(encoding="utf-8"))
    patched = apply_evidence(
        checklist, root=ROOT, evidence_root=evidence_root, names=names
    )
    checklist_path.write_text(
        yaml.safe_dump(patched, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    for row in patched["manual_checks"]:
        print(f"manual {row['code']} {row['status']}")
    print(f"evidence_dir={evidence_root.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    import typer

    typer.run(main)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/unit/test_build_acceptance_evidence.py -q`
Expected: `9 passed`

- [ ] **Step 5: lint 与提交**

```bash
ruff check project/build_acceptance_evidence.py tests/unit/test_build_acceptance_evidence.py
git add project/build_acceptance_evidence.py tests/unit/test_build_acceptance_evidence.py
git commit -m "feat: build the scripted acceptance evidence pack"
```

---

## Task 4: 执行真实全窗口数据更新

> **本任务由 operator 执行，不交给 subagent。** 它是一次约一小时的联网长任务，
> 且失败会消耗 tushare 配额。

**Files:**
- 无代码改动；产出是一次更新与新数据集版本

**Interfaces:**
- Consumes: Task 1 的 `project/verify_update_readiness.py`、Task 2 的 `project/probe_dataset_gates.py`
- Produces: 新的 CURRENT 数据集版本号（后续任务引用）

- [ ] **Step 1: 确认方案一已发布为 CURRENT**

```bash
cd project
python project/verify_update_readiness.py
```

Expected: `ready=true`，且 `probe tushare.daily rows=<>0`、
两个公司行为端点 probe 出现（行数可为 0）。

**若报 `BASELINE_UNIVERSE_ID_MISMATCH`**：方案一尚未执行。
**停下**，先执行方案一（`docs/superpowers/plans/2026-09-11-csi300-tradable-filter.md`），
再回到本任务。原因：`data update` 原样携带 `universe_membership`
（`src/stock_quant/data_pipeline.py:1002` 的 `_read_baseline`），
顺序颠倒会把 1221 行未裁剪表带进新数据集。

- [ ] **Step 2: 记录更新前的基线**

```bash
python -c "
from pathlib import Path
from stock_quant.data_model.dataset import DatasetPublisher
print('before=' + DatasetPublisher(Path('.')).current().version)
"
```

记下这个版本号，写报告时要用。

- [ ] **Step 3: 执行更新**

```bash
python -m stock_quant data update --start 2015-01-01 --end 2026-08-28 --root .
```

Expected: 打印 `run_id=…`、`resolved_end_date=…`，然后
`CURRENT -> <新版本号>`。

**若以 `source_fetch_failed` 或任何 FATAL 结束**：更新不会发布数据集，
CURRENT 保持不变。确认配额后重跑；**不要**改成 `--engineering` 或离线重建——
那样产出的数据集 `build_config.origin` 不是 `data_update`，
`source_role_health` 一定 FAIL。

- [ ] **Step 4: 跑两道关卡**

```bash
python project/probe_dataset_gates.py
```

Expected: `bar_gate passed=true unexplained=0`，
`corporate_action_gate trusted=true reasons=0`，
8 项 `check … PASS`，`verdict passed=true`。

**若 `bar_gate passed=false`**：这是方案二设计好的停止出口。
记录 `unexplained` 总数与前 20 条样本，**就地停止本方案**，
把缺口分布写进报告并重新设计。**不要**为了让检查通过去裁剪窗口或放宽口径。

**若 `corporate_action_gate trusted=false`**：按打印出的
`coverage <symbol> <code>` 逐条定位。`SOURCE_FETCH_FAILED` 需确认端点与配额后重跑更新；
`SOURCE_CONFLICT` 需走 `project/configs/corporate_action_reviews.yml` 的人工复核流程；
`COVERAGE_INCOMPLETE` 说明覆盖没有铺满窗口，属更新窗口问题。

- [ ] **Step 5: 提交产物记录**

本任务不产生可提交的代码。把新版本号与两道关卡的输出写入
`docs/operations/2026-09-11-trusted-data-chain.md` 的草稿（Task 6 完成）。

---

## Task 5: 验收清单准备与签署

> **本任务 Step 1–3 由 operator 执行，Step 4 的口头确认由 owner 完成。**

**Files:**
- Create: `project/data/acceptance/<version>-checklist.yml`（本地，gitignored）

**Interfaces:**
- Consumes: Task 3 的 `project/build_acceptance_evidence.py`、Task 4 的新版本号
- Produces: `data/acceptance/` 下的 ACCEPTED 记录

- [ ] **Step 1: 生成清单草稿**

```bash
cd project
python -m stock_quant data acceptance prepare \
    --version <Task 4 的新版本号> \
    --operator <owner 的 operator id> \
    --output data/acceptance/<version>-checklist.yml \
    --root .
```

Expected: `checklist=<version>-checklist.yml`。

- [ ] **Step 2: 回填 6 项可脚本化的证据**

```bash
python project/build_acceptance_evidence.py \
    <Task 4 的新版本号> \
    data/acceptance/<version>-checklist.yml
```

Expected: 6 行 `manual <code> PASS`，3 行 `manual <code> FAIL`。

- [ ] **Step 3: 人工完成剩余 3 项**

`exchange_calendar_sample`、`cross_source_price_sample`、
`trading_rule_effective_dates` 需要本项目无法自产的外部佐证，由 owner 逐项填入
`evidence` 条目：

- `kind: external` 时，`sha256` 必须等于 `summary` 字符串自身的 sha256
  （`_verify_evidence_reference` 只校验这个自洽哈希，不联网抓取）：

  ```bash
  python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "<summary 文本>"
  ```

- `kind: local` 时，`reference` 必须是项目根内的相对路径，且 `sha256` 与文件字节相符。

`cross_source_price_sample` 的第二价格源：baostock 已停用，可用公开行情页作为
`external` 佐证，或先用 `project/probe_dataset_gates.py` 的输出锁定待抽查的最大跨源差异。

- [ ] **Step 4: owner 审阅并发布**

owner 逐条确认 9 项人工核证的证据属实后执行：

```bash
python -m stock_quant data acceptance publish \
    --checklist data/acceptance/<version>-checklist.yml --root .
```

Expected: `acceptance_id=<64 hex>` 与 `decision=ACCEPTED`。

**若 `decision=REJECTED`**：命令会打印 `reason=` 行并已把 REJECTED 记录写入注册表
（注册表历史会解释失败原因）。修正后重新 `prepare`——**不要**直接改已发布的记录。

**签字是 owner 的行为。AI 不代为认定任何一项人工核证。**

---

## Task 6: 正式通路验证与报告

> **Step 1–2 由 operator 执行。**

**Files:**
- Create: `docs/operations/2026-09-11-trusted-data-chain.md`

**Interfaces:**
- Consumes: Task 5 的 ACCEPTED 记录、Task 4 的新版本号
- Produces: 报告文档

- [ ] **Step 1: 确认验收记录可见**

```bash
cd project
python -m stock_quant data acceptance show --version <新版本号> --root .
```

Expected: 至少一行 `… ACCEPTED …`。

- [ ] **Step 2: 跑正式通路**

```bash
python -m stock_quant research run \
    --spec configs/experiments/momentum_60d_pit_tradable.yml --root .
```

Expected: 不再出现
`no valid real-data-v1 acceptance for dataset …` 而终止；
命令走完 `universe_acceptance` 与数据验收门禁。

**若仍在验收门禁失败**：说明记录没有绑定到该数据集版本或已过期，
回到 Task 5 核对 `dataset_manifest_sha256`。

- [ ] **Step 3: 写报告**

创建 `docs/operations/2026-09-11-trusted-data-chain.md`，必须包含：

1. 更新前后两个数据集版本号；
2. 旧数据集缺口的实测证据（4,823 对 `(symbol, date)`；`_missing_row_failures` = 5,327）；
3. 新数据集的 `bar_gate` 与 `corporate_action_gate` 结果，以及 8 项自动检查逐项状态；
4. ACCEPTED 记录的 `acceptance_id`、`operator_id`、`created_at`；
5. 正式通路的实际输出；
6. **信任等级段落**：明确区分「数据已 ACCEPTED」与「股票池仍是人工挑选的 30 只、
   幸存者偏差仍在」，并说明本次验收**不**证明策略有效性；
7. 问题 2 的外部阻塞留痕（csindex 500/404、`index_weight` 无权限）。

- [ ] **Step 4: lint 与提交**

```bash
git add docs/operations/2026-09-11-trusted-data-chain.md
git commit -m "docs: report the trusted data chain"
```

---

## 自审记录

**规格覆盖：**

| 规格组件 | 对应任务 |
| --- | --- |
| ① 真实全窗口数据更新 | Task 4 Step 3 |
| ② 第一关：缺口实测 | Task 2（工具）+ Task 4 Step 4 |
| ③ 第二关：公司行为实测 | Task 2（工具）+ Task 4 Step 4 |
| ④ 自动检查预演 | Task 2 的 `main()` + Task 4 Step 4 |
| ⑤ 人工核证证据包 + 草稿清单 | Task 3 + Task 5 Step 1–3 |
| ⑥ owner 审阅签署发布 | Task 5 Step 4 |
| ⑦ 正式通路验证 | Task 6 Step 1–2 |
| ⑧ 报告 | Task 6 Step 3 |
| 前置约束（方案一先行） | Task 4 Step 1 |
| 已知风险 2（tushare 配额） | Task 1 + Task 4 Step 3 |

**类型一致性：** `ReadinessIssue`、`Gap`、`GateVerdict`、`EvidenceFile`
在定义任务与使用任务中字段名一致。

**逐符号实测核对（写计划时对着源码核对，非推测）：**

| 符号 | 核对结果 |
| --- | --- |
| `classify_missing_row` | 关键字参数与计划一致；`MISSING_UNKNOWN_OR_SUSPENDED = "unknown_or_suspended"`（`data_quality/models.py:65`） |
| `_open_days(calendar)` | `research/acceptance/checks.py:647`，返回 `list[date]`（注意 `raw_checks.py:416` 另有一个同名函数，签名不同——**必须从 checks 导入**） |
| `_missing_row_failures` | 返回 `[["unexplained_missing_rows", str(n)], …]`，故 `int(row[1])` 取值正确 |
| `_window(build)` | `checks.py:626`，读 `build_config.requested_start_date` / `resolved_end_date`——**门禁的窗口来自构建配置，不是常量** |
| `_universe(root)` | `checks.py:637`，`Universe.from_yaml(configs/universe.yml)` 且用 `universe.symbols` |
| `Universe` | 在 **`stock_quant.data_model.universe`**（`research.universe` 里是 `UniverseDefinition`）——初稿导入路径写错，已改 |
| `evaluate_corporate_action_trust` | 返回 `CorporateActionTrustDecision(trusted, reasons)`；空/None coverage → 每个 symbol 一条 `SOURCE_NOT_REQUESTED` |
| `run_automated_checks` | 返回 **`tuple[CheckResult, ...]`**；`CheckStatus` 为 str 枚举，值 `PASS`/`FAIL` |
| `AcceptanceChecklist` | `extra="forbid", frozen=True`；顶层键为 `automated_checks`/`manual_checks`，行键 `code`/`status`/`summary`/`details`/`evidence` |
| `DatasetReader.open(v)` | 上下文管理器，`.tables` 为 tuple，`.read(name)` 取名 |
| `DataRequest` | 位置序 `(endpoint, symbols, start_date, end_date, params)`；`fetch()` → `FetchResult.frame` |
| 表名 | `daily_bar`/`security_master`/`trading_calendar`/`corporate_action`/`corporate_action_coverage`/`universe_membership` 均为 manifest 实际表名 |
| `build_config.raw_snapshots` | 落盘为 **dict 列表**（不是 `RawSnapshotBinding` 模型）——初稿按属性访问，已改为 `.get()` |

**实测验证（不是断言，是跑过的）：** 用本计划 `gap_details` 的同一段逻辑
对着当前数据集 `af5799ae` 复算，得 **5,327**，与
`_missing_row_failures(daily, master, grid)` 的 **5,327** 逐条一致
（`AGREE = True`），也等于规格 §「本轮新增实测发现」记录的值。
`Universe.from_yaml(...).symbols` 实测为 **30** 个 symbol 的 tuple。
Task 2 的 `bar_gate` 断言因此有实测依据。

**占位符扫描：** 无 TBD/TODO；所有代码步骤给出完整代码。

**已知的取舍：**

- 三个工具都 import 了 `checks.py` 的私有符号（`_open_days`、
  `_missing_row_failures`、`_ACCEPTED_MISSING_CODES`）。这是刻意的：
  它们必须与验收门禁用**同一套**判定逻辑，重新实现一份会立刻产生漂移。
  `bar_gate` 里那条断言就是把这种漂移变成硬失败。
- Task 4/5/6 是运维任务而非编码任务，标注为 operator 执行；
  计划的 TDD 循环只覆盖 Task 1–3。
