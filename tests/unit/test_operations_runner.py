"""Pure surfaces of the UpdateRunner: argv building, CLI-mirroring
parameter validation, exit-code mapping and the success contract-line
parser (spec 9.1).  The orchestration itself is exercised with real and
fake child processes in tests/integration/test_operations_cli.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stock_quant.operations.runner import (
    HEARTBEAT_INTERVAL_SECONDS,
    InvalidUpdateParams,
    OperationsUpdateResult,
    build_data_update_argv,
    build_operations_update_argv,
    classify_child_exit,
    operations_exit_code,
    parse_contract_lines,
    read_boot_id,
    validate_update_params,
)
from stock_quant.operations.update_lock import UPDATE_ALREADY_RUNNING_EXIT_CODE


def test_the_inner_argv_is_an_argument_array_with_only_allowed_options():
    argv = build_data_update_argv(
        Path("/tmp/p"),
        validate_update_params(
            start="2026-09-01",
            end="2026-09-30",
            sources="tushare, akshare",
            disclosure_lookback_days=120,
        ),
    )
    assert argv == [
        sys.executable,
        "-m",
        "stock_quant",
        "data",
        "update",
        "--root",
        "/tmp/p",
        "--start",
        "2026-09-01",
        "--end",
        "2026-09-30",
        "--sources",
        "tushare,akshare",
        "--disclosure-lookback-days",
        "120",
    ]


def test_no_job_id_or_shell_string_ever_enters_the_spawned_argv():
    # The job id must never reach the update path (spec 9.2: job ids do not
    # enter dataset versions), and no argv is ever a shell string.
    argv = build_data_update_argv(Path("/tmp/p"), validate_update_params())
    assert isinstance(argv, list)
    assert all(isinstance(item, str) for item in argv)
    assert not any("job_" in item for item in argv)
    outer = build_operations_update_argv(
        Path("/tmp/p"), validate_update_params(), job_id="job_20261001T1010Z_abcdef12"
    )
    assert outer[-2:] == ["--job-id", "job_20261001T1010Z_abcdef12"]
    assert not any("job_" in item for item in outer[:-2])


def test_params_reject_what_the_cli_would_reject():
    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(start="2026-9-1")
    assert error.value.parameter == "start"

    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(end="not-a-date")
    assert error.value.parameter == "end"

    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(disclosure_lookback_days=0)
    assert error.value.parameter == "disclosure_lookback_days"

    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(disclosure_lookback_days=True)
    assert error.value.parameter == "disclosure_lookback_days"

    with pytest.raises(InvalidUpdateParams) as error:
        validate_update_params(sources="tushare,,akshare")
    assert error.value.parameter == "sources"


def test_params_accept_the_cli_shape_everywhere():
    params = validate_update_params(
        sources=["tushare", "akshare"], disclosure_lookback_days=1
    )
    assert params.sources == ("tushare", "akshare")
    assert validate_update_params() == validate_update_params(sources=None)


def test_exit_codes_map_onto_the_job_vocabulary():
    assert classify_child_exit(0) == ("SUCCEEDED", None)
    assert classify_child_exit(UPDATE_ALREADY_RUNNING_EXIT_CODE) == (
        "FAILED",
        "update_already_running",
    )
    assert classify_child_exit(1) == ("FAILED", "update_failed")
    assert classify_child_exit(2) == ("FAILED", "update_failed")


def test_contract_line_parsing_reads_only_stable_keys():
    text = (
        "INFO stock_quant.data_sources.tushare: resolved transport=relay\n"
        "run_id=data_update_abc123\n"
        "resolved_end_date=2026-09-30\n"
        "some free prose about a failure, run_id= not at line start anyway\n"
        "dataset_version="
        + "ab"
        * 32
        + "\n"
        "PASS\n"
    )
    assert parse_contract_lines(text) == {
        "run_id": "data_update_abc123",
        "resolved_end_date": "2026-09-30",
        "dataset_version": "ab" * 32,
    }


def test_operations_exit_code_mirrors_success_and_conflict():
    assert (
        operations_exit_code(
            OperationsUpdateResult(job_id="j", status="SUCCEEDED", exit_code=0)
        )
        == 0
    )
    conflict = OperationsUpdateResult(
        job_id="j",
        status="FAILED",
        exit_code=75,
        failure_reason="update_already_running",
    )
    assert operations_exit_code(conflict) == UPDATE_ALREADY_RUNNING_EXIT_CODE
    failed = OperationsUpdateResult(
        job_id="j", status="FAILED", exit_code=1, failure_reason="update_failed"
    )
    assert operations_exit_code(failed) == 1


def test_boot_id_reads_the_kernel_value(tmp_path):
    boot_file = tmp_path / "boot_id"
    boot_file.write_text("2fdf3cf0-97bf-4896-bd6f-1a5b8841637d\n", encoding="utf-8")
    assert read_boot_id(boot_file) == "2fdf3cf0-97bf-4896-bd6f-1a5b8841637d"


def test_the_heartbeat_cadence_is_well_under_the_orphan_threshold():
    assert HEARTBEAT_INTERVAL_SECONDS == 30.0
