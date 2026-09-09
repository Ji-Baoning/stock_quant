"""Official research run orchestration with resume and immutable publish (Task 11).

:class:`ResearchRunner` turns one frozen :class:`ExperimentSpec` into a complete,
reproducible experiment.  It resolves a ``CURRENT`` dataset/universe request to
an explicit version *exactly once* (``ExperimentSpec.freeze``) before any market
data is read, then runs the pinned pipeline: a factor dataset adapter, the
specified factor, a weekly top-N equal-weight portfolio, one T+1 backtest per
cost scenario and analytics/report generation.  Results are staged under
``data/runs/<run_id>/`` and only an evaluated, manifest-verified publish is
renamed into ``data/experiments/<experiment_id>/`` by the registry.

The workspace is resumable: every stage records the run input digest plus the
sha256 of each output it produced in ``.stages.json``; re-running the same
``run_id`` skips exactly the stages whose recorded digest and artifacts still
match, so an interrupted official run picks up where it left off instead of
redoing work.  Failures record the failed stage, exception class, redacted
message and a retriable flag in ``run_manifest.json``, never touch data
``CURRENT`` and never create a partial experiment directory.

Analytics, report and evaluation are small injected protocols (with real
default implementations below) so the engineering gate is decoupled from the
published metrics.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

import pandas as pd
import yaml

from stock_quant.backtest.costs import CostModel
from stock_quant.backtest.engine import BacktestEngine, BacktestRequest, OrderDay
from stock_quant.backtest.models import BUY, SELL, Fill, Order
from stock_quant.backtest.rebalancer import AccountAwareWeeklyRebalancer
from stock_quant.config import load_project_config
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.corporate_action_coverage import CoverageReason
from stock_quant.data_model.dataset import (
    DatasetContext,
    DatasetPublisher,
    DatasetReader,
)
from stock_quant.data_model.security_master import missing_master_coverage_symbols
from stock_quant.data_model.trading_rules import TradingRuleBook
from stock_quant.data_model.universe import Universe
from stock_quant.factors.base import Factor, FactorContext
from stock_quant.factors.models import FactorResult
from stock_quant.logging import StructuredLogger, redact_text
from stock_quant.portfolio.equal_weight import TopNEqualWeight
from stock_quant.research.models import (
    CANONICAL_SCENARIO,
    MANIFESTED_ARTIFACTS,
    DataStage,
    Evaluation,
    ExperimentEvaluation,
    ResearchRunFailed,
    RunState,
    RunStatus,
    read_run_manifest,
    write_run_manifest,
)
from stock_quant.research.reconcile import (
    STATUS_FILLED,
    STATUS_PARTIAL,
    STATUS_REJECTED,
    reconcile_orders,
)
from stock_quant.research.registry import (
    ExperimentIdentity,
    ExperimentRegistry,
    PublishedExperiment,
)
from stock_quant.research.spec import ExperimentSpec, load_experiment_spec
from stock_quant.research.trust import (
    DataTrustMode,
    evaluate_corporate_action_trust,
)

_CURRENT = "CURRENT"

#: Coarse pipeline stage labels, in dependency order.  The stage record is
#: also the resume unit: labels map to the shared ``DataStage`` vocabulary.
_STAGE_LABELS = ("pin", "factor", "portfolio", "backtest", "report")

_LABEL_TO_DATASTAGE = {
    "pin": DataStage.PUBLISHED,
    "factor": DataStage.FACTOR_READY,
    "portfolio": DataStage.FACTOR_READY,
    "backtest": DataStage.BACKTESTED,
    "report": DataStage.REPORTED,
}

_STAGES_FILE = ".stages.json"
_RUN_MANIFEST = "run_manifest.json"

#: Fraction of starting cash the weekly portfolio sizes its target notional
#: from.  Sizing below 100% leaves a standing cash buffer so every order in the
#: precomputed, signal-day-fixed buy list can be fully filled at the execution
#: open without re-optimising quantities against a later price.
_SIZING_FRACTION = 0.85

#: Bar quality annotation fed to factors and the backtest engine for the
#: phase-one authoritative path.  Design §13.5: the publication gate must not
#: depend on strategy inputs, and cross-source stock-close disagreement above
#: tolerance is a non-required, report-only signal -- the Tushare primary close
#: series is authoritative for factors and backtests.  Cross-source close ERROR
#: is therefore never fed here: it is surfaced only in the quality report.  The
#: value stays the constant "INFO" (no ERROR bars reach factors/engine); this
#: name documents the ruling instead of a bare literal.
QUALITY_SEVERITY_AUTHORITATIVE = "INFO"

_DEPENDENCY_VERSIONS = ("pandas", "numpy", "pyarrow", "duckdb", "pydantic", "yaml")


# --------------------------------------------------------------------------- #
# Injected protocol roles (with small real defaults below)
# --------------------------------------------------------------------------- #


class Analytics(Protocol):
    """Reduces scenario ledgers into a JSON-ready metrics mapping."""

    def compute(self, metrics_input: "AnalyticsInput") -> dict[str, object]: ...


class Report(Protocol):
    """Renders the metrics mapping into the deterministic ``report.html``."""

    def render(self, metrics: dict[str, object]) -> str: ...


class Evaluator(Protocol):
    """Records an engineering acceptance decision independent of performance."""

    def evaluate(self, metrics: dict[str, object]) -> Evaluation: ...


@dataclass(frozen=True)
class AnalyticsInput:
    """Everything the analytics/report step may read (all inputs are pinned)."""

    run_dir: Path
    experiment_id: str
    run_id: str
    spec: ExperimentSpec
    dataset_version: str
    universe_version: str
    code_commit: str
    initial_cash: float
    sizing_capital: float
    scenarios: tuple[str, ...]
    canonical_scenario: str


def _analytics_callable(value: object) -> Callable[["AnalyticsInput"], dict]:
    if value is None:
        return _DefaultAnalytics().compute
    if callable(value):
        return value
    return getattr(value, "compute")


def _report_callable(value: object) -> Callable[[dict], str]:
    if value is None:
        return _DefaultReport().render
    if callable(value):
        return value
    return getattr(value, "render")


def _evaluator_callable(value: object) -> Callable[[dict], Evaluation]:
    if value is None:
        return _DefaultEvaluator().evaluate
    if callable(value):
        return value
    return getattr(value, "evaluate")


def _factor_provider_default() -> Mapping[str, Factor]:
    # Imported lazily so the runner stays decoupled until a provider is needed.
    from stock_quant.factors.momentum import Momentum60

    factor = Momentum60()
    return {factor.name: factor}


# --------------------------------------------------------------------------- #
# Default analytics / report / evaluator
# --------------------------------------------------------------------------- #


class _DefaultAnalytics:
    """Per-scenario summary over the completed backtest ledgers.

    Each scenario records how its own submitted orders -- the account-aware
    rebalance plan generated from that scenario's realized account state --
    reconciled against execution.  ``order_diffs.parquet`` is the
    per-``order_id`` submitted-order view (counts and quantities), while
    ``rejections.parquet`` keeps the flow-level view (one row per rejection
    record).  ``plan_diverged`` is true exactly when ``unfilled_quantity > 0``,
    so a scenario whose submission was not fully executed is self-auditing
    from ``metrics.json`` alone.
    """

    def compute(self, metrics_input: AnalyticsInput) -> dict[str, object]:
        scenarios: dict[str, object] = {}
        for scenario in metrics_input.scenarios:
            directory = metrics_input.run_dir / "backtest" / scenario
            equity = pd.read_parquet(directory / "daily_equity.parquet")
            fills = pd.read_parquet(directory / "fills.parquet")
            rejections = pd.read_parquet(directory / "rejections.parquet")
            diffs = pd.read_parquet(directory / "order_diffs.parquet")
            start = float(equity["total_equity"].iloc[0])
            end = float(equity["total_equity"].iloc[-1])
            n_rejections = int(len(rejections))
            planned_order_count = int(len(diffs))
            planned_quantity = (
                int(diffs["planned_quantity"].sum()) if planned_order_count else 0
            )
            filled_quantity = (
                int(diffs["filled_quantity"].sum()) if planned_order_count else 0
            )
            unfilled_quantity = planned_quantity - filled_quantity
            status_counts = (
                diffs["status"].value_counts().to_dict() if planned_order_count else {}
            )
            filled_order_count = int(status_counts.get(STATUS_FILLED, 0))
            partial_order_count = int(status_counts.get(STATUS_PARTIAL, 0))
            rejected_order_count = int(status_counts.get(STATUS_REJECTED, 0))
            unfilled = (
                diffs[diffs["status"] != STATUS_FILLED]
                if planned_order_count
                else diffs
            )
            unfilled_reason_counts = {
                str(reason): int(count)
                for reason, count in (
                    unfilled["reason"].value_counts().items()
                    if planned_order_count and len(unfilled)
                    else []
                )
            }
            scenarios[scenario] = {
                "periods": int(len(equity)),
                "start_date": _date_text(equity["trade_date"].iloc[0]),
                "end_date": _date_text(equity["trade_date"].iloc[-1]),
                "start_equity": round(start, 2),
                "end_equity": round(end, 2),
                "total_return": round(end / start - 1.0, 8) if start > 0 else None,
                "end_cash": round(float(equity["cash"].iloc[-1]), 2),
                "n_fills": int(len(fills)),
                "commission": _round2(float(fills["commission"].sum()))
                if len(fills)
                else 0.0,
                "stamp_tax": _round2(float(fills["stamp_tax"].sum()))
                if len(fills)
                else 0.0,
                # Flow level (rejections.parquet, one row per rejection record).
                "n_rejections": n_rejections,
                "rejected_quantity": int(rejections["rejected_quantity"].sum())
                if n_rejections
                else 0,
                "rejections_by_reason": {
                    str(reason): int(count)
                    for reason, count in (
                        rejections["reason"].value_counts().items()
                        if n_rejections
                        else []
                    )
                },
                # Order level (order_diffs.parquet, one row per planned order).
                "planned_order_count": planned_order_count,
                "planned_quantity": planned_quantity,
                "filled_quantity": filled_quantity,
                "unfilled_quantity": unfilled_quantity,
                "filled_order_count": filled_order_count,
                "partial_order_count": partial_order_count,
                "rejected_order_count": rejected_order_count,
                "unfilled_reason_counts": unfilled_reason_counts,
                "plan_diverged": bool(unfilled_quantity > 0),
            }
        return {"scenarios": scenarios}


class _DefaultReport:
    """A small deterministic HTML report over the metrics mapping."""

    def render(self, metrics: dict[str, object]) -> str:
        rows: list[str] = []
        scenarios = metrics.get("scenarios")
        if isinstance(scenarios, Mapping):
            for name, summary in scenarios.items():
                rows.append(
                    "<tr>"
                    f"<td>{_html(name)}</td>"
                    f"<td>{summary.get('start_date')}</td>"
                    f"<td>{summary.get('end_date')}</td>"
                    f"<td>{summary.get('periods')}</td>"
                    f"<td>{summary.get('end_equity')}</td>"
                    f"<td>{summary.get('total_return')}</td>"
                    "</tr>"
                )
        meta = metrics.get("meta")
        experiment_id = str(meta.get("experiment_id")) if isinstance(meta, Mapping) \
            else ""
        evaluation = metrics.get("evaluation")
        status = ""
        reason = ""
        if isinstance(evaluation, Mapping):
            status = str(evaluation.get("status"))
            reason = _html(str(evaluation.get("reason")))
        return (
            "<!doctype html>\n<html><head><meta charset='utf-8'>"
            "<title>experiment report</title></head><body>"
            f"<h1>{_html(experiment_id)}</h1>"
            f"<p>evaluation: {_html(status)}</p><p>{reason}</p>"
            "<table><thead><tr><th>scenario</th><th>start</th><th>end</th>"
            "<th>periods</th><th>end_equity</th><th>total_return</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></body></html>\n"
        )


class _DefaultEvaluator:
    """Engineering acceptance only: complete, positive-terminal scenarios."""

    def evaluate(self, metrics: dict[str, object]) -> Evaluation:
        scenarios = metrics.get("scenarios")
        if not isinstance(scenarios, Mapping) or not scenarios:
            return Evaluation(
                ExperimentEvaluation.REJECTED,
                "no scenario metrics produced; backtests did not complete",
            )
        completed = all(
            isinstance(summary, Mapping)
            and int(summary.get("periods", 0)) > 0
            and float(summary.get("end_equity", 0.0)) > 0.0
            for summary in scenarios.values()
        )
        if not completed:
            return Evaluation(
                ExperimentEvaluation.REJECTED,
                "one or more cost scenarios did not reach a positive terminal "
                "equity",
            )
        reason = (
            f"engineering_gate: {len(scenarios)}/{len(scenarios)} cost scenarios "
            "completed with positive terminal equity; performance reviewed in "
            "metrics.json"
        )
        return Evaluation(ExperimentEvaluation.ACCEPTED, reason)


# --------------------------------------------------------------------------- #
# The research runner
# --------------------------------------------------------------------------- #


class ResearchRunner:
    """Runs one frozen spec end-to-end with a resumable, auditable workspace.

    ``project_root`` owns the data tree (``data/runs``, ``data/experiments`` and
    the standardized datasets); ``config_root`` defaults to it and holds the
    ``configs/`` tree (costs, rules, universe and experiment specs).  Tests pin
    a temporary ``project_root`` while pointing ``config_root`` at the
    repository so a synthetic dataset drives the whole pipeline.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        config_root: str | Path | None = None,
        factor_provider: Callable[[], Mapping[str, Factor]] | None = None,
        analytics: object | None = None,
        report: object | None = None,
        evaluator: object | None = None,
        stage_observer=None,
        secrets: Sequence[object] = (),
    ) -> None:
        self._project_root = Path(project_root)
        self._config_root = Path(config_root) if config_root is not None \
            else self._project_root
        self._factor_provider = factor_provider or _factor_provider_default
        self._analytics = _analytics_callable(analytics)
        self._report = _report_callable(report)
        self._evaluator = _evaluator_callable(evaluator)
        self._observer = stage_observer
        self._secrets = tuple(secrets)
        # ``self._logger`` is the per-run file-backed logger created in
        # ``_begin`` (it must write under ``data/runs/<run_id>/``); there is no
        # injectable logger parameter because it would silently be discarded.
        self._logger: StructuredLogger | None = None

        self._project_config = load_project_config(self._config_root)
        self._rule_book = TradingRuleBook.from_yaml(
            self._config_root / "configs" / "trading_rules.yml"
        )
        self._registry = ExperimentRegistry(self._project_root)
        self._run_dir: Path | None = None
        self._run_id: str | None = None
        self._active_stage: str | None = None
        self._context: DatasetContext | None = None
        self._digest: str = ""

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def run(
        self,
        spec_path: str | Path,
        *,
        stage_observer=None,
        trust_mode: DataTrustMode | str = DataTrustMode.RESEARCH,
    ) -> PublishedExperiment:
        """Freeze, run and publish one experiment spec.

        ``stage_observer(stage, state)`` is called after each fine stage
        completes; an observer that raises aborts the run into a FAILED state
        (useful for injected-failure tests).  ``trust_mode`` selects the
        corporate-action evidence bar: ``research`` (the default) raises before
        any backtest when the pinned coverage is not trusted, while
        ``engineering`` still runs the full pipeline and records the decision as
        an UNTRUSTED metrics evaluation that can never publish an ACCEPTED
        experiment.  ``spec_path`` is resolved against ``config_root`` unless
        absolute.
        """
        observer = self._observer if stage_observer is None else stage_observer
        mode = trust_mode if isinstance(trust_mode, DataTrustMode) \
            else DataTrustMode(trust_mode)
        frozen = self._freeze(spec_path, trust_mode=mode)
        state = self._begin(frozen)
        try:
            self._active_stage = None
            self._pipeline(state, frozen, observer)
            published = self._publish(state, frozen)
            state.status = RunStatus.COMPLETED
            state.stage = DataStage.REPORTED
            state.touch()
            # A resumed-fully run skipped the backtest gate that records the
            # decision, so recompute it here to keep the manifest complete.
            if state.corporate_action_trust is None:
                self._record_run_trust(state, frozen)
            write_run_manifest(self._run_dir, state)
            self._logger.info(
                "experiment published",
                run_id=self._run_id,
                stage="publish",
                event="published",
            )
            return published
        except ResearchRunFailed:
            self._close_context()
            raise
        except Exception as error:  # noqa: BLE001 - any failure is audited
            self._close_context()
            self._fail(state, error)
            raise ResearchRunFailed(
                f"research run {self._run_id} failed at stage "
                f"{self._active_stage}: {redact_text(error, self._secrets)}",
                run_id=self._run_id,
                failed_stage=self._active_stage,
                retriable=not isinstance(error, (TypeError, ValueError)),
            ) from error

    def latest_run_manifest(self) -> RunState:
        """The persisted run manifest of the most recent :meth:`run`."""
        if self._run_dir is None:
            raise FileNotFoundError(
                "no research run has been started by this runner yet"
            )
        return read_run_manifest(self._run_dir)

    def partial_experiment_exists(self) -> bool:
        """True when the current run's publish staging never reached an experiment.

        A failed run must never leave a half-formed experiment directory: on a
        successful publish the ``data/runs/<run_id>/publish/`` staging is
        renamed into ``data/experiments/<id>`` (an identical-id re-publish
        leaves the staging in place but the experiment already exists).  A
        publish that was interrupted between staging and the registry rename
        leaves ``publish/`` behind with no matching published experiment -- the
        only state this reports as a partial publication.  Other experiment
        directories in the project are irrelevant.
        """
        if self._run_dir is None:
            return False
        publish_dir = self._run_dir / "publish"
        if not publish_dir.is_dir():
            return False
        manifest_path = publish_dir / "experiment_manifest.json"
        if not manifest_path.is_file():
            return True
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        experiment_id = raw.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            return True
        return not (self._registry.experiments_root / experiment_id).is_dir()

    @property
    def run_id(self) -> str | None:
        """The most recent ``run_<experiment_id>``, or ``None``."""
        return self._run_id

    # ------------------------------------------------------------------ #
    # Freeze / identity / workspace
    # ------------------------------------------------------------------ #

    def _freeze(
        self, spec_path: str | Path, *, trust_mode: DataTrustMode
    ) -> ExperimentSpec:
        path = Path(spec_path)
        if not path.is_absolute():
            path = self._config_root / path
        spec = load_experiment_spec(path)
        dataset_version = spec.dataset_version
        if dataset_version == _CURRENT:
            dataset_version = DatasetPublisher(self._project_root).current().version
        universe_version = spec.universe_version
        if universe_version == _CURRENT:
            universe = Universe.from_yaml(self._config_root / "configs"
                                          / "universe.yml")
            universe_version = universe.version
        code_commit = self._detect_code_commit() or spec.code_commit
        return spec.freeze(
            dataset_version=dataset_version,
            universe_version=universe_version,
            code_commit=code_commit,
            trust_mode=trust_mode,
        )

    def _detect_code_commit(self) -> str | None:
        """The repository HEAD when ``config_root`` is inside a git work tree."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self._config_root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except Exception:  # noqa: BLE001 - a non-git root simply stays unversioned
            return None
        commit = result.stdout.strip()
        return commit or None

    def _begin(self, frozen: ExperimentSpec) -> RunState:
        identity = ExperimentIdentity.of(frozen)
        run_id = f"run_{identity.experiment_id}"
        run_dir = self._project_root / "data" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._run_dir = run_dir
        self._logger = StructuredLogger(
            run_dir / ".run.log.jsonl",
            terminal=False,
            secrets=self._secrets,
        )
        state = RunState(
            run_id=run_id,
            experiment_id=identity.experiment_id,
            status=RunStatus.RUNNING,
            stage=DataStage.CREATED,
            dataset_version=frozen.dataset_version,
            universe_version=frozen.universe_version,
            code_commit=frozen.code_commit,
            factor_versions=dict(frozen.factor_versions),
            cost_scenarios=list(frozen.cost_scenarios),
            random_seed=frozen.random_seed,
            parent_experiment_ids=list(frozen.parent_experiment_ids),
            agent_id=frozen.agent_id,
            trust_mode=frozen.trust_mode.value,
        )
        write_run_manifest(run_dir, state)
        self._digest = self._run_digest(frozen)
        return state

    def _run_digest(self, frozen: ExperimentSpec) -> str:
        """One deterministic sha over every input the stages consume."""
        config_hashes = {
            name: _sha256_file(self._config_root / "configs" / name)
            for name in (
                "project.yml",
                "costs.yml",
                "trading_rules.yml",
                "universe.yml",
            )
        }
        payload = {
            "frozen_spec": frozen.model_dump(mode="json"),
            "config_file_hashes": config_hashes,
            "python_version": platform.python_version(),
            "dependency_versions": self._dependency_versions(),
        }
        text = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _dependency_versions(self) -> dict[str, str]:
        versions: dict[str, str] = {}
        for name in _DEPENDENCY_VERSIONS:
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:  # pragma: no cover
                versions[name] = "unknown"
        return versions

    # ------------------------------------------------------------------ #
    # Stage pipeline
    # ------------------------------------------------------------------ #

    def _pipeline(
        self,
        state: RunState,
        frozen: ExperimentSpec,
        observer,
    ) -> None:
        # Pipeline-level pins that every stage may read.  Computed up front (not
        # as a side effect of the pin stage) so a resumed run whose pin outputs
        # are already intact still has the universe symbols and pinned version.
        self._frozen_version = frozen.dataset_version
        self._universe_symbols = self._load_universe_symbols()
        self._enforce_research_master_evidence(frozen)
        for label in _STAGE_LABELS:
            self._active_stage = label
            producer = {
                "pin": lambda: self._produce_pin(frozen),
                "factor": lambda: self._produce_factor(state, frozen),
                "portfolio": lambda: self._produce_portfolio(state, frozen),
                "backtest": lambda: self._produce_backtest(state, frozen),
                "report": lambda: self._produce_report(state, frozen),
            }[label]
            self._run_stage(label, _LABEL_TO_DATASTAGE[label], state, producer,
                            observer)
        self._active_stage = None

    def _run_stage(self, label, target_stage, state, producer, observer) -> None:
        records = self._stage_records()
        record = records.get(label)
        outputs = record.get("outputs", {}) if record else {}
        if record and record.get("input_hash") == self._digest and \
                self._outputs_intact(outputs):
            self._logger.info(
                f"resume: stage {label} already complete; skipping",
                run_id=self._run_id, stage=label, event="resume",
            )
            state.stage = target_stage
            write_run_manifest(self._run_dir, state)
            return
        self._logger.info(
            f"running stage {label}", run_id=self._run_id, stage=label,
            event="stage_start",
        )
        produced = producer()  # writes files; raises on failure
        state.stage = target_stage
        state.touch()
        write_run_manifest(self._run_dir, state)
        if observer is not None:
            observer(label, state.model_copy(deep=True))
        self._record_stage(label, self._digest, produced)
        self._logger.info(
            f"stage {label} complete", run_id=self._run_id, stage=label,
            event="stage_complete",
        )

    def _stage_records(self) -> dict[str, dict[str, object]]:
        path = self._run_dir / _STAGES_FILE
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _record_stage(
        self, label: str, digest: str, outputs: Mapping[str, str]
    ) -> None:
        records = self._stage_records()
        records[label] = {
            "input_hash": digest,
            "outputs": dict(sorted(outputs.items())),
        }
        (self._run_dir / _STAGES_FILE).write_text(
            json.dumps(records, indent=2, sort_keys=True), encoding="utf-8"
        )

    def _outputs_intact(self, outputs: Mapping[str, str]) -> bool:
        for relative, expected in outputs.items():
            path = self._run_dir / relative
            if not path.is_file() or _sha256_file(path) != expected:
                return False
        return True

    def _publish(self, state: RunState, frozen: ExperimentSpec) -> PublishedExperiment:
        """Copy the canonical content into ``publish/`` and call the registry."""
        publish_dir = self._run_dir / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True)
        for name in MANIFESTED_ARTIFACTS:
            source = self._run_dir / name
            if not source.is_file():
                raise FileNotFoundError(
                    f"cannot publish run {self._run_id}: content artifact "
                    f"{name!r} is missing from {self._run_dir}"
                )
            shutil.copyfile(source, publish_dir / name)
        (publish_dir / _RUN_MANIFEST).write_text(
            json.dumps(
                {"run_id": self._run_id, "status": "COMPLETED"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        artifacts = {name: _sha256_file(self._run_dir / name) for name in
                     MANIFESTED_ARTIFACTS}
        metrics = json.loads(
            (self._run_dir / "metrics.json").read_text(encoding="utf-8")
        )
        evaluation = metrics.get("evaluation")
        status = evaluation.get("status") if isinstance(evaluation, Mapping) \
            else None
        reason = evaluation.get("reason") if isinstance(evaluation, Mapping) \
            else None
        accepted = ExperimentEvaluation.ACCEPTED.value
        rejected = ExperimentEvaluation.REJECTED.value
        untrusted = ExperimentEvaluation.UNTRUSTED.value
        # No silent ACCEPTED default: the manifest is written only from an
        # explicit evaluator decision, so a run can never be stamped accepted
        # just because an evaluator failed to record one.  An UNTRUSTED
        # metrics decision (an untrusted ENGINEERING diagnostic) is allowed
        # here but is always recorded as a REJECTED experiment -- the registry
        # and its immutable manifests only ever carry ACCEPTED or REJECTED.
        if status not in (accepted, rejected, untrusted):
            raise ValueError(
                f"cannot publish run {self._run_id}: metrics.json records no "
                "explicit evaluator decision; an ACCEPTED, REJECTED or "
                "UNTRUSTED status is required before the experiment manifest "
                "is written"
            )
        if status in (rejected, untrusted) and not (
            isinstance(reason, str) and reason.strip()
        ):
            raise ValueError(
                f"cannot publish run {self._run_id}: a REJECTED or UNTRUSTED "
                "experiment requires an explicit reason in metrics.json "
                "evaluation"
            )
        manifest = {
            "experiment_id": state.experiment_id,
            "status": rejected if status == untrusted else status,
            "dataset_version": frozen.dataset_version,
            "universe_version": frozen.universe_version,
            "code_commit": frozen.code_commit,
            "evaluation_reason": reason,
            "artifacts": artifacts,
        }
        (publish_dir / "experiment_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self._registry.publish(
            self._run_dir, ExperimentIdentity.of(frozen)
        )

    def _fail(self, state: RunState, error: Exception) -> None:
        state.status = RunStatus.FAILED
        state.failed_stage = self._active_stage
        state.error = {
            "stage": self._active_stage,
            "exception_class": type(error).__name__,
            "message": redact_text(str(error), self._secrets),
            "retriable": not isinstance(error, (TypeError, ValueError)),
        }
        state.touch()
        write_run_manifest(self._run_dir, state)
        self._logger.error(
            f"run failed at stage {self._active_stage}: "
            f"{type(error).__name__}: {redact_text(error, self._secrets)}",
            run_id=self._run_id, stage=self._active_stage, event="run_failed",
        )

    def _close_context(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None

    # ------------------------------------------------------------------ #
    # Pinned data access
    # ------------------------------------------------------------------ #

    def _open_context(self, dataset_version: str) -> DatasetContext:
        if self._context is None or self._context.version != dataset_version:
            self._close_context()
            self._context = DatasetReader(self._project_root).open(dataset_version)
        return self._context

    def _calendar(self) -> TradingCalendar:
        context = self._open_context(self._frozen_version)
        frame = context.read("trading_calendar")
        open_days = tuple(
            _as_date(day)
            for day, open_flag in zip(frame["calendar_date"],
                                      frame["is_trading_day"])
            if bool(open_flag)
        )
        if not open_days:
            raise ValueError(
                f"dataset {self._frozen_version} trading_calendar has no open days"
            )
        return TradingCalendar.from_open_days(open_days)

    def _signals(self, frozen: ExperimentSpec) -> tuple[date, ...]:
        calendar = self._calendar()
        return calendar.last_trading_day_each_week(
            frozen.date_range.start_date, frozen.date_range.end_date
        )

    # ------------------------------------------------------------------ #
    # Corporate-action trust gate
    # ------------------------------------------------------------------ #

    def _record_run_trust(
        self, state: RunState, frozen: ExperimentSpec
    ) -> dict[str, object]:
        """Compute this run's corporate-action trust record and store it.

        The record is the single deterministic decision shared by the backtest
        gate, the report/metrics stage and the run manifest.  Storing it on
        ``state`` means even a FAILED research run's manifest shows exactly
        which possible holdings were not trusted and why.
        """
        window_start, window_end = self._execution_window()
        decision = evaluate_corporate_action_trust(
            self._coverage_evidence(frozen),
            self._universe_symbols,
            window_start,
            window_end,
        )
        record: dict[str, object] = {
            "mode": frozen.trust_mode.value,
            "dataset_version": frozen.dataset_version,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }
        record.update(decision.to_dict())
        state.corporate_action_trust = record
        return record

    def _coverage_evidence(
        self, frozen: ExperimentSpec
    ) -> pd.DataFrame | None:
        """The pinned coverage table, or ``None`` when the dataset lacks one.

        An older dataset that predates the coverage-evidence table has no
        ``corporate_action_coverage`` key and therefore no verified evidence:
        the evaluator reads every holding as ``SOURCE_NOT_REQUESTED`` rather
        than trusting empty evidence.
        """
        context = self._open_context(frozen.dataset_version)
        if "corporate_action_coverage" not in context.tables:
            return None
        return context.read("corporate_action_coverage")

    def _execution_window(self) -> tuple[date, date]:
        """The coverage window a run must tile: the open day immediately before
        the first execution through the last execution."""
        signals = pd.read_parquet(self._run_dir / "signals.parquet")
        if signals.empty:
            raise ValueError(
                f"run {self._run_id} produced no signals; cannot derive the "
                "corporate-action coverage window"
            )
        execution_days = sorted(
            {_as_date(day) for day in signals["execution_date"]}
        )
        first_execution = execution_days[0]
        open_days = self._calendar().open_days
        try:
            index = open_days.index(first_execution)
        except ValueError as error:
            raise ValueError(
                f"first execution {first_execution.isoformat()} is not an open "
                "day of the pinned calendar"
            ) from error
        window_start = open_days[index - 1] if index > 0 else first_execution
        return window_start, execution_days[-1]

    def _enforce_research_trust(
        self, state: RunState, frozen: ExperimentSpec
    ) -> None:
        """Raise before any backtest when a RESEARCH run's evidence is untrusted.

        An ENGINEERING run records the same decision and proceeds as a
        diagnostic; the report stage then stamps its evaluation UNTRUSTED so it
        can never publish an ACCEPTED experiment manifest.
        """
        record = self._record_run_trust(state, frozen)
        if record["trusted"]:
            return
        if frozen.trust_mode is DataTrustMode.RESEARCH:
            reasons = record["reasons"]
            if not isinstance(reasons, list):
                raise TypeError("corporate action trust reasons must be a list")
            affected = len({str(item["symbol"]) for item in reasons})
            codes = ", ".join(
                sorted({str(item["code"]) for item in reasons})
            )
            raise ValueError(
                f"corporate action trust: research run {self._run_id} cannot "
                f"backtest dataset {frozen.dataset_version}: "
                f"{affected} of {len(self._universe_symbols)} possible "
                f"holdings lack full VERIFIED corporate-action coverage across "
                f"{record['window_start']}..{record['window_end']}; "
                f"untrusted codes: {codes}"
            )

    def _master_coverage_evidence(
        self, frozen: ExperimentSpec
    ) -> pd.DataFrame | None:
        """The pinned security_master_coverage table, or None when absent."""
        context = self._open_context(frozen.dataset_version)
        if "security_master_coverage" not in context.tables:
            return None
        return context.read("security_master_coverage")

    def _enforce_research_master_evidence(
        self, frozen: ExperimentSpec
    ) -> None:
        """Raise before any stage when a RESEARCH universe lacks master evidence.

        Formal research requires one ``security_master_coverage`` row per
        universe symbol: row presence means the listing facts were applied from
        a real tushare ``stock_basic`` refresh.  A bootstrap seed, an older
        dataset without the table and an incomplete refresh all fail here, each
        symbol carrying the stable ``SOURCE_NOT_REQUESTED`` code.  ENGINEERING
        always proceeds as a diagnostic (its evaluation is stamped UNTRUSTED by
        the report stage).
        """
        if frozen.trust_mode is not DataTrustMode.RESEARCH:
            return
        missing = missing_master_coverage_symbols(
            self._universe_symbols, self._master_coverage_evidence(frozen)
        )
        if not missing:
            return
        listed = ", ".join(
            f"{symbol}:{CoverageReason.SOURCE_NOT_REQUESTED.value}"
            for symbol in missing
        )
        raise ValueError(
            "security master evidence: research run {} cannot use dataset {}: "
            "{} of {} universe symbols have no security_master_coverage row "
            "({})".format(
                self._run_id,
                frozen.dataset_version,
                len(missing),
                len(self._universe_symbols),
                listed,
            )
        )

    def _untrusted_reason(
        self, frozen: ExperimentSpec, record: Mapping[str, object]
    ) -> str:
        """The explicit reason an untrusted ENGINEERING run publishes REJECTED."""
        reasons = record["reasons"]
        if not isinstance(reasons, list):
            raise TypeError("corporate action trust reasons must be a list")
        affected = len({str(item["symbol"]) for item in reasons})
        details = "; ".join(
            f"{item['symbol']}:{item['code']}" for item in reasons
        )
        return (
            f"corporate action trust: mode={record['mode']} dataset "
            f"{record['dataset_version']} is untrusted; "
            f"{affected} of {len(self._universe_symbols)} possible holdings "
            f"lack full VERIFIED corporate-action coverage across "
            f"{record['window_start']}..{record['window_end']}; {details}"
        )

    def _engineering_reason(
        self, frozen: ExperimentSpec, record: Mapping[str, object]
    ) -> str:
        """Why an ENGINEERING diagnostic never publishes an ACCEPTED experiment.

        Names the data when its evidence is untrusted, or the mode when its
        evidence is trusted (an ENGINEERING run is diagnostic-only; formal
        acceptance is reserved for RESEARCH).
        """
        if not bool(record["trusted"]):
            return self._untrusted_reason(frozen, record)
        return (
            f"corporate action trust: mode={record['mode']} is diagnostic-only "
            f"and never publishes an ACCEPTED experiment; dataset "
            f"{record['dataset_version']} has trusted coverage across "
            f"{record['window_start']}..{record['window_end']}"
        )

    # ------------------------------------------------------------------ #
    # Stage producers
    # ------------------------------------------------------------------ #

    def _produce_pin(self, frozen: ExperimentSpec) -> dict[str, str]:
        """Write the frozen spec, resolved dataset version and provenance."""
        spec_yaml = yaml.safe_dump(
            frozen.model_dump(mode="json"), sort_keys=True, allow_unicode=True
        )
        spec_path = self._run_dir / "experiment_spec.yml"
        spec_path.write_text(spec_yaml, encoding="utf-8")
        version_path = self._run_dir / "dataset_version.txt"
        version_path.write_text(f"{frozen.dataset_version}\n", encoding="utf-8")
        snapshot = {
            "experiment_id": ExperimentIdentity.of(frozen).experiment_id,
            "dataset_version": frozen.dataset_version,
            "universe_version": frozen.universe_version,
            "code_commit": frozen.code_commit,
            "factor_versions": dict(frozen.factor_versions),
            "cost_scenarios": list(frozen.cost_scenarios),
            "random_seed": frozen.random_seed,
            "parent_experiment_ids": list(frozen.parent_experiment_ids),
            "agent_id": frozen.agent_id,
            "python_version": platform.python_version(),
            "dependency_versions": self._dependency_versions(),
            "config_file_hashes": self._config_hashes(),
            "run_input_digest": self._digest,
        }
        snapshot_path = self._run_dir / "config_snapshot.yml"
        snapshot_path.write_text(
            yaml.safe_dump(snapshot, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        return {name: _sha256_file(self._run_dir / name)
                for name in ("experiment_spec.yml", "dataset_version.txt",
                             "config_snapshot.yml")}

    def _config_hashes(self) -> dict[str, str]:
        return {
            name: _sha256_file(self._config_root / "configs" / name)
            for name in ("project.yml", "costs.yml", "trading_rules.yml",
                         "universe.yml")
        }

    def _load_universe_symbols(self) -> tuple[str, ...]:
        context = self._open_context(self._frozen_version)
        master = context.read("security_master")
        symbols = tuple(str(symbol) for symbol in
                        sorted(master["symbol"].unique()))
        if not symbols:
            raise ValueError(
                f"dataset {self._frozen_version} has no securities in "
                "security_master; cannot run a factor"
            )
        return symbols

    def _produce_factor(self, state: RunState, frozen: ExperimentSpec) -> dict:
        """Compute every requested factor over one pinned signal-dataset."""
        signals = self._signals(frozen)
        factors = self._resolve_factors(frozen)
        frames: list[pd.DataFrame] = []
        for factor in factors:
            adapter = _DatasetFactorAdapter(
                context=self._open_context(frozen.dataset_version),
                universe_symbols=self._universe_symbols,
            )
            factor_context = FactorContext(
                dataset=adapter,
                universe_version=frozen.universe_version,
                start_date=frozen.date_range.start_date,
                end_date=frozen.date_range.end_date,
                signal_dates=signals,
            )
            result = factor.compute(factor_context)
            if not isinstance(result, FactorResult):
                raise TypeError(
                    f"factor {factor.name} returned {type(result).__name__}, "
                    "expected a FactorResult"
                )
            frames.append(result.frame)
        combined = frames[0] if len(frames) == 1 else pd.concat(
            frames, ignore_index=True
        )
        combined = combined.sort_values(
            ["trade_date", "symbol"], kind="stable"
        ).reset_index(drop=True)
        combined.to_parquet(self._run_dir / "factor_results.parquet", index=False)
        return {"factor_results.parquet":
                _sha256_file(self._run_dir / "factor_results.parquet")}

    def _resolve_factors(self, frozen: ExperimentSpec) -> list[Factor]:
        available = dict(self._factor_provider())
        resolved: list[Factor] = []
        for name, version in frozen.factor_versions.items():
            factor = available.get(name)
            if factor is None:
                raise ValueError(
                    f"no registered factor {name!r}; available: "
                    + ", ".join(sorted(available))
                )
            if factor.name != name or factor.version != version:
                raise ValueError(
                    f"factor {factor.name!r} version {factor.version!r} does not "
                    f"match the spec request {name!r}/{version!r}"
                )
            resolved.append(factor)
        return resolved

    def _produce_portfolio(
        self, state: RunState, frozen: ExperimentSpec
    ) -> dict[str, str]:
        """Build weekly signals and the frozen target book (pure intent).

        The target book is the only order intent this stage produces.  Each
        cost scenario's backtest generates its own orders from it against the
        scenario's realized account state (account-aware rebalance), so there
        is deliberately no top-level planned-order ledger: an idealized
        previous target book can never again mask what the account actually
        holds.
        """
        rule = frozen.portfolio_rule
        builder = TopNEqualWeight(top_n=rule.top_n, lot_size=rule.lot_size)
        factor_frame = pd.read_parquet(self._run_dir / "factor_results.parquet")
        # The factor frame is written from python-``date`` objects; a parquet
        # round-trip can surface the date column as datetime64, which compares
        # unequal to the plain ``date`` signals below, so normalise it back.
        factor_frame["trade_date"] = factor_frame["trade_date"].map(_as_date)
        prices = self._signal_price_frame(frozen)
        price_map: dict[date, pd.DataFrame] = {
            day: frame for day, frame in prices.groupby("trade_date")
        }
        signals = self._signals(frozen)
        calendar = self._calendar()

        target_rows: list[dict] = []
        signal_rows: list[dict] = []

        sizing_capital = self._sizing_capital
        for signal in signals:
            one_signal = factor_frame[factor_frame["trade_date"] == signal]
            if one_signal.empty:
                continue
            result = FactorResult(
                factor_name=str(one_signal["factor_name"].iloc[0]),
                factor_version=str(one_signal["factor_version"].iloc[0]),
                frame=one_signal.copy(),
            )
            execution_date = calendar.next_trading_day(signal)
            target = builder.build(result, price_map.get(signal, pd.DataFrame(
                columns=["symbol", "close"])), sizing_capital)
            planned_notional = sum(
                float(row["signal_price"]) * int(row["target_quantity"])
                for row in target.frame.to_dict("records")
            )
            signal_rows.append({
                "signal_date": signal,
                "execution_date": execution_date,
                "n_targets": int(len(target.frame)),
                "planned_notional": round(planned_notional, 2),
                "unallocated_weight": round(target.unallocated_weight, 6),
            })
            target_rows.extend(
                {**row, "trade_date": signal} for row in
                target.frame.to_dict("records")
            )

        target_frame = pd.DataFrame(target_rows)
        target_frame.to_parquet(
            self._run_dir / "target_positions.parquet", index=False
        )
        signal_frame = pd.DataFrame(signal_rows)
        signal_frame.to_parquet(self._run_dir / "signals.parquet", index=False)
        return {
            name: _sha256_file(self._run_dir / name)
            for name in ("signals.parquet", "target_positions.parquet")
        }

    def _signal_price_frame(self, frozen: ExperimentSpec) -> pd.DataFrame:
        context = self._open_context(frozen.dataset_version)
        daily = context.read("daily_bar")
        equity = daily[daily["symbol"].isin(self._universe_symbols)].copy()
        equity["trade_date"] = equity["trade_date"].map(_as_date)
        equity = equity[["trade_date", "symbol", "close"]]
        equity = equity[equity["close"].notna() & (equity["close"] > 0)]
        return equity

    def _produce_backtest(
        self, state: RunState, frozen: ExperimentSpec
    ) -> dict[str, str]:
        """Replay every cost scenario with account-aware order generation.

        Each scenario runs its own :class:`AccountAwareWeeklyRebalancer`
        against the same frozen, scenario-independent target book: the engine
        asks the provider once per open day with that scenario's live account,
        so orders are always ``frozen target - realized holdings`` (I1a) and a
        previously rejected difference is naturally resubmitted on a later
        rebalance day (stale-residual self-healing).  Submitted orders are
        scenario-specific by design; ``reconcile_orders`` then proves only the
        per-scenario bookkeeping completeness (filled + rejected == submitted,
        reasons within the auditable vocabulary), not generation correctness.
        """
        # The corporate-action gate runs first, before the target book is read
        # or the market/engine is built, so a RESEARCH run with untrusted
        # evidence never spends time on a backtest it will not keep.
        self._enforce_research_trust(state, frozen)
        targets_by_day, possible_held = self._load_target_book()
        if not targets_by_day:
            raise ValueError("no frozen target periods; nothing to backtest")
        market = self._load_market(frozen, sorted(targets_by_day))
        outputs: dict[str, str] = {}
        scenario_names = list(frozen.cost_scenarios)
        for scenario in scenario_names:
            directory = self._run_dir / "backtest" / scenario
            directory.mkdir(parents=True, exist_ok=True)
            rebalancer = AccountAwareWeeklyRebalancer(targets_by_day)
            request = BacktestRequest(
                dataset_version=frozen.dataset_version,
                initial_cash=self._project_config.initial_cash,
                calendar=market.calendar,
                rule_book=self._rule_book,
                cost_model=CostModel.from_config(
                    self._project_config.costs, scenario
                ),
                bars=market.bars,
                corporate_actions=market.corporate_actions,
                schedule=(),
                benchmark_symbols=tuple(self._project_config.benchmark_symbols),
                benchmarks=market.benchmarks,
                order_provider=rebalancer.orders_for,
                possible_held_symbols=possible_held,
            )
            result = BacktestEngine().run(request)
            result.fills.to_parquet(directory / "fills.parquet", index=False)
            result.rejections.to_parquet(
                directory / "rejections.parquet", index=False
            )
            result.action_ledger.to_parquet(
                directory / "action_ledger.parquet", index=False
            )
            result.daily_equity.to_parquet(
                directory / "daily_equity.parquet", index=False
            )
            result.submitted_orders.to_parquet(
                directory / "submitted_orders.parquet", index=False
            )
            diffs = reconcile_orders(
                plan=self._plan_from_submitted(
                    result.submitted_orders, rebalancer
                ),
                submitted=result.submitted_orders,
                fills=result.fills,
                rejections=result.rejections,
            )
            diffs.to_parquet(directory / "order_diffs.parquet", index=False)
            for relative in (
                "fills.parquet",
                "rejections.parquet",
                "action_ledger.parquet",
                "daily_equity.parquet",
                "submitted_orders.parquet",
                "order_diffs.parquet",
            ):
                outputs[f"backtest/{scenario}/{relative}"] = _sha256_file(
                    directory / relative
                )
        canonical = self._canonical_scenario(scenario_names)
        scenario_dir = self._run_dir / "backtest" / canonical
        canonical_mapping = {
            "fills.parquet": scenario_dir / "fills.parquet",
            "daily_equity.parquet": scenario_dir / "daily_equity.parquet",
            "corporate_action_ledger.parquet":
                scenario_dir / "action_ledger.parquet",
        }
        for artifact_name, source in canonical_mapping.items():
            destination = self._run_dir / artifact_name
            shutil.copyfile(source, destination)
            outputs[artifact_name] = _sha256_file(destination)
        cash_ledger = self._derive_cash_ledger(
            pd.read_parquet(scenario_dir / "fills.parquet"),
            pd.read_parquet(scenario_dir / "action_ledger.parquet"),
            float(self._project_config.initial_cash),
            market.calendar,
            scenario_dir / "daily_equity.parquet",
        )
        cash_ledger.to_parquet(self._run_dir / "cash_ledger.parquet", index=False)
        outputs["cash_ledger.parquet"] = _sha256_file(
            self._run_dir / "cash_ledger.parquet"
        )
        return outputs

    def _canonical_scenario(self, scenario_names: Sequence[str]) -> str:
        if CANONICAL_SCENARIO in scenario_names:
            return CANONICAL_SCENARIO
        return scenario_names[-1]

    def _load_target_book(
        self,
    ) -> tuple[dict[date, tuple[date, dict[str, int]]], frozenset[str]]:
        """The frozen target book and the possibly-held symbol superset.

        Targets come from the portfolio stage's ``signals.parquet`` (the
        signal/execution day pairs) and ``target_positions.parquet`` (the
        per-symbol quantities); a period with an empty target frame keeps an
        empty target map so held names are still carried into that rebalance
        and sold flat.  The possibly-held superset is the union of every
        period's target symbols (M0): a name can only be held if some period
        targeted it (corporate actions never create a different ticker), so
        the union covers every stale residual a provider order could touch.
        """
        signals = pd.read_parquet(self._run_dir / "signals.parquet")
        if signals.empty:
            return {}, frozenset()
        signals["signal_date"] = signals["signal_date"].map(_as_date)
        signals["execution_date"] = signals["execution_date"].map(_as_date)
        signal_records = signals.to_dict("records")
        targets_by_day: dict[date, tuple[date, dict[str, int]]] = {
            record["execution_date"]: (record["signal_date"], {})
            for record in signal_records
        }
        execution_of_signal = {
            record["signal_date"]: record["execution_date"]
            for record in signal_records
        }
        targets = pd.read_parquet(self._run_dir / "target_positions.parquet")
        possible: set[str] = set()
        if not targets.empty:
            targets["trade_date"] = targets["trade_date"].map(_as_date)
            for record in targets.to_dict("records"):
                execution_date = execution_of_signal[record["trade_date"]]
                targets_by_day[execution_date][1][str(record["symbol"])] = int(
                    record["target_quantity"]
                )
                possible.add(str(record["symbol"]))
        return targets_by_day, frozenset(possible)

    @staticmethod
    def _plan_from_submitted(
        submitted: pd.DataFrame, rebalancer: AccountAwareWeeklyRebalancer
    ) -> pd.DataFrame:
        """The in-memory plan: what this scenario actually submitted (I3).

        With account-aware generation the plan *is* the submitted stream, so
        ``reconcile_orders``' stable-ID identity assertions hold by
        construction and only bookkeeping completeness is being proven; the
        generation contract is owned by the rebalancer's own oracle tests and
        the engine's provider-fidelity test.  ``signal_date`` is stamped from
        the frozen target book for reporting continuity.
        """
        signal_of_day = rebalancer.signal_date_by_execution_day
        records = []
        for row in submitted.to_dict("records"):
            execution_date = _as_date(row["trade_date"])
            records.append(
                {
                    "signal_date": signal_of_day[execution_date],
                    "execution_date": execution_date,
                    "order_id": str(row["order_id"]),
                    "side": str(row["side"]),
                    "symbol": str(row["symbol"]),
                    "quantity": int(row["quantity"]),
                }
            )
        plan = pd.DataFrame(
            records,
            columns=["signal_date", "execution_date", "order_id", "side",
                     "symbol", "quantity"],
        )
        if plan.empty:
            return plan
        return plan.sort_values(
            ["execution_date", "order_id"], kind="stable"
        ).reset_index(drop=True)

    @staticmethod
    def _order_schedule(plan: pd.DataFrame) -> tuple[OrderDay, ...]:
        """Group the plan ledger into engine ``OrderDay`` rows (pure intent).

        Every plan order keeps its frozen ``order_id``; no execution-day
        information is consulted (the plan is already sized at signal close).
        """
        by_day: dict[date, list[dict]] = {}
        for record in plan.to_dict("records"):
            by_day.setdefault(record["execution_date"], []).append(record)
        order_days: list[OrderDay] = []
        for execution_day in sorted(by_day):
            rows = by_day[execution_day]
            ordered = sorted(rows, key=lambda record: record["order_id"])
            sells = tuple(
                Order(
                    order_id=str(record["order_id"]),
                    side=SELL,
                    symbol=str(record["symbol"]),
                    quantity=int(record["quantity"]),
                )
                for record in ordered
                if record["side"] == SELL
            )
            buys = tuple(
                Order(
                    order_id=str(record["order_id"]),
                    side=BUY,
                    symbol=str(record["symbol"]),
                    quantity=int(record["quantity"]),
                )
                for record in ordered
                if record["side"] == BUY
            )
            order_days.append(
                OrderDay(trade_date=execution_day, sells=sells, buys=buys)
            )
        return tuple(order_days)

    def _load_market(
        self, frozen: ExperimentSpec, execution_days: Sequence[date]
    ):
        """The pinned equity/benchmark/corporate-action frames for one replay.

        ``execution_days`` are the frozen target book's rebalance days (the
        provider's fire dates); the window starts one open day earlier so the
        first execution open always has a prior close for its price-limit band.
        """
        calendar = self._calendar()
        days = sorted(set(execution_days))
        if not days:
            raise ValueError("no execution days; nothing to backtest")
        first_execution, last_execution = days[0], days[-1]
        open_days = calendar.open_days
        index = open_days.index(first_execution)
        window_start = open_days[index - 1] if index > 0 else first_execution
        window_end = last_execution

        context = self._open_context(frozen.dataset_version)
        daily = context.read("daily_bar")
        equity = daily[daily["symbol"].isin(self._universe_symbols)].copy()
        equity["trade_date"] = equity["trade_date"].map(_as_date)
        equity = equity[
            (equity["trade_date"] >= window_start)
            & (equity["trade_date"] <= window_end)
        ].copy()
        if equity.empty:
            raise ValueError("no equity bars cover the backtest window")
        equity = equity.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
        bars = pd.DataFrame({
            "symbol": equity["symbol"],
            "trade_date": equity["trade_date"],
            "open": equity["open"],
            "close": equity["close"],
            # Phase-one ruling (§13.5): the primary close series is authoritative;
            # engine ERROR-level bars (e.g. cross-source close disagreement) are
            # report-only and never reach the backtest.
            "quality_severity": QUALITY_SEVERITY_AUTHORITATIVE,
        })
        benchmark_symbols = set(self._project_config.benchmark_symbols)
        benchmark = daily[daily["symbol"].isin(benchmark_symbols)].copy()
        benchmark["trade_date"] = benchmark["trade_date"].map(_as_date)
        benchmark = benchmark[
            (benchmark["trade_date"] >= window_start)
            & (benchmark["trade_date"] <= window_end)
        ].copy()
        benchmarks = pd.DataFrame({
            "symbol": benchmark["symbol"],
            "trade_date": benchmark["trade_date"],
            "close": benchmark["close"],
        })
        if len(benchmarks) != len(benchmark):
            raise ValueError("benchmark frame must be one row per symbol/date")
        corporate_actions = context.read("corporate_action")
        return _MarketFrames(
            calendar=calendar,
            bars=bars,
            benchmarks=benchmarks,
            corporate_actions=corporate_actions,
        )

    def _derive_cash_ledger(
        self,
        fills: pd.DataFrame,
        actions: pd.DataFrame,
        initial_cash: float,
        calendar: TradingCalendar,
        equity_path: Path,
    ) -> pd.DataFrame:
        """Rebuild the canonical cash ledger by replaying the fills/actions."""
        from stock_quant.backtest.account import Account

        account = Account(initial_cash, calendar=calendar)
        actions_by_day: dict[date, list[dict]] = {}
        if actions is not None and not actions.empty:
            for record in actions.sort_values("seq").to_dict("records"):
                ex_date = _as_date(record["ex_date"])
                if ex_date is None:
                    continue
                actions_by_day.setdefault(ex_date, []).append(record)
        fills_by_day: dict[date, list[Fill]] = {}
        if fills is not None and not fills.empty:
            for record in fills.to_dict("records"):
                fill = _as_fill(record)
                fills_by_day.setdefault(fill.trade_date, []).append(fill)
        for day in sorted(set(actions_by_day) | set(fills_by_day)):
            for record in actions_by_day.get(day, []):
                amount = Decimal(str(record.get("cash_credited") or 0))
                shares = int(record.get("shares_added") or 0)
                action_id = str(record.get("action_id") or "")
                note = str(record.get("note") or "") or f"corporate action {day}"
                if amount > 0:
                    account.credit_cash(amount, note=note)
                if shares > 0 and action_id:
                    account.increase_position(
                        str(record["symbol"]), shares, buy_date=day,
                        fill_id=action_id, note=note,
                    )
            for fill in fills_by_day.get(day, []):
                account.apply_fill(fill)
        entries = [
            {
                "seq": entry.seq,
                "kind": entry.kind,
                "amount": float(entry.amount),
                "balance": float(entry.balance),
                "note": entry.note,
            }
            for entry in account.cash_ledger
        ]
        ledger = pd.DataFrame(entries)
        final = ledger["balance"].iloc[-1] if len(ledger) else 0.0
        equity = pd.read_parquet(equity_path)
        expected = float(equity["cash"].iloc[-1])
        if abs(final - expected) > 1e-3:
            raise ValueError(
                f"derived cash ledger balance {final} does not reconcile with "
                f"final scenario cash {expected}"
            )
        return ledger

    def _produce_report(self, state: RunState, frozen: ExperimentSpec) -> dict:
        """Compute metrics, record the evaluation and render the report."""
        canonical = self._canonical_scenario(list(frozen.cost_scenarios))
        info = AnalyticsInput(
            run_dir=self._run_dir,
            experiment_id=state.experiment_id,
            run_id=self._run_id,
            spec=frozen,
            dataset_version=frozen.dataset_version,
            universe_version=frozen.universe_version,
            code_commit=frozen.code_commit,
            initial_cash=self._project_config.initial_cash,
            sizing_capital=self._sizing_capital,
            scenarios=tuple(frozen.cost_scenarios),
            canonical_scenario=canonical,
        )
        analytics_out = self._analytics(info)
        if not isinstance(analytics_out, dict):
            raise TypeError(
                "analytics must return a JSON-serializable dict, got "
                f"{type(analytics_out).__name__}"
            )
        meta = {
            "run_id": self._run_id,
            "experiment_id": state.experiment_id,
            "spec": frozen.model_dump(mode="json"),
            "dataset_version": frozen.dataset_version,
            "universe_version": frozen.universe_version,
            "code_commit": frozen.code_commit,
            "python_version": platform.python_version(),
            "dependency_versions": self._dependency_versions(),
            "config_file_hashes": self._config_hashes(),
            "run_input_digest": self._digest,
            "initial_cash": self._project_config.initial_cash,
            "sizing_capital": self._sizing_capital,
            "benchmark_symbols": list(self._project_config.benchmark_symbols),
            "canonical_scenario": canonical,
        }
        metrics: dict[str, object] = {"meta": meta, **analytics_out}
        evaluation = self._evaluator(metrics)
        if not isinstance(evaluation, Evaluation):
            raise TypeError(
                "evaluator must return an Evaluation, got "
                f"{type(evaluation).__name__}"
            )
        # Persist the trust decision alongside the performance claim so
        # metrics.json is self-auditing.  ENGINEERING is diagnostic-only and
        # never publishes an ACCEPTED experiment, so every ENGINEERING run is
        # stamped UNTRUSTED here regardless of evidence: the reason names the
        # data when its evidence is untrusted, or the mode when it is trusted.
        trust_record = self._record_run_trust(state, frozen)
        metrics["corporate_action_trust"] = trust_record
        if frozen.trust_mode is DataTrustMode.ENGINEERING:
            evaluation = Evaluation(
                ExperimentEvaluation.UNTRUSTED,
                self._engineering_reason(frozen, trust_record),
            )
        metrics["evaluation"] = {
            "status": evaluation.status.value,
            "reason": evaluation.reason,
        }
        report_html = self._report(metrics)
        if not isinstance(report_html, str):
            raise TypeError(
                f"report must return an HTML string, got {type(report_html).__name__}"
            )
        metrics_path = self._run_dir / "metrics.json"
        metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report_path = self._run_dir / "report.html"
        report_path.write_text(report_html, encoding="utf-8")
        return {
            "metrics.json": _sha256_file(metrics_path),
            "report.html": _sha256_file(report_path),
        }

    @property
    def _sizing_capital(self) -> float:
        return self._project_config.initial_cash * _SIZING_FRACTION


@dataclass(frozen=True)
class _MarketFrames:
    calendar: TradingCalendar
    bars: pd.DataFrame
    benchmarks: pd.DataFrame
    corporate_actions: pd.DataFrame


# --------------------------------------------------------------------------- #
# Factor dataset adapter over a pinned dataset context
# --------------------------------------------------------------------------- #


class _DatasetFactorAdapter:
    """A :class:`FactorDataset` read surface over one pinned dataset version.

    The canonical ``daily_bar`` table carries no quality annotation, so the
    adapter annotates every clean bar with ``INFO`` quality and derives
    ``listed_trading_days`` from the pinned trading calendar and the security
    master's listing dates.  Rows are restricted to the securities named by the
    master so index/benchmark series never enter factor observations.
    """

    def __init__(
        self,
        *,
        context: DatasetContext,
        universe_symbols: Sequence[str],
    ) -> None:
        self._context = context
        self._universe_symbols = set(universe_symbols)

    def factor_input(self) -> pd.DataFrame:
        daily = self._context.read("daily_bar")
        rows = daily[daily["symbol"].isin(self._universe_symbols)].copy()
        if rows.empty:
            return pd.DataFrame(
                columns=[
                    "trade_date", "symbol", "source", "adjustment",
                    "adjusted_close", "quality_severity", "listed_trading_days",
                ]
            )
        # DuckDB surfaces DATE as datetime64; factors compare ``trade_date``
        # against plain ``date`` objects, so normalize to real dates up front.
        rows["trade_date"] = rows["trade_date"].map(_as_date)
        master = self._context.read("security_master")
        list_date = {
            str(row["symbol"]): _as_date(row["list_date"])
            for row in master.to_dict("records")
        }
        calendar = self._context.read("trading_calendar")
        open_days = sorted(
            _as_date(day)
            for day, open_flag in zip(calendar["calendar_date"],
                                      calendar["is_trading_day"])
            if bool(open_flag)
        )
        open_ordinals = [_to_ordinal(day) for day in open_days]
        rows = rows.sort_values(
            ["symbol", "trade_date"], kind="stable"
        ).reset_index(drop=True)
        from bisect import bisect_left

        listed_by_ordinal: dict[str, dict[int, int]] = {}
        for symbol in sorted(set(rows["symbol"])):
            mapping: dict[int, int] = {}
            listed_on = list_date.get(symbol)
            if listed_on is not None:
                anchor = bisect_left(open_ordinals, listed_on.toordinal())
                for index in range(anchor, len(open_days)):
                    mapping[open_days[index].toordinal()] = index - anchor + 1
            listed_by_ordinal[symbol] = mapping
        listed = [
            listed_by_ordinal[symbol].get(_to_ordinal(day), 0)
            for symbol, day in zip(rows["symbol"], rows["trade_date"])
        ]
        out = pd.DataFrame({
            "trade_date": rows["trade_date"],
            "symbol": rows["symbol"],
            "source": rows["source"],
            "adjustment": rows["adjustment"],
            "adjusted_close": rows["close"],
            # Phase-one ruling (§13.5): the Tushare primary close series is
            # authoritative for factors; per-bar cross-source close ERROR is
            # report-only (quality report) and never fed to a factor.
            "quality_severity": QUALITY_SEVERITY_AUTHORITATIVE,
            "listed_trading_days": listed,
        })
        return out


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #


def _to_ordinal(day: object) -> int:
    return _as_date(day).toordinal()


def _as_date(value: object) -> date:
    """Normalize a datetime/Timestamp/date to a plain ``date``.

    A ``datetime`` is a ``date`` subclass but compares unequal to a plain
    ``date``, so it (and any object exposing ``.date()`` such as a pandas
    ``Timestamp``) is reduced to a calendar date before it joins engine,
    calendar or signal logic.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        result = value.date()
        if isinstance(result, datetime):
            return result.date()
        if isinstance(result, date):
            return result
    raise TypeError(f"cannot interpret {value!r} as a date")


def _as_fill(record: Mapping[str, object]) -> Fill:
    recorded = record.get("reference_price")
    return Fill(
        fill_id=str(record["fill_id"]),
        order_id=str(record["order_id"]),
        trade_date=_as_date(record["trade_date"]),
        side=str(record["side"]),
        symbol=str(record["symbol"]),
        quantity=int(record["quantity"]),
        price=Decimal(str(record["price"])),
        commission=Decimal(str(record["commission"])),
        stamp_tax=Decimal(str(record["stamp_tax"])),
        # Legacy ledgers have no reference column; fall back to the fill price
        # so replayed fills stay consistent with the zero-slippage convention.
        reference_price=(
            Decimal(str(recorded))
            if recorded is not None
            else Decimal(str(record["price"]))
        ),
    )


def _date_text(value: object) -> str:
    day = _as_date(value)
    return day.isoformat()


def _html(value: object) -> str:
    from html import escape

    return escape(str(value))


def _round2(value: float) -> float:
    return round(float(value), 2)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
