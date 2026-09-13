"""Preflight the full-window data update before spending the network budget.

The 2015-2026 update walks 30 equity names plus two benchmarks through two
required suppliers and only publishes when every required fetch answered.  A
stale baseline (plan one not yet published), a missing token or a dead
endpoint would otherwise surface halfway through that run.  This script is
read-only: it never writes the raw store, never publishes and never advances
CURRENT.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

from stock_quant.config import ProjectConfig, SourceConfig, load_project_config
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.base import AuthenticationError, DataRequest
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.data_sources.tushare_transport import resolve_transport
from stock_quant.project_root import resolve_project_root

UPDATE_START = date(2015, 1, 1)
UPDATE_END = date(2026, 8, 28)
TRADABLE_UNIVERSE_ID = "custom_csi300_ic_tradable"
EXPECTED_TRADABLE_SYMBOLS = 28
PROBE_SYMBOL = "600519.SH"


@dataclass(frozen=True)
class ReadinessIssue:
    """One precondition the update must satisfy before it is worth starting."""

    code: str
    message: str


def baseline_issues(
    membership: pd.DataFrame | None,
    *,
    universe_id: str = TRADABLE_UNIVERSE_ID,
    expected_symbols: int = EXPECTED_TRADABLE_SYMBOLS,
) -> list[ReadinessIssue]:
    """Require plan one's trimmed membership to already be CURRENT.

    ``data update`` carries ``universe_membership`` verbatim, so updating
    before the trim is published would freeze the untrimmed 1221-row table
    into the new dataset.
    """
    if membership is None or membership.empty:
        return [
            ReadinessIssue(
                "BASELINE_MEMBERSHIP_EMPTY",
                "CURRENT dataset carries no universe_membership rows; "
                "run the plan-one trim first",
            )
        ]
    ids = sorted({str(value) for value in membership["universe_id"]})
    issues: list[ReadinessIssue] = []
    if ids != [universe_id]:
        issues.append(
            ReadinessIssue(
                "BASELINE_UNIVERSE_ID_MISMATCH",
                f"CURRENT membership universe_id={ids}, expected "
                f"[{universe_id!r}]; run the plan-one trim first",
            )
        )
    found = int(membership["symbol"].nunique())
    if found != expected_symbols:
        issues.append(
            ReadinessIssue(
                "BASELINE_SYMBOL_COUNT_MISMATCH",
                f"CURRENT membership has {found} unique symbols, "
                f"expected {expected_symbols}",
            )
        )
    return issues


def transport_issue(
    environ: Mapping[str, str] | None = None,
    *,
    config: SourceConfig | None = None,
) -> ReadinessIssue | None:
    """Require the published build's own transport gate to pass.

    Delegates to the adapter's resolver rather than re-deriving the rules, so
    this probe can never be weaker than what ``data update`` will enforce: a
    missing explicit transport, a forbidden proxy, or an unlogged official
    fallback all surface here first, as a readiness issue rather than a
    traceback halfway through the run.
    """
    source = os.environ if environ is None else environ
    try:
        resolve_transport(
            config if config is not None else SourceConfig(), environ=source
        )
    except AuthenticationError as error:
        return ReadinessIssue("TUSHARE_TRANSPORT_UNUSABLE", str(error))
    return None


def probe_endpoint(
    fetch: Callable[[str, str, date, date], pd.DataFrame],
    endpoint: str,
    symbol: str,
    start: date,
    end: date,
) -> tuple[ReadinessIssue | None, int]:
    """Probe one supplier endpoint once; return ``(issue, row_count)``.

    A supplier that answers with an empty frame is reachable, which is all a
    probe claims.  Whether 2015-2020 really carries events is the update's
    finding, not the probe's.
    """
    try:
        frame = fetch(endpoint, symbol, start, end)
    except Exception as error:  # noqa: BLE001 - a probe reports, never raises
        return (
            ReadinessIssue(
                "SOURCE_PROBE_FAILED",
                f"{endpoint} {symbol} {start}..{end}: "
                f"{type(error).__name__}: {error}",
            ),
            0,
        )
    return None, len(frame)


def report(issues: list[ReadinessIssue], probes: dict[str, int]) -> str:
    """Render the operator-facing verdict; ``ready`` only when issue-free."""
    lines = [f"ready={str(not issues).lower()}"]
    for name, count in sorted(probes.items()):
        lines.append(f"probe {name} rows={count}")
    for issue in issues:
        lines.append(f"issue {issue.code} {issue.message}")
    return "\n".join(lines)


def _fetch(source: object, endpoint: str, symbol: str, start: date, end: date):
    return source.fetch(DataRequest(endpoint, (symbol,), start, end, {})).frame


def run(root: Path, config: ProjectConfig) -> int:
    publisher = DatasetPublisher(root)
    version = publisher.current().version
    print(f"current dataset={version}")
    with DatasetReader(root).open(version) as dataset:
        membership = (
            dataset.read("universe_membership")
            if "universe_membership" in dataset.tables
            else None
        )
    issues = baseline_issues(membership)
    missing_transport = transport_issue(config=config.sources["tushare"])
    if missing_transport is not None:
        issues.append(missing_transport)
    probes: dict[str, int] = {}
    if missing_transport is None:
        sources = {
            "tushare.daily": (
                TushareSource(config.sources["tushare"]),
                "daily",
            ),
            "cninfo_corporate_actions": (
                AkShareSource(config.sources["akshare"]),
                "cninfo_corporate_actions",
            ),
            "eastmoney_corporate_actions": (
                AkShareSource(config.sources["akshare"]),
                "eastmoney_corporate_actions",
            ),
        }
        for name, (source, endpoint) in sources.items():
            issue, count = probe_endpoint(
                partial(_fetch, source),
                endpoint,
                PROBE_SYMBOL,
                UPDATE_START,
                UPDATE_END,
            )
            probes[name] = count
            if issue is not None:
                issues.append(issue)
    print(report(issues, probes))
    return 1 if issues else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config)


if __name__ == "__main__":
    raise SystemExit(main())
