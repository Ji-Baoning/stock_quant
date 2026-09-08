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
from stock_quant.research.acceptance.service import (
    AcceptanceBindingError,
    AcceptanceRejected,
)

__all__ = [
    "AUTOMATED_CHECK_CODES",
    "CURRENT_ACCEPTED",
    "MANUAL_CHECK_CODES",
    "POLICY_VERSION",
    "SCHEMA_VERSION",
    "AcceptanceBindingError",
    "AcceptanceChecklist",
    "AcceptanceDecision",
    "AcceptanceIdentityConflict",
    "AcceptanceIntegrityError",
    "AcceptanceNotFound",
    "AcceptanceRecord",
    "AcceptanceRejected",
    "AcceptanceRegistry",
    "CheckResult",
    "CheckStatus",
    "EvidenceReference",
    "NoValidAcceptance",
    "RawSnapshotBinding",
    "canonical_record_json",
    "compute_acceptance_id",
]
