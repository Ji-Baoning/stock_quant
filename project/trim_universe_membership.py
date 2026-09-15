"""Derive a tradable-universe dataset from the frozen csi300 membership facts.

Status: migration.

A frozen membership universe can list symbols the published ``security_master``
does not carry, and the acceptance gate rejects every such symbol as a FATAL
``UNIVERSE_UNKNOWN_SYMBOL`` with no bypass.  This script narrows the membership
table to the intersection with the master and republishes the dataset, so the
point-in-time filter can actually run.  The default pairing takes
``custom_csi300_tw`` (792 facts, 682 symbols) down to
``custom_csi300_tw_tradable`` against a 659-row master, leaving 766 rows over
657 symbols; both ids are overridable with ``--base-universe-id`` and
``--tradable-universe-id``.

**This does not remove survivorship bias.**  The tracked pool keeps only
symbols still listed (``list_status=L``); index members that have since
delisted are absent from it, so trimming changes which symbols the filter can
*choose from*, not how the pool was chosen.  An unbiased CSI300 needs daily
bars for every historical member, which is external data acquisition and out
of scope here.  The derived universe therefore carries the ``+tradable``
suffix and must never be presented as CSI300.

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

BASE_UNIVERSE_ID = "custom_csi300_tw"
TRADABLE_UNIVERSE_ID = "custom_csi300_tw_tradable"
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
    tradable_universe_id: str = TRADABLE_UNIVERSE_ID,
) -> dict:
    """Render the definition document for the trimmed facts.

    ``coverage_start`` / ``coverage_end`` come from the dataset's ``daily_bar``
    window, mirroring ``build_csi300_universe.py`` -- coverage is what the
    dataset actually covers, never what the facts aspire to.
    """
    return {
        "schema_version": base.schema_version,
        "universe_id": tradable_universe_id,
        "rules_version": base.rules_version + TRADABLE_SUFFIX,
        "membership_table_sha256": membership_content_hash(facts),
        "evidence_summary_sha256": base.evidence_summary_sha256,
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
    }


def run(
    root: Path,
    config: ProjectConfig,
    *,
    base_universe_id: str = BASE_UNIVERSE_ID,
    tradable_universe_id: str = TRADABLE_UNIVERSE_ID,
) -> int:
    """Publish the trimmed dataset and write the matching definition."""
    publisher = DatasetPublisher(root)
    version = publisher.current().version
    print(f"base dataset_version={version}")

    definition_path = root / "configs" / "universes" / f"{base_universe_id}.yml"
    base = load_universe_definition(definition_path)

    reader = DatasetReader(root)
    with reader.open(version) as dataset:
        tables = {name: dataset.read(name) for name in dataset.tables}
        build_config = dataset.manifest.get("build_config")

    master = tables["security_master"]
    membership = tables["universe_membership"]
    print(
        f"membership rows={len(membership)} "
        f"symbols={membership['symbol'].nunique()} "
        f"master symbols={master['symbol'].nunique()}"
    )

    trimmed = retarget_universe_id(
        trim_membership_rows(membership, set(master["symbol"])),
        tradable_universe_id,
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
        tradable_universe_id=tradable_universe_id,
    )

    tables["universe_membership"] = membership_frame(facts)
    published = publisher.publish(
        tables, QualityReport(), build_config=build_config
    )
    print(f"dataset_version={published.version}")
    print(f"membership_table_sha256={definition['membership_table_sha256']}")

    out_path = root / "configs" / "universes" / f"{tradable_universe_id}.yml"
    header = (
        "# Frozen universe definition generated by "
        "trim_universe_membership.py.\n"
        f"# Derived from {base_universe_id} by keeping only the symbols\n"
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
    parser.add_argument(
        "--base-universe-id",
        default=BASE_UNIVERSE_ID,
        help="Frozen base universe definition the facts are trimmed from.",
    )
    parser.add_argument(
        "--tradable-universe-id",
        default=TRADABLE_UNIVERSE_ID,
        help="Derived universe id for the master-trimmed facts.",
    )
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(
        root,
        config,
        base_universe_id=args.base_universe_id,
        tradable_universe_id=args.tradable_universe_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
