"""Live xingyao contract: the only test that pins the real SDK's names.

Marked ``external``: it needs the private wheel, ``AD_*`` credentials and
network, and it is excluded from the default run.  It exists because the
unit tests drive a fake client and therefore cannot notice that
``query_kline`` was renamed upstream.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.xingyao import XingyaoSource

pytestmark = pytest.mark.external


@pytest.mark.skipif(
    not os.environ.get("AD_USERNAME"), reason="AD_* credentials are not configured"
)
def test_the_sdk_answers_a_one_week_window():
    end = date.today()
    start = end - timedelta(days=7)
    result = XingyaoSource(SourceConfig(timeout_seconds=60)).fetch(
        DataRequest(
            "daily", ("000001.SZ",), start, end, {"adjustment": "unadjusted"}
        )
    )
    assert not result.frame.empty
    assert "kline_time" in result.frame.columns
