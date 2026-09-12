"""Versioned universe definition and signal-day membership resolver.

Covers the frozen ``UniverseDefinition`` identity, the pure ``UniverseResolver``
(announcement visibility plus resolved date bounds only), and YAML loading.
The resolver never inspects factor, market or execution state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from stock_quant.data_model.universe_membership import (
    MembershipFact,
    ResolvedMembership,
    SecurityMasterBoundary,
    membership_content_hash,
    resolve_memberships,
)
from stock_quant.research.universe import (
    UniverseCoverageCriterion,
    UniverseCoverageError,
    UniverseDefinition,
    UniverseResolver,
    load_universe_coverage_criterion,
    load_universe_definition,
)

RULES_VERSION = "csi-index-rules-2020h1"
EVIDENCE_SUMMARY_SHA256 = "cd" * 32


def make_fact(**overrides):
    """Return a valid raw fact payload with per-test overrides."""
    values = {
        "universe_id": "csi300",
        "symbol": "600000.SH",
        "raw_effective_from": date(2020, 1, 1),
        "raw_effective_to": None,
        "announcement_date": date(2019, 12, 27),
        "status": "active",
        "reason": "initial_constituent",
        "source": "csi_index_announcement",
        "source_url": "https://www.csindex.com.cn/announcement-2018-12.pdf",
        "snapshot_sha256": "a1" * 32,
        "source_document_sha256": "b2" * 32,
    }
    values.update(overrides)
    return values


def fact(**overrides) -> MembershipFact:
    return MembershipFact.model_validate(make_fact(**overrides))


def base_facts() -> list[MembershipFact]:
    """A three-symbol csi300 slice: removal, add and stable member."""
    return [
        fact(symbol="000001.SZ"),
        fact(
            symbol="600000.SH",
            raw_effective_to=date(2020, 6, 14),
            status="removed",
            reason="regular_rebalance",
            announcement_date=date(2020, 6, 1),
        ),
        fact(
            symbol="600519.SH",
            raw_effective_from=date(2020, 6, 15),
            status="active",
            reason="regular_rebalance",
            announcement_date=date(2020, 6, 15),
        ),
    ]


def definition(**overrides) -> UniverseDefinition:
    """Return a valid frozen definition pinned to ``base_facts`` by default."""
    values = {
        "schema_version": 1,
        "universe_id": "csi300",
        "rules_version": RULES_VERSION,
        "membership_table_sha256": membership_content_hash(base_facts()),
        "coverage_start": date(2020, 1, 1),
        "coverage_end": date(2020, 12, 31),
        "evidence_summary_sha256": EVIDENCE_SUMMARY_SHA256,
    }
    values.update(overrides)
    return UniverseDefinition.model_validate(values)


def build_resolver(facts, **definition_overrides) -> UniverseResolver:
    """Build a resolver whose definition is pinned to exactly these facts."""
    return UniverseResolver(
        definition(
            membership_table_sha256=membership_content_hash(facts),
            **definition_overrides,
        ),
        resolve_memberships(facts, {}),
        facts=facts,
    )


@pytest.fixture()
def resolver() -> UniverseResolver:
    return build_resolver(base_facts())


@pytest.fixture()
def reordered_resolver() -> UniverseResolver:
    """Same facts and definition, but resolved rows in reversed order."""
    facts = base_facts()
    resolved = tuple(reversed(resolve_memberships(facts, {})))
    return UniverseResolver(
        definition(membership_table_sha256=membership_content_hash(facts)),
        resolved,
        facts=facts,
    )


@pytest.fixture()
def announcement_resolver() -> UniverseResolver:
    """One member whose existence becomes public only on 2020-06-15."""
    return build_resolver([fact(announcement_date=date(2020, 6, 15))])


@pytest.fixture()
def removal_resolver() -> UniverseResolver:
    """One member whose inclusive membership ends on 2020-06-14."""
    return build_resolver(
        [
            fact(
                raw_effective_to=date(2020, 6, 14),
                status="removed",
                reason="regular_rebalance",
                announcement_date=date(2020, 6, 8),
            )
        ]
    )


# ---------------------------------------------------------------------------
# Signal-day visibility (plan examples)
# ---------------------------------------------------------------------------


def test_member_is_hidden_until_announcement_date(announcement_resolver):
    assert announcement_resolver.members_on(date(2020, 6, 14)) == ()
    assert announcement_resolver.members_on(date(2020, 6, 15)) == ("600000.SH",)


def test_removed_member_is_absent_after_end(removal_resolver):
    assert "600000.SH" in removal_resolver.members_on(date(2020, 6, 14))
    assert "600000.SH" not in removal_resolver.members_on(date(2020, 6, 15))


def test_daily_snapshot_ignores_source_row_order(resolver, reordered_resolver):
    assert resolver.snapshot_for(date(2020, 6, 15)) == reordered_resolver.snapshot_for(
        date(2020, 6, 15)
    )


def test_members_ignore_resolved_row_order(resolver, reordered_resolver):
    days = [date(2020, 1, 1), date(2020, 6, 14), date(2020, 6, 15), date(2020, 12, 31)]
    for day in days:
        assert resolver.members_on(day) == reordered_resolver.members_on(day)


# ---------------------------------------------------------------------------
# members_on semantics: resolved bounds + announcement gate, nothing else
# ---------------------------------------------------------------------------


def test_members_are_returned_sorted(resolver):
    assert resolver.members_on(date(2020, 6, 14)) == ("000001.SZ", "600000.SH")
    assert resolver.members_on(date(2020, 6, 15)) == ("000001.SZ", "600519.SH")


def test_members_use_resolved_bounds_not_raw_bounds():
    facts = [fact(raw_effective_from=date(2019, 6, 1))]
    boundary = SecurityMasterBoundary(
        symbol="600000.SH", list_date=date(2020, 3, 1), last_tradable_date=None
    )
    value = UniverseResolver(
        definition(membership_table_sha256=membership_content_hash(facts)),
        resolve_memberships(facts, {"600000.SH": boundary}),
        facts=facts,
    )
    assert value.members_on(date(2020, 2, 29)) == ()
    assert value.members_on(date(2020, 3, 1)) == ("600000.SH",)


def test_interval_bounds_are_inclusive(removal_resolver):
    assert removal_resolver.members_on(date(2020, 6, 7)) == ()
    # The announcement day itself is visible; the end bound is inclusive.
    assert removal_resolver.members_on(date(2020, 6, 8)) == ("600000.SH",)
    assert removal_resolver.members_on(date(2020, 6, 14)) == ("600000.SH",)
    assert removal_resolver.members_on(date(2020, 6, 15)) == ()


def test_open_ended_membership_extends_to_coverage_end(resolver):
    members = resolver.members_on(date(2020, 12, 31))
    assert members == ("000001.SZ", "600519.SH")


def test_unusable_resolved_rows_never_become_members():
    unusable = ResolvedMembership(
        universe_id="csi300",
        symbol="600000.SH",
        raw_effective_from=date(2020, 1, 1),
        raw_effective_to=None,
        effective_from=date(2021, 1, 1),
        effective_to=date(2020, 6, 13),
        boundary_adjustment_reason="before_listing,after_delisting",
        announcement_date=date(2019, 12, 27),
        usable=False,
    )
    usable = ResolvedMembership(
        universe_id="csi300",
        symbol="000001.SZ",
        raw_effective_from=date(2020, 1, 1),
        raw_effective_to=None,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        boundary_adjustment_reason=None,
        announcement_date=date(2019, 12, 27),
    )
    value = UniverseResolver(
        definition(membership_table_sha256="ab" * 32), (unusable, usable)
    )
    assert value.members_on(date(2020, 6, 15)) == ("000001.SZ",)


def test_days_outside_definition_coverage_raise(resolver):
    for day in (date(2019, 12, 31), date(2021, 1, 1)):
        with pytest.raises(UniverseCoverageError, match="coverage"):
            resolver.members_on(day)
        with pytest.raises(UniverseCoverageError, match="coverage"):
            resolver.snapshot_for(day)


def test_coverage_bounds_themselves_are_inside_coverage(resolver):
    # The June removal fact is announced 2020-06-01, so only the stable
    # initial member is knowable on the first covered day.
    assert resolver.members_on(date(2020, 1, 1)) == ("000001.SZ",)
    assert resolver.members_on(date(2020, 12, 31)) == ("000001.SZ", "600519.SH")


# ---------------------------------------------------------------------------
# Resolver construction: identity and pinned-hash validation
# ---------------------------------------------------------------------------


def test_resolver_rejects_rows_from_another_universe():
    resolved = resolve_memberships([fact(universe_id="csi500")], {})
    with pytest.raises(ValueError, match="universe_id"):
        UniverseResolver(definition(), resolved)


def test_resolver_pins_the_membership_table_hash():
    facts = base_facts()
    pinned = definition(membership_table_sha256=membership_content_hash(facts))
    assert UniverseResolver(pinned, resolve_memberships(facts, {}), facts=facts)

    tampered = [fact(symbol="000001.SZ", announcement_date=date(2019, 12, 20))]
    with pytest.raises(ValueError, match="membership_table_sha256"):
        UniverseResolver(
            pinned,
            resolve_memberships(tampered, {}),
            facts=tampered,
        )


def test_resolver_accepts_facts_as_raw_mappings():
    facts = base_facts()
    pinned = definition(membership_table_sha256=membership_content_hash(facts))
    mappings = [make_fact(**item.model_dump()) for item in facts]
    assert UniverseResolver(pinned, resolve_memberships(facts, {}), facts=mappings)


def test_resolver_rejects_rows_not_resolved_from_the_pinned_facts():
    other = [fact(symbol="000001.SZ")]
    pinned = definition(membership_table_sha256=membership_content_hash(other))
    with pytest.raises(ValueError, match="resolved"):
        UniverseResolver(
            pinned,
            resolve_memberships(base_facts(), {}),
            facts=other,
        )


def test_resolver_exposes_only_membership_identity(resolver):
    assert resolver.universe_id == "csi300"
    assert resolver.definition.version == definition().version


# ---------------------------------------------------------------------------
# Daily snapshots: canonical (universe_id, day, sorted symbols) SHA-256
# ---------------------------------------------------------------------------


def test_snapshot_format_is_canonical_id_date_symbols_json(resolver):
    expected_payload = {
        "day": "2020-06-15",
        "symbols": ["000001.SZ", "600519.SH"],
        "universe_id": "csi300",
    }
    expected = hashlib.sha256(
        json.dumps(
            expected_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    assert resolver.snapshot_for(date(2020, 6, 15)) == expected


def test_snapshot_is_deterministic_and_day_bound(resolver):
    assert resolver.snapshot_for(date(2020, 6, 15)) == resolver.snapshot_for(
        date(2020, 6, 15)
    )
    assert resolver.snapshot_for(date(2020, 6, 15)) != resolver.snapshot_for(
        date(2020, 6, 14)
    )


def test_snapshot_binds_universe_identity():
    day = date(2020, 6, 15)
    sh = build_resolver([fact(symbol="600000.SH", announcement_date=day)])
    custom = build_resolver(
        [fact(universe_id="csi500", symbol="600000.SH", announcement_date=day)],
        universe_id="csi500",
    )
    assert sh.snapshot_for(day) != custom.snapshot_for(day)


# ---------------------------------------------------------------------------
# UniverseDefinition: frozen identity whose canonical JSON hash is the version
# ---------------------------------------------------------------------------


def test_definition_version_is_canonical_json_sha256():
    value = definition()
    canonical = json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    assert value.version == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "field, changed",
    [
        ("universe_id", "csi500"),
        ("rules_version", "csi-index-rules-2021h1"),
        ("membership_table_sha256", "ef" * 32),
        ("coverage_start", date(2020, 1, 2)),
        ("coverage_end", date(2020, 12, 30)),
        ("evidence_summary_sha256", "01" * 32),
    ],
)
def test_definition_version_changes_with_every_pinned_field(field, changed):
    base = definition()
    assert base.version != definition(**{field: changed}).version


def test_definition_is_frozen():
    value = definition()
    with pytest.raises(ValidationError):
        value.rules_version = "tampered"


def test_definition_rejects_unknown_schema_version():
    with pytest.raises(ValidationError):
        definition(schema_version=2)


def test_definition_rejects_unknown_universe_id():
    with pytest.raises(ValidationError):
        definition(universe_id="hs300")


@pytest.mark.parametrize(
    "field", ["membership_table_sha256", "evidence_summary_sha256"]
)
@pytest.mark.parametrize("bad", ["AB" * 32, "cd" * 31, "", "0" * 63 + "g"])
def test_definition_rejects_malformed_hashes(field, bad):
    with pytest.raises(ValidationError):
        definition(**{field: bad})


def test_definition_rejects_inverted_coverage():
    with pytest.raises(ValidationError):
        definition(coverage_start=date(2020, 12, 31), coverage_end=date(2020, 1, 1))


def test_definition_rejects_blank_rules_version():
    with pytest.raises(ValidationError):
        definition(rules_version="   ")


# ---------------------------------------------------------------------------
# load_universe_definition: YAML loading and hash validation
# ---------------------------------------------------------------------------


def test_load_universe_definition_round_trips_a_yaml_document(tmp_path):
    path = tmp_path / "csi300.yml"
    document = {
        "schema_version": 1,
        "universe_id": "csi300",
        "rules_version": RULES_VERSION,
        "membership_table_sha256": "ab" * 32,
        "coverage_start": "2020-01-01",
        "coverage_end": "2020-12-31",
        "evidence_summary_sha256": "cd" * 32,
    }
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    value = load_universe_definition(path)
    assert value == definition(
        membership_table_sha256="ab" * 32,
    )
    assert value.coverage_start == date(2020, 1, 1)
    assert value.coverage_end == date(2020, 12, 31)


def test_load_universe_definition_accepts_unquoted_iso_dates(tmp_path):
    path = tmp_path / "csi300.yml"
    path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "universe_id: csi300",
                f"rules_version: {RULES_VERSION}",
                "membership_table_sha256: " + "ab" * 32,
                "coverage_start: 2020-01-01",
                "coverage_end: 2020-12-31",
                "evidence_summary_sha256: " + "cd" * 32,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert load_universe_definition(path).coverage_start == date(2020, 1, 1)


def test_load_universe_definition_rejects_placeholder_values(tmp_path):
    path = tmp_path / "template.yml"
    path.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "universe_id: csi300",
                "rules_version: PLACEHOLDER",
                "membership_table_sha256: PLACEHOLDER_MEMBERSHIP_TABLE_SHA256",
                "coverage_start: 1900-01-01",
                "coverage_end: 1900-01-01",
                "evidence_summary_sha256: PLACEHOLDER_EVIDENCE_SUMMARY_SHA256",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_universe_definition(path)


@pytest.mark.parametrize(
    "document",
    [
        [],
        None,
        {"universe_id": "csi300"},
        {
            "schema_version": 1,
            "universe_id": "csi300",
            "rules_version": RULES_VERSION,
            "membership_table_sha256": "ab" * 32,
            "coverage_start": "2020-01-01",
            "coverage_end": "2020-12-31",
            "evidence_summary_sha256": "cd" * 32,
            "extra_field": "rejected",
        },
    ],
)
def test_load_universe_definition_rejects_malformed_documents(tmp_path, document):
    path = tmp_path / "bad.yml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        load_universe_definition(path)


def _write_definition(directory, name: str, *, start: str, enabled=None, **overrides):
    payload = {
        "schema_version": 1,
        "universe_id": f"custom_{name.replace('.', '_')}",
        "rules_version": "fixture-rules-v1",
        "membership_table_sha256": "ab" * 32,
        "coverage_start": start,
        "coverage_end": "2022-01-07",
        "evidence_summary_sha256": "cd" * 32,
    }
    payload.update(overrides)
    if enabled is not None:
        payload["enabled"] = enabled
    (directory / name).write_text(yaml.safe_dump(payload), encoding="utf-8")
    return payload


def test_coverage_criterion_takes_the_earliest_enabled_start(tmp_path):
    _write_definition(tmp_path, "b_second.yml", start="2019-01-02")
    _write_definition(tmp_path, "a_first.yml", start="2015-01-05")
    criterion = load_universe_coverage_criterion(tmp_path)
    assert criterion.acceptance_start == date(2015, 1, 5)
    assert criterion.skipped == ()
    assert set(criterion.definition_hashes) == {
        "custom_a_first_yml",
        "custom_b_second_yml",
    }
    assert all(len(value) == 64 for value in criterion.definition_hashes.values())


def test_coverage_criterion_skips_explicitly_disabled_definitions(tmp_path):
    _write_definition(tmp_path, "csi300.yml", start="2005-01-03", enabled=False)
    _write_definition(tmp_path, "custom_live.yml", start="2015-01-05")
    criterion = load_universe_coverage_criterion(tmp_path)
    assert criterion.acceptance_start == date(2015, 1, 5)
    assert criterion.skipped == ("csi300.yml",)
    assert set(criterion.definition_hashes) == {"custom_custom_live_yml"}


def test_coverage_criterion_blocks_an_enabled_definition_that_cannot_parse(tmp_path):
    _write_definition(tmp_path, "custom_broken.yml", start="2015-01-05",
                      membership_table_sha256="PLACEHOLDER")
    with pytest.raises(UniverseCoverageError):
        load_universe_coverage_criterion(tmp_path)


def test_coverage_criterion_blocks_a_non_mapping_document(tmp_path):
    (tmp_path / "custom_bad.yml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(UniverseCoverageError):
        load_universe_coverage_criterion(tmp_path)


def test_coverage_criterion_is_empty_without_any_definition(tmp_path):
    criterion = load_universe_coverage_criterion(tmp_path / "missing")
    assert criterion == UniverseCoverageCriterion(None, {}, ())
