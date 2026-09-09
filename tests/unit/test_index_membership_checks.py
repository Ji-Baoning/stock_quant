"""Membership fact acceptance checks and the size-exception mechanism.

``validate_membership_facts`` is the pure, offline validator behind the
mandatory ``index_membership_evidence`` acceptance result: it rejects missing
evidence, unknown symbols, overlapping intervals, announcement look-ahead,
empty security-master intersections, unproven delisting endpoints, coverage
gaps and wrong daily member counts -- every rejection is ``FATAL`` because a
formal Research run must stop before factor calculation. Officially allowed
temporary cardinality deviations are accepted only through immutable,
hash-backed exception records.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.universe_membership import (
    SecurityMasterBoundary,
    membership_content_hash,
    membership_frame,
)
from stock_quant.data_quality.models import QualityIssue, Severity
from stock_quant.data_quality.raw_checks import (
    UNIVERSE_ANNOUNCEMENT_AFTER_USE,
    UNIVERSE_COVERAGE_GAP,
    UNIVERSE_DELISTING_ENDPOINT_UNPROVEN,
    UNIVERSE_EVIDENCE_MISSING,
    UNIVERSE_EXCEPTION_CONFLICT,
    UNIVERSE_INTERVAL_OVERLAP,
    UNIVERSE_MASTER_INTERSECTION_EMPTY,
    UNIVERSE_MEMBER_COUNT_MISMATCH,
    UNIVERSE_UNKNOWN_SYMBOL,
    MembershipSizeException,
    validate_membership_facts,
)

_CSI300_SIZE = 300
_COVERAGE_START = date(2019, 1, 1)
_COVERAGE_END = date(2021, 12, 31)


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _csi300_symbols(count: int = _CSI300_SIZE) -> list[str]:
    return [f"{600000 + offset}.SH" for offset in range(count)]


def make_fact(**overrides: Any) -> dict[str, Any]:
    """One valid raw-fact payload with per-test overrides."""
    values: dict[str, Any] = {
        "universe_id": "csi300",
        "symbol": "600000.SH",
        "raw_effective_from": _COVERAGE_START,
        "raw_effective_to": None,
        "announcement_date": date(2018, 12, 17),
        "status": "active",
        "reason": "initial_constituent",
        "source": "csi_index_announcement",
        "source_url": "https://www.csindex.com.cn/announcement-2018-12.pdf",
        "snapshot_sha256": "a1" * 32,
        "source_document_sha256": "b2" * 32,
    }
    values.update(overrides)
    return values


@pytest.fixture
def calendar() -> TradingCalendar:
    return TradingCalendar.from_open_days(
        tuple(_weekdays(_COVERAGE_START, _COVERAGE_END))
    )


@pytest.fixture
def valid_facts() -> pd.DataFrame:
    """A full, evidence-backed csi300 initial batch of 300 open facts."""
    return membership_frame(
        [
            make_fact(symbol=symbol)
            for symbol in _csi300_symbols()
        ]
    )


def _expected_sizes(**overrides: int) -> dict[str, int]:
    sizes = {"csi300": _CSI300_SIZE}
    sizes.update(overrides)
    return sizes


# --------------------------------------------------------------------------- #
# The plan's two fatal-check examples
# --------------------------------------------------------------------------- #


def test_csi300_cardinality_mismatch_is_fatal(calendar, valid_facts):
    issues = validate_membership_facts(
        valid_facts.iloc[:-1],
        calendar=calendar,
        expected_sizes=_expected_sizes(),
    )
    assert ("UNIVERSE_MEMBER_COUNT_MISMATCH", "FATAL") in {
        (issue.code, issue.severity.value) for issue in issues
    }


def test_missing_snapshot_evidence_is_fatal(calendar, valid_facts):
    valid_facts.loc[0, "snapshot_sha256"] = ""
    assert "UNIVERSE_EVIDENCE_MISSING" in {
        issue.code
        for issue in validate_membership_facts(
            valid_facts, calendar=calendar, expected_sizes=_expected_sizes()
        )
    }


# --------------------------------------------------------------------------- #
# Clean data and the issue contract
# --------------------------------------------------------------------------- #


def test_complete_evidenced_batch_validates_cleanly(calendar, valid_facts):
    issues = validate_membership_facts(
        valid_facts, calendar=calendar, expected_sizes=_expected_sizes()
    )
    assert issues == []


def test_every_rejection_is_a_fatal_quality_issue(calendar, valid_facts):
    frame = valid_facts.iloc[:-1]
    for issue in validate_membership_facts(
        frame, calendar=calendar, expected_sizes=_expected_sizes()
    ):
        assert isinstance(issue, QualityIssue)
        assert issue.severity is Severity.FATAL
        assert issue.table == "universe_membership"


def test_member_count_mismatch_names_window_and_counts(calendar, valid_facts):
    issues = validate_membership_facts(
        valid_facts.iloc[:-1],
        calendar=calendar,
        expected_sizes=_expected_sizes(),
    )
    issue = next(
        item for item in issues
        if item.code == UNIVERSE_MEMBER_COUNT_MISMATCH
    )
    assert issue.details["universe_id"] == "csi300"
    assert issue.details["expected"] == _CSI300_SIZE
    assert issue.details["actual"] == _CSI300_SIZE - 1
    assert issue.details["trading_days"] > 0


# --------------------------------------------------------------------------- #
# Evidence, symbols, intervals, announcement visibility
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field", ["snapshot_sha256", "source_document_sha256"]
)
def test_missing_or_malformed_evidence_hash_is_fatal(
    calendar, valid_facts, field
):
    for bad in ("", "AB" * 32, "cd" * 31):
        frame = valid_facts.copy()
        frame.loc[0, field] = bad
        codes = {
            issue.code
            for issue in validate_membership_facts(
                frame, calendar=calendar, expected_sizes=_expected_sizes()
            )
        }
        assert UNIVERSE_EVIDENCE_MISSING in codes


@pytest.mark.parametrize("field", ["source", "source_url"])
def test_missing_source_or_url_is_fatal(calendar, valid_facts, field):
    frame = valid_facts.copy()
    frame.loc[0, field] = ""
    codes = {
        issue.code
        for issue in validate_membership_facts(
            frame, calendar=calendar, expected_sizes=_expected_sizes()
        )
    }
    assert UNIVERSE_EVIDENCE_MISSING in codes


def test_non_canonical_symbol_violates_master_conventions(calendar, valid_facts):
    frame = valid_facts.copy()
    frame.loc[0, "symbol"] = "600000"
    codes = {
        issue.code
        for issue in validate_membership_facts(
            frame, calendar=calendar, expected_sizes=_expected_sizes()
        )
    }
    assert UNIVERSE_UNKNOWN_SYMBOL in codes


def test_symbol_absent_from_master_is_unknown(calendar, valid_facts):
    master = {
        symbol: SecurityMasterBoundary(symbol, list_date=date(2001, 1, 2))
        for symbol in _csi300_symbols()[:-1]
    }
    codes = {
        issue.code
        for issue in validate_membership_facts(
            valid_facts,
            calendar=calendar,
            expected_sizes=_expected_sizes(),
            master=master,
        )
    }
    issue = next(
        item
        for item in validate_membership_facts(
            valid_facts,
            calendar=calendar,
            expected_sizes=_expected_sizes(),
            master=master,
        )
        if item.code == UNIVERSE_UNKNOWN_SYMBOL
    )
    assert issue.symbol == "600299.SH"
    assert issue.details["reason"] == "absent_from_security_master"
    assert codes == {UNIVERSE_UNKNOWN_SYMBOL}


def test_overlapping_intervals_are_rejected(calendar):
    facts = [
        make_fact(symbol="600000.SH"),
        make_fact(
            symbol="600000.SH",
            raw_effective_from=date(2020, 3, 1),
            raw_effective_to=date(2020, 6, 14),
            status="removed",
            reason="regular_rebalance",
            announcement_date=date(2020, 2, 28),
        ),
    ]
    codes = {
        issue.code
        for issue in validate_membership_facts(
            membership_frame(facts),
            calendar=calendar,
            expected_sizes={},
        )
    }
    assert UNIVERSE_INTERVAL_OVERLAP in codes


def test_announcement_after_effective_start_is_rejected(calendar):
    facts = [
        make_fact(
            announcement_date=date(2019, 6, 1),
        )
    ]
    codes = {
        issue.code
        for issue in validate_membership_facts(
            membership_frame(facts),
            calendar=calendar,
            expected_sizes={},
        )
    }
    assert UNIVERSE_ANNOUNCEMENT_AFTER_USE in codes


# --------------------------------------------------------------------------- #
# Security-master intersection and delisting endpoints
# --------------------------------------------------------------------------- #


def test_empty_master_intersection_is_rejected(calendar):
    facts = [
        make_fact(
            raw_effective_to=date(2019, 6, 30),
            status="removed",
            reason="regular_rebalance",
            announcement_date=date(2018, 12, 17),
        )
    ]
    master = {
        "600000.SH": SecurityMasterBoundary(
            "600000.SH", list_date=date(2020, 1, 2)
        )
    }
    codes = {
        issue.code
        for issue in validate_membership_facts(
            membership_frame(facts),
            calendar=calendar,
            expected_sizes={},
            master=master,
        )
    }
    assert UNIVERSE_MASTER_INTERSECTION_EMPTY in codes


def _delisting_fact() -> dict[str, Any]:
    """A delisting-removal segment announced before its interval starts.

    The segment starts at the rebalance boundary that put the symbol in
    (announced 2019-12-20) and ends on the day before the removal effective
    date; the master's ``last_tradable_date`` decides whether the termination
    endpoint is provable.
    """
    return make_fact(
        raw_effective_from=date(2020, 1, 1),
        raw_effective_to=date(2020, 6, 14),
        status="removed",
        reason="delisting",
        announcement_date=date(2019, 12, 20),
    )


def test_unproven_delisting_endpoint_is_rejected(calendar):
    master = {
        "600000.SH": SecurityMasterBoundary(
            "600000.SH", list_date=date(2001, 1, 2), last_tradable_date=None
        )
    }
    codes = {
        issue.code
        for issue in validate_membership_facts(
            membership_frame([_delisting_fact()]),
            calendar=calendar,
            expected_sizes={},
            master=master,
        )
    }
    assert UNIVERSE_DELISTING_ENDPOINT_UNPROVEN in codes


def test_proven_delisting_endpoint_validates_cleanly(calendar):
    master = {
        "600000.SH": SecurityMasterBoundary(
            "600000.SH",
            list_date=date(2001, 1, 2),
            last_tradable_date=date(2020, 6, 13),
        )
    }
    issues = validate_membership_facts(
        membership_frame([_delisting_fact()]),
        calendar=calendar,
        expected_sizes={},
        master=master,
    )
    assert issues == []


# --------------------------------------------------------------------------- #
# Coverage gaps and the official size-exception mechanism
# --------------------------------------------------------------------------- #


def test_days_without_members_are_a_coverage_gap(calendar, valid_facts):
    ended = [
        make_fact(
            symbol=symbol,
            raw_effective_to=date(2020, 6, 30),
            status="removed",
            reason="regular_rebalance",
        )
        for symbol in _csi300_symbols()
    ]
    codes = {
        issue.code
        for issue in validate_membership_facts(
            membership_frame(ended),
            calendar=calendar,
            expected_sizes=_expected_sizes(),
        )
    }
    assert UNIVERSE_COVERAGE_GAP in codes
    assert UNIVERSE_MEMBER_COUNT_MISMATCH not in codes


def test_universe_without_facts_is_a_coverage_gap(calendar, valid_facts):
    issues = validate_membership_facts(
        valid_facts,
        calendar=calendar,
        expected_sizes=_expected_sizes(csi500=500),
    )
    gap = next(
        item for item in issues if item.code == UNIVERSE_COVERAGE_GAP
    )
    assert gap.details["universe_id"] == "csi500"


def test_official_exception_record_absorbs_cardinality_mismatch(
    calendar, valid_facts
):
    exception = MembershipSizeException(
        universe_id="csi300",
        interval_start=_COVERAGE_START,
        interval_end=_COVERAGE_END,
        allowed_size=_CSI300_SIZE - 1,
        reason="official temporary adjustment window",
        rules_version="official-2026-rules",
        evidence_sha256="c3" * 32,
    )
    issues = validate_membership_facts(
        valid_facts.iloc[:-1],
        calendar=calendar,
        expected_sizes=_expected_sizes(),
        exceptions=[exception],
    )
    assert issues == []


def test_exception_outside_the_mismatch_days_does_not_suppress(
    calendar, valid_facts
):
    exception = MembershipSizeException(
        universe_id="csi300",
        interval_start=date(2005, 1, 1),
        interval_end=date(2010, 12, 31),
        allowed_size=_CSI300_SIZE - 1,
        reason="official temporary adjustment window",
        rules_version="official-2026-rules",
        evidence_sha256="c3" * 32,
    )
    issues = validate_membership_facts(
        valid_facts.iloc[:-1],
        calendar=calendar,
        expected_sizes=_expected_sizes(),
        exceptions=[exception],
    )
    assert UNIVERSE_MEMBER_COUNT_MISMATCH in {
        issue.code for issue in issues
    }


def test_overlapping_exception_records_conflict(calendar, valid_facts):
    exceptions = [
        MembershipSizeException(
            universe_id="csi300",
            interval_start=_COVERAGE_START,
            interval_end=date(2020, 12, 31),
            allowed_size=_CSI300_SIZE - 1,
            reason="first official exception",
            rules_version="official-2026-rules",
            evidence_sha256="c3" * 32,
        ),
        MembershipSizeException(
            universe_id="csi300",
            interval_start=date(2020, 6, 1),
            interval_end=_COVERAGE_END,
            allowed_size=_CSI300_SIZE - 2,
            reason="second official exception",
            rules_version="official-2026-rules",
            evidence_sha256="d4" * 32,
        ),
    ]
    issues = validate_membership_facts(
        valid_facts.iloc[:-1],
        calendar=calendar,
        expected_sizes=_expected_sizes(),
        exceptions=exceptions,
    )
    assert UNIVERSE_EXCEPTION_CONFLICT in {
        issue.code for issue in issues
    }


def test_exception_record_is_hash_backed_and_immutable():
    exception = MembershipSizeException(
        universe_id="csi300",
        interval_start=_COVERAGE_START,
        interval_end=_COVERAGE_END,
        allowed_size=299,
        reason="official temporary adjustment window",
        rules_version="official-2026-rules",
        evidence_sha256="c3" * 32,
    )
    assert len(exception.content_hash) == 64
    with pytest.raises(ValidationError):
        MembershipSizeException.model_validate(
            {
                "universe_id": "csi300",
                "interval_start": _COVERAGE_START,
                "interval_end": _COVERAGE_END,
                "allowed_size": 299,
                "reason": "no evidence",
                "rules_version": "official-2026-rules",
                "evidence_sha256": "not-a-hash",
            }
        )
    with pytest.raises(ValidationError):
        exception.allowed_size = 298


# --------------------------------------------------------------------------- #
# project/refresh_index_membership.py converter contract
# --------------------------------------------------------------------------- #


def _load_refresh_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "project"
        / "refresh_index_membership.py"
    )
    spec = importlib.util.spec_from_file_location(
        "refresh_index_membership", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refresh_script_requires_evidence_and_date_arguments():
    module = _load_refresh_module()
    parser = module.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["--universe-id", "csi300", "--input", "members.csv"]
        )


def test_refresh_script_normalizes_rows_to_canonical_facts():
    module = _load_refresh_module()
    rows = pd.DataFrame({"symbol": ["600000.SH", "000001.SZ"]})
    facts = module.build_membership_facts(
        rows,
        universe_id="csi300",
        source="csi_index_announcement",
        source_url="https://www.csindex.com.cn/announcement-2018-12.pdf",
        snapshot_sha256="a1" * 32,
        source_document_sha256="b2" * 32,
        effective_date=date(2019, 1, 1),
        announcement_date=date(2018, 12, 17),
    )
    assert len(facts) == 2
    assert membership_content_hash(facts) == membership_content_hash(
        [
            make_fact(symbol="600000.SH"),
            make_fact(symbol="000001.SZ"),
        ]
    )


def test_refresh_script_rejects_unknown_universe_id():
    module = _load_refresh_module()
    with pytest.raises(ValueError, match="universe_id"):
        module.build_membership_facts(
            pd.DataFrame({"symbol": ["600000.SH"]}),
            universe_id="hs300",
            source="csi_index_announcement",
            source_url="https://www.csindex.com.cn/announcement.pdf",
            snapshot_sha256="a1" * 32,
            source_document_sha256="b2" * 32,
            effective_date=date(2019, 1, 1),
            announcement_date=date(2018, 12, 17),
        )
