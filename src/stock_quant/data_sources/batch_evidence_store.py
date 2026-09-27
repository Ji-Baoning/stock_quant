"""Content-addressed store for batch transmission evidence.

Deliberately *not* the raw snapshot tree: ``RawStore.save`` reuses an existing
``<request_key>/<file_sha256>`` directory and reads the stored manifest back
instead of writing a new one, so batch metadata welded onto a per-symbol
manifest would be silently dropped whenever those exact bytes had already been
stored by a single-symbol request.  Batch provenance gets its own immutable
tree instead.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from stock_quant.data_model.batch_evidence import (
    BatchRequestEvidence,
    canonical_json,
)

_ROOT = ("data", "raw_batch_requests")


class BatchEvidenceStore:
    """``data/raw_batch_requests/<source>/<endpoint>/<transport>/<batch_id>/<sha>.json``."""

    def __init__(self, project_root: Path) -> None:
        self._root = Path(project_root).joinpath(*_ROOT)

    def path_for(
        self, source: str, endpoint: str, transport_id: str, batch_id: str, sha256: str
    ) -> Path:
        return (
            self._root / source / endpoint / transport_id / batch_id / f"{sha256}.json"
        )

    def save(self, evidence: BatchRequestEvidence) -> Path:
        path = self.path_for(
            evidence.source,
            evidence.endpoint,
            evidence.transport_id,
            evidence.batch_id,
            evidence.sha256,
        )
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(evidence.payload()) + "\n", encoding="utf-8")
        return path

    def load(
        self, source: str, endpoint: str, transport_id: str, batch_id: str, sha256: str
    ) -> BatchRequestEvidence | None:
        path = self.path_for(source, endpoint, transport_id, batch_id, sha256)
        if not path.is_file():
            return None
        return self._read(path, sha256)

    def load_by_sha(self, sha256: str) -> BatchRequestEvidence | None:
        """Resolve a recorded evidence hash without knowing its batch path.

        The published ``build_config.batch_request_evidence`` stores hashes,
        so a reader (the drift audit) has nothing but the hash to start from.
        """
        if not self._root.is_dir():
            return None
        for path in sorted(self._root.rglob(f"{sha256}.json")):
            return self._read(path, sha256)
        return None

    def lookup_by_snapshot(
        self,
        source: str,
        endpoint: str,
        request_key: str,
        sha256: str,
    ) -> BatchRequestEvidence | None:
        """The batch record that produced this snapshot, or ``None``.

        A snapshot read back from the store must carry the batch it came from
        (spec §5); its per-symbol manifest cannot hold that (``RawStore``
        dedupe), so it is resolved from the batch registry instead.  The scan
        is bounded by one source x endpoint subtree, and deliberately does not
        name a transport: the caller is the pipeline, which must not have to
        know which adapter answered.
        """
        base = self._root / source / endpoint
        if not base.is_dir():
            return None
        for path in sorted(base.glob("*/*/*.json")):
            evidence = self._read(path, path.stem)
            if any(
                outcome.request_key == request_key
                and outcome.snapshot_file_sha256 == sha256
                for outcome in evidence.outcomes
            ):
                return evidence
        return None

    def _read(self, path: Path, sha256: str) -> BatchRequestEvidence:
        raw = path.read_text(encoding="utf-8")
        if hashlib.sha256(raw.strip().encode("utf-8")).hexdigest() != sha256:
            raise ValueError("batch evidence hash mismatch")
        payload = json.loads(raw)
        return BatchRequestEvidence.from_payload(payload)
