"""Standardized factor result model and its frame validation (Task 6).

Every factor emits a ``FactorResult`` whose ``frame`` is a standardized table:
exact, fixed column order ``trade_date, symbol, factor_name, factor_version,
raw_value, processed_value, is_valid, invalid_reason``; unique
``(trade_date, symbol)`` keys; and an explicit, non-empty ``invalid_reason``
for every invalid row (never a bare NaN).  ``processed_value`` is reserved for
later winsorization/standardization and may equal ``raw_value`` (as it does for
Momentum60 in this phase).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: The exact, ordered output columns of every standardized factor-result frame.
FACTOR_RESULT_COLUMNS = (
    "trade_date",
    "symbol",
    "factor_name",
    "factor_version",
    "raw_value",
    "processed_value",
    "is_valid",
    "invalid_reason",
)


def validate_factor_frame(frame: pd.DataFrame) -> None:
    """Validate a standardized factor-result frame, raising ``ValueError``.

    Checks the exact ordered columns, unique ``(trade_date, symbol)`` keys and
    the invalid-reason invariant: every invalid row carries a non-empty reason
    and every valid row carries an empty one.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(
            f"factor result must be a pandas DataFrame, got {type(frame).__name__}"
        )
    if list(frame.columns) != list(FACTOR_RESULT_COLUMNS):
        raise ValueError(
            "factor result columns must be exactly "
            f"{list(FACTOR_RESULT_COLUMNS)} in order; got {list(frame.columns)}"
        )
    if frame[["trade_date", "symbol"]].duplicated().any():
        raise ValueError("factor result (trade_date, symbol) keys must be unique")

    valid = frame["is_valid"].fillna(False).astype(bool)
    reason = frame["invalid_reason"]
    empty_reason = reason.isna() | reason.astype(str).str.strip().eq("")
    reason_on_invalid = (~valid) & empty_reason
    reason_on_valid = valid & (~empty_reason)
    if reason_on_invalid.any() or reason_on_valid.any():
        raise ValueError(
            "every invalid factor row must carry a non-empty invalid_reason "
            "and every valid row an empty one"
        )


@dataclass(frozen=True)
class FactorResult:
    """A validated, versioned factor output for one computation."""

    factor_name: str
    factor_version: str
    frame: pd.DataFrame

    def __post_init__(self) -> None:
        if not self.factor_name:
            raise ValueError("factor_name must be non-empty")
        if not self.factor_version:
            raise ValueError("factor_version must be non-empty")
        validate_factor_frame(self.frame)
