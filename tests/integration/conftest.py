"""Shared offline fixtures for the CLI / data-pipeline end-to-end tests.

Task 13 wires the research runner to a Typer CLI and builds a thin end-to-end
acceptance over one synthetic project.  A test-support file is justified here
because *both* ``test_cli.py`` and ``test_end_to_end.py`` need the exact same
deterministic synthetic project (a ``configs/`` tree copied from the committed
``templates/project-config`` template plus one content-addressed 8-table dataset that also carries
``adjusted_bar``, ``corporate_action_quarantine``,
``corporate_action_coverage`` and ``security_master_coverage`` evidence
tables) and the same two fixtures
(``cli_runner``, ``fixture_root``).  Trusted fixture datasets are published
with genuine data-update provenance -- deterministic raw snapshots under
``data/raw`` and the sanitized ``build_config`` the pipeline itself writes --
and carry one fixed ACCEPTED real-data record (created 2022-01-08, operator
``integration-fixture``) whose automated checks come from the real offline
checker, so formal RESEARCH runs pass the acceptance gate exactly like an
operator dataset would.  Broken/untrusted fixtures publish no ACCEPTED record.
All fixtures are offline and live under ``tmp_path_factory``; nothing here
touches the network or a token, and no real market data is committed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
import yaml

from stock_quant.data_model.adjusted_bar import build_adjusted_bars
from stock_quant.data_model.calendar_coverage import (
    SOURCE_TUSHARE_RELAY,
    supplier_span,
)
from stock_quant.data_model.corporate_action_coverage import (
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_model.fetch_coverage import (
    KIND_FETCHED,
    FetchSegment,
    to_build_config_payload,
)
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    CORPORATE_ACTION_QUARANTINE_COLUMNS,
    DAILY_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
)
from stock_quant.data_model.security_master import (
    MASTER_SOURCE_STOCK_BASIC,
    ListStatus,
    master_coverage_frame,
    master_coverage_record,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_model.universe_membership import (
    membership_content_hash,
    membership_frame,
)
from stock_quant.data_pipeline import (
    DataUpdateRequest,
    SourceStatus,
    dataset_build_config,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.data_sources.base import DataRequest, FetchResult, request_key
from stock_quant.data_sources.raw_store import RawStore
from stock_quant.research.acceptance.checks import (
    AcceptanceCheckInput,
    dataset_evidence,
    run_automated_checks,
)
from stock_quant.research.acceptance.models import (
    MANUAL_CHECK_CODES,
    AcceptanceDecision,
    AcceptanceRecord,
    CheckStatus,
    EvidenceReference,
    ManualCheckResult,
    ManualCheckStatus,
    compute_acceptance_id,
)
from stock_quant.research.acceptance.registry import AcceptanceRegistry
from stock_quant.research.universe import load_universe_coverage_criterion

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The committed configuration template the fixture projects copy.  The
#: repository root deliberately carries no live ``configs/`` tree; the
#: template directory is a copy source only and is never resolved as a root.
_TEMPLATE_CONFIG = _REPO_ROOT / "templates" / "project-config"

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
  momentum_60d: 2.0.0
dataset_version: CURRENT
universe_version: CURRENT
data_acceptance_id: CURRENT_ACCEPTED
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

#: A formal walk-forward spec over the same fixture dataset.  The requested
#: OOS range is the complete 2021 calendar year (its 2018-2020 warmup carries
#: 784 confirmed weekday sessions, above the 756-session policy floor), so
#: the run completes with one executed fold and an INCONCLUSIVE conclusion.
_WF_SPEC_YAML = """\
# 正式 walk-forward 规格（合成数据、非投资建议）：2021 单年度 OOS fold。
hypothesis: >-
  过去 60 个交易日的复权收益在合成样本 2021 年度样本外区间内的稳定性验证；
  仅验证 walk-forward 管线与审计产物，不构成投资建议。
execution_pipeline: walk_forward_oos_v1
factor_versions:
  momentum_60d: 2.0.0
dataset_version: CURRENT
universe_version: CURRENT
universe_definition: custom_wf_fixture
data_acceptance_id: CURRENT_ACCEPTED
date_range:
  start_date: 2021-01-01
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

#: A walk-forward spec whose only fold cannot satisfy the 756-session warmup
#: floor (2019's warmup spans 2018 only): the run must FAIL before any
#: account exists, exit nonzero and publish no experiment.
_WF_EARLY_SPEC_YAML = """\
# 预热不足的 walk-forward 规格（合成数据）：2019 fold 预热仅 2018 一个年度。
hypothesis: >-
  验证预热不足的 fold 在任何回测前被拒绝（FAILED，结论为空）；不构成投资建议。
execution_pipeline: walk_forward_oos_v1
factor_versions:
  momentum_60d: 2.0.0
dataset_version: CURRENT
universe_version: CURRENT
universe_definition: custom_wf_fixture
data_acceptance_id: CURRENT_ACCEPTED
date_range:
  start_date: 2019-01-01
  end_date: 2019-12-31
train_validation_holdout_policy: not_applicable_engineering_mvp
preprocessing:
  winsorization: none
  standardization: none
portfolio_rule:
  name: top_n_equal_weight
  top_n: 10
  lot_size: 100
cost_scenarios:
  - full_cost
random_seed: 42
code_commit: unversioned
parent_experiment_ids: []
agent_id: null
"""

#: A narrow UNTRUSTED evidence band for the ``broken`` fixture: wide enough
#: that a RESEARCH run's execution window is not fully trusted, narrow enough
#: that momentum windows opening 61 observations past the band recover, so an
#: ENGINEERING diagnostic can still replay a real backtest over point-in-time
#: adjusted bars (rows inside and just after the band are ERROR breaks).
_BROKEN_COVERAGE_START = date(2020, 6, 1)
_BROKEN_COVERAGE_END = date(2020, 6, 5)

#: The evidence-backed fixture universe definition the walk-forward specs
#: resolve through (a custom pool carries no canonical cardinality).
_WF_UNIVERSE_ID = "custom_wf_fixture"
_WF_FACTS_START = date(2018, 1, 2)
_WF_ANNOUNCED = date(2018, 1, 2)
_WF_RULES_VERSION = "fixture-rules-v1"
_WF_EVIDENCE_SUMMARY = "cd" * 32
_WF_SNAPSHOT = "ef" * 32
_WF_DOCUMENT = "ab" * 32

#: The data-update window every trusted fixture dataset's sanitized build
#: evidence binds.  It sits inside the fixture calendar/bars span and ends on
#: an open day, so the real offline checks can fully verify it.
_UPDATE_WINDOW_START = date(2021, 11, 1)
_UPDATE_WINDOW_END = date(2021, 11, 30)
_UPDATE_RUN_ID = "fixture-data-update-0001"

#: The fixed acceptance identity of the trusted fixture record: a constant
#: creation instant and operator keep the content-derived ``acceptance_id``
#: stable within one fixture build (and identical reruns against it).  Across
#: independent builds the id differs because the dataset manifest itself
#: carries a wall-clock ``created_at`` that stays outside the dataset version
#: hash; nothing depends on cross-build stability.
_ACCEPTANCE_OPERATOR = "integration-fixture"
_ACCEPTANCE_CREATED_AT = datetime(2022, 1, 8, tzinfo=timezone.utc)
_EVIDENCE_DIR = Path("data") / "acceptance_evidence"

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
    #: The acceptance id of the published ACCEPTED record, or ``None`` when
    #: the fixture is broken/untrusted and publishes no acceptance.
    acceptance_id: str | None = None

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


def _wf_membership_facts(universe: Universe) -> list[dict[str, object]]:
    """One evidence-backed open interval per universe symbol.

    Announced on/before the interval start and open-ended over the whole
    fixture calendar, so the frozen ``custom_wf_fixture`` definition resolves
    the full symbol set on every walk-forward OOS day.
    """
    return [
        {
            "universe_id": _WF_UNIVERSE_ID,
            "symbol": entry.symbol,
            "raw_effective_from": _WF_FACTS_START,
            "raw_effective_to": None,
            "announcement_date": _WF_ANNOUNCED,
            "status": "active",
            "reason": "initial_constituent",
            "source": "fixture_announcement",
            "source_url": "https://fixture.invalid/membership-2018.pdf",
            "snapshot_sha256": _WF_SNAPSHOT,
            "source_document_sha256": _WF_DOCUMENT,
        }
        for entry in universe.entries
    ]


def _fixture_sources_yaml() -> str:
    """The template ``sources.yml`` with every supplier enabled by default.

    The fixture projects must not inherit the template's per-supplier toggles:
    the template may ship with ``baostock.enabled: false`` (an operator
    preference), while fixture updates expect every optional supplier to be
    attempted unless a test explicitly disables one via ``write_sources``.
    """
    payload = yaml.safe_load(
        (_TEMPLATE_CONFIG / "sources.yml").read_text(encoding="utf-8")
    )
    for settings in payload.values():
        if isinstance(settings, dict):
            settings["enabled"] = True
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def build_fixture_project(root: Path, *, broken: bool = False) -> FixtureProject:
    """Materialise one full synthetic project under ``root``.

    Copies the committed ``templates/project-config`` tree (project/sources/
    costs/rules/universe) into ``root/configs/``, rewrites ``sources.yml`` so
    every supplier is enabled (see ``_fixture_sources_yaml``), authors a short
    experiment spec, then
    publishes a deterministic 9-table dataset over the repository's 30-symbol
    universe plus two benchmark indices.  The ``adjusted_bar`` rows are
    generated through the production ``build_adjusted_bars`` over the same
    synthetic bars, and one ``corporate_action_coverage``
    row per universe symbol over the whole fixture bars window so the default
    RESEARCH runs in the CLI / end-to-end suite pass the corporate-action trust
    gate, and one ``security_master_coverage`` row per universe symbol (at the
    master's listing facts) so the RESEARCH master-evidence gate accepts the
    dataset too.

    A trusted dataset (``broken=False``) is published exactly the way the data
    pipeline publishes an update: deterministic raw snapshots are saved through
    ``RawStore`` for every required source role and the sanitized
    ``build_config`` binds them with ``origin=data_update`` and healthy
    required-source statuses.  The real offline checker must pass every
    ``real-data-v1`` check and one fixed ACCEPTED record is published, so
    formal RESEARCH runs pass the acceptance gate.  ``broken=True`` keeps the
    (empty) facts table so an ENGINEERING diagnostic can still replay, but
    marks the coverage evidence UNTRUSTED (``SOURCE_FETCH_FAILED``) over the
    narrow ``_BROKEN_COVERAGE_*`` band, keeps the security-master coverage
    table *empty*, and publishes neither build evidence nor an ACCEPTED
    record: a RESEARCH run must fail the acceptance gate before any backtest
    while an ENGINEERING run may still complete as an UNTRUSTED diagnostic
    that is never accepted as a trusted performance claim.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    config_dir = root / "configs"
    experiments_dir = config_dir / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)
    for name in _CONFIG_NAMES:
        (config_dir / name).write_text(
            (_TEMPLATE_CONFIG / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (config_dir / "sources.yml").write_text(
        _fixture_sources_yaml(), encoding="utf-8"
    )
    (experiments_dir / "momentum_60d.yml").write_text(
        _FIXTURE_SPEC_YAML, encoding="utf-8"
    )
    (experiments_dir / "walk_forward.yml").write_text(
        _WF_SPEC_YAML, encoding="utf-8"
    )
    (experiments_dir / "walk_forward_early.yml").write_text(
        _WF_EARLY_SPEC_YAML, encoding="utf-8"
    )
    universe = Universe.from_yaml(config_dir / "universe.yml")
    (config_dir / "universes").mkdir(parents=True, exist_ok=True)
    facts = _wf_membership_facts(universe)
    # ``coverage_start`` doubles as the build's ``full_history_acceptance_start``
    # (ADR-011), and the acceptance window is anchored there: it must sit
    # inside the bar evidence this fixture carries (bars start at BARS_START,
    # not at the calendar's CAL_START), or ``date_window_completeness``
    # correctly fails its own fixture.
    (config_dir / "universes" / f"{_WF_UNIVERSE_ID}.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "universe_id": _WF_UNIVERSE_ID,
                "rules_version": _WF_RULES_VERSION,
                "membership_table_sha256": membership_content_hash(facts),
                "coverage_start": BARS_START.isoformat(),
                "coverage_end": CAL_END.isoformat(),
                "evidence_summary_sha256": _WF_EVIDENCE_SUMMARY,
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    sessions = _weekdays(BARS_START, BARS_END)
    daily = _bars(sessions, universe)
    corporate_actions = _corporate_action()
    coverage = _coverage_table(universe, trusted=not broken)
    empty_quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
    tables = {
        "daily_bar": daily,
        "adjusted_bar": build_adjusted_bars(
            daily,
            corporate_actions,
            empty_quarantine,
            coverage,
            symbols=tuple(universe.symbols),
        ),
        "security_master": _security_master(universe),
        "security_master_coverage": _master_coverage_table(
            universe, present=not broken
        ),
        "corporate_action": corporate_actions,
        "corporate_action_quarantine": empty_quarantine,
        "corporate_action_coverage": coverage,
        "trading_calendar": _trading_calendar(),
        # The canonical schema contract also registers the immutable
        # ``universe_membership`` table.  Trusted fixtures carry one
        # evidence-backed open interval per universe symbol so the
        # walk-forward specs can resolve the frozen point-in-time
        # ``custom_wf_fixture`` definition; the empty frame would satisfy
        # only the engineering workflows.
        "universe_membership": membership_frame(facts),
    }
    if broken:
        version = DatasetPublisher(root).publish(tables, QualityReport()).version
        return FixtureProject(root=root, version=version, acceptance_id=None)
    version = DatasetPublisher(root).publish(
        tables, QualityReport(), build_config=fixture_build_config(root)
    ).version
    acceptance_id = publish_fixture_acceptance(root, version)
    return FixtureProject(
        root=root, version=version, acceptance_id=acceptance_id
    )


# --------------------------------------------------------------------------- #
# Synthetic data-update provenance and the fixed fixture acceptance record
# --------------------------------------------------------------------------- #


def fixture_build_config(
    project_root: Path,
    *,
    calendar_start: date = CAL_START,
    calendar_end: date = CAL_END,
) -> dict[str, object]:
    """The sanitized data-update build evidence bound into trusted fixtures.

    Saves deterministic ``FetchResult`` frames for every required source role
    (tushare primary daily + security master + both ``trade_cal`` exchanges,
    akshare benchmark) through the real ``RawStore`` and assembles the payload
    through the pipeline's own ``dataset_build_config`` primitive, so fixture
    datasets carry exactly the provenance shape an operator update produces --
    including one relay calendar span over the whole fixture calendar and the
    full-history criterion scanned from the project's universe definitions.
    ``calendar_start`` / ``calendar_end`` widen the relay span to the open-day
    range of the actually published ``trading_calendar`` table, because
    ``data validate`` fails any manifest whose coverage leaves a hole against
    the published calendar.
    """
    store = RawStore(project_root)
    results = _fixture_fetch_results()
    snapshots = tuple(store.save(result) for result in results)
    calendar_hashes = {
        exchange: [
            snapshot.sha256
            for snapshot, result in zip(snapshots, results)
            if result.endpoint == "trade_cal"
            and result.frame["exchange"].iloc[0] == exchange
        ]
        for exchange in ("SSE", "SZSE")
    }
    criterion = load_universe_coverage_criterion(
        project_root / "configs" / "universes"
    )
    # Minimal legal fetch-coverage evidence (plan Task 13 Step 6): one
    # ``fetched`` segment tiling the review window (acceptance anchor through
    # the resolved end).  It sits on the lane-less derived ``adjusted_bar``
    # table on purpose: a fetched segment on a fetch-lane table would make
    # ``last_covered_plus_1`` planning treat this hand-published baseline as
    # covering through the review end, flipping every explicit-window update
    # into a full carry with no raw evidence.
    review_start = criterion.acceptance_start or _UPDATE_WINDOW_START
    table_fetch_coverage = to_build_config_payload(
        {
            "adjusted_bar": [
                FetchSegment("adjusted_bar", KIND_FETCHED, review_start,
                             _UPDATE_WINDOW_END)
            ]
        }
    )
    return dataset_build_config(
        run_id=_UPDATE_RUN_ID,
        request=DataUpdateRequest(
            start_date=_UPDATE_WINDOW_START, end_date=_UPDATE_WINDOW_END
        ),
        effective_start_date=_UPDATE_WINDOW_START,
        resolved_end_date=_UPDATE_WINDOW_END,
        statuses=_fixture_source_statuses(),
        raw_snapshots=snapshots,
        calendar_spans=(
            supplier_span(
                calendar_start,
                calendar_end,
                source=SOURCE_TUSHARE_RELAY,
                snapshots_by_exchange=calendar_hashes,
            ),
        ),
        acceptance_start=criterion.acceptance_start,
        definition_hashes=criterion.definition_hashes,
        skipped_definitions=criterion.skipped,
        table_fetch_coverage=table_fetch_coverage,
    )


def publish_fixture_acceptance(project_root: Path, dataset_version: str) -> str:
    """Run the real offline checker and publish the fixed ACCEPTED record.

    Every ``real-data-v1`` automated check must PASS (fixture sanity); the
    manual checks are all PASS with fixture-local, deterministically-byteed
    evidence files under the project; ``created_at`` and the operator are
    fixed constants, so the content-derived acceptance id stays stable for
    identical evidence.  Returns the acceptance id.  Broken/untrusted fixture
    datasets never call this -- their research runs must fail the gate.
    """
    root = Path(project_root)
    value = AcceptanceCheckInput(root, dataset_version)
    automated = run_automated_checks(value)
    failed = [row.code for row in automated if row.status is not CheckStatus.PASS]
    assert not failed, f"fixture dataset failed automated checks: {failed}"
    evidence = dataset_evidence(value)
    manual = tuple(
        ManualCheckResult(
            code=code,
            status=ManualCheckStatus.PASS,
            summary="integration fixture operator sample verified",
            evidence=(_fixture_evidence_reference(root, code),),
        )
        for code in MANUAL_CHECK_CODES
    )
    provisional = AcceptanceRecord(
        acceptance_id="0" * 64,
        dataset_version=dataset_version,
        dataset_manifest_sha256=evidence.dataset_manifest_sha256,
        quality_report_sha256=evidence.quality_report_sha256,
        created_at=_ACCEPTANCE_CREATED_AT,
        operator_id=_ACCEPTANCE_OPERATOR,
        automated_checks=automated,
        manual_checks=manual,
        raw_snapshot_evidence=evidence.raw_snapshot_evidence,
        decision=AcceptanceDecision.ACCEPTED,
        reasons=(),
    )
    record = provisional.model_copy(
        update={"acceptance_id": compute_acceptance_id(provisional)}
    )
    AcceptanceRegistry(root).publish(record)
    return record.acceptance_id


def _fixture_evidence_reference(root: Path, code: str) -> EvidenceReference:
    """One local evidence file with deterministic bytes per manual check."""
    relative = (_EVIDENCE_DIR / f"{code}.txt").as_posix()
    summary = f"{code}: integration fixture operator sample verified"
    payload = (summary + "\n").encode("utf-8")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return EvidenceReference(
        kind="local",
        reference=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        summary=summary,
    )


def _fixture_source_statuses() -> dict[str, SourceStatus]:
    """Required sources ok; the optional validation source was not fetched."""
    return {
        "tushare": SourceStatus(
            source="tushare", required=True, ok=True,
            reason="ok", reason_code="ok",
        ),
        "akshare": SourceStatus(
            source="akshare", required=True, ok=True,
            reason="ok", reason_code="ok",
        ),
        "baostock": SourceStatus(
            source="baostock", required=False, ok=False,
            reason="validation series not fetched by the fixture",
            reason_code="optional_source_unavailable",
        ),
    }


def _fixture_fetch_results() -> tuple[FetchResult, ...]:
    """Deterministic raw responses for every required source role."""
    sessions = _weekdays(_UPDATE_WINDOW_START, _UPDATE_WINDOW_END)
    daily_request = DataRequest(
        endpoint="daily",
        symbols=("000001.SZ",),
        start_date=_UPDATE_WINDOW_START,
        end_date=_UPDATE_WINDOW_END,
    )
    daily = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * len(sessions),
            "trade_date": [day.strftime("%Y%m%d") for day in sessions],
            "open": [55.0] * len(sessions),
            "high": [55.0] * len(sessions),
            "low": [55.0] * len(sessions),
            "close": [55.0] * len(sessions),
            "volume": [1000] * len(sessions),
            "amount": [55000.0] * len(sessions),
        }
    )
    basic_request = DataRequest(
        endpoint="stock_basic",
        symbols=("000001.SZ",),
        start_date=_UPDATE_WINDOW_START,
        end_date=_UPDATE_WINDOW_END,
    )
    stock_basic = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "name": ["fixture_security"],
            "list_date": ["20180102"],
            "delist_date": [""],
            "list_status": ["L"],
        }
    )
    index_request = DataRequest(
        endpoint="index_history",
        symbols=("000300.SH",),
        start_date=_UPDATE_WINDOW_START,
        end_date=_UPDATE_WINDOW_END,
    )
    # The full akshare raw shape, not a minimal close-only frame: this stored
    # snapshot shares its request key with a real benchmark update over the
    # same window, and raw-snapshot reuse (ADR-015) may serve it back through
    # ``_normalize_index``, which requires the open/high/low/volume columns.
    index_history = pd.DataFrame(
        {
            "日期": sessions,
            "开盘": [4000.0] * len(sessions),
            "最高": [4000.0] * len(sessions),
            "最低": [4000.0] * len(sessions),
            "收盘": [4000.0] * len(sessions),
            "成交量": [0] * len(sessions),
            "成交额": [0.0] * len(sessions),
        }
    )
    return (
        FetchResult(
            source="tushare", endpoint="daily",
            request_key=request_key(daily_request), frame=daily,
            metadata={
                "source": "tushare",
                "sdk_version": "fixture",
                "transport_id": "api.waditu.com",
            },
        ),
        FetchResult(
            source="tushare", endpoint="stock_basic",
            request_key=request_key(basic_request), frame=stock_basic,
            metadata={
                "source": "tushare",
                "sdk_version": "fixture",
                "transport_id": "api.waditu.com",
            },
        ),
        FetchResult(
            source="akshare", endpoint="index_history",
            request_key=request_key(index_request), frame=index_history,
            metadata={
                "source": "akshare",
                "sdk_version": "fixture",
                "transport_id": "sina.stock-zh-index-daily",
            },
        ),
        _tushare_trade_cal_result("SSE", _UPDATE_WINDOW_START, _UPDATE_WINDOW_END),
        _tushare_trade_cal_result("SZSE", _UPDATE_WINDOW_START, _UPDATE_WINDOW_END),
    )


def _tushare_trade_cal_result(exchange: str, start: date, end: date) -> FetchResult:
    """A recorded ``trade_cal`` halo response for one exchange.

    Weekends are closed and ``pretrade_date`` chains to the previous weekday,
    matching the fixture ``trading_calendar`` (see ``_weekdays``), so a fixture
    window's continuity check passes.
    """
    request = DataRequest(
        "trade_cal",
        (),
        start - timedelta(days=1),
        end + timedelta(days=1),
        {"exchange": exchange},
    )
    rows: list[dict[str, object]] = []
    current = request.start_date
    while current <= request.end_date:
        previous = current - timedelta(days=1)
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
        rows.append(
            {
                "exchange": exchange,
                "cal_date": current.strftime("%Y%m%d"),
                "is_open": 1 if current.weekday() < 5 else 0,
                "pretrade_date": previous.strftime("%Y%m%d"),
            }
        )
        current += timedelta(days=1)
    return FetchResult(
        source="tushare",
        endpoint="trade_cal",
        request_key=request_key(request),
        frame=pd.DataFrame(rows),
        metadata={
            "source": "tushare",
            "sdk_version": "fixture",
            "transport_id": "api.waditu.com",
        },
    )


def _coverage_table(universe: Universe, *, trusted: bool) -> pd.DataFrame:
    """One deterministic coverage row per universe symbol.

    The corporate_action facts table of this fixture is empty; that is only
    trusted when the evidence says so.  ``trusted=True`` publishes a
    ``VERIFIED_EMPTY`` row per symbol over ``BARS_START..BARS_END`` (both action
    endpoints succeeded and found nothing) so the RESEARCH gate accepts the
    dataset; ``trusted=False`` publishes ``UNTRUSTED``/``SOURCE_FETCH_FAILED``
    rows over the narrow ``_BROKEN_COVERAGE_*`` band (the same empty facts are
    *not* trusted because the sources could not be checked), which the RESEARCH
    gate rejects while an ENGINEERING diagnostic can still replay.
    """
    status = CoverageStatus.VERIFIED_EMPTY if trusted else CoverageStatus.UNTRUSTED
    outcome = "success_empty" if trusted else "failed"
    reason = None if trusted else CoverageReason.SOURCE_FETCH_FAILED
    endpoints = (
        "cninfo_corporate_actions",
        "eastmoney_corporate_actions",
        "rights_issue_corporate_actions",
    )
    window_start = BARS_START if trusted else _BROKEN_COVERAGE_START
    window_end = BARS_END if trusted else _BROKEN_COVERAGE_END
    records = [
        coverage_record(
            entry.symbol,
            window_start,
            window_end,
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


def _master_coverage_table(universe: Universe, *, present: bool) -> pd.DataFrame:
    """One deterministic security_master_coverage row per universe symbol.

    Row presence is the whole evidence vocabulary: ``present=True`` publishes a
    row per symbol at the master's listing facts (the RESEARCH master-evidence
    gate accepts the dataset); ``present=False`` (the ``broken`` fixture)
    publishes an empty table so a RESEARCH run is rejected for missing master
    evidence while an ENGINEERING diagnostic may still replay.
    """
    if not present:
        return master_coverage_frame([])
    records = [
        master_coverage_record(
            entry.symbol,
            list_date=LIST_DATE,
            list_status=ListStatus.L,
            source=MASTER_SOURCE_STOCK_BASIC,
            snapshot_sha256="f" * 64,
            sdk_version="fixture",
            checked_at=_INGESTED,
        )
        for entry in universe.entries
    ]
    return master_coverage_frame(records)


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
            "list_status": [ListStatus.L.value] * n,
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


def write_sources(project_root: Path, *, tushare: bool = True,
                  akshare: bool = True, baostock: bool = True) -> None:
    """Rewrite ``configs/sources.yml`` enabling or disabling each supplier.

    Fixture projects start from the template ``sources.yml`` with every
    supplier enabled; tests that need a specific enablement call this before
    constructing any pipeline so the config gate (never a CLI flag) decides
    which sources may be built.
    """
    (Path(project_root) / "configs" / "sources.yml").write_text(
        yaml.safe_dump(
            {
                "tushare": {"enabled": tushare},
                "akshare": {"enabled": akshare},
                "baostock": {"enabled": baostock},
            }
        ),
        encoding="utf-8",
    )


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
