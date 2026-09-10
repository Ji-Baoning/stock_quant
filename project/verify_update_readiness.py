"""Preflight the full-window data update before spending the network budget.

The 2015-2026 update walks 30 equity names plus two benchmarks through two
required suppliers and only publishes when every required fetch answered.  A
stale baseline (plan one not yet published), a missing token or a dead
endpoint would otherwise surface halfway through that run.  This script is
read-only: it never writes the raw store, never publishes and never advances
CURRENT.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.tushare import TushareSource

ROOT = Path(__file__).resolve().parent
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


def token_issue(
    environ: Mapping[str, str] | None = None,
) -> ReadinessIssue | None:
    """Require a non-empty ``TUSHARE_TOKEN`` before any fetch is attempted."""
    source = os.environ if environ is None else environ
    if not str(source.get("TUSHARE_TOKEN", "")).strip():
        return ReadinessIssue(
            "TUSHARE_TOKEN_MISSING", "TUSHARE_TOKEN is unset or empty"
        )
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


def main() -> int:
    publisher = DatasetPublisher(ROOT)
    version = publisher.current().version
    print(f"current dataset={version}")
    with DatasetReader(ROOT).open(version) as dataset:
        membership = (
            dataset.read("universe_membership")
            if "universe_membership" in dataset.tables
            else None
        )
    issues = baseline_issues(membership)
    missing_token = token_issue()
    if missing_token is not None:
        issues.append(missing_token)
    probes: dict[str, int] = {}
    if missing_token is None:
        sources = {
            "tushare.daily": (TushareSource(SourceConfig()), "daily"),
            "cninfo_corporate_actions": (
                AkShareSource(SourceConfig()),
                "cninfo_corporate_actions",
            ),
            "eastmoney_corporate_actions": (
                AkShareSource(SourceConfig()),
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


if __name__ == "__main__":
    raise SystemExit(main())
