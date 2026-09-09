"""Trusted 60-session risk estimation from point-in-time adjusted closes.

``estimate_risk`` builds each requested symbol's risk input from the final
``risk_lookback_days`` confirmed market sessions ending at the signal date:
normal rows need a finite positive close with non-ERROR quality and no
missing reason; ``suspended_verified`` rows carry the previous close forward,
contribute a zero path return and never count toward the 40 real closes; any
other gap, quality error or suspension without a prior trusted close makes
that symbol's risk input invalid with a stable reason -- never a silent
fallback.  The estimate never reads a session after the signal date, so
appending future prices cannot change a past signal's risk.

All tests are offline and in-memory.
"""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from stock_quant.portfolio.buffered_models import BufferedRiskWeightedPolicy
from stock_quant.portfolio.risk_estimation import (
    RISK_ESTIMATE_COLUMNS,
    RISK_INVALID_INSUFFICIENT_REAL_CLOSES,
    RISK_INVALID_UNTRUSTED_MISSING,
    RiskInputError,
    estimate_risk,
)

SYMBOL = "000001.SZ"

_INPUT_COLUMNS = [
    "trade_date",
    "symbol",
    "adjusted_close",
    "quality_severity",
    "missing_reason",
]


def _weekdays(count: int, start: date = date(2020, 1, 1)) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@pytest.fixture
def sixty_sessions() -> list[date]:
    return _weekdays(60)


@pytest.fixture
def risk_rows(sixty_sessions) -> pd.DataFrame:
    """Sixty trusted closes with a deterministic alternating drift."""
    closes = [
        10.0 * (1.0 + 0.01 * ((-1.0) ** position)) for position in range(60)
    ]
    return pd.DataFrame(
        {
            "trade_date": sixty_sessions,
            "symbol": [SYMBOL] * 60,
            "adjusted_close": closes,
            "quality_severity": ["INFO"] * 60,
            "missing_reason": [None] * 60,
        },
        columns=_INPUT_COLUMNS,
    )


@pytest.fixture
def request_kwargs(sixty_sessions, risk_rows) -> dict:
    return {
        "signal_date": sixty_sessions[-1],
        "symbols": (SYMBOL,),
        "observations": risk_rows,
        "market_sessions": sixty_sessions,
        "policy": BufferedRiskWeightedPolicy(),
    }


def mark_last_twenty_as_verified_suspension(rows: pd.DataFrame) -> pd.DataFrame:
    """Carry the previous close forward over the final twenty sessions."""
    frame = rows.copy()
    for position in range(len(frame) - 20, len(frame)):
        frame.iloc[position, frame.columns.get_loc("missing_reason")] = (
            "suspended_verified"
        )
        frame.iloc[position, frame.columns.get_loc("adjusted_close")] = (
            float(frame.iloc[position - 1]["adjusted_close"])
        )
    return frame


def future_rows(sixty_sessions=None) -> pd.DataFrame:
    """Five trusted sessions strictly after the signal date."""
    days = _weekdays(5, start=date(2030, 1, 1))
    return pd.DataFrame(
        {
            "trade_date": days,
            "symbol": [SYMBOL] * 5,
            "adjusted_close": [50.0] * 5,
            "quality_severity": ["INFO"] * 5,
            "missing_reason": [None] * 5,
        },
        columns=_INPUT_COLUMNS,
    )


# ---------------------------------------------------------------------------
# Plan Step 1: boundary and suspension semantics
# ---------------------------------------------------------------------------


def test_exactly_forty_real_closes_is_valid(sixty_sessions, risk_rows):
    frame = mark_last_twenty_as_verified_suspension(risk_rows)
    result = estimate_risk(signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
                           observations=frame, market_sessions=sixty_sessions,
                           policy=BufferedRiskWeightedPolicy())
    row = result.iloc[0]
    assert row.real_close_observations == 40
    assert row.suspension_carry_days == 20
    assert row.risk_is_valid


def test_unknown_missing_row_invalidates_risk(sixty_sessions, risk_rows):
    frame = risk_rows.drop(risk_rows.index[30])
    result = estimate_risk(signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
                           observations=frame, market_sessions=sixty_sessions,
                           policy=BufferedRiskWeightedPolicy())
    assert not result.iloc[0].risk_is_valid
    assert result.iloc[0].risk_invalid_reason == "untrusted_missing_observation"


def test_thirty_nine_real_closes_is_insufficient(sixty_sessions, risk_rows):
    # twenty-one verified carry days leave only 39 real closes: no untrusted
    # observation exists anywhere in the window, so the sole failure is the
    # real-observation floor.
    frame = risk_rows.copy()
    for position in range(len(frame) - 21, len(frame)):
        frame.iloc[position, frame.columns.get_loc("missing_reason")] = (
            "suspended_verified"
        )
        frame.iloc[position, frame.columns.get_loc("adjusted_close")] = (
            float(frame.iloc[position - 1]["adjusted_close"])
        )
    result = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=frame, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    row = result.iloc[0]
    assert row.real_close_observations == 39
    assert row.suspension_carry_days == 21
    assert not row.risk_is_valid
    assert row.risk_invalid_reason == "insufficient_real_close_observations"


def test_quality_error_row_invalidates_risk(sixty_sessions, risk_rows):
    frame = risk_rows.copy()
    frame.iloc[10, frame.columns.get_loc("quality_severity")] = "ERROR"
    result = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=frame, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    assert not result.iloc[0].risk_is_valid
    assert result.iloc[0].risk_invalid_reason == RISK_INVALID_UNTRUSTED_MISSING


def test_nonpositive_or_missing_close_invalidates_risk(sixty_sessions, risk_rows):
    for bad in (0.0, -1.0, float("nan")):
        frame = risk_rows.copy()
        frame.iloc[10, frame.columns.get_loc("adjusted_close")] = bad
        result = estimate_risk(
            signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
            observations=frame, market_sessions=sixty_sessions,
            policy=BufferedRiskWeightedPolicy(),
        )
        assert not result.iloc[0].risk_is_valid
        assert result.iloc[0].risk_invalid_reason == RISK_INVALID_UNTRUSTED_MISSING


def test_unverified_missing_reason_invalidates_risk(sixty_sessions, risk_rows):
    frame = risk_rows.copy()
    frame.iloc[10, frame.columns.get_loc("missing_reason")] = "unknown_gap"
    result = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=frame, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    assert not result.iloc[0].risk_is_valid
    assert result.iloc[0].risk_invalid_reason == RISK_INVALID_UNTRUSTED_MISSING


def test_suspension_on_first_window_session_has_no_prior_close(
    sixty_sessions, risk_rows
):
    frame = risk_rows.copy()
    frame.iloc[0, frame.columns.get_loc("missing_reason")] = "suspended_verified"
    frame.iloc[0, frame.columns.get_loc("adjusted_close")] = 10.0
    result = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=frame, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    assert not result.iloc[0].risk_is_valid
    assert result.iloc[0].risk_invalid_reason == RISK_INVALID_UNTRUSTED_MISSING


def test_suspension_requires_positive_carried_close(sixty_sessions, risk_rows):
    frame = mark_last_twenty_as_verified_suspension(risk_rows)
    frame.iloc[45, frame.columns.get_loc("adjusted_close")] = float("nan")
    result = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=frame, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    assert not result.iloc[0].risk_is_valid
    assert result.iloc[0].risk_invalid_reason == RISK_INVALID_UNTRUSTED_MISSING


def test_symbol_without_any_rows_is_invalid(sixty_sessions, risk_rows):
    result = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=("600000.SH",),
        observations=risk_rows, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    row = result.iloc[0]
    assert row.symbol == "600000.SH"
    assert not row.risk_is_valid
    assert row.risk_invalid_reason == RISK_INVALID_UNTRUSTED_MISSING
    assert row.real_close_observations == 0


# ---------------------------------------------------------------------------
# Plan Step 4: no-lookahead and determinism regressions
# ---------------------------------------------------------------------------


def test_appending_future_prices_cannot_change_signal_risk(request_kwargs):
    before = estimate_risk(**request_kwargs)
    request_kwargs["observations"] = pd.concat(
        [request_kwargs["observations"], future_rows()], ignore_index=True
    )
    assert_frame_equal(estimate_risk(**request_kwargs), before)


def test_input_row_order_does_not_change_output(request_kwargs):
    shuffled = {
        **request_kwargs,
        "observations": request_kwargs["observations"].sample(
            frac=1, random_state=7
        ),
    }
    assert_frame_equal(estimate_risk(**shuffled), estimate_risk(**request_kwargs))


# ---------------------------------------------------------------------------
# Window selection, output contract and hard input errors
# ---------------------------------------------------------------------------


def test_window_is_exactly_the_final_policy_sessions(sixty_sessions, risk_rows):
    # thirty trusted sessions strictly before the fixture window: the final
    # sixty sessions of the extended history are exactly the fixture window,
    # so both estimates must agree tick for tick.
    earlier_sessions = _weekdays(30, start=date(2019, 11, 1))
    while earlier_sessions[-1] >= sixty_sessions[0]:
        earlier_sessions = earlier_sessions[:-1]
    earlier = pd.DataFrame(
        {
            "trade_date": earlier_sessions,
            "symbol": [SYMBOL] * len(earlier_sessions),
            "adjusted_close": [9.0] * len(earlier_sessions),
            "quality_severity": ["INFO"] * len(earlier_sessions),
            "missing_reason": [None] * len(earlier_sessions),
        },
        columns=_INPUT_COLUMNS,
    )
    long_observations = pd.concat([earlier, risk_rows], ignore_index=True)
    long_sessions = earlier_sessions + sixty_sessions
    from_long = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=long_observations, market_sessions=long_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    from_window = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=risk_rows, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    row = from_long.iloc[0]
    assert row.window_start == sixty_sessions[0]
    assert row.window_end == sixty_sessions[-1]
    assert_frame_equal(from_long, from_window)


def test_window_selection_uses_policy_lookback(request_kwargs):
    result = estimate_risk(**request_kwargs)
    row = result.iloc[0]
    assert row.window_start == request_kwargs["market_sessions"][0]
    assert row.window_end == request_kwargs["signal_date"]


def test_output_columns_and_symbol_order(sixty_sessions, risk_rows):
    rows = pd.concat(
        [
            risk_rows,
            pd.DataFrame(
                {
                    "trade_date": sixty_sessions,
                    "symbol": ["000002.SZ"] * 60,
                    "adjusted_close": [5.0] * 60,
                    "quality_severity": ["INFO"] * 60,
                    "missing_reason": [None] * 60,
                },
                columns=_INPUT_COLUMNS,
            ),
            pd.DataFrame(
                {
                    "trade_date": sixty_sessions,
                    "symbol": ["600000.SH"] * 60,
                    "adjusted_close": [7.0] * 60,
                    "quality_severity": ["INFO"] * 60,
                    "missing_reason": [None] * 60,
                },
                columns=_INPUT_COLUMNS,
            ),
        ],
        ignore_index=True,
    )
    result = estimate_risk(
        signal_date=sixty_sessions[-1],
        symbols=("600000.SH", "000001.SZ", "000002.SZ"),
        observations=rows,
        market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    assert list(result.columns) == list(RISK_ESTIMATE_COLUMNS)
    assert result["symbol"].tolist() == ["000001.SZ", "000002.SZ", "600000.SH"]
    assert result["risk_is_valid"].all()
    assert result["risk_invalid_reason"].tolist() == ["", "", ""]


def test_constant_prices_floor_the_annualized_volatility(
    sixty_sessions, risk_rows
):
    frame = risk_rows.copy()
    frame["adjusted_close"] = [10.0] * 60
    result = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=frame, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    row = result.iloc[0]
    assert row.risk_is_valid
    assert row.raw_annualized_volatility == 0.0
    assert row.applied_annualized_volatility == pytest.approx(0.10)


def test_raw_volatility_is_preserved_above_the_floor(request_kwargs):
    result = estimate_risk(**request_kwargs)
    row = result.iloc[0]
    closes = request_kwargs["observations"]["adjusted_close"].to_numpy()
    returns = closes[1:] / closes[:-1] - 1.0
    expected = float(np.std(returns, ddof=1) * np.sqrt(252))
    assert row.raw_annualized_volatility == pytest.approx(expected)
    assert row.applied_annualized_volatility == pytest.approx(expected)


def test_suspension_carry_days_contribute_zero_returns(sixty_sessions, risk_rows):
    carried = mark_last_twenty_as_verified_suspension(risk_rows)
    plain = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=carried, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    ).iloc[0]
    # the zero-return carry days depress the raw volatility below the
    # all-real-close path but the estimate stays valid at 40 real closes
    full = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=risk_rows, market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    ).iloc[0]
    assert plain.risk_is_valid and full.risk_is_valid
    assert plain.raw_annualized_volatility < full.raw_annualized_volatility
    assert plain.applied_annualized_volatility >= Decimal("0.10")


def test_duplicate_symbol_date_rows_raise(risk_rows, sixty_sessions):
    duplicated = pd.concat([risk_rows, risk_rows.iloc[[30]]], ignore_index=True)
    with pytest.raises(RiskInputError, match="duplicate"):
        estimate_risk(
            signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
            observations=duplicated, market_sessions=sixty_sessions,
            policy=BufferedRiskWeightedPolicy(),
        )


def test_duplicate_rows_after_the_signal_date_are_immune(
    risk_rows, sixty_sessions
):
    # future rows never enter the window, so even a future duplicate cannot
    # change (or fail) a past signal's estimate
    future = future_rows()
    duplicated_future = pd.concat([future, future], ignore_index=True)
    result = estimate_risk(
        signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
        observations=pd.concat([risk_rows, duplicated_future],
                               ignore_index=True),
        market_sessions=sixty_sessions,
        policy=BufferedRiskWeightedPolicy(),
    )
    assert result.iloc[0].risk_is_valid


def test_signal_date_outside_market_sessions_raises(risk_rows, sixty_sessions):
    with pytest.raises(RiskInputError, match="confirmed market session"):
        estimate_risk(
            signal_date=sixty_sessions[-1] + timedelta(days=1),
            symbols=(SYMBOL,), observations=risk_rows,
            market_sessions=sixty_sessions,
            policy=BufferedRiskWeightedPolicy(),
        )


def test_wrong_input_columns_raise(risk_rows, sixty_sessions):
    with pytest.raises(RiskInputError, match="columns"):
        estimate_risk(
            signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
            observations=risk_rows.drop(columns=["missing_reason"]),
            market_sessions=sixty_sessions,
            policy=BufferedRiskWeightedPolicy(),
        )
    with pytest.raises(RiskInputError, match="columns"):
        estimate_risk(
            signal_date=sixty_sessions[-1], symbols=(SYMBOL,),
            observations=risk_rows.assign(extra=[1] * len(risk_rows)),
            market_sessions=sixty_sessions,
            policy=BufferedRiskWeightedPolicy(),
        )


def test_invalid_reason_constants_are_stable():
    assert RISK_INVALID_UNTRUSTED_MISSING == "untrusted_missing_observation"
    assert (
        RISK_INVALID_INSUFFICIENT_REAL_CLOSES
        == "insufficient_real_close_observations"
    )
