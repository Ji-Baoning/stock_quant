"""Point-in-time total-return builder unit tests (Task 1).

All fixtures are deterministic in-memory canonical frames -- no DuckDB, no
files and no network.  The tests pin the canonical schema, the exact
total-return recursion for cash dividends, bonus shares and same-day merged
events, the no-lookahead guarantee, stable JSON action ids, the
NaN/None-robust handling of canonical float64 fact columns and the
conservative trust breaks for quarantined or untrusted corporate actions.
"""

from datetime import date, timedelta

import pandas as pd
import pyarrow as pa
import pytest

from stock_quant.data_model.adjusted_bar import (
    ADJUSTMENT_NAME,
    build_adjusted_bars,
)
from stock_quant.data_model.schemas import (
    ADJUSTED_BAR_COLUMNS,
    ADJUSTED_BAR_SCHEMA,
    CORPORATE_ACTION_COLUMNS,
    CORPORATE_ACTION_COVERAGE_COLUMNS,
    CORPORATE_ACTION_QUARANTINE_COLUMNS,
    DAILY_COLUMNS,
)

_SYMBOL = "600000.SH"
_SOURCE = "baostock"


def _sessions(count: int, start: date = date(2022, 6, 1)) -> list[date]:
    """``count`` consecutive weekday trading dates starting at ``start``."""
    out: list[date] = []
    day = start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


# The 90 deterministic weekday sessions every multi-day fixture draws from.
days = _sessions(90)


def _daily_frame(
    closes: list[float], sessions: list[date], symbol: str = _SYMBOL
) -> pd.DataFrame:
    rows = [
        {
            "trade_date": day,
            "symbol": symbol,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "amount": close * 1000,
            "adjustment": "none",
            "source": _SOURCE,
            "ingested_at": pd.Timestamp("2022-06-03", tz="UTC"),
        }
        for day, close in zip(sessions, closes)
    ]
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def _action_row(
    ex_date: date,
    *,
    cash: float = 0.0,
    bonus: float = 0.0,
    capitalization: float = 0.0,
    rights: float = 0.0,
    status: str = "implemented",
    symbol: str = _SYMBOL,
) -> dict:
    return {
        "symbol": symbol,
        "announcement_date": ex_date - timedelta(days=7),
        "record_date": ex_date - timedelta(days=1),
        "ex_date": ex_date,
        "cash_dividend_per_share": cash,
        "bonus_share_ratio": bonus,
        "capitalization_ratio": capitalization,
        "rights_issue_ratio": rights,
        "rights_issue_price": 0.0,
        "source": _SOURCE,
        "status": status,
    }


def _empty_actions() -> pd.DataFrame:
    return pd.DataFrame(columns=CORPORATE_ACTION_COLUMNS)


def empty_quarantine() -> pd.DataFrame:
    return pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)


def _coverage_frame(
    status: str, reason: str = "", symbol: str = _SYMBOL
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "window_start": date(2022, 1, 1),
                "window_end": date(2023, 12, 31),
                "status": status,
                "reason": reason,
                "sources": "[]",
                "snapshot_hashes": "[]",
                "checked_at": pd.Timestamp("2022-06-03", tz="UTC"),
            }
        ],
        columns=CORPORATE_ACTION_COVERAGE_COLUMNS,
    )


@pytest.fixture
def daily():
    return _daily_frame([10.0, 9.0], days[:2])


@pytest.fixture
def empty_actions():
    return _empty_actions()


@pytest.fixture
def verified_coverage():
    return _coverage_frame("VERIFIED_EMPTY")


@pytest.fixture
def daily_10_then_9():
    return _daily_frame([10.0, 9.0], days[:2])


@pytest.fixture
def daily_10_then_5():
    return _daily_frame([10.0, 5.0], days[:2])


@pytest.fixture
def cash_dividend():
    return pd.DataFrame(
        [_action_row(days[1], cash=1.0)], columns=CORPORATE_ACTION_COLUMNS
    )


@pytest.fixture
def bonus_share():
    return pd.DataFrame(
        [_action_row(days[1], bonus=1.0)], columns=CORPORATE_ACTION_COLUMNS
    )


@pytest.fixture
def combined_action():
    return pd.DataFrame(
        [_action_row(days[1], cash=0.10, bonus=0.05, capitalization=0.05)],
        columns=CORPORATE_ACTION_COLUMNS,
    )


@pytest.fixture
def daily_three_days():
    return _daily_frame([10.0, 9.0, 9.5], days[:3])


@pytest.fixture
def daily_90_days():
    return _daily_frame([10.0 + 0.1 * index for index in range(90)], days)


@pytest.fixture
def untrusted_coverage():
    return _coverage_frame("UNTRUSTED", reason="supplier_endpoint_failure")


def quarantined_event(ex_date: date, reason: str) -> pd.DataFrame:
    row = _action_row(ex_date, status="not_implemented")
    row["confirmed_by"] = "eastmoney"
    row["reason"] = reason
    return pd.DataFrame([row], columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)


def future_cash_action(day: date) -> pd.DataFrame:
    return pd.DataFrame(
        [_action_row(day, cash=0.5)], columns=CORPORATE_ACTION_COLUMNS
    )


def test_adjusted_bar_schema_is_canonical():
    assert ADJUSTED_BAR_COLUMNS == [
        "trade_date", "symbol", "source", "adjustment", "raw_close",
        "adjusted_close", "adjustment_factor", "quality_severity",
        "invalid_reason", "applied_action_ids",
    ]
    assert ADJUSTED_BAR_SCHEMA.field("trade_date").type == pa.date32()


def test_no_event_series_equals_unadjusted_close(
    daily, empty_actions, verified_coverage
):
    result = build_adjusted_bars(
        daily, empty_actions, empty_actions, verified_coverage,
        symbols=("600000.SH",),
    )
    assert result["adjustment"].unique().tolist() == [ADJUSTMENT_NAME]
    assert result["adjusted_close"].tolist() == result["raw_close"].tolist()
    assert result["quality_severity"].tolist() == ["INFO"] * len(result)


def test_cash_dividend_preserves_flat_total_return(
    daily_10_then_9, cash_dividend, verified_coverage
):
    result = build_adjusted_bars(
        daily_10_then_9, cash_dividend, empty_quarantine(), verified_coverage,
        symbols=("600000.SH",),
    )
    assert result["adjusted_close"].tolist() == [10.0, 10.0]
    assert result.iloc[1]["applied_action_ids"] == '["600000.SH#2022-06-02"]'


def test_one_for_one_bonus_preserves_flat_total_return(
    daily_10_then_5, bonus_share, verified_coverage
):
    result = build_adjusted_bars(
        daily_10_then_5, bonus_share, empty_quarantine(), verified_coverage,
        symbols=("600000.SH",),
    )
    assert result["adjusted_close"].tolist() == [10.0, 10.0]


def test_same_day_cash_bonus_and_capitalization_are_applied_once(
    daily, combined_action, verified_coverage
):
    result = build_adjusted_bars(
        daily, combined_action, empty_quarantine(), verified_coverage,
        symbols=("600000.SH",),
    )
    expected_multiplier = (9.0 * (1.0 + 0.05 + 0.05) + 0.10) / 10.0
    assert result.iloc[1]["adjusted_close"] == pytest.approx(10.0 * expected_multiplier)


@pytest.mark.parametrize(
    "reason",
    ["unsupported_corporate_action", "cross_source_conflict", "incomplete"],
)
def test_quarantined_event_reanchors_and_marks_error(
    reason, daily_three_days, verified_coverage
):
    quarantine = quarantined_event(ex_date=date(2022, 6, 2), reason=reason)
    result = build_adjusted_bars(
        daily_three_days, _empty_actions(), quarantine, verified_coverage,
        symbols=("600000.SH",),
    )
    assert result.loc[1, "quality_severity"] == "ERROR"
    assert result.loc[1, "invalid_reason"] == reason
    assert result.loc[1, "adjusted_close"] == result.loc[1, "raw_close"]


def test_future_action_does_not_change_rows_before_ex_date(
    daily_90_days, verified_coverage
):
    before = build_adjusted_bars(
        daily_90_days, _empty_actions(), empty_quarantine(), verified_coverage,
        symbols=("600000.SH",),
    )
    after = build_adjusted_bars(
        daily_90_days, future_cash_action(day=days[70]), empty_quarantine(),
        verified_coverage, symbols=("600000.SH",),
    )
    pd.testing.assert_frame_equal(before.iloc[:70], after.iloc[:70])


def test_untrusted_coverage_marks_covered_window_as_error(daily, untrusted_coverage):
    result = build_adjusted_bars(
        daily, _empty_actions(), empty_quarantine(), untrusted_coverage,
        symbols=("600000.SH",),
    )
    assert set(result["quality_severity"]) == {"ERROR"}
    assert set(result["invalid_reason"]) == {"corporate_action_coverage_untrusted"}


def test_empty_daily_bar_returns_empty_canonical_frame(verified_coverage):
    result = build_adjusted_bars(
        pd.DataFrame(columns=DAILY_COLUMNS),
        _empty_actions(),
        empty_quarantine(),
        verified_coverage,
        symbols=(_SYMBOL,),
    )
    assert isinstance(result, pd.DataFrame)
    assert result.empty
    assert list(result.columns) == ADJUSTED_BAR_COLUMNS


def test_symbols_subset_excludes_other_symbols(verified_coverage):
    other_symbol = "000001.SZ"
    daily = pd.concat(
        [
            _daily_frame([10.0, 9.0], days[:2]),
            _daily_frame([20.0, 21.0], days[:2], symbol=other_symbol),
        ],
        ignore_index=True,
    )
    actions = pd.DataFrame(
        [
            _action_row(days[1], cash=1.0),
            _action_row(days[1], status="not_implemented", symbol=other_symbol),
        ],
        columns=CORPORATE_ACTION_COLUMNS,
    )
    result = build_adjusted_bars(
        daily, actions, empty_quarantine(),
        _coverage_frame("UNTRUSTED", reason="supplier_gap", symbol=other_symbol),
        symbols=(_SYMBOL,),
    )
    assert set(result["symbol"]) == {_SYMBOL}
    assert result["raw_close"].tolist() == [10.0, 9.0]
    assert result["adjusted_close"].tolist() == [10.0, 10.0]
    assert result["quality_severity"].tolist() == ["INFO", "INFO"]


def test_output_is_globally_sorted_and_deterministic(verified_coverage):
    other_symbol = "000001.SZ"
    daily = pd.concat(
        [
            _daily_frame([10.0, 11.0], days[:2]),
            _daily_frame([20.0, 19.0], days[:2], symbol=other_symbol),
        ],
        ignore_index=True,
    )
    actions = pd.DataFrame(
        [
            _action_row(days[1], cash=1.0, symbol=other_symbol),
            _action_row(days[1], bonus=1.0),
        ],
        columns=CORPORATE_ACTION_COLUMNS,
    )

    def build():
        return build_adjusted_bars(
            daily, actions, empty_quarantine(), verified_coverage,
            symbols=(_SYMBOL, other_symbol),
        )

    first, second = build(), build()
    assert list(zip(first["trade_date"], first["symbol"])) == [
        (days[0], other_symbol),
        (days[0], _SYMBOL),
        (days[1], other_symbol),
        (days[1], _SYMBOL),
    ]
    pd.testing.assert_frame_equal(first, second)


def test_day_after_break_chains_from_break_raw_close(verified_coverage):
    daily = _daily_frame([10.0, 9.0, 9.5], days[:3])
    quarantine = quarantined_event(ex_date=days[1], reason="incomplete")
    actions = pd.DataFrame(
        [_action_row(days[2], cash=0.5)], columns=CORPORATE_ACTION_COLUMNS
    )
    result = build_adjusted_bars(
        daily, actions, quarantine, verified_coverage, symbols=(_SYMBOL,)
    )
    assert result["adjusted_close"].tolist() == [10.0, 9.0, 10.0]
    assert result["quality_severity"].tolist() == ["INFO", "ERROR", "INFO"]
    assert result.loc[0, "applied_action_ids"] == "[]"
    assert result.loc[1, "applied_action_ids"] == "[]"
    assert result.loc[2, "applied_action_ids"] == (
        f'["{_SYMBOL}#{days[2].isoformat()}"]'
    )


def test_nan_filled_bonus_action_keeps_series_finite(
    daily_10_then_5, verified_coverage
):
    row = _action_row(days[1], bonus=1.0)
    for field in (
        "cash_dividend_per_share",
        "capitalization_ratio",
        "rights_issue_ratio",
    ):
        row[field] = None
    actions = pd.DataFrame([row], columns=CORPORATE_ACTION_COLUMNS).astype(
        {
            "cash_dividend_per_share": "float64",
            "capitalization_ratio": "float64",
            "rights_issue_ratio": "float64",
        }
    )
    result = build_adjusted_bars(
        daily_10_then_5, actions, empty_quarantine(), verified_coverage,
        symbols=(_SYMBOL,),
    )
    assert result["adjusted_close"].notna().all()
    assert result["adjustment_factor"].notna().all()
    expected_multiplier = (5.0 * (1.0 + 1.0) + 0.0) / 10.0
    assert result.iloc[1]["adjusted_close"] == pytest.approx(
        10.0 * expected_multiplier
    )
    assert result["adjusted_close"].tolist() == pytest.approx([10.0, 10.0])
    assert result["adjustment_factor"].tolist() == pytest.approx([1.0, 2.0])
    assert result["quality_severity"].tolist() == ["INFO", "INFO"]


def test_same_day_quarantine_keeps_smallest_reason(daily_three_days, verified_coverage):
    quarantine = pd.concat(
        [
            quarantined_event(ex_date=days[1], reason="supplier_missing"),
            quarantined_event(ex_date=days[1], reason="cross_source_conflict"),
        ],
        ignore_index=True,
    )

    def build(frame):
        return build_adjusted_bars(
            daily_three_days, _empty_actions(), frame, verified_coverage,
            symbols=(_SYMBOL,),
        )

    first = build(quarantine)
    flipped = build(quarantine.iloc[::-1].reset_index(drop=True))
    pd.testing.assert_frame_equal(first, flipped)
    assert first.loc[1, "invalid_reason"] == "cross_source_conflict"
    assert first.loc[1, "quality_severity"] == "ERROR"
