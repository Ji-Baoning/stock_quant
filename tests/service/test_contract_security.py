"""OpenAPI contract freeze and the fail-closed security battery (spec §8.3).

This file pins what Tasks 3/4 built: any red here is an implementation
gap -- fix the service, never the assertion.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import SYMBOLS  # noqa: E402
from stock_quant.service.app import create_app, register_error_handlers
from stock_quant.service.errors import ServiceConflict

EXPECTED_PATHS = {
    "/api/v1/health",
    "/api/v1/datasets",
    "/api/v1/datasets/{version}",
    "/api/v1/datasets/{version}/quality",
    "/api/v1/datasets/{version}/tables/{table}",
    "/api/v1/datasets/{version}/benchmark",
    "/api/v1/experiments",
    "/api/v1/experiments/summaries",
    "/api/v1/experiments/{experiment_id}/results",
    "/api/v1/experiments/{experiment_id}/folds/{fold_id}/equity",
    "/api/v1/experiments/{experiment_id}/report",
}

EXPECTED_SCHEMAS = {
    "HealthResponse",
    "DatasetsListResponse",
    "DatasetDetailResponse",
    "QualityViewResponse",
    "TablePreviewResponse",
    "BenchmarkResponse",
    "BenchmarkRow",
    "ExperimentsListResponse",
    "ExperimentResultsResponse",
    "FoldEquityResponse",
    "AggregateRow",
    "DisplayExtremes",
    "ExperimentSummaryRow",
    "ExperimentSummariesResponse",
    "ErrorResponse",
}


def test_openapi_freezes_the_api_v1_surface(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]) == EXPECTED_PATHS
    for path, operations in schema["paths"].items():
        assert set(operations) <= {"get"}, path
        assert "requestBody" not in operations.get("get", {})
    assert EXPECTED_SCHEMAS <= set(schema["components"]["schemas"])


def test_the_409_conflict_envelope_is_contracted(service_project: Path) -> None:
    """P3 has no write verb to conflict; the 409 envelope P4 will raise is
    frozen here so both surfaces share one error vocabulary (spec §8.3)."""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/_probe")
    def _probe() -> dict:
        raise ServiceConflict("another update holds the lock")

    response = TestClient(app).get("/_probe")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "update_already_running"


def test_every_versioned_endpoint_echoes_the_resolved_full_hash(
    client: TestClient, current_version: str
) -> None:
    for path in (
        "/api/v1/datasets/current",
        "/api/v1/datasets/current/quality?limit=1",
        "/api/v1/datasets/current/tables/daily_bar?limit=1",
    ):
        body = client.get(path).json()
        assert body["dataset_version"] == current_version, path
        assert body["requested_version"] == "current", path


def test_pagination_arguments_are_echoed(client: TestClient) -> None:
    quality = client.get("/api/v1/datasets/current/quality?offset=2&limit=1").json()
    assert (quality["offset"], quality["limit"]) == (2, 1)
    preview = client.get(
        "/api/v1/datasets/current/tables/daily_bar?offset=3&limit=2"
    ).json()
    assert preview["arguments"]["offset"] == 3
    assert preview["arguments"]["limit"] == 2


def test_the_404_vocabulary_is_stable(client: TestClient) -> None:
    cases = {
        "/api/v1/datasets/" + "f" * 64: "dataset_not_found",
        "/api/v1/datasets/current/tables/no_such_table": "unknown_table",
        f"/api/v1/experiments/{'b' * 64}/report": "experiment_not_found",
    }
    for path, code in cases.items():
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == code, path


def test_the_422_vocabulary_is_stable(client: TestClient) -> None:
    cases = {
        "/api/v1/datasets/short": "invalid_request",
        "/api/v1/datasets/current/tables/daily_bar?limit=9999": "invalid_request",
        "/api/v1/datasets/current/tables/daily_bar?columns=nope": "unknown_column",
        "/api/v1/datasets/current/tables/trading_calendar?symbol=600000.SH": (
            "unsupported_filter"
        ),
    }
    for path, code in cases.items():
        response = client.get(path)
        assert response.status_code == 422, path
        assert response.json()["error"]["code"] == code, path


def test_path_traversal_cannot_escape_the_project(client: TestClient) -> None:
    for path in (
        "/api/v1/datasets/current/tables/..%2F..%2Fetc%2Fpasswd",
        "/api/v1/experiments/..%2F..%2Fetc%2Fpasswd/report",
        "/api/v1/experiments/%2e%2e%2e/report",
    ):
        response = client.get(path)
        assert response.status_code in (404, 422), path
        assert response.json()["error"]["code"] in {
            "unknown_table",
            "invalid_request",
            "not_found",
        }, path


def test_sql_injection_is_rejected_in_identifiers_and_inert_in_values(
    client: TestClient,
) -> None:
    identifier = client.get(
        "/api/v1/datasets/current/tables/daily_bar",
        params={"columns": 'symbol"; DROP TABLE daily_bar; --'},
    )
    assert identifier.status_code == 422
    assert identifier.json()["error"]["code"] == "unknown_column"
    value = client.get(
        "/api/v1/datasets/current/tables/daily_bar",
        params={"symbol": "600000.SH' OR '1'='1"},
    )
    assert value.status_code == 200
    assert value.json()["rows"] == []


def test_a_corrupt_dataset_manifest_fails_closed(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    manifest = (
        service_project / "data" / "standardized" / current_version
        / "dataset_manifest.json"
    )
    original = manifest.read_text(encoding="utf-8")
    manifest.write_text("{ this is not json", encoding="utf-8")
    try:
        for path in (
            f"/api/v1/datasets/{current_version}",
            f"/api/v1/datasets/{current_version}/tables/daily_bar",
        ):
            response = client.get(path)
            assert response.status_code == 500, path
            assert response.json()["error"]["code"] == "dataset_manifest_unreadable"
    finally:
        manifest.write_text(original, encoding="utf-8")


def test_a_corrupt_quality_report_fails_closed(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    report = (
        service_project / "data" / "standardized" / current_version
        / "quality_report.json"
    )
    original = report.read_text(encoding="utf-8")
    report.write_text("[", encoding="utf-8")
    try:
        response = client.get("/api/v1/datasets/current/quality")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "quality_report_unreadable"
    finally:
        report.write_text(original, encoding="utf-8")


def test_a_corrupt_acceptance_record_fails_closed(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    directory = (
        service_project / "data" / "acceptances" / current_version / ("c" * 64)
    )
    directory.mkdir(parents=True)
    (directory / "acceptance.json").write_text("not json", encoding="utf-8")
    response = client.get("/api/v1/datasets/current")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "acceptance_record_unreadable"


def test_a_corrupt_experiment_manifest_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    manifest = (
        service_project / "data" / "experiments" / ("e" * 64)
        / "experiment_manifest.json"
    )
    manifest.write_text("[", encoding="utf-8")
    response = client.get("/api/v1/experiments")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "experiment_manifest_unreadable"


def test_responses_never_leak_absolute_paths(
    client: TestClient, service_project: Path
) -> None:
    for path in (
        "/api/v1/health",
        "/api/v1/datasets",
        "/api/v1/datasets/current",
        "/api/v1/datasets/current/quality",
        "/api/v1/datasets/current/tables/daily_bar",
    ):
        body = client.get(path).text
        assert str(service_project) not in body, path
        assert "/home/" not in body, path
