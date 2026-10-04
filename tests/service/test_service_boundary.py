"""The service's architectural boundary: GET-only, no data pipeline, loopback."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from stock_quant.service.app import create_app
from stock_quant.service.security import NonLoopbackBindRejected, validate_bind_host


def test_every_route_is_get_only(service_project: Path) -> None:
    app = create_app(service_project)
    for route in app.routes:
        methods = set(getattr(route, "methods", set())) - {"HEAD"}
        assert methods <= {"GET"}, f"{route.path} declares {methods}"


def test_a_write_verb_on_a_defined_path_is_405(client: TestClient) -> None:
    response = client.post("/api/v1/datasets")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_importing_the_service_never_pulls_in_the_data_pipeline() -> None:
    probe = (
        "import sys, stock_quant.service; "
        "print('stock_quant.data_pipeline' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


def test_the_service_refuses_non_loopback_hosts() -> None:
    for host in ("0.0.0.0", "192.168.1.10", "example.com"):
        with pytest.raises(NonLoopbackBindRejected):
            validate_bind_host(host)


def test_the_service_accepts_loopback_hosts() -> None:
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    assert validate_bind_host("localhost") == "localhost"
    assert validate_bind_host("::1") == "::1"


def test_serve_validates_the_host_before_uvicorn_starts(
    service_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stock_quant.service.__main__ as service_main

    started: list[tuple] = []
    monkeypatch.setattr(
        service_main,
        "uvicorn",
        SimpleNamespace(run=lambda *args, **kwargs: started.append((args, kwargs))),
    )
    with pytest.raises(NonLoopbackBindRejected):
        service_main.serve(
            service_project, host="0.0.0.0", port=8321, query_budget_seconds=5.0
        )
    assert started == []


def test_importing_the_service_never_pulls_in_the_challenge_subsystem() -> None:
    probe = (
        "import sys, stock_quant.service; "
        "print('stock_quant.research.strategy_challenge' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
