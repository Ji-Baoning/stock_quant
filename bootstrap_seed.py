#!/usr/bin/env python
"""Thin wrapper for the canonical ``data bootstrap`` baseline publisher.

The canonical way to publish the phase-one baseline dataset is the CLI:

    python -m stock_quant data bootstrap --root .

``stock_quant.bootstrap.bootstrap_dataset`` validates ``configs/project.yml``
and ``configs/universe.yml``, builds the four baseline tables (``daily_bar`` /
``security_master`` / ``corporate_action`` / ``trading_calendar``) and
publishes them via ``DatasetPublisher``.  This script is the equivalent
direct-script invocation (kept for convenience), with the same effect; unlike
the CLI it also prints the equity-benchmark warning (see
``_warn_equity_benchmarks``).

Phase-one boundaries mirrored here (see the ops checklist section 4):
- The trading calendar is a *weekday* approximation of the exchange calendar
  (no CN public holidays).  To use the official trading days, supply a file
  with one ISO ``YYYY-MM-DD`` date per line and re-run either form:
      python -m stock_quant data bootstrap --root . --calendar-csv official_calendar.txt
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
from pathlib import Path

import yaml

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.data_model.universe import Universe


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
    try:
        result = bootstrap_dataset(root, calendar_csv=args.calendar_csv)
    except (FileNotFoundError, ValueError) as error:
        print(f"FAILED: {error}", flush=True)
        return 1

    universe = Universe.from_yaml(root / "configs" / "universe.yml")
    _warn_equity_benchmarks(root, universe)

    print(f"published seed dataset: {result.version}", flush=True)
    print(f"security_master symbols : {result.symbol_count}", flush=True)
    print(
        f"trading_calendar days    : {result.trading_day_count} "
        f"({result.start}..{result.end})",
        flush=True,
    )
    print(f"CURRENT -> {result.version}", flush=True)
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
