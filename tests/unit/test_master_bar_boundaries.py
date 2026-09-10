"""合成夹具覆盖 security_master 的 bar 边界 WARNING。

真实数据集里 30 只标的的 ``delist_date`` 全为空，``after_delist_date``
分支永远不会被触发，所以这条分支只能用构造数据验证。
"""

from datetime import date

import pandas as pd

from stock_quant.data_pipeline import (
    CODE_MASTER_BAR_BOUNDARY,
    master_bar_boundary_issues,
)
from stock_quant.data_quality.models import Severity


def _master(symbol: str, *, list_date=None, delist_date=None) -> pd.DataFrame:
    return pd.DataFrame(
        [{"symbol": symbol, "list_date": list_date, "delist_date": delist_date}]
    )


def _daily(symbol: str, trade_date) -> pd.DataFrame:
    return pd.DataFrame([{"symbol": symbol, "trade_date": trade_date}])


def test_bar_after_delist_date_is_flagged():
    """退市后的 bar 与 master 事实矛盾，报 WARNING 而不是静默通过。"""
    issues = master_bar_boundary_issues(
        _master(
            "600005.SH",
            list_date=date(1999, 8, 3),
            delist_date=date(2017, 2, 13),
        ),
        _daily("600005.SH", date(2017, 2, 14)),
    )
    assert [issue.code for issue in issues] == [CODE_MASTER_BAR_BOUNDARY]
    assert issues[0].severity is Severity.WARNING
    assert issues[0].symbol == "600005.SH"
    assert issues[0].details["boundary"] == "after_delist_date"
    assert issues[0].details["delist_date"] == "2017-02-13"


def test_bar_before_list_date_is_flagged():
    """回归：上市前的 bar 仍按原语义报 WARNING。"""
    issues = master_bar_boundary_issues(
        _master("600000.SH", list_date=date(2021, 11, 20)),
        _daily("600000.SH", date(2021, 11, 19)),
    )
    assert issues[0].details["boundary"] == "before_list_date"
    assert issues[0].details["list_date"] == "2021-11-20"


def test_bar_on_the_boundaries_is_clean():
    """两端是闭区间：恰好等于 list_date / delist_date 的行不算越界。"""
    master = _master(
        "600005.SH", list_date=date(2017, 2, 13), delist_date=date(2017, 2, 13)
    )
    assert master_bar_boundary_issues(master, _daily("600005.SH", date(2017, 2, 13))) == []


def test_symbol_without_bounds_is_skipped():
    """master 缺两侧边界的标的完全不参与判定。"""
    assert master_bar_boundary_issues(
        _master("600005.SH"), _daily("600005.SH", date(2017, 2, 14))
    ) == []


def test_empty_daily_returns_nothing():
    assert master_bar_boundary_issues(
        _master("600005.SH", delist_date=date(2017, 2, 13)), pd.DataFrame()
    ) == []
