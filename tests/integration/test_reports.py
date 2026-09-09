"""Self-contained static HTML experiment and quality reports (Task 12).

Both renderers must write files that open with no server and no CDN: the tests
scan the generated HTML for remote ``src`` / ``href`` request tags and for a
canary credential / supplier-error string that must never surface.  The four
Chinese engineering-disclosure strings required by the report contract are
asserted literally, and every content section of the two reports is exercised
by a synthetic input rendered under a ``tmp_path`` (never committed).
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from stock_quant.analytics.performance import compute_metrics
from stock_quant.data_quality.models import (
    CODE_DUPLICATE_CONFLICT,
    CODE_INVALID_OHLC,
    CODE_UNIT_MISMATCH,
    CODE_WITHIN_TOLERANCE,
    MISSING_UNKNOWN_OR_SUSPENDED,
    QualityIssue,
    QualityReport,
    Severity,
)
from stock_quant.reporting.html import (
    AdjustmentSample,
    BuildCounts,
    ExperimentReportInput,
    ExperimentScenario,
    FieldDifference,
    QualityReportInput,
    SourceStatus,
    render_experiment_report,
    render_quality_report,
)

_DATES = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
]

_EQUITY_COLUMNS = (
    "trade_date",
    "cash",
    "market_value",
    "total_equity",
    "stale_market_value",
    "stale_days",
)
_FILL_COLUMNS = (
    "trade_date",
    "fill_id",
    "order_id",
    "side",
    "symbol",
    "quantity",
    "price",
    "commission",
    "stamp_tax",
)
_REJECTION_COLUMNS = (
    "trade_date",
    "order_id",
    "side",
    "symbol",
    "requested_quantity",
    "filled_quantity",
    "rejected_quantity",
    "reason",
)
_ACTION_COLUMNS = (
    "seq",
    "action_id",
    "symbol",
    "ex_date",
    "record_date",
    "cash_credited",
    "shares_added",
    "note",
)

_SCENARIO_TOTALS = {
    "zero_cost": [100000.0, 101000.0, 103000.0, 102000.0, 104000.0],
    "commission_tax": [100000.0, 100700.0, 102700.0, 101500.0, 103300.0],
    "full_cost": [100000.0, 100500.0, 102500.0, 101200.0, 102800.0],
}

_SCENARIO_COMMISSION = {
    "zero_cost": 0.0,
    "commission_tax": 6.0,
    "full_cost": 12.0,
}


# --------------------------------------------------------------------------- #
# Synthetic fixtures
# --------------------------------------------------------------------------- #


def _equity(
    totals: list[float], stale_last: float = 0.0, stale_days: int = 0
) -> pd.DataFrame:
    cash = [total * 0.1 for total in totals]
    stale_mv = [0.0] * (len(totals) - 1) + [stale_last]
    return pd.DataFrame(
        {
            "trade_date": _DATES,
            "cash": cash,
            "market_value": [total - c for total, c in zip(totals, cash)],
            "total_equity": totals,
            "stale_market_value": stale_mv,
            "stale_days": [0] * (len(totals) - 1) + [stale_days],
        }
    )


def _benchmark() -> pd.DataFrame:
    rows: list[dict] = []
    for index, day in enumerate(_DATES):
        rows.append(
            {"symbol": "000300.SH", "trade_date": day, "close": 3000.0 + 60.0 * index}
        )
        rows.append(
            {"symbol": "000905.SH", "trade_date": day, "close": 5000.0 + 50.0 * index}
        )
    return pd.DataFrame(rows)


def _fills(commission: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_date": _DATES[0],
                "fill_id": "F000001",
                "order_id": "O000001",
                "side": "BUY",
                "symbol": "600001.SH",
                "quantity": 1000,
                "price": 10.0,
                "commission": commission,
                "stamp_tax": 0.0,
            }
        ]
    )


def _rejections(with_row: bool) -> pd.DataFrame:
    if not with_row:
        return pd.DataFrame(columns=list(_REJECTION_COLUMNS))
    return pd.DataFrame(
        [
            {
                "trade_date": _DATES[2],
                "order_id": "O000002",
                "side": "BUY",
                "symbol": "600002.SH",
                "requested_quantity": 300,
                "filled_quantity": 0,
                "rejected_quantity": 300,
                "reason": "buy_at_upper_limit",
            }
        ]
    )


def _action_ledger() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "seq": 1,
                "action_id": "600003.SH-2024-01-02",
                "symbol": "600003.SH",
                "ex_date": date(2024, 1, 3),
                "record_date": date(2024, 1, 2),
                "cash_credited": 120.0,
                "shares_added": 0,
                "note": "cash dividend",
            },
            {
                "seq": 2,
                "action_id": "600003.SH-2024-01-05",
                "symbol": "600003.SH",
                "ex_date": date(2024, 1, 8),
                "record_date": date(2024, 1, 5),
                "cash_credited": 0.0,
                "shares_added": 300,
                "note": "capitalization",
            },
        ]
    )


def _holdings() -> pd.DataFrame:
    rows = [
        {
            "symbol": "600001.SH",
            "quantity": 2000,
            "market_value": 41200.0,
            "weight": 0.4,
        },
        {
            "symbol": "600002.SH",
            "quantity": 1500,
            "market_value": 30900.0,
            "weight": 0.3,
        },
        {
            "symbol": "600003.SH",
            "quantity": 1000,
            "market_value": 30900.0,
            "weight": 0.3,
        },
    ]
    return pd.DataFrame(rows)


def _experiment_input(
    known_limitations: tuple[str, ...] | None = None,
    corporate_action_trust: dict | None = None,
    factor_input_audit: dict | None = None,
    data_acceptance: dict | None = None,
) -> ExperimentReportInput:
    benchmark = _benchmark()
    scenarios: list[ExperimentScenario] = []
    for name in ("zero_cost", "commission_tax", "full_cost"):
        totals = _SCENARIO_TOTALS[name]
        stale_last = 4000.0 if name == "full_cost" else 0.0
        equity = _equity(totals, stale_last=stale_last, stale_days=2)
        metrics = compute_metrics(equity, _fills(_SCENARIO_COMMISSION[name]), benchmark)
        scenarios.append(
            ExperimentScenario(
                name=name,
                equity=equity,
                fills=_fills(_SCENARIO_COMMISSION[name]),
                rejections=_rejections(with_row=(name == "full_cost")),
                action_ledger=_action_ledger(),
                holdings=_holdings(),
                metrics=metrics,
            )
        )
    if known_limitations is None:
        known_limitations = (
            "T+1 约束：当日买入次日方可卖出；卖出受限时记录未成交订单。",
            "停牌沿用最近有效收盘价仅用于估值，延续价格不得用于成交。",
            "不模拟盘口排队、概率性部分成交与非线性市场冲击。",
        )
    return ExperimentReportInput(
        experiment_id="exp-abc123",
        run_id="run-xyz789",
        dataset_version="dataset-v1",
        universe_version="universe-v1",
        code_commit="a1b2c3d4",
        hypothesis="60 日动量周频调仓的工程链路验证",
        initial_cash=100000.0,
        scenarios=tuple(scenarios),
        benchmark_closes=benchmark,
        benchmark_symbols=("000300.SH", "000905.SH"),
        generated_at="2024-01-09T09:00:00",
        known_limitations=known_limitations,
        corporate_action_trust=corporate_action_trust,
        factor_input_audit=factor_input_audit,
        data_acceptance=data_acceptance,
    )


def _quality_input() -> QualityReportInput:
    issues = (
        QualityIssue(
            severity=Severity.ERROR,
            code=CODE_DUPLICATE_CONFLICT,
            table="daily_bar",
            symbol="600001.SH",
            trade_date=_DATES[0],
            details={
                "sources": ["tushare", "akshare"],
                "supplier_message": "SECRET_CANARY_QUALITY duplicate",
            },
        ),
        QualityIssue(
            severity=Severity.ERROR,
            code=CODE_INVALID_OHLC,
            table="daily_bar",
            symbol="600002.SH",
            trade_date=_DATES[1],
            details={
                "field": "close",
                "left": 9.9,
                "right": 11.0,
                "absolute_difference": 1.1,
                "relative_difference": 0.111,
                "supplier_message": "SECRET_CANARY_QUALITY ohlc",
            },
        ),
        QualityIssue(
            severity=Severity.WARNING,
            code=CODE_UNIT_MISMATCH,
            table="daily_bar",
            symbol="600003.SH",
            trade_date=_DATES[2],
        ),
        QualityIssue(
            severity=Severity.WARNING,
            code=MISSING_UNKNOWN_OR_SUSPENDED,
            table="daily_bar",
            symbol="600004.SH",
            trade_date=_DATES[3],
        ),
        QualityIssue(
            severity=Severity.INFO,
            code=CODE_WITHIN_TOLERANCE,
            table="daily_bar",
            symbol="600005.SH",
            trade_date=_DATES[4],
            details={
                "field": "open",
                "absolute_difference": 0.001,
                "relative_difference": 0.0001,
            },
        ),
    )
    return QualityReportInput(
        report=QualityReport(issues=issues),
        dataset_version="dataset-v1",
        gate_passed=False,
        gate_reasons=(
            "invalid_ohlc in daily_bar symbol=600002.SH",
            "duplicate_conflict in daily_bar symbol=600001.SH",
        ),
        sources=(
            SourceStatus(source="tushare", status="OK", version="v1"),
            SourceStatus(source="akshare", status="WARNING", version="v2"),
            SourceStatus(source="baostock", status="OK", version="v1"),
        ),
        counts=BuildCounts(raw_rows=12340, standard_rows=11800, quarantine_rows=120),
        cross_sources=(
            FieldDifference(
                field="close",
                n_compared=100,
                max_absolute=0.021,
                max_relative=0.015,
                relative_samples=(0.001, 0.002, 0.0005, 0.003, 0.0008),
            ),
            FieldDifference(
                field="open",
                n_compared=98,
                max_absolute=0.012,
                max_relative=0.008,
                relative_samples=(0.0009, 0.0011),
            ),
        ),
        adjustment_samples=(
            AdjustmentSample(
                symbol="600010.SH",
                trade_date=date(2024, 1, 2),
                left=1.5,
                right=1.49,
                relative_difference=0.0067,
            ),
        ),
        missing_counts=(("unknown_or_suspended", 3),),
    )


# --------------------------------------------------------------------------- #
# Static HTML self-containment helper
# --------------------------------------------------------------------------- #

_EXTERNAL_TAG = re.compile(
    r"<(?:script|link|img|iframe|embed|object|source|video|audio)\b[^>]*"
    r"\b(?:src|href)\s*=\s*(?:https?:)?//",
    re.IGNORECASE,
)


def _assert_self_contained(html: str, secrets: tuple[str, ...] = ()) -> None:
    assert "Plotly.newPlot" in html  # charts are embedded, not fetched
    assert not _EXTERNAL_TAG.search(html), "found a remote src/href request tag"
    for secret in secrets:
        assert secret not in html


# --------------------------------------------------------------------------- #
# Brief Step-1 verbatim tests
# --------------------------------------------------------------------------- #


def report_input() -> ExperimentReportInput:
    return _experiment_input()


def test_experiment_html_discloses_engineering_only_limit(tmp_path):
    path = render_experiment_report(report_input(), tmp_path / "report.html")
    html = path.read_text(encoding="utf-8")
    assert "工程验证" in html
    assert "不代表策略具备实盘价值" in html
    assert "未成交订单" in html and "停牌资产比例" in html


# --------------------------------------------------------------------------- #
# Experiment report: full section coverage
# --------------------------------------------------------------------------- #


def test_experiment_html_covers_required_sections(tmp_path):
    path = render_experiment_report(_experiment_input(), tmp_path / "report.html")
    html = path.read_text(encoding="utf-8")
    for scenario in ("zero_cost", "commission_tax", "full_cost"):
        assert scenario in html
    assert "000300.SH" in html and "000905.SH" in html
    assert "最大回撤" in html
    assert "换手率" in html
    assert "成本拆分" in html
    assert "持仓" in html
    assert "现金" in html
    assert "未成交订单" in html
    assert "停牌资产比例" in html
    assert "公司行为流水" in html
    assert "600003.SH" in html  # corporate-action ledger row
    assert "已知限制" in html
    assert "dataset-v1" in html
    assert "a1b2c3d4" in html  # code version
    assert "exp-abc123" in html  # experiment version


def test_experiment_html_renders_execution_divergence_summary(tmp_path):
    report = _experiment_input()
    scenario = report.scenarios[0]
    report = ExperimentReportInput(
        **{
            **report.__dict__,
            "scenarios": (
                ExperimentScenario(
                    **{
                        **scenario.__dict__,
                        "execution_summary": {
                            "planned_order_count": 20,
                            "filled_order_count": 15,
                            "partial_order_count": 2,
                            "rejected_order_count": 3,
                            "planned_gross_notional": 100000.0,
                            "actual_gross_notional": 85000.0,
                            "unfilled_notional": 15000.0,
                            "execution_deviation_ratio": 0.15,
                            "unfilled_reason_counts": {
                                "insufficient_cash": 2,
                                "suspended_or_unknown": 3,
                            },
                            "end_cash": 12000.0,
                            "cash_ratio": 0.3,
                            "stale_asset_ratio": 0.02,
                        },
                    }
                ),
            ),
        }
    )

    html = render_experiment_report(report, tmp_path / "report.html").read_text(
        encoding="utf-8"
    )

    assert "执行偏离诊断" in html
    assert "未成交原因" in html
    assert "部分成交数" in html
    assert "拒单数" in html
    assert "15.00%" in html
    assert "insufficient_cash: 2" in html
    assert "suspended_or_unknown: 3" in html
    assert "调仓前约束" not in html


def test_experiment_html_has_collapsed_daily_trade_snapshot(tmp_path):
    report_path = tmp_path / "report.html"
    html = render_experiment_report(_experiment_input(), report_path).read_text(
        encoding="utf-8"
    )
    assert '<details class="daily-trade-snapshot">' in html
    assert "每日持仓快照" in html
    assert "600001.SH" in html
    assert "买入" in html


def test_experiment_html_collapses_empty_ledgers_to_hint(tmp_path):
    report = _experiment_input()
    empty = report.scenarios[0]
    report = ExperimentReportInput(
        **{
            **report.__dict__,
            "scenarios": (
                ExperimentScenario(
                    **{
                        **empty.__dict__,
                        "holdings": pd.DataFrame(),
                        "rejections": pd.DataFrame(),
                        "action_ledger": pd.DataFrame(),
                    }
                ),
            ),
        }
    )
    html = render_experiment_report(report, tmp_path / "report.html").read_text(
        encoding="utf-8"
    )
    assert "📭 回测期末无持仓" in html
    assert "📭 回测期内无未成交订单" in html
    assert "📭 回测期内无公司行为记录" in html


def test_experiment_html_is_self_contained(tmp_path):
    path = render_experiment_report(_experiment_input(), tmp_path / "report.html")
    html = path.read_text(encoding="utf-8")
    _assert_self_contained(html)


def test_experiment_html_always_surfaces_phase_one_known_limitations(tmp_path):
    # The CLI report build path supplies no run-specific known limitations; the
    # remaining phase-one boundary must still render in the 已知限制 section.
    # The obsolete "no adjusted series is consumed" limitation is gone: the
    # 因子价格口径 section now states the consumed basis explicitly.
    path = render_experiment_report(
        _experiment_input(known_limitations=()), tmp_path / "report.html"
    )
    html = path.read_text(encoding="utf-8")
    assert "跨源收盘价差异超过容差" in html
    assert "Tushare 主源收盘序列为准" in html
    assert "不发布、也不消费复权日线" not in html
    assert "adjusted_close=close" not in html
    # Run-specific limitations still follow the phase-one boundaries when given.
    with_extra = _experiment_input(
        known_limitations=("自定义实验限制：示例。",)
    )
    combined = render_experiment_report(
        with_extra, tmp_path / "report2.html"
    ).read_text(encoding="utf-8")
    phase_one_pos = combined.index("跨源收盘价差异超过容差")
    extra_pos = combined.index("自定义实验限制：示例。")
    assert phase_one_pos < extra_pos


def test_experiment_html_shows_factor_price_basis(tmp_path):
    audit = {
        "adjustment": "internal_total_return_v1",
        "factor_versions": {"momentum_60d": "2.0.0"},
        "row_count": 100,
        "error_break_count": 2,
        "invalid_reason_counts": {"cross_source_conflict": 2},
    }
    path = render_experiment_report(
        _experiment_input(factor_input_audit=audit), tmp_path / "report.html"
    )
    html = path.read_text(encoding="utf-8")
    assert "因子价格口径" in html
    assert "internal_total_return_v1" in html
    assert "momentum_60d: 2.0.0" in html
    assert "cross_source_conflict" in html
    assert "100" in html  # 输入行数
    assert "不可信断点" in html


def test_experiment_html_omits_factor_price_basis_without_audit(tmp_path):
    # Reports rebuilt before the audit existed render no empty claims.
    html = render_experiment_report(
        _experiment_input(), tmp_path / "report.html"
    ).read_text(encoding="utf-8")
    assert "因子价格口径" not in html


def test_experiment_html_escapes_factor_audit_reasons(tmp_path):
    audit = {
        "adjustment": "internal_total_return_v1",
        "factor_versions": {"momentum_60d": "2.0.0"},
        "row_count": 1,
        "error_break_count": 1,
        "invalid_reason_counts": {"<script>alert(1)</script>": 1},
    }
    html = render_experiment_report(
        _experiment_input(factor_input_audit=audit), tmp_path / "report.html"
    ).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --------------------------------------------------------------------------- #
# Real-data acceptance audit section (plan Task 7)
# --------------------------------------------------------------------------- #


def test_experiment_report_shows_data_acceptance(tmp_path):
    report = _experiment_input(
        data_acceptance={
            "acceptance_id": "a" * 64,
            "policy_version": "real-data-v1",
            "operator_id": "operator-a",
            "created_at": "2026-09-08T12:00:00+00:00",
            "decision": "ACCEPTED",
        }
    )
    path = render_experiment_report(report, tmp_path / "report.html")
    html = path.read_text(encoding="utf-8")
    assert "真实数据验收" in html
    assert "real-data-v1" in html
    assert "operator-a" in html
    assert "a" * 64 in html
    assert "ACCEPTED" in html
    assert "2026-09-08T12:00:00+00:00" in html
    # The audit section leads the performance presentation, never follows it.
    assert html.index("真实数据验收") < html.index("绩效汇总")
    assert html.index("真实数据验收") < html.index("净值与回撤")


def test_experiment_html_flags_unverified_without_acceptance(tmp_path):
    # No acceptance audit (or the engineering {"acceptance_id": None,
    # "status": "UNVERIFIED"} shape) must render the prominent UNVERIFIED
    # alert and never an inferred ACCEPTED claim.
    for missing in (None, {"acceptance_id": None, "status": "UNVERIFIED"}):
        html = render_experiment_report(
            _experiment_input(data_acceptance=missing), tmp_path / "r.html"
        ).read_text(encoding="utf-8")
        assert "真实数据验收" in html
        assert "UNVERIFIED" in html
        assert "ACCEPTED" not in html
        assert "real-data-v1" not in html


def test_experiment_html_escapes_data_acceptance_fields(tmp_path):
    audit = {
        "acceptance_id": "b" * 64,
        "policy_version": "real-data-v1",
        "operator_id": "<script>alert(1)</script>",
        "created_at": "2026-09-08T12:00:00+00:00",
        "decision": "ACCEPTED",
    }
    html = render_experiment_report(
        _experiment_input(data_acceptance=audit), tmp_path / "report.html"
    ).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --------------------------------------------------------------------------- #
# Quality report: full section coverage and supplier-text redaction
# --------------------------------------------------------------------------- #


def test_quality_html_covers_required_content(tmp_path):
    path = render_quality_report(_quality_input(), tmp_path / "quality.html")
    html = path.read_text(encoding="utf-8")
    assert "dataset-v1" in html
    assert "BLOCK" in html  # gate decision text
    assert "tushare" in html and "akshare" in html  # source/version status
    assert "原始行数" in html
    assert "12340" in html and "11800" in html and "120" in html
    assert "重复记录" in html
    assert "缺失/停牌" in html
    assert "OHLC 非法" in html
    assert "单位不一致" in html
    assert "跨源差异" in html
    assert "0.021000" in html  # max cross-source absolute difference
    assert "复权差异样本" in html
    assert "600010.SH" in html and "2024-01-02" in html  # adjustment sample
    assert "duplicate_conflict" in html
    assert "600001.SH" in html  # issue row symbol


def test_quality_html_is_self_contained_and_redacts_supplier_text(tmp_path):
    path = render_quality_report(_quality_input(), tmp_path / "quality.html")
    html = path.read_text(encoding="utf-8")
    _assert_self_contained(html, secrets=("SECRET_CANARY_QUALITY",))


def test_quality_gate_pass_renders_pass_banner(tmp_path):
    passed = QualityReportInput(
        report=QualityReport(),
        dataset_version="dataset-v1",
        gate_passed=True,
    )
    path = render_quality_report(passed, tmp_path / "pass.html")
    html = path.read_text(encoding="utf-8")
    assert "PASS" in html
    assert "可通过中性发布门禁" in html


# --------------------------------------------------------------------------- #
# cwd independence
# --------------------------------------------------------------------------- #


def test_templates_resolve_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    exp_path = render_experiment_report(
        _experiment_input(), tmp_path / "exp.html"
    )
    qual_path = render_quality_report(
        _quality_input(), tmp_path / "qual.html"
    )
    assert "工程验证" in exp_path.read_text(encoding="utf-8")
    assert "门禁决定" in qual_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Walk-forward audit section
# --------------------------------------------------------------------------- #


def _walk_forward_payload() -> dict:
    return {
        "research_status": "COMPLETED",
        "stability_conclusion": "UNSTABLE",
        "stability_policy_hash": "ab" * 32,
        "stability_policy_version": "stability-v1",
        "thresholds": {
            "policy_version": "stability-v1",
            "minimum_executed_folds": 5,
            "minimum_positive_fold_ratio": 0.60,
            "worst_fold_calendar_return_floor": -0.10,
            "annualization_sessions": 252,
            "risk_free_rate": 0.0,
        },
        "integrity_failures": [],
        "skipped_fold_ids": [],
        "reasons": ["unstable scenarios: full_cost"],
        "schedule": {
            "requested_start": "2020-01-01",
            "requested_end": "2025-12-31",
            "fold_count": 5,
            "boundary_count": 1,
            "boundaries": [
                {
                    "calendar_start": "2026-01-01",
                    "calendar_end": "2026-09-02",
                    "reason": "trailing_partial_year",
                }
            ],
            "fold_schedule_sha256": "cd" * 32,
            "fold_outcomes_sha256": "de" * 32,
        },
        "fold_statuses": [
            {"fold_id": "fold-0001", "status": "executed", "reason_code": None},
            {"fold_id": "fold-0002", "status": "failed_preflight",
             "reason_code": "DATA_GAP"},
        ],
        "scenario_results": [
            {
                "scenario": "full_cost",
                "executed_fold_count": 5,
                "positive_fold_count": 2,
                "positive_fold_ratio": 0.4,
                "worst_fold_calendar_return": -0.5,
                "minimum_positive_fold_ratio": 0.6,
                "worst_fold_calendar_return_floor": -0.1,
                "policy_version": "stability-v1",
                "passed": False,
                "reasons": ["positive fold ratio 0.4 < 0.6"],
            }
        ],
        "scenario_aggregates": [
            {
                "scenario": "full_cost",
                "aggregate_return": 0.21,
                "annualized_return": 0.039,
                "annualized_volatility": 0.18,
                "sharpe_zero_rf": 0.216,
                "oos_return_observations": 1220,
                "annualization_observations": 1220,
            }
        ],
        "fold_metrics": [
            {
                "fold_id": "fold-0001",
                "scenario": "full_cost",
                "fold_calendar_return": 0.05,
                "per_fold_max_drawdown": -0.03,
                "observation_count": 244,
                "reject_rate": 0.1,
                "turnover": 0.42,
                "explicit_cost_ratio": 0.001,
            },
            {
                "fold_id": "fold-0002",
                "scenario": "full_cost",
                "fold_calendar_return": -0.5,
                "per_fold_max_drawdown": -0.55,
                "observation_count": 244,
                "reject_rate": 0.2,
                "turnover": 0.51,
                "explicit_cost_ratio": 0.002,
            },
        ],
        "experiment_id": "exp-abc123",
        "dataset_version": "dataset-v1",
        "universe_version": "universe-v1",
    }


def test_experiment_html_renders_the_walk_forward_audit_section(tmp_path):
    payload = _walk_forward_payload()
    run_input = _experiment_input()
    report_input = ExperimentReportInput(
        experiment_id=run_input.experiment_id,
        dataset_version=run_input.dataset_version,
        universe_version=run_input.universe_version,
        code_commit=run_input.code_commit,
        scenarios=(),
        benchmark_closes=run_input.benchmark_closes,
        benchmark_symbols=run_input.benchmark_symbols,
        run_id=run_input.run_id,
        hypothesis=run_input.hypothesis,
        initial_cash=run_input.initial_cash,
        walk_forward=payload,
    )
    destination = tmp_path / "walk_forward.html"
    render_experiment_report(report_input, destination)
    html = destination.read_text(encoding="utf-8")
    # every fold id, every scenario, the policy hash and observation counts
    assert "fold-0001" in html
    assert "fold-0002" in html
    assert "full_cost" in html
    assert ("ab" * 32) in html
    assert "stability_policy_hash" in html
    assert "oos_return_observations" in html
    # per-fold drawdowns are rendered; no global drawdown field exists
    assert "per_fold_max_drawdown" in html
    assert "-55.00%" in html
    assert "aggregate_max_drawdown" not in html
    assert "全局最大回撤" not in html
    # no Calmar metric field exists anywhere; the policy disclaimer names it
    assert "calmar_ratio" not in html
    assert "Calmar" in html and "被政策禁止" in html
    # boundary exclusions are recorded, never silently dropped
    assert "trailing_partial_year" in html
    assert "not_evaluated_boundary" in html or "边界未评估" in html
    # failed folds remain listed
    assert "failed_preflight" in html
    # the conclusion and its null-on-failure rule are visible
    assert "UNSTABLE" in html


def test_experiment_html_omits_walk_forward_section_without_payload(tmp_path):
    destination = tmp_path / "plain.html"
    render_experiment_report(_experiment_input(), destination)
    html = destination.read_text(encoding="utf-8")
    assert "stability_policy_hash" not in html
    assert "Walk-Forward" not in html


# --------------------------------------------------------------------------- #
# Buffered construction audit section (buffered plan Task 5)
# ---------------------------------------------------------------------------


def _buffered_walk_forward_payload() -> dict:
    payload = _walk_forward_payload()
    payload["buffered"] = {
        "portfolio_rule_version": "ef" * 32,
        "folds": [
            {
                "fold_id": "fold-0001",
                "signal_days": 50,
                "retained": 210,
                "entered": 42,
                "exited": 40,
                "risk_invalid": 7,
                "achieved_gross_exposure": 0.97,
                "cash_residue": 0.03,
            }
        ],
        "scenarios": [
            {
                "scenario": "full_cost",
                "band_suppressed_rows": 30,
                "band_suppressed_amount": 125000.5,
                "lot_suppressed_rows": 12,
                "lot_suppressed_amount": 9000.25,
            }
        ],
    }
    return payload


def test_buffered_report_explains_turnover_sources(tmp_path):
    run_input = _experiment_input()
    report_input = ExperimentReportInput(
        experiment_id=run_input.experiment_id,
        dataset_version=run_input.dataset_version,
        universe_version=run_input.universe_version,
        code_commit=run_input.code_commit,
        scenarios=(),
        benchmark_closes=run_input.benchmark_closes,
        benchmark_symbols=run_input.benchmark_symbols,
        run_id=run_input.run_id,
        hypothesis=run_input.hypothesis,
        initial_cash=run_input.initial_cash,
        walk_forward=_buffered_walk_forward_payload(),
    )
    rendered_html = render_experiment_report(
        report_input, tmp_path / "buffered.html"
    ).read_text(encoding="utf-8")
    assert "成员变化换手" in rendered_html
    assert "连续持仓再平衡换手" in rendered_html
    assert "带宽抑制金额" in rendered_html
    assert "手数抑制金额" in rendered_html
    # the canonical rule version renders, and suppression is never presented
    # as an execution rejection
    assert "ef" * 32 in rendered_html
    assert "不是执行拒单" in rendered_html


def test_plain_report_has_no_buffered_section(tmp_path):
    run_input = _experiment_input()
    report_input = ExperimentReportInput(
        experiment_id=run_input.experiment_id,
        dataset_version=run_input.dataset_version,
        universe_version=run_input.universe_version,
        code_commit=run_input.code_commit,
        scenarios=(),
        benchmark_closes=run_input.benchmark_closes,
        benchmark_symbols=run_input.benchmark_symbols,
        walk_forward=_walk_forward_payload(),
    )
    rendered_html = render_experiment_report(
        report_input, tmp_path / "plain.html"
    ).read_text(encoding="utf-8")
    assert "成员变化换手" not in rendered_html
    assert "带宽抑制金额" not in rendered_html
