# Relay 交易日历主线化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 jiaoch relay 的 Tushare `trade_cal` 纳入每一次 `data update`，让日历与本轮行情、企业行为在同一原子发布中绑定，并在日历请求/解析/连续性校验失败时 FATAL 且不推进 `CURRENT`。

**Architecture:** 新增三个纯逻辑模块（span 代数与 manifest 证据校验、供应商原始日历事实的解析/两市比对/连续性、以及已有的 universe 判据扫描），`TushareSource` 增加 `trade_cal` endpoint；`DataPipeline.update` 在起点/终点解析完成之后、`_refresh_security_master` 之前插入一次日历刷新（两市 halo 请求 → 归一化校验 → 逐日比对 → 物化候选表 → 连续性 → 合并 spans），并把 `calendar_coverage` / `full_history_acceptance_start` / `universe_coverage_definition_hashes` 写进 `build_config`；`--end` 缺省时改用"已发布 `trading_calendar` 的最大日期"，删除全部时钟启发式发现逻辑。

**Tech Stack:** Python 3.11+、pandas、DuckDB、pydantic v2、typer、pytest；测试全部离线（stub `DataSource`、录制帧），真实 relay 只做独立小窗口 smoke probe。

**Spec:** `docs/superpowers/specs/2026-09-12-relay-trading-calendar-mainline-design.md`

## Global Constraints

- 有效窗口 = 物化窗口 = 闭区间 `[start, end]`；halo = `[start - 1 天, end + 1 天]`，**只用于校验**，绝不扩大物化窗口。
- `--end` 缺省时**只能**取已发布 `trading_calendar.calendar_date` 的最大值；已发布日历为空 → FATAL（`calendar_empty_requires_explicit_end`），要求操作员显式传 `--end`。不得从当前自然日/自然日前若干日或其他时钟启发式推断终点。
- 日历请求、原始校验、两市比对、连续性任一失败 → FATAL，保留旧 `CURRENT`；本轮已成功写入 raw store 的 `trade_cal` 快照**保留**；失败运行不得写标准化日历、不得产生部分 coverage span、不得用旧 raw 响应完成下一次运行（每次运行都必须重新请求供应商）。
- 不改变 transport 政策：relay 是唯一正常发布路径；official 仅在既有 break-glass 条件（`TUSHARE_OFFICIAL_PUBLISH_ENV=1`）下显式使用；绝不自动回退。本计划不修改 `src/stock_quant/data_sources/tushare_transport.py`。
- bootstrap 必须仍能离线工作（`bootstrap_dataset` 不联网），且 bootstrap 数据集必须带一条 `bootstrap_seed` coverage span。
- `DATASET_BUILD_CONTRACT_VERSION` **保持 `1`，不 bump**（`docs/superpowers/specs/2026-09-12-data-source-role-division-design.md:338` 的先例：bump 会让每一份已发布数据集在 `source_role_health` 上立刻失败）。代价必须写进代码注释：同一版本号下从此存在两种 `build_config` 形状，判据是**有没有 `calendar_coverage` 键**；旧形状（无该键）按 legacy 兼容读取，但其 `data validate` / 接受链**必然**报 `calendar_coverage_missing`，处置方式是重发布（`data update` 覆盖全历史窗口），不是豁免。
- "新 manifest 里读到已删除的 `resolved_end_is_fallback` 字段直接报错；旧 manifest 只做兼容读取"：判据同上（`calendar_coverage` 在 → 新形状 → 报 `removed_fallback_field_present`；不在 → legacy → 容忍该字段）。
- `full_history_acceptance_start` 为空扫描结果时写 `null`。此时 `data validate` 报 **WARNING**（显式"无法做全历史验收"，不默认通过），接受链 `calendar_coverage_evidence` 报 **FAIL**。这条不对称是有意的：工程冒烟项目（`tests/smoke/test_small_market_download.py` 无 `configs/universes/`）仍要能发布，而正式验收不得在缺少判据时通过。
- 测试只跑具名文件，绝不跑整套：`pytest tests/<path>::<test> -v`；例外是"重新发布数据集"的步骤。
- 提交信息用英文，结尾必须带 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- 工作树里已有无关 WIP（`project/RUNBOOK.md`、`project/configs/project.yml` 的 `initial_cash`、`data_pipeline.py` 的 `effective_start_date` 与公司行为 review 窗口过滤、`tests/integration/conftest.py`、`tests/integration/test_data_pipeline.py`）。本计划**保留** `effective_start_date`（它已进入 `dataset_build_config`），其余 WIP 不改动。

---

## File Structure

**新建（纯逻辑，无 I/O）**

| 文件 | 职责 |
| --- | --- |
| `src/stock_quant/data_model/calendar_coverage.py` | coverage span 的数据模型、payload 编解码、排序/重叠/空洞/覆盖校验、窗口替换与同源合并、manifest 日历证据校验（`validate_build_calendar_evidence`） |
| `src/stock_quant/data_model/trade_calendar_facts.py` | 供应商 `trade_cal` 原始帧 → 事实行（schema、halo 自然日集合、字段合法性）、两市逐日比对、物化候选开市日、`pretrade_date` 跨窗口连续性 |
| `tests/unit/test_calendar_coverage.py` / `tests/unit/test_trade_calendar_facts.py` | 上面两个模块的离线单元测试 |

**修改**

| 文件 | 改动 |
| --- | --- |
| `src/stock_quant/data_sources/tushare.py` | 新增 `trade_cal` endpoint（`_fetch_trade_cal` + `_validate_trade_cal`） |
| `src/stock_quant/research/universe.py` | 新增 `load_universe_coverage_criterion`（启用集合扫描：显式禁用跳过、解析失败阻断） |
| `src/stock_quant/bootstrap.py` | `build_config` 写入 `calendar_coverage`（一条 `bootstrap_seed` span） |
| `src/stock_quant/data_model/dataset.py` | `DatasetContext.manifest` 暴露已解析 manifest |
| `src/stock_quant/data_pipeline.py` | 删除时钟启发式发现 API 与 fallback 字段；新增 `_refresh_calendar`、发布期 span 门、`validate` 的日历证据检查 |
| `src/stock_quant/cli.py` | 删除 fallback 输出行；`--end` 帮助文案改为"缺省取已发布日历最大日期" |
| `src/stock_quant/config.py` | `publication_time` 文档字符串改为"仅记录用途，不再驱动终点发现" |
| `configs/universes/csi300.yml` | 加 `enabled: false`（占位模板永不进入验收判据） |
| `tests/integration/conftest.py` | 基线 build config 同步（Task 6 删 fallback 字段；Task 7 补两份 trade_cal 录制帧与 relay span、验收起点、真实 `definition_hashes`） |
| `tests/integration/test_data_pipeline.py`、`tests/integration/test_acceptance_checks.py`、`tests/unit/test_acceptance_service.py` | 三份 `StubAdapter` 支持 `trade_cal`；删除发现逻辑用例；新增日历用例 |
| `tests/integration/test_source_contracts.py` | `trade_cal` 供应商契约用例 |
| `tests/unit/test_acceptance_models.py` | 自动检查码清单加 `calendar_coverage_evidence` |
| `tests/smoke/test_small_market_download.py` | 合成基线写入 `bootstrap_seed` span |
| `project/rebuild_trading_calendar.py` | 加 guard：拒绝在无日历证据的情况下重发布 |
| `project/RUNBOOK.md`、`project/configs/project.yml`、`docs/operations/*` | 文案与操作说明更新 |

**任务依赖顺序：** 1 → 3 → 7；2 → 7；4 → 7；5 → 7；6 → 7；7 → 8 → 9 → 10。（1/2/3/4/5/6 之间除 1→3 外互相独立。）

---

### Task 1: coverage span 代数与 manifest 日历证据校验

**Files:**
- Create: `src/stock_quant/data_model/calendar_coverage.py`
- Create: `tests/unit/test_calendar_coverage.py`

**Interfaces:**
- Consumes: 无（纯逻辑，只依赖标准库）
- Produces（后续任务按这些签名调用）：
  - `CalendarCoverageSpan(start_date: date, end_date: date, source: str, snapshot_sha256s: Mapping[str, tuple[str, ...]])`
  - `CalendarCoverageError(violations: tuple[tuple[str, dict[str, object]], ...])`，属性 `.violations`
  - `SOURCE_BOOTSTRAP_SEED = "bootstrap_seed"`、`SOURCE_TUSHARE_RELAY = "tushare_relay"`
  - `seed_span(start, end)`、`supplier_span(start, end, *, source, snapshots_by_exchange)`
  - `span_payload(span) -> dict`、`coverage_payload(spans) -> list[dict]`、`coverage_from_payload(payload) -> tuple[CalendarCoverageSpan, ...]`
  - `span_violations(spans)`、`coverage_violations(spans, *, open_days)`
  - `merge_window(spans, *, start, end, replacement, open_days) -> tuple[CalendarCoverageSpan, ...]`（失败抛 `CalendarCoverageError`）
  - `full_history_violations(spans, *, acceptance_start, calendar_last_open) -> tuple[...]`
  - `validate_build_calendar_evidence(build, *, open_days) -> tuple[tuple[str, dict], ...]`
  - `COVERAGE_WARNING_CODES`、各 `CODE_*` 常量

- [x] **Step 1: 写失败的测试**

创建 `tests/unit/test_calendar_coverage.py`：

```python
"""Calendar coverage spans: split, replace, merge, and evidence validation."""

from __future__ import annotations

from datetime import date

import pytest

from stock_quant.data_model.calendar_coverage import (
    CODE_BOOTSTRAP_SEED_IN_FULL_HISTORY,
    CODE_CALENDAR_COVERAGE_GAP,
    CODE_CALENDAR_COVERAGE_INVALID,
    CODE_CALENDAR_COVERAGE_MISSING,
    CODE_CALENDAR_COVERAGE_OVERLAP,
    CODE_CALENDAR_COVERAGE_UNSORTED,
    CODE_CALENDAR_UNCOVERED,
    CODE_FULL_HISTORY_START_MISSING,
    CODE_REMOVED_FALLBACK_FIELD_PRESENT,
    SOURCE_BOOTSTRAP_SEED,
    SOURCE_TUSHARE_RELAY,
    CalendarCoverageError,
    coverage_from_payload,
    coverage_payload,
    full_history_violations,
    merge_window,
    seed_span,
    span_payload,
    span_violations,
    supplier_span,
    validate_build_calendar_evidence,
)


def _codes(violations) -> list[str]:
    return [code for code, _ in violations]


def _relay(start: str, end: str, *, sse=("a" * 64,), szse=("b" * 64,)):
    return supplier_span(
        date.fromisoformat(start),
        date.fromisoformat(end),
        source=SOURCE_TUSHARE_RELAY,
        snapshots_by_exchange={"SSE": list(sse), "SZSE": list(szse)},
    )


def _seed(start: str, end: str):
    return seed_span(date.fromisoformat(start), date.fromisoformat(end))


def _days(start: str, end: str) -> tuple[date, ...]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return tuple(
        date.fromordinal(day)
        for day in range(first.toordinal(), last.toordinal() + 1)
    )


def test_seed_span_payload_has_no_snapshot_field():
    payload = span_payload(_seed("2020-01-01", "2020-01-31"))
    assert payload == {
        "start_date": "2020-01-01",
        "end_date": "2020-01-31",
        "source": SOURCE_BOOTSTRAP_SEED,
    }


def test_supplier_span_sorts_and_dedups_snapshot_hashes():
    span = _relay(
        "2020-01-01",
        "2020-01-31",
        sse=("b" * 64, "a" * 64, "a" * 64),
        szse=("d" * 64, "c" * 64),
    )
    assert span.snapshot_sha256s == {
        "SSE": ("a" * 64, "b" * 64),
        "SZSE": ("c" * 64, "d" * 64),
    }
    assert span_payload(span)["snapshot_sha256s"] == {
        "SSE": ["a" * 64, "b" * 64],
        "SZSE": ["c" * 64, "d" * 64],
    }


def test_coverage_payload_round_trips():
    spans = (_seed("2020-01-01", "2020-01-09"), _relay("2020-01-10", "2020-01-31"))
    assert coverage_from_payload(coverage_payload(spans)) == spans


@pytest.mark.parametrize(
    "payload",
    [
        [{"start_date": "2020-01-31", "end_date": "2020-01-01", "source": SOURCE_TUSHARE_RELAY}],
        [{"start_date": "2020-01-01", "end_date": "2020-01-31", "source": "unknown_source"}],
        [{"start_date": "2020-01-01", "end_date": "2020-01-31"}],
        [{"start_date": "nope", "end_date": "2020-01-31", "source": SOURCE_BOOTSTRAP_SEED}],
        ["not-a-mapping"],
    ],
)
def test_coverage_from_payload_rejects_malformed_spans(payload):
    with pytest.raises(CalendarCoverageError) as error:
        coverage_from_payload(payload)
    assert _codes(error.value.violations) == [CODE_CALENDAR_COVERAGE_INVALID]


def test_supplier_span_rejects_a_non_supplier_source_and_incomplete_hashes():
    """A seed never carries hashes; a supplier span must carry both exchanges."""
    with pytest.raises(ValueError, match="seed span carries no snapshot hashes"):
        supplier_span(
            date(2020, 1, 1),
            date(2020, 1, 31),
            source=SOURCE_BOOTSTRAP_SEED,
            snapshots_by_exchange={"SSE": ["a" * 64]},
        )
    with pytest.raises(ValueError, match="SSE, SZSE"):
        supplier_span(
            date(2020, 1, 1),
            date(2020, 1, 31),
            source=SOURCE_TUSHARE_RELAY,
            snapshots_by_exchange={"SSE": ["a" * 64]},
        )


def test_span_violations_report_unsorted_overlap_and_gap():
    unsorted = (_relay("2020-02-01", "2020-02-28"), _relay("2020-01-01", "2020-01-31"))
    assert _codes(span_violations(unsorted)) == [CODE_CALENDAR_COVERAGE_UNSORTED]
    overlap = (_relay("2020-01-01", "2020-01-31"), _relay("2020-01-31", "2020-02-28"))
    assert _codes(span_violations(overlap)) == [CODE_CALENDAR_COVERAGE_OVERLAP]
    gap = (_relay("2020-01-01", "2020-01-31"), _relay("2020-02-02", "2020-02-28"))
    assert _codes(span_violations(gap)) == [CODE_CALENDAR_COVERAGE_GAP]


def test_merge_window_splits_the_seed_span_around_the_window():
    spans = (_seed("2020-01-01", "2020-01-31"),)
    merged = merge_window(
        spans,
        start=date(2020, 1, 10),
        end=date(2020, 1, 20),
        replacement=_relay("2020-01-10", "2020-01-20"),
        open_days=_days("2020-01-02", "2020-01-30"),
    )
    assert [(s.start_date, s.end_date, s.source) for s in merged] == [
        (date(2020, 1, 1), date(2020, 1, 9), SOURCE_BOOTSTRAP_SEED),
        (date(2020, 1, 10), date(2020, 1, 20), SOURCE_TUSHARE_RELAY),
        (date(2020, 1, 21), date(2020, 1, 31), SOURCE_BOOTSTRAP_SEED),
    ]


def test_merge_window_merges_adjacent_same_source_spans_and_unions_hashes():
    spans = (_relay("2020-01-01", "2020-01-09", sse=("a" * 64,)),)
    merged = merge_window(
        spans,
        start=date(2020, 1, 10),
        end=date(2020, 1, 20),
        replacement=_relay("2020-01-10", "2020-01-20", sse=("b" * 64,)),
        open_days=_days("2020-01-02", "2020-01-20"),
    )
    assert len(merged) == 1
    assert merged[0].start_date == date(2020, 1, 1)
    assert merged[0].end_date == date(2020, 1, 20)
    assert merged[0].snapshot_sha256s["SSE"] == ("a" * 64, "b" * 64)


def test_merge_window_never_merges_seed_with_a_supplier_span():
    spans = (_seed("2020-01-01", "2020-01-09"),)
    merged = merge_window(
        spans,
        start=date(2020, 1, 10),
        end=date(2020, 1, 20),
        replacement=_relay("2020-01-10", "2020-01-20"),
        open_days=_days("2020-01-02", "2020-01-20"),
    )
    assert [(s.source, s.start_date, s.end_date) for s in merged] == [
        (SOURCE_BOOTSTRAP_SEED, date(2020, 1, 1), date(2020, 1, 9)),
        (SOURCE_TUSHARE_RELAY, date(2020, 1, 10), date(2020, 1, 20)),
    ]


def test_merge_window_raises_on_a_gap_the_window_does_not_touch():
    spans = (_seed("2020-01-01", "2020-01-31"),)
    with pytest.raises(CalendarCoverageError) as error:
        merge_window(
            spans,
            start=date(2020, 6, 1),
            end=date(2020, 6, 30),
            replacement=_relay("2020-06-01", "2020-06-30"),
            open_days=_days("2020-01-02", "2020-06-30"),
        )
    assert _codes(error.value.violations) == [CODE_CALENDAR_COVERAGE_GAP]

def test_merge_window_raises_when_the_calendar_range_is_not_covered():
    with pytest.raises(CalendarCoverageError) as error:
        merge_window(
            (),
            start=date(2020, 1, 10),
            end=date(2020, 1, 20),
            replacement=_relay("2020-01-10", "2020-01-20"),
            open_days=_days("2019-12-02", "2021-01-04"),
        )
    assert _codes(error.value.violations) == [CODE_CALENDAR_UNCOVERED]
    assert error.value.violations[0][1] == {
        "first_open_day": "2019-12-02",
        "last_open_day": "2021-01-04",
        "coverage_start": "2020-01-10",
        "coverage_end": "2020-01-20",
    }


def test_validate_build_calendar_evidence_reads_legacy_manifests_compatibly():
    legacy = {"origin": "data_update", "resolved_end_is_fallback": True}
    violations = validate_build_calendar_evidence(legacy, open_days=_days("2020-01-02", "2020-01-03"))
    assert _codes(violations) == [CODE_CALENDAR_COVERAGE_MISSING]


def test_validate_build_calendar_evidence_rejects_the_removed_field_on_new_manifests():
    build = {
        "calendar_coverage": coverage_payload([_relay("2020-01-01", "2020-01-31")]),
        "full_history_acceptance_start": "2020-01-01",
        "resolved_end_is_fallback": False,
    }
    violations = validate_build_calendar_evidence(
        build, open_days=_days("2020-01-02", "2020-01-30")
    )
    assert _codes(violations) == [CODE_REMOVED_FALLBACK_FIELD_PRESENT]


def test_validate_build_calendar_evidence_reports_a_missing_acceptance_start():
    build = {"calendar_coverage": coverage_payload([_relay("2020-01-01", "2020-01-31")])}
    violations = validate_build_calendar_evidence(
        build, open_days=_days("2020-01-02", "2020-01-30")
    )
    assert _codes(violations) == [CODE_FULL_HISTORY_START_MISSING]
    assert violations[0][1] == {"reason": "no_enabled_universe_definition"}


def test_validate_build_calendar_evidence_passes_a_bound_version():
    build = {
        "calendar_coverage": coverage_payload([_relay("2020-01-01", "2020-01-31")]),
        "full_history_acceptance_start": "2020-01-01",
    }
    assert validate_build_calendar_evidence(
        build, open_days=_days("2020-01-02", "2020-01-30")
    ) == ()


def test_full_history_violations_flag_only_the_seed_inside_the_accepted_range():
    spans = (_seed("2020-01-01", "2020-01-09"), _relay("2020-01-10", "2020-01-31"))
    violations = full_history_violations(
        spans,
        acceptance_start=date(2020, 1, 5),
        calendar_last_open=date(2020, 1, 30),
    )
    assert _codes(violations) == [CODE_BOOTSTRAP_SEED_IN_FULL_HISTORY]
    assert violations[0][1] == {
        "start_date": "2020-01-01",
        "end_date": "2020-01-09",
        "full_history_acceptance_start": "2020-01-05",
        "last_calendar_date": "2020-01-30",
    }


def test_full_history_violations_allow_a_seed_that_ends_before_the_start():
    spans = (_seed("2019-01-01", "2019-12-31"), _relay("2020-01-01", "2020-01-31"))
    assert full_history_violations(
        spans,
        acceptance_start=date(2020, 1, 5),
        calendar_last_open=date(2020, 1, 30),
    ) == ()
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_calendar_coverage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.data_model.calendar_coverage'`

- [x] **Step 3: 写实现**

创建 `src/stock_quant/data_model/calendar_coverage.py`：

```python
"""Which natural-day range of the published calendar came from which source.

A published dataset must explain the provenance of every calendar day it
ships.  ``build_config.calendar_coverage`` is that ordered, non-overlapping,
gap-free span list: an update splits the existing spans at the materialisation
window, replaces the window with one supplier span, merges adjacent
same-source non-seed spans (union of snapshot hashes -- old evidence is never
dropped for a merge) and re-validates order/overlap/gaps before publishing.
``data validate`` and the acceptance chain re-check the same invariants
against the *published* calendar range, using only the criterion bound into
that version (a later ``configs/universes`` change never re-judges an old
version).

``DATASET_BUILD_CONTRACT_VERSION`` stays ``1`` on purpose: bumping it would
fail ``source_role_health`` for every already-published dataset.  The price is
that two ``build_config`` shapes exist under one version number, told apart by
the presence of the ``calendar_coverage`` key.  A manifest without it is read
compatibly (and reported as ``calendar_coverage_missing``); its disposition is
a republish, not an exemption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

SOURCE_BOOTSTRAP_SEED = "bootstrap_seed"
SOURCE_TUSHARE_RELAY = "tushare_relay"
SOURCE_TUSHARE_OFFICIAL_BREAK_GLASS = "tushare_official_break_glass"

#: Sources whose spans must carry per-exchange raw snapshot hashes.
SUPPLIER_SOURCES = (SOURCE_TUSHARE_RELAY, SOURCE_TUSHARE_OFFICIAL_BREAK_GLASS)
COVERAGE_SOURCES = (SOURCE_BOOTSTRAP_SEED, *SUPPLIER_SOURCES)

#: Exchanges every supplier calendar span must account for.
EXCHANGES = ("SSE", "SZSE")

CODE_CALENDAR_COVERAGE_MISSING = "calendar_coverage_missing"
CODE_CALENDAR_COVERAGE_INVALID = "calendar_coverage_invalid"
CODE_CALENDAR_COVERAGE_UNSORTED = "calendar_coverage_unsorted"
CODE_CALENDAR_COVERAGE_OVERLAP = "calendar_coverage_overlap"
CODE_CALENDAR_COVERAGE_GAP = "calendar_coverage_gap"
CODE_CALENDAR_UNCOVERED = "calendar_uncovered"
CODE_FULL_HISTORY_START_MISSING = "full_history_acceptance_start_missing"
CODE_BOOTSTRAP_SEED_IN_FULL_HISTORY = "bootstrap_seed_in_full_history"
CODE_REMOVED_FALLBACK_FIELD_PRESENT = "removed_fallback_field_present"

#: Violations that are reported but never block a publication: an empty
#: universe scan yields no acceptance start, and an engineering project must
#: still be publishable.  The acceptance chain fails on them regardless.
COVERAGE_WARNING_CODES = frozenset({CODE_FULL_HISTORY_START_MISSING})

#: The ``build_config`` key the whole evidence contract hangs on.
COVERAGE_KEY = "calendar_coverage"
ACCEPTANCE_START_KEY = "full_history_acceptance_start"
DEFINITION_HASHES_KEY = "universe_coverage_definition_hashes"
SKIPPED_DEFINITIONS_KEY = "universe_coverage_skipped"
REMOVED_FALLBACK_KEY = "resolved_end_is_fallback"

Violation = tuple[str, dict[str, object]]
Violations = tuple[Violation, ...]


class CalendarCoverageError(ValueError):
    """One or more coverage violations; ``.violations`` carries the codes."""

    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations: Violations = tuple(violations)
        code = self.violations[0][0] if self.violations else CODE_CALENDAR_COVERAGE_INVALID
        super().__init__(code)


@dataclass(frozen=True)
class CalendarCoverageSpan:
    """One contiguous natural-day range and the calendar evidence behind it."""

    start_date: date
    end_date: date
    source: str
    snapshot_sha256s: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError(
                f"calendar coverage span ends before it starts: "
                f"{self.start_date} > {self.end_date}"
            )
        if self.source not in COVERAGE_SOURCES:
            raise ValueError(f"unknown calendar coverage source: {self.source!r}")
        expected = set(EXCHANGES) if self.source in SUPPLIER_SOURCES else set()
        if self.source == SOURCE_BOOTSTRAP_SEED:
            if self.snapshot_sha256s:
                raise ValueError("a bootstrap seed span carries no snapshot hashes")
        elif set(self.snapshot_sha256s) != expected:
            raise ValueError(
                "a supplier span must carry snapshot hashes for exactly "
                f"{', '.join(EXCHANGES)}"
            )
        for exchange, hashes in self.snapshot_sha256s.items():
            if not hashes:
                raise ValueError(
                    f"span {self.start_date}..{self.end_date} has no "
                    f"{exchange} snapshot hash"
                )


def seed_span(start: date, end: date) -> CalendarCoverageSpan:
    """The offline bootstrap's weekday approximation over ``[start, end]``."""
    return CalendarCoverageSpan(start, end, SOURCE_BOOTSTRAP_SEED)


def supplier_span(
    start: date,
    end: date,
    *,
    source: str,
    snapshots_by_exchange: Mapping[str, Sequence[str]],
) -> CalendarCoverageSpan:
    """One supplier span with per-exchange hashes sorted and deduplicated."""
    return CalendarCoverageSpan(
        start,
        end,
        source,
        {
            exchange: tuple(sorted(set(snapshots_by_exchange.get(exchange, ()))))
            for exchange in EXCHANGES
        },
    )


def span_payload(span: CalendarCoverageSpan) -> dict[str, object]:
    """The sanitized JSON payload of one span (seed spans omit the hashes)."""
    payload: dict[str, object] = {
        "start_date": span.start_date.isoformat(),
        "end_date": span.end_date.isoformat(),
        "source": span.source,
    }
    if span.source in SUPPLIER_SOURCES:
        payload["snapshot_sha256s"] = {
            exchange: list(span.snapshot_sha256s[exchange]) for exchange in EXCHANGES
        }
    return payload


def coverage_payload(spans: Sequence[CalendarCoverageSpan]) -> list[dict[str, object]]:
    """The ordered ``build_config.calendar_coverage`` list."""
    return [span_payload(span) for span in spans]


def coverage_from_payload(payload: object) -> tuple[CalendarCoverageSpan, ...]:
    """Parse a ``calendar_coverage`` payload, or raise ``CalendarCoverageError``."""
    if payload is None:
        return ()
    if not isinstance(payload, (list, tuple)):
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "not_a_list"}),)
        )
    parsed: list[CalendarCoverageSpan] = []
    for row in payload:
        parsed.append(_span_from_row(row))
    return tuple(parsed)


def _span_from_row(row: object) -> CalendarCoverageSpan:
    if not isinstance(row, Mapping):
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "row_not_a_mapping"}),)
        )
    source = row.get("source")
    if source not in COVERAGE_SOURCES:
        raise CalendarCoverageError(
            (
                (
                    CODE_CALENDAR_COVERAGE_INVALID,
                    {"reason": "unknown_source", "source": str(source)},
                ),
            )
        )
    try:
        start = date.fromisoformat(str(row["start_date"]))
        end = date.fromisoformat(str(row["end_date"]))
    except (KeyError, ValueError):
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "bad_date"}),)
        ) from None
    if end < start:
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "inverted_range"}),)
        )
    if source not in SUPPLIER_SOURCES:
        return CalendarCoverageSpan(start, end, source)
    raw_hashes = row.get("snapshot_sha256s")
    if not isinstance(raw_hashes, Mapping):
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "missing_snapshot_hashes"}),)
        )
    try:
        return supplier_span(
            start,
            end,
            source=source,
            snapshots_by_exchange={
                exchange: list(raw_hashes.get(exchange, ()))
                for exchange in EXCHANGES
            },
        )
    except ValueError:
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "blank_snapshot_hashes"}),)
        ) from None


def span_violations(
    spans: Sequence[CalendarCoverageSpan],
) -> Violations:
    """Sort order, no overlap and no hole between consecutive spans."""
    for previous, current in zip(spans, spans[1:]):
        if previous.start_date > current.start_date:
            return ((CODE_CALENDAR_COVERAGE_UNSORTED, {"reason": "date_order"}),)
    for previous, current in zip(spans, spans[1:]):
        if current.start_date <= previous.end_date:
            return (
                (
                    CODE_CALENDAR_COVERAGE_OVERLAP,
                    {
                        "start_date": current.start_date.isoformat(),
                        "end_date": previous.end_date.isoformat(),
                    },
                ),
            )
    for previous, current in zip(spans, spans[1:]):
        if current.start_date != previous.end_date + timedelta(days=1):
            return (
                (
                    CODE_CALENDAR_COVERAGE_GAP,
                    {
                        "after": previous.end_date.isoformat(),
                        "before": current.start_date.isoformat(),
                    },
                ),
            )
    return ()


def coverage_violations(
    spans: Sequence[CalendarCoverageSpan], *, open_days: Sequence[date]
) -> Violations:
    """The published open-day range must sit inside the span range."""
    if not open_days or not spans:
        return ()
    first, last = min(open_days), max(open_days)
    if spans[0].start_date <= first and last <= spans[-1].end_date:
        return ()
    return (
        (
            CODE_CALENDAR_UNCOVERED,
            {
                "first_open_day": first.isoformat(),
                "last_open_day": last.isoformat(),
                "coverage_start": spans[0].start_date.isoformat(),
                "coverage_end": spans[-1].end_date.isoformat(),
            },
        ),
    )


def merge_adjacent_spans(
    spans: Sequence[CalendarCoverageSpan],
) -> tuple[CalendarCoverageSpan, ...]:
    """The spans with every adjacent same-source non-seed pair merged."""
    merged: list[CalendarCoverageSpan] = []
    for span in spans:
        if merged and _mergeable(merged[-1], span):
            merged[-1] = _merged(merged[-1], span)
            continue
        merged.append(span)
    return tuple(merged)


def _mergeable(previous: CalendarCoverageSpan, current: CalendarCoverageSpan) -> bool:
    if previous.source != current.source:
        return False
    if previous.source == SOURCE_BOOTSTRAP_SEED:
        return False
    return current.start_date == previous.end_date + timedelta(days=1)


def _merged(previous: CalendarCoverageSpan, current: CalendarCoverageSpan) -> CalendarCoverageSpan:
    return CalendarCoverageSpan(
        previous.start_date,
        current.end_date,
        previous.source,
        {
            exchange: tuple(
                sorted(
                    set(previous.snapshot_sha256s[exchange])
                    | set(current.snapshot_sha256s[exchange])
                )
            )
            for exchange in EXCHANGES
        },
    )


def _replaced(
    spans: Sequence[CalendarCoverageSpan],
    *,
    start: date,
    end: date,
    replacement: CalendarCoverageSpan,
) -> tuple[CalendarCoverageSpan, ...]:
    """Drop ``[start, end]`` from every span, then insert the replacement."""
    kept: list[CalendarCoverageSpan] = []
    for span in spans:
        if span.end_date < start or span.start_date > end:
            kept.append(span)
            continue
        if span.start_date < start:
            kept.append(
                _slice(span, span.start_date, start - timedelta(days=1))
            )
        if span.end_date > end:
            kept.append(_slice(span, end + timedelta(days=1), span.end_date))
    kept.append(replacement)
    return tuple(sorted(kept, key=lambda span: span.start_date))


def _slice(span: CalendarCoverageSpan, start: date, end: date) -> CalendarCoverageSpan:
    return CalendarCoverageSpan(
        start, end, span.source, dict(span.snapshot_sha256s)
    )


def merge_window(
    spans: Sequence[CalendarCoverageSpan],
    *,
    start: date,
    end: date,
    replacement: CalendarCoverageSpan,
    open_days: Sequence[date],
) -> tuple[CalendarCoverageSpan, ...]:
    """Replace ``[start, end]`` with ``replacement`` and re-validate.

    Raises :class:`CalendarCoverageError` when the result is unsorted,
    overlapping, has a hole, or no longer covers the published open-day range
    -- so a window that does not touch existing coverage is blocked *before*
    publishing, never after.
    """
    merged = merge_adjacent_spans(
        _replaced(spans, start=start, end=end, replacement=replacement)
    )
    violations = span_violations(merged) + coverage_violations(merged, open_days=open_days)
    if violations:
        raise CalendarCoverageError(violations)
    return merged


def full_history_violations(
    spans: Sequence[CalendarCoverageSpan],
    *,
    acceptance_start: date,
    calendar_last_open: date,
) -> Violations:
    """Seeds may not intersect ``[acceptance_start, last open day]``."""
    violations: list[Violation] = []
    for span in spans:
        if span.source != SOURCE_BOOTSTRAP_SEED:
            continue
        if span.end_date < acceptance_start or span.start_date > calendar_last_open:
            continue
        violations.append(
            (
                CODE_BOOTSTRAP_SEED_IN_FULL_HISTORY,
                {
                    "start_date": span.start_date.isoformat(),
                    "end_date": span.end_date.isoformat(),
                    "full_history_acceptance_start": acceptance_start.isoformat(),
                    "last_calendar_date": calendar_last_open.isoformat(),
                },
            )
        )
    return tuple(violations)


def validate_build_calendar_evidence(
    build: Mapping[str, Any] | None, *, open_days: Sequence[date]
) -> Violations:
    """Every calendar-evidence violation of one manifest's ``build_config``."""
    if not isinstance(build, Mapping) or COVERAGE_KEY not in build:
        return ((CODE_CALENDAR_COVERAGE_MISSING, {"build_config": "calendar_coverage"}),)
    violations: list[Violation] = []
    if REMOVED_FALLBACK_KEY in build:
        violations.append(
            (CODE_REMOVED_FALLBACK_FIELD_PRESENT, {"key": REMOVED_FALLBACK_KEY})
        )
    try:
        spans = coverage_from_payload(build[COVERAGE_KEY])
    except CalendarCoverageError as error:
        return tuple(violations) + error.violations
    violations.extend(span_violations(spans))
    violations.extend(coverage_violations(spans, open_days=open_days))
    start_raw = build.get(ACCEPTANCE_START_KEY)
    if not isinstance(start_raw, str):
        violations.append(
            (
                CODE_FULL_HISTORY_START_MISSING,
                {"reason": "no_enabled_universe_definition"},
            )
        )
        return tuple(violations)
    if open_days:
        violations.extend(
            full_history_violations(
                spans,
                acceptance_start=date.fromisoformat(start_raw),
                calendar_last_open=max(open_days),
            )
        )
    return tuple(violations)


def merged_snapshot_hashes(
    spans: Sequence[CalendarCoverageSpan],
) -> dict[str, tuple[str, ...]]:
    """Every snapshot hash any span carries, per exchange (audit helper)."""
    collected: dict[str, set[str]] = {exchange: set() for exchange in EXCHANGES}
    for span in spans:
        for exchange in EXCHANGES:
            collected[exchange].update(span.snapshot_sha256s.get(exchange, ()))
    return {
        exchange: tuple(sorted(collected[exchange])) for exchange in EXCHANGES
    }
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_calendar_coverage.py -v`
Expected: PASS（16 passed）

- [x] **Step 5: 提交**

```bash
git add src/stock_quant/data_model/calendar_coverage.py tests/unit/test_calendar_coverage.py
git commit -m "feat(calendar): add coverage span algebra and manifest evidence checks

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: `TushareSource` 的 `trade_cal` endpoint

**Files:**
- Modify: `src/stock_quant/data_sources/tushare.py:83-91`（`fetch` 分发）、新增 `_fetch_trade_cal` / `_validate_trade_cal`（放在 `_fetch_stock_basic` / `_validate_stock_basic` 之后）
- Test: `tests/integration/test_source_contracts.py`（追加用例，紧接 `test_tushare_stock_basic_rejects_symbol_scoped_request` 之后）

**Interfaces:**
- Consumes: `DataRequest(endpoint="trade_cal", symbols=(), start_date, end_date, params={"exchange": "SSE"|"SZSE"})`
- Produces: `FetchResult(endpoint="trade_cal", ...)`，`metadata["supplier_endpoint"] == "tushare.pro.trade_cal"`（relay 下为 `tushare_relay.<host>.trade_cal`），request_key 含 `exchange` 参数，因此 SSE / SZSE 是两份独立 raw snapshot

- [x] **Step 1: 写失败的测试**

在 `tests/integration/test_source_contracts.py` 末尾追加：

```python
class _TradeCalClient:
    """A recording stub session exposing only the ``trade_cal`` endpoint."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls: list[dict[str, str]] = []

    def trade_cal(self, **kwargs: str) -> pd.DataFrame:
        self.calls.append(kwargs)
        return self.frame


def _trade_cal_frame(exchange: str = "SSE") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "exchange": [exchange, exchange],
            "cal_date": ["20240102", "20240103"],
            "is_open": [1, 1],
            "pretrade_date": ["20231229", "20240102"],
        }
    )


def test_tushare_trade_cal_requires_a_supported_exchange(monkeypatch):
    """``trade_cal`` is a per-exchange request with a fixed exchange set."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    client = _TradeCalClient(_trade_cal_frame())
    source = TushareSource(SourceConfig(), client)
    for params in ({}, {"exchange": "BSE"}):
        with pytest.raises(ValueError, match="exchange"):
            source.fetch(
                DataRequest(
                    "trade_cal", (), date(2024, 1, 2), date(2024, 1, 5), params
                )
            )
    assert client.calls == []


def test_tushare_trade_cal_rejects_a_symbol_scoped_request(monkeypatch):
    """A calendar is fetched per exchange, never per symbol."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    client = _TradeCalClient(_trade_cal_frame())
    with pytest.raises(ValueError, match="whole-exchange"):
        TushareSource(SourceConfig(), client).fetch(
            DataRequest(
                "trade_cal", ("600000.SH",), date(2024, 1, 2), date(2024, 1, 5),
                {"exchange": "SSE"},
            )
        )
    assert client.calls == []


def test_tushare_trade_cal_returns_native_columns_and_supplier_endpoint(monkeypatch):
    """The raw boundary keeps Tushare naming and records the real endpoint."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    client = _TradeCalClient(_trade_cal_frame())
    source = TushareSource(SourceConfig(), client)

    result = source.fetch(
        DataRequest(
            "trade_cal", (), date(2024, 1, 2), date(2024, 1, 3), {"exchange": "SSE"}
        )
    )

    assert client.calls == [
        {"exchange": "SSE", "start_date": "20240102", "end_date": "20240103"}
    ]
    assert result.endpoint == "trade_cal"
    assert result.metadata["supplier_endpoint"] == "tushare.pro.trade_cal"
    assert result.frame.columns.tolist() == [
        "exchange", "cal_date", "is_open", "pretrade_date",
    ]
    # The exchange is part of the request key, so one refresh is two raw
    # snapshots rather than one that overwrites the other.
    other = source.fetch(
        DataRequest(
            "trade_cal", (), date(2024, 1, 2), date(2024, 1, 3), {"exchange": "SZSE"}
        )
    )
    assert other.request_key != result.request_key


def test_tushare_trade_cal_rejects_a_response_without_the_calendar_columns(
    monkeypatch,
):
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    client = _TradeCalClient(pd.DataFrame({"cal_date": ["20240102"]}))
    with pytest.raises(ContractError, match="is_open"):
        TushareSource(SourceConfig(), client).fetch(
            DataRequest(
                "trade_cal", (), date(2024, 1, 2), date(2024, 1, 3),
                {"exchange": "SSE"},
            )
        )


def test_tushare_trade_cal_names_a_client_without_the_endpoint(monkeypatch):
    """A transport whose session lacks ``trade_cal`` must say so, not crash."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")

    class _NoCalendar:
        pass

    with pytest.raises(ValueError, match="trade_cal"):
        TushareSource(SourceConfig(), _NoCalendar()).fetch(
            DataRequest(
                "trade_cal", (), date(2024, 1, 2), date(2024, 1, 3),
                {"exchange": "SSE"},
            )
        )
```

该文件顶部（第 4-21 行）已导入 `date`、`pd`、`pytest`、`SourceConfig`、`ContractError`、`TushareSource`，且新用例用**位置参数**传 client（`TushareSource(SourceConfig(), client)`，与同文件既有的 `TushareClient` 用例一致）——`monkeypatch.setenv("TUSHARE_TOKEN", ...)` 与既有 Tushare 用例保持同样风格。

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/integration/test_source_contracts.py -k trade_cal -v`
Expected: FAIL — `ValueError: TushareSource supports only the daily, index_daily, and stock_basic endpoints`

- [x] **Step 3: 写实现**

修改 `src/stock_quant/data_sources/tushare.py`。`fetch` 改为：

```python
    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint == "stock_basic":
            return self._fetch_stock_basic(request)
        if request.endpoint == "trade_cal":
            return self._fetch_trade_cal(request)
        if request.endpoint in ("daily", "index_daily"):
            return self._fetch_symbol_series(request)
        raise ValueError(
            "TushareSource supports only the daily, index_daily, stock_basic, "
            "and trade_cal endpoints"
        )
```

在 `_validate_stock_basic` 之后新增（模块级常量放在文件顶部 import 之后）：

```python
#: The exchanges a published trading calendar must agree on.
_TRADE_CAL_EXCHANGES = ("SSE", "SZSE")

#: The native columns a ``trade_cal`` response must carry.
_TRADE_CAL_COLUMNS = ("cal_date", "is_open", "pretrade_date")
```

```python
    def _fetch_trade_cal(self, request: DataRequest) -> FetchResult:
        """Fetch one exchange's calendar for the requested date range.

        ``trade_cal`` is a per-exchange request: ``request.symbols`` must be
        empty and ``params["exchange"]`` must be one of SSE / SZSE.  The
        exchange is part of the request key, so the two exchanges of one
        refresh are two independent raw snapshots.  Values are validated
        later, by ``trade_calendar_facts.parse_trade_cal_frame``; this method
        only enforces the endpoint contract.
        """
        if request.symbols:
            raise ValueError(
                "Tushare trade_cal is a whole-exchange request, not a "
                "symbol-scoped query"
            )
        exchange = request.params.get("exchange")
        if exchange not in _TRADE_CAL_EXCHANGES:
            raise ValueError(
                "Tushare trade_cal requires an exchange of "
                f"{' or '.join(_TRADE_CAL_EXCHANGES)}, got {exchange!r}"
            )
        client_endpoint = getattr(self._client, "trade_cal", None)
        if client_endpoint is None:
            raise ValueError(
                f"tushare transport {self._transport.transport_id} has no "
                "trade_cal endpoint"
            )
        request_timestamp = _utc_timestamp()
        try:
            frame = client_endpoint(
                exchange=exchange,
                start_date=request.start_date.strftime("%Y%m%d"),
                end_date=request.end_date.strftime("%Y%m%d"),
            )
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from None
        response_timestamp = _utc_timestamp()
        self._validate_trade_cal(frame)
        metadata = request_metadata(
            request,
            self._supplier_endpoint("trade_cal"),
            self._sdk_version,
            transport_id=self._transport.transport_id,
            request_timestamp=request_timestamp,
            response_timestamp=response_timestamp,
        )
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=metadata,
        )

    @staticmethod
    def _validate_trade_cal(frame: pd.DataFrame) -> None:
        """Validate the native shape; day-set and value checks come later."""
        if not isinstance(frame, pd.DataFrame):
            raise ContractError("supplier response is not a pandas DataFrame")
        if frame.empty:
            raise ContractError("supplier returned an empty trade_cal response")
        missing = [name for name in _TRADE_CAL_COLUMNS if name not in frame.columns]
        if missing:
            raise ContractError(
                "supplier trade_cal response is missing columns: "
                + ", ".join(missing)
            )
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/integration/test_source_contracts.py -k trade_cal -v`
Expected: PASS（5 passed）

再跑一遍该文件确保没破坏既有契约：

Run: `pytest tests/integration/test_source_contracts.py -v`
Expected: PASS（全绿）

- [x] **Step 5: 真实 relay 小窗口 probe（发布前必须人工确认一次）**

本步验证设计的核心假设：relay 的 `trade_cal` 在 **halo 每一个自然日**（含休市日）都回填 `pretrade_date`。任一 halo 行 `pretrade_date` 为空或不可解析，就说明严格解析会把上游缺陷当成我们的失败——**停下来报告**，不要继续 Task 3。

```bash
python - <<'PY'
import os
from datetime import date, timedelta
from pathlib import Path

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.data_sources.tushare_relay import TushareRelayClient

# Same .env loading as project/crosscheck_calendar_relay.py: simple KEY=VALUE
# lines, no shell evaluation, and token values are never printed.
for raw in Path(".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.removeprefix("export ").split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

client = TushareRelayClient.from_env()
assert client is not None, "relay not configured: TUSHARE_RELAY_URL / TUSHARE_RELAY_KEY"
source = TushareSource(SourceConfig(), client)
print(f"transport={source.transport.transport_id} kind={source.transport.kind}")

end = date.today()
start = end - timedelta(days=20)
for exchange in ("SSE", "SZSE"):
    result = source.fetch(
        DataRequest(
            "trade_cal", (), start, end + timedelta(days=1), {"exchange": exchange}
        )
    )
    frame = result.frame
    blank = frame["pretrade_date"].astype(str).str.strip().eq("")
    print(
        exchange,
        "supplier_endpoint", result.metadata["supplier_endpoint"],
        "rows", len(frame),
        "blank_pretrade", int(blank.sum()),
        "is_open_values", sorted(set(frame["is_open"].astype(str))),
        "columns", frame.columns.tolist(),
    )
PY
```

Expected: 两市 `blank_pretrade 0`，`is_open_values ['0', '1']`，`rows` 等于 halo 自然日数（21+2 天窗口 = 23 行），`supplier_endpoint` 形如 `tushare_relay.<host>.trade_cal`。

- [x] **Step 6: 提交**

```bash
git add src/stock_quant/data_sources/tushare.py tests/integration/test_source_contracts.py
git commit -m "feat(tushare): add the per-exchange trade_cal endpoint

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 原始日历事实、两市比对与跨窗口连续性

**Files:**
- Create: `src/stock_quant/data_model/trade_calendar_facts.py`
- Create: `tests/unit/test_trade_calendar_facts.py`

**Interfaces:**
- Consumes: `EXCHANGES` 语义（但**不** import Task 1 的模块，避免循环；两处各自持有 `("SSE", "SZSE")`）
- Produces：
  - `TradeCalRow(calendar_date: date, is_open: bool, pretrade_date: date)`
  - `ExchangeCalendarFacts(exchange: str, rows: tuple[TradeCalRow, ...])`，属性 `open_days`
  - `parse_trade_cal_frame(frame, *, exchange: str, halo_start: date, halo_end: date) -> ExchangeCalendarFacts`（失败抛 `TradeCalendarFactError`，带 `.violations`）
  - `check_exchange_agreement(sse, szse) -> Violations`
  - `materialize_open_days(current_open_days, *, facts, start, end) -> tuple[date, ...]`
  - `check_pretrade_continuity(rows, merged_open_days, *, start, end) -> ContinuityResult(violations, allowed_pre_coverage)`
  - `CODE_CALENDAR_RAW_INVALID` / `CODE_CALENDAR_EXCHANGE_MISMATCH` / `CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN`

- [x] **Step 1: 写失败的测试**

创建 `tests/unit/test_trade_calendar_facts.py`：

```python
"""Raw Tushare trade_cal facts: halo day sets, cross-exchange, continuity."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stock_quant.data_model.trade_calendar_facts import (
    CODE_CALENDAR_EXCHANGE_MISMATCH,
    CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN,
    CODE_CALENDAR_RAW_INVALID,
    TradeCalRow,
    TradeCalendarFactError,
    check_exchange_agreement,
    check_pretrade_continuity,
    materialize_open_days,
    parse_trade_cal_frame,
)

HALO_START = date(2024, 1, 1)
HALO_END = date(2024, 1, 7)
#: Mon 1 .. Sun 7 January 2024; 1-5 are the open days.
OPEN = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))
CLOSED = (date(2024, 1, 6), date(2024, 1, 7))


def _previous_open(day: date) -> str:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.strftime("%Y%m%d")


def _frame(**overrides):
    """One row per halo natural day, Friday-to-Monday aware."""
    rows = []
    for offset in range((HALO_END - HALO_START).days + 1):
        day = HALO_START + timedelta(days=offset)
        row = {
            "exchange": "SSE",
            "cal_date": day.strftime("%Y%m%d"),
            "is_open": 1 if day.weekday() < 5 else 0,
            "pretrade_date": _previous_open(day),
        }
        row.update(overrides.get(day.isoformat(), {}))
        rows.append(row)
    return pd.DataFrame(rows)


def _codes(violations) -> list[str]:
    return [code for code, _ in violations]


def _facts(frame, exchange="SSE"):
    return parse_trade_cal_frame(
        frame, exchange=exchange, halo_start=HALO_START, halo_end=HALO_END
    )


def test_parse_accepts_a_complete_halo_and_exposes_open_days():
    facts = _facts(_frame())
    assert facts.exchange == "SSE"
    assert len(facts.rows) == 7
    assert facts.open_days == OPEN
    assert facts.rows[-1].pretrade_date == date(2024, 1, 5)


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"empty": True}, "empty_response"),
        ({"drop": ["is_open"]}, "missing_columns"),
        ({"bad_cal_date": True}, "unparsable_cal_date"),
        ({"bad_pretrade_date": True}, "unparsable_pretrade_date"),
        ({"blank_pretrade_date": True}, "unparsable_pretrade_date"),
        ({"is_open": "2"}, "invalid_is_open"),
    ],
)
def test_parse_rejects_malformed_supplier_values(kwargs, reason):
    if kwargs.pop("empty", False):
        frame = pd.DataFrame(
            columns=["cal_date", "is_open", "pretrade_date"]
        )
    else:
        frame = _frame()
        if "drop" in kwargs:
            frame = frame.drop(columns=kwargs.pop("drop"))
        if kwargs.pop("bad_cal_date", False):
            frame.loc[0, "cal_date"] = "2024-01-01"
        if kwargs.pop("bad_pretrade_date", False):
            frame.loc[1, "pretrade_date"] = "20231229-"
        if kwargs.pop("blank_pretrade_date", False):
            frame.loc[1, "pretrade_date"] = ""
        if "is_open" in kwargs:
            frame["is_open"] = kwargs.pop("is_open")
    with pytest.raises(TradeCalendarFactError) as error:
        _facts(frame)
    assert _codes(error.value.violations) == [CODE_CALENDAR_RAW_INVALID]
    assert error.value.violations[0][1]["reason"] == reason
    assert error.value.violations[0][1]["exchange"] == "SSE"


def test_parse_reports_a_missing_halo_day_before_any_cross_exchange_check():
    """A short day set is reported as missing, never as an exchange mismatch."""
    frame = _frame().drop(index=2)
    with pytest.raises(TradeCalendarFactError) as error:
        _facts(frame)
    assert error.value.violations[0][1] == {
        "exchange": "SSE",
        "reason": "missing_date",
        "calendar_date": "2024-01-03",
    }


def test_parse_reports_a_duplicated_halo_day():
    frame = pd.concat([_frame(), _frame().iloc[[2]]], ignore_index=True)
    with pytest.raises(TradeCalendarFactError) as error:
        _facts(frame)
    assert error.value.violations[0][1]["reason"] == "duplicate_date"


def test_exchange_agreement_reports_the_disagreeing_days():
    sse = _facts(_frame())
    szse_frame = _frame()
    szse_frame.loc[4, "is_open"] = 0
    szse_frame.loc[5, "pretrade_date"] = "20240118"
    szse = _facts(szse_frame, exchange="SZSE")
    violations = check_exchange_agreement(sse, szse)
    assert _codes(violations) == [CODE_CALENDAR_EXCHANGE_MISMATCH]
    assert violations[0][1] == {
        "field": "is_open",
        "dates": ["2024-01-05"],
        "exchanges": ["SSE", "SZSE"],
    }


def test_materialize_replaces_only_the_window_open_days():
    facts = _facts(_frame())
    current = (
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    )
    today = date(2024, 1, 3)
    back = date(2024, 1, 5)
    merged = materialize_open_days(
        current, facts=facts, start=today, end=back
    )
    assert merged == (
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    )


def test_materialize_drops_a_stale_window_day_absent_from_the_supplier():
    facts = _facts(_frame())
    window = (date(2024, 1, 1), date(2024, 1, 2))
    merged = materialize_open_days(
        (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)),
        facts=facts,
        start=window[0],
        end=window[1],
    )
    assert merged == window + (date(2024, 1, 3),)


def test_continuity_accepts_a_chain_that_matches_the_merged_table():
    facts = _facts(_frame())
    merged = facts.open_days
    result = check_pretrade_continuity(
        facts.rows, merged, start=HALO_START, end=date(2024, 1, 5)
    )
    assert result.violations == ()
    assert result.allowed_pre_coverage == ()


def test_continuity_reports_a_window_row_that_skipped_an_open_day():
    facts = _facts(_frame())
    merged = tuple(day for day in facts.open_days if day != date(2024, 1, 4))
    result = check_pretrade_continuity(
        facts.rows, merged, start=HALO_START, end=date(2024, 1, 5)
    )
    assert _codes(result.violations) == [CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN]
    assert result.violations[0][1] == {
        "calendar_date": "2024-01-05",
        "pretrade_date": "2024-01-04",
        "expected": "2024-01-03",
    }


def test_continuity_checks_the_end_plus_one_halo_row_against_the_merged_table():
    """The ``end + 1`` row is compared to the merged table, not the response.

    The window is the single Monday 2024-01-08 and the merged table has lost
    Friday 2024-01-05; Monday still claims Friday as its pretrade day, which is
    exactly the "a deleted window day is still referenced by a neighbouring
    row" failure the rule exists to catch.
    """
    facts = _facts(_frame())
    result = check_pretrade_continuity(
        facts.rows,
        (date(2024, 1, 4), date(2024, 1, 8)),
        start=date(2024, 1, 8),
        end=date(2024, 1, 8),
    )
    assert _codes(result.violations) == [CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN]
    assert result.violations[0][1] == {
        "calendar_date": "2024-01-08",
        "pretrade_date": "2024-01-05",
        "expected": "2024-01-04",
    }


def test_continuity_accepts_a_window_that_starts_inside_existing_coverage():
    """A first row whose pretrade day is an earlier merged row is a real chain."""
    facts = _facts(_frame())
    result = check_pretrade_continuity(
        facts.rows, facts.open_days, start=date(2024, 1, 2), end=date(2024, 1, 5)
    )
    assert result.violations == ()
    assert result.allowed_pre_coverage == ()


def test_continuity_returns_the_pre_coverage_boundary_explicitly():
    """Only a pretrade day strictly before the window *and* no earlier merged
    row at all is a boundary -- and it is reported, never passed over."""
    facts = _facts(_frame())
    result = check_pretrade_continuity(
        (facts.rows[1],), (), start=date(2024, 1, 2), end=date(2024, 1, 5)
    )
    assert result.violations == ()
    assert result.allowed_pre_coverage == (date(2024, 1, 2),)


def test_continuity_reports_a_row_whose_pretrade_is_not_before_the_window():
    """No earlier merged row plus a pretrade day inside the window is a break."""
    row = TradeCalRow(date(2024, 1, 2), True, date(2024, 1, 2))
    result = check_pretrade_continuity(
        (row,), (), start=date(2024, 1, 2), end=date(2024, 1, 5)
    )
    assert _codes(result.violations) == [CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN]
    assert result.violations[0][1] == {
        "calendar_date": "2024-01-02",
        "pretrade_date": "2024-01-02",
        "expected": None,
    }
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_trade_calendar_facts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.data_model.trade_calendar_facts'`

- [x] **Step 3: 写实现**

创建 `src/stock_quant/data_model/trade_calendar_facts.py`：

```python
"""Raw ``trade_cal`` facts: halo day sets, agreement, continuity.

The validation order is fixed and matters: per-exchange schema, then
per-exchange halo day set (one row per natural day, no gap, no duplicate),
then per-exchange field legality, and only then a day-by-day cross-exchange
comparison.  A missing day is therefore always reported as a missing day and
never as an ambiguous "the two exchanges disagree".
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Sequence

import pandas as pd

from stock_quant.data_model.clean import parse_trade_date

CODE_CALENDAR_RAW_INVALID = "calendar_raw_invalid"
CODE_CALENDAR_EXCHANGE_MISMATCH = "calendar_exchange_mismatch"
CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN = "calendar_pretrade_continuity_broken"

EXCHANGES = ("SSE", "SZSE")

CAL_DATE_COLUMN = "cal_date"
IS_OPEN_COLUMN = "is_open"
PRETRADE_DATE_COLUMN = "pretrade_date"

#: How many dates one violation may list.
_MAX_DATES = 20

Violation = tuple[str, dict[str, object]]
Violations = tuple[Violation, ...]


class TradeCalendarFactError(ValueError):
    """A supplier calendar frame cannot be trusted; see ``.violations``."""

    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations: Violations = tuple(violations)
        super().__init__(self.violations[0][0] if self.violations else "calendar_raw_invalid")


@dataclass(frozen=True)
class TradeCalRow:
    """One halo natural day as the supplier reported it."""

    calendar_date: date
    is_open: bool
    pretrade_date: date


@dataclass(frozen=True)
class ExchangeCalendarFacts:
    """One exchange's validated halo rows, ordered by natural day."""

    exchange: str
    rows: tuple[TradeCalRow, ...]

    @property
    def open_days(self) -> tuple[date, ...]:
        return tuple(row.calendar_date for row in self.rows if row.is_open)


@dataclass(frozen=True)
class ContinuityResult:
    """Index-chain violations plus the explicit pre-coverage boundary rows."""

    violations: Violations
    allowed_pre_coverage: tuple[date, ...]


def _raw_invalid(exchange: str, reason: str, **details: object) -> TradeCalendarFactError:
    return TradeCalendarFactError(
        ((CODE_CALENDAR_RAW_INVALID, {"exchange": exchange, "reason": reason, **details}),)
    )


def parse_trade_cal_frame(
    frame: pd.DataFrame,
    *,
    exchange: str,
    halo_start: date,
    halo_end: date,
) -> ExchangeCalendarFacts:
    """Validate one exchange's halo response into ordered calendar facts."""
    if exchange not in EXCHANGES:
        raise _raw_invalid(exchange, "unknown_exchange")
    if not isinstance(frame, pd.DataFrame):
        raise _raw_invalid(exchange, "not_a_data_frame")
    required = (CAL_DATE_COLUMN, IS_OPEN_COLUMN, PRETRADE_DATE_COLUMN)
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise _raw_invalid(exchange, "missing_columns", columns=missing)
    if frame.empty:
        raise _raw_invalid(exchange, "empty_response")
    rows = _parsed_rows(frame, exchange=exchange)
    _check_halo_days(rows, exchange=exchange, halo_start=halo_start, halo_end=halo_end)
    return ExchangeCalendarFacts(exchange, rows)


def _parsed_rows(frame: pd.DataFrame, *, exchange: str) -> tuple[TradeCalRow, ...]:
    rows: list[TradeCalRow] = []
    for record in frame.to_dict("records"):
        calendar_date = parse_trade_date(record[CAL_DATE_COLUMN])
        if calendar_date is None:
            raise _raw_invalid(
                exchange,
                "unparsable_cal_date",
                value=str(record[CAL_DATE_COLUMN]),
            )
        is_open_raw = record[IS_OPEN_COLUMN]
        if isinstance(is_open_raw, bool):
            is_open = is_open_raw
        elif str(is_open_raw).strip() in {"0", "1"}:
            is_open = str(is_open_raw).strip() == "1"
        else:
            raise _raw_invalid(
                exchange, "invalid_is_open", value=str(is_open_raw)
            )
        pretrade_raw = record[PRETRADE_DATE_COLUMN]
        if pretrade_raw is None or not str(pretrade_raw).strip():
            raise _raw_invalid(
                exchange, "unparsable_pretrade_date", calendar_date=calendar_date.isoformat()
            )
        pretrade_date = parse_trade_date(pretrade_raw)
        if pretrade_date is None:
            raise _raw_invalid(
                exchange,
                "unparsable_pretrade_date",
                calendar_date=calendar_date.isoformat(),
            )
        rows.append(TradeCalRow(calendar_date, is_open, pretrade_date))
    return tuple(sorted(rows, key=lambda row: row.calendar_date))


def _check_halo_days(
    rows: Sequence[TradeCalRow],
    *,
    exchange: str,
    halo_start: date,
    halo_end: date,
) -> None:
    expected = [
        halo_start + timedelta(days=offset)
        for offset in range((halo_end - halo_start).days + 1)
    ]
    seen: dict[date, int] = {}
    for row in rows:
        seen[row.calendar_date] = seen.get(row.calendar_date, 0) + 1
    duplicates = sorted(day for day, count in seen.items() if count > 1)
    if duplicates:
        raise _raw_invalid(
            exchange,
            "duplicate_date",
            calendar_date=duplicates[0].isoformat(),
        )
    outside = sorted(day for day in seen if day < halo_start or day > halo_end)
    if outside:
        raise _raw_invalid(
            exchange, "outside_halo", calendar_date=outside[0].isoformat()
        )
    for day in expected:
        if day not in seen:
            raise _raw_invalid(
                exchange, "missing_date", calendar_date=day.isoformat()
            )


def check_exchange_agreement(
    sse: ExchangeCalendarFacts, szse: ExchangeCalendarFacts
) -> Violations:
    """Compare two complete halos day by day; any difference blocks."""
    other = {row.calendar_date: row for row in szse.rows}
    for field in ("is_open", "pretrade_date"):
        dates = [
            row.calendar_date.isoformat()
            for row in sse.rows
            if other[row.calendar_date].__getattribute__(field) != getattr(row, field)
        ]
        if dates:
            return (
                (
                    CODE_CALENDAR_EXCHANGE_MISMATCH,
                    {
                        "field": field,
                        "dates": sorted(dates)[:_MAX_DATES],
                        "exchanges": list(EXCHANGES),
                    },
                ),
            )
    return ()


def materialize_open_days(
    current_open_days: Sequence[date],
    *,
    facts: ExchangeCalendarFacts,
    start: date,
    end: date,
) -> tuple[date, ...]:
    """Drop the window's open days, then insert the supplier's window days.

    Rows outside ``[start, end]`` are carried unchanged; the result is sorted
    and deduplicated.  The supplier's days outside the window are ignored --
    the materialisation window never grows past ``[start, end]``.
    """
    kept = [day for day in current_open_days if not start <= day <= end]
    fresh = [day for day in facts.open_days if start <= day <= end]
    return tuple(sorted(set(kept) | set(fresh)))


def check_pretrade_continuity(
    rows: Sequence[TradeCalRow],
    merged_open_days: Sequence[date],
    *,
    start: date,
    end: date,
) -> ContinuityResult:
    """Every window row and the ``end + 1`` halo row must chain correctly.

    ``pretrade_date`` is compared against the *merged candidate table* (the
    carried calendar plus this window's materialisation), never against the
    supplier's own response -- so both a deleted window day and a neighbour
    still referencing it are caught.  The single allowed escape is the row
    whose ``pretrade_date`` is strictly before ``start`` while the merged table
    holds no earlier open day at all: that is the coverage boundary, and it is
    returned explicitly in ``allowed_pre_coverage`` instead of passing
    silently.
    """
    open_sorted = tuple(sorted(set(merged_open_days)))
    last_row = end + timedelta(days=1)
    violations: list[Violation] = []
    allowed: list[date] = []
    for row in rows:
        if not start <= row.calendar_date <= last_row:
            continue
        index = bisect_left(open_sorted, row.calendar_date)
        expected = open_sorted[index - 1] if index > 0 else None
        if expected is None:
            if row.pretrade_date < start:
                allowed.append(row.calendar_date)
                continue
            violations.append(_continuity(row, None))
            continue
        if row.pretrade_date != expected:
            violations.append(_continuity(row, expected))
    return ContinuityResult(tuple(violations), tuple(allowed))


def _continuity(row: TradeCalRow, expected: date | None) -> Violation:
    return (
        CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN,
        {
            "calendar_date": row.calendar_date.isoformat(),
            "pretrade_date": row.pretrade_date.isoformat(),
            "expected": expected.isoformat() if expected is not None else None,
        },
    )
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_trade_calendar_facts.py -v`
Expected: PASS（全绿）

- [x] **Step 5: 提交**

```bash
git add src/stock_quant/data_model/trade_calendar_facts.py tests/unit/test_trade_calendar_facts.py
git commit -m "feat(calendar): validate raw trade_cal halos, agreement and continuity

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: 启用 universe 判据（全历史验收起点）

**Files:**
- Modify: `src/stock_quant/research/universe.py`（追加 `UniverseCoverageCriterion` 与 `load_universe_coverage_criterion`）
- Modify: `configs/universes/csi300.yml`（加 `enabled: false` 并在文件头说明）
- Test: `tests/unit/test_research_universe.py`（追加）

**Interfaces:**
- Consumes: `UniverseDefinition.model_validate`、`UniverseCoverageError`
- Produces：
  - `UniverseCoverageCriterion(acceptance_start: date | None, definition_hashes: Mapping[str, str], skipped: tuple[str, ...])`
  - `load_universe_coverage_criterion(directory: str | Path) -> UniverseCoverageCriterion`
  - 语义：`*.yml` 按文件名排序扫描；`enabled: false` → 跳过并记入 `skipped`；其余（含未写 `enabled`）必须能 `model_validate`，失败即抛 `UniverseCoverageError`（阻断发布）；`acceptance_start = min(coverage_start)`，扫描为空 → `None`

- [x] **Step 1: 写失败的测试**

在 `tests/unit/test_research_universe.py` 末尾追加：

```python
def _write_definition(directory, name: str, *, start: str, enabled=None, **overrides):
    payload = {
        "schema_version": 1,
        "universe_id": f"custom_{name.replace('.', '_')}",
        "rules_version": "fixture-rules-v1",
        "membership_table_sha256": "ab" * 32,
        "coverage_start": start,
        "coverage_end": "2022-01-07",
        "evidence_summary_sha256": "cd" * 32,
    }
    payload.update(overrides)
    if enabled is not None:
        payload["enabled"] = enabled
    (directory / name).write_text(yaml.safe_dump(payload), encoding="utf-8")
    return payload


def test_coverage_criterion_takes_the_earliest_enabled_start(tmp_path):
    _write_definition(tmp_path, "b_second.yml", start="2019-01-02")
    _write_definition(tmp_path, "a_first.yml", start="2015-01-05")
    criterion = load_universe_coverage_criterion(tmp_path)
    assert criterion.acceptance_start == date(2015, 1, 5)
    assert criterion.skipped == ()
    assert set(criterion.definition_hashes) == {"custom_a_first_yml", "custom_b_second_yml"}
    assert all(len(value) == 64 for value in criterion.definition_hashes.values())


def test_coverage_criterion_skips_explicitly_disabled_definitions(tmp_path):
    _write_definition(tmp_path, "csi300.yml", start="2005-01-03", enabled=False)
    _write_definition(tmp_path, "custom_live.yml", start="2015-01-05")
    criterion = load_universe_coverage_criterion(tmp_path)
    assert criterion.acceptance_start == date(2015, 1, 5)
    assert criterion.skipped == ("csi300.yml",)
    assert set(criterion.definition_hashes) == {"custom_custom_live_yml"}


def test_coverage_criterion_blocks_an_enabled_definition_that_cannot_parse(tmp_path):
    _write_definition(tmp_path, "custom_broken.yml", start="2015-01-05",
                      membership_table_sha256="PLACEHOLDER")
    with pytest.raises(UniverseCoverageError):
        load_universe_coverage_criterion(tmp_path)


def test_coverage_criterion_blocks_a_non_mapping_document(tmp_path):
    (tmp_path / "custom_bad.yml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(UniverseCoverageError):
        load_universe_coverage_criterion(tmp_path)


def test_coverage_criterion_is_empty_without_any_definition(tmp_path):
    criterion = load_universe_coverage_criterion(tmp_path / "missing")
    assert criterion == UniverseCoverageCriterion(None, {}, ())
```

补齐 import：`from stock_quant.research.universe import UniverseCoverageCriterion, load_universe_coverage_criterion`（`pytest` / `date` / `yaml` 已在该文件内）。

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_research_universe.py -k coverage_criterion -v`
Expected: FAIL — `ImportError: cannot import name 'UniverseCoverageCriterion'`

- [x] **Step 3: 写实现**

在 `src/stock_quant/research/universe.py` 的 `load_universe_definition` 之前新增：

```python
@dataclass(frozen=True)
class UniverseCoverageCriterion:
    """The version-bound full-history criterion of one publish moment.

    ``acceptance_start`` is the earliest ``coverage_start`` over the enabled
    universe definitions; ``None`` means the scan found no enabled definition,
    and full-history acceptance then has no criterion and cannot be claimed.
    ``definition_hashes`` pins the exact definition versions the criterion was
    computed from, so a later ``configs/universes`` change never re-judges an
    already-published dataset.  ``skipped`` records the explicitly disabled
    files so the manifest shows what was ignored on purpose.
    """

    acceptance_start: date | None
    definition_hashes: Mapping[str, str]
    skipped: tuple[str, ...]


def load_universe_coverage_criterion(
    directory: str | Path,
) -> UniverseCoverageCriterion:
    """Scan ``*.yml`` universe definitions for the full-history start.

    A definition that is not in the enabled set (``enabled: false``) is skipped
    and recorded.  Every other file must load and validate: a parse or schema
    failure blocks publication rather than silently shrinking the criterion.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return UniverseCoverageCriterion(None, {}, ())
    hashes: dict[str, str] = {}
    starts: list[date] = []
    skipped: list[str] = []
    for path in sorted(directory.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise UniverseCoverageError(
                f"universe definition {path.name} must be a YAML mapping"
            )
        if document.get("enabled", True) is False:
            skipped.append(path.name)
            continue
        payload = {
            key: value for key, value in document.items() if key != "enabled"
        }
        try:
            definition = UniverseDefinition.model_validate(payload)
        except ValidationError as error:
            raise UniverseCoverageError(
                f"enabled universe definition {path.name} does not validate: "
                f"{error.error_count()} error(s)"
            ) from None
        if definition.universe_id in hashes:
            raise UniverseCoverageError(
                f"duplicate universe_id {definition.universe_id!r} in {directory}"
            )
        hashes[definition.universe_id] = definition.version
        starts.append(definition.coverage_start)
    return UniverseCoverageCriterion(
        acceptance_start=min(starts) if starts else None,
        definition_hashes=dict(sorted(hashes.items())),
        skipped=tuple(skipped),
    )
```

`dataclass` / `ValidationError` import 若缺失则补齐：`from dataclasses import dataclass`、`from pydantic import ValidationError`。

修改 `configs/universes/csi300.yml`：在文件头注释块之后、`schema_version` 之前插入一行（占位模板必须永不进入验收判据）：

```yaml
enabled: false   # TEMPLATE ONLY: never counted into full-history acceptance
```

并在头部注释里补一句：

```
# `enabled: false` keeps this template out of the publish-time full-history
# criterion (`load_universe_coverage_criterion`); fill in real values and set
# `enabled: true` when the definition becomes usable.
```

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_research_universe.py -v`
Expected: PASS（全绿）

- [x] **Step 5: 提交**

```bash
git add src/stock_quant/research/universe.py configs/universes/csi300.yml tests/unit/test_research_universe.py
git commit -m "feat(research): scan enabled universe definitions for the full-history start

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: bootstrap 种子 span 与 manifest 读取侧

**Files:**
- Modify: `src/stock_quant/data_model/dataset.py`（`DatasetContext.manifest`）
- Modify: `src/stock_quant/bootstrap.py`（`build_config` 写入 `calendar_coverage`）
- Modify: `tests/smoke/test_small_market_download.py`（合成基线写 `bootstrap_seed` span）
- Test: `tests/unit/test_probe_dataset_gates.py` 或 `tests/unit/test_acceptance_service.py` 里已有的 bootstrap 断言文件；本任务新增 `tests/unit/test_bootstrap_calendar_seed.py`

**Interfaces:**
- Consumes: `seed_span`、`coverage_payload`（Task 1）
- Produces：
  - `DatasetContext.manifest: Mapping[str, Any]`（带默认值的新字段，位置构造不受影响；`DatasetReader.open` 传入已解析的 manifest）
  - `bootstrap_dataset` 的 `build_config` 含 `"origin": "bootstrap"`、`"pipeline_contract_version"`、`"calendar_coverage": [seed_payload]`、`"full_history_acceptance_start": null`（bootstrap 不扫描 universe 定义；离线可用性优先）
  - `BootstrapResult.start` / `.end` 是种子 span 的自然日边界

- [x] **Step 1: 写失败的测试**

创建 `tests/unit/test_bootstrap_calendar_seed.py`：

```python
"""A bootstrap dataset must explain its calendar as an offline seed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.data_model.calendar_coverage import SOURCE_BOOTSTRAP_SEED
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "project.yml").write_text(
        yaml.safe_dump(
            {
                "start_date": "2020-01-01",
                "end_date": "2020-01-31",
                "benchmark_symbols": ["000300.SH"],
                "publication_time": "15:00",
            }
        ),
        encoding="utf-8",
    )
    (configs / "universe.yml").write_text(
        yaml.safe_dump(
            {
                "selected_as_of": "2020-01-01",
                "entries": [
                    {
                        "symbol": "600000.SH",
                        "name_at_selection": "浦发银行",
                        "exchange": "SH",
                        "board": "sh_main",
                        "selected_as_of": "2020-01-01",
                        "boundary_tags": ["engineering_smoke"],
                        "selection_reason": "种子日历单元测试",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_bootstrap_writes_one_seed_span_and_exposes_it(project_root):
    result = bootstrap_dataset(project_root)
    with DatasetReader(project_root).open(result.version) as context:
        build = context.manifest["build_config"]
    assert build["calendar_coverage"] == [
        {
            "start_date": result.start.isoformat(),
            "end_date": result.end.isoformat(),
            "source": SOURCE_BOOTSTRAP_SEED,
        }
    ]
    assert build["full_history_acceptance_start"] is None


def test_bootstrap_span_payload_is_the_published_manifest_bytes(project_root):
    result = bootstrap_dataset(project_root)
    manifest_path = (
        DatasetPublisher(project_root).standardized_root
        / result.version
        / "dataset_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    span = manifest["build_config"]["calendar_coverage"][0]
    assert "snapshot_sha256s" not in span
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_bootstrap_calendar_seed.py -v`
Expected: FAIL — `KeyError: 'calendar_coverage'`

- [x] **Step 3: 写实现**

`src/stock_quant/data_model/dataset.py`：`DatasetContext` 增加字段（放在最后，带默认值，保证既有构造点不受影响）：

```python
@dataclass
class DatasetContext:
    """A pinned, read-only dataset version for factor and research queries."""

    version: str
    path: Path
    tables: tuple[str, ...]
    connection: duckdb.DuckDBPyConnection
    #: The parsed ``dataset_manifest.json`` of this version.  Exposed so the
    #: pipeline and the acceptance chain can read the sanitized build
    #: evidence (calendar coverage, raw bindings) without re-reading bytes.
    manifest: Mapping[str, Any] = field(default_factory=dict)
```

`DatasetReader.open` 的返回改为 `manifest=manifest`（`field` / `Mapping` / `Any` import 按需补齐）：

```python
        return DatasetContext(
            version=version,
            path=version_dir,
            tables=tables,
            connection=connection,
            manifest=manifest,
        )
```

`src/stock_quant/bootstrap.py`：import `from stock_quant.data_model.calendar_coverage import coverage_payload, seed_span`，发布段改为：

```python
    version = DatasetPublisher(root).publish(
        tables,
        QualityReport(),
        build_config={
            "origin": "bootstrap",
            "pipeline_contract_version": DATASET_BUILD_CONTRACT_VERSION,
            # The offline seed is the only calendar source a brand-new project
            # has.  It is published as an explicit bootstrap_seed span so the
            # first data update can tell "approximated weekdays" apart from
            # relay facts -- and so full-history acceptance can refuse to call
            # a seed day a verified trading day.  No universe scan happens here
            # (bootstrap must stay offline): the acceptance start is bound by
            # the first data update that actually scans the definitions.
            "calendar_coverage": coverage_payload([seed_span(start, end)]),
            "full_history_acceptance_start": None,
        },
    ).version
```

`tests/smoke/test_small_market_download.py`：`build_smoke_project` 的发布段改为带最小 build config：

```python
    DatasetPublisher(root).publish(
        tables,
        QualityReport(),
        build_config={
            "origin": "bootstrap",
            "pipeline_contract_version": DATASET_BUILD_CONTRACT_VERSION,
            "calendar_coverage": coverage_payload(
                [seed_span(baseline_start, window_end)]
            ),
            "full_history_acceptance_start": None,
        },
    )
```

（import `DATASET_BUILD_CONTRACT_VERSION`、`coverage_payload`、`seed_span`。）

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_bootstrap_calendar_seed.py tests/unit/test_acceptance_service.py -v`
Expected: PASS

再跑 bootstrap 的既有用例：

Run: `pytest tests/smoke/test_small_market_download.py --collect-only -q`
Expected: 收集成功（无 token 时用例 skip）

- [x] **Step 5: 提交**

```bash
git add src/stock_quant/data_model/dataset.py src/stock_quant/bootstrap.py tests/smoke/test_small_market_download.py tests/unit/test_bootstrap_calendar_seed.py
git commit -m "feat(bootstrap): publish the offline calendar seed as a coverage span

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: `--end` 规则切换，删除全部时钟启发式发现 API

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（删 `_discover_end`/`resolve_latest_complete_date`/`_nominal_candidate`/`_previous_open_day`/`_day_complete`/`SourceCoverage`；`DataUpdateResult` 去掉 `resolved_end_is_fallback`；`dataset_build_config` 去掉该键；`_result` 去掉该参数；`update()` 换终点规则）
- Modify: `src/stock_quant/cli.py:273-283`（删 fallback 输出；`--end` 帮助文案）
- Modify: `src/stock_quant/config.py:66-68`（`publication_time` 文档）
- Modify: `src/stock_quant/research/acceptance/checks.py`（若有引用 `SourceCoverage`/`resolve_latest_complete_date`，改为直接读已发布日历最大值）
- Modify: `tests/integration/conftest.py:437`（删 `resolved_end_is_fallback=False`）
- Modify: `tests/integration/test_data_pipeline.py`（删两个 discovery 用例，新增终点规则用例，清理 import）
- Test: `tests/integration/test_data_pipeline.py`

**Interfaces:**
- Consumes: 无新依赖
- Produces：
  - `CODE_CALENDAR_EMPTY_NO_END = "calendar_empty_requires_explicit_end"`
  - `DataUpdateRequest.end_date=None` → `resolved_end = max(published calendar open days)`；日历为空 → FATAL `calendar_empty_requires_explicit_end` 且不发布
  - `dataset_build_config` 不再有 `resolved_end_is_fallback` 键

- [x] **Step 1: 写失败的测试**

在 `tests/integration/test_data_pipeline.py` 中**删除**以下两个用例及其专用 helper（若 helper 只服务它们）：

- `test_discovery_passes_configured_publication_time_to_resolver`（约 1091 行）
- `test_discovery_reports_fallback_when_a_benchmark_series_lags`（约 1105 行）

并新增（放在同一区域）：

```python
def test_update_without_end_uses_the_max_published_calendar_date(project):
    """``--end`` omission means the newest published calendar day, nothing else.

    No clock is consulted: the resolved end is exactly the maximum
    ``trading_calendar.calendar_date`` of the carried dataset (2022-01-07,
    years before "today"), never the current natural day.
    """
    with DatasetReader(project.root).open(project.version) as context:
        published = context.read("trading_calendar")
    expected = max(published["calendar_date"]).date()
    assert expected != date.today()
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=None)
    )
    assert result.resolved_end_date == expected
    assert not hasattr(result, "resolved_end_is_fallback")


def test_update_without_end_fails_when_no_calendar_is_published(tmp_path):
    """An empty published calendar must ask the operator for an explicit --end."""
    project = build_fixture_project(tmp_path / "project")
    publisher = DatasetPublisher(project.root)
    with DatasetReader(project.root).open(project.version) as context:
        tables = {name: context.read(name) for name in context.tables}
    tables["trading_calendar"] = tables["trading_calendar"].iloc[0:0]
    publisher.publish(tables, QualityReport())
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=None, end_date=None)
    )
    assert result.dataset_ref is None
    assert CODE_CALENDAR_EMPTY_NO_END in result.quality_report.by_code()
```

导入 `CODE_CALENDAR_EMPTY_NO_END`，在文件顶部的 `from stock_quant.data_pipeline import (...)` 里删掉 `SourceCoverage` / `resolve_latest_complete_date`，并把文件 docstring 第 4-5 行的

```
``resolve_latest_complete_date`` is tested as a pure
function; ``DataPipeline.update`` / ``DataPipeline.validate`` run against stub
```

改为

```
``DataPipeline.update`` / ``DataPipeline.validate`` run against stub
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/integration/test_data_pipeline.py -k "without_end" -v`
Expected: FAIL — `ImportError: cannot import name 'CODE_CALENDAR_EMPTY_NO_END'`

- [x] **Step 3: 写实现**

`src/stock_quant/data_pipeline.py`：

1. 删除 `SourceCoverage`、`_nominal_candidate`、`resolve_latest_complete_date`、`_previous_open_day`、`_day_complete`，以及 `DataUpdateResult.resolved_end_is_fallback` 字段与文档字符串段落；新增代码常量：

```python
CODE_CALENDAR_EMPTY_NO_END = "calendar_empty_requires_explicit_end"
```

2. `dataset_build_config` 去掉 `resolved_end_is_fallback` 形参与 `"resolved_end_is_fallback": ...` 键。

3. `_result` 去掉 `resolved_end_is_fallback` 关键字参数，`DataUpdateResult(...)` 不再传该字段。

4. `update()` 的终点解析段（原 584-607 行）替换为：

```python
        # ---- end resolution: the published calendar is the only clock ----- #
        end = request.end_date
        if end is None:
            if not calendar_open:
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_CALENDAR_EMPTY_NO_END,
                        details={
                            "message": (
                                "the published trading_calendar has no open day; "
                                "pass an explicit --end"
                            )
                        },
                    )
                )
                return self._result(
                    issues, None, run_id, None, statuses, raw_snapshots
                )
            end = calendar_open[-1]
        resolved_end: date | None = end
```

（`calendar_open` 已按日期排序，`[-1]` 即最大日期；`start` 计算与 `end < start` 校验保持原样紧跟其后。）

5. 删除所有 `resolved_end_is_fallback=end_fallback` 实参（共 7 处 `self._result(...)` 调用点）与 `end_fallback` 变量。

6. 清理因删除而不再使用的 import（`TradingCalendar`、`from datetime import time as dt_time` 及 `SourceCoverage` 相关的 `Mapping` 用途）——以 `ruff check src/stock_quant/data_pipeline.py` 的输出为准逐条删除，直至零告警。

`src/stock_quant/cli.py`：删掉 `typer.echo(f"resolved_end_is_fallback=...")` 与随后的 `if result.resolved_end_is_fallback:` 提示块；`--end` 帮助文案改为：

```python
    end: str | None = typer.Option(
        None,
        "--end",
        help="Inclusive end (YYYY-MM-DD). Defaults to the newest published calendar day.",
    ),
```

`src/stock_quant/config.py`：`publication_time` 的 description 改为：

```python
    publication_time: dt_time = Field(
        default=dt_time(15, 0),
        description=(
            "Recorded market publication time. Informational only: update end "
            "dates come from the published trading calendar, never from the clock."
        ),
    )
```

`tests/integration/conftest.py`：`fixture_build_config` 删去 `resolved_end_is_fallback=False,`。

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/integration/test_data_pipeline.py -k "without_end" -v`
Expected: PASS（2 passed）

Run: `pytest tests/integration/test_data_pipeline.py -v`
Expected: PASS（全绿；失败项若断言了 `resolved_end_is_fallback` 或 discovery API，按新契约改写断言，不要恢复已删除的 API）

Run: `pytest tests/integration/test_raw_provenance_chain.py tests/unit/test_raw_store.py -v`
Expected: PASS（`pipeline_contract_version == 1` 的守卫不变）

- [x] **Step 5: 提交**

```bash
git add -u src/stock_quant tests/
git commit -m "refactor(pipeline): resolve the update end from the published calendar

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: `update()` 的日历刷新、manifest 证据与发布期 span 门

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（`_read_baseline` 返回 spans；新增 `_refresh_calendar`；`dataset_build_config` 新增四个键；`update()` 插入日历步骤与发布期校验；`_calendar_open_days` helper 抽出复用）
- Modify: `tests/integration/test_data_pipeline.py`（`StubAdapter` 支持 `trade_cal`；新增日历用例）
- Modify: `tests/integration/test_acceptance_checks.py`、`tests/unit/test_acceptance_service.py`（各自的 `StubAdapter` 支持 `trade_cal`）
- Test: `tests/integration/test_data_pipeline.py`

**Interfaces:**
- Consumes: Task 1/2/3/4/5 的全部 API；`UniverseCoverageCriterion`
- Produces：
  - `_refresh_calendar(start, end, issues, statuses, raw_snapshots, baseline_spans) -> tuple[tuple[date, ...], tuple[CalendarCoverageSpan, ...]] | None`
  - `dataset_build_config(..., calendar_spans, acceptance_start, definition_hashes, skipped_definitions)` 写入 `calendar_coverage` / `full_history_acceptance_start` / `universe_coverage_definition_hashes` / `universe_coverage_skipped`
  - 发布前 `merge_window(...)`：不覆盖已发布开市日范围或产生空洞 → FATAL `calendar_coverage_gap` / `calendar_uncovered`，不发布

- [x] **Step 1: 写失败的测试**

先给 `tests/integration/test_data_pipeline.py` 的 `StubAdapter` 加 `trade_cal`。

`fetch` 的既有前缀（记录 call、`failing_endpoints`、`raise_with`）保持不变，在其后插入一个按 exchange 定向失败的钩子：

```python
        if (
            request.endpoint == "trade_cal"
            and request.params.get("exchange") in self.trade_cal_failing_exchanges
        ):
            raise AuthenticationError(f"{self.name} supplier failure on trade_cal")
```

（用 `AuthenticationError` 而非 `ServerError`：后者是 transient，`fetch_with_retry` 会重试并真的 sleep，测试不该等。）

dataclass 增加四个字段（放在 `stock_basic_list_date_by_symbol` 之后）：

```python
    trade_cal_is_open_by_exchange: dict[tuple[str, date], int] | None = None
    trade_cal_failing_exchanges: tuple[str, ...] = ()
    trade_cal_missing_dates: tuple[date, ...] = ()
    trade_cal_pretrade_overrides: dict[date, str] | None = None
```

`__post_init__` 补两行归一化：

```python
        object.__setattr__(
            self,
            "trade_cal_is_open_by_exchange",
            self.trade_cal_is_open_by_exchange or {},
        )
        object.__setattr__(
            self, "trade_cal_pretrade_overrides", self.trade_cal_pretrade_overrides or {}
        )
```

`_frame` 中 `stock_basic` 分支之后插入分发：

```python
        if request.endpoint == "trade_cal":
            return self._trade_cal_frame(request)
```

并新增方法（放在 `_stock_basic_frame` 附近）：

```python
    def _trade_cal_frame(self, request: DataRequest) -> pd.DataFrame:
        """One row per halo natural day; weekends closed, pretrade chained.

        ``trade_cal_is_open_by_exchange`` is keyed by ``(exchange, date)`` so a
        test can make exactly one exchange disagree; the response carries no
        symbol scope, so the exchange is read from ``request.params``.
        """
        exchange = str(request.params.get("exchange"))
        assert exchange in ("SSE", "SZSE")
        rows: list[dict[str, object]] = []
        current = request.start_date
        while current <= request.end_date:
            if current not in self.trade_cal_missing_dates:
                previous = current - timedelta(days=1)
                while previous.weekday() >= 5:
                    previous -= timedelta(days=1)
                rows.append(
                    {
                        "exchange": exchange,
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": self.trade_cal_is_open_by_exchange.get(
                            (exchange, current), 1 if current.weekday() < 5 else 0
                        ),
                        "pretrade_date": self.trade_cal_pretrade_overrides.get(
                            current, previous.strftime("%Y%m%d")
                        ),
                    }
                )
            current += timedelta(days=1)
        return pd.DataFrame(rows)
```

`tests/integration/conftest.py` 必须在本任务一起改：`dataset_build_config` 的新形参是必填的，fixture 不补 span，Step 4 会整片报错。

`_fixture_fetch_results()` 的返回元组里追加两份 trade_cal 录制帧：

```python
        _tushare_trade_cal_result("SSE", _UPDATE_WINDOW_START, _UPDATE_WINDOW_END),
        _tushare_trade_cal_result("SZSE", _UPDATE_WINDOW_START, _UPDATE_WINDOW_END),
```

并在 `_fixture_fetch_results` 之后新增 helper：

```python
def _tushare_trade_cal_result(exchange: str, start: date, end: date) -> FetchResult:
    """A recorded ``trade_cal`` halo response for one exchange.

    Weekends are closed and ``pretrade_date`` chains to the previous weekday,
    matching the fixture ``trading_calendar`` (see ``_weekdays``), so a fixture
    window's continuity check passes.
    """
    request = DataRequest(
        "trade_cal",
        (),
        start - timedelta(days=1),
        end + timedelta(days=1),
        {"exchange": exchange},
    )
    rows: list[dict[str, object]] = []
    current = request.start_date
    while current <= request.end_date:
        previous = current - timedelta(days=1)
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
        rows.append(
            {
                "exchange": exchange,
                "cal_date": current.strftime("%Y%m%d"),
                "is_open": 1 if current.weekday() < 5 else 0,
                "pretrade_date": previous.strftime("%Y%m%d"),
            }
        )
        current += timedelta(days=1)
    return FetchResult(
        source="tushare",
        endpoint="trade_cal",
        request_key=request_key(request),
        frame=pd.DataFrame(rows),
        metadata={
            "source": "tushare",
            "sdk_version": "fixture",
            "transport_id": "api.waditu.com",
        },
    )
```

`fixture_build_config` 改为（`_fixture_fetch_results()` 只求值一次，快照与结果按下标配对）：

```python
def fixture_build_config(project_root: Path) -> dict[str, object]:
    """The sanitized data-update build evidence bound into trusted fixtures."""
    store = RawStore(project_root)
    results = _fixture_fetch_results()
    snapshots = tuple(store.save(result) for result in results)
    calendar_hashes = {
        exchange: [
            snapshot.sha256
            for snapshot, result in zip(snapshots, results)
            if result.endpoint == "trade_cal"
            and result.frame["exchange"].iloc[0] == exchange
        ]
        for exchange in ("SSE", "SZSE")
    }
    return dataset_build_config(
        run_id=_UPDATE_RUN_ID,
        request=DataUpdateRequest(
            start_date=_UPDATE_WINDOW_START, end_date=_UPDATE_WINDOW_END
        ),
        effective_start_date=_UPDATE_WINDOW_START,
        resolved_end_date=_UPDATE_WINDOW_END,
        statuses=_fixture_source_statuses(),
        raw_snapshots=snapshots,
        calendar_spans=(
            supplier_span(
                CAL_START,
                CAL_END,
                source=SOURCE_TUSHARE_RELAY,
                snapshots_by_exchange=calendar_hashes,
            ),
        ),
        acceptance_start=CAL_START,
        definition_hashes={
            _WF_UNIVERSE_ID: load_universe_definition(
                project_root / "configs" / "universes" / f"{_WF_UNIVERSE_ID}.yml"
            ).version
        },
        skipped_definitions=(),
    )
```

同时 import 追加：

```python
from stock_quant.data_model.calendar_coverage import (
    SOURCE_TUSHARE_RELAY,
    supplier_span,
)
from stock_quant.research.universe import load_universe_definition
```

`tests/integration/test_data_pipeline.py` 顶部把 conftest 的日期常量一并导入（该文件已有 `from conftest import build_fixture_project`）：

```python
from conftest import CAL_END, CAL_START, build_fixture_project  # noqa: E402
```

新增用例：

```python
def test_update_records_two_trade_cal_snapshots_and_binds_a_relay_span(project):
    """A successful update binds both exchanges' raw calendars into the manifest."""
    stub = StubAdapter("tushare")
    result = DataPipeline(project.root, sources=_all_stubs(tushare=stub)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is not None
    assert [call for call in stub.calls if call[0] == "trade_cal"] == [
        ("trade_cal", None),
        ("trade_cal", None),
    ]
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        build = context.manifest["build_config"]
        calendar = context.read("trading_calendar")
    relay = [
        span
        for span in build["calendar_coverage"]
        if span["source"] == "tushare_relay"
    ]
    assert len(relay) == 1
    # The fixture's carried span already covers [CAL_START, CAL_END] and the
    # window sits inside it, so the merged span keeps those bounds.
    assert relay[0]["start_date"] == CAL_START.isoformat()
    assert relay[0]["end_date"] == CAL_END.isoformat()
    assert sorted(relay[0]["snapshot_sha256s"]) == ["SSE", "SZSE"]
    fresh = set(result.raw_snapshots)
    for hashes in relay[0]["snapshot_sha256s"].values():
        assert set(hashes) & fresh, "this round's snapshot hash must be bound"
    assert build["full_history_acceptance_start"] == CAL_START.isoformat()
    assert build["universe_coverage_definition_hashes"]
    assert build["universe_coverage_skipped"] == []
    assert "resolved_end_is_fallback" not in build
    assert max(calendar["calendar_date"]).date() == CAL_END


def test_update_fails_when_the_two_exchanges_disagree(project):
    """One exchange calling 2021-11-10 closed is an SSE/SZSE conflict."""
    stub = StubAdapter(
        "tushare", trade_cal_is_open_by_exchange={("SZSE", date(2021, 11, 10)): 0}
    )
    result = DataPipeline(project.root, sources=_all_stubs(tushare=stub)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is None
    assert CODE_CALENDAR_EXCHANGE_MISMATCH in result.quality_report.by_code()
```

```python
def test_update_blocks_on_a_broken_pretrade_chain(project):
    """Both exchanges agree on a wrong pretrade link; the chain kills the run."""
    stub = StubAdapter(
        "tushare",
        trade_cal_pretrade_overrides={date(2021, 11, 10): "20211101"},
    )
    result = DataPipeline(project.root, sources=_all_stubs(tushare=stub)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is None
    assert CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN in result.quality_report.by_code()
    assert result.raw_snapshots, "a blocked calendar run still records raw responses"


def test_update_keeps_current_and_raw_when_the_calendar_fetch_fails(project):
    """A half-fetched calendar is fatal: raw kept, CURRENT untouched, no replay."""
    before = DatasetPublisher(project.root).current().version
    partial = StubAdapter("tushare", trade_cal_failing_exchanges=("SZSE",))
    result = DataPipeline(project.root, sources=_all_stubs(tushare=partial)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is None
    assert CODE_SOURCE_FETCH_FAILED in result.quality_report.by_code()
    assert DatasetPublisher(project.root).current().version == before
    assert result.raw_snapshots, "the SSE response was already written to the raw store"
    # The next run asks the supplier again for both exchanges; old raw bytes are
    # never replayed as a calendar cache.
    retry = StubAdapter("tushare")
    DataPipeline(project.root, sources=_all_stubs(tushare=retry)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert [call for call in retry.calls if call[0] == "trade_cal"] == [
        ("trade_cal", None),
        ("trade_cal", None),
    ]


def test_update_blocks_a_window_that_leaves_a_hole_in_calendar_coverage(project):
    """A left-side window that is not adjacent to coverage fails the span gate.

    Continuity passes here (the merged table holds no earlier open day at all,
    so the boundary rows land in ``allowed_pre_coverage``), which is how this
    case isolates the post-merge span gate from the chain check.
    """
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=date(2017, 12, 4), end_date=date(2017, 12, 29))
    )
    assert result.dataset_ref is None
    assert CODE_CALENDAR_COVERAGE_GAP in result.quality_report.by_code()


def test_rerun_merges_adjacent_relay_spans_and_keeps_every_snapshot_hash(project):
    """A window that extends coverage merges into the carried relay span."""
    first = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert first.dataset_ref is not None
    with DatasetReader(project.root).open(first.dataset_ref.version) as context:
        before = [
            span
            for span in context.manifest["build_config"]["calendar_coverage"]
            if span["source"] == "tushare_relay"
        ]
    assert len(before) == 1
    before_hashes = {
        exchange: set(hashes)
        for exchange, hashes in before[0]["snapshot_sha256s"].items()
    }
    extended_end = CAL_END + timedelta(days=20)
    second = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(
            start_date=CAL_END + timedelta(days=1), end_date=extended_end
        )
    )
    assert second.dataset_ref is not None
    with DatasetReader(project.root).open(second.dataset_ref.version) as context:
        after = [
            span
            for span in context.manifest["build_config"]["calendar_coverage"]
            if span["source"] == "tushare_relay"
        ]
    assert len(after) == 1, "adjacent same-source spans merge into one"
    assert after[0]["start_date"] == CAL_START.isoformat()
    assert after[0]["end_date"] == extended_end.isoformat()
    fresh = set(second.raw_snapshots)
    for exchange, hashes in after[0]["snapshot_sha256s"].items():
        assert before_hashes[exchange] < set(hashes), "merging never drops evidence"
        assert fresh & set(hashes), "this round's snapshot is bound as well"
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/integration/test_data_pipeline.py -k "trade_cal or calendar" -v`
Expected: FAIL — `AssertionError`（`calendar_coverage` 不在 build_config，或 stub 不响应 `trade_cal`）

- [x] **Step 3: 写实现**

`src/stock_quant/data_pipeline.py`：

1. import 追加：

```python
from stock_quant.data_model.calendar_coverage import (
    SOURCE_TUSHARE_RELAY,
    CalendarCoverageError,
    CalendarCoverageSpan,
    coverage_from_payload,
    coverage_payload,
    merge_window,
    COVERAGE_KEY,
    ACCEPTANCE_START_KEY,
    DEFINITION_HASHES_KEY,
    SKIPPED_DEFINITIONS_KEY,
    supplier_span,
)
from stock_quant.data_model.trade_calendar_facts import (
    EXCHANGES as CALENDAR_EXCHANGES,
    ExchangeCalendarFacts,
    TradeCalendarFactError,
    check_exchange_agreement,
    check_pretrade_continuity,
    materialize_open_days,
    parse_trade_cal_frame,
)
from stock_quant.research.universe import load_universe_coverage_criterion
```

2. 抽出模块级 helper（`_read_baseline` 与 `validate` 共用）：

```python
def _calendar_open_days(calendar_frame: pd.DataFrame) -> tuple[date, ...]:
    """The published open days of one ``trading_calendar`` frame, sorted."""
    return tuple(
        sorted(
            day.date()
            for day, flag in zip(
                calendar_frame["calendar_date"],
                calendar_frame["is_trading_day"],
            )
            if bool(flag)
        )
    )
```

3. `dataset_build_config` 增加四个形参并在返回字典里写入：

```python
        "calendar_coverage": coverage_payload(calendar_spans),
        "full_history_acceptance_start": (
            acceptance_start.isoformat() if acceptance_start is not None else None
        ),
        "universe_coverage_definition_hashes": dict(definition_hashes),
        "universe_coverage_skipped": list(skipped_definitions),
```

4. `_read_baseline` 改为返回 7 元组 `(master, open_days, daily, ca, membership, quarantine, spans)`：在 `with reader.open(...)` 内取 `manifest = context.manifest`，open_days 改用 `_calendar_open_days(calendar_frame)`；出块后：

```python
        build = manifest.get("build_config") if isinstance(manifest, Mapping) else None
        raw_spans = build.get(COVERAGE_KEY, []) if isinstance(build, Mapping) else []
        try:
            spans = coverage_from_payload(raw_spans)
        except CalendarCoverageError as error:
            issues.extend(_calendar_issues(error.violations))
            return None
        return master, open_days, daily, ca, membership, quarantine, spans
```

（legacy 基线没有该键 → `spans = ()`，交由发布期门去要求窗口覆盖整个日历范围并给出可操作的错误。）

5. 新增模块级 severity 映射 helper：

```python
def _calendar_issues(
    violations: Sequence[tuple[str, Mapping[str, object]]],
) -> list[QualityIssue]:
    """Pipeline issues for calendar violations (one shared severity map)."""
    return [
        _issue(
            Severity.WARNING if code in COVERAGE_WARNING_CODES else Severity.FATAL,
            code,
            table="trading_calendar",
            details=dict(details),
        )
        for code, details in violations
    ]
```

（import `COVERAGE_WARNING_CODES`。）

6. `update()`：把 `baseline` 解包改为 7 元组（`..., current_quarantine, baseline_spans`），并在**起点/终点解析完成之后、`_refresh_security_master` 之前**插入日历步骤（必须排在 `_require_available` 之后，`test_disabled_required_source_is_never_called` 才仍然成立；也必须排在终点解析之后，`_refresh_calendar` 才拿得到 `start` / `end`）：

```python
        # ---- required trading-calendar refresh --------------------------- #
        # Runs before every other fetch so a calendar failure blocks the run
        # before any window data is pulled, and so the whole update publishes
        # as one atomic unit with its calendar evidence.
        refreshed = self._refresh_calendar(
            start,
            end,
            published_open_days,
            issues,
            statuses,
            raw_snapshots,
            baseline_spans,
        )
        if refreshed is None:
            return self._result(
                issues, None, run_id, end, statuses, raw_snapshots
            )
        calendar_open, calendar_spans = refreshed
```

7. 新增 `_refresh_calendar`：

```python
    def _refresh_calendar(
        self,
        start,
        end,
        published_open_days,
        issues,
        statuses,
        raw_snapshots,
        baseline_spans,
    ):
        """Fetch, validate and materialise this window's trading calendar.

        Both exchanges are requested over the validation halo ``[start - 1,
        end + 1]`` and saved to the raw store as ordinary responses (also on
        the failure path).  Values are validated per exchange, then compared
        day by day, then used to replace the window's open days and to check
        the ``pretrade_date`` chain over the merged candidate table.  Returns
        ``(open_days, spans)`` or ``None`` after a FATAL issue: a raised
        calendar never publishes, never writes a partial span, and never
        reuses a previous run's raw response (the supplier is re-requested on
        every run).
        """
        source = self._adapter_or_fail("tushare", statuses)
        if source is None:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_SOURCE_FETCH_FAILED,
                    details={
                        "source": "tushare",
                        "endpoint": "trade_cal",
                        "message": (statuses["tushare"].reason or "adapter unavailable"),
                    },
                )
            )
            return None
        halo_start = start - timedelta(days=1)
        halo_end = end + timedelta(days=1)
        config = self._project_config.sources.get("tushare", SourceConfig())
        policy = RetryPolicy(
            max_attempts=min(config.max_retries + 1, 3),
            maximum_wait_seconds=min(config.timeout_seconds, 30),
        )
        facts: dict[str, ExchangeCalendarFacts] = {}
        hashes: dict[str, list[str]] = {exchange: [] for exchange in CALENDAR_EXCHANGES}
        for exchange in CALENDAR_EXCHANGES:
            request = DataRequest(
                "trade_cal", (), halo_start, halo_end, {"exchange": exchange}
            )
            try:
                result = fetch_with_retry(
                    source, request, policy, sleeper=self._sleeper
                )
            except Exception as error:  # noqa: BLE001 - required calendar
                issues.append(
                    _issue(
                        Severity.FATAL,
                        CODE_SOURCE_FETCH_FAILED,
                        details={
                            "source": "tushare",
                            "endpoint": "trade_cal",
                            "exchange": exchange,
                            "message": str(translate_supplier_error(error)),
                        },
                    )
                )
                statuses["tushare"] = SourceStatus(
                    "tushare", True, False,
                    reason=f"trade_cal fetch failed: {error}",
                    reason_code="source_fetch_failed",
                )
                return None
            snapshot = self._record_raw(result)
            raw_snapshots.append(snapshot)
            hashes[exchange].append(snapshot.sha256)
            try:
                facts[exchange] = parse_trade_cal_frame(
                    result.frame,
                    exchange=exchange,
                    halo_start=halo_start,
                    halo_end=halo_end,
                )
            except TradeCalendarFactError as error:
                issues.extend(_calendar_issues(error.violations))
                statuses["tushare"] = SourceStatus(
                    "tushare", True, False,
                    reason=f"trade_cal {exchange} raw validation failed",
                    reason_code="partial_fetch_failure",
                )
                return None
        violations = check_exchange_agreement(facts["SSE"], facts["SZSE"])
        if violations:
            issues.extend(_calendar_issues(violations))
            statuses["tushare"] = SourceStatus(
                "tushare", True, False,
                reason="SSE and SZSE calendars disagree",
                reason_code="partial_fetch_failure",
            )
            return None
        open_days = materialize_open_days(
            published_open_days, facts=facts["SSE"], start=start, end=end
        )
        continuity = check_pretrade_continuity(
            facts["SSE"].rows, open_days, start=start, end=end
        )
        if continuity.violations:
            issues.extend(_calendar_issues(continuity.violations))
            statuses["tushare"] = SourceStatus(
                "tushare", True, False,
                reason="calendar pretrade chain is broken",
                reason_code="partial_fetch_failure",
            )
            return None
        for boundary in continuity.allowed_pre_coverage:
            issues.append(
                _issue(
                    Severity.INFO,
                    "calendar_pre_coverage_boundary",
                    trade_date=boundary,
                    details={"calendar_date": boundary.isoformat()},
                )
            )
        replacement = supplier_span(
            start,
            end,
            source=SOURCE_TUSHARE_RELAY,
            snapshots_by_exchange=hashes,
        )
        try:
            spans = merge_window(
                baseline_spans,
                start=start,
                end=end,
                replacement=replacement,
                open_days=open_days,
            )
        except CalendarCoverageError as error:
            issues.extend(_calendar_issues(error.violations))
            return None
        statuses["tushare"] = SourceStatus("tushare", True, True, reason_code="ok")
        return open_days, spans
```

设计说明（实现时保留为注释）：`SOURCE_TUSHARE_RELAY` 是唯一允许的 span 来源——本计划不引入 official break-glass 分支；若将来要走 break-glass，必须由 transport 的 `kind` 决定 span source，而不是由调用方自由填写。

8. `update()` 中：`_read_baseline` 解包出的 baseline open days 存为局部变量 `published_open_days`，`_materialize_suspensions(... calendar_open ...)` 与本轮 `trading_calendar` 发布改用 `_refresh_calendar` 返回的新 `calendar_open`。**不要**把它挂到 `self` 上——显式传参，与 `_materialize_suspensions` 的既有写法一致。

9. `update()` 的发布段：`dataset_build_config(...)` 传入新证据：

```python
        criterion = load_universe_coverage_criterion(
            self._project_root / "configs" / "universes"
        )
```

（放在发布前；抛 `UniverseCoverageError` 时捕获并转成 FATAL `calendar_coverage_invalid`，details 只带异常类名与文件名的稳定部分——**不带**异常文本里的路径细节。实际写法：）

```python
        try:
            criterion = load_universe_coverage_criterion(
                self._project_root / "configs" / "universes"
            )
        except UniverseCoverageError as error:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_UNIVERSE_DEFINITION_INVALID,
                    details={"message": str(error)},
                )
            )
            return self._result(issues, None, run_id, end, statuses, raw_snapshots)
```

`CODE_UNIVERSE_DEFINITION_INVALID = "universe_definition_invalid"`（本任务新增常量）。随后：

```python
                build_config=dataset_build_config(
                    run_id=run_id,
                    request=request,
                    effective_start_date=start,
                    resolved_end_date=end,
                    statuses=statuses,
                    raw_snapshots=raw_snapshots,
                    calendar_spans=calendar_spans,
                    acceptance_start=criterion.acceptance_start,
                    definition_hashes=criterion.definition_hashes,
                    skipped_definitions=criterion.skipped,
                ),
```

10. `tests/integration/test_acceptance_checks.py` 与 `tests/unit/test_acceptance_service.py` 的 `StubAdapter` 各加一份等价的 `trade_cal` 分支（与 Step 1 相同实现）。

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/integration/test_data_pipeline.py -v`
Expected: PASS（全绿）

Run: `pytest tests/integration/test_acceptance_checks.py tests/unit/test_acceptance_service.py tests/integration/test_acceptance_cli.py -v`
Expected: PASS

Run: `ruff check src/stock_quant/data_pipeline.py`
Expected: 无输出（零告警）

- [x] **Step 5: 提交**

```bash
git add -u src/stock_quant tests/
git commit -m "feat(pipeline): bind the relay trading calendar into every update

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: `data validate` 的日历证据检查

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`（`validate()` 增加日历证据检查）
- Test: `tests/integration/test_data_pipeline.py`、`tests/integration/test_cli.py`

**Interfaces:**
- Consumes: `validate_build_calendar_evidence`（Task 1）、`DatasetContext.manifest`（Task 5）、Task 4 的判据、Task 7 的 fixture relay span
- Produces: `DataPipeline.validate()` 对 `CURRENT`/指定版本同时校验 span 格式/排序/重叠/空洞/覆盖与全历史种子，且 legacy manifest 报 `calendar_coverage_missing`

- [x] **Step 1: 写失败的测试**

在 `tests/integration/test_data_pipeline.py` 追加：

```python
def test_validate_reports_calendar_evidence_of_the_fixture(project):
    report = DataPipeline(project.root).validate()
    codes = report.by_code()
    assert "calendar_coverage_missing" not in codes
    assert "bootstrap_seed_in_full_history" not in codes
    assert "removed_fallback_field_present" not in codes
    assert report.by_severity()[Severity.FATAL.value] == 0


def test_validate_flags_a_legacy_manifest_without_calendar_coverage(project):
    """A pre-calendar-manifest dataset cannot claim calendar provenance."""
    with DatasetReader(project.root).open(project.version) as context:
        tables = {name: context.read(name) for name in context.tables}
        build = dict(context.manifest["build_config"])
    build.pop("calendar_coverage")
    build.pop("full_history_acceptance_start")
    version = DatasetPublisher(project.root).publish(
        tables, QualityReport(), build_config=build
    ).version
    codes = DataPipeline(project.root).validate(version).by_code()
    assert "calendar_coverage_missing" in codes


def test_validate_ignores_a_compatible_legacy_fallback_field_but_not_a_new_one(project):
    with DatasetReader(project.root).open(project.version) as context:
        tables = {name: context.read(name) for name in context.tables}
        build = dict(context.manifest["build_config"])
    legacy = dict(build)
    legacy.pop("calendar_coverage")
    legacy.pop("full_history_acceptance_start")
    legacy["resolved_end_is_fallback"] = False
    legacy_version = DatasetPublisher(project.root).publish(
        tables, QualityReport(), build_config=legacy
    ).version
    assert "removed_fallback_field_present" not in DataPipeline(
        project.root
    ).validate(legacy_version).by_code()
    fresh = dict(build, resolved_end_is_fallback=False)
    fresh_version = DatasetPublisher(project.root).publish(
        tables, QualityReport(), build_config=fresh
    ).version
    assert "removed_fallback_field_present" in DataPipeline(
        project.root
    ).validate(fresh_version).by_code()
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/integration/test_data_pipeline.py -k "validate_reports_calendar or validate_flags_a_legacy or validate_ignores_a_compatible" -v`
Expected: FAIL — `test_validate_flags_a_legacy_manifest_without_calendar_coverage` 与 `test_validate_ignores_a_compatible_legacy_fallback_field_but_not_a_new_one` 的 `calendar_coverage_missing` 断言失败（`validate()` 还没接日历检查）

- [x] **Step 3: 写实现**

`tests/integration/conftest.py` 已在 Task 7 改完（两份 trade_cal 录制帧、relay span、验收起点与真实 `definition_hashes`），本任务不再改它。

`src/stock_quant/data_pipeline.py` 的 `validate()`：**先在 `with` 块内**取出 manifest（连接关闭后不要再读表）：

```python
            calendar_frame = context.read("trading_calendar")
            build = (
                context.manifest.get("build_config")
                if isinstance(context.manifest, Mapping)
                else None
            )
```

再在 `issues.extend(self._master_coverage_consistency_issues(...))` 之后追加日历证据检查：

```python
        issues.extend(
            _calendar_issues(
                validate_build_calendar_evidence(
                    build, open_days=_calendar_open_days(calendar_frame)
                )
            )
        )
```

（`calendar_frame` 与 `build` 都在 `with` 块外可读——它们是普通对象，不是游标。）

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/integration/test_data_pipeline.py -v`
Expected: PASS

Run: `pytest tests/integration/test_cli.py tests/integration/test_research_runner.py tests/integration/test_factor_no_lookahead.py tests/integration/test_walk_forward_runner.py -v`
Expected: PASS（这些文件都用 conftest 的 fixture 数据集或 `data update` 的产物；若有用例自建无 span 的清单并调用 validate，按新契约补 span，不要放宽校验）

- [x] **Step 5: 提交**

```bash
git add -u src/stock_quant tests/
git commit -m "feat(validate): check calendar coverage evidence and legacy manifests

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: 接受链新增 `calendar_coverage_evidence`

**Files:**
- Modify: `src/stock_quant/research/acceptance/models.py:80-88`（`AUTOMATED_CHECK_CODES` 加一项）
- Modify: `src/stock_quant/research/acceptance/checks.py`（新增 `_check_calendar_coverage` 并注册）
- Modify: `tests/unit/test_acceptance_models.py:336-345`（码清单）
- Modify: `tests/integration/test_acceptance_checks.py`（补一条日历失败变异）
- Test: `tests/unit/test_acceptance_models.py`、`tests/integration/test_acceptance_checks.py`

**Interfaces:**
- Consumes: `dataset_evidence`、`_build_config`、`validate_build_calendar_evidence`、Task 8 的 fixture span
- Produces: 自动检查码 `calendar_coverage_evidence`，任何日历证据违规（含 `full_history_acceptance_start_missing`）→ FAIL

- [x] **Step 1: 写失败的测试**

`tests/unit/test_acceptance_models.py`：在 `AUTOMATED_CHECK_CODES` 的断言元组中，`"source_role_health",` 之前插入 `"calendar_coverage_evidence",`（新增检查排在语义检查之后、源角色检查之前）。

`tests/integration/test_acceptance_checks.py`：给 `_apply_mutation` 追加两条日历变异分支

```python
    elif mutation == "strip_calendar_coverage":
        build_config.pop("calendar_coverage")
        build_config.pop("full_history_acceptance_start")
    elif mutation == "seed_inside_full_history":
        spans = build_config["calendar_coverage"]
        build_config["calendar_coverage"] = [
            {
                "start_date": spans[0]["start_date"],
                "end_date": spans[-1]["end_date"],
                "source": "bootstrap_seed",
            }
        ]
```

（种子 span 没有 `snapshot_sha256s` 字段——这正是种子 payload 的形状。）

在既有的参数化表里追加两行

```python
        ("strip_calendar_coverage", "calendar_coverage_evidence"),
        ("seed_inside_full_history", "calendar_coverage_evidence"),
```

并新增一条断言 details 码的用例：

```python
def test_calendar_coverage_evidence_names_the_missing_manifest_key(mutated_project):
    """A manifest without calendar evidence fails with a stable, parseable code."""
    project = mutated_project("strip_calendar_coverage")
    checks = _checks_by_code(run_automated_checks(_input(project)))
    result = checks["calendar_coverage_evidence"]
    assert result.status is CheckStatus.FAIL
    assert result.details["code"] == "calendar_coverage_missing"
```

- [x] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_acceptance_models.py::test_policy_check_codes_are_fixed tests/integration/test_acceptance_checks.py -k calendar -v`
Expected: FAIL — 码清单不匹配 / 未注册

- [x] **Step 3: 写实现**

`models.py`：`AUTOMATED_CHECK_CODES` 变为：

```python
AUTOMATED_CHECK_CODES = (
    "dataset_manifest_integrity",
    "quality_report_integrity",
    "required_table_coverage",
    "date_window_completeness",
    "security_master_evidence",
    "corporate_action_evidence",
    "raw_snapshot_traceability",
    "calendar_coverage_evidence",
    "source_role_health",
)
```

`checks.py`：`run_automated_checks` 的 `functions` 字典加一项，并新增：

```python
def _check_calendar_coverage(value: AcceptanceCheckInput) -> CheckResult:
    """Require every published calendar day to name its source, and no seed
    inside the version-bound full-history window.

    The criterion is read from the dataset's own ``build_config`` (the
    ``full_history_acceptance_start`` fixed at publish time), never recomputed
    from the current ``configs/universes`` tree: a later definition change must
    not retroactively re-judge an already-published version.
    """
    evidence = dataset_evidence(value)
    build = _build_config(evidence.manifest)
    with DatasetReader(value.project_root).open(value.dataset_version) as (
        context
    ):
        calendar = context.read("trading_calendar")
    violations = validate_build_calendar_evidence(
        build, open_days=_open_days(calendar)
    )
    failures = [
        [code, _calendar_subject(details)] for code, details in violations
    ]
    return _result("calendar_coverage_evidence", failures)


def _calendar_subject(details: Mapping[str, Any]) -> str:
    """A deterministic ``key=value`` subject for one calendar violation.

    Mirrors the other checks' ``[reason_code, subject]`` failure rows: only
    stable string detail values are rendered, so identical evidence always
    yields identical rows and no path or exception text can leak.
    """
    parts = [
        f"{key}={value}"
        for key, value in sorted(details.items())
        if isinstance(value, str)
    ]
    return " ".join(parts) if parts else "trading_calendar"
```

（`validate_build_calendar_evidence` 的 import 补齐：`from stock_quant.data_model.calendar_coverage import validate_build_calendar_evidence`。`json` 不需要——failures 行沿用本模块既有的 `[reason_code, subject]` 形状，`_result` 会把 `details["code"]` 设为首个（按字母序）原因码。）

- [x] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_acceptance_models.py tests/integration/test_acceptance_checks.py tests/integration/test_acceptance_cli.py tests/integration/test_acceptance_registry.py tests/unit/test_acceptance_service.py -v`
Expected: PASS

Run: `pytest tests/integration/test_data_pipeline.py -v`
Expected: PASS（fixture ACCEPTED 记录仍由真实检查器生成并通过）

- [x] **Step 5: 提交**

```bash
git add -u src/stock_quant tests/
git commit -m "feat(acceptance): add the calendar coverage evidence check

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 10: RUNBOOK、运维文档与收尾

**Files:**
- Modify: `project/RUNBOOK.md`（"最新完整交易日 + 发布时间 15:00" 段、全历史窗口、验收判据）
- Modify: `project/configs/project.yml`（`publication_time` 注释）
- Modify: `project/rebuild_trading_calendar.py`（legacy guard）
- Modify: `docs/operations/*`（受影响的验收/操作说明）
- Test: 具名回归集

- [x] **Step 1: 更新 RUNBOOK**

`project/RUNBOOK.md`：把描述发现逻辑的段落改成

```
- `data update` 不带 `--end` 时，终点只能取已发布 `trading_calendar.calendar_date`
  的最大值；已发布日历为空时必须显式传 `--end`。
- 每次 unmarked `data update` 都会向 relay 分别请求 SSE / SZSE 的
  `trade_cal`（halo `[start-1, end+1]`），两份响应都进 raw store。任一请求、
  原始校验、两市比对或 `pretrade_date` 连续性失败都会 FATAL，`CURRENT` 不动。
- 全历史验收：从 manifest 绑定的 `full_history_acceptance_start` 到已发布日历
  最大日期之间，不允许出现 `bootstrap_seed` span。种子只允许留在该起点之前。
- 消除 bootstrap 种子日历的唯一方式：提交一次覆盖全历史的更新窗口，例如
  `data update --start 2015-01-05 --end <已发布日历最大日期>`；窗口必须覆盖
  整个已发布日历范围，否则发布被 `calendar_coverage_gap` / `calendar_uncovered`
  阻断，错误详情里会给出需要覆盖的 `first_open_day` / `last_open_day`。
```

- [x] **Step 2: project 配置与 legacy 脚本**

`project/configs/project.yml`：`publication_time` 行上方注释改为"仅记录用途；更新终点来自已发布交易日历，不再使用时钟"。`project/rebuild_trading_calendar.py` 顶部加 guard：

```python
# This one-off script rebuilds the calendar from benchmark sessions and drops
# every build-evidence field, so the republished dataset can no longer explain
# its calendar provenance.  Use `data update` with an explicit window instead.
raise SystemExit(
    "rebuild_trading_calendar.py is superseded: run "
    "`data update --start <coverage_start> --end <last published calendar day>`"
)
```

- [x] **Step 3: 运维文档**

`docs/operations/` 下凡是写"最新完整交易日 + 回退标记"的验收说明（`2026-09-11-trusted-data-chain.md`、`phase-one-validation.md`）补一句：日历证据由 `calendar_coverage` span 与版本绑定的 `full_history_acceptance_start` 提供，`resolved_end_is_fallback` 已删除；旧 manifest 会被 `data validate` 报 `calendar_coverage_missing`，处置方式是重发布。

- [x] **Step 4: 收尾具名回归**

```bash
pytest tests/unit/test_calendar_coverage.py tests/unit/test_trade_calendar_facts.py tests/unit/test_bootstrap_calendar_seed.py tests/unit/test_research_universe.py tests/unit/test_acceptance_models.py -v
pytest tests/integration/test_source_contracts.py tests/integration/test_data_pipeline.py tests/integration/test_acceptance_checks.py tests/integration/test_acceptance_cli.py tests/integration/test_acceptance_registry.py tests/integration/test_cli.py tests/integration/test_raw_provenance_chain.py -v
pytest tests/unit/test_raw_store.py tests/unit/test_acceptance_service.py tests/unit/test_calendar.py tests/unit/test_suspensions.py -v
ruff check src/stock_quant tests
```

Expected: 全绿；`ruff check` 零告警。**不要**跑 `pytest`（整库）也不要跑 `ruff format --check`（仓库在已安装的 ruff 0.16.5 下从不 format-clean）。

- [x] **Step 5: 提交**

```bash
git add -u project docs
git commit -m "docs(ops): document the relay calendar mainline and its migration

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## 真实数据的一次性落地步骤（代码合入后人工执行，不属于任务清单）

1. `set -a; source .env; set +a; export TUSHARE_TRANSPORT=relay`
2. `python -m stock_quant.cli data validate` —— 现有数据集是 legacy 形状（无 `calendar_coverage`），预期报 FATAL `calendar_coverage_missing` 并以非零码退出；这是设计允许的过渡状态，处置方式是第 3 步的重发布，不是豁免。
3. `python -m stock_quant.cli data update --start 2015-01-05 --end <已发布日历最大日期>` —— 一次覆盖全历史的窗口，消灭全部种子 span；若报 `calendar_uncovered`，按 details 里的 `first_open_day` / `last_open_day` 调整窗口。
4. `python -m stock_quant.cli data validate` —— 预期 PASS，`calendar_coverage` 只有一条 `tushare_relay` span，`full_history_acceptance_start = 2015-01-05`。
5. 验收记录重发布走既有 `project/build_acceptance_evidence.py` 流程。

## 自检记录（Self-Review）

- **Spec 覆盖**：更新流程 6 步 → Task 5/6/7；数据源接口与原始证据 → Task 2 + Task 7 的 `_record_raw`；原始响应校验与固定顺序 → Task 3；跨窗口连续性含 `allowed_pre_coverage` → Task 3；Manifest 覆盖证据（拆分/替换/合并/去重并集/种子不合并/重新校验）→ Task 1 + Task 7；`data validate` 与接受链 → Task 8/9；失败语义 → Task 7 的五个用例 + Task 6 的空日历用例；测试要求 10 条 → 分别落在 Task 1/3/6/7/8/9 的用例与 Task 2 的真实 probe。
- **Type 一致性**：`coverage_payload`/`coverage_from_payload`/`merge_window`/`validate_build_calendar_evidence`/`supplier_span`/`seed_span` 在 Task 1 定义，Task 5/7/8/9 调用同一签名；`parse_trade_cal_frame`/`check_exchange_agreement`/`materialize_open_days`/`check_pretrade_continuity` 在 Task 3 定义，Task 7 调用同一签名；`load_universe_coverage_criterion` 在 Task 4 定义，Task 7 调用。
- **测试与实现的期望值已对齐**：Task 7 的五条集成用例都写成确定断言（两市差异 → `calendar_exchange_mismatch`；断链 → `calendar_pretrade_continuity_broken`；单市抓取失败 → `calendar_coverage` 无新 span、raw 保留、`CURRENT` 不动、重跑仍请求两市；制造空洞 → `calendar_coverage_gap`；相邻扩展 → 合并为一条 relay span 且旧哈希是严格子集）。
