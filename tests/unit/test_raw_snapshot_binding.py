"""RawSnapshotBinding must read both payload shapes (design §2.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stock_quant.research.acceptance.models import RawSnapshotBinding

DIGEST = "0" * 64


def test_a_five_field_binding_still_validates():
    binding = RawSnapshotBinding(
        source="tushare",
        endpoint="daily",
        request_key="rk",
        file_sha256=DIGEST,
        manifest_sha256=DIGEST,
    )
    assert binding.transport_id is None


def test_a_current_binding_carries_the_transport_id():
    binding = RawSnapshotBinding(
        source="tushare",
        endpoint="daily",
        request_key="rk",
        file_sha256=DIGEST,
        manifest_sha256=DIGEST,
        transport_id="jiaoch.top",
    )
    assert binding.transport_id == "jiaoch.top"


def test_an_unknown_field_is_still_rejected():
    with pytest.raises(ValidationError):
        RawSnapshotBinding(
            source="tushare",
            endpoint="daily",
            request_key="rk",
            file_sha256=DIGEST,
            manifest_sha256=DIGEST,
            provenance_key="jiaoch.top",
        )
