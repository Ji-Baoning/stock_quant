"""Criterion 1 (publish side): a downgraded anchored table publishes, a core
one blocks; the published quality report carries the UNTRUSTED record.

The frame builders below are copied from the existing publication tests
(``tests/integration/test_dataset_publish.py`` for ``daily_bar`` and the
integration ``conftest.py`` fixture for the typed empty ``corporate_action``
frame) so the staged frames satisfy the canonical schemas exactly.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from stock_quant.data_model.dataset import DatasetPublisher, PublicationBlocked
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    DAILY_COLUMNS,
)
from stock_quant.data_pipeline import _downgrade_issues
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import (
    CODE_COVERAGE_DOWNGRADED,
    CODE_SCHEMA_MISMATCH,
    QualityIssue,
    QualityReport,
    Severity,
)

TRADING_DAYS = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]
INGESTED = pd.Timestamp("2020-01-06T08:00:00Z")


def _report() -> QualityReport:
    return QualityReport(
        issues=(
            QualityIssue(
                Severity.ERROR, CODE_SCHEMA_MISMATCH, table="corporate_action"
            ),
        )
    )


def _downgrade_record(issues):
    assert len(issues) == 1
    issue = issues[0]
    assert issue.code == CODE_COVERAGE_DOWNGRADED
    assert issue.table == "corporate_action"
    assert issue.severity is Severity.WARNING
    details = issue.details
    assert details["status"] == "UNTRUSTED"
    assert details["reason_codes"] == [CODE_SCHEMA_MISMATCH]
    assert details["window_start"] is None and details["window_end"] is None
    assert details["symbols"] is None  # table-level issue: whole table


def test_downgrade_emitted_for_anchored_table():
    contracts = {
        "corporate_action": type("C", (), {"tier": "anchored"}),  # tier only
    }
    _downgrade_record(_downgrade_issues(_report().issues, contracts))


def test_downgrade_accepts_declared_tier_strings():
    """The tier mapping the publish path passes is also a valid contract view."""
    _downgrade_record(
        _downgrade_issues(_report().issues, {"corporate_action": "anchored"})
    )


def test_no_downgrade_for_core_table():
    contracts = {"corporate_action": type("C", (), {"tier": "core"})}
    assert _downgrade_issues(_report().issues, contracts) == []


def test_no_downgrade_for_undeclared_table():
    """Fail-closed: a missing declaration blocks at the gate, never downgrades."""
    assert _downgrade_issues(_report().issues, {}) == []


def test_gate_admits_anchored_table_only_with_declared_tier():
    # Without tiers every table is undeclared, so the schema issue blocks
    # (fail-closed); the declared anchored tier lets it through and publish
    # records the downgrade evidence instead.
    assert evaluate_publication(_report()).passed is False
    assert (
        evaluate_publication(
            _report(), table_tiers={"corporate_action": "anchored"}
        ).passed
        is True
    )


def test_publish_downgraded_anchored_table_and_block_core(tmp_path):
    daily = _daily_frame([10.5, 10.8, 11.0])
    actions = _corporate_action_frame()
    tables = {"daily_bar": daily, "corporate_action": actions}

    tiers = {"daily_bar": "core", "corporate_action": "anchored"}
    publisher = DatasetPublisher(tmp_path)
    dataset_ref = publisher.publish(
        tables,
        _report_with_downgrade(_report(), tiers),
        build_config={"requested_start_date": "2024-01-02",
                      "resolved_end_date": "2024-01-31",
                      "full_history_acceptance_start": "2024-01-02"},
        table_tiers=tiers,
    )
    stored = json.loads(
        (dataset_ref.path / "quality_report.json").read_text(encoding="utf-8")
    )
    codes = {item["code"] for item in stored["issues"]}
    assert CODE_COVERAGE_DOWNGRADED in codes

    blocked = DatasetPublisher(tmp_path / "blocked")
    with pytest.raises(PublicationBlocked):
        blocked.publish(
            tables,
            _report_with_downgrade(_report(), {"daily_bar": "core",
                                               "corporate_action": "core"}),
            build_config={"requested_start_date": "2024-01-02",
                          "resolved_end_date": "2024-01-31"},
            table_tiers={"daily_bar": "core", "corporate_action": "core"},
        )


def _report_with_downgrade(report, contracts):
    issues = list(report.issues) + _downgrade_issues(report.issues, contracts)
    return QualityReport(issues=tuple(issues))


# ---- canonical frame builders (copied from the existing publish tests) ----- #


def _daily_frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(TRADING_DAYS),
            "symbol": ["600000.SH"] * len(closes),
            "open": [c - 0.5 for c in closes],
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [100] * len(closes),
            "amount": [c * 100 for c in closes],
            "adjustment": ["unadjusted"] * len(closes),
            "source": ["baostock"] * len(closes),
            "ingested_at": [INGESTED] * len(closes),
        }
    )[DAILY_COLUMNS]


def _corporate_action_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series([], dtype="object"),
            "announcement_date": pd.Series([], dtype="object"),
            "record_date": pd.Series([], dtype="object"),
            "ex_date": pd.Series([], dtype="object"),
            "cash_dividend_per_share": pd.Series([], dtype="float64"),
            "bonus_share_ratio": pd.Series([], dtype="float64"),
            "capitalization_ratio": pd.Series([], dtype="float64"),
            "rights_issue_ratio": pd.Series([], dtype="float64"),
            "rights_issue_price": pd.Series([], dtype="float64"),
            "source": pd.Series([], dtype="object"),
            "status": pd.Series([], dtype="object"),
        }
    )[CORPORATE_ACTION_COLUMNS]
