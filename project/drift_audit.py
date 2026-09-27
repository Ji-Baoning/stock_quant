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
import json
import sys
from datetime import date
from pathlib import Path
from typing import Mapping, NoReturn, Sequence

import pandas as pd

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.batch_evidence import BatchRequestEvidence
from stock_quant.data_model.dataset import DatasetPublisher
from stock_quant.data_sources.akshare import AkShareSource
from stock_quant.data_sources.baostock import BaoStockSource
from stock_quant.data_sources.base import DataRequest, FetchResult
from stock_quant.data_sources.batch_evidence_store import BatchEvidenceStore
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


def render_audit_record(
    version: str,
    rows: Sequence[Mapping[str, object]],
    *,
    drifted: int,
    audit_failures: int,
) -> str:
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
    lines.extend(
        [
            "",
            f"汇总：比对完成 {len(rows) - audit_failures} 条，漂移 {drifted} 条，"
            f"未完成 {audit_failures} 条。",
            "未完成不是通过：它表示这个通道本轮没有被审到，需在下次审计前修好。",
        ]
    )
    return "\n".join(lines) + "\n"


def count_audit_failures(rows: Sequence[Mapping[str, object]]) -> int:
    """Rows the audit could not compare: unverifiable or unfetchable."""
    return sum(
        1
        for row in rows
        if str(row.get("fetched_sha256", "")) in ("unverifiable", "fetch_failed")
    )


def _read_build_config(project_root: Path, version: str) -> Mapping[str, object]:
    """The pinned version's recorded build configuration."""
    manifest_path = (
        project_root / "data" / "standardized" / version / "dataset_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest.get("build_config", {})


def _targets_from_build(build: Mapping[str, object]) -> list[RawSnapshotEvidence]:
    return [RawSnapshotEvidence(**row) for row in build.get("raw_snapshots", [])]


def _batch_hashes_from_build(build: Mapping[str, object]) -> list[str]:
    return [str(sha) for sha in (build.get("batch_request_evidence") or [])]


def load_targets(project_root: Path, version: str) -> list[RawSnapshotEvidence]:
    """The pinned version's bound raw-snapshot evidence rows."""
    return _targets_from_build(_read_build_config(project_root, version))


def load_batch_request_evidence(project_root: Path, version: str) -> list[str]:
    """The batch transmissions the version bound, as evidence hashes.

    A version built before the batch channel bound none: the absent key is
    an empty list, not an error.
    """
    return _batch_hashes_from_build(_read_build_config(project_root, version))


def replay_batch(
    source: object, evidence: BatchRequestEvidence
) -> dict[str, pd.DataFrame]:
    """Re-ask exactly the recorded batch, once, and split it back per code.

    Degrading a batch record into per-symbol re-asks would change the
    question: an absent key, a truncation or a different answer shape can
    depend on which codes travelled together, so the comparison would no
    longer be like-for-like.  A code the replay did not answer is simply
    absent -- never synthesised.
    """
    parameters = json.loads(evidence.batch_request_parameters)
    requests = [
        DataRequest(
            str(parameters["endpoint"]),
            (str(symbol),),
            date.fromisoformat(str(parameters["start_date"])),
            date.fromisoformat(str(parameters["end_date"])),
            dict(parameters.get("params") or {}),
        )
        for symbol in parameters["symbols"]
    ]
    result = source.fetch_batch(requests)
    # Production outcomes carry the adapter's FetchResult; a fake source may
    # hand back the bare frame.  Either way it is the answer that code got.
    return {
        outcome.symbol: getattr(outcome.result, "frame", outcome.result)
        for outcome in result.outcomes
        if outcome.result is not None
    }


def replay_recorded_batches(
    source: object, store: BatchEvidenceStore, shas: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """Replay every batch the version bound to, and return frames by symbol.

    ``build_config.batch_request_evidence`` records hashes, so the resolution
    starts from the hash: an unresolvable one is skipped rather than turned
    into a guessed single-symbol re-ask.
    """
    frames: dict[str, pd.DataFrame] = {}
    for sha in shas:
        evidence = store.load_by_sha(sha)
        if evidence is None:
            continue
        frames.update(replay_batch(source, evidence))
    return frames


def _xingyao_daily_source(config):
    from stock_quant.data_sources.xingyao import XingyaoSource

    return XingyaoSource(config)


def _xingyao_factor_source(config):
    from stock_quant.data_sources.xingyao_factor import XingyaoFactorSource

    return XingyaoFactorSource(config)


def _source_for(name: str, endpoint: str, config: ProjectConfig):
    """The adapter that can re-ask this exact question.

    Dispatch is on the pair for xingyao, and only for xingyao: its factor
    channel answers through a wide-table API its daily adapter knows nothing
    about, so a source-prefix mapping alone would send the audit back with a
    request the supplier cannot honour -- or worse, report a comparison it
    never made.  The other three adapters serve all of their own endpoints, so
    their prefix dispatch stays as it is.  An unknown endpoint raises, and
    ``run`` counts that as an audit failure rather than a pass.
    """
    if name.startswith("tushare"):
        return TushareSource(config.sources["tushare"])
    if name.startswith("akshare"):
        return AkShareSource(config.sources["akshare"])
    if name.startswith("baostock"):
        return BaoStockSource(config.sources["baostock"])
    if name.startswith("xingyao"):
        if endpoint == "daily":
            return _xingyao_daily_source(config.sources["xingyao"])
        if endpoint == "backward_factor":
            return _xingyao_factor_source(config.sources["xingyao"])
    raise ValueError(
        f"no adapter for raw-snapshot source {name!r} endpoint {endpoint!r}"
    )


def _batch_replay_coverage(
    config: ProjectConfig,
    store_batch: BatchEvidenceStore,
    shas: Sequence[str],
) -> dict[tuple[str, str, str, str], pd.DataFrame]:
    """Map each snapshot a recorded batch produced to its replayed frame.

    Keyed the way ``run``'s evidence rows are keyed: ``(source, endpoint,
    request_key, file_sha256)``.  Each ``(source, endpoint)`` group is
    resolved and replayed once; a group whose replay source has no
    ``fetch_batch`` -- or whose resolution or replay raised -- stays
    uncovered, so its targets fall back to the per-symbol re-ask path and
    the audit keeps reporting instead of crashing.
    """
    resolved: dict[tuple[str, str], list[BatchRequestEvidence]] = {}
    for sha in shas:
        try:
            evidence = store_batch.load_by_sha(sha)
        except Exception:  # noqa: BLE001 - an unreadable record is not a crash
            continue
        if evidence is None:
            continue
        resolved.setdefault((evidence.source, evidence.endpoint), []).append(evidence)
    coverage: dict[tuple[str, str, str, str], pd.DataFrame] = {}
    for (name, endpoint), records in resolved.items():
        try:
            source = _source_for(name, endpoint, config)
        except Exception:  # noqa: BLE001 - an unresolvable group stays uncovered
            continue
        if getattr(source, "fetch_batch", None) is None:
            continue
        try:
            frames = replay_recorded_batches(
                source, store_batch, [record.sha256 for record in records]
            )
        except Exception:  # noqa: BLE001 - a failed replay leaves the group uncovered
            continue
        for record in records:
            for outcome in record.outcomes:
                frame = frames.get(outcome.symbol)
                if outcome.snapshot_file_sha256 is None or frame is None:
                    continue
                coverage[
                    (
                        record.source,
                        record.endpoint,
                        outcome.request_key,
                        outcome.snapshot_file_sha256,
                    )
                ] = frame
    return coverage


def run(
    root: Path,
    config: ProjectConfig,
    *,
    version: str | None = None,
    output: Path | None = None,
) -> int:
    """Re-fetch and compare; write the dated ops record.

    Returns the count of drifted snapshots plus the count of snapshots the
    audit could not compare at all: an unfinished audit is not a pass.
    """
    publisher = DatasetPublisher(root)
    pinned = version or publisher.current().version
    print(f"drift audit: dataset={pinned}")
    store = RawStore(root)
    build = _read_build_config(root, pinned)
    targets = _targets_from_build(build)
    coverage = _batch_replay_coverage(
        config, BatchEvidenceStore(root), _batch_hashes_from_build(build)
    )
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
        replayed = coverage.get(
            (
                evidence.source,
                evidence.endpoint,
                evidence.request_key,
                evidence.file_sha256,
            )
        )
        if replayed is not None:
            # The snapshot was produced by a recorded batch: compare against
            # the batch replay instead of degrading it to a per-symbol ask.
            # The metadata comes from the STORED manifest, so the re-saved
            # bytes differ from the audited ones only when the frame differs.
            try:
                fetched = store.save(
                    FetchResult(
                        source=evidence.source,
                        endpoint=evidence.endpoint,
                        request_key=evidence.request_key,
                        frame=replayed,
                        metadata=dict(snapshot.manifest.get("metadata") or {}),
                    )
                )
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
                    "note": f"{verdict} batch_replayed",
                }
            )
            continue
        parameters = snapshot.manifest.get("request_parameters", {})
        try:
            source = _source_for(evidence.source, evidence.endpoint, config)
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
    audit_failures = count_audit_failures(rows)
    if output is None:
        output = Path(root) / "docs" / "operations" / f"{date.today().isoformat()}-drift-audit.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_audit_record(pinned, rows, drifted=drifted, audit_failures=audit_failures),
        encoding="utf-8",
    )
    print(
        f"drift audit: {len(rows)} snapshots, {drifted} drifted, "
        f"{audit_failures} unfinished -> {output}"
    )
    return drifted + audit_failures


def main(argv: Sequence[str] | None = None) -> NoReturn:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--version", type=str, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    count = run(root, config, version=args.version, output=args.output)
    # POSIX keeps only the low 8 bits of the exit status, so a raw 256 (or any
    # multiple of 256) would reach the shell as 0 -- silently passing an audit
    # that never completed.  Clamp while preserving zero/non-zero semantics.
    sys.exit(min(count, 255))


if __name__ == "__main__":
    main()
