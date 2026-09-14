"""Unit behaviour of the worksheet program area (Task 5).

The program area carries what one code's confirmation is judged against: the
candidate rows, the pending-review queue and -- for the three operator-only
codes -- the comparison result and the program-decided confirmation strength.
The strength is never an input: an operator cannot claim
``EXTERNAL_CORROBORATED``, only a comparison with no difference can produce it.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.research.acceptance.external_inputs import (
    CalendarComparison,
    PriceComparison,
    RuleComparison,
    RuleRow,
    VersionFacts,
)
from stock_quant.research.acceptance.worksheet import (
    EXTERNAL_CORROBORATED,
    OPERATOR_ATTESTED,
    WorksheetError,
)
from stock_quant.research.acceptance.worksheet_program import (
    build_program,
    candidate_bytes,
    candidate_rows,
    comparison_for_checklist,
    review_queue,
    strength_for,
)


def _facts() -> VersionFacts:
    return VersionFacts(
        open_days=(date(2021, 11, 1), date(2021, 11, 2)),
        rules=(RuleRow("star", "NORMAL", "2019-07-22", "0.20"),),
        price_sample=pd.DataFrame(
            [{"symbol": "600000.SH", "trade_date": date(2021, 11, 1)}]
        ),
    )


def test_candidate_rows_are_grouped_by_the_code_that_reviews_them() -> None:
    assert candidate_rows(_facts()) == {
        "exchange_calendar_sample": (
            "calendar:2021-11-01",
            "calendar:2021-11-02",
        ),
        "trading_rule_effective_dates": ("rule:star|NORMAL|2019-07-22|0.20",),
        "cross_source_price_sample": ("price:600000.SH@2021-11-01",),
    }


def test_candidate_bytes_are_deterministic_and_named_per_code() -> None:
    payload = candidate_bytes("exchange_calendar_sample", ("calendar:2021-11-01",))
    assert payload == candidate_bytes(
        "exchange_calendar_sample", ("calendar:2021-11-01",)
    )
    assert b'"rows"' in payload


def test_mechanisable_codes_never_have_a_queue() -> None:
    assert review_queue(
        "secret_scan", ("row",), previous_rows=("row",)
    ) == ()


def test_first_signing_queues_every_candidate() -> None:
    assert review_queue(
        "exchange_calendar_sample",
        ("calendar:2021-11-01", "calendar:2021-11-02"),
        previous_rows=None,
    ) == ("calendar:2021-11-01", "calendar:2021-11-02")


def test_follow_up_signing_queues_only_the_delta() -> None:
    assert review_queue(
        "exchange_calendar_sample",
        ("calendar:2021-11-01", "calendar:2021-11-02", "calendar:2021-11-03"),
        previous_rows=("calendar:2021-11-01", "calendar:2021-11-02"),
    ) == ("calendar:2021-11-03",)


def test_cross_source_price_always_queues_everything() -> None:
    assert review_queue(
        "cross_source_price_sample",
        ("price:600000.SH@2021-11-01",),
        previous_rows=("price:600000.SH@2021-11-01",),
    ) == ("price:600000.SH@2021-11-01",)


def test_supersede_always_queues_everything() -> None:
    assert review_queue(
        "exchange_calendar_sample",
        ("calendar:2021-11-01",),
        previous_rows=("calendar:2021-11-01",),
        supersede=True,
    ) == ("calendar:2021-11-01",)


def test_strength_is_decided_by_the_comparison_not_by_input() -> None:
    assert strength_for(
        "exchange_calendar_sample",
        CalendarComparison("compared", True, (), (), ()),
    ) == EXTERNAL_CORROBORATED
    assert strength_for(
        "exchange_calendar_sample",
        CalendarComparison("compared", False, (), (), ()),
    ) == OPERATOR_ATTESTED
    assert strength_for(
        "exchange_calendar_sample",
        CalendarComparison("compared", True, ("2021-11-03",), (), ()),
    ) == OPERATOR_ATTESTED
    assert strength_for(
        "trading_rule_effective_dates",
        RuleComparison("compared", (), (), ()),
    ) == EXTERNAL_CORROBORATED
    assert strength_for(
        "trading_rule_effective_dates",
        RuleComparison("compared", ("star|NORMAL|2019-07-22",), (), ()),
    ) == OPERATOR_ATTESTED
    assert strength_for(
        "cross_source_price_sample",
        PriceComparison("compared", "", ("a", "b"), 12, ()),
    ) == OPERATOR_ATTESTED


def test_mechanisable_strength_is_constant() -> None:
    assert strength_for("secret_scan", None) == OPERATOR_ATTESTED


def test_an_unknown_code_is_rejected() -> None:
    with pytest.raises(WorksheetError) as error:
        strength_for("not_a_check", None)
    assert error.value.category == "revision_chain_invalid"


def test_comparison_payload_is_plain_data() -> None:
    assert comparison_for_checklist(
        "exchange_calendar_sample",
        CalendarComparison("compared", True, ("2021-11-03",), (), ()),
    ) == {
        "status": "compared",
        "has_close_column": True,
        "dataset_open_official_absent": ["2021-11-03"],
        "official_open_dataset_absent": [],
        "official_closed_dataset_open": [],
    }


def test_build_program_omits_operator_only_keys_for_mechanisable_codes() -> None:
    program = build_program(
        code="secret_scan",
        dataset_version="a" * 64,
        dataset_manifest_sha256="a" * 64,
        window={"start": "2021-11-01", "end": "2021-11-30"},
        generated_at="2026-09-08T00:00:00+00:00",
        candidate=[{"reference": "data/x.json", "sha256": "b" * 64}],
        previous_signed=None,
        supersedes=None,
    )
    assert set(program) == {
        "code",
        "dataset_version",
        "dataset_manifest_sha256",
        "window",
        "generated_at",
        "candidate_evidence",
        "previous_signed",
        "supersedes",
    }


def test_build_program_carries_the_operator_only_keys() -> None:
    program = build_program(
        code="exchange_calendar_sample",
        dataset_version="a" * 64,
        dataset_manifest_sha256="a" * 64,
        window={"start": "2021-11-01", "end": "2021-11-30"},
        generated_at="2026-09-08T00:00:00+00:00",
        candidate=[{"reference": "data/c.json", "sha256": "b" * 64}],
        previous_signed=None,
        supersedes=None,
        external_input={"reference": "data/i.txt", "sha256": "c" * 64},
        comparison={"status": "compared"},
        strength=EXTERNAL_CORROBORATED,
        queue=["calendar:2021-11-01"],
    )
    assert program["strength"] == EXTERNAL_CORROBORATED
    assert program["queue"] == ["calendar:2021-11-01"]
    assert program["external_input"] == {
        "reference": "data/i.txt",
        "sha256": "c" * 64,
    }
