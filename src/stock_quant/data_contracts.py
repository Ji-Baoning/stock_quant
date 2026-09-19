"""Table-level data-contract declarations (spec D2).

Loaded exclusively from the ``data_contracts`` section of
``configs/sources.yml``.  This module is the ONLY home of tier literals
under ``src/`` — enforced by tests/unit/test_tier_literals.py (spec §5
criterion 3): tier values enter the runtime only through the sources.yml
loading path, never as in-code constants elsewhere.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The three evidence tiers of spec D1 / §6 B1.
TIER_CORE = "core"
TIER_ANCHORED = "anchored"
TIER_RESEARCH_ONLY = "research_only"
TIERS = frozenset({TIER_CORE, TIER_ANCHORED, TIER_RESEARCH_ONLY})

#: Per-tier publication semantics for table-level blocking codes (spec §6 A1).
#: ``None`` is the fail-closed default: an undeclared table blocks.
TIER_BLOCKS_PUBLICATION: dict[str | None, bool] = {
    None: True,
    TIER_CORE: True,
    TIER_ANCHORED: False,
    TIER_RESEARCH_ONLY: False,
}

#: Transport-kind tokens (tushare_transport RELAY/OFFICIAL/PROXY values) —
#: ``primary_transport`` kind must be one of these, never free text.
TRANSPORT_KINDS = frozenset({"relay", "official", "proxy"})

INCREMENTAL_LAST_COVERED_PLUS_1 = "last_covered_plus_1"
INCREMENTAL_DISCLOSURE_CALENDAR = "disclosure_calendar"
INCREMENTAL_CHANGE_DRIVEN_FULL = "change_driven_full"
INCREMENTAL_STRATEGIES = frozenset(
    {
        INCREMENTAL_LAST_COVERED_PLUS_1,
        INCREMENTAL_DISCLOSURE_CALENDAR,
        INCREMENTAL_CHANGE_DRIVEN_FULL,
    }
)

CONFLICT_BLOCK = "block"
CONFLICT_DOWNGRADE = "downgrade"
CONFLICT_ARBITRATE = "arbitrate"
CONFLICT_MODES = frozenset({CONFLICT_BLOCK, CONFLICT_DOWNGRADE, CONFLICT_ARBITRATE})

#: First-version fact-row policy (spec D2, owner 约束 3: versioned rule name).
FACT_ROW_POLICY_MAX_REPORT_TYPE_V1 = "max_report_type_v1"
FACT_ROW_POLICIES = frozenset({FACT_ROW_POLICY_MAX_REPORT_TYPE_V1})

COVERAGE_SHAPE_PER_SYMBOL_WINDOW = "per_symbol_window"
COVERAGE_SHAPE_NONE = "none"
COVERAGE_SHAPES = frozenset({COVERAGE_SHAPE_PER_SYMBOL_WINDOW, COVERAGE_SHAPE_NONE})


class PitContract(BaseModel):
    """PIT fact-row selection contract (spec D2 ``pit`` block)."""

    model_config = ConfigDict(extra="forbid")

    as_of_field: str = Field(min_length=1)
    fallback: str = Field(min_length=1)
    fact_row_policy: Literal["max_report_type_v1"]


class DataContract(BaseModel):
    """One table's declaration; every field required (spec D2「缺一不得接入」)."""

    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1)
    tier: Literal["core", "anchored", "research_only"]
    primary_transport: str
    anchors: list[str] = Field(default_factory=list)
    conflict: Literal["block", "downgrade", "arbitrate"]
    pit: PitContract | None = None
    coverage_shape: Literal["per_symbol_window", "none"]
    incremental: Literal[
        "last_covered_plus_1", "disclosure_calendar", "change_driven_full"
    ]

    @model_validator(mode="after")
    def _transport_shape(self) -> "DataContract":
        # ``<source-name>:<transport-kind>``; the kind must be a transport
        # vocabulary token, not a source name (spec D2).
        if ":" not in self.primary_transport:
            raise ValueError(
                "primary_transport must be '<source>:<kind>', got "
                f"{self.primary_transport!r}"
            )
        kind = self.primary_transport.rsplit(":", 1)[1]
        if kind not in TRANSPORT_KINDS:
            raise ValueError(
                f"primary_transport kind {kind!r} is not a transport token"
            )
        return self

    @model_validator(mode="after")
    def _anchor_shape(self) -> "DataContract":
        # Anchored tables must name their independent anchors; core and
        # research_only tables must not (research_only means "no anchor").
        if self.tier == TIER_ANCHORED and not self.anchors:
            raise ValueError(
                f"anchored table {self.table!r} requires at least one anchor"
            )
        if self.tier != TIER_ANCHORED and self.anchors:
            raise ValueError(
                f"{self.tier} table {self.table!r} must not declare anchors"
            )
        return self


def parse_data_contracts(payload: object) -> dict[str, DataContract]:
    """Parse a ``data_contracts`` section; duplicate tables are rejected."""
    if payload is None:
        return {}
    if not isinstance(payload, list):
        raise ValueError("data_contracts must be a list")
    contracts: dict[str, DataContract] = {}
    for row in payload:
        contract = DataContract.model_validate(row)
        if contract.table in contracts:
            raise ValueError(f"duplicate data_contract table {contract.table!r}")
        contracts[contract.table] = contract
    return contracts
