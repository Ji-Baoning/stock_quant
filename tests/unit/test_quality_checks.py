"""Quality severity, row checks, cross-source thresholds and the neutral gate.

The publication gate checks only schema, uniqueness, illegal OHLC, provenance,
quarantine reasons and report generation. Momentum lookback and execution-date
concerns belong to ``evaluate_backtest_readiness`` and must never block here.
"""

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_quant.data_model.schemas import DAILY_COLUMNS, DAILY_SCHEMA
from stock_quant.data_pipeline import DataPipeline
from stock_quant.data_quality.compare import (
    DEFAULT_THRESHOLDS,
    ComparisonThresholds,
    compare_daily_sources,
)
from stock_quant.data_quality.gates import (
    PUBLICATION_BLOCKING_CODES,
    evaluate_publication,
)
from stock_quant.data_quality.models import (
    CODE_CLOSE_DIFFERENCE,
    CODE_DUPLICATE_CONFLICT,
    CODE_INVALID_OHLC,
    CODE_NEGATIVE_AMOUNT,
    CODE_NEGATIVE_VOLUME,
    CODE_NONPOSITIVE_PRICE,
    CODE_PRICE_DIFFERENCE,
    CODE_QUARANTINE_MISSING_REASON,
    CODE_SCHEMA_MISMATCH,
    CODE_UNKNOWN_ADJUSTMENT,
    CODE_UNKNOWN_SOURCE,
    CODE_WITHIN_TOLERANCE,
    MISSING_DELISTED,
    MISSING_NON_TRADING_DAY,
    MISSING_NOT_LISTED,
    MISSING_PRIMARY_SOURCE,
    MISSING_UNEXPLAINED,
    MISSING_UNKNOWN_OR_SUSPENDED,
    QualityIssue,
    QualityReport,
    Severity,
)
from stock_quant.data_quality.raw_checks import (
    check_daily_values,
    check_primary_key_conflicts,
    check_provenance,
    check_quarantine_reasons,
    check_schema,
    classify_missing_row,
)


def row(**values: object) -> SimpleNamespace:
    return SimpleNamespace(**values)


def issue(
    severity: Severity,
    code: str,
    *,
    symbol: str | None = None,
    trade_date: date | None = None,
    **details: object,
) -> QualityIssue:
    return QualityIssue(
        severity=severity,
        code=code,
        table="daily_bar",
        symbol=symbol,
        trade_date=trade_date,
        details=details,
    )


def _daily_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def _bar(
    symbol: str = "600000.SH",
    trade_date: object = date(2020, 1, 2),
    *,
    open: float = 10.0,
    high: float = 11.0,
    low: float = 9.0,
    close: float = 10.5,
    volume: int = 100,
    amount: float = 1050.0,
    source: str = "baostock",
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "adjustment": "unadjusted",
        "source": source,
        "ingested_at": pd.Timestamp("2020-01-03", tz="UTC"),
    }


def test_close_difference_over_point_two_percent_is_error():
    issues = compare_daily_sources(
        row(close=10.00), row(close=10.03), DEFAULT_THRESHOLDS
    )
    assert issues[0].severity is Severity.ERROR
    assert issues[0].code == CODE_CLOSE_DIFFERENCE


def test_absolute_difference_at_most_one_cent_is_info():
    issues = compare_daily_sources(
        row(close=10.00), row(close=10.01), DEFAULT_THRESHOLDS
    )
    assert issues[0].severity is Severity.INFO
    assert issues[0].code == CODE_WITHIN_TOLERANCE


def test_open_difference_above_cent_and_zero_zero_five_percent_is_warning():
    issues = compare_daily_sources(
        row(open=10.00, close=10.00), row(open=10.02, close=10.00), DEFAULT_THRESHOLDS
    )
    assert [i.severity for i in issues] == [Severity.WARNING]


def test_close_difference_between_warning_and_error_tiers_stays_warning():
    # 10.00 -> 10.015 is +0.15%: above the 0.05% warning and below the 0.20% error.
    issues = compare_daily_sources(
        row(close=10.00), row(close=10.015), DEFAULT_THRESHOLDS
    )
    assert issues[0].severity is Severity.WARNING
    assert issues[0].code == CODE_PRICE_DIFFERENCE


def test_identical_bars_produce_no_issue():
    issues = compare_daily_sources(row(close=10.0), row(close=10.0), DEFAULT_THRESHOLDS)
    assert issues == []


def test_close_error_uses_custom_threshold():
    thresholds = ComparisonThresholds(close_error_relative=0.001)
    issues = compare_daily_sources(row(close=100.00), row(close=100.12), thresholds)
    assert issues[0].severity is Severity.ERROR


def test_comparison_refuses_different_adjustment_bases():
    issues = compare_daily_sources(
        row(adjustment="unadjusted", close=10.0),
        row(adjustment="qfq", close=10.0),
        DEFAULT_THRESHOLDS,
    )
    assert len(issues) == 1
    assert issues[0].code == "adjustment_basis_mismatch"
    assert issues[0].severity is Severity.WARNING


def test_comparison_refuses_different_units():
    issues = compare_daily_sources(
        row(volume_unit="share", close=10.0),
        row(volume_unit="lot", close=10.0),
        DEFAULT_THRESHOLDS,
    )
    assert len(issues) == 1
    assert issues[0].code == "unit_mismatch"
    assert issues[0].severity is Severity.WARNING


def test_comparison_reports_both_symbol_and_date_when_present():
    (found,) = compare_daily_sources(
        row(symbol="600000.SH", trade_date=date(2020, 1, 2), close=10.0),
        row(symbol="600000.SH", trade_date=date(2020, 1, 2), close=10.03),
        DEFAULT_THRESHOLDS,
    )
    assert found.symbol == "600000.SH"
    assert found.trade_date == date(2020, 1, 2)


def test_quality_issue_defaults_allow_missing_symbol_and_date():
    found = issue(Severity.WARNING, "demo")
    assert found.symbol is None
    assert found.trade_date is None
    assert found.details == {}


def test_severity_values_are_the_contract_strings():
    assert [s.value for s in Severity] == ["INFO", "WARNING", "ERROR", "FATAL"]


def test_nonpositive_price_is_error():
    bad = _bar(close=0.0)
    issues = check_daily_values(_daily_frame([_bar(), bad]))
    assert any(
        i.code == CODE_NONPOSITIVE_PRICE and i.severity is Severity.ERROR
        for i in issues
    )


def test_negative_volume_is_error_but_zero_is_allowed():
    negative = check_daily_values(_daily_frame([_bar(volume=-5)]))
    assert any(
        i.code == CODE_NEGATIVE_VOLUME and i.severity is Severity.ERROR
        for i in negative
    )
    zero = check_daily_values(_daily_frame([_bar(volume=0)]))
    assert not any(i.code == CODE_NEGATIVE_VOLUME for i in zero)


def test_negative_amount_is_error():
    issues = check_daily_values(_daily_frame([_bar(amount=-1.0)]))
    assert any(
        i.code == CODE_NEGATIVE_AMOUNT and i.severity is Severity.ERROR
        for i in issues
    )


def test_illegal_ohlc_relationship_is_error():
    issues = check_daily_values(_daily_frame([_bar(low=11.0, high=11.0)]))
    assert any(
        i.code == CODE_INVALID_OHLC and i.severity is Severity.ERROR
        for i in issues
    )


def test_clean_bar_produces_no_value_issue():
    assert check_daily_values(_daily_frame([_bar()])) == []


def test_duplicate_primary_key_conflict_is_reported():
    frame = _daily_frame([_bar(), _bar(close=10.6)])
    issues = check_primary_key_conflicts(frame)
    assert len(issues) == 1
    assert issues[0].code == CODE_DUPLICATE_CONFLICT
    assert issues[0].severity is Severity.ERROR
    assert issues[0].symbol == "600000.SH"
    assert issues[0].trade_date == date(2020, 1, 2)


def test_unique_primary_keys_produce_no_conflict():
    frame = _daily_frame([_bar(), _bar(trade_date=date(2020, 1, 3))])
    assert check_primary_key_conflicts(frame) == []


def test_schema_check_reports_missing_column():
    frame = _daily_frame([_bar()]).drop(columns=["amount"])
    issues = check_schema(frame, DAILY_SCHEMA, table="daily_bar")
    assert len(issues) == 1
    assert issues[0].code == CODE_SCHEMA_MISMATCH
    assert issues[0].severity is Severity.ERROR
    assert "amount" in issues[0].details["missing"]


def test_schema_check_passes_for_canonical_frame():
    assert check_schema(_daily_frame([_bar()]), DAILY_SCHEMA, table="daily_bar") == []


def test_provenance_accepts_documented_source_and_adjustment():
    assert check_provenance(_daily_frame([_bar()])) == []


def test_provenance_rejects_unknown_source():
    issues = check_provenance(_daily_frame([_bar(source="not-a-supplier")]))
    assert any(
        i.code == CODE_UNKNOWN_SOURCE and i.severity is Severity.ERROR
        for i in issues
    )


def test_provenance_rejects_unknown_adjustment_basis():
    issues = check_provenance(_daily_frame([_bar() | {"adjustment": "magic"}]))
    assert any(
        i.code == CODE_UNKNOWN_ADJUSTMENT and i.severity is Severity.ERROR
        for i in issues
    )


def test_quarantine_reason_required_for_each_isolated_record():
    rejected = pd.DataFrame({"reason": ["invalid_symbol", ""]})
    issues = check_quarantine_reasons(rejected)
    assert len(issues) == 1
    assert issues[0].code == CODE_QUARANTINE_MISSING_REASON
    assert issues[0].severity is Severity.ERROR


def test_quarantine_with_reasons_passes():
    assert check_quarantine_reasons(pd.DataFrame({"reason": ["invalid_symbol"]})) == []


def test_classify_missing_orders_not_listed_before_non_trading_day():
    assert (
        classify_missing_row(
            trade_date=date(2020, 1, 1),
            list_date=date(2020, 3, 1),
            is_trading_day=False,
            primary_present=False,
            validation_present=False,
        )
        == MISSING_NOT_LISTED
    )


def test_classify_missing_orders_delisted_before_non_trading_day():
    assert (
        classify_missing_row(
            trade_date=date(2021, 1, 2),
            delist_date=date(2020, 12, 31),
            is_trading_day=False,
            primary_present=False,
            validation_present=False,
        )
        == MISSING_DELISTED
    )


def test_classify_missing_orders_non_trading_day_before_suspension():
    assert (
        classify_missing_row(
            trade_date=date(2020, 1, 4),
            is_trading_day=False,
            primary_present=False,
            validation_present=False,
        )
        == MISSING_NON_TRADING_DAY
    )


def test_classify_all_sources_missing_as_unknown_or_suspended():
    assert (
        classify_missing_row(
            trade_date=date(2020, 1, 6),
            is_trading_day=True,
            primary_present=False,
            validation_present=False,
        )
        == MISSING_UNKNOWN_OR_SUSPENDED
    )


def test_classify_primary_only_missing_as_primary_source_missing():
    assert (
        classify_missing_row(
            trade_date=date(2020, 1, 6),
            is_trading_day=True,
            primary_present=False,
            validation_present=True,
        )
        == MISSING_PRIMARY_SOURCE
    )


def test_classify_unexplained_when_primary_present_but_validation_gap():
    assert (
        classify_missing_row(
            trade_date=date(2020, 1, 6),
            is_trading_day=True,
            primary_present=True,
            validation_present=False,
        )
        == MISSING_UNEXPLAINED
    )


def _report(*issues_list: QualityIssue) -> QualityReport:
    return QualityReport(issues=tuple(issues_list))


def test_gate_passes_empty_report():
    decision = evaluate_publication(_report())
    assert decision.passed is True
    assert decision.decision == "PASS"
    assert decision.reasons == ()


def test_gate_passes_backtest_readiness_issues_only():
    decision = evaluate_publication(
        _report(
            issue(Severity.ERROR, "momentum_lookback"),
            issue(Severity.FATAL, "execution_date_unavailable"),
        )
    )
    assert decision.passed is True


def test_gate_passes_cross_source_close_error():
    # A close-price ERROR blocks backtests, not publication.
    decision = evaluate_publication(
        _report(
            issue(Severity.ERROR, CODE_CLOSE_DIFFERENCE, details={"field": "close"})
        )
    )
    assert decision.passed is True


def test_gate_passes_info_and_warning_only():
    decision = evaluate_publication(
        _report(
            issue(Severity.INFO, CODE_WITHIN_TOLERANCE),
            issue(Severity.WARNING, CODE_PRICE_DIFFERENCE),
        )
    )
    assert decision.passed is True


@pytest.mark.parametrize("code", sorted(PUBLICATION_BLOCKING_CODES))
def test_gate_blocks_each_publication_blocking_code(code):
    decision = evaluate_publication(_report(issue(Severity.ERROR, code)))
    assert decision.passed is False
    assert decision.decision == "BLOCK"
    assert decision.reasons


def test_gate_block_reason_mentions_the_code():
    blocked = _report(issue(Severity.ERROR, CODE_DUPLICATE_CONFLICT))
    decision = evaluate_publication(blocked)
    assert CODE_DUPLICATE_CONFLICT in decision.reasons[0]


def test_report_tallies_severities_and_codes():
    report = _report(
        issue(Severity.ERROR, CODE_DUPLICATE_CONFLICT),
        issue(Severity.INFO, CODE_WITHIN_TOLERANCE),
        issue(Severity.ERROR, CODE_DUPLICATE_CONFLICT),
    )
    # The tally reports the full severity axis, including zero-count levels.
    assert report.by_severity() == {
        "INFO": 1,
        "WARNING": 0,
        "ERROR": 2,
        "FATAL": 0,
    }
    assert report.by_code() == {CODE_DUPLICATE_CONFLICT: 2, CODE_WITHIN_TOLERANCE: 1}


def test_report_json_round_trip_is_deterministic():
    report = _report(
        issue(Severity.ERROR, CODE_DUPLICATE_CONFLICT, symbol="600000.SH", count=2),
        issue(Severity.INFO, CODE_WITHIN_TOLERANCE),
    )
    first = report.to_dict()
    second = _report(*report.issues).to_dict()
    assert first == second
    # Issues serialize severity-major, so the INFO issue sorts before ERROR.
    assert [i["code"] for i in first["issues"]] == [
        CODE_WITHIN_TOLERANCE,
        CODE_DUPLICATE_CONFLICT,
    ]


# --------------------------------------------------------------------------- #
# The update path's missing-day accounting (primary coverage membership)
# --------------------------------------------------------------------------- #


def _primary_fetch_pipeline(monkeypatch) -> SimpleNamespace:
    """A ``DataPipeline`` skin whose tushare dispatch returns two fixed bars."""
    pipeline = DataPipeline.__new__(DataPipeline)
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "trade_date": ["20211101", "20211102"],
            "open": [55.0, 55.0],
            "high": [55.0, 55.0],
            "low": [55.0, 55.0],
            "close": [55.0, 55.0],
            "vol": [1000.0, 1000.0],
            "amount": [55000.0, 55000.0],
        }
    )
    fetch_result = SimpleNamespace(
        frame=frame, metadata={"response_timestamp": "2021-11-03T00:00:00Z"}
    )
    monkeypatch.setattr(pipeline, "_dispatch", lambda *args, **kwargs: fetch_result)
    monkeypatch.setattr(
        pipeline, "_adapter_or_fail", lambda name, statuses: object()
    )
    monkeypatch.setattr(pipeline, "_record_raw", lambda result: result)
    return pipeline


def test_primary_stock_fetch_records_symbol_scoped_coverage_pairs(monkeypatch):
    """Regression: primary coverage must be ``(symbol, date)`` pairs.

    The set used to hold bare dates, so the ``(symbol, date)`` membership test
    in ``_missing_issues`` never matched and every listed symbol was warned on
    every open day -- 56,660 spurious WARNINGs on the 2015-2026 update.
    """
    pipeline = _primary_fetch_pipeline(monkeypatch)
    issues: list = []
    raw_snapshots: list = []
    primary_rows: list = []
    primary_dates: set = set()

    fatal = pipeline._fetch_primary_stock(
        ("tushare",),
        ["000001.SZ"],
        date(2021, 11, 1),
        date(2021, 11, 2),
        issues,
        {},
        raw_snapshots,
        primary_rows,
        primary_dates,
    )

    assert fatal is False
    assert len(primary_rows) == 1
    assert primary_dates == {
        ("000001.SZ", date(2021, 11, 1)),
        ("000001.SZ", date(2021, 11, 2)),
    }


def test_missing_issues_flags_only_days_absent_from_primary_pairs(monkeypatch):
    """A present ``(symbol, day)`` pair must never be warned as missing."""
    pipeline = _primary_fetch_pipeline(monkeypatch)
    master = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "list_date": pd.Timestamp("2001-01-02"),
                "delist_date": pd.NaT,
            }
        ]
    )
    primary_dates = {
        ("000001.SZ", date(2021, 11, 1)),
        ("000001.SZ", date(2021, 11, 2)),
    }
    issues = DataPipeline._missing_issues(
        pipeline,
        ["000001.SZ"],
        master,
        date(2021, 11, 1),
        date(2021, 11, 3),
        {date(2021, 11, 1), date(2021, 11, 2), date(2021, 11, 3)},
        primary_dates,
        set(),
        None,
    )

    assert [(i.symbol, i.trade_date, i.code) for i in issues] == [
        ("000001.SZ", date(2021, 11, 3), MISSING_UNKNOWN_OR_SUSPENDED)
    ]
