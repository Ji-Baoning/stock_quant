"""Drift-audit report: pure rendering, redaction, verdict classification."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "project"))

from drift_audit import classify_drift, render_audit_record  # noqa: E402


def test_classify_drift():
    assert classify_drift("abc", "abc") == ("stable", None)
    assert classify_drift("abc", "def") == ("drifted", "abc")


def test_redaction_never_leaks_credentials():
    row = {
        "source": "tushare",
        "endpoint": "daily",
        "request_key": "x" * 16,
        "stored_sha256": "a" * 64,
        "fetched_sha256": "b" * 64,
        "note": "TUSHARE_TOKEN=SECRETVALUE123",
    }
    rendered = render_audit_record("version-1", [row])
    assert "SECRETVALUE123" not in rendered
    assert "TUSHARE_TOKEN" not in rendered
    # Endpoint names and hashes are allowed (no credential segments).
    assert "daily" in rendered and "a" * 64 in rendered


def test_render_audit_record_shape():
    rendered = render_audit_record("version-1", [])
    assert "# 漂移审计" in rendered or "drift audit" in rendered.lower()
    assert "version-1" in rendered
