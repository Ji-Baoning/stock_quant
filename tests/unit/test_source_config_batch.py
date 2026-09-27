"""The two batch field pairs (spec §5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stock_quant.config import SourceConfig


def test_both_pairs_default_off():
    config = SourceConfig()
    assert config.batch_size is None
    assert config.batch_timeout_seconds is None
    assert config.factor_batch_size is None
    assert config.factor_batch_timeout_seconds is None


def test_a_pair_may_be_configured_together():
    config = SourceConfig(batch_size=1000, batch_timeout_seconds=180)
    assert config.batch_size == 1000
    assert config.batch_timeout_seconds == 180


def test_a_size_without_its_timeout_is_a_configuration_error():
    with pytest.raises(ValidationError, match="batch_timeout_seconds"):
        SourceConfig(batch_size=1000)


def test_a_timeout_without_its_size_is_a_configuration_error():
    with pytest.raises(ValidationError, match="batch_size"):
        SourceConfig(batch_timeout_seconds=180)


def test_the_factor_pair_is_validated_independently():
    """Endpoints are probed separately; one pair never configures the other."""
    with pytest.raises(ValidationError, match="factor_batch_timeout_seconds"):
        SourceConfig(factor_batch_size=200)


def test_batch_sizes_must_be_positive():
    with pytest.raises(ValidationError):
        SourceConfig(batch_size=0, batch_timeout_seconds=180)
