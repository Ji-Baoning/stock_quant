"""Unit behaviour of ``stock_quant.data_pipeline`` (Task 13 Step 3).

Imported before ``stock_quant.data_pipeline`` exists so the file fails during
import in Step 2.  ``DataPipeline.update`` / ``DataPipeline.validate`` run against stub
``DataSource`` adapters that never touch a network or a token.  Every test
builds its own synthetic project (updates change ``CURRENT``, so they must not
share the session dataset used by the CLI / end-to-end modules).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
import yaml
from conftest import CAL_END, CAL_START, build_fixture_project  # noqa: E402

from stock_quant.data_model.calendar_coverage import CODE_CALENDAR_COVERAGE_GAP
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.security_master import master_coverage_frame
from stock_quant.data_model.trade_calendar_facts import (
    CODE_CALENDAR_EXCHANGE_MISMATCH,
    CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_model.universe_membership import membership_frame
from stock_quant.data_pipeline import (  # noqa: F401  (gates Step 2 collection)
    CODE_ADJUSTED_BAR_MISSING_FROM_DATASET,
    CODE_CALENDAR_EMPTY_NO_END,
    CODE_MASTER_BAR_BOUNDARY,
    CODE_MASTER_COVERAGE_MISMATCH,
    CODE_MASTER_SNAPSHOT_INCOMPLETE,
    CODE_OPTIONAL_SOURCE_FAILURE,
    CODE_SOURCE_FETCH_FAILED,
    CODE_UNIVERSE_MASTER_MISMATCH,
    DataPipeline,
    DataUpdateRequest,
)
from stock_quant.data_quality.models import (
    CODE_NONPOSITIVE_PRICE,
    CODE_QUARANTINE_OUT_OF_WINDOW,
    QualityReport,
    Severity,
)
from stock_quant.data_quality.raw_checks import UNIVERSE_EVIDENCE_MISSING
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

#: The ex-date baked into the stub cross-confirmed cash-dividend frames.
_EVENT_DAY = date(2021, 11, 11)


#: The repository fixture universe every stub project is built from (30 symbols
#: mirrored from ``conftest._REPO_ROOT`` / ``configs/universe.yml``).
_FIXTURE_UNIVERSE_SYMBOLS = tuple(
    Universe.from_yaml(
        Path(__file__).resolve().parents[2] / "templates" / "project-config" / "universe.yml"
    ).symbols
)
_STOCK_BASIC_LIST_DATE = date(2001, 1, 2)


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
    (negative-close) bar so the publication gate blocks;
    ``action_frames`` maps a symbol to per-endpoint corporate-action frames
    (returned for ``cninfo_corporate_actions`` / ``eastmoney_corporate_actions``
    only, so one symbol can hold accepted plus quarantined events);
    ``stock_basic_symbols`` narrows the whole-market ``stock_basic`` response
    to a subset (defaults to the fixture universe) and
    ``stock_basic_list_date_by_symbol`` overrides a symbol's ``list_date``.
    The ``trade_cal_*`` knobs shape the per-exchange ``trade_cal`` halo
    responses (see ``_trade_cal_frame``).
    """

    name: str
    raise_with: type[Exception] | None = None
    negative_close_symbol: str | None = None
    failing_endpoints: tuple[str, ...] = ()
    action_frames: dict[str, dict[str, pd.DataFrame]] | None = None
    stock_basic_symbols: tuple[str, ...] | None = None
    stock_basic_list_date_by_symbol: dict[str, date] | None = None
    trade_cal_is_open_by_exchange: dict[tuple[str, date], int] | None = None
    trade_cal_failing_exchanges: tuple[str, ...] = ()
    trade_cal_missing_dates: tuple[date, ...] = ()
    trade_cal_pretrade_overrides: dict[date, str] | None = None
    calls: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "action_frames", self.action_frames or {})
        object.__setattr__(
            self,
            "stock_basic_list_date_by_symbol",
            self.stock_basic_list_date_by_symbol or {},
        )
        object.__setattr__(
            self,
            "trade_cal_is_open_by_exchange",
            self.trade_cal_is_open_by_exchange or {},
        )
        object.__setattr__(
            self,
            "trade_cal_pretrade_overrides",
            self.trade_cal_pretrade_overrides or {},
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
        if (
            request.endpoint == "trade_cal"
            and request.params.get("exchange") in self.trade_cal_failing_exchanges
        ):
            raise AuthenticationError(f"{self.name} supplier failure on trade_cal")
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

    def _trade_cal_frame(self, request: DataRequest) -> pd.DataFrame:
        """One row per halo natural day; weekends closed, pretrade chained.

        ``trade_cal_is_open_by_exchange`` is keyed by ``(exchange, date)`` so a
        test can make exactly one exchange disagree; the response carries no
        symbol scope, so the exchange is read from ``request.params``.
        """
        exchange = str(request.params.get("exchange"))
        assert exchange in ("SSE", "SZSE")
        rows: list[dict[str, object]] = []
        current = request.start_date
        while current <= request.end_date:
            if current not in self.trade_cal_missing_dates:
                previous = current - timedelta(days=1)
                while previous.weekday() >= 5:
                    previous -= timedelta(days=1)
                rows.append(
                    {
                        "exchange": exchange,
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": self.trade_cal_is_open_by_exchange.get(
                            (exchange, current), 1 if current.weekday() < 5 else 0
                        ),
                        "pretrade_date": self.trade_cal_pretrade_overrides.get(
                            current, previous.strftime("%Y%m%d")
                        ),
                    }
                )
            current += timedelta(days=1)
        return pd.DataFrame(rows)


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


def _all_action_endpoints_failing() -> dict[str, DataSource]:
    """Every corporate-action endpoint fails per symbol while akshare still
    serves its benchmark history (so the update publishes)."""
    failing = StubAdapter(
        "akshare",
        failing_endpoints=(
            "cninfo_corporate_actions",
            "eastmoney_corporate_actions",
        ),
    )
    return _all_stubs(akshare=failing)


# Native AKShare corporate-action columns (CNINFO primary, Eastmoney cross).
_ACTION_CNINFO_COLUMNS = [
    "证券代码",
    "证券简称",
    "公告日期",
    "股权登记日",
    "除权除息日",
    "派息(税前)(元/10股)",
    "送股(股/10股)",
    "转增(股/10股)",
    "进度",
    "方案",
]
_ACTION_EASTMONEY_COLUMNS = [
    "代码",
    "名称",
    "最新公告日期",
    "股权登记日",
    "除权除息日",
    "现金分红-现金分红比例",
    "送转股份-送股比例",
    "送转股份-转股比例",
    "方案进度",
    "方案",
]


def _cninfo_cash_and_rights_issue(symbol: str) -> pd.DataFrame:
    """One CNINFO frame carrying a cross-confirmable cash dividend (implemented)
    plus an unsupported rights issue (quarantined) for ``symbol``."""
    code = symbol.split(".")[0]
    return pd.DataFrame(
        [
            {
                "证券代码": code,
                "证券简称": "placeholder",
                "公告日期": "2021-11-02",
                "股权登记日": "2021-11-10",
                "除权除息日": "2021-11-11",
                "派息(税前)(元/10股)": 4.6,
                "送股(股/10股)": 0.0,
                "转增(股/10股)": 0.0,
                "进度": "实施",
                "方案": "10派4.6元(含税)",
            },
            {
                "证券代码": code,
                "证券简称": "placeholder",
                "公告日期": "2021-11-05",
                "股权登记日": "2021-11-17",
                "除权除息日": "2021-11-18",
                "派息(税前)(元/10股)": 0.0,
                "送股(股/10股)": 0.0,
                "转增(股/10股)": 0.0,
                "进度": "实施",
                "方案": "拟配股，每10股配2股",
            },
        ],
        columns=_ACTION_CNINFO_COLUMNS,
    )


def _eastmoney_cash(symbol: str) -> pd.DataFrame:
    """The matching Eastmoney cash dividend that cross-confirms the CNINFO one."""
    code = symbol.split(".")[0]
    return pd.DataFrame(
        [
            {
                "代码": code,
                "名称": "placeholder",
                "最新公告日期": "2021-11-02",
                "股权登记日": "2021-11-10",
                "除权除息日": "2021-11-11",
                "现金分红-现金分红比例": 4.6,
                "送转股份-送股比例": 0.0,
                "送转股份-转股比例": 0.0,
                "方案进度": "实施",
                "方案": "10派4.6元(含税)",
            }
        ],
        columns=_ACTION_EASTMONEY_COLUMNS,
    )


def _cninfo_single_cash(symbol: str) -> pd.DataFrame:
    """CNINFO frame holding ONLY the cross-confirmable cash dividend.

    Reuses the first row of ``_cninfo_cash_and_rights_issue`` -- the exact cash
    dividend the sibling test proves books at 0.46/share -- while dropping the
    unsupported rights-issue sibling so nothing is quarantined for ``symbol``.
    """
    return _cninfo_cash_and_rights_issue(symbol).head(1).reset_index(drop=True)


def _cninfo_dividend_11823(symbol: str) -> pd.DataFrame:
    """Recorded shape of AKShare 1.18.23 ``stock_dividend_cninfo`` output."""
    return pd.DataFrame(
        [
            {
                "实施方案公告日期": "2021-11-02",
                "分红类型": "现金分红",
                "送股比例": 0.0,
                "转增比例": 0.0,
                "派息比例": 4.6,
                "股权登记日": "2021-11-10",
                "除权日": "2021-11-11",
                "派息日": "2021-11-11",
                "股份到账日": "2021-11-11",
                "实施方案分红说明": "10派4.6元(含税)",
                "报告时间": "2021-09-30",
            }
        ]
    )


def _akshare_11823_cninfo_sources() -> dict[str, DataSource]:
    """One recorded 1.18.23 CNINFO dividend; other requests are empty."""
    source = StubAdapter(
        "akshare",
        action_frames={
            "000333.SZ": {
                "cninfo_corporate_actions": _cninfo_dividend_11823("000333.SZ")
            }
        },
    )
    return _all_stubs(akshare=source)


def _verified_cash_dividend_sources() -> dict[str, DataSource]:
    """``600036.SH`` reports a cross-confirmed cash dividend (accepted) with
    nothing unsupported; every other symbol answers no events."""
    clean = StubAdapter(
        "akshare",
        action_frames={
            "600036.SH": {
                "cninfo_corporate_actions": _cninfo_single_cash("600036.SH"),
                "eastmoney_corporate_actions": _eastmoney_cash("600036.SH"),
            }
        },
    )
    return _all_stubs(akshare=clean)


def _mixed_accepted_and_unsupported_action_sources() -> dict[str, DataSource]:
    """``600000.SH`` reports BOTH a cross-confirmed cash dividend (accepted) and
    an unsupported CNINFO rights issue (quarantined) inside the update window;
    every other symbol answers no events."""
    mixed = StubAdapter(
        "akshare",
        action_frames={
            "600000.SH": {
                "cninfo_corporate_actions": _cninfo_cash_and_rights_issue(
                    "600000.SH"
                ),
                "eastmoney_corporate_actions": _eastmoney_cash("600000.SH"),
            }
        },
    )
    return _all_stubs(akshare=mixed)


def _cninfo_cash_out_of_window(symbol: str) -> pd.DataFrame:
    """An implemented CNINFO cash dividend dated AFTER the backtest window.

    The ex-date (2021-12-17) lies past ``_WINDOW_END`` (2021-11-30), so the row
    survives ``prepare_cninfo_dividend_frame`` but is dropped by
    ``filter_corporate_actions_to_window``: the raw response carries rows while
    the window itself holds no in-window event.
    """
    code = symbol.split(".")[0]
    return pd.DataFrame(
        [
            {
                "证券代码": code,
                "证券简称": "placeholder",
                "公告日期": "2021-12-10",
                "股权登记日": "2021-12-16",
                "除权除息日": "2021-12-17",
                "派息(税前)(元/10股)": 4.6,
                "送股(股/10股)": 0.0,
                "转增(股/10股)": 0.0,
                "进度": "实施",
                "方案": "10派4.6元(含税)",
            }
        ],
        columns=_ACTION_CNINFO_COLUMNS,
    )


def _eastmoney_cash_out_of_window(symbol: str) -> pd.DataFrame:
    """The matching Eastmoney cash dividend dated after the backtest window."""
    code = symbol.split(".")[0]
    return pd.DataFrame(
        [
            {
                "代码": code,
                "名称": "placeholder",
                "最新公告日期": "2021-12-10",
                "股权登记日": "2021-12-16",
                "除权除息日": "2021-12-17",
                "现金分红-现金分红比例": 4.6,
                "送转股份-送股比例": 0.0,
                "送转股份-转股比例": 0.0,
                "方案进度": "实施",
                "方案": "10派4.6元(含税)",
            }
        ],
        columns=_ACTION_EASTMONEY_COLUMNS,
    )


def _cninfo_cash_plus_stale_plan(symbol: str) -> pd.DataFrame:
    """The cross-confirmable cash dividend plus a stale implemented plan.

    The appended row is a 1998 plan the supplier marks ``实施`` but reports with
    no ex-date and no record date.  It cannot be booked (``_standardize_source``
    keys candidates on ``(symbol, ex_date)``), and it survives
    ``filter_corporate_actions_to_window`` on purpose -- that filter keeps an
    implemented record whose ex-date is missing so reconciliation can flag it
    -- so it lands in the quarantine table as ``incomplete``.
    """
    code = symbol.split(".")[0]
    stale = pd.DataFrame(
        [
            {
                "证券代码": code,
                "证券简称": "placeholder",
                "公告日期": "1998-06-01",
                "股权登记日": "",
                "除权除息日": "",
                "派息(税前)(元/10股)": 1.0,
                "送股(股/10股)": 0.0,
                "转增(股/10股)": 0.0,
                "进度": "实施",
                "方案": "10派1元(含税)",
            }
        ],
        columns=_ACTION_CNINFO_COLUMNS,
    )
    # The dividend row is the same one ``_cninfo_single_cash`` proves books at
    # 0.46/share; composing keeps the two fixtures from drifting apart.
    return pd.concat([_cninfo_single_cash(symbol), stale], ignore_index=True)


def _stale_pre_window_plan_sources() -> dict[str, DataSource]:
    """``600036.SH`` holds an in-window accepted dividend AND a 1998 implemented
    plan with no ex-date; every other symbol answers no events."""
    source = StubAdapter(
        "akshare",
        action_frames={
            "600036.SH": {
                "cninfo_corporate_actions": _cninfo_cash_plus_stale_plan("600036.SH"),
                "eastmoney_corporate_actions": _eastmoney_cash("600036.SH"),
            }
        },
    )
    return _all_stubs(akshare=source)


def _out_of_window_cash_sources() -> dict[str, DataSource]:
    """``600036.SH`` reports only implemented dividends dated past the window
    on both endpoints; every other symbol answers no events."""
    source = StubAdapter(
        "akshare",
        action_frames={
            "600036.SH": {
                "cninfo_corporate_actions": _cninfo_cash_out_of_window("600036.SH"),
                "eastmoney_corporate_actions": _eastmoney_cash_out_of_window(
                    "600036.SH"
                ),
            }
        },
    )
    return _all_stubs(akshare=source)


@pytest.fixture
def project(tmp_path):
    """A fresh synthetic project per test (updates republish ``CURRENT``)."""
    return build_fixture_project(tmp_path / "project")


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


def test_successful_update_binds_sanitized_build_evidence(project):
    """The dataset manifest must carry sanitized, identity-bearing evidence.

    ``build_config`` records where every byte came from: hashes, stable reason
    codes and the request window -- never exception text, URLs, local paths or
    reason prose, so identical builds stay byte-identical apart from run_id.
    """
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    manifest = json.loads(
        (result.dataset_ref.path / "dataset_manifest.json").read_text()
    )
    build = manifest["build_config"]
    assert build["origin"] == "data_update"
    assert build["pipeline_contract_version"] == 1
    assert build["run_id"] == result.run_id
    assert build["requested_start_date"] == _WINDOW_START.isoformat()
    assert build["effective_start_date"] == _WINDOW_START.isoformat()
    assert build["requested_end_date"] == _WINDOW_END.isoformat()
    assert build["resolved_end_date"] == _WINDOW_END.isoformat()
    assert "resolved_end_is_fallback" not in build
    assert build["raw_snapshots"] == sorted(
        build["raw_snapshots"],
        key=lambda row: (
            row["source"],
            row["endpoint"],
            row["transport_id"] or "",
            row["request_key"],
            row["file_sha256"],
        ),
    )
    assert all(
        set(row)
        == {
            "source",
            "endpoint",
            "transport_id",
            "request_key",
            "file_sha256",
            "manifest_sha256",
        }
        for row in build["raw_snapshots"]
    )
    assert all(
        set(row) == {"source", "required", "ok", "reason_code"}
        for row in build["source_status"]
    )
    assert all(row["reason_code"] == "ok" for row in build["source_status"])
    assert "token" not in json.dumps(build).lower()


def test_update_records_configured_start_when_request_omits_start(project):
    """Manifest evidence distinguishes an omitted start from its effective value."""
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(end_date=_WINDOW_END)
    )

    manifest = json.loads(
        (result.dataset_ref.path / "dataset_manifest.json").read_text()
    )
    build = manifest["build_config"]
    assert build["requested_start_date"] is None
    assert build["effective_start_date"] == "2020-01-01"


def test_update_ignores_review_before_the_requested_window(project):
    """A carried historical review cannot block a later incremental update.

    The update window is November 2021, while this explicitly pinned 2018
    conflict is already outside that refresh window.  Removing the production
    window filter for reviews must make this test fail because the reconciler
    cannot find a 2018 conflict in the current supplier responses.
    """
    (project.root / "configs" / "corporate_action_reviews.yml").write_text(
        yaml.safe_dump(
            [
                {
                    "symbol": "601318.SH",
                    "ex_date": "2018-06-07",
                    "selected_source": "cninfo",
                    "record_date": "2018-06-06",
                    "cash_dividend_per_share": 1.2,
                    "bonus_share_ratio": 0.0,
                    "capitalization_ratio": 0.0,
                    "rationale": "already reconciled in carried history",
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())

    assert result.dataset_ref is not None


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


def test_update_publishes_internal_total_return_rows(project):
    """A successful update publishes the total-return bars and quarantine.

    Every published ``adjusted_bar`` row carries the single internal
    ``internal_total_return_v1`` basis over the whole pinned universe, and the
    quarantine table ships alongside it so trust breaks stay auditable.
    """
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        adjusted = context.read("adjusted_bar")
        assert set(adjusted["adjustment"]) == {"internal_total_return_v1"}
        assert set(adjusted["symbol"]) == set(_FIXTURE_UNIVERSE_SYMBOLS)
        assert "corporate_action_quarantine" in context.tables


def _run_update_with_actions(project, action_symbols):
    """One full update; each named symbol cross-reports one cash dividend."""
    frames = {
        symbol: {
            "cninfo_corporate_actions": _cninfo_single_cash(symbol),
            "eastmoney_corporate_actions": _eastmoney_cash(symbol),
        }
        for symbol in action_symbols
    }
    sources = _all_stubs(akshare=StubAdapter("akshare", action_frames=frames))
    return DataPipeline(project.root, sources=sources).update(_request())


def _read_adjusted(root, version: str) -> pd.DataFrame:
    with DatasetReader(root).open(version) as context:
        return context.read("adjusted_bar")


def test_later_action_does_not_rewrite_pre_ex_date_adjusted_rows(project):
    """A newly discovered event only affects rows from its ex_date onward.

    Two sequential updates over the same window; the second learns one
    cross-confirmed cash dividend ex-dated 2021-11-11.  Every adjusted row
    strictly before that ex_date must be frame-equal across both published
    versions -- a later action can never rewrite past total-return values.
    """
    first = _run_update_with_actions(project, [])
    assert first.dataset_ref is not None
    before = _read_adjusted(project.root, first.dataset_ref.version)
    second = _run_update_with_actions(project, ["600036.SH"])
    assert second.dataset_ref is not None
    after = _read_adjusted(project.root, second.dataset_ref.version)
    cutoff = before["trade_date"] < pd.Timestamp(_EVENT_DAY)
    assert cutoff.any()
    pd.testing.assert_frame_equal(
        before.loc[cutoff].reset_index(drop=True),
        after.loc[cutoff].reset_index(drop=True),
    )


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


def test_update_all_action_endpoints_failed_publishes_null_checked_at(project):
    """An all-endpoint-failed coverage row must not carry a wall clock.

    Coverage evidence must be byte-deterministic: when every action endpoint
    fails there is no source ``checked_at`` to record, so the published row's
    ``checked_at`` is NaT rather than a live ``pd.Timestamp.now``.
    """
    result = DataPipeline(project.root, sources=_all_action_endpoints_failing()).update(
        _request()
    )
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        coverage = context.read("corporate_action_coverage")
    assert set(coverage["status"]) == {"UNTRUSTED"}
    assert "SOURCE_FETCH_FAILED" in set(coverage["reason"])
    assert coverage["checked_at"].isna().all()


def test_update_marks_mixed_accepted_and_unsupported_window_untrusted(project):
    """A symbol/window holding BOTH an accepted fact and a quarantined
    unsupported event must not read VERIFIED: the unbooked rights issue would
    otherwise disappear from the evidence while the coverage row claims the
    window is fully accounted."""
    result = DataPipeline(
        project.root, sources=_mixed_accepted_and_unsupported_action_sources()
    ).update(_request())
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        facts = context.read("corporate_action")
        coverage = context.read("corporate_action_coverage")
    # The cash dividend is genuinely booked (has_accepted) ...
    booked = facts.loc[facts["symbol"] == "600000.SH"]
    assert len(booked) == 1
    assert booked.iloc[0]["cash_dividend_per_share"] == pytest.approx(0.46)
    # ... yet the sibling rights issue keeps the window from reading VERIFIED.
    row = coverage.loc[coverage["symbol"] == "600000.SH"].iloc[0]
    assert row["status"] == "UNTRUSTED"
    assert row["reason"] == "UNSUPPORTED_ACTION"


def test_update_marks_clean_cross_confirmed_cash_dividend_verified(project):
    """Positive control for the has_accepted -> VERIFIED branch.

    A symbol whose window holds ONLY a cross-confirmed cash dividend -- no
    unsupported sibling to quarantine -- must publish a VERIFIED coverage row
    with no reason, and the dividend must book.  Without this control every
    published coverage row in the suite is VERIFIED_EMPTY or quarantined, so
    the accepted-fact verdict the whole trust gate keys on is unprotected.
    """
    result = DataPipeline(
        project.root, sources=_verified_cash_dividend_sources()
    ).update(_request())
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        facts = context.read("corporate_action")
        coverage = context.read("corporate_action_coverage")
    # The dividend books as the accepted fact the sibling test proves (0.46).
    booked = facts.loc[facts["symbol"] == "600036.SH"]
    assert len(booked) == 1
    assert booked.iloc[0]["cash_dividend_per_share"] == pytest.approx(0.46)
    # ... and, with nothing quarantined, the window reads VERIFIED, reason None.
    row = coverage.loc[coverage["symbol"] == "600036.SH"].iloc[0]
    assert row["status"] == "VERIFIED"
    assert pd.isna(row["reason"])


def test_update_events_only_outside_window_read_verified_empty(project):
    """Raw rows whose events fall outside the window must not mark the window
    as holding events.

    Both endpoints answer successfully but carry ONLY an implemented dividend
    ex-dated after the window (2021-12-17 > 2021-11-30).  Nothing survives the
    window filter, so each endpoint's recorded outcome must read empty and the
    symbol/window publishes ``VERIFIED_EMPTY`` -- not ``FACTS_INCOMPLETE``
    inferred from raw-response row presence.
    """
    result = DataPipeline(
        project.root, sources=_out_of_window_cash_sources()
    ).update(_request())
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        facts = context.read("corporate_action")
        coverage = context.read("corporate_action_coverage")
    assert facts.loc[facts["symbol"] == "600036.SH"].empty
    row = coverage.loc[coverage["symbol"] == "600036.SH"].iloc[0]
    assert row["status"] == "VERIFIED_EMPTY"
    assert pd.isna(row["reason"])


def test_update_ignores_quarantine_rows_whose_dates_predate_the_window(project):
    """A quarantine row that cannot affect the window must not mark it UNTRUSTED.

    ``600036.SH`` books an in-window dividend while carrying a 1998 implemented
    plan the supplier reports without any date.  That stale record has nothing
    to say about 2021-11, so the window reads VERIFIED -- yet it stays in the
    published quarantine table (evidence is not hidden) and the exclusion
    leaves an INFO trace naming the rule that dropped it (ADR-006).
    """
    result = DataPipeline(
        project.root, sources=_stale_pre_window_plan_sources()
    ).update(_request())
    assert result.dataset_ref is not None, result.quality_report

    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        facts = context.read("corporate_action")
        coverage = context.read("corporate_action_coverage")
        quarantine = context.read("corporate_action_quarantine")

    # The in-window dividend still books and the window reads VERIFIED.
    booked = facts.loc[facts["symbol"] == "600036.SH"]
    assert len(booked) == 1
    row = coverage.loc[coverage["symbol"] == "600036.SH"].iloc[0]
    assert row["status"] == "VERIFIED"
    assert pd.isna(row["reason"])

    # Evidence is not hidden: the stale plan is still published.
    stale = quarantine.loc[quarantine["symbol"] == "600036.SH"]
    assert len(stale) == 1
    assert stale.iloc[0]["reason"] == "incomplete"

    # ... and the suppression left an auditable trace.
    trace = [
        issue
        for issue in result.quality_report.issues
        if issue.code == CODE_QUARANTINE_OUT_OF_WINDOW
    ]
    assert len(trace) == 1
    assert trace[0].severity is Severity.INFO
    assert trace[0].symbol == "600036.SH"
    assert trace[0].details["rows"] == 1
    assert trace[0].details["branch"] == "announcement_pre_window_implemented"


def test_update_publishes_akshare_11823_cninfo_dividend(project):
    """The current CNINFO response shape must produce a bookable dividend."""
    result = DataPipeline(
        project.root, sources=_akshare_11823_cninfo_sources()
    ).update(_request())

    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        facts = context.read("corporate_action")
    booked = facts.loc[facts["symbol"] == "000333.SZ"]
    assert len(booked) == 1
    assert booked.iloc[0]["cash_dividend_per_share"] == pytest.approx(0.46)


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


def test_disabled_baostock_is_never_constructed_or_fetched(project, monkeypatch):
    """``--sources baostock`` cannot bypass ``baostock.enabled: false``.

    A config-disabled source is never built through ``_build_source`` and
    never requested: naming it in the update request only narrows the enabled
    set to nothing, so the run fails (or carries it as not-run) without the
    adapter ever existing.
    """
    from conftest import write_sources

    write_sources(project.root, baostock=False)
    constructed: list[str] = []

    def build(name, _config):
        constructed.append(name)
        if name == "baostock":
            raise AssertionError("disabled baostock constructed")
        return _all_stubs()[name]

    monkeypatch.setattr("stock_quant.data_pipeline._build_source", build)
    result = DataPipeline(project.root).update(
        DataUpdateRequest(sources=("baostock",))
    )
    assert constructed == []
    assert result.dataset_ref is None


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


def test_update_without_end_uses_the_max_published_calendar_date(project):
    """``--end`` omission means the newest published calendar day, nothing else.

    No clock is consulted: the resolved end is exactly the maximum
    ``trading_calendar.calendar_date`` of the carried dataset (2022-01-07,
    years before "today"), never the current natural day.
    """
    with DatasetReader(project.root).open(project.version) as context:
        published = context.read("trading_calendar")
    expected = max(published["calendar_date"]).date()
    assert expected != date.today()
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=None)
    )
    assert result.resolved_end_date == expected
    assert not hasattr(result, "resolved_end_is_fallback")


def test_update_without_end_fails_when_no_calendar_is_published(tmp_path):
    """An empty published calendar must ask the operator for an explicit --end."""
    project = build_fixture_project(tmp_path / "project")
    publisher = DatasetPublisher(project.root)
    with DatasetReader(project.root).open(project.version) as context:
        tables = {name: context.read(name) for name in context.tables}
    tables["trading_calendar"] = tables["trading_calendar"].iloc[0:0]
    publisher.publish(tables, QualityReport())
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=None, end_date=None)
    )
    assert result.dataset_ref is None
    assert CODE_CALENDAR_EMPTY_NO_END in result.quality_report.by_code()


# --------------------------------------------------------------------------- #
# Relay trading-calendar refresh, manifest evidence and the publish span gate
# --------------------------------------------------------------------------- #


def test_update_records_two_trade_cal_snapshots_and_binds_a_relay_span(project):
    """A successful update binds both exchanges' raw calendars into the manifest."""
    stub = StubAdapter("tushare")
    result = DataPipeline(project.root, sources=_all_stubs(tushare=stub)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is not None
    assert [call for call in stub.calls if call[0] == "trade_cal"] == [
        ("trade_cal", None),
        ("trade_cal", None),
    ]
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        build = context.manifest["build_config"]
        calendar = context.read("trading_calendar")
    relay = [
        span
        for span in build["calendar_coverage"]
        if span["source"] == "tushare_relay"
    ]
    assert len(relay) == 1
    # The fixture's carried span already covers [CAL_START, CAL_END] and the
    # window sits inside it, so the merged span keeps those bounds.
    assert relay[0]["start_date"] == CAL_START.isoformat()
    assert relay[0]["end_date"] == CAL_END.isoformat()
    assert sorted(relay[0]["snapshot_sha256s"]) == ["SSE", "SZSE"]
    fresh = set(result.raw_snapshots)
    for hashes in relay[0]["snapshot_sha256s"].values():
        assert set(hashes) & fresh, "this round's snapshot hash must be bound"
    assert build["full_history_acceptance_start"] == CAL_START.isoformat()
    assert build["universe_coverage_definition_hashes"]
    assert build["universe_coverage_skipped"] == []
    assert "resolved_end_is_fallback" not in build
    assert max(calendar["calendar_date"]).date() == CAL_END


def test_update_fails_when_the_two_exchanges_disagree(project):
    """One exchange calling 2021-11-10 closed is an SSE/SZSE conflict."""
    stub = StubAdapter(
        "tushare", trade_cal_is_open_by_exchange={("SZSE", date(2021, 11, 10)): 0}
    )
    result = DataPipeline(project.root, sources=_all_stubs(tushare=stub)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is None
    assert CODE_CALENDAR_EXCHANGE_MISMATCH in result.quality_report.by_code()


def test_update_blocks_on_a_broken_pretrade_chain(project):
    """Both exchanges agree on a wrong pretrade link; the chain kills the run."""
    stub = StubAdapter(
        "tushare",
        trade_cal_pretrade_overrides={date(2021, 11, 10): "20211101"},
    )
    result = DataPipeline(project.root, sources=_all_stubs(tushare=stub)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is None
    assert CODE_CALENDAR_PRETRADE_CONTINUITY_BROKEN in result.quality_report.by_code()
    assert result.raw_snapshots, "a blocked calendar run still records raw responses"


def test_update_keeps_current_and_raw_when_the_calendar_fetch_fails(project):
    """A half-fetched calendar is fatal: raw kept, CURRENT untouched, no replay."""
    before = DatasetPublisher(project.root).current().version
    partial = StubAdapter("tushare", trade_cal_failing_exchanges=("SZSE",))
    result = DataPipeline(project.root, sources=_all_stubs(tushare=partial)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert result.dataset_ref is None
    assert CODE_SOURCE_FETCH_FAILED in result.quality_report.by_code()
    assert DatasetPublisher(project.root).current().version == before
    assert result.raw_snapshots, "the SSE response was already written to the raw store"
    # The next run asks the supplier again for both exchanges; old raw bytes are
    # never replayed as a calendar cache.
    retry = StubAdapter("tushare")
    DataPipeline(project.root, sources=_all_stubs(tushare=retry)).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert [call for call in retry.calls if call[0] == "trade_cal"] == [
        ("trade_cal", None),
        ("trade_cal", None),
    ]


def test_update_blocks_a_window_that_leaves_a_hole_in_calendar_coverage(project):
    """A left-side window that is not adjacent to coverage fails the span gate.

    Continuity passes here (the merged table holds no earlier open day at all,
    so the boundary rows land in ``allowed_pre_coverage``), which is how this
    case isolates the post-merge span gate from the chain check.
    """
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=date(2017, 12, 4), end_date=date(2017, 12, 29))
    )
    assert result.dataset_ref is None
    assert CODE_CALENDAR_COVERAGE_GAP in result.quality_report.by_code()


def test_rerun_merges_adjacent_relay_spans_and_keeps_every_snapshot_hash(project):
    """A window that extends coverage merges into the carried relay span."""
    first = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )
    assert first.dataset_ref is not None
    with DatasetReader(project.root).open(first.dataset_ref.version) as context:
        before = [
            span
            for span in context.manifest["build_config"]["calendar_coverage"]
            if span["source"] == "tushare_relay"
        ]
    assert len(before) == 1
    before_hashes = {
        exchange: set(hashes)
        for exchange, hashes in before[0]["snapshot_sha256s"].items()
    }
    extended_end = CAL_END + timedelta(days=20)
    second = DataPipeline(project.root, sources=_all_stubs()).update(
        DataUpdateRequest(
            start_date=CAL_END + timedelta(days=1), end_date=extended_end
        )
    )
    assert second.dataset_ref is not None
    with DatasetReader(project.root).open(second.dataset_ref.version) as context:
        after = [
            span
            for span in context.manifest["build_config"]["calendar_coverage"]
            if span["source"] == "tushare_relay"
        ]
    assert len(after) == 1, "adjacent same-source spans merge into one"
    assert after[0]["start_date"] == CAL_START.isoformat()
    assert after[0]["end_date"] == extended_end.isoformat()
    fresh = set(second.raw_snapshots)
    for exchange, hashes in after[0]["snapshot_sha256s"].items():
        assert before_hashes[exchange] < set(hashes), "merging never drops evidence"
        assert fresh & set(hashes), "this round's snapshot is bound as well"


# --------------------------------------------------------------------------- #
# Master fact-vs-bar boundary and coverage-consistency checks (Task 5)
# --------------------------------------------------------------------------- #


def _republish_current_tables(
    root: Path,
    *,
    master_coverage: pd.DataFrame | None = None,
    include_master_coverage: bool = True,
    include_corporate_action: bool = True,
) -> str:
    """Republish CURRENT with optional canonical-table variants.

    ``master_coverage`` replaces the published table; ``include_master_coverage
    = False`` omits it entirely, and ``include_corporate_action=False`` omits
    the facts table (the "older dataset" shapes).  Used only to stage
    validate-only audit breaches the pipeline itself can never write.
    """
    publisher = DatasetPublisher(root)
    version = publisher.current().version
    reader = DatasetReader(root)
    with reader.open(version) as context:
        tables = {
            "daily_bar": context.read("daily_bar"),
            "security_master": context.read("security_master"),
            "corporate_action_coverage": context.read(
                "corporate_action_coverage"
            ),
            "trading_calendar": context.read("trading_calendar"),
        }
        if include_corporate_action:
            tables["corporate_action"] = context.read("corporate_action")
        if include_master_coverage:
            tables["security_master_coverage"] = (
                master_coverage
                if master_coverage is not None
                else context.read("security_master_coverage")
            )
    return publisher.publish(tables, QualityReport()).version


def test_validate_fails_closed_without_total_return_tables(project):
    """A dataset manifest without adjusted_bar can never validate clean.

    The publisher accepts table subsets silently, so ``validate`` must fail
    closed on the missing total-return tables instead of waving a legacy (or
    trimmed) dataset through as trusted.
    """
    pipeline, result = _update_and_validate(project)
    assert result.dataset_ref is not None
    # Republish CURRENT from an update-published dataset minus both new
    # tables -- the exact shape a pre-migration or trimmed dataset has.
    _republish_current_tables(project.root)
    report = pipeline.validate()
    issue = next(
        item
        for item in report.issues
        if item.code == CODE_ADJUSTED_BAR_MISSING_FROM_DATASET
    )
    assert issue.severity is Severity.FATAL
    assert issue.table == "adjusted_bar"
    assert issue.details["missing_tables"] == [
        "adjusted_bar",
        "corporate_action_quarantine",
    ]


def test_validate_fails_closed_without_corporate_action(project):
    """The same coded FATAL guards the facts the lineage checks read.

    A dataset without ``corporate_action`` could not have its adjusted rows
    audited, so it must fail closed too -- never crash on the bare read.
    """
    pipeline, _ = _update_and_validate(project)
    _republish_current_tables(project.root, include_corporate_action=False)
    report = pipeline.validate()
    issue = next(
        item
        for item in report.issues
        if item.code == CODE_ADJUSTED_BAR_MISSING_FROM_DATASET
    )
    assert issue.severity is Severity.FATAL
    assert issue.table == "adjusted_bar"
    assert issue.details["missing_tables"] == [
        "corporate_action",
        "adjusted_bar",
        "corporate_action_quarantine",
    ]


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


# --------------------------------------------------------------------------- #
# Carried universe_membership raw table (point-in-time universe task)
# --------------------------------------------------------------------------- #


def _membership_fixture_frame() -> pd.DataFrame:
    """A small, fully evidenced csi300 fact table over fixture symbols."""
    return membership_frame(
        [
            {
                "universe_id": "csi300",
                "symbol": symbol,
                "raw_effective_from": date(2018, 1, 2),
                "raw_effective_to": None,
                "announcement_date": date(2018, 1, 2),
                "status": "active",
                "reason": "initial_constituent",
                "source": "csi_index_announcement",
                "source_url": "https://www.csindex.com.cn/fixture.pdf",
                "snapshot_sha256": "a1" * 32,
                "source_document_sha256": "b2" * 32,
            }
            for symbol in ("600000.SH", "000333.SZ")
        ]
    )


def _publish_baseline_with_membership(root: Path, membership: pd.DataFrame):
    """Republish CURRENT with the membership table added to its tables.

    The carried ``build_config`` is bound again unchanged: only the membership
    table differs, and stripping the build evidence would turn the baseline
    into a legacy manifest whose calendar coverage the update span gate
    rejects (``calendar_uncovered``) -- which is not what this helper tests.
    """
    publisher = DatasetPublisher(root)
    reader = DatasetReader(root)
    with reader.open(publisher.current().version) as context:
        tables = {name: context.read(name) for name in context.tables}
        build_config = context.manifest.get("build_config")
    tables["universe_membership"] = membership
    return publisher.publish(
        tables, QualityReport(), build_config=build_config
    ).version


def _publish_legacy_baseline_without_membership(root: Path):
    """Republish CURRENT without the membership table (a pre-membership
    legacy baseline that post-dates bootstrap but predates the membership
    era and carries no universe_membership table at all).  The carried
    ``build_config`` is bound again unchanged; only the membership table
    differs, so the update span gate still sees relay calendar coverage."""
    publisher = DatasetPublisher(root)
    reader = DatasetReader(root)
    with reader.open(publisher.current().version) as context:
        tables = {
            name: context.read(name)
            for name in context.tables
            if name != "universe_membership"
        }
        build_config = context.manifest.get("build_config")
    return publisher.publish(
        tables, QualityReport(), build_config=build_config
    ).version


def test_update_carries_universe_membership_table_unchanged(project):
    """Membership facts are immutable: an update carries the registered raw
    table through to the new dataset version byte-for-byte and the auditor
    stays clean over it."""
    membership = _membership_fixture_frame()
    _publish_baseline_with_membership(project.root, membership)
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        _request()
    )
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        assert "universe_membership" in context.tables
        carried = context.read("universe_membership")
    pd.testing.assert_frame_equal(carried, membership, check_dtype=False)
    report = DataPipeline(project.root).validate()
    assert report.by_severity()[Severity.FATAL.value] == 0
    assert report.by_severity()[Severity.ERROR.value] == 0


def test_update_publishes_without_membership_table_when_absent(project):
    """A pre-membership legacy baseline stays publishable; the update must
    not invent an empty membership table for it (older datasets simply
    update without the table until an explicit membership refresh)."""
    _publish_legacy_baseline_without_membership(project.root)
    result = DataPipeline(project.root, sources=_all_stubs()).update(
        _request()
    )
    assert result.dataset_ref is not None
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        assert "universe_membership" not in context.tables


def test_validate_surfaces_tampered_membership_evidence_as_fatal(project):
    """A membership row without evidence cannot pass ``data validate``: the
    auditor runs the same fatal fact checks the acceptance gate relies on."""
    membership = _membership_fixture_frame()
    membership.loc[0, "snapshot_sha256"] = ""
    _publish_baseline_with_membership(project.root, membership)
    report = DataPipeline(project.root).validate()
    assert report.by_code()[UNIVERSE_EVIDENCE_MISSING] == 1
    issue = next(
        item
        for item in report.issues
        if item.code == UNIVERSE_EVIDENCE_MISSING
    )
    assert issue.severity is Severity.FATAL
    assert issue.table == "universe_membership"


def test_validate_reports_calendar_evidence_of_the_fixture(project):
    report = DataPipeline(project.root).validate()
    codes = report.by_code()
    assert "calendar_coverage_missing" not in codes
    assert "bootstrap_seed_in_full_history" not in codes
    assert "removed_fallback_field_present" not in codes
    assert report.by_severity()[Severity.FATAL.value] == 0


def test_validate_flags_a_legacy_manifest_without_calendar_coverage(project):
    """A pre-calendar-manifest dataset cannot claim calendar provenance."""
    with DatasetReader(project.root).open(project.version) as context:
        tables = {name: context.read(name) for name in context.tables}
        build = dict(context.manifest["build_config"])
    build.pop("calendar_coverage")
    build.pop("full_history_acceptance_start")
    version = DatasetPublisher(project.root).publish(
        tables, QualityReport(), build_config=build
    ).version
    codes = DataPipeline(project.root).validate(version).by_code()
    assert "calendar_coverage_missing" in codes


def test_validate_ignores_a_compatible_legacy_fallback_field_but_not_a_new_one(
    project,
):
    with DatasetReader(project.root).open(project.version) as context:
        tables = {name: context.read(name) for name in context.tables}
        build = dict(context.manifest["build_config"])
    legacy = dict(build)
    legacy.pop("calendar_coverage")
    legacy.pop("full_history_acceptance_start")
    legacy["resolved_end_is_fallback"] = False
    legacy_version = DatasetPublisher(project.root).publish(
        tables, QualityReport(), build_config=legacy
    ).version
    assert "removed_fallback_field_present" not in DataPipeline(
        project.root
    ).validate(legacy_version).by_code()
    fresh = dict(build, resolved_end_is_fallback=False)
    fresh_version = DatasetPublisher(project.root).publish(
        tables, QualityReport(), build_config=fresh
    ).version
    assert "removed_fallback_field_present" in DataPipeline(
        project.root
    ).validate(fresh_version).by_code()
