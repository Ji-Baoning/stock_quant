"""The whitelisted table preview: echo, filters, caps, budget, per-request
context (spec §8.2)."""

from __future__ import annotations

import re

import pytest
from conftest import DATES, SYMBOLS  # noqa: E402
from fastapi.testclient import TestClient

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS, DatasetReader
from stock_quant.service.app import create_app
from stock_quant.service.tables import QueryTimeBudgetExceeded, run_with_budget

_DAILY_COLUMNS = [field.name for field in STANDARDIZED_SCHEMAS["daily_bar"]]


def test_default_preview_echoes_the_full_hash_and_arguments(
    client: TestClient, current_version: str
) -> None:
    body = client.get("/api/v1/datasets/current/tables/daily_bar").json()
    assert body["dataset_version"] == current_version
    assert re.fullmatch(r"[0-9a-f]{64}", body["dataset_version"])
    assert body["table"] == "daily_bar"
    assert body["arguments"] == {
        "requested_version": "current",
        "table": "daily_bar",
        "columns": _DAILY_COLUMNS,
        "symbol": None,
        "trade_date": None,
        "offset": 0,
        "limit": 100,
    }
    assert body["columns"] == _DAILY_COLUMNS
    assert len(body["rows"]) == len(DATES) * len(SYMBOLS)
    assert {row["symbol"] for row in body["rows"]} == set(SYMBOLS)
    assert all(isinstance(row["trade_date"], str) for row in body["rows"])


def test_a_column_subset_selects_whitelisted_columns(client: TestClient) -> None:
    body = client.get(
        "/api/v1/datasets/current/tables/daily_bar?columns=symbol,close"
    ).json()
    assert body["columns"] == ["symbol", "close"]
    assert body["arguments"]["columns"] == ["symbol", "close"]
    assert set(body["rows"][0]) == {"symbol", "close"}


def test_an_unknown_column_is_a_stable_422(client: TestClient) -> None:
    response = client.get(
        "/api/v1/datasets/current/tables/daily_bar?columns=symbol,password"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_column"


def test_symbol_and_date_filters_are_bound_parameters(
    client: TestClient,
) -> None:
    body = client.get(
        "/api/v1/datasets/current/tables/daily_bar",
        params={"symbol": SYMBOLS[0], "trade_date": DATES[0].isoformat()},
    ).json()
    assert body["arguments"]["symbol"] == SYMBOLS[0]
    assert body["arguments"]["trade_date"] == DATES[0].isoformat()
    assert [row["symbol"] for row in body["rows"]] == [SYMBOLS[0]]
    assert [row["trade_date"] for row in body["rows"]] == [DATES[0].isoformat()]


def test_an_injection_payload_in_a_value_filter_is_just_a_value(
    client: TestClient,
) -> None:
    payload = "'; DROP TABLE daily_bar; --"
    body = client.get(
        "/api/v1/datasets/current/tables/daily_bar", params={"symbol": payload}
    ).json()
    assert body["rows"] == []
    still_there = client.get("/api/v1/datasets/current/tables/daily_bar?limit=1")
    assert still_there.status_code == 200


def test_a_filter_on_a_table_without_that_column_is_a_stable_422(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/datasets/current/tables/trading_calendar?trade_date=2026-01-05"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_filter"


def test_limit_is_capped_and_offset_pages(client: TestClient) -> None:
    too_large = client.get("/api/v1/datasets/current/tables/daily_bar?limit=501")
    assert too_large.status_code == 422
    zero = client.get("/api/v1/datasets/current/tables/daily_bar?limit=0")
    assert zero.status_code == 422
    page_one = client.get(
        "/api/v1/datasets/current/tables/daily_bar?limit=2&offset=0"
    ).json()
    page_two = client.get(
        "/api/v1/datasets/current/tables/daily_bar?limit=2&offset=2"
    ).json()
    whole = client.get("/api/v1/datasets/current/tables/daily_bar?limit=4").json()
    assert page_one["rows"] != page_two["rows"]
    assert page_one["rows"] + page_two["rows"] == whole["rows"]


def test_an_unknown_or_traversal_table_name_is_a_stable_404(
    client: TestClient,
) -> None:
    for table in ("no_such_table", "Daily_Bar", "daily_bar%22%20--"):
        response = client.get(f"/api/v1/datasets/current/tables/{table}")
        assert response.status_code == 404, table
        assert response.json()["error"]["code"] == "unknown_table"


def test_a_zero_budget_refuses_to_run_the_query(service_project) -> None:
    client = TestClient(create_app(service_project, query_budget_seconds=0.0))
    response = client.get("/api/v1/datasets/current/tables/daily_bar")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "query_time_budget_exceeded"
    assert response.json()["error"]["dataset_version"]  # echo survives the failure


def test_a_slow_query_is_interrupted_at_the_budget(
    service_project, current_version: str
) -> None:
    """The budget is real interruption, not a row cap: a pure-compute query
    over ``range()`` takes seconds at this size and must be cut at 0.5s."""
    reader = DatasetReader(service_project)
    with reader.open(current_version) as context:
        with pytest.raises(QueryTimeBudgetExceeded):
            run_with_budget(
                context.connection,
                "SELECT count(*) FROM range(5000000000)",
                (),
                0.5,
            )


def test_each_request_opens_and_closes_its_own_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    original = DatasetReader.open

    def counting_open(self: DatasetReader, version: str):
        opened.append(version)
        return original(self, version)

    monkeypatch.setattr(DatasetReader, "open", counting_open)
    client.get("/api/v1/datasets/current/tables/daily_bar?limit=1")
    client.get("/api/v1/datasets/current/tables/daily_bar?limit=1")
    assert len(opened) == 2
