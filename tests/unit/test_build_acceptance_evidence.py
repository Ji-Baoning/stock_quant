"""Unit tests for the scripted acceptance evidence pack."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

from build_acceptance_evidence import (  # noqa: E402
    MECHANISABLE,
    OPERATOR_ONLY,
    apply_evidence,
    benchmark_evidence,
    corporate_action_evidence,
    missing_reason_evidence,
    secret_scan_evidence,
    security_master_evidence,
    source_row_count_evidence,
)

from stock_quant.research.acceptance.models import MANUAL_CHECK_CODES  # noqa: E402

DAYS = [date(2015, 1, 5), date(2015, 1, 6), date(2015, 1, 7)]


def _calendar(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {"calendar_date": days, "is_trading_day": [True] * len(days)}
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_mechanisable_and_operator_only_tile_the_policy_vocabulary() -> None:
    assert set(MECHANISABLE) | set(OPERATOR_ONLY) == set(MANUAL_CHECK_CODES)
    assert set(MECHANISABLE) & set(OPERATOR_ONLY) == set()


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
    assert payload["000300.SH"]["rows"] == 2
    assert payload["000300.SH"]["open_days"] == 3
    assert payload["000300.SH"]["missing_open_days"] == 1


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


def test_secret_scan_flags_a_credential_like_line(tmp_path: Path) -> None:
    (tmp_path / "clean.txt").write_text("nothing to see\n", encoding="utf-8")
    (tmp_path / "leaky.txt").write_text(
        "TUSHARE_TOKEN=abcdef\n", encoding="utf-8"
    )
    payload = json.loads(
        secret_scan_evidence(
            [tmp_path / "clean.txt", tmp_path / "leaky.txt"], root=tmp_path
        ).text
    )
    assert payload["scanned"] == 2
    assert payload["hits"] == [{"path": "leaky.txt", "line": 1}]


def test_apply_evidence_passes_scripted_rows_and_leaves_the_rest(
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    (evidence_root / "pack.json").write_text("{}\n", encoding="utf-8")
    checklist = {
        "manual_checks": [
            {"code": "secret_scan", "status": "FAIL", "summary": "x"},
            {"code": "benchmark_sample", "status": "FAIL", "summary": "x"},
        ]
    }
    patched = apply_evidence(
        checklist,
        root=tmp_path,
        evidence_root=evidence_root,
        names={"secret_scan": "pack.json"},
    )
    rows = {row["code"]: row for row in patched["manual_checks"]}
    assert rows["benchmark_sample"]["status"] == "FAIL"
    assert rows["secret_scan"]["status"] == "PASS"
    reference = rows["secret_scan"]["evidence"][0]
    assert reference["kind"] == "local"
    assert reference["reference"] == "evidence/pack.json"
    assert reference["sha256"] == _sha256("{}\n")
