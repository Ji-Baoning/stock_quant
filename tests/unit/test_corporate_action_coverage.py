"""Per-symbol corporate-action coverage evidence builder tests (Task 1).

``coverage_frame`` renders coverage records (symbol, window, status, reason,
source outcomes, snapshot hashes and a deterministic ``checked_at``) into the
standardized ``CORPORATE_ACTION_COVERAGE_COLUMNS`` layout.  A single test pins
the load-bearing distinction: an explicit successful no-event response is
``VERIFIED_EMPTY`` and is never confused with an ``UNTRUSTED`` fetch failure.
"""

from __future__ import annotations

from datetime import date

from stock_quant.data_model.corporate_action_coverage import (
    CORPORATE_ACTION_COVERAGE_COLUMNS,
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)


def test_coverage_distinguishes_verified_empty_from_failure():
    frame = coverage_frame([
        coverage_record("600000.SH", date(2024, 1, 1), date(2024, 12, 31),
                        CoverageStatus.VERIFIED_EMPTY),
        coverage_record("600001.SH", date(2024, 1, 1), date(2024, 12, 31),
                        CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_FETCH_FAILED),
    ])
    assert list(frame.columns) == CORPORATE_ACTION_COVERAGE_COLUMNS
    assert frame.status.tolist() == ["VERIFIED_EMPTY", "UNTRUSTED"]
