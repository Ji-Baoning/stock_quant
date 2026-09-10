"""Build the frozen csi300 universe from a sealed index-constitution snapshot.

Offline and evidence-bound: the snapshot's recorded hashes are verified before
a single fact is built, the raw CSVs are never rewritten, and every deviation
from the adjudication is expressed in the snapshot's ``repairs.csv``.

The upstream frame is ``symbol, name, opt-in, opt-out`` in ``SZ000001`` form;
facts carry the project's canonical ``000001.SZ`` form.  ``announcement_date``
is taken from the interval start: the data set records no announcement date,
and using the effective date means a fact becomes visible only on the day it
takes effect.  That is deliberately the late side of the truth (real
announcements precede the effective date by about a fortnight), so it can
underestimate early inclusion but can never leak future information.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Sequence

import pandas as pd
import yaml

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.index_membership_import import (
    prepare_membership_file,
)
from stock_quant.data_quality.models import QualityReport

ROOT = Path(__file__).resolve().parent

#: The index launch cohort; the only intervals allowed to stay
#: ``initial_constituent`` (which the fact contract requires to be active).
BASE_COHORT_FROM = date(2005, 4, 8)

OPT_IN = "opt-in"
OPT_OUT = "opt-out"

EXCHANGES = ("SH", "SZ")

#: The columns ``prepare_membership_file`` reads, in canonical order.
MEMBERSHIP_ROW_COLUMNS = (
    "symbol",
    "raw_effective_from",
    "raw_effective_to",
    "announcement_date",
    "reason",
)


def to_canonical_symbol(raw: str) -> str:
    """Map index-constitution's ``SZ000001`` to the repo's ``000001.SZ``."""
    text = str(raw).strip().upper()
    if len(text) != 8 or text[:2] not in EXCHANGES or not text[2:].isdigit():
        raise ValueError(f"not an index-constitution symbol: {raw!r}")
    return f"{text[2:]}.{text[:2]}"


def membership_rows(history: pd.DataFrame) -> pd.DataFrame:
    """Derive ``prepare_membership_file`` rows from the raw history frame.

    A row whose ``opt-in`` is missing cannot become a fact: it has no start
    date.  Such rows are rejected loudly rather than dropped, because a
    silently dropped row is exactly how a missing inclusion hides -- and a
    missing inclusion is what makes a later removal never take effect.
    """
    missing = history[history[OPT_IN].isna()]
    if not missing.empty:
        symbols = sorted(missing["symbol"].astype(str))
        raise ValueError(
            "history carries rows with no opt-in date, which cannot be mapped "
            "to a membership fact: "
            + ", ".join(symbols)
            + " (adjudicate them in repairs.csv or exclude them explicitly)"
        )
    rows = pd.DataFrame(
        {
            "symbol": history["symbol"].map(to_canonical_symbol),
            "raw_effective_from": pd.to_datetime(history[OPT_IN]),
            "raw_effective_to": pd.to_datetime(history[OPT_OUT]),
            "announcement_date": pd.to_datetime(history[OPT_IN]),
        }
    )
    active = rows["raw_effective_to"].isna()
    base = rows["raw_effective_from"].dt.date == BASE_COHORT_FROM
    rows = rows.reset_index(drop=True)
    rows["reason"] = [
        "initial_constituent" if still_open and launched else "regular_rebalance"
        for still_open, launched in zip(active, base, strict=True)
    ]
    return rows[list(MEMBERSHIP_ROW_COLUMNS)]


#: The adjudicated-correction table.  Every row must name the evidence it
#: rests on; an unadjudicated dispute is deliberately absent from this table
#: and lives in adjudication_report.md instead.
REPAIR_COLUMNS = (
    "symbol",
    "action",
    "field",
    "old_value",
    "new_value",
    "evidence_tier",
    "evidence_source",
    "evidence_detail",
)
REPAIR_ACTIONS = ("set_field", "drop_row", "insert_row")
REPAIR_FIELDS = (OPT_IN, OPT_OUT)
#: A = official CSI announcement body; B = Sina history table (corroborating).
REPAIR_TIERS = ("A", "B")


def read_repairs(path: Path) -> pd.DataFrame:
    """Read the repair table, tolerating a header-only (no-repair) file."""
    return pd.read_csv(Path(path), dtype=str).fillna("")


def _validated_repairs(repairs: pd.DataFrame) -> pd.DataFrame:
    if repairs.empty:
        return repairs
    unknown_actions = sorted(set(repairs["action"]) - set(REPAIR_ACTIONS))
    if unknown_actions:
        raise ValueError(f"unknown repair actions: {', '.join(unknown_actions)}")
    unknown_fields = sorted(set(repairs["field"]) - set(REPAIR_FIELDS))
    if unknown_fields:
        raise ValueError(f"unknown repair fields: {', '.join(unknown_fields)}")
    unknown_tiers = sorted(set(repairs["evidence_tier"]) - set(REPAIR_TIERS))
    if unknown_tiers:
        raise ValueError(f"unknown evidence tiers: {', '.join(unknown_tiers)}")
    for row in repairs.itertuples(index=False):
        if not str(row.evidence_source).strip():
            raise ValueError(f"repair on {row.symbol} names no evidence source")
    return repairs


def _cell(value: object) -> str:
    """Comparable text for one side of a repair comparison.

    Both the frame cell and the repair's ``old_value`` are normalized here, so
    ``2013-12-16``, ``2013/12/16`` and ``2013-12-16 00:00:00`` all compare
    equal.  Normalizing only the frame side would let a mistyped date in
    ``repairs.csv`` match nothing -- indistinguishable from a genuinely stale
    repair, and this table is hand-written.  NaT/NaN/blank read as empty.
    """
    if value is None or value is pd.NaT:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return text
    return str(pd.Timestamp(parsed).date())


def apply_repairs(history: pd.DataFrame, repairs: pd.DataFrame) -> pd.DataFrame:
    """Apply the adjudicated corrections to a copy of the raw history frame.

    Each repair identifies its target by ``symbol`` plus the current value in
    ``field`` (``old_value``); a repair whose ``old_value`` no longer matches
    does nothing, so a stale table cannot mis-edit a changed upstream frame.
    A repair naming a ``symbol`` absent from the frame raises instead -- a row
    that matches no target at all is a broken adjudication, not a stale value.
    Unknown actions, fields and tiers raise rather than being skipped: a typo
    in the adjudication must not silently drop a fix.  ``insert_row`` is the
    exception -- it has no target, and takes its start date from ``old_value``
    and its end date (empty for still-active) from ``new_value``.
    """
    frame = history.copy()
    if repairs.empty:
        return frame
    repairs = _validated_repairs(repairs)
    for row in repairs.itertuples(index=False):
        symbol = str(row.symbol).strip()
        if row.action == "insert_row":
            frame = pd.concat(
                [
                    frame,
                    pd.DataFrame(
                        [
                            {
                                "symbol": symbol,
                                "name": str(row.evidence_detail).strip() or symbol,
                                OPT_IN: pd.to_datetime(row.old_value or pd.NaT),
                                OPT_OUT: pd.to_datetime(row.new_value or pd.NaT),
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
            continue
        on_symbol = frame["symbol"].astype(str) == symbol
        if not on_symbol.any():
            raise ValueError(
                f"repair on {symbol} matches no row in the history; the "
                f"snapshot or the repair is stale"
            )
        matched = on_symbol & (
            frame[row.field].map(_cell) == _cell(row.old_value)
        )
        if row.action == "set_field":
            if matched.any():
                frame.loc[matched, row.field] = pd.to_datetime(
                    row.new_value or pd.NaT
                )
            continue
        # drop_row: a stale old_value is a no-op, not an error.
        if matched.any():
            frame = frame.loc[~matched].reset_index(drop=True)
    return frame


MANIFEST_NAME = "manifest.json"
REPAIRS_NAME = "repairs.csv"
EVIDENCE_NAME = "evidence_summary.json"

SOURCE = "index_constitution"
SOURCE_URL = "https://github.com/unliftedq/index-constitution"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seal_evidence(snapshot_dir: Path) -> dict:
    """Pin ``manifest.json`` and ``repairs.csv`` into ``evidence_summary.json``.

    This file's own SHA-256 becomes each fact's ``source_document_sha256`` and
    the definition's ``evidence_summary_sha256``; the file itself pins
    ``manifest_sha256`` (which covers the upstream CSVs) and ``repairs_sha256``
    (which covers the hand-authored corrections).  Binding the repair table here
    is what makes the corrections tamper-evident: without it a repair could be
    edited while every other hash still verified.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / MANIFEST_NAME
    repairs_path = snapshot_dir / REPAIRS_NAME
    for path in (manifest_path, repairs_path):
        if not path.is_file():
            raise FileNotFoundError(f"snapshot is missing {path.name}: {path}")
    summary = {
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "manifest_sha256": _sha256_file(manifest_path),
        "repairs_sha256": _sha256_file(repairs_path),
    }
    (snapshot_dir / EVIDENCE_NAME).write_text(
        json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8"
    )
    return summary


def verify_snapshot(snapshot_dir: Path) -> tuple[dict, dict]:
    """Verify every recorded hash and return ``(manifest, evidence_summary)``.

    Nothing is built until the whole evidence set matches: a snapshot that
    cannot be verified must not produce facts.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / MANIFEST_NAME
    repairs_path = snapshot_dir / REPAIRS_NAME
    summary_path = snapshot_dir / EVIDENCE_NAME
    for path in (manifest_path, repairs_path, summary_path):
        if not path.is_file():
            raise FileNotFoundError(f"snapshot is missing {path.name}: {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if _sha256_file(manifest_path) != summary["manifest_sha256"]:
        raise ValueError(
            f"{MANIFEST_NAME} does not match the recorded manifest_sha256"
        )
    if _sha256_file(repairs_path) != summary["repairs_sha256"]:
        raise ValueError(
            f"{REPAIRS_NAME} does not match the recorded repairs_sha256; the "
            "repair table was edited after the snapshot was sealed"
        )
    for name, recorded in manifest["files"].items():
        actual = _sha256_file(snapshot_dir / name)
        if actual != recorded:
            raise ValueError(
                f"{name} hashes to {actual} but the manifest records {recorded}"
            )
    return manifest, summary


CANONICAL_ID = "csi300"
CUSTOM_ID = "custom_csi300_ic"
EXPECTED_MEMBERS = 300


def cardinality_deviations(
    rows: pd.DataFrame, sessions: Sequence[date], *, expected: int = 300
) -> list[str]:
    """``["<day>:<count>", ...]`` for every session whose member count is off.

    Checked globally rather than at the repaired interval: a locally plausible
    fix can move a +1 onto another date, and only a full re-scan catches that.

    This answers a build-time question -- "is this history clean enough to
    claim the canonical ``csi300`` id?" -- so it deliberately has no notion of
    a sanctioned exception and treats every off-count day as a deviation.
    Erring strict is the safe direction here: a doubtful history is downgraded
    to ``custom_csi300_ic`` rather than allowed to masquerade as canonical.

    This grid intentionally differs from the published-facts check
    ``_cardinality_issues``, which starts counting at the universe's first
    claimed start date.  This scan instead counts every session in the pinned
    calendar, including sessions before the history's first inclusion.  That is
    the strict choice: if the calendar ever reached before the history, every
    such session would be flagged ``<day>:0`` and the history would always
    downgrade to the custom id -- the intended safe outcome, not a bug.

    Officially sanctioned temporary exceptions are a separate, later concern
    owned by the dataset quality layer, which already models them via
    ``MembershipSizeException`` in
    ``stock_quant.data_quality.raw_checks._cardinality_issues``.  That check
    runs on published facts and cannot be reused here without a publish
    round-trip, which is why this scan is a local duplicate rather than a
    call into it.
    """
    intervals = [
        (
            pd.Timestamp(row.raw_effective_from).date(),
            None
            if pd.isna(row.raw_effective_to)
            else pd.Timestamp(row.raw_effective_to).date(),
        )
        for row in rows.itertuples(index=False)
    ]
    deviations: list[str] = []
    for day in sessions:
        count = sum(
            1
            for start, end in intervals
            if start <= day and (end is None or day <= end)
        )
        if count != expected:
            deviations.append(f"{day.isoformat()}:{count}")
    return deviations


def resolve_universe_id(deviations: list[str], *, requested: str) -> str:
    """Keep ``csi300`` only when every session carries exactly 300 members.

    A deviating history may not claim the canonical id, because that id is
    what downstream cardinality acceptance gates on.  The custom pool skips
    only that check; the evidence chain is identical.
    """
    if not deviations or requested != CANONICAL_ID:
        return requested
    return CUSTOM_ID


def membership_snapshot_path(universe_id: str) -> Path:
    """Staging path for the derived membership CSV handed to the importer.

    Deliberately OUTSIDE the sealed snapshot directory: a snapshot holds only
    the enumerated evidence files, and this derived artifact is legitimately
    rewritten on every build.
    """
    return ROOT / "data" / "raw" / "csi" / f"{universe_id}_membership_snapshot.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the frozen csi300 universe from a sealed "
            "index-constitution snapshot (offline; no network access)."
        )
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        required=True,
        help=(
            "the dated snapshot directory to build from; always explicit so "
            "the same experiment cannot silently pick up a newer snapshot"
        ),
    )
    parser.add_argument(
        "--universe-id",
        default=CANONICAL_ID,
        help=(
            "requested universe id; falls back to custom_csi300_ic when the "
            "history cannot guarantee exactly 300 members per session"
        ),
    )
    parser.add_argument(
        "--seal-evidence",
        action="store_true",
        help="write evidence_summary.json and exit (run after adjudication)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="membership parquet path; defaults to data/membership/<id>.parquet",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    snapshot_dir = Path(args.snapshot_dir)

    if args.seal_evidence:
        summary = seal_evidence(snapshot_dir)
        print(f"sealed {snapshot_dir / EVIDENCE_NAME}")
        print(f"  manifest_sha256={summary['manifest_sha256']}")
        print(f"  repairs_sha256={summary['repairs_sha256']}")
        return

    manifest, summary = verify_snapshot(snapshot_dir)
    print(
        f"verified snapshot {snapshot_dir}\n"
        f"  manifest_sha256={summary['manifest_sha256']}\n"
        f"  repairs_sha256={summary['repairs_sha256']}\n"
        f"  package_version={manifest['package_version']}"
    )
    history = pd.read_csv(snapshot_dir / "csi300_history.csv")
    repairs = read_repairs(snapshot_dir / REPAIRS_NAME)
    repaired = apply_repairs(history, repairs)
    print(
        f"history rows={len(history)} repairs={len(repairs)} "
        f"effective rows={len(repaired)}"
    )
    rows = membership_rows(repaired)

    publisher = DatasetPublisher(ROOT)
    with DatasetReader(ROOT).open(publisher.current().version) as dataset:
        calendar = dataset.read("trading_calendar")
        tables = {name: dataset.read(name) for name in dataset.tables}
    sessions = [
        day.date()
        for day in pd.to_datetime(
            calendar.loc[calendar["is_trading_day"], "calendar_date"]
        )
    ]
    deviations = cardinality_deviations(rows, sessions)
    universe_id = resolve_universe_id(deviations, requested=args.universe_id)
    if deviations:
        print(
            f"cardinality deviates from {EXPECTED_MEMBERS} on "
            f"{len(deviations)}/{len(sessions)} sessions "
            f"(first: {deviations[:8]})"
        )
        if universe_id != args.universe_id:
            print(
                f"falling back to {universe_id}: a custom pool skips only the "
                "cardinality check; the evidence chain is identical"
            )
    else:
        print(f"cardinality exactly {EXPECTED_MEMBERS} on every session")

    snapshot_csv = membership_snapshot_path(universe_id)
    snapshot_csv.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(snapshot_csv, index=False)
    output = args.output or (
        ROOT / "data" / "membership" / f"{universe_id}.parquet"
    )
    result = prepare_membership_file(
        snapshot_csv,
        universe_id=universe_id,
        source=SOURCE,
        source_url=SOURCE_URL,
        snapshot_sha256=manifest["files"]["csi300_history.csv"],
        source_document_sha256=_sha256_file(snapshot_dir / EVIDENCE_NAME),
        effective_date=pd.Timestamp(
            rows["raw_effective_from"].min()
        ).date(),
        announcement_date=pd.Timestamp(
            rows["raw_effective_from"].min()
        ).date(),
        reason="regular_rebalance",
        output=output,
    )
    print(
        f"membership rows={len(result.frame)} "
        f"membership_table_sha256={result.content_hash}"
    )

    tables["universe_membership"] = result.frame
    published = publisher.publish(tables, QualityReport())
    print(f"dataset_version={published.version}")

    with DatasetReader(ROOT).open(published.version) as dataset:
        daily = dataset.read("daily_bar")
    repairs_sha = _sha256_file(snapshot_dir / REPAIRS_NAME)
    definition = {
        "schema_version": 1,
        "universe_id": universe_id,
        "rules_version": (
            f"{SOURCE}-{manifest['package_version']}"
            f"+repairs-{repairs_sha[:8]}"
        ),
        "membership_table_sha256": result.content_hash,
        "evidence_summary_sha256": _sha256_file(snapshot_dir / EVIDENCE_NAME),
        "coverage_start": pd.to_datetime(daily["trade_date"]).min().date().isoformat(),
        "coverage_end": pd.to_datetime(daily["trade_date"]).max().date().isoformat(),
    }
    definition_path = ROOT / "configs" / "universes" / f"{universe_id}.yml"
    definition_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Frozen universe definition generated by build_csi300_universe.py.\n"
        f"# Built from snapshot {snapshot_dir}\n"
        "# evidence_summary_sha256 = SHA-256 of that snapshot's\n"
        "# evidence_summary.json, which pins manifest.json (the upstream CSV\n"
        "# hashes) and repairs.csv (the adjudicated corrections).\n"
    )
    definition_path.write_text(
        header + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"definition={definition_path}")


if __name__ == "__main__":
    main()
