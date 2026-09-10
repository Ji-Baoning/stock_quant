"""The atomic, irreversible holdout consumption registry.

A formal one-time challenge consumes its holdout exactly once per
``strategy_family + fold_schedule_hash`` (never per universe version: the
universe identity is recorded and hashed in every record but never widens
the key, so a new universe version cannot re-open an already consumed
history as "unseen").  While holding the exclusive ``.holdout.lock`` file
(created with ``O_CREAT|O_EXCL``, released in ``finally``) the registry
re-reads the authoritative consumption JSON files: an existing record is
returned *only* when its challenge id and every declaration hash match the
present declaration, and any other claimant of the consumed history is
refused with :class:`HoldoutAlreadyConsumed` without modifying a single
file.  A first consumer durably publishes the declaration's canonical bytes
under ``declarations/<challenge_id>.json`` (conflicting bytes under one id
are an identity failure), writes its own immutable consumption record to a
temporary sibling, ``fsync``s it, renames it atomically, and rebuilds the
``holdout_registry.parquet`` index from the immutable JSON files.

Consumed records are never deleted or overwritten: a crash, a FAILED run, a
REJECTED conclusion and an INCONCLUSIVE conclusion all leave the holdout
consumed.  Only the identical ``challenge_id`` (and therefore identical
declaration content) can recover idempotently.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, ValidationError

from stock_quant.research.strategy_challenge.models import (
    ChallengeDeclaration,
    UniverseIdentity,
    canonical_challenge_json_text,
    canonical_challenge_sha256,
    compute_challenge_id,
    holdout_consumption_key,
)

_LOCK_NAME = ".holdout.lock"
_REGISTRY_NAME = "holdout_registry.parquet"
_LOCK_RETRY_SECONDS = 0.02
_LOCK_TIMEOUT_SECONDS = 30.0

#: Column order of the rebuilt Parquet index (rebuilt from the immutable
#: consumption JSON files, which remain the authoritative state).
_INDEX_COLUMNS = (
    "consumption_key",
    "challenge_id",
    "status",
    "strategy_family",
    "fold_schedule_hash",
    "declaration_sha256",
    "comparison_policy_hash",
    "baseline_experiment_id",
    "challenger_strategy_hash",
    "universe_id",
    "universe_version",
    "membership_table_sha256",
    "evidence_summary_sha256",
    "consumed_at",
)


class HoldoutRegistryError(RuntimeError):
    """Base class for holdout-registry failures."""


class HoldoutBusy(HoldoutRegistryError):
    """Another consumer process holds the exclusive holdout lock."""


class HoldoutAlreadyConsumed(HoldoutRegistryError):
    """This strategy-family/calendar holdout is already consumed.

    The message carries the consuming challenge id; the consuming record is
    never modified or deleted, and the refused declaration wrote nothing.
    """


class HoldoutIdentityConflict(HoldoutRegistryError):
    """Published bytes or a recovered record disagree with the declaration."""


class HoldoutConsumption(BaseModel):
    """The immutable, atomic consumption record of one holdout.

    The complete universe identity block and the declaration hash are fixed
    in the record (and mirrored in the Parquet index) so every audit surface
    carries the full predeclared identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["consumed"] = "consumed"
    consumption_key: str
    challenge_id: str
    strategy_family: str
    fold_schedule_hash: str
    declaration_sha256: str
    comparison_policy_hash: str
    baseline_experiment_id: str
    challenger_strategy_hash: str
    universe_definition: UniverseIdentity
    #: The UTC instant of the atomic consumption rename (evidence metadata).
    consumed_at: str

    @property
    def key(self) -> str:
        return self.consumption_key


class HoldoutRegistry:
    """Single-writer, irreversible holdout registry below one project root."""

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root)

    # -- layout -----------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._project_root / "data" / "strategy_challenges"

    @property
    def declarations_root(self) -> Path:
        return self.root / "declarations"

    @property
    def consumptions_root(self) -> Path:
        return self.root / "consumptions"

    @property
    def registry_path(self) -> Path:
        return self.root / _REGISTRY_NAME

    @property
    def lock_path(self) -> Path:
        return self.root / _LOCK_NAME

    def declaration_path(self, challenge_id: str) -> Path:
        return self.declarations_root / f"{challenge_id}.json"

    def consumption_path(self, challenge_id: str) -> Path:
        return self.consumptions_root / f"{challenge_id}.json"

    # -- public API ---------------------------------------------------------

    def consume(
        self,
        declaration: ChallengeDeclaration,
        *,
        event_sink: Callable[[str], None] | list | None = None,
    ) -> HoldoutConsumption:
        """Consume the declaration's holdout exactly once (see module docs).

        ``event_sink`` (a list or callable) receives ``"holdout_consumed"``
        after the atomic consumption rename, never before it.
        """
        if not isinstance(declaration, ChallengeDeclaration):
            raise TypeError(
                "HoldoutRegistry.consume expects a ChallengeDeclaration, got "
                f"{type(declaration).__name__}"
            )
        challenge_id = compute_challenge_id(declaration)
        key = holdout_consumption_key(
            declaration.strategy_family, declaration.fold_schedule_hash
        )
        with self._exclusive_lock():
            # Re-read the authoritative state while holding the lock.
            record_path = self.consumption_path(challenge_id)
            if record_path.is_file():
                record = self._read_consumption(record_path)
                self._assert_record_matches(record, declaration, challenge_id)
                self._publish_declaration(declaration, challenge_id)
                self._ensure_index()
                return record
            prior = self._find_by_key(key)
            if prior is not None:
                if prior.challenge_id == challenge_id:
                    # The record file vanished under the lock (external
                    # tampering): the identity still matches, but consumed
                    # records are never rewritten -- refuse loudly.
                    raise HoldoutIdentityConflict(
                        f"consumption record {record_path} is missing while "
                        "the holdout is already consumed; consumed records "
                        "are never deleted or rewritten"
                    )
                raise HoldoutAlreadyConsumed(
                    f"holdout {key!r} is already consumed by challenge "
                    f"{prior.challenge_id}; a changed universe version, "
                    "parameter or strategy hash can never re-open a "
                    "consumed history as unseen"
                )
            self._publish_declaration(declaration, challenge_id)
            record = HoldoutConsumption(
                consumption_key=key,
                challenge_id=challenge_id,
                strategy_family=declaration.strategy_family,
                fold_schedule_hash=declaration.fold_schedule_hash,
                declaration_sha256=canonical_challenge_sha256(
                    declaration.model_dump(mode="json")
                ),
                comparison_policy_hash=declaration.comparison_policy_hash,
                baseline_experiment_id=declaration.baseline_experiment_id,
                challenger_strategy_hash=declaration.challenger_strategy_hash,
                universe_definition=declaration.universe_definition,
                consumed_at=_utc_now_iso(),
            )
            # Temporary sibling, fsync, atomic rename: a reader can never
            # observe a partial consumption record.
            _write_atomic(
                record_path,
                canonical_challenge_json_text(
                    record.model_dump(mode="json")
                ).encode("utf-8"),
            )
            self._rebuild_index_unlocked()
            _emit(event_sink, "holdout_consumed")
            return record

    def lookup(
        self, strategy_family: str, fold_schedule_hash: str
    ) -> HoldoutConsumption | None:
        """The consumption record for the key, or ``None`` when unconsumed."""
        key = holdout_consumption_key(strategy_family, fold_schedule_hash)
        return self._find_by_key(key)

    def recover(self, challenge_id: str) -> HoldoutConsumption:
        """The consumed record of exactly this challenge id, or failure."""
        record_path = self.consumption_path(challenge_id)
        if not record_path.is_file():
            raise HoldoutRegistryError(
                f"no consumption record exists for challenge {challenge_id}"
            )
        return self._read_consumption(record_path)

    def rebuild(self) -> Path:
        """Regenerate the Parquet index from the immutable JSON records."""
        with self._exclusive_lock():
            return self._rebuild_index_unlocked()

    # -- internals ----------------------------------------------------------

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                descriptor = os.open(
                    self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise HoldoutBusy(
                        f"holdout lock {self.lock_path} is held by another "
                        "consumer"
                    ) from None
                time.sleep(_LOCK_RETRY_SECONDS)
                continue
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            break
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def _publish_declaration(
        self, declaration: ChallengeDeclaration, challenge_id: str
    ) -> None:
        """Durably publish canonical declaration bytes (or verify existing).

        An existing declaration path is reusable only when its bytes match
        the canonical serialization of this exact declaration; conflicting
        bytes under one challenge id are an identity failure.
        """
        data = canonical_challenge_json_text(
            declaration.model_dump(mode="json")
        ).encode("utf-8")
        path = self.declaration_path(challenge_id)
        if path.is_file() and path.read_bytes() != data:
            raise HoldoutIdentityConflict(
                f"declaration {path} already exists with different bytes "
                f"under challenge id {challenge_id}; a published declaration "
                "is never overwritten"
            )
        _write_atomic(path, data)

    def _read_consumption(self, path: Path) -> HoldoutConsumption:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return HoldoutConsumption.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise HoldoutIdentityConflict(
                f"consumption record {path} is corrupt: {error}"
            ) from error

    def _assert_record_matches(
        self,
        record: HoldoutConsumption,
        declaration: ChallengeDeclaration,
        challenge_id: str,
    ) -> None:
        """Idempotent recovery only for the identical declaration content."""
        expected = {
            "consumption_key": holdout_consumption_key(
                declaration.strategy_family, declaration.fold_schedule_hash
            ),
            "challenge_id": challenge_id,
            "strategy_family": declaration.strategy_family,
            "fold_schedule_hash": declaration.fold_schedule_hash,
            "declaration_sha256": canonical_challenge_sha256(
                declaration.model_dump(mode="json")
            ),
            "comparison_policy_hash": declaration.comparison_policy_hash,
            "baseline_experiment_id": declaration.baseline_experiment_id,
            "challenger_strategy_hash": declaration.challenger_strategy_hash,
        }
        mismatches = sorted(
            field for field, value in expected.items()
            if getattr(record, field) != value
        )
        if record.universe_definition != declaration.universe_definition:
            mismatches.append("universe_definition")
        if mismatches:
            raise HoldoutIdentityConflict(
                "consumption record "
                f"{self.consumption_path(challenge_id)} disagrees with the "
                f"declaration on {mismatches}; only the identical challenge "
                "recovers idempotently"
            )

    def _consumption_records(self) -> list[HoldoutConsumption]:
        if not self.consumptions_root.is_dir():
            return []
        records = []
        for name in sorted(
            path.name for path in self.consumptions_root.glob("*.json")
        ):
            records.append(self._read_consumption(self.consumptions_root / name))
        return records

    def _find_by_key(self, key: str) -> HoldoutConsumption | None:
        for record in self._consumption_records():
            if record.consumption_key == key:
                return record
        return None

    def _ensure_index(self) -> None:
        if not self.registry_path.is_file():
            self._rebuild_index_unlocked()

    def _rebuild_index_unlocked(self) -> Path:
        self.consumptions_root.mkdir(parents=True, exist_ok=True)
        rows = []
        for record in self._consumption_records():
            universe = record.universe_definition
            rows.append({
                "consumption_key": record.consumption_key,
                "challenge_id": record.challenge_id,
                "status": record.status,
                "strategy_family": record.strategy_family,
                "fold_schedule_hash": record.fold_schedule_hash,
                "declaration_sha256": record.declaration_sha256,
                "comparison_policy_hash": record.comparison_policy_hash,
                "baseline_experiment_id": record.baseline_experiment_id,
                "challenger_strategy_hash": record.challenger_strategy_hash,
                "universe_id": universe.universe_id,
                "universe_version": universe.universe_version,
                "membership_table_sha256": universe.membership_table_sha256,
                "evidence_summary_sha256": universe.evidence_summary_sha256,
                "consumed_at": record.consumed_at,
            })
        index = pd.DataFrame.from_records(rows, columns=list(_INDEX_COLUMNS))
        if not index.empty:
            index = index.sort_values(
                "consumption_key", kind="stable"
            ).reset_index(drop=True)
        destination = self.registry_path
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            index.to_parquet(temporary, index=False)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


# --------------------------------------------------------------------------- #
# Small primitives
# --------------------------------------------------------------------------- #


def _write_atomic(path: Path, data: bytes) -> None:
    """Temporary sibling, ``fsync``, atomic rename (never overwrite)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise HoldoutIdentityConflict(
                f"{path} already exists with different bytes; consumed "
                "identity files are immutable and never overwritten"
            )
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform-dependent
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - platform-dependent
        pass
    finally:
        os.close(descriptor)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(sink: object, event: str) -> None:
    if sink is None:
        return
    if isinstance(sink, list):
        sink.append(event)
    else:
        sink(event)  # type: ignore[operator]
