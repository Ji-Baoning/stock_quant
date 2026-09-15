"""``parse_trade_date`` missing-value contract.

The helper is the single date reader for corporate actions, the suspension
proof, the trading calendar and daily normalization, and it is annotated
``date | None``.  Every spelling of "no date" the suppliers emit must
therefore come back as ``None`` -- including ``pandas.NaT``, which reaches the
parser from AKShare dividend frames and is a ``datetime`` *instance*, so a
guard ordered after the ``isinstance`` branches never sees it.
"""

import datetime

import pandas as pd
import pytest

from stock_quant.data_model.clean import parse_trade_date


def test_nat_reads_as_missing():
    """``NaT`` must not survive as a date: the annotation promises ``None``."""
    assert parse_trade_date(pd.NaT) is None


@pytest.mark.parametrize("value", [None, float("nan"), pd.NaT, ""])
def test_every_missing_spelling_reads_as_none(value):
    assert parse_trade_date(value) is None


def test_present_dates_still_parse_unchanged():
    assert parse_trade_date("20200611") == datetime.date(2020, 6, 11)
    assert parse_trade_date("2020-06-11") == datetime.date(2020, 6, 11)
    assert parse_trade_date(datetime.date(2020, 6, 11)) == datetime.date(2020, 6, 11)
    assert parse_trade_date(pd.Timestamp("2020-06-11")) == datetime.date(2020, 6, 11)
