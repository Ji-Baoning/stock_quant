"""Rebuild the published calendar from benchmark sessions (no network access)."""
from pathlib import Path

import pandas as pd

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_quality.models import QualityReport

root = Path(__file__).resolve().parent
version = DatasetPublisher(root).current().version
with DatasetReader(root).open(version) as dataset:
    tables = {name: dataset.read(name) for name in (
        "daily_bar", "security_master", "corporate_action", "trading_calendar"
    )}
benchmarks = ("000300.SH", "000905.SH")
sessions = [
    set(pd.to_datetime(tables["daily_bar"].loc[
        tables["daily_bar"]["symbol"] == symbol, "trade_date"
    ]).dt.normalize())
    for symbol in benchmarks
]
dates = sorted(set.intersection(*sessions))
tables["trading_calendar"] = pd.DataFrame(
    {"calendar_date": dates, "is_trading_day": True}
)
published = DatasetPublisher(root).publish(tables, QualityReport())
print(f"dataset_version={published.version} sessions={len(dates)}")
