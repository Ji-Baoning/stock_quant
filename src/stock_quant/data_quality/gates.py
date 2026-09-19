"""Strategy-neutral data-publication gate.

The gate checks only schema conformance, key uniqueness, illegal OHLC,
provenance, quarantine reasons, report generation and adjusted-bar lineage.
Issues whose codes fall outside the gate domain — for example cross-source
close ``ERROR`` records, momentum lookback or execution-date gaps — never block
publication; they belong to ``evaluate_backtest_readiness`` in a later task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from stock_quant.data_contracts import TIER_BLOCKS_PUBLICATION
from stock_quant.data_quality.models import (
    CODE_ADJUSTED_BAR_MISSING_RAW,
    CODE_ADJUSTED_BAR_RAW_CLOSE_MISMATCH,
    CODE_ADJUSTED_BAR_UNKNOWN_ACTION,
    CODE_ADJUSTED_BAR_WRONG_BASIS,
    CODE_DUPLICATE_CONFLICT,
    CODE_INVALID_OHLC,
    CODE_NEGATIVE_AMOUNT,
    CODE_NEGATIVE_VOLUME,
    CODE_NONPOSITIVE_PRICE,
    CODE_QUARANTINE_MISSING_REASON,
    CODE_REPORT_GENERATION_FAILED,
    CODE_SCHEMA_MISMATCH,
    CODE_UNEXPLAINED_PRIMARY_GAP,
    CODE_UNKNOWN_ADJUSTMENT,
    CODE_UNKNOWN_SOURCE,
    CODE_UNREGISTERED_TABLE,
    QualityReport,
)

#: The only issue codes that block a dataset from being published. Kept as an
#: explicit allow-list of the gate conditions so that backtest-readiness
#: issues (momentum lookback, execution dates) can never block publication.
#: The adjusted-bar lineage codes are included: a total-return series that
#: cannot be reconciled with ``daily_bar`` / the canonical actions must never
#: reach the immutable dataset.
PUBLICATION_BLOCKING_CODES = frozenset(
    {
        CODE_SCHEMA_MISMATCH,
        CODE_DUPLICATE_CONFLICT,
        CODE_INVALID_OHLC,
        CODE_NONPOSITIVE_PRICE,
        CODE_NEGATIVE_VOLUME,
        CODE_NEGATIVE_AMOUNT,
        CODE_UNKNOWN_SOURCE,
        CODE_UNKNOWN_ADJUSTMENT,
        # Publish-path contract validation (spec D2): a table with no
        # data_contracts declaration blocks unconditionally — from B1 on it
        # joins the global process-code set.
        CODE_UNREGISTERED_TABLE,
        CODE_QUARANTINE_MISSING_REASON,
        CODE_REPORT_GENERATION_FAILED,
        CODE_ADJUSTED_BAR_MISSING_RAW,
        CODE_ADJUSTED_BAR_RAW_CLOSE_MISMATCH,
        CODE_ADJUSTED_BAR_WRONG_BASIS,
        CODE_ADJUSTED_BAR_UNKNOWN_ACTION,
        CODE_UNEXPLAINED_PRIMARY_GAP,
    }
)

#: Global process codes (spec §6 A1): these describe the build itself, not
#: any table's data, so no tier downgrades them — they always block.
GLOBAL_PROCESS_CODES = frozenset(
    {
        CODE_REPORT_GENERATION_FAILED,
        CODE_QUARANTINE_MISSING_REASON,
        CODE_UNREGISTERED_TABLE,
    }
)

#: The remaining blocking codes route by (code, table) tier (spec §6 A1).
TABLE_LEVEL_BLOCKING_CODES = PUBLICATION_BLOCKING_CODES - GLOBAL_PROCESS_CODES


@dataclass(frozen=True)
class GateDecision:
    """Whether a quality report passes the neutral publication gate."""

    passed: bool
    reasons: tuple[str, ...] = ()

    @property
    def decision(self) -> str:
        return "PASS" if self.passed else "BLOCK"


def evaluate_publication(
    report: QualityReport,
    *,
    table_tiers: Mapping[str, str] | None = None,
) -> GateDecision:
    """Return ``PASS`` unless the report contains a publication-blocking issue.

    Global process codes always block.  Table-level codes block when the
    issue's table is core or has no declaration in ``table_tiers``
    (fail-closed, spec D1); anchored and research_only tables downgrade
    instead — the publish path turns those into coverage evidence (Task 6).
    With ``table_tiers=None`` every table is treated as undeclared, which
    preserves the legacy all-blocking behavior.
    """
    tiers = dict(table_tiers or {})
    reasons: set[str] = set()
    for item in report.issues:
        if item.code not in PUBLICATION_BLOCKING_CODES:
            continue
        if item.code in GLOBAL_PROCESS_CODES:
            reasons.add(_describe(item))
            continue
        tier = tiers.get(item.table)
        if TIER_BLOCKS_PUBLICATION.get(tier, True):
            reasons.add(_describe(item))
    if not reasons:
        return GateDecision(passed=True)
    return GateDecision(passed=False, reasons=tuple(sorted(reasons)))


def _describe(item) -> str:
    parts = [item.code, "in", item.table or "unknown-table"]
    if item.symbol is not None:
        parts.append(f"symbol={item.symbol}")
    if item.trade_date is not None:
        parts.append(f"date={item.trade_date.isoformat()}")
    return " ".join(parts)
