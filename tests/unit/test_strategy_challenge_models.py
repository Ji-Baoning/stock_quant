"""Strict-identity tests for the one-time strategy-challenge contracts.

The declaration is the pre-registered, immutable research contract: a
complete four-field universe identity, the exact frozen comparison policy
with its recomputed hash, both strategy identities and the fold schedule
hash.  Every SHA-256 field must be 64 lowercase hexadecimal characters,
every policy Decimal must be finite, no runtime field (path, pid, host,
worker count) may enter the identity, and every predeclared field --
including each of the four universe identity fields -- must change the
computed ``challenge_id``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from stock_quant.research.strategy_challenge.models import (
    ChallengeDeclaration,
    ChallengeResult,
    StrategyComparisonPolicy,
    UniverseIdentity,
    canonical_challenge_sha256,
    compute_challenge_id,
    holdout_consumption_key,
)

_UTC = timezone.utc


def valid_declaration_dict() -> dict:
    """One valid declaration payload (hashes are syntactically valid hex)."""
    policy = StrategyComparisonPolicy()
    return {
        "identity_scheme_version": "strategy-challenge-v1",
        "strategy_family": "momentum_60d",
        "baseline_experiment_id": "1" * 64,
        "challenger_strategy_hash": "2" * 64,
        "fold_schedule_hash": "3" * 64,
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
        "declared_before_run_at": "2026-09-09T00:00:00Z",
    }


@pytest.fixture
def declaration() -> ChallengeDeclaration:
    return ChallengeDeclaration.model_validate(valid_declaration_dict())


def mutate_universe(universe: object, field: str) -> UniverseIdentity:
    payload = universe.model_dump(mode="json")
    value = payload[field]
    if field == "universe_id":
        payload[field] = "csi500"
    else:
        payload[field] = (
            ("0" if value[0] != "0" else "1") + value[1:]
        )
    return UniverseIdentity.model_validate(payload)


# --------------------------------------------------------------------------- #
# Canonical hashing
# --------------------------------------------------------------------------- #


def test_canonical_challenge_sha256_is_plain_compact_json_sha256():
    payload = {"b": 1, "a": [1, 2]}
    expected = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert canonical_challenge_sha256(payload) == expected
    assert canonical_challenge_sha256(payload) == canonical_challenge_sha256(
        {"a": [1, 2], "b": 1}
    )


def test_canonical_challenge_sha256_refuses_non_finite_floats():
    with pytest.raises(ValueError):
        canonical_challenge_sha256({"x": float("nan")})


# --------------------------------------------------------------------------- #
# Universe identity completeness
# --------------------------------------------------------------------------- #


def test_complete_universe_identity_is_required():
    with pytest.raises(ValidationError, match="membership_table_sha256"):
        ChallengeDeclaration.model_validate(valid_declaration_dict() | {
            "universe_definition": {
                "universe_id": "csi300", "universe_version": "a" * 64,
                "evidence_summary_sha256": "b" * 64,
            }
        })


def test_universe_identity_rejects_extra_fields():
    with pytest.raises(ValidationError, match="rules_version"):
        ChallengeDeclaration.model_validate(valid_declaration_dict() | {
            "universe_definition": {
                "universe_id": "csi300",
                "universe_version": "a" * 64,
                "membership_table_sha256": "b" * 64,
                "evidence_summary_sha256": "c" * 64,
                "rules_version": "extra",
            }
        })


# --------------------------------------------------------------------------- #
# challenge_id: every predeclared field participates
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field", ["universe_id", "universe_version",
                                   "membership_table_sha256",
                                   "evidence_summary_sha256"])
def test_each_universe_field_changes_challenge_id(declaration, field):
    changed = declaration.model_copy(update={
        "universe_definition": mutate_universe(declaration.universe_definition, field)
    })
    assert compute_challenge_id(changed) != compute_challenge_id(declaration)


@pytest.mark.parametrize("field", ["strategy_family", "baseline_experiment_id",
                                   "challenger_strategy_hash",
                                   "fold_schedule_hash"])
def test_each_predeclared_field_changes_challenge_id(declaration, field):
    value = getattr(declaration, field)
    if field == "strategy_family":
        changed_value = value + "_v2"
    else:
        changed_value = ("0" if value[0] != "0" else "1") + value[1:]
    changed = declaration.model_copy(update={field: changed_value})
    assert compute_challenge_id(changed) != compute_challenge_id(declaration)


def test_changed_comparison_policy_changes_challenge_id(declaration):
    tightened = declaration.comparison_policy.model_copy(update={
        "aggregate_sharpe_delta_floor": Decimal("0.20"),
    })
    changed = declaration.model_copy(update={
        "comparison_policy": tightened,
        "comparison_policy_hash": canonical_challenge_sha256(
            tightened.model_dump(mode="json")
        ),
    })
    assert compute_challenge_id(changed) != compute_challenge_id(declaration)


def test_declared_time_changes_challenge_id(declaration):
    changed = declaration.model_copy(update={
        "declared_before_run_at": declaration.declared_before_run_at
        + timedelta(minutes=1),
    })
    assert compute_challenge_id(changed) != compute_challenge_id(declaration)


def test_challenge_id_is_deterministic_64_hex(declaration):
    first = compute_challenge_id(declaration)
    second = compute_challenge_id(
        ChallengeDeclaration.model_validate(valid_declaration_dict())
    )
    assert first == second
    assert len(first) == 64
    int(first, 16)


def test_identity_carries_no_runtime_field(declaration):
    exported = declaration.model_dump(mode="json")
    for forbidden in ("path", "pid", "host", "worker_count", "run_id"):
        assert forbidden not in json.dumps(exported)


# --------------------------------------------------------------------------- #
# SHA-256 and policy validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field", ["baseline_experiment_id",
                                   "challenger_strategy_hash",
                                   "fold_schedule_hash",
                                   "comparison_policy_hash"])
def test_declaration_hashes_must_be_64_lowercase_hex(declaration, field):
    payload = valid_declaration_dict()
    payload[field] = "F" * 64  # uppercase hex is never a valid identity hash
    with pytest.raises(ValidationError):
        ChallengeDeclaration.model_validate(payload)


def test_universe_sha_fields_must_be_64_lowercase_hex():
    payload = valid_declaration_dict()
    payload["universe_definition"] = {
        "universe_id": "csi300",
        "universe_version": "a" * 64,
        "membership_table_sha256": "B" * 64,
        "evidence_summary_sha256": "c" * 64,
    }
    with pytest.raises(ValidationError):
        ChallengeDeclaration.model_validate(payload)


def test_policy_hash_is_recomputed_and_validated():
    payload = valid_declaration_dict()
    payload["comparison_policy_hash"] = "d" * 64
    with pytest.raises(ValidationError, match="comparison_policy_hash"):
        ChallengeDeclaration.model_validate(payload)


@pytest.mark.parametrize("value", [Decimal("Infinity"), Decimal("-Infinity"),
                                   Decimal("NaN")])
def test_policy_decimals_must_be_finite(value):
    with pytest.raises(ValidationError):
        StrategyComparisonPolicy(aggregate_sharpe_delta_floor=value)


def test_policy_is_frozen_and_closed():
    policy = StrategyComparisonPolicy()
    with pytest.raises(ValidationError):
        policy.aggregate_sharpe_delta_floor = Decimal("0.20")
    with pytest.raises(ValidationError):
        StrategyComparisonPolicy.model_validate(
            policy.model_dump(mode="json") | {"preferred_scenario": "full_cost"}
        )


def test_declaration_is_frozen(declaration):
    with pytest.raises(ValidationError):
        declaration.strategy_family = "mutated"


# --------------------------------------------------------------------------- #
# Declaration-time discipline
# --------------------------------------------------------------------------- #


def test_declaration_time_must_be_utc():
    payload = valid_declaration_dict()
    payload["declared_before_run_at"] = "2026-09-09T00:00:00+08:00"
    with pytest.raises(ValidationError, match="UTC"):
        ChallengeDeclaration.model_validate(payload)


def test_naive_declaration_time_is_rejected():
    payload = valid_declaration_dict()
    payload["declared_before_run_at"] = datetime(2026, 9, 9, 0, 0, 0)
    with pytest.raises(ValidationError):
        ChallengeDeclaration.model_validate(payload)


def test_strategy_family_must_be_nonblank():
    payload = valid_declaration_dict()
    payload["strategy_family"] = "   "
    with pytest.raises(ValidationError):
        ChallengeDeclaration.model_validate(payload)


def test_consumption_key_is_exactly_family_and_schedule_hash(declaration):
    assert holdout_consumption_key(
        declaration.strategy_family, declaration.fold_schedule_hash
    ) == f"{declaration.strategy_family}:{declaration.fold_schedule_hash}"


# --------------------------------------------------------------------------- #
# Challenge result contracts
# --------------------------------------------------------------------------- #


def completed_result_dict() -> dict:
    return {
        "challenge_id": "e" * 64,
        "strategy_family": "momentum_60d",
        "status": "COMPLETED",
        "conclusion": "INCONCLUSIVE_RESEARCH_ONLY",
    }


def test_failed_result_forces_null_conclusion():
    with pytest.raises(ValidationError):
        ChallengeResult.model_validate(
            completed_result_dict()
            | {"status": "FAILED", "conclusion": "PROMOTED"}
        )


def test_completed_result_requires_a_conclusion():
    with pytest.raises(ValidationError):
        ChallengeResult.model_validate(
            completed_result_dict() | {"conclusion": None}
        )


def test_result_conclusion_vocabulary_is_closed():
    with pytest.raises(ValidationError):
        ChallengeResult.model_validate(
            completed_result_dict() | {"conclusion": "MAYBE"}
        )


def test_failed_result_allows_null_conclusion():
    result = ChallengeResult.model_validate(
        completed_result_dict()
        | {"status": "FAILED", "conclusion": None,
           "error_code": "IDENTITY_MISMATCH"}
    )
    assert result.conclusion is None
    assert result.error_code == "IDENTITY_MISMATCH"
