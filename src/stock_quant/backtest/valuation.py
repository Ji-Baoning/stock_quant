"""Close-of-day holding valuation against unadjusted closes (Task 10).

``value_account`` marks every holding to its unadjusted close on a valuation
date.  A held name with no bar -- or no *valid* close -- on that date is valued
at its last valid unadjusted close for VALUATION ONLY: the resulting
``HoldingValuation`` is flagged stale, records how many sessions the price has
been carried, and the account valuation reports the stale market value and the
stale share of total equity.  Carried prices are never used to fill orders --
a missing bar on an execution date simply yields no fill -- so valuation carry
never leaks into execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

from stock_quant.backtest.account import Account
from stock_quant.backtest.models import as_decimal

PRICE_SOURCE_CLOSE = "close"
PRICE_SOURCE_CARRIED = "carried"


class NoCloseToValue(ValueError):
    """A held name has neither a close today nor any earlier valid close."""


@dataclass(frozen=True)
class HoldingValuation:
    """One held name priced for valuation: fresh or carried."""

    symbol: str
    quantity: int
    price: Decimal
    price_source: str
    market_value: Decimal
    stale_days: int

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise TypeError(f"quantity must be an int, got {self.quantity!r}")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive: {self.quantity}")
        if self.price_source not in (PRICE_SOURCE_CLOSE, PRICE_SOURCE_CARRIED):
            raise ValueError(f"unknown price source {self.price_source!r}")
        if self.stale_days < 0:
            raise ValueError(f"stale_days must be non-negative: {self.stale_days}")
        if self.stale_days > 0 and self.price_source == PRICE_SOURCE_CLOSE:
            raise ValueError("a fresh close cannot be flagged stale")
        if self.market_value != self.price * self.quantity:
            raise ValueError(
                "market_value must equal price * quantity: "
                f"{self.market_value} != {self.price} * {self.quantity}"
            )

    @property
    def is_stale(self) -> bool:
        return self.stale_days > 0


@dataclass(frozen=True)
class AccountValuation:
    """One account's close-of-day mark, with the stale share made explicit."""

    cash: Decimal
    holdings: tuple[HoldingValuation, ...]
    trade_date: date | None = None

    @property
    def market_value(self) -> Decimal:
        return sum((holding.market_value for holding in self.holdings), Decimal("0"))

    @property
    def total_equity(self) -> Decimal:
        return self.cash + self.market_value

    @property
    def stale_market_value(self) -> Decimal:
        return sum(
            (holding.market_value for holding in self.holdings if holding.is_stale),
            Decimal("0"),
        )

    @property
    def stale_days(self) -> int:
        return max((holding.stale_days for holding in self.holdings), default=0)

    @property
    def stale_valuation(self) -> bool:
        return any(holding.is_stale for holding in self.holdings)

    @property
    def stale_ratio(self) -> Decimal:
        total = self.total_equity
        if total == 0:
            return Decimal("0")
        return self.stale_market_value / total


def value_account(
    account: Account,
    *,
    closes: Mapping[str, object] | None = None,
    carried: Mapping[str, tuple[object, int]] | None = None,
    trade_date: date | None = None,
) -> AccountValuation:
    """Value ``account`` at its unadjusted closes on ``trade_date``.

    ``closes`` maps each held symbol that has a valid close that day to that
    close; ``carried`` maps each held symbol without a close that day to its
    ``(last_valid_close, stale_days)``.  Holdings are priced only when the
    account holds them and priced in deterministic (symbol-sorted) order.
    """
    if not isinstance(account, Account):
        raise TypeError(f"account must be an Account, got {type(account).__name__}")
    if closes is None:
        closes = {}
    if carried is None:
        carried = {}
    held = sorted(
        {lot.symbol for lot in account.lots if lot.quantity > 0}
    )
    valuations: list[HoldingValuation] = []
    for symbol in held:
        quantity = account.position_quantity(symbol)
        if symbol in closes:
            price = as_decimal(closes[symbol])
            valuations.append(
                HoldingValuation(
                    symbol=symbol,
                    quantity=quantity,
                    price=price,
                    price_source=PRICE_SOURCE_CLOSE,
                    market_value=price * quantity,
                    stale_days=0,
                )
            )
        elif symbol in carried:
            last_close, stale_days = carried[symbol]
            price = as_decimal(last_close)
            if stale_days <= 0:
                raise ValueError(
                    f"carried price for {symbol} must carry at least one session"
                )
            valuations.append(
                HoldingValuation(
                    symbol=symbol,
                    quantity=quantity,
                    price=price,
                    price_source=PRICE_SOURCE_CARRIED,
                    market_value=price * quantity,
                    stale_days=int(stale_days),
                )
            )
        else:
            raise NoCloseToValue(
                f"no close available to value held {symbol} on "
                f"{trade_date.isoformat() if trade_date is not None else '<date>'}"
            )
    return AccountValuation(
        cash=account.cash, holdings=tuple(valuations), trade_date=trade_date
    )
