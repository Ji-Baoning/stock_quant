"""Execution simulation: rejections, sells-before-buys and lot reduction (Task 9).

``ExecutionSimulator.execute`` consumes the engine's ordered ``Order`` list and
the execution-date unadjusted bars, drives an ``Account`` and returns an
immutable ``ExecutionResult``.  Each order is pre-checked and every rejection
carries an exact reason; sells execute before buys so same-day sell proceeds
fund buys; when cash cannot cover a buy's full target quantity the simulator
fills the largest whole-100-share-lot quantity that fits and never overdrafts.
Sells consume sellable lots; buys become sellable on the next trading day.
"""

from datetime import date
from decimal import Decimal
from itertools import count

import pandas as pd
import pytest

from stock_quant.backtest.account import Account
from stock_quant.backtest.costs import CostModel
from stock_quant.backtest.execution import (
    EXECUTION_BAR_REQUIRED_COLUMNS,
    ExecutionSimulator,
)
from stock_quant.backtest.models import (
    BUY,
    REASON_INSUFFICIENT_CASH,
    REASON_INSUFFICIENT_SELLABLE_QUANTITY,
    REASON_MISSING_OPEN,
    REASON_MISSING_PRE_CLOSE,
    REASON_QUALITY_ERROR,
    REASON_SUSPENDED_OR_UNKNOWN,
    REASON_UNCOVERED_RULE,
    SELL,
    Fill,
    Order,
)
from stock_quant.config import CostRate
from stock_quant.data_model.calendar import TradingCalendar
from stock_quant.data_model.trading_rules import (
    REASON_BUY_AT_UPPER_LIMIT,
    REASON_SELL_AT_LOWER_LIMIT,
    TradingRuleBook,
)

# --------------------------------------------------------------------------- #
# Deterministic offline fixtures
# --------------------------------------------------------------------------- #

D0 = date(2020, 1, 2)  # buy date for the prior day's position
EXEC_DATE = date(2020, 1, 3)
_OPEN_DAYS = (
    date(2020, 1, 2),
    date(2020, 1, 3),
    date(2020, 1, 6),
    date(2020, 1, 7),
    date(2020, 1, 8),
)
_INITIAL_CASH = Decimal("5010.00")

_PRICE_RULES_YAML = """\
price_tick: "0.01"
price_limits:
  - boards: [sh_main, sz_main]
    status: NORMAL
    effective_from: 1996-12-16
    rate: "0.10"
no_limit_first_sessions: []
"""


def _rate() -> CostRate:
    return CostRate(
        effective_from=date(2020, 1, 1),
        commission_rate=0.0003,
        minimum_commission=5.0,
        stamp_tax_sell_rate=0.0005,
        slippage_rate=0.001,
    )


@pytest.fixture
def calendar() -> TradingCalendar:
    return TradingCalendar.from_open_days(_OPEN_DAYS)


@pytest.fixture
def rule_book(tmp_path) -> TradingRuleBook:
    path = tmp_path / "trading_rules.yml"
    path.write_text(_PRICE_RULES_YAML, encoding="utf-8")
    return TradingRuleBook.from_yaml(path)


@pytest.fixture
def simulator(rule_book: TradingRuleBook) -> ExecutionSimulator:
    return ExecutionSimulator(cost_model=CostModel(_rate()), rule_book=rule_book)


def make_bars(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows))


def bar(
    symbol: str,
    open_price: float,
    pre_close: float,
    quality: str = "INFO",
    **extra,
) -> dict:
    row = {
        "symbol": symbol,
        "open": open_price,
        "pre_close": pre_close,
        "quality_severity": quality,
    }
    row.update(extra)
    return row


def bars() -> pd.DataFrame:
    """Default execution bars: three names each opening at 10.00."""
    return make_bars(
        bar("600000.SH", 10.0, 10.0),
        bar("600001.SH", 10.0, 10.0),
        bar("600002.SH", 10.0, 10.0),
    )


_order_seq = count(1)


def order(
    side: str, symbol: str, quantity: int, *, order_id: str | None = None
) -> Order:
    return Order(
        order_id=order_id or f"o{next(_order_seq)}",
        side=side,
        symbol=symbol,
        quantity=quantity,
    )


def expensive_buy_orders() -> list[Order]:
    """Buys whose full quantities cannot all fit inside 1500 cash."""
    return [
        order(BUY, symbol, 200)
        for symbol in ("600000.SH", "600001.SH", "600002.SH")
    ]


def _fill(
    side: str,
    symbol: str,
    quantity: int,
    trade_date: date,
    *,
    price: str = "10.00",
    commission: str = "5.00",
    stamp: str = "0.00",
) -> Fill:
    n = next(_order_seq)
    return Fill(
        fill_id=f"F{n}",
        order_id=f"o{n}",
        trade_date=trade_date,
        side=side,
        symbol=symbol,
        quantity=quantity,
        price=Decimal(price),
        commission=Decimal(commission),
        stamp_tax=Decimal(stamp),
    )


# --------------------------------------------------------------------------- #
# Cash-shortage reduction in whole lots, without overdraft
# --------------------------------------------------------------------------- #


def test_cash_shortage_reduces_buys_in_lots_without_overdraft(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("1500"), calendar=calendar)
    result = simulator.execute(expensive_buy_orders(), bars(), account, EXEC_DATE)
    fills = result.fills
    assert sum(f.cash_delta for f in fills) >= -1500
    assert all(f.quantity % 100 == 0 for f in fills)
    # The first (highest-priority) order is reduced to one affordable lot ...
    assert [f.quantity for f in fills] == [100]
    assert account.cash == Decimal("494.00")
    # o1's unaffordable remainder is rejected for cash (it filled only one
    # lot); the two lower-priority buys are rejected in full, never skipped.
    reasons = {entry.reason for entry in result.rejections}
    assert reasons == {REASON_INSUFFICIENT_CASH}
    assert len(result.rejections) == 3
    residual = next(entry for entry in result.rejections if entry.filled_quantity)
    assert residual.symbol == "600000.SH"
    assert residual.requested_quantity == 200
    assert residual.filled_quantity == 100
    assert residual.rejected_quantity == 100
    unfilled = [entry for entry in result.rejections if entry.filled_quantity == 0]
    assert len(unfilled) == 2
    assert {entry.symbol for entry in unfilled} == {"600001.SH", "600002.SH"}
    assert all(entry.requested_quantity == 200 for entry in unfilled)


def test_buys_fully_fill_when_cash_is_sufficient(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    result = simulator.execute(
        [order(BUY, "600000.SH", 100)], bars(), account, EXEC_DATE
    )
    assert len(result.fills) == 1
    assert result.rejections == ()
    fill = result.fills[0]
    assert fill.quantity == 100
    assert fill.price == Decimal("10.01")
    assert fill.commission == Decimal("5.00")
    assert fill.stamp_tax == 0
    assert fill.cash_delta == Decimal("-1006.00")
    assert account.cash == Decimal("98994.00")


# --------------------------------------------------------------------------- #
# Sells before buys: same-day sell proceeds fund buys
# --------------------------------------------------------------------------- #


def test_sells_execute_before_buys_and_proceeds_fund_same_day_buys(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(_INITIAL_CASH, calendar=calendar)
    account.apply_fill(_fill(BUY, "600000.SH", 200, D0, price="20.02"))
    # The buy orders are listed first to prove the simulator reorders sells.
    orders_list = [
        order(BUY, "600001.SH", 100),
        order(BUY, "600002.SH", 100),
        order(BUY, "600003.SH", 100),
        order(SELL, "600000.SH", 200),
    ]
    day_bars = make_bars(
        bar("600000.SH", 20.0, 20.0),
        bar("600001.SH", 10.0, 10.0),
        bar("600002.SH", 10.0, 10.0),
        bar("600003.SH", 10.0, 10.0),
    )
    result = simulator.execute(orders_list, day_bars, account, EXEC_DATE)
    # Without the sell proceeds (~3989) the 1001 cash could not fund any buy.
    assert result.rejections == ()
    assert {f.symbol for f in result.fills if f.side == BUY} == {
        "600001.SH",
        "600002.SH",
        "600003.SH",
    }
    sell_fill = next(f for f in result.fills if f.side == SELL)
    assert sell_fill.quantity == 200
    assert sell_fill.price == Decimal("19.98")
    assert sell_fill.stamp_tax == Decimal("2.00")
    assert account.cash == Decimal("1972.00")
    assert account.position_quantity("600000.SH") == 0
    assert account.position_quantity("600001.SH") == 100


# --------------------------------------------------------------------------- #
# Exact rejection reasons
# --------------------------------------------------------------------------- #


def test_buy_with_no_bar_row_is_rejected_suspended_or_unknown(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    result = simulator.execute(
        [order(BUY, "600009.SH", 100)], bars(), account, EXEC_DATE
    )
    assert _only_reason(result) == REASON_SUSPENDED_OR_UNKNOWN


def test_buy_with_missing_open_is_rejected(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    missing = make_bars(bar("600000.SH", float("nan"), 10.0))
    result = simulator.execute(
        [order(BUY, "600000.SH", 100)], missing, account, EXEC_DATE
    )
    assert _only_reason(result) == REASON_MISSING_OPEN


def test_buy_with_missing_pre_close_is_rejected(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    missing = make_bars(bar("600000.SH", 10.0, None))
    result = simulator.execute(
        [order(BUY, "600000.SH", 100)], missing, account, EXEC_DATE
    )
    assert _only_reason(result) == REASON_MISSING_PRE_CLOSE


@pytest.mark.parametrize("quality", ["ERROR", "FATAL"])
def test_quality_error_bar_is_rejected(
    simulator: ExecutionSimulator, calendar: TradingCalendar, quality: str
):
    account = Account(Decimal("100000"), calendar=calendar)
    flagged = make_bars(bar("600000.SH", 10.0, 10.0, quality=quality))
    result = simulator.execute(
        [order(BUY, "600000.SH", 100)], flagged, account, EXEC_DATE
    )
    assert _only_reason(result) == REASON_QUALITY_ERROR


def test_buy_at_upper_limit_is_rejected(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    limit_up = make_bars(bar("600000.SH", 11.0, 10.0))
    result = simulator.execute(
        [order(BUY, "600000.SH", 100)], limit_up, account, EXEC_DATE
    )
    assert _only_reason(result) == REASON_BUY_AT_UPPER_LIMIT


def test_sell_at_lower_limit_is_rejected(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    account.apply_fill(_fill(BUY, "600000.SH", 100, D0))
    limit_down = make_bars(bar("600000.SH", 9.0, 10.0))
    result = simulator.execute(
        [order(SELL, "600000.SH", 100)], limit_down, account, EXEC_DATE
    )
    assert _only_reason(result) == REASON_SELL_AT_LOWER_LIMIT


def test_uncovered_rule_is_rejected(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    uncovered = make_bars(bar("430001.BJ", 10.0, 10.0))
    result = simulator.execute(
        [order(BUY, "430001.BJ", 100)], uncovered, account, EXEC_DATE
    )
    assert _only_reason(result) == REASON_UNCOVERED_RULE


def test_buy_below_a_single_lot_of_cash_is_dropped_not_partially_filled(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("1000"), calendar=calendar)
    result = simulator.execute(
        [order(BUY, "600000.SH", 100)], bars(), account, EXEC_DATE
    )
    assert result.fills == ()
    assert _only_reason(result) == REASON_INSUFFICIENT_CASH
    assert account.cash == Decimal("1000.00")


def test_sell_exceeding_sellable_quantity_is_rejected(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    account.apply_fill(_fill(BUY, "600000.SH", 100, D0))
    result = simulator.execute(
        [order(SELL, "600000.SH", 200)], bars(), account, EXEC_DATE
    )
    assert result.fills == ()
    assert _only_reason(result) == REASON_INSUFFICIENT_SELLABLE_QUANTITY


# --------------------------------------------------------------------------- #
# Bar-contract enforcement
# --------------------------------------------------------------------------- #


def test_required_execution_bar_columns_are_enforced(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    bad = make_bars({"symbol": "600000.SH", "open": 10.0, "quality_severity": "INFO"})
    with pytest.raises(ValueError, match="pre_close"):
        simulator.execute([order(BUY, "600000.SH", 100)], bad, account, EXEC_DATE)
    assert EXECUTION_BAR_REQUIRED_COLUMNS == (
        "symbol",
        "open",
        "pre_close",
        "quality_severity",
    )


def test_duplicate_symbol_bar_rows_are_rejected(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account = Account(Decimal("100000"), calendar=calendar)
    duplicated = make_bars(bar("600000.SH", 10.0, 10.0), bar("600000.SH", 10.0, 10.0))
    with pytest.raises(ValueError, match="one row"):
        simulator.execute(
            [order(BUY, "600000.SH", 100)], duplicated, account, EXEC_DATE
        )


# --------------------------------------------------------------------------- #
# Deterministic replay from the executor's own log
# --------------------------------------------------------------------------- #


def test_executor_output_replays_to_identical_account_state(
    simulator: ExecutionSimulator, calendar: TradingCalendar
):
    account_a = Account(_INITIAL_CASH, calendar=calendar)
    account_a.apply_fill(_fill(BUY, "600000.SH", 200, D0, price="20.02"))
    orders_list = [
        order(BUY, "600001.SH", 100),
        order(BUY, "600002.SH", 100),
        order(SELL, "600000.SH", 200),
        order(BUY, "600003.SH", 100),
    ]
    day_bars = make_bars(
        bar("600000.SH", 20.0, 20.0),
        bar("600001.SH", 10.0, 10.0),
        bar("600002.SH", 10.0, 10.0),
        bar("600003.SH", 10.0, 10.0),
    )
    simulator.execute(orders_list, day_bars, account_a, EXEC_DATE)

    account_b = Account(_INITIAL_CASH, calendar=calendar)
    for entry in account_a.order_ledger:
        account_b.record_order(entry.order)
    for entry in account_a.fill_ledger:
        account_b.apply_fill(entry.fill)
    assert account_b.state() == account_a.state()


def _only_reason(result) -> str:
    assert len(result.rejections) == 1
    assert len(result.fills) == 0
    return result.rejections[0].reason
