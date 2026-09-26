"""A configured source is registered everywhere or it crashes a run."""

from __future__ import annotations

from stock_quant.config import SourceConfig
from stock_quant.data_model.normalize import _UNIT_FACTORS
from stock_quant.data_pipeline import (
    _CONFIGURED_SOURCES,
    _REQUIRED_ROLE,
    _build_source,
)
from stock_quant.data_quality.raw_checks import KNOWN_SUPPLIERS


def test_xingyao_is_a_configured_optional_source():
    assert "xingyao" in _CONFIGURED_SOURCES
    assert _REQUIRED_ROLE["xingyao"] is False


def test_every_configured_source_declares_a_required_role():
    """A name without a role raises KeyError while the run is being set up."""
    assert set(_CONFIGURED_SOURCES) == set(_REQUIRED_ROLE)


def test_every_configured_source_is_buildable():
    for name in _CONFIGURED_SOURCES:
        if name == "xingyao":
            continue  # needs the private SDK; covered by its own adapter test
        assert _build_source(name, SourceConfig()).name == name


def test_xingyao_is_a_documented_supplier_with_declared_units():
    assert "xingyao" in KNOWN_SUPPLIERS
    assert _UNIT_FACTORS["xingyao"] == (1, 1)


def test_an_unknown_source_is_refused():
    import pytest

    with pytest.raises(ValueError, match="unknown configured source"):
        _build_source("not_a_source", SourceConfig())
