"""The three frozen research snapshots and the snapshot-bound identity bundle.

Before an experiment identity exists, the runner freezes three immutable
snapshots whose canonical JSON SHA-256 bind every research-relevant input:

- ``StrategySnapshot`` -- strategy/factor/portfolio versions, the rebalance
  frequency and the strategy parameter hash;
- ``ExperimentSnapshot`` -- the universe and corporate-action content hashes,
  the ordered predeclared cost scenarios, both frozen policies and the
  content hashes of the execution configs those scenarios resolve to;
- ``DataEnvironmentSnapshot`` -- the price, fundamental and calendar table
  content hashes; ``fundamental_version`` is the exact sentinel ``NOT_USED``
  when the strategy consumes no fundamentals (phase one: always).

Mappings are sorted and duplicate cost scenarios are rejected, so identical
inputs always render identical canonical bytes regardless of authoring order.
:class:`SnapshotBundle` carries each full snapshot object next to its
recomputed SHA-256 and refuses any bundle whose supplied hash disagrees with
its content (or that carries any undeclared, runtime-only field such as a
worker count).  ``build_snapshot_bundle`` is the single deterministic
constructor used by the research runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from stock_quant.research.walk_forward.policy import (
    StabilityPolicy,
    WalkForwardPolicy,
    canonical_sha256,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stock_quant.research.spec import ExperimentSpec

#: Exact sentinel for ``DataEnvironmentSnapshot.fundamental_version`` when the
#: strategy consumes no fundamental data.  A permissive placeholder would
#: fabricate a data dependency just to fill the snapshot.
NOT_USED = "NOT_USED"

#: Dataset tables whose combined content hash is the frozen price basis
#: (execution prices plus the point-in-time total-return factor input).
_PRICE_TABLES = ("adjusted_bar", "daily_bar")

#: Dataset tables whose combined content hash is the frozen corporate-action
#: evidence (the facts and the coverage evidence that makes them trusted).
_CORPORATE_ACTION_TABLES = ("corporate_action", "corporate_action_coverage")

_SHA256_HEX: str = "0123456789abcdef"


def _table_sha256(dataset_manifest: Mapping, name: str) -> str:
    """One pinned dataset table's content hash from its published manifest."""
    tables = dataset_manifest.get("tables")
    if not isinstance(tables, Mapping) or name not in tables:
        raise ValueError(
            "dataset manifest has no pinned content hash for table "
            f"{name!r}; a walk-forward snapshot requires the published "
            "dataset_manifest.json tables block"
        )
    digest = str(tables[name].get("sha256", ""))
    if len(digest) != 64 or set(digest) - set(_SHA256_HEX):
        raise ValueError(f"dataset table {name!r} sha256 is not 64 hex: {digest!r}")
    return digest


def _combined_table_hash(dataset_manifest: Mapping, names: tuple[str, ...]) -> str:
    """Deterministic content hash over an ordered set of dataset tables."""
    return canonical_sha256({name: _table_sha256(dataset_manifest, name)
                             for name in names})


class _SnapshotBase(BaseModel):
    """Shared strict/frozen configuration and the canonical content hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def content_hash(self) -> str:
        """SHA-256 of this snapshot's canonical JSON payload."""
        return canonical_sha256(self.model_dump(mode="json"))


class StrategySnapshot(_SnapshotBase):
    """The frozen strategy definition: versions, frequency, parameters."""

    strategy_version: str
    factor_versions: dict[str, str]
    portfolio_rule_version: str
    rebalance_frequency: str
    parameters_hash: str

    @field_validator("factor_versions", mode="after")
    @classmethod
    def _sort_versions(cls, value: dict[str, str]) -> dict[str, str]:
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def _check_fields(self) -> "StrategySnapshot":
        if not self.strategy_version.strip():
            raise ValueError("strategy_version must be non-empty")
        if not self.factor_versions:
            raise ValueError("factor_versions must name at least one factor")
        if any(not name.strip() or not version.strip()
               for name, version in self.factor_versions.items()):
            raise ValueError("factor names and versions must be non-empty")
        for field in ("portfolio_rule_version", "rebalance_frequency",
                      "parameters_hash"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} must be non-empty")
        return self


class ExperimentSnapshot(_SnapshotBase):
    """The frozen experiment frame: universe, costs, policies, config hashes."""

    universe_version: str
    corporate_action_version: str
    cost_scenarios: tuple[str, ...]
    walk_forward_policy: WalkForwardPolicy
    stability_policy: StabilityPolicy
    #: Content hashes of the frozen execution configs the declared cost
    #: scenarios resolve to (e.g. ``costs.yml``/``trading_rules.yml``), so a
    #: change of fee or slippage *content* re-keys the experiment snapshot
    #: even when the scenario names are unchanged.
    config_hashes: dict[str, str]

    @field_validator("cost_scenarios", mode="after")
    @classmethod
    def _check_scenarios(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("cost_scenarios must name at least one scenario")
        if len(set(value)) != len(value):
            raise ValueError("duplicate cost scenario in the snapshot")
        if any(not scenario.strip() for scenario in value):
            raise ValueError("cost scenario names must be non-empty")
        return value

    @field_validator("config_hashes", mode="after")
    @classmethod
    def _sort_config_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def _check_fields(self) -> "ExperimentSnapshot":
        if not self.universe_version.strip():
            raise ValueError("universe_version must be non-empty")
        if not self.corporate_action_version.strip():
            raise ValueError("corporate_action_version must be non-empty")
        if any(not name.strip() or not digest.strip()
               for name, digest in self.config_hashes.items()):
            raise ValueError("config hash names and values must be non-empty")
        return self


class DataEnvironmentSnapshot(_SnapshotBase):
    """The frozen data environment: price, fundamental and calendar hashes."""

    price_version: str
    fundamental_version: str
    calendar_version: str

    @model_validator(mode="after")
    def _check_fields(self) -> "DataEnvironmentSnapshot":
        for field in ("price_version", "fundamental_version", "calendar_version"):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} must be non-empty")
        if self.fundamental_version != NOT_USED:
            raise ValueError(
                "fundamental_version must be the exact NOT_USED sentinel while "
                "no strategy consumes fundamentals; do not introduce an "
                "unused data dependency to fill the snapshot"
            )
        return self


class SnapshotBundle(BaseModel):
    """Three full snapshots beside their recomputed canonical SHA-256 hashes.

    Validation recomputes every hash from the payload it accompanies, so a
    bundle whose declared hash disagrees with its content can never be
    constructed, and any undeclared runtime field (paths, timestamps, host,
    pid, worker count) is rejected as an ``extra`` field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_snapshot: StrategySnapshot
    experiment_snapshot: ExperimentSnapshot
    data_environment_snapshot: DataEnvironmentSnapshot
    strategy_hash: str
    experiment_hash: str
    data_environment_hash: str

    @model_validator(mode="after")
    def _verify_hashes(self) -> "SnapshotBundle":
        declared = {
            "strategy_hash": (self.strategy_snapshot, self.strategy_hash),
            "experiment_hash": (self.experiment_snapshot, self.experiment_hash),
            "data_environment_hash": (
                self.data_environment_snapshot,
                self.data_environment_hash,
            ),
        }
        for field, (payload, supplied) in declared.items():
            actual = payload.content_hash
            if actual != supplied:
                raise ValueError(
                    f"{field} {supplied!r} does not match the snapshot content "
                    f"hash {actual!r}; snapshots are hashed, never trusted"
                )
        return self

    @classmethod
    def of(
        cls,
        *,
        strategy_snapshot: StrategySnapshot,
        experiment_snapshot: ExperimentSnapshot,
        data_environment_snapshot: DataEnvironmentSnapshot,
    ) -> "SnapshotBundle":
        """Build a bundle, computing each snapshot's hash from its content."""
        return cls(
            strategy_snapshot=strategy_snapshot,
            experiment_snapshot=experiment_snapshot,
            data_environment_snapshot=data_environment_snapshot,
            strategy_hash=strategy_snapshot.content_hash,
            experiment_hash=experiment_snapshot.content_hash,
            data_environment_hash=data_environment_snapshot.content_hash,
        )

    @property
    def hashes(self) -> dict[str, str]:
        """The three snapshot hashes as a sorted, JSON-ready mapping."""
        return {
            "data_environment_hash": self.data_environment_hash,
            "experiment_hash": self.experiment_hash,
            "strategy_hash": self.strategy_hash,
        }


def build_snapshot_bundle(
    *,
    spec: ExperimentSpec,
    dataset_manifest: Mapping,
    universe_definition: object | None = None,
    config_hashes: Mapping[str, str],
) -> SnapshotBundle:
    """Freeze the three snapshots from one frozen spec and its pinned inputs.

    ``dataset_manifest`` is the published dataset manifest (its ``tables``
    block carries each table's content hash); ``universe_definition`` is the
    frozen :class:`~stock_quant.research.universe.UniverseDefinition` when the
    run is definition-backed, else ``None`` (the legacy engineering universe
    keeps the spec's resolved ``universe_version``); ``config_hashes`` are the
    content hashes of the consumed execution configs.
    """
    # Local import: spec.py imports SnapshotBundle for identity, so a
    # module-level import would be circular.
    from stock_quant.research.spec import ExperimentSpec

    if not isinstance(spec, ExperimentSpec):
        raise TypeError(
            "build_snapshot_bundle expects an ExperimentSpec, got "
            f"{type(spec).__name__}"
        )
    universe_version = getattr(universe_definition, "version", None)
    if universe_version is None:
        universe_version = spec.universe_version
    config_hashes = {str(name): str(digest)
                     for name, digest in dict(config_hashes).items()}
    strategy = StrategySnapshot(
        strategy_version=spec.code_commit,
        factor_versions=dict(spec.factor_versions),
        portfolio_rule_version=canonical_sha256(
            spec.portfolio_rule.model_dump(mode="json")
        ),
        rebalance_frequency="weekly",
        parameters_hash=canonical_sha256({
            "preprocessing": spec.preprocessing.model_dump(mode="json"),
            "portfolio_rule": spec.portfolio_rule.model_dump(mode="json"),
            "random_seed": spec.random_seed,
        }),
    )
    experiment = ExperimentSnapshot(
        universe_version=universe_version,
        corporate_action_version=_combined_table_hash(
            dataset_manifest, _CORPORATE_ACTION_TABLES
        ),
        cost_scenarios=tuple(spec.cost_scenarios),
        walk_forward_policy=WalkForwardPolicy(),
        stability_policy=StabilityPolicy(),
        config_hashes=config_hashes,
    )
    data_environment = DataEnvironmentSnapshot(
        price_version=_combined_table_hash(dataset_manifest, _PRICE_TABLES),
        fundamental_version=NOT_USED,
        calendar_version=_table_sha256(dataset_manifest, "trading_calendar"),
    )
    return SnapshotBundle.of(
        strategy_snapshot=strategy,
        experiment_snapshot=experiment,
        data_environment_snapshot=data_environment,
    )
