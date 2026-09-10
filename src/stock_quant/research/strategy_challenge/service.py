"""Consume-before-read orchestration and immutable challenge artifacts.

``StrategyChallengeService.run`` is the only formal entry point and its
ordering is the discipline:

1. load and validate *only* the declaration, then atomically publish its
   canonical bytes under ``declarations/<challenge_id>.json`` (an existing
   path is reusable only when its bytes match; conflicting bytes under one
   id are an identity failure) -- the ``declaration_published`` event;
2. atomically consume the strategy-family/calendar holdout through the
   registry -- the ``holdout_consumed`` event fires only after the
   consumption rename;
3. only then open the baseline and challenger experiments (the loader emits
   ``challenger_opened`` on the first challenger file open), build their
   identity/metric views and evaluate the challenge;
4. publish immutable artifacts into a staging directory
   (``results/<challenge_id>/``), hash every artifact, and atomically rename;
   an existing result is reusable only when every byte matches.

On any post-declaration error the service still atomically publishes a
FAILED ``strategy_comparison.json`` with a null conclusion and the redacted
error code, and retains the consumption -- a failed challenge can never
un-consume a holdout.  The published challenger artifact set is never
rerun, retuned or altered: the service only reads it.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pandas as pd

from stock_quant.research.registry import ExperimentManifest
from stock_quant.research.strategy_challenge.compare import (
    ScenarioMetricSet,
    evaluate_challenge,
    merge_threshold_columns,
    pair_fold_metrics,
)
from stock_quant.research.strategy_challenge.models import (
    ChallengeDeclaration,
    ChallengeResult,
    canonical_challenge_json_text,
    canonical_challenge_sha256,
    compute_challenge_id,
)
from stock_quant.research.strategy_challenge.registry import (
    HoldoutAlreadyConsumed,
    HoldoutConsumption,
    HoldoutIdentityConflict,
    HoldoutRegistry,
    HoldoutRegistryError,
)
from stock_quant.research.strategy_challenge.reporting import (
    render_strategy_challenge_report,
)

#: The frozen walk-forward rebalance cadence (the strategy snapshot's
#: ``rebalance_frequency``; both challenge sides always share it).
REBALANCE_FREQUENCY = "weekly"

#: The universe identity fields that must exist inside ``metrics.json``.
_UNIVERSE_FIELDS = (
    "universe_id",
    "universe_version",
    "membership_table_sha256",
    "evidence_summary_sha256",
)


class ChallengeServiceError(RuntimeError):
    """A challenge failure that happens before a challenge id exists."""


class ExperimentLoadError(ChallengeServiceError):
    """The published baseline/challenger artifacts are missing or invalid."""


class ResultConflictError(ChallengeServiceError):
    """A published result already exists with different immutable bytes."""


@dataclass(frozen=True)
class ChallengeExperiment:
    """One published experiment's challenge identity view and metric set."""

    experiment_id: str
    manifest: dict
    metrics: ScenarioMetricSet


def factor_signal_hash_from_spec(spec: Mapping) -> str:
    """The factor/strategy input hash, excluding the portfolio-rule hash.

    Both challenge sides must match on every strategy input *except* the
    portfolio construction rule, so the hash deliberately excludes the rule
    (whose difference is the experiment under test) while covering the
    factor versions, preprocessing, random seed and dataset version.
    """
    return canonical_challenge_sha256({
        "dataset_version": spec["dataset_version"],
        "factor_versions": dict(spec["factor_versions"]),
        "preprocessing": spec["preprocessing"],
        "random_seed": spec["random_seed"],
    })


class PublishedExperimentLoader:
    """The default loader over the immutable published experiment registry.

    ``events`` receives ``"challenger_opened"`` exactly when the challenger
    artifacts are first opened, so an injected journal (the service reuses
    this list as its event sink) can audit the consume-before-read order.
    """

    def __init__(self) -> None:
        self.events: list[str] = []

    def resolve(
        self,
        project_root: Path,
        *,
        side: str,
        experiment_id: str | None,
        strategy_hash: str | None,
    ) -> str:
        """Locate the experiment identity a declaration side pins.

        The baseline is pinned by its experiment id; the challenger by its
        strategy snapshot hash *and* the declared fold schedule hash (the
        experiment id alone depends on all three snapshots, which the
        challenge must verify against the declaration instead of trusting).
        """
        if side == "baseline":
            if not experiment_id:
                raise ExperimentLoadError(
                    "the declaration pins no baseline experiment id"
                )
            return experiment_id
        if side != "challenger" or not strategy_hash:
            raise ExperimentLoadError(
                f"cannot resolve a {side!r} experiment without a pinned "
                "strategy hash"
            )
        # The first challenger artifact open happens inside the scan below:
        # emit the ordering event exactly here, before any file is read.
        self.events.append("challenger_opened")
        matches = []
        experiments_root = Path(project_root) / "data" / "experiments"
        if experiments_root.is_dir():
            for manifest_path in sorted(
                experiments_root.glob("*/experiment_manifest.json")
            ):
                manifest = _read_experiment_manifest(manifest_path)
                if (
                    manifest.strategy_snapshot_sha256 == strategy_hash
                    and manifest.fold_schedule_sha256 is not None
                ):
                    matches.append(manifest.experiment_id)
        if len(matches) != 1:
            raise ExperimentLoadError(
                f"expected exactly one published challenger experiment with "
                f"strategy snapshot {strategy_hash!r} on the declared fold "
                f"schedule, found {len(matches)}"
            )
        return matches[0]

    def load(
        self, project_root: Path, experiment_id: str, *, side: str
    ) -> ChallengeExperiment:
        """Read one published experiment into its challenge view."""
        directory = Path(project_root) / "data" / "experiments" / experiment_id
        if not directory.is_dir():
            raise ExperimentLoadError(
                f"no published experiment {experiment_id!r} exists in the "
                "experiment registry"
            )
        walk_forward = _read_json(directory / "walk_forward_manifest.json")
        stability = _read_json(directory / "stability_report.json")
        metrics = _read_json(directory / "metrics.json")
        meta = metrics.get("meta")
        if not isinstance(meta, Mapping) or not isinstance(
            meta.get("spec"), Mapping
        ):
            raise ExperimentLoadError(
                f"experiment {experiment_id!r} metrics.json carries no "
                "meta.spec identity block"
            )
        spec = meta["spec"]
        universe = meta.get("universe")
        if not isinstance(universe, Mapping) or any(
            field not in universe for field in _UNIVERSE_FIELDS
        ):
            raise ExperimentLoadError(
                f"experiment {experiment_id!r} metrics.json carries no "
                "complete universe identity block"
            )
        snapshot_hashes = walk_forward.get("snapshot_hashes")
        if not isinstance(snapshot_hashes, Mapping):
            raise ExperimentLoadError(
                f"experiment {experiment_id!r} manifest carries no snapshot "
                "hashes"
            )
        fold_statuses = stability.get("fold_statuses")
        if not isinstance(fold_statuses, list):
            raise ExperimentLoadError(
                f"experiment {experiment_id!r} stability report carries no "
                "fold statuses"
            )
        executed_fold_ids = [
            str(row["fold_id"])
            for row in fold_statuses
            if row.get("status") == "executed"
        ]
        manifest = {
            "experiment_id": experiment_id,
            "status": stability.get("research_status"),
            "stability_conclusion": stability.get("stability_conclusion"),
            "stability_policy_hash": stability.get("stability_policy_hash"),
            "data_environment_snapshot_sha256": snapshot_hashes[
                "data_environment_hash"
            ],
            "strategy_snapshot_sha256": snapshot_hashes["strategy_hash"],
            "experiment_snapshot_sha256": snapshot_hashes["experiment_hash"],
            "fold_schedule_sha256": walk_forward["fold_schedule_sha256"],
            "universe_definition": {
                field: str(universe[field]) for field in _UNIVERSE_FIELDS
            },
            "factor_signal_hash": factor_signal_hash_from_spec(spec),
            "initial_cash": float(walk_forward["initial_cash"]),
            "rebalance_frequency": REBALANCE_FREQUENCY,
            "cost_scenarios": [str(s) for s in walk_forward["declared_scenarios"]],
            "portfolio_rule_name": str(spec["portfolio_rule"]["name"]),
            "executed_fold_ids": executed_fold_ids,
            "skipped_fold_ids": [
                str(fold) for fold in stability.get("skipped_fold_ids", ())
            ],
        }
        return ChallengeExperiment(
            experiment_id=experiment_id,
            manifest=manifest,
            metrics=_scenario_metric_set(directory, stability, manifest),
        )


class StrategyChallengeService:
    """Run one one-time strategy challenge end-to-end (consume-first)."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        experiment_loader: object | None = None,
        registry: HoldoutRegistry | None = None,
        event_sink: object | None = None,
    ) -> None:
        self._project_root = Path(project_root)
        self._loader = experiment_loader or PublishedExperimentLoader()
        self._registry = registry or HoldoutRegistry(self._project_root)
        sink = event_sink
        if sink is None:
            sink = getattr(self._loader, "events", None)
        self.events: list = sink if isinstance(sink, list) else []

    @property
    def challenges_root(self) -> Path:
        return self._project_root / "data" / "strategy_challenges"

    def declaration_path(self, challenge_id: str) -> Path:
        return self.challenges_root / "declarations" / f"{challenge_id}.json"

    def consumption_path(self, challenge_id: str) -> Path:
        return self.challenges_root / "consumptions" / f"{challenge_id}.json"

    def result_path(self, challenge_id: str) -> Path:
        return self.challenges_root / "results" / challenge_id

    def run(self, declaration_path: str | Path) -> ChallengeResult:
        """Load, publish, consume, read, evaluate and publish immutably."""
        declaration = self._load_declaration(Path(declaration_path))
        challenge_id = compute_challenge_id(declaration)
        declaration_bytes = canonical_challenge_json_text(
            declaration.model_dump(mode="json")
        ).encode("utf-8")
        _write_atomic(self.declaration_path(challenge_id), declaration_bytes)
        self._emit("declaration_published")
        try:
            consumption = self._registry.consume(
                declaration, event_sink=self.events
            )
        except (HoldoutAlreadyConsumed, HoldoutIdentityConflict,
                HoldoutRegistryError) as error:
            return self._publish_failure(
                challenge_id,
                declaration,
                declaration_bytes,
                "HOLDOUT_CONSUMPTION_REFUSED",
                str(error),
            )
        try:
            return self._evaluate_and_publish(
                challenge_id, declaration, declaration_bytes, consumption
            )
        except ExperimentLoadError as error:
            return self._publish_failure(
                challenge_id,
                declaration,
                declaration_bytes,
                "EXPERIMENT_LOAD_ERROR",
                _redacted(str(error)),
            )

    # -- internals ---------------------------------------------------------

    def _load_declaration(self, path: Path) -> ChallengeDeclaration:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ChallengeServiceError(
                f"{path} is not a readable declaration file: {error}"
            ) from error
        if not isinstance(payload, Mapping):
            raise ChallengeServiceError(
                f"{path} must contain a JSON declaration object"
            )
        try:
            return ChallengeDeclaration.model_validate(payload)
        except Exception as error:  # noqa: BLE001 - surfaced to the operator
            raise ChallengeServiceError(
                f"{path} is not a valid ChallengeDeclaration: {error}"
            ) from error

    def _evaluate_and_publish(
        self,
        challenge_id: str,
        declaration: ChallengeDeclaration,
        declaration_bytes: bytes,
        consumption: HoldoutConsumption,
    ) -> ChallengeResult:
        baseline_id = _call_loader(
            self._loader, "resolve",
            self._project_root,
            side="baseline",
            experiment_id=declaration.baseline_experiment_id,
            strategy_hash=None,
        )
        baseline = _call_loader(
            self._loader, "load",
            self._project_root, baseline_id, side="baseline",
        )
        challenger_id = _call_loader(
            self._loader, "resolve",
            self._project_root,
            side="challenger",
            experiment_id=None,
            strategy_hash=declaration.challenger_strategy_hash,
        )
        challenger = _call_loader(
            self._loader, "load",
            self._project_root, challenger_id, side="challenger",
        )
        result = evaluate_challenge(
            declaration=declaration,
            consumption=consumption,
            baseline_manifest=baseline.manifest,
            challenger_manifest=challenger.manifest,
            baseline_metrics=baseline.metrics,
            challenger_metrics=challenger.metrics,
        )
        self._publish_results(
            challenge_id=challenge_id,
            declaration=declaration,
            declaration_bytes=declaration_bytes,
            consumption=consumption,
            result=result,
            baseline=baseline if result.status == "COMPLETED" else None,
            challenger=challenger if result.status == "COMPLETED" else None,
        )
        return result

    def _publish_failure(
        self,
        challenge_id: str,
        declaration: ChallengeDeclaration,
        declaration_bytes: bytes,
        error_code: str,
        detail: str,
    ) -> ChallengeResult:
        """Terminal failure: publish null-conclusion comparison JSON."""
        result = ChallengeResult.model_validate({
            "challenge_id": challenge_id,
            "strategy_family": declaration.strategy_family,
            "baseline_experiment_id": declaration.baseline_experiment_id,
            "status": "FAILED",
            "conclusion": None,
            "error_code": error_code,
            "reasons": (detail,),
        })
        self._publish_results(
            challenge_id=challenge_id,
            declaration=declaration,
            declaration_bytes=declaration_bytes,
            consumption=None,
            result=result,
            baseline=None,
            challenger=None,
        )
        return result

    def _publish_results(
        self,
        *,
        challenge_id: str,
        declaration: ChallengeDeclaration,
        declaration_bytes: bytes,
        consumption: HoldoutConsumption | None,
        result: ChallengeResult,
        baseline: ChallengeExperiment | None,
        challenger: ChallengeExperiment | None,
    ) -> None:
        """Stage every artifact, hash them, and atomically rename."""
        results_root = self.challenges_root / "results"
        staging = results_root / f".{challenge_id}.staging.{uuid.uuid4().hex}"
        final = results_root / challenge_id
        staged: dict[str, bytes] = {
            "strategy_challenge.json": declaration_bytes,
        }
        consumption_bytes = (
            canonical_challenge_json_text(
                consumption.model_dump(mode="json")
            ).encode("utf-8")
            if consumption is not None
            else None
        )
        if consumption_bytes is not None:
            staged["holdout_consumption.json"] = consumption_bytes
        if result.status == "COMPLETED":
            pairs = merge_threshold_columns(
                pair_fold_metrics(
                    baseline.metrics.folds,
                    challenger.metrics.folds,
                    declaration.comparison_policy,
                ),
                result,
            )
            staged["paired_fold_metrics.parquet"] = _parquet_bytes(pairs)
            report_html = render_strategy_challenge_report(
                declaration=declaration,
                consumption=consumption,
                result=result,
                baseline_manifest=baseline.manifest,
                challenger_manifest=challenger.manifest,
                pairs=pairs,
            ).encode("utf-8")
            staged["strategy_comparison_report.html"] = report_html
        comparison = _comparison_payload(
            declaration=declaration,
            result=result,
            consumption=consumption,
            baseline=baseline,
            challenger=challenger,
            staged=staged,
        )
        staged["strategy_comparison.json"] = canonical_challenge_json_text(
            comparison
        ).encode("utf-8")
        for name, payload in staged.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        if final.exists():
            self._assert_reusable(final, staging, challenge_id)
            _remove_directory(staging)
            return
        results_root.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)
        _fsync_directory(results_root)

    def _assert_reusable(
        self, final: Path, staging: Path, challenge_id: str
    ) -> None:
        """An existing result is reusable only when every byte matches."""
        existing = {
            path.relative_to(final).as_posix() for path in final.rglob("*")
            if path.is_file()
        }
        staged = {
            path.relative_to(staging).as_posix() for path in staging.rglob("*")
            if path.is_file()
        }
        if existing != staged:
            raise ResultConflictError(
                f"published result {challenge_id} already exists with a "
                "different artifact set; immutable results are never "
                f"overwritten (existing {sorted(existing)}, staged "
                f"{sorted(staged)})"
            )
        for relative in sorted(existing):
            if (final / relative).read_bytes() != (
                staging / relative
            ).read_bytes():
                raise ResultConflictError(
                    f"published result {challenge_id} already exists with "
                    f"different bytes in {relative!r}; immutable results "
                    "are never overwritten"
                )

    def _emit(self, event: str) -> None:
        self.events.append(event)


# --------------------------------------------------------------------------- #
# Loading helpers
# --------------------------------------------------------------------------- #


def _scenario_metric_set(
    directory: Path, stability: Mapping, manifest: Mapping
) -> ScenarioMetricSet:
    """The per-fold and aggregate metric frames of a published experiment."""
    fold_rows = []
    for record in stability.get("fold_metrics", ()):  # executed folds only
        fold_rows.append({
            "fold_id": str(record["fold_id"]),
            "scenario": str(record["scenario"]),
            "fold_calendar_return": record.get("fold_calendar_return"),
            "abs_max_drawdown": abs(float(record.get("per_fold_max_drawdown")
                                          or 0.0)),
            "turnover": record.get("turnover"),
            "explicit_cost_ratio": record.get("explicit_cost_ratio"),
            "reject_rate": record.get("reject_rate"),
            "invested_exposure": _fold_invested_exposure(
                directory,
                str(record["fold_id"]),
                str(record["scenario"]),
            ),
        })
    aggregate_rows = []
    for record in stability.get("scenario_aggregates", ()):
        aggregate_rows.append({
            "scenario": str(record["scenario"]),
            "aggregate_sharpe": record.get("sharpe_zero_rf"),
            "aggregate_annualized_return": record.get("annualized_return"),
        })
    return ScenarioMetricSet(
        folds=pd.DataFrame(
            fold_rows,
            columns=[
                "fold_id", "scenario", "fold_calendar_return",
                "abs_max_drawdown", "turnover", "explicit_cost_ratio",
                "reject_rate", "invested_exposure",
            ],
        ),
        aggregates=pd.DataFrame(
            aggregate_rows,
            columns=["scenario", "aggregate_sharpe",
                     "aggregate_annualized_return"],
        ),
    )


def _fold_invested_exposure(
    directory: Path, fold_id: str, scenario: str
) -> float | None:
    """Mean daily ``market_value / net_equity_after_cost`` of one fold."""
    path = directory / "folds" / fold_id / "backtest" / scenario / "equity.parquet"
    if not path.is_file():
        raise ExperimentLoadError(
            f"the published fold scenario equity artifact for {fold_id}"
            f"/{scenario} is missing from the experiment"
        )
    equity = pd.read_parquet(path)
    for column in ("market_value", "net_equity_after_cost"):
        if column not in equity.columns:
            raise ExperimentLoadError(
                f"the published equity artifact for {fold_id}/{scenario} "
                f"lacks the {column} column"
            )
    market_value = pd.to_numeric(equity["market_value"], errors="coerce")
    net_equity = pd.to_numeric(
        equity["net_equity_after_cost"], errors="coerce"
    )
    mask = net_equity > 0
    ratios = (market_value[mask] / net_equity[mask]).replace(
        [math.inf, -math.inf], math.nan
    ).dropna()
    if ratios.empty:
        return None
    return float(ratios.mean())


def _read_experiment_manifest(path: Path) -> ExperimentManifest:
    from pydantic import ValidationError

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentLoadError(
            f"cannot read the experiment manifest beside {path.name}"
        ) from error
    try:
        return ExperimentManifest.model_validate(raw)
    except ValidationError as error:
        raise ExperimentLoadError(
            f"the experiment manifest beside {path.name} is invalid: {error}"
        ) from error


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise ExperimentLoadError(f"missing published artifact {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExperimentLoadError(
            f"unreadable published artifact {path.name}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise ExperimentLoadError(
            f"published artifact {path.name} must be a JSON object"
        )
    return payload


def _call_loader(loader: object, method: str, *args, **kwargs):
    """Duck-typed two-method loader protocol (default or injected)."""
    attribute = getattr(loader, method, None)
    if not callable(attribute):
        raise ChallengeServiceError(
            "the experiment loader must implement resolve() and load()"
        )
    return attribute(*args, **kwargs)


# --------------------------------------------------------------------------- #
# Artifact helpers
# --------------------------------------------------------------------------- #


def _comparison_payload(
    *,
    declaration: ChallengeDeclaration,
    result: ChallengeResult,
    consumption: HoldoutConsumption | None,
    baseline: ChallengeExperiment | None,
    challenger: ChallengeExperiment | None,
    staged: Mapping[str, bytes],
) -> dict:
    """The immutable comparison JSON: identity, hashes, consumption, result."""
    import hashlib

    def snapshot_view(experiment: ChallengeExperiment | None) -> dict | None:
        if experiment is None:
            return None
        manifest = experiment.manifest
        return {
            "strategy_snapshot_sha256": manifest["strategy_snapshot_sha256"],
            "experiment_snapshot_sha256": manifest[
                "experiment_snapshot_sha256"
            ],
            "data_environment_snapshot_sha256": manifest[
                "data_environment_snapshot_sha256"
            ],
        }

    payloads = {
        name: hashlib.sha256(payload).hexdigest()
        for name, payload in sorted(staged.items())
    }
    return {
        "challenge_id": result.challenge_id,
        "status": result.status,
        "conclusion": result.conclusion,
        "declaration": declaration.model_dump(mode="json"),
        "comparison_policy_hash": declaration.comparison_policy_hash,
        "fold_schedule_hash": declaration.fold_schedule_hash,
        "universe_definition": declaration.universe_definition.model_dump(
            mode="json"
        ),
        "holdout_consumption": (
            consumption.model_dump(mode="json")
            if consumption is not None
            else None
        ),
        "baseline_experiment_id": (
            baseline.experiment_id if baseline is not None
            else declaration.baseline_experiment_id
        ),
        "challenger_experiment_id": (
            challenger.experiment_id if challenger is not None else None
        ),
        "snapshot_hashes": {
            "baseline": snapshot_view(baseline),
            "challenger": snapshot_view(challenger),
        },
        "result": result.model_dump(mode="json"),
        "artifacts": payloads,
    }


def _parquet_bytes(frame: pd.DataFrame) -> bytes:
    import io

    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def _write_atomic(path: Path, data: bytes) -> None:
    """Temporary sibling, ``fsync``, atomic rename (never overwrite)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ChallengeServiceError(
                f"{path} already exists with different bytes; a published "
                "declaration is immutable and never overwritten"
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


def _remove_directory(directory: Path) -> None:
    import shutil

    shutil.rmtree(directory, ignore_errors=True)


def _redacted(message: str) -> str:
    """Drop anything that smells like a filesystem path from the message."""
    cleaned = str(message)
    return " ".join(
        part for part in cleaned.split() if "/" not in part and "\\" not in part
    ) or cleaned
