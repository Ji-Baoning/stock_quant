"""Publish the baseline dataset required before the first data update."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_model.schemas import (
    ADJUSTED_BAR_COLUMNS,
    CORPORATE_ACTION_COLUMNS,
    CORPORATE_ACTION_QUARANTINE_COLUMNS,
    DAILY_COLUMNS,
    SECURITY_MASTER_COLUMNS,
    TRADING_CALENDAR_COLUMNS,
)
from stock_quant.data_model.security_master import ListStatus
from stock_quant.data_model.universe import Universe
from stock_quant.data_quality.models import QualityReport


@dataclass(frozen=True)
class BootstrapResult:
    version: str
    symbol_count: int
    trading_day_count: int
    start: date
    end: date


def bootstrap_dataset(
    root: Path, *, calendar_csv: Path | None = None
) -> BootstrapResult:
    """Publish empty market tables plus the configured universe and calendar."""
    root = Path(root).resolve()
    config_dir = root / "configs"
    for name in ("project.yml", "universe.yml"):
        if not (config_dir / name).is_file():
            raise FileNotFoundError(f"missing {config_dir / name}")
    project = yaml.safe_load(
        (config_dir / "project.yml").read_text(encoding="utf-8")
    ) or {}
    start, end = _as_date(project["start_date"]), _as_date(project["end_date"])
    if end < start:
        raise ValueError(f"project.yml end_date {end} precedes start_date {start}")
    universe = Universe.from_yaml(config_dir / "universe.yml")
    days = _calendar_days(calendar_csv, start, end)
    if not days:
        raise ValueError("no trading days in range; check --calendar-csv / dates")
    tables = {
        "daily_bar": _empty_daily(),
        "adjusted_bar": _empty_adjusted_bar(),
        "security_master": _security_master(universe),
        "corporate_action": _empty_corporate_action(),
        "corporate_action_quarantine": _empty_quarantine(),
        "trading_calendar": _trading_calendar(days),
    }
    version = DatasetPublisher(root).publish(tables, QualityReport()).version
    return BootstrapResult(version, len(universe.entries), len(days), start, end)


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _calendar_days(path: Path | None, start: date, end: date) -> list[date]:
    if path is not None:
        return sorted(
            {
                date.fromisoformat(line.split()[0])
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
            & {start + timedelta(days=i) for i in range((end - start).days + 1)}
        )
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _security_master(universe: Universe) -> pd.DataFrame:
    entries = universe.entries
    count = len(entries)
    return pd.DataFrame(
        {
            "symbol": [entry.symbol for entry in entries],
            "name": [entry.name_at_selection for entry in entries],
            "exchange": [entry.exchange for entry in entries],
            "board": [entry.board for entry in entries],
            "list_date": pd.Series(
                pd.NaT, index=range(count), dtype="datetime64[ns]"
            ),
            "delist_date": pd.Series(
                pd.NaT, index=range(count), dtype="datetime64[ns]"
            ),
            "list_status": [ListStatus.NOT_APPLIED.value] * count,
        }
    )[SECURITY_MASTER_COLUMNS]


def _trading_calendar(days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        {"calendar_date": pd.to_datetime(days), "is_trading_day": [True] * len(days)}
    )[TRADING_CALENDAR_COLUMNS]


def _empty_daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.Series(dtype="datetime64[ns]"),
            "symbol": pd.Series(dtype="object"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
            "amount": pd.Series(dtype="float64"),
            "adjustment": pd.Series(dtype="object"),
            "source": pd.Series(dtype="object"),
            "ingested_at": pd.Series(dtype="datetime64[ns]"),
        }
    )[DAILY_COLUMNS]


def _empty_corporate_action() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series(dtype="object"),
            "announcement_date": pd.Series(dtype="object"),
            "record_date": pd.Series(dtype="object"),
            "ex_date": pd.Series(dtype="object"),
            "cash_dividend_per_share": pd.Series(dtype="float64"),
            "bonus_share_ratio": pd.Series(dtype="float64"),
            "capitalization_ratio": pd.Series(dtype="float64"),
            "rights_issue_ratio": pd.Series(dtype="float64"),
            "rights_issue_price": pd.Series(dtype="float64"),
            "source": pd.Series(dtype="object"),
            "status": pd.Series(dtype="object"),
        }
    )[CORPORATE_ACTION_COLUMNS]


def _empty_adjusted_bar() -> pd.DataFrame:
    """The canonical empty total-return table (no bars to adjust yet)."""
    return pd.DataFrame(columns=ADJUSTED_BAR_COLUMNS)


def _empty_quarantine() -> pd.DataFrame:
    """The canonical empty corporate-action quarantine table."""
    return pd.DataFrame(columns=CORPORATE_ACTION_QUARANTINE_COLUMNS)
