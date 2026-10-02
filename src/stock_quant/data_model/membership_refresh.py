"""Crash-consistent membership refresh state machine (spec 7.0.10, ADR-024).

The owner ruling for §7.0.10 is protocol **B (Iceberg-style single pointer)**:
``data/.membership_generation.json`` is the *single authoritative pointer*
and one atomic ``os.replace`` is the whole commit.  ``CURRENT`` and the
top-level universe definition are derived caches that the commit rewrites
best-effort after the pointer swap, so an interrupted commit can never tear
the project -- it can only leave a cache lagging, and the next
:func:`verify_generation` heals it from the immutable registry instead of
raising.  Consistency is never guessed: the authoritative artifacts (the
generation's dataset directory and its ``configs/universes/versions/``
registry entry) must exist and re-hash to the recorded generation, or the
failure is a stable :class:`MembershipRefreshError` code that the operator
resolves with an explicit ``data index-membership recover --to`` choice.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from stock_quant.data_model.dataset import (
    DatasetNotFoundError,
    DatasetPublisher,
    DatasetReader,
)
from stock_quant.data_model.schemas import UNIVERSE_MEMBERSHIP_COLUMNS
from stock_quant.data_model.universe_membership import (
    MembershipFact,
    membership_slice_hash,
)
from stock_quant.data_quality.models import QualityReport, Severity
from stock_quant.data_quality.raw_checks import validate_membership_facts
from stock_quant.research.universe import (
    MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID,
    MembershipCoverageGap,
    MembershipCoverageSegment,
    UniverseDefinition,
    load_universe_definition,
)

#: ADR-024 owner ruling: protocol B.  The alternative dual_replace state
#: machine was deleted at the ruling, so the two paths cannot coexist.
COMMIT_PROTOCOL = "single_pointer"

GENERATION_STATE_RELATIVE = Path("data") / ".membership_generation.json"


class MembershipRefreshError(RuntimeError):
    """A refresh failure carrying its stable operator-facing ``error_code``."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class Generation(BaseModel):
    """One committed visibility generation of the membership dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_version: str
    definition_name: str
    definition_version: str
    committed_at: str


class GenerationState(BaseModel):
    """The authoritative pointer: the active generation plus its history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    active: Generation
    history: tuple[Generation, ...] = ()


def read_generation_state(project_root: Path) -> GenerationState | None:
    """Parse the generation pointer, or ``None`` before the first commit."""
    path = Path(project_root) / GENERATION_STATE_RELATIVE
    if not path.exists():
        return None
    try:
        return GenerationState.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, ValidationError, OSError) as error:
        raise MembershipRefreshError(
            f"{path} is malformed; refusing to guess (spec 7.0.10)",
            error_code="membership_generation_state_malformed",
        ) from error


def verify_generation(project_root: Path) -> GenerationState | None:
    """Fail closed on authoritative disagreement; heal lagging caches.

    Protocol B (ADR-024): the generation pointer is authoritative, so
    verification only demands that the recorded generation is resolvable --
    its dataset directory and registry entry must exist and the entry must
    still load to its recorded version.  ``CURRENT`` and the top-level
    definition are derived caches: when they lag the generation (a crash
    between the pointer swap and the cache rewrite) they are rewritten from
    the authoritative artifacts instead of raising, so a reader that verifies
    first never consumes a stale cache.
    """
    project_root = Path(project_root)
    state = read_generation_state(project_root)
    if state is None:
        return None
    generation = state.active
    dataset_dir = project_root / "data" / "standardized" / generation.dataset_version
    entry = (
        project_root
        / "configs"
        / "universes"
        / "versions"
        / f"{generation.definition_version}.yml"
    )
    if not dataset_dir.is_dir() or not entry.is_file():
        raise MembershipRefreshError(
            "membership generation state names artifacts missing on disk: "
            f"dataset={generation.dataset_version} entry={entry.name}",
            error_code="membership_generation_inconsistent",
        )
    if load_universe_definition(entry).version != generation.definition_version:
        raise MembershipRefreshError(
            f"registry entry {entry.name} no longer hashes to its version",
            error_code="membership_generation_inconsistent",
        )
    # Derived caches below: lag is healed, never trusted and never fatal.
    definition_file = (
        project_root / "configs" / "universes" / f"{generation.definition_name}.yml"
    )
    try:
        lagging = (
            load_universe_definition(definition_file).version
            != generation.definition_version
        )
    except Exception:  # noqa: BLE001 - a corrupt cache is rewritten, not trusted
        lagging = True
    if lagging:
        _install_definition(project_root, generation)
    publisher = DatasetPublisher(project_root)
    try:
        lagging = publisher.current().version != generation.dataset_version
    except DatasetNotFoundError:
        lagging = True
    if lagging:
        publisher.promote(generation.dataset_version)
    return state


def commit_steps(
    project_root: Path, generation: Generation
) -> Iterator[Callable[[], None]]:
    """The visibility promotions of one commit, in order (spec 7.0.10).

    Protocol B commits in exactly one step -- the atomic generation pointer
    swap -- and then rewrites the derived caches (``CURRENT``, top-level
    definition) best-effort.  Re-committing the content that is already the
    active generation is a no-op that only re-heals the caches: a rerun must
    not churn history or ``committed_at``.
    """
    previous = read_generation_state(project_root)
    if previous is not None and (
        previous.active.dataset_version == generation.dataset_version
        and previous.active.definition_version == generation.definition_version
    ):
        _heal_caches(project_root, generation)
        return

    state = GenerationState(
        active=generation,
        history=(previous.active, *previous.history) if previous else (),
    )

    def write_state() -> None:
        _write_state(project_root, state)

    def replace_current() -> None:
        DatasetPublisher(project_root).promote(generation.dataset_version)

    def replace_definition() -> None:
        _install_definition(project_root, generation)

    yield write_state
    replace_current()
    replace_definition()


def commit_refresh(project_root: Path, generation: Generation) -> None:
    """Run every commit step of :func:`commit_steps` to completion."""
    for step in commit_steps(project_root, generation):
        step()


def recover_refresh(project_root: Path, definition_version: str) -> Generation:
    """Converge to a self-consistent generation chosen by the operator."""
    state = read_generation_state(project_root)
    if state is None:
        raise MembershipRefreshError(
            "no generation state to recover from",
            error_code="membership_generation_state_malformed",
        )
    target = next(
        (
            generation
            for generation in (state.active, *state.history)
            if generation.definition_version == definition_version
        ),
        None,
    )
    if target is None:
        raise MembershipRefreshError(
            f"generation {definition_version!r} is not recorded in "
            f"{GENERATION_STATE_RELATIVE}; refusing to guess",
            error_code="membership_generation_inconsistent",
        )
    commit_refresh(project_root, target)
    return target


def prepare_refresh(
    project_root: Path,
    *,
    universe_id: str,
    definition_name: str,
    prepared_frame: pd.DataFrame,
    rules_version: str,
    evidence_summary_sha256: str,
    segments: Sequence[MembershipCoverageSegment | Mapping[str, Any]] = (),
    gaps: Sequence[MembershipCoverageGap | Mapping[str, Any]] = (),
) -> Generation:
    """Publish one universe's replacement slice without promoting anything.

    The current dataset is opened through ``CURRENT``, every other slice is
    carried unchanged, the merged table must pass the unchanged
    ``validate_membership_facts`` gate without a FATAL issue, the frozen
    schema-v2 definition pins the slice hash, and the new dataset is parked
    with ``promote=False``.  Visibility changes only in
    :func:`commit_refresh`: the registry is appended (idempotent) here, but
    ``CURRENT``, the top-level definition and the generation pointer are
    untouched until the commit.
    """
    project_root = Path(project_root)
    verify_generation(project_root)
    publisher = DatasetPublisher(project_root)
    current = publisher.current()
    with DatasetReader(project_root).open(current.version) as context:
        tables = {name: context.read(name) for name in context.tables}
        # The calendar evidence in the baseline manifest describes the carried
        # tables, which this refresh carries unchanged; dropping it would make
        # the parked version fail validation with calendar_coverage_missing.
        carried_config = (
            context.manifest.get("build_config")
            if isinstance(context.manifest, Mapping)
            else None
        )
    baseline = tables.get("universe_membership")
    if baseline is None:
        baseline = pd.DataFrame(columns=list(UNIVERSE_MEMBERSHIP_COLUMNS))
    missing = [
        column for column in UNIVERSE_MEMBERSHIP_COLUMNS
        if column not in prepared_frame.columns
    ]
    if missing:
        raise MembershipRefreshError(
            "prepared frame is missing canonical membership columns: "
            + ", ".join(missing),
            error_code="membership_prepared_frame_invalid",
        )

    merged = _replace_slice(baseline, prepared_frame, universe_id)
    tables["universe_membership"] = merged
    issues = validate_membership_facts(
        merged, calendar=_open_days(tables.get("trading_calendar")), expected_sizes={}
    )
    fatal_codes = sorted(
        {issue.code for issue in issues if issue.severity is Severity.FATAL}
    )
    if fatal_codes:
        raise MembershipRefreshError(
            "prepared membership facts rejected by the acceptance validator: "
            + ", ".join(fatal_codes),
            error_code="membership_facts_rejected",
        )

    slice_facts = _facts_from_membership_frame(
        merged[merged["universe_id"] == universe_id]
    )
    if not slice_facts:
        raise MembershipRefreshError(
            f"prepared frame has no rows for universe {universe_id!r}; "
            "refusing to mint an empty slice as v2 evidence (spec 7.0.4)",
            error_code="membership_prepared_frame_invalid",
        )
    definition = _build_definition(
        universe_id=universe_id,
        rules_version=rules_version,
        evidence_summary_sha256=evidence_summary_sha256,
        slice_facts=slice_facts,
        segments=segments,
        gaps=gaps,
    )
    published = publisher.publish(
        tables, QualityReport(), build_config=carried_config, promote=False
    )
    _append_registry_entry(project_root, definition)
    return Generation(
        dataset_version=published.version,
        definition_name=definition_name,
        definition_version=definition.version,
        committed_at=_utc_now_iso(),
    )


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _write_state(project_root: Path, state: GenerationState) -> None:
    path = Path(project_root) / GENERATION_STATE_RELATIVE
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(state.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heal_caches(project_root: Path, generation: Generation) -> None:
    """Rewrite both derived caches to the generation (idempotent)."""
    publisher = DatasetPublisher(project_root)
    try:
        lagging = publisher.current().version != generation.dataset_version
    except DatasetNotFoundError:
        lagging = True
    if lagging:
        publisher.promote(generation.dataset_version)
    _install_definition(project_root, generation)


def _install_definition(project_root: Path, generation: Generation) -> None:
    """Rewrite the top-level definition from its immutable registry entry.

    The registry bytes are copied whole through a temporary file and one
    ``os.replace`` so a loader never observes a partial definition.
    """
    entry = (
        Path(project_root)
        / "configs"
        / "universes"
        / "versions"
        / f"{generation.definition_version}.yml"
    )
    target = (
        Path(project_root)
        / "configs"
        / "universes"
        / f"{generation.definition_name}.yml"
    )
    payload = entry.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, target)


def _replace_slice(
    baseline: pd.DataFrame, prepared: pd.DataFrame, universe_id: str
) -> pd.DataFrame:
    """Swap one universe's slice; every other slice is carried untouched."""
    kept = baseline[baseline["universe_id"] != universe_id]
    merged = pd.concat([kept, prepared], ignore_index=True)
    merged = merged.sort_values(
        ["universe_id", "symbol", "raw_effective_from"], kind="stable"
    ).reset_index(drop=True)
    return merged[list(UNIVERSE_MEMBERSHIP_COLUMNS)]


def _open_days(calendar_table: pd.DataFrame | None) -> tuple[date, ...]:
    """The confirmed open days of the carried ``trading_calendar`` table."""
    if calendar_table is None or calendar_table.empty:
        return ()
    open_rows = calendar_table[calendar_table["is_trading_day"].astype(bool)]
    return tuple(
        sorted({pd.Timestamp(day).date() for day in open_rows["calendar_date"]})
    )


def _facts_from_membership_frame(frame: pd.DataFrame) -> list[MembershipFact]:
    """Rebuild validated facts from ``universe_membership`` rows.

    Local copy of the shared row-to-fact coercion: a breach raises, so a
    table that violates the fact contract can never enter a refresh.
    """
    facts: list[MembershipFact] = []
    for record in frame.to_dict("records"):
        payload = dict(record)
        for column in (
            "raw_effective_from",
            "raw_effective_to",
            "announcement_date",
        ):
            value = payload.get(column)
            payload[column] = (
                None
                if value is None or pd.isna(value)
                else pd.Timestamp(value).date()
            )
        facts.append(MembershipFact.model_validate(payload))
    return facts


def _build_definition(
    *,
    universe_id: str,
    rules_version: str,
    evidence_summary_sha256: str,
    slice_facts: list[MembershipFact],
    segments: Sequence[MembershipCoverageSegment | Mapping[str, Any]],
    gaps: Sequence[MembershipCoverageGap | Mapping[str, Any]],
) -> UniverseDefinition:
    """Mint the frozen schema-v2 definition for the replaced slice.

    Without explicit coverage segments the single segment spans the slice's
    smallest ``raw_effective_from`` through the slice's latest attested day.
    """
    starts = [fact.raw_effective_from for fact in slice_facts]
    ends = [
        fact.raw_effective_to
        for fact in slice_facts
        if fact.raw_effective_to is not None
    ]
    coverage_start = min(starts)
    coverage_end = max(ends + starts)
    coverage_segments = list(segments) or [
        MembershipCoverageSegment(
            start=coverage_start,
            end=coverage_end,
            evidence_sha256=evidence_summary_sha256,
        )
    ]
    try:
        return UniverseDefinition(
            schema_version=2,
            universe_id=universe_id,
            rules_version=rules_version,
            membership_table_sha256=membership_slice_hash(
                slice_facts, universe_id
            ),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            evidence_summary_sha256=evidence_summary_sha256,
            membership_hash_scope=MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID,
            coverage_segments=coverage_segments,
            coverage_gaps=list(gaps) or None,
        )
    except ValidationError as error:
        raise MembershipRefreshError(
            f"prepared coverage segments do not tile the slice envelope: {error}",
            error_code="membership_prepared_frame_invalid",
        ) from None


def _append_registry_entry(
    project_root: Path, definition: UniverseDefinition
) -> None:
    """Append the definition to the immutable registry (skip if present)."""
    registry = Path(project_root) / "configs" / "universes" / "versions"
    registry.mkdir(parents=True, exist_ok=True)
    entry = registry / f"{definition.version}.yml"
    if entry.exists():
        return
    temporary = entry.with_name(f".{entry.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        yaml.safe_dump(
            definition.model_dump(mode="json", exclude_none=True),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, entry)
