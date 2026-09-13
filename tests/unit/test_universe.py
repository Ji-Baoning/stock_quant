"""Fixed engineering-universe configuration tests (Task 5)."""

import re
from datetime import date
from pathlib import Path

from stock_quant.data_model.universe import Universe

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_YAML = ROOT / "templates" / "project-config" / "universe.yml"

_CANONICAL_SYMBOL = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")

# The exact 30 engineering samples fixed by the Task 5 brief, grouped by board.
SH_MAIN = {
    "600000.SH",
    "600036.SH",
    "600519.SH",
    "601318.SH",
    "601398.SH",
    "601857.SH",
    "601919.SH",
    "603288.SH",
}
SZ_MAIN = {
    "000001.SZ",
    "000333.SZ",
    "000651.SZ",
    "000858.SZ",
    "002415.SZ",
    "002475.SZ",
    "002594.SZ",
    "002714.SZ",
}
CHINEXT = {
    "300001.SZ",
    "300059.SZ",
    "300122.SZ",
    "300274.SZ",
    "300750.SZ",
    "300760.SZ",
    "301269.SZ",
}
STAR = {
    "688001.SH",
    "688008.SH",
    "688009.SH",
    "688036.SH",
    "688111.SH",
    "688506.SH",
    "688981.SH",
}
ALL_SYMBOLS = SH_MAIN | SZ_MAIN | CHINEXT | STAR
EXPECTED_BOARDS = {
    "sh_main": SH_MAIN,
    "sz_main": SZ_MAIN,
    "chinext": CHINEXT,
    "star": STAR,
}


def test_universe_has_exact_board_quotas():
    universe = Universe.from_yaml(UNIVERSE_YAML)
    assert len(universe.entries) == 30
    expected = {"sh_main": 8, "sz_main": 8, "chinext": 7, "star": 7}
    assert universe.counts_by_board() == expected
    assert all(e.selection_reason and e.boundary_tags for e in universe.entries)


def test_universe_symbols_are_exactly_the_brief_sample_per_board():
    universe = Universe.from_yaml(UNIVERSE_YAML)
    by_board: dict[str, set[str]] = {}
    for entry in universe.entries:
        by_board.setdefault(entry.board, set()).add(entry.symbol)

    assert set(universe.symbols) == ALL_SYMBOLS
    assert by_board == EXPECTED_BOARDS


def test_each_entry_carries_the_required_point_in_time_fields():
    universe = Universe.from_yaml(UNIVERSE_YAML)
    for entry in universe.entries:
        assert entry.symbol in ALL_SYMBOLS
        assert _CANONICAL_SYMBOL.match(entry.symbol)
        assert entry.name_at_selection
        assert entry.exchange in {"SH", "SZ"}
        assert entry.selected_as_of == date(2026, 9, 3)


def test_exchange_and_board_are_consistent_with_each_symbol():
    universe = Universe.from_yaml(UNIVERSE_YAML)
    for entry in universe.entries:
        code = entry.symbol[:6]
        if entry.symbol in STAR:
            assert entry.exchange == "SH" and entry.board == "star"
        elif entry.symbol in SH_MAIN:
            assert entry.exchange == "SH" and entry.board == "sh_main"
        elif code.startswith("3"):
            assert entry.exchange == "SZ" and entry.board == "chinext"
        else:
            assert entry.exchange == "SZ" and entry.board == "sz_main"


def test_post_2020_listings_are_tagged_for_pre_listing_gaps():
    universe = Universe.from_yaml(UNIVERSE_YAML)
    post_2020 = {"301269.SZ", "688506.SH", "688981.SH"}
    tags_by_symbol = {e.symbol: set(e.boundary_tags) for e in universe.entries}

    for symbol in post_2020:
        assert "pre_listing_gap_expected" in tags_by_symbol[symbol]
    for symbol in ALL_SYMBOLS - post_2020:
        assert "pre_listing_gap_expected" not in tags_by_symbol[symbol]


def test_universe_version_is_deterministic_sha256_of_canonical_content():
    first = Universe.from_yaml(UNIVERSE_YAML)
    second = Universe.from_yaml(UNIVERSE_YAML)

    assert first.version == second.version
    assert len(first.version) == 64
    assert all(c in "0123456789abcdef" for c in first.version)


def test_universe_version_changes_when_a_name_field_changes():
    universe = Universe.from_yaml(UNIVERSE_YAML)
    renamed = [
        entry.model_copy(update={"name_at_selection": "重命名占位"})
        for entry in universe.entries
    ]

    assert Universe.from_entries(renamed).version != universe.version


def test_entries_are_sorted_uniquely_by_symbol():
    universe = Universe.from_yaml(UNIVERSE_YAML)
    symbols = [entry.symbol for entry in universe.entries]
    assert symbols == sorted(symbols)
    assert len(symbols) == len(set(symbols))
