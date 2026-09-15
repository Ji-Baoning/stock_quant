"""Expand ``configs/universe.yml`` to the published ``security_master`` set.

Status: migration.

Stage B of the full-composition campaign (owner decision 2026-09-14).  The
frozen csi300 membership facts name 682 symbols, but only 28 of them are in
the 30-row engineering master, so the point-in-time filter can see almost
none of its own members.  This script makes the tracked universe and the
master agree again in one step:

1. read one published dataset version (default: ``CURRENT``);
2. drop the master rows whose ``delist_date`` is set, together with their
   ``security_master_coverage`` rows -- *unless* ``--keep-delisted`` is given;
3. republish the dataset carrying the baseline's ``build_config``;
4. rewrite ``configs/universe.yml`` so the ``universe_master_mismatch``
   contract (set equality, ``data_pipeline.py:907``) holds.

Why the delisted members are dropped in this stage
--------------------------------------------------
``data update`` refreshes the master from exactly one whole-market tushare
``stock_basic`` request (``data_pipeline.py:1497``), whose default
``list_status`` is ``L`` -- listed only.  ``_apply_stock_basic`` fails the
run with a FATAL ``master_snapshot_incomplete`` when the snapshot cannot
account for every master symbol, so a master carrying delisted members can
never advance.  Pulling ``L``/``D``/``P`` and merging them is stage A.

Entries already present in the old ``configs/universe.yml`` are carried
verbatim (their curation survives); every new entry is generated from the
master row and marked with a ``csi300_pit_member`` boundary tag.

Survivorship bias is *not* removed by this script: it narrows the pool to
symbols that are still listed, which is exactly the bias the universe
header warns about.
"""

from __future__ import annotations

import argparse
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_quality.models import QualityReport
from stock_quant.project_root import resolve_project_root
from stock_quant.safe_yaml import read_yaml

MEMBER_TAG = "csi300_pit_member"
ENGINEERING_TAG = "engineering_sample"

_HEADER = (
    "# 追踪宇宙定义（tracked universe）——由 expand_universe_to_membership.py 生成。\n"
    "#\n"
    "# 内容 = 已发布数据版本 security_master 的全部标的；与 master 的集合相等是\n"
    "# data_pipeline._universe_master_issues 的硬契约，二者必须逐符号一致。\n"
    "# 其中 csi300_pit_member 标记的标的有官方 index_weight 月度快照的时点成员证据\n"
    "# （custom_csi300_tw）；engineering_sample 标记的 2 只是最初的工程边界样本。\n"
    "#\n"
    "# 这不是推荐，也不是无偏 CSI300：本阶段只保留仍在市（list_status=L）的标的，\n"
    "# 25 只已退市成员不在其中，幸存者偏差保留在池子里。\n"
)


def drop_delisted(
    master: pd.DataFrame, coverage: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Remove every master row that has a ``delist_date``, and its coverage row.

    Returns the filtered frames plus the dropped symbols, so the caller can
    report exactly which names left the universe.
    """
    if "delist_date" not in master.columns:
        raise ValueError("master frame has no delist_date column")
    delisted = master.loc[master["delist_date"].notna(), "symbol"].astype(str)
    dropped = sorted(delisted)
    kept_master = master[~master["symbol"].astype(str).isin(set(dropped))]
    kept_coverage = coverage[~coverage["symbol"].astype(str).isin(set(dropped))]
    return (
        kept_master.reset_index(drop=True),
        kept_coverage.reset_index(drop=True),
        dropped,
    )


def _generated_entry(
    row: Mapping[str, Any], *, as_of: date, member: bool
) -> dict[str, Any]:
    """One universe entry from a master row, with a machine-written reason."""
    symbol = str(row["symbol"])
    if member:
        tag = MEMBER_TAG
        reason = (
            "CSI300 时点成分（官方 index_weight 月度快照证据链 custom_csi300_tw）；"
            "自动生成的追踪条目，非推荐。"
        )
    else:
        tag = ENGINEERING_TAG
        reason = (
            "最初的工程边界样本，不属于 CSI300 时点成员表；随 master 一并追踪，非推荐。"
        )
    return {
        "symbol": symbol,
        "name_at_selection": str(row["name"]) or symbol,
        "exchange": str(row["exchange"]),
        "board": str(row["board"]),
        "selected_as_of": as_of,
        "boundary_tags": [tag],
        "selection_reason": reason,
    }


def expand_universe_document(
    old_document: Mapping[str, Any] | None,
    master: pd.DataFrame,
    *,
    member_symbols: Collection[str],
    as_of: date,
) -> dict[str, Any]:
    """Build the new universe document, carrying curated entries verbatim.

    A symbol that already has an entry keeps it whole -- including its
    ``selection_reason`` prose and ``selected_as_of`` -- so regenerating the
    file never silently rewrites human curation.
    """
    kept: dict[str, dict[str, Any]] = {}
    if isinstance(old_document, Mapping):
        for entry in old_document.get("entries") or ():
            if isinstance(entry, Mapping) and entry.get("symbol"):
                kept[str(entry["symbol"])] = dict(entry)
    members = set(member_symbols)
    entries: list[dict[str, Any]] = []
    for row in master.to_dict("records"):
        symbol = str(row["symbol"])
        entries.append(
            kept.get(symbol)
            or _generated_entry(row, as_of=as_of, member=symbol in members)
        )
    entries.sort(key=lambda entry: entry["symbol"])
    return {"selected_as_of": as_of.isoformat(), "entries": entries}


def run(
    root: Path,
    *,
    source_version: str | None = None,
    keep_delisted: bool = False,
    dry_run: bool = False,
) -> int:
    publisher = DatasetPublisher(root)
    version = source_version or publisher.current().version
    reader = DatasetReader(root)
    with reader.open(version) as dataset:
        tables = {name: dataset.read(name) for name in dataset.tables}
        build_config = dataset.manifest.get("build_config")

    master = tables["security_master"]
    coverage = tables["security_master_coverage"]
    membership = tables["universe_membership"]
    print(
        f"source dataset_version={version} master={len(master)} "
        f"coverage={len(coverage)} membership_rows={len(membership)}"
    )

    dropped: list[str] = []
    if not keep_delisted:
        master, coverage, dropped = drop_delisted(master, coverage)
        tables["security_master"] = master
        tables["security_master_coverage"] = coverage
        print(f"dropped delisted master rows={len(dropped)}")
        print("  " + ", ".join(dropped))

    member_symbols = set(membership["symbol"].astype(str))
    universe_path = root / "configs" / "universe.yml"
    old_document = read_yaml(universe_path) if universe_path.exists() else None
    as_of = date.today()
    document = expand_universe_document(
        old_document, master, member_symbols=member_symbols, as_of=as_of
    )
    carried = sum(
        1
        for entry in document["entries"]
        if isinstance(old_document, Mapping)
        and any(
            item.get("symbol") == entry["symbol"]
            for item in (old_document.get("entries") or ())
        )
    )
    entries = document["entries"]
    print(
        f"universe entries={len(entries)} carried_verbatim={carried} "
        f"members={sum(1 for e in entries if e['boundary_tags'] == [MEMBER_TAG])} "
        f"non_members="
        f"{sum(1 for e in entries if e['boundary_tags'] == [ENGINEERING_TAG])}"
    )
    if dry_run:
        print("dry-run: nothing written, nothing published")
        return 0

    universe_path.write_text(
        _HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"wrote {universe_path}")

    if dropped:
        published = publisher.publish(
            tables, QualityReport(), build_config=build_config
        )
        print(f"dataset_version={published.version}")
    else:
        print("master unchanged; no dataset published")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--source-version",
        default=None,
        help="Published dataset version to read (default: CURRENT).",
    )
    parser.add_argument(
        "--keep-delisted",
        action="store_true",
        help=(
            "Only rewrite universe.yml; keep every master row.  Use once the "
            "source merges list_status L/D/P (stage A)."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return run(
        resolve_project_root(args.root),
        source_version=args.source_version,
        keep_delisted=args.keep_delisted,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
