#!/usr/bin/env python
"""Quarterly full-window drift audit against published raw snapshots (spec D5.4).

Status: diagnostic.

Re-fetches every raw snapshot bound by the published version's
``build_config.raw_snapshots`` and compares the re-fetched bytes against the
stored file hashes.  A drift is never edited in place: the report records it
as evidence for a NEW dataset version plus an operations event record — the
operator decides, this script only reports.  The record never contains
token/key/credential URL segments.

Run with an explicit project root:
    python project/drift_audit.py --root .
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest
from stock_quant.data_sources.raw_store import (
    RawSnapshotEvidence,
    RawStore,
)
from stock_quant.data_sources.tushare import TushareSource
from stock_quant.project_root import resolve_project_root


def classify_drift(stored_sha256: str, fetched_sha256: str) -> tuple[str, str | None]:
    """``("stable", None)`` or ``("drifted", stored_sha256)``."""
    if stored_sha256 == fetched_sha256:
        return "stable", None
    return "drifted", stored_sha256


def render_audit_record(version: str, rows: Sequence[Mapping[str, object]]) -> str:
    """Operator-facing ops-record body; endpoint names + hashes only."""
    today = date.today().isoformat()
    lines = [
        f"# 漂移审计 {today}（dataset {version}）",
        "",
        "机制：重取 build_config.raw_snapshots 绑定的原始快照，与已记录哈希逐条比对",
        "（spec D5.4）。发现漂移 = 新证据版本发布 + 事件记录，绝不就地改历史。",
        "本记录不含任何 token/key/凭证 URL 段。",
        "",
    ]
    for row in rows:
        endpoint = str(row.get("endpoint", "unknown"))
        request_key = str(row.get("request_key", ""))[:16]
        stored = str(row.get("stored_sha256", ""))
        fetched = str(row.get("fetched_sha256", "unfetched"))
        lines.append(
            f"- {row.get('source', 'unknown')} {endpoint} "
            f"key={request_key} stored={stored} fetched={fetched}"
        )
    return "\n".join(lines) + "\n"


def load_targets(project_root: Path, version: str) -> list[RawSnapshotEvidence]:
    """The pinned version's bound raw-snapshot evidence rows."""
    import json

    manifest_path = (
        project_root / "data" / "standardized" / version / "dataset_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build = manifest.get("build_config", {})
    rows = build.get("raw_snapshots", [])
    return [RawSnapshotEvidence(**row) for row in rows]


def _source_for(name: str, config: ProjectConfig):
    if name.startswith("tushare"):
        return TushareSource(config.sources["tushare"])
    if name.startswith("akshare"):
        return AkShareSource(config.sources["akshare"])
    if name.startswith("baostock"):
        return BaoStockSource(config.sources["baostock"])
    raise ValueError(f"no adapter for raw-snapshot source {name!r}")


def run(
    root: Path,
    config: ProjectConfig,
    *,
    version: str | None = None,
    output: Path | None = None,
) -> int:
    """Re-fetch and compare; write the dated ops record; return drift count."""
    publisher = DatasetPublisher(root)
    pinned = version or publisher.current().version
    print(f"drift audit: dataset={pinned}")
    store = RawStore(root)
    targets = load_targets(root, pinned)
    rows: list[dict[str, object]] = []
    drifted = 0
    for evidence in targets:
        try:
            snapshot = store.verify_evidence(evidence)
        except (OSError, ValueError) as error:
            rows.append(
                {
                    "source": evidence.source,
                    "endpoint": evidence.endpoint,
                    "request_key": evidence.request_key,
                    "stored_sha256": evidence.file_sha256,
                    "fetched_sha256": "unverifiable",
                    "note": type(error).__name__,
                }
            )
            continue
        parameters = snapshot.manifest.get("request_parameters", {})
        try:
            source = _source_for(evidence.source, config)
            request = DataRequest(
                str(snapshot.manifest.get("endpoint", evidence.endpoint)),
                tuple(str(symbol) for symbol in parameters.get("symbols", [])),
                date.fromisoformat(str(parameters["start_date"])),
                date.fromisoformat(str(parameters["end_date"])),
                dict(parameters.get("params", {})),
            )
            fetched = store.save(source.fetch(request))
        except Exception as error:  # noqa: BLE001 - audit reports, never raises
            rows.append(
                {
                    "source": evidence.source,
                    "endpoint": evidence.endpoint,
                    "request_key": evidence.request_key,
                    "stored_sha256": evidence.file_sha256,
                    "fetched_sha256": "fetch_failed",
                    "note": type(error).__name__,
                }
            )
            continue
        verdict, _ = classify_drift(evidence.file_sha256, fetched.sha256)
        drifted += int(verdict == "drifted")
        rows.append(
            {
                "source": evidence.source,
                "endpoint": evidence.endpoint,
                "request_key": evidence.request_key,
                "stored_sha256": evidence.file_sha256,
                "fetched_sha256": fetched.sha256,
                "note": verdict,
            }
        )
    if output is None:
        output = Path(root) / "docs" / "operations" / f"{date.today().isoformat()}-drift-audit.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_audit_record(pinned, rows), encoding="utf-8")
    print(f"drift audit: {len(rows)} snapshots, {drifted} drifted -> {output}")
    return drifted


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--version", type=str, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(root, config, version=args.version, output=args.output)


if __name__ == "__main__":
    raise SystemExit(main())
