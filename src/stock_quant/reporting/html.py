"""Static, self-contained HTML performance and data-quality reports (Task 12).

Two pure renderers turn explicit presentation inputs into single-file HTML that
opens with no server and no CDN: Plotly figures are embedded inline (the whole
``plotly.js`` bundle once per page, each figure as a ``Plotly.newPlot``
fragment), and every dynamic string is HTML-escaped by the Jinja layer while
only the trusted Plotly fragments are marked ``|safe``.  Generated files carry
no external data requests, no credentials and no raw supplier/error text.

Interface reconciliation with the brief's ``render_quality_report(report,
destination)``: the quality content a report must show (source/version status,
raw/standard/quarantine counts, cross-source difference distributions and
maxima, adjustment samples, current dataset version and the publication-gate
decision) cannot be reconstructed from a bare :class:`QualityReport` issue
list.  So the first argument is a richer frozen
:class:`QualityReportInput` that *composes* a :class:`QualityReport` (the
``report`` field) plus provenance/gate/cross-source fields -- same callable
name, same two-argument ``(report, destination) -> Path`` shape.  The input
types (``ExperimentReportInput`` / ``QualityReportInput``) are owned here so a
later CLI adapter can populate them additively from the published artifacts
without changing either renderer's contract.

The heavy presentation work (date normalization, numeric formatting, grouping
issues, chart building) lives in Python; the ``.j2`` templates only lay out the
pre-built page model so they stay readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader

from stock_quant.analytics.performance import PerformanceMetrics
from stock_quant.data_quality.models import (
    CODE_ADJUSTMENT_BASIS_MISMATCH,
    CODE_CLOSE_DIFFERENCE,
    CODE_DUPLICATE_CONFLICT,
    CODE_INVALID_OHLC,
    CODE_NEGATIVE_AMOUNT,
    CODE_NEGATIVE_VOLUME,
    CODE_NONPOSITIVE_PRICE,
    CODE_PRICE_DIFFERENCE,
    CODE_UNIT_MISMATCH,
    CODE_WITHIN_TOLERANCE,
    MISSING_DELISTED,
    MISSING_NON_TRADING_DAY,
    MISSING_NOT_LISTED,
    MISSING_PRIMARY_SOURCE,
    MISSING_UNEXPLAINED,
    MISSING_UNKNOWN_OR_SUSPENDED,
    QualityReport,
    Severity,
)

_PRIMARY_BENCHMARK = "000300.SH"

# Plotly never emits an external request from a fragment with the logo and
# send-to-cloud links disabled.
_PLOTLY_CONFIG = {"displaylogo": False}

# Phase-one universal limitations, surfaced in every experiment report's
# 已知限制 section (and the README).  It is a report-only boundary, not a
# defect: cross-source stock-close disagreement above tolerance is recorded as
# ERROR in the quality report but the Tushare primary close series is
# authoritative for factors and backtests (design §13.5 keeps the publication
# gate strategy-independent).  The former "no adjusted series is published or
# consumed" limitation is obsolete since momentum_60d v2 consumes the project's
# internal_total_return_v1 ``adjusted_bar`` series; the 因子价格口径 section
# now reports that basis, factor versions and break counts explicitly.
_PHASE_ONE_KNOWN_LIMITATIONS = (
    "跨源收盘价差异超过容差时，仅在质量报告中记录为 ERROR，本阶段不阻断发布："
    "因子与回测以 Tushare 主源收盘序列为准（设计 §13.5 令发布门禁与策略输入无关），"
    "跨源收盘差异仅作报告提示。",
)

# --------------------------------------------------------------------------- #
# Input types (frozen, presentation-level)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExperimentScenario:
    """One cost scenario's ledgers, end holdings and computed metrics."""

    name: str
    equity: pd.DataFrame
    fills: pd.DataFrame
    rejections: pd.DataFrame
    action_ledger: pd.DataFrame
    holdings: pd.DataFrame
    metrics: PerformanceMetrics
    execution_summary: dict[str, object] | None = None


@dataclass(frozen=True)
class ExperimentReportInput:
    """Everything ``experiment.html`` needs, kept close to the run artifacts."""

    experiment_id: str
    dataset_version: str
    universe_version: str
    code_commit: str
    scenarios: tuple[ExperimentScenario, ...]
    benchmark_closes: pd.DataFrame
    benchmark_symbols: tuple[str, ...] = ("000300.SH", "000905.SH")
    run_id: str = ""
    hypothesis: str = ""
    initial_cash: float = 0.0
    primary_benchmark_symbol: str = _PRIMARY_BENCHMARK
    known_limitations: tuple[str, ...] = ()
    generated_at: str = ""
    #: The persisted ``metrics["factor_input"]`` audit of the pinned
    #: ``adjusted_bar`` rows the factor actually consumed: ``{"adjustment",
    #: "factor_versions", "row_count", "error_break_count",
    #: "invalid_reason_counts"}``.  ``None`` (report inputs that predate the
    #: audit) simply omits the 因子价格口径 section instead of rendering
    #: empty provenance claims.
    factor_input_audit: dict | None = None
    #: The frozen corporate-action trust decision recorded on the run
    #: (``metrics["corporate_action_trust"]``): ``{"trusted", "reasons",
    #: "mode", "dataset_version", "window_start", "window_end"}``.  ``None``
    #: (report inputs that predate the trust gate) reads as a trusted default so
    #: no report renders an untrusted alarm it cannot substantiate.
    corporate_action_trust: dict | None = None
    #: The frozen universe definition identity recorded on the run
    #: (``metrics["meta"]["universe"]`` plus the daily snapshot map):
    #: ``{"universe_id", "universe_version", "rules_version",
    #: "membership_table_sha256", "coverage_start", "coverage_end",
    #: "expected_size"}`` and, when the caller composes them in, the
    #: ``universe_daily_member_counts`` / ``universe_daily_snapshots`` maps.
    #: ``None``/empty (legacy runs resolved through the engineering universe)
    #: hides the frozen-universe section entirely.
    universe: dict | None = None
    #: The pinned real-data acceptance audit persisted with the run
    #: (``metrics["data_acceptance"]``): ``{"acceptance_id", "policy_version",
    #: "operator_id", "created_at", "decision"}``.  ``None`` -- or the
    #: engineering ``{"acceptance_id": None, "status": "UNVERIFIED"}`` shape
    #: that carries no decision -- renders the prominent UNVERIFIED alert: a
    #: report never infers ACCEPTED from missing data.
    data_acceptance: dict[str, object] | None = None
    #: The persisted ``stability_report.json`` payload of a formal
    #: walk-forward run (schedule coverage, boundary exclusions, fold
    #: statuses, per-scenario aggregate returns, per-fold metrics, the policy
    #: hash/thresholds and the final conclusion).  ``None`` (single-window
    #: engineering runs) omits the entire walk-forward section: such a run
    #: has no formal stability conclusion to show, and the section would
    #: never render a global drawdown field.
    walk_forward: dict | None = None


@dataclass(frozen=True)
class SourceStatus:
    """One data source's health line: name, status and version."""

    source: str
    status: str = ""
    version: str = ""


@dataclass(frozen=True)
class BuildCounts:
    """Row counts of the dataset build: raw, standardized and quarantined."""

    raw_rows: int = 0
    standard_rows: int = 0
    quarantine_rows: int = 0


@dataclass(frozen=True)
class FieldDifference:
    """Cross-source comparison outcome for one OHLC field."""

    field: str
    n_compared: int = 0
    max_absolute: float = 0.0
    max_relative: float = 0.0
    relative_samples: tuple[float, ...] = ()


@dataclass(frozen=True)
class AdjustmentSample:
    """One symbol-date whose adjustment differs across sources."""

    symbol: str
    trade_date: date
    left: float | None = None
    right: float | None = None
    relative_difference: float | None = None


@dataclass(frozen=True)
class QualityReportInput:
    """Quality input: a :class:`QualityReport` plus report-only provenance.

    Carries everything ``quality.html`` must show that a bare issue list cannot:
    source/version status, build row counts, cross-source difference
    distributions and samples, the current dataset version and the neutral
    publication-gate decision.
    """

    report: QualityReport
    dataset_version: str = ""
    gate_passed: bool = False
    gate_reasons: tuple[str, ...] = ()
    sources: tuple[SourceStatus, ...] = ()
    counts: BuildCounts = BuildCounts()
    cross_sources: tuple[FieldDifference, ...] = ()
    adjustment_samples: tuple[AdjustmentSample, ...] = ()
    missing_counts: tuple[tuple[str, int], ...] = ()


# --------------------------------------------------------------------------- #
# Template loader (cwd-independent, package-rooted)
# --------------------------------------------------------------------------- #

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _template(name: str):
    return _environment().get_template(name)


# --------------------------------------------------------------------------- #
# Shared formatting / normalization helpers
# --------------------------------------------------------------------------- #


def _day(value: object) -> date:
    """Reduce a datetime64/Timestamp/datetime/date to a plain date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    stamp = pd.Timestamp(value)
    return stamp.date() if stamp is not pd.NaT else date.min


def _iso(value: object) -> str:
    return _day(value).isoformat()


def _trust_block(corporate_action_trust: dict | None) -> dict[str, object]:
    """The report header's corporate-action trust verdict block.

    A missing mapping reads as a *trusted* default so a report input that
    predates the trust gate (or a renderer caller that does not carry the
    decision) never shows an untrusted alarm it cannot substantiate.  When the
    frozen decision is present, the block carries its ``trusted`` flag, the
    per-holding ``reasons`` (only the stable ``symbol`` / ``code`` fields the
    decision records) and the covered date range when the decision names one.
    """
    if not isinstance(corporate_action_trust, dict):
        return {"trusted": True, "reasons": [], "window_text": ""}
    trusted = bool(corporate_action_trust.get("trusted"))
    raw_reasons = corporate_action_trust.get("reasons")
    reasons: list[dict[str, str]] = []
    if isinstance(raw_reasons, list):
        for item in raw_reasons:
            if not isinstance(item, dict):
                continue
            reasons.append(
                {
                    "symbol": str(item.get("symbol", "")),
                    "code": str(item.get("code", "")),
                }
            )
    window_start = corporate_action_trust.get("window_start")
    window_end = corporate_action_trust.get("window_end")
    window_text = ""
    if window_start and window_end:
        window_text = f"{_iso(window_start)} ~ {_iso(window_end)}"
    return {
        "trusted": trusted,
        "reasons": reasons,
        "window_text": window_text,
    }


def _universe_block(universe: dict | None) -> dict | None:
    """The report's frozen-universe section model, or ``None``.

    A missing or empty mapping (a run resolved through the legacy engineering
    universe) renders no frozen-universe section at all.  The daily rows are
    built from the caller-supplied ``universe_daily_member_counts`` /
    ``universe_daily_snapshots`` maps, sorted by ISO date so identical inputs
    always render identical bytes.
    """
    if not isinstance(universe, dict) or not universe.get("universe_id"):
        return None
    coverage_start = universe.get("coverage_start")
    coverage_end = universe.get("coverage_end")
    coverage_text = ""
    if coverage_start and coverage_end:
        coverage_text = f"{coverage_start} ~ {coverage_end}"
    counts = universe.get("universe_daily_member_counts")
    snapshots = universe.get("universe_daily_snapshots")
    counts = counts if isinstance(counts, dict) else {}
    snapshots = snapshots if isinstance(snapshots, dict) else {}
    daily_rows = [
        [
            day,
            str(counts.get(day, "—")),
            str(snapshots.get(day, "—")),
        ]
        for day in sorted({*counts, *snapshots})
    ]
    return {
        "universe_id": str(universe.get("universe_id", "")),
        "universe_version": str(universe.get("universe_version", "")),
        "rules_version": str(universe.get("rules_version", "")),
        "membership_table_sha256": str(
            universe.get("membership_table_sha256", "")
        ),
        "coverage_text": coverage_text,
        "expected_size": universe.get("expected_size"),
        "daily_rows": daily_rows,
    }


def _factor_input_block(factor_input_audit: dict | None) -> dict | None:
    """Normalize the persisted factor-input audit for the template.

    Returns ``None`` when no audit is supplied so the 因子价格口径 section is
    omitted rather than rendered with empty claims.  Factor versions and break
    reasons become sorted ``key: value`` strings (reason strings are attacker-
    controllable data, so they stay plain values that only Jinja's autoescape
    ever renders, never markup).
    """
    if not isinstance(factor_input_audit, dict):
        return None
    versions = factor_input_audit.get("factor_versions")
    version_items = (
        sorted(versions.items()) if isinstance(versions, dict) else []
    )
    reasons = factor_input_audit.get("invalid_reason_counts")
    reason_items = sorted(reasons.items()) if isinstance(reasons, dict) else []
    return {
        "adjustment": str(factor_input_audit.get("adjustment", "")),
        "version_text": "、".join(
            f"{name}: {version}" for name, version in version_items
        ),
        "row_count": _int_text(factor_input_audit.get("row_count")),
        "error_break_count": _int_text(
            factor_input_audit.get("error_break_count")
        ),
        "reason_text": "、".join(
            f"{reason}: {count}" for reason, count in reason_items
        ),
    }


def _data_acceptance_block(data_acceptance: dict[str, object] | None) -> dict | None:
    """Normalize the persisted real-data acceptance audit for the template.

    Returns ``None`` when no audit is supplied -- or when the mapping carries
    no concrete ``decision`` (the engineering ``{"acceptance_id": None,
    "status": "UNVERIFIED"}`` shape) -- so the 真实数据验收 section renders its
    prominent UNVERIFIED alert instead of an empty claim: a report never
    infers ACCEPTED from missing data.  Every field stays a plain string that
    only Jinja's autoescape ever renders, never markup.
    """
    if not isinstance(data_acceptance, dict):
        return None
    decision = data_acceptance.get("decision")
    if not decision:
        return None
    return {
        "decision": str(decision),
        "policy_version": str(data_acceptance.get("policy_version", "")),
        "acceptance_id": str(data_acceptance.get("acceptance_id") or ""),
        "operator_id": str(data_acceptance.get("operator_id", "")),
        "created_at": str(data_acceptance.get("created_at", "")),
    }


def _money(value: float | int | None, nd: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{nd}f}"


def _pct(value: float | int | None, nd: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{nd}f}%"


def _int_text(value: object) -> str:
    return "—" if value is None else f"{int(value)}"


def _frame_table(
    frame: pd.DataFrame,
    columns: list[str],
    row_of,
) -> tuple[list[str], list[list[str]]]:
    """Convert a frame into a header list and string-valued row lists."""
    if frame is None or frame.empty:
        return columns, []
    rows: list[list[str]] = []
    for record in frame.to_dict("records"):
        rows.append(row_of(record))
    return columns, rows


# --------------------------------------------------------------------------- #
# Formatting builders shared by the two renderers
# --------------------------------------------------------------------------- #


def _issue_brief(item) -> str:
    """A short, safe detail string for an issue (never raw supplier text)."""
    details = item.details if item.details else {}
    pieces: list[str] = []
    field = details.get("field")
    if field is not None:
        pieces.append(f"字段 {field}")
    for key in ("left", "right"):
        if details.get(key) is not None:
            pieces.append(f"{key}={details[key]}")
    absolute = details.get("absolute_difference")
    relative = details.get("relative_difference")
    if absolute is not None:
        pieces.append(f"绝对差 {absolute}")
    if relative is not None:
        pieces.append(f"相对差 {_pct(relative, 4)}")
    return " ".join(pieces)


_MISSING_LABELS = frozenset(
    {
        MISSING_NOT_LISTED,
        MISSING_DELISTED,
        MISSING_NON_TRADING_DAY,
        MISSING_UNKNOWN_OR_SUSPENDED,
        MISSING_PRIMARY_SOURCE,
        MISSING_UNEXPLAINED,
    }
)

_CATEGORY_CODES: tuple[tuple[str, frozenset[str]], ...] = (
    ("duplicate", frozenset({CODE_DUPLICATE_CONFLICT})),
    ("ohlc", frozenset({
        CODE_INVALID_OHLC,
        CODE_NONPOSITIVE_PRICE,
        CODE_NEGATIVE_VOLUME,
        CODE_NEGATIVE_AMOUNT,
    })),
    ("unit", frozenset({CODE_UNIT_MISMATCH})),
    ("cross_source", frozenset({
        CODE_WITHIN_TOLERANCE,
        CODE_PRICE_DIFFERENCE,
        CODE_CLOSE_DIFFERENCE,
        CODE_ADJUSTMENT_BASIS_MISMATCH,
    })),
)

_CATEGORY_LABELS = {
    "duplicate": "重复记录",
    "missing": "缺失/停牌",
    "ohlc": "OHLC 非法",
    "unit": "单位不一致",
    "cross_source": "跨源差异",
    "other": "其他",
}


def _category_of(code: str) -> str:
    if code in _MISSING_LABELS:
        return "missing"
    for category, codes in _CATEGORY_CODES:
        if code in codes:
            return category
    return "other"


# --------------------------------------------------------------------------- #
# Plotly embedding
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _plotlyjs() -> str:
    import plotly.offline

    return plotly.offline.get_plotlyjs()


def _figure_html(fig: go.Figure) -> str:
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        config=_PLOTLY_CONFIG,
    )


def _layout(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=title,
        height=420,
        margin={"l": 50, "r": 20, "t": 50, "b": 40},
        legend={"orientation": "h", "y": -0.2},
    )
    return fig


def _first_day(frame: pd.DataFrame) -> date | None:
    """The earliest trade date in ``frame``, or ``None`` when it holds none."""
    if frame.empty:
        return None
    return _day(frame["trade_date"].min())


def _common_base_day(frames: list[pd.DataFrame]) -> date | None:
    """The first day every frame is plotted on, or ``None`` if none has one.

    Indexing each series against its own first point is what made the net-value
    chart read as a comparison it never was: a benchmark whose history starts
    years before the strategy got rebased at *its* own first day, so the gap
    between the lines carried the benchmark's earlier run instead of any
    relative performance.  One shared base day removes the ambiguity.
    """
    firsts = [day for day in (_first_day(frame) for frame in frames) if day]
    return max(firsts) if firsts else None


def _return_series(
    frame: pd.DataFrame, value_col: str, base_day: date
) -> tuple[list[str], list[float]]:
    """Chronological labels plus cumulative return (%) since ``base_day``."""
    data = frame.sort_values("trade_date")
    dated = [
        (_day(day), float(value))
        for day, value in zip(data["trade_date"], data[value_col])
        if _day(day) >= base_day
    ]
    if not dated:
        return [], []
    base = dated[0][1]
    xs = [day.isoformat() for day, _ in dated]
    if base > 0:
        ys = [round((value / base - 1.0) * 100.0, 4) for _, value in dated]
    else:
        ys = [0.0] * len(dated)
    return xs, ys


# --------------------------------------------------------------------------- #
# Experiment report
# --------------------------------------------------------------------------- #

_SUMMARY_HEADINGS = [
    "成本情景",
    "区间",
    "天数",
    "期初权益",
    "期末权益",
    "累计收益",
    "年化收益",
    "年化波动",
    "最大回撤",
    "换手率",
    "期末现金",
    "现金占比",
    "停牌资产比例",
]


def _summary_rows(experiment: ExperimentReportInput) -> list[list[str]]:
    rows: list[list[str]] = []
    for scenario in experiment.scenarios:
        metrics = scenario.metrics
        rows.append(
            [
                scenario.name,
                f"{_iso(scenario.equity['trade_date'].iloc[0])} ~ "
                f"{_iso(scenario.equity['trade_date'].iloc[-1])}",
                str(metrics.n_days),
                _money(metrics.start_equity),
                _money(metrics.end_equity),
                _pct(metrics.cumulative_return),
                _pct(metrics.annualized_return),
                _pct(metrics.annualized_volatility),
                _pct(metrics.max_drawdown),
                _pct(metrics.turnover),
                _money(metrics.end_equity * metrics.cash_ratio_end),
                _pct(metrics.cash_ratio_end),
                _pct(metrics.stale_asset_ratio_end),
            ]
        )
    return rows


def _net_value_figure(experiment: ExperimentReportInput) -> go.Figure:
    """Every trace as cumulative return over one shared base day.

    Strategy and benchmarks are indexed from the same day, and that day is
    named in the title: "期初 = 100" per series is only meaningful when the
    series share an origin, and a benchmark rebased at its own start (which
    may predate the experiment by years) draws a period difference as if it
    were relative performance.
    """
    plotted: list[tuple[str, pd.DataFrame, str, bool]] = [
        (scenario.name, scenario.equity, "total_equity", False)
        for scenario in experiment.scenarios
    ]
    for symbol in experiment.benchmark_symbols:
        series = experiment.benchmark_closes[
            experiment.benchmark_closes["symbol"] == symbol
        ]
        plotted.append((symbol, series, "close", True))

    base_day = _common_base_day([frame for _, frame, _, _ in plotted])
    fig = go.Figure()
    if base_day is not None:
        for name, frame, value_col, is_benchmark in plotted:
            xs, ys = _return_series(frame, value_col, base_day)
            if not xs:
                continue
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines",
                    name=name,
                    **({"line": {"dash": "dash"}} if is_benchmark else {}),
                )
            )
    title = "同区间累计收益（%）"
    if base_day is not None:
        title = f"{title}（基准日 = {base_day.isoformat()}）"
    return _layout(fig, title)


def _drawdown_figure(experiment: ExperimentReportInput) -> go.Figure:
    fig = go.Figure()
    for scenario in experiment.scenarios:
        frame = scenario.equity.sort_values("trade_date")
        values = frame["total_equity"].astype(float)
        running = values.cummax()
        dd = ((values / running - 1.0) * 100.0).where(running > 0, 0.0)
        fig.add_trace(
            go.Scatter(
                x=[_iso(day) for day in frame["trade_date"]],
                y=[round(float(v), 4) for v in dd],
                mode="lines",
                name=scenario.name,
                fill="tozeroy",
            )
        )
    return _layout(fig, "回撤曲线（%）")


def _cost_rows(metrics: PerformanceMetrics) -> list[list[str]]:
    total = (
        metrics.total_commission
        + metrics.total_stamp_tax
        + metrics.slippage_estimate
    )
    return [
        ["佣金总额", _money(metrics.total_commission)],
        ["印花税总额", _money(metrics.total_stamp_tax)],
        ["滑点成本", _money(metrics.slippage_estimate)],
        ["成本合计（佣金+印花税+滑点）", _money(total)],
    ]


def _holdings_rows(frame: pd.DataFrame) -> tuple[list[str], list[list[str]]]:
    columns = ["证券代码", "持股数量", "市值", "权重"]
    if frame is None or frame.empty:
        return columns, []

    def row_of(record: dict) -> list[str]:
        weight = record.get("weight")
        return [
            str(record.get("symbol", "")),
            _int_text(record.get("quantity")),
            _money(record.get("market_value")),
            _pct(weight) if weight is not None else "—",
        ]

    return _frame_table(frame, columns, row_of)


def _unfilled_rows(frame: pd.DataFrame) -> tuple[list[str], list[list[str]]]:
    columns = [
        "交易日",
        "订单",
        "方向",
        "证券代码",
        "请求数量",
        "成交数量",
        "未成交数量",
        "原因",
    ]
    if frame is None or frame.empty:
        return columns, []

    def row_of(record: dict) -> list[str]:
        return [
            _iso(record.get("trade_date")),
            str(record.get("order_id", "")),
            str(record.get("side", "")),
            str(record.get("symbol", "")),
            _int_text(record.get("requested_quantity")),
            _int_text(record.get("filled_quantity")),
            _int_text(record.get("rejected_quantity")),
            str(record.get("reason", "")),
        ]

    return _frame_table(frame, columns, row_of)


def _action_rows(frame: pd.DataFrame) -> tuple[list[str], list[list[str]]]:
    columns = [
        "序号",
        "事件ID",
        "证券代码",
        "除权除息日",
        "股权登记日",
        "现金入账",
        "送转股数",
        "说明",
    ]
    if frame is None or frame.empty:
        return columns, []

    def row_of(record: dict) -> list[str]:
        return [
            _int_text(record.get("seq")),
            str(record.get("action_id", "")),
            str(record.get("symbol", "")),
            _iso(record.get("ex_date")) if record.get("ex_date") is not None else "—",
            _iso(record.get("record_date"))
            if record.get("record_date") is not None
            else "—",
            _money(record.get("cash_credited")),
            _int_text(record.get("shares_added")),
            str(record.get("note", "")),
        ]

    return _frame_table(frame, columns, row_of)


def _daily_trade_snapshot_rows(frame: pd.DataFrame) -> list[list[str]]:
    """Return per-day BUY/SELL top-five trades by notional."""
    if frame is None or frame.empty:
        return []
    required = {"trade_date", "side", "symbol", "quantity", "price"}
    if not required.issubset(frame.columns):
        return []
    data = frame.copy()
    data["notional"] = data["quantity"].astype(float) * data["price"].astype(float)
    data = data.sort_values(
        ["trade_date", "side", "notional", "symbol"],
        ascending=[True, True, False, True],
    )
    data["rank"] = data.groupby(["trade_date", "side"], sort=False).cumcount()
    data = data[data["rank"] < 5]
    rows: list[list[str]] = []
    for record in data.to_dict("records"):
        rows.append(
            [
                _iso(record.get("trade_date")),
                "买入" if record.get("side") == "BUY" else "卖出",
                str(record.get("symbol", "")),
                _money(record.get("notional")),
                _int_text(record.get("quantity")),
            ]
        )
    return rows


def _execution_rows(summary: dict[str, object] | None) -> list[list[str]]:
    """Format the persisted execution-drift summary for one cost scenario.

    ``summary`` is the execution_diagnostics.json object written under the Goal
    #3 schema (spec section 8): order-level counts plus notional priced at the
    signal-day close, with unfilled reasons counted by reason.  Missing keys
    degrade gracefully to "无"/blank until the local writer is updated.
    """
    if not summary:
        return []
    reasons = summary.get("unfilled_reason_counts", {})
    reason_text = "、".join(
        f"{reason}: {count}" for reason, count in sorted(dict(reasons).items())
    ) or "无"
    return [
        [
            _int_text(summary.get("planned_order_count")),
            _int_text(summary.get("filled_order_count")),
            _int_text(summary.get("partial_order_count")),
            _int_text(summary.get("rejected_order_count")),
            _money(summary.get("planned_gross_notional")),
            _money(summary.get("actual_gross_notional")),
            _money(summary.get("unfilled_notional")),
            _pct(summary.get("execution_deviation_ratio")),
            reason_text,
            _money(summary.get("end_cash")),
            _pct(summary.get("stale_asset_ratio")),
        ]
    ]


def _scenario_sections(
    experiment: ExperimentReportInput,
) -> list[dict]:
    sections: list[dict] = []
    for scenario in experiment.scenarios:
        metrics = scenario.metrics
        equity = scenario.equity.sort_values("trade_date")
        stale_days = int(equity["stale_days"].iloc[-1]) if len(equity) else 0
        end_cash = equity["cash"].iloc[-1] if len(equity) else 0.0
        holdings_cols, holdings_rows = _holdings_rows(scenario.holdings)
        unfilled_cols, unfilled_rows = _unfilled_rows(scenario.rejections)
        action_cols, action_rows = _action_rows(scenario.action_ledger)
        execution_rows = _execution_rows(scenario.execution_summary)
        daily_trade_rows = _daily_trade_snapshot_rows(scenario.fills)
        sections.append(
            {
                "name": scenario.name,
                "cost_title": f"{scenario.name} 成本拆分",
                "cost_rows": _cost_rows(metrics),
                "cash_text": (
                    f"期末现金 {_money(end_cash)}，现金占比 "
                    f"{_pct(metrics.cash_ratio_end)}，停牌资产比例 "
                    f"{_pct(metrics.stale_asset_ratio_end)}，末次陈旧天数 "
                    f"{stale_days}"
                ),
                "holdings_cols": holdings_cols,
                "holdings_rows": holdings_rows,
                "unfilled_cols": unfilled_cols,
                "unfilled_rows": unfilled_rows,
                "action_cols": action_cols,
                "action_rows": action_rows,
                "execution_rows": execution_rows,
                "daily_trade_rows": daily_trade_rows,
            }
        )
    return sections


def _walk_forward_block(payload: dict | None) -> dict | None:
    """Normalize the stability-report payload for the template.

    ``None`` (a single-window engineering run) omits the walk-forward
    section entirely.  Every dynamic string stays a plain value that only
    Jinja's autoescape renders.  The block deliberately carries no global
    drawdown or Calmar field: cross-fold path metrics are forbidden.
    """
    if not isinstance(payload, dict) or not payload.get("stability_policy_hash"):
        return None
    schedule = payload.get("schedule") or {}
    boundaries = [
        {
            "calendar_start": str(item.get("calendar_start", "")),
            "calendar_end": str(item.get("calendar_end", "")),
            "reason": str(item.get("reason", "")),
        }
        for item in schedule.get("boundaries", []) or []
        if isinstance(item, dict)
    ]
    fold_statuses = [
        {
            "fold_id": str(item.get("fold_id", "")),
            "status": str(item.get("status", "")),
            "reason_code": str(item.get("reason_code") or "—"),
        }
        for item in payload.get("fold_statuses", []) or []
        if isinstance(item, dict)
    ]
    scenario_rows = [
        {
            "scenario": str(item.get("scenario", "")),
            "aggregate_return": _pct(item.get("aggregate_return")),
            "annualized_return": _pct(item.get("annualized_return")),
            "annualized_volatility": _pct(item.get("annualized_volatility")),
            "sharpe_zero_rf": (
                "—" if item.get("sharpe_zero_rf") is None
                else f"{float(item['sharpe_zero_rf']):.4f}"
            ),
            "oos_return_observations": str(item.get("oos_return_observations", 0)),
            "annualization_observations": str(
                item.get("annualization_observations", 0)
            ),
        }
        for item in payload.get("scenario_aggregates", []) or []
        if isinstance(item, dict)
    ]
    fold_rows = [
        {
            "fold_id": str(item.get("fold_id", "")),
            "scenario": str(item.get("scenario", "")),
            "fold_calendar_return": _pct(item.get("fold_calendar_return")),
            "per_fold_max_drawdown": _pct(item.get("per_fold_max_drawdown")),
            "observation_count": str(item.get("observation_count", 0)),
            "reject_rate": _pct(item.get("reject_rate")),
            "turnover": (
                "—" if item.get("turnover") is None
                else f"{float(item['turnover']):.4f}"
            ),
            "explicit_cost_ratio": _pct(item.get("explicit_cost_ratio")),
        }
        for item in payload.get("fold_metrics", []) or []
        if isinstance(item, dict)
    ]
    thresholds = payload.get("thresholds") or {}
    buffered_payload = payload.get("buffered")
    buffered = None
    if isinstance(buffered_payload, dict) and buffered_payload.get(
        "portfolio_rule_version"
    ):
        buffered_folds = [
            {
                "fold_id": str(item.get("fold_id", "")),
                "signal_days": str(item.get("signal_days", 0)),
                "retained": str(item.get("retained", 0)),
                "entered": str(item.get("entered", 0)),
                "exited": str(item.get("exited", 0)),
                "risk_invalid": str(item.get("risk_invalid", 0)),
                "achieved_gross_exposure": _pct(
                    item.get("achieved_gross_exposure")
                ),
                "cash_residue": _pct(item.get("cash_residue")),
            }
            for item in buffered_payload.get("folds", []) or []
            if isinstance(item, dict)
        ]
        buffered_scenarios = [
            {
                "scenario": str(item.get("scenario", "")),
                "band_suppressed_rows": str(item.get("band_suppressed_rows", 0)),
                "band_suppressed_amount": _money(
                    item.get("band_suppressed_amount")
                ),
                "lot_suppressed_rows": str(item.get("lot_suppressed_rows", 0)),
                "lot_suppressed_amount": _money(
                    item.get("lot_suppressed_amount")
                ),
            }
            for item in buffered_payload.get("scenarios", []) or []
            if isinstance(item, dict)
        ]
        buffered = {
            "portfolio_rule_version": str(
                buffered_payload["portfolio_rule_version"]
            ),
            "folds": buffered_folds,
            "scenarios": buffered_scenarios,
        }
    return {
        "research_status": str(payload.get("research_status", "")),
        "stability_conclusion": str(payload.get("stability_conclusion")),
        "stability_policy_hash": str(payload["stability_policy_hash"]),
        "stability_policy_version": str(
            payload.get("stability_policy_version", "")
        ),
        "minimum_executed_folds": str(thresholds.get("minimum_executed_folds", "")),
        "minimum_positive_fold_ratio": str(
            thresholds.get("minimum_positive_fold_ratio", "")
        ),
        "worst_fold_calendar_return_floor": str(
            thresholds.get("worst_fold_calendar_return_floor", "")
        ),
        "fold_count": str(schedule.get("fold_count", 0)),
        "boundary_count": str(schedule.get("boundary_count", 0)),
        "boundaries": boundaries,
        "fold_statuses": fold_statuses,
        "scenario_rows": scenario_rows,
        "fold_rows": fold_rows,
        "reasons": [str(reason) for reason in payload.get("reasons", []) or []],
        "buffered": buffered,
    }


def render_experiment_report(
    experiment: ExperimentReportInput,
    destination: Path,
) -> Path:
    """Render ``experiment`` to a self-contained HTML file at ``destination``."""
    if not isinstance(experiment, ExperimentReportInput):
        raise TypeError(
            "render_experiment_report expects an ExperimentReportInput, got "
            f"{type(experiment).__name__}"
        )
    benchmark_excess = None
    if experiment.scenarios:
        metrics = experiment.scenarios[-1].metrics
        benchmark_excess = metrics.benchmark_excess_return
    versions = {
        "experiment_id": experiment.experiment_id,
        "run_id": experiment.run_id,
        "dataset_version": experiment.dataset_version,
        "universe_version": experiment.universe_version,
        "code_commit": experiment.code_commit,
        "generated_at": experiment.generated_at,
    }
    universe = _universe_block(experiment.universe)
    charts = [
        _figure_html(_net_value_figure(experiment)),
        _figure_html(_drawdown_figure(experiment)),
    ]
    body = _template("experiment.html.j2").render(
        versions=versions,
        hypothesis=experiment.hypothesis,
        initial_cash=_money(experiment.initial_cash),
        summary_headings=_SUMMARY_HEADINGS,
        summary_rows=_summary_rows(experiment),
        charts=charts,
        plotly_js=_plotlyjs(),
        scenario_sections=_scenario_sections(experiment),
        # Phase-one boundaries always lead the 已知限制 list; any run-specific
        # limitations supplied by the caller follow.
        known_limitations=(
            list(_PHASE_ONE_KNOWN_LIMITATIONS) + list(experiment.known_limitations)
        ),
        primary_benchmark=experiment.primary_benchmark_symbol,
        benchmark_excess=(
            _pct(benchmark_excess) if benchmark_excess is not None else "—"
        ),
        trust=_trust_block(experiment.corporate_action_trust),
        universe=universe,
        factor_input=_factor_input_block(experiment.factor_input_audit),
        data_acceptance=_data_acceptance_block(experiment.data_acceptance),
        walk_forward=_walk_forward_block(experiment.walk_forward),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body, encoding="utf-8")
    return destination


# --------------------------------------------------------------------------- #
# Quality report
# --------------------------------------------------------------------------- #

_ISSUE_COLUMNS = ["严重级", "代码", "表", "证券代码", "日期", "说明"]


def _issue_rows(report: QualityReport) -> tuple[list[str], list[list[str]]]:
    rows: list[list[str]] = []
    for item in report.issues:
        rows.append(
            [
                item.severity.value,
                item.code,
                item.table,
                item.symbol or "—",
                item.trade_date.isoformat() if item.trade_date is not None else "—",
                _issue_brief(item) or "—",
            ]
        )
    return _ISSUE_COLUMNS, rows


def _category_groups(report: QualityReport) -> list[dict]:
    groups: dict[str, list] = {}
    for item in report.issues:
        category = _category_of(item.code)
        groups.setdefault(category, []).append(item)
    ordered = []
    for label in (
        "missing",
        "duplicate",
        "ohlc",
        "unit",
        "cross_source",
        "other",
    ):
        items = groups.get(label)
        if items is None:
            continue
        rows = [
            [
                item.severity.value,
                item.code,
                item.table,
                item.symbol or "—",
                item.trade_date.isoformat() if item.trade_date is not None else "—",
                _issue_brief(item) or "—",
            ]
            for item in items
        ]
        ordered.append(
            {
                "label": _CATEGORY_LABELS[label],
                "count": len(items),
                "rows": rows,
            }
        )
    return ordered


def _severity_counts(report: QualityReport) -> list[dict]:
    counts = report.by_severity()
    return [
        {"severity": severity.value, "count": counts.get(severity.value, 0)}
        for severity in Severity
    ]


def _cross_rows(cross_sources: tuple[FieldDifference, ...]) -> list[list[str]]:
    rows: list[list[str]] = []
    for diff in cross_sources:
        rows.append(
            [
                diff.field,
                str(diff.n_compared),
                f"{diff.max_absolute:.6f}",
                f"{diff.max_relative:.6f}",
            ]
        )
    return rows


def _difference_figure(cross_sources: tuple[FieldDifference, ...]) -> go.Figure:
    fig = go.Figure()
    for diff in cross_sources:
        samples = [float(v) * 100.0 for v in diff.relative_samples]
        if samples:
            fig.add_trace(
                go.Histogram(
                    x=samples,
                    name=diff.field,
                    opacity=0.6,
                    nbinsx=30,
                )
            )
    return _layout(fig, "跨源相对差异分布（%）")


def _adjustment_rows(
    samples: tuple[AdjustmentSample, ...],
) -> tuple[list[str], list[list[str]]]:
    columns = ["证券代码", "日期", "左源值", "右源值", "相对差"]
    rows: list[list[str]] = []
    for sample in samples:
        rows.append(
            [
                sample.symbol,
                _iso(sample.trade_date),
                f"{sample.left:.6f}" if sample.left is not None else "—",
                f"{sample.right:.6f}" if sample.right is not None else "—",
                f"{sample.relative_difference:.6f}"
                if sample.relative_difference is not None
                else "—",
            ]
        )
    return columns, rows


def render_quality_report(
    report: QualityReportInput,
    destination: Path,
) -> Path:
    """Render the quality input to a self-contained HTML file at ``destination``."""
    if not isinstance(report, QualityReportInput):
        raise TypeError(
            "render_quality_report expects a QualityReportInput (a QualityReport "
            f"plus provenance/gate fields), got {type(report).__name__}"
        )
    sources = [
        {
            "source": item.source,
            "status": item.status,
            "version": item.version,
        }
        for item in report.sources
    ]
    counts = {
        "raw": report.counts.raw_rows,
        "standard": report.counts.standard_rows,
        "quarantine": report.counts.quarantine_rows,
    }
    issue_cols, issue_rows = _issue_rows(report.report)
    missing_rows = [[label, str(count)] for label, count in report.missing_counts]
    difference = _difference_figure(report.cross_sources)
    chart_fragments = []
    if difference.data:
        chart_fragments.append(_figure_html(difference))
    adj_cols, adj_rows = _adjustment_rows(report.adjustment_samples)
    reasons = list(report.gate_reasons)
    body = _template("quality.html.j2").render(
        dataset_version=report.dataset_version,
        gate=report.gate_passed,
        gate_text="PASS" if report.gate_passed else "BLOCK",
        gate_reasons=reasons,
        sources=sources,
        counts=counts,
        severity_counts=_severity_counts(report.report),
        category_groups=_category_groups(report.report),
        issue_cols=issue_cols,
        issue_rows=issue_rows,
        missing_cols=["缺失类别", "记录数"],
        missing_rows=missing_rows,
        cross_cols=["字段", "比较数", "最大绝对差", "最大相对差"],
        cross_rows=_cross_rows(report.cross_sources),
        adj_cols=adj_cols,
        adj_rows=adj_rows,
        charts=chart_fragments,
        plotly_js=_plotlyjs() if chart_fragments else "",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body, encoding="utf-8")
    return destination
