"""Complete offline acceptance: one reproducible research run over the CLI.

The three brief behaviour tests live here plus the acceptance file helpers
(``run_offline_fixture``) shared with ``test_cli.py``.  Everything runs over a
deterministic synthetic project from ``conftest``; nothing touches a network or
a token and no market data is committed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from stock_quant.cli import (
    app,  # noqa: F401  (imported before the CLI exists to gate Step 2)
)
from stock_quant.research.models import REQUIRED_ARTIFACTS


@dataclass(frozen=True)
class RunOutcome:
    """What one offline fixture run produced (experiment id + manifest dict)."""

    experiment_id: str
    path: Path
    manifest: dict


def run_offline_fixture(project_root) -> RunOutcome:
    """Invoke the real CLI research command and read the published manifest."""
    from typer.testing import CliRunner

    outcome = CliRunner().invoke(
        app,
        [
            "research",
            "run",
            "--spec",
            "configs/experiments/momentum_60d.yml",
            "--root",
            str(project_root),
        ],
    )
    assert outcome.exit_code == 0, outcome.stdout
    experiment_id = _parse_experiment_id(outcome.stdout)
    path = Path(project_root) / "data" / "experiments" / experiment_id
    manifest = json.loads(
        (path / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    return RunOutcome(experiment_id=experiment_id, path=path, manifest=manifest)


def _parse_experiment_id(stdout: str) -> str:
    for line in stdout.splitlines():
        if "experiment_id=" in line:
            return line.strip().split("experiment_id=", 1)[1].strip()
    raise AssertionError(f"no experiment_id= line in stdout:\n{stdout}")


def test_cached_end_to_end_run_is_reproducible(fixture_root):
    first = run_offline_fixture(fixture_root.root)
    second = run_offline_fixture(fixture_root.root)
    assert first.experiment_id == second.experiment_id
    assert first.manifest["artifacts"] == second.manifest["artifacts"]


def test_published_manifest_records_the_accepted_data_identity(fixture_root):
    """The formal CLI run pins the fixture's ACCEPTED record everywhere: the
    frozen spec, the experiment manifest and metrics.json carry the same
    concrete acceptance id -- never the CURRENT_ACCEPTED placeholder."""
    outcome = run_offline_fixture(fixture_root.root)
    assert fixture_root.acceptance_id is not None
    assert outcome.manifest["data_acceptance_id"] == fixture_root.acceptance_id
    spec = yaml.safe_load(
        (outcome.path / "experiment_spec.yml").read_text(encoding="utf-8")
    )
    assert spec["data_acceptance_id"] == fixture_root.acceptance_id
    metrics = json.loads(
        (outcome.path / "metrics.json").read_text(encoding="utf-8")
    )
    audit = metrics["data_acceptance"]
    assert audit["acceptance_id"] == fixture_root.acceptance_id
    assert audit["policy_version"] == "real-data-v1"
    assert audit["decision"] == "ACCEPTED"
    assert audit["operator_id"] == "integration-fixture"


def test_published_experiment_holds_complete_immutable_artifact_contract(
    fixture_root,
):
    outcome = run_offline_fixture(fixture_root.root)
    assert set(p.name for p in outcome.path.iterdir()) == REQUIRED_ARTIFACTS
    manifest = outcome.manifest
    assert manifest["experiment_id"] == outcome.experiment_id
    assert manifest["status"] in ("ACCEPTED", "REJECTED")
    assert manifest["dataset_version"] == fixture_root.version
    # The CLI analytics adapter must record the auditable keys plus the richer
    # PerformanceMetrics dictionary for every cost scenario.
    metrics = json.loads(
        (outcome.path / "metrics.json").read_text(encoding="utf-8")
    )
    assert isinstance(metrics["evaluation"], dict)
    scenarios = metrics["scenarios"]
    assert set(scenarios) == {"zero_cost", "commission_tax", "full_cost"}
    for summary in scenarios.values():
        assert int(summary["periods"]) > 0
        assert "n_rejections" in summary
        assert "plan_diverged" in summary
        assert isinstance(summary["performance"], dict)
        assert "max_drawdown" in summary["performance"]
        assert "benchmark_excess_return" in summary["performance"]
        assert "n_pretrade_adjustments" not in summary
        assert "planned_order_count" in summary
        assert "filled_order_count" in summary
        assert "unfilled_reason_counts" in summary
    run_id = metrics["meta"]["run_id"]
    plan = pd.read_parquet(outcome.path / "orders.parquet")
    submitted_frames: list[pd.DataFrame] = []
    for scenario in scenarios:
        scenario_dir = (
            Path(fixture_root.root) / "data" / "runs" / run_id / "backtest" / scenario
        )
        submitted = pd.read_parquet(scenario_dir / "submitted_orders.parquet")
        submitted_frames.append(submitted)
        assert (scenario_dir / "order_diffs.parquet").is_file()
        assert not (scenario_dir / "rebalance_adjustments.parquet").exists()
        assert not (scenario_dir / "executable_targets.parquet").exists()
        # The submitted set is exactly the frozen plan ledger.
        sub = submitted[["order_id", "side", "symbol", "quantity"]].sort_values(
            "order_id"
        ).reset_index(drop=True)
        planned = plan[["order_id", "side", "symbol", "quantity"]].sort_values(
            "order_id"
        ).reset_index(drop=True)
        assert sub.equals(planned)
    # Submitted orders are scenario-independent (spec section 6).
    for frame in submitted_frames[1:]:
        assert submitted_frames[0].equals(frame)
    html = (outcome.path / "report.html").read_text(encoding="utf-8")
    assert len(html) > 0


def test_metrics_are_deterministic_across_cached_runs(fixture_root):
    first = run_offline_fixture(fixture_root.root)
    second = run_offline_fixture(fixture_root.root)
    first_metrics = (first.path / "metrics.json").read_bytes()
    second_metrics = (second.path / "metrics.json").read_bytes()
    assert first_metrics == second_metrics


def test_published_metrics_report_realized_slippage_per_scenario(fixture_root):
    # Regression guard for the report bug where a configured slippage rate
    # produced no slippage figure: every fill now records its cent-quantized
    # reference open, so the published performance reflects it.
    outcome = run_offline_fixture(fixture_root.root)
    metrics = json.loads(
        (outcome.path / "metrics.json").read_text(encoding="utf-8")
    )
    scenarios = metrics["scenarios"]
    assert set(scenarios) == {"zero_cost", "commission_tax", "full_cost"}
    # The zero-rate scenarios fill exactly at each reference open, so the
    # realized slippage must be exactly zero.
    for name in ("zero_cost", "commission_tax"):
        assert scenarios[name]["performance"]["slippage_estimate"] == 0.0
    # full_cost adds 0.1% around real fixture opens, so its realized slippage
    # is strictly positive.
    assert scenarios["full_cost"]["performance"]["slippage_estimate"] > 0
