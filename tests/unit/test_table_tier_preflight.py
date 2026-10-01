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


def test_not_fetched_input_tables_detected():
    from stock_quant.data_model.fetch_coverage import not_fetched_input_tables

    build_config = {
        "table_fetch_coverage": {
            "daily_bar": [
                {"table": "daily_bar", "kind": "not_fetched",
                 "window_start": "2021-01-04", "window_end": "2026-08-28",
                 "reason": "operator_explicit_window"},
            ],
            "income": [
                {"table": "income", "kind": "fetched",
                 "window_start": "2026-08-01", "window_end": "2026-08-28"},
            ],
        }
    }
    assert not_fetched_input_tables(build_config, ("income",)) == []
    assert not_fetched_input_tables(build_config, ("daily_bar",)) == ["daily_bar"]


def test_window_scoped_not_fetched_reasons_are_not_whole_table_skips():
    """The §7.5.1/2 zone reasons are partial-coverage, not whole-table skips.

    A ``history_begins_after_anchor`` prefix is legal pinned coverage; a run
    over it is judged window-aware by ``table_unsupported_window_tables``
    (``table_history_start_after_window``), so it must not trip the
    whole-table ``table_not_fetched`` routing (spec §7.5.3).
    """
    from stock_quant.data_model.fetch_coverage import not_fetched_input_tables

    build_config = {
        "table_fetch_coverage": {
            "daily_bar": [
                {"table": "daily_bar", "kind": "not_fetched",
                 "window_start": "2021-01-04", "window_end": "2023-12-29",
                 "reason": "history_begins_after_anchor"},
                {"table": "daily_bar", "kind": "fetched",
                 "window_start": "2023-12-30", "window_end": "2026-08-28"},
            ],
        }
    }
    assert not_fetched_input_tables(build_config, ("daily_bar",)) == []


def test_unsupported_window_tables_detected():
    """Windows inside a pinned not-fetched zone route to the new violation.

    The runner appends ``table_history_start_after_window`` for the tables
    this helper returns (RESEARCH) and counts the same code into the
    ENGINEERING RESEARCH-ONLY exemption family; the routing itself is covered
    by the integration preflight tests.
    """
    from datetime import date

    from stock_quant.data_model.fetch_coverage import (
        table_unsupported_window_tables,
    )

    build_config = {
        "table_fetch_coverage": {
            "daily_bar": [
                {"table": "daily_bar", "kind": "not_fetched",
                 "window_start": "2021-01-04", "window_end": "2023-12-29",
                 "reason": "history_begins_after_anchor"},
                {"table": "daily_bar", "kind": "fetched",
                 "window_start": "2023-12-30", "window_end": "2026-08-28"},
            ],
            "income": [
                {"table": "income", "kind": "fetched",
                 "window_start": "2021-01-04", "window_end": "2026-08-28"},
            ],
        }
    }
    assert table_unsupported_window_tables(
        build_config, ("daily_bar", "income"), date(2024, 1, 1), date(2024, 6, 30)
    ) == []
    assert table_unsupported_window_tables(
        build_config, ("daily_bar", "income"), date(2022, 1, 1), date(2022, 6, 30)
    ) == ["daily_bar"]
