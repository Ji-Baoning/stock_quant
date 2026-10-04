"""Contract tests for the benchmark rows endpoint (v3 §7.2).

The fixture dataset's ``daily_bar`` carries only the ``conftest.SYMBOLS``
symbols -- no 000300.SH rows -- so the symbol cases run through an existing
fixture symbol; the default-symbol case pins the constant's echo instead.
"""

from __future__ import annotations

from conftest import DATES, SYMBOLS  # noqa: E402
from fastapi.testclient import TestClient


def test_benchmark_serves_daily_bar_rows_for_the_symbol(
    client: TestClient, current_version: str
) -> None:
    response = client.get(
        "/api/v1/datasets/current/benchmark",
        params={
            "symbol": SYMBOLS[0],
            "start": DATES[0].isoformat(),
            "end": DATES[-1].isoformat(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == SYMBOLS[0]
    assert body["dataset_version"] == current_version
    # Same echo rule as every datasets endpoint: "current" resolves to the
    # full hash in dataset_version, requested_version echoes the ask.
    assert body["requested_version"] == "current"
    assert len(body["rows"]) >= 1
    assert set(body["rows"][0]) == {"trade_date", "close"}
    assert all(isinstance(row["trade_date"], str) for row in body["rows"])
    assert all(isinstance(row["close"], float) for row in body["rows"])


def test_benchmark_window_filters_rows(client: TestClient) -> None:
    full = client.get(
        "/api/v1/datasets/current/benchmark", params={"symbol": SYMBOLS[0]}
    ).json()["rows"]
    narrow = client.get(
        "/api/v1/datasets/current/benchmark",
        params={
            "symbol": SYMBOLS[0],
            "start": DATES[1].isoformat(),
            "end": DATES[1].isoformat(),
        },
    ).json()["rows"]
    assert 0 < len(narrow) < len(full)
    assert {row["trade_date"] for row in narrow} == {DATES[1].isoformat()}


def test_benchmark_defaults_to_the_primary_benchmark_symbol(
    client: TestClient,
) -> None:
    """000300.SH (PRIMARY_BENCHMARK_SYMBOL's value, restated locally); the
    fixture dataset has no benchmark rows, so only the echo is observable."""
    body = client.get("/api/v1/datasets/current/benchmark").json()
    assert body["symbol"] == "000300.SH"
    assert body["rows"] == []


def test_benchmark_rejects_a_non_benchmark_symbol(client: TestClient) -> None:
    response = client.get(
        "/api/v1/datasets/current/benchmark",
        params={"symbol": SYMBOLS[0] + "' OR '1'='1"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_benchmark_unknown_version_uses_the_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/datasets/" + "9" * 64 + "/benchmark")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"
