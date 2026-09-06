"""Security-master coverage evidence rows (point-in-time task).

Row presence is VERIFIED for the research freeze; there is deliberately no
status/reason vocabulary on this table (unlike the corporate-action coverage
table).  The frame must be deterministic: fixed columns, co-erced dates, rows
sorted by symbol.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS
from stock_quant.data_model.schemas import SECURITY_MASTER_COVERAGE_COLUMNS
from stock_quant.data_model.security_master import (
    MASTER_SOURCE_STOCK_BASIC,
    ListStatus,
    master_coverage_frame,
    master_coverage_record,
    missing_master_coverage_symbols,
)


def test_frame_has_canonical_columns_and_sorts_by_symbol():
    frame = master_coverage_frame(
        [
            master_coverage_record(
                "600001.SH", list_date=date(2019, 6, 1),
                list_status=ListStatus.L,
            ),
            master_coverage_record(
                "600000.SH", list_date=date(2018, 1, 2),
                list_status=ListStatus.L,
                snapshot_sha256="ab" * 32,
                sdk_version="1.0.0",
                checked_at=pd.Timestamp("2026-09-05T08:00:00Z"),
            ),
        ]
    )
    assert list(frame.columns) == SECURITY_MASTER_COVERAGE_COLUMNS
    assert frame["symbol"].tolist() == ["600000.SH", "600001.SH"]
    assert frame["list_status"].tolist() == ["L", "L"]
    assert frame.loc[0, "source"] == MASTER_SOURCE_STOCK_BASIC
    assert frame.loc[0, "snapshot_sha256"] == "ab" * 32


def test_missing_symbols_cover_none_empty_and_partial_evidence():
    covered = master_coverage_frame(
        [master_coverage_record("600000.SH", list_date=date(2018, 1, 2))]
    )
    empty = master_coverage_frame([])
    assert missing_master_coverage_symbols({"600000.SH"}, None) == ["600000.SH"]
    assert missing_master_coverage_symbols({"600000.SH"}, empty) == ["600000.SH"]
    assert missing_master_coverage_symbols(
        {"600000.SH", "600001.SH"}, covered
    ) == ["600001.SH"]
    assert missing_master_coverage_symbols({"600000.SH"}, covered) == []


def test_coverage_table_is_registered_for_publication():
    assert "security_master_coverage" in STANDARDIZED_SCHEMAS
