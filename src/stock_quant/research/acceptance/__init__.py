"""Strict real-data acceptance contracts, registry and read-only checks.

This package owns the verdicts formal Research consumes before factor work:

- :mod:`models` -- the :class:`AcceptanceResult` run-gate contract
  (deterministic, redacted details only), the mandatory-result vocabulary
  and :func:`enforce_required_results`, the loud gate failure primitive the
  runner's ``universe_acceptance`` preflight stage calls, plus the strict
  immutable :class:`AcceptanceRecord` registry contracts and their
  content-addressed identity (:func:`compute_acceptance_id`).
- :mod:`checks` -- the offline ``real-data-v1`` automated checks over one
  pinned dataset version and the offline ``index_membership_evidence``
  evaluation over a pinned dataset table and universe definition, plus the
  safe table reader that turns a missing membership table into an explicit
  failure.
- :mod:`registry` -- the append-only, content-addressed store of published
  acceptance records under ``data/acceptances/``.
- :mod:`service` -- the operator workflows (prepare / publish / verify /
  show) over that registry.

Nothing here mutates datasets or touches the network (design spec:
纯读取验收); the only write path is the append-only registry.
"""

from stock_quant.research.acceptance.checks import (
    CODE_DEFINITION_HASH_MISMATCH,
    CODE_INDEX_MEMBERSHIP_EVIDENCE,
    CODE_TABLE_MISSING,
    evaluate_index_membership_evidence,
    read_membership_table,
)
from stock_quant.research.acceptance.models import (
    ACCEPTANCE_SCHEMA_VERSION,
    ALLOWED_DETAIL_KEYS,
    AUTOMATED_CHECK_CODES,
    CURRENT_ACCEPTED,
    MANUAL_CHECK_CODES,
    POLICY_VERSION,
    REQUIRED_ACCEPTANCE_RESULTS,
    SCHEMA_VERSION,
    AcceptanceChecklist,
    AcceptanceDecision,
    AcceptanceGateError,
    AcceptanceRecord,
    AcceptanceResult,
    AcceptanceStatus,
    CheckResult,
    CheckStatus,
    EvidenceReference,
    RawSnapshotBinding,
    canonical_record_json,
    compute_acceptance_id,
    enforce_required_results,
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
    "ACCEPTANCE_SCHEMA_VERSION",
    "ALLOWED_DETAIL_KEYS",
    "AUTOMATED_CHECK_CODES",
    "CODE_DEFINITION_HASH_MISMATCH",
    "CODE_INDEX_MEMBERSHIP_EVIDENCE",
    "CODE_TABLE_MISSING",
    "CURRENT_ACCEPTED",
    "MANUAL_CHECK_CODES",
    "POLICY_VERSION",
    "REQUIRED_ACCEPTANCE_RESULTS",
    "SCHEMA_VERSION",
    "AcceptanceBindingError",
    "AcceptanceChecklist",
    "AcceptanceDecision",
    "AcceptanceGateError",
    "AcceptanceIdentityConflict",
    "AcceptanceIntegrityError",
    "AcceptanceNotFound",
    "AcceptanceRecord",
    "AcceptanceRegistry",
    "AcceptanceRejected",
    "AcceptanceResult",
    "AcceptanceStatus",
    "CheckResult",
    "CheckStatus",
    "EvidenceReference",
    "NoValidAcceptance",
    "RawSnapshotBinding",
    "canonical_record_json",
    "compute_acceptance_id",
    "enforce_required_results",
    "evaluate_index_membership_evidence",
    "read_membership_table",
]
