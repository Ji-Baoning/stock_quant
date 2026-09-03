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
    CorporateActionLedgerEntry,
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


class DuplicateCorporateActionError(AccountError):
    """A corporate action with an already-applied ``action_id`` was recorded."""

    def __init__(self, action_id: str) -> None:
        super().__init__(f"corporate action {action_id!r} is already booked")
        self.action_id = action_id


class Account:
    """A cash ledger with T+1 whole-lot holdings and corporate-action events.

    ``calendar`` is required only to stamp the next trading day as a buy lot's
    ``available_date``; sellability itself is decided from the stamped dates.

    Cash and holdings are mutated by ``apply_fill`` (an exchange trade) and by
    the two corporate-action primitives ``credit_cash`` and
    ``increase_position`` (Task 10: a cash dividend and a bonus/capitalization
    share credit booked on an ex-date before the open).  Every such event is
    appended to an action ledger so ``state()`` stays a byte-identical function
    of the applied event log and re-running the same log reproduces the account.
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
        self._action_entries: list[CorporateActionLedgerEntry] = []
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

    @property
    def action_ledger(self) -> tuple[CorporateActionLedgerEntry, ...]:
        return tuple(self._action_entries)

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
            self.action_ledger,
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

    # ------------------------------------------------------------------ #
    # Corporate-action transitions (Task 10)
    # ------------------------------------------------------------------ #

    def credit_cash(self, amount: object, *, note: str = "") -> None:
        """Credit pre-tax cash (e.g. a cash dividend) without an exchange fill."""
        cash = as_decimal(amount)
        if cash <= 0:
            raise ValueError(f"credit amount must be positive: {cash}")
        balance = self._cash + cash
        self._cash = balance
        self._append_cash("credit", cash, balance, note or "cash credit")

    def increase_position(
        self,
        symbol: str,
        shares: int,
        *,
        buy_date: object,
        fill_id: str,
        note: str = "",
    ) -> None:
        """Open a zero-cost lot of ``shares`` (a bonus/capitalization credit).

        The lot is stamped with the same T+1 ``available_date`` rule as a buy on
        ``buy_date`` (the action's ex-date); ``fill_id`` records the corporate
        action that created the shares so the position ledger stays auditable.
        """
        if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
            raise ValueError(f"shares must be a positive integer, got {shares!r}")
        if not symbol or not fill_id:
            raise ValueError("symbol and fill_id must be non-empty")
        available = self._available_after(buy_date)
        lot = PositionLot(
            symbol=symbol,
            buy_date=buy_date,
            quantity=shares,
            cost_basis=Decimal("0"),
            available_date=available,
        )
        self._lots.append(lot)
        self._position_entries.append(
            PositionLedgerEntry(
                seq=len(self._position_entries),
                kind="OPEN",
                symbol=symbol,
                buy_date=lot.buy_date,
                quantity=shares,
                cost_basis=Decimal("0"),
                available_date=available,
                fill_id=fill_id,
            )
        )

    def record_corporate_action(self, entry: CorporateActionLedgerEntry) -> None:
        """Append one booked corporate action; refuses a duplicate ``action_id``."""
        if not isinstance(entry, CorporateActionLedgerEntry):
            raise TypeError(
                "record_corporate_action expects a CorporateActionLedgerEntry, "
                f"got {type(entry).__name__}"
            )
        if any(
            existing.action_id == entry.action_id
            for existing in self._action_entries
        ):
            raise DuplicateCorporateActionError(entry.action_id)
        self._action_entries.append(entry)

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
        return self._available_after(fill.trade_date)

    def _available_after(self, day: object) -> object:
        """The first open day after ``day``, or ``None`` past the calendar end."""
        if self._calendar is None:
            raise AccountError(
                "buying requires a trading calendar so the new lot can be stamped "
                "with its T+1 available_date"
            )
        try:
            return self._calendar.next_trading_day(day)
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
