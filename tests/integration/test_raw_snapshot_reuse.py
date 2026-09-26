"""A retry round reuses the stored answers of the round before it (ADR-015).

The fetch layer consults the raw store before the network for the admitted
channels only; a failed round leaves its snapshots behind and the retry
round must not pay for them again.  Corporate actions, the calendar and the
security master stay live every round, and a tampered snapshot is refused
(never silently reused, never silently replaced) before the live fetch runs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from conftest import (  # noqa: E402
    BARS_END,
    BARS_START,
    _UPDATE_WINDOW_END,
    _UPDATE_WINDOW_START,
    build_fixture_project,
)

from stock_quant.data_model.fetch_coverage import validate_table_fetch_coverage
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import (
    CODE_REUSE_CANDIDATE_REJECTED,
    CODE_SOURCE_FETCH_FAILED,
    DataPipeline,
    DataUpdateRequest,
)
from stock_quant.data_sources.base import (
    DataRequest,
    DataSource,
    FetchResult,
    request_key,
    request_metadata,
)
from stock_quant.data_sources.raw_store import RawSnapshotEvidence, _sha256_file

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

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", [])

    def fetch(self, request: DataRequest) -> FetchResult:
        self.calls.append(
            {
                "endpoint": request.endpoint,
                "symbol": request.symbols[0] if request.symbols else None,
            }
        )
        frame = self._frame(request)
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=frame,
            # The real adapter metadata contract: ``_manifest_for`` parses
            # ``request_parameters`` out of this, and the defensive reuse
            # check refuses any snapshot whose manifest lacks it.
            metadata=request_metadata(
                request,
                f"{self.name}.stub.endpoint",
                "stub",
                transport_id=self.name,
            ),
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


class _DailyFailAfter:
    """Answers like the wrapped stub until the daily quota is exhausted.

    The first ``after`` ``daily`` requests go through; every later one raises
    like a rate-limited supplier would, which makes the required primary lane
    fail the round while the already-answered symbols keep their snapshots.
    """

    def __init__(self, inner: StubAdapter, after: int) -> None:
        self.inner = inner
        self.after = after
        self.name = inner.name
        self.calls = inner.calls

    def fetch(self, request: DataRequest) -> FetchResult:
        if request.endpoint == "daily":
            served = len(
                [
                    entry
                    for entry in self.calls
                    if entry["endpoint"] == "daily"
                ]
            )
            if served >= self.after:
                raise RuntimeError("tushare daily rate limited")
        return self.inner.fetch(request)


class _DailyVolumeDrift:
    """Answers like the wrapped stub, but daily volume comes back doubled.

    This is the supplier-revision shape: same request, different bytes, so a
    post-tamper live fetch lands beside the refused snapshot instead of
    collapsing onto it.
    """

    def __init__(self, inner: StubAdapter) -> None:
        self.inner = inner
        self.name = inner.name
        self.calls = inner.calls

    def fetch(self, request: DataRequest) -> FetchResult:
        result = self.inner.fetch(request)
        if request.endpoint == "daily" and not result.frame.empty:
            frame = result.frame.copy()
            frame["volume"] = frame["volume"] * 2
            result = replace(result, frame=frame)
        return result


def _all_stubs() -> dict[str, DataSource]:
    return {
        name: StubAdapter(name)
        for name in ("tushare", "akshare", "baostock")
    }


def _sources(tushare) -> dict[str, DataSource]:
    return {
        "tushare": tushare,
        "akshare": StubAdapter("akshare"),
        "baostock": StubAdapter("baostock"),
    }


def _request() -> DataUpdateRequest:
    # No explicit start: the corporate-action lanes keep their disclosure-
    # calendar contract window (an explicit start would skip them, F1), and
    # the end sits on the newest published open day.
    return DataUpdateRequest(end_date=BARS_END)


#: The fixture baseline pre-stores one ``tushare daily`` snapshot for a
#: bare ``{}``-params request over its own window.  It never shares a request
#: key with a real update (which always passes the adjustment param), so it
#: can never be reused -- but it does sit in the raw tree, and these tests
#: count only the snapshots the update rounds themselves produced.
_FIXTURE_DAILY_KEY = request_key(
    DataRequest(
        "daily", ("000001.SZ",), _UPDATE_WINDOW_START, _UPDATE_WINDOW_END, {}
    )
)


def _daily_snapshot_dirs(project_root: Path, key: str | None = None) -> list[Path]:
    pattern = "*/" + (key + "/*" if key else "*/*")
    return sorted(
        (project_root / "data" / "raw" / "tushare" / "daily").glob(pattern)
    )


def _manifest_build(project_root: Path, version: str) -> dict:
    manifest = json.loads(
        (
            project_root / "data" / "standardized" / version
            / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )
    return manifest["build_config"]


def _stored_daily_answers(project_root: Path) -> set[tuple[str, str]]:
    """The ``(request_key, file_sha256)`` pairs this project's rounds stored."""
    return {
        (path.parent.name, path.name)
        for path in _daily_snapshot_dirs(project_root)
        if path.parent.name != _FIXTURE_DAILY_KEY
    }


def test_a_retry_round_reuses_the_stored_prefix(tmp_path):
    """A blocked round's snapshots stand in for the retry round's calls.

    The first round dies on a rate-limited symbol after answering ten; the
    retry round must serve those ten from disk (zero adapter calls), fetch
    only the remainder live, keep the corporate-action endpoints live, bind
    the reused snapshots as its own evidence, and account for all of it in
    the build config and the call ledger.
    """
    project = build_fixture_project(tmp_path / "project")
    symbols = sorted(_FIXTURE_UNIVERSE_SYMBOLS)
    blocked = DataPipeline(
        project.root, sources=_sources(_DailyFailAfter(StubAdapter("tushare"), 10))
    ).update(_request())
    assert blocked.dataset_ref is None
    assert CODE_SOURCE_FETCH_FAILED in blocked.quality_report.by_code()
    stored = _stored_daily_answers(project.root)
    assert len(stored) == 10

    retry_tushare = StubAdapter("tushare")
    retry_akshare = StubAdapter("akshare")
    result = DataPipeline(
        project.root,
        sources={
            "tushare": retry_tushare,
            "akshare": retry_akshare,
            "baostock": StubAdapter("baostock"),
        },
    ).update(_request())
    assert result.dataset_ref is not None

    daily_calls = {
        entry["symbol"] for entry in retry_tushare.calls
        if entry["endpoint"] == "daily"
    }
    assert daily_calls == set(symbols[10:])

    # The revision-sensitive disclosure endpoints are live again this round:
    # reuse never stands in for a corporate-action refresh (design §2.3).
    action_endpoints = {
        entry["endpoint"] for entry in retry_akshare.calls
        if entry["endpoint"].endswith("_corporate_actions")
    }
    assert action_endpoints == {
        "cninfo_corporate_actions",
        "eastmoney_corporate_actions",
        "rights_issue_corporate_actions",
    }

    build = _manifest_build(project.root, result.dataset_ref.version)
    assert build["raw_snapshot_reuse"] == {
        "akshare": {"index_history": {"reused": 0, "fetched": 2}},
        "baostock": {"daily": {"reused": 0, "fetched": len(symbols)}},
        "tushare": {"daily": {"reused": 10, "fetched": len(symbols) - 10}},
    }
    evidence = {
        (row["source"], row["endpoint"], row["file_sha256"])
        for row in build["raw_snapshots"]
    }
    for key, sha in stored:
        assert ("tushare", "daily", sha) in evidence, key

    ledger = json.loads(
        (project.root / "data" / "runs" / result.run_id / "call_ledger.json")
        .read_text(encoding="utf-8")
    )
    assert ledger["tushare"]["calls"] == len(retry_tushare.calls)
    assert ledger["tushare"]["endpoints"]["daily"] == len(symbols) - 10
    assert ledger["tushare"]["reused"] == {"daily": 10}
    assert ledger["akshare"]["reused"] == {}
    assert ledger["baostock"]["reused"] == {}

    # The carried-plus-fetched tiling still validates offline.
    violations = validate_table_fetch_coverage(
        build["table_fetch_coverage"],
        anchor_start=BARS_START,
        published_end=BARS_END,
    )
    assert [v for v in violations if v[0] == "fetch_coverage_gap"] == []


def test_a_tampered_snapshot_is_refused_then_refetched_beside(tmp_path):
    """A tampered snapshot is a visible refusal, and the drift stays evidence.

    The refused candidate is not silently served, not silently deleted: the
    live refetch lands as a second digest under the same request key, the
    published round carries a ``reuse_candidate_rejected`` warning, and the
    reuse counters show one answer fewer.
    """
    project = build_fixture_project(tmp_path / "project")
    symbols = sorted(_FIXTURE_UNIVERSE_SYMBOLS)
    blocked = DataPipeline(
        project.root, sources=_sources(_DailyFailAfter(StubAdapter("tushare"), 3))
    ).update(_request())
    assert blocked.dataset_ref is None
    stored = _stored_daily_answers(project.root)
    assert len(stored) == 3
    # Tamper the snapshot of one named symbol: the daily request window is
    # the anchor start through the published end, so its evidence directory
    # is located by the same request key the pipeline would compute.
    victim = symbols[1]
    victim_key = request_key(
        DataRequest(
            "daily", (victim,), date(2020, 1, 1), BARS_END,
            {"adjustment": "unadjusted"},
        )
    )
    assert victim_key in {key for key, _ in stored}
    victim_dir = _daily_snapshot_dirs(project.root, victim_key)[0]
    (victim_dir / "data.parquet").write_bytes(b"tampered")

    published = DataPipeline(
        project.root,
        sources=_sources(_DailyVolumeDrift(StubAdapter("tushare"))),
    )
    result = published.update(_request())
    assert result.dataset_ref is not None

    # Two digests under one request key: the refused observation and the new
    # live answer, kept side by side as drift evidence (design §4.3).
    assert len(_daily_snapshot_dirs(project.root, victim_key)) == 2

    build = _manifest_build(project.root, result.dataset_ref.version)
    assert build["raw_snapshot_reuse"]["tushare"]["daily"] == {
        "reused": 2,
        "fetched": len(symbols) - 2,
    }
    assert CODE_REUSE_CANDIDATE_REJECTED in result.quality_report.by_code()
    rejected = [
        issue for issue in result.quality_report.issues
        if issue.code == CODE_REUSE_CANDIDATE_REJECTED
    ]
    assert [issue.symbol for issue in rejected] == [symbols[1]]


def test_a_reused_answer_equals_the_live_answer_it_stands_in_for(tmp_path):
    """Reuse changes where the bytes came from, not what they say.

    Two identical projects: one answers every request live; the other loses
    its first round after one symbol and reuses that answer in the retry.
    The reused daily rows match the live ones value for value (only
    ``ingested_at``, the observation timestamp, differs), and the published
    evidence row is the very snapshot the first round stored.
    """
    live_project = build_fixture_project(tmp_path / "live")
    live = DataPipeline(live_project.root, sources=_all_stubs()).update(_request())
    assert live.dataset_ref is not None

    resume_project = build_fixture_project(tmp_path / "resume")
    blocked = DataPipeline(
        resume_project.root,
        sources=_sources(_DailyFailAfter(StubAdapter("tushare"), 1)),
    ).update(_request())
    assert blocked.dataset_ref is None
    stored = _stored_daily_answers(resume_project.root)
    assert len(stored) == 1
    (key, sha) = next(iter(stored))
    manifest_path = next(
        _daily_snapshot_dirs(resume_project.root, key)[0].glob("manifest.json")
    )

    resumed = DataPipeline(
        resume_project.root, sources=_all_stubs()
    ).update(_request())
    assert resumed.dataset_ref is not None

    symbol = sorted(_FIXTURE_UNIVERSE_SYMBOLS)[0]
    live_rows = _symbol_rows(live_project.root, live.dataset_ref.version, symbol)
    reused_rows = _symbol_rows(
        resume_project.root, resumed.dataset_ref.version, symbol
    )
    columns = [column for column in live_rows.columns if column != "ingested_at"]
    pd.testing.assert_frame_equal(live_rows[columns], reused_rows[columns])
    assert (
        live_rows["ingested_at"].tolist() != reused_rows["ingested_at"].tolist()
    )

    stored_evidence = RawSnapshotEvidence(
        source="tushare",
        endpoint="daily",
        request_key=key,
        file_sha256=sha,
        manifest_sha256=_sha256_file(manifest_path),
        transport_id=json.loads(manifest_path.read_text(encoding="utf-8"))[
            "transport_id"
        ],
    )
    build = _manifest_build(resume_project.root, resumed.dataset_ref.version)
    rows = [
        row
        for row in build["raw_snapshots"]
        if row["source"] == "tushare" and row["endpoint"] == "daily"
        and row["request_key"] == key
    ]
    assert rows == [asdict(stored_evidence)]


def _symbol_rows(project_root: Path, version: str, symbol: str) -> pd.DataFrame:
    daily = pd.read_parquet(
        project_root / "data" / "standardized" / version / "daily_bar.parquet"
    )
    rows = daily.loc[daily["symbol"] == symbol].sort_values("trade_date")
    return rows.reset_index(drop=True)
