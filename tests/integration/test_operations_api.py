"""Operations API contract over real loopback HTTP (spec 9.3).

The app is served by uvicorn in a thread on an ephemeral loopback port and
hit with stdlib urllib, so the loopback-only bind is part of what is
tested.  Everything is offline: the spawned ``operations update`` child
fails on the missing transport exactly like the CLI tests, and its job
record is the assertion surface.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
import uvicorn
from conftest import build_fixture_project

from stock_quant.operations.api import create_operations_app
from stock_quant.operations.jobs import FAILED, JobStore
from stock_quant.operations.runner import read_boot_id
from stock_quant.operations.serve import DEFAULT_PORT, is_loopback
from stock_quant.operations.serve import main as serve_main
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_CODE


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _request(method: str, base: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


@pytest.fixture()
def operations(tmp_path, monkeypatch):
    monkeypatch.delenv("TUSHARE_TRANSPORT", raising=False)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    project = build_fixture_project(tmp_path / "p")
    app = create_operations_app(project.root, enabled=True)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}", project
    server.should_exit = True
    thread.join(timeout=10)


def _wait_for_terminal(project_root: Path, job_id: str, timeout: float = 120.0) -> None:
    store = JobStore(project_root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if store.get(job_id).status in {
                "SUCCEEDED",
                "FAILED",
                "CANCELLED_BY_SHUTDOWN",
            }:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} never reached a terminal status")


def test_an_empty_project_lists_no_jobs(operations):
    base, _ = operations
    status, body = _request("GET", base, "/api/v1/update-jobs")
    assert status == 200
    assert body == {"jobs": []}


def test_a_disabled_app_refuses_every_endpoint(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    app = create_operations_app(project.root, enabled=False)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    try:
        status, body = _request(
            "GET", f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}",
            "/api/v1/update-jobs",
        )
        assert status == 503
        assert body == {"error": {"code": "operations_disabled"}}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_an_invalid_parameter_is_422_and_creates_no_job(operations):
    base, project = operations
    status, body = _request(
        "POST",
        base,
        "/api/v1/update-jobs",
        {"start": "2026-9-1"},
    )
    assert status == 422
    assert body["error"]["code"] == "invalid_parameter"
    assert body["error"]["parameter"] == "start"
    assert not JobStore(project.root).root.is_dir()


def test_a_conflicting_active_job_answers_409_with_the_same_code(operations):
    base, project = operations
    store = JobStore(project.root)
    job = store.create({"entrypoint": "test"})
    store.mark_running(
        job.job_id,
        pid=999999,
        boot_id=read_boot_id(),
        now=datetime.now(timezone.utc),
    )
    status, body = _request("POST", base, "/api/v1/update-jobs", {})
    assert status == 409
    assert body["error"]["code"] == UPDATE_ALREADY_RUNNING_CODE
    assert body["error"]["job_id"] == job.job_id


def test_a_post_spawns_a_job_that_fails_offline_and_a_rerun_gets_a_new_id(
    operations,
):
    base, project = operations
    status, body = _request("POST", base, "/api/v1/update-jobs", {})
    assert status == 201
    first_id = body["job_id"]
    assert body["status"] == "QUEUED"

    _wait_for_terminal(project.root, first_id)
    status, detail = _request("GET", base, f"/api/v1/update-jobs/{first_id}")
    assert status == 200
    # 离线：内层 data update 因缺 transport 失败 → job FAILED/update_failed。
    assert detail["status"] == FAILED
    assert detail["failure_reason"] == "update_failed"
    assert isinstance(detail["stdout_tail"], str)
    assert isinstance(detail["stderr_tail"], str)

    status, body = _request("POST", base, "/api/v1/update-jobs", {})
    assert status == 201
    assert body["job_id"] != first_id  # 失败后的再次运行 = 新 job id


def test_an_unknown_job_is_404(operations):
    base, _ = operations
    status, body = _request("GET", base, "/api/v1/update-jobs/job_nope")
    assert status == 404
    assert body["error"]["code"] == "job_not_found"


def test_log_tails_are_sanitized(operations, monkeypatch):
    base, project = operations
    monkeypatch.setenv("TUSHARE_TOKEN", "sekrit-token-value")
    store = JobStore(project.root)
    job = store.create({"entrypoint": "test"})
    stdout_path, _ = store.log_paths(job.job_id)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(
        f"token sekrit-token-value at {project.root}/configs\n", encoding="utf-8"
    )
    status, detail = _request("GET", base, f"/api/v1/update-jobs/{job.job_id}")
    assert status == 200
    assert "sekrit-token-value" not in json.dumps(detail)
    assert str(project.root) not in detail["stdout_tail"]
    assert "[redacted]" in detail["stdout_tail"]
    assert "<project-root>" in detail["stdout_tail"]


def test_the_route_surface_is_exactly_the_three_endpoints(tmp_path):
    from conftest import build_fixture_project as build

    app = create_operations_app(build(tmp_path / "p").root, enabled=True)
    # 按 (path, method) 对收集：GET 与 POST /update-jobs 是两条同路径的
    # APIRoute，用 path 做 dict 键会互相覆盖（fastapi 0.128 下只剩 GET，
    # 0.141 起 include_router 延迟展开、连 .methods 都没有，得到空集）。
    api_routes = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or ())
        if route.path.startswith("/api/v1")
    }
    assert api_routes == {
        ("/api/v1/update-jobs", "POST"),
        ("/api/v1/update-jobs", "GET"),
        ("/api/v1/update-jobs/{job_id}", "GET"),
    }


def test_serve_refuses_disabled_and_non_loopback(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    assert (
        serve_main(["--root", str(project.root)]) == 1
    )  # 默认禁用：不带 --enable 拒绝启动
    assert (
        serve_main(
            ["--root", str(project.root), "--enable", "--host", "0.0.0.0"]
        )
        == 1
    )
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5")
    assert DEFAULT_PORT == 8642
