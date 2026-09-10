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


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["SZ000001", "SZ000780", "SH600000"],
            "name": ["平安银行", "平庄能源", "浦发银行"],
            "opt-in": pd.to_datetime(["2005-04-08", "2005-04-08", "2005-04-08"]),
            "opt-out": pd.to_datetime([None, "2013-12-16", "2007-04-30"]),
        }
    )


def _repairs(**overrides) -> pd.DataFrame:
    row = {
        "symbol": "SZ000780",
        "action": "set_field",
        "field": "opt-out",
        "old_value": "2013-12-16",
        "new_value": "2006-08-14",
        "evidence_tier": "A",
        "evidence_source": "data/raw/csi/csi_index_announcements/85.json",
        "evidence_detail": "官方公告：2006-08-15 起调出 000780 草原兴发",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_read_repairs_returns_the_declared_columns(tmp_path: Path):
    module = _load_build_module()
    path = tmp_path / "repairs.csv"
    path.write_text(
        "symbol,action,field,old_value,new_value,evidence_tier,"
        "evidence_source,evidence_detail\n",
        encoding="utf-8",
    )
    repairs = module.read_repairs(path)
    assert list(repairs.columns) == list(module.REPAIR_COLUMNS)
    assert repairs.empty


def test_empty_repairs_leave_the_history_untouched():
    module = _load_build_module()
    history = _history()
    result = module.apply_repairs(
        history, pd.DataFrame(columns=list(module.REPAIR_COLUMNS))
    )
    pd.testing.assert_frame_equal(result, history)


def test_set_field_replaces_the_matching_value():
    module = _load_build_module()
    result = module.apply_repairs(_history(), _repairs())
    row = result[result["symbol"] == "SZ000780"].iloc[0]
    assert row["opt-out"] == pd.Timestamp("2006-08-14")
    assert len(result) == 3


def test_set_field_does_not_mutate_the_input_frame():
    module = _load_build_module()
    history = _history()
    module.apply_repairs(history, _repairs())
    assert history.loc[history["symbol"] == "SZ000780", "opt-out"].iloc[0] == (
        pd.Timestamp("2013-12-16")
    )


def test_set_field_with_a_stale_old_value_sets_nothing():
    """A repair whose old_value no longer matches must not silently apply."""
    module = _load_build_module()
    result = module.apply_repairs(
        _history(), _repairs(old_value="1999-01-01")
    )
    row = result[result["symbol"] == "SZ000780"].iloc[0]
    assert row["opt-out"] == pd.Timestamp("2013-12-16")


def test_drop_row_removes_the_matching_interval():
    module = _load_build_module()
    result = module.apply_repairs(
        _history(),
        _repairs(action="drop_row", field="opt-in", old_value="2005-04-08"),
    )
    assert "SZ000780" not in set(result["symbol"])


def test_insert_row_appends_a_new_interval():
    module = _load_build_module()
    result = module.apply_repairs(
        _history(),
        _repairs(
            symbol="SZ002558",
            action="insert_row",
            field="opt-in",
            old_value="2026-06-12",
            new_value="",
        ),
    )
    assert "SZ002558" in set(result["symbol"])
    assert len(result) == 4


def test_unknown_action_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(action="frobnicate"))
    assert "frobnicate" in str(excinfo.value)


def test_unknown_field_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(field="name"))
    assert "name" in str(excinfo.value)


def test_unknown_evidence_tier_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(evidence_tier="C"))
    assert "C" in str(excinfo.value)


def test_set_field_on_an_absent_symbol_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(symbol="SZ999999"))
    assert "SZ999999" in str(excinfo.value)


def test_drop_row_with_a_stale_old_value_sets_nothing():
    """A stale old_value is a no-op for drop_row too, not only set_field."""
    module = _load_build_module()
    result = module.apply_repairs(
        _history(),
        _repairs(action="drop_row", field="opt-in", old_value="1999-01-01"),
    )
    assert len(result) == 3
    assert "SZ000780" in set(result["symbol"])


def test_drop_row_on_an_absent_symbol_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(action="drop_row", symbol="SZ999999"))
    assert "SZ999999" in str(excinfo.value)


def test_missing_evidence_source_is_rejected():
    module = _load_build_module()
    with pytest.raises(ValueError) as excinfo:
        module.apply_repairs(_history(), _repairs(evidence_source=""))
    assert "SZ000780" in str(excinfo.value)


def test_repair_old_value_format_is_normalized():
    """A repair's date may be written in any common format and still match.

    Normalizing only the frame side would leave a mistyped or differently
    formatted ``old_value`` matching nothing -- indistinguishable from a
    genuinely stale repair -- so the correction would silently never apply.
    """
    module = _load_build_module()
    for old_value in ("2013/12/16", "2013-12-16 00:00:00"):
        result = module.apply_repairs(_history(), _repairs(old_value=old_value))
        row = result[result["symbol"] == "SZ000780"].iloc[0]
        assert row["opt-out"] == pd.Timestamp("2006-08-14")
