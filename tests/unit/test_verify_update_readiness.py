"""Unit tests for the pre-update readiness probe."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

from verify_update_readiness import (  # noqa: E402
    EXPECTED_TRADABLE_SYMBOLS,
    TRADABLE_UNIVERSE_ID,
    ReadinessIssue,
    baseline_issues,
    probe_endpoint,
    report,
    token_issue,
)


def _membership(universe_id: str, symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"universe_id": [universe_id] * len(symbols), "symbol": symbols}
    )


def test_baseline_issues_passes_for_the_trimmed_membership() -> None:
    symbols = [f"{index:06d}.SZ" for index in range(EXPECTED_TRADABLE_SYMBOLS)]
    assert baseline_issues(_membership(TRADABLE_UNIVERSE_ID, symbols)) == []


def test_baseline_issues_flags_an_untrimmed_universe_id() -> None:
    symbols = [f"{index:06d}.SZ" for index in range(EXPECTED_TRADABLE_SYMBOLS)]
    issues = baseline_issues(_membership("custom_csi300_ic", symbols))
    assert [issue.code for issue in issues] == [
        "BASELINE_UNIVERSE_ID_MISMATCH"
    ]


def test_baseline_issues_flags_a_wrong_symbol_count() -> None:
    issues = baseline_issues(_membership(TRADABLE_UNIVERSE_ID, ["000001.SZ"]))
    assert [issue.code for issue in issues] == [
        "BASELINE_SYMBOL_COUNT_MISMATCH"
    ]


def test_baseline_issues_flags_an_empty_membership() -> None:
    assert baseline_issues(None) == [
        ReadinessIssue(
            "BASELINE_MEMBERSHIP_EMPTY",
            "CURRENT dataset carries no universe_membership rows; "
            "run the plan-one trim first",
        )
    ]
    assert baseline_issues(pd.DataFrame({"universe_id": [], "symbol": []})) == (
        baseline_issues(None)
    )


def test_token_issue_flags_a_missing_token() -> None:
    assert token_issue({}) is not None
    assert token_issue({"TUSHARE_TOKEN": "   "}) is not None
    assert token_issue({"TUSHARE_TOKEN": "abc"}) is None


def test_token_issue_accepts_proxy_credentials_without_a_token() -> None:
    assert token_issue({"TUSHARE_PROXY_URL": "https://proxy.example"}) is not None
    assert (
        token_issue(
            {
                "TUSHARE_PROXY_URL": "https://proxy.example",
                "TUSHARE_PROXY_KEY": "key-123",
            }
        )
        is None
    )


def test_probe_endpoint_reports_a_raising_supplier() -> None:
    def boom(endpoint, symbol, start, end):
        raise RuntimeError("endpoint down")

    issue, count = probe_endpoint(
        boom, "tushare.daily", "600519.SH", date(2015, 1, 1), date(2026, 8, 28)
    )
    assert isinstance(issue, ReadinessIssue)
    assert issue.code == "SOURCE_PROBE_FAILED"
    assert "RuntimeError" in issue.message
    assert count == 0


def test_probe_endpoint_accepts_an_empty_frame() -> None:
    def empty(endpoint, symbol, start, end):
        return pd.DataFrame()

    issue, count = probe_endpoint(
        empty,
        "cninfo_corporate_actions",
        "600519.SH",
        date(2015, 1, 1),
        date(2026, 8, 28),
    )
    assert issue is None
    assert count == 0


def test_report_marks_not_ready_when_issues_exist() -> None:
    text = report([ReadinessIssue("X", "boom")], {"tushare.daily": 3})
    assert "ready=false" in text
    assert "probe tushare.daily rows=3" in text
    assert "issue X boom" in text
    assert "ready=true" in report([], {})
