"""Integration behaviour of the offline automated acceptance checks (Task 4).

``run_automated_checks`` runs over a *real* pipeline build: a synthetic project
(the repository configs plus the shared fixture baseline) is updated with stub
``DataSource`` adapters that never touch a network or a token, so the pinned
dataset carries genuine ``build_config`` provenance -- raw-snapshot bindings
under ``data/raw`` and structured per-source statuses.  Semantic mutations
republish a mutated copy of that dataset through ``DatasetPublisher`` (the
repository's ``_republish_current_tables`` pattern) and point the checker at
the fresh version, so every semantic check is shown to fail closed on exactly
the evidence it owns.  All failure details stay deterministic and redacted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from conftest import build_fixture_project  # noqa: E402

from stock_quant.config import load_project_config
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.security_master import master_coverage_frame
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import DataPipeline, DataUpdateRequest
from stock_quant.data_quality.models import QualityReport
from stock_quant.data_sources.base import (
    DataRequest,
    DataSource,
    FetchResult,
    request_key,
)
from stock_quant.research.acceptance.checks import (
    AcceptanceCheckInput,
    run_automated_checks,
)
from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    CheckStatus,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The update window every fixture dataset is built over (inside the fixture
#: calendar and bars span, so the resolved window is fully covered).
_WINDOW_START = date(2021, 11, 1)
_WINDOW_END = date(2021, 11, 30)

#: The repository fixture universe the stub ``stock_basic`` answer must cover.
_FIXTURE_UNIVERSE_SYMBOLS = tuple(
    Universe.from_yaml(
        _REPO_ROOT / "configs" / "universe.yml"
    ).symbols
)
_STOCK_BASIC_LIST_DATE = date(2001, 1, 2)


# --------------------------------------------------------------------------- #
# Offline stub suppliers (same contract as test_data_pipeline.py)
# --------------------------------------------------------------------------- #


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
                "transport_id": self.name,
            },
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
            "stock_metadata",
        ):
            return pd.DataFrame()
        rows: list[dict[str, object]] = []
        for day in sessions:
            rows.append(
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
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _index_frame(sessions: list[date]) -> pd.DataFrame:
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

    def _stock_basic_frame(self) -> pd.DataFrame:
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


def _all_stubs() -> dict[str, DataSource]:
    return {
        name: StubAdapter(name)
        for name in ("tushare", "akshare", "baostock")
    }


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AcceptanceProject:
    """One synthetic project pinned to its update-published dataset."""

    root: Path
    version: str


def _published_project(root: Path) -> AcceptanceProject:
    """Baseline fixture project plus one real stubbed ``data update``."""
    base = build_fixture_project(root)
    result = DataPipeline(base.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is not None
    return AcceptanceProject(root=base.root, version=result.dataset_ref.version)


@pytest.fixture
def project(tmp_path):
    """A fresh update-published project per test (mutations republish)."""
    return _published_project(tmp_path / "project")


def _input(value: AcceptanceProject) -> AcceptanceCheckInput:
    return AcceptanceCheckInput(
        project_root=value.root, dataset_version=value.version
    )


def _checks_by_code(checks) -> dict[str, object]:
    return {check.code: check for check in checks}


# --------------------------------------------------------------------------- #
# Manifest / quality-report integrity (plan Task 4 Steps 1-2)
# --------------------------------------------------------------------------- #


def test_manifest_integrity_passes_for_untouched_dataset(project):
    checks = _checks_by_code(run_automated_checks(_input(project)))
    assert checks["dataset_manifest_integrity"].status is CheckStatus.PASS
    assert checks["quality_report_integrity"].status is CheckStatus.PASS


def test_manifest_integrity_fails_when_table_hash_changes(project):
    # The plan's tamper target is ``daily_bar``: appending bytes corrupts the
    # Parquet footer, so with a cold DuckDB catalog even binding the table's
    # view crashes (``duckdb.InvalidInputException`` out of ``_build_catalog``
    # via ``reader.open``) and with a warm catalog the same error moves to
    # ``context.read("daily_bar")``.  The runner must survive both and fail
    # exactly the checks that read the table.  Catalogs live under ``/tmp``
    # keyed by the resolved standardized root plus version, and every test
    # builds its own ``tmp_path`` project, so this exercises the cold case
    # without any warming and never depends on test order.
    table = (
        project.root
        / "data"
        / "standardized"
        / project.version
        / "daily_bar.parquet"
    )
    table.write_bytes(table.read_bytes() + b"tamper")
    checks = _checks_by_code(run_automated_checks(_input(project)))
    assert len(checks) == len(AUTOMATED_CHECK_CODES)
    manifest_check = checks["dataset_manifest_integrity"]
    assert manifest_check.status is CheckStatus.FAIL
    assert manifest_check.details["code"] == "table_hash_mismatch"
    assert ["table_hash_mismatch", "daily_bar"] in (
        manifest_check.details["failures"]
    )
    quality_check = checks["quality_report_integrity"]
    assert quality_check.status is CheckStatus.FAIL
    assert quality_check.details == {"error_code": "InvalidInputException"}


# --------------------------------------------------------------------------- #
# Policy order and determinism (plan Task 4 Step 7)
# --------------------------------------------------------------------------- #


def test_healthy_dataset_passes_every_check_in_policy_order(project):
    checks = run_automated_checks(_input(project))
    assert [row.code for row in checks] == list(AUTOMATED_CHECK_CODES)
    failed = [row.code for row in checks if row.status is CheckStatus.FAIL]
    assert failed == []


def test_runner_output_is_deterministic_and_redacted(project):
    first = run_automated_checks(_input(project))
    second = run_automated_checks(_input(project))
    assert first == second
    for row in first:
        payload = json.dumps(row.model_dump(mode="json"), sort_keys=True)
        assert str(project.root) not in payload
        assert "Traceback" not in payload


# --------------------------------------------------------------------------- #
# Semantic evidence mutations (plan Task 4 Steps 5-6)
# --------------------------------------------------------------------------- #


def _apply_mutation(
    mutation: str,
    value: AcceptanceProject,
    tables: dict,
    build_config: dict,
) -> None:
    """Rewrite one evidence dimension of the staged tables/build config."""
    if mutation == "remove_benchmark":
        benchmarks = set(load_project_config(value.root).benchmark_symbols)
        daily = tables["daily_bar"]
        tables["daily_bar"] = daily.loc[
            ~daily["symbol"].isin(benchmarks)
        ].reset_index(drop=True)
    elif mutation == "remove_master_coverage":
        tables["security_master_coverage"] = master_coverage_frame([])
    elif mutation == "mark_action_coverage_untrusted":
        coverage = tables["corporate_action_coverage"].copy()
        coverage["status"] = "UNTRUSTED"
        coverage["reason"] = "SOURCE_FETCH_FAILED"
        tables["corporate_action_coverage"] = coverage
    elif mutation == "remove_raw_snapshot":
        build_config["raw_snapshots"] = []
    elif mutation == "fail_required_source":
        for row in build_config["source_status"]:
            if row["source"] == "tushare":
                row["ok"] = False
                row["reason_code"] = "source_fetch_failed"
    elif mutation == "mark_bootstrap_origin":
        build_config["origin"] = "bootstrap"
    else:
        raise AssertionError(f"unknown mutation {mutation!r}")


@pytest.fixture
def mutated_project(project):
    """Return a factory republishing a mutated copy of the pinned dataset.

    Imitates the repository's ``_republish_current_tables`` pattern: the
    current tables and ``build_config`` are staged, one dimension is mutated,
    and ``DatasetPublisher`` publishes the result as a fresh immutable version
    the checker is then pointed at.
    """

    def build(mutation: str) -> AcceptanceProject:
        publisher = DatasetPublisher(project.root)
        with DatasetReader(project.root).open(project.version) as context:
            tables = {name: context.read(name) for name in context.tables}
        manifest_path = (
            publisher.standardized_root
            / project.version
            / "dataset_manifest.json"
        )
        build_config = json.loads(manifest_path.read_text())["build_config"]
        _apply_mutation(mutation, project, tables, build_config)
        reference = publisher.publish(
            tables, QualityReport(), build_config=build_config
        )
        return AcceptanceProject(root=project.root, version=reference.version)

    return build


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("remove_benchmark", "date_window_completeness"),
        ("remove_master_coverage", "security_master_evidence"),
        ("mark_action_coverage_untrusted", "corporate_action_evidence"),
        ("remove_raw_snapshot", "raw_snapshot_traceability"),
        ("fail_required_source", "source_role_health"),
        ("mark_bootstrap_origin", "source_role_health"),
    ],
)
def test_semantic_check_fails_closed(mutated_project, mutation, code):
    project = mutated_project(mutation)
    checks = _checks_by_code(run_automated_checks(_input(project)))
    assert checks[code].status is CheckStatus.FAIL
