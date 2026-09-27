"""Drift-audit report: rendering, redaction, dispatch, and incomplete audits.

An audit that could not compare two thirds of its targets must not exit 0.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_quant.config import SourceConfig
from stock_quant.data_sources.raw_store import RawSnapshotEvidence

#: ``project/`` is an operator directory, not an installed package.
_SPEC = importlib.util.spec_from_file_location(
    "drift_audit", Path(__file__).resolve().parents[2] / "project" / "drift_audit.py"
)
drift_audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(drift_audit)

classify_drift = drift_audit.classify_drift
render_audit_record = drift_audit.render_audit_record


def _config() -> SimpleNamespace:
    """Only ``config.sources`` is read, and only to pick a segment."""
    return SimpleNamespace(
        sources={
            name: SourceConfig() for name in ("tushare", "akshare", "baostock", "xingyao")
        }
    )


def _evidence(source: str, endpoint: str) -> RawSnapshotEvidence:
    return RawSnapshotEvidence(
        source=source,
        endpoint=endpoint,
        request_key="a" * 32,
        file_sha256="b" * 64,
        manifest_sha256="c" * 64,
        transport_id="xingyao-broker-tcp",
    )


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
    rendered = render_audit_record("version-1", [row], drifted=0, audit_failures=0)
    assert "SECRETVALUE123" not in rendered
    assert "TUSHARE_TOKEN" not in rendered
    # Endpoint names and hashes are allowed (no credential segments).
    assert "daily" in rendered and "a" * 64 in rendered


def test_render_audit_record_shape():
    rendered = render_audit_record("version-1", [], drifted=0, audit_failures=0)
    assert "# 漂移审计" in rendered or "drift audit" in rendered.lower()
    assert "version-1" in rendered


def test_the_record_keeps_the_endpoint_the_audit_was_asked_to_compare():
    assert _evidence("xingyao", "backward_factor").endpoint == "backward_factor"


def test_a_known_xingyao_endpoint_dispatches_to_its_own_builder(monkeypatch):
    """The two xingyao endpoints are different adapters, not one prefixed one."""
    seen: list[str] = []

    def _daily(config):
        seen.append("daily")
        return SimpleNamespace(name="xingyao")

    def _factor(config):
        seen.append("backward_factor")
        return SimpleNamespace(name="xingyao")

    monkeypatch.setattr(drift_audit, "_xingyao_daily_source", _daily)
    monkeypatch.setattr(drift_audit, "_xingyao_factor_source", _factor)
    config = _config()

    assert drift_audit._source_for("xingyao", "daily", config).name == "xingyao"
    assert (
        drift_audit._source_for("xingyao", "backward_factor", config).name
        == "xingyao"
    )
    assert seen == ["daily", "backward_factor"]


def test_an_unknown_endpoint_is_refused_rather_than_silently_skipped():
    """A mapping that fell through to the daily adapter would compare nothing."""
    with pytest.raises(ValueError, match="backward_factor_v2"):
        drift_audit._source_for("xingyao", "backward_factor_v2", _config())


def test_an_unknown_source_is_still_refused():
    with pytest.raises(ValueError, match="not_a_source"):
        drift_audit._source_for("not_a_source", "daily", _config())


def test_the_unverifiable_and_the_unfetchable_both_count_as_audit_failures():
    rows = [
        {"endpoint": "daily", "fetched_sha256": "unverifiable"},
        {"endpoint": "daily", "fetched_sha256": "fetch_failed"},
        {"endpoint": "daily", "fetched_sha256": "c" * 64, "note": "stable"},
    ]
    assert drift_audit.count_audit_failures(rows) == 2


def test_the_record_separates_drift_from_incomplete_audits():
    body = drift_audit.render_audit_record("v1", [], drifted=0, audit_failures=3)
    assert "0" in body and "3" in body
    assert "未完成" in body


def _patch_main_io(monkeypatch, count: int) -> None:
    """Route main() to a stubbed run() returning ``count`` unfinished rows."""
    monkeypatch.setattr(drift_audit, "resolve_project_root", lambda root: root)
    monkeypatch.setattr(drift_audit, "load_project_config", lambda root: SimpleNamespace())
    monkeypatch.setattr(drift_audit, "run", lambda *args, **kwargs: count)


def test_the_process_exit_code_is_clamped_so_an_incomplete_audit_never_exits_zero(
    monkeypatch,
):
    """POSIX keeps only the low 8 bits: a raw 256 would reach the shell as 0."""
    _patch_main_io(monkeypatch, 256)
    with pytest.raises(SystemExit) as excinfo:
        drift_audit.main([])
    assert excinfo.value.code == 255


def test_a_fully_compared_audit_still_exits_zero(monkeypatch):
    _patch_main_io(monkeypatch, 0)
    with pytest.raises(SystemExit) as excinfo:
        drift_audit.main([])
    assert excinfo.value.code == 0


def _frame(symbol: str) -> pd.DataFrame:
    return pd.DataFrame({"code": [symbol], "kline_time": ["2024-01-02"]})


def _batch_evidence(symbols=("000001.SZ", "600000.SH")):
    from stock_quant.data_model.batch_evidence import (
        BatchOutcomeRecord,
        BatchRequestEvidence,
        batch_id_for,
        batch_request_parameters,
    )

    parameters = batch_request_parameters(
        "daily",
        symbols,
        date(2024, 1, 2),
        date(2024, 1, 5),
        {"adjustment": "unadjusted"},
    )
    return BatchRequestEvidence(
        source="xingyao",
        endpoint="daily",
        transport_id="xingyao-broker-tcp",
        batch_id=batch_id_for(parameters),
        batch_request_parameters=parameters,
        request_timestamp="2024-01-05T09:00:00+00:00",
        response_timestamp="2024-01-05T09:00:07+00:00",
        outcomes=tuple(
            BatchOutcomeRecord(symbol, symbol, "ok", "a" * 64) for symbol in symbols
        ),
    )


def _batch_result(symbols):
    from stock_quant.data_sources.xingyao import BatchOutcome, BatchResult

    return BatchResult(
        outcomes=tuple(
            BatchOutcome(symbol, "ok", _frame(symbol)) for symbol in symbols
        ),
        transmissions=(),
    )


def test_a_batch_record_replays_the_whole_chunk_once():
    """A per-symbol re-ask cannot reproduce a batch answer (ADR-020 D8)."""
    from project.drift_audit import replay_batch

    asked: list[list[str]] = []

    class _Source:
        def fetch_batch(self, requests, **_):
            asked.append([r.symbols[0] for r in requests])
            return _batch_result([r.symbols[0] for r in requests])

    frames = replay_batch(_Source(), _batch_evidence())

    assert asked == [["000001.SZ", "600000.SH"]]
    assert set(frames) == {"000001.SZ", "600000.SH"}


def test_a_code_the_replay_did_not_answer_is_absent_not_invented():
    from project.drift_audit import replay_batch

    class _Source:
        def fetch_batch(self, requests, **_):
            return _batch_result([requests[0].symbols[0]])

    frames = replay_batch(_Source(), _batch_evidence())

    assert set(frames) == {"000001.SZ"}


def test_a_refused_code_contributes_no_frame():
    """An outcome with no result must not become a frame-shaped hole."""
    from project.drift_audit import replay_batch
    from stock_quant.data_sources.xingyao import BatchOutcome, BatchResult

    class _Source:
        def fetch_batch(self, requests, **_):
            return BatchResult(
                outcomes=(
                    BatchOutcome("000001.SZ", "ok", _frame("000001.SZ")),
                    BatchOutcome("600000.SH", "refused", None, "no key"),
                ),
                transmissions=(),
            )

    assert set(replay_batch(_Source(), _batch_evidence())) == {"000001.SZ"}


def test_every_recorded_batch_is_replayed_once(tmp_path):
    from project.drift_audit import replay_recorded_batches
    from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore

    store = BatchEvidenceStore(tmp_path)
    evidence = _batch_evidence()
    store.save(evidence)
    asked: list[list[str]] = []

    class _Source:
        def fetch_batch(self, requests, **_):
            asked.append([r.symbols[0] for r in requests])
            return _batch_result([r.symbols[0] for r in requests])

    frames = replay_recorded_batches(_Source(), store, [evidence.sha256])

    assert asked == [["000001.SZ", "600000.SH"]]
    assert set(frames) == {"000001.SZ", "600000.SH"}


def test_an_unresolvable_evidence_hash_is_skipped_not_guessed(tmp_path):
    from project.drift_audit import replay_recorded_batches
    from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore

    class _Source:
        def fetch_batch(self, requests, **_):  # pragma: no cover
            raise AssertionError("nothing should be replayed")

    frames = replay_recorded_batches(
        _Source(), BatchEvidenceStore(tmp_path), ["f" * 64]
    )

    assert frames == {}
