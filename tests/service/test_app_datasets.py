"""Skeleton behaviour: health, datasets, quality, acceptance states,
experiments and the once-per-request version resolution."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from conftest import SYMBOLS, publish_acceptance_record, write_configs  # noqa: E402
from fastapi.testclient import TestClient

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.service.app import create_app

_HEX64 = re.compile(r"[0-9a-f]{64}")


def test_health_reports_a_fingerprint_not_a_path(
    client: TestClient, service_project: Path
) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert re.fullmatch(r"[0-9a-f]{16}", body["project_root_fingerprint"])
    assert str(service_project) not in json.dumps(body)


def test_datasets_list_reports_current_tables_quality_and_acceptance(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/datasets").json()
    assert body["current"] == current_version
    entry = next(
        item for item in body["datasets"] if item["dataset_version"] == current_version
    )
    assert entry["is_current"] is True
    assert entry["table_count"] == len(STANDARDIZED_SCHEMAS)
    assert entry["quality"]["by_severity"]["WARNING"] == len(SYMBOLS)
    assert entry["acceptance"] == {
        "state": "UNVERIFIED",
        "has_valid_accepted_record": False,
        "latest_verdict": None,
        "record_count": 0,
    }


def test_dataset_detail_resolves_current_once_and_echoes_the_full_hash(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/datasets/current").json()
    assert body["dataset_version"] == current_version
    assert _HEX64.fullmatch(body["dataset_version"])
    assert body["requested_version"] == "current"
    assert {table["name"] for table in body["tables"]} == set(STANDARDIZED_SCHEMAS)
    assert body["manifest"]["dataset_version"] == current_version
    assert body["acceptance"]["state"] == "UNVERIFIED"


def test_a_project_without_publications_lists_nothing(tmp_path: Path) -> None:
    write_configs(tmp_path)
    body = TestClient(create_app(tmp_path)).get("/api/v1/datasets").json()
    assert body == {"datasets": [], "current": None}


def test_current_without_a_current_file_is_a_stable_404(tmp_path: Path) -> None:
    write_configs(tmp_path)
    response = TestClient(create_app(tmp_path)).get("/api/v1/datasets/current")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_current_dataset"


def test_unknown_full_hash_version_is_a_stable_404(client: TestClient) -> None:
    response = client.get("/api/v1/datasets/" + "f" * 64)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"


def test_malformed_version_is_a_stable_422(client: TestClient) -> None:
    response = client.get("/api/v1/datasets/not-a-hash")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_quality_view_paginates_and_echoes_its_arguments(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/datasets/current/quality?offset=1&limit=2").json()
    assert body["dataset_version"] == current_version
    assert body["requested_version"] == "current"
    assert body["offset"] == 1
    assert body["limit"] == 2
    assert body["total"] == len(SYMBOLS)
    assert [issue["symbol"] for issue in body["issues"]] == [SYMBOLS[1], SYMBOLS[2]]


def test_a_later_rejected_record_never_masks_a_valid_accepted_record(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    publish_acceptance_record(
        service_project,
        current_version,
        decision="ACCEPTED",
        created_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    publish_acceptance_record(
        service_project,
        current_version,
        decision="REJECTED",
        created_at=datetime(2026, 1, 11, tzinfo=timezone.utc),
        reasons=("fixture rejection",),
    )
    body = client.get("/api/v1/datasets/current").json()["acceptance"]
    assert body["state"] == "ACCEPTED"
    assert body["has_valid_accepted_record"] is True
    assert body["latest_verdict"] == "REJECTED"
    assert body["record_count"] == 2


def test_a_rejection_without_any_accepted_record_is_rejected(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    publish_acceptance_record(
        service_project,
        current_version,
        decision="REJECTED",
        created_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
        reasons=("fixture rejection",),
    )
    body = client.get("/api/v1/datasets/current").json()["acceptance"]
    assert body == {
        "state": "REJECTED",
        "has_valid_accepted_record": False,
        "latest_verdict": "REJECTED",
        "record_count": 1,
    }


def test_prepared_worksheets_without_any_record_are_pending_confirmation(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    (service_project / "data" / "acceptance-worksheets" / current_version).mkdir(
        parents=True
    )
    body = client.get("/api/v1/datasets/current").json()["acceptance"]
    assert body == {
        "state": "PENDING_CONFIRMATION",
        "has_valid_accepted_record": False,
        "latest_verdict": None,
        "record_count": 0,
    }


def test_experiments_list_and_report_of_an_existing_experiment(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/experiments").json()
    assert body["experiments"][0]["experiment_id"] == "e" * 64
    assert body["experiments"][0]["dataset_version"] == current_version
    response = client.get(f"/api/v1/experiments/{'e' * 64}/report")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "fixture report" in response.text


def test_a_missing_experiment_or_report_is_a_stable_404(
    client: TestClient, service_project: Path, current_version: str
) -> None:
    from conftest import publish_experiment  # noqa: E402

    publish_experiment(service_project, current_version, "a" * 64, with_report=False)
    without_report = client.get(f"/api/v1/experiments/{'a' * 64}/report")
    assert without_report.status_code == 404
    assert without_report.json()["error"]["code"] == "report_not_found"
    missing = client.get(f"/api/v1/experiments/{'b' * 64}/report")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "experiment_not_found"
