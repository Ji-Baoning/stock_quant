"""The batched validation lane: one session per chunk, three states per symbol."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pandas as pd

from stock_quant.bootstrap import bootstrap_dataset
from stock_quant.config import SourceConfig
from stock_quant.data_model.batch_evidence import (
    batch_id_for,
    batch_request_parameters,
)
from stock_quant.data_pipeline import (
    DataPipeline,
    DataUpdateRequest,
    SourceStatus,
    dataset_build_config,
)
from stock_quant.data_sources.base import (
    DataRequest,
    FetchResult,
    ServerError,
    request_key,
    request_metadata,
)
from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore
from stock_quant.data_sources.xingyao import (
    TRANSPORT_ID,
    BatchOutcome,
    BatchResult,
    BatchTransmission,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_START = date(2024, 1, 2)
_END = date(2024, 1, 5)
_SYMBOLS = ["000001.SZ", "600000.SH"]


def _project(tmp_path) -> Path:
    """A minimal valid project root (the recipe tests/unit/test_suspensions.py uses)."""
    root = tmp_path / "project"
    configs = root / "configs"
    configs.mkdir(parents=True)
    for name in (
        "project.yml",
        "sources.yml",
        "costs.yml",
        "trading_rules.yml",
        "universe.yml",
    ):
        shutil.copy(_REPO_ROOT / "templates" / "project-config" / name, configs / name)
    bootstrap_dataset(root)
    return root


def _frame(symbol: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "kline_time": "2024-01-02",
                "code": symbol,
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 10500.0,
            }
        ]
    )


def _result_for(request: DataRequest, frame: pd.DataFrame) -> FetchResult:
    return FetchResult(
        source="xingyao",
        endpoint="daily",
        request_key=request_key(request),
        frame=frame,
        metadata=request_metadata(
            request,
            "xingyao.query_kline",
            "fake",
            transport_id=TRANSPORT_ID,
            request_timestamp="2024-01-05T09:00:00+00:00",
            response_timestamp="2024-01-05T09:00:07+00:00",
        ),
    )


class _BatchSource:
    """A lane-level fake: scripted outcomes, answered in-process (no fork)."""

    name = "xingyao"

    def __init__(self, outcomes, *, error=None):
        self.outcomes = outcomes
        self.error = error
        self.chunks: list[list[str]] = []

    def _answer(self, request: DataRequest) -> BatchOutcome:
        # The default answer for a symbol with no scripted outcome: ok.
        # (The plan snippet guarded this with ``if symbol not in
        # self.outcomes: refused``, which is unreachable-contradictory --
        # ``_answer`` only runs for symbols absent from ``outcomes`` -- and
        # would fail 6 of this file's own assertions, so it is dropped.)
        symbol = request.symbols[0]
        result = _result_for(request, _frame(symbol))
        return BatchOutcome(symbol, "ok", result)

    @staticmethod
    def _empty(request: DataRequest) -> BatchOutcome:
        symbol = request.symbols[0]
        result = _result_for(request, _frame(symbol).iloc[:0])
        return BatchOutcome(symbol, "empty", result)

    @staticmethod
    def _refused(request: DataRequest) -> BatchOutcome:
        return BatchOutcome(
            request.symbols[0], "refused", None, "no frame for this code"
        )

    def fetch_batch(self, requests, *, on_attempt=None):
        symbols = tuple(request.symbols[0] for request in requests)
        self.chunks.append(list(symbols))
        if on_attempt is not None:
            on_attempt(len(symbols))
        if self.error is not None:
            raise self.error
        parameters = batch_request_parameters(
            "daily",
            symbols,
            requests[0].start_date,
            requests[0].end_date,
            dict(requests[0].params),
        )
        return BatchResult(
            outcomes=tuple(
                self.outcomes.get(symbol, self._answer)(request)
                for symbol, request in zip(symbols, requests, strict=True)
            ),
            transmissions=(
                BatchTransmission(
                    symbols=symbols,
                    request_parameters=parameters,
                    batch_id=batch_id_for(parameters),
                    transport_id=TRANSPORT_ID,
                    request_timestamp="2024-01-05T09:00:00+00:00",
                    response_timestamp="2024-01-05T09:00:07+00:00",
                ),
            ),
        )


class _PerSymbolSource:
    """An adapter with no batch capability: tushare/akshare/baostock's shape."""

    name = "xingyao"

    def __init__(self):
        self.requests: list[str] = []

    def fetch(self, request):
        symbol = request.symbols[0]
        self.requests.append(symbol)
        return _result_for(request, _frame(symbol))


def _lane(tmp_path, source, *, batch=True):
    """Drive ``_fetch_validation_daily`` with (or without) the batch pair."""
    root = _project(tmp_path)
    pipeline = DataPipeline(root, sources={"xingyao": source})
    if batch:
        pipeline._project_config = pipeline._project_config.model_copy(
            update={
                "sources": {
                    **pipeline._project_config.sources,
                    "xingyao": SourceConfig(
                        enabled=True, batch_size=1000, batch_timeout_seconds=180
                    ),
                }
            }
        )
    issues: list = []
    statuses: dict[str, SourceStatus] = {}
    snapshots: list = []
    rows: list = []
    pipeline._fetch_validation_daily(
        "xingyao",
        {"xingyao"},
        _SYMBOLS,
        _START,
        _END,
        issues,
        statuses,
        snapshots,
        rows,
        reuse=False,
    )
    return pipeline, root, issues, statuses, snapshots, rows


def test_one_chunk_is_one_call_for_the_whole_universe(tmp_path):
    source = _BatchSource({})
    pipeline, root, issues, statuses, snapshots, rows = _lane(tmp_path, source)

    assert source.chunks == [_SYMBOLS]
    assert statuses["xingyao"].ok is True
    assert statuses["xingyao"].reason_code == "ok"
    assert len(rows) == 2
    assert len(snapshots) == 2


def test_an_empty_answer_is_evidence_and_does_not_touch_the_status(tmp_path):
    source = _BatchSource({"600000.SH": _BatchSource._empty})
    _, root, issues, statuses, snapshots, rows = _lane(tmp_path, source)

    assert statuses["xingyao"].ok is True
    assert statuses["xingyao"].reason_code == "ok"
    assert len(snapshots) == 2, "the empty answer must be snapshotted"
    assert len(rows) == 1, "an empty answer contributes no validation row"


def test_a_refused_symbol_warns_and_marks_the_source_partial(tmp_path):
    source = _BatchSource({"600000.SH": _BatchSource._refused})
    _, root, issues, statuses, snapshots, rows = _lane(tmp_path, source)

    assert statuses["xingyao"].ok is False
    assert statuses["xingyao"].reason_code == "partial_fetch_failure"
    assert len(snapshots) == 1, "a refusal has no supplier object to snapshot"
    assert [i for i in issues if i.details.get("symbol") == "600000.SH"]


def test_a_terminal_chunk_failure_is_one_chunk_record_with_a_stable_code(tmp_path):
    source = _BatchSource({}, error=ServerError("chunk timed out"))
    _, root, issues, statuses, snapshots, rows = _lane(tmp_path, source)

    assert statuses["xingyao"].ok is False
    assert statuses["xingyao"].reason_code == "batch_fetch_failure"
    assert snapshots == [] and rows == []
    assert not [
        issue for issue in issues if issue.details.get("symbol") is not None
    ], "a chunk-level failure must never be attributed to individual symbols"


def test_a_batch_lane_counts_attempts_not_per_symbol_calls(tmp_path):
    source = _BatchSource({})
    pipeline, *_ = _lane(tmp_path, source)

    assert pipeline._transport_counts["xingyao"]["daily"] == {
        "sessions": 1,
        "code_queries": 1,
    }


def test_the_chunk_is_recorded_as_batch_evidence(tmp_path):
    source = _BatchSource({})
    pipeline, root, *_ = _lane(tmp_path, source)

    records = list(
        (root / "data" / "raw_batch_requests" / "xingyao" / "daily").rglob("*.json")
    )
    assert len(records) == 1
    evidence = BatchEvidenceStore(root).load_by_sha(records[0].stem)
    assert evidence is not None
    assert {outcome.outcome for outcome in evidence.outcomes} == {"ok"}
    assert all(outcome.snapshot_file_sha256 for outcome in evidence.outcomes)
    assert pipeline._batch_evidence_shas == frozenset({evidence.sha256})


def test_a_refusal_is_recorded_too_but_without_a_snapshot_identity(tmp_path):
    """Losing a refusal would leave the record claiming it was never asked."""
    source = _BatchSource({"600000.SH": _BatchSource._refused})
    _, root, *_ = _lane(tmp_path, source)

    records = list(
        (root / "data" / "raw_batch_requests" / "xingyao" / "daily").rglob("*.json")
    )
    evidence = BatchEvidenceStore(root).load_by_sha(records[0].stem)
    refused = [o for o in evidence.outcomes if o.outcome == "refused"]
    assert len(refused) == 1
    assert refused[0].snapshot_file_sha256 is None


class _StoredSnapshot:
    """The two attributes the reuse path reads off a snapshot."""

    def __init__(self, sha256, manifest):
        self.sha256 = sha256
        self.manifest = manifest


def test_a_reused_snapshot_brings_its_batch_evidence_back(tmp_path):
    """Reuse must not drop the batch fact behind the bytes (spec §5)."""
    source = _BatchSource({})
    pipeline, root, *_ = _lane(tmp_path, source)
    (sha,) = pipeline._batch_evidence_shas
    witness = BatchEvidenceStore(root).load_by_sha(sha).outcomes[0]

    pipeline._batch_evidence_shas.clear()
    pipeline._raw_store.resolve_reusable = lambda name, endpoint, request: (
        _StoredSnapshot(witness.snapshot_file_sha256, {"metadata": {}}),
        _frame(_SYMBOLS[0]),
    )

    pending, hits = pipeline._partition_reusable(
        "xingyao", "daily", _SYMBOLS[:1], _START, _END, None, reuse=True
    )

    assert pending == []
    assert len(hits) == 1
    assert pipeline._batch_evidence_shas == frozenset({sha})


def test_the_published_build_config_binds_the_evidence_hash(tmp_path):
    """The dataset version must point at the batch records it was built from."""
    source = _BatchSource({})
    pipeline, *_ = _lane(tmp_path, source)
    (sha,) = pipeline._batch_evidence_shas

    payload = dataset_build_config(
        run_id="data_update_test",
        request=DataUpdateRequest(start_date=_START, end_date=_END),
        effective_start_date=_START,
        resolved_end_date=_END,
        statuses={},
        raw_snapshots=[],
        calendar_spans=[],
        acceptance_start=None,
        definition_hashes={},
        skipped_definitions=[],
        batch_request_evidence=sorted(pipeline._batch_evidence_shas),
    )
    assert payload["batch_request_evidence"] == [sha]


def test_a_lane_without_the_batch_pair_stays_per_symbol(tmp_path):
    """Unset means unused: no probe value, no batch channel (ADR-020 D6)."""
    source = _PerSymbolSource()
    _, root, issues, statuses, snapshots, rows = _lane(tmp_path, source, batch=False)

    assert source.requests == _SYMBOLS
    assert statuses["xingyao"].ok is True
    assert len(rows) == 2


def _own_request(symbol: str) -> DataRequest:
    """The request one symbol's evidence must be filed under, batch or not."""
    return DataRequest("daily", (symbol,), _START, _END, {"adjustment": "unadjusted"})


def test_a_batched_symbol_lands_on_its_own_request_path(tmp_path):
    """Batching changes who answers, never what one symbol's evidence *is*.

    ADR-020 D1's whole safety argument is that one symbol stays one logical
    request with one piece of evidence: a batch-written snapshot must be filed
    under that symbol's own request -- not a batch-level one -- and must land
    on exactly the path the per-symbol lane would have produced.  If the lane
    ever filed a chunk-wide request key, every later single-request lookup
    would miss while every test above still passed, so this invariant is
    asserted against the raw tree itself (spec §6).
    """
    symbol = _SYMBOLS[0]
    pipeline, root, _, _, batch_snapshots, _ = _lane(tmp_path, _BatchSource({}))
    _, solo_root, _, _, solo_snapshots, _ = _lane(
        tmp_path / "solo", _PerSymbolSource(), batch=False
    )

    key = request_key(_own_request(symbol))
    batched = next(s for s in batch_snapshots if s.manifest["request_key"] == key)
    solo = next(s for s in solo_snapshots if s.manifest["request_key"] == key)
    # Same request key, same transport, same frame bytes => same content
    # address.  Timestamps differ between the two rounds on purpose: the path
    # must not depend on them.  The two rounds run in two independent project
    # roots, so the claim is about the store's relative layout.
    assert batched.path.relative_to(root) == solo.path.relative_to(solo_root)
    assert batched.sha256 == solo.sha256

    # And it is therefore usable by that symbol's own request next round: the
    # batch lane bought throughput without buying a new evidence identity.
    found = pipeline._raw_store.resolve_reusable(
        "xingyao", "daily", _own_request(symbol)
    )
    assert found is not None
    assert found[0].sha256 == batched.sha256

    # The chunk as a whole is *not* a request anyone may look up: the batch
    # request lives in BatchRequestEvidence, never in the per-symbol tree.
    assert not (
        root
        / "data"
        / "raw"
        / "xingyao"
        / "daily"
        / TRANSPORT_ID
        / request_key(
            DataRequest("daily", tuple(_SYMBOLS), _START, _END)
        )
    ).exists()
