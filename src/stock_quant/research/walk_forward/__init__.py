"""Deterministic, audit-ready fixed-calendar Walk-Forward OOS evaluation.

The package owns the first-phase Walk-Forward workflow only: strict frozen
policies (:mod:`stock_quant.research.walk_forward.policy`), the three frozen
research snapshots and the experiment-identity bundle
(:mod:`stock_quant.research.walk_forward.snapshots`), the immutable fold
schedule and its separate outcome ledger
(:mod:`stock_quant.research.walk_forward.schedule`), pure OOS/risk/cost
metrics (:mod:`stock_quant.research.walk_forward.metrics`), the single
stability verdict (:mod:`stock_quant.research.walk_forward.evaluation`) and
the isolated fold runner (:mod:`stock_quant.research.walk_forward.runner`).

It never selects parameters, rewrites calendars, or bypasses the data
acceptance and point-in-time universe gates owned by the rest of
:mod:`stock_quant.research`.
"""

from __future__ import annotations

from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    WalkForwardPolicy,
    canonical_json_text,
    canonical_sha256,
)

__all__ = [
    "StabilityPolicy",
    "WalkForwardPolicy",
    "canonical_json_text",
    "canonical_sha256",
]
