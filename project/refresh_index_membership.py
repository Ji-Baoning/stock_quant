"""Convert an official index-membership snapshot into immutable facts.

Status: migration.

This is an offline operator tool: it reads one already-downloaded membership
list (CSV or Parquet), normalizes every row into ``MembershipFact`` records
bound to the operator-supplied snapshot/document evidence, and emits the
canonical ``universe_membership`` frame plus its content hash. It performs no
network access whatsoever -- the raw snapshot and the official document must
already be stored, and their SHA-256 digests are *required* arguments so an
import can never publish unevidenced facts.

The normalization itself lives in
:mod:`stock_quant.data_model.index_membership_import` and is shared with the
``python -m stock_quant data index-membership prepare`` command, which takes
the same arguments; this thin script stays the PYTHONPATH-free entry point for
crontab-style use. Exit code 1 means the import was rejected (contract or
overlap violation); nothing is written in that case.

Example::

    PYTHONPATH=src python project/refresh_index_membership.py \
        --universe-id csi300 \
        --input data/raw/csi/members_2005.csv \
        --snapshot-sha256 <64-hex> --source-document-sha256 <64-hex> \
        --source csi_index_announcement \
        --source-url https://www.csindex.com.cn/announcement.pdf \
        --effective-date 2005-01-01 --announcement-date 2005-01-01 \
        --output data/membership/universe_membership.parquet

Per-row CSV columns ``raw_effective_from`` / ``raw_effective_to`` /
``announcement_date`` / ``reason`` override the CLI-level defaults, so one
file can carry additions (no end) and removals (finite end) at once.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from stock_quant.config import load_project_config
from stock_quant.data_model.index_membership_import import (
    build_membership_facts,  # noqa: F401  (re-exported for the script's tests)
    prepare_membership_file,
)
from stock_quant.data_model.universe_membership import MembershipReason
from stock_quant.project_root import resolve_project_root


def build_parser() -> argparse.ArgumentParser:
    """The converter CLI. Evidence and date arguments are mandatory."""
    parser = argparse.ArgumentParser(
        description=(
            "Normalize an official index-membership snapshot into immutable "
            "universe_membership facts (offline; no network access)."
        )
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--universe-id",
        required=True,
        help="canonical universe id, e.g. csi300 (or custom_<slug>)",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="path to the downloaded membership list (.csv or .parquet)",
    )
    parser.add_argument(
        "--snapshot-sha256",
        required=True,
        help="SHA-256 of the stored raw snapshot this import is bound to",
    )
    parser.add_argument(
        "--source-document-sha256",
        required=True,
        help="SHA-256 of the stored official source document",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="evidence source label, e.g. csi_index_announcement",
    )
    parser.add_argument(
        "--source-url",
        required=True,
        help="credential-free http(s) locator of the evidence document",
    )
    parser.add_argument(
        "--effective-date",
        required=True,
        type=date.fromisoformat,
        help="default raw_effective_from for rows without their own (ISO)",
    )
    parser.add_argument(
        "--announcement-date",
        required=True,
        type=date.fromisoformat,
        help="default announcement_date for rows without their own (ISO)",
    )
    parser.add_argument(
        "--reason",
        choices=tuple(reason.value for reason in MembershipReason),
        default=MembershipReason.INITIAL_CONSTITUENT.value,
        help="default reason for rows without their own",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="destination for the canonical frame (.parquet or .csv)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config, args=args)


def run(root, config, *, args) -> int:
    try:
        result = prepare_membership_file(
            args.input,
            universe_id=args.universe_id,
            source=args.source,
            source_url=args.source_url,
            snapshot_sha256=args.snapshot_sha256,
            source_document_sha256=args.source_document_sha256,
            effective_date=args.effective_date,
            announcement_date=args.announcement_date,
            reason=args.reason,
            output=args.output,
        )
    except (ValueError, TypeError) as error:
        print(f"membership import rejected: {error}", file=sys.stderr)
        return 1
    print(f"rows={len(result.frame)}")
    print(f"universe_id={result.universe_id}")
    print(f"membership_table_sha256={result.content_hash}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
