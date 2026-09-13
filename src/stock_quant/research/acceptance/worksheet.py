"""Standing acceptance worksheets: the operator's confirm main line.

Nine manual checks each get one worksheet per dataset version.  ``prepare``
writes an unsigned worksheet (``<version>/<code>.md``) whose program area is
refreshed on every run; ``confirm`` turns it into one immutable *revision*
(``<version>/<code>/<sha256>.md``) that the checklist binds to, and
``confirm --supersede`` appends a further revision that replaces the current
one without touching it.

The layout exists to enforce three rules:

* A signed revision is never overwritten, moved or deleted -- a published
  ``ACCEPTED`` record's evidence reference is a path plus a SHA-256, and
  :func:`stock_quant.research.acceptance.service.verify_acceptance_bindings`
  re-verifies every one of them on every research run.
* The checklist binds the *effective* revision only (the head of the
  ``supersedes`` chain).
* External inputs (official calendar and rule excerpts) are copied into a
  content-addressed, append-only store under the project root, so an
  ``EXTERNAL_CORROBORATED`` strength stays checkable after publication.

The one-line rule for reading the layout: ``<code>.md`` is always unsigned,
``<code>/<hash>.md`` is always signed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

__all__ = [
    "CONFIRMATION_STRENGTHS",
    "EXTERNAL_CORROBORATED",
    "EXTERNAL_INPUT_DIRNAME",
    "MARKER_HUMAN_BEGIN",
    "MARKER_HUMAN_END",
    "MARKER_PROGRAM_BEGIN",
    "MARKER_PROGRAM_END",
    "OPERATOR_ATTESTED",
    "SIGNATURE_FENCE",
    "WORKSHEET_DIRNAME",
    "WORKSHEET_ERROR_CATEGORIES",
    "WorksheetError",
    "append_signature",
    "external_inputs_root",
    "last_signature",
    "pending_path",
    "program_payload",
    "render_program",
    "render_worksheet",
    "revisions_dir",
    "signature_blocks",
    "verify_markers",
    "version_dir",
    "worksheets_root",
]

#: Directory (under the project root) holding every version's worksheets.
WORKSHEET_DIRNAME = "acceptance-worksheets"

#: Directory (under the project root) holding the append-only input store.
EXTERNAL_INPUT_DIRNAME = "acceptance-external-inputs"

#: The four markers bounding the program and human areas, in required order.
MARKER_PROGRAM_BEGIN = "<!-- ws:program:begin -->"
MARKER_PROGRAM_END = "<!-- ws:program:end -->"
MARKER_HUMAN_BEGIN = "<!-- ws:human:begin -->"
MARKER_HUMAN_END = "<!-- ws:human:end -->"
_MARKERS_IN_ORDER = (
    MARKER_PROGRAM_BEGIN,
    MARKER_PROGRAM_END,
    MARKER_HUMAN_BEGIN,
    MARKER_HUMAN_END,
)

#: The fenced-block info string every signature block carries.  Deliberately
#: not an HTML marker: the four markers above are the whole marker vocabulary,
#: and an unknown marker is rejected.
SIGNATURE_FENCE = "```ws-signature"

#: The closed set of confirmation strengths, decided by the program.
EXTERNAL_CORROBORATED = "EXTERNAL_CORROBORATED"
OPERATOR_ATTESTED = "OPERATOR_ATTESTED"
CONFIRMATION_STRENGTHS = (EXTERNAL_CORROBORATED, OPERATOR_ATTESTED)

#: The stable failure categories every worksheet operation reports.
WORKSHEET_ERROR_CATEGORIES = (
    "marker_invalid",
    "program_unreadable",
    "signed_worksheets_present",
    "signed_worksheet_drift",
    "already_signed",
    "nothing_to_supersede",
    "superseded_revision_drift",
    "revision_chain_invalid",
    "previous_signed_ambiguous",
    "acknowledgement_required",
    "conclusion_required",
    "external_input_invalid",
    "candidate_evidence_missing",
)


class WorksheetError(RuntimeError):
    """A worksheet operation refused to proceed.

    ``category`` is one of :data:`WORKSHEET_ERROR_CATEGORIES` and is the only
    thing a caller ever prints, so failures stay stable and free of paths.
    """

    def __init__(self, category: str) -> None:
        if category not in WORKSHEET_ERROR_CATEGORIES:
            raise ValueError(f"unknown worksheet error category {category!r}")
        self.category = category
        super().__init__(category)


def worksheets_root(project_root: Path) -> Path:
    """The directory holding every version's worksheets."""
    return Path(project_root) / "data" / WORKSHEET_DIRNAME


def version_dir(project_root: Path, dataset_version: str) -> Path:
    """The directory holding one dataset version's worksheets."""
    return worksheets_root(project_root) / dataset_version


def pending_path(project_root: Path, dataset_version: str, code: str) -> Path:
    """The one unsigned worksheet of one code.  Never cited by a checklist."""
    return version_dir(project_root, dataset_version) / f"{code}.md"


def revisions_dir(project_root: Path, dataset_version: str, code: str) -> Path:
    """The directory holding one code's immutable signed revisions."""
    return version_dir(project_root, dataset_version) / code


def external_inputs_root(project_root: Path) -> Path:
    """The content-addressed, append-only store for external inputs."""
    return Path(project_root) / "data" / EXTERNAL_INPUT_DIRNAME


def verify_markers(text: str) -> tuple[str, str]:
    """Split a worksheet into its program and human areas, or fail closed.

    Every marker must appear exactly once and in the order
    ``program:begin < program:end < human:begin < human:end``.  A missing
    human area is a violation, never something to create by guessing.
    """
    for marker in _MARKERS_IN_ORDER:
        if text.count(marker) != 1:
            raise WorksheetError("marker_invalid")
    positions = [text.index(marker) for marker in _MARKERS_IN_ORDER]
    if positions != sorted(positions):
        raise WorksheetError("marker_invalid")
    program = text[positions[0] + len(MARKER_PROGRAM_BEGIN) : positions[1]]
    human = text[positions[2] + len(MARKER_HUMAN_BEGIN) : positions[3]]
    return program, human


def render_program(payload: Mapping[str, object]) -> str:
    """Render one program area: a single fenced YAML block plus its markers."""
    body = yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True)
    return f"{MARKER_PROGRAM_BEGIN}\n```yaml\n{body}```\n{MARKER_PROGRAM_END}\n"


def program_payload(program_text: str) -> dict[str, object]:
    """Parse a program area back into its mapping, or fail closed."""
    lines = program_text.strip().splitlines()
    if len(lines) < 2 or lines[0].strip() != "```yaml":
        raise WorksheetError("program_unreadable")
    if lines[-1].strip() != "```":
        raise WorksheetError("program_unreadable")
    try:
        payload = yaml.safe_load("\n".join(lines[1:-1]))
    except yaml.YAMLError as error:
        raise WorksheetError("program_unreadable") from error
    if not isinstance(payload, dict):
        raise WorksheetError("program_unreadable")
    return payload


def append_signature(human: str, block: Mapping[str, object]) -> str:
    """Append one signature block to a human area, appending only."""
    body = yaml.safe_dump(dict(block), sort_keys=False, allow_unicode=True)
    if human and not human.endswith("\n"):
        human += "\n"
    return f"{human}{SIGNATURE_FENCE}\n{body}```\n"


def signature_blocks(human: str) -> tuple[dict[str, object], ...]:
    """Every signature block in a human area, in file order."""
    blocks: list[dict[str, object]] = []
    lines = human.splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip() == SIGNATURE_FENCE:
            body: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() != "```":
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                raise WorksheetError("marker_invalid")
            try:
                payload = yaml.safe_load("\n".join(body))
            except yaml.YAMLError as error:
                raise WorksheetError("marker_invalid") from error
            if not isinstance(payload, dict):
                raise WorksheetError("marker_invalid")
            blocks.append(payload)
        index += 1
    return tuple(blocks)


def last_signature(human: str) -> dict[str, object]:
    """The effective signature of one human area, or fail closed."""
    blocks = signature_blocks(human)
    if not blocks:
        raise WorksheetError("marker_invalid")
    return blocks[-1]


def render_worksheet(program: str, human: str) -> str:
    """Render one whole worksheet: program area then human area.

    A program area the caller left open is closed with
    :data:`MARKER_PROGRAM_END` here; the parsing side
    (:func:`verify_markers`) never guesses.
    """
    if program and not program.endswith("\n"):
        program += "\n"
    if MARKER_PROGRAM_END not in program:
        program += f"{MARKER_PROGRAM_END}\n"
    if human and not human.endswith("\n"):
        human += "\n"
    return f"{program}\n{MARKER_HUMAN_BEGIN}\n{human}{MARKER_HUMAN_END}\n"
