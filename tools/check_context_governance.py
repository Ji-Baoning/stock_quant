"""Check that the required context-governance documents exist."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


REQUIRED_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/architecture/overview.md",
    "docs/architecture/module-map.md",
    "docs/architecture/data-flow.md",
    "docs/architecture/invariants.md",
    "docs/adr/DECISIONS_INDEX.md",
)

ADR_LINK = re.compile(r"\[[^]]+\]\(([^)]+\.md)\)")
ROOT_PROTOCOL_FILES = ("AGENTS.md", "CLAUDE.md")
ARCHITECTURE_FILES = (
    "docs/architecture/overview.md",
    "docs/architecture/module-map.md",
    "docs/architecture/data-flow.md",
    "docs/architecture/invariants.md",
)
ADR_INDEX = "docs/adr/DECISIONS_INDEX.md"
RULES_DIRECTORY = ".claude/rules"
ROOT_PROTOCOL_REQUIRED_GUIDANCE = (
    "用户即时指令 > 安全与平台指令 > 路径规则",
    "更具体的路径规则优先",
)
REQUIRED_RULE_FILES = (
    "data.md",
    "research.md",
    "portfolio.md",
    "config-and-operations.md",
    "tests.md",
)


def validate(root: Path) -> list[str]:
    """Return validation errors for required governance documents under *root*."""
    errors = [
        f"missing required file: {relative_path}"
        for relative_path in REQUIRED_FILES
        if not (root / relative_path).is_file()
    ]
    errors.extend(_line_limit_errors(root))
    errors.extend(_indexed_adr_errors(root))
    errors.extend(_root_protocol_errors(root))
    errors.extend(_path_rule_errors(root))
    return errors


def _line_limit_errors(root: Path) -> list[str]:
    """Return errors for governance documents exceeding their bounded context."""
    limited_files = [
        *((path, 120) for path in ROOT_PROTOCOL_FILES),
        *((path, 400) for path in ARCHITECTURE_FILES),
    ]
    adr_directory = root / "docs/adr"
    if adr_directory.is_dir():
        limited_files.extend(
            (path.relative_to(root).as_posix(), 400)
            for path in adr_directory.glob("*.md")
            if path.name != Path(ADR_INDEX).name
        )
    limited_files.extend(
        (path.relative_to(root).as_posix(), 400)
        for path in _path_rule_files(root)
    )
    return [
        f"document exceeds {limit} lines: {relative_path}"
        for relative_path, limit in limited_files
        if (path := root / relative_path).is_file()
        and len(path.read_text(encoding="utf-8").splitlines()) > limit
    ]


def _indexed_adr_errors(root: Path) -> list[str]:
    """Return errors for index links that are not local, existing ADR files."""
    index = root / ADR_INDEX
    if not index.is_file():
        return []

    adr_directory = index.parent.resolve()
    errors: list[str] = []
    for match in ADR_LINK.finditer(index.read_text(encoding="utf-8")):
        target = match.group(1)
        candidate = (adr_directory / target).resolve()
        if candidate.parent != adr_directory:
            errors.append(f"indexed ADR escapes docs/adr/: {target}")
        elif not candidate.is_file():
            errors.append(
                "indexed ADR does not exist: "
                f"{candidate.relative_to(root.resolve()).as_posix()}"
            )
    return errors


def _root_protocol_errors(root: Path) -> list[str]:
    """Return errors when either root protocol lacks required guidance."""
    errors: list[str] = []
    for relative_path in ROOT_PROTOCOL_FILES:
        protocol = root / relative_path
        if not protocol.is_file():
            continue
        content = protocol.read_text(encoding="utf-8")
        errors.extend(
            "root protocol missing required guidance: "
            f"{relative_path}: {guidance}"
            for guidance in ROOT_PROTOCOL_REQUIRED_GUIDANCE
            if guidance not in content
        )
    return errors


def _path_rule_files(root: Path) -> list[Path]:
    rules_directory = root / RULES_DIRECTORY
    return sorted(rules_directory.rglob("*.md")) if rules_directory.is_dir() else []


def _path_rule_errors(root: Path) -> list[str]:
    """Return errors when the Claude path-rule directory lacks usable rules."""
    rule_files = _path_rule_files(root)
    if not rule_files:
        return [f"no path rule files found: {RULES_DIRECTORY}"]

    errors = [
        f"missing required path rule: {RULES_DIRECTORY}/{filename}"
        for filename in REQUIRED_RULE_FILES
        if not (root / RULES_DIRECTORY / filename).is_file()
    ]
    for rule in rule_files:
        relative_path = rule.relative_to(root).as_posix()
        content = rule.read_text(encoding="utf-8")
        if "Paths:" not in content:
            errors.append(f"path rule missing Paths:: {relative_path}")
        if "Read first:" not in content:
            errors.append(f"path rule missing Read first:: {relative_path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    errors = validate(args.root)
    for error in errors:
        print(f"ERROR: {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
