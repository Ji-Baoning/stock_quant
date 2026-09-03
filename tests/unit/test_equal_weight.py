"""Lot-sized equal-weight target portfolio tests (Task 8).

These tests fix the deterministic conversion of one signal date's standardized
factor ranks into a target portfolio: valid rows ranked by descending
``processed_value`` then ascending ``symbol``, at most ``top_n`` selected at
equal weight ``1/top_n``, quantity estimated from the signal-day unadjusted
close floored to the lot size, and every residual slot left as explicit
unallocated cash.  They also pin the no-lookahead guards: a name whose
signal-day close is unavailable is dropped to cash (never replaced by a later
price or a later-ranked name), and an ambiguous multi-close price map is
rejected rather than silently choosing a later row.  Everything runs offline
on deterministic in-memory frames.
"""

from datetime import date

import pandas as pd
import pytest

from stock_quant.factors.models import FACTOR_RESULT_COLUMNS, FactorResult
from stock_quant.portfolio.equal_weight import TopNEqualWeight
from stock_quant.portfolio.models import (
    PORTFOLIO_TARGET_COLUMNS,
    PortfolioTarget,
)

_SIGNAL_DATE = date(2023, 1, 6)

# --------------------------------------------------------------------------- #
# Deterministic in-memory fixtures
# --------------------------------------------------------------------------- #

# Twelve names: the top two share the highest processed_value (tie), eight more
# descend strictly below them, and two sit outside the top ten.
_TIE_SYMBOLS = [
    "000001.SZ",
    "600000.SH",
    "000002.SZ",
    "000063.SZ",
    "000651.SZ",
    "000858.SZ",
    "002415.SZ",
    "300750.SZ",
    "600519.SH",
    "601318.SH",
    "000333.SZ",
    "300059.SZ",
]
_TIE_VALUES = {
    "000001.SZ": 0.09,
    "600000.SH": 0.09,
    "000002.SZ": 0.08,
    "000063.SZ": 0.07,
    "000651.SZ": 0.06,
    "000858.SZ": 0.05,
    "002415.SZ": 0.04,
    "300750.SZ": 0.03,
    "600519.SH": 0.02,
    "601318.SH": 0.01,
    "000333.SZ": -0.05,
    "300059.SZ": -0.10,
}
_EXPECTED_TOP_TEN = [
    "000001.SZ",
    "600000.SH",
    "000002.SZ",
    "000063.SZ",
    "000651.SZ",
    "000858.SZ",
    "002415.SZ",
    "300750.SZ",
    "600519.SH",
    "601318.SH",
]
# Signal-day unadjusted closes chosen so every quantity is a positive 100-share
# lot at 100000 capital and 10% slot weight.
_CLOSES = {
    "000001.SZ": 10.0,
    "600000.SH": 20.0,
    "000002.SZ": 5.0,
    "000063.SZ": 25.0,
    "000651.SZ": 40.0,
    "000858.SZ": 10.0,
    "002415.SZ": 50.0,
    "300750.SZ": 100.0,
    "600519.SH": 8.0,
    "601318.SH": 20.0,
    "000333.SZ": 10.0,
    "300059.SZ": 10.0,
}
_EXPECTED_QUANTITIES = {
    "000001.SZ": 1000,
    "600000.SH": 500,
    "000002.SZ": 2000,
    "000063.SZ": 400,
    "000651.SZ": 200,
    "000858.SZ": 1000,
    "002415.SZ": 200,
    "300750.SZ": 100,
    "600519.SH": 1200,
    "601318.SH": 500,
}


def _factor_row(
    symbol: str, value: float, *, is_valid: bool = True, reason: str = ""
) -> dict:
    return {
        "trade_date": _SIGNAL_DATE,
        "symbol": symbol,
        "factor_name": "momentum_60d",
        "factor_version": "1.0.0",
        "raw_value": value,
        "processed_value": value,
        "is_valid": is_valid,
        "invalid_reason": reason,
    }


def _momentum_result(rows: list[dict]) -> FactorResult:
    frame = pd.DataFrame(rows, columns=list(FACTOR_RESULT_COLUMNS))
    return FactorResult(factor_name="momentum_60d", factor_version="1.0.0", frame=frame)


def factors_with_tie() -> FactorResult:
    """Twelve valid names with a two-name tie at the top of the ranking."""
    rows = [_factor_row(symbol, _TIE_VALUES[symbol]) for symbol in _TIE_SYMBOLS]
    return _momentum_result(rows)


def six_valid_factors() -> FactorResult:
    """Only six eligible names, so four of ten equal-weight slots stay cash."""
    symbols = [
        "000001.SZ",
        "600000.SH",
        "000002.SZ",
        "000063.SZ",
        "000651.SZ",
        "000858.SZ",
    ]
    values = {
        "000001.SZ": 0.06,
        "600000.SH": 0.05,
        "000002.SZ": 0.04,
        "000063.SZ": 0.03,
        "000651.SZ": 0.02,
        "000858.SZ": 0.01,
    }
    rows = [_factor_row(symbol, values[symbol]) for symbol in symbols]
    return _momentum_result(rows)


def closes() -> pd.DataFrame:
    """Signal-day unadjusted close per symbol, one row per symbol."""
    return pd.DataFrame(
        [{"symbol": symbol, "close": close} for symbol, close in _CLOSES.items()],
        columns=["symbol", "close"],
    )


# --------------------------------------------------------------------------- #
# Ranked equal-weight selection and lot sizing
# --------------------------------------------------------------------------- #


def test_top_ten_equal_weight_and_symbol_tie_break():
    target = TopNEqualWeight(top_n=10, lot_size=100).build(
        factors_with_tie(), closes(), capital=100000
    )
    assert target.frame.symbol.tolist()[:2] == ["000001.SZ", "600000.SH"]
    assert target.frame.symbol.tolist() == _EXPECTED_TOP_TEN
    assert set(target.frame.target_weight) == {0.1}
    assert (target.frame.target_quantity % 100 == 0).all()
    assert target.frame.target_quantity.tolist() == [
        _EXPECTED_QUANTITIES[symbol] for symbol in _EXPECTED_TOP_TEN
    ]
    assert target.frame["rank"].tolist() == list(range(1, 11))
    assert target.frame.signal_price.tolist() == [
        _CLOSES[symbol] for symbol in _EXPECTED_TOP_TEN
    ]
    assert (target.frame.selection_reason.astype(str).str.strip() != "").all()
    assert target.unallocated_weight == pytest.approx(0.0)


def test_fewer_than_ten_eligible_names_keeps_unallocated_cash():
    target = TopNEqualWeight(top_n=10).build(
        six_valid_factors(), closes(), capital=100000
    )
    assert target.frame.target_weight.sum() == pytest.approx(0.6)
    assert target.unallocated_weight == pytest.approx(0.4)
    assert len(target.frame) == 6
    assert target.frame.target_weight.tolist() == pytest.approx([0.1] * 6)


def test_build_is_deterministic_across_factor_row_order():
    sorted_result = factors_with_tie()
    shuffled = _momentum_result(list(reversed(sorted_result.frame.to_dict("records"))))
    first = TopNEqualWeight(top_n=10, lot_size=100).build(
        sorted_result, closes(), capital=100000
    )
    second = TopNEqualWeight(top_n=10, lot_size=100).build(
        shuffled, closes(), capital=100000
    )
    pd.testing.assert_frame_equal(first.frame, second.frame)
    assert first.unallocated_weight == second.unallocated_weight


def test_unaffordable_top_rank_slot_stays_cash_without_backfill():
    # A top-ranked name whose floored quantity is below one lot cannot afford a
    # single lot, so it is excluded and its equal-weight slot stays unallocated
    # cash; the next-ranked affordable name still enters at its own rank, and a
    # still-lower affordable name is never backfilled into the dropped slot.
    rows = [
        _factor_row("600519.SH", 0.10),  # rank 1 -- signal close far too high
        _factor_row("000001.SZ", 0.09),  # rank 2 -- affordable, still enters
        _factor_row("000002.SZ", 0.08),  # rank 3 -- affordable but outside top_n
    ]
    prices = pd.DataFrame(
        [
            {"symbol": "600519.SH", "close": 50000.0},
            {"symbol": "000001.SZ", "close": 10.0},
            {"symbol": "000002.SZ", "close": 10.0},
        ],
        columns=["symbol", "close"],
    )
    target = TopNEqualWeight(top_n=2, lot_size=100).build(
        _momentum_result(rows), prices, capital=100000
    )
    # 600519.SH would size to floor(0.5*100000/50000/100) == 0 lots: excluded.
    assert "600519.SH" not in set(target.frame.symbol)
    # The rank-2 affordable name enters at slot weight 1/2 (0.5 lots -> 5000).
    assert target.frame.symbol.tolist() == ["000001.SZ"]
    assert target.frame["rank"].tolist() == [2]
    assert target.frame["target_quantity"].tolist() == [5000]
    assert target.frame["target_weight"].tolist() == pytest.approx([0.5])
    # Its slot stays unallocated cash and the rank-3 name is not promoted in.
    assert "000002.SZ" not in set(target.frame.symbol)
    assert target.unallocated_weight == pytest.approx(0.5)


def test_invalid_rows_are_never_selected_even_when_top_ranked():
    rows = [_factor_row(symbol, _TIE_VALUES[symbol]) for symbol in _TIE_SYMBOLS]
    # A never-eligible name carries the highest value but is marked invalid.
    rows.append(
        _factor_row(
            "600999.SH",
            9.99,
            is_valid=False,
            reason="quality_error",
        )
    )
    target = TopNEqualWeight(top_n=10, lot_size=100).build(
        _momentum_result(rows), closes(), capital=100000
    )
    assert "600999.SH" not in set(target.frame.symbol)
    assert target.frame.symbol.tolist() == _EXPECTED_TOP_TEN


# --------------------------------------------------------------------------- #
# No-lookahead guards
# --------------------------------------------------------------------------- #


def test_missing_signal_price_drops_name_without_promoting_next_rank():
    prices = closes()[closes()["symbol"] != "000001.SZ"]  # top name unavailable
    target = TopNEqualWeight(top_n=10, lot_size=100).build(
        factors_with_tie(), prices, capital=100000
    )
    assert "000001.SZ" not in set(target.frame.symbol)
    # The next-ranked priced name must not be backfilled into the dropped slot.
    assert "000333.SZ" not in set(target.frame.symbol)
    assert len(target.frame) == 9
    assert target.frame["rank"].tolist() == [2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert target.unallocated_weight == pytest.approx(0.1)


def test_ambiguous_multi_close_price_map_is_rejected():
    future_prices = pd.concat(
        [closes(), closes()], ignore_index=True
    )  # duplicate symbols across days
    with pytest.raises(ValueError, match="one row per symbol"):
        TopNEqualWeight(top_n=10, lot_size=100).build(
            factors_with_tie(), future_prices, capital=100000
        )


def test_build_rejects_a_multi_signal_date_factor_result():
    second_day = _momentum_result(
        [
            {
                **_factor_row("000001.SZ", 0.05),
                "trade_date": date(2023, 1, 13),
            }
        ]
    )
    both_days = pd.concat(
        [factors_with_tie().frame, second_day.frame], ignore_index=True
    )
    multi = _momentum_result(both_days.to_dict("records"))
    with pytest.raises(ValueError, match="exactly one"):
        TopNEqualWeight(top_n=10).build(multi, closes(), capital=100000)


# --------------------------------------------------------------------------- #
# Result model shape and invariants
# --------------------------------------------------------------------------- #


def test_portfolio_target_columns_are_exact_and_consistent():
    target = TopNEqualWeight(top_n=10, lot_size=100).build(
        factors_with_tie(), closes(), capital=100000
    )
    assert PORTFOLIO_TARGET_COLUMNS == (
        "trade_date",
        "symbol",
        "rank",
        "signal_price",
        "target_weight",
        "target_quantity",
        "selection_reason",
    )
    assert list(target.frame.columns) == list(PORTFOLIO_TARGET_COLUMNS)
    assert isinstance(target, PortfolioTarget)


def test_portfolio_target_rejects_inconsistent_unallocated_weight():
    target = TopNEqualWeight(top_n=10, lot_size=100).build(
        factors_with_tie(), closes(), capital=100000
    )
    with pytest.raises(ValueError, match="1 - sum"):
        PortfolioTarget(frame=target.frame, unallocated_weight=0.5)
