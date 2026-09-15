"""Rebuild a full canonical dataset from the stored real tables (no network).

Status: migration.

The operator ``data update`` path is currently blocked by external-source
outages (EastMoney IP block / baostock down / tushare 1-per-minute rate limit)
and by the corporate-action review gate, which needs both cninfo and eastmoney
online to reproduce the reviewed 300750.SZ cross-source conflict.

The stored CURRENT version (a813fdcad…) already carries the *reconciled* real
tables the network step would otherwise re-fetch: daily_bar (2021-01-04..
2026-08-28, tushare single-source for all 30 universe names), the reviewed
corporate_action facts (511 implemented events, all coverage VERIFIED), the
security master, and the trading calendar.  What it lacks is the derived
layer a fresh update would add: ``adjusted_bar`` (built from daily + corporate
actions), the canonical empty quarantine frame (this dataset predates the
quarantine migration), and the empty membership frame.

This script rebuilds exactly that 9-table canonical dataset offline from the
stored tables and republishes it, mirroring ``rebuild_trading_calendar.py``.
It changes nothing on disk except publishing a new immutable version and
advancing the CURRENT pointer.  It is an ENGINEERING artifact: no raw-source
fetch evidence is (or can be) bound, so no formal ACCEPTED record may ever be
pinned to the version it publishes.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.adjusted_bar import build_adjusted_bars
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.schemas import CORPORATE_ACTION_QUARANTINE_COLUMNS
from stock_quant.data_model.universe_membership import membership_frame
from stock_quant.data_quality.models import QualityReport
from stock_quant.project_root import resolve_project_root
from stock_quant.safe_yaml import read_yaml


def run(root: Path, config: ProjectConfig) -> int:
    universe = read_yaml(root / "configs" / "universe.yml")
    symbols = [entry["symbol"] for entry in universe["entries"]]
    print(f"universe symbols={len(symbols)}")

    publisher = DatasetPublisher(root)
    version = publisher.current().version
    print(f"base version={version}")
    with DatasetReader(root).open(version) as dataset:
        daily = dataset.read("daily_bar")
        corporate_action = dataset.read("corporate_action")
        coverage = dataset.read("corporate_action_coverage")
        master = dataset.read("security_master")
        master_coverage = dataset.read("security_master_coverage")
        calendar = dataset.read("trading_calendar")

    quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
    adjusted = build_adjusted_bars(
        daily,
        corporate_action,
        quarantine,
        coverage,
        symbols=tuple(symbols),
    )
    print(
        f"adjusted_bar rows={len(adjusted)} symbols={adjusted['symbol'].nunique()} "
        f"severity={adjusted['quality_severity'].value_counts().to_dict()}"
    )

    tables = {
        "daily_bar": daily,
        "adjusted_bar": adjusted,
        "security_master": master,
        "security_master_coverage": master_coverage,
        "corporate_action": corporate_action,
        "corporate_action_quarantine": quarantine,
        "corporate_action_coverage": coverage,
        "trading_calendar": calendar,
        "universe_membership": membership_frame([]),
    }
    published = publisher.publish(tables, QualityReport())
    print(f"dataset_version={published.version}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config)


if __name__ == "__main__":
    raise SystemExit(main())
