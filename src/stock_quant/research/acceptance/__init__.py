"""Strict data-acceptance contracts and read-only acceptance checks.

This package owns the verdicts formal Research consumes before factor work:

- :mod:`models` -- the :class:`AcceptanceResult` contract (deterministic,
  redacted details only), the mandatory-result vocabulary and
  :func:`enforce_required_results`, the loud gate failure primitive Task 4's
  preflight stage calls.
- :mod:`checks` -- the offline ``index_membership_evidence`` evaluation over a
  pinned dataset table and universe definition, plus the safe table reader
  that turns a missing membership table into an explicit failure.

Nothing here mutates data or touches the network (design spec: 纯读取验收).
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
    REQUIRED_ACCEPTANCE_RESULTS,
    AcceptanceGateError,
    AcceptanceResult,
    AcceptanceStatus,
    enforce_required_results,
)

__all__ = [
    "ACCEPTANCE_SCHEMA_VERSION",
    "ALLOWED_DETAIL_KEYS",
    "CODE_DEFINITION_HASH_MISMATCH",
    "CODE_INDEX_MEMBERSHIP_EVIDENCE",
    "CODE_TABLE_MISSING",
    "REQUIRED_ACCEPTANCE_RESULTS",
    "AcceptanceGateError",
    "AcceptanceResult",
    "AcceptanceStatus",
    "enforce_required_results",
    "evaluate_index_membership_evidence",
    "read_membership_table",
]
