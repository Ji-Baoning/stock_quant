"""Immutable order, fill, fee, lot and execution-result records (Task 9).

The execution core exchanges only frozen records so a backtest can be replayed
byte-for-byte: ``Order`` is the requested trade, ``Fill`` the executed one,
``PositionLot`` the T+1 lot a buy opens and ``ExecutionResult`` the per-day
report of fills and exact-reason rejections.  The rejection-reason constants
are the single vocabulary ``ExecutionSimulator`` writes and Task 10 reads;
price-limit rejections reuse the strings already emitted by
``TradingRuleBook.PriceLimits.block_reason``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

BUY = "BUY"
SELL = "SELL"
SIDES = (BUY, SELL)

#: Shares per A-share board lot; buys and sells are whole lots only.
LOT_SIZE = 100

#: Decimal cent, the smallest unit of money the account tracks.
CENT = Decimal("0.01")

# --- Exact-rejection reasons written on ``RejectedOrder`` records. -----------
# Missing-open / missing reference price / suspended / quality / uncovered-rule
# reasons are decided by the executor before an order reaches the account; the
# two cash/sellable reasons can also arise dynamically during execution.
REASON_MISSING_OPEN = "missing_open"
REASON_MISSING_PRE_CLOSE = "missing_pre_close"
REASON_SUSPENDED_OR_UNKNOWN = "suspended_or_unknown"
REASON_QUALITY_ERROR = "quality_error"
REASON_INSUFFICIENT_CASH = "insufficient_cash"
REASON_INSUFFICIENT_SELLABLE_QUANTITY = "insufficient_sellable_quantity"
REASON_UNCOVERED_RULE = "uncovered_rule"

# Price-limit rejections reuse the A-share conservative policy already encoded
# by ``PriceLimits.block_reason`` (buy at/above the upper limit, sell at/below
# the lower limit are prohibited).  Their strings live in
# ``stock_quant.data_model.trading_rules`` as ``REASON_BUY_AT_UPPER_LIMIT`` and
# ``REASON_SELL_AT_LOWER_LIMIT``; the executor stores those values verbatim.


def require_side(side: object) -> str:
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")
    return side  # type: ignore[return-value]


def require_lot_quantity(quantity: object, *, name: str = "quantity") -> int:
    """Return ``quantity`` if it is a positive whole 100-share lot else raise."""
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TypeError(f"{name} must be an int number of shares, got {quantity!r}")
    if quantity <= 0 or quantity % LOT_SIZE != 0:
        raise ValueError(
            f"{name} must be a positive multiple of {LOT_SIZE}, got {quantity}"
        )
    return quantity


def require_price(value: object, *, name: str = "price") -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric, got {value!r}")
    converted = value if isinstance(value, Decimal) else Decimal(str(value))
    if converted <= 0:
        raise ValueError(f"{name} must be positive, got {converted}")
    return converted


def as_decimal(value: object) -> Decimal:
    """Convert a number/string to an exact ``Decimal`` (``Decimal`` passes)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError(f"cannot convert {value!r} to Decimal")
    return Decimal(str(value))


# --------------------------------------------------------------------------- #
# Fee quote
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FeeBreakdown:
    """One fill's price, gross notional and fees at cent precision."""

    side: str
    quantity: int
    price: Decimal
    gross: Decimal
    commission: Decimal
    stamp_tax: Decimal

    def __post_init__(self) -> None:
        require_side(self.side)
        require_lot_quantity(self.quantity, name="quantity")
        if self.price < 0:
            raise ValueError(f"price must be non-negative: {self.price}")
        if self.gross != self.price * self.quantity:
            raise ValueError(
                "gross must equal price * quantity: "
                f"{self.gross} != {self.price} * {self.quantity}"
            )
        if self.commission < 0 or self.stamp_tax < 0:
            raise ValueError("fees must be non-negative")

    @property
    def total_fee(self) -> Decimal:
        return self.commission + self.stamp_tax


# --------------------------------------------------------------------------- #
# Order and fill
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Order:
    """A requested whole-lot buy or sell for one execution date.

    ``order_id`` must be unique across the whole backtest so fills and
    rejections are traceable to exactly one order; the engine encodes any
    portfolio context (e.g. the target rank) in the identifier.
    """

    order_id: str
    side: str
    symbol: str
    quantity: int
    note: str = ""

    def __post_init__(self) -> None:
        require_side(self.side)
        require_lot_quantity(self.quantity, name="quantity")
        if not self.order_id or not self.symbol:
            raise ValueError("order_id and symbol must be non-empty")


@dataclass(frozen=True)
class Fill:
    """One executed trade with its price, fees and net cash effect."""

    fill_id: str
    order_id: str
    trade_date: date
    side: str
    symbol: str
    quantity: int
    price: Decimal
    commission: Decimal
    stamp_tax: Decimal

    def __post_init__(self) -> None:
        require_side(self.side)
        require_lot_quantity(self.quantity, name="quantity")
        if not self.fill_id or not self.order_id or not self.symbol:
            raise ValueError("fill_id, order_id and symbol must be non-empty")
        if not isinstance(self.trade_date, date):
            raise TypeError(f"trade_date must be a date, got {self.trade_date!r}")
        if self.price <= 0:
            raise ValueError(f"fill price must be positive: {self.price}")
        if self.commission < 0 or self.stamp_tax < 0:
            raise ValueError("fees must be non-negative")
        if self.side == BUY and self.stamp_tax != 0:
            raise ValueError("stamp tax is only charged on sells")

    @property
    def gross(self) -> Decimal:
        return self.price * self.quantity

    @property
    def fees(self) -> Decimal:
        return self.commission + self.stamp_tax

    @property
    def cash_delta(self) -> Decimal:
        """Signed change to account cash: sells credit, buys debit gross+fees."""
        if self.side == SELL:
            return self.gross - self.commission - self.stamp_tax
        return -(self.gross + self.commission)


# --------------------------------------------------------------------------- #
# Positions and T+1 lots
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PositionLot:
    """A block of shares with one ``buy_date`` and one T+1 ``available_date``.

    ``cost_basis`` is the lot's full acquisition cost (gross buy notional plus
    commission).  ``available_date`` is the next open day after ``buy_date``;
    ``None`` means no later open day is known in the trading calendar, so the
    lot is never sellable within the simulated window.

    ``quantity`` may be any positive whole share count: buys always open
    whole 100-share lots, but corporate actions (bonus/capitalization share
    credits, Task 10) can add odd-lot residuals to a holding, so a lot is not
    required to remain a board-lot multiple.  ``Order`` and ``Fill`` quantities
    are still validated as whole lots by ``require_lot_quantity``.
    """

    symbol: str
    buy_date: date
    quantity: int
    cost_basis: Decimal
    available_date: date | None

    def __post_init__(self) -> None:
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise TypeError(f"quantity must be an int, got {self.quantity!r}")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if self.cost_basis < 0:
            raise ValueError(f"cost_basis must be non-negative: {self.cost_basis}")
        if not isinstance(self.buy_date, date):
            raise TypeError(f"buy_date must be a date, got {self.buy_date!r}")

    @property
    def average_price(self) -> Decimal:
        return self.cost_basis / self.quantity


# --------------------------------------------------------------------------- #
# Rejections and the per-execution result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RejectedOrder:
    """An order (or the unfilled remainder of a reduced buy) and its reason.

    ``requested_quantity`` is the order's original target quantity;
    ``filled_quantity`` is nonzero only when a buy was filled below its target
    and this record captures the unaffordable remainder.
    """

    order_id: str
    side: str
    symbol: str
    requested_quantity: int
    reason: str
    filled_quantity: int = 0

    def __post_init__(self) -> None:
        require_side(self.side)
        if not self.order_id or not self.symbol:
            raise ValueError("order_id and symbol must be non-empty")
        if not self.reason:
            raise ValueError("reason must be non-empty")
        if self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be positive")
        if not (0 <= self.filled_quantity <= self.requested_quantity):
            raise ValueError("filled_quantity must lie in [0, requested_quantity]")

    @property
    def rejected_quantity(self) -> int:
        return self.requested_quantity - self.filled_quantity


@dataclass(frozen=True)
class ExecutionResult:
    """The immutable outcome of one ``ExecutionSimulator.execute`` call."""

    trade_date: date
    fills: tuple[Fill, ...] = ()
    rejections: tuple[RejectedOrder, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.trade_date, date):
            raise TypeError(f"trade_date must be a date, got {self.trade_date!r}")

    @property
    def fill_count(self) -> int:
        return len(self.fills)

    @property
    def rejection_count(self) -> int:
        return len(self.rejections)


# --------------------------------------------------------------------------- #
# Append-only ledger entries (cash, order, fill, position)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CashLedgerEntry:
    """One cash movement and the running balance after it."""

    seq: int
    kind: str
    amount: Decimal
    balance: Decimal
    note: str = ""


@dataclass(frozen=True)
class OrderLedgerEntry:
    """Every order submitted to the account (filled, reduced or rejected)."""

    seq: int
    order: Order


@dataclass(frozen=True)
class FillLedgerEntry:
    """Every fill applied to the account."""

    seq: int
    fill: Fill


@dataclass(frozen=True)
class PositionLedgerEntry:
    """A lot opened by a buy or the lot shares consumed by a sell.

    ``quantity`` and ``cost_basis`` are signed: positive on ``OPEN``, negative
    on ``CONSUME``, so summing the ledger reproduces current holdings.
    """

    seq: int
    kind: str
    symbol: str
    buy_date: date | None
    quantity: int
    cost_basis: Decimal
    available_date: date | None
    fill_id: str

    def __post_init__(self) -> None:
        if self.kind not in ("OPEN", "CONSUME"):
            raise ValueError(
                f"position kind must be OPEN or CONSUME, got {self.kind!r}"
            )
        if not self.symbol:
            raise ValueError("symbol must be non-empty")


@dataclass(frozen=True)
class CorporateActionLedgerEntry:
    """One booked implemented corporate action (Task 10).

    Recorded once per unique ``action_id`` when a corporate action is applied
    to an account that holds the name on the action's ``ex_date``; re-applying
    the same ``action_id`` is refused as a no-op.  ``cash_credited`` is the
    pre-tax cash dividend and ``shares_added`` the bonus/capitalization share
    increase booked on ``ex_date``.
    """

    seq: int
    action_id: str
    symbol: str
    ex_date: date
    record_date: date | None
    cash_credited: Decimal
    shares_added: int
    note: str = ""

    def __post_init__(self) -> None:
        if not self.action_id or not self.symbol:
            raise ValueError("action_id and symbol must be non-empty")
        if not isinstance(self.ex_date, date):
            raise TypeError(f"ex_date must be a date, got {self.ex_date!r}")
        if self.cash_credited < 0:
            raise ValueError(
                f"cash_credited must be non-negative: {self.cash_credited}"
            )
        if isinstance(self.shares_added, bool) or not isinstance(
            self.shares_added, int
        ):
            raise TypeError(f"shares_added must be an int, got {self.shares_added!r}")
        if self.shares_added < 0:
            raise ValueError(
                f"shares_added must be non-negative: {self.shares_added}"
            )
