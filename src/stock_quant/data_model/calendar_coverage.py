"""Which natural-day range of the published calendar came from which source.

A published dataset must explain the provenance of every calendar day it
ships.  ``build_config.calendar_coverage`` is that ordered, non-overlapping,
gap-free span list: an update splits the existing spans at the materialisation
window, replaces the window with one supplier span, merges adjacent
same-source non-seed spans (union of snapshot hashes -- old evidence is never
dropped for a merge) and re-validates order/overlap/gaps before publishing.
``data validate`` and the acceptance chain re-check the same invariants
against the *published* calendar range, using only the criterion bound into
that version (a later ``configs/universes`` change never re-judges an old
version).

``DATASET_BUILD_CONTRACT_VERSION`` stays ``1`` on purpose: bumping it would
fail ``source_role_health`` for every already-published dataset.  The price is
that two ``build_config`` shapes exist under one version number, told apart by
the presence of the ``calendar_coverage`` key.  A manifest without it is read
compatibly (and reported as ``calendar_coverage_missing``); its disposition is
a republish, not an exemption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

SOURCE_BOOTSTRAP_SEED = "bootstrap_seed"
SOURCE_TUSHARE_RELAY = "tushare_relay"
SOURCE_TUSHARE_OFFICIAL_BREAK_GLASS = "tushare_official_break_glass"

#: Sources whose spans must carry per-exchange raw snapshot hashes.
SUPPLIER_SOURCES = (SOURCE_TUSHARE_RELAY, SOURCE_TUSHARE_OFFICIAL_BREAK_GLASS)
COVERAGE_SOURCES = (SOURCE_BOOTSTRAP_SEED, *SUPPLIER_SOURCES)

#: Exchanges every supplier calendar span must account for.
EXCHANGES = ("SSE", "SZSE")

CODE_CALENDAR_COVERAGE_MISSING = "calendar_coverage_missing"
CODE_CALENDAR_COVERAGE_INVALID = "calendar_coverage_invalid"
CODE_CALENDAR_COVERAGE_UNSORTED = "calendar_coverage_unsorted"
CODE_CALENDAR_COVERAGE_OVERLAP = "calendar_coverage_overlap"
CODE_CALENDAR_COVERAGE_GAP = "calendar_coverage_gap"
CODE_CALENDAR_UNCOVERED = "calendar_uncovered"
CODE_FULL_HISTORY_START_MISSING = "full_history_acceptance_start_missing"
CODE_BOOTSTRAP_SEED_IN_FULL_HISTORY = "bootstrap_seed_in_full_history"
CODE_REMOVED_FALLBACK_FIELD_PRESENT = "removed_fallback_field_present"

#: Violations that are reported but never block a publication: an empty
#: universe scan yields no acceptance start, and an engineering project must
#: still be publishable.  The acceptance chain fails on them regardless.
COVERAGE_WARNING_CODES = frozenset({CODE_FULL_HISTORY_START_MISSING})

#: The ``build_config`` key the whole evidence contract hangs on.
COVERAGE_KEY = "calendar_coverage"
ACCEPTANCE_START_KEY = "full_history_acceptance_start"
DEFINITION_HASHES_KEY = "universe_coverage_definition_hashes"
SKIPPED_DEFINITIONS_KEY = "universe_coverage_skipped"
REMOVED_FALLBACK_KEY = "resolved_end_is_fallback"

Violation = tuple[str, dict[str, object]]
Violations = tuple[Violation, ...]


class CalendarCoverageError(ValueError):
    """One or more coverage violations; ``.violations`` carries the codes."""

    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations: Violations = tuple(violations)
        code = (
            self.violations[0][0]
            if self.violations
            else CODE_CALENDAR_COVERAGE_INVALID
        )
        super().__init__(code)


@dataclass(frozen=True)
class CalendarCoverageSpan:
    """One contiguous natural-day range and the calendar evidence behind it."""

    start_date: date
    end_date: date
    source: str
    snapshot_sha256s: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError(
                f"calendar coverage span ends before it starts: "
                f"{self.start_date} > {self.end_date}"
            )
        if self.source not in COVERAGE_SOURCES:
            raise ValueError(f"unknown calendar coverage source: {self.source!r}")
        expected = set(EXCHANGES) if self.source in SUPPLIER_SOURCES else set()
        if self.source == SOURCE_BOOTSTRAP_SEED:
            if self.snapshot_sha256s:
                raise ValueError("a bootstrap seed span carries no snapshot hashes")
        elif set(self.snapshot_sha256s) != expected:
            raise ValueError(
                "a supplier span must carry snapshot hashes for exactly "
                f"{', '.join(EXCHANGES)}"
            )
        for exchange, hashes in self.snapshot_sha256s.items():
            if not hashes:
                raise ValueError(
                    f"span {self.start_date}..{self.end_date} has no "
                    f"{exchange} snapshot hash"
                )


def seed_span(start: date, end: date) -> CalendarCoverageSpan:
    """The offline bootstrap's weekday approximation over ``[start, end]``."""
    return CalendarCoverageSpan(start, end, SOURCE_BOOTSTRAP_SEED)


def supplier_span(
    start: date,
    end: date,
    *,
    source: str,
    snapshots_by_exchange: Mapping[str, Sequence[str]],
) -> CalendarCoverageSpan:
    """One supplier span with per-exchange hashes sorted and deduplicated."""
    return CalendarCoverageSpan(
        start,
        end,
        source,
        {
            exchange: tuple(sorted(set(snapshots_by_exchange[exchange])))
            for exchange in EXCHANGES
            if exchange in snapshots_by_exchange
        },
    )


def span_payload(span: CalendarCoverageSpan) -> dict[str, object]:
    """The sanitized JSON payload of one span (seed spans omit the hashes)."""
    payload: dict[str, object] = {
        "start_date": span.start_date.isoformat(),
        "end_date": span.end_date.isoformat(),
        "source": span.source,
    }
    if span.source in SUPPLIER_SOURCES:
        payload["snapshot_sha256s"] = {
            exchange: list(span.snapshot_sha256s[exchange]) for exchange in EXCHANGES
        }
    return payload


def coverage_payload(spans: Sequence[CalendarCoverageSpan]) -> list[dict[str, object]]:
    """The ordered ``build_config.calendar_coverage`` list."""
    return [span_payload(span) for span in spans]


def coverage_from_payload(payload: object) -> tuple[CalendarCoverageSpan, ...]:
    """Parse a ``calendar_coverage`` payload, or raise ``CalendarCoverageError``."""
    if payload is None:
        return ()
    if not isinstance(payload, (list, tuple)):
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "not_a_list"}),)
        )
    parsed: list[CalendarCoverageSpan] = []
    for row in payload:
        parsed.append(_span_from_row(row))
    return tuple(parsed)


def _span_from_row(row: object) -> CalendarCoverageSpan:
    if not isinstance(row, Mapping):
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "row_not_a_mapping"}),)
        )
    source = row.get("source")
    if source not in COVERAGE_SOURCES:
        raise CalendarCoverageError(
            (
                (
                    CODE_CALENDAR_COVERAGE_INVALID,
                    {"reason": "unknown_source", "source": str(source)},
                ),
            )
        )
    try:
        start = date.fromisoformat(str(row["start_date"]))
        end = date.fromisoformat(str(row["end_date"]))
    except (KeyError, ValueError):
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "bad_date"}),)
        ) from None
    if end < start:
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "inverted_range"}),)
        )
    if source not in SUPPLIER_SOURCES:
        return CalendarCoverageSpan(start, end, source)
    raw_hashes = row.get("snapshot_sha256s")
    if not isinstance(raw_hashes, Mapping):
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "missing_snapshot_hashes"}),)
        )
    try:
        return supplier_span(
            start,
            end,
            source=source,
            snapshots_by_exchange={
                exchange: list(raw_hashes.get(exchange, ()))
                for exchange in EXCHANGES
            },
        )
    except ValueError:
        raise CalendarCoverageError(
            ((CODE_CALENDAR_COVERAGE_INVALID, {"reason": "blank_snapshot_hashes"}),)
        ) from None


def span_violations(
    spans: Sequence[CalendarCoverageSpan],
) -> Violations:
    """Sort order, no overlap and no hole between consecutive spans."""
    for previous, current in zip(spans, spans[1:]):
        if previous.start_date > current.start_date:
            return ((CODE_CALENDAR_COVERAGE_UNSORTED, {"reason": "date_order"}),)
    for previous, current in zip(spans, spans[1:]):
        if current.start_date <= previous.end_date:
            return (
                (
                    CODE_CALENDAR_COVERAGE_OVERLAP,
                    {
                        "start_date": current.start_date.isoformat(),
                        "end_date": previous.end_date.isoformat(),
                    },
                ),
            )
    for previous, current in zip(spans, spans[1:]):
        if current.start_date != previous.end_date + timedelta(days=1):
            return (
                (
                    CODE_CALENDAR_COVERAGE_GAP,
                    {
                        "after": previous.end_date.isoformat(),
                        "before": current.start_date.isoformat(),
                    },
                ),
            )
    return ()


def coverage_violations(
    spans: Sequence[CalendarCoverageSpan], *, open_days: Sequence[date]
) -> Violations:
    """The published open-day range must sit inside the span range."""
    if not open_days or not spans:
        return ()
    first, last = min(open_days), max(open_days)
    if spans[0].start_date <= first and last <= spans[-1].end_date:
        return ()
    return (
        (
            CODE_CALENDAR_UNCOVERED,
            {
                "first_open_day": first.isoformat(),
                "last_open_day": last.isoformat(),
                "coverage_start": spans[0].start_date.isoformat(),
                "coverage_end": spans[-1].end_date.isoformat(),
            },
        ),
    )


def merge_adjacent_spans(
    spans: Sequence[CalendarCoverageSpan],
) -> tuple[CalendarCoverageSpan, ...]:
    """The spans with every adjacent same-source non-seed pair merged."""
    merged: list[CalendarCoverageSpan] = []
    for span in spans:
        if merged and _mergeable(merged[-1], span):
            merged[-1] = _merged(merged[-1], span)
            continue
        merged.append(span)
    return tuple(merged)


def _mergeable(previous: CalendarCoverageSpan, current: CalendarCoverageSpan) -> bool:
    if previous.source != current.source:
        return False
    if previous.source == SOURCE_BOOTSTRAP_SEED:
        return False
    return current.start_date == previous.end_date + timedelta(days=1)


def _merged(
    previous: CalendarCoverageSpan, current: CalendarCoverageSpan
) -> CalendarCoverageSpan:
    return CalendarCoverageSpan(
        previous.start_date,
        current.end_date,
        previous.source,
        {
            exchange: tuple(
                sorted(
                    set(previous.snapshot_sha256s[exchange])
                    | set(current.snapshot_sha256s[exchange])
                )
            )
            for exchange in EXCHANGES
        },
    )


def _replaced(
    spans: Sequence[CalendarCoverageSpan],
    *,
    start: date,
    end: date,
    replacement: CalendarCoverageSpan,
) -> tuple[CalendarCoverageSpan, ...]:
    """Drop ``[start, end]`` from every span, then insert the replacement."""
    kept: list[CalendarCoverageSpan] = []
    for span in spans:
        if span.end_date < start or span.start_date > end:
            kept.append(span)
            continue
        if span.start_date < start:
            kept.append(
                _slice(span, span.start_date, start - timedelta(days=1))
            )
        if span.end_date > end:
            kept.append(_slice(span, end + timedelta(days=1), span.end_date))
    kept.append(replacement)
    return tuple(sorted(kept, key=lambda span: span.start_date))


def _slice(span: CalendarCoverageSpan, start: date, end: date) -> CalendarCoverageSpan:
    return CalendarCoverageSpan(
        start, end, span.source, dict(span.snapshot_sha256s)
    )


def merge_window(
    spans: Sequence[CalendarCoverageSpan],
    *,
    start: date,
    end: date,
    replacement: CalendarCoverageSpan,
    open_days: Sequence[date],
) -> tuple[CalendarCoverageSpan, ...]:
    """Replace ``[start, end]`` with ``replacement`` and re-validate.

    Raises :class:`CalendarCoverageError` when the result is unsorted,
    overlapping, has a hole, or no longer covers the published open-day range
    -- so a window that does not touch existing coverage is blocked *before*
    publishing, never after.
    """
    merged = merge_adjacent_spans(
        _replaced(spans, start=start, end=end, replacement=replacement)
    )
    violations = span_violations(merged) + coverage_violations(
        merged, open_days=open_days
    )
    if violations:
        raise CalendarCoverageError(violations)
    return merged


def full_history_violations(
    spans: Sequence[CalendarCoverageSpan],
    *,
    acceptance_start: date,
    calendar_last_open: date,
) -> Violations:
    """Seeds may not intersect ``[acceptance_start, last open day]``."""
    violations: list[Violation] = []
    for span in spans:
        if span.source != SOURCE_BOOTSTRAP_SEED:
            continue
        if span.end_date < acceptance_start or span.start_date > calendar_last_open:
            continue
        violations.append(
            (
                CODE_BOOTSTRAP_SEED_IN_FULL_HISTORY,
                {
                    "start_date": span.start_date.isoformat(),
                    "end_date": span.end_date.isoformat(),
                    "full_history_acceptance_start": acceptance_start.isoformat(),
                    "last_calendar_date": calendar_last_open.isoformat(),
                },
            )
        )
    return tuple(violations)


def validate_build_calendar_evidence(
    build: Mapping[str, Any] | None, *, open_days: Sequence[date]
) -> Violations:
    """Every calendar-evidence violation of one manifest's ``build_config``."""
    if not isinstance(build, Mapping) or COVERAGE_KEY not in build:
        return (
            (CODE_CALENDAR_COVERAGE_MISSING, {"build_config": "calendar_coverage"}),
        )
    violations: list[Violation] = []
    if REMOVED_FALLBACK_KEY in build:
        violations.append(
            (CODE_REMOVED_FALLBACK_FIELD_PRESENT, {"key": REMOVED_FALLBACK_KEY})
        )
    try:
        spans = coverage_from_payload(build[COVERAGE_KEY])
    except CalendarCoverageError as error:
        return tuple(violations) + error.violations
    violations.extend(span_violations(spans))
    violations.extend(coverage_violations(spans, open_days=open_days))
    start_raw = build.get(ACCEPTANCE_START_KEY)
    if not isinstance(start_raw, str):
        violations.append(
            (
                CODE_FULL_HISTORY_START_MISSING,
                {"reason": "no_enabled_universe_definition"},
            )
        )
        return tuple(violations)
    if open_days:
        violations.extend(
            full_history_violations(
                spans,
                acceptance_start=date.fromisoformat(start_raw),
                calendar_last_open=max(open_days),
            )
        )
    return tuple(violations)


def merged_snapshot_hashes(
    spans: Sequence[CalendarCoverageSpan],
) -> dict[str, tuple[str, ...]]:
    """Every snapshot hash any span carries, per exchange (audit helper)."""
    collected: dict[str, set[str]] = {exchange: set() for exchange in EXCHANGES}
    for span in spans:
        for exchange in EXCHANGES:
            collected[exchange].update(span.snapshot_sha256s.get(exchange, ()))
    return {
        exchange: tuple(sorted(collected[exchange])) for exchange in EXCHANGES
    }
