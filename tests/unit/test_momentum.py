"""Momentum60 factor unit tests (Task 6).

All fixtures are deterministic in-memory frames served through a tiny read
surface implementing the ``FactorDataset`` protocol -- no DuckDB, no files and
no network.  The tests pin the metadata, the exact sixty-session lag value,
the three invalid reasons and their precedence, deterministic ordering and the
byte-stable Parquet guarantee.
"""

from datetime import date, timedelta
from io import BytesIO

import pandas as pd
import pytest

from stock_quant.factors.base import FactorContext
from stock_quant.factors.models import FACTOR_RESULT_COLUMNS
from stock_quant.factors.momentum import Momentum60

_SYMBOL = "600000.SH"
_SOURCE = "baostock"
_ADJUSTMENT = "qfq"

# The standardized result columns, mirrored from factors.models.
_FACTOR_INPUT_COLUMNS = [
    "trade_date",
    "symbol",
    "source",
    "adjustment",
    "adjusted_close",
    "quality_severity",
    "listed_trading_days",
]


def _sessions(count: int, start: date = date(2023, 1, 2)) -> list[date]:
    """``count`` consecutive weekday trading dates starting at ``start``."""
    out: list[date] = []
    day = start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


class _FrameDataset:
    """Deterministic in-memory ``FactorDataset`` over one fixed frame."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def factor_input(self) -> pd.DataFrame:
        return self._frame.copy()


def _symbol_rows(
    symbol: str,
    sessions: list[date],
    *,
    close_start: float = 100.0,
    step: float = 1.0,
    error_index: int | None = None,
    listed_start: int = 200,
    source: str = _SOURCE,
    adjustment: str = _ADJUSTMENT,
) -> list[dict]:
    rows: list[dict] = []
    for index, day in enumerate(sessions):
        rows.append(
            {
                "trade_date": day,
                "symbol": symbol,
                "source": source,
                "adjustment": adjustment,
                "adjusted_close": close_start + index * step,
                "quality_severity": "ERROR" if error_index == index else "INFO",
                "listed_trading_days": listed_start + index,
            }
        )
    return rows


def _make_context(
    rows: list[dict],
    *,
    start_date: date,
    end_date: date,
    signal_dates: tuple[date, ...],
    universe_version: str = "universe-v1",
) -> FactorContext:
    frame = pd.DataFrame(rows, columns=_FACTOR_INPUT_COLUMNS)
    dataset = _FrameDataset(frame)
    return FactorContext(
        dataset=dataset,
        universe_version=universe_version,
        start_date=start_date,
        end_date=end_date,
        signal_dates=signal_dates,
    )


def _single_symbol_context(
    count: int,
    *,
    signal_at_last: bool = True,
    error_index: int | None = None,
    listed_start: int = 200,
    close_start: float = 100.0,
    step: float = 1.0,
    source: str = _SOURCE,
    adjustment: str = _ADJUSTMENT,
) -> FactorContext:
    sessions = _sessions(count)
    signal = sessions[-1] if signal_at_last else sessions[0]
    rows = _symbol_rows(
        _SYMBOL,
        sessions,
        close_start=close_start,
        step=step,
        error_index=error_index,
        listed_start=listed_start,
        source=source,
        adjustment=adjustment,
    )
    return _make_context(
        rows,
        start_date=sessions[0],
        end_date=sessions[-1],
        signal_dates=(signal,),
    )


def factor_input_with_error_at(index: int, rows: int) -> pd.DataFrame:
    """A single-symbol ``rows``-session frame with one ERROR break at ``index``."""
    sessions = _sessions(rows)
    return pd.DataFrame(
        _symbol_rows(_SYMBOL, sessions, error_index=index),
        columns=_FACTOR_INPUT_COLUMNS,
    )


def _context_at(
    frame: pd.DataFrame, sessions: list[date], *, signal_index: int
) -> FactorContext:
    """A context over ``frame`` whose only signal date is ``sessions[signal_index]``."""
    return FactorContext(
        dataset=_FrameDataset(frame),
        universe_version="universe-v1",
        start_date=sessions[0],
        end_date=sessions[-1],
        signal_dates=(sessions[signal_index],),
    )


@pytest.fixture
def context_with_61_rows() -> FactorContext:
    """Exactly 61 sessions; signal date is the last session.

    Adjusted close runs from 100.0 (60 sessions before the signal date) to 121.0
    (on the signal date) so momentum equals ``121 / 100 - 1``.
    """
    return _single_symbol_context(61, step=21.0 / 60.0)


# --------------------------------------------------------------------------- #
# Metadata and output schema
# --------------------------------------------------------------------------- #


def test_momentum_contract_metadata():
    factor = Momentum60()
    assert (factor.name, factor.version, factor.lookback, factor.frequency) == (
        "momentum_60d",
        "2.0.0",
        60,
        "weekly",
    )
    assert factor.required_fields == frozenset(
        {"adjusted_close", "quality_severity", "listed_trading_days"}
    )


def test_momentum_output_uses_standardized_schema(context_with_61_rows):
    result = Momentum60().compute(context_with_61_rows)
    assert result.factor_name == "momentum_60d"
    assert result.factor_version == "2.0.0"
    assert list(result.frame.columns) == list(FACTOR_RESULT_COLUMNS)
    assert set(result.frame["factor_name"]) == {"momentum_60d"}
    assert set(result.frame["factor_version"]) == {"2.0.0"}


# --------------------------------------------------------------------------- #
# Value, eligibility and invalid reasons
# --------------------------------------------------------------------------- #


def test_momentum_uses_exact_sixty_session_lag(context_with_61_rows):
    result = Momentum60().compute(context_with_61_rows)
    assert len(result.frame) == 1
    row = result.frame.iloc[0]
    assert row.raw_value == pytest.approx(121 / 100 - 1)
    assert row.processed_value == pytest.approx(row.raw_value)
    assert row.is_valid
    assert row.invalid_reason == ""


def test_momentum_invalidates_with_insufficient_observations():
    context = _single_symbol_context(60)  # one short of the required 61
    row = Momentum60().compute(context).frame.iloc[0]
    assert not row.is_valid
    assert row.invalid_reason == "insufficient_observations"
    assert pd.isna(row.raw_value)
    assert pd.isna(row.processed_value)


def test_momentum_invalidates_when_quality_error_in_lookback():
    # 61 sessions with an ERROR-quality row inside the lookback.
    context = _single_symbol_context(61, error_index=30)
    row = Momentum60().compute(context).frame.iloc[0]
    assert not row.is_valid
    assert row.invalid_reason == "quality_error"


def test_momentum_ignores_quality_error_outside_lookback():
    # 100 sessions: the ERROR sits 100 sessions back, outside the trailing 61.
    context = _single_symbol_context(100, error_index=0)
    row = Momentum60().compute(context).frame.iloc[0]
    assert row.is_valid
    assert row.invalid_reason == ""


def test_momentum_is_invalid_until_error_break_leaves_61_row_window():
    # 72 sessions with an ERROR-quality break at index 10.  The signal at
    # index 70 still looks back over rows 10..70, so the break is inside the
    # 61-row window and the factor is invalid with "quality_error"; one
    # session later the trailing window starts at row 11 and the factor
    # recovers -- the series is never silently patched from raw closes.
    sessions = _sessions(72)
    frame = factor_input_with_error_at(index=10, rows=72)
    crossed = Momentum60().compute(
        _context_at(frame, sessions, signal_index=70)
    ).frame.iloc[0]
    recovered = Momentum60().compute(
        _context_at(frame, sessions, signal_index=71)
    ).frame.iloc[0]
    assert not crossed["is_valid"]
    assert crossed["invalid_reason"] == "quality_error"
    assert recovered["is_valid"]
    assert recovered["invalid_reason"] == ""


def test_momentum_invalidates_when_seasoning_below_120():
    # 61 sessions but only 119 listed trading days as of the signal date.
    context = _single_symbol_context(61, listed_start=59)
    row = Momentum60().compute(context).frame.iloc[0]
    assert not row.is_valid
    assert row.invalid_reason == "seasoning_below_120"


def test_momentum_requirement_precedence_observations_before_seasoning():
    # Fewer than 61 observations must report insufficiency even when seasoned.
    context = _single_symbol_context(50, listed_start=500)
    row = Momentum60().compute(context).frame.iloc[0]
    assert row.invalid_reason == "insufficient_observations"


# --------------------------------------------------------------------------- #
# Input-contract guards
# --------------------------------------------------------------------------- #


def test_momentum_requires_declared_fields():
    sessions = _sessions(61)
    rows = _symbol_rows(_SYMBOL, sessions)
    frame = pd.DataFrame(rows, columns=_FACTOR_INPUT_COLUMNS).drop(
        columns=["adjusted_close"]
    )
    dataset = _FrameDataset(frame)
    context = FactorContext(
        dataset=dataset,
        universe_version="universe-v1",
        start_date=sessions[0],
        end_date=sessions[-1],
        signal_dates=(sessions[-1],),
    )
    with pytest.raises(ValueError, match="adjusted_close"):
        Momentum60().compute(context)


def test_momentum_never_mixes_source_adjustment_series():
    sessions = _sessions(80)
    rows = _symbol_rows(_SYMBOL, sessions[:60], source="baostock", adjustment="qfq")
    rows += _symbol_rows(
        _SYMBOL,
        sessions[60:],
        close_start=300.0,
        source="baostock",
        adjustment="hfq",
    )
    context = _make_context(
        rows,
        start_date=sessions[0],
        end_date=sessions[-1],
        signal_dates=(sessions[-1],),
    )
    with pytest.raises(ValueError, match="source"):
        Momentum60().compute(context)


def test_momentum_rejects_duplicate_dates_within_a_symbol_series():
    sessions = _sessions(61)
    rows = _symbol_rows(_SYMBOL, sessions)
    rows.append(rows[-1])  # duplicate the signal-date row
    context = _make_context(
        rows,
        start_date=sessions[0],
        end_date=sessions[-1],
        signal_dates=(sessions[-1],),
    )
    with pytest.raises(ValueError, match="duplicate"):
        Momentum60().compute(context)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_momentum_sorts_by_trade_date_then_symbol():
    sessions = _sessions(66)
    first_symbol = "600000.SH"
    second_symbol = "000001.SZ"
    rows = _symbol_rows(second_symbol, sessions, close_start=200.0)
    rows += _symbol_rows(first_symbol, sessions)
    signal_dates = (sessions[61], sessions[65])
    context = _make_context(
        rows,
        start_date=sessions[0],
        end_date=sessions[-1],
        signal_dates=signal_dates,
    )
    frame = Momentum60().compute(context).frame

    assert len(frame) == 4  # 2 signal dates x 2 symbols
    assert list(frame["trade_date"]) == sorted(frame["trade_date"])
    for _, group in frame.groupby("trade_date", sort=True):
        assert list(group["symbol"]) == sorted(group["symbol"])


def _parquet_bytes(frame: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def test_momentum_parquet_output_is_byte_stable(context_with_61_rows):
    factor = Momentum60()
    first = factor.compute(context_with_61_rows).frame
    second = factor.compute(context_with_61_rows).frame  # identical inputs again
    assert _parquet_bytes(first) == _parquet_bytes(second)
