"""Replay the price-observation rule over the 25 published conflicts.

Reads the immutable ``e732b191`` version's quarantine table and the tushare
``daily`` raw snapshots already on disk and prints what the rule settles.  No
network, no writes: the verdict is a measurement, and the dated operations
record beside it is its evidence.

One conflict is classified in the order ADR-014 fixes -- every channel is asked
first, and the float32 representation floor is consulted only for a pair no
channel names -- so a settlement is never pre-empted by the floor.  The pair
comparison is the production one (``_within_representation_floor`` over every
ratio field, with an exactly equal record date), not a copy of its constant.

Run from the repository root:

    PYTHONPATH=src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python \
        project/replay_price_arbitration.py
"""

from __future__ import annotations

import glob
from datetime import date
from decimal import Decimal

import pandas as pd

from stock_quant.data_model.corporate_actions import (
    ConflictTerms,
    _within_representation_floor,
)
from stock_quant.data_sources.price_observed import (
    PriceObservation,
    expected_factor,
    settle,
)

VERSION = "e732b19177bda1938d4ac6008ee4f5be131a5f40ebc04765cda2186e5837cf05"
DATASET = f"project/data/standardized/{VERSION}"
SNAPSHOTS = "project/data/raw/tushare/daily/*/*/*/data.parquet"
_TEN = Decimal("10")

#: The five ratio fields ADR-014's floor is applied to, as the published
#: quarantine columns state them (per share).
_RATIO_COLUMNS = (
    "cash_dividend_per_share",
    "bonus_share_ratio",
    "capitalization_ratio",
    "rights_issue_ratio",
    "rights_issue_price",
)


def daily_prices() -> dict[str, pd.DataFrame]:
    """Every stored tushare daily row, one date-sorted frame per symbol."""
    frames = [pd.read_parquet(path) for path in sorted(glob.glob(SNAPSHOTS))]
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts_code", "trade_date"])
    merged = merged.sort_values(["ts_code", "trade_date"])
    return {code: frame for code, frame in merged.groupby("ts_code")}


def _decimal(value) -> Decimal:
    """A supplier cell as a Decimal, reading a missing term as zero."""
    if value is None or pd.isna(value):
        return Decimal("0")
    return Decimal(str(value))


def terms(row: dict, symbol: str, ex_date: date) -> ConflictTerms:
    """One quarantine row's terms, at the per-ten scale suppliers state."""
    return ConflictTerms(
        symbol=symbol,
        ex_date=ex_date,
        cash_per_ten=_decimal(row["cash_dividend_per_share"]) * _TEN,
        bonus_per_ten=_decimal(row["bonus_share_ratio"]) * _TEN,
        capitalization_per_ten=_decimal(row["capitalization_ratio"]) * _TEN,
        rights_per_ten=_decimal(row["rights_issue_ratio"]) * _TEN,
        rights_price_per_share=_decimal(row["rights_issue_price"]),
    )


def one_rendering(cninfo_row: dict, eastmoney_row: dict) -> bool:
    """Whether the two sides state one ratio inside float32's grid (ADR-014)."""
    if cninfo_row["record_date"] != eastmoney_row["record_date"]:
        return False
    return all(
        _within_representation_floor(
            _decimal(cninfo_row[column]), _decimal(eastmoney_row[column])
        )
        for column in _RATIO_COLUMNS
    )


def observation_for(frame, ex_date: date) -> PriceObservation | None:
    """The observation for ``ex_date`` from one symbol's stored bars."""
    if frame is None:
        return None
    dated = {
        date.fromisoformat(str(value)[:10]): row
        for value, row in zip(frame["trade_date"], frame.to_dict("records"))
    }
    if ex_date not in dated:
        return None
    earlier = sorted(day for day in dated if day < ex_date)
    if not earlier:
        return None
    prev_close_date = earlier[-1]
    return PriceObservation(
        prev_close=float(dated[prev_close_date]["close"]),
        prev_close_date=prev_close_date,
        pre_close=float(dated[ex_date]["pre_close"]),
    )


def classify(cninfo, eastmoney, cninfo_row, eastmoney_row, observation):
    """``(verdict, settlement)`` for one conflict, in ADR-014's order.

    A channel names a side first; only a pair no channel names is offered to
    the representation floor, and a pair that is one ratio in two renderings
    is folded there rather than counted against the rule.
    """
    if observation is not None:
        settlement = settle(cninfo, eastmoney, observation)
        if settlement is not None:
            return settlement.side, settlement
    if one_rendering(cninfo_row, eastmoney_row):
        return "merged (D4)", None
    return ("held", None) if observation is not None else ("no observation", None)


def report(symbol, ex_date, cninfo, eastmoney, observation, verdict) -> None:
    """One line per conflict, with both sides' tick deviations."""
    if observation is None:
        print(f"{symbol} {ex_date} no stored price -> {verdict}")
        return
    tick = 0.01 / observation.prev_close
    cn = expected_factor(cninfo, observation.prev_close)
    em = expected_factor(eastmoney, observation.prev_close)
    print(
        f"{symbol} {ex_date} observed={observation.observed:.6f} "
        f"cninfo={cn:.6f} ({abs(observation.observed - cn) / tick:.1f} tick) "
        f"eastmoney={em:.6f} ({abs(observation.observed - em) / tick:.1f} tick) "
        f"-> {verdict}"
    )


def main() -> None:
    quarantine = pd.read_parquet(f"{DATASET}/corporate_action_quarantine.parquet")
    conflicts = quarantine[quarantine["reason"] == "cross_source_conflict"]
    prices = daily_prices()
    totals: dict[str, int] = {}
    for (symbol, ex_date), group in conflicts.groupby(["symbol", "ex_date"]):
        rows = group.to_dict("records")
        if len(rows) != 2:
            verdict, settlement = "held", None
        else:
            cninfo, eastmoney = (terms(row, symbol, ex_date) for row in rows)
            cninfo_row, eastmoney_row = rows
            observation = observation_for(prices.get(symbol), ex_date)
            verdict, settlement = classify(
                cninfo, eastmoney, cninfo_row, eastmoney_row, observation
            )
            report(symbol, ex_date, cninfo, eastmoney, observation, verdict)
        totals[verdict] = totals.get(verdict, 0) + 1
    print(f"\nconflicts={len(conflicts) // 2} {totals}")


if __name__ == "__main__":
    main()
