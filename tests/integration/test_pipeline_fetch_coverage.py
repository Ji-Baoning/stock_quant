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
        if request.endpoint == "trade_cal":
            return self._trade_cal_frame(request)
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
    return {
        name: StubAdapter(name)
        for name in ("tushare", "akshare", "baostock")
    }


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
