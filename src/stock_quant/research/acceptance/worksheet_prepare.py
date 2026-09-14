"""The prepare side of the standing worksheet line.

``prepare`` writes one unsigned worksheet per manual code, carrying forward the
human area of the last confirmed ``PASS`` revision of the same code from
another dataset version -- content carry-forward, never file reuse.  A version
that already has a signed revision is refused *before anything is written*
unless ``--force`` is given, in which case the signed rows are restored into
the rebuilt checklist and the revisions are left byte-identical.

The value builders in this module (``candidate_blob``, ``candidate_evidence``,
``previous_candidate_rows``, ``previous_signed_payload``, ``revision_reference``,
``window_of``) are public because ``worksheet.confirm`` builds the *same*
program area from the *same* facts: two renderings of one worksheet must not be
able to disagree, and duplicating them would guarantee that they eventually do.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from stock_quant.research.acceptance.evidence import (
    evidence_window,
    load_manifest,
    read_pack_references,
)
from stock_quant.research.acceptance.external_inputs import (
    load_blob,
    store_blob,
    version_facts,
)
from stock_quant.research.acceptance.models import (
    MANUAL_CHECK_CODES,
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
    AcceptanceChecklist,
    EvidenceReference,
    ManualCheckResult,
    ManualCheckStatus,
)
from stock_quant.research.acceptance.worksheet import (
    SIGNED_SUMMARY,
    Revision,
    WorksheetError,
    effective_revision,
    latest_pass_revision,
    pending_path,
    render_program,
    render_worksheet,
    signed_codes,
)
from stock_quant.research.acceptance.worksheet_program import (
    build_program,
    candidate_bytes,
    candidate_name,
    candidate_rows,
    review_queue,
)

__all__ = [
    "candidate_blob",
    "candidate_evidence",
    "precheck_signed",
    "prepare_worksheets",
    "previous_candidate_rows",
    "previous_signed_payload",
    "restore_signed_rows",
    "revision_reference",
    "window_of",
]


def precheck_signed(project_root: Path, dataset_version: str, *, force: bool) -> None:
    """Refuse a re-prepare over a signed version before any write happens.

    Rebuilding the evidence pack is itself a write, so this cannot be checked
    after it: ``prepare`` would already have changed the tree it must protect.
    """
    if not force and signed_codes(Path(project_root).resolve(), dataset_version):
        raise WorksheetError("signed_worksheets_present")


def window_of(project_root: Path, dataset_version: str) -> tuple[date, date]:
    """The window every worksheet of one version is computed over."""
    return evidence_window(load_manifest(Path(project_root).resolve(), dataset_version))


def candidate_blob(
    project_root: Path, code: str, rows: tuple[str, ...]
) -> dict[str, str]:
    """Store one code's candidate snapshot and return its evidence pointer."""
    stored = store_blob(
        Path(project_root).resolve(),
        candidate_bytes(code, rows),
        candidate_name(code),
    )
    return {"reference": stored.reference, "sha256": stored.sha256}


def candidate_evidence(
    project_root: Path,
    code: str,
    rows_by_code: dict[str, tuple[str, ...]],
    pack_references: dict[str, EvidenceReference],
) -> list[dict[str, str]]:
    """What one confirmation is judged against.

    The six mechanisable codes point at the evidence pack ``prepare`` wrote;
    the three operator-only codes point at a content-addressed snapshot of the
    version-side rows they enumerate.  Both are read back from disk rather
    than lifted off the checklist row, so a supersede cites the same pack
    files as the first signing instead of citing the revision it replaces.
    When the evidence build failed the list is honestly empty rather than
    pointing at something that was never written.
    """
    if code in MECHANISABLE_CODES:
        reference = pack_references.get(code)
        return (
            []
            if reference is None
            else [{"reference": reference.reference, "sha256": reference.sha256}]
        )
    return [candidate_blob(project_root, code, rows_by_code[code])]


def previous_candidate_rows(
    project_root: Path, revision: Revision
) -> tuple[str, ...] | None:
    """The candidate rows a previous revision queued against, if still readable.

    ``None`` means "no usable baseline", which only ever *enlarges* the next
    queue: a missing or unreadable snapshot falls back to the full candidate
    set instead of silently claiming those rows were already reviewed.
    """
    recorded = revision.payload.get("candidate_evidence")
    if not isinstance(recorded, list) or not recorded:
        return None
    first = recorded[0]
    if not isinstance(first, dict) or "reference" not in first:
        return None
    try:
        payload = json.loads(load_blob(project_root, str(first["reference"])))
    except (WorksheetError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    return tuple(str(row) for row in rows)


def previous_signed_payload(revision: Revision | None) -> dict[str, object] | None:
    """The ``previous_signed`` program header for one revision."""
    if revision is None:
        return None
    return {
        "dataset_version": revision.payload.get("dataset_version"),
        "confirmed_at": revision.confirmed_at,
        "operator_id": revision.operator_id,
        "reference": revision.reference,
        "sha256": revision.sha256,
    }


def revision_reference(revision: Revision) -> EvidenceReference:
    """The evidence pointer a checklist row uses for one revision."""
    return EvidenceReference(
        kind="local",
        reference=revision.reference,
        sha256=revision.sha256,
        summary=f"{revision.payload.get('code')} worksheet revision",
    )


def prepare_worksheets(
    project_root: Path,
    checklist: AcceptanceChecklist,
    *,
    force: bool = False,
) -> AcceptanceChecklist:
    """Write or refresh this version's worksheets and return the checklist."""
    root = Path(project_root).resolve()
    version = checklist.dataset_version
    precheck_signed(root, version, force=force)
    if force:
        checklist = checklist.model_copy(
            update={"manual_checks": restore_signed_rows(root, checklist)}
        )
    start, end = window_of(root, version)
    facts = version_facts(root, version, start, end)
    rows_by_code = candidate_rows(facts)
    pack_references = read_pack_references(root, version)
    window = {"start": start.isoformat(), "end": end.isoformat()}
    for code in MANUAL_CHECK_CODES:
        path = pending_path(root, version, code)
        if effective_revision(root, version, code) is not None:
            # A signed code has no unsigned worksheet: a leftover one would
            # invite a re-signing against a state nothing else agrees with.
            path.unlink(missing_ok=True)
            continue
        previous = latest_pass_revision(root, code, exclude_version=version)
        program = build_program(
            code=code,
            dataset_version=version,
            dataset_manifest_sha256=checklist.dataset_manifest_sha256,
            window=window,
            generated_at=checklist.prepared_at.isoformat(),
            candidate=candidate_evidence(root, code, rows_by_code, pack_references),
            previous_signed=previous_signed_payload(previous),
            supersedes=None,
            queue=(
                review_queue(
                    code,
                    rows_by_code[code],
                    previous_rows=(
                        previous_candidate_rows(root, previous)
                        if previous is not None
                        else None
                    ),
                )
                if code in OPERATOR_ONLY_CODES
                else ()
            ),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            render_worksheet(
                render_program(program),
                previous.human if previous is not None else "",
            ),
            encoding="utf-8",
        )
    return checklist


def restore_signed_rows(
    project_root: Path, checklist: AcceptanceChecklist
) -> tuple[ManualCheckResult, ...]:
    """Rebuild signed rows from their effective revisions, verifying binding.

    Every revision must still hash to its own name (``revision_chain``
    enforces that), bind this version's manifest and the current window, and
    keep every cited file verifiable.  Any drift fails closed here, before a
    single worksheet is written.
    """
    root = Path(project_root).resolve()
    version = checklist.dataset_version
    start, end = window_of(root, version)
    window = {"start": start.isoformat(), "end": end.isoformat()}
    restored: list[ManualCheckResult] = []
    for row in checklist.manual_checks:
        try:
            revision = effective_revision(root, version, row.code)
        except WorksheetError as error:
            # A revision that no longer parses as itself is drift like any
            # other: the restore path repairs nothing, it refuses.
            raise WorksheetError("signed_worksheet_drift") from error
        restored.append(
            row
            if revision is None
            else _restored_row(root, row, revision, checklist, window)
        )
    return tuple(restored)


def _restored_row(
    root: Path,
    row: ManualCheckResult,
    revision: Revision,
    checklist: AcceptanceChecklist,
    window: dict[str, str],
) -> ManualCheckResult:
    """One signed row, restored from its revision without rewriting it."""
    if revision.payload.get("dataset_manifest_sha256") != (
        checklist.dataset_manifest_sha256
    ):
        raise WorksheetError("signed_worksheet_drift")
    if revision.payload.get("window") != window:
        raise WorksheetError("signed_worksheet_drift")
    evidence = [revision_reference(revision)]
    external = revision.payload.get("external_input")
    if isinstance(external, dict) and "reference" in external:
        evidence.append(
            EvidenceReference(
                kind="local",
                reference=str(external["reference"]),
                sha256=str(external["sha256"]),
                summary=f"{revision.payload.get('code')} external input",
            )
        )
    for reference in evidence:
        # ``service`` imports this module, so the shared verifier is imported
        # lazily: a module-level import would be a cycle.
        from stock_quant.research.acceptance.service import (
            verify_evidence_reference,
        )

        if verify_evidence_reference(root, reference) is not None:
            raise WorksheetError("signed_worksheet_drift")
    return row.model_copy(
        update={
            "status": ManualCheckStatus(revision.decision),
            "summary": SIGNED_SUMMARY[revision.decision],
            "evidence": tuple(evidence),
        }
    )
