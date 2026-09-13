"""Unit behaviour of the content-addressed external input store (Task 3).

External inputs are the official excerpts an operator supplies.  They are
copied into the project under ``data/acceptance-external-inputs/<sha256>/``
and never overwritten, because a published ``ACCEPTED`` record cites them by
path and hash: a replaced excerpt must be detected, not silently accepted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from stock_quant.research.acceptance.external_inputs import load_blob, store_blob
from stock_quant.research.acceptance.worksheet import WorksheetError


def test_blob_lands_under_its_own_sha256(tmp_path: Path) -> None:
    stored = store_blob(tmp_path, b"2021-11-01\n", "official_calendar.txt")
    expected = hashlib.sha256(b"2021-11-01\n").hexdigest()
    assert stored.sha256 == expected
    assert stored.reference == (
        f"data/acceptance-external-inputs/{stored.sha256}/official_calendar.txt"
    )
    assert (tmp_path / stored.reference).read_bytes() == b"2021-11-01\n"


def test_the_same_content_is_stored_once_whatever_the_name(tmp_path: Path) -> None:
    first = store_blob(tmp_path, b"same\n", "a.txt")
    second = store_blob(tmp_path, b"same\n", "b.txt")
    assert first.reference == second.reference
    directory = tmp_path / "data" / "acceptance-external-inputs" / first.sha256
    assert len(list(directory.iterdir())) == 1


def test_different_content_never_overwrites(tmp_path: Path) -> None:
    first = store_blob(tmp_path, b"one\n", "official.txt")
    second = store_blob(tmp_path, b"two\n", "official.txt")
    assert first.reference != second.reference
    assert (tmp_path / first.reference).read_bytes() == b"one\n"
    assert (tmp_path / second.reference).read_bytes() == b"two\n"


def test_an_unusable_name_falls_back_to_a_stable_one(tmp_path: Path) -> None:
    assert store_blob(tmp_path, b"x\n", "").name == "external-input"
    assert store_blob(tmp_path, b"y\n", ".hidden").name == "external-input"


def test_load_blob_reads_back_what_was_stored(tmp_path: Path) -> None:
    stored = store_blob(tmp_path, b"payload\n", "official.txt")
    assert load_blob(tmp_path, stored.reference) == b"payload\n"


def test_load_blob_refuses_to_leave_the_project(tmp_path: Path) -> None:
    with pytest.raises(WorksheetError) as error:
        load_blob(tmp_path, "../outside.txt")
    assert error.value.category == "external_input_invalid"
