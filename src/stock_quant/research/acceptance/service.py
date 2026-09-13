"""Operator workflows over the immutable acceptance registry.

``build_checklist`` is the pure kernel: it recomputes every automated verdict
for one pinned dataset version and returns the operator-facing checklist whose
manual rows all start as explicit PENDING_CONFIRMATION entries, without
touching disk.  ``prepare_checklist`` layers the deterministic evidence pack on
top -- generating ``data/acceptance-evidence/<version>/``, pointing the six
mechanisable manual rows at their artifacts and writing the checklist YAML.
``publish_checklist`` re-runs the *pure* recompute against the live dataset,
verifies every manual evidence reference, and only then records an ACCEPTED or
REJECTED decision; a rejection is persisted *before*
:class:`AcceptanceRejected` is raised so the registry always shows why an
operator attempt failed.  The read-only paths deliberately never generate
evidence, so a tampered pack cannot be silently repaired.  Nothing here
mutates datasets, raw snapshots or ``CURRENT``, and every published artifact
keeps hashes, summaries, relative paths and public identifiers only.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import uuid4

import yaml

from stock_quant.research.acceptance.checks import (
    AcceptanceCheckInput,
    dataset_evidence,
    run_automated_checks,
)
from stock_quant.research.acceptance.evidence import (
    EvidenceBuildError,
    build_mechanisable_evidence,
)
from stock_quant.research.acceptance.models import (
    MANUAL_CHECK_CODES,
    MECHANISABLE_CODES,
    POLICY_VERSION,
    AcceptanceChecklist,
    AcceptanceDecision,
    AcceptanceRecord,
    CheckStatus,
    EvidenceReference,
    ManualCheckResult,
    ManualCheckStatus,
    compute_acceptance_id,
)
from stock_quant.research.acceptance.registry import AcceptanceRegistry


class AcceptanceRejected(Exception):
    """A checklist was rejected; the REJECTED record is already persisted.

    Raised only *after* :meth:`AcceptanceRegistry.publish` recorded the
    decision, so the registry history always explains a failed operator
    attempt.  ``record`` carries the persisted record with its reasons.
    """

    def __init__(self, record: AcceptanceRecord) -> None:
        self.record = record
        super().__init__("; ".join(record.reasons))


class AcceptanceBindingError(ValueError):
    """An acceptance record no longer binds its dataset evidence.

    ``reasons`` is the sorted deduplicated set of binding failures found by
    :func:`verify_acceptance_bindings`.
    """

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


#: The stable placeholder replacing a local reference the project cannot
#: contain, so a persisted record never carries an operator-supplied path.
_UNPUBLISHABLE_REFERENCE = "unverifiable_local_reference"


def build_checklist(
    project_root: Path,
    dataset_version: str,
    operator_id: str,
    *,
    prepared_at: datetime | None = None,
) -> AcceptanceChecklist:
    """Recompute the deterministic operator checklist for one dataset version.

    Pure: it reads the pinned dataset and returns the checklist, writing
    nothing.  The automated rows carry the fresh offline checker results in
    policy order while every manual row starts as a PENDING_CONFIRMATION
    placeholder an operator must turn into PASS with evidence; ``prepared_at``
    defaults to the current UTC time so identical inputs differ only by that
    clock field.
    """
    value = AcceptanceCheckInput(Path(project_root), dataset_version)
    evidence = dataset_evidence(value)
    automated = run_automated_checks(value)
    manual = tuple(
        ManualCheckResult(
            code=code,
            status=ManualCheckStatus.PENDING_CONFIRMATION,
            summary=(
                "operator review required"
                if code in MECHANISABLE_CODES
                else "external corroboration required"
            ),
        )
        for code in MANUAL_CHECK_CODES
    )
    return AcceptanceChecklist(
        dataset_version=dataset_version,
        dataset_manifest_sha256=evidence.dataset_manifest_sha256,
        quality_report_sha256=evidence.quality_report_sha256,
        prepared_at=prepared_at or datetime.now(timezone.utc),
        operator_id=operator_id.strip(),
        automated_checks=automated,
        manual_checks=manual,
        raw_snapshot_evidence=evidence.raw_snapshot_evidence,
    )


def prepare_checklist(
    project_root: Path,
    dataset_version: str,
    operator_id: str,
    output_path: Path,
    *,
    prepared_at: datetime | None = None,
) -> AcceptanceChecklist:
    """Build the checklist, generate the evidence pack, and write both.

    The pack is replaced whole before the checklist is written, and a failed
    build never yields a row with fake evidence: those rows stay
    ``PENDING_CONFIRMATION`` with empty evidence and a stable failure category
    in their summary, so publishing them rejects instead of accepting.
    """
    root = Path(project_root).resolve()
    checklist = build_checklist(
        root, dataset_version, operator_id, prepared_at=prepared_at
    )
    failed: str | None = None
    try:
        references = build_mechanisable_evidence(root, dataset_version)
    except EvidenceBuildError as error:
        references = {}
        failed = error.category
    checklist = checklist.model_copy(
        update={
            "manual_checks": _attach_evidence(
                checklist.manual_checks, references, failed=failed
            )
        }
    )
    _write_checklist(checklist, Path(output_path))
    return checklist


def _attach_evidence(
    rows: tuple[ManualCheckResult, ...],
    references: Mapping[str, EvidenceReference],
    *,
    failed: str | None,
) -> tuple[ManualCheckResult, ...]:
    """Point each mechanisable row at its artifact, or name the failure."""
    attached: list[ManualCheckResult] = []
    for row in rows:
        reference = references.get(row.code)
        if reference is not None:
            attached.append(row.model_copy(update={"evidence": (reference,)}))
        elif failed is not None and row.code in MECHANISABLE_CODES:
            attached.append(
                row.model_copy(
                    update={
                        "summary": f"evidence generation failed: {failed}"
                    }
                )
            )
        else:
            attached.append(row)
    return tuple(attached)


def _write_checklist(
    checklist: AcceptanceChecklist, output_path: Path
) -> None:
    """Write the checklist YAML atomically (temp file, then replace)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    staging.write_text(
        yaml.safe_dump(
            checklist.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    os.replace(staging, output_path)


def publish_checklist(
    project_root: Path,
    checklist_path: Path,
    created_at: datetime | None = None,
) -> AcceptanceRecord:
    """Recompute, verify and publish one operator checklist.

    The checklist is re-prepared against the live dataset: any drift in the
    bound evidence, tampering with the automated rows, a failing automated
    check, an unmet manual row or unverifiable evidence turns the decision
    into REJECTED.  Local evidence references the project cannot contain are
    stored as a stable placeholder, so the persisted record never carries an
    operator-supplied path.  The record -- accepted or rejected -- is
    persisted atomically before anything is raised, so a rejected attempt
    stays in the registry history; :class:`AcceptanceRejected` then carries
    it to the caller.
    """
    root = Path(project_root).resolve()
    checklist = AcceptanceChecklist.model_validate(
        yaml.safe_load(Path(checklist_path).read_text(encoding="utf-8"))
    )
    fresh = build_checklist(
        root,
        checklist.dataset_version,
        checklist.operator_id,
        prepared_at=checklist.prepared_at,
    )
    reasons = _binding_reasons(checklist, fresh)
    reasons.extend(_manual_check_reasons(root, checklist.manual_checks))
    decision = (
        AcceptanceDecision.REJECTED if reasons else AcceptanceDecision.ACCEPTED
    )
    provisional = AcceptanceRecord(
        acceptance_id="0" * 64,
        dataset_version=checklist.dataset_version,
        dataset_manifest_sha256=fresh.dataset_manifest_sha256,
        quality_report_sha256=fresh.quality_report_sha256,
        created_at=created_at or datetime.now(timezone.utc),
        operator_id=checklist.operator_id,
        automated_checks=fresh.automated_checks,
        manual_checks=_sanitized_manual_checks(root, checklist.manual_checks),
        raw_snapshot_evidence=fresh.raw_snapshot_evidence,
        decision=decision,
        reasons=tuple(sorted(set(reasons))),
    )
    record = provisional.model_copy(
        update={"acceptance_id": compute_acceptance_id(provisional)}
    )
    AcceptanceRegistry(root).publish(record)
    if decision is AcceptanceDecision.REJECTED:
        raise AcceptanceRejected(record)
    return record


def verify_acceptance_bindings(
    project_root: Path,
    record: AcceptanceRecord,
) -> None:
    """Re-verify a stored record against the live dataset evidence.

    Rebuilds the fresh checklist the record must still bind to (manifest and
    quality hashes, raw snapshot bindings, automated verdicts), re-verifies
    every manual evidence reference and requires the current policy version;
    any drift raises :class:`AcceptanceBindingError` with the sorted reason
    set.  Read-only: it calls the pure :func:`build_checklist` and generates no
    evidence, so a tampered pack fails the hash check instead of being
    rewritten into a pass.
    """
    root = Path(project_root).resolve()
    fresh = build_checklist(
        root,
        record.dataset_version,
        record.operator_id,
        prepared_at=record.created_at,
    )
    requested = AcceptanceChecklist(
        dataset_version=record.dataset_version,
        dataset_manifest_sha256=record.dataset_manifest_sha256,
        quality_report_sha256=record.quality_report_sha256,
        prepared_at=record.created_at,
        operator_id=record.operator_id,
        automated_checks=record.automated_checks,
        manual_checks=record.manual_checks,
        raw_snapshot_evidence=record.raw_snapshot_evidence,
    )
    reasons = _binding_reasons(requested, fresh)
    reasons.extend(_manual_check_reasons(root, record.manual_checks))
    if record.policy_version != POLICY_VERSION:
        reasons.append("policy_version_expired")
    if reasons:
        raise AcceptanceBindingError(tuple(sorted(set(reasons))))


def acceptance_audit_dict(record: AcceptanceRecord) -> dict[str, object]:
    """The sanitized audit mapping persisted with formal research runs."""
    return {
        "acceptance_id": record.acceptance_id,
        "policy_version": record.policy_version,
        "operator_id": record.operator_id,
        "created_at": record.created_at.isoformat(),
        "decision": record.decision.value,
    }


def show_acceptances(
    project_root: Path,
    dataset_version: str,
) -> tuple[AcceptanceRecord, ...]:
    """Every stored record for a version, oldest first (read-only)."""
    return AcceptanceRegistry(project_root).list(dataset_version)


def _binding_reasons(
    requested: AcceptanceChecklist,
    fresh: AcceptanceChecklist,
) -> list[str]:
    """Every field the requested checklist fails to bind to fresh evidence."""
    reasons = []
    for field in (
        "schema_version",
        "policy_version",
        "dataset_version",
        "dataset_manifest_sha256",
        "quality_report_sha256",
        "raw_snapshot_evidence",
    ):
        if getattr(requested, field) != getattr(fresh, field):
            reasons.append(f"{field}_changed")
    if requested.automated_checks != fresh.automated_checks:
        reasons.append("automated_checks_changed")
    for check in fresh.automated_checks:
        if check.status is CheckStatus.FAIL:
            reasons.append(f"automated_{check.code}_failed")
    return reasons


def _manual_check_reasons(
    project_root: Path,
    checks: tuple[ManualCheckResult, ...],
) -> list[str]:
    """Every manual row that is not a verified PASS with good evidence."""
    reasons = []
    for check in checks:
        if check.status is ManualCheckStatus.PENDING_CONFIRMATION:
            reasons.append(f"manual_{check.code}_pending_confirmation")
        elif check.status is not ManualCheckStatus.PASS:
            reasons.append(f"manual_{check.code}_failed")
        if not check.evidence:
            reasons.append(f"manual_{check.code}_evidence_missing")
        for evidence in check.evidence:
            reason = _verify_evidence_reference(project_root, evidence)
            if reason is not None:
                reasons.append(f"manual_{check.code}_{reason}")
    return reasons


def _sanitized_manual_checks(
    project_root: Path,
    checks: tuple[ManualCheckResult, ...],
) -> tuple[ManualCheckResult, ...]:
    """The manual rows as they may be persisted.

    A local reference is kept verbatim only when it is a relative path the
    project root can contain; absolute paths, traversal and unresolvable text
    are replaced with ``_UNPUBLISHABLE_REFERENCE`` so a stored record never
    carries operator-supplied paths.  Reason codes are unaffected -- they
    already explain why the row did not verify -- and fully verified
    checklists (the ACCEPTED path) are returned unchanged.
    """
    sanitized: list[ManualCheckResult] = []
    for check in checks:
        evidence = tuple(
            row
            if _reference_is_publishable(project_root, row)
            else row.model_copy(update={"reference": _UNPUBLISHABLE_REFERENCE})
            for row in check.evidence
        )
        sanitized.append(check.model_copy(update={"evidence": evidence}))
    return tuple(sanitized)


def _reference_is_publishable(
    project_root: Path,
    evidence: EvidenceReference,
) -> bool:
    """True when a reference is fit for persistence as submitted."""
    if evidence.kind != "local":
        return True
    if Path(evidence.reference).is_absolute():
        return False
    candidate, _ = _resolve_local_reference(project_root, evidence.reference)
    return candidate is not None


def _resolve_local_reference(
    project_root: Path,
    reference: str,
) -> tuple[Path | None, str | None]:
    """Resolve one local reference under the project root.

    Returns ``(candidate, None)`` when the path resolves inside the root,
    otherwise ``(None, reason)`` where the reason is
    ``evidence_path_invalid`` (unresolvable, e.g. a NUL byte) or
    ``evidence_path_outside_project`` (traversal, symlink escape).
    """
    try:
        candidate = (project_root / reference).resolve()
    except ValueError:
        return None, "evidence_path_invalid"
    if not candidate.is_relative_to(project_root):
        return None, "evidence_path_outside_project"
    return candidate, None


def _verify_evidence_reference(
    project_root: Path,
    evidence: EvidenceReference,
) -> str | None:
    """Verify one evidence reference, or name the reason it fails.

    ``local`` references must resolve (traversal and symlinks included)
    inside the project root and match their pinned SHA-256.  ``external``
    references are never fetched: their hash pins the stored UTF-8 summary.
    """
    if evidence.kind == "external":
        actual = hashlib.sha256(evidence.summary.encode("utf-8")).hexdigest()
        return None if actual == evidence.sha256 else "evidence_hash_changed"
    candidate, path_reason = _resolve_local_reference(
        project_root, evidence.reference
    )
    if candidate is None:
        return path_reason
    if not candidate.is_file():
        return "evidence_missing"
    return (
        None
        if _sha256_file(candidate) == evidence.sha256
        else "evidence_hash_changed"
    )


def _sha256_file(path: Path) -> str:
    """The lowercase hex SHA-256 of one file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
