"""Official research run orchestration with resume and immutable publish (Task 11).

:class:`ResearchRunner` turns one frozen :class:`ExperimentSpec` into a complete,
reproducible experiment.  It resolves a ``CURRENT`` dataset/universe request to
an explicit version *exactly once* before any market data is read: the dataset
version is pinned first, then a spec that names a ``universe_definition`` is
preflighted against it -- the frozen :class:`~stock_quant.research.universe.
UniverseDefinition` is loaded from ``configs/universes/``, its membership-table
hash and the mandatory ``index_membership_evidence`` acceptance gate are
enforced on the pinned dataset *before* the identity is computed, and only then
is ``universe_version`` frozen to the definition version
(``ExperimentSpec.freeze``).  A preflight rejection stops the run at the
distinct ``universe_acceptance`` stage with a redacted manifest, never produces
factor artifacts and never falls back to the master's full symbol list; a spec
without a ``universe_definition`` keeps the legacy engineering
``configs/universe.yml`` resolution.

The run then executes the pinned pipeline: a factor dataset adapter, the
specified factor, a weekly top-N equal-weight portfolio, one T+1 backtest per
cost scenario and analytics/report generation.  Definition identity
(universe id/version, rules version, table hash, coverage) plus the
per-signal-day member counts and ``{ISO date: snapshot sha256}`` map are
persisted into the run manifest, ``universe_preflight.json``, the experiment
manifest, ``config_snapshot.yml`` and ``metrics.json``.  Results are staged
under ``data/runs/<run_id>/`` and only an evaluated, manifest-verified publish
is renamed into ``data/experiments/<experiment_id>/`` by the registry.

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
import uuid
from bisect import bisect_left
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
from stock_quant.data_contracts import TIER_ANCHORED, TIER_RESEARCH_ONLY
from stock_quant.data_model.adjusted_bar import ADJUSTMENT_NAME
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
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    SecurityMasterBoundary,
    resolve_memberships,
)
from stock_quant.data_quality.models import CODE_COVERAGE_DOWNGRADED
from stock_quant.factors.base import Factor, FactorContext
from stock_quant.factors.models import FactorResult
from stock_quant.logging import StructuredLogger, redact_text
from stock_quant.portfolio.equal_weight import TopNEqualWeight
from stock_quant.research.acceptance import (
    AcceptanceGateError,
    AcceptanceResult,
    enforce_required_results,
    evaluate_index_membership_evidence,
    read_membership_table,
)
from stock_quant.research.acceptance.models import (
    CURRENT_ACCEPTED,
    AcceptanceRecord,
)
from stock_quant.research.acceptance.registry import (
    AcceptanceIntegrityError,
    AcceptanceNotFound,
    AcceptanceRegistry,
    NoValidAcceptance,
)
from stock_quant.research.acceptance.service import (
    AcceptanceBindingError,
    acceptance_audit_dict,
    verify_acceptance_bindings,
)
from stock_quant.research.models import (
    CANONICAL_SCENARIO,
    FOLD_ARTIFACTS,
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
from stock_quant.research.spec import (
    BufferedRiskWeightedPortfolioRule,
    ExperimentSpec,
    load_experiment_spec,
)
from stock_quant.research.trust import (
    DataTrustMode,
    evaluate_corporate_action_trust,
)
from stock_quant.research.universe import (
    UniverseDefinition,
    UniverseResolver,
    load_universe_definition,
)
from stock_quant.research.walk_forward.evaluation import evaluate_stability
from stock_quant.research.walk_forward.metrics import (
    FoldMetrics,
    aggregate_oos_returns,
)
from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    WalkForwardPolicy,
)
from stock_quant.research.walk_forward.runner import (
    WalkForwardRequest,
    WalkForwardRunner,
)
from stock_quant.research.walk_forward.schedule import (
    FoldOutcomeLedger,
    FoldOutcomeStatus,
    FoldSchedule,
    FoldWindow,
    materialize_schedule,
    sha256_file,
)
from stock_quant.research.walk_forward.snapshots import build_snapshot_bundle

_CURRENT = "CURRENT"

#: Coarse pipeline stage labels, in dependency order.  The stage record is
#: also the resume unit: labels map to the shared ``DataStage`` vocabulary.
_STAGE_LABELS = ("pin", "factor", "portfolio", "backtest", "report")

#: Walk-forward pipeline stage labels (the ``walk_forward_oos_v1``
#: execution pipeline): the schedule materializes before any fold runs and
#: the stability report closes the run.
_WF_STAGE_LABELS = ("pin", "schedule", "folds", "report")

_WF_LABEL_TO_DATASTAGE = {
    "pin": DataStage.PUBLISHED,
    "schedule": DataStage.PUBLISHED,
    "folds": DataStage.BACKTESTED,
    "report": DataStage.REPORTED,
}

#: The fine pre-factor stage label a frozen-definition preflight failure is
#: recorded under: the membership acceptance gate runs before identity and
#: before any factor work, so its failures never reach a pipeline stage.
_STAGE_UNIVERSE_ACCEPTANCE = "universe_acceptance"

#: The fine pre-factor stage label a table-tier preflight failure is recorded
#: under (spec A2): tier routing happens before identity and factor work.
_STAGE_TABLE_TIERS = "table_tiers"

#: Steady-state member counts of the first-class indices.  The preflight
#: cardinality check enforces the count on every trading day of the pinned
#: calendar unless an immutable official exception record applies; custom
#: pools carry no canonical size and are validated without one.
_CANONICAL_UNIVERSE_SIZES = {
    "csi300": 300,
    "csi500": 500,
    "csi1000": 1000,
    "sse50": 50,
    "sse180": 180,
    "szse100": 100,
}

_LABEL_TO_DATASTAGE = {
    "pin": DataStage.PUBLISHED,
    "factor": DataStage.FACTOR_READY,
    "portfolio": DataStage.FACTOR_READY,
    "backtest": DataStage.BACKTESTED,
    "report": DataStage.REPORTED,
}

_STAGES_FILE = ".stages.json"
_RUN_MANIFEST = "run_manifest.json"

#: Factor-stage metadata artifact: the frozen universe identity plus the
#: per-signal-day member counts and snapshot hashes the factor stage filtered
#: under.  A run workspace file (resume-hashed), never a published artifact.
_FACTOR_METADATA = "factor_metadata.json"

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


class UniversePreflightFailed(RuntimeError):
    """The frozen universe definition was rejected before any factor work.

    Carries only the stable error codes (never evidence payloads) so the
    redacted preflight manifest can name the rejection without leaking data.
    """

    def __init__(self, message: str, *, error_codes: Sequence[str] = (),
                 universe_id: str | None = None) -> None:
        super().__init__(message)
        self.error_codes = tuple(sorted(set(error_codes)))
        self.universe_id = universe_id


class TableTierPreflightFailed(RuntimeError):
    """The pinned dataset/current tier policy rejected the run's input tables.

    Carries only stable error codes so the redacted preflight manifest can
    name the rejection without leaking data (mirrors
    :class:`UniversePreflightFailed`).
    """

    def __init__(self, message: str, *, error_codes: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.error_codes = tuple(sorted(set(error_codes)))


def factor_input_tables(factors: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
    """Map factor name to its declared ``inputs``; missing declarations fail.

    Fail-closed (spec A2): every factor must name the canonical tables it
    reads -- a factor without an ``inputs`` declaration is a contract bug
    that rejects the run instead of silently skipping the tier gate.
    """
    resolved: dict[str, tuple[str, ...]] = {}
    for name, factor in sorted(factors.items()):
        inputs = getattr(factor, "inputs", None)
        if inputs is None:
            raise ValueError(
                f"factor {name!r} declares no inputs (spec A2): every factor "
                "must name the canonical tables it reads"
            )
        resolved[name] = tuple(inputs)
    return resolved


def table_tier_violations(
    contracts: Mapping[str, object],
    input_tables: Sequence[str],
    downgrade_records: Sequence[Mapping[str, object]],
    mode: str,
) -> list[str]:
    """Tier violations for one run's input tables (spec A2 consumer gate).

    ``downgrade_records`` are the pinned version's ``coverage_downgraded``
    quality-report details.  RESEARCH mode fails on research_only inputs and
    untrusted anchored inputs; ENGINEERING (diagnostic-only) is exempt and
    must label the report RESEARCH-ONLY (the preflight manifest carries the
    label).  Undeclared tables fail closed.  Tier is runtime policy read
    from the current sources.yml, never from the frozen spec.
    """
    engineering = mode in ("engineering", "ENGINEERING")
    untrusted_tables = {
        str(record["table"])
        for record in downgrade_records
        if record.get("status") == "UNTRUSTED"
    }
    codes: list[str] = []
    for table in sorted(set(input_tables)):
        contract = contracts.get(table)
        if contract is None:
            codes.append("table_tier_undeclared")
            continue
        tier = getattr(contract, "tier", None)
        if tier == TIER_RESEARCH_ONLY and not engineering:
            codes.append("table_tier_research_only")
        elif tier == TIER_ANCHORED and table in untrusted_tables and not engineering:
            codes.append("table_tier_untrusted")
    return codes


@dataclass(frozen=True)
class UniversePreflight:
    """The frozen universe definition a formal run was accepted under.

    Produced once during the preflight (before the experiment identity is
    computed): the validated :class:`UniverseDefinition`, its signal-day
    :class:`UniverseResolver`, the mandatory ``index_membership_evidence``
    verdict and the per-signal-day member counts / snapshot-hash maps that
    every artifact persists.
    """

    definition: UniverseDefinition
    resolver: UniverseResolver
    universe_version: str
    expected_size: int | None
    acceptance: AcceptanceResult
    daily_member_counts: dict[str, int]
    daily_snapshots: dict[str, str]


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
            + _factor_input_paragraph(metrics)
            + _data_acceptance_paragraph(metrics)
            + "<table><thead><tr><th>scenario</th><th>start</th><th>end</th>"
            "<th>periods</th><th>end_equity</th><th>total_return</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></body></html>\n"
        )


def _factor_input_paragraph(metrics: dict[str, object]) -> str:
    """An escaped 因子价格口径 line over the persisted factor-input audit.

    The rebuilt rich report renders ``metrics["factor_input"]`` as a full
    section; this paragraph keeps the lightweight report produced directly by
    ``research run`` aligned on the same adjustment basis, factor versions and
    break count.  Every interpolated value goes through ``_html`` because
    invalid-reason strings are data, not markup.
    """
    audit = metrics.get("factor_input")
    if not isinstance(audit, Mapping):
        return ""
    versions = audit.get("factor_versions")
    version_text = ""
    if isinstance(versions, Mapping):
        version_text = ", ".join(
            f"{name}: {versions[name]}" for name in sorted(versions)
        )
    return (
        "<p>因子价格口径: "
        f"调整方法 {_html(audit.get('adjustment'))}；"
        f"因子版本 {_html(version_text)}；"
        f"输入行数 {_html(audit.get('row_count'))}；"
        f"不可信断点 {_html(audit.get('error_break_count'))}</p>"
    )


def _data_acceptance_paragraph(metrics: dict[str, object]) -> str:
    """An escaped 真实数据验收 status line over the pinned acceptance audit.

    The rebuilt rich report renders ``metrics["data_acceptance"]`` as a full
    section; this one line keeps the lightweight report produced directly by
    ``research run`` aligned on the same acceptance identity.  A run without a
    concrete accepted decision -- engineering runs and older metrics alike --
    prints UNVERIFIED: the lightweight report never infers ACCEPTED from
    missing data.
    """
    audit = metrics.get("data_acceptance")
    if isinstance(audit, Mapping) and audit.get("decision"):
        return (
            "<p>真实数据验收: "
            f"{_html(audit.get('decision'))}；"
            f"规则 {_html(audit.get('policy_version'))}；"
            f"验收 ID {_html(audit.get('acceptance_id'))}；"
            f"操作者 {_html(audit.get('operator_id'))}；"
            f"时间 {_html(audit.get('created_at'))}</p>"
        )
    return "<p>真实数据验收: UNVERIFIED（没有绑定真实数据验收记录）</p>"


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
        self._universe_preflight: UniversePreflight | None = None
        # The table-tier preflight summary (spec A2 consumer gate), computed
        # before identity and persisted with the run workspace on ``_begin``;
        # ``None`` when the preflight rejected the run.
        self._table_tier_preflight: dict[str, object] | None = None
        # Factors constructed for the preflight input lookup, reused by the
        # factor stage so one run touches the provider exactly once; ``None``
        # on resume (the provider is never touched).
        self._prefetched_factors: Mapping[str, Factor] | None = None
        # The frozen snapshot bundle (identity scheme v2), built once per run
        # after the spec freezes and before any identity is computed.
        self._bundle = None
        # The sanitized audit of the acceptance record resolved before the
        # experiment identity is frozen; ``None`` when no acceptance was
        # pinned (an ENGINEERING diagnostic or a preflight failure).
        self._data_acceptance: dict[str, object] | None = None

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
        spec, dataset_version = self._pin_inputs(spec_path)
        # The universe preflight runs before identity: the frozen definition
        # must validate against the pinned dataset's membership evidence and
        # the mandatory acceptance gate before ``universe_version`` (and with
        # it the experiment id) can be frozen, and any rejection must stop the
        # run before a single factor is computed.
        try:
            preflight = self._preflight_universe(spec, dataset_version)
        except Exception as error:  # noqa: BLE001 - any rejection is audited
            self._fail_universe_preflight(spec, dataset_version, mode, error)
            raise ResearchRunFailed(
                f"research run failed at stage "
                f"{_STAGE_UNIVERSE_ACCEPTANCE}: "
                f"{redact_text(error, self._secrets)}",
                run_id=self._run_id,
                failed_stage=_STAGE_UNIVERSE_ACCEPTANCE,
                retriable=not isinstance(error, (TypeError, ValueError)),
            ) from error
        universe_version = (
            preflight.universe_version
            if preflight is not None
            else self._legacy_universe_version(spec)
        )
        # The real-data acceptance gate runs after dataset pinning and the
        # universe preflight, but before experiment identity/factor work: a
        # RESEARCH run must resolve (and re-verify) its acceptance record
        # here, and any failure leaves only a FAILED preflight manifest -- no
        # factor provider, portfolio builder or backtest engine is ever
        # invoked.
        acceptance_id = spec.data_acceptance_id
        self._data_acceptance = None
        if (
            mode is DataTrustMode.RESEARCH
            or acceptance_id not in (None, CURRENT_ACCEPTED)
        ):
            selected = self._resolve_acceptance(
                dataset_version, acceptance_id or CURRENT_ACCEPTED,
                universe_version=universe_version,
                trust_mode=mode,
            )
            acceptance_id = selected.acceptance_id
            self._data_acceptance = acceptance_audit_dict(selected)
        elif mode is DataTrustMode.ENGINEERING:
            acceptance_id = None
        frozen = spec.freeze(
            dataset_version=dataset_version,
            universe_version=universe_version,
            data_acceptance_id=acceptance_id,
            code_commit=self._detect_code_commit() or spec.code_commit,
            trust_mode=mode,
        )
        self._universe_preflight = preflight
        # Freeze the three research snapshots before the identity is computed:
        # identity scheme v2 hashes the snapshot bundle beside the spec.  A
        # dataset that cannot support the snapshots (e.g. a legacy dataset
        # without adjusted_bar) fails closed here, audited as a FAILED
        # preflight manifest with no experiment identity.
        try:
            self._bundle = self._build_snapshot_bundle(frozen)
        except Exception as error:  # noqa: BLE001 - any rejection is audited
            self._close_context()
            self._record_snapshot_preflight_failure(frozen, mode, error)
            raise ResearchRunFailed(
                "research run failed to freeze the snapshot bundle: "
                f"{redact_text(error, self._secrets)}",
                run_id=self._run_id,
                failed_stage="snapshot_bundle",
                retriable=False,
            ) from error
        # The table-tier preflight runs after the acceptance gate and after
        # the identity inputs are frozen, but before any run workspace is
        # created (spec A2 / D1 consumer gate): RESEARCH runs referencing
        # research_only or untrusted-anchored tables fail here with a
        # redacted preflight manifest; ENGINEERING runs are exempt and
        # labeled RESEARCH-ONLY.  A resumed run whose factor stage is intact
        # skips the preflight: the frozen experiment was judged under the
        # policy of its first run and is not re-judged retroactively (spec
        # A2), and the factor provider is never touched on resume.
        resume_run_dir = (
            self._project_root
            / "data"
            / "runs"
            / f"run_{ExperimentIdentity.of(frozen, self._bundle).experiment_id}"
        )
        if not self._factor_stage_will_resume(
            resume_run_dir, self._run_digest(frozen)
        ):
            try:
                self._table_tier_preflight = self._preflight_table_tiers(
                    frozen, dataset_version, mode
                )
            except TableTierPreflightFailed as error:
                raise ResearchRunFailed(
                    f"research run failed at stage {_STAGE_TABLE_TIERS}: "
                    f"{redact_text(error, self._secrets)}",
                    run_id=self._run_id,
                    failed_stage=_STAGE_TABLE_TIERS,
                    retriable=False,
                ) from error
        state = self._begin(frozen)
        try:
            self._active_stage = None
            if frozen.execution_pipeline == "walk_forward_oos_v1":
                self._walk_forward_pipeline(state, frozen, observer)
            else:
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

    def _pin_inputs(self, spec_path: str | Path) -> tuple[ExperimentSpec, str]:
        """Load the spec and pin the dataset version exactly once.

        The returned spec may still request ``CURRENT`` for the universe; the
        caller resolves that through the definition preflight (formal runs) or
        the legacy engineering file.  A dataset whose ``CURRENT`` pointer is
        missing raises before any run workspace is created.
        """
        path = Path(spec_path)
        if not path.is_absolute():
            path = self._config_root / path
        spec = load_experiment_spec(path)
        dataset_version = spec.dataset_version
        if dataset_version == _CURRENT:
            dataset_version = DatasetPublisher(self._project_root).current().version
        return spec, dataset_version

    def _legacy_universe_version(self, spec: ExperimentSpec) -> str:
        """Resolve a ``CURRENT`` universe through the legacy engineering file.

        This is the engineering-only path: a formal spec names a
        ``universe_definition`` and resolves through a frozen
        :class:`UniverseDefinition` instead (see ``_preflight_universe``).
        """
        if spec.universe_version != _CURRENT:
            return spec.universe_version
        universe = Universe.from_yaml(self._config_root / "configs"
                                      / "universe.yml")
        return universe.version

    def _preflight_universe(
        self, spec: ExperimentSpec, dataset_version: str
    ) -> UniversePreflight | None:
        """Validate the selected universe definition against the pinned data.

        Loads ``configs/universes/<universe_definition>.yml``, opens the fixed
        dataset context and evaluates the mandatory
        ``index_membership_evidence`` result (evidence, boundaries, coverage
        and cardinality) plus the pinned membership-table hash *before* any
        factor computation, then freezes ``universe_version`` to the
        definition's content-derived version.  Any rejection raises
        :class:`UniversePreflightFailed` (or the underlying error) -- the run
        never falls back to the master's full symbol list.
        """
        if spec.universe_definition is None:
            return None
        definition_path = (
            self._config_root / "configs" / "universes"
            / f"{spec.universe_definition}.yml"
        )
        definition = load_universe_definition(definition_path)
        # Pin the dataset context once: every later stage reads the same
        # immutable version through the already-open context.
        self._frozen_version = dataset_version
        context = self._open_context(dataset_version)
        frame = read_membership_table(context)
        calendar = self._calendar()
        boundaries = self._master_boundaries(context)
        size = _CANONICAL_UNIVERSE_SIZES.get(definition.universe_id)
        expected_sizes = (
            {definition.universe_id: size} if size is not None else {}
        )
        result = evaluate_index_membership_evidence(
            frame,
            definition=definition,
            calendar=calendar,
            expected_sizes=expected_sizes,
            master=boundaries,
        )
        try:
            enforce_required_results([result])
        except AcceptanceGateError as error:
            codes = tuple(result.details.get("error_codes") or ())
            raise UniversePreflightFailed(
                str(error), error_codes=codes,
                universe_id=definition.universe_id,
            ) from error
        facts = _facts_from_membership_frame(frame)
        resolver = UniverseResolver(
            definition, resolve_memberships(facts, boundaries), facts=facts
        )
        if spec.universe_version != _CURRENT and \
                spec.universe_version != definition.version:
            raise UniversePreflightFailed(
                f"spec universe_version {spec.universe_version!r} does not "
                f"match the {definition.universe_id} definition version "
                f"{definition.version!r}",
                universe_id=definition.universe_id,
            )
        signals = self._signals(spec)
        return UniversePreflight(
            definition=definition,
            resolver=resolver,
            universe_version=definition.version,
            expected_size=size,
            acceptance=result,
            daily_member_counts={
                day.isoformat(): len(resolver.members_on(day))
                for day in signals
            },
            daily_snapshots={
                day.isoformat(): resolver.snapshot_for(day) for day in signals
            },
        )

    def _master_boundaries(
        self, context: DatasetContext
    ) -> dict[str, SecurityMasterBoundary]:
        """Fixed master boundaries for the acceptance check.

        Only the listing date is applied: ``security_master.delist_date``
        carries no proven last-tradable-date semantics in this schema, so it
        is never used to close a membership interval (a delisting removal
        without other proof fails the gate instead of being guessed).
        """
        master = context.read("security_master")
        boundaries: dict[str, SecurityMasterBoundary] = {}
        for record in master.to_dict("records"):
            symbol = str(record["symbol"])
            raw_list = record.get("list_date")
            list_date = (
                None
                if raw_list is None or pd.isna(raw_list)
                else _as_date(raw_list)
            )
            boundaries[symbol] = SecurityMasterBoundary(
                symbol=symbol, list_date=list_date, last_tradable_date=None
            )
        return boundaries

    def _fail_universe_preflight(
        self,
        spec: ExperimentSpec,
        dataset_version: str,
        mode: DataTrustMode,
        error: Exception,
    ) -> None:
        """Audit a preflight rejection without producing any factor artifact.

        Writes a FAILED run manifest plus the redacted preflight record
        (``universe_preflight.json``) under a deterministic preflight run id:
        only stable error codes and the redacted message are recorded, never
        membership rows or evidence payloads.
        """
        self._active_stage = _STAGE_UNIVERSE_ACCEPTANCE
        self._close_context()
        digest_payload = json.dumps(
            {
                "spec": spec.model_dump(mode="json"),
                "dataset_version": dataset_version,
                "trust_mode": mode.value,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        run_id = (
            "run_preflight_"
            + hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:16]
        )
        run_dir = self._project_root / "data" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._run_dir = run_dir
        self._logger = StructuredLogger(
            run_dir / ".run.log.jsonl",
            terminal=False,
            secrets=self._secrets,
        )
        message = redact_text(str(error), self._secrets)
        state = RunState(
            run_id=run_id,
            experiment_id="",
            status=RunStatus.FAILED,
            stage=DataStage.FAILED,
            dataset_version=dataset_version,
            universe_version=spec.universe_version,
            code_commit=spec.code_commit,
            factor_versions=dict(spec.factor_versions),
            cost_scenarios=list(spec.cost_scenarios),
            random_seed=spec.random_seed,
            parent_experiment_ids=list(spec.parent_experiment_ids),
            agent_id=spec.agent_id,
            trust_mode=mode.value,
            failed_stage=_STAGE_UNIVERSE_ACCEPTANCE,
            error={
                "stage": _STAGE_UNIVERSE_ACCEPTANCE,
                "exception_class": type(error).__name__,
                "message": message,
                "retriable": not isinstance(error, (TypeError, ValueError)),
            },
        )
        write_run_manifest(run_dir, state)
        preflight = {
            "status": "FAIL",
            "failed_stage": _STAGE_UNIVERSE_ACCEPTANCE,
            "dataset_version": dataset_version,
            "universe_definition": spec.universe_definition,
            "universe_id": getattr(error, "universe_id", None),
            "universe_version": None,
            "error_codes": list(getattr(error, "error_codes", ())),
            "error": {
                "exception_class": type(error).__name__,
                "message": message,
                "retriable": not isinstance(error, (TypeError, ValueError)),
            },
        }
        (run_dir / "universe_preflight.json").write_text(
            json.dumps(preflight, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._logger.error(
            f"universe preflight failed: {type(error).__name__}: {message}",
            run_id=run_id,
            stage=_STAGE_UNIVERSE_ACCEPTANCE,
            event="universe_preflight_failed",
        )

    def _factor_stage_will_resume(self, run_dir: Path, digest: str) -> bool:
        """Whether a prior run at ``run_dir`` would resume-skip its factor stage.

        Mirrors the ``_run_stage`` resume test for the ``"factor"`` label: the
        stage record exists, its ``input_hash`` matches ``digest`` and every
        recorded output is intact.  The table-tier preflight uses this to stay
        off a resumed run: the frozen experiment was judged under the tier
        policy of its first run (spec A2: no retroactive re-judgement) and the
        factor provider is never touched on resume.
        """
        path = run_dir / _STAGES_FILE
        if not path.is_file():
            return False
        records = json.loads(path.read_text(encoding="utf-8"))
        record = records.get("factor")
        if not record or record.get("input_hash") != digest:
            return False
        for relative, expected in dict(record.get("outputs", {})).items():
            output = run_dir / relative
            if not output.is_file() or _sha256_file(output) != expected:
                return False
        return True

    def _preflight_table_tiers(
        self,
        spec: ExperimentSpec,
        dataset_version: str,
        mode: DataTrustMode,
    ) -> dict[str, object]:
        """Route the run's factor input tables through the current tier policy.

        Returns the preflight summary (persisted with the run workspace on
        ``_begin``): declared inputs, research-only usage and the engineering
        exemption label.  Raises :class:`TableTierPreflightFailed` in RESEARCH
        mode when any input table is research_only or untrusted-anchored, and
        in either mode when a table has no declaration (spec A2 / D1).
        """
        contracts = self._project_config.data_contracts
        provider = self._factor_provider()
        # The factor stage reuses this construction (``_resolve_factors``) so
        # one run touches the provider exactly once: the preflight's input
        # lookup is not separate factor work.
        self._prefetched_factors = provider
        inputs = factor_input_tables(
            {
                name: provider[name]
                for name in spec.factor_versions
                if name in provider
            }
        )
        input_tables = sorted(
            {table for tables in inputs.values() for table in tables}
        )
        # The pinned quality report carries the coverage_downgraded records
        # (D1 publish side); each record is the issue's details plus the
        # issue's table name (the details payload itself is table-blind).
        # Only those records are parsed, never the report prose.
        quality_path = (
            self._project_root
            / "data"
            / "standardized"
            / dataset_version
            / "quality_report.json"
        )
        downgrade_records: list[dict[str, object]] = []
        if quality_path.is_file():
            report = json.loads(quality_path.read_text(encoding="utf-8"))
            downgrade_records = [
                {**item.get("details", {}), "table": item.get("table")}
                for item in report.get("issues", [])
                if item.get("code") == CODE_COVERAGE_DOWNGRADED
            ]
        violations = table_tier_violations(
            contracts, input_tables, downgrade_records, mode.value
        )
        # ENGINEERING exempts research_only/untrusted-anchored inputs, so the
        # exemption label is judged against RESEARCH semantics: exempt exactly
        # when the same inputs would have been rejected under RESEARCH.
        engineering = mode is DataTrustMode.ENGINEERING
        research_codes = (
            table_tier_violations(
                contracts,
                input_tables,
                downgrade_records,
                DataTrustMode.RESEARCH.value,
            )
            if engineering
            else violations
        )
        engineering_exempt = engineering and any(
            code in research_codes
            for code in ("table_tier_research_only", "table_tier_untrusted")
        )
        summary: dict[str, object] = {
            "dataset_version": dataset_version,
            "mode": mode.value,
            "input_tables": input_tables,
            "research_only_used": any(
                getattr(contracts.get(table), "tier", None) == TIER_RESEARCH_ONLY
                for table in input_tables
            ),
            "engineering_exempt": engineering_exempt,
            "label": "RESEARCH-ONLY" if engineering_exempt else None,
            "violations": violations,
        }
        if violations:
            self._write_table_tier_preflight(summary, failed=True, spec=spec)
            raise TableTierPreflightFailed(
                "table-tier preflight rejected input tables: "
                + ", ".join(violations),
                error_codes=violations,
            )
        return summary

    def _write_table_tier_preflight_record(self) -> None:
        """Persist the PASS tier-preflight summary in the run workspace."""
        summary = self._table_tier_preflight
        if summary is None:
            return
        self._write_table_tier_preflight(summary, failed=False)

    def _write_table_tier_preflight(
        self,
        summary: Mapping[str, object],
        *,
        failed: bool,
        spec: ExperimentSpec | None = None,
    ) -> None:
        """Persist the (redacted) tier-preflight manifest for audit.

        A PASS record lands in the run workspace (written on ``_begin``); a
        FAIL record lands under a deterministic preflight run id together
        with a FAILED run manifest, mirroring :meth:`_fail_universe_preflight`
        -- only stable error codes and the mode summary are recorded, never
        data or evidence payloads.
        """
        payload = dict(summary)
        payload["failed"] = failed
        if not failed:
            path = self._run_dir / "table_tier_preflight.json"
            if (
                path.exists()
                and json.loads(path.read_text(encoding="utf-8")) != payload
            ):
                raise ValueError(
                    "table_tier_preflight.json already exists with different "
                    f"content: {path}"
                )
            path.write_text(
                json.dumps(
                    payload, ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n",
                encoding="utf-8",
            )
            return
        self._active_stage = _STAGE_TABLE_TIERS
        self._close_context()
        digest_payload = json.dumps(
            {
                "spec": spec.model_dump(mode="json") if spec is not None else None,
                "dataset_version": payload.get("dataset_version"),
                "trust_mode": payload.get("mode"),
                "stage": _STAGE_TABLE_TIERS,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        run_id = (
            "run_preflight_"
            + hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:16]
        )
        run_dir = self._project_root / "data" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._run_dir = run_dir
        self._logger = StructuredLogger(
            run_dir / ".run.log.jsonl",
            terminal=False,
            secrets=self._secrets,
        )
        message = (
            "table-tier preflight rejected input tables: "
            + ", ".join(str(code) for code in payload.get("violations", ()))
        )
        state = RunState(
            run_id=run_id,
            experiment_id="",
            status=RunStatus.FAILED,
            stage=DataStage.FAILED,
            dataset_version=payload.get("dataset_version"),
            universe_version=spec.universe_version if spec is not None else None,
            code_commit=spec.code_commit if spec is not None else None,
            factor_versions=(
                dict(spec.factor_versions) if spec is not None else {}
            ),
            cost_scenarios=(
                list(spec.cost_scenarios) if spec is not None else []
            ),
            random_seed=spec.random_seed if spec is not None else None,
            parent_experiment_ids=(
                list(spec.parent_experiment_ids) if spec is not None else []
            ),
            agent_id=spec.agent_id if spec is not None else None,
            trust_mode=payload.get("mode"),
            failed_stage=_STAGE_TABLE_TIERS,
            error={
                "stage": _STAGE_TABLE_TIERS,
                "exception_class": "TableTierPreflightFailed",
                "message": message,
                "retriable": False,
            },
        )
        write_run_manifest(run_dir, state)
        payload["status"] = "FAIL"
        payload["failed_stage"] = _STAGE_TABLE_TIERS
        (run_dir / "table_tier_preflight.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._logger.error(
            f"table-tier preflight failed: {message}",
            run_id=run_id,
            stage=_STAGE_TABLE_TIERS,
            event="table_tier_preflight_failed",
        )

    def _resolve_acceptance(
        self,
        dataset_version: str,
        requested_id: str,
        *,
        universe_version: str,
        trust_mode: DataTrustMode,
    ) -> AcceptanceRecord:
        """Select and re-verify the pinned acceptance record (read-only)."""
        try:
            selected = AcceptanceRegistry(self._project_root).select(
                dataset_version, requested_id
            )
            verify_acceptance_bindings(self._project_root, selected)
        except (
            NoValidAcceptance,
            AcceptanceNotFound,
            AcceptanceIntegrityError,
            AcceptanceBindingError,
            ValueError,
        ) as error:
            # The universe preflight may have opened the pinned dataset
            # context; a rejected acceptance stops the run here, so release
            # it before auditing the failure.
            self._close_context()
            self._record_acceptance_preflight_failure(
                dataset_version=dataset_version,
                universe_version=universe_version,
                trust_mode=trust_mode,
                error=error,
            )
            raise ResearchRunFailed(
                "no valid real-data-v1 acceptance for dataset "
                f"{dataset_version}: {type(error).__name__}: "
                f"{redact_text(error, self._secrets)}",
                run_id=self._run_id,
                failed_stage="acceptance",
                retriable=False,
            ) from error
        return selected

    def _record_acceptance_preflight_failure(
        self,
        *,
        dataset_version: str,
        universe_version: str,
        trust_mode: DataTrustMode,
        error: Exception,
    ) -> None:
        """Persist the FAILED preflight manifest for an acceptance-gate stop.

        No experiment identity exists yet, so the run id is a fresh
        ``preflight_acceptance_<uuid>`` and the recorded ``experiment_id`` is
        ``None``; the resolved ``trust_mode`` is stamped so an ENGINEERING run
        that pinned a concrete-but-invalid acceptance is not mislabelled with
        the default research mode.  The redacted error keeps the failure
        auditable without ever carrying secrets or absolute paths.
        """
        run_id = f"preflight_acceptance_{uuid.uuid4().hex}"
        run_dir = self._project_root / "data" / "runs" / run_id
        state = RunState(
            run_id=run_id,
            experiment_id=None,
            status=RunStatus.FAILED,
            stage=DataStage.FAILED,
            dataset_version=dataset_version,
            universe_version=universe_version,
            trust_mode=trust_mode.value,
            failed_stage="acceptance",
            error={
                "stage": "acceptance",
                "exception_class": type(error).__name__,
                "message": redact_text(str(error), self._secrets),
                "retriable": False,
            },
        )
        write_run_manifest(run_dir, state)
        self._run_id = run_id
        self._run_dir = run_dir

    def _record_snapshot_preflight_failure(
        self,
        frozen: ExperimentSpec,
        mode: DataTrustMode,
        error: Exception,
    ) -> None:
        """Persist the FAILED manifest for a snapshot-bundle freeze rejection.

        No experiment identity exists yet, so the run id is a fresh
        ``preflight_snapshot_<uuid>`` and the recorded ``experiment_id`` is
        ``None``; the redacted error keeps the failure auditable.
        """
        run_id = f"preflight_snapshot_{uuid.uuid4().hex}"
        run_dir = self._project_root / "data" / "runs" / run_id
        state = RunState(
            run_id=run_id,
            experiment_id=None,
            status=RunStatus.FAILED,
            stage=DataStage.FAILED,
            dataset_version=frozen.dataset_version,
            universe_version=frozen.universe_version,
            trust_mode=mode.value,
            failed_stage="snapshot_bundle",
            error={
                "stage": "snapshot_bundle",
                "exception_class": type(error).__name__,
                "message": redact_text(str(error), self._secrets),
                "retriable": False,
            },
        )
        write_run_manifest(run_dir, state)
        self._run_id = run_id
        self._run_dir = run_dir

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

    def _build_snapshot_bundle(self, frozen: ExperimentSpec):
        """Freeze the three research snapshots for the identity (scheme v2).

        Reads the pinned dataset version's published manifest for the table
        content hashes and combines it with the frozen universe definition
        (when preflighted) and the consumed config hashes.  Deterministic in
        its inputs: no path, clock, host or worker count enters the bundle.
        """
        manifest_path = (
            self._project_root
            / "data"
            / "standardized"
            / frozen.dataset_version
            / "dataset_manifest.json"
        )
        dataset_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return build_snapshot_bundle(
            spec=frozen,
            dataset_manifest=dataset_manifest,
            universe_definition=(
                self._universe_preflight.definition
                if self._universe_preflight is not None
                else None
            ),
            config_hashes=self._config_hashes(frozen),
        )

    def _begin(self, frozen: ExperimentSpec) -> RunState:
        identity = ExperimentIdentity.of(frozen, self._bundle)
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
            universe=self._universe_record(frozen),
            data_acceptance=self._data_acceptance,
            code_commit=frozen.code_commit,
            factor_versions=dict(frozen.factor_versions),
            cost_scenarios=list(frozen.cost_scenarios),
            random_seed=frozen.random_seed,
            parent_experiment_ids=list(frozen.parent_experiment_ids),
            agent_id=frozen.agent_id,
            trust_mode=frozen.trust_mode.value,
        )
        write_run_manifest(run_dir, state)
        self._write_universe_preflight_record(frozen)
        self._write_table_tier_preflight_record()
        self._digest = self._run_digest(frozen)
        return state

    def _universe_record(self, frozen: ExperimentSpec) -> dict[str, object] | None:
        """The frozen definition identity persisted with the run manifest.

        Carries the definition identity, the acceptance verdict and the
        per-signal-day member counts / snapshot-hash maps, so a run manifest
        alone locates the frozen universe and every day's membership hash.
        """
        preflight = self._universe_preflight
        if preflight is None:
            return None
        definition = preflight.definition
        return {
            "universe_id": definition.universe_id,
            "universe_version": frozen.universe_version,
            "rules_version": definition.rules_version,
            "membership_table_sha256": definition.membership_table_sha256,
            "evidence_summary_sha256": definition.evidence_summary_sha256,
            "coverage_start": definition.coverage_start.isoformat(),
            "coverage_end": definition.coverage_end.isoformat(),
            "expected_size": preflight.expected_size,
            "acceptance_status": preflight.acceptance.status.value,
            "daily_member_counts": dict(preflight.daily_member_counts),
            "daily_snapshots": dict(preflight.daily_snapshots),
        }

    def _universe_identity(self, frozen: ExperimentSpec) -> dict[str, object]:
        """The universe identity block for metrics/config snapshot artifacts."""
        preflight = self._universe_preflight
        if preflight is None:
            return {}
        definition = preflight.definition
        return {
            "universe_id": definition.universe_id,
            "universe_version": frozen.universe_version,
            "rules_version": definition.rules_version,
            "membership_table_sha256": definition.membership_table_sha256,
            "evidence_summary_sha256": definition.evidence_summary_sha256,
            "coverage_start": definition.coverage_start.isoformat(),
            "coverage_end": definition.coverage_end.isoformat(),
            "expected_size": preflight.expected_size,
        }

    def _write_universe_preflight_record(self, frozen: ExperimentSpec) -> None:
        """Persist the redacted PASS preflight record in the run workspace."""
        preflight = self._universe_preflight
        if preflight is None:
            return
        definition = preflight.definition
        record = {
            "status": "PASS",
            "failed_stage": None,
            "dataset_version": frozen.dataset_version,
            "universe_definition": frozen.universe_definition,
            "universe_id": definition.universe_id,
            "universe_version": frozen.universe_version,
            "rules_version": definition.rules_version,
            "membership_table_sha256": definition.membership_table_sha256,
            "coverage_start": definition.coverage_start.isoformat(),
            "coverage_end": definition.coverage_end.isoformat(),
            "acceptance_summary": preflight.acceptance.summary,
            "error_codes": [],
        }
        (self._run_dir / "universe_preflight.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _run_digest(self, frozen: ExperimentSpec) -> str:
        """One deterministic sha over every input the stages consume."""
        payload = {
            "frozen_spec": frozen.model_dump(mode="json"),
            "config_file_hashes": self._config_hashes(frozen),
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

    def _walk_forward_pipeline(
        self,
        state: RunState,
        frozen: ExperimentSpec,
        observer,
    ) -> None:
        """The formal ``walk_forward_oos_v1`` pipeline.

        Freezing, the dataset acceptance gate and the point-in-time universe
        preflight already ran before the identity was computed.  The runner
        then only orchestrates: it materializes the immutable fold schedule,
        delegates isolated fold execution to :class:`WalkForwardRunner` and
        writes the stability report.  It never selects parameters, rewrites
        the calendar or bypasses a gate.
        """
        self._frozen_version = frozen.dataset_version
        self._universe_symbols = self._load_universe_symbols()
        self._enforce_research_master_evidence(frozen)
        producers = {
            "pin": lambda: self._produce_pin(frozen),
            "schedule": lambda: self._produce_walk_forward_schedule(frozen),
            "folds": lambda: self._produce_walk_forward_folds(state, frozen),
            "report": lambda: self._produce_walk_forward_report(state, frozen),
        }
        for label in _WF_STAGE_LABELS:
            self._active_stage = label
            self._run_stage(
                label, _WF_LABEL_TO_DATASTAGE[label], state, producers[label],
                observer,
            )
        self._active_stage = None

    def _produce_walk_forward_schedule(
        self, frozen: ExperimentSpec
    ) -> dict[str, str]:
        """Materialize the immutable fold schedule (written by the fold runner)."""
        if self._universe_preflight is None:
            raise ValueError(
                "a formal walk-forward run requires a frozen universe "
                "definition; the legacy engineering universe cannot provide "
                "point-in-time fold membership"
            )
        schedule = self._materialize_walk_forward_schedule(frozen)
        if not schedule.folds:
            raise ValueError(
                "the requested range "
                f"{frozen.date_range.start_date}..{frozen.date_range.end_date} "
                "contains no complete 12-month OOS fold; a walk-forward run "
                "needs at least one complete calendar year"
            )
        return {}

    def _materialize_walk_forward_schedule(
        self, frozen: ExperimentSpec
    ) -> FoldSchedule:
        """The deterministic fold schedule over the pinned calendar."""
        preflight = self._universe_preflight
        return materialize_schedule(
            requested_start=frozen.date_range.start_date,
            requested_end=frozen.date_range.end_date,
            calendar=self._calendar(),
            policy=WalkForwardPolicy(),
            membership_snapshots=dict(preflight.daily_snapshots),
        )

    def _produce_walk_forward_folds(
        self, state: RunState, frozen: ExperimentSpec
    ) -> dict[str, str]:
        """Delegate isolated fold execution; the fold runner owns its artifacts."""
        schedule = self._materialize_walk_forward_schedule(frozen)
        first = schedule.folds[0]
        last = schedule.folds[-1]
        window_start = first.first_trading_day or first.calendar_start
        window_end = last.last_trading_day or last.calendar_end
        market = self._walk_forward_market(frozen, window_start, window_end)
        scenarios = {
            scenario: CostModel.from_config(self._project_config.costs, scenario)
            for scenario in frozen.cost_scenarios
        }
        factors = {factor.name: factor for factor in self._resolve_factors(frozen)}
        rule = frozen.portfolio_rule
        if isinstance(rule, BufferedRiskWeightedPortfolioRule):
            # The buffered rule builds its own common weight-target periods
            # from the frozen policy inside the fold runner; there is no
            # equal-weight fallback to hand the request.
            builder = None
        else:
            builder = TopNEqualWeight(top_n=rule.top_n, lot_size=rule.lot_size)
        request = WalkForwardRequest(
            run_dir=self._run_dir,
            spec=frozen,
            schedule=schedule,
            snapshot_bundle=self._bundle,
            walk_forward_policy=WalkForwardPolicy(),
            stability_policy=StabilityPolicy(),
            calendar=self._calendar(),
            rule_book=self._rule_book,
            initial_cash=float(self._project_config.initial_cash),
            dataset_version=frozen.dataset_version,
            universe_symbols=self._universe_symbols,
            bars=market.bars,
            benchmarks=market.benchmarks,
            benchmark_symbols=tuple(self._project_config.benchmark_symbols),
            corporate_actions=market.corporate_actions,
            factors=factors,
            factor_input_provider=self._walk_forward_factor_input(frozen),
            portfolio_builder=builder,
            cost_models=scenarios,
            universe_resolver=self._universe_preflight.resolver,
            acceptance_audit=self._walk_forward_acceptance_audit(frozen),
        )
        WalkForwardRunner().run(request)
        outputs: dict[str, str] = {}
        for name in (
            "fold_schedule.json",
            "fold_outcomes.json",
            "walk_forward_manifest.json",
        ):
            outputs[name] = sha256_file(self._run_dir / name)
        for fold in schedule.folds:
            fold_dir = self._run_dir / "folds" / fold.fold_id
            if not fold_dir.is_dir():
                continue  # a fold that failed preflight publishes no assets
            for name in FOLD_ARTIFACTS:
                path = fold_dir / name
                if path.is_file():
                    outputs[f"folds/{fold.fold_id}/{name}"] = sha256_file(path)
            for scenario in frozen.cost_scenarios:
                path = fold_dir / "backtest" / scenario / \
                    "rebalance_decisions.parquet"
                if path.is_file():
                    key = (
                        f"folds/{fold.fold_id}/backtest/{scenario}/"
                        "rebalance_decisions.parquet"
                    )
                    outputs[key] = sha256_file(path)
        return outputs

    def _walk_forward_factor_input(self, frozen: ExperimentSpec):
        """The pinned adjusted-bar factor input provider for the fold runner."""
        def provider() -> pd.DataFrame:
            adapter = _DatasetFactorAdapter(
                context=self._open_context(frozen.dataset_version),
                universe_symbols=self._universe_symbols,
            )
            return adapter.factor_input()

        return provider

    def _walk_forward_acceptance_audit(
        self, frozen: ExperimentSpec
    ) -> dict[str, object] | None:
        """The pinned acceptance audit, bound to the pinned dataset version."""
        if self._data_acceptance is None:
            return None
        return {
            "dataset_version": frozen.dataset_version,
            **dict(self._data_acceptance),
        }

    def _walk_forward_market(
        self, frozen: ExperimentSpec, window_start: date, window_end: date
    ):
        """The pinned OOS market frames (bars restricted to the fold span)."""
        context = self._open_context(frozen.dataset_version)
        daily = context.read("daily_bar")
        equity = daily[daily["symbol"].isin(self._universe_symbols)].copy()
        equity["trade_date"] = equity["trade_date"].map(_as_date)
        equity = equity[
            (equity["trade_date"] >= window_start)
            & (equity["trade_date"] <= window_end)
        ].copy()
        if equity.empty:
            raise ValueError("no equity bars cover the walk-forward OOS window")
        equity = equity.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
        bars = pd.DataFrame({
            "symbol": equity["symbol"],
            "trade_date": equity["trade_date"],
            "open": equity["open"],
            "close": equity["close"],
            # Phase-one ruling (§13.5): the primary close series is
            # authoritative; ERROR bars never reach the backtest.
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
        return _MarketFrames(
            calendar=self._calendar(),
            bars=bars,
            benchmarks=benchmarks,
            corporate_actions=context.read("corporate_action"),
        )

    def _produce_walk_forward_report(
        self, state: RunState, frozen: ExperimentSpec
    ) -> dict[str, str]:
        """Build ``stability_report.json``, the evaluation and the report."""
        schedule = self._materialize_walk_forward_schedule(frozen)
        ledger = FoldOutcomeLedger.model_validate(
            json.loads(
                (self._run_dir / "fold_outcomes.json").read_text(encoding="utf-8")
            )
        )
        windows = {fold.fold_id: fold for fold in schedule.folds}
        scenario_metrics: list[FoldMetrics] = []
        fold_metrics_payload: list[dict] = []
        for fold in schedule.folds:
            metrics_path = self._run_dir / "folds" / fold.fold_id / "metrics.json"
            if not metrics_path.is_file():
                continue  # a fold that failed preflight has no metrics
            payload = json.loads(metrics_path.read_text(encoding="utf-8"))
            for scenario in sorted(payload["scenarios"]):
                metrics = FoldMetrics.model_validate(payload["scenarios"][scenario])
                scenario_metrics.append(metrics)
                fold_metrics_payload.append(metrics.model_dump(mode="json"))
        evaluation = evaluate_stability(
            policy=StabilityPolicy(),
            declared_scenarios=tuple(frozen.cost_scenarios),
            scenario_metrics=scenario_metrics,
            outcomes=ledger.outcomes,
            integrity_failures=(),
        )
        if evaluation.research_status.value == "FAILED":
            raise ValueError(
                "the executed folds failed the stability integrity review: "
                + "; ".join(evaluation.reasons)
            )
        scenario_aggregates: list[dict] = []
        for scenario in frozen.cost_scenarios:
            per_fold: list[tuple[FoldWindow, pd.DataFrame]] = []
            for outcome in ledger.outcomes:
                if outcome.status is not FoldOutcomeStatus.EXECUTED:
                    continue
                fold = windows[outcome.fold_id]
                path = (
                    self._run_dir / "backtest" / fold.fold_id / scenario
                    / "daily_returns.parquet"
                )
                if path.is_file():
                    per_fold.append((fold, pd.read_parquet(path)))
            aggregate = aggregate_oos_returns(per_fold)
            scenario_aggregates.append(
                {"scenario": scenario, **aggregate.model_dump(mode="json")}
            )
        trust_record = self._record_walk_forward_trust(state, frozen, windows)
        reasons = list(evaluation.reasons)
        if evaluation.stability_conclusion == "STABLE":
            status = ExperimentEvaluation.ACCEPTED
            reason = (
                "stability STABLE under the frozen policy "
                f"{evaluation.stability_policy_version} "
                f"(hash {evaluation.stability_policy_hash})"
            )
        else:
            status = ExperimentEvaluation.REJECTED
            reason = (
                f"stability {evaluation.stability_conclusion} under the frozen "
                f"policy {evaluation.stability_policy_version}: "
                + ("; ".join(reasons) if reasons else "thresholds not met")
            )
            reasons.insert(0, f"stability {evaluation.stability_conclusion}")
        boundaries = [
            {
                "calendar_start": window.calendar_start.isoformat(),
                "calendar_end": window.calendar_end.isoformat(),
                "reason": window.reason,
            }
            for window in schedule.boundaries
        ]
        report: dict[str, object] = {
            "research_status": evaluation.research_status.value,
            "stability_conclusion": evaluation.stability_conclusion,
            "stability_policy_hash": evaluation.stability_policy_hash,
            "stability_policy_version": evaluation.stability_policy_version,
            "thresholds": StabilityPolicy().model_dump(mode="json"),
            "integrity_failures": list(evaluation.integrity_failures),
            "skipped_fold_ids": list(evaluation.skipped_fold_ids),
            "reasons": reasons,
            "schedule": {
                "requested_start": schedule.requested_start.isoformat(),
                "requested_end": schedule.requested_end.isoformat(),
                "fold_count": len(schedule.folds),
                "boundary_count": len(schedule.boundaries),
                "boundaries": boundaries,
                "fold_schedule_sha256": ledger.schedule_sha256,
                "fold_outcomes_sha256": sha256_file(
                    self._run_dir / "fold_outcomes.json"
                ),
            },
            "fold_statuses": [
                {
                    "fold_id": outcome.fold_id,
                    "status": outcome.status.value,
                    "reason_code": outcome.reason_code,
                }
                for outcome in ledger.outcomes
            ],
            "scenario_results": [
                result.model_dump(mode="json")
                for result in evaluation.scenario_results
            ],
            "scenario_aggregates": scenario_aggregates,
            "fold_metrics": fold_metrics_payload,
            "experiment_id": state.experiment_id,
            "dataset_version": frozen.dataset_version,
            "universe_version": frozen.universe_version,
        }
        if "aggregate_max_drawdown" in report or "calmar" in report:
            raise ValueError(
                "the stability report can never carry a cross-fold drawdown "
                "or Calmar field"
            )
        buffered = self._buffered_report_block()
        if buffered is not None:
            report["buffered"] = buffered
        report_path = self._run_dir / "stability_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        meta = {
            "run_id": self._run_id,
            "experiment_id": state.experiment_id,
            "spec": frozen.model_dump(mode="json"),
            "dataset_version": frozen.dataset_version,
            "universe_version": frozen.universe_version,
            "universe": self._universe_identity(frozen),
            "code_commit": frozen.code_commit,
            "config_file_hashes": self._config_hashes(frozen),
            "run_input_digest": self._digest,
            "initial_cash": float(self._project_config.initial_cash),
            "benchmark_symbols": list(self._project_config.benchmark_symbols),
            "execution_pipeline": frozen.execution_pipeline,
        }
        metrics = {
            "meta": meta,
            "walk_forward": {
                "research_status": report["research_status"],
                "stability_conclusion": report["stability_conclusion"],
                "stability_policy_hash": report["stability_policy_hash"],
                "schedule": report["schedule"],
                "scenario_aggregates": scenario_aggregates,
                **({"buffered": buffered} if buffered is not None else {}),
            },
            "corporate_action_trust": trust_record,
            "evaluation": {"status": status.value, "reason": reason},
        }
        if frozen.trust_mode is DataTrustMode.ENGINEERING:
            metrics["evaluation"] = {
                "status": ExperimentEvaluation.UNTRUSTED.value,
                "reason": (
                    "engineering diagnostics can never publish a formal "
                    "stability conclusion; the walk-forward run is recorded "
                    "as an UNTRUSTED diagnostic"
                ),
            }
        metrics_path = self._run_dir / "metrics.json"
        metrics_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report_html = self._report(metrics)
        if not isinstance(report_html, str):
            raise TypeError("report must return an HTML string")
        html_path = self._run_dir / "report.html"
        html_path.write_text(report_html, encoding="utf-8")
        return {
            "stability_report.json": sha256_file(report_path),
            "metrics.json": sha256_file(metrics_path),
            "report.html": sha256_file(html_path),
        }

    def _buffered_report_block(self) -> dict[str, object] | None:
        """The buffered construction/turnover audit for the stability report.

        Reads the published fold construction frames and scenario decision
        frames only -- nothing is recomputed.  ``None`` for an equal-weight
        run (no non-empty construction frame anywhere), so the report never
        renders an empty buffered section.  Suppressed amounts are exact:
        ``|weight_difference| * signal_close_equity`` from the recorded
        decision rows, priced at the scenario's own signal close.
        """
        folds_dir = self._run_dir / "folds"
        if not folds_dir.is_dir():
            return None
        fold_rows: list[dict[str, object]] = []
        scenario_rows: dict[str, dict[str, object]] = {}
        rule_version: str | None = None
        for fold_dir in sorted(folds_dir.iterdir()):
            construction_path = fold_dir / "portfolio_construction.parquet"
            if not construction_path.is_file():
                continue
            construction = pd.read_parquet(construction_path)
            if construction.empty:
                continue
            rule_version = str(construction["portfolio_rule_version"].iloc[0])
            per_signal = construction.groupby("signal_date", sort=True)
            cash_series = per_signal["cash_weight"].first()
            achieved_gross = float(1.0 - cash_series.mean())
            fold_rows.append(
                {
                    "fold_id": fold_dir.name,
                    "signal_days": int(construction["signal_date"].nunique()),
                    "retained": int(
                        (construction["member_status"] == "retained").sum()
                    ),
                    "entered": int(
                        (construction["member_status"] == "entered").sum()
                    ),
                    "exited": int(
                        (construction["member_status"] == "exited").sum()
                    ),
                    "risk_invalid": int(
                        (construction["member_status"] == "risk_invalid").sum()
                    ),
                    "achieved_gross_exposure": round(achieved_gross, 6),
                    "cash_residue": round(float(cash_series.mean()), 6),
                }
            )
            for decision_path in sorted(
                fold_dir.glob("backtest/*/rebalance_decisions.parquet")
            ):
                scenario = decision_path.parent.name
                decisions = pd.read_parquet(decision_path)
                row = scenario_rows.setdefault(
                    scenario,
                    {
                        "scenario": scenario,
                        "band_suppressed_rows": 0,
                        "band_suppressed_amount": 0.0,
                        "lot_suppressed_rows": 0,
                        "lot_suppressed_amount": 0.0,
                    },
                )
                if decisions.empty:
                    continue
                for reason, prefix in (
                    ("within_rebalance_band", "band"),
                    ("below_one_lot", "lot"),
                ):
                    suppressed = decisions[decisions["reason"] == reason]
                    amount = sum(
                        abs(diff) * equity
                        for diff, equity in zip(
                            suppressed["weight_difference"],
                            suppressed["signal_close_equity"],
                        )
                    )
                    row[f"{prefix}_suppressed_rows"] = (
                        int(row[f"{prefix}_suppressed_rows"])
                        + int(len(suppressed))
                    )
                    row[f"{prefix}_suppressed_amount"] = round(
                        float(row[f"{prefix}_suppressed_amount"])
                        + float(amount),
                        2,
                    )
        if rule_version is None:
            return None
        return {
            "portfolio_rule_version": rule_version,
            "folds": fold_rows,
            "scenarios": [
                scenario_rows[scenario] for scenario in sorted(scenario_rows)
            ],
        }

    def _record_walk_forward_trust(
        self,
        state: RunState,
        frozen: ExperimentSpec,
        windows: Mapping[str, FoldWindow],
    ) -> dict[str, object]:
        """The corporate-action trust record over the walk-forward OOS span."""
        starts = [
            fold.first_trading_day or fold.calendar_start
            for fold in windows.values()
        ]
        ends = [
            fold.last_trading_day or fold.calendar_end for fold in windows.values()
        ]
        decision = evaluate_corporate_action_trust(
            self._coverage_evidence(frozen),
            self._universe_symbols,
            min(starts) if starts else frozen.date_range.start_date,
            max(ends) if ends else frozen.date_range.end_date,
        )
        record: dict[str, object] = {
            "mode": frozen.trust_mode.value,
            "dataset_version": frozen.dataset_version,
            "window_start": min(starts).isoformat() if starts else None,
            "window_end": max(ends).isoformat() if ends else None,
        }
        record.update(decision.to_dict())
        state.corporate_action_trust = record
        return record

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
        if frozen.execution_pipeline == "walk_forward_oos_v1":
            manifested = self._walk_forward_publish_set()
        else:
            manifested = MANIFESTED_ARTIFACTS
        for name in manifested:
            source = self._run_dir / name
            if not source.is_file():
                raise FileNotFoundError(
                    f"cannot publish run {self._run_id}: content artifact "
                    f"{name!r} is missing from {self._run_dir}"
                )
            destination = publish_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        (publish_dir / _RUN_MANIFEST).write_text(
            json.dumps(
                {"run_id": self._run_id, "status": "COMPLETED"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        artifacts = {name: _sha256_file(self._run_dir / name) for name in
                     manifested}
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
        if state.experiment_id is None:
            raise ValueError(
                f"cannot publish run {self._run_id}: the run state carries no "
                "experiment identity; a completed run must publish under the "
                "frozen experiment id"
            )
        manifest = {
            "experiment_id": state.experiment_id,
            "status": rejected if status == untrusted else status,
            "dataset_version": frozen.dataset_version,
            "universe_version": frozen.universe_version,
            "data_acceptance_id": frozen.data_acceptance_id,
            "code_commit": frozen.code_commit,
            "strategy_snapshot_sha256": self._bundle.strategy_hash,
            "experiment_snapshot_sha256": self._bundle.experiment_hash,
            "data_environment_snapshot_sha256":
                self._bundle.data_environment_hash,
            "evaluation_reason": reason,
            "artifacts": artifacts,
        }
        if self._universe_preflight is not None:
            # A definition-backed run carries its frozen universe identity in
            # the immutable experiment manifest, so the registry index can
            # locate the exact membership definition an experiment used.
            manifest.update({
                "universe_id": self._universe_preflight.definition.universe_id,
                "universe_rules_version":
                    self._universe_preflight.definition.rules_version,
                "universe_membership_table_sha256":
                    self._universe_preflight.definition.membership_table_sha256,
            })
        if frozen.execution_pipeline == "walk_forward_oos_v1":
            wf_report = json.loads(
                (self._run_dir / "stability_report.json").read_text(
                    encoding="utf-8"
                )
            )
            manifest.update({
                "stability_conclusion": wf_report["stability_conclusion"],
                "stability_policy_hash": wf_report["stability_policy_hash"],
                "fold_schedule_sha256": wf_report["schedule"][
                    "fold_schedule_sha256"
                ],
                "fold_outcomes_sha256": wf_report["schedule"][
                    "fold_outcomes_sha256"
                ],
            })
        (publish_dir / "experiment_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self._registry.publish(
            self._run_dir, ExperimentIdentity.of(frozen, self._bundle)
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
            "experiment_id": ExperimentIdentity.of(
                frozen, self._bundle
            ).experiment_id,
            "dataset_version": frozen.dataset_version,
            "universe_version": frozen.universe_version,
            "universe": self._universe_identity(frozen),
            "universe_daily_member_counts": (
                dict(self._universe_preflight.daily_member_counts)
                if self._universe_preflight is not None
                else {}
            ),
            "universe_daily_snapshots": (
                dict(self._universe_preflight.daily_snapshots)
                if self._universe_preflight is not None
                else {}
            ),
            "code_commit": frozen.code_commit,
            "factor_versions": dict(frozen.factor_versions),
            "cost_scenarios": list(frozen.cost_scenarios),
            "random_seed": frozen.random_seed,
            "parent_experiment_ids": list(frozen.parent_experiment_ids),
            "agent_id": frozen.agent_id,
            "python_version": platform.python_version(),
            "dependency_versions": self._dependency_versions(),
            "config_file_hashes": self._config_hashes(frozen),
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

    def _config_hashes(self, frozen: ExperimentSpec) -> dict[str, str]:
        """sha256 of every config file the run consumes.

        A frozen-definition run hashes its ``configs/universes/<name>.yml``
        too: the definition content is a run input (its version is derived
        from it), so any definition change re-digests every stage.
        """
        names = [
            "project.yml",
            "costs.yml",
            "trading_rules.yml",
            "universe.yml",
        ]
        if frozen.universe_definition is not None:
            names.append(f"universes/{frozen.universe_definition}.yml")
        return {
            name: _sha256_file(self._config_root / "configs" / name)
            for name in names
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
        """Compute every requested factor over one pinned signal-dataset.

        When the run preflighted a frozen universe definition, the factor
        context carries its point-in-time membership hooks (``members_on`` /
        ``membership_snapshot_for``, resolved from the frozen resolver) so
        every factor filters its candidates to signal-day members before any
        eligibility logic; the full master price surface stays pinned to the
        adapter.  The per-signal-day snapshot hashes are persisted as
        factor-stage metadata (``factor_metadata.json``).
        """
        signals = self._signals(frozen)
        factors = self._resolve_factors(frozen)
        preflight = self._universe_preflight
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
                members_on=(
                    preflight.resolver.members_on
                    if preflight is not None
                    else None
                ),
                membership_snapshot_for=(
                    preflight.resolver.snapshot_for
                    if preflight is not None
                    else None
                ),
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
        outputs = {"factor_results.parquet":
                   _sha256_file(self._run_dir / "factor_results.parquet")}
        metadata = self._factor_stage_metadata(signals)
        if metadata is not None:
            path = self._run_dir / _FACTOR_METADATA
            path.write_text(
                json.dumps(
                    metadata, ensure_ascii=False, indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )
            outputs[_FACTOR_METADATA] = _sha256_file(path)
        return outputs

    def _factor_stage_metadata(self, signals: tuple[date, ...]) -> dict | None:
        """The factor-stage membership metadata record, or ``None``.

        A definition-backed run records which frozen universe the factor stage
        filtered under and the exact ``{ISO date: snapshot sha256}`` and
        member-count maps for its signal days, so factor artifacts stay
        auditable against membership evidence independently of the later
        report stage.  A legacy run without a definition records nothing.
        """
        preflight = self._universe_preflight
        if preflight is None:
            return None
        return {
            "universe_id": preflight.definition.universe_id,
            "universe_version": preflight.universe_version,
            "signal_dates": [day.isoformat() for day in signals],
            "daily_member_counts": dict(
                sorted(preflight.daily_member_counts.items())
            ),
            "daily_snapshots": dict(
                sorted(preflight.daily_snapshots.items())
            ),
        }

    def _resolve_factors(self, frozen: ExperimentSpec) -> list[Factor]:
        available = dict(
            self._prefetched_factors
            if self._prefetched_factors is not None
            else self._factor_provider()
        )
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
        if isinstance(rule, BufferedRiskWeightedPortfolioRule):
            # The buffered rule has no equal-weight fallback anywhere: the
            # engineering single-window pipeline is an equal-weight diagnostic
            # and must refuse a buffered spec loudly instead of silently
            # building a different strategy.
            raise ValueError(
                "the buffered_risk_weighted portfolio rule executes only "
                "under the walk_forward_oos_v1 pipeline; the engineering "
                "single-window diagnostic pipeline keeps the "
                "top_n_equal_weight rule"
            )
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
            target_records = target.frame.to_dict("records")
            self._assert_targets_are_signal_day_members(signal, target_records)
            planned_notional = sum(
                float(row["signal_price"]) * int(row["target_quantity"])
                for row in target_records
            )
            signal_rows.append({
                "signal_date": signal,
                "execution_date": execution_date,
                "n_targets": int(len(target.frame)),
                "planned_notional": round(planned_notional, 2),
                "unallocated_weight": round(target.unallocated_weight, 6),
            })
            target_rows.extend(
                {**row, "trade_date": signal} for row in target_records
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

    def _assert_targets_are_signal_day_members(
        self, signal: date, target_records: list[dict]
    ) -> None:
        """Loud guard: every NEW portfolio target is a member on its signal day.

        Membership is an input gate, never a trading instruction: a removed
        member only stops generating new targets and its exit stays with the
        normal execution/exit accounting (removal is not a forced sell).  A
        target outside the signal-day member set therefore means the
        membership-first factor order was violated, and the run fails instead
        of publishing it.
        """
        preflight = self._universe_preflight
        if preflight is None:
            return
        members = set(preflight.resolver.members_on(signal))
        outsiders = sorted(
            {str(record["symbol"]) for record in target_records} - members
        )
        if outsiders:
            raise ValueError(
                f"portfolio targets {outsiders} on signal date "
                f"{signal.isoformat()} are not {preflight.definition.universe_id} "
                "members on that day; membership-first factor filtering was "
                "violated"
            )

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

    def _walk_forward_publish_set(self) -> tuple[str, ...]:
        """The complete walk-forward audit set (relative paths, sorted).

        Root audit files, every executed fold's declared artifact set and
        every scenario-local rebalance-decision artifact; an executed fold's
        assets publish only when the whole set is present, so an incomplete
        experiment can never be staged.  A buffered fold (a non-empty
        ``portfolio_construction.parquet``) must carry one decision file per
        declared scenario.
        """
        names = [
            "experiment_spec.yml",
            "config_snapshot.yml",
            "dataset_version.txt",
            "metrics.json",
            "report.html",
            "stability_report.json",
            "fold_schedule.json",
            "fold_outcomes.json",
            "walk_forward_manifest.json",
        ]
        for fold_dir in sorted((self._run_dir / "folds").iterdir()):
            if not fold_dir.is_dir():
                continue
            for name in FOLD_ARTIFACTS:
                if not (fold_dir / name).is_file():
                    raise FileNotFoundError(
                        f"fold {fold_dir.name} is missing {name}; an "
                        "incomplete fold audit set can never be published"
                    )
            names.extend(f"folds/{fold_dir.name}/{name}" for name in FOLD_ARTIFACTS)
            construction = pd.read_parquet(
                fold_dir / "portfolio_construction.parquet"
            )
            buffered = not construction.empty
            for scenario in self._active_cost_scenarios(fold_dir):
                scenario_dir = fold_dir / "backtest" / scenario
                # Every declared scenario's daily equity path is mandatory
                # evidence for an executed fold (the one-time challenge's
                # invested-exposure input).
                equity = scenario_dir / "equity.parquet"
                if not equity.is_file():
                    raise FileNotFoundError(
                        f"fold {fold_dir.name} is missing the {scenario} "
                        "equity artifact; an incomplete fold audit set can "
                        "never be published"
                    )
                names.append(
                    f"folds/{fold_dir.name}/backtest/{scenario}/equity.parquet"
                )
                path = scenario_dir / "rebalance_decisions.parquet"
                if buffered and not path.is_file():
                    raise FileNotFoundError(
                        f"buffered fold {fold_dir.name} is missing the "
                        f"{scenario} rebalance_decisions artifact; an "
                        "incomplete fold audit set can never be published"
                    )
                if path.is_file():
                    names.append(
                        f"folds/{fold_dir.name}/backtest/{scenario}/"
                        "rebalance_decisions.parquet"
                    )
        return tuple(sorted(names))

    def _active_cost_scenarios(self, fold_dir: Path) -> list[str]:
        """The declared scenarios recorded in the fold's manifest."""
        manifest_path = fold_dir / "fold_manifest.json"
        if not manifest_path.is_file():
            return []
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scenarios = manifest.get("declared_scenarios")
        if not isinstance(scenarios, list):
            return []
        return [str(scenario) for scenario in scenarios]

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

    def _factor_input_audit(self, frozen: ExperimentSpec) -> dict[str, object]:
        """The deterministic provenance audit of the factor's price input.

        Reads ``adjusted_bar`` from the *frozen* dataset version (never
        ``CURRENT``), restricted to the universe symbols this run can hold, and
        counts ERROR quality breaks plus their ``invalid_reason`` distribution
        sorted by reason.  Every key is sorted before encoding so identical
        runs publish byte-identical ``metrics.json`` ``factor_input`` sections;
        the evaluator and both reports consume exactly these persisted facts.
        """
        context = self._open_context(frozen.dataset_version)
        if "adjusted_bar" not in context.tables:
            raise ValueError(
                f"dataset {frozen.dataset_version} has no adjusted_bar; "
                f"research factors require {ADJUSTMENT_NAME}"
            )
        adjusted = context.read("adjusted_bar")
        universe = adjusted[adjusted["symbol"].isin(set(self._universe_symbols))]
        errors = universe[universe["quality_severity"] == "ERROR"]
        counts = errors["invalid_reason"].value_counts().sort_index()
        return {
            "adjustment": ADJUSTMENT_NAME,
            "factor_versions": dict(sorted(frozen.factor_versions.items())),
            "row_count": int(len(universe)),
            "error_break_count": int(len(errors)),
            "invalid_reason_counts": {
                str(reason): int(count) for reason, count in counts.items()
            },
        }

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
            "universe": self._universe_identity(frozen),
            "universe_daily_member_counts": (
                dict(self._universe_preflight.daily_member_counts)
                if self._universe_preflight is not None
                else {}
            ),
            "universe_daily_snapshots": (
                dict(self._universe_preflight.daily_snapshots)
                if self._universe_preflight is not None
                else {}
            ),
            "code_commit": frozen.code_commit,
            "python_version": platform.python_version(),
            "dependency_versions": self._dependency_versions(),
            "config_file_hashes": self._config_hashes(frozen),
            "run_input_digest": self._digest,
            "initial_cash": self._project_config.initial_cash,
            "sizing_capital": self._sizing_capital,
            "benchmark_symbols": list(self._project_config.benchmark_symbols),
            "canonical_scenario": canonical,
        }
        metrics: dict[str, object] = {"meta": meta, **analytics_out}
        # Persist the factor-input provenance before evaluation and report
        # rendering so the evaluator, the direct-run report and any later
        # rebuild from metrics.json all consume the same committed facts.
        metrics["factor_input"] = self._factor_input_audit(frozen)
        # Persist the pinned real-data acceptance audit next to it: a run
        # without one is recorded as explicitly UNVERIFIED, never as trusted.
        metrics["data_acceptance"] = self._data_acceptance or {
            "acceptance_id": None,
            "status": "UNVERIFIED",
        }
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

    The adapter reads *only* the canonical ``adjusted_bar`` table: research
    factor prices are the point-in-time total-return series derived from
    unadjusted closes and verified corporate actions, never a disguised
    ``daily_bar.close``.  A pinned dataset without that table, without rows for
    the universe, or carrying any other adjustment basis fails loudly instead
    of falling back.  ``listed_trading_days`` is derived from the pinned
    trading calendar and the security master's listing dates; rows are
    restricted to the universe symbols so index/benchmark series never enter
    factor observations.
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
        if "adjusted_bar" not in self._context.tables:
            raise ValueError(
                f"dataset {self._context.version} has no adjusted_bar; "
                f"research factors require {ADJUSTMENT_NAME}"
            )
        adjusted = self._context.read("adjusted_bar")
        rows = adjusted[adjusted["symbol"].isin(self._universe_symbols)].copy()
        if rows.empty:
            raise ValueError(
                f"dataset {self._context.version} has no adjusted rows for "
                "the universe"
            )
        if set(rows["adjustment"].astype(str)) != {ADJUSTMENT_NAME}:
            raise ValueError(
                f"factor input must use only {ADJUSTMENT_NAME}, got "
                + ", ".join(sorted(set(rows["adjustment"].astype(str))))
            )
        # DuckDB surfaces DATE as datetime64; factors compare ``trade_date``
        # against plain ``date`` objects, so normalize to real dates up front.
        rows["trade_date"] = rows["trade_date"].map(_as_date)
        rows["listed_trading_days"] = self._listed_trading_days(rows)
        return rows[[
            "trade_date", "symbol", "source", "adjustment", "adjusted_close",
            "quality_severity", "listed_trading_days",
        ]].sort_values(["symbol", "trade_date"], kind="stable").reset_index(
            drop=True
        )

    def _listed_trading_days(self, rows: pd.DataFrame) -> list[int]:
        """Sessions listed as of each row's trade date (seasoning input)."""
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
        listed_by_ordinal: dict[str, dict[int, int]] = {}
        for symbol in sorted(set(rows["symbol"])):
            mapping: dict[int, int] = {}
            listed_on = list_date.get(symbol)
            if listed_on is not None:
                anchor = bisect_left(open_ordinals, listed_on.toordinal())
                for index in range(anchor, len(open_days)):
                    mapping[open_days[index].toordinal()] = index - anchor + 1
            listed_by_ordinal[symbol] = mapping
        return [
            listed_by_ordinal[symbol].get(_to_ordinal(day), 0)
            for symbol, day in zip(rows["symbol"], rows["trade_date"])
        ]


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #


def _facts_from_membership_frame(frame: pd.DataFrame) -> list[MembershipFact]:
    """Rebuild validated facts from the dataset's ``universe_membership`` rows.

    Only called after the acceptance gate passed, so every row satisfies the
    fact contract; a breach here raises and fails the run loudly.
    """
    facts: list[MembershipFact] = []
    for record in frame.to_dict("records"):
        payload = dict(record)
        for column in ("raw_effective_from", "raw_effective_to",
                       "announcement_date"):
            value = payload.get(column)
            payload[column] = (
                None
                if value is None or pd.isna(value)
                else pd.Timestamp(value).date()
            )
        facts.append(MembershipFact.model_validate(payload))
    return facts


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
