"""OOS daily returns, per-fold risk/execution/cost metrics and aggregation.

The fold equity artifact carries the canonical columns ``trade_date``,
``initial_equity`` and ``net_equity_after_cost``; the last column is the
engine's current ``total_equity`` under a different name -- its economic
meaning (mark-to-market net equity after explicit costs and slippage) is
unchanged, only the audit-facing name is explicit.

Conventions (spec 指标口径):

- Every confirmed open OOS day has exactly one equity row and therefore one
  portfolio return; the fold's first-day return uses the fixed
  ``initial_equity`` as its predecessor, later returns the prior day's
  ``net_equity_after_cost``.  A missing day, a duplicate date, an out-of-range
  date or a non-positive mark is an :class:`OOSIntegrityError`, never a
  silently smaller sample.
- ``fold_calendar_return`` is the product of the fold's daily returns minus
  one; volatility/Sharpe are sample statistics (``ddof=1``) annualized with
  252 sessions; undefined values (fewer than two observations, or zero
  standard deviation) are JSON ``null``, never zero or NaN.
- ``per_fold_max_drawdown`` is computed only from the fold's own
  ``net_equity_after_cost`` path (cross-fold drawdown and Calmar are
  forbidden and no such aggregate field exists anywhere in this module).
- Execution quality counts *unique* submitted order ids: ``reject_rate``
  counts orders with any rejected quantity, and the full/partial counts plus
  the rejected/requested quantity ratio are emitted beside it (``None`` when
  no order was submitted).
- Turnover is versioned ``turnover-v1``: numerator
  ``(buy_notional + sell_notional) / 2`` over the mean daily
  ``net_equity_after_cost`` denominator, both persisted beside the ratio.
- The same-path cost replay uses the exact realized fill ids, quantities and
  prices and removes explicit commissions/taxes only; slippage impact uses
  the recorded reference price and realized quantity.  ``zero_cost`` is a
  separate path-changing counterfactual and is never used here.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, datetime
from typing import Literal, Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from stock_quant.research.walk_forward.schedule import FoldWindow

TRADING_DAYS_PER_YEAR = 252

#: Turnover formula version preserved from the single-window analytics.
TURNOVER_VERSION = "turnover-v1"

_EQUITY_REQUIRED_COLUMNS = ("trade_date", "net_equity_after_cost")
_FILLS_REQUIRED_COLUMNS = (
    "trade_date",
    "fill_id",
    "order_id",
    "side",
    "symbol",
    "quantity",
    "price",
    "commission",
    "stamp_tax",
)
_ORDER_DIFF_COLUMNS = ("order_id", "planned_quantity", "rejected_quantity")

_BUY = "BUY"
_SELL = "SELL"


class OOSIntegrityError(ValueError):
    """An OOS observation contract breach: never masked, never downgraded."""


class _StrictFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _null(value: float | None) -> float | None:
    """Render a non-finite float as JSON ``null`` instead of NaN."""
    if value is None:
        return None
    return value if math.isfinite(value) else None


class SamePathCostMetrics(_StrictFrozen):
    """Same-fill-path explicit-cost replay of one fold execution."""

    fill_count: int
    total_explicit_cost: float
    gross_return_before_explicit_cost: float
    net_return: float
    explicit_cost_drag: float
    slippage_impact: float
    initial_equity: float

    @property
    def explicit_cost_ratio(self) -> float:
        """``total_explicit_cost / initial_equity`` (fixed denominator)."""
        return self.total_explicit_cost / self.initial_equity


class FoldMetrics(_StrictFrozen):
    """Every auditable metric of one executed fold under one cost scenario.

    A fold executes once per predeclared cost scenario, so one record is
    inherently scenario-bound; :class:`ScenarioMetrics` is the evaluation-side
    name of this record.
    """

    fold_id: str = ""
    scenario: str = ""
    #: ``False`` only when the fold's scenario artifacts are incomplete; the
    #: stability evaluation must treat that as a terminal FAILED, never as
    #: evidence about performance.
    artifact_complete: bool = True
    first_trading_day: date | None = None
    last_trading_day: date | None = None
    observation_count: int = 0
    fold_calendar_return: float | None = 0.0
    annualized_volatility: float | None = None
    sharpe_zero_rf: float | None = None
    per_fold_max_drawdown: float = 0.0
    # Same-path cost decomposition.
    gross_return_before_explicit_cost: float = 0.0
    net_return: float = 0.0
    explicit_cost_drag: float = 0.0
    slippage_impact: float = 0.0
    total_explicit_cost: float = 0.0
    initial_equity: float = 0.0
    explicit_cost_ratio: float = 0.0
    # Execution quality.
    submitted_order_count: int = 0
    reject_rate: float | None = None
    fully_rejected_order_count: int = 0
    partially_filled_order_count: int = 0
    unfilled_quantity_rate: float | None = None
    slippage_estimate: float = 0.0
    # Versioned turnover with both operands persisted beside the ratio.
    turnover: float | None = None
    turnover_version: Literal["turnover-v1"] = TURNOVER_VERSION
    turnover_numerator: float = 0.0
    turnover_denominator: float = 0.0


#: The evaluation-side name of one (fold, scenario) metric record: every fold
#: executes once per predeclared cost scenario, so a scenario's metrics are a
#: sequence of per-fold records.
ScenarioMetrics = FoldMetrics


class AggregateOOSMetrics(_StrictFrozen):
    """Aggregate statistics over concatenated executed-fold OOS daily returns.

    Deliberately carries no drawdown, Calmar or any other cross-fold path
    metric: concatenating independent accounts' returns into one equity path
    is exactly what the walk-forward policy forbids.
    """

    aggregate_return: float | None
    annualized_return: float | None
    annualized_volatility: float | None
    sharpe_zero_rf: float | None
    oos_return_observations: int
    annualization_observations: int


# --------------------------------------------------------------------------- #
# Daily returns
# --------------------------------------------------------------------------- #


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    stamp = pd.Timestamp(value)
    if stamp is pd.NaT:
        raise OOSIntegrityError(f"cannot interpret {value!r} as a trading date")
    return stamp.date()


def build_daily_returns(
    equity: pd.DataFrame,
    *,
    initial_equity: float,
    expected_open_days: Sequence[date],
) -> pd.DataFrame:
    """One return per expected open session; first day from ``initial_equity``.

    ``equity`` must carry exactly one finite, positive
    ``net_equity_after_cost`` row per expected open session -- no missing day,
    no duplicate date, no extra date.  The returned frame carries
    ``trade_date``, ``net_equity_after_cost`` and ``daily_return`` sorted by
    date.
    """
    for column in _EQUITY_REQUIRED_COLUMNS:
        if column not in equity.columns:
            raise OOSIntegrityError(
                f"fold equity must include {column}; got {list(equity.columns)}"
            )
    frame = equity.copy()
    frame["trade_date"] = [_as_date(day) for day in frame["trade_date"]]
    frame["net_equity_after_cost"] = pd.to_numeric(
        frame["net_equity_after_cost"], errors="coerce"
    )
    dates = list(frame["trade_date"])
    if len(set(dates)) != len(dates):
        raise OOSIntegrityError("fold equity contains duplicate trade dates")
    expected = [_as_date(day) for day in expected_open_days]
    if len(set(expected)) != len(expected):
        raise OOSIntegrityError("expected open days contain duplicates")
    rows = frame.set_index("trade_date")
    missing = [day for day in expected if day not in rows.index]
    if missing:
        raise OOSIntegrityError(
            "missing portfolio equity on confirmed open day(s): "
            + ", ".join(day.isoformat() for day in missing)
        )
    extra = [day for day in dates if day not in set(expected)]
    if extra:
        raise OOSIntegrityError(
            "fold equity carries rows outside the expected open days: "
            + ", ".join(day.isoformat() for day in sorted(set(extra)))
        )
    ordered = rows.loc[expected]
    values = ordered["net_equity_after_cost"].to_numpy(dtype=float)
    for index, value in enumerate(values):
        if not math.isfinite(value) or value <= 0:
            day = expected[index].isoformat()
            raise OOSIntegrityError(
                f"net_equity_after_cost on {day} must be finite and positive, "
                f"got {value}"
            )
    predecessor = float(initial_equity)
    if not math.isfinite(predecessor) or predecessor <= 0:
        raise OOSIntegrityError("initial_equity must be finite and positive")
    returns: list[float] = []
    for value in values:
        returns.append(value / predecessor - 1.0)
        predecessor = value
    return pd.DataFrame(
        {
            "trade_date": expected,
            "net_equity_after_cost": values,
            "daily_return": returns,
        }
    )


# --------------------------------------------------------------------------- #
# Same-path cost replay
# --------------------------------------------------------------------------- #


def compute_same_path_costs(
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    initial_equity: float,
) -> SamePathCostMetrics:
    """Replay the realized fill path with explicit commissions/taxes removed.

    The gross path adds each fill's ``commission + stamp_tax`` back to the
    net mark of its own day and every later day of the same fold: identical
    fill ids, quantities and prices, identical dates -- only the explicit
    fees are removed.  Slippage impact is computed per fill from the recorded
    reference price and the realized quantity and never alters the fill set.
    """
    _require_columns(equity, _EQUITY_REQUIRED_COLUMNS, "equity")
    frame = equity.copy()
    frame["trade_date"] = [_as_date(day) for day in frame["trade_date"]]
    frame["net_equity_after_cost"] = pd.to_numeric(
        frame["net_equity_after_cost"], errors="coerce"
    )
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    fill_rows = _prepared_fills(fills)
    cumulative = 0.0
    cost_by_day: dict[date, float] = {}
    for record in fill_rows.to_dict("records"):
        explicit = float(record["commission"]) + float(record["stamp_tax"])
        cumulative += explicit
        cost_by_day[record["trade_date"]] = cumulative
    if frame.empty:
        raise OOSIntegrityError("fold equity is empty; no cost replay possible")
    gross_values: list[float] = []
    fill_days = sorted(cost_by_day)
    for row in frame.to_dict("records"):  # already sorted by trade_date
        earlier = bisect_right(fill_days, row["trade_date"]) - 1
        removed = cost_by_day[fill_days[earlier]] if earlier >= 0 else 0.0
        gross_values.append(float(row["net_equity_after_cost"]) + removed)
    net_terminal = float(frame["net_equity_after_cost"].iloc[-1])
    gross_terminal = gross_values[-1]
    net_return = net_terminal / float(initial_equity) - 1.0
    gross_return = gross_terminal / float(initial_equity) - 1.0
    total_explicit_cost = round(
        float(
            (fill_rows["commission"].sum() + fill_rows["stamp_tax"].sum())
            if len(fill_rows)
            else 0.0
        ),
        2,
    )
    return SamePathCostMetrics(
        fill_count=int(len(fill_rows)),
        total_explicit_cost=total_explicit_cost,
        gross_return_before_explicit_cost=gross_return,
        net_return=net_return,
        explicit_cost_drag=gross_return - net_return,
        slippage_impact=_slippage_of_fills(fill_rows),
        initial_equity=float(initial_equity),
    )


# --------------------------------------------------------------------------- #
# Fold metrics
# --------------------------------------------------------------------------- #


def compute_fold_metrics(
    equity: pd.DataFrame,
    fills: pd.DataFrame,
    *,
    submitted_orders: pd.DataFrame | None = None,
    order_diffs: pd.DataFrame | None = None,
    initial_equity: float | None = None,
    expected_open_days: Sequence[date] | None = None,
    fold_id: str = "",
    scenario: str = "",
) -> FoldMetrics:
    """Reduce one executed fold's ledgers into its full metric record.

    ``initial_equity`` defaults to the first equity mark (a first-day return
    of exactly zero) and ``expected_open_days`` to the equity frame's own
    dates; the fold runner always passes both explicitly.  See the module
    docstring for every formula and its ``None`` conventions.
    """
    _require_columns(equity, _EQUITY_REQUIRED_COLUMNS, "equity")
    frame = equity.copy()
    frame["trade_date"] = [_as_date(day) for day in frame["trade_date"]]
    frame["net_equity_after_cost"] = pd.to_numeric(
        frame["net_equity_after_cost"], errors="coerce"
    )
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    if frame.empty:
        raise OOSIntegrityError("fold equity is empty")
    resolved_initial = (
        float(initial_equity)
        if initial_equity is not None
        else float(frame["net_equity_after_cost"].iloc[0])
    )
    days = (
        [_as_date(day) for day in expected_open_days]
        if expected_open_days is not None
        else list(frame["trade_date"])
    )
    returns = build_daily_returns(
        frame, initial_equity=resolved_initial, expected_open_days=days
    )
    daily = returns["daily_return"].to_numpy(dtype=float)
    n = int(len(daily))
    fold_calendar_return = float(np.prod(1.0 + daily)) - 1.0 if n else None
    values = frame["net_equity_after_cost"].to_numpy(dtype=float)
    running_max = np.maximum.accumulate(values)
    with np.errstate(invalid="ignore", divide="ignore"):
        drawdown = values / running_max - 1.0
    per_fold_max_drawdown = float(np.min(drawdown))
    volatility = _sample_annualized(daily, lambda std, mean: std)
    sharpe = _sample_annualized(daily, lambda std, mean: mean / std)

    prepared_fills = _prepared_fills(fills)
    same_path = compute_same_path_costs(
        frame, fills, initial_equity=resolved_initial
    )
    submitted = (
        pd.DataFrame(columns=["order_id"])
        if submitted_orders is None
        else submitted_orders
    )
    submitted_count = (
        int(submitted["order_id"].nunique())
        if "order_id" in submitted.columns and len(submitted)
        else 0
    )
    diffs = _prepared_order_diffs(order_diffs)
    rejected_orders = int((diffs["rejected_quantity"] > 0).sum())
    fully_rejected = int((diffs["status"] == "REJECTED").sum())
    partially_filled = int((diffs["status"] == "PARTIAL").sum())
    total_rejected = int(diffs["rejected_quantity"].sum())
    total_requested = int(diffs["planned_quantity"].sum())
    reject_rate = rejected_orders / submitted_count if submitted_count else None
    unfilled_rate = (
        total_rejected / total_requested if total_requested else None
    )
    buy = prepared_fills[prepared_fills["side"].eq(_BUY)]
    sell = prepared_fills[prepared_fills["side"].eq(_SELL)]
    buy_notional = float((buy["quantity"] * buy["price"]).sum())
    sell_notional = float((sell["quantity"] * sell["price"]).sum())
    numerator = (buy_notional + sell_notional) / 2.0
    denominator = float(values.mean())
    turnover = numerator / denominator if denominator > 0 else None
    return FoldMetrics(
        fold_id=fold_id,
        scenario=scenario,
        first_trading_day=days[0] if days else None,
        last_trading_day=days[-1] if days else None,
        observation_count=n,
        fold_calendar_return=_null(fold_calendar_return),
        annualized_volatility=_null(volatility),
        sharpe_zero_rf=_null(sharpe),
        per_fold_max_drawdown=per_fold_max_drawdown,
        gross_return_before_explicit_cost=same_path.gross_return_before_explicit_cost,
        net_return=same_path.net_return,
        explicit_cost_drag=same_path.explicit_cost_drag,
        slippage_impact=same_path.slippage_impact,
        total_explicit_cost=same_path.total_explicit_cost,
        initial_equity=same_path.initial_equity,
        explicit_cost_ratio=same_path.explicit_cost_ratio,
        submitted_order_count=submitted_count,
        reject_rate=_null(reject_rate),
        fully_rejected_order_count=fully_rejected,
        partially_filled_order_count=partially_filled,
        unfilled_quantity_rate=_null(unfilled_rate),
        slippage_estimate=_slippage_of_fills(prepared_fills),
        turnover=_null(turnover),
        turnover_numerator=numerator,
        turnover_denominator=denominator,
    )


# --------------------------------------------------------------------------- #
# Aggregate OOS metrics
# --------------------------------------------------------------------------- #


def aggregate_oos_returns(
    per_fold_returns: Sequence[tuple[FoldWindow, pd.DataFrame]],
) -> AggregateOOSMetrics:
    """Concatenate executed folds' daily returns and aggregate OOS-only.

    Every return date must lie inside its own fold's confirmed open sessions,
    folds must be non-overlapping in dates (no duplicates anywhere), and the
    concatenated series is sorted by date.  ``N`` is the total observation
    count and is reported as both ``oos_return_observations`` and
    ``annualization_observations``.
    """
    observations: dict[date, float] = {}
    for fold, frame in per_fold_returns:
        for column in ("trade_date", "daily_return"):
            if column not in frame.columns:
                raise OOSIntegrityError(
                    f"per-fold returns must include {column}"
                )
        for row in frame.to_dict("records"):
            day = _as_date(row["trade_date"])
            if fold.first_trading_day is None or fold.last_trading_day is None:
                raise OOSIntegrityError(
                    f"fold {fold.fold_id} has no confirmed open sessions; it "
                    "cannot contribute OOS returns"
                )
            if not (
                fold.first_trading_day <= day <= fold.last_trading_day
            ):
                raise OOSIntegrityError(
                    f"return date {day.isoformat()} lies outside fold "
                    f"{fold.fold_id} "
                    f"[{fold.first_trading_day.isoformat()}, "
                    f"{fold.last_trading_day.isoformat()}]"
                )
            if day in observations:
                raise OOSIntegrityError(
                    f"duplicate/overlapping OOS return date {day.isoformat()}"
                )
            observations[day] = float(row["daily_return"])
    series = pd.Series(
        [observations[day] for day in sorted(observations)],
        dtype=float,
    )
    n = int(len(series))
    if n == 0:
        product = None
        annualized = None
    else:
        product = float(np.prod(1.0 + series.to_numpy())) - 1.0
        annualized = float(np.prod(1.0 + series.to_numpy())) ** (
            TRADING_DAYS_PER_YEAR / n
        ) - 1.0
    volatility = _sample_annualized(series.to_numpy(), lambda std, mean: std)
    sharpe = _sample_annualized(series.to_numpy(), lambda std, mean: mean / std)
    return AggregateOOSMetrics(
        aggregate_return=_null(product),
        annualized_return=_null(annualized),
        annualized_volatility=_null(volatility),
        sharpe_zero_rf=_null(sharpe),
        oos_return_observations=n,
        annualization_observations=n,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _sample_annualized(daily: np.ndarray, reduce) -> float | None:
    """Annualized sample statistic, or ``None`` when undefined (ddof=1)."""
    if len(daily) < 2:
        return None
    std = float(np.std(daily, ddof=1))
    if not math.isfinite(std) or std == 0.0:
        return None
    mean = float(np.mean(daily))
    return float(reduce(std, mean)) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _prepared_fills(fills: pd.DataFrame) -> pd.DataFrame:
    if fills is None:
        return pd.DataFrame(columns=list(_FILLS_REQUIRED_COLUMNS))
    _require_columns(fills, _FILLS_REQUIRED_COLUMNS, "fills")
    frame = fills.copy()
    if frame.empty:
        return frame
    frame["side"] = frame["side"].astype(str).str.upper()
    frame["trade_date"] = [_as_date(day) for day in frame["trade_date"]]
    for column in ("quantity", "price", "commission", "stamp_tax"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "reference_price" not in frame.columns:
        frame["reference_price"] = frame["price"]
    else:
        frame["reference_price"] = frame["reference_price"].fillna(
            frame["price"]
        )
    frame["reference_price"] = pd.to_numeric(
        frame["reference_price"], errors="coerce"
    )
    return frame


def _prepared_order_diffs(order_diffs: pd.DataFrame | None) -> pd.DataFrame:
    if order_diffs is None:
        return pd.DataFrame(
            columns=list(_ORDER_DIFF_COLUMNS) + ["status"]
        )
    _require_columns(order_diffs, _ORDER_DIFF_COLUMNS, "order_diffs")
    frame = order_diffs.copy()
    if "status" not in frame.columns:
        frame["status"] = ""
    for column in ("planned_quantity", "rejected_quantity"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    return frame


def _slippage_of_fills(fills: pd.DataFrame) -> float:
    """Adverse realized slippage summed over fills (buy above / sell below)."""
    if fills is None or not len(fills):
        return 0.0
    buy = fills[fills["side"].eq(_BUY)]
    sell = fills[fills["side"].eq(_SELL)]
    buy_slippage = (
        (buy["price"] - buy["reference_price"]) * buy["quantity"]
    ).clip(lower=0).sum()
    sell_slippage = (
        (sell["reference_price"] - sell["price"]) * sell["quantity"]
    ).clip(lower=0).sum()
    return round(float(buy_slippage + sell_slippage), 2)


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise OOSIntegrityError(
            f"{name} must include columns {list(columns)}; missing {missing}"
        )
