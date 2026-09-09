"""Scenario-local whole-lot sizing against the common weight target.

``WeightTargetRebalancer`` is the buffered rule's order provider: it holds
the frozen, scenario-independent ``signal date -> WeightTargetPeriod`` book
(keyed by execution day) and converts each period into one scenario's orders
from that scenario's *signal-close* account state.  On a rebalance day it

1. values the account with the period's ``net_equity_prices`` only -- the
   signal-close map plus approved carried marks; an execution open, a
   suspension fact, a limit or any later price is never read here (those
   facts stay solely in ``ExecutionSimulator``);
2. derives each symbol's lot-floored target quantity from the common target
   weight and that signal-close equity;
3. applies the 2-percentage-point band only to continuing positions (symbols
   in both the previous and current common member sets with a positive
   target weight); entries and zero-weight exits always reconcile, and
   differences below one lot never order;
4. records every decision (orders and suppressions alike) before returning
   the deterministic sells-then-buys, symbol-ascending plan, clamping sells
   to the T+1 sellable lots -- an unsellable difference waits for a later
   rebalance day, exactly like the equal-weight rebalancer it accompanies.

One rebalancer instance serves exactly one scenario account: decision state
can never cross accounts, so scenarios sharing the common weights may
diverge freely in quantities, orders, fills and band decisions.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext
from typing import Mapping

import pandas as pd

from stock_quant.backtest.account import Account
from stock_quant.backtest.models import SELL, Order
from stock_quant.portfolio.buffered_models import (
    REBALANCE_DECISION_COLUMNS,
    BufferedRiskWeightedPolicy,
    WeightTargetPeriod,
)
from stock_quant.portfolio.rebalance_band import (
    RebalanceDecision,
    apply_rebalance_band,
)

_PRECISION = 50


class WeightTargetRebalancer:
    """One scenario's rebalance orders from the common weight targets."""

    def __init__(
        self,
        periods: Mapping[date, WeightTargetPeriod],
        lot_size: int,
        policy: BufferedRiskWeightedPolicy | None = None,
    ) -> None:
        self._periods: dict[date, WeightTargetPeriod] = {
            execution_day: period for execution_day, period in
            sorted(periods.items())
        }
        self._lot_size = int(lot_size)
        if self._lot_size < 1:
            raise ValueError(f"lot_size must be positive, got {lot_size}")
        self._policy = policy or BufferedRiskWeightedPolicy()
        # Per-execution-day recorded decisions, insertion-ordered by symbol.
        # One instance serves one scenario account: the state never leaks
        # into another scenario's rebalancer.
        self._decisions: dict[date, list[RebalanceDecision]] = {}
        self._seq = 0

    @property
    def signal_date_by_execution_day(self) -> Mapping[date, date]:
        """The frozen signal-date attribution of every rebalance day."""
        return {
            execution_day: period.signal_date
            for execution_day, period in self._periods.items()
        }

    def orders_for(self, day: date, account: Account) -> tuple[Order, ...]:
        """The ``day`` orders for ``account``; ``()`` on non-rebalance days."""
        period = self._periods.get(day)
        if period is None:
            return ()
        equity = self._signal_close_equity(period, account)
        symbols = sorted(
            {lot.symbol for lot in account.lots} | set(period.target_weights)
        )
        decisions: list[RebalanceDecision] = []
        sells: list[Order] = []
        buys: list[Order] = []
        signal_date = period.signal_date
        for symbol in symbols:
            price = period.net_equity_prices.get(symbol)
            current_quantity = account.position_quantity(symbol)
            target_weight = period.target_weights.get(symbol, Decimal("0"))
            if price is None:
                if current_quantity > 0:
                    raise ValueError(
                        f"no signal-close (or carried) price to value held "
                        f"{symbol} on {signal_date.isoformat()}; the period's "
                        "net_equity_prices must carry approved marks for "
                        "every held symbol"
                    )
                if target_weight > 0:
                    raise ValueError(
                        f"target weight for {symbol} carries no signal-close "
                        "price; a target without a valuation price cannot be "
                        "sized"
                    )
                continue
            with localcontext() as context:
                context.prec = _PRECISION
                current_weight = (
                    Decimal(current_quantity) * price / equity
                    if equity > 0
                    else Decimal("0")
                )
                target_lots = int(
                    (target_weight * equity / price) // self._lot_size
                )
            target_quantity = target_lots * self._lot_size
            is_continuing = (
                symbol in period.previous_members
                and symbol in period.current_members
                and target_weight > 0
            )
            decision = apply_rebalance_band(
                symbol=symbol,
                is_continuing=is_continuing,
                current_weight=current_weight,
                target_weight=target_weight,
                current_quantity=current_quantity,
                target_quantity=target_quantity,
                policy=self._policy,
                lot_size=self._lot_size,
            )
            if decision.should_order:
                quantity = decision.order_quantity
                if decision.order_side == SELL:
                    sellable = (
                        account.sellable_quantity(symbol, day)
                        // self._lot_size
                        * self._lot_size
                    )
                    quantity = min(quantity, sellable)
                # The decision row records the submitted reality: a T+1
                # locked sell keeps its band reason but shows the zero lots
                # it could actually submit (it self-heals a later day).
                decision = replace(decision, order_quantity=quantity)
                if quantity > 0:
                    order = self._order(
                        decision.order_side, symbol, quantity, signal_date
                    )
                    (sells if decision.order_side == SELL else buys).append(
                        order
                    )
            decisions.append(
                replace(
                    decision,
                    signal_date=signal_date,
                    execution_day=day,
                )
            )
        self._decisions[day] = decisions
        # Sells fund buys in the executor regardless of input order; returning
        # them first makes the within-side priority explicit and reproducible.
        return (*sells, *buys)

    def decision_frame(self) -> pd.DataFrame:
        """Every recorded decision, ordered by execution day then symbol."""
        records = [
            {
                "signal_date": decision.signal_date,
                "execution_day": decision.execution_day,
                "symbol": decision.symbol,
                "is_continuing": decision.is_continuing,
                "current_weight": decision.current_weight,
                "target_weight": decision.target_weight,
                "weight_difference": decision.weight_difference,
                "current_quantity": decision.current_quantity,
                "target_quantity": decision.target_quantity,
                "order_side": decision.order_side,
                "order_quantity": decision.order_quantity,
                "reason": decision.reason,
            }
            for day in sorted(self._decisions)
            for decision in self._decisions[day]
        ]
        return pd.DataFrame(records, columns=list(REBALANCE_DECISION_COLUMNS))

    def _signal_close_equity(
        self, period: WeightTargetPeriod, account: Account
    ) -> Decimal:
        """Account net equity at the period's frozen signal-close prices."""
        with localcontext() as context:
            context.prec = _PRECISION
            equity = account.cash
            for lot in account.lots:
                price = period.net_equity_prices.get(lot.symbol)
                if price is None:
                    raise ValueError(
                        f"no signal-close (or carried) price to value held "
                        f"{lot.symbol} on {period.signal_date.isoformat()}"
                    )
                equity += Decimal(lot.quantity) * price
            return equity

    def _order(
        self, side: str, symbol: str, quantity: int, signal_date: date
    ) -> Order:
        self._seq += 1
        return Order(
            order_id=f"w{self._seq:06d}",
            side=side,
            symbol=symbol,
            quantity=quantity,
            note=f"weight_rebalance:{signal_date.isoformat()}",
        )
