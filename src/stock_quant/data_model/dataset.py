"""Immutable, content-addressed publication of standardized datasets.

Standardized tables are written to a staging directory together with
``quality_report.json`` and ``dataset_manifest.json``, then atomically renamed
to ``data/standardized/<dataset_version>/`` where ``dataset_version`` is derived
from the sorted Parquet file hashes, the schema versions and a normalized build
configuration. A small ``CURRENT`` file is atomically replaced only after the
version directory is in place, so the previous version stays available when a
publication is blocked.

Readers open a genuinely read-only DuckDB connection whose views are bound to
the exact absolute Parquet paths of one version; once opened they never consult
``CURRENT`` again. DuckDB catalogs are kept in the OS temporary directory so
the immutable version directory remains Parquet-only (design spec §11: Parquet
is the only persisted standardized data).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_quant.data_model.schemas import (
    CORPORATE_ACTION_SCHEMA,
    DAILY_SCHEMA,
    SECURITY_MASTER_SCHEMA,
    TRADING_CALENDAR_SCHEMA,
)
from stock_quant.data_quality.gates import evaluate_publication
from stock_quant.data_quality.models import QualityReport, issue_dict_dumps

#: The standardized tables this repository can publish, keyed by canonical
#: table name and fixed to the Task 3 Arrow schemas. New canonical tables must
#: be registered here before they can be published.
STANDARDIZED_SCHEMAS: dict[str, pa.Schema] = {
    "daily_bar": DAILY_SCHEMA,
    "security_master": SECURITY_MASTER_SCHEMA,
    "corporate_action": CORPORATE_ACTION_SCHEMA,
    "trading_calendar": TRADING_CALENDAR_SCHEMA,
}

_MANIFEST_NAME = "dataset_manifest.json"
_QUALITY_REPORT_NAME = "quality_report.json"
_CURRENT_NAME = "CURRENT"


class PublicationBlocked(RuntimeError):
    """The neutral publication gate refused this dataset."""


class DatasetNotFoundError(FileNotFoundError):
    """A requested dataset version does not exist under the standardized root."""


@dataclass(frozen=True)
class DatasetRef:
    """A published, immutable dataset version and its on-disk directory."""

    version: str
    path: Path


class DatasetPublisher:
    """Publish gated, content-addressed datasets below one project's data tree."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = Path(project_root)

    @property
    def standardized_root(self) -> Path:
        return self._project_root / "data" / "standardized"

    @property
    def staging_root(self) -> Path:
        return self._project_root / "data" / "staging"

    def publish(
        self,
        tables: Mapping[str, pd.DataFrame],
        report: QualityReport,
        *,
        build_config: Mapping[str, Any] | None = None,
    ) -> DatasetRef:
        """Gate, stage and atomically publish one immutable dataset version."""
        decision = evaluate_publication(report)
        if not decision.passed:
            raise PublicationBlocked(
                "publication blocked: " + "; ".join(decision.reasons)
            )
        normalized_config = _normalize_build_config(build_config)
        staging = self.staging_root / uuid.uuid4().hex
        staging.mkdir(parents=True)
        try:
            records = _stage_tables(tables, staging)
            dataset_version = _dataset_version(records, normalized_config)
            (staging / _QUALITY_REPORT_NAME).write_text(
                issue_dict_dumps(report), encoding="utf-8"
            )
            manifest = _manifest(dataset_version, records, normalized_config)
            (staging / _MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            destination = self.standardized_root / dataset_version
            if destination.exists():
                shutil.rmtree(staging)
            else:
                self.standardized_root.mkdir(parents=True, exist_ok=True)
                os.replace(staging, destination)
            self._replace_current(dataset_version)
            return DatasetRef(version=dataset_version, path=destination)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def current(self) -> DatasetRef:
        """Return the version named by the atomically replaced ``CURRENT`` file."""
        current_file = self.standardized_root / _CURRENT_NAME
        if not current_file.exists():
            raise DatasetNotFoundError(
                f"no published dataset yet under {self.standardized_root}"
            )
        version = current_file.read_text(encoding="utf-8").strip()
        path = self.standardized_root / version
        if not path.is_dir():
            raise DatasetNotFoundError(
                f"CURRENT names {version} but {path} is not a dataset directory"
            )
        return DatasetRef(version=version, path=path)

    def _replace_current(self, version: str) -> None:
        self.standardized_root.mkdir(parents=True, exist_ok=True)
        temporary = self.standardized_root / f".{_CURRENT_NAME}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(version + "\n", encoding="utf-8")
        os.replace(temporary, self.standardized_root / _CURRENT_NAME)


class DatasetReader:
    """Read-only access to an exact immutable dataset version."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = Path(project_root)

    @property
    def standardized_root(self) -> Path:
        return self._project_root / "data" / "standardized"

    def open(self, version: str) -> "DatasetContext":
        """Open read-only DuckDB views over one version's Parquet files."""
        version_dir = self.standardized_root / version
        if not version_dir.is_dir():
            raise DatasetNotFoundError(
                f"dataset version {version!r} does not exist under "
                f"{self.standardized_root}"
            )
        manifest_file = version_dir / _MANIFEST_NAME
        if not manifest_file.is_file():
            raise DatasetNotFoundError(
                f"dataset {version} is missing {_MANIFEST_NAME}"
            )
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        tables = tuple(sorted(manifest.get("tables", {})))
        connection = _readonly_connection(version_dir, tables, manifest)
        return DatasetContext(
            version=version,
            path=version_dir,
            tables=tables,
            connection=connection,
        )


@dataclass
class DatasetContext:
    """A pinned, read-only dataset version for factor and research queries."""

    version: str
    path: Path
    tables: tuple[str, ...]
    connection: duckdb.DuckDBPyConnection

    def read(self, table: str) -> pd.DataFrame:
        """Read one standardized table through its pinned DuckDB view."""
        if table not in self.tables:
            raise ValueError(
                f"dataset {self.version} has no table {table!r}; "
                f"available: {', '.join(self.tables)}"
            )
        return self.connection.execute(f'SELECT * FROM "{_sql_identifier(table)}"').df()

    def query(self, sql: str, params: tuple[Any, ...] | None = None) -> pd.DataFrame:
        """Run an arbitrary SQL query against the pinned views."""
        return self.connection.execute(sql, params or ()).df()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DatasetContext":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _stage_tables(
    tables: Mapping[str, pd.DataFrame], staging: Path
) -> dict[str, dict[str, Any]]:
    if not isinstance(tables, Mapping):
        raise PublicationBlocked("tables must be a mapping of table name to frame")
    records: dict[str, dict[str, Any]] = {}
    for name in sorted(tables):
        schema = STANDARDIZED_SCHEMAS.get(name)
        if schema is None:
            raise ValueError(f"no canonical schema for standardized table {name!r}")
        frame = tables[name]
        table = _frame_to_arrow(frame, schema)
        filename = f"{name}.parquet"
        path = staging / filename
        pq.write_table(table, path)
        records[name] = {
            "path": filename,
            "row_count": table.num_rows,
            "sha256": _sha256_file(path),
            "schema_version": _schema_version(schema),
        }
    return records


def _frame_to_arrow(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    if not isinstance(frame, pd.DataFrame):
        raise PublicationBlocked(f"expected a DataFrame, got {type(frame).__name__}")
    expected = [field.name for field in schema]
    if list(frame.columns) != expected:
        raise PublicationBlocked(
            "table columns do not match the canonical schema: "
            f"expected {expected}, got {list(frame.columns)}"
        )
    try:
        table = pa.Table.from_pandas(frame, preserve_index=False, schema=schema)
    except (TypeError, ValueError, pa.ArrowException) as error:
        message = f"cannot cast table to canonical schema: {error}"
        raise PublicationBlocked(message) from error
    return table.replace_schema_metadata(None)


def _dataset_version(
    records: Mapping[str, dict[str, Any]], build_config: Mapping[str, Any]
) -> str:
    descriptor = {
        "files": [
            [name, records[name]["sha256"]] for name in sorted(records)
        ],
        "schema_versions": [
            [name, records[name]["schema_version"]] for name in sorted(records)
        ],
        "build_config": build_config,
    }
    encoded = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _manifest(
    dataset_version: str,
    records: Mapping[str, dict[str, Any]],
    build_config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "dataset_version": dataset_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_versions": {
            name: records[name]["schema_version"] for name in sorted(records)
        },
        "tables": {name: dict(records[name]) for name in sorted(records)},
        "build_config": dict(build_config),
    }


def _schema_version(schema: pa.Schema) -> str:
    descriptor = [{"name": field.name, "type": str(field.type)} for field in schema]
    encoded = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_build_config(
    build_config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if build_config is None:
        return {}
    if not isinstance(build_config, Mapping):
        raise TypeError("build_config must be a mapping of JSON-serializable values")
    return json.loads(json.dumps(dict(build_config), sort_keys=True))


def _readonly_connection(
    version_dir: Path, tables: tuple[str, ...], manifest: dict[str, Any]
) -> duckdb.DuckDBPyConnection:
    catalog = _catalog_path(version_dir)
    if not catalog.exists():
        _build_catalog(catalog, version_dir, tables, manifest)
    try:
        return duckdb.connect(str(catalog), read_only=True)
    except duckdb.Error:
        catalog.unlink(missing_ok=True)
        _build_catalog(catalog, version_dir, tables, manifest)
        return duckdb.connect(str(catalog), read_only=True)


def _build_catalog(
    catalog: Path,
    version_dir: Path,
    tables: tuple[str, ...],
    manifest: dict[str, Any],
) -> None:
    catalog.parent.mkdir(parents=True, exist_ok=True)
    temporary = catalog.with_name(f"{catalog.name}.{uuid.uuid4().hex}.tmp")
    connection = duckdb.connect(str(temporary))
    try:
        table_files = manifest.get("tables", {})
        for name in tables:
            rel_path = table_files[name]["path"]
            parquet_path = (version_dir / rel_path).resolve()
            source = f"read_parquet({_sql_string(parquet_path)})"
            connection.execute(
                "CREATE VIEW "
                f'"{_sql_identifier(name)}" AS SELECT * FROM {source}'
            )
        connection.close()
        os.replace(temporary, catalog)
    except BaseException:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise


def _catalog_path(version_dir: Path) -> Path:
    root_key = hashlib.sha256(
        str(version_dir.parent.resolve()).encode("utf-8")
    ).hexdigest()
    directory = Path(tempfile.gettempdir()) / "stock_quant_duckdb" / root_key
    return directory / f"{version_dir.name}.duckdb"


def _sql_identifier(name: str) -> str:
    return name.replace('"', '""')


def _sql_string(value: Path) -> str:
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
