"""Durable job-record semantics and the orphan self-check (spec 9.2).

Liveness is judged from the status.json heartbeat alone -- never from "the
directory exists" or "the logs went quiet" (the Dagster run-monitoring
lesson the spec cites).  Every test injects its clock and boot id; nothing
waits five real minutes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stock_quant.operations.jobs import (
    CANCELLED_BY_SHUTDOWN,
    FAILED,
    FAILURE_ORPHANED_PROCESS,
    JOB_STATUSES,
    ORPHAN_HEARTBEAT_TIMEOUT_SECONDS,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    JobAlreadyExists,
    JobStateError,
    JobStore,
    active_job,
    list_jobs,
    new_job_id,
    read_log_tail,
    reap_orphaned_jobs,
)

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
_BOOT = "2fdf3cf0-97bf-4896-bd6f-1a5b8841637d"
_OTHER_BOOT = "11111111-2222-3333-4444-555555555555"


def test_the_status_vocabulary_is_frozen():
    assert JOB_STATUSES == frozenset(
        {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED_BY_SHUTDOWN}
    )
    assert ORPHAN_HEARTBEAT_TIMEOUT_SECONDS == 300
    assert FAILURE_ORPHANED_PROCESS == "orphaned_process"


def test_job_ids_sort_by_creation():
    earlier = new_job_id(now=_NOW)
    later = new_job_id(now=_NOW + timedelta(seconds=1))
    assert earlier < later


def test_request_json_is_written_once(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    request_path = store.root / job.job_id / "request.json"
    before = request_path.read_bytes()
    with pytest.raises(JobAlreadyExists):
        store.create({"entrypoint": "again"}, job_id=job.job_id)
    assert request_path.read_bytes() == before  # 不可变：字节未动


def test_terminal_jobs_never_transition(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    store.mark_running(job.job_id, pid=1, boot_id=_BOOT, now=_NOW)
    store.mark_failed(job.job_id, reason="update_failed", exit_code=1, now=_NOW)
    with pytest.raises(JobStateError):
        store.mark_failed(job.job_id, reason="again", exit_code=1, now=_NOW)
    with pytest.raises(JobStateError):
        store.mark_succeeded(job.job_id, run_id="r", dataset_version="v", now=_NOW)


def test_status_json_stays_parseable_and_leaves_no_temporary(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    store.mark_running(job.job_id, pid=1, boot_id=_BOOT, now=_NOW)
    store.heartbeat(job.job_id, now=_NOW + timedelta(seconds=30))
    store.mark_succeeded(
        job.job_id, run_id="data_update_x", dataset_version="ab" * 32, now=_NOW
    )
    # 每一步之后 status.json 都是完整合法 JSON（原子替换，无撕裂半写）。
    assert store.get(job.job_id).status == SUCCEEDED
    assert store.get(job.job_id).dataset_version == "ab" * 32
    leftovers = [
        child.name
        for child in (store.root / job.job_id).iterdir()
        if child.name.endswith(".tmp")
    ]
    assert leftovers == []


def _running_job(
    tmp_path: Path,
    *,
    heartbeat: datetime | None,
    boot_id: str = _BOOT,
) -> str:
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    store.mark_running(
        job.job_id, pid=4242, boot_id=boot_id, now=_NOW - timedelta(minutes=10)
    )
    if heartbeat is not None:
        store.heartbeat(job.job_id, now=heartbeat)
    return job.job_id


def test_a_fresh_running_job_is_not_reaped(tmp_path):
    job_id = _running_job(tmp_path, heartbeat=_NOW - timedelta(seconds=30))
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == []
    assert JobStore(tmp_path).get(job_id).status == RUNNING


def test_a_stale_heartbeat_is_reaped_as_orphaned_process(tmp_path):
    job_id = _running_job(tmp_path, heartbeat=_NOW - timedelta(seconds=400))
    reaped = reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT)
    assert reaped == [job_id]
    record = JobStore(tmp_path).get(job_id)
    assert record.status == FAILED
    assert record.failure_reason == FAILURE_ORPHANED_PROCESS
    assert record.failure_detail == "heartbeat_stale"
    # 目录与日志一律不删。
    assert (JobStore(tmp_path).root / job_id).is_dir()
    stdout_path, _ = JobStore(tmp_path).log_paths(job_id)
    assert stdout_path.parent.is_dir()


def test_a_changed_boot_id_is_reaped_even_with_a_fresh_heartbeat(tmp_path):
    # 机器重启过：心跳再新，写它的进程也不可能在世（且 pid 可能已被复用）。
    job_id = _running_job(
        tmp_path, heartbeat=_NOW - timedelta(seconds=1), boot_id=_OTHER_BOOT
    )
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == [job_id]
    assert JobStore(tmp_path).get(job_id).failure_detail == "boot_id_changed"


def test_a_running_job_without_any_heartbeat_is_reaped(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    # 手工构造一个从未心跳的 RUNNING（合法 API 之外能出现的唯一形态）。
    status_path = store.root / job.job_id / "status.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    payload["status"] = RUNNING
    payload.pop("heartbeat_at", None)
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == [job.job_id]
    assert JobStore(tmp_path).get(job.job_id).failure_detail == "no_heartbeat"


def test_an_unreadable_running_status_is_reaped_fail_closed(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    (store.root / job.job_id / "status.json").write_text("{not json", encoding="utf-8")
    # 不可读 = 没有心跳证据 = 判孤儿（fail closed），不以目录存在推断存活。
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == [job.job_id]
    record = JobStore(tmp_path).get(job.job_id)
    assert record.status == FAILED
    assert record.failure_reason == FAILURE_ORPHANED_PROCESS


def test_a_stale_queued_job_is_reaped_as_never_started(tmp_path):
    store = JobStore(tmp_path)
    job = store.create({"entrypoint": "test"})
    older = JobStore(tmp_path).root / job.job_id / "status.json"
    payload = json.loads(older.read_text(encoding="utf-8"))
    payload["created_at"] = (_NOW - timedelta(seconds=400)).isoformat()
    older.write_text(json.dumps(payload), encoding="utf-8")
    assert reap_orphaned_jobs(tmp_path, now=_NOW, boot_id=_BOOT) == [job.job_id]
    assert JobStore(tmp_path).get(job.job_id).failure_detail == "never_started"


def test_active_job_links_the_fresh_running_one(tmp_path):
    fresh = _running_job(tmp_path, heartbeat=_NOW - timedelta(seconds=30))
    assert active_job(tmp_path, now=_NOW, boot_id=_BOOT) is not None
    assert active_job(tmp_path, now=_NOW, boot_id=_BOOT).job_id == fresh


def test_active_job_ignores_stale_and_terminal_jobs(tmp_path):
    _running_job(tmp_path, heartbeat=_NOW - timedelta(seconds=400))
    store = JobStore(tmp_path)
    done = store.create({"entrypoint": "test"})
    store.mark_running(done.job_id, pid=1, boot_id=_BOOT, now=_NOW)
    store.mark_succeeded(done.job_id, run_id="r", dataset_version="v", now=_NOW)
    assert active_job(tmp_path, now=_NOW, boot_id=_BOOT) is None


def test_list_jobs_is_oldest_first_and_skips_unreadable(tmp_path):
    store = JobStore(tmp_path)
    first = store.create({"entrypoint": "test"})
    second = store.create({"entrypoint": "test"})
    (store.root / second.job_id / "status.json").write_text("{broken", encoding="utf-8")
    listed = list_jobs(tmp_path)
    assert [record.job_id for record in listed] == [first.job_id]


def test_read_log_tail_returns_the_last_bytes_lossily(tmp_path):
    log = tmp_path / "stdout.log"
    log.write_text("x" * 10000 + "TAIL", encoding="utf-8")
    tail = read_log_tail(log, max_bytes=8)
    assert tail == "xxxxTAIL"
    assert read_log_tail(tmp_path / "missing.log") == ""
