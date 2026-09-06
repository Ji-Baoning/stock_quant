"""Corporate-action bookkeeping onto a T+1 account (Task 10).

``apply_corporate_action`` books one *implemented, supported* action onto an
``Account`` that holds the name on the action's ``ex_date``.  Booking happens
on the ex-date before the open:

* the pre-tax cash dividend is credited against the record-date holding
  quantity -- the shares still held whose lots have ``buy_date <= record_date``;
* bonus (送股) and capitalization (转增) ratios increase the share count by
  adding a zero-cost lot on the ex-date (an A-share odd-lot residual is legal,
  and sells consume whole lots FIFO across lots as usual).

Re-applying the same ``action_id`` is a no-op guarded by a unique-action-id
check (one ledger entry per unique action id).  Rights issues, mergers,
conversions, incomplete or cross-source-conflicted actions touching a *held*
name raise :class:`UnsupportedCorporateAction` and abort that scenario;
actions for names the account does not hold never mutate it.

Dividends are booked pre-tax and never change cost basis; bonus/capitalization
shares are booked at zero cost with the lot's cost basis left unchanged, which
reproduces the standard A-share treatment that the *unadjusted* price series
falls on the ex-date to keep total equity conserved (the engine values at the
unadjusted close and the corporate action only adjusts cash and share count).
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Mapping

from stock_quant.backtest.account import Account
from stock_quant.backtest.models import CENT, CorporateActionLedgerEntry

#: Canonical implemented-action columns the engine consumes (schemas.py).
CORPORATE_ACTION_COLUMNS = (
    "symbol",
    "announcement_date",
    "record_date",
    "ex_date",
    "cash_dividend_per_share",
    "bonus_share_ratio",
    "capitalization_ratio",
    "rights_issue_ratio",
    "rights_issue_price",
    "source",
    "status",
)

STATUS_IMPLEMENTED = "implemented"

#: Separator in the deterministic action id ``{symbol}#{ex_date}``.
ACTION_ID_SEP = "#"


class UnsupportedCorporateAction(ValueError):
    """A corporate action the engine cannot book touches a held name.

    Rights issues, mergers, conversions, incomplete plans and cross-source
    conflicts are quarantined upstream and must never reach a holding-period
    backtest; this exception aborts the scenario if one still does.
    """


def action_id_of(symbol: str, ex_date) -> str:
    """The deterministic unique action id: ``{symbol}#{ex_date:%Y-%m-%d}``."""
    return f"{symbol}{ACTION_ID_SEP}{_as_date(ex_date).isoformat()}"


def apply_corporate_action(
    account: Account, action: Mapping[str, object]
) -> CorporateActionLedgerEntry | None:
    """Book ``action`` on ``account``; returns the ledger entry or ``None``.

    ``None`` means the action was a no-op: the name is not held, or the action
    id was already booked with identical facts (idempotent re-application).
    """
    if not isinstance(account, Account):
        raise TypeError(
            f"account must be an Account, got {type(account).__name__}"
        )
    if not isinstance(action, Mapping):
        raise TypeError(f"action must be a mapping/row, got {type(action).__name__}")
    symbol = _required_text(action, "symbol")
    ex_date = _as_date(_field(action, "ex_date"))
    record_date = _as_date(_field(action, "record_date"))
    action_id = action_id_of(symbol, ex_date)

    if account.position_quantity(symbol) == 0:
        return None  # the account does not hold the name: nothing to book

    status = _text(_field(action, "status"))
    if status != STATUS_IMPLEMENTED:
        raise UnsupportedCorporateAction(
            f"corporate action {action_id} is {status!r}, not implemented; "
            f"holding-period backtests must not book it"
        )
    if record_date is None or ex_date is None:
        raise UnsupportedCorporateAction(
            f"corporate action {action_id} is incomplete (missing record/ex date)"
        )
    if _ratio(_field(action, "rights_issue_ratio")) > 0:
        raise UnsupportedCorporateAction(
            f"corporate action {action_id} is a rights issue, which a "
            f"holding-period backtest cannot book"
        )

    cash_per_share = _ratio(_field(action, "cash_dividend_per_share"))
    bonus = _ratio(_field(action, "bonus_share_ratio"))
    capitalization = _ratio(_field(action, "capitalization_ratio"))

    # Idempotency: one ledger entry per unique action id.  ``action_id`` fixes
    # the (symbol, ex_date) event and upstream normalization quarantines any
    # conflicting second source for that key, so a duplicate application here is
    # always refused as a no-op.  (Cross-source fact conflicts on a held name
    # are detected by the engine's readiness check before any account mutation.)
    if any(booked.action_id == action_id for booked in account.action_ledger):
        return None

    # Cash dividend against the record-date holding; then share increases from
    # bonus/capitalization ratios against the whole ex-date holding.
    cash_amount = _dividend_cash(account, symbol, record_date, cash_per_share)
    shares_added = _bonus_shares(account, symbol, bonus, capitalization)

    note = _text(_field(action, "source")) or "corporate_action"
    if cash_amount > 0:
        account.credit_cash(cash_amount, note=f"dividend {symbol}")
    if shares_added > 0:
        account.increase_position(
            symbol,
            shares_added,
            buy_date=ex_date,
            fill_id=action_id,
            note=f"{bonus + capitalization} ratio credit",
        )
    entry = CorporateActionLedgerEntry(
        seq=len(account.action_ledger),
        action_id=action_id,
        symbol=symbol,
        ex_date=ex_date,
        record_date=record_date,
        cash_credited=cash_amount,
        shares_added=shares_added,
        note=note,
    )
    account.record_corporate_action(entry)
    return entry


# --------------------------------------------------------------------------- #
# Booking helpers
# --------------------------------------------------------------------------- #


def _dividend_cash(
    account: Account, symbol: str, record_date: date, cash_per_share: Decimal
) -> Decimal:
    """Pre-tax cash dividend on the still-held record-date lots, to the cent."""
    record_quantity = sum(
        lot.quantity
        for lot in account.lots
        if lot.symbol == symbol and lot.buy_date <= record_date
    )
    return (Decimal(record_quantity) * cash_per_share).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def _bonus_shares(
    account: Account, symbol: str, bonus: Decimal, capitalization: Decimal
) -> int:
    """Whole shares added by bonus/capitalization on the ex-date holding."""
    held = Decimal(account.position_quantity(symbol))
    return int(
        (held * (bonus + capitalization)).to_integral_value(rounding=ROUND_HALF_UP)
    )


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value.strip()[:10])
    raise TypeError(f"cannot interpret {value!r} as a trading date")


def _field(action: Mapping[str, object], key: str) -> object:
    value = action.get(key)  # type: ignore[arg-type]
    return None if value is None else value


def _required_text(action: Mapping[str, object], key: str) -> str:
    value = _field(action, key)
    if value is None or not str(value).strip():
        raise ValueError(f"action is missing a {key!r} value")
    return str(value).strip()


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def _ratio(value: object) -> Decimal:
    """Convert a per-share ratio/amount to a non-negative ``Decimal`` (0 if empty)."""
    if value is None:
        return Decimal("0")
    if isinstance(value, bool):
        return Decimal("1") if value else Decimal("0")
    if isinstance(value, float) and math.isnan(value):
        return Decimal("0")
    if isinstance(value, Decimal):
        converted = value
    else:
        try:
            converted = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return Decimal("0")
    if converted < 0:
        raise ValueError(f"corporate-action amount/ratio must be non-negative: {value}")
    return converted
