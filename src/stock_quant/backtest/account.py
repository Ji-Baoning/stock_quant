"""T+1 cash account with whole-lot positions and append-only ledgers (Task 9).

``Account`` is a pure, replayable state machine over an ordered stream of
``Order`` records and ``Fill`` records: cash is initial cash plus the sum of
each applied fill's ``cash_delta``; holdings are lots opened by buys and
consumed FIFO by sells.  A lot bought on date ``D`` carries
``available_date = next trading day after D``, and the account decides
sellability solely by ``available_date <= fill.trade_date`` -- so T+1 falls out
naturally and the account never needs a calendar to answer "may I sell".
Duplicate ``fill_id`` and cash overdrafts raise instead of corrupting state;
ledgers are append-only and ``state()`` is a byte-identical function of the
applied fill log.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from stock_quant.backtest.models import (
    BUY,
    SELL,
    CashLedgerEntry,
    Fill,
    FillLedgerEntry,
    Order,
    OrderLedgerEntry,
    PositionLedgerEntry,
    PositionLot,
    as_decimal,
)
from stock_quant.data_model.calendar import CalendarBoundaryError, TradingCalendar


class AccountError(Exception):
    """Base class for account invariant violations."""


class DuplicateFillError(AccountError):
    """A fill with an already-applied ``fill_id`` was applied again."""


class CashShortfallError(AccountError):
    """Applying a fill would push cash below zero."""


class InsufficientSellableQuantity(AccountError):
    """A sell exceeds the lots whose ``available_date`` has passed."""

    def __init__(self, symbol: str, requested: int, available: int) -> None:
        super().__init__(
            f"insufficient sellable quantity for {symbol}: requested {requested} "
            f"shares but only {available} are T+1 available"
        )
        self.symbol = symbol
        self.requested = requested
        self.available = available


class Account:
    """A cash ledger with T+1 whole-lot holdings.

    ``calendar`` is required only to stamp the next trading day as a buy lot's
    ``available_date``; sellability itself is decided from the stamped dates.
    """

    def __init__(
        self,
        initial_cash: object,
        *,
        calendar: TradingCalendar | None = None,
    ) -> None:
        cash = as_decimal(initial_cash)
        if cash < 0:
            raise ValueError(f"initial_cash must be non-negative: {cash}")
        self._calendar = calendar
        self._cash = cash
        self._initial_cash = cash
        self._lots: list[PositionLot] = []
        self._cash_entries: list[CashLedgerEntry] = []
        self._order_entries: list[OrderLedgerEntry] = []
        self._fill_entries: list[FillLedgerEntry] = []
        self._position_entries: list[PositionLedgerEntry] = []
        self._seen_fill_ids: set[str] = set()
        self._append_cash("initial", cash, cash, "initial cash")

    # ------------------------------------------------------------------ #
    # Public account surface
    # ------------------------------------------------------------------ #

    @property
    def initial_cash(self) -> Decimal:
        return self._initial_cash

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def lots(self) -> tuple[PositionLot, ...]:
        return tuple(self._lots)

    @property
    def cash_ledger(self) -> tuple[CashLedgerEntry, ...]:
        return tuple(self._cash_entries)

    @property
    def order_ledger(self) -> tuple[OrderLedgerEntry, ...]:
        return tuple(self._order_entries)

    @property
    def fill_ledger(self) -> tuple[FillLedgerEntry, ...]:
        return tuple(self._fill_entries)

    @property
    def position_ledger(self) -> tuple[PositionLedgerEntry, ...]:
        return tuple(self._position_entries)

    def sellable_quantity(self, symbol: str, trade_date) -> int:
        """Shares of ``symbol`` whose lots are T+1 available on ``trade_date``."""
        return sum(
            lot.quantity
            for lot in self._lots
            if lot.symbol == symbol and self._is_sellable(lot, trade_date)
        )

    def position_quantity(self, symbol: str) -> int:
        """All shares of ``symbol`` held, whether or not yet sellable."""
        return sum(lot.quantity for lot in self._lots if lot.symbol == symbol)

    def state(self) -> tuple:
        """A canonical snapshot; equal iff two accounts are byte-identical."""
        return (
            self._cash,
            self.lots,
            self.order_ledger,
            self.fill_ledger,
            self.cash_ledger,
            self.position_ledger,
        )

    # ------------------------------------------------------------------ #
    # Order and fill transitions
    # ------------------------------------------------------------------ #

    def record_order(self, order: Order) -> None:
        """Append an order attempt to the order ledger (audit only)."""
        if not isinstance(order, Order):
            raise TypeError(
                f"record_order expects an Order, got {type(order).__name__}"
            )
        self._order_entries.append(
            OrderLedgerEntry(seq=len(self._order_entries), order=order)
        )

    def apply_fill(self, fill: Fill) -> None:
        """Apply one fill to cash and holdings, guarding every invariant."""
        if not isinstance(fill, Fill):
            raise TypeError(f"apply_fill expects a Fill, got {type(fill).__name__}")
        if fill.fill_id in self._seen_fill_ids:
            raise DuplicateFillError(
                f"fill {fill.fill_id!r} has already been applied to this account"
            )
        if fill.side == BUY:
            self._open_position(fill)
        elif fill.side == SELL:
            self._consume_position(fill)
        else:  # pragma: no cover - Fill already validates side
            raise AccountError(f"unsupported fill side: {fill.side!r}")
        self._seen_fill_ids.add(fill.fill_id)
        self._fill_entries.append(
            FillLedgerEntry(seq=len(self._fill_entries), fill=fill)
        )

    # ------------------------------------------------------------------ #
    # Transitions
    # ------------------------------------------------------------------ #

    def _open_position(self, fill: Fill) -> None:
        new_cash = self._cash + fill.cash_delta
        if new_cash < 0:
            raise CashShortfallError(
                f"buy of {fill.quantity} {fill.symbol} on "
                f"{fill.trade_date.isoformat()} would draw cash to {new_cash}"
            )
        available = self._next_available_date(fill)
        cost_basis = fill.gross + fill.commission
        lot = PositionLot(
            symbol=fill.symbol,
            buy_date=fill.trade_date,
            quantity=fill.quantity,
            cost_basis=cost_basis,
            available_date=available,
        )
        self._cash = new_cash
        self._lots.append(lot)
        self._position_entries.append(
            PositionLedgerEntry(
                seq=len(self._position_entries),
                kind="OPEN",
                symbol=fill.symbol,
                buy_date=fill.trade_date,
                quantity=fill.quantity,
                cost_basis=cost_basis,
                available_date=available,
                fill_id=fill.fill_id,
            )
        )
        self._append_cash("fill", fill.cash_delta, new_cash, f"buy {fill.symbol}")

    def _consume_position(self, fill: Fill) -> None:
        remaining = fill.quantity
        consumed: list[tuple[PositionLot, int, Decimal]] = []
        new_lots: list[PositionLot] = []
        sellable_total = 0
        for lot in self._lots:
            if lot.symbol != fill.symbol:
                new_lots.append(lot)
                continue
            if not self._is_sellable(lot, fill.trade_date):
                new_lots.append(lot)
                continue
            sellable_total += lot.quantity
            if remaining <= 0:
                new_lots.append(lot)
                continue
            take = min(lot.quantity, remaining)
            basis_consumed = (lot.cost_basis * take) / lot.quantity
            consumed.append((lot, take, basis_consumed))
            remaining -= take
            if take < lot.quantity:
                new_lots.append(
                    replace(
                        lot,
                        quantity=lot.quantity - take,
                        cost_basis=lot.cost_basis - basis_consumed,
                    )
                )
        if remaining > 0:
            raise InsufficientSellableQuantity(
                symbol=fill.symbol, requested=fill.quantity, available=sellable_total
            )
        new_cash = self._cash + fill.cash_delta
        if new_cash < 0:
            raise CashShortfallError(
                f"sell of {fill.quantity} {fill.symbol} on "
                f"{fill.trade_date.isoformat()} would draw cash to {new_cash}"
            )
        self._cash = new_cash
        self._lots = new_lots
        for lot, take, basis_consumed in consumed:
            self._position_entries.append(
                PositionLedgerEntry(
                    seq=len(self._position_entries),
                    kind="CONSUME",
                    symbol=fill.symbol,
                    buy_date=lot.buy_date,
                    quantity=-take,
                    cost_basis=-basis_consumed,
                    available_date=lot.available_date,
                    fill_id=fill.fill_id,
                )
            )
        self._append_cash("fill", fill.cash_delta, new_cash, f"sell {fill.symbol}")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _next_available_date(self, fill: Fill) -> object:
        if self._calendar is None:
            raise AccountError(
                "buying requires a trading calendar so the new lot can be stamped "
                "with its T+1 available_date"
            )
        try:
            return self._calendar.next_trading_day(fill.trade_date)
        except CalendarBoundaryError:
            return None

    @staticmethod
    def _is_sellable(lot: PositionLot, trade_date) -> bool:
        return lot.available_date is not None and lot.available_date <= trade_date

    def _append_cash(
        self, kind: str, amount: Decimal, balance: Decimal, note: str
    ) -> None:
        self._cash_entries.append(
            CashLedgerEntry(
                seq=len(self._cash_entries),
                kind=kind,
                amount=amount,
                balance=balance,
                note=note,
            )
        )
