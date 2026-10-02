"""Crash-consistent membership refresh (spec 7.0.10, ADR-024 protocol B).

The generation file ``data/.membership_generation.json`` is the single
authoritative pointer; ``CURRENT`` and the top-level universe definition are
derived caches that a lagging crash heals on the next verify, so under the
single-pointer protocol no interrupted commit can leave the project torn --
``TEARING[COMMIT_PROTOCOL]`` is empty by construction and only
``recover_refresh`` (driven by the operator's ``--to`` choice) ever replays a
cache rewrite from the immutable registry.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from stock_quant.cli import app
from stock_quant.data_model.dataset import (
    STANDARDIZED_SCHEMAS,
    DatasetPublisher,
    DatasetReader,
)
from stock_quant.data_model.membership_refresh import (
    COMMIT_PROTOCOL,
    MembershipRefreshError,
    _facts_from_membership_frame,
    commit_refresh,
    commit_steps,
    prepare_refresh,
    read_generation_state,
    recover_refresh,
    verify_generation,
)
from stock_quant.data_model.schemas import DAILY_COLUMNS
from stock_quant.data_model.universe_membership import (
    membership_content_hash,
    membership_frame,
    membership_slice_hash,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.research.acceptance.checks import slice_membership_frame
from stock_quant.research.universe import load_universe_definition

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Two universes share the baseline dataset so the refresh must replace one
#: slice and carry the other byte-for-byte (spec 7.0.10).
CSI300_ID = "csi300"
CUSTOM_ID = "custom_csi500_tw"
RULES_VERSION = "tushare-index-weight-monthly-v1"
EVIDENCE_SUMMARY = "cd" * 32
SNAPSHOT_SHA256 = "ef" * 32
DOCUMENT_SHA256 = "ab" * 32
CALENDAR_DAYS = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]
INGESTED = pd.Timestamp("2020-01-06T08:00:00Z")

# Only the single-pointer protocol exists (ADR-024): one commit step, so the
# parameter space of interruptible commits has no tearing point at all.
ORDER = {"single_pointer": ["state"]}
TEARING = {"single_pointer": []}


def _fact(universe_id: str, symbol: str, start: date, end: date | None = None):
    return {
        "universe_id": universe_id,
        "symbol": symbol,
        "raw_effective_from": start,
        "raw_effective_to": end,
        "announcement_date": start,
        "status": "active",
        "reason": "initial_constituent",
        "source": "csi_index_announcement",
        "source_url": "https://www.csindex.com.cn/announcement.pdf",
        "snapshot_sha256": SNAPSHOT_SHA256,
        "source_document_sha256": DOCUMENT_SHA256,
    }


def _daily_frame() -> pd.DataFrame:
    rows = []
    for day in CALENDAR_DAYS:
        for symbol, close in (("600000.SH", 10.5), ("000333.SZ", 30.25)):
            rows.append(
                {
                    "trade_date": pd.Timestamp(day),
                    "symbol": symbol,
                    "open": close - 0.5,
                    "high": close + 0.5,
                    "low": close - 0.6,
                    "close": close,
                    "volume": 1000,
                    "amount": close * 1000,
                    "adjustment": "unadjusted",
                    "source": "baostock",
                    "ingested_at": INGESTED,
                }
            )
    return pd.DataFrame(rows)[DAILY_COLUMNS]


@pytest.fixture(name="refresh_project")
def build_refresh_project(tmp_path: Path) -> SimpleNamespace:
    """A minimal project: published two-slice dataset + v1 definitions.

    The baseline dataset carries the canonical registry tables (mostly
    empty), a three-day ``daily_bar``/``trading_calendar`` pair and one
    evidence-backed membership slice per universe.  Both universes get a
    frozen schema-v1 top-level definition plus its immutable ``versions/``
    registry entry.  ``args`` are the keyword arguments of the replacement
    slice refresh: the prepared frame adds one constituent to ``CUSTOM_ID``.
    """
    root = tmp_path / "project"
    configs = root / "configs"
    (configs / "universes" / "versions").mkdir(parents=True)
    for name in ("project.yml", "sources.yml", "costs.yml"):
        (configs / name).write_bytes(
            (_REPO_ROOT / "templates" / "project-config" / name).read_bytes()
        )

    csi300_facts = [_fact(CSI300_ID, "600000.SH", date(2019, 12, 2))]
    custom_facts = [_fact(CUSTOM_ID, "000333.SZ", date(2019, 12, 2))]
    prepared_facts = custom_facts + [_fact(CUSTOM_ID, "000651.SZ", date(2020, 1, 2))]

    tables = {
        name: pd.DataFrame(columns=list(schema.names))
        for name, schema in STANDARDIZED_SCHEMAS.items()
    }
    tables["daily_bar"] = _daily_frame()
    tables["trading_calendar"] = pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(CALENDAR_DAYS),
            "is_trading_day": [True] * len(CALENDAR_DAYS),
        }
    )
    tables["universe_membership"] = membership_frame(csi300_facts + custom_facts)
    published = DatasetPublisher(root).publish(tables, QualityReport())

    whole_table_sha256 = membership_content_hash(csi300_facts + custom_facts)
    for universe_id in (CSI300_ID, CUSTOM_ID):
        text = yaml.safe_dump(
            {
                "universe_id": universe_id,
                "rules_version": RULES_VERSION,
                "membership_table_sha256": whole_table_sha256,
                "coverage_start": CALENDAR_DAYS[0].isoformat(),
                "coverage_end": CALENDAR_DAYS[-1].isoformat(),
                "evidence_summary_sha256": EVIDENCE_SUMMARY,
            },
            sort_keys=False,
            allow_unicode=True,
        )
        (configs / "universes" / f"{universe_id}.yml").write_text(
            text, encoding="utf-8"
        )
        version = load_universe_definition(
            configs / "universes" / f"{universe_id}.yml"
        ).version
        (configs / "universes" / "versions" / f"{version}.yml").write_text(
            text, encoding="utf-8"
        )

    return SimpleNamespace(
        root=root,
        args={
            "universe_id": CUSTOM_ID,
            "definition_name": CUSTOM_ID,
            "prepared_frame": membership_frame(prepared_facts),
            "rules_version": RULES_VERSION,
            "evidence_summary_sha256": EVIDENCE_SUMMARY,
        },
        version_before=published.version,
        csi300_facts=csi300_facts,
        custom_facts_before=custom_facts,
        prepared_facts=prepared_facts,
    )


def _run_commit(root: Path, generation, stop_after: str | None) -> None:
    """Drive ``commit_steps`` and stop after the named step (None = finish)."""
    steps = commit_steps(root, generation)
    for name in ORDER[COMMIT_PROTOCOL]:
        if stop_after == "prepare":
            return
        next(steps)()
        if name == stop_after:
            return
    for step in steps:  # drive the protocol tail to completion
        step()


@pytest.mark.parametrize("stop_after", ["prepare", *ORDER[COMMIT_PROTOCOL], None])
def test_every_crash_point_converges_to_a_self_consistent_generation(
    refresh_project, stop_after
):
    """No commit interruption leaves the project torn (protocol B)."""
    root = refresh_project.root
    generation = prepare_refresh(root, **refresh_project.args)
    _run_commit(root, generation, stop_after)
    if stop_after not in TEARING[COMMIT_PROTOCOL]:
        state = verify_generation(root)
        if state is not None:  # prepare: no state file yet, which is expected
            assert state.active.definition_version == generation.definition_version
        return
    with pytest.raises(MembershipRefreshError) as error:
        verify_generation(root)
    assert error.value.error_code == "membership_generation_inconsistent"
    recovered = recover_refresh(root, generation.definition_version)
    assert (
        verify_generation(root).active.definition_version
        == recovered.definition_version
    )


def test_prepare_appends_the_registry_but_promotes_nothing(refresh_project):
    """prepare parks the dataset and the definition; nothing visible moves."""
    root = refresh_project.root
    top = root / "configs" / "universes" / f"{CUSTOM_ID}.yml"
    top_before = top.read_bytes()
    versions_dir = root / "configs" / "universes" / "versions"
    versions_before = sorted(path.name for path in versions_dir.glob("*.yml"))

    generation = prepare_refresh(root, **refresh_project.args)

    assert DatasetPublisher(root).current().version == refresh_project.version_before
    assert top.read_bytes() == top_before
    assert (root / "data" / "standardized" / generation.dataset_version).is_dir()
    entry = versions_dir / f"{generation.definition_version}.yml"
    assert entry.is_file()
    assert load_universe_definition(entry).version == generation.definition_version
    assert sorted(path.name for path in versions_dir.glob("*.yml")) == [
        *versions_before,
        entry.name,
    ]
    assert read_generation_state(root) is None  # prepare never commits


def test_rerun_is_idempotent(refresh_project):
    """The same refresh twice: identical versions, byte-identical state."""
    root = refresh_project.root
    first = prepare_refresh(root, **refresh_project.args)
    commit_refresh(root, first)
    state_path = root / "data" / ".membership_generation.json"
    first_state = state_path.read_bytes()

    second = prepare_refresh(root, **refresh_project.args)
    commit_refresh(root, second)

    assert second.dataset_version == first.dataset_version
    assert second.definition_version == first.definition_version
    assert state_path.read_bytes() == first_state


def test_rollback_is_a_file_replace(refresh_project):
    """``recover --to`` converges to the operator-chosen older generation."""
    root = refresh_project.root
    first = prepare_refresh(root, **refresh_project.args)
    commit_refresh(root, first)

    second_args = dict(refresh_project.args)
    second_args["prepared_frame"] = membership_frame(
        refresh_project.prepared_facts
        + [_fact(CUSTOM_ID, "600519.SH", date(2020, 1, 6))]
    )
    second = prepare_refresh(root, **second_args)
    commit_refresh(root, second)
    assert second.definition_version != first.definition_version

    recovered = recover_refresh(root, first.definition_version)

    assert recovered.definition_version == first.definition_version
    entry = (
        root / "configs" / "universes" / "versions" / f"{first.definition_version}.yml"
    )
    top = root / "configs" / "universes" / f"{CUSTOM_ID}.yml"
    assert top.read_bytes() == entry.read_bytes()
    assert DatasetPublisher(root).current().version == first.dataset_version
    assert (
        verify_generation(root).active.definition_version == first.definition_version
    )


def test_the_refresh_replaces_one_slice_and_keeps_the_others(refresh_project):
    """Only the refreshed universe's slice changes; the other slice is intact."""
    root = refresh_project.root
    generation = prepare_refresh(root, **refresh_project.args)
    commit_refresh(root, generation)
    with DatasetReader(root).open(generation.dataset_version) as context:
        frame = context.read("universe_membership")

    kept = slice_membership_frame(frame, CSI300_ID)
    replaced = slice_membership_frame(frame, CUSTOM_ID)
    assert membership_slice_hash(
        _facts_from_membership_frame(kept), CSI300_ID
    ) == membership_slice_hash(refresh_project.csi300_facts, CSI300_ID)
    assert membership_slice_hash(
        _facts_from_membership_frame(replaced), CUSTOM_ID
    ) == membership_slice_hash(refresh_project.prepared_facts, CUSTOM_ID)
    assert len(frame) == len(refresh_project.csi300_facts) + len(
        refresh_project.prepared_facts
    )


def test_lagging_derived_caches_self_heal(refresh_project):
    """A crash after the pointer swap leaves caches lagging; verify heals them.

    Protocol B's core semantics: the generation pointer is authoritative, so
    a stale ``CURRENT`` and a reverted top-level definition are rewritten
    from the immutable registry instead of failing verification.
    """
    root = refresh_project.root
    generation = prepare_refresh(root, **refresh_project.args)
    _run_commit(root, generation, stop_after=ORDER[COMMIT_PROTOCOL][0])

    standardized = root / "data" / "standardized"
    (standardized / "CURRENT").write_text(refresh_project.version_before + "\n")
    (root / "configs" / "universes" / f"{CUSTOM_ID}.yml").write_text(
        "universe_id: custom_csi500_tw\n", encoding="utf-8"
    )  # not even a loadable definition: the cache is rewritten, not trusted

    state = verify_generation(root)

    assert state.active.definition_version == generation.definition_version
    assert DatasetPublisher(root).current().version == generation.dataset_version
    top = load_universe_definition(root / "configs" / "universes" / f"{CUSTOM_ID}.yml")
    assert top.version == generation.definition_version


def test_recover_refuses_an_unrecorded_generation(refresh_project):
    """Recovery never guesses: an unrecorded ``--to`` fails with a code."""
    root = refresh_project.root
    generation = prepare_refresh(root, **refresh_project.args)
    commit_refresh(root, generation)
    with pytest.raises(MembershipRefreshError) as error:
        recover_refresh(root, "f" * 64)
    assert error.value.error_code == "membership_generation_inconsistent"


def test_cli_publish_and_recover_round_trip(refresh_project, tmp_path):
    """The operator surface publishes, heals and recovers with exit codes."""
    root = refresh_project.root
    input_path = tmp_path / "prepared.parquet"
    refresh_project.args["prepared_frame"].to_parquet(input_path)
    runner = CliRunner()

    publish = runner.invoke(
        app,
        [
            "data", "index-membership", "publish",
            "--universe-id", CUSTOM_ID,
            "--definition-name", CUSTOM_ID,
            "--input", str(input_path),
            "--rules-version", RULES_VERSION,
            "--evidence-summary-sha256", EVIDENCE_SUMMARY,
            "--root", str(root),
        ],
    )
    assert publish.exit_code == 0, publish.output
    state = verify_generation(root)
    generation = state.active
    assert f"dataset_version={generation.dataset_version}" in publish.output
    assert f"definition_version={generation.definition_version}" in publish.output

    # Simulate a lagging cache crash, then recover through the CLI.
    (root / "data" / "standardized" / "CURRENT").write_text(
        refresh_project.version_before + "\n"
    )
    recover = runner.invoke(
        app,
        [
            "data", "index-membership", "recover",
            "--to", generation.definition_version,
            "--root", str(root),
        ],
    )
    assert recover.exit_code == 0, recover.output
    assert f"recovered_to={generation.definition_version}" in recover.output
    assert DatasetPublisher(root).current().version == generation.dataset_version

    unknown = runner.invoke(
        app,
        [
            "data", "index-membership", "recover",
            "--to", "f" * 64,
            "--root", str(root),
        ],
    )
    assert unknown.exit_code == 2
    assert "membership_generation_inconsistent" in unknown.output


def test_cli_publish_rejects_fatally_invalid_prepared_facts(refresh_project, tmp_path):
    """The evidence gate is not weakened: bad rows fail the publish command."""
    root = refresh_project.root
    invalid = refresh_project.args["prepared_frame"].copy()
    invalid.loc[invalid.index[-1], "snapshot_sha256"] = "nothex"
    input_path = tmp_path / "invalid.parquet"
    invalid.to_parquet(input_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "data", "index-membership", "publish",
            "--universe-id", CUSTOM_ID,
            "--definition-name", CUSTOM_ID,
            "--input", str(input_path),
            "--rules-version", RULES_VERSION,
            "--evidence-summary-sha256", EVIDENCE_SUMMARY,
            "--root", str(root),
        ],
    )
    assert result.exit_code == 2
    assert "membership_facts_rejected" in result.output
    assert DatasetPublisher(root).current().version == refresh_project.version_before
    assert read_generation_state(root) is None
