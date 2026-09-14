import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools" / "check_context_governance.py"
ADR_DIRECTORY = ROOT / "docs" / "adr"
ADR_INDEX = ADR_DIRECTORY / "DECISIONS_INDEX.md"
ADR_FILENAME = re.compile(r"\d{3}-[a-z0-9-]+\.md")
ADR_FRONTMATTER_FIELDS = ("status", "date", "decision", "affects")


def create_complete_governance_tree(root: Path) -> None:
    """Create the smallest governance tree that satisfies the structure contract."""
    required_files = (
        "AGENTS.md",
        "CLAUDE.md",
        "docs/architecture/overview.md",
        "docs/architecture/module-map.md",
        "docs/architecture/data-flow.md",
        "docs/architecture/invariants.md",
    )
    for relative_path in required_files:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("governance document\n", encoding="utf-8")

    adr_directory = root / "docs" / "adr"
    adr_directory.mkdir(parents=True)
    (adr_directory / "DECISIONS_INDEX.md").write_text(
        "[Existing](001-existing.md)\n", encoding="utf-8"
    )
    (adr_directory / "001-existing.md").write_text("ADR\n", encoding="utf-8")

    (root / "AGENTS.md").write_text(
        "用户即时指令 > 安全与平台指令 > 路径规则\n更具体的路径规则优先\n",
        encoding="utf-8",
    )
    rule_directory = root / ".claude" / "rules"
    rule_directory.mkdir(parents=True)
    for filename in (
        "data.md",
        "research.md",
        "portfolio.md",
        "config-and-operations.md",
        "tests.md",
    ):
        (rule_directory / filename).write_text(
            "Paths: src/\nRead first: docs/architecture/overview.md\n",
            encoding="utf-8",
        )


def run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_checker_reports_missing_root_protocol(tmp_path: Path) -> None:
    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert "ERROR: missing required file: AGENTS.md" in result.stdout
    assert "ERROR: missing required file: CLAUDE.md" in result.stdout


def test_checker_accepts_complete_governance_tree(tmp_path: Path) -> None:
    create_complete_governance_tree(tmp_path)

    result = run_checker(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""


def test_checker_rejects_indexed_adr_that_does_not_exist(tmp_path: Path) -> None:
    create_complete_governance_tree(tmp_path)
    (tmp_path / "docs" / "adr" / "DECISIONS_INDEX.md").write_text(
        "[Missing](099-missing.md)\n", encoding="utf-8"
    )

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert "ERROR: indexed ADR does not exist: docs/adr/099-missing.md" in result.stdout


@pytest.mark.parametrize(
    ("path", "content", "expected_error"),
    [
        (
            "docs/adr/DECISIONS_INDEX.md",
            "[Outside](../architecture/overview.md)\n",
            "ERROR: indexed ADR escapes docs/adr/: ../architecture/overview.md",
        ),
        (
            ".claude/rules/core.md",
            "Read first: docs/architecture/overview.md\n",
            "ERROR: path rule missing Paths:: .claude/rules/core.md",
        ),
        (
            ".claude/rules/core.md",
            "Paths: src/\n",
            "ERROR: path rule missing Read first:: .claude/rules/core.md",
        ),
    ],
)
def test_checker_rejects_invalid_governance_structure(
    tmp_path: Path, path: str, content: str, expected_error: str
) -> None:
    create_complete_governance_tree(tmp_path)
    (tmp_path / path).write_text(content, encoding="utf-8")

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert expected_error in result.stdout


@pytest.mark.parametrize(
    ("path", "line_count", "expected_error"),
    [
        ("AGENTS.md", 121, "ERROR: document exceeds 120 lines: AGENTS.md"),
        (
            "docs/architecture/overview.md",
            401,
            "ERROR: document exceeds 400 lines: docs/architecture/overview.md",
        ),
        (
            "docs/adr/001-existing.md",
            401,
            "ERROR: document exceeds 400 lines: docs/adr/001-existing.md",
        ),
        (
            ".claude/rules/core.md",
            401,
            "ERROR: document exceeds 400 lines: .claude/rules/core.md",
        ),
    ],
)
def test_checker_rejects_governance_documents_over_line_limits(
    tmp_path: Path, path: str, line_count: int, expected_error: str
) -> None:
    create_complete_governance_tree(tmp_path)
    (tmp_path / path).write_text("line\n" * line_count, encoding="utf-8")

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert expected_error in result.stdout


@pytest.mark.parametrize(
    ("relative_path", "required_text"),
    [
        ("docs/architecture/overview.md", "reproducible"),
        ("docs/architecture/module-map.md", "data_sources"),
        ("docs/architecture/data-flow.md", "dataset"),
        ("docs/architecture/invariants.md", "MUST NOT"),
    ],
)
def test_architecture_documents_cover_required_topics(
    relative_path: str, required_text: str
) -> None:
    """Each architecture fact file states the topic it is the authority for."""
    path = ROOT / relative_path
    assert path.is_file(), f"missing architecture document: {relative_path}"

    assert required_text in path.read_text(encoding="utf-8")


def test_checker_requires_at_least_one_path_rule(tmp_path: Path) -> None:
    create_complete_governance_tree(tmp_path)
    for rule in (tmp_path / ".claude" / "rules").glob("*.md"):
        rule.unlink()

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert "ERROR: no path rule files found: .claude/rules" in result.stdout


def test_checker_requires_protocol_priority_and_named_path_rules(tmp_path: Path) -> None:
    """A usable governance tree names its precedence and all task rule scopes."""
    create_complete_governance_tree(tmp_path)
    (tmp_path / "AGENTS.md").write_text("governance document\n", encoding="utf-8")
    for rule in (tmp_path / ".claude" / "rules").glob("*.md"):
        rule.unlink()
    (tmp_path / ".claude" / "rules" / "core.md").write_text(
        "Paths: src/\nRead first: docs/architecture/overview.md\n",
        encoding="utf-8",
    )

    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert (
        "ERROR: root protocol missing required guidance: AGENTS.md: "
        "用户即时指令 > 安全与平台指令 > 路径规则"
    ) in result.stdout
    for filename in (
        "data.md",
        "research.md",
        "portfolio.md",
        "config-and-operations.md",
        "tests.md",
    ):
        assert f"ERROR: missing required path rule: .claude/rules/{filename}" in result.stdout


def adr_files() -> list[Path]:
    """Every numbered ADR file, excluding the index."""
    if not ADR_DIRECTORY.is_dir():
        return []
    return sorted(
        path
        for path in ADR_DIRECTORY.glob("*.md")
        if path.name != ADR_INDEX.name
    )


def adr_frontmatter(path: Path) -> dict:
    """The YAML frontmatter block of one ADR document."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"ADR must open with YAML frontmatter: {path.name}"
    return yaml.safe_load(text.split("---\n", 2)[1]) or {}


def test_adr_directory_holds_at_least_five_numbered_decisions() -> None:
    adrs = adr_files()

    assert len(adrs) >= 5, "expected at least five recorded ADRs"
    for path in adrs:
        assert ADR_FILENAME.fullmatch(path.name), f"unnumbered ADR: {path.name}"


@pytest.mark.parametrize("field", ADR_FRONTMATTER_FIELDS)
def test_every_adr_declares_required_frontmatter_field(field: str) -> None:
    adrs = adr_files()
    assert adrs, "no ADR documents to check"

    for path in adrs:
        assert field in adr_frontmatter(path), f"{path.name} lacks frontmatter: {field}"


def test_every_adr_is_indexed_by_relative_link() -> None:
    index = ADR_INDEX.read_text(encoding="utf-8")

    for path in adr_files():
        assert f"]({path.name})" in index, f"{path.name} is not indexed"


def test_root_entry_points_link_the_authoritative_layers() -> None:
    """README and PROJECT_MEMORY must point at the new fact and decision layers."""
    assert "docs/architecture/" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/adr/DECISIONS_INDEX.md" in (ROOT / "PROJECT_MEMORY.md").read_text(
        encoding="utf-8"
    )
