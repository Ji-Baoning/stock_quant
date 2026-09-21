"""The exchange's own account of an ex-date, as a conflict arbiter (ADR-013).

CNINFO and Eastmoney occasionally state different terms for one
``(symbol, ex_date)`` and neither outweighs the other, so the row stays
quarantined until a human rules on it.  This module adds the fourth evidence
channel: the ex-rights reference price the exchange published for that day
(tushare's ``pre_close``), which is non-circular -- it does not pass through
this project's own booked actions the way ``adjusted_bar``'s
``adjustment_factor`` does, so it can judge a conflict our data has no opinion
about.

The limit that matters most, recorded here because the ADR rests on it: the
reference price shares the issuer's announcement as its origin with CNINFO, so
it is an **independent verification path** but not an independent third opinion
the way TDX is.  All 16 settlements measured on the ``e732b191`` corpus point
at CNINFO; that direction distribution is evidence of the channel's shape and
must stay auditable.

Nothing here imports ``stock_quant.data_pipeline``: the pipeline imports the
source adapters, so that direction would be a cycle.  The channel reports
failures and settlements through callables the pipeline injects.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from stock_quant.data_model.corporate_actions import (
    CONFLICT_SIDE_CNINFO,
    CONFLICT_SIDE_EASTMONEY,
    ConflictTerms,
)

#: The arbiter's ``name``; ``confirmed_by`` reads ``<side>+price_observed``.
ARBITER_NAME = "price_observed"

#: Winner tolerance, in ticks: how far one side's expected factor may sit from
#: the observation and still count as consistent with it.  Two ticks absorb the
#: double rounding of a 2-decimal previous close and a 2-decimal reference
#: price.
WINNER_TOLERANCE_TICKS = 2.0

#: Loser bar, in ticks: how far the other side must sit before it counts as
#: distinguishable at all.  Deliberately independent of the tolerance above --
#: one shared scale over-relaxes low-priced symbols (4.96-yuan 601828 reads as
#: indistinguishable at ``tol * 4``) where the split thresholds settle it.
LOSER_BAR_TICKS = 3.0

#: The quote's smallest increment, in yuan.
_TICK_YUAN = 0.01

#: Two expected factors closer than this are the same number, not two sides.
_SAME_EXPECTED = 1e-12

#: Per-ten to per-share.
_PER_TEN = 10.0

#: One key's price evidence, as the channel produces it.
Observe = Callable[[str, date], "PriceObservation | None"]


@dataclass(frozen=True)
class PriceObservation:
    """One ex-date's price evidence, as the exchange states it.

    ``prev_close`` is the close of the open day immediately preceding
    ``ex_date`` (``prev_close_date`` names it for audit); ``pre_close`` is the
    reference price the exchange published *for* ``ex_date``.

    ``snapshot_sha256`` is the content hash of the stored raw response this was
    read from, so a settlement is recomputable from bytes (spec D3).  It is
    ``None`` when an observation is authored without a snapshot, as the rule's
    own tests do.
    """

    prev_close: float
    prev_close_date: date
    pre_close: float
    snapshot_sha256: str | None = None

    @property
    def observed(self) -> float:
        """The event's price factor, as the market applied it."""
        return self.pre_close / self.prev_close


@dataclass(frozen=True)
class Settlement:
    """A distinguishable settlement, with the numbers that produced it."""

    side: str
    observed: float
    tick: float
    prev_close: float
    prev_close_date: date
    pre_close: float
    cninfo_expected: float
    eastmoney_expected: float
    cninfo_ticks: float
    eastmoney_ticks: float

    def to_details(self) -> dict[str, object]:
        """The auditable numbers, as issue-detail values (spec D3)."""
        return {
            "side": self.side,
            "prev_close": self.prev_close,
            "prev_close_date": self.prev_close_date.isoformat(),
            "pre_close": self.pre_close,
            "observed_factor": self.observed,
            "tick": self.tick,
            "cninfo_expected": self.cninfo_expected,
            "eastmoney_expected": self.eastmoney_expected,
            "cninfo_ticks": self.cninfo_ticks,
            "eastmoney_ticks": self.eastmoney_ticks,
        }


def expected_factor(terms: ConflictTerms, prev_close: float) -> float:
    """The ex-date price factor one side's terms imply.

    ``(prev_close - cash) / (prev_close * (1 + bonus + cap))`` with the ratios
    at per-share scale.  No subscription term appears because neither the
    corpus this rule was calibrated against nor the conflicts it targets carry
    one; a side with rights terms is refused before this is reached.
    """
    cash = float(terms.cash_per_ten) / _PER_TEN
    bonus = float(terms.bonus_per_ten) / _PER_TEN
    capitalization = float(terms.capitalization_per_ten) / _PER_TEN
    return (prev_close - cash) / (prev_close * (1.0 + bonus + capitalization))


def settle(
    cninfo: ConflictTerms,
    eastmoney: ConflictTerms,
    observation: PriceObservation,
) -> Settlement | None:
    """Name the side the reference price corroborates, or ``None``.

    ``None`` is the answer for every indeterminate shape: an unmodellable side
    (subscription terms), two sides implying the same factor, a winner that
    does not match the observation, and a loser too close to it to be told
    apart (spec D5).
    """
    if (
        cninfo.rights_per_ten
        or eastmoney.rights_per_ten
        or cninfo.rights_price_per_share
        or eastmoney.rights_price_per_share
    ):
        return None
    prev_close = observation.prev_close
    observed = observation.observed
    cninfo_expected = expected_factor(cninfo, prev_close)
    eastmoney_expected = expected_factor(eastmoney, prev_close)
    if abs(cninfo_expected - eastmoney_expected) < _SAME_EXPECTED:
        return None
    tick = _TICK_YUAN / prev_close
    cninfo_ticks = abs(observed - cninfo_expected) / tick
    eastmoney_ticks = abs(observed - eastmoney_expected) / tick
    side = _winner(cninfo_ticks, eastmoney_ticks)
    if side is None:
        return None
    return Settlement(
        side=side,
        observed=observed,
        tick=tick,
        prev_close=prev_close,
        prev_close_date=observation.prev_close_date,
        pre_close=observation.pre_close,
        cninfo_expected=cninfo_expected,
        eastmoney_expected=eastmoney_expected,
        cninfo_ticks=cninfo_ticks,
        eastmoney_ticks=eastmoney_ticks,
    )


def _winner(cninfo_ticks: float, eastmoney_ticks: float) -> str | None:
    """The one side both thresholds admit, or ``None`` when they do not.

    Exactly one side may sit within the winner tolerance, and the other must
    clear the loser bar.  Both-clear and neither-clear get the same answer:
    this rule does not pick the nearer of two candidates.
    """
    cn_wins = (
        cninfo_ticks <= WINNER_TOLERANCE_TICKS and eastmoney_ticks > LOSER_BAR_TICKS
    )
    em_wins = (
        eastmoney_ticks <= WINNER_TOLERANCE_TICKS and cninfo_ticks > LOSER_BAR_TICKS
    )
    if cn_wins == em_wins:
        return None
    return CONFLICT_SIDE_CNINFO if cn_wins else CONFLICT_SIDE_EASTMONEY


class PriceObservedArbiter:
    """Names a side from the exchange's ex-rights reference price.

    ``observe`` is the channel that produces the price evidence; ``None`` from
    it means the channel has no observation for that key and the conflict stays
    quarantined -- the fail-closed direction every absent channel takes.
    """

    name = ARBITER_NAME

    def __init__(self, observe: Observe) -> None:
        self._observe = observe

    def arbitrate(self, cninfo: ConflictTerms, eastmoney: ConflictTerms) -> str | None:
        observation = self._observe(cninfo.symbol, cninfo.ex_date)
        if observation is None:
            return None
        settlement = settle(cninfo, eastmoney, observation)
        return None if settlement is None else settlement.side
