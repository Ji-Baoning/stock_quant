"""Corporate-action cross-source reconciliation tests (Task 5).

``normalize_corporate_actions`` consumes supplier-native corporate-action
frames in the documented AKShare layouts (CNINFO primary, Eastmoney
cross-check) and reconciles them into implemented, supported events. These
tests never call a network or supplier; every frame is authored inline.
"""

import pandas as pd
import pytest

from stock_quant.data_model.corporate_actions import (
    REASON_CROSS_SOURCE_CONFLICT,
    REASON_INCOMPLETE,
    REASON_NOT_IMPLEMENTED,
    REASON_UNSUPPORTED_CORPORATE_ACTION,
    normalize_corporate_actions,
    prepare_cninfo_dividend_frame,
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
