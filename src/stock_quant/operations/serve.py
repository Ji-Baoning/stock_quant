"""Launcher for the operations API process (spec 9.3).

Default disabled and loopback-only: ``--enable`` is required to serve at
all, and any non-loopback ``--host`` is refused before uvicorn ever binds.
There is no flag or configuration that turns this process into a network
service.
"""

from __future__ import annotations

import argparse
import ipaddress
from collections.abc import Sequence

import uvicorn

from stock_quant.operations.api import create_operations_app
from stock_quant.project_root import ProjectRootError, resolve_project_root

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642


def is_loopback(host: str) -> bool:
    """True only for loopback addresses (and the ``localhost`` name)."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    return address.is_loopback


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m stock_quant.operations.serve",
        description=(
            "Serve the stock-quant operations API (default disabled, "
            "loopback only)."
        ),
    )
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument(
        "--enable",
        action="store_true",
        help=(
            "Serve the operations endpoints; without this flag the process "
            "refuses to start (spec 9.3)."
        ),
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="Bind address (loopback only)."
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    args = parser.parse_args(argv)
    if not args.enable:
        print("FAILED: operations API is disabled; start with --enable (spec 9.3)")
        return 1
    if not is_loopback(args.host):
        print(
            f"FAILED: refusing non-loopback bind {args.host!r}; "
            "the operations API is loopback-only"
        )
        return 1
    try:
        project_root = resolve_project_root(args.root)
    except ProjectRootError as error:
        print(f"FAILED: {error}")
        return 1
    uvicorn.run(
        create_operations_app(project_root, enabled=True),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
