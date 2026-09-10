"""Consume-before-read orchestration and immutable challenge publication.

The service must publish the declaration and consume the holdout *before*
any challenger artifact is opened (proven with an event-spying loader),
publish immutable artifacts only after identity/registry/pairing checks
pass, keep the consumption on every failure, publish a null-conclusion
FAILED comparison JSON with a redacted error code, and reuse an existing
result only when every byte matches.  A module-scoped fixture publishes a
real equal-weight baseline and a real buffered challenger walk-forward
experiment over the identical offline synthetic project, so the default
registry loader is exercised end-to-end.  Everything is offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_quant.research.strategy_challenge.compare import ScenarioMetricSet
from stock_quant.research.strategy_challenge.models import (
    ChallengeDeclaration,
    StrategyComparisonPolicy,
    canonical_challenge_json_text,
    canonical_challenge_sha256,
    compute_challenge_id,
)
from stock_quant.research.strategy_challenge.registry import HoldoutRegistry
from stock_quant.research.strategy_challenge.service import (
    ChallengeExperiment,
    ChallengeServiceError,
    StrategyChallengeService,
)

SCENARIOS = ("zero_cost", "full_cost")
FOLDS = tuple(f"fold-{index}" for index in range(5))


# --------------------------------------------------------------------------- #
# Declaration builders (shared with the registry tests' conventions)
# --------------------------------------------------------------------------- #


def valid_declaration(**overrides) -> ChallengeDeclaration:
    policy = StrategyComparisonPolicy()
    payload = {
        "identity_scheme_version": "strategy-challenge-v1",
        "strategy_family": "momentum_60d",
        "baseline_experiment_id": "1a" * 32,
        "challenger_strategy_hash": "2b" * 32,
        "fold_schedule_hash": "3c" * 32,
        "universe_definition": {
            "universe_id": "csi300",
            "universe_version": "a" * 64,
            "membership_table_sha256": "b" * 64,
            "evidence_summary_sha256": "c" * 64,
        },
        "comparison_policy": policy.model_dump(mode="json"),
        "comparison_policy_hash": canonical_challenge_sha256(
            policy.model_dump(mode="json")
        ),
        "declared_before_run_at": datetime(
            2026, 9, 9, tzinfo=timezone.utc
        ).isoformat(),
    }
    payload.update(overrides)
    return ChallengeDeclaration.model_validate(payload)


@pytest.fixture
def declaration() -> ChallengeDeclaration:
    return valid_declaration()


@pytest.fixture
def declaration_file(tmp_path, declaration) -> Path:
    path = tmp_path / "declaration.json"
    path.write_text(
        canonical_challenge_json_text(declaration.model_dump(mode="json")),
        encoding="utf-8",
    )
    return path


def _flip(value: str) -> str:
    return ("0" if value[0] != "0" else "1") + value[1:]


# --------------------------------------------------------------------------- #
# Synthetic published-experiment views and the spying loader
# --------------------------------------------------------------------------- #


def synthetic_manifest(
    declaration: ChallengeDeclaration, side: str, *, universe_break: bool = False
) -> dict:
    universe = declaration.universe_definition.model_dump(mode="json")
    if universe_break:
        universe["evidence_summary_sha256"] = "f" * 64
    return {
        "experiment_id": (
            declaration.baseline_experiment_id
            if side == "baseline"
            else "9b" * 32
        ),
        "status": "COMPLETED",
        "stability_conclusion": "STABLE",
        "stability_policy_hash": "bb" * 32,
        "data_environment_snapshot_sha256": "dd" * 32,
        "strategy_snapshot_sha256": (
            declaration.challenger_strategy_hash
            if side == "challenger"
            else "11" * 32
        ),
        "experiment_snapshot_sha256": "22" * 32,
        "fold_schedule_sha256": declaration.fold_schedule_hash,
        "universe_definition": universe,
        "factor_signal_hash": "cc" * 32,
        "initial_cash": 1_000_000.0,
        "rebalance_frequency": "weekly",
        "cost_scenarios": list(SCENARIOS),
        "portfolio_rule_name": (
            "top_n_equal_weight" if side == "baseline"
            else "buffered_risk_weighted"
        ),
        "executed_fold_ids": list(FOLDS),
        "skipped_fold_ids": [],
    }


def synthetic_metric_set(side: str, *, failing: bool = False) -> ScenarioMetricSet:
    is_challenger = side == "challenger"
    rows = []
    for index, fold in enumerate(FOLDS):
        for scenario in SCENARIOS:
            broken = is_challenger and failing and scenario == "full_cost"
            rows.append({
                "fold_id": fold,
                "scenario": scenario,
                "fold_calendar_return": (0.03 if is_challenger else 0.02)
                + 0.001 * index,
                "abs_max_drawdown": 0.08 if is_challenger else 0.10,
                "turnover": 60_000.0 if is_challenger else 100_000.0,
                "explicit_cost_ratio": 0.0008 if is_challenger else 0.001,
                "reject_rate": 0.005 if is_challenger else 0.01,
                "invested_exposure": 0.97 if is_challenger else 0.95,
            })
            if broken:
                rows[-1]["fold_calendar_return"] = -0.05
    folds = pd.DataFrame(rows)
    aggregates = pd.DataFrame([
        {
            "scenario": scenario,
            "aggregate_sharpe": (
                0.1 if is_challenger and failing and scenario == "full_cost"
                else (0.80 if is_challenger else 0.50)
            ),
            "aggregate_annualized_return": (
                0.08 if is_challenger else 0.05
            ),
        }
        for scenario in SCENARIOS
    ])
    return ScenarioMetricSet(folds=folds, aggregates=aggregates)


class SpyingLoader:
    """The event-spying loader: it journals ``challenger_opened`` itself."""

    def __init__(
        self,
        declaration: ChallengeDeclaration,
        *,
        challenger_metrics: ScenarioMetricSet | None = None,
        universe_break: bool = False,
    ) -> None:
        self.events: list[str] = []
        self._declaration = declaration
        self._challenger_metrics = (
            challenger_metrics or synthetic_metric_set("challenger")
        )
        self._universe_break = universe_break

    def resolve(self, project_root, *, side, experiment_id, strategy_hash):
        if side == "challenger":
            self.events.append("challenger_opened")
            return "9b" * 32
        return experiment_id

    def load(self, project_root, experiment_id, *, side):
        if side == "baseline":
            return ChallengeExperiment(
                experiment_id=experiment_id,
                manifest=synthetic_manifest(
                    self._declaration, "baseline",
                    universe_break=self._universe_break,
                ),
                metrics=synthetic_metric_set("baseline"),
            )
        return ChallengeExperiment(
            experiment_id=experiment_id,
            manifest=synthetic_manifest(
                self._declaration, "challenger",
                universe_break=self._universe_break,
            ),
            metrics=self._challenger_metrics,
        )


@pytest.fixture
def spying_loader(declaration) -> SpyingLoader:
    return SpyingLoader(declaration)


# --------------------------------------------------------------------------- #
# Step 1: the consume-before-read ordering
# --------------------------------------------------------------------------- #


def test_service_consumes_before_opening_challenger_artifacts(
    tmp_path, declaration_file, spying_loader
):
    service = StrategyChallengeService(tmp_path, experiment_loader=spying_loader)
    result = service.run(declaration_file)
    assert spying_loader.events[:3] == [
        "declaration_published", "holdout_consumed", "challenger_opened"
    ]
    assert result.conclusion == "PROMOTED"
    assert service.events[:3] == spying_loader.events[:3]


def test_declaration_is_published_before_consumption(
    tmp_path, declaration_file, spying_loader, declaration
):
    service = StrategyChallengeService(tmp_path, experiment_loader=spying_loader)
    service.run(declaration_file)
    published = (
        tmp_path / "data" / "strategy_challenges" / "declarations"
        / f"{compute_challenge_id(declaration)}.json"
    )
    assert published.is_file()
    assert published.read_bytes() == canonical_challenge_json_text(
        declaration.model_dump(mode="json")
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# Immutable publication and failure behaviour
# --------------------------------------------------------------------------- #


def test_completed_challenge_publishes_every_artifact(
    tmp_path, declaration_file, spying_loader
):
    service = StrategyChallengeService(tmp_path, experiment_loader=spying_loader)
    result = service.run(declaration_file)
    directory = service.result_path(result.challenge_id)
    names = {
        "strategy_challenge.json",
        "holdout_consumption.json",
        "paired_fold_metrics.parquet",
        "strategy_comparison.json",
        "strategy_comparison_report.html",
    }
    assert {path.name for path in directory.iterdir()} == names
    comparison = json.loads(
        (directory / "strategy_comparison.json").read_text(encoding="utf-8")
    )
    import hashlib

    for name, digest in comparison["artifacts"].items():
        assert digest == hashlib.sha256(
            (directory / name).read_bytes()
        ).hexdigest()
    pairs = pd.read_parquet(directory / "paired_fold_metrics.parquet")
    assert len(pairs) == len(FOLDS) * len(SCENARIOS)
    # Every policy metric's threshold and flag is auditable in the parquet.
    for metric in ("aggregate_sharpe_delta", "median_invested_exposure",
                   "median_turnover_ratio"):
        assert f"{metric}_threshold" in pairs.columns
        assert f"{metric}_passed" in pairs.columns
    assert comparison["holdout_consumption"]["status"] == "consumed"
    assert comparison["declaration"] == (
        json.loads(declaration_file.read_text(encoding="utf-8"))
    )


def test_completed_challenge_rerun_reuses_identical_bytes(
    tmp_path, declaration_file, spying_loader
):
    service = StrategyChallengeService(tmp_path, experiment_loader=spying_loader)
    first = service.run(declaration_file)
    second = service.run(declaration_file)
    assert first == second


def test_tampered_published_result_is_never_overwritten(
    tmp_path, declaration_file, spying_loader
):
    service = StrategyChallengeService(tmp_path, experiment_loader=spying_loader)
    result = service.run(declaration_file)
    report = service.result_path(result.challenge_id) / (
        "strategy_comparison_report.html"
    )
    report.write_bytes(report.read_bytes() + b"<!-- tampered -->\n")
    with pytest.raises(ChallengeServiceError, match="different bytes"):
        service.run(declaration_file)


def test_conflicting_declaration_bytes_are_an_identity_failure(
    tmp_path, declaration_file, spying_loader, declaration
):
    service = StrategyChallengeService(tmp_path, experiment_loader=spying_loader)
    service.run(declaration_file)
    published = service.declaration_path(compute_challenge_id(declaration))
    published.write_bytes(b'{"tampered": true}\n')
    with pytest.raises(ChallengeServiceError, match="different bytes"):
        service.run(declaration_file)


def test_failed_challenge_publishes_null_conclusion_and_keeps_consumption(
    tmp_path, declaration_file, declaration
):
    corrupt_loader = SpyingLoader(declaration, universe_break=True)
    service = StrategyChallengeService(
        tmp_path, experiment_loader=corrupt_loader
    )
    result = service.run(declaration_file)
    consumption_path = service.consumption_path(result.challenge_id)
    assert result.status == "FAILED" and result.conclusion is None
    assert result.error_code == "IDENTITY_MISMATCH"
    assert consumption_path.is_file()
    comparison = json.loads(
        (service.result_path(result.challenge_id)
         / "strategy_comparison.json").read_text(encoding="utf-8")
    )
    assert comparison["conclusion"] is None
    assert comparison["result"]["error_code"] == "IDENTITY_MISMATCH"


def test_refused_consumer_publishes_failure_and_keeps_first_consumption(
    tmp_path, declaration_file, spying_loader, declaration
):
    registry = HoldoutRegistry(tmp_path)
    other = valid_declaration(
        challenger_strategy_hash=_flip(declaration.challenger_strategy_hash)
    )
    first = registry.consume(other)
    service = StrategyChallengeService(tmp_path, experiment_loader=spying_loader)
    result = service.run(declaration_file)
    assert result.status == "FAILED"
    assert result.conclusion is None
    assert result.error_code == "HOLDOUT_CONSUMPTION_REFUSED"
    assert service.consumption_path(first.challenge_id).is_file()
    comparison = json.loads(
        (service.result_path(result.challenge_id)
         / "strategy_comparison.json").read_text(encoding="utf-8")
    )
    assert comparison["conclusion"] is None
    assert comparison["holdout_consumption"] is None


def test_report_contains_every_failed_threshold(
    tmp_path, declaration_file, declaration
):
    rejected_loader = SpyingLoader(
        declaration,
        challenger_metrics=synthetic_metric_set("challenger", failing=True),
    )
    service = StrategyChallengeService(
        tmp_path, experiment_loader=rejected_loader
    )
    result = service.run(declaration_file)
    assert result.conclusion == "REJECTED"
    assert "full_cost" in result.failed_scenarios
    expected_failures = [
        cell.metric
        for scenario in result.scenario_results
        for cell in scenario.cells
        if not cell.passed
    ]
    assert expected_failures
    report = service.result_path(result.challenge_id)
    rendered = (report / "strategy_comparison_report.html").read_text(
        encoding="utf-8"
    )
    assert all(name in rendered for name in expected_failures)
    assert "下一组参数" not in rendered
    assert "REJECTED" in rendered


# --------------------------------------------------------------------------- #
# End-to-end over two really published experiments (identical inputs)
# --------------------------------------------------------------------------- #


def _published_experiments(root: Path) -> dict[str, dict]:
    experiments_root = root / "data" / "experiments"
    found = {}
    for path in sorted(experiments_root.iterdir()):
        if path.name == "registry.parquet" or not path.is_dir():
            continue
        manifest = json.loads(
            (path / "experiment_manifest.json").read_text(encoding="utf-8")
        )
        metrics = json.loads(
            (path / "metrics.json").read_text(encoding="utf-8")
        )
        found[metrics["meta"]["spec"]["portfolio_rule"]["name"]] = {
            "experiment_id": manifest["experiment_id"],
            "manifest": manifest,
            "metrics": metrics,
        }
    return found


def build_challenge_project(root: Path) -> SimpleNamespace:
    """Publish the equal-weight baseline and buffered challenger once.

    Both specs share the identical synthetic dataset, acceptance, universe
    definition and fold calendar, so only the portfolio construction rule
    differs.  Returns the published identities plus a ready declaration.
    """
    from conftest import _WF_SPEC_YAML, build_fixture_project
    from typer.testing import CliRunner

    from stock_quant.cli import app

    project = build_fixture_project(root)
    buffered_spec = _WF_SPEC_YAML.replace(
        "portfolio_rule:\n"
        "  name: top_n_equal_weight\n"
        "  top_n: 10\n"
        "  lot_size: 100",
        "portfolio_rule:\n  name: buffered_risk_weighted",
    )
    assert "buffered_risk_weighted" in buffered_spec
    buffered_path = (
        project.root / "configs" / "experiments" / "walk_forward_buffered.yml"
    )
    buffered_path.write_text(buffered_spec, encoding="utf-8")
    runner = CliRunner()
    for spec in (
        "configs/experiments/walk_forward.yml",
        "configs/experiments/walk_forward_buffered.yml",
    ):
        outcome = runner.invoke(
            app,
            ["research", "run", "--spec", spec, "--root", str(project.root)],
        )
        assert outcome.exit_code == 0, outcome.stdout
    published = _published_experiments(project.root)
    baseline = published["top_n_equal_weight"]
    challenger = published["buffered_risk_weighted"]
    universe = {
        field: str(challenger["metrics"]["meta"]["universe"][field])
        for field in (
            "universe_id",
            "universe_version",
            "membership_table_sha256",
            "evidence_summary_sha256",
        )
    }
    policy = StrategyComparisonPolicy()
    declaration = ChallengeDeclaration.model_validate({
        "identity_scheme_version": "strategy-challenge-v1",
        "strategy_family": "momentum_60d",
        "baseline_experiment_id": baseline["experiment_id"],
        "challenger_strategy_hash":
            challenger["manifest"]["strategy_snapshot_sha256"],
        "fold_schedule_hash": challenger["manifest"]["fold_schedule_sha256"],
        "universe_definition": universe,
        "comparison_policy": policy.model_dump(mode="json"),
        "comparison_policy_hash": canonical_challenge_sha256(
            policy.model_dump(mode="json")
        ),
        "declared_before_run_at": datetime(
            2026, 9, 9, tzinfo=timezone.utc
        ).isoformat(),
    })
    declaration_path = project.root / "strategy_challenge_declaration.json"
    declaration_path.write_text(
        canonical_challenge_json_text(declaration.model_dump(mode="json")),
        encoding="utf-8",
    )
    return SimpleNamespace(
        root=project.root,
        baseline=baseline,
        challenger=challenger,
        declaration=declaration,
        declaration_path=declaration_path,
    )


@pytest.fixture(scope="module")
def challenge_project(tmp_path_factory) -> SimpleNamespace:
    return build_challenge_project(tmp_path_factory.mktemp("challenge_e2e"))


def test_real_challenge_is_inconclusive_and_publishes_everything(
    challenge_project,
):
    project = challenge_project
    service = StrategyChallengeService(project.root)
    result = service.run(project.declaration_path)
    assert result.status == "COMPLETED"
    assert result.conclusion == "INCONCLUSIVE_RESEARCH_ONLY"
    assert result.challenge_id == compute_challenge_id(project.declaration)
    directory = service.result_path(result.challenge_id)
    assert all(
        (directory / name).is_file()
        for name in (
            "strategy_challenge.json",
            "holdout_consumption.json",
            "paired_fold_metrics.parquet",
            "strategy_comparison.json",
            "strategy_comparison_report.html",
        )
    )
    comparison = json.loads(
        (directory / "strategy_comparison.json").read_text(encoding="utf-8")
    )
    assert comparison["baseline_experiment_id"] == (
        project.baseline["experiment_id"]
    )
    assert comparison["challenger_experiment_id"] == (
        project.challenger["experiment_id"]
    )
    assert comparison["snapshot_hashes"]["baseline"]["strategy_snapshot_sha256"]
    assert comparison["snapshot_hashes"]["challenger"]["strategy_snapshot_sha256"]
    rendered = (directory / "strategy_comparison_report.html").read_text(
        encoding="utf-8"
    )
    assert "INCONCLUSIVE_RESEARCH_ONLY" in rendered
    assert "holdout" in rendered
    # The identical rerun is an idempotent recovery, not a second challenge.
    again = StrategyChallengeService(project.root).run(project.declaration_path)
    assert again == result


def test_real_challenge_pairs_only_executed_folds(challenge_project):
    project = challenge_project
    service = StrategyChallengeService(project.root)
    result = service.run(project.declaration_path)
    pairs = pd.read_parquet(
        service.result_path(result.challenge_id)
        / "paired_fold_metrics.parquet"
    )
    executed = result.executed_fold_count
    declared = result.declared_scenario_count
    assert len(pairs) == executed * declared
    assert pairs["scenario"].nunique() == declared
