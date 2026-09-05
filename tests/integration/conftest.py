"""Shared offline fixtures for the CLI / data-pipeline end-to-end tests.

Task 13 wires the research runner to a Typer CLI and builds a thin end-to-end
acceptance over one synthetic project.  A test-support file is justified here
because *both* ``test_cli.py`` and ``test_end_to_end.py`` need the exact same
deterministic synthetic project (a ``configs/`` tree copied from the
repository plus one content-addressed 5-table dataset that also carries a
``corporate_action_coverage`` evidence table) and the same two fixtures
(``cli_runner``, ``fixture_root``).  All fixtures are offline and live
under ``tmp_path_factory``; nothing here touches the network or a token, and no
real market data is committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.data_model.corporate_action_coverage import (
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    DAILY_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_quality.models import QualityReport

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The experiment spec name the CLI research command is invoked with.
FIXTURE_SPEC = "configs/experiments/momentum_60d.yml"

#: Synthetic calendar/bars run from 2018 to a few sessions into 2022 so the
#: last weekly signal of 2021 has a real execution open day ahead of it.
CAL_START = date(2018, 1, 1)
CAL_END = date(2022, 1, 7)
BARS_START = date(2019, 8, 1)
BARS_END = date(2022, 1, 7)
LIST_DATE = date(2018, 1, 2)
_BASE_PRICE = 55.0
_INGESTED = pd.Timestamp("2022-01-08T00:00:00Z")

#: The two benchmark indices carried as flat daily closes (like the reference
#: research-runner fixture).
_BENCHMARK_SYMBOLS = ("000300.SH", "000905.SH")
_BENCHMARK_LEVELS = (4000.0, 2000.0)

#: A short authored experiment spec whose date range is fully covered by the
#: synthetic bars (the repository example spec runs to 2026 and would be
#: pointless to replay over a 2021 fixture).
_FIXTURE_SPEC_YAML = """\
# 离线验收用的短规格（合成数据、非投资建议）。覆盖 2020-01-01..2021-12-31，
# 由 tests/integration/conftest.py 生成到 fixture 工程的 configs/experiments/ 下。
hypothesis: >-
  过去 60 个交易日的复权收益在合成样本内对随后短期收益存在持续性；
  仅验证离线 CLI 工程链路可复现并跑通全部运行状态，不构成投资建议。
factor_versions:
  momentum_60d: 1.0.0
dataset_version: CURRENT
universe_version: CURRENT
date_range:
  start_date: 2020-01-01
  end_date: 2021-12-31
train_validation_holdout_policy: not_applicable_engineering_mvp
preprocessing:
  winsorization: none
  standardization: none
portfolio_rule:
  name: top_n_equal_weight
  top_n: 10
  lot_size: 100
cost_scenarios:
  - zero_cost
  - commission_tax
  - full_cost
random_seed: 42
code_commit: unversioned
parent_experiment_ids: []
agent_id: null
"""

_CONFIG_NAMES = (
    "project.yml",
    "sources.yml",
    "costs.yml",
    "trading_rules.yml",
    "universe.yml",
)


@dataclass(frozen=True)
class FixtureProject:
    """One built synthetic project under a writable root."""

    root: Path
    version: str

    @property
    def experiment_spec(self) -> str:
        return FIXTURE_SPEC


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def build_fixture_project(root: Path, *, broken: bool = False) -> FixtureProject:
    """Materialise one full synthetic project under ``root``.

    Copies the repository ``configs/`` tree (project/sources/costs/rules/
    universe) into ``root/configs/``, authors a short experiment spec, then
    publishes a deterministic 5-table dataset over the repository's 30-symbol
    universe plus two benchmark indices, including one ``corporate_action_coverage``
    row per universe symbol over the whole fixture bars window so the default
    RESEARCH runs in the CLI / end-to-end suite pass the corporate-action trust
    gate.  ``broken=True`` keeps the (empty) facts table so an ENGINEERING
    diagnostic can still replay, but marks every coverage row UNTRUSTED
    (``SOURCE_FETCH_FAILED``): a RESEARCH run must fail the gate before any
    backtest while an ENGINEERING run may still complete as an UNTRUSTED
    diagnostic that is never accepted as a trusted performance claim.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    config_dir = root / "configs"
    experiments_dir = config_dir / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)
    for name in _CONFIG_NAMES:
        (config_dir / name).write_text(
            (_REPO_ROOT / "configs" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (experiments_dir / "momentum_60d.yml").write_text(
        _FIXTURE_SPEC_YAML, encoding="utf-8"
    )

    universe = Universe.from_yaml(config_dir / "universe.yml")
    sessions = _weekdays(BARS_START, BARS_END)
    tables = {
        "daily_bar": _bars(sessions, universe),
        "security_master": _security_master(universe),
        "corporate_action": _corporate_action(),
        "corporate_action_coverage": _coverage_table(universe, trusted=not broken),
        "trading_calendar": _trading_calendar(),
    }
    version = DatasetPublisher(root).publish(tables, QualityReport()).version
    return FixtureProject(root=root, version=version)


def _coverage_table(universe: Universe, *, trusted: bool) -> pd.DataFrame:
    """One deterministic coverage row per universe symbol over the full bars.

    The corporate_action facts table of this fixture is empty; that is only
    trusted when the evidence says so.  ``trusted=True`` publishes a
    ``VERIFIED_EMPTY`` row per symbol over ``BARS_START..BARS_END`` (both action
    endpoints succeeded and found nothing) so the RESEARCH gate accepts the
    dataset; ``trusted=False`` publishes ``UNTRUSTED``/``SOURCE_FETCH_FAILED``
    rows (the same empty facts are *not* trusted because the sources could not
    be checked), which the RESEARCH gate rejects.
    """
    status = CoverageStatus.VERIFIED_EMPTY if trusted else CoverageStatus.UNTRUSTED
    outcome = "success_empty" if trusted else "failed"
    reason = None if trusted else CoverageReason.SOURCE_FETCH_FAILED
    endpoints = ("cninfo_corporate_actions", "eastmoney_corporate_actions")
    records = [
        coverage_record(
            entry.symbol,
            BARS_START,
            BARS_END,
            status,
            reason,
            sources=[
                {"endpoint": endpoint, "outcome": outcome} for endpoint in endpoints
            ],
            checked_at=_INGESTED,
        )
        for entry in universe.entries
    ]
    return coverage_frame(records)


def _bars(sessions: list[date], universe: Universe) -> pd.DataFrame:
    """Deterministic daily bars: equities + benchmarks over every session."""
    n = len(sessions)
    frames: list[pd.DataFrame] = []
    for index, entry in enumerate(universe.entries, start=1):
        growth = 0.0001 * index
        closes = [
            _BASE_PRICE * math_exp(growth * (position - (n - 1)))
            for position in range(n)
        ]
        frames.append(
            _instrument_frame(
                sessions, entry.symbol, closes, source="tushare"
            )
        )
    for symbol, level in zip(_BENCHMARK_SYMBOLS, _BENCHMARK_LEVELS):
        frames.append(
            _instrument_frame(
                sessions, symbol, [level] * n, source="akshare"
            )
        )
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def _instrument_frame(
    sessions: list[date],
    symbol: str,
    closes: list[float],
    *,
    source: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(sessions),
            "symbol": symbol,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [0] * len(sessions),
            "amount": [0.0] * len(sessions),
            "adjustment": "unadjusted",
            "source": source,
            "ingested_at": [_INGESTED] * len(sessions),
        }
    )


def _security_master(universe: Universe) -> pd.DataFrame:
    entries = universe.entries
    n = len(entries)
    return pd.DataFrame(
        {
            "symbol": [entry.symbol for entry in entries],
            "name": [
                f"{entry.name_at_selection}_{entry.symbol}" for entry in entries
            ],
            "exchange": [entry.exchange for entry in entries],
            "board": [entry.board for entry in entries],
            "list_date": pd.to_datetime([LIST_DATE] * n),
            "delist_date": pd.Series(
                pd.NaT, index=range(n), dtype="datetime64[ns]"
            ),
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


def _trading_calendar() -> pd.DataFrame:
    days = _weekdays(CAL_START, CAL_END)
    return pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(days),
            "is_trading_day": [True] * len(days),
        }
    )[TRADING_CALENDAR_COLUMNS]


def math_exp(value: float) -> float:
    import math

    return math.exp(value)


@pytest.fixture
def cli_runner():
    """A Typer ``CliRunner`` used to invoke the CLI application in-process."""
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture(scope="session")
def fixture_root(tmp_path_factory) -> FixtureProject:
    """One shared synthetic project so the acceptance tests reuse one run."""
    return build_fixture_project(tmp_path_factory.mktemp("fixture_root"))


@pytest.fixture
def broken_fixture_root(tmp_path) -> FixtureProject:
    """A synthetic project whose corporate-action coverage evidence is
    UNTRUSTED (``SOURCE_FETCH_FAILED``) over an empty facts table."""
    return build_fixture_project(tmp_path / "broken", broken=True)
