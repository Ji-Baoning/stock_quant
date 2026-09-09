"""Offline, read-only ``index_membership_evidence`` acceptance check.

The check turns the pure membership validator
(:func:`stock_quant.data_quality.raw_checks.validate_membership_facts`) plus a
pinned :class:`~stock_quant.research.universe.UniverseDefinition` into the
mandatory ``index_membership_evidence`` :class:`AcceptanceResult`. It reads
only the evidence it is handed -- the dataset's ``universe_membership`` table,
the trading calendar, the security-master boundaries and any official
size-exception records -- and never mutates data or touches the network.

It fails (``status=FAIL``) on:

- a missing or empty ``universe_membership`` table
  (``UNIVERSE_MEMBERSHIP_TABLE_MISSING``);
- any ``FATAL`` validator issue: missing evidence, unknown symbols, interval
  overlap, announcement look-ahead, empty master intersections, unproven
  delisting endpoints, coverage gaps or member-count mismatches
  (the validator's own stable codes);
- facts whose content hash differs from the definition's pinned
  ``membership_table_sha256`` (``UNIVERSE_DEFINITION_HASH_MISMATCH``).

Failure details are deterministic and redacted: only the covered window,
row/symbol counts, the pinned hashes and the sorted fatal error codes are
recorded -- never verbose rows or exception text.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pandas as pd

from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    SecurityMasterBoundary,
    membership_content_hash,
)
from stock_quant.data_quality.raw_checks import (
    TABLE_UNIVERSE_MEMBERSHIP,
    MembershipSizeException,
    Severity,
    validate_membership_facts,
)
from stock_quant.research.acceptance.models import (
    AcceptanceResult,
    AcceptanceStatus,
)
from stock_quant.research.universe import UniverseDefinition

CODE_INDEX_MEMBERSHIP_EVIDENCE = "index_membership_evidence"
CODE_TABLE_MISSING = "UNIVERSE_MEMBERSHIP_TABLE_MISSING"
CODE_DEFINITION_HASH_MISMATCH = "UNIVERSE_DEFINITION_HASH_MISMATCH"


def read_membership_table(context: Any) -> pd.DataFrame | None:
    """Read ``universe_membership`` from a dataset context, or ``None``.

    ``context`` is any object exposing ``tables`` and ``read`` (the pinned
    :class:`~stock_quant.data_model.dataset.DatasetContext`); a dataset that
    predates the membership table yields ``None`` so the caller can fail on a
    missing table instead of an exception.
    """
    if TABLE_UNIVERSE_MEMBERSHIP not in getattr(context, "tables", ()):
        return None
    return context.read(TABLE_UNIVERSE_MEMBERSHIP)


def evaluate_index_membership_evidence(
    frame: pd.DataFrame | None,
    *,
    definition: UniverseDefinition,
    calendar: TradingCalendar | Sequence,
    expected_sizes: Mapping[str, int],
    master: Mapping[str, SecurityMasterBoundary] | None = None,
    exceptions: Sequence[MembershipSizeException | Mapping[str, Any]]
    | None = None,
) -> AcceptanceResult:
    """Produce the mandatory ``index_membership_evidence`` result.

    ``frame`` is the dataset's ``universe_membership`` table (``None`` when
    the dataset lacks it). The definition must pin exactly the facts it
    evaluates: the table's content hash is compared against
    ``definition.membership_table_sha256`` after the pure validator found no
    fatal issue, so a mismatched definition or tampered facts both fail.
    """
    if frame is None or frame.empty:
        return _failed(
            summary=(
                "universe_membership table is missing from the pinned dataset"
            ),
            error_codes=(CODE_TABLE_MISSING,),
            hashes=_definition_hashes(definition),
        )
    issues = validate_membership_facts(
        frame,
        calendar=calendar,
        expected_sizes=expected_sizes,
        master=master,
        exceptions=exceptions,
    )
    fatal_codes = sorted(
        {issue.code for issue in issues if issue.severity is Severity.FATAL}
    )
    if fatal_codes:
        return _failed(
            summary="membership facts rejected by the acceptance validator",
            error_codes=tuple(fatal_codes),
            hashes=_definition_hashes(definition),
        )
    facts = _facts_from_frame(frame)
    if facts is None:
        return _failed(
            summary="membership rows violate the fact contract",
            error_codes=("UNIVERSE_FACT_CONTRACT_VIOLATION",),
            hashes=_definition_hashes(definition),
        )
    table_hash = membership_content_hash(facts)
    hashes = _definition_hashes(definition)
    hashes["membership_table_sha256"] = table_hash
    if table_hash != definition.membership_table_sha256:
        return _failed(
            summary=(
                "membership table hash does not match the pinned universe "
                "definition"
            ),
            error_codes=(CODE_DEFINITION_HASH_MISMATCH,),
            hashes={
                **hashes,
                "pinned_membership_table_sha256": (
                    definition.membership_table_sha256
                ),
            },
        )
    return AcceptanceResult(
        code=CODE_INDEX_MEMBERSHIP_EVIDENCE,
        status=AcceptanceStatus.PASS,
        summary="membership evidence, coverage and cardinality accepted",
        details={
            "coverage": {
                "universe_id": definition.universe_id,
                "coverage_start": definition.coverage_start.isoformat(),
                "coverage_end": definition.coverage_end.isoformat(),
            },
            "counts": {
                "fact_rows": int(len(frame)),
                "distinct_symbols": int(frame["symbol"].nunique()),
                "expected_sizes": {
                    universe: int(size)
                    for universe, size in sorted(expected_sizes.items())
                },
            },
            "hashes": hashes,
            "error_codes": [],
        },
    )


def _failed(
    *,
    summary: str,
    error_codes: tuple[str, ...],
    hashes: dict[str, str],
) -> AcceptanceResult:
    return AcceptanceResult(
        code=CODE_INDEX_MEMBERSHIP_EVIDENCE,
        status=AcceptanceStatus.FAIL,
        summary=summary,
        details={
            "hashes": hashes,
            "error_codes": sorted(set(error_codes)),
        },
    )


def _definition_hashes(definition: UniverseDefinition) -> dict[str, str]:
    return {
        "universe_id": definition.universe_id,
        "definition_version": definition.version,
        "rules_version": definition.rules_version,
        "pinned_membership_table_sha256": definition.membership_table_sha256,
    }


def _facts_from_frame(frame: pd.DataFrame) -> list[MembershipFact] | None:
    """Rebuild validated facts from table rows, or ``None`` on violation."""
    facts: list[MembershipFact] = []
    for record in frame.to_dict("records"):
        payload = dict(record)
        for column in ("raw_effective_from", "raw_effective_to",
                       "announcement_date"):
            value = payload.get(column)
            payload[column] = (
                None
                if value is None or pd.isna(value)
                else pd.Timestamp(value).date()
            )
        try:
            facts.append(MembershipFact.model_validate(payload))
        except Exception:  # noqa: BLE001 - any contract breach fails the gate
            return None
    return facts
