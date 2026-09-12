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
    exit_code,
    main,
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


def test_report_declares_incompleteness_only_when_it_is_incomplete():
    """A complete audit must not be able to look partial, or vice versa."""
    skipped = [
        SkippedManifest(
            path=Path("/store/data/raw/x/manifest.json"), reason="not valid JSON"
        )
    ]
    incomplete = report([], skipped=skipped)
    assert "本次审计不完整" in incomplete
    # In 结论, not only in the trailing listing: a reader who stops at the
    # conclusion still has to learn the inventory is partial.
    assert incomplete.index("本次审计不完整") < incomplete.index(
        "## 未能读取的 manifest"
    )

    complete = report(
        [SnapshotRecord("tushare", "daily", "tushare.pro.daily", "1.4.29", None, "e")]
    )
    assert "本次审计不完整" not in complete


def _scan_exit_code(root: Path) -> int:
    """The exit status ``main`` would return for a store, driving ``scan``."""
    result = scan(root)
    return exit_code(result.records, result.skipped)


def test_exit_code_is_zero_for_a_store_whose_manifests_all_read(tmp_path):
    _write_manifest(
        tmp_path / "data" / "raw",
        "tushare",
        "daily",
        "rk",
        "aa" * 32,
        manifest=TUSHARE_MANIFEST,
    )
    assert _scan_exit_code(tmp_path) == 0


def test_exit_code_is_one_for_an_empty_or_absent_store(tmp_path):
    # Store absent entirely...
    assert _scan_exit_code(tmp_path) == 1
    # ...and store present but with nothing under it: both are "found nothing".
    (tmp_path / "data" / "raw").mkdir(parents=True)
    assert _scan_exit_code(tmp_path) == 1


def test_exit_code_is_two_when_every_manifest_is_unreadable(tmp_path):
    raw = tmp_path / "data" / "raw"
    for name in ("aa" * 32, "bb" * 32):
        broken = raw / "tushare" / "daily" / "rk" / name
        broken.mkdir(parents=True)
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    # The all-unreadable store is *not* an empty store: 2, not 1.
    assert _scan_exit_code(tmp_path) == 2


def test_exit_code_is_two_when_only_some_manifests_are_readable(tmp_path):
    """Partial readability must not masquerade as a complete inventory."""
    raw = tmp_path / "data" / "raw"
    _write_manifest(raw, "tushare", "daily", "rk", "aa" * 32, manifest=TUSHARE_MANIFEST)
    broken = raw / "tushare" / "daily" / "rk" / ("bb" * 32)
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    assert _scan_exit_code(tmp_path) == 2


def test_main_writes_an_incomplete_report_and_returns_2(tmp_path, monkeypatch, capsys):
    """The load-bearing claim: exit 2 still writes the report, paths and all."""
    broken = tmp_path / "data" / "raw" / "tushare" / "daily" / "rk" / ("cc" * 32)
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    out = tmp_path / "out.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_raw_provenance.py",
            "--root",
            str(tmp_path),
            "--report",
            str(out),
        ],
    )

    code = main()

    assert code == 2
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "本次审计不完整" in text
    assert str(broken / "manifest.json") in text
    assert "not valid JSON" in text
    assert f"report: {out}" in capsys.readouterr().out


def test_main_finds_nothing_prints_the_hint_and_writes_no_report(
    tmp_path, monkeypatch, capsys
):
    """Exit 1 must not overwrite the committed report with a "0 snapshots" one."""
    out = tmp_path / "out.md"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_raw_provenance.py",
            "--root",
            str(tmp_path),
            "--report",
            str(out),
        ],
    )

    code = main()

    assert code == 1
    assert not out.exists()
    assert f"no snapshots found under {tmp_path / 'data' / 'raw'}" in (
        capsys.readouterr().out
    )
