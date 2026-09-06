"""Canonical security-code normalization tests."""

import pytest

from stock_quant.data_model.symbols import SymbolNormalizationError, normalize_symbol


def test_symbols_are_canonical():
    assert normalize_symbol("sh.600000", "baostock") == "600000.SH"
    assert normalize_symbol("000001.SZ", "tushare") == "000001.SZ"


def test_baostock_shenzhen_code_becomes_sz_suffix():
    assert normalize_symbol("sz.000001", "baostock") == "000001.SZ"


def test_prefixed_and_suffixed_forms_work_for_either_source():
    assert normalize_symbol("sh.600000", "tushare") == "600000.SH"
    assert normalize_symbol("600000.SH", "baostock") == "600000.SH"


def test_symbol_is_trimmed_and_suffix_uppercased():
    assert normalize_symbol(" 000001.sz ", "tushare") == "000001.SZ"


def test_unrecognized_symbol_raises_without_guessing():
    with pytest.raises(SymbolNormalizationError):
        normalize_symbol("not-a-code", "baostock")
    with pytest.raises(SymbolNormalizationError):
        normalize_symbol("60000", "tushare")
