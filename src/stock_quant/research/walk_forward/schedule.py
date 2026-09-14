"""Immutable fold schedule materialization and the separate outcome ledger.

``materialize_schedule`` derives, once and deterministically, the complete
January-December OOS folds fully contained in the requested evaluation range:
each fold's natural calendar boundaries, its first/last *confirmed open*
sessions from the pinned calendar, its warmup window (three calendar years at
minimum, extended back in whole January-1-anchored years when the exchange's
per-year session count cannot reach the confirmed-session floor within the
minimum span, bounded so a sparse calendar records a loud deficiency) and
session counts, and a deterministic ``fold_id`` (the canonical SHA-256 of the
fold's identifying inputs).  A complete fold year with no open session at all
is retained with null trading boundaries so the runner must later produce
market-wide closure evidence; absence of that evidence becomes FAILED.
Requested dates that cannot form a complete 12-month fold are recorded as
``not_evaluated_boundary`` windows -- recorded, never silently dropped, and
never confused with executed-fold skips.

The schedule carries only pre-execution facts: statuses, reasons and any
other runtime state belong exclusively to :class:`FoldOutcomeLedger`, whose
``schedule_sha256`` binds every outcome to the exact immutable schedule it
executed against.  :func:`write_canonical_json` is the single canonical
persistence primitive (sorted keys, compact UTF-8, ISO dates, no NaN,
atomic replace) and refuses to overwrite an existing file with different
bytes, so a schedule hash written before fold execution cannot change after.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError

from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.research.walk_forward.policy import WalkForwardPolicy, canonical_sha256

#: Hard bound on how many extra whole years the warmup window may extend back
#: beyond its three-calendar-year minimum while reaching for the confirmed
#: session floor.  A calendar that still cannot satisfy the floor records the
#: deficient count; the runner preflight then fails the fold loudly.
MAX_WARMUP_EXTENSION_YEARS = 10


#: The one legal boundary disposition: dates that cannot form a complete
#: 12-month OOS fold.  They are recorded in the schedule, excluded from OOS
#: aggregation, and are categorically not ``skipped`` folds.
class ScheduleDisposition(str, Enum):
    """Vocabulary of planned-but-not-evaluated schedule windows."""

    NOT_EVALUATED_BOUNDARY = "not_evaluated_boundary"


class FoldOutcomeStatus(str, Enum):
    """Closed vocabulary of per-fold run outcomes (never schedule content)."""

    EXECUTED = "executed"
    FAILED_PREFLIGHT = "failed_preflight"
    SKIPPED_NOT_TRADEABLE = "skipped_not_tradeable"


class _StrictFrozen(BaseModel):
    """Shared strict (extra-forbidden) frozen model configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FoldWindow(_StrictFrozen):
    """One complete 12-month OOS fold's pre-execution facts."""

    fold_id: str
    calendar_start: date
    calendar_end: date
    #: The first/last confirmed open sessions of the fold; ``None`` only when
    #: the pinned calendar confirms no open session in the whole fold year.
    first_trading_day: date | None
    last_trading_day: date | None
    warmup_calendar_start: date
    warmup_calendar_end: date
    warmup_session_count: int
    oos_session_count: int
    #: Planned point-in-time membership snapshots (ISO date -> snapshot hash)
    #: inside the fold's calendar range, as known when the schedule froze.
    membership_snapshot_sha256s: dict[str, str] = {}

    @model_validator(mode="after")
    def _check(self) -> "FoldWindow":
        if self.calendar_start > self.calendar_end:
            raise ValueError("fold calendar_start must not follow calendar_end")
        if self.warmup_calendar_start >= self.calendar_start:
            raise ValueError("warmup window must precede the fold calendar_start")
        if self.first_trading_day is None and self.last_trading_day is not None:
            raise ValueError("a fold with no open session has null boundaries")
        if self.first_trading_day is not None and self.last_trading_day is None:
            raise ValueError("a fold with an open session has both boundaries")
        if (
            self.first_trading_day is not None
            and self.first_trading_day > self.last_trading_day
        ):
            raise ValueError("first_trading_day must not follow last_trading_day")
        if self.warmup_session_count < 0 or self.oos_session_count < 0:
            raise ValueError("session counts must be non-negative")
        if list(self.membership_snapshot_sha256s) != sorted(
            self.membership_snapshot_sha256s
        ):
            raise ValueError("membership snapshots must be keyed in sorted order")
        return self


class BoundaryWindow(_StrictFrozen):
    """Requested dates that cannot form a complete OOS fold: recorded only."""

    disposition: ScheduleDisposition
    calendar_start: date
    calendar_end: date
    reason: str

    @model_validator(mode="after")
    def _check(self) -> "BoundaryWindow":
        if self.calendar_start > self.calendar_end:
            raise ValueError("boundary calendar_start must not follow calendar_end")
        if not self.reason.strip():
            raise ValueError("boundary reason must be non-empty")
        return self


class FoldSchedule(_StrictFrozen):
    """The immutable pre-execution schedule: folds plus boundary exclusions."""

    mode: Literal["fixed_calendar_oos_v1"] = "fixed_calendar_oos_v1"
    requested_start: date
    requested_end: date
    folds: tuple[FoldWindow, ...]
    boundaries: tuple[BoundaryWindow, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "FoldSchedule":
        if self.requested_start > self.requested_end:
            raise ValueError("requested_start must not follow requested_end")
        windows = sorted(self.folds, key=lambda fold: fold.calendar_start)
        if list(self.folds) != windows:
            raise ValueError("folds must be ordered by calendar_start")
        fold_ids = [fold.fold_id for fold in self.folds]
        if len(set(fold_ids)) != len(fold_ids):
            raise ValueError("duplicate fold ids in the schedule")
        for earlier, later in zip(windows, windows[1:]):
            if later.calendar_start <= earlier.calendar_end:
                raise ValueError(
                    f"overlapping fold windows {earlier.calendar_start}.."
                    f"{earlier.calendar_end} and {later.calendar_start}.."
                    f"{later.calendar_end}"
                )
        return self


class FoldOutcome(_StrictFrozen):
    """One planned fold's run result: status plus a sanitized reason."""

    fold_id: str
    status: FoldOutcomeStatus
    reason_code: str | None = None
    detail: str | None = None
    #: Market-level closure evidence references for a legal
    #: ``skipped_not_tradeable`` outcome (never per-stock results).
    evidence: dict[str, str] = {}

    @model_validator(mode="after")
    def _check(self) -> "FoldOutcome":
        if list(self.evidence) != sorted(self.evidence):
            raise ValueError("evidence keys must be in sorted order")
        return self


class FoldOutcomeLedger(_StrictFrozen):
    """The immutable run-result ledger bound to one schedule hash."""

    schedule_sha256: str
    outcomes: tuple[FoldOutcome, ...]

    @classmethod
    def for_schedule(
        cls,
        schedule: FoldSchedule,
        outcomes: Sequence[FoldOutcome],
    ) -> "FoldOutcomeLedger":
        """Bind outcomes to ``schedule``, requiring exact fold-id coverage.

        Exactly one outcome per planned fold, no unknown and no duplicate
        fold ids: the ledger must be a one-to-one answer to the schedule.
        Coverage violations are validation errors.
        """
        scheduled = [fold.fold_id for fold in schedule.folds]
        given = [outcome.fold_id for outcome in outcomes]
        problems: list[str] = []
        if len(set(given)) != len(given):
            problems.append("duplicate fold ids")
        unknown = sorted(set(given) - set(scheduled))
        if unknown:
            problems.append(f"unknown fold ids {unknown}")
        missing = sorted(set(scheduled) - set(given))
        if missing:
            problems.append(f"missing outcomes for fold ids {missing}")
        if problems:
            details = InitErrorDetails(
                type=PydanticCustomError(
                    "value_error",
                    "outcomes must reference the schedule's fold ids exactly "
                    "once: {problems}",
                    {"problems": "; ".join(problems)},
                ),
                loc=("outcomes",),
                input=list(outcomes),
            )
            raise ValidationError.from_exception_data("FoldOutcomeLedger", [details])
        return cls(
            schedule_sha256=canonical_sha256(_payload(schedule)),
            outcomes=tuple(outcomes),
        )


def fold_id_for(
    *,
    calendar_start: date,
    calendar_end: date,
    first_trading_day: date | None,
    last_trading_day: date | None,
) -> str:
    """The deterministic content hash of one fold's identifying inputs."""
    return canonical_sha256(
        {
            "calendar_start": calendar_start.isoformat(),
            "calendar_end": calendar_end.isoformat(),
            "first_trading_day": None
            if first_trading_day is None
            else first_trading_day.isoformat(),
            "last_trading_day": None
            if last_trading_day is None
            else last_trading_day.isoformat(),
            "mode": WalkForwardPolicy().mode,
        }
    )


def materialize_schedule(
    *,
    requested_start: date,
    requested_end: date,
    calendar: TradingCalendar,
    policy: WalkForwardPolicy,
    membership_snapshots: Mapping[str, str],
) -> FoldSchedule:
    """Derive the immutable fold schedule from the requested range.

    Only complete January-December calendar years fully inside
    ``[requested_start, requested_end]`` become folds.  A year the pinned
    calendar confirms as wholly closed keeps its fold with null trading
    boundaries (the runner must later evidence market-wide closure or fail).
    Leading/trailing partial years become ``not_evaluated_boundary`` windows.
    The pinned calendar must span the whole requested range; otherwise the
    schedule would silently mis-bound and the request is rejected.
    """
    if requested_start > requested_end:
        raise ValueError("requested_start must not follow requested_end")
    open_days = calendar.open_days
    if not open_days:
        raise ValueError("the pinned calendar has no open days")
    if requested_start < open_days[0] or requested_end > open_days[-1]:
        raise ValueError(
            f"the pinned calendar does not cover the requested range "
            f"{requested_start.isoformat()}..{requested_end.isoformat()} "
            f"(confirmed open days {open_days[0].isoformat()}.."
            f"{open_days[-1].isoformat()})"
        )

    def open_days_within(start: date, end: date) -> list[date]:
        return [day for day in open_days if start <= day <= end]

    folds: list[FoldWindow] = []
    boundaries: list[BoundaryWindow] = []
    for year in range(requested_start.year, requested_end.year + 1):
        calendar_start = date(year, 1, 1)
        calendar_end = date(year, 12, 31)
        if calendar_start < requested_start or calendar_end > requested_end:
            leading = calendar_start < requested_start
            trailing = calendar_end > requested_end
            if leading and trailing:
                reason = "partial_year_not_evaluated"
            elif leading:
                reason = "leading_partial_year"
            else:
                reason = "trailing_partial_year"
            boundaries.append(
                BoundaryWindow(
                    disposition=ScheduleDisposition.NOT_EVALUATED_BOUNDARY,
                    calendar_start=max(calendar_start, requested_start),
                    calendar_end=min(calendar_end, requested_end),
                    reason=reason,
                )
            )
            continue
        sessions = open_days_within(calendar_start, calendar_end)
        first_trading_day = sessions[0] if sessions else None
        last_trading_day = sessions[-1] if sessions else None
        warmup_calendar_end = date(year - 1, 12, 31)
        warmup_calendar_start = date(year - policy.warmup_years, 1, 1)
        warmup_sessions = open_days_within(warmup_calendar_start, warmup_calendar_end)
        # The three-calendar-year span is the warmup minimum, not its size:
        # real exchange calendars may hold fewer sessions per year than the
        # confirmed-session floor requires, so the window extends back in
        # whole January-1-anchored years until the floor is met (bounded so a
        # sparse calendar records a loud deficiency instead of looping).
        earliest_warmup_start = date(
            year - policy.warmup_years - MAX_WARMUP_EXTENSION_YEARS, 1, 1
        )
        while (
            len(warmup_sessions) < policy.min_warmup_trading_days
            and warmup_calendar_start > earliest_warmup_start
        ):
            warmup_calendar_start = date(warmup_calendar_start.year - 1, 1, 1)
            warmup_sessions = open_days_within(
                warmup_calendar_start, warmup_calendar_end
            )
        snapshots = {
            day.isoformat(): membership_snapshots[day.isoformat()]
            for day in sorted(
                day
                for day in (
                    date.fromisoformat(key) for key in membership_snapshots
                )
                if calendar_start <= day <= calendar_end
            )
        }
        folds.append(
            FoldWindow(
                fold_id=fold_id_for(
                    calendar_start=calendar_start,
                    calendar_end=calendar_end,
                    first_trading_day=first_trading_day,
                    last_trading_day=last_trading_day,
                ),
                calendar_start=calendar_start,
                calendar_end=calendar_end,
                first_trading_day=first_trading_day,
                last_trading_day=last_trading_day,
                warmup_calendar_start=warmup_calendar_start,
                warmup_calendar_end=warmup_calendar_end,
                warmup_session_count=len(warmup_sessions),
                oos_session_count=len(sessions),
                membership_snapshot_sha256s=snapshots,
            )
        )
    boundaries.sort(key=lambda window: window.calendar_start)
    for earlier, later in zip(boundaries, boundaries[1:]):
        if later.calendar_start <= earlier.calendar_end:
            raise ValueError("overlapping boundary windows")
    return FoldSchedule(
        mode=policy.mode,
        requested_start=requested_start,
        requested_end=requested_end,
        folds=tuple(folds),
        boundaries=tuple(boundaries),
    )


def write_canonical_json(path: str | Path, model: BaseModel) -> str:
    """Persist ``model`` canonically; return the SHA-256 of the written bytes.

    Canonical form: JSON with sorted keys, compact UTF-8 separators, ISO
    dates (``model_dump(mode="json")``) and no NaN/Infinity.  The write is an
    atomic replace; an existing file with *different* bytes is refused so an
    already-hashed schedule can never be modified in place (rewriting the
    identical bytes is allowed -- resuming must not require deletion).
    """
    destination = Path(path)
    payload = _payload(model)
    text = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    data = text.encode("utf-8")
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError(
                f"{destination} already exists with different bytes; an "
                "immutable artifact is never overwritten in place"
            )
        return sha256_file(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """The SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload(model: BaseModel) -> dict:
    return model.model_dump(mode="json")
