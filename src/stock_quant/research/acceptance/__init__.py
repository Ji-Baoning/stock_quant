"""Strict real-data acceptance contracts and immutable registry."""

from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    CURRENT_ACCEPTED,
    MANUAL_CHECK_CODES,
    POLICY_VERSION,
    SCHEMA_VERSION,
    AcceptanceChecklist,
    AcceptanceDecision,
    AcceptanceRecord,
    CheckResult,
    CheckStatus,
    EvidenceReference,
    RawSnapshotBinding,
    canonical_record_json,
    compute_acceptance_id,
)
from stock_quant.research.acceptance.registry import (
    AcceptanceIdentityConflict,
    AcceptanceIntegrityError,
    AcceptanceNotFound,
    AcceptanceRegistry,
    NoValidAcceptance,
)

__all__ = [
    "AUTOMATED_CHECK_CODES",
    "CURRENT_ACCEPTED",
    "MANUAL_CHECK_CODES",
    "POLICY_VERSION",
    "SCHEMA_VERSION",
    "AcceptanceChecklist",
    "AcceptanceDecision",
    "AcceptanceIdentityConflict",
    "AcceptanceIntegrityError",
    "AcceptanceNotFound",
    "AcceptanceRecord",
    "AcceptanceRegistry",
    "CheckResult",
    "CheckStatus",
    "EvidenceReference",
    "NoValidAcceptance",
    "RawSnapshotBinding",
    "canonical_record_json",
    "compute_acceptance_id",
]
