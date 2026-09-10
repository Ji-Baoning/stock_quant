"""The csi300 build turns an index-constitution snapshot into validated facts.

Loaded by path because ``project/`` is not a package (repo convention).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_build_module():
    path = REPO_ROOT / "project" / "build_csi300_universe.py"
    spec = importlib.util.spec_from_file_location("build_csi300_universe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _history_with_nat() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["SZ000001", "SH600000", "SH600501"],
            "name": ["平安银行", "浦发银行", "航天晨光"],
            "opt-in": pd.to_datetime(["2005-04-08", "2005-04-08", None]),
            "opt-out": pd.to_datetime([None, "2007-04-30", "2008-06-14"]),
        }
    )


def test_to_canonical_symbol_reverses_the_exchange_prefix():
    module = _load_build_module()
    assert module.to_canonical_symbol("SZ000001") == "000001.SZ"
    assert module.to_canonical_symbol("SH600000") == "600000.SH"


def test_to_canonical_symbol_rejects_unknown_input():
    module = _load_build_module()
    with pytest.raises(ValueError):
        module.to_canonical_symbol("000001.SZ")
    with pytest.raises(ValueError):
        module.to_canonical_symbol("BJ430047")


def test_membership_rows_marks_the_open_base_cohort_as_initial():
    module = _load_build_module()
    rows = module.membership_rows(
        pd.DataFrame(
            {
                "symbol": ["SZ000001"],
                "name": ["平安银行"],
                "opt-in": pd.to_datetime(["2005-04-08"]),
                "opt-out": pd.to_datetime([None]),
            }
        )
    )
    assert rows.iloc[0]["symbol"] == "000001.SZ"
    assert rows.iloc[0]["reason"] == "initial_constituent"
    assert pd.isna(rows.iloc[0]["raw_effective_to"])


def test_membership_rows_marks_later_entries_as_regular_rebalance():
    module = _load_build_module()
    rows = module.membership_rows(
        pd.DataFrame(
            {
                "symbol": ["SH601006"],
                "name": ["大秦铁路"],
                "opt-in": pd.to_datetime(["2006-08-12"]),
                "opt-out": pd.to_datetime([None]),
            }
        )
    )
    assert rows.iloc[0]["reason"] == "regular_rebalance"


def test_membership_rows_never_marks_a_removed_row_initial():
    module = _load_build_module()
    rows = module.membership_rows(
        pd.DataFrame(
            {
                "symbol": ["SH600000"],
                "name": ["浦发银行"],
                "opt-in": pd.to_datetime(["2005-04-08"]),
                "opt-out": pd.to_datetime(["2007-04-30"]),
            }
        )
    )
    assert rows.iloc[0]["reason"] == "regular_rebalance"


def test_membership_rows_sets_announcement_date_to_the_effective_date():
    module = _load_build_module()
    rows = module.membership_rows(
        pd.DataFrame(
            {
                "symbol": ["SZ000001"],
                "name": ["平安银行"],
                "opt-in": pd.to_datetime(["2005-04-08"]),
                "opt-out": pd.to_datetime([None]),
            }
        )
    )
    assert rows.iloc[0]["announcement_date"] == pd.Timestamp("2005-04-08")


def test_membership_rows_rejects_a_missing_opt_in_instead_of_dropping_it():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.membership_rows(_history_with_nat())
    assert "SH600501" in str(excinfo.value)
