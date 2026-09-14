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

from stock_quant.data_sources.base import FetchResult

#: Reserved: it means "this snapshot predates transport tracking".  It is
#: never a valid value for a *new* snapshot, so the directory name can never
#: collide with the legacy layout (design §2.3).
RESERVED_TRANSPORT_ID = "unknown"


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
