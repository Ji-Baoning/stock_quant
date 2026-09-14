"""Unit behaviour of the standing acceptance worksheet format (Task 1).

The worksheet is one markdown file per (dataset version, manual check code)
with a machine-written program area and an append-only human area.  A signed
revision is content-addressed by its own file name, so the format has to be
parseable without guessing: the four markers are validated fail-closed and
every signature block is written by ``confirm`` alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stock_quant.research.acceptance.worksheet import (
    MARKER_HUMAN_BEGIN,
    MARKER_HUMAN_END,
    MARKER_PROGRAM_BEGIN,
    MARKER_PROGRAM_END,
    WorksheetError,
    append_signature,
    effective_revision,
    last_signature,
    latest_pass_revision,
    pending_path,
    previous_pass_revision,
    program_payload,
    render_program,
    render_worksheet,
    revision_chain,
    signed_codes,
    verify_markers,
    write_revision,
)


def test_layout_puts_pending_and_revisions_in_separate_shapes(tmp_path: Path) -> None:
    root = tmp_path
    assert pending_path(root, "a" * 64, "secret_scan") == (
        root / "data" / "acceptance-worksheets" / ("a" * 64) / "secret_scan.md"
    )


def test_markers_round_trip_a_program_area() -> None:
    payload = {"code": "secret_scan", "dataset_version": "a" * 64}
    text = render_worksheet(render_program(payload), "")
    program, human = verify_markers(text)
    assert program_payload(program) == payload
    assert human.strip() == ""


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace(MARKER_PROGRAM_END, ""),
        lambda text: text.replace(MARKER_PROGRAM_BEGIN, MARKER_PROGRAM_BEGIN * 2),
        lambda text: text.replace(MARKER_HUMAN_BEGIN, ""),
        lambda text: text.replace(MARKER_HUMAN_END, ""),
        lambda text: text.replace(MARKER_HUMAN_BEGIN, "<!-- ws:unknown:begin -->"),
    ],
)
def test_marker_violations_fail_closed(mutate) -> None:
    text = render_worksheet(render_program({"code": "secret_scan"}), "")
    with pytest.raises(WorksheetError) as error:
        verify_markers(mutate(text))
    assert error.value.category == "marker_invalid"


def test_reordered_markers_fail_closed() -> None:
    text = (
        f"{MARKER_HUMAN_BEGIN}\nhuman\n{MARKER_HUMAN_END}\n"
        f"{MARKER_PROGRAM_BEGIN}\n```yaml\ncode: x\n```\n{MARKER_PROGRAM_END}\n"
    )
    with pytest.raises(WorksheetError) as error:
        verify_markers(text)
    assert error.value.category == "marker_invalid"


def test_unparseable_program_area_is_named() -> None:
    text = render_worksheet(f"{MARKER_PROGRAM_BEGIN}\nnot yaml\n", "")
    program, _ = verify_markers(text)
    with pytest.raises(WorksheetError) as error:
        program_payload(program)
    assert error.value.category == "program_unreadable"


def test_signature_blocks_append_and_last_one_wins() -> None:
    human = append_signature("", {"operator_id": "a", "decision": "FAIL"})
    human = append_signature(human, {"operator_id": "b", "decision": "PASS"})
    assert last_signature(human) == {"operator_id": "b", "decision": "PASS"}


def test_human_area_without_a_signature_block_is_rejected() -> None:
    with pytest.raises(WorksheetError) as error:
        last_signature("operator typed prose only\n")
    assert error.value.category == "marker_invalid"


_VERSION = "a" * 64
_OTHER_VERSION = "b" * 64


def _revision(
    root: Path,
    *,
    version: str = _VERSION,
    code: str = "secret_scan",
    decision: str = "PASS",
    confirmed_at: str = "2026-09-08T12:00:00+00:00",
    supersedes: str | None = None,
):
    payload = {
        "code": code,
        "dataset_version": version,
        "dataset_manifest_sha256": version,
        "supersedes": {"reference": supersedes} if supersedes else None,
    }
    human = append_signature(
        "",
        {
            "operator_id": "operator-a",
            "confirmed_at": confirmed_at,
            "decision": decision,
            "strength": "OPERATOR_ATTESTED",
            "conclusion": "reviewed",
            "supersedes": supersedes,
        },
    )
    return write_revision(root, version, code, payload, human)


def test_revision_file_name_is_its_own_sha256(tmp_path: Path) -> None:
    revision = _revision(tmp_path)
    assert revision.path.name == f"{revision.sha256}.md"
    assert revision.path.stem == revision.sha256


def test_writing_the_same_revision_twice_is_idempotent(tmp_path: Path) -> None:
    first = _revision(tmp_path)
    second = _revision(tmp_path)
    assert first.reference == second.reference
    assert (
        len(
            list(
                (
                    tmp_path
                    / "data"
                    / "acceptance-worksheets"
                    / _VERSION
                    / "secret_scan"
                ).iterdir()
            )
        )
        == 1
    )


def test_chain_head_is_the_only_revision_nobody_supersedes(tmp_path: Path) -> None:
    first = _revision(tmp_path)
    second = _revision(tmp_path, supersedes=first.reference, decision="FAIL")
    chain = revision_chain(tmp_path, _VERSION, "secret_scan")
    assert [row.reference for row in chain] == [first.reference, second.reference]
    assert effective_revision(tmp_path, _VERSION, "secret_scan").reference == (
        second.reference
    )
    assert previous_pass_revision(tmp_path, _VERSION, "secret_scan").reference == (
        first.reference
    )


def test_a_forked_chain_is_rejected(tmp_path: Path) -> None:
    first = _revision(tmp_path)
    _revision(tmp_path, supersedes=first.reference, decision="FAIL")
    _revision(
        tmp_path, supersedes=first.reference, confirmed_at="2026-09-09T12:00:00+00:00"
    )
    with pytest.raises(WorksheetError) as error:
        revision_chain(tmp_path, _VERSION, "secret_scan")
    assert error.value.category == "revision_chain_invalid"


def test_a_supersedes_target_that_is_absent_is_rejected(tmp_path: Path) -> None:
    _revision(
        tmp_path,
        supersedes=f"data/acceptance-worksheets/{_VERSION}/secret_scan/{'c' * 64}.md",
    )
    with pytest.raises(WorksheetError) as error:
        revision_chain(tmp_path, _VERSION, "secret_scan")
    assert error.value.category == "revision_chain_invalid"


def test_a_tampered_revision_file_is_rejected(tmp_path: Path) -> None:
    revision = _revision(tmp_path)
    revision.path.write_text(
        revision.path.read_text(encoding="utf-8") + "\nstray\n", encoding="utf-8"
    )
    with pytest.raises(WorksheetError) as error:
        revision_chain(tmp_path, _VERSION, "secret_scan")
    assert error.value.category == "revision_chain_invalid"


def test_an_unexpected_file_in_the_revision_directory_is_rejected(
    tmp_path: Path,
) -> None:
    _revision(tmp_path)
    stray = (
        tmp_path
        / "data"
        / "acceptance-worksheets"
        / _VERSION
        / "secret_scan"
        / "notes.md"
    )
    stray.write_text("operator notes\n", encoding="utf-8")
    with pytest.raises(WorksheetError) as error:
        revision_chain(tmp_path, _VERSION, "secret_scan")
    assert error.value.category == "revision_chain_invalid"


def test_latest_pass_revision_spans_versions_and_refuses_ties(tmp_path: Path) -> None:
    _revision(
        tmp_path, version=_OTHER_VERSION, confirmed_at="2026-09-01T12:00:00+00:00"
    )
    newest = _revision(tmp_path, confirmed_at="2026-09-08T12:00:00+00:00")
    found = latest_pass_revision(tmp_path, "secret_scan", exclude_version=_VERSION)
    assert found.reference != newest.reference
    _revision(tmp_path, version="d" * 64, confirmed_at="2026-09-01T12:00:00+00:00")
    with pytest.raises(WorksheetError) as error:
        latest_pass_revision(tmp_path, "secret_scan", exclude_version=_VERSION)
    assert error.value.category == "previous_signed_ambiguous"


def test_signed_codes_reports_only_chains_with_a_head(tmp_path: Path) -> None:
    assert signed_codes(tmp_path, _VERSION) == ()
    _revision(tmp_path)
    assert signed_codes(tmp_path, _VERSION) == ("secret_scan",)
