"""Derive a tradable-universe dataset from the frozen csi300 membership facts.

The frozen ``custom_csi300_ic`` universe (1221 facts, 948 symbols) cannot
drive any experiment: the acceptance gate rejects every symbol absent from
``security_master`` (30 rows) as a FATAL ``UNIVERSE_UNKNOWN_SYMBOL`` and has
no bypass.  This script narrows the membership table to the intersection with
the published master and republishes the dataset, so the point-in-time filter
can actually run.

**This does not remove survivorship bias.**  The 30-name pool is hand-picked
and still listed; trimming it to 28 changes which symbols the filter can
*choose from*, not how the pool was chosen.  An unbiased CSI300 universe needs
daily bars for roughly 918 more symbols since 2015, which is external data
acquisition and out of scope here.  The derived universe is therefore named
``custom_csi300_ic_tradable`` and must never be presented as CSI300.

The membership facts keep every evidence field (``snapshot_sha256``,
``source_document_sha256``, ``source_url`` ...) across the filter, so the
evidence chain to the sealed snapshot stays intact.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    membership_content_hash,
    membership_frame,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.research.universe import (
    UniverseDefinition,
    load_universe_definition,
)

ROOT = Path(__file__).resolve().parent
BASE_UNIVERSE_ID = "custom_csi300_ic"
TRADABLE_UNIVERSE_ID = "custom_csi300_ic_tradable"
TRADABLE_SUFFIX = "+tradable"
_DATE_COLUMNS = ("raw_effective_from", "raw_effective_to", "announcement_date")


def trim_membership_rows(
    membership: pd.DataFrame, master_symbols: Collection[str]
) -> pd.DataFrame:
    """Keep only the membership rows whose symbol is in the tradable master."""
    keep = set(master_symbols)
    trimmed = membership[membership["symbol"].isin(keep)]
    return trimmed.reset_index(drop=True)


def retarget_universe_id(
    rows: pd.DataFrame, universe_id: str
) -> pd.DataFrame:
    """Relabel derived rows onto the tradable universe id.

    ``UniverseResolver`` requires every resolved fact to carry the definition's
    own ``universe_id``, so the derived facts cannot keep the base label.  Only
    that one identity column changes; every evidence field rides along.
    """
    relabelled = rows.copy()
    relabelled["universe_id"] = universe_id
    return relabelled


def facts_from_rows(rows: pd.DataFrame) -> list[MembershipFact]:
    """Rebuild validated facts from table rows, coercing dates to ``date``.

    The frame is the canonical ``universe_membership`` layout, so this is a
    pure round trip; every evidence field rides along unchanged.
    """
    facts: list[MembershipFact] = []
    for record in rows.to_dict("records"):
        payload = dict(record)
        for column in _DATE_COLUMNS:
            value = payload.get(column)
            payload[column] = (
                None
                if value is None or pd.isna(value)
                else pd.Timestamp(value).date()
            )
        facts.append(MembershipFact.model_validate(payload))
    return facts


def build_tradable_definition(
    base: UniverseDefinition,
    *,
    facts: list[MembershipFact],
    coverage_start: date,
    coverage_end: date,
) -> dict:
    """Render the definition document for the trimmed facts.

    ``coverage_start`` / ``coverage_end`` come from the dataset's ``daily_bar``
    window, mirroring ``build_csi300_universe.py`` -- coverage is what the
    dataset actually covers, never what the facts aspire to.
    """
    return {
        "schema_version": base.schema_version,
        "universe_id": TRADABLE_UNIVERSE_ID,
        "rules_version": base.rules_version + TRADABLE_SUFFIX,
        "membership_table_sha256": membership_content_hash(facts),
        "evidence_summary_sha256": base.evidence_summary_sha256,
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
    }
