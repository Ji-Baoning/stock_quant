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
from typing import Any, Mapping, Protocol

import pandas as pd

from stock_quant.data_model.clean import parse_trade_date

REASON_CROSS_SOURCE_CONFLICT = "cross_source_conflict"
REASON_UNSUPPORTED_CORPORATE_ACTION = "unsupported_corporate_action"
REASON_NOT_IMPLEMENTED = "not_implemented"
REASON_INCOMPLETE = "incomplete"
#: A bankruptcy-reorganisation share transfer (重整转增) that holders never
#: receive and that carries no exchange ex-date adjustment.
REASON_NON_DISTRIBUTIVE_RESTRUCTURING = "non_distributive_restructuring"

#: CNINFO's 分红类型 value naming a reorganisation share transfer.  It is the
#: only type of a bookable event that is refused as a matter of its nature
#: (see ``_reject_reason``); 承诺补偿 and 股改分红 are deliberately *not* listed
#: here, because 股改分红 can carry a real ex-date.
_CNINFO_TYPE_RESTRUCTURING = "重整转增"

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
#: Keywords that name an action the *rights-issue* lane exists to support.  A
#: dividend frame that merely mentions one in its plan text still cannot express
#: the subscription facts, so it stays unsupported there; only the allotment
#: lane, which carries ``配股比例``/``配股价格``, may claim them.
_RIGHTS_KEYWORDS = ("配股", "配售")
_IMPLEMENTED_MARKER = "实施"
_BOTH_SOURCES = "cninfo+eastmoney"

#: The two sides a cross-source conflict is between.  A third-party arbiter
#: (ADR-007) names one of them, and the booked value becomes
#: ``<side>+<arbiter>`` -- the same ``<origin>+<authority>`` form
#: ``apply_corporate_action_reviews`` already writes as ``<side>+reviewed``.
CONFLICT_SIDE_CNINFO = "cninfo"
CONFLICT_SIDE_EASTMONEY = "eastmoney"

#: The one source of rights-issue facts: CNINFO's allotment endpoint, reached
#: through ``stock_allotment_cninfo``.  There is no second source to
#: cross-confirm against, so its rows are accepted on their own and labelled
#: with this name -- never with ``_BOTH_SOURCES``.
RIGHTS_SOURCE = "cninfo_allotment"

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

# AKShare 1.18.23 exposes CNINFO's allotment endpoint as
# ``stock_allotment_cninfo``: the only interface that reports a rights issue.
# Its frame carries a per-ten-share subscription *ratio* and a per-*share*
# subscription *price*, so the two must not share a converter.  It also omits a
# progress column and never states a cash/bonus/capitalization component, and
# the request identifies the security.
_ALLOTMENT_COLUMNS = {
    "证券代码": "证券代码",
    "公告日期": "公告日期",
    "股权登记日": "股权登记日",
    "除权基准日": "除权除息日",
    "配股比例": "配股(股/10股)",
    "配股价格": "配股价格(元/股)",
}

# Ordered native column candidates per canonical input field. The first present
# column is used; ``plan``, ``rights`` and ``rights_price`` are optional (only
# the allotment lane carries the last two; ``plan`` is only needed for
# unsupported tagging).
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
    "rights": ("配股(股/10股)",),
    "rights_price": ("配股价格(元/股)",),
    # Only CNINFO's dividend interface declares what kind of distribution a row
    # is; its own legacy layout and the Eastmoney fallback have no such column.
    "distribution_type": ("分红类型",),
}
#: Fields a frame may legitimately omit, per lane; each resolves to ``""`` and
#: their readers must treat ``""`` as "column absent".  The dividend lanes
#: cannot express a subscription, and the allotment lane reports nothing but a
#: subscription, so each lane is excused from the other's facts -- while any
#: *other* missing column stays a hard frame-level error.
_LANE_OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "distribution": ("plan", "rights", "rights_price", "distribution_type"),
    # The allotment endpoint reports a subscription and nothing else, so it has
    # no distribution type either.
    "rights": (
        "plan",
        "cash_dividend",
        "bonus",
        "capitalization",
        "distribution_type",
    ),
}
_LANE_DISTRIBUTION = "distribution"
_LANE_RIGHTS = "rights"

_SUFFIXED_SYMBOL = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$", re.IGNORECASE)
_BARE_SYMBOL = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class ConflictTerms:
    """One side's terms for a conflicted ``(symbol, ex_date)``.

    The four ratios are the supplier's **per-ten-share** figures, recovered
    exactly from the internal event's per-share Decimals by multiplying by
    ``_PER_SHARE_SCALE`` -- exact in ``Decimal``, so an arbiter compares the
    supplier's own number rather than one this project rounded to.
    """

    symbol: str
    ex_date: date
    cash_per_ten: Decimal
    bonus_per_ten: Decimal
    capitalization_per_ten: Decimal
    rights_per_ten: Decimal
    #: The subscription price is quoted per *share* by every supplier and is
    #: never scaled, unlike the four ratios above.
    rights_price_per_share: Decimal


class CorporateActionArbiter(Protocol):
    """A third party that may name the side a conflict should be booked from.

    An arbiter decides; it never supplies a row.  Returning ``None`` -- because
    it has no record, or because its record does not separate the two sides --
    leaves the conflict quarantined exactly as it was.
    """

    name: str

    def arbitrate(
        self, cninfo: ConflictTerms, eastmoney: ConflictTerms
    ) -> str | None: ...


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


def prepare_allotment_rights_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Adapt AKShare's ``stock_allotment_cninfo`` response for reconciliation.

    The raw supplier frame is persisted before this conversion.  Rows without a
    ``除权基准日`` describe an announced plan rather than a settled event: they
    carry no ex-date, so they can never explain a historical window and are
    dropped here instead of being quarantined as an incomplete *event* -- a
    quarantine for the symbol would make its coverage read as untrusted for the
    whole window on the strength of a plan that never happened.  The remaining
    rows are settled, so they are marked implemented; the request supplies the
    security code.
    """
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("cninfo allotment response must be a DataFrame")
    if frame.empty:
        return frame.copy()
    if {"证券代码", "配股(股/10股)"}.issubset(frame.columns):
        return frame.copy()
    missing = sorted(set(_ALLOTMENT_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(
            "cninfo allotment response is missing required columns: "
            + ", ".join(missing)
        )
    prepared = frame.rename(columns=_ALLOTMENT_COLUMNS).copy()
    prepared["证券代码"] = symbol.split(".", maxsplit=1)[0]
    prepared["进度"] = "实施"
    settled = pd.to_datetime(prepared["除权除息日"], errors="coerce").notna()
    return prepared.loc[settled].reset_index(drop=True)


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
    *,
    arbiter: CorporateActionArbiter | None = None,
) -> CorporateActionResult:
    """Reconcile the CNINFO and Eastmoney corporate-action frames.

    ``arbiter`` (ADR-007) is consulted **only** where the two sources disagree
    on the same ``(symbol, ex_date)``, and it is asked *first*: the booked value
    names the side that was corroborated plus the arbiter that corroborated it,
    so a channel's verdict is never pre-empted (ADR-014).

    Only when no arbiter can name a side does ``_merged_within_representation_
    floor`` get a turn: a pair whose every field agrees to within float32's own
    grid is one stated ratio in two renderings, and books as ``_BOTH_SOURCES``
    carrying the longer rendering.  Anything further apart than that -- a real
    disagreement no channel could settle -- is quarantined exactly as before.
    """
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
                booked = _arbitrated_event(cn_event, em_event, arbiter)
                if booked is not None:
                    event, confirmed_by = booked
                    accepted.append(_row(event, confirmed_by=confirmed_by))
                    continue
                merged = _merged_within_representation_floor(cn_event, em_event)
                if merged is not None:
                    accepted.append(_row(merged, confirmed_by=_BOTH_SOURCES))
                    continue
                quarantined.append(
                    _row(
                        cn_event,
                        confirmed_by=CONFLICT_SIDE_CNINFO,
                        reason=REASON_CROSS_SOURCE_CONFLICT,
                    )
                )
                quarantined.append(
                    _row(
                        em_event,
                        confirmed_by=CONFLICT_SIDE_EASTMONEY,
                        reason=REASON_CROSS_SOURCE_CONFLICT,
                    )
                )
        elif cn_event is not None:
            accepted.append(_row(cn_event, confirmed_by=CONFLICT_SIDE_CNINFO))
        elif em_event is not None:
            accepted.append(_row(em_event, confirmed_by=CONFLICT_SIDE_EASTMONEY))

    return CorporateActionResult(
        accepted=_finalize(accepted, RECONCILED_COLUMNS),
        quarantined=_finalize(quarantined, QUARANTINE_COLUMNS),
    )


def _arbitrated_event(
    cn_event: dict[str, Any],
    em_event: dict[str, Any],
    arbiter: CorporateActionArbiter | None,
) -> tuple[dict[str, Any], str] | None:
    """The event an arbiter books, with its ``<side>+<arbiter>`` label.

    ``None`` when no arbiter is configured or it does not name a side; the
    caller then quarantines both sides, which is the fail-closed outcome.
    """
    if arbiter is None:
        return None
    side = arbiter.arbitrate(_conflict_terms(cn_event), _conflict_terms(em_event))
    if side is None:
        return None
    event = cn_event if side == CONFLICT_SIDE_CNINFO else em_event
    return event, f"{side}+{arbiter.name}"


def _conflict_terms(event: dict[str, Any]) -> ConflictTerms:
    """One side's terms at the per-ten-share scale the supplier stated them."""
    return ConflictTerms(
        symbol=event["symbol"],
        ex_date=event["ex_date"],
        cash_per_ten=_zeroed(event["cash"]) * _PER_SHARE_SCALE,
        bonus_per_ten=_zeroed(event["bonus"]) * _PER_SHARE_SCALE,
        capitalization_per_ten=(
            _zeroed(event["capitalization"]) * _PER_SHARE_SCALE
        ),
        rights_per_ten=_zeroed(event["rights"]) * _PER_SHARE_SCALE,
        rights_price_per_share=_zeroed(event["rights_price"]),
    )


def normalize_rights_issue_actions(
    frame: pd.DataFrame | None,
) -> CorporateActionResult:
    """Normalize the single-source rights-issue lane into canonical rows.

    A subscription is reported by CNINFO's allotment interface alone, so unlike
    a distribution there is no second source to cross-confirm against and no
    cross-source conflict to resolve: every candidate is accepted on its own and
    labelled with the lane's own source, never with ``_BOTH_SOURCES``.
    """
    candidates, quarantine = _standardize_source(
        frame, RIGHTS_SOURCE, rights_supported=True
    )
    accepted = [
        _row(candidates[key], confirmed_by=RIGHTS_SOURCE) for key in sorted(candidates)
    ]
    return CorporateActionResult(
        accepted=_finalize(accepted, RECONCILED_COLUMNS),
        quarantined=_finalize(quarantine, QUARANTINE_COLUMNS),
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
    frame: pd.DataFrame | None,
    source: str,
    *,
    rights_supported: bool = False,
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Parse one supplier frame into candidates and quarantined rows."""
    if frame is None:
        return {}, []
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{source} corporate actions must be a DataFrame")
    if frame.empty:
        return {}, []

    lane = _LANE_RIGHTS if rights_supported else _LANE_DISTRIBUTION
    columns = _resolve_columns(frame, source, lane=lane)
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    quarantine: list[dict[str, Any]] = []

    for _, row in frame.iterrows():
        event = _parse_event(row, columns, source, rights_supported=rights_supported)
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


def _resolve_columns(
    frame: pd.DataFrame, source: str, *, lane: str = _LANE_DISTRIBUTION
) -> dict[str, str]:
    """Map each canonical input field onto a present native column."""
    present = set(frame.columns)
    optional = _LANE_OPTIONAL_FIELDS[lane]
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for field, aliases in _FIELD_ALIASES.items():
        column = next(
            (candidate for candidate in aliases if candidate in present), None
        )
        if column is None:
            if field in optional:
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
    for required in _FIELD_ALIASES:
        if required not in resolved:  # pragma: no cover - defensive invariant
            raise AssertionError(f"internal layout bug: {required} not resolved")
    return resolved


def _parse_event(
    row: pd.Series,
    columns: dict[str, str],
    source: str,
    *,
    rights_supported: bool = False,
) -> dict[str, Any]:
    """Parse one native row into an internal event (ratios are Decimals).

    ``rights_supported`` marks the lane that carries subscription facts: there
    the ``配股`` wording is a claim the lane can honour, so it is not read as an
    unsupported action.
    """
    progress = _text(row, columns["progress"])
    implemented = progress is not None and _IMPLEMENTED_MARKER in progress
    event: dict[str, Any] = {
        "symbol": _canonical_symbol(row[columns["symbol"]]),
        "announcement_date": parse_trade_date(row[columns["announcement_date"]]),
        "record_date": parse_trade_date(row[columns["record_date"]]),
        "ex_date": parse_trade_date(row[columns["ex_date"]]),
        # A lane that omits a distribution column resolves it to "" (see
        # _resolve_columns) and must not be read -- ``row[""]`` raises.
        "cash": (
            _per_share(row[columns["cash_dividend"]])
            if columns["cash_dividend"]
            else None
        ),
        "bonus": _per_share(row[columns["bonus"]]) if columns["bonus"] else None,
        "capitalization": (
            _per_share(row[columns["capitalization"]])
            if columns["capitalization"]
            else None
        ),
        # ``rights`` is a per-ten-share ratio like the distributions above, but
        # ``rights_price`` is the subscription price *per share* and must not be
        # scaled down with it.
        "rights": _per_share(row[columns["rights"]]) if columns["rights"] else None,
        "rights_price": (
            _price(row[columns["rights_price"]]) if columns["rights_price"] else None
        ),
        "status": STATUS_IMPLEMENTED if implemented else STATUS_NOT_IMPLEMENTED,
        "source": source,
    }
    # ``plan`` is optional (see _resolve_columns): when no 方案 / 方案说明 column is
    # present it resolves to "" and must not be read, so it falls back to its
    # documented default (absent -> no text) for unsupported tagging.
    plan = _text(row, columns["plan"]) if columns["plan"] else None
    progress_text = _text(row, columns["progress"])
    event["unsupported"] = _mentions_unsupported(
        plan, progress_text, rights_supported=rights_supported
    )
    # Also optional: an absent type column means the frame cannot classify its
    # rows, which must read as an ordinary distribution rather than withhold
    # every row of the frame.
    event["distribution_type"] = (
        _text(row, columns["distribution_type"])
        if columns["distribution_type"]
        else None
    )
    return event


def _reject_reason(event: dict[str, Any]) -> str | None:
    """Return a quarantine reason when the event must not be booked."""
    if event["unsupported"]:
        return REASON_UNSUPPORTED_CORPORATE_ACTION
    if event["status"] != STATUS_IMPLEMENTED:
        return REASON_NOT_IMPLEMENTED
    # Checked before the date-completeness gate below, because a 重整转增 is
    # *never* a price event: its supplier-reported ex-date is spurious and its
    # absent one is expected.  Letting it fall through would book a share
    # increase no holder received, or -- with no ex-date -- report a whole
    # symbol/window as an incomplete record.
    if event["distribution_type"] == _CNINFO_TYPE_RESTRUCTURING:
        return REASON_NON_DISTRIBUTIVE_RESTRUCTURING
    if (
        event["announcement_date"] is None
        or event["record_date"] is None
        or event["ex_date"] is None
    ):
        return REASON_INCOMPLETE
    ratio_keys = ("cash", "bonus", "capitalization", "rights")
    if not any(event[key] for key in ratio_keys):
        return REASON_INCOMPLETE
    # A subscription is an exchange of cash for shares: without the price the
    # ex-date ratio cannot be formed, so the event is incomplete rather than
    # bookable with a zero-cost assumption.
    if event["rights"] and event["rights_price"] is None:
        return REASON_INCOMPLETE
    return None


def _same_facts(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Economic facts *identically* equal: record date and every ratio field.

    Exact equality, and it stays exact: this is the basis on which the
    corroborated classes are detected at all, so widening it would blur the
    evidence a channel is asked to adjudicate (ADR-007 §4).  A pair agreeing
    only to within float32's own grid is a different statement, and is handled
    by ``_merged_within_representation_floor`` -- which runs only once every
    channel has declined to name a side (ADR-014).
    """
    return (
        left["record_date"] == right["record_date"]
        and _zeroed(left["cash"]) == _zeroed(right["cash"])
        and _zeroed(left["bonus"]) == _zeroed(right["bonus"])
        and _zeroed(left["capitalization"]) == _zeroed(right["capitalization"])
        and _zeroed(left["rights"]) == _zeroed(right["rights"])
        and _zeroed(left["rights_price"]) == _zeroed(right["rights_price"])
    )


#: float32's machine epsilon, ``2 ** -23`` (≈ 1.19e-7), written as a power so
#: it is exactly representable in ``Decimal`` and the comparison never leaves
#: decimal arithmetic.  It is the width of float32's own grid, and so the width
#: at which two suppliers stating one ratio can still land on adjacent values:
#: the published 丁-class pairs are exactly one ULP apart.  A corroborating
#: channel separates pairs at that same one-ULP distance, which is why the
#: channel is asked first and this floor is only the fallback (ADR-014).
_FLOAT32_RELATIVE_EPSILON = Decimal(2) ** -23

#: The per-share ratio fields, as ``_row`` renders them.
_RATIO_FIELDS = ("cash", "bonus", "capitalization", "rights", "rights_price")


def _within_representation_floor(left: Decimal, right: Decimal) -> bool:
    """Whether two supplier ratios differ only inside float32's own grid."""
    if left == right:
        return True
    scale = max(abs(left), abs(right))
    if scale == 0:
        return False
    return abs(left - right) <= _FLOAT32_RELATIVE_EPSILON * scale


def _more_precise(left: Decimal, right: Decimal) -> Decimal:
    """The side of a floor-level pair stating its ratio in more digits.

    Two renderings of one float32 value differ in how much they say about it,
    not in what the exchange did: 9.998781 states seven digits where 9.998780
    states six, so the longer one carries strictly more of the number.  A tie
    keeps ``left`` (CNINFO's side) -- the same party ``_same_facts`` favours by
    treating the pair as one fact at all.
    """
    if len(right.as_tuple().digits) > len(left.as_tuple().digits):
        return right
    return left


def _merged_within_representation_floor(
    cn_event: dict[str, Any], em_event: dict[str, Any]
) -> dict[str, Any] | None:
    """One event from two sides differing only inside float32's grid.

    ``None`` unless *every* ratio field is either identical or within one ULP
    at its own magnitude -- one field further apart is a real disagreement and
    keeps its quarantine, so this can never widen into a general tolerance.  The
    record date must match exactly, as in ``_same_facts``.

    The merged event keeps CNINFO's dates and status and takes each ratio field
    from whichever side stated it in more digits.  Because the two sides state
    one ratio, the row books ``_BOTH_SOURCES``: the choice between renderings is
    not a doubt about the event.
    """
    if cn_event["record_date"] != em_event["record_date"]:
        return None
    merged = dict(cn_event)
    for field in _RATIO_FIELDS:
        left = _zeroed(cn_event[field])
        right = _zeroed(em_event[field])
        if left == right:
            continue
        if not _within_representation_floor(left, right):
            return None
        merged[field] = _more_precise(left, right)
    return merged


def _combine_same_day_events(
    existing: dict[str, Any], incoming: dict[str, Any], source: str
) -> dict[str, Any]:
    """Combine supplier distributions that settle on the same ex/record date.

    Multiple implemented distributions on one ex date (for example an annual
    and a special dividend) have the same account effect as one summed event.
    Different record dates remain ambiguous and are rejected rather than
    silently selecting one, and so does a second subscription at a different
    price -- two rights issues cannot share an ex date, so the pair is a
    supplier defect rather than a summable event.
    """
    if existing["record_date"] != incoming["record_date"]:
        raise ValueError(
            f"{source} reports more than one implemented supported action for "
            f"{incoming['symbol']} on {incoming['ex_date'].isoformat()}"
        )
    existing_price = existing["rights_price"]
    incoming_price = incoming["rights_price"]
    if (
        existing_price is not None
        and incoming_price is not None
        and existing_price != incoming_price
    ):
        raise ValueError(
            f"{source} reports two subscription prices for "
            f"{incoming['symbol']} on {incoming['ex_date'].isoformat()}"
        )
    combined = dict(existing)
    for field in ("cash", "bonus", "capitalization", "rights"):
        combined[field] = _zeroed(existing[field]) + _zeroed(incoming[field])
    combined["rights_price"] = (
        existing_price if existing_price is not None else incoming_price
    )
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
        "rights_issue_ratio": _to_float(event.get("rights")),
        "rights_issue_price": _to_float(event.get("rights_price")),
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
    parsed = _decimal(value)
    return None if parsed is None else parsed / _PER_SHARE_SCALE


def _price(value: object) -> Decimal | None:
    """Parse a native per-*share* amount (a subscription price) unchanged."""
    return _decimal(value)


def _decimal(value: object) -> Decimal | None:
    """Parse a native decimal cell, or ``None`` when it carries no number."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _text(row: pd.Series, column: str) -> str | None:
    value = row[column]
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _mentions_unsupported(*fields: str | None, rights_supported: bool = False) -> bool:
    text = " ".join(field for field in fields if field)
    keywords = (
        tuple(
            keyword
            for keyword in _UNSUPPORTED_KEYWORDS
            if keyword not in _RIGHTS_KEYWORDS
        )
        if rights_supported
        else _UNSUPPORTED_KEYWORDS
    )
    return any(keyword in text for keyword in keywords)


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
