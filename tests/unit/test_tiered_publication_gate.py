# tests/unit/test_tiered_publication_gate.py
"""Publish-path contract validation: unregistered tables are FATAL (D2)."""

from __future__ import annotations

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.data_pipeline import _contract_issues
from stock_quant.data_quality.models import CODE_UNREGISTERED_TABLE, Severity

CONTRACTS = parse_data_contracts(
    [
        {
            "table": "daily_bar",
            "tier": "core",
            "primary_transport": "tushare:relay",
            "anchors": [],
            "conflict": "downgrade",
            "pit": None,
            "coverage_shape": "none",
            "incremental": "last_covered_plus_1",
        }
    ]
)


def test_registered_table_emits_nothing():
    assert _contract_issues({"daily_bar": object()}, CONTRACTS) == []


def test_unregistered_table_is_fatal():
    issues = _contract_issues({"daily_bar": object(), "novel_table": object()}, CONTRACTS)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity is Severity.FATAL
    assert issue.code == CODE_UNREGISTERED_TABLE
    assert issue.table == "novel_table"
