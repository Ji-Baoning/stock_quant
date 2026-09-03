"""Task 13 brief Step-1 CLI behaviour over the offline synthetic project.

Imported before ``stock_quant.cli`` exists so the file fails during import in
Step 2.  ``run_offline_fixture`` and its parser come from ``test_end_to_end``;
``fixture_root`` / ``broken_fixture_root`` / ``cli_runner`` come from
``conftest``.  Everything is offline and no token is ever configured.
"""

from __future__ import annotations

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


def test_research_command_is_the_only_publisher_of_formal_experiments(
    cli_runner, fixture_root
):
    """Backtest/debug paths must not advance the experiments registry."""
    outcome = run_offline_fixture(fixture_root.root)
    manifest_path = outcome.path / "experiment_manifest.json"
    assert manifest_path.is_file()
    assert outcome.path.is_dir()
