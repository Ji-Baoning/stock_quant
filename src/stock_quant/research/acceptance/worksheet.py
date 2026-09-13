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

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from stock_quant.research.acceptance.models import MANUAL_CHECK_CODES

__all__ = [
    "CONFIRMATION_STRENGTHS",
    "EXTERNAL_CORROBORATED",
    "EXTERNAL_INPUT_DIRNAME",
    "MARKER_HUMAN_BEGIN",
    "MARKER_HUMAN_END",
    "MARKER_PROGRAM_BEGIN",
    "MARKER_PROGRAM_END",
    "OPERATOR_ATTESTED",
    "Revision",
    "SIGNATURE_FENCE",
    "SIGNED_SUMMARY",
    "WORKSHEET_DIRNAME",
    "WORKSHEET_ERROR_CATEGORIES",
    "WorksheetError",
    "append_signature",
    "effective_revision",
    "external_inputs_root",
    "last_signature",
    "latest_pass_revision",
    "pending_path",
    "previous_pass_revision",
    "program_payload",
    "render_program",
    "render_worksheet",
    "revision_chain",
    "revisions_dir",
    "signature_blocks",
    "signed_codes",
    "verify_markers",
    "version_dir",
    "worksheets_root",
    "write_revision",
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

#: The two decisions a signature block may carry.
_SIGNED_DECISIONS = ("PASS", "FAIL")

#: The conclusion-free summary a signed manual row carries.  The signature and
#: the conclusion live in the worksheet's human area only: a row's ``summary``
#: is rewritten by every supersede, so putting the old conclusion there would
#: leave a stale judgement attached to the newest decision.
SIGNED_SUMMARY = {
    "PASS": "operator confirmed on worksheet revision",
    "FAIL": "operator rejected on worksheet revision",
}

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
    "unknown_check_code",
    "worksheet_missing",
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


@dataclass(frozen=True)
class Revision:
    """One immutable signed worksheet revision."""

    reference: str
    sha256: str
    path: Path
    payload: Mapping[str, object]
    human: str
    decision: str
    confirmed_at: str
    operator_id: str
    supersedes: str | None


def _sha256_file(path: Path) -> str:
    """The lowercase hex SHA-256 of one file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _revision_from_path(project_root: Path, path: Path) -> Revision:
    """Parse one stored revision, enforcing its content-addressed name.

    A file whose bytes no longer hash to its own name can never be a
    baseline: its ``sha256`` is the identity every checklist cites.  A file
    that no longer parses as a worksheet is the same corruption seen from
    the other side, so both fail as ``revision_chain_invalid``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WorksheetError("revision_chain_invalid") from error
    try:
        program, human = verify_markers(text)
        payload = program_payload(program)
        signature = last_signature(human)
    except WorksheetError as error:
        raise WorksheetError("revision_chain_invalid") from error
    digest = _sha256_file(path)
    if path.stem != digest:
        raise WorksheetError("revision_chain_invalid")
    decision = str(signature.get("decision", ""))
    if decision not in _SIGNED_DECISIONS:
        raise WorksheetError("revision_chain_invalid")
    supersedes = payload.get("supersedes")
    return Revision(
        reference=path.relative_to(Path(project_root).resolve()).as_posix(),
        sha256=digest,
        path=path,
        payload=payload,
        human=human,
        decision=decision,
        confirmed_at=str(signature.get("confirmed_at", "")),
        operator_id=str(signature.get("operator_id", "")),
        supersedes=(
            str(supersedes["reference"])
            if isinstance(supersedes, dict) and "reference" in supersedes
            else None
        ),
    )


def write_revision(
    project_root: Path,
    dataset_version: str,
    code: str,
    payload: Mapping[str, object],
    human: str,
) -> Revision:
    """Write one immutable revision under its own SHA-256, or reuse it.

    The file is written to a temporary name, hashed, and then renamed to its
    digest, so a crash never leaves a file whose name disagrees with its
    bytes.  An already-present revision is left untouched: revisions are only
    ever appended.
    """
    root = Path(project_root).resolve()
    directory = revisions_dir(root, dataset_version, code)
    directory.mkdir(parents=True, exist_ok=True)
    if payload.get("code") != code:
        raise WorksheetError("revision_chain_invalid")
    text = render_worksheet(render_program(payload), human)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    destination = directory / f"{digest}.md"
    if not destination.is_file():
        staging = directory / f".{digest}.{uuid4().hex}.tmp"
        try:
            staging.write_text(text, encoding="utf-8")
            os.replace(staging, destination)
        except OSError as error:
            staging.unlink(missing_ok=True)
            raise WorksheetError("revision_chain_invalid") from error
    return _revision_from_path(root, destination)


def revision_chain(
    project_root: Path, dataset_version: str, code: str
) -> tuple[Revision, ...]:
    """Every signed revision of one code, oldest first along ``supersedes``.

    Fails closed on anything the chain cannot explain: an unparseable or
    renamed file, a ``supersedes`` target that is missing, a fork (two heads)
    or a cycle.  There is no "best effort" head.
    """
    root = Path(project_root).resolve()
    directory = revisions_dir(root, dataset_version, code)
    if not directory.is_dir():
        return ()
    revisions: dict[str, Revision] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            raise WorksheetError("revision_chain_invalid")
        revision = _revision_from_path(root, path)
        revisions[revision.reference] = revision
    if not revisions:
        return ()
    cited = {
        revision.supersedes
        for revision in revisions.values()
        if revision.supersedes is not None
    }
    if not cited.issubset(revisions):
        raise WorksheetError("revision_chain_invalid")
    heads = [
        revision for revision in revisions.values() if revision.reference not in cited
    ]
    if len(heads) != 1:
        raise WorksheetError("revision_chain_invalid")
    chain: list[Revision] = []
    seen: set[str] = set()
    node: Revision | None = heads[0]
    while node is not None:
        if node.reference in seen:
            raise WorksheetError("revision_chain_invalid")
        seen.add(node.reference)
        chain.append(node)
        node = revisions.get(node.supersedes) if node.supersedes else None
    if len(chain) != len(revisions):
        raise WorksheetError("revision_chain_invalid")
    return tuple(reversed(chain))


def effective_revision(
    project_root: Path, dataset_version: str, code: str
) -> Revision | None:
    """The head revision a checklist binds, or ``None`` when unsigned."""
    chain = revision_chain(project_root, dataset_version, code)
    return chain[-1] if chain else None


def previous_pass_revision(
    project_root: Path, dataset_version: str, code: str
) -> Revision | None:
    """The nearest ``PASS`` ancestor of the head revision, or ``None``.

    ``FAIL`` is a historical clue, not a carry-forward baseline: reading it as
    "the operator signed this off" would turn a rejection into a confirmation.
    """
    chain = revision_chain(project_root, dataset_version, code)
    for revision in reversed(chain):
        if revision.decision == "PASS":
            return revision
    return None


def latest_pass_revision(
    project_root: Path,
    code: str,
    *,
    exclude_version: str | None = None,
) -> Revision | None:
    """The most recently confirmed ``PASS`` revision of one code, cross-version.

    Dataset versions are content hashes, so directory names carry no time
    order; ``confirmed_at`` (written by ``confirm``, not typed by hand) is the
    only cross-version ordering available.  Ties fail closed rather than
    guess.  A wrong pick only enlarges the review queue, never shrinks it.
    """
    root = Path(project_root).resolve()
    directory = worksheets_root(root)
    if not directory.is_dir():
        return None
    candidates: list[Revision] = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or child.name == exclude_version:
            continue
        revision = previous_pass_revision(root, child.name, code)
        if revision is not None:
            candidates.append(revision)
    if not candidates:
        return None
    newest = max(revision.confirmed_at for revision in candidates)
    newest_set = [
        revision for revision in candidates if revision.confirmed_at == newest
    ]
    if len(newest_set) != 1:
        raise WorksheetError("previous_signed_ambiguous")
    return newest_set[0]


def signed_codes(project_root: Path, dataset_version: str) -> tuple[str, ...]:
    """Every manual code this version already has an effective revision for."""
    return tuple(
        code
        for code in MANUAL_CHECK_CODES
        if effective_revision(project_root, dataset_version, code) is not None
    )
