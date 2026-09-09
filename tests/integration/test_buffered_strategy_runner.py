"""Buffered fold-runner integration over one offline synthetic dataset.

The fold runner materializes the common ``WeightTargetPeriod`` book (pure
construction, fold-local membership state) *before* any cost scenario
replays, then gives every scenario its own ``WeightTargetRebalancer`` so
decision state can never cross accounts.  The fold publishes the common
``portfolio_construction.parquet`` beside each scenario's
``rebalance_decisions.parquet``; absence or duplicate signal/symbol rows fail
the fold (FAILED) instead of silently falling back to equal weight.

All tests are offline and live under ``tmp_path_factory``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from test_walk_forward_runner import (  # noqa: F401 - shared offline fixture
    BENCHMARKS,
    SYMBOLS,
    _Project,
    _project_root,
    _resolver,
    _table_hashes,
)

from stock_quant.backtest.costs import CostModel
from stock_quant.config import CostRate
from stock_quant.data_model.trading_rules import TradingRuleBook
from stock_quant.factors.momentum import Momentum60
from stock_quant.portfolio.buffered_models import (
    PORTFOLIO_CONSTRUCTION_COLUMNS,
    REBALANCE_DECISION_COLUMNS,
)
from stock_quant.research.spec import ExperimentSpec
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


def _buffered_spec(scenarios: list[str]) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "hypothesis": "buffered risk-weighted fold runner fixture",
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
            "portfolio_rule": {"name": "buffered_risk_weighted"},
            "cost_scenarios": scenarios,
            "random_seed": 42,
            "code_commit": "fixture",
            "parent_experiment_ids": [],
            "agent_id": None,
        }
    )


def build_buffered_request(
    project: _Project,
    tmp_path: Path,
    *,
    start: date,
    end: date,
    scenarios: list[str] | None = None,
) -> WalkForwardRequest:
    scenarios = scenarios if scenarios is not None else ["full_cost"]
    spec = _buffered_spec(scenarios)
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
            _REPO_ROOT / "configs" / "trading_rules.yml"
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
        portfolio_builder=None,  # the buffered rule builds its own targets
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
            for scenario in scenarios
        },
        universe_resolver=project.resolver,
        acceptance_audit={
            "acceptance_id": "a" * 64,
            "decision": "ACCEPTED",
            "dataset_version": project.version,
            "policy_version": "real-data-v1",
        },
    )


@pytest.fixture(scope="module")
def buffered_project(tmp_path_factory) -> _Project:
    root, version = _project_root(tmp_path_factory.mktemp("buffered"), ())
    return _Project(root, version)


@pytest.fixture
def buffered_runner():
    return WalkForwardRunner()


@pytest.fixture
def two_fold_request(buffered_project, tmp_path):
    return build_buffered_request(
        buffered_project,
        tmp_path,
        start=date(2020, 1, 1),
        end=date(2021, 12, 31),
    )


@pytest.fixture
def three_scenario_request(buffered_project, tmp_path):
    return build_buffered_request(
        buffered_project,
        tmp_path,
        start=date(2021, 1, 1),
        end=date(2021, 12, 31),
        scenarios=["zero_cost", "commission_tax", "full_cost"],
    )


# ---------------------------------------------------------------------------
# Plan Step 1: end-to-end construction semantics
# ---------------------------------------------------------------------------


def test_fold_first_signal_resets_buffer_state(buffered_runner, two_fold_request):
    result = buffered_runner.run(two_fold_request)
    assert len(result.executed_folds) == 2
    first_rows = [fold.construction.iloc[0] for fold in result.executed_folds]
    assert all(bool(row.previous_target_member) is False for row in first_rows)
    # fold-local state: the second fold's first signal saw no inherited
    # members even though the first fold ended with a full target book
    second_first_signal = result.executed_folds[1].construction[
        result.executed_folds[1].construction["signal_date"]
        == result.executed_folds[1].construction["signal_date"].iloc[0]
    ]
    assert (~second_first_signal["previous_target_member"]).all()


def test_all_scenarios_share_members_and_weights(
    buffered_runner, three_scenario_request
):
    result = buffered_runner.run(three_scenario_request)
    names = [scenario.name for scenario in result.scenarios]
    assert names == ["zero_cost", "commission_tax", "full_cost"]
    common = result.portfolio_construction[["signal_date", "symbol",
                                            "target_weight"]]
    for scenario in result.scenarios:
        assert set(scenario.rebalance_decisions.symbol) <= set(common.symbol)
    # the common construction frame is scenario-free: exactly one row set
    assert not common.empty
    assert not common.duplicated(subset=["signal_date", "symbol"]).any()


def test_runner_persists_complete_construction_audit(
    buffered_runner, two_fold_request
):
    buffered_runner.run(two_fold_request)
    fold_id = two_fold_request.schedule.folds[0].fold_id
    fold_dir = Path(two_fold_request.run_dir) / "folds" / fold_id
    construction = pd.read_parquet(fold_dir / "portfolio_construction.parquet")
    assert {
        "raw_momentum_rank",
        "risk_eligible_rank",
        "real_close_observations",
        "member_status",
        "raw_weight",
        "target_weight",
        "cash_weight",
        "portfolio_rule_version",
    } <= set(construction.columns)
    assert list(construction.columns) == list(PORTFOLIO_CONSTRUCTION_COLUMNS)
    for scenario in ("full_cost",):
        decisions = pd.read_parquet(
            fold_dir / "backtest" / scenario / "rebalance_decisions.parquet"
        )
        assert list(decisions.columns) == list(REBALANCE_DECISION_COLUMNS)
    # the fold manifest carries the common rule version (canonical policy
    # JSON hash) so the published artifact identifies its policy
    import json

    manifest = json.loads(
        (fold_dir / "fold_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["portfolio_rule"]["name"] == "buffered_risk_weighted"
    assert manifest["portfolio_rule"]["portfolio_rule_version"]
    assert (
        manifest["portfolio_rule"]["portfolio_rule_version"]
        == construction["portfolio_rule_version"].iloc[0]
    )


def test_target_outsider_or_bad_observations_fail_the_fold(
    buffered_project, tmp_path
):
    """Duplicate risk-observation rows make the fold FAILED, never a silent
    equal-weight fallback."""
    request = build_buffered_request(
        buffered_project,
        tmp_path,
        start=date(2021, 1, 1),
        end=date(2021, 12, 31),
    )

    original_provider = request.factor_input_provider
    base_frame = original_provider()

    def duplicated_provider() -> pd.DataFrame:
        return pd.concat([base_frame, base_frame.iloc[[0]]],
                         ignore_index=True)

    tampered = WalkForwardRequest(
        **{
            **request.__dict__,
            "factor_input_provider": duplicated_provider,
            "run_dir": tmp_path / "run_tampered",
        }
    )
    with pytest.raises(WalkForwardRunFailed) as caught:
        WalkForwardRunner().run(tampered)
    assert caught.value.stability_conclusion is None
    assert caught.value.outcomes[0].status == "failed_preflight"
    assert caught.value.outcomes[0].reason_code == "EXECUTION_INTEGRITY"


def test_buffered_run_publishes_common_and_scenario_audit_sets(
    buffered_runner, three_scenario_request
):
    result = buffered_runner.run(three_scenario_request)
    fold_id = three_scenario_request.schedule.folds[0].fold_id
    run_dir = Path(three_scenario_request.run_dir)
    for scenario in ("zero_cost", "commission_tax", "full_cost"):
        scenario_dir = run_dir / "backtest" / fold_id / scenario
        for name in (
            "submitted_orders.parquet",
            "fills.parquet",
            "rejections.parquet",
            "rebalance_decisions.parquet",
        ):
            assert (scenario_dir / name).is_file(), name
    # every scenario's decisions stay inside the common member universe
    common_symbols = set(
        result.portfolio_construction[
            result.portfolio_construction["target_weight"] > 0
        ]["symbol"]
    )
    for scenario in result.scenarios:
        ordered = scenario.rebalance_decisions[
            scenario.rebalance_decisions["order_quantity"] > 0
        ]
        assert set(ordered["symbol"]) <= common_symbols
