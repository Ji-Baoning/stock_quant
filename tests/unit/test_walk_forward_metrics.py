"""OOS daily returns, per-fold risk/execution metrics and same-path costs.

Every confirmed open OOS day has exactly one portfolio return; the fold's
first-day return uses the fixed ``initial_equity`` as its predecessor and
every later return the prior day's ``net_equity_after_cost``.  A missing
market-open day, a duplicate date or a non-positive equity mark is an
``OOSIntegrityError`` -- never a silently smaller sample.  Per-fold risk uses
only the fold's own mark-to-market ``net_equity_after_cost`` (cross-fold
drawdown/Calmar are forbidden and no aggregate path field exists here), and
the same-path cost replay removes explicit commissions/taxes only, never
touching the realized fill set.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.research.walk_forward.metrics import (
    AggregateOOSMetrics,
    OOSIntegrityError,
    aggregate_oos_returns,
    build_daily_returns,
    compute_fold_metrics,
    compute_same_path_costs,
)
from stock_quant.research.walk_forward.schedule import FoldWindow, fold_id_for

_D1, _D2, _D3, _D4 = (
    date(2020, 1, 2),
    date(2020, 1, 3),
    date(2020, 1, 6),
    date(2020, 1, 7),
)


def equity() -> pd.DataFrame:
    """Four open days: rise to 120 then fall to 90 from an initial 100."""
    return pd.DataFrame(
        {
            "trade_date": [_D1, _D2, _D3, _D4],
            "net_equity_after_cost": [120.0, 132.0, 118.8, 90.0],
        }
    )


def one_day_equity() -> pd.DataFrame:
    return pd.DataFrame(
        {"trade_date": [_D1], "net_equity_after_cost": [100.0]}
    )


def fills() -> pd.DataFrame:
    """Two fills at the reference price (zero slippage)."""
    return pd.DataFrame(
        [
            {
                "trade_date": _D1,
                "fill_id": "F000001",
                "order_id": "O000001",
                "side": "BUY",
                "symbol": "600001.SH",
                "quantity": 100,
                "price": 10.0,
                "commission": 5.0,
                "stamp_tax": 0.0,
                "reference_price": 10.0,
            },
            {
                "trade_date": _D3,
                "fill_id": "F000002",
                "order_id": "O000002",
                "side": "SELL",
                "symbol": "600001.SH",
                "quantity": 100,
                "price": 10.5,
                "commission": 5.0,
                "stamp_tax": 5.25,
                "reference_price": 10.5,
            },
        ]
    )


def fills_with_costs() -> pd.DataFrame:
    frame = fills().copy()
    frame["reference_price"] = frame["price"]  # zero slippage: pure fees
    return frame


def three_orders() -> pd.DataFrame:
    rows = [
        (_D1, "O000001", "BUY", "600001.SH", 100),
        (_D1, "O000002", "SELL", "600001.SH", 200),
        (_D2, "O000003", "BUY", "600002.SH", 100),
    ]
    return pd.DataFrame(
        [
            {
                "trade_date": trade_date,
                "order_id": order_id,
                "side": side,
                "symbol": symbol,
                "quantity": quantity,
            }
            for trade_date, order_id, side, symbol, quantity in rows
        ]
    )


def one_partial_one_full() -> pd.DataFrame:
    """One fully rejected order, one partially filled, one fully filled."""
    rows = [
        ("O000001", 100, 0, 100, "REJECTED"),
        ("O000002", 200, 100, 100, "PARTIAL"),
        ("O000003", 100, 100, 0, "FILLED"),
    ]
    return pd.DataFrame(
        [
            {
                "order_id": order_id,
                "planned_quantity": planned,
                "filled_quantity": filled,
                "rejected_quantity": rejected,
                "status": status,
            }
            for order_id, planned, filled, rejected, status in rows
        ]
    )


def make_fold(first: date, last: date, fold_id: str = "f" * 64) -> FoldWindow:
    return FoldWindow(
        fold_id=fold_id,
        calendar_start=date(first.year, 1, 1),
        calendar_end=date(first.year, 12, 31),
        first_trading_day=first,
        last_trading_day=last,
        warmup_calendar_start=date(first.year - 3, 1, 1),
        warmup_calendar_end=date(first.year - 1, 12, 31),
        warmup_session_count=756,
        oos_session_count=4,
    )


# ---------------------------------------------------------------------------
# Daily returns
# ---------------------------------------------------------------------------


def test_first_oos_return_uses_initial_equity():
    frame = pd.DataFrame(
        {
            "trade_date": [_D1, _D2],
            "net_equity_after_cost": [101.0, 99.99],
        }
    )
    result = build_daily_returns(
        frame, initial_equity=100.0, expected_open_days=(_D1, _D2)
    )
    assert result["daily_return"].tolist() == pytest.approx([0.01, -0.01])


def test_missing_market_open_day_is_integrity_failure():
    with pytest.raises(OOSIntegrityError, match="2020-01-03"):
        build_daily_returns(
            one_day_equity(),
            initial_equity=100.0,
            expected_open_days=(_D1, _D2),
        )


def test_duplicate_equity_row_is_integrity_failure():
    frame = pd.DataFrame(
        {
            "trade_date": [_D1, _D1],
            "net_equity_after_cost": [100.0, 101.0],
        }
    )
    with pytest.raises(OOSIntegrityError, match="duplicate"):
        build_daily_returns(frame, initial_equity=100.0, expected_open_days=(_D1,))


def test_equity_row_outside_expected_open_days_is_integrity_failure():
    with pytest.raises(OOSIntegrityError, match="2020-01-06"):
        build_daily_returns(
            equity(), initial_equity=100.0, expected_open_days=(_D1, _D2)
        )


def test_non_positive_or_non_finite_equity_is_integrity_failure():
    frame = pd.DataFrame(
        {"trade_date": [_D1, _D2], "net_equity_after_cost": [100.0, 0.0]}
    )
    with pytest.raises(OOSIntegrityError, match="positive"):
        build_daily_returns(frame, initial_equity=100.0, expected_open_days=(_D1, _D2))
    frame.loc[1, "net_equity_after_cost"] = float("nan")
    with pytest.raises(OOSIntegrityError, match="positive"):
        build_daily_returns(frame, initial_equity=100.0, expected_open_days=(_D1, _D2))


# ---------------------------------------------------------------------------
# Fold metrics: risk and execution quality
# ---------------------------------------------------------------------------


def test_partial_order_counts_in_reject_rate():
    value = compute_fold_metrics(
        equity(),
        fills(),
        submitted_orders=three_orders(),
        order_diffs=one_partial_one_full(),
    )
    assert value.reject_rate == pytest.approx(2 / 3)
    assert value.fully_rejected_order_count == 1
    assert value.partially_filled_order_count == 1


def test_unfilled_quantity_rate_uses_requested_quantities():
    value = compute_fold_metrics(
        equity(),
        fills(),
        submitted_orders=three_orders(),
        order_diffs=one_partial_one_full(),
    )
    assert value.unfilled_quantity_rate == pytest.approx(200 / 400)


def test_no_submitted_orders_makes_reject_rates_null():
    value = compute_fold_metrics(
        equity(),
        fills().iloc[0:0],
        submitted_orders=pd.DataFrame(columns=["order_id"]),
        order_diffs=pd.DataFrame(columns=["order_id", "planned_quantity",
                                          "rejected_quantity", "status"]),
    )
    assert value.reject_rate is None
    assert value.unfilled_quantity_rate is None
    assert value.submitted_order_count == 0


def test_fold_calendar_return_and_drawdown_use_net_equity_after_cost():
    value = compute_fold_metrics(
        equity(), fills(), initial_equity=100.0, expected_open_days=(_D1, _D2, _D3, _D4)
    )
    assert value.fold_calendar_return == pytest.approx(90.0 / 100.0 - 1.0)
    # running max is 132 at the third day, final mark is 90: dd = 90/132 - 1
    assert value.per_fold_max_drawdown == pytest.approx(90.0 / 132.0 - 1.0)
    assert value.observation_count == 4


def test_single_observation_volatility_and_sharpe_are_null():
    value = compute_fold_metrics(
        one_day_equity(), fills().iloc[0:0], initial_equity=100.0
    )
    assert value.observation_count == 1
    assert value.annualized_volatility is None
    assert value.sharpe_zero_rf is None


def test_zero_std_returns_leave_volatility_and_sharpe_null():
    flat = pd.DataFrame(
        {
            "trade_date": [_D1, _D2, _D3],
            "net_equity_after_cost": [100.0, 100.0, 100.0],
        }
    )
    value = compute_fold_metrics(flat, fills().iloc[0:0], initial_equity=100.0)
    assert value.annualized_volatility is None
    assert value.sharpe_zero_rf is None


def test_turnover_is_versioned_with_both_operands_persisted():
    value = compute_fold_metrics(
        equity(), fills(), initial_equity=100.0,
        expected_open_days=(_D1, _D2, _D3, _D4),
    )
    assert value.turnover_version == "turnover-v1"
    assert value.turnover_numerator == pytest.approx((100 * 10.0 + 100 * 10.5) / 2)
    assert value.turnover_denominator == pytest.approx(
        (120.0 + 132.0 + 118.8 + 90.0) / 4
    )
    assert value.turnover == pytest.approx(
        value.turnover_numerator / value.turnover_denominator
    )


def test_slippage_estimate_sums_adverse_reference_drift():
    slipped = fills().copy()
    slipped.loc[0, ["price", "reference_price"]] = [10.02, 10.00]
    slipped.loc[1, ["price", "reference_price"]] = [10.48, 10.50]
    value = compute_fold_metrics(equity(), slipped, initial_equity=100.0)
    assert value.slippage_estimate == pytest.approx(0.02 * 100 + 0.02 * 100)


# ---------------------------------------------------------------------------
# Same-path cost replay
# ---------------------------------------------------------------------------


def test_same_path_cost_replay_never_changes_fills():
    result = compute_same_path_costs(
        equity(), fills_with_costs(), initial_equity=1_000_000
    )
    assert result.fill_count == len(fills_with_costs())
    assert result.total_explicit_cost == pytest.approx(
        fills_with_costs()[["commission", "stamp_tax"]].to_numpy().sum()
    )


def test_gross_replay_adds_explicit_costs_back_to_the_same_path():
    frame = pd.DataFrame(
        {
            "trade_date": [_D1, _D2],
            "net_equity_after_cost": [99.0, 108.0],
        }
    )
    fill_rows = pd.DataFrame(
        [
            {
                "trade_date": _D1,
                "fill_id": "F000001",
                "order_id": "O000001",
                "side": "BUY",
                "symbol": "600001.SH",
                "quantity": 100,
                "price": 10.0,
                "commission": 1.0,
                "stamp_tax": 0.0,
                "reference_price": 10.0,
            }
        ]
    )
    result = compute_same_path_costs(frame, fill_rows, initial_equity=100.0)
    # gross path adds the 1.0 commission back to every mark from day one on
    assert result.gross_return_before_explicit_cost == pytest.approx(109.0 / 100.0 - 1)
    assert result.net_return == pytest.approx(108.0 / 100.0 - 1)
    assert result.explicit_cost_drag == pytest.approx(
        result.gross_return_before_explicit_cost - result.net_return
    )
    assert result.slippage_impact == 0.0


def test_explicit_cost_ratio_uses_the_fixed_initial_equity():
    result = compute_same_path_costs(equity(), fills(), initial_equity=100.0)
    assert result.explicit_cost_ratio == pytest.approx(
        result.total_explicit_cost / 100.0
    )


# ---------------------------------------------------------------------------
# Aggregate OOS metrics
# ---------------------------------------------------------------------------


def per_fold_returns(dates, values):
    return pd.DataFrame({"trade_date": list(dates), "daily_return": list(values)})


def test_aggregate_concatenates_executed_folds_without_overlap():
    fold_a = make_fold(_D1, _D2)
    fold_b = make_fold(_D3, _D4, fold_id="a" * 64)
    value = aggregate_oos_returns(
        [
            (fold_a, per_fold_returns([_D1, _D2], [0.01, -0.01])),
            (fold_b, per_fold_returns([_D3, _D4], [0.02, 0.03])),
        ]
    )
    n = 4
    product = 1.01 * 0.99 * 1.02 * 1.03
    assert value.oos_return_observations == n
    assert value.annualization_observations == n
    assert value.aggregate_return == pytest.approx(product - 1.0)
    assert value.annualized_return == pytest.approx(product ** (252 / n) - 1.0)
    assert value.annualized_volatility == pytest.approx(
        pd.Series([0.01, -0.01, 0.02, 0.03]).std(ddof=1) * (252 ** 0.5)
    )


def test_aggregate_rejects_duplicate_dates_across_folds():
    fold_a = make_fold(_D1, _D2)
    fold_b = make_fold(_D2, _D3, fold_id="a" * 64)
    with pytest.raises(OOSIntegrityError, match="overlap|duplicate"):
        aggregate_oos_returns(
            [
                (fold_a, per_fold_returns([_D1, _D2], [0.01, 0.01])),
                (fold_b, per_fold_returns([_D2, _D3], [0.02, 0.02])),
            ]
        )


def test_aggregate_rejects_dates_outside_their_fold():
    fold_a = make_fold(_D1, _D2)
    with pytest.raises(OOSIntegrityError, match="outside"):
        aggregate_oos_returns(
            [(fold_a, per_fold_returns([_D1, _D3], [0.01, 0.02]))]
        )


def test_aggregate_has_no_global_drawdown_or_calmar_field():
    fold_a = make_fold(_D1, _D2)
    value = aggregate_oos_returns(
        [(fold_a, per_fold_returns([_D1, _D2], [0.01, -0.01]))]
    )
    payload = value.model_dump()
    assert "aggregate_max_drawdown" not in payload
    assert "calmar" not in payload
    assert not any("drawdown" in key for key in payload)
    assert isinstance(value, AggregateOOSMetrics)


def test_aggregate_with_a_single_observation_has_null_risk():
    fold_a = make_fold(_D1, _D2)
    value = aggregate_oos_returns([(fold_a, per_fold_returns([_D1], [0.01]))])
    assert value.oos_return_observations == 1
    assert value.annualized_volatility is None
    assert value.sharpe_zero_rf is None


def test_fold_id_helper_is_deterministic():
    assert fold_id_for(
        calendar_start=date(2020, 1, 1),
        calendar_end=date(2020, 12, 31),
        first_trading_day=_D1,
        last_trading_day=_D2,
    ) == fold_id_for(
        calendar_start=date(2020, 1, 1),
        calendar_end=date(2020, 12, 31),
        first_trading_day=_D1,
        last_trading_day=_D2,
    )
