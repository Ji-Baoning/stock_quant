"""The pipeline records per-table fetch coverage into ``build_config`` (B2/T13).

``tests/integration/test_data_pipeline.py`` is too slow to run per task, so the
plan's ``test_update_records_table_fetch_coverage`` scenario lives here against
the shared fixture project: the first update over a legacy (pre-coverage)
baseline re-fetches its explicit window and records ``fetched`` segments while
the corporate-action interfaces -- whose disclosure-calendar contract window
the explicit start deviates from (F1) -- record ``not_fetched``; a follow-up
one-session increment records ``carried`` for the baseline span plus a
single-day ``fetched`` segment (spec D5.2-3).  The stub suppliers mirror the
``StubAdapter`` contract of the other pipeline suites and never touch a
network or a token.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from conftest import BARS_END, BARS_START, build_fixture_project  # noqa: E402

from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_model.fetch_coverage import validate_table_fetch_coverage
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import DataPipeline, DataUpdateRequest
from stock_quant.data_sources.base import (
    DataRequest,
    DataSource,
    FetchResult,
    request_key,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The repository fixture universe the stub ``stock_basic`` answer must cover.
_FIXTURE_UNIVERSE_SYMBOLS = tuple(
    Universe.from_yaml(
        _REPO_ROOT / "templates" / "project-config" / "universe.yml"
    ).symbols
)
_STOCK_BASIC_LIST_DATE = date(2001, 1, 2)

#: The first update covers the fixture baseline up to the session before the
#: last fixture bar, so the second update's contract window is that one
#: session -- the plan's "one extra session" increment.
_GEN1_END = date(2022, 1, 6)
_GEN2_END = BARS_END

_DECLARED_TABLES = (
    "daily_bar",
    "adjusted_bar",
    "security_master",
    "security_master_coverage",
    "corporate_action",
    "corporate_action_quarantine",
    "corporate_action_coverage",
    "trading_calendar",
    "universe_membership",
    "basic_factor",
    "basic_factor_coverage",
)


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@dataclass(frozen=True)
class StubAdapter:
    """A ``DataSource`` whose ``fetch`` returns deterministic raw frames."""

    name: str
    #: The stub raises for ``daily_basic`` instead of answering (the wired
    #: basic_factor lane's wholesale-outage path).
    fail_daily_basic: bool = False
    #: (ts_code, YYYYMMDD) rows the ``daily_basic`` answer omits, so a test
    # can open a per-symbol per-day facts hole.
    daily_basic_missing: tuple[tuple[str, str], ...] = ()
    #: (ts_code, YYYYMMDD) rows the ``daily_basic`` answer emits twice, so a
    # test can plant a duplicate (trade_date, symbol) join key.
    daily_basic_duplicated: tuple[tuple[str, str], ...] = ()

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint == "daily_basic" and self.fail_daily_basic:
            raise ValueError("stub daily_basic outage")
        frame = self._frame(request)
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata={
                "source": self.name,
                "sdk_version": "stub",
                "transport_id": self.name,
            },
        )

    def _frame(self, request: DataRequest) -> pd.DataFrame:
        if request.endpoint == "stock_basic":
            return self._stock_basic_frame()
        if request.endpoint == "trade_cal":
            return self._trade_cal_frame(request)
        if request.endpoint == "daily_basic":
            return self._daily_basic_frame(request)
        if request.endpoint in (
            "cninfo_corporate_actions",
            "eastmoney_corporate_actions",
            "rights_issue_corporate_actions",
            "stock_metadata",
        ):
            return pd.DataFrame()
        sessions = _weekdays(request.start_date, request.end_date)
        if request.endpoint == "index_history":
            return pd.DataFrame(
                {
                    "日期": sessions,
                    "开盘": [4000.0] * len(sessions),
                    "最高": [4000.0] * len(sessions),
                    "最低": [4000.0] * len(sessions),
                    "收盘": [4000.0] * len(sessions),
                    "成交量": [0] * len(sessions),
                    "成交额": [0.0] * len(sessions),
                }
            )
        symbol = request.symbols[0]
        return pd.DataFrame(
            [
                {
                    "code": symbol,
                    "date": day.strftime("%Y%m%d"),
                    "open": 55.0,
                    "high": 55.0,
                    "low": 55.0,
                    "close": 55.0,
                    "volume": 1000,
                    "amount": 55000.0,
                }
                for day in sessions
            ]
        )

    def _daily_basic_frame(self, request: DataRequest) -> pd.DataFrame:
        """One whole-market per-day daily_basic snapshot (spec §6.2)."""
        days = [
            day.strftime("%Y%m%d")
            for day in _weekdays(request.start_date, request.end_date)
        ]
        missing = set(self.daily_basic_missing)
        rows = [
            {
                "ts_code": symbol,
                "trade_date": day,
                "total_mv": 15000_0000.0,
                "turnover_rate": 1.25,
            }
            for symbol in _FIXTURE_UNIVERSE_SYMBOLS
            for day in days
            if (symbol, day) not in missing
        ]
        duplicated = set(self.daily_basic_duplicated)
        rows += [
            row for row in rows
            if (row["ts_code"], row["trade_date"]) in duplicated
        ]
        return pd.DataFrame(rows)

    @staticmethod
    def _stock_basic_frame() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": symbol,
                    "name": f"stub_{symbol}",
                    "list_date": _STOCK_BASIC_LIST_DATE.strftime("%Y%m%d"),
                    "delist_date": "",
                    "list_status": "L",
                }
                for symbol in _FIXTURE_UNIVERSE_SYMBOLS
            ]
        )

    @staticmethod
    def _trade_cal_frame(request: DataRequest) -> pd.DataFrame:
        exchange = str(request.params.get("exchange"))
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
        return pd.DataFrame(rows)


def _all_stubs() -> dict[str, DataSource]:
    return {name: StubAdapter(name) for name in _STUB_NAMES}


#: Every configurable supplier, so ``sources=`` overrides can never be the
#: reason a lane was built from a real adapter.  baostock is off by default in
#: fixtures (ADR-016) but stays here: tests that opt the dormant lane back in
#: pass the same dict.
_STUB_NAMES = ("tushare", "akshare", "baostock", "xingyao")


def _manifest_build(project_root: Path, version: str) -> dict:
    manifest = json.loads(
        (
            project_root / "data" / "standardized" / version
            / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )
    return manifest["build_config"]


def test_update_records_table_fetch_coverage(tmp_path):
    """One update publishes ``build_config.table_fetch_coverage`` (plan Step 2).

    Every declared table is recorded: the window lanes re-fetch their explicit
    window (``fetched``), and the corporate-action tables whose
    disclosure-calendar contract window the explicit start deviates from are
    recorded as ``not_fetched`` with the operator-window reason (F1).
    """
    project = build_fixture_project(tmp_path / "project")
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=BARS_START, end_date=_GEN1_END)
    )
    assert result.dataset_ref is not None
    build = _manifest_build(project.root, result.dataset_ref.version)
    coverage = build["table_fetch_coverage"]
    assert set(coverage) == set(_DECLARED_TABLES)
    fetched = [
        segment
        for segment in coverage["daily_bar"]
        if segment["kind"] == "fetched"
    ]
    assert fetched[0]["window_start"] == BARS_START.isoformat()
    assert fetched[0]["window_end"] == _GEN1_END.isoformat()
    for table in ("corporate_action", "corporate_action_quarantine",
                  "corporate_action_coverage"):
        skipped = coverage[table]
        assert [segment["kind"] for segment in skipped] == ["not_fetched"]
        assert skipped[0]["reason"] == "operator_explicit_window"


def test_incremental_update_records_carried_and_fetched(tmp_path):
    """A one-session increment carries the baseline and fetches the session.

    The second update's ``daily_bar`` contract window continues one day after
    the span the first update recorded, so the published segments are the
    carried baseline history plus a single fetched session.
    """
    project = build_fixture_project(tmp_path / "project")
    pipeline = DataPipeline(project.root, sources=_all_stubs())
    first = pipeline.update(
        DataUpdateRequest(start_date=BARS_START, end_date=_GEN1_END)
    )
    assert first.dataset_ref is not None
    second = pipeline.update(DataUpdateRequest(end_date=_GEN2_END))
    assert second.dataset_ref is not None
    build = _manifest_build(project.root, second.dataset_ref.version)
    segments = build["table_fetch_coverage"]["daily_bar"]
    kinds = {segment["kind"] for segment in segments}
    assert "carried" in kinds and "fetched" in kinds
    fetched = [s for s in segments if s["kind"] == "fetched"][0]
    assert fetched["window_start"] == fetched["window_end"]
    assert fetched["window_start"] == _GEN2_END.isoformat()
    carried = [s for s in segments if s["kind"] == "carried"][0]
    assert carried["window_start"] == BARS_START.isoformat()
    assert carried["window_end"] == _GEN1_END.isoformat()


def test_pre_record_baseline_tiles_the_acceptance_window(tmp_path):
    """A baseline without recorded spans still chains carried segments.

    A baseline published before fetch coverage was recorded (no per-table
    spans) is itself the evidence for its own review window: the next
    update's segments must chain from ``full_history_acceptance_start`` --
    carried history plus the fetched contract window, contiguous to the
    published end -- or the offline check reads a coverage gap where the
    data is in fact complete (spec D5.3, the real ``01c74bee…`` failure).
    """
    project = build_fixture_project(tmp_path / "project")
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(end_date=_GEN2_END)
    )
    assert result.dataset_ref is not None
    build = _manifest_build(project.root, result.dataset_ref.version)
    coverage = build["table_fetch_coverage"]
    violations = validate_table_fetch_coverage(
        coverage, anchor_start=BARS_START, published_end=_GEN2_END
    )
    gaps = [v for v in violations if v[0] == "fetch_coverage_gap"]
    assert gaps == [], f"coverage gap on a pre-record baseline: {gaps}"
    segments = coverage["daily_bar"]
    kinds = [segment["kind"] for segment in segments]
    assert kinds == ["carried", "fetched"]
    carried, fetched = segments
    assert carried["window_start"] == BARS_START.isoformat()
    assert (
        date.fromisoformat(carried["window_end"])
        == date.fromisoformat(fetched["window_start"]) - timedelta(days=1)
    )
    assert fetched["window_end"] == _GEN2_END.isoformat()


def test_fetched_ca_lane_merges_baseline_coverage_rows(tmp_path):
    """A fetched disclosure window keeps the baseline's earlier coverage.

    The refreshed coverage verdict covers only its own contract window; the
    baseline's per-symbol rows for the history before it stay valid evidence,
    clipped to end just before the fetched window, so every symbol's
    published coverage still tiles the whole review window and the
    corporate-action trust gate reads one continuous trusted span (spec D5.2).
    """
    project = build_fixture_project(tmp_path / "project")
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(end_date=_GEN2_END)
    )
    assert result.dataset_ref is not None
    version = result.dataset_ref.version
    published = pd.read_parquet(
        project.root / "data" / "standardized" / version
        / "corporate_action_coverage.parquet"
    )
    assert published["symbol"].nunique() == len(_FIXTURE_UNIVERSE_SYMBOLS)
    for symbol, rows in published.groupby("symbol"):
        starts = sorted(pd.Timestamp(value).date() for value in rows["window_start"])
        ends = sorted(pd.Timestamp(value).date() for value in rows["window_end"])
        assert starts[0] == BARS_START, symbol
        assert ends[-1] == _GEN2_END, symbol
        ordered = sorted(zip(starts, ends))
        for (_, prev_end), (next_start, _) in zip(ordered, ordered[1:]):
            assert next_start == prev_end + timedelta(days=1), (
                f"{symbol}: coverage gap {prev_end} .. {next_start}"
            )


def test_update_writes_call_ledger(tmp_path):
    """A published update persists the per-source call ledger (spec D5.5).

    The ledger lands under ``data/runs/<run_id>/call_ledger.json`` only after
    a successful publish, one row per source the update actually used.  The
    stub suppliers expose no ``calls`` counter, so every row renders the
    zero-call contract shape; the ``reused`` section (ADR-015) is always
    present and empty here because no snapshot was ever stored twice, and so
    is the ``transport`` section (ADR-020): no batch pair is configured, so
    no session or code query was attempted.
    Endpoint names and parameter shapes are the only things a real ledger
    records -- never credentials.
    """
    project = build_fixture_project(tmp_path / "project")
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=BARS_START, end_date=_GEN1_END)
    )
    assert result.dataset_ref is not None
    path = project.root / "data" / "runs" / result.run_id / "call_ledger.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        name: {"calls": 0, "endpoints": {}, "reused": {}, "transport": {}}
        for name in ("tushare", "akshare", "xingyao")
    }


def test_basic_factor_source_outage_falls_back_to_disabled(tmp_path):
    """A wholesale ``daily_basic`` outage keeps the disabled record (§7.5.2).

    The wired lane (P2c Task 2) never raises: every requested day fails,
    the round publishes the carried (empty) canonical frames, and the
    segment shape stays the Task 1 whole-window ``source_disabled`` record
    -- never a fabricated fetched segment.
    """
    project = build_fixture_project(tmp_path / "project")
    stubs = {
        **_all_stubs(),
        "tushare": StubAdapter("tushare", fail_daily_basic=True),
    }
    result = DataPipeline(project.root, sources=stubs).update(
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


def test_basic_factor_lane_publishes_facts_and_history_prefix(tmp_path):
    """A fully answered ``daily_basic`` window publishes both tables.

    The first wired pull over a baseline without basic_factor history
    starts at the project's own start anchor (2020-01-01), later than the
    acceptance anchor (BARS_START): the manifest records the ONLY legal
    prefix -- ``history_begins_after_anchor`` over [anchor,
    supported_start - 1] -- followed by this round's ``fetched`` segment,
    and the coverage table shares the facts table's window exactly
    (spec §7.5.1, P2a's reserved prefix writer).
    """
    project = build_fixture_project(tmp_path / "project")
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(end_date=_GEN2_END)
    )
    assert result.dataset_ref is not None
    version = result.dataset_ref.version
    build = _manifest_build(project.root, version)
    segments = build["table_fetch_coverage"]["basic_factor"]
    assert [s["kind"] for s in segments] == ["not_fetched", "fetched"]
    prefix, fetched = segments
    assert prefix["reason"] == "history_begins_after_anchor"
    assert prefix["window_start"] == BARS_START.isoformat()
    assert (
        date.fromisoformat(prefix["window_end"])
        == date.fromisoformat(fetched["window_start"]) - timedelta(days=1)
    )
    assert fetched["window_start"] == date(2020, 1, 1).isoformat()
    assert fetched["window_end"] == _GEN2_END.isoformat()
    coverage_segments = build["table_fetch_coverage"][
        "basic_factor_coverage"
    ]
    strip_table = [
        {k: v for k, v in s.items() if k != "table"} for s in coverage_segments
    ]
    assert strip_table == [
        {k: v for k, v in s.items() if k != "table"} for s in segments
    ]
    violations = validate_table_fetch_coverage(
        build["table_fetch_coverage"],
        anchor_start=BARS_START,
        published_end=_GEN2_END,
    )
    assert [
        v
        for v in violations
        if v[1].get("table")
        in ("basic_factor", "basic_factor_coverage")
    ] == [], f"basic_factor fetch coverage violations: {violations}"

    tables_root = project.root / "data" / "standardized" / version
    facts = pd.read_parquet(tables_root / "basic_factor.parquet")
    coverage = pd.read_parquet(tables_root / "basic_factor_coverage.parquet")
    assert not facts.empty and not coverage.empty
    assert pd.Timestamp(facts["trade_date"].min()) == pd.Timestamp(
        "2020-01-01"
    )
    assert facts["source"].eq("tushare").all()
    probe = facts.iloc[0]
    assert probe["market_cap"] == 15000_0000.0 * 10_000.0
    assert probe["turnover_rate"] == 1.25 / 100.0
    assert not coverage["status"].eq("UNTRUSTED").any()


def test_basic_factor_missing_row_is_evidence_not_a_blocker(tmp_path):
    """A per-symbol per-day facts hole publishes UNTRUSTED coverage rows.

    The stub omits one symbol's row on the last session: the facts table
    simply lacks that row, the coverage table carries the
    ``UNTRUSTED``/``FACTS_INCOMPLETE`` verdict for that symbol -- evidence,
    never a blocker -- and every fully answered symbol stays ``VERIFIED``
    (spec §7.3).
    """
    project = build_fixture_project(tmp_path / "project")
    stubs = {
        **_all_stubs(),
        "tushare": StubAdapter(
            "tushare",
            daily_basic_missing=(("600000.SH", _GEN2_END.strftime("%Y%m%d")),),
        ),
    }
    result = DataPipeline(project.root, sources=stubs).update(
        DataUpdateRequest(end_date=_GEN2_END)
    )
    assert result.dataset_ref is not None
    tables_root = (
        project.root / "data" / "standardized" / result.dataset_ref.version
    )
    coverage = pd.read_parquet(tables_root / "basic_factor_coverage.parquet")
    facts = pd.read_parquet(tables_root / "basic_factor.parquet")
    victim = coverage[coverage["symbol"] == "600000.SH"]
    assert len(victim) == 1
    assert victim.iloc[0]["status"] == "UNTRUSTED"
    assert victim.iloc[0]["reason"] == "FACTS_INCOMPLETE"
    others = coverage[coverage["symbol"] != "600000.SH"]
    assert not others.empty
    assert others["status"].eq("VERIFIED").all()
    victim_dates = pd.to_datetime(
        facts.loc[facts["symbol"] == "600000.SH", "trade_date"]
    )
    assert pd.Timestamp(_GEN2_END).date() not in set(victim_dates.dt.date)


def test_duplicate_basic_factor_key_fails_the_round(tmp_path):
    """A duplicated (trade_date, symbol) fact fails the round (spec §7.2).

    The stub answers ``daily_basic`` with one duplicated row: the P2c
    Task 3 join check blocks publication -- no dataset ref, CURRENT stays
    on the fixture baseline, and the quality report carries the FATAL
    ``basic_factor_join_duplicate`` code.  (The formal integration shape
    lands with Task 5's ``test_basic_factor_publish.py``.)
    """
    project = build_fixture_project(tmp_path / "project")
    standardized_root = project.root / "data" / "standardized"
    before = (standardized_root / "CURRENT").read_text(encoding="utf-8")
    stubs = {
        **_all_stubs(),
        "tushare": StubAdapter(
            "tushare",
            daily_basic_duplicated=(
                ("600000.SH", _GEN1_END.strftime("%Y%m%d")),
            ),
        ),
    }
    result = DataPipeline(project.root, sources=stubs).update(
        DataUpdateRequest(start_date=BARS_START, end_date=_GEN1_END)
    )
    assert result.dataset_ref is None
    assert "basic_factor_join_duplicate" in result.quality_report.by_code()
    assert [
        item.severity.value
        for item in result.quality_report.issues
        if item.code == "basic_factor_join_duplicate"
    ] == ["FATAL"]
    after = (standardized_root / "CURRENT").read_text(encoding="utf-8")
    assert after == before
