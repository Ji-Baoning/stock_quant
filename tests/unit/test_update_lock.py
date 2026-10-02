"""flock single-flight semantics for the data-update lock (spec 9.2).

The lock's lifetime IS the holder process's lifetime: the kernel releases
it on exit or crash, so recovery never touches the lock file.  These tests
exercise that contract with real file descriptors and one real holder
subprocess; nothing here needs a network, a token or a dataset.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from stock_quant.operations.update_lock import (
    UPDATE_ALREADY_RUNNING_EXIT_CODE,
    UPDATE_LOCK_RELATIVE_PATH,
    UpdateAlreadyRunning,
    acquire_update_lock,
)


def _minimal_project(tmp_path: Path) -> Path:
    """A directory that satisfies ``resolve_project_root``'s config check."""
    configs = tmp_path / "configs"
    configs.mkdir()
    for name in ("project.yml", "sources.yml", "costs.yml"):
        (configs / name).write_text("{}\n", encoding="utf-8")
    return tmp_path


def test_constants_are_frozen_as_decided():
    assert UPDATE_ALREADY_RUNNING_EXIT_CODE == 75
    assert UPDATE_LOCK_RELATIVE_PATH == Path("data/.locks/update.lock")


def test_a_second_acquire_in_the_same_process_raises(tmp_path):
    # flock() judges per open file description: a second open() in the same
    # process is a second claim on the same lock and must be refused.
    root = _minimal_project(tmp_path)
    first = acquire_update_lock(root)
    try:
        with pytest.raises(UpdateAlreadyRunning):
            acquire_update_lock(root)
        assert first.path == root / UPDATE_LOCK_RELATIVE_PATH
        assert first.path.is_file()
    finally:
        os.close(first.fd)  # test hygiene only; production never closes it


_HOLDER_SCRIPT = """\
import sys, time
from pathlib import Path
from stock_quant.operations.update_lock import acquire_update_lock
acquire_update_lock(Path(sys.argv[1]))
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


def test_the_kernel_releases_the_lock_when_the_holder_dies(tmp_path):
    root = _minimal_project(tmp_path)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, str(root), "120"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(UpdateAlreadyRunning):
            acquire_update_lock(root)
        holder.kill()
        holder.wait(timeout=10)
        # No cleanup, no waiting, no lock-file deletion: the next acquire
        # succeeds at once against the very same file.
        lock = acquire_update_lock(root)
        import os

        os.close(lock.fd)
        assert lock.path.is_file()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
