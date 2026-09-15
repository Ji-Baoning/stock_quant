#!/usr/bin/env python
"""Read-only connectivity check for the configured market-data suppliers.

Status: diagnostic.

Run with an explicit project root (default: the current directory):
    python project/check_data_sources.py --root .

The supplier gate comes from the project's ``configs/sources.yml``: a source
with ``enabled: false`` prints one ``SKIP disabled by config`` line and its
constructor is never invoked.  Token values are never printed.  The
``tushare`` line reports whichever transport is active (official SDK or the
shared GET proxy, visible in the ``endpoint`` label); when the proxy
credentials are configured, an ``index_daily`` probe exercises the
proxy-specific surface as well.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.data_sources.tushare_proxy import TushareProxyClient
from stock_quant.data_sources.tushare_transport import build_transport
from stock_quant.project_root import resolve_project_root


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


def run(root: Path, config: ProjectConfig) -> int:
    """Probe every enabled supplier; disabled ones only print a SKIP line."""
    env_file = root / ".env"
    if env_file.is_file():
        _load_env(env_file)
        print(f"environment: loaded {env_file}")
    else:
        print(f"environment: not found ({env_file}); using current environment")

    start, end = _window()
    builders: dict[str, tuple[Callable[[], object], DataRequest]] = {
        "tushare": (
            lambda: TushareSource(
                config.sources["tushare"], allow_auto_transport=True
            ),
            DataRequest("daily", ("600000.SH",), start, end, {}),
        ),
        "akshare": (
            lambda: AkShareSource(config.sources["akshare"]),
            DataRequest("index_history", ("000300.SH",), start, end, {}),
        ),
        "baostock": (
            lambda: BaoStockSource(config.sources["baostock"]),
            DataRequest("daily", ("sh.600000",), start, end, {}),
        ),
    }
    results: list[bool] = []
    for name, (build, request) in builders.items():
        if not config.sources[name].enabled:
            print(f"{name}: SKIP disabled by config")
            continue
        results.append(_try_check(name, build, request))
    tushare_config = config.sources["tushare"]
    if (
        tushare_config.enabled
        and TushareProxyClient.from_env(
            timeout_seconds=tushare_config.timeout_seconds
        )
        is not None
    ):
        results.append(
            _try_check(
                "tushare_proxy",
                lambda: TushareSource(
                    tushare_config, transport=build_transport("proxy", tushare_config)
                ),
                DataRequest("index_daily", ("000300.SH",), start, end, {}),
            )
        )
    return 0 if all(results) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config)


if __name__ == "__main__":
    raise SystemExit(main())
