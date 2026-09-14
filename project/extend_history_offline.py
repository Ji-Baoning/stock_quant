"""Extend the published real dataset with 2015-2020 history (offline artifact).

The operator ``data update`` path is still blocked by the corporate-action
review gate (EastMoney unreachable), so this script follows the documented
offline-rebuild pattern of ``rebuild_offline_real_dataset.py``.  It pulls the
missing 2015-01-01..2020-12-31 history directly from the reachable suppliers
-- tushare ``daily`` for the 30 universe names and tushare ``index_daily``
for the two benchmark indices -- and reuses the already-published
full-history corporate-action facts (implemented events since 1991 are in the
reviewed table, so the backfill window's adjustments are covered).  It then
rebuilds the trading calendar from the benchmark intersection, rebuilds
``adjusted_bar`` over the extended window, and publishes a new immutable
dataset version.

ENGINEERING artifact: raw-fetch evidence is not bound into the acceptance
chain (nothing is written to ``data/raw``), so no formal ACCEPTED record may
ever pin the version this script publishes; research runs against it stay
UNTRUSTED diagnostics.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.adjusted_bar import build_adjusted_bars
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_QUARANTINE_COLUMNS,
    DAILY_COLUMNS,
)
from stock_quant.data_model.universe_membership import membership_frame
from stock_quant.data_quality.models import QualityReport
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.base import ContractError, DataRequest
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.project_root import resolve_project_root

BACKFILL_START = date(2015, 1, 1)
BACKFILL_END = date(2020, 12, 31)


def _canonical_stock_frame(
    frame: pd.DataFrame, symbol: str, ingested: datetime
) -> pd.DataFrame:
    """Canonicalise one tushare ``daily`` response (vol hands, amount kCNY)."""
    rows = []
    for record in frame.to_dict("records"):
        rows.append(
            {
                "trade_date": pd.Timestamp(str(record["trade_date"])),
                "symbol": symbol,
                "open": float(record["open"]),
                "high": float(record["high"]),
                "low": float(record["low"]),
                "close": float(record["close"]),
                "volume": int(round(float(record["vol"]) * 100.0)),
                "amount": float(record["amount"]) * 1000.0,
                "adjustment": "unadjusted",
                "source": "tushare",
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows)


def _canonical_index_frame(
    frame: pd.DataFrame, symbol: str, endpoint_name: str, ingested: datetime
) -> pd.DataFrame:
    """Canonicalise one index-history response (mirrors ``_normalize_index``).

    Sina/Tencent frames carry a ``date`` column and volume already in shares;
    EastMoney frames use Chinese columns with volume in lots.  Amount is only
    available from EastMoney; it is recorded as 0.0 elsewhere, matching the
    pipeline's index normalization.
    """
    has_date_column = "date" in frame.columns
    volume_multiplier = 1 if has_date_column else 100
    rows = []
    for record in frame.to_dict("records"):
        if has_date_column:
            trade_date = record["date"]
            open_ = record["open"]
            high = record["high"]
            low = record["low"]
            close = record["close"]
            volume = record.get("volume")
            amount = record.get("amount")
        else:
            trade_date = record["日期"]
            open_ = record["开盘"]
            high = record["最高"]
            low = record["最低"]
            close = record["收盘"]
            volume = record.get("成交量")
            amount = record.get("成交额")
        source_name = "akshare"
        rows.append(
            {
                "trade_date": pd.Timestamp(trade_date),
                "symbol": symbol,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": int(round(float(volume) * volume_multiplier)),
                "amount": float(amount)
                if (amount is not None and not has_date_column)
                else 0.0,
                "adjustment": "unadjusted",
                "source": source_name,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows)


def run(root: Path, config: ProjectConfig) -> int:
    universe = yaml.safe_load((root / "configs" / "universe.yml").read_text())
    symbols = [entry["symbol"] for entry in universe["entries"]]
    benchmarks = list(config.benchmark_symbols)
    print(f"universe symbols={len(symbols)} benchmarks={benchmarks}")

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

    token = os.environ["TUSHARE_TOKEN"]  # noqa: F841 - the transport reads it
    source = TushareSource(config.sources["tushare"])
    akshare_source = AkShareSource(config.sources["akshare"])
    ingested = datetime.now(timezone.utc)

    backfill_frames: list[pd.DataFrame] = []
    master_dates = {
        str(row["symbol"]): row["list_date"] for row in master.to_dict("records")
    }
    for index, symbol in enumerate(symbols):
        try:
            result = source.fetch(
                DataRequest(
                    endpoint="daily",
                    symbols=(symbol,),
                    start_date=BACKFILL_START,
                    end_date=BACKFILL_END,
                )
            )
        except ContractError:
            # An empty supplier response is a legitimate fact for symbols
            # listed after the backfill window (the pool deliberately includes
            # listed_after_2020 boundary names); anything else stays fatal.
            list_date = master_dates.get(symbol)
            if (
                list_date is not None
                and pd.Timestamp(list_date).date() > BACKFILL_END
            ):
                print(
                    f"[{index + 1}/{len(symbols)}] {symbol} no bars: listed "
                    f"{pd.Timestamp(list_date).date()} after backfill window; "
                    "skipping"
                )
                backfill_frames.append(pd.DataFrame(columns=DAILY_COLUMNS))
                continue
            raise
        frame = _canonical_stock_frame(result.frame, symbol, ingested)
        print(
            f"[{index + 1}/{len(symbols)}] {symbol} rows={len(frame)} "
            f"window={frame['trade_date'].min().date()}..{frame['trade_date'].max().date()}"
            if not frame.empty
            else f"[{index + 1}/{len(symbols)}] {symbol} EMPTY"
        )
        backfill_frames.append(frame)

    for symbol in benchmarks:
        # tushare index_daily is quota-limited to 1 call/hour on this token, so
        # benchmarks come from the akshare eastmoney->sina->tencent fallback
        # chain (the same normalization the pipeline applies to index_history
        # frames).
        frame, endpoint_name = akshare_source._index_history(
            DataRequest(
                endpoint="index_history",
                symbols=(symbol,),
                start_date=BACKFILL_START,
                end_date=BACKFILL_END,
            )
        )
        canonical = _canonical_index_frame(frame, symbol, endpoint_name, ingested)
        print(f"benchmark {symbol} via {endpoint_name} rows={len(canonical)}")
        backfill_frames.append(canonical)

    backfill = pd.concat(backfill_frames, ignore_index=True)

    # ---- guardrails before anything is published ------------------------- #
    existing_keys = {
        (str(row["symbol"]), pd.Timestamp(row["trade_date"]))
        for row in daily.to_dict("records")
    }
    overlap = [
        key
        for key in zip(backfill["symbol"], backfill["trade_date"])
        if (str(key[0]), pd.Timestamp(key[1])) in existing_keys
    ]
    if overlap:
        raise SystemExit(
            f"FATAL: {len(overlap)} backfill rows overlap existing daily_bar"
        )
    merged = pd.concat([daily, backfill], ignore_index=True)
    merged = merged.sort_values(["symbol", "trade_date"], kind="stable").reset_index(
        drop=True
    )

    # The published calendar is the intersection of benchmark sessions over
    # the merged window (same rule as rebuild_trading_calendar.py).
    benchmark_dates = [
        set(
            pd.to_datetime(
                merged.loc[merged["symbol"] == symbol, "trade_date"]
            ).dt.normalize()
        )
        for symbol in benchmarks
    ]
    sessions = sorted(set.intersection(*benchmark_dates))
    new_calendar = pd.DataFrame({"calendar_date": sessions, "is_trading_day": True})
    print(
        f"calendar sessions={len(new_calendar)} "
        f"{new_calendar['calendar_date'].min().date()}..{new_calendar['calendar_date'].max().date()}"
    )

    quarantine = pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
    adjusted = build_adjusted_bars(
        merged,
        corporate_action,
        quarantine,
        coverage,
        symbols=tuple(symbols),
    )
    severity = adjusted["quality_severity"].value_counts().to_dict()
    print(
        f"adjusted_bar rows={len(adjusted)} symbols={adjusted['symbol'].nunique()} "
        f"severity={severity}"
    )

    tables = {
        "daily_bar": merged,
        "adjusted_bar": adjusted,
        "security_master": master,
        "security_master_coverage": master_coverage,
        "corporate_action": corporate_action,
        "corporate_action_quarantine": quarantine,
        "corporate_action_coverage": coverage,
        "trading_calendar": new_calendar,
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
