"""TDX (通达信) as a third-party arbiter for corporate-action conflicts.

ADR-007: this channel is *not* a source.  It never contributes a row, never
enters ``CORPORATE_ACTION_ENDPOINTS`` or ``_CONFIGURED_SOURCES``, and never
appears alone in ``source``.  Its only job is to pick which official filing to
adopt when CNINFO and Eastmoney disagree on the same ``(symbol, ex_date)``.

The comparison is threshold-free by construction.  TDX transports each
per-ten-share amount as a **float32** and divides by ten in double precision
(``pytdxdata``, ``protocol/commands/xdxr.py``::

    fenhong, peigujia, songzhuangu, peigu = struct.unpack("<ffff", chunk)
    rec.fenhong = fenhong / 10.0

so the values a side must reproduce are the ones *that* arithmetic produces --
not the mathematically equal double.  ``float32(1.4)`` is not ``14.0 / 10.0``,
which is why an "obvious" per-share comparison gets the wrong answer and why no
tolerance is involved on either side of the test.

``pytdxdata`` is an optional import: a third-party package deliberately kept out
of the base dependencies, reachable only when ``sources.yml`` enables ``tdx``.
It declares no runtime dependencies, but its ``Requires-Python`` is ``>=3.11``
-- one minor above this project's own ``>=3.10`` floor -- so an interpreter at
that floor cannot install it from PyPI.  A missing package is an unavailable
arbiter, never an import error at startup.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

import pandas as pd

from stock_quant.data_model.corporate_actions import (
    CONFLICT_SIDE_CNINFO,
    CONFLICT_SIDE_EASTMONEY,
    ConflictTerms,
)

#: The arbiter's name in ``confirmed_by``/``source`` values (``cninfo+tdx``).
ARBITER_NAME = "tdx"

#: Raw-snapshot endpoint label for the xdxr responses this arbiter consumed.
XDXR_ENDPOINT = "tdx_xdxr"

#: TDX's 除权除息 category.  Every other category (股本变动, 增发, ...) carries no
#: distribution terms and is therefore not arbitration evidence.
XDXR_CATEGORY_DISTRIBUTION = 1

#: Columns of the raw snapshot frame, in the order they are written.
XDXR_COLUMNS = (
    "symbol",
    "date",
    "category",
    "name",
    "fenhong",
    "songzhuangu",
    "peigu",
    "peigujia",
)

#: TDX's own divisor: it states each per-ten-share amount per share.
_PER_TEN_SCALE = 10.0

#: TDX's standard market carries SH and SZ only; a symbol outside them is not
#: arbitrated rather than guessed onto a market.
_MARKET_BY_SUFFIX = {"SH": "SH", "SZ": "SZ"}


class TdxUnavailableError(RuntimeError):
    """The TDX channel cannot be used, so no conflict may be arbitrated."""


def _tdx_float32(value: float) -> float:
    """Round ``value`` to the float32 TDX transmits, kept in a double."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _tdx_quote(per_ten: Decimal) -> float:
    """The double TDX exposes for a per-ten-share amount (its own arithmetic)."""
    return _tdx_float32(float(per_ten)) / _PER_TEN_SCALE


def _tdx_price(per_share: Decimal) -> float:
    """The double TDX exposes for a per-share amount (transport only, unscaled)."""
    return _tdx_float32(float(per_share))


def _as_date(value: object) -> date | None:
    """A plain ``date`` for an index key.

    ``datetime`` is narrowed to its date first: a snapshot read back from
    parquet yields ``Timestamp`` cells, and a ``Timestamp`` key would not match
    the ``date`` the reconciliation looks up with -- silently arbitrating
    nothing where it should have decided.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


@dataclass(frozen=True)
class TdxXdxrTerms:
    """One ex-date's distribution terms, as TDX states them.

    Every field is the double TDX's own float32 decode yields, so equality here
    is exact bit-for-bit agreement rather than agreement within a tolerance.
    """

    fenhong: float
    songzhuangu: float
    peigu: float
    peigujia: float

    def corroborates(self, terms: ConflictTerms) -> bool:
        """Whether this record states exactly ``terms``' distribution.

        送股 and 转增 share one field in TDX (``songzhuangu``), so they are
        compared as the sum they are: a conflict that turns only on how the
        total splits between the two cannot be corroborated either way.
        """
        return (
            _tdx_quote(terms.cash_per_ten) == self.fenhong
            and _tdx_quote(terms.bonus_per_ten + terms.capitalization_per_ten)
            == self.songzhuangu
            and _tdx_quote(terms.rights_per_ten) == self.peigu
            and _tdx_price(terms.rights_price_per_share) == self.peigujia
        )


def build_xdxr_frame(symbol: str, records: Sequence[Any]) -> pd.DataFrame:
    """Render one symbol's raw xdxr records into the snapshot layout.

    Every category is kept: the snapshot is the supplier's response, and the
    category-1 restriction is the arbiter's reading of it, not the record.
    """
    rows = [
        {
            "symbol": symbol,
            "date": str(record.date),
            "category": int(record.category),
            "name": str(getattr(record, "name", "")),
            "fenhong": float(record.fenhong),
            "songzhuangu": float(record.songzhuangu),
            "peigu": float(record.peigu),
            "peigujia": float(record.peigujia),
        }
        for record in records
    ]
    return pd.DataFrame(rows, columns=list(XDXR_COLUMNS))


def fetch_xdxr_frames(
    symbols: Sequence[str],
    *,
    timeout: float = 30.0,
    servers: Sequence[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch each symbol's xdxr table from the TDX channel.

    Returns one frame per symbol, in ``XDXR_COLUMNS``.  Raises
    ``TdxUnavailableError`` when the channel itself cannot be used; a single
    symbol's failure propagates and leaves the whole conflict set unarbitrated,
    which is the fail-closed direction.
    """
    return asyncio.run(_fetch_xdxr(symbols, timeout=timeout, servers=servers))


async def _fetch_xdxr(
    symbols: Sequence[str],
    *,
    timeout: float,
    servers: Sequence[str] | None,
) -> dict[str, pd.DataFrame]:
    client_type = _load_pytdxdata()
    frames: dict[str, pd.DataFrame] = {}
    kwargs: dict[str, Any] = {"timeout": timeout}
    if servers:
        kwargs["standard_servers"] = list(servers)
    async with client_type(**kwargs) as client:
        for symbol in symbols:
            records = await client.get_xdxr(_tdx_symbol(symbol))
            frames[symbol] = build_xdxr_frame(symbol, records)
    return frames


def _load_pytdxdata():
    try:
        from pytdxdata import TdxData
    except ImportError as error:  # pragma: no cover - exercised by the guard test
        raise TdxUnavailableError(
            "the tdx arbiter needs the optional pytdxdata package "
            "(pip install pytdxdata); install it or leave sources.yml "
            "tdx.enabled false"
        ) from error
    return TdxData


def _tdx_symbol(symbol: str) -> str:
    """The channel's prefixed symbol (``sz300124``) for a canonical one.

    pytdxdata 0.6.0 takes one prefixed string and rejects the suffixed
    canonical form without a roundtrip, so the market scope is enforced here:
    the standard market carries SH and SZ only, and anything else is not
    arbitrated rather than guessed onto a market.
    """
    code, _, suffix = symbol.rpartition(".")
    market = _MARKET_BY_SUFFIX.get(suffix.upper())
    if not code or market is None:
        raise TdxUnavailableError(f"TDX has no market for {symbol!r}")
    return f"{suffix.lower()}{code}"


class TdxXdxrArbiter:
    """Picks a side in a cross-source conflict from TDX's xdxr records.

    The index is built from the *same frames* that were written as raw
    snapshots, so what arbitrated a conflict is byte-for-byte what the snapshot
    holds.
    """

    name = ARBITER_NAME

    def __init__(self, index: Mapping[str, Mapping[date, TdxXdxrTerms]]) -> None:
        self._index = {symbol: dict(by_date) for symbol, by_date in index.items()}

    @classmethod
    def from_frames(cls, frames: Mapping[str, pd.DataFrame]) -> "TdxXdxrArbiter":
        """Index category-1 records by ``(symbol, ex_date)``.

        A repeated ex-date is dropped rather than resolved: which of two TDX
        rows states the event is unknowable here, and an unknown record must
        arbitrate nothing.
        """
        index: dict[str, dict[date, TdxXdxrTerms]] = {}
        for symbol, frame in frames.items():
            if frame is None or frame.empty:
                continue
            distribution = frame[frame["category"] == XDXR_CATEGORY_DISTRIBUTION]
            by_date: dict[date, TdxXdxrTerms] = {}
            for row in distribution.itertuples(index=False):
                ex_date = _as_date(row.date)
                if ex_date is None or ex_date in by_date:
                    by_date.pop(ex_date, None)
                    continue
                by_date[ex_date] = TdxXdxrTerms(
                    fenhong=float(row.fenhong),
                    songzhuangu=float(row.songzhuangu),
                    peigu=float(row.peigu),
                    peigujia=float(row.peigujia),
                )
            index[symbol] = by_date
        return cls(index)

    def arbitrate(self, cninfo: ConflictTerms, eastmoney: ConflictTerms) -> str | None:
        """Name the side TDX corroborates, or ``None`` to leave both quarantined.

        Exactly one side must match.  No record for the ex-date, both sides
        matching, and neither side matching are the same answer: no arbitration.
        """
        record = self._index.get(cninfo.symbol, {}).get(cninfo.ex_date)
        if record is None:
            return None
        cninfo_matches = record.corroborates(cninfo)
        eastmoney_matches = record.corroborates(eastmoney)
        if cninfo_matches == eastmoney_matches:
            return None
        return CONFLICT_SIDE_CNINFO if cninfo_matches else CONFLICT_SIDE_EASTMONEY
