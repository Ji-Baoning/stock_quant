"""Isolated fold execution over one offline synthetic dataset.

Each fold constructs a fresh account with the identical fixed initial cash,
reads warmup factor inputs but produces no warmup orders or returns, and
writes its artifacts beside the immutable ``fold_schedule.json`` and the
separate ``fold_outcomes.json`` ledger.  A fold/system preflight failure
leaves the schedule untouched, writes the outcome ledger plus a FAILED
manifest with a null conclusion and raises ``WalkForwardRunFailed``; an
ordinary order rejection (a limit-up buy that self-heals next week) is a
statistical strategy result -- the fold stays ``executed`` with a positive
reject rate and the run never becomes a system failure.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.backtest.costs import CostModel
from stock_quant.config import CostRate
from stock_quant.data_model.adjusted_bar import build_adjusted_bars
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.corporate_action_coverage import (
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_model.dataset import DatasetPublisher
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
from stock_quant.data_model.trading_rules import TradingRuleBook
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    MembershipReason,
    MembershipStatus,
    membership_content_hash,
    resolve_memberships,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.factors.momentum import Momentum60
from stock_quant.portfolio.equal_weight import TopNEqualWeight
from stock_quant.research.runner import _DatasetFactorAdapter
from stock_quant.research.spec import ExperimentSpec
from stock_quant.research.universe import UniverseDefinition, UniverseResolver
from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    WalkForwardPolicy,
)
from stock_quant.research.walk_forward.runner import (
    WalkForwardRequest,
    WalkForwardRunFailed,
    WalkForwardRunner,
)
from stock_quant.research.walk_forward.schedule import materialize_schedule
from stock_quant.research.walk_forward.snapshots import build_snapshot_bundle

_REPO_ROOT = Path(__file__).resolve().parents[2]

SYMBOLS = ("600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH")
BENCHMARKS = ("000300.SH", "000905.SH")
LIST_DATE = date(2015, 1, 5)
UNIVERSE_ID = "custom_wf_test"
BASE_PRICE = 10.0
_GROWTH = 0.0002
_INGESTED = pd.Timestamp("2022-02-01T00:00:00Z")

JUMP_MONDAY = date(2021, 1, 11)  # first fold-2021 execution day: limit-up buy


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5 and (current.month, current.day) != (1, 1):
            days.append(current)
        current += timedelta(days=1)
    return days


def _sessions() -> list[date]:
    return _weekdays(date(2016, 1, 1), date(2022, 1, 10))


def _closes(
    sessions: list[date], jump_dates: tuple[date, ...]
) -> dict[str, list[float]]:
    closes: dict[str, list[float]] = {}
    n = len(sessions)
    for index, symbol in enumerate(SYMBOLS, start=1):
        level = BASE_PRICE * (1.0 + 0.1 * index)
        series = [
            level * (1.0 + _GROWTH * index) ** (position - (n - 1))
            for position in range(n)
        ]
        if symbol == SYMBOLS[-1]:
            for jump in jump_dates:
                if jump in sessions:
                    position = sessions.index(jump)
                    for later in range(position, n):
                        series[later] *= 1.15
        closes[symbol] = series
    return closes


def _daily_bar(sessions: list[date], jump_dates: tuple[date, ...]) -> pd.DataFrame:
    closes = _closes(sessions, jump_dates)
    frames: list[pd.DataFrame] = []
    for symbol, series in closes.items():
        frames.append(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(sessions),
                    "symbol": symbol,
                    "open": series,
                    "high": series,
                    "low": series,
                    "close": series,
                    "volume": [1000] * len(sessions),
                    "amount": [1000.0 * value for value in series],
                    "adjustment": "unadjusted",
                    "source": "tushare",
                    "ingested_at": [_INGESTED] * len(sessions),
                }
            )
        )
    for symbol, level in zip(BENCHMARKS, (4000.0, 2000.0)):
        frames.append(
            pd.DataFrame(
                {
                    "trade_date": pd.to_datetime(sessions),
                    "symbol": symbol,
                    "open": [level] * len(sessions),
                    "high": [level] * len(sessions),
                    "low": [level] * len(sessions),
                    "close": [level] * len(sessions),
                    "volume": [1000] * len(sessions),
                    "amount": [1000.0 * level] * len(sessions),
                    "adjustment": "unadjusted",
                    "source": "akshare",
                    "ingested_at": [_INGESTED] * len(sessions),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def _project_root(tmp_path: Path, jump_dates: tuple[date, ...]) -> tuple[Path, str]:
    root = tmp_path / "project"
    sessions = _sessions()
    daily = _daily_bar(sessions, jump_dates)
    empty_actions = pd.DataFrame(columns=CORPORATE_ACTION_COLUMNS)
    empty_quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
    coverage = coverage_frame(
        [
            coverage_record(
                symbol,
                sessions[0],
                sessions[-1],
                CoverageStatus.VERIFIED_EMPTY,
                None,
                sources=[{"endpoint": "fixture", "outcome": "success_empty"}],
                checked_at=_INGESTED,
            )  # one trusted row per symbol over the whole bars window
            for symbol in SYMBOLS
        ]
    )
    master = pd.DataFrame(
        {
            "symbol": list(SYMBOLS),
            "name": [f"fixture {symbol}" for symbol in SYMBOLS],
            "exchange": ["SSE"] * len(SYMBOLS),
            "board": ["main"] * len(SYMBOLS),
            "list_date": pd.to_datetime([LIST_DATE] * len(SYMBOLS)),
            "delist_date": pd.Series(
                pd.NaT, index=range(len(SYMBOLS)), dtype="datetime64[ns]"
            ),
            "list_status": [ListStatus.L.value] * len(SYMBOLS),
        }
    )[SECURITY_MASTER_COLUMNS]
    master_coverage = master_coverage_frame(
        [
            master_coverage_record(
                symbol,
                list_date=LIST_DATE,
                list_status=ListStatus.L,
                source=MASTER_SOURCE_STOCK_BASIC,
                snapshot_sha256="f" * 64,
                sdk_version="fixture",
                checked_at=_INGESTED,
            )
            for symbol in SYMBOLS
        ]
    )
    calendar_days = _weekdays(date(2015, 1, 1), date(2022, 1, 10))
    calendar = pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(calendar_days),
            "is_trading_day": [True] * len(calendar_days),
        }
    )[TRADING_CALENDAR_COLUMNS]
    version = DatasetPublisher(root).publish(
        {
            "daily_bar": daily,
            "adjusted_bar": build_adjusted_bars(
                daily, empty_actions, empty_quarantine, coverage, symbols=SYMBOLS
            ),
            "security_master": master,
            "security_master_coverage": master_coverage,
            "corporate_action": empty_actions,
            "corporate_action_quarantine": empty_quarantine,
            "corporate_action_coverage": coverage,
            "trading_calendar": calendar,
        },
        QualityReport(),
    ).version
    return root, version


def _resolver() -> UniverseResolver:
    facts = [
        MembershipFact(
            universe_id=UNIVERSE_ID,
            symbol=symbol,
            raw_effective_from=date(2015, 1, 1),
            raw_effective_to=None,
            announcement_date=date(2015, 1, 1),
            status=MembershipStatus.ACTIVE,
            reason=MembershipReason.INITIAL_CONSTITUENT,
            source="fixture",
            source_url="https://fixture.invalid/membership",
            snapshot_sha256="a" * 64,
            source_document_sha256="b" * 64,
        )
        for symbol in SYMBOLS
    ]
    definition = UniverseDefinition(
        universe_id=UNIVERSE_ID,
        rules_version="fixture-rules-v1",
        membership_table_sha256=membership_content_hash(facts),
        coverage_start=date(2015, 1, 1),
        coverage_end=date(2022, 1, 10),
        evidence_summary_sha256="e" * 64,
    )
    return UniverseResolver(definition, resolve_memberships(facts), facts=facts)


def _spec(cost_scenario: str = "full_cost") -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "hypothesis": "walk-forward fold runner fixture",
            "factor_versions": {"momentum_60d": "2.0.0"},
            "dataset_version": "d" * 64,
            "universe_version": "u" * 64,
            "data_acceptance_id": "a" * 64,
            "date_range": {
                "start_date": date(2020, 1, 1),
                "end_date": date(2021, 12, 31),
            },
            "train_validation_holdout_policy": "not_applicable_engineering_mvp",
            "preprocessing": {"winsorization": "none", "standardization": "none"},
            "portfolio_rule": {
                "name": "top_n_equal_weight",
                "top_n": 5,
                "lot_size": 100,
            },
            "cost_scenarios": [cost_scenario],
            "random_seed": 42,
            "code_commit": "fixture",
            "parent_experiment_ids": [],
            "agent_id": None,
        }
    )


class _Project:
    """One synthetic dataset plus every factory the fold runner consumes."""

    def __init__(self, root: Path, version: str) -> None:
        from stock_quant.data_model.dataset import DatasetReader

        self.root = root
        self.version = version
        self.context = DatasetReader(root).open(version)
        frame = self.context.read("trading_calendar")
        open_days = tuple(
            day
            for day, flag in zip(
                (pd.Timestamp(value).date() for value in frame["calendar_date"]),
                frame["is_trading_day"],
            )
            if bool(flag)
        )
        self.calendar = TradingCalendar.from_open_days(open_days)
        self.resolver = _resolver()

    def factor_input(self) -> pd.DataFrame:
        return _DatasetFactorAdapter(
            context=self.context, universe_symbols=SYMBOLS
        ).factor_input()


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> _Project:
    root, version = _project_root(tmp_path_factory.mktemp("wf"), ())
    return _Project(root, version)


def build_request(
    project: _Project,
    tmp_path: Path,
    *,
    start: date,
    end: date,
    acceptance: dict | None = None,
    scenario: str = "full_cost",
) -> WalkForwardRequest:
    spec = _spec(cost_scenario=scenario)
    schedule = materialize_schedule(
        requested_start=start,
        requested_end=end,
        calendar=project.calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    bundle = build_snapshot_bundle(
        spec=spec,
        dataset_manifest={"tables": _table_hashes(project)},
        universe_definition=project.resolver.definition,
        config_hashes={"costs.yml": "c" * 64},
    )
    bars = project.context.read("daily_bar")
    bars = bars[bars["symbol"].isin(set(SYMBOLS))].copy()
    bars["trade_date"] = bars["trade_date"].map(pd.Timestamp)
    benchmark = project.context.read("daily_bar")
    benchmark = benchmark[benchmark["symbol"].isin(set(BENCHMARKS))].copy()
    benchmark["trade_date"] = benchmark["trade_date"].map(pd.Timestamp)
    return WalkForwardRequest(
        run_dir=tmp_path / "run",
        spec=spec,
        schedule=schedule,
        snapshot_bundle=bundle,
        walk_forward_policy=WalkForwardPolicy(),
        stability_policy=StabilityPolicy(),
        calendar=project.calendar,
        rule_book=TradingRuleBook.from_yaml(
            _REPO_ROOT / "templates" / "project-config" / "trading_rules.yml"
        ),
        initial_cash=1_000_000.0,
        dataset_version=project.version,
        universe_symbols=SYMBOLS,
        bars=bars,
        benchmarks=benchmark,
        benchmark_symbols=BENCHMARKS,
        corporate_actions=project.context.read("corporate_action"),
        factors={"momentum_60d": Momentum60()},
        factor_input_provider=project.factor_input,
        portfolio_builder=TopNEqualWeight(top_n=5, lot_size=100),
        cost_models={
            scenario: CostModel(
                CostRate(
                    effective_from=date(2020, 1, 1),
                    commission_rate=0.0003,
                    minimum_commission=5.0,
                    stamp_tax_sell_rate=0.0005,
                    slippage_rate=0.001,
                )
            )
        },
        universe_resolver=project.resolver,
        acceptance_audit=(
            acceptance
            if acceptance is not None
            else {
                "acceptance_id": "a" * 64,
                "decision": "ACCEPTED",
                "dataset_version": project.version,
                "policy_version": "real-data-v1",
            }
        ),
    )


def _table_hashes(project: _Project) -> dict[str, dict[str, str]]:
    manifest_path = (
        project.root
        / "data"
        / "standardized"
        / project.version
        / "dataset_manifest.json"
    )
    import json

    return json.loads(manifest_path.read_text(encoding="utf-8"))["tables"]


@pytest.fixture
def wf_request(project, tmp_path):
    return build_request(
        project, tmp_path, start=date(2020, 1, 1), end=date(2021, 12, 31)
    )


@pytest.fixture
def rejected_order_request(project, tmp_path):
    root, version = _project_root(tmp_path / "jump", (JUMP_MONDAY,))
    jumped = _Project(root, version)
    return build_request(
        jumped, tmp_path, start=date(2021, 1, 1), end=date(2021, 12, 31)
    )


@pytest.fixture
def bad_request(project, tmp_path):
    """Fold 2016 has almost no confirmed warmup sessions: preflight fails."""
    return build_request(
        project, tmp_path, start=date(2016, 1, 1), end=date(2016, 12, 31)
    )


@pytest.fixture
def walk_forward_runner():
    return WalkForwardRunner()


# ---------------------------------------------------------------------------
# Plan steps: isolation and warmup suppression
# ---------------------------------------------------------------------------


def test_each_fold_constructs_a_fresh_account(walk_forward_runner, wf_request):
    result = walk_forward_runner.run(wf_request)
    assert [fold.initial_cash for fold in result.executed_folds] == [1_000_000.0] * 2
    assert result.executed_folds[1].opening_positions == {}


def test_warmup_produces_no_orders_or_returns(walk_forward_runner, wf_request):
    result = walk_forward_runner.run(wf_request)
    for fold in result.executed_folds:
        assert fold.orders.trade_date.min() >= fold.first_trading_day
        assert fold.daily_returns.trade_date.min() >= fold.first_trading_day


# ---------------------------------------------------------------------------
# Plan steps: audit/failure behavior
# ---------------------------------------------------------------------------


def test_failed_preflight_keeps_schedule_and_writes_outcome(
    walk_forward_runner, bad_request
):
    with pytest.raises(WalkForwardRunFailed) as caught:
        walk_forward_runner.run(bad_request)
    assert caught.value.schedule_path.is_file()
    assert caught.value.outcomes[0].status == "failed_preflight"
    assert caught.value.stability_conclusion is None


def test_ordinary_order_rejection_is_not_system_failure(
    walk_forward_runner, rejected_order_request
):
    result = walk_forward_runner.run(rejected_order_request)
    assert result.outcomes[0].status == "executed"
    assert result.scenario_metrics[0].reject_rate > 0


# ---------------------------------------------------------------------------
# Artifact contract and audit chain
# ---------------------------------------------------------------------------


def test_run_writes_immutable_schedule_separate_outcomes_and_manifest(
    walk_forward_runner, wf_request
):
    result = walk_forward_runner.run(wf_request)
    schedule_bytes = wf_request.run_dir / "fold_schedule.json"
    assert schedule_bytes.is_file()
    assert (wf_request.run_dir / "fold_outcomes.json").is_file()
    assert (wf_request.run_dir / "walk_forward_manifest.json").is_file()
    for fold in wf_request.schedule.folds:
        fold_dir = wf_request.run_dir / "folds" / fold.fold_id
        for name in (
            "fold_manifest.json",
            "signals.parquet",
            "orders.parquet",
            "fills.parquet",
            "equity.parquet",
            "daily_returns.parquet",
            "metrics.json",
        ):
            assert (fold_dir / name).is_file(), name
    assert result.schedule_sha256
    assert result.outcomes_sha256 != result.schedule_sha256
    # the schedule is never modified by fold execution
    import hashlib

    assert (
        hashlib.sha256(schedule_bytes.read_bytes()).hexdigest()
        == result.schedule_sha256
    )


def test_equity_artifact_carries_canonical_columns(walk_forward_runner, wf_request):
    result = walk_forward_runner.run(wf_request)
    equity = result.executed_folds[0].equity
    for column in ("trade_date", "initial_equity", "net_equity_after_cost"):
        assert column in equity.columns
    assert (equity["initial_equity"] == 1_000_000.0).all()
    expected_days = [
        day
        for day in wf_request.calendar.open_days
        if result.executed_folds[0].first_trading_day
        <= day
        <= result.executed_folds[0].last_trading_day
    ]
    assert [day for day in equity["trade_date"]] == expected_days


def test_completed_run_evaluates_stability_and_declares_few_fold_evidence(
    walk_forward_runner, wf_request
):
    result = walk_forward_runner.run(wf_request)
    assert result.evaluation.research_status == "COMPLETED"
    # two executed folds: a valid process with insufficient evidence
    assert result.evaluation.stability_conclusion == "INCONCLUSIVE"
    assert result.evaluation.stability_policy_hash
    assert result.manifest["stability_conclusion"] == "INCONCLUSIVE"


def test_missing_acceptance_audit_fails_the_run(walk_forward_runner, project, tmp_path):
    bad = build_request(
        project,
        tmp_path,
        start=date(2020, 1, 1),
        end=date(2020, 12, 31),
        acceptance={
            "acceptance_id": "a" * 64,
            "decision": "REJECTED",
            "dataset_version": project.version,
        },
    )
    with pytest.raises(WalkForwardRunFailed) as caught:
        walk_forward_runner.run(bad)
    assert caught.value.outcomes[0].status == "failed_preflight"
    assert caught.value.outcomes[0].reason_code == "ACCEPTANCE_MISSING"


def test_resume_reuses_matching_fold_stages(walk_forward_runner, wf_request):
    first = walk_forward_runner.run(wf_request)
    schedule_before = (wf_request.run_dir / "fold_schedule.json").read_bytes()
    second = walk_forward_runner.run(wf_request)
    assert (wf_request.run_dir / "fold_schedule.json").read_bytes() == schedule_before
    assert second.schedule_sha256 == first.schedule_sha256
    assert second.outcomes_sha256 == first.outcomes_sha256
    assert [
        metrics.fold_calendar_return for metrics in second.scenario_metrics
    ] == [metrics.fold_calendar_return for metrics in first.scenario_metrics]
