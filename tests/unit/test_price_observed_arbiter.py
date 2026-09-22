"""The price-observation settlement rule (spec D2/D5).

Every test authors its observation inline: the rule is pure, the channel that
obtains observations is injected, and nothing here touches a network or a wall
clock.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_quant.data_model.corporate_actions import ConflictTerms
from stock_quant.data_pipeline import (
    CODE_OPTIONAL_SOURCE_FAILURE,
    CODE_PRICE_OBSERVED_SETTLEMENT,
    FirstAnsweringArbiter,
    _GuardedArbiter,
    _price_settlement_issue,
)
from stock_quant.data_sources.base import DataRequest, FetchResult, request_key
from stock_quant.data_sources.price_observed import (
    DAILY_ENDPOINT,
    TUSHARE_SOURCE,
    LazyDailyPriceChannel,
    PriceObservation,
    PriceObservedArbiter,
    expected_factor,
    settle,
)

_DAY = date(2023, 7, 17)
_PREV = date(2023, 7, 14)


def terms(
    *,
    symbol="600188.SH",
    cash_per_ten="0.5",
    bonus_per_ten="0.0",
    capitalization_per_ten="0.0",
    rights_per_ten="0.0",
    rights_price_per_share="0.0",
) -> ConflictTerms:
    """One side's stated terms, per ten shares as the suppliers state them."""
    return ConflictTerms(
        symbol=symbol,
        ex_date=_DAY,
        cash_per_ten=Decimal(cash_per_ten),
        bonus_per_ten=Decimal(bonus_per_ten),
        capitalization_per_ten=Decimal(capitalization_per_ten),
        rights_per_ten=Decimal(rights_per_ten),
        rights_price_per_share=Decimal(rights_price_per_share),
    )


def test_expected_factor_is_the_exchange_formula():
    """(prev_close - cash) / (prev_close * (1 + bonus + cap)), per share."""
    priced = terms(cash_per_ten="4.3", bonus_per_ten="5.0")

    assert expected_factor(priced, 30.0) == (30.0 - 0.43) / (30.0 * 1.5)


def test_settles_the_side_the_reference_price_corroborates():
    """cninfo implies 0.98, eastmoney 0.95, and the market applied 0.98.

    At prev_close 10.0 one tick is exactly 0.001, so cninfo sits 0 ticks from
    the observation and eastmoney 30 -- inside the winner tolerance, far
    outside it, and clear of the loser bar.
    """
    cninfo = terms(cash_per_ten="2.0")  # 0.20/share -> (10 - 0.2) / 10
    eastmoney = terms(cash_per_ten="5.0")  # 0.50/share -> (10 - 0.5) / 10
    observation = PriceObservation(
        prev_close=10.0, prev_close_date=_PREV, pre_close=9.80
    )

    settlement = settle(cninfo, eastmoney, observation)

    assert settlement is not None
    assert settlement.side == "cninfo"
    assert settlement.cninfo_ticks == pytest.approx(0.0)
    assert settlement.eastmoney_ticks == pytest.approx(30.0)


def test_two_sides_implying_one_factor_are_never_settled():
    """002269.SZ 2015-05-12: same total, different split.

    cninfo states 10送6转9 and eastmoney 10送5转10; both scale by 1.5, so the
    market's own factor -- 0.396 here, which both sides agree on -- cannot
    separate them.  This is the channel's principled ceiling, not an
    implementation gap.
    """
    cninfo = terms(
        symbol="002269.SZ",
        cash_per_ten="1.0",
        bonus_per_ten="6.0",
        capitalization_per_ten="9.0",
    )
    eastmoney = terms(
        symbol="002269.SZ",
        cash_per_ten="1.0",
        bonus_per_ten="5.0",
        capitalization_per_ten="10.0",
    )
    observation = PriceObservation(
        prev_close=10.0, prev_close_date=_PREV, pre_close=3.96
    )

    assert expected_factor(cninfo, 10.0) == expected_factor(eastmoney, 10.0)
    assert settle(cninfo, eastmoney, observation) is None


def test_a_loser_inside_the_bar_is_not_settled():
    """The near-miss form: the loser sits 1.4 ticks away, under the bar.

    Both sides lie close to the observation, so neither is distinguishable --
    the machine says so rather than picking the nearer one (spec D5).
    """
    cninfo = terms(cash_per_ten="0.84")  # expected 0.9958
    eastmoney = terms(cash_per_ten="0.70")  # expected 0.9965
    observation = PriceObservation(
        prev_close=20.0, prev_close_date=_PREV, pre_close=19.916
    )

    assert settle(cninfo, eastmoney, observation) is None


def test_a_winner_outside_the_tolerance_is_not_settled():
    """The far form: nobody matches the observation, so nobody is chosen."""
    cninfo = terms(cash_per_ten="0.84")
    eastmoney = terms(cash_per_ten="0.70")
    observation = PriceObservation(
        prev_close=20.0, prev_close_date=_PREV, pre_close=19.0
    )

    assert settle(cninfo, eastmoney, observation) is None


def test_an_absent_channel_settles_nothing():
    """The fail-closed answer, not a default side."""
    arbiter = PriceObservedArbiter(lambda symbol, ex_date: None)

    assert arbiter.arbitrate(terms(), terms(cash_per_ten="5.0")) is None


def test_a_side_with_subscription_terms_is_refused():
    """The calibrated formula models no 配股 term, so it judges none."""
    cninfo = terms(rights_per_ten="3.0", rights_price_per_share="1.0")

    settlement = settle(
        cninfo,
        terms(cash_per_ten="5.0"),
        PriceObservation(prev_close=10.0, prev_close_date=_PREV, pre_close=9.80),
    )

    assert settlement is None


def daily_frame(symbol: str, rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """One tushare ``daily`` response: (trade_date, close, pre_close) rows."""
    return pd.DataFrame(
        [
            {
                "ts_code": symbol,
                "trade_date": day,
                "close": close,
                "pre_close": pre_close,
            }
            for day, close, pre_close in rows
        ]
    )


def store_returning(sha256: str):
    """A stand-in for the content-addressed store's ``record_raw``."""

    def record_raw(result):
        return SimpleNamespace(sha256=sha256)

    return record_raw


def channel_with(rows, *, open_days, failures=None, snapshots=None, recorded=None):
    """A channel over one stubbed response, with no network and no clock."""

    def fetch_daily(symbol, start, end):
        request = DataRequest(DAILY_ENDPOINT, (symbol,), start, end)
        return FetchResult(
            source=TUSHARE_SOURCE,
            endpoint=DAILY_ENDPOINT,
            request_key=request_key(request),
            frame=daily_frame(symbol, rows),
            metadata={"transport_id": TUSHARE_SOURCE},
        )

    target = failures if failures is not None else []
    return LazyDailyPriceChannel(
        fetch_daily,
        open_days,
        on_failure=lambda symbol, error: target.append((symbol, error)),
        raw_snapshots=[] if snapshots is None else snapshots,
        record_raw=store_returning("stub") if recorded is None else recorded,
    )


def test_observes_the_reference_price_when_the_prior_day_is_adjacent():
    """The happy path: the row before the ex-date is the prior open day."""
    channel = channel_with(
        [("2023-07-14", 7.18, 7.20), ("2023-07-17", 4.60, 4.17828)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
    )

    observation = channel.observe("600188.SH", date(2023, 7, 17))

    assert observation is not None
    assert observation.prev_close_date == date(2023, 7, 14)
    assert observation.prev_close == 7.18
    assert observation.pre_close == 4.17828


def test_a_suspension_spanning_the_ex_date_settles_nothing():
    """Spec §6 leaves this form unverified, so it must fail closed.

    The frame's row before the ex-date is 2023-07-05, but the published
    calendar says 2023-07-14 was open: the symbol did not trade normally into
    its ex-date, where ``pre_close``'s meaning is unconfirmed.
    """
    channel = channel_with(
        [("2023-07-05", 7.18, 7.20), ("2023-07-17", 4.60, 4.17828)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
    )

    assert channel.observe("600188.SH", date(2023, 7, 17)) is None


def test_a_channel_failure_settles_nothing_and_is_reported():
    """A raise degrades to an absent channel, never to a guessed side."""
    failures = []

    def fetch_daily(symbol, start, end):
        raise RuntimeError("supplier unreachable")

    channel = LazyDailyPriceChannel(
        fetch_daily,
        (date(2023, 7, 14), date(2023, 7, 17)),
        on_failure=lambda symbol, error: failures.append((symbol, error)),
        raw_snapshots=[],
        record_raw=store_returning("unreached"),
    )

    assert channel.observe("600188.SH", date(2023, 7, 17)) is None
    assert [symbol for symbol, _ in failures] == ["600188.SH"]
    assert isinstance(failures[0][1], RuntimeError)


def test_a_symbol_that_failed_is_not_fetched_again():
    """A dead lane is not retried within the run."""
    attempts = []

    def fetch_daily(symbol, start, end):
        attempts.append(symbol)
        raise RuntimeError("supplier unreachable")

    channel = LazyDailyPriceChannel(
        fetch_daily,
        (date(2023, 7, 14), date(2023, 7, 17)),
        on_failure=lambda symbol, error: None,
        raw_snapshots=[],
        record_raw=store_returning("unreached"),
    )

    channel.observe("600188.SH", date(2023, 7, 17))
    channel.observe("600188.SH", date(2023, 7, 17))

    assert attempts == ["600188.SH"]


def test_the_observation_carries_the_snapshot_it_was_read_from():
    """What settled a conflict must be the bytes the snapshot holds (D3)."""
    snapshots = []
    fetched = []

    def record_raw(result):
        fetched.append(result)
        return SimpleNamespace(sha256="deadbeef")

    channel = channel_with(
        [("2023-07-14", 7.18, 7.20), ("2023-07-17", 4.60, 4.17828)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
        snapshots=snapshots,
        recorded=record_raw,
    )

    observation = channel.observe("600188.SH", date(2023, 7, 17))

    # The hash travels on the observation, and what the store returned is what
    # the run keeps: the snapshot list holds record_raw's own return value.
    assert observation.snapshot_sha256 == "deadbeef"
    assert [snapshot.sha256 for snapshot in snapshots] == ["deadbeef"]

    # One request, for the ex-date's trailing window.
    assert len(fetched) == 1
    assert fetched[0].endpoint == DAILY_ENDPOINT
    assert fetched[0].request_key == request_key(
        DataRequest(
            DAILY_ENDPOINT,
            ("600188.SH",),
            date(2023, 7, 17) - timedelta(days=30),
            date(2023, 7, 17),
        )
    )


def test_a_missing_calendar_answer_settles_nothing():
    """Without a published open day before the ex-date, nothing is claimed."""
    channel = channel_with(
        [("2023-07-14", 7.18, 7.20), ("2023-07-17", 4.60, 4.17828)],
        open_days=(date(2023, 7, 17),),
    )

    assert channel.observe("600188.SH", date(2023, 7, 17)) is None


def test_a_frame_without_the_ex_date_settles_nothing():
    """A supplier that omits the ex-date from its own bars proves nothing."""
    channel = channel_with(
        [("2023-07-13", 7.18, 7.20), ("2023-07-14", 7.19, 7.18)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
    )

    assert channel.observe("600188.SH", date(2023, 7, 17)) is None


def settled_channel():
    """A channel whose frame makes cninfo the corroborated side.

    The previous close is 10.0 and the reference price 9.80, so the observed
    factor is 0.98 and one tick is 0.001: cninfo's 2.0-per-ten dividend implies
    exactly 0.98, eastmoney's 5.0-per-ten implies 0.95.
    """
    return channel_with(
        [("2023-07-14", 10.0, 10.1), ("2023-07-17", 9.80, 9.80)],
        open_days=(date(2023, 7, 14), date(2023, 7, 17)),
        recorded=store_returning("abc123"),
    )


def test_a_settlement_reports_the_numbers_and_the_snapshot():
    """Spec D3: the decision must be recomputable from stored bytes.

    The trace carries the arithmetic and the hash of the raw response the
    reference price was read from.
    """
    traces = []
    channel = settled_channel()
    cninfo = terms(cash_per_ten="2.0")
    eastmoney = terms(cash_per_ten="5.0")

    arbiter = PriceObservedArbiter(
        channel.observe,
        record=lambda cn, em, settlement, observation: traces.append(
            (cn, em, settlement, observation)
        ),
    )
    side = arbiter.arbitrate(cninfo, eastmoney)

    assert side == "cninfo"
    assert len(traces) == 1
    got_cninfo, got_eastmoney, settlement, observation = traces[0]
    assert got_cninfo == cninfo
    assert got_eastmoney == eastmoney
    assert observation.snapshot_sha256 == "abc123"
    details = settlement.to_details()
    assert details["side"] == "cninfo"
    assert details["prev_close"] == 10.0
    assert details["pre_close"] == 9.80
    assert details["tick"] == pytest.approx(0.001)
    assert details["cninfo_ticks"] == pytest.approx(0.0)
    assert details["eastmoney_ticks"] == pytest.approx(30.0)


def test_a_conflict_that_stays_quarantined_reports_nothing():
    """No settlement means no trace; the absence is the evidence.

    Both sides sit near the observation -- 0 and 2.0 ticks -- so neither is
    distinguishable and the arbiter declines.
    """
    traces = []
    channel = settled_channel()
    arbiter = PriceObservedArbiter(
        channel.observe, record=lambda *args: traces.append(args)
    )

    assert (
        arbiter.arbitrate(terms(cash_per_ten="2.0"), terms(cash_per_ten="2.02"))
        is None
    )
    assert traces == []


def test_an_arbiter_without_a_record_callback_still_settles():
    """The rule must not depend on the trace being wired."""
    channel = settled_channel()
    arbiter = PriceObservedArbiter(channel.observe)

    assert (
        arbiter.arbitrate(terms(cash_per_ten="2.0"), terms(cash_per_ten="5.0"))
        == "cninfo"
    )


class _Fixed:
    """An arbiter that always answers the same way, and says who was asked."""

    def __init__(self, name, side, calls):
        self.name = name
        self._side = side
        self._calls = calls

    def arbitrate(self, cninfo, eastmoney):
        self._calls.append(self.name)
        return self._side


def test_the_chain_asks_each_lane_until_one_answers():
    """TDX answers where it can; the price lane where TDX refuses."""
    calls = []
    chain = FirstAnsweringArbiter(
        [_Fixed("tdx", None, calls), _Fixed("price_observed", "cninfo", calls)],
        issues=[],
    )

    assert chain.arbitrate(terms(), terms(cash_per_ten="5.0")) == "cninfo"
    assert calls == ["tdx", "price_observed"]


def test_the_chain_names_who_decided_not_who_was_asked_first():
    """``confirmed_by`` reads ``<side>+<authority>``, so the label must follow
    the lane that answered."""
    calls = []
    chain = FirstAnsweringArbiter(
        [
            _Fixed("tdx", "eastmoney", calls),
            _Fixed("price_observed", "cninfo", calls),
        ],
        issues=[],
    )

    assert chain.arbitrate(terms(), terms(cash_per_ten="5.0")) == "eastmoney"
    assert calls == ["tdx"]
    assert chain.name == "tdx"


def test_the_guard_carries_the_answering_lane_name_through():
    """The wrapper must read its inner arbiter's name after the call, not copy
    it at construction."""
    calls = []
    guarded = _GuardedArbiter(
        FirstAnsweringArbiter(
            [
                _Fixed("tdx", None, calls),
                _Fixed("price_observed", "cninfo", calls),
            ],
            issues=[],
        ),
        issues=[],
    )

    assert guarded.arbitrate(terms(), terms(cash_per_ten="5.0")) == "cninfo"
    assert guarded.name == "price_observed"


def test_a_reviewed_key_is_still_never_arbitrated_by_the_new_lane():
    """ADR-007's rail applies to the price lane for free, and must."""
    calls = []
    guarded = _GuardedArbiter(
        FirstAnsweringArbiter(
            [
                _Fixed("tdx", None, calls),
                _Fixed("price_observed", "cninfo", calls),
            ],
            issues=[],
        ),
        issues=[],
        reviewed=frozenset({("600188.SH", _DAY)}),
    )

    assert guarded.arbitrate(terms(), terms(cash_per_ten="5.0")) is None
    assert calls == []


def test_a_raising_lane_degrades_to_the_next_one_and_is_reported():
    """A dead lane asserts nothing, so the lane behind it may still answer."""
    calls = []
    issues = []

    class _Raising:
        name = "tdx"

        def arbitrate(self, cninfo, eastmoney):
            calls.append("tdx")
            raise RuntimeError("channel down")

    chain = FirstAnsweringArbiter(
        [_Raising(), _Fixed("price_observed", "cninfo", calls)], issues=issues
    )

    assert chain.arbitrate(terms(), terms(cash_per_ten="5.0")) == "cninfo"
    assert calls == ["tdx", "price_observed"]
    assert [issue.code for issue in issues] == [CODE_OPTIONAL_SOURCE_FAILURE]


def test_a_raising_lane_still_leaves_the_key_quarantined_when_no_lane_answers():
    """With every lane down, the key keeps its conflict -- fail closed."""
    issues = []

    class _Raising:
        def __init__(self, name):
            self.name = name

        def arbitrate(self, cninfo, eastmoney):
            raise RuntimeError("channel down")

    chain = FirstAnsweringArbiter(
        [_Raising("tdx"), _Raising("price_observed")], issues=issues
    )

    assert chain.arbitrate(terms(), terms(cash_per_ten="5.0")) is None
    assert [issue.code for issue in issues] == [
        CODE_OPTIONAL_SOURCE_FAILURE,
        CODE_OPTIONAL_SOURCE_FAILURE,
    ]


def test_a_settlement_puts_its_numbers_in_a_quality_issue():
    """Spec D3: what settled the conflict must be readable from the report."""
    cninfo = terms(cash_per_ten="2.0")
    observation = PriceObservation(
        prev_close=10.0,
        prev_close_date=_PREV,
        pre_close=9.80,
        snapshot_sha256="abc123",
    )
    settlement = settle(cninfo, terms(cash_per_ten="5.0"), observation)

    issue = _price_settlement_issue(cninfo, settlement, observation)

    assert issue.code == CODE_PRICE_OBSERVED_SETTLEMENT
    assert issue.symbol == "600188.SH"
    assert issue.trade_date == _DAY
    assert issue.details["prev_close"] == 10.0
    assert issue.details["pre_close"] == 9.80
    assert issue.details["snapshot_sha256"] == "abc123"
    assert issue.details["cninfo_ticks"] == pytest.approx(0.0)
