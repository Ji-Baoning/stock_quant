# Panda 嫁接 P2c · `basic_factor` 全链路实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 注册并发布 `basic_factor` 与 `basic_factor_coverage`（spec §7.2/§7.3）：`daily_basic` → 规范化 → per symbol×window 覆盖证据 → `daily_bar`×`basic_factor` 联接一致性门禁 → `build_config.table_lineage` 发布期核对 → §7.4 对应完成条件，不弱化任何既有门禁。

**Architecture:** 新逻辑全部是 `data_model/basic_factor.py` 的纯函数（normalize、coverage 行、下游过滤规则、联接一致性检查），管线只在 `_execute_update` 的既有装配点接线；coverage 表**复用** `corporate_action_coverage` 的 8 列布局与 `CoverageStatus/CoverageReason` 词汇，不新造状态码；`table_lineage` 走"加 key、旧 manifest 兼容读取、处置=重发布"，`pipeline_contract_version` 保持 `1`。

**Tech Stack:** Python 3.12（`/home/ji/miniconda3/envs/sq312/bin/python`）、pandas、pyarrow、pytest。无新依赖。

**Spec:** [2026-09-29-panda-data-loop-grafting-design.md](../specs/2026-09-29-panda-data-loop-grafting-design.md) §7.2/§7.3/§7.4/§6.4/§11。前置批次：**P1**（`daily_basic` 端点、单位实测、transport 胜出值）与 **P2a 全部任务**（词汇 `history_begins_after_anchor`/`source_disabled`/`source_unavailable`、分区校验、发布期门禁 `missing_registered_table`、每表必记录）。

## Global Constraints

- 解释器 `/home/ji/miniconda3/envs/sq312/bin/python`；跑点名测试文件，不跑裸 `pytest`（integration 全量 ≈18.5 分钟）。
- **单位换算唯一效力来源是 §6.4 探针实测**：常量值必须从 `docs/operations/<probe-date>-endpoint-probe-evidence.md` 逐字抄录并注明出处；探针与 ×10000/÷100 假设不符时以探针为准并回写 spec §7.2，探针前不得写死。
- 缺失保持 null 禁止填 0；不复制 `daily_bar` 的任何 OHLCV/amount 列；不提供 Panda 十列宽表（§7.2/§11）。
- **`pipeline_contract_version` 保持 `1`**：`table_lineage` 是新 key，无该键旧 manifest 兼容读取，处置=重发布（§7.3）。
- **真实数据操作（探针复核、真实 update/publish）需 owner 明确授权**；离线任务一律 stub/fixture。
- 两份 sources.yml（`project/configs/sources.yml`、`templates/project-config/sources.yml`）**只追加**两条契约，不触碰在途行。
- 保护在途 WIP（2026-09-30 实查 `git status`）：`RUNBOOK.md`、`docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md`、`src/stock_quant/cli.py`、`src/stock_quant/reporting/html.py`、`src/stock_quant/reporting/templates/experiment.html.j2`、`tests/integration/test_cli.py`、`tests/integration/test_reports.py`——不覆盖/不回退/不暂存；只 `git add` 本任务文件。
- 下游过滤只许 `status == "UNTRUSTED"`，不得 `status != "VERIFIED"`（§7.3）。
- 每任务独立提交，提交信息英文，结尾 `Co-Authored-By: Claude Code <noreply@anthropic.com>`。

## 开工前必须知道的实现形态

1. **P2a 尚未合码**（2026-09-30 实查：`fetch_coverage.py` 仍只有 `operator_explicit_window`）。本计划假定 [P2a 计划](2026-10-01-panda-coverage-evidence-model.md) 已执行；开工先跑 `tests/unit/test_fetch_coverage.py` 确认三个新理由常量存在，否则停下先执行 P2a。
2. **注册即门禁**：P2a 把"当前注册表齐全"装在 `DatasetPublisher.publish`（FATAL `missing_registered_table`），故**注册 schema 与管线装配必须同任务落码**，否则 `data update` 及其集成测试立即全红——这是 Task 1 含"禁用态装配"的原因。
3. 契约一落 `sources.yml`，`plan_table_fetch_windows`（`fetch_windows.py:80`）即给 `basic_factor` 生成 `fetched` 计划；fetch 通道未接线前，诚实记录是整窗/尾段 `not_fetched(source_disabled)`，**绝不能**写出无凭据的 `fetched` 段（Task 1 Step 5 的 override）。
4. `ingested_at` 确定性沿用既有机制 `_ingest_time(result.metadata)`（`data_pipeline.py:3587`，优先 adapter `response_timestamp`），carried 行保留 baseline 值——与 §7.2"重放同一原始响应不改变已落盘值"同强度。
5. coverage 词汇/帮手直接 import：`CoverageStatus`/`CoverageReason`/`coverage_record`/`coverage_frame`（`data_model/corporate_action_coverage.py`），`CORPORATE_ACTION_COVERAGE_SCHEMA` 即本表 schema；`SecurityMasterBoundary(symbol, list_date, last_tradable_date)` 在 `data_model/universe_membership.py:208`（`delist_date` 是终止日、**不是**已证最后可交易日，只能传 None）。
6. `_read_baseline`（`data_pipeline.py:1641`）已 carry 整表与 coverage 帧（quarantine 空表先例，空 canonical frame 可过 `_frame_to_arrow`）；FATAL issue 让 `update()` 在 publish 前失败（`:1392` `fatal_present`）——联接三码走 FATAL + GLOBAL_PROCESS，research_only tier 不得降级放行（§7.4）。
7. 集成 harness 照 `tests/integration/test_pipeline_fetch_coverage.py`：`build_fixture_project(tmp_path / "project")` + `StubAdapter` + `DataPipeline(project.root, sources=...)`；其 `_DECLARED_TABLES` 断言每表必记录，Task 1 必须同步扩它。`dataset_build_config`（`:568`）是 build_config 唯一构造点；`_raw_snapshot_evidence_rows`（`:368`）的行含 `source/endpoint/transport_id/request_key/file_sha256/manifest_sha256`。

## 文件结构

**新增**：`src/stock_quant/data_model/basic_factor.py`（normalize、coverage 行、过滤规则、联接检查，纯函数）；`tests/unit/test_basic_factor.py`；`tests/integration/test_basic_factor_publish.py`。

**修改**：`data_model/schemas.py`（`BASIC_FACTOR_*`）；`data_model/dataset.py`（注册两表）；两份 sources.yml（只追加）；`data_pipeline.py`（carry、装配、段装配、fetch 通道、联接接线、lineage 写点与核对）；`data_quality/models.py`+`gates.py`（新码登记）；`research/acceptance/models.py`+`checks.py`（`table_lineage_evidence`）；`research/runner.py`（预热窗口并入）；`tests/unit/test_data_contracts.py`、`tests/integration/test_pipeline_fetch_coverage.py`、`tests/unit/test_quality_checks.py`、`tests/unit/test_acceptance_checks.py`（既有形态扩展）。

---

### Task 1: schema 注册、契约回填与禁用态装配

**Files:** Modify `src/stock_quant/data_model/schemas.py`（`ADJUSTED_BAR_COLUMNS` 后）、`src/stock_quant/data_model/dataset.py:54-64`、`project/configs/sources.yml:168` 后、`templates/project-config/sources.yml:125` 后（各追加两条）、`src/stock_quant/data_pipeline.py`（`_read_baseline`、tables 装配 `:1283-1307`、段装配 `:1323` 后）；Test `tests/unit/test_data_contracts.py`（既有 backfill 用例即失败测试）、`tests/integration/test_pipeline_fetch_coverage.py`。

**Interfaces:** Produces `BASIC_FACTOR_COLUMNS`/`BASIC_FACTOR_SCHEMA`；`STANDARDIZED_SCHEMAS["basic_factor"]`/`["basic_factor_coverage"]`；契约行（`research_only`/`core`、`tushare:relay` 或探针胜出值、`incremental=last_covered_plus_1`、`conflict=block`、`coverage_shape=per_symbol_window`/`none`）；管线 `_disabled_or_carried_segments(table, covered, anchor, end)`。

- [ ] **Step 1: 注册 schema，跑 backfill 测试确认失败**

`schemas.py` 追加（常量区末尾加 `BASIC_FACTOR_SCHEMA = pa.schema(_basic_factor_fields())`；`dataset.py` import 并在 `STANDARDIZED_SCHEMAS` 追加 `"basic_factor": BASIC_FACTOR_SCHEMA` 与 `"basic_factor_coverage": CORPORATE_ACTION_COVERAGE_SCHEMA`——布局与词汇复用，§7.3）：

```python
# Daily per-symbol factors Stock has no other unique fact source for (spec
# §7.2): market cap and turnover rate ONLY -- no OHLCV, no amount, no panda
# ten-column wide table.  ``ingested_at`` is deterministic provenance.
BASIC_FACTOR_COLUMNS = [
    "trade_date", "symbol", "market_cap", "turnover_rate",
    "source", "ingested_at",
]


def _basic_factor_fields() -> list[pa.Field]:
    return [
        pa.field("trade_date", pa.date32()),
        pa.field("symbol", pa.string()),
        pa.field("market_cap", pa.float64()),
        pa.field("turnover_rate", pa.float64()),
        pa.field("source", pa.string()),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
```

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_data_contracts.py -q -k backfill`
Expected: FAIL — `registered tables without a contract: ['basic_factor', 'basic_factor_coverage']`

- [ ] **Step 2: 两份 sources.yml 只追加契约行**（`data_contracts:` 列表末尾；`<probe-date>` 换实际证据日期，transport 用探针胜出值）

```yaml
  # basic_factor (spec §7.2/§7.3, ADR-022): research_only -- no independent
  # anchor this phase; single-candidate tushare daily_basic.  primary_transport
  # is the P1 probe winner (docs/operations/<probe-date>-endpoint-probe-evidence.md).
  - table: basic_factor
    tier: research_only
    primary_transport: tushare:relay
    anchors: []
    conflict: block
    pit: null
    coverage_shape: per_symbol_window
    incremental: last_covered_plus_1
  - table: basic_factor_coverage
    tier: core
    primary_transport: tushare:relay
    anchors: []
    conflict: block
    pit: null
    coverage_shape: none
    incremental: last_covered_plus_1
```

Run: 同上 → Expected: PASS

- [ ] **Step 3: 写禁用态装配的失败测试**（追加到 `test_pipeline_fetch_coverage.py`；`_DECLARED_TABLES` 扩为含两新表；import 补 `from stock_quant.data_model.dataset import DatasetReader`）

```python
def test_unwired_basic_factor_publishes_disabled_not_fetched(tmp_path):
    """A registered-but-unwired table publishes empty canonical frames with
    whole-window source_disabled segments (§7.5.2/§11), never a fabricated
    fetched segment."""
    project = build_fixture_project(tmp_path / "project")
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=BARS_START, end_date=_GEN1_END)
    )
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as ctx:
        assert ctx.read("basic_factor").empty
        assert ctx.read("basic_factor_coverage").empty
    build = _manifest_build(project.root, result.dataset_ref.version)
    for table in ("basic_factor", "basic_factor_coverage"):
        segments = build["table_fetch_coverage"][table]
        assert [s["kind"] for s in segments] == ["not_fetched"]
        assert segments[0]["reason"] == "source_disabled"
        assert segments[0]["window_end"] == _GEN1_END.isoformat()
```

- [ ] **Step 4: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_pipeline_fetch_coverage.py -q -k basic_factor`
Expected: FAIL — FATAL `missing_registered_table: basic_factor`（P2a 发布期门禁拦截）。

- [ ] **Step 5: 最小实现（禁用态装配）**

`_read_baseline` 的 `with reader.open(...)` 块内按 quarantine 先例追加（返回元组与调用点同步扩两元）：

```python
            basic_factor = (
                context.read("basic_factor")
                if "basic_factor" in context.tables
                else pd.DataFrame(columns=BASIC_FACTOR_COLUMNS)
            )
            basic_factor_coverage = (
                context.read("basic_factor_coverage")
                if "basic_factor_coverage" in context.tables
                else pd.DataFrame(columns=CORPORATE_ACTION_COVERAGE_COLUMNS)
            )
```

tables 装配（`:1298` 前）加 `tables["basic_factor"] = basic_factor[BASIC_FACTOR_COLUMNS]` 与 `tables["basic_factor_coverage"] = basic_factor_coverage`；段装配循环后（`:1382` 后）加 override（import 补 `BASIC_FACTOR_COLUMNS`、`NOT_FETCHED_SOURCE_DISABLED`）：

```python
        # fetch lane lands in Task 2; until then the honest record for a
        # registered-but-unwired table is source_disabled (§7.5.2), never a
        # fabricated fetched segment.
        for table in ("basic_factor", "basic_factor_coverage"):
            fetch_segments.pop(table, None)
            fetch_segments[table] = _disabled_or_carried_segments(
                table, recorded_spans.get(table), acceptance_anchor, end
            )
```

```python
def _disabled_or_carried_segments(
    table: str, covered: tuple[date, date] | None, anchor: date, end: date,
) -> list[FetchSegment]:
    """Carry a not-enabled table's baseline span; mark the rest disabled."""
    if covered is None:
        return [FetchSegment(table, KIND_NOT_FETCHED, anchor, end,
                             reason=NOT_FETCHED_SOURCE_DISABLED)]
    segments: list[FetchSegment] = []
    low, high = max(covered[0], anchor), min(covered[1], end)
    if low <= high:
        segments.append(FetchSegment(table, KIND_CARRIED, low, high))
    if covered[1] < end:
        segments.append(FetchSegment(
            table, KIND_NOT_FETCHED, covered[1] + timedelta(days=1), end,
            reason=NOT_FETCHED_SOURCE_DISABLED))
    return segments
```

- [ ] **Step 6: 跑测试确认通过 + 邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_pipeline_fetch_coverage.py tests/unit/test_data_contracts.py tests/integration/test_dataset_publish.py -q` → Expected: PASS（`_DECLARED_TABLES` 扩表后既有每表必记录断言一并覆盖两新表）。

- [ ] **Step 7: 提交**

```bash
git add src/stock_quant/data_model/schemas.py src/stock_quant/data_model/dataset.py src/stock_quant/data_pipeline.py project/configs/sources.yml templates/project-config/sources.yml tests/unit/test_data_contracts.py tests/integration/test_pipeline_fetch_coverage.py
git commit -m "feat(basic-factor): register both tables with contracts and disabled-state assembly"
```

---

### Task 2: `daily_basic` → `basic_factor` normalize 与 coverage 行

**Files:** Create `src/stock_quant/data_model/basic_factor.py`；Modify `src/stock_quant/data_pipeline.py`（fetch 通道接线 + 段装配换实fetch）；Test `tests/unit/test_basic_factor.py`（新）、`tests/integration/test_pipeline_fetch_coverage.py`。

**Interfaces:** Consumes Task 1 注册与契约、P1 `daily_basic` 端点（stub 帧列 `ts_code/trade_date/total_mv/turnover_rate`）。Produces `MARKET_CAP_UNIT_FACTOR`、`TURNOVER_RATE_UNIT_DIVISOR`、`BASIC_FACTOR_SOURCE`、`normalize_basic_factor(raw, *, ingested_at)`、`build_basic_factor_coverage(*, window_start, window_end, factor, daily_bar, master, failed_days=(), request_days_missing=False, checked_at=None)`、`untrusted_coverage_rows(coverage)`；管线 `_fetch_basic_factor_window(start, end) -> (frame, meta)`、`_basic_factor_fetch_segments(...)`、`_merge_basic_factor(baseline, fetched)`。

- [ ] **Step 1: 冻结单位常量（探针前置，不许猜）**

读 `docs/operations/<probe-date>-endpoint-probe-evidence.md` 单位节与数量级素材，把实测值抄进模块常量；探针若与 ×10000/÷100 不符，先回写 spec §7.2 再写码。

- [ ] **Step 2: 写失败测试**（`tests/unit/test_basic_factor.py`）

```python
"""basic_factor normalization, coverage rows and the trust filter (§7.2/7.3)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from stock_quant.data_model.basic_factor import (
    MARKET_CAP_UNIT_FACTOR,
    TURNOVER_RATE_UNIT_DIVISOR,
    build_basic_factor_coverage,
    normalize_basic_factor,
    untrusted_coverage_rows,
)
from stock_quant.data_model.universe_membership import SecurityMasterBoundary

INGESTED = pd.Timestamp("2026-10-01T08:00:00Z")


def _raw(**overrides):
    row = {
        "ts_code": "600000.SH", "trade_date": "20260930",
        "total_mv": 15000_0000.0, "turnover_rate": 1.25,
        # OHLCV/amount 不该被复制：
        "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
        "amount": 999.0, "vol": 100,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_units_come_from_the_probe_and_nulls_stay_null():
    frame = normalize_basic_factor(_raw(), ingested_at=INGESTED)
    assert frame["market_cap"].tolist() == [15000_0000.0 * MARKET_CAP_UNIT_FACTOR]
    assert frame["turnover_rate"].tolist() == [1.25 / TURNOVER_RATE_UNIT_DIVISOR]
    assert frame.columns.tolist() == [
        "trade_date", "symbol", "market_cap", "turnover_rate",
        "source", "ingested_at",
    ]
    assert frame["source"].tolist() == ["tushare"]
    gap = normalize_basic_factor(
        _raw(total_mv=float("nan"), turnover_rate=None), ingested_at=INGESTED
    )
    assert gap["market_cap"].isna().all() and gap["turnover_rate"].isna().all()


def test_missing_factor_row_lands_untrusted_facts_incomplete():
    bar = pd.DataFrame(
        {"trade_date": [date(2026, 9, 29), date(2026, 9, 30)],
         "symbol": ["600000.SH", "600000.SH"]}
    )
    factor = normalize_basic_factor(
        _raw(trade_date="20260929"), ingested_at=INGESTED
    )
    coverage = build_basic_factor_coverage(
        window_start=date(2026, 9, 29), window_end=date(2026, 9, 30),
        factor=factor, daily_bar=bar, master={}, checked_at=INGESTED,
    )
    assert coverage.iloc[0]["status"] == "UNTRUSTED"
    assert coverage.iloc[0]["reason"] == "FACTS_INCOMPLETE"
    assert len(untrusted_coverage_rows(coverage)) == 1


def test_verified_empty_requires_master_evidence_and_filter_is_equality():
    empty = pd.DataFrame(columns=["trade_date", "symbol"])
    master = {"600000.SH": SecurityMasterBoundary(
        "600000.SH", list_date=date(2026, 10, 1)
    )}
    coverage = build_basic_factor_coverage(
        window_start=date(2026, 9, 29), window_end=date(2026, 9, 30),
        factor=empty, daily_bar=empty, master=master, checked_at=INGESTED,
    )
    assert coverage.iloc[0]["status"] == "VERIFIED_EMPTY"
    assert untrusted_coverage_rows(coverage).empty
    mixed = pd.DataFrame({"status": ["UNTRUSTED", "VERIFIED", "VERIFIED_EMPTY"]})
    # 只许 == "UNTRUSTED"；!= "VERIFIED" 会把 VERIFIED_EMPTY 误判（§7.3）
    assert untrusted_coverage_rows(mixed)["status"].tolist() == ["UNTRUSTED"]
```

- [ ] **Step 3: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_basic_factor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stock_quant.data_model.basic_factor'`

- [ ] **Step 4: 实现 `data_model/basic_factor.py`**

```python
"""daily_basic -> basic_factor normalization and coverage evidence (§7.2/7.3).

Pure functions only (§6.3): no I/O, no wall-clock reads.  Unit constants are
frozen from the P1 probe evidence and cite it; the probe is the only
authority (§6.4).  Coverage vocabulary is reused verbatim from
corporate_action_coverage -- no new status codes (§7.3).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from stock_quant.data_model.clean import parse_trade_date
from stock_quant.data_model.corporate_action_coverage import (
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_model.schemas import BASIC_FACTOR_COLUMNS
from stock_quant.data_model.symbols import (
    SymbolNormalizationError,
    normalize_symbol,
)
from stock_quant.data_model.universe_membership import SecurityMasterBoundary

#: FROZEN FROM PROBE EVIDENCE (spec §6.4):
#: docs/operations/<probe-date>-endpoint-probe-evidence.md -- daily_basic
#: returns total_mv in wan-yuan and turnover_rate in percent.  The probe
#: wins over any naming-based guess; do not edit without new dated evidence.
MARKET_CAP_UNIT_FACTOR = 10_000.0
TURNOVER_RATE_UNIT_DIVISOR = 100.0

BASIC_FACTOR_SOURCE = "tushare"
JOIN_KEY = ("trade_date", "symbol")


def normalize_basic_factor(
    raw: pd.DataFrame, *, ingested_at: pd.Timestamp
) -> pd.DataFrame:
    """One daily_basic response frame -> canonical basic_factor rows.

    Only the four evidence columns are read (OHLCV/amount never enter);
    missing stays NaN (null at publish) -- fill-zero is forbidden (§7.2).
    Unparseable rows drop here and resurface as FACTS_INCOMPLETE via the
    coverage builder's key-set comparison.
    """
    records: list[dict[str, Any]] = []
    for row in raw.to_dict("records"):
        trade_date = parse_trade_date(row.get("trade_date"))
        try:
            symbol = normalize_symbol(row.get("ts_code"), "tushare")
        except SymbolNormalizationError:
            symbol = None
        if trade_date is None or symbol is None:
            continue
        records.append({
            "trade_date": trade_date,
            "symbol": symbol,
            "market_cap": _scaled(row.get("total_mv"), MARKET_CAP_UNIT_FACTOR),
            "turnover_rate": _scaled(
                row.get("turnover_rate"), 1.0 / TURNOVER_RATE_UNIT_DIVISOR),
            "source": BASIC_FACTOR_SOURCE,
            "ingested_at": ingested_at,
        })
    frame = pd.DataFrame(records, columns=BASIC_FACTOR_COLUMNS)
    return frame.sort_values(list(JOIN_KEY), kind="stable").reset_index(drop=True)


def build_basic_factor_coverage(
    *,
    window_start: date,
    window_end: date,
    factor: pd.DataFrame,
    daily_bar: pd.DataFrame,
    master: Mapping[str, SecurityMasterBoundary],
    failed_days: Sequence[date] = (),
    request_days_missing: bool = False,
    checked_at: object = None,
) -> pd.DataFrame:
    """Per symbol x window coverage rows (§7.3 decision table).

    ``daily_bar`` is the observable expected set (bar-present days);
    ``master`` is the ONLY evidence that may justify VERIFIED_EMPTY
    (not-listed / delisted), matching date_window_completeness practice.
    """
    bar = daily_bar[
        (daily_bar["trade_date"] >= pd.Timestamp(window_start))
        & (daily_bar["trade_date"] <= pd.Timestamp(window_end))
    ]
    expected = _days_by_symbol(bar)
    factor_days = _days_by_symbol(factor)
    rows = []
    for symbol in sorted(set(expected) | set(factor_days) | set(master)):
        status, reason = _coverage_status(
            expected=expected.get(symbol, set()),
            present=factor_days.get(symbol, set()),
            boundary=master.get(symbol),
            window_start=window_start,
            window_end=window_end,
            failed=bool(failed_days),
            request_days_missing=request_days_missing,
        )
        rows.append(coverage_record(
            symbol, window_start, window_end, status, reason,
            sources=[{"endpoint": "daily_basic",
                      "outcome": "success_with_events"}],
            checked_at=checked_at,
        ))
    return coverage_frame(rows)


def _days_by_symbol(frame: pd.DataFrame) -> dict[str, set[date]]:
    days: dict[str, set[date]] = {}
    for record in frame.to_dict("records"):
        day = pd.Timestamp(record["trade_date"]).date()
        days.setdefault(str(record["symbol"]), set()).add(day)
    return days


def _coverage_status(
    *, expected, present, boundary, window_start, window_end,
    failed, request_days_missing,
):
    if failed:
        return CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_FETCH_FAILED
    if request_days_missing:
        return CoverageStatus.UNTRUSTED, CoverageReason.COVERAGE_INCOMPLETE
    if expected - present:
        return CoverageStatus.UNTRUSTED, CoverageReason.FACTS_INCOMPLETE
    if not expected and boundary is not None and (
        boundary.list_date is not None and boundary.list_date > window_end
        or boundary.last_tradable_date is not None
        and boundary.last_tradable_date < window_start
    ):
        return CoverageStatus.VERIFIED_EMPTY, None
    return CoverageStatus.VERIFIED, None


def untrusted_coverage_rows(coverage: pd.DataFrame) -> pd.DataFrame:
    """The ONLY legal downstream trust filter (§7.3): equality against
    ``UNTRUSTED`` -- never ``!= VERIFIED``, which mis-scores VERIFIED_EMPTY
    ("confirmed no facts") as a failure."""
    return coverage[coverage["status"] == CoverageStatus.UNTRUSTED.value]


def _scaled(value: Any, factor: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if pd.isna(number):
        return float("nan")
    return number * factor
```

- [ ] **Step 5: 跑测试确认通过**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_basic_factor.py -q`
Expected: PASS（单位断言过 = 常量与探针一致；不一致先回 Step 1）。

- [ ] **Step 6: 管线接线（先读再接）**

定位：`grep -n "raw_daily_frames\|_materialize_suspensions" src/stock_quant/data_pipeline.py | head`；在 `_execute_update` 内 `new_daily` 终帧确定后（约 `:1195` 后）加 fetch 与装配，替换 Task 1 的禁用态 override：

```python
        basic_factor_raw, basic_factor_meta = self._fetch_basic_factor_window(
            plans["basic_factor"].window_start, end
        )
        fetched_factor = normalize_basic_factor(
            basic_factor_raw, ingested_at=_ingest_time(basic_factor_meta)
        )
        new_basic_factor = _merge_basic_factor(baseline_basic_factor,
                                               fetched_factor)
        # 空结果（源禁用/不可用）保持 Task 1 禁用态路径，不重建 coverage。
        basic_factor_coverage = build_basic_factor_coverage(
            window_start=new_basic_factor["trade_date"].min().date(),
            window_end=end,
            factor=new_basic_factor,
            daily_bar=new_daily,
            master={
                # delist_date 是终止日，不是已证最后可交易日
                # （SecurityMasterBoundary 契约），只能传 None。
                record["symbol"]: SecurityMasterBoundary(
                    record["symbol"], record.get("list_date"), None
                )
                for record in master.to_dict("records")
            },
            failed_days=basic_factor_meta.get("failed_days", ()),
            request_days_missing=bool(basic_factor_meta.get("truncated")),
            checked_at=_ingest_time(basic_factor_meta),
        )
```

段装配 override 换成 `_basic_factor_fetch_segments(...)`：以 `recorded_spans.get("basic_factor")` 为 carried 前段；首拉时 `supported_start = new_basic_factor["trade_date"].min().date()`，`supported_start > acceptance_anchor` 时写**唯一前缀** `FetchSegment(表, KIND_NOT_FETCHED, anchor, supported_start-1天, reason=NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR)`，随后 `KIND_FETCHED [supported_start, end]`——这是 P2a 预留的 history 前缀**写入方**；`basic_factor_coverage` 段与事实表同窗。源整体失败回落 `_disabled_or_carried_segments`。`_fetch_basic_factor_window` 走 P1 端点（stub 由 StubAdapter 以 `daily_basic` endpoint 应答，按日/按区间分页），失败分类进 `meta`。

- [ ] **Step 7: 集成失败测试→通过**（`test_pipeline_fetch_coverage.py` 追加）

用例一：stub 完整应答 `daily_basic` → manifest 两表非空、`basic_factor` 段为 history 前缀（首拉起点晚于 anchor 时）+ `fetched`；用例二：stub 缺某 symbol 某日行 → `basic_factor_coverage` 出现 `UNTRUSTED/FACTS_INCOMPLETE` 行且该轮**照常发布**（UNTRUSTED 是证据不是阻断，§7.3）。两用例先在"无 fetch 接线"上确认失败（表仍空/段仍 disabled），再随 Step 6 转绿。

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_pipeline_fetch_coverage.py tests/unit/test_basic_factor.py -q` → Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add src/stock_quant/data_model/basic_factor.py src/stock_quant/data_pipeline.py tests/unit/test_basic_factor.py tests/integration/test_pipeline_fetch_coverage.py
git commit -m "feat(basic-factor): normalize daily_basic into facts and per-symbol coverage rows"
```

---

### Task 3: `daily_bar` × `basic_factor` 联接一致性

**Files:** Modify `src/stock_quant/data_model/basic_factor.py`（追加检查）、`src/stock_quant/data_quality/models.py`+`gates.py`（三码登记）、`src/stock_quant/data_pipeline.py`（publish 前接线）；Test `tests/unit/test_basic_factor.py`、`tests/unit/test_quality_checks.py`。

**Interfaces:** Produces 码 `basic_factor_join_duplicate`/`basic_factor_join_expansion`/`basic_factor_coverage_row_missing`（FATAL + GLOBAL_PROCESS——跨表一致性，tier 不得降级，§7.4"让该轮失败"）；`basic_factor_join_issues(daily_bar, basic_factor, coverage) -> list[QualityIssue]`。

- [ ] **Step 1: 写失败测试**（追加到 `tests/unit/test_basic_factor.py`；import 补 `basic_factor_join_issues`）

```python
def _bar():
    return pd.DataFrame(
        {"trade_date": [date(2026, 9, 29), date(2026, 9, 30)],
         "symbol": ["600000.SH", "600000.SH"]}
    )


def _factor():
    return pd.DataFrame({"trade_date": [date(2026, 9, 29)],
                         "symbol": ["600000.SH"]})


def _covering_coverage(symbol="600000.SH"):
    return pd.DataFrame(
        [{"symbol": symbol, "window_start": date(2026, 9, 1),
          "window_end": date(2026, 9, 30), "status": "UNTRUSTED"}]
    )


def test_duplicate_key_and_join_expansion_fail_the_round():
    dup_bar = pd.DataFrame(
        {"trade_date": [date(2026, 9, 29), date(2026, 9, 29)],
         "symbol": ["600000.SH", "600000.SH"]}
    )
    issues = basic_factor_join_issues(dup_bar, _factor(), _covering_coverage())
    codes = [i.code for i in issues]
    assert "basic_factor_join_duplicate" in codes
    # 2 x 1 同键内联接得 2 行 > len(factor) == 1：扩行可见
    assert "basic_factor_join_expansion" in codes


def test_row_set_diff_without_a_coverage_row_fails():
    issues = basic_factor_join_issues(
        _bar(), _factor(), _covering_coverage(symbol="000001.SZ"))
    missing = [i for i in issues
               if i.code == "basic_factor_coverage_row_missing"]
    assert missing and missing[0].symbol == "600000.SH"
    assert missing[0].trade_date == date(2026, 9, 30)


def test_covered_diff_and_clean_join_produce_no_issue():
    assert basic_factor_join_issues(
        _bar(), _factor(), _covering_coverage()) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_basic_factor.py -q -k "duplicate or diff or clean"`
Expected: FAIL — `ImportError: cannot import name 'basic_factor_join_issues'`

- [ ] **Step 3: 实现检查函数**（`basic_factor.py` 末尾追加）

```python
CODE_BASIC_FACTOR_JOIN_DUPLICATE = "basic_factor_join_duplicate"
CODE_BASIC_FACTOR_JOIN_EXPANSION = "basic_factor_join_expansion"
CODE_BASIC_FACTOR_COVERAGE_ROW_MISSING = "basic_factor_coverage_row_missing"


def basic_factor_join_issues(
    daily_bar: pd.DataFrame,
    basic_factor: pd.DataFrame,
    coverage: pd.DataFrame,
) -> list:
    """One-to-one (trade_date, symbol) between daily_bar and basic_factor.

    §7.2: duplicated keys on either side, a merge that expands rows, or a
    daily-bar row with no basic_factor row and no covering UNTRUSTED
    coverage row are quality errors -- never silently absorbed by a left
    join.  All three are cross-table invariants: FATAL global-process codes
    no tier may waive.

    The row-set difference is judged on the ``daily_bar`` side only:
    basic_factor days without a daily_bar row are the coverage decision
    table's own domain (factor support legitimately precedes the bar
    baseline's first session and a VERIFIED row blesses it), while a bar
    day lacking its factor row is exactly the silent loss the left join
    would otherwise absorb.
    """
    from stock_quant.data_quality.models import QualityIssue, Severity

    issues: list[QualityIssue] = []
    key = list(JOIN_KEY)
    for side, frame in (("daily_bar", daily_bar),
                        ("basic_factor", basic_factor)):
        count = int(frame.duplicated(subset=key).sum())
        if count:
            issues.append(QualityIssue(
                severity=Severity.FATAL,
                code=CODE_BASIC_FACTOR_JOIN_DUPLICATE,
                table="basic_factor",
                details={"side": side, "duplicate_rows": count},
            ))
    merged = daily_bar.merge(basic_factor, on=key, how="inner")
    if len(merged) > len(daily_bar) or len(merged) > len(basic_factor):
        issues.append(QualityIssue(
            severity=Severity.FATAL,
            code=CODE_BASIC_FACTOR_JOIN_EXPANSION,
            table="basic_factor",
            details={"merged_rows": int(len(merged)),
                     "daily_bar_rows": int(len(daily_bar)),
                     "basic_factor_rows": int(len(basic_factor))},
        ))
    untrusted = untrusted_coverage_rows(coverage)
    windows = {
        (str(r["symbol"]), pd.Timestamp(r["window_start"]).date(),
         pd.Timestamp(r["window_end"]).date())
        for r in untrusted.to_dict("records")
    }
    for symbol, day in sorted(_key_set(daily_bar) - _key_set(basic_factor)):
        if not any(s == symbol and lo <= day <= hi for s, lo, hi in windows):
            issues.append(QualityIssue(
                severity=Severity.FATAL,
                code=CODE_BASIC_FACTOR_COVERAGE_ROW_MISSING,
                table="basic_factor",
                symbol=symbol,
                trade_date=day,
                details={"side": "daily_bar_only"},
            ))
    return issues


def _key_set(frame: pd.DataFrame) -> set:
    return {
        (str(r["symbol"]), pd.Timestamp(r["trade_date"]).date())
        for r in frame.to_dict("records")
    }
```

`data_quality/models.py` 照 `CODE_UNREGISTERED_TABLE` 先例登记三码，`gates.py` 把三码同时加进 `PUBLICATION_BLOCKING_CODES` 与 `GLOBAL_PROCESS_CODES`（`:42`/`:68`）；`tests/unit/test_quality_checks.py` 若有码集字面断言，按失败信息补期望值，不改成子集断言。

- [ ] **Step 4: 跑测试确认通过 + 管线接线**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_basic_factor.py tests/unit/test_quality_checks.py -q` → Expected: PASS

`data_pipeline.py`：两帧终定后、`report = QualityReport(...)`（`:1384`）前加 `issues.extend(basic_factor_join_issues(new_daily, new_basic_factor, basic_factor_coverage))`；在 `test_pipeline_fetch_coverage.py` 临时注入重复 stub 行断言该轮失败、CURRENT 不动、质量报告含 `basic_factor_join_duplicate`（正式集成形态在 Task 5 的 `test_basic_factor_publish.py` 固化）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_model/basic_factor.py src/stock_quant/data_pipeline.py src/stock_quant/data_quality/models.py src/stock_quant/data_quality/gates.py tests/unit/test_basic_factor.py tests/unit/test_quality_checks.py tests/integration/test_pipeline_fetch_coverage.py
git commit -m "feat(gates): enforce one-to-one daily_bar x basic_factor join with coverage evidence"
```

---

### Task 4: `table_lineage` 与发布期核对

**Files:** Modify `src/stock_quant/data_pipeline.py`（`dataset_build_config:568` 加参数与键；发布前核对）、`src/stock_quant/data_quality/models.py`+`gates.py`（`table_lineage_transport_mismatch`、`coverage_table_missing`）、`src/stock_quant/research/acceptance/models.py:78`（`AUTOMATED_CHECK_CODES` 追加）+`checks.py`（新检查函数）；Test `tests/integration/test_basic_factor_publish.py`（新建）、`tests/unit/test_acceptance_checks.py`。

**Interfaces:** Produces `dataset_build_config(..., table_lineage=None)`；build_config 键 `table_lineage` = `{table: {"table", "transport", "raw_snapshot": {source, endpoint, transport_id, request_key, file_sha256, manifest_sha256}}}`；管线帮手 `_basic_factor_table_lineage(raw_snapshots)`（过滤 `endpoint == "daily_basic"` 证据行）与 `_table_lineage_issues(lineage, contracts, tables)`；验收检查码 `"table_lineage_evidence"`（无该键旧 manifest PASS 兼容）；FATAL 码 `table_lineage_transport_mismatch`、`coverage_table_missing`；`coverage_table_of(name) = name + "_coverage"`。

- [ ] **Step 1: 写失败测试**

`tests/integration/test_basic_factor_publish.py`（新，harness 照 `test_pipeline_fetch_coverage.py`，三个用例按既有 stub 手法把构造体写全、不留省略）：① 完整窗 update → `manifest["build_config"]["table_lineage"]["basic_factor"]["transport"] == "tushare:relay"` 且 `["raw_snapshot"]["endpoint"] == "daily_basic"`；② 管线实际 transport 与契约声明不符 → 该轮失败、质量报告含 FATAL `table_lineage_transport_mismatch`、CURRENT 不动；③ `coverage_shape=per_symbol_window` 的表发布时缺 `<table>_coverage` 表 → FATAL `coverage_table_missing`。`tests/unit/test_acceptance_checks.py` 追加：带 `table_lineage` 且与声明一致 → `table_lineage_evidence` PASS；**无该键旧 manifest → PASS**（兼容读取、不升 `pipeline_contract_version`、处置=重发布）。

- [ ] **Step 2: 跑测试确认失败**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_basic_factor_publish.py tests/unit/test_acceptance_checks.py -q -k "lineage or coverage_table"`
Expected: FAIL — manifest 无 `table_lineage` 键 / 新码不存在。

- [ ] **Step 3: 实现**

`dataset_build_config` 签名加 `table_lineage: Mapping[str, object] | None = None`，`config` 构造加：

```python
    if table_lineage:
        # table -> transport -> raw snapshot evidence row (spec §7.3).  A new
        # build-config key, not a contract bump: manifests without it read
        # compatibly; their remedy is republish, never a standing exemption.
        config["table_lineage"] = dict(table_lineage)
```

`_execute_update` 发布前：`table_lineage = _basic_factor_table_lineage(raw_snapshots)`，随后 `issues.extend(_table_lineage_issues(table_lineage, self._project_config.data_contracts, tables))`。`_table_lineage_issues`：每条 lineage 行的 `transport` 必须等于对应契约 `primary_transport`，否则 FATAL `table_lineage_transport_mismatch`；每个 `coverage_shape == "per_symbol_window"` 的已发布表必须在 `tables` 中存在 `coverage_table_of(table)`，否则 FATAL `coverage_table_missing`。两码照 Task 3 先例登进 `models.py` 与 `gates.py` 两集合。`checks.py` 加 `_check_table_lineage`（读 `dataset_evidence(value).manifest`；无键 → PASS 兼容；有键按同型规则判）并注册进 `functions` 与 `AUTOMATED_CHECK_CODES`。

`coverage_downgraded` 写入点失败测试（同文件追加）：给 `basic_factor` 注入一个 TABLE_LEVEL 阻断码（如 `schema_mismatch` ERROR）→ 该轮**不阻断**、发布成功且质量报告含 `coverage_downgraded` WARNING（`_downgrade_issues` 对 research_only 的既有路径，本测试锁它对 basic_factor 真正生效）。

- [ ] **Step 4: 跑测试确认通过 + 邻居**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/integration/test_basic_factor_publish.py tests/unit/test_acceptance_checks.py tests/integration/test_tiered_publication.py tests/unit/test_tiered_publication_gate.py -q` → Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/data_pipeline.py src/stock_quant/data_quality/models.py src/stock_quant/data_quality/gates.py src/stock_quant/research/acceptance/models.py src/stock_quant/research/acceptance/checks.py tests/integration/test_basic_factor_publish.py tests/unit/test_acceptance_checks.py
git commit -m "feat(lineage): record table_lineage evidence and verify it against declared transports"
```

---

### Task 5: 端到端完成条件（§7.4 子集）与预热窗口并入

**Files:** Modify `src/stock_quant/research/runner.py`（`_preflight_table_tiers:1153` 判定窗口并入 warmup 起点）；Test `tests/integration/test_table_tier_preflight.py`、`tests/integration/test_basic_factor_publish.py`。

**Interfaces:** Produces preflight 调 `table_unsupported_window_tables` 的窗口起点改为 `min(spec.date_range.start_date, earliest_warmup_start)`；`_earliest_warmup_start(frozen)` 用 `_materialize_walk_forward_schedule`（`runner.py:1753`）取各折 `warmup_calendar_start` 最小值，材料化失败（ValueError/OSError）返回 `None` 并维持 date_range 判定——"无完整 12 个月 OOS 折"的失败归属仍留在 pipeline 阶段（ADR-022 后果节的成文义务）。

- [ ] **Step 1: 离线端到端用例**（`test_basic_factor_publish.py`，照既有手法写全）

① stub raw → update → 新 version → 子进程 `python -m stock_quant data validate --root <project>` 退出 0（手法照 `test_acceptance_cli.py`）；② 相同输入重跑 → `dataset_version` 哈希不变且旧版本目录字节不动（照 `test_dataset_publish.py` 幂等/不可变断言）；③ stub 改一个 `total_mv` 事实 → 新版本且旧版本字节不动；④ 缺行注入 → coverage 含 `UNTRUSTED/FACTS_INCOMPLETE` 行、发布照常、且 RESEARCH 预检引用该表（或 not_fetched 段版本）被拒（`table_not_fetched`/`table_history_start_after_window`），ENGINEERING 豁免并带 RESEARCH-ONLY 标签；⑤ 全窗 null `total_mv` → Parquet 值为 null 而非 0（无 fill-zero、无可信空值空洞）。

- [ ] **Step 2: 预热窗口并入的失败测试**

`tests/integration/test_table_tier_preflight.py` 追加：spec 的 `date_range` 完全落在 basic_factor 支持段、但其折的 `warmup_calendar_start` 落进 `history_begins_after_anchor` 前缀 → RESEARCH run 以 `table_history_start_after_window` 失败。先跑确认失败（现状只判 `date_range`，该 run 被放行）。

- [ ] **Step 3: 实现 runner 接线**

`_preflight_table_tiers` 内，调 `table_unsupported_window_tables` 前加（该调用 `window_start` 实参换成下面的值，`window_end` 不变；preflight summary 的 `"check_window"` 记录实际判定窗口）：

```python
        window_start = spec.date_range.start_date
        warmup_start = self._earliest_warmup_start(frozen)
        if warmup_start is not None:
            window_start = min(window_start, warmup_start)
```

- [ ] **Step 4: 全量相关套件**

Run: `/home/ji/miniconda3/envs/sq312/bin/python -m pytest tests/unit/test_basic_factor.py tests/unit/test_data_contracts.py tests/unit/test_quality_checks.py tests/unit/test_acceptance_checks.py tests/integration/test_basic_factor_publish.py tests/integration/test_pipeline_fetch_coverage.py tests/integration/test_dataset_publish.py tests/integration/test_table_tier_preflight.py tests/integration/test_window_anchor_regression.py -q` → Expected: PASS

- [ ] **Step 5: 真实小窗口（owner 授权后执行，单列一步）**

授权范围内：`data update --root <project> --start <supported_start> --end <小窗末>` → 新 version；`data validate` 通过；重跑同窗哈希不变。命令、version 哈希与 validate 摘要记入 dated evidence（`docs/operations/2026-10-<date>-basic-factor-first-real-window.md`，新文件）。未授权时本步停在"向 owner 申请"并汇报。

- [ ] **Step 6: §7.4 勾稽 + 提交**

| §7.4 条目（本计划子集） | 交付 |
| --- | --- |
| 真实小窗口 raw→新 version→validate 通过 | Task 5 Step 5（需授权）；离线形态 Step 1① |
| 相同输入重跑哈希不变；单源事实变化出新版本且旧版本不动 | Task 5 Step 1②③ |
| 缺失保 null；应有而无落 UNTRUSTED(FACTS_INCOMPLETE) 且正式研究 fail closed；无 fill-zero、无可信空值空洞 | Task 2 Step 2 + Task 5 Step 1④⑤ |
| 单位换算有探针记录支撑 | Task 2 Step 1 |
| 联接一对一失败测试（重复/扩行/差异未落 coverage） | Task 3 |
| 覆盖扩展失败测试（前缀发布+validate、早于起点 RESEARCH 拒、支持窗口放行） | 写入方 Task 2 Step 6、窗口判定 Task 5 Step 2（含 warmup）；判定器本身 P2a 已交付 |
| 注册新表后旧版本复审不缺表、旧 manifest 兼容读取 | P2a 已交付；Task 4 Step 1 锁 lineage 键兼容 |

```bash
git add src/stock_quant/research/runner.py tests/integration/test_table_tier_preflight.py tests/integration/test_basic_factor_publish.py
git commit -m "feat(preflight): fold walk-forward warmup starts into unsupported-window judgment"
```

---

## Self-Review 记录

- **§7.2 勾稽**：PK `(trade_date,symbol)`、六列类型逐字对应（Task 1 Step 1）；`ingested_at` 不进身份语义以"_ingest_time 确定值 + carried 行保留"实现（须知 4）；null 禁 0 与不复制 OHLCV/amount（Task 2 测试）；联接一对一（Task 3）；单位换算以探针为唯一效力来源（Global Constraints + Task 2 Step 1）；十列宽表不做（Global Constraints）。
- **§7.3 勾稽**：注册+契约（Task 1）；tier/transport/incremental/conflict/coverage_shape（Task 1 Step 2）；coverage 表 core+none+词汇复用（Task 1/2）；落码表六行全数实现于 `_coverage_status`（VERIFIED、FACTS_INCOMPLETE、COVERAGE_INCOMPLETE、SOURCE_FETCH_FAILED；SOURCE_CONFLICT 词汇已有、无 anchor 故无写入路径=预留；VERIFIED_EMPTY 须 master 上市/退市证据）；`status == "UNTRUSTED"` 过滤规则（`untrusted_coverage_rows`）；`table_lineage` 发布核对、coverage_shape 要求 coverage 表、`coverage_downgraded` 写入点失败测试、旧 manifest 兼容、不升版（Task 4）。
- **§7.4 勾稽**：见 Task 5 Step 6 表；membership slice-hash、gap 复现、refresh 重钉/崩溃注入归 P2b，本计划未声称。
- **类型一致性**：`BASIC_FACTOR_COLUMNS`/`BASIC_FACTOR_SCHEMA`/`normalize_basic_factor`/`build_basic_factor_coverage`/`untrusted_coverage_rows`/`basic_factor_join_issues`/三联接码/`table_lineage_transport_mismatch`/`coverage_table_missing`/`table_lineage_evidence`/`_disabled_or_carried_segments`/`_basic_factor_fetch_segments`/`_merge_basic_factor`/`_earliest_warmup_start` 各任务引用一致；P2a 依赖名（`NOT_FETCHED_SOURCE_DISABLED`/`NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR`/`missing_registered_table`）按 P2a 计划原文引用。
- **留白（有意的，非占位符）**：① Task 2 Step 6 与 Task 4 Step 3 的管线接线按"先读再接"给出定位命令与行为规格（行号以实读为准）；② 探针值与 `<probe-date>` 执行时以证据文档实值替换（同范例计划的 `<date>` 惯例）；③ `UNTRUSTED` 行的首个研究消费方不在本计划——P2c 交付规则与 fail-closed 预检证据，消费方落地时必须引用 `untrusted_coverage_rows`；④ `SOURCE_CONFLICT` 预留无写入路径（本期无 anchor）。
- **复核记录（2026-09-30 实查）**：`fetch_coverage.py`/`checks.py`/发布门禁仍是 P2a 前形态（须知 1 把"P2a 先行"写成硬前置）；`test_dataset_publish.py` 空 quarantine 发布先例证实空 canonical frame 可过 `_frame_to_arrow`；初稿 Task 3 的扩行断言曾含无效表达式、Task 2 管线片段曾用未定义的 `plan_start` 与越权的 `delist_date→last_tradable_date` 映射，三处已在本稿修正。
- **修订记录（2026-10-02，owner 追认）**：
  - **Task 3 键差判定收窄（裁决 1，owner 追认）**：本计划 Task 3 伪码原写"任一侧"行集差（`daily_bar_only`/`basic_factor_only` 双循环）。实测对 VERIFIED 祝福的历史前缀行产生 **20384 个误报**（outage 轮 `basic_factor_coverage_row_missing`）：basic_factor 因子支持日合法地先于 bar 基线首个交易日，且 coverage 构建器以 VERIFIED 行祝福了它，按任一侧判差即全部误杀。Task 3 实现（commit a5f058a90）收窄为**bar 侧判行集差**（丢弃 `basic_factor_only` 循环），重复键与扩行仍**双侧**判定；误报行的覆盖豁免域 = coverage 构建器自己的域。上文 Step 3 伪码已同步为 bar 侧判差——**勿按旧伪码把实现改回任一侧**。
  - **无锚回退口径（裁决 3b，owner 追认+登记）**：无 `configs/universes` 工程 `acceptance_anchor=None` 时，整窗 disabled/not_fetched 段的窗口起点用请求窗起点回退（commit ec1ba28db，套用既有 `_unavailable_outage_segments` 同型回退模式）；有锚工程的行为逐字节不变。这是口径裁定，不是 bug 修复；回归钉在 `test_no_anchor_whole_window_disabled_record_falls_back_to_window_start` / `test_anchored_whole_window_disabled_record_keeps_the_anchor`。
  - **待办登记（裁决 3a，owner 追认）**：单位常量单一来源化——`data_model/basic_factor.py`（`MARKET_CAP_UNIT_FACTOR`/`TURNOVER_RATE_UNIT_DIVISOR`）与 P1 `data_model/basic_factor_normalize.py`（`TOTAL_MV_TO_YUAN_MULTIPLIER`/`TURNOVER_RATE_TO_RATIO_DIVISOR`）目前并存，同引 `docs/operations/2026-10-01-endpoint-probe-evidence.md`。后续合并为单一来源，消除双份漂移风险。
