"""Rebuild the published calendar from benchmark sessions (no network access)."""

# This one-off script rebuilds the calendar from benchmark sessions and drops
# every build-evidence field, so the republished dataset can no longer explain
# its calendar provenance.  Use `data update` with an explicit window instead.
# The rebuild body below is kept for reference only: `main` refuses to run it.

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_quality.models import QualityReport
from stock_quant.project_root import resolve_project_root

_SUPERSEDED_MESSAGE = (
    "rebuild_trading_calendar.py is superseded: run "
    "`data update --start <coverage_start> --end <last published calendar day>`"
)


def run(root: Path, config: ProjectConfig) -> int:
    """The original rebuild body.  Unreachable while the guard above holds."""
    version = DatasetPublisher(root).current().version
    with DatasetReader(root).open(version) as dataset:
        tables = {name: dataset.read(name) for name in (
            "daily_bar", "security_master", "corporate_action", "trading_calendar"
        )}
    benchmarks = ("000300.SH", "000905.SH")
    sessions = [
        set(pd.to_datetime(tables["daily_bar"].loc[
            tables["daily_bar"]["symbol"] == symbol, "trade_date"
        ]).dt.normalize())
        for symbol in benchmarks
    ]
    dates = sorted(set.intersection(*sessions))
    tables["trading_calendar"] = pd.DataFrame(
        {"calendar_date": dates, "is_trading_day": True}
    )
    published = DatasetPublisher(root).publish(tables, QualityReport())
    print(f"dataset_version={published.version} sessions={len(dates)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    # The script is superseded; it never touches the dataset.
    raise SystemExit(_SUPERSEDED_MESSAGE)
    return run(root, config)  # pragma: no cover - kept for reference


if __name__ == "__main__":
    raise SystemExit(main())
