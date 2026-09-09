"""The frozen buffered risk-weighted portfolio policy and its pure targets.

``BufferedRiskWeightedPolicy`` is the pre-registered construction contract of
the ``buffered_risk_weighted`` portfolio rule: two-stage ranking
(``entry_rank``/``hold_rank`` over the risk-eligible rank), trusted
60-session risk estimation with a 40-real-close floor, a 10% annualized
volatility floor, capped inverse-volatility weights over a 100% long-only
gross exposure quantized to ``1e-12``, and a 2-percentage-point absolute
rebalance band applied only to continuing positions.  Every field enters the
canonical policy JSON that feeds ``portfolio_rule_version``,
``parameters_hash``, the strategy snapshot and therefore the experiment id;
the defaults are the only approved first-run parameters and later explicit
changes are new strategy identities, never mutations of a registered run.

The target types are deliberately account-free: ``PortfolioConstructionResult``
is the one *common* construction decision of a signal date (members, decimal
weights, cash residue, the ordered audit frame) and ``WeightTargetPeriod`` the
frozen per-signal-date weight target every cost scenario consumes.  Neither
carries quantities, account values, fills or any scenario field -- scenario
accounts may diverge only downstream in their own sizing decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Stable ``member_status`` vocabulary of the construction audit frame.
MEMBER_STATUS_RETAINED = "retained"
MEMBER_STATUS_ENTERED = "entered"
MEMBER_STATUS_EXITED = "exited"
MEMBER_STATUS_NOT_SELECTED = "not_selected"
MEMBER_STATUS_RISK_INVALID = "risk_invalid"

#: The exact, ordered columns of every common ``portfolio_construction``
#: frame: one row per factor-valid candidate plus every previous target, so
#: membership, risk trust and the weight path from risk score to quantized
#: target weight are auditable without touching any scenario ledger.
PORTFOLIO_CONSTRUCTION_COLUMNS = (
    "signal_date",
    "symbol",
    "previous_target_member",
    "raw_momentum_rank",
    "risk_eligible_rank",
    "member_status",
    "member_reason",
    "window_start",
    "window_end",
    "real_close_observations",
    "suspension_carry_days",
    "risk_is_valid",
    "risk_invalid_reason",
    "raw_annualized_volatility",
    "applied_annualized_volatility",
    "risk_score",
    "raw_weight",
    "capped_weight",
    "target_weight",
    "cash_weight",
    "portfolio_rule_version",
)

#: The exact, ordered columns of every scenario-local ``rebalance_decisions``
#: frame: one row per reconciled symbol per rebalance day, including the
#: suppressed differences (``within_rebalance_band`` / ``below_one_lot``) so
#: a suppressed adjustment is auditable, never silent.
REBALANCE_DECISION_COLUMNS = (
    "signal_date",
    "execution_day",
    "symbol",
    "is_continuing",
    "current_weight",
    "target_weight",
    "weight_difference",
    "current_quantity",
    "target_quantity",
    "order_side",
    "order_quantity",
    "reason",
)

#: The policy's Decimal-valued fields; every one must be finite and positive.
_DECIMAL_FIELDS = (
    "volatility_floor_annualized",
    "max_single_weight",
    "rebalance_band_absolute",
    "gross_exposure",
    "weight_quantum",
)


class BufferedRiskWeightedPolicy(BaseModel):
    """The frozen, pre-registered buffered construction policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_count: int = Field(default=10, ge=1)
    entry_rank: int = Field(default=10, ge=1)
    hold_rank: int = Field(default=15, ge=1)
    risk_lookback_days: int = Field(default=60, ge=2)
    min_risk_observations: int = Field(default=40, ge=2)
    volatility_floor_annualized: Decimal = Decimal("0.10")
    max_single_weight: Decimal = Decimal("0.15")
    rebalance_band_absolute: Decimal = Decimal("0.02")
    gross_exposure: Decimal = Decimal("1.00")
    long_only: bool = True
    leverage: bool = False
    weight_quantum: Decimal = Decimal("0.000000000001")

    @model_validator(mode="after")
    def _check_contract(self) -> "BufferedRiskWeightedPolicy":
        for name in _DECIMAL_FIELDS:
            value = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise ValueError(
                    f"{name} must be a finite positive Decimal, got {value}"
                )
        if self.entry_rank > self.hold_rank:
            raise ValueError(
                "entry_rank must not exceed hold_rank: "
                f"{self.entry_rank} > {self.hold_rank}"
            )
        if self.target_count != self.entry_rank:
            raise ValueError(
                "target_count must equal entry_rank: "
                f"{self.target_count} != {self.entry_rank}"
            )
        if self.min_risk_observations > self.risk_lookback_days:
            raise ValueError(
                "min_risk_observations must not exceed risk_lookback_days: "
                f"{self.min_risk_observations} > {self.risk_lookback_days}"
            )
        if self.long_only is not True:
            raise ValueError("the policy is long-only; long_only cannot be False")
        if self.leverage is not False:
            raise ValueError("the policy forbids leverage; leverage cannot be True")
        return self


@dataclass(frozen=True)
class PortfolioConstructionResult:
    """One signal date's common, scenario-free construction decision.

    ``target_members`` is the buffered membership in ascending full-symbol
    order, ``target_weights`` the quantized decimal target weight per member,
    ``cash_weight`` the unallocatable residue (always
    ``1.00 - sum(target_weights)``) and ``audit_frame`` the ordered
    construction audit (exact :data:`PORTFOLIO_CONSTRUCTION_COLUMNS`).  The
    result never contains quantities, account values or scenario fields.
    """

    signal_date: date
    target_members: tuple[str, ...]
    target_weights: Mapping[str, Decimal]
    cash_weight: Decimal
    audit_frame: pd.DataFrame

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_members", tuple(self.target_members))
        object.__setattr__(self, "target_weights", dict(self.target_weights))
        members = self.target_members
        if len(set(members)) != len(members):
            raise ValueError("target_members must be unique")
        if list(members) != sorted(members):
            raise ValueError(
                "target_members must be in ascending full-symbol string order"
            )
        if set(self.target_weights) != set(members):
            raise ValueError(
                "target_weights keys must equal the target member set"
            )
        total = self.cash_weight + sum(self.target_weights.values())
        if total != Decimal("1.00"):
            raise ValueError(
                f"target weights plus cash must equal gross exposure 1.00, "
                f"got {total}"
            )
        frame = self.audit_frame
        if list(frame.columns) != list(PORTFOLIO_CONSTRUCTION_COLUMNS):
            raise ValueError(
                "the construction audit_frame columns must be exactly "
                f"{list(PORTFOLIO_CONSTRUCTION_COLUMNS)} in order"
            )


@dataclass(frozen=True)
class WeightTargetPeriod:
    """One signal date's frozen weight target, shared by every scenario.

    ``target_weights`` maps the current common member set to positive decimal
    weights; ``net_equity_prices`` is the signal-close valuation price map
    (approved carried marks included) every scenario values its account and
    quantities with; ``previous_members``/``current_members`` are the common
    membership sets the rebalance band's continuing test reads.  The period
    contains no execution-day data: execution open, suspension, limit and any
    later price stay solely in the execution simulator.
    """

    signal_date: date
    target_weights: Mapping[str, Decimal]
    net_equity_prices: Mapping[str, Decimal]
    previous_members: frozenset[str]
    current_members: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_weights", dict(self.target_weights))
        object.__setattr__(self, "net_equity_prices", dict(self.net_equity_prices))
        object.__setattr__(
            self, "previous_members", frozenset(self.previous_members)
        )
        object.__setattr__(
            self, "current_members", frozenset(self.current_members)
        )
        if set(self.target_weights) != set(self.current_members):
            raise ValueError(
                "target_weights keys must equal the current common member set"
            )
        for symbol, weight in self.target_weights.items():
            if not weight.is_finite() or weight <= 0:
                raise ValueError(
                    f"target weight for {symbol} must be a positive finite "
                    f"Decimal, got {weight}"
                )
        for symbol, price in self.net_equity_prices.items():
            if not price.is_finite() or price <= 0:
                raise ValueError(
                    f"the net-equity price for {symbol} must be a positive "
                    f"finite Decimal, got {price}"
                )
