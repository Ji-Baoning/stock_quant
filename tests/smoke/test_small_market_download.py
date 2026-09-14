"""Opt-in live smoke: one small real data update through the whole pipeline.

The smoke verifies interface connectivity and the raw-to-published contract
over a small recent window for a single symbol (``600000.SH``), per design spec
§28.7: it claims nothing about the absolute correctness of the fetched values.
A quality ``BLOCK`` is a valid diagnostic result (for example an AKShare
symbol-column/function drift or an unavailable optional source); a schema or
authentication exception escaping the pipeline is what fails the test.

The test is marked ``smoke`` and deselected by the default pytest marker
expression; ordinary ``pytest`` never runs it.  An operator exports their own
rotated ``TUSHARE_TOKEN`` and runs ``pytest -m smoke -v``.  Everything offline
buildable here (the mini project config tree and the synthetic baseline
dataset) mirrors ``tests/integration/conftest.build_fixture_project`` but for a
one-symbol universe, so the update performs only a handful of live requests.
Nothing in this module imports a supplier SDK or reads a token at module scope.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from importlib.util import find_spec
from pathlib import Path

import pandas as pd
import pytest
import yaml

from stock_quant.data_model.adjusted_bar import build_adjusted_bars
from stock_quant.data_model.calendar_coverage import coverage_payload, seed_span
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    CORPORATE_ACTION_COVERAGE_COLUMNS,
    CORPORATE_ACTION_QUARANTINE_COLUMNS,
    DAILY_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
)
from stock_quant.data_pipeline import (
    DATASET_BUILD_CONTRACT_VERSION,
    DataPipeline,
    DataUpdateRequest,
)
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import QualityReport

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The committed configuration template the smoke project copies.  The
#: repository root carries no live ``configs/`` tree; the template directory
#: is a copy source only and is never resolved as a project root.
_TEMPLATE_CONFIG = _REPO_ROOT / "templates" / "project-config"

#: The single equity the smoke updates.  ``600000.SH`` is the canonical form the
#: Tushare role and the standardized ``daily_bar`` use.
_SMOKE_SYMBOL = "600000.SH"
_NAME = "浦发银行"
_LIST_DATE = date(1999, 11, 10)

#: Benchmark codes for the smoke's live AKShare ``index_history`` role.  The EM
#: endpoint consumes the bare six-digit code (``000300``), which is what this
#: throwaway project passes on; the repository's canonical benchmark symbols
#: carry ``.SH`` suffixes and are documented as a known live-fidelity gap in
#: ``docs/operations/phase-one-validation.md``.
_BENCHMARKS = ("000300", "000905")

_BASE_PRICE = 10.0
_INDEX_PRICE = 4000.0
_INGESTED = pd.Timestamp("2020-01-01T00:00:00Z")

_CONFIG_NAMES = (
    "project.yml",
    "sources.yml",
    "costs.yml",
    "trading_rules.yml",
    "universe.yml",
)

_BACKFILL_DAYS = 45


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _recent_window() -> tuple[date, date]:
    """The small live window: newest weekday strictly before today and ten back."""
    end = date.today() - timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    return end - timedelta(days=10), end


def _instrument_frame(
    sessions: list[date], symbol: str, close: float, *, source: str
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(sessions),
            "symbol": symbol,
            "open": [close] * len(sessions),
            "high": [close] * len(sessions),
            "low": [close] * len(sessions),
            "close": [close] * len(sessions),
            "volume": [1_000_000] * len(sessions),
            "amount": [close * 1_000_000.0] * len(sessions),
            "adjustment": "unadjusted",
            "source": source,
            "ingested_at": [_INGESTED] * len(sessions),
        }
    )


def _bars(sessions: list[date]) -> pd.DataFrame:
    frames = [
        _instrument_frame(sessions, _SMOKE_SYMBOL, _BASE_PRICE, source="tushare")
    ]
    for symbol in _BENCHMARKS:
        frames.append(
            _instrument_frame(sessions, symbol, _INDEX_PRICE, source="akshare")
        )
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def _security_master() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series([_SMOKE_SYMBOL], dtype="object"),
            "name": pd.Series([_NAME], dtype="object"),
            "exchange": pd.Series(["SH"], dtype="object"),
            "board": pd.Series(["sh_main"], dtype="object"),
            "list_date": pd.to_datetime([_LIST_DATE]),
            "delist_date": pd.Series([pd.NaT], dtype="datetime64[ns]"),
            "list_status": pd.Series(["L"], dtype="object"),
        }
    )[SECURITY_MASTER_COLUMNS]


def _corporate_action() -> pd.DataFrame:
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


def _trading_calendar(sessions: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(sessions),
            "is_trading_day": [True] * len(sessions),
        }
    )[TRADING_CALENDAR_COLUMNS]


def build_smoke_project(root: Path) -> Path:
    """Materialise a one-symbol live project: configs plus a synthetic baseline.

    The committed ``templates/project-config`` tree is copied so source/cost/
    rule shapes match the real project, and ``sources.yml`` is rewritten with
    every supplier enabled -- the smoke requires the optional ``baostock``
    validation series, which the template may ship disabled.  ``project.yml``
    and ``universe.yml`` are overridden for a
    one-symbol universe over a recent backfill window.  A synthetic six-table
    dataset (including ``adjusted_bar`` and the quarantine table, built with
    the production adjusted-bar builder) is published so
    ``DataPipeline.update`` has the carried master/calendar baseline it
    requires.  No market data or credentials are committed; the project lives
    under ``tmp_path``.
    """
    root = Path(root)
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in _CONFIG_NAMES:
        (config_dir / name).write_text(
            (_TEMPLATE_CONFIG / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    sources = yaml.safe_load(
        (_TEMPLATE_CONFIG / "sources.yml").read_text(encoding="utf-8")
    )
    for settings in sources.values():
        if isinstance(settings, dict):
            settings["enabled"] = True
    (config_dir / "sources.yml").write_text(
        yaml.safe_dump(sources, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    today = date.today()
    _, window_end = _recent_window()
    baseline_start = window_end - timedelta(days=_BACKFILL_DAYS)
    (config_dir / "project.yml").write_text(
        yaml.safe_dump(
            {
                "start_date": baseline_start.isoformat(),
                "end_date": window_end.isoformat(),
                "initial_cash": 100000,
                "benchmark_symbols": list(_BENCHMARKS),
                "publication_time": "15:00",
            }
        ),
        encoding="utf-8",
    )
    entry = {
        "symbol": _SMOKE_SYMBOL,
        "name_at_selection": _NAME,
        "exchange": "SH",
        "board": "sh_main",
        "selected_as_of": today.isoformat(),
        "boundary_tags": ["engineering_smoke"],
        "selection_reason": "实时冒烟唯一样本，工程验收用，非投资建议",
    }
    (config_dir / "universe.yml").write_text(
        yaml.safe_dump(
            {
                "selected_as_of": today.isoformat(),
                "entries": [entry],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    sessions = _weekdays(baseline_start, window_end)
    daily = _bars(sessions)
    corporate_actions = _corporate_action()
    empty_quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
    tables = {
        "daily_bar": daily,
        "adjusted_bar": build_adjusted_bars(
            daily,
            corporate_actions,
            empty_quarantine,
            # The synthetic baseline carries no coverage evidence table yet;
            # an empty frame keeps the adjusted rows at INFO until the live
            # update republishes real per-window evidence.
            pd.DataFrame(columns=CORPORATE_ACTION_COVERAGE_COLUMNS),
            symbols=(_SMOKE_SYMBOL,),
        ),
        "security_master": _security_master(),
        "corporate_action": corporate_actions,
        "corporate_action_quarantine": empty_quarantine,
        "trading_calendar": _trading_calendar(sessions),
    }
    DatasetPublisher(root).publish(
        tables,
        QualityReport(),
        build_config={
            "origin": "bootstrap",
            "pipeline_contract_version": DATASET_BUILD_CONTRACT_VERSION,
            "calendar_coverage": coverage_payload(
                [seed_span(baseline_start, window_end)]
            ),
            "full_history_acceptance_start": None,
        },
    )
    return root


def _assert_published_rows(root: Path, symbol: str, start: date, end: date) -> None:
    """On PASS, confirm live bars for the smoke symbol reached the dataset."""
    reader = DatasetReader(root)
    version = DatasetPublisher(root).current().version
    with reader.open(version) as context:
        daily = context.read("daily_bar")
    selected = daily[
        (daily["symbol"] == symbol)
        & (daily["trade_date"] >= pd.Timestamp(start))
        & (daily["trade_date"] <= pd.Timestamp(end))
    ]
    assert not selected.empty, (
        "published daily_bar has no live rows for the smoke symbol"
    )
    assert selected["close"].notna().all()


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("TUSHARE_TOKEN") or find_spec("akshare") is None
    or find_spec("baostock") is None or find_spec("tushare") is None,
    reason="TUSHARE_TOKEN and the supplier SDKs are required",
)
def test_small_live_update_publishes_or_returns_blocked_report(tmp_path) -> None:
    root = build_smoke_project(tmp_path)
    start, end = _recent_window()
    outcome = DataPipeline(root).update(
        DataUpdateRequest(start_date=start, end_date=end)
    )

    names = {status.source for status in outcome.source_status}
    assert {"tushare", "akshare", "baostock"} <= names
    decision = evaluate_publication(outcome.quality_report).decision
    assert decision in {"PASS", "BLOCK"}
    if outcome.dataset_ref is None and decision == "PASS":
        # The pipeline records a required-adapter *initialisation* failure only
        # in source_status (no blocking issue), so PASS-without-publish means an
        # environment/authentication misconfiguration rather than a data
        # diagnosis.  Surface it as a failure instead of hiding it as a BLOCK.
        details = ", ".join(
            f"{status.source}: {status.reason}"
            for status in outcome.source_status
            if not status.ok
        )
        pytest.fail(
            "no dataset was published but the report gate reads PASS; a required "
            f"source could not be initialised: {details}"
        )
    assert (outcome.dataset_ref is not None) == (decision == "PASS")
    if decision == "PASS":
        _assert_published_rows(root, _SMOKE_SYMBOL, start, end)
