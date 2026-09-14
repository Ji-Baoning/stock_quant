"""Unit behaviour of the worksheet confirm writer (Task 7).

``confirm`` is the only writer that turns a manual row into PASS/FAIL.  It
appends one immutable revision, cites it (plus any external input) as the row's
evidence, and leaves every other byte of the checklist alone -- including the
container's ``operator_id``, which only ``prepare`` may change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from stock_quant.research.acceptance.models import OPERATOR_ONLY_CODES
from stock_quant.research.acceptance.service import prepare_checklist
from stock_quant.research.acceptance.worksheet import (
    WorksheetError,
    confirm,
    effective_revision,
    pending_path,
    program_payload,
    revision_chain,
    verify_markers,
)

_PREPARED_AT = datetime(2026, 9, 8, tzinfo=timezone.utc)
_CALENDAR_EXCERPT = b"2021-11-01 1\n2021-11-02 1\n"


def _prepared(project, tmp_path: Path) -> Path:
    """A really prepared checklist on disk, with its evidence pack."""
    path = tmp_path / "checklist.yml"
    prepare_checklist(
        project.root,
        project.version,
        "operator-a",
        path,
        prepared_at=_PREPARED_AT,
    )
    return path


def _payload(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _row(path: Path, code: str) -> dict:
    return next(
        row for row in _payload(path)["manual_checks"] if row["code"] == code
    )


def _queue_length(project, code: str) -> int:
    """The queue this code's pending worksheet publishes."""
    text = pending_path(project.root, project.version, code).read_text(
        encoding="utf-8"
    )
    return len(program_payload(verify_markers(text)[0]).get("queue") or ())


def _calendar_rows(project) -> list[str]:
    """The open days the pending worksheet enumerates, as ISO strings.

    Read from the worksheet's own queue -- on a first signing the queue *is*
    the candidate set -- so the excerpt a test transcribes is the same set of
    days the operator was shown, with no second source of truth.
    """
    text = pending_path(
        project.root, project.version, "exchange_calendar_sample"
    ).read_text(encoding="utf-8")
    queue = program_payload(verify_markers(text)[0])["queue"]
    return [str(row).removeprefix("calendar:") for row in queue]


def test_confirm_flips_exactly_one_row(published_project, tmp_path) -> None:
    path = _prepared(published_project, tmp_path)
    before = _payload(path)
    confirm(
        published_project.root,
        path,
        code="secret_scan",
        operator_id="operator-a",
        decision="PASS",
    )
    after = _payload(path)
    changed = [
        row["code"]
        for row, other in zip(before["manual_checks"], after["manual_checks"])
        if row != other
    ]
    assert changed == ["secret_scan"]
    assert before["automated_checks"] == after["automated_checks"]
    assert before["operator_id"] == after["operator_id"]
    for field in ("dataset_version", "dataset_manifest_sha256", "prepared_at"):
        assert before[field] == after[field]


def test_confirm_cites_the_revision_and_drops_the_pending_file(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    confirm(
        published_project.root,
        path,
        code="secret_scan",
        operator_id="operator-a",
        decision="PASS",
    )
    revision = effective_revision(
        published_project.root, published_project.version, "secret_scan"
    )
    row = _row(path, "secret_scan")
    assert row["status"] == "PASS"
    assert row["evidence"][0]["reference"] == revision.reference
    assert row["evidence"][0]["sha256"] == revision.sha256
    assert revision.payload["candidate_evidence"], (
        "a mechanisable signing cites the evidence pack it reviewed"
    )
    assert not pending_path(
        published_project.root, published_project.version, "secret_scan"
    ).exists()
    assert all(
        other["status"] == "PENDING_CONFIRMATION"
        for other in _payload(path)["manual_checks"]
        if other["code"] != "secret_scan"
    )


def test_confirming_an_already_signed_row_is_refused(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="PASS",
    )
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "already_signed"


def test_a_queue_requires_an_exact_acknowledgement(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    expected = _queue_length(published_project, "trading_rule_effective_dates")
    assert expected > 0
    with pytest.raises(WorksheetError) as missing:
        confirm(
            published_project.root, path,
            code="trading_rule_effective_dates",
            operator_id="operator-a", decision="PASS",
        )
    assert missing.value.category == "acknowledgement_required"
    with pytest.raises(WorksheetError) as wrong:
        confirm(
            published_project.root, path,
            code="trading_rule_effective_dates",
            operator_id="operator-a", decision="PASS",
            acknowledge=expected - 1,
        )
    assert wrong.value.category == "acknowledgement_required"
    confirm(
        published_project.root, path,
        code="trading_rule_effective_dates",
        operator_id="operator-a", decision="PASS",
        acknowledge=expected,
    )


def test_an_external_input_is_stored_and_cited_second(
    published_project, tmp_path
) -> None:
    """A one-column excerpt is stored, cited second, and cannot corroborate."""
    path = _prepared(published_project, tmp_path)
    excerpt = tmp_path / "official_calendar.txt"
    excerpt.write_text(
        "".join(f"{day}\n" for day in _calendar_rows(published_project)),
        encoding="utf-8",
    )
    confirm(
        published_project.root, path,
        code="exchange_calendar_sample",
        operator_id="operator-a", decision="PASS",
        external_input=excerpt,
        acknowledge=_queue_length(published_project, "exchange_calendar_sample"),
    )
    row = _row(path, "exchange_calendar_sample")
    references = [entry["reference"] for entry in row["evidence"]]
    assert references[0].startswith("data/acceptance-worksheets/")
    assert references[1].startswith("data/acceptance-external-inputs/")
    assert (published_project.root / references[1]).read_bytes() == (
        excerpt.read_bytes()
    )
    revision = effective_revision(
        published_project.root, published_project.version, "exchange_calendar_sample"
    )
    assert revision.payload["external_input"]["reference"] == references[1]
    assert revision.payload["comparison"]["status"] == "compared"
    assert revision.payload["comparison"]["has_close_column"] is False
    assert revision.payload["strength"] == "OPERATOR_ATTESTED", (
        "a one-column excerpt cannot tell 'officially closed' from 'forgotten'"
    )


def test_a_partial_excerpt_enlarges_the_queue(published_project, tmp_path) -> None:
    """Differences are what the operator signs about, so they must be claimed.

    The acknowledgement the prepared worksheet advertised no longer suffices
    once the excerpt itself introduces 20 uncovered window days: the queue is
    recomputed at signing time from the input that was actually supplied.
    """
    path = _prepared(published_project, tmp_path)
    excerpt = tmp_path / "partial.txt"
    excerpt.write_bytes(_CALENDAR_EXCERPT)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="exchange_calendar_sample",
            operator_id="operator-a", decision="PASS",
            external_input=excerpt,
            acknowledge=_queue_length(
                published_project, "exchange_calendar_sample"
            ),
        )
    assert error.value.category == "acknowledgement_required"


def test_a_faithful_two_column_excerpt_corroborates(
    published_project, tmp_path
) -> None:
    """The only way to EXTERNAL_CORROBORATED: cover every window open day."""
    path = _prepared(published_project, tmp_path)
    rows = _calendar_rows(published_project)
    excerpt = tmp_path / "official_calendar.txt"
    excerpt.write_text(
        "".join(f"{day} 1\n" for day in rows), encoding="utf-8"
    )
    confirm(
        published_project.root, path,
        code="exchange_calendar_sample",
        operator_id="operator-a", decision="PASS",
        external_input=excerpt,
        acknowledge=len(rows),
    )
    revision = effective_revision(
        published_project.root, published_project.version, "exchange_calendar_sample"
    )
    comparison = revision.payload["comparison"]
    assert comparison["has_close_column"] is True
    assert comparison["dataset_open_official_absent"] == []
    assert comparison["official_open_dataset_absent"] == []
    assert comparison["official_closed_dataset_open"] == []
    assert revision.payload["strength"] == "EXTERNAL_CORROBORATED"
    assert revision.payload["queue"], "the full candidate set was still queued"


def test_an_external_input_for_a_mechanisable_code_is_refused(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    excerpt = tmp_path / "official_calendar.txt"
    excerpt.write_bytes(_CALENDAR_EXCERPT)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan",
            operator_id="operator-a", decision="PASS",
            external_input=excerpt,
        )
    assert error.value.category == "external_input_invalid"


def test_a_mechanisable_row_without_a_pack_is_refused(
    published_project, tmp_path, monkeypatch
) -> None:
    """No published artifact means nothing the program can cite.

    ``confirm`` may not generate evidence, so signing here would leave the row
    citing only its own revision -- a signature standing in for its evidence.
    This also pins the exemption's other half: rows left by a *failed* evidence
    build (placeholder summary, empty evidence) are an acceptable state for the
    other eight rows, not a drift.
    """
    from stock_quant.research.acceptance import evidence

    def _fail(*args, **kwargs):
        raise evidence.EvidenceBuildError("dataset_unreadable")

    monkeypatch.setattr(
        "stock_quant.research.acceptance.service.build_mechanisable_evidence", _fail
    )
    path = _prepared(published_project, tmp_path)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "candidate_evidence_missing"


def test_a_missing_pending_worksheet_is_refused(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    pending_path(
        published_project.root, published_project.version, "secret_scan"
    ).unlink()
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "worksheet_missing"


def test_fail_requires_a_conclusion(published_project, tmp_path) -> None:
    path = _prepared(published_project, tmp_path)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="FAIL",
        )
    assert error.value.category == "conclusion_required"
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="FAIL",
        conclusion="row counts do not match the sample",
    )
    assert _row(path, "secret_scan")["status"] == "FAIL"


def test_confirm_refuses_when_another_row_was_edited_by_hand(
    published_project, tmp_path
) -> None:
    """The three-state exemption accepts a fresh row or a bound signed row."""
    import yaml

    path = _prepared(published_project, tmp_path)
    payload = _payload(path)
    edited = next(
        row for row in payload["manual_checks"] if row["code"] != "secret_scan"
    )
    edited["summary"] = "hand-written by an operator"
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "signed_worksheet_drift"


def test_supersede_appends_without_touching_the_old_revision(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="PASS",
    )
    first = effective_revision(
        published_project.root, published_project.version, "secret_scan"
    )
    before = first.path.read_bytes()
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-b", decision="FAIL",
        conclusion="the first judgement was wrong", supersede=True,
    )
    chain = revision_chain(
        published_project.root, published_project.version, "secret_scan"
    )
    assert [row.reference for row in chain] == [first.reference, chain[-1].reference]
    assert first.path.read_bytes() == before
    assert chain[-1].decision == "FAIL"
    assert chain[-1].supersedes == first.reference
    assert chain[-1].operator_id == "operator-b"
    assert chain[-1].payload["supersedes"] == {
        "reference": first.reference,
        "sha256": first.sha256,
    }
    row = _row(path, "secret_scan")
    assert row["status"] == "FAIL"
    assert row["evidence"][0]["reference"] == chain[-1].reference
    assert row["summary"] == "operator rejected on worksheet revision", (
        "a superseded row carries no stale conclusion: the reason lives in the "
        "revision it belongs to"
    )
    assert "the first judgement was wrong" in chain[-1].human
    assert "the first judgement was wrong" not in first.human


def test_supersede_without_a_signed_row_is_refused(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="secret_scan", operator_id="operator-a", decision="PASS",
            conclusion="nothing to replace", supersede=True,
        )
    assert error.value.category == "nothing_to_supersede"


def test_supersede_refuses_a_stale_baseline(
    published_project, tmp_path
) -> None:
    """A checklist left behind by another supersede must not flip the row back.

    The row of the stale copy still binds the revision it signed; the chain
    head has moved on.  Accepting that copy would publish a decision against
    a worksheet the operator never saw as current.
    """
    import shutil

    path = _prepared(published_project, tmp_path)
    stale = tmp_path / "stale.yml"
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="PASS",
    )
    shutil.copy(path, stale)
    confirm(
        published_project.root, path,
        code="secret_scan", operator_id="operator-a", decision="FAIL",
        conclusion="second thoughts", supersede=True,
    )
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, stale,
            code="secret_scan", operator_id="operator-a", decision="PASS",
            conclusion="third thoughts", supersede=True,
        )
    assert error.value.category == "superseded_revision_drift"


def test_confirm_refuses_an_unknown_code(published_project, tmp_path) -> None:
    path = _prepared(published_project, tmp_path)
    with pytest.raises(WorksheetError) as error:
        confirm(
            published_project.root, path,
            code="not_a_check", operator_id="operator-a", decision="PASS",
        )
    assert error.value.category == "unknown_check_code"


def test_every_operator_only_code_accepts_its_own_signing(
    published_project, tmp_path
) -> None:
    path = _prepared(published_project, tmp_path)
    for code in OPERATOR_ONLY_CODES:
        confirm(
            published_project.root, path,
            code=code, operator_id="operator-a", decision="PASS",
            acknowledge=_queue_length(published_project, code),
        )
    payload = _payload(path)
    assert all(
        row["status"] == "PASS"
        for row in payload["manual_checks"]
        if row["code"] in OPERATOR_ONLY_CODES
    )
