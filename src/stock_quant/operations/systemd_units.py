"""Rendering helpers for the committed systemd timer units (spec 9.4).

Pure functions only: nothing here installs, enables or starts anything, and
no test requires systemd.  The committed units under ``systemd/`` are the
authoritative templates; these helpers exist so operators and tests can
derive the instance unit name for a project root without calling
``systemd-escape``.  In the unit files, ``%f`` expands the escaped
instance name back to the absolute project root.
"""

from __future__ import annotations

import string
from pathlib import Path

#: Fixed unit prefix; the instance is the systemd-escaped project root
#: (spec 9.4: ``stock-quant-data-update@<project-id>.timer``).
TIMER_UNIT_PREFIX = "stock-quant-data-update"

#: The pinned interpreter (Global Constraints); the units hard-code it too.
INTERPRETER = "/home/ji/miniconda3/envs/sq312/bin/python"

#: Characters systemd-escape leaves untouched ('-' is meaningful and '.' is
#: special only in leading position, so both are handled separately).
_UNRESERVED = frozenset(string.ascii_letters + string.digits + ":_.")


def systemd_escape_path(project_root: Path) -> str:
    """``systemd-escape --path`` for an absolute path (the needed subset).

    ``/home/ji/work/program/stock`` -> ``home-ji-work-program-stock``; a
    literal '-' becomes ``\\x2d`` and a space ``\\x20`` so decoding is
    unambiguous.
    """
    text = str(Path(project_root))
    if not text.startswith("/"):
        raise ValueError(f"an absolute project root is required, got {text!r}")
    pieces: list[str] = []
    for character in text[1:]:
        if character == "/":
            pieces.append("-")
        elif character in _UNRESERVED:
            pieces.append(character)
        else:
            pieces.append(f"\\x{ord(character):02x}")
    escaped = "".join(pieces)
    if escaped.startswith("."):
        escaped = "\\x2e" + escaped[1:]
    return escaped


def timer_unit_name(project_root: Path) -> str:
    """The timer instance unit name for one project root."""
    return f"{TIMER_UNIT_PREFIX}@{systemd_escape_path(project_root)}.timer"


def service_unit_name(project_root: Path) -> str:
    """The oneshot service instance unit name for one project root."""
    return f"{TIMER_UNIT_PREFIX}@{systemd_escape_path(project_root)}.service"
