"""The program area of a standing worksheet.

Split from :mod:`~stock_quant.research.acceptance.worksheet` because it is a
different job: that module owns the file format and the revision chain, this
one owns what the operator is being asked to confirm -- the candidate rows,
the pending-review queue, the comparison result and the confirmation strength.

The strength is a *verdict*, never an input.  Only an excerpt that covers the
configuration with no difference at all yields ``EXTERNAL_CORROBORATED``; the
absence of an external input always yields ``OPERATOR_ATTESTED``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from stock_quant.research.acceptance.external_inputs import (
    CalendarComparison,
    PriceComparison,
    RuleComparison,
    VersionFacts,
)
from stock_quant.research.acceptance.models import (
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
)
from stock_quant.research.acceptance.worksheet import (
    EXTERNAL_CORROBORATED,
    OPERATOR_ATTESTED,
    WorksheetError,
)

__all__ = [
    "build_program",
    "candidate_bytes",
    "candidate_name",
    "candidate_payload",
    "candidate_rows",
    "comparison_for_checklist",
    "review_queue",
    "strength_for",
]

#: The candidate rows' stable prefixes, one per operator-only code.
_CANDIDATE_PREFIX = {
    "exchange_calendar_sample": "calendar",
    "trading_rule_effective_dates": "rule",
    "cross_source_price_sample": "price",
}


def candidate_rows(facts: VersionFacts) -> dict[str, tuple[str, ...]]:
    """Each operator-only code's version-side candidate rows.

    Grouped by the code that reviews them rather than returned as one pile:
    a calendar queue that also listed rule rows would ask the operator to
    acknowledge something their signature says nothing about.
    """
    return {
        "exchange_calendar_sample": tuple(
            f"calendar:{day.isoformat()}" for day in facts.open_days
        ),
        "trading_rule_effective_dates": tuple(
            f"rule:{row.board}|{row.status}|{row.effective_from}|{row.rate}"
            for row in facts.rules
        ),
        "cross_source_price_sample": tuple(
            f"price:{row['symbol']}@{row['trade_date'].isoformat()}"
            for row in facts.price_sample.to_dict("records")
        ),
    }


def candidate_name(code: str) -> str:
    """The stored file name of one code's candidate snapshot."""
    return f"{code}.candidates.json"


def candidate_payload(code: str, rows: Sequence[str]) -> dict[str, object]:
    """The candidate snapshot as plain data: the code and its rows."""
    return {"code": code, "rows": list(rows)}


def candidate_bytes(code: str, rows: Sequence[str]) -> bytes:
    """The candidate snapshot's canonical bytes (stable for equal rows)."""
    return (
        json.dumps(
            candidate_payload(code, rows),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def review_queue(
    code: str,
    rows: Sequence[str],
    *,
    previous_rows: Sequence[str] | None,
    supersede: bool = False,
    extra: Sequence[str] = (),
) -> tuple[str, ...]:
    """The rows this signing must have looked at, computed by the program.

    The six mechanisable codes have no queue at all: their evidence is built
    and bound by ``prepare``, so there is nothing an operator has to hunt for.
    A first signing, a supersede, and the cross-source sample always queue the
    full candidate set; everything else queues only what changed since the
    last ``PASS`` revision.
    """
    if code in MECHANISABLE_CODES:
        return ()
    if code not in OPERATOR_ONLY_CODES:
        raise WorksheetError("revision_chain_invalid")
    if supersede or previous_rows is None or code == "cross_source_price_sample":
        return tuple(rows) + tuple(extra)
    seen = set(previous_rows)
    delta = tuple(row for row in rows if row not in seen)
    return delta + tuple(extra)


def strength_for(code: str, comparison: object) -> str:
    """The confirmation strength the program grants, never the operator asks for."""
    if code in MECHANISABLE_CODES:
        return OPERATOR_ATTESTED
    if code not in OPERATOR_ONLY_CODES:
        raise WorksheetError("revision_chain_invalid")
    if code == "cross_source_price_sample":
        # Design decision: a cross-source check is corroboration only when a
        # second source actually ran; the shipped single-source versions never
        # reach it, so the strength is fixed rather than dressed up.
        return OPERATOR_ATTESTED
    corroborated = (
        comparison.corroborated
        if isinstance(comparison, (CalendarComparison, RuleComparison))
        else False
    )
    return EXTERNAL_CORROBORATED if corroborated else OPERATOR_ATTESTED


def comparison_for_checklist(code: str, comparison: object) -> dict[str, object]:
    """The comparison result as plain JSON-ready data for the program area."""
    if isinstance(comparison, CalendarComparison):
        return {
            "status": comparison.status,
            "has_close_column": comparison.has_close_column,
            "dataset_open_official_absent": list(
                comparison.dataset_open_official_absent
            ),
            "official_open_dataset_absent": list(
                comparison.official_open_dataset_absent
            ),
            "official_closed_dataset_open": list(
                comparison.official_closed_dataset_open
            ),
        }
    if isinstance(comparison, RuleComparison):
        return {
            "status": comparison.status,
            "uncovered": list(comparison.uncovered),
            "conflicting": list(comparison.conflicting),
            "unclaimed": list(comparison.unclaimed),
        }
    if isinstance(comparison, PriceComparison):
        return {
            "status": comparison.status,
            "reason": comparison.reason,
            "sources": list(comparison.sources),
            "rows_compared": comparison.rows_compared,
            "exceeding": list(comparison.exceeding),
        }
    return {"status": "no_external_input"}


def build_program(
    *,
    code: str,
    dataset_version: str,
    dataset_manifest_sha256: str,
    window: Mapping[str, str],
    generated_at: str,
    candidate: Sequence[Mapping[str, str]],
    previous_signed: Mapping[str, object] | None,
    supersedes: Mapping[str, str] | None,
    external_input: Mapping[str, str] | None = None,
    comparison: Mapping[str, object] | None = None,
    strength: str | None = None,
    queue: Sequence[str] = (),
) -> dict[str, object]:
    """One worksheet's program area: the shared header plus operator-only keys.

    The three operator-only keys are present only for the codes that have an
    external contract.  Writing them for a mechanisable code would suggest an
    input that code does not accept.
    """
    program: dict[str, object] = {
        "code": code,
        "dataset_version": dataset_version,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "window": dict(window),
        "generated_at": generated_at,
        "candidate_evidence": [dict(row) for row in candidate],
        "previous_signed": dict(previous_signed) if previous_signed else None,
        "supersedes": dict(supersedes) if supersedes else None,
    }
    if code in OPERATOR_ONLY_CODES:
        program["external_input"] = dict(external_input) if external_input else None
        program["comparison"] = (
            dict(comparison) if comparison else {"status": "no_external_input"}
        )
        program["strength"] = strength or OPERATOR_ATTESTED
        program["queue"] = list(queue)
    return program
