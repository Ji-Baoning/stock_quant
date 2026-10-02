"""Project-local single-flight advisory lock for ``data update`` (spec 9.2).

flock(2) semantics, on purpose: the lock's lifetime IS the holder process's
lifetime.  The kernel releases it when the holder exits or crashes for any
reason, so there is no stale lock, no pid file to trust, and no recovery
path that ever deletes or rewrites the lock file.  The lock file lives
inside the project root at ``data/.locks/update.lock``; every entry point
(the manual CLI, the ``operations update`` shell whose inner child is the
same CLI, the operations API and the systemd timer) funnels through the
inner ``data update`` process, so they all contend on this one lock.
"""

from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from pathlib import Path

#: Stable machine-readable conflict signal (spec 9.1): the dedicated-exit-code
#: option, frozen here.  75 is sysexits ``EX_TEMPFAIL`` -- "try again later",
#: which is exactly this state.  Consumers (UpdateRunner, operations API,
#: operator scripts) key on this code; they must never match prose.
UPDATE_ALREADY_RUNNING_EXIT_CODE = 75

#: The same conflict as a fixed token, printed on its own line for humans
#: and the journal.  It is a readability echo, not a parsing contract.
UPDATE_ALREADY_RUNNING_CODE = "update_already_running"

#: The advisory lock file, relative to the resolved project root.
UPDATE_LOCK_RELATIVE_PATH = Path("data") / ".locks" / "update.lock"


class UpdateAlreadyRunning(RuntimeError):
    """Another process holds this project root's update lock."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        super().__init__(
            "another data update already holds this project root's lock "
            "(flock on data/.locks/update.lock); not queuing, not killing it"
        )


@dataclass(frozen=True)
class UpdateLock:
    """An acquired single-flight lock.

    ``fd`` is deliberately never closed by anyone: it stays open for the
    holder process's whole lifetime and the kernel drops the lock with it.
    There is no ``release()`` on purpose -- recovery never involves the lock
    file (no deletion, no truncation, no pid comparisons).
    """

    path: Path
    fd: int


def acquire_update_lock(project_root: Path) -> UpdateLock:
    """Acquire the project's update lock or raise :class:`UpdateAlreadyRunning`.

    Non-blocking by design: a held lock is reported immediately -- nothing
    queues and the current holder is never disturbed (spec 9.2).
    """
    lock_path = Path(project_root) / UPDATE_LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise UpdateAlreadyRunning(lock_path) from None
    return UpdateLock(path=lock_path, fd=descriptor)
