"""Schema-v2 universe definitions: slice scope, coverage segments and gaps."""
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_quant.data_model.universe_membership import (
    membership_content_hash,
    membership_slice_hash,
)
from stock_quant.research.universe import (
    MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID,
    MEMBERSHIP_OBSERVATION_GAP,
    MembershipCoverageGap,
    MembershipCoverageSegment,
    UniverseDefinition,
    canonical_json_sha256,
)
from stock_quant.safe_yaml import read_yaml
from tests.unit.test_universe_membership import fact, make_fact

_REPO = Path(__file__).resolve().parents[2]


def _v2(**overrides):
    values = dict(
        schema_version=2, universe_id="custom_csi500_tw",
        rules_version="tushare-index-weight-monthly-v1",
        membership_table_sha256="a" * 64,
        coverage_start=date(2015, 1, 5), coverage_end=date(2026, 8, 28),
        evidence_summary_sha256="b" * 64,
        membership_hash_scope=MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID,
        coverage_segments=[MembershipCoverageSegment(
            start=date(2015, 1, 5), end=date(2026, 8, 28),
            evidence_sha256="b" * 64)])
    values.update(overrides)
    return values


def test_v1_rejects_v2_keys_with_no_implicit_fallback():
    base = dict(schema_version=1, universe_id="csi300", rules_version="r1",
                membership_table_sha256="a" * 64,
                coverage_start=date(2019, 1, 1), coverage_end=date(2021, 12, 31),
                evidence_summary_sha256="b" * 64)
    with pytest.raises(ValidationError, match="schema-v1"):
        UniverseDefinition.model_validate(
            {**base, "membership_hash_scope": MEMBERSHIP_HASH_SCOPE_UNIVERSE_ID})
    assert UniverseDefinition.model_validate(base).schema_version == 1


def test_gaps_must_exactly_complement_the_segments():
    tiled = _v2(coverage_segments=[
        MembershipCoverageSegment(start=date(2015, 1, 5), end=date(2020, 12, 31),
                                  evidence_sha256="b" * 64),
        MembershipCoverageSegment(start=date(2021, 2, 1), end=date(2026, 8, 28),
                                  evidence_sha256="b" * 64)])
    gap = lambda s, e: MembershipCoverageGap(  # noqa: E731
        start=s, end=e, reason=MEMBERSHIP_OBSERVATION_GAP,
        evidence_sha256="c" * 64)
    with pytest.raises(ValidationError, match="complement"):
        UniverseDefinition.model_validate(  # 留洞：gap 不在段缝里
            {**tiled, "coverage_gaps": [gap(date(2021, 2, 1), date(2021, 2, 28))]})
    with pytest.raises(ValidationError, match="complement"):
        UniverseDefinition.model_validate(  # 落在整段覆盖内
            {**_v2(), "coverage_gaps": [gap(date(2020, 6, 1), date(2020, 6, 30))]})
    ok = UniverseDefinition.model_validate(
        {**tiled, "coverage_gaps": [gap(date(2021, 1, 1), date(2021, 1, 31))]})
    assert ok.coverage_gaps is not None


def test_segments_must_match_the_declared_envelope():
    with pytest.raises(ValidationError, match="envelope"):
        UniverseDefinition.model_validate(_v2(coverage_start=date(2014, 1, 1)))
    with pytest.raises(ValidationError, match="overlap"):
        UniverseDefinition.model_validate(_v2(coverage_segments=[
            MembershipCoverageSegment(start=date(2015, 1, 5), end=date(2020, 1, 1),
                                      evidence_sha256="b" * 64),
            MembershipCoverageSegment(start=date(2020, 1, 1), end=date(2026, 8, 28),
                                      evidence_sha256="b" * 64)]))


def test_v1_version_hashes_are_byte_stable_after_the_change():
    root = _REPO / "project" / "configs" / "universes"
    for name in ("custom_csi300_ic", "custom_csi300_ic_tradable",
                 "custom_csi300_tw", "custom_csi300_tw_tradable"):
        doc = read_yaml(root / f"{name}.yml")
        legacy = canonical_json_sha256({key: doc[key] for key in (
            "coverage_end", "coverage_start", "evidence_summary_sha256",
            "membership_table_sha256", "rules_version", "schema_version",
            "universe_id")})
        assert UniverseDefinition.model_validate(doc).version == legacy


def test_the_slice_hash_scopes_to_one_universe_id():
    mine = make_fact(universe_id="custom_csi500_tw")
    theirs = make_fact(universe_id="custom_csi300_tw", symbol="000001.SZ")
    assert membership_slice_hash([mine, theirs], "custom_csi500_tw") == \
        membership_content_hash([mine])
    assert membership_slice_hash([mine, theirs], "custom_csi500_tw") != \
        membership_content_hash([mine, theirs])
    assert fact().collected_at is None  # 新字段默认不出现


def test_collected_at_never_changes_the_content_hash():
    bare = fact()
    stamped = fact(collected_at=datetime(2026, 9, 30, 12, 0))
    assert membership_content_hash([bare]) == membership_content_hash([stamped])
