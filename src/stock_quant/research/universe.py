"""Frozen universe definitions and signal-day membership resolution.

This module is the pure membership layer between immutable membership facts
and the factor layer. It contains no factor, market or execution logic: a
symbol is a member of a frozen universe on a signal day if and only if its
resolved interval covers the day and its evidence was public
(``announcement_date <= day``) on that day.

- ``UniverseDefinition`` — one explicit, frozen version of a universe:
  schema version, universe id, rules version, membership-table content hash,
  actual coverage window and evidence-summary hash. The SHA-256 of its
  canonical JSON is the definition ``version`` (``universe_version``).
- ``UniverseResolver`` — resolves members for a single day from fixed
  ``ResolvedMembership`` rows: date bounds and announcement visibility only.
  Rows whose intersection with the security master was empty (``usable=False``)
  never become members. Days outside the pinned coverage window raise
  ``UniverseCoverageError`` instead of silently returning an empty set.
  When the backing ``facts`` are supplied, the pinned membership-table hash
  is validated against them before any member is served.
- ``snapshot_for`` — the SHA-256 of the canonical JSON
  ``{"day": ISO, "symbols": [sorted], "universe_id": id}``; it is
  deterministic and independent of source row order.
- ``load_universe_definition`` — loads and validates a YAML definition
  document. Placeholder values (as in ``configs/universes/csi300.yml``)
  fail validation: a formal run can never use an unpinned template.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from stock_quant.data_model.universe_membership import (
    MembershipFact,
    ResolvedMembership,
    membership_content_hash,
    membership_slice_hash,
)
from stock_quant.safe_yaml import read_yaml

#: Mirrors the global plan constraint for first-class and custom universe ids.
_CANONICAL_UNIVERSE_ID = re.compile(
    r"(?:csi300|csi500|csi1000|sse50|sse180|szse100|custom_[a-z0-9_]+)"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class UniverseCoverageError(ValueError):
    """A requested signal day lies outside the pinned coverage window."""


def _identity_row(
    item: MembershipFact | ResolvedMembership,
) -> tuple[str, str, date, date | None, date]:
    """The date/identity core shared by a fact and its resolved row."""
    return (
        item.universe_id,
        item.symbol,
        item.raw_effective_from,
        item.raw_effective_to,
        item.announcement_date,
    )


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


def canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical compact JSON rendering of ``payload``."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID = "universe_id"
MEMBERSHIP_OBSERVATION_GAP = "membership_observation_gap"
_GAP_REASONS = frozenset({MEMBERSHIP_OBSERVATION_GAP})


class MembershipCoverageSegment(BaseModel):
    """One maximal run of consecutive membership observations (7.0.1)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    start: date
    end: date
    evidence_sha256: str

    @field_validator("evidence_sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        return _validated_sha256(value)


class MembershipCoverageGap(BaseModel):
    """A missed-observation window; never carries inferred boundaries."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    start: date
    end: date
    reason: str
    evidence_sha256: str

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        if value not in _GAP_REASONS:
            raise ValueError(f"unknown membership gap reason: {value!r}")
        return value

    @field_validator("evidence_sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        return _validated_sha256(value)


class UniverseDefinition(BaseModel):
    """One frozen, auditable version of an index universe.

    The definition pins the exact membership-table content it was built from
    (``membership_table_sha256``), the rules that produced it, the actual
    covered window (never the aspirational target) and the evidence summary.
    Its canonical-JSON SHA-256 (``version``) enters the experiment identity,
    so any content change yields a new version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1, 2] = 1
    universe_id: str
    rules_version: str
    membership_table_sha256: str
    coverage_start: date
    coverage_end: date
    evidence_summary_sha256: str
    membership_hash_scope: Literal["universe_id"] | None = None
    coverage_segments: tuple[MembershipCoverageSegment, ...] | None = None
    coverage_gaps: tuple[MembershipCoverageGap, ...] | None = None

    @field_validator("universe_id")
    @classmethod
    def _universe_id(cls, value: str) -> str:
        return _validated_universe_id(value)

    @field_validator("rules_version")
    @classmethod
    def _rules_version(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(f"rules_version must be nonblank text: {value!r}")
        return value

    @field_validator("membership_table_sha256", "evidence_summary_sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        return _validated_sha256(value)

    @field_validator("coverage_gaps")
    @classmethod
    def _no_empty_gaps(cls, value):
        return value or None  # 空列表归一为 None，保证序列化唯一形

    @model_validator(mode="after")
    def _check_coverage(self) -> "UniverseDefinition":
        if self.coverage_start > self.coverage_end:
            raise ValueError(
                "coverage_start must not follow coverage_end: "
                f"{self.coverage_start} > {self.coverage_end}"
            )
        return self

    @model_validator(mode="after")
    def _check_schema_shape(self) -> "UniverseDefinition":
        if self.schema_version == 1:
            if any(value is not None for value in (
                    self.membership_hash_scope, self.coverage_segments,
                    self.coverage_gaps)):
                raise ValueError(
                    "schema-v1 definitions carry no membership_hash_scope/"
                    "coverage_segments/coverage_gaps (spec 7.0.1/7.0.3 keep v1 "
                    "whole-table-only; refusing v2 keys is this plan's guard)")
            return self
        if self.membership_hash_scope != MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID:
            raise ValueError("schema-v2 requires membership_hash_scope: universe_id")
        segments = self.coverage_segments or ()
        if not segments:
            raise ValueError("schema-v2 requires coverage_segments")
        ordered = sorted(segments, key=lambda item: item.start)
        for earlier, later in zip(ordered, ordered[1:]):
            if later.start <= earlier.end:
                raise ValueError(f"coverage segments overlap: {earlier}/{later}")
        if (self.coverage_start != ordered[0].start
                or self.coverage_end != ordered[-1].end):
            raise ValueError(
                "coverage_start/end must equal the segment envelope "
                f"[{ordered[0].start}, {ordered[-1].end}]")
        tiles = sorted(
            [(item.start, item.end) for item in ordered]
            + [(gap.start, gap.end) for gap in self.coverage_gaps or ()])
        cursor = self.coverage_start
        for start, end in tiles:
            if start != cursor:
                raise ValueError(
                    "segments and gaps must complement exactly over the "
                    f"envelope: expected a tile starting {cursor}, got "
                    f"[{start}, {end}]")
            cursor = end + timedelta(days=1)
        if cursor != self.coverage_end + timedelta(days=1):
            raise ValueError(
                "segments and gaps must complement exactly over the envelope: "
                f"tiling stops at {cursor - timedelta(days=1)}")
        return self

    @property
    def version(self) -> str:
        """SHA-256 of the definition's canonical JSON; the universe version.

        ``exclude_none`` keeps schema-v1 documents byte-identical to the
        pre-v2 rendering (v1 has no optional keys), so every frozen v1
        version survives this change (spec 2.3).
        """
        return canonical_json_sha256(
            self.model_dump(mode="json", exclude_none=True))


def membership_coverage_violations(
    definition: "UniverseDefinition",
    facts: Sequence[MembershipFact],
) -> list[str]:
    """Fact intervals must sit inside the segments and never cross a gap."""
    segments = definition.coverage_segments or ()
    gaps = definition.coverage_gaps or ()
    violations: list[str] = []
    for item in facts:
        # An open fact (``raw_effective_to is None``) is still open only as far
        # as this definition's envelope; ``date.max`` would sit beyond every
        # segment and fail ``end <= segment.end``, marking every active fact as
        # outside coverage.
        end = item.raw_effective_to or definition.coverage_end
        inside = any(segment.start <= item.raw_effective_from and end <= segment.end
                     for segment in segments)
        crosses_gap = any(item.raw_effective_from <= gap.end and end >= gap.start
                          for gap in gaps)
        if not inside or crosses_gap:
            violations.append(f"{item.symbol}@{item.raw_effective_from.isoformat()}")
    return violations


def window_crosses_membership_gap(
    definition: "UniverseDefinition", window_start: date, window_end: date
) -> "MembershipCoverageGap | None":
    """The first gap intersecting [window_start, window_end], if any."""
    for gap in definition.coverage_gaps or ():
        if window_start <= gap.end and window_end >= gap.start:
            return gap
    return None


class UniverseResolver:
    """Signal-day membership resolution over one frozen definition.

    Built from the definition and its fixed ``ResolvedMembership`` rows. The
    optional ``facts`` are the immutable ``MembershipFact`` rows backing those
    resolved rows; when supplied, their content hash must equal the
    definition's pinned ``membership_table_sha256`` and the resolved rows must
    correspond one-to-one, otherwise construction fails loudly.

    ``members_on`` applies only the resolved date bounds and the
    ``announcement_date <= day`` visibility gate, excludes rows marked
    unusable, and returns symbols in sorted order. It never consults factor,
    market or execution state; trading conditions remain downstream.
    """

    def __init__(
        self,
        definition: UniverseDefinition,
        resolved: Sequence[ResolvedMembership],
        *,
        facts: Sequence[MembershipFact | Mapping[str, Any]] | None = None,
    ) -> None:
        if not isinstance(definition, UniverseDefinition):
            raise TypeError(
                "definition must be a UniverseDefinition, got "
                + type(definition).__name__
            )
        self._definition = definition
        rows: list[ResolvedMembership] = []
        for row in resolved:
            if not isinstance(row, ResolvedMembership):
                raise TypeError(
                    "resolved rows must be ResolvedMembership, got "
                    + type(row).__name__
                )
            if row.universe_id != definition.universe_id:
                raise ValueError(
                    "resolved row belongs to universe_id "
                    f"{row.universe_id!r}, not the definition's "
                    f"{definition.universe_id!r}"
                )
            rows.append(row)
        rows.sort(
            key=lambda row: (
                row.symbol,
                row.raw_effective_from,
                row.effective_from,
                row.announcement_date,
            )
        )
        self._resolved = tuple(rows)
        if facts is not None:
            self._validate_pinned_facts(facts)

    def _validate_pinned_facts(
        self, facts: Sequence[MembershipFact | Mapping[str, Any]]
    ) -> None:
        table_hash = (
            membership_slice_hash(list(facts), self._definition.universe_id)
            if self._definition.schema_version == 2
            else membership_content_hash(list(facts))
        )
        if table_hash != self._definition.membership_table_sha256:
            raise ValueError(
                "pinned membership_table_sha256 mismatch: definition pins "
                f"{self._definition.membership_table_sha256}, facts hash to "
                f"{table_hash}"
            )
        fact_identities = sorted(
            _identity_row(item) for item in _validated_fact_rows(facts)
        )
        resolved_identities = sorted(
            _identity_row(row) for row in self._resolved
        )
        if fact_identities != resolved_identities:
            raise ValueError(
                "resolved rows do not correspond to the pinned facts; "
                "resolution must cover exactly the pinned membership table"
            )

    @property
    def definition(self) -> UniverseDefinition:
        return self._definition

    @property
    def universe_id(self) -> str:
        return self._definition.universe_id

    def _check_coverage(self, day: date) -> None:
        if (
            day < self._definition.coverage_start
            or day > self._definition.coverage_end
        ):
            raise UniverseCoverageError(
                f"signal day {day.isoformat()} is outside the "
                f"{self._definition.universe_id} coverage window "
                f"[{self._definition.coverage_start.isoformat()}, "
                f"{self._definition.coverage_end.isoformat()}]"
            )

    def members_on(self, day: date) -> tuple[str, ...]:
        """Members of the frozen universe on ``day``, sorted.

        A symbol qualifies only when its resolved interval (inclusive bounds,
        open end allowed) covers ``day``, its evidence was announced on or
        before ``day``, and its resolved intersection is usable.
        """
        self._check_coverage(day)
        symbols = {
            row.symbol
            for row in self._resolved
            if row.usable
            and row.effective_from <= day
            and (row.effective_to is None or day <= row.effective_to)
            and row.announcement_date <= day
        }
        return tuple(sorted(symbols))

    def snapshot_for(self, day: date) -> str:
        """SHA-256 of the canonical (universe_id, day, sorted symbols) JSON."""
        payload = {
            "universe_id": self._definition.universe_id,
            "day": day.isoformat(),
            "symbols": list(self.members_on(day)),
        }
        return canonical_json_sha256(payload)


def _validated_fact_rows(
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


@dataclass(frozen=True)
class UniverseCoverageCriterion:
    """The version-bound full-history criterion of one publish moment.

    ``acceptance_start`` is the earliest ``coverage_start`` over the enabled
    universe definitions; ``None`` means the scan found no enabled definition,
    and full-history acceptance then has no criterion and cannot be claimed.
    ``definition_hashes`` pins the exact definition versions the criterion was
    computed from, so a later ``configs/universes`` change never re-judges an
    already-published dataset.  ``skipped`` records the explicitly disabled
    files so the manifest shows what was ignored on purpose.
    """

    acceptance_start: date | None
    definition_hashes: Mapping[str, str]
    skipped: tuple[str, ...]


def load_universe_coverage_criterion(
    directory: str | Path,
) -> UniverseCoverageCriterion:
    """Scan ``*.yml`` universe definitions for the full-history start.

    A definition that is not in the enabled set (``enabled: false``) is skipped
    and recorded.  Every other file must load and validate: a parse or schema
    failure blocks publication rather than silently shrinking the criterion.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return UniverseCoverageCriterion(None, {}, ())
    hashes: dict[str, str] = {}
    starts: list[date] = []
    skipped: list[str] = []
    for path in sorted(directory.glob("*.yml")):
        document = read_yaml(path)
        if not isinstance(document, dict):
            raise UniverseCoverageError(
                f"universe definition {path.name} must be a YAML mapping"
            )
        if document.get("enabled", True) is False:
            skipped.append(path.name)
            continue
        payload = {
            key: value for key, value in document.items() if key != "enabled"
        }
        try:
            definition = UniverseDefinition.model_validate(payload)
        except ValidationError as error:
            raise UniverseCoverageError(
                f"enabled universe definition {path.name} does not validate: "
                f"{error.error_count()} error(s)"
            ) from None
        if definition.universe_id in hashes:
            raise UniverseCoverageError(
                f"duplicate universe_id {definition.universe_id!r} in {directory}"
            )
        hashes[definition.universe_id] = definition.version
        starts.append(definition.coverage_start)
    return UniverseCoverageCriterion(
        acceptance_start=min(starts) if starts else None,
        definition_hashes=dict(sorted(hashes.items())),
        skipped=tuple(skipped),
    )


def load_universe_definition(path: str | Path) -> UniverseDefinition:
    """Load and validate a universe definition from a YAML document.

    Placeholder values cannot pass validation: formal runs require a
    definition pinned to real hashes and the dataset's actual coverage.
    """
    path = Path(path)
    document = read_yaml(path)
    if not isinstance(document, dict):
        raise ValueError(f"universe definition {path} must be a YAML mapping")
    return UniverseDefinition.model_validate(document)


def resolve_versioned_universe_definition(
    configs_root: str | Path, universe_version: str
) -> UniverseDefinition:
    """Resolve an explicit universe_version from the immutable registry.

    The entry must validate and its canonical-JSON version must equal the
    file name's version (content re-check); only ``CURRENT`` specs resolve
    the top-level file (spec 7.0.4).
    """
    entry = Path(configs_root) / "versions" / f"{universe_version}.yml"
    if not entry.is_file():
        raise UniverseCoverageError(
            f"universe_version {universe_version!r} has no registry entry "
            f"under {entry.parent}; only CURRENT resolves the top-level file")
    definition = load_universe_definition(entry)
    if definition.version != universe_version:
        raise UniverseCoverageError(
            f"registry entry {entry.name} content hash {definition.version} "
            f"does not match its requested version {universe_version!r}")
    return definition


def definition_slice_hash(
    facts: Sequence[MembershipFact | Mapping[str, Any]],
    definition: UniverseDefinition,
) -> str:
    """The schema-v2 table hash of one definition's slice of the facts.

    A definition whose universe has no rows in the target dataset has no
    legitimate v2 evidence: refuse to hash the empty slice instead of
    minting a schema-v2 definition over nothing (spec 7.0.4 keeps such
    universes on their schema-v1 definitions).
    """
    rows = [
        item for item in facts
        if (item.universe_id if isinstance(item, MembershipFact)
            else str(item["universe_id"])) == definition.universe_id
    ]
    if not rows:
        raise ValueError(
            f"universe {definition.universe_id!r} has no membership slice in "
            "the target dataset; refusing to hash an empty slice as v2 "
            "evidence (keep the schema-v1 definition)")
    return membership_slice_hash(rows, definition.universe_id)


def archive_universe_definition(directory: str | Path, name: str) -> Path:
    """Move a top-level definition into ``archive/`` when the criterion holds.

    A definition that stays schema-v1 (no slice in the target dataset) may
    leave the enabled set only without changing the publication criterion:
    the minimum ``coverage_start`` over the remaining enabled definitions
    must equal the previous one.  Otherwise the move is rolled back and
    rejected, so the criterion's acceptance start never silently rises.
    """
    directory = Path(directory)
    source = directory / name
    if not source.is_file():
        raise UniverseCoverageError(
            f"universe definition {name!r} does not exist under {directory}")
    before = load_universe_coverage_criterion(directory).acceptance_start
    archive_dir = directory / "archive"
    archive_dir.mkdir(exist_ok=True)
    target = archive_dir / name
    source.replace(target)
    after = load_universe_coverage_criterion(directory).acceptance_start
    if after != before:
        target.replace(source)
        raise UniverseCoverageError(
            f"archiving {name!r} would raise the acceptance start "
            f"from {before} to {after}; the definition stays enabled")
    return target
