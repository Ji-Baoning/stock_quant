import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools" / "check_context_governance.py"


def test_checker_reports_missing_root_protocol(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "ERROR: missing required file: AGENTS.md" in result.stdout
    assert "ERROR: missing required file: CLAUDE.md" in result.stdout
