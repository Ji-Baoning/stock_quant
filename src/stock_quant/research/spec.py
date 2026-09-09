"""Strict experiment specifications and their deterministic canonical identity.

Every official study is described by an immutable :class:`ExperimentSpec`
(Pydantic, ``extra="forbid"``).  A spec requests a data set by an *explicit*
``dataset_version``/``universe_version`` string; the reserved token
``CURRENT`` is allowed only as a pre-freeze placeholder.  The research runner
resolves ``CURRENT`` once against the data layer and writes the explicit
resolved version into the *frozen* spec via :meth:`ExperimentSpec.freeze`, so a
stored spec never references the token.

A formal spec additionally names a frozen universe ``universe_definition``
(e.g. ``csi300``): the runner validates that definition against the pinned
dataset's membership evidence *before* the identity is computed, then freezes
``universe_version`` to the definition's content-derived version.  A spec
without a ``universe_definition`` keeps the legacy engineering
``configs/universe.yml`` resolution path.

Identity (:func:`compute_experiment_id`) hashes the frozen spec with
RFC-8785-style canonical JSON semantics implemented as sorted UTF-8 JSON with
compact separators.  Dates are already normalized to ISO strings by
``model_dump(mode="json")``; ``allow_nan=False`` refuses any non-finite float.
Every field of the spec is hashed, so code and data versions plus every input
that can affect execution change the id, and ``model_copy(deep=True)`` of the
same spec hashes identically.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stock_quant.research.trust import DataTrustMode

#: Reserved dataset/universe version placeholder; resolved to an explicit
#: version by :meth:`ExperimentSpec.freeze` before an identity may be computed.
_CURRENT = "CURRENT"

#: Scheme version embedded in the canonical hash payload so a future change of
#: the serialization semantics deliberately invalidates all prior identities.
_IDENTITY_SCHEME_VERSION = 1


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


class PortfolioRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["top_n_equal_weight"] = "top_n_equal_weight"
    top_n: int = Field(default=10, ge=1, le=500)
    lot_size: int = Field(default=100, ge=100, multiple_of=100)


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
    date_range: DateRange
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
        """True when both data versions are explicit (not the ``CURRENT`` token)."""
        return (
            self.dataset_version != _CURRENT and self.universe_version != _CURRENT
        )

    def freeze(
        self,
        *,
        dataset_version: str | None = None,
        universe_version: str | None = None,
        code_commit: str | None = None,
        trust_mode: DataTrustMode | None = None,
    ) -> "ExperimentSpec":
        """Return an explicit-version copy, resolving any ``CURRENT`` placeholder.

        Resolution happens exactly here: every version the spec keeps must be
        explicit, so a requested ``CURRENT`` with no supplied explicit version
        is an error rather than a silently unresolved spec.  Supplying an
        explicit version for an already-explicit field overrides it.  The
        corporate-action ``trust_mode`` is pinned the same way, so a stored
        frozen spec never loses which evidence bar the run applied.
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
        if code_commit is not None:
            updates["code_commit"] = code_commit
        if trust_mode is not None:
            updates["trust_mode"] = trust_mode
        if not updates:
            return self
        return self.model_copy(update=updates)


def load_experiment_spec(path: str | Path) -> ExperimentSpec:
    """Strictly parse one experiment-spec YAML file into an ``ExperimentSpec``.

    Extra top-level keys are rejected by the model.  The returned spec may
    still request ``CURRENT``; call :meth:`ExperimentSpec.freeze` with the
    versions the runner pinned before computing an identity.
    """
    spec_path = Path(path)
    raw = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{spec_path} must contain a YAML mapping, got {type(raw).__name__}"
        )
    return ExperimentSpec.model_validate(raw)


def compute_experiment_id(spec: ExperimentSpec) -> str:
    """Deterministic sha256 of the frozen spec under canonical JSON semantics.

    Raises :class:`ExperimentNotFrozenError` if the spec still references
    ``CURRENT``; an identity only exists for an explicit-version spec.
    """
    if not isinstance(spec, ExperimentSpec):
        raise TypeError(
            "compute_experiment_id expects an ExperimentSpec, got "
            f"{type(spec).__name__}"
        )
    if not spec.is_frozen:
        raise ExperimentNotFrozenError(
            "a spec that still requests CURRENT has no identity; resolve "
            "dataset_version and universe_version with ExperimentSpec.freeze() "
            "before computing an experiment_id"
        )
    payload = {
        "experiment_spec_scheme_version": _IDENTITY_SCHEME_VERSION,
        "experiment_spec": spec.model_dump(mode="json"),
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
