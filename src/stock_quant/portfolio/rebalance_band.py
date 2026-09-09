"""The absolute 2-percentage-point rebalance band.

``apply_rebalance_band`` decides one symbol's reconciliation for one
scenario: a *continuing* position -- a symbol in both the previous and the
current common member set with a positive target weight -- is rebalanced
only when its weight difference reaches the band
(``abs(current - target) >= rebalance_band_absolute``; exactly the band
rebalances, strictly below it is suppressed as ``within_rebalance_band``).
New entries and zero-weight exits never use the band: they always reconcile
into their order, and any difference below one lot emits ``below_one_lot``
with no order.  Every suppression is a recorded :class:`RebalanceDecision`
row -- symbol, both weights, the difference, both quantities and a stable
reason -- never a silent drop, and never an execution rejection: the band
and lot suppression happen before any order exists.

All quantities are whole lots; the lot size is the canonical A-share board
lot the execution layer trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

from stock_quant.backtest.models import BUY, LOT_SIZE, SELL
from stock_quant.portfolio.buffered_models import BufferedRiskWeightedPolicy

#: Stable decision reasons.  ``within_rebalance_band`` and ``below_one_lot``
#: are the two suppression reasons the audit contract names; ``at_target``
#: records the already-reconciled rows; the remaining three name the order
#: the decision produced.
REASON_WITHIN_BAND = "within_rebalance_band"
REASON_BELOW_ONE_LOT = "below_one_lot"
REASON_AT_TARGET = "at_target"
REASON_OUTSIDE_BAND = "rebalance_outside_band"
REASON_ENTERING = "entering_position"
REASON_EXITING = "exiting_position"


@dataclass(frozen=True)
class RebalanceDecision:
    """One symbol's recorded reconciliation decision for one scenario day.

    ``should_order`` is exactly ``order_quantity > 0``: a suppressed row
    keeps its full weight/quantity context beside its stable reason.  The
    scenario dates are ``None`` on a bare band decision and stamped by the
    rebalancer when the row is recorded.
    """

    symbol: str
    is_continuing: bool
    current_weight: Decimal
    target_weight: Decimal
    weight_difference: Decimal
    current_quantity: int
    target_quantity: int
    order_side: str
    order_quantity: int
    reason: str
    signal_date: date | None = None
    execution_day: date | None = None

    @property
    def should_order(self) -> bool:
        return self.order_quantity > 0


def apply_rebalance_band(
    *,
    symbol: str,
    is_continuing: bool,
    current_weight: Decimal,
    target_weight: Decimal,
    current_quantity: int,
    target_quantity: int,
    policy: BufferedRiskWeightedPolicy,
    lot_size: int = LOT_SIZE,
) -> RebalanceDecision:
    """The band decision for one symbol (pure arithmetic, no account)."""
    difference = target_weight - current_weight
    quantity_difference = target_quantity - current_quantity
    if is_continuing and abs(difference) < policy.rebalance_band_absolute:
        return RebalanceDecision(
            symbol=symbol,
            is_continuing=True,
            current_weight=current_weight,
            target_weight=target_weight,
            weight_difference=difference,
            current_quantity=current_quantity,
            target_quantity=target_quantity,
            order_side="",
            order_quantity=0,
            reason=REASON_WITHIN_BAND,
        )
    if quantity_difference == 0:
        return RebalanceDecision(
            symbol=symbol,
            is_continuing=is_continuing,
            current_weight=current_weight,
            target_weight=target_weight,
            weight_difference=difference,
            current_quantity=current_quantity,
            target_quantity=target_quantity,
            order_side="",
            order_quantity=0,
            reason=REASON_WITHIN_BAND if is_continuing else REASON_AT_TARGET,
        )
    if abs(quantity_difference) < lot_size:
        return RebalanceDecision(
            symbol=symbol,
            is_continuing=is_continuing,
            current_weight=current_weight,
            target_weight=target_weight,
            weight_difference=difference,
            current_quantity=current_quantity,
            target_quantity=target_quantity,
            order_side="",
            order_quantity=0,
            reason=REASON_BELOW_ONE_LOT,
        )
    side = BUY if quantity_difference > 0 else SELL
    if is_continuing:
        reason = REASON_OUTSIDE_BAND
    else:
        reason = REASON_ENTERING if side == BUY else REASON_EXITING
    return RebalanceDecision(
        symbol=symbol,
        is_continuing=is_continuing,
        current_weight=current_weight,
        target_weight=target_weight,
        weight_difference=difference,
        current_quantity=current_quantity,
        target_quantity=target_quantity,
        order_side=side,
        order_quantity=abs(quantity_difference),
        reason=reason,
    )


def suppressions_of(
    decisions: Mapping[str, RebalanceDecision],
) -> dict[str, str]:
    """The suppressed symbols of one day with their stable reasons."""
    return {
        symbol: decision.reason
        for symbol, decision in sorted(decisions.items())
        if not decision.should_order
        and decision.reason in (REASON_WITHIN_BAND, REASON_BELOW_ONE_LOT)
    }
