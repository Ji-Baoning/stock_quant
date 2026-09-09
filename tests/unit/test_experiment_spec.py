"""Strict experiment-spec parsing, canonical identity and CURRENT resolution.

``ExperimentSpec`` is a strict (extra-forbidden) Pydantic spec that never lets
an execution-affecting input go un-typed.  Its deterministic identity
``compute_experiment_id`` hashes a frozen (explicit-version) spec with
RFC-8785-style canonical JSON semantics: sorted UTF-8 keys, compact
separators, dates already normalized to ISO strings by ``model_dump(mode=
"json")`` and ``allow_nan=False`` so no non-finite float can ever be hashed.
A requested ``CURRENT`` dataset/universe version must be resolved to an
explicit version before an identity exists (``ExperimentSpec.freeze``), so the
stored/frozen spec never references the token.

All tests are offline and in-memory except two that load the committed example
spec file ``configs/experiments/momentum_60d.yml``.
"""

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from stock_quant.research.spec import (
    ExperimentNotFrozenError,
    ExperimentSpec,
    compute_experiment_id,
    load_experiment_spec,
)
from stock_quant.research.trust import DataTrustMode

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_SPEC = _REPO_ROOT / "configs" / "experiments" / "momentum_60d.yml"


def spec_kwargs(**overrides):
    kwargs = dict(
        hypothesis=(
            "60-day adjusted-return momentum predicts short-horizon "
            "continuation; phase-one run is an engineering reproducibility "
            "check, not a claim of statistical validity."
        ),
        factor_versions={"momentum_60d": "1.0.0"},
        dataset_version="d" * 64,
        universe_version="u" * 64,
        # An explicit acceptance id keeps the default helper spec frozen; the
        # CURRENT_ACCEPTED placeholder tests override it explicitly.
        data_acceptance_id="a" * 64,
        date_range={"start_date": date(2020, 1, 1), "end_date": date(2026, 9, 2)},
        train_validation_holdout_policy="not_applicable_engineering_mvp",
        preprocessing={"winsorization": "none", "standardization": "none"},
        portfolio_rule={"name": "top_n_equal_weight", "top_n": 10, "lot_size": 100},
        cost_scenarios=["zero_cost", "commission_tax", "full_cost"],
        random_seed=42,
        code_commit="a1b2c3d4",
        parent_experiment_ids=[],
        agent_id=None,
    )
    kwargs.update(overrides)
    return kwargs


def make_spec(**overrides) -> ExperimentSpec:
    return ExperimentSpec.model_validate(spec_kwargs(**overrides))


@pytest.fixture
def spec() -> ExperimentSpec:
    return make_spec()


def test_experiment_id_is_deterministic_and_sensitive_to_result_inputs(spec):
    assert compute_experiment_id(spec) == compute_experiment_id(
        spec.model_copy(deep=True)
    )
    changed = spec.model_copy(update={"random_seed": spec.random_seed + 1})
    assert compute_experiment_id(spec) != compute_experiment_id(changed)


def test_experiment_id_survives_a_serialization_round_trip(spec):
    rebuilt = ExperimentSpec.model_validate_json(spec.model_dump_json())
    assert compute_experiment_id(rebuilt) == compute_experiment_id(spec)


@pytest.mark.parametrize(
    "change",
    [
        {"dataset_version": "e" * 64},
        {"universe_version": "v" * 64},
        {"data_acceptance_id": "c" * 64},
        {"code_commit": "another-commit"},
        {"factor_versions": {"momentum_60d": "2.0.0"}},
        {"random_seed": 7},
        {"cost_scenarios": ["zero_cost"]},
        {
            "date_range": {
                "start_date": date(2020, 1, 1),
                "end_date": date(2025, 12, 31),
            }
        },
        {"portfolio_rule": {"name": "top_n_equal_weight", "top_n": 5, "lot_size": 100}},
        {"parent_experiment_ids": ["experiment-0001"]},
        {"agent_id": "research-agent-1"},
    ],
)
def test_experiment_id_changes_when_an_input_changes(change):
    base = make_spec()
    changed = make_spec(**change)
    assert compute_experiment_id(base) != compute_experiment_id(changed)


def test_freeze_resolves_current_placeholders_to_explicit_versions():
    placeholder = make_spec(dataset_version="CURRENT", universe_version="CURRENT")
    assert not placeholder.is_frozen
    frozen = placeholder.freeze(
        dataset_version="resolved-dataset", universe_version="resolved-universe"
    )
    assert frozen.dataset_version == "resolved-dataset"
    assert frozen.universe_version == "resolved-universe"
    assert frozen.is_frozen
    identical = make_spec(
        dataset_version="resolved-dataset", universe_version="resolved-universe"
    )
    assert compute_experiment_id(frozen) == compute_experiment_id(identical)


def test_freeze_keeps_already_explicit_versions(spec):
    frozen = spec.freeze()
    assert frozen.dataset_version == spec.dataset_version
    assert compute_experiment_id(frozen) == compute_experiment_id(spec)


def test_freeze_can_stamp_the_code_commit(spec):
    stamped = spec.freeze(code_commit="repo-head-sha")
    assert stamped.code_commit == "repo-head-sha"
    assert compute_experiment_id(stamped) != compute_experiment_id(spec)


def test_freeze_requires_an_explicit_version_for_each_current_field():
    dataset_only = make_spec(dataset_version="CURRENT")
    with pytest.raises(ValueError):
        dataset_only.freeze()
    with pytest.raises(ValueError):
        dataset_only.freeze(universe_version="resolved-universe")
    both = make_spec(dataset_version="CURRENT", universe_version="CURRENT")
    with pytest.raises(ValueError):
        both.freeze(dataset_version="resolved-dataset")
    frozen = both.freeze(
        dataset_version="resolved-dataset", universe_version="resolved-universe"
    )
    assert frozen.is_frozen


def test_compute_experiment_id_rejects_unresolved_current_spec():
    placeholder = make_spec(dataset_version="CURRENT")
    with pytest.raises(ExperimentNotFrozenError):
        compute_experiment_id(placeholder)


def test_research_spec_is_not_frozen_with_current_accepted():
    spec = make_spec(data_acceptance_id="CURRENT_ACCEPTED")
    assert not spec.is_frozen
    frozen = spec.freeze(data_acceptance_id="a" * 64)
    assert frozen.data_acceptance_id == "a" * 64
    assert frozen.is_frozen


def test_acceptance_id_changes_experiment_identity():
    first = make_spec(data_acceptance_id="a" * 64)
    second = make_spec(data_acceptance_id="b" * 64)
    assert compute_experiment_id(first) != compute_experiment_id(second)


def test_engineering_spec_may_freeze_without_acceptance():
    spec = make_spec(data_acceptance_id=None, trust_mode=DataTrustMode.ENGINEERING)
    assert spec.is_frozen


def test_freeze_rejects_research_spec_without_acceptance():
    with pytest.raises(
        ValueError,
        match="research specs require an explicit data_acceptance_id",
    ):
        make_spec().freeze(data_acceptance_id=None)


def test_spec_forbids_extra_fields():
    with pytest.raises(ValidationError):
        make_spec(unexpected_key="not part of the spec")


def test_load_experiment_spec_rejects_unknown_top_level_keys(tmp_path):
    data = spec_kwargs()
    data["sneaky_extra"] = True
    path = tmp_path / "sneaky.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_experiment_spec(path)


def test_spec_rejects_non_mvp_train_validation_holdout_policy():
    with pytest.raises(ValidationError):
        make_spec(train_validation_holdout_policy="kfold_time_split")


def test_spec_rejects_date_range_end_before_start():
    with pytest.raises(ValidationError):
        make_spec(
            date_range={
                "start_date": date(2020, 1, 1),
                "end_date": date(2019, 12, 31),
            }
        )


@pytest.mark.parametrize(
    "factor_versions",
    [
        {},
        {"": "1.0.0"},
        {"momentum_60d": ""},
        {"momentum_60d": "1.0.0", "": "1.0.0"},
    ],
)
def test_spec_rejects_empty_or_blank_factor_entries(factor_versions):
    with pytest.raises(ValidationError):
        make_spec(factor_versions=factor_versions)


def test_spec_rejects_blank_hypothesis_or_cost_scenarios():
    with pytest.raises(ValidationError):
        make_spec(hypothesis="   ")
    with pytest.raises(ValidationError):
        make_spec(cost_scenarios=[])


def test_committed_example_spec_is_coherent_and_loadable():
    loaded = load_experiment_spec(_EXAMPLE_SPEC)
    assert loaded.factor_versions == {"momentum_60d": "2.0.0"}
    assert loaded.date_range.start_date == date(2020, 1, 1)
    assert loaded.date_range.start_date <= loaded.date_range.end_date
    assert (
        loaded.train_validation_holdout_policy == "not_applicable_engineering_mvp"
    )
    assert loaded.dataset_version == "CURRENT"
    assert loaded.universe_version == "CURRENT"
    # The committed example requests the newest valid acceptance record and is
    # therefore not frozen until the runner resolves it.
    assert loaded.data_acceptance_id == "CURRENT_ACCEPTED"
    assert not loaded.is_frozen
    # resolve-to-explicit semantics: freezing the same example twice with the
    # same explicit versions and acceptance yields one stable id; a different
    # data version or a different acceptance yields a different id.
    frozen_once = loaded.freeze(
        dataset_version="aa" * 32,
        universe_version="bb" * 32,
        data_acceptance_id="ac" * 32,
        code_commit="example-head",
    )
    frozen_twice = load_experiment_spec(_EXAMPLE_SPEC).freeze(
        dataset_version="aa" * 32,
        universe_version="bb" * 32,
        data_acceptance_id="ac" * 32,
        code_commit="example-head",
    )
    assert frozen_once.is_frozen
    assert compute_experiment_id(frozen_once) == compute_experiment_id(frozen_twice)
    other_data = frozen_once.freeze(dataset_version="cc" * 32)
    assert compute_experiment_id(frozen_once) != compute_experiment_id(other_data)
    other_acceptance = frozen_once.freeze(data_acceptance_id="ad" * 32)
    assert compute_experiment_id(frozen_once) != compute_experiment_id(
        other_acceptance
    )
