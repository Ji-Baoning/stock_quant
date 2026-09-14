#!/usr/bin/env python
"""Cross-check the pinned trading calendar against an independent relay.

The design spec (§14) verifies the open-day set against benchmarks rather than
trusting a single supplier, and this repository has already seen one transport
falsified on exactly this interface (a relay that dropped ``is_open=0`` rows,
left the first ``pretrade_date`` blank, and returned zero rows for dashed
dates).  So the calendar is worth a second, independent read.

This is **read-only and offline-only**: it opens the already-published dataset,
fetches ``trade_cal`` from the relay, and compares the two open-day sets plus
the relay's own ``pretrade_date`` chain.  It publishes nothing, changes no
dataset version, and never touches the pipeline's evidence model -- the relay
is used as an external benchmark, never as a transport for published data.

Run from the repository root or the project directory:
    python project/crosscheck_calendar_relay.py

Exit codes: 0 = the relay agrees; 1 = it disagrees (an open-day conflict, a
coverage gap, or a broken pretrade_date chain); 2 = the relay is not
configured.  Token values are never printed.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_sources.tushare_relay import TushareRelayClient
from stock_quant.project_root import resolve_project_root


def _load_env(path: Path) -> None:
    """Load simple KEY=VALUE lines without evaluating shell code."""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _as_date(value: object) -> date | None:
    """Coerce one calendar cell to a date; blank/NaT reads as ``None``."""
    if value is None or value == "" or pd.isna(value):
        return None
    return pd.Timestamp(str(value)).date()


def _open_mask(frame: pd.DataFrame, flag_column: str) -> pd.Series:
    """Rows the frame marks open. A frame with no flag column is all-open."""
    if flag_column not in frame.columns:
        return pd.Series(True, index=frame.index)
    text = frame[flag_column].astype(str).str.strip()
    return ~text.isin({"", "0", "0.0", "False", "false", "nan"})


def _open_days(frame: pd.DataFrame, day_column: str, flag_column: str) -> set[date]:
    """The set of dates the frame marks open."""
    if day_column not in frame.columns:
        return set()
    return {
        day
        for day in (
            _as_date(value)
            for value in frame.loc[_open_mask(frame, flag_column), day_column]
        )
        if day
    }


def _pretrade_breaks(relay: pd.DataFrame) -> list[tuple[date, date | None, date]]:
    """Open days whose ``pretrade_date`` is not the preceding open day.

    The first open day is skipped: its predecessor lies before the window.
    """
    if "pretrade_date" not in relay.columns or "cal_date" not in relay.columns:
        return []
    open_rows = relay.loc[_open_mask(relay, "is_open"), ["cal_date", "pretrade_date"]]
    claimed = {
        day: _as_date(row["pretrade_date"])
        for day, row in (
            (_as_date(row["cal_date"]), row) for _, row in open_rows.iterrows()
        )
        if day
    }
    ordered = sorted(claimed)
    return [
        (current, claimed[current], previous)
        for previous, current in zip(ordered, ordered[1:])
        if claimed[current] != previous
    ]


def run(
    root: Path,
    config: ProjectConfig,
    *,
    env_file: Path | None = None,
    exchange: str = "SSE",
) -> int:
    if env_file is None:
        env_file = root / ".env"
    if env_file.is_file():
        _load_env(env_file)
        print(f"environment: loaded {env_file}")
    else:
        print(f"environment: not found ({env_file}); using current environment")

    client = TushareRelayClient.from_env()
    if client is None:
        print(
            "relay: not configured "
            "(set TUSHARE_RELAY_URL and TUSHARE_RELAY_KEY); nothing to check"
        )
        return 2
    print(f"relay: host={client.host} sdk={client.sdk_version}")

    publisher = DatasetPublisher(root)
    with DatasetReader(root).open(publisher.current().version) as dataset:
        stored = dataset.read("trading_calendar")

    stored_open = _open_days(stored, "calendar_date", "is_trading_day")
    if not stored_open:
        print("stored calendar is empty; nothing to compare")
        return 1
    start, end = min(stored_open), max(stored_open)
    print(
        f"stored calendar: {len(stored_open)} open days "
        f"({start.isoformat()}..{end.isoformat()})"
    )

    relay = client.query(
        "trade_cal",
        exchange=exchange,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    relay_open = _open_days(relay, "cal_date", "is_open")
    print(f"relay calendar: {len(relay_open)} open days (exchange={exchange})")

    only_stored = sorted(stored_open - relay_open)
    only_relay = sorted(relay_open - stored_open)
    breaks = _pretrade_breaks(relay)

    if not only_stored and not only_relay and not breaks:
        print("AGREE: open-day sets identical and the pretrade_date chain is intact")
        return 0

    if only_stored:
        print(f"CONFLICT: stored says open, relay says closed ({len(only_stored)}):")
        for day in only_stored[:10]:
            print(f"  {day.isoformat()}")
    if only_relay:
        print(f"GAP: relay open, stored lacks ({len(only_relay)}):")
        for day in only_relay[:10]:
            print(f"  {day.isoformat()}")
    if breaks:
        print(f"PRETRADE CHAIN BREAKS ({len(breaks)}):")
        for current, claimed, previous in breaks[:10]:
            got = claimed.isoformat() if claimed else "blank"
            print(f"  {current}: claimed={got} expected={previous}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="KEY=VALUE file to load first (default: <root>/.env)",
    )
    parser.add_argument("--exchange", default="SSE")
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config, env_file=args.env_file, exchange=args.exchange)


if __name__ == "__main__":
    raise SystemExit(main())
