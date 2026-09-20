"""Criterion 6: descriptor-driven frame checks — hand-written code shrinks
to descriptor + normalize + verification logic only."""

from __future__ import annotations

import pandas as pd
import pytest

from stock_quant.data_sources.descriptors import (
    EndpointDescriptor,
    TruncationDetected,
    check_frame,
    contract_test_skeleton,
    slice_plan,
)


def _descriptor(**overrides):
    values = dict(
        endpoint="daily_basic",
        keying="date_keyed",
        required_params=(),
        auto_slice=False,
        retry_categories=frozenset({"tls_eof"}),
        projection=("ts_code", "trade_date", "close"),
        cadence="daily",
    )
    values.update(overrides)
    return EndpointDescriptor(**values)


def test_6000_row_truncation_fails_closed():
    frame = pd.DataFrame({"trade_date": range(6000)})
    with pytest.raises(TruncationDetected):
        check_frame(_descriptor(), frame)


def test_auto_slice_opted_in_produces_provenance_plan():
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 12500,
            "trade_date": range(12500),
            "close": [1.0] * 12500,
        }
    )
    issues = check_frame(_descriptor(auto_slice=True), frame)
    assert issues == []
    plan = slice_plan(_descriptor(auto_slice=True), frame)
    assert plan == [(0, 6000), (6000, 12000), (12000, 12500)]


def test_projection_gap_is_flagged():
    frame = pd.DataFrame({"trade_date": range(5)})
    issues = check_frame(_descriptor(), frame)
    assert [issue["code"] for issue in issues] == ["projection_missing"]
    assert "ts_code" in issues[0]["columns"]


def test_unknown_retry_category_rejected():
    with pytest.raises(ValueError):
        _descriptor(retry_categories=frozenset({"dns_timeout"}))


def test_skeleton_is_a_runnable_pytest_shape():
    skeleton = contract_test_skeleton(_descriptor())
    assert "def test_" in skeleton
    assert "daily_basic" in skeleton
    assert "date_keyed" in skeleton
