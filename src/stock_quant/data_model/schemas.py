"""Canonical column contracts and PyArrow schemas for standardized data.

The standardized data model converts supplier-native raw frames (Task 2) into
timezone-free, source-labelled, unit-normalized tables. ``DAILY_COLUMNS`` fixes
the exact output column order of ``normalize_daily``; the PyArrow schemas below
are the persistence contracts that later tasks validate and publish against.
"""

from __future__ import annotations

import pyarrow as pa

DAILY_COLUMNS = [
    "trade_date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "adjustment",
    "source",
    "ingested_at",
]

# Cleaning-audit columns: source, security, date, rule, action and the raw
# values that were removed or changed. Kept deliberately narrow: deterministic
# transforms are traceable through their constants, whereas information-losing
# events (for example collapsing identical duplicate rows) must be recorded.
AUDIT_COLUMNS = [
    "source",
    "symbol",
    "trade_date",
    "rule",
    "action",
    "old_value",
    "new_value",
    "ingested_at",
]

# Corporate actions follow the design-spec corporate_action contract.
CORPORATE_ACTION_COLUMNS = [
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
]

# Corporate-action coverage evidence records, per symbol and window, whether
# the action sources were actually checked and what was concluded.  Empty
# facts are never trusted by default: an explicit successful no-event response
# from every applicable endpoint yields VERIFIED_EMPTY, any endpoint failure or
# an unrequested/missing source yields UNTRUSTED with a structured reason.
CORPORATE_ACTION_COVERAGE_COLUMNS = [
    "symbol",
    "window_start",
    "window_end",
    "status",
    "reason",
    "sources",
    "snapshot_hashes",
    "checked_at",
]

# Security-master coverage evidence records one VERIFIED row per universe
# symbol after a real tushare stock_basic refresh.  Row presence is the whole
# vocabulary (no status/reason): a symbol with a row had its listing facts
# confirmed against a live snapshot; an empty or absent table is unverified.
SECURITY_MASTER_COVERAGE_COLUMNS = [
    "symbol",
    "list_date",
    "delist_date",
    "list_status",
    "source",
    "snapshot_sha256",
    "sdk_version",
    "checked_at",
]

# Security master lists the point-in-time identifier attributes per instrument.
SECURITY_MASTER_COLUMNS = [
    "symbol",
    "name",
    "exchange",
    "board",
    "list_date",
    "delist_date",
    "list_status",
]

# Trading calendar marks which calendar dates are confirmed open days.
TRADING_CALENDAR_COLUMNS = [
    "calendar_date",
    "is_trading_day",
]

# Adjusted bars are the point-in-time total-return series derived from the
# unadjusted ``daily_bar`` closes plus accepted corporate actions.  ``raw_*``
# columns keep the audit trail back to ``daily_bar``; ``applied_action_ids``
# carries the deterministic JSON list of action ids first applied that day.
ADJUSTED_BAR_COLUMNS = [
    "trade_date",
    "symbol",
    "source",
    "adjustment",
    "raw_close",
    "adjusted_close",
    "adjustment_factor",
    "quality_severity",
    "invalid_reason",
    "applied_action_ids",
]

# Corporate-action quarantine holds the reconciled-but-untrusted events
# (``QUARANTINE_COLUMNS`` from the reconciler, persisted verbatim) so quality
# gates and the adjusted-bar builder can mark exact trust breaks per ex_date.
CORPORATE_ACTION_QUARANTINE_COLUMNS = [
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
    "reason",
]


def _daily_fields() -> list[pa.Field]:
    return [
        pa.field("trade_date", pa.date32()),
        pa.field("symbol", pa.string()),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.int64()),
        pa.field("amount", pa.float64()),
        pa.field("adjustment", pa.string()),
        pa.field("source", pa.string()),
        pa.field("ingested_at", pa.timestamp("us", tz="UTC")),
    ]


def _corporate_action_fields() -> list[pa.Field]:
    return [
        pa.field("symbol", pa.string()),
        pa.field("announcement_date", pa.date32()),
        pa.field("record_date", pa.date32()),
        pa.field("ex_date", pa.date32()),
        pa.field("cash_dividend_per_share", pa.float64()),
        pa.field("bonus_share_ratio", pa.float64()),
        pa.field("capitalization_ratio", pa.float64()),
        pa.field("rights_issue_ratio", pa.float64()),
        pa.field("rights_issue_price", pa.float64()),
        pa.field("source", pa.string()),
        pa.field("status", pa.string()),
    ]


def _corporate_action_coverage_fields() -> list[pa.Field]:
    # ``sources`` and ``snapshot_hashes`` carry deterministic JSON (one entry
    # per supplier endpoint) so the evidence stays machine-readable inside a
    # single string column, matching the Arrow vocabulary already in use.
    return [
        pa.field("symbol", pa.string()),
        pa.field("window_start", pa.date32()),
        pa.field("window_end", pa.date32()),
        pa.field("status", pa.string()),
        pa.field("reason", pa.string()),
        pa.field("sources", pa.string()),
        pa.field("snapshot_hashes", pa.string()),
        pa.field("checked_at", pa.timestamp("us", tz="UTC")),
    ]


def _security_master_fields() -> list[pa.Field]:
    return [
        pa.field("symbol", pa.string()),
        pa.field("name", pa.string()),
        pa.field("exchange", pa.string()),
        pa.field("board", pa.string()),
        pa.field("list_date", pa.date32()),
        pa.field("delist_date", pa.date32()),
        pa.field("list_status", pa.string()),
    ]


def _security_master_coverage_fields() -> list[pa.Field]:
    return [
        pa.field("symbol", pa.string()),
        pa.field("list_date", pa.date32()),
        pa.field("delist_date", pa.date32()),
        pa.field("list_status", pa.string()),
        pa.field("source", pa.string()),
        pa.field("snapshot_sha256", pa.string()),
        pa.field("sdk_version", pa.string()),
        pa.field("checked_at", pa.timestamp("us", tz="UTC")),
    ]


def _trading_calendar_fields() -> list[pa.Field]:
    return [
        pa.field("calendar_date", pa.date32()),
        pa.field("is_trading_day", pa.bool_()),
    ]


def _adjusted_bar_fields() -> list[pa.Field]:
    return [
        pa.field("trade_date", pa.date32()),
        pa.field("symbol", pa.string()),
        pa.field("source", pa.string()),
        pa.field("adjustment", pa.string()),
        pa.field("raw_close", pa.float64()),
        pa.field("adjusted_close", pa.float64()),
        pa.field("adjustment_factor", pa.float64()),
        pa.field("quality_severity", pa.string()),
        pa.field("invalid_reason", pa.string()),
        pa.field("applied_action_ids", pa.string()),
    ]


def _corporate_action_quarantine_fields() -> list[pa.Field]:
    return [
        pa.field("symbol", pa.string()),
        pa.field("announcement_date", pa.date32()),
        pa.field("record_date", pa.date32()),
        pa.field("ex_date", pa.date32()),
        pa.field("cash_dividend_per_share", pa.float64()),
        pa.field("bonus_share_ratio", pa.float64()),
        pa.field("capitalization_ratio", pa.float64()),
        pa.field("rights_issue_ratio", pa.float64()),
        pa.field("rights_issue_price", pa.float64()),
        pa.field("status", pa.string()),
        pa.field("confirmed_by", pa.string()),
        pa.field("reason", pa.string()),
    ]


DAILY_SCHEMA = pa.schema(_daily_fields())
CORPORATE_ACTION_SCHEMA = pa.schema(_corporate_action_fields())
CORPORATE_ACTION_COVERAGE_SCHEMA = pa.schema(_corporate_action_coverage_fields())
CORPORATE_ACTION_QUARANTINE_SCHEMA = pa.schema(_corporate_action_quarantine_fields())
SECURITY_MASTER_SCHEMA = pa.schema(_security_master_fields())
SECURITY_MASTER_COVERAGE_SCHEMA = pa.schema(_security_master_coverage_fields())
TRADING_CALENDAR_SCHEMA = pa.schema(_trading_calendar_fields())
ADJUSTED_BAR_SCHEMA = pa.schema(_adjusted_bar_fields())
