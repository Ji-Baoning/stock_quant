# Point-in-Time Total Return Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an auditable point-in-time total-return series from unadjusted closes and verified corporate actions, then make Momentum60 v2 consume it without an unadjusted fallback.

**Architecture:** Add a pure `adjusted_bar` builder and canonical Arrow contracts, feed it accepted and quarantined corporate actions during every dataset update, and persist the result in the immutable dataset. The research adapter reads only this table for factor prices while execution and valuation remain on `daily_bar`; experiment metadata and HTML report the adjustment basis and break counts.

**Tech Stack:** Python 3.10, pandas 2+, PyArrow 14+, DuckDB, Pydantic 2, pytest, Jinja2, Ruff

**Spec:** `docs/superpowers/specs/2026-09-08-point-in-time-total-return-design.md`

## Global Constraints

- Preserve `daily_bar` as unadjusted tradable OHLC; never use `adjusted_bar` for orders, limits, fills, or account valuation.
- Use adjustment identifier `internal_total_return_v1` exactly.
- A future corporate action must not alter any `adjusted_bar` row before its `ex_date`.
- Accepted events require `status=implemented`, complete facts, supported action types, and `announcement_date <= ex_date`.
- An uncertain corporate-action transition creates an ERROR break; no Research path may fall back to `daily_bar.close`.
- Upgrade `momentum_60d` from `1.0.0` to `2.0.0`; old immutable datasets and experiments remain readable.
- Keep outputs deterministically sorted and JSON fields deterministically encoded so identical inputs publish byte-identical artifacts.
- Execute this plan in an isolated worktree created with `superpowers:using-git-worktrees`; the current checkout contains unrelated user changes.

---

### Task 1: Total-return domain model and exact arithmetic

**Files:**
- Create: `src/stock_quant/data_model/adjusted_bar.py`
- Modify: `src/stock_quant/data_model/schemas.py`
- Test: `tests/unit/test_adjusted_bar.py`

**Interfaces:**
- Consumes: canonical `daily_bar`, `corporate_action`, `corporate_action_quarantine`, and `corporate_action_coverage` DataFrames.
- Produces: `build_adjusted_bars(daily_bar: pd.DataFrame, corporate_actions: pd.DataFrame, quarantined_actions: pd.DataFrame, coverage: pd.DataFrame, *, symbols: Sequence[str]) -> pd.DataFrame`.
- Produces: `ADJUSTED_BAR_COLUMNS`, `ADJUSTED_BAR_SCHEMA`, `CORPORATE_ACTION_QUARANTINE_COLUMNS`, and `CORPORATE_ACTION_QUARANTINE_SCHEMA`.

- [ ] **Step 1: Write failing schema and no-event tests**

```python
from datetime import date

import pandas as pd
import pyarrow as pa

from stock_quant.data_model.adjusted_bar import (
    ADJUSTMENT_NAME,
    build_adjusted_bars,
)
from stock_quant.data_model.schemas import ADJUSTED_BAR_COLUMNS, ADJUSTED_BAR_SCHEMA


def test_adjusted_bar_schema_is_canonical():
    assert ADJUSTED_BAR_COLUMNS == [
        "trade_date", "symbol", "source", "adjustment", "raw_close",
        "adjusted_close", "adjustment_factor", "quality_severity",
        "invalid_reason", "applied_action_ids",
    ]
    assert ADJUSTED_BAR_SCHEMA.field("trade_date").type == pa.date32()


def test_no_event_series_equals_unadjusted_close(daily, empty_actions, verified_coverage):
    result = build_adjusted_bars(
        daily, empty_actions, empty_actions, verified_coverage,
        symbols=("600000.SH",),
    )
    assert result["adjustment"].unique().tolist() == [ADJUSTMENT_NAME]
    assert result["adjusted_close"].tolist() == result["raw_close"].tolist()
    assert result["quality_severity"].tolist() == ["INFO"] * len(result)
```

- [ ] **Step 2: Run the schema and no-event tests to verify they fail**

Run: `pytest tests/unit/test_adjusted_bar.py -k 'schema or no_event' -v`

Expected: FAIL because `stock_quant.data_model.adjusted_bar` and the new schema constants do not exist.

- [ ] **Step 3: Add canonical schemas and the public builder skeleton**

```python
# src/stock_quant/data_model/schemas.py
ADJUSTED_BAR_COLUMNS = [
    "trade_date", "symbol", "source", "adjustment", "raw_close",
    "adjusted_close", "adjustment_factor", "quality_severity",
    "invalid_reason", "applied_action_ids",
]

CORPORATE_ACTION_QUARANTINE_COLUMNS = [
    "symbol", "announcement_date", "record_date", "ex_date",
    "cash_dividend_per_share", "bonus_share_ratio", "capitalization_ratio",
    "rights_issue_ratio", "rights_issue_price", "status", "confirmed_by",
    "reason",
]

ADJUSTED_BAR_SCHEMA = pa.schema([
    pa.field("trade_date", pa.date32()),
    pa.field("symbol", pa.string()),
    pa.field("source", pa.string()),
    pa.field("adjustment", pa.string()),
    pa.field("raw_close", pa.float64()),
    pa.field("adjusted_close", pa.float64()),
    pa.field("adjustment_factor", pa.float64()),
    pa.field("quality_severity", pa.string()),
    pa.field("invalid_reason", pa.string()),
    pa.field("applied_action_ids", pa.string()),
])

CORPORATE_ACTION_QUARANTINE_SCHEMA = pa.schema([
    pa.field("symbol", pa.string()),
    pa.field("announcement_date", pa.date32()),
    pa.field("record_date", pa.date32()),
    pa.field("ex_date", pa.date32()),
    pa.field("cash_dividend_per_share", pa.float64()),
    pa.field("bonus_share_ratio", pa.float64()),
    pa.field("capitalization_ratio", pa.float64()),
    pa.field("rights_issue_ratio", pa.float64()),
    pa.field("rights_issue_price", pa.float64()),
    pa.field("status", pa.string()),
    pa.field("confirmed_by", pa.string()),
    pa.field("reason", pa.string()),
])
```

```python
# src/stock_quant/data_model/adjusted_bar.py
ADJUSTMENT_NAME = "internal_total_return_v1"
QUALITY_INFO = "INFO"
QUALITY_ERROR = "ERROR"


def build_adjusted_bars(
    daily_bar: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    quarantined_actions: pd.DataFrame,
    coverage: pd.DataFrame,
    *,
    symbols: Sequence[str],
) -> pd.DataFrame:
    rows = daily_bar[daily_bar["symbol"].isin(set(symbols))].copy()
    if rows.empty:
        return pd.DataFrame(columns=ADJUSTED_BAR_COLUMNS)
    return _build_all_symbols(rows, corporate_actions, quarantined_actions, coverage)
```

- [ ] **Step 4: Write failing economic-event tests**

```python
def test_cash_dividend_preserves_flat_total_return(daily_10_then_9, cash_dividend, verified_coverage):
    result = build_adjusted_bars(
        daily_10_then_9, cash_dividend, empty_quarantine(), verified_coverage,
        symbols=("600000.SH",),
    )
    assert result["adjusted_close"].tolist() == [10.0, 10.0]
    assert result.iloc[1]["applied_action_ids"] == '["600000.SH#2022-06-02"]'


def test_one_for_one_bonus_preserves_flat_total_return(daily_10_then_5, bonus_share, verified_coverage):
    result = build_adjusted_bars(
        daily_10_then_5, bonus_share, empty_quarantine(), verified_coverage,
        symbols=("600000.SH",),
    )
    assert result["adjusted_close"].tolist() == [10.0, 10.0]


def test_same_day_cash_bonus_and_capitalization_are_applied_once(
    daily, combined_action, verified_coverage
):
    result = build_adjusted_bars(
        daily, combined_action, empty_quarantine(), verified_coverage,
        symbols=("600000.SH",),
    )
    expected_multiplier = (9.0 * (1.0 + 0.05 + 0.05) + 0.10) / 10.0
    assert result.iloc[1]["adjusted_close"] == pytest.approx(10.0 * expected_multiplier)
```

- [ ] **Step 5: Implement the deterministic per-symbol recursion**

```python
def _build_symbol(bars: pd.DataFrame, actions: pd.DataFrame, breaks: dict[date, str]):
    previous_close = None
    total_return = None
    records = []
    action_by_day = _actions_by_ex_date(actions)
    for row in bars.sort_values("trade_date", kind="stable").to_dict("records"):
        day = _as_date(row["trade_date"])
        raw_close = float(row["close"])
        reason = breaks.get(day, "")
        if previous_close is None or reason:
            total_return = raw_close
        else:
            day_actions = action_by_day.get(day, ())
            cash = sum(float(item["cash_dividend_per_share"] or 0) for item in day_actions)
            share_ratio = sum(
                float(item["bonus_share_ratio"] or 0)
                + float(item["capitalization_ratio"] or 0)
                for item in day_actions
            )
            total_return *= (raw_close * (1.0 + share_ratio) + cash) / previous_close
        ids = sorted(action_id_of(item["symbol"], day) for item in action_by_day.get(day, ()))
        records.append({
            "trade_date": day,
            "symbol": str(row["symbol"]),
            "source": str(row["source"]),
            "adjustment": ADJUSTMENT_NAME,
            "raw_close": raw_close,
            "adjusted_close": float(total_return),
            "adjustment_factor": float(total_return) / raw_close,
            "quality_severity": QUALITY_ERROR if reason else QUALITY_INFO,
            "invalid_reason": reason,
            "applied_action_ids": json.dumps(ids, ensure_ascii=False, separators=(",", ":")),
        })
        previous_close = raw_close
    return records
```

- [ ] **Step 6: Write failing trust-break and no-lookahead tests**

```python
@pytest.mark.parametrize(
    "reason",
    ["unsupported_corporate_action", "cross_source_conflict", "incomplete"],
)
def test_quarantined_event_reanchors_and_marks_error(reason, daily_three_days, verified_coverage):
    quarantine = quarantined_event(ex_date=date(2022, 6, 2), reason=reason)
    result = build_adjusted_bars(
        daily_three_days, empty_actions(), quarantine, verified_coverage,
        symbols=("600000.SH",),
    )
    assert result.loc[1, "quality_severity"] == "ERROR"
    assert result.loc[1, "invalid_reason"] == reason
    assert result.loc[1, "adjusted_close"] == result.loc[1, "raw_close"]


def test_future_action_does_not_change_rows_before_ex_date(daily_90_days, verified_coverage):
    before = build_adjusted_bars(
        daily_90_days, empty_actions(), empty_quarantine(), verified_coverage,
        symbols=("600000.SH",),
    )
    after = build_adjusted_bars(
        daily_90_days, future_cash_action(day=days[70]), empty_quarantine(),
        verified_coverage, symbols=("600000.SH",),
    )
    pd.testing.assert_frame_equal(before.iloc[:70], after.iloc[:70])


def test_untrusted_coverage_marks_covered_window_as_error(daily, untrusted_coverage):
    result = build_adjusted_bars(
        daily, empty_actions(), empty_quarantine(), untrusted_coverage,
        symbols=("600000.SH",),
    )
    assert set(result["quality_severity"]) == {"ERROR"}
    assert set(result["invalid_reason"]) == {"corporate_action_coverage_untrusted"}
```

- [ ] **Step 7: Implement supported-action validation and conservative breaks**

```python
def _accepted_actions_and_breaks(
    actions, quarantined, coverage, symbol, trading_days
):
    accepted = []
    breaks: dict[date, str] = {}
    for item in _records_for(actions, symbol):
        ex_date = _as_date(item.get("ex_date"))
        announcement = _as_date(item.get("announcement_date"))
        if str(item.get("status")) != "implemented":
            breaks[ex_date] = "corporate_action_not_implemented"
        elif ex_date is None or announcement is None or announcement > ex_date:
            if ex_date is not None:
                breaks[ex_date] = "corporate_action_not_point_in_time"
        elif float(item.get("rights_issue_ratio") or 0) > 0:
            breaks[ex_date] = "unsupported_corporate_action"
        else:
            accepted.append(item)
    for item in _records_for(quarantined, symbol):
        ex_date = _as_date(item.get("ex_date"))
        if ex_date is not None:
            breaks[ex_date] = str(item.get("reason") or "corporate_action_untrusted")
    _overlay_untrusted_coverage(breaks, coverage, symbol, trading_days)
    return pd.DataFrame(accepted), breaks
```

Pass the symbol's sorted bar dates into `_accepted_actions_and_breaks`; `_overlay_untrusted_coverage` assigns `corporate_action_coverage_untrusted` to every such date inside an UNTRUSTED coverage row's inclusive `window_start..window_end`. Exact quarantine reasons take precedence over this window-level reason on their `ex_date`.

- [ ] **Step 8: Run the total-return unit suite**

Run: `pytest tests/unit/test_adjusted_bar.py -v`

Expected: PASS, including exact event arithmetic, stable JSON action IDs, point-in-time invariance, and conservative breaks.

- [ ] **Step 9: Commit the domain model**

```bash
git add src/stock_quant/data_model/adjusted_bar.py src/stock_quant/data_model/schemas.py tests/unit/test_adjusted_bar.py
git commit -m "feat: build point-in-time total return bars"
```

---

### Task 2: Immutable dataset publication contracts

**Files:**
- Modify: `src/stock_quant/data_model/dataset.py`
- Modify: `tests/integration/test_dataset_publish.py`

**Interfaces:**
- Consumes: `ADJUSTED_BAR_SCHEMA` and `CORPORATE_ACTION_QUARANTINE_SCHEMA` from Task 1.
- Produces: DuckDB-readable `adjusted_bar` and `corporate_action_quarantine` standardized tables with manifest hashes.

- [ ] **Step 1: Write failing publication and reader tests**

```python
def valid_tables(closes=None):
    daily = _daily_frame(closes or [10.5, 10.8, 11.0])
    return {
        "daily_bar": daily,
        "adjusted_bar": build_adjusted_bars(
            daily, empty_corporate_actions(), empty_quarantine(),
            verified_coverage(), symbols=("600000.SH",),
        ),
        "corporate_action_quarantine": empty_quarantine(),
    }


def test_publish_persists_adjusted_bar_and_quarantine_schemas(tmp_path):
    ref = DatasetPublisher(tmp_path).publish(valid_tables(), QualityReport())
    assert pq.ParquetFile(ref.path / "adjusted_bar.parquet").schema_arrow == ADJUSTED_BAR_SCHEMA
    assert pq.ParquetFile(
        ref.path / "corporate_action_quarantine.parquet"
    ).schema_arrow == CORPORATE_ACTION_QUARANTINE_SCHEMA
    with DatasetReader(tmp_path).open(ref.version) as context:
        assert context.read("adjusted_bar")["adjustment"].unique().tolist() == [
            "internal_total_return_v1"
        ]
```

- [ ] **Step 2: Run the publication test to verify it fails**

Run: `pytest tests/integration/test_dataset_publish.py::test_publish_persists_adjusted_bar_and_quarantine_schemas -v`

Expected: FAIL with `no canonical schema for standardized table 'adjusted_bar'`.

- [ ] **Step 3: Register both tables with the publisher**

```python
# src/stock_quant/data_model/dataset.py
from stock_quant.data_model.schemas import (
    ADJUSTED_BAR_SCHEMA,
    CORPORATE_ACTION_QUARANTINE_SCHEMA,
)

STANDARDIZED_SCHEMAS = {
    "daily_bar": DAILY_SCHEMA,
    "adjusted_bar": ADJUSTED_BAR_SCHEMA,
    "security_master": SECURITY_MASTER_SCHEMA,
    "security_master_coverage": SECURITY_MASTER_COVERAGE_SCHEMA,
    "corporate_action": CORPORATE_ACTION_SCHEMA,
    "corporate_action_quarantine": CORPORATE_ACTION_QUARANTINE_SCHEMA,
    "corporate_action_coverage": CORPORATE_ACTION_COVERAGE_SCHEMA,
    "trading_calendar": TRADING_CALENDAR_SCHEMA,
}
```

- [ ] **Step 4: Run the complete dataset publication suite**

Run: `pytest tests/integration/test_dataset_publish.py -v`

Expected: PASS; idempotent publication and pinned-reader tests include the new tables.

- [ ] **Step 5: Commit the publication contracts**

```bash
git add src/stock_quant/data_model/dataset.py tests/integration/test_dataset_publish.py
git commit -m "feat: publish adjusted price audit tables"
```

---

### Task 3: Incremental data pipeline and fixtures

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`
- Modify: `src/stock_quant/bootstrap.py`
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_data_pipeline.py`
- Modify: `tests/integration/test_research_runner.py`
- Modify: `tests/smoke/test_small_market_download.py`

**Interfaces:**
- Consumes: `build_adjusted_bars(daily_bar, corporate_actions, quarantined_actions, coverage, *, symbols)` and canonical schemas from Tasks 1–2.
- Produces: `_refresh_corporate_actions(enabled, symbols, start, end, issues, statuses, raw_snapshots, current_ca, current_quarantine) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]` ordered as facts, coverage, quarantine.
- Produces: `_read_baseline(issues)` returning master, calendar, daily bars, facts, and quarantine; every successful update republishes a full-history deterministic `adjusted_bar`.

- [ ] **Step 1: Write a failing end-to-end pipeline publication test**

```python
def test_update_publishes_internal_total_return_rows(project):
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        adjusted = context.read("adjusted_bar")
        assert set(adjusted["adjustment"]) == {"internal_total_return_v1"}
        assert set(adjusted["symbol"]) == set(_UNIVERSE_SYMBOLS)
        assert "corporate_action_quarantine" in context.tables
```

- [ ] **Step 2: Run the pipeline test to verify it fails**

Run: `pytest tests/integration/test_data_pipeline.py::test_update_publishes_internal_total_return_rows -v`

Expected: FAIL because successful updates do not publish either new table.

- [ ] **Step 3: Carry and merge quarantine rows instead of discarding them**

```python
def _refresh_corporate_actions(
    self,
    enabled,
    symbols,
    start,
    end,
    issues,
    statuses,
    raw_snapshots,
    current_ca,
    current_quarantine,
):
    if source is None:
        return current_ca, coverage_frame([]), current_quarantine
    merged_facts = _merge_corporate_actions(current_ca, canonical)
    merged_quarantine = _merge_corporate_action_quarantine(
        current_quarantine, reviewed.quarantined
    )
    return merged_facts, coverage, merged_quarantine
```

Implement `_merge_corporate_action_quarantine` with a deterministic key of `(symbol, ex_date, confirmed_by, reason)` and stable ordering by those columns. Update `_read_baseline` to read `corporate_action_quarantine` when present and otherwise return an empty canonical frame, preserving the ability to update pre-migration datasets.

- [ ] **Step 4: Build and validate the adjusted table before publication**

```python
adjusted = build_adjusted_bars(
    new_daily,
    corporate_action,
    corporate_action_quarantine,
    coverage,
    symbols=equity_symbols,
)
issues.extend(check_schema(adjusted, ADJUSTED_BAR_SCHEMA, table="adjusted_bar"))
issues.extend(check_primary_key_conflicts(adjusted, table="adjusted_bar"))

tables = {
    "daily_bar": new_daily,
    "adjusted_bar": adjusted,
    "security_master": master[list(SECURITY_MASTER_COLUMNS)],
    "security_master_coverage": master_coverage,
    "corporate_action": corporate_action[list(CORPORATE_ACTION_COLUMNS)],
    "corporate_action_quarantine": corporate_action_quarantine[
        list(CORPORATE_ACTION_QUARANTINE_COLUMNS)
    ],
    "corporate_action_coverage": coverage,
    "trading_calendar": _calendar_frame(calendar_open)[
        list(TRADING_CALENDAR_COLUMNS)
    ],
}
```

- [ ] **Step 5: Extend `validate` to verify adjusted-table schema, keys, and lineage**

```python
adjusted = context.read("adjusted_bar")
issues.extend(check_schema(adjusted, ADJUSTED_BAR_SCHEMA, table="adjusted_bar"))
issues.extend(check_primary_key_conflicts(adjusted, table="adjusted_bar"))
issues.extend(check_adjusted_bar_lineage(adjusted, daily, corporate_actions))
```

`check_adjusted_bar_lineage` must emit FATAL issues for a missing raw `(symbol, trade_date)`, mismatched `raw_close`, unknown `applied_action_ids`, or any non-`internal_total_return_v1` adjustment value.

```python
def check_adjusted_bar_lineage(
    adjusted: pd.DataFrame,
    daily: pd.DataFrame,
    corporate_actions: pd.DataFrame,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    raw = daily.set_index(["symbol", "trade_date"])["close"]
    known_action_ids = {
        action_id_of(row["symbol"], row["ex_date"])
        for row in corporate_actions.to_dict("records")
    }
    for row in adjusted.to_dict("records"):
        key = (row["symbol"], row["trade_date"])
        if key not in raw:
            issues.append(_adjusted_issue("adjusted_bar_missing_raw", row))
        elif float(row["raw_close"]) != float(raw.loc[key]):
            issues.append(_adjusted_issue("adjusted_bar_raw_close_mismatch", row))
        if row["adjustment"] != "internal_total_return_v1":
            issues.append(_adjusted_issue("adjusted_bar_wrong_basis", row))
        for action_id in json.loads(row["applied_action_ids"]):
            if action_id not in known_action_ids:
                issues.append(_adjusted_issue("adjusted_bar_unknown_action", row))
    return issues
```

`_adjusted_issue` returns a `QualityIssue` with `Severity.FATAL`, `table="adjusted_bar"`, and the row's symbol/trade date. Add all four codes to the repository's publication-blocking vocabulary so a lineage mismatch cannot publish.

- [ ] **Step 6: Update bootstrap and all synthetic publishers**

```python
empty_adjusted = pd.DataFrame(columns=ADJUSTED_BAR_COLUMNS)
empty_quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
tables = {
    "daily_bar": daily,
    "adjusted_bar": build_adjusted_bars(
        daily, corporate_actions, empty_quarantine, coverage,
        symbols=equity_symbols,
    ),
    "corporate_action_quarantine": empty_quarantine,
    # retain every existing table unchanged
}
```

Apply this pattern to `bootstrap.py`, the shared integration fixture, the research-runner synthetic publisher, and the smoke bootstrap. Bootstrap publishes empty canonical new tables; fixtures with bars generate adjusted rows through the production builder rather than copying `close` in test code.

- [ ] **Step 7: Add an incremental-update regression test**

```python
def test_later_action_does_not_rewrite_pre_ex_date_adjusted_rows(project):
    first = _run_update_with_actions(project, actions=[])
    before = _read_adjusted(project, first.version)
    second = _run_update_with_actions(project, actions=[_cash_action(ex_date=EVENT_DAY)])
    after = _read_adjusted(project, second.version)
    cutoff = before["trade_date"] < pd.Timestamp(EVENT_DAY)
    pd.testing.assert_frame_equal(
        before.loc[cutoff].reset_index(drop=True),
        after.loc[cutoff].reset_index(drop=True),
    )
```

- [ ] **Step 8: Run pipeline, bootstrap, and fixture-dependent tests**

Run: `pytest tests/integration/test_data_pipeline.py tests/integration/test_dataset_publish.py tests/integration/test_cli.py -v`

Expected: PASS with no network access.

- [ ] **Step 9: Commit the data path**

```bash
git add src/stock_quant/data_pipeline.py src/stock_quant/bootstrap.py tests/integration/conftest.py tests/integration/test_data_pipeline.py tests/integration/test_research_runner.py tests/smoke/test_small_market_download.py
git commit -m "feat: publish total return bars from verified actions"
```

---

### Task 4: Research-only consumption and Momentum60 v2

**Files:**
- Modify: `src/stock_quant/research/runner.py`
- Modify: `src/stock_quant/factors/momentum.py`
- Modify: `configs/experiments/momentum_60d.yml`
- Modify: `tests/unit/test_momentum.py`
- Modify: `tests/integration/test_factor_no_lookahead.py`
- Modify: `tests/integration/test_research_runner.py`

**Interfaces:**
- Consumes: pinned dataset table `adjusted_bar` from Tasks 2–3.
- Produces: `_DatasetFactorAdapter.factor_input()` with `adjusted_close`, quality status, and one fixed adjustment basis.
- Produces: `Momentum60.version == "2.0.0"`.

- [ ] **Step 1: Write failing adapter provenance and no-fallback tests**

```python
def test_research_factor_input_uses_adjusted_bar(project):
    with DatasetReader(project.root).open(project.version) as context:
        adjusted = context.read("adjusted_bar")
        adapter = _DatasetFactorAdapter(
            context=context, universe_symbols=tuple(adjusted["symbol"].unique())
        )
        factor_input = adapter.factor_input()
    assert set(factor_input["adjustment"]) == {"internal_total_return_v1"}
    assert factor_input["adjusted_close"].tolist() == adjusted["adjusted_close"].tolist()


def test_research_rejects_dataset_without_adjusted_bar(legacy_project):
    with pytest.raises(ResearchRunFailed, match="adjusted_bar"):
        ResearchRunner(legacy_project.root).run(legacy_project.experiment_spec)
```

- [ ] **Step 2: Run the adapter tests to verify they fail**

Run: `pytest tests/integration/test_research_runner.py -k 'adjusted_bar or factor_input' -v`

Expected: FAIL because the adapter reads `daily_bar` and assigns `adjusted_close = close`.

- [ ] **Step 3: Replace the adapter fallback with strict adjusted-table reads**

```python
def factor_input(self) -> pd.DataFrame:
    if "adjusted_bar" not in self._context.tables:
        raise ValueError(
            f"dataset {self._context.version} has no adjusted_bar; "
            "Momentum60 v2 requires internal_total_return_v1"
        )
    adjusted = self._context.read("adjusted_bar")
    rows = adjusted[adjusted["symbol"].isin(self._universe_symbols)].copy()
    if rows.empty:
        raise ValueError(
            f"dataset {self._context.version} has no adjusted rows for the universe"
        )
    if set(rows["adjustment"].astype(str)) != {"internal_total_return_v1"}:
        raise ValueError("factor input must use only internal_total_return_v1")
    rows["trade_date"] = rows["trade_date"].map(_as_date)
    rows["listed_trading_days"] = self._listed_trading_days(rows)
    return rows[[
        "trade_date", "symbol", "source", "adjustment", "adjusted_close",
        "quality_severity", "listed_trading_days",
    ]].sort_values(["symbol", "trade_date"], kind="stable").reset_index(drop=True)
```

Extract the existing calendar/list-date calculation into `_listed_trading_days(rows)` without changing its semantics.

- [ ] **Step 4: Add a failing Momentum60 break-window recovery test**

```python
def test_momentum_is_invalid_until_error_break_leaves_61_row_window():
    frame = factor_input_with_error_at(index=10, rows=72)
    crossed = Momentum60().compute(context(frame, signal_index=70)).frame.iloc[0]
    recovered = Momentum60().compute(context(frame, signal_index=71)).frame.iloc[0]
    assert crossed["is_valid"] == False
    assert crossed["invalid_reason"] == "quality_error"
    assert recovered["is_valid"] == True
```

- [ ] **Step 5: Upgrade the factor version and default spec**

```python
# src/stock_quant/factors/momentum.py
class Momentum60:
    name = "momentum_60d"
    version = "2.0.0"
```

```yaml
# configs/experiments/momentum_60d.yml
factor_versions:
  momentum_60d: 2.0.0
```

Update the shared fixture spec to `2.0.0`; retain explicit tests that a requested `1.0.0` is rejected by the current provider rather than silently upgraded.

- [ ] **Step 6: Strengthen the no-lookahead integration test at the dataset boundary**

```python
def test_future_corporate_action_cannot_change_prior_factor_results(project):
    before_version = publish_with_actions(project, actions=[])
    after_version = publish_with_actions(project, actions=[future_action])
    before = run_factor(project, before_version)
    after = run_factor(project, after_version)
    pd.testing.assert_frame_equal(
        before[before["trade_date"] < future_action.ex_date].reset_index(drop=True),
        after[after["trade_date"] < future_action.ex_date].reset_index(drop=True),
    )
```

- [ ] **Step 7: Prove execution still reads unadjusted prices**

```python
def test_total_return_factor_does_not_change_fill_or_valuation_prices(project):
    published = ResearchRunner(project.root).run(project.experiment_spec)
    fills = pd.read_parquet(published.path / "fills.parquet")
    daily = _read_table(project, "daily_bar")
    expected_opens = daily.set_index(["trade_date", "symbol"])["open"]
    for record in fills.itertuples():
        assert record.reference_price == pytest.approx(
            expected_opens.loc[(record.trade_date, record.symbol)]
        )
```

- [ ] **Step 8: Run factor and research suites**

Run: `pytest tests/unit/test_momentum.py tests/integration/test_factor_no_lookahead.py tests/integration/test_research_runner.py -v`

Expected: PASS; v2 consumes only internal total-return rows and execution remains unadjusted.

- [ ] **Step 9: Commit research consumption**

```bash
git add src/stock_quant/research/runner.py src/stock_quant/factors/momentum.py configs/experiments/momentum_60d.yml tests/unit/test_momentum.py tests/integration/test_factor_no_lookahead.py tests/integration/test_research_runner.py tests/integration/conftest.py
git commit -m "feat: make momentum v2 consume total return bars"
```

---

### Task 5: Experiment audit metadata and HTML reporting

**Files:**
- Modify: `src/stock_quant/research/runner.py`
- Modify: `src/stock_quant/reporting/html.py`
- Modify: `src/stock_quant/reporting/templates/experiment.html.j2`
- Modify: `tests/integration/test_research_runner.py`
- Modify: `tests/integration/test_reports.py`

**Interfaces:**
- Consumes: pinned `adjusted_bar` used by the experiment.
- Produces: `metrics["factor_input"]` with adjustment basis, factor versions, row count, ERROR break count, and invalid-reason counts.
- Produces: a visible “因子价格口径” report section.

- [ ] **Step 1: Write failing metrics audit test**

```python
def test_metrics_records_total_return_input_audit(project):
    published = ResearchRunner(project.root).run(project.experiment_spec)
    metrics = json.loads((published.path / "metrics.json").read_text())
    audit = metrics["factor_input"]
    assert audit["adjustment"] == "internal_total_return_v1"
    assert audit["factor_versions"] == {"momentum_60d": "2.0.0"}
    assert audit["row_count"] > 0
    assert audit["error_break_count"] == 0
    assert audit["invalid_reason_counts"] == {}
```

- [ ] **Step 2: Run the audit test to verify it fails**

Run: `pytest tests/integration/test_research_runner.py::test_metrics_records_total_return_input_audit -v`

Expected: FAIL with missing key `factor_input`.

- [ ] **Step 3: Add one deterministic audit helper and persist it in metrics**

```python
def _factor_input_audit(self, frozen: ExperimentSpec) -> dict[str, object]:
    adjusted = self._open_context(frozen.dataset_version).read("adjusted_bar")
    universe = adjusted[adjusted["symbol"].isin(self._universe_symbols)]
    errors = universe[universe["quality_severity"] == "ERROR"]
    counts = errors["invalid_reason"].value_counts().sort_index()
    return {
        "adjustment": "internal_total_return_v1",
        "factor_versions": dict(sorted(frozen.factor_versions.items())),
        "row_count": int(len(universe)),
        "error_break_count": int(len(errors)),
        "invalid_reason_counts": {
            str(reason): int(count) for reason, count in counts.items()
        },
    }
```

Add `metrics["factor_input"] = self._factor_input_audit(frozen)` before evaluator and report rendering so both consume the same persisted facts.

Also extend `_DefaultReport.render` with an escaped factor-input paragraph; the lightweight report produced directly by `research run` must expose the same basis and break count as the richer rebuilt report.

- [ ] **Step 4: Write failing rich-report rendering tests**

```python
def test_experiment_html_shows_factor_price_basis(tmp_path):
    report = _experiment_input(
        factor_input_audit={
            "adjustment": "internal_total_return_v1",
            "factor_versions": {"momentum_60d": "2.0.0"},
            "row_count": 100,
            "error_break_count": 2,
            "invalid_reason_counts": {"cross_source_conflict": 2},
        }
    )
    path = build_experiment_report(report, tmp_path / "report.html")
    html = path.read_text()
    assert "因子价格口径" in html
    assert "internal_total_return_v1" in html
    assert "momentum_60d: 2.0.0" in html
    assert "cross_source_conflict" in html
```

- [ ] **Step 5: Extend report input and template**

```python
# Add this field immediately after `generated_at` on ExperimentReportInput.
factor_input_audit: dict | None = None
```

```html
<section>
  <h2>因子价格口径</h2>
  <p>调整方法：{{ factor_input.adjustment }}</p>
  <p>因子版本：{{ factor_input.factor_versions }}</p>
  <p>输入行数：{{ factor_input.row_count }}；不可信断点：{{ factor_input.error_break_count }}</p>
  {% if factor_input.invalid_reason_counts %}
  <p>断点原因：{{ factor_input.invalid_reason_counts }}</p>
  {% endif %}
</section>
```

Remove only the obsolete universal limitation claiming that no adjusted series is consumed; retain the cross-source-close limitation.

- [ ] **Step 6: Run research and report tests**

Run: `pytest tests/integration/test_research_runner.py tests/integration/test_reports.py -v`

Expected: PASS with deterministic metrics and visible adjustment audit fields.

- [ ] **Step 7: Commit reporting changes**

```bash
git add src/stock_quant/research/runner.py src/stock_quant/reporting/html.py src/stock_quant/reporting/templates/experiment.html.j2 tests/integration/test_research_runner.py tests/integration/test_reports.py
git commit -m "feat: report factor price provenance"
```

---

### Task 6: Documentation, migration guidance, and full verification

**Files:**
- Modify: `README.md`
- Modify: `RUNBOOK.md`
- Modify: `PROJECT_MEMORY.md`
- Modify: `docs/operations/phase-one-validation.md`
- Modify: `notebooks/01_momentum_baseline.ipynb` only if its rendered text asserts unadjusted momentum

**Interfaces:**
- Consumes: final behavior from Tasks 1–5.
- Produces: operator guidance that distinguishes legacy v1 experiments from v2 total-return research.

- [ ] **Step 1: Replace obsolete behavior statements with exact migration rules**

```markdown
- `momentum_60d` v2 consumes the immutable `adjusted_bar` table with
  `adjustment=internal_total_return_v1`. The table is derived from unadjusted
  closes and verified cash-dividend/bonus/capitalization events.
- Orders, fills, price-limit checks and account valuation continue to use
  unadjusted `daily_bar` prices.
- Datasets created before `adjusted_bar` remain auditable but cannot run a v2
  Research experiment. Run a full data update to publish a compatible dataset.
- An untrusted corporate-action transition invalidates every momentum window
  that crosses it; the system never substitutes unadjusted close silently.
```

Record the credibility status as “复权/公司行为一致性已实现，等待真实数据验收” only after all verification commands below pass.

- [ ] **Step 2: Run formatting and static checks**

Run: `ruff check .`

Expected: PASS with no lint errors.

- [ ] **Step 3: Run focused offline regression suites**

Run: `pytest tests/unit/test_adjusted_bar.py tests/unit/test_momentum.py tests/integration/test_dataset_publish.py tests/integration/test_data_pipeline.py tests/integration/test_factor_no_lookahead.py tests/integration/test_research_runner.py tests/integration/test_reports.py -q`

Expected: PASS; no test is skipped except tests already explicitly marked for external services.

- [ ] **Step 4: Run the complete offline suite once**

Run: `pytest -q`

Expected: PASS under the repository default marker expression `not external and not smoke`.

- [ ] **Step 5: Verify documentation and secrets boundaries**

Run: `rg -n "adjusted_close=close|不发布、也不消费复权|momentum_60d: 1\.0\.0" README.md RUNBOOK.md PROJECT_MEMORY.md docs configs tests/integration/conftest.py`

Expected: no current-behavior matches; historical design documents may retain explicitly labeled phase-one history but must not be presented as current operation.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 6: Commit documentation and migration guidance**

```bash
git add README.md RUNBOOK.md PROJECT_MEMORY.md docs/operations/phase-one-validation.md notebooks/01_momentum_baseline.ipynb
git commit -m "docs: document total return research boundary"
```

- [ ] **Step 7: Record final evidence for review**

Run: `git status --short && git log --oneline -6`

Expected: the isolated worktree is clean and shows one focused commit for each task; hand off the exact Ruff and pytest summaries with the branch review.
