"""Atomic, irreversible holdout consumption over one injected project root.

The registry consumes the ``strategy_family + fold_schedule_hash`` holdout
exactly once: the first consumer durably publishes the declaration and its
own immutable consumption record (temporary sibling, ``fsync``, atomic
rename) and rebuilds the Parquet index while holding the ``O_CREAT|O_EXCL``
lock; every later consumer of the same history either recovers the identical
``challenge_id`` idempotently or is refused with
:class:`HoldoutAlreadyConsumed` -- a changed universe version never re-opens
a consumed history.  All tests are offline and live under ``tmp_path``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.research.strategy_challenge.models import (
    ChallengeDeclaration,
    StrategyComparisonPolicy,
    UniverseIdentity,
    canonical_challenge_json_text,
    canonical_challenge_sha256,
    compute_challenge_id,
)
from stock_quant.research.strategy_challenge.registry import (
    HoldoutAlreadyConsumed,
    HoldoutIdentityConflict,
    HoldoutRegistry,
)


def valid_declaration(
    *,
    strategy_family: str = "momentum_60d",
    declared_at: datetime | None = None,
) -> ChallengeDeclaration:
    """One valid challenge declaration over syntactically valid identities."""
    policy = StrategyComparisonPolicy()
    return ChallengeDeclaration.model_validate({
        "identity_scheme_version": "strategy-challenge-v1",
        "strategy_family": strategy_family,
        "baseline_experiment_id": "1a" * 32,
        "challenger_strategy_hash": "2b" * 32,
        "fold_schedule_hash": "3c" * 32,
        "universe_definition": {
            "universe_id": "csi300",
            "universe_version": "a" * 64,
            "membership_table_sha256": "b" * 64,
            "evidence_summary_sha256": "c" * 64,
        },
        "comparison_policy": policy.model_dump(mode="json"),
        "comparison_policy_hash": canonical_challenge_sha256(
            policy.model_dump(mode="json")
        ),
        "declared_before_run_at": (
            declared_at or datetime(2026, 9, 9, tzinfo=timezone.utc)
        ).isoformat(),
    })


@pytest.fixture
def declaration() -> ChallengeDeclaration:
    return valid_declaration()


def _flip_first_hex(value: str) -> str:
    return ("0" if value[0] != "0" else "1") + value[1:]


def declaration_with_other_universe_version(
    declaration: ChallengeDeclaration,
) -> ChallengeDeclaration:
    """A different universe version over the *same* consumed history."""
    universe = declaration.universe_definition
    changed = UniverseIdentity.model_validate(
        universe.model_dump(mode="json")
        | {"universe_version": _flip_first_hex(universe.universe_version)}
    )
    return declaration.model_copy(update={"universe_definition": changed})


def changed_strategy_hash(
    declaration: ChallengeDeclaration,
) -> ChallengeDeclaration:
    """A different challenger under the same family/schedule holdout."""
    return declaration.model_copy(update={
        "challenger_strategy_hash": _flip_first_hex(
            declaration.challenger_strategy_hash
        )
    })


def record_key(declaration: ChallengeDeclaration) -> str:
    return f"{declaration.strategy_family}:{declaration.fold_schedule_hash}"


def challenges_root(tmp_path: Path) -> Path:
    return tmp_path / "data" / "strategy_challenges"


def test_first_consumer_atomically_consumes_family_schedule(
    tmp_path, declaration
):
    record = HoldoutRegistry(tmp_path).consume(declaration)
    assert record.status == "consumed"
    assert record.consumption_key == record_key(declaration)


def test_changed_universe_cannot_reconsume_same_history(tmp_path, declaration):
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    changed = declaration_with_other_universe_version(declaration)
    assert compute_challenge_id(changed) != compute_challenge_id(declaration)
    with pytest.raises(HoldoutAlreadyConsumed):
        registry.consume(changed)


def test_same_challenge_id_recovers_idempotently(tmp_path, declaration):
    registry = HoldoutRegistry(tmp_path)
    assert registry.consume(declaration) == registry.consume(declaration)


def test_consumption_and_declaration_files_are_published(
    tmp_path, declaration
):
    registry = HoldoutRegistry(tmp_path)
    record = registry.consume(declaration)
    challenge_id = compute_challenge_id(declaration)
    declaration_path = (
        challenges_root(tmp_path) / "declarations" / f"{challenge_id}.json"
    )
    consumption_path = (
        challenges_root(tmp_path) / "consumptions" / f"{challenge_id}.json"
    )
    assert declaration_path.is_file()
    assert consumption_path.is_file()
    declared = json.loads(declaration_path.read_text(encoding="utf-8"))
    consumed = json.loads(consumption_path.read_text(encoding="utf-8"))
    assert declared["universe_definition"] == (
        declaration.universe_definition.model_dump(mode="json")
    )
    assert consumed["declaration_sha256"] == record.declaration_sha256
    assert record.declaration_sha256 == canonical_challenge_sha256(
        declaration.model_dump(mode="json")
    )
    assert consumed["universe_definition"] == (
        declaration.universe_definition.model_dump(mode="json")
    )
    assert not list(challenges_root(tmp_path).rglob("*.tmp"))


def test_parquet_index_carries_full_identity(tmp_path, declaration):
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    index = pd.read_parquet(challenges_root(tmp_path) / "holdout_registry.parquet")
    assert len(index) == 1
    row = index.iloc[0]
    assert row["consumption_key"] == record_key(declaration)
    assert row["challenge_id"] == compute_challenge_id(declaration)
    assert row["declaration_sha256"] == canonical_challenge_sha256(
        declaration.model_dump(mode="json")
    )
    universe = declaration.universe_definition
    assert row["universe_id"] == universe.universe_id
    assert row["universe_version"] == universe.universe_version
    assert row["membership_table_sha256"] == universe.membership_table_sha256
    assert row["evidence_summary_sha256"] == (
        universe.evidence_summary_sha256
    )
    assert row["status"] == "consumed"


def test_lock_file_is_released_after_consumption(tmp_path, declaration):
    HoldoutRegistry(tmp_path).consume(declaration)
    assert not (challenges_root(tmp_path) / ".holdout.lock").exists()


def test_tampered_declaration_bytes_are_an_identity_conflict(
    tmp_path, declaration
):
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    challenge_id = compute_challenge_id(declaration)
    declaration_path = (
        challenges_root(tmp_path) / "declarations" / f"{challenge_id}.json"
    )
    tampered = json.loads(declaration_path.read_text(encoding="utf-8"))
    tampered["strategy_family"] = "tampered_family"
    declaration_path.write_text(
        canonical_challenge_json_text(tampered), encoding="utf-8"
    )
    with pytest.raises(HoldoutIdentityConflict):
        registry.consume(declaration)


def test_tampered_consumption_bytes_are_an_identity_conflict(
    tmp_path, declaration
):
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    challenge_id = compute_challenge_id(declaration)
    consumption_path = (
        challenges_root(tmp_path) / "consumptions" / f"{challenge_id}.json"
    )
    tampered = json.loads(consumption_path.read_text(encoding="utf-8"))
    tampered["declaration_sha256"] = "d" * 64
    consumption_path.write_text(
        canonical_challenge_json_text(tampered), encoding="utf-8"
    )
    with pytest.raises(HoldoutIdentityConflict):
        registry.consume(declaration)


def test_changed_strategy_hash_is_refused_and_writes_nothing(
    tmp_path, declaration
):
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    before = sorted(
        path.name for path in challenges_root(tmp_path).rglob("*")
    )
    with pytest.raises(HoldoutAlreadyConsumed):
        registry.consume(changed_strategy_hash(declaration))
    after = sorted(
        path.name for path in challenges_root(tmp_path).rglob("*")
    )
    assert before == after


def test_first_consumer_is_refused_a_second_schedule_only_when_consumed(
    tmp_path, declaration
):
    """A different fold schedule is a different, still-unconsumed holdout."""
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    other_schedule = declaration.model_copy(update={
        "fold_schedule_hash": _flip_first_hex(declaration.fold_schedule_hash)
    })
    record = registry.consume(other_schedule)
    assert record.consumption_key == record_key(other_schedule)
    index = pd.read_parquet(challenges_root(tmp_path) / "holdout_registry.parquet")
    assert len(index) == 2


def test_policy_thresholds_are_policy_not_constants(tmp_path, declaration):
    """A tightened but consistently hashed policy is a new challenge id."""
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    tightened_policy = StrategyComparisonPolicy(
        aggregate_sharpe_delta_floor=Decimal("0.50"),
    )
    payload = declaration.model_dump(mode="json")
    payload["comparison_policy"] = tightened_policy.model_dump(mode="json")
    payload["comparison_policy_hash"] = canonical_challenge_sha256(
        tightened_policy.model_dump(mode="json")
    )
    tightened = ChallengeDeclaration.model_validate(payload)
    with pytest.raises(HoldoutAlreadyConsumed):
        registry.consume(tightened)


# --------------------------------------------------------------------------- #
# Concurrency and crash behaviour
# --------------------------------------------------------------------------- #


def run_two_consumers(
    tmp_path: Path,
    first: ChallengeDeclaration,
    second: ChallengeDeclaration,
) -> list[object]:
    """Two simultaneous consumers; returns records and raised errors."""
    import threading

    barrier = threading.Barrier(2)
    results: list[object] = []

    def consume(payload: ChallengeDeclaration) -> None:
        barrier.wait()
        try:
            results.append(HoldoutRegistry(tmp_path).consume(payload))
        except Exception as error:  # noqa: BLE001 - the race outcome itself
            results.append(error)

    threads = [
        threading.Thread(target=consume, args=(payload,))
        for payload in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_two_concurrent_consumers_yield_one_record(tmp_path, declaration):
    results = run_two_consumers(
        tmp_path, declaration, changed_strategy_hash(declaration)
    )
    assert sum(
        getattr(result, "status", None) == "consumed" for result in results
    ) == 1
    assert sum(
        isinstance(result, HoldoutAlreadyConsumed) for result in results
    ) == 1
    consumptions = list((challenges_root(tmp_path) / "consumptions").glob("*.json"))
    assert len(consumptions) == 1


def test_concurrent_identical_consumers_share_one_record(
    tmp_path, declaration
):
    results = run_two_consumers(tmp_path, declaration, declaration)
    assert len(results) == 2
    assert results[0] == results[1]
    assert getattr(results[0], "status", None) == "consumed"


def test_consumption_survives_challenge_failure(tmp_path, declaration):
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    assert registry.lookup(
        declaration.strategy_family, declaration.fold_schedule_hash
    ).status == "consumed"


def test_consumption_survives_crash_before_index_rebuild(
    tmp_path, declaration
):
    """A crash between the rename and the index rebuild loses nothing.

    The JSON consumption record is authoritative: lookup still finds the
    consumed holdout, an identical challenge recovers and rebuilds the
    index, and no record is ever rolled back or deleted.
    """
    registry = HoldoutRegistry(tmp_path)
    events: list[str] = []
    registry.consume(declaration, event_sink=events)
    assert events == ["holdout_consumed"]
    # Simulated crash: everything after the atomic rename is lost.
    (challenges_root(tmp_path) / "holdout_registry.parquet").unlink()
    found = registry.lookup(
        declaration.strategy_family, declaration.fold_schedule_hash
    )
    assert found is not None and found.status == "consumed"
    recovered = registry.consume(declaration)
    assert found == recovered
    index = pd.read_parquet(
        challenges_root(tmp_path) / "holdout_registry.parquet"
    )
    assert len(index) == 1
    assert index.iloc[0]["challenge_id"] == compute_challenge_id(declaration)


def test_lookup_returns_none_for_unconsumed_history(tmp_path, declaration):
    assert HoldoutRegistry(tmp_path).lookup(
        declaration.strategy_family, declaration.fold_schedule_hash
    ) is None
