"""The FastAPI application factory, shared error envelope and health route.

The query service is local, read-only and loopback-bound (spec §8.1,
ADR-021): it resolves one explicit dataset version per request, echoes the
resolved full hash in every response and never exposes a write verb.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from stock_quant.project_root import resolve_project_root
from stock_quant.service import (
    benchmarks,
    datasets,
    experiments,
    results,
    strategy_challenges,
    tables,
)
from stock_quant.service.errors import ErrorBody, ErrorResponse, ServiceError

#: Default per-request query budget in seconds (spec §8.2: "默认量级 5 秒").
DEFAULT_QUERY_BUDGET_SECONDS = 5.0

API_PREFIX = "/api/v1"


class HealthResponse(BaseModel):
    status: Literal["ok"]
    project_root_fingerprint: str


def project_root_fingerprint(root: Path) -> str:
    """A stable, non-revealing fingerprint of the resolved project root."""
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]


def register_error_handlers(app: FastAPI) -> None:
    """Fail every error through the one envelope, echoing the resolved version."""

    @app.exception_handler(ServiceError)
    def _service_error(request: Request, error: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(
                error=ErrorBody(
                    code=error.code,
                    message=error.message,
                    dataset_version=getattr(request.state, "resolved_version", None),
                )
            ).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    def _invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = "; ".join(str(item) for item in error.errors()[:3])
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error=ErrorBody(
                    code="invalid_request",
                    message=f"request parameters failed validation: {details}",
                    dataset_version=getattr(request.state, "resolved_version", None),
                )
            ).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    def _http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            error.status_code, "http_error"
        )
        return JSONResponse(
            status_code=error.status_code,
            content=ErrorResponse(
                error=ErrorBody(code=code, message=str(error.detail))
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    def _unhandled(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorBody(
                    code="internal_error",
                    message=f"unhandled {type(error).__name__}",
                )
            ).model_dump(),
        )


def create_app(
    project_root: str | Path,
    *,
    query_budget_seconds: float = DEFAULT_QUERY_BUDGET_SECONDS,
) -> FastAPI:
    """Build the read-only query app for one validated project root."""
    root = resolve_project_root(project_root)
    app = FastAPI(
        title="stock-quant read-only query service",
        version="1.0.0",
        description=(
            "Local, loopback-only, GET-only views over published dataset "
            "versions, acceptance records and experiment reports (ADR-021)."
        ),
    )
    app.state.project_root = root
    app.state.query_budget_seconds = query_budget_seconds
    # Document the shared error envelope on every versioned operation so the
    # fail-closed contract is visible in openapi.json (spec §8.3).
    error_responses = {
        code: {"model": ErrorResponse} for code in ("404", "409", "422", "500")
    }
    app.include_router(benchmarks.router, responses=error_responses)
    app.include_router(datasets.router, responses=error_responses)
    app.include_router(experiments.router, responses=error_responses)
    app.include_router(results.router, responses=error_responses)
    app.include_router(strategy_challenges.router, responses=error_responses)
    app.include_router(tables.router, responses=error_responses)
    register_error_handlers(app)

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse, tags=["service"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok", project_root_fingerprint=project_root_fingerprint(root)
        )

    return app
