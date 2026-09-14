"""Check that the required context-governance documents exist."""

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/architecture/overview.md",
    "docs/architecture/module-map.md",
    "docs/architecture/data-flow.md",
    "docs/architecture/invariants.md",
    "docs/adr/DECISIONS_INDEX.md",
)


def validate(root: Path) -> list[str]:
    """Return validation errors for required governance documents under *root*."""
    return [
        f"missing required file: {relative_path}"
        for relative_path in REQUIRED_FILES
        if not (root / relative_path).is_file()
    ]


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
