"""Run-state machine, evaluation records and artifact contract (Task 11).

The research runner keeps one mutable :class:`RunState` per ``run_id`` and
persists it as ``run_manifest.json`` so a run can be resumed and audited.  The
run ``status`` moves ``CREATED -> RUNNING -> COMPLETED`` and lands on ``FAILED``
when any stage or injected observer raises; the coarse ``stage`` field follows
the data-stage vocabulary (``PUBLISHED`` after the data is pinned,
``FACTOR_READY``, ``BACKTESTED``, ``REPORTED``) while ``failed_stage`` records
the fine internal stage label where a failure happened.

This module also owns the *artifact contract*: the exact set of file names a
published experiment must contain (``REQUIRED_ARTIFACTS``), and the
``Evaluation`` / ``ExperimentEvaluation`` records the report stage hands to the
experiment manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _utc_now() -> str:
    """An ISO-8601 timestamp with the local offset, second precision."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# State vocabularies
# --------------------------------------------------------------------------- #


class RunStatus(str, Enum):
    """Lifecycle of one ``run_id`` under ``data/runs/<run_id>/``."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DataStage(str, Enum):
    """Coarse pipeline stage reached by the run (the shared data-stage vocab)."""

    CREATED = "CREATED"
    FETCHING = "FETCHING"
    RAW_SAVED = "RAW_SAVED"
    CLEANING = "CLEANING"
    VALIDATING = "VALIDATING"
    PUBLISHED = "PUBLISHED"
    FACTOR_READY = "FACTOR_READY"
    BACKTESTED = "BACKTESTED"
    REPORTED = "REPORTED"
    FAILED = "FAILED"


class ExperimentEvaluation(str, Enum):
    """Engineering/performance acceptance recorded on a published experiment.

    ``UNTRUSTED`` records a run whose corporate-action evidence is not trusted
    (an ENGINEERING diagnostic); it is never written to an experiment manifest,
    which only ever records ``ACCEPTED`` or ``REJECTED``.
    """

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNTRUSTED = "UNTRUSTED"


@dataclass(frozen=True)
class Evaluation:
    """One evaluation decision: accepted/rejected plus an explicit reason."""

    status: ExperimentEvaluation
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExperimentEvaluation):
            raise TypeError(
                "Evaluation.status must be an ExperimentEvaluation, got "
                f"{self.status!r}"
            )


# --------------------------------------------------------------------------- #
# Run failure
# --------------------------------------------------------------------------- #


class ResearchRunFailed(RuntimeError):
    """A research run failed before publishing; its partial state stays auditable.

    ``run_id`` identifies ``data/runs/<run_id>/`` where the failed run's
    artifacts and FAILED manifest remain; ``failed_stage`` is the fine internal
    stage label where the failure happened.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        failed_stage: str | None = None,
        retriable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.failed_stage = failed_stage
        self.retriable = retriable


# --------------------------------------------------------------------------- #
# RunState (persisted as data/runs/<run_id>/run_manifest.json)
# --------------------------------------------------------------------------- #


class RunState(BaseModel):
    """Mutable, persisted state of one research run.

    Every field is JSON-serialisable so ``to_dict()`` can be written verbatim as
    the run manifest; ``status``/``stage`` use the shared vocabularies above and
    ``error`` mirrors the redacted failure record.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    #: ``None`` only for a preflight failure that happened before an
    #: experiment identity could be formed (e.g. the data-acceptance gate).
    experiment_id: str | None
    status: RunStatus = RunStatus.CREATED
    stage: DataStage = DataStage.CREATED
    dataset_version: str
    universe_version: str
    #: The frozen universe definition identity a formal run was accepted
    #: under (Task 4): ``universe_id``, ``universe_version``,
    #: ``rules_version``, ``membership_table_sha256``,
    #: ``evidence_summary_sha256``, the coverage window, the acceptance
    #: status and the per-signal-day member counts / snapshot-hash maps.
    #: ``None`` for runs resolved through the legacy engineering universe.
    universe: dict[str, Any] | None = None
    #: Sanitized audit of the pinned real-data acceptance record
    #: (:func:`acceptance_audit_dict`), or ``None`` when no acceptance was
    #: resolved (an ENGINEERING diagnostic or a preflight failure).
    data_acceptance: dict[str, Any] | None = None
    code_commit: str = "unversioned"
    factor_versions: dict[str, str] = Field(default_factory=dict)
    cost_scenarios: list[str] = Field(default_factory=list)
    random_seed: int | None = None
    parent_experiment_ids: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    trust_mode: str = "research"
    corporate_action_trust: dict[str, Any] | None = None
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)
    failed_stage: str | None = None
    error: dict[str, Any] | None = None

    def touch(self) -> None:
        """Stamp ``updated_at`` after any transition."""
        self.updated_at = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready manifest dict (enums rendered as their string values)."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunState":
        return cls.model_validate(raw)


def write_run_manifest(run_dir: Path, state: RunState) -> None:
    """Persist ``state`` as ``run_manifest.json`` under ``run_dir``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    import json

    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    (run_dir / "run_manifest.json").write_text(payload, encoding="utf-8")


def read_run_manifest(run_dir: Path) -> RunState:
    """Load ``run_manifest.json`` under ``run_dir`` as a :class:`RunState`."""
    import json

    raw = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    return RunState.from_dict(raw)


# --------------------------------------------------------------------------- #
# Artifact contract
# --------------------------------------------------------------------------- #

#: The exact set of file names a published experiment directory must contain.
#: The two JSON manifests describe the run/evaluation; the remaining files are
#: the immutable, content-hashed artifacts an ``experiment_manifest.json``
#: records.  Anything else under a published experiment is an integrity error.
REQUIRED_ARTIFACTS = frozenset(
    {
        "experiment_spec.yml",
        "config_snapshot.yml",
        "dataset_version.txt",
        "run_manifest.json",
        "experiment_manifest.json",
        "factor_results.parquet",
        "signals.parquet",
        "target_positions.parquet",
        "fills.parquet",
        "cash_ledger.parquet",
        "corporate_action_ledger.parquet",
        "daily_equity.parquet",
        "metrics.json",
        "report.html",
    }
)

#: The two self-describing manifests are never listed inside the artifacts dict
#: of ``experiment_manifest.json`` (they would be self-referential); content
#: artifacts are everything else.
_MANIFEST_NAMES = frozenset({"run_manifest.json", "experiment_manifest.json"})

#: The scenario whose fills/ledgers are the canonical published artifacts.  When
#: a spec omits ``full_cost`` the runner falls back to its last scenario.
CANONICAL_SCENARIO = "full_cost"

#: Content artifacts recorded (and sha256-hashed) by ``experiment_manifest.json``
#: -- everything except the two self-describing manifests.
MANIFESTED_ARTIFACTS = tuple(sorted(REQUIRED_ARTIFACTS - _MANIFEST_NAMES))

# --------------------------------------------------------------------------- #
# Walk-forward artifact contract
# --------------------------------------------------------------------------- #

#: Root files a formal walk-forward experiment publishes beside the classic
#: artifacts.  ``fold_schedule.json`` is written and hashed before any fold
#: executes and is never modified; ``fold_outcomes.json`` is the separate,
#: schedule-hash-bound ledger of per-fold results; the manifest binds both
#: hashes, the snapshot hashes and the stability evaluation; the stability
#: report carries the verdict and every per-fold/per-scenario metric.
WALK_FORWARD_ROOT_ARTIFACTS = (
    "fold_schedule.json",
    "fold_outcomes.json",
    "walk_forward_manifest.json",
    "stability_report.json",
)

#: The exact file set published under ``folds/<fold_id>/`` for every executed
#: fold.  ``equity.parquet`` carries the canonical columns ``trade_date``,
#: ``initial_equity`` and ``net_equity_after_cost`` (the engine's current
#: ``total_equity`` under its audit-facing name); ``metrics.json`` records
#: every declared cost scenario's fold metrics;
#: ``portfolio_construction.parquet`` is the common construction audit
#: (empty with the exact columns for the equal-weight rule).
FOLD_ARTIFACTS = (
    "fold_manifest.json",
    "signals.parquet",
    "orders.parquet",
    "fills.parquet",
    "equity.parquet",
    "daily_returns.parquet",
    "portfolio_construction.parquet",
    "metrics.json",
)

#: The scenario-local audit artifact published under
#: ``folds/<fold_id>/backtest/<scenario>/`` for the buffered risk-weighted
#: rule: one rebalance-decision row per reconciled symbol per rebalance day,
#: including the band/lot suppressions.
SCENARIO_ARTIFACTS = ("rebalance_decisions.parquet",)

#: Every admissible root artifact name across both execution pipelines.
ADMISSIBLE_ROOT_ARTIFACTS = frozenset(REQUIRED_ARTIFACTS) | frozenset(
    WALK_FORWARD_ROOT_ARTIFACTS
)


def admissible_artifact_path(name: str) -> bool:
    """True when ``name`` is a declared root file or a fold artifact path.

    The published-artifact contract is a deterministic map: declared root
    files, ``folds/<fold_id>/<declared fold artifact>`` paths and
    ``folds/<fold_id>/backtest/<scenario>/<declared scenario artifact>``
    paths (with a 64-hex content-hash fold id and a nonblank scenario name)
    may appear in an experiment manifest.
    """
    if name in ADMISSIBLE_ROOT_ARTIFACTS:
        return True
    parts = name.split("/")
    if parts[0] != "folds" or len(parts) < 3:
        return False
    fold_id, artifact = parts[1], parts[-1]
    if len(fold_id) != 64 or any(char not in "0123456789abcdef" for char in fold_id):
        return False
    if len(parts) == 3:
        return artifact in FOLD_ARTIFACTS
    if len(parts) == 5 and parts[2] == "backtest":
        scenario = parts[3]
        return bool(scenario.strip()) and artifact in SCENARIO_ARTIFACTS
    return False


def validate_artifact_paths(names: Iterable[str]) -> None:
    """Reject any artifact path outside the declared contract."""
    for name in names:
        if not admissible_artifact_path(name):
            raise ValueError(
                f"artifact path {name!r} is not a declared root file or a "
                "folds/<fold_id>/<declared-name> artifact"
            )
