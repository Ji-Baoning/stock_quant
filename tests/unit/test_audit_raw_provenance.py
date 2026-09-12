"""Unit tests for the one-off raw provenance audit (design §5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

import audit_raw_provenance  # noqa: E402
from audit_raw_provenance import (  # noqa: E402
    INSTALLED,
    UNKNOWN,
    SkippedManifest,
    SnapshotRecord,
    report,
    scan,
    summarise,
)


def _write(root: Path, *parts: str, text: str) -> None:
    target = root.joinpath(*parts)
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(text, encoding="utf-8")


def _write_manifest(root: Path, *parts: str, manifest: dict) -> None:
    _write(root, *parts, text=json.dumps(manifest))


TUSHARE_MANIFEST = {
    "source": "tushare",
    "endpoint": "daily",
    "supplier_endpoint": "tushare_relay.jiaoch.top.daily",
    "sdk_version": "1.4.24",
    "transport_id": "jiaoch.top",
    "request_timestamp": "2026-09-12T00:00:00Z",
}


def test_scan_reads_both_path_shapes(tmp_path):
    raw = tmp_path / "data" / "raw"
    _write_manifest(
        raw,
        "tushare",
        "daily",
        "jiaoch.top",
        "rk",
        "aa" * 32,
        manifest=TUSHARE_MANIFEST,
    )
    _write_manifest(
        raw,
        "tushare",
        "daily",
        "rk",
        "bb" * 32,
        manifest={
            "source": "tushare",
            "endpoint": "daily",
            "supplier_endpoint": "tushare.pro.daily",
            "sdk_version": "1.4.29",
            "request_timestamp": "2026-09-10T00:00:00Z",
        },
    )
    result = scan(tmp_path)
    assert result.skipped == []
    assert len(result.records) == 2
    assert {record.transport_id for record in result.records} == {"jiaoch.top", None}
    assert {record.sdk_version for record in result.records} == {"1.4.24", "1.4.29"}


def test_scan_reports_what_it_could_not_read_instead_of_dropping_it(tmp_path):
    """A silently skipped manifest makes every total below it a lie."""
    raw = tmp_path / "data" / "raw"
    _write_manifest(
        raw, "tushare", "daily", "rk", "cc" * 32, manifest={"unrelated": True}
    )
    broken = raw / "tushare" / "daily" / "rk" / ("dd" * 32)
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    result = scan(tmp_path)
    assert result.records == []
    # NOTE (task-7): the brief keyed this on ``skipped.path.name``, but every
    # skipped entry is literally named ``manifest.json`` -- the dict collapsed
    # to one key and dropped a reason, so asserting both reasons against that
    # single value is unsatisfiable by any implementation.  Assert over the
    # list instead; count, absolute-path and reason-text checks are unchanged.
    reasons = [skipped.reason for skipped in result.skipped]
    assert len(result.skipped) == 2
    assert all(skipped.path.is_absolute() for skipped in result.skipped)
    assert any("not valid JSON" in reason for reason in reasons)
    assert any("no 'source' field" in reason for reason in reasons)


def test_scan_returns_nothing_for_a_store_that_does_not_exist(tmp_path):
    result = scan(tmp_path)
    assert result.records == []
    assert result.skipped == []


def test_the_default_root_is_the_project_directory():
    # Real data lives in ``project/data/raw``; a repo-root default would find
    # nothing and report an empty audit as if it were the truth.
    assert audit_raw_provenance.PROJECT_ROOT == PROJECT
    assert (PROJECT / "data" / "raw").is_dir()
    assert audit_raw_provenance.DEFAULT_REPORT.parent == (
        PROJECT.parent / "docs" / "operations"
    )


def test_summarise_flags_environments_absent_from_this_interpreter():
    records = [
        SnapshotRecord("tushare", "daily", "tushare.pro.daily", "1.4.29", None, "e"),
        SnapshotRecord("tushare", "daily", "tushare_relay.x.daily", "1.4.24", "x", "e"),
    ]
    summary = summarise(records, local_sdk_versions={"tushare": {"1.4.24"}})
    provenance = {row["sdk_version"]: row["provenance"] for row in summary.sdk_rows}
    assert provenance["1.4.24"] == INSTALLED
    assert provenance["1.4.29"] == UNKNOWN
    assert summary.total == 2
    assert summary.by_source == {"tushare": 2}


def test_report_states_the_limit_of_what_it_can_conclude():
    records = [
        SnapshotRecord("tushare", "daily", "tushare.pro.daily", "1.4.29", None, "e"),
    ]
    text = report(records)
    assert "1.4.29" in text
    # The report must refuse to infer a provider from a label that has no
    # discriminating power, and must say so in so many words.
    assert "不对历史 provider 下结论" in text
    assert UNKNOWN in text


def test_report_does_not_claim_a_missing_environment_is_absent_from_the_machine():
    """``local_sdk_versions`` only sees the running interpreter.

    Concluding "不在本机" would require scanning other environments and caches,
    which this script does not do -- so it must not say it.
    """
    text = report(
        [SnapshotRecord("tushare", "daily", "tushare.pro.daily", "1.4.29", None, "e")]
    )
    assert "当前解释器未安装" in text
    assert "不在本机" not in text
    assert "不能在本机原样复现" not in text


def test_report_names_every_manifest_it_could_not_read():
    skipped = [
        SkippedManifest(
            path=Path("/store/data/raw/x/manifest.json"), reason="not valid JSON"
        )
    ]
    text = report([], skipped=skipped)
    assert "1 个 `manifest.json` 未能读取" in text
    assert "/store/data/raw/x/manifest.json" in text
    assert "not valid JSON" in text
