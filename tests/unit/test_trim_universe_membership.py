"""裁剪纯函数：从冻结的 csi300 facts 派生可交易子集定义。

Loaded by path because ``project/`` is not a package (repo convention).
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

from stock_quant.data_model.universe_membership import (
    membership_content_hash,
    membership_frame,
)
from stock_quant.research.universe import UniverseDefinition

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = REPO_ROOT / "project" / "trim_universe_membership.py"
    spec = importlib.util.spec_from_file_location("trim_universe_membership", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_m = _load_module()
BASE_UNIVERSE_ID = _m.BASE_UNIVERSE_ID
TRADABLE_UNIVERSE_ID = _m.TRADABLE_UNIVERSE_ID
build_tradable_definition = _m.build_tradable_definition
facts_from_rows = _m.facts_from_rows
retarget_universe_id = _m.retarget_universe_id
trim_membership_rows = _m.trim_membership_rows

_SHA = "a" * 64


def _fact(symbol: str, *, start: date, end: date | None = None,
          reason: str = "regular_rebalance") -> dict:
    return {
        "universe_id": BASE_UNIVERSE_ID,
        "symbol": symbol,
        "raw_effective_from": start,
        "raw_effective_to": end,
        "announcement_date": start,
        "status": "active" if end is None else "removed",
        "reason": reason,
        "source": "index_constitution",
        "source_url": "https://example.invalid/csi300",
        "snapshot_sha256": _SHA,
        "source_document_sha256": _SHA,
    }


def _base_definition() -> UniverseDefinition:
    return UniverseDefinition(
        schema_version=1,
        universe_id=BASE_UNIVERSE_ID,
        rules_version="index_constitution-1.0.0+repairs-07e2f18d",
        membership_table_sha256=_SHA,
        evidence_summary_sha256=_SHA,
        coverage_start=date(2015, 1, 5),
        coverage_end=date(2026, 8, 28),
    )


def test_trim_keeps_only_master_symbols():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
        _fact("600005.SH", start=date(2015, 1, 5)),
        _fact("688001.SH", start=date(2019, 7, 22), reason="initial_constituent"),
    ])
    trimmed = trim_membership_rows(frame, {"000001.SZ", "688001.SH"})
    assert sorted(trimmed["symbol"]) == ["000001.SZ", "688001.SH"]
    assert list(trimmed.columns) == list(frame.columns)


def test_trim_resets_the_index():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
        _fact("000002.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    trimmed = trim_membership_rows(frame, {"000002.SZ"})
    assert list(trimmed.index) == [0]


def test_trim_to_empty_is_allowed():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    assert trim_membership_rows(frame, set()).empty


def test_retarget_relabels_every_row_without_touching_evidence():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
        _fact("000300.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    retargeted = retarget_universe_id(frame, TRADABLE_UNIVERSE_ID)
    assert set(retargeted["universe_id"]) == {TRADABLE_UNIVERSE_ID}
    assert retargeted["symbol"].tolist() == frame["symbol"].tolist()
    assert (
        retargeted["source_document_sha256"].tolist()
        == frame["source_document_sha256"].tolist()
    )
    # 原 frame 不被就地修改
    assert set(frame["universe_id"]) == {BASE_UNIVERSE_ID}


def test_retarget_changes_the_content_hash():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    before = membership_content_hash(facts_from_rows(frame))
    after = membership_content_hash(
        facts_from_rows(retarget_universe_id(frame, TRADABLE_UNIVERSE_ID))
    )
    assert after != before


def test_facts_from_rows_round_trips_dates_and_evidence():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    facts = facts_from_rows(frame)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.raw_effective_from == date(2015, 1, 5)
    assert fact.raw_effective_to is None
    assert fact.source_document_sha256 == _SHA
    assert membership_content_hash(facts) == membership_content_hash(
        facts_from_rows(frame)
    )


def test_build_tradable_definition_rewrites_id_hash_and_rules():
    frame = membership_frame([
        _fact("000001.SZ", start=date(2015, 1, 5), reason="initial_constituent"),
    ])
    facts = facts_from_rows(frame)
    base = _base_definition()
    document = build_tradable_definition(
        base,
        facts=facts,
        coverage_start=date(2015, 1, 5),
        coverage_end=date(2026, 8, 28),
    )
    assert document["universe_id"] == TRADABLE_UNIVERSE_ID
    assert document["rules_version"] == base.rules_version + "+tradable"
    assert document["evidence_summary_sha256"] == base.evidence_summary_sha256
    assert document["membership_table_sha256"] == membership_content_hash(facts)
    assert document["coverage_start"] == "2015-01-05"
    assert document["coverage_end"] == "2026-08-28"
    # 身份必须与原定义不同，否则冻结版本会撞车。
    assert document["membership_table_sha256"] != base.membership_table_sha256
    UniverseDefinition.model_validate(document)


def test_tradable_id_is_custom_prefixed():
    """护栏：派生定义不得占用 canonical id。"""
    assert TRADABLE_UNIVERSE_ID.startswith("custom_")
    assert TRADABLE_UNIVERSE_ID != "csi300"
    assert TRADABLE_UNIVERSE_ID != BASE_UNIVERSE_ID
