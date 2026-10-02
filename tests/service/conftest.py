"""Offline fixtures for the read-only query-service tests.

One fixture project per test: template configs, an 11-table dataset published
through the production :class:`DatasetPublisher`, one published experiment
with a static report, and helpers for real content-addressed acceptance
records -- all under ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS, DatasetPublisher
from stock_quant.data_quality.models import QualityIssue, QualityReport, Severity
from stock_quant.research.acceptance.models import (
    AUTOMATED_CHECK_CODES,
    MANUAL_CHECK_CODES,
    POLICY_VERSION,
    AcceptanceDecision,
    AcceptanceRecord,
    CheckResult,
    CheckStatus,
    ManualCheckResult,
    ManualCheckStatus,
    compute_acceptance_id,
)
from stock_quant.research.acceptance.registry import AcceptanceRegistry
from stock_quant.service.app import create_app

_TEMPLATE_CONFIG = Path(__file__).resolve().parents[2] / "templates" / "project-config"

SYMBOLS = ("600000.SH", "600004.SH", "600006.SH")
DATES = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8))
INGESTED_AT = datetime(2026, 1, 9, 1, 2, 3, tzinfo=timezone.utc)


def write_configs(root: Path) -> None:
    """Materialise the three config files a valid project root requires."""
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name in ("project.yml", "sources.yml", "costs.yml"):
        (config_dir / name).write_text(
            (_TEMPLATE_CONFIG / name).read_text(encoding="utf-8"), encoding="utf-8"
        )


def canonical_frame(table: str, rows: list[dict]) -> pd.DataFrame:
    """A frame whose columns match the canonical schema order exactly."""
    columns = [field.name for field in STANDARDIZED_SCHEMAS[table]]
    return pd.DataFrame(rows, columns=columns)


def bar_rows(symbols: tuple[str, ...]) -> list[dict]:
    return [
        {
            "trade_date": trade_date,
            "symbol": symbol,
            "open": 10.0 + index,
            "high": 11.0 + index,
            "low": 9.0 + index,
            "close": 10.5 + index,
            "volume": 1_000 + index,
            "amount": 10_500.0 + index,
            "adjustment": "none",
            "source": "fixture",
            "ingested_at": INGESTED_AT,
        }
        for trade_date in DATES
        for index, symbol in enumerate(symbols)
    ]


def canonical_tables(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """All eleven canonical tables, so the fixture survives any future
    publish-time registry-completeness gate."""
    bars = bar_rows(symbols)
    return {
        "daily_bar": canonical_frame("daily_bar", bars),
        "adjusted_bar": canonical_frame(
            "adjusted_bar",
            [
                {
                    "trade_date": row["trade_date"],
                    "symbol": row["symbol"],
                    "source": row["source"],
                    "adjustment": row["adjustment"],
                    "raw_close": row["close"],
                    "adjusted_close": row["close"],
                    "adjustment_factor": 1.0,
                    "quality_severity": "INFO",
                    "invalid_reason": None,
                    "applied_action_ids": "[]",
                }
                for row in bars
            ],
        ),
        "security_master": canonical_frame(
            "security_master",
            [
                {
                    "symbol": symbol,
                    "name": f"fixture {symbol}",
                    "exchange": "SSE",
                    "board": "main",
                    "list_date": date(2020, 1, 2),
                    "delist_date": None,
                    "list_status": "L",
                }
                for symbol in symbols
            ],
        ),
        "security_master_coverage": canonical_frame(
            "security_master_coverage",
            [
                {
                    "symbol": symbol,
                    "list_date": date(2020, 1, 2),
                    "delist_date": None,
                    "list_status": "L",
                    "source": "fixture",
                    "snapshot_sha256": "0" * 64,
                    "sdk_version": "fixture",
                    "checked_at": INGESTED_AT,
                }
                for symbol in symbols
            ],
        ),
        "corporate_action": canonical_frame("corporate_action", []),
        "corporate_action_quarantine": canonical_frame(
            "corporate_action_quarantine", []
        ),
        "corporate_action_coverage": canonical_frame("corporate_action_coverage", []),
        "basic_factor": canonical_frame(
            "basic_factor",
            [
                {
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "market_cap": 1_000_000.0 + index,
                    "turnover_rate": 0.01 + index / 100,
                    "source": "fixture",
                    "ingested_at": INGESTED_AT,
                }
                for trade_date in DATES
                for index, symbol in enumerate(symbols)
            ],
        ),
        "basic_factor_coverage": canonical_frame("basic_factor_coverage", []),
        "trading_calendar": canonical_frame(
            "trading_calendar",
            [
                {"calendar_date": trade_date, "is_trading_day": True}
                for trade_date in DATES
            ],
        ),
        "universe_membership": canonical_frame("universe_membership", []),
    }


def fixture_quality_report() -> QualityReport:
    """Three non-blocking WARNING issues so the quality view has content."""
    return QualityReport(
        issues=tuple(
            QualityIssue(
                severity=Severity.WARNING,
                code="within_tolerance",
                table="daily_bar",
                symbol=symbol,
                trade_date=DATES[0],
                details={"diff": 0.01},
            )
            for symbol in SYMBOLS
        )
    )


def publish_version(root: Path, symbols: tuple[str, ...]) -> str:
    return DatasetPublisher(root).publish(
        canonical_tables(symbols), fixture_quality_report()
    ).version


def publish_experiment(
    root: Path,
    dataset_version: str,
    experiment_id: str = "e" * 64,
    *,
    with_report: bool = True,
) -> Path:
    directory = root / "data" / "experiments" / experiment_id
    directory.mkdir(parents=True)
    (directory / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "status": "ACCEPTED",
                "dataset_version": dataset_version,
                "universe_version": "u" * 64,
                "evaluation_reason": None,
            }
        ),
        encoding="utf-8",
    )
    if with_report:
        (directory / "report.html").write_text(
            "<html><body>fixture report</body></html>", encoding="utf-8"
        )
    return directory


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_acceptance_record(
    root: Path,
    version: str,
    *,
    decision: str,
    created_at: datetime,
    reasons: tuple[str, ...] = (),
    policy_version: str = POLICY_VERSION,
) -> None:
    """Publish one real, content-addressed acceptance record for ``version``.

    Tests may import the heavy acceptance package; the *service* may not
    (its ``__init__`` transitively imports ``stock_quant.data_pipeline``).
    """
    version_dir = root / "data" / "standardized" / version
    draft = AcceptanceRecord(
        policy_version=policy_version,
        acceptance_id="0" * 64,
        dataset_version=version,
        dataset_manifest_sha256=sha256_of(version_dir / "dataset_manifest.json"),
        quality_report_sha256=sha256_of(version_dir / "quality_report.json"),
        created_at=created_at,
        operator_id="fixture-operator",
        automated_checks=tuple(
            CheckResult(code=code, status=CheckStatus.PASS, summary="fixture pass")
            for code in AUTOMATED_CHECK_CODES
        ),
        manual_checks=tuple(
            ManualCheckResult(
                code=code, status=ManualCheckStatus.PASS, summary="fixture pass"
            )
            for code in MANUAL_CHECK_CODES
        ),
        raw_snapshot_evidence=(),
        decision=AcceptanceDecision(decision),
        reasons=reasons,
    )
    record = draft.model_copy(update={"acceptance_id": compute_acceptance_id(draft)})
    AcceptanceRegistry(root).publish(record)


@pytest.fixture()
def service_project(tmp_path: Path) -> Path:
    write_configs(tmp_path)
    version = publish_version(tmp_path, SYMBOLS)
    publish_experiment(tmp_path, version)
    return tmp_path


@pytest.fixture()
def current_version(service_project: Path) -> str:
    return (service_project / "data" / "standardized" / "CURRENT").read_text(
        encoding="utf-8"
    ).strip()


@pytest.fixture()
def client(service_project: Path) -> TestClient:
    return TestClient(create_app(service_project))
