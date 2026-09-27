"""The exchange reference price as a refused restructuring row's holder basis.

A 重整转增 row refused as non-distributive (ADR-008) can still carry a real
holder-level price adjustment: the exchange publishes an ex-rights reference
price for the ex-date whenever shares expand, and that reference implies the
fraction of the expansion that reached holders.  ADR-009 measured the three
stated values for `600518.SH`/`600515.SH`, found them irreconcilable, and
refused to adjudicate; the in-repo bytes later adjudicated them -- the two
price-adjacent channels agree to ~1e-7 (the cumulative factor series' step
across the ex-date vs the exchange reference implied by the traded prices),
while the announcement and TDX capital ratios describe the share expansion,
not the price adjustment (ADR-019).

This module owns the settlement that books the reference-implied holder ratio
when the evidence is unanimous, and answers ``None`` -- keep the quarantine --
for every shape the evidence does not carry:

1. **The reference is stated, not inferred.** The supplier's ex-date row
   states ``pre_close`` -- the exchange's own 除权参考价.  The last traded
   close before the ex-date may sit further back than the previous open day
   (a suspension into the ex-date leaves no row for the halt days): the gap
   form ADR-013 decision 6 declined to automate.  Here it is verified rather
   than assumed, by legs 3 and 4.
2. **An exchange event attests the date.** TDX's category-1 除权除息 record
   exists at the ex-date.  Without it the reference difference could be a
   data defect; with it, a stated reference differing from the last close is
   the exchange's adjustment at a stated event.
3. **The no-adjustment hypothesis is rejected from the symbol's own trades.**
   The reference must lie outside the traded price-limit band implied by the
   calibration window (the largest daily move the symbol actually traded
   there), and the ex-date's traded prices must sit inside the band around
   the reference -- the exchange enforcing its own reference.  This is the
   verification whose absence made ADR-013 decline the shape: the reference's
   meaning is proven in the instance, not assumed for the class.
4. **A factor series, when a channel is configured, must agree.**  The
   cumulative adjustment ratio across the ex-date is recorded corroboration;
   a disagreement fails closed.  A missing channel skips the leg (ADR-016
   decision 11 ships the successor disabled).

Every settlement carries the numbers that produced it, so the run's evidence
recomputes it from stored bytes without re-fetching anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

#: Calendar days the calibration fetch reaches back, to measure the largest
#: daily move the symbol actually traded around the ex-date.
CALIBRATION_DAYS = 180

#: Fewer traded rows than this in the calibration window cannot establish the
#: price-limit band; the falsification is refused rather than guessed.
MIN_CALIBRATION_ROWS = 20

#: Relative agreement demanded of the optional factor-series leg.  The
#: measured agreement on the two attested rows is ~3e-7; the tolerance sits
#: three orders above the measurement and far below any real disagreement.
FACTOR_AGREEMENT_TOLERANCE = 1e-5


@dataclass(frozen=True)
class PriceBasisSettlement:
    """A distinguishable settlement, with the numbers that produced it."""

    symbol: str
    ex_date: date
    prev_close: float
    prev_close_date: date
    pre_close: float
    calibration_move: float
    calibration_rows: int
    factor_series_ratio: float | None

    @property
    def factor_ratio(self) -> float:
        """The holder adjustment the exchange's reference implies."""
        return self.prev_close / self.pre_close

    def to_details(self) -> dict[str, object]:
        """The auditable numbers, as issue-detail values."""
        return {
            "prev_close": self.prev_close,
            "prev_close_date": self.prev_close_date.isoformat(),
            "pre_close": self.pre_close,
            "implied_factor": self.factor_ratio,
            "calibration_move": self.calibration_move,
            "calibration_rows": self.calibration_rows,
            "factor_series_ratio": self.factor_series_ratio,
        }


class PriceBasisSettler:
    """Books the reference-implied holder ratio of a refused restructuring row.

    ``fetch_daily`` re-asks the price supplier for one symbol's window ending
    at the ex-date (the raw response is snapshotted by the caller's
    ``record_raw``, so the settlement recomputes from stored bytes).
    ``frame_for`` exposes the TDX channel's per-symbol xdxr frame (the lazy
    arbiter's own accessor; ``None`` means the channel is absent and asserts
    nothing).  ``factor_ratio``, when given, reads the cumulative adjustment
    ratio across the ex-date from the configured factor channel.
    """

    def __init__(
        self,
        *,
        fetch_daily: Callable[[str, date, date], Any],
        frame_for: Callable[[str], Any] | None,
        factor_ratio: Callable[[str, date], float | None] | None = None,
        on_failure: Callable[[str, Exception], None] | None = None,
        on_settlement: Callable[[PriceBasisSettlement], None] | None = None,
        raw_snapshots: list[Any] | None = None,
        record_raw: Callable[[Any], Any] | None = None,
    ) -> None:
        self._fetch_daily = fetch_daily
        self._frame_for = frame_for
        self._factor_ratio = factor_ratio
        self._on_failure = on_failure
        self._on_settlement = on_settlement
        self._raw_snapshots = raw_snapshots if raw_snapshots is not None else []
        self._record_raw = record_raw
        self._settlements: dict[tuple[str, date], PriceBasisSettlement | None] = {}
        self._failed: set[str] = set()

    def settle(self, symbol: str, ex_date: date) -> PriceBasisSettlement | None:
        """The settlement the evidence supports for one refused row, or ``None``.

        ``None`` is the answer for every absent, unmodelled or disputed shape;
        a channel failure is remembered for the symbol and not retried within
        the run, mirroring the arbiter channels' discipline.
        """
        key = (symbol, ex_date)
        if key in self._settlements:
            return self._settlements[key]
        if symbol in self._failed:
            return None
        try:
            settlement = self._settle(symbol, ex_date)
        except Exception as error:  # noqa: BLE001 - best-effort evidence path
            if self._on_failure is not None:
                self._on_failure(symbol, error)
            self._failed.add(symbol)
            settlement = None
        self._settlements[key] = settlement
        return settlement

    def _settle(self, symbol: str, ex_date: date) -> PriceBasisSettlement | None:
        frame = self._calibration_frame(symbol, ex_date)
        if frame is None:
            return None
        inputs = _read_settlement_inputs(frame, ex_date)
        if inputs is None:
            return None
        prev_close, prev_close_date, pre_close, ex_row = inputs
        calibration = _calibration_move(frame, ex_date)
        if calibration is None:
            return None
        calibration_move, calibration_rows = calibration
        if not _reference_rejects_no_adjustment(
            prev_close, pre_close, calibration_move
        ):
            return None
        if not _ex_day_trades_around_the_reference(ex_row, pre_close, calibration_move):
            return None
        if not _tdx_attests_the_event(self._frame_for, symbol, ex_date):
            return None
        implied = prev_close / pre_close
        series_ratio = self._factor_series_ratio(symbol, ex_date, implied)
        if series_ratio is False:
            return None
        settlement = PriceBasisSettlement(
            symbol=symbol,
            ex_date=ex_date,
            prev_close=prev_close,
            prev_close_date=prev_close_date,
            pre_close=pre_close,
            calibration_move=calibration_move,
            calibration_rows=calibration_rows,
            factor_series_ratio=series_ratio,
        )
        if self._on_settlement is not None:
            try:
                self._on_settlement(settlement)
            except Exception:  # noqa: BLE001 - evidence reporting is best effort
                pass
        return settlement

    def _calibration_frame(self, symbol: str, ex_date: date):
        try:
            result = self._fetch_daily(
                symbol, ex_date - timedelta(days=CALIBRATION_DAYS), ex_date
            )
        except Exception as error:  # noqa: BLE001 - best-effort evidence path
            if self._on_failure is not None:
                self._on_failure(symbol, error)
            self._failed.add(symbol)
            return None
        frame = getattr(result, "frame", result)
        if frame is None or frame.empty:
            return None
        if self._record_raw is not None:
            try:
                self._raw_snapshots.append(self._record_raw(result))
            except Exception:  # noqa: BLE001 - evidence capture is best effort
                pass
        return frame

    def _factor_series_ratio(
        self, symbol: str, ex_date: date, implied: float
    ) -> float | None | bool:
        """The configured channel's cumulative ratio across the ex-date.

        ``None`` when no channel is configured or the channel carries no
        readable ratio for the symbol (the leg is skipped -- ADR-016
        decision 11 ships the successor channel disabled);
        ``False`` when a channel answered and *disagrees* -- a stated series
        moving by a different ratio at the attested date is exactly the
        dispute ADR-009 recorded, and the settlement must not book past it.
        """
        if self._factor_ratio is None:
            return None
        try:
            ratio = self._factor_ratio(symbol, ex_date)
        except Exception as error:  # noqa: BLE001 - a failing leg fails closed
            if self._on_failure is not None:
                self._on_failure(symbol, error)
            return False
        if ratio is None:
            return None
        if abs(ratio / implied - 1.0) > FACTOR_AGREEMENT_TOLERANCE:
            return False
        return ratio


def _read_settlement_inputs(
    frame: pd.DataFrame, ex_date: date
) -> tuple[float, date, float, dict[str, Any]] | None:
    """The stated numbers one frame carries for the ex-date, or ``None``.

    The ex-date row must exist and state a reference price; the previous
    traded close is the last row strictly before it -- the halt days between
    them carry no trade, so the last close *is* the price the exchange's
    reference formula consumed.
    """
    by_date: dict[date, dict[str, Any]] = {}
    for record in frame.to_dict("records"):
        day = _as_date(record.get("trade_date"))
        if day is not None:
            by_date[day] = record
    ex_row = by_date.get(ex_date)
    if ex_row is None:
        return None
    pre_close = _as_float(ex_row.get("pre_close"))
    if pre_close is None or pre_close <= 0.0:
        return None
    earlier = sorted(day for day in by_date if day < ex_date)
    if not earlier:
        return None
    prev_close_date = earlier[-1]
    prev_close = _as_float(by_date[prev_close_date].get("close"))
    if prev_close is None or prev_close <= 0.0:
        return None
    return prev_close, prev_close_date, pre_close, ex_row


def _calibration_move(frame: pd.DataFrame, ex_date: date) -> tuple[float, int] | None:
    """The largest daily move the symbol traded before the ex-date.

    Rows on and after the ex-date are excluded: the ex-date's own bar moves
    by the very adjustment under test and would dilute the band into
    uselessness.  A window too thin to establish a band refuses the
    settlement.
    """
    by_date: dict[date, float] = {}
    for record in frame.to_dict("records"):
        day = _as_date(record.get("trade_date"))
        close = _as_float(record.get("close"))
        if day is not None and close is not None and close > 0.0 and day < ex_date:
            by_date[day] = close
    days = sorted(by_date)
    if len(days) < MIN_CALIBRATION_ROWS:
        return None
    largest = 0.0
    for previous, current in zip(days, days[1:]):
        move = abs(by_date[current] / by_date[previous] - 1.0)
        largest = max(largest, move)
    return largest, len(days)


def _reference_rejects_no_adjustment(
    prev_close: float, pre_close: float, calibration_move: float
) -> bool:
    """Whether the reference cannot be a plausible unadjusted traded price.

    If no adjustment intervened, the reference would be one day's price
    against the last close -- bounded by the moves the symbol actually
    traded in its calibration window.  A reference beyond that band is the
    exchange's adjustment, not a price.
    """
    implied_move = abs(pre_close / prev_close - 1.0)
    return implied_move > calibration_move


def _ex_day_trades_around_the_reference(
    ex_row: dict[str, Any], pre_close: float, calibration_move: float
) -> bool:
    """Whether the ex-date actually traded, around the stated reference.

    The exchange bounds a session's trades to the band around its own
    reference; a traded close outside it would mean the reference read here
    is not the operative one.  A session with no trade at all cannot verify
    the reference and refuses the settlement.  The volume column is the
    supplier-native ``vol`` (tushare's daily shape); ``volume`` is accepted
    for frames that carry the renamed form.
    """
    volume = _as_float(ex_row.get("vol", ex_row.get("volume")))
    close = _as_float(ex_row.get("close"))
    if volume is None or volume <= 0.0 or close is None or close <= 0.0:
        return False
    return abs(close / pre_close - 1.0) <= calibration_move


def _tdx_attests_the_event(
    frame_for: Callable[[str], Any] | None, symbol: str, ex_date: date
) -> bool:
    """Whether TDX's category-1 除权除息 record sits at the ex-date.

    A required leg: without an attested exchange event the reference
    difference is indistinguishable from a data defect.  An absent channel
    attests nothing and refuses the settlement.
    """
    if frame_for is None:
        return False
    frame = frame_for(symbol)
    if frame is None or frame.empty or "category" not in frame.columns:
        return False
    distribution = frame[frame["category"] == 1]
    for value in distribution["date"]:
        if _as_date(value) == ex_date:
            return True
    return False


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number
