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
import yaml

from stock_quant.research.acceptance.checks import (
    AcceptanceCheckInput,
    _check_required_tables,
    _check_table_fetch_coverage,
    _check_table_lineage,
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


def test_a_published_table_without_a_coverage_record_fails(tmp_path):
    """Every published table must carry its own fetch-coverage record.

    The recorded ``daily_bar`` payload tiles the review window, so the only
    failure is the published ``trading_calendar`` the build evidence never
    mentions (spec §7.5.6: one coverage record per published table).
    """
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
    dataset = tmp_path / "data" / "standardized" / ("a" * 64)
    manifest = json.loads(
        (dataset / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    manifest["tables"] = {
        "daily_bar": {"path": "daily_bar.parquet"},
        "trading_calendar": {"path": "trading_calendar.parquet"},
    }
    (dataset / "dataset_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    check = _check_table_fetch_coverage(_input(tmp_path))
    assert check.status is CheckStatus.FAIL
    assert check.details["code"] == "table_fetch_coverage_table_unrecorded"
    assert ["table_fetch_coverage_table_unrecorded", "trading_calendar"] in (
        check.details["failures"]
    )


def test_required_tables_no_longer_judge_by_the_current_registry(tmp_path):
    """``required_table_coverage`` judges the manifest's own recorded tables.

    Re-reviewing an old version must not fail because a later release
    registered new tables: a manifest whose table set sits outside the
    current ``STANDARDIZED_SCHEMAS`` registry passes, while a table the
    manifest records in its own build evidence without publishing it still
    fails (spec §7.5.6 moved registry completeness to the publish gate).
    """
    dataset = _write_dataset(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {
                "legacy_extra_table": {"path": "legacy_extra_table.parquet"}
            },
        },
    )
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")
    check = _check_required_tables(_input(tmp_path))
    assert check.status is CheckStatus.PASS

    # A table the manifest itself records without publishing it still fails.
    stray = {
        "dataset_version": "a" * 64,
        "tables": {
            "legacy_extra_table": {"path": "legacy_extra_table.parquet"}
        },
        "build_config": {"table_fetch_coverage": {"daily_bar": []}},
    }
    (dataset / "dataset_manifest.json").write_text(
        json.dumps(stray), encoding="utf-8"
    )
    check = _check_required_tables(_input(tmp_path))
    assert check.status is CheckStatus.FAIL
    assert ["missing_required_table", "daily_bar"] in check.details["failures"]


def test_required_tables_reads_legacy_manifest_without_table_lineage(tmp_path):
    """A build_config without ``table_lineage`` is read in its legacy form.

    Dataset versions recorded before per-table lineage existed carry only
    ``tables`` and ``table_fetch_coverage``: the check must neither error on
    the absent key nor invent a missing-table failure from it, and must judge
    the manifest by its own recorded sets (spec §7.4 re-review of old
    versions after newer releases registered further tables).
    """
    dataset = _write_dataset(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {
                "daily_bar": {"path": "daily_bar.parquet"},
                "trading_calendar": {"path": "trading_calendar.parquet"},
            },
            "build_config": {
                "origin": "data_update",
                "table_fetch_coverage": {
                    "daily_bar": [],
                    "trading_calendar": [],
                },
            },
        },
    )
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")
    check = _check_required_tables(_input(tmp_path))
    assert check.status is CheckStatus.PASS
    assert check.details == {}


# --------------------------------------------------------------------------- #
# table_lineage_evidence (P2c Task 4)
# --------------------------------------------------------------------------- #


def _write_contracts_project(root: Path) -> None:
    """A minimal project root whose contracts declare the basic_factor pair.

    ``table_lineage_evidence`` judges a recorded lineage row against the
    table contract's ``primary_transport``, so the evidence tree needs a
    project config to read the declarations from -- the same config path the
    other config-reading checks take (``_check_quality_report``).
    """
    configs = root / "configs"
    configs.mkdir()
    (configs / "project.yml").write_text(
        yaml.safe_dump(
            {
                "start_date": "2020-01-01",
                "end_date": "2020-01-31",
                "initial_cash": 1000000,
                "benchmark_symbols": ["000300.SH"],
            }
        ),
        encoding="utf-8",
    )
    (configs / "sources.yml").write_text(
        yaml.safe_dump(
            {
                "data_contracts": [
                    {
                        "table": "basic_factor",
                        "tier": "research_only",
                        "primary_transport": "tushare:relay",
                        "anchors": [],
                        "conflict": "block",
                        "pit": None,
                        "coverage_shape": "per_symbol_window",
                        "incremental": "last_covered_plus_1",
                    },
                    {
                        "table": "basic_factor_coverage",
                        "tier": "core",
                        "primary_transport": "tushare:relay",
                        "anchors": [],
                        "conflict": "block",
                        "pit": None,
                        "coverage_shape": "none",
                        "incremental": "last_covered_plus_1",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (configs / "costs.yml").write_text("", encoding="utf-8")


def _lineage_row(transport: str) -> dict:
    return {
        "table": "basic_factor",
        "transport": transport,
        "raw_snapshot": {
            "source": "tushare",
            "endpoint": "daily_basic",
            "transport_id": "tushare",
            "request_key": "k1",
            "file_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
        },
    }


def _write_lineage_manifest(tmp_path: Path, manifest: dict) -> None:
    dataset = _write_dataset(tmp_path, manifest)
    (dataset / "quality_report.json").write_text("{}\n", encoding="utf-8")


def test_table_lineage_evidence_passes_consistent_lineage(tmp_path):
    """A recorded lineage row consistent with the declaration passes.

    ``basic_factor`` is declared ``tushare:relay`` with a
    ``per_symbol_window`` coverage shape: the row names the same transport
    and the manifest publishes ``basic_factor_coverage``, so the check has
    nothing to flag.
    """
    _write_contracts_project(tmp_path)
    _write_lineage_manifest(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {
                "basic_factor": {"path": "basic_factor.parquet"},
                "basic_factor_coverage": {
                    "path": "basic_factor_coverage.parquet"
                },
            },
            "build_config": {
                "origin": "data_update",
                "pipeline_contract_version": 1,
                "table_lineage": {
                    "basic_factor": _lineage_row("tushare:relay")
                },
            },
        },
    )
    check = _check_table_lineage(_input(tmp_path))
    assert check.status is CheckStatus.PASS
    assert check.details == {}


def test_table_lineage_evidence_fails_on_transport_mismatch(tmp_path):
    """A recorded transport that differs from the declaration fails."""
    _write_contracts_project(tmp_path)
    _write_lineage_manifest(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {
                "basic_factor": {"path": "basic_factor.parquet"},
                "basic_factor_coverage": {
                    "path": "basic_factor_coverage.parquet"
                },
            },
            "build_config": {
                "origin": "data_update",
                "pipeline_contract_version": 1,
                "table_lineage": {
                    "basic_factor": _lineage_row("tushare:proxy")
                },
            },
        },
    )
    check = _check_table_lineage(_input(tmp_path))
    assert check.status is CheckStatus.FAIL
    assert ["table_lineage_transport_mismatch", "basic_factor"] in (
        check.details["failures"]
    )


def test_table_lineage_evidence_fails_on_missing_coverage_table(tmp_path):
    """A published per-symbol-window table without its coverage table fails.

    The lineage row itself matches the declaration; the failure is the
    absent ``basic_factor_coverage`` the ``per_symbol_window`` shape owes
    the manifest.
    """
    _write_contracts_project(tmp_path)
    _write_lineage_manifest(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {"basic_factor": {"path": "basic_factor.parquet"}},
            "build_config": {
                "origin": "data_update",
                "pipeline_contract_version": 1,
                "table_lineage": {
                    "basic_factor": _lineage_row("tushare:relay")
                },
            },
        },
    )
    check = _check_table_lineage(_input(tmp_path))
    assert check.status is CheckStatus.FAIL
    assert ["coverage_table_missing", "basic_factor"] in (
        check.details["failures"]
    )


def test_table_lineage_evidence_reads_legacy_manifest_without_the_key(tmp_path):
    """A build_config without ``table_lineage`` passes compatibly.

    Dataset versions recorded before per-table lineage existed carry no
    ``table_lineage`` key: the check reads them in their legacy form and
    passes without error -- the remedy for such a version is republish
    under the current build evidence, never a standing exemption, and the
    build-config contract version stays ``1``.
    """
    _write_contracts_project(tmp_path)
    _write_lineage_manifest(
        tmp_path,
        {
            "dataset_version": "a" * 64,
            "tables": {
                "basic_factor": {"path": "basic_factor.parquet"},
                "basic_factor_coverage": {
                    "path": "basic_factor_coverage.parquet"
                },
            },
            "build_config": {
                "origin": "data_update",
                "pipeline_contract_version": 1,
            },
        },
    )
    check = _check_table_lineage(_input(tmp_path))
    assert check.status is CheckStatus.PASS
    assert check.details == {}
