# Price-Observed Conflict Arbitration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settle corporate-action cross-source conflicts mechanically from the exchange's own ex-rights reference price, so 16 of the 25 real conflicts stop needing a human, 2 stop being filed as conflicts at all, and the remaining 7 go to the human channel with their tick deviations stated.

**Architecture:** A new `PriceObservedArbiter` implements the existing `CorporateActionArbiter` protocol and sits behind TDX in a first-answering chain, so `_GuardedArbiter`'s two rails (a reviewed `(symbol, ex_date)` is never arbitrated; a raise degrades to no arbitration) are inherited rather than restated. It reads tushare's `pre_close` -- the exchange's ex-rights reference price -- through a lazy per-symbol channel shaped like `_LazyFactorChannel`, and proves the previous close sits on the open day the run's published calendar names immediately before the ex-date. `_same_facts` additionally collapses float32 round-trip noise so 2 pairs stop being quarantined.

**Tech Stack:** Python 3.12 (`sq312` conda env), pandas 3.0.6, numpy 2.5.3, pytest, pyarrow, Parquet.

**Spec:** `docs/superpowers/specs/2026-09-22-price-observed-conflict-arbitration-design.md`

## Global Constraints

- Fail-closed everywhere: any missing, unreadable, non-adjacent or ambiguous input answers "no arbitration", never a guess.
- `W = 2`, `L = 3`; `tick = 0.01 / prev_close`. The two thresholds stay independent -- never collapse them into a single `tol × safety` scale.
- float32 round-trip tolerance: relative `2 ** -23` (≈ 1.19e-7). No wider tolerance: the narrowest genuine disagreement in the corpus (600989.SH 2025-05-13, 1.66e-5) must stay quarantined.
- Do not change the `CorporateActionArbiter` protocol definition. Do not add a quarantine reason. Settlements label `<side>+price_observed`.
- `src/stock_quant/data_sources/price_observed.py` must not import `stock_quant.data_pipeline` -- the pipeline imports the source adapters, so that direction is a cycle. It talks to the run through injected callables.
- Do not edit `tests/unit/test_tdx_arbiter.py`, `project/configs/sources.yml`, or `templates/project-config/sources.yml`: unrelated work in progress. The price lane is gated on the **existing** `tushare` segment, so no config file changes at all.
- No production data in `tests/`: unit tests author frames inline; the corpus replay is an operations script under `project/`.
- Never commit credentials, tokens, or production outputs.
- Run pytest from the repo root with the documented interpreter:
  `PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest ...`
  (the checked-in `.venv` points at a Linux path and is broken).
- Do not run networked or publishing commands. The corpus replay reads snapshots already on disk.

---

### Task 1: float32 round-trip noise stops reading as a cross-source conflict (D4)

**Files:**
- Modify: `src/stock_quant/data_model/corporate_actions.py` (`_same_facts`, currently ~754-763; add `_same_ratio` beside it)
- Test: `tests/unit/test_corporate_action_normalize.py` (clean at HEAD; safe to extend)

**Interfaces:**
- Consumes: the existing `normalize_corporate_actions(cninfo, eastmoney)` two-frame call.
- Produces: `_FLOAT32_RELATIVE_EPSILON: Decimal` and `_same_ratio(left: Decimal, right: Decimal) -> bool`. `_same_facts(left, right) -> bool` keeps its signature; `record_date` keeps exact equality. Task 6's replay imports `_same_ratio` so it classifies the 2 float32 pairs by the production constant rather than a copy.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_corporate_action_normalize.py`. Helpers already there: `_cninfo_plan(*, cash_per_10, bonus_per_10, cap_per_10, progress, plan, record)` (all per ten shares) and `eastmoney_without_plan_column(per_share, *, symbol, progress, bonus_per_10, cap_per_10)` (cash per **share**, ratios per ten).

```python
def test_float32_round_trip_noise_is_not_a_cross_source_conflict():
    """A ratio pair differing by one float32 round trip is one event.

    300124.SZ's real conflict states capitalization 9.99878 per ten shares on
    one side and 9.998781 on the other: the same number after a float32 round
    trip.  Two quarantined rows for it spend an operator's review on a
    difference no supplier stated.
    """
    result = normalize_corporate_actions(
        _cninfo_plan(cash_per_10=4.99939, cap_per_10=9.99878),
        eastmoney_without_plan_column(per_share=0.499939, cap_per_10=9.998781),
    )

    assert result.quarantined.empty
    assert len(result.accepted) == 1
    assert result.accepted.iloc[0]["confirmed_by"] == "cninfo+eastmoney"


def test_a_genuine_ratio_disagreement_survives_the_tolerance():
    """600989.SH 2025-05-13 disagrees by 1.66e-5 -- two orders above float32.

    Widening the tolerance to reach it would be the machine deciding the
    difference does not matter, which is exactly the judgement this project
    keeps for its owner.
    """
    result = normalize_corporate_actions(
        _cninfo_plan(cash_per_10=4.10),
        eastmoney_without_plan_column(per_share=0.409993),
    )

    assert result.accepted.empty
    assert set(result.quarantined["reason"]) == {REASON_CROSS_SOURCE_CONFLICT}
```

If `REASON_CROSS_SOURCE_CONFLICT` is not already imported in that module, import it from `stock_quant.data_model.corporate_actions` -- **do not** replace it with the literal string.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_corporate_action_normalize.py -q -k "float32_round_trip or genuine_ratio_disagreement"`

Expected: `test_float32_round_trip_noise_is_not_a_cross_source_conflict` FAILS on `assert 2 == 0` (the frame is read as two conflicting rows). `test_a_genuine_ratio_disagreement_survives_the_tolerance` PASSES already -- it is the regression guard and must still pass after Step 3.

- [ ] **Step 3: Implement the tolerance**

In `src/stock_quant/data_model/corporate_actions.py`, add above `_same_facts`:

```python
#: float32's machine epsilon, ``2 ** -23`` (≈ 1.19e-7), written as a power so
#: it is exactly representable in ``Decimal`` and the comparison never leaves
#: decimal arithmetic.  Supplier ratios arrive as float32 and are re-emitted as
#: decimals, so two sides stating the same ratio can differ by one float32
#: round trip.  A relative tolerance of exactly this width collapses that
#: artifact and nothing wider: the narrowest genuine disagreement in the corpus
#: (600989.SH 2025-05-13) is 1.66e-5, two orders of magnitude larger.
_FLOAT32_RELATIVE_EPSILON = Decimal(2) ** -23


def _same_ratio(left: Decimal, right: Decimal) -> bool:
    """Whether two supplier ratios state the same number up to float32 noise."""
    if left == right:
        return True
    scale = max(abs(left), abs(right))
    if scale == 0:
        return False
    return abs(left - right) <= _FLOAT32_RELATIVE_EPSILON * scale
```

Then route the five ratio fields through it, leaving `record_date` exact:

```python
def _same_facts(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Economic facts equal: record date, distribution and subscription terms."""
    return (
        left["record_date"] == right["record_date"]
        and _same_ratio(_zeroed(left["cash"]), _zeroed(right["cash"]))
        and _same_ratio(_zeroed(left["bonus"]), _zeroed(right["bonus"]))
        and _same_ratio(
            _zeroed(left["capitalization"]), _zeroed(right["capitalization"])
        )
        and _same_ratio(_zeroed(left["rights"]), _zeroed(right["rights"]))
        and _same_ratio(
            _zeroed(left["rights_price"]), _zeroed(right["rights_price"])
        )
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_corporate_action_normalize.py tests/unit/test_corporate_action_coverage.py -q`

Expected: PASS, no failures.

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/data_model/corporate_actions.py tests/unit/test_corporate_action_normalize.py
git commit -m "fix(corporate-actions): collapse float32 round-trip noise in same-facts"
```

---

### Task 2: the settlement rule (D2, D5)

**Files:**
- Create: `src/stock_quant/data_sources/price_observed.py`
- Test: `tests/unit/test_price_observed_arbiter.py` (new file -- `tests/unit/test_tdx_arbiter.py` carries unrelated WIP)

**Interfaces:**
- Consumes: `ConflictTerms` from `stock_quant.data_model.corporate_actions` -- fields `symbol: str`, `ex_date: date`, `cash_per_ten`, `bonus_per_ten`, `capitalization_per_ten`, `rights_per_ten`, `rights_price_per_share`, all `Decimal`, all per ten shares.
- Produces:
  - `ARBITER_NAME = "price_observed"`, `WINNER_TOLERANCE_TICKS = 2.0`, `LOSER_BAR_TICKS = 3.0`
  - `@dataclass(frozen=True) PriceObservation(prev_close: float, prev_close_date: date, pre_close: float, snapshot_sha256: str | None = None)`, with property `observed -> float`
  - `@dataclass(frozen=True) Settlement` with fields `side, observed, tick, prev_close, prev_close_date, pre_close, cninfo_expected, eastmoney_expected, cninfo_ticks, eastmoney_ticks` and `to_details() -> dict[str, object]`
  - `expected_factor(terms: ConflictTerms, prev_close: float) -> float`
  - `settle(cninfo: ConflictTerms, eastmoney: ConflictTerms, observation: PriceObservation) -> Settlement | None`
  - `Observe = Callable[[str, date], PriceObservation | None]`
  - `class PriceObservedArbiter` with `name = ARBITER_NAME`, `__init__(self, observe: Observe)`, `arbitrate(self, cninfo, eastmoney) -> str | None`

**The synthetic numbers in these tests are chosen to be self-consistent, not to reproduce the corpus.** `prev_close = 10.0` makes `tick = 0.001` exactly, so tick deviations read at a glance. The real per-row figures are asserted by the Task 6 replay, where they belong.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_price_observed_arbiter.py`:

```python
"""The price-observation settlement rule (spec D2/D5).

Every test authors its observation inline: the rule is pure, the channel that
obtains observations is injected, and nothing here touches a network or a wall
clock.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from stock_quant.data_model.corporate_actions import ConflictTerms
from stock_quant.data_sources.price_observed import (
    PriceObservation,
    PriceObservedArbiter,
    expected_factor,
    settle,
)

_DAY = date(2023, 7, 17)
_PREV = date(2023, 7, 14)


def terms(
    *,
    symbol="600188.SH",
    cash_per_ten="0.5",
    bonus_per_ten="0.0",
    capitalization_per_ten="0.0",
    rights_per_ten="0.0",
    rights_price_per_share="0.0",
) -> ConflictTerms:
    """One side's stated terms, per ten shares as the suppliers state them."""
    return ConflictTerms(
        symbol=symbol,
        ex_date=_DAY,
        cash_per_ten=Decimal(cash_per_ten),
        bonus_per_ten=Decimal(bonus_per_ten),
        capitalization_per_ten=Decimal(capitalization_per_ten),
        rights_per_ten=Decimal(rights_per_ten),
        rights_price_per_share=Decimal(rights_price_per_share),
    )


def test_expected_factor_is_the_exchange_formula():
    """(prev_close - cash) / (prev_close * (1 + bonus + cap)), per share."""
    priced = terms(cash_per_ten="4.3", bonus_per_ten="5.0")

    assert expected_factor(priced, 30.0) == (30.0 - 0.43) / (30.0 * 1.5)


def test_settles_the_side_the_reference_price_corroborates():
    """cninfo implies 0.98, eastmoney 0.95, and the market applied 0.98.

    At prev_close 10.0 one tick is exactly 0.001, so cninfo sits 0 ticks from
    the observation and eastmoney 30 -- inside the winner tolerance, far
    outside it, and clear of the loser bar.
    """
    cninfo = terms(cash_per_ten="2.0")  # 0.20/share -> (10 - 0.2) / 10
    eastmoney = terms(cash_per_ten="5.0")  # 0.50/share -> (10 - 0.5) / 10
    observation = PriceObservation(
        prev_close=10.0, prev_close_date=_PREV, pre_close=9.80
    )

    settlement = settle(cninfo, eastmoney, observation)

    assert settlement is not None
    assert settlement.side == "cninfo"
    assert settlement.cninfo_ticks == 0.0
    assert settlement.eastmoney_ticks == 30.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'stock_quant.data_sources.price_observed'`.

- [ ] **Step 3: Write the rule**

Create `src/stock_quant/data_sources/price_observed.py`:

```python
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
        cninfo_ticks <= WINNER_TOLERANCE_TICKS
        and eastmoney_ticks > LOSER_BAR_TICKS
    )
    em_wins = (
        eastmoney_ticks <= WINNER_TOLERANCE_TICKS
        and cninfo_ticks > LOSER_BAR_TICKS
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

    def arbitrate(
        self, cninfo: ConflictTerms, eastmoney: ConflictTerms
    ) -> str | None:
        observation = self._observe(cninfo.symbol, cninfo.ex_date)
        if observation is None:
            return None
        settlement = settle(cninfo, eastmoney, observation)
        return None if settlement is None else settlement.side
```

`math`, `Iterable`, `timedelta`, `Any` and `pd` are used by Task 3; keep them -- it lands in the same file.

- [ ] **Step 4: Run the test to verify it passes**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py -q`

Expected: PASS (2 passed).

- [ ] **Step 5: Add the remaining branches of criterion 2**

Append to `tests/unit/test_price_observed_arbiter.py`:

```python
def test_two_sides_implying_one_factor_are_never_settled():
    """002269.SZ 2015-05-12: same total, different split.

    cninfo states 10送6转9 and eastmoney 10送5转10; both scale by 1.5, so no
    price factor can separate them.  This is the channel's principled ceiling,
    not an implementation gap.
    """
    cninfo = terms(
        symbol="002269.SZ",
        cash_per_ten="1.0",
        bonus_per_ten="6.0",
        capitalization_per_ten="9.0",
    )
    eastmoney = terms(
        symbol="002269.SZ",
        cash_per_ten="1.0",
        bonus_per_ten="5.0",
        capitalization_per_ten="10.0",
    )
    observation = PriceObservation(
        prev_close=10.0, prev_close_date=_PREV, pre_close=6.6
    )

    assert settle(cninfo, eastmoney, observation) is None


def test_a_loser_inside_the_bar_is_not_settled():
    """The near-miss form: the loser sits 1.4 ticks away, under the bar.

    Both sides lie close to the observation, so neither is distinguishable --
    the machine says so rather than picking the nearer one (spec D5).
    """
    cninfo = terms(cash_per_ten="0.84")  # expected 0.9958
    eastmoney = terms(cash_per_ten="0.70")  # expected 0.9965
    observation = PriceObservation(
        prev_close=20.0, prev_close_date=_PREV, pre_close=19.916
    )

    assert settle(cninfo, eastmoney, observation) is None


def test_a_winner_outside_the_tolerance_is_not_settled():
    """The far form: nobody matches the observation, so nobody is chosen."""
    cninfo = terms(cash_per_ten="0.84")
    eastmoney = terms(cash_per_ten="0.70")
    observation = PriceObservation(
        prev_close=20.0, prev_close_date=_PREV, pre_close=19.0
    )

    assert settle(cninfo, eastmoney, observation) is None


def test_an_absent_channel_settles_nothing():
    """The fail-closed answer, not a default side."""
    arbiter = PriceObservedArbiter(lambda symbol, ex_date: None)

    assert arbiter.arbitrate(terms(), terms(cash_per_ten="5.0")) is None


def test_a_side_with_subscription_terms_is_refused():
    """The calibrated formula models no 配股 term, so it judges none."""
    cninfo = terms(rights_per_ten="3.0", rights_price_per_share="1.0")

    settlement = settle(
        cninfo,
        terms(cash_per_ten="5.0"),
        PriceObservation(
            prev_close=10.0, prev_close_date=_PREV, pre_close=9.80
        ),
    )

    assert settlement is None
```

- [ ] **Step 6: Run the whole file and confirm every branch passes**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py -q`

Expected: PASS (7 passed).

- [ ] **Step 7: Commit**

```bash
git add src/stock_quant/data_sources/price_observed.py tests/unit/test_price_observed_arbiter.py
git commit -m "feat(data_sources): add the price-observation settlement rule"
```

---

### Task 3: the lazy price channel -- fetch, adjacency proof, raw snapshot (D3)

**Files:**
- Modify: `src/stock_quant/data_sources/price_observed.py` (append)
- Test: `tests/unit/test_price_observed_arbiter.py` (append)

**Interfaces:**
- Consumes: `PriceObservation` (Task 2); `FetchResult` from `stock_quant.data_sources.base` -- fields `source, endpoint, request_key, frame, metadata`, it carries **no** hash; `RawSnapshot` from `stock_quant.data_sources.raw_store` -- fields `path, sha256, manifest`.
- Produces:
  - `TUSHARE_SOURCE = "tushare"`, `DAILY_ENDPOINT = "daily"`, `LOOKBACK_DAYS = 30`
  - `class LazyDailyPriceChannel` with
    `__init__(self, fetch_daily: Callable[[str, date, date], FetchResult], open_days: Iterable[date], *, on_failure: Callable[[str, Exception], None], raw_snapshots: list, record_raw: Any)`
    and `observe(self, symbol: str, ex_date: date) -> PriceObservation | None`.

The snapshot hash travels **on the observation**: `record_raw(result)` returns a `RawSnapshot`, whose `sha256` is set into `PriceObservation.snapshot_sha256` before the observation is cached. That is how Task 4 records which bytes settled a conflict without either module reaching into the other's private state.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_price_observed_arbiter.py`:

```python
from datetime import timedelta

import pandas as pd

from stock_quant.data_sources.base import DataRequest, FetchResult, request_key
from stock_quant.data_sources.price_observed import (
    DAILY_ENDPOINT,
    TUSHARE_SOURCE,
    LazyDailyPriceChannel,
)


def daily_frame(symbol: str, rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """One tushare ``daily`` response: (trade_date, close, pre_close) rows."""
    return pd.DataFrame(
        [
            {
                "ts_code": symbol,
                "trade_date": day,
                "close": close,
                "pre_close": pre_close,
            }
            for day, close, pre_close in rows
        ]
    )


def channel_with(rows, *, open_days, failures=None, snapshots=None, recorded=None):
    """A channel over one stubbed response, with no network and no clock."""
    def fetch_daily(symbol, start, end):
        request = DataRequest(DAILY_ENDPOINT, (symbol,), start, end)
        return FetchResult(
            source=TUSHARE_SOURCE,
            endpoint=DAILY_ENDPOINT,
            request_key=request_key(request),
            frame=daily_frame(symbol, rows),
            metadata={"transport_id": TUSHARE_SOURCE},
        )

    target = failures if failures is not None else []
    return LazyDailyPriceChannel(
        fetch_daily,
        open_days,
        on_failure=lambda symbol, error: target.append((symbol, error)),
        raw_snapshots=[] if snapshots is None else snapshots,
        record_raw=recorded,
    )


def test_observes_the_reference_price_when_the_prior_day_is_adjacent():
    """The happy path: the row before the ex-date is the prior open day."""
    channel = channel_with(
        [("2023-07-14", 7.18, 7.20), ("2023-07-17", 4.60, 4.17828)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
    )

    observation = channel.observe("600188.SH", date(2023, 7, 17))

    assert observation is not None
    assert observation.prev_close_date == date(2023, 7, 14)
    assert observation.prev_close == 7.18
    assert observation.pre_close == 4.17828


def test_a_suspension_spanning_the_ex_date_settles_nothing():
    """Spec §6 leaves this form unverified, so it must fail closed.

    The frame's row before the ex-date is 2023-07-05, but the published
    calendar says 2023-07-14 was open: the symbol did not trade normally into
    its ex-date, where ``pre_close``'s meaning is unconfirmed.
    """
    channel = channel_with(
        [("2023-07-05", 7.18, 7.20), ("2023-07-17", 4.60, 4.17828)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
    )

    assert channel.observe("600188.SH", date(2023, 7, 17)) is None


def test_a_channel_failure_settles_nothing_and_is_reported():
    """A raise degrades to an absent channel, never to a guessed side."""
    failures = []

    def fetch_daily(symbol, start, end):
        raise RuntimeError("supplier unreachable")

    channel = LazyDailyPriceChannel(
        fetch_daily,
        (date(2023, 7, 14), date(2023, 7, 17)),
        on_failure=lambda symbol, error: failures.append((symbol, error)),
        raw_snapshots=[],
        record_raw=None,
    )

    assert channel.observe("600188.SH", date(2023, 7, 17)) is None
    assert [symbol for symbol, _ in failures] == ["600188.SH"]
    assert isinstance(failures[0][1], RuntimeError)


def test_a_symbol_that_failed_is_not_fetched_again():
    """A dead lane is not retried within the run."""
    attempts = []

    def fetch_daily(symbol, start, end):
        attempts.append(symbol)
        raise RuntimeError("supplier unreachable")

    channel = LazyDailyPriceChannel(
        fetch_daily,
        (date(2023, 7, 14), date(2023, 7, 17)),
        on_failure=lambda symbol, error: None,
        raw_snapshots=[],
        record_raw=None,
    )

    channel.observe("600188.SH", date(2023, 7, 17))
    channel.observe("600188.SH", date(2023, 7, 17))

    assert attempts == ["600188.SH"]


def test_the_observation_carries_the_snapshot_it_was_read_from():
    """What settled a conflict must be the bytes the snapshot holds (D3)."""
    snapshots = []
    recorded = []

    def record_raw(result):
        recorded.append(result)
        return type("S", (), {"sha256": "deadbeef"})()

    channel = channel_with(
        [("2023-07-14", 7.18, 7.20), ("2023-07-17", 4.60, 4.17828)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
        snapshots=snapshots,
        recorded=record_raw,
    )

    observation = channel.observe("600188.SH", date(2023, 7, 17))

    assert observation.snapshot_sha256 == "deadbeef"
    assert len(recorded) == 1
    assert recorded[0].endpoint == DAILY_ENDPOINT
    assert recorded[0].request_key == request_key(
        DataRequest(
            DAILY_ENDPOINT,
            ("600188.SH",),
            date(2023, 7, 17) - timedelta(days=30),
            date(2023, 7, 17),
        )
    )
    assert snapshots == recorded
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py -q -k "observes_the_reference or suspension_spanning or channel_failure or failed_is_not_fetched or carries_the_snapshot"`

Expected: FAIL with `ImportError: cannot import name 'LazyDailyPriceChannel'`.

- [ ] **Step 3: Write the channel**

Append to `src/stock_quant/data_sources/price_observed.py`:

```python
#: The window asked of the daily lane: wide enough for a normal week's
#: holidays, narrow enough that a longer gap means the symbol did not trade
#: normally into its ex-date -- in which case the adjacency check refuses it.
LOOKBACK_DAYS = 30

TUSHARE_SOURCE = "tushare"
DAILY_ENDPOINT = "daily"

_DATE_COLUMN = "trade_date"
_CLOSE_COLUMN = "close"
_PRE_CLOSE_COLUMN = "pre_close"


class LazyDailyPriceChannel:
    """The ex-rights reference price for one ``(symbol, ex_date)``, on demand.

    Consulted only for a conflicted key, fetched once per symbol and cached;
    every response goes through the same content-addressed raw store as every
    supplier, so what settled a conflict is byte-for-byte what the snapshot
    holds.  Anything the lane raises degrades to an absent channel -- it
    asserts nothing, the fail-closed direction -- is reported to ``on_failure``
    and is not retried within the run.

    Adjacency is proven, not assumed: the row before the ex-date must sit on
    the open day the published calendar says immediately precedes it.  A symbol
    suspended into its ex-date therefore settles nothing, which is the form the
    spec deliberately leaves unverified.
    """

    def __init__(
        self,
        fetch_daily: Callable[[str, date, date], FetchResult],
        open_days: Iterable[date],
        *,
        on_failure: Callable[[str, Exception], None],
        raw_snapshots: list[Any],
        record_raw: Any,
    ) -> None:
        self._fetch_daily = fetch_daily
        self._previous_open_day = _previous_open_days(open_days)
        self._on_failure = on_failure
        self._raw_snapshots = raw_snapshots
        self._record_raw = record_raw
        self._observations: dict[tuple[str, date], PriceObservation | None] = {}
        self._failed: set[str] = set()

    def observe(self, symbol: str, ex_date: date) -> PriceObservation | None:
        key = (symbol, ex_date)
        if key in self._observations:
            return self._observations[key]
        if symbol in self._failed:
            return None
        try:
            start = ex_date - timedelta(days=LOOKBACK_DAYS)
            result = self._fetch_daily(symbol, start, ex_date)
        except Exception as error:  # noqa: BLE001 - best-effort evidence channel
            self._on_failure(symbol, error)
            self._failed.add(symbol)
            return None
        snapshot_sha256 = None
        if result.frame is not None and not result.frame.empty:
            snapshot = self._record_raw(result)
            self._raw_snapshots.append(snapshot)
            snapshot_sha256 = snapshot.sha256
        observation = _read_observation(
            result.frame,
            ex_date,
            self._previous_open_day.get(ex_date),
            snapshot_sha256,
        )
        self._observations[key] = observation
        return observation


def _previous_open_days(open_days: Iterable[date]) -> dict[date, date]:
    """Map each open day to the open day immediately before it."""
    ordered = sorted(set(open_days))
    return {day: previous for previous, day in zip(ordered, ordered[1:])}


def _read_observation(
    frame: pd.DataFrame | None,
    ex_date: date,
    prior_open_day: date | None,
    snapshot_sha256: str | None,
) -> PriceObservation | None:
    """The observation ``frame`` supports, or ``None`` when it supports none.

    Every refusal is one of the fail-closed shapes: the calendar names no open
    day before this ex-date, the frame holds no row on the ex-date, no usable
    row before it, or a row that is not the open day immediately preceding --
    the suspension form.
    """
    if prior_open_day is None or frame is None or frame.empty:
        return None
    by_date: dict[date, dict[str, Any]] = {}
    for record in frame.to_dict("records"):
        day = _as_date(record.get(_DATE_COLUMN))
        if day is not None:
            by_date[day] = record
    ex_row = by_date.get(ex_date)
    if ex_row is None:
        return None
    earlier = [day for day in by_date if day < ex_date]
    if not earlier:
        return None
    prev_close_date = max(earlier)
    if prev_close_date != prior_open_day:
        return None
    prev_close = _as_float(by_date[prev_close_date].get(_CLOSE_COLUMN))
    pre_close = _as_float(ex_row.get(_PRE_CLOSE_COLUMN))
    if prev_close is None or pre_close is None or prev_close <= 0.0:
        return None
    return PriceObservation(
        prev_close=prev_close,
        prev_close_date=prev_close_date,
        pre_close=pre_close,
        snapshot_sha256=snapshot_sha256,
    )


def _as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py -q`

Expected: PASS (12 passed).

- [ ] **Step 5: Confirm the module has no pipeline dependency**

Run:
`grep -n "data_pipeline" src/stock_quant/data_sources/price_observed.py`

Expected: no output. Then:

`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -c "import stock_quant.data_sources.price_observed as m; print(m.__name__)"`

- [ ] **Step 6: Commit**

```bash
git add src/stock_quant/data_sources/price_observed.py tests/unit/test_price_observed_arbiter.py
git commit -m "feat(data_sources): read the reference price through a lazy daily channel"
```

---

### Task 4: every settlement leaves its numbers behind (D3)

**Files:**
- Modify: `src/stock_quant/data_sources/price_observed.py` (extend `PriceObservedArbiter`)
- Test: `tests/unit/test_price_observed_arbiter.py` (append)

**Interfaces:**
- Consumes: `Settlement.to_details()` and `PriceObservation.snapshot_sha256` (Tasks 2-3).
- Produces: `Record = Callable[[ConflictTerms, ConflictTerms, Settlement, PriceObservation], None]` and `PriceObservedArbiter(observe, record=None)` -- when a settlement is reached and `record` is not `None`, it is called with the four values that produced the decision.

The callback is the seam that keeps this module free of pipeline imports: Task 5 supplies a `record` that builds the real `QualityIssue`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_price_observed_arbiter.py`:

```python
def settled_channel(*, recorded=None):
    """A channel whose observation is the ground truth for cninfo at 0.20."""

    def record_raw(result):
        return type("S", (), {"sha256": "abc123"})()

    return channel_with(
        [("2023-07-14", 10.0, 10.1), ("2023-07-17", 9.80, 9.80)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
        recorded=record_raw if recorded is None else recorded,
    )


def test_a_settlement_reports_the_numbers_and_the_snapshot():
    """Spec D3: the decision must be recomputable from stored bytes.

    The trace carries the arithmetic and the hash of the raw response the
    reference price was read from.
    """
    traces = []
    channel = settled_channel()
    cninfo = terms(cash_per_ten="2.0")
    eastmoney = terms(cash_per_ten="5.0")

    arbiter = PriceObservedArbiter(
        channel.observe,
        record=lambda cn, em, settlement, observation: traces.append(
            (cn, em, settlement, observation)
        ),
    )
    side = arbiter.arbitrate(cninfo, eastmoney)

    assert side == "cninfo"
    assert len(traces) == 1
    got_cninfo, got_eastmoney, settlement, observation = traces[0]
    assert got_cninfo == cninfo
    assert got_eastmoney == eastmoney
    assert observation.snapshot_sha256 == "abc123"
    details = settlement.to_details()
    assert details["side"] == "cninfo"
    assert details["prev_close"] == 10.0
    assert details["pre_close"] == 9.80
    assert details["tick"] == 0.001
    assert details["cninfo_ticks"] == 0.0
    assert details["eastmoney_ticks"] == 30.0


def test_a_conflict_that_stays_quarantined_reports_nothing():
    """No settlement means no trace; the absence is the evidence."""
    traces = []
    channel = settled_channel()
    arbiter = PriceObservedArbiter(
        channel.observe, record=lambda *args: traces.append(args)
    )

    # 0.84 and 0.70 per ten leave the loser inside the bar at prev_close 10.0.
    assert arbiter.arbitrate(
        terms(cash_per_ten="0.84"), terms(cash_per_ten="0.70")
    ) is None
    assert traces == []


def test_an_arbiter_without_a_record_callback_still_settles():
    """The rule must not depend on the trace being wired."""
    channel = settled_channel()
    arbiter = PriceObservedArbiter(channel.observe)

    assert arbiter.arbitrate(
        terms(cash_per_ten="2.0"), terms(cash_per_ten="5.0")
    ) == "cninfo"
```

Every number above is fixed by the stubbed frame: `prev_close=10.0`, `pre_close=9.80`, so `observed = 0.98`, `tick = 0.001`, cninfo's factor is 0.98 and eastmoney's is 0.95. Read the actual ticks from the failure output before adjusting anything.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py -q -k "reports_the_numbers or quarantined_reports_nothing or without_a_record"`

Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'record'`.

- [ ] **Step 3: Implement the trace seam**

In `src/stock_quant/data_sources/price_observed.py`, add near `Observe`:

```python
#: How a settlement is reported to the run, when it wants to be told.
Record = Callable[
    [ConflictTerms, ConflictTerms, "Settlement", "PriceObservation"], None
]
```

and replace the arbiter's `__init__` and `arbitrate` with:

```python
    def __init__(self, observe: Observe, record: Record | None = None) -> None:
        self._observe = observe
        self._record = record

    def arbitrate(
        self, cninfo: ConflictTerms, eastmoney: ConflictTerms
    ) -> str | None:
        observation = self._observe(cninfo.symbol, cninfo.ex_date)
        if observation is None:
            return None
        settlement = settle(cninfo, eastmoney, observation)
        if settlement is None:
            return None
        if self._record is not None:
            self._record(cninfo, eastmoney, settlement, observation)
        return settlement.side
```

and extend its docstring with:

```
    ``record``, when given, receives every settlement with the values that
    produced it, so the run can leave the arithmetic and the source snapshot in
    its evidence (spec D3).  A non-settling key reports nothing: the absence is
    the evidence.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py -q`

Expected: PASS (15 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stock_quant/data_sources/price_observed.py tests/unit/test_price_observed_arbiter.py
git commit -m "feat(data_sources): report each settlement's numbers and snapshot"
```

---

### Task 5: compose the lanes and wire them into the update (D1)

**Files:**
- Modify: `src/stock_quant/data_pipeline.py` -- `_GuardedArbiter` (~3509), `_build_factor_channel`'s neighbour `_LazyFactorChannel` (~3447), `_build_action_arbiter` (~2249), `_refresh_corporate_actions` (~2093), and its call site (~989)
- Test: `tests/unit/test_price_observed_arbiter.py`. **Do not add tests to `tests/unit/test_tdx_arbiter.py`.**

**Interfaces:**
- Consumes: `PriceObservedArbiter`, `LazyDailyPriceChannel`, `ARBITER_NAME` (Tasks 2-4); the existing `_issue(severity, code, *, symbol=None, trade_date=None, table="data_update", details=None) -> QualityIssue` at `data_pipeline.py:3626`; `Severity` from `stock_quant.data_quality.models`; the existing `_warn_arbiter_failure(issues, symbol, error)`; and the tdx `ARBITER_NAME` from the module's own imports.
- Produces:
  - `class FirstAnsweringArbiter` with `__init__(self, arbiters, issues)` and `name` always holding the name of the arbiter that last answered
  - `_GuardedArbiter.name` becomes a read-through property
  - `CODE_PRICE_OBSERVED_SETTLEMENT = "price_observed_settlement"` and `_price_settlement_issue(cninfo, settlement, observation) -> QualityIssue`
  - `DataPipeline._build_price_channel(self, issues, raw_snapshots, open_days)` returning `(channel, report_settlement) | None`

**Read `data_pipeline.py:3509-3551` and the `_warn_arbiter_failure` definition before writing the tests.** Assert the failure code that helper actually emits and match `_GuardedArbiter`'s real `issues` parameter form; do not trust this plan's guess at either.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_price_observed_arbiter.py`:

```python
from stock_quant.data_pipeline import (
    CODE_PRICE_OBSERVED_SETTLEMENT,
    FirstAnsweringArbiter,
    _GuardedArbiter,
    _price_settlement_issue,
)


class _Fixed:
    """An arbiter that always answers the same way, and says who was asked."""

    def __init__(self, name, side, calls):
        self.name = name
        self._side = side
        self._calls = calls

    def arbitrate(self, cninfo, eastmoney):
        self._calls.append(self.name)
        return self._side


def test_the_chain_asks_each_lane_until_one_answers():
    """TDX answers where it can; the price lane where TDX refuses."""
    calls = []
    chain = FirstAnsweringArbiter(
        [_Fixed("tdx", None, calls), _Fixed("price_observed", "cninfo", calls)],
        issues=[],
    )

    assert chain.arbitrate(terms(), terms(cash_per_ten="5.0")) == "cninfo"
    assert calls == ["tdx", "price_observed"]


def test_the_chain_names_who_decided_not_who_was_asked_first():
    """``confirmed_by`` reads ``<side>+<authority>``, so the label must follow
    the answering lane."""
    calls = []
    chain = FirstAnsweringArbiter(
        [
            _Fixed("tdx", "eastmoney", calls),
            _Fixed("price_observed", "cninfo", calls),
        ],
        issues=[],
    )

    assert chain.arbitrate(terms(), terms(cash_per_ten="5.0")) == "eastmoney"
    assert calls == ["tdx"]
    assert chain.name == "tdx"


def test_the_guard_carries_the_answering_lane_name_through():
    """The wrapper must read its inner arbiter's name after the call, not copy
    it at construction."""
    calls = []
    guarded = _GuardedArbiter(
        FirstAnsweringArbiter(
            [
                _Fixed("tdx", None, calls),
                _Fixed("price_observed", "cninfo", calls),
            ],
            issues=[],
        ),
        issues=[],
    )

    assert guarded.arbitrate(terms(), terms(cash_per_ten="5.0")) == "cninfo"
    assert guarded.name == "price_observed"


def test_a_reviewed_key_is_still_never_arbitrated_by_the_new_lane():
    """ADR-007's rail applies to the price lane for free, and must."""
    calls = []
    guarded = _GuardedArbiter(
        FirstAnsweringArbiter(
            [
                _Fixed("tdx", None, calls),
                _Fixed("price_observed", "cninfo", calls),
            ],
            issues=[],
        ),
        issues=[],
        reviewed=frozenset({("600188.SH", _DAY)}),
    )

    assert guarded.arbitrate(terms(), terms(cash_per_ten="5.0")) is None
    assert calls == []


def test_a_raising_lane_degrades_to_the_next_one_and_is_reported():
    """A dead lane asserts nothing, so the lane behind it may still answer."""
    calls = []
    issues = []

    class _Raising:
        name = "tdx"

        def arbitrate(self, cninfo, eastmoney):
            calls.append("tdx")
            raise RuntimeError("channel down")

    chain = FirstAnsweringArbiter(
        [_Raising(), _Fixed("price_observed", "cninfo", calls)], issues=issues
    )

    assert chain.arbitrate(terms(), terms(cash_per_ten="5.0")) == "cninfo"
    assert calls == ["tdx", "price_observed"]
    assert [issue.code for issue in issues] == [<the code _warn_arbiter_failure emits>]


def test_a_settlement_puts_its_numbers_in_a_quality_issue():
    """Spec D3: what settled the conflict must be readable from the report."""
    cninfo = terms(cash_per_ten="2.0")
    observation = PriceObservation(
        prev_close=10.0,
        prev_close_date=_PREV,
        pre_close=9.80,
        snapshot_sha256="abc123",
    )
    settlement = settle(cninfo, terms(cash_per_ten="5.0"), observation)

    issue = _price_settlement_issue(cninfo, settlement, observation)

    assert issue.code == CODE_PRICE_OBSERVED_SETTLEMENT
    assert issue.symbol == "600188.SH"
    assert issue.trade_date == _DAY
    assert issue.details["prev_close"] == 10.0
    assert issue.details["pre_close"] == 9.80
    assert issue.details["snapshot_sha256"] == "abc123"
    assert issue.details["cninfo_ticks"] == 0.0
```

Replace `<the code _warn_arbiter_failure emits>` with the literal the helper uses once you have read it.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py -q -k "chain_asks or names_who_decided or carries_the_answering or reviewed_key_is_still or raising_lane_degrades or puts_its_numbers"`

Expected: FAIL with `ImportError: cannot import name 'FirstAnsweringArbiter'`.

- [ ] **Step 3: Implement the chain and the read-through name**

In `src/stock_quant/data_pipeline.py`, add beside `_GuardedArbiter`:

```python
class FirstAnsweringArbiter:
    """Ask each conflict arbiter in turn; the first to name a side books it.

    Order is policy, not preference: TDX is a genuinely independent third
    opinion (ADR-007) while the price lane is an independent verification path
    that shares the issuer's announcement as its origin with CNINFO (ADR-013),
    so the stronger evidence is asked first and only a refusal falls through.

    ``name`` always holds the name of the arbiter that last answered, so the
    ``<side>+<authority>`` label ``_arbitrated_event`` builds names who decided
    rather than who was asked first.  That is why ``_GuardedArbiter.name`` has
    to read through instead of copying at construction.

    A lane that raises is reported and skipped: it asserted nothing, which says
    nothing about the lanes behind it.
    """

    def __init__(self, arbiters, issues) -> None:
        self._arbiters = tuple(arbiters)
        if not self._arbiters:
            raise ValueError("an arbiter chain needs at least one arbiter")
        self._issues = issues
        self.name = self._arbiters[0].name

    def arbitrate(self, cninfo, eastmoney):
        for arbiter in self._arbiters:
            try:
                side = arbiter.arbitrate(cninfo, eastmoney)
            except Exception as error:  # noqa: BLE001 - best-effort opinion
                _warn_arbiter_failure(self._issues, cninfo.symbol, error)
                continue
            if side is not None:
                self.name = arbiter.name
                return side
        return None

    def frame_for(self, symbol: str):
        """The first inner arbiter holding a frame for ``symbol``.

        ADR-009's classification consults the same lazily fetched frames the
        arbitration uses, so the chain forwards the hook rather than hiding it.
        """
        for arbiter in self._arbiters:
            frame_for = getattr(arbiter, "frame_for", None)
            if frame_for is None:
                continue
            frame = frame_for(symbol)
            if frame is not None:
                return frame
        return None
```

Then change `_GuardedArbiter` so its name follows its inner arbiter:

```python
    @property
    def name(self):
        """The inner arbiter's current name.

        Read through rather than copied: a first-answering chain renames itself
        to whichever lane decided, and ``_arbitrated_event`` reads ``name``
        *after* ``arbitrate`` to build the ``<side>+<authority>`` label.
        """
        return self._arbiter.name
```

- [ ] **Step 4: Run the tests to verify the chain passes and TDX is untouched**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py tests/unit/test_tdx_arbiter.py -q`

Expected: PASS. `test_tdx_arbiter.py` is read-only here -- if it fails, the `_GuardedArbiter` change broke an existing contract; fix the change, do not edit that file.

- [ ] **Step 5: Add the settlement trace and the channel builder**

In `src/stock_quant/data_pipeline.py`, near `CODE_OPTIONAL_SOURCE_FAILURE`:

```python
CODE_PRICE_OBSERVED_SETTLEMENT = "price_observed_settlement"
```

beside `_issue`:

```python
def _price_settlement_issue(cninfo, settlement, observation) -> QualityIssue:
    """The INFO trace of one price-observed settlement (spec D3).

    The label names the winner; this names the arithmetic that chose it and the
    stored bytes the reference price came from, so a settlement can be
    recomputed from the raw snapshot rather than taken on trust.
    """
    return _issue(
        Severity.INFO,
        CODE_PRICE_OBSERVED_SETTLEMENT,
        symbol=cninfo.symbol,
        trade_date=cninfo.ex_date,
        details={
            **settlement.to_details(),
            "symbol": cninfo.symbol,
            "ex_date": cninfo.ex_date.isoformat(),
            "snapshot_sha256": observation.snapshot_sha256,
        },
    )
```

and beside `_build_factor_channel`:

```python
    def _build_price_channel(self, issues, raw_snapshots, open_days):
        """The ADR-013 reference-price lane, or ``None`` when it is off.

        Gated on the existing ``tushare`` segment rather than one of its own:
        the lane reads that source's ``daily`` endpoint and adds no name to the
        configured sources.  Its responses go through ``self._source`` so a
        test override replaces them exactly as it replaces the source's bars.

        Returns the channel with the reporter that turns a settlement into the
        run's own quality issue -- the issue vocabulary lives in this module,
        and the arbiter must not import it.
        """

        def report_failure(symbol, error):
            issues.append(
                _issue(
                    Severity.WARNING,
                    CODE_OPTIONAL_SOURCE_FAILURE,
                    details={
                        "source": TUSHARE_SOURCE,
                        "endpoint": DAILY_ENDPOINT,
                        "symbol": symbol,
                        "message": str(error),
                    },
                )
            )

        def report_settlement(cninfo, eastmoney, settlement, observation):
            issues.append(_price_settlement_issue(cninfo, settlement, observation))

        config = self._project_config.sources.get(TUSHARE_SOURCE)
        if config is None or not config.enabled:
            return None

        def fetch_daily(symbol, start, ex_date):
            return self._source(TUSHARE_SOURCE).fetch(
                DataRequest(DAILY_ENDPOINT, (symbol,), start, ex_date)
            )

        return (
            LazyDailyPriceChannel(
                fetch_daily,
                open_days,
                on_failure=report_failure,
                raw_snapshots=raw_snapshots,
                record_raw=self._record_raw,
            ),
            report_settlement,
        )
```

Read the existing `_build_factor_channel` first and match how it reads `self._project_config.sources` and the enabled flag -- if the accessor differs, follow it.

- [ ] **Step 6: Wire the lane into the arbiter builder**

In `_build_action_arbiter(self, symbols, start, end, issues, raw_snapshots, open_days)`, keep the existing TDX `lazy` construction and its config gate, then build the chain:

```python
        arbiters = [lazy]
        price = self._build_price_channel(issues, raw_snapshots, open_days)
        if price is not None:
            channel, report_settlement = price
            arbiters.append(
                PriceObservedArbiter(channel.observe, record=report_settlement)
            )
        return _GuardedArbiter(
            FirstAnsweringArbiter(arbiters, issues), issues, reviewed
        )
```

Then thread `open_days` in:

1. `_refresh_corporate_actions(...)` gains `open_days` as its last parameter.
2. Its single call site (currently ~989) passes `calendar_open` -- the same value the sibling calls at ~935 and ~1037 already receive. **Read those two call sites first**; if the value is bound to a different local name, follow that.
3. Add the imports at the top of `data_pipeline.py`:

```python
from stock_quant.data_sources.price_observed import (
    DAILY_ENDPOINT,
    TUSHARE_SOURCE,
    LazyDailyPriceChannel,
    PriceObservedArbiter,
)
```

- [ ] **Step 7: Verify the wiring imports and the suite is not worse**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -c "from stock_quant.data_pipeline import DataPipeline; print('ok')"`

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_price_observed_arbiter.py tests/unit/test_corporate_action_normalize.py tests/unit/test_tdx_arbiter.py -q`

Expected: PASS.

Known baseline, not a regression: `tests/integration/test_data_pipeline.py` has 4 tests red since commit `c313e2a` (carried-coverage chaining), and `tests/unit/test_normalize.py` / `tests/unit/test_raw_store.py` have 2 red on pandas 3.0 dtype text. Do not fix them here; they belong to other work.

- [ ] **Step 8: Commit**

```bash
git add src/stock_quant/data_pipeline.py tests/unit/test_price_observed_arbiter.py
git commit -m "feat(pipeline): chain the price lane behind TDX for conflicted keys"
```

---

### Task 6: replay the corpus and record what the rule settles (criterion 3)

**Files:**
- Create: `project/replay_price_arbitration.py`
- Create: `docs/operations/2026-09-22-price-arbitration-replay.md`
- Test: none -- this is an operations script over production snapshots, which must not enter `tests/`

**Interfaces:**
- Consumes: `expected_factor`, `settle`, `PriceObservation` from `stock_quant.data_sources.price_observed`; `_same_ratio` from `stock_quant.data_model.corporate_actions` (so the 2 float32 pairs are classified by the production constant, not a copy); the published dataset `e732b19177bda1938d4ac6008ee4f5be131a5f40ebc04765cda2186e5837cf05`.
- Produces: one line per conflict with both sides' tick deviations, plus a totals line.

The published version predates Task 1, so its quarantine table still holds all 50 conflict rows -- including the 2 float32 pairs. The replay classifies in this order: float32-equal sides count as **merged**; else a settlement counts to its **side**; else **held**. That is what yields 16 / 2 / 7.

- [ ] **Step 1: Write the replay script**

Create `project/replay_price_arbitration.py`:

```python
"""Replay the price-observation rule over the 25 published conflicts.

Reads the immutable ``e732b191`` version's quarantine table and the tushare
``daily`` raw snapshots already on disk and prints what the rule settles.  No
network, no writes: the verdict is a measurement, and the dated operations
record beside it is its evidence.

Run from the repository root:

    PYTHONPATH=src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python \
        project/replay_price_arbitration.py
"""

from __future__ import annotations

import glob
from datetime import date
from decimal import Decimal

import pandas as pd

from stock_quant.data_model.corporate_actions import ConflictTerms, _same_ratio
from stock_quant.data_sources.price_observed import (
    PriceObservation,
    expected_factor,
    settle,
)

VERSION = "e732b19177bda1938d4ac6008ee4f5be131a5f40ebc04765cda2186e5837cf05"
DATASET = f"project/data/standardized/{VERSION}"
SNAPSHOTS = "project/data/raw/tushare/daily/*/*/*/data.parquet"
_TEN = Decimal("10")


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


def classify(cninfo, eastmoney, observation):
    """``(verdict, settlement)`` for one conflict.

    A float32-equal pair is folded before it is ever called a conflict, so it
    is reported as such rather than counted against the rule.
    """
    if observation is None:
        return "no observation", None
    if (
        _same_ratio(cninfo.cash_per_ten, eastmoney.cash_per_ten)
        and _same_ratio(cninfo.bonus_per_ten, eastmoney.bonus_per_ten)
        and _same_ratio(
            cninfo.capitalization_per_ten, eastmoney.capitalization_per_ten
        )
    ):
        return "merged (D4)", None
    settlement = settle(cninfo, eastmoney, observation)
    return ("held", None) if settlement is None else (settlement.side, settlement)


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
            observation = observation_for(prices.get(symbol), ex_date)
            verdict, settlement = classify(cninfo, eastmoney, observation)
            report(symbol, ex_date, cninfo, eastmoney, observation, verdict)
        totals[verdict] = totals.get(verdict, 0) + 1
    print(f"\nconflicts={len(conflicts) // 2} {totals}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and read the result**

Run:
`cd /Users/emilyxu/Desktop/jbn/stock && PYTHONPATH=src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python project/replay_price_arbitration.py`

Expected: `conflicts=25` and a totals line reading `cninfo: 16`, `eastmoney: 0`, `merged (D4): 2`, and 7 rows split between `held` and `no observation` depending on which of the 7 have stored bars.

**If the settled count is not 16, stop and report it.** The rule is calibrated by this measurement; a different number means either the rule diverged from the spec or the spec's §2.1 is wrong. Both are findings for the owner, not something to tune away by moving `W` or `L`.

- [ ] **Step 3: Write the operations record**

Create `docs/operations/2026-09-22-price-arbitration-replay.md` with: the date, the script path and its exact command line, the dataset version replayed, the totals line verbatim, the per-conflict table verbatim, and the observed direction distribution. If every settlement names cninfo, say so explicitly and record that the ADR requires this distribution to be monitored -- a channel that always picks one side is a rubber stamp, not an arbiter.

- [ ] **Step 4: Commit**

```bash
git add project/replay_price_arbitration.py docs/operations/2026-09-22-price-arbitration-replay.md
git commit -m "chore(ops): replay the price-observation rule over the conflict corpus"
```

---

### Task 7: ADR-013 and the decisions index (criterion 6)

**Files:**
- Create: `docs/adr/013-price-observed-arbitration.md`
- Modify: `docs/adr/DECISIONS_INDEX.md`

**Interfaces:**
- Consumes: the spec and Task 6's measured result.
- Produces: nothing importable.

**Blocking prerequisite -- read before editing:** `docs/adr/DECISIONS_INDEX.md` carries uncommitted work in progress that is not mine (ADR-012's entry). Adding ADR-013's row means staging a file that also holds that other change. **Stop and ask the owner before staging `DECISIONS_INDEX.md`**; do not sweep their entry into this commit. ADR-012's own file is untracked while the committed spec links to it -- raise that in the same question.

- [ ] **Step 1: Write the ADR**

Create `docs/adr/013-price-observed-arbitration.md`, following the shape of its neighbours (`docs/adr/012-corporate-action-exemption-reads-the-classification.md`). It records, in this order:

- **Decision:** a fourth evidence channel, `PriceObservedArbiter`, implementing the existing `CorporateActionArbiter` protocol, chained behind TDX by first-answer.
- **Rule:** `tick = 0.01 / prev_close`, winner tolerance `W = 2`, loser bar `L = 3`, and why the two stay independent rather than collapsing into one scale.
- **The float32 tolerance** and why it is not wider (600989.SH 2025-05-13's 1.66e-5 stays quarantined).
- **The independence limit, in the spec's own words:** the reference price shares the issuer's announcement as its origin with CNINFO, so it is an independent verification path and not an independent third opinion. No future reader may take a settlement as "two independent sources agreed".
- **The direction distribution** measured in Task 6, and the requirement to keep recording it per settlement.
- **The suspension form fails closed**, with the reasoning from spec §6.
- **Consequences:** which conflicts stop being reviewed, that the 7 remaining ones go to `project/configs/corporate_action_reviews.yml`, and that the rule takes effect from the next `data update` because published versions are immutable.

- [ ] **Step 2: Add the index row**

Append ADR-013's row to `docs/adr/DECISIONS_INDEX.md` in the same column shape as its neighbours.

- [ ] **Step 3: Verify the governance tests**

Run:
`PYTHONPATH=/Users/emilyxu/Desktop/jbn/stock/src /Users/emilyxu/opt/anaconda3/envs/sq312/bin/python -m pytest tests/unit/test_context_governance_docs.py -q`

Expected: PASS. If a doc-shape rule fails, fix the ADR's shape -- not the test.

- [ ] **Step 4: Commit the ADR, then ask about the index**

```bash
git add docs/adr/013-price-observed-arbitration.md
git commit -m "docs(adr): land ADR-013 price-observed conflict arbitration"
```

Then stop and ask the owner before:

```bash
git add docs/adr/DECISIONS_INDEX.md
git commit -m "docs(adr): index ADR-013"
```

---

## Self-Review

**Spec coverage:**

| Spec item | Task |
| --- | --- |
| §3 D1 -- new arbiter implementing the existing protocol, no new protocol or label shape | 2 (protocol), 5 (chain, label) |
| §3 D1 -- TDX first, price on refusal, guards inherited | 5 |
| §3 D2 -- `d_side`, `tick`, two independent thresholds `W=2`/`L=3` | 2 |
| §3 D3 -- prev_close / pre_close / both factors / side / snapshot hash recorded | 3 (hash on the observation), 4 (record seam), 5 (the issue) |
| §3 D3 -- unavailable channel asserts nothing, fail-closed | 2, 3 |
| §3 D4 -- float32 relative tolerance, and nothing wider | 1 |
| §3 D5 -- separation insufficient means no settlement | 2 |
| §4 -- no adjudication of `UNTRUSTED`, no widening of the non-blocking list | not implemented, by design |
| §4 -- 002269's split is a principled ceiling | 2 |
| §4 -- review-worksheet tooling deferred | not implemented, by design |
| §6 -- independence limit recorded, direction monitored | 7 |
| §6 -- suspension form fails closed | 3 |
| §6 -- takes effect from the next update | 7 |
| §7.1 protocol conformance, unchanged protocol | 2, 5 |
| §7.2 five unit branches | 2 (settle / same-factor / winner-out / unavailable / rights), 3 (suspension), 4 |
| §7.3 corpus replay 16/2/7 | 6 |
| §7.4 recomputable from snapshot bytes, covered by `_check_raw_snapshots` | 3, 4 (hash recorded); the lane records through the pipeline's own `record_raw`, so `_check_raw_snapshots` sees it |
| §7.5 full suite passes | 5 Step 7 records the HEAD baseline; the rest is verified at execution |
| §7.6 ADR-013 lands in the index | 7 |

**Known gaps, stated rather than hidden:**

- §7.3 is asserted by an operations script, not a pytest test: the calibration data is production snapshots and this repo keeps production data out of `tests/`. Task 6 Step 2 states the expected numbers and says to stop and report if they differ.
- The price lane's end-to-end path through `DataPipeline.update` has no pytest test. Exercising it needs a fixture whose `sources.yml` state is controlled, and the fixture config carries unrelated WIP. Unit tests cover the chain, the guard rails, and the settlement trace; Task 6 exercises the real numbers. End-to-end coverage needs a fixture change and belongs in its own task.
- The unit tests use self-consistent synthetic numbers rather than the corpus's: the rule's branches are what they pin down, and Task 6's replay is where the real per-row figures are asserted. Two places in the plan say so at the point of use.

**Type consistency:** `PriceObservation(prev_close, prev_close_date, pre_close, snapshot_sha256=None)` is produced by Task 3 and consumed by Tasks 2/4/5 with those exact field names. `settle` returns `Settlement | None` throughout; `Settlement.to_details()` keys (`side`, `prev_close`, `prev_close_date`, `pre_close`, `observed_factor`, `tick`, `cninfo_expected`, `eastmoney_expected`, `cninfo_ticks`, `eastmoney_ticks`) are the ones Tasks 4 and 5 assert. `PriceObservedArbiter(observe, record=None)` in Task 4 matches its construction in Task 5. `FirstAnsweringArbiter(arbiters, issues)` in Task 5 matches its construction in `_build_action_arbiter`. `_build_price_channel` returns `(channel, report_settlement) | None`, and Task 5 Step 6 unpacks exactly that pair. `LazyDailyPriceChannel(fetch_daily, open_days, *, on_failure, raw_snapshots, record_raw)` in Task 3 matches Task 5's call.
