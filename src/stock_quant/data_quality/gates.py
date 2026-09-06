"""Strategy-neutral data-publication gate.

The gate checks only schema conformance, key uniqueness, illegal OHLC,
provenance, quarantine reasons and report generation. Issues whose codes fall
outside the gate domain — for example cross-source close ``ERROR`` records, momentum
lookback or execution-date gaps — never block publication; they belong to
``evaluate_backtest_readiness`` in a later task.
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_quant.data_quality.models import (
    CODE_DUPLICATE_CONFLICT,
    CODE_INVALID_OHLC,
    CODE_NEGATIVE_AMOUNT,
    CODE_NEGATIVE_VOLUME,
    CODE_NONPOSITIVE_PRICE,
    CODE_QUARANTINE_MISSING_REASON,
    CODE_REPORT_GENERATION_FAILED,
    CODE_SCHEMA_MISMATCH,
    CODE_UNKNOWN_ADJUSTMENT,
    CODE_UNKNOWN_SOURCE,
    QualityReport,
)

#: The only issue codes that block a dataset from being published. Kept as an
#: explicit allow-list of the six gate conditions so that backtest-readiness
#: issues (momentum lookback, execution dates) can never block publication.
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
        CODE_QUARANTINE_MISSING_REASON,
        CODE_REPORT_GENERATION_FAILED,
    }
)


@dataclass(frozen=True)
class GateDecision:
    """Whether a quality report passes the neutral publication gate."""

    passed: bool
    reasons: tuple[str, ...] = ()

    @property
    def decision(self) -> str:
        return "PASS" if self.passed else "BLOCK"


def evaluate_publication(report: QualityReport) -> GateDecision:
    """Return ``PASS`` unless the report contains a publication-blocking issue."""
    reasons: set[str] = set()
    for item in report.issues:
        if item.code not in PUBLICATION_BLOCKING_CODES:
            continue
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
