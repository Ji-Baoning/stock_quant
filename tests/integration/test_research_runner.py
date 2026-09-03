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

from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    DAILY_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.factors.momentum import Momentum60
from stock_quant.research.models import (
    REQUIRED_ARTIFACTS,
    Evaluation,
    ExperimentEvaluation,
    ResearchRunFailed,
)
from stock_quant.research.runner import ResearchRunner

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
) -> pd.DataFrame:
    """One deterministic daily_bar frame for all instruments over ``sessions``.

    ``limit_locked_symbols`` names equities whose bar on every Monday (the
    weekly execution day) opens at half the prior session's close, far below
    the main-board lower price limit.  Such names stay ranked by the factor
    (their signal-day closes are untouched) but every Monday *sell* of them is
    price-limit-blocked by the engine -- a rejection, not a hard error.
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


def _security_master() -> pd.DataFrame:
    symbols = [symbol for symbol, _ in EQUITY_GROWTH]
    n = len(symbols)
    list_dates = pd.to_datetime([_LIST_DATE] * n)
    delist = pd.Series(pd.NaT, index=range(n), dtype="datetime64[ns]")
    return pd.DataFrame(
        {
            "symbol": symbols,
            "name": [f"synth_{symbol}" for symbol in symbols],
            "exchange": "SH",
            "board": "sh_main",
            "list_date": list_dates,
            "delist_date": delist,
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


def _publish_synthetic_dataset(
    project_root: Path,
    *,
    index_close: tuple[float, float] = (4000.0, 2000.0),
    limit_locked_symbols: tuple[str, ...] = (),
) -> str:
    """Publish the synthetic market under ``project_root``; return its version."""
    tables = {
        "daily_bar": _bars(
            _weekdays(_BARS_START, _BARS_END),
            index_close=index_close,
            limit_locked_symbols=limit_locked_symbols,
        ),
        "security_master": _security_master(),
        "corporate_action": _corporate_action(),
        "trading_calendar": _trading_calendar(),
    }
    return DatasetPublisher(project_root).publish(
        tables, QualityReport()
    ).version


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


def test_published_metrics_record_unreachable_orders(tmp_path):
    # 601318.SH stays ranked in the weekly top-10 (its signal-day closes are
    # untouched) but its Monday execution-day bar opens below the lower price
    # limit, so every weekly trim-sell of it is price-limit-blocked -- a
    # scheduled sell the plan can never reach.  Rejections are not published
    # artifacts, so the engineering gate must see them in metrics.json.
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
        assert int(summary["n_rejections"]) > 0
        assert summary["rejections_by_reason"]
        assert summary["plan_diverged"] is True
