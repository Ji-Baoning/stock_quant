"""Two-axis absent-ex-date classification (ADR-009 decisions 1-3).

The six rows of the ADR's measurement table are the fixtures: the supplier
axis (stated / absent) crossed with the market's price-event view at the
probe date (observed / bracketed-empty / unknown).  The bracketing test is
decision 2 verbatim: only a date strictly between a channel's first and last
reported events is covered by that channel's silence; a reported event at
the date is an observed adjustment and overrides any absence (decision 3);
an unbracketed row stays unknown and blocking (fail-closed).
"""

from datetime import date

from stock_quant.data_model.ex_date_classification import (
    MARKET_ADJUSTMENT_OBSERVED,
    MARKET_NO_ADJUSTMENT_BRACKETED,
    MARKET_UNKNOWN,
    SUPPLIER_ABSENT,
    SUPPLIER_STATED,
    classify_ex_date,
    market_view,
)

_TDX = ("tdx", [date(2015, 1, 5), date(2018, 9, 19), date(2024, 6, 7)])
_BAOSTOCK = (
    "baostock",
    [date(2016, 3, 2), date(2018, 9, 19), date(2021, 12, 22)],
)


def test_row_1_bracketed_empty_asserts_absence():
    """Both channels bracket 2019 and are empty there: absence is asserted."""
    classification = classify_ex_date(
        supplier_ex_date=None,
        probe_date=date(2019, 5, 10),
        channels=(_TDX, _BAOSTOCK),
    )
    assert classification.supplier_axis == SUPPLIER_ABSENT
    assert classification.market == MARKET_NO_ADJUSTMENT_BRACKETED
    assert classification.code == "absent+no_adjustment_bracketed_empty"


def test_row_2_an_observed_adjustment_overrides_the_absent_ex_date():
    """A channel reporting an event at the probe date wins over absence."""
    tdx = ("tdx", [date(2015, 1, 5), date(2026, 6, 22), date(2026, 9, 2)])
    classification = classify_ex_date(
        supplier_ex_date=None,
        probe_date=date(2026, 6, 22),
        channels=(tdx, _BAOSTOCK),
    )
    assert classification.market == MARKET_ADJUSTMENT_OBSERVED
    assert classification.code == "absent+adjustment_observed"


def test_row_3_no_bracketing_channel_stays_unknown():
    """A probe date outside every channel's ends asserts nothing (blocking)."""
    classification = classify_ex_date(
        supplier_ex_date=None,
        probe_date=date(1996, 5, 16),
        channels=(_TDX, _BAOSTOCK),
    )
    assert classification.market == MARKET_UNKNOWN
    assert classification.code == "absent+unknown"


def test_row_4_a_stated_ex_date_is_classified_on_the_market_axis_too():
    classification = classify_ex_date(
        supplier_ex_date=date(2021, 6, 11),
        probe_date=date(2021, 6, 11),
        channels=(_TDX,),
    )
    assert classification.supplier_axis == SUPPLIER_STATED
    assert classification.code == "stated+no_adjustment_bracketed_empty"


def test_row_5_stated_ex_date_with_an_observed_adjustment():
    classification = classify_ex_date(
        supplier_ex_date=date(2021, 12, 22),
        probe_date=date(2021, 12, 22),
        channels=(
            ("tdx", [date(2021, 12, 22)]),
            ("baostock", [date(2021, 12, 22), date(2022, 3, 1)]),
        ),
    )
    assert classification.code == "stated+adjustment_observed"


def test_row_6_stated_ex_date_no_bracketing_channel():
    classification = classify_ex_date(
        supplier_ex_date=date(2022, 1, 4),
        probe_date=date(2025, 6, 1),
        channels=(_TDX, _BAOSTOCK),
    )
    assert classification.code == "stated+unknown"


def test_an_absent_channel_asserts_nothing():
    """A None channel is unavailable and must not license an absence claim."""
    assert market_view(None, date(2019, 5, 10)) == MARKET_UNKNOWN
    classification = classify_ex_date(
        supplier_ex_date=None,
        probe_date=date(2019, 5, 10),
        channels=(("baostock", None),),
    )
    assert classification.code == "absent+unknown"


def test_an_empty_event_list_cannot_bracket():
    """A channel that reported no events at all has no ends to bracket with."""
    assert market_view([], date(2019, 5, 10)) == MARKET_UNKNOWN


def test_the_ends_of_a_list_are_not_covered():
    """Strictly-between is the bracket; the first/last events are not it."""
    dates = [date(2015, 1, 5), date(2018, 9, 19), date(2024, 6, 7)]
    assert market_view(dates, date(2015, 1, 5)) == MARKET_ADJUSTMENT_OBSERVED
    assert market_view(dates, date(2024, 6, 7)) == MARKET_ADJUSTMENT_OBSERVED
    assert market_view(dates, date(2014, 1, 1)) == MARKET_UNKNOWN
    assert market_view(dates, date(2026, 1, 1)) == MARKET_UNKNOWN


def test_an_observed_adjustment_beats_another_channel_s_bracketed_empty():
    """Decision 3: an event row at the date overrides an absence claim."""
    bracketing = ("tdx", [date(2015, 1, 5), date(2024, 6, 7)])
    observing = ("baostock", [date(2019, 5, 10)])
    classification = classify_ex_date(
        supplier_ex_date=None,
        probe_date=date(2019, 5, 10),
        channels=(bracketing, observing),
    )
    assert classification.market == MARKET_ADJUSTMENT_OBSERVED


def test_no_probe_date_reads_unknown_not_a_special_verdict():
    """Without an anchor date the market question cannot even be asked."""
    classification = classify_ex_date(
        supplier_ex_date=None, probe_date=None, channels=(_TDX,)
    )
    assert classification.code == "absent+unknown"
    assert classification.probe_date is None


# --------------------------------------------------------------------------- #
# baostock's adjustment-factor series as an event-date channel (ADR-009 #3)
# --------------------------------------------------------------------------- #


def _factor_frame(rows):
    """A native-shape factor series: one row per (date, cumulative factor)."""
    import pandas as pd

    return pd.DataFrame(
        [
            {
                "code": "sz.600519",
                "dividOperateDate": day,
                "foreAdjustFactor": value,
                "backAdjustFactor": value,
                "adjustFactor": value,
            }
            for day, value in rows
        ]
    )


def test_a_factor_change_is_an_event_and_a_flat_row_is_not():
    """The artifact row copies the factor forward; only changes are events."""
    from stock_quant.data_sources.baostock_factor import factor_event_dates

    frame = _factor_frame(
        [
            ("2015-01-05", "1.000000"),
            ("2018-09-19", "1.304348"),
            ("2019-07-19", "1.304348"),  # ADR-009's no-change artifact
            ("2024-06-07", "1.500000"),
        ]
    )
    assert factor_event_dates(frame) == [date(2018, 9, 19), date(2024, 6, 7)]


def test_the_first_factor_row_is_a_baseline_never_an_event():
    """A lone row asserts a baseline, so its date cannot read as observed."""
    from stock_quant.data_sources.baostock_factor import factor_event_dates

    assert factor_event_dates(_factor_frame([("2015-01-05", "1.000000")])) == []


def test_an_absent_or_empty_series_answers_no_events():
    """A missing frame and an empty one both assert nothing."""
    from stock_quant.data_sources.baostock_factor import factor_event_dates

    assert factor_event_dates(None) == []
    assert factor_event_dates(_factor_frame([])) == []


def test_logout_runs_while_the_socket_timeout_still_binds(monkeypatch):
    """The 2026-09-21 hang: a logout recv after the restore is unbounded.

    The flaky :10030 server stalls reads; every read of the fetch -- logout
    included -- must run while the default socket timeout is bound.
    """
    import socket as socket_module
    import sys

    from stock_quant.data_sources import baostock_factor

    events: list[str] = []
    real_setdefault = socket_module.setdefaulttimeout

    class _Response:
        fields = ["code", "dividOperateDate", "adjustFactor"]
        error_code = "0"

        def next(self):
            return False

    class _Stub:
        __version__ = "stub"

        def login(self):
            events.append("login")
            return _Response()

        def query_adjust_factor(self, code, start_date, end_date):
            events.append("query")
            return _Response()

        def logout(self):
            events.append("logout")

    class _PatchedSocketModule:
        def __getattr__(self, name):
            return getattr(socket_module, name)

        def setdefaulttimeout(self, value):
            events.append(f"timeout={value}")
            real_setdefault(value)

    monkeypatch.setitem(sys.modules, "baostock", _Stub())
    monkeypatch.setattr(baostock_factor, "socket", _PatchedSocketModule())

    baostock_factor.fetch_adjust_factor_frames(["600519.SH"], timeout=30)

    assert events == [
        "timeout=30",
        "login",
        "query",
        "logout",
        f"timeout={socket_module.getdefaulttimeout()}",
    ]
