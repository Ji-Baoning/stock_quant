"""Immutable index-membership fact contract and raw/resolved dates.

Membership facts are qualification records only: they state whether a symbol
belonged to a canonical index over an inclusive raw date range and bind that
statement to auditable evidence. They never encode ST status, suspensions,
prices, factor values, cash or orders; those stay with the factor and
execution layers.

- ``MembershipFact`` — one published raw interval per ``universe_id/symbol``.
  ``raw_effective_to=None`` only means no removal was observed in this
  immutable data version; it never means "valid forever".
- ``ResolvedMembership`` — the fact intersected with fixed security-master
  boundaries: ``effective_from=max(raw_start, list_date)`` and a finite
  ``effective_to=min(raw_end, last_tradable_date)`` when both exist. Raw
  values are preserved and boundary moves are recorded as ``before_listing``
  / ``after_delisting``; an empty intersection keeps the fact and is marked
  unusable. Resolution never overwrites the source facts.
- ``membership_frame`` / ``membership_content_hash`` — deterministic
  rendering into the canonical column layout and the order-independent
  content hash used by versioned universe definitions.
- ``resolve_memberships`` — pure fact-plus-boundary resolution. A delisting
  removal without a proven ``last_tradable_date`` raises
  ``UniverseBoundaryError``: the last trade date is never inferred from a
  delisting notice or announcement date.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Mapping, Sequence

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from stock_quant.data_model.schemas import UNIVERSE_MEMBERSHIP_COLUMNS

#: First-class formal indices; custom pools must use the ``custom_<slug>`` form.
CANONICAL_UNIVERSE_IDS = (
    "csi300",
    "csi500",
    "csi1000",
    "sse50",
    "sse180",
    "szse100",
)

_CANONICAL_SYMBOL = re.compile(r"\d{6}\.(?:SH|SZ|BJ)")
_CANONICAL_UNIVERSE_ID = re.compile(
    r"(?:csi300|csi500|csi1000|sse50|sse180|szse100|custom_[a-z0-9_]+)"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# Auditable http(s) locator; the authority part must not embed credentials.
_AUDITABLE_URL = re.compile(r"https?://[^\s/@]+(?:/[^\s]*)?")


class MembershipStatus(str, Enum):
    """Known termination state of one membership interval."""

    ACTIVE = "active"
    REMOVED = "removed"


class MembershipReason(str, Enum):
    """Strict vocabulary for the event that produced the interval."""

    INITIAL_CONSTITUENT = "initial_constituent"
    REGULAR_REBALANCE = "regular_rebalance"
    TEMPORARY_ADJUSTMENT = "temporary_adjustment"
    DELISTING = "delisting"
    MERGER_OR_REORGANIZATION = "merger_or_reorganization"
    CORRECTION = "correction"


class MembershipBoundaryAdjustment(str, Enum):
    """How a security-master boundary moved a raw effective date."""

    BEFORE_LISTING = "before_listing"
    AFTER_DELISTING = "after_delisting"


class UniverseBoundaryError(ValueError):
    """A membership boundary cannot be resolved without guessing."""


def _validated_symbol(value: str) -> str:
    if not _CANONICAL_SYMBOL.fullmatch(value):
        raise ValueError(f"symbol must be canonical six-digit + exchange: {value!r}")
    return value


def _validated_universe_id(value: str) -> str:
    if not _CANONICAL_UNIVERSE_ID.fullmatch(value):
        raise ValueError(
            f"universe_id must be a canonical index or custom_<slug>: {value!r}"
        )
    return value


def _validated_sha256(value: str) -> str:
    if not _SHA256_HEX.fullmatch(value):
        raise ValueError(f"hash must be 64 lowercase hex characters: {value!r}")
    return value


def _validated_source_url(value: str) -> str:
    if not _AUDITABLE_URL.fullmatch(value):
        raise ValueError(
            "source_url must be a credential-free http(s) locator: "
            f"{value!r}"
        )
    return value


def _adjustment_text(adjustments: Sequence[MembershipBoundaryAdjustment]) -> str | None:
    """Render boundary adjustments in canonical order, or ``None``."""
    if not adjustments:
        return None
    return ",".join(adjustment.value for adjustment in adjustments)


class MembershipFact(BaseModel):
    """One immutable raw membership interval backed by evidence.

    Dates are inclusive closed bounds. ``raw_effective_to=None`` (paired with
    ``status=active``) means no removal was observed in this data version.
    A ``removed`` interval ends on the day before its removal effective date
    and carries the removal reason; ``initial_constituent`` always describes
    a still-open interval, while ``delisting`` and
    ``merger_or_reorganization`` are always terminations.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_id: str
    symbol: str
    raw_effective_from: date
    raw_effective_to: date | None = None
    announcement_date: date
    status: MembershipStatus
    reason: MembershipReason
    source: str
    source_url: str
    snapshot_sha256: str
    source_document_sha256: str

    @field_validator("universe_id")
    @classmethod
    def _universe_id(cls, value: str) -> str:
        return _validated_universe_id(value)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _validated_symbol(value)

    @field_validator("snapshot_sha256", "source_document_sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        return _validated_sha256(value)

    @field_validator("source")
    @classmethod
    def _nonempty_source(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(f"source must be nonblank text: {value!r}")
        return value

    @field_validator("source_url")
    @classmethod
    def _auditable_url(cls, value: str) -> str:
        return _validated_source_url(value)

    @model_validator(mode="after")
    def _check_interval(self) -> "MembershipFact":
        if (
            self.raw_effective_to is not None
            and self.raw_effective_to < self.raw_effective_from
        ):
            raise ValueError(
                "raw_effective_to must not precede raw_effective_from: "
                f"{self.raw_effective_from}..{self.raw_effective_to}"
            )
        active = self.status is MembershipStatus.ACTIVE
        if active != (self.raw_effective_to is None):
            raise ValueError(
                "status and raw_effective_to disagree: active facts have no raw "
                "end and removed facts end on the day before removal"
            )
        if self.reason in (
            MembershipReason.DELISTING,
            MembershipReason.MERGER_OR_REORGANIZATION,
        ) and self.status is not MembershipStatus.REMOVED:
            raise ValueError(f"{self.reason.value} intervals must be removed")
        if (
            self.reason is MembershipReason.INITIAL_CONSTITUENT
            and self.status is not MembershipStatus.ACTIVE
        ):
            raise ValueError("initial_constituent intervals must be active")
        return self


@dataclass(frozen=True)
class SecurityMasterBoundary:
    """Fixed point-in-time master boundaries for one canonical symbol.

    ``last_tradable_date`` may only carry a value whose source semantics are
    explicitly "last tradable date" (design spec: resolution rules); a
    termination-announcement date must be left as ``None``.
    """

    symbol: str
    list_date: date | None = None
    last_tradable_date: date | None = None

    def __post_init__(self) -> None:
        _validated_symbol(self.symbol)


class ResolvedMembership(BaseModel):
    """A raw fact intersected with fixed security-master boundaries.

    Raw values are preserved verbatim; ``effective_from``/``effective_to``
    are the usable bounds. ``usable=False`` marks an empty intersection and
    lets coverage acceptance fail loudly instead of hiding the fact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_id: str
    symbol: str
    raw_effective_from: date
    raw_effective_to: date | None
    effective_from: date
    effective_to: date | None
    boundary_adjustment_reason: str | None
    announcement_date: date
    usable: bool = True

    @field_validator("universe_id")
    @classmethod
    def _universe_id(cls, value: str) -> str:
        return _validated_universe_id(value)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _validated_symbol(value)

    @model_validator(mode="after")
    def _check_resolved(self) -> "ResolvedMembership":
        if self.effective_from < self.raw_effective_from:
            raise ValueError(
                "effective_from cannot precede raw_effective_from: "
                f"{self.effective_from} < {self.raw_effective_from}"
            )
        if (
            self.raw_effective_to is not None
            and self.effective_to is not None
            and self.effective_to > self.raw_effective_to
        ):
            raise ValueError(
                "effective_to cannot follow raw_effective_to: "
                f"{self.effective_to} > {self.raw_effective_to}"
            )
        if self.effective_to is None:
            if not self.usable:
                raise ValueError("an open resolved interval cannot be unusable")
        elif self.effective_from > self.effective_to:
            if self.usable:
                raise ValueError(
                    "an empty resolved intersection must be marked unusable"
                )
        elif not self.usable:
            raise ValueError("a non-empty resolved interval must be usable")
        expected: list[MembershipBoundaryAdjustment] = []
        if self.effective_from > self.raw_effective_from:
            expected.append(MembershipBoundaryAdjustment.BEFORE_LISTING)
        if self.effective_to is not None and (
            self.raw_effective_to is None
            or self.effective_to < self.raw_effective_to
        ):
            expected.append(MembershipBoundaryAdjustment.AFTER_DELISTING)
        expected_text = _adjustment_text(expected)
        if self.boundary_adjustment_reason != expected_text:
            raise ValueError(
                "boundary_adjustment_reason does not match the resolved dates: "
                f"expected {expected_text!r}, got "
                f"{self.boundary_adjustment_reason!r}"
            )
        return self


def _fact_sort_key(item: MembershipFact) -> tuple[str, str, date]:
    return (item.universe_id, item.symbol, item.raw_effective_from)


def _validated_facts(
    facts: Sequence[MembershipFact | Mapping[str, Any]],
) -> list[MembershipFact]:
    validated: list[MembershipFact] = []
    for item in facts:
        if isinstance(item, MembershipFact):
            validated.append(item)
        elif isinstance(item, Mapping):
            validated.append(MembershipFact.model_validate(dict(item)))
        else:
            raise TypeError(
                "membership facts must be MembershipFact or mapping, got "
                + type(item).__name__
            )
    return validated


def _interval_text(start: date, end: date | None) -> str:
    """Render one inclusive interval, with an open end marked explicitly."""
    return f"[{start.isoformat()}, {end.isoformat() if end else 'open'}]"


def _ensure_non_overlapping(facts: Sequence[MembershipFact]) -> None:
    """Reject overlapping raw intervals for the same universe_id/symbol."""
    ordered = sorted(facts, key=_fact_sort_key)
    for previous, following in zip(ordered, ordered[1:]):
        if (previous.universe_id, previous.symbol) != (
            following.universe_id,
            following.symbol,
        ):
            continue
        if previous.raw_effective_to is None or (
            previous.raw_effective_to >= following.raw_effective_from
        ):
            first = _interval_text(
                previous.raw_effective_from, previous.raw_effective_to
            )
            second = _interval_text(
                following.raw_effective_from, following.raw_effective_to
            )
            raise ValueError(
                "membership facts overlap for "
                f"{previous.universe_id}/{previous.symbol}: "
                f"{first} and {second}"
            )


def _fact_record(item: MembershipFact) -> dict[str, Any]:
    return {
        "universe_id": item.universe_id,
        "symbol": item.symbol,
        "raw_effective_from": item.raw_effective_from,
        "raw_effective_to": item.raw_effective_to,
        "announcement_date": item.announcement_date,
        "status": item.status.value,
        "reason": item.reason.value,
        "source": item.source,
        "source_url": item.source_url,
        "snapshot_sha256": item.snapshot_sha256,
        "source_document_sha256": item.source_document_sha256,
    }


def membership_frame(
    facts: Sequence[MembershipFact | Mapping[str, Any]],
) -> pd.DataFrame:
    """Render facts into the canonical ``universe_membership`` column layout.

    Date columns are co-erced to ``datetime64`` (Arrow casts them to
    ``date32`` at publish) and rows are sorted by universe, symbol and start
    date so identical inputs always produce identical bytes.
    """
    validated = _validated_facts(facts)
    validated.sort(key=_fact_sort_key)
    frame = pd.DataFrame(
        [_fact_record(item) for item in validated],
        columns=UNIVERSE_MEMBERSHIP_COLUMNS,
    )
    for column in ("raw_effective_from", "raw_effective_to", "announcement_date"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    frame = frame.sort_values(
        ["universe_id", "symbol", "raw_effective_from"], kind="stable"
    ).reset_index(drop=True)
    return frame[UNIVERSE_MEMBERSHIP_COLUMNS]


def membership_content_hash(
    facts: Sequence[MembershipFact | Mapping[str, Any]],
) -> str:
    """SHA-256 of the canonical serialization of one fact version.

    The hash is order-independent and changes whenever any fact content
    changes; fact sets with overlapping same-symbol intervals have no
    legitimate version and are rejected.
    """
    validated = _validated_facts(facts)
    _ensure_non_overlapping(validated)
    payload = [
        item.model_dump(mode="json")
        for item in sorted(validated, key=_fact_sort_key)
    ]
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_fact(
    fact: MembershipFact, boundary: SecurityMasterBoundary | None
) -> ResolvedMembership:
    if fact.reason is MembershipReason.DELISTING and (
        boundary is None or boundary.last_tradable_date is None
    ):
        raise UniverseBoundaryError(
            f"membership fact {fact.universe_id}/{fact.symbol} removed for "
            "delisting cannot be resolved without a proven last_tradable_date "
            "in the security master boundary; the delisting notice never "
            "substitutes for it"
        )
    adjustments: list[MembershipBoundaryAdjustment] = []
    effective_from = fact.raw_effective_from
    if boundary is not None and boundary.list_date is not None:
        if boundary.list_date > effective_from:
            effective_from = boundary.list_date
            adjustments.append(MembershipBoundaryAdjustment.BEFORE_LISTING)
    effective_to = fact.raw_effective_to
    if boundary is not None and boundary.last_tradable_date is not None:
        last_tradable = boundary.last_tradable_date
        if effective_to is None or last_tradable < effective_to:
            effective_to = last_tradable
            adjustments.append(MembershipBoundaryAdjustment.AFTER_DELISTING)
    return ResolvedMembership(
        universe_id=fact.universe_id,
        symbol=fact.symbol,
        raw_effective_from=fact.raw_effective_from,
        raw_effective_to=fact.raw_effective_to,
        effective_from=effective_from,
        effective_to=effective_to,
        boundary_adjustment_reason=_adjustment_text(adjustments),
        announcement_date=fact.announcement_date,
        usable=effective_to is None or effective_from <= effective_to,
    )


def resolve_memberships(
    facts: Sequence[MembershipFact | Mapping[str, Any]],
    master: SecurityMasterBoundary
    | Mapping[str, SecurityMasterBoundary]
    | None = None,
) -> tuple[ResolvedMembership, ...]:
    """Resolve raw facts against fixed master boundaries without rewriting them.

    ``master`` is either a mapping from canonical symbol to
    ``SecurityMasterBoundary`` or a single boundary applied to its own
    symbol. Results are sorted by universe, symbol and raw start date.
    """
    validated = _validated_facts(facts)
    _ensure_non_overlapping(validated)
    if master is None:
        boundaries: dict[str, SecurityMasterBoundary] = {}
    elif isinstance(master, SecurityMasterBoundary):
        boundaries = {master.symbol: master}
    else:
        boundaries = {}
        for symbol, boundary in master.items():
            if not isinstance(boundary, SecurityMasterBoundary):
                raise TypeError(
                    "master boundaries must be SecurityMasterBoundary, got "
                    f"{type(boundary).__name__} for {symbol!r}"
                )
            boundaries[symbol] = boundary
    resolved = [
        _resolve_fact(item, boundaries.get(item.symbol)) for item in validated
    ]
    resolved.sort(
        key=lambda item: (item.universe_id, item.symbol, item.raw_effective_from)
    )
    return tuple(resolved)
