"""The ADR-019 price-basis settler: the exchange reference as holder basis.

Every fixture is authored inline around `600518.SH`'s real December 2021
shape: the halt day carries no row in the supplier's per-symbol response, the
resumption day states the exchange's 除权参考价 as ``pre_close``, and the
traded prices form the limit-down ladder the calibration band reads.  The
settler must book exactly that shape and refuse every weaker one.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.data_sources.price_basis import (
    CALIBRATION_DAYS,
    MIN_CALIBRATION_ROWS,
    PriceBasisSettler,
)

EX = date(2021, 12, 15)


def _ladder_frame(
    *,
    prev_close: float = 4.58,
    pre_close: float = 4.30,
    ex_close: float = 4.09,
    ex_volume: float = 17_556_800.0,
    rows: int = 60,
    halt_gap: bool = True,
    end: date = EX,
) -> pd.DataFrame:
    """A supplier-native daily frame: a calm ladder, the halt gap, the ex row.

    Built backwards from the last close before the halt: one real 5.07%
    limit day immediately before the gap (the ST regime's mark), calm ±0.3%
    alternations before it, then -- the gap shape -- no row on the halt day
    itself, and the ex-date row stating the exchange's reference as
    ``pre_close``.
    """
    halt_day = end - timedelta(days=1) if halt_gap else None
    closes: list[float] = []
    day = end
    while len(closes) < rows + 1:
        day -= timedelta(days=1)
        if day == halt_day:
            continue
        if not closes:
            closes.append(prev_close)
            continue
        step = 1.0507 if len(closes) == 1 else (1.003 if len(closes) % 2 else 0.997)
        closes.append(round(closes[-1] / step, 2))
    closes.reverse()
    dates: list[date] = []
    day = end
    while len(dates) < len(closes):
        day -= timedelta(days=1)
        if day == halt_day:
            continue
        dates.append(day)
    dates.reverse()
    records = [
        {
            "trade_date": day.isoformat(),
            "close": close,
            "pre_close": closes[index - 1] if index else close,
            "vol": 1_000_000.0,
        }
        for index, (day, close) in enumerate(zip(dates, closes))
    ]
    records.append(
        {
            "trade_date": end.isoformat(),
            "close": ex_close,
            "pre_close": pre_close,
            "vol": ex_volume,
        }
    )
    return pd.DataFrame(records)


from datetime import timedelta  # noqa: E402  (fixture helper dependency)


def _settler(frame, *, tdx_dates=(EX,), factor_ratio=None, record_raw=None):
    snapshots: list = []

    def fetch_daily(symbol, start, end):
        return frame

    def frame_for(symbol):
        if not tdx_dates:
            return pd.DataFrame(columns=["category", "date"])
        return pd.DataFrame(
            {
                "category": [1 for _ in tdx_dates],
                "date": [d.isoformat() for d in tdx_dates],
            }
        )

    settler = PriceBasisSettler(
        fetch_daily=fetch_daily,
        frame_for=frame_for,
        factor_ratio=factor_ratio,
        raw_snapshots=snapshots,
        record_raw=record_raw or (lambda result: result),
    )
    return settler, snapshots


def test_the_real_shape_settles():
    """600518's December 2021: halt gap, stated reference, limit ladder.

    The implied holder factor is the exchange's own: 4.58 / 4.30 = 1.065116,
    the number the factor series corroborates to ~3e-7.
    """
    settler, _ = _settler(_ladder_frame())
    settlement = settler.settle("600518.SH", EX)
    assert settlement is not None
    assert settlement.prev_close == pytest.approx(4.58)
    assert settlement.pre_close == pytest.approx(4.30)
    assert settlement.factor_ratio == pytest.approx(1.065116, abs=1e-6)
    assert settlement.factor_series_ratio is None  # no channel configured
    # The gap shape is the one ADR-013 declined: the row before the ex-date
    # is two open days back, and the settlement records that date.
    assert settlement.prev_close_date == EX - timedelta(days=2)


def test_a_stated_reference_inside_the_limit_band_refuses():
    """No falsification, no booking: the reference could be a mere price.

    A reference within the calibration band is exactly the shape a data
    defect would produce -- the settlement must not book past it.
    """
    frame = _ladder_frame(pre_close=4.55, ex_close=4.35)
    settler, _ = _settler(frame)
    assert settler.settle("600518.SH", EX) is None


def test_a_reference_without_traded_ex_day_refuses():
    """An ex-date with no trade cannot verify the reference's meaning."""
    frame = _ladder_frame(ex_volume=0.0)
    settler, _ = _settler(frame)
    assert settler.settle("600518.SH", EX) is None


def test_a_thin_calibration_window_refuses():
    """A band built on too few traded rows proves nothing."""
    frame = _ladder_frame(rows=5)
    settler, _ = _settler(frame)
    assert settler.settle("600518.SH", EX) is None


def test_no_tdx_event_at_the_date_refuses():
    """The reference difference without an attested event is a defect shape."""
    settler, _ = _settler(_ladder_frame(), tdx_dates=())
    assert settler.settle("600518.SH", EX) is None


def test_an_absent_tdx_channel_refuses():
    """The required leg cannot be skipped: None frame_for fails closed."""
    frame = _ladder_frame()

    def fetch_daily(symbol, start, end):
        return frame

    settler = PriceBasisSettler(fetch_daily=fetch_daily, frame_for=None)
    assert settler.settle("600518.SH", EX) is None


def test_an_agreeing_factor_series_is_recorded():
    """The corroboration leg records the series' own ratio when it agrees."""
    frame = _ladder_frame()
    settler, _ = _settler(
        frame, factor_ratio=lambda symbol, ex: 1.065116
    )
    settlement = settler.settle("600518.SH", EX)
    assert settlement is not None
    assert settlement.factor_series_ratio == pytest.approx(1.065116)


def test_a_disagreeing_factor_series_fails_closed():
    """A stated series moving by a different ratio is ADR-009's dispute."""
    settler, _ = _settler(
        _ladder_frame(), factor_ratio=lambda symbol, ex: 2.8
    )
    assert settler.settle("600518.SH", EX) is None


def test_a_failing_factor_reader_fails_closed():
    """A configured channel that cannot answer must not be skipped silently."""
    def broken(symbol, ex):
        raise RuntimeError("channel down")

    settler, _ = _settler(_ladder_frame(), factor_ratio=broken)
    assert settler.settle("600518.SH", EX) is None


def test_a_channel_failure_is_remembered_not_retried():
    """One fetch failure: the symbol's channel is absent for the run."""
    calls: list[str] = []

    def fetch_daily(symbol, start, end):
        calls.append(symbol)
        raise RuntimeError("relay down")

    settler = PriceBasisSettler(
        fetch_daily=fetch_daily,
        frame_for=lambda symbol: None,
    )
    assert settler.settle("600518.SH", EX) is None
    assert settler.settle("600518.SH", EX) is None
    assert calls == ["600518.SH"]


def test_the_calibration_fetch_is_snapshotted():
    """The bytes the settlement read are captured for the run's evidence."""
    frame = _ladder_frame()
    captured: list = []
    settler, snapshots = _settler(
        frame, record_raw=lambda result: captured.append(result) or result
    )
    assert settler.settle("600518.SH", EX) is not None
    assert len(captured) == 1
    assert snapshots
