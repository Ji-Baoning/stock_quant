"""Pure portfolio performance metrics over the backtest ledgers (Task 12).

``compute_metrics(daily_equity, fills, benchmark)`` is a boundary-clean reducer:
it takes exactly the three committed frames -- the daily equity curve, the
per-fill cost ledger and the benchmark index closes -- and returns one frozen
:class:`PerformanceMetrics`.  It performs no I/O and imports no factors,
portfolio, execution, backtest or research module, so the analytics never
duplicate trading logic and a later CLI adapter can drop it into the research
runner's injected ``analytics`` port.  The input column tuples below mirror the
engine's canonical ``EQUITY_COLUMNS`` / ``FILL_COLUMNS`` / ``BENCHMARK_COLUMNS``
(``src/stock_quant/backtest/engine.py``) and are repeated here so this module
stays import-free of the backtest layer.

Conventions (documented, never silently NaN):

- ``trade_date`` arrives as DuckDB ``datetime64[us]`` and is normalized to a
  plain ``date`` before any arithmetic.
- Returns are geometric over ``total_equity``.  With ``n`` observations the
  window spans ``n - 1`` return intervals, so cumulative return is
  ``end / start - 1`` and annualized return is
  ``(end / start) ** (252 / (n - 1)) - 1`` -- annualization by actual
  observations, which is meaningful for windows far shorter than 252 days.
- Annualized volatility is the sample standard deviation of daily simple
  returns scaled by ``sqrt(252)``; with fewer than two returns it is ``0``.
- Maximum drawdown is the minimum peak-to-trough decline of ``total_equity``
  (a value in ``[-1, 0]``; ``0`` for a never-declining curve).
- ``turnover`` is the one-way traded notional
  ``(buy notional + sell notional) / 2`` divided by the window's mean
  ``total_equity``: fully replacing the book once reads as ``1.0``.
- Cash and stale-asset ratios are window-end fractions:
  ``cash / total_equity`` and ``stale_market_value / total_equity``.
- ``slippage_estimate`` is the realized slippage cost summed over fills: a buy
  pays ``price - reference_price`` per share and a sell loses
  ``reference_price - price``.  The reference is the cent-quantized execution
  open (a tick price), so a zero-slippage scenario prices every fill exactly at
  its reference and reports zero.  Legacy fills recorded without a reference
  price (no ``reference_price`` column) default to zero slippage.
- Benchmark excess is measured against the primary index
  (``000300.SH`` when present, else the first symbol in the benchmark frame)
  over the window; an empty benchmark or missing primary symbol reports
  ``benchmark_symbol=None`` and zero return/excess.
- Edge frames are safe: an empty fills frame yields zero costs and turnover, an
  empty benchmark yields zero benchmark figures, a single-row equity curve
  yields zero returns, and a non-positive initial equity guards every division.

Unfilled-order counts and holding concentration are deliberately *not* part of
``PerformanceMetrics``: fills are all-filled and the account equity is not
broken down by symbol, so those two quantities cannot be derived purely from
the three frames and belong to the richer experiment-report input.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import sqrt

import numpy as np
import pandas as pd

#: Trading days assumed per calendar year for annualization.
TRADING_DAYS_PER_YEAR = 252

#: Canonical daily-equity ledger columns (mirrors engine.EQUITY_COLUMNS).
DAILY_EQUITY_COLUMNS = (
    "trade_date",
    "cash",
    "market_value",
    "total_equity",
    "stale_market_value",
    "stale_days",
)

#: Canonical per-fill cost ledger columns (mirrors engine.FILL_COLUMNS).
FILL_COLUMNS = (
    "trade_date",
    "fill_id",
    "order_id",
    "side",
    "symbol",
    "quantity",
    "price",
    "commission",
    "stamp_tax",
    "reference_price",
)

#: Columns every fill frame must carry, including legacy ledgers recorded
#: before ``reference_price`` existed (which default to their fill price and
#: therefore contribute zero slippage).
FILL_REQUIRED_COLUMNS = FILL_COLUMNS[:-1]

#: Canonical benchmark frame columns (mirrors engine.BENCHMARK_COLUMNS).
BENCHMARK_COLUMNS = ("symbol", "trade_date", "close")

#: The index treated as the primary comparison benchmark when present.
PRIMARY_BENCHMARK_SYMBOL = "000300.SH"

#: Engine side vocabulary (mirrors backtest.models.BUY / SELL).
BUY = "BUY"
SELL = "SELL"

_EQUITY_NUMERIC = ("cash", "market_value", "total_equity", "stale_market_value")
_FILL_NUMERIC = ("quantity", "price", "commission", "stamp_tax", "reference_price")


@dataclass(frozen=True)
class PerformanceMetrics:
    """Frozen summary of one backtest window computed from the three frames."""

    n_days: int
    start_date: date
    end_date: date
    start_equity: float
    end_equity: float
    cumulative_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    turnover: float
    total_commission: float
    total_stamp_tax: float
    slippage_estimate: float
    cash_ratio_end: float
    stale_asset_ratio_end: float
    benchmark_symbol: str | None
    benchmark_total_return: float
    benchmark_excess_return: float

    def to_dict(self) -> dict[str, object]:
        """A JSON-ready mapping of every field (dates as ISO strings)."""
        return {
            "n_days": self.n_days,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "start_equity": self.start_equity,
            "end_equity": self.end_equity,
            "cumulative_return": self.cumulative_return,
            "annualized_return": self.annualized_return,
            "annualized_volatility": self.annualized_volatility,
            "max_drawdown": self.max_drawdown,
            "turnover": self.turnover,
            "total_commission": self.total_commission,
            "total_stamp_tax": self.total_stamp_tax,
            "slippage_estimate": self.slippage_estimate,
            "cash_ratio_end": self.cash_ratio_end,
            "stale_asset_ratio_end": self.stale_asset_ratio_end,
            "benchmark_symbol": self.benchmark_symbol,
            "benchmark_total_return": self.benchmark_total_return,
            "benchmark_excess_return": self.benchmark_excess_return,
        }


def compute_metrics(
    daily_equity: pd.DataFrame,
    fills: pd.DataFrame,
    benchmark: pd.DataFrame,
) -> PerformanceMetrics:
    """Reduce the three ledger frames into a :class:`PerformanceMetrics` record.

    ``daily_equity`` must be non-empty and carry ``EQUITY_COLUMNS``; ``fills``
    and ``benchmark`` may be empty but must still carry their canonical columns
    so every referenced field exists.  See the module docstring for the exact
    conventions applied to short windows and empty edge frames.
    """
    equity = _prepare_equity(daily_equity)
    fill_data = _prepare_fills(fills)
    benchmark_data = _prepare_benchmark(benchmark)

    totals = equity["total_equity"].to_numpy(dtype=float)
    start = float(equity["total_equity"].iloc[0])
    end = float(equity["total_equity"].iloc[-1])
    n_days = len(equity)
    start_date = equity["trade_date"].iloc[0]
    end_date = equity["trade_date"].iloc[-1]

    if n_days >= 2 and start > 0 and end > 0:
        cumulative = end / start - 1.0
        annualized = (end / start) ** (
            TRADING_DAYS_PER_YEAR / (n_days - 1)
        ) - 1.0
    else:
        cumulative = 0.0
        annualized = 0.0

    returns = pd.Series(totals).pct_change().replace([np.inf, -np.inf], np.nan)
    returns = returns.dropna()
    if len(returns) >= 2:
        annualized_volatility = float(returns.std(ddof=1)) * sqrt(
            TRADING_DAYS_PER_YEAR
        )
    else:
        annualized_volatility = 0.0

    running_max = np.maximum.accumulate(totals)
    with np.errstate(invalid="ignore", divide="ignore"):
        drawdown = np.where(running_max > 0, totals / running_max - 1.0, 0.0)
    max_drawdown = float(np.min(drawdown)) if n_days else 0.0

    # Costs and turnover from the per-fill ledger.
    if len(fill_data):
        buy = fill_data.loc[fill_data["side"].eq(BUY)]
        sell = fill_data.loc[fill_data["side"].eq(SELL)]
        buy_notional = float((buy["quantity"] * buy["price"]).sum())
        sell_notional = float((sell["quantity"] * sell["price"]).sum())
        total_commission = float(fill_data["commission"].sum())
        total_stamp_tax = float(fill_data["stamp_tax"].sum())
        buy_slippage = (
            ((buy["price"] - buy["reference_price"]) * buy["quantity"])
            .clip(lower=0)
            .sum()
        )
        sell_slippage = (
            ((sell["reference_price"] - sell["price"]) * sell["quantity"])
            .clip(lower=0)
            .sum()
        )
        # Every term is an integer multiple of a cent, so rounding to cents
        # only strips the float-accumulation noise from the sums.
        slippage_estimate = round(float(buy_slippage + sell_slippage), 2)
    else:
        buy_notional = 0.0
        sell_notional = 0.0
        total_commission = 0.0
        total_stamp_tax = 0.0
        slippage_estimate = 0.0
    mean_equity = float(equity["total_equity"].mean())
    if mean_equity > 0:
        turnover = (buy_notional + sell_notional) / 2.0 / mean_equity
    else:
        turnover = 0.0

    last_total = float(equity["total_equity"].iloc[-1])
    last_cash = float(equity["cash"].iloc[-1])
    last_stale = float(equity["stale_market_value"].iloc[-1])
    if last_total > 0:
        cash_ratio_end = last_cash / last_total
        stale_ratio_end = last_stale / last_total
    else:
        cash_ratio_end = 0.0
        stale_ratio_end = 0.0

    benchmark_symbol, benchmark_return = _benchmark_return(
        benchmark_data, start_date, end_date
    )
    benchmark_excess = (
        cumulative - benchmark_return if benchmark_symbol is not None else 0.0
    )

    return PerformanceMetrics(
        n_days=n_days,
        start_date=start_date,
        end_date=end_date,
        start_equity=start,
        end_equity=end,
        cumulative_return=float(cumulative),
        annualized_return=float(annualized),
        annualized_volatility=annualized_volatility,
        max_drawdown=max_drawdown,
        turnover=float(turnover),
        total_commission=total_commission,
        total_stamp_tax=total_stamp_tax,
        slippage_estimate=slippage_estimate,
        cash_ratio_end=float(cash_ratio_end),
        stale_asset_ratio_end=float(stale_ratio_end),
        benchmark_symbol=benchmark_symbol,
        benchmark_total_return=benchmark_return,
        benchmark_excess_return=float(benchmark_excess),
    )


# --------------------------------------------------------------------------- #
# Frame preparation
# --------------------------------------------------------------------------- #


def _prepare_equity(daily_equity: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(daily_equity, pd.DataFrame):
        raise TypeError(
            "daily_equity must be a pandas DataFrame, got "
            f"{type(daily_equity).__name__}"
        )
    _require_columns(daily_equity, DAILY_EQUITY_COLUMNS, "daily_equity")
    frame = daily_equity.copy()
    frame["trade_date"] = [_day(value) for value in frame["trade_date"]]
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    if frame.empty:
        raise ValueError("daily_equity must contain at least one row")
    for column in _EQUITY_NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _prepare_fills(fills: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(fills, pd.DataFrame):
        raise TypeError(
            f"fills must be a pandas DataFrame, got {type(fills).__name__}"
        )
    _require_columns(fills, FILL_REQUIRED_COLUMNS, "fills")
    if fills.empty:
        return fills
    frame = fills.copy()
    frame["side"] = frame["side"].astype(str).str.strip().str.upper()
    if "reference_price" not in frame.columns:
        # Legacy ledger: no unadjusted reference was recorded, so the fill
        # price is the only evidence and slippage is treated as zero.
        frame["reference_price"] = frame["price"]
    else:
        frame["reference_price"] = frame["reference_price"].fillna(frame["price"])
    for column in _FILL_NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _prepare_benchmark(benchmark: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(benchmark, pd.DataFrame):
        raise TypeError(
            f"benchmark must be a pandas DataFrame, got {type(benchmark).__name__}"
        )
    _require_columns(benchmark, BENCHMARK_COLUMNS, "benchmark")
    if benchmark.empty:
        return benchmark
    frame = benchmark.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.strip()
    frame["trade_date"] = [_day(value) for value in frame["trade_date"]]
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(
            f"{name} must include columns {list(columns)}; missing {missing}"
        )


def _day(value: object) -> date:
    """Normalize a datetime64/Timestamp/datetime/date/str to a plain date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    stamp = pd.Timestamp(value)
    if stamp is pd.NaT:
        raise ValueError(f"cannot interpret {value!r} as a trading date")
    return stamp.date()


def _benchmark_return(
    benchmark: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> tuple[str | None, float]:
    """Total return of the primary index over the equity window.

    Returns ``(None, 0.0)`` when the benchmark frame is empty; the primary
    symbol is ``000300.SH`` when present and otherwise the first symbol the
    frame names.  Fewer than two in-window closes or a non-positive first close
    yield ``0.0`` so a partial/empty benchmark never divides by zero.
    """
    if benchmark is None or benchmark.empty:
        return None, 0.0
    symbols = list(dict.fromkeys(benchmark["symbol"].tolist()))
    if PRIMARY_BENCHMARK_SYMBOL in symbols:
        primary = PRIMARY_BENCHMARK_SYMBOL
    else:
        primary = symbols[0]
    series = benchmark.loc[benchmark["symbol"].eq(primary)].sort_values(
        "trade_date"
    )
    within = series[
        series["trade_date"].between(start_date, end_date)
    ]["close"].dropna()
    if len(within) < 2:
        return primary, 0.0
    first = float(within.iloc[0])
    last = float(within.iloc[-1])
    if first <= 0:
        return primary, 0.0
    return primary, last / first - 1.0
