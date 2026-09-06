"""Versioned factor contracts: the read surface, context and ``Factor`` protocol.

Every factor in this package implements the uniform, versioned ``Factor``
protocol and runs against an immutable ``FactorContext`` that fixes the read
surface (``dataset``), the universe version and the allowed signal dates.  The
read surface is deliberately a small structural ``Protocol`` so factor unit
tests can satisfy it with deterministic in-memory frames instead of a real
DuckDB publication; a published ``DatasetContext`` (data_model.dataset) can
back a conforming implementation by reading its pinned Parquet views.

A factor never resolves ``CURRENT`` again and never writes to the standardized
layer: it only reads what ``FactorContext.dataset`` exposes and returns a
``FactorResult`` (factors.models).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

from stock_quant.factors.models import FactorResult


class FactorDataset(Protocol):
    """Immutable read surface returning the observations a factor needs.

    ``factor_input`` returns the per-(``symbol``, ``trade_date``) daily
    observations a factor computes over, with at least the columns
    ``trade_date, symbol, source, adjustment`` plus every field a factor
    declares in ``Factor.required_fields`` (for Momentum60 that is
    ``adjusted_close``, ``quality_severity`` and ``listed_trading_days``).
    The returned series for each symbol belongs to one ``(source,
    adjustment)`` (the dataset layer resolves the canonical momentum series),
    so a factor never mixes sources or adjustment bases.
    """

    def factor_input(self) -> pd.DataFrame:
        """Return the full observation frame this factor may read."""
        ...


@dataclass(frozen=True)
class FactorContext:
    """The immutable inputs fixed for one factor computation.

    ``dataset`` is pinned to one immutable dataset version; ``signal_dates``
    are the trading dates the factor computes for (weekly last trading days),
    supplied by the caller from calendar semantics.
    """

    dataset: FactorDataset
    universe_version: str
    start_date: date
    end_date: date
    signal_dates: tuple[date, ...]


class Factor(Protocol):
    """Uniform, versioned factor contract (design spec §15.1).

    Every factor exposes stable identity metadata and produces a conforming
    ``FactorResult`` whose rows carry the raw value, the processed value and,
    for every invalid row, an explicit reason.
    """

    name: str
    version: str
    lookback: int
    required_fields: frozenset[str]
    frequency: str

    def compute(self, context: FactorContext) -> FactorResult:
        """Compute the factor over ``context`` and return a validated result."""
        ...
