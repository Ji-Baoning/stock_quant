"""Per-symbol corporate-action coverage evidence (trust-gate Task 1).

Empty corporate-action facts are never trusted by default: a dataset is only
allowed to conclude "nothing happened" for a symbol/window when every
applicable supplier endpoint answered the check explicitly.  This module owns
that vocabulary and the deterministic evidence layout:

- ``CoverageStatus`` -- ``VERIFIED`` (reconciled facts exist), ``VERIFIED_EMPTY``
  (every requested endpoint succeeded and returned no events) or ``UNTRUSTED``.
- ``CoverageReason`` -- the six stable audit codes (source fetch failed, source
  not requested, coverage incomplete, facts incomplete, cross-source conflict,
  unsupported action).
- ``coverage_record`` / ``coverage_frame`` -- render one row per
  symbol/window/evidence into the standardized
  ``CORPORATE_ACTION_COVERAGE_COLUMNS`` layout (dates and ``checked_at`` are
  co-erced deterministically and rows are sorted by symbol then window).

``sources`` and ``snapshot_hashes`` are deterministic JSON: ``sources`` lists
one ``{"endpoint", "outcome"}`` entry per requested endpoint and
``snapshot_hashes`` maps each endpoint that answered successfully to the raw
snapshot's content hash, so every published verdict is auditable back to the
supplier bytes it was based on.
"""

from __future__ import annotations

import json
from datetime import date
from enum import Enum
from typing import Any, Mapping, Sequence

import pandas as pd

from stock_quant.data_model.schemas import CORPORATE_ACTION_COVERAGE_COLUMNS

# Canonical per-endpoint outcomes recorded inside the ``sources`` column.
OUTCOME_FAILED = "failed"
OUTCOME_SUCCESS_EMPTY = "success_empty"
OUTCOME_SUCCESS_EVENTS = "success_with_events"
OUTCOME_NOT_REQUESTED = "not_requested"


class CoverageStatus(str, Enum):
    VERIFIED = "VERIFIED"
    VERIFIED_EMPTY = "VERIFIED_EMPTY"
    UNTRUSTED = "UNTRUSTED"


class CoverageReason(str, Enum):
    SOURCE_FETCH_FAILED = "SOURCE_FETCH_FAILED"
    SOURCE_NOT_REQUESTED = "SOURCE_NOT_REQUESTED"
    COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"
    FACTS_INCOMPLETE = "FACTS_INCOMPLETE"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"


def coverage_record(
    symbol: str,
    window_start: date,
    window_end: date,
    status: CoverageStatus | str,
    reason: CoverageReason | str | None = None,
    sources: str | Sequence[Mapping[str, Any]] | None = None,
    snapshot_hashes: str | Mapping[str, str] | None = None,
    checked_at: Any = None,
) -> dict[str, Any]:
    """Return one coverage row holding the eight canonical columns.

    ``status``/``reason`` may be the enums or their code strings; ``sources``
    and ``snapshot_hashes`` may be pre-encoded JSON strings or structured
    values that are rendered deterministically.
    """
    return {
        "symbol": str(symbol),
        "window_start": window_start,
        "window_end": window_end,
        "status": _code_text(status),
        "reason": _code_text(reason),
        "sources": _encode_sources(sources),
        "snapshot_hashes": _encode_snapshot_hashes(snapshot_hashes),
        "checked_at": checked_at,
    }


def coverage_frame(records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Build the standardized coverage frame from coverage rows.

    Date columns are co-erced to ``datetime64`` (Arrow casts them to ``date32``
    at publish) and ``checked_at`` to timezone-aware UTC; rows are sorted by
    symbol then window so identical inputs always produce identical bytes.
    """
    frame = pd.DataFrame(records, columns=CORPORATE_ACTION_COVERAGE_COLUMNS)
    frame["window_start"] = pd.to_datetime(frame["window_start"], errors="coerce")
    frame["window_end"] = pd.to_datetime(frame["window_end"], errors="coerce")
    frame["checked_at"] = pd.to_datetime(
        frame["checked_at"], utc=True, errors="coerce"
    )
    frame = (
        frame.sort_values(
            ["symbol", "window_start", "window_end"], kind="stable"
        )
        .reset_index(drop=True)
    )
    return frame[CORPORATE_ACTION_COVERAGE_COLUMNS]


def _code_text(value: Any) -> str | None:
    """Render an enum member or code string as its stable code text."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _encode_sources(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(list(value), ensure_ascii=False, sort_keys=True)


def _encode_snapshot_hashes(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
