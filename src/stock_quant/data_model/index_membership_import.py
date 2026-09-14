"""Normalize an official index-membership snapshot into immutable facts.

One shared implementation for both operator entry points -- the standalone
script ``project/refresh_index_membership.py`` and the
``python -m stock_quant data index-membership prepare`` command -- so the two
surfaces can never drift from the documented conversion contract.

Everything here is offline and evidence-bound: the raw snapshot and the
official source document must already be stored by the operator, and their
SHA-256 digests are *required* arguments, so an import can never publish
unevidenced facts. No function performs network access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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

_CANONICAL_UNIVERSE_ID = re.compile(
    r"(?:csi300|csi500|csi1000|sse50|sse180|szse100|custom_[a-z0-9_]+)"
)


@dataclass(frozen=True)
class MembershipImportResult:
    """The canonical frame plus the content hash a definition must pin."""

    universe_id: str
    frame: pd.DataFrame
    content_hash: str


def read_snapshot_rows(path: Path) -> pd.DataFrame:
    """Read one already-downloaded membership list (``.csv`` or ``.parquet``)."""
    path = Path(path)
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
    if not _CANONICAL_UNIVERSE_ID.fullmatch(universe_id):
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


def prepare_membership_file(
    input_path: Path,
    *,
    universe_id: str,
    source: str,
    source_url: str,
    snapshot_sha256: str,
    source_document_sha256: str,
    effective_date: date,
    announcement_date: date,
    reason: str = MembershipReason.INITIAL_CONSTITUENT.value,
    output: Path,
) -> MembershipImportResult:
    """Read, normalize, hash and write one membership import, end to end.

    ``output`` is written as Parquet (``.parquet`` suffix) or CSV (anything
    else) only after every row validated: a rejected import leaves no partial
    artifact behind. The returned ``content_hash`` is the
    ``membership_table_sha256`` the frozen universe definition must pin.
    """
    rows = read_snapshot_rows(input_path)
    facts = build_membership_facts(
        rows,
        universe_id=universe_id,
        source=source,
        source_url=source_url,
        snapshot_sha256=snapshot_sha256,
        source_document_sha256=source_document_sha256,
        effective_date=effective_date,
        announcement_date=announcement_date,
        reason=reason,
    )
    frame = membership_frame(facts)
    content_hash = membership_content_hash(facts)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".parquet":
        frame.to_parquet(output, index=False)
    else:
        frame.to_csv(output, index=False)
    return MembershipImportResult(
        universe_id=universe_id, frame=frame, content_hash=content_hash
    )


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
