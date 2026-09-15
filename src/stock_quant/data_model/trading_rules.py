"""Effective-dated A股 price-limit and IPO no-limit rule book (Task 5).

``TradingRuleBook`` is a pure reader over ``configs/trading_rules.yml``: the
schedule is versioned configuration, never fetched from the network at runtime
(design spec §9, §19). Execution code does not embed any percentage; instead it
asks the book ``limit_rate`` (round to ¥0.01, half-up) and ``price_limits``, and
blocks rather than guesses whenever a security/date/status combination is not
covered by the book (:class:`UncoveredRuleError`).

Registration-based IPO regimes (STAR from 2019-07-22, ChiNext from 2020-08-24,
main boards from 2023-04-10) mark the first five trading sessions as having no
daily limit; the caller supplies the ``listed_sessions`` ordinal for the trade
date and the book counts against the session window in effect on that date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable

from stock_quant.safe_yaml import read_yaml

REASON_BUY_AT_UPPER_LIMIT = "buy_at_upper_limit"
REASON_SELL_AT_LOWER_LIMIT = "sell_at_lower_limit"

_NORMAL_STATUS = "NORMAL"
_PRICE_TICK = Decimal("0.01")
_CANONICAL_SYMBOL = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")


class UncoveredRuleError(LookupError):
    """No rule covers the security/date/status queried; execution must block."""


@dataclass(frozen=True)
class PriceLimits:
    """The effective band for one security/date/status under one reference price."""

    no_limit: bool
    upper: Decimal | None
    lower: Decimal | None

    def block_reason(self, side: str, price: object) -> str | None:
        """Why an order at ``price`` is rejected, or ``None`` when allowed.

        A-share exchanges let members queue orders at the limit price but not
        buy at/above the upper limit or sell at/below the lower limit once the
        band is locked, so the engine conservatively rejects those orders.
        """
        if self.no_limit:
            return None
        converted = _as_decimal(price)
        if side == "buy" and self.upper is not None and converted >= self.upper:
            return REASON_BUY_AT_UPPER_LIMIT
        if side == "sell" and self.lower is not None and converted <= self.lower:
            return REASON_SELL_AT_LOWER_LIMIT
        return None


@dataclass(frozen=True)
class _LimitRule:
    boards: tuple[str, ...]
    statuses: tuple[str, ...]
    effective_from: date
    rate: Decimal

    def covers(self, board: str, status: str, day: date) -> bool:
        return (
            board in self.boards
            and status in self.statuses
            and day >= self.effective_from
        )


@dataclass(frozen=True)
class _NoLimitRule:
    boards: tuple[str, ...]
    effective_from: date
    sessions: int

    def covers(self, board: str, day: date) -> bool:
        return board in self.boards and day >= self.effective_from


class TradingRuleBook:
    """Effective-dated price limits and IPO no-limit windows from YAML config."""

    def __init__(
        self,
        *,
        price_tick: Decimal,
        limit_rules: Iterable[_LimitRule],
        no_limit_rules: Iterable[_NoLimitRule],
    ) -> None:
        if price_tick <= 0:
            raise ValueError(f"price_tick must be positive: {price_tick}")
        self._price_tick = price_tick
        self._limit_rules = tuple(limit_rules)
        self._no_limit_rules = tuple(no_limit_rules)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "TradingRuleBook":
        """Parse a ``configs/trading_rules.yml`` document into a rule book."""
        path = Path(path)
        document = read_yaml(path)
        if not isinstance(document, dict):
            raise ValueError(f"trading-rules yaml {path} must be a mapping")
        try:
            limit_rules = [
                _parse_limit_rule(raw) for raw in document["price_limits"]
            ]
            no_limit_rules = [
                _parse_no_limit_rule(raw)
                for raw in document["no_limit_first_sessions"]
            ]
        except KeyError as exc:
            message = f"trading-rules yaml {path} is missing {exc.args[0]!r}"
            raise ValueError(message) from exc
        except TypeError as exc:
            message = f"trading-rules yaml {path} has a malformed rule list"
            raise ValueError(message) from exc
        return cls(
            price_tick=_as_decimal(document["price_tick"]),
            limit_rules=limit_rules,
            no_limit_rules=no_limit_rules,
        )

    def limit_rate(
        self, symbol: str, day: date, *, status: str = _NORMAL_STATUS
    ) -> Decimal:
        """Fractional daily limit (e.g. ``0.10``) for ``symbol`` on ``day``."""
        board = board_of_symbol(symbol)
        normalized_status = status.upper()
        matching = [
            rule
            for rule in self._limit_rules
            if rule.covers(board, normalized_status, day)
        ]
        if not matching:
            raise UncoveredRuleError(
                f"no price-limit rule covers {symbol} ({board}) with status "
                f"{normalized_status!r} on {day.isoformat()}"
            )
        return max(matching, key=lambda rule: rule.effective_from).rate

    def no_limit_first_sessions(self, symbol: str, day: date) -> int:
        """How many first-session days carry no daily limit for ``symbol`` on ``day``.

        Zero when no registration-based IPO regime is in force for the symbol's
        board on that date.
        """
        board = board_of_symbol(symbol)
        matching = [rule for rule in self._no_limit_rules if rule.covers(board, day)]
        if not matching:
            return 0
        return max(matching, key=lambda rule: rule.effective_from).sessions

    def price_limits(
        self,
        symbol: str,
        day: date,
        reference_price: object,
        *,
        status: str = _NORMAL_STATUS,
        listed_sessions: int | None = None,
    ) -> PriceLimits:
        """The effective band around ``reference_price`` for one session.

        ``listed_sessions`` is the ordinal of the trade date counted from the
        security's listing (1 on its first session); within an in-force IPO
        no-limit window the band is open. Otherwise the band is the applicable
        limit rounded half-up to the configured price tick.
        """
        reference = _as_decimal(reference_price)
        if reference <= 0:
            raise ValueError(f"reference price must be positive: {reference}")
        if listed_sessions is not None:
            if listed_sessions <= 0:
                raise ValueError(f"listed_sessions must be positive: {listed_sessions}")
            if listed_sessions <= self.no_limit_first_sessions(symbol, day):
                return PriceLimits(no_limit=True, upper=None, lower=None)
        rate = self.limit_rate(symbol, day, status=status)
        upper = self._round_to_tick(reference * (Decimal("1") + rate))
        lower = self._round_to_tick(reference * (Decimal("1") - rate))
        return PriceLimits(no_limit=False, upper=upper, lower=lower)

    def _round_to_tick(self, value: Decimal) -> Decimal:
        return value.quantize(self._price_tick, rounding=ROUND_HALF_UP)


def board_of_symbol(symbol: str) -> str:
    """Resolve an A股 symbol to its board by six-digit prefix + exchange suffix.

    Raises :class:`UncoveredRuleError` for anything not resolvable, so callers
    never assume a board for an unrecognized code.
    """
    match = _CANONICAL_SYMBOL.fullmatch(symbol)
    if not match:
        raise UncoveredRuleError(f"unrecognized symbol {symbol!r}")
    code, exchange = match.group(1), match.group(2)
    if exchange == "BJ":
        return "bj"
    if exchange == "SH":
        if code.startswith(("600", "601", "603", "605")):
            return "sh_main"
        if code.startswith("688"):
            return "star"
    elif exchange == "SZ":
        if code.startswith(("000", "001", "002", "003")):
            return "sz_main"
        # 302xxx is the newer ChiNext series (e.g. 302132.SZ, listed 2025).
        if code.startswith(("300", "301", "302")):
            return "chinext"
    raise UncoveredRuleError(f"unrecognized symbol {symbol!r}")


def _parse_limit_rule(raw: Any) -> _LimitRule:
    if not isinstance(raw, dict):
        raise ValueError("each price_limits entry must be a mapping")
    return _LimitRule(
        boards=tuple(_require_str_list(raw, "boards")),
        statuses=tuple(_as_str_list(raw["status"])),
        effective_from=_require_date(raw, "effective_from"),
        rate=_as_decimal(raw["rate"]),
    )


def _parse_no_limit_rule(raw: Any) -> _NoLimitRule:
    if not isinstance(raw, dict):
        raise ValueError("each no_limit_first_sessions entry must be a mapping")
    sessions = raw["sessions"]
    if not isinstance(sessions, int) or sessions <= 0:
        raise ValueError(f"sessions must be a positive integer: {sessions!r}")
    return _NoLimitRule(
        boards=tuple(_require_str_list(raw, "boards")),
        effective_from=_require_date(raw, "effective_from"),
        sessions=sessions,
    )


def _require_str_list(mapping: dict[str, Any], key: str) -> list[str]:
    if key not in mapping:
        raise KeyError(key)
    return _as_str_list(mapping[key])


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise ValueError(f"expected a string or list of strings, got {value!r}")


def _require_date(mapping: dict[str, Any], key: str) -> date:
    if key not in mapping:
        raise KeyError(key)
    value = mapping[key]
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{key} is not a date: {value!r}") from exc


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
