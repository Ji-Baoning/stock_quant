"""Immutable index-membership fact contract and raw/resolved dates.

Membership facts are qualification records only: whether a symbol belonged to
an index over an inclusive raw date range, backed by auditable evidence. The
resolver produces separate ``ResolvedMembership`` records that intersect the
raw facts with security-master boundaries and never rewrites the facts.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pyarrow as pa
import pytest
from pydantic import ValidationError

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.data_model.schemas import (
    UNIVERSE_MEMBERSHIP_COLUMNS,
    UNIVERSE_MEMBERSHIP_SCHEMA,
)
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    MembershipReason,
    MembershipStatus,
    ResolvedMembership,
    SecurityMasterBoundary,
    UniverseBoundaryError,
    membership_content_hash,
    membership_frame,
    resolve_memberships,
)


def make_fact(**overrides):
    """Return a valid raw fact payload with per-test overrides."""
    values = {
        "universe_id": "csi300",
        "symbol": "600000.SH",
        "raw_effective_from": date(2019, 1, 1),
        "raw_effective_to": None,
        "announcement_date": date(2018, 12, 17),
        "status": "active",
        "reason": "initial_constituent",
        "source": "csi_index_announcement",
        "source_url": "https://www.csindex.com.cn/announcement-2018-12.pdf",
        "snapshot_sha256": "a1" * 32,
        "source_document_sha256": "b2" * 32,
    }
    values.update(overrides)
    return values


def fact(**overrides) -> MembershipFact:
    return MembershipFact.model_validate(make_fact(**overrides))


def master(**overrides) -> SecurityMasterBoundary:
    """Return a boundary whose last tradable date is unproven by default."""
    values = {
        "symbol": "600000.SH",
        "list_date": date(2020, 1, 2),
        "last_tradable_date": None,
    }
    values.update(overrides)
    return SecurityMasterBoundary(**values)


# ---------------------------------------------------------------------------
# Fact contract: enums and strict validation
# ---------------------------------------------------------------------------


def test_status_and_reason_vocabularies_are_exact():
    assert {status.value for status in MembershipStatus} == {"active", "removed"}
    assert {reason.value for reason in MembershipReason} == {
        "initial_constituent",
        "regular_rebalance",
        "temporary_adjustment",
        "delisting",
        "merger_or_reorganization",
        "correction",
    }


def test_regular_removal_has_an_inclusive_end_before_removal_day():
    value = MembershipFact.model_validate(
        make_fact(
            raw_effective_to=date(2020, 6, 14),
            status="removed",
            reason="regular_rebalance",
        )
    )
    assert value.raw_effective_to == date(2020, 6, 14)


@pytest.mark.parametrize(
    "symbol", ["600000.SH", "000001.SZ", "430047.BJ", "688981.SH"]
)
def test_canonical_symbols_are_accepted(symbol):
    assert fact(symbol=symbol).symbol == symbol


@pytest.mark.parametrize(
    "symbol", ["600000", "sh.600000", "600000.XSHG", "60000.SH", "6000000.SH"]
)
def test_non_canonical_symbols_are_rejected(symbol):
    with pytest.raises(ValidationError):
        fact(symbol=symbol)


@pytest.mark.parametrize(
    "universe_id",
    ["csi300", "csi500", "csi1000", "sse50", "sse180", "szse100",
     "custom_momentum_pool", "custom_300v2"],
)
def test_canonical_universe_ids_are_accepted(universe_id):
    assert fact(universe_id=universe_id).universe_id == universe_id


@pytest.mark.parametrize(
    "universe_id", ["CSI300", "csi_300", "custom-foo", "custom_", "", "hs300"]
)
def test_non_canonical_universe_ids_are_rejected(universe_id):
    with pytest.raises(ValidationError):
        fact(universe_id=universe_id)


@pytest.mark.parametrize("field", ["snapshot_sha256", "source_document_sha256"])
def test_hashes_must_be_64_lowercase_hex_characters(field):
    assert fact(**{field: "0123abcd" * 8}) is not None
    for bad in ("AB" * 32, "cd" * 31, "0" * 63 + "g", ""):
        with pytest.raises(ValidationError):
            fact(**{field: bad})


@pytest.mark.parametrize("field", ["source", "source_url"])
def test_evidence_fields_must_be_nonempty(field):
    with pytest.raises(ValidationError):
        fact(**{field: ""})
    with pytest.raises(ValidationError):
        fact(**{field: "   "})


def test_source_url_must_be_an_auditable_locator_without_credentials():
    assert fact(source_url="https://www.csindex.com.cn/a.pdf") is not None
    assert fact(source_url="http://mirror.local:8080/snapshots/csi300.csv") is not None
    with pytest.raises(ValidationError):
        fact(source_url="https://user:secret@www.csindex.com.cn/a.pdf")
    with pytest.raises(ValidationError):
        fact(source_url="ftp://www.csindex.com.cn/a.pdf")
    with pytest.raises(ValidationError):
        fact(source_url="www.csindex.com.cn/a.pdf")


def test_raw_end_before_raw_start_is_rejected():
    with pytest.raises(ValidationError):
        fact(
            raw_effective_from=date(2020, 6, 15),
            raw_effective_to=date(2020, 6, 14),
            status="removed",
            reason="regular_rebalance",
        )


def test_null_raw_end_means_no_removal_observed():
    value = fact(raw_effective_to=None, status="active")
    assert value.raw_effective_to is None
    assert value.status is MembershipStatus.ACTIVE


@pytest.mark.parametrize(
    "status, reason",
    [
        ("removed", "regular_rebalance"),
        ("removed", "temporary_adjustment"),
        ("removed", "delisting"),
        ("removed", "merger_or_reorganization"),
        ("removed", "correction"),
        ("active", "initial_constituent"),
        ("active", "regular_rebalance"),
        ("active", "temporary_adjustment"),
        ("active", "correction"),
    ],
)
def test_consistent_status_reason_pairs_are_accepted(status, reason):
    value = fact(
        status=status,
        reason=reason,
        raw_effective_to=None if status == "active" else date(2020, 6, 14),
    )
    assert value.reason is MembershipReason(reason)


@pytest.mark.parametrize(
    "status, reason",
    [
        ("active", "delisting"),
        ("active", "merger_or_reorganization"),
        ("removed", "initial_constituent"),
    ],
)
def test_contradictory_status_reason_pairs_are_rejected(status, reason):
    with pytest.raises(ValidationError):
        fact(
            status=status,
            reason=reason,
            raw_effective_to=None if status == "active" else date(2020, 6, 14),
        )


def test_facts_are_frozen_and_reject_trading_or_quality_fields():
    value = fact()
    with pytest.raises(ValidationError):
        value.status = MembershipStatus.REMOVED
    with pytest.raises(ValidationError):
        MembershipFact.model_validate(make_fact(close_price=10.0))
    with pytest.raises(ValidationError):
        MembershipFact.model_validate(make_fact(is_suspended=False))


# ---------------------------------------------------------------------------
# Rendering and content hashing
# ---------------------------------------------------------------------------


def test_membership_frame_has_canonical_columns_and_is_deterministic():
    sh = fact()
    sz = fact(
        symbol="000001.SZ",
        raw_effective_from=date(2020, 6, 15),
        raw_effective_to=date(2024, 6, 14),
        status="removed",
        reason="regular_rebalance",
        announcement_date=date(2020, 6, 1),
    )
    frame = membership_frame([sz, sh])
    assert list(frame.columns) == UNIVERSE_MEMBERSHIP_COLUMNS
    assert frame["symbol"].tolist() == ["000001.SZ", "600000.SH"]
    assert frame["status"].tolist() == ["removed", "active"]
    assert frame["reason"].tolist() == ["regular_rebalance", "initial_constituent"]
    assert pd.to_datetime(frame.loc[0, "raw_effective_from"]) == pd.Timestamp(
        date(2020, 6, 15)
    )
    assert pd.isna(frame.loc[1, "raw_effective_to"])
    again = membership_frame([sh, sz])
    pd.testing.assert_frame_equal(frame, again)


def test_membership_frame_renders_empty_fact_sets():
    frame = membership_frame([])
    assert list(frame.columns) == UNIVERSE_MEMBERSHIP_COLUMNS
    assert frame.empty


def test_membership_content_hash_is_order_dependent_free_and_content_bound():
    first = fact()
    second = fact(symbol="000001.SZ")
    assert membership_content_hash([first, second]) == membership_content_hash(
        [second, first]
    )
    assert (
        membership_content_hash([first])
        != membership_content_hash([first, second])
    )
    changed = fact(snapshot_sha256="ff" * 32)
    assert membership_content_hash([first]) != membership_content_hash([changed])


def test_overlapping_fact_ranges_for_same_symbol_are_rejected():
    overlap_start = fact(
        raw_effective_to=date(2020, 6, 20),
        status="removed",
        reason="regular_rebalance",
    )
    overlap_end = fact(
        raw_effective_from=date(2020, 6, 20),
        status="active",
        reason="regular_rebalance",
    )
    with pytest.raises(ValueError, match="overlap"):
        membership_content_hash([overlap_start, overlap_end])
    with pytest.raises(ValueError, match="overlap"):
        resolve_memberships([overlap_start, overlap_end], {"600000.SH": master()})


def test_adjacent_fact_ranges_for_same_symbol_do_not_overlap():
    ended = fact(
        raw_effective_to=date(2020, 6, 14),
        status="removed",
        reason="regular_rebalance",
    )
    restarted = fact(
        raw_effective_from=date(2020, 6, 15),
        status="active",
        reason="regular_rebalance",
    )
    assert membership_content_hash([ended, restarted]) == membership_content_hash(
        [restarted, ended]
    )
    assert membership_content_hash(
        [ended, fact(symbol="000001.SZ")]
    ) == membership_content_hash([fact(symbol="000001.SZ"), ended])


def test_open_ended_range_overlaps_any_later_range_for_same_symbol():
    open_ended = fact()
    later = fact(
        raw_effective_from=date(2021, 1, 1),
        status="active",
        reason="regular_rebalance",
    )
    with pytest.raises(ValueError, match="overlap"):
        membership_content_hash([open_ended, later])


# ---------------------------------------------------------------------------
# Resolution: raw facts plus master boundaries
# ---------------------------------------------------------------------------


def test_resolver_keeps_raw_start_and_applies_listing_start():
    resolved = resolve_memberships(
        (fact(raw_effective_from=date(2019, 1, 1)),),
        {"600000.SH": master()},
    )[0]
    assert resolved.raw_effective_from == date(2019, 1, 1)
    assert resolved.effective_from == date(2020, 1, 2)
    assert resolved.boundary_adjustment_reason == "before_listing"
    assert resolved.raw_effective_to is None
    assert resolved.announcement_date == date(2018, 12, 17)
    assert resolved.usable is True


def test_resolution_never_overwrites_the_source_fact():
    source = fact(raw_effective_from=date(2019, 1, 1))
    resolve_memberships((source,), {"600000.SH": master()})
    assert source.raw_effective_from == date(2019, 1, 1)
    assert source.raw_effective_to is None
    assert source.status is MembershipStatus.ACTIVE


def test_start_at_or_after_listing_needs_no_boundary_adjustment():
    resolved = resolve_memberships(
        (fact(raw_effective_from=date(2020, 6, 15)),),
        {"600000.SH": master()},
    )[0]
    assert resolved.effective_from == date(2020, 6, 15)
    assert resolved.boundary_adjustment_reason is None


def test_removal_end_beyond_last_tradable_date_is_clamped():
    resolved = resolve_memberships(
        (
            fact(
                raw_effective_from=date(2020, 6, 15),
                raw_effective_to=date(2025, 6, 30),
                status="removed",
                reason="regular_rebalance",
            ),
        ),
        {"600000.SH": master(last_tradable_date=date(2025, 6, 13))},
    )[0]
    assert resolved.raw_effective_to == date(2025, 6, 30)
    assert resolved.effective_to == date(2025, 6, 13)
    assert resolved.boundary_adjustment_reason == "after_delisting"


def test_removal_end_within_listing_window_is_kept_as_is():
    resolved = resolve_memberships(
        (
            fact(
                raw_effective_from=date(2020, 6, 15),
                raw_effective_to=date(2024, 1, 1),
                status="removed",
                reason="regular_rebalance",
            ),
        ),
        {"600000.SH": master()},
    )[0]
    assert resolved.effective_to == date(2024, 1, 1)
    assert resolved.boundary_adjustment_reason is None


def test_open_end_meeting_a_delisted_security_is_clamped():
    resolved = resolve_memberships(
        (fact(raw_effective_from=date(2020, 6, 15)),),
        {"600000.SH": master(last_tradable_date=date(2025, 6, 13))},
    )[0]
    assert resolved.raw_effective_to is None
    assert resolved.effective_to == date(2025, 6, 13)
    assert resolved.boundary_adjustment_reason == "after_delisting"


def test_both_boundary_adjustments_are_recorded_in_canonical_order():
    resolved = resolve_memberships(
        (
            fact(
                raw_effective_from=date(2019, 1, 1),
                raw_effective_to=date(2025, 6, 30),
                status="removed",
                reason="regular_rebalance",
            ),
        ),
        {"600000.SH": master(last_tradable_date=date(2025, 6, 13))},
    )[0]
    assert resolved.effective_from == date(2020, 1, 2)
    assert resolved.effective_to == date(2025, 6, 13)
    assert resolved.boundary_adjustment_reason == "before_listing,after_delisting"


def test_delisting_removal_with_proven_last_tradable_date_resolves():
    resolved = resolve_memberships(
        (
            fact(
                raw_effective_from=date(2020, 6, 15),
                raw_effective_to=date(2025, 6, 30),
                status="removed",
                reason="delisting",
            ),
        ),
        {"600000.SH": master(last_tradable_date=date(2025, 6, 13))},
    )[0]
    assert resolved.effective_to == date(2025, 6, 13)
    assert resolved.boundary_adjustment_reason == "after_delisting"


def test_ambiguous_delisting_boundary_is_rejected():
    with pytest.raises(UniverseBoundaryError, match="last_tradable_date"):
        resolve_memberships(
            (
                fact(
                    raw_effective_to=date(2025, 6, 30),
                    announcement_date=date(2025, 6, 20),
                    status="removed",
                    reason="delisting",
                ),
            ),
            {"600000.SH": master(last_tradable_date=None)},
        )
    with pytest.raises(UniverseBoundaryError, match="last_tradable_date"):
        resolve_memberships(
            (
                fact(
                    raw_effective_to=date(2025, 6, 30),
                    status="removed",
                    reason="delisting",
                ),
            ),
            {},
        )


def test_resolver_never_infers_last_trade_date_from_the_announcement():
    """A delisting notice date is not a last-tradable-date, even when present."""
    with pytest.raises(UniverseBoundaryError, match="last_tradable_date"):
        resolve_memberships(
            (
                fact(
                    raw_effective_to=date(2025, 6, 20),
                    announcement_date=date(2025, 6, 20),
                    status="removed",
                    reason="delisting",
                ),
            ),
            {"600000.SH": master(last_tradable_date=None)},
        )


def test_empty_resolved_intersection_keeps_the_fact_and_marks_it_unusable():
    resolved = resolve_memberships(
        (fact(),),
        {
            "600000.SH": master(
                list_date=date(2021, 1, 1),
                last_tradable_date=date(2020, 6, 13),
            )
        },
    )[0]
    assert resolved.effective_from == date(2021, 1, 1)
    assert resolved.effective_to == date(2020, 6, 13)
    assert resolved.usable is False
    assert resolved.raw_effective_from == date(2019, 1, 1)
    assert resolved.raw_effective_to is None


def test_resolver_sorts_output_and_accepts_raw_mappings():
    first = make_fact(symbol="600000.SH")
    second = make_fact(
        symbol="000001.SZ",
        raw_effective_from=date(2018, 1, 1),
        announcement_date=date(2017, 12, 1),
    )
    resolved = resolve_memberships([second, first], {})
    assert [item.symbol for item in resolved] == ["000001.SZ", "600000.SH"]
    for item in resolved:
        assert isinstance(item, ResolvedMembership)
        assert item.effective_to is None
        assert item.boundary_adjustment_reason is None


def test_single_boundary_applies_to_a_single_symbol_batch():
    resolved = resolve_memberships(
        (fact(raw_effective_from=date(2019, 1, 1)),), master()
    )[0]
    assert resolved.effective_from == date(2020, 1, 2)
    assert resolved.boundary_adjustment_reason == "before_listing"


# ---------------------------------------------------------------------------
# Resolved model invariants
# ---------------------------------------------------------------------------


def test_resolved_membership_rejects_dates_outside_the_raw_interval():
    with pytest.raises(ValidationError):
        ResolvedMembership(
            universe_id="csi300",
            symbol="600000.SH",
            raw_effective_from=date(2020, 6, 15),
            raw_effective_to=None,
            effective_from=date(2020, 1, 2),
            effective_to=None,
            boundary_adjustment_reason=None,
            announcement_date=date(2020, 6, 1),
        )
    with pytest.raises(ValidationError):
        ResolvedMembership(
            universe_id="csi300",
            symbol="600000.SH",
            raw_effective_from=date(2019, 1, 1),
            raw_effective_to=date(2020, 6, 14),
            effective_to=date(2020, 6, 20),
            effective_from=date(2019, 1, 1),
            boundary_adjustment_reason=None,
            announcement_date=date(2018, 12, 17),
        )


def test_resolved_membership_rejects_inconsistent_usable_flag():
    base = {
        "universe_id": "csi300",
        "symbol": "600000.SH",
        "raw_effective_from": date(2020, 6, 15),
        "raw_effective_to": None,
        "effective_from": date(2020, 6, 15),
        "effective_to": None,
        "boundary_adjustment_reason": None,
        "announcement_date": date(2020, 6, 1),
    }
    assert ResolvedMembership(**base, usable=True).usable is True
    with pytest.raises(ValidationError):
        ResolvedMembership(**base, usable=False)
    with pytest.raises(ValidationError):
        ResolvedMembership(
            **{
                **base,
                "effective_from": date(2021, 1, 1),
                "effective_to": date(2020, 6, 13),
                "boundary_adjustment_reason": "after_delisting",
                "usable": True,
            }
        )


def test_resolved_membership_rejects_unknown_adjustment_reasons():
    base = {
        "universe_id": "csi300",
        "symbol": "600000.SH",
        "raw_effective_from": date(2019, 1, 1),
        "raw_effective_to": None,
        "effective_from": date(2020, 1, 2),
        "effective_to": None,
        "announcement_date": date(2018, 12, 17),
    }
    with pytest.raises(ValidationError):
        ResolvedMembership(**base, boundary_adjustment_reason="suspended")
    with pytest.raises(ValidationError):
        ResolvedMembership(**base, boundary_adjustment_reason="after_delisting")


# ---------------------------------------------------------------------------
# Arrow schema registration (raw table only)
# ---------------------------------------------------------------------------


def test_membership_columns_are_registered_in_canonical_order():
    assert UNIVERSE_MEMBERSHIP_COLUMNS == [
        "universe_id",
        "symbol",
        "raw_effective_from",
        "raw_effective_to",
        "announcement_date",
        "status",
        "reason",
        "source",
        "source_url",
        "snapshot_sha256",
        "source_document_sha256",
    ]


def test_membership_schema_uses_date32_for_dates_and_string_elsewhere():
    assert [field.name for field in UNIVERSE_MEMBERSHIP_SCHEMA] == (
        UNIVERSE_MEMBERSHIP_COLUMNS
    )
    date_columns = {"raw_effective_from", "raw_effective_to", "announcement_date"}
    for field in UNIVERSE_MEMBERSHIP_SCHEMA:
        if field.name in date_columns:
            assert pa.types.is_date32(field.type)
        else:
            assert pa.types.is_string(field.type)


def test_membership_table_is_registered_for_publication():
    assert STANDARDIZED_SCHEMAS["universe_membership"] is UNIVERSE_MEMBERSHIP_SCHEMA
