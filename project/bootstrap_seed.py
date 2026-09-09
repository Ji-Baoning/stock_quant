#!/usr/bin/env python
"""One-time seed: publish the carried baseline dataset for THIS project.

Phase one has no ``data bootstrap`` CLI: ``data update`` can only *extend* an
already-published dataset that carries ``security_master`` and
``trading_calendar`` (see docs/operations/phase-one-validation.md section 1).
This script publishes that baseline from ``configs/universe.yml`` (the 30 fixed
samples) and ``configs/project.yml`` (the configured date range), with an
*empty* ``daily_bar`` / ``corporate_action`` so the first real ``data update``
fills them with supplier data and leaves no synthetic market rows behind.

Phase-one boundaries mirrored here (see the ops checklist section 4):
- The trading calendar is a *weekday* approximation of the exchange calendar
  (no CN public holidays).  To use the official trading days, supply a file
  with one ISO ``YYYY-MM-DD`` date per line and re-run:
      python bootstrap_seed.py --calendar-csv official_calendar.txt
- ``security_master.list_date`` is a synthetic early constant: the universe is
  fixed samples all listed before the configured window, so one early date is
  behaviourally identical within the window.  Real list dates are not fetched
  in phase one (no supplier security-master endpoint).

Idempotent: identical tables publish the same content-addressed version.

Usage (in the activated env, from this project root):
    python bootstrap_seed.py
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_COLUMNS,
    DAILY_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
)
from stock_quant.data_model.universe import Universe
from stock_quant.data_quality.models import QualityReport

#: Synthetic list date for every sample.  All 30 samples are pre-window
#: listings (the 2021+ window), so a single early constant is equivalent to
#: their real (earlier) list dates for listing-duration purposes.
SYNTHETIC_LIST_DATE = date(2018, 1, 2)


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _calendar_days(path: Path | None, start: date, end: date) -> list[date]:
    """Official calendar file (one ISO date per line) or weekday approximation."""
    if path is None:
        return _weekdays(start, end)
    days: list[date] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        days.append(date.fromisoformat(line.split()[0]))
    return sorted({day for day in days if start <= day <= end})


def _security_master(universe: Universe) -> pd.DataFrame:
    entries = universe.entries
    n = len(entries)
    return pd.DataFrame(
        {
            "symbol": [entry.symbol for entry in entries],
            "name": [entry.name_at_selection for entry in entries],
            "exchange": [entry.exchange for entry in entries],
            "board": [entry.board for entry in entries],
            "list_date": pd.to_datetime([SYNTHETIC_LIST_DATE] * n),
            "delist_date": pd.Series(
                pd.NaT, index=range(n), dtype="datetime64[ns]"
            ),
        }
    )[SECURITY_MASTER_COLUMNS]


def _trading_calendar(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(days),
            "is_trading_day": [True] * len(days),
        }
    )[TRADING_CALENDAR_COLUMNS]


def _empty_daily() -> pd.DataFrame:
    """Zero-row ``daily_bar`` with the canonical columns and native dtypes."""
    return pd.DataFrame(
        {
            "trade_date": pd.Series(dtype="datetime64[ns]"),
            "symbol": pd.Series(dtype="object"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
            "amount": pd.Series(dtype="float64"),
            "adjustment": pd.Series(dtype="object"),
            "source": pd.Series(dtype="object"),
            "ingested_at": pd.Series(dtype="datetime64[ns]"),
        }
    )[DAILY_COLUMNS]


def _empty_corporate_action() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series(dtype="object"),
            "announcement_date": pd.Series(dtype="object"),
            "record_date": pd.Series(dtype="object"),
            "ex_date": pd.Series(dtype="object"),
            "cash_dividend_per_share": pd.Series(dtype="float64"),
            "bonus_share_ratio": pd.Series(dtype="float64"),
            "capitalization_ratio": pd.Series(dtype="float64"),
            "rights_issue_ratio": pd.Series(dtype="float64"),
            "rights_issue_price": pd.Series(dtype="float64"),
            "source": pd.Series(dtype="object"),
            "status": pd.Series(dtype="object"),
        }
    )[CORPORATE_ACTION_COLUMNS]


def _as_date(value: object) -> date:
    """yaml.safe_load turns bare ISO dates into datetime/date; normalise."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _warn_equity_benchmarks(root: Path, universe: Universe) -> None:
    """Flag benchmark_symbols that look like investable samples, not indices."""
    config_path = root / "configs" / "project.yml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    benchmarks = raw.get("benchmark_symbols") or []
    universe_symbols = set(universe.symbols)
    misplaced = [b for b in benchmarks if b in universe_symbols]
    if misplaced:
        print(
            "[warn] benchmark_symbols contains symbols that are also universe "
            f"samples (they will be fetched as *indices* by the akshare "
            f"benchmark role and likely BLOCK the update): {misplaced}. "
            "Design benchmark set is 000300.SH + 000905.SH.",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="project root holding configs/ and data/ (default: this script's dir)",
    )
    parser.add_argument(
        "--calendar-csv",
        type=Path,
        default=None,
        help="optional official trading-day file (one ISO date per line)",
    )
    args = parser.parse_args()

    root: Path = args.root.resolve()
    config_dir = root / "configs"
    for required in ("project.yml", "universe.yml"):
        if not (config_dir / required).exists():
            print(f"FAILED: missing {config_dir / required}", flush=True)
            return 1

    project = yaml.safe_load(
        (config_dir / "project.yml").read_text(encoding="utf-8")
    ) or {}
    start = _as_date(project["start_date"])
    end = _as_date(project["end_date"])
    if end < start:
        print(
            f"FAILED: project.yml end_date {end} precedes start_date {start}",
            flush=True,
        )
        return 1

    universe = Universe.from_yaml(config_dir / "universe.yml")
    _warn_equity_benchmarks(root, universe)

    days = _calendar_days(args.calendar_csv, start, end)
    if not days:
        print(
            "FAILED: no trading days in range; check --calendar-csv / dates",
            flush=True,
        )
        return 1

    tables = {
        "daily_bar": _empty_daily(),
        "security_master": _security_master(universe),
        "corporate_action": _empty_corporate_action(),
        "trading_calendar": _trading_calendar(days),
    }

    result = DatasetPublisher(root).publish(tables, QualityReport())
    version = result.version
    current = DatasetPublisher(root).current().version

    print(f"published seed dataset: {version}", flush=True)
    print(f"security_master symbols : {len(universe.entries)}", flush=True)
    print(f"trading_calendar days    : {len(days)} ({start}..{end})", flush=True)
    print(f"CURRENT -> {current}", flush=True)
    print(
        "NOTE: weekday-approximation calendar (no CN holidays) and synthetic "
        "list_date. Override the calendar with --calendar-csv for the official "
        "exchange days, and reconcile real list dates per the ops checklist.",
        flush=True,
    )
    print(
        "\nNext: "
        "python -m stock_quant data update --start <FIRST> --end <RECENT> --root .",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
