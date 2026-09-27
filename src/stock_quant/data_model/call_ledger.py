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


def render_call_ledger(
    sources: Mapping[str, object],
    reused: Mapping[str, Mapping[str, int]] | None = None,
    transport: Mapping[str, Mapping[str, Mapping[str, int]]] | None = None,
) -> dict[str, object]:
    """Normalized ledger rows: one entry per source.

    ``reused`` carries, per source x endpoint, how many requests were served
    from stored raw snapshots instead of the supplier (ADR-015).  ``transport``
    carries the *attempted* transport operations per source x endpoint --
    sessions established and code-bearing queries issued -- so retry and
    bisection attempts stay visible instead of hiding behind a success count;
    a batch call is one code-bearing query however many codes it carried.
    A name that appears only in ``reused`` or ``transport`` -- a source the
    run never got to record ``calls`` for -- still gets a row, so consumed
    quota is never dropped from the accounting just because the source object
    is missing; such a row renders ``calls`` 0 and empty ``endpoints``.
    """
    rows: dict[str, object] = {}
    names = set(sources) | set(reused or {}) | set(transport or {})
    for name in sorted(names):
        total, endpoints = _summarize_calls(getattr(sources.get(name), "calls", 0))
        reused_endpoints: dict[str, int] = {}
        reused_for_source = (reused or {}).get(name)
        if isinstance(reused_for_source, Mapping):
            reused_endpoints = {
                str(endpoint): int(count)
                for endpoint, count in sorted(reused_for_source.items())
            }
        transport_endpoints: dict[str, dict[str, int]] = {}
        transport_for_source = (transport or {}).get(name)
        if isinstance(transport_for_source, Mapping):
            transport_endpoints = {
                str(endpoint): {
                    "sessions": int(counts.get("sessions", 0)),
                    "code_queries": int(counts.get("code_queries", 0)),
                }
                for endpoint, counts in sorted(transport_for_source.items())
            }
        rows[name] = {
            "calls": total,
            "endpoints": endpoints,
            "reused": reused_endpoints,
            "transport": transport_endpoints,
        }
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
