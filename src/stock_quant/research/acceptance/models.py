"""Strict data-acceptance contracts, canonical record identity and gates.

This module carries the two strict contract layers the acceptance package
publishes:

- The **run gate** contracts: :class:`AcceptanceResult` -- one named,
  deterministic verdict about the pinned dataset evidence that formal
  Research consumes before factor work -- plus the mandatory-result
  vocabulary :data:`REQUIRED_ACCEPTANCE_RESULTS` and
  :func:`enforce_required_results`, the loud gate failure primitive the
  runner's ``universe_acceptance`` preflight stage calls.
- The **registry** contracts: :class:`AcceptanceRecord` payloads whose
  content identity is a SHA-256 over a canonical JSON rendering: key-sorted,
  compact separators, ``acceptance_id`` excluded and arrays placed in a
  stable order (checks by ``code``, raw snapshot bindings by their
  five-component location key).  The models here are deliberately strict --
  unknown fields are rejected, every hash field is a lowercase 64-hex string,
  and the check codes must cover the ``real-data-v1`` policy vocabulary
  exactly once -- so a record either binds the full evidence chain or does
  not validate at all.

Determinism is enforced structurally: an :class:`AcceptanceResult`'s
``details`` may only carry the four fixed keys (``coverage``, ``counts``,
``hashes``, ``error_codes``), every value is JSON-safe, and ``error_codes``
must be sorted and unique.  Verdicts therefore never embed verbose rows,
exception text or timestamps, and identical evidence always renders
identical bytes.

This module only defines contracts and canonical serialisation; the offline
checkers, the content-addressed registry and the operator workflows live in
the sibling modules of this package.  Nothing here mutates data or touches
the network (design spec: 纯读取验收).
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal, Sequence

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

#: The only acceptance policy vocabulary this package publishes under.
POLICY_VERSION = "real-data-v1"
#: The acceptance-payload schema version pinned by ``Literal[1]`` fields.
SCHEMA_VERSION = 1
#: Alias of :data:`SCHEMA_VERSION` used by the run-gate contracts.
ACCEPTANCE_SCHEMA_VERSION = SCHEMA_VERSION
#: Placeholder requesting the latest valid ACCEPTED record for a dataset.
CURRENT_ACCEPTED = "CURRENT_ACCEPTED"

#: Acceptance results a formal Research run requires present and ``PASS``.
REQUIRED_ACCEPTANCE_RESULTS = ("index_membership_evidence",)

#: The only detail keys a deterministic acceptance result may carry: the
#: covered window, the member/fact counts, the pinned hashes and the sorted
#: fatal error codes. Anything else (rows, symbols lists, messages) is not a
#: deterministic detail and must stay out.
ALLOWED_DETAIL_KEYS = (
    "coverage",
    "counts",
    "hashes",
    "error_codes",
)

#: Automated checks rerun offline against the pinned dataset evidence.
AUTOMATED_CHECK_CODES = (
    "dataset_manifest_integrity",
    "quality_report_integrity",
    "required_table_coverage",
    "date_window_completeness",
    "security_master_evidence",
    "corporate_action_evidence",
    "raw_snapshot_traceability",
    "calendar_coverage_evidence",
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


class AcceptanceStatus(str, Enum):
    """Outcome of one acceptance result."""

    PASS = "PASS"
    FAIL = "FAIL"


class CheckStatus(str, Enum):
    """Outcome of one acceptance check."""

    PASS = "PASS"
    FAIL = "FAIL"


class AcceptanceDecision(str, Enum):
    """The published verdict for one dataset version."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class AcceptanceResult(BaseModel):
    """One named, deterministic acceptance verdict over pinned evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = ACCEPTANCE_SCHEMA_VERSION
    code: str = Field(min_length=1)
    status: AcceptanceStatus
    summary: str = Field(min_length=1)
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def _known_code(cls, value: str) -> str:
        if value not in REQUIRED_ACCEPTANCE_RESULTS:
            raise ValueError(
                f"acceptance result code {value!r} is not in the required "
                f"vocabulary {REQUIRED_ACCEPTANCE_RESULTS}"
            )
        return value

    @model_validator(mode="after")
    def _deterministic_details(self) -> "AcceptanceResult":
        unknown = sorted(set(self.details) - set(ALLOWED_DETAIL_KEYS))
        if unknown:
            raise ValueError(
                "acceptance details carry non-deterministic keys "
                f"{unknown}; allowed: {list(ALLOWED_DETAIL_KEYS)}"
            )
        error_codes = self.details.get("error_codes")
        if error_codes is not None:
            if not isinstance(error_codes, list) or not all(
                isinstance(code, str) for code in error_codes
            ):
                raise ValueError("error_codes must be a list of strings")
            if error_codes != sorted(set(error_codes)):
                raise ValueError(
                    "error_codes must be sorted and unique: "
                    f"{error_codes}"
                )
        return self


class AcceptanceGateError(ValueError):
    """A mandatory acceptance result is missing or did not pass."""


def enforce_required_results(
    results: Sequence[AcceptanceResult],
) -> None:
    """Raise :class:`AcceptanceGateError` unless every mandatory result passes.

    The error message names only stable codes -- never evidence payloads --
    so a blocked run is diagnosable without leaking verbose data.
    """
    by_code = {result.code: result for result in results}
    missing = [
        code for code in REQUIRED_ACCEPTANCE_RESULTS if code not in by_code
    ]
    failed = sorted(
        result.code
        for result in results
        if result.status is AcceptanceStatus.FAIL
    )
    if not missing and not failed:
        return
    problems: list[str] = []
    if missing:
        problems.append(f"missing required results {missing}")
    if failed:
        problems.append(f"failing results {failed}")
    raise AcceptanceGateError(
        "universe acceptance gate failed: " + "; ".join(problems)
    )


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
    """The binding of one raw snapshot into dataset identity.

    The location fields resolve the snapshot under
    ``data/raw/<source>/<endpoint>/<transport_id>/<request_key>/<file_sha256>/``
    and ``manifest_sha256`` pins its manifest, so a binding resolves to exactly
    one stored snapshot instead of any file with the same bytes.

    ``transport_id`` is optional on purpose: bindings written before transport
    tracking existed carry five fields and resolve on the older four-segment
    ``<source>/<endpoint>/<request_key>/<file_sha256>`` layout.  A row with no
    transport contributes no ``transport_id`` key to the canonical identity
    payload (see :func:`_canonical_payload`), so a five-field binding's
    pre-change ``acceptance_id`` still verifies; a row with a real transport
    keeps the key and the value stays identity-bearing.  Both shapes are
    readable under ``DATASET_BUILD_CONTRACT_VERSION = 1``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    request_key: str = Field(min_length=1)
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport_id: str | None = None


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
    """The identity-bearing payload: no ``acceptance_id``, stable array order.

    A ``raw_snapshot_evidence`` row whose ``transport_id`` is ``None``
    predates transport tracking, so its ``transport_id`` key is dropped from
    the payload.  ``None`` is the correct signal for "predates the field": the
    store writes the reserved id for snapshots that existed before transport
    tracking, so a ``None`` here never means a real transport we failed to
    name.  Dropping the key keeps every legacy ``acceptance_id`` bit-identical
    to the value computed before ``transport_id`` existed, while a row with a
    real ``transport_id`` keeps the key and stays identity-bearing.  The drop
    is deliberately scoped to this one field instead of a blanket
    ``exclude_none=True``: other fields legitimately take ``None`` and must
    keep affecting identity.
    """
    payload = record.model_dump(mode="json")
    payload.pop("acceptance_id", None)
    payload["automated_checks"] = sorted(
        payload["automated_checks"], key=lambda row: row["code"]
    )
    payload["manual_checks"] = sorted(
        payload["manual_checks"], key=lambda row: row["code"]
    )
    evidence = [
        {key: value for key, value in row.items() if key != "transport_id"}
        if row.get("transport_id") is None
        else row
        for row in payload["raw_snapshot_evidence"]
    ]
    payload["raw_snapshot_evidence"] = sorted(
        evidence,
        # The plan pins ``transport_id`` third (2026-09-12 plan, Task 5).  Do
        # not "unify" this back to a trailing position: a legacy row drops its
        # ``transport_id`` key (the ``None`` branch above), so this component
        # is the constant ``""`` for every such row and a constant cannot
        # reorder a sort -- legacy ``acceptance_id`` values stay bit-identical
        # under either key order.  Only transport-bearing rows can differ, and
        # none have been published, so aligning with the plan now is free.
        key=lambda row: (
            row["source"],
            row["endpoint"],
            row.get("transport_id") or "",
            row["request_key"],
            row["file_sha256"],
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
