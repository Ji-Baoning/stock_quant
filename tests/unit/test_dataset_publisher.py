"""Unit behaviour of the publisher's registered-table completeness gate.

Registry completeness (spec §7.5.6) is a *publication* obligation: a publish
that omits any currently registered standardized table must be refused with
``missing_registered_table`` folded into the gated report, so every caller --
``data update`` and the offline ``project/*.py`` publishers alike -- is
covered without re-reviewing old versions against a newer registry.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_quant.data_model.dataset import (
    DatasetNotFoundError,
    DatasetPublisher,
    PublicationBlocked,
    _with_registered_table_issues,
)
from stock_quant.data_model.schemas import DAILY_COLUMNS
from stock_quant.data_quality.models import QualityReport, issue_dict_dumps

TRADING_DAYS = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]
INGESTED = pd.Timestamp("2020-01-06T08:00:00Z")


def _daily_frame() -> pd.DataFrame:
    closes = [10.5, 10.8, 11.0]
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


def test_publishing_without_every_registered_table_is_fatal(tmp_path):
    """A publish omitting a registered table is blocked, naming the table.

    A blocked publish leaves nothing behind; the folded report -- the same
    object publish persists as ``quality_report.json`` once a publish gets
    past the gate -- carries the fatal code for every absent table.
    """
    publisher = DatasetPublisher(tmp_path)
    with pytest.raises(PublicationBlocked, match="missing_registered_table"):
        publisher.publish({"daily_bar": _daily_frame()}, QualityReport())
    with pytest.raises(DatasetNotFoundError):
        publisher.current()

    folded = _with_registered_table_issues(
        QualityReport(), {"daily_bar": _daily_frame()}
    )
    dumped = issue_dict_dumps(folded)
    assert "missing_registered_table" in dumped
    assert "adjusted_bar" in dumped

    # A publish covering the whole registry folds nothing into the report.
    from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS

    whole = _with_registered_table_issues(
        QualityReport(), {name: None for name in STANDARDIZED_SCHEMAS}
    )
    assert whole.issues == ()


def _differing_daily_frame() -> pd.DataFrame:
    frame = _daily_frame()
    frame["close"] = [11.0, 11.3, 11.5]
    frame["high"] = [11.5, 11.8, 12.0]
    frame["low"] = [10.5, 10.8, 11.0]
    return frame


def _full_registry_tables(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """One publishable payload: the named daily bars plus empty siblings."""
    from stock_quant.data_model.dataset import STANDARDIZED_SCHEMAS

    tables = {
        name: pd.DataFrame(columns=list(schema.names))
        for name, schema in STANDARDIZED_SCHEMAS.items()
    }
    tables["daily_bar"] = frame
    return tables


def test_publish_without_promotion_parks_the_version_and_keeps_current(tmp_path):
    """``promote=False`` parks a version directory but never moves CURRENT.

    The membership refresh (spec 7.0.10) parks the replacement dataset while
    it appends the frozen definition to the registry; only the committed
    generation-pointer swap may promote it.
    """
    publisher = DatasetPublisher(tmp_path)
    old = publisher.publish(_full_registry_tables(_daily_frame()), QualityReport())
    parked = publisher.publish(
        _full_registry_tables(_differing_daily_frame()),
        QualityReport(),
        promote=False,
    )
    assert parked.version != old.version
    assert (tmp_path / "data" / "standardized" / parked.version).is_dir()
    assert publisher.current().version == old.version


def test_promote_moves_current_to_a_parked_version(tmp_path):
    """``promote`` points CURRENT at an already-published version directory."""
    publisher = DatasetPublisher(tmp_path)
    old = publisher.publish(_full_registry_tables(_daily_frame()), QualityReport())
    parked = publisher.publish(
        _full_registry_tables(_differing_daily_frame()),
        QualityReport(),
        promote=False,
    )
    assert publisher.current().version == old.version
    publisher.promote(parked.version)
    assert publisher.current().version == parked.version


def test_promote_refuses_a_version_that_is_not_on_disk(tmp_path):
    """A ``promote`` target must be an existing dataset directory."""
    publisher = DatasetPublisher(tmp_path)
    publisher.publish(_full_registry_tables(_daily_frame()), QualityReport())
    with pytest.raises(DatasetNotFoundError, match="cannot promote"):
        publisher.promote("deadbeef")
