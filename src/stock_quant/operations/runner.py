"""UpdateRunner: one supervised ``data update`` subprocess per job (spec 9.1/9.2).

The runner never calls the ``DataPipeline`` Python API and never builds a
shell string: it launches ``python -m stock_quant data update --root ...``
as an argument array carrying only the four options the CLI allows, and
mirrors the child's success/failure exit code.  While the child runs, the
runner atomically heartbeats pid + timestamp + boot_id into the job's
``status.json`` (spec 9.2).  On SIGTERM the job is recorded
CANCELLED_BY_SHUTDOWN and the child is terminated -- cancellation is a
process-shutdown semantic, never an API endpoint (spec 9.3).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from stock_quant.operations.jobs import (
    CANCELLED_BY_SHUTDOWN,
    FAILED,
    FAILURE_UPDATE_ALREADY_RUNNING,
    FAILURE_UPDATE_FAILED,
    QUEUED,
    SUCCEEDED,
    JobAlreadyExists,
    JobStateError,
    JobStore,
)
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_EXIT_CODE

#: The runner heartbeats at this cadence -- an order of magnitude under the
#: ~5 minute orphan threshold (spec 9.2).
HEARTBEAT_INTERVAL_SECONDS = 30.0

#: The kernel boot id source, guarding heartbeat pids against PID reuse.
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

#: The stable ``key=value`` stdout lines the CLI contract guarantees on
#: success.  Reading these whole-line keys is contract parsing, not the
#: free-text failure guessing spec 9.1 forbids (the conflict signal is the
#: dedicated exit code and nothing else).
_CONTRACT_KEYS = frozenset({"run_id", "dataset_version", "resolved_end_date"})


class InvalidUpdateParams(ValueError):
    """A parameter outside the CLI's own type constraints (spec 9.1)."""

    def __init__(self, parameter: str, reason: str) -> None:
        self.parameter = parameter
        self.reason = reason
        super().__init__(f"invalid {parameter}: {reason}")


@dataclass(frozen=True)
class UpdateRunParams:
    """Exactly the options ``data update`` accepts (spec 9.1)."""

    start: date | None = None
    end: date | None = None
    sources: tuple[str, ...] | None = None
    disclosure_lookback_days: int | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "sources": list(self.sources) if self.sources else None,
            "disclosure_lookback_days": self.disclosure_lookback_days,
        }


def validate_update_params(
    *,
    start: str | None = None,
    end: str | None = None,
    sources: str | Sequence[str] | None = None,
    disclosure_lookback_days: int | None = None,
) -> UpdateRunParams:
    """Validate the options exactly the way ``data update`` constrains them.

    ``start``/``end`` must parse as ISO dates (``date.fromisoformat``); a
    comma-separated ``--sources`` string is split and stripped per item the
    way the CLI does it, and every name must be non-empty; the lookback must
    be an integer >= 1 (the CLI declares ``min=1``).
    """
    parsed_start = _parse_date_option("start", start)
    parsed_end = _parse_date_option("end", end)
    parsed_sources: tuple[str, ...] | None = None
    if sources is not None:
        raw = sources.split(",") if isinstance(sources, str) else tuple(sources)
        parsed_sources = tuple(item.strip() for item in raw)
        if any(not item for item in parsed_sources):
            raise InvalidUpdateParams(
                "sources", "source names must be non-empty after stripping"
            )
    if disclosure_lookback_days is not None and (
        not isinstance(disclosure_lookback_days, int)
        or isinstance(disclosure_lookback_days, bool)
        or disclosure_lookback_days < 1
    ):
        raise InvalidUpdateParams(
            "disclosure_lookback_days", "must be an integer >= 1"
        )
    return UpdateRunParams(
        start=parsed_start,
        end=parsed_end,
        sources=parsed_sources,
        disclosure_lookback_days=disclosure_lookback_days,
    )


def _parse_date_option(parameter: str, value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise InvalidUpdateParams(
            parameter, f"{value!r} is not an ISO date (YYYY-MM-DD)"
        ) from error


def build_data_update_argv(project_root: Path, params: UpdateRunParams) -> list[str]:
    """The inner CLI argv (spec 9.1): an argument array, never a shell string."""
    argv = [
        sys.executable,
        "-m",
        "stock_quant",
        "data",
        "update",
        "--root",
        str(project_root),
    ]
    _append_update_options(argv, params)
    return argv


def build_operations_update_argv(
    project_root: Path, params: UpdateRunParams, *, job_id: str | None = None
) -> list[str]:
    """The outer shell argv the operations API spawns (spec 9.3)."""
    argv = [
        sys.executable,
        "-m",
        "stock_quant",
        "operations",
        "update",
        "--root",
        str(project_root),
    ]
    _append_update_options(argv, params)
    if job_id is not None:
        argv += ["--job-id", job_id]
    return argv


def _append_update_options(argv: list[str], params: UpdateRunParams) -> None:
    """Only the four allowed options ever reach a spawned CLI (spec 9.1)."""
    if params.start is not None:
        argv += ["--start", params.start.isoformat()]
    if params.end is not None:
        argv += ["--end", params.end.isoformat()]
    if params.sources:
        argv += ["--sources", ",".join(params.sources)]
    if params.disclosure_lookback_days is not None:
        argv += ["--disclosure-lookback-days", str(params.disclosure_lookback_days)]


def classify_child_exit(code: int) -> tuple[str, str | None]:
    """Map the inner CLI's exit code onto the job vocabulary (spec 9.1).

    The conflict is identified by the dedicated exit code alone -- never by
    matching the child's output text.
    """
    if code == 0:
        return SUCCEEDED, None
    if code == UPDATE_ALREADY_RUNNING_EXIT_CODE:
        return FAILED, FAILURE_UPDATE_ALREADY_RUNNING
    return FAILED, FAILURE_UPDATE_FAILED


def parse_contract_lines(text: str) -> dict[str, str]:
    """Read the CLI's stable ``key=value`` stdout lines (success contract)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in _CONTRACT_KEYS:
            values[key] = value
    return values


def operations_exit_code(result: "OperationsUpdateResult") -> int:
    """The outer shell exits with the inner outcome (0 / 75 / 1)."""
    if result.status == SUCCEEDED:
        return 0
    if result.failure_reason == FAILURE_UPDATE_ALREADY_RUNNING:
        return UPDATE_ALREADY_RUNNING_EXIT_CODE
    return 1


def read_boot_id(path: Path = BOOT_ID_PATH) -> str:
    """The current kernel boot id (``/proc``, Linux)."""
    return path.read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class OperationsUpdateResult:
    job_id: str
    status: str
    exit_code: int
    failure_reason: str | None = None
    run_id: str | None = None
    dataset_version: str | None = None


def run_operations_update(
    project_root: Path,
    params: UpdateRunParams,
    *,
    job_id: str | None = None,
    entrypoint: str = "operations_cli",
    heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
    boot_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> OperationsUpdateResult:
    """Create/adopt the job, run the inner CLI child, persist every state.

    ``popen``/``clock``/``boot_id``/``heartbeat_interval_seconds`` are test
    injection points; production uses the real subprocess, wall clock,
    ``/proc`` boot id and the 30 s cadence.  The lock is NOT pre-checked
    here on purpose: when the inner CLI hits it, this job becomes the
    FAILED/``update_already_running`` record spec 9.4 requires to be visible
    in both the journal and the operations API.
    """
    moment = clock or (lambda: datetime.now(timezone.utc))
    kernel_boot_id = boot_id if boot_id is not None else read_boot_id()
    store = JobStore(project_root)
    request = {"entrypoint": entrypoint, "request": params.to_payload()}
    try:
        job = store.create(request, job_id=job_id)
    except JobAlreadyExists:
        existing = store.get(str(job_id))
        if existing.status != QUEUED:
            raise JobStateError(
                f"job {job_id} is {existing.status}; only a QUEUED job can be adopted"
            ) from None
        job = existing
    argv = build_data_update_argv(project_root, params)
    stdout_path, stderr_path = store.log_paths(job.job_id)
    cancelled = threading.Event()
    previous_handler: object = None
    with stdout_path.open("ab") as stdout_log, stderr_path.open("ab") as stderr_log:
        child = popen(argv, stdout=stdout_log, stderr=stderr_log)
        try:
            previous_handler = _install_sigterm_handler(cancelled, child)
            store.mark_running(
                job.job_id, pid=os.getpid(), boot_id=kernel_boot_id, now=moment()
            )
            while child.poll() is None:
                if cancelled.wait(heartbeat_interval_seconds):
                    try:
                        child.terminate()
                    except ProcessLookupError:
                        pass
                    continue
                store.heartbeat(job.job_id, now=moment())
            exit_code = int(child.returncode)
        finally:
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)  # type: ignore[arg-type]
    if cancelled.is_set():
        store.mark_cancelled_by_shutdown(job.job_id, now=moment())
        return OperationsUpdateResult(
            job_id=job.job_id, status=CANCELLED_BY_SHUTDOWN, exit_code=1
        )
    status, failure_reason = classify_child_exit(exit_code)
    if status == SUCCEEDED:
        contract = parse_contract_lines(
            stdout_path.read_text(encoding="utf-8", errors="replace")
        )
        run_id = contract.get("run_id") or None
        dataset_version = contract.get("dataset_version") or None
        store.mark_succeeded(
            job.job_id,
            run_id=run_id,
            dataset_version=dataset_version,
            exit_code=exit_code,
            now=moment(),
        )
        return OperationsUpdateResult(
            job_id=job.job_id,
            status=SUCCEEDED,
            exit_code=0,
            run_id=run_id,
            dataset_version=dataset_version,
        )
    store.mark_failed(
        job.job_id,
        reason=failure_reason or FAILURE_UPDATE_FAILED,
        exit_code=exit_code,
        now=moment(),
    )
    return OperationsUpdateResult(
        job_id=job.job_id,
        status=FAILED,
        exit_code=exit_code,
        failure_reason=failure_reason,
    )


def _install_sigterm_handler(
    cancelled: threading.Event, child: subprocess.Popen
) -> object:
    """Mark the job CANCELLED_BY_SHUTDOWN and stop the child on SIGTERM."""

    def _on_sigterm(signum, frame) -> None:  # noqa: ANN001
        cancelled.set()
        try:
            child.terminate()
        except ProcessLookupError:
            pass

    try:
        return signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:
        return None  # not the main thread: no handler installed
