"""Unit behaviour of the content-addressed external input store (Task 3).

External inputs are the official excerpts an operator supplies.  They are
copied into the project under ``data/acceptance-external-inputs/<sha256>/``
and never overwritten, because a published ``ACCEPTED`` record cites them by
path and hash: a replaced excerpt must be detected, not silently accepted.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.research.acceptance.external_inputs import (
    PriceComparison,
    RuleRow,
    VersionFacts,
    compare_calendar,
    compare_for_code,
    compare_price_sources,
    compare_trading_rules,
    load_blob,
    parse_calendar_excerpt,
    parse_rule_excerpt,
    price_sample_frame,
    rule_rows,
    store_blob,
)
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


_RULE_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "project-config"
    / "trading_rules.yml"
)


def _excerpt_text(*rows: str) -> bytes:
    header = "board,status,effective_from,rate,source_url"
    return ("\n".join((header, *rows)) + "\n").encode("utf-8")


def test_a_single_column_calendar_excerpt_cannot_corroborate() -> None:
    excerpt = parse_calendar_excerpt(b"# official\n2021-11-01\n2021-11-02\n")
    assert excerpt.has_close_column is False
    compared = compare_calendar(
        [date(2021, 11, 1), date(2021, 11, 3)], excerpt
    )
    assert compared.status == "compared"
    assert compared.dataset_open_official_absent == ("2021-11-03",)
    assert compared.official_open_dataset_absent == ("2021-11-02",)
    assert compared.corroborated is False


def test_a_two_column_calendar_excerpt_corroborates_only_when_identical() -> None:
    excerpt = parse_calendar_excerpt(b"2021-11-01 1\n2021-11-02 0\n")
    assert excerpt.has_close_column is True
    exact = compare_calendar([date(2021, 11, 1)], excerpt)
    assert exact.official_closed_dataset_open == ()
    assert exact.corroborated is True
    conflicting = compare_calendar(
        [date(2021, 11, 1), date(2021, 11, 2)], excerpt
    )
    assert conflicting.official_closed_dataset_open == ("2021-11-02",)
    assert conflicting.corroborated is False


def test_a_malformed_calendar_excerpt_is_rejected() -> None:
    with pytest.raises(WorksheetError) as error:
        parse_calendar_excerpt(b"2021-11-01 maybe\n")
    assert error.value.category == "external_input_invalid"


def test_trading_rules_corroborate_only_when_every_row_is_covered() -> None:
    rows = rule_rows(_RULE_CONFIG)
    assert rows, "the shipped trading rules config declares price limits"
    excerpt = parse_rule_excerpt(
        _excerpt_text(
            *(
                f"{row.board},{row.status},{row.effective_from},{row.rate},"
                "https://example.invalid/official"
                for row in rows
            )
        )
    )
    complete = compare_trading_rules(rows, excerpt)
    assert complete.status == "compared"
    assert complete.corroborated is True

    partial = parse_rule_excerpt(
        _excerpt_text(
            f"{rows[0].board},{rows[0].status},{rows[0].effective_from},"
            "0.99,https://example.invalid/official"
        )
    )
    incomplete = compare_trading_rules(rows, partial)
    assert incomplete.conflicting == (f"{rows[0].board}|{rows[0].status}|"
                                      f"{rows[0].effective_from}",)
    assert incomplete.uncovered
    assert incomplete.corroborated is False


def test_rule_rates_compare_numerically_not_textually() -> None:
    rows = (RuleRow("star", "NORMAL", "2019-07-22", "0.10"),)
    excerpt = parse_rule_excerpt(
        _excerpt_text("star,NORMAL,2019-07-22,0.1,https://example.invalid")
    )
    assert compare_trading_rules(rows, excerpt).corroborated is True


def test_a_malformed_rule_excerpt_is_rejected() -> None:
    with pytest.raises(WorksheetError) as error:
        parse_rule_excerpt(b"board,status\nstar,NORMAL\n")
    assert error.value.category == "external_input_invalid"


def _price_frame(sources: tuple[str, ...]) -> pd.DataFrame:
    records = []
    for source in sources:
        # The baseline difference stays inside the design thresholds
        # (0.05% relative on close, under the 0.002 error line), so the
        # ``disagreeing`` case below is what actually crosses them.
        close = 10.0 if source == "primary" else 10.005
        records.append(
            {
                "trade_date": date(2021, 11, 1),
                "symbol": "600000.SH",
                "source": source,
                "adjustment": "unadjusted",
                "volume_unit": "share",
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": close,
            }
        )
    return pd.DataFrame(records)


def test_price_sample_takes_the_window_edges() -> None:
    daily = pd.DataFrame(
        {
            "trade_date": [date(2021, 11, 1), date(2021, 11, 2), date(2021, 11, 3)],
            "symbol": ["600000.SH"] * 3,
        }
    )
    sample = price_sample_frame(
        daily,
        [date(2021, 11, 1), date(2021, 11, 2), date(2021, 11, 3)],
    )
    assert sorted(sample["trade_date"]) == [date(2021, 11, 1), date(2021, 11, 3)]


def test_a_single_price_source_refuses_to_compare() -> None:
    compared = compare_price_sources(_price_frame(("primary",)))
    assert compared.status == "not_comparable"
    assert compared.reason == "single_price_source"
    assert compared.sources == ("primary",)


def test_two_sources_are_compared_under_the_design_thresholds() -> None:
    within = compare_price_sources(_price_frame(("primary", "secondary")))
    assert within.status == "compared"
    assert within.exceeding == ()
    assert within.rows_compared == 1

    disagreeing = _price_frame(("primary", "secondary"))
    disagreeing.loc[1, "close"] = 11.0
    exceeding = compare_price_sources(disagreeing)
    assert exceeding.exceeding == ("600000.SH@2021-11-01",)


def test_sources_that_never_share_a_bar_are_not_comparable() -> None:
    """Two sources dividing the symbol space must not read as a clean pass.

    The shape this guards is the trusted fixture's: equities carried by one
    source and benchmarks by another, so no ``(symbol, trade_date)`` has two
    readings.  ``compared`` with ``rows_compared=0`` would claim the cross
    check ran and found nothing.
    """
    frame = pd.concat(
        [
            _price_frame(("primary",)).assign(symbol="600000.SH"),
            _price_frame(("secondary",)).assign(symbol="000300.SH"),
        ],
        ignore_index=True,
    )
    compared = compare_price_sources(frame)
    assert compared.status == "not_comparable"
    assert compared.reason == "no_paired_bars"
    assert compared.sources == ("primary", "secondary")
    assert compared.rows_compared == 0
    assert compared.exceeding == ()


def test_compare_for_code_dispatches_on_the_code() -> None:
    """One entry point, so prepare and confirm cannot build two comparisons."""
    facts = VersionFacts(
        open_days=(date(2021, 11, 1),),
        rules=rule_rows(_RULE_CONFIG),
        price_sample=_price_frame(("primary", "secondary")),
    )
    assert (
        compare_for_code("exchange_calendar_sample", facts, None).status
        == "no_external_input"
    )
    assert (
        compare_for_code("trading_rule_effective_dates", facts, None).status
        == "no_external_input"
    )
    # The price comparison takes no excerpt at all: it is always the version's
    # own cross-source sample, so ``data`` is ignored for that code.
    price = compare_for_code("cross_source_price_sample", facts, None)
    assert isinstance(price, PriceComparison)
    assert price.status == "compared"
    assert price.rows_compared == 1
    assert compare_for_code(
        "exchange_calendar_sample", facts, b"2021-11-01 1\n"
    ).corroborated is True
    with pytest.raises(WorksheetError) as error:
        compare_for_code("secret_scan", facts, None)
    assert error.value.category == "unknown_check_code"
