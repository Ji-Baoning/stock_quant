"""External inputs: the operator-supplied official evidence an operator attests.

Three of the nine manual checks cannot be evidenced by this project alone (an
official exchange calendar, a second price source, official trading-rule
effective dates).  Whatever the operator supplies is copied into a
content-addressed, append-only store under the project root rather than
merely pointed at: a published record only pins the worksheet's hash, so an
excerpt deleted or swapped afterwards would leave an
``EXTERNAL_CORROBORATED`` claim nobody could re-check.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from stock_quant.research.acceptance.worksheet import (
    WorksheetError,
    external_inputs_root,
)

__all__ = ["StoredBlob", "load_blob", "store_blob"]


@dataclass(frozen=True)
class StoredBlob:
    """One stored external input: its project-relative path and its hash."""

    reference: str
    sha256: str
    name: str


def _safe_name(name: str) -> str:
    """A file name fit for the store, or a stable fallback."""
    candidate = Path(name).name
    if not candidate or candidate.startswith(".") or "\x00" in candidate:
        return "external-input"
    return candidate


def store_blob(project_root: Path, data: bytes, name: str) -> StoredBlob:
    """Copy bytes into the store under their SHA-256 and return the pointer.

    Identical content is stored exactly once, whatever name it arrives under;
    different content lands under a different digest and never overwrites.
    """
    root = Path(project_root).resolve()
    digest = hashlib.sha256(data).hexdigest()
    directory = external_inputs_root(root) / digest
    existing = (
        sorted(entry for entry in directory.iterdir()) if directory.is_dir() else []
    )
    if existing:
        chosen = existing[0]
    else:
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = directory.parent / f".{digest}.{uuid4().hex}.tmp"
        try:
            staging.mkdir()
            (staging / _safe_name(name)).write_bytes(data)
            if directory.exists():
                # A concurrent writer won the rename: reuse its copy.
                shutil.rmtree(staging, ignore_errors=True)
                chosen = sorted(directory.iterdir())[0]
            else:
                os.replace(staging, directory)
                chosen = directory / _safe_name(name)
        except OSError as error:
            shutil.rmtree(staging, ignore_errors=True)
            raise WorksheetError("external_input_invalid") from error
    return StoredBlob(
        reference=chosen.relative_to(root).as_posix(),
        sha256=digest,
        name=chosen.name,
    )


def load_blob(project_root: Path, reference: str) -> bytes:
    """Read one stored blob back, refusing any path outside the project root."""
    root = Path(project_root).resolve()
    try:
        candidate = (root / reference).resolve()
    except ValueError as error:
        raise WorksheetError("external_input_invalid") from error
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise WorksheetError("external_input_invalid")
    return candidate.read_bytes()
