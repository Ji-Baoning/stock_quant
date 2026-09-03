"""Cross-source corporate-action reconciliation (Task 5).

``normalize_corporate_actions`` consumes the two supplier-native corporate
action frames (CNINFO primary, Eastmoney cross-check, both fetched through the
AKShare adapter) and reconciles them into *implemented, supported* events.
Acceptance gates follow design spec §19: only implemented plans with an
announcement, record and ex date and at least one distribution component may be
booked; equal CNINFO/Eastmoney facts cross-confirm into one row; disagreements
are quarantined with ``cross_source_conflict`` and never resolved in the more
favourable direction; rights issues, mergers and conversions are tagged
``unsupported_corporate_action`` so a holding-period backtest later blocks.

Supplier-native layout

Both documented native frames report distribution amounts per ten shares
(canonical output is per share) and use the AKShare Chinese column spellings
below. Unknown layouts raise instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from stock_quant.data_model.clean import parse_trade_date

REASON_CROSS_SOURCE_CONFLICT = "cross_source_conflict"
REASON_UNSUPPORTED_CORPORATE_ACTION = "unsupported_corporate_action"
REASON_NOT_IMPLEMENTED = "not_implemented"
REASON_INCOMPLETE = "incomplete"

STATUS_IMPLEMENTED = "implemented"
STATUS_NOT_IMPLEMENTED = "not_implemented"

#: Canonical output columns shared by accepted and quarantined rows.
RECONCILED_COLUMNS = [
    "symbol",
    "announcement_date",
    "record_date",
    "ex_date",
    "cash_dividend_per_share",
    "bonus_share_ratio",
    "capitalization_ratio",
    "rights_issue_ratio",
    "rights_issue_price",
    "status",
    "confirmed_by",
]
QUARANTINE_COLUMNS = [*RECONCILED_COLUMNS, "reason"]

_PER_SHARE_SCALE = Decimal("10")
_UNSUPPORTED_KEYWORDS = ("配股", "配售", "吸收合并", "换股")
_IMPLEMENTED_MARKER = "实施"
_BOTH_SOURCES = "cninfo+eastmoney"

# Ordered native column candidates per canonical input field. The first present
# column is used; ``plan`` is optional and only needed for unsupported tagging.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("证券代码", "代码"),
    "announcement_date": ("公告日期", "最新公告日期"),
    "record_date": ("股权登记日",),
    "ex_date": ("除权除息日",),
    "cash_dividend": ("派息(税前)(元/10股)", "现金分红-现金分红比例"),
    "bonus": ("送股(股/10股)", "送转股份-送股比例"),
    "capitalization": ("转增(股/10股)", "送转股份-转股比例"),
    "progress": ("进度", "方案进度"),
    "plan": ("方案", "方案说明"),
}
_REQUIRED_FIELDS = (
    "symbol",
    "announcement_date",
    "record_date",
    "ex_date",
    "cash_dividend",
    "bonus",
    "capitalization",
    "progress",
)

_SUFFIXED_SYMBOL = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$", re.IGNORECASE)
_BARE_SYMBOL = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class CorporateActionResult:
    """Accepted (cross-confirmed/single-source) and quarantined events."""

    accepted: pd.DataFrame
    quarantined: pd.DataFrame


def normalize_corporate_actions(
    cninfo: pd.DataFrame | None,
    eastmoney: pd.DataFrame | None,
) -> CorporateActionResult:
    """Reconcile the CNINFO and Eastmoney corporate-action frames."""
    cn_candidates, cn_quarantine = _standardize_source(cninfo, "cninfo")
    em_candidates, em_quarantine = _standardize_source(eastmoney, "eastmoney")

    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = cn_quarantine + em_quarantine
    keys = set(cn_candidates) | set(em_candidates)
    for key in sorted(keys):
        cn_event = cn_candidates.get(key)
        em_event = em_candidates.get(key)
        if cn_event is not None and em_event is not None:
            if _same_facts(cn_event, em_event):
                accepted.append(_row(cn_event, confirmed_by=_BOTH_SOURCES))
            else:
                quarantined.append(
                    _row(
                        cn_event,
                        confirmed_by="cninfo",
                        reason=REASON_CROSS_SOURCE_CONFLICT,
                    )
                )
                quarantined.append(
                    _row(
                        em_event,
                        confirmed_by="eastmoney",
                        reason=REASON_CROSS_SOURCE_CONFLICT,
                    )
                )
        elif cn_event is not None:
            accepted.append(_row(cn_event, confirmed_by="cninfo"))
        elif em_event is not None:
            accepted.append(_row(em_event, confirmed_by="eastmoney"))

    return CorporateActionResult(
        accepted=_finalize(accepted, RECONCILED_COLUMNS),
        quarantined=_finalize(quarantined, QUARANTINE_COLUMNS),
    )


def _standardize_source(
    frame: pd.DataFrame | None, source: str
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Parse one supplier frame into candidates and quarantined rows."""
    if frame is None:
        return {}, []
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{source} corporate actions must be a DataFrame")
    if frame.empty:
        return {}, []

    columns = _resolve_columns(frame, source)
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    quarantine: list[dict[str, Any]] = []

    for _, row in frame.iterrows():
        event = _parse_event(row, columns, source)
        reason = _reject_reason(event)
        if reason is not None:
            quarantine.append(_row(event, confirmed_by=source, reason=reason))
            continue
        key = (event["symbol"], event["ex_date"])
        if key in candidates:
            raise ValueError(
                f"{source} reports more than one implemented supported action for "
                f"{event['symbol']} on {event['ex_date'].isoformat()}"
            )
        candidates[key] = event
    return candidates, quarantine


def _resolve_columns(frame: pd.DataFrame, source: str) -> dict[str, str]:
    """Map each canonical input field onto a present native column."""
    present = set(frame.columns)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for field, aliases in _FIELD_ALIASES.items():
        column = next(
            (candidate for candidate in aliases if candidate in present), None
        )
        if column is None:
            if field == "plan":
                resolved[field] = ""  # optional
                continue
            missing.append(field)
            continue
        resolved[field] = column
    if missing:
        names = ", ".join(missing)
        raise ValueError(
            f"{source} corporate-action frame is missing required columns: {names}"
        )
    for required in _REQUIRED_FIELDS:
        if required not in resolved:  # pragma: no cover - defensive invariant
            raise AssertionError(f"internal layout bug: {required} not resolved")
    return resolved


def _parse_event(
    row: pd.Series, columns: dict[str, str], source: str
) -> dict[str, Any]:
    """Parse one native row into an internal event (ratios are Decimals)."""
    progress = _text(row, columns["progress"])
    implemented = progress is not None and _IMPLEMENTED_MARKER in progress
    event: dict[str, Any] = {
        "symbol": _canonical_symbol(row[columns["symbol"]]),
        "announcement_date": parse_trade_date(row[columns["announcement_date"]]),
        "record_date": parse_trade_date(row[columns["record_date"]]),
        "ex_date": parse_trade_date(row[columns["ex_date"]]),
        "cash": _per_share(row[columns["cash_dividend"]]),
        "bonus": _per_share(row[columns["bonus"]]),
        "capitalization": _per_share(row[columns["capitalization"]]),
        "status": STATUS_IMPLEMENTED if implemented else STATUS_NOT_IMPLEMENTED,
        "source": source,
    }
    plan = _text(row, columns["plan"])
    progress_text = _text(row, columns["progress"])
    event["unsupported"] = _mentions_unsupported(plan, progress_text)
    return event


def _reject_reason(event: dict[str, Any]) -> str | None:
    """Return a quarantine reason when the event must not be booked."""
    if event["unsupported"]:
        return REASON_UNSUPPORTED_CORPORATE_ACTION
    if event["status"] != STATUS_IMPLEMENTED:
        return REASON_NOT_IMPLEMENTED
    if (
        event["announcement_date"] is None
        or event["record_date"] is None
        or event["ex_date"] is None
    ):
        return REASON_INCOMPLETE
    ratio_keys = ("cash", "bonus", "capitalization")
    if not any(event[key] for key in ratio_keys):
        return REASON_INCOMPLETE
    return None


def _same_facts(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Economic facts equal: record date and the three distribution ratios."""
    return (
        left["record_date"] == right["record_date"]
        and _zeroed(left["cash"]) == _zeroed(right["cash"])
        and _zeroed(left["bonus"]) == _zeroed(right["bonus"])
        and _zeroed(left["capitalization"]) == _zeroed(right["capitalization"])
    )


def _zeroed(value: Decimal | None) -> Decimal:
    return Decimal("0") if value is None else value


def _row(
    event: dict[str, Any],
    *,
    confirmed_by: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Render an event into one canonical output row."""
    row: dict[str, Any] = {
        "symbol": event["symbol"],
        "announcement_date": event["announcement_date"],
        "record_date": event["record_date"],
        "ex_date": event["ex_date"],
        "cash_dividend_per_share": _to_float(event["cash"]),
        "bonus_share_ratio": _to_float(event["bonus"]),
        "capitalization_ratio": _to_float(event["capitalization"]),
        "rights_issue_ratio": None,
        "rights_issue_price": None,
        "status": event["status"],
        "confirmed_by": confirmed_by,
    }
    if reason is not None:
        row["reason"] = reason
    return row


def _finalize(records: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(records, columns=columns)
    for column in (
        "cash_dividend_per_share",
        "bonus_share_ratio",
        "capitalization_ratio",
        "rights_issue_ratio",
        "rights_issue_price",
    ):
        if column in frame.columns:
            frame[column] = frame[column].astype("float64")
    sort_keys = ("symbol", "ex_date", "confirmed_by", "reason")
    present_keys = [key for key in sort_keys if key in frame.columns]
    if not frame.empty:
        frame = frame.sort_values(by=present_keys, kind="stable").reset_index(drop=True)
    return frame


def _per_share(value: object) -> Decimal | None:
    """Parse a native per-ten-share amount into a per-share Decimal."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed / _PER_SHARE_SCALE


def _text(row: pd.Series, column: str) -> str | None:
    value = row[column]
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _mentions_unsupported(*fields: str | None) -> bool:
    text = " ".join(field for field in fields if field)
    return any(keyword in text for keyword in _UNSUPPORTED_KEYWORDS)


def _to_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _canonical_symbol(value: object) -> str:
    """Return the canonical ``000001.SZ``-style symbol of a native code."""
    text = str(value).strip()
    suffixed = _SUFFIXED_SYMBOL.fullmatch(text)
    if suffixed:
        return f"{suffixed.group(1)}.{suffixed.group(2).upper()}"
    if _BARE_SYMBOL.fullmatch(text):
        if text[0] == "6":
            return f"{text}.SH"
        if text[0] in ("0", "3"):
            return f"{text}.SZ"
        if text[0] in ("4", "8") or text.startswith("92"):
            return f"{text}.BJ"
    raise ValueError(f"cannot canonicalize corporate-action symbol {value!r}")
