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

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from stock_quant.data_model.universe_membership import (
    MembershipFact,
    MembershipReason,
    _validated_facts,
    membership_content_hash,
    membership_frame,
)
from stock_quant.research.universe import (
    MEMBERSHIP_OBSERVATION_GAP,
    MembershipCoverageGap,
    MembershipCoverageSegment,
    canonical_json_sha256,
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


# --------------------------------------------------------------------------- #
# Attested-boundary snapshot facts (spec 7.1)
# --------------------------------------------------------------------------- #


_SUPPORTED_CADENCES = ("monthly", "unknown")


def build_snapshot_facts(
    snapshots: Sequence[tuple[date, pd.DataFrame, str]],
    *,
    universe_id: str,
    source: str,
    source_url: str,
    collected_at: datetime,
    cadence: str,
    manual_confirmation_sha256: str | None = None,
) -> tuple[
    list[MembershipFact],
    tuple[MembershipCoverageSegment, ...],
    tuple[MembershipCoverageGap, ...],
]:
    """Turn a series of official membership snapshots into raw facts.

    Each snapshot item is ``(snapshot day, constituent frame, snapshot file
    SHA-256)``; the function is pure and offline -- it never reads the disk
    or the network. Under a declared ``monthly`` cadence every month must
    carry one non-empty snapshot; a missing, empty or month-key-anomalous
    month is an observation gap ``[previous month end + 1, next valid
    snapshot day - 1]`` and no boundary is ever inferred across it: the
    earlier segment's open intervals close on the last attested snapshot day
    and the next segment reopens from its own first snapshot.

    Inside one segment adjacent snapshots are differenced: a newly listed
    symbol opens ``raw_effective_from = snapshot day`` (active) and a
    disappearing symbol closes on ``snapshot day - 1`` (removed), both with
    reason ``snapshot_observed_change``. Every fact carries
    ``announcement_date = raw_effective_from`` -- the snapshot day that
    attests that start boundary, never the collection moment. Intervals
    opened by a segment's first snapshot keep reason ``initial_constituent``
    while they remain open. Segments carry the canonical-JSON SHA-256 of
    their snapshot hashes as evidence; a gap's evidence hashes the missing
    month list plus the hashes of the snapshots on both sides.

    ``collected_at`` is written onto every fact as provenance; it never
    enters any content hash. ``cadence="unknown"`` requires
    ``manual_confirmation_sha256`` (spec 7.1) and, with no declared cadence,
    claims no gaps: the confirmed snapshots form one segment.
    """
    if not _CANONICAL_UNIVERSE_ID.fullmatch(universe_id):
        raise ValueError(
            f"universe_id must be a canonical index or custom_<slug>: "
            f"{universe_id!r}"
        )
    if cadence not in _SUPPORTED_CADENCES:
        raise ValueError(
            f"unsupported membership cadence {cadence!r}; expected one of "
            f"{_SUPPORTED_CADENCES}"
        )
    if cadence == "unknown" and manual_confirmation_sha256 is None:
        raise ValueError(
            "membership cadence unknown; manual confirmation required "
            "(spec 7.1)"
        )
    # The official source document is identified by ``source_url``; offline,
    # its per-fact evidence hash is the SHA-256 of the canonical URL text
    # (the auditable pointer), not of the document bytes.
    document_sha256 = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    runs = _snapshot_runs(snapshots, infer_gaps=cadence == "monthly")
    facts: list[MembershipFact] = []
    for position, run in enumerate(runs):
        is_final_run = position == len(runs) - 1
        facts.extend(
            _run_facts(
                run,
                universe_id=universe_id,
                source=source,
                source_url=source_url,
                document_sha256=document_sha256,
                collected_at=collected_at,
                is_final_run=is_final_run,
            )
        )
    segments = tuple(
        MembershipCoverageSegment(
            start=run[0][0],
            end=run[-1][0],
            evidence_sha256=canonical_json_sha256(
                {"snapshot_sha256": [digest for _, _, digest in run]}
            ),
        )
        for run in runs
    )
    gaps = tuple(
        MembershipCoverageGap(
            start=_month_end(earlier[-1][0]) + timedelta(days=1),
            end=later[0][0] - timedelta(days=1),
            reason=MEMBERSHIP_OBSERVATION_GAP,
            evidence_sha256=canonical_json_sha256(
                {
                    "after_snapshot_sha256": later[0][2],
                    "before_snapshot_sha256": earlier[-1][2],
                    "missing_months": _months_between(
                        earlier[-1][0], later[0][0]
                    ),
                }
            ),
        )
        for earlier, later in zip(runs, runs[1:])
    )
    return facts, segments, gaps


def membership_difference_report(
    candidate_facts: Sequence[MembershipFact | Mapping[str, Any]],
    official_facts: Sequence[MembershipFact | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Report per-symbol interval differences between two fact sets.

    Facts are grouped by ``(universe_id, symbol)``; a symbol present only in
    the candidate set reports ``symbol_added``, only in the official set
    ``symbol_removed``, and a same-symbol set with different intervals
    reports ``interval_disagreement``. Entries render as
    ``{"kind", "symbol", "candidate", "official"}``; neither input is
    mutated. Matching facts produce an empty report: overlap evidence only,
    never a silent rewrite of the official table.
    """
    candidate = _intervals_by_symbol(_validated_facts(candidate_facts))
    official = _intervals_by_symbol(_validated_facts(official_facts))
    report: list[dict[str, Any]] = []
    for key in sorted(set(candidate) | set(official)):
        mine = candidate.get(key, set())
        theirs = official.get(key, set())
        if mine == theirs:
            continue
        if not theirs:
            kind = "symbol_added"
        elif not mine:
            kind = "symbol_removed"
        else:
            kind = "interval_disagreement"
        report.append(
            {
                "kind": kind,
                "symbol": key[1],
                "candidate": _render_intervals(mine),
                "official": _render_intervals(theirs),
            }
        )
    return report


def merge_collected_at_first_write(
    new_frame: pd.DataFrame, baseline_frame: pd.DataFrame | None
) -> pd.DataFrame:
    """Keep the first-write ``collected_at`` for already-landed facts.

    Rows are matched on ``(universe_id, symbol, raw_effective_from)``; a
    baseline hit fixes ``collected_at`` at the baseline value (the earliest
    landing wins, replays never bump provenance) and only unmatched rows
    keep the new value. Column order and row order are preserved.
    """
    result = new_frame.copy()
    if "collected_at" not in result.columns:
        result["collected_at"] = pd.NaT
    key = ["universe_id", "symbol", "raw_effective_from"]
    if (
        baseline_frame is None
        or baseline_frame.empty
        or not set(key) <= set(baseline_frame.columns)
    ):
        return result
    baseline = baseline_frame.loc[:, key + ["collected_at"]].copy()
    baseline["raw_effective_from"] = pd.to_datetime(
        baseline["raw_effective_from"], errors="coerce"
    )
    baseline["collected_at"] = pd.to_datetime(
        baseline["collected_at"], errors="coerce"
    )
    baseline = baseline.sort_values(
        "collected_at", kind="stable"
    ).drop_duplicates(key, keep="first")
    left = result.loc[:, key].copy()
    left["raw_effective_from"] = pd.to_datetime(
        left["raw_effective_from"], errors="coerce"
    )
    matched = left.merge(baseline, on=key, how="left", indicator=True)
    hit = (matched["_merge"] == "both").to_numpy()
    result.loc[hit, "collected_at"] = matched["collected_at"].to_numpy()[hit]
    result["collected_at"] = pd.to_datetime(
        result["collected_at"], errors="coerce"
    )
    return result


def _snapshot_runs(
    snapshots: Sequence[tuple[date, pd.DataFrame, str]],
    *,
    infer_gaps: bool,
) -> list[list[tuple[date, pd.DataFrame, str]]]:
    """Group snapshots into maximal runs of consecutive observed months.

    Within one month the last snapshot supersedes earlier ones; a month whose
    snapshot is empty carries no usable observation. With ``infer_gaps``
    (``cadence="monthly"``) a month-index break splits the runs; otherwise
    the confirmed snapshots form one run and no gap is claimed.
    """
    by_month: dict[tuple[int, int], tuple[date, pd.DataFrame, str]] = {}
    for item in snapshots:
        day, frame, digest = item
        by_month[(day.year, day.month)] = (day, frame, digest)
    observed: list[tuple[date, pd.DataFrame, str]] = [
        by_month[key]
        for key in sorted(by_month)
        if _snapshot_symbols(by_month[key][1])
    ]
    if not observed:
        return []
    if not infer_gaps:
        return [observed]
    runs: list[list[tuple[date, pd.DataFrame, str]]] = []
    run: list[tuple[date, pd.DataFrame, str]] = [observed[0]]
    for item in observed[1:]:
        previous = (run[-1][0].year, run[-1][0].month)
        current = (item[0].year, item[0].month)
        if current != (previous[0] + (previous[1] // 12),
                       previous[1] % 12 + 1):
            runs.append(run)
            run = []
        run.append(item)
    runs.append(run)
    return runs


def _run_facts(
    run: list[tuple[date, pd.DataFrame, str]],
    *,
    universe_id: str,
    source: str,
    source_url: str,
    document_sha256: str,
    collected_at: datetime,
    is_final_run: bool,
) -> list[MembershipFact]:
    """Difference one run's snapshots into facts; close leftovers at its end.

    A non-final run is followed by an observation gap: its still-open
    intervals close on the run's last attested snapshot day (removed,
    ``snapshot_observed_change``) -- no precise removal day is claimed. The
    final run's open intervals stay open: those opened by the run's first
    snapshot are ``initial_constituent``, later ones are
    ``snapshot_observed_change``.
    """
    facts: list[MembershipFact] = []
    open_intervals: dict[str, tuple[date, str]] = {}

    def _fact(
        symbol: str,
        opened: tuple[date, str],
        *,
        effective_to: date | None,
        status: str,
        reason: str,
    ) -> MembershipFact:
        from_day, from_digest = opened
        return MembershipFact(
            universe_id=universe_id,
            symbol=symbol,
            raw_effective_from=from_day,
            raw_effective_to=effective_to,
            announcement_date=from_day,
            status=status,
            reason=reason,
            source=source,
            source_url=source_url,
            snapshot_sha256=from_digest,
            source_document_sha256=document_sha256,
            collected_at=collected_at,
        )

    for position, (day, frame, digest) in enumerate(run):
        symbols = set(_snapshot_symbols(frame))
        if position == 0:
            for symbol in sorted(symbols):
                open_intervals[symbol] = (day, digest)
            continue
        for symbol in sorted(set(open_intervals) - symbols):
            opened = open_intervals.pop(symbol)
            facts.append(
                _fact(
                    symbol,
                    opened,
                    effective_to=day - timedelta(days=1),
                    status="removed",
                    reason=MembershipReason.SNAPSHOT_OBSERVED_CHANGE.value,
                )
            )
        for symbol in sorted(symbols - set(open_intervals)):
            open_intervals[symbol] = (day, digest)
    for symbol in sorted(open_intervals):
        opened = open_intervals[symbol]
        if is_final_run:
            stays_initial = opened[0] == run[0][0]
            facts.append(
                _fact(
                    symbol,
                    opened,
                    effective_to=None,
                    status="active",
                    reason=(
                        MembershipReason.INITIAL_CONSTITUENT.value
                        if stays_initial
                        else MembershipReason.SNAPSHOT_OBSERVED_CHANGE.value
                    ),
                )
            )
        else:
            facts.append(
                _fact(
                    symbol,
                    opened,
                    effective_to=run[-1][0],
                    status="removed",
                    reason=MembershipReason.SNAPSHOT_OBSERVED_CHANGE.value,
                )
            )
    return facts


def _snapshot_symbols(frame: pd.DataFrame) -> tuple[str, ...]:
    """Canonical symbol list of one snapshot; blank or null rows are dropped."""
    if frame is None or frame.empty or "symbol" not in frame.columns:
        return ()
    symbols = {
        str(value).strip()
        for value in frame["symbol"]
        if value is not None
        and not (isinstance(value, float) and pd.isna(value))
        and str(value).strip()
    }
    return tuple(sorted(symbols))


def _month_end(day: date) -> date:
    last_day = pd.Timestamp(day).days_in_month
    return date(day.year, day.month, last_day)


def _months_between(earlier: date, later: date) -> list[str]:
    """``YYYY-MM`` labels of every month strictly between the two months."""
    months: list[str] = []
    year, month = earlier.year, earlier.month
    while (year, month) != (later.year, later.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = year + month // 12, month % 12 + 1
    return months[1:]  # drop the earlier month itself


def _intervals_by_symbol(
    facts: Sequence[MembershipFact],
) -> dict[tuple[str, str], set[tuple[date, date | None]]]:
    grouped: dict[tuple[str, str], set[tuple[date, date | None]]] = {}
    for item in facts:
        grouped.setdefault((item.universe_id, item.symbol), set()).add(
            (item.raw_effective_from, item.raw_effective_to)
        )
    return grouped


def _render_intervals(
    intervals: set[tuple[date, date | None]],
) -> list[dict[str, str | None]]:
    return [
        {
            "raw_effective_from": start.isoformat(),
            "raw_effective_to": end.isoformat() if end else None,
        }
        for start, end in sorted(intervals)
    ]
