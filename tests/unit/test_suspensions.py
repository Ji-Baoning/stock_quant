"""Suspension-bar materialization proven by the primary source's pre_close chain."""

import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_model.suspensions import suspension_rows
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import DataPipeline, DataUpdateRequest
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import (
    CODE_SUSPENSION_ROW,
    CODE_SUSPENSION_RUN_UNVERIFIED,
    CODE_UNEXPLAINED_PRIMARY_GAP,
    QualityIssue,
    QualityReport,
    Severity,
)
from stock_quant.data_quality.raw_checks import check_provenance
from stock_quant.data_sources.base import DataRequest, FetchResult, request_key
from stock_quant.research.acceptance.checks import (
    _missing_row_failures,
    _open_days,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

INGESTED = pd.Timestamp("2021-11-13", tz="UTC")

#: 2021-11-01 is a Monday; two full Mon-Fri weeks.
OPEN_DAYS = [
    date(2021, 11, 1),
    date(2021, 11, 2),
    date(2021, 11, 3),
    date(2021, 11, 4),
    date(2021, 11, 5),
    date(2021, 11, 8),
    date(2021, 11, 9),
    date(2021, 11, 10),
    date(2021, 11, 11),
    date(2021, 11, 12),
]

_SECOND_WEEK = [(f"202111{day:02d}", 10.5, 10.5) for day in (8, 9, 10, 11, 12)]


def _chain(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [pd.Timestamp(day) for day, _, _ in rows],
            "close": [close for _, close, _ in rows],
            "pre_close": [pre for _, _, pre in rows],
        }
    )


def _actions(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["000333.SZ"] * len(rows),
            "ex_date": [pd.Timestamp(day) for day, _, _ in rows],
            "cash_dividend_per_share": [cash for _, cash, _ in rows],
            "bonus_share_ratio": [bonus for _, _, bonus in rows],
            "status": ["implemented"] * len(rows),
        }
    )


def _run(symbol, chain, **kwargs):
    defaults = {
        "list_date": None,
        "delist_date": None,
        "actions": pd.DataFrame(columns=["symbol", "ex_date", "status"]),
        "ingested_at": INGESTED,
    }
    defaults.update(kwargs)
    return suspension_rows(symbol, chain, OPEN_DAYS, **defaults)


def test_complete_series_yields_no_rows_and_no_issues():
    chain = _chain(
        [(f"202111{day:02d}", 10.0, 10.0) for day in (1, 2, 3, 4, 5)] + _SECOND_WEEK
    )
    rows, issues = _run("000333.SZ", chain)
    assert rows.empty
    assert issues == []


def test_proven_suspension_run_materializes_carried_bars():
    """2021-11-05's pre_close equals 11-01's close: 11-02..04 are suspended."""
    chain = _chain(
        [
            ("20211101", 10.0, 9.9),
            ("20211105", 10.5, 10.0),
        ]
        + _SECOND_WEEK
    )
    rows, issues = _run("000333.SZ", chain)

    assert list(rows["trade_date"]) == [
        pd.Timestamp("2021-11-02"),
        pd.Timestamp("2021-11-03"),
        pd.Timestamp("2021-11-04"),
    ]
    for _, row in rows.iterrows():
        assert row["symbol"] == "000333.SZ"
        assert row["open"] == row["high"] == row["low"] == row["close"] == 10.0
        assert row["volume"] == 0
        assert row["amount"] == 0.0
        assert row["adjustment"] == "unadjusted"
        assert row["source"] == "tushare_suspend"
    assert [i.code for i in issues] == [CODE_SUSPENSION_ROW]
    assert issues[0].severity is Severity.INFO
    assert issues[0].details["days"] == 3


def test_unexplained_break_without_action_is_a_blocking_gap():
    """A chain break no action explains is real data loss, not a suspension."""
    chain = _chain(
        [
            ("20211101", 10.0, 9.9),
            ("20211105", 10.5, 11.0),
        ]
        + _SECOND_WEEK
    )
    rows, issues = _run("000333.SZ", chain)

    assert rows.empty
    errors = [i for i in issues if i.code == CODE_UNEXPLAINED_PRIMARY_GAP]
    assert len(errors) == 1
    assert errors[0].severity is Severity.ERROR
    assert errors[0].details["prev_close"] == 10.0
    assert errors[0].details["next_pre_close"] == 11.0


def test_run_spanning_an_accepted_action_carries_both_references():
    """Days before the ex-date carry the old close; from it, the new reference."""
    chain = _chain(
        [
            ("20211101", 10.0, 9.9),
            ("20211105", 10.5, 9.0),
        ]
        + _SECOND_WEEK
    )
    actions = _actions([("20211103", 1.0, 0.0)])
    rows, issues = _run("000333.SZ", chain, actions=actions)

    carried = dict(zip(rows["trade_date"].dt.date, rows["close"], strict=True))
    assert carried[date(2021, 11, 2)] == 10.0
    assert carried[date(2021, 11, 3)] == 9.0
    assert carried[date(2021, 11, 4)] == 9.0
    assert not [i for i in issues if i.code == CODE_UNEXPLAINED_PRIMARY_GAP]
    assert [i.code for i in issues] == [CODE_SUSPENSION_ROW]
    assert issues[0].details["action_ex_dates"] == ["2021-11-03"]


def test_chain_tolerance_is_half_a_cent():
    chain = _chain(
        [
            ("20211101", 10.0, 9.9),
            ("20211105", 10.5, 10.004),
        ]
        + _SECOND_WEEK
    )
    rows, issues = _run("000333.SZ", chain)
    assert len(rows) == 3
    assert not [i for i in issues if i.code == CODE_UNEXPLAINED_PRIMARY_GAP]

    chain = _chain(
        [
            ("20211101", 10.0, 9.9),
            ("20211105", 10.5, 10.006),
        ]
        + _SECOND_WEEK
    )
    rows, issues = _run("000333.SZ", chain)
    assert rows.empty
    assert [i.code for i in issues] == [CODE_UNEXPLAINED_PRIMARY_GAP]


def test_head_run_without_anchor_is_not_materialized():
    """No present row before the run: the chain cannot prove anything."""
    chain = _chain(
        [("20211104", 10.0, 9.8), ("20211105", 10.1, 10.0)] + _SECOND_WEEK
    )
    rows, issues = _run("000333.SZ", chain)

    assert rows.empty
    assert [i.code for i in issues] == [CODE_SUSPENSION_RUN_UNVERIFIED]
    assert issues[0].severity is Severity.WARNING


def test_tail_run_without_anchor_is_not_materialized():
    chain = _chain([("20211101", 10.0, 9.9), ("20211102", 10.1, 10.0)])
    rows, issues = _run("000333.SZ", chain)

    assert rows.empty
    assert [i.code for i in issues] == [CODE_SUSPENSION_RUN_UNVERIFIED]


def test_days_outside_the_listing_window_are_ignored():
    """Before list_date (and after delist_date) absence is expected, not owed."""
    chain = _chain(
        [("20211103", 10.0, 9.8), ("20211104", 10.1, 10.0), ("20211105", 10.2, 10.1)]
        + _SECOND_WEEK
    )
    rows, issues = _run("000333.SZ", chain, list_date=date(2021, 11, 3))
    assert rows.empty
    assert issues == []

    rows, issues = _run(
        "000333.SZ",
        _chain([("20211101", 10.0, 9.9), ("20211102", 10.1, 10.0)]),
        delist_date=date(2021, 11, 2),
    )
    assert rows.empty
    assert issues == []


def test_tushare_suspend_source_passes_provenance():
    frame = pd.DataFrame(
        [
            {
                "trade_date": pd.Timestamp("2021-11-02"),
                "symbol": "000333.SZ",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 0,
                "amount": 0.0,
                "adjustment": "unadjusted",
                "source": "tushare_suspend",
                "ingested_at": INGESTED,
            }
        ]
    )
    assert check_provenance(frame) == []


def test_unexplained_primary_gap_blocks_publication():
    report = QualityReport(
        issues=(
            QualityIssue(
                severity=Severity.ERROR,
                code=CODE_UNEXPLAINED_PRIMARY_GAP,
                table="daily_bar",
                symbol="000333.SZ",
                details={"prev_close": 10.0, "next_pre_close": 11.0},
            ),
        )
    )
    decision = evaluate_publication(report)
    assert decision.passed is False
    assert any("unexplained_primary_gap" in reason for reason in decision.reasons)


def test_missing_pre_close_column_disables_the_proof():
    """A chain without pre_close cannot prove anything and must stay silent."""
    chain = _chain([("20211101", 10.0, 9.9)]).drop(columns=["pre_close"])
    rows, issues = _run("000333.SZ", chain)
    assert rows.empty
    assert [i.code for i in issues] == [CODE_SUSPENSION_RUN_UNVERIFIED]


# --------------------------------------------------------------------------- #
# End to end: the update materializes proven suspension bars and passes the
# acceptance completeness recount without any policy change.
# --------------------------------------------------------------------------- #

_WINDOW_START = date(2021, 11, 1)
_WINDOW_END = date(2021, 11, 30)
_UNIVERSE_SYMBOLS = tuple(
    Universe.from_yaml(_REPO_ROOT / "configs" / "universe.yml").symbols
)
_GAPPY_SYMBOL = "000001.SZ"
_GAP_DAYS = {date(2021, 11, 2), date(2021, 11, 3), date(2021, 11, 4)}


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@dataclass(frozen=True)
class _SuspendStub:
    """Stubs whose primary daily carries pre_close and one suspended name."""

    name: str

    def fetch(self, request: DataRequest) -> FetchResult:
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=self._frame(request),
            metadata={
                "source": self.name,
                "response_timestamp": "2021-12-01T00:00:00Z",
            },
        )

    def _frame(self, request: DataRequest) -> pd.DataFrame:
        if request.endpoint == "stock_basic":
            return pd.DataFrame(
                [
                    {
                        "ts_code": symbol,
                        "name": f"stub_{symbol}",
                        "list_date": "19910102",
                        "delist_date": "",
                        "list_status": "L",
                    }
                    for symbol in _UNIVERSE_SYMBOLS
                ]
            )
        if request.endpoint == "index_history":
            days = _weekdays(request.start_date, request.end_date)
            return pd.DataFrame(
                {
                    "日期": days,
                    "开盘": [4000.0] * len(days),
                    "最高": [4000.0] * len(days),
                    "最低": [4000.0] * len(days),
                    "收盘": [4000.0] * len(days),
                    "成交量": [0] * len(days),
                }
            )
        if request.endpoint in (
            "cninfo_corporate_actions",
            "eastmoney_corporate_actions",
        ):
            return pd.DataFrame()
        symbol = request.symbols[0]
        days = [
            day
            for day in _weekdays(request.start_date, request.end_date)
            if not (symbol == _GAPPY_SYMBOL and day in _GAP_DAYS)
        ]
        return pd.DataFrame(
            [
                {
                    "code": symbol,
                    "date": day.strftime("%Y%m%d"),
                    "open": 55.0,
                    "high": 55.0,
                    "low": 55.0,
                    "close": 55.0,
                    "vol": 1000.0,
                    "amount": 55000.0,
                    "pre_close": 55.0,
                }
                for day in days
            ]
        )


def test_update_materializes_proven_suspension_bars(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    configs = root / "configs"
    configs.mkdir()
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
    ):
        shutil.copy(_REPO_ROOT / "configs" / name, configs / name)
    bootstrap_dataset(root)

    stubs = {name: _SuspendStub(name) for name in ("tushare", "akshare")}
    result = DataPipeline(root, sources=stubs).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )

    assert result.dataset_ref is not None, result.quality_report
    codes = {issue.code for issue in result.quality_report.issues}
    assert CODE_UNEXPLAINED_PRIMARY_GAP not in codes
    assert "unknown_or_suspended" not in codes
    assert CODE_SUSPENSION_ROW in codes

    with DatasetReader(root).open(result.dataset_ref.version) as dataset:
        daily = dataset.read("daily_bar")
        master = dataset.read("security_master")
        calendar = dataset.read("trading_calendar")
    carried = daily[
        (daily["symbol"] == _GAPPY_SYMBOL)
        & (daily["trade_date"] == pd.Timestamp("2021-11-02"))
    ]
    assert len(carried) == 1
    row = carried.iloc[0]
    assert row["source"] == "tushare_suspend"
    assert row["volume"] == 0
    assert row["close"] == 55.0

    grid = [
        day
        for day in _open_days(calendar)
        if _WINDOW_START <= day <= _WINDOW_END
    ]
    assert _missing_row_failures(daily, master, grid) == []
