"""Read-only view over published one-time strategy-challenge results.

The service never recomputes a challenge: it reads the immutable
``data/strategy_challenges/results/<challenge_id>/strategy_comparison.json``
artifacts actually on disk and projects the decision-layer evidence verbatim
(spec 2026-10-04 §3.1). The mutable holdout registry
(``holdout_registry.parquet``, ``.holdout.lock``) is deliberately not read --
it is locked operational state, not published evidence (spec §2.2).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, Field, ValidationError, field_validator

from stock_quant.service.datasets import read_json_or_fail
from stock_quant.service.errors import (
    ChallengeUnreadable,
    ExperimentManifestUnreadable,
    UnknownExperiment,
)

_EXPERIMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
#: Only a hex64-named *directory* under ``results/`` is a published challenge.
_CHALLENGE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_COMPARISON_NAME = "strategy_comparison.json"
_MANIFEST_NAME = "experiment_manifest.json"


class UniverseIdentityView(BaseModel):
    """The four-field universe identity block, verbatim (spec §3.1)."""

    universe_id: str
    universe_version: str
    membership_table_sha256: str
    evidence_summary_sha256: str


class ChallengeDeclarationView(BaseModel):
    """The pre-registered identity fields, verbatim; constants omitted."""

    strategy_family: str
    baseline_experiment_id: str
    challenger_strategy_hash: str
    fold_schedule_hash: str
    comparison_policy_hash: str
    declared_before_run_at: str
    universe_definition: UniverseIdentityView

    @field_validator("declared_before_run_at")
    @classmethod
    def _iso_instant(cls, value: str) -> str:
        """Kept verbatim, but an unparseable instant is not a valid artifact."""
        try:
            datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(
                f"declared_before_run_at is not an ISO instant: {value!r}"
            ) from error
        return value


class ChallengeConsumptionView(BaseModel):
    """The holdout consumption fact kept where declaration/result do not
    already carry it (spec §3.1 (a)); ``declaration_sha256`` is omitted as
    verification-layer evidence, not decision-layer evidence."""

    status: str
    consumption_key: str
    consumed_at: str
    universe_definition: UniverseIdentityView


class MetricCellView(BaseModel):
    """One policy metric's paired values, threshold expression and verdict."""

    metric: str
    baseline: float | None = None
    challenger: float | None = None
    delta: float | None = None
    threshold: str
    passed: bool


class ScenarioResultView(BaseModel):
    scenario: str
    executed_fold_count: int
    passed: bool
    cells: list[MetricCellView] = Field(default_factory=list)


class ChallengeResultView(BaseModel):
    status: str
    conclusion: str | None = None
    challenger_stability_conclusion: str | None = None
    challenger_experiment_id: str | None = None
    executed_fold_count: int | None = None
    declared_scenario_count: int | None = None
    skipped_fold_ids: list[str] = Field(default_factory=list)
    failed_scenarios: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    scenario_results: list[ScenarioResultView] = Field(default_factory=list)
    error_code: str | None = None


class ChallengeView(BaseModel):
    challenge_id: str
    #: Which side of the pairing this experiment occupies (spec §2.3). A
    #: challenge never puts one experiment on both sides.
    role: Literal["baseline", "challenger"]
    declaration: ChallengeDeclarationView
    consumption: ChallengeConsumptionView | None = None
    result: ChallengeResultView


class ExperimentChallengesResponse(BaseModel):
    experiment_id: str
    challenges: list[ChallengeView]


router = APIRouter(prefix="/api/v1", tags=["strategy-challenges"])


def _experiment_dir(request: Request, experiment_id: str) -> Path:
    """The published experiment directory, else ``UnknownExperiment``.

    Mirrors ``results.py::_experiment_dir`` so the read-only surface keeps one
    trust rule per router without cross-router coupling.
    """
    root = (Path(request.app.state.project_root) / "data" / "experiments").resolve()
    directory = (root / experiment_id).resolve()
    if directory.parent != root or not directory.is_dir():
        raise UnknownExperiment(f"no published experiment {experiment_id!r}")
    return directory


def _strategy_snapshot_hash(directory: Path) -> str | None:
    """The manifest's top-level ``strategy_snapshot_sha256`` (spec §2.3).

    This is the exact field ``PublishedExperimentLoader.resolve`` scans, so a
    challenger-side match here agrees with the run that produced the result.
    An unreadable manifest fails closed with the ``list_experiments`` envelope.
    """
    path = directory / _MANIFEST_NAME
    if not path.is_file():
        return None
    payload = read_json_or_fail(path, ExperimentManifestUnreadable, _MANIFEST_NAME)
    if not isinstance(payload, dict):
        return None
    value = payload.get("strategy_snapshot_sha256")
    return value if isinstance(value, str) else None


def _load_comparison(path: Path) -> dict[str, Any]:
    """Read and shape-check one published ``strategy_comparison.json``.

    A file that exists but is not a JSON object, or that lacks the
    declaration/result blocks every challenge always carries, fails closed
    (spec §3.2): a silent skip would hide a challenge that consumed the
    holdout.
    """
    payload = read_json_or_fail(path, ChallengeUnreadable, _COMPARISON_NAME)
    if not isinstance(payload, dict):
        raise ChallengeUnreadable(f"{_COMPARISON_NAME} is not a JSON object")
    if not isinstance(payload.get("declaration"), dict) or not isinstance(
        payload.get("result"), dict
    ):
        raise ChallengeUnreadable(
            f"{_COMPARISON_NAME} carries no declaration/result block"
        )
    return payload


def _project(
    payload: dict[str, Any],
    challenge_id: str,
    role: Literal["baseline", "challenger"],
) -> ChallengeView:
    """Project the published evidence verbatim (zero derivation, spec §3.1).

    Unknown fields are dropped by the models' default ``extra="ignore"``; a
    payload that does not satisfy the declared subset fails closed instead of
    being shown partially. A ``holdout_consumption`` that is neither absent
    nor an object is corrupted evidence, not an absent record, so it fails
    closed too (spec §3.2) instead of projecting as "never consumed".
    """
    consumption = payload.get("holdout_consumption")
    try:
        if consumption is None:
            consumption_view = None
        elif isinstance(consumption, dict):
            consumption_view = ChallengeConsumptionView.model_validate(consumption)
        else:
            raise ChallengeUnreadable(
                f"{_COMPARISON_NAME} of {challenge_id} carries a malformed "
                "holdout_consumption record"
            )
        return ChallengeView(
            challenge_id=challenge_id,
            role=role,
            declaration=ChallengeDeclarationView.model_validate(
                payload["declaration"]
            ),
            consumption=consumption_view,
            result=ChallengeResultView.model_validate(payload["result"]),
        )
    except ValidationError as error:
        raise ChallengeUnreadable(
            f"{_COMPARISON_NAME} of {challenge_id} does not project: {error}"
        ) from error


def _sort_key(view: ChallengeView) -> tuple[datetime, str]:
    """Ascending by declaration instant, ``challenge_id`` as the tiebreak."""
    return (
        datetime.fromisoformat(view.declaration.declared_before_run_at),
        view.challenge_id,
    )


@router.get(
    "/experiments/{experiment_id}/challenges",
    response_model=ExperimentChallengesResponse,
)
def experiment_challenges(
    experiment_id: Annotated[str, PathParam(pattern=_EXPERIMENT_ID_RE.pattern)],
    request: Request,
) -> ExperimentChallengesResponse:
    """Every published challenge this experiment takes part in (spec §2.3).

    Read-only and zero-derivation: membership is the two identity equalities
    -- baseline by experiment id, challenger by the manifest's strategy
    snapshot hash.
    """
    directory = _experiment_dir(request, experiment_id)
    strategy_hash = _strategy_snapshot_hash(directory)
    results_root = (
        Path(request.app.state.project_root)
        / "data"
        / "strategy_challenges"
        / "results"
    )
    views: list[ChallengeView] = []
    children = sorted(results_root.iterdir()) if results_root.is_dir() else []
    for child in children:
        if not child.is_dir() or not _CHALLENGE_ID_RE.fullmatch(child.name):
            continue
        payload = _load_comparison(child / _COMPARISON_NAME)
        declaration = payload["declaration"]
        is_baseline = declaration.get("baseline_experiment_id") == experiment_id
        is_challenger = strategy_hash is not None and (
            declaration.get("challenger_strategy_hash") == strategy_hash
        )
        if not is_baseline and not is_challenger:
            continue
        views.append(
            _project(payload, child.name, "baseline" if is_baseline else "challenger")
        )
    views.sort(key=_sort_key)
    return ExperimentChallengesResponse(experiment_id=experiment_id, challenges=views)
