"""Operator-facing command line for the offline quant engineering loop.

Four thin groups over the existing ports, all offline-testable against a
synthetic project and never printing a token or a raw supplier response:

- ``data update`` / ``data validate`` -- drive :class:`DataPipeline`, the only
  writer of the immutable published dataset.
- ``research run`` -- the **only** formal publisher: runs one experiment spec
  end-to-end (freeze -> factor -> portfolio -> backtest -> metrics -> report)
  through :class:`ResearchRunner` and publishes into ``data/experiments``.
  Repeated runs of an identical spec are content-addressed and reproducible.
- ``backtest momentum_60d`` -- the same pipeline in a **debug** registry whose
  experiments root is ``data/runs/debug``, so a scratch run never advances the
  formal experiments index.
- ``report build`` -- renders the richer self-contained experiment report HTML
  from a published experiment's committed artifacts under ``data/reports``.

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

from stock_quant.analytics.performance import compute_metrics
from stock_quant.config import load_project_config
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_pipeline import DataPipeline, DataUpdateRequest
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import QualityReport
from stock_quant.reporting.html import (
    ExperimentReportInput,
    ExperimentScenario,
    render_experiment_report,
)
from stock_quant.research.models import ResearchRunFailed
from stock_quant.research.registry import ExperimentRegistry, PublishedExperiment
from stock_quant.research.runner import AnalyticsInput, ResearchRunner

app = typer.Typer(
    help=(
        "Reproducible offline A-share quant engineering (data / research / "
        "backtest / report). "
    ),
    no_args_is_help=True,
)

data_app = typer.Typer(help="Fetch, quality-check and publish one dataset window.")
research_app = typer.Typer(help="Run and publish one formal experiment spec.")
backtest_app = typer.Typer(help="Scratch backtests that never publish experiments.")
report_app = typer.Typer(help="Render self-contained reports from committed artifacts.")

app.add_typer(data_app, name="data")
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
) -> "PublishedExperiment":
    """Run a spec; ``registry`` overrides the formal experiments root."""
    runner = ResearchRunner(
        project_root,
        analytics=_ExperimentAnalytics(project_root),
        secrets=_secret_values(),
    )
    if registry is not None:
        runner._registry = registry  # type: ignore[attr-defined]
    try:
        return runner.run(spec)
    except ResearchRunFailed as error:
        _echo_failure(str(error))
        raise typer.Exit(code=1) from None


# --------------------------------------------------------------------------- #
# data group
# --------------------------------------------------------------------------- #


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
    typer.echo(_report_summary(result.quality_report))
    for status in result.source_status:
        state = "ok" if status.ok else "not_ok"
        typer.echo(f"source {status.source}: {state}")
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
    if decision.passed:
        typer.echo("PASS")
        return
    _echo_failure("quality gate did not pass")
    raise typer.Exit(code=1)


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
    """Run one experiment spec end-to-end and publish it (content-addressed)."""
    published = _run_one_research(Path(root), spec)
    typer.echo(f"experiment_id={published.experiment_id}")
    typer.echo(f"published={published.path}")


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
) -> None:
    """Run the momentum spec into the debug area without publishing formally."""
    published = _run_one_research(Path(root), spec, registry=_DebugRegistry(Path(root)))
    typer.echo(f"experiment_id={published.experiment_id}")
    typer.echo(f"debug={published.path}")


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
    """Render the richer experiment report from one published experiment."""
    project_root = Path(root)
    experiment_id = experiment or _latest_experiment_id(project_root)
    if experiment_id is None:
        _echo_failure("no published experiment found under data/experiments")
        raise typer.Exit(code=1)
    try:
        run_input = _experiment_report_input(project_root, experiment_id)
        destination = out or (
            project_root / "data" / "reports" / f"{experiment_id}.html"
        )
        render_experiment_report(run_input, destination)
    except Exception as error:  # noqa: BLE001 - surface cleanly to the operator
        _echo_failure(str(error))
        raise typer.Exit(code=1) from None
    typer.echo(f"report={destination}")


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

    benchmark = _benchmark_closes(project_root, dataset_version, benchmark_symbols)
    scenarios: list[ExperimentScenario] = []
    for name in scenario_names:
        scenario_dir = run_dir / "backtest" / name
        if not scenario_dir.is_dir():
            continue
        equity = pd.read_parquet(scenario_dir / "daily_equity.parquet")
        fills = pd.read_parquet(scenario_dir / "fills.parquet")
        rejections = pd.read_parquet(scenario_dir / "rejections.parquet")
        action_ledger = pd.read_parquet(scenario_dir / "action_ledger.parquet")
        metrics_out = compute_metrics(equity, fills, benchmark)
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
        planned = pd.read_parquet(
            metrics_input.run_dir / "orders.parquet",
            columns=["order_id", "quantity"],
        )
        planned_quantity = {
            str(row["order_id"]): int(row["quantity"])
            for row in planned.to_dict("records")
        }
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
            start = float(equity["total_equity"].iloc[0])
            end = float(equity["total_equity"].iloc[-1])
            n_rejections = int(len(rejections))
            filled_quantity = {
                str(row["order_id"]): int(row["quantity"])
                for row in fills[["order_id", "quantity"]].to_dict("records")
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
                "plan_diverged": any(
                    filled_quantity.get(order_id, 0) != quantity
                    for order_id, quantity in planned_quantity.items()
                ),
                "performance": compute_metrics(
                    equity, fills, benchmark
                ).to_dict(),
            }
            scenarios[scenario] = summary
        return {"scenarios": scenarios}
