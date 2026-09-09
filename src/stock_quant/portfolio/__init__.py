"""Deterministic target-portfolio construction from factor ranks.

Two construction families share this package: the legacy fixed-slot
equal-weight builder (:mod:`stock_quant.portfolio.equal_weight`, the
baseline / engineering rule) and the buffered risk-weighted rule
(:mod:`stock_quant.portfolio.buffered_models` plus
:mod:`stock_quant.portfolio.buffered_risk_weight`), whose frozen policy,
trusted risk estimation, two-stage buffered membership and capped decimal
weights feed the common construction target every cost scenario consumes.
"""

from stock_quant.portfolio.buffered_models import (
    MEMBER_STATUS_ENTERED,
    MEMBER_STATUS_EXITED,
    MEMBER_STATUS_NOT_SELECTED,
    MEMBER_STATUS_RETAINED,
    MEMBER_STATUS_RISK_INVALID,
    PORTFOLIO_CONSTRUCTION_COLUMNS,
    REBALANCE_DECISION_COLUMNS,
    BufferedRiskWeightedPolicy,
    PortfolioConstructionResult,
    WeightTargetPeriod,
)
from stock_quant.portfolio.buffered_risk_weight import (
    BUFFERED_RULE_NAME,
    allocate_capped_inverse_volatility,
    buffered_portfolio_rule_version,
    build_buffered_target,
)

__all__ = [
    "BUFFERED_RULE_NAME",
    "MEMBER_STATUS_ENTERED",
    "MEMBER_STATUS_EXITED",
    "MEMBER_STATUS_NOT_SELECTED",
    "MEMBER_STATUS_RETAINED",
    "MEMBER_STATUS_RISK_INVALID",
    "PORTFOLIO_CONSTRUCTION_COLUMNS",
    "REBALANCE_DECISION_COLUMNS",
    "BufferedRiskWeightedPolicy",
    "PortfolioConstructionResult",
    "WeightTargetPeriod",
    "allocate_capped_inverse_volatility",
    "build_buffered_target",
    "buffered_portfolio_rule_version",
]
