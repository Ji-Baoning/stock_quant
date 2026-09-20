"""Endpoint descriptors drive frame checks for new data lanes (spec D4).

Every endpoint declares its parameter shape, window-slicing needs, retry
categories, field projection and cadence; the framework then generates the
frame checks, the truncation guard and a contract-test skeleton — what stays
hand-written is normalize + verification logic (A4: the first new table is
hand-written as the generator's reference baseline).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

Keying = Literal["date_keyed", "symbol_keyed", "index_keyed"]
_RETRY_CATEGORIES = frozenset({"tls_eof", "timeout", "rate_limit"})

#: The silent-truncation threshold observed in production (spec D4).
TRUNCATION_ROW_LIMIT = 6000


class TruncationDetected(RuntimeError):
    """A frame hit the truncation limit without ``auto_slice`` declared.

    Fail-closed by default: this endpoint's round fails and leaves evidence
    rather than silently publishing a truncated view (spec D4).
    """


@dataclass(frozen=True)
class EndpointDescriptor:
    """One endpoint's declared shape (spec D4)."""

    endpoint: str
    keying: Keying
    required_params: tuple[str, ...] = ()
    auto_slice: bool = False
    retry_categories: frozenset[str] = frozenset()
    truncation_limit: int = TRUNCATION_ROW_LIMIT
    projection: tuple[str, ...] = ()
    cadence: str = "daily"

    def __post_init__(self) -> None:
        unknown = set(self.retry_categories) - _RETRY_CATEGORIES
        if unknown:
            raise ValueError(
                f"unknown retry categories {sorted(unknown)!r}; "
                f"allowed: {sorted(_RETRY_CATEGORIES)}"
            )
        if self.truncation_limit <= 0:
            raise ValueError("truncation_limit must be positive")


def check_frame(
    descriptor: EndpointDescriptor, frame: pd.DataFrame
) -> list[dict]:
    """Frame-level checks: truncation guard + declared projection."""
    issues: list[dict] = []
    if len(frame) >= descriptor.truncation_limit and not descriptor.auto_slice:
        raise TruncationDetected(
            f"{descriptor.endpoint}: {len(frame)} rows reach the "
            f"truncation limit without auto_slice"
        )
    if descriptor.projection:
        missing = [
            column for column in descriptor.projection if column not in frame.columns
        ]
        if missing:
            issues.append({"code": "projection_missing", "columns": missing})
    return issues


def slice_plan(
    descriptor: EndpointDescriptor, frame: pd.DataFrame
) -> list[tuple[int, int]]:
    """Chunk boundaries for an ``auto_slice`` endpoint (provenance-recorded)."""
    if not descriptor.auto_slice:
        raise ValueError("slice_plan requires auto_slice: true")
    limit = descriptor.truncation_limit
    total = len(frame)
    return [(start, min(start + limit, total)) for start in range(0, total, limit)]


def contract_test_skeleton(descriptor: EndpointDescriptor) -> str:
    """A pytest skeleton asserting the declared shape (spec D4)."""
    return (
        "# Generated from the endpoint descriptor; extend, do not weaken.\n"
        "import pytest\n\n"
        f"from stock_quant.data_sources.descriptors import EndpointDescriptor\n\n"
        f"DESCRIPTOR = EndpointDescriptor(\n"
        f'    endpoint={descriptor.endpoint!r},\n'
        f'    keying={descriptor.keying!r},\n'
        f"    required_params={descriptor.required_params!r},\n"
        f"    auto_slice={descriptor.auto_slice!r},\n"
        f"    retry_categories=frozenset({sorted(descriptor.retry_categories)!r}),\n"
        f"    projection={descriptor.projection!r},\n"
        f"    cadence={descriptor.cadence!r},\n"
        ")\n\n"
        "@pytest.fixture\ndef frame():\n"
        "    ...  # fetch one real response and normalize it\n\n"
        "def test_descriptor_shape(frame):\n"
        "    assert not check_frame(DESCRIPTOR, frame)\n\n"
        "def test_projection_present(frame):\n"
        "    assert set(DESCRIPTOR.projection) <= set(frame.columns)\n"
    )
