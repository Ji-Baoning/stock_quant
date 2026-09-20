"""D6 canary: official-channel probes, redacted records, no auto-fallback."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "project")
)

from verify_update_readiness import (  # noqa: E402
    OFFICIAL_CANARY_ENDPOINTS,
    render_canary_record,
)


def test_canary_endpoint_list_is_fixed():
    assert "daily" in OFFICIAL_CANARY_ENDPOINTS


def test_record_redacts_credentials():
    probes = {
        "daily": {
            "status": "unusable",
            "note": "AuthenticationError: token TUSHARE_TOKEN=SECRETVALUE999",
        }
    }
    rendered = render_canary_record("2026-09-19", probes)
    assert "SECRETVALUE999" not in rendered
    assert "TUSHARE_TOKEN" not in rendered
    assert "daily" in rendered


def test_record_reports_endpoint_shape_only():
    probes = {"daily": {"status": "reachable", "rows": 200}}
    rendered = render_canary_record("2026-09-19", probes)
    assert "reachable" in rendered and "200" in rendered
