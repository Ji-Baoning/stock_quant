#!/usr/bin/env python
"""Remove disposable derived data while preserving the current reusable dataset.

Status: active.

The default is a dry run.  Deletion requires both ``--apply`` and an exact
``--confirm-current`` value read from ``data/standardized/CURRENT``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import sys


_VERSION = re.compile(r"^[0-9a-f]{64}$")
_CLEAR_ALL_CHILDREN = ("experiments", "reports", "runs", "staging", "acceptance")
_VERSIONED_CHILDREN = ("acceptances", "acceptance-evidence", "acceptance-worksheets")


class CleanupError(ValueError):
    """A requested cleanup does not meet the safety contract."""


def _project_root(value: str) -> Path:
    root = Path(value).resolve()
    if not root.is_dir() or not all(
        (root / "configs" / name).is_file()
        for name in ("project.yml", "sources.yml", "costs.yml")
    ):
        raise CleanupError(f"not a valid project root: {value}")
    return root


def _current_version(data: Path) -> str:
    pointer = data / "standardized" / "CURRENT"
    if pointer.is_symlink() or not pointer.is_file():
        raise CleanupError("CURRENT must be a regular file")
    value = pointer.read_text(encoding="utf-8").strip()
    if not _VERSION.fullmatch(value):
        raise CleanupError("CURRENT must contain one lowercase SHA-256 version")
    current = data / "standardized" / value
    if current.is_symlink() or not current.is_dir():
        raise CleanupError("CURRENT does not name a regular standardized dataset")
    return value


def _children(data: Path, name: str) -> tuple[Path, ...]:
    directory = data / name
    if not directory.exists():
        return ()
    if directory.is_symlink() or not directory.is_dir():
        raise CleanupError(f"cleanup directory must be a regular directory: data/{name}")
    entries = tuple(sorted(directory.iterdir()))
    for entry in entries:
        if entry.is_symlink():
            raise CleanupError(f"refusing symbolic-link cleanup target: {entry.relative_to(data)}")
    return entries


def cleanup_plan(root: Path) -> tuple[str, tuple[Path, ...]]:
    """Return the pinned current version and the fixed-whitelist removal plan."""
    data = root / "data"
    if data.is_symlink() or not data.is_dir():
        raise CleanupError("data must be a regular directory")
    current = _current_version(data)
    plan: list[Path] = []

    for entry in _children(data, "standardized"):
        if entry.name != "CURRENT" and _VERSION.fullmatch(entry.name) and entry.name != current:
            plan.append(entry)
    for name in _CLEAR_ALL_CHILDREN:
        plan.extend(_children(data, name))
    for name in _VERSIONED_CHILDREN:
        plan.extend(
            entry
            for entry in _children(data, name)
            if _VERSION.fullmatch(entry.name) and entry.name != current
        )
    return current, tuple(plan)


def _size(path: Path) -> int:
    """Return bytes below a checked, non-symlink candidate without following links."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for directory, _, filenames in os.walk(path, followlinks=False):
        total += sum((Path(directory) / name).stat().st_size for name in filenames)
    return total


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="explicit project root")
    parser.add_argument("--apply", action="store_true", help="perform the printed removal plan")
    parser.add_argument("--confirm-current", help="exact CURRENT SHA-256 required with --apply")
    args = parser.parse_args(argv)
    try:
        root = _project_root(args.root)
        current, plan = cleanup_plan(root)
        if args.apply and args.confirm_current != current:
            raise CleanupError("--confirm-current does not match CURRENT")
        if not args.apply and args.confirm_current is not None:
            raise CleanupError("--confirm-current requires --apply")
        total = sum(_size(path) for path in plan)
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"{mode} current={current} remove_count={len(plan)} total_bytes={total}")
        for path in plan:
            print(path.relative_to(root).as_posix())
        if args.apply:
            for path in plan:
                _remove(path)
    except CleanupError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
