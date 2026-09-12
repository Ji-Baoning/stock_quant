#!/usr/bin/env python
"""Read-only probe of the shared Tushare-compatible aggregation front.

Prints what the proxy can do *right now*: the interface catalog by category,
the upstream chain it *reports* for each interface this repository has an open
problem for, and those interfaces' declared shape.

Reports facts only, and never asserts trust.  The catalog is a declaration,
not a guarantee: measured on 2026-09-12 it declared 60 requests/min per IP
where the response headers said 200, and reported ``enabled=true`` for
interfaces that return zero rows.  The upstream list is likewise not the
answering set -- probing ``suspend_d`` reported all six upstreams as
unsupporting it while the data endpoint served it.

Run from the repository root or the project directory:
    python project/probe_tushare_proxy.py
    python project/probe_tushare_proxy.py --probe suspend_d
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

from stock_quant.data_sources.tushare_proxy import TushareProxyClient


def _load_env(path: Path) -> None:
    """Load simple KEY=VALUE lines without evaluating shell code.

    Mirrors ``project/check_data_sources.py``: parsing beats ``source``, which
    chokes on this repo's ``.env`` (line 4 is ``BASIC_RDS_KEY = ...`` with
    spaces, which bash reads as a command).
    """
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


#: Interfaces this repository has an open problem for.  See the assessment
#: report's "可解锁卡点索引" for what each one unblocks.
WATCHED = (
    "suspend_d",
    "adj_factor",
    "trade_cal",
    "daily_basic",
    "index_weight",
    "dividend",
    "stk_limit",
    "index_member",
    "bak_basic",
    "moneyflow",
)


def _print_catalog(client: TushareProxyClient) -> None:
    try:
        frame = client.capabilities()
    except Exception as error:  # noqa: BLE001 - diagnostic boundary
        print(f"catalog: FAIL {type(error).__name__}: {error}")
        return
    enabled = frame[frame["enabled"].astype(bool)]
    print(f"interfaces: {len(frame)} (enabled {len(enabled)})")
    print("\nby category (enabled/total):")
    totals = Counter(frame["category"].astype(str))
    live = Counter(enabled["category"].astype(str))
    for category, count in totals.most_common():
        print(f"  {category:<14} {live.get(category, 0):>4}/{count:<4}")


def _print_chain(client: TushareProxyClient, name: str) -> None:
    try:
        chain = client.upstreams(name)
        results = chain.get("results") or []
    except Exception as error:  # noqa: BLE001 - diagnostic boundary
        print(f"      upstreams: ERROR {type(error).__name__}: {error}")
        return
    for entry in results:
        status = "ok" if entry.get("ok") else "FAIL"
        detail = entry.get("message") or entry.get("error") or ""
        print(
            f"      {str(entry.get('name')):<14} {status:<5} "
            f"rows={entry.get('rows')} {str(detail)[:60]}"
        )


def _print_watched(client: TushareProxyClient) -> None:
    print("\nwatched interfaces:")
    for name in WATCHED:
        try:
            capability = client.capability(name)
        except Exception as error:  # noqa: BLE001 - diagnostic boundary
            print(f"  {name:<14} ERROR {type(error).__name__}: {error}")
            continue
        required_any = capability.get("required_any") or []
        alternatives = " | ".join(
            ",".join(str(field) for field in group) for group in required_any
        )
        methods = ",".join(str(method) for method in (capability.get("methods") or []))
        print(
            f"  {name:<14} enabled={str(capability.get('enabled')):<5} "
            f"methods={methods or '-'} max_limit={capability.get('max_limit')} "
            f"required_any={alternatives or '-'}"
        )
        _print_chain(client, str(capability.get("name") or name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".env",
        help="KEY=VALUE file to load first (default: repo-root .env)",
    )
    parser.add_argument(
        "--probe",
        metavar="NAME",
        help="smoke-read one interface with __probe=1 (max 5 rows, server-side)",
    )
    args = parser.parse_args()
    if args.env_file.is_file():
        _load_env(args.env_file)
        print(f"environment: loaded {args.env_file}")
    else:
        print(f"environment: not found ({args.env_file}); using current environment")

    client = TushareProxyClient.from_env()
    if client is None:
        print("TUSHARE_PROXY_URL / TUSHARE_PROXY_KEY are not set; nothing to probe")
        return 1
    print(f"proxy host: {client.host}")

    if args.probe:
        # __probe=1 is the server's own sample mode and ignores the interface's
        # required_any, so this read is deliberately unchecked.  A live
        # pre-flight would reject e.g. `daily` (required_any:
        # ts_code|trade_date|start_date|end_date) before the sample is asked for.
        frame = client.query(args.probe, verify_capability="none", **{"__probe": 1})
        print(f"\n{args.probe} __probe=1 -> {len(frame)} rows")
        print(frame.head().to_string(index=False))
        return 0

    _print_catalog(client)
    _print_watched(client)
    print(
        "\nNote: catalog entries are declarations, not guarantees. Pre-flight "
        "checks shape only - no response identifies the answering upstream."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
