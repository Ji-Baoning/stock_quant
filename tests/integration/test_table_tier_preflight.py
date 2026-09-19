"""Criterion 1/2 (consumer side): RESEARCH runs reject research_only and
untrusted-anchored inputs; ENGINEERING runs proceed with the exemption.

The scenario runs the real :class:`ResearchRunner` over one synthetic fixture
project (``conftest.build_fixture_project``) whose ``configs/sources.yml``
gains one extra ``industry_classify: research_only`` declaration in the test's
temporary project tree (the repository template declares all nine tables
core and is never modified).  The probe factor is a real :class:`Momentum60`
subclass whose declared ``inputs`` add the research_only table, so the
RESEARCH run is rejected by the table-tier preflight before any factor work
while the ENGINEERING diagnostic still replays the full pipeline and is
labeled RESEARCH-ONLY.  All fixtures are offline and live under ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from conftest import build_fixture_project

from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.factors.momentum import Momentum60
from stock_quant.research.models import ResearchRunFailed
from stock_quant.research.runner import ResearchRunner

_PROBE_SPEC = "configs/experiments/tier_probe.yml"


class _ResearchOnlyProbe(Momentum60):
    """Momentum60 read surface whose declared inputs add a research_only table.

    ``compute`` is inherited unchanged, so the ENGINEERING diagnostic replays
    real adjusted-bar momentum; the research_only entry in ``inputs`` is what
    the table-tier preflight routes through the tier policy.
    """

    name = "industry_probe"
    inputs = ("adjusted_bar", "industry_classify")


class _CountingProvider:
    """Provides the probe factor once per call and counts the invocations."""

    def __init__(self) -> None:
        self.calls = 0

    def provide(self) -> dict[str, _ResearchOnlyProbe]:
        self.calls += 1
        factor = _ResearchOnlyProbe()
        return {factor.name: factor}


def _append_contract(project_root: Path, *, table: str, tier: str) -> None:
    """Append one research_only-style declaration to the fixture's sources.yml.

    The fixture project lives entirely under ``tmp_path``; the repository's
    own ``sources.yml`` files are never touched.  The appended row uses the
    exact D2 field set (every field required) so it passes
    ``parse_data_contracts`` like an operator declaration would.
    """
    path = Path(project_root) / "configs" / "sources.yml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["data_contracts"].append(
        {
            "table": table,
            "tier": tier,
            "primary_transport": "tushare:relay",
            "anchors": [],
            "conflict": "block",
            "pit": None,
            "coverage_shape": "none",
            "incremental": "change_driven_full",
        }
    )
    path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _write_probe_spec(project_root: Path) -> str:
    """A single-window spec over the fixture dataset naming the probe factor.

    Mirrors the fixture spec's shape (``conftest._FIXTURE_SPEC_YAML``); only
    the hypothesis and ``factor_versions`` change.
    """
    path = Path(project_root) / _PROBE_SPEC
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "hypothesis": (
                    "table-tier preflight probe declaring a research_only "
                    "input; offline synthetic sample, not a recommendation"
                ),
                "factor_versions": {"industry_probe": "2.0.0"},
                "dataset_version": "CURRENT",
                "universe_version": "CURRENT",
                "data_acceptance_id": "CURRENT_ACCEPTED",
                "date_range": {
                    "start_date": "2020-01-01",
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
                "cost_scenarios": ["zero_cost", "commission_tax", "full_cost"],
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
    return _PROBE_SPEC


def test_research_run_rejects_research_only_input(tmp_path):
    project = build_fixture_project(tmp_path / "project")
    _append_contract(project.root, table="industry_classify", tier="research_only")
    spec_path = _write_probe_spec(project.root)
    provider = _CountingProvider()
    runner = ResearchRunner(project.root, factor_provider=provider.provide)
    with pytest.raises(ResearchRunFailed, match="table_tiers"):
        runner.run(spec_path, trust_mode="research")
    # The rejection is audited under the deterministic preflight run id with
    # the stable error code, before any factor work and without touching
    # the published CURRENT pointer.
    manifests = sorted(
        (project.root / "data" / "runs").glob(
            "run_preflight_*/table_tier_preflight.json"
        )
    )
    assert len(manifests) == 1
    payload = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert payload["failed"] is True
    assert payload["mode"] == "research"
    assert payload["violations"] == ["table_tier_research_only"]
    assert payload["research_only_used"] is True
    assert payload["label"] is None
    assert "industry_classify" in payload["input_tables"]
    state = runner.latest_run_manifest()
    assert state.status == "FAILED"
    assert state.failed_stage == "table_tiers"
    # No run workspace beyond the preflight audit: the provider is touched
    # once (reading the declared inputs), but no factor stage ever runs.
    run_dirs = [path.name for path in (project.root / "data" / "runs").iterdir()]
    assert run_dirs and all(
        name.startswith("run_preflight_") for name in run_dirs
    )
    assert DatasetPublisher(project.root).current().version == project.version
    assert not runner.partial_experiment_exists()


def test_engineering_run_accepts_research_only_with_label(tmp_path):
    project = build_fixture_project(tmp_path / "project")
    _append_contract(project.root, table="industry_classify", tier="research_only")
    spec_path = _write_probe_spec(project.root)
    runner = ResearchRunner(
        project.root, factor_provider=_CountingProvider().provide
    )
    published = runner.run(spec_path, trust_mode="engineering")
    # The diagnostic run completes and its run workspace carries the PASS
    # preflight record labeled RESEARCH-ONLY (engineering exemption).
    record = json.loads(
        (project.root / "data" / "runs" / runner.run_id
         / "table_tier_preflight.json").read_text(encoding="utf-8")
    )
    assert record["failed"] is False
    assert record["mode"] == "engineering"
    assert record["violations"] == []
    assert record["research_only_used"] is True
    assert record["engineering_exempt"] is True
    assert record["label"] == "RESEARCH-ONLY"
    assert sorted(record["input_tables"]) == [
        "adjusted_bar",
        "industry_classify",
    ]
    assert published.manifest.dataset_version == project.version
