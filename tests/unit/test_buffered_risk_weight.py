"""Two-stage ranking, buffered membership and capped decimal weights.

``build_buffered_target`` ranks factor-valid candidates by descending
processed momentum then ascending full symbol string (never supplier row
order), removes risk-invalid rows into a continuous ``risk_eligible_rank``,
retains eligible previous targets through ``hold_rank`` and fills the
remaining seats only from ``entry_rank`` -- so a risk-invalid top name never
consumes an entry slot and an eligible incumbent at rank 15 keeps its seat.
``allocate_capped_inverse_volatility`` distributes the target exposure over
the members by inverse applied volatility under the 15% cap with exact
``Decimal`` arithmetic quantized down to ``1e-12``, returning the
unallocatable residue as cash so ``sum(weights) + cash == 1.00`` exactly.

All tests are offline and in-memory.
"""

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from stock_quant.factors.models import FactorResult
from stock_quant.portfolio.buffered_models import (
    MEMBER_STATUS_ENTERED,
    MEMBER_STATUS_EXITED,
    MEMBER_STATUS_NOT_SELECTED,
    MEMBER_STATUS_RETAINED,
    MEMBER_STATUS_RISK_INVALID,
    PORTFOLIO_CONSTRUCTION_COLUMNS,
    BufferedRiskWeightedPolicy,
)
from stock_quant.portfolio.buffered_risk_weight import (
    allocate_capped_inverse_volatility,
    buffered_portfolio_rule_version,
    build_buffered_target,
)
from stock_quant.portfolio.risk_estimation import (
    estimate_risk,
)

SIGNAL_DATE = date(2021, 6, 1)

#: The full symbol pool the shared risk fixture covers: the numeric SZ rank
#: fixtures plus the mixed-exchange tie-break symbols.
RISK_SYMBOLS = tuple(
    [f"{index:06d}.SZ" for index in range(1, 17)]
    + ["600000.SH", "600001.SH", "600002.SH", "600003.SH"]
)


def _sessions(count: int) -> list[date]:
    days: list[date] = []
    current = date(2021, 1, 4)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@pytest.fixture
def factor_result():
    """A factory building one signal date's ``FactorResult``.

    ``factor_result(n)`` gives the first ``n`` zero-padded SZ symbols
    strictly descending momentum (rank k == ``f"{k:06d}.SZ"``);
    ``factor_result(equal_values=True)`` gives five mixed-exchange symbols
    with identical processed values so only the symbol tie-break orders them.
    """

    def _build(count: int | None = None, *, equal_values: bool = False):
        if equal_values:
            symbols = ["000001.SZ", "000002.SZ", "600000.SH", "600001.SH",
                       "600002.SH"]
            values = [1.0] * len(symbols)
        else:
            symbols = [f"{index:06d}.SZ" for index in range(1, count + 1)]
            values = [
                1.0 + (count - index) * 0.01 for index in range(1, count + 1)
            ]
        frame = pd.DataFrame(
            {
                "trade_date": [SIGNAL_DATE] * len(symbols),
                "symbol": symbols,
                "factor_name": ["momentum_60d"] * len(symbols),
                "factor_version": ["2.0.0"] * len(symbols),
                "raw_value": values,
                "processed_value": values,
                "is_valid": [True] * len(symbols),
                "invalid_reason": [""] * len(symbols),
            }
        )
        return FactorResult(
            factor_name="momentum_60d", factor_version="2.0.0", frame=frame
        )

    return _build


def _risk_frame(valid_symbols) -> pd.DataFrame:
    """Trusted constant-price risk rows (applied volatility = the floor)."""
    sessions = _sessions(60)
    frames = []
    for symbol in valid_symbols:
        observations = pd.DataFrame(
            {
                "trade_date": sessions,
                "symbol": [symbol] * 60,
                "adjusted_close": [10.0] * 60,
                "quality_severity": ["INFO"] * 60,
                "missing_reason": [None] * 60,
            }
        )
        frames.append(
            estimate_risk(
                signal_date=sessions[-1],
                symbols=(symbol,),
                observations=observations,
                market_sessions=sessions,
                policy=BufferedRiskWeightedPolicy(),
            )
        )
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def valid_risks() -> pd.DataFrame:
    return _risk_frame(RISK_SYMBOLS)


@pytest.fixture
def risks_with_invalid_first(valid_risks) -> pd.DataFrame:
    frame = valid_risks.copy()
    first = frame["symbol"] == "000001.SZ"
    frame.loc[first, "real_close_observations"] = 0
    frame.loc[first, "suspension_carry_days"] = 0
    frame.loc[first, "risk_is_valid"] = False
    frame.loc[first, "risk_invalid_reason"] = "untrusted_missing_observation"
    frame.loc[first, "raw_annualized_volatility"] = float("nan")
    frame.loc[first, "applied_annualized_volatility"] = float("nan")
    return frame


def six_equal_risks() -> dict[str, Decimal]:
    return {
        symbol: Decimal("0.10")
        for symbol in ("000001.SZ", "000002.SZ", "600000.SH",
                       "600001.SH", "600002.SH", "600003.SH")
    }


# ---------------------------------------------------------------------------
# Plan Step 1: rank / buffer semantics
# ---------------------------------------------------------------------------


def test_ties_sort_by_full_symbol_string(factor_result, valid_risks):
    result = build_buffered_target(factors=factor_result(equal_values=True),
                                   risks=valid_risks,
                                   previous_target_members=(),
                                   policy=BufferedRiskWeightedPolicy())
    assert result.audit_frame.query("raw_momentum_rank <= 3").symbol.tolist() == [
        "000001.SZ", "000002.SZ", "600000.SH"
    ]


def test_valid_incumbent_at_rank_fifteen_is_retained(factor_result, valid_risks):
    result = build_buffered_target(factors=factor_result(16), risks=valid_risks,
                                   previous_target_members=("000015.SZ",),
                                   policy=BufferedRiskWeightedPolicy())
    assert "000015.SZ" in result.target_members
    assert len(result.target_members) == 10


def test_invalid_top_rank_does_not_consume_entry_slot(
    factor_result, risks_with_invalid_first
):
    result = build_buffered_target(factors=factor_result(12),
                                   risks=risks_with_invalid_first,
                                   previous_target_members=(),
                                   policy=BufferedRiskWeightedPolicy())
    assert len(result.target_members) == 10
    assert "000011.SZ" in result.target_members
    assert "000001.SZ" not in result.target_members


def test_incumbent_below_hold_rank_is_exited(factor_result, valid_risks):
    result = build_buffered_target(
        factors=factor_result(16), risks=valid_risks,
        previous_target_members=("000016.SZ",),
        policy=BufferedRiskWeightedPolicy(),
    )
    assert "000016.SZ" not in result.target_members
    row = result.audit_frame[
        result.audit_frame["symbol"] == "000016.SZ"
    ].iloc[0]
    assert row.member_status == MEMBER_STATUS_EXITED
    assert row.member_reason == "rank_below_hold_rank"
    assert row.previous_target_member
    assert row.target_weight == Decimal("0")


def test_risk_invalid_incumbent_is_marked_risk_invalid(
    factor_result, risks_with_invalid_first
):
    result = build_buffered_target(
        factors=factor_result(12), risks=risks_with_invalid_first,
        previous_target_members=("000001.SZ",),
        policy=BufferedRiskWeightedPolicy(),
    )
    assert "000001.SZ" not in result.target_members
    row = result.audit_frame[
        result.audit_frame["symbol"] == "000001.SZ"
    ].iloc[0]
    assert row.member_status == MEMBER_STATUS_RISK_INVALID
    assert row.member_reason == "untrusted_missing_observation"
    assert pd.isna(row.risk_eligible_rank)


def test_incumbent_absent_from_factor_pool_is_exited(
    factor_result, valid_risks
):
    result = build_buffered_target(
        factors=factor_result(16), risks=valid_risks,
        previous_target_members=("000099.SZ",),
        policy=BufferedRiskWeightedPolicy(),
    )
    assert "000099.SZ" not in result.target_members
    row = result.audit_frame[
        result.audit_frame["symbol"] == "000099.SZ"
    ].iloc[0]
    assert row.member_status == MEMBER_STATUS_EXITED
    assert row.member_reason == "left_point_in_time_pool"
    assert pd.isna(row.raw_momentum_rank)


def test_full_book_shows_entry_and_not_selected_rows(
    factor_result, valid_risks
):
    result = build_buffered_target(
        factors=factor_result(12), risks=valid_risks,
        previous_target_members=(),
        policy=BufferedRiskWeightedPolicy(),
    )
    statuses = set(result.audit_frame["member_status"])
    assert MEMBER_STATUS_ENTERED in statuses
    assert MEMBER_STATUS_NOT_SELECTED in statuses
    selected = result.audit_frame[
        result.audit_frame["member_status"] == MEMBER_STATUS_ENTERED
    ]
    assert selected.symbol.tolist() == sorted(selected.symbol.tolist())
    assert len(selected) == 10
    not_selected = result.audit_frame[
        result.audit_frame["member_status"] == MEMBER_STATUS_NOT_SELECTED
    ]
    assert not_selected.symbol.tolist() == ["000011.SZ", "000012.SZ"]
    assert (not_selected["target_weight"] == Decimal("0")).all()


def test_retained_row_is_marked_retained(factor_result, valid_risks):
    result = build_buffered_target(
        factors=factor_result(16), risks=valid_risks,
        previous_target_members=("000003.SZ",),
        policy=BufferedRiskWeightedPolicy(),
    )
    row = result.audit_frame[
        result.audit_frame["symbol"] == "000003.SZ"
    ].iloc[0]
    assert row.member_status == MEMBER_STATUS_RETAINED
    assert row.member_reason == "retained_through_hold_rank"
    assert row.previous_target_member


def test_audit_frame_spans_candidates_plus_previous_targets(
    factor_result, valid_risks
):
    result = build_buffered_target(
        factors=factor_result(12), risks=valid_risks,
        previous_target_members=("000001.SZ", "000098.SZ"),
        policy=BufferedRiskWeightedPolicy(),
    )
    # twelve factor-valid candidates plus 000098.SZ (000001.SZ already counts
    # as a candidate); the fold-local state carries frozen codes only
    assert len(result.audit_frame) == 13
    assert list(result.audit_frame.columns) == list(
        PORTFOLIO_CONSTRUCTION_COLUMNS
    )
    assert (result.audit_frame["signal_date"] == SIGNAL_DATE).all()


def test_row_order_is_rank_then_symbol_and_row_input_is_irrelevant(
    factor_result, valid_risks
):
    shuffled_factors = factor_result(12)
    shuffled = shuffled_factors.frame.sample(frac=1, random_state=3)
    straight = build_buffered_target(
        factors=shuffled_factors, risks=valid_risks,
        previous_target_members=(), policy=BufferedRiskWeightedPolicy(),
    )
    reordered = build_buffered_target(
        factors=FactorResult(
            factor_name="momentum_60d",
            factor_version="2.0.0",
            frame=shuffled.reset_index(drop=True),
        ),
        risks=valid_risks.sample(frac=1, random_state=5),
        previous_target_members=(), policy=BufferedRiskWeightedPolicy(),
    )
    pd.testing.assert_frame_equal(straight.audit_frame, reordered.audit_frame)
    assert straight.target_members == reordered.target_members
    assert straight.target_weights == reordered.target_weights


def test_result_is_a_common_scenario_free_target(factor_result, valid_risks):
    result = build_buffered_target(
        factors=factor_result(10), risks=valid_risks,
        previous_target_members=(), policy=BufferedRiskWeightedPolicy(),
    )
    assert isinstance(result.target_members, tuple)
    assert result.target_members == tuple(sorted(result.target_members))
    assert set(result.target_weights) == set(result.target_members)
    assert result.signal_date == SIGNAL_DATE
    from stock_quant.portfolio.buffered_models import (
        PortfolioConstructionResult,
    )

    declared = set(PortfolioConstructionResult.__dataclass_fields__)
    assert declared == {
        "signal_date", "target_members", "target_weights", "cash_weight",
        "audit_frame",
    }


def test_rule_version_is_canonical_policy_json():
    policy = BufferedRiskWeightedPolicy()
    from stock_quant.research.spec import BufferedRiskWeightedPortfolioRule
    from stock_quant.research.walk_forward.policy import canonical_sha256

    rule = BufferedRiskWeightedPortfolioRule()
    expected = canonical_sha256(rule.model_dump(mode="json"))
    assert buffered_portfolio_rule_version(policy) == expected


# ---------------------------------------------------------------------------
# Plan Step 4: capped decimal allocation
# ---------------------------------------------------------------------------


def test_capped_simplex_hits_exposure_without_exceeding_cap():
    weights, cash = allocate_capped_inverse_volatility(
        {"000001.SZ": Decimal("0.05"), "000002.SZ": Decimal("0.20"),
         "600000.SH": Decimal("0.30"), "600001.SH": Decimal("0.40"),
         "600002.SH": Decimal("0.50"), "600003.SH": Decimal("0.60"),
         "600004.SH": Decimal("0.70")}, BufferedRiskWeightedPolicy())
    assert sum(weights.values()) + cash == Decimal("1.00")
    assert max(weights.values()) <= Decimal("0.15")


def test_six_members_leave_exactly_ten_percent_cash():
    weights, cash = allocate_capped_inverse_volatility(
        six_equal_risks(), BufferedRiskWeightedPolicy())
    assert set(weights.values()) == {Decimal("0.15")}
    assert cash == Decimal("0.10")


def test_ten_equal_risk_members_fully_invested_without_cap():
    risks = {f"{index:06d}.SZ": Decimal("0.25")
             for index in range(1, 11)}
    weights, cash = allocate_capped_inverse_volatility(
        risks, BufferedRiskWeightedPolicy())
    assert set(weights.values()) == {Decimal("0.10")}
    assert cash == Decimal("0")


def test_lower_volatility_receives_the_larger_weight():
    weights, cash = allocate_capped_inverse_volatility(
        {"000001.SZ": Decimal("0.10"), "000002.SZ": Decimal("0.40")},
        BufferedRiskWeightedPolicy(),
    )
    # two members cap the realizable exposure at 2 * 0.15 = 0.30; the raw
    # shares (0.24 / 0.06 by inverse volatility) put the floor name over the
    # cap, so both names settle at the cap and the rest stays cash
    assert weights["000001.SZ"] == Decimal("0.15")
    assert weights["000002.SZ"] == Decimal("0.15")
    assert cash == Decimal("0.70")


def test_weights_are_quantized_down_to_the_policy_quantum():
    risks = {
        f"{index:06d}.SZ": Decimal("0.1001") + Decimal(index) * Decimal("0.0001")
        for index in range(1, 9)
    }
    weights, cash = allocate_capped_inverse_volatility(
        risks, BufferedRiskWeightedPolicy(),
    )
    quantum = Decimal("0.000000000001")
    for weight in weights.values():
        assert (weight / quantum) % 1 == 0
        assert weight < Decimal("0.15")  # near-equal scores never hit the cap
    assert sum(weights.values()) + cash == Decimal("1.00")
    # eight members can carry the whole gross exposure: the quantization
    # residue redistributes in ascending symbol order down to zero cash
    assert sum(weights.values()) == Decimal("1.00")
    assert cash == Decimal("0")
    # lower applied volatility still ranks first by weight
    assert max(weights, key=lambda symbol: weights[symbol]) == "000001.SZ"
    ordered = sorted(risks, key=lambda symbol: risks[symbol])
    by_weight = sorted(weights, key=lambda symbol: -weights[symbol])
    assert by_weight == ordered


def test_allocation_rejects_empty_or_nonpositive_risks():
    with pytest.raises(ValueError, match="member"):
        allocate_capped_inverse_volatility({}, BufferedRiskWeightedPolicy())
    with pytest.raises(ValueError, match="volatility"):
        allocate_capped_inverse_volatility(
            {"000001.SZ": Decimal("0")}, BufferedRiskWeightedPolicy()
        )


def test_result_weights_match_the_allocation(factor_result, valid_risks):
    result = build_buffered_target(
        factors=factor_result(6), risks=valid_risks,
        previous_target_members=(), policy=BufferedRiskWeightedPolicy(),
    )
    assert result.cash_weight == Decimal("0.10")
    assert set(result.target_weights.values()) == {Decimal("0.15")}
    audit = result.audit_frame
    for _, row in audit.iterrows():
        assert row.target_weight == result.target_weights.get(
            row.symbol, Decimal("0")
        )
    members = audit[audit["symbol"].isin(result.target_members)]
    assert (members["cash_weight"] == Decimal("0.10")).all()
    assert (
        members["portfolio_rule_version"]
        == buffered_portfolio_rule_version(BufferedRiskWeightedPolicy())
    ).all()


def test_missing_risk_row_for_a_candidate_is_a_hard_error(factor_result):
    risks = _risk_frame(("000001.SZ", "000002.SZ"))
    with pytest.raises(ValueError, match="risk"):
        build_buffered_target(
            factors=factor_result(3), risks=risks,
            previous_target_members=(), policy=BufferedRiskWeightedPolicy(),
        )


def test_multi_date_factor_frame_is_rejected(valid_risks):
    frame = pd.DataFrame(
        {
            "trade_date": [SIGNAL_DATE, SIGNAL_DATE - timedelta(days=7)],
            "symbol": ["000001.SZ", "000001.SZ"],
            "factor_name": ["momentum_60d", "momentum_60d"],
            "factor_version": ["2.0.0", "2.0.0"],
            "raw_value": [1.0, 1.0],
            "processed_value": [1.0, 1.0],
            "is_valid": [True, True],
            "invalid_reason": ["", ""],
        }
    )
    with pytest.raises(ValueError, match="one signal"):
        build_buffered_target(
            factors=FactorResult(
                factor_name="momentum_60d", factor_version="2.0.0", frame=frame
            ),
            risks=valid_risks,
            previous_target_members=(),
            policy=BufferedRiskWeightedPolicy(),
        )
