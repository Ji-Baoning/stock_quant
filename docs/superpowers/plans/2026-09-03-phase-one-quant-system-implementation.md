# A股量化项目第一阶段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个使用30只A股工程样本、三类免费数据源、固定60日动量和真实化账户约束的可复现研究闭环，输出可审计实验与静态报告。

**Architecture:** 系统采用严格单向依赖：数据源原始快照经过标准化、质量门禁和不可变版本发布后，才进入因子、组合、事件驱动回测、分析与报告。正式研究由`ExperimentSpec`固定全部输入，通过`research run`编排，并以确定性`experiment_id`原子发布产物；Notebook和CLI只调用正式模块。

**Tech Stack:** Conda、Python 3.10、Pandas、NumPy、PyArrow、DuckDB、Pydantic、Typer、PyYAML、Tushare、AKShare、BaoStock、Jinja2、Plotly、pytest、pytest-cov、Ruff

**Spec:** `docs/superpowers/specs/2026-09-03-phase-one-quant-system-design.md`

## Global Constraints

- 第一阶段是工程验证型MVP，不评价策略能否实盘，不连接券商，不自动下单。
- 数据范围固定为30只边界样本股、沪深300和中证500，时间为2020-01-01至最近完整交易日。
- 环境固定使用Conda与Python 3.10；第一阶段不安装VectorBT、RQAlpha、Qlib或VeighNa。
- Tushare Token只从`TUSHARE_TOKEN`环境变量读取，不进入代码、配置、日志、异常响应或运行清单。
- 原始数据按供应商隔离且不可覆盖；标准数据通过策略无关质量门禁后以不可变版本发布。
- BaoStock复权序列只用于连续性和动量；成交、估值与现金账户使用未复权价格和独立公司行为。
- 信号日为每周最后交易日收盘后，目标股数当日固定，下一交易日开盘先卖后买；100股整手且不透支。
- 正式研究只通过`ExperimentSpec`和`research run`发布；已发布实验不可覆盖，运行中只读固定`dataset_version`。
- 所有任务遵循TDD；普通测试不得联网，外部契约测试标记`external`，真实小范围冒烟测试标记`smoke`。
- 每个任务结束时只提交该任务列出的文件；不得提交`.env`、Token、市场数据、运行产物或生成报告。

---

### Task 1: 项目骨架、环境与类型安全配置

**Files:**
- Create: `environment.yml`
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `.env.example`
- Create: `configs/project.yml`
- Create: `configs/sources.yml`
- Create: `configs/costs.yml`
- Create: `src/stock_quant/__init__.py`
- Create: `src/stock_quant/config.py`
- Create: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: no application interfaces.
- Produces: `load_project_config(root: Path) -> ProjectConfig`; Pydantic models `ProjectConfig`, `SourceConfig`, `CostConfig`, `CostScenario`, `CostRate`.

- [ ] **Step 1: Write the failing configuration tests**

```python
# tests/unit/test_config.py
from pathlib import Path
import pytest
from pydantic import ValidationError
from stock_quant.config import ProjectConfig, load_project_config

def test_project_config_rejects_end_before_start():
    with pytest.raises(ValidationError):
        ProjectConfig(
            start_date="2020-02-01", end_date="2020-01-01",
            initial_cash=100000, benchmark_symbols=["000300.SH", "000905.SH"],
        )

def test_load_project_config_never_contains_tushare_token(tmp_path: Path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/project.yml").write_text(
        "start_date: 2020-01-01\nend_date: 2020-12-31\n"
        "initial_cash: 100000\nbenchmark_symbols: [000300.SH, 000905.SH]\n"
    )
    (tmp_path / "configs/sources.yml").write_text("tushare: {enabled: true}\n")
    (tmp_path / "configs/costs.yml").write_text("rates: []\n")
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-value")
    loaded = load_project_config(tmp_path)
    assert "secret-value" not in loaded.model_dump_json()
```

- [ ] **Step 2: Run the tests and confirm the missing package/config failure**

Run: `pytest tests/unit/test_config.py -v`

Expected: collection fails because `stock_quant.config` does not exist.

- [ ] **Step 3: Create the environment, packaging metadata, safe defaults and config models**

```python
# src/stock_quant/config.py
from datetime import date
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml

class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_retries: int = Field(default=3, ge=0, le=3)

class CostRate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effective_from: date
    commission_rate: float = Field(ge=0)
    minimum_commission: float = Field(ge=0)
    stamp_tax_sell_rate: float = Field(ge=0)
    slippage_rate: float = Field(ge=0)

class CostScenario(BaseModel):
    name: str
    rates: list[CostRate]

class CostConfig(BaseModel):
    scenarios: list[CostScenario] = Field(default_factory=list)

class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    initial_cash: float = Field(gt=0)
    benchmark_symbols: list[str]
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    costs: CostConfig = Field(default_factory=CostConfig)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self

def load_project_config(root: Path) -> ProjectConfig:
    def read(name: str):
        return yaml.safe_load((root / "configs" / name).read_text()) or {}
    project = read("project.yml")
    project["sources"] = read("sources.yml")
    project["costs"] = read("costs.yml")
    return ProjectConfig.model_validate(project)
```

Set `.env.example` to the single line `TUSHARE_TOKEN=replace_me`; configure `pyproject.toml` with `src` packaging, Ruff, pytest markers `external` and `smoke`, and pytest default options `-m "not external and not smoke"`. In `environment.yml`, declare Python 3.10 and every library in the Tech Stack. In the YAML configs, encode initial cash `100000`, the two benchmarks, and the three approved cost scenarios with the default full-cost rate `0.0003/5/0.0005/0.001`.

- [ ] **Step 4: Run static and unit checks**

Run: `ruff check src tests && pytest tests/unit/test_config.py -v`

Expected: both commands pass; the serialized configuration contains no environment secret.

- [ ] **Step 5: Commit the foundation**

```bash
git add environment.yml pyproject.toml README.md .env.example configs src/stock_quant/__init__.py src/stock_quant/config.py tests/unit/test_config.py
git commit -m "build: scaffold quant project configuration"
```

### Task 2: 数据源契约、重试与不可变原始快照

**Files:**
- Create: `src/stock_quant/data_sources/base.py`
- Create: `src/stock_quant/data_sources/tushare.py`
- Create: `src/stock_quant/data_sources/akshare.py`
- Create: `src/stock_quant/data_sources/baostock.py`
- Create: `src/stock_quant/data_sources/raw_store.py`
- Create: `tests/unit/test_raw_store.py`
- Create: `tests/unit/test_source_retry.py`
- Create: `tests/integration/test_source_contracts.py`

**Interfaces:**
- Consumes: `SourceConfig` from Task 1.
- Produces: `DataRequest`, `FetchResult`, `RetryPolicy`, source exception classes, `DataSource.fetch(request)`, `fetch_with_retry(source, request, policy)`, `RawStore.save(result) -> RawSnapshot`.

- [ ] **Step 1: Write failing tests for immutable snapshots and retry classification**

```python
# tests/unit/test_raw_store.py
import pandas as pd
import pytest
from stock_quant.data_sources.base import FetchResult
from stock_quant.data_sources.raw_store import RawStore

def test_raw_store_is_content_addressed_and_refuses_conflicting_overwrite(tmp_path):
    store = RawStore(tmp_path)
    result = FetchResult(source="tushare", endpoint="daily", request_key="abc", frame=pd.DataFrame({"x": [1]}), metadata={})
    first = store.save(result)
    second = store.save(result)
    assert first.path == second.path
    assert first.sha256 == second.sha256
    changed = FetchResult(source="tushare", endpoint="daily", request_key="abc", frame=pd.DataFrame({"x": [2]}), metadata={})
    revision = store.save(changed)
    assert revision.path != first.path
    assert pd.read_parquet(first.path / "data.parquet").x.tolist() == [1]

# tests/unit/test_source_retry.py
def test_retry_retries_rate_limit_but_not_authentication(fake_source, request):
    fake_source.failures = [RateLimitError("slow"), None]
    assert fetch_with_retry(fake_source, request, RetryPolicy(max_attempts=3)).source == "fake"
    fake_source.failures = [AuthenticationError("bad token")]
    with pytest.raises(AuthenticationError):
        fetch_with_retry(fake_source, request, RetryPolicy(max_attempts=3))
```

- [ ] **Step 2: Run the focused tests and confirm missing interfaces**

Run: `pytest tests/unit/test_raw_store.py tests/unit/test_source_retry.py -v`

Expected: FAIL during import because the data-source modules are absent.

- [ ] **Step 3: Implement the protocol, adapters, redaction, retries and snapshot manifest**

```python
# src/stock_quant/data_sources/base.py
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol
import pandas as pd

@dataclass(frozen=True)
class DataRequest:
    endpoint: str
    symbols: tuple[str, ...]
    start_date: date
    end_date: date
    params: dict[str, str] = field(default_factory=dict)

@dataclass(frozen=True)
class FetchResult:
    source: str
    endpoint: str
    request_key: str
    frame: pd.DataFrame
    metadata: dict[str, str]

class DataSource(Protocol):
    name: str
    def fetch(self, request: DataRequest) -> FetchResult: ...

class TransientSourceError(RuntimeError): pass
class RateLimitError(TransientSourceError): pass
class AuthenticationError(RuntimeError): pass
class ContractError(RuntimeError): pass
```

Define `RetryPolicy(max_attempts: int = 3, maximum_wait_seconds: int = 30)` and a test-local `FakeSource` whose `fetch` pops exceptions/results from a list. `TushareSource` must require `os.environ["TUSHARE_TOKEN"]` only inside its constructor and support unadjusted `daily`; `BaoStockSource` must open/close a session and support unadjusted plus forward/back adjustment flags; `AkShareSource` must support index history, stock metadata, CNINFO corporate actions and Eastmoney cross-check data. Each adapter returns supplier-native columns unchanged. `fetch_with_retry` retries only `TransientSourceError`, rate limits, server failures and expired BaoStock sessions, at most three attempts with waits capped at 30 seconds; authentication, contract, type and parameter errors escape immediately. `RawStore.save` writes Parquet plus JSON manifest under `data/raw/<source>/<endpoint>/<request_key>/<sha256>/` using a temporary sibling directory and atomic rename; the manifest records request parameters, request/response timestamps, supplier endpoint, SDK version, row count, schema, response/file hashes and redacted status. An existing identical hash is reused, while revised content gets a distinct hash directory and leaves the first snapshot unchanged.

- [ ] **Step 4: Add recorded-response contract tests and run offline verification**

Create fixture responses with supplier-native column names for one symbol and two dates. Assert each adapter maps authentication, empty/truncated response, date range and symbol mismatch to the exact exception type without calling the network.

Run: `ruff check src tests && pytest tests/unit/test_raw_store.py tests/unit/test_source_retry.py tests/integration/test_source_contracts.py -v`

Expected: PASS with no network access and no Token in captured logs.

- [ ] **Step 5: Commit the source boundary**

```bash
git add src/stock_quant/data_sources tests/unit/test_raw_store.py tests/unit/test_source_retry.py tests/integration/test_source_contracts.py tests/fixtures
git commit -m "feat: add auditable market data source adapters"
```

### Task 3: 标准数据模型、证券代码与确定性清洗

**Files:**
- Create: `src/stock_quant/data_model/schemas.py`
- Create: `src/stock_quant/data_model/symbols.py`
- Create: `src/stock_quant/data_model/clean.py`
- Create: `src/stock_quant/data_model/normalize.py`
- Create: `tests/unit/test_symbols.py`
- Create: `tests/unit/test_normalize.py`
- Create: `tests/fixtures/raw/*.parquet`

**Interfaces:**
- Consumes: supplier-native `FetchResult.frame` from Task 2.
- Produces: `normalize_symbol(value, source) -> str`; `normalize_daily(frame, source, ingested_at) -> CleanResult`; PyArrow schemas `DAILY_SCHEMA`, `CORPORATE_ACTION_SCHEMA`, `SECURITY_MASTER_SCHEMA`, `TRADING_CALENDAR_SCHEMA`.

- [ ] **Step 1: Write failing normalization and audit tests**

```python
def test_symbols_are_canonical():
    assert normalize_symbol("sh.600000", "baostock") == "600000.SH"
    assert normalize_symbol("000001.SZ", "tushare") == "000001.SZ"

def test_daily_normalization_converts_units_and_audits_drops():
    raw = pd.DataFrame({
        "date": ["2020-01-02", "bad"], "code": ["sh.600000", "sh.600000"],
        "open": [10, 10], "high": [11, 11], "low": [9, 9], "close": [10.5, 10.5],
        "volume": [100, 100], "amount": [1050, 1050],
    })
    result = normalize_daily(raw, "baostock", pd.Timestamp("2020-01-03", tz="UTC"))
    assert result.valid.iloc[0].to_dict()["symbol"] == "600000.SH"
    assert result.valid.iloc[0].to_dict()["volume"] == 100
    assert result.rejected.iloc[0]["reason"] == "invalid_trade_date"
```

- [ ] **Step 2: Run tests and verify failure before implementation**

Run: `pytest tests/unit/test_symbols.py tests/unit/test_normalize.py -v`

Expected: FAIL because canonicalization and schemas are undefined.

- [ ] **Step 3: Implement canonical schemas and deterministic transforms**

```python
@dataclass(frozen=True)
class CleanResult:
    valid: pd.DataFrame
    rejected: pd.DataFrame
    audit: pd.DataFrame

DAILY_COLUMNS = [
    "trade_date", "symbol", "open", "high", "low", "close",
    "volume", "amount", "adjustment", "source", "ingested_at",
]
```

Canonical symbols are six digits plus `.SH` or `.SZ`; dates are timezone-free `date`; prices use yuan; `volume` uses shares; `amount` uses yuan. Cleaning may trim strings, parse declared dates/numbers, normalize codes and convert documented units. It must never interpolate prices, forward-fill missing trade rows, average suppliers, infer suspensions, or repair illegal OHLC. Every rejected row retains source row identity, reason and original values. Duplicate identical rows collapse with an audit record; duplicate keys with different content remain for the quality layer to reject.

- [ ] **Step 4: Run schema and normalization tests**

Run: `ruff check src tests && pytest tests/unit/test_symbols.py tests/unit/test_normalize.py -v`

Expected: PASS; normalized frames have exact column order and stable dtypes.

- [ ] **Step 5: Commit the standard data contract**

```bash
git add src/stock_quant/data_model tests/unit/test_symbols.py tests/unit/test_normalize.py tests/fixtures/raw
git commit -m "feat: normalize supplier data into canonical schemas"
```

### Task 4: 数据质量、跨源比较与不可变数据集发布

**Files:**
- Create: `src/stock_quant/data_quality/models.py`
- Create: `src/stock_quant/data_quality/raw_checks.py`
- Create: `src/stock_quant/data_quality/compare.py`
- Create: `src/stock_quant/data_quality/gates.py`
- Create: `src/stock_quant/data_model/dataset.py`
- Create: `tests/unit/test_quality_checks.py`
- Create: `tests/integration/test_dataset_publish.py`

**Interfaces:**
- Consumes: `CleanResult` and canonical tables from Task 3.
- Produces: `QualityIssue`, `QualityReport`, `compare_daily_sources(left, right, thresholds)`, `evaluate_publication(report) -> GateDecision`, `DatasetPublisher.publish(tables, report) -> DatasetRef`, `DatasetReader.open(version) -> DatasetContext`.

- [ ] **Step 1: Write failing threshold, gate and atomic publication tests**

```python
def test_close_difference_over_point_two_percent_is_error():
    issues = compare_daily_sources(row(close=10.00), row(close=10.03), DEFAULT_THRESHOLDS)
    assert issues[0].severity is Severity.ERROR

def test_fatal_quality_does_not_move_current(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    good = publisher.publish(valid_tables(), passing_report())
    with pytest.raises(PublicationBlocked):
        publisher.publish(valid_tables(), report_with_fatal("duplicate_conflict"))
    assert publisher.current().version == good.version
```

- [ ] **Step 2: Run tests and confirm they fail for missing quality layer**

Run: `pytest tests/unit/test_quality_checks.py tests/integration/test_dataset_publish.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement severity rules, missing classification and strategy-neutral gate**

```python
class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"

@dataclass(frozen=True)
class QualityIssue:
    severity: Severity
    code: str
    table: str
    symbol: str | None
    trade_date: date | None
    details: dict[str, object]
```

Implement positive price/nonnegative volume, OHLC relationship, primary-key conflict, source metadata and unit checks. Classify missing rows in this order: not listed, delisted, non-trading day, all-source missing/unknown-or-suspended, primary-only missing, unexplained. Cross-source comparison may only compare equal adjustment and units: absolute difference at most ¥0.01 is `INFO`; above ¥0.01 and relative difference above 0.05% is `WARNING`; close difference above 0.20% is `ERROR`. The publication gate checks only schemas, uniqueness, illegal OHLC, provenance, quarantine reasons and report generation; momentum lookback and execution dates belong to `evaluate_backtest_readiness`.

- [ ] **Step 4: Implement version hashing, DuckDB reader and atomic CURRENT update**

Write standardized tables plus `quality_report.json` and `dataset_manifest.json` to a staging directory. Compute `dataset_version` from sorted file hashes, schema versions and normalized build configuration, atomically rename to `data/standardized/<dataset_version>/`, then atomically replace a small `CURRENT` text file. `DatasetReader.open(version)` creates read-only DuckDB views over exact Parquet paths and never consults `CURRENT` again. Add a test that changes `CURRENT` after opening and confirms queries still use the original version.

Run: `ruff check src tests && pytest tests/unit/test_quality_checks.py tests/integration/test_dataset_publish.py -v`

Expected: PASS, including preservation of the prior `CURRENT` after blocked publication.

- [ ] **Step 5: Commit the publication boundary**

```bash
git add src/stock_quant/data_quality src/stock_quant/data_model/dataset.py tests/unit/test_quality_checks.py tests/integration/test_dataset_publish.py
git commit -m "feat: gate and publish immutable standardized datasets"
```

### Task 5: 固定工程股票池、交易日与公司行为标准化

**Files:**
- Create: `configs/universe.yml`
- Create: `configs/trading_rules.yml`
- Create: `src/stock_quant/data_model/universe.py`
- Create: `src/stock_quant/data_model/calendar.py`
- Create: `src/stock_quant/data_model/corporate_actions.py`
- Create: `src/stock_quant/data_model/trading_rules.py`
- Create: `tests/unit/test_universe.py`
- Create: `tests/unit/test_calendar.py`
- Create: `tests/unit/test_corporate_action_normalize.py`
- Create: `tests/unit/test_trading_rules.py`

**Interfaces:**
- Consumes: canonical schemas and AKShare native corporate-action frames.
- Produces: `Universe`, `UniverseEntry`, `TradingCalendar`, `TradingRuleBook`, `normalize_corporate_actions(cninfo, eastmoney) -> CorporateActionResult`.

- [ ] **Step 1: Write failing boundary tests**

```python
def test_universe_has_exact_board_quotas():
    universe = Universe.from_yaml(Path("configs/universe.yml"))
    assert len(universe.entries) == 30
    assert universe.counts_by_board() == {"sh_main": 8, "sz_main": 8, "chinext": 7, "star": 7}
    assert all(e.selection_reason and e.boundary_tags for e in universe.entries)

def test_conflicting_implemented_actions_are_quarantined():
    result = normalize_corporate_actions(cninfo_cash(0.1), eastmoney_cash(0.2))
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == "cross_source_conflict"

def test_price_limit_schedule_is_effective_dated(rule_book):
    assert rule_book.limit_rate("300001.SZ", date(2020, 8, 21), status="NORMAL") == Decimal("0.10")
    assert rule_book.limit_rate("300001.SZ", date(2020, 8, 24), status="NORMAL") == Decimal("0.20")
    assert rule_book.limit_rate("600000.SH", date(2020, 8, 24), status="ST") == Decimal("0.05")
```

- [ ] **Step 2: Run tests and verify missing domain modules**

Run: `pytest tests/unit/test_universe.py tests/unit/test_calendar.py tests/unit/test_corporate_action_normalize.py tests/unit/test_trading_rules.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement versioned universe and calendar rules**

Populate exactly these engineering samples, which are coverage fixtures rather than recommendations: SH main `600000.SH,600036.SH,600519.SH,601318.SH,601398.SH,601857.SH,601919.SH,603288.SH`; SZ main `000001.SZ,000333.SZ,000651.SZ,000858.SZ,002415.SZ,002475.SZ,002594.SZ,002714.SZ`; ChiNext `300001.SZ,300059.SZ,300122.SZ,300274.SZ,300750.SZ,300760.SZ,301269.SZ`; STAR `688001.SH,688008.SH,688009.SH,688036.SH,688111.SH,688506.SH,688981.SH`. Populate `name_at_selection` from the point-in-time security master as of `selected_as_of=2026-09-03`, not from a hard-coded current-name lookup. Each YAML entry must contain `symbol`, `name_at_selection`, `exchange`, `board`, `selected_as_of`, `boundary_tags`, `selection_reason`; tag post-2020 listings so pre-listing gaps are expected. `Universe.version` is the SHA-256 of canonical YAML content. `TradingCalendar.last_trading_day_each_week(start, end)` uses benchmark-confirmed open days and returns the final open date in each ISO week; `next_trading_day(date)` must raise after the calendar boundary rather than guess.

- [ ] **Step 4: Implement corporate-action reconciliation and effective-dated trading rules**

Accept only implemented actions with announcement, record, ex-date and complete distribution ratios. Preserve pre-tax cash dividend, bonus, capitalization, rights fields and both source references. Equal CNINFO/Eastmoney facts cross-confirm; conflicts quarantine; unsupported rights issue, merger or conversion is explicitly tagged and later blocks a holding-period backtest.

`TradingRuleBook` reads effective-dated rules rather than embedding them in execution code: ordinary main-board 10%, ST/*ST 5%, STAR 20%, ChiNext 10% before 2020-08-24 and 20% from that date; STAR and registration-based IPO regimes mark the first five trading sessions as having no daily limit. Limit prices round to ¥0.01 using exchange-compatible decimal rounding. Record main-board registration regime from 2023-04-10 in the YAML. Given no queue data, conservatively reject a buy at the upper limit and a sell at the lower limit. Any security/date/status not covered by the rule book blocks execution instead of assuming a percentage.

Run: `ruff check src tests && pytest tests/unit/test_universe.py tests/unit/test_calendar.py tests/unit/test_corporate_action_normalize.py tests/unit/test_trading_rules.py -v`

Expected: PASS; the tests prove the fixed board quotas, dated rules and conflict quarantine.

- [ ] **Step 5: Commit reference data rules**

```bash
git add configs/universe.yml configs/trading_rules.yml src/stock_quant/data_model/universe.py src/stock_quant/data_model/calendar.py src/stock_quant/data_model/corporate_actions.py src/stock_quant/data_model/trading_rules.py tests/unit/test_universe.py tests/unit/test_calendar.py tests/unit/test_corporate_action_normalize.py tests/unit/test_trading_rules.py
git commit -m "feat: define fixed universe calendar and market rules"
```

### Task 6: 因子协议、结果模式与60日动量

**Files:**
- Create: `src/stock_quant/factors/base.py`
- Create: `src/stock_quant/factors/models.py`
- Create: `src/stock_quant/factors/momentum.py`
- Create: `tests/unit/test_factor_contract.py`
- Create: `tests/unit/test_momentum.py`
- Create: `tests/integration/test_factor_no_lookahead.py`

**Interfaces:**
- Consumes: immutable `DatasetContext`, `Universe`, `TradingCalendar`.
- Produces: `Factor` protocol; `FactorContext`; `FactorResult`; `Momentum60.compute(context) -> FactorResult`.

- [ ] **Step 1: Write failing contract, eligibility and no-lookahead tests**

```python
def test_momentum_contract_metadata():
    factor = Momentum60()
    assert (factor.name, factor.version, factor.lookback, factor.frequency) == ("momentum_60d", "1.0.0", 60, "weekly")
    assert factor.required_fields == frozenset({"adjusted_close", "quality_severity", "listed_trading_days"})

def test_momentum_uses_exact_sixty_session_lag(context_with_61_rows):
    result = Momentum60().compute(context_with_61_rows).frame.iloc[0]
    assert result.raw_value == pytest.approx(121 / 100 - 1)
    assert result.is_valid

def test_future_rows_do_not_change_historical_factor(base_context, context_with_future):
    before = Momentum60().compute(base_context).frame
    after = Momentum60().compute(context_with_future).frame.query("trade_date <= @base_context.end_date")
    pd.testing.assert_frame_equal(before, after.reset_index(drop=True))
```

- [ ] **Step 2: Run tests and confirm missing factor interfaces**

Run: `pytest tests/unit/test_factor_contract.py tests/unit/test_momentum.py tests/integration/test_factor_no_lookahead.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement immutable context and standardized result**

```python
@dataclass(frozen=True)
class FactorContext:
    dataset: DatasetContext
    universe_version: str
    start_date: date
    end_date: date
    signal_dates: tuple[date, ...]

@dataclass(frozen=True)
class FactorResult:
    factor_name: str
    factor_version: str
    frame: pd.DataFrame

class Factor(Protocol):
    name: str
    version: str
    lookback: int
    required_fields: frozenset[str]
    frequency: str
    def compute(self, context: FactorContext) -> FactorResult: ...
```

Validate exact result columns `trade_date,symbol,factor_name,factor_version,raw_value,processed_value,is_valid,invalid_reason`, unique date/symbol keys and nonempty reasons for invalid rows.

- [ ] **Step 4: Implement Momentum60 and run tests**

For each weekly signal date, use one source and one adjustment series, require 61 observations, no `ERROR` in the lookback, and at least 120 listed trading days. Compute `adjusted_close[t] / adjusted_close[t-60] - 1`; set processed equal to raw; emit invalid rows with `insufficient_observations`, `quality_error` or `seasoning_below_120`. Sort by trade date and symbol for determinism.

Run: `ruff check src tests && pytest tests/unit/test_factor_contract.py tests/unit/test_momentum.py tests/integration/test_factor_no_lookahead.py -v`

Expected: PASS, including byte-stable Parquet output when written twice with identical inputs.

- [ ] **Step 5: Commit the first factor**

```bash
git add src/stock_quant/factors tests/unit/test_factor_contract.py tests/unit/test_momentum.py tests/integration/test_factor_no_lookahead.py
git commit -m "feat: add versioned momentum factor contract"
```

### Task 7: 实验规格、确定性身份与不可变注册表

**Files:**
- Create: `configs/experiments/momentum_60d.yml`
- Create: `src/stock_quant/research/spec.py`
- Create: `src/stock_quant/research/registry.py`
- Create: `tests/unit/test_experiment_spec.py`
- Create: `tests/integration/test_experiment_registry.py`

**Interfaces:**
- Consumes: config models, dataset/universe/factor versions.
- Produces: `ExperimentSpec`, `ExperimentIdentity`, `load_experiment_spec(path)`, `compute_experiment_id(spec)`, `ExperimentRegistry.publish(run_dir, identity)`, `ExperimentRegistry.rebuild()`.

- [ ] **Step 1: Write failing identity and immutability tests**

```python
def test_experiment_id_is_deterministic_and_sensitive_to_result_inputs(spec):
    assert compute_experiment_id(spec) == compute_experiment_id(spec.model_copy(deep=True))
    changed = spec.model_copy(update={"random_seed": spec.random_seed + 1})
    assert compute_experiment_id(spec) != compute_experiment_id(changed)

def test_publish_is_atomic_and_never_overwrites(tmp_path, completed_run, identity):
    registry = ExperimentRegistry(tmp_path)
    published = registry.publish(completed_run, identity)
    assert published.manifest.status == "REJECTED"
    assert registry.publish(completed_run, identity).path == published.path
    (completed_run / "metrics.json").write_text("{}")
    with pytest.raises(IdentityConflict):
        registry.publish(completed_run, identity)
```

- [ ] **Step 2: Run tests and verify absent experiment layer**

Run: `pytest tests/unit/test_experiment_spec.py tests/integration/test_experiment_registry.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement strict spec parsing and canonical hashing**

`ExperimentSpec` forbids extra fields and includes hypothesis, versioned factors, explicit dataset/universe versions, date range, `not_applicable_engineering_mvp`, preprocessing, portfolio rule, cost scenarios, seed, code commit, parent IDs and optional agent ID. Resolve a requested `CURRENT` once before validation and write the explicit version into the frozen spec. Hash RFC-8785-style canonical JSON semantics implemented as sorted UTF-8 JSON with normalized dates/floats; include every field that can affect execution plus code and data versions.

- [ ] **Step 4: Implement staging publication, statuses and single-writer registry**

Require a complete manifest with `COMPLETED` plus evaluation `ACCEPTED` or `REJECTED`; `ACCEPTED` never means live approval. Validate all declared artifact hashes, then atomically rename from `data/runs/<run_id>/publish/` to `data/experiments/<experiment_id>/`. On identical existing content, return it; on conflicting content, raise. Rebuild `registry.parquet` from immutable manifests while holding an exclusive lock file created with `O_CREAT|O_EXCL`; always release it in `finally`. Execution failures remain under `data/runs/<run_id>/` and never enter an experiment directory.

Run: `ruff check src tests && pytest tests/unit/test_experiment_spec.py tests/integration/test_experiment_registry.py -v`

Expected: PASS, including a two-process test in which only one registry publisher obtains the lock.

- [ ] **Step 5: Commit reproducible experiment identity**

```bash
git add configs/experiments src/stock_quant/research/spec.py src/stock_quant/research/registry.py tests/unit/test_experiment_spec.py tests/integration/test_experiment_registry.py
git commit -m "feat: add immutable experiment specifications and registry"
```

### Task 8: 因子排名到目标组合

**Files:**
- Create: `src/stock_quant/portfolio/models.py`
- Create: `src/stock_quant/portfolio/equal_weight.py`
- Create: `tests/unit/test_equal_weight.py`

**Interfaces:**
- Consumes: `FactorResult`, signal-date unadjusted close and eligibility.
- Produces: `PortfolioTarget`; `TopNEqualWeight(top_n=10, lot_size=100).build(factors: FactorResult, signal_prices: pd.DataFrame, capital: float) -> PortfolioTarget`.

- [ ] **Step 1: Write failing deterministic ranking and cash-retention tests**

```python
def test_top_ten_equal_weight_and_symbol_tie_break():
    target = TopNEqualWeight(top_n=10, lot_size=100).build(factors_with_tie(), closes(), capital=100000)
    assert target.frame.symbol.tolist()[:2] == ["000001.SZ", "600000.SH"]
    assert set(target.frame.target_weight) == {0.1}
    assert (target.frame.target_quantity % 100 == 0).all()

def test_fewer_than_ten_eligible_names_keeps_unallocated_cash():
    target = TopNEqualWeight(top_n=10).build(six_valid_factors(), closes(), capital=100000)
    assert target.frame.target_weight.sum() == pytest.approx(0.6)
    assert target.unallocated_weight == pytest.approx(0.4)
```

- [ ] **Step 2: Run tests and confirm missing portfolio builder**

Run: `pytest tests/unit/test_equal_weight.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement target schema and builder**

Rank only `is_valid=True` rows by descending processed value and ascending symbol. Select at most ten; assign fixed 10% target weight each. Estimate quantity from signal-day unadjusted close and initial/scenario equity, floor to 100 shares, and record `rank`, `signal_price`, `target_weight`, `target_quantity`, `selection_reason`. Never inspect next-day prices or replace an unavailable name using later information.

- [ ] **Step 4: Run portfolio tests**

Run: `ruff check src tests && pytest tests/unit/test_equal_weight.py -v`

Expected: PASS with deterministic ordering and explicit retained cash.

- [ ] **Step 5: Commit portfolio construction**

```bash
git add src/stock_quant/portfolio tests/unit/test_equal_weight.py
git commit -m "feat: convert factor ranks into lot-sized targets"
```

### Task 9: 订单、成交、费用与T+1账户账本

**Files:**
- Create: `src/stock_quant/backtest/models.py`
- Create: `src/stock_quant/backtest/costs.py`
- Create: `src/stock_quant/backtest/execution.py`
- Create: `src/stock_quant/backtest/account.py`
- Create: `tests/unit/test_costs.py`
- Create: `tests/unit/test_execution.py`
- Create: `tests/unit/test_account.py`

**Interfaces:**
- Consumes: `PortfolioTarget`, execution-date unadjusted bars, `CostConfig`, `TradingRuleBook`.
- Produces: immutable `Order`, `Fill`, `PositionLot`, ledger entries; `CostModel.calculate(side: str, quantity: int, raw_price: Decimal, trade_date: date) -> FeeBreakdown`; `ExecutionSimulator.execute(orders: list[Order], bars: pd.DataFrame, account: Account, trade_date: date) -> ExecutionResult`; `Account.apply_fill(fill: Fill) -> None`.

- [ ] **Step 1: Write failing fee, slippage, cash and T+1 tests**

```python
def test_minimum_commission_and_sell_stamp_tax():
    model = CostModel(rate(commission=.0003, minimum=5, stamp=.0005, slippage=.001))
    buy = model.calculate(side="BUY", quantity=100, raw_price=10, trade_date=date(2020, 1, 2))
    sell = model.calculate(side="SELL", quantity=100, raw_price=10, trade_date=date(2020, 1, 2))
    assert buy.commission == 5 and buy.stamp_tax == 0
    assert sell.commission == 5 and sell.stamp_tax == pytest.approx(0.5)

def test_same_day_lot_is_not_sellable(account):
    account.apply_fill(fill("BUY", "600000.SH", 100, date(2020, 1, 2)))
    with pytest.raises(InsufficientSellableQuantity):
        account.apply_fill(fill("SELL", "600000.SH", 100, date(2020, 1, 2)))

def test_cash_shortage_reduces_buys_in_lots_without_overdraft(simulator):
    fills = simulator.execute(expensive_buy_orders(), bars(), cash=1500)
    assert sum(f.cash_delta for f in fills) >= -1500
    assert all(f.quantity % 100 == 0 for f in fills)
```

- [ ] **Step 2: Run tests and verify missing execution/account modules**

Run: `pytest tests/unit/test_costs.py tests/unit/test_execution.py tests/unit/test_account.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement dated fees, slippage and execution rejections**

Choose the latest cost rate whose effective date is not after trade date. Buy price is `open*(1+slippage)` and sell price is `open*(1-slippage)`. Commission is `max(notional*rate, minimum)` per fill; stamp tax applies to sells only. Reject with exact reason for missing open, suspended/unknown, quality `ERROR`, prohibited price-limit state, insufficient sellable quantity or cash. Execute sells before buys. When buys exceed cash, reduce in the precomputed priority order by 100-share lots; never recompute target ranking or use leverage.

- [ ] **Step 4: Implement lots and append-only ledgers, then run tests**

Each buy creates a `PositionLot(symbol,buy_date,quantity,cost_basis,available_date)` with the next trading date as available. Sells consume available lots FIFO. Account methods append cash, order, fill and position events and assert cash remains nonnegative within one-cent tolerance. Add tests for duplicate `fill_id` rejection and deterministic replay to the same account state.

Run: `ruff check src tests && pytest tests/unit/test_costs.py tests/unit/test_execution.py tests/unit/test_account.py -v`

Expected: PASS for zero-cost, commission/tax and full-cost parameterizations.

- [ ] **Step 5: Commit the execution core**

```bash
git add src/stock_quant/backtest tests/unit/test_costs.py tests/unit/test_execution.py tests/unit/test_account.py
git commit -m "feat: simulate A-share orders costs and T-plus-one lots"
```

### Task 10: 公司行为入账、停牌估值与事件驱动回测引擎

**Files:**
- Create: `src/stock_quant/backtest/corporate_actions.py`
- Create: `src/stock_quant/backtest/valuation.py`
- Create: `src/stock_quant/backtest/engine.py`
- Create: `tests/unit/test_corporate_action_accounting.py`
- Create: `tests/unit/test_valuation.py`
- Create: `tests/integration/test_backtest_engine.py`
- Create: `tests/fixtures/synthetic_market/*.parquet`

**Interfaces:**
- Consumes: account/execution interfaces, calendar, corporate actions, targets and exact dataset version.
- Produces: `BacktestRequest`, `BacktestResult`; `BacktestEngine.run(request) -> BacktestResult`.

- [ ] **Step 1: Write failing action, stale valuation and end-to-end ledger tests**

```python
def test_ex_date_cash_and_bonus_are_applied_once(account_with_record_date_holding):
    action = implemented_action(cash_per_share=.1, bonus_ratio=.2)
    apply_corporate_action(account_with_record_date_holding, action)
    apply_corporate_action(account_with_record_date_holding, action)
    assert account_with_record_date_holding.cash == pytest.approx(10000 + 10)
    assert account_with_record_date_holding.quantity("600000.SH") == 120

def test_suspended_holding_uses_last_close_only_for_valuation():
    value = value_account(account_with_100_shares(), bar=None, last_close=10, stale_days=3)
    assert value.market_value == 1000
    assert value.stale_valuation and value.stale_days == 3

def test_engine_golden_ledgers_match(synthetic_request):
    result = BacktestEngine().run(synthetic_request)
    assert_frame_equal(result.fills, expected_fills())
    assert_frame_equal(result.daily_equity, expected_equity())
```

- [ ] **Step 2: Run tests and verify missing engine components**

Run: `pytest tests/unit/test_corporate_action_accounting.py tests/unit/test_valuation.py tests/integration/test_backtest_engine.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement idempotent actions and valuation**

On ex-date before the open, credit pre-tax cash using record-date quantity and increase lots for bonus/capitalization ratios; create one ledger entry keyed by unique action ID and refuse duplicate application. If a held name encounters rights, merger, conversion, incomplete or conflicted action, raise `UnsupportedCorporateAction` and stop that scenario. At each close, value at unadjusted close; if absent, carry the last valid close only for valuation and record stale days and stale asset percentage. Never use carried prices for fills.

- [ ] **Step 4: Implement chronological engine and the 10-name/80-day golden fixture**

Before starting, run backtest-readiness checks for both benchmark series, all factor lookbacks, signal/execution prices and held-period actions. For each open date: apply actions, unlock prior lots, execute scheduled sells then buys. For each close: value and append equity. The fixture must cover normal trading, suspension, dividend, bonus/capitalization, new listing, open-price limit rejection, unaffordable high price, duplicate conflict, illegal OHLC and cross-source conflict. Run three completely separate accounts for zero, fee/tax and full-cost scenarios so costs affect subsequent quantities.

Run: `ruff check src tests && pytest tests/unit/test_corporate_action_accounting.py tests/unit/test_valuation.py tests/integration/test_backtest_engine.py -v`

Expected: PASS with exact order, fill, fee, cash, holding, corporate-action and equity ledgers.

- [ ] **Step 5: Commit the backtest engine**

```bash
git add src/stock_quant/backtest tests/unit/test_corporate_action_accounting.py tests/unit/test_valuation.py tests/integration/test_backtest_engine.py tests/fixtures/synthetic_market
git commit -m "feat: add chronological portfolio backtest engine"
```

### Task 11: 正式研究Runner、运行状态与中断恢复

**Files:**
- Create: `src/stock_quant/research/runner.py`
- Create: `src/stock_quant/research/models.py`
- Create: `src/stock_quant/logging.py`
- Create: `tests/unit/test_logging.py`
- Create: `tests/integration/test_research_runner.py`

**Interfaces:**
- Consumes: `ExperimentSpec`, registry, dataset reader, factor, portfolio and backtest engine.
- Produces: `ResearchRunner.run(spec_path, stage_observer=None) -> PublishedExperiment`; `RunState`; complete experiment artifact set.

- [ ] **Step 1: Write failing frozen-data, artifact and failure-state tests**

```python
def test_runner_freezes_current_before_factor_execution(runner, current_switcher):
    experiment = runner.run("configs/experiments/momentum_60d.yml", stage_observer=current_switcher)
    assert experiment.manifest.dataset_version == current_switcher.original_version

def test_runner_publishes_complete_artifact_contract(runner):
    experiment = runner.run("configs/experiments/momentum_60d.yml")
    assert set(p.name for p in experiment.path.iterdir()) == REQUIRED_ARTIFACTS

def test_failure_keeps_previous_current_and_auditable_run(failing_runner):
    with pytest.raises(ResearchRunFailed):
        failing_runner.run("configs/experiments/momentum_60d.yml")
    assert failing_runner.latest_run_manifest().status == "FAILED"
    assert not failing_runner.partial_experiment_exists()

def test_structured_log_redacts_secrets(log_writer, capsys):
    log_writer.error(stage="FETCHING", source="tushare", message="token=secret-value")
    assert "secret-value" not in capsys.readouterr().out
    assert "[REDACTED]" in log_writer.path.read_text()
```

- [ ] **Step 2: Run the tests and confirm runner is missing**

Run: `pytest tests/unit/test_logging.py tests/integration/test_research_runner.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement state transitions and resumable run workspace**

Use data states `CREATED,FETCHING,RAW_SAVED,CLEANING,VALIDATING,PUBLISHED,FACTOR_READY,BACKTESTED,REPORTED,FAILED` and experiment states `CREATED,RUNNING,COMPLETED,ACCEPTED,REJECTED`. Each successful stage writes an input hash and completion record; rerunning the same `run_id` skips only stages whose recorded input hashes and artifacts still match. JSONL logs contain timestamp, severity, run ID, stage, source, symbol, event and message; redact configured secret values plus `token`, `authorization`, `api_key` key/value patterns before terminal or file output. Failures record stage, exception class, redacted message and retriable flag, return a nonzero CLI outcome, and never change data `CURRENT` or publish partial experiments.

- [ ] **Step 4: Implement orchestration and exact artifact set**

Resolve `CURRENT` once, freeze the validated spec, run readiness, factor, portfolio, three backtests, analytics and report generation. Produce `experiment_spec.yml`, `experiment_manifest.json`, `run_manifest.json`, `config_snapshot.yml`, `dataset_version.txt`, `factor_results.parquet`, `signals.parquet`, `target_positions.parquet`, `orders.parquet`, `fills.parquet`, `cash_ledger.parquet`, `corporate_action_ledger.parquet`, `daily_equity.parquet`, `metrics.json`, `report.html`. Record Git commit or `unversioned`, Python and dependency versions, dataset/file hashes, factor versions, cost scenarios, random seed, parent IDs and optional agent ID. Hash each artifact, evaluate engineering acceptance separately from performance, then call `ExperimentRegistry.publish`.

Run: `ruff check src tests && pytest tests/unit/test_logging.py tests/integration/test_research_runner.py -v`

Expected: PASS for success, `REJECTED`, repeat/reuse, CURRENT-switch and injected failure cases.

- [ ] **Step 5: Commit the official research path**

```bash
git add src/stock_quant/research/runner.py src/stock_quant/research/models.py src/stock_quant/logging.py tests/unit/test_logging.py tests/integration/test_research_runner.py
git commit -m "feat: orchestrate resumable reproducible research runs"
```

### Task 12: 绩效分析、数据质量报告与静态HTML

**Files:**
- Create: `src/stock_quant/analytics/performance.py`
- Create: `src/stock_quant/reporting/html.py`
- Create: `src/stock_quant/reporting/templates/experiment.html.j2`
- Create: `src/stock_quant/reporting/templates/quality.html.j2`
- Create: `tests/unit/test_performance.py`
- Create: `tests/integration/test_reports.py`

**Interfaces:**
- Consumes: quality report, benchmark prices and immutable experiment ledgers.
- Produces: `compute_metrics(daily_equity: pd.DataFrame, fills: pd.DataFrame, benchmark: pd.DataFrame) -> PerformanceMetrics`; `render_experiment_report(input: ExperimentReportInput, destination: Path) -> Path`; `render_quality_report(report: QualityReport, destination: Path) -> Path`.

- [ ] **Step 1: Write failing metric and report-content tests**

```python
def test_drawdown_turnover_and_cost_decomposition():
    metrics = compute_metrics(equity_fixture(), fills_fixture(), benchmark_fixture())
    assert metrics.max_drawdown == pytest.approx(-0.2)
    assert metrics.total_commission == 10
    assert metrics.total_stamp_tax == pytest.approx(0.5)
    assert metrics.turnover >= 0

def test_experiment_html_discloses_engineering_only_limit(tmp_path):
    path = render_experiment_report(report_input(), tmp_path / "report.html")
    html = path.read_text()
    assert "工程验证" in html
    assert "不代表策略具备实盘价值" in html
    assert "未成交订单" in html and "停牌资产比例" in html
```

- [ ] **Step 2: Run tests and verify absent analysis/report modules**

Run: `pytest tests/unit/test_performance.py tests/integration/test_reports.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement pure analytics without duplicated trading logic**

Calculate cumulative/annualized return, annualized volatility, maximum drawdown, benchmark excess, turnover, commission, stamp tax, slippage estimate, unfilled count, cash ratio, stale asset ratio and holding concentration strictly from saved ledgers. Metrics functions must not import factors, portfolio or execution modules and must handle fewer than 252 observations without dividing by zero.

- [ ] **Step 4: Implement self-contained static reports and run tests**

Quality HTML includes source/version status, raw/standard/quarantine counts, duplicate/missing/OHLC/unit issues, cross-source difference distributions, maximum differences, adjustment samples, current dataset version and gate decision. Experiment HTML overlays three cost scenarios and two benchmarks and includes drawdown, turnover, cost split, holdings, cash, unfilled orders, stale valuations, corporate-action ledger, experiment/data/code versions and all known limitations. Embed Plotly output so opening the HTML does not require a server; escape supplier/error text.

Run: `ruff check src tests && pytest tests/unit/test_performance.py tests/integration/test_reports.py -v`

Expected: PASS and generated fixture reports contain no external data requests or secrets.

- [ ] **Step 5: Commit analysis and reports**

```bash
git add src/stock_quant/analytics src/stock_quant/reporting tests/unit/test_performance.py tests/integration/test_reports.py
git commit -m "feat: report quality and portfolio performance"
```

### Task 13: CLI、Notebook薄界面与离线端到端验收

**Files:**
- Create: `src/stock_quant/cli.py`
- Create: `src/stock_quant/__main__.py`
- Create: `src/stock_quant/data_pipeline.py`
- Create: `notebooks/01_momentum_baseline.ipynb`
- Create: `tests/integration/test_cli.py`
- Create: `tests/integration/test_data_pipeline.py`
- Create: `tests/integration/test_end_to_end.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: all prior public interfaces.
- Produces: `DataPipeline.update(request: DataUpdateRequest) -> DataUpdateResult`; `DataPipeline.validate(version: str) -> QualityReport`; `resolve_latest_complete_date(status: SourceCoverage, calendar: TradingCalendar, publication_time: time) -> date`; `python -m stock_quant data update`; `data validate`; `research run --spec`; debug-only `backtest momentum_60d`; `report build`.

- [ ] **Step 1: Write failing CLI and complete offline acceptance tests**

```python
def test_official_research_command_returns_zero_and_prints_identity(cli_runner, fixture_root):
    result = cli_runner.invoke(app, ["research", "run", "--spec", "configs/experiments/momentum_60d.yml", "--root", str(fixture_root)])
    assert result.exit_code == 0
    assert "experiment_id=" in result.stdout

def test_partial_failure_returns_nonzero(cli_runner, broken_fixture_root):
    result = cli_runner.invoke(app, ["research", "run", "--spec", "configs/experiments/momentum_60d.yml", "--root", str(broken_fixture_root)])
    assert result.exit_code != 0
    assert "FAILED" in result.stdout

def test_cached_end_to_end_run_is_reproducible(fixture_root):
    first = run_offline_fixture(fixture_root)
    second = run_offline_fixture(fixture_root)
    assert first.experiment_id == second.experiment_id
    assert first.manifest.artifact_hashes == second.manifest.artifact_hashes
```

- [ ] **Step 2: Run tests and confirm the CLI is absent**

Run: `pytest tests/integration/test_cli.py tests/integration/test_end_to_end.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement the data pipeline and latest-complete-date resolver**

`DataPipeline.update` builds idempotent requests, saves every raw response before normalization, runs cross-source quality checks, renders the quality report and, only on `PASS`, calls `DatasetPublisher`. It always returns `DataUpdateResult(quality_report, dataset_ref | None, run_id)`, so a blocked dataset remains diagnosable without publication. Tushare unadjusted daily data, BaoStock adjusted data, both AKShare benchmarks and held-period corporate actions are required for their declared use; extra BaoStock/AKShare stock cross-check failures are warnings. `resolve_latest_complete_date` requires both benchmarks, expected Tushare stock coverage, at least one updated validation source and passage of the configured publication time; otherwise it walks backward over confirmed trading days. Explicit `--end YYYY-MM-DD` bypasses discovery but still undergoes quality checks. Unit-test the fallback, explicit end date, a late validation source, each required/optional source failure and blocked publication with recorded adapter responses.

- [ ] **Step 4: Implement Typer commands with safe summaries and exit codes**

Commands call application services only. `data update` fetches/saves/normalizes/validates/publishes; `data validate` checks a specified or current dataset; `research run` is the only command that publishes formal experiments; direct `backtest momentum_60d` writes only to a run/debug directory; `report build` rebuilds display from existing immutable artifacts without recomputing factors or trades. Catch known domain exceptions, print run ID/stage/redacted cause, and exit nonzero. Never print environment variables or raw supplier responses.

- [ ] **Step 5: Build the thin Notebook and document exact operator workflow**

The Notebook may load `DatasetReader`, `FactorResult`, experiment metrics and report paths, but contains no momentum formula, ranking, fee, execution or accounting code. README documents Conda creation, setting `TUSHARE_TOKEN` locally, offline tests, external/smoke opt-in, the five CLI commands, data directories, experiment immutability, recovery by run ID and the explicit warning that the MVP is not investment advice or evidence of alpha.

- [ ] **Step 6: Run the full verification suite**

Run: `ruff check . && pytest --cov=stock_quant --cov-report=term-missing`

Expected: all offline unit/integration/golden/no-lookahead tests pass; no `external` or `smoke` test runs by default; coverage report has no untested core branch in quality gates, experiment identity, costs, T+1 or corporate-action accounting.

Run: `python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root tests/fixtures/project`

Expected: exit code 0, one immutable experiment directory, a complete artifact manifest, an HTML report and the same experiment ID on the second invocation.

- [ ] **Step 7: Commit the complete offline MVP interface**

```bash
git add src/stock_quant/cli.py src/stock_quant/__main__.py src/stock_quant/data_pipeline.py notebooks/01_momentum_baseline.ipynb tests/integration/test_cli.py tests/integration/test_data_pipeline.py tests/integration/test_end_to_end.py README.md
git commit -m "feat: deliver reproducible phase-one quant workflow"
```

### Task 14: 用户本地外部契约与真实数据冒烟验收

**Files:**
- Create: `tests/external/test_live_source_contracts.py`
- Create: `tests/smoke/test_small_market_download.py`
- Create: `docs/operations/phase-one-validation.md`

**Interfaces:**
- Consumes: installed supplier SDKs, local `TUSHARE_TOKEN`, source adapters and CLI.
- Produces: an operator-owned validation record; no committed market data or credentials.

- [ ] **Step 1: Write opt-in tests with explicit skip conditions**

```python
@pytest.mark.external
@pytest.mark.skipif(not os.getenv("TUSHARE_TOKEN"), reason="TUSHARE_TOKEN is required")
def test_tushare_daily_live_contract():
    result = TushareSource().fetch(two_day_request("600000.SH"))
    assert not result.frame.empty
    assert {"ts_code", "trade_date", "open", "high", "low", "close"} <= set(result.frame.columns)

@pytest.mark.smoke
def test_three_sources_can_build_small_quality_report(configured_sources, tmp_path):
    pipeline = DataPipeline(root=tmp_path, sources=configured_sources)
    outcome = pipeline.update(small_request(symbols=("600000.SH",), trading_days=5))
    assert outcome.quality_report.source_status.keys() >= {"tushare", "akshare", "baostock"}
    assert outcome.quality_report.decision in {"PASS", "BLOCK"}
    assert (outcome.dataset_ref is not None) == (outcome.quality_report.decision == "PASS")
```

- [ ] **Step 2: Confirm default pytest excludes the live tests**

Run: `pytest --collect-only -q`

Expected: external and smoke tests are collected but deselected by default marker expression.

- [ ] **Step 3: Add the operations checklist**

Document commands `pytest -m external`, `pytest -m smoke`, `data update`, `data validate` and `research run`; require checking the latest-complete-date rule, source row counts, quarantine reasons, cross-source maximum differences, adjustment spot checks, corporate-action conflicts, two benchmark coverage and secret scan before accepting a live dataset. Record outcomes by dataset and experiment IDs, never by copying market files into Git.

- [ ] **Step 4: Run only safe local verification unless credentials are intentionally present**

Run: `ruff check . && pytest`

Expected: all offline tests pass and live tests remain deselected. If the user has intentionally configured the services, separately run `pytest -m external -v` and then `pytest -m smoke -v`; a quality `BLOCK` is a valid diagnostic result, while a schema/authentication exception fails the test.

- [ ] **Step 5: Commit validation harness without generated data**

```bash
git add tests/external tests/smoke docs/operations/phase-one-validation.md
git commit -m "test: add opt-in live data validation harness"
```

## Final Acceptance Gate

- [ ] Run `git status --short` and confirm only intentionally ignored local secrets/data remain.
- [ ] Run `ruff check .` and confirm exit code 0.
- [ ] Run `pytest --cov=stock_quant --cov-report=term-missing` and confirm all offline tests pass.
- [ ] Run the cached fixture research command twice and confirm identical experiment IDs and artifact hashes.
- [ ] Time the cached 30-stock fixture run with `/usr/bin/time -f '%e' python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root tests/fixtures/project`; record and investigate if elapsed time exceeds 600 seconds on the target personal computer.
- [ ] Inspect one quality HTML and one experiment HTML for the required provenance, limitations, three cost scenarios and two benchmarks.
- [ ] Confirm `git grep -nE '(TUSHARE_TOKEN=.{8,}|[A-Za-z0-9]{32,})' -- . ':!docs/superpowers'` finds no credential-like committed value; manually review any match before proceeding.
- [ ] Confirm no claim of strategy profitability or live readiness appears in README, Notebook or reports.
