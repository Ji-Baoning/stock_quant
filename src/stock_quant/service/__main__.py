"""Loopback-only entrypoint: ``python -m stock_quant.service --root <ROOT>``."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from stock_quant.project_root import resolve_project_root
from stock_quant.service.app import DEFAULT_QUERY_BUDGET_SECONDS, create_app
from stock_quant.service.security import validate_bind_host


def serve(
    project_root: Path, *, host: str, port: int, query_budget_seconds: float
) -> None:
    """Validate the bind host first, then run uvicorn (never on non-loopback)."""
    validate_bind_host(host)
    app = create_app(project_root, query_budget_seconds=query_budget_seconds)
    uvicorn.run(app, host=host, port=port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m stock_quant.service",
        description="Local read-only query service (loopback only, ADR-021).",
    )
    parser.add_argument("--root", default=".", help="Project root (no fallback).")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback bind host.")
    parser.add_argument("--port", type=int, default=8321)
    parser.add_argument(
        "--query-budget-seconds",
        type=float,
        default=DEFAULT_QUERY_BUDGET_SECONDS,
        help="Per-request query time budget (independent of the row limit).",
    )
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    serve(
        root,
        host=args.host,
        port=args.port,
        query_budget_seconds=args.query_budget_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
