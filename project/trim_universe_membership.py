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

import argparse
from collections.abc import Collection, Sequence
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    membership_content_hash,
    membership_frame,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.project_root import resolve_project_root
from stock_quant.research.universe import (
    UniverseDefinition,
    load_universe_definition,
)

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


def run(root: Path, config: ProjectConfig) -> int:
    """Publish the trimmed dataset and write the matching definition."""
    publisher = DatasetPublisher(root)
    version = publisher.current().version
    print(f"base dataset_version={version}")

    definition_path = root / "configs" / "universes" / f"{BASE_UNIVERSE_ID}.yml"
    base = load_universe_definition(definition_path)

    reader = DatasetReader(root)
    with reader.open(version) as dataset:
        tables = {name: dataset.read(name) for name in dataset.tables}

    master = tables["security_master"]
    membership = tables["universe_membership"]
    print(
        f"membership rows={len(membership)} "
        f"symbols={membership['symbol'].nunique()} "
        f"master symbols={master['symbol'].nunique()}"
    )

    trimmed = retarget_universe_id(
        trim_membership_rows(membership, set(master["symbol"])),
        TRADABLE_UNIVERSE_ID,
    )
    facts = facts_from_rows(trimmed)
    print(
        f"trimmed rows={len(trimmed)} symbols={trimmed['symbol'].nunique()} "
        f"status={trimmed['status'].value_counts().to_dict()} "
        f"reason={trimmed['reason'].value_counts().to_dict()}"
    )

    daily = tables["daily_bar"]
    coverage_start = pd.to_datetime(daily["trade_date"]).min().date()
    coverage_end = pd.to_datetime(daily["trade_date"]).max().date()
    definition = build_tradable_definition(
        base,
        facts=facts,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )

    tables["universe_membership"] = membership_frame(facts)
    published = publisher.publish(tables, QualityReport())
    print(f"dataset_version={published.version}")
    print(f"membership_table_sha256={definition['membership_table_sha256']}")

    out_path = root / "configs" / "universes" / f"{TRADABLE_UNIVERSE_ID}.yml"
    header = (
        "# Frozen universe definition generated by "
        "trim_universe_membership.py.\n"
        f"# Derived from {BASE_UNIVERSE_ID} by keeping only the symbols\n"
        "# present in the dataset's security_master.  NOT an unbiased\n"
        "# CSI300 universe: the underlying pool is hand-picked and still\n"
        "# listed, so survivorship bias remains.\n"
    )
    out_path.write_text(
        header + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"definition={out_path}")
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
