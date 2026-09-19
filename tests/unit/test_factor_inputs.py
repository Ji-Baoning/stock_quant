# tests/unit/test_factor_inputs.py
"""Every factor declares its input tables (spec A2/F4)."""

from __future__ import annotations

from typing import get_type_hints

from stock_quant.factors.base import Factor
from stock_quant.factors.momentum import Momentum60


def test_protocol_requires_inputs_declaration():
    annotations = Factor.__annotations__
    assert "inputs" in annotations
    # ``base.py`` uses postponed annotations (PEP 563), so the stored value is
    # a string; resolve hints like test_factor_contract.py does.
    assert get_type_hints(Factor)["inputs"] == tuple[str, ...]


def test_momentum60_declares_adjusted_bar():
    assert Momentum60.inputs == ("adjusted_bar",)
