# 时点成分过滤可用化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让冻结的 CSI300 成分事实在现有 30 只人工池上真正参与选股（消除池内前视偏差），并补上 `after_delist_date` 边界的机制覆盖。

**Architecture:** 从当前数据集读出 `universe_membership`，逐行裁剪到 `security_master` 的交集，重发一个**新**数据集版本并写出配套的 `custom_csi300_ic_tradable` 定义；实验规格通过 `universe_definition` 引用它，由 runner 的预检门禁校验。退市边界以合成夹具单测覆盖，不引入外部数据。

**Tech Stack:** Python 3.10 / pandas 2.3.3（主环境）、pydantic、duckdb、pytest、PyYAML。

**设计依据：** `docs/superpowers/specs/2026-09-11-csi300-pit-filter-design.md`

## Global Constraints

- **不生成 canonical `csi300`**。新池一律 `custom_csi300_ic_tradable`，且任何报告/注释都不得暗示它是无偏 CSI300。
- **不改动 `custom_csi300_ic`** 的定义文件与其 membership facts（那是忠实记录，留给未来的无偏池工作）。
- **不改 `security_master`**。
- **禁止跑全量测试**。只跑本计划点名的具名测试文件。
- **禁止跑 `tests/integration`**。本计划的全部验收在 `tests/unit/` 与具名命令内完成。
- **主环境永不 `import index_constitution`**。
- `project/data/` 整个被 `.gitignore`；数据集不可变，只能发布**新版本**，产物一律不入库。
- 既有 `python -m stock_quant research run` 在本环境**必定失败**（无 ACCEPTED 验收记录，属方案二）。本计划的运行验收一律用 `python -m stock_quant backtest momentum_60d --engineering`。
- 报告必须标注 `UNTRUSTED` 与"仍存在幸存者偏差"。
- 提交信息、代码标识符用英文；注释与文档用中文（沿用仓库现状）。

## 已实测的前提（探针结果，2026-09-11）

在写本计划前已用当前数据集（`af5799ae…`）实跑裁剪路径，结果如下，实现时应对得上：

| 项 | 实测值 |
| --- | --- |
| 原 membership | 1221 行 / 948 只唯一标的 |
| **裁剪后** | **31 行 / 28 只唯一标的** |
| 裁剪后 `membership_content_hash` | `756db257b6a13846c4452fd2e7504c54636705ca4f6acb4b712d092b549a4255` |
| 定义 `version` | `6405c180e810c2c1…` |
| `status` 分布 | 28 `active`、3 `removed` |
| `reason` 分布 | 25 `regular_rebalance`、6 `initial_constituent` |
| 被排除的两只 | `300001.SZ`、`688001.SH`（master 在册、从未入选 CSI300） |
| `validate_membership_facts(..., master=…)` | **0 issue，0 FATAL** |
| 数据集 `daily_bar` 区间 | 2015-01-05 ~ 2026-08-28 |
| 每年 6/1 的成员数 | 2015:14, 2016:16, 2017:16, 2018:17, 2019:18, 2020:18, 2021:21, 2022:26, 2023:25, 2024:26, 2025:28, 2026:28 |

**两条关键结论：**

1. spec 里标记的头号风险 `UNIVERSE_DELISTING_ENDPOINT_UNPROVEN` 经实测**不触发**
   （被 removal 的只有 3 行，reason 全是 `regular_rebalance`，不属于终止类）。
2. **裁剪必须同时重贴 `universe_id`。** 只过滤行是不够的——`UniverseResolver.__init__`
   （`src/stock_quant/research/universe.py:180`）要求每条 resolved fact 的
   `universe_id` 等于定义的 `universe_id`，否则直接 `ValueError`。所以
   `custom_csi300_ic` 的行要改标成 `custom_csi300_ic_tradable`，
   `membership_table_sha256` 也随之从上文那个值变成
   `756db257…`。**上表的 hash 是重贴之后的值。**

成员数逐年从 14 涨到 28，说明过滤确实在按信号日裁，而不是恒等于全集。

---

### Task 1: `after_delist_date` 边界机制覆盖

问题 6 的机制覆盖。现有测试只覆盖 `before_list_date`
（`tests/integration/test_data_pipeline.py:1196`、`:1213`），`after_delist_date`
分支从未被触发过——当前 `security_master` 的 `delist_date` 全为 `None`。

把 `_master_bar_boundary_issues` 的实现抽成模块级纯函数（它**不使用 `self`**），
使该分支无需构造 `DataPipeline`（其 `__init__` 需要真实 config 根）即可单测。

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（在 `:382` 的 `class DataPipeline:` 之前插入纯函数；`:844-892` 的方法改为委托）
- Test: `tests/unit/test_master_bar_boundaries.py`（新建）

**Interfaces:**
- Produces: `master_bar_boundary_issues(master: pd.DataFrame, daily: pd.DataFrame) -> list[QualityIssue]`
  —— 读取 `master` 的 `symbol` / `list_date` / `delist_date` 与 `daily` 的
  `symbol` / `trade_date`，返回 `code=CODE_MASTER_BAR_BOUNDARY` 的 `Severity.WARNING` 列表。
  `DataPipeline._master_bar_boundary_issues` 保留原签名并委托给它。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_master_bar_boundaries.py`：

```python
"""合成夹具覆盖 security_master 的 bar 边界 WARNING。

真实数据集里 30 只标的的 ``delist_date`` 全为空，``after_delist_date``
分支永远不会被触发，所以这条分支只能用构造数据验证。
"""

from datetime import date

import pandas as pd

from stock_quant.data_pipeline import (
    CODE_MASTER_BAR_BOUNDARY,
    master_bar_boundary_issues,
)
from stock_quant.data_quality.models import Severity


def _master(symbol: str, *, list_date=None, delist_date=None) -> pd.DataFrame:
    return pd.DataFrame(
        [{"symbol": symbol, "list_date": list_date, "delist_date": delist_date}]
    )


def _daily(symbol: str, trade_date) -> pd.DataFrame:
    return pd.DataFrame([{"symbol": symbol, "trade_date": trade_date}])


def test_bar_after_delist_date_is_flagged():
    """退市后的 bar 与 master 事实矛盾，报 WARNING 而不是静默通过。"""
    issues = master_bar_boundary_issues(
        _master(
            "600005.SH",
            list_date=date(1999, 8, 3),
            delist_date=date(2017, 2, 13),
        ),
        _daily("600005.SH", date(2017, 2, 14)),
    )
    assert [issue.code for issue in issues] == [CODE_MASTER_BAR_BOUNDARY]
    assert issues[0].severity is Severity.WARNING
    assert issues[0].symbol == "600005.SH"
    assert issues[0].details["boundary"] == "after_delist_date"
    assert issues[0].details["delist_date"] == "2017-02-13"


def test_bar_before_list_date_is_flagged():
    """回归：上市前的 bar 仍按原语义报 WARNING。"""
    issues = master_bar_boundary_issues(
        _master("600000.SH", list_date=date(2021, 11, 20)),
        _daily("600000.SH", date(2021, 11, 19)),
    )
    assert issues[0].details["boundary"] == "before_list_date"
    assert issues[0].details["list_date"] == "2021-11-20"


def test_bar_on_the_boundaries_is_clean():
    """两端是闭区间：恰好等于 list_date / delist_date 的行不算越界。"""
    master = _master(
        "600005.SH", list_date=date(2017, 2, 13), delist_date=date(2017, 2, 13)
    )
    assert master_bar_boundary_issues(master, _daily("600005.SH", date(2017, 2, 13))) == []


def test_symbol_without_bounds_is_skipped():
    """master 缺两侧边界的标的完全不参与判定。"""
    assert master_bar_boundary_issues(
        _master("600005.SH"), _daily("600005.SH", date(2017, 2, 14))
    ) == []


def test_empty_daily_returns_nothing():
    assert master_bar_boundary_issues(
        _master("600005.SH", delist_date=date(2017, 2, 13)), pd.DataFrame()
    ) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_master_bar_boundaries.py -q`
Expected: FAIL — `ImportError: cannot import name 'master_bar_boundary_issues'`

- [ ] **Step 3: 抽出模块级纯函数**

在 `src/stock_quant/data_pipeline.py` 中 `class DataPipeline:` 这一行**之前**插入
（`_as_date` / `_issue` 定义在文件后部，但 Python 在调用时才解析，前向引用无碍）：

```python
def master_bar_boundary_issues(
    master: pd.DataFrame, daily: pd.DataFrame
) -> list[QualityIssue]:
    """WARNING when a bar row lies outside its symbol's listing window.

    A bar before ``list_date`` (or after ``delist_date``) contradicts the
    refreshed master facts.  Missing rows are *not* this check's job -- they
    are classified separately by ``classify_missing_row``.  A WARNING never
    blocks publication; it surfaces a source-vs-master disagreement.
    """
    if daily is None or daily.empty:
        return []
    bounds = {
        str(row["symbol"]): (_as_date(row["list_date"]),
                             _as_date(row["delist_date"]))
        for row in master.to_dict("records")
    }
    issues: list[QualityIssue] = []
    for record in daily.to_dict("records"):
        symbol = str(record["symbol"])
        list_date, delist_date = bounds.get(symbol, (None, None))
        if list_date is None and delist_date is None:
            continue
        trade_date = _as_date(record["trade_date"])
        if trade_date is None:
            continue
        if list_date is not None and trade_date < list_date:
            boundary = "before_list_date"
        elif delist_date is not None and trade_date > delist_date:
            boundary = "after_delist_date"
        else:
            continue
        issues.append(
            _issue(
                Severity.WARNING,
                CODE_MASTER_BAR_BOUNDARY,
                symbol=symbol,
                trade_date=trade_date,
                table="daily_bar",
                details={
                    "boundary": boundary,
                    "list_date": list_date.isoformat()
                    if list_date is not None else None,
                    "delist_date": delist_date.isoformat()
                    if delist_date is not None else None,
                },
            )
        )
    return issues
```

然后把同文件 `:844-892` 的方法体整体替换为委托（**docstring 逐字保留**）：

```python
    def _master_bar_boundary_issues(
        self, master: pd.DataFrame, daily: pd.DataFrame
    ) -> list[QualityIssue]:
        """WARNING when a bar row lies outside its symbol's listing window.

        A bar before ``list_date`` (or after ``delist_date``) contradicts the
        refreshed master facts.  Missing rows are *not* this check's job -- they
        are classified separately by ``classify_missing_row``.  A WARNING never
        blocks publication; it surfaces a source-vs-master disagreement.
        """
        return master_bar_boundary_issues(master, daily)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/unit/test_master_bar_boundaries.py -q`
Expected: `5 passed`

- [ ] **Step 5: 确认未破坏既有调用方**

Run: `python -m pytest tests/unit/test_quality_checks.py -q`
Expected: 全绿（该文件覆盖 `data_pipeline` 的质量检查面）

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/data_pipeline.py tests/unit/test_master_bar_boundaries.py
git commit -m "test: cover the after_delist_date bar boundary with a pure validator"
```

---

### Task 2: 裁剪纯函数与定义构造

把裁剪逻辑写成**无 I/O 的纯函数**，这样它能被单测覆盖，而 `main()` 只负责读写。

**Files:**
- Create: `project/trim_universe_membership.py`
- Test: `tests/unit/test_trim_universe_membership.py`（新建）

**Interfaces:**
- Consumes: 无（本任务只写纯函数）
- Produces:
  - `trim_membership_rows(membership: pd.DataFrame, master_symbols: Collection[str]) -> pd.DataFrame`
  - `retarget_universe_id(rows: pd.DataFrame, universe_id: str) -> pd.DataFrame`
  - `facts_from_rows(rows: pd.DataFrame) -> list[MembershipFact]`
  - `build_tradable_definition(base: UniverseDefinition, *, facts: list[MembershipFact], coverage_start: date, coverage_end: date) -> dict`
  - 模块常量 `BASE_UNIVERSE_ID = "custom_csi300_ic"`、`TRADABLE_UNIVERSE_ID = "custom_csi300_ic_tradable"`、`TRADABLE_SUFFIX = "+tradable"`、`ROOT`

**为什么需要 `retarget_universe_id`：** `UniverseResolver.__init__`
（`src/stock_quant/research/universe.py:180`）逐条比对 resolved fact 的
`universe_id` 与定义的 `universe_id`，不一致就 `ValueError`
（实测：`resolved row belongs to universe_id 'custom_csi300_ic', not the
definition's 'custom_csi300_ic_tradable'`）。只过滤行而不重贴，实验必然在
预检阶段崩掉。

- [ ] **Step 1: 写失败测试**

创建 `tests/unit/test_trim_universe_membership.py`：

```python
"""裁剪纯函数：从冻结的 csi300 facts 派生可交易子集定义。"""

from datetime import date

import pandas as pd
import pytest

from stock_quant.data_model.universe_membership import (
    membership_content_hash,
    membership_frame,
)
from stock_quant.research.universe import UniverseDefinition
from trim_universe_membership import (
    BASE_UNIVERSE_ID,
    TRADABLE_UNIVERSE_ID,
    build_tradable_definition,
    facts_from_rows,
    retarget_universe_id,
    trim_membership_rows,
)

_SHA = "a" * 64


def _fact(symbol: str, *, start: date, end: date | None = None,
          reason: str = "regular_rebalance") -> dict:
    return {
        "universe_id": BASE_UNIVERSE_ID,
        "symbol": symbol,
        "raw_effective_from": start,
        "raw_effective_to": end,
        "announcement_date": start,
        "status": "active" if end is None else "removed",
        "reason": reason,
        "source": "index_constitution",
        "source_url": "https://example.invalid/csi300",
        "snapshot_sha256": _SHA,
        "source_document_sha256": _SHA,
    }


def _base_definition() -> UniverseDefinition:
    return UniverseDefinition(
        schema_version=1,
        universe_id=BASE_UNIVERSE_ID,
        rules_version="index_constitution-1.0.0+repairs-07e2f18d",
        membership_table_sha256=_SHA,
        evidence_summary_sha256=_SHA,
        coverage_start=date(2015, 1, 5),
        coverage_end=date(2026, 8, 28),
    )


def test_trim_keeps_only_master_symbols():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
        _fact("600005.SH", start=date(2015, 1, 5)),
        _fact("688001.SH", start=date(2019, 7, 22), reason="initial_constituent"),
    ])
    trimmed = trim_membership_rows(frame, {"000001.SZ", "688001.SH"})
    assert sorted(trimmed["symbol"]) == ["000001.SZ", "688001.SH"]
    assert list(trimmed.columns) == list(frame.columns)


def test_trim_resets_the_index():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
        _fact("000002.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    trimmed = trim_membership_rows(frame, {"000002.SZ"})
    assert list(trimmed.index) == [0]


def test_trim_to_empty_is_allowed():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    assert trim_membership_rows(frame, set()).empty


def test_retarget_relabels_every_row_without_touching_evidence():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
        _fact("000300.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    retargeted = retarget_universe_id(frame, TRADABLE_UNIVERSE_ID)
    assert set(retargeted["universe_id"]) == {TRADABLE_UNIVERSE_ID}
    assert retargeted["symbol"].tolist() == frame["symbol"].tolist()
    assert (
        retargeted["source_document_sha256"].tolist()
        == frame["source_document_sha256"].tolist()
    )
    # 原 frame 不被就地修改
    assert set(frame["universe_id"]) == {BASE_UNIVERSE_ID}


def test_retarget_changes_the_content_hash():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    before = membership_content_hash(facts_from_rows(frame))
    after = membership_content_hash(
        facts_from_rows(retarget_universe_id(frame, TRADABLE_UNIVERSE_ID))
    )
    assert after != before


def test_facts_from_rows_round_trips_dates_and_evidence():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    facts = facts_from_rows(frame)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.raw_effective_from == date(2015, 1, 5)
    assert fact.raw_effective_to is None
    assert fact.source_document_sha256 == _SHA
    assert membership_content_hash(facts) == membership_content_hash(
        facts_from_rows(frame)
    )


def test_build_tradable_definition_rewrites_id_hash_and_rules():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    facts = facts_from_rows(frame)
    base = _base_definition()
    document = build_tradable_definition(
        base,
        facts=facts,
        coverage_start=date(2015, 1, 5),
        coverage_end=date(2026, 8, 28),
    )
    assert document["universe_id"] == TRADABLE_UNIVERSE_ID
    assert document["rules_version"] == base.rules_version + "+tradable"
    assert document["evidence_summary_sha256"] == base.evidence_summary_sha256
    assert document["membership_table_sha256"] == membership_content_hash(facts)
    assert document["coverage_start"] == "2015-01-05"
    assert document["coverage_end"] == "2026-08-28"
    # 身份必须与原定义不同，否则冻结版本会撞车。
    assert document["membership_table_sha256"] != base.membership_table_sha256
    UniverseDefinition.model_validate(document)


def test_tradable_id_is_custom_prefixed():
    """护栏：派生定义不得占用 canonical id。"""
    assert TRADABLE_UNIVERSE_ID.startswith("custom_")
    assert TRADABLE_UNIVERSE_ID != "csi300"
    assert TRADABLE_UNIVERSE_ID != BASE_UNIVERSE_ID
```

（本文件不需要 `import pytest`；`UniverseDefinition` 只在定义测试里用到。）

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/unit/test_trim_universe_membership.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'trim_universe_membership'`

- [ ] **Step 3: 写纯函数**

创建 `project/trim_universe_membership.py`：

```python
"""Derive a tradable-universe dataset from the frozen csi300 membership facts.

The frozen ``custom_csi300_ic`` universe (1221 facts, 948 symbols) cannot
drive any experiment: the acceptance gate rejects every symbol absent from
``security_master`` (30 rows) as a FATAL ``UNIVERSE_UNKNOWN_SYMBOL`` and has
no bypass.  This script narrows the membership table to the intersection with
the published master and republishes the dataset, so the point-in-time filter
can actually run.

**This does not remove survivorship bias.**  The 30-name pool is hand-picked
and still listed; trimming it to 28 changes which symbols the filter can
*choose from*, not how the pool was chosen.  An unbiased CSI300 universe needs
daily bars for roughly 918 more symbols since 2015, which is external data
acquisition and out of scope here.  The derived universe is therefore named
``custom_csi300_ic_tradable`` and must never be presented as CSI300.

The membership facts keep every evidence field (``snapshot_sha256``,
``source_document_sha256``, ``source_url`` ...) across the filter, so the
evidence chain to the sealed snapshot stays intact.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    membership_content_hash,
    membership_frame,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.research.universe import (
    UniverseDefinition,
    load_universe_definition,
)

ROOT = Path(__file__).resolve().parent
BASE_UNIVERSE_ID = "custom_csi300_ic"
TRADABLE_UNIVERSE_ID = "custom_csi300_ic_tradable"
TRADABLE_SUFFIX = "+tradable"
_DATE_COLUMNS = ("raw_effective_from", "raw_effective_to", "announcement_date")


def trim_membership_rows(
    membership: pd.DataFrame, master_symbols: Collection[str]
) -> pd.DataFrame:
    """Keep only the membership rows whose symbol is in the tradable master."""
    keep = set(master_symbols)
    trimmed = membership[membership["symbol"].isin(keep)]
    return trimmed.reset_index(drop=True)


def retarget_universe_id(
    rows: pd.DataFrame, universe_id: str
) -> pd.DataFrame:
    """Relabel derived rows onto the tradable universe id.

    ``UniverseResolver`` requires every resolved fact to carry the definition's
    own ``universe_id``, so the derived facts cannot keep the base label.  Only
    that one identity column changes; every evidence field rides along.
    """
    relabelled = rows.copy()
    relabelled["universe_id"] = universe_id
    return relabelled


def facts_from_rows(rows: pd.DataFrame) -> list[MembershipFact]:
    """Rebuild validated facts from table rows, coercing dates to ``date``.

    The frame is the canonical ``universe_membership`` layout, so this is a
    pure round trip; every evidence field rides along unchanged.
    """
    facts: list[MembershipFact] = []
    for record in rows.to_dict("records"):
        payload = dict(record)
        for column in _DATE_COLUMNS:
            value = payload.get(column)
            payload[column] = (
                None
                if value is None or pd.isna(value)
                else pd.Timestamp(value).date()
            )
        facts.append(MembershipFact.model_validate(payload))
    return facts


def build_tradable_definition(
    base: UniverseDefinition,
    *,
    facts: list[MembershipFact],
    coverage_start: date,
    coverage_end: date,
) -> dict:
    """Render the definition document for the trimmed facts.

    ``coverage_start`` / ``coverage_end`` come from the dataset's ``daily_bar``
    window, mirroring ``build_csi300_universe.py`` -- coverage is what the
    dataset actually covers, never what the facts aspire to.
    """
    return {
        "schema_version": base.schema_version,
        "universe_id": TRADABLE_UNIVERSE_ID,
        "rules_version": base.rules_version + TRADABLE_SUFFIX,
        "membership_table_sha256": membership_content_hash(facts),
        "evidence_summary_sha256": base.evidence_summary_sha256,
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/unit/test_trim_universe_membership.py -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add project/trim_universe_membership.py tests/unit/test_trim_universe_membership.py
git commit -m "feat: add the csi300 membership trim pure functions"
```

---

### Task 3: `main()` 与真实发布

把纯函数接到数据集上：读 → 裁 → 重发 → 写定义。这一步**会真实地发布一个新数据集版本并推进 `CURRENT`**，这是设计意图。

`momentum_60d_offline_real_extended.yml` 用的是 `dataset_version: CURRENT`，所以它之后会跑到新版本上；但它的 `daily_bar` 逐字节未变，且它没有 `universe_definition`（不读 membership 表），因此结果不受影响。

**Files:**
- Modify: `project/trim_universe_membership.py`（追加 `main()` 与 `__main__` 块）

**Interfaces:**
- Consumes: Task 2 的 `trim_membership_rows` / `facts_from_rows` / `build_tradable_definition`
- Produces: `main() -> None`；副作用 = 发布新数据集版本 + 写
  `project/configs/universes/custom_csi300_ic_tradable.yml`

- [ ] **Step 1: 追加 `main()`**

在 `project/trim_universe_membership.py` 末尾追加：

```python
def main() -> None:
    """Publish the trimmed dataset and write the matching definition."""
    publisher = DatasetPublisher(ROOT)
    version = publisher.current().version
    print(f"base dataset_version={version}")

    definition_path = (
        ROOT / "configs" / "universes" / f"{BASE_UNIVERSE_ID}.yml"
    )
    base = load_universe_definition(definition_path)

    reader = DatasetReader(ROOT)
    with reader.open(version) as dataset:
        tables = {name: dataset.read(name) for name in dataset.tables}

    master = tables["security_master"]
    membership = tables["universe_membership"]
    print(
        f"membership rows={len(membership)} "
        f"symbols={membership['symbol'].nunique()} "
        f"master symbols={master['symbol'].nunique()}"
    )

    trimmed = retarget_universe_id(
        trim_membership_rows(membership, set(master["symbol"])),
        TRADABLE_UNIVERSE_ID,
    )
    facts = facts_from_rows(trimmed)
    print(
        f"trimmed rows={len(trimmed)} symbols={trimmed['symbol'].nunique()} "
        f"status={trimmed['status'].value_counts().to_dict()} "
        f"reason={trimmed['reason'].value_counts().to_dict()}"
    )

    daily = tables["daily_bar"]
    coverage_start = pd.to_datetime(daily["trade_date"]).min().date()
    coverage_end = pd.to_datetime(daily["trade_date"]).max().date()
    definition = build_tradable_definition(
        base,
        facts=facts,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )

    tables["universe_membership"] = membership_frame(facts)
    published = publisher.publish(tables, QualityReport())
    print(f"dataset_version={published.version}")
    print(f"membership_table_sha256={definition['membership_table_sha256']}")

    out_path = (
        ROOT / "configs" / "universes" / f"{TRADABLE_UNIVERSE_ID}.yml"
    )
    header = (
        "# Frozen universe definition generated by "
        "trim_universe_membership.py.\n"
        f"# Derived from {BASE_UNIVERSE_ID} by keeping only the symbols\n"
        "# present in the dataset's security_master.  NOT an unbiased\n"
        "# CSI300 universe: the underlying pool is hand-picked and still\n"
        "# listed, so survivorship bias remains.\n"
    )
    out_path.write_text(
        header + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"definition={out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行**

Run: `cd project && python trim_universe_membership.py`
Expected 输出（数值必须与"已实测的前提"表一致）：

```
base dataset_version=af5799ae4e62...
membership rows=1221 symbols=948 master symbols=30
trimmed rows=31 symbols=28 status={'active': 28, 'removed': 3} reason={'regular_rebalance': 25, 'initial_constituent': 6}
dataset_version=<一个全新的 64 位 hex>
membership_table_sha256=756db257b6a13846c4452fd2e7504c54636705ca4f6acb4b712d092b549a4255
definition=/.../project/configs/universes/custom_csi300_ic_tradable.yml
```

**若 `trimmed symbols` 不是 28，或 hash 与上表不符，停下来报告，不要继续。**

- [ ] **Step 3: 核对定义文件与门禁**

Run:

```bash
cd project && python - <<'PY'
from pathlib import Path

import pandas as pd

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe_membership import SecurityMasterBoundary
from stock_quant.research.acceptance.checks import (
    evaluate_index_membership_evidence,
    read_membership_table,
)
from stock_quant.research.universe import load_universe_definition

root = Path(".")
definition = load_universe_definition(
    root / "configs" / "universes" / "custom_csi300_ic_tradable.yml"
)
print("universe_id:", definition.universe_id)
print("version:", definition.version[:16])

version = DatasetPublisher(root).current().version
with DatasetReader(root).open(version) as ds:
    frame = read_membership_table(ds)
    master = ds.read("security_master")
    calendar = ds.read("trading_calendar")

assert set(frame["universe_id"]) == {"custom_csi300_ic_tradable"}, "relabel failed"

boundaries = {}
for record in master.to_dict("records"):
    raw = record.get("list_date")
    boundaries[str(record["symbol"])] = SecurityMasterBoundary(
        symbol=str(record["symbol"]),
        list_date=None if raw is None or pd.isna(raw) else pd.Timestamp(raw).date(),
    )

# master 必须传进来：master=None 会整段跳过符号校验，等于没验。
result = evaluate_index_membership_evidence(
    frame, definition=definition, calendar=calendar,
    expected_sizes={}, master=boundaries,
)
print("accepted:", result.accepted, "error_codes:", result.error_codes)
assert result.accepted, result.summary
print("preflight gate: OK")
PY
```

Expected: `universe_id: custom_csi300_ic_tradable`，
`accepted: True`，`error_codes: ()`，`preflight gate: OK`。

- [ ] **Step 4: 提交**

```bash
git add project/trim_universe_membership.py project/configs/universes/custom_csi300_ic_tradable.yml
git commit -m "feat: publish the tradable csi300 subset and its definition"
```

> `project/data/**` 已在 `.gitignore` 内；**不要**试图把数据集产物加进提交。

---

### Task 4: 实验规格与端到端验收

**Files:**
- Create: `project/configs/experiments/momentum_60d_pit_tradable.yml`

**Interfaces:**
- Consumes: Task 3 产出的定义 `custom_csi300_ic_tradable`
- Produces: 一个可复现的 ENGINEERING 实验运行

- [ ] **Step 1: 写规格**

创建 `project/configs/experiments/momentum_60d_pit_tradable.yml`：

```yaml
# 时点成分过滤的 ENGINEERING 验收规格。
#
# 与 momentum_60d_offline_real_extended.yml 相同的信任语义，唯一区别是启用
# universe_definition：runner 在算实验身份之前先做预检，把每个信号日的候选
# 收窄到「当时确实是 CSI300 成分」的标的上。
#
# 股票池仍是人工挑选的 30 只（裁剪后 28 只），所以幸存者偏差依然存在；
# 本规格只消除池内的前视偏差。非投资建议，非可信绩效声明。
hypothesis: >-
  把选股候选限制在信号日当时确属 CSI300 成分的标的，会在同一批真实行情上
  改变组合构成与绩效路径；本规格用于验证时点过滤确实生效并可复现。
factor_versions:
  momentum_60d: 2.0.0
dataset_version: CURRENT
universe_version: CURRENT
universe_definition: custom_csi300_ic_tradable
trust_mode: engineering
data_acceptance_id: null
date_range:
  start_date: 2015-01-01
  end_date: 2026-08-21
train_validation_holdout_policy: not_applicable_engineering_mvp
preprocessing:
  winsorization: none
  standardization: none
portfolio_rule:
  name: top_n_equal_weight
  top_n: 10
  lot_size: 100
cost_scenarios:
  - zero_cost
  - commission_tax
  - full_cost
random_seed: 42
code_commit: unversioned
parent_experiment_ids: []
agent_id: null
```

- [ ] **Step 2: 先验过滤语义（不跑回测）**

Run:

```bash
cd project && python - <<'PY'
from datetime import date
from pathlib import Path

import pandas as pd

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe_membership import (
    SecurityMasterBoundary,
    resolve_memberships,
)
from stock_quant.research.acceptance.checks import read_membership_table
from stock_quant.research.universe import (
    UniverseResolver,
    load_universe_definition,
)
from trim_universe_membership import facts_from_rows

root = Path(".")
definition = load_universe_definition(
    root / "configs" / "universes" / "custom_csi300_ic_tradable.yml"
)
version = DatasetPublisher(root).current().version
with DatasetReader(root).open(version) as ds:
    frame = read_membership_table(ds)
    master = ds.read("security_master")

boundaries = {}
for record in master.to_dict("records"):
    raw = record.get("list_date")
    boundaries[str(record["symbol"])] = SecurityMasterBoundary(
        symbol=str(record["symbol"]),
        list_date=None if raw is None or pd.isna(raw) else pd.Timestamp(raw).date(),
    )

facts = facts_from_rows(frame)
resolver = UniverseResolver(
    definition, resolve_memberships(facts, boundaries), facts=facts
)
days = [
    date(2015, 6, 1), date(2018, 6, 1), date(2020, 6, 1),
    date(2022, 6, 1), date(2024, 6, 1), date(2026, 6, 1),
]
counts = {day.isoformat(): len(resolver.members_on(day)) for day in days}
print("member counts:", counts)
for day in days:
    members = resolver.members_on(day)
    assert "300001.SZ" not in members, day
    assert "688001.SH" not in members, day
print("non-members absent on all sampled days: OK")
assert len(set(counts.values())) > 1, "candidate count is constant; filter looks inert"
print("candidate count varies over time: OK")
PY
```

Expected（实测值）：

```
member counts: {'2015-06-01': 14, '2018-06-01': 17, '2020-06-01': 18, '2022-06-01': 26, '2024-06-01': 26, '2026-06-01': 28}
non-members absent on all sampled days: OK
candidate count varies over time: OK
```

注意 2022 与 2024 都是 26，6 天里有 5 个不同取值——断言的是"不是常数"，
不是"两两不同"。

- [ ] **Step 3: 跑 ENGINEERING 回测**

Run:

```bash
cd project && python -m stock_quant backtest momentum_60d \
    --spec configs/experiments/momentum_60d_pit_tradable.yml \
    --engineering --root .
```

Expected: 成功，打印 `experiment_id=…`、`debug=data/runs/debug/…`、
`trust=UNTRUSTED`。

**若失败在 `universe_acceptance` 阶段**，把完整的错误码与
`UniversePreflightFailed` 消息记下来停下来报告——那说明 Task 3 的
探针结论没能泛化到完整预检。

- [ ] **Step 4: 断言每信号日快照非空**

Run:

```bash
cd project && python - <<'PY'
import json, pathlib
runs = sorted(pathlib.Path("data/runs/debug").glob("*/factor_metadata.json"),
              key=lambda p: p.stat().st_mtime)
meta = json.loads(runs[-1].read_text())
print("file:", runs[-1])
snapshots = meta.get("daily_snapshots") or {}
print("signal days:", len(snapshots))
assert snapshots, "no per-signal-day snapshots persisted"
assert all(v for v in snapshots.values()), "empty membership snapshot on some day"
print("all snapshots non-empty: OK")
PY
```

Expected: `signal days: <N>`（N > 0）与 `all snapshots non-empty: OK`。

> 若 `factor_metadata.json` 的键名与此处不符，先打印它的顶层键，
> 按实际键名调整断言；**不要**为了让断言通过而删掉它。

- [ ] **Step 5: 提交**

```bash
git add project/configs/experiments/momentum_60d_pit_tradable.yml
git commit -m "feat: add the point-in-time tradable momentum spec"
```

---

### Task 5: 诊断报告

**Files:**
- Create: `docs/operations/2026-09-11-pit-tradable-filter.md`

**Interfaces:**
- Consumes: Task 3 的数据集版本与定义哈希、Task 4 的实验 id 与指标

- [ ] **Step 1: 写报告**

按 `docs/operations/2026-09-11-momentum60d-extended-diagnostic.md` 的体例写，
**必须**包含：

1. 顶部一句 **信任等级：UNTRUSTED**，并说明原因（无 ACCEPTED 验收记录）。
2. **明确写"仍存在幸存者偏差"**：池子是人工挑选的、仍在市的 30 只（裁剪后 28 只），
   本工作只消除池内前视偏差，**不是**无偏 CSI300。
3. 裁剪前后对照表：1221 行 / 948 只 → 31 行 / 28 只；被排除的
   `300001.SZ`、`688001.SH`。
4. 新数据集版本、`membership_table_sha256`、定义路径与 `universe_version`。
5. 与 `ab378f9d…`（未过滤）的对照：组合构成、换手、三情景累计收益的变化。
6. `after_delist_date` 覆盖的说明：真实数据上不触发，靠合成夹具验证机制。
7. 可复现命令（Task 3 Step 2、Task 4 Step 3）。

- [ ] **Step 2: 提交**

```bash
git add docs/operations/2026-09-11-pit-tradable-filter.md
git commit -m "docs: report the point-in-time tradable filter diagnostic"
```

---

## Self-Review

**Spec coverage**

| spec 章节 | 对应任务 |
| --- | --- |
| 组件 ① trim 脚本 | Task 2（纯函数）、Task 3（main + 发布） |
| 组件 ② 实验规格 | Task 4 |
| 组件 ③ 合成夹具单测 | Task 1 |
| 组件 ④ ENGINEERING 运行 + 报告 | Task 4 Step 3、Task 5 |
| 验收 1（唯一 symbol 数 = 28） | Task 3 Step 2 |
| 验收 2（跑通、快照哈希非空） | Task 4 Step 3、Step 4 |
| 验收 3（过滤确实在裁） | Task 4 Step 2 |
| 验收 4（`after_delist_date` 被断言触发） | Task 1 Step 4 |
| 验收 5（报告标注幸存者偏差） | Task 5 Step 1 |
| 已知风险（`UNIVERSE_DELISTING_ENDPOINT_UNPROVEN`） | 已实测不触发；Task 3 Step 2 断言 hash 复核 |
| **spec 未写但实测必需**：重贴 `universe_id` | Task 2 的 `retarget_universe_id` + Task 3 Step 3 的断言 |

**未覆盖 / 需注意**

- 任务里对 `factor_metadata.json` 键名的断言是**推测**，Task 4 Step 4 已写明
  按实际键名调整的处置，不会静默放过。
- `factor_metadata.json` 是否真的持久化了 `daily_snapshots` 键，本计划未事先验证；
  Task 4 Step 4 的兜底是"打印顶层键再调整"，而不是删断言。若该键根本不存在，
  这是 spec 验收标准 2 的一个缺口，需回头改 spec。
- 本计划不触碰问题 2/3/4/5/7，它们分属方案二、三。
