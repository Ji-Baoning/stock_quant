"""Contract tests for the S1 read-only experiment results endpoints."""

from __future__ import annotations

from pathlib import Path

from conftest import publish_experiment  # noqa: E402
from fastapi.testclient import TestClient


def test_summaries_carry_verdict_hypothesis_and_aggregates(
    service_project: Path, client: TestClient
):
    publish_experiment(service_project, "v1", walk_forward=True)
    publish_experiment(service_project, "v1", experiment_id="d" * 64)  # 非 WF
    response = client.get("/api/v1/experiments/summaries")
    assert response.status_code == 200
    rows = response.json()["summaries"]
    assert len(rows) == 2
    by_id = {row["experiment_id"]: row for row in rows}
    wf = by_id["e" * 64]
    # pin-I5 five fields straight from the manifest.
    assert wf["status"] == "ACCEPTED"
    assert wf["dataset_version"] == "v1"
    assert wf["universe_version"] == "u" * 64
    assert wf["evaluation_reason"] is None
    # hypothesis comes from the published experiment_spec.yml folded scalar.
    assert wf["hypothesis"] == "fixture hypothesis"
    assert wf["stability_conclusion"] == "STABLE"
    assert wf["stability_policy_hash"] == "p" * 16
    assert wf["research_status"] == "COMPLETED"
    assert wf["canonical_scenario"] == "full_cost"
    assert wf["aggregates"][0]["sharpe_zero_rf"] == 1.5
    assert wf["aggregates"][0]["aggregate_return"] == 0.1
    assert wf["aggregates"][0]["annualization_observations"] == 250
    # Display extremes aggregate ONLY the canonical-scenario fold_metrics
    # rows: max drawdown, max reject rate, mean turnover.
    assert wf["display_extremes"] == {
        "max_per_fold_drawdown": -0.08,
        "max_reject_rate": 0.02,
        "mean_turnover": 0.35,
    }
    legacy = by_id["d" * 64]
    assert legacy["hypothesis"] is None  # 未发布 experiment_spec.yml
    assert legacy["stability_conclusion"] is None
    assert legacy["stability_policy_hash"] is None
    assert legacy["research_status"] is None
    assert legacy["canonical_scenario"] is None
    assert legacy["aggregates"] is None
    assert legacy["display_extremes"] is None


def test_results_aggregates_the_published_json_payloads(
    service_project: Path, client: TestClient
):
    publish_experiment(service_project, "v1", walk_forward=True)
    response = client.get("/api/v1/experiments/" + "e" * 64 + "/results")
    assert response.status_code == 200
    body = response.json()
    assert body["experiment_id"] == "e" * 64
    assert body["manifest"]["status"] == "ACCEPTED"
    assert body["metrics"]["walk_forward"]["stability_conclusion"] == "STABLE"
    assert body["stability_report"]["stability_policy_hash"] == "p" * 16


def test_results_returns_null_for_artifacts_the_experiment_never_published(
    service_project, client
):
    publish_experiment(service_project, "v1")  # 无 walk_forward 产物
    response = client.get("/api/v1/experiments/" + "e" * 64 + "/results")
    assert response.status_code == 200
    assert response.json()["metrics"] is None
    assert response.json()["stability_report"] is None


def test_results_unknown_experiment_is_the_stable_envelope(service_project, client):
    response = client.get("/api/v1/experiments/" + "9" * 64 + "/results")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "experiment_not_found"


def test_results_without_a_readable_manifest_is_results_not_found(
    service_project, client
):
    """A directory that exists but carries no readable manifest is not
    "aggregate with nulls": it fails closed as ``results_not_found``."""
    manifest = (
        service_project
        / "data"
        / "experiments"
        / ("e" * 64)
        / "experiment_manifest.json"
    )
    manifest.unlink()
    response = client.get("/api/v1/experiments/" + "e" * 64 + "/results")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "results_not_found"


def test_fold_equity_serves_only_declared_artifacts(service_project, client):
    """An equity parquet sitting on disk is not servable: only files inside
    the manifest's declared artifact graph are. The fixture declares a
    *non-equity* fold artifact so the fold is "present" in the graph and the
    404 is genuinely the artifact branch, not the fold branch."""
    directory = publish_experiment(service_project, "v1", walk_forward=True)
    import json as _json

    fold = "f" * 64
    key = f"folds/{fold}/backtest/full_cost/rebalance_decisions.parquet"
    manifest_path = directory / "experiment_manifest.json"
    manifest = _json.loads(manifest_path.read_text())
    manifest["artifacts"] = {key: "0" * 64}
    manifest_path.write_text(_json.dumps(manifest))
    response = client.get(f"/api/v1/experiments/{'e' * 64}/folds/{fold}/equity")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_found"


def test_fold_equity_serves_a_declared_scenario_artifact(service_project, client):
    """A declared, hash-verified scenario equity file is served with the
    whitelisted columns only; trade dates serialise as ISO dates."""
    directory = publish_experiment(service_project, "v1", walk_forward=True)
    import hashlib
    import json as _json

    manifest_path = directory / "experiment_manifest.json"
    manifest = _json.loads(manifest_path.read_text())
    key = f"folds/{'f' * 64}/backtest/full_cost/equity.parquet"
    manifest["artifacts"] = {
        key: hashlib.sha256((directory / key).read_bytes()).hexdigest()
    }
    manifest_path.write_text(_json.dumps(manifest))
    response = client.get(
        f"/api/v1/experiments/{'e' * 64}/folds/{'f' * 64}/equity?scenario=full_cost"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] == "full_cost"
    assert [row["net_equity_after_cost"] for row in body["rows"]] == [
        1_000_000.0,
        1_000_000.0,
    ]
    assert set(body["rows"][0]) == {
        "trade_date",
        "cash",
        "market_value",
        "net_equity_after_cost",
    }
    assert body["rows"][0]["trade_date"] == "2026-01-05"


def test_fold_equity_declared_hash_mismatch_is_artifact_not_found(
    service_project, client
):
    """A declared equity artifact whose bytes no longer match the manifest
    hash fails closed: the hash re-verification is load-bearing, not
    decorative."""
    directory = publish_experiment(service_project, "v1", walk_forward=True)
    import json as _json

    key = f"folds/{'f' * 64}/backtest/full_cost/equity.parquet"
    manifest_path = directory / "experiment_manifest.json"
    manifest = _json.loads(manifest_path.read_text())
    manifest["artifacts"] = {key: "0" * 64}
    manifest_path.write_text(_json.dumps(manifest))
    response = client.get(
        f"/api/v1/experiments/{'e' * 64}/folds/{'f' * 64}/equity?scenario=full_cost"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_found"


def test_fold_equity_declared_canonical_fallback_serves_the_canonical_file(
    service_project, client
):
    """With no scenario equity declared, a request without ``?scenario``
    falls back to the fold's canonical file -- but only when the manifest
    declares it; the response then carries ``scenario: null`` and the
    canonical file's own columns, not a scenario file's."""
    directory = publish_experiment(service_project, "v1", walk_forward=True)
    import hashlib
    import json as _json

    key = f"folds/{'f' * 64}/equity.parquet"
    manifest_path = directory / "experiment_manifest.json"
    manifest = _json.loads(manifest_path.read_text())
    manifest["artifacts"] = {
        key: hashlib.sha256((directory / key).read_bytes()).hexdigest()
    }
    manifest_path.write_text(_json.dumps(manifest))
    response = client.get(f"/api/v1/experiments/{'e' * 64}/folds/{'f' * 64}/equity")
    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] is None
    assert set(body["rows"][0]) == {
        "trade_date",
        "initial_equity",
        "net_equity_after_cost",
    }
    assert [row["net_equity_after_cost"] for row in body["rows"]] == [
        1_000_000.0,
        1_000_000.0,
    ]


def test_fold_equity_unknown_fold_is_fold_not_found(service_project, client):
    """A fold with nothing declared under folds/<id>/ reads as "no such
    fold" -- the fold branch of the declared-graph guard."""
    publish_experiment(service_project, "v1", walk_forward=True)
    response = client.get(f"/api/v1/experiments/{'e' * 64}/folds/{'a' * 64}/equity")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "fold_not_found"
