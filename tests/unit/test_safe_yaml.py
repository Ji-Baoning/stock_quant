"""The shared fail-closed YAML reader every configuration loader now uses.

``yaml.safe_load`` keeps the *last* of two identical mapping keys, so a
configuration file can load, hash and run under a value no reader of the file
can see.  ``stock_quant.safe_yaml`` is the one entry point for the documents
that decide an experiment identity, an acceptance decision, a trading rule or a
universe, and it rejects a repeated mapping key at any nesting level.  These
tests pin the two halves of that contract: what must be rejected, and what must
still be accepted (merge keys, ordinary documents, non-mapping keys).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stock_quant.safe_yaml import DuplicateKeyError, load_yaml, read_yaml


def test_repeated_top_level_key_fails_closed():
    document = "trust_mode: research\ntrust_mode: engineering\n"
    with pytest.raises(DuplicateKeyError, match="duplicate key 'trust_mode'"):
        load_yaml(document)


def test_repeated_key_two_levels_down_fails_closed():
    document = (
        "experiment:\n"
        "  window:\n"
        "    start_date: 2020-01-01\n"
        "    end_date: 2026-08-27\n"
        "    start_date: 2019-01-01\n"
    )
    with pytest.raises(DuplicateKeyError, match="duplicate key 'start_date'"):
        load_yaml(document)


def test_repeated_key_inside_a_sequence_item_fails_closed():
    document = "rules:\n  - board: sh_main\n    status: NORMAL\n    board: sz_main\n"
    with pytest.raises(DuplicateKeyError, match="duplicate key 'board'"):
        load_yaml(document)


def test_repeated_key_is_rejected_for_every_value_shape():
    """The check is structural: it never depends on how a value would be typed.

    Two ``null``/``~``/empty values and two ``true`` spellings are all repeats
    of one key; none of them may be resolved to whatever the last line says.
    """
    for document in (
        "data_acceptance_id:\ndata_acceptance_id: null\n",
        "enabled: true\nenabled: yes\n",
        "top_n: 10\ntop_n: 10\n",
    ):
        with pytest.raises(DuplicateKeyError):
            load_yaml(document)


def test_merge_key_merge_then_override_is_not_a_repeat():
    """``<<`` is the language's own rule, not a duplicated key.

    A merge key plus an explicit override of a merged key is valid YAML and
    must keep working: the loader compares only the keys actually written out.
    """
    document = (
        "defaults: &defaults\n"
        "  winsorization: none\n"
        "  standardization: none\n"
        "preprocessing:\n"
        "  <<: *defaults\n"
        "  standardization: none\n"
    )
    loaded = load_yaml(document)
    assert loaded["preprocessing"] == {
        "winsorization": "none",
        "standardization": "none",
    }


def test_ordinary_document_still_loads_and_empty_document_is_none():
    loaded = load_yaml("top_n: 10\nboard: sh_main\n")
    assert loaded == {"top_n": 10, "board": "sh_main"}
    assert load_yaml("") is None
    assert load_yaml("# only a comment\n") is None


def test_read_yaml_names_the_offending_file(tmp_path: Path):
    path = tmp_path / "universe.yml"
    path.write_text("entries: []\nentries: []\n", encoding="utf-8")
    with pytest.raises(DuplicateKeyError, match=str(path)):
        read_yaml(path)


def test_read_yaml_reads_utf8(tmp_path: Path):
    path = tmp_path / "hypothesis.yml"
    path.write_text("hypothesis: 过去 60 个交易日的复权收益\n", encoding="utf-8")
    assert read_yaml(path) == {"hypothesis": "过去 60 个交易日的复权收益"}


def test_duplicate_key_error_is_both_a_parse_and_a_value_error():
    """Callers translate this error in two different, both-correct ways.

    The acceptance worksheet turns an unreadable embedded block into
    ``program_unreadable`` / ``marker_invalid`` by catching ``yaml.YAMLError``;
    the CLI turns unusable checklist input into ``invalid_checklist`` by
    catching ``ValueError``.  A repeated key must reach both, not escape as a
    bare traceback.
    """
    assert issubclass(DuplicateKeyError, yaml.YAMLError)
    assert issubclass(DuplicateKeyError, ValueError)
