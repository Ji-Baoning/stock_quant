"""End-to-end research-run orchestration over a synthetic pinned dataset.

The runner must resolve the ``CURRENT`` dataset *once* before any factor
execution, publish an immutable 15-artifact experiment only after a complete,
evaluated run, keep failures auditable under ``data/runs`` without touching
``CURRENT`` and without creating a partial experiment directory, publish
REJECTED experiments as full results, and resume a re-run of the same
experiment id without recomputing any stage.

Every test publishes a deterministic synthetic market -- twelve SH main-board
equities whose closes follow ``55 * exp(growth * (session - (n - 1)))`` plus two
flat benchmark index rows -- under a temporary project root, then runs the
repository's real ``momentum_60d`` spec (``configs/experiments/
momentum_60d.yml``) pointed at that project.  The growth vector ranks are
distinct and time-stable, so the weekly top-10 of the 12 is the ten largest
``growth`` values on every signal date and the weekly net rebalance keeps every
name on at most one order side per execution day.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.backtest.models import BUY
from stock_quant.data_model.adjusted_bar import build_adjusted_bars
from stock_quant.data_model.corporate_action_coverage import (
    OUTCOME_SUCCESS_EVENTS,
    CoverageReason,
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
from stock_quant.data_model.security_master import (
    MASTER_SOURCE_STOCK_BASIC,
    ListStatus,
    master_coverage_frame,
    master_coverage_record,
)
from stock_quant.data_model.trading_rules import REASON_SELL_AT_LOWER_LIMIT
from stock_quant.data_quality.models import QualityReport
from stock_quant.factors.momentum import Momentum60
from stock_quant.research.models import (
    REQUIRED_ARTIFACTS,
    Evaluation,
    ExperimentEvaluation,
    ResearchRunFailed,
)
from stock_quant.research.runner import ResearchRunner, _DatasetFactorAdapter
from stock_quant.research.trust import DataTrustMode

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = "configs/experiments/momentum_60d.yml"
_CAL_START = date(2018, 1, 1)
_BARS_START = date(2019, 8, 1)
_BARS_END = date(2026, 9, 4)
_LIST_DATE = date(2018, 1, 2)
_BASE_PRICE = 55.0
_BENCHMARK_SYMBOLS = ("000300.SH", "000905.SH")

#: Twelve SH main-board equities, growth ordered ascending so the weekly
#: top-10 portfolio is always the ten names with the largest growth.
EQUITY_GROWTH = (
    ("600000.SH", 0.00042),
    ("601398.SH", 0.00052),
    ("600028.SH", 0.00063),
    ("600036.SH", 0.00074),
    ("600030.SH", 0.00086),
    ("601166.SH", 0.00098),
    ("601088.SH", 0.00110),
    ("600519.SH", 0.00122),
    ("601919.SH", 0.00134),
    ("603288.SH", 0.00145),
    ("601318.SH", 0.00155),
    ("601857.SH", 0.00164),
)

_INGESTED = pd.Timestamp("2026-09-04T08:00:00Z")


@dataclass(frozen=True)
class _Env:
    """A temporary project with one published synthetic dataset version."""

    root: Path
    version: str


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _bars(
    sessions: list[date],
    *,
    index_close: tuple[float, float],
    limit_locked_symbols: tuple[str, ...] = (),
    fresh: tuple[str, date, float] | None = None,
) -> pd.DataFrame:
    """One deterministic daily_bar frame for all instruments over ``sessions``.

    ``limit_locked_symbols`` names equities whose bar on every Monday (the
    weekly execution day) opens at half the prior session's close, far below
    the main-board lower price limit.  Such names stay ranked by the factor
    (their signal-day closes are untouched) but every Monday *sell* of them is
    price-limit-blocked by the engine -- a rejection, not a hard error.
    ``fresh`` is ``(symbol, list_date, growth)`` for a recently-listed symbol
    whose bars begin at its list date (the new-IPO acceptance fixture).
    """
    n = len(sessions)
    frames: list[pd.DataFrame] = []
    for symbol, growth in EQUITY_GROWTH:
        closes = [_BASE_PRICE * math.exp(growth * (index - (n - 1)))
                  for index in range(n)]
        if symbol in limit_locked_symbols:
            opens = list(closes)
            for index in range(1, n):
                if sessions[index].weekday() == 0:  # Monday == execution day
                    opens[index] = 0.5 * closes[index - 1]
            frames.append(_instrument_frame(sessions, symbol, closes, opens))
        else:
            frames.append(_instrument_frame(sessions, symbol, closes))
    if fresh is not None:
        symbol, list_date, growth = fresh
        start_at = next(
            index for index, day in enumerate(sessions) if day >= list_date
        )
        closes = [
            _BASE_PRICE * math.exp(growth * (index - (n - 1)))
            for index in range(start_at, n)
        ]
        frames.append(_instrument_frame(sessions[start_at:], symbol, closes))
    for symbol, level in zip(_BENCHMARK_SYMBOLS, index_close):
        frames.append(_instrument_frame(sessions, symbol, [level] * n))
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def _instrument_frame(
    sessions: list[date],
    symbol: str,
    closes: list[float],
    opens: list[float] | None = None,
) -> pd.DataFrame:
    n = len(sessions)
    opens = closes if opens is None else opens
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(sessions),
            "symbol": symbol,
            "open": opens,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [0] * n,
            "amount": [0.0] * n,
            "adjustment": "unadjusted",
            "source": "synthetic",
            "ingested_at": [_INGESTED] * n,
        }
    )


def _security_master(
    fresh: tuple[str, date, float] | None = None,
) -> pd.DataFrame:
    """One security_master row per EQUITY_GROWTH symbol, plus ``fresh``.

    ``fresh`` is ``(symbol, list_date, growth)`` for the new-IPO acceptance
    fixture; every row keeps ``list_status="L"``.  ``name`` stays a synthetic
    label (universe.yml owns the real labels, as in production).
    """
    symbols = [symbol for symbol, _ in EQUITY_GROWTH]
    list_dates = [_LIST_DATE] * len(symbols)
    if fresh is not None:
        symbols = symbols + [fresh[0]]
        list_dates = list_dates + [fresh[1]]
    count = len(symbols)
    return pd.DataFrame(
        {
            "symbol": symbols,
            "name": [f"synth_{symbol}" for symbol in symbols],
            "exchange": ["SH"] * count,
            "board": ["sh_main"] * count,
            "list_date": pd.to_datetime(list_dates),
            "delist_date": pd.Series(
                pd.NaT, index=range(count), dtype="datetime64[ns]"
            ),
            "list_status": ["L"] * count,
        }
    )[SECURITY_MASTER_COLUMNS]


def _corporate_action() -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "symbol": pd.Series([], dtype="object"),
            "announcement_date": pd.Series([], dtype="object"),
            "record_date": pd.Series([], dtype="object"),
            "ex_date": pd.Series([], dtype="object"),
            "cash_dividend_per_share": pd.Series([], dtype="float64"),
            "bonus_share_ratio": pd.Series([], dtype="float64"),
            "capitalization_ratio": pd.Series([], dtype="float64"),
            "rights_issue_ratio": pd.Series([], dtype="float64"),
            "rights_issue_price": pd.Series([], dtype="float64"),
            "source": pd.Series([], dtype="object"),
            "status": pd.Series([], dtype="object"),
        }
    )
    return frame[CORPORATE_ACTION_COLUMNS]


def _trading_calendar() -> pd.DataFrame:
    days = _weekdays(_CAL_START, _BARS_END)
    return pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(days),
            "is_trading_day": [True] * len(days),
        }
    )[TRADING_CALENDAR_COLUMNS]


def _universe_symbols() -> tuple[str, ...]:
    return tuple(symbol for symbol, _ in EQUITY_GROWTH)


def _coverage(
    symbols: tuple[str, ...],
    *,
    status: CoverageStatus,
    reason: CoverageReason | None = None,
    window_start: date = _CAL_START,
    window_end: date = _BARS_END,
    event_symbols: tuple[str, ...] = (),
) -> pd.DataFrame:
    """One deterministic coverage row per symbol over a common window.

    The default window is the full synthetic calendar, a superset of the
    execution window any momentum spec derives, so a single row per symbol is
    always enough evidence when the status is trusted.  ``event_symbols``
    names holdings whose evidence is ``VERIFIED`` with
    ``success_with_events`` endpoints (facts exist and were reconciled)
    instead of the plain status, so a dataset carrying corporate-action facts
    never claims an empty-verified provenance for them.
    """
    endpoints = ("cninfo_corporate_actions", "eastmoney_corporate_actions")
    records = []
    for symbol in sorted(symbols):
        if symbol in event_symbols:
            records.append(
                coverage_record(
                    symbol,
                    window_start,
                    window_end,
                    CoverageStatus.VERIFIED,
                    None,
                    sources=[
                        {"endpoint": endpoint, "outcome": OUTCOME_SUCCESS_EVENTS}
                        for endpoint in endpoints
                    ],
                    checked_at=_INGESTED,
                )
            )
        else:
            records.append(
                coverage_record(
                    symbol,
                    window_start,
                    window_end,
                    status,
                    reason,
                    checked_at=_INGESTED,
                )
            )
    return coverage_frame(records)


def _master_coverage(symbols: tuple[str, ...]) -> pd.DataFrame:
    """One deterministic security_master_coverage row per symbol."""
    records = [
        master_coverage_record(
            symbol,
            list_date=_LIST_DATE,
            list_status=ListStatus.L,
            source=MASTER_SOURCE_STOCK_BASIC,
            snapshot_sha256="f" * 64,
            sdk_version="fixture",
            checked_at=_INGESTED,
        )
        for symbol in sorted(symbols)
    ]
    return master_coverage_frame(records)


def _cash_action(symbol: str, ex_date: date) -> pd.DataFrame:
    """One verified, implemented 0.5/share cash dividend at ``ex_date``.

    Complete point-in-time facts (announcement two weeks before, record the
    day before, no rights issue), so the adjusted-bar builder accepts it and
    applies it exactly once on the ``ex_date``.
    """
    return pd.DataFrame(
        {
            "symbol": [symbol],
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


def _publish_synthetic_dataset(
    project_root: Path,
    *,
    index_close: tuple[float, float] = (4000.0, 2000.0),
    limit_locked_symbols: tuple[str, ...] = (),
    coverage: pd.DataFrame | None = None,
    master_coverage: pd.DataFrame | None = None,
    fresh: tuple[str, date, float] | None = None,
    corporate_actions: pd.DataFrame | None = None,
) -> str:
    """Publish the synthetic market under ``project_root``; return its version.

    The default dataset carries an explicit ``VERIFIED_EMPTY`` corporate-action
    coverage row per symbol AND a ``security_master_coverage`` row per symbol
    (both evidence tables), so the ResearchRunner's RESEARCH gates pass over the
    default market.  ``fresh`` optionally adds one recently-listed symbol
    ``(symbol, list_date, growth)`` whose bars begin at its list date (the
    new-IPO acceptance fixture); ``master_coverage`` overrides the evidence
    (an empty frame publishes an empty table that fails the master gate);
    ``corporate_actions`` overrides the (empty) facts table, e.g. with
    ``_cash_action`` for a total-return market.
    """
    master = _security_master(fresh)
    symbols = tuple(master["symbol"])
    if coverage is None:
        coverage = _coverage(symbols, status=CoverageStatus.VERIFIED_EMPTY)
    if master_coverage is None:
        master_coverage = _master_coverage(symbols)
    daily = _bars(
        _weekdays(_BARS_START, _BARS_END),
        index_close=index_close,
        limit_locked_symbols=limit_locked_symbols,
        fresh=fresh,
    )
    corporate_actions = (
        _corporate_action()
        if corporate_actions is None
        else corporate_actions
    )
    empty_quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
    tables = {
        "daily_bar": daily,
        "adjusted_bar": build_adjusted_bars(
            daily,
            corporate_actions,
            empty_quarantine,
            coverage,
            symbols=symbols,
        ),
        "security_master": master,
        "security_master_coverage": master_coverage,
        "corporate_action": corporate_actions,
        "corporate_action_quarantine": empty_quarantine,
        "corporate_action_coverage": coverage,
        "trading_calendar": _trading_calendar(),
    }
    return DatasetPublisher(project_root).publish(tables, QualityReport()).version


#: A narrow untrusted evidence band: wide enough that the RESEARCH execution
#: window is not fully trusted, narrow enough that momentum windows opening 61
#: observations past the band recover, so an ENGINEERING diagnostic can still
#: replay a real backtest (point-in-time factor rows inside and just after the
#: band are ERROR-quality and stay invalid).
_UNTRUSTED_BAND = (date(2020, 6, 1), date(2020, 6, 5))


def _publish_dataset_with_untrusted_coverage(project_root: Path) -> str:
    """Republish CURRENT with an untrusted evidence band over every holding."""
    return _publish_synthetic_dataset(
        project_root,
        coverage=_coverage(
            _universe_symbols(),
            status=CoverageStatus.UNTRUSTED,
            reason=CoverageReason.SOURCE_FETCH_FAILED,
            window_start=_UNTRUSTED_BAND[0],
            window_end=_UNTRUSTED_BAND[1],
        ),
    )


def _publish_dataset_with_verified_empty_coverage(project_root: Path) -> str:
    """Republish CURRENT so every holding's coverage is explicitly VERIFIED_EMPTY."""
    return _publish_synthetic_dataset(
        project_root,
        coverage=_coverage(
            _universe_symbols(), status=CoverageStatus.VERIFIED_EMPTY
        ),
    )


def _publish_legacy_dataset(project_root: Path) -> str:
    """Republish CURRENT in the pre-adjusted_bar six-table legacy shape.

    The dataset is otherwise healthy (evidence tables, calendar, master), so a
    research run can only fail because the pinned version has no ``adjusted_bar``
    table for the factor adapter to read.
    """
    master = _security_master()
    symbols = tuple(master["symbol"])
    tables = {
        "daily_bar": _bars(
            _weekdays(_BARS_START, _BARS_END), index_close=(4000.0, 2000.0)
        ),
        "security_master": master,
        "security_master_coverage": _master_coverage(symbols),
        "corporate_action": _corporate_action(),
        "corporate_action_coverage": _coverage(
            symbols, status=CoverageStatus.VERIFIED_EMPTY
        ),
        "trading_calendar": _trading_calendar(),
    }
    return DatasetPublisher(project_root).publish(tables, QualityReport()).version


def _new_env(tmp_path) -> _Env:
    project_root = tmp_path / "project"
    version = _publish_synthetic_dataset(project_root)
    return _Env(root=project_root, version=version)


# --------------------------------------------------------------------------- #
# Injected observers / evaluators / providers
# --------------------------------------------------------------------------- #


def _raise_on(stage: str):
    def observer(current_stage, _state):
        if current_stage == stage:
            raise RuntimeError(f"injected failure at stage {stage!r}")
    return observer


class _CurrentSwitcher:
    """Publishes a different dataset version when the run reaches ``pin``."""

    def __init__(self, project_root: Path, original_version: str) -> None:
        self.project_root = project_root
        self.original_version = original_version
        self.flipped_version: str | None = None

    def __call__(self, stage, _state) -> None:
        if stage == "pin":
            self.flipped_version = _publish_synthetic_dataset(
                self.project_root, index_close=(4001.0, 2000.0)
            )


class _RejectEvaluator:
    """Always rejects (published REJECTED experiment with a reason)."""

    def evaluate(self, metrics: dict[str, object]) -> Evaluation:
        del metrics
        return Evaluation(
            ExperimentEvaluation.REJECTED, "below_qualifying_names"
        )


class _CountingFactorProvider:
    """A real Momentum60 provider that counts its invocations."""

    def __init__(self) -> None:
        self.calls = 0

    def provide(self) -> dict[str, Momentum60]:
        self.calls += 1
        factor = Momentum60()
        return {factor.name: factor}


# --------------------------------------------------------------------------- #
# Shared fixtures (one published project per test)
# --------------------------------------------------------------------------- #


@pytest.fixture
def env(tmp_path) -> _Env:
    return _new_env(tmp_path)


@pytest.fixture
def runner(env: _Env) -> ResearchRunner:
    return ResearchRunner(env.root, config_root=_REPO_ROOT)


@pytest.fixture
def current_switcher(env: _Env) -> _CurrentSwitcher:
    return _CurrentSwitcher(env.root, env.version)


@pytest.fixture
def failing_runner(env: _Env) -> ResearchRunner:
    return ResearchRunner(env.root, config_root=_REPO_ROOT)


# --------------------------------------------------------------------------- #
# The brief's behaviour tests
# --------------------------------------------------------------------------- #


def test_runner_freezes_current_before_factor_execution(runner, current_switcher):
    experiment = runner.run(_SPEC, stage_observer=current_switcher)
    assert current_switcher.flipped_version is not None
    assert current_switcher.flipped_version != current_switcher.original_version
    assert experiment.manifest.dataset_version == current_switcher.original_version
    spec_text = (experiment.path / "experiment_spec.yml").read_text()
    assert "CURRENT" not in spec_text
    assert current_switcher.original_version in spec_text


def test_runner_publishes_complete_artifact_contract(runner):
    experiment = runner.run(_SPEC)
    assert set(p.name for p in experiment.path.iterdir()) == REQUIRED_ARTIFACTS
    assert experiment.manifest.status in ("ACCEPTED", "REJECTED")
    assert runner.latest_run_manifest().status == "COMPLETED"
    assert (experiment.path.parent / "registry.parquet").is_file()


def test_failure_keeps_previous_current_and_auditable_run(failing_runner, env):
    with pytest.raises(ResearchRunFailed):
        failing_runner.run(_SPEC, stage_observer=_raise_on("pin"))
    assert failing_runner.latest_run_manifest().status == "FAILED"
    assert failing_runner.latest_run_manifest().failed_stage == "pin"
    assert failing_runner.latest_run_manifest().error is not None
    assert DatasetPublisher(env.root).current().version == env.version
    assert not failing_runner.partial_experiment_exists()


def test_rejected_experiment_still_publishes_full_artifact_set(env):
    evaluator = _RejectEvaluator()
    rejected_runner = ResearchRunner(
        env.root, config_root=_REPO_ROOT, evaluator=evaluator
    )
    experiment = rejected_runner.run(_SPEC)
    assert experiment.manifest.status == "REJECTED"
    assert experiment.manifest.evaluation_reason == "below_qualifying_names"
    assert set(p.name for p in experiment.path.iterdir()) == REQUIRED_ARTIFACTS


def test_rerun_reuses_same_experiment_and_skips_factor(tmp_path):
    env = _new_env(tmp_path)
    provider = _CountingFactorProvider()
    first_runner = ResearchRunner(
        env.root, config_root=_REPO_ROOT, factor_provider=provider.provide
    )
    first = first_runner.run(_SPEC)
    assert provider.calls == 1

    fresh_runner = ResearchRunner(
        env.root, config_root=_REPO_ROOT, factor_provider=provider.provide
    )
    second = fresh_runner.run(_SPEC)
    assert provider.calls == 1  # factor stage resumed, not recomputed
    assert second.path == first.path
    assert fresh_runner.latest_run_manifest().status == "COMPLETED"
    assert set(p.name for p in second.path.iterdir()) == REQUIRED_ARTIFACTS


def test_published_metrics_record_execution_rejected_orders(tmp_path):
    # 601318.SH stays ranked in the weekly top-10 (its signal-day closes are
    # untouched) but its Monday execution-day bar opens below the lower price
    # limit, so every weekly trim-sell of it is submitted as planned and then
    # rejected by the executor -- the pure-intent replay keeps the plan intact
    # and records the sell-at-lower-limit rejection in the ledgers.
    symbol = "601318.SH"
    project_root = tmp_path / "project"
    _publish_synthetic_dataset(project_root, limit_locked_symbols=(symbol,))
    runner = ResearchRunner(project_root, config_root=_REPO_ROOT)
    experiment = runner.run(_SPEC)
    assert experiment.manifest.status in ("ACCEPTED", "REJECTED")
    metrics = json.loads(
        (experiment.path / "metrics.json").read_text(encoding="utf-8")
    )
    scenarios = metrics["scenarios"]
    assert isinstance(scenarios, dict) and scenarios
    for summary in scenarios.values():
        # The pre-trade concepts are gone; the divergence is a rejection.
        assert "n_pretrade_adjustments" not in summary
        assert "pretrade_adjustments_by_reason" not in summary
        assert int(summary["n_rejections"]) > 0
        assert REASON_SELL_AT_LOWER_LIMIT in summary["rejections_by_reason"]
        assert REASON_SELL_AT_LOWER_LIMIT in summary["unfilled_reason_counts"]
        # Spec section 7 reconciliation invariants.
        planned_order_count = int(summary["planned_order_count"])
        assert planned_order_count > 0
        assert (
            int(summary["filled_order_count"])
            + int(summary["partial_order_count"])
            + int(summary["rejected_order_count"])
        ) == planned_order_count
        assert (
            int(summary["filled_quantity"]) + int(summary["unfilled_quantity"])
        ) == int(summary["planned_quantity"])
        assert int(summary["unfilled_quantity"]) == int(
            summary["rejected_quantity"]
        )
        assert int(summary["n_rejections"]) == (
            int(summary["partial_order_count"])
            + int(summary["rejected_order_count"])
        )
        assert sum(
            int(count) for count in summary["unfilled_reason_counts"].values()
        ) == (
            int(summary["partial_order_count"])
            + int(summary["rejected_order_count"])
        )
        assert summary["plan_diverged"] is (int(summary["unfilled_quantity"]) > 0)


def test_metrics_records_total_return_input_audit(env):
    """metrics.json carries a deterministic factor-input provenance audit.

    The audit must describe the pinned dataset version the run actually used:
    the adjustment basis, the requested factor versions, the adjusted-bar rows
    the universe consumed, and the ERROR break / invalid-reason counts (zero
    for the trusted synthetic fixture).
    """
    experiment = ResearchRunner(env.root, config_root=_REPO_ROOT).run(_SPEC)
    metrics = json.loads(
        (experiment.path / "metrics.json").read_text(encoding="utf-8")
    )
    audit = metrics["factor_input"]
    assert audit["adjustment"] == "internal_total_return_v1"
    assert audit["factor_versions"] == {"momentum_60d": "2.0.0"}
    assert audit["row_count"] > 0
    assert audit["error_break_count"] == 0
    assert audit["invalid_reason_counts"] == {}


def test_run_report_shows_factor_price_basis(env):
    """The direct ``research run`` report exposes the same adjustment basis and
    break count the richer rebuilt report renders from metrics.json."""
    experiment = ResearchRunner(env.root, config_root=_REPO_ROOT).run(_SPEC)
    html = (experiment.path / "report.html").read_text(encoding="utf-8")
    assert "因子价格口径" in html
    assert "调整方法 internal_total_return_v1" in html
    assert "因子版本 momentum_60d: 2.0.0" in html
    # Pinned values: every adjusted-bar row of the twelve universe symbols
    # over the full published session range, and zero trusted-evidence breaks.
    expected_rows = len(EQUITY_GROWTH) * len(_weekdays(_BARS_START, _BARS_END))
    assert f"输入行数 {expected_rows}" in html
    assert "不可信断点 0" in html


def test_research_rejects_untrusted_but_engineering_is_untrusted(env):
    _publish_dataset_with_untrusted_coverage(env.root)
    runner = ResearchRunner(env.root, config_root=_REPO_ROOT)
    with pytest.raises(ResearchRunFailed, match="corporate action trust"):
        runner.run(_SPEC)
    debug = runner.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)
    assert json.loads(
        (debug.path / "metrics.json").read_text(encoding="utf-8")
    )["evaluation"]["status"] == "UNTRUSTED"


def test_research_accepts_verified_empty_coverage(env):
    _publish_dataset_with_verified_empty_coverage(env.root)
    assert ResearchRunner(
        env.root, config_root=_REPO_ROOT
    ).run(_SPEC).manifest.status in ("ACCEPTED", "REJECTED")


def test_engineering_never_publishes_accepted_even_with_trusted_evidence(env):
    # Mode-level trust ruling: ENGINEERING is diagnostic-only, so even fully
    # trusted coverage never yields an ACCEPTED experiment (formal acceptance
    # is reserved for RESEARCH); the evaluation is stamped UNTRUSTED and the
    # manifest REJECTED with a mode reason naming the diagnostic-only rule.
    _publish_dataset_with_verified_empty_coverage(env.root)
    runner = ResearchRunner(env.root, config_root=_REPO_ROOT)
    debug = runner.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)
    metrics = json.loads(
        (debug.path / "metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["corporate_action_trust"]["trusted"] is True
    assert metrics["evaluation"]["status"] == "UNTRUSTED"
    assert "diagnostic-only" in metrics["evaluation"]["reason"]
    assert "never publishes an ACCEPTED" in metrics["evaluation"]["reason"]
    assert debug.manifest.status == "REJECTED"


def _publish_dataset_without_master_evidence(project_root: Path) -> str:
    """Republish CURRENT with an empty security_master_coverage table."""
    from stock_quant.data_model.security_master import master_coverage_frame

    return _publish_synthetic_dataset(
        project_root,
        master_coverage=master_coverage_frame([]),
    )


def test_research_rejects_missing_master_evidence_but_engineering_is_untrusted(
    env,
):
    """Empty master evidence freezes RESEARCH; ENGINEERING still diagnoses.

    The rejection must name the security-master evidence and happen before any
    backtest; the ENGINEERING run proceeds as an UNTRUSTED diagnostic that can
    never be accepted as a trusted performance claim.
    """
    _publish_dataset_without_master_evidence(env.root)
    runner = ResearchRunner(env.root, config_root=_REPO_ROOT)
    with pytest.raises(ResearchRunFailed, match="security master evidence"):
        runner.run(_SPEC)
    debug = runner.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)
    metrics = json.loads(
        (debug.path / "metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["evaluation"]["status"] == "UNTRUSTED"
    assert debug.manifest.status == "REJECTED"


def test_research_factor_input_uses_adjusted_bar(env):
    """The factor adapter's input comes from the pinned adjusted_bar table."""
    with DatasetReader(env.root).open(env.version) as context:
        adjusted = context.read("adjusted_bar")
        adapter = _DatasetFactorAdapter(
            context=context,
            universe_symbols=tuple(sorted(adjusted["symbol"].unique())),
        )
        factor_input = adapter.factor_input()
    assert set(factor_input["adjustment"]) == {"internal_total_return_v1"}
    expected = adjusted.sort_values(
        ["symbol", "trade_date"], kind="stable"
    ).reset_index(drop=True)
    assert factor_input["adjusted_close"].tolist() == (
        expected["adjusted_close"].tolist()
    )


def test_research_rejects_dataset_without_adjusted_bar(env):
    """A legacy dataset without adjusted_bar fails the run, never falls back."""
    _publish_legacy_dataset(env.root)
    runner = ResearchRunner(env.root, config_root=_REPO_ROOT)
    with pytest.raises(ResearchRunFailed, match="adjusted_bar"):
        runner.run(_SPEC)


def test_research_rejects_spec_requesting_retired_factor_version(env, tmp_path):
    """A spec pinning momentum_60d/1.0.0 is rejected, never silently upgraded.

    v2 consumes only the internal total-return basis, so a request for the
    retired v1 must fail the provider's version check instead of recomputing
    v1 values from unadjusted closes.
    """
    spec_text = (
        _REPO_ROOT / "configs" / "experiments" / "momentum_60d.yml"
    ).read_text(encoding="utf-8")
    assert "momentum_60d: 2.0.0" in spec_text
    spec_path = tmp_path / "momentum_60d_v1.yml"
    spec_path.write_text(
        spec_text.replace("momentum_60d: 2.0.0", "momentum_60d: 1.0.0"),
        encoding="utf-8",
    )
    runner = ResearchRunner(env.root, config_root=_REPO_ROOT)
    with pytest.raises(ResearchRunFailed, match="1.0.0"):
        runner.run(spec_path)


def test_new_stock_excluded_before_120_listed_days(tmp_path):
    """A symbol with a real list_date under 120 sessions before the window end
    never enters a factor candidate set (momentum seasoning)."""
    sessions = _weekdays(_BARS_START, _BARS_END)
    fresh_symbol = "603999.SH"
    fresh_list_date = sessions[-90]
    fresh_growth = 0.00200
    project_root = tmp_path / "project"
    _publish_synthetic_dataset(
        project_root,
        fresh=(fresh_symbol, fresh_list_date, fresh_growth),
    )
    runner = ResearchRunner(project_root, config_root=_REPO_ROOT)
    experiment = runner.run(_SPEC)
    assert experiment.manifest.status in ("ACCEPTED", "REJECTED")

    factors = pd.read_parquet(experiment.path / "factor_results.parquet")
    fresh_rows = factors.loc[factors["symbol"] == fresh_symbol]
    assert not fresh_rows.empty, "the fresh symbol must produce factor rows"
    assert not fresh_rows["is_valid"].astype(bool).any()
    assert "seasoning_below_120" in set(fresh_rows["invalid_reason"])

    targets = pd.read_parquet(experiment.path / "target_positions.parquet")
    assert fresh_symbol not in set(targets["symbol"]), (
        "a symbol with fewer than 120 listed trading days must be kept out of "
        "target positions"
    )


def _plain_date(value: object) -> date:
    """Normalize a parquet-round-tripped Timestamp/date back to a plain date."""
    return pd.Timestamp(value).date()


def test_total_return_factor_does_not_change_fill_or_valuation_prices(tmp_path):
    """Total-return factor input never leaks into execution or valuation.

    The market carries one verified, implemented cash dividend on the
    highest-growth holding (always in the weekly top-10), so its pre-ex-date
    ``adjusted_close`` sits roughly one percent above the unadjusted prices.
    Every fill must still reference the unadjusted open of its bar, and the
    persisted daily mark-to-market must equal a valuation recomputed from
    unadjusted ``daily_bar`` closes.
    """
    action_symbol = "601857.SH"
    sessions = _weekdays(_BARS_START, _BARS_END)
    ex_date = sessions[len(sessions) // 2]
    project_root = tmp_path / "project"
    version = _publish_synthetic_dataset(
        project_root,
        corporate_actions=_cash_action(action_symbol, ex_date),
        coverage=_coverage(
            _universe_symbols(),
            status=CoverageStatus.VERIFIED_EMPTY,
            event_symbols=(action_symbol,),
        ),
    )
    experiment = ResearchRunner(project_root, config_root=_REPO_ROOT).run(_SPEC)
    with DatasetReader(project_root).open(version) as context:
        daily = context.read("daily_bar")
        adjusted = context.read("adjusted_bar")
    equity = daily[daily["symbol"].isin(_universe_symbols())]
    day_symbol = [equity["trade_date"].map(_plain_date), "symbol"]

    # Sanity: the dividend really moves the adjusted series away from the raw
    # one (far beyond the cent-level tolerances below), so a wrongly adjusted
    # execution/valuation price could never pass this test silently.
    action_rows = adjusted[adjusted["symbol"] == action_symbol]
    divergence = (action_rows["adjusted_close"] - action_rows["raw_close"]).abs()
    assert float(divergence.max()) > 0.1

    fills = pd.read_parquet(experiment.path / "fills.parquet")
    assert not fills.empty
    assert action_symbol in set(fills["symbol"])
    opens = equity.set_index(day_symbol)["open"]
    for record in fills.itertuples():
        expected = float(opens.loc[(_plain_date(record.trade_date), record.symbol)])
        # The executor cent-quantizes the reference, so half a cent is the
        # largest possible distance from the raw unadjusted open.
        assert float(record.reference_price) == pytest.approx(expected, abs=0.005)

    # Valuation: the published daily equity is portfolio-level only, so
    # recompute its market value from unadjusted daily_bar closes over the
    # per-day quantities the fill ledger implies (a cash dividend changes no
    # share counts) and require a match within a cent.
    daily_equity = pd.read_parquet(experiment.path / "daily_equity.parquet")
    closes = equity.set_index(day_symbol)["close"]
    held_by_day: dict[date, dict[str, int]] = {}
    positions: dict[str, int] = {}
    for record in fills.sort_values("trade_date", kind="stable").itertuples():
        symbol = str(record.symbol)
        signed = int(record.quantity) * (1 if record.side == BUY else -1)
        positions[symbol] = positions.get(symbol, 0) + signed
        held_by_day[_plain_date(record.trade_date)] = dict(positions)
    fill_days = sorted(held_by_day)

    def _positions_at(day: date) -> dict[str, int]:
        previous = None
        for fill_day in fill_days:
            if fill_day > day:
                break
            previous = fill_day
        return held_by_day[previous] if previous is not None else {}

    checked = 0
    for row in daily_equity.itertuples():
        day = _plain_date(row.trade_date)
        held = _positions_at(day)
        if not held:
            continue  # no holdings yet; nothing to mark
        expected_value = sum(
            quantity * float(closes.loc[(day, symbol)])
            for symbol, quantity in held.items()
        )
        assert float(row.market_value) == pytest.approx(expected_value, abs=0.01)
        checked += 1
    assert checked > 0
