"""Immutable standardized-dataset publication and the DuckDB read path.

A blocked publication must preserve the prior CURRENT; published versions are
content-addressed and never rewritten; ``DatasetReader.open`` pins the exact
Parquet paths so changing CURRENT after open does not change queries.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_quant.data_model.dataset import (
    DatasetNotFoundError,
    DatasetPublisher,
    DatasetReader,
    PublicationBlocked,
)
from stock_quant.data_model.schemas import DAILY_COLUMNS, DAILY_SCHEMA
from stock_quant.data_quality.models import QualityIssue, QualityReport, Severity

TRADING_DAYS = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]
INGESTED = pd.Timestamp("2020-01-06T08:00:00Z")


def _daily_frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(TRADING_DAYS),
            "symbol": ["600000.SH"] * len(closes),
            "open": [c - 0.5 for c in closes],
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [100] * len(closes),
            "amount": [c * 100 for c in closes],
            "adjustment": ["unadjusted"] * len(closes),
            "source": ["baostock"] * len(closes),
            "ingested_at": [INGESTED] * len(closes),
        }
    )[DAILY_COLUMNS]


def valid_tables(closes: list[float] | None = None) -> dict[str, pd.DataFrame]:
    if closes is None:
        closes = [10.5, 10.8, 11.0]
    return {"daily_bar": _daily_frame(closes)}


def passing_report() -> QualityReport:
    return QualityReport()


def report_with_fatal(code: str) -> QualityReport:
    return QualityReport(
        issues=(
            QualityIssue(
                severity=Severity.FATAL,
                code=code,
                table="daily_bar",
                details={"count": 1},
            ),
        )
    )


def _standardized(tmp_path):
    return tmp_path / "data" / "standardized"


def test_fatal_quality_does_not_move_current(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    good = publisher.publish(valid_tables(), passing_report())
    with pytest.raises(PublicationBlocked):
        publisher.publish(valid_tables(), report_with_fatal("duplicate_conflict"))
    assert publisher.current().version == good.version


def test_publish_writes_parquet_and_both_json_reports(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    ref = publisher.publish(valid_tables(), passing_report())
    version_dir = ref.path
    names = sorted(p.name for p in version_dir.iterdir())
    assert names == [
        "daily_bar.parquet",
        "dataset_manifest.json",
        "quality_report.json",
    ]
    manifest = json.loads((version_dir / "dataset_manifest.json").read_text())
    assert manifest["dataset_version"] == ref.version
    assert set(manifest["tables"]) == {"daily_bar"}
    assert manifest["tables"]["daily_bar"]["row_count"] == 3
    assert manifest["tables"]["daily_bar"]["sha256"]
    assert json.loads((version_dir / "quality_report.json").read_text())["issues"] == []


def test_published_daily_parquet_matches_canonical_schema(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    ref = publisher.publish(valid_tables(), passing_report())
    parquet = pq.ParquetFile(ref.path / "daily_bar.parquet")
    assert parquet.schema_arrow == DAILY_SCHEMA
    assert parquet.schema_arrow.field("trade_date").type == pa.date32()
    ingested = parquet.schema_arrow.field("ingested_at").type
    assert ingested == pa.timestamp("us", tz="UTC")
    table = pd.read_parquet(ref.path / "daily_bar.parquet")
    assert table.columns.tolist() == DAILY_COLUMNS
    assert table["trade_date"].tolist() == TRADING_DAYS
    assert table["close"].tolist() == [10.5, 10.8, 11.0]


def test_identical_publish_is_idempotent_and_never_rewrites(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    first = publisher.publish(valid_tables(), passing_report())
    files_before = {p.name: p.read_bytes() for p in first.path.iterdir()}
    second = publisher.publish(valid_tables(), passing_report())
    assert second.version == first.version
    assert second.path == first.path
    assert {p.name: p.read_bytes() for p in first.path.iterdir()} == files_before


def test_new_content_yields_new_version_and_old_version_is_intact(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    first = publisher.publish(valid_tables(), passing_report())
    old_bytes = {p.name: p.read_bytes() for p in first.path.iterdir()}
    second = publisher.publish(valid_tables([12.5, 12.8, 13.0]), passing_report())
    assert second.version != first.version
    assert {p.name: p.read_bytes() for p in first.path.iterdir()} == old_bytes


def test_current_tracks_the_latest_publish(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    first = publisher.publish(valid_tables(), passing_report())
    second = publisher.publish(valid_tables([12.5, 12.8, 13.0]), passing_report())
    assert publisher.current().version == second.version
    assert first.version != second.version


def test_blocked_publish_leaves_no_new_version_directory(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    good = publisher.publish(valid_tables(), passing_report())
    standardized = _standardized(tmp_path)
    before = sorted(p.name for p in standardized.iterdir())
    with pytest.raises(PublicationBlocked):
        publisher.publish(valid_tables(), report_with_fatal("schema_mismatch"))
    assert sorted(p.name for p in standardized.iterdir()) == before
    assert publisher.current().version == good.version


def test_staging_is_cleaned_after_publish_and_block(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    publisher.publish(valid_tables(), passing_report())
    assert not (tmp_path / "data" / "staging").exists() or not list(
        (tmp_path / "data" / "staging").iterdir()
    )
    with pytest.raises(PublicationBlocked):
        publisher.publish(valid_tables(), report_with_fatal("duplicate_conflict"))
    assert not (tmp_path / "data" / "staging").exists() or not list(
        (tmp_path / "data" / "staging").iterdir()
    )


def test_reader_is_pinned_to_opened_version_when_current_moves(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    first = publisher.publish(valid_tables(), passing_report())
    reader = DatasetReader(tmp_path)
    with reader.open(first.version) as context:
        before = context.read("daily_bar")
        assert before["close"].tolist() == [10.5, 10.8, 11.0]

        second = publisher.publish(valid_tables([12.5, 12.8, 13.0]), passing_report())
        assert publisher.current().version == second.version
        assert second.version != first.version

        after = context.read("daily_bar")
        pd.testing.assert_frame_equal(
            after.reset_index(drop=True), before.reset_index(drop=True)
        )
        assert after["close"].tolist() == [10.5, 10.8, 11.0]

    with DatasetReader(tmp_path).open(second.version) as current:
        assert current.read("daily_bar")["close"].tolist() == [12.5, 12.8, 13.0]


def test_reader_supports_sql_queries_over_the_pinned_version(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    ref = publisher.publish(valid_tables(), passing_report())
    with DatasetReader(tmp_path).open(ref.version) as context:
        result = context.query("SELECT count(*) AS n FROM daily_bar WHERE close > 10.6")
        assert int(result.iloc[0, 0]) == 2


def test_reader_connection_rejects_writes(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    ref = publisher.publish(valid_tables(), passing_report())
    with DatasetReader(tmp_path).open(ref.version) as context:
        with pytest.raises(Exception):
            context.connection.execute("CREATE TABLE bad (x INTEGER)")


def test_reader_open_unknown_version_raises(tmp_path):
    with pytest.raises(DatasetNotFoundError):
        DatasetReader(tmp_path).open("0" * 64)


def test_current_raises_before_any_publish(tmp_path):
    publisher = DatasetPublisher(tmp_path)
    with pytest.raises(DatasetNotFoundError):
        publisher.current()
