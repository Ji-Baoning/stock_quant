"""Operations API: three endpoints over persistent update jobs (spec 9.3).

A separate router/process, disabled by default; when enabled it is still
loopback-only (``operations.serve`` refuses any non-loopback bind before
uvicorn ever runs).  The three endpoints are deliberately the whole
surface: no cancel, no retry-to-green, no log deletion, no acceptance and
no research endpoints.  A rerun after a failure is a NEW job id, created
by a new explicit POST or the next timer trigger -- never by this process
on its own.  Every request first runs the orphan self-check, so restarted
services never present dead jobs as running.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from stock_quant.operations.jobs import (
    JobNotFound,
    JobStore,
    active_job,
    list_jobs,
    read_log_tail,
    reap_orphaned_jobs,
)
from stock_quant.operations.runner import (
    InvalidUpdateParams,
    build_operations_update_argv,
    read_boot_id,
    validate_update_params,
)
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_CODE

router = APIRouter(prefix="/api/v1")

#: The env keys whose values are redacted from log tails (mirrors the CLI's
#: own secret list; tokens are only ever read from the environment).
_SECRET_ENV_KEYS = (
    "TUSHARE_TOKEN",
    "AKSHARE_TOKEN",
    "BAOSTOCK_USER",
    "BAOSTOCK_PASSWORD",
)


class UpdateJobRequest(BaseModel):
    """Exactly the parameters ``data update`` allows (spec 9.1)."""

    start: str | None = None
    end: str | None = None
    sources: list[str] | None = None
    disclosure_lookback_days: int | None = None


def create_operations_app(project_root: Path, *, enabled: bool = False) -> FastAPI:
    """Build the operations app; ``enabled=False`` answers 503 everywhere.

    Default-disabled is the fail-closed state (spec 9.3): even if the router
    is mounted by mistake, nothing executes until the operator explicitly
    enables the operations surface at launch.
    """
    application = FastAPI(title="stock-quant operations API", version="1")
    application.state.project_root = Path(project_root)
    application.state.enabled = bool(enabled)
    # fastapi 0.141 起 include_router 延迟展开：app.routes 只剩一个
    # _IncludedRouter 惰性包装（连 .methods 都没有），"恰三端点"的表面
    # 在构建后不可内省。这里直接挂载 router 的具体 APIRoute（路由自身已带
    # /api/v1 前缀），请求匹配与 include_router 等价，但表面对内省可见。
    application.router.routes.extend(router.routes)
    return application


def redact_text(
    text: str, *, secrets: Iterable[str], project_root: Path
) -> str:
    """Strip secret values and the absolute project root from log tails."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text.replace(str(project_root), "<project-root>")


def _secret_values() -> tuple[str, ...]:
    return tuple(
        value
        for value in (os.environ.get(key) for key in _SECRET_ENV_KEYS)
        if value
    )


def _guard_enabled(request: Request) -> JSONResponse | None:
    if not request.app.state.enabled:
        return JSONResponse(
            status_code=503, content={"error": {"code": "operations_disabled"}}
        )
    return None


def _self_check(request: Request) -> None:
    """Reap orphans before answering, so dead jobs are never shown live."""
    state = request.app.state
    reap_orphaned_jobs(
        state.project_root,
        now=datetime.now(timezone.utc),
        boot_id=read_boot_id(),
    )


@router.post("/update-jobs")
def create_update_job(payload: UpdateJobRequest, request: Request) -> JSONResponse:
    """Validate and launch one ``operations update`` (spec 9.3).

    An active job answers 409 with the same conflict code the CLI exits
    with.  A lock held outside the job system (a manual CLI run) is NOT
    probed here on purpose: the spawned job then ends
    FAILED/``update_already_running`` by the inner CLI's exit code -- the
    flock stays the single-flight arbiter across all three entrances.
    """
    refused = _guard_enabled(request)
    if refused is not None:
        return refused
    project_root: Path = request.app.state.project_root
    _self_check(request)
    now = datetime.now(timezone.utc)
    running = active_job(project_root, now=now, boot_id=read_boot_id())
    if running is not None:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": UPDATE_ALREADY_RUNNING_CODE,
                    "job_id": running.job_id,
                }
            },
        )
    try:
        params = validate_update_params(
            start=payload.start,
            end=payload.end,
            sources=payload.sources,
            disclosure_lookback_days=payload.disclosure_lookback_days,
        )
    except InvalidUpdateParams as error:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_parameter",
                    "parameter": error.parameter,
                    "reason": error.reason,
                }
            },
        )
    job = JobStore(project_root).create(
        {"entrypoint": "operations_api", "request": params.to_payload()}
    )
    argv = build_operations_update_argv(project_root, params, job_id=job.job_id)
    stdout_path, stderr_path = JobStore(project_root).log_paths(job.job_id)
    # Detached: the job survives an API restart and keeps heartbeating on
    # its own; if it dies instead, the self-check reaps it fail-closed.
    with stdout_path.open("ab") as stdout_log, stderr_path.open("ab") as stderr_log:
        subprocess.Popen(
            argv,
            stdout=stdout_log,
            stderr=stderr_log,
            start_new_session=True,
            cwd=str(project_root),
        )
    return JSONResponse(
        status_code=201, content={"job_id": job.job_id, "status": job.status}
    )


@router.get("/update-jobs", response_model=None)
def list_update_jobs(request: Request) -> JSONResponse | dict:
    """Persisted job summaries (spec 9.3)."""
    refused = _guard_enabled(request)
    if refused is not None:
        return refused
    _self_check(request)
    return {
        "jobs": [
            {
                "job_id": record.job_id,
                "status": record.status,
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
                "run_id": record.run_id,
                "dataset_version": record.dataset_version,
            }
            for record in list_jobs(request.app.state.project_root)
        ]
    }


@router.get("/update-jobs/{job_id}")
def get_update_job(job_id: str, request: Request) -> JSONResponse:
    """One job's status and sanitized log tails (spec 9.3)."""
    refused = _guard_enabled(request)
    if refused is not None:
        return refused
    _self_check(request)
    project_root: Path = request.app.state.project_root
    try:
        record = JobStore(project_root).get(job_id)
    except JobNotFound:
        # 与 409/422/503 以及 P3 冻结的 `{"error": {...}}` 信封同形；用
        # HTTPException(detail=...) 会得到 FastAPI 默认的 `{"detail": ...}`，
        # 成为三个端点里唯一一个消费者读不到 code 的响应。
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "job_not_found", "job_id": job_id}},
        )
    stdout_path, stderr_path = JobStore(project_root).log_paths(job_id)
    secrets = _secret_values()
    return JSONResponse(
        status_code=200,
        content={
            "job_id": record.job_id,
            "status": record.status,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "pid": record.pid,
            "boot_id": record.boot_id,
            "heartbeat_at": (
                record.heartbeat_at.isoformat() if record.heartbeat_at else None
            ),
            "exit_code": record.exit_code,
            "failure_reason": record.failure_reason,
            "failure_detail": record.failure_detail,
            "run_id": record.run_id,
            "dataset_version": record.dataset_version,
            "stdout_tail": redact_text(
                read_log_tail(stdout_path),
                secrets=secrets,
                project_root=project_root,
            ),
            "stderr_tail": redact_text(
                read_log_tail(stderr_path),
                secrets=secrets,
                project_root=project_root,
            ),
        },
    )
