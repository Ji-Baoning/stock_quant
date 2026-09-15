"""Deterministic row-level cleaning primitives and the normalized result type.

Cleaning trims strings, parses declared dates and numbers, normalizes codes and
converts documented units. It never interpolates prices, forward-fills missing
rows, averages suppliers, infers suspensions, or repairs illegal OHLC values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

REASON_INVALID_SYMBOL = "invalid_symbol"
REASON_INVALID_TRADE_DATE = "invalid_trade_date"
REASON_INVALID_NUMBER = "invalid_number"
REASON_INVALID_VOLUME = "invalid_volume"

RULE_DUPLICATE_ROW_COLLAPSE = "duplicate_row_collapse"

_COMPACT_DATE = re.compile(r"^\d{8}$")


@dataclass(frozen=True)
class CleanResult:
    """The outcome of normalizing one supplier-native frame.

    ``valid`` holds canonical rows that parsed cleanly, ``rejected`` holds rows
    that could not be normalized (original values preserved, with a ``reason``
    column appended), and ``audit`` records information-losing cleaning events
    such as collapsing identical duplicate rows.
    """

    valid: pd.DataFrame
    rejected: pd.DataFrame
    audit: pd.DataFrame


def parse_trade_date(value: object) -> date | None:
    """Parse a timezone-free trade date, or return ``None`` when invalid."""
    if value is None:
        return None
    # ``pd.NaT`` is a ``datetime`` instance and ``NaT.date()`` is still ``NaT``,
    # so the missing-value guard must run *before* the date branches -- behind
    # them it never sees a ``NaT`` and the helper leaks one where its callers
    # were promised ``None``.
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if _COMPACT_DATE.fullmatch(text):
        parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    else:
        parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def parse_number(value: object) -> float | None:
    """Parse a declared decimal price/amount, or return ``None`` when invalid."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_shares(value: object) -> int | None:
    """Parse a whole share count, or return ``None`` when not representable.

    Canonical ``volume`` is an integer share count, so a fractional value cannot
    be faithfully represented and is treated as invalid rather than rounded.
    """
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    else:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    if number != number.to_integral_value():
        return None
    return int(number)
