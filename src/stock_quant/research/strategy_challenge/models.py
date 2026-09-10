"""Immutable one-time strategy-challenge declarations and identity contracts.

A formal strategy comparison is a *pre-registered* research act: before any
challenger result artifact is opened, one :class:`ChallengeDeclaration`
durably binds both strategy identities, the exact
:class:`StrategyComparisonPolicy`, the immutable fold schedule hash and the
complete four-field universe identity block (``universe_id``,
``universe_version``, ``membership_table_sha256``,
``evidence_summary_sha256``).  Every predeclared field -- including each of
the four universe fields -- participates in the content-derived
``challenge_id`` (:func:`compute_challenge_id`), so any identity change is a
different challenge that can never reuse an already consumed holdout.

Validation is total and strict: every SHA-256 field must be 64 lowercase
hexadecimal characters, every policy ``Decimal`` must be finite, the
``comparison_policy_hash`` is always recomputed from the embedded policy and
refused on mismatch, the declaration instant is UTC-only evidence metadata,
and the models are frozen with ``extra="forbid"`` so runtime fields (paths,
pid, host, worker count) can never enter the identity.  No constructor
accepts a mutable fallback for a missing identity field.

:class:`ChallengeResult` is the auditable outcome vocabulary: a terminal
``FAILED`` always carries a null conclusion, and a completed research
outcome is exactly one of ``PROMOTED``, ``REJECTED`` or
``INCONCLUSIVE_RESEARCH_ONLY``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Identity scheme version of the challenge declaration payload.  A future
#: serialization change deliberately invalidates every prior challenge id.
ChallengeIdentityScheme = Literal["strategy-challenge-v1"]

#: The one strategy family scope of the phase-one momentum challenge.
SHA256_PATTERN = r"^[0-9a-f]{64}$"

#: A strict 64-character lowercase hexadecimal SHA-256 field.
Sha256Hex = Annotated[str, Field(pattern=SHA256_PATTERN)]

#: The holdout consumption key scope: exactly the strategy family plus the
#: fold schedule hash.  A universe version never extends this key.
ConsumptionKeyStatus = Literal["consumed"]

#: The closed research-outcome vocabulary of a completed challenge.  A
#: terminal ``FAILED`` carries a null conclusion instead.
ChallengeConclusion = Literal[
    "PROMOTED", "REJECTED", "INCONCLUSIVE_RESEARCH_ONLY"
]


def canonical_challenge_json_text(value: Any) -> str:
    """Canonical JSON text: sorted keys, compact UTF-8 separators, no NaN.

    Values must already be JSON-ready (``model_dump(mode="json")`` for
    Pydantic models); ``allow_nan=False`` refuses any non-finite float so a
    challenge id can never silently depend on a NaN payload.
    """
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_challenge_sha256(value: Any) -> str:
    """SHA-256 of :func:`canonical_challenge_json_text` of ``value``."""
    return hashlib.sha256(
        canonical_challenge_json_text(value).encode("utf-8")
    ).hexdigest()


def holdout_consumption_key(strategy_family: str, fold_schedule_hash: str) -> str:
    """The exact holdout key: ``strategy_family + fold_schedule_hash``.

    The universe identity is recorded and hashed beside every consumption but
    never widens this key: a new universe version cannot re-open an already
    consumed historical holdout as "unseen".
    """
    return f"{strategy_family}:{fold_schedule_hash}"


class UniverseIdentity(BaseModel):
    """The inseparable four-field universe identity block.

    Exactly these four fields exist; any additional field (or any missing
    one) is a validation error.  All three hash fields must be 64 lowercase
    hexadecimal characters, and every field enters the ``challenge_id`` and
    the idempotent recovery checks.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    universe_id: str
    universe_version: Sha256Hex
    membership_table_sha256: Sha256Hex
    evidence_summary_sha256: Sha256Hex

    @model_validator(mode="after")
    def _check(self) -> "UniverseIdentity":
        if not self.universe_id.strip() or self.universe_id != self.universe_id.strip():
            raise ValueError(
                f"universe_id must be nonblank trimmed text: {self.universe_id!r}"
            )
        return self


class StrategyComparisonPolicy(BaseModel):
    """The frozen, hashable comparison policy (``strategy-comparison-v1``).

    The thresholds are policy, never hidden code constants: every declared
    cost scenario must satisfy every threshold for a ``PROMOTED`` conclusion,
    and no preferred scenario may be selected after the results are read.
    All thresholds are finite Decimals.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: Literal["strategy-comparison-v1"] = (
        "strategy-comparison-v1"
    )
    minimum_unconsumed_executed_folds: Literal[5] = 5
    aggregate_sharpe_delta_floor: Decimal = Field(
        default=Decimal("0.10"), allow_inf_nan=False
    )
    aggregate_annualized_return_delta_floor: Decimal = Field(
        default=Decimal("-0.02"), allow_inf_nan=False
    )
    positive_fold_ratio_delta_floor: Decimal = Field(
        default=Decimal("0.00"), allow_inf_nan=False
    )
    worst_fold_calendar_return_delta_floor: Decimal = Field(
        default=Decimal("-0.02"), allow_inf_nan=False
    )
    median_abs_max_drawdown_delta_ceiling: Decimal = Field(
        default=Decimal("0.00"), allow_inf_nan=False
    )
    median_turnover_ratio_ceiling: Decimal = Field(
        default=Decimal("0.85"), allow_inf_nan=False
    )
    median_explicit_cost_ratio_delta_ceiling: Decimal = Field(
        default=Decimal("0.00"), allow_inf_nan=False
    )
    median_reject_rate_delta_ceiling: Decimal = Field(
        default=Decimal("0.02"), allow_inf_nan=False
    )
    median_invested_exposure_floor: Decimal = Field(
        default=Decimal("0.90"), allow_inf_nan=False
    )


class ChallengeDeclaration(BaseModel):
    """The immutable pre-registration of one one-time strategy challenge.

    ``declared_before_run_at`` is serialized UTC evidence metadata only: it
    never substitutes for a missing identity field, but it does participate
    in the ``challenge_id`` so a re-declaration at a later instant is a new
    (and after consumption, impossible) challenge.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity_scheme_version: ChallengeIdentityScheme = (
        "strategy-challenge-v1"
    )
    strategy_family: str
    baseline_experiment_id: Sha256Hex
    challenger_strategy_hash: Sha256Hex
    fold_schedule_hash: Sha256Hex
    universe_definition: UniverseIdentity
    comparison_policy: StrategyComparisonPolicy
    #: The canonical SHA-256 of the embedded policy; recomputed and validated
    #: on every construction so the hash can never drift from the policy.
    comparison_policy_hash: Sha256Hex
    declared_before_run_at: datetime

    @model_validator(mode="after")
    def _check(self) -> "ChallengeDeclaration":
        if not self.strategy_family.strip() or (
            self.strategy_family != self.strategy_family.strip()
        ):
            raise ValueError(
                "strategy_family must be nonblank trimmed text: "
                f"{self.strategy_family!r}"
            )
        if self.declared_before_run_at.utcoffset() != timezone.utc.utcoffset(
            self.declared_before_run_at
        ):
            raise ValueError(
                "declared_before_run_at must be a UTC instant; a local or "
                "naive declaration time has no auditable UTC meaning"
            )
        recomputed = canonical_challenge_sha256(
            self.comparison_policy.model_dump(mode="json")
        )
        if recomputed != self.comparison_policy_hash:
            raise ValueError(
                "comparison_policy_hash does not match the embedded "
                f"comparison policy (declared {self.comparison_policy_hash!r}, "
                f"recomputed {recomputed!r})"
            )
        return self


def compute_challenge_id(declaration: ChallengeDeclaration) -> str:
    """The deterministic content identity of one challenge declaration.

    Every predeclared field participates: the identity scheme version, the
    strategy family, the baseline experiment id, the challenger strategy
    hash, the comparison policy hash (which covers the full policy content),
    the fold schedule hash, all four universe identity fields and the UTC
    declaration instant.  No runtime path, pid, host or worker count enters.
    """
    if not isinstance(declaration, ChallengeDeclaration):
        raise TypeError(
            "compute_challenge_id expects a ChallengeDeclaration, got "
            f"{type(declaration).__name__}"
        )
    payload = {
        "identity_scheme_version": declaration.identity_scheme_version,
        "strategy_family": declaration.strategy_family,
        "baseline_experiment_id": declaration.baseline_experiment_id,
        "challenger_strategy_hash": declaration.challenger_strategy_hash,
        "comparison_policy_hash": declaration.comparison_policy_hash,
        "fold_schedule_hash": declaration.fold_schedule_hash,
        "universe_definition": declaration.universe_definition.model_dump(
            mode="json"
        ),
        "declared_before_run_at": declaration.model_dump(mode="json")[
            "declared_before_run_at"
        ],
    }
    return canonical_challenge_sha256(payload)


class MetricCell(BaseModel):
    """One policy metric's paired values, threshold and verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    baseline: float | None = None
    challenger: float | None = None
    #: Challenger-minus-baseline delta, or the baseline-relative ratio for
    #: the turnover metric; ``None`` when either operand is undefined.
    delta: float | None = None
    #: The human-readable threshold expression from the frozen policy.
    threshold: str
    passed: bool


class ScenarioComparisonResult(BaseModel):
    """One declared cost scenario's full threshold evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario: str
    executed_fold_count: int
    passed: bool
    cells: tuple[MetricCell, ...] = ()

    @property
    def failed_metrics(self) -> tuple[str, ...]:
        return tuple(cell.metric for cell in self.cells if not cell.passed)


class ChallengeResult(BaseModel):
    """The auditable outcome of one one-time strategy challenge.

    ``status="FAILED"`` marks a terminal identity/registry/pairing/system
    integrity failure and always carries ``conclusion=None`` plus a redacted
    ``error_code``; every completed research outcome carries exactly one
    :class:`ChallengeConclusion` -- ``PROMOTED`` and ``REJECTED`` preserve
    every scenario's every threshold cell, so no failure can be hidden.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    challenge_id: str
    strategy_family: str
    status: Literal["COMPLETED", "FAILED"]
    conclusion: ChallengeConclusion | None = None
    baseline_experiment_id: str | None = None
    challenger_experiment_id: str | None = None
    challenger_stability_conclusion: str | None = None
    comparison_policy_hash: str | None = None
    executed_fold_count: int | None = None
    declared_scenario_count: int | None = None
    skipped_fold_ids: tuple[str, ...] = ()
    failed_scenarios: tuple[str, ...] = ()
    scenario_results: tuple[ScenarioComparisonResult, ...] = ()
    reasons: tuple[str, ...] = ()
    #: Redacted machine-readable failure code (never a path or traceback).
    error_code: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ChallengeResult":
        if self.status == "FAILED":
            if self.conclusion is not None:
                raise ValueError(
                    "a FAILED challenge has a null comparison conclusion; a "
                    "terminal integrity failure can never carry a verdict"
                )
        elif self.conclusion is None:
            raise ValueError(
                "a COMPLETED challenge carries exactly one formal conclusion"
            )
        return self
