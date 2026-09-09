"""Immutable, single-writer experiment registry (Task 7).

Publication requires a COMPLETED run manifest plus an evaluation of ACCEPTED
or REJECTED; ``ACCEPTED`` never means live approval, and a REJECTED experiment
is published and indexed exactly as it is, preserving its reason.  Declared
artifact hashes are validated before an atomic rename from
``data/runs/<run_id>/publish/`` to ``data/experiments/<experiment_id>/``.
Re-publishing identical content reuses the existing experiment; re-publishing
changed content under the same identity raises ``IdentityConflict``.  Failed
runs stay under ``data/runs/<run_id>/`` and never enter an experiment
directory.  ``rebuild`` regenerates ``registry.parquet`` from the immutable
experiment manifests while holding an exclusive ``O_CREAT|O_EXCL`` lock file
that is always released in ``finally``.  Everything is offline under
``tmp_path``; the lock test drives two child processes to prove mutual
exclusion deterministically.
"""

from __future__ import annotations

import json
import multiprocessing
import shutil
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.research.registry import (
    ArtifactHashMismatch,
    ExperimentIdentity,
    ExperimentRegistry,
    ExperimentRegistryError,
    IdentityConflict,
    IncompleteRunError,
)
from stock_quant.research.spec import ExperimentSpec

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"

_METRICS_JSON = '{"annualized_return": 0.0521, "max_drawdown": -0.13}'
_EVALUATION_REASON = "below_minimum_qualifying_names"


def _spec_kwargs(**overrides) -> dict:
    kwargs = dict(
        hypothesis="60-day momentum short-horizon continuation, engineering MVP.",
        factor_versions={"momentum_60d": "1.0.0"},
        dataset_version="d" * 64,
        universe_version="u" * 64,
        data_acceptance_id="a" * 64,
        date_range={"start_date": date(2020, 1, 1), "end_date": date(2026, 9, 2)},
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


def _spec(**overrides) -> ExperimentSpec:
    return ExperimentSpec.model_validate(_spec_kwargs(**overrides))


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stage_publish(
    run_dir: Path,
    spec: ExperimentSpec,
    identity: ExperimentIdentity,
    *,
    run_status: str = "COMPLETED",
    evaluation: str = "REJECTED",
    evaluation_reason: str = _EVALUATION_REASON,
    metrics_json: str = _METRICS_JSON,
) -> Path:
    """Write a complete staged run under ``run_dir/publish`` and hash it."""
    publish = run_dir / "publish"
    publish.mkdir(parents=True, exist_ok=True)
    run_id = run_dir.name
    (publish / "run_manifest.json").write_text(
        json.dumps({"run_id": run_id, "status": run_status}, indent=2),
        encoding="utf-8",
    )
    spec_file = publish / "experiment_spec.yml"
    spec_file.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    metrics_file = publish / "metrics.json"
    metrics_file.write_text(metrics_json, encoding="utf-8")
    artifacts = {
        "experiment_spec.yml": _sha256_file(spec_file),
        "metrics.json": _sha256_file(metrics_file),
    }
    experiment_manifest = {
        "experiment_id": identity.experiment_id,
        "status": evaluation,
        "dataset_version": spec.dataset_version,
        "universe_version": spec.universe_version,
        "data_acceptance_id": spec.data_acceptance_id,
        "code_commit": spec.code_commit,
        "evaluation_reason": evaluation_reason,
        "artifacts": artifacts,
    }
    (publish / "experiment_manifest.json").write_text(
        json.dumps(experiment_manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return run_dir


@pytest.fixture
def frozen_spec() -> ExperimentSpec:
    return _spec()


@pytest.fixture
def identity(frozen_spec) -> ExperimentIdentity:
    return ExperimentIdentity.of(frozen_spec)


@pytest.fixture
def completed_run(tmp_path, frozen_spec, identity) -> Path:
    run_dir = tmp_path / "data" / "runs" / "run_0001"
    return _stage_publish(run_dir, frozen_spec, identity)


def _experiments_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "experiments"


def _read_registry(tmp_path: Path) -> pd.DataFrame:
    return pd.read_parquet(_experiments_root(tmp_path) / "registry.parquet")


def test_publish_is_atomic_and_never_overwrites(
    tmp_path, completed_run, identity, frozen_spec
):
    registry = ExperimentRegistry(tmp_path)
    published = registry.publish(completed_run, identity)
    assert published.manifest.status == "REJECTED"
    # re-publishing the same completed run (whose staging already moved) reuses
    # the published experiment and reports the same path
    assert registry.publish(completed_run, identity).path == published.path
    # a re-run that restages changed content under the same identity must never
    # overwrite the immutable experiment
    _stage_publish(completed_run, frozen_spec, identity)
    (completed_run / "publish" / "metrics.json").write_text("{}")
    with pytest.raises(IdentityConflict):
        registry.publish(completed_run, identity)


def test_republish_of_identical_experiment_from_a_fresh_run_reuses_path(
    tmp_path, frozen_spec, identity
):
    registry = ExperimentRegistry(tmp_path)
    first = _stage_publish(
        tmp_path / "data" / "runs" / "run_0001", frozen_spec, identity
    )
    published = registry.publish(first, identity)
    second = _stage_publish(
        tmp_path / "data" / "runs" / "run_0002", frozen_spec, identity
    )
    assert registry.publish(second, identity).path == published.path
    experiment_dirs = [
        p for p in _experiments_root(tmp_path).iterdir() if p.is_dir()
    ]
    assert [p.name for p in experiment_dirs] == [identity.experiment_id]


def test_rejected_and_accepted_experiments_are_published_and_indexed(tmp_path):
    registry = ExperimentRegistry(tmp_path)
    rejected_spec = _spec(random_seed=1)
    rejected_id = ExperimentIdentity.of(rejected_spec)
    rejected_run = _stage_publish(
        tmp_path / "data" / "runs" / "run_rejected",
        rejected_spec,
        rejected_id,
        evaluation="REJECTED",
        evaluation_reason=_EVALUATION_REASON,
    )
    rejected = registry.publish(rejected_run, rejected_id)
    assert rejected.manifest.status == "REJECTED"
    assert rejected.manifest.evaluation_reason == _EVALUATION_REASON
    assert (_experiments_root(tmp_path) / rejected_id.experiment_id).is_dir()

    accepted_spec = _spec(random_seed=2, code_commit="cafe1234")
    accepted_id = ExperimentIdentity.of(accepted_spec)
    accepted_run = _stage_publish(
        tmp_path / "data" / "runs" / "run_accepted",
        accepted_spec,
        accepted_id,
        evaluation="ACCEPTED",
        evaluation_reason="",
    )
    accepted = registry.publish(accepted_run, accepted_id)
    assert accepted.manifest.status == "ACCEPTED"

    index = _read_registry(tmp_path).set_index("experiment_id")
    assert index.loc[rejected_id.experiment_id, "status"] == "REJECTED"
    assert (
        index.loc[rejected_id.experiment_id, "evaluation_reason"]
        == _EVALUATION_REASON
    )
    assert index.loc[accepted_id.experiment_id, "status"] == "ACCEPTED"


def test_failed_run_never_enters_an_experiment_directory(
    tmp_path, frozen_spec, identity
):
    registry = ExperimentRegistry(tmp_path)
    # a failed run has no publish/ staging at all
    failed = tmp_path / "data" / "runs" / "run_failed"
    failed.mkdir(parents=True)
    (failed / "run_manifest.json").write_text(
        json.dumps({"run_id": "run_failed", "status": "FAILED"}), encoding="utf-8"
    )
    with pytest.raises(IncompleteRunError):
        registry.publish(failed, identity)
    assert not (_experiments_root(tmp_path) / identity.experiment_id).exists()


def test_publish_requires_a_completed_run_manifest(tmp_path, identity, frozen_spec):
    registry = ExperimentRegistry(tmp_path)
    run_dir = tmp_path / "data" / "runs" / "run_running"
    _stage_publish(run_dir, frozen_spec, identity, run_status="RUNNING")
    with pytest.raises(IncompleteRunError):
        registry.publish(run_dir, identity)
    assert not (_experiments_root(tmp_path) / identity.experiment_id).exists()


def test_publish_requires_an_accepted_or_rejected_evaluation(
    tmp_path, identity, frozen_spec
):
    registry = ExperimentRegistry(tmp_path)
    run_dir = tmp_path / "data" / "runs" / "run_pending"
    _stage_publish(run_dir, frozen_spec, identity, evaluation="PENDING")
    with pytest.raises(ExperimentRegistryError):
        registry.publish(run_dir, identity)
    assert not (_experiments_root(tmp_path) / identity.experiment_id).exists()


def test_registry_rejects_an_untrusted_manifest_status(tmp_path, identity, frozen_spec):
    """A metrics-level UNTRUSTED decision never reaches an experiment manifest.

    The runner maps an untrusted ENGINEERING decision onto a REJECTED manifest
    (never ACCEPTED), so the immutable registry must refuse a manifest that
    literally claims ``UNTRUSTED`` as its status.
    """
    registry = ExperimentRegistry(tmp_path)
    run_dir = tmp_path / "data" / "runs" / "run_untrusted"
    _stage_publish(
        run_dir,
        frozen_spec,
        identity,
        evaluation="UNTRUSTED",
        evaluation_reason="corporate action trust: source fetch failed",
    )
    with pytest.raises(ExperimentRegistryError):
        registry.publish(run_dir, identity)
    assert not (_experiments_root(tmp_path) / identity.experiment_id).exists()


def test_publish_validates_declared_artifact_hashes_before_renaming(
    tmp_path, identity, frozen_spec
):
    registry = ExperimentRegistry(tmp_path)
    run_dir = tmp_path / "data" / "runs" / "run_tampered"
    _stage_publish(run_dir, frozen_spec, identity)
    (run_dir / "publish" / "metrics.json").write_text(
        json.dumps({"annualized_return": 9.9}), encoding="utf-8"
    )
    with pytest.raises(ArtifactHashMismatch):
        registry.publish(run_dir, identity)
    assert not (_experiments_root(tmp_path) / identity.experiment_id).exists()


def test_publish_rejects_acceptance_drift_from_the_frozen_spec(
    tmp_path, identity, frozen_spec
):
    """A manifest whose data_acceptance_id disagrees with the frozen spec
    stored beside it can never be published."""
    registry = ExperimentRegistry(tmp_path)
    run_dir = tmp_path / "data" / "runs" / "run_drift"
    _stage_publish(run_dir, frozen_spec, identity)
    manifest_path = run_dir / "publish" / "experiment_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["data_acceptance_id"] = "f" * 64
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(ExperimentRegistryError):
        registry.publish(run_dir, identity)
    assert not (_experiments_root(tmp_path) / identity.experiment_id).exists()


def test_rebuild_rejects_acceptance_drift_from_the_frozen_spec(
    tmp_path, identity, frozen_spec
):
    """The rebuilt index re-checks every manifest against its frozen spec."""
    registry = ExperimentRegistry(tmp_path)
    run_dir = _stage_publish(
        tmp_path / "data" / "runs" / "run_ok", frozen_spec, identity
    )
    registry.publish(run_dir, identity)
    manifest_path = (
        _experiments_root(tmp_path) / identity.experiment_id
        / "experiment_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["data_acceptance_id"] = None
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(ExperimentRegistryError):
        registry.rebuild()


def test_rebuild_reconstructs_registry_from_immutable_manifests(tmp_path):
    registry = ExperimentRegistry(tmp_path)
    ids = []
    for seed, evaluation in ((1, "REJECTED"), (2, "ACCEPTED"), (3, "REJECTED")):
        spec = _spec(random_seed=seed)
        experiment_id = ExperimentIdentity.of(spec)
        ids.append(experiment_id.experiment_id)
        run_dir = _stage_publish(
            tmp_path / "data" / "runs" / f"run_{seed}",
            spec,
            experiment_id,
            evaluation=evaluation,
        )
        registry.publish(run_dir, experiment_id)
    registry_path = _experiments_root(tmp_path) / "registry.parquet"
    registry_path.unlink()

    rebuilt = registry.rebuild()
    assert rebuilt == registry_path
    index = pd.read_parquet(rebuilt).set_index("experiment_id")
    assert sorted(index.index) == sorted(ids)
    assert index.loc[ids[0], "status"] == "REJECTED"
    assert index.loc[ids[1], "status"] == "ACCEPTED"


def test_rebuild_releases_exclusive_lock_when_a_manifest_is_corrupt(tmp_path):
    registry = ExperimentRegistry(tmp_path)
    bad_id = "b" * 64
    bad_dir = _experiments_root(tmp_path) / bad_id
    bad_dir.mkdir(parents=True)
    (bad_dir / "experiment_manifest.json").write_text(
        "{ this is not json", encoding="utf-8"
    )
    with pytest.raises(ExperimentRegistryError):
        registry.rebuild()
    lock_file = _experiments_root(tmp_path) / ".registry.lock"
    assert not lock_file.exists()
    # the registry is usable again once the corrupt entry is removed
    shutil.rmtree(bad_dir)
    assert registry.rebuild().is_file()


# --- two-process single-writer lock test -----------------------------------


def _lock_child(root: str, hold_seconds: float, timeout: float | None, queue, src: str):
    """Acquire the registry lock; report acquired/busy then released."""
    if src not in sys.path:
        sys.path.insert(0, src)
    from stock_quant.research.registry import ExperimentRegistry, RegistryBusy

    registry = ExperimentRegistry(Path(root))
    try:
        with registry.exclusive_lock(timeout=timeout):
            queue.put("acquired")
            if hold_seconds:
                time.sleep(hold_seconds)
        queue.put("released")
    except RegistryBusy:
        queue.put("busy")


def test_only_one_process_holds_the_registry_lock(tmp_path):
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    root = str(tmp_path)
    src = str(_SRC_ROOT)

    holder = ctx.Process(target=_lock_child, args=(root, 2.0, None, queue, src))
    holder.start()
    assert queue.get(timeout=30) == "acquired"

    contender = ctx.Process(target=_lock_child, args=(root, 0.0, 0.3, queue, src))
    contender.start()
    # the contender must NOT be able to acquire while the holder owns the lock
    assert queue.get(timeout=30) == "busy"
    assert queue.get(timeout=30) == "released"

    holder.join(timeout=30)
    contender.join(timeout=30)
    assert holder.exitcode == 0
    assert contender.exitcode == 0
    assert not holder.is_alive()
    assert not contender.is_alive()

    # the lock was released cleanly: a fresh process can acquire and rebuild
    fresh = ctx.Process(target=_lock_child, args=(root, 0.0, None, queue, src))
    fresh.start()
    assert queue.get(timeout=30) == "acquired"
    assert queue.get(timeout=30) == "released"
    fresh.join(timeout=30)
    assert fresh.exitcode == 0
    assert not (_experiments_root(tmp_path) / ".registry.lock").exists()
