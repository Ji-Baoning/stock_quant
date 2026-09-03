"""Standardized target-portfolio model and its frame validation (Task 8).

``PortfolioTarget`` fixes one signal date's target portfolio as a standardized
frame of exact, fixed column order ``trade_date, symbol, rank, signal_price,
target_weight, target_quantity, selection_reason`` plus a scalar
``unallocated_weight`` -- the share of equity deliberately left in cash, i.e.
one minus the sum of the allocated slot weights.  The scalar is only
well-defined for a single signal date, so a non-empty frame must carry exactly
one ``trade_date``.  ``rank`` is the 1-based factor rank of the symbol on that
date; ``signal_price`` the signal-day unadjusted close used to estimate
``target_quantity``; ``target_weight`` the weight of the equal-weight slot the
name occupies.  The number of slots (``top_n``) may exceed the number of names
actually priced or affordable, and every unused slot stays as unallocated cash.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

#: The exact, ordered output columns of every target-portfolio frame.
PORTFOLIO_TARGET_COLUMNS = (
    "trade_date",
    "symbol",
    "rank",
    "signal_price",
    "target_weight",
    "target_quantity",
    "selection_reason",
)


def _as_numeric(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any():
        raise ValueError(
            f"portfolio target column {series.name!r} must be numeric"
        )
    return numeric


def validate_portfolio_frame(frame: pd.DataFrame) -> None:
    """Validate a standardized target frame, raising ``ValueError``.

    Checks the exact ordered columns; for a non-empty frame it also enforces
    the single-``trade_date`` invariant, unique symbols, positive integer
    strictly-ascending ranks, positive integer ``target_quantity`` in lots, a
    finite positive ``signal_price``, a finite ``target_weight`` in ``(0, 1]``
    and a non-empty ``selection_reason`` on every row.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(
            f"portfolio target must be a pandas DataFrame, got {type(frame).__name__}"
        )
    if list(frame.columns) != list(PORTFOLIO_TARGET_COLUMNS):
        raise ValueError(
            "portfolio target columns must be exactly "
            f"{list(PORTFOLIO_TARGET_COLUMNS)} in order; got {list(frame.columns)}"
        )
    if frame.empty:
        return

    if frame["trade_date"].nunique() != 1:
        raise ValueError("portfolio target frame must span exactly one trade_date")
    if frame["symbol"].duplicated().any():
        raise ValueError("portfolio target symbols must be unique")

    ranks = _as_numeric(frame["rank"])
    if (ranks < 1).any() or (ranks % 1 != 0).any():
        raise ValueError("portfolio target rank must be a positive integer per row")
    if (ranks.diff().dropna() <= 0).any():
        raise ValueError("portfolio target rows must be ordered by ascending rank")

    quantities = _as_numeric(frame["target_quantity"])
    if (quantities < 1).any() or (quantities % 1 != 0).any():
        raise ValueError("portfolio target_quantity must be a positive integer")

    prices = _as_numeric(frame["signal_price"])
    if (prices <= 0).any():
        raise ValueError("portfolio target signal_price must be positive")

    weights = _as_numeric(frame["target_weight"])
    if (weights <= 0).any() or (weights > 1).any():
        raise ValueError("portfolio target target_weight must lie in (0, 1]")

    reasons = frame["selection_reason"].astype(str).str.strip()
    if (reasons == "").any():
        raise ValueError("every portfolio target row must carry a selection_reason")


@dataclass(frozen=True)
class PortfolioTarget:
    """A validated target portfolio for one signal date.

    ``frame`` rows are sorted by ascending ``rank``; ``unallocated_weight`` is
    the equity share left in cash, always equal to one minus the sum of the
    allocated ``target_weight`` values.
    """

    frame: pd.DataFrame
    unallocated_weight: float

    def __post_init__(self) -> None:
        validate_portfolio_frame(self.frame)
        unallocated = float(self.unallocated_weight)
        if not math.isfinite(unallocated) or not (0.0 <= unallocated <= 1.0):
            raise ValueError(
                "unallocated_weight must be a finite fraction in [0, 1]"
            )
        allocated = (
            0.0 if self.frame.empty else float(self.frame["target_weight"].sum())
        )
        if not math.isclose(allocated + unallocated, 1.0, abs_tol=1e-9):
            raise ValueError(
                "unallocated_weight must equal 1 - sum(target_weight)"
            )
