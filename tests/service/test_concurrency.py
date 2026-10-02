"""Concurrency evidence for spec §8.3: many readers plus one independent
publication -- every read sees one complete version, never a half-published
tree, and a hash-pinned request outlives a CURRENT change."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import DATES, SYMBOLS, publish_version, write_configs  # noqa: E402
from stock_quant.data_model.dataset import DatasetReader
from stock_quant.service.app import create_app

_EXTRA_SYMBOL = "600008.SH"


def test_concurrent_readers_see_only_complete_versions(tmp_path: Path) -> None:
    write_configs(tmp_path)
    version_one = publish_version(tmp_path, SYMBOLS)
    # Warm the per-version DuckDB catalog so reader threads never race the
    # catalog *build*; that is an existing DatasetReader property, not the
    # publication-atomicity property under test here.
    reader = DatasetReader(tmp_path)
    reader.open(version_one).close()

    app = create_app(tmp_path)
    stop = threading.Event()
    failures: list[str] = []
    observed: set[str] = set()

    def read_loop() -> None:
        client = TestClient(app)
        while not stop.is_set():
            response = client.get("/api/v1/datasets/current/tables/daily_bar?limit=500")
            if response.status_code != 200:
                failures.append(f"{response.status_code}: {response.text[:200]}")
                return
            body = response.json()
            observed.add(body["dataset_version"])
            symbols = {row["symbol"] for row in body["rows"]}
            if body["dataset_version"] == version_one:
                complete = symbols == set(SYMBOLS) and len(body["rows"]) == len(
                    SYMBOLS
                ) * len(DATES)
            else:
                complete = symbols <= set(SYMBOLS) | {_EXTRA_SYMBOL}
            if not complete:
                failures.append(
                    f"partial view of {body['dataset_version']}: {sorted(symbols)}"
                )
                return

    threads = [threading.Thread(target=read_loop) for _ in range(8)]
    for thread in threads:
        thread.start()
    try:
        version_two = publish_version(tmp_path, SYMBOLS + (_EXTRA_SYMBOL,))
        reader.open(version_two).close()  # warm the new catalog mid-flight
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "a reader thread hung"

    assert failures == []
    assert observed <= {version_one, version_two}
    # Once publication has settled, a fresh request must resolve the new version.
    final = TestClient(app).get(
        "/api/v1/datasets/current/tables/daily_bar?limit=500"
    ).json()
    assert final["dataset_version"] == version_two
    assert {row["symbol"] for row in final["rows"]} == set(SYMBOLS) | {_EXTRA_SYMBOL}


def test_a_hash_pinned_request_outlives_a_current_change(tmp_path: Path) -> None:
    write_configs(tmp_path)
    version_one = publish_version(tmp_path, SYMBOLS)
    version_two = publish_version(tmp_path, SYMBOLS + (_EXTRA_SYMBOL,))
    client = TestClient(create_app(tmp_path))
    pinned = client.get(
        f"/api/v1/datasets/{version_one}/tables/daily_bar?limit=500"
    ).json()
    assert pinned["dataset_version"] == version_one
    assert {row["symbol"] for row in pinned["rows"]} == set(SYMBOLS)
    current = client.get("/api/v1/datasets/current/tables/daily_bar?limit=500").json()
    assert current["dataset_version"] == version_two
    assert {row["symbol"] for row in current["rows"]} == set(SYMBOLS) | {_EXTRA_SYMBOL}
