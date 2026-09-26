"""The wide factor frame narrowed to one symbol's price-event dates."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import ContractError, DataRequest
from stock_quant.data_sources.xingyao_factor import (
    factor_event_dates,
    snapshot_result,
)


def _wide(values: list[float | None], symbol: str = "000001.SZ") -> pd.DataFrame:
    index = pd.to_datetime(
        [f"2024-01-{day:02d}" for day in range(2, 2 + len(values))]
    )
    return pd.DataFrame({symbol: values}, index=index)


def test_a_change_in_the_factor_is_the_event_and_the_first_row_is_the_baseline():
    frame = _wide([1.0, 1.0, 1.05, 1.05, 1.10])
    assert factor_event_dates(frame, "000001.SZ") == [
        date(2024, 1, 4),
        date(2024, 1, 6),
    ]


def test_leading_and_trailing_nulls_are_dropped_not_treated_as_changes():
    frame = _wide([None, None, 1.0, 1.0, 1.2, None])
    assert factor_event_dates(frame, "000001.SZ") == [date(2024, 1, 6)]


def test_an_interior_null_never_invents_an_event():
    """A missing cell is an absent observation, not a factor change."""
    frame = _wide([1.0, None, 1.0, 1.1])
    assert factor_event_dates(frame, "000001.SZ") == [date(2024, 1, 5)]


def test_rows_are_ordered_by_date_before_comparison():
    frame = _wide([1.0, 1.05, 1.05])
    frame = frame.iloc[::-1]
    assert factor_event_dates(frame, "000001.SZ") == [date(2024, 1, 3)]


def test_a_duplicated_date_with_the_same_value_collapses():
    index = pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"])
    frame = pd.DataFrame({"000001.SZ": [1.0, 1.0, 1.1]}, index=index)
    assert factor_event_dates(frame, "000001.SZ") == [date(2024, 1, 3)]


def test_a_duplicated_date_with_conflicting_values_is_a_contract_break():
    index = pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"])
    frame = pd.DataFrame({"000001.SZ": [1.0, 2.0, 1.1]}, index=index)
    with pytest.raises(ContractError, match="2024-01-02"):
        factor_event_dates(frame, "000001.SZ")


def test_a_frame_without_the_requested_symbol_column_is_a_contract_break():
    with pytest.raises(ContractError, match="000002.SZ"):
        factor_event_dates(_wide([1.0, 1.1]), "000002.SZ")


def test_a_single_valid_row_has_no_event():
    assert factor_event_dates(_wide([1.0]), "000001.SZ") == []


def test_the_snapshot_keeps_the_supplier_wide_frame_and_a_rebuildable_request():
    frame = _wide([1.0, 1.1])
    result = snapshot_result("000001.SZ", frame, end=date(2024, 1, 31))
    assert result.source == "xingyao"
    assert result.endpoint == "backward_factor"
    assert result.frame.index.equals(frame.index)
    parameters = __import__("json").loads(result.metadata["request_parameters"])
    assert parameters["symbols"] == ["000001.SZ"]
    assert parameters["end_date"] == "2024-01-31"


def test_the_factor_source_re_asks_the_recorded_question(monkeypatch):
    """The audit's re-fetch must reproduce the recorded request exactly."""
    import stock_quant.data_sources.xingyao_factor as module

    frame = _wide([1.0, 1.1])
    asked: dict[str, object] = {}

    def _fake_frame(symbol, *, timeout_seconds, end=None):
        asked.update(symbol=symbol, timeout_seconds=timeout_seconds, end=end)
        return frame

    monkeypatch.setattr(module, "fetch_factor_frame", _fake_frame)
    recorded = snapshot_result("000001.SZ", frame, end=date(2024, 1, 31))

    result = module.XingyaoFactorSource(SourceConfig()).fetch(
        DataRequest(
            "backward_factor", ("000001.SZ",), date(1990, 12, 19), date(2024, 1, 31)
        )
    )

    assert asked["symbol"] == "000001.SZ"
    assert asked["end"] == date(2024, 1, 31)
    assert result.request_key == recorded.request_key
    assert (
        result.metadata["request_parameters"]
        == recorded.metadata["request_parameters"]
    )


def test_the_factor_source_refuses_any_other_endpoint():
    module = __import__(
        "stock_quant.data_sources.xingyao_factor", fromlist=["XingyaoFactorSource"]
    )
    with pytest.raises(ValueError, match="backward_factor"):
        module.XingyaoFactorSource(SourceConfig()).fetch(
            DataRequest("daily", ("000001.SZ",), date(2024, 1, 2), date(2024, 1, 5), {})
        )


def test_the_factor_source_refuses_more_than_one_symbol():
    module = __import__(
        "stock_quant.data_sources.xingyao_factor", fromlist=["XingyaoFactorSource"]
    )
    with pytest.raises(ValueError, match="one symbol"):
        module.XingyaoFactorSource(SourceConfig()).fetch(
            DataRequest(
                "backward_factor",
                ("000001.SZ", "600000.SH"),
                date(1990, 12, 19),
                date(2024, 1, 31),
                {},
            )
        )
