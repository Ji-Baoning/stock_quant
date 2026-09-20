"""TDX conflict-arbiter tests (ADR-007).

The arbiter is a third opinion on a cross-source disagreement, never a source:
it names the side to book and the winning row still comes from that official
filing.  These tests never call a network; every frame is authored inline, and
the TDX values are computed with the transport's own arithmetic
(``struct.pack("<f")`` then ``/ 10.0``) rather than restated from the
implementation.
"""

import struct
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from stock_quant.data_model.corporate_actions import (
    REASON_CROSS_SOURCE_CONFLICT,
    normalize_corporate_actions,
)
from stock_quant.data_pipeline import _GuardedArbiter
from stock_quant.data_sources.tdx import (
    XDXR_COLUMNS,
    TdxUnavailableError,
    TdxXdxrArbiter,
    fetch_xdxr_frames,
)

_CNINFO_COLUMNS = [
    "证券代码",
    "证券简称",
    "公告日期",
    "股权登记日",
    "除权除息日",
    "派息(税前)(元/10股)",
    "送股(股/10股)",
    "转增(股/10股)",
    "配股(股/10股)",
    "配股价格(元/股)",
    "进度",
    "方案",
]
_EASTMONEY_COLUMNS = [
    "代码",
    "名称",
    "最新公告日期",
    "股权登记日",
    "除权除息日",
    "现金分红-现金分红比例",
    "送转股份-送股比例",
    "送转股份-转股比例",
    "配股(股/10股)",
    "配股价格(元/股)",
    "方案进度",
    "方案",
]

_BARE = "600519"
_SYMBOL = "600519.SH"
_ANNOUNCEMENT = "2020-05-20"
_RECORD = "2020-06-10"
_EX = "2020-06-11"


def _cninfo(
    *,
    cash_per_10: float = 0.0,
    bonus_per_10: float = 0.0,
    cap_per_10: float = 0.0,
    rights_per_10: float = 0.0,
    rights_price: float = 0.0,
    ex_date: str = _EX,
    record_date: str = _RECORD,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "证券代码": _BARE,
                "证券简称": "placeholder",
                "公告日期": _ANNOUNCEMENT,
                "股权登记日": record_date,
                "除权除息日": ex_date,
                "派息(税前)(元/10股)": cash_per_10,
                "送股(股/10股)": bonus_per_10,
                "转增(股/10股)": cap_per_10,
                "配股(股/10股)": rights_per_10,
                "配股价格(元/股)": rights_price,
                "进度": "实施",
                "方案": "10派1元(含税)",
            }
        ],
        columns=_CNINFO_COLUMNS,
    )


def _eastmoney(
    *,
    cash_per_10: float = 0.0,
    bonus_per_10: float = 0.0,
    cap_per_10: float = 0.0,
    rights_per_10: float = 0.0,
    rights_price: float = 0.0,
    ex_date: str = _EX,
    record_date: str = _RECORD,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": _BARE,
                "名称": "placeholder",
                "最新公告日期": _ANNOUNCEMENT,
                "股权登记日": record_date,
                "除权除息日": ex_date,
                "现金分红-现金分红比例": cash_per_10,
                "送转股份-送股比例": bonus_per_10,
                "送转股份-转股比例": cap_per_10,
                "配股(股/10股)": rights_per_10,
                "配股价格(元/股)": rights_price,
                "方案进度": "实施",
                "方案": "10派1元(含税)",
            }
        ],
        columns=_EASTMONEY_COLUMNS,
    )


def _tdx_sends(per_ten: float) -> float:
    """The double TDX's decode yields for a per-ten-share amount.

    TDX unpacks a float32 and divides by ten in double precision, which is the
    only thing a side may be compared against.
    """
    return struct.unpack("<f", struct.pack("<f", float(per_ten)))[0] / 10.0


def _xdxr(
    *,
    fenhong: float = 0.0,
    songzhuangu: float = 0.0,
    peigu: float = 0.0,
    peigujia: float = 0.0,
    ex_date: str = _EX,
    symbol: str = _SYMBOL,
    category: int = 1,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "date": ex_date,
                "category": category,
                "name": "除权除息",
                "fenhong": fenhong,
                "songzhuangu": songzhuangu,
                "peigu": peigu,
                "peigujia": peigujia,
            }
        ],
        columns=list(XDXR_COLUMNS),
    )


def _arbiter(*frames: pd.DataFrame) -> TdxXdxrArbiter:
    return TdxXdxrArbiter.from_frames(
        {frame["symbol"].iloc[0]: frame for frame in frames}
    )


# --------------------------------------------------------------------------- #
# The comparison itself
# --------------------------------------------------------------------------- #


def test_the_quote_is_quantised_per_ten_shares_not_per_share():
    """A side matches TDX's float32 of the *per-ten* value, not of the ratio.

    ``14.0 / 10.0`` is exactly ``1.4``, so the per-ten reading matches CNINFO.
    Quantising the per-share ratio instead gives ``1.399999976158142``, which
    matches neither side and would arbitrate nothing -- so an arbiter that
    books CNINFO here is proving which arithmetic it used.
    """
    result = normalize_corporate_actions(
        _cninfo(cash_per_10=14.0),
        _eastmoney(cash_per_10=15.0),
        arbiter=_arbiter(_xdxr(fenhong=_tdx_sends(14.0))),
    )

    assert result.quarantined.empty
    assert result.accepted.iloc[0]["confirmed_by"] == "cninfo+tdx"
    assert float(result.accepted.iloc[0]["cash_dividend_per_share"]) == 1.4


def test_arbiter_books_the_side_tdx_corroborates():
    """A seventh-digit disagreement is separable, with no tolerance involved.

    The measured values for ``300124.SZ`` on 2016-05-18 (published quarantine
    rows and a live TDX read, both 2026-09-16).  The sides agree on cash and
    differ in 转增 -- 9.998780 against 9.998781 per ten shares -- and TDX's
    ``songzhuangu`` is the float32 of CNINFO's figure.
    """
    assert _tdx_sends(9.998780) == 0.9998780250549316
    assert _tdx_sends(9.998781) == 0.9998781204223632
    assert _tdx_sends(4.99939) == 0.4999390125274658

    result = normalize_corporate_actions(
        _cninfo(
            cash_per_10=4.99939,
            cap_per_10=9.998780,
            ex_date="2016-05-18",
            record_date="2016-05-17",
        ),
        _eastmoney(
            cash_per_10=4.99939,
            cap_per_10=9.998781,
            ex_date="2016-05-18",
            record_date="2016-05-17",
        ),
        arbiter=_arbiter(
            _xdxr(
                fenhong=0.4999390125274658,
                songzhuangu=0.9998780250549316,
                ex_date="2016-05-18",
            )
        ),
    )

    assert result.quarantined.empty
    assert result.accepted.iloc[0]["confirmed_by"] == "cninfo+tdx"


def test_arbiter_can_name_eastmoney():
    """TDX is not an echo of CNINFO: it also decides against it.

    The measured values for ``301308.SZ`` on 2026-06-02.  Here the sides agree
    on 送转 and differ in cash -- 9.90744266 against 9.907442 per ten shares --
    and TDX's ``fenhong`` is the float32 of **Eastmoney's** figure.

    Note the two events arbitrate on different fields: the class is separable in
    both directions, so the rule cannot be reduced to "prefer CNINFO".
    """
    assert _tdx_sends(9.90744266) == 0.9907443046569824
    assert _tdx_sends(9.907442) == 0.9907442092895508

    result = normalize_corporate_actions(
        _cninfo(cash_per_10=9.90744266, ex_date="2026-06-02", record_date="2026-06-01"),
        _eastmoney(
            cash_per_10=9.907442, ex_date="2026-06-02", record_date="2026-06-01"
        ),
        arbiter=_arbiter(_xdxr(fenhong=0.9907442092895508, ex_date="2026-06-02")),
    )

    assert result.quarantined.empty
    assert result.accepted.iloc[0]["confirmed_by"] == "eastmoney+tdx"


def test_the_merged_songzhuan_total_is_what_tdx_corroborates():
    """送股 and 转增 share one TDX field, so they are compared as their sum.

    This is the shape of the class where one supplier omits part of a same-day
    event: CNINFO's 送6转9 totals 15 per ten shares and TDX states that total,
    while Eastmoney's 送5 does not.
    """
    result = normalize_corporate_actions(
        _cninfo(bonus_per_10=6.0, cap_per_10=9.0),
        _eastmoney(bonus_per_10=5.0),
        arbiter=_arbiter(_xdxr(songzhuangu=_tdx_sends(15.0))),
    )

    assert result.quarantined.empty
    assert result.accepted.iloc[0]["confirmed_by"] == "cninfo+tdx"
    assert float(result.accepted.iloc[0]["bonus_share_ratio"]) == 0.6


def test_subscription_terms_are_part_of_the_comparison():
    """A corroboration must hold on every field TDX carries, not just cash."""
    result = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0, rights_per_10=3.0, rights_price=5.0),
        _eastmoney(cash_per_10=1.0, rights_per_10=4.0, rights_price=5.0),
        arbiter=_arbiter(
            _xdxr(fenhong=_tdx_sends(1.0), peigu=_tdx_sends(4.0), peigujia=5.0)
        ),
    )

    assert result.quarantined.empty
    assert result.accepted.iloc[0]["confirmed_by"] == "eastmoney+tdx"


def test_a_date_cell_from_a_reloaded_snapshot_still_arbitrates():
    """A snapshot's date cells come back as timestamps, not as strings.

    A key of one type would miss a lookup of the other, and the arbiter would
    silently decide nothing exactly where it should have decided.
    """
    frame = _xdxr(fenhong=_tdx_sends(1.0))
    frame["date"] = pd.to_datetime(frame["date"])

    result = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0),
        _eastmoney(cash_per_10=2.0),
        arbiter=_arbiter(frame),
    )

    assert result.quarantined.empty
    assert result.accepted.iloc[0]["confirmed_by"] == "cninfo+tdx"


# --------------------------------------------------------------------------- #
# Fail-closed: every case where TDX must arbitrate nothing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(
            _xdxr(fenhong=_tdx_sends(1.0), ex_date="2020-06-12"), id="other ex-date"
        ),
        pytest.param(
            _xdxr(fenhong=_tdx_sends(1.0), symbol="000001.SZ"), id="other symbol"
        ),
        pytest.param(_xdxr(fenhong=_tdx_sends(3.0)), id="matches neither side"),
        pytest.param(
            _xdxr(fenhong=_tdx_sends(1.0), category=2), id="not a distribution"
        ),
    ],
)
def test_no_arbitration_leaves_both_sides_quarantined(frame):
    result = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0),
        _eastmoney(cash_per_10=2.0),
        arbiter=_arbiter(frame),
    )

    assert result.accepted.empty
    assert len(result.quarantined) == 2
    assert set(result.quarantined["reason"]) == {REASON_CROSS_SOURCE_CONFLICT}


def test_no_arbitration_when_the_total_matches_both_sides():
    """An identical total split differently is confirmed by neither a nor b.

    TDX states 送转 as one number, so it agrees with both sides of such a
    conflict and therefore separates neither.
    """
    result = normalize_corporate_actions(
        _cninfo(bonus_per_10=6.0, cap_per_10=9.0),
        _eastmoney(bonus_per_10=5.0, cap_per_10=10.0),
        arbiter=_arbiter(_xdxr(songzhuangu=_tdx_sends(15.0))),
    )

    assert result.accepted.empty
    assert len(result.quarantined) == 2


def test_a_repeated_ex_date_is_not_arbitrated():
    """Two TDX rows for one ex-date state nothing to decide from."""
    first = _xdxr(fenhong=_tdx_sends(1.0))
    second = _xdxr(fenhong=_tdx_sends(1.0))
    arbiter = _arbiter(pd.concat([first, second], ignore_index=True))

    result = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0),
        _eastmoney(cash_per_10=2.0),
        arbiter=arbiter,
    )

    assert result.accepted.empty
    assert len(result.quarantined) == 2


def test_without_an_arbiter_a_conflict_is_quarantined():
    """The default -- what ``tdx.enabled: false`` produces -- is unchanged."""
    result = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0), _eastmoney(cash_per_10=2.0)
    )

    assert result.accepted.empty
    assert len(result.quarantined) == 2


def test_a_raising_arbiter_quarantines_rather_than_losing_the_lane():
    """An optional third opinion must not be able to discard a symbol's facts.

    ``_reconcile_action_frames`` turns any reconciliation exception into an
    empty dividend lane, so a broken arbiter would otherwise lose the symbol's
    conflicts *and* its agreed facts.  It degrades to "no arbitration" instead,
    and the reason is recorded.
    """

    class _Exploding:
        name = "tdx"

        def arbitrate(self, cninfo, eastmoney):
            raise RuntimeError("arbiter failed")

    issues: list = []
    arbiter = _GuardedArbiter(_Exploding(), issues)

    result = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0), _eastmoney(cash_per_10=2.0), arbiter=arbiter
    )

    assert result.accepted.empty
    assert len(result.quarantined) == 2
    assert [issue.details["symbol"] for issue in issues] == ["600519.SH"]
    assert "arbiter failed" in issues[0].details["message"]


def test_an_owner_review_outranks_the_arbiter():
    """A reviewed conflict must reach the review instead of being booked.

    ``apply_corporate_action_reviews`` raises when a reviewed key has no
    quarantined row left to match, and that raise costs the symbol its whole
    dividend lane -- so arbitrating a conflict the owner had already signed off
    on would destroy the facts it was meant to complement.
    """
    issues: list = []
    inner = _arbiter(_xdxr(fenhong=_tdx_sends(1.0)))
    arbiter = _GuardedArbiter(inner, issues, {("600519.SH", date(2020, 6, 11))})

    result = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0), _eastmoney(cash_per_10=2.0), arbiter=arbiter
    )

    assert result.accepted.empty
    assert len(result.quarantined) == 2
    assert issues == []


# --------------------------------------------------------------------------- #
# The channel is optional, and off by default
# --------------------------------------------------------------------------- #


def test_a_missing_package_is_an_unavailable_arbiter(monkeypatch):
    """Absent pytdxdata must not be an import error at startup."""
    monkeypatch.setitem(sys.modules, "pytdxdata", None)

    with pytest.raises(TdxUnavailableError):
        fetch_xdxr_frames(["600519.SH"])


def test_the_shipped_config_ships_the_arbiter_on():
    """Owner decision 2026-09-20: the arbiter ships enabled.

    It stays a third opinion, never a source: the channel is consulted
    lazily, only where the two official sources disagree, and an absent or
    unreachable channel degrades to the quarantined state (see the lazy
    wrapper tests).
    """
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "project/configs/sources.yml",
        "templates/project-config/sources.yml",
    ):
        sources = yaml.safe_load((root / relative).read_text())
        assert sources["tdx"]["enabled"] is True, relative


# --------------------------------------------------------------------------- #
# The lazy wrapper: the channel is touched only on an actual disagreement
# --------------------------------------------------------------------------- #


class _StubConfig:
    timeout_seconds = 30


def _lazy_arbiter(monkeypatch, outcome, calls, raw_snapshots, issues=None):
    """A ``_LazyActionArbiter`` over a scripted ``fetch_xdxr_frames``."""
    from stock_quant import data_pipeline

    def fake_fetch(symbols, *, timeout=30.0, servers=None):
        calls.extend(symbols)
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    monkeypatch.setattr(data_pipeline, "fetch_xdxr_frames", fake_fetch)
    return data_pipeline._LazyActionArbiter(
        _StubConfig(),
        date(2020, 1, 1),
        date(2020, 12, 31),
        issues=issues if issues is not None else [],
        raw_snapshots=raw_snapshots,
        record_raw=lambda result: result,
    )


def test_a_clean_round_never_touches_the_channel(monkeypatch):
    """Agreeing sources mean zero TDX calls, whatever the shipped default."""
    calls: list[str] = []
    lazy = _lazy_arbiter(monkeypatch, {"600519.SH": _xdxr()}, calls, [])

    result = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0),
        _eastmoney(cash_per_10=1.0),
        arbiter=_GuardedArbiter(lazy, []),
    )

    assert calls == []
    assert not result.accepted.empty


def test_a_conflict_fetches_its_symbol_once_and_records_the_frame(monkeypatch):
    """One disputed symbol costs exactly one fetch, cached for later keys."""
    calls: list[str] = []
    raw: list = []
    lazy = _lazy_arbiter(
        monkeypatch,
        {"600519.SH": _xdxr(fenhong=_tdx_sends(14.0))},
        calls,
        raw,
    )
    guarded = _GuardedArbiter(lazy, [])

    first = normalize_corporate_actions(
        _cninfo(cash_per_10=14.0),
        _eastmoney(cash_per_10=15.0),
        arbiter=guarded,
    )
    second = normalize_corporate_actions(
        _cninfo(cash_per_10=14.0),
        _eastmoney(cash_per_10=15.0),
        arbiter=guarded,
    )

    assert calls == ["600519.SH"]
    assert len(raw) == 1
    assert first.accepted.iloc[0]["confirmed_by"] == "cninfo+tdx"
    assert second.accepted.iloc[0]["confirmed_by"] == "cninfo+tdx"


def test_an_unreachable_channel_degrades_once_per_symbol(monkeypatch):
    """The fail-closed direction: unreachable TDX leaves conflicts quarantined."""
    calls: list[str] = []
    issues: list = []
    lazy = _lazy_arbiter(
        monkeypatch,
        TdxUnavailableError("channel down"),
        calls,
        [],
        issues=issues,
    )
    guarded = _GuardedArbiter(lazy, issues)

    first = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0),
        _eastmoney(cash_per_10=2.0),
        arbiter=guarded,
    )
    second = normalize_corporate_actions(
        _cninfo(cash_per_10=1.0),
        _eastmoney(cash_per_10=2.0),
        arbiter=guarded,
    )

    assert calls == ["600519.SH"]
    assert first.accepted.empty and len(first.quarantined) == 2
    assert second.accepted.empty and len(second.quarantined) == 2
    assert len(issues) == 1
    assert issues[0].details["symbol"] == "600519.SH"


def test_classification_records_for_newly_quarantined_absent_ex_dates(monkeypatch):
    """ADR-009 decision 1: new incomplete rows carry their classification.

    Only this round's newly quarantined rows are classified -- rows with a
    stated ex-date, rows outside the round's window, and carried history are
    not re-classified -- and the TDX channel answers from the same lazily
    fetched frame arbitration would use.
    """
    from stock_quant import data_pipeline
    from stock_quant.data_quality.models import CODE_ABSENT_EX_DATE_CLASSIFIED

    probe = date(2026, 7, 1)
    calls: list[str] = []
    lazy = _lazy_arbiter(
        monkeypatch,
        {"600519.SH": _xdxr(fenhong=_tdx_sends(14.0), ex_date=probe.isoformat())},
        calls,
        [],
    )
    issues: list = []
    quarantine = pd.DataFrame(
        [
            {  # classified: absent ex-date, announced in-window
                "symbol": "600519.SH",
                "announcement_date": date(2026, 7, 1),
                "ex_date": None,
                "reason": "incomplete",
            },
            {  # stated ex-date: nothing for ADR-009's absent row to classify
                "symbol": "000001.SZ",
                "announcement_date": date(2026, 7, 2),
                "ex_date": date(2026, 7, 20),
                "reason": "incomplete",
            },
            {  # announced outside the round's window: carried history
                "symbol": "000002.SZ",
                "announcement_date": date(1996, 5, 16),
                "ex_date": None,
                "reason": "incomplete",
            },
        ]
    )

    data_pipeline._record_absent_ex_date_classifications(
        quarantine,
        _GuardedArbiter(lazy, issues),
        date(2026, 6, 20),
        date(2026, 9, 18),
        issues,
    )

    assert calls == ["600519.SH"]
    assert [issue.code for issue in issues] == [CODE_ABSENT_EX_DATE_CLASSIFIED]
    assert issues[0].details["classification"] == "absent+adjustment_observed"
    assert issues[0].details["probe_date"] == "2026-07-01"


def test_classification_with_an_unavailable_channel_reads_unknown(monkeypatch):
    """A dead channel asserts nothing: the row still records, as unknown."""
    from stock_quant import data_pipeline

    calls: list[str] = []
    issues: list = []
    lazy = _lazy_arbiter(
        monkeypatch,
        TdxUnavailableError("channel down"),
        calls,
        [],
        issues=issues,
    )
    quarantine = pd.DataFrame(
        [
            {
                "symbol": "600519.SH",
                "announcement_date": date(2026, 7, 1),
                "ex_date": None,
                "reason": "incomplete",
            }
        ]
    )

    data_pipeline._record_absent_ex_date_classifications(
        quarantine,
        _GuardedArbiter(lazy, issues),
        date(2026, 6, 20),
        date(2026, 9, 18),
        issues,
    )

    classified = [
        issue
        for issue in issues
        if issue.code == "absent_ex_date_classified"
    ]
    assert len(classified) == 1
    assert classified[0].details["classification"] == "absent+unknown"


def test_classification_without_an_arbiter_records_nothing():
    """No arbiter, no channel, no record: the report stays as it was."""
    from stock_quant import data_pipeline

    issues: list = []
    quarantine = pd.DataFrame(
        [
            {
                "symbol": "600519.SH",
                "announcement_date": date(2026, 7, 1),
                "ex_date": None,
                "reason": "incomplete",
            }
        ]
    )

    data_pipeline._record_absent_ex_date_classifications(
        quarantine, None, date(2026, 6, 20), date(2026, 9, 18), issues
    )

    assert issues == []
