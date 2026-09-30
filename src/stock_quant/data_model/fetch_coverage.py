"""Per-table fetch coverage: which history segments this version re-fetched,
carried from the baseline, or skipped (spec D5.3 / A3).

Recorded into ``build_config.table_fetch_coverage``; consumed by the
``table_fetch_coverage_evidence`` acceptance check and the research
preflight (NOT_FETCHED segments reject runs referencing that table).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

KIND_FETCHED = "fetched"
KIND_CARRIED = "carried"
KIND_NOT_FETCHED = "not_fetched"
_KINDS = frozenset({KIND_FETCHED, KIND_CARRIED, KIND_NOT_FETCHED})

#: An explicit --start/--end window deviated from the table's contract fetch
#: window (spec D5.2): the table skipped fetching this round, whole-table.
NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW = "operator_explicit_window"

#: The table's own history starts later than the acceptance anchor (spec
#: §7.5.1): the ONLY legal prefix shape, spanning exactly
#: [acceptance_start, supported_start - 1].
NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR = "history_begins_after_anchor"

#: The source was not enabled this round (spec §7.5.2) -- a tail segment.
NOT_FETCHED_SOURCE_DISABLED = "source_disabled"

#: The source was enabled but unreachable this round (spec §7.5.2) -- a tail
#: segment; the carried baseline keeps every fact before it (spec §7.5.4).
NOT_FETCHED_SOURCE_UNAVAILABLE = "source_unavailable"

_REASONS = frozenset(
    {
        NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW,
        NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR,
        NOT_FETCHED_SOURCE_DISABLED,
        NOT_FETCHED_SOURCE_UNAVAILABLE,
    }
)


@dataclass(frozen=True)
class FetchSegment:
    """One contiguous segment of a table's history in one build.

    A ``not_fetched`` segment may carry ``reason=None`` at construction time;
    a recorded payload still fails validation until the operator-window
    reason is attached (the validator emits ``not_fetched_reason_missing``).
    """

    table: str
    kind: str
    window_start: date
    window_end: date
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown fetch-coverage kind {self.kind!r}")
        if self.window_end < self.window_start:
            raise ValueError("fetch segment window is inverted")
        if self.kind != KIND_NOT_FETCHED:
            if self.reason is not None:
                raise ValueError(
                    f"only not_fetched segments carry a reason, got {self.reason!r}"
                )
        elif self.reason is not None and self.reason not in _REASONS:
            raise ValueError(f"unknown not_fetched reason {self.reason!r}")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "table": self.table,
            "kind": self.kind,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


def to_build_config_payload(
    coverage: Mapping[str, Sequence[FetchSegment]],
) -> dict[str, list[dict[str, object]]]:
    """Serialize per-table segments into the build_config payload shape."""
    return {
        table: [segment.to_dict() for segment in segments]
        for table, segments in sorted(coverage.items())
    }


def not_fetched_input_tables(
    build_config: Mapping[str, object], input_tables: Sequence[str]
) -> list[str]:
    """Input tables whose pinned version carries NOT_FETCHED segments.

    A version with skipped fetches is not a complete-fetch version; research
    runs referencing such tables fail preflight and should pin a full-update
    version instead (spec D5.3, sixth-round ruling).
    """
    coverage = build_config.get("table_fetch_coverage", {})
    if not isinstance(coverage, dict):
        return []
    skipped: list[str] = []
    for table in input_tables:
        segments = coverage.get(table, [])
        if isinstance(segments, list) and any(
            isinstance(segment, Mapping) and segment.get("kind") == KIND_NOT_FETCHED
            for segment in segments
        ):
            skipped.append(table)
    return skipped


def validate_table_fetch_coverage(
    payload: object,
    *,
    anchor_start: date,
    published_end: date,
) -> list[tuple[str, dict]]:
    """Structural violations of a recorded ``table_fetch_coverage`` payload.

    Rules (spec §7.5.1/2/4): at least one table recorded; segments parse and
    sit inside [anchor_start, published_end].  A table with no ``not_fetched``
    segment must cover the whole span contiguously (calendar days).
    ``not_fetched`` layouts, by reason:

    - ``operator_explicit_window``: whole-table skip, nothing else mixes in
      (spec D5.2, unchanged).
    - ``history_begins_after_anchor``: the unique prefix -- exactly one
      segment, starting at ``anchor_start`` and ending the day before the
      first fetched/carried segment.
    - ``source_disabled`` / ``source_unavailable``: tail segments -- after
      every fetched/carried (or history-prefix) segment, running to
      ``published_end``.

    Placement is judged before alignment: a ``not_fetched`` segment in the
    wrong zone (a second history segment, a history segment that is not first,
    or an unavailable segment that does not form a contiguous run reaching
    ``published_end``) is ``not_fetched_mixed_with_fetch``.  A correctly placed
    history prefix whose ``window_start`` is not ``anchor_start`` is
    ``not_fetched_prefix_misaligned``.  The fetched/carried segments must still
    tile ``[anchor_start, published_end]`` contiguously, with the prefix and
    tail zones excluded from that coverage.
    """
    if not isinstance(payload, dict) or not payload:
        return [("table_fetch_coverage_missing", {})]
    violations: list[tuple[str, dict]] = []
    for table, raw_segments in sorted(payload.items()):
        if not isinstance(raw_segments, list):
            violations.append(("table_fetch_coverage_malformed", {"table": table}))
            continue
        try:
            segments = [
                FetchSegment(
                    table=str(segment["table"]),
                    kind=str(segment["kind"]),
                    window_start=date.fromisoformat(str(segment["window_start"])),
                    window_end=date.fromisoformat(str(segment["window_end"])),
                    reason=(
                        None
                        if segment.get("reason") is None
                        else str(segment["reason"])
                    ),
                )
                for segment in raw_segments
            ]
        except (KeyError, ValueError, TypeError) as error:
            violations.append(
                (
                    "table_fetch_coverage_malformed",
                    {"table": table, "error_code": type(error).__name__},
                )
            )
            continue
        ordered = sorted(segments, key=lambda item: item.window_start)
        if any(
            segment.window_start < anchor_start
            or segment.window_end > published_end
            for segment in ordered
        ):
            violations.append(("fetch_coverage_out_of_window", {"table": table}))
        not_fetched = [s for s in ordered if s.kind == KIND_NOT_FETCHED]
        if any(s.reason is None for s in not_fetched):
            violations.append(("not_fetched_reason_missing", {"table": table}))
            continue
        if not not_fetched:
            violations.extend(
                _contiguity_violations(table, ordered, anchor_start, published_end)
            )
            continue
        reasons = {s.reason for s in not_fetched}
        if reasons == {NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW}:
            if any(s.kind != KIND_NOT_FETCHED for s in ordered):
                violations.append(
                    ("not_fetched_mixed_with_fetch", {"table": table})
                )
            continue  # a skipped table covers nothing by design
        if any(
            s.reason == NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW for s in not_fetched
        ):
            violations.append(("not_fetched_mixed_with_fetch", {"table": table}))
            continue
        history = [
            s
            for s in not_fetched
            if s.reason == NOT_FETCHED_HISTORY_BEGINS_AFTER_ANCHOR
        ]
        unavailable = [
            s
            for s in not_fetched
            if s.reason in (NOT_FETCHED_SOURCE_DISABLED, NOT_FETCHED_SOURCE_UNAVAILABLE)
        ]
        # Zone placement first (spec §7.5.1/2): at most one history segment,
        # and it must sit in first position; the unavailable segments must form
        # a contiguous run that reaches published_end.  Anything else is an
        # interleaving, not a history prefix.
        if len(history) > 1 or (history and ordered[0] is not history[0]):
            violations.append(("not_fetched_mixed_with_fetch", {"table": table}))
            continue
        if unavailable:
            zone_start = next(
                index
                for index, segment in enumerate(ordered)
                if segment is unavailable[0]
            )
            zone = ordered[zone_start:]
            if any(segment.kind != KIND_NOT_FETCHED for segment in zone) or (
                zone[-1].window_end != published_end
            ):
                violations.append(
                    ("not_fetched_mixed_with_fetch", {"table": table})
                )
                continue
        # Anchor alignment comes second: a correctly placed prefix must still
        # start exactly at the acceptance anchor (spec §7.5.1).
        if history and history[0].window_start != anchor_start:
            violations.append(("not_fetched_prefix_misaligned", {"table": table}))
            continue
        # The fetched/carried middle must still be contiguous from the anchor;
        # the prefix/tail not_fetched segments advance the cursor but are never
        # themselves coverage.
        cursor = anchor_start
        for segment in ordered:
            if segment.kind == KIND_NOT_FETCHED:
                cursor = max(cursor, segment.window_end + timedelta(days=1))
                continue
            if segment.window_start > cursor:
                violations.append(
                    (
                        "fetch_coverage_gap",
                        {"table": table, "gap_start": cursor.isoformat()},
                    )
                )
            cursor = max(cursor, segment.window_end + timedelta(days=1))
        if cursor <= published_end and not unavailable:
            violations.append(
                (
                    "fetch_coverage_gap",
                    {"table": table, "gap_start": cursor.isoformat()},
                )
            )
    return violations


def _contiguity_violations(
    table: str,
    ordered: Sequence[FetchSegment],
    anchor_start: date,
    published_end: date,
) -> list[tuple[str, dict]]:
    """The legacy whole-span walk for tables without not_fetched segments.

    The cursor starts at ``anchor_start``, not at the first segment's own
    start: a table that simply begins late, without declaring a
    ``history_begins_after_anchor`` prefix, must still fail with
    ``fetch_coverage_gap`` (spec §7.5.1; ``fetch_coverage.py``'s pre-change
    semantics).
    """
    violations: list[tuple[str, dict]] = []
    cursor = anchor_start
    for segment in ordered:
        if segment.window_start > cursor:
            violations.append(
                ("fetch_coverage_gap", {"table": table, "gap_start": cursor.isoformat()})
            )
        cursor = max(cursor, segment.window_end + timedelta(days=1))
    if cursor <= published_end:
        violations.append(
            ("fetch_coverage_gap", {"table": table, "gap_start": cursor.isoformat()})
        )
    return violations
