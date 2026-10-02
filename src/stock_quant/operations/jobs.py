"""Persistent job records under ``data/service/jobs/<job_id>/`` (spec 9.2).

Directory contract per job:
- ``request.json``  immutable: written once, with exclusive create.
- ``stdout.log`` / ``stderr.log``  append-only child output.
- ``status.json``  atomically replaced (temp file + ``os.replace``) on every
  transition and heartbeat; it carries pid, boot_id and heartbeat_at, the
  only evidence of liveness.

The status vocabulary is frozen (spec 9.2): QUEUED / RUNNING / SUCCEEDED /
FAILED / CANCELLED_BY_SHUTDOWN.  Nothing outside that set is ever written.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_CODE

QUEUED = "QUEUED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
CANCELLED_BY_SHUTDOWN = "CANCELLED_BY_SHUTDOWN"

#: The frozen job-status vocabulary (spec 9.2).
JOB_STATUSES = frozenset(
    {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED_BY_SHUTDOWN}
)

#: Job failure reasons.  ``update_already_running`` is the SAME token the
#: conflict exit code carries (single source of truth: update_lock.py).
FAILURE_UPDATE_ALREADY_RUNNING = UPDATE_ALREADY_RUNNING_CODE
FAILURE_UPDATE_FAILED = "update_failed"

#: Job directories live here, relative to the resolved project root.
JOBS_RELATIVE_PATH = Path("data") / "service" / "jobs"


class JobNotFound(KeyError):
    """No readable job record under this id."""


class JobAlreadyExists(ValueError):
    """A job directory or request.json already exists for this id."""


class JobStateError(ValueError):
    """A transition the job's current status does not allow."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_job_id(*, now: datetime | None = None) -> str:
    """A lexicographically sortable job id (UTC stamp + random tail)."""
    moment = now or _utcnow()
    return f"job_{moment:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class JobRecord:
    """One job's persisted state (parsed from ``status.json``)."""

    job_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    pid: int | None = None
    boot_id: str | None = None
    heartbeat_at: datetime | None = None
    exit_code: int | None = None
    failure_reason: str | None = None
    failure_detail: str | None = None
    run_id: str | None = None
    dataset_version: str | None = None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class JobStore:
    """Read/write access to one project root's ``data/service/jobs`` tree."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / JOBS_RELATIVE_PATH

    # -- creation ------------------------------------------------------ #
    def create(
        self, request: Mapping[str, object], *, job_id: str | None = None
    ) -> JobRecord:
        """Create a QUEUED job; ``request.json`` is written exactly once."""
        now = _utcnow()
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            candidate = job_id if job_id is not None else new_job_id(now=now)
            try:
                (self.root / candidate).mkdir()
            except FileExistsError:
                if job_id is not None:
                    raise JobAlreadyExists(job_id) from None
                continue  # random-tail collision: draw again
            break
        payload = {**dict(request), "job_id": candidate, "created_at": _iso(now)}
        with (self.root / candidate / "request.json").open(
            "x", encoding="utf-8"
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return self._write_status(
            JobRecord(job_id=candidate, status=QUEUED, created_at=now, updated_at=now)
        )

    def log_paths(self, job_id: str) -> tuple[Path, Path]:
        """The append-only output logs of one job."""
        directory = self.root / job_id
        return directory / "stdout.log", directory / "stderr.log"

    # -- transitions --------------------------------------------------- #
    def mark_running(
        self, job_id: str, *, pid: int, boot_id: str, now: datetime | None = None
    ) -> JobRecord:
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(
                record,
                status=RUNNING,
                pid=pid,
                boot_id=boot_id,
                heartbeat_at=moment,
                updated_at=moment,
            )
        )

    def heartbeat(self, job_id: str, *, now: datetime | None = None) -> JobRecord:
        """Refresh the liveness evidence; pid/boot_id stay as they were."""
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(record, heartbeat_at=moment, updated_at=moment)
        )

    def mark_succeeded(
        self,
        job_id: str,
        *,
        run_id: str | None,
        dataset_version: str | None,
        exit_code: int = 0,
        now: datetime | None = None,
    ) -> JobRecord:
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(
                record,
                status=SUCCEEDED,
                run_id=run_id,
                dataset_version=dataset_version,
                exit_code=exit_code,
                updated_at=moment,
            )
        )

    def mark_failed(
        self,
        job_id: str,
        *,
        reason: str,
        exit_code: int | None,
        now: datetime | None = None,
        detail: str | None = None,
    ) -> JobRecord:
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(
                record,
                status=FAILED,
                failure_reason=reason,
                failure_detail=detail,
                exit_code=exit_code,
                updated_at=moment,
            )
        )

    def mark_cancelled_by_shutdown(
        self, job_id: str, *, now: datetime | None = None
    ) -> JobRecord:
        moment = now or _utcnow()
        record = self.get(job_id)
        self._require_active(record)
        return self._write_status(
            replace(record, status=CANCELLED_BY_SHUTDOWN, updated_at=moment)
        )

    # -- reads --------------------------------------------------------- #
    def get(self, job_id: str) -> JobRecord:
        status_path = self.root / job_id / "status.json"
        if not status_path.is_file():
            raise JobNotFound(job_id)
        return _record_from_payload(
            json.loads(status_path.read_text(encoding="utf-8"))
        )

    def _require_active(self, record: JobRecord) -> None:
        if record.status not in (QUEUED, RUNNING):
            raise JobStateError(
                f"job {record.job_id} is {record.status}; "
                "only an active job can transition"
            )

    def _write_status(self, record: JobRecord) -> JobRecord:
        if record.status not in JOB_STATUSES:
            raise JobStateError(
                f"{record.status!r} is outside the frozen job status vocabulary"
            )
        payload = {
            "job_id": record.job_id,
            "status": record.status,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
            "pid": record.pid,
            "boot_id": record.boot_id,
            "heartbeat_at": _iso(record.heartbeat_at),
            "exit_code": record.exit_code,
            "failure_reason": record.failure_reason,
            "failure_detail": record.failure_detail,
            "run_id": record.run_id,
            "dataset_version": record.dataset_version,
        }
        destination = self.root / record.job_id / "status.json"
        temporary = destination.with_name("status.json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, destination)
        return record


def _record_from_payload(payload: Mapping[str, object]) -> JobRecord:
    created = _parse_iso(payload.get("created_at")) or _utcnow()
    updated = _parse_iso(payload.get("updated_at")) or created
    pid = payload.get("pid")
    exit_code = payload.get("exit_code")
    return JobRecord(
        job_id=str(payload["job_id"]),
        status=str(payload["status"]),
        created_at=created,
        updated_at=updated,
        pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        boot_id=(
            str(payload["boot_id"]) if payload.get("boot_id") is not None else None
        ),
        heartbeat_at=_parse_iso(payload.get("heartbeat_at")),
        exit_code=(
            exit_code
            if isinstance(exit_code, int) and not isinstance(exit_code, bool)
            else None
        ),
        failure_reason=(
            str(payload["failure_reason"])
            if payload.get("failure_reason") is not None
            else None
        ),
        failure_detail=(
            str(payload["failure_detail"])
            if payload.get("failure_detail") is not None
            else None
        ),
        run_id=(
            str(payload["run_id"]) if payload.get("run_id") is not None else None
        ),
        dataset_version=(
            str(payload["dataset_version"])
            if payload.get("dataset_version") is not None
            else None
        ),
    )
