#!/usr/bin/env python
"""One-off audit of what the existing raw snapshots say about their own origin.

Design spec §5.  This report is deliberately **not** an answer to "which
provider produced each snapshot": the historical manifests carry no
discriminating evidence, and the design refuses to pretend otherwise.  What it
*can* say is the distribution of producing environments -- which SDK versions
appear, and whether those versions are installed in the interpreter running this
script -- which decides whether these datasets can be rebuilt as-is, here and
now.

That last claim is deliberately narrow.  A version missing from this
interpreter is *not* evidence that it is absent from the machine: it may live in
another conda env, another virtualenv, or an uninstalled cache.  This script
scans none of those, so it says "当前解释器未安装" and stops there.

The ``tushare.pro.*`` label is the clearest case of why: it proves neither that
the producer was the official API nor that it was not, because the official SDK
and a relay that rewrote its base URL are the same client talking to a
different host.  The report therefore says "不对历史 provider 下结论".

Snapshots whose environment cannot be established are reported as ``unknown``.
That word is a *conclusion field* of this report and has nothing to do with the
reserved ``transport_id`` of design §2.3, which is a different concept about a
different layer.

Run from the repository root or the project directory:
    python project/audit_raw_provenance.py
    python project/audit_raw_provenance.py --no-report

Exit codes: 0 = a complete audit (some manifest read, none skipped); 1 = no
manifest was found at all; 2 = the audit is incomplete (a manifest was skipped).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.project_root import resolve_project_root

UNKNOWN = "unknown"
#: Present in the *running interpreter* -- not "present on this machine".
#: The two are different claims, and this script can only make the first.
INSTALLED = "installed"


@dataclass(frozen=True)
class SnapshotRecord:
    """One stored snapshot's self-reported origin."""

    source: str
    endpoint: str
    supplier_endpoint: str
    sdk_version: str
    transport_id: str | None
    request_timestamp: str | None


@dataclass(frozen=True)
class SkippedManifest:
    """A ``manifest.json`` that could not be counted."""

    path: Path
    reason: str


@dataclass(frozen=True)
class Scan:
    """Everything ``scan`` managed to read, and everything it did not.

    Both halves are returned.  Dropping the unreadable ones would make every
    count derived from ``records`` look complete when it is not.
    """

    records: list[SnapshotRecord]
    skipped: list[SkippedManifest]


@dataclass(frozen=True)
class Summary:
    total: int
    by_source: dict[str, int]
    sdk_rows: list[dict[str, object]]


def scan(store_root: Path) -> Scan:
    """Read every ``manifest.json`` below ``<store_root>/data/raw``.

    Both the four-segment legacy layout and the five-segment layout that
    carries ``transport_id`` are read, because the whole point is to describe
    what is already on disk.
    """
    raw = Path(store_root) / "data" / "raw"
    records: list[SnapshotRecord] = []
    skipped: list[SkippedManifest] = []
    if not raw.is_dir():
        return Scan(records=records, skipped=skipped)
    for manifest_path in sorted(raw.rglob("manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except OSError as exc:
            skipped.append(SkippedManifest(manifest_path, f"unreadable: {exc}"))
            continue
        except json.JSONDecodeError as exc:
            skipped.append(SkippedManifest(manifest_path, f"not valid JSON: {exc}"))
            continue
        if not isinstance(manifest, dict) or "source" not in manifest:
            skipped.append(SkippedManifest(manifest_path, "no 'source' field"))
            continue
        records.append(
            SnapshotRecord(
                source=str(manifest["source"]),
                endpoint=str(manifest.get("endpoint", UNKNOWN)),
                supplier_endpoint=str(
                    manifest.get("supplier_endpoint", manifest.get("endpoint", UNKNOWN))
                ),
                sdk_version=str(manifest.get("sdk_version", UNKNOWN)),
                transport_id=manifest.get("transport_id"),
                request_timestamp=manifest.get("request_timestamp"),
            )
        )
    return Scan(records=records, skipped=skipped)


def local_sdk_versions() -> dict[str, set[str]]:
    """The SDK versions installed in this interpreter, per source family."""
    versions: dict[str, set[str]] = {}
    for family, module_name in (
        ("tushare", "tushare"),
        ("akshare", "akshare"),
        ("baostock", "baostock"),
    ):
        try:
            module = __import__(module_name)
        except ImportError:  # pragma: no cover - depends on the environment
            versions[family] = set()
        else:
            versions[family] = {str(getattr(module, "__version__", UNKNOWN))}
    return versions


def summarise(
    records: list[SnapshotRecord], *, local_sdk_versions: dict[str, set[str]]
) -> Summary:
    """Group by (source, sdk_version) and classify each group's environment."""
    groups: Counter[tuple[str, str]] = Counter()
    for record in records:
        groups[(record.source, record.sdk_version)] += 1
    sdk_rows: list[dict[str, object]] = []
    for (source, sdk_version), count in sorted(groups.items()):
        installed = sdk_version in local_sdk_versions.get(source, set())
        sdk_rows.append(
            {
                "source": source,
                "sdk_version": sdk_version,
                "count": count,
                "provenance": INSTALLED if installed else UNKNOWN,
            }
        )
    return Summary(
        total=len(records),
        by_source=dict(Counter(record.source for record in records)),
        sdk_rows=sdk_rows,
    )


def report(
    records: list[SnapshotRecord],
    *,
    skipped: Sequence[SkippedManifest] = (),
    today: date | None = None,
) -> str:
    """Render the audit as a committable operations report."""
    day = (today or date.today()).isoformat()
    summary = summarise(records, local_sdk_versions=local_sdk_versions())
    lines = [
        f"# 存量 raw 快照出处审计（{day}）",
        "",
        "来源：`project/audit_raw_provenance.py`（设计规格 §5，落地阶段 2）。",
        "",
        "## 这次审计能得出什么、不能得出什么",
        "",
        "- **不能**判定每份快照的真实 provider。历史 manifest 没有留下判别依据，",
        "  本审计**不对历史 provider 下结论**。`tushare.pro.*` 这个标签既证明不了",
        "  是官方、也证明不了不是官方 —— 官方 SDK 与会话基址被改写的中转是同一个",
        "  客户端、不同的 host，标签本身不具判别力。",
        "- **能**判定产出环境的分布：哪些 SDK 版本出现在**当前解释器**、哪些不出现。",
        "- 本脚本只检查运行它的那个解释器。SDK 装在别的 conda 环境、别的虚拟环境或",
        "  缓存里，本报告看不见，因此**只断言「当前解释器未安装」**，不断言「本机",
        "  不存在」；也不据此断言数据集不能复现——那需要扫描多环境与缓存，本脚本",
        "  不做这件事。",
        "- 反查不出产出环境的记 `unknown`（本报告的**结论字段**，与 §2.3 的保留字",
        "  `transport_id` 无关），不猜、不重标。是否需要用新标注重建，由 owner 决定，",
        "  不在本脚本内自动进行。",
        "",
        "## 总量",
        "",
        f"共 {summary.total} 份快照。",
        "",
        "| 源 | 份数 |",
        "| --- | --- |",
    ]
    for source, count in sorted(summary.by_source.items()):
        lines.append(f"| `{source}` | {count} |")
    lines += [
        "",
        "## SDK 版本分布与产出环境",
        "",
        "| 源 | SDK 版本 | 份数 | 当前解释器 |",
        "| --- | --- | --- | --- |",
    ]
    for row in summary.sdk_rows:
        lines.append(
            f"| `{row['source']}` | `{row['sdk_version']}` | {row['count']} "
            f"| {row['provenance']} |"
        )
    unknown_rows = [row for row in summary.sdk_rows if row["provenance"] == UNKNOWN]
    lines += ["", "## 结论", ""]
    if skipped:
        # Stated here, not only in the trailing section: a reader who stops at
        # the conclusion must still learn that the inventory is partial.
        lines += [
            f"**本次审计不完整**：{len(skipped)} 个 `manifest.json` 未能读取，"
            "下面的总量与分布只覆盖读到的那部分。未读取的路径与原因见文末"
            "「未能读取的 manifest」。",
            "",
        ]
    if unknown_rows:
        total_unknown = sum(int(row["count"]) for row in unknown_rows)
        lines.append(
            f"{total_unknown} 份快照的产出环境**当前解释器未安装**（SDK 版本组合与"
            "本解释器安装的不符），记 `unknown`。这不等于本机没有该环境：SDK 可能"
            "装在别的环境或缓存里，本脚本不扫描那些位置。是否需要用新标注重建，由"
            "owner 决定。"
        )
    else:
        lines.append("所有快照的 SDK 版本组合都出现在当前解释器里。")
    if skipped:
        lines += [
            "",
            "## 未能读取的 manifest",
            "",
            f"{len(skipped)} 个 `manifest.json` 未能读取，**未计入上面的总量**：",
            "",
        ]
        for item in skipped:
            lines.append(f"- `{item.path}` —— {item.reason}")
    lines.append("")
    return "\n".join(lines)


def exit_code(
    records: Sequence[SnapshotRecord], skipped: Sequence[SkippedManifest]
) -> int:
    """The process status for a scan result.

    ``1`` means *no manifest was found at all*, not "no record was read":
    every manifest ``scan`` finds becomes either a record or a skipped entry,
    so ``records or skipped`` being false is exactly "found nothing" -- an
    empty store, or a ``--root`` that points somewhere else.  A non-empty
    ``skipped`` is an incomplete audit whether or not records were also read,
    so it outranks the success case; only a non-empty ``records`` with nothing
    skipped is a complete inventory.
    """
    if skipped:
        return 2
    return 0 if records else 1


def run(
    root: Path,
    config: ProjectConfig,
    *,
    report_path: Path | None = None,
    no_report: bool = False,
) -> int:
    """Audit the raw store under ``root`` and optionally write the report.

    The default report path derives from ``root`` (``<root>/docs/operations``),
    never from this file's location.
    """
    if report_path is None:
        report_path = (
            root
            / "docs"
            / "operations"
            / f"raw-provenance-audit-{date.today().isoformat()}.md"
        )
    result = scan(root)
    # Built unconditionally: even an all-unreadable store has something to say,
    # and its report lists the paths and reasons instead of vanishing.
    text = report(result.records, skipped=result.skipped)
    if result.records or result.skipped:
        sys.stdout.write(text)
        if not no_report:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(text, encoding="utf-8")
            print(f"report: {report_path}")
    else:
        # Nothing found at all.  The default report path is a *committed*
        # ``docs/operations/raw-provenance-audit-<today>.md``, so a wrong
        # ``--root`` must not overwrite that audit with a "0 snapshots" one --
        # hence no report file is written here, and this line is the whole
        # behaviour.
        print(f"no snapshots found under {root / 'data' / 'raw'}")
    return exit_code(result.records, result.skipped)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="report file (default: <root>/docs/operations/"
        "raw-provenance-audit-<today>.md)",
    )
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(
        root, config, report_path=args.report, no_report=args.no_report
    )


if __name__ == "__main__":
    raise SystemExit(main())
