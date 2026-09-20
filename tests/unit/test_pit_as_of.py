"""Criterion 4: PIT accessor — no look-ahead, fallback WARN, duplicate ruling."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_quant.data_contracts import parse_data_contracts
from stock_quant.research.pit import PitConflictError, _fact_row, as_of

CONTRACT = parse_data_contracts(
    [
        {
            "table": "income",
            "tier": "anchored",
            "primary_transport": "tushare:relay",
            "anchors": ["akshare_cninfo_announcement"],
            "conflict": "downgrade",
            "pit": {
                "as_of_field": "f_ann_date",
                "fallback": "ann_date",
                "fact_row_policy": "max_report_type_v1",
            },
            "coverage_shape": "per_symbol_window",
            "incremental": "disclosure_calendar",
        }
    ]
)["income"]


def _frame(rows):
    return pd.DataFrame(rows)


def test_lookahead_rows_are_invisible():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    value, warnings = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 29), contract=CONTRACT
    )
    assert value is None and warnings == []


def test_fact_row_visible_after_disclosure():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    value, warnings = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 30), contract=CONTRACT
    )
    assert value == 10.0 and warnings == []


def test_fallback_to_ann_date_emits_warning():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-28",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    value, warnings = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 29), contract=CONTRACT
    )
    assert value == 10.0
    assert any(w["code"] == "pit_fallback" for w in warnings)


def test_duplicate_identical_rows_deduplicate():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    value, warnings = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 30), contract=CONTRACT
    )
    assert value == 10.0 and warnings == []


def test_duplicate_conflicting_rows_fail():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 11.0},
        ]
    )
    with pytest.raises(PitConflictError):
        _fact_row(frame, "600000.SH", "revenue", date(2025, 3, 30), contract=CONTRACT)


def test_fact_row_policy_prefers_max_report_type():
    frame = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "2", "revenue": 12.0},
        ]
    )
    value, _ = _fact_row(
        frame, "600000.SH", "revenue", date(2025, 3, 30), contract=CONTRACT
    )
    assert value == 12.0


def test_as_of_reads_exact_pinned_context(tmp_path):
    from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
    from stock_quant.data_model.schemas import DAILY_COLUMNS
    from stock_quant.data_quality.models import QualityReport

    daily = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2025-01-02"]),
            "symbol": ["600000.SH"],
            "open": [10.0],
            "high": [10.5],
            "low": [9.5],
            "close": [10.2],
            "volume": [100],
            "amount": [1020.0],
            "adjustment": ["unadjusted"],
            "source": ["baostock"],
            "ingested_at": [pd.Timestamp("2025-01-02T08:00:00Z")],
        }
    )[DAILY_COLUMNS]
    # The synthetic table has no canonical schema yet, so publish only the
    # registered daily_bar (minimal publish pattern from
    # tests/integration/test_dataset_publish.py) and place the PIT frame in a
    # parquet beside it, registered in the version's manifest.  The context is
    # pinned by opening the exact version -- never CURRENT.
    ref = DatasetPublisher(tmp_path).publish({"daily_bar": daily}, QualityReport())

    income = _frame(
        [
            {"symbol": "600000.SH", "end_date": "2024-12-31",
             "f_ann_date": "2025-03-30", "ann_date": "2025-03-30",
             "report_type": "1", "revenue": 10.0},
        ]
    )
    income_path = ref.path / "income.parquet"
    income.to_parquet(income_path, index=False)
    manifest_file = ref.path / "dataset_manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["tables"]["income"] = {
        "path": "income.parquet",
        "row_count": len(income),
        "sha256": hashlib.sha256(income_path.read_bytes()).hexdigest(),
        "schema_version": "synthetic-income",
    }
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with DatasetReader(tmp_path).open(ref.version) as context:
        value, _ = as_of(
            context, "income", "600000.SH", "revenue", date(2025, 3, 30),
            contract=CONTRACT,
        )
    assert value == 10.0


def test_pit_module_never_reads_current():
    source = (Path(__file__).resolve().parents[2] / "src" / "stock_quant"
              / "research" / "pit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "current":
            pytest.fail("pit.py must never read CURRENT (criterion 4)")
