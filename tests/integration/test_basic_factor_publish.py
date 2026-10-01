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

P2c Task 5 Step 1 adds the offline end-to-end completion conditions (§7.4
subset): the published stub window passes the real ``data validate`` CLI,
identical payloads republish to the same content-addressed version without
rewriting a byte, one changed ``total_mv`` fact yields a new version beside
an intact old one, one missing fact row lands an UNTRUSTED
FACTS_INCOMPLETE coverage row without blocking the publish, and a
whole-null ``total_mv`` window publishes nulls -- never fill-zero.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from conftest import BARS_END, build_fixture_project  # noqa: E402

from stock_quant.data_contracts import DataContract
from stock_quant.data_model.basic_factor import MARKET_CAP_UNIT_FACTOR
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import (
    DataPipeline,
    DataUpdateRequest,
    _table_lineage_issues,
    coverage_table_of,
)
from stock_quant.data_quality.models import QualityIssue, QualityReport, Severity
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


# --------------------------------------------------------------------------- #
# Task 5 Step 1: offline end-to-end completion conditions (§7.4 subset)
# --------------------------------------------------------------------------- #


#: The universe symbol and in-window sessions the fact-level scenarios probe.
_PROBE_SYMBOL = _FIXTURE_UNIVERSE_SYMBOLS[0]
_MISSING_DAY = "20211115"  # a Monday session inside the stub update window
_MUTATED_DAY = "20211116"
_MUTATED_TOTAL_MV = 15123_4567.0


class _NullTotalMvAdapter(StubAdapter):
    """Same answers with every ``total_mv`` fact nulled (§7.2: no fill-zero)."""

    def _daily_basic_frame(self, request: DataRequest) -> pd.DataFrame:
        frame = super()._daily_basic_frame(request)
        frame["total_mv"] = None
        return frame


class _MissingRowAdapter(StubAdapter):
    """Same answers minus one covered symbol-day ``daily_basic`` fact row."""

    def _daily_basic_frame(self, request: DataRequest) -> pd.DataFrame:
        frame = super()._daily_basic_frame(request)
        drop = (frame["ts_code"] == _PROBE_SYMBOL) & (
            frame["trade_date"] == _MISSING_DAY
        )
        return frame[~drop].reset_index(drop=True)


def _named_stubs(adapter_cls) -> dict[str, DataSource]:
    return {name: adapter_cls(name) for name in _STUB_NAMES}


def _update_stub_window(project_root: Path, adapter_cls=StubAdapter):
    """One offline stub update over the fixture project's whole window."""
    return DataPipeline(project_root, sources=_named_stubs(adapter_cls)).update(
        DataUpdateRequest(end_date=BARS_END)
    )


def _version_bytes(project_root: Path, version: str) -> dict[str, bytes]:
    """Every file byte under one published version directory."""
    version_dir = project_root / "data" / "standardized" / version
    return {
        str(path.relative_to(version_dir)): path.read_bytes()
        for path in sorted(version_dir.rglob("*"))
        if path.is_file()
    }


def _fact_row(factor: pd.DataFrame, symbol: str, day: str) -> pd.DataFrame:
    """The one factor row for a (symbol, trade_date) key, dtype-agnostic."""
    day_mask = pd.to_datetime(factor["trade_date"]) == pd.Timestamp(day)
    return factor[day_mask & (factor["symbol"] == symbol)]


def test_validate_cli_passes_on_the_published_stub_window(tmp_path):
    """The stub-published window passes the real ``data validate`` CLI.

    §7.4 "真实小窗口 raw→新 version→validate 通过" in its offline form: one
    stub update publishes a new version as CURRENT, then the same CLI
    subprocess an operator runs (``python -m stock_quant data validate
    --root <project>``) re-checks that version and exits 0 with PASS.
    """
    project = build_fixture_project(tmp_path / "project")
    result = _update_stub_window(project.root)
    assert result.dataset_ref is not None
    version = result.dataset_ref.version
    completed = subprocess.run(
        [sys.executable, "-m", "stock_quant", "data", "validate",
         "--root", str(project.root)],
        capture_output=True, text=True, timeout=600,
        cwd=str(project.root),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f"version={version}" in completed.stdout
    assert "PASS" in completed.stdout


def test_republishing_the_round_payload_reuses_the_version_never_rewrites(
    tmp_path,
):
    """The same round payload hashes to the same version, byte for byte.

    §7.4 "相同输入重跑哈希不变;旧版本目录字节不动".  The content-addressed
    identity is the publish boundary: the exact (tables, build_config)
    payload of the stub round, republished, hashes to the same
    ``dataset_version`` and the publisher stages nothing over the existing
    directory.  (A second ``update`` round is a distinct build event by
    design -- its ``run_id`` and reuse evidence are identity-bearing -- so
    the invariance claim is judged on the payload, like
    ``test_dataset_publish.py``'s idempotency test.)
    """
    project = build_fixture_project(tmp_path / "project")
    result = _update_stub_window(project.root)
    assert result.dataset_ref is not None
    version = result.dataset_ref.version
    before = _version_bytes(project.root, version)
    with DatasetReader(project.root).open(version) as context:
        tables = {name: context.read(name) for name in context.tables}
        build_config = context.manifest["build_config"]
    republished = DatasetPublisher(project.root).publish(
        tables, QualityReport(), build_config=build_config
    )
    assert republished.version == version
    assert republished.path == (
        project.root / "data" / "standardized" / version
    )
    assert _version_bytes(project.root, version) == before


def test_single_fact_change_yields_new_version_and_keeps_old_bytes(tmp_path):
    """One changed total_mv-derived fact moves the version, not old bytes.

    §7.4 "单源事实变化出新版本且旧版本不动".  An incremental round carries
    its covered facts (``last_covered_plus_1``), so the fact-level identity
    change is exercised at the publish boundary over the real stub round
    payload: exactly one ``basic_factor`` ``market_cap`` value (the
    probe-symbol session's ``total_mv`` × the probe-frozen unit factor)
    changes, the version hash changes, and the old version directory keeps
    its bytes with the old fact.
    """
    project = build_fixture_project(tmp_path / "project")
    result = _update_stub_window(project.root)
    assert result.dataset_ref is not None
    old_version = result.dataset_ref.version
    old_bytes = _version_bytes(project.root, old_version)
    with DatasetReader(project.root).open(old_version) as context:
        tables = {name: context.read(name) for name in context.tables}
        build_config = context.manifest["build_config"]
    factor = tables["basic_factor"]
    row = (
        (pd.to_datetime(factor["trade_date"]) == pd.Timestamp(_MUTATED_DAY))
        & (factor["symbol"] == _PROBE_SYMBOL)
    )
    assert row.sum() == 1
    changed = factor.copy()
    changed.loc[row, "market_cap"] = _MUTATED_TOTAL_MV * MARKET_CAP_UNIT_FACTOR
    tables["basic_factor"] = changed
    republished = DatasetPublisher(project.root).publish(
        tables, QualityReport(), build_config=build_config
    )
    assert republished.version != old_version
    assert _version_bytes(project.root, old_version) == old_bytes
    with DatasetReader(project.root).open(republished.version) as context:
        moved = _fact_row(
            context.read("basic_factor"), _PROBE_SYMBOL, _MUTATED_DAY
        )
        assert moved["market_cap"].tolist() == [
            _MUTATED_TOTAL_MV * MARKET_CAP_UNIT_FACTOR
        ]
    with DatasetReader(project.root).open(old_version) as context:
        kept = _fact_row(
            context.read("basic_factor"), _PROBE_SYMBOL, _MUTATED_DAY
        )
        assert kept["market_cap"].tolist() == [
            15000_0000.0 * MARKET_CAP_UNIT_FACTOR
        ]


def test_missing_daily_basic_row_lands_untrusted_fact_coverage(tmp_path):
    """One missing fact row: UNTRUSTED coverage, publish proceeds.

    §7.4 "应有而无落 UNTRUSTED(FACTS_INCOMPLETE)" on the publish side: the
    stub answers every ``daily_basic`` row except the probe symbol's one
    session.  The round still publishes (the covering UNTRUSTED row is the
    join check's legal answer for the bar-day lacking its factor row), the
    coverage table marks exactly that symbol's round window
    FACTS_INCOMPLETE, and the missing key is absent from the facts while
    every other symbol stays complete.  The fail-closed consumer half (a
    RESEARCH preflight rejecting the table, ENGINEERING exempt and labeled)
    is pinned in ``tests/integration/test_table_tier_preflight.py``.
    """
    project = build_fixture_project(tmp_path / "project")
    result = _update_stub_window(project.root, _MissingRowAdapter)
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as ctx:
        coverage = ctx.read("basic_factor_coverage")
        untrusted = coverage[coverage["status"] == "UNTRUSTED"]
        assert len(untrusted) == 1
        row = untrusted.iloc[0]
        assert row["symbol"] == _PROBE_SYMBOL
        assert row["reason"] == "FACTS_INCOMPLETE"
        assert pd.Timestamp(row["window_start"]) <= pd.Timestamp(_MISSING_DAY)
        assert pd.Timestamp(row["window_end"]) >= pd.Timestamp(_MISSING_DAY)
        verified = coverage[coverage["status"] == "VERIFIED"]
        assert len(verified) == len(_FIXTURE_UNIVERSE_SYMBOLS) - 1
        factor = ctx.read("basic_factor")
        assert _fact_row(factor, _PROBE_SYMBOL, _MISSING_DAY).empty
        per_symbol = factor.groupby("symbol").size()
        assert per_symbol[_PROBE_SYMBOL] == per_symbol.max() - 1
        assert per_symbol.drop(_PROBE_SYMBOL).nunique() == 1


def test_all_null_total_mv_publishes_nulls_without_fill_zero(tmp_path):
    """A whole-null total_mv window publishes nulls, never fill-zero.

    §7.4 "缺失保 null;无 fill-zero、无可信空值空洞": the stub answers every
    row with a null ``total_mv``; the normalized facts keep the null (the
    probe-frozen unit scaling maps null to null), the answered keys count as
    present so the coverage verdicts stay VERIFIED -- a null value with
    evidence is not a trust hole -- and the turnover column is untouched.
    """
    project = build_fixture_project(tmp_path / "project")
    result = _update_stub_window(project.root, _NullTotalMvAdapter)
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as ctx:
        factor = ctx.read("basic_factor")
        assert not factor.empty
        assert factor["market_cap"].isna().all()
        assert (factor["market_cap"] == 0).sum() == 0
        assert not factor["turnover_rate"].isna().any()
        coverage = ctx.read("basic_factor_coverage")
        assert (coverage["status"] == "VERIFIED").all()
        assert not (coverage["status"] == "UNTRUSTED").any()
