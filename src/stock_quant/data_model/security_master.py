"""Security-master listing-fact coverage evidence (point-in-time task).

Formal research requires one ``security_master_coverage`` row per universe
symbol: row presence *is* VERIFIED — the listing facts were applied from a real
tushare ``stock_basic`` snapshot during ``data update``.  There is no
status/reason vocabulary here (unlike the corporate-action coverage table) and
no sentinel-date detection; an empty or absent table simply means every symbol
is unverified.

- ``ListStatus`` — ``L`` (listed) / ``D`` (delisted) / ``P`` (suspended) /
  ``NOT_APPLIED`` (bootstrap seed, real facts not yet applied).
- ``master_coverage_record`` / ``master_coverage_frame`` — render rows into the
  standardized ``SECURITY_MASTER_COVERAGE_COLUMNS`` layout (dates and
  ``checked_at`` co-erced deterministically, rows sorted by symbol).
- ``missing_master_coverage_symbols`` — the sorted universe symbols with no row,
  used by the RESEARCH freeze gate.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from stock_quant.data_model.schemas import SECURITY_MASTER_COVERAGE_COLUMNS

#: The only source this phase records security-master listing facts from.
MASTER_SOURCE_STOCK_BASIC = "tushare.stock_basic"


class ListStatus(str, Enum):
    L = "L"
    D = "D"
    P = "P"
    NOT_APPLIED = "NOT_APPLIED"


def master_coverage_record(
    symbol: str,
    *,
    list_date: Any = None,
    delist_date: Any = None,
    list_status: ListStatus | str | None = None,
    source: str = MASTER_SOURCE_STOCK_BASIC,
    snapshot_sha256: str | None = None,
    sdk_version: str | None = None,
    checked_at: Any = None,
) -> dict[str, Any]:
    """Return one coverage row holding the eight canonical columns."""
    return {
        "symbol": str(symbol),
        "list_date": list_date,
        "delist_date": delist_date,
        "list_status": _code_text(list_status) or ListStatus.L.value,
        "source": source or MASTER_SOURCE_STOCK_BASIC,
        "snapshot_sha256": snapshot_sha256,
        "sdk_version": sdk_version,
        "checked_at": checked_at,
    }


def master_coverage_frame(
    records: Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    """Build the standardized coverage frame from coverage rows.

    Date columns are co-erced to ``datetime64`` (Arrow casts them to ``date32``
    at publish) and ``checked_at`` to timezone-aware UTC; rows are sorted by
    symbol so identical inputs always produce identical bytes.
    """
    frame = pd.DataFrame(records, columns=SECURITY_MASTER_COVERAGE_COLUMNS)
    frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    frame["delist_date"] = pd.to_datetime(frame["delist_date"], errors="coerce")
    frame["checked_at"] = pd.to_datetime(
        frame["checked_at"], utc=True, errors="coerce"
    )
    frame = frame.sort_values(["symbol"], kind="stable").reset_index(drop=True)
    return frame[SECURITY_MASTER_COVERAGE_COLUMNS]


def missing_master_coverage_symbols(
    symbols: Iterable[str],
    coverage: pd.DataFrame | None,
) -> list[str]:
    """Return the sorted universe symbols with no coverage row.

    ``coverage`` ``None`` (older dataset without the table), a non-frame or an
    empty frame reads as *no* evidence, so every symbol is unverified.
    """
    if coverage is None or not isinstance(coverage, pd.DataFrame):
        return sorted({str(item) for item in symbols})
    covered = set(str(value) for value in coverage.get("symbol", []))
    return sorted({str(item) for item in symbols} - covered)


def _code_text(value: Any) -> str | None:
    """Render an enum member or code string as its stable code text."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    return str(value)
