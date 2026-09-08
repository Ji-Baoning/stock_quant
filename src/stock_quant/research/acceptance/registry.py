"""Content-addressed, immutable acceptance registry.

Published acceptance records live at
``<project_root>/data/acceptances/<dataset_version>/<acceptance_id>/
acceptance.json`` where ``acceptance_id`` is the SHA-256 content identity
computed by :func:`~stock_quant.research.acceptance.models.compute_acceptance_id`.
Publication stages the canonical payload in a temporary sibling directory
inside the dataset-version directory and moves it into place with
``os.replace`` so readers only ever observe fully written records.  Existing
records are never modified: republishing byte-identical content is an
idempotent success, while different content under the same id raises
:class:`AcceptanceIdentityConflict`.  Reads are strict -- a stored record
whose path binding or canonical hash disagrees with its location is an
integrity error, never silently skipped or reinterpreted -- and all root
paths are injected through ``project_root`` so tests run under ``tmp_path``.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from pydantic import ValidationError

from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    CURRENT_ACCEPTED,
    MANUAL_CHECK_CODES,
    POLICY_VERSION,
    AcceptanceDecision,
    AcceptanceRecord,
    CheckStatus,
    canonical_record_json,
    compute_acceptance_id,
)


class AcceptanceNotFound(LookupError):
    """No acceptance record exists under the requested registry path."""


class AcceptanceIntegrityError(ValueError):
    """A stored record disagrees with its path or canonical content hash."""


class AcceptanceIdentityConflict(ValueError):
    """The acceptance id already exists on disk with different content."""


class NoValidAcceptance(LookupError):
    """No stored record satisfies the requested acceptance policy."""


class AcceptanceRegistry:
    """The append-only store of real-data acceptance records."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / "data" / "acceptances"

    def path_for(self, record: AcceptanceRecord) -> Path:
        """The canonical on-disk location of ``record``."""
        return (
            self.root
            / record.dataset_version
            / record.acceptance_id
            / "acceptance.json"
        )

    def publish(self, record: AcceptanceRecord) -> AcceptanceRecord:
        """Atomically publish ``record``; identical republish is a no-op."""
        payload = canonical_record_json(record).encode("utf-8")
        destination = self.path_for(record)
        if destination.is_file():
            if destination.read_bytes() != payload:
                raise AcceptanceIdentityConflict(record.acceptance_id)
            return record
        destination.parent.parent.mkdir(parents=True, exist_ok=True)
        temporary = (
            destination.parent.parent
            / f".{record.acceptance_id}.{uuid.uuid4().hex}.tmp"
        )
        temporary.mkdir()
        try:
            (temporary / "acceptance.json").write_bytes(payload)
            os.replace(temporary, destination.parent)
        except OSError:
            # A concurrent publisher of the same record may have won the
            # rename onto the same destination; an already-existing
            # byte-identical destination is an idempotent success.
            identical = (
                destination.is_file() and destination.read_bytes() == payload
            )
            shutil.rmtree(temporary, ignore_errors=True)
            if identical:
                return record
            raise
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return record

    def get(self, dataset_version: str, acceptance_id: str) -> AcceptanceRecord:
        """Load one record, verifying its path binding and content hash."""
        path = self.root / dataset_version / acceptance_id / "acceptance.json"
        if not path.is_file():
            raise AcceptanceNotFound(acceptance_id)
        try:
            record = AcceptanceRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except ValidationError as error:
            raise AcceptanceIntegrityError(
                "acceptance.json is not a valid record"
            ) from error
        if (
            record.dataset_version != dataset_version
            or record.acceptance_id != acceptance_id
        ):
            raise AcceptanceIntegrityError("acceptance path disagrees with payload")
        if compute_acceptance_id(record) != acceptance_id:
            raise AcceptanceIntegrityError("acceptance payload hash mismatch")
        if record.decision is AcceptanceDecision.ACCEPTED and (
            not _has_exact_policy_checks(record)
        ):
            raise AcceptanceIntegrityError(
                "ACCEPTED record lacks exact policy check coverage"
            )
        return record

    def list(self, dataset_version: str) -> tuple[AcceptanceRecord, ...]:
        """Every stored record for a version, oldest first; read-only."""
        directory = self.root / dataset_version
        if not directory.is_dir():
            return ()
        records = [
            self.get(dataset_version, child.name)
            for child in directory.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        ]
        return tuple(
            sorted(records, key=lambda row: (row.created_at, row.acceptance_id))
        )

    def select(
        self,
        dataset_version: str,
        requested_id: str,
        policy_version: str = POLICY_VERSION,
    ) -> AcceptanceRecord:
        """Resolve the acceptance record a Research run must consume.

        ``CURRENT_ACCEPTED`` picks the newest record that is ACCEPTED under
        ``policy_version`` with every automated and manual check PASS; an
        explicit id only ever considers that one record.
        """
        records = self.list(dataset_version)
        if requested_id != CURRENT_ACCEPTED:
            records = tuple(
                row for row in records if row.acceptance_id == requested_id
            )
        valid = tuple(
            row
            for row in records
            if row.decision is AcceptanceDecision.ACCEPTED
            and row.policy_version == policy_version
            and all(
                check.status is CheckStatus.PASS
                for check in row.automated_checks
            )
            and all(
                check.status is CheckStatus.PASS
                for check in row.manual_checks
            )
        )
        if not valid:
            raise NoValidAcceptance(dataset_version)
        return valid[-1]


def _has_exact_policy_checks(record: AcceptanceRecord) -> bool:
    """True when the record covers each policy check code exactly once."""
    automated = sorted(check.code for check in record.automated_checks)
    manual = sorted(check.code for check in record.manual_checks)
    return (
        automated == sorted(AUTOMATED_CHECK_CODES)
        and manual == sorted(MANUAL_CHECK_CODES)
    )
