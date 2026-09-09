#!/usr/bin/env python
"""Read-only connectivity check for the three market-data suppliers.

Run from the repository root or project directory:
    python project/check_data_sources.py

The default environment file is the phase-one worktree's ``.env.example``.
Use ``--env-file`` to override it.  Token values are never printed.
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

from stock_quant.config import SourceConfig
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.tushare import TushareSource


def _load_env(path: Path) -> None:
    """Load simple KEY=VALUE lines without evaluating shell code."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def _window() -> tuple[date, date]:
    end = date.today() - timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    return end - timedelta(days=10), end


def _check(name: str, source: object, request: DataRequest) -> bool:
    try:
        result = source.fetch(request)  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001 - diagnostic boundary
        print(f"{name}: FAIL {type(error).__name__}: {error}")
        return False
    endpoint = result.metadata["supplier_endpoint"]
    print(f"{name}: OK rows={len(result.frame)} endpoint={endpoint}")
    return True


def main() -> int:
    default_env = (
        Path(__file__).resolve().parents[1]
        / ".worktrees"
        / "phase-one-quant-system"
        / ".env.example"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=default_env)
    args = parser.parse_args()
    if args.env_file.is_file():
        _load_env(args.env_file)
        print(f"environment: loaded {args.env_file}")
    else:
        print(f"environment: not found ({args.env_file}); using current environment")

    start, end = _window()
    config = SourceConfig()
    results = (
        _check(
            "tushare",
            TushareSource(config),
            DataRequest("daily", ("600000.SH",), start, end, {}),
        ),
        _check(
            "akshare",
            AkShareSource(config),
            DataRequest("index_history", ("000300.SH",), start, end, {}),
        ),
        _check(
            "baostock",
            BaoStockSource(config),
            DataRequest("daily", ("sh.600000",), start, end, {}),
        ),
    )
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
