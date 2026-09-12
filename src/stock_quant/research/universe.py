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
from datetime import date
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from stock_quant.data_model.universe_membership import (
    MembershipFact,
    ResolvedMembership,
    membership_content_hash,
)

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


class UniverseDefinition(BaseModel):
    """One frozen, auditable version of an index universe.

    The definition pins the exact membership-table content it was built from
    (``membership_table_sha256``), the rules that produced it, the actual
    covered window (never the aspirational target) and the evidence summary.
    Its canonical-JSON SHA-256 (``version``) enters the experiment identity,
    so any content change yields a new version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    universe_id: str
    rules_version: str
    membership_table_sha256: str
    coverage_start: date
    coverage_end: date
    evidence_summary_sha256: str

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

    @model_validator(mode="after")
    def _check_coverage(self) -> "UniverseDefinition":
        if self.coverage_start > self.coverage_end:
            raise ValueError(
                "coverage_start must not follow coverage_end: "
                f"{self.coverage_start} > {self.coverage_end}"
            )
        return self

    @property
    def version(self) -> str:
        """SHA-256 of the definition's canonical JSON; the universe version."""
        return canonical_json_sha256(self.model_dump(mode="json"))


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
        table_hash = membership_content_hash(list(facts))
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
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
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
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"universe definition {path} must be a YAML mapping")
    return UniverseDefinition.model_validate(document)
