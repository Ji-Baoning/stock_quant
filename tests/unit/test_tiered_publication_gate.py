# tests/unit/test_tiered_publication_gate.py
"""Gate tests: unregistered tables are FATAL (D2); tier-aware predicate (D1).

D1 contract: global process codes always block; table-level blocking codes
route by the ``(code, table)`` evidence tier, with undeclared tables
fail-closed as core.
"""

from __future__ import annotations

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.data_pipeline import _contract_issues
from stock_quant.data_quality.gates import (
    GLOBAL_PROCESS_CODES,
    TABLE_LEVEL_BLOCKING_CODES,
    evaluate_publication,
)
from stock_quant.data_quality.models import (
    CODE_QUARANTINE_MISSING_REASON,
    CODE_REPORT_GENERATION_FAILED,
    CODE_SCHEMA_MISMATCH,
    CODE_UNREGISTERED_TABLE,
    QualityIssue,
    QualityReport,
    Severity,
)

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


def _report(code: str, table: str = "income") -> QualityReport:
    return QualityReport(
        issues=(QualityIssue(Severity.ERROR, code, table=table),)
    )


def test_global_codes_block_even_on_anchored_table():
    for code in (CODE_REPORT_GENERATION_FAILED, CODE_QUARANTINE_MISSING_REASON):
        decision = evaluate_publication(
            _report(code), table_tiers={"income": "anchored"}
        )
        assert not decision.passed, code


def test_table_code_downgrades_anchored_table():
    decision = evaluate_publication(
        _report(CODE_SCHEMA_MISMATCH), table_tiers={"income": "anchored"}
    )
    assert decision.passed


def test_table_code_downgrades_research_only_table():
    decision = evaluate_publication(
        _report(CODE_SCHEMA_MISMATCH), table_tiers={"income": "research_only"}
    )
    assert decision.passed


def test_table_code_blocks_core_table():
    decision = evaluate_publication(
        _report(CODE_SCHEMA_MISMATCH), table_tiers={"income": "core"}
    )
    assert not decision.passed


def test_undeclared_table_blocks_fail_closed():
    decision = evaluate_publication(_report(CODE_SCHEMA_MISMATCH))
    assert not decision.passed
    with_tiers = evaluate_publication(
        _report(CODE_SCHEMA_MISMATCH), table_tiers={"daily_bar": "anchored"}
    )
    assert not with_tiers.passed


def test_tierless_default_matches_legacy_behavior():
    decision = evaluate_publication(_report(CODE_SCHEMA_MISMATCH))
    assert not decision.passed


def test_unregistered_table_is_global_process_code():
    assert CODE_UNREGISTERED_TABLE in GLOBAL_PROCESS_CODES


def test_blocking_codes_partition():
    from stock_quant.data_quality.gates import PUBLICATION_BLOCKING_CODES

    assert TABLE_LEVEL_BLOCKING_CODES | GLOBAL_PROCESS_CODES == (
        PUBLICATION_BLOCKING_CODES
    )
    assert not (TABLE_LEVEL_BLOCKING_CODES & GLOBAL_PROCESS_CODES)
