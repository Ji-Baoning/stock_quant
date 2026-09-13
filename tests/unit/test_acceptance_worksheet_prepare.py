"""Unit behaviour of the prepare side of the worksheet line (Task 6).

``prepare`` must be safe to re-run: it never touches a signed revision, and a
re-run over a signed version refuses *before writing anything* unless
``--force`` is given.  ``--force`` restores the signed rows into a rebuilt
checklist without inventing, discarding or re-dating a conclusion.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from stock_quant.research.acceptance.models import (
    MANUAL_CHECK_CODES,
    ManualCheckStatus,
)
from stock_quant.research.acceptance.service import prepare_checklist
from stock_quant.research.acceptance.worksheet import (
    MARKER_HUMAN_BEGIN,
    WorksheetError,
    append_signature,
    pending_path,
    program_payload,
    verify_markers,
    write_revision,
)
from stock_quant.research.acceptance.worksheet_prepare import window_of

_PREPARED_AT = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _prepare(project, tmp_path: Path, name: str, **kwargs):
    return prepare_checklist(
        project.root,
        project.version,
        "operator-a",
        tmp_path / name,
        prepared_at=_PREPARED_AT,
        **kwargs,
    )


def _program(project, code: str) -> dict:
    """One pending worksheet's program area, as parsed."""
    text = pending_path(project.root, project.version, code).read_text(
        encoding="utf-8"
    )
    return program_payload(verify_markers(text)[0])


def _sign(project, code: str, decision: str = "PASS"):
    """Store one signed revision directly, without going through ``confirm``.

    This task is the prepare side only.  A hand-built revision is the smallest
    thing that puts a version into the "already signed" state this side has to
    refuse, so these tests do not depend on the confirm writer of Task 7.
    """
    path = pending_path(project.root, project.version, code)
    program, human = verify_markers(path.read_text(encoding="utf-8"))
    human = append_signature(
        human,
        {
            "operator_id": "operator-a",
            "confirmed_at": _PREPARED_AT.isoformat(),
            "decision": decision,
            "strength": "OPERATOR_ATTESTED",
            "conclusion": "reviewed",
            "supersedes": None,
        },
    )
    return write_revision(
        project.root, project.version, code, program_payload(program), human
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under a root, so "wrote nothing" can be asserted exactly."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_prepare_writes_nine_unsigned_worksheets(published_project, tmp_path) -> None:
    checklist = _prepare(published_project, tmp_path, "checklist.yml")
    for code in MANUAL_CHECK_CODES:
        path = pending_path(published_project.root, published_project.version, code)
        assert MARKER_HUMAN_BEGIN in path.read_text(encoding="utf-8")
        assert _program(published_project, code)["code"] == code
    assert all(
        row.status is ManualCheckStatus.PENDING_CONFIRMATION
        for row in checklist.manual_checks
    )


def test_the_window_comes_from_the_version_manifest(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    assert window_of(published_project.root, published_project.version) == (
        date(2021, 11, 1),
        date(2021, 11, 30),
    )
    assert _program(published_project, "secret_scan")["window"] == {
        "start": "2021-11-01",
        "end": "2021-11-30",
    }


def test_a_mechanisable_worksheet_cites_the_evidence_pack(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    program = _program(published_project, "secret_scan")
    assert program["candidate_evidence"][0]["reference"].startswith(
        "data/acceptance-evidence/"
    )
    assert "queue" not in program
    assert "strength" not in program


def test_an_operator_only_worksheet_carries_its_own_contract(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    program = _program(published_project, "exchange_calendar_sample")
    assert program["candidate_evidence"][0]["reference"].startswith(
        "data/acceptance-external-inputs/"
    )
    assert program["external_input"] is None
    assert program["comparison"] == {"status": "no_external_input"}
    assert program["strength"] == "OPERATOR_ATTESTED"
    assert program["queue"], "a first signing queues the whole candidate set"


def test_a_rerun_over_a_signed_version_writes_nothing(
    published_project, tmp_path, monkeypatch
) -> None:
    """The pre-check must fire *before* the evidence pack is rebuilt.

    Rebuilding the pack is itself a write, so a check that ran after it would
    already have touched the tree it is supposed to protect.  Booby-trapping
    the rebuild is the only assertion that proves the order: a snapshot
    comparison would pass even if the pack had been rewritten, because the
    rebuild is deterministic and leaves the same bytes behind.
    """
    _prepare(published_project, tmp_path, "checklist.yml")
    _sign(published_project, "secret_scan")
    before = _snapshot(published_project.root)

    def _explode(*args, **kwargs):
        raise AssertionError("the evidence pack was rebuilt before the pre-check")

    monkeypatch.setattr(
        "stock_quant.research.acceptance.service.build_mechanisable_evidence",
        _explode,
    )
    with pytest.raises(WorksheetError) as error:
        _prepare(published_project, tmp_path, "again.yml")
    assert error.value.category == "signed_worksheets_present"
    assert _snapshot(published_project.root) == before


def test_force_restores_signed_rows_without_touching_the_revision(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    revision = _sign(published_project, "secret_scan")
    before = revision.path.read_bytes()
    checklist = _prepare(published_project, tmp_path, "forced.yml", force=True)
    row = next(row for row in checklist.manual_checks if row.code == "secret_scan")
    assert row.status is ManualCheckStatus.PASS
    assert row.evidence[0].reference == revision.reference
    assert row.evidence[0].sha256 == revision.sha256
    assert revision.path.read_bytes() == before
    assert not pending_path(
        published_project.root, published_project.version, "secret_scan"
    ).exists()
    assert all(
        other.status is ManualCheckStatus.PENDING_CONFIRMATION
        for other in checklist.manual_checks
        if other.code != "secret_scan"
    )


def test_force_refuses_when_a_signed_revision_drifted(
    published_project, tmp_path
) -> None:
    """``--force`` restores signed rows; it never repairs a broken revision."""
    _prepare(published_project, tmp_path, "checklist.yml")
    revision = _sign(published_project, "secret_scan")
    revision.path.chmod(0o644)
    revision.path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WorksheetError) as error:
        _prepare(published_project, tmp_path, "forced.yml", force=True)
    assert error.value.category == "signed_worksheet_drift"
    assert revision.path.read_text(encoding="utf-8") == "tampered\n"
    assert not (tmp_path / "forced.yml").exists()


def test_a_failed_evidence_build_still_writes_a_worksheet(
    published_project, tmp_path, monkeypatch
) -> None:
    from stock_quant.research.acceptance import evidence

    def _fail(*args, **kwargs):
        raise evidence.EvidenceBuildError("dataset_unreadable")

    monkeypatch.setattr(
        "stock_quant.research.acceptance.service.build_mechanisable_evidence", _fail
    )
    checklist = _prepare(published_project, tmp_path, "checklist.yml")
    assert all(not row.evidence for row in checklist.manual_checks)
    assert _program(published_project, "secret_scan")["candidate_evidence"] == []
    assert _program(published_project, "exchange_calendar_sample")[
        "candidate_evidence"
    ], "the operator-only snapshot does not depend on the evidence pack"


def test_a_tampered_revision_breaks_its_own_chain(published_project, tmp_path) -> None:
    from stock_quant.research.acceptance.worksheet import revision_chain

    _prepare(published_project, tmp_path, "checklist.yml")
    revision = _sign(published_project, "secret_scan")
    revision.path.chmod(0o644)
    revision.path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WorksheetError) as error:
        revision_chain(
            published_project.root, published_project.version, "secret_scan"
        )
    assert error.value.category == "revision_chain_invalid"


def test_an_unexpected_file_in_a_revision_directory_is_rejected(
    published_project, tmp_path
) -> None:
    from stock_quant.research.acceptance.worksheet import revisions_dir

    _prepare(published_project, tmp_path, "checklist.yml")
    _sign(published_project, "secret_scan")
    stray = (
        revisions_dir(published_project.root, published_project.version, "secret_scan")
        / "operator_notes.md"
    )
    stray.write_text("notes\n", encoding="utf-8")
    with pytest.raises(WorksheetError) as error:
        _prepare(published_project, tmp_path, "forced.yml", force=True)
    # The restore path deliberately reports a revision chain it cannot
    # explain as signed-worksheet drift (worksheet_prepare re-raises every
    # chain failure that way); the chain-level category itself is covered by
    # test_a_tampered_revision_breaks_its_own_chain.
    assert error.value.category == "signed_worksheet_drift"
    assert stray.exists(), "the stray file is reported, never swept away"
    assert not (tmp_path / "forced.yml").exists()


def test_worksheets_are_written_outside_the_version_evidence_pack(
    published_project, tmp_path
) -> None:
    _prepare(published_project, tmp_path, "checklist.yml")
    pack = (
        published_project.root
        / "data"
        / "acceptance-evidence"
        / published_project.version
    )
    assert pack.is_dir()
    assert not (pack / "acceptance-worksheets").exists()
    assert not (pack / "secret_scan.md").exists()


def test_rebuilding_the_evidence_pack_leaves_its_bytes_unchanged(
    published_project, tmp_path
) -> None:
    """The pack is rebuilt on every prepare; its bytes must not drift.

    A signed checklist row cites pack files by hash, so a rebuild that changed
    a single byte would break published records retroactively.  This is also
    why the candidate snapshots of the operator-only codes were put in the
    external-input store instead of in the pack.
    """
    from stock_quant.research.acceptance.evidence import (
        EVIDENCE_FILENAMES,
        build_mechanisable_evidence,
    )

    _prepare(published_project, tmp_path, "checklist.yml")
    pack = (
        published_project.root
        / "data"
        / "acceptance-evidence"
        / published_project.version
    )
    before = {
        name: hashlib.sha256((pack / name).read_bytes()).hexdigest()
        for name in EVIDENCE_FILENAMES.values()
    }
    build_mechanisable_evidence(published_project.root, published_project.version)
    after = {
        name: hashlib.sha256((pack / name).read_bytes()).hexdigest()
        for name in EVIDENCE_FILENAMES.values()
    }
    assert before == after
