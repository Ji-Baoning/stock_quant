"""Effective-dated A-share fees, slippage and per-fill cost quotes (Task 9).

``CostModel`` answers every money question the executor needs from the
unadjusted execution open: the slippage-adjusted fill price (buy at
``open*(1+slippage)``, sell at ``open*(1-slippage)``), and a ``FeeBreakdown``
with commission ``max(notional*rate, minimum)`` per fill and stamp tax on
sells only.  Float ``CostRate`` fields are converted to ``Decimal`` so money
stays exact to the cent, and the active rate is the latest whose
``effective_from`` is not after the trade date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from stock_quant.backtest.models import BUY, SELL, FeeBreakdown, as_decimal
from stock_quant.config import CostConfig, CostRate, CostScenario

CENT = Decimal("0.01")


@dataclass(frozen=True)
class _DatedFees:
    effective_from: date
    commission_rate: Decimal
    minimum_commission: Decimal
    stamp_tax_sell_rate: Decimal
    slippage_rate: Decimal

    @classmethod
    def from_rate(cls, rate: CostRate) -> "_DatedFees":
        return cls(
            effective_from=rate.effective_from,
            commission_rate=Decimal(str(rate.commission_rate)),
            minimum_commission=Decimal(str(rate.minimum_commission)),
            stamp_tax_sell_rate=Decimal(str(rate.stamp_tax_sell_rate)),
            slippage_rate=Decimal(str(rate.slippage_rate)),
        )


class CostModel:
    """A dated schedule of A-share trading costs with exact Decimal rates."""

    def __init__(self, rates: CostRate | Iterable[CostRate]) -> None:
        if isinstance(rates, CostRate):
            rates = (rates,)
        converted = [_DatedFees.from_rate(rate) for rate in rates]
        if not converted:
            raise ValueError("CostModel needs at least one CostRate")
        self._schedule = tuple(sorted(converted, key=lambda item: item.effective_from))

    @classmethod
    def from_scenario(cls, scenario: CostScenario) -> "CostModel":
        """Build a model from one :class:`CostScenario` of a cost config."""
        return cls(rates=scenario.rates)

    @classmethod
    def from_config(cls, config: CostConfig, name: str) -> "CostModel":
        """Build a model from the named scenario of a cost config."""
        for scenario in config.scenarios:
            if scenario.name == name:
                return cls.from_scenario(scenario)
        available = ", ".join(scenario.name for scenario in config.scenarios)
        raise ValueError(
            f"unknown cost scenario {name!r}; available scenarios: {available}"
        )

    def _select(self, trade_date: date) -> _DatedFees:
        applicable = [
            fees for fees in self._schedule if fees.effective_from <= trade_date
        ]
        if not applicable:
            raise ValueError(
                "no cost rate is effective on or before "
                f"{trade_date.isoformat()}; earliest rate starts "
                f"{self._schedule[0].effective_from.isoformat()}"
            )
        return applicable[-1]

    # -- Dated rate accessors -------------------------------------------------

    def commission_rate(self, trade_date: date) -> Decimal:
        return self._select(trade_date).commission_rate

    def minimum_commission(self, trade_date: date) -> Decimal:
        return self._select(trade_date).minimum_commission

    def stamp_tax_rate(self, trade_date: date) -> Decimal:
        return self._select(trade_date).stamp_tax_sell_rate

    def slippage(self, trade_date: date) -> Decimal:
        return self._select(trade_date).slippage_rate

    def fill_price(self, side: str, raw_price: object, trade_date: date) -> Decimal:
        """Slippage-adjusted fill price rounded half-up to the cent."""
        if side not in (BUY, SELL):
            raise ValueError(f"side must be {BUY!r} or {SELL!r}, got {side!r}")
        raw = as_decimal(raw_price)
        if raw <= 0:
            raise ValueError(f"raw price must be positive: {raw}")
        slippage = self.slippage(trade_date)
        factor = Decimal("1") + slippage if side == BUY else Decimal("1") - slippage
        price = (raw * factor).quantize(CENT, rounding=ROUND_HALF_UP)
        if price <= 0:
            raise ValueError(f"slippage pushes the fill price non-positive: {price}")
        return price

    # -- Full per-fill quote --------------------------------------------------

    def calculate(
        self,
        side: str,
        quantity: int,
        raw_price: object,
        trade_date: date,
    ) -> FeeBreakdown:
        """Quote one fill: slippage price, gross and exact fees at the cent."""
        if side not in (BUY, SELL):
            raise ValueError(f"side must be {BUY!r} or {SELL!r}, got {side!r}")
        if quantity <= 0:
            raise ValueError(f"quantity must be positive: {quantity}")
        price = self.fill_price(side, raw_price, trade_date)
        fees = self._select(trade_date)
        gross = price * quantity
        commission = max(
            gross * fees.commission_rate, fees.minimum_commission
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        stamp_tax = Decimal("0")
        if side == SELL:
            stamp_tax = (gross * fees.stamp_tax_sell_rate).quantize(
                CENT, rounding=ROUND_HALF_UP
            )
        return FeeBreakdown(
            side=side,
            quantity=quantity,
            price=price,
            gross=gross,
            commission=commission,
            stamp_tax=stamp_tax,
        )
