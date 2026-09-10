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

from datetime import date
from pathlib import Path

import pandas as pd

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
