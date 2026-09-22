"""The carried-coverage merge drops the rows a fresh verdict supersedes.

Spec D5.2: a fetched refresh rebuilds the coverage verdict for its own
contract window only, and the baseline's rows for the history *before* that
window stay valid evidence published beside it, clipped to end the day before
the fetched window starts.  A baseline row that lies *inside* the fetched
window is superseded by the fresh verdict and is dropped -- clipping its end
instead publishes a row whose ``window_start`` is after its ``window_end``,
which the corporate-action trust gate then counts as a real ``UNTRUSTED``
window (the 2026-09-21 ``e732b191`` acceptance: 38 of its 62 failures).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_quant.data_model.corporate_action_coverage import (
    CoverageReason,
    CoverageStatus,
    coverage_frame,
    coverage_record,
)
from stock_quant.data_pipeline import _merge_carried_coverage


def test_carried_row_inside_the_fetched_window_is_dropped():
    """A baseline row the refresh supersedes must not reach the publish.

    The baseline's row starts after the fetched window begins, so the fresh
    verdict covers it entirely and the baseline row carries no evidence the
    refresh does not replace.
    """
    carried = coverage_frame(
        [
            coverage_record(
                "000001.SZ",
                date(2026, 6, 20),
                date(2026, 9, 18),
                CoverageStatus.UNTRUSTED,
                CoverageReason.SOURCE_FETCH_FAILED,
            )
        ]
    )
    refreshed = coverage_frame(
        [
            coverage_record(
                "000001.SZ",
                date(2021, 1, 1),
                date(2026, 9, 18),
                CoverageStatus.VERIFIED,
                None,
            )
        ]
    )

    merged = _merge_carried_coverage(
        carried, refreshed, fetch_start=date(2021, 1, 1)
    )

    assert len(merged) == 1, merged.to_dict("records")
    assert merged.iloc[0]["status"] == CoverageStatus.VERIFIED.value


def test_carried_row_with_an_unreadable_start_keeps_its_prior_treatment():
    """Supersession must be proven before a baseline row is discarded.

    A row the fetched window covers can only be recognised from its own
    ``window_start``; when that cell cannot be read, the row is still the
    baseline's evidence for *some* earlier history, so it keeps the
    clip-and-publish treatment instead of vanishing from the publish.
    """
    carried = coverage_frame(
        [
            coverage_record(
                "000001.SZ",
                None,
                date(2026, 9, 18),
                CoverageStatus.UNTRUSTED,
                CoverageReason.SOURCE_CONFLICT,
            )
        ]
    )
    refreshed = coverage_frame(
        [
            coverage_record(
                "000001.SZ",
                date(2021, 1, 1),
                date(2026, 9, 18),
                CoverageStatus.VERIFIED,
                None,
            )
        ]
    )

    merged = _merge_carried_coverage(
        carried, refreshed, fetch_start=date(2021, 1, 1)
    )

    assert len(merged) == 2, merged.to_dict("records")
    carried_row = merged[
        merged["status"] == CoverageStatus.UNTRUSTED.value
    ].iloc[0]
    assert pd.Timestamp(carried_row["window_end"]).date() == date(2020, 12, 31)


def test_carried_row_straddling_the_boundary_is_clipped_and_kept():
    """History the refresh does not re-judge survives, clipped at the day before.

    A baseline row that starts before the fetched window and ends inside it is
    still the only evidence for the pre-fetch history, so it publishes clipped
    to end the day before the fetched window begins.
    """
    carried = coverage_frame(
        [
            coverage_record(
                "000001.SZ",
                date(2015, 1, 5),
                date(2026, 9, 18),
                CoverageStatus.UNTRUSTED,
                CoverageReason.SOURCE_CONFLICT,
            )
        ]
    )
    refreshed = coverage_frame(
        [
            coverage_record(
                "000001.SZ",
                date(2021, 1, 1),
                date(2026, 9, 18),
                CoverageStatus.VERIFIED,
                None,
            )
        ]
    )

    merged = _merge_carried_coverage(
        carried, refreshed, fetch_start=date(2021, 1, 1)
    )

    assert len(merged) == 2, merged.to_dict("records")
    carried_row = merged[
        merged["status"] == CoverageStatus.UNTRUSTED.value
    ].iloc[0]
    assert pd.Timestamp(carried_row["window_start"]).date() == date(2015, 1, 5)
    assert pd.Timestamp(carried_row["window_end"]).date() == date(2020, 12, 31)
