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
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

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

# AKShare 1.18.23 exposes CNINFO's historical dividend endpoint as
# ``stock_dividend_cninfo``.  Its supplier-native labels differ from the older
# frame consumed below and omit the security code (the request identifies it).
_CNINFO_DIVIDEND_COLUMNS = {
    "实施方案公告日期": "公告日期",
    "送股比例": "送股(股/10股)",
    "转增比例": "转增(股/10股)",
    "派息比例": "派息(税前)(元/10股)",
    "除权日": "除权除息日",
    "实施方案分红说明": "方案",
}

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


def prepare_cninfo_dividend_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Adapt AKShare's ``stock_dividend_cninfo`` response for reconciliation.

    The raw supplier frame is persisted before this conversion.  CNINFO's
    historical-dividend response consists of implemented events, so the
    compatibility frame marks each row as implemented and supplies the symbol
    from the symbol-scoped request.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("cninfo dividend response must be a DataFrame")
    if frame.empty:
        return frame.copy()
    if {"证券代码", "公告日期"}.issubset(frame.columns):
        return frame.copy()
    missing = sorted(set(_CNINFO_DIVIDEND_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(
            "cninfo dividend response is missing required columns: "
            + ", ".join(missing)
        )
    prepared = frame.rename(columns=_CNINFO_DIVIDEND_COLUMNS).copy()
    prepared["证券代码"] = symbol.split(".", maxsplit=1)[0]
    prepared["进度"] = "实施"
    return prepared


def prepare_eastmoney_dividend_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Adapt Eastmoney or its THS fallback to the reconciliation layout."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("eastmoney dividend response must be a DataFrame")
    if frame.empty:
        return frame.copy()
    if "分红方案说明" in frame.columns:
        return _prepare_ths_dividend_frame(frame, symbol)
    if "代码" in frame.columns:
        return frame.copy()
    prepared = frame.copy()
    prepared["代码"] = symbol.split(".", maxsplit=1)[0]
    return prepared


def _prepare_ths_dividend_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Map AKShare's ``stock_fhps_detail_ths`` frame to Eastmoney labels."""
    required = {
        "实施公告日",
        "分红方案说明",
        "A股股权登记日",
        "A股除权除息日",
        "方案进度",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError("ths dividend response is missing: " + ", ".join(missing))
    plan = frame["分红方案说明"].fillna("").astype(str)
    prepared = pd.DataFrame(
        {
            "代码": symbol.split(".", maxsplit=1)[0],
            "最新公告日期": frame["实施公告日"],
            "股权登记日": frame["A股股权登记日"],
            "除权除息日": frame["A股除权除息日"],
            "现金分红-现金分红比例": _ths_plan_ratio(plan, "派"),
            "送转股份-送股比例": _ths_plan_ratio(plan, "送"),
            "送转股份-转股比例": _ths_plan_ratio(plan, "转"),
            "方案进度": frame["方案进度"],
            "方案": plan,
        }
    )
    return prepared


def _ths_plan_ratio(plan: pd.Series, marker: str) -> pd.Series:
    """Extract a per-ten-share THS plan component, leaving absent values null."""
    return pd.to_numeric(
        plan.str.extract(rf"{marker}([0-9]+(?:\\.[0-9]+)?)", expand=False),
        errors="coerce",
    )


def filter_corporate_actions_to_window(
    frame: pd.DataFrame, start, end
) -> pd.DataFrame:
    """Exclude dated events outside the requested backtest window.

    A supplier's unimplemented plan without an ex-date cannot affect a completed
    historical window, so it is excluded too.  An implemented record with a
    missing ex-date remains for reconciliation to flag as a genuine defect.
    """
    if frame.empty:
        return frame.copy()
    ex_column = next(
        (name for name in ("除权除息日", "除权日") if name in frame.columns), None
    )
    if ex_column is None:
        return frame.copy()
    dates = pd.to_datetime(frame[ex_column], errors="coerce")
    in_window = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    progress_column = next(
        (name for name in ("进度", "方案进度") if name in frame.columns), None
    )
    implemented = (
        frame[progress_column].fillna("").astype(str).str.contains(_IMPLEMENTED_MARKER)
        if progress_column is not None
        else pd.Series(True, index=frame.index)
    )
    keep = in_window | (dates.isna() & implemented)
    return frame.loc[keep].reset_index(drop=True)


#: Why a quarantined row cannot affect a window.  Each label names the rule
#: that excluded it, so the audit trail records whether the exclusion rests on
#: a dated fact (a derivation), on the bounded settlement-lag inference for a
#: pre-window record date, or on the pre-window announcement rule for an
#: implemented record (a conditional relaxation -- see ADR-006).
EXCLUSION_EX_DATE_OUT_OF_WINDOW = "ex_date_out_of_window"
EXCLUSION_RECORD_DATE_OUT_OF_WINDOW = "record_date_out_of_window"
EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED = "announcement_pre_window_implemented"

#: The widest settlement lag between a record date and its ex-date among the
#: accepted facts (6,872/6,872 carry both; 1-13 days, no negatives).  It bounds
#: how far *before* a window a record date may sit and still leave the ex-date
#: inside it, so a pre-window record date is excluded only beyond this margin
#: (see ADR-006).
_EX_DATE_LAG_MAX_DAYS = 13


def quarantine_row_out_of_window_reason(
    row: Mapping[str, object], start: date, end: date
) -> str | None:
    """Why a quarantined row cannot affect ``[start, end]``; ``None`` if it can.

    A quarantine row is evidence about one event, and an event can only matter
    to a window it falls in.  This answers the *window* question the coverage
    verdict asks; it never edits or hides the row itself.  The first date the
    row actually knows decides:

    1. ``ex_date`` known -- an ex-date outside the window is a transition
       outside it.  This is a derivation.
    2. ``record_date`` known -- the ex-date is never earlier than its record
       date (measured on the accepted facts: 6,872/6,872 carry both, lag 1-13
       days, no negatives).  A record date *after* the window therefore puts the
       ex-date after it; one more than ``_EX_DATE_LAG_MAX_DAYS`` before ``start``
       cannot settle inside the window either.  A record date inside that margin
       before ``start`` keeps the row -- the lag could still land in the window.
    3. no ex_date and no record_date, but an ``announcement_date`` before
       ``start`` on an ``implemented`` record -- excluded.  This *relaxes* the
       policy ``filter_corporate_actions_to_window`` states (an implemented
       record with a missing ex-date is kept so reconciliation can flag it) and
       is recorded in ``docs/adr/006-corporate-action-window-scope.md``.  An
       announcement *after* the window does not qualify: the event it announces
       may still settle inside the window.
    4. no known date at all -- kept (fail closed).
    """
    ex_date = _row_date(row.get("ex_date"))
    if ex_date is not None:
        return None if start <= ex_date <= end else EXCLUSION_EX_DATE_OUT_OF_WINDOW
    record_date = _row_date(row.get("record_date"))
    if record_date is not None:
        if start <= record_date <= end:
            return None
        # An ex-date is never earlier than its record date, so a record date
        # after the window puts the ex-date after it too.  Before the window
        # that implication needs the lag bound: a record date this close to
        # ``start`` may still settle inside it, so the row is kept (fail closed
        # rather than silently clean).
        after_window = record_date > end
        before_window = record_date < start - timedelta(days=_EX_DATE_LAG_MAX_DAYS)
        if after_window or before_window:
            return EXCLUSION_RECORD_DATE_OUT_OF_WINDOW
        return None
    announcement_date = _row_date(row.get("announcement_date"))
    if (
        announcement_date is not None
        and announcement_date < start
        and str(row.get("status")) == STATUS_IMPLEMENTED
    ):
        return EXCLUSION_ANNOUNCEMENT_PRE_WINDOW_IMPLEMENTED
    return None


def _row_date(value: object) -> date | None:
    """Coerce a canonical row's date cell (``date``/``Timestamp``/``NaT``)."""
    if value is None:
        return None
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return None
    return timestamp.date()


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


def apply_corporate_action_reviews(
    result: CorporateActionResult, reviews: list[dict[str, object]]
) -> CorporateActionResult:
    """Resolve explicitly reviewed cross-source conflicts without guessing.

    Each review pins the selected source and economic facts. A changed supplier
    response therefore fails closed instead of silently reusing a stale review.
    """
    accepted = result.accepted.to_dict("records")
    quarantined = result.quarantined.to_dict("records")
    for review in reviews:
        symbol = str(review["symbol"])
        ex_date = parse_trade_date(review["ex_date"])
        selected_source = str(review["selected_source"])
        matched = [
            row
            for row in quarantined
            if row.get("reason") == REASON_CROSS_SOURCE_CONFLICT
            and row.get("symbol") == symbol
            and row.get("ex_date") == ex_date
            and row.get("confirmed_by") == selected_source
        ]
        if len(matched) != 1:
            raise ValueError(
                f"reviewed corporate action {symbol}#{ex_date} has "
                f"{len(matched)} matching {selected_source} conflicts"
            )
        selected = matched[0]
        for field in (
            "record_date",
            "cash_dividend_per_share",
            "bonus_share_ratio",
            "capitalization_ratio",
        ):
            if not _review_value_matches(selected.get(field), review[field]):
                raise ValueError(
                    f"reviewed corporate action {symbol}#{ex_date} differs in {field}"
                )
        selected = dict(selected)
        selected["confirmed_by"] = f"{selected_source}+reviewed"
        selected.pop("reason", None)
        accepted.append(selected)
        quarantined = [
            row
            for row in quarantined
            if not (
                row.get("reason") == REASON_CROSS_SOURCE_CONFLICT
                and row.get("symbol") == symbol
                and row.get("ex_date") == ex_date
            )
        ]
    return CorporateActionResult(
        accepted=_finalize(accepted, RECONCILED_COLUMNS),
        quarantined=_finalize(quarantined, QUARANTINE_COLUMNS),
    )


def _review_value_matches(actual: object, expected: object) -> bool:
    if isinstance(expected, (float, int)):
        return actual is not None and abs(float(actual) - float(expected)) < 1e-12
    return parse_trade_date(actual) == parse_trade_date(expected)


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
            candidates[key] = _combine_same_day_events(candidates[key], event, source)
            continue
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
    # ``plan`` is optional (see _resolve_columns): when no 方案 / 方案说明 column is
    # present it resolves to "" and must not be read, so it falls back to its
    # documented default (absent -> no text) for unsupported tagging.
    plan = _text(row, columns["plan"]) if columns["plan"] else None
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


def _combine_same_day_events(
    existing: dict[str, Any], incoming: dict[str, Any], source: str
) -> dict[str, Any]:
    """Combine supplier distributions that settle on the same ex/record date.

    Multiple implemented distributions on one ex date (for example an annual
    and a special dividend) have the same account effect as one summed event.
    Different record dates remain ambiguous and are rejected rather than
    silently selecting one.
    """
    if existing["record_date"] != incoming["record_date"]:
        raise ValueError(
            f"{source} reports more than one implemented supported action for "
            f"{incoming['symbol']} on {incoming['ex_date'].isoformat()}"
        )
    combined = dict(existing)
    for field in ("cash", "bonus", "capitalization"):
        combined[field] = _zeroed(existing[field]) + _zeroed(incoming[field])
    combined["announcement_date"] = min(
        existing["announcement_date"], incoming["announcement_date"]
    )
    return combined


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
