"""The publish path records ``table_lineage`` and verifies it (P2c Task 4).

``tests/integration/test_data_pipeline.py`` is too slow to run per task, so
the publish-shape scenarios live here against the shared fixture project with
the same ``StubAdapter`` contract the other pipeline suites use (no network,
no token).  The publish-time obligations pinned here (spec §7.3):

- a fully answered ``daily_basic`` window records ``build_config
  .table_lineage`` rows whose ``transport`` normalizes the answering party
  into the contract's ``<source>:<kind>`` vocabulary;
- a round whose answering transport cannot be shown to equal the declared
  ``primary_transport`` fails with the FATAL
  ``table_lineage_transport_mismatch`` and leaves ``CURRENT`` untouched;
- a ``per_symbol_window`` table must publish its ``<table>_coverage`` table
  (FATAL ``coverage_table_missing``), and a research_only table's table-level
  blocking issues downgrade to ``coverage_downgraded`` WARNING records
  instead of blocking (spec D1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from conftest import BARS_END, build_fixture_project  # noqa: E402

from stock_quant.data_contracts import DataContract
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import (
    DataPipeline,
    DataUpdateRequest,
    _table_lineage_issues,
    coverage_table_of,
)
from stock_quant.data_quality.models import QualityIssue, Severity
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
    #: The identity of the answering party recorded in the raw snapshot
    #: manifest; defaults to the adapter's own name (the standard stub form).
    #: A distinct value models a genuinely different transport answering the
    #: same source name, so the lineage comparison has something to catch.
    transport_name: str | None = None

    def fetch(self, request: DataRequest) -> FetchResult:
        frame = self._frame(request)
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            metadata={
                "source": self.name,
                "sdk_version": "stub",
                "transport_id": self.transport_name or self.name,
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
        rows = [
            {
                "ts_code": symbol,
                "trade_date": day,
                "total_mv": 15000_0000.0,
                "turnover_rate": 1.25,
            }
            for symbol in _FIXTURE_UNIVERSE_SYMBOLS
            for day in days
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


#: Every configurable supplier, so ``sources=`` overrides can never be the
#: reason a lane was built from a real adapter (mirrors the other suites).
_STUB_NAMES = ("tushare", "akshare", "baostock", "xingyao")


def _all_stubs() -> dict[str, DataSource]:
    return {name: StubAdapter(name) for name in _STUB_NAMES}


def _manifest_build(project_root: Path, version: str) -> dict:
    manifest = (
        project_root / "data" / "standardized" / version
        / "dataset_manifest.json"
    ).read_text(encoding="utf-8")
    return json.loads(manifest)["build_config"]


# --------------------------------------------------------------------------- #
# ① The published build_config records the daily_basic lineage
# --------------------------------------------------------------------------- #


def test_publish_records_basic_factor_table_lineage(tmp_path):
    """A fully answered window records the basic_factor lineage row.

    The row's ``transport`` normalizes the answering party into the
    contract's ``<source>:<kind>`` vocabulary -- the stub answers as the
    source itself, the publish-mandated relay form (tushare_transport §1.2)
    -- and its ``raw_snapshot`` is a sanitized, re-resolvable daily_basic
    evidence row.  The build-config contract version stays ``1``: a new key,
    not a bump.
    """
    project = build_fixture_project(tmp_path / "project")
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(end_date=BARS_END)
    )
    assert result.dataset_ref is not None
    build = _manifest_build(project.root, result.dataset_ref.version)
    assert build["pipeline_contract_version"] == 1
    lineage = build["table_lineage"]
    row = lineage["basic_factor"]
    assert row["table"] == "basic_factor"
    assert row["transport"] == "tushare:relay"
    raw = row["raw_snapshot"]
    assert set(raw) == {
        "source",
        "endpoint",
        "transport_id",
        "request_key",
        "file_sha256",
        "manifest_sha256",
    }
    assert raw["source"] == "tushare"
    assert raw["endpoint"] == "daily_basic"
    assert raw["transport_id"] == "tushare"
    # The coverage table is served by the same lane: same lineage shape.
    coverage_row = lineage["basic_factor_coverage"]
    assert coverage_row["transport"] == "tushare:relay"
    assert coverage_row["raw_snapshot"]["endpoint"] == "daily_basic"


# --------------------------------------------------------------------------- #
# ② A mismatching answering transport fails the round
# --------------------------------------------------------------------------- #


def test_lineage_transport_mismatch_fails_the_round(tmp_path):
    """A round answered by a different transport must not publish.

    The stub answers ``daily_basic`` under a distinguishable transport id
    (``tushare-proxy``) while the contract declares ``tushare:relay``: the
    publish-time comparison fails with the FATAL
    ``table_lineage_transport_mismatch``, no dataset ref is returned and
    ``CURRENT`` stays on the fixture baseline.
    """
    project = build_fixture_project(tmp_path / "project")
    standardized_root = project.root / "data" / "standardized"
    before = (standardized_root / "CURRENT").read_text(encoding="utf-8")
    stubs = {
        **_all_stubs(),
        "tushare": StubAdapter("tushare", transport_name="tushare-proxy"),
    }
    result = DataPipeline(project.root, sources=stubs).update(
        DataUpdateRequest(end_date=BARS_END)
    )
    assert result.dataset_ref is None
    assert "table_lineage_transport_mismatch" in result.quality_report.by_code()
    # Both served tables record lineage against the same answering party,
    # so the declared transport is contradicted once per recorded row.
    mismatches = [
        item
        for item in result.quality_report.issues
        if item.code == "table_lineage_transport_mismatch"
    ]
    assert [item.severity.value for item in mismatches] == ["FATAL", "FATAL"]
    assert {item.table for item in mismatches} == {
        "basic_factor",
        "basic_factor_coverage",
    }
    after = (standardized_root / "CURRENT").read_text(encoding="utf-8")
    assert after == before


# --------------------------------------------------------------------------- #
# ③ per_symbol_window tables must publish their coverage table
# --------------------------------------------------------------------------- #


def _alpha_contract() -> DataContract:
    return DataContract(
        table="alpha",
        tier="core",
        primary_transport="src:relay",
        anchors=[],
        conflict="block",
        pit=None,
        coverage_shape="per_symbol_window",
        incremental="last_covered_plus_1",
    )


def _alpha_lineage(transport: str) -> dict:
    return {
        "alpha": {
            "table": "alpha",
            "transport": transport,
            "raw_snapshot": {
                "source": "src",
                "endpoint": "alpha_snapshot",
                "transport_id": "relay-host",
                "request_key": "k",
                "file_sha256": "a" * 64,
                "manifest_sha256": "b" * 64,
            },
        }
    }


def test_per_symbol_window_table_without_coverage_table_fails(tmp_path):
    """A published ``per_symbol_window`` table needs its coverage table.

    ``coverage_table_of`` names the obligation: a tables mapping that
    publishes ``alpha`` without ``alpha_coverage`` fails the publish-time
    lineage check with the FATAL ``coverage_table_missing`` -- and a fully
    verified lineage row raises no transport issue of its own.
    """
    issues = _table_lineage_issues(
        _alpha_lineage("src:relay"),
        {"alpha": _alpha_contract()},
        {"alpha": pd.DataFrame()},
    )
    assert [(item.code, item.severity) for item in issues] == [
        ("coverage_table_missing", Severity.FATAL)
    ]
    assert issues[0].table == "alpha"
    assert coverage_table_of("alpha") == "alpha_coverage"


def test_lineage_transport_mismatch_names_declared_and_recorded(tmp_path):
    """A lineage row whose transport differs from the declaration fails.

    The failure is decided per recorded row: ``src:proxy`` answered while
    the contract declares ``src:relay`` -- with the coverage table present,
    the transport mismatch is the only issue the check raises.
    """
    issues = _table_lineage_issues(
        _alpha_lineage("src:proxy"),
        {"alpha": _alpha_contract()},
        {"alpha": pd.DataFrame(), "alpha_coverage": pd.DataFrame()},
    )
    assert [(item.code, item.table, item.severity) for item in issues] == [
        ("table_lineage_transport_mismatch", "alpha", Severity.FATAL)
    ]


# --------------------------------------------------------------------------- #
# ④ research_only tables downgrade instead of blocking (spec D1)
# --------------------------------------------------------------------------- #


def test_basic_factor_table_block_downgrades_instead_of_blocking(
    tmp_path, monkeypatch
):
    """A TABLE_LEVEL blocking code on ``basic_factor`` does not block.

    ``basic_factor`` is declared research_only: a ``schema_mismatch`` ERROR
    scoped to it passes the publication gate and the publish path turns it
    into a ``coverage_downgraded`` WARNING record over the same table --
    the round publishes and the lineage evidence is still recorded.
    """
    import stock_quant.data_pipeline as data_pipeline_module

    original_check_schema = data_pipeline_module.check_schema

    def inject_basic_factor_block(frame, schema, *, table):
        issues = list(original_check_schema(frame, schema, table=table))
        if table == "daily_bar":
            issues.append(
                QualityIssue(
                    severity=Severity.ERROR,
                    code="schema_mismatch",
                    table="basic_factor",
                    symbol=None,
                    trade_date=None,
                    details={"extra": ["injected_basic_factor_column"]},
                )
            )
        return issues

    monkeypatch.setattr(
        data_pipeline_module, "check_schema", inject_basic_factor_block
    )
    project = build_fixture_project(tmp_path / "project")
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(end_date=BARS_END)
    )
    assert result.dataset_ref is not None
    downgrades = [
        item
        for item in result.quality_report.issues
        if item.code == "coverage_downgraded"
    ]
    assert [item.severity.value for item in downgrades] == ["WARNING"]
    assert [item.table for item in downgrades] == ["basic_factor"]
    assert [item.details["reason_codes"] for item in downgrades] == [
        ["schema_mismatch"]
    ]
    build = _manifest_build(project.root, result.dataset_ref.version)
    assert build["table_lineage"]["basic_factor"]["transport"] == "tushare:relay"
    with DatasetReader(project.root).open(result.dataset_ref.version) as ctx:
        assert not ctx.read("basic_factor").empty
