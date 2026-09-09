"""Account-aware weekly rebalancer (account-reconciliation rebalancing).

``AccountAwareWeeklyRebalancer`` turns one frozen, scenario-independent target
book (``execution_date -> (signal_date, targets)``) into a day's orders from
the *realized* account state at the execution open -- never from an idealized
previous target book.  Every rebalance day re-derives the full difference
``frozen target - actual holdings`` (invariant I1a): a difference that went
unfilled last week re-enters this week's deterministic computation while the
holdings still disagree with the target, so a rejected exit cannot silently
leave a stale residual behind.

Symbol-set invariant (U0): the day's symbols are the union of the currently
held lots and the period's targets, with a missing target meaning zero -- an
exiting name still held produces its sell even though it is absent from the
target book.  Iterating the targets alone would preserve exactly the bug this
module replaces.

Quantity rules: buys are the whole-lot floor of the target difference and are
never pre-clamped to cash (the executor's partial-fill self-healing owns
affordability); sells are the whole-lot floor of the excess, clamped to the
T+1 sellable quantity (an unsellable excess waits for a later rebalance day to
self-heal; a corporate-action odd-lot residual below one lot stays stale by
design because whole-lot sells cannot dispose of it).

Each symbol gets at most one order side per day; sells are returned before
buys (the executor preserves input order as the within-side funding priority)
and symbols ascend within each side, so the plan is deterministic and
reproducible.
"""

from __future__ import annotations

from datetime import date
from typing import Mapping

from stock_quant.backtest.account import Account
from stock_quant.backtest.models import BUY, LOT_SIZE, SELL, Order

#: One frozen rebalance period: ``execution_date -> (signal_date, targets)``.
#: ``targets`` maps symbol to the frozen whole-lot target quantity and is the
#: same object for every cost scenario (scenario independence).
TargetsByDay = Mapping[date, tuple[date, Mapping[str, int]]]


class AccountAwareWeeklyRebalancer:
    """Emit one scenario's rebalance orders from its live account state."""

    def __init__(self, targets_by_day: TargetsByDay) -> None:
        self._targets_by_day: dict[date, tuple[date, dict[str, int]]] = {
            execution_day: (signal_date, dict(targets))
            for execution_day, (signal_date, targets) in sorted(
                targets_by_day.items()
            )
        }
        self._seq = 0

    @property
    def signal_date_by_execution_day(
        self,
    ) -> Mapping[date, date]:
        """The frozen signal-date attribution of every rebalance day."""
        return {
            execution_day: signal_date
            for execution_day, (signal_date, _) in self._targets_by_day.items()
        }

    def orders_for(self, day: date, account: Account) -> tuple[Order, ...]:
        """The ``day`` orders for ``account``; ``()`` on non-rebalance days."""
        entry = self._targets_by_day.get(day)
        if entry is None:
            return ()
        signal_date, targets = entry
        # U0: held lots join the target symbols, and an absent target is an
        # explicit zero, so an exiting name still held keeps producing its
        # sell (the stale-residual retry).
        symbols = sorted({lot.symbol for lot in account.lots} | set(targets))
        sells: list[Order] = []
        buys: list[Order] = []
        for symbol in symbols:
            held = account.position_quantity(symbol)
            target = targets.get(symbol, 0)
            if held > target:
                quantity = (
                    min(held - target, account.sellable_quantity(symbol, day))
                    // LOT_SIZE
                    * LOT_SIZE
                )
                if quantity:
                    sells.append(
                        self._order(SELL, symbol, quantity, signal_date)
                    )
            elif held < target:
                quantity = (target - held) // LOT_SIZE * LOT_SIZE
                if quantity:
                    buys.append(self._order(BUY, symbol, quantity, signal_date))
        # Sells fund buys in the executor regardless of input order; returning
        # them first makes the within-side priority explicit and reproducible.
        return (*sells, *buys)

    def _order(
        self, side: str, symbol: str, quantity: int, signal_date: date
    ) -> Order:
        self._seq += 1
        return Order(
            order_id=f"r{self._seq:06d}",
            side=side,
            symbol=symbol,
            quantity=quantity,
            note=f"weekly_rebalance:{signal_date.isoformat()}",
        )
