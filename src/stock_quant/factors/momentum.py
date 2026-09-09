"""Versioned 60-trading-session momentum factor ``momentum_60d`` (Task 6).

For each weekly signal date the factor uses the continuity series of one
``(source, adjustment)`` and computes

    Momentum60 = adjusted_close[t] / adjusted_close[t-60] - 1

where ``t`` is the signal date and ``t-60`` the observation sixty sessions
earlier.  A symbol is eligible only with at least 61 observations on or before
``t``, no ``ERROR``-quality row inside the lookback and at least 120 listed
trading days as of ``t`` (seasoning).  Every observation used for a signal
date has ``trade_date <= t`` -- rows added after a signal date can never
change that date's factor value -- and ``processed_value`` equals
``raw_value`` in this phase.

Membership-first order (Task 5): when the context carries the point-in-time
membership hooks, the candidate symbols of each signal day are filtered to
``context.members_on(signal)`` BEFORE the observation/quality/seasoning
filters, so a non-member never produces a row (valid or invalid) for that day
while a member removed from the universe keeps every row up to its last
membership day and simply makes no new signal afterwards.  Removal is an
input gate, not a trading instruction.  A context without ``members_on``
(the legacy engineering path) applies no membership gate.

Version 2.0.0 permanently isolates the total-return input basis
(``adjusted_bar`` / ``internal_total_return_v1``) from the pre-2.0 unadjusted
results: a spec that still requests 1.0.0 is rejected by the provider instead
of being silently upgraded.

Only the signal dates given in ``FactorContext.signal_dates`` produce rows;
rows are sorted by trade date then symbol, so identical inputs yield
byte-stable Parquet.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_quant.data_quality.models import Severity
from stock_quant.factors.base import FactorContext
from stock_quant.factors.models import FACTOR_RESULT_COLUMNS, FactorResult

_INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
_QUALITY_ERROR = "quality_error"
_SEASONING_BELOW_120 = "seasoning_below_120"

_MIN_LISTED_DAYS = 120
_NAN = float("nan")


class Momentum60:
    """Sixty-trading-session price momentum over one adjusted-close series."""

    name = "momentum_60d"
    version = "2.0.0"
    lookback = 60
    frequency = "weekly"
    required_fields = frozenset(
        {"adjusted_close", "quality_severity", "listed_trading_days"}
    )

    _STRUCTURAL_FIELDS = frozenset({"trade_date", "symbol", "source", "adjustment"})

    def compute(self, context: FactorContext) -> FactorResult:
        observations = context.dataset.factor_input()
        self._assert_complete_fields(observations)
        if not context.signal_dates:
            empty = pd.DataFrame(columns=list(FACTOR_RESULT_COLUMNS))
            return FactorResult(
                factor_name=self.name, factor_version=self.version, frame=empty
            )

        newest_signal = max(context.signal_dates)
        observations = observations[observations["trade_date"] <= newest_signal]
        self._assert_single_series_per_symbol(observations)
        by_symbol = self._per_symbol_series(observations)

        records: list[dict] = []
        for signal in sorted(set(context.signal_dates)):
            # Membership-first: resolve this signal day's point-in-time member
            # set once, before any factor eligibility logic touches a row.
            members = (
                None
                if context.members_on is None
                else frozenset(context.members_on(signal))
            )
            for symbol in sorted(by_symbol):
                if members is not None and symbol not in members:
                    continue  # not a member on this signal day: no row at all
                series = by_symbol[symbol]
                if series["trade_date"].iloc[0] > signal:
                    continue  # symbol has no observation by this signal date
                valid, reason, value = self._momentum_at(series, signal)
                records.append(
                    {
                        "trade_date": signal,
                        "symbol": symbol,
                        "factor_name": self.name,
                        "factor_version": self.version,
                        "raw_value": value,
                        "processed_value": value,
                        "is_valid": valid,
                        "invalid_reason": reason,
                    }
                )

        frame = pd.DataFrame(records, columns=list(FACTOR_RESULT_COLUMNS))
        frame = frame.sort_values(
            ["trade_date", "symbol"], kind="stable"
        ).reset_index(drop=True)
        return FactorResult(
            factor_name=self.name, factor_version=self.version, frame=frame
        )

    def _assert_complete_fields(self, observations: pd.DataFrame) -> None:
        missing = (self.required_fields | self._STRUCTURAL_FIELDS) - set(
            observations.columns
        )
        if missing:
            raise ValueError(
                "factor input is missing required fields: "
                + ", ".join(sorted(missing))
            )

    @staticmethod
    def _assert_single_series_per_symbol(observations: pd.DataFrame) -> None:
        key = observations["source"].astype(str) + "\x1f" + observations[
            "adjustment"
        ].astype(str)
        counts = pd.DataFrame({"symbol": observations["symbol"], "key": key})
        mixed = counts.groupby("symbol")["key"].nunique()
        mixed = mixed[mixed > 1]
        if not mixed.empty:
            raise ValueError(
                "factor input mixes source/adjustment series per symbol; momentum "
                "needs one source and one adjustment, got multiple for: "
                + ", ".join(sorted(mixed.index))
            )

    @staticmethod
    def _per_symbol_series(observations: pd.DataFrame) -> dict[str, pd.DataFrame]:
        by_symbol: dict[str, pd.DataFrame] = {}
        for symbol, group in observations.groupby("symbol", sort=True):
            series = group.sort_values("trade_date").reset_index(drop=True)
            if series["trade_date"].duplicated().any():
                raise ValueError(
                    f"factor input for {symbol} contains duplicate trade_date rows"
                )
            by_symbol[symbol] = series
        return by_symbol

    def _momentum_at(
        self, series: pd.DataFrame, signal: date
    ) -> tuple[bool, str, float]:
        available = series[series["trade_date"] <= signal]
        if len(available) < self.lookback + 1:
            return False, _INSUFFICIENT_OBSERVATIONS, _NAN
        window = available.tail(self.lookback + 1)
        if (window["quality_severity"] == Severity.ERROR.value).any():
            return False, _QUALITY_ERROR, _NAN
        if int(window["listed_trading_days"].iloc[-1]) < _MIN_LISTED_DAYS:
            return False, _SEASONING_BELOW_120, _NAN
        numerator = float(window["adjusted_close"].iloc[-1])
        denominator = float(window["adjusted_close"].iloc[0])
        value = numerator / denominator - 1.0
        return True, "", value
