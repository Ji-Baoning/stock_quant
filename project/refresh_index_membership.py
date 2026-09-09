"""Convert an official index-membership snapshot into immutable facts.

This is an offline operator tool: it reads one already-downloaded membership
list (CSV or Parquet), normalizes every row into ``MembershipFact`` records
bound to the operator-supplied snapshot/document evidence, and emits the
canonical ``universe_membership`` frame plus its content hash. It performs no
network access whatsoever -- the raw snapshot and the official document must
already be stored, and their SHA-256 digests are *required* arguments so an
import can never publish unevidenced facts.

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
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from stock_quant.data_model.universe_membership import (
    MembershipFact,
    MembershipReason,
    membership_content_hash,
    membership_frame,
)

_REASON_CHOICES = tuple(reason.value for reason in MembershipReason)


def build_parser() -> argparse.ArgumentParser:
    """The converter CLI. Evidence and date arguments are mandatory."""
    parser = argparse.ArgumentParser(
        description=(
            "Normalize an official index-membership snapshot into immutable "
            "universe_membership facts (offline; no network access)."
        )
    )
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
        choices=_REASON_CHOICES,
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


def _read_rows(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str)


def build_membership_facts(
    rows: pd.DataFrame,
    *,
    universe_id: str,
    source: str,
    source_url: str,
    snapshot_sha256: str,
    source_document_sha256: str,
    effective_date: date,
    announcement_date: date,
    reason: str = MembershipReason.INITIAL_CONSTITUENT.value,
) -> list[MembershipFact]:
    """Normalize input rows into validated ``MembershipFact`` records.

    Every row must carry a canonical ``symbol``; the optional per-row columns
    ``raw_effective_from``, ``raw_effective_to``, ``announcement_date`` and
    ``reason`` override the call-level defaults. ``status`` is derived from
    the end date (active when open, removed when finite), which the fact
    contract enforces anyway. Invalid rows raise ``ValidationError`` and the
    whole import fails loudly.
    """
    if not re.fullmatch(
        r"(?:csi300|csi500|csi1000|sse50|sse180|szse100|custom_[a-z0-9_]+)",
        universe_id,
    ):
        raise ValueError(
            f"universe_id must be a canonical index or custom_<slug>: "
            f"{universe_id!r}"
        )
    if "symbol" not in rows.columns:
        raise ValueError("input rows must carry a 'symbol' column")
    facts: list[MembershipFact] = []
    for record in rows.to_dict("records"):
        facts.append(
            MembershipFact.model_validate(
                _fact_payload(
                    record,
                    universe_id=universe_id,
                    source=source,
                    source_url=source_url,
                    snapshot_sha256=snapshot_sha256,
                    source_document_sha256=source_document_sha256,
                    effective_date=effective_date,
                    announcement_date=announcement_date,
                    reason=reason,
                )
            )
        )
    return facts


def _fact_payload(
    record: dict[str, Any],
    *,
    universe_id: str,
    source: str,
    source_url: str,
    snapshot_sha256: str,
    source_document_sha256: str,
    effective_date: date,
    announcement_date: date,
    reason: str,
) -> dict[str, Any]:
    def _row_date(column: str, fallback: date) -> date:
        value = record.get(column)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return fallback
        text = str(value).strip()
        if not text or text.lower() in {"nan", "nat", "none"}:
            return fallback
        return pd.Timestamp(text).date()

    end = _row_date("raw_effective_to", pd.NaT)
    payload = {
        "universe_id": universe_id,
        "symbol": str(record["symbol"]).strip(),
        "raw_effective_from": _row_date("raw_effective_from", effective_date),
        "raw_effective_to": None if pd.isna(end) else end,
        "announcement_date": _row_date(
            "announcement_date", announcement_date
        ),
        "status": "removed" if not pd.isna(end) else "active",
        "reason": _row_text(record.get("reason")) or reason,
        "source": source,
        "source_url": source_url,
        "snapshot_sha256": snapshot_sha256,
        "source_document_sha256": source_document_sha256,
    }
    return payload


def _row_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    rows = _read_rows(args.input)
    try:
        facts = build_membership_facts(
            rows,
            universe_id=args.universe_id,
            source=args.source,
            source_url=args.source_url,
            snapshot_sha256=args.snapshot_sha256,
            source_document_sha256=args.source_document_sha256,
            effective_date=args.effective_date,
            announcement_date=args.announcement_date,
            reason=args.reason,
        )
        frame = membership_frame(facts)
        content_hash = membership_content_hash(facts)
    except (ValueError, TypeError) as error:
        print(f"membership import rejected: {error}", file=sys.stderr)
        return 1
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".parquet":
        frame.to_parquet(output, index=False)
    else:
        frame.to_csv(output, index=False)
    print(f"rows={len(frame)}")
    print(f"universe_id={args.universe_id}")
    print(f"membership_table_sha256={content_hash}")
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
