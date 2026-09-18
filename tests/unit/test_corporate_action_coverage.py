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
from stock_quant.data_model.corporate_actions import (
    REASON_CROSS_SOURCE_CONFLICT,
    REASON_INCOMPLETE,
    REASON_NON_DISTRIBUTIVE_RESTRUCTURING,
)

# The verdict is the gate that turns a quarantine reason into a symbol/window's
# trust; it is exercised here directly because the pipeline-level tests cannot
# cheaply build one symbol per reason.
from stock_quant.data_pipeline import _coverage_verdict

_FETCHED = {"cninfo": {"ok": True, "empty": False}}


def test_coverage_distinguishes_verified_empty_from_failure():
    frame = coverage_frame([
        coverage_record("600000.SH", date(2024, 1, 1), date(2024, 12, 31),
                        CoverageStatus.VERIFIED_EMPTY),
        coverage_record("600001.SH", date(2024, 1, 1), date(2024, 12, 31),
                        CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_FETCH_FAILED),
    ])
    assert list(frame.columns) == CORPORATE_ACTION_COVERAGE_COLUMNS
    assert frame.status.tolist() == ["VERIFIED_EMPTY", "UNTRUSTED"]


def test_refused_non_distributive_event_does_not_untrust_the_window():
    """A 重整转增 is a real, correctly reported event -- not a gap.

    Reading it as ``incomplete`` marked every symbol that had one UNTRUSTED for
    the whole window, blocking holdings of the six symbols that carry one even
    though their other events were accepted and their prices are unaffected.
    """
    status, reason = _coverage_verdict(
        _FETCHED, has_accepted=True,
        quarantine_reasons={REASON_NON_DISTRIBUTIVE_RESTRUCTURING},
    )
    assert status is CoverageStatus.VERIFIED
    assert reason is None


def test_unaccounted_quarantine_reasons_still_untrust_the_window():
    """The exemption is a named deny-list, so new reasons keep failing closed."""
    for reasons, expected in (
        ({REASON_INCOMPLETE}, CoverageReason.FACTS_INCOMPLETE),
        ({REASON_CROSS_SOURCE_CONFLICT}, CoverageReason.SOURCE_CONFLICT),
        ({REASON_NON_DISTRIBUTIVE_RESTRUCTURING, REASON_INCOMPLETE},
         CoverageReason.FACTS_INCOMPLETE),
        ({"some_future_reason"}, CoverageReason.FACTS_INCOMPLETE),
    ):
        status, reason = _coverage_verdict(
            _FETCHED, has_accepted=True, quarantine_reasons=reasons
        )
        assert status is CoverageStatus.UNTRUSTED, reasons
        assert reason is expected, reasons
