"""Corporate-action cross-source reconciliation tests (Task 5).

``normalize_corporate_actions`` consumes supplier-native corporate-action
frames in the documented AKShare layouts (CNINFO primary, Eastmoney
cross-check) and reconciles them into implemented, supported events. These
tests never call a network or supplier; every frame is authored inline.
"""

import datetime

import pandas as pd
import pytest

from stock_quant.data_model.corporate_actions import (
    EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED,
    EXCLUSION_EX_DATE_OUT_OF_WINDOW,
    EXCLUSION_RECORD_DATE_OUT_OF_WINDOW,
    REASON_CROSS_SOURCE_CONFLICT,
    REASON_INCOMPLETE,
    REASON_NON_DISTRIBUTIVE_RESTRUCTURING,
    REASON_NOT_IMPLEMENTED,
    REASON_UNSUPPORTED_CORPORATE_ACTION,
    apply_corporate_action_reviews,
    filter_corporate_actions_to_window,
    normalize_corporate_actions,
    normalize_rights_issue_actions,
    prepare_allotment_rights_frame,
    prepare_cninfo_dividend_frame,
    prepare_eastmoney_dividend_frame,
    quarantine_row_out_of_window_reason,
)

# Documented supplier-native column names (CNINFO primary, Eastmoney cross).
_CNINFO_COLUMNS = [
    "证券代码",
    "证券简称",
    "公告日期",
    "股权登记日",
    "除权除息日",
    "派息(税前)(元/10股)",
    "送股(股/10股)",
    "转增(股/10股)",
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
    "方案进度",
    "方案",
]
# Eastmoney may report 方案进度 without a standalone 方案 / 方案说明 column; the
# optional ``plan`` field must then fall back to its documented default.
_EASTMONEY_COLUMNS_WITHOUT_PLAN = [c for c in _EASTMONEY_COLUMNS if c != "方案"]


def test_prepare_cninfo_dividend_frame_maps_akshare_11823_schema():
    """AKShare 1.18.23's dividend endpoint must feed the canonical parser."""
    raw = pd.DataFrame(
        [
            {
                "实施方案公告日期": "2024-05-20",
                "分红类型": "现金分红",
                "送股比例": 2.0,
                "转增比例": 3.0,
                "派息比例": 1.0,
                "股权登记日": "2024-06-10",
                "除权日": "2024-06-11",
                "派息日": "2024-06-11",
                "股份到账日": "2024-06-11",
                "实施方案分红说明": "10送2转3派1元(含税)",
                "报告时间": "2023-12-31",
            }
        ]
    )

    prepared = prepare_cninfo_dividend_frame(raw, "000333.SZ")
    result = normalize_corporate_actions(prepared, None)

    row = result.accepted.iloc[0]
    assert row["symbol"] == "000333.SZ"
    assert row["announcement_date"] == pd.Timestamp("2024-05-20").date()
    assert row["record_date"] == pd.Timestamp("2024-06-10").date()
    assert row["ex_date"] == pd.Timestamp("2024-06-11").date()
    assert row["cash_dividend_per_share"] == 0.1
    assert row["bonus_share_ratio"] == 0.2
    assert row["capitalization_ratio"] == 0.3
    assert row["status"] == "implemented"
    assert row["confirmed_by"] == "cninfo"


def test_filter_corporate_actions_to_window_excludes_future_events():
    """Future plans must not make a historical backtest window untrusted."""
    frame = pd.DataFrame(
        {
            "除权除息日": ["2026-08-28", "2026-08-29", None, None],
            "方案进度": ["实施", "实施", "预案", "实施"],
            "派息(税前)(元/10股)": [1.0, 2.0, 3.0, 4.0],
        }
    )

    filtered = filter_corporate_actions_to_window(
        frame, pd.Timestamp("2021-01-08"), pd.Timestamp("2026-08-28")
    )

    assert filtered["派息(税前)(元/10股)"].tolist() == [1.0, 4.0]


def test_prepare_eastmoney_dividend_frame_maps_ths_fallback():
    frame = pd.DataFrame(
        {
            "实施公告日": ["2024-05-20"],
            "分红方案说明": ["10送2转3派1元(含税)"],
            "A股股权登记日": ["2024-06-10"],
            "A股除权除息日": ["2024-06-11"],
            "方案进度": ["实施分配"],
        }
    )

    prepared = prepare_eastmoney_dividend_frame(frame, "000333.SZ")
    result = normalize_corporate_actions(None, prepared)

    row = result.accepted.iloc[0]
    assert row["symbol"] == "000333.SZ"
    assert row["cash_dividend_per_share"] == pytest.approx(0.1)
    assert row["bonus_share_ratio"] == pytest.approx(0.2)
    assert row["capitalization_ratio"] == pytest.approx(0.3)


def test_prepare_cninfo_dividend_frame_preserves_legacy_cninfo_schema():
    """Older recorded CNINFO frames remain valid after adding 1.18.23 support."""
    legacy = cninfo_cash(0.1, symbol="600036")

    prepared = prepare_cninfo_dividend_frame(legacy, "600036.SH")

    assert prepared.equals(legacy)


def test_normalize_combines_same_day_implemented_cninfo_distributions():
    """Annual and special dividends sharing an ex date book as one event."""
    raw = pd.DataFrame(
        [
            {
                "实施方案公告日期": "2024-04-23",
                "分红类型": "年度分红",
                "送股比例": None,
                "转增比例": None,
                "派息比例": 20.11,
                "股权登记日": "2024-04-29",
                "除权日": "2024-04-30",
                "派息日": "2024-04-30",
                "股份到账日": None,
                "实施方案分红说明": "10派20.11元(含税)",
                "报告时间": "2023年报",
            },
            {
                "实施方案公告日期": "2024-04-23",
                "分红类型": "特别分红",
                "送股比例": None,
                "转增比例": None,
                "派息比例": 30.17,
                "股权登记日": "2024-04-29",
                "除权日": "2024-04-30",
                "派息日": "2024-04-30",
                "股份到账日": None,
                "实施方案分红说明": "10派30.17元(含税)",
                "报告时间": "2023年报",
            },
        ]
    )

    prepared = prepare_cninfo_dividend_frame(raw, "300750.SZ")
    result = normalize_corporate_actions(prepared, None)

    assert len(result.accepted) == 1
    assert result.accepted.iloc[0]["symbol"] == "300750.SZ"
    assert result.accepted.iloc[0]["ex_date"] == pd.Timestamp("2024-04-30").date()
    assert result.accepted.iloc[0]["cash_dividend_per_share"] == pytest.approx(5.028)


def _per10(per_share: float) -> float:
    """Convert a per-share fraction to the native per-10-share value exactly."""
    return float(per_share) * 10


def allotment(
    *,
    symbol: str = "002202",
    announcement: str = "2019-03-18",
    record: str = "2019-03-20",
    ex: str | None = "2019-03-29",
    ratio_per_10: float = 1.9,
    price: float | None = 7.02,
) -> pd.DataFrame:
    """One CNINFO allotment row in the ``stock_allotment_cninfo`` layout.

    ``ratio_per_10`` is the native per-ten-share _subscription_ ratio while
    ``price`` is the subscription price per _share_ -- the unit asymmetry the
    adapter exists to keep straight.  ``ex`` of ``None`` models an announced but
    unsettled plan.
    """
    return pd.DataFrame(
        [
            {
                "证券代码": symbol,
                "证券简称": "placeholder",
                "公告日期": announcement,
                "股权登记日": record,
                "除权基准日": ex,
                "配股比例": ratio_per_10,
                "配股价格": price,
                "停牌起始日": "2019-03-21",
                "停牌截止日": "2019-03-28",
            }
        ]
    )


def cninfo_cash(
    per_share: float,
    *,
    symbol: str = "600519",
    announcement: str = "2020-05-20",
    record: str = "2020-06-10",
    ex: str = "2020-06-11",
    progress: str = "实施",
    plan: str = "10派1元(含税)",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "证券代码": symbol,
                "证券简称": "placeholder",
                "公告日期": announcement,
                "股权登记日": record,
                "除权除息日": ex,
                "派息(税前)(元/10股)": _per10(per_share),
                "送股(股/10股)": 0.0,
                "转增(股/10股)": 0.0,
                "进度": progress,
                "方案": plan,
            }
        ],
        columns=_CNINFO_COLUMNS,
    )


def eastmoney_cash(
    per_share: float,
    *,
    symbol: str = "600519",
    announcement: str = "2020-05-20",
    record: str = "2020-06-10",
    ex: str = "2020-06-11",
    progress: str = "实施",
    plan: str = "10派1元(含税)",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": symbol,
                "名称": "placeholder",
                "最新公告日期": announcement,
                "股权登记日": record,
                "除权除息日": ex,
                "现金分红-现金分红比例": _per10(per_share),
                "送转股份-送股比例": 0.0,
                "送转股份-转股比例": 0.0,
                "方案进度": progress,
                "方案": plan,
            }
        ],
        columns=_EASTMONEY_COLUMNS,
    )


def eastmoney_without_plan_column(
    per_share: float,
    *,
    symbol: str = "600519",
    progress: str = "实施",
    bonus_per_10: float = 0.0,
    cap_per_10: float = 0.0,
) -> pd.DataFrame:
    """An Eastmoney frame with ``方案进度`` but no ``方案`` / ``方案说明`` column."""
    return pd.DataFrame(
        [
            {
                "代码": symbol,
                "名称": "placeholder",
                "最新公告日期": "2020-05-20",
                "股权登记日": "2020-06-10",
                "除权除息日": "2020-06-11",
                "现金分红-现金分红比例": _per10(per_share),
                "送转股份-送股比例": bonus_per_10,
                "送转股份-转股比例": cap_per_10,
                "方案进度": progress,
            }
        ],
        columns=_EASTMONEY_COLUMNS_WITHOUT_PLAN,
    )


def _cninfo_plan(
    *,
    cash_per_10: float = 0.0,
    bonus_per_10: float = 0.0,
    cap_per_10: float = 0.0,
    progress: str = "实施",
    plan: str = "10派1元(含税)",
    record: str | None = "2020-06-10",
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "证券代码": "600519",
                "证券简称": "placeholder",
                "公告日期": "2020-05-20",
                "股权登记日": record,
                "除权除息日": "2020-06-11",
                "派息(税前)(元/10股)": cash_per_10,
                "送股(股/10股)": bonus_per_10,
                "转增(股/10股)": cap_per_10,
                "进度": progress,
                "方案": plan,
            }
        ],
        columns=_CNINFO_COLUMNS,
    )


def test_conflicting_implemented_actions_are_quarantined():
    result = normalize_corporate_actions(cninfo_cash(0.1), eastmoney_cash(0.2))
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_CROSS_SOURCE_CONFLICT


def test_conflicting_actions_preserve_both_source_records():
    result = normalize_corporate_actions(cninfo_cash(0.1), eastmoney_cash(0.2))
    assert len(result.quarantined) == 2
    assert set(result.quarantined["confirmed_by"]) == {"cninfo", "eastmoney"}
    assert (result.quarantined["reason"] == REASON_CROSS_SOURCE_CONFLICT).all()


def test_reviewed_conflict_accepts_pinned_source_and_facts():
    conflict = normalize_corporate_actions(cninfo_cash(0.1), eastmoney_cash(0.2))

    reviewed = apply_corporate_action_reviews(
        conflict,
        [
            {
                "symbol": "600519.SH",
                "ex_date": "2020-06-11",
                "selected_source": "cninfo",
                "record_date": "2020-06-10",
                "cash_dividend_per_share": 0.1,
                "bonus_share_ratio": 0.0,
                "capitalization_ratio": 0.0,
            }
        ],
    )

    assert reviewed.quarantined.empty
    assert reviewed.accepted.iloc[0]["confirmed_by"] == "cninfo+reviewed"


def test_equal_cninfo_and_eastmoney_facts_cross_confirm_to_one_row():
    result = normalize_corporate_actions(cninfo_cash(0.1), eastmoney_cash(0.1))
    assert result.quarantined.empty
    assert len(result.accepted) == 1
    row = result.accepted.iloc[0]
    assert row["symbol"] == "600519.SH"
    assert row["confirmed_by"] == "cninfo+eastmoney"
    assert row["status"] == "implemented"
    assert row["cash_dividend_per_share"] == pytest.approx(0.1)


def test_conflict_detects_differing_record_date_even_when_ratio_matches():
    result = normalize_corporate_actions(
        cninfo_cash(0.1, record="2020-06-10"),
        eastmoney_cash(0.1, record="2020-06-11"),
    )
    assert result.accepted.empty
    assert (result.quarantined["reason"] == REASON_CROSS_SOURCE_CONFLICT).all()


def test_single_source_implemented_action_is_accepted():
    result = normalize_corporate_actions(cninfo_cash(0.1), None)
    assert not result.accepted.empty
    assert result.quarantined.empty
    assert result.accepted.iloc[0]["confirmed_by"] == "cninfo"


def test_both_empty_frames_yield_typed_empty_result():
    result = normalize_corporate_actions(None, pd.DataFrame())
    assert result.accepted.empty
    assert result.quarantined.empty


def test_not_implemented_action_is_quarantined_not_accepted():
    result = normalize_corporate_actions(_cninfo_plan(progress="预案"), None)
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_NOT_IMPLEMENTED


def test_implemented_action_missing_record_date_is_incomplete():
    result = normalize_corporate_actions(_cninfo_plan(record=None), None)
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_INCOMPLETE


def test_implemented_action_without_distribution_components_is_incomplete():
    result = normalize_corporate_actions(_cninfo_plan(cash_per_10=0.0), None)
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_INCOMPLETE


def _cninfo_nat_row_plus_valid():
    """One row whose ex-date is ``NaT``, one fully valid row, same symbol.

    AKShare dividend frames carry ``NaT`` ex-dates for announced-but-unscheduled
    distributions.  A ``NaT`` is not ``None``, so it slips past the
    missing-value quarantine and reaches the reconciliation key set.
    """
    frame = _cninfo_plan(cash_per_10=10.0)
    nat = frame.iloc[[0]].copy()
    nat["股权登记日"] = "2021-06-10"
    nat["公告日期"] = "2021-05-20"
    merged = pd.concat([nat, frame], ignore_index=True)
    # ``concat`` downcasts a bare ``pd.NaT`` to float ``nan``, which the parser
    # already reads as missing -- assign into the object cell afterwards so the
    # fixture carries the real ``NaT`` the supplier delivers.
    merged.at[0, "除权除息日"] = pd.NaT
    return merged


def test_nat_ex_date_is_quarantined_as_incomplete():
    """A ``NaT`` ex-date is a missing fact, not a bookable event."""
    result = normalize_corporate_actions(_cninfo_nat_row_plus_valid(), None)
    quarantined = result.quarantined
    assert len(quarantined) == 1
    assert quarantined.iloc[0]["reason"] == REASON_INCOMPLETE


def test_nat_row_does_not_discard_the_symbols_other_actions():
    """The sibling must survive: production books per symbol, not per frame.

    Before the fix this case raised ``TypeError: Cannot compare NaT with
    datetime.date object`` while sorting the key set, and the caller treated
    the whole symbol as having no corporate actions at all.
    """
    result = normalize_corporate_actions(_cninfo_nat_row_plus_valid(), None)
    assert len(result.accepted) == 1
    assert result.accepted.iloc[0]["ex_date"] == datetime.date(2020, 6, 11)
    assert result.accepted.iloc[0]["cash_dividend_per_share"] == pytest.approx(1.0)


def test_mixed_cash_bonus_and_capitalization_fields_are_preserved():
    frame = _cninfo_plan(cash_per_10=10.0, bonus_per_10=2.0, cap_per_10=3.0)
    result = normalize_corporate_actions(frame, None)
    row = result.accepted.iloc[0]
    assert row["cash_dividend_per_share"] == pytest.approx(1.0)
    assert row["bonus_share_ratio"] == pytest.approx(0.2)
    assert row["capitalization_ratio"] == pytest.approx(0.3)
    # No rights issue in this plan: the rights fields are preserved as empty.
    assert pd.isna(row["rights_issue_ratio"])
    assert pd.isna(row["rights_issue_price"])


def test_rights_issue_plan_is_tagged_unsupported():
    result = normalize_corporate_actions(
        _cninfo_plan(cash_per_10=5.0, plan="拟配股，每10股配2股"), None
    )
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_UNSUPPORTED_CORPORATE_ACTION


def test_merger_conversion_plan_is_tagged_unsupported():
    result = normalize_corporate_actions(
        _cninfo_plan(plan="吸收合并注销股份"), None
    )
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_UNSUPPORTED_CORPORATE_ACTION


def test_cross_source_equal_unsupported_stays_unsupported():
    result = normalize_corporate_actions(
        _cninfo_plan(plan="配股每10股配2股"),
        eastmoney_cash(0.0, plan="配股每10股配2股"),
    )
    assert result.accepted.empty
    assert (result.quarantined["reason"] == REASON_UNSUPPORTED_CORPORATE_ACTION).all()


def test_plan_column_absent_uses_documented_default_without_raising():
    # Regression: an Eastmoney-shaped frame with 方案进度 but no standalone
    # 方案 / 方案说明 column used to crash ``normalize_corporate_actions`` with
    # ``KeyError('')`` because the optional plan field was resolved to "" and
    # then read unconditionally. The plan read is now skipped when no column is
    # present, so the plan keeps its documented default (absent -> no text) and
    # normalization proceeds.
    result = normalize_corporate_actions(
        None, eastmoney_without_plan_column(per_share=0.1)
    )
    assert not result.accepted.empty
    assert result.quarantined.empty
    row = result.accepted.iloc[0]
    assert row["symbol"] == "600519.SH"
    assert row["confirmed_by"] == "eastmoney"
    assert row["status"] == "implemented"
    assert row["cash_dividend_per_share"] == pytest.approx(0.1)


def test_plan_column_absent_unsupported_detection_still_reads_progress():
    # With no 方案 / 方案说明 column the plan defaults to absent, but unsupported
    # keyword detection must still consult the 方案进度 text.
    result = normalize_corporate_actions(
        None, eastmoney_without_plan_column(per_share=0.1, progress="拟配股")
    )
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_UNSUPPORTED_CORPORATE_ACTION


def test_plan_column_absent_second_supplier_never_reads_empty_plan():
    # Cross-source path: a plan-less Eastmoney frame reconciled against CNINFO
    # must not read the empty plan resolution on either side.
    result = normalize_corporate_actions(
        _cninfo_plan(cash_per_10=1.0),
        eastmoney_without_plan_column(per_share=0.1),
    )
    assert result.quarantined.empty
    assert not result.accepted.empty
    assert result.accepted.iloc[0]["confirmed_by"] == "cninfo+eastmoney"


# --------------------------------------------------------------------------- #
# Non-distributive events (ADR-008)
# --------------------------------------------------------------------------- #
#
# CNINFO types every 分红 row with 分红类型.  Three of those types describe a
# share transfer that never reaches a pre-event holder: 重整转增 moves shares
# to bankruptcy-reorganisation creditors (深交所自律监管指引第14号 §39: 重整
# 转增不向原股东分配), while 承诺补偿 and 股改分红 shift shares between existing
# holders without changing total share capital.  None of them is a price event,
# so booking one invents an ex-date adjustment and -- because the supplier
# reports no 除权日 for them -- an absent ex-date that only looks like a data
# gap.  Only 重整转增 is refused here: 股改分红 can carry a real, exchange-
# published ex-date (600733's 10转25) and must keep being booked.


def _cninfo_typed(
    distribution_type: str | None,
    *,
    cap_per_10: float = 19.24,
    ex: str | None = None,
    record: str | None = "2021-12-21",
    plan: str = "重整计划转增股票",
) -> pd.DataFrame:
    """A CNINFO frame in the akshare-native layout, typed via 分红类型."""
    frame = pd.DataFrame(
        [
            {
                # 600515.SH's real 重整转增: ann 2021-12-16, record 2021-12-21,
                # ex 2021-12-22, 转增 19.2387 per 10 shares.
                "实施方案公告日期": "2021-12-16",
                "股权登记日": record,
                "除权日": ex,
                "派息比例": 0.0,
                "送股比例": 0.0,
                "转增比例": cap_per_10,
                "方案进度": "实施",
                "实施方案分红说明": plan,
                "分红类型": distribution_type,
            }
        ]
    )
    return prepare_cninfo_dividend_frame(frame, "600515.SH")


def test_restructuring_capitalization_is_quarantined_not_booked():
    """600515's 2021 重整转增 carried an ex-date yet moved no price.

    Booking it produced a phantom +88% in ``adjusted_bar``: the supplier
    publishes a 除权日 for the row, but the shares go to creditors, so no
    exchange ex-date adjustment exists.  The row must be refused on its
    declared type rather than booked.
    """
    result = normalize_corporate_actions(
        _cninfo_typed("重整转增", ex="2021-12-22"), None
    )
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_NON_DISTRIBUTIVE_RESTRUCTURING


def test_restructuring_capitalization_without_ex_date_is_not_incomplete():
    """Its missing ex-date is a property of the event, not a data gap.

    Reading it as ``incomplete`` made the whole symbol/window UNTRUSTED and
    blocked every unrelated holding of that symbol.  The refused event is
    evidence about a real, correctly-reported event, so it gets its own reason.
    """
    result = normalize_corporate_actions(_cninfo_typed("重整转增"), None)
    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_NON_DISTRIBUTIVE_RESTRUCTURING


def test_share_reform_capitalization_is_still_booked():
    """600733's 股改分红 10转25 (ex 2018-09-19) is a real ex-date event.

    股改分红's share transfer is a genuine distribution to holders, so the
    refusal must key on 重整转增 alone and leave this type bookable.
    """
    frame = pd.DataFrame(
        [
            {
                "实施方案公告日期": "2018-08-20",
                "股权登记日": "2018-09-18",
                "除权日": "2018-09-19",
                "派息比例": 0.0,
                "送股比例": 0.0,
                "转增比例": 25.0,
                "方案进度": "实施",
                "实施方案分红说明": "股权分置改革方案",
                "分红类型": "股改分红",
            }
        ]
    )
    prepared = prepare_cninfo_dividend_frame(frame, "600733.SH")
    result = normalize_corporate_actions(prepared, None)
    assert result.quarantined.empty
    assert result.accepted.iloc[0]["capitalization_ratio"] == pytest.approx(2.5)


def test_ordinary_distribution_is_unaffected_by_its_declared_type():
    """A typed ordinary distribution with an ex-date books exactly as before."""
    result = normalize_corporate_actions(
        _cninfo_typed(
            "年度分红", cap_per_10=10.0, ex="2021-12-22", plan="10转10"
        ),
        None,
    )
    assert result.quarantined.empty
    assert result.accepted.iloc[0]["capitalization_ratio"] == pytest.approx(1.0)


def test_absent_type_column_is_treated_as_an_ordinary_distribution():
    """Only CNINFO publishes 分红类型; the legacy and Eastmoney layouts omit it.

    An absent column must stay a documented default (ordinary distribution),
    never a frame-level error, or every Eastmoney row would fail to parse.
    """
    result = normalize_corporate_actions(_cninfo_plan(cash_per_10=1.0), None)
    assert result.quarantined.empty
    assert len(result.accepted) == 1


# --------------------------------------------------------------------------- #
# The subscription (rights-issue) lane
# --------------------------------------------------------------------------- #
#
# A subscription is reported by CNINFO's allotment interface alone; neither
# dividend interface reports one at all.  The lane therefore has no sibling to
# cross-confirm against and parses its own native layout.


def test_allotment_row_becomes_an_accepted_subscription():
    """The native per-ten ratio becomes a per-share ratio; the price does not."""
    prepared = prepare_allotment_rights_frame(allotment(), "002202.SZ")
    result = normalize_rights_issue_actions(prepared)

    row = result.accepted.iloc[0]
    assert row["symbol"] == "002202.SZ"
    assert row["record_date"] == datetime.date(2019, 3, 20)
    assert row["ex_date"] == datetime.date(2019, 3, 29)
    assert row["rights_issue_ratio"] == pytest.approx(0.19)
    assert row["rights_issue_price"] == pytest.approx(7.02)
    assert row["status"] == "implemented"
    # One source only: never the cross-confirmed label.
    assert row["confirmed_by"] == "cninfo_allotment"
    assert result.quarantined.empty


def test_subscription_stays_supported_when_the_lane_can_honour_it():
    """``配股`` is a claim the allotment lane can satisfy, so it is not refused.

    The same word in a *dividend* frame still quarantines, because that lane
    carries no subscription facts -- see the unsupported-plan tests above.
    """
    frame = prepare_allotment_rights_frame(allotment(), "002202.SZ")
    frame["方案"] = "10配1.9股，配股价7.02元"

    result = normalize_rights_issue_actions(frame)
    assert len(result.accepted) == 1
    assert result.quarantined.empty


def test_a_subscription_without_a_price_is_incomplete():
    """A ratio with no price cannot be turned into an ex-date adjustment."""
    prepared = prepare_allotment_rights_frame(
        allotment(price=None), "002202.SZ"
    )
    result = normalize_rights_issue_actions(prepared)

    assert result.accepted.empty
    assert result.quarantined.iloc[0]["reason"] == REASON_INCOMPLETE


def test_an_unsettled_plan_never_quarantines_the_symbol():
    """No ex-date means no event: the row is dropped, not flagged.

    Quarantining it would make the symbol's coverage read UNTRUSTED for the
    whole window on the strength of a plan that may never settle.
    """
    prepared = prepare_allotment_rights_frame(
        allotment(ex=None), "002202.SZ"
    )
    assert prepared.empty

    result = normalize_rights_issue_actions(prepared)
    assert result.accepted.empty
    assert result.quarantined.empty


def test_allotment_frame_is_dropped_when_a_required_column_is_absent():
    """A layout change must fail loudly rather than parse as a no-event frame."""
    frame = allotment().drop(columns=["配股价格"])

    with pytest.raises(ValueError, match="missing required columns"):
        prepare_allotment_rights_frame(frame, "002202.SZ")


# --------------------------------------------------------------------------- #
# Window scope of a quarantined row (ADR-006): an event can only matter to a
# window it falls in, and the first *known* date decides which window that is.
# --------------------------------------------------------------------------- #

_WINDOW_START = datetime.date(2015, 1, 5)
_WINDOW_END = datetime.date(2016, 12, 30)


def _quarantine_row(**overrides):
    """One canonical quarantine row; dates default to ``None``."""
    row = {
        "symbol": "600000.SH",
        "announcement_date": None,
        "record_date": None,
        "ex_date": None,
        "status": "implemented",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        # 1. ex_date known: the transition itself is outside the window.
        (
            _quarantine_row(ex_date=datetime.date(2014, 12, 31)),
            EXCLUSION_EX_DATE_OUT_OF_WINDOW,
        ),
        (
            _quarantine_row(ex_date=datetime.date(2017, 1, 3)),
            EXCLUSION_EX_DATE_OUT_OF_WINDOW,
        ),
        (
            _quarantine_row(ex_date=datetime.date(2015, 1, 5)),
            None,
        ),
        (
            _quarantine_row(ex_date=datetime.date(2016, 12, 30)),
            None,
        ),
        # ex_date wins over a record_date that would say otherwise.
        (
            _quarantine_row(
                ex_date=datetime.date(2015, 6, 1),
                record_date=datetime.date(2014, 6, 1),
            ),
            None,
        ),
        # 2. no ex_date: the record date decides (its own transition follows it).
        (
            _quarantine_row(record_date=datetime.date(2017, 1, 3)),
            EXCLUSION_RECORD_DATE_OUT_OF_WINDOW,
        ),
        (
            _quarantine_row(record_date=datetime.date(2015, 1, 5)),
            None,
        ),
        #    A record date inside the settlement-lag margin of ``start`` is kept:
        #    its ex-date could still settle inside the window.
        (
            _quarantine_row(record_date=datetime.date(2014, 12, 31)),
            None,
        ),
        #    The margin is the measured lag: 2015-01-05 - 13 days = 2014-12-23.
        (
            _quarantine_row(record_date=datetime.date(2014, 12, 22)),
            EXCLUSION_RECORD_DATE_OUT_OF_WINDOW,
        ),
        (
            _quarantine_row(record_date=datetime.date(2014, 12, 23)),
            None,
        ),
        # 3. neither dated fact: a pre-window announcement on an implemented
        #    record cannot affect the window.
        (
            _quarantine_row(announcement_date=datetime.date(1998, 6, 1)),
            EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED,
        ),
        #    ... but only when the record claims to be implemented.
        (
            _quarantine_row(
                announcement_date=datetime.date(1998, 6, 1),
                status="not_implemented",
            ),
            None,
        ),
        #    An announcement *after* the window keeps the row: its ex-date may
        #    still land inside it.
        (
            _quarantine_row(announcement_date=datetime.date(2017, 1, 3)),
            None,
        ),
        (
            _quarantine_row(announcement_date=datetime.date(2015, 6, 1)),
            None,
        ),
        # 4. no known date at all: fail closed.
        (_quarantine_row(), None),
    ],
)
def test_quarantine_row_out_of_window_reason_decides_by_first_known_date(row, expected):
    assert (
        quarantine_row_out_of_window_reason(row, _WINDOW_START, _WINDOW_END) == expected
    )


def test_quarantine_row_out_of_window_reason_reads_pandas_date_cells():
    """A published quarantine table hands back ``Timestamp`` / ``NaT`` cells."""
    row = _quarantine_row(
        ex_date=pd.NaT,
        record_date=pd.NaT,
        announcement_date=pd.Timestamp("1998-06-01"),
    )
    assert (
        quarantine_row_out_of_window_reason(row, _WINDOW_START, _WINDOW_END)
        == EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED
    )
