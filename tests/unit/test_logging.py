"""Secret redaction of the structured JSONL logger (Task 11).

The runner's audit trail must never persist a Tushare token, an authorization
header or any other configured secret -- neither in the JSONL file nor on the
terminal.  :func:`redact_text` masks configured secret literals and the values
of recognised sensitive key/value pairs; the logger applies it to the message,
the JSON line and the terminal line independently so escaping cannot smuggle a
secret back in.
"""

from __future__ import annotations

import json

import pytest

from stock_quant.logging import REDACTED, StructuredLogger, redact_text


@pytest.fixture
def log_writer(tmp_path, capsys):
    """A file-backed StructuredLogger whose terminal writes are captured."""
    return StructuredLogger(tmp_path / "run.jsonl", secrets=("top-secret",))


def test_structured_log_redacts_secrets(log_writer, capsys):
    log_writer.error(
        stage="FETCHING", source="tushare", message="token=secret-value"
    )
    assert "secret-value" not in capsys.readouterr().out
    assert "[REDACTED]" in log_writer.path.read_text()


def test_logger_redacts_literal_secret_in_file_and_terminal(log_writer, capsys):
    log_writer.info("refresh authorization header top-secret")
    captured = capsys.readouterr().out
    assert "top-secret" not in captured
    assert "top-secret" not in log_writer.path.read_text()
    assert REDACTED in captured
    assert REDACTED in log_writer.path.read_text()


def test_log_records_are_json_lines_with_context(log_writer):
    log_writer.warning(
        "universe is stale",
        run_id="run_x",
        stage="pin",
        source="universe",
        event="stale_universe",
    )
    record = json.loads(log_writer.path.read_text().strip().splitlines()[-1])
    assert record["severity"] == "WARNING"
    assert record["run_id"] == "run_x"
    assert record["stage"] == "pin"
    assert record["source"] == "universe"
    assert record["event"] == "stale_universe"
    assert record["message"] == "universe is stale"
    assert "timestamp" in record


def test_redact_text_literal_secret_is_masked_everywhere():
    assert redact_text("connect using top-secret now", ("top-secret",)) == (
        f"connect using {REDACTED} now"
    )


def test_redact_text_token_key_value_is_masked_without_configured_secret():
    assert redact_text("api error token=abc123def detail") == (
        f"api error token{REDACTED} detail"
    )


def test_redact_text_masks_authorization_and_json_shapes():
    assert redact_text("Authorization: k_9f8e7d6c5b4a rest") == (
        f"Authorization{REDACTED} rest"
    )
    masked = redact_text('"api_key": "k_9f8e7d6c5b4a"')
    assert "k_9f8e7d6c5b4a" not in masked
    assert "api_key" in masked
    assert REDACTED in masked
    assert redact_text("token=abc123, symbol=600000.SH") == (
        f"token{REDACTED}, symbol=600000.SH"
    )


def test_redact_text_removes_configured_secret_inside_auth_header():
    masked = redact_text(
        "Authorization: Bearer top-secret", ("top-secret",)
    )
    assert "top-secret" not in masked
    assert REDACTED in masked


def test_redact_text_leaves_ordinary_values_untouched():
    text = "symbol=600519.SH commission=0.0003 factor=momentum_60d"
    assert redact_text(text) == text
