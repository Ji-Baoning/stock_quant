"""Extend ``security_master`` to cover the whole frozen membership universe.

Status: migration.

The point-in-time membership evidence (``custom_csi300_tw``, 682 distinct
symbols from official ``index_weight`` monthly snapshots) can only pass the
research ``index_membership_evidence`` gate when the dataset's
``security_master`` accounts for every member symbol: the gate intersects the
facts with the master boundaries and rejects unknown symbols.  The 30-symbol
engineering master therefore blocks the real universe even though the
membership facts themselves are complete.

This script follows the documented offline-rebuild pattern
(``rebuild_offline_real_dataset.py`` / ``extend_history_offline.py``):

1. Pull the required whole-market tushare ``stock_basic`` snapshot through the
   same transport stack the pipeline uses, and store it in the immutable raw
   store (evidence on disk, hash-pinned).
2. Refresh the existing master rows from the snapshot exactly like
   ``DataPipeline._apply_stock_basic`` does.
3. Add master rows for every membership symbol missing from the master —
   listing facts come verbatim from the snapshot (name, list/delist dates,
   status); exchange and board come from the canonical symbol rules.  A
   membership symbol the snapshot cannot account for is a hard failure: no
   listing fact is ever invented.
4. Rebuild ``security_master_coverage`` for every master symbol from the same
   snapshot and republish the dataset carrying the membership table and the
   baseline manifest's build_config (calendar evidence).

ENGINEERING artifact: the price/adjusted tables are carried unchanged; only
the master, its coverage evidence and the membership table move.  No formal
ACCEPTED record may pin this version without the operator acceptance flow.
"""

from __future__ import annotations

import argparse
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stock_quant.config import load_project_config
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.security_master import (
    MASTER_SOURCE_STOCK_BASIC,
    master_coverage_frame,
    master_coverage_record,
)
from stock_quant.data_model.trading_rules import board_of_symbol
from stock_quant.data_pipeline import _apply_stock_basic
from stock_quant.data_quality.models import QualityReport
from stock_quant.data_sources.base import DataRequest, FetchResult
from stock_quant.data_sources.raw_store import RawStore
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.project_root import resolve_project_root


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stock_basic_frame(
    source: TushareSource, root: Path
) -> tuple[pd.DataFrame, str, str]:
    """Fetch and raw-store one whole-market snapshot; return frame + hashes."""
    now = _utc_now()
    start = pd.Timestamp(now.date()) - pd.Timedelta(days=90)
    result = source.fetch(
        DataRequest(
            "stock_basic",
            (),
            start.date(),
            now.date(),
            {},
        )
    )
    snapshot = RawStore(root).save(result)
    frame = result.frame
    # The default snapshot lists only active securities; membership history
    # contains delisted symbols, so pull the delisted/pause statuses too and
    # merge them into one whole-market frame (dedupe by ts_code, listed wins).
    # The merged frame is raw-stored as well, so every master fact added from
    # the delisted slice resolves to pinned bytes under data/raw.
    extra = []
    for status in ("D", "P"):
        extra.append(
            source._client.stock_basic(
                fields="ts_code,name,exchange,list_date,delist_date,list_status",
                list_status=status,
            )
        )
        time.sleep(2.0)
    if extra:
        merged = pd.concat([frame, *extra], ignore_index=True)
        merged = merged.drop_duplicates(subset=["ts_code"], keep="first")
        merged = merged.reset_index(drop=True)
        merged_snapshot = RawStore(root).save(
            FetchResult(
                source=result.source,
                endpoint=result.endpoint,
                request_key=result.request_key + "_merged_LDP",
                frame=merged,
                metadata={
                    **result.metadata,
                    "merged_list_statuses": "L,D,P",
                },
            )
        )
        frame = merged
        snapshot = merged_snapshot
    canonical = frame[["ts_code", "name", "list_date", "delist_date", "list_status"]]
    payload = canonical.to_csv(index=False).encode("utf-8")
    return frame, hashlib.sha256(payload).hexdigest(), str(snapshot.sha256)


def run(root: Path) -> int:
    config = load_project_config(root)
    source = TushareSource(config.sources["tushare"], allow_auto_transport=True)
    sdk_version = getattr(source, "_sdk_version", None)
    raw, snapshot_sha256, store_sha256 = _stock_basic_frame(source, root)
    print(
        f"stock_basic rows={len(raw)} snapshot_sha256={snapshot_sha256} "
        f"raw_store_sha256={store_sha256}"
    )

    publisher = DatasetPublisher(root)
    version = publisher.current().version
    with DatasetReader(root).open(version) as dataset:
        tables: dict[str, pd.DataFrame] = {
            name: dataset.read(name) for name in dataset.tables
        }
        build_config = dataset.manifest.get("build_config")

    master = tables["security_master"]
    applied = _apply_stock_basic(
        master,
        raw,
        store_sha256,
        sdk_version=sdk_version,
        checked_at=_utc_now(),
    )
    if applied is None:
        raise SystemExit(
            "stock_basic snapshot cannot account for every pinned master symbol"
        )
    refreshed_master, _ = applied

    facts: dict[str, dict[str, Any]] = {}
    for record in raw.to_dict("records"):
        ts_code = str(record.get("ts_code", "")).strip()
        if ts_code:
            facts[ts_code] = record

    membership = tables["universe_membership"]
    member_symbols = sorted(set(membership["symbol"].astype(str)))
    pinned = {str(row["symbol"]) for row in refreshed_master.to_dict("records")}
    additions: list[dict[str, object]] = []
    missing: list[str] = []
    for symbol in member_symbols:
        if symbol in pinned:
            continue
        fact = facts.get(symbol)
        if fact is None:
            missing.append(symbol)
            continue
        list_date = _date_cell(fact.get("list_date"))
        delist_date = _date_cell(fact.get("delist_date"))
        if list_date is None:
            missing.append(symbol)
            continue
        code, exchange = symbol.split(".")
        additions.append(
            {
                "symbol": symbol,
                "name": str(fact.get("name", "")).strip() or symbol,
                "exchange": exchange,
                "board": board_of_symbol(symbol),
                "list_date": pd.Timestamp(list_date),
                "delist_date": (
                    pd.Timestamp(delist_date) if delist_date else pd.NaT
                ),
                "list_status": str(fact.get("list_status", "L")).strip() or "L",
            }
        )
    if missing:
        raise SystemExit(
            "stock_basic snapshot cannot account for membership symbols: "
            + ", ".join(sorted(missing))
        )

    combined = pd.concat(
        [refreshed_master, pd.DataFrame(additions, columns=list(master.columns))],
        ignore_index=True,
    )
    combined = (
        combined.drop_duplicates(subset=["symbol"], keep="last")
        .sort_values("symbol", kind="stable")
        .reset_index(drop=True)
    )

    checked_at = _utc_now()
    coverage_rows = [
        master_coverage_record(
            str(row["symbol"]),
            list_date=row["list_date"],
            delist_date=(
                None if pd.isna(row["delist_date"]) else row["delist_date"]
            ),
            list_status=row["list_status"],
            source=MASTER_SOURCE_STOCK_BASIC,
            snapshot_sha256=store_sha256,
            sdk_version=sdk_version,
            checked_at=checked_at,
        )
        for row in combined.to_dict("records")
    ]

    tables["security_master"] = combined
    tables["security_master_coverage"] = master_coverage_frame(coverage_rows)
    published = publisher.publish(tables, QualityReport(), build_config=build_config)
    print(f"master_symbols={len(combined)} additions={len(additions)}")
    print(f"dataset_version={published.version}")
    return 0


def _date_cell(value: Any):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    return pd.to_datetime(text).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extend security_master to the membership universe from one "
            "whole-market stock_basic snapshot (network: one request)."
        )
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    return run(resolve_project_root(args.root))


if __name__ == "__main__":
    raise SystemExit(main())
