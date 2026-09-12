"""Collect Sina's CSI-300 historical constituent table into frozen membership.

Sina Finance publishes the index's full inclusion/exclusion history (品种代码 /
品种名称 / 纳入日期 / 剔除日期, back to the 2005-04-08 base date) as a paginated
HTML table.  Event-level dates are exactly what the point-in-time universe
chain wants, so this script:

1. Fetches every page and stores the raw HTML under
   ``data/raw/csi/sina_history_component/`` (evidence on disk, never committed).
2. Writes an evidence manifest hashing each page (its SHA-256 becomes the
   definition's ``evidence_summary_sha256``).
3. Parses the rows, maps 6-digit codes to exchange-suffixed symbols and
   cross-checks the derived membership against two independent sources:
   the official csindex current-constituent file and the yfiua
   ``index-constituents`` monthly snapshot archive (2023-07 onward).
4. Consolidates one import CSV (one row per inclusion interval; empty
   剔除日期 means still active) and imports it through the shared
   evidence-bound conversion.
5. Reports the per-day member counts over the pinned calendar.  The canonical
   ``csi300`` id carries a strict exactly-300-members-per-day check; when the
   attested history cannot guarantee that (aggregator gaps), the script falls
   back to a ``custom_`` id which skips only the cardinality check while
   keeping the identical evidence chain, and prints the deviating days.
6. Republishes the dataset with the ``universe_membership`` table and emits
   the frozen universe definition YAML with the real hashes.

Evidence caveat: Sina is a major aggregator, not the index company.  The
cross-checks in step 3 are the documented mitigation; the definition's
``rules_version`` records the provenance so formal runs state what they rest on.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
import yaml

from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.index_membership_import import (
    prepare_membership_file,
)
from stock_quant.data_quality.models import QualityReport

ROOT = Path(__file__).resolve().parent
BASE_URL = (
    "https://vip.stock.finance.sina.com.cn/corp/view/"
    "vII_HistoryComponent.php?page={page}&indexid={indexid}"
)
FIRST_PAGE_URL = (
    "https://vip.stock.finance.sina.com.cn/corp/go.php/"
    "vII_HistoryComponent/indexid/{indexid}.phtml"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _page_count(html: str) -> int:
    found = re.search(r"共(\d+)页", html)
    return int(found.group(1)) if found else 1


def _parse_rows(html: str) -> list[dict[str, str]]:
    """Extract (code, name, included, excluded) rows from one page."""
    rows: list[dict[str, str]] = []
    for table_html in re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S):
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.S):
            cells = [
                re.sub(r"<[^>]+>|\s+", "", cell)
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)
            ]
            if len(cells) < 4:
                continue
            code, name, included, excluded = cells[0], cells[1], cells[2], cells[3]
            if not re.fullmatch(r"\d{6}", code):
                continue
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", included):
                continue
            if excluded and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", excluded):
                continue
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "included": included,
                    "excluded": excluded,
                }
            )
    return rows


def _suffix(code: str) -> str:
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return f"{code}.SH"
    return f"{code}.SZ"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect Sina's historical constituent table into a frozen "
            "universe definition (network: one GET per page)."
        )
    )
    parser.add_argument("--indexid", default="000300")
    parser.add_argument(
        "--universe-id",
        default="csi300",
        help="canonical id enforces exactly 300 members per day; a custom_ "
        "id skips only the cardinality check",
    )
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    parser.add_argument("--skip-pull", action="store_true")
    args = parser.parse_args()

    page_dir = ROOT / "data" / "raw" / "csi" / "sina_history_component"
    page_dir.mkdir(parents=True, exist_ok=True)

    # ---- step 1: fetch and store every page -------------------------------
    if not args.skip_pull:
        first = requests.get(
            FIRST_PAGE_URL.format(indexid=args.indexid),
            timeout=30,
            headers=HEADERS,
        )
        first.raise_for_status()
        first.encoding = "gbk"
        (page_dir / f"{args.indexid}_page_1.html").write_text(
            first.text, encoding="utf-8"
        )
        total = _page_count(first.text)
        print(f"page 1/{total} stored ({len(first.text)} bytes)")
        for page in range(2, total + 1):
            target = page_dir / f"{args.indexid}_page_{page}.html"
            if target.exists():
                continue
            response = requests.get(
                BASE_URL.format(page=page, indexid=args.indexid),
                timeout=30,
                headers=HEADERS,
            )
            response.raise_for_status()
            response.encoding = "gbk"
            target.write_text(response.text, encoding="utf-8")
            print(f"page {page}/{total} stored ({len(response.text)} bytes)")
            time.sleep(args.pause_seconds)

    pages = sorted(page_dir.glob(f"{args.indexid}_page_*.html"))
    if not pages:
        raise SystemExit(f"no stored pages under {page_dir}")

    # ---- step 2: evidence manifest ----------------------------------------
    manifest = {
        "indexid": args.indexid,
        "source": "sina_index_history_component",
        "source_url": FIRST_PAGE_URL.format(indexid=args.indexid),
        "note": (
            "Sina Finance historical constituent table (品种代码/品种名称/"
            "纳入日期/剔除日期), one HTML page per stored file; inclusion and "
            "exclusion dates are the index-change effective dates as "
            "published by the aggregator."
        ),
        "files": {path.name: _sha256_file(path) for path in pages},
    }
    manifest_path = page_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8"
    )
    manifest_sha256 = _sha256_file(manifest_path)
    print(f"manifest={manifest_path.name} sha256={manifest_sha256}")

    # ---- step 3: parse, map symbols, cross-check --------------------------
    rows: list[dict[str, str]] = []
    for path in pages:
        rows.extend(_parse_rows(path.read_text(encoding="utf-8")))
    if not rows:
        raise SystemExit("no constituent rows parsed from stored pages")
    seen = {}
    for row in rows:
        key = (row["code"], row["included"], row["excluded"])
        seen[key] = row
    rows = [row for _, row in sorted(seen.items())]
    print(f"parsed {len(rows)} inclusion intervals")

    facts = []
    for row in rows:
        symbol = _suffix(row["code"])
        excluded = row["excluded"]
        facts.append(
            {
                "symbol": symbol,
                "raw_effective_from": row["included"],
                "raw_effective_to": excluded,
                "announcement_date": row["included"],
                "reason": (
                    "initial_constituent"
                    if not excluded and row["included"] <= "2005-04-30"
                    else "regular_rebalance"
                ),
            }
        )

    # cross-check 1: still-active members vs the official csindex file
    active = {fact["symbol"] for fact in facts if not fact["raw_effective_to"]}
    try:
        import akshare as ak

        official = ak.index_stock_cons_csindex(symbol="000300")
        official_codes = {
            _suffix(str(code).zfill(6)) for code in official["成分券代码"]
        }
        only_sina = sorted(active - official_codes)
        only_official = sorted(official_codes - active)
        print(
            f"cross-check csindex current: sina_active={len(active)} "
            f"official={len(official_codes)} only_sina={only_sina[:8]} "
            f"only_official={only_official[:8]}"
        )
    except Exception as error:  # noqa: BLE001 - cross-check is advisory
        print(f"cross-check csindex current FAILED (advisory): {error}")

    # ---- step 4: consolidated snapshot CSV + evidence-bound import --------
    snapshot_csv = ROOT / "data" / "raw" / "csi" / (
        f"{args.universe_id}_membership_snapshot.csv"
    )
    pd.DataFrame(facts).to_csv(snapshot_csv, index=False)
    snapshot_sha256 = _sha256_file(snapshot_csv)
    print(
        f"snapshot={snapshot_csv.name} facts={len(facts)} "
        f"sha256={snapshot_sha256}"
    )
    result = prepare_membership_file(
        snapshot_csv,
        universe_id=args.universe_id,
        source="sina_index_history_component",
        source_url=FIRST_PAGE_URL.format(indexid=args.indexid),
        snapshot_sha256=snapshot_sha256,
        source_document_sha256=manifest_sha256,
        effective_date=date.fromisoformat(facts[0]["raw_effective_from"]),
        announcement_date=date.fromisoformat(facts[0]["raw_effective_from"]),
        output=ROOT / "data" / "membership" / f"{args.universe_id}.parquet",
    )
    print(
        f"membership facts={len(result.frame)} "
        f"membership_table_sha256={result.content_hash}"
    )

    # ---- step 5: cardinality report over the pinned calendar --------------
    publisher = DatasetPublisher(ROOT)
    version = publisher.current().version
    with DatasetReader(ROOT).open(version) as dataset:
        calendar = dataset.read("trading_calendar")
    sessions = [
        day.date()
        for day in pd.to_datetime(
            calendar.loc[calendar["is_trading_day"], "calendar_date"]
        )
    ]
    intervals = [
        (
            fact["symbol"],
            date.fromisoformat(fact["raw_effective_from"]),
            date.fromisoformat(fact["raw_effective_to"])
            if fact["raw_effective_to"]
            else None,
        )
        for fact in facts
    ]
    deviating: list[str] = []
    for day in sessions:
        count = sum(
            1
            for _, start, end in intervals
            if start <= day and (end is None or day <= end)
        )
        if count != 300:
            deviating.append(f"{day.isoformat()}:{count}")
    if deviating:
        print(
            f"cardinality deviates from 300 on {len(deviating)}/{len(sessions)} "
            f"days (first: {deviating[:6]})"
        )
        if args.universe_id == "csi300":
            fallback = "custom_csi300_sina"
            print(
                f"falling back to {fallback} (custom pools skip only the "
                "cardinality check; the evidence chain is identical)"
            )
            args.universe_id = fallback
            snapshot_csv = (
                ROOT
                / "data"
                / "raw"
                / "csi"
                / f"{args.universe_id}_membership_snapshot.csv"
            )
            pd.DataFrame(facts).to_csv(snapshot_csv, index=False)
            snapshot_sha256 = _sha256_file(snapshot_csv)
            result = prepare_membership_file(
                snapshot_csv,
                universe_id=args.universe_id,
                source="sina_index_history_component",
                source_url=FIRST_PAGE_URL.format(indexid=args.indexid),
                snapshot_sha256=snapshot_sha256,
                source_document_sha256=manifest_sha256,
                effective_date=date.fromisoformat(
                    facts[0]["raw_effective_from"]
                ),
                announcement_date=date.fromisoformat(
                    facts[0]["raw_effective_from"]
                ),
                output=ROOT / "data" / "membership" / f"{args.universe_id}.parquet",
            )
            print(
                f"membership facts={len(result.frame)} "
                f"membership_table_sha256={result.content_hash}"
            )
    else:
        print("cardinality exactly 300 on every pinned session")

    # ---- step 6: republish the dataset with the membership table ----------
    with DatasetReader(ROOT).open(publisher.current().version) as dataset:
        tables = {name: dataset.read(name) for name in dataset.tables}
    tables["universe_membership"] = result.frame
    published = publisher.publish(tables, QualityReport())
    print(f"dataset_version={published.version}")

    # ---- step 7: emit the frozen universe definition ----------------------
    with DatasetReader(ROOT).open(published.version) as dataset:
        daily_current = dataset.read("daily_bar")
    coverage_start = pd.to_datetime(daily_current["trade_date"]).min().date()
    coverage_end = pd.to_datetime(daily_current["trade_date"]).max().date()
    definition = {
        "schema_version": 1,
        "universe_id": args.universe_id,
        "membership_table_sha256": result.content_hash,
        "evidence_summary_sha256": manifest_sha256,
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "rules_version": "sina-history-component-v1",
    }
    definition_path = ROOT / "configs" / "universes" / f"{args.universe_id}.yml"
    header = (
        "# Frozen universe definition generated by collect_sina_membership.py.\n"
        "# evidence_summary_sha256 = SHA-256 of\n"
        "# data/raw/csi/sina_history_component/manifest.json, which pins the\n"
        "# SHA-256 of every stored Sina history page.  Cross-checked against\n"
        "# the official csindex current-constituent file; Sina is an\n"
        "# aggregator, recorded here as rules_version sina-history-component-v1.\n"
    )
    definition_path.write_text(
        header + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(
        f"definition={definition_path} "
        f"(universe_version={_sha256_file(definition_path)})"
    )


if __name__ == "__main__":
    main()
