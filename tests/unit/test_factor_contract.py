"""Factor protocol, immutable context and standardized result contract tests (Task 6).

These tests fix the *generic* factor contract that every factor must satisfy:
the frozen ``FactorContext``/``FactorResult`` shapes, the ``Factor`` protocol
surface, and the validation rules for standardized result frames (exact
columns, unique ``(trade_date, symbol)`` keys, and a non-empty
``invalid_reason`` for every invalid row).  They run on deterministic
in-memory frames and never touch DuckDB, Parquet files or the network.
"""

from dataclasses import FrozenInstanceError, fields
from datetime import date
from typing import get_type_hints

import pandas as pd
import pytest

from stock_quant.factors.base import Factor, FactorContext
from stock_quant.factors.models import (
    FACTOR_RESULT_COLUMNS,
    FactorResult,
    validate_factor_frame,
)


def _conforming_frame() -> pd.DataFrame:
    """A two-row, sorted, fully valid standardized factor-result frame."""
    rows = [
        {
            "trade_date": date(2023, 1, 3),
            "symbol": "000001.SZ",
            "factor_name": "some_factor",
            "factor_version": "1.0.0",
            "raw_value": 0.25,
            "processed_value": 0.25,
            "is_valid": True,
            "invalid_reason": "",
        },
        {
            "trade_date": date(2023, 1, 3),
            "symbol": "600000.SH",
            "factor_name": "some_factor",
            "factor_version": "1.0.0",
            "raw_value": -0.05,
            "processed_value": -0.05,
            "is_valid": True,
            "invalid_reason": "",
        },
    ]
    return pd.DataFrame(rows, columns=list(FACTOR_RESULT_COLUMNS))


class _ReadOnlyStubDataset:
    """A dataset stand-in satisfying ``FactorContext.dataset`` (no real read)."""

    def factor_input(self) -> pd.DataFrame:
        return pd.DataFrame()


# --------------------------------------------------------------------------- #
# FactorContext
# --------------------------------------------------------------------------- #


def test_factor_context_fields_match_contract():
    assert tuple(field.name for field in fields(FactorContext)) == (
        "dataset",
        "universe_version",
        "start_date",
        "end_date",
        "signal_dates",
    )


def test_factor_context_is_frozen():
    context = FactorContext(
        dataset=_ReadOnlyStubDataset(),
        universe_version="universe-v1",
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 31),
        signal_dates=(date(2023, 1, 6), date(2023, 1, 31)),
    )
    assert context.universe_version == "universe-v1"
    assert context.signal_dates == (date(2023, 1, 6), date(2023, 1, 31))
    with pytest.raises(FrozenInstanceError):
        context.end_date = date(2023, 2, 1)  # type: ignore[misc]


def test_factor_context_datasets_are_typed_as_read_surface():
    hints = get_type_hints(FactorContext)
    assert hints["dataset"].__name__ == "FactorDataset"
    assert hints["signal_dates"].__name__ == "tuple"


# --------------------------------------------------------------------------- #
# Factor protocol surface
# --------------------------------------------------------------------------- #


def test_factor_protocol_declares_full_contract():
    assert {"name", "version", "lookback", "required_fields", "frequency"} <= set(
        Factor.__annotations__
    )
    assert callable(Factor.compute)


# --------------------------------------------------------------------------- #
# FactorResult and result-frame validation
# --------------------------------------------------------------------------- #


def test_factor_result_columns_are_exact_and_ordered():
    assert FACTOR_RESULT_COLUMNS == (
        "trade_date",
        "symbol",
        "factor_name",
        "factor_version",
        "raw_value",
        "processed_value",
        "is_valid",
        "invalid_reason",
    )


def test_factor_result_fields_match_contract():
    assert tuple(field.name for field in fields(FactorResult)) == (
        "factor_name",
        "factor_version",
        "frame",
    )


def test_factor_result_accepts_a_conforming_frame():
    result = FactorResult(
        factor_name="some_factor", factor_version="1.0.0", frame=_conforming_frame()
    )
    assert result.factor_name == "some_factor"
    assert list(result.frame.columns) == list(FACTOR_RESULT_COLUMNS)


def test_factor_result_is_frozen():
    result = FactorResult(
        factor_name="f", factor_version="1", frame=_conforming_frame()
    )
    with pytest.raises(FrozenInstanceError):
        result.frame = pd.DataFrame()  # type: ignore[misc]


def test_validate_factor_frame_rejects_missing_columns():
    bad = _conforming_frame().drop(columns=["processed_value"])
    with pytest.raises(ValueError, match="columns"):
        validate_factor_frame(bad)


def test_validate_factor_frame_rejects_reordered_columns():
    bad = _conforming_frame()[list(reversed(FACTOR_RESULT_COLUMNS))]
    with pytest.raises(ValueError, match="columns"):
        validate_factor_frame(bad)


def test_validate_factor_frame_rejects_duplicate_date_symbol_keys():
    bad = _conforming_frame().copy()
    duplicate = bad.iloc[[0]].copy()
    bad = pd.concat([bad, duplicate], ignore_index=True)
    with pytest.raises(ValueError, match="unique"):
        validate_factor_frame(bad)


def test_validate_factor_frame_requires_reason_for_every_invalid_row():
    bad = _conforming_frame().copy()
    bad.loc[0, "is_valid"] = False
    bad.loc[0, "invalid_reason"] = ""
    with pytest.raises(ValueError, match="reason"):
        validate_factor_frame(bad)


def test_validate_factor_frame_rejects_reason_on_a_valid_row():
    bad = _conforming_frame().copy()
    bad.loc[0, "invalid_reason"] = "some_reason"
    with pytest.raises(ValueError, match="reason"):
        validate_factor_frame(bad)
