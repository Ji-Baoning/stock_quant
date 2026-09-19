"""D2 contract-shape parsing: sources.yml ``data_contracts`` declarations."""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_quant.config import ProjectConfig
from stock_quant.data_contracts import DataContract, parse_data_contracts


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "table": "income",
        "tier": "anchored",
        "primary_transport": "tushare:relay",
        "anchors": ["akshare_cninfo_announcement"],
        "conflict": "downgrade",
        "pit": {
            "as_of_field": "f_ann_date",
            "fallback": "ann_date",
            "fact_row_policy": "max_report_type_v1",
        },
        "coverage_shape": "per_symbol_window",
        "incremental": "disclosure_calendar",
    }
    row.update(overrides)
    return row


def test_valid_anchored_contract_parses():
    parsed = parse_data_contracts([_row()])
    contract = parsed["income"]
    assert isinstance(contract, DataContract)
    assert contract.tier == "anchored"
    assert contract.pit is not None and contract.pit.fallback == "ann_date"


def test_missing_key_rejected():
    row = _row()
    del row["incremental"]
    with pytest.raises(ValueError):
        parse_data_contracts([row])


def test_unknown_key_rejected():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(invented_key="x")])


def test_tier_vocabulary_closed():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(tier="important")])


def test_transport_kind_must_be_token():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(primary_transport="tushare")])
    with pytest.raises(ValueError):
        parse_data_contracts([_row(primary_transport="tushare:sdksdk")])


def test_anchored_requires_anchor():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(anchors=[])])


def test_core_and_research_only_forbid_anchors():
    with pytest.raises(ValueError):
        parse_data_contracts(
            [_row(tier="core", anchors=["akshare_cninfo_announcement"])]
        )
    with pytest.raises(ValueError):
        parse_data_contracts(
            [_row(tier="research_only", anchors=["akshare_cninfo_announcement"])]
        )


def test_duplicate_table_rejected():
    with pytest.raises(ValueError):
        parse_data_contracts([_row(), _row()])


def test_project_config_carries_contracts():
    contracts = parse_data_contracts([_row()])
    config = ProjectConfig.model_validate(
        {
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "initial_cash": 100000,
            "benchmark_symbols": ["000300.SH"],
            "data_contracts": contracts,
        }
    )
    assert config.data_contracts["income"].tier == "anchored"
