"""Fixed-slot equal-weight portfolio construction from factor ranks (Task 8).

``TopNEqualWeight`` converts one signal date's standardized ``FactorResult``
into a deterministic, lot-sized ``PortfolioTarget``.  It ranks only
``is_valid`` rows by descending ``processed_value`` then ascending ``symbol``
(stable), selects at most ``top_n`` and assigns each selected name the same
slot weight ``1 / top_n``.  The target quantity is estimated on the signal day
from the unadjusted close and the equity ``capital`` and floored down to the
lot size, so no position can overdraw the slot's capital.  A name that ranks
inside ``top_n`` but has no positive finite signal-day close -- or whose
floored quantity falls below one lot -- is excluded and its slot weight is left
as unallocated cash; the builder never reads a later price and never backfills
a dropped slot with a later-ranked name, so identical inputs always yield the
identical target.
"""

from __future__ import annotations

import math

import pandas as pd

from stock_quant.factors.models import FactorResult
from stock_quant.portfolio.models import (
    PORTFOLIO_TARGET_COLUMNS,
    PortfolioTarget,
)


class TopNEqualWeight:
    """Select the top ``top_n`` valid names at an equal slot weight each."""

    def __init__(self, top_n: int = 10, lot_size: int = 100) -> None:
        if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
            raise ValueError("top_n must be a positive integer")
        if not isinstance(lot_size, int) or isinstance(lot_size, bool) or lot_size < 1:
            raise ValueError("lot_size must be a positive integer")
        self.top_n = top_n
        self.lot_size = lot_size

    def build(
        self,
        factors: FactorResult,
        signal_prices: pd.DataFrame,
        capital: float,
    ) -> PortfolioTarget:
        """Build one signal date's target portfolio from factor ranks.

        ``factors`` must contain rows for exactly one ``trade_date`` (the
        signal day); ``signal_prices`` is a symbol-keyed map of that day's
        unadjusted closes (one row per symbol, duplicates rejected so a later
        close can never leak in); ``capital`` is the initial/scenario equity
        used to size positions.
        """
        self._assert_single_signal_date(factors)
        if not isinstance(capital, (int, float)) or isinstance(capital, bool):
            raise TypeError(
                f"capital must be a number, got {type(capital).__name__}"
            )
        capital = float(capital)
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("capital must be a positive finite number")

        prices = self._signal_close_map(signal_prices)
        frame = factors.frame
        valid = frame[frame["is_valid"].fillna(False).astype(bool)]
        ranked = valid.sort_values(
            ["processed_value", "symbol"], ascending=[False, True], kind="stable"
        )
        slot_weight = 1.0 / self.top_n
        reason = f"top_{self.top_n}_equal_weight"

        records: list[dict] = []
        for rank, row in enumerate(
            ranked.head(self.top_n).itertuples(index=False), start=1
        ):
            close = prices.get(row.symbol)
            if close is None:
                continue  # no usable signal-day close -> keep the slot as cash
            lots = math.floor(slot_weight * capital / close / self.lot_size)
            quantity = int(lots) * self.lot_size
            if quantity < self.lot_size:
                continue  # cannot afford one lot -> keep the slot as cash
            records.append(
                {
                    "trade_date": row.trade_date,
                    "symbol": row.symbol,
                    "rank": rank,
                    "signal_price": close,
                    "target_weight": slot_weight,
                    "target_quantity": quantity,
                    "selection_reason": reason,
                }
            )

        target_frame = pd.DataFrame(records, columns=list(PORTFOLIO_TARGET_COLUMNS))
        return PortfolioTarget(
            frame=target_frame,
            unallocated_weight=1.0 - len(target_frame) * slot_weight,
        )

    @staticmethod
    def _assert_single_signal_date(factors: FactorResult) -> None:
        frame = factors.frame
        if frame.empty:
            raise ValueError(
                "factor result must contain rows for exactly one signal "
                "trade_date, got none"
            )
        signal_dates = frame["trade_date"].nunique()
        if signal_dates != 1:
            raise ValueError(
                "portfolio target needs exactly one signal trade_date, "
                f"got {signal_dates}"
            )

    @staticmethod
    def _signal_close_map(signal_prices: pd.DataFrame) -> dict[str, float]:
        if not isinstance(signal_prices, pd.DataFrame):
            raise TypeError(
                "signal_prices must be a pandas DataFrame, got "
                f"{type(signal_prices).__name__}"
            )
        missing = {"symbol", "close"} - set(signal_prices.columns)
        if missing:
            raise ValueError(
                "signal_prices must include columns symbol and close; missing "
                + ", ".join(sorted(missing))
            )
        if signal_prices["symbol"].duplicated().any():
            raise ValueError(
                "signal_prices must carry one row per symbol: pass the "
                "signal-day unadjusted close map, never a panel of several "
                "days, so a later close can never leak in"
            )
        prices: dict[str, float] = {}
        for symbol, close in zip(signal_prices["symbol"], signal_prices["close"]):
            value = float(close)
            if math.isfinite(value) and value > 0.0:
                prices[str(symbol)] = value
        return prices
