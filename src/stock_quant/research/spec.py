"""Strict experiment specifications and their deterministic canonical identity.

Every official study is described by an immutable :class:`ExperimentSpec`
(Pydantic, ``extra="forbid"``).  A spec requests a data set by an *explicit*
``dataset_version``/``universe_version`` string; the reserved token
``CURRENT`` is allowed only as a pre-freeze placeholder.  The research runner
resolves ``CURRENT`` once against the data layer and writes the explicit
resolved version into the *frozen* spec via :meth:`ExperimentSpec.freeze`, so a
stored spec never references the token.  The same holds for
``data_acceptance_id``: a spec requests the ``CURRENT_ACCEPTED`` placeholder
and the runner pins the resolved real-data acceptance record id before the
spec may freeze (a RESEARCH spec can never freeze without one; an
ENGINEERING diagnostic may freeze with ``None``).

A formal spec additionally names a frozen universe ``universe_definition``
(e.g. ``csi300``): the runner validates that definition against the pinned
dataset's membership evidence *before* the identity is computed, then freezes
``universe_version`` to the definition's content-derived version.  A spec
without a ``universe_definition`` keeps the legacy engineering
``configs/universe.yml`` resolution path.

Identity (:func:`compute_experiment_id`) hashes the frozen spec *together
with* the frozen :class:`~stock_quant.research.walk_forward.snapshots.
SnapshotBundle` -- all three full snapshot payloads (strategy, experiment,
data environment) and their three canonical SHA-256 hashes enter the identity
payload -- under RFC-8785-style canonical JSON semantics implemented as
sorted UTF-8 JSON with compact separators.  Dates are already normalized to
ISO strings by ``model_dump(mode="json")``; ``allow_nan=False`` refuses any
non-finite float.  Every field of the spec and of the snapshots is hashed, so
code and data versions, the pinned data acceptance, the frozen policies and
every other research-relevant input change the id, and
``model_copy(deep=True)`` of the same inputs hashes identically.  Runtime
metadata (run id, output paths, timestamps, host name, pid, worker count)
never enters the identity.  The identity scheme version was incremented to 2
when the snapshot payloads became part of the canonical payload, deliberately
invalidating every pre-snapshot identity.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_quant.portfolio.buffered_models import BufferedRiskWeightedPolicy
from stock_quant.research.acceptance.models import CURRENT_ACCEPTED
from stock_quant.research.trust import DataTrustMode
from stock_quant.research.walk_forward.snapshots import SnapshotBundle
from stock_quant.safe_yaml import read_yaml

#: Reserved dataset/universe version placeholder; resolved to an explicit
#: version by :meth:`ExperimentSpec.freeze` before an identity may be computed.
_CURRENT = "CURRENT"

#: Internal freeze() sentinel distinguishing "argument not supplied" from the
#: legitimate ``data_acceptance_id=None`` (an ENGINEERING diagnostic).
_ACCEPTANCE_UNSET = object()

#: Scheme version embedded in the canonical hash payload so a future change of
#: the serialization semantics deliberately invalidates all prior identities.
#: Version 2 added the three frozen snapshot payloads and hashes to the
#: canonical identity payload (a semantics change, not an extension).
_IDENTITY_SCHEME_VERSION = 2


class ExperimentNotFrozenError(ValueError):
    """A spec still requesting ``CURRENT`` cannot have a deterministic id."""


class DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "DateRange":
        if self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class Preprocessing(BaseModel):
    """Phase-one factor preprocessing. The MVP factor keeps raw==processed."""

    model_config = ConfigDict(extra="forbid")

    winsorization: Literal["none"] = "none"
    standardization: Literal["none"] = "none"


class EqualWeightPortfolioRule(BaseModel):
    """The legacy fixed-slot equal-weight rule (baseline / engineering specs)."""

    model_config = ConfigDict(extra="forbid")

    name: Literal["top_n_equal_weight"] = "top_n_equal_weight"
    top_n: int = Field(default=10, ge=1, le=500)
    lot_size: int = Field(default=100, ge=100, multiple_of=100)


class BufferedRiskWeightedPortfolioRule(BaseModel):
    """The pre-registered buffered, inverse-volatility-weighted rule.

    The rule carries exactly the frozen policy parameters; its canonical JSON
    content is the ``portfolio_rule_version`` and part of the strategy
    snapshot, so any explicit parameter change is a new strategy identity.
    Validation is delegated to :class:`~stock_quant.portfolio.buffered_models.
    BufferedRiskWeightedPolicy` -- one source of truth for the contract.
    """

    model_config = ConfigDict(extra="forbid")

    name: Literal["buffered_risk_weighted"] = "buffered_risk_weighted"
    target_count: int = Field(default=10, ge=1)
    entry_rank: int = Field(default=10, ge=1)
    hold_rank: int = Field(default=15, ge=1)
    risk_lookback_days: int = Field(default=60, ge=2)
    min_risk_observations: int = Field(default=40, ge=2)
    volatility_floor_annualized: Decimal = Decimal("0.10")
    max_single_weight: Decimal = Decimal("0.15")
    rebalance_band_absolute: Decimal = Decimal("0.02")
    gross_exposure: Decimal = Decimal("1.00")
    long_only: bool = True
    leverage: bool = False
    weight_quantum: Decimal = Decimal("0.000000000001")

    def policy(self) -> "BufferedRiskWeightedPolicy":
        """The frozen policy implied by this rule's parameters."""
        payload = {key: value for key, value in self.model_dump().items()
                   if key != "name"}
        return BufferedRiskWeightedPolicy.model_validate(payload)

    @model_validator(mode="after")
    def _enforce_policy_contract(self) -> "BufferedRiskWeightedPortfolioRule":
        self.policy()  # raises ValidationError on any contract breach
        return self


#: The discriminated portfolio-rule union: ``name`` selects the member, so a
#: buffered spec and an equal-weight spec are both strictly typed and both
#: hash their full canonical parameter content into the experiment identity.
PortfolioRule = Annotated[
    EqualWeightPortfolioRule | BufferedRiskWeightedPortfolioRule,
    Field(discriminator="name"),
]


class ExperimentSpec(BaseModel):
    """Strict, immutable description of one reproducible research study."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis: str
    factor_versions: dict[str, str]
    dataset_version: str
    universe_version: str
    #: The frozen universe definition a formal run resolves, e.g.
    #: ``csi300``: the runner loads
    #: ``configs/universes/<universe_definition>.yml``, validates its
    #: membership-table hash against the pinned dataset through the mandatory
    #: acceptance gate and freezes ``universe_version`` to the definition
    #: version.  ``None`` keeps the legacy engineering ``configs/universe.yml``
    #: path, which formal runs never take.
    universe_definition: str | None = None
    #: The real-data acceptance this study consumes.  ``CURRENT_ACCEPTED``
    #: requests the newest valid ACCEPTED record and is resolved by the runner
    #: before freezing; ``None`` is only ever valid for an ENGINEERING
    #: diagnostic (its performance decision stays UNTRUSTED).
    data_acceptance_id: str | None = CURRENT_ACCEPTED
    date_range: DateRange
    #: The explicit execution policy of this study.  The first-phase formal
    #: research pipeline is ``walk_forward_oos_v1`` (fixed-calendar annual
    #: OOS folds with a policy-bound stability conclusion); the legacy
    #: single-window engineering pipeline stays available only under its
    #: explicit ``engineering_single_window`` policy and can never publish a
    #: formal stability conclusion.
    execution_pipeline: Literal[
        "engineering_single_window", "walk_forward_oos_v1"
    ] = "engineering_single_window"
    train_validation_holdout_policy: Literal["not_applicable_engineering_mvp"]
    preprocessing: Preprocessing
    portfolio_rule: PortfolioRule
    cost_scenarios: list[str]
    random_seed: int
    code_commit: str = "unversioned"
    parent_experiment_ids: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    trust_mode: DataTrustMode = DataTrustMode.RESEARCH

    @model_validator(mode="after")
    def _validate_content(self) -> "ExperimentSpec":
        if not self.hypothesis.strip():
            raise ValueError("hypothesis must be non-empty")
        if not self.factor_versions:
            raise ValueError("factor_versions must name at least one factor")
        for name, version in self.factor_versions.items():
            if not name.strip() or not version.strip():
                raise ValueError("factor names and versions must be non-empty")
        if not self.dataset_version.strip() or not self.universe_version.strip():
            raise ValueError("dataset_version and universe_version must be non-empty")
        if self.universe_definition is not None and (
            not self.universe_definition.strip()
            or self.universe_definition != self.universe_definition.strip()
        ):
            raise ValueError(
                "universe_definition must be None or nonblank trimmed text: "
                f"{self.universe_definition!r}"
            )
        if not self.cost_scenarios:
            raise ValueError("cost_scenarios must name at least one scenario")
        if any(not scenario.strip() for scenario in self.cost_scenarios):
            raise ValueError("cost scenario names must be non-empty")
        if not self.code_commit.strip():
            raise ValueError("code_commit must be non-empty")
        if any(not parent.strip() for parent in self.parent_experiment_ids):
            raise ValueError("parent experiment ids must be non-empty")
        return self

    @property
    def is_frozen(self) -> bool:
        """True when both data versions and the acceptance id are explicit.

        A RESEARCH spec additionally requires a concrete (non-``None``)
        acceptance id: formal research without a pinned real-data acceptance
        has no identity and can never run.
        """
        versions_fixed = (
            self.dataset_version != _CURRENT and self.universe_version != _CURRENT
        )
        acceptance_fixed = self.data_acceptance_id != CURRENT_ACCEPTED
        if self.trust_mode is DataTrustMode.RESEARCH:
            acceptance_fixed = (
                acceptance_fixed and self.data_acceptance_id is not None
            )
        return versions_fixed and acceptance_fixed

    def freeze(
        self,
        *,
        dataset_version: str | None = None,
        universe_version: str | None = None,
        data_acceptance_id: str | None | object = _ACCEPTANCE_UNSET,
        code_commit: str | None = None,
        trust_mode: DataTrustMode | None = None,
    ) -> "ExperimentSpec":
        """Return an explicit-version copy, resolving any ``CURRENT`` placeholder.

        Resolution happens exactly here: every version the spec keeps must be
        explicit, so a requested ``CURRENT`` with no supplied explicit version
        is an error rather than a silently unresolved spec.  Supplying an
        explicit version for an already-explicit field overrides it.  The
        pinned ``data_acceptance_id`` resolves the same way (``None`` must be
        passed explicitly because it is a legitimate resolved value for an
        ENGINEERING diagnostic), and a RESEARCH spec can never freeze without
        an explicit acceptance id.  The corporate-action ``trust_mode`` is
        pinned the same way, so a stored frozen spec never loses which
        evidence bar the run applied.
        """
        updates: dict[str, object] = {}
        if dataset_version is not None:
            updates["dataset_version"] = dataset_version
        elif self.dataset_version == _CURRENT:
            raise ValueError(
                "dataset_version requests CURRENT but freeze() was not given "
                "an explicit dataset_version"
            )
        if universe_version is not None:
            updates["universe_version"] = universe_version
        elif self.universe_version == _CURRENT:
            raise ValueError(
                "universe_version requests CURRENT but freeze() was not given "
                "an explicit universe_version"
            )
        if data_acceptance_id is not _ACCEPTANCE_UNSET:
            updates["data_acceptance_id"] = data_acceptance_id
        elif self.data_acceptance_id == CURRENT_ACCEPTED:
            raise ValueError(
                "data_acceptance_id requests CURRENT_ACCEPTED but freeze() was "
                "not given a resolved acceptance id"
            )
        resolved_acceptance = updates.get(
            "data_acceptance_id", self.data_acceptance_id
        )
        resolved_mode = trust_mode or self.trust_mode
        if resolved_mode is DataTrustMode.RESEARCH and resolved_acceptance is None:
            raise ValueError(
                "research specs require an explicit data_acceptance_id"
            )
        if code_commit is not None:
            updates["code_commit"] = code_commit
        if trust_mode is not None:
            updates["trust_mode"] = trust_mode
        if not updates:
            return self
        return self.model_copy(update=updates)


def load_experiment_spec(path: str | Path) -> ExperimentSpec:
    """Strictly parse one experiment-spec YAML file into an ``ExperimentSpec``.

    Extra top-level keys are rejected by the model; a mapping key repeated
    anywhere in the document is rejected by the shared fail-closed YAML reader
    (:mod:`stock_quant.safe_yaml`), which YAML itself would otherwise let
    silently keep the last value.  The returned spec may still request
    ``CURRENT``; call :meth:`ExperimentSpec.freeze` with the versions the
    runner pinned before computing an identity.
    """
    spec_path = Path(path)
    raw = read_yaml(spec_path) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{spec_path} must contain a YAML mapping, got {type(raw).__name__}"
        )
    return ExperimentSpec.model_validate(raw)


def compute_experiment_id(
    spec: ExperimentSpec, snapshots: "SnapshotBundle"
) -> str:
    """Deterministic sha256 of the frozen spec plus the frozen snapshot bundle.

    Both full snapshot payloads and their three hashes enter the canonical
    identity payload (scheme version 2).  Raises
    :class:`ExperimentNotFrozenError` if the spec still references ``CURRENT``;
    an identity only exists for an explicit-version spec bound to a
    :class:`~stock_quant.research.walk_forward.snapshots.SnapshotBundle`.
    """
    if not isinstance(spec, ExperimentSpec):
        raise TypeError(
            "compute_experiment_id expects an ExperimentSpec, got "
            f"{type(spec).__name__}"
        )
    if not isinstance(snapshots, SnapshotBundle):
        raise TypeError(
            "compute_experiment_id expects a SnapshotBundle as its second "
            f"argument, got {type(snapshots).__name__}"
        )
    if not spec.is_frozen:
        raise ExperimentNotFrozenError(
            "a spec that still requests CURRENT or CURRENT_ACCEPTED has no "
            "identity; resolve dataset_version, universe_version and "
            "data_acceptance_id with ExperimentSpec.freeze() before computing "
            "an experiment_id"
        )
    payload = {
        "experiment_spec_scheme_version": _IDENTITY_SCHEME_VERSION,
        "experiment_spec": spec.model_dump(mode="json"),
        "snapshot_bundle": snapshots.model_dump(mode="json"),
        "snapshot_hashes": snapshots.hashes,
    }
    text = _canonical_json(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(value: object) -> str:
    """RFC-8785-style canonical JSON: sorted keys, compact UTF-8, no NaN/Inf."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
