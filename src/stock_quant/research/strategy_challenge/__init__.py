"""One-time, pre-registered strategy challenges (the holdout gate).

The package owns the immutable challenge workflow only: the strict
declaration/policy/identity contracts
(:mod:`stock_quant.research.strategy_challenge.models`), the atomic
single-writer holdout consumption registry
(:mod:`stock_quant.research.strategy_challenge.registry`), the pure
identity/pairing/threshold evaluation
(:mod:`stock_quant.research.strategy_challenge.compare`), the
consume-before-read orchestration and immutable artifact publication
(:mod:`stock_quant.research.strategy_challenge.service`) and the complete
HTML comparison report
(:mod:`stock_quant.research.strategy_challenge.reporting`).

It never reruns, tunes or alters either strategy while comparing them: the
baseline equal-weight experiment and the buffered challenger experiment are
consumed exactly as published, after the holdout has been irreversibly
consumed.
"""

from __future__ import annotations

from stock_quant.research.strategy_challenge.models import (
    ChallengeConclusion,
    ChallengeDeclaration,
    ChallengeIdentityScheme,
    ChallengeResult,
    MetricCell,
    ScenarioComparisonResult,
    StrategyComparisonPolicy,
    UniverseIdentity,
    canonical_challenge_json_text,
    canonical_challenge_sha256,
    compute_challenge_id,
    holdout_consumption_key,
)

__all__ = [
    "ChallengeDeclaration",
    "ChallengeIdentityScheme",
    "ChallengeConclusion",
    "ChallengeResult",
    "MetricCell",
    "ScenarioComparisonResult",
    "StrategyComparisonPolicy",
    "UniverseIdentity",
    "canonical_challenge_json_text",
    "canonical_challenge_sha256",
    "compute_challenge_id",
    "holdout_consumption_key",
]
