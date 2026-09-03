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
from stock_quant.config import load_project_config
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.dataset import (
    DatasetContext,
    DatasetPublisher,
    DatasetReader,
)
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
from stock_quant.research.registry import (
    ExperimentIdentity,
    ExperimentRegistry,
    PublishedExperiment,
)
from stock_quant.research.spec import ExperimentSpec, load_experiment_spec

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
    """Per-scenario summary over the completed backtest ledgers."""

    def compute(self, metrics_input: AnalyticsInput) -> dict[str, object]:
        scenarios: dict[str, object] = {}
        for scenario in metrics_input.scenarios:
            directory = metrics_input.run_dir / "backtest" / scenario
            equity = pd.read_parquet(directory / "daily_equity.parquet")
            fills = pd.read_parquet(directory / "fills.parquet")
            start = float(equity["total_equity"].iloc[0])
            end = float(equity["total_equity"].iloc[-1])
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
        logger: StructuredLogger | None = None,
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
        self._logger = logger or StructuredLogger(terminal=False, secrets=secrets)

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
    ) -> PublishedExperiment:
        """Freeze, run and publish one experiment spec.

        ``stage_observer(stage, state)`` is called after each fine stage
        completes; an observer that raises aborts the run into a FAILED state
        (useful for injected-failure tests).  ``spec_path`` is resolved against
        ``config_root`` unless absolute.
        """
        observer = self._observer if stage_observer is None else stage_observer
        frozen = self._freeze(spec_path)
        state = self._begin(frozen)
        try:
            self._active_stage = None
            self._pipeline(state, frozen, observer)
            published = self._publish(state, frozen)
            state.status = RunStatus.COMPLETED
            state.stage = DataStage.REPORTED
            state.touch()
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
        """True when an experiment directory was created without a full publish.

        A failed run must never leave a half-formed experiment directory, so
        this is normally ``False`` until a publish completes.
        """
        experiments_root = self._project_root / "data" / "experiments"
        if not experiments_root.is_dir():
            return False
        return any(
            entry.is_dir()
            for entry in experiments_root.iterdir()
            if entry.name != "registry.parquet"
        )

    @property
    def run_id(self) -> str | None:
        """The most recent ``run_<experiment_id>``, or ``None``."""
        return self._run_id

    # ------------------------------------------------------------------ #
    # Freeze / identity / workspace
    # ------------------------------------------------------------------ #

    def _freeze(self, spec_path: str | Path) -> ExperimentSpec:
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
        evaluation = metrics.get("evaluation", {})
        default_status = ExperimentEvaluation.ACCEPTED.value
        manifest = {
            "experiment_id": state.experiment_id,
            "status": str(evaluation.get("status", default_status)),
            "dataset_version": frozen.dataset_version,
            "universe_version": frozen.universe_version,
            "code_commit": frozen.code_commit,
            "evaluation_reason": evaluation.get("reason"),
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
        """Build weekly signals, target positions and net-rebalance orders."""
        rule = frozen.portfolio_rule
        builder = TopNEqualWeight(top_n=rule.top_n, lot_size=rule.lot_size)
        factor_frame = pd.read_parquet(self._run_dir / "factor_results.parquet")
        prices = self._signal_price_frame(frozen)
        price_map: dict[date, pd.DataFrame] = {
            day: frame for day, frame in prices.groupby("trade_date")
        }
        signals = self._signals(frozen)
        calendar = self._calendar()

        target_rows: list[dict] = []
        signal_rows: list[dict] = []
        order_rows: list[dict] = []
        order_days: list[OrderDay] = []
        previous_book: dict[str, int] = {}
        order_seq = 0

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
            target_by_symbol = {
                str(row["symbol"]): int(row["target_quantity"])
                for row in target.frame.to_dict("records")
            }
            # A weekly *net* rebalance: sell names that leave or shrink below
            # their fixed target and buy names that enter or grow above it, so
            # a retained name is never both sold and bought on the same open
            # (the execution contract allows one bar row per symbol per day).
            sells: list[Order] = []
            for symbol in sorted(previous_book):
                quantity = previous_book[symbol] - target_by_symbol.get(symbol, 0)
                if quantity <= 0:
                    continue
                order_seq += 1
                order = Order(
                    order_id=f"o{order_seq:06d}", side=SELL, symbol=symbol,
                    quantity=quantity, note="weekly_rebalance",
                )
                sells.append(order)
                order_rows.append({
                    "execution_date": execution_date,
                    "order_id": order.order_id,
                    "side": SELL,
                    "symbol": symbol,
                    "quantity": quantity,
                })
            buys_list: list[Order] = []
            for symbol, target_quantity in sorted(target_by_symbol.items()):
                quantity = target_quantity - previous_book.get(symbol, 0)
                if quantity <= 0:
                    continue
                order_seq += 1
                order = Order(
                    order_id=f"o{order_seq:06d}", side=BUY, symbol=symbol,
                    quantity=quantity, note="weekly_rebalance",
                )
                buys_list.append(order)
                order_rows.append({
                    "execution_date": execution_date,
                    "order_id": order.order_id,
                    "side": BUY,
                    "symbol": symbol,
                    "quantity": quantity,
                })
            order_days.append(OrderDay(
                trade_date=execution_date,
                sells=tuple(sells),
                buys=tuple(buys_list),
            ))
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
            previous_book = target_by_symbol

        target_frame = pd.DataFrame(target_rows)
        target_frame.to_parquet(
            self._run_dir / "target_positions.parquet", index=False
        )
        signal_frame = pd.DataFrame(signal_rows)
        signal_frame.to_parquet(self._run_dir / "signals.parquet", index=False)
        order_frame = pd.DataFrame(order_rows)
        order_frame.to_parquet(self._run_dir / "orders.parquet", index=False)
        self._schedule = tuple(order_days)
        return {
            name: _sha256_file(self._run_dir / name)
            for name in ("signals.parquet", "target_positions.parquet",
                         "orders.parquet")
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
        """Replay the precomputed order schedule once per cost scenario."""
        schedule = self._read_schedule()
        market = self._load_market(frozen, schedule)
        outputs: dict[str, str] = {}
        scenario_names = list(frozen.cost_scenarios)
        for scenario in scenario_names:
            directory = self._run_dir / "backtest" / scenario
            directory.mkdir(parents=True, exist_ok=True)
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
                schedule=schedule,
                benchmark_symbols=tuple(self._project_config.benchmark_symbols),
                benchmarks=market.benchmarks,
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
            for relative in ("fills.parquet", "rejections.parquet",
                             "action_ledger.parquet", "daily_equity.parquet"):
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

    def _read_schedule(self) -> tuple[OrderDay, ...]:
        orders = pd.read_parquet(self._run_dir / "orders.parquet")
        if orders.empty:
            return ()
        by_day: dict[date, dict[str, list[Order]]] = {}
        for record in orders.to_dict("records"):
            day = _as_date(record["execution_date"])
            order = Order(
                order_id=str(record["order_id"]),
                side=str(record["side"]),
                symbol=str(record["symbol"]),
                quantity=int(record["quantity"]),
                note="weekly_rebalance",
            )
            by_day.setdefault(day, {"sells": [], "buys": []})[
                "sells" if order.side == SELL else "buys"
            ].append(order)
        return tuple(
            OrderDay(
                trade_date=day,
                sells=tuple(parts["sells"]),
                buys=tuple(parts["buys"]),
            )
            for day, parts in sorted(by_day.items())
        )

    def _load_market(self, frozen: ExperimentSpec, schedule: tuple[OrderDay, ...]):
        """The pinned equity/benchmark/corporate-action frames for one replay."""
        calendar = self._calendar()
        execution_days = sorted(
            order_day.trade_date for order_day in schedule
        )
        if not execution_days:
            raise ValueError("schedule contains no order days; nothing to backtest")
        first_execution, last_execution = execution_days[0], execution_days[-1]
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
            "quality_severity": "INFO",
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
            "quality_severity": "INFO",
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
