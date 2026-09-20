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

#: The only accepted NOT_FETCHED reason (spec D5.2): an explicit --start/--end
#: window deviated from the table's contract fetch window, so the table
#: skipped fetching this round and the baseline was carried instead.
NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW = "operator_explicit_window"
_REASONS = frozenset({NOT_FETCHED_OPERATOR_EXPLICIT_WINDOW})


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


def validate_table_fetch_coverage(
    payload: object,
    *,
    anchor_start: date,
    published_end: date,
) -> list[tuple[str, dict]]:
    """Structural violations of a recorded ``table_fetch_coverage`` payload.

    Rules: at least one table recorded; segments parse and sit inside
    [anchor_start, published_end]; ``fetched``/``carried`` segments cover the
    whole span contiguously; ``not_fetched`` only with the operator-window
    reason (an explicit window made every segment skip -- the empty-run case,
    a skipped table covers nothing by design).
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
        if not_fetched:
            if any(s.kind != KIND_NOT_FETCHED for s in ordered):
                violations.append(
                    ("not_fetched_mixed_with_fetch", {"table": table})
                )
            if any(s.reason not in _REASONS for s in not_fetched):
                violations.append(
                    ("not_fetched_reason_missing", {"table": table})
                )
            continue  # a skipped table covers nothing by design
        cursor = anchor_start
        for segment in ordered:
            if segment.window_start > cursor:
                violations.append(
                    (
                        "fetch_coverage_gap",
                        {"table": table, "gap_start": cursor.isoformat()},
                    )
                )
            cursor = max(cursor, segment.window_end + timedelta(days=1))
        if cursor <= published_end:
            violations.append(
                (
                    "fetch_coverage_gap",
                    {"table": table, "gap_start": cursor.isoformat()},
                )
            )
    return violations
