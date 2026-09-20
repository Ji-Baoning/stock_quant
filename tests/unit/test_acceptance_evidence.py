"""Unit tests for the version-bound acceptance evidence pack."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.research.acceptance import evidence
from stock_quant.research.acceptance.checks import _window
from stock_quant.research.acceptance.evidence import (
    EVIDENCE_DIRNAME,
    MECHANISABLE_CODES,
    OPERATOR_ONLY_CODES,
    EvidenceBuildError,
    EvidenceFile,
    benchmark_evidence,
    build_mechanisable_evidence,
    corporate_action_evidence,
    evidence_window,
    missing_reason_evidence,
    secret_scan_evidence,
    security_master_evidence,
    source_row_count_evidence,
)

DAYS = [date(2015, 1, 5), date(2015, 1, 6), date(2015, 1, 7)]


def _calendar(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {"calendar_date": days, "is_trading_day": [True] * len(days)}
    )


def test_evidence_reexports_the_manual_check_vocabularies() -> None:
    """The module republishes the model layer's manual-check vocabulary."""
    assert evidence.MECHANISABLE_CODES is MECHANISABLE_CODES
    assert evidence.OPERATOR_ONLY_CODES is OPERATOR_ONLY_CODES


def test_source_row_count_evidence_lists_tables_and_snapshots() -> None:
    manifest = {"tables": {"daily_bar": {"row_count": 7}}}
    empty = source_row_count_evidence(manifest, (), trading_days=3)
    assert empty.name == "source_row_counts.json"
    payload = json.loads(empty.text)
    assert payload["tables"] == {"daily_bar": 7}
    assert payload["trading_days"] == 3
    assert payload["raw_snapshots"] == []


def test_source_row_count_evidence_reads_manifest_snapshot_mappings() -> None:
    """Snapshots arrive as the manifest's dicts, never as model objects."""
    manifest = {"tables": {"daily_bar": {"row_count": 7}}}
    snapshot = {
        "source": "tushare",
        "endpoint": "daily",
        "request_key": "600519.SH",
        "file_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
    }
    payload = json.loads(
        source_row_count_evidence(manifest, [snapshot], trading_days=3).text
    )
    assert payload["raw_snapshots"] == [
        {
            "source": "tushare",
            "endpoint": "daily",
            "request_key": "600519.SH",
            "file_sha256": "a" * 64,
        }
    ]


def test_missing_reason_evidence_counts_accepted_classifications() -> None:
    master = pd.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "list_date": [pd.Timestamp("2015-01-06")],
            "delist_date": [pd.NaT],
        }
    )
    daily = pd.DataFrame(
        {
            "symbol": ["000001.SZ", "000001.SZ"],
            "trade_date": [pd.Timestamp(DAYS[1]), pd.Timestamp(DAYS[2])],
        }
    )
    payload = json.loads(
        missing_reason_evidence(
            daily, master, _calendar(DAYS), DAYS[0], DAYS[-1]
        ).text
    )
    assert payload["counts"] == {"not_listed": 1}
    assert payload["first_sample"] == {"not_listed": "000001.SZ@2015-01-05"}
    assert payload["window"]["start"] == "2015-01-05"


def test_security_master_evidence_is_symbol_sorted_csv() -> None:
    master = pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "list_date": [pd.Timestamp("2001-08-27"), pd.Timestamp("1991-04-03")],
            "delist_date": [pd.NaT, pd.NaT],
        }
    )
    text = security_master_evidence(master).text
    assert text.splitlines()[0] == "symbol,list_date,delist_date"
    assert text.splitlines()[1].startswith("000001.SZ")


def test_benchmark_evidence_counts_covered_open_days() -> None:
    daily = pd.DataFrame(
        {
            "symbol": ["000300.SH", "000300.SH"],
            "trade_date": [pd.Timestamp(DAYS[0]), pd.Timestamp(DAYS[2])],
        }
    )
    payload = json.loads(
        benchmark_evidence(
            daily, _calendar(DAYS), ("000300.SH",), DAYS[0], DAYS[-1]
        ).text
    )
    assert payload["window"] == {"start": "2015-01-05", "end": "2015-01-07"}
    assert payload["coverage"]["000300.SH"]["rows"] == 2
    assert payload["coverage"]["000300.SH"]["open_days"] == 3
    assert payload["coverage"]["000300.SH"]["missing_open_days"] == 1


def test_corporate_action_evidence_samples_facts_in_order() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["600519.SH", "000001.SZ"],
            "ex_date": [pd.Timestamp("2024-04-30"), pd.Timestamp("2021-06-11")],
            "status": ["implemented", "implemented"],
        }
    )
    text = corporate_action_evidence(frame).text
    assert text.splitlines()[1].startswith("000001.SZ")


def test_secret_scan_flags_a_credential_like_line() -> None:
    payload = json.loads(
        secret_scan_evidence(
            [
                EvidenceFile("clean.txt", "nothing to see\n"),
                EvidenceFile("leaky.txt", "TUSHARE_TOKEN=abcdef\n"),
            ]
        ).text
    )
    assert payload["scanned"] == ["clean.txt", "leaky.txt"]
    assert payload["hits"] == [{"path": "leaky.txt", "line": 1}]


def test_evidence_window_uses_the_requested_start_the_checks_use() -> None:
    """The manual evidence window is the automated check's window, exactly.

    ``effective_start_date`` is deliberately ignored: a request that started
    before the data does must show up as absent bars, not silently shrink the
    window a human is signing off on.
    """
    build = {
        "requested_start_date": "2015-01-05",
        "effective_start_date": "2015-01-06",
        "resolved_end_date": "2026-08-28",
    }
    assert evidence_window({"build_config": build}) == _window(build)
    assert evidence_window({"build_config": build}) == (
        date(2015, 1, 5),
        date(2026, 8, 28),
    )


def test_evidence_window_is_missing_without_a_build_window() -> None:
    for manifest in ({}, {"build_config": "broken"}):
        with pytest.raises(EvidenceBuildError) as captured:
            evidence_window(manifest)
        assert captured.value.category == "window_missing"


def test_evidence_window_names_a_missing_acceptance_anchor() -> None:
    """An empty build names neither window start: the dedicated category.

    The anchor-missing path (spec §0-12) must be distinguishable from a
    malformed window, so a checklist row can say exactly what is absent.
    """
    with pytest.raises(EvidenceBuildError) as captured:
        evidence_window({"build_config": {}})
    assert captured.value.category == "full_history_acceptance_start_missing"


def test_build_mechanisable_evidence_fails_loudly_on_a_missing_version(
    tmp_path: Path,
) -> None:
    with pytest.raises(EvidenceBuildError) as captured:
        build_mechanisable_evidence(tmp_path, "0" * 64)
    assert captured.value.category == "dataset_unreadable"


def test_failed_staging_leaves_the_previous_pack_untouched(
    tmp_path, monkeypatch
) -> None:
    """A pack is replaced whole: a failure never yields a half-written pack."""
    pack = tmp_path / "data" / EVIDENCE_DIRNAME / "v1"
    pack.mkdir(parents=True)
    (pack / "sentinel.txt").write_text("old pack", encoding="utf-8")

    def half_written(staging, files):
        (staging / files[0].name).write_text(files[0].text, encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(evidence, "_stage_files", half_written)
    with pytest.raises(EvidenceBuildError) as captured:
        evidence._write_pack(
            tmp_path, "v1", [EvidenceFile("a.json", "{}\n")]
        )
    assert captured.value.category == "evidence_write_failed"
    assert (pack / "sentinel.txt").read_text(encoding="utf-8") == "old pack"
    assert sorted(item.name for item in pack.parent.iterdir()) == ["v1"]


def test_evidence_build_error_rejects_an_unknown_category() -> None:
    with pytest.raises(ValueError):
        EvidenceBuildError("something_else")
