"""Safety contract for the standalone project test-data cleanup tool."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "project" / "clean_test_data.py"
CURRENT = "a" * 64
OLD = "b" * 64


def _project(root: Path) -> Path:
    """Create a minimal project root with retained and removable test data."""
    for name in ("project.yml", "sources.yml", "costs.yml"):
        path = root / "configs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    data = root / "data"
    (data / "standardized" / "CURRENT").parent.mkdir(parents=True)
    (data / "standardized" / "CURRENT").write_text(CURRENT + "\n", encoding="utf-8")
    for version in (CURRENT, OLD):
        (data / "standardized" / version).mkdir()
        (data / "standardized" / version / "dataset_manifest.json").write_text(
            "{}\n", encoding="utf-8"
        )

    for relative in (
        "raw/tushare/snapshot.json",
        "membership/custom_csi300_tw.parquet",
        "acceptance-external-inputs/candidate.json",
        "experiments/example/metrics.json",
        "reports/report.html",
        "runs/debug/output.json",
        "staging/run-1/payload.json",
        "acceptance/legacy.json",
        f"acceptances/{CURRENT}/accepted/acceptance.json",
        f"acceptances/{OLD}/old/acceptance.json",
        f"acceptance-evidence/{CURRENT}/evidence.json",
        f"acceptance-evidence/{OLD}/evidence.json",
        f"acceptance-worksheets/{CURRENT}/check.md",
        f"acceptance-worksheets/{OLD}/check.md",
        "unknown/keep.txt",
    ):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    return root


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_dry_run_lists_only_disposable_data_and_changes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")

    result = _run(root)

    assert result.returncode == 0
    assert f"data/standardized/{OLD}" in result.stdout
    assert "data/raw" not in result.stdout
    assert "data/membership" not in result.stdout
    assert "data/acceptance-external-inputs" not in result.stdout
    assert (root / "data" / "standardized" / OLD).is_dir()
    assert (root / "data" / "experiments" / "example").is_dir()


def test_apply_removes_only_disposable_data_and_keeps_current_chain(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")

    result = _run(root, "--apply", "--confirm-current", CURRENT)

    assert result.returncode == 0
    for relative in (
        f"standardized/{OLD}",
        "experiments/example",
        "reports/report.html",
        "runs/debug",
        "staging/run-1",
        "acceptance/legacy.json",
        f"acceptances/{OLD}",
        f"acceptance-evidence/{OLD}",
        f"acceptance-worksheets/{OLD}",
    ):
        assert not (root / "data" / relative).exists()
    for relative in (
        "raw/tushare/snapshot.json",
        "membership/custom_csi300_tw.parquet",
        "acceptance-external-inputs/candidate.json",
        f"standardized/{CURRENT}/dataset_manifest.json",
        f"acceptances/{CURRENT}/accepted/acceptance.json",
        f"acceptance-evidence/{CURRENT}/evidence.json",
        f"acceptance-worksheets/{CURRENT}/check.md",
        "unknown/keep.txt",
    ):
        assert (root / "data" / relative).exists()


def test_apply_requires_an_exact_current_confirmation(tmp_path: Path) -> None:
    root = _project(tmp_path / "project")

    result = _run(root, "--apply", "--confirm-current", OLD)

    assert result.returncode == 2
    assert "does not match CURRENT" in result.stderr
    assert (root / "data" / "experiments" / "example").is_dir()
