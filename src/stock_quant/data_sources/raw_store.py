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


@dataclass(frozen=True)
class RawSnapshot:
    path: Path
    sha256: str
    manifest: dict[str, Any]


class RawStore:
    """Persist immutable Parquet responses below one project's ``data/raw`` tree."""

    def __init__(self, project_root: Path) -> None:
        self._root = Path(project_root) / "data" / "raw"

    def save(self, result: FetchResult) -> RawSnapshot:
        source = _path_component(result.source, "source")
        endpoint = _path_component(result.endpoint, "endpoint")
        request_key = _path_component(result.request_key, "request key")
        destination_parent = self._root / source / endpoint / request_key
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
                manifest = _manifest_for(result, response_sha256, file_sha256)
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


def _manifest_for(
    result: FetchResult, response_sha256: str, file_sha256: str
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
