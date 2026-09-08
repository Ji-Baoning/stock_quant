"""Strict real-data acceptance contracts and canonical record identity.

The acceptance registry persists :class:`AcceptanceRecord` payloads whose
content identity is a SHA-256 over a canonical JSON rendering: key-sorted,
compact separators, ``acceptance_id`` excluded and arrays placed in a stable
order (checks by ``code``, raw snapshot bindings by their four-component
location key).  The models here are deliberately strict -- unknown fields are
rejected, every hash field is a lowercase 64-hex string, and the check codes
must cover the ``real-data-v1`` policy vocabulary exactly once -- so a record
either binds the full evidence chain or does not validate at all.

This module only defines contracts and canonical serialisation; the offline
checkers, the content-addressed registry and the operator workflows live in
the sibling modules of this package.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

#: The only acceptance policy vocabulary this package publishes under.
POLICY_VERSION = "real-data-v1"
#: The acceptance-payload schema version guarded by ``Literal[1]`` fields.
SCHEMA_VERSION = 1
#: Placeholder requesting the latest valid ACCEPTED record for a dataset.
CURRENT_ACCEPTED = "CURRENT_ACCEPTED"

#: Automated checks rerun offline against the pinned dataset evidence.
AUTOMATED_CHECK_CODES = (
    "dataset_manifest_integrity",
    "quality_report_integrity",
    "required_table_coverage",
    "date_window_completeness",
    "security_master_evidence",
    "corporate_action_evidence",
    "raw_snapshot_traceability",
    "source_role_health",
)
#: Manual checks only an operator can complete, each with evidence.
MANUAL_CHECK_CODES = (
    "exchange_calendar_sample",
    "source_row_count_sample",
    "missing_reason_sample",
    "cross_source_price_sample",
    "corporate_action_sample",
    "benchmark_sample",
    "trading_rule_effective_dates",
    "security_master_sample",
    "secret_scan",
)

#: ``JsonValue`` (imported from pydantic) is the recursive JSON-safe union a
#: structured ``CheckResult.details`` mapping may hold; hand-rolled recursive
#: aliases recurse infinitely in pydantic's schema generation.


class CheckStatus(str, Enum):
    """Outcome of one acceptance check."""

    PASS = "PASS"
    FAIL = "FAIL"


class AcceptanceDecision(str, Enum):
    """The published verdict for one dataset version."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class EvidenceReference(BaseModel):
    """One sanitised pointer at manual-check evidence.

    ``reference`` is a project-relative path (``local``) or a public external
    source identifier (``external``); ``summary`` is a one-line human summary
    and ``sha256`` pins the referenced content.  No market payloads, secrets
    or absolute local paths are stored.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["local", "external"]
    reference: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(min_length=1)


class RawSnapshotBinding(BaseModel):
    """The five-field binding of one raw snapshot into dataset identity.

    The four leading fields locate the snapshot under
    ``data/raw/<source>/<endpoint>/<request_key>/<file_sha256>/`` and
    ``manifest_sha256`` pins its manifest, so a binding resolves to exactly
    one stored snapshot instead of any file with the same bytes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    request_key: str = Field(min_length=1)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckResult(BaseModel):
    """One check outcome: stable code, explicit status, redacted details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    status: CheckStatus
    summary: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    evidence: tuple[EvidenceReference, ...] = ()


def _require_exact_codes(
    checks: tuple[CheckResult, ...],
    expected_codes: tuple[str, ...],
    label: str,
) -> None:
    """Require ``checks`` to carry each of ``expected_codes`` exactly once."""
    found = [check.code for check in checks]
    missing = [code for code in expected_codes if code not in found]
    duplicated = sorted({code for code in found if found.count(code) > 1})
    unknown = sorted({code for code in found if code not in expected_codes})
    if not (missing or duplicated or unknown):
        return
    problems: list[str] = []
    if missing:
        problems.append(f"missing {missing}")
    if duplicated:
        problems.append(f"duplicated {duplicated}")
    if unknown:
        problems.append(f"unknown {unknown}")
    raise ValueError(
        f"{label} check codes must match the policy vocabulary exactly once: "
        + "; ".join(problems)
    )


class AcceptanceChecklist(BaseModel):
    """The operator-facing checklist an acceptance record is published from.

    The automated rows carry the fresh offline checker results while the
    manual rows start as explicit FAIL entries the operator must turn into
    PASS with evidence; both vocabularies must be covered exactly once.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    policy_version: Literal["real-data-v1"] = POLICY_VERSION
    dataset_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: Naive datetimes are rejected at validation time; canonical identity is
    #: unchanged because the JSON rendering is the same aware ISO instant.
    prepared_at: AwareDatetime
    operator_id: str = Field(min_length=1)
    automated_checks: tuple[CheckResult, ...]
    manual_checks: tuple[CheckResult, ...]
    raw_snapshot_evidence: tuple[RawSnapshotBinding, ...]

    @model_validator(mode="after")
    def validate_codes(self) -> "AcceptanceChecklist":
        """Enforce exact automated/manual check-code coverage."""
        _require_exact_codes(
            self.automated_checks, AUTOMATED_CHECK_CODES, "automated"
        )
        _require_exact_codes(self.manual_checks, MANUAL_CHECK_CODES, "manual")
        return self


class AcceptanceRecord(BaseModel):
    """The immutable, content-addressed verdict for one dataset version.

    ``acceptance_id`` is the SHA-256 of the canonical payload computed by
    :func:`compute_acceptance_id`; unlike the checklist, ``policy_version``
    stays a plain string so records published under older policies remain
    readable (they simply no longer satisfy the Research gate).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    policy_version: str = Field(default=POLICY_VERSION, min_length=1)
    acceptance_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: See ``AcceptanceChecklist.prepared_at``: aware instants only.
    created_at: AwareDatetime
    operator_id: str = Field(min_length=1)
    automated_checks: tuple[CheckResult, ...]
    manual_checks: tuple[CheckResult, ...]
    raw_snapshot_evidence: tuple[RawSnapshotBinding, ...]
    decision: AcceptanceDecision
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_decision(self) -> "AcceptanceRecord":
        """Keep the decision consistent with the recorded evidence."""
        all_pass = all(
            item.status is CheckStatus.PASS
            for item in (*self.automated_checks, *self.manual_checks)
        )
        if self.decision is AcceptanceDecision.ACCEPTED and (
            not all_pass or self.reasons
        ):
            raise ValueError("ACCEPTED requires all checks PASS and no reasons")
        if self.decision is AcceptanceDecision.REJECTED and not self.reasons:
            raise ValueError("REJECTED requires at least one reason")
        return self


def _canonical_payload(record: AcceptanceRecord) -> dict[str, JsonValue]:
    """The identity-bearing payload: no ``acceptance_id``, stable array order."""
    payload = record.model_dump(mode="json")
    payload.pop("acceptance_id", None)
    payload["automated_checks"] = sorted(
        payload["automated_checks"], key=lambda row: row["code"]
    )
    payload["manual_checks"] = sorted(
        payload["manual_checks"], key=lambda row: row["code"]
    )
    payload["raw_snapshot_evidence"] = sorted(
        payload["raw_snapshot_evidence"],
        key=lambda row: (
            row["source"], row["endpoint"], row["request_key"], row["file_sha256"]
        ),
    )
    return payload


def compute_acceptance_id(record: AcceptanceRecord) -> str:
    """The SHA-256 content identity of ``record`` (excluding its own id)."""
    encoded = json.dumps(
        _canonical_payload(record),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_record_json(record: AcceptanceRecord) -> str:
    """The published ``acceptance.json`` text, verified against identity."""
    if compute_acceptance_id(record) != record.acceptance_id:
        raise ValueError("acceptance_id does not match canonical payload")
    return json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
