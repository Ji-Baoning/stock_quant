"""Contract tests for the S1 read-only experiment results endpoints."""

from __future__ import annotations

from pathlib import Path

from conftest import publish_experiment  # noqa: E402
from fastapi.testclient import TestClient


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
