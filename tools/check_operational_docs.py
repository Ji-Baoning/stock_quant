"""Check the minimal operational-document contract."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


SCRIPT_PATH = re.compile(r"`(project/[^`/]+\.py)`")
REQUIRED_FILES = (
    "RUNBOOK.md",
    "project/SCRIPTS.md",
    "docs/operations/asset-retention.md",
)


def validate(root: Path) -> list[str]:
    """Return operational-document errors for *root*."""
    errors: list[str] = []
    errors.extend(
        f"missing required file: {relative_path}"
        for relative_path in REQUIRED_FILES
        if not (root / relative_path).is_file()
    )
    duplicate = "project/RUNBOOK.md"
    if (root / duplicate).is_file():
        errors.append(f"duplicate operational runbook: {duplicate}")
    inventory = root / "project/SCRIPTS.md"
    if inventory.is_file():
        documented = set(SCRIPT_PATH.findall(inventory.read_text(encoding="utf-8")))
        actual = {
            path.relative_to(root).as_posix() for path in (root / "project").glob("*.py")
        }
        if documented != actual:
            errors.append("script inventory does not match top-level project scripts")
    readme = root / "README.md"
    if not readme.is_file() or "[RUNBOOK.md](RUNBOOK.md)" not in readme.read_text(
        encoding="utf-8"
    ):
        errors.append("README.md does not link to root RUNBOOK.md")
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
