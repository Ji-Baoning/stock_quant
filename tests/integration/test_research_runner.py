"""End-to-end research-run orchestration over a synthetic pinned dataset.

The runner must resolve the ``CURRENT`` dataset *once* before any factor
execution, publish an immutable 15-artifact experiment only after a complete,
evaluated run, keep failures auditable under ``data/runs`` without touching
``CURRENT`` and without creating a partial experiment directory, publish
REJECTED experiments as full results, and resume a re-run of the same
experiment id without recomputing any stage.

Task 4 (point-in-time universe): the formal ``momentum_60d`` spec names
``universe_definition: csi300`` with ``universe_version: CURRENT``, so every
run first preflights a frozen :class:`UniverseDefinition`
(``configs/universes/csi300.yml``) against the pinned dataset's
``universe_membership`` evidence through the mandatory acceptance gate --
before identity is computed and before any factor work.  A failing gate stops
the run at the distinct ``universe_acceptance`` stage with a redacted
preflight manifest and never produces factor artifacts.  A passing run freezes
``universe_version`` to the definition version and persists the definition
identity plus the per-signal-day member snapshot map into the run/experiment
manifests, metrics and config snapshot.

The real-data acceptance layer (plan 2026-09-08) pins the dataset-side
acceptance gate: a RESEARCH run over a project whose pinned dataset carries
no valid ACCEPTED record fails closed at the ``acceptance`` stage before any
factor work; an ENGINEERING diagnostic that explicitly pins a missing record
fails the same gate; and an accepted run freezes the concrete acceptance id
into the frozen spec, the experiment manifest and ``metrics.json``.  Trusted
fixture datasets are therefore published exactly the way the data pipeline
publishes an update -- deterministic raw snapshots under ``data/raw`` and the
sanitized ``build_config`` the pipeline itself writes -- and carry one fixed
ACCEPTED record produced by the real offline ``real-data-v1`` checker, so the
universe preflight and the acceptance gate are exercised together exactly
like an operator dataset would.  Broken/untrusted fixtures publish no
ACCEPTED record.

Every test publishes a deterministic synthetic market -- twelve SH main-board
equities with bars (whose closes follow ``55 * exp(growth * (session - (n-1)))``
plus two flat benchmark index rows), a security master that also carries the
300 synthetic ``csi300`` constituents (with bars over the bound data-update
window, so the acceptance checker's date-window completeness sees no
unexplained gaps), an evidence-backed ``universe_membership`` table whose 300
constituents include every traded equity (so the membership-first factor
filter leaves the tradable candidate pool intact), the immutable
``adjusted_bar`` total-return table and its corporate-action evidence --
under a temporary project root, then runs the repository's real
``momentum_60d`` spec from a temporary config tree whose ``csi300``
definition is real rather than the repository placeholder.  All fixtures are
offline; nothing here touches the network or a token, and no real market
data is committed.
"""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
import yaml
from conftest import (
    _UPDATE_WINDOW_END,
    _UPDATE_WINDOW_START,
    fixture_build_config,
    publish_fixture_acceptance,
)

from stock_quant.backtest.models import BUY
from stock_quant.data_model.adjusted_bar import build_adjusted_bars
from stock_quant.data_model.corporate_action_coverage import (
    OUTCOME_SUCCESS_EVENTS,
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    CORPORATE_ACTION_QUARANTINE_COLUMNS,
    DAILY_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
    UNIVERSE_MEMBERSHIP_COLUMNS,
)
from stock_quant.data_model.security_master import (
    MASTER_SOURCE_STOCK_BASIC,
    ListStatus,
    master_coverage_frame,
    master_coverage_record,
)
from stock_quant.data_model.trading_rules import REASON_SELL_AT_LOWER_LIMIT
from stock_quant.data_model.universe_membership import (
    membership_content_hash,
    membership_frame,
    resolve_memberships,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.factors.momentum import Momentum60
from stock_quant.research.acceptance.models import CURRENT_ACCEPTED
from stock_quant.research.acceptance.registry import AcceptanceRegistry
from stock_quant.research.models import (
    REQUIRED_ARTIFACTS,
    Evaluation,
    ExperimentEvaluation,
    ResearchRunFailed,
)
from stock_quant.research.runner import ResearchRunner, _DatasetFactorAdapter
from stock_quant.research.spec import ExperimentSpec
from stock_quant.research.trust import DataTrustMode
from stock_quant.research.universe import UniverseDefinition, UniverseResolver

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = "configs/experiments/momentum_60d.yml"
_CAL_START = date(2018, 1, 1)
_BARS_START = date(2019, 8, 1)
_BARS_END = date(2026, 9, 4)
_LIST_DATE = date(2018, 1, 2)
_BASE_PRICE = 55.0
_BENCHMARK_SYMBOLS = ("000300.SH", "000905.SH")

#: The frozen csi300 test definition: 300 synthetic constituents announced
#: before their intervals start and open-ended over the whole calendar.
_CSI300_SIZE = 300
_RULES_VERSION = "csi-index-rules-test-2026h2"
_EVIDENCE_SUMMARY = "cd" * 32
_FACTS_START = date(2018, 1, 2)
_FACTS_ANNOUNCED = date(2017, 12, 15)

#: Twelve SH main-board equities, growth ordered ascending so the weekly
#: top-10 portfolio is always the ten names with the largest growth.
EQUITY_GROWTH = (
    ("600000.SH", 0.00042),
    ("601398.SH", 0.00052),
    ("600028.SH", 0.00063),
    ("600036.SH", 0.00074),
    ("600030.SH", 0.00086),
    ("601166.SH", 0.00098),
    ("601088.SH", 0.00110),
    ("600519.SH", 0.00122),
    ("601919.SH", 0.00134),
    ("603288.SH", 0.00145),
    ("601318.SH", 0.00155),
    ("601857.SH", 0.00164),
)

_INGESTED = pd.Timestamp("2026-09-04T08:00:00Z")

#: The repository config files the offline acceptance checker reads from the
#: *project* root (``configs/`` in ``project_root``, not ``config_root``):
#: the checker re-runs the publication gate and the evidence checks against
#: the project's own configs, so the fixture writes a ``universe.yml`` listing
#: exactly the dataset's securities (the publication gate is FATAL on any
#: universe/master disagreement) and copies the remaining files.
_CONFIG_NAMES = (
    "project.yml",
    "sources.yml",
    "costs.yml",
    "trading_rules.yml",
)

#: Fixed ``selected_as_of`` for the generated fixture universe (inside the
#: synthetic calendar) so the written YAML is deterministic.
_UNIVERSE_AS_OF = date(2022, 1, 7)


def _ensure_fixture_configs(project_root: Path, master: pd.DataFrame) -> None:
    """Write the project configs the offline acceptance checker reads.

    The checker re-runs the publication gate and the semantic evidence checks
    against ``configs/`` in the *project* root, so the synthetic project
    carries a fixture-local ``universe.yml`` listing exactly the dataset's
    securities (the publication gate is FATAL on any universe/master
    disagreement); the remaining config files are copied from the repository.
    """
    config_dir = project_root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in _CONFIG_NAMES:
        target = config_dir / name
        if not target.is_file():
            target.write_text(
                (_REPO_ROOT / "templates" / "project-config" / name).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
    entries = [
        {
            "symbol": str(symbol),
            "name_at_selection": f"synth_{symbol}",
            "exchange": "SH",
            "board": "sh_main",
            "selected_as_of": _UNIVERSE_AS_OF,
            "boundary_tags": ["fixture"],
            "selection_reason": (
                "synthetic research-runner fixture sample; not a recommendation"
            ),
        }
        for symbol in sorted(str(value) for value in master["symbol"])
    ]
    document = {"selected_as_of": _UNIVERSE_AS_OF, "entries": entries}
    (config_dir / "universe.yml").write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    # The full-history criterion must be non-empty: an enabled universe
    # definition is what binds ``full_history_acceptance_start`` into the
    # build config, and the acceptance chain fails a manifest without one.
    (config_dir / "universes").mkdir(parents=True, exist_ok=True)
    (config_dir / "universes" / "csi300.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "universe_id": "csi300",
                "rules_version": "fixture-rules-v1",
                "membership_table_sha256": membership_content_hash(
                    _fact_payloads()
                ),
                "coverage_start": _CAL_START.isoformat(),
                "coverage_end": _BARS_END.isoformat(),
                "evidence_summary_sha256": "cd" * 32,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


@dataclass(frozen=True)
class _Env:
    """A temporary project with one published synthetic dataset version."""

    root: Path
    version: str
    config_root: Path
    facts: tuple[dict, ...]

    @property
    def definition(self) -> UniverseDefinition:
        """The frozen csi300 definition the temporary config tree pins."""
        document = yaml.safe_load(
            (self.config_root / "configs" / "universes" / "csi300.yml")
            .read_text(encoding="utf-8")
        )
        return UniverseDefinition.model_validate(document)


@dataclass(frozen=True)
class _AcceptedEnv(_Env):
    """A synthetic project whose pinned dataset carries a valid acceptance."""

    acceptance_id: str = ""


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _csi300_symbols(count: int = _CSI300_SIZE) -> list[str]:
    return [f"{600000 + offset}.SH" for offset in range(count)]


def _member_symbols(count: int = _CSI300_SIZE) -> list[str]:
    """``count`` csi300 constituents that always include the traded names.

    The factor/portfolio stages rank only securities with daily bars (the
    twelve traded equities), and Task 5 filters factor candidates to the
    signal-day members, so every traded name must be a point-in-time member
    for the synthetic market to exercise the full pipeline.  Filler
    constituents from the canonical ``600000..`` block make up the remaining
    slots; the result has exactly ``count`` symbols.
    """
    traded = [symbol for symbol, _ in EQUITY_GROWTH]
    filler = [
        symbol
        for symbol in _csi300_symbols(count + len(traded))
        if symbol not in set(traded)
    ]
    return sorted(filler[: count - len(traded)] + traded)


def _fact_payload(symbol: str) -> dict:
    """One evidence-backed open csi300 fact valid on every calendar day."""
    return {
        "universe_id": "csi300",
        "symbol": symbol,
        "raw_effective_from": _FACTS_START,
        "raw_effective_to": None,
        "announcement_date": _FACTS_ANNOUNCED,
        "status": "active",
        "reason": "initial_constituent",
        "source": "csi_index_announcement",
        "source_url": "https://www.csindex.com.cn/announcement-2017-12.pdf",
        "snapshot_sha256": "a1" * 32,
        "source_document_sha256": "b2" * 32,
    }


def _fact_payloads(count: int = _CSI300_SIZE) -> tuple[dict, ...]:
    return tuple(_fact_payload(symbol) for symbol in _member_symbols(count))

def _bars(
    sessions: list[date],
    *,
    index_close: tuple[float, float],
    limit_locked_symbols: tuple[str, ...] = (),
    fresh: tuple[str, date, float] | None = None,
) -> pd.DataFrame:
    """One deterministic daily_bar frame for all instruments over ``sessions``.

    ``limit_locked_symbols`` names equities whose bar on every Monday (the
    weekly execution day) opens at half the prior session's close, far below
    the main-board lower price limit.  Such names stay ranked by the factor
    (their signal-day closes are untouched) but every Monday *sell* of them is
    price-limit-blocked by the engine -- a rejection, not a hard error.
    ``fresh`` is ``(symbol, list_date, growth)`` for a recently-listed symbol
    whose bars begin at its list date (the new-IPO acceptance fixture).
    Equity rows carry a known supplier source so the publication gate's
    provenance rules accept them.
    """
    n = len(sessions)
    frames: list[pd.DataFrame] = []
    for symbol, growth in EQUITY_GROWTH:
        closes = [_BASE_PRICE * math.exp(growth * (index - (n - 1)))
                  for index in range(n)]
        if symbol in limit_locked_symbols:
            opens = list(closes)
            for index in range(1, n):
                if sessions[index].weekday() == 0:  # Monday == execution day
                    opens[index] = 0.5 * closes[index - 1]
            # Keep the bar OHLC-valid: the limit-locked Monday open sits far
            # below the close, so the low follows it down on those sessions.
            lows = [min(open_, close_) for open_, close_ in zip(opens, closes)]
            frames.append(_instrument_frame(
                sessions, symbol, closes, opens, lows=lows, source="tushare"
            ))
        else:
            frames.append(_instrument_frame(
                sessions, symbol, closes, source="tushare"
            ))
    if fresh is not None:
        symbol, list_date, growth = fresh
        start_at = next(
            index for index, day in enumerate(sessions) if day >= list_date
        )
        closes = [
            _BASE_PRICE * math.exp(growth * (index - (n - 1)))
            for index in range(start_at, n)
        ]
        frames.append(_instrument_frame(
            sessions[start_at:], symbol, closes, source="tushare"
        ))
    for symbol, level in zip(_BENCHMARK_SYMBOLS, index_close):
        frames.append(_instrument_frame(
            sessions, symbol, [level] * n, source="akshare"
        ))
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def _window_filler_bars(symbols: tuple[str, ...] | list[str]) -> pd.DataFrame:
    """Flat bars for the non-traded master symbols over the data-update window.

    The acceptance checker's ``date_window_completeness`` treats a *listed*
    symbol without a bar inside the bound update window as an unexplained
    suspension, so the csi300 filler constituents (which the membership
    cardinality requires in the security master) carry real bars over exactly
    that window.  Their flat closes never produce a valid momentum row (61
    observations are required), so the tradable candidate pool is unchanged.
    """
    sessions = _weekdays(_UPDATE_WINDOW_START, _UPDATE_WINDOW_END)
    frames = [
        _instrument_frame(sessions, symbol, [_BASE_PRICE] * len(sessions),
                          source="tushare")
        for symbol in symbols
    ]
    if not frames:
        return pd.DataFrame(columns=DAILY_COLUMNS)
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def _instrument_frame(
    sessions: list[date],
    symbol: str,
    closes: list[float],
    opens: list[float] | None = None,
    *,
    source: str,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    """One deterministic daily-bar frame (``source`` is a known supplier)."""
    n = len(sessions)
    opens = closes if opens is None else opens
    lows = closes if lows is None else lows
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(sessions),
            "symbol": symbol,
            "open": opens,
            "high": closes,
            "low": lows,
            "close": closes,
            "volume": [0] * n,
            "amount": [0.0] * n,
            "adjustment": "unadjusted",
            "source": source,
            "ingested_at": [_INGESTED] * n,
        }
    )


def _master_symbols(fresh: tuple[str, date, float] | None = None) -> list[str]:
    """Every security_master symbol: the traded names plus all constituents.

    The synthetic csi300 membership facts must intersect the master (the
    acceptance gate validates fact symbols against it), so the master carries
    the 300 constituents alongside the twelve tradable equities; only the
    tradable names (and the optional fresh listing) have bars over the full
    session range.
    """
    symbols = [symbol for symbol, _ in EQUITY_GROWTH]
    symbols += [symbol for symbol in _csi300_symbols()
                if symbol not in set(symbols)]
    if fresh is not None:
        symbols.append(fresh[0])
    return symbols


def _security_master(
    fresh: tuple[str, date, float] | None = None,
) -> pd.DataFrame:
    """One security_master row per traded/constituent symbol, plus ``fresh``.

    ``fresh`` is ``(symbol, list_date, growth)`` for the new-IPO acceptance
    fixture; every row keeps ``list_status="L"``.  ``name`` stays a synthetic
    label (universe.yml owns the real labels, as in production).
    """
    symbols = _master_symbols(fresh)
    list_dates = [_LIST_DATE] * len(symbols)
    if fresh is not None:
        list_dates[-1] = fresh[1]
    count = len(symbols)
    return pd.DataFrame(
        {
            "symbol": symbols,
            "name": [f"synth_{symbol}" for symbol in symbols],
            "exchange": ["SH"] * count,
            "board": ["sh_main"] * count,
            "list_date": pd.to_datetime(list_dates),
            "delist_date": pd.Series(
                pd.NaT, index=range(count), dtype="datetime64[ns]"
            ),
            "list_status": ["L"] * count,
        }
    )[SECURITY_MASTER_COLUMNS]


def _corporate_action() -> pd.DataFrame:
    frame = pd.DataFrame(
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
    )
    return frame[CORPORATE_ACTION_COLUMNS]


def _cash_action(symbol: str, ex_date: date) -> pd.DataFrame:
    """One verified, implemented 0.5/share cash dividend at ``ex_date``.

    Complete point-in-time facts (announcement two weeks before, record the
    day before, no rights issue), so the adjusted-bar builder accepts it and
    applies it exactly once on the ``ex_date``.
    """
    return pd.DataFrame(
        {
            "symbol": [symbol],
            "announcement_date": [ex_date - timedelta(days=14)],
            "record_date": [ex_date - timedelta(days=1)],
            "ex_date": [ex_date],
            "cash_dividend_per_share": [0.5],
            "bonus_share_ratio": [0.0],
            "capitalization_ratio": [0.0],
            "rights_issue_ratio": [0.0],
            "rights_issue_price": [0.0],
            "source": ["synthetic"],
            "status": ["implemented"],
        }
    )[CORPORATE_ACTION_COLUMNS]


def _trading_calendar() -> pd.DataFrame:
    days = _weekdays(_CAL_START, _BARS_END)
    return pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(days),
            "is_trading_day": [True] * len(days),
        }
    )[TRADING_CALENDAR_COLUMNS]


def _universe_symbols() -> tuple[str, ...]:
    return tuple(symbol for symbol, _ in EQUITY_GROWTH)


def _coverage(
    symbols: tuple[str, ...],
    *,
    status: CoverageStatus,
    reason: CoverageReason | None = None,
    window_start: date = _CAL_START,
    window_end: date = _BARS_END,
    event_symbols: tuple[str, ...] = (),
) -> pd.DataFrame:
    """One deterministic coverage row per symbol over a common window.

    The default window is the full synthetic calendar, a superset of the
    execution window any momentum spec derives, so a single row per symbol is
    always enough evidence when the status is trusted.  ``event_symbols``
    names holdings whose evidence is ``VERIFIED`` with ``success_with_events``
    endpoints (facts exist and were reconciled) instead of the plain status,
    so a dataset carrying corporate-action facts never claims an
    empty-verified provenance for them.
    """
    endpoints = ("cninfo_corporate_actions", "eastmoney_corporate_actions")
    records = []
    for symbol in sorted(symbols):
        if symbol in event_symbols:
            records.append(
                coverage_record(
                    symbol,
                    window_start,
                    window_end,
                    CoverageStatus.VERIFIED,
                    None,
                    sources=[
                        {"endpoint": endpoint, "outcome": OUTCOME_SUCCESS_EVENTS}
                        for endpoint in endpoints
                    ],
                    checked_at=_INGESTED,
                )
            )
        else:
            records.append(
                coverage_record(
                    symbol,
                    window_start,
                    window_end,
                    status,
                    reason,
                    checked_at=_INGESTED,
                )
            )
    return coverage_frame(records)


def _master_coverage(master: pd.DataFrame) -> pd.DataFrame:
    """One deterministic security_master_coverage row per master row.

    Each coverage row carries the master's own listing facts, so the coverage
    evidence always agrees with the master (a freshly-listed fixture symbol
    keeps its real late ``list_date``).
    """
    records = [
        master_coverage_record(
            str(row["symbol"]),
            list_date=pd.Timestamp(row["list_date"]).date(),
            list_status=ListStatus.L,
            source=MASTER_SOURCE_STOCK_BASIC,
            snapshot_sha256="f" * 64,
            sdk_version="fixture",
            checked_at=_INGESTED,
        )
        for row in master.to_dict("records")
    ]
    return master_coverage_frame(records)

def _publish_synthetic_dataset(
    project_root: Path,
    *,
    index_close: tuple[float, float] = (4000.0, 2000.0),
    limit_locked_symbols: tuple[str, ...] = (),
    coverage: pd.DataFrame | None = None,
    master_coverage: pd.DataFrame | None = None,
    fresh: tuple[str, date, float] | None = None,
    corporate_actions: pd.DataFrame | None = None,
    membership_facts: tuple[dict, ...] | None = None,
    with_membership: bool = True,
    accept: bool = True,
) -> str:
    """Publish the synthetic market under ``project_root``; return its version.

    The default dataset carries an explicit ``VERIFIED_EMPTY`` corporate-action
    coverage row per master symbol AND a ``security_master_coverage`` row per
    master symbol (both evidence tables), so the ResearchRunner's RESEARCH
    gates pass over the default market.  It also carries the immutable
    ``universe_membership`` table with the 300 evidenced csi300 facts the
    temporary definition pins, the ``adjusted_bar`` total-return table built
    by the production builder, and the ``corporate_action_quarantine`` table.
    ``corporate_actions`` overrides the (empty) facts table, e.g. with
    ``_cash_action`` for a total-return market; its coverage evidence must
    then name the affected symbols (see ``_coverage`` ``event_symbols``).
    Trusted datasets are published exactly the way the data pipeline publishes
    an update: deterministic raw snapshots are saved through ``RawStore`` and
    the sanitized ``build_config`` binds them with ``origin=data_update`` and
    healthy required-source statuses.  With ``accept=True`` (the default) the
    real offline checker must pass every ``real-data-v1`` check and one fixed
    ACCEPTED record is published for the version, so formal RESEARCH runs pass
    the acceptance gate; datasets whose evidence is intentionally broken pass
    ``accept=False`` -- they publish no acceptance, so a RESEARCH run over them
    must fail the gate.
    """
    master = _security_master(fresh)
    _ensure_fixture_configs(project_root, master)
    symbols = tuple(master["symbol"])
    if coverage is None:
        coverage = _coverage(symbols, status=CoverageStatus.VERIFIED_EMPTY)
    if master_coverage is None:
        master_coverage = _master_coverage(master)
    facts = _fact_payloads() if membership_facts is None else membership_facts
    traded = [symbol for symbol, _ in EQUITY_GROWTH]
    filler_symbols = tuple(symbol for symbol in symbols if symbol not in set(traded))
    daily = pd.concat(
        [
            _bars(
                _weekdays(_BARS_START, _BARS_END),
                index_close=index_close,
                limit_locked_symbols=limit_locked_symbols,
                fresh=fresh,
            ),
            _window_filler_bars(filler_symbols),
        ],
        ignore_index=True,
    )[DAILY_COLUMNS]
    corporate_actions = (
        _corporate_action() if corporate_actions is None else corporate_actions
    )
    empty_quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
    tables = {
        "daily_bar": daily,
        "adjusted_bar": build_adjusted_bars(
            daily,
            corporate_actions,
            empty_quarantine,
            coverage,
            symbols=symbols,
        ),
        "security_master": master,
        "security_master_coverage": master_coverage,
        "corporate_action": corporate_actions,
        "corporate_action_quarantine": empty_quarantine,
        "corporate_action_coverage": coverage,
        "trading_calendar": _trading_calendar(),
    }
    if with_membership:
        tables["universe_membership"] = membership_frame(list(facts))[
            UNIVERSE_MEMBERSHIP_COLUMNS
        ]
    version = DatasetPublisher(project_root).publish(
        tables,
        QualityReport(),
        # The relay span must cover this dataset's own published calendar
        # (wider than the conftest fixture calendar): ``data validate``
        # reports any manifest whose calendar coverage leaves a hole.
        build_config=fixture_build_config(
            project_root, calendar_start=_CAL_START, calendar_end=_BARS_END
        ),
    ).version
    if accept:
        publish_fixture_acceptance(project_root, version)
    return version


def _write_config_tree(
    root: Path, *, facts: tuple[dict, ...] | None = None
) -> Path:
    """A temporary config tree whose csi300 definition is real, not a template.

    Copies the repository config files (project/sources/costs/rules/universe)
    and the formal momentum spec, then writes ``configs/universes/csi300.yml``
    pinned to the canonical synthetic facts (``facts`` overrides them, e.g. to
    swap a fresh listing in as a member) and the dataset's actual covered
    window.  Tests never use the repository's placeholder definition template.
    """
    config_root = root / "config_root"
    configs = config_root / "configs"
    (configs / "universes").mkdir(parents=True, exist_ok=True)
    (configs / "experiments").mkdir(parents=True, exist_ok=True)
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
        "corporate_action_reviews.yml",
    ):
        source = _REPO_ROOT / "templates" / "project-config" / name
        if source.is_file():
            shutil.copyfile(source, configs / name)
    shutil.copyfile(
        _REPO_ROOT / "templates" / "project-config" / "experiments" / "momentum_60d.yml",
        configs / "experiments" / "momentum_60d.yml",
    )
    # The repository example spec declares the formal walk-forward policy and
    # the buffered risk-weighted rule; these single-window pipeline tests pin
    # the legacy engineering policy explicitly (their 2020 start cannot satisfy
    # the 756-session fold-2020 warmup floor of the fixed-calendar contract
    # anyway) and pin the equal-weight rule the engineering pipeline executes.
    spec_document = yaml.safe_load(
        (configs / "experiments" / "momentum_60d.yml").read_text(encoding="utf-8")
    )
    spec_document["execution_pipeline"] = "engineering_single_window"
    spec_document["portfolio_rule"] = {
        "name": "top_n_equal_weight",
        "top_n": 10,
        "lot_size": 100,
    }
    (configs / "experiments" / "momentum_60d.yml").write_text(
        yaml.safe_dump(spec_document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    fact_payloads = _fact_payloads() if facts is None else facts
    document = {
        "schema_version": 1,
        "universe_id": "csi300",
        "rules_version": _RULES_VERSION,
        "membership_table_sha256": membership_content_hash(list(fact_payloads)),
        "coverage_start": _FACTS_START.isoformat(),
        "coverage_end": _BARS_END.isoformat(),
        "evidence_summary_sha256": _EVIDENCE_SUMMARY,
    }
    (configs / "universes" / "csi300.yml").write_text(
        yaml.safe_dump(document, sort_keys=True), encoding="utf-8"
    )
    return config_root


def _publish_dataset_with_untrusted_coverage(project_root: Path) -> str:
    """Republish CURRENT so every holding's coverage evidence is UNTRUSTED.

    The untrusted coverage fails the real ``corporate_action_evidence`` check,
    so this dataset publishes no ACCEPTED record: a RESEARCH run over it must
    fail the acceptance gate before any corporate-action or factor work.
    """
    return _publish_synthetic_dataset(
        project_root,
        coverage=_coverage(
            _master_symbols(),
            status=CoverageStatus.UNTRUSTED,
            reason=CoverageReason.SOURCE_FETCH_FAILED,
        ),
        accept=False,
    )


def _publish_dataset_with_verified_empty_coverage(project_root: Path) -> str:
    """Republish CURRENT so every holding's coverage is explicitly VERIFIED_EMPTY."""
    return _publish_synthetic_dataset(
        project_root,
        coverage=_coverage(
            _master_symbols(), status=CoverageStatus.VERIFIED_EMPTY
        ),
    )


def _publish_dataset_without_master_evidence(project_root: Path) -> str:
    """Republish CURRENT with an empty security_master_coverage table.

    The missing master evidence fails the real ``security_master_evidence``
    check, so this dataset publishes no ACCEPTED record: a RESEARCH run over
    it must fail the acceptance gate before any factor work.
    """
    return _publish_synthetic_dataset(
        project_root,
        master_coverage=master_coverage_frame([]),
        accept=False,
    )


def _publish_legacy_dataset(project_root: Path) -> str:
    """Republish CURRENT in the pre-adjusted_bar legacy shape (with membership).

    The dataset keeps its pre-provenance shape (no ``build_config`` evidence,
    no ``adjusted_bar``) and publishes no acceptance record, so a RESEARCH run
    over it fails the acceptance gate; the membership table is present so the
    universe preflight passes and the acceptance gate is what stops the run.
    The missing-``adjusted_bar`` no-fallback contract of the factor adapter is
    exercised by an ENGINEERING diagnostic instead.
    """
    master = _security_master()
    _ensure_fixture_configs(project_root, master)
    symbols = tuple(master["symbol"])
    traded = [symbol for symbol, _ in EQUITY_GROWTH]
    filler_symbols = tuple(symbol for symbol in symbols if symbol not in set(traded))
    tables = {
        "daily_bar": pd.concat(
            [
                _bars(
                    _weekdays(_BARS_START, _BARS_END),
                    index_close=(4000.0, 2000.0),
                ),
                _window_filler_bars(filler_symbols),
            ],
            ignore_index=True,
        )[DAILY_COLUMNS],
        "security_master": master,
        "security_master_coverage": _master_coverage(master),
        "corporate_action": _corporate_action(),
        "corporate_action_coverage": _coverage(
            symbols, status=CoverageStatus.VERIFIED_EMPTY
        ),
        "trading_calendar": _trading_calendar(),
        "universe_membership": membership_frame(list(_fact_payloads()))[
            UNIVERSE_MEMBERSHIP_COLUMNS
        ],
    }
    return DatasetPublisher(project_root).publish(tables, QualityReport()).version


def _new_env(tmp_path) -> _Env:
    project_root = tmp_path / "project"
    version = _publish_synthetic_dataset(project_root)
    return _Env(
        root=project_root,
        version=version,
        config_root=_write_config_tree(tmp_path),
        facts=_fact_payloads(),
    )


# --------------------------------------------------------------------------- #
# Task: formal walk-forward publication (audit chain + snapshot boundary)
# --------------------------------------------------------------------------- #


@pytest.fixture
def walk_forward_spec(env: _Env) -> str:
    """A formal walk-forward spec over the synthetic csi300 project.

    The requested OOS range is the complete 2021 calendar year: its
    2018-2020 warmup carries 784 confirmed weekday sessions (above the
    756-session policy floor) and the pinned bars cover the whole fold.
    """
    path = (
        env.config_root / "configs" / "experiments" / "walk_forward_2021.yml"
    )
    path.write_text(
        yaml.safe_dump(
            {
                "hypothesis": "formal walk-forward stability over fold 2021",
                "execution_pipeline": "walk_forward_oos_v1",
                "factor_versions": {"momentum_60d": "2.0.0"},
                "dataset_version": "CURRENT",
                "universe_version": "CURRENT",
                "universe_definition": "csi300",
                "data_acceptance_id": "CURRENT_ACCEPTED",
                "date_range": {
                    "start_date": "2021-01-01",
                    "end_date": "2021-12-31",
                },
                "train_validation_holdout_policy":
                    "not_applicable_engineering_mvp",
                "preprocessing": {
                    "winsorization": "none",
                    "standardization": "none",
                },
                "portfolio_rule": {
                    "name": "top_n_equal_weight",
                    "top_n": 10,
                    "lot_size": 100,
                },
                "cost_scenarios": ["zero_cost", "full_cost"],
                "random_seed": 42,
                "code_commit": "unversioned",
                "parent_experiment_ids": [],
                "agent_id": None,
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return "configs/experiments/walk_forward_2021.yml"


@pytest.fixture
def snapshot_payload() -> dict:
    from stock_quant.research.walk_forward.snapshots import build_snapshot_bundle

    bundle = build_snapshot_bundle(
        spec=ExperimentSpec.model_validate(
            {
                "hypothesis": "snapshot payload boundary",
                "factor_versions": {"momentum_60d": "2.0.0"},
                "dataset_version": "d" * 64,
                "universe_version": "u" * 64,
                "data_acceptance_id": "a" * 64,
                "date_range": {
                    "start_date": "2021-01-01",
                    "end_date": "2021-12-31",
                },
                "train_validation_holdout_policy":
                    "not_applicable_engineering_mvp",
                "preprocessing": {
                    "winsorization": "none",
                    "standardization": "none",
                },
                "portfolio_rule": {
                    "name": "top_n_equal_weight",
                    "top_n": 10,
                    "lot_size": 100,
                },
                "cost_scenarios": ["full_cost"],
                "random_seed": 42,
                "code_commit": "unversioned",
                "parent_experiment_ids": [],
                "agent_id": None,
            }
        ),
        dataset_manifest={
            "tables": {
                "adjusted_bar": {"sha256": "1" * 64},
                "daily_bar": {"sha256": "2" * 64},
                "trading_calendar": {"sha256": "3" * 64},
                "corporate_action": {"sha256": "4" * 64},
                "corporate_action_coverage": {"sha256": "5" * 64},
            }
        },
        universe_definition=None,
        config_hashes={"costs.yml": "c" * 64},
    )
    return bundle.model_dump(mode="json")


def test_formal_walk_forward_publishes_complete_audit_chain(
    runner, walk_forward_spec, env, tmp_path
):
    published = runner.run(walk_forward_spec)
    root = published.path
    assert (root / "fold_schedule.json").is_file()
    assert (root / "fold_outcomes.json").is_file()
    assert (root / "walk_forward_manifest.json").is_file()
    for fold_dir in sorted((root / "folds").iterdir()):
        for name in (
            "fold_manifest.json",
            "signals.parquet",
            "orders.parquet",
            "fills.parquet",
            "equity.parquet",
            "daily_returns.parquet",
            "metrics.json",
        ):
            assert (fold_dir / name).is_file(), name
    report = json.loads((root / "stability_report.json").read_text())
    assert report["stability_policy_hash"]
    assert "aggregate_max_drawdown" not in report
    assert report["stability_conclusion"] == "INCONCLUSIVE"
    assert report["research_status"] == "COMPLETED"
    # the published experiment manifest binds the two immutable hashes
    manifest = json.loads(
        (root / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    assert (
        manifest["fold_schedule_sha256"]
        == report["schedule"]["fold_schedule_sha256"]
    )
    # the rich report renders the complete walk-forward audit section
    from stock_quant.cli import _experiment_report_input
    from stock_quant.reporting.html import render_experiment_report

    run_input = _experiment_report_input(env.root, published.experiment_id)
    destination = tmp_path / "walk_forward.html"
    render_experiment_report(run_input, destination)
    html = destination.read_text(encoding="utf-8")
    schedule = json.loads((root / "fold_schedule.json").read_text())
    for fold in schedule["folds"]:
        assert fold["fold_id"] in html
    for scenario in ("zero_cost", "full_cost"):
        assert scenario in html
    assert "stability_policy_hash" in html
    assert "oos_return_observations" in html
    assert "per_fold_max_drawdown" in html
    assert "aggregate_max_drawdown" not in html
    assert "全局最大回撤" not in html


def test_runtime_metadata_is_not_accepted_as_snapshot_content(snapshot_payload):
    from pydantic import ValidationError

    from stock_quant.research.walk_forward.snapshots import SnapshotBundle

    snapshot_payload["worker_count"] = 4
    with pytest.raises(ValidationError, match="extra"):
        SnapshotBundle.model_validate(snapshot_payload)


# --------------------------------------------------------------------------- #
# Injected observers / evaluators / providers
# --------------------------------------------------------------------------- #


def _raise_on(stage: str):
    def observer(current_stage, _state):
        if current_stage == stage:
            raise RuntimeError(f"injected failure at stage {stage!r}")
    return observer


class _CurrentSwitcher:
    """Publishes a different dataset version when the run reaches ``pin``."""

    def __init__(self, project_root: Path, original_version: str) -> None:
        self.project_root = project_root
        self.original_version = original_version
        self.flipped_version: str | None = None

    def __call__(self, stage, _state) -> None:
        if stage == "pin":
            self.flipped_version = _publish_synthetic_dataset(
                self.project_root, index_close=(4001.0, 2000.0)
            )


class _RejectEvaluator:
    """Always rejects (published REJECTED experiment with a reason)."""

    def evaluate(self, metrics: dict[str, object]) -> Evaluation:
        del metrics
        return Evaluation(
            ExperimentEvaluation.REJECTED, "below_qualifying_names"
        )


class _CountingFactorProvider:
    """A real Momentum60 provider that counts its invocations."""

    def __init__(self) -> None:
        self.calls = 0

    def provide(self) -> dict[str, Momentum60]:
        self.calls += 1
        factor = Momentum60()
        return {factor.name: factor}


# --------------------------------------------------------------------------- #
# Shared fixtures (one published project per test)
# --------------------------------------------------------------------------- #


@pytest.fixture
def env(tmp_path) -> _Env:
    return _new_env(tmp_path)


@pytest.fixture
def runner(env: _Env) -> ResearchRunner:
    return ResearchRunner(env.root, config_root=env.config_root)


@pytest.fixture
def current_switcher(env: _Env) -> _CurrentSwitcher:
    return _CurrentSwitcher(env.root, env.version)


@pytest.fixture
def failing_runner(env: _Env) -> ResearchRunner:
    return ResearchRunner(env.root, config_root=env.config_root)



# --------------------------------------------------------------------------- #
# The brief's behaviour tests
# --------------------------------------------------------------------------- #


def test_runner_freezes_current_before_factor_execution(runner, current_switcher):
    experiment = runner.run(_SPEC, stage_observer=current_switcher)
    assert current_switcher.flipped_version is not None
    assert current_switcher.flipped_version != current_switcher.original_version
    assert experiment.manifest.dataset_version == current_switcher.original_version
    spec_text = (experiment.path / "experiment_spec.yml").read_text()
    assert "CURRENT" not in spec_text
    assert current_switcher.original_version in spec_text


def test_runner_publishes_complete_artifact_contract(runner):
    experiment = runner.run(_SPEC)
    assert set(p.name for p in experiment.path.iterdir()) == REQUIRED_ARTIFACTS
    assert experiment.manifest.status in ("ACCEPTED", "REJECTED")
    assert runner.latest_run_manifest().status == "COMPLETED"
    assert (experiment.path.parent / "registry.parquet").is_file()


def test_failure_keeps_previous_current_and_auditable_run(failing_runner, env):
    with pytest.raises(ResearchRunFailed):
        failing_runner.run(_SPEC, stage_observer=_raise_on("pin"))
    assert failing_runner.latest_run_manifest().status == "FAILED"
    assert failing_runner.latest_run_manifest().failed_stage == "pin"
    assert failing_runner.latest_run_manifest().error is not None
    assert DatasetPublisher(env.root).current().version == env.version
    assert not failing_runner.partial_experiment_exists()


def test_rejected_experiment_still_publishes_full_artifact_set(env):
    evaluator = _RejectEvaluator()
    rejected_runner = ResearchRunner(
        env.root, config_root=env.config_root, evaluator=evaluator
    )
    experiment = rejected_runner.run(_SPEC)
    assert experiment.manifest.status == "REJECTED"
    assert experiment.manifest.evaluation_reason == "below_qualifying_names"
    assert set(p.name for p in experiment.path.iterdir()) == REQUIRED_ARTIFACTS


def test_rerun_reuses_same_experiment_and_skips_factor(tmp_path):
    env = _new_env(tmp_path)
    provider = _CountingFactorProvider()
    first_runner = ResearchRunner(
        env.root, config_root=env.config_root, factor_provider=provider.provide
    )
    first = first_runner.run(_SPEC)
    assert provider.calls == 1

    fresh_runner = ResearchRunner(
        env.root, config_root=env.config_root, factor_provider=provider.provide
    )
    second = fresh_runner.run(_SPEC)
    assert provider.calls == 1  # factor stage resumed, not recomputed
    assert second.path == first.path
    assert fresh_runner.latest_run_manifest().status == "COMPLETED"
    assert set(p.name for p in second.path.iterdir()) == REQUIRED_ARTIFACTS


def test_published_metrics_record_execution_rejected_orders(tmp_path):
    # 601318.SH stays ranked in the weekly top-10 (its signal-day closes are
    # untouched) but its Monday execution-day bar opens below the lower price
    # limit.  Under account-aware rebalancing each scenario trims it whenever
    # its realized holding exceeds the frozen target, and every such Monday
    # sell is executor-rejected: the divergence is an auditable rejection with
    # the lower-limit reason present in both the flow-level and the order-level
    # reason maps.  Whether/how much a scenario trims depends on that
    # scenario's realized path, so the assertion is reason-existence, not an
    # exact submission ledger.
    symbol = "601318.SH"
    project_root = tmp_path / "project"
    _publish_synthetic_dataset(project_root, limit_locked_symbols=(symbol,))
    runner = ResearchRunner(project_root, config_root=_write_config_tree(tmp_path))
    experiment = runner.run(_SPEC)
    assert experiment.manifest.status in ("ACCEPTED", "REJECTED")
    metrics = json.loads(
        (experiment.path / "metrics.json").read_text(encoding="utf-8")
    )
    scenarios = metrics["scenarios"]
    assert isinstance(scenarios, dict) and scenarios
    for summary in scenarios.values():
        # The pre-trade concepts are gone; the divergence is a rejection.
        assert "n_pretrade_adjustments" not in summary
        assert "pretrade_adjustments_by_reason" not in summary
        assert int(summary["n_rejections"]) > 0
        assert REASON_SELL_AT_LOWER_LIMIT in summary["rejections_by_reason"]
        assert REASON_SELL_AT_LOWER_LIMIT in summary["unfilled_reason_counts"]
        assert sum(
            int(count) for count in summary["unfilled_reason_counts"].values()
        ) > 0



def test_research_rejects_untrusted_but_engineering_is_untrusted(env):
    """Untrusted corporate-action evidence cannot earn an ACCEPTED record, so
    the formal research run fails at the acceptance gate (before any
    corporate-action or factor work); the ENGINEERING diagnostic still replays
    the full pipeline and is stamped UNTRUSTED."""
    _publish_dataset_with_untrusted_coverage(env.root)
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(
        ResearchRunFailed, match="no valid real-data-v1 acceptance"
    ):
        runner.run(_SPEC)
    debug = runner.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)
    assert json.loads(
        (debug.path / "metrics.json").read_text(encoding="utf-8")
    )["evaluation"]["status"] == "UNTRUSTED"


def test_research_accepts_verified_empty_coverage(env):
    _publish_dataset_with_verified_empty_coverage(env.root)
    assert ResearchRunner(
        env.root, config_root=env.config_root
    ).run(_SPEC).manifest.status in ("ACCEPTED", "REJECTED")


def test_engineering_never_publishes_accepted_even_with_trusted_evidence(env):
    # Mode-level trust ruling: ENGINEERING is diagnostic-only, so even fully
    # trusted coverage never yields an ACCEPTED experiment (formal acceptance
    # is reserved for RESEARCH); the evaluation is stamped UNTRUSTED and the
    # manifest REJECTED with a mode reason naming the diagnostic-only rule.
    _publish_dataset_with_verified_empty_coverage(env.root)
    runner = ResearchRunner(env.root, config_root=env.config_root)
    debug = runner.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)
    metrics = json.loads(
        (debug.path / "metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["corporate_action_trust"]["trusted"] is True
    assert metrics["evaluation"]["status"] == "UNTRUSTED"
    assert "diagnostic-only" in metrics["evaluation"]["reason"]
    assert "never publishes an ACCEPTED" in metrics["evaluation"]["reason"]
    assert debug.manifest.status == "REJECTED"



# --------------------------------------------------------------------------- #
# The real-data acceptance gate (plan Task 6 Steps 4-6)
# --------------------------------------------------------------------------- #


@pytest.fixture
def accepted_project(tmp_path) -> _AcceptedEnv:
    """A project whose CURRENT dataset passed every real-data-v1 check and
    whose registry holds one fixed ACCEPTED record for it."""
    root = tmp_path / "project"
    version = _publish_synthetic_dataset(root)
    selected = AcceptanceRegistry(root).select(version, CURRENT_ACCEPTED)
    return _AcceptedEnv(
        root=root,
        version=version,
        config_root=_write_config_tree(tmp_path),
        facts=_fact_payloads(),
        acceptance_id=selected.acceptance_id,
    )


@pytest.fixture
def unaccepted_project(tmp_path) -> _Env:
    """A healthy-trust project whose registry holds no acceptance record."""
    root = tmp_path / "project"
    version = _publish_synthetic_dataset(root, accept=False)
    return _Env(
        root=root,
        version=version,
        config_root=_write_config_tree(tmp_path),
        facts=_fact_payloads(),
    )


def test_research_without_acceptance_fails_before_factor(unaccepted_project):
    """A RESEARCH run over a project without an ACCEPTED record fails closed.

    The universe preflight passes first (the membership evidence is intact),
    then the acceptance gate stops the run before any run workspace, factor
    provider, portfolio builder or backtest engine work and persists a FAILED
    preflight manifest whose experiment_id is still ``None`` because no
    experiment identity can exist without a pinned acceptance.
    """
    provider = _CountingFactorProvider()
    runner = ResearchRunner(
        unaccepted_project.root,
        config_root=unaccepted_project.config_root,
        factor_provider=provider.provide,
    )
    with pytest.raises(
        ResearchRunFailed, match="no valid real-data-v1 acceptance"
    ):
        runner.run(_SPEC)
    assert provider.calls == 0
    state = runner.latest_run_manifest()
    assert state.experiment_id is None
    assert state.failed_stage == "acceptance"
    assert state.status == "FAILED"
    assert state.trust_mode == "research"
    assert state.run_id.startswith("preflight_acceptance_")


def test_engineering_preflight_stamps_resolved_trust_mode(unaccepted_project):
    """An ENGINEERING run that explicitly pins a concrete-but-missing
    acceptance id still fails the gate, and its preflight manifest stamps the
    resolved engineering mode instead of the RunState ``research`` default."""
    spec_text = (
        unaccepted_project.config_root / _SPEC
    ).read_text(encoding="utf-8")
    assert "data_acceptance_id: CURRENT_ACCEPTED" in spec_text
    spec_dir = unaccepted_project.root / "configs" / "experiments"
    spec_dir.mkdir(parents=True, exist_ok=True)
    pinned = spec_dir / "pinned_missing_acceptance.yml"
    pinned.write_text(
        spec_text.replace(
            "data_acceptance_id: CURRENT_ACCEPTED",
            f"data_acceptance_id: {'e' * 64}",
        ),
        encoding="utf-8",
    )
    runner = ResearchRunner(
        unaccepted_project.root, config_root=unaccepted_project.config_root
    )
    with pytest.raises(
        ResearchRunFailed, match="no valid real-data-v1 acceptance"
    ):
        runner.run(pinned, trust_mode=DataTrustMode.ENGINEERING)
    state = runner.latest_run_manifest()
    assert state.experiment_id is None
    assert state.failed_stage == "acceptance"
    assert state.trust_mode == "engineering"
    assert state.run_id.startswith("preflight_acceptance_")


def test_research_pins_accepted_identity(accepted_project):
    """The frozen spec stored with the published experiment carries the
    concrete resolved acceptance id, never the CURRENT_ACCEPTED placeholder."""
    published = ResearchRunner(
        accepted_project.root, config_root=accepted_project.config_root
    ).run(_SPEC)
    frozen = yaml.safe_load(
        (published.path / "experiment_spec.yml").read_text(encoding="utf-8")
    )
    assert frozen["data_acceptance_id"] == accepted_project.acceptance_id
    assert frozen["data_acceptance_id"] != "CURRENT_ACCEPTED"
    assert published.manifest.data_acceptance_id == (
        accepted_project.acceptance_id
    )
    metrics = json.loads(
        (published.path / "metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["data_acceptance"]["acceptance_id"] == (
        accepted_project.acceptance_id
    )
    assert metrics["data_acceptance"]["decision"] == "ACCEPTED"


def test_research_factor_input_uses_adjusted_bar(env):
    """The factor adapter's input comes from the pinned adjusted_bar table."""
    with DatasetReader(env.root).open(env.version) as context:
        adjusted = context.read("adjusted_bar")
        adapter = _DatasetFactorAdapter(
            context=context,
            universe_symbols=tuple(sorted(adjusted["symbol"].unique())),
        )
        factor_input = adapter.factor_input()
    assert set(factor_input["adjustment"]) == {"internal_total_return_v1"}
    expected = adjusted.sort_values(
        ["symbol", "trade_date"], kind="stable"
    ).reset_index(drop=True)
    assert factor_input["adjusted_close"].tolist() == (
        expected["adjusted_close"].tolist()
    )


def test_research_rejects_dataset_without_adjusted_bar(env):
    """A legacy dataset fails closed: it has no build evidence, so a RESEARCH
    run cannot even accept it, and an ENGINEERING diagnostic that does reach
    the factor adapter never falls back to an unadjusted close series."""
    _publish_legacy_dataset(env.root)
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(
        ResearchRunFailed, match="no valid real-data-v1 acceptance"
    ):
        runner.run(_SPEC)
    debug = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(ResearchRunFailed, match="adjusted_bar"):
        debug.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)


def test_research_rejects_spec_requesting_retired_factor_version(env, tmp_path):
    """A spec pinning momentum_60d/1.0.0 is rejected, never silently upgraded.

    v2 consumes only the internal total-return basis, so a request for the
    retired v1 must fail the provider's version check instead of recomputing
    v1 values from unadjusted closes.
    """
    spec_text = (env.config_root / _SPEC).read_text(encoding="utf-8")
    assert "momentum_60d: 2.0.0" in spec_text
    spec_path = tmp_path / "momentum_60d_v1.yml"
    spec_path.write_text(
        spec_text.replace("momentum_60d: 2.0.0", "momentum_60d: 1.0.0"),
        encoding="utf-8",
    )
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(ResearchRunFailed, match="1.0.0"):
        runner.run(spec_path)


def test_metrics_records_total_return_input_audit(env):
    """metrics.json carries a deterministic factor-input provenance audit.

    The audit must describe the pinned dataset version the run actually used:
    the adjustment basis, the requested factor versions, the adjusted-bar rows
    the universe consumed, and the ERROR break / invalid-reason counts (zero
    for the trusted synthetic fixture).
    """
    experiment = ResearchRunner(
        env.root, config_root=env.config_root
    ).run(_SPEC)
    metrics = json.loads(
        (experiment.path / "metrics.json").read_text(encoding="utf-8")
    )
    audit = metrics["factor_input"]
    assert audit["adjustment"] == "internal_total_return_v1"
    assert audit["factor_versions"] == {"momentum_60d": "2.0.0"}
    assert audit["row_count"] > 0
    assert audit["error_break_count"] == 0
    assert audit["invalid_reason_counts"] == {}


def test_run_report_shows_factor_price_basis(env):
    """The direct ``research run`` report exposes the same adjustment basis and
    break count the richer rebuilt report renders from metrics.json."""
    experiment = ResearchRunner(
        env.root, config_root=env.config_root
    ).run(_SPEC)
    html = (experiment.path / "report.html").read_text(encoding="utf-8")
    assert "因子价格口径" in html
    assert "调整方法 internal_total_return_v1" in html
    assert "因子版本 momentum_60d: 2.0.0" in html
    # Pinned values: every adjusted-bar row of the master symbols over the
    # published session range (traded equities) plus the data-update window
    # (the filler constituents), and zero trusted-evidence breaks.
    full_sessions = len(_weekdays(_BARS_START, _BARS_END))
    window_sessions = len(_weekdays(_UPDATE_WINDOW_START, _UPDATE_WINDOW_END))
    fillers = len(_master_symbols()) - len(EQUITY_GROWTH)
    expected_rows = len(EQUITY_GROWTH) * full_sessions + fillers * window_sessions
    assert f"输入行数 {expected_rows}" in html
    assert "不可信断点 0" in html



def test_research_rejects_missing_master_evidence_but_engineering_is_untrusted(
    env,
):
    """Missing master evidence cannot earn an ACCEPTED record, so the formal
    research run fails at the acceptance gate; the ENGINEERING diagnostic
    still replays as an UNTRUSTED run that can never be accepted as a trusted
    performance claim."""
    _publish_dataset_without_master_evidence(env.root)
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(
        ResearchRunFailed, match="no valid real-data-v1 acceptance"
    ):
        runner.run(_SPEC)
    debug = runner.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)
    metrics = json.loads(
        (debug.path / "metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["evaluation"]["status"] == "UNTRUSTED"
    assert debug.manifest.status == "REJECTED"



def test_new_stock_excluded_before_120_listed_days(tmp_path):
    """A symbol with a real list_date under 120 sessions before the window end
    never enters a factor candidate set (momentum seasoning)."""
    sessions = _weekdays(_BARS_START, _BARS_END)
    fresh_symbol = "603999.SH"
    fresh_list_date = sessions[-90]
    fresh_growth = 0.00200
    # The fresh listing must itself be a point-in-time member (swapped in for
    # one filler constituent, keeping the csi300 cardinality at 300) so this
    # test isolates the momentum seasoning filter: membership alone does not
    # admit the symbol, the minimum-history rule still does.
    facts = list(_fact_payloads())
    facts[-1] = _fact_payload(fresh_symbol)
    project_root = tmp_path / "project"
    _publish_synthetic_dataset(
        project_root,
        fresh=(fresh_symbol, fresh_list_date, fresh_growth),
        membership_facts=tuple(facts),
    )
    runner = ResearchRunner(
        project_root,
        config_root=_write_config_tree(tmp_path, facts=tuple(facts)),
    )
    experiment = runner.run(_SPEC)
    assert experiment.manifest.status in ("ACCEPTED", "REJECTED")

    factors = pd.read_parquet(experiment.path / "factor_results.parquet")
    fresh_rows = factors.loc[factors["symbol"] == fresh_symbol]
    assert not fresh_rows.empty, "the fresh symbol must produce factor rows"
    assert not fresh_rows["is_valid"].astype(bool).any()
    assert "seasoning_below_120" in set(fresh_rows["invalid_reason"])

    targets = pd.read_parquet(experiment.path / "target_positions.parquet")
    assert fresh_symbol not in set(targets["symbol"]), (
        "a symbol with fewer than 120 listed trading days must be kept out of "
        "target positions"
    )


# --------------------------------------------------------------------------- #
# Task 4: frozen definition preflight, identity and snapshot persistence
# --------------------------------------------------------------------------- #


def _preflight_manifest(runner: ResearchRunner, env: _Env) -> dict:
    path = env.root / "data" / "runs" / runner.run_id / "universe_preflight.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_invalid_membership_fails_before_factor_artifact(env, tmp_path):
    """The plan's preflight rule: invalid membership evidence stops the run.

    The dataset's membership table is one member short of the csi300
    cardinality, so the mandatory ``index_membership_evidence`` gate must fail
    at the distinct pre-factor stage ``universe_acceptance``: the failure is
    auditable under data/runs, no factor artifact is ever produced, and the
    run never falls back to the master's full symbol list.
    """
    broken_facts = _fact_payloads(_CSI300_SIZE - 1)
    broken_version = _publish_synthetic_dataset(
        env.root, membership_facts=broken_facts
    )
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(ResearchRunFailed) as excinfo:
        runner.run(_SPEC)
    assert excinfo.value.failed_stage == "universe_acceptance"
    manifest = runner.latest_run_manifest()
    assert manifest.status == "FAILED"
    assert manifest.failed_stage == "universe_acceptance"
    assert manifest.error is not None
    preflight = _preflight_manifest(runner, env)
    assert preflight["status"] == "FAIL"
    assert preflight["failed_stage"] == "universe_acceptance"
    assert "UNIVERSE_MEMBER_COUNT_MISMATCH" in preflight["error_codes"]
    run_dir = env.root / "data" / "runs" / runner.run_id
    assert not (run_dir / "factor_results.parquet").exists()
    assert not (run_dir / "signals.parquet").exists()
    assert not runner.partial_experiment_exists()
    # the dataset CURRENT pointer is untouched by the failed run
    assert DatasetPublisher(env.root).current().version == broken_version


def test_missing_membership_table_fails_at_universe_acceptance(env):
    """An empty membership table cannot start a formal run.

    Publish acceptance requires the ``universe_membership`` table to exist
    (``required_table_coverage`` enforces presence only), so a membership-less
    dataset can no longer be acceptance-published.  The universe evidence
    check still treats a present-but-empty table as missing, so a dataset
    carrying an empty membership table passes the acceptance gate and then
    fails the formal run at ``universe_acceptance``.
    """
    _publish_synthetic_dataset(env.root, membership_facts=())
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(ResearchRunFailed) as excinfo:
        runner.run(_SPEC)
    assert excinfo.value.failed_stage == "universe_acceptance"
    preflight = _preflight_manifest(runner, env)
    assert "UNIVERSE_MEMBERSHIP_TABLE_MISSING" in preflight["error_codes"]
    run_dir = env.root / "data" / "runs" / runner.run_id
    assert not (run_dir / "factor_results.parquet").exists()


def test_tampered_membership_table_fails_the_definition_hash(env):
    """Facts that differ from the pinned table hash never reach a factor."""
    tampered = list(_fact_payloads())
    tampered[0] = {**tampered[0], "announcement_date": date(2018, 1, 1)}
    _publish_synthetic_dataset(env.root, membership_facts=tuple(tampered))
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(ResearchRunFailed) as excinfo:
        runner.run(_SPEC)
    assert excinfo.value.failed_stage == "universe_acceptance"
    preflight = _preflight_manifest(runner, env)
    assert preflight["error_codes"]
    run_dir = env.root / "data" / "runs" / runner.run_id
    assert not (run_dir / "factor_results.parquet").exists()


def test_formal_run_persists_definition_identity_and_signal_day_snapshots(env):
    """A passing preflight freezes the definition into every artifact.

    The experiment id incorporates the definition version; the run/experiment
    manifests, metrics and config snapshot carry the universe id, frozen
    version, rules version and table hash plus the ``{ISO date: snapshot}``
    map for every signal day; the frozen spec never keeps ``CURRENT``.
    """
    runner = ResearchRunner(env.root, config_root=env.config_root)
    experiment = runner.run(_SPEC)
    definition = env.definition
    table_hash = membership_content_hash(list(env.facts))

    manifest = experiment.manifest
    assert manifest.universe_id == "csi300"
    assert manifest.universe_version == definition.version
    assert manifest.universe_rules_version == _RULES_VERSION
    assert manifest.universe_membership_table_sha256 == table_hash

    spec_text = (experiment.path / "experiment_spec.yml").read_text()
    assert "CURRENT" not in spec_text
    assert definition.version in spec_text

    metrics = json.loads(
        (experiment.path / "metrics.json").read_text(encoding="utf-8")
    )
    universe = metrics["meta"]["universe"]
    assert universe["universe_id"] == "csi300"
    assert universe["universe_version"] == definition.version
    assert universe["rules_version"] == _RULES_VERSION
    assert universe["membership_table_sha256"] == table_hash
    assert universe["coverage_start"] == _FACTS_START.isoformat()
    assert universe["coverage_end"] == _BARS_END.isoformat()

    snapshots = metrics["meta"]["universe_daily_snapshots"]
    counts = metrics["meta"]["universe_daily_member_counts"]
    signals = pd.read_parquet(experiment.path / "signals.parquet")
    signal_days = sorted(
        pd.Timestamp(day).strftime("%Y-%m-%d") for day in signals["signal_date"]
    )
    assert sorted(snapshots) == signal_days
    assert sorted(counts) == signal_days
    assert all(counts[day] == _CSI300_SIZE for day in counts)

    # every persisted snapshot equals an independently built resolver's value
    resolver = UniverseResolver(
        definition,
        resolve_memberships(list(env.facts), {}),
        facts=list(env.facts),
    )
    for day in (signal_days[0], signal_days[len(signal_days) // 2], signal_days[-1]):
        assert snapshots[day] == resolver.snapshot_for(date.fromisoformat(day))

    # run manifest and config snapshot carry the same frozen identity
    run_state = runner.latest_run_manifest()
    assert run_state.status == "COMPLETED"
    assert run_state.universe["universe_id"] == "csi300"
    assert run_state.universe["universe_version"] == definition.version
    assert run_state.universe["membership_table_sha256"] == table_hash
    assert run_state.universe["daily_snapshots"] == snapshots

    config_snapshot = yaml.safe_load(
        (experiment.path / "config_snapshot.yml").read_text(encoding="utf-8")
    )
    assert config_snapshot["universe"]["universe_id"] == "csi300"
    assert config_snapshot["universe_daily_snapshots"] == snapshots

    preflight = _preflight_manifest(runner, env)
    assert preflight["status"] == "PASS"
    assert preflight["failed_stage"] is None
    assert preflight["universe_version"] == definition.version


def test_same_facts_freeze_the_same_definition_across_datasets(tmp_path):
    """Identical membership facts freeze one definition version everywhere.

    Two projects whose datasets differ only in the synthetic price surface
    resolve to different dataset versions (and so different experiment ids),
    but the identical evidenced facts pin the same universe version and
    reproduce the identical per-signal-day member snapshot map.
    """
    env_a = _new_env(tmp_path)
    experiment_a = ResearchRunner(
        env_a.root, config_root=env_a.config_root
    ).run(_SPEC)

    other_root = tmp_path / "other"
    version_b = _publish_synthetic_dataset(
        other_root, index_close=(4001.0, 2000.0)
    )
    assert version_b != env_a.version
    experiment_b = ResearchRunner(
        other_root, config_root=_write_config_tree(tmp_path / "tree-b")
    ).run(_SPEC)
    assert experiment_b.experiment_id != experiment_a.experiment_id
    assert (
        experiment_b.manifest.universe_version
        == experiment_a.manifest.universe_version
    )
    assert experiment_b.manifest.universe_membership_table_sha256 == (
        experiment_a.manifest.universe_membership_table_sha256
    )
    assert _signal_snapshot_map(experiment_b.path) == _signal_snapshot_map(
        experiment_a.path
    )


def _signal_snapshot_map(experiment_path: Path) -> dict[str, str]:
    metrics = json.loads(
        (experiment_path / "metrics.json").read_text(encoding="utf-8")
    )
    return metrics["meta"]["universe_daily_snapshots"]


def test_stable_membership_rerun_reproduces_the_same_snapshot_map(tmp_path):
    """Identical frozen inputs reproduce the identical per-day member hashes."""
    env = _new_env(tmp_path)
    first = ResearchRunner(env.root, config_root=env.config_root).run(_SPEC)
    second = ResearchRunner(env.root, config_root=env.config_root).run(_SPEC)
    assert second.experiment_id == first.experiment_id
    assert _signal_snapshot_map(second.path) == _signal_snapshot_map(first.path)


def _plain_date(value: object) -> date:
    """Normalize a parquet-round-tripped Timestamp/date back to a plain date."""
    return pd.Timestamp(value).date()


def test_total_return_factor_does_not_change_fill_or_valuation_prices(tmp_path):
    """Total-return factor input never leaks into execution or valuation.

    The market carries one verified, implemented cash dividend on the
    highest-growth holding (always in the weekly top-10), so its ``adjusted``
    total-return series diverges from the unadjusted prices.  Every fill must
    still reference the unadjusted open of its bar, and the persisted daily
    mark-to-market must equal a valuation recomputed from unadjusted
    ``daily_bar`` closes.
    """
    action_symbol = "601857.SH"
    sessions = _weekdays(_BARS_START, _BARS_END)
    ex_date = sessions[len(sessions) // 2]
    project_root = tmp_path / "project"
    version = _publish_synthetic_dataset(
        project_root,
        corporate_actions=_cash_action(action_symbol, ex_date),
        coverage=_coverage(
            _master_symbols(),
            status=CoverageStatus.VERIFIED_EMPTY,
            event_symbols=(action_symbol,),
        ),
    )
    experiment = ResearchRunner(
        project_root, config_root=_write_config_tree(tmp_path)
    ).run(_SPEC)
    with DatasetReader(project_root).open(version) as context:
        daily = context.read("daily_bar")
        adjusted = context.read("adjusted_bar")
    equity = daily[daily["symbol"].isin(_universe_symbols())]
    day_symbol = [equity["trade_date"].map(_plain_date), "symbol"]

    # Sanity: the dividend really moves the adjusted series away from the raw
    # one (far beyond the cent-level tolerances below), so a wrongly adjusted
    # execution/valuation price could never pass this test silently.
    action_rows = adjusted[adjusted["symbol"] == action_symbol]
    divergence = (action_rows["adjusted_close"] - action_rows["raw_close"]).abs()
    assert float(divergence.max()) > 0.1

    fills = pd.read_parquet(experiment.path / "fills.parquet")
    assert not fills.empty
    assert action_symbol in set(fills["symbol"])
    opens = equity.set_index(day_symbol)["open"]
    for record in fills.itertuples():
        expected = float(opens.loc[(_plain_date(record.trade_date), record.symbol)])
        # The executor cent-quantizes the reference, so half a cent is the
        # largest possible distance from the raw unadjusted open.
        assert float(record.reference_price) == pytest.approx(expected, abs=0.005)

    # Valuation: the published daily equity is portfolio-level only, so
    # recompute its market value from unadjusted daily_bar closes over the
    # per-day quantities the fill ledger implies (a cash dividend changes no
    # share counts) and require a match within a cent.
    daily_equity = pd.read_parquet(experiment.path / "daily_equity.parquet")
    closes = equity.set_index(day_symbol)["close"]
    held_by_day: dict[date, dict[str, int]] = {}
    positions: dict[str, int] = {}
    for record in fills.sort_values("trade_date", kind="stable").itertuples():
        symbol = str(record.symbol)
        signed = int(record.quantity) * (1 if record.side == BUY else -1)
        positions[symbol] = positions.get(symbol, 0) + signed
        held_by_day[_plain_date(record.trade_date)] = dict(positions)
    fill_days = sorted(held_by_day)

    def _positions_at(day: date) -> dict[str, int]:
        previous = None
        for fill_day in fill_days:
            if fill_day > day:
                break
            previous = fill_day
        return held_by_day[previous] if previous is not None else {}

    checked = 0
    for row in daily_equity.itertuples():
        day = _plain_date(row.trade_date)
        held = _positions_at(day)
        if not held:
            continue  # no holdings yet; nothing to mark
        expected_value = sum(
            quantity * float(closes.loc[(day, symbol)])
            for symbol, quantity in held.items()
        )
        assert float(row.market_value) == pytest.approx(expected_value, abs=0.01)
        checked += 1
    assert checked > 0
