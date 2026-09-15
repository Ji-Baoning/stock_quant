"""Suspension-bar materialization proven by the primary source's pre_close chain."""

import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_model.normalize import normalize_daily
from stock_quant.data_model.suspensions import (
    canonicalize_supplier_suspensions,
    suspension_rows,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import DataPipeline, DataUpdateRequest
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import (
    CODE_INVALID_OHLC,
    CODE_NONPOSITIVE_PRICE,
    CODE_SUSPENSION_ROW,
    CODE_SUSPENSION_RUN_UNVERIFIED,
    CODE_UNEXPLAINED_PRIMARY_GAP,
    QualityIssue,
    QualityReport,
    Severity,
)
from stock_quant.data_quality.raw_checks import (
    check_daily_values,
    check_provenance,
)
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


def test_action_ex_date_on_the_resumption_day_explains_the_gap():
    """A subscription halts through payment and goes ex on the resumption day.

    The run is 11-02..11-04 and the resumption day is 11-05; the action that
    moves that day's reference price carries 11-05 as its ex-date, one day past
    the last absent day.  Range-checking the ex-date against the absent run
    alone would call this break a data loss.
    """
    chain = _chain(
        [
            ("20211101", 10.0, 9.9),
            ("20211105", 9.3, 9.3),
        ]
        + _SECOND_WEEK
    )
    actions = _actions([("20211105", 0.0, 0.0)])
    rows, issues = _run("000333.SZ", chain, actions=actions)

    carried = dict(zip(rows["trade_date"].dt.date, rows["close"], strict=True))
    assert len(carried) == 3
    for day in (date(2021, 11, 2), date(2021, 11, 3), date(2021, 11, 4)):
        assert carried[day] == 10.0
    assert not [i for i in issues if i.code == CODE_UNEXPLAINED_PRIMARY_GAP]
    assert [i.code for i in issues] == [CODE_SUSPENSION_ROW]
    assert issues[0].details["action_ex_dates"] == ["2021-11-05"]


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
# Supplier-emitted no-trade rows: the supplier returns the suspended session as
# a row (zero open/high/low, zero volume, close carried at pre_close) instead of
# omitting it.  It is the same object the chain proof materializes, so it is
# canonicalized in place rather than rejected as a zero-priced bar -- and only
# under a signature narrow enough that a row carrying any real trade still
# reaches the value checks.
# --------------------------------------------------------------------------- #


def _raw_daily(rows: list[tuple[str, float, float, float, float, float, float, float]]):
    """One supplier daily frame: date, open, high, low, close, vol, amount, pre."""
    return pd.DataFrame(
        [
            {
                "code": "000333.SZ",
                "date": day,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "vol": volume,
                "amount": amount,
                "pre_close": pre_close,
            }
            for day, open_, high, low, close, volume, amount, pre_close in rows
        ]
    )


_TRADED_DAY = ("20211104", 10.0, 10.2, 9.9, 10.0, 1000.0, 10000.0, 9.9)


def _canonicalize(raw: pd.DataFrame):
    valid = normalize_daily(raw, "tushare", INGESTED).valid
    return canonicalize_supplier_suspensions(
        "000333.SZ", valid, raw, ingested_at=INGESTED
    )


def test_supplier_no_trade_row_becomes_a_carried_suspension_bar():
    raw = _raw_daily(
        [
            _TRADED_DAY,
            ("20211105", 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 10.0),
            ("20211108", 10.5, 10.6, 10.4, 10.5, 1200.0, 12600.0, 10.0),
        ]
    )
    frame, issues = _canonicalize(raw)

    assert list(frame["trade_date"]) == [
        pd.Timestamp(day) for day in ("20211104", "20211105", "20211108")
    ]
    assert list(frame["source"]) == ["tushare", "tushare_suspend", "tushare"]
    held = frame.iloc[1]
    assert held["open"] == held["high"] == held["low"] == held["close"] == 10.0
    assert held["volume"] == 0
    assert held["amount"] == 0.0
    assert held["adjustment"] == "unadjusted"
    # The repaired frame is exactly what the value and provenance checks accept.
    assert check_daily_values(frame, table="daily_bar") == []
    assert check_provenance(frame, table="daily_bar") == []
    assert [i.code for i in issues] == [CODE_SUSPENSION_ROW]
    assert issues[0].severity is Severity.INFO
    assert issues[0].details["kind"] == "supplier_no_trade"
    assert issues[0].details["days"] == 1
    assert issues[0].trade_date == date(2021, 11, 5)


def test_supplier_row_carrying_a_trade_is_left_to_the_value_checks():
    """Zero prices with real volume are a supplier defect, never a suspension."""
    raw = _raw_daily(
        [
            _TRADED_DAY,
            ("20211105", 0.0, 0.0, 0.0, 10.0, 500.0, 5000.0, 10.0),
        ]
    )
    frame, issues = _canonicalize(raw)

    assert issues == []
    assert list(frame["source"]) == ["tushare", "tushare"]
    codes = {issue.code for issue in check_daily_values(frame, table="daily_bar")}
    assert codes == {CODE_NONPOSITIVE_PRICE, CODE_INVALID_OHLC}


def test_supplier_row_whose_close_breaks_its_own_reference_is_left_alone():
    """A carried close that disagrees with pre_close is not a held reference."""
    raw = _raw_daily(
        [
            _TRADED_DAY,
            ("20211105", 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 9.5),
        ]
    )
    frame, issues = _canonicalize(raw)

    assert issues == []
    assert list(frame["source"]) == ["tushare", "tushare"]
    assert check_daily_values(frame, table="daily_bar") != []


def test_signature_columns_absent_leaves_the_frame_untouched():
    """Offline stubs carry no pre_close, so nothing is inferred from them."""
    raw = _raw_daily([_TRADED_DAY]).drop(columns=["pre_close"])
    frame, issues = _canonicalize(raw)

    assert issues == []
    assert list(frame["source"]) == ["tushare"]


# --------------------------------------------------------------------------- #
# End to end: the update materializes proven suspension bars and passes the
# acceptance completeness recount without any policy change.
# --------------------------------------------------------------------------- #

_WINDOW_START = date(2021, 11, 1)
_WINDOW_END = date(2021, 11, 30)
_UNIVERSE_SYMBOLS = tuple(
    Universe.from_yaml(_REPO_ROOT / "templates" / "project-config" / "universe.yml").symbols
)
_GAPPY_SYMBOL = "000001.SZ"
_GAP_DAYS = {date(2021, 11, 2), date(2021, 11, 3), date(2021, 11, 4)}
#: The supplier's second suspension shape: the session is present but untraded.
_NO_TRADE_SYMBOL = "000651.SZ"
_NO_TRADE_DAYS = {date(2021, 11, 5)}

#: A name whose suspension run opens the window: absent from the window's first
#: open day onwards, so the run has no ``before`` bar inside the requested range.
_HEAD_GAP_SYMBOL = "601318.SH"
_HEAD_GAP_DAYS = frozenset(date(2021, 11, day) for day in (1, 2, 3, 4, 5, 8, 9))
#: The same shape, but the symbol has never traded before the window opens.
_BARE_HEAD_GAP_SYMBOL = "601398.SH"


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


#: ``stock_basic`` 的上市日 stub 复用的固定值。
_STUB_LIST_DATE = date(1991, 1, 2)


@dataclass(frozen=True)
class _SuspendStub:
    """Stubs whose primary daily carries pre_close and one suspended name."""

    name: str
    #: Per-symbol absent days that *open* the window (a head-suspended name).
    head_gap_days: dict[str, frozenset[date]] = field(default_factory=dict)
    #: Symbols with no bar at all before the window opens.
    bare_before_window: frozenset[str] = frozenset()
    #: Every request this stub answered, as (endpoint, symbol, start, end).
    calls: list[tuple[str, str | None, date, date]] = field(default_factory=list)

    def fetch(self, request: DataRequest) -> FetchResult:
        self.calls.append(
            (
                request.endpoint,
                request.symbols[0] if request.symbols else None,
                request.start_date,
                request.end_date,
            )
        )
        return FetchResult(
            source=self.name,
            endpoint=request.endpoint,
            request_key=request_key(request),
            frame=self._frame(request),
            metadata={
                "source": self.name,
                "response_timestamp": "2021-12-01T00:00:00Z",
                "transport_id": self.name,
            },
        )

    def _frame(self, request: DataRequest) -> pd.DataFrame:
        if request.endpoint == "stock_basic":
            return pd.DataFrame(
                [
                    {
                        "ts_code": symbol,
                        "name": f"stub_{symbol}",
                        "list_date": _STUB_LIST_DATE.strftime("%Y%m%d"),
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
            "rights_issue_corporate_actions",
        ):
            return pd.DataFrame()
        if request.endpoint == "trade_cal":
            rows = []
            current = request.start_date
            while current <= request.end_date:
                previous = current - timedelta(days=1)
                while previous.weekday() >= 5:
                    previous -= timedelta(days=1)
                rows.append(
                    {
                        "exchange": request.params["exchange"],
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 1 if current.weekday() < 5 else 0,
                        "pretrade_date": previous.strftime("%Y%m%d"),
                    }
                )
                current += timedelta(days=1)
            return pd.DataFrame(rows)
        symbol = request.symbols[0]
        absent = self.head_gap_days.get(symbol, frozenset())
        days = [
            day
            for day in _weekdays(request.start_date, request.end_date)
            if not (symbol == _GAPPY_SYMBOL and day in _GAP_DAYS)
            and day not in absent
        ]
        if symbol in self.bare_before_window:
            days = [day for day in days if day >= _WINDOW_START]
        rows = []
        for day in days:
            # The supplier's other suspension shape: the session is present as a
            # row with zero open/high/low and zero volume, close carried flat.
            untraded = symbol == _NO_TRADE_SYMBOL and day in _NO_TRADE_DAYS
            price = 0.0 if untraded else 55.0
            rows.append(
                {
                    "code": symbol,
                    "date": day.strftime("%Y%m%d"),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": 55.0,
                    "vol": 0.0 if untraded else 1000.0,
                    "amount": 0.0 if untraded else 55000.0,
                    "pre_close": 55.0,
                }
            )
        return pd.DataFrame(rows)


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
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)

    stubs = {
        name: _SuspendStub(name)
        for name in ("tushare", "akshare", "baostock")
    }
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


def test_update_canonicalizes_supplier_reported_no_trade_rows(tmp_path):
    """A session the supplier reported as a zero-priced row publishes as a
    carried suspension bar instead of blocking the update."""
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
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)

    stubs = {
        name: _SuspendStub(name)
        for name in ("tushare", "akshare", "baostock")
    }
    result = DataPipeline(root, sources=stubs).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )

    # The zero-priced rows would otherwise fail invalid_ohlc/nonpositive_price,
    # both of which block publication.
    assert result.dataset_ref is not None, result.quality_report
    codes = {issue.code for issue in result.quality_report.issues}
    assert CODE_NONPOSITIVE_PRICE not in codes
    assert CODE_INVALID_OHLC not in codes

    with DatasetReader(root).open(result.dataset_ref.version) as dataset:
        daily = dataset.read("daily_bar")
    # All the days but the untraded one are ordinary rows, so the repair
    # replaced exactly one bar rather than reshaping the symbol's series.
    traded = daily[
        (daily["symbol"] == _NO_TRADE_SYMBOL)
        & (daily["trade_date"] == pd.Timestamp("2021-11-08"))
    ].iloc[0]
    assert traded["source"] == "tushare"
    assert traded["volume"] == 100000
    untraded = daily[
        (daily["symbol"] == _NO_TRADE_SYMBOL)
        & (daily["trade_date"] == pd.Timestamp("2021-11-05"))
    ]
    assert len(untraded) == 1
    bar = untraded.iloc[0]
    assert bar["source"] == "tushare_suspend"
    assert bar["open"] == bar["high"] == bar["low"] == bar["close"] == 55.0
    assert bar["volume"] == 0


def _fixture_root(tmp_path) -> Path:
    """A fresh synthetic project bootstrapped from the repository templates."""
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
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)
    return root


def _probes(stub: _SuspendStub) -> list[tuple[str, str | None, date, date]]:
    """Requests the stub answered with a start before the window opened."""
    return [
        call
        for call in stub.calls
        if call[0] == "daily" and call[2] < _WINDOW_START
    ]


def test_update_anchors_a_suspension_run_that_opens_the_window(tmp_path):
    """A run with no bar inside the window is proven by the symbol's own
    pre-window bar, fetched for proof only and never published."""
    root = _fixture_root(tmp_path)
    tushare = _SuspendStub("tushare", head_gap_days={_HEAD_GAP_SYMBOL: _HEAD_GAP_DAYS})
    stubs = {
        "tushare": tushare,
        "akshare": _SuspendStub("akshare"),
        "baostock": _SuspendStub("baostock"),
    }
    result = DataPipeline(root, sources=stubs).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )

    assert result.dataset_ref is not None, result.quality_report
    codes = {issue.code for issue in result.quality_report.issues}
    assert CODE_SUSPENSION_RUN_UNVERIFIED not in codes

    with DatasetReader(root).open(result.dataset_ref.version) as dataset:
        daily = dataset.read("daily_bar")
    carried = daily[
        (daily["symbol"] == _HEAD_GAP_SYMBOL)
        & (daily["trade_date"] == pd.Timestamp("2021-11-01"))
    ]
    assert len(carried) == 1
    row = carried.iloc[0]
    assert row["source"] == "tushare_suspend"
    assert row["volume"] == 0
    assert row["close"] == 55.0

    # The proof input stays proof input: no pre-window bar is published.
    assert daily["trade_date"].min() >= pd.Timestamp(_WINDOW_START)
    # Only the candidate was probed -- the mid-window gap name already has a bar
    # on the window's first open day, so its run never needs an outside anchor.
    assert [call[1] for call in _probes(tushare)] == [_HEAD_GAP_SYMBOL]


def test_update_leaves_a_head_run_with_no_pre_window_history_unproven(tmp_path):
    """A symbol that never traded before the window has no anchor to find, so
    its head run stays an honest gap instead of a materialized bar."""
    root = _fixture_root(tmp_path)
    tushare = _SuspendStub(
        "tushare",
        head_gap_days={_BARE_HEAD_GAP_SYMBOL: _HEAD_GAP_DAYS},
        bare_before_window=frozenset({_BARE_HEAD_GAP_SYMBOL}),
    )
    stubs = {
        "tushare": tushare,
        "akshare": _SuspendStub("akshare"),
        "baostock": _SuspendStub("baostock"),
    }
    result = DataPipeline(root, sources=stubs).update(
        DataUpdateRequest(start_date=_WINDOW_START, end_date=_WINDOW_END)
    )

    assert result.dataset_ref is not None, result.quality_report
    codes = {issue.code for issue in result.quality_report.issues}
    assert CODE_SUSPENSION_RUN_UNVERIFIED in codes

    with DatasetReader(root).open(result.dataset_ref.version) as dataset:
        daily = dataset.read("daily_bar")
    assert daily[
        (daily["symbol"] == _BARE_HEAD_GAP_SYMBOL)
        & (daily["source"] == "tushare_suspend")
    ].empty

    # The probe stepped back more than once and stopped at the listing date:
    # the depth is the symbol's own history, not a guessed constant.
    probes = _probes(tushare)
    assert [call[1] for call in probes] == [_BARE_HEAD_GAP_SYMBOL] * len(probes)
    assert len(probes) >= 2
    assert min(call[2] for call in probes) == _STUB_LIST_DATE
