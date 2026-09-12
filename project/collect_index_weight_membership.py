"""Collect tushare ``index_weight`` snapshots into frozen universe membership.

This is the missing evidence link for the point-in-time universe: tushare's
``index_weight`` interface returns official index constituent weight snapshots
(one row per constituent per snapshot date), which are exactly the
point-in-time membership facts the walk-forward universe gate demands.

Pipeline (one command, network only in step 1):

1. Pull ``index_weight`` month by month and store every raw response under
   ``data/raw/csi/index_weight/`` (never committed; evidence on disk).
2. Write an evidence manifest hashing every snapshot file (this manifest's
   SHA-256 becomes the definition's ``evidence_summary_sha256``).
3. Consolidate the snapshots into one import CSV whose rows carry per-row
   ``raw_effective_from`` / ``raw_effective_to`` / ``announcement_date`` so a
   single import can encode the whole history.  Membership windows use the
   attested-boundary convention: a constituent stays a member through the day
   before the first snapshot that no longer lists it, and a newly listed
   constituent becomes a member on its first listing snapshot.  Monthly
   snapshots therefore date mid-month rebalances with up to ~1 month of lag —
   a documented approximation.
4. Import through the shared, evidence-bound conversion
   (:func:`prepare_membership_file`) and republish the dataset with the
   resulting ``universe_membership`` table.
5. Emit the frozen universe definition YAML with the real hashes so the
   walk-forward specs can pin it.

Permission note: ``index_weight`` requires a tushare token with sufficient
points (2000-point tier); the committed low-point token cannot call it.

Default ``--universe-id`` is a ``custom_`` pool because the canonical
``csi300`` cardinality check demands exactly 300 members on every trading day,
which the monthly-snapshot lag cannot guarantee.  A custom pool skips only the
cardinality check; the evidence chain (snapshot hashes, source document,
announcement dates) is identical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from stock_quant.config import SourceConfig
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.index_membership_import import (
    prepare_membership_file,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.data_sources.tushare import TushareSource

ROOT = Path(__file__).resolve().parent


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _month_ends(start_yearmonth: str, end_yearmonth: str) -> list[str]:
    """``YYYYMM`` values from start to end inclusive."""
    start = pd.Period(start_yearmonth, freq="M")
    end = pd.Period(end_yearmonth, freq="M")
    if start > end:
        raise SystemExit(f"start {start} is after end {end}")
    return [
        str(period).replace("-", "")
        for period in pd.period_range(start, end, freq="M")
    ]


def _month_end_date(yearmonth: str) -> date:
    period = pd.Period(yearmonth, freq="M")
    return period.end_time.date()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect tushare index_weight snapshots into a frozen universe "
            "definition (network: one index_weight call per month)."
        )
    )
    parser.add_argument("--index-code", default="399300.SZ")
    parser.add_argument("--start", default="201412", help="YYYYMM first month")
    parser.add_argument("--end", default="202608", help="YYYYMM last month")
    parser.add_argument(
        "--universe-id",
        default="custom_csi300_tw",
        help="custom_<slug> (no cardinality check) or csi300 (exactly 300/day)",
    )
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    parser.add_argument("--skip-pull", action="store_true",
                        help="reuse stored snapshots; only re-run steps 2-5")
    args = parser.parse_args()

    token = os.environ.get("TUSHARE_TOKEN")
    if not token and not args.skip_pull:
        raise SystemExit("TUSHARE_TOKEN is required (index_weight permission)")

    snapshot_dir = ROOT / "data" / "raw" / "csi" / "index_weight"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    months = _month_ends(args.start, args.end)

    # ---- step 1: pull and store every monthly snapshot --------------------
    if not args.skip_pull:
        # ``allow_auto_transport=True`` keeps this offline collector working
        # after the published path became strict.  Rewiring it to consume the
        # relay as its transport (and dropping the TUSHARE_TOKEN gate above) is
        # design spec §4, stage 4 -- not this change.
        source = TushareSource(SourceConfig(), allow_auto_transport=True)
        client = source._client
        for index, yearmonth in enumerate(months):
            target = snapshot_dir / f"{args.index_code}_{yearmonth}.csv"
            if target.exists():
                continue
            frame = client.index_weight(
                index_code=args.index_code,
                start_date=f"{yearmonth}01",
                end_date=f"{yearmonth}31",
            )
            if frame is None or frame.empty:
                print(f"[{index + 1}/{len(months)}] {yearmonth}: EMPTY (kept)")
                frame = pd.DataFrame(
                    columns=["index_code", "con_code", "trade_date", "weight"]
                )
            frame.to_csv(target, index=False)
            print(
                f"[{index + 1}/{len(months)}] {yearmonth}: {len(frame)} rows"
            )
            time.sleep(args.pause_seconds)

    # ---- step 2: evidence manifest ----------------------------------------
    snapshot_files = sorted(snapshot_dir.glob(f"{args.index_code}_*.csv"))
    if not snapshot_files:
        raise SystemExit(f"no snapshots stored under {snapshot_dir}")
    manifest = {
        "index_code": args.index_code,
        "source": "tushare_index_weight",
        "source_url": "https://api.tushare.pro",
        "endpoint": "index_weight",
        "note": (
            "Official index constituent weight snapshots re-published by the "
            "tushare data vendor; one file per calendar month, rows attested "
            "at each snapshot trade_date."
        ),
        "files": {
            path.name: _sha256_file(path) for path in snapshot_files
        },
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8"
    )
    manifest_sha256 = _sha256_file(manifest_path)
    print(f"manifest={manifest_path.name} sha256={manifest_sha256}")

    # ---- step 3: consolidate into one attested-window snapshot CSV --------
    frames = [pd.read_csv(path, dtype=str) for path in snapshot_files]
    rows = pd.concat(frames, ignore_index=True)
    rows = rows[rows["con_code"].notna() & (rows["con_code"] != "")]
    rows["trade_date"] = pd.to_datetime(rows["trade_date"]).dt.date
    global_dates = sorted(set(rows["trade_date"]))
    next_date = {
        day: global_dates[i + 1] for i, day in enumerate(global_dates[:-1])
    }
    last_global = global_dates[-1]

    by_symbol: dict[str, set[date]] = {}
    for record in rows.to_dict("records"):
        by_symbol.setdefault(str(record["con_code"]), set()).add(
            record["trade_date"]
        )

    consolidated: list[dict[str, object]] = []
    for symbol in sorted(by_symbol):
        present = sorted(by_symbol[symbol])
        streak: list[date] = [present[0]]
        streaks: list[list[date]] = []
        for day in present[1:]:
            previous = streak[-1]
            between = [d for d in global_dates if previous < d < day]
            if between:  # an intervening snapshot omitted the symbol
                streaks.append(streak)
                streak = [day]
            else:
                streak.append(day)
        streaks.append(streak)
        for order, window in enumerate(streaks):
            first = window[0]
            last = window[-1]
            open_ended = last == last_global
            if open_ended:
                effective_to_text = ""
            else:
                boundary = next_date[last] - pd.Timedelta(days=1)
                effective_to_text = boundary.date().isoformat()
            consolidated.append(
                {
                    "symbol": symbol,
                    "raw_effective_from": first.isoformat(),
                    "raw_effective_to": effective_to_text,
                    "announcement_date": first.isoformat(),
                    "reason": (
                        "initial_constituent"
                        if open_ended and order == 0
                        else "regular_rebalance"
                    ),
                }
            )
    snapshot_csv = ROOT / "data" / "raw" / "csi" / (
        f"{args.universe_id}_membership_snapshot.csv"
    )
    snapshot_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(consolidated).to_csv(snapshot_csv, index=False)
    snapshot_sha256 = _sha256_file(snapshot_csv)
    print(
        f"snapshot={snapshot_csv.name} facts={len(consolidated)} "
        f"sha256={snapshot_sha256}"
    )

    # ---- step 4: evidence-bound import ------------------------------------
    result = prepare_membership_file(
        snapshot_csv,
        universe_id=args.universe_id,
        source="tushare_index_weight",
        source_url="https://api.tushare.pro",
        snapshot_sha256=snapshot_sha256,
        source_document_sha256=manifest_sha256,
        effective_date=_month_end_date(args.start),
        announcement_date=_month_end_date(args.start),
        output=ROOT / "data" / "membership" / f"{args.universe_id}.parquet",
    )
    print(
        f"membership facts={len(result.frame)} "
        f"membership_table_sha256={result.content_hash}"
    )

    # ---- step 5: republish the dataset with the membership table ----------
    publisher = DatasetPublisher(ROOT)
    version = publisher.current().version
    with DatasetReader(ROOT).open(version) as dataset:
        tables = {name: dataset.read(name) for name in dataset.tables}
    tables["universe_membership"] = result.frame
    published = publisher.publish(tables, QualityReport())
    print(f"dataset_version={published.version}")

    # ---- step 6: emit the frozen universe definition ----------------------
    with DatasetReader(ROOT).open(published.version) as dataset:
        daily = dataset.read("daily_bar")
    coverage_start = pd.to_datetime(daily["trade_date"]).min().date()
    coverage_end = pd.to_datetime(daily["trade_date"]).max().date()
    definition = {
        "schema_version": 1,
        "universe_id": args.universe_id,
        "membership_table_sha256": result.content_hash,
        "evidence_summary_sha256": manifest_sha256,
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "rules_version": "tushare-index-weight-monthly-v1",
    }
    definition_path = (
        ROOT / "configs" / "universes" / f"{args.universe_id}.yml"
    )
    header = (
        "# Frozen universe definition generated by "
        "collect_index_weight_membership.py.\n"
        "# evidence_summary_sha256 = SHA-256 of data/raw/csi/index_weight/"
        "manifest.json,\n"
        "# which pins the SHA-256 of every monthly index_weight snapshot "
        "file.\n"
    )
    definition_path.write_text(
        header
        + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    derived = _sha256_file(definition_path)
    print(f"definition={definition_path} (universe_version={derived})")
    print("next: research specs pin universe_definition: "
          f"{args.universe_id} + universe_version: {derived[:16]}…")


if __name__ == "__main__":
    main()
