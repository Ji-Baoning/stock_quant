"""daily_basic -> basic_factor rows (P1: units frozen from probe evidence)."""

from __future__ import annotations

import pandas as pd

#: Probe evidence (docs/operations/2026-10-01-endpoint-probe-evidence.md,
#: §daily_basic): total_mv arrives in 万元 and turnover_rate in percent --
#: values below are the measured confirmation, cited verbatim from the
#: record; never guessed from column names (spec §6.2/§7.2).
#: Relay by-day reading: symbol ``000001.SZ``, trade_date ``20260930``,
#: raw_total_mv ``22452647.3574`` (万元), raw_turnover_rate ``0.5387`` (%).
TOTAL_MV_TO_YUAN_MULTIPLIER = 10_000.0
TURNOVER_RATE_TO_RATIO_DIVISOR = 100.0
PROBE_EVIDENCE_RECORD = "docs/operations/2026-10-01-endpoint-probe-evidence.md"
PROBE_EVIDENCE_JSON = (
    "docs/operations/2026-10-01-endpoint-probe-evidence.evidence.json"
)


#: Known-security readings copied VERBATIM from the relay section of the
#: dated probe evidence (docs/operations/2026-10-01-endpoint-probe-evidence.md
#: and .evidence.json, §daily_basic by-day magnitude_reference; recorded
#: 2026-10-01).  String values stay strings; these feed the magnitude
#: assertions in tests/unit/test_tushare_endpoints.py.
EVIDENCE_REFERENCE_READINGS: tuple[dict[str, str], ...] = (
    {"symbol": "000001.SZ", "trade_date": "20260930",
     "raw_total_mv": "22452647.3574", "raw_turnover_rate": "0.5387"},
)


def daily_basic_to_basic_factor_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert raw daily_basic rows to basic_factor business columns.

    Nulls stay null (never 0); no bar columns are copied (spec §6.1/§7.2).
    """
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(
                frame["trade_date"], format="%Y%m%d", errors="raise"
            ).dt.date,
            "symbol": frame["ts_code"].astype(str),
            "market_cap": pd.to_numeric(frame["total_mv"], errors="raise")
            * TOTAL_MV_TO_YUAN_MULTIPLIER,
            "turnover_rate": pd.to_numeric(frame["turnover_rate"], errors="raise")
            / TURNOVER_RATE_TO_RATIO_DIVISOR,
        }
    )
