"""Preflight the full-window data update before spending the network budget.

Status: diagnostic.

The 2015-2026 update walks every name of the tracked `universe.yml` plus two
benchmarks through two required suppliers and only publishes when every
required fetch answered.  A stale baseline (the trim not yet published), a
missing token or a dead endpoint would otherwise surface halfway through that
run.  This script is read-only: it never writes the raw store, never publishes
and never advances CURRENT.
"""

from __future__ import annotations

import argparse
import os
import re
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
from stock_quant.data_sources.tushare_transport import (
    build_transport,
    resolve_transport,
)
from stock_quant.project_root import resolve_project_root

UPDATE_START = date(2015, 1, 1)
UPDATE_END = date(2026, 8, 28)
TRADABLE_UNIVERSE_ID = "custom_csi300_tw_tradable"
EXPECTED_TRADABLE_SYMBOLS = 657
PROBE_SYMBOL = "600519.SH"
#: Fixed official-channel probe surface (spec D6): which endpoints answer
#: without the relay.  The list changes only by editing this constant — the
#: canary never auto-switches or auto-degrades anything.
OFFICIAL_CANARY_ENDPOINTS = ("daily",)


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
    """Require the trimmed membership to already be CURRENT.

    ``data update`` carries ``universe_membership`` verbatim, so updating
    before the trim is published would freeze the untrimmed base table into
    the new dataset.
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


def official_canary_probes(
    environ: Mapping[str, str] | None = None,
    *,
    config: SourceConfig | None = None,
) -> dict[str, dict]:
    """Probe api.waditu.com official transport directly, once per endpoint.

    Forced ``official`` transport — the canary exists to answer "which
    endpoints still work without the relay", so a relay/proxy fallback here
    would be a silent downgrade and is forbidden (spec D6).  Returns one row
    per endpoint: status + row count or an exception class name only.
    """
    source = os.environ if environ is None else environ
    probes: dict[str, dict] = {}
    try:
        transport = build_transport(
            "official",
            config if config is not None else SourceConfig(),
            environ=source,
        )
        tushare = TushareSource(
            config if config is not None else SourceConfig(),
            transport=transport,
        )
    except Exception as error:  # noqa: BLE001 - reported, never raised
        note = f"{type(error).__name__}"
        return {
            endpoint: {"status": "unusable", "note": note}
            for endpoint in OFFICIAL_CANARY_ENDPOINTS
        }
    for endpoint in OFFICIAL_CANARY_ENDPOINTS:
        try:
            frame = tushare.fetch(
                DataRequest(endpoint, (PROBE_SYMBOL,), UPDATE_START, UPDATE_END, {})
            ).frame
            probes[endpoint] = {"status": "reachable", "rows": len(frame)}
        except Exception as error:  # noqa: BLE001 - class name only, redacted
            probes[endpoint] = {
                "status": "unreachable",
                "note": type(error).__name__,
            }
    return probes


def _note_head(note: str) -> str:
    """Keep only the leading exception class name of a probe note.

    The rendered record is the credential boundary for a persisted ops
    file: a note body may embed a token in its message, so nothing past
    the first ``:``, ``=`` or blank ever reaches the record.  (This is
    stricter than the pipeline log redaction, which masks values but keeps
    key names; the ops record keeps neither.)
    """
    return re.split(r"[:=\s]", note, maxsplit=1)[0]


def render_canary_record(probe_date: str, probes: dict[str, dict]) -> str:
    """Ops-record body: endpoint names, status and counts — no credentials."""
    lines = [
        f"# 官方通道金丝雀 {probe_date}",
        "",
        "月度对 api.waditu.com 官方直连实测「无 relay 时哪些端点仍可得」（spec D6）。",
        "本记录只含端点名、状态与行数；不含 token/key/凭证 URL 段。",
        "本探测只读、不落任何切换：降级永远是操作者显式决定。",
        "",
    ]
    for endpoint in OFFICIAL_CANARY_ENDPOINTS:
        row = probes.get(endpoint, {"status": "not_probed"})
        note = row.get("note")
        lines.append(
            f"- {endpoint}: status={row.get('status')} "
            f"rows={row.get('rows', '-')}"
            + (f" note={_note_head(str(note))}" if note else "")
        )
    return "\n".join(lines) + "\n"


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
    parser.add_argument(
        "--official-canary",
        action="store_true",
        help="probe official-channel endpoints and write the dated ops record",
    )
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    if args.official_canary:
        probes = official_canary_probes(config=config.sources["tushare"])
        record = render_canary_record(date.today().isoformat(), probes)
        destination = (
            root / "docs" / "operations"
            / f"{date.today().isoformat()}-official-channel-canary.md"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(record, encoding="utf-8")
        print(record)
        print(f"canary record -> {destination}")
        return 0
    return run(root, config)


if __name__ == "__main__":
    raise SystemExit(main())
