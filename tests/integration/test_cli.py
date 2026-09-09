"""Task 13 brief Step-1 CLI behaviour over the offline synthetic project.

Imported before ``stock_quant.cli`` exists so the file fails during import in
Step 2.  ``run_offline_fixture`` and its parser come from ``test_end_to_end``;
``fixture_root`` / ``broken_fixture_root`` / ``cli_runner`` come from
``conftest``.  Everything is offline and no token is ever configured.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from conftest import build_fixture_project  # noqa: E402
from test_end_to_end import run_offline_fixture  # noqa: E402  (after app import)
from test_reports import _experiment_input  # noqa: E402  (synthetic report helper)

from stock_quant.cli import app  # noqa: F401  (gates Step 2 collection)
from stock_quant.reporting.html import render_experiment_report


def test_official_research_command_returns_zero_and_prints_identity(
    cli_runner, fixture_root
):
    result = cli_runner.invoke(
        app,
        [
            "research",
            "run",
            "--spec",
            "configs/experiments/momentum_60d.yml",
            "--root",
            str(fixture_root.root),
        ],
    )
    assert result.exit_code == 0
    assert "experiment_id=" in result.stdout


def test_partial_failure_returns_nonzero(cli_runner, broken_fixture_root):
    result = cli_runner.invoke(
        app,
        [
            "research",
            "run",
            "--spec",
            "configs/experiments/momentum_60d.yml",
            "--root",
            str(broken_fixture_root.root),
        ],
    )
    assert result.exit_code != 0
    assert "FAILED" in result.stdout


def test_debug_backtest_writes_only_to_run_debug_dir(
    cli_runner, fixture_root
):
    experiments = fixture_root.root / "data" / "experiments"
    before = (
        set(p.name for p in experiments.iterdir())
        if experiments.is_dir()
        else set()
    )
    result = cli_runner.invoke(
        app,
        ["backtest", "momentum_60d", "--root", str(fixture_root.root)],
    )
    assert result.exit_code == 0, result.stdout
    debug_root = fixture_root.root / "data" / "runs" / "debug"
    assert debug_root.is_dir()
    published = set(p.name for p in experiments.iterdir())
    assert published == before, "debug backtest must not publish an experiment"


def test_data_update_without_token_fails_and_prints_failed(
    cli_runner, fixture_root, monkeypatch
):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    result = cli_runner.invoke(
        app,
        [
            "data",
            "update",
            "--start",
            "2021-11-01",
            "--end",
            "2021-11-30",
            "--root",
            str(fixture_root.root),
        ],
    )
    assert result.exit_code != 0
    assert "FAILED" in result.stdout


def test_data_validate_reports_current_dataset(
    cli_runner, fixture_root
):
    # An explicit --version keeps this independent of any other test that
    # republishes CURRENT on the shared session project.
    result = cli_runner.invoke(
        app,
        [
            "data",
            "validate",
            "--version",
            fixture_root.version,
            "--root",
            str(fixture_root.root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert fixture_root.version in result.stdout
    assert "PASS" in result.stdout


def test_data_bootstrap_publishes_initial_dataset(cli_runner, tmp_path):
    """Bootstrap creates the baseline tables required by the first update."""
    root = tmp_path / "seed-project"
    configs = root / "configs"
    configs.mkdir(parents=True)
    repo_configs = Path(__file__).resolve().parents[2] / "configs"
    for name in ("project.yml", "universe.yml"):
        shutil.copy(repo_configs / name, configs / name)

    result = cli_runner.invoke(app, ["data", "bootstrap", "--root", str(root)])

    assert result.exit_code == 0, result.stdout
    assert "published seed dataset:" in result.stdout
    assert "CURRENT ->" in result.stdout
    assert (root / "data" / "standardized" / "CURRENT").is_file()

    # The seed must be an honest empty placeholder: NaT listing dates and
    # NOT_APPLIED status, no security_master_coverage evidence table.
    from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader

    version = DatasetPublisher(root).current().version
    with DatasetReader(root).open(version) as context:
        assert "security_master_coverage" not in context.tables
        master = context.read("security_master")
    assert set(master["list_status"]) == {"NOT_APPLIED"}
    assert master["list_date"].isna().all()
    assert master["delist_date"].isna().all()


def test_fixture_dataset_carries_master_coverage_rows(fixture_root):
    """The default fixture publishes one security_master_coverage row per symbol."""
    from stock_quant.data_model.dataset import DatasetReader

    with DatasetReader(fixture_root.root).open(fixture_root.version) as context:
        master = context.read("security_master")
        coverage = context.read("security_master_coverage")
    assert set(master["list_status"]) == {"L"}
    assert not coverage.empty
    assert set(coverage["symbol"]) == set(master["symbol"])
    assert set(coverage["list_status"]) == {"L"}


def test_research_command_is_the_only_publisher_of_formal_experiments(
    cli_runner, fixture_root
):
    """Backtest/debug paths must not advance the experiments registry."""
    outcome = run_offline_fixture(fixture_root.root)
    manifest_path = outcome.path / "experiment_manifest.json"
    assert manifest_path.is_file()
    assert outcome.path.is_dir()


@pytest.fixture(scope="module")
def quality_report_project(tmp_path_factory):
    """One fresh project with one completed experiment for report-build tests.

    Module-scoped so the two report tests share a single (expensive) research
    run; the second test prunes the run's backtest workspace, so it must come
    after the first test in this module and nothing else may reuse the project.
    """
    project = build_fixture_project(tmp_path_factory.mktemp("quality_report"))
    outcome = run_offline_fixture(project.root)
    return project, outcome


def test_report_build_renders_quality_html_with_markers_and_no_external_refs(
    cli_runner, quality_report_project, monkeypatch
):
    """``report build`` also emits the data-quality HTML for the pinned version."""
    project, outcome = quality_report_project
    monkeypatch.setenv("TUSHARE_TOKEN", "report-secret-token")
    result = cli_runner.invoke(
        app,
        ["report", "build", "--root", str(project.root)],
    )
    assert result.exit_code == 0, result.stdout
    assert "FAILED" not in result.stdout
    assert "report=" in result.stdout
    assert "quality_report=" in result.stdout

    quality_path = (
        project.root / "data" / "reports" / f"quality-{project.version}.html"
    )
    assert quality_path.is_file(), f"{quality_path} was not written"
    html = quality_path.read_text(encoding="utf-8")
    assert "数据质量报告" in html
    assert project.version in html
    assert "门禁决定：PASS" in html
    assert "来源与版本状态" in html
    # self-contained: no external fetches, and no secret value leaks in.
    assert "src=\"http" not in html
    assert "href=\"http" not in html
    assert "report-secret-token" not in html

    experiment_path = (
        project.root / "data" / "reports" / f"{outcome.experiment_id}.html"
    )
    assert experiment_path.is_file()


def test_report_build_fails_when_backtest_workspace_pruned(
    cli_runner, quality_report_project
):
    """A pruned run workspace must fail loudly, not render an empty report."""
    project, outcome = quality_report_project
    metrics = json.loads(
        (outcome.path / "metrics.json").read_text(encoding="utf-8")
    )
    run_id = metrics["meta"]["run_id"]
    scenario_dir = project.root / "data" / "runs" / run_id / "backtest" / "zero_cost"
    assert scenario_dir.is_dir()
    shutil.rmtree(scenario_dir)
    result = cli_runner.invoke(
        app,
        ["report", "build", "--root", str(project.root)],
    )
    assert result.exit_code != 0
    assert "FAILED" in result.stdout
    assert "backtest workspace was pruned" in result.stdout


# --------------------------------------------------------------------------- #
# Task 3: the corporate-action trust boundary on the public surface
# --------------------------------------------------------------------------- #


def test_formal_research_has_no_bypass(cli_runner):
    """Formal ``research run`` must never expose an ``--engineering`` bypass."""
    result = cli_runner.invoke(app, ["research", "run", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "--engineering" not in result.output


def test_debug_and_report_show_untrusted_reason(
    cli_runner, broken_fixture_root, tmp_path
):
    """The debug backtest surfaces the frozen UNTRUSTED decision on stdout, and
    the report renders the same untrusted reason with the affected symbol."""
    result = cli_runner.invoke(
        app,
        [
            "backtest",
            "momentum_60d",
            "--root",
            str(broken_fixture_root.root),
            "--engineering",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "trust=UNTRUSTED" in result.output

    report_path = render_experiment_report(
        _experiment_input(
            corporate_action_trust={
                "trusted": False,
                "reasons": [{"symbol": "600000.SH", "code": "SOURCE_FETCH_FAILED"}],
            }
        ),
        tmp_path / "untrusted.html",
    )
    html = report_path.read_text(encoding="utf-8")
    assert "数据可信度未通过" in html
    assert "600000.SH" in html
    assert "SOURCE_FETCH_FAILED" in html


def test_fixture_project_dataset_publishes_trusted_coverage(fixture_root):
    """The shared fixture dataset itself must carry trusted coverage evidence.

    Without it the default RESEARCH runs in the CLI / end-to-end suite fail the
    corporate-action trust gate before any backtest (SOURCE_NOT_REQUESTED).
    """
    from stock_quant.data_model.dataset import DatasetReader

    with DatasetReader(fixture_root.root).open(fixture_root.version) as context:
        coverage = context.read("corporate_action_coverage")
    assert not coverage.empty
    assert set(coverage["status"]) == {"VERIFIED_EMPTY"}


# --------------------------------------------------------------------------- #
# Task 6: point-in-time index membership on the operator surface
# --------------------------------------------------------------------------- #

#: The formal spec written into the membership fixture project: the same short
#: momentum spec the other CLI tests run, but naming the frozen csi300
#: definition (``universe_definition: csi300``) so the run preflights it.
_BAD_UNIVERSE_SPEC = "momentum_60d_csi300_unverified.yml"

_FACTS_START = date(2018, 1, 2)
_FACTS_ANNOUNCED = date(2017, 12, 15)
_FACTS_END = date(2022, 1, 7)
_RULES_VERSION = "csi-index-rules-cli-fixture-2026h2"

# One evidence-backed open csi300 fact, following the Task 4 fixture shape.
def _fact_payload(symbol: str) -> dict:
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


@pytest.fixture(scope="module")
def membership_project(tmp_path_factory):
    """A synthetic project whose dataset carries *insufficient* csi300 evidence.

    Built exactly like ``fixture_root``, then republished with an evidenced
    ``universe_membership`` table (the 30 fixture symbols only) and a REAL
    ``configs/universes/csi300.yml`` definition pinned to exactly those facts.
    The definition itself is valid; the evidence fails the mandatory csi300
    cardinality check (30 members vs 300 expected), so a formal run naming the
    definition must stop at ``universe_acceptance`` before any factor work --
    the operator-facing shape of "a wrong member count stops work".
    """
    from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
    from stock_quant.data_model.universe import Universe
    from stock_quant.data_model.universe_membership import (
        membership_content_hash,
        membership_frame,
    )
    from stock_quant.data_quality.models import QualityReport

    project = build_fixture_project(tmp_path_factory.mktemp("membership"))
    universe = Universe.from_yaml(project.root / "configs" / "universe.yml")
    facts = [_fact_payload(entry.symbol) for entry in universe.entries]

    reader = DatasetReader(project.root)
    version = DatasetPublisher(project.root).current().version
    with reader.open(version) as context:
        tables = {name: context.read(name) for name in context.tables}
    tables["universe_membership"] = membership_frame(facts)
    DatasetPublisher(project.root).publish(tables, QualityReport())

    definition = {
        "schema_version": 1,
        "universe_id": "csi300",
        "rules_version": _RULES_VERSION,
        "membership_table_sha256": membership_content_hash(facts),
        "coverage_start": _FACTS_START.isoformat(),
        "coverage_end": _FACTS_END.isoformat(),
        "evidence_summary_sha256": "cd" * 32,
    }
    (project.root / "configs" / "universes").mkdir(parents=True, exist_ok=True)
    (project.root / "configs" / "universes" / "csi300.yml").write_text(
        yaml.safe_dump(definition, sort_keys=True), encoding="utf-8"
    )
    (project.root / "configs" / "experiments" / _BAD_UNIVERSE_SPEC).write_text(
        _BAD_SPEC_YAML, encoding="utf-8"
    )
    return project


#: Same frozen spec the offline fixtures run, plus the formal universe
#: definition name. The date range stays fully inside the synthetic bars.
_BAD_SPEC_YAML = """\
# 离线验收用：指向冻结 csi300 定义的正式规格，但数据集只携带不足的成分证据
#（30 只 vs 300 预期）——按操作规程必须在 universe_acceptance 处大声停止。
hypothesis: >-
  过去 60 个交易日的复权收益在合成样本内对随后短期收益存在持续性；
  仅验证离线 CLI 工程链路可复现并跑通全部运行状态，不构成投资建议。
factor_versions:
  momentum_60d: 1.0.0
dataset_version: CURRENT
universe_version: CURRENT
universe_definition: csi300
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


def test_membership_preflight_returns_nonzero_without_factor(
    cli_runner, membership_project
):
    """Formal research over insufficient membership evidence stops loudly.

    The operator rule under test: a wrong member count is a STOP, never a
    bypass. ``research run`` must exit non-zero, name the
    ``universe_acceptance`` stage, leave only the redacted preflight manifest
    under ``data/runs`` and never publish an experiment or factor artifact.
    """
    result = cli_runner.invoke(
        app,
        [
            "research",
            "run",
            "--spec",
            f"configs/experiments/{_BAD_UNIVERSE_SPEC}",
            "--root",
            str(membership_project.root),
        ],
    )
    assert result.exit_code != 0
    assert "universe_acceptance" in result.output

    runs = membership_project.root / "data" / "runs"
    manifests = sorted(runs.glob("run_preflight_*/universe_preflight.json"))
    assert manifests, "the failed run must write its redacted preflight manifest"
    record = json.loads(manifests[-1].read_text(encoding="utf-8"))
    assert record["failed_stage"] == "universe_acceptance"
    assert record["error_codes"], "the rejection must name its stable codes"

    # The redacted preflight workspace is the ONLY run artifact: no factor
    # stage ever started and no experiment was published.
    assert sorted(path.name for path in runs.iterdir()) == [
        manifests[-1].parent.name
    ]
    experiments = membership_project.root / "data" / "experiments"
    published = (
        sorted(path.name for path in experiments.iterdir())
        if experiments.is_dir()
        else []
    )
    assert published == []


def test_membership_import_requires_snapshot_hash(cli_runner, tmp_path):
    """``data index-membership prepare`` refuses unevidenced imports.

    The snapshot hash is a mandatory argument: calling prepare without it is a
    usage error that names the missing flag, so an import can never publish
    facts that are not bound to a stored raw snapshot.
    """
    source_file = tmp_path / "members.csv"
    source_file.write_text("symbol\n600000.SH\n000001.SZ\n", encoding="utf-8")
    result = cli_runner.invoke(
        app,
        [
            "data",
            "index-membership",
            "prepare",
            "--universe-id",
            "csi300",
            "--input",
            str(source_file),
        ],
    )
    assert result.exit_code != 0
    assert "--snapshot-sha256" in result.output


def test_membership_import_prepare_writes_canonical_facts(cli_runner, tmp_path):
    """The documented happy path: prepare prints the hash a definition pins.

    ``prepare`` binds every row to the operator-supplied snapshot/document
    evidence, writes the canonical ``universe_membership`` frame and prints
    the ``membership_table_sha256=`` line the frozen universe definition must
    pin (see README / RUNBOOK). It performs no network access.
    """
    source_file = tmp_path / "members.csv"
    source_file.write_text("symbol\n600000.SH\n000001.SZ\n", encoding="utf-8")
    output = tmp_path / "membership" / "universe_membership.parquet"
    result = cli_runner.invoke(
        app,
        [
            "data",
            "index-membership",
            "prepare",
            "--universe-id",
            "csi300",
            "--input",
            str(source_file),
            "--snapshot-sha256",
            "a1" * 32,
            "--source-document-sha256",
            "b2" * 32,
            "--source",
            "csi_index_announcement",
            "--source-url",
            "https://www.csindex.com.cn/announcement-2017-12.pdf",
            "--effective-date",
            "2018-01-02",
            "--announcement-date",
            "2017-12-15",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "rows=2" in result.output
    assert "universe_id=csi300" in result.output
    assert "membership_table_sha256=" in result.output
    assert output.is_file()

    from stock_quant.data_model.universe_membership import (
        membership_content_hash,
    )

    printed_hash = next(
        line.split("=", 1)[1]
        for line in result.output.splitlines()
        if line.startswith("membership_table_sha256=")
    )
    assert printed_hash == membership_content_hash(
        [_fact_payload("600000.SH"), _fact_payload("000001.SZ")]
    )
