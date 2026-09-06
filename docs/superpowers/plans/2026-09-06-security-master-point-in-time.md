# Security-Master Point-in-Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Use focused tests per task and run the entire suite once at the end.

**Goal:** Replace the unified bootstrap listing placeholder (`SYNTHETIC_LIST_DATE = 2018-01-02`) with real, traceable listing/delisting dates and a snapshot status, refreshed from a live whole-market tushare `stock_basic` snapshot during every `data update`, gated at RESEARCH freeze by a per-symbol `security_master_coverage` evidence table (row presence = VERIFIED).

**Architecture:** `security_master` gains one `list_status` column (`L`/`D`/`P`/`NOT_APPLIED`); a new minimal evidence table `security_master_coverage` (row presence only — no status/reason columns) is published atomically beside the master whenever `data update` reconciles a real tushare `stock_basic` snapshot. Bootstrap seeds become NaT dates + `NOT_APPLIED` with no coverage table (they can never pass a freeze). ResearchRunner rejects in RESEARCH mode before any stage when any universe symbol lacks a coverage row; ENGINEERING always proceeds as a diagnostic. Two new quality checks join the existing matrix: a fact-vs-bar boundary WARNING (update + validate) and a coverage↔master consistency FATAL (validate only).

**Tech Stack:** Python 3.10, pandas, PyArrow/Parquet, DuckDB, tushare Pro SDK, pytest.

**Spec:** docs/superpowers/specs/2026-09-06-security-master-point-in-time-design.md

## Global Constraints

- Scope is tushare-only for master facts this phase; akshare/baostock master cross-checks are out of scope (P1 #4).
- ST/risk-warning *historical* series are out of scope; `list_status` is a snapshot status only.
- The engineering universe stays fixed: 30 sample equities + two benchmark indices.
- `name` / `exchange` / `board` remain labels from `configs/universe.yml` — never overwritten by supplier values.
- `security_master_coverage` row presence **is** VERIFIED: no `status`/`reason` columns, no sentinel-date detection logic anywhere.
- No new trust mode and no new metrics key: reuse `DataTrustMode` (RESEARCH/ENGINEERING) and write freeze failures into the run failure reason.
- `stock_basic` is a required step: a fetch failure or a snapshot missing any universe symbol is a FATAL issue and blocks that update's publication (same atomic "no publish on gate failure" semantics as today).
- Offline tests never touch the network or read a token; `external`/`smoke` stay deselected by default.
- Tests stay minimal and non-redundant (no re-testing of `momentum._MIN_LISTED_DAYS` internals; exactly one new-IPO acceptance fixture).

## Efficiency Rules

- Preserve uncommitted work; stage only files owned by a task.
- Reuse existing fixture builders and helper vocabulary; do not create a parallel fixture framework.
- Per task: add the related failures, run one focused selection, implement, rerun the same selection once.
- Run `pytest -q` and `ruff check src tests && ruff format --check src tests` only at the final checkpoint.
- A task is green only when its focused selection passes; each task ends with one commit.

---

### Task 1: Security-master coverage evidence model + schema + registration

**Files:**
- Create: `src/stock_quant/data_model/security_master.py`, `tests/unit/test_security_master_coverage.py`
- Modify: `src/stock_quant/data_model/schemas.py`, `src/stock_quant/data_model/dataset.py:37-56`

**Produces:** `ListStatus` enum (`L`/`D`/`P`/`NOT_APPLIED`); `MASTER_SOURCE_STOCK_BASIC = "tushare.stock_basic"`; `master_coverage_record(...)` → one canonical dict row; `master_coverage_frame(records)` → deterministic standardized frame; `missing_master_coverage_symbols(symbols, coverage)` → sorted unverified symbols; canonical `SECURITY_MASTER_COVERAGE_COLUMNS` / `SECURITY_MASTER_COVERAGE_SCHEMA`; table registered in `STANDARDIZED_SCHEMAS`.

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_security_master_coverage.py`:

```python
"""Security-master coverage evidence rows (point-in-time task).

Row presence is VERIFIED for the research freeze; there is deliberately no
status/reason vocabulary on this table (unlike the corporate-action coverage
table).  The frame must be deterministic: fixed columns, co-erced dates, rows
sorted by symbol.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.data_model.schemas import SECURITY_MASTER_COVERAGE_COLUMNS
from stock_quant.data_model.security_master import (
    MASTER_SOURCE_STOCK_BASIC,
    ListStatus,
    master_coverage_frame,
    master_coverage_record,
    missing_master_coverage_symbols,
)


def test_frame_has_canonical_columns_and_sorts_by_symbol():
    frame = master_coverage_frame(
        [
            master_coverage_record(
                "600001.SH", list_date=date(2019, 6, 1),
                list_status=ListStatus.L,
            ),
            master_coverage_record(
                "600000.SH", list_date=date(2018, 1, 2),
                list_status=ListStatus.L,
                snapshot_sha256="ab" * 32,
                sdk_version="1.0.0",
                checked_at=pd.Timestamp("2026-09-05T08:00:00Z"),
            ),
        ]
    )
    assert list(frame.columns) == SECURITY_MASTER_COVERAGE_COLUMNS
    assert frame["symbol"].tolist() == ["600000.SH", "600001.SH"]
    assert frame["list_status"].tolist() == ["L", "L"]
    assert frame.loc[0, "source"] == MASTER_SOURCE_STOCK_BASIC
    assert frame.loc[0, "snapshot_sha256"] == "ab" * 32


def test_missing_symbols_cover_none_empty_and_partial_evidence():
    covered = master_coverage_frame(
        [master_coverage_record("600000.SH", list_date=date(2018, 1, 2))]
    )
    empty = master_coverage_frame([])
    assert missing_master_coverage_symbols({"600000.SH"}, None) == ["600000.SH"]
    assert missing_master_coverage_symbols({"600000.SH"}, empty) == ["600000.SH"]
    assert missing_master_coverage_symbols(
        {"600000.SH", "600001.SH"}, covered
    ) == ["600001.SH"]
    assert missing_master_coverage_symbols({"600000.SH"}, covered) == []


def test_coverage_table_is_registered_for_publication():
    assert "security_master_coverage" in STANDARDIZED_SCHEMAS
```

- [ ] **Step 2: Verify failure once**

Run: `pytest tests/unit/test_security_master_coverage.py -v`

Expected: FAIL — `security_master` module does not exist (import error); the registered-table assertion fails.

- [ ] **Step 3: Implement**

Add to `src/stock_quant/data_model/schemas.py` (after `CORPORATE_ACTION_COVERAGE_COLUMNS`):

```python
# Security-master coverage evidence records one VERIFIED row per universe
# symbol after a real tushare stock_basic refresh.  Row presence is the whole
# vocabulary (no status/reason): a symbol with a row had its listing facts
# confirmed against a live snapshot; an empty or absent table is unverified.
SECURITY_MASTER_COVERAGE_COLUMNS = [
    "symbol",
    "list_date",
    "delist_date",
    "list_status",
    "source",
    "snapshot_sha256",
    "sdk_version",
    "checked_at",
]
```

Add to `_security_master_fields()` an appended trailing field and add a new fields function + schema (after `_security_master_fields` / `SECURITY_MASTER_SCHEMA`):

```python
def _security_master_coverage_fields() -> list[pa.Field]:
    return [
        pa.field("symbol", pa.string()),
        pa.field("list_date", pa.date32()),
        pa.field("delist_date", pa.date32()),
        pa.field("list_status", pa.string()),
        pa.field("source", pa.string()),
        pa.field("snapshot_sha256", pa.string()),
        pa.field("sdk_version", pa.string()),
        pa.field("checked_at", pa.timestamp("us", tz="UTC")),
    ]
```

```python
SECURITY_MASTER_COVERAGE_SCHEMA = pa.schema(_security_master_coverage_fields())
```

Create `src/stock_quant/data_model/security_master.py`:

```python
"""Security-master listing-fact coverage evidence (point-in-time task).

Formal research requires one ``security_master_coverage`` row per universe
symbol: row presence *is* VERIFIED — the listing facts were applied from a real
tushare ``stock_basic`` snapshot during ``data update``.  There is no
status/reason vocabulary here (unlike the corporate-action coverage table) and
no sentinel-date detection; an empty or absent table simply means every symbol
is unverified.

- ``ListStatus`` — ``L`` (listed) / ``D`` (delisted) / ``P`` (suspended) /
  ``NOT_APPLIED`` (bootstrap seed, real facts not yet applied).
- ``master_coverage_record`` / ``master_coverage_frame`` — render rows into the
  standardized ``SECURITY_MASTER_COVERAGE_COLUMNS`` layout (dates and
  ``checked_at`` co-erced deterministically, rows sorted by symbol).
- ``missing_master_coverage_symbols`` — the sorted universe symbols with no row,
  used by the RESEARCH freeze gate.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from stock_quant.data_model.schemas import SECURITY_MASTER_COVERAGE_COLUMNS

#: The only source this phase records security-master listing facts from.
MASTER_SOURCE_STOCK_BASIC = "tushare.stock_basic"


class ListStatus(str, Enum):
    L = "L"
    D = "D"
    P = "P"
    NOT_APPLIED = "NOT_APPLIED"


def master_coverage_record(
    symbol: str,
    *,
    list_date: Any = None,
    delist_date: Any = None,
    list_status: ListStatus | str | None = None,
    source: str = MASTER_SOURCE_STOCK_BASIC,
    snapshot_sha256: str | None = None,
    sdk_version: str | None = None,
    checked_at: Any = None,
) -> dict[str, Any]:
    """Return one coverage row holding the eight canonical columns."""
    return {
        "symbol": str(symbol),
        "list_date": list_date,
        "delist_date": delist_date,
        "list_status": _code_text(list_status) or ListStatus.L.value,
        "source": source or MASTER_SOURCE_STOCK_BASIC,
        "snapshot_sha256": snapshot_sha256,
        "sdk_version": sdk_version,
        "checked_at": checked_at,
    }


def master_coverage_frame(
    records: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    """Build the standardized coverage frame from coverage rows.

    Date columns are co-erced to ``datetime64`` (Arrow casts them to ``date32``
    at publish) and ``checked_at`` to timezone-aware UTC; rows are sorted by
    symbol so identical inputs always produce identical bytes.
    """
    frame = pd.DataFrame(records, columns=SECURITY_MASTER_COVERAGE_COLUMNS)
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    frame["delist_date"] = pd.to_datetime(frame["delist_date"], errors="coerce")
    frame["checked_at"] = pd.to_datetime(
        frame["checked_at"], utc=True, errors="coerce"
    )
    frame = frame.sort_values(["symbol"], kind="stable").reset_index(drop=True)
    return frame[SECURITY_MASTER_COVERAGE_COLUMNS]


def missing_master_coverage_symbols(
    symbols: Iterable[str],
    coverage: pd.DataFrame | None,
) -> list[str]:
    """Return the sorted universe symbols with no coverage row.

    ``coverage`` ``None`` (older dataset without the table), a non-frame or an
    empty frame reads as *no* evidence, so every symbol is unverified.
    """
    if coverage is None or not isinstance(coverage, pd.DataFrame):
        return sorted({str(item) for item in symbols})
    covered = set(str(value) for value in coverage.get("symbol", []))
    return sorted({str(item) for item in symbols} - covered)


def _code_text(value: Any) -> str | None:
    """Render an enum member or code string as its stable code text."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    return str(value)
```

Register the table in `src/stock_quant/data_model/dataset.py` — add `SECURITY_MASTER_COVERAGE_SCHEMA` to the `from stock_quant.data_model.schemas import (...)` block and a `"security_master_coverage": SECURITY_MASTER_COVERAGE_SCHEMA,` entry to the `STANDARDIZED_SCHEMAS` dict (line 50-56).

- [ ] **Step 4: Verify once, commit**

Run: `pytest tests/unit/test_security_master_coverage.py -v`

Expected: PASS.

```bash
git add src/stock_quant/data_model/security_master.py \
        src/stock_quant/data_model/schemas.py \
        src/stock_quant/data_model/dataset.py \
        tests/unit/test_security_master_coverage.py
git commit -m "feat: security-master coverage evidence model and schema"
```

---

### Task 2: `list_status` column + empty bootstrap seed + fixture ripple

**Files:**
- Modify: `src/stock_quant/data_model/schemas.py` (`SECURITY_MASTER_COLUMNS`, `_security_master_fields()`), `src/stock_quant/bootstrap.py:22,90-104`, `tests/integration/conftest.py:155-195,246-262`, `tests/integration/test_research_runner.py:160-174,238-268`, `tests/smoke/test_small_market_download.py:120-130`, `tests/integration/test_cli.py:120-134`

**Consumes:** `ListStatus`, `MASTER_SOURCE_STOCK_BASIC`, `master_coverage_record`, `master_coverage_frame`, `SECURITY_MASTER_COVERAGE_*` (Task 1).
**Produces:** `security_master` published with 7 columns everywhere; bootstrap seeds with `list_date=NaT`, `delist_date=NaT`, `list_status="NOT_APPLIED"` and **no** `security_master_coverage` table; every test fixture dataset publishes one deterministic `security_master_coverage` row per universe symbol (`broken=True` publishes an empty table).

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_cli.py` (replace the tail of `test_data_bootstrap_publishes_initial_dataset` with the stronger assertion body):

```python
def test_data_bootstrap_publishes_initial_dataset(cli_runner, tmp_path):
    """Bootstrap creates the baseline tables required by the first update."""
    root = tmp_path / "seed-project"
    configs = root / "configs"
    configs.mkdir(parents=True)
    repo_configs = Path(__file__).resolve().parents[2] / "configs"
    for name in ("project.yml", "universe.yml"):
        shutil.copy(repo_configs / name, configs / name)

    result = cli_runner.invoke(app, ["data", "bootstrap", "--root", str(root)])

    assert result.exit_code == 0, result.stdout
    assert "published seed dataset:" in result.stdout
    assert "CURRENT ->" in result.stdout
    assert (root / "data" / "standardized" / "CURRENT").is_file()

    # The seed must be an honest empty placeholder: NaT listing dates and
    # NOT_APPLIED status, no security_master_coverage evidence table.
    from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader

    version = DatasetPublisher(root).current().version
    with DatasetReader(root).open(version) as context:
        assert "security_master_coverage" not in context.tables
        master = context.read("security_master")
    assert set(master["list_status"]) == {"NOT_APPLIED"}
    assert master["list_date"].isna().all()
    assert master["delist_date"].isna().all()
```

Add to `tests/integration/test_cli.py` (guards the default shared fixture keeps working once the RESEARCH gate lands in Task 6):

```python
def test_fixture_dataset_carries_master_coverage_rows(fixture_root):
    """The default fixture publishes one security_master_coverage row per symbol."""
    from stock_quant.data_model.dataset import DatasetReader

    with DatasetReader(fixture_root.root).open(fixture_root.version) as context:
        master = context.read("security_master")
        coverage = context.read("security_master_coverage")
    assert set(master["list_status"]) == {"L"}
    assert not coverage.empty
    assert set(coverage["symbol"]) == set(master["symbol"])
    assert set(coverage["list_status"]) == {"L"}
```

- [ ] **Step 2: Verify failure once**

Run: `pytest tests/integration/test_cli.py -k 'bootstrap or master_coverage' -v`

Expected: FAIL — the seed still writes the synthetic 2018-01-02 list date, master has no `list_status` column, and fixtures publish no `security_master_coverage` table.

- [ ] **Step 3: Implement the ripple**

`src/stock_quant/data_model/schemas.py` — append `"list_status"` to `SECURITY_MASTER_COLUMNS` (after `"delist_date"`) and append `pa.field("list_status", pa.string())` to `_security_master_fields()`.

`src/stock_quant/bootstrap.py` — delete the `SYNTHETIC_LIST_DATE = date(2018, 1, 2)` constant and rewrite `_security_master`:

```python
def _security_master(universe: Universe) -> pd.DataFrame:
    entries = universe.entries
    count = len(entries)
    return pd.DataFrame(
        {
            "symbol": [entry.symbol for entry in entries],
            "name": [entry.name_at_selection for entry in entries],
            "exchange": [entry.exchange for entry in entries],
            "board": [entry.board for entry in entries],
            "list_date": pd.Series(pd.NaT, index=range(count), dtype="datetime64[ns]"),
            "delist_date": pd.Series(pd.NaT, index=range(count), dtype="datetime64[ns]"),
            "list_status": ["NOT_APPLIED"] * count,
        }
    )[SECURITY_MASTER_COLUMNS]
```

Add a `ListStatus` import to `bootstrap.py`. (The `datetime` import stays; `date`/`timedelta` are still used by `_calendar_days`.)

`tests/integration/conftest.py` — extend the module imports:

```python
from stock_quant.data_model.security_master import (
    MASTER_SOURCE_STOCK_BASIC,
    ListStatus,
    master_coverage_frame,
    master_coverage_record,
)
```

Extend `build_fixture_project` so the tables dict becomes:

```python
    universe = Universe.from_yaml(config_dir / "universe.yml")
    sessions = _weekdays(BARS_START, BARS_END)
    tables = {
        "daily_bar": _bars(sessions, universe),
        "security_master": _security_master(universe),
        "security_master_coverage": _master_coverage_table(universe, present=not broken),
        "corporate_action": _corporate_action(),
        "corporate_action_coverage": _coverage_table(universe, trusted=not broken),
        "trading_calendar": _trading_calendar(),
    }
```

Rewrite `_security_master` to carry `list_status`:

```python
def _security_master(universe: Universe) -> pd.DataFrame:
    entries = universe.entries
    n = len(entries)
    return pd.DataFrame(
        {
            "symbol": [entry.symbol for entry in entries],
            "name": [
                f"{entry.name_at_selection}_{entry.symbol}" for entry in entries
            ],
            "exchange": [entry.exchange for entry in entries],
            "board": [entry.board for entry in entries],
            "list_date": pd.to_datetime([LIST_DATE] * n),
            "delist_date": pd.Series(
                pd.NaT, index=range(n), dtype="datetime64[ns]"
            ),
            "list_status": [ListStatus.L.value] * n,
        }
    )[SECURITY_MASTER_COLUMNS]
```

Add the coverage builder next to `_coverage_table`:

```python
def _master_coverage_table(universe: Universe, *, present: bool) -> pd.DataFrame:
    """One deterministic security_master_coverage row per universe symbol.

    Row presence is the whole evidence vocabulary: ``present=True`` publishes a
    row per symbol at the master's listing facts (the RESEARCH master-evidence
    gate accepts the dataset); ``present=False`` (the ``broken`` fixture)
    publishes an empty table so a RESEARCH run is rejected for missing master
    evidence while an ENGINEERING diagnostic may still replay.
    """
    if not present:
        return master_coverage_frame([])
    records = [
        master_coverage_record(
            entry.symbol,
            list_date=LIST_DATE,
            list_status=ListStatus.L,
            source=MASTER_SOURCE_STOCK_BASIC,
            snapshot_sha256="f" * 64,
            sdk_version="fixture",
            checked_at=_INGESTED,
        )
        for entry in universe.entries
    ]
    return master_coverage_frame(records)
```

`tests/integration/test_research_runner.py` — add the `list_status` column and a default master-coverage table. Extend imports with the Task 1 helpers (`ListStatus`, `MASTER_SOURCE_STOCK_BASIC`, `master_coverage_frame`, `master_coverage_record`), add `"list_status"` to the `_security_master` dict (all `"L"`), and rewrite `_publish_synthetic_dataset` so a default master coverage is always present:

```python
def _master_coverage(symbols: tuple[str, ...]) -> pd.DataFrame:
    """One deterministic security_master_coverage row per symbol."""
    records = [
        master_coverage_record(
            symbol,
            list_date=_LIST_DATE,
            list_status=ListStatus.L,
            source=MASTER_SOURCE_STOCK_BASIC,
            snapshot_sha256="f" * 64,
            sdk_version="fixture",
            checked_at=_INGESTED,
        )
        for symbol in sorted(symbols)
    ]
    return master_coverage_frame(records)
```

```python
def _publish_synthetic_dataset(
    project_root: Path,
    *,
    index_close: tuple[float, float] = (4000.0, 2000.0),
    limit_locked_symbols: tuple[str, ...] = (),
    coverage: pd.DataFrame | None = None,
    master_coverage: pd.DataFrame | None = None,
    fresh: tuple[str, date, float] | None = None,
) -> str:
    """Publish the synthetic market under ``project_root``; return its version.

    The default dataset carries an explicit ``VERIFIED_EMPTY`` corporate-action
    coverage row per symbol AND a ``security_master_coverage`` row per symbol
    (both evidence tables), so the ResearchRunner's RESEARCH gates pass over the
    default market.  ``fresh`` optionally adds one recently-listed symbol
    ``(symbol, list_date, growth)`` whose bars begin at its list date (the
    new-IPO acceptance fixture); ``master_coverage`` overrides the evidence
    (an empty frame publishes an empty table that fails the master gate).
    """
    master = _security_master(fresh)
    symbols = tuple(master["symbol"])
    if coverage is None:
        coverage = _coverage(symbols, status=CoverageStatus.VERIFIED_EMPTY)
    if master_coverage is None:
        master_coverage = _master_coverage(symbols)
    tables = {
        "daily_bar": _bars(
            _weekdays(_BARS_START, _BARS_END),
            index_close=index_close,
            limit_locked_symbols=limit_locked_symbols,
            fresh=fresh,
        ),
        "security_master": master,
        "security_master_coverage": master_coverage,
        "corporate_action": _corporate_action(),
        "corporate_action_coverage": coverage,
        "trading_calendar": _trading_calendar(),
    }
    return DatasetPublisher(project_root).publish(tables, QualityReport()).version
```

Supporting helper changes in the same file. The new `_security_master` appends the fresh symbol with a real `list_date` while every row keeps `list_status="L"`; the extended `_bars` starts the fresh symbol's bars at its list date (the existing 12-symbol market is untouched when `fresh` is `None`):

```python
def _security_master(
    fresh: tuple[str, date, float] | None = None,
) -> pd.DataFrame:
    """One security_master row per EQUITY_GROWTH symbol, plus ``fresh``.

    ``fresh`` is ``(symbol, list_date, growth)`` for the new-IPO acceptance
    fixture; every row keeps ``list_status="L"``.  ``name`` stays a synthetic
    label (universe.yml owns the real labels, as in production).
    """
    symbols = [symbol for symbol, _ in EQUITY_GROWTH]
    list_dates = [_LIST_DATE] * len(symbols)
    if fresh is not None:
        symbols = symbols + [fresh[0]]
        list_dates = list_dates + [fresh[1]]
    count = len(symbols)
    return pd.DataFrame(
        {
            "symbol": symbols,
            "name": [f"synth_{symbol}" for symbol in symbols],
            "exchange": ["SH"] * count,
            "board": ["sh_main"] * count,
            "list_date": pd.to_datetime(list_dates),
            "delist_date": pd.Series(
                pd.NaT, index=range(count), dtype="datetime64[ns]"
            ),
            "list_status": ["L"] * count,
        }
    )[SECURITY_MASTER_COLUMNS]


def _bars(
    sessions: list[date],
    *,
    index_close: tuple[float, float],
    limit_locked_symbols: tuple[str, ...] = (),
    fresh: tuple[str, date, float] | None = None,
) -> pd.DataFrame:
    n = len(sessions)
    frames: list[pd.DataFrame] = []
    for symbol, growth in EQUITY_GROWTH:
        closes = [_BASE_PRICE * math.exp(growth * (index - (n - 1)))
                  for index in range(n)]
        if symbol in limit_locked_symbols:
            opens = list(closes)
            for index in range(1, n):
                if sessions[index].weekday() == 0:  # Monday == execution day
                    opens[index] = 0.5 * closes[index - 1]
            frames.append(_instrument_frame(sessions, symbol, closes, opens))
        else:
            frames.append(_instrument_frame(sessions, symbol, closes))
    if fresh is not None:
        symbol, list_date, growth = fresh
        start_at = next(
            index for index, day in enumerate(sessions) if day >= list_date
        )
        closes = [
            _BASE_PRICE * math.exp(growth * (index - (n - 1)))
            for index in range(start_at, n)
        ]
        frames.append(_instrument_frame(sessions[start_at:], symbol, closes))
    for symbol, level in zip(_BENCHMARK_SYMBOLS, index_close):
        frames.append(_instrument_frame(sessions, symbol, [level] * n))
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]
```

`_universe_symbols()` stays 12-symbol; the default-coverage helpers now derive symbols from `master["symbol"]` (as in the `_publish_synthetic_dataset` above) so a fresh symbol is always covered when present.

`tests/smoke/test_small_market_download.py` — add `"list_status"` to the `_security_master` dict with a single `"L"` (the smoke baseline stays a four-table dataset; the live update adds the coverage table).

`tests/integration/conftest.py` docstring for `build_fixture_project`/`broken` — note that `broken=True` keeps the security-master coverage table *empty* (as well as CA coverage UNTRUSTED) so the RESEARCH master-evidence gate rejects it.

- [ ] **Step 4: Verify once, commit**

Run: `pytest tests/integration/test_cli.py tests/integration/test_data_pipeline.py tests/integration/test_research_runner.py -q`

Expected: PASS (all existing update/runner tests still pass over the 7-column master and the new default evidence table; the RESEARCH master gate does not exist yet).

```bash
git add src/stock_quant/data_model/schemas.py src/stock_quant/bootstrap.py \
        tests/integration/conftest.py tests/integration/test_research_runner.py \
        tests/smoke/test_small_market_download.py tests/integration/test_cli.py
git commit -m "feat: master list_status, honest bootstrap seed, fixture evidence rows"
```

---

### Task 3: Tushare `stock_basic` whole-market adapter endpoint

**Files:**
- Modify: `src/stock_quant/data_sources/tushare.py`, `tests/integration/test_source_contracts.py`, `tests/external/test_live_source_contracts.py`
- Create: `tests/fixtures/tushare_stock_basic.csv`

**Consumes:** `DataRequest`, `FetchResult`, `request_key`, `request_metadata`, `ContractError`, `translate_supplier_error`, `_utc_timestamp` (all from `data_sources/base.py`).
**Produces:** `TushareSource.fetch` accepts `endpoint == "stock_basic"` with an empty symbol tuple, calls `self._client.stock_basic(fields="ts_code,name,exchange,list_date,delist_date,list_status")`, validates the whole-market raw frame (`_validate_stock_basic`), and returns a `FetchResult` with `metadata["supplier_endpoint"] == "tushare.pro.stock_basic"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/fixtures/tushare_stock_basic.csv`:

```csv
ts_code,name,list_date,delist_date,list_status
600000.SH,浦发银行,19991110,,L
000001.SZ,平安银行,19910403,,L
```

Add a `stock_basic` method to the offline `TushareClient` in `tests/integration/test_source_contracts.py`:

```python
    def stock_basic(self, **_: str) -> pd.DataFrame:
        if isinstance(self.frame, Exception):
            raise self.frame
        return self.frame
```

Add the two tests (they reuse `_request`'s `DataRequest` helper — the stock_basic request must be whole-market with no symbols):

```python
def test_tushare_stock_basic_returns_whole_market_native_columns(monkeypatch):
    """stock_basic is a whole-market reference: empty symbols, native columns."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    frame = pd.read_csv(FIXTURES / "tushare_stock_basic.csv", dtype={"list_date": str})
    request = DataRequest("stock_basic", (), date(2020, 1, 1), date(2020, 1, 2))

    result = TushareSource(SourceConfig(), TushareClient(frame)).fetch(request)

    assert result.endpoint == "stock_basic"
    assert result.frame.columns.tolist() == [
        "ts_code", "name", "list_date", "delist_date", "list_status"
    ]
    assert result.metadata["supplier_endpoint"] == "tushare.pro.stock_basic"


def test_tushare_stock_basic_rejects_symbol_scoped_request(monkeypatch):
    """A symbol-scoped stock_basic request is a caller bug, not a valid query."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    frame = pd.read_csv(FIXTURES / "tushare_stock_basic.csv", dtype={"list_date": str})
    client = TushareClient(frame)
    request = DataRequest("stock_basic", ("600000.SH",), date(2020, 1, 1), date(2020, 1, 2))

    with pytest.raises(ValueError, match="whole-market"):
        TushareSource(SourceConfig(), client).fetch(request)
```

Add a live contract test to `tests/external/test_live_source_contracts.py` next to `test_tushare_daily_live_contract`:

```python
@pytest.mark.external
@pytest.mark.skipif(
    find_spec("tushare") is None or not os.getenv("TUSHARE_TOKEN"),
    reason="TUSHARE_TOKEN is required",
)
def test_tushare_stock_basic_live_contract() -> None:
    """The live whole-market reference carries the master facts columns."""
    start, end = _recent_window()
    request = DataRequest("stock_basic", (), start, end, {})
    result = TushareSource(SourceConfig()).fetch(request)
    assert not result.frame.empty
    required = {"ts_code", "name", "list_date", "delist_date", "list_status"}
    missing = sorted(required - set(result.frame.columns))
    assert not missing, f"live stock_basic response is missing columns: {missing}"
```

- [ ] **Step 2: Verify failure once**

Run: `pytest tests/integration/test_source_contracts.py -k stock_basic -v`

Expected: FAIL — the adapter rejects `stock_basic` as an unsupported endpoint.

- [ ] **Step 3: Implement**

`src/stock_quant/data_sources/tushare.py` — route `stock_basic` before the daily guard and add the two methods:

```python
    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint == "stock_basic":
            return self._fetch_stock_basic(request)
        if request.endpoint != "daily":
            raise ValueError(
                "TushareSource supports only the unadjusted daily endpoint"
            )
        # ... existing daily body unchanged ...
```

```python
    def _fetch_stock_basic(self, request: DataRequest) -> FetchResult:
        """Fetch one whole-market security-master reference snapshot.

        ``stock_basic`` is a single whole-market request: ``request.symbols``
        must be empty (it is never issued per symbol) and there is no
        window/date-range semantics on the endpoint.
        """
        if request.symbols:
            raise ValueError(
                "Tushare stock_basic is a whole-market request, not a "
                "symbol-scoped query"
            )
        request_timestamp = _utc_timestamp()
        try:
            frame = self._client.stock_basic(
                fields="ts_code,name,exchange,list_date,delist_date,list_status"
            )
        except Exception as error:
            translated = translate_supplier_error(error)
            if translated is error:
                raise
            raise translated from None
        response_timestamp = _utc_timestamp()
        self._validate_stock_basic(frame)
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata=request_metadata(
                request,
                "tushare.pro.stock_basic",
                self._sdk_version,
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
            ),
        )

    @staticmethod
    def _validate_stock_basic(frame: pd.DataFrame) -> None:
        """Validate the whole-market shape without date/symbol-set semantics."""
        if not isinstance(frame, pd.DataFrame):
            raise ContractError("supplier response is not a pandas DataFrame")
        if frame.empty:
            raise ContractError("supplier returned an empty response")
        required = ("ts_code", "name", "list_date", "delist_date", "list_status")
        missing = [name for name in required if name not in frame.columns]
        if missing:
            raise ContractError(
                "supplier response is missing columns: " + ", ".join(missing)
            )
        if frame["ts_code"].isna().any() or frame["list_status"].isna().any():
            raise ContractError("supplier response has a blank identity column")
```

- [ ] **Step 4: Verify once, commit**

Run: `pytest tests/integration/test_source_contracts.py -q`

Expected: PASS (all offline contract tests, old and new).

```bash
git add src/stock_quant/data_sources/tushare.py \
        tests/integration/test_source_contracts.py \
        tests/external/test_live_source_contracts.py \
        tests/fixtures/tushare_stock_basic.csv
git commit -m "feat: tushare stock_basic whole-market adapter endpoint"
```

---

### Task 4: Required `stock_basic` refresh in `data update` + coverage publish

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`, `tests/integration/test_data_pipeline.py`

**Consumes:** `TushareSource` `stock_basic` endpoint (Task 3); `ListStatus`, `MASTER_SOURCE_STOCK_BASIC`, `master_coverage_record/frame`, `SECURITY_MASTER_COVERAGE_COLUMNS` (Task 1).
**Produces:** `DataPipeline.update()` applies the snapshot to `master` listing facts before the primary daily fetch and publishes `security_master_coverage`; new module codes `CODE_MASTER_SNAPSHOT_INCOMPLETE`; a refresh failure or incomplete snapshot is FATAL and blocks publication. `StubAdapter` answers `stock_basic`.

- [ ] **Step 1: Add the failing tests and stub support**

Extend the imports at the top of `tests/integration/test_data_pipeline.py`:

```python
from stock_quant.data_pipeline import (  # noqa: F401
    CODE_MASTER_SNAPSHOT_INCOMPLETE,
    CODE_OPTIONAL_SOURCE_FAILURE,
    CODE_SOURCE_FETCH_FAILED,
    CODE_UNIVERSE_MASTER_MISMATCH,
    DataPipeline,
    DataUpdateRequest,
    SourceCoverage,
    resolve_latest_complete_date,
)
```

Add a repository-universe symbol constant (mirrors `conftest._REPO_ROOT`) and a `stock_basic` branch to `StubAdapter`. `Universe` is already imported at the top of the module (line 24); read the 30 symbols once at module level from the repo universe used by every fixture project in this module:

```python
_FIXTURE_UNIVERSE_SYMBOLS = tuple(
    Universe.from_yaml(
        Path(__file__).resolve().parents[2] / "configs" / "universe.yml"
    ).symbols
)
_STOCK_BASIC_LIST_DATE = date(2001, 1, 2)
```

Extend the `StubAdapter` frozen dataclass with two fields and rewrite `fetch`/`_frame` so the whole-market request neither indexes `symbols[0]` nor falls through to the daily branch:

```python
    stock_basic_symbols: tuple[str, ...] | None = None
    stock_basic_list_date_by_symbol: dict[str, date] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "action_frames", self.action_frames or {})
        object.__setattr__(
            self,
            "stock_basic_list_date_by_symbol",
            self.stock_basic_list_date_by_symbol or {},
        )

    def fetch(self, request: DataRequest) -> FetchResult:
        self.calls.append(
            (request.endpoint, request.symbols[0] if request.symbols else None)
        )
        if request.endpoint in self.failing_endpoints:
            failure = self.raise_with or RuntimeError
            raise failure(f"{self.name} supplier failure on {request.endpoint}")
        if self.raise_with is not None:
            raise self.raise_with(f"{self.name} supplier failure")
        frame = self._frame(request)
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata={"source": self.name, "sdk_version": "stub"},
        )

    def _frame(self, request: DataRequest) -> pd.DataFrame:
        if request.endpoint == "stock_basic":
            return self._stock_basic_frame()
        symbol = request.symbols[0]
        sessions = _weekdays(request.start_date, request.end_date)
        if request.endpoint == "index_history":
            return self._index_frame(sessions)
        if request.endpoint in (
            "cninfo_corporate_actions",
            "eastmoney_corporate_actions",
        ):
            overrides = self.action_frames.get(symbol)
            if overrides and request.endpoint in overrides:
                return overrides[request.endpoint]
            return pd.DataFrame()
        if request.endpoint == "stock_metadata":
            return pd.DataFrame()
        # ... existing per-session daily rows unchanged ...

    def _stock_basic_frame(self) -> pd.DataFrame:
        """One whole-market stock_basic response over the configured symbols.

        ``stock_basic_symbols`` defaults to the repository fixture universe so
        the required refresh can verify every symbol; ``list_date`` defaults to
        a real date well before the fixture windows (2001-01-02, observably
        different from the fixtures' 2018-01-02 baseline) unless overridden per
        symbol.
        """
        symbols = self.stock_basic_symbols or _FIXTURE_UNIVERSE_SYMBOLS
        return pd.DataFrame(
            [
                {
                    "ts_code": symbol,
                    "name": f"stub_{symbol}",
                    "list_date": self.stock_basic_list_date_by_symbol.get(
                        symbol, _STOCK_BASIC_LIST_DATE
                    ).strftime("%Y%m%d"),
                    "delist_date": "",
                    "list_status": "L",
                }
                for symbol in symbols
            ]
        )
```

Add the focused tests after `test_update_with_explicit_end_publishes_merged_dataset`:

```python
def test_update_refreshes_master_and_publishes_master_coverage(project):
    """A successful update applies stock_basic facts and records per-symbol
    evidence rows, so the published master is traceable to the snapshot."""
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        master = context.read("security_master")
        coverage = context.read("security_master_coverage")
    assert set(master["list_date"].dt.date) == {_STOCK_BASIC_LIST_DATE}
    assert set(master["list_status"]) == {"L"}
    assert not coverage.empty
    assert len(coverage) == len(master)
    assert set(coverage["symbol"]) == set(master["symbol"])
    assert set(coverage["list_status"]) == {"L"}
    assert set(coverage["source"]) == {"tushare.stock_basic"}
    assert coverage["snapshot_sha256"].str.len().eq(64).all()


def test_stock_basic_fetch_failure_blocks_update(project):
    failing = StubAdapter("tushare", failing_endpoints=("stock_basic",))
    result = DataPipeline(project.root, sources=_all_stubs(tushare=failing)).update(
        _request()
    )
    assert result.dataset_ref is None
    assert CODE_SOURCE_FETCH_FAILED in result.quality_report.by_code()
    tushare_status = next(
        status for status in result.source_status if status.source == "tushare"
    )
    assert not tushare_status.ok and tushare_status.required


def test_stock_basic_snapshot_missing_symbol_blocks_update(project):
    """A whole-market snapshot that cannot account for a universe symbol must
    never publish a dataset claiming verified master facts."""
    incomplete = StubAdapter(
        "tushare", stock_basic_symbols=_FIXTURE_UNIVERSE_SYMBOLS[:-1]
    )
    result = DataPipeline(
        project.root, sources=_all_stubs(tushare=incomplete)
    ).update(_request())
    assert result.dataset_ref is None
    assert CODE_MASTER_SNAPSHOT_INCOMPLETE in result.quality_report.by_code()
```

- [ ] **Step 2: Verify failure once**

Run: `pytest tests/integration/test_data_pipeline.py -k 'master or stock_basic' -v`

Expected: FAIL — no refresh step exists and the stub's `stock_basic` branch is absent.

- [ ] **Step 3: Implement**

`src/stock_quant/data_pipeline.py`:

1. Imports — add to the `schemas` import `SECURITY_MASTER_COVERAGE_COLUMNS`, and add a new import block:

```python
from stock_quant.data_model.security_master import (
    ListStatus,
    MASTER_SOURCE_STOCK_BASIC,
    master_coverage_frame,
    master_coverage_record,
)
```

2. Module constants — add next to the existing `CODE_*` definitions:

```python
CODE_MASTER_SNAPSHOT_INCOMPLETE = "master_snapshot_incomplete"
```

3. In `update()`, immediately after the `end < start` guard (`start`/`end` are concrete there) and **before** the `_fetch_primary_stock` block, insert the required refresh:

```python
        # ---- required security-master reference (tushare stock_basic) ----- #
        master, master_coverage = self._refresh_security_master(
            start, end, issues, statuses, raw_snapshots, master,
        )
        if master is None:
            return self._result(
                issues, None, run_id, end, statuses, raw_snapshots,
                resolved_end_is_fallback=end_fallback,
            )
```

4. After the `_missing_issues(...)` block (just before `report = QualityReport(...)`), extend issues with the fact-vs-bar boundary check over the *refreshed* master:

```python
        issues.extend(self._master_bar_boundary_issues(master, new_daily))
```

5. Publish the refreshed master and its evidence (extend the publish dict):

```python
        tables = {
            "daily_bar": new_daily,
            "security_master": master[list(SECURITY_MASTER_COLUMNS)],
            "security_master_coverage": master_coverage,
            "corporate_action": corporate_action[
                list(CORPORATE_ACTION_COLUMNS)
            ],
            "corporate_action_coverage": coverage,
            "trading_calendar": _calendar_frame(calendar_open)[
                list(TRADING_CALENDAR_COLUMNS)
            ],
        }
```

6. Add the refresh method to the `DataPipeline` helpers (near `_refresh_corporate_actions`):

```python
    def _refresh_security_master(
        self, start, end, issues, statuses, raw_snapshots, master,
    ):
        """Apply the required tushare stock_basic whole-market snapshot.

        Runs only after ``_require_available`` has already blocked when tushare
        is unavailable, so a ``None`` source here can only be an adapter
        construction failure (already recorded as a FATAL issue by
        ``_adapter_or_fail``).

        Refreshes only the listing facts (``list_date`` / ``delist_date`` /
        ``list_status``); the universe labels stay from ``configs/universe.yml``
        as carried by ``master``.  Returns ``(refreshed_master, coverage)`` on
        success, or ``(None, None)`` after a FATAL issue (transport failure or
        a snapshot missing a universe symbol), which blocks publication.
        """
        source = self._adapter_or_fail("tushare", statuses)
        if source is None:
            # Adapter construction failure (e.g. a missing TUSHARE_TOKEN):
            # ``_adapter_or_fail`` records the source status but no issue, so
            # surface the same stable FATAL the daily path would have produced.
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_SOURCE_FETCH_FAILED,
                    details={
                        "source": "tushare",
                        "endpoint": "stock_basic",
                        "message": (
                            statuses["tushare"].reason or "adapter unavailable"
                        ),
                    },
                )
            )
            return None, None
        config = self._project_config.sources.get("tushare", SourceConfig())
        policy = RetryPolicy(
            max_attempts=min(config.max_retries + 1, 3),
            maximum_wait_seconds=min(config.timeout_seconds, 30),
        )
        try:
            result = fetch_with_retry(
                source,
                DataRequest("stock_basic", (), start, end, {}),
                policy,
                sleeper=self._sleeper,
            )
        except Exception as error:  # noqa: BLE001 - required role
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_SOURCE_FETCH_FAILED,
                    details={
                        "source": "tushare",
                        "endpoint": "stock_basic",
                        "message": str(translate_supplier_error(error)),
                    },
                )
            )
            statuses["tushare"] = SourceStatus(
                "tushare", True, False,
                reason=f"stock_basic fetch failed: {error}",
            )
            return None, None
        snapshot_sha256 = self._record_raw(result)
        raw_snapshots.append(snapshot_sha256)
        applied = _apply_stock_basic(
            master,
            result.frame,
            snapshot_sha256,
            sdk_version=str(result.metadata.get("sdk_version", "unknown")),
            checked_at=_ingest_time(result.metadata),
        )
        if applied is None:
            issues.append(
                _issue(
                    Severity.FATAL,
                    CODE_MASTER_SNAPSHOT_INCOMPLETE,
                    details={
                        "message": (
                            "tushare stock_basic snapshot cannot account for "
                            "every universe symbol"
                        ),
                        "source": "tushare",
                        "endpoint": "stock_basic",
                    },
                )
            )
            statuses["tushare"] = SourceStatus(
                "tushare", True, False,
                reason="stock_basic snapshot incomplete",
            )
            return None, None
        statuses["tushare"] = SourceStatus("tushare", True, True)
        return applied
```

7. Add the module-local mapper next to the other module builders (near `_coverage_record_for`):

```python
def _apply_stock_basic(master, raw, snapshot_sha256, *, sdk_version, checked_at):
    """Map one stock_basic snapshot onto master rows and build coverage rows.

    Only listing facts are refreshed; ``name``/``exchange``/``board`` stay from
    the universe labels carried by ``master``.  Returns ``(refreshed_master,
    coverage)`` or ``None`` when the snapshot cannot account for every master
    symbol (a FATAL condition the caller records).
    """
    facts: dict[str, dict[str, object]] = {}
    for record in raw.to_dict("records"):
        ts_code = str(record.get("ts_code", "")).strip()
        list_date = _stock_basic_date(record.get("list_date"))
        status = str(record.get("list_status", "")).strip()
        if not ts_code or list_date is None or status not in {
            ListStatus.L.value,
            ListStatus.D.value,
            ListStatus.P.value,
        }:
            continue
        facts[ts_code] = {
            "list_date": list_date,
            "delist_date": _stock_basic_date(record.get("delist_date")),
            "list_status": status,
        }
    refreshed: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    missing: list[str] = []
    for record in master.to_dict("records"):
        symbol = str(record["symbol"])
        fact = facts.get(symbol)
        if fact is None:
            missing.append(symbol)
            continue
        row = dict(record)
        row["list_date"] = pd.Timestamp(fact["list_date"])
        row["delist_date"] = (
            pd.Timestamp(fact["delist_date"])
            if fact["delist_date"] is not None
            else pd.NaT
        )
        row["list_status"] = fact["list_status"]
        refreshed.append(row)
        coverage_rows.append(
            master_coverage_record(
                symbol,
                list_date=fact["list_date"],
                delist_date=fact["delist_date"],
                list_status=fact["list_status"],
                source=MASTER_SOURCE_STOCK_BASIC,
                snapshot_sha256=snapshot_sha256,
                sdk_version=sdk_version,
                checked_at=checked_at,
            )
        )
    if missing:
        return None
    frame = pd.DataFrame(refreshed, columns=list(SECURITY_MASTER_COLUMNS))
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    frame["delist_date"] = pd.to_datetime(frame["delist_date"], errors="coerce")
    return frame, master_coverage_frame(coverage_rows)


def _stock_basic_date(value):
    """Parse a stock_basic date cell (YYYYMMDD str / NaT / None) to ``date``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nat", "nan", "none", ""}:
        return None
    timestamp = pd.to_datetime(text, errors="coerce")
    if pd.isna(timestamp):
        return None
    return timestamp.date()
```

- [ ] **Step 4: Verify once, commit**

Run: `pytest tests/integration/test_data_pipeline.py -q`

Expected: PASS (the three new tests plus every existing update/validate test over the now-required stub `stock_basic`).

```bash
git add src/stock_quant/data_pipeline.py tests/integration/test_data_pipeline.py
git commit -m "feat: required stock_basic master refresh and coverage publish"
```

---

### Task 5: Master fact-vs-bar boundary and coverage-consistency checks

**Files:**
- Modify: `src/stock_quant/data_pipeline.py`, `tests/integration/test_data_pipeline.py`

**Consumes:** refreshed 7-column master + published `security_master_coverage` (Task 4).
**Produces:** `_master_bar_boundary_issues(master, daily)` → WARNING `master_bar_boundary` issues, run in both `update()` and `validate()`; `_master_coverage_consistency_issues(master, coverage)` → FATAL `master_coverage_mismatch` issues (missing table/missing rows or per-symbol date/status disagreement), run in `validate()` only. New module codes `CODE_MASTER_BAR_BOUNDARY`, `CODE_MASTER_COVERAGE_MISMATCH`.

- [ ] **Step 1: Write the failing tests**

Add `from stock_quant.data_model.security_master import master_coverage_frame` to the module imports, extend the import list with the two new codes, and add one generic republish helper plus the focused tests. Every dataset below is first produced by a real `update()` over the stub suppliers (so its coverage was derived from the same master it publishes and is internally consistent), then either validated directly or tampered via a manual republish to stage an audit breach:

```python
def _republish_current_tables(
    root: Path,
    *,
    master_coverage: pd.DataFrame | None = None,
    include_master_coverage: bool = True,
) -> str:
    """Republish CURRENT with an optional security_master_coverage variant.

    ``master_coverage`` replaces the published table; ``include_master_coverage
    = False`` omits it entirely (the "older dataset" shape).  Used only to stage
    validate-only audit breaches the pipeline itself can never write.
    """
    publisher = DatasetPublisher(root)
    version = publisher.current().version
    reader = DatasetReader(root)
    with reader.open(version) as context:
        tables = {
            "daily_bar": context.read("daily_bar"),
            "security_master": context.read("security_master"),
            "corporate_action": context.read("corporate_action"),
            "corporate_action_coverage": context.read(
                "corporate_action_coverage"
            ),
            "trading_calendar": context.read("trading_calendar"),
        }
        if include_master_coverage:
            tables["security_master_coverage"] = (
                master_coverage
                if master_coverage is not None
                else context.read("security_master_coverage")
            )
    return publisher.publish(tables, QualityReport()).version


def _update_and_validate(project, **tushare_overrides):
    """One healthy update; returns ``(pipeline, result)`` for the follow-on."""
    tushare = StubAdapter("tushare", **tushare_overrides)
    pipeline = DataPipeline(project.root, sources=_all_stubs(tushare=tushare))
    result = pipeline.update(_request())
    assert result.dataset_ref is not None
    return pipeline, result


def test_update_reports_bar_before_list_date_as_warning(project):
    """A stock_basic list_date inside the bar window exposes pre-listing bars as
    a WARNING (fact-vs-bar boundary) and never blocks publication."""
    _, result = _update_and_validate(
        project,
        stock_basic_list_date_by_symbol={"600000.SH": date(2021, 11, 20)},
    )
    assert result.dataset_ref is not None
    report = result.quality_report
    assert report.by_code()[CODE_MASTER_BAR_BOUNDARY] >= 1
    issue = next(
        item for item in report.issues if item.code == CODE_MASTER_BAR_BOUNDARY
    )
    assert issue.severity is Severity.WARNING
    assert issue.symbol == "600000.SH"


def test_validate_reports_bar_before_list_date_as_warning(project):
    """The same boundary check re-runs over a published version in validate(),
    and the update-derived dataset stays coverage-consistent (no FATAL)."""
    pipeline, result = _update_and_validate(
        project,
        stock_basic_list_date_by_symbol={"600000.SH": date(2021, 11, 20)},
    )
    report = pipeline.validate(result.dataset_ref.version)
    assert report.by_code()[CODE_MASTER_BAR_BOUNDARY] >= 1
    issue = next(
        item for item in report.issues if item.code == CODE_MASTER_BAR_BOUNDARY
    )
    assert issue.severity is Severity.WARNING
    assert report.by_severity()[Severity.FATAL.value] == 0


def test_validate_surfaces_master_coverage_mismatch_as_fatal(project):
    """Validate-only: a coverage row disagreeing with master is a FATAL break."""
    pipeline, result = _update_and_validate(project)
    version = result.dataset_ref.version
    reader = DatasetReader(project.root)
    with reader.open(version) as context:
        coverage = context.read("security_master_coverage")
    records = coverage.to_dict("records")
    for record in records:
        if record["symbol"] == "600000.SH":
            record["list_status"] = "P"
    _republish_current_tables(
        project.root, master_coverage=master_coverage_frame(records)
    )
    report = pipeline.validate()
    assert report.by_code()[CODE_MASTER_COVERAGE_MISMATCH] == 1
    issue = next(
        item for item in report.issues
        if item.code == CODE_MASTER_COVERAGE_MISMATCH
    )
    assert issue.severity is Severity.FATAL
    assert issue.details["symbols"] == ["600000.SH"]


def test_validate_surfaces_missing_master_coverage_as_fatal(project):
    """Validate-only: a dataset without the evidence table cannot validate."""
    pipeline, _ = _update_and_validate(project)
    _republish_current_tables(project.root, include_master_coverage=False)
    report = pipeline.validate()
    assert report.by_code()[CODE_MASTER_COVERAGE_MISMATCH] == 1
    issue = next(
        item for item in report.issues
        if item.code == CODE_MASTER_COVERAGE_MISMATCH
    )
    assert issue.severity is Severity.FATAL
    assert issue.details["missing"]
```

- [ ] **Step 2: Verify failure once**

Run: `pytest tests/integration/test_data_pipeline.py -k 'boundary or master_coverage' -v`

Expected: FAIL — neither code nor check exists.

- [ ] **Step 3: Implement**

`src/stock_quant/data_pipeline.py`:

1. Add the two module codes beside `CODE_MASTER_SNAPSHOT_INCOMPLETE`:

```python
CODE_MASTER_BAR_BOUNDARY = "master_bar_boundary"
CODE_MASTER_COVERAGE_MISMATCH = "master_coverage_mismatch"
```

2. In `validate()` (lines ~299-311), read the optional coverage table *inside* the existing `with reader.open(version) as context:` block, then extend the two check calls after it (the boundary check runs on the dataset's own `daily`; the consistency check is validate-only):

```python
            master = context.read("security_master")
            coverage = None
            if "security_master_coverage" in context.tables:
                coverage = context.read("security_master_coverage")
        issues.extend(self._universe_master_issues(master))
        issues.extend(self._master_bar_boundary_issues(master, daily))
        issues.extend(self._master_coverage_consistency_issues(master, coverage))
        return QualityReport(issues=tuple(issues))
```

3. Add the two methods beside `_universe_master_issues`:

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

    def _master_coverage_consistency_issues(
        self, master: pd.DataFrame, coverage: pd.DataFrame | None
    ) -> list[QualityIssue]:
        """Validate-only FATAL when evidence is absent or disagrees with master.

        Never runs inside ``update()``: the refresh builds the coverage rows
        from the very master it publishes, so a mismatch there is impossible;
        the check guards the *stored* dataset against drift, tampering or an
        older schema.
        """
        covered = _covered_symbols(coverage)
        missing = sorted(
            {str(row["symbol"]) for row in master.to_dict("records")} - covered
        )
        if missing:
            return [
                _issue(
                    Severity.FATAL,
                    CODE_MASTER_COVERAGE_MISMATCH,
                    table="security_master",
                    details={
                        "message": (
                            "security_master_coverage is missing rows for "
                            "pinned symbols"
                        ),
                        "missing": missing,
                    },
                )
            ]
        facts = {
            str(row["symbol"]): row
            for row in coverage.to_dict("records")
        }
        disagree: list[str] = []
        for record in master.to_dict("records"):
            symbol = str(record["symbol"])
            fact = facts[symbol]
            if (
                _as_date(record["list_date"]) != _as_date(fact["list_date"])
                or _as_date(record["delist_date"])
                != _as_date(fact["delist_date"])
                or str(record["list_status"]) != str(fact["list_status"])
            ):
                disagree.append(symbol)
        if not disagree:
            return []
        return [
            _issue(
                Severity.FATAL,
                CODE_MASTER_COVERAGE_MISMATCH,
                table="security_master",
                details={
                    "message": "security_master disagrees with its coverage "
                    "evidence",
                    "symbols": sorted(disagree),
                },
            )
        ]
```

4. Add the module helper `_covered_symbols` next to the other module builders:

```python
def _covered_symbols(coverage: pd.DataFrame | None) -> set[str]:
    if coverage is None or not isinstance(coverage, pd.DataFrame):
        return set()
    return set(str(value) for value in coverage.get("symbol", []))
```

5. `update()` already extends issues with `_master_bar_boundary_issues` (Task 4 step 3.4); confirm that call site exists and the refresh runs before it.

- [ ] **Step 4: Verify once, commit**

Run: `pytest tests/integration/test_data_pipeline.py -q`

Expected: PASS.

```bash
git add src/stock_quant/data_pipeline.py tests/integration/test_data_pipeline.py
git commit -m "feat: master fact-boundary warning and validate-only coverage consistency"
```

---

### Task 6: RESEARCH master-evidence freeze + new-IPO acceptance + docs

**Files:**
- Modify: `src/stock_quant/research/runner.py`, `tests/integration/test_research_runner.py`, `docs/operations/phase-one-validation.md`, `RUNBOOK.md`

**Consumes:** `missing_master_coverage_symbols` (Task 1); fixture default master-coverage rows and `_publish_synthetic_dataset` fresh/`master_coverage` params (Task 2); `_BARS_END` helper market.
**Produces:** `ResearchRunner._enforce_research_master_evidence(frozen)` raising in RESEARCH mode before any stage when any universe symbol lacks a coverage row, with a per-symbol stable code (`SOURCE_NOT_REQUESTED`) in the message; ENGINEERING never raises. Acceptance proof that a real listing date under 120 sessions excludes the symbol via the existing `seasoning_below_120` gate.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_research_runner.py`:

```python
def _publish_dataset_without_master_evidence(project_root: Path) -> str:
    """Republish CURRENT with an empty security_master_coverage table."""
    from stock_quant.data_model.security_master import master_coverage_frame

    return _publish_synthetic_dataset(
        project_root,
        master_coverage=master_coverage_frame([]),
    )


def test_research_rejects_missing_master_evidence_but_engineering_is_untrusted(
    env,
):
    """Empty master evidence freezes RESEARCH; ENGINEERING still diagnoses.

    The rejection must name the security-master evidence and happen before any
    backtest; the ENGINEERING run proceeds as an UNTRUSTED diagnostic that can
    never be accepted as a trusted performance claim.
    """
    _publish_dataset_without_master_evidence(env.root)
    runner = ResearchRunner(env.root, config_root=_REPO_ROOT)
    with pytest.raises(ResearchRunFailed, match="security master evidence"):
        runner.run(_SPEC)
    debug = runner.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)
    metrics = json.loads(
        (debug.path / "metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["evaluation"]["status"] == "UNTRUSTED"
    assert debug.manifest.status == "REJECTED"


def test_new_stock_excluded_before_120_listed_days(tmp_path):
    """A symbol with a real list_date under 120 sessions before the window end
    never enters a factor candidate set (momentum seasoning)."""
    sessions = _weekdays(_BARS_START, _BARS_END)
    fresh_symbol = "603999.SH"
    fresh_list_date = sessions[-90]
    fresh_growth = 0.00200
    project_root = tmp_path / "project"
    _publish_synthetic_dataset(
        project_root,
        fresh=(fresh_symbol, fresh_list_date, fresh_growth),
    )
    runner = ResearchRunner(project_root, config_root=_REPO_ROOT)
    experiment = runner.run(_SPEC)
    assert experiment.manifest.status in ("ACCEPTED", "REJECTED")

    factors = pd.read_parquet(experiment.path / "factor_results.parquet")
    fresh_rows = factors.loc[factors["symbol"] == fresh_symbol]
    assert not fresh_rows.empty, "the fresh symbol must produce factor rows"
    assert not fresh_rows["is_valid"].astype(bool).any()
    assert "seasoning_below_120" in set(fresh_rows["invalid_reason"])

    targets = pd.read_parquet(experiment.path / "target_positions.parquet")
    assert fresh_symbol not in set(targets["symbol"]), (
        "a symbol with fewer than 120 listed trading days must be kept out of "
        "target positions"
    )
```

- [ ] **Step 2: Verify failure once**

Run: `pytest tests/integration/test_research_runner.py -k 'master_evidence or listed_days' -v`

Expected: FAIL — no freeze gate exists (RESEARCH accepts the empty-evidence dataset) and the fresh-IPO fixture helpers are absent.

- [ ] **Step 3: Implement the gate and finish the fixture helpers**

`src/stock_quant/research/runner.py`:

1. Imports — add (near the trust imports):

```python
from stock_quant.data_model.corporate_action_coverage import CoverageReason
from stock_quant.data_model.security_master import missing_master_coverage_symbols
```

2. Add the helpers and the gate method beside the CA-trust gate (`_enforce_research_trust`):

```python
    def _master_coverage_evidence(
        self, frozen: ExperimentSpec
    ) -> pd.DataFrame | None:
        """The pinned security_master_coverage table, or None when absent."""
        context = self._open_context(frozen.dataset_version)
        if "security_master_coverage" not in context.tables:
            return None
        return context.read("security_master_coverage")

    def _enforce_research_master_evidence(
        self, frozen: ExperimentSpec
    ) -> None:
        """Raise before any stage when a RESEARCH universe lacks master evidence.

        Formal research requires one ``security_master_coverage`` row per
        universe symbol: row presence means the listing facts were applied from
        a real tushare ``stock_basic`` refresh.  A bootstrap seed, an older
        dataset without the table and an incomplete refresh all fail here, each
        symbol carrying the stable ``SOURCE_NOT_REQUESTED`` code.  ENGINEERING
        always proceeds as a diagnostic (its evaluation is stamped UNTRUSTED by
        the report stage).
        """
        if frozen.trust_mode is not DataTrustMode.RESEARCH:
            return
        missing = missing_master_coverage_symbols(
            self._universe_symbols, self._master_coverage_evidence(frozen)
        )
        if not missing:
            return
        listed = ", ".join(
            f"{symbol}:{CoverageReason.SOURCE_NOT_REQUESTED.value}"
            for symbol in missing
        )
        raise ValueError(
            "security master evidence: research run {} cannot use dataset {}: "
            "{} of {} universe symbols have no security_master_coverage row "
            "({})".format(
                self._run_id,
                frozen.dataset_version,
                len(missing),
                len(self._universe_symbols),
                listed,
            )
        )
```

3. Call it at the top of `_pipeline`, immediately after the universe load:

```python
        self._universe_symbols = self._load_universe_symbols()
        self._enforce_research_master_evidence(frozen)
```

(Rejects before the pin stage writes anything; ENGINEERING returns immediately. The CA gate at `_produce_backtest` is untouched and still runs second.)

Finish the `test_research_runner.py` fixture helpers so `_security_master(fresh=None)`, `_bars(..., fresh=None)` and `_master_coverage(symbols)` support the acceptance test:

- `_security_master(fresh: tuple[str, date, float] | None = None)` returns a frame over `EQUITY_GROWTH` symbols plus, when `fresh` is given, the fresh symbol appended with `list_date` = `fresh[1]` and `list_status="L"`.
- `_bars(sessions, *, index_close, limit_locked_symbols=(), fresh=None)` builds the 12 `EQUITY_GROWTH` frames as today, then when `fresh` is given appends `_instrument_frame(sessions[start_index:], fresh[0], closes[start_index:])` where `start_index` is the first index whose session `>= fresh[1]` and `closes[idx] = _BASE_PRICE * math.exp(fresh[2] * (idx - (len(sessions) - 1)))`.
- `_publish_synthetic_dataset` (already extended in Task 2) computes `master = _security_master(fresh)` first, derives `symbols` from `master["symbol"]`, defaults both `coverage` and `master_coverage` from that full symbol set, and passes `fresh` through to `_bars`.

Docs:

- `docs/operations/phase-one-validation.md` — add a checklist item under section 4 after item 12:

```markdown
13. **证券主数据现场核对**：活数据集的 `security_master` 中 `list_date`/`delist_date`/
    `list_status` 应可逐标的回溯到 tushare `stock_basic` 快照：抽查
    `data/standardized/<version>/security_master_coverage.parquet`——每只股票池标的应
    恰有一行（`source=tushare.stock_basic`、`snapshot_sha256` 对应原始快照哈希）。
    研究冻结以「每标的行存在」为 VERIFIED 前提；缺行/空表/旧数据集在 RESEARCH 模式会被
    拒绝（逐标的 `SOURCE_NOT_REQUESTED`），bootstrap 空种子恒被拒。对已发布数据集执行
    `data validate` 复核「事实 vs 行情边界」WARNING 与「coverage↔master 一致性」FATAL。
```

- Add a §5 limitation bullet noting the tushare `stock_basic` snapshot is the default listed-only (`L`) reference, so a universe containing a delisted/suspended sample would surface as `master_snapshot_incomplete` (expected for the 30 fixed listed samples this phase).

- `RUNBOOK.md` — under the `data update` description note that tushare `stock_basic` is a required whole-market step: it refreshes listing facts and publishes `security_master_coverage`; a fetch failure or a snapshot missing a universe symbol blocks publication.

- [ ] **Step 4: Final verification and commit**

Run: `pytest tests/integration/test_research_runner.py tests/integration/test_cli.py tests/integration/test_end_to_end.py tests/integration/test_data_pipeline.py -q`

Expected: PASS — the default RESEARCH suites now pass the master gate (fixture rows present), the new rejection/acceptance tests pass, and broken fixtures fail in RESEARCH with `security master evidence`.

Run: `pytest -q && ruff check src tests && ruff format --check src tests`

Expected: PASS; no external-network tests run.

```bash
git add src/stock_quant/research/runner.py \
        tests/integration/test_research_runner.py \
        docs/operations/phase-one-validation.md RUNBOOK.md
git commit -m "feat: freeze research on security master evidence; accept new-IPO exclusion"
```

---

## Plan Self-Review

- **Spec coverage:** real `stock_basic` snapshot + refresh (Tasks 3-4); `list_status` column and empty `NOT_APPLIED` seed with no coverage table (Task 2); traceable per-symbol evidence published atomically (Tasks 1, 4); research freeze = row-presence gate rejecting bootstrap/old/empty datasets in RESEARCH only (Tasks 2 fixture + 6); validation matrix rows for boundary WARNING and coverage-consistency FATAL (Task 5); exactly one new-IPO `<120` acceptance fixture (Task 6); engineering always diagnostic (Task 6, unchanged CA machinery); docs matrix + RUNBOOK updated (Task 6). Out-of-scope items (akshare/baostock master cross-checks, ST history, pool expansion) are untouched.
- **Placeholder scan:** every task has concrete code for the new units and exact anchors for mechanical edits; no TBD/TODO.
- **Type consistency:** `master_coverage_record`/`master_coverage_frame`/`missing_master_coverage_symbols`/`ListStatus`/`MASTER_SOURCE_STOCK_BASIC` are defined once (Task 1) and consumed with the same signatures in Tasks 2, 4 and 6; `SECURITY_MASTER_COVERAGE_COLUMNS` matches the field builder order exactly (publish validates column equality). The runner gate raises `"security master evidence: ..."` and the tests match that substring; fixture `_publish_synthetic_dataset` param names (`coverage`, `master_coverage`, `fresh`) are identical across Tasks 2 and 6.
- Each requirement has one owning task and each task has exactly one focused red/green cycle; the full suite and Ruff run only at the final checkpoint.
