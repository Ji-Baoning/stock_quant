"""No-lookahead and reproducibility integration tests for Momentum60 (Task 6).

The factor is computed once on a base context and again on a context whose
dataset has *future* rows appended (and extra weekly signal dates).  Every
historical factor row must be bit-identical: appending data after a signal
date can never rewrite the factor value at that signal date.  Both frames must
also write to byte-identical Parquet.  Everything runs offline on
deterministic in-memory datasets.

Task 5 (point-in-time universe) adds the membership-first gate: a context
whose ``members_on`` resolves per signal day must keep a removed member's
history but never produce a NEW signal after its removal day, and the
research runner's factor stage must persist the per-signal-day membership
snapshot hashes it computed under (``factor_metadata.json``).
"""

import hashlib
import json
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from test_research_runner import _SPEC, _new_env

from stock_quant.data_model.adjusted_bar import build_adjusted_bars
from stock_quant.data_model.corporate_action_coverage import (
    OUTCOME_SUCCESS_EVENTS,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    CORPORATE_ACTION_QUARANTINE_COLUMNS,
    DAILY_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
)
from stock_quant.data_model.universe_membership import resolve_memberships
from stock_quant.data_quality.models import QualityReport
from stock_quant.factors.base import FactorContext
from stock_quant.factors.momentum import Momentum60
from stock_quant.research.runner import ResearchRunner, _DatasetFactorAdapter
from stock_quant.research.universe import UniverseResolver

_SYMBOL = "600000.SH"
_FACTOR_INPUT_COLUMNS = [
    "trade_date",
    "symbol",
    "source",
    "adjustment",
    "adjusted_close",
    "quality_severity",
    "listed_trading_days",
]
_BASE_SIGNAL_INDEXES = (60, 65, 70, 75)
_FUTURE_SIGNAL_INDEXES = (80, 85)


def _sessions(count: int, start: date = date(2023, 1, 2)) -> list[date]:
    """``count`` consecutive weekday trading dates starting at ``start``."""
    out: list[date] = []
    day = start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


class _FrameDataset:
    """Deterministic in-memory ``FactorDataset`` over one fixed frame."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def factor_input(self) -> pd.DataFrame:
        return self._frame.copy()


def _symbol_rows(
    sessions: list[date], symbol: str = _SYMBOL, close_start: float = 100.0
) -> list[dict]:
    rows: list[dict] = []
    for index, day in enumerate(sessions):
        rows.append(
            {
                "trade_date": day,
                "symbol": symbol,
                "source": "baostock",
                "adjustment": "qfq",
                "adjusted_close": close_start + index,
                "quality_severity": "INFO",
                "listed_trading_days": 200 + index,
            }
        )
    return rows


def _snapshot_hash(day: date, symbols: tuple[str, ...]) -> str:
    """A deterministic stand-in for the resolver's daily snapshot hash."""
    payload = f"{day.isoformat()}\x1f{','.join(symbols)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _make_context(
    rows: list[dict],
    *,
    start_date: date,
    end_date: date,
    signal_dates: tuple[date, ...],
    members: dict[date, tuple[str, ...]] | None = None,
) -> FactorContext:
    frame = pd.DataFrame(rows, columns=_FACTOR_INPUT_COLUMNS)
    if members is None:
        members_on = None
        snapshot_for = None
    else:

        def members_on(day: date) -> tuple[str, ...]:
            return members[day]

        def snapshot_for(day: date) -> str:
            return _snapshot_hash(day, members[day])

    return FactorContext(
        dataset=_FrameDataset(frame),
        universe_version="universe-v1",
        start_date=start_date,
        end_date=end_date,
        signal_dates=signal_dates,
        members_on=members_on,
        membership_snapshot_for=snapshot_for,
    )


def _context_ending_at(
    last_index: int, signal_indexes: tuple[int, ...]
) -> FactorContext:
    sessions = _sessions(last_index + 1)
    signal_dates = tuple(sessions[index] for index in signal_indexes)
    return _make_context(
        _symbol_rows(sessions),
        start_date=sessions[0],
        end_date=sessions[-1],
        signal_dates=signal_dates,
    )


@pytest.fixture
def base_context() -> FactorContext:
    return _context_ending_at(75, _BASE_SIGNAL_INDEXES)


@pytest.fixture
def context_with_future() -> FactorContext:
    # Same history plus ten later sessions and two later weekly signal dates.
    return _context_ending_at(85, (*_BASE_SIGNAL_INDEXES, *_FUTURE_SIGNAL_INDEXES))


def _parquet_bytes(frame: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def test_future_rows_do_not_change_historical_factor(base_context, context_with_future):
    before = Momentum60().compute(base_context).frame
    after = Momentum60().compute(context_with_future).frame
    after = after[after["trade_date"] <= base_context.end_date]
    pd.testing.assert_frame_equal(before, after.reset_index(drop=True))


def test_future_only_symbol_does_not_change_historical_factor(base_context):
    future_sessions = _sessions(86)
    rows = _symbol_rows(future_sessions)
    # A brand-new symbol whose first bar lands after every base signal date.
    rows += _symbol_rows(future_sessions[80:], symbol="000001.SZ", close_start=50.0)
    signal_indexes = (*_BASE_SIGNAL_INDEXES, *_FUTURE_SIGNAL_INDEXES)
    signal_dates = tuple(future_sessions[index] for index in signal_indexes)
    future_context = _make_context(
        rows,
        start_date=future_sessions[0],
        end_date=future_sessions[-1],
        signal_dates=signal_dates,
    )

    before = Momentum60().compute(base_context).frame
    after = Momentum60().compute(future_context).frame
    after = after[after["trade_date"] <= base_context.end_date]
    pd.testing.assert_frame_equal(before, after.reset_index(drop=True))


def test_no_lookahead_rows_are_byte_stable_in_parquet(
    base_context, context_with_future
):
    before = Momentum60().compute(base_context).frame
    after = Momentum60().compute(context_with_future).frame
    after = after[after["trade_date"] <= base_context.end_date]
    assert _parquet_bytes(before) == _parquet_bytes(after.reset_index(drop=True))


# --------------------------------------------------------------------------- #
# Membership-first candidate filtering (point-in-time universe, Task 5)
# --------------------------------------------------------------------------- #


def test_removed_member_makes_no_new_signal():
    """A removed member keeps its history but makes no NEW signal after removal.

    ``600000.SH`` is a member through 2020-06-14 (the last membership signal
    here is Friday 2020-06-12) and is removed from 2020-06-15: its rows at the
    last membership day stay, it produces no row at all -- valid or invalid --
    on the first post-removal signal day, and a still-member name keeps
    producing rows on that same day.
    """
    sessions = _sessions(130, start=date(2020, 1, 2))
    last_member_day = date(2020, 6, 12)
    removal_day = date(2020, 6, 15)
    rows = _symbol_rows(sessions)  # 600000.SH
    rows += _symbol_rows(sessions, symbol="000001.SZ", close_start=50.0)
    context = _make_context(
        rows,
        start_date=sessions[0],
        end_date=sessions[-1],
        signal_dates=(last_member_day, removal_day),
        members={
            last_member_day: ("600000.SH", "000001.SZ"),
            removal_day: ("000001.SZ",),
        },
    )

    frame = Momentum60().compute(context).frame

    assert "600000.SH" in set(
        frame.loc[frame["trade_date"].eq(last_member_day), "symbol"]
    )
    assert "600000.SH" not in set(
        frame.loc[frame["trade_date"].eq(removal_day), "symbol"]
    )
    assert "000001.SZ" in set(
        frame.loc[frame["trade_date"].eq(removal_day), "symbol"]
    )


def test_factor_stage_metadata_carries_daily_snapshot_hash(tmp_path):
    """The factor stage persists the membership snapshot hashes it ran under.

    ``factor_metadata.json`` in the run workspace records the frozen universe
    identity plus the per-signal-day member counts and snapshot hashes; they
    equal the run's persisted metrics map and an independently built frozen
    resolver's hashes, so the factor artifact is auditable against the exact
    point-in-time membership day by day.
    """
    env = _new_env(tmp_path)
    runner = ResearchRunner(env.root, config_root=env.config_root)
    experiment = runner.run(_SPEC)

    metadata = json.loads(
        (env.root / "data" / "runs" / runner.run_id / "factor_metadata.json")
        .read_text(encoding="utf-8")
    )
    metrics = json.loads(
        (experiment.path / "metrics.json").read_text(encoding="utf-8")
    )

    assert metadata["universe_id"] == "csi300"
    assert metadata["universe_version"] == experiment.manifest.universe_version
    assert (
        metadata["daily_snapshots"]
        == metrics["meta"]["universe_daily_snapshots"]
    )
    assert (
        metadata["daily_member_counts"]
        == metrics["meta"]["universe_daily_member_counts"]
    )

    resolver = UniverseResolver(
        env.definition,
        resolve_memberships(list(env.facts), {}),
        facts=list(env.facts),
    )
    for day_text, snapshot in metadata["daily_snapshots"].items():
        assert snapshot == resolver.snapshot_for(date.fromisoformat(day_text))


# --------------------------------------------------------------------------- #
# Dataset-boundary pinning: a future action cannot rewrite prior factor rows
# --------------------------------------------------------------------------- #

_CALENDAR_START = date(2018, 1, 1)
_LIST_DATE = date(2018, 1, 2)
_INGESTED = pd.Timestamp("2026-09-04T08:00:00Z")
_SESSION_COUNT = 100
_ACTION_EX_INDEX = 90  # inside the published bars, after every signal date


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _daily_bar(sessions: list[date]) -> pd.DataFrame:
    n = len(sessions)
    closes = [100.0 + index for index in range(n)]
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(sessions),
            "symbol": _SYMBOL,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [0] * n,
            "amount": [0.0] * n,
            "adjustment": "unadjusted",
            "source": "synthetic",
            "ingested_at": [_INGESTED] * n,
        }
    )[DAILY_COLUMNS]


def _corporate_actions(ex_date: date | None) -> pd.DataFrame:
    """No facts, or one verified implemented cash dividend at ``ex_date``."""
    if ex_date is None:
        return pd.DataFrame(columns=CORPORATE_ACTION_COLUMNS)
    return pd.DataFrame(
        {
            "symbol": [_SYMBOL],
            "announcement_date": [ex_date - timedelta(days=14)],
            "record_date": [ex_date - timedelta(days=1)],
            "ex_date": [ex_date],
            "cash_dividend_per_share": [0.5],
            "bonus_share_ratio": [0.0],
            "capitalization_ratio": [0.0],
            "rights_issue_ratio": [0.0],
            "rights_issue_price": [0.0],
            "source": ["synthetic"],
            "status": ["implemented"],
        }
    )[CORPORATE_ACTION_COLUMNS]


def _security_master() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [_SYMBOL],
            "name": [f"synth_{_SYMBOL}"],
            "exchange": ["SH"],
            "board": ["sh_main"],
            "list_date": pd.to_datetime([_LIST_DATE]),
            "delist_date": pd.Series(pd.NaT, index=range(1),
                                     dtype="datetime64[ns]"),
            "list_status": ["L"],
        }
    )[SECURITY_MASTER_COLUMNS]


def _coverage(sessions: list[date], *, with_events: bool) -> pd.DataFrame:
    """Trusted evidence matching the facts table: ``VERIFIED_EMPTY`` when no
    action exists, ``VERIFIED`` with event-bearing endpoint outcomes when one
    does, so the published provenance never claims an empty check over facts.
    """
    endpoints = (
        "cninfo_corporate_actions",
        "eastmoney_corporate_actions",
        "rights_issue_corporate_actions",
    )
    if with_events:
        record = coverage_record(
            _SYMBOL,
            sessions[0],
            sessions[-1],
            CoverageStatus.VERIFIED,
            None,
            sources=[
                {"endpoint": endpoint, "outcome": OUTCOME_SUCCESS_EVENTS}
                for endpoint in endpoints
            ],
            checked_at=_INGESTED,
        )
    else:
        record = coverage_record(
            _SYMBOL,
            sessions[0],
            sessions[-1],
            CoverageStatus.VERIFIED_EMPTY,
            None,
            checked_at=_INGESTED,
        )
    return coverage_frame([record])


def _trading_calendar(sessions: list[date]) -> pd.DataFrame:
    days = _weekdays(_CALENDAR_START, sessions[-1])
    return pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(days),
            "is_trading_day": [True] * len(days),
        }
    )[TRADING_CALENDAR_COLUMNS]


def _publish_market(root: Path, ex_date: date | None) -> str:
    """Publish one deterministic market version; ``ex_date=None`` has no actions."""
    sessions = _sessions(_SESSION_COUNT)
    daily = _daily_bar(sessions)
    actions = _corporate_actions(ex_date)
    empty_quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
    coverage = _coverage(sessions, with_events=ex_date is not None)
    tables = {
        "daily_bar": daily,
        "adjusted_bar": build_adjusted_bars(
            daily, actions, empty_quarantine, coverage, symbols=(_SYMBOL,),
        ),
        "security_master": _security_master(),
        "corporate_action": actions,
        "corporate_action_quarantine": empty_quarantine,
        "corporate_action_coverage": coverage,
        "trading_calendar": _trading_calendar(sessions),
    }
    return DatasetPublisher(root).publish(tables, QualityReport()).version


def _factor_frame(root: Path, version: str, sessions: list[date]) -> pd.DataFrame:
    signal_dates = tuple(sessions[index] for index in _BASE_SIGNAL_INDEXES)
    with DatasetReader(root).open(version) as context:
        adapter = _DatasetFactorAdapter(context=context, universe_symbols=(_SYMBOL,))
        factor_context = FactorContext(
            dataset=adapter,
            universe_version="universe-v1",
            start_date=sessions[0],
            end_date=sessions[-1],
            signal_dates=signal_dates,
        )
        return Momentum60().compute(factor_context).frame


def test_future_corporate_action_cannot_change_prior_factor_results(tmp_path):
    """v1 (no actions) vs v2 (one future verified cash dividend), both pinned.

    The dividend acts for the first time on its ``ex_date`` -- inside the
    published bars but after every signal date -- so every factor row computed
    before that date must be bit-identical across the two pinned versions.
    """
    sessions = _sessions(_SESSION_COUNT)
    ex_date = sessions[_ACTION_EX_INDEX]
    before_version = _publish_market(tmp_path, ex_date=None)
    after_version = _publish_market(tmp_path, ex_date=ex_date)
    # Sanity: the action really does move the series on and after its ex_date.
    with DatasetReader(tmp_path).open(before_version) as context:
        before_adjusted = context.read("adjusted_bar")
    with DatasetReader(tmp_path).open(after_version) as context:
        after_adjusted = context.read("adjusted_bar")
    at_ex = after_adjusted["trade_date"] == pd.Timestamp(ex_date)
    assert (
        after_adjusted.loc[at_ex, "adjusted_close"].tolist()
        != before_adjusted.loc[at_ex, "adjusted_close"].tolist()
    )
    before = _factor_frame(tmp_path, before_version, sessions)
    after = _factor_frame(tmp_path, after_version, sessions)
    pd.testing.assert_frame_equal(
        before[before["trade_date"] < ex_date].reset_index(drop=True),
        after[after["trade_date"] < ex_date].reset_index(drop=True),
    )
