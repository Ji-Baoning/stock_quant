"""Per-update call ledger: endpoint x count x quota consumption (spec D5.5)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path


def _summarize_calls(calls: object) -> tuple[int, dict[str, int]]:
    """Normalize a source's ``calls`` attribute into (total, by endpoint)."""
    if calls is None:
        return 0, {}
    if isinstance(calls, int):
        return calls, {}
    records = list(calls)
    endpoints: dict[str, int] = {}
    for record in records:
        key = (
            str(record.get("endpoint"))
            if isinstance(record, Mapping)
            else "unknown"
        )
        endpoints[key] = endpoints.get(key, 0) + 1
    return len(records), endpoints


def render_call_ledger(sources: Mapping[str, object]) -> dict[str, object]:
    """Normalized ledger rows: one entry per source."""
    rows: dict[str, object] = {}
    for name, source in sorted(sources.items()):
        total, endpoints = _summarize_calls(getattr(source, "calls", 0))
        rows[name] = {"calls": total, "endpoints": endpoints}
    return rows


def write_call_ledger(
    project_root: Path, run_id: str, payload: Mapping[str, object]
) -> Path:
    """Persist the ledger under ``data/runs/<run_id>/call_ledger.json``."""
    path = Path(project_root) / "data" / "runs" / run_id / "call_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path
