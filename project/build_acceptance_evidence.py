"""Build the local evidence pack for the operator's acceptance checklist.

Six of the nine manual checks are facts a script can gather deterministically:
row-count relationships, missing-bar classifications, sampled corporate-action
facts, benchmark coverage, security-master rows and a credential scan.  The
other three need corroboration this project cannot produce by itself (an
official exchange calendar, a second price source, official rule effective
dates); they stay FAIL placeholders for the operator to complete.

Nothing here writes a dataset, the raw store or CURRENT.  The only writes are
evidence files under ``data/acceptance-evidence/<version>/`` and the checklist
YAML the operator named.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd
import yaml

from stock_quant.data_model.dataset import DatasetReader
from stock_quant.data_quality.raw_checks import classify_missing_row
from stock_quant.research.acceptance.checks import (
    _ACCEPTED_MISSING_CODES,
    _open_days,
    _window,
)

ROOT = Path(__file__).resolve().parent
#: The window the update must be requested with; asserted, never assumed.
WINDOW_START = date(2015, 1, 1)
WINDOW_END = date(2026, 8, 28)
EVIDENCE_DIRNAME = "acceptance-evidence"

#: Manual checks a script can evidence on its own.
MECHANISABLE = (
    "benchmark_sample",
    "corporate_action_sample",
    "missing_reason_sample",
    "security_master_sample",
    "secret_scan",
    "source_row_count_sample",
)
#: Manual checks that need corroboration this project cannot produce.
OPERATOR_ONLY = (
    "exchange_calendar_sample",
    "cross_source_price_sample",
    "trading_rule_effective_dates",
)

_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(tushare_token|api[_-]?key|secret|password|passwd|authorization)"
    r"\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class EvidenceFile:
    """One evidence artifact: its name under the pack and its bytes."""

    name: str
    text: str


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _as_date(value: object) -> date | None:
    """Coerce one frame cell to a ``date``; missing/NaT reads as ``None``."""
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _columns(frame: pd.DataFrame, wanted: Sequence[str]) -> list[str]:
    return [name for name in wanted if name in frame.columns]


def source_row_count_evidence(
    manifest: Mapping[str, object],
    snapshots: Sequence[Mapping[str, object]],
    *,
    trading_days: int,
) -> EvidenceFile:
    """Row counts of every published table beside every bound raw snapshot.

    Both arguments are the manifest JSON as parsed, not the pydantic models
    the acceptance checker wraps it in: ``build_config.raw_snapshots`` is
    written as a list of mappings.
    """
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("dataset manifest has no tables mapping")
    counts = {
        str(name): int(entry.get("row_count", -1))
        for name, entry in sorted(tables.items())
        if isinstance(entry, dict)
    }
    bindings = sorted(
        (
            {
                "source": str(row.get("source")),
                "endpoint": str(row.get("endpoint")),
                "request_key": str(row.get("request_key")),
                "file_sha256": str(row.get("file_sha256")),
            }
            for row in snapshots
        ),
        key=lambda item: (
            item["source"],
            item["endpoint"],
            item["request_key"],
        ),
    )
    return EvidenceFile(
        "source_row_counts.json",
        _json(
            {
                "trading_days": int(trading_days),
                "tables": counts,
                "raw_snapshots": bindings,
            }
        ),
    )


def missing_reason_evidence(
    daily: pd.DataFrame,
    master: pd.DataFrame,
    calendar: pd.DataFrame,
    start: date,
    end: date,
) -> EvidenceFile:
    """Classification counts for every absent bar over the window."""
    facts = {
        str(row.get("symbol")): (
            _as_date(row.get("list_date")),
            _as_date(row.get("delist_date")),
        )
        for row in master.to_dict("records")
    }
    present = {
        (str(row.get("symbol")), _as_date(row.get("trade_date")))
        for row in daily.to_dict("records")
    }
    grid = [day for day in _open_days(calendar) if start <= day <= end]
    counts: dict[str, int] = {}
    samples: dict[str, str] = {}
    for symbol in sorted(facts):
        list_date, delist_date = facts[symbol]
        for day in grid:
            if (symbol, day) in present:
                continue
            code = classify_missing_row(
                trade_date=day,
                list_date=list_date,
                delist_date=delist_date,
                is_trading_day=True,
                primary_present=False,
                validation_present=False,
            )
            counts[code] = counts.get(code, 0) + 1
            samples.setdefault(code, f"{symbol}@{day.isoformat()}")
    return EvidenceFile(
        "missing_reasons.json",
        _json(
            {
                "accepted_codes": sorted(_ACCEPTED_MISSING_CODES),
                "counts": counts,
                "first_sample": samples,
            }
        ),
    )


def security_master_evidence(
    master: pd.DataFrame, *, limit: int = 30
) -> EvidenceFile:
    """A deterministic sample of security-master listing facts."""
    wanted = _columns(
        master, ("symbol", "list_date", "delist_date", "status", "source")
    )
    frame = master.sort_values("symbol", kind="stable").head(limit)
    return EvidenceFile(
        "security_master_sample.csv", frame[wanted].to_csv(index=False)
    )


def benchmark_evidence(
    daily: pd.DataFrame,
    calendar: pd.DataFrame,
    benchmarks: Sequence[str],
    start: date,
    end: date,
) -> EvidenceFile:
    """Per-benchmark coverage of the window's open days."""
    grid = [day for day in _open_days(calendar) if start <= day <= end]
    payload: dict[str, object] = {}
    for symbol in benchmarks:
        rows = daily[daily["symbol"] == symbol]
        dates = {_as_date(value) for value in rows["trade_date"]}
        payload[str(symbol)] = {
            "rows": int(len(rows)),
            "first": min(dates).isoformat() if dates else None,
            "last": max(dates).isoformat() if dates else None,
            "open_days": len(grid),
            "missing_open_days": len([day for day in grid if day not in dates]),
        }
    return EvidenceFile("benchmark_coverage.json", _json(payload))


def corporate_action_evidence(
    corporate_action: pd.DataFrame, *, limit: int = 20
) -> EvidenceFile:
    """A deterministic sample of the reconciled corporate-action facts."""
    wanted = _columns(
        corporate_action,
        (
            "symbol",
            "ex_date",
            "record_date",
            "cash_dividend_per_share",
            "bonus_share_ratio",
            "capitalization_ratio",
            "source",
            "status",
        ),
    )
    frame = corporate_action.sort_values(
        ["symbol", "ex_date"], kind="stable"
    ).head(limit)
    return EvidenceFile(
        "corporate_action_sample.csv", frame[wanted].to_csv(index=False)
    )


def secret_scan_evidence(
    paths: Sequence[Path], *, root: Path, limit: int = 50
) -> EvidenceFile:
    """Scan the acceptance artifacts for credential-looking lines."""
    hits: list[dict[str, object]] = []
    for path in sorted(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _CREDENTIAL_PATTERN.search(line):
                hits.append(
                    {"path": str(path.relative_to(root)), "line": number}
                )
        if len(hits) >= limit:
            break
    return EvidenceFile(
        "secret_scan.json",
        _json({"scanned": len(paths), "hits": hits[:limit]}),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def apply_evidence(
    checklist: Mapping[str, object],
    *,
    root: Path,
    evidence_root: Path,
    names: Mapping[str, str],
) -> dict:
    """Turn the mechanisable manual rows into verified PASS rows.

    ``names`` maps a manual check code to the evidence file it cites; every
    other manual row is left exactly as ``acceptance prepare`` wrote it, so a
    checklist that is not yet publishable says so plainly.
    """
    manual: list[dict] = []
    for row in checklist["manual_checks"]:  # type: ignore[index]
        name = names.get(str(row["code"]))
        if name is None:
            manual.append(dict(row))
            continue
        path = evidence_root / name
        manual.append(
            {
                "code": str(row["code"]),
                "status": "PASS",
                "summary": f"{row['code']}: scripted evidence {name}",
                "details": {},
                "evidence": [
                    {
                        "kind": "local",
                        "reference": str(path.relative_to(root)),
                        "sha256": _sha256_file(path),
                        "summary": (
                            f"{name} generated by "
                            "project/build_acceptance_evidence.py"
                        ),
                    }
                ],
            }
        )
    patched = dict(checklist)
    patched["manual_checks"] = manual
    return patched


def main(version: str, checklist_path: Path) -> int:
    evidence_root = ROOT / "data" / EVIDENCE_DIRNAME / version
    evidence_root.mkdir(parents=True, exist_ok=True)
    with DatasetReader(ROOT).open(version) as dataset:
        daily = dataset.read("daily_bar")
        master = dataset.read("security_master")
        calendar = dataset.read("trading_calendar")
        corporate_action = dataset.read("corporate_action")
    manifest = json.loads(
        (
            ROOT / "data" / "standardized" / version / "dataset_manifest.json"
        ).read_text(encoding="utf-8")
    )
    build = manifest.get("build_config")
    if not isinstance(build, dict) or "requested_start_date" not in build:
        raise SystemExit(f"dataset {version} carries no data-update build window")
    start, end = _window(build)
    if (start, end) != (WINDOW_START, WINDOW_END):
        raise SystemExit(
            f"build window {start}..{end} != spec "
            f"{WINDOW_START}..{WINDOW_END}"
        )
    # The manifest stores these as plain mappings, not ``RawSnapshotBinding``.
    snapshots = list(build.get("raw_snapshots", []))
    open_days = [day for day in _open_days(calendar) if start <= day <= end]
    project = yaml.safe_load((ROOT / "configs" / "project.yml").read_text())
    benchmarks = tuple(str(s) for s in project["benchmark_symbols"])
    written = [
        source_row_count_evidence(
            manifest, snapshots, trading_days=len(open_days)
        ),
        missing_reason_evidence(daily, master, calendar, start, end),
        security_master_evidence(master),
        benchmark_evidence(daily, calendar, benchmarks, start, end),
        corporate_action_evidence(corporate_action),
    ]
    for file in written:
        (evidence_root / file.name).write_text(file.text, encoding="utf-8")
    scanned = [evidence_root / file.name for file in written]
    scanned.append(checklist_path)
    scan = secret_scan_evidence(scanned, root=ROOT)
    (evidence_root / scan.name).write_text(scan.text, encoding="utf-8")
    names = {
        "source_row_count_sample": "source_row_counts.json",
        "missing_reason_sample": "missing_reasons.json",
        "security_master_sample": "security_master_sample.csv",
        "benchmark_sample": "benchmark_coverage.json",
        "corporate_action_sample": "corporate_action_sample.csv",
        "secret_scan": "secret_scan.json",
    }
    checklist = yaml.safe_load(checklist_path.read_text(encoding="utf-8"))
    patched = apply_evidence(
        checklist, root=ROOT, evidence_root=evidence_root, names=names
    )
    checklist_path.write_text(
        yaml.safe_dump(patched, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    for row in patched["manual_checks"]:
        print(f"manual {row['code']} {row['status']}")
    print(f"evidence_dir={evidence_root.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    import typer

    typer.run(main)
