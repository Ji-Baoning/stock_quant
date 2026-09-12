#!/usr/bin/env python
"""Read-only connectivity check for the configured market-data suppliers.

Run from the repository root or project directory:
    python project/check_data_sources.py

The default environment file is the phase-one worktree's ``.env.example``.
Use ``--env-file`` to override it.  Token values are never printed.  The
``tushare`` line reports whichever transport is active (official SDK or the
shared GET proxy, visible in the ``endpoint`` label); when the proxy
credentials are configured, an ``index_daily`` probe exercises the
proxy-specific surface as well.
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from stock_quant.config import SourceConfig
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.data_sources.tushare_proxy import TushareProxyClient
from stock_quant.data_sources.tushare_transport import build_transport


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


def _try_check(
    name: str, build: Callable[[], object], request: DataRequest
) -> bool:
    """Check one supplier; a source that cannot even be built reports FAIL."""
    try:
        source = build()
    except Exception as error:  # noqa: BLE001 - diagnostic boundary
        print(f"{name}: FAIL {type(error).__name__}: {error}")
        return False
    return _check(name, source, request)


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
    results = [
        _try_check(
            "tushare",
            lambda: TushareSource(config, allow_auto_transport=True),
            DataRequest("daily", ("600000.SH",), start, end, {}),
        ),
        _try_check(
            "akshare",
            lambda: AkShareSource(config),
            DataRequest("index_history", ("000300.SH",), start, end, {}),
        ),
        _try_check(
            "baostock",
            lambda: BaoStockSource(config),
            DataRequest("daily", ("sh.600000",), start, end, {}),
        ),
    ]
    if TushareProxyClient.from_env(timeout_seconds=config.timeout_seconds) is not None:
        results.append(
            _try_check(
                "tushare_proxy",
                lambda: TushareSource(
                    config, transport=build_transport("proxy", config)
                ),
                DataRequest("index_daily", ("000300.SH",), start, end, {}),
            )
        )
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
