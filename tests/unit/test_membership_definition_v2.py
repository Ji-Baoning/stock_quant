"""Schema-v2 universe definitions: slice scope, coverage segments and gaps."""
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_quant.data_model.index_membership_import import (
    build_snapshot_facts,
    membership_difference_report,
    merge_collected_at_first_write,
)
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
    window_crosses_membership_gap,
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


def _mixed_v2():
    import pandas as pd

    from stock_quant.data_model.calendar import TradingCalendar
    from stock_quant.data_model.universe_membership import membership_frame
    from stock_quant.research.acceptance.checks import (
        evaluate_index_membership_evidence,
    )
    mine = make_fact(universe_id="custom_csi500_tw",
                     raw_effective_from=date(2019, 1, 1))
    theirs = make_fact(universe_id="custom_csi300_tw", symbol="000001.SZ")
    frame = membership_frame([mine, theirs])
    definition = UniverseDefinition.model_validate(_v2(
        universe_id="custom_csi500_tw",
        coverage_start=date(2019, 1, 1), coverage_end=date(2021, 12, 31),
        membership_table_sha256=membership_slice_hash(
            [mine, theirs], "custom_csi500_tw"),
        coverage_segments=[MembershipCoverageSegment(
            start=date(2019, 1, 1), end=date(2021, 12, 31),
            evidence_sha256="b" * 64)]))
    days = tuple(pd.bdate_range(date(2019, 1, 1), date(2021, 12, 31)).date)
    return evaluate_index_membership_evidence, frame, definition, \
        TradingCalendar.from_open_days(days), mine, theirs


def test_v2_evidence_selects_the_slice_before_every_check():
    evaluate, frame, definition, calendar, *_ = _mixed_v2()
    assert evaluate(frame, definition=definition, calendar=calendar,
                    expected_sizes={}).status.value == "PASS"


def test_an_empty_slice_fails_with_a_stable_code():
    evaluate, frame, definition, calendar, *_ = _mixed_v2()
    only_theirs = frame[frame["universe_id"] == "custom_csi300_tw"]
    result = evaluate(only_theirs.reset_index(drop=True), definition=definition,
                      calendar=calendar, expected_sizes={})
    assert result.status.value == "FAIL"
    assert "UNIVERSE_SLICE_EMPTY" in result.details["error_codes"]


def test_slice_facts_outside_the_segments_fail():
    from stock_quant.data_model.universe_membership import membership_frame
    evaluate, _, _, calendar, mine, _ = _mixed_v2()
    early = make_fact(universe_id="custom_csi500_tw",
                      raw_effective_from=date(2018, 6, 1),
                      raw_effective_to=date(2018, 12, 31),
                      announcement_date=date(2018, 5, 15),
                      status="removed",
                      reason="regular_rebalance")  # 必须闭合：与 mine 同为
    # custom_csi500_tw/600000.SH，若两条都 open，membership_slice_hash 会先在
    # _ensure_non_overlapping 抛 "membership facts overlap"，测不到越界码；
    # removed/regular_rebalance 与公告先于生效是区间一致性所必需的，否则验收器
    # 先报 UNIVERSE_INTERVAL_CONFLICT / UNIVERSE_ANNOUNCEMENT_AFTER_USE，
    # 同样测不到越界码。
    definition = UniverseDefinition.model_validate(_v2(
        universe_id="custom_csi500_tw",
        coverage_start=date(2019, 1, 1), coverage_end=date(2021, 12, 31),
        membership_table_sha256=membership_slice_hash([early, mine],
                                                      "custom_csi500_tw"),
        coverage_segments=[MembershipCoverageSegment(
            start=date(2019, 1, 1), end=date(2021, 12, 31),
            evidence_sha256="b" * 64)]))
    result = evaluate(membership_frame([early, mine]), definition=definition,
                      calendar=calendar, expected_sizes={})
    assert "UNIVERSE_FACT_OUTSIDE_COVERAGE" in result.details["error_codes"]


def test_a_window_crossing_a_gap_is_reported():
    gap = MembershipCoverageGap(start=date(2020, 6, 1), end=date(2020, 6, 30),
                                reason=MEMBERSHIP_OBSERVATION_GAP,
                                evidence_sha256="c" * 64)
    definition = UniverseDefinition.model_validate(_v2(
        universe_id="custom_csi500_tw",
        coverage_segments=[
            MembershipCoverageSegment(start=date(2015, 1, 5), end=date(2020, 5, 31),
                                      evidence_sha256="b" * 64),
            MembershipCoverageSegment(start=date(2020, 7, 1), end=date(2026, 8, 28),
                                      evidence_sha256="b" * 64)],
        coverage_gaps=[gap]))
    assert window_crosses_membership_gap(
        definition, date(2020, 5, 1), date(2020, 6, 15)) is gap
    assert window_crosses_membership_gap(
        definition, date(2020, 7, 1), date(2020, 8, 31)) is None


def test_the_resolver_rejects_whole_table_forks():
    from stock_quant.data_model.universe_membership import resolve_memberships
    from stock_quant.research.universe import UniverseResolver
    _, _, definition, _, mine, theirs = _mixed_v2()
    with pytest.raises(ValueError, match="belongs to universe_id"):
        UniverseResolver(definition, resolve_memberships([mine, theirs]),
                         facts=[mine, theirs])


# ---------------------------------------------------------------------------
# P2b Task 3: the version registry, the non-recursive top-level scan, the
# no-slice migration helpers and the v1 archive rule (spec 7.0.4)
# ---------------------------------------------------------------------------


def test_an_explicit_version_resolves_from_the_registry_and_rechecks_hash(
        tmp_path):
    import yaml

    from stock_quant.research.universe import (
        UniverseCoverageError,
        resolve_versioned_universe_definition,
    )
    definition = UniverseDefinition.model_validate(_v2())
    registry = tmp_path / "versions"
    registry.mkdir()
    entry = registry / f"{definition.version}.yml"
    entry.write_text(
        yaml.safe_dump(definition.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8")
    resolved = resolve_versioned_universe_definition(tmp_path, definition.version)
    assert resolved.version == definition.version
    entry.write_text(yaml.safe_dump(
        {**definition.model_dump(mode="json"), "rules_version": "evil"},
        sort_keys=True), encoding="utf-8")
    with pytest.raises(UniverseCoverageError, match="content hash"):
        resolve_versioned_universe_definition(tmp_path, definition.version)


def test_a_missing_registry_entry_never_falls_back_to_the_top_level(tmp_path):
    from stock_quant.research.universe import (
        UniverseCoverageError,
        resolve_versioned_universe_definition,
    )
    with pytest.raises(UniverseCoverageError, match="registry"):
        resolve_versioned_universe_definition(tmp_path, "f" * 64)


def test_the_top_level_scan_stays_non_recursive(tmp_path):
    import yaml

    from stock_quant.research.universe import (
        UniverseCoverageError,
        load_universe_coverage_criterion,
        resolve_versioned_universe_definition,
    )
    v2 = UniverseDefinition.model_validate(_v2())
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / f"{v2.version}.yml").write_text(
        yaml.safe_dump(v2.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8")
    top_level = dict(
        schema_version=1, universe_id="custom_csi300_ic",
        rules_version="tushare-index-weight-monthly-v1",
        membership_table_sha256="a" * 64,
        coverage_start=date(2019, 1, 1), coverage_end=date(2021, 12, 31),
        evidence_summary_sha256="b" * 64, enabled=True)
    (tmp_path / "custom_csi300_ic.yml").write_text(
        yaml.safe_dump(top_level, sort_keys=True), encoding="utf-8")
    criterion = load_universe_coverage_criterion(tmp_path)
    assert list(criterion.definition_hashes) == ["custom_csi300_ic"]
    assert criterion.acceptance_start == date(2019, 1, 1)
    # 注册表里的 v2 条目只走显式版本解析，永远不进顶层扫描。
    assert resolve_versioned_universe_definition(
        tmp_path, v2.version).version == v2.version
    # 顶层指针文件缺 universe_id：扫描必须阻断发布，而不是悄悄跳过。
    (tmp_path / "pointer.yml").write_text(
        yaml.safe_dump({"universe_version": v2.version}, sort_keys=True),
        encoding="utf-8")
    with pytest.raises(UniverseCoverageError):
        load_universe_coverage_criterion(tmp_path)


def test_no_slice_definitions_stay_v1_and_archive(tmp_path):
    import yaml

    from stock_quant.data_model.universe_membership import membership_slice_hash
    from stock_quant.research.universe import (
        UniverseCoverageError,
        archive_universe_definition,
        definition_slice_hash,
        load_universe_coverage_criterion,
    )

    def _v1(universe_id: str, start: date) -> dict:
        return dict(
            schema_version=1, universe_id=universe_id,
            rules_version="tushare-index-weight-monthly-v1",
            membership_table_sha256="a" * 64,
            coverage_start=start, coverage_end=date(2021, 12, 31),
            evidence_summary_sha256="b" * 64)

    keeper = UniverseDefinition.model_validate(
        _v1("custom_csi300_tw", date(2015, 1, 5)))
    noslice = UniverseDefinition.model_validate(
        _v1("custom_csi300_ic", date(2019, 1, 1)))
    root = tmp_path / "universes"
    root.mkdir()
    payloads = {
        "custom_csi300_tw.yml": keeper.model_dump(mode="json"),
        "custom_csi300_ic.yml": noslice.model_dump(mode="json"),
    }
    for name, payload in payloads.items():
        (root / name).write_text(
            yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    facts = [make_fact(universe_id="custom_csi300_tw")]
    # 目标数据集没有它的 slice：拒绝为空 slice 计算哈希，不冒充 v2。
    with pytest.raises(ValueError, match="empty slice"):
        definition_slice_hash(facts, noslice)
    assert definition_slice_hash(facts, keeper) == \
        membership_slice_hash(facts, "custom_csi300_tw")

    before = load_universe_coverage_criterion(root).acceptance_start
    assert before == date(2015, 1, 5)
    archived = archive_universe_definition(root, "custom_csi300_ic.yml")
    assert archived == root / "archive" / "custom_csi300_ic.yml"
    assert archived.read_bytes() == yaml.safe_dump(
        payloads["custom_csi300_ic.yml"], sort_keys=True).encode("utf-8")
    after = load_universe_coverage_criterion(root).acceptance_start
    assert after == before  # 移出后剩余启用定义的最小 coverage_start 不变
    criterion = load_universe_coverage_criterion(root)
    assert list(criterion.definition_hashes) == ["custom_csi300_tw"]

    # 反例：移出唯一的最小起点定义会抬高 acceptance_start → 拒绝且原位不动。
    with pytest.raises(UniverseCoverageError, match="acceptance start"):
        archive_universe_definition(root, "custom_csi300_tw.yml")
    assert (root / "custom_csi300_tw.yml").is_file()
    assert not (root / "archive" / "custom_csi300_tw.yml").exists()


# ---------------------------------------------------------------------------
# P2b Task 5: attested-boundary fact generation, the difference report and
# the first-write collected_at provenance merge (spec 7.1)
# ---------------------------------------------------------------------------

_KW = dict(universe_id="custom_csi500_tw", source="tushare",
           source_url="https://tushare.pro/document/2?doc_id=95",
           collected_at=datetime(2026, 9, 30, 8, 0), cadence="monthly")


def _snap(day, symbols, digest="a" * 64):
    import pandas as pd
    # 第三个元素是快照文件哈希：MembershipFact.snapshot_sha256 是必填 64-hex，
    # 测试不能省。
    return (day, pd.DataFrame({"symbol": symbols}), digest)


def test_snapshot_diff_produces_attested_boundary_facts():
    facts, segments, gaps = build_snapshot_facts(
        [_snap(date(2024, 1, 31), ["600000.SH"]),
         _snap(date(2024, 2, 28), ["600000.SH", "000001.SZ"]),
         _snap(date(2024, 3, 29), ["000001.SZ"])], **_KW)
    join = next(f for f in facts if f.symbol == "000001.SZ"
                and f.raw_effective_from == date(2024, 2, 28))
    assert join.announcement_date == date(2024, 2, 28)  # = raw_effective_from
    assert join.reason.value == "snapshot_observed_change"
    gone = next(f for f in facts if f.symbol == "600000.SH")
    assert gone.raw_effective_to == date(2024, 3, 28)  # 最后列出快照前一日
    assert gone.status.value == "removed"
    assert gaps == () and len(segments) == 1


def test_a_missing_snapshot_opens_a_gap_and_starts_a_new_segment():
    facts, segments, gaps = build_snapshot_facts(
        [_snap(date(2024, 1, 31), ["600000.SH"]),
         _snap(date(2024, 3, 29), ["600000.SH"])], **_KW)
    assert [g.reason for g in gaps] == [MEMBERSHIP_OBSERVATION_GAP]
    assert len(segments) == 2
    # 跨 gap 不差分：段一闭、段二重开，无任何“精确移除日”声称
    intervals = sorted((f.raw_effective_from, f.raw_effective_to)
                       for f in facts if f.symbol == "600000.SH")
    assert len(intervals) == 2


def test_unknown_cadence_refuses_to_guess_without_manual_confirmation():
    with pytest.raises(ValueError, match="manual confirmation"):
        build_snapshot_facts([_snap(date(2024, 1, 31), ["600000.SH"])],
                             **{**_KW, "cadence": "unknown"})


def test_backfill_keeps_collected_at_provenance_only():
    import pandas as pd

    from stock_quant.data_model.universe_membership import membership_frame
    facts, *_ = build_snapshot_facts(
        [_snap(date(2015, 1, 30), ["600000.SH"])], **_KW)
    first = facts[0]
    assert first.collected_at.date() != first.announcement_date  # 补采 ≠ 快照日
    # merge 的键是 (universe_id, symbol, raw_effective_from)：baseline 必须
    # 落在同一键上，否则左连不命中、拿的是 new 的 collected_at。
    landed = membership_frame([fact(universe_id="custom_csi500_tw",
                                    raw_effective_from=date(2015, 1, 30),
                                    collected_at=datetime(2020, 1, 1))])
    merged = merge_collected_at_first_write(membership_frame([first]), landed)
    assert pd.Timestamp(merged["collected_at"].iloc[0]).date() == date(2020, 1, 1)


def test_official_overlaps_produce_only_a_difference_report():
    # 两侧必须真有区间差异：`fact()` 的默认值相同，照默认构造会得到两条
    # 逐字段相等的事实，正确实现应返回空报告，断言就永远不成立。
    candidate = [fact(universe_id="csi300", symbol="600000.SH",
                      raw_effective_from=date(2019, 1, 1),
                      raw_effective_to=date(2021, 12, 31),
                      status="removed", reason="regular_rebalance")]
    official = [fact(universe_id="csi300", symbol="600000.SH",
                     raw_effective_from=date(2019, 1, 1),
                     raw_effective_to=date(2022, 6, 30),
                     status="removed", reason="regular_rebalance")]
    report = membership_difference_report(candidate, official)
    assert report and report[0]["kind"] == "interval_disagreement"
    assert report[0]["symbol"] == "600000.SH"


def test_a_generated_gap_definition_fails_a_crossing_window():
    facts, segments, gaps = build_snapshot_facts(
        [_snap(date(2024, 1, 31), ["600000.SH"]),
         _snap(date(2024, 3, 29), ["600000.SH"])], **_KW)
    definition = UniverseDefinition.model_validate(_v2(
        coverage_start=segments[0].start, coverage_end=segments[-1].end,
        membership_table_sha256=membership_slice_hash(
            facts, "custom_csi500_tw"),
        coverage_segments=list(segments), coverage_gaps=list(gaps)))
    assert window_crosses_membership_gap(
        definition, date(2024, 2, 1), date(2024, 2, 15)) is gaps[0]
    assert window_crosses_membership_gap(
        definition, date(2024, 3, 29), date(2024, 3, 29)) is None
