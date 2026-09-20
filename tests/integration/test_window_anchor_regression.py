"""Criterion 7/8: the review window anchors to ``full_history_acceptance_start``.

``_window`` is pinned twice.  The pure-function cases pin the anchor
preference sequence of ADR-011 (acceptance anchor first, legacy
``requested_start_date`` fallback, dedicated ``FullHistoryAcceptanceStartMissing``
when a build names neither -- a ``ValueError`` subclass so
``run_automated_checks`` turns it into a FAIL result, never a crash).
The published-version cases republish the shared fixture dataset
(``tests/integration/test_acceptance_checks.py``'s ``mutated_project``
pattern) and run the real offline checker, so a ``--start`` earlier than the
first open day is shown to flip ``date_window_completeness`` from FAIL to
PASS exactly on a published version while an anchor the bars do not back
still fails closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from conftest import BARS_START, CAL_START, build_fixture_project

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_quality.models import QualityReport
from stock_quant.research.acceptance.checks import (
    AcceptanceCheckInput,
    FullHistoryAcceptanceStartMissing,
    _window,
    run_automated_checks,
)
from stock_quant.research.acceptance.evidence import (
    EvidenceBuildError,
    evidence_window,
)
from stock_quant.research.acceptance.models import CheckStatus

# --------------------------------------------------------------------------- #
# The pure anchor preference sequence (ADR-011 decision, spec §2 D5.1)
# --------------------------------------------------------------------------- #


def test_anchor_prefers_acceptance_start():
    build = {
        "requested_start_date": "2015-01-01",
        "resolved_end_date": "2026-08-28",
        "full_history_acceptance_start": "2015-01-05",
    }
    assert _window(build) == (date(2015, 1, 5), date(2026, 8, 28))


def test_legacy_requested_fallback_unchanged():
    build = {
        "requested_start_date": "2015-01-05",
        "resolved_end_date": "2026-08-28",
    }
    assert _window(build) == (date(2015, 1, 5), date(2026, 8, 28))


def test_blank_acceptance_start_is_not_an_anchor():
    """ADR-011 candidate 1 is a *non-empty* string; blank falls back."""
    build = {
        "requested_start_date": "2015-01-05",
        "resolved_end_date": "2026-08-28",
        "full_history_acceptance_start": "",
    }
    assert _window(build) == (date(2015, 1, 5), date(2026, 8, 28))


def test_missing_both_raises_dedicated_error():
    with pytest.raises(FullHistoryAcceptanceStartMissing):
        _window({"resolved_end_date": "2026-08-28"})


def test_bootstrap_key_shape_raises_dedicated_error():
    """The real bootstrap manifest carries no requested/resolved key either.

    bootstrap.py binds only origin/contract/calendar_coverage/anchor(None):
    the dedicated anchor reason must win over the generic window-missing
    ValueError there (spec §0-12), so the anchor check precedes the end check.
    """
    with pytest.raises(FullHistoryAcceptanceStartMissing):
        _window(
            {
                "origin": "bootstrap",
                "pipeline_contract_version": 1,
                "calendar_coverage": [],
                "full_history_acceptance_start": None,
            }
        )


def test_dedicated_error_is_caught_as_fail_not_crash():
    # run_automated_checks catches ValueError subclasses into FAIL results
    # (checks.py catch table) -- a non-ValueError would crash the whole run.
    assert issubclass(FullHistoryAcceptanceStartMissing, ValueError)


# --------------------------------------------------------------------------- #
# evidence_window tells the two failure paths apart (spec §0 item 12)
# --------------------------------------------------------------------------- #


def test_evidence_window_distinguishes_anchor_missing():
    with pytest.raises(EvidenceBuildError) as captured:
        evidence_window(
            {"build_config": {"resolved_end_date": "2026-08-28"}}
        )
    assert str(captured.value) == "full_history_acceptance_start_missing"


def test_evidence_window_keeps_window_missing_for_malformed():
    with pytest.raises(EvidenceBuildError) as captured:
        evidence_window(
            {
                "build_config": {
                    "full_history_acceptance_start": "x",
                    "resolved_end_date": None,
                }
            }
        )
    assert str(captured.value) == "window_missing"


# --------------------------------------------------------------------------- #
# Published-version regression through the real offline checker
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _PinnedVersion:
    """One published dataset version the checker is pointed at."""

    root: Path
    version: str


@pytest.fixture
def published(tmp_path):
    """A fresh trusted fixture project (conftest builders, offline)."""
    base = build_fixture_project(tmp_path / "project")
    return _PinnedVersion(root=base.root, version=base.version)


def _republished(
    base: _PinnedVersion,
    *,
    requested: str | None = ...,
    anchor: str | None = ...,
) -> _PinnedVersion:
    """Republish a copy with the build's window keys mutated.

    The ``mutated_project`` pattern of ``test_acceptance_checks.py``: the
    current tables and ``build_config`` are staged, one dimension is mutated,
    and ``DatasetPublisher`` publishes the result as a fresh immutable
    version.  ``requested``/``anchor`` mirror what the pipeline binds: a
    default ``data_update`` build writes ``requested_start_date: null`` and a
    bootstrap manifest omits the requested key and writes a null anchor.
    """
    publisher = DatasetPublisher(base.root)
    with DatasetReader(base.root).open(base.version) as context:
        tables = {name: context.read(name) for name in context.tables}
    manifest_path = (
        publisher.standardized_root / base.version / "dataset_manifest.json"
    )
    build_config = json.loads(manifest_path.read_text())["build_config"]
    if requested is not ...:
        if requested is None:
            build_config["requested_start_date"] = None
        else:
            build_config["requested_start_date"] = requested
    if anchor is not ...:
        build_config["full_history_acceptance_start"] = anchor
    reference = publisher.publish(
        tables, QualityReport(), build_config=build_config
    )
    return _PinnedVersion(root=base.root, version=reference.version)


def _input(value: _PinnedVersion) -> AcceptanceCheckInput:
    return AcceptanceCheckInput(
        project_root=value.root, dataset_version=value.version
    )


def _checks_by_code(checks) -> dict[str, object]:
    return {check.code: check for check in checks}


def _bound_anchor(base: _PinnedVersion) -> str:
    manifest_path = (
        DatasetPublisher(base.root).standardized_root
        / base.version
        / "dataset_manifest.json"
    )
    build_config = json.loads(manifest_path.read_text())["build_config"]
    return build_config["full_history_acceptance_start"]


def test_requested_before_first_open_day_flips_window_to_pass(published):
    """A version published with ``--start`` before the first open day passes.

    The anchored window is the acceptance obligation bound at publish time
    (kept untouched here); the earlier requested start no longer widens the
    window past the calendar evidence (ADR-011, criterion 7).
    """
    value = _republished(published, requested="2015-01-01")
    checks = _checks_by_code(run_automated_checks(_input(value)))
    assert (
        checks["date_window_completeness"].status is CheckStatus.PASS
    )


def test_requested_equal_to_anchor_keeps_the_window(published):
    """requested == acceptance start: the window is unchanged (criterion 7)."""
    anchor = _bound_anchor(published)
    value = _republished(published, requested=anchor)
    checks = _checks_by_code(run_automated_checks(_input(value)))
    assert (
        checks["date_window_completeness"].status is CheckStatus.PASS
    )


def test_requested_null_default_path_now_accepts(published):
    """A default data_update build (requested null) is reviewable (criterion 8)."""
    value = _republished(published, requested=None)
    checks = _checks_by_code(run_automated_checks(_input(value)))
    assert (
        checks["date_window_completeness"].status is CheckStatus.PASS
    )


def test_bootstrap_without_any_start_fails_with_dedicated_reason(published):
    """A bootstrap-shaped build (no requested key, null anchor) FAILs closed.

    It must fail with the dedicated missing-anchor reason mapped by the
    runner's catch table -- never a crash and never a vacuous pass
    (spec §0 item 12).
    """
    value = _republished(published, requested=None, anchor=None)
    checks = _checks_by_code(run_automated_checks(_input(value)))
    result = checks["date_window_completeness"]
    assert result.status is CheckStatus.FAIL
    assert result.details == {
        "error_code": "FullHistoryAcceptanceStartMissing"
    }


def test_anchor_beyond_the_bars_still_fails_closed(published):
    """Re-anchoring never relaxes completeness: bars must back the anchor.

    The calendar's first open day precedes the fixture's bar coverage
    (:data:`conftest.CAL_START` < :data:`conftest.BARS_START`), so a window
    anchored there has open days no bar explains and must FAIL.
    """
    value = _republished(
        published,
        requested="2015-01-01",
        anchor=CAL_START.isoformat(),
    )
    checks = _checks_by_code(run_automated_checks(_input(value)))
    assert checks["date_window_completeness"].status is CheckStatus.FAIL


def test_anchored_window_starts_at_the_bound_obligation(published):
    """The fixture binds its acceptance start at its first bar-covered day."""
    assert _bound_anchor(published) == BARS_START.isoformat()
