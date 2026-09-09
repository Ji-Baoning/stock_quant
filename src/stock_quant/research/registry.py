"""Immutable, single-writer experiment registry (Task 7).

A run publishes only when it is *complete*: a ``COMPLETED`` run manifest plus
an evaluation of ``ACCEPTED`` or ``REJECTED`` in the experiment manifest.
``ACCEPTED`` never means live approval; a REJECTED experiment is published and
indexed exactly as it is, preserving its reason.  The registry validates every
declared artifact sha256, then atomically renames ``data/runs/<run_id>/publish/``
to ``data/experiments/<experiment_id>/``.  Re-publishing identical content
returns the existing experiment (reuse); re-publishing changed content under
the same identity raises :class:`IdentityConflict`.  Execution failures stay
under ``data/runs/<run_id>/`` and never enter an experiment directory.

``data/experiments/registry.parquet`` is the shared index and is rebuilt from
the immutable experiment manifests only while holding an exclusive lock file
created with ``O_CREAT|O_EXCL``; the lock is always released in a ``finally``
block.  All root paths are injected through ``project_root`` so tests run
entirely under ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from stock_quant.research.spec import (
    ExperimentSpec,
    compute_experiment_id,
    load_experiment_spec,
)
from stock_quant.research.walk_forward.snapshots import SnapshotBundle

_MANIFEST_NAME = "experiment_manifest.json"
_RUN_MANIFEST_NAME = "run_manifest.json"
_STAGING_NAME = "publish"
_REGISTRY_NAME = "registry.parquet"
_LOCK_NAME = ".registry.lock"
_LOCK_RETRY_SECONDS = 0.02

#: Column order of the rebuilt ``registry.parquet`` index.
_INDEX_COLUMNS = (
    "experiment_id",
    "status",
    "dataset_version",
    "universe_version",
    "universe_id",
    "code_commit",
    "evaluation_reason",
)


class ExperimentRegistryError(RuntimeError):
    """Base class for registry failures."""


class IncompleteRunError(ExperimentRegistryError):
    """A run did not reach a publishable COMPLETED state."""


class InvalidExperimentManifest(ExperimentRegistryError):
    """The staged experiment manifest is missing, malformed or inconsistent."""


class IdentityConflict(ExperimentRegistryError):
    """The experiment already exists with different immutable content."""


class ArtifactHashMismatch(ExperimentRegistryError):
    """A declared artifact sha256 does not match the staged file."""


class RegistryBusy(ExperimentRegistryError):
    """Another publisher process holds the exclusive registry lock."""


class RegistryIntegrityError(ExperimentRegistryError):
    """An experiment directory is missing or has a corrupt manifest."""


class ExperimentManifest(BaseModel):
    """Typed read of the immutable experiment manifest published with a run."""

    experiment_id: str
    status: Literal["ACCEPTED", "REJECTED"]
    dataset_version: str
    universe_version: str
    #: The frozen universe definition identity a definition-backed run
    #: records (Task 4).  ``None`` for legacy manifests and runs resolved
    #: through the engineering ``configs/universe.yml`` path.
    universe_id: str | None = None
    universe_rules_version: str | None = None
    universe_membership_table_sha256: str | None = None
    #: The pinned real-data acceptance of the frozen spec (``None`` for an
    #: ENGINEERING diagnostic).  Validated against the frozen spec itself.
    data_acceptance_id: str | None = None
    code_commit: str | None = None
    #: The three frozen walk-forward snapshot hashes (identity scheme v2).
    #: A manifest without all three carries no snapshot bundle and can be
    #: neither published nor indexed.
    strategy_snapshot_sha256: str
    experiment_snapshot_sha256: str
    data_environment_snapshot_sha256: str
    evaluation_reason: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentIdentity:
    """Deterministic identity of one frozen experiment spec + snapshot bundle.

    Carries the explicit data versions, code commit and the three frozen
    snapshot hashes the identity was computed from so the registry can
    cross-check a staged manifest against the exact bundle.
    """

    experiment_id: str
    dataset_version: str
    universe_version: str
    code_commit: str
    strategy_snapshot_sha256: str
    experiment_snapshot_sha256: str
    data_environment_snapshot_sha256: str

    @classmethod
    def of(cls, spec: ExperimentSpec, snapshots: "SnapshotBundle") -> "ExperimentIdentity":
        if not isinstance(spec, ExperimentSpec):
            raise TypeError(
                "ExperimentIdentity.of expects an ExperimentSpec, got "
                f"{type(spec).__name__}"
            )
        if not isinstance(snapshots, SnapshotBundle):
            raise TypeError(
                "ExperimentIdentity.of expects a SnapshotBundle as its second "
                f"argument, got {type(snapshots).__name__}"
            )
        return cls(
            experiment_id=compute_experiment_id(spec, snapshots),
            dataset_version=spec.dataset_version,
            universe_version=spec.universe_version,
            code_commit=spec.code_commit,
            strategy_snapshot_sha256=snapshots.strategy_hash,
            experiment_snapshot_sha256=snapshots.experiment_hash,
            data_environment_snapshot_sha256=snapshots.data_environment_hash,
        )


@dataclass(frozen=True)
class PublishedExperiment:
    """A published immutable experiment and its parsed manifest."""

    experiment_id: str
    path: Path
    manifest: ExperimentManifest


class ExperimentRegistry:
    """Single-writer, immutable experiment registry below one project root."""

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root)

    @property
    def experiments_root(self) -> Path:
        return self._project_root / "data" / "experiments"

    @contextmanager
    def exclusive_lock(self, *, timeout: float | None = None) -> Iterator[None]:
        """Hold the single-writer registry lock, releasing it in ``finally``.

        ``timeout=None`` waits until the lock is free; a finite ``timeout``
        raises :class:`RegistryBusy` instead of blocking forever.
        """
        lock_file = self.experiments_root / _LOCK_NAME
        descriptor = self._acquire_lock_file(lock_file, timeout)
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                lock_file.unlink()
            except FileNotFoundError:
                pass

    def publish(
        self, run_dir: str | Path, identity: ExperimentIdentity
    ) -> PublishedExperiment:
        """Validate and atomically publish one complete run's staging.

        ``run_dir`` is ``data/runs/<run_id>``; its ``publish/`` subtree is the
        immutable content that is renamed into ``data/experiments/``.
        """
        if not isinstance(identity, ExperimentIdentity):
            raise TypeError(
                "publish expects an ExperimentIdentity, got "
                f"{type(identity).__name__}"
            )
        run_dir = Path(run_dir)
        destination = self.experiments_root / identity.experiment_id
        with self.exclusive_lock():
            staged = run_dir / _STAGING_NAME
            if not staged.is_dir():
                # The staging was already renamed away on a prior publish:
                # reuse the published experiment rather than raise.
                if destination.exists():
                    existing = self._read_manifest(destination)
                    if existing.experiment_id != identity.experiment_id:
                        raise InvalidExperimentManifest(
                            f"existing experiment {existing.experiment_id} does not "
                            f"match identity {identity.experiment_id}"
                        )
                    return PublishedExperiment(
                        experiment_id=identity.experiment_id,
                        path=destination,
                        manifest=existing,
                    )
                raise IncompleteRunError(
                    f"no {_STAGING_NAME}/ under {run_dir}; a run must reach "
                    "COMPLETED with staged artifacts before it can be published"
                )
            run_manifest = self._read_run_manifest(staged)
            if run_manifest.get("status") != "COMPLETED":
                raise IncompleteRunError(
                    f"run {run_dir.name} has status "
                    f"{run_manifest.get('status')!r}; only a COMPLETED run may "
                    "be published. Execution failures remain under data/runs/ "
                    "and never become experiments."
                )
            manifest = self._read_manifest(staged)
            if manifest.experiment_id != identity.experiment_id:
                raise InvalidExperimentManifest(
                    f"staged experiment {manifest.experiment_id} does not match "
                    f"identity {identity.experiment_id}"
                )
            _assert_manifest_snapshot_binding(
                staged, manifest, identity, InvalidExperimentManifest
            )
            _assert_manifest_acceptance_binding(
                staged, manifest, InvalidExperimentManifest
            )
            if destination.exists():
                if _declared_files_match(staged, destination, manifest.artifacts):
                    return PublishedExperiment(
                        experiment_id=identity.experiment_id,
                        path=destination,
                        manifest=manifest,
                    )
                raise IdentityConflict(
                    f"experiment {identity.experiment_id} already exists under "
                    f"{destination} with different content; immutable experiments "
                    "are never overwritten"
                )
            _assert_declared_hashes(staged, manifest.artifacts)
            self.experiments_root.mkdir(parents=True, exist_ok=True)
            os.replace(staged, destination)
            self._rebuild_unlocked()
            return PublishedExperiment(
                experiment_id=identity.experiment_id,
                path=destination,
                manifest=manifest,
            )

    def rebuild(self) -> Path:
        """Regenerate ``registry.parquet`` from the immutable manifests."""
        with self.exclusive_lock():
            return self._rebuild_unlocked()

    # -- internals ---------------------------------------------------------

    def _acquire_lock_file(self, lock_file: Path, timeout: float | None) -> int:
        self.experiments_root.mkdir(parents=True, exist_ok=True)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                descriptor = os.open(
                    lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except FileExistsError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise RegistryBusy(
                        f"registry lock {lock_file} is held by another publisher"
                    ) from None
                time.sleep(_LOCK_RETRY_SECONDS)
                continue
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            return descriptor

    def _read_run_manifest(self, staged: Path) -> dict[str, object]:
        """Return the staged run manifest, which must be a JSON object."""
        try:
            raw = json.loads((staged / _RUN_MANIFEST_NAME).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as error:
            raise IncompleteRunError(
                f"{staged.parent} has no valid {_RUN_MANIFEST_NAME}"
            ) from error
        if not isinstance(raw, dict):
            raise IncompleteRunError(
                f"{staged.parent}/{_RUN_MANIFEST_NAME} must be a JSON object"
            )
        return raw

    def _read_manifest(self, staged: Path) -> ExperimentManifest:
        manifest_path = staged / _MANIFEST_NAME
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as error:
            raise InvalidExperimentManifest(
                f"staging has no valid {_MANIFEST_NAME} under {staged}"
            ) from error
        try:
            manifest = ExperimentManifest.model_validate(raw)
        except ValidationError as error:
            raise InvalidExperimentManifest(
                f"{_MANIFEST_NAME} under {staged} is invalid: {error}"
            ) from error
        if not isinstance(manifest.experiment_id, str) or (
            not manifest.experiment_id
        ):
            # A published experiment must always carry its frozen identity; a
            # null experiment id belongs only to a FAILED preflight run
            # manifest, which can never reach publication.
            raise InvalidExperimentManifest(
                f"{_MANIFEST_NAME} under {staged} carries no experiment id"
            )
        return manifest

    def _rebuild_unlocked(self) -> Path:
        self.experiments_root.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, object]] = []
        for name in sorted(
            entry.name for entry in self.experiments_root.iterdir() if entry.is_dir()
        ):
            directory = self.experiments_root / name
            manifest_path = directory / _MANIFEST_NAME
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as error:
                raise RegistryIntegrityError(
                    f"experiment {name} has no valid {_MANIFEST_NAME}: {error}"
                ) from error
            if not isinstance(raw, dict) or raw.get("experiment_id") != name:
                raise RegistryIntegrityError(
                    f"experiment directory {name} disagrees with its manifest "
                    f"experiment_id {raw.get('experiment_id')!r}"
                )
            manifest = self._read_manifest(directory)
            _assert_manifest_acceptance_binding(
                directory, manifest, RegistryIntegrityError
            )
            records.append(
                {
                    "experiment_id": name,
                    "status": raw.get("status"),
                    "dataset_version": raw.get("dataset_version"),
                    "universe_version": raw.get("universe_version"),
                    "universe_id": raw.get("universe_id"),
                    "code_commit": raw.get("code_commit"),
                    "evaluation_reason": raw.get("evaluation_reason"),
                }
            )
        index = pd.DataFrame.from_records(records, columns=list(_INDEX_COLUMNS))
        if not index.empty:
            index = (
                index.sort_values("experiment_id", kind="stable")
                .reset_index(drop=True)
            )
        destination = self.experiments_root / _REGISTRY_NAME
        temporary = self.experiments_root / f".{_REGISTRY_NAME}.{uuid.uuid4().hex}.tmp"
        try:
            index.to_parquet(temporary, index=False)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


def _assert_manifest_snapshot_binding(
    directory: Path,
    manifest: ExperimentManifest,
    identity: ExperimentIdentity,
    error_class: type[ExperimentRegistryError],
) -> None:
    """Require the manifest to carry exactly the identity's snapshot hashes.

    An experiment is published only under the frozen snapshot bundle its
    identity was computed from: a manifest whose three snapshot hashes
    disagree with the identity (or, by construction of the manifest model,
    one that omits them) can never be published or indexed.
    """
    declared = {
        "strategy_snapshot_sha256": (
            manifest.strategy_snapshot_sha256,
            identity.strategy_snapshot_sha256,
        ),
        "experiment_snapshot_sha256": (
            manifest.experiment_snapshot_sha256,
            identity.experiment_snapshot_sha256,
        ),
        "data_environment_snapshot_sha256": (
            manifest.data_environment_snapshot_sha256,
            identity.data_environment_snapshot_sha256,
        ),
    }
    for field, (manifest_value, identity_value) in declared.items():
        if manifest_value != identity_value:
            raise error_class(
                f"experiment {manifest.experiment_id} manifest records "
                f"{field} {manifest_value!r} but the identity pins "
                f"{identity_value!r} ({directory})"
            )


def _assert_manifest_acceptance_binding(
    directory: Path,
    manifest: ExperimentManifest,
    error_class: type[ExperimentRegistryError],
) -> None:
    """Require the manifest to carry exactly its frozen spec's acceptance id.

    The frozen ``experiment_spec.yml`` stored beside the manifest is the
    authority: a manifest whose ``data_acceptance_id`` disagrees with it (or a
    spec whose acceptance was never resolved) cannot be published or indexed.
    """
    spec_path = directory / "experiment_spec.yml"
    try:
        spec = load_experiment_spec(spec_path)
    except (OSError, ValueError) as error:
        raise error_class(
            f"{spec_path} is not a valid frozen experiment spec: {error}"
        ) from error
    if spec.data_acceptance_id != manifest.data_acceptance_id:
        raise error_class(
            f"experiment {manifest.experiment_id} manifest records "
            f"data_acceptance_id {manifest.data_acceptance_id!r} but its "
            f"frozen spec pins {spec.data_acceptance_id!r}"
        )


def _declared_files_match(
    staged: Path, destination: Path, artifacts: dict[str, str]
) -> bool:
    """True when every declared artifact file is byte-identical in both trees."""
    return _actual_declared_hashes(staged, artifacts) == _actual_declared_hashes(
        destination, artifacts
    )


def _actual_declared_hashes(
    directory: Path, artifacts: dict[str, str]
) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for name in artifacts:
        path = directory / name
        hashes[name] = _sha256_file(path) if path.is_file() else None
    return hashes


def _assert_declared_hashes(staged: Path, artifacts: dict[str, str]) -> None:
    for name, declared in sorted(artifacts.items()):
        path = staged / name
        actual = _sha256_file(path) if path.is_file() else None
        if actual is None or actual != declared:
            raise ArtifactHashMismatch(
                f"declared sha256 of {name!r} does not match the staged file "
                f"{path} (declared {declared}, staged {actual})"
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
