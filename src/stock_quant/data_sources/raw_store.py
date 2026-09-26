"""Immutable, supplier-isolated persistence for native raw responses."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stock_quant.data_sources.base import (
    DataRequest,
    FetchResult,
    request_key,
)

#: Reserved: it means "this snapshot predates transport tracking".  It is
#: never a valid value for a *new* snapshot, so the directory name can never
#: collide with the legacy layout (design §2.3).
RESERVED_TRANSPORT_ID = "unknown"

#: The channels whose stored answers :meth:`RawStore.resolve_reusable` may
#: serve back (ADR-015).  Membership is the first reuse gate: a
#: ``(source, endpoint)`` outside the set is always fetched live, so the
#: trading calendar (the clock), the security master (the universe
#: definition), the corporate-action endpoints (a revision-sensitive
#: disclosure channel on its mandatory lookback) and the lazy arbitration
#: channels can never be answered from disk, no matter what their caller
#: asks for.
REUSABLE_CHANNELS: frozenset[tuple[str, str]] = frozenset(
    {
        ("tushare", "daily"),
        ("baostock", "daily"),
        ("akshare", "index_history"),
    }
)


@dataclass(frozen=True)
class RawSnapshot:
    path: Path
    sha256: str
    manifest: dict[str, Any]


@dataclass(frozen=True)
class RawSnapshotEvidence:
    """Sanitized, re-resolvable pointer to one stored raw snapshot.

    Carries path-safe identifiers plus the content and manifest hashes only --
    never local paths, supplier metadata or reason prose -- so it can travel
    through dataset build evidence and later be verified back to the exact
    stored bytes via :meth:`RawStore.verify_evidence`.

    ``transport_id`` is the answering party (design §2.3).  ``None`` means the
    record predates transport tracking and resolves on the four-segment
    ``<source>/<endpoint>/<request_key>/<file_sha256>`` layout.
    """

    source: str
    endpoint: str
    request_key: str
    file_sha256: str
    manifest_sha256: str
    transport_id: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: RawSnapshot) -> "RawSnapshotEvidence":
        return cls(
            source=str(snapshot.manifest["source"]),
            endpoint=str(snapshot.manifest["endpoint"]),
            request_key=str(snapshot.manifest["request_key"]),
            file_sha256=snapshot.sha256,
            manifest_sha256=_sha256_file(snapshot.path / "manifest.json"),
            transport_id=snapshot.manifest.get("transport_id"),
        )


class RawStore:
    """Persist immutable Parquet responses below one project's ``data/raw`` tree."""

    def __init__(self, project_root: Path) -> None:
        self._root = Path(project_root) / "data" / "raw"

    def save(self, result: FetchResult) -> RawSnapshot:
        source = _path_component(result.source, "source")
        endpoint = _path_component(result.endpoint, "endpoint")
        transport_id = _transport_id(result.metadata.get("transport_id"))
        request_key = _path_component(result.request_key, "request key")
        destination_parent = self._root / source / endpoint / transport_id / request_key
        destination_parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_parent / f".{uuid.uuid4().hex}.tmp"
        temporary.mkdir()
        data_path = temporary / "data.parquet"
        try:
            result.frame.to_parquet(data_path, index=True)
            file_sha256 = _sha256_file(data_path)
            response_sha256 = _response_sha256(result.frame)
            snapshot_path = destination_parent / file_sha256
            if snapshot_path.exists():
                shutil.rmtree(temporary)
                manifest = json.loads((snapshot_path / "manifest.json").read_text())
            else:
                manifest = _manifest_for(
                    result, response_sha256, file_sha256, transport_id
                )
                (temporary / "manifest.json").write_text(
                    json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, snapshot_path)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return RawSnapshot(path=snapshot_path, sha256=file_sha256, manifest=manifest)

    def verify_evidence(self, evidence: RawSnapshotEvidence) -> RawSnapshot:
        """Re-resolve an evidence pointer to the exact stored snapshot.

        A ``transport_id`` of ``None`` means the record predates transport
        tracking, and the pointer is resolved on the older four-segment
        layout; a present ``transport_id`` is re-validated exactly like a
        fresh one, so a legacy reader cannot be tricked into walking out of
        the store.
        """
        source = _path_component(evidence.source, "source")
        endpoint = _path_component(evidence.endpoint, "endpoint")
        request_key = _path_component(evidence.request_key, "request key")
        file_sha256 = _sha256_value(evidence.file_sha256)
        transport_id = (
            None
            if evidence.transport_id is None
            else _transport_id(evidence.transport_id)
        )
        path = (
            self._root / source / endpoint / request_key / file_sha256
            if transport_id is None
            else self._root
            / source
            / endpoint
            / transport_id
            / request_key
            / file_sha256
        )
        data_path = path / "data.parquet"
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("raw manifest is not a mapping")
        if _sha256_file(data_path) != file_sha256:
            raise ValueError("raw data hash mismatch")
        if _sha256_file(manifest_path) != evidence.manifest_sha256:
            raise ValueError("raw manifest hash mismatch")
        expected_fields = [
            ("source", source),
            ("endpoint", endpoint),
            ("request_key", request_key),
            ("file_sha256", file_sha256),
        ]
        if transport_id is not None:
            expected_fields.append(("transport_id", transport_id))
        for key, expected in expected_fields:
            if manifest.get(key) != expected:
                raise ValueError(f"raw manifest {key} mismatch")
        return RawSnapshot(path=path, sha256=file_sha256, manifest=manifest)

    def resolve_reusable(
        self,
        source: str,
        endpoint: str,
        request: DataRequest,
        *,
        allow_empty: bool = False,
    ) -> tuple[RawSnapshot, pd.DataFrame] | None:
        """The newest stored answer to exactly this request, or ``None``.

        Reuse (ADR-015) is gated first on :data:`REUSABLE_CHANNELS`; then the
        candidates for this request's key are ordered by
        ``(response_timestamp desc, missing last, file_sha256 secondary)``
        and **only the newest one is considered**.  A candidate is served
        back only after two checks both pass: a defensive comparison of the
        stored ``request_parameters`` against this request (a missing or
        wrongly-shaped record never matches, and never raises), and a full
        :meth:`verify_evidence` re-verification of the stored bytes.  When
        the newest candidate fails either check the method returns ``None``
        -- it never falls back to an older candidate, because an older
        digest under the same request key is by definition a supplier-revised
        historical observation, and silently reusing it would demote a
        tamper signal into a successful reuse.

        An empty frame is absence, not an answer (the ADR-009 stance):
        ``None`` unless the caller passes ``allow_empty=True``, which only
        the head-anchor backfill probe does.

        The glob covers the five-segment layout only; the four-segment
        pre-transport tree that :meth:`verify_evidence` can still read is
        deliberately never matched, so legacy snapshots are always re-fetched.
        """
        if (source, endpoint) not in REUSABLE_CHANNELS:
            return None
        source = _path_component(source, "source")
        endpoint = _path_component(endpoint, "endpoint")
        key = request_key(request)
        candidates: list[tuple[tuple[int, pd.Timestamp, str], Path, dict[str, Any], str]] = []
        pattern = f"{source}/{endpoint}/*/{key}/*/manifest.json"
        for manifest_path in sorted(self._root.glob(pattern)):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("raw manifest is not a mapping")
                sha = _sha256_value(str(manifest["file_sha256"]))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue  # an unreadable candidate is not evidence
            candidates.append(
                (_candidate_order(manifest), manifest_path, manifest, sha)
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, manifest_path, manifest, sha = candidates[0]
        if not _request_matches(manifest.get("request_parameters"), request):
            return None
        try:
            snapshot = self.verify_evidence(
                RawSnapshotEvidence(
                    source=source,
                    endpoint=endpoint,
                    request_key=key,
                    file_sha256=sha,
                    manifest_sha256=_sha256_file(manifest_path),
                    transport_id=manifest.get("transport_id"),
                )
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        frame = pd.read_parquet(snapshot.path / "data.parquet")
        if frame.empty and not allow_empty:
            return None
        return snapshot, frame

    def has_candidate(
        self, source: str, endpoint: str, request: DataRequest
    ) -> bool:
        """Whether any stored snapshot directory exists for this request.

        Existence only -- no verification, no channel gate.  The fetch layer
        uses it to tell a normal miss ("nothing stored yet") from a refused
        candidate ("something was stored but ``resolve_reusable`` would not
        serve it"), which is worth a visible warning.
        """
        try:
            source = _path_component(source, "source")
            endpoint = _path_component(endpoint, "endpoint")
            key = request_key(request)
        except ValueError:
            return False
        pattern = f"{source}/{endpoint}/*/{key}/*/manifest.json"
        return any(self._root.glob(pattern))


def _candidate_order(manifest: dict[str, Any]) -> tuple[int, pd.Timestamp, str]:
    """Sort key picking the newest stored answer for one request key.

    Present timestamps order newest-first; a missing or unparseable
    ``response_timestamp`` sorts after every present one; ``file_sha256``
    breaks remaining ties deterministically.  All timestamps are normalised
    to UTC so naive and aware forms stay comparable.
    """
    sha = str(manifest.get("file_sha256", ""))
    raw = manifest.get("response_timestamp")
    if isinstance(raw, str) and raw:
        try:
            timestamp = pd.Timestamp(raw)
        except (ValueError, TypeError):
            timestamp = pd.NaT
        if not pd.isna(timestamp):
            if timestamp.tz is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
            return (1, timestamp, sha)
    return (0, _MISSING_TIMESTAMP, sha)


#: Sentinel placing candidates without a usable ``response_timestamp`` after
#: every timestamped one, in UTC so the tuple comparison stays type-stable.
_MISSING_TIMESTAMP = pd.Timestamp.min.tz_localize("UTC")


def _request_matches(stored: object, request: DataRequest) -> bool:
    """Defensive equality between a stored manifest's request shape and ours.

    The request-key directory name already hashes these fields; this check
    keeps a foreign or hand-edited manifest from being served as the answer.
    JSON round-tripping on both sides normalises tuples to lists, so the
    comparison is shape-stable; anything non-serialisable fails closed.
    """
    if not isinstance(stored, dict):
        return False
    expected = {
        "symbols": list(request.symbols),
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat(),
        "params": request.params,
    }
    try:
        return json.dumps(stored, sort_keys=True) == json.dumps(
            expected, sort_keys=True
        )
    except (TypeError, ValueError):
        return False


def _manifest_for(
    result: FetchResult,
    response_sha256: str,
    file_sha256: str,
    transport_id: str,
) -> dict[str, Any]:
    metadata = _redact(result.metadata)
    request_parameters = metadata.get("request_parameters", {})
    if isinstance(request_parameters, str):
        try:
            request_parameters = _redact(json.loads(request_parameters))
        except json.JSONDecodeError:
            pass
    metadata["request_parameters"] = request_parameters
    return {
        "source": result.source,
        "endpoint": result.endpoint,
        "supplier_endpoint": metadata.get("supplier_endpoint", result.endpoint),
        "transport_id": transport_id,
        "request_key": result.request_key,
        "request_parameters": request_parameters,
        "request_timestamp": metadata.get("request_timestamp"),
        "response_timestamp": metadata.get("response_timestamp"),
        "sdk_version": metadata.get("sdk_version", "unknown"),
        "row_count": len(result.frame),
        "schema": {column: str(dtype) for column, dtype in result.frame.dtypes.items()},
        "response_sha256": response_sha256,
        "file_sha256": file_sha256,
        "redacted": True,
        "metadata": metadata,
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if _is_secret_key(str(key))
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lower = key.lower()
    secret_markers = ("token", "secret", "password", "authorization")
    return any(part in lower for part in secret_markers)


def _path_component(value: str, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid {label}")
    return value


def _transport_id(value: object) -> str:
    """Validate one snapshot's answering-party id (design §2.3).

    Missing, blank and the reserved word ``unknown`` are all rejected: a
    default would let two different upstreams share a content-addressed path
    and silently keep each other's ``supplier_endpoint``.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"raw metadata must carry a transport_id string, got {type(value).__name__}"
        )
    text = _path_component(value.strip(), "transport id")
    if text == RESERVED_TRANSPORT_ID:
        raise ValueError(
            f"{RESERVED_TRANSPORT_ID!r} is reserved for snapshots that predate "
            "transport tracking and is never valid for a new snapshot"
        )
    return text


def _sha256_value(value: str) -> str:
    """Normalise and validate one hex-encoded SHA-256 digest."""
    text = str(value).strip().lower()
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError("invalid sha256 digest")
    return text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _response_sha256(frame: pd.DataFrame) -> str:
    """Hash supplier-native frame content independently from its Parquet encoding."""
    digest = hashlib.sha256()
    descriptor = {
        "columns": [str(column) for column in frame.columns],
        "dtypes": [str(dtype) for dtype in frame.dtypes],
        "index_name": str(frame.index.name),
    }
    digest.update(json.dumps(descriptor, ensure_ascii=False, sort_keys=True).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()
