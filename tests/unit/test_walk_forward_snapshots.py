"""Frozen research snapshots and the snapshot-bound experiment identity.

``build_snapshot_bundle`` freezes the strategy, experiment and data-environment
snapshots from one frozen spec plus the pinned dataset manifest, universe
definition and config hashes; :class:`SnapshotBundle` recomputes each
snapshot's canonical SHA-256 and rejects a supplied hash that disagrees with
its content.  ``compute_experiment_id(spec, snapshots)`` hashes the frozen
spec together with every full snapshot payload and its three hashes under
identity scheme version 2, so research-relevant changes always re-key the
experiment while runtime metadata (paths, timestamps, host, pid, worker
count) can never enter the identity.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from stock_quant.research.spec import ExperimentSpec, compute_experiment_id
from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    WalkForwardPolicy,
)
from stock_quant.research.walk_forward.snapshots import (
    NOT_USED,
    DataEnvironmentSnapshot,
    ExperimentSnapshot,
    SnapshotBundle,
    StrategySnapshot,
    build_snapshot_bundle,
)


def spec_kwargs(**overrides):
    kwargs = dict(
        hypothesis="walk-forward OOS stability over annual folds",
        factor_versions={"momentum_60d": "2.0.0"},
        dataset_version="d" * 64,
        universe_version="u" * 64,
        data_acceptance_id="a" * 64,
        date_range={"start_date": date(2020, 1, 1), "end_date": date(2024, 12, 31)},
        train_validation_holdout_policy="not_applicable_engineering_mvp",
        preprocessing={"winsorization": "none", "standardization": "none"},
        portfolio_rule={"name": "top_n_equal_weight", "top_n": 10, "lot_size": 100},
        cost_scenarios=["zero_cost", "commission_tax", "full_cost"],
        random_seed=42,
        code_commit="cafe1234",
        parent_experiment_ids=[],
        agent_id=None,
    )
    kwargs.update(overrides)
    return kwargs


def make_spec(**overrides) -> ExperimentSpec:
    return ExperimentSpec.model_validate(spec_kwargs(**overrides))


def dataset_manifest() -> dict:
    return {
        "dataset_version": "d" * 64,
        "tables": {
            "daily_bar": {"sha256": "1" * 64, "row_count": 10},
            "adjusted_bar": {"sha256": "2" * 64, "row_count": 10},
            "trading_calendar": {"sha256": "3" * 64, "row_count": 10},
            "corporate_action": {"sha256": "4" * 64, "row_count": 0},
            "corporate_action_coverage": {"sha256": "5" * 64, "row_count": 1},
        },
    }


def config_hashes() -> dict:
    return {"costs.yml": "c" * 64, "trading_rules.yml": "e" * 64}


def make_bundle(spec: ExperimentSpec | None = None, **overrides) -> SnapshotBundle:
    return build_snapshot_bundle(
        spec=spec if spec is not None else make_spec(),
        dataset_manifest=overrides.get("dataset_manifest", dataset_manifest()),
        universe_definition=overrides.get("universe_definition", None),
        config_hashes=overrides.get("config_hashes", config_hashes()),
    )


@pytest.fixture
def frozen_spec() -> ExperimentSpec:
    return make_spec()


@pytest.fixture
def snapshots(frozen_spec) -> SnapshotBundle:
    return make_bundle(frozen_spec)


@pytest.fixture
def bundle_factory():
    """Build a bundle whose strategy snapshot carries a supplied parameter hash."""

    def factory(parameters_hash: str) -> SnapshotBundle:
        strategy = StrategySnapshot(
            strategy_version="cafe1234",
            factor_versions={"momentum_60d": "2.0.0"},
            portfolio_rule_version="top_n_equal_weight",
            rebalance_frequency="weekly",
            parameters_hash=parameters_hash,
        )
        experiment = ExperimentSnapshot(
            universe_version="u" * 64,
            corporate_action_version="ca" * 32,
            cost_scenarios=("zero_cost", "commission_tax", "full_cost"),
            walk_forward_policy=WalkForwardPolicy(),
            stability_policy=StabilityPolicy(),
            config_hashes=config_hashes(),
        )
        data = DataEnvironmentSnapshot(
            price_version="1" * 64,
            fundamental_version=NOT_USED,
            calendar_version="3" * 64,
        )
        return SnapshotBundle.of(
            strategy_snapshot=strategy,
            experiment_snapshot=experiment,
            data_environment_snapshot=data,
        )

    return factory


# ---------------------------------------------------------------------------
# Plan steps: identity boundary tests
# ---------------------------------------------------------------------------


def test_strategy_parameter_change_changes_only_strategy_hash(bundle_factory):
    left = bundle_factory(parameters_hash="a" * 64)
    right = bundle_factory(parameters_hash="b" * 64)
    assert left.strategy_hash != right.strategy_hash
    assert left.experiment_hash == right.experiment_hash
    assert left.data_environment_hash == right.data_environment_hash


def test_runtime_metadata_does_not_change_identity(frozen_spec, snapshots):
    first = compute_experiment_id(frozen_spec, snapshots)
    second = compute_experiment_id(frozen_spec, snapshots)
    assert first == second


# ---------------------------------------------------------------------------
# Bundle content and validation
# ---------------------------------------------------------------------------


def test_bundle_carries_snapshot_payloads_and_recomputed_hashes(snapshots):
    assert isinstance(snapshots.strategy_snapshot, StrategySnapshot)
    assert isinstance(snapshots.experiment_snapshot, ExperimentSnapshot)
    assert isinstance(
        snapshots.data_environment_snapshot, DataEnvironmentSnapshot
    )
    assert snapshots.strategy_hash == snapshots.strategy_snapshot.content_hash
    assert snapshots.experiment_hash == snapshots.experiment_snapshot.content_hash
    assert (
        snapshots.data_environment_hash
        == snapshots.data_environment_snapshot.content_hash
    )


def test_bundle_rejects_a_supplied_hash_that_differs_from_content(snapshots):
    payload = snapshots.model_dump(mode="json")
    payload["strategy_hash"] = "0" * 64
    with pytest.raises(ValidationError):
        SnapshotBundle.model_validate(payload)


def test_bundle_rejects_runtime_metadata_as_snapshot_content(snapshots):
    payload = snapshots.model_dump(mode="json")
    payload["worker_count"] = 4
    with pytest.raises(ValidationError, match="extra"):
        SnapshotBundle.model_validate(payload)


def test_snapshot_mappings_are_sorted_and_duplicate_scenarios_rejected():
    strategy = StrategySnapshot(
        strategy_version="s",
        factor_versions={"momentum_60d": "2.0.0", "alpha_5d": "1.0.0"},
        portfolio_rule_version="top_n_equal_weight",
        rebalance_frequency="weekly",
        parameters_hash="p" * 64,
    )
    assert list(strategy.factor_versions) == sorted(strategy.factor_versions)
    with pytest.raises(ValidationError, match="cost scenario"):
        ExperimentSnapshot(
            universe_version="u" * 64,
            corporate_action_version="ca" * 32,
            cost_scenarios=("full_cost", "full_cost"),
            walk_forward_policy=WalkForwardPolicy(),
            stability_policy=StabilityPolicy(),
            config_hashes=config_hashes(),
        )


def test_data_environment_requires_not_used_sentinel_for_fundamentals():
    with pytest.raises(ValidationError, match="NOT_USED"):
        DataEnvironmentSnapshot(
            price_version="1" * 64,
            fundamental_version="some_fundamental_table",
            calendar_version="3" * 64,
        )


def test_bundle_serialization_round_trip_is_stable(snapshots):
    rebuilt = SnapshotBundle.model_validate(snapshots.model_dump(mode="json"))
    assert rebuilt == snapshots


# ---------------------------------------------------------------------------
# Identity integration
# ---------------------------------------------------------------------------


def test_identity_changes_when_a_snapshot_payload_changes(frozen_spec, snapshots):
    changed_strategy = snapshots.strategy_snapshot.model_copy(
        update={"parameters_hash": "f" * 64}
    )
    changed = SnapshotBundle.of(
        strategy_snapshot=changed_strategy,
        experiment_snapshot=snapshots.experiment_snapshot,
        data_environment_snapshot=snapshots.data_environment_snapshot,
    )
    assert compute_experiment_id(
        frozen_spec, snapshots
    ) != compute_experiment_id(frozen_spec, changed)


def test_identity_is_stable_across_a_bundle_round_trip(frozen_spec, snapshots):
    rebuilt = SnapshotBundle.model_validate(snapshots.model_dump(mode="json"))
    assert compute_experiment_id(frozen_spec, rebuilt) == compute_experiment_id(
        frozen_spec, snapshots
    )


def test_identity_requires_a_snapshot_bundle(frozen_spec):
    with pytest.raises(TypeError):
        compute_experiment_id(frozen_spec, None)  # type: ignore[arg-type]


def test_identity_rejects_an_unfrozen_spec(snapshots):
    with pytest.raises(Exception, match="CURRENT"):
        compute_experiment_id(make_spec(dataset_version="CURRENT"), snapshots)


def test_bundle_binds_the_spec_cost_scenarios(frozen_spec, snapshots):
    assert snapshots.experiment_snapshot.cost_scenarios == tuple(
        frozen_spec.cost_scenarios
    )
    assert isinstance(
        snapshots.experiment_snapshot.walk_forward_policy, WalkForwardPolicy
    )
    assert isinstance(
        snapshots.experiment_snapshot.stability_policy, StabilityPolicy
    )
