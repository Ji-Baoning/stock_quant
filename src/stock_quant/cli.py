"""Operator-facing command line for the offline quant engineering loop.

Four thin groups over the existing ports, all offline-testable against a
synthetic project and never printing a token or a raw supplier response:

- ``data update`` / ``data validate`` -- drive :class:`DataPipeline`, the only
  writer of the immutable published dataset. ``data index-membership prepare``
  is the offline first step of the membership workflow: it normalizes one
  already-stored official snapshot into the canonical ``universe_membership``
  frame, bound to the snapshot/document SHA-256 evidence the operator passes
  as mandatory arguments (no network access, no bypass flag).
- ``research run`` -- the **only** formal publisher: runs one experiment spec
  end-to-end (freeze -> factor -> portfolio -> backtest -> metrics -> report)
  through :class:`ResearchRunner` and publishes into ``data/experiments``.
  Repeated runs of an identical spec are content-addressed and reproducible.
- ``backtest momentum_60d`` -- the same pipeline in a **debug** registry whose
  experiments root is ``data/runs/debug``, so a scratch run never advances the
  formal experiments index.
- ``report build`` -- renders the display reports under ``data/reports`` from
  committed artifacts only: the richer self-contained experiment report HTML for
  the selected (default latest) published experiment *and* the data-quality HTML
  for the pinned ``CURRENT`` dataset version (rebuilt from the persisted
  ``quality_report.json``; nothing is recomputed).

The metrics every research-style run records include, per cost scenario, both
the auditable engineering summary (periods, rejections, ``plan_diverged``) and
the richer ``PerformanceMetrics`` mapping (``max_drawdown``,
``benchmark_excess_return``, ...) over the committed ledgers and the pinned
dataset's benchmark closes.  Nothing here is investment advice.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import typer

from stock_quant.analytics.performance import PerformanceMetrics, compute_metrics
from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.config import load_project_config
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_model.index_membership_import import prepare_membership_file
from stock_quant.data_model.universe_membership import MembershipReason
from stock_quant.data_pipeline import DataPipeline, DataUpdateRequest
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import (
    QualityIssue,
    QualityReport,
    Severity,
)
from stock_quant.reporting.html import (
    ExperimentReportInput,
    ExperimentScenario,
    QualityReportInput,
    render_experiment_report,
    render_quality_report,
)
from stock_quant.research.models import ResearchRunFailed
from stock_quant.research.reconcile import (
    STATUS_FILLED,
    STATUS_PARTIAL,
    STATUS_REJECTED,
)
from stock_quant.research.registry import ExperimentRegistry, PublishedExperiment
from stock_quant.research.runner import AnalyticsInput, ResearchRunner
from stock_quant.research.trust import DataTrustMode

app = typer.Typer(
    help=(
        "Reproducible offline A-share quant engineering (data / research / "
        "backtest / report). "
    ),
    no_args_is_help=True,
)

data_app = typer.Typer(help="Fetch, quality-check and publish one dataset window.")
index_membership_app = typer.Typer(
    help=(
        "Offline evidence-bound preparation of immutable universe_membership "
        "facts (snapshot/document hashes are mandatory arguments)."
    )
)
research_app = typer.Typer(help="Run and publish one formal experiment spec.")
backtest_app = typer.Typer(help="Scratch backtests that never publish experiments.")
report_app = typer.Typer(help="Render self-contained reports from committed artifacts.")

app.add_typer(data_app, name="data")
data_app.add_typer(index_membership_app, name="index-membership")
app.add_typer(research_app, name="research")
app.add_typer(backtest_app, name="backtest")
app.add_typer(report_app, name="report")


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _secret_values() -> tuple[object, ...]:
    """Non-empty secret-like environment values, used only for log redaction."""
    values = [
        os.environ.get(key)
        for key in (
            "TUSHARE_TOKEN",
            "AKSHARE_TOKEN",
            "BAOSTOCK_USER",
            "BAOSTOCK_PASSWORD",
        )
    ]
    return tuple(value for value in values if value)


def _echo_failure(message: str) -> None:
    typer.echo(f"FAILED: {message}")


def _report_summary(report: QualityReport) -> str:
    counts = {str(key): int(value) for key, value in report.by_severity().items()}
    parts = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
    return parts or "issues=none"


def _run_one_research(
    project_root: Path,
    spec: str,
    *,
    registry: ExperimentRegistry | None = None,
    trust_mode: DataTrustMode = DataTrustMode.RESEARCH,
) -> "PublishedExperiment":
    """Run a spec; ``registry`` overrides the formal experiments root.

    Formal ``research run`` always runs RESEARCH (no bypass); only the debug
    ``backtest`` path may select ``DataTrustMode.ENGINEERING``.
    """
    runner = ResearchRunner(
        project_root,
        analytics=_ExperimentAnalytics(project_root),
        secrets=_secret_values(),
    )
    if registry is not None:
        runner._registry = registry  # type: ignore[attr-defined]
    try:
        return runner.run(spec, trust_mode=trust_mode)
    except ResearchRunFailed as error:
        _echo_failure(str(error))
        raise typer.Exit(code=1) from None


def _echo_trust(published: "PublishedExperiment") -> None:
    """Print the frozen corporate-action trust verdict from the artifacts."""
    trusted = True
    metrics_path = Path(published.path) / "metrics.json"
    if metrics_path.is_file():
        record = json.loads(metrics_path.read_text(encoding="utf-8")).get(
            "corporate_action_trust"
        )
        if isinstance(record, dict):
            trusted = bool(record.get("trusted"))
    typer.echo(f"trust={'TRUSTED' if trusted else 'UNTRUSTED'}")


# --------------------------------------------------------------------------- #
# data group
# --------------------------------------------------------------------------- #


@data_app.command("bootstrap")
def data_bootstrap(
    root: Path = typer.Option(".", "--root", help="Project root."),
    calendar_csv: Path | None = typer.Option(
        None,
        "--calendar-csv",
        help="Official trading-day file (one ISO date per line).",
    ),
) -> None:
    """Publish the baseline dataset required before the first data update."""
    try:
        result = bootstrap_dataset(root, calendar_csv=calendar_csv)
    except Exception as error:  # noqa: BLE001 - surface cleanly to the operator
        _echo_failure(str(error))
        raise typer.Exit(code=1) from None
    typer.echo(f"published seed dataset: {result.version}")
    typer.echo(f"security_master symbols : {result.symbol_count}")
    typer.echo(
        f"trading_calendar days    : {result.trading_day_count} "
        f"({result.start}..{result.end})"
    )
    typer.echo(f"CURRENT -> {result.version}")
    typer.echo(
        "NOTE: weekday-approximation calendar (no CN holidays). "
        "Use --calendar-csv for official exchange days."
    )


@data_app.command("update")
def data_update(
    start: str | None = typer.Option(
        None, "--start", help="Inclusive start (YYYY-MM-DD)."
    ),
    end: str | None = typer.Option(
        None, "--end", help="Inclusive end (YYYY-MM-DD)."
    ),
    sources: str | None = typer.Option(
        None, "--sources", help="Comma-separated source subset."
    ),
    root: Path = typer.Option(".", "--root", help="Project root."),
) -> None:
    """Fetch one window into the raw-store and publish when the gate passes."""
    project_root = Path(root)
    request = DataUpdateRequest(
        start_date=date.fromisoformat(start) if start else None,
        end_date=date.fromisoformat(end) if end else None,
        sources=tuple(s.strip() for s in sources.split(",")) if sources else None,
    )
    try:
        result = DataPipeline(project_root).update(request)
    except Exception as error:  # noqa: BLE001 - surface cleanly to the operator
        _echo_failure(str(error))
        raise typer.Exit(code=1) from None
    typer.echo(f"run_id={result.run_id}")
    typer.echo(f"resolved_end_date={result.resolved_end_date or ''}")
    typer.echo(f"resolved_end_is_fallback={str(result.resolved_end_is_fallback).lower()}")
    typer.echo(_report_summary(result.quality_report))
    for status in result.source_status:
        state = "ok" if status.ok else "not_ok"
        typer.echo(f"source {status.source}: {state}")
    if result.resolved_end_is_fallback:
        typer.echo(
            "note: coverage was incomplete for the latest date; end date walked "
            "back to the last confirmed complete trading day"
        )
    if result.dataset_ref is not None:
        typer.echo(f"dataset_version={result.dataset_ref.version}")
        typer.echo("PASS")
        return
    _echo_failure("publication gate did not pass; dataset unchanged")
    raise typer.Exit(code=1)


@data_app.command("validate")
def data_validate(
    version: str | None = typer.Option(None, "--version", help="Dataset version hash."),
    root: Path = typer.Option(".", "--root", help="Project root."),
) -> None:
    """Re-run the shared quality checks over one published dataset version."""
    project_root = Path(root)
    pipeline = DataPipeline(project_root)
    try:
        if version is None:
            from stock_quant.data_model.dataset import DatasetPublisher

            version = DatasetPublisher(project_root).current().version
        report = pipeline.validate(version)
    except Exception as error:  # noqa: BLE001 - surface cleanly to the operator
        _echo_failure(str(error))
        raise typer.Exit(code=1) from None
    typer.echo(f"version={version}")
    typer.echo(_report_summary(report))
    decision = evaluate_publication(report)
    fatal = any(item.severity is Severity.FATAL for item in report.issues)
    if decision.passed and not fatal:
        typer.echo("PASS")
        return
    if fatal:
        _echo_failure("quality report contains a FATAL issue")
        raise typer.Exit(code=1)
    _echo_failure("quality gate did not pass")
    raise typer.Exit(code=1)


@index_membership_app.command("prepare")
def data_index_membership_prepare(
    universe_id: str = typer.Option(
        ..., "--universe-id", help="Canonical universe id, e.g. csi300."
    ),
    input_path: Path = typer.Option(
        ...,
        "--input",
        exists=True,
        dir_okay=False,
        help="Already-downloaded membership list (.csv or .parquet).",
    ),
    snapshot_sha256: str = typer.Option(
        ...,
        "--snapshot-sha256",
        help="SHA-256 of the stored raw snapshot this import is bound to.",
    ),
    source_document_sha256: str = typer.Option(
        ...,
        "--source-document-sha256",
        help="SHA-256 of the stored official source document.",
    ),
    source: str = typer.Option(
        ...,
        "--source",
        help="Evidence source label, e.g. csi_index_announcement.",
    ),
    source_url: str = typer.Option(
        ...,
        "--source-url",
        help="Credential-free http(s) locator of the evidence document.",
    ),
    effective_date: str = typer.Option(
        ...,
        "--effective-date",
        help="Default raw_effective_from for rows without their own (ISO).",
    ),
    announcement_date: str = typer.Option(
        ...,
        "--announcement-date",
        help="Default announcement_date for rows without their own (ISO).",
    ),
    reason: MembershipReason = typer.Option(
        MembershipReason.INITIAL_CONSTITUENT,
        "--reason",
        help="Default reason for rows without their own.",
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        help="Destination for the canonical frame (.parquet or .csv).",
    ),
) -> None:
    """Normalize one official snapshot into immutable membership facts.

    Every fact is bound to the stored snapshot/document evidence named by the
    mandatory arguments; a missing or invalid argument is rejected before any
    output byte is written. Prints the ``membership_table_sha256=`` that the
    frozen universe definition must pin. There is no bypass flag: unevidenced
    facts cannot be prepared.
    """
    try:
        result = prepare_membership_file(
            input_path,
            universe_id=universe_id,
            source=source,
            source_url=source_url,
            snapshot_sha256=snapshot_sha256,
            source_document_sha256=source_document_sha256,
            effective_date=date.fromisoformat(effective_date),
            announcement_date=date.fromisoformat(announcement_date),
            reason=reason.value,
            output=output,
        )
    except (ValueError, TypeError) as error:
        _echo_failure(f"membership import rejected: {error}")
        raise typer.Exit(code=1) from None
    typer.echo(f"rows={len(result.frame)}")
    typer.echo(f"universe_id={result.universe_id}")
    typer.echo(f"membership_table_sha256={result.content_hash}")
    typer.echo(f"output={output}")


# --------------------------------------------------------------------------- #
# research group (the only formal publisher)
# --------------------------------------------------------------------------- #


@research_app.command("run")
def research_run(
    spec: str = typer.Option(
        ..., "--spec", "-s", help="Experiment spec path, relative to --root/configs."
    ),
    root: Path = typer.Option(".", "--root", help="Project root."),
) -> None:
    """Run one experiment spec end-to-end and publish it (content-addressed).

    Formal research always applies the RESEARCH corporate-action trust bar and
    refuses to backtest a dataset whose pinned coverage is not trusted, so a
    formal run can never lower its own evidence bar.
    """
    published = _run_one_research(Path(root), spec)
    typer.echo(f"experiment_id={published.experiment_id}")
    typer.echo(f"published={published.path}")
    _echo_trust(published)


# --------------------------------------------------------------------------- #
# backtest group (debug registry; never advances data/experiments)
# --------------------------------------------------------------------------- #


class _DebugRegistry(ExperimentRegistry):
    """A registry that publishes under ``data/runs/debug`` instead."""

    @property
    def experiments_root(self) -> Path:
        return self._project_root / "data" / "runs" / "debug"


@backtest_app.command("momentum_60d")
def backtest_momentum_60d(
    root: Path = typer.Option(".", "--root", help="Project root."),
    spec: str = typer.Option(
        "configs/experiments/momentum_60d.yml", "--spec", help="Experiment spec path."
    ),
    engineering: bool = typer.Option(
        False,
        "--engineering",
        help=(
            "Run as an UNTRUSTED diagnostic even when corporate-action coverage "
            "is missing/incomplete (never a trusted performance claim)."
        ),
    ),
) -> None:
    """Run the momentum spec into the debug area without publishing formally.

    The debug backtest is RESEARCH by default; ``--engineering`` keeps the run
    going when the pinned corporate-action coverage is not trusted, stamps it
    UNTRUSTED and prints ``trust=UNTRUSTED`` so the shell can tell a completed
    diagnostic from a trusted performance claim.
    """
    mode = DataTrustMode.ENGINEERING if engineering else DataTrustMode.RESEARCH
    published = _run_one_research(
        Path(root), spec, registry=_DebugRegistry(Path(root)), trust_mode=mode
    )
    typer.echo(f"experiment_id={published.experiment_id}")
    typer.echo(f"debug={published.path}")
    _echo_trust(published)


# --------------------------------------------------------------------------- #
# report group
# --------------------------------------------------------------------------- #


@report_app.command("build")
def report_build(
    experiment: str | None = typer.Option(None, "--experiment", help="Experiment id."),
    out: Path | None = typer.Option(
        None, "--out", help="Destination HTML file (default data/reports/<id>.html)."
    ),
    root: Path = typer.Option(".", "--root", help="Project root."),
) -> None:
    """Render the experiment and current data-quality display reports.

    Rebuilds the richer self-contained experiment report for the selected
    (default latest) published experiment *and* the data-quality report for the
    pinned ``CURRENT`` dataset version, from committed artifacts only, into
    ``data/reports``.  The quality HTML is reconstructed from the persisted
    ``quality_report.json`` / version manifest -- nothing is recomputed.
    """
    project_root = Path(root)
    experiment_id = experiment or _latest_experiment_id(project_root)
    if experiment_id is None:
        _echo_failure("no published experiment found under data/experiments")
        raise typer.Exit(code=1)
    try:
        from stock_quant.data_model.dataset import DatasetPublisher

        dataset_version = DatasetPublisher(project_root).current().version
        run_input = _experiment_report_input(project_root, experiment_id)
        destination = out or (
            project_root / "data" / "reports" / f"{experiment_id}.html"
        )
        render_experiment_report(run_input, destination)
        quality_input = _quality_report_input(project_root, dataset_version)
        quality_destination = (
            project_root / "data" / "reports" / f"quality-{dataset_version}.html"
        )
        render_quality_report(quality_input, quality_destination)
    except Exception as error:  # noqa: BLE001 - surface cleanly to the operator
        _echo_failure(str(error))
        raise typer.Exit(code=1) from None
    typer.echo(f"report={destination}")
    typer.echo(f"quality_report={quality_destination}")


def _latest_experiment_id(project_root: Path) -> str | None:
    experiments = ExperimentRegistry(project_root).experiments_root
    if not experiments.is_dir():
        return None
    manifest = experiments / "registry.parquet"
    if not manifest.is_file():
        entries = sorted(
            path.name
            for path in experiments.iterdir()
            if (path / "experiment_manifest.json").is_file()
        )
    else:
        index = pd.read_parquet(manifest)
        entries = [str(value) for value in index["experiment_id"].tolist()]
    return entries[-1] if entries else None


def _experiment_report_input(project_root: Path, experiment_id: str):
    """Rebuild the rich report input from committed artifacts + pinned dataset."""
    experiment_dir = ExperimentRegistry(project_root).experiments_root / experiment_id
    metrics_path = experiment_dir / "metrics.json"
    if not metrics_path.is_file():
        raise FileNotFoundError(f"{experiment_dir} holds no metrics.json")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    meta = metrics.get("meta", {})
    spec = meta.get("spec", {})
    run_id = str(meta.get("run_id", ""))
    dataset_version = str(meta.get("dataset_version", ""))
    scenario_names = tuple(str(item) for item in spec.get("cost_scenarios", ()))
    benchmark_symbols = tuple(str(item) for item in meta.get("benchmark_symbols", ()))
    run_dir = project_root / "data" / "runs" / run_id
    committed_scenarios = metrics.get("scenarios", {})

    benchmark = _benchmark_closes(project_root, dataset_version, benchmark_symbols)
    scenarios: list[ExperimentScenario] = []
    for name in scenario_names:
        scenario_dir = run_dir / "backtest" / name
        if not scenario_dir.is_dir():
            raise FileNotFoundError(
                f"experiment {experiment_id} backtest workspace was pruned or "
                f"never completed: expected {scenario_dir} (scenario {name!r}); "
                f"data/runs/{run_id} must be retained to rebuild the display "
                "report from committed ledgers"
            )
        equity = pd.read_parquet(scenario_dir / "daily_equity.parquet")
        fills = pd.read_parquet(scenario_dir / "fills.parquet")
        rejections = pd.read_parquet(scenario_dir / "rejections.parquet")
        action_ledger = pd.read_parquet(scenario_dir / "action_ledger.parquet")
        execution_path = scenario_dir / "execution_diagnostics.json"
        execution_summary = (
            json.loads(execution_path.read_text(encoding="utf-8"))
            if execution_path.is_file()
            else None
        )
        committed = (committed_scenarios.get(name) or {}).get("performance")
        metrics_out = (
            _performance_from_dict(committed)
            if committed
            else compute_metrics(equity, fills, benchmark)
        )
        scenarios.append(
            ExperimentScenario(
                name=name,
                equity=equity,
                fills=fills,
                rejections=rejections,
                action_ledger=action_ledger,
                holdings=pd.DataFrame(
                    columns=["symbol", "quantity", "market_value", "weight"]
                ),
                metrics=metrics_out,
                execution_summary=execution_summary,
            )
        )
    return ExperimentReportInput(
        experiment_id=experiment_id,
        dataset_version=dataset_version,
        universe_version=str(meta.get("universe_version", "")),
        code_commit=str(meta.get("code_commit", "")),
        scenarios=tuple(scenarios),
        benchmark_closes=benchmark,
        benchmark_symbols=benchmark_symbols,
        run_id=run_id,
        hypothesis=str(spec.get("hypothesis", "")),
        initial_cash=float(meta.get("initial_cash", 0.0)),
        # The frozen trust decision persisted in metrics.json; the report reads
        # it and renders trusted or untrusted state from these committed bytes.
        corporate_action_trust=metrics.get("corporate_action_trust"),
    )


def _benchmark_closes(
    project_root: Path, dataset_version: str, benchmark_symbols: tuple[str, ...]
) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["symbol", "trade_date", "close"])
    if not dataset_version or not benchmark_symbols:
        return empty
    reader = DatasetReader(project_root)
    try:
        with reader.open(dataset_version) as context:
            daily = context.read("daily_bar")
    except Exception:  # noqa: BLE001 - report build stays best-effort
        return empty
    if daily.empty:
        return empty
    if "symbol" not in daily.columns or "close" not in daily.columns:
        return empty
    selected = daily[daily["symbol"].isin(set(benchmark_symbols))]
    if selected.empty:
        return empty
    return selected[["symbol", "trade_date", "close"]].reset_index(drop=True)


def _performance_from_dict(mapping: dict) -> PerformanceMetrics:
    """Rebuild a :class:`PerformanceMetrics` from a committed ``to_dict()``."""
    values = dict(mapping)
    values["start_date"] = date.fromisoformat(str(values["start_date"]))
    values["end_date"] = date.fromisoformat(str(values["end_date"]))
    return PerformanceMetrics(**values)


def _quality_report_input(
    project_root: Path, dataset_version: str
) -> QualityReportInput:
    """Reconstruct the quality HTML input from the persisted report only.

    The per-version ``quality_report.json`` records the full issue list of the
    publish-time :class:`QualityReport`; the neutral publication-gate decision
    is re-derived from those issues.  Nothing here re-runs the schema/value
    checks or touches supplier data.
    """
    version_dir = project_root / "data" / "standardized" / dataset_version
    quality_file = version_dir / "quality_report.json"
    if not quality_file.is_file():
        raise FileNotFoundError(
            f"dataset version {dataset_version} holds no quality_report.json "
            f"under {version_dir}"
        )
    payload = json.loads(quality_file.read_text(encoding="utf-8"))
    issues = tuple(_issue_from_dict(item) for item in payload.get("issues", ()))
    report = QualityReport(issues=issues)
    decision = evaluate_publication(report)
    return QualityReportInput(
        report=report,
        dataset_version=dataset_version,
        gate_passed=decision.passed,
        gate_reasons=decision.reasons,
    )


def _issue_from_dict(item: dict) -> QualityIssue:
    """One persisted issue row back to a :class:`QualityIssue`."""
    raw_date = item.get("trade_date")
    return QualityIssue(
        severity=Severity(str(item["severity"])),
        code=str(item["code"]),
        table=str(item["table"]),
        symbol=item.get("symbol"),
        trade_date=date.fromisoformat(raw_date) if raw_date else None,
        details=dict(item.get("details") or {}),
    )


# --------------------------------------------------------------------------- #
# Research analytics: auditable summary + richer performance per scenario
# --------------------------------------------------------------------------- #


def _date_text(value: object) -> str:
    return pd.Timestamp(value).date().isoformat()


def _round2(value: float) -> float:
    return round(float(value), 2)


class _ExperimentAnalytics:
    """Per-scenario engineering summary plus :class:`PerformanceMetrics`.

    Kept byte-deterministic: every input is a committed frame or a row of the
    pinned immutable dataset, and nothing here touches a clock or the network.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = Path(project_root)
        self._benchmark_symbols = tuple(
            load_project_config(self._project_root).benchmark_symbols
        )

    def compute(self, metrics_input: AnalyticsInput) -> dict[str, object]:
        scenarios: dict[str, object] = {}
        benchmark = _benchmark_closes(
            self._project_root,
            metrics_input.dataset_version,
            self._benchmark_symbols,
        )
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
            summary: dict[str, object] = {
                "periods": int(len(equity)),
                "start_date": _date_text(equity["trade_date"].iloc[0]),
                "end_date": _date_text(equity["trade_date"].iloc[-1]),
                "start_equity": _round2(start),
                "end_equity": _round2(end),
                "total_return": round(end / start - 1.0, 8) if start > 0 else None,
                "end_cash": _round2(float(equity["cash"].iloc[-1])),
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
                "performance": compute_metrics(
                    equity, fills, benchmark
                ).to_dict(),
            }
            scenarios[scenario] = summary
        return {"scenarios": scenarios}
