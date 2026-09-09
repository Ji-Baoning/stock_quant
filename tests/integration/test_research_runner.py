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

Every test publishes a deterministic synthetic market -- twelve SH main-board
equities with bars (whose closes follow ``55 * exp(growth * (session - (n-1)))``
plus two flat benchmark index rows), a security master that also carries the
300 synthetic ``csi300`` constituents without bars, and an evidence-backed
``universe_membership`` table -- under a temporary project root, then runs the
repository's real ``momentum_60d`` spec (``configs/experiments/
momentum_60d.yml``) from a temporary config tree whose ``csi300`` definition is
pinned to exactly those facts (the repository's ``configs/universes/csi300.yml``
is a placeholder template that formal runs must reject).  The growth vector
ranks are distinct and time-stable, so the weekly top-10 of the 12 is the ten
largest ``growth`` values on every signal date and the weekly net rebalance
keeps every name on at most one order side per execution day.
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
from stock_quant.research.models import (
    REQUIRED_ARTIFACTS,
    Evaluation,
    ExperimentEvaluation,
    ResearchRunFailed,
)
from stock_quant.research.runner import ResearchRunner
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
    return tuple(_fact_payload(symbol) for symbol in _csi300_symbols(count))


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
            frames.append(_instrument_frame(sessions, symbol, closes, opens))
        else:
            frames.append(_instrument_frame(sessions, symbol, closes))
    if fresh is not None:
        symbol, list_date, growth = fresh
        start_at = next(
            index for index, day in enumerate(sessions) if day >= list_date
        )
        closes = [
            _BASE_PRICE * math.exp(growth * (index - (n - 1)))
            for index in range(start_at, n)
        ]
        frames.append(_instrument_frame(sessions[start_at:], symbol, closes))
    for symbol, level in zip(_BENCHMARK_SYMBOLS, index_close):
        frames.append(_instrument_frame(sessions, symbol, [level] * n))
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def _instrument_frame(
    sessions: list[date],
    symbol: str,
    closes: list[float],
    opens: list[float] | None = None,
) -> pd.DataFrame:
    n = len(sessions)
    opens = closes if opens is None else opens
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(sessions),
            "symbol": symbol,
            "open": opens,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [0] * n,
            "amount": [0.0] * n,
            "adjustment": "unadjusted",
            "source": "synthetic",
            "ingested_at": [_INGESTED] * n,
        }
    )


def _master_symbols(fresh: tuple[str, date, float] | None = None) -> list[str]:
    """Every security_master symbol: the traded names plus all constituents.

    The synthetic csi300 membership facts must intersect the master (the
    acceptance gate validates fact symbols against it), so the master carries
    the 300 constituents alongside the twelve tradable equities; only the
    tradable names (and the optional fresh listing) have daily bars.
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
) -> pd.DataFrame:
    """One deterministic coverage row per symbol over a common window.

    The default window is the full synthetic calendar, a superset of the
    execution window any momentum spec derives, so a single row per symbol is
    always enough evidence when the status is trusted.
    """
    records = [
        coverage_record(
            symbol,
            window_start,
            window_end,
            status,
            reason,
            checked_at=_INGESTED,
        )
        for symbol in sorted(symbols)
    ]
    return coverage_frame(records)


def _master_coverage(symbols: tuple[str, ...]) -> pd.DataFrame:
    """One deterministic security_master_coverage row per symbol."""
    records = [
        master_coverage_record(
            symbol,
            list_date=_LIST_DATE,
            list_status=ListStatus.L,
            source=MASTER_SOURCE_STOCK_BASIC,
            snapshot_sha256="f" * 64,
            sdk_version="fixture",
            checked_at=_INGESTED,
        )
        for symbol in sorted(symbols)
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
    membership_facts: tuple[dict, ...] | None = None,
    with_membership: bool = True,
) -> str:
    """Publish the synthetic market under ``project_root``; return its version.

    The default dataset carries an explicit ``VERIFIED_EMPTY`` corporate-action
    coverage row per master symbol AND a ``security_master_coverage`` row per
    master symbol (both evidence tables), so the ResearchRunner's RESEARCH
    gates pass over the default market.  It also carries the immutable
    ``universe_membership`` table with the 300 evidenced csi300 facts the
    temporary definition pins.  ``membership_facts`` overrides the fact rows
    (a wrong row count fails the acceptance gate's cardinality check);
    ``with_membership=False`` drops the table entirely.
    """
    master = _security_master(fresh)
    symbols = tuple(master["symbol"])
    if coverage is None:
        coverage = _coverage(symbols, status=CoverageStatus.VERIFIED_EMPTY)
    if master_coverage is None:
        master_coverage = _master_coverage(symbols)
    facts = _fact_payloads() if membership_facts is None else membership_facts
    tables = {
        "daily_bar": _bars(
            _weekdays(_BARS_START, _BARS_END),
            index_close=index_close,
            limit_locked_symbols=limit_locked_symbols,
            fresh=fresh,
        ),
        "security_master": master,
        "security_master_coverage": master_coverage,
        "corporate_action": _corporate_action(),
        "corporate_action_coverage": coverage,
        "trading_calendar": _trading_calendar(),
    }
    if with_membership:
        tables["universe_membership"] = membership_frame(list(facts))[
            UNIVERSE_MEMBERSHIP_COLUMNS
        ]
    return DatasetPublisher(project_root).publish(tables, QualityReport()).version


def _write_config_tree(root: Path) -> Path:
    """A temporary config tree whose csi300 definition is real, not a template.

    Copies the repository config files (project/sources/costs/rules/universe)
    and the formal momentum spec, then writes ``configs/universes/csi300.yml``
    pinned to the canonical synthetic facts and the dataset's actual covered
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
        source = _REPO_ROOT / "configs" / name
        if source.is_file():
            shutil.copyfile(source, configs / name)
    shutil.copyfile(
        _REPO_ROOT / "configs" / "experiments" / "momentum_60d.yml",
        configs / "experiments" / "momentum_60d.yml",
    )
    document = {
        "schema_version": 1,
        "universe_id": "csi300",
        "rules_version": _RULES_VERSION,
        "membership_table_sha256": membership_content_hash(list(_fact_payloads())),
        "coverage_start": _FACTS_START.isoformat(),
        "coverage_end": _BARS_END.isoformat(),
        "evidence_summary_sha256": _EVIDENCE_SUMMARY,
    }
    (configs / "universes" / "csi300.yml").write_text(
        yaml.safe_dump(document, sort_keys=True), encoding="utf-8"
    )
    return config_root


def _publish_dataset_with_untrusted_coverage(project_root: Path) -> str:
    """Republish CURRENT so every holding's coverage evidence is UNTRUSTED."""
    return _publish_synthetic_dataset(
        project_root,
        coverage=_coverage(
            _master_symbols(),
            status=CoverageStatus.UNTRUSTED,
            reason=CoverageReason.SOURCE_FETCH_FAILED,
        ),
    )


def _publish_dataset_with_verified_empty_coverage(project_root: Path) -> str:
    """Republish CURRENT so every holding's coverage is explicitly VERIFIED_EMPTY."""
    return _publish_synthetic_dataset(
        project_root,
        coverage=_coverage(
            _master_symbols(), status=CoverageStatus.VERIFIED_EMPTY
        ),
    )


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
    _publish_dataset_with_untrusted_coverage(env.root)
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(ResearchRunFailed, match="corporate action trust"):
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


def _publish_dataset_without_master_evidence(project_root: Path) -> str:
    """Republish CURRENT with an empty security_master_coverage table."""
    from stock_quant.data_model.security_master import master_coverage_frame

    return _publish_synthetic_dataset(
        project_root,
        master_coverage=master_coverage_frame([]),
    )


def test_research_rejects_missing_master_evidence_but_engineering_is_untrusted(
    env,
):
    """Empty master evidence freezes RESEARCH; ENGINEERING still diagnoses.

    The rejection must name the security-master evidence and happen before any
    backtest; the ENGINEERING run proceeds as an UNTRUSTED diagnostic that can
    never be accepted as a trusted performance claim.
    """
    _publish_dataset_without_master_evidence(env.root)
    runner = ResearchRunner(env.root, config_root=env.config_root)
    with pytest.raises(ResearchRunFailed, match="security master evidence"):
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
    project_root = tmp_path / "project"
    _publish_synthetic_dataset(
        project_root,
        fresh=(fresh_symbol, fresh_list_date, fresh_growth),
    )
    runner = ResearchRunner(project_root, config_root=_write_config_tree(tmp_path))
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
    """A dataset without a membership table cannot start a formal run."""
    _publish_synthetic_dataset(env.root, with_membership=False)
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
