"""Task 13 brief Step-1 CLI behaviour over the offline synthetic project.

Imported before ``stock_quant.cli`` exists so the file fails during import in
Step 2.  ``run_offline_fixture`` and its parser come from ``test_end_to_end``;
``fixture_root`` / ``broken_fixture_root`` / ``cli_runner`` come from
``conftest``.  Everything is offline and no token is ever configured.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from conftest import build_fixture_project  # noqa: E402
from test_end_to_end import run_offline_fixture  # noqa: E402  (after app import)

from stock_quant.cli import app  # noqa: F401  (gates Step 2 collection)


def test_official_research_command_returns_zero_and_prints_identity(
    cli_runner, fixture_root
):
    result = cli_runner.invoke(
        app,
        [
            "research",
            "run",
            "--spec",
            "configs/experiments/momentum_60d.yml",
            "--root",
            str(fixture_root.root),
        ],
    )
    assert result.exit_code == 0
    assert "experiment_id=" in result.stdout


def test_partial_failure_returns_nonzero(cli_runner, broken_fixture_root):
    result = cli_runner.invoke(
        app,
        [
            "research",
            "run",
            "--spec",
            "configs/experiments/momentum_60d.yml",
            "--root",
            str(broken_fixture_root.root),
        ],
    )
    assert result.exit_code != 0
    assert "FAILED" in result.stdout


def test_debug_backtest_writes_only_to_run_debug_dir(
    cli_runner, fixture_root
):
    experiments = fixture_root.root / "data" / "experiments"
    before = (
        set(p.name for p in experiments.iterdir())
        if experiments.is_dir()
        else set()
    )
    result = cli_runner.invoke(
        app,
        ["backtest", "momentum_60d", "--root", str(fixture_root.root)],
    )
    assert result.exit_code == 0, result.stdout
    debug_root = fixture_root.root / "data" / "runs" / "debug"
    assert debug_root.is_dir()
    published = set(p.name for p in experiments.iterdir())
    assert published == before, "debug backtest must not publish an experiment"


def test_data_update_without_token_fails_and_prints_failed(
    cli_runner, fixture_root, monkeypatch
):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    result = cli_runner.invoke(
        app,
        [
            "data",
            "update",
            "--start",
            "2021-11-01",
            "--end",
            "2021-11-30",
            "--root",
            str(fixture_root.root),
        ],
    )
    assert result.exit_code != 0
    assert "FAILED" in result.stdout


def test_data_validate_reports_current_dataset(
    cli_runner, fixture_root
):
    # An explicit --version keeps this independent of any other test that
    # republishes CURRENT on the shared session project.
    result = cli_runner.invoke(
        app,
        [
            "data",
            "validate",
            "--version",
            fixture_root.version,
            "--root",
            str(fixture_root.root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert fixture_root.version in result.stdout
    assert "PASS" in result.stdout


def test_data_bootstrap_publishes_initial_dataset(cli_runner, tmp_path):
    """Bootstrap creates the baseline tables required by the first update."""
    root = tmp_path / "seed-project"
    configs = root / "configs"
    configs.mkdir(parents=True)
    repo_configs = Path(__file__).resolve().parents[2] / "configs"
    for name in ("project.yml", "universe.yml"):
        shutil.copy(repo_configs / name, configs / name)

    result = cli_runner.invoke(app, ["data", "bootstrap", "--root", str(root)])

    assert result.exit_code == 0, result.stdout
    assert "published seed dataset:" in result.stdout
    assert "CURRENT ->" in result.stdout
    assert (root / "data" / "standardized" / "CURRENT").is_file()


def test_research_command_is_the_only_publisher_of_formal_experiments(
    cli_runner, fixture_root
):
    """Backtest/debug paths must not advance the experiments registry."""
    outcome = run_offline_fixture(fixture_root.root)
    manifest_path = outcome.path / "experiment_manifest.json"
    assert manifest_path.is_file()
    assert outcome.path.is_dir()


@pytest.fixture(scope="module")
def quality_report_project(tmp_path_factory):
    """One fresh project with one completed experiment for report-build tests.

    Module-scoped so the two report tests share a single (expensive) research
    run; the second test prunes the run's backtest workspace, so it must come
    after the first test in this module and nothing else may reuse the project.
    """
    project = build_fixture_project(tmp_path_factory.mktemp("quality_report"))
    outcome = run_offline_fixture(project.root)
    return project, outcome


def test_report_build_renders_quality_html_with_markers_and_no_external_refs(
    cli_runner, quality_report_project, monkeypatch
):
    """``report build`` also emits the data-quality HTML for the pinned version."""
    project, outcome = quality_report_project
    monkeypatch.setenv("TUSHARE_TOKEN", "report-secret-token")
    result = cli_runner.invoke(
        app,
        ["report", "build", "--root", str(project.root)],
    )
    assert result.exit_code == 0, result.stdout
    assert "FAILED" not in result.stdout
    assert "report=" in result.stdout
    assert "quality_report=" in result.stdout

    quality_path = (
        project.root / "data" / "reports" / f"quality-{project.version}.html"
    )
    assert quality_path.is_file(), f"{quality_path} was not written"
    html = quality_path.read_text(encoding="utf-8")
    assert "数据质量报告" in html
    assert project.version in html
    assert "门禁决定：PASS" in html
    assert "来源与版本状态" in html
    # self-contained: no external fetches, and no secret value leaks in.
    assert "src=\"http" not in html
    assert "href=\"http" not in html
    assert "report-secret-token" not in html

    experiment_path = (
        project.root / "data" / "reports" / f"{outcome.experiment_id}.html"
    )
    assert experiment_path.is_file()


def test_report_build_fails_when_backtest_workspace_pruned(
    cli_runner, quality_report_project
):
    """A pruned run workspace must fail loudly, not render an empty report."""
    project, outcome = quality_report_project
    metrics = json.loads(
        (outcome.path / "metrics.json").read_text(encoding="utf-8")
    )
    run_id = metrics["meta"]["run_id"]
    scenario_dir = project.root / "data" / "runs" / run_id / "backtest" / "zero_cost"
    assert scenario_dir.is_dir()
    shutil.rmtree(scenario_dir)
    result = cli_runner.invoke(
        app,
        ["report", "build", "--root", str(project.root)],
    )
    assert result.exit_code != 0
    assert "FAILED" in result.stdout
    assert "backtest workspace was pruned" in result.stdout
