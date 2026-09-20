"""Criterion 3: tier literals live only in data_contracts.py (static scan).

Tier values enter the runtime exclusively through the sources.yml loading
path; a negative assertion can only be constructed as a source scan.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
TIER_LITERALS = frozenset({"core", "anchored", "research_only"})


def _string_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def test_tier_literals_only_in_data_contracts_module():
    offenders: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "data_contracts.py":
            continue
        for literal in _string_literals(path):
            if literal in TIER_LITERALS:
                offenders.setdefault(str(path.relative_to(SRC)), set()).add(
                    literal
                )
    assert not offenders, f"tier literals outside data_contracts.py: {offenders}"
