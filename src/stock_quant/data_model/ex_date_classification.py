"""Two-axis classification of an absent ex-date (ADR-009).

Decision 1: a corporate-action row whose ex-date is absent is classified on
two axes -- whether the supplier states an ex-date, and what the market's
price-event record shows at the event's probe date -- *before* any rule
refuses the row or exempts its quarantine reason from withholding coverage
trust.  Decision 2 fixes the only admissible absence claim: a channel whose
price-event list *brackets* the probe date (strictly between its first and
last reported events) and is empty at it.  A date at or beyond a channel's
ends is covered by nothing, a channel that emits rows only on events cannot
prove a bare tail, and an event row at the date is an observed adjustment
that overrides any absence (decision 3).

The admissible channels are named by ADR-009 decision 3: TDX category-1
records and baostock's adjustment-factor series (the latter is not wired in
this repo yet; an absent channel asserts nothing, which is the fail-closed
direction -- an unbracketed row reads ``unknown`` and stays blocking).

This module records classifications only: nothing here moves a verdict.
Moving one is a separate decision (ADR-009 decision 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

#: Supplier axis (first axis of ADR-009's table).
SUPPLIER_STATED = "stated"
SUPPLIER_ABSENT = "absent"

#: Market axis (second axis of ADR-009's table).
MARKET_ADJUSTMENT_OBSERVED = "adjustment_observed"
MARKET_NO_ADJUSTMENT_BRACKETED = "no_adjustment_bracketed_empty"
MARKET_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Classification:
    """One row's two-axis classification, and its composed ADR-009 code.

    ``code`` composes the axes (``absent+no_adjustment_bracketed_empty`` is
    the ADR's row 1, ``absent+adjustment_observed`` row 2, ``absent+unknown``
    row 3, and so on).  A row probed with no date and no channels reads
    ``unknown`` on the market axis -- the fail-closed direction.
    """

    supplier_axis: str
    market: str
    probe_date: date | None

    @property
    def code(self) -> str:
        return f"{self.supplier_axis}+{self.market}"


def market_view(
    event_dates: Iterable[date] | None, target: date
) -> str:
    """What one price-event channel shows at ``target``.

    ``event_dates`` are the channel's reported price-event dates.  A date
    strictly between the channel's first and last reported events is covered
    by the list, so an empty bracket asserts absence; a reported event at the
    date is an observed adjustment; anything else -- an empty list, a date at
    or beyond the list's ends -- asserts nothing.
    """
    if event_dates is None:
        return MARKET_UNKNOWN
    dates = sorted({day for day in event_dates if day is not None})
    if not dates:
        return MARKET_UNKNOWN
    if target in dates:
        return MARKET_ADJUSTMENT_OBSERVED
    if dates[0] < target < dates[-1]:
        return MARKET_NO_ADJUSTMENT_BRACKETED
    return MARKET_UNKNOWN


def classify_ex_date(
    *,
    supplier_ex_date: date | None,
    probe_date: date | None,
    channels: Sequence[tuple[str, Iterable[date] | None]] = (),
) -> Classification:
    """Classify one row on the two axes of ADR-009.

    ``supplier_ex_date`` reads the first axis (``None`` = absent).  The
    market axis aggregates the admissible ``channels`` -- each a ``(name,
    event_dates)`` pair where ``None`` dates mean the channel is unavailable
    and asserts nothing: an observed adjustment anywhere wins (decision 3),
    else a bracketing-empty channel asserts absence (decision 2), else the
    view is unknown and the row stays blocking (fail-closed).
    """
    supplier_axis = SUPPLIER_STATED if supplier_ex_date is not None else SUPPLIER_ABSENT
    if probe_date is None:
        return Classification(supplier_axis, MARKET_UNKNOWN, None)
    views = [market_view(dates, probe_date) for _, dates in channels]
    if MARKET_ADJUSTMENT_OBSERVED in views:
        market = MARKET_ADJUSTMENT_OBSERVED
    elif MARKET_NO_ADJUSTMENT_BRACKETED in views:
        market = MARKET_NO_ADJUSTMENT_BRACKETED
    else:
        market = MARKET_UNKNOWN
    return Classification(supplier_axis, market, probe_date)
