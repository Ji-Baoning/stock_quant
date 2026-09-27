"""The real transport request behind a batch of per-symbol evidence.

A logical request is one symbol; a batch call answers many at once.  Reuse,
the raw snapshot path and ``request_key`` stay keyed by the logical request
(ADR-015, unchanged), so the batch itself has to be recorded somewhere else:
this module is that record.  Its ``batch_id`` is the canonical hash of the
parameters the call actually carried, which is what makes a later replay
comparable -- a per-symbol digest cannot express "these codes travelled
together", and an absent key or a truncated answer may depend on that
grouping.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date

#: Outcome vocabulary, shared with the lane's per-symbol state.
OUTCOME_OK = "ok"
OUTCOME_EMPTY = "empty"
OUTCOME_REFUSED = "refused"

OUTCOMES = (OUTCOME_OK, OUTCOME_EMPTY, OUTCOME_REFUSED)


def canonical_json(payload: object) -> str:
    """One serialization for every hash and every stored record."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def batch_request_parameters(
    endpoint: str,
    symbols: tuple[str, ...],
    start_date: date,
    end_date: date,
    params: dict[str, str],
) -> str:
    """The chunk as it was asked, in a rebuildable canonical form."""
    return canonical_json(
        {
            "endpoint": endpoint,
            "symbols": list(symbols),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "params": dict(params),
        }
    )


def batch_id_for(parameters: str) -> str:
    """The batch identity: the hash of the parameters string as given."""
    return hashlib.sha256(parameters.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BatchOutcomeRecord:
    """One requested symbol's outcome inside a batch call.

    ``snapshot_file_sha256`` is the content address of the per-symbol raw
    snapshot this outcome produced, or ``None`` for ``refused`` -- where no
    supplier object exists, so no snapshot may be fabricated for it.
    """

    symbol: str
    request_key: str
    outcome: str
    snapshot_file_sha256: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown batch outcome {self.outcome!r}")
        if (self.outcome == OUTCOME_REFUSED) != (self.snapshot_file_sha256 is None):
            raise ValueError("only a refused outcome may lack a snapshot identity")


@dataclass(frozen=True)
class BatchRequestEvidence:
    """One actual supplier transmission and what it produced, per symbol."""

    source: str
    endpoint: str
    transport_id: str
    batch_id: str
    batch_request_parameters: str
    request_timestamp: str
    response_timestamp: str
    outcomes: tuple[BatchOutcomeRecord, ...]

    def payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "endpoint": self.endpoint,
            "transport_id": self.transport_id,
            "batch_id": self.batch_id,
            "batch_request_parameters": self.batch_request_parameters,
            "request_timestamp": self.request_timestamp,
            "response_timestamp": self.response_timestamp,
            "outcomes": [asdict(outcome) for outcome in self.outcomes],
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(
            canonical_json(self.payload()).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "BatchRequestEvidence":
        rows = payload["outcomes"]
        assert isinstance(rows, list)
        return cls(
            source=str(payload["source"]),
            endpoint=str(payload["endpoint"]),
            transport_id=str(payload["transport_id"]),
            batch_id=str(payload["batch_id"]),
            batch_request_parameters=str(payload["batch_request_parameters"]),
            request_timestamp=str(payload["request_timestamp"]),
            response_timestamp=str(payload["response_timestamp"]),
            outcomes=tuple(
                BatchOutcomeRecord(
                    symbol=str(row["symbol"]),
                    request_key=str(row["request_key"]),
                    outcome=str(row["outcome"]),
                    snapshot_file_sha256=(
                        None
                        if row.get("snapshot_file_sha256") is None
                        else str(row["snapshot_file_sha256"])
                    ),
                    message=str(row.get("message", "")),
                )
                for row in rows
            ),
        )
