"""Unit behaviour of ``stock_quant.data_pipeline`` (Task 13 Step 3).

Imported before ``stock_quant.data_pipeline`` exists so the file fails during
import in Step 2.  ``resolve_latest_complete_date`` is tested as a pure
function; ``DataPipeline.update`` / ``DataPipeline.validate`` run against stub
``DataSource`` adapters that never touch a network or a token.  Every test
builds its own synthetic project (updates change ``CURRENT``, so they must not
share the session dataset used by the CLI / end-to-end modules).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import pytest
import yaml
from conftest import build_fixture_project  # noqa: E402

from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import (  # noqa: F401  (gates Step 2 collection)
    CODE_OPTIONAL_SOURCE_FAILURE,
    CODE_SOURCE_FETCH_FAILED,
    CODE_UNIVERSE_MASTER_MISMATCH,
    DataPipeline,
    DataUpdateRequest,
    SourceCoverage,
    resolve_latest_complete_date,
)
from stock_quant.data_quality.models import (
    CODE_NONPOSITIVE_PRICE,
    QualityReport,
    Severity,
)
from stock_quant.data_sources.base import (
    AuthenticationError,
    DataRequest,
    DataSource,
    FetchResult,
    ServerError,
    request_key,
)


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


_WINDOW_START = date(2021, 11, 1)
_WINDOW_END = date(2021, 11, 30)


# --------------------------------------------------------------------------- #
# Small stub supplier that answers per-symbol raw requests deterministically
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StubAdapter:
    """A ``DataSource`` whose ``fetch`` returns deterministic raw frames.

    ``raise_with`` forces a permanent/transient supplier failure on every
    request; ``failing_endpoints`` narrows a failure to the named endpoints
    only (so an action endpoint can fail while the same supplier still serves
    its benchmark history); ``negative_close_symbol`` injects one illegal
    (negative-close) bar so the publication gate blocks.
    """

    name: str
    raise_with: type[Exception] | None = None
    negative_close_symbol: str | None = None
    failing_endpoints: tuple[str, ...] = ()
    calls: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", [])

    def fetch(self, request: DataRequest) -> FetchResult:
        self.calls.append((request.endpoint, request.symbols[0]))
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
            metadata={"source": self.name},
        )

    def _frame(self, request: DataRequest) -> pd.DataFrame:
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
            close = -55.0 if symbol == self.negative_close_symbol else 55.0
            rows.append(
                {
                    "code": symbol,
                    "date": day.strftime("%Y%m%d"),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1000,
                    "amount": close * 1000.0,
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


def _all_stubs(**overrides) -> dict[str, DataSource]:
    stubs: dict[str, DataSource] = {
        "tushare": StubAdapter("tushare"),
        "akshare": StubAdapter("akshare"),
        "baostock": StubAdapter("baostock"),
    }
    for name, override in overrides.items():
        if override is not None:
            stubs[name] = override
    return stubs


def _successful_empty_action_sources() -> dict[str, DataSource]:
    """Every source healthy; both action endpoints answer with no events."""
    return _all_stubs()


def _one_failing_action_endpoint() -> dict[str, DataSource]:
    """The CNINFO action endpoint fails per symbol while akshare still serves
    its benchmark history and the Eastmoney cross-check answers empty."""
    failing = StubAdapter(
        "akshare", failing_endpoints=("cninfo_corporate_actions",)
    )
    return _all_stubs(akshare=failing)


@pytest.fixture
def project(tmp_path):
    """A fresh synthetic project per test (updates republish ``CURRENT``)."""
    return build_fixture_project(tmp_path / "project")


# --------------------------------------------------------------------------- #
# resolve_latest_complete_date
# --------------------------------------------------------------------------- #


def _freeze_now(monkeypatch, iso: str) -> None:
    now = _dt.datetime.fromisoformat(iso)

    class _FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now.replace(tzinfo=tz)

    monkeypatch.setattr("stock_quant.data_pipeline._datetime", _FixedDatetime)


def _november_calendar() -> TradingCalendar:
    return TradingCalendar.from_open_days(
        tuple(_weekdays(date(2021, 11, 1), date(2021, 11, 30)))
    )


def test_resolver_returns_newest_complete_open_day_after_publication_time(
    monkeypatch,
):
    calendar = _november_calendar()
    latest = calendar.open_days[-1]
    coverage = SourceCoverage(
        stock_primary=latest,
        benchmarks={"000300.SH": latest, "000905.SH": latest},
        validation={"baostock": latest},
    )
    _freeze_now(monkeypatch, "2021-11-30T15:30:00")
    assert resolve_latest_complete_date(
        coverage, calendar, _dt.time(15, 0)
    ) == latest


def test_resolver_waits_until_publication_time_for_today(monkeypatch):
    calendar = _november_calendar()
    latest = calendar.open_days[-1]
    coverage = SourceCoverage(
        stock_primary=latest,
        benchmarks={"000300.SH": latest, "000905.SH": latest},
        validation={"baostock": latest},
    )
    _freeze_now(monkeypatch, "2021-11-30T09:30:00")  # before the 15:00 gate
    assert resolve_latest_complete_date(
        coverage, calendar, _dt.time(15, 0)
    ) == calendar.open_days[-2]


def test_resolver_falls_back_when_a_required_series_lags(monkeypatch):
    calendar = _november_calendar()
    stock_latest = calendar.open_days[-3]
    coverage = SourceCoverage(
        stock_primary=stock_latest,
        benchmarks={
            "000300.SH": calendar.open_days[-1],
            "000905.SH": calendar.open_days[-1],
        },
        validation={"baostock": calendar.open_days[-1]},
    )
    _freeze_now(monkeypatch, "2021-11-30T15:30:00")
    assert resolve_latest_complete_date(
        coverage, calendar, _dt.time(15, 0)
    ) == stock_latest


def test_resolver_is_limited_by_a_late_validation_source(monkeypatch):
    calendar = _november_calendar()
    validation_latest = calendar.open_days[-2]
    coverage = SourceCoverage(
        stock_primary=calendar.open_days[-1],
        benchmarks={
            "000300.SH": calendar.open_days[-1],
            "000905.SH": calendar.open_days[-1],
        },
        validation={"baostock": validation_latest},
    )
    _freeze_now(monkeypatch, "2021-11-30T15:30:00")
    assert resolve_latest_complete_date(
        coverage, calendar, _dt.time(15, 0)
    ) == validation_latest


def test_resolver_returns_none_without_required_coverage(monkeypatch):
    calendar = _november_calendar()
    coverage = SourceCoverage(
        stock_primary=None,
        benchmarks={"000300.SH": calendar.open_days[-1]},
        validation={},
    )
    _freeze_now(monkeypatch, "2021-11-30T15:30:00")
    assert (
        resolve_latest_complete_date(coverage, calendar, _dt.time(15, 0))
        is None
    )


def test_resolver_uses_previous_open_day_when_today_is_a_weekend(monkeypatch):
    """A non-trading today never waits for the publication-time gate."""
    calendar = _november_calendar()
    latest = calendar.open_days[-1]  # 2021-11-30 (Tuesday)
    coverage = SourceCoverage(
        stock_primary=latest,
        benchmarks={"000300.SH": latest, "000905.SH": latest},
        validation={"akshare": latest},
    )
    # Saturday 2021-12-04, well before the 15:00 gate.
    _freeze_now(monkeypatch, "2021-12-04T09:00:00")
    assert (
        resolve_latest_complete_date(coverage, calendar, _dt.time(15, 0))
        == latest
    )


def test_resolver_walks_back_from_a_non_trading_weekend_today(monkeypatch):
    """A lagging required series still walks back over a weekend today."""
    calendar = _november_calendar()
    stock_latest = calendar.open_days[-2]
    coverage = SourceCoverage(
        stock_primary=stock_latest,
        benchmarks={
            "000300.SH": calendar.open_days[-1],
            "000905.SH": calendar.open_days[-1],
        },
        validation={"akshare": calendar.open_days[-1]},
    )
    _freeze_now(monkeypatch, "2021-12-04T09:00:00")
    assert (
        resolve_latest_complete_date(coverage, calendar, _dt.time(15, 0))
        == stock_latest
    )


def test_resolver_returns_none_for_an_empty_calendar():
    calendar = TradingCalendar.from_open_days(())
    coverage = SourceCoverage(
        stock_primary=None,
        benchmarks={},
        validation={},
    )
    assert (
        resolve_latest_complete_date(coverage, calendar, _dt.time(15, 0))
        is None
    )


# --------------------------------------------------------------------------- #
# DataPipeline.update / validate over a fresh synthetic project
# --------------------------------------------------------------------------- #


def _request() -> DataUpdateRequest:
    return DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)


def test_update_with_explicit_end_publishes_merged_dataset(project):
    pipeline = DataPipeline(project.root, sources=_all_stubs())
    result = pipeline.update(_request())
    assert result.dataset_ref is not None
    assert result.dataset_ref.version != project.version
    assert result.resolved_end_date == _WINDOW_END
    assert all(status.ok for status in result.source_status)


def test_update_records_empty_success_and_fetch_failure(project):
    ok = DataPipeline(
        project.root, sources=_successful_empty_action_sources()
    ).update(_request())
    bad = DataPipeline(
        project.root, sources=_one_failing_action_endpoint()
    ).update(_request())
    with DatasetReader(project.root).open(ok.dataset_ref.version) as context:
        assert set(context.read("corporate_action_coverage").status) == {
            "VERIFIED_EMPTY"
        }
    with DatasetReader(project.root).open(bad.dataset_ref.version) as context:
        assert "SOURCE_FETCH_FAILED" in set(
            context.read("corporate_action_coverage").reason
        )


def test_update_blocked_records_raw_responses(project):
    negative = StubAdapter("tushare", negative_close_symbol="600000.SH")
    pipeline = DataPipeline(project.root, sources=_all_stubs(tushare=negative))
    result = pipeline.update(_request())
    assert result.dataset_ref is None
    assert CODE_NONPOSITIVE_PRICE in result.quality_report.by_code()
    assert result.raw_snapshots, "blocked run must still record adapter responses"


def test_required_source_failure_blocks_update(project):
    failing = StubAdapter("tushare", raise_with=AuthenticationError)
    pipeline = DataPipeline(project.root, sources=_all_stubs(tushare=failing))
    result = pipeline.update(_request())
    assert result.dataset_ref is None
    assert CODE_SOURCE_FETCH_FAILED in result.quality_report.by_code()
    tushare_status = next(
        status for status in result.source_status if status.source == "tushare"
    )
    assert not tushare_status.ok and tushare_status.required


def test_optional_validation_failure_still_publishes(project):
    failing = StubAdapter("baostock", raise_with=ServerError)
    pipeline = DataPipeline(project.root, sources=_all_stubs(baostock=failing))
    result = pipeline.update(_request())
    assert result.dataset_ref is not None
    assert CODE_OPTIONAL_SOURCE_FAILURE in result.quality_report.by_code()
    baostock_status = next(
        status for status in result.source_status if status.source == "baostock"
    )
    assert not baostock_status.ok and not baostock_status.required


def test_disabled_required_source_is_never_called(tmp_path):
    project = build_fixture_project(tmp_path / "project")
    sources_path = project.root / "configs" / "sources.yml"
    sources_path.write_text(
        yaml.safe_dump(
            {
                "tushare": {"enabled": False},
                "akshare": {"enabled": True},
                "baostock": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    tushare_stub = StubAdapter("tushare")
    pipeline = DataPipeline(project.root, sources=_all_stubs(tushare=tushare_stub))
    result = pipeline.update(_request())
    assert tushare_stub.calls == []
    assert result.dataset_ref is None
    tushare_status = next(
        status for status in result.source_status if status.source == "tushare"
    )
    assert not tushare_status.ok and tushare_status.required


def test_validate_returns_clean_report_over_current_dataset(project):
    pipeline = DataPipeline(project.root)
    report = pipeline.validate()
    assert report.by_severity()[Severity.ERROR.value] == 0
    assert report.by_severity()[Severity.FATAL.value] == 0


def _rewrite_mismatched_universe(root) -> None:
    """Drop two pinned symbols and add one never-listed synthetic symbol.

    The rewritten ``configs/universe.yml`` disagrees with the fixture's
    ``security_master`` on both sides: the dropped symbols appear only in the
    master and the synthetic symbol appears only in the universe.
    """
    path = root / "configs" / "universe.yml"
    universe = Universe.from_yaml(path)
    entries = [entry.model_dump(mode="json") for entry in universe.entries]
    clone = dict(entries[-1])
    clone["symbol"] = "688999.SH"
    clone["name_at_selection"] = "合成新增样本"
    document = {
        "selected_as_of": entries[0]["selected_as_of"],
        "entries": entries[:-2] + [clone],
    }
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def test_validate_surfaces_universe_master_mismatch_as_fatal(project):
    _rewrite_mismatched_universe(project.root)
    pipeline = DataPipeline(project.root)
    report = pipeline.validate()
    assert report.by_code()[CODE_UNIVERSE_MASTER_MISMATCH] == 1
    issue = next(
        item
        for item in report.issues
        if item.code == CODE_UNIVERSE_MASTER_MISMATCH
    )
    assert issue.severity is Severity.FATAL
    assert issue.table == "security_master"
    assert issue.details["universe_symbol_count"] == 29
    assert issue.details["security_master_symbol_count"] == 30
    assert issue.details["missing_from_security_master"] == ["688999.SH"]
    assert issue.details["extra_in_security_master"] == [
        "688506.SH",
        "688981.SH",
    ]


def test_update_blocks_publication_when_universe_mismatches_master(project):
    _rewrite_mismatched_universe(project.root)
    pipeline = DataPipeline(project.root, sources=_all_stubs())
    result = pipeline.update(_request())
    assert result.dataset_ref is None
    assert result.quality_report.by_code()[CODE_UNIVERSE_MASTER_MISMATCH] == 1


def test_discovery_passes_configured_publication_time_to_resolver(
    project, monkeypatch
):
    """The configured ``publication_time`` -- not a hard-coded 15:00 -- governs."""
    config_path = project.root / "configs" / "project.yml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["publication_time"] = "23:59"
    config_path.write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )

    captured: dict = {}

    def fake_resolve(status, calendar, publication_time):
        captured["publication_time"] = publication_time
        return date(2021, 11, 30)

    monkeypatch.setattr(
        "stock_quant.data_pipeline.resolve_latest_complete_date", fake_resolve
    )
    pipeline = DataPipeline(project.root, sources=_all_stubs())
    result = pipeline.update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=None)
    )
    assert captured["publication_time"] == _dt.time(23, 59)
    assert result.dataset_ref is not None
    assert result.resolved_end_date == date(2021, 11, 30)


def _republish_with_benchmark_trim(root, symbol, cutoff) -> None:
    """Re-publish CURRENT with one benchmark symbol trimmed to ``<= cutoff``."""
    reader = DatasetReader(root)
    version = DatasetPublisher(root).current().version
    with reader.open(version) as context:
        daily = context.read("daily_bar")
        master = context.read("security_master")
        ca = context.read("corporate_action")
        calendar = context.read("trading_calendar")
    keep = ~(
        (daily["symbol"] == symbol)
        & (daily["trade_date"] > pd.Timestamp(cutoff))
    )
    trimmed = daily.loc[keep].reset_index(drop=True)
    DatasetPublisher(root).publish(
        {
            "daily_bar": trimmed,
            "security_master": master,
            "corporate_action": ca,
            "trading_calendar": calendar,
        },
        QualityReport(),
    )


def test_discovery_reports_fallback_when_a_benchmark_series_lags(project, monkeypatch):
    """A lagging required benchmark walks the end date back and flags it."""
    _freeze_now(monkeypatch, "2022-01-07T16:00:00")
    _republish_with_benchmark_trim(project.root, "000300.SH", date(2022, 1, 5))
    pipeline = DataPipeline(project.root, sources=_all_stubs())
    result = pipeline.update(
        DataUpdateRequest(start_date=date(2021, 11, 1), end_date=None)
    )
    assert result.dataset_ref is not None
    assert result.resolved_end_date == date(2022, 1, 5)
    assert result.resolved_end_is_fallback is True
