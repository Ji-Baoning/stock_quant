"""CLI-visible single-flight behaviour of ``data update`` (spec 9.1/9.2).

The conflict path must be reachable fully offline: the lock is taken right
after root resolution, before any transport or credential is ever touched.
The multiprocess test below uses a real holder subprocess so "does not
queue, does not kill the first runner" is asserted against a real process,
not a mock.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from conftest import build_fixture_project

from stock_quant.cli import app
from stock_quant.operations.jobs import (
    CANCELLED_BY_SHUTDOWN,
    FAILED,
    RUNNING,
    SUCCEEDED,
    JobStore,
)
from stock_quant.operations.runner import (
    UpdateRunParams,
    operations_exit_code,
    run_operations_update,
)
from stock_quant.operations.update_lock import (
    UPDATE_ALREADY_RUNNING_CODE,
    UPDATE_ALREADY_RUNNING_EXIT_CODE,
    acquire_update_lock,
)

_HOLDER_SCRIPT = """\
import sys, time
from pathlib import Path
from stock_quant.operations.update_lock import acquire_update_lock
acquire_update_lock(Path(sys.argv[1]))
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


def _offline_env() -> dict:
    env = os.environ.copy()
    env.pop("TUSHARE_TRANSPORT", None)
    env.pop("TUSHARE_TOKEN", None)
    return env


def test_a_conflicting_data_update_exits_75_with_the_token_and_never_queues(
    cli_runner, fixture_root
):
    lock = acquire_update_lock(fixture_root.root)
    try:
        started = time.monotonic()
        result = cli_runner.invoke(
            app,
            [
                "data",
                "update",
                "--start",
                "2021-11-01",
                "--end",
                "2021-11-30",
                "--root",
                str(fixture_root.root),
            ],
        )
        elapsed = time.monotonic() - started
        assert result.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE
        assert UPDATE_ALREADY_RUNNING_CODE in result.stdout
        assert "FAILED" in result.stdout
        assert elapsed < 10  # 不排队：立即以稳定冲突码退出
        # 冲突处理既没有偷走也没有释放持锁者的锁。
        again = cli_runner.invoke(
            app, ["data", "update", "--root", str(fixture_root.root)]
        )
        assert again.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE
    finally:
        os.close(lock.fd)  # 归还会话级 fixture 工程的锁，供其他测试使用


def test_two_processes_second_gets_conflict_code_first_survives(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, str(project.root), "120"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        conflicting = subprocess.run(
            [
                sys.executable,
                "-m",
                "stock_quant",
                "data",
                "update",
                "--start",
                "2021-11-01",
                "--end",
                "2021-11-30",
                "--root",
                str(project.root),
            ],
            capture_output=True,
            text=True,
            env=_offline_env(),
            timeout=60,
        )
        elapsed = time.monotonic() - started
        assert conflicting.returncode == UPDATE_ALREADY_RUNNING_EXIT_CODE
        assert UPDATE_ALREADY_RUNNING_CODE in conflicting.stdout
        assert elapsed < 30  # 显著短于持锁者的 120s：没有排队等待
        assert holder.poll() is None  # 首个持锁者未被杀死、未被干扰

        holder.terminate()
        holder.wait(timeout=10)
        # 崩溃/退出后由内核释放：同一锁文件、无清理动作，下一次更新不再冲突
        #（离线环境下它以 transport 缺失失败，退出码 1 而不是 75）。
        after = subprocess.run(
            [
                sys.executable,
                "-m",
                "stock_quant",
                "data",
                "update",
                "--root",
                str(project.root),
            ],
            capture_output=True,
            text=True,
            env=_offline_env(),
            timeout=120,
        )
        assert after.returncode == 1
        assert "FAILED" in after.stdout
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)


# --------------------------------------------------------------------------- #
# operations update + UpdateRunner (spec 9.1/9.2)
# --------------------------------------------------------------------------- #

_SUCCESS_CHILD = """\
print("run_id=data_update_fixture01")
print("dataset_version=" + "ab" * 32)
print("PASS")
"""

_CONFLICT_CHILD = """\
import sys
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_EXIT_CODE
sys.exit(UPDATE_ALREADY_RUNNING_EXIT_CODE)
"""

_FAIL_CHILD = "import sys\nsys.exit(3)\n"


def _fake_popen(script: str):
    def popen(argv, *, stdout, stderr, **kwargs):
        return subprocess.Popen(
            [sys.executable, "-c", script], stdout=stdout, stderr=stderr
        )

    return popen


def _job_ids(project_root) -> list:
    store = JobStore(project_root)
    if not store.root.is_dir():
        return []
    return sorted(child.name for child in store.root.iterdir() if child.is_dir())


def test_operations_update_records_a_failed_conflict_job(cli_runner, tmp_path):
    project = build_fixture_project(tmp_path / "p")
    lock = acquire_update_lock(project.root)
    try:
        result = cli_runner.invoke(
            app,
            [
                "operations",
                "update",
                "--start",
                "2021-11-01",
                "--end",
                "2021-11-30",
                "--root",
                str(project.root),
            ],
        )
        assert result.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE
        assert "job_id=" in result.stdout
        assert "status=FAILED" in result.stdout
        assert UPDATE_ALREADY_RUNNING_CODE in result.stdout
        job_id = next(
            line.split("=", 1)[1]
            for line in result.stdout.splitlines()
            if line.startswith("job_id=")
        )
        record = JobStore(project.root).get(job_id)
        assert record.status == FAILED
        assert record.failure_reason == "update_already_running"
        assert record.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE
        stdout_path, stderr_path = JobStore(project.root).log_paths(job_id)
        assert stdout_path.is_file() and stderr_path.is_file()
        assert (JobStore(project.root).root / job_id / "request.json").is_file()
    finally:
        os.close(lock.fd)


def test_operations_update_rejects_invalid_parameters_before_any_job(
        tmp_path, cli_runner):
    project = build_fixture_project(tmp_path / "p")
    result = cli_runner.invoke(
        app,
        ["operations", "update", "--start", "2026-9-1", "--root", str(project.root)]
    )
    assert result.exit_code == 1
    assert "invalid parameter start" in result.stdout
    assert _job_ids(project.root) == []


def test_a_successful_child_records_run_id_and_dataset_version(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    result = run_operations_update(
        project.root,
        UpdateRunParams(),
        popen=_fake_popen(_SUCCESS_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    assert result.status == SUCCEEDED
    assert result.exit_code == 0
    assert result.run_id == "data_update_fixture01"
    assert result.dataset_version == "ab" * 32
    record = JobStore(project.root).get(result.job_id)
    assert record.status == SUCCEEDED
    assert record.run_id == "data_update_fixture01"
    assert record.dataset_version == "ab" * 32
    assert record.heartbeat_at is not None  # 心跳是存活判定的唯一证据
    stdout_path, _ = JobStore(project.root).log_paths(result.job_id)
    assert "run_id=data_update_fixture01" in stdout_path.read_text(encoding="utf-8")


def test_a_conflicting_child_maps_to_update_already_running(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    result = run_operations_update(
        project.root,
        UpdateRunParams(),
        popen=_fake_popen(_CONFLICT_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    assert result.status == FAILED
    assert result.failure_reason == "update_already_running"
    assert operations_exit_code(result) == UPDATE_ALREADY_RUNNING_EXIT_CODE
    record = JobStore(project.root).get(result.job_id)
    assert record.failure_reason == "update_already_running"
    assert record.exit_code == UPDATE_ALREADY_RUNNING_EXIT_CODE


def test_another_failing_child_maps_to_update_failed(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    result = run_operations_update(
        project.root,
        UpdateRunParams(),
        popen=_fake_popen(_FAIL_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    assert result.status == FAILED
    assert result.failure_reason == "update_failed"
    record = JobStore(project.root).get(result.job_id)
    assert record.exit_code == 3


def test_a_precreated_queued_job_is_adopted_not_duplicated(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    pre = JobStore(project.root).create(
        {"entrypoint": "operations_api", "request": UpdateRunParams().to_payload()}
    )
    result = run_operations_update(
        project.root,
        UpdateRunParams(),
        job_id=pre.job_id,
        entrypoint="operations_api",
        popen=_fake_popen(_SUCCESS_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    assert result.job_id == pre.job_id
    assert _job_ids(project.root) == [pre.job_id]


_CANCEL_DRIVER = """\
import subprocess, sys
from pathlib import Path
from stock_quant.operations import runner


def slow_popen(argv, *, stdout, stderr, **kwargs):
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, time; print(os.getpid(), flush=True); time.sleep(120)",
        ],
        stdout=stdout,
        stderr=stderr,
    )


result = runner.run_operations_update(
    Path(sys.argv[1]),
    runner.UpdateRunParams(),
    popen=slow_popen,
    heartbeat_interval_seconds=0.05,
)
print("status=" + result.status, flush=True)
"""


def test_sigterm_marks_cancelled_by_shutdown_and_terminates_the_child(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    driver = subprocess.Popen(
        [sys.executable, "-c", _CANCEL_DRIVER, str(project.root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        store = JobStore(project.root)
        job_id = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and job_id is None:
            for candidate in _job_ids(project.root):
                try:
                    if store.get(candidate).status == RUNNING:
                        job_id = candidate
                        break
                except Exception:
                    continue
            time.sleep(0.05)
        assert job_id is not None, "the runner never reached RUNNING"

        stdout_log, _ = store.log_paths(job_id)
        grandchild_pid = None
        pid_deadline = time.monotonic() + 10
        while time.monotonic() < pid_deadline and grandchild_pid is None:
            text = (
                stdout_log.read_text(encoding="utf-8") if stdout_log.is_file() else ""
            )
            digits = [line for line in text.splitlines() if line.strip().isdigit()]
            if digits:
                grandchild_pid = int(digits[0])
            else:
                time.sleep(0.05)
        assert grandchild_pid is not None

        driver.terminate()
        stdout, _ = driver.communicate(timeout=30)
        assert "status=CANCELLED_BY_SHUTDOWN" in stdout
        assert store.get(job_id).status == CANCELLED_BY_SHUTDOWN

        gone_deadline = time.monotonic() + 10
        while time.monotonic() < gone_deadline:
            try:
                os.kill(grandchild_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the inner child survived the runner shutdown")
    finally:
        if driver.poll() is None:
            driver.kill()
            driver.wait(timeout=10)


def test_the_queued_job_request_is_immutable_and_secret_free(tmp_path):
    project = build_fixture_project(tmp_path / "p")
    result = run_operations_update(
        project.root,
        UpdateRunParams(sources=("tushare",)),
        popen=_fake_popen(_SUCCESS_CHILD),
        heartbeat_interval_seconds=0.05,
    )
    request = json.loads(
        (
            JobStore(project.root).root / result.job_id / "request.json"
        ).read_text(encoding="utf-8")
    )
    assert request["request"]["sources"] == ["tushare"]
    assert "TOKEN" not in json.dumps(request)
