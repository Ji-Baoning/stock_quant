"""Regression tests for the small operational-document contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools" / "check_operational_docs.py"


def test_duplicate_project_runbook_is_rejected(tmp_path: Path) -> None:
    """A project-local operation manual must not silently shadow the root one."""
    duplicate = tmp_path / "project" / "RUNBOOK.md"
    duplicate.parent.mkdir()
    duplicate.write_text("# obsolete duplicate\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "ERROR: duplicate operational runbook: project/RUNBOOK.md" in result.stdout


def test_script_inventory_lists_each_top_level_project_script() -> None:
    """The inventory must classify the complete, non-recursive script surface."""
    inventory = (REPO_ROOT / "project" / "SCRIPTS.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`(project/[^`/]+\.py)`", inventory))
    actual = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "project").glob("*.py")
    }

    assert documented == actual


def test_required_operational_documents_are_named_when_missing(tmp_path: Path) -> None:
    """A partial documentation root must report every required entry point."""
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "ERROR: missing required file: RUNBOOK.md" in result.stdout
    assert "ERROR: missing required file: project/SCRIPTS.md" in result.stdout
    assert "ERROR: missing required file: docs/operations/asset-retention.md" in result.stdout
    assert "ERROR: README.md does not link to root RUNBOOK.md" in result.stdout
