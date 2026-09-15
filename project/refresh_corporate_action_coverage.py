"""Refresh only corporate-action evidence and publish a new dataset version.

Status: migration.

Usage:
    PYTHONPATH=../src conda run -n py310 python refresh_corporate_action_coverage.py
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.universe import Universe
from stock_quant.data_pipeline import DataPipeline
from stock_quant.data_quality.models import QualityIssue, QualityReport, Severity
from stock_quant.project_root import resolve_project_root


def _report(path: Path) -> QualityReport:
    records = json.loads(path.read_text(encoding="utf-8"))["issues"]
    return QualityReport(
        issues=tuple(
            QualityIssue(
                severity=Severity(row["severity"]),
                code=row["code"],
                table=row["table"],
                symbol=row["symbol"],
                trade_date=(date.fromisoformat(row["trade_date"])
                            if row["trade_date"] else None),
                details=row["details"],
            )
            for row in records
        )
    )


def run(root: Path, config: ProjectConfig) -> int:
    reader = DatasetReader(root)
    current = DatasetPublisher(root).current()
    with reader.open(current.version) as context:
        tables = {name: context.read(name) for name in context.tables}

    coverage = tables["corporate_action_coverage"]
    start = coverage["window_start"].min().date()
    end = coverage["window_end"].max().date()
    pipeline = DataPipeline(root)
    symbols = Universe.from_yaml(root / "configs" / "universe.yml").symbols
    issues: list[QualityIssue] = []
    raw_snapshots: list[str] = []
    actions, refreshed_coverage = pipeline._refresh_corporate_actions(
        {"akshare"},
        tuple(symbols),
        start,
        end,
        issues,
        {},
        raw_snapshots,
        tables["corporate_action"],
    )
    tables["corporate_action"] = actions
    tables["corporate_action_coverage"] = refreshed_coverage
    report = _report(current.path / "quality_report.json")
    report = QualityReport(issues=report.issues + tuple(issues))
    published = DatasetPublisher(root).publish(
        tables,
        report,
        build_config={
            "refresh": "corporate_action_coverage",
            "parent_dataset": current.version,
            "raw_snapshots": sorted(raw_snapshots),
        },
    )
    print(f"dataset_version={published.version}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config)


if __name__ == "__main__":
    raise SystemExit(main())
