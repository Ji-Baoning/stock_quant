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
    last_signature,
    pending_path,
    program_payload,
    render_program,
    render_worksheet,
    verify_markers,
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
