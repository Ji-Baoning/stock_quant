"""Tier violations over declared input tables (spec A2 / D1 consumer gate)."""

from __future__ import annotations

import pytest

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.data_quality.models import CODE_SCHEMA_MISMATCH
from stock_quant.research.runner import (
    factor_input_tables,
    table_tier_violations,
)


class _Factor:
    def __init__(self, inputs):
        self.inputs = tuple(inputs)


class _NoInputsFactor:
    pass


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
        },
        {
            "table": "income",
            "tier": "anchored",
            "primary_transport": "tushare:relay",
            "anchors": ["akshare_cninfo_announcement"],
            "conflict": "downgrade",
            "pit": None,
            "coverage_shape": "per_symbol_window",
            "incremental": "disclosure_calendar",
        },
        {
            "table": "industry_classify",
            "tier": "research_only",
            "primary_transport": "tushare:relay",
            "anchors": [],
            "conflict": "block",
            "pit": None,
            "coverage_shape": "none",
            "incremental": "change_driven_full",
        },
    ]
)


def test_input_tables_aggregate_across_factors():
    tables = factor_input_tables(
        {"a": _Factor(["daily_bar"]), "b": _Factor(["income"])}
    )
    assert tables == {"a": ("daily_bar",), "b": ("income",)}


def test_factor_without_inputs_fails_closed():
    with pytest.raises(ValueError, match="inputs"):
        factor_input_tables({"bad": _NoInputsFactor()})


def test_core_inputs_pass_in_research_mode():
    assert table_tier_violations(CONTRACTS, ("daily_bar",), (), "research") == []


def test_research_only_rejected_in_research_mode():
    codes = table_tier_violations(
        CONTRACTS, ("industry_classify",), (), "research"
    )
    assert "table_tier_research_only" in codes


def test_research_only_exempt_in_engineering_mode():
    codes = table_tier_violations(
        CONTRACTS, ("industry_classify",), (), "engineering"
    )
    assert "table_tier_research_only" not in codes


def test_untrusted_anchored_rejected_in_research_mode():
    downgrades = [
        {
            "table": "income",
            "status": "UNTRUSTED",
            "reason_codes": [CODE_SCHEMA_MISMATCH],
            "symbols": None,
            "window_start": None,
            "window_end": None,
        }
    ]
    codes = table_tier_violations(CONTRACTS, ("income",), downgrades, "research")
    assert "table_tier_untrusted" in codes


def test_untrusted_anchored_exempt_in_engineering_mode():
    downgrades = [
        {
            "table": "income",
            "status": "UNTRUSTED",
            "reason_codes": [CODE_SCHEMA_MISMATCH],
            "symbols": None,
            "window_start": None,
            "window_end": None,
        }
    ]
    codes = table_tier_violations(CONTRACTS, ("income",), downgrades, "engineering")
    assert "table_tier_untrusted" not in codes


def test_undeclared_input_table_fails_closed():
    codes = table_tier_violations(CONTRACTS, ("novel_table",), (), "research")
    assert "table_tier_undeclared" in codes


def test_clean_anchored_passes_in_research_mode():
    assert table_tier_violations(CONTRACTS, ("income",), (), "research") == []
