"""Immutable fold schedule and the separate outcome ledger.

``materialize_schedule`` turns the requested evaluation range plus the pinned
trading calendar into complete January-December OOS folds with real trading
boundaries, warmup windows and deterministic content-hashed fold ids; dates
that cannot form a complete 12-month fold are recorded as
``not_evaluated_boundary`` rows and are never silently dropped.  The schedule
holds only pre-execution facts: after ``fold_schedule.json`` is written and
hashed it is never modified -- run results live in a separate
``FoldOutcomeLedger`` bound to the schedule's exact canonical SHA-256, with
exactly one outcome per planned fold and no unknown or duplicate fold ids.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.research.walk_forward.policy import WalkForwardPolicy
from stock_quant.research.walk_forward.schedule import (
    FoldOutcome,
    FoldOutcomeLedger,
    materialize_schedule,
    sha256_file,
    write_canonical_json,
)


def _open_days(start: date, end: date) -> list[date]:
    """Weekdays minus January 1 (a deterministic stand-in exchange calendar)."""
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5 and (current.month, current.day) != (1, 1):
            days.append(current)
        current += timedelta(days=1)
    return days


@pytest.fixture
def calendar() -> TradingCalendar:
    return TradingCalendar.from_open_days(
        _open_days(date(2014, 1, 1), date(2024, 12, 31))
    )


@pytest.fixture
def schedule(calendar) -> object:
    return materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2021, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )


# ---------------------------------------------------------------------------
# Calendar boundaries
# ---------------------------------------------------------------------------


def test_annual_fold_uses_real_trading_boundaries(calendar):
    schedule = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2020, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    fold = schedule.folds[0]
    assert (fold.calendar_start, fold.calendar_end) == (
        date(2020, 1, 1),
        date(2020, 12, 31),
    )
    assert (fold.first_trading_day, fold.last_trading_day) == (
        date(2020, 1, 2),
        date(2020, 12, 31),
    )


def test_partial_tail_is_recorded_not_silently_dropped(calendar):
    schedule = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2021, 6, 30),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    assert schedule.boundaries[-1].disposition == "not_evaluated_boundary"
    assert schedule.boundaries[-1].calendar_start == date(2021, 1, 1)


def test_partial_head_is_also_recorded(calendar):
    schedule = materialize_schedule(
        requested_start=date(2019, 7, 1),
        requested_end=date(2020, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    assert [fold.calendar_start.year for fold in schedule.folds] == [2020]
    assert len(schedule.boundaries) == 1
    assert schedule.boundaries[0].calendar_start == date(2019, 7, 1)
    assert schedule.boundaries[0].calendar_end == date(2019, 12, 31)


def test_warmup_window_is_three_calendar_years_before_the_fold(calendar):
    schedule = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2020, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    fold = schedule.folds[0]
    assert (fold.warmup_calendar_start, fold.warmup_calendar_end) == (
        date(2017, 1, 1),
        date(2019, 12, 31),
    )
    assert fold.warmup_session_count == len(
        _open_days(date(2017, 1, 1), date(2019, 12, 31))
    )
    assert fold.oos_session_count == len(
        _open_days(date(2020, 1, 2), date(2020, 12, 31))
    )


def test_fold_id_is_deterministic_content_hash(calendar):
    left = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2020, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    right = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2020, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    assert left.folds[0].fold_id == right.folds[0].fold_id
    assert len(left.folds[0].fold_id) == 64


def test_market_wide_closed_year_is_retained_with_null_boundaries():
    empty_calendar = TradingCalendar.from_open_days(
        _open_days(date(2014, 1, 1), date(2019, 12, 31))
        + _open_days(date(2021, 1, 1), date(2024, 12, 31))
    )
    schedule = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2020, 12, 31),
        calendar=empty_calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    fold = schedule.folds[0]
    assert fold.first_trading_day is None
    assert fold.last_trading_day is None
    assert fold.oos_session_count == 0


def test_schedule_rejects_a_range_outside_the_pinned_calendar(calendar):
    with pytest.raises(ValueError, match="cover"):
        materialize_schedule(
            requested_start=date(2020, 1, 1),
            requested_end=date(2030, 12, 31),
            calendar=calendar,
            policy=WalkForwardPolicy(),
            membership_snapshots={},
        )


def test_schedule_rejects_end_before_start(calendar):
    with pytest.raises(ValueError):
        materialize_schedule(
            requested_start=date(2020, 12, 31),
            requested_end=date(2020, 1, 1),
            calendar=calendar,
            policy=WalkForwardPolicy(),
            membership_snapshots={},
        )


def test_membership_snapshots_are_carried_per_fold_day(calendar):
    snapshots = {
        day.isoformat(): "a" * 64
        for day in _open_days(date(2020, 1, 2), date(2020, 3, 31))
    }
    schedule = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2020, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots=snapshots,
    )
    fold = schedule.folds[0]
    assert set(fold.membership_snapshot_sha256s) == set(snapshots)
    assert list(fold.membership_snapshot_sha256s) == sorted(
        fold.membership_snapshot_sha256s
    )


def test_schedule_is_frozen_and_serializes_deterministically(schedule, calendar):
    with pytest.raises(ValidationError):
        schedule.requested_start = date(2019, 1, 1)  # type: ignore[misc]
    again = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2021, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    assert schedule == again


# ---------------------------------------------------------------------------
# Canonical persistence and the outcome ledger
# ---------------------------------------------------------------------------


def test_outcome_does_not_mutate_schedule(tmp_path, schedule):
    before = write_canonical_json(tmp_path / "fold_schedule.json", schedule)
    ledger = FoldOutcomeLedger(
        schedule_sha256=before,
        outcomes=(
            FoldOutcome(
                fold_id=schedule.folds[0].fold_id,
                status="failed_preflight",
                reason_code="DATA_GAP",
            ),
        ),
    )
    write_canonical_json(tmp_path / "fold_outcomes.json", ledger)
    assert sha256_file(tmp_path / "fold_schedule.json") == before


def test_outcome_must_reference_every_planned_fold(schedule):
    with pytest.raises(ValidationError, match="fold ids"):
        FoldOutcomeLedger.for_schedule(schedule, outcomes=())


def test_outcome_rejects_unknown_fold_ids(schedule):
    with pytest.raises(ValidationError, match="fold ids"):
        FoldOutcomeLedger.for_schedule(
            schedule,
            outcomes=(
                FoldOutcome(
                    fold_id="0" * 64,
                    status="failed_preflight",
                    reason_code="DATA_GAP",
                ),
            ),
        )


def test_outcome_rejects_duplicate_fold_ids(schedule):
    outcome = FoldOutcome(
        fold_id=schedule.folds[0].fold_id,
        status="executed",
        reason_code=None,
    )
    with pytest.raises(ValidationError, match="fold ids"):
        FoldOutcomeLedger.for_schedule(schedule, outcomes=(outcome, outcome))


def test_for_schedule_binds_the_exact_schedule_hash(tmp_path, schedule):
    first, second = schedule.folds
    ledger = FoldOutcomeLedger.for_schedule(
        schedule,
        outcomes=(
            FoldOutcome(fold_id=first.fold_id, status="executed"),
            FoldOutcome(
                fold_id=second.fold_id,
                status="skipped_not_tradeable",
                reason_code="MARKET_WIDE_CLOSURE",
            ),
        ),
    )
    expected = write_canonical_json(tmp_path / "fold_schedule.json", schedule)
    assert ledger.schedule_sha256 == expected
    assert [outcome.fold_id for outcome in ledger.outcomes] == [
        first.fold_id,
        second.fold_id,
    ]


def test_write_canonical_json_is_sorted_compact_and_nan_free(tmp_path):
    from pydantic import BaseModel

    class Holder(BaseModel):
        b: int
        a: dict

    model = Holder(b=1, a={"d": 2.5, "c": date(2020, 1, 2)})
    path = tmp_path / "x.json"
    digest = write_canonical_json(path, model)
    assert path.read_text(encoding="utf-8") == (
        '{"a":{"c":"2020-01-02","d":2.5},"b":1}'
    )
    assert digest == sha256_file(path)


def test_write_canonical_json_refuses_overwriting_with_different_bytes(
    tmp_path, schedule, calendar
):
    path = tmp_path / "fold_schedule.json"
    first_hash = write_canonical_json(path, schedule)
    assert write_canonical_json(path, schedule) == first_hash
    changed = materialize_schedule(
        requested_start=date(2020, 1, 1),
        requested_end=date(2022, 12, 31),
        calendar=calendar,
        policy=WalkForwardPolicy(),
        membership_snapshots={},
    )
    with pytest.raises(ValueError, match="already exists"):
        write_canonical_json(path, changed)
    assert sha256_file(path) == first_hash


def test_fold_outcome_status_vocabulary_is_closed():
    with pytest.raises(ValidationError):
        FoldOutcome(fold_id="x", status="deleted_for_underperformance")
