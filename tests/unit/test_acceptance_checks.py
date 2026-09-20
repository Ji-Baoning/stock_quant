"""Unit behaviour of the offline automated acceptance checks (Task 4).

The runner is exercised over bare ``tmp_path`` evidence trees: missing
datasets, corrupt manifest JSON and an unreadable quality report must each map
to exception-safe ``FAIL`` results whose details carry only the exception
class name -- never ``str(error)``, so supplier paths or secrets cannot leak.
``dataset_evidence`` pre-verification (version binding, path-escape rejection,
hashing, snapshot-binding parsing) is pinned against hand-written manifests,
so no DuckDB reader or pipeline is needed here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_quant.research.acceptance.checks import (
    AcceptanceCheckInput,
    _check_table_fetch_coverage,
    _open_days,
    dataset_evidence,
    run_automated_checks,
)
from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    CheckStatus,
)


def _input(root: Path) -> AcceptanceCheckInput:
    return AcceptanceCheckInput(
        project_root=root, dataset_version="a" * 64
    )


def _write_dataset(root: Path, manifest: dict) -> Path:
    """Materialise a minimal dataset directory holding one manifest."""
    dataset = root / "data" / "standardized" / ("a" * 64)
    dataset.mkdir(parents=True)
    (dataset / "dataset_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return dataset


def _minimal_manifest() -> dict:
    return {"dataset_version": "a" * 64, "tables": {}}


# --------------------------------------------------------------------------- #
# Exception-safe runner mapping
# --------------------------------------------------------------------------- #


def test_runner_returns_every_code_in_policy_order(tmp_path):
    results = run_automated_checks(_input(tmp_path))
    assert [row.code for row in results] == list(AUTOMATED_CHECK_CODES)
    assert all(row.status is CheckStatus.FAIL for row in results)
    assert all(
        row.details == {"error_code": "FileNotFoundError"} for row in results
    )
    assert all(
        row.summary == f"{row.code} could not be verified" for row in results
    )


def test_runner_details_are_deterministic_and_redacted(tmp_path):
    first = run_automated_checks(_input(tmp_path))
    second = run_automated_checks(_input(tmp_path))
    assert first == second
    for row in first:
        payload = json.dumps(row.model_dump(mode="json"), sort_keys=True)
        assert str(tmp_path) not in payload
        assert "Traceback" not in payload
        assert "[Errno" not in payload


def test_corrupt_manifest_json_maps_to_decode_error(tmp_path):
    dataset = _write_dataset(tmp_path, _minimal_manifest())
    (dataset / "dataset_manifest.json").write_text("{not json", encoding="utf-8")
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")
    checks = {row.code: row for row in run_automated_checks(_input(tmp_path))}
    manifest_check = checks["dataset_manifest_integrity"]
    assert manifest_check.status is CheckStatus.FAIL
    assert manifest_check.details == {"error_code": "JSONDecodeError"}


def test_unreadable_quality_report_maps_to_file_not_found(tmp_path):
    _write_dataset(tmp_path, _minimal_manifest())
    checks = {row.code: row for row in run_automated_checks(_input(tmp_path))}
    manifest_check = checks["dataset_manifest_integrity"]
    assert manifest_check.status is CheckStatus.FAIL
    assert manifest_check.details == {"error_code": "FileNotFoundError"}


def test_runner_lets_programming_errors_propagate(tmp_path, monkeypatch):
    import stock_quant.research.acceptance.checks as checks_module

    def boom(value):
        raise TypeError("programming error")

    monkeypatch.setattr(checks_module, "_check_dataset_manifest", boom)
    with pytest.raises(TypeError, match="programming error"):
        run_automated_checks(_input(tmp_path))


# --------------------------------------------------------------------------- #
# Trading-calendar flag coercion
# --------------------------------------------------------------------------- #


def test_open_days_treats_missing_and_nan_flags_as_closed():
    calendar = pd.DataFrame(
        {
            "calendar_date": [
                pd.Timestamp("2021-11-01"),
                pd.Timestamp("2021-11-02"),
                pd.Timestamp("2021-11-03"),
                pd.Timestamp("2021-11-04"),
            ],
            "is_trading_day": np.array(
                [True, pd.NA, np.nan, False], dtype=object
            ),
        }
    )
    assert _open_days(calendar) == [date(2021, 11, 1)]


def test_open_days_keeps_ordinary_boolean_columns():
    calendar = pd.DataFrame(
        {
            "calendar_date": [
                pd.Timestamp("2021-11-01"),
                pd.Timestamp("2021-11-02"),
            ],
            "is_trading_day": np.array([np.True_, np.False_]),
        }
    )
    assert _open_days(calendar) == [date(2021, 11, 1)]


# --------------------------------------------------------------------------- #
# dataset_evidence pre-verification
# --------------------------------------------------------------------------- #


def test_dataset_evidence_hashes_and_binds_snapshots(tmp_path):
    dataset = _write_dataset(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {},
            "build_config": {
                "raw_snapshots": [
                    {
                        "source": "tushare",
                        "endpoint": "daily",
                        "request_key": "k1",
                        "file_sha256": "b" * 64,
                        "manifest_sha256": "c" * 64,
                    }
                ]
            },
        },
    )
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")
    evidence = dataset_evidence(_input(tmp_path))
    manifest_bytes = (dataset / "dataset_manifest.json").read_bytes()
    quality_bytes = (dataset / "quality_report.json").read_bytes()
    assert evidence.dataset_manifest_sha256 == hashlib.sha256(
        manifest_bytes
    ).hexdigest()
    assert evidence.quality_report_sha256 == hashlib.sha256(
        quality_bytes
    ).hexdigest()
    assert evidence.dataset_path == dataset
    assert [row.request_key for row in evidence.raw_snapshot_evidence] == ["k1"]


def test_dataset_evidence_rejects_version_mismatch(tmp_path):
    dataset = _write_dataset(tmp_path, _minimal_manifest())
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")
    stray = dict(_minimal_manifest())
    stray["dataset_version"] = "b" * 64
    (dataset / "dataset_manifest.json").write_text(
        json.dumps(stray), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="version mismatch"):
        dataset_evidence(_input(tmp_path))


def test_dataset_evidence_rejects_escaping_table_path(tmp_path):
    dataset = _write_dataset(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {"daily_bar": {"path": "../escape.parquet"}},
        },
    )
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes the dataset directory"):
        dataset_evidence(_input(tmp_path))


# --------------------------------------------------------------------------- #
# table_fetch_coverage_evidence (B2/T13)
# --------------------------------------------------------------------------- #


def _write_build_manifest(tmp_path: Path, build: dict) -> None:
    dataset = _write_dataset(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {},
            "build_config": build,
        },
    )
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")


# The check is exercised directly: ``run_automated_checks`` over a bare
# evidence tree escapes through ``_check_quality_report``'s project-config
# load (a config fault is a programming error by policy), so the runner-level
# path belongs to the integration suite over real fixture projects.


def test_table_fetch_coverage_evidence_passes_contiguous_payload(tmp_path):
    _write_build_manifest(
        tmp_path,
        {
            "origin": "data_update",
            "full_history_acceptance_start": "2021-11-01",
            "resolved_end_date": "2021-11-30",
            "table_fetch_coverage": {
                "daily_bar": [
                    {
                        "table": "daily_bar",
                        "kind": "carried",
                        "window_start": "2021-11-01",
                        "window_end": "2021-11-29",
                    },
                    {
                        "table": "daily_bar",
                        "kind": "fetched",
                        "window_start": "2021-11-30",
                        "window_end": "2021-11-30",
                    },
                ]
            },
        },
    )
    check = _check_table_fetch_coverage(_input(tmp_path))
    assert check.status is CheckStatus.PASS


def test_table_fetch_coverage_evidence_fails_without_payload(tmp_path):
    _write_build_manifest(
        tmp_path,
        {
            "origin": "data_update",
            "full_history_acceptance_start": "2021-11-01",
            "resolved_end_date": "2021-11-30",
        },
    )
    check = _check_table_fetch_coverage(_input(tmp_path))
    assert check.status is CheckStatus.FAIL
    assert check.details["code"] == "table_fetch_coverage_missing"


def test_table_fetch_coverage_evidence_fails_on_window_gap(tmp_path):
    _write_build_manifest(
        tmp_path,
        {
            "origin": "data_update",
            "full_history_acceptance_start": "2021-11-01",
            "resolved_end_date": "2021-11-30",
            "table_fetch_coverage": {
                "daily_bar": [
                    {
                        "table": "daily_bar",
                        "kind": "fetched",
                        "window_start": "2021-11-15",
                        "window_end": "2021-11-30",
                    }
                ]
            },
        },
    )
    check = _check_table_fetch_coverage(_input(tmp_path))
    assert check.status is CheckStatus.FAIL
    assert check.details["code"] == "fetch_coverage_gap"


def test_table_fetch_coverage_evidence_requires_build_config(tmp_path):
    dataset = _write_dataset(tmp_path, _minimal_manifest())
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")
    check = _check_table_fetch_coverage(_input(tmp_path))
    assert check.status is CheckStatus.FAIL
    assert ["dataset_build_evidence_missing", "build_config"] in (
        check.details["failures"]
    )
