"""CLI-visible single-flight behaviour of ``data update`` (spec 9.1/9.2).

The conflict path must be reachable fully offline: the lock is taken right
after root resolution, before any transport or credential is ever touched.
The multiprocess test below uses a real holder subprocess so "does not
queue, does not kill the first runner" is asserted against a real process,
not a mock.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from conftest import build_fixture_project

from stock_quant.cli import app
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
