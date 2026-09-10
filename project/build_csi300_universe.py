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
