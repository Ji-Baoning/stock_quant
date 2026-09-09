"""Pure portfolio performance analytics over the backtest ledgers (Task 12).

``compute_metrics`` reduces the three committed frames -- daily equity, per-fill
costs and the benchmark index closes -- into a frozen :class:`PerformanceMetrics`
record.  The module must stay pure: it imports no factors, portfolio, execution
or research code and derives every figure strictly from the three frames, so a
single-row equity curve, empty fills or an empty benchmark never divide by zero.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.analytics.performance import compute_metrics

_DATES = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
]


def equity_fixture() -> pd.DataFrame:
    """A curve that peaks 25% up then pulls back to exactly -20% from the peak.

    ``total_equity`` walks ``100000 -> 125000 -> 100000 -> 125000 -> 100000`` so
    the deepest peak-to-trough decline is ``100000 / 125000 - 1 == -0.2`` while
    the cumulative return over the whole window is zero.
    """
    totals = [100000.0, 125000.0, 100000.0, 125000.0, 100000.0]
    cash = [total * 0.2 for total in totals]
    return pd.DataFrame(
        {
            "trade_date": _DATES,
            "cash": cash,
            "market_value": [total - c for total, c in zip(totals, cash)],
            "total_equity": totals,
            "stale_market_value": [0.0] * len(totals),
            "stale_days": [0] * len(totals),
        }
    )


def fills_fixture() -> pd.DataFrame:
    """Two fills: a 10 000 buy and a 1 000 sell.

    Commission is 5 per side (10 total); the sell pays 0.05% stamp tax on 1 000
    notional (0.5).  ``side`` uses the engine's ``BUY`` / ``SELL`` vocabulary.
    """
    return pd.DataFrame(
        [
            {
                "trade_date": _DATES[0],
                "fill_id": "F000001",
                "order_id": "O000001",
                "side": "BUY",
                "symbol": "600001.SH",
                "quantity": 1000,
                "price": 10.0,
                "commission": 5.0,
                "stamp_tax": 0.0,
            },
            {
                "trade_date": _DATES[1],
                "fill_id": "F000002",
                "order_id": "O000002",
                "side": "SELL",
                "symbol": "600001.SH",
                "quantity": 100,
                "price": 10.0,
                "commission": 5.0,
                "stamp_tax": 0.5,
            },
        ]
    )


def benchmark_fixture() -> pd.DataFrame:
    """Both index series over the same window (CSI300 +5/300 first row 3000)."""
    csi300 = [3000.0 + 60.0 * i for i in range(5)]  # 3000 -> 3240 (+8%)
    csi500 = [5000.0 + 50.0 * i for i in range(5)]  # 5000 -> 5200 (+4%)
    rows: list[dict] = []
    for day, (close300, close500) in zip(_DATES, zip(csi300, csi500)):
        rows.append(
            {
                "symbol": "000300.SH",
                "trade_date": day,
                "close": close300,
            }
        )
        rows.append(
            {
                "symbol": "000905.SH",
                "trade_date": day,
                "close": close500,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Brief Step-1 verbatim test
# --------------------------------------------------------------------------- #


def test_turnover_is_versioned_with_both_operands_persisted():
    # turnover-v1: numerator (buy+sell notional)/2 over mean total_equity,
    # with both operands persisted beside the ratio for recomputation.
    metrics = compute_metrics(equity_fixture(), fills_fixture(), benchmark_fixture())
    assert metrics.turnover_version == "turnover-v1"
    expected_numerator = (1000 * 10.0 + 100 * 10.0) / 2
    expected_denominator = (
        sum([100000.0, 125000.0, 100000.0, 125000.0, 100000.0]) / 5
    )
    assert metrics.turnover_numerator == pytest.approx(expected_numerator)
    assert metrics.turnover_denominator == pytest.approx(expected_denominator)
    assert metrics.turnover == pytest.approx(
        metrics.turnover_numerator / metrics.turnover_denominator
    )
    payload = metrics.to_dict()
    assert payload["turnover_version"] == "turnover-v1"
    assert payload["turnover_numerator"] == pytest.approx(expected_numerator)
    assert payload["turnover_denominator"] == pytest.approx(expected_denominator)


def test_drawdown_turnover_and_cost_decomposition():
    metrics = compute_metrics(equity_fixture(), fills_fixture(), benchmark_fixture())
    assert metrics.max_drawdown == pytest.approx(-0.2)
    assert metrics.total_commission == 10
    assert metrics.total_stamp_tax == pytest.approx(0.5)
    assert metrics.turnover >= 0
    # The fixture predates ``reference_price`` (legacy 9-column ledger): the
    # missing column defaults to ``price`` and so contributes zero slippage.
    assert metrics.slippage_estimate == 0.0


def test_legacy_fills_without_reference_price_report_zero_slippage():
    frame = fills_fixture().copy()
    assert "reference_price" not in frame.columns
    metrics = compute_metrics(equity_fixture(), frame, benchmark_fixture())
    assert metrics.slippage_estimate == 0.0


def test_slippage_is_realized_price_minus_reference_summed_over_fills():
    # A buy fills 0.02 above its raw open and a sell 0.02 below it: slippage
    # must be (10.02 - 10.00) * 1000 + (10.00 - 9.98) * 100 == 22.
    fills = fills_fixture().copy()
    fills.loc[0, ["price", "reference_price"]] = [10.02, 10.00]
    fills.loc[1, ["price", "reference_price"]] = [9.98, 10.00]
    metrics = compute_metrics(equity_fixture(), fills, benchmark_fixture())
    assert metrics.slippage_estimate == pytest.approx(22.0)


# --------------------------------------------------------------------------- #
# Edge conventions: short series and empty edge frames never divide by zero
# --------------------------------------------------------------------------- #


def test_single_row_equity_is_zero_safe():
    one = equity_fixture().iloc[[0]].reset_index(drop=True)
    metrics = compute_metrics(one, fills_fixture().iloc[0:0], benchmark_fixture())
    assert metrics.n_days == 1
    assert metrics.cumulative_return == 0.0
    assert metrics.annualized_return == 0.0
    assert metrics.annualized_volatility == 0.0
    assert metrics.max_drawdown == 0.0
    assert metrics.turnover == 0.0


def test_two_row_equity_and_empty_fills_benchmark_are_safe():
    two = equity_fixture().iloc[[0, 4]].reset_index(drop=True)
    empty_fills = fills_fixture().iloc[0:0]
    empty_bench = pd.DataFrame(columns=["symbol", "trade_date", "close"])
    metrics = compute_metrics(two, empty_fills, empty_bench)
    assert metrics.n_days == 2
    assert metrics.total_commission == 0.0
    assert metrics.total_stamp_tax == 0.0
    assert metrics.turnover == 0.0
    assert metrics.benchmark_symbol is None
    assert metrics.benchmark_total_return == 0.0
    assert metrics.benchmark_excess_return == 0.0
    assert metrics.max_drawdown == 0.0


def test_fewer_than_252_observations_do_not_divide_by_zero():
    # Five points over a four-interval window are annualized by actual obs.
    metrics = compute_metrics(equity_fixture(), fills_fixture(), benchmark_fixture())
    expected = (metrics.end_equity / metrics.start_equity) ** (
        252.0 / (metrics.n_days - 1)
    ) - 1.0
    assert metrics.annualized_return == pytest.approx(expected)
    assert metrics.annualized_volatility >= 0.0


def test_monotonic_rise_has_zero_max_drawdown():
    totals = [100000.0, 105000.0, 112000.0, 120000.0, 130000.0]
    frame = equity_fixture().copy()
    frame["total_equity"] = totals
    frame["market_value"] = totals
    metrics = compute_metrics(frame, fills_fixture(), benchmark_fixture())
    assert metrics.max_drawdown == 0.0
    assert metrics.cumulative_return == pytest.approx(0.3)


def test_benchmark_excess_uses_primary_series():
    metrics = compute_metrics(equity_fixture(), fills_fixture(), benchmark_fixture())
    assert metrics.benchmark_symbol == "000300.SH"
    # cumulative return here is 0, so excess is the negative benchmark return.
    assert metrics.benchmark_total_return == pytest.approx(0.08)
    assert metrics.benchmark_excess_return == pytest.approx(-0.08)


def test_cash_and_stale_ratios_are_window_end_fractions():
    metrics = compute_metrics(equity_fixture(), fills_fixture(), benchmark_fixture())
    assert metrics.cash_ratio_end == pytest.approx(0.2)
    assert metrics.stale_asset_ratio_end == 0.0


def test_missing_required_column_raises_value_error():
    broken = equity_fixture().drop(columns=["stale_market_value"])
    with pytest.raises(ValueError):
        compute_metrics(broken, fills_fixture(), benchmark_fixture())


def test_datetime64_dates_are_normalized_before_arithmetic():
    # DuckDB surfaces DATE columns as datetime64[us]; the metrics must cope.
    frame = equity_fixture()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    metrics = compute_metrics(frame, fills_fixture(), benchmark_fixture())
    assert isinstance(metrics.start_date, date)
    assert metrics.max_drawdown == pytest.approx(-0.2)
