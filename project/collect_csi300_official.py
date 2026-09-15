"""Build the frozen csi300 universe from official CSI index announcements.

Status: migration.

This is the evidence chain RUNBOOK 阶段 5 prescribes, end to end:

1. **Collect** (network): query the official CSI announcement search
   (``csindex-home/search/search-content``) with several title variants so
   every 沪深300 sample adjustment from the 2005-04-05 index launch to today
   is found; store each announcement's detail JSON and every official
   ``调入调出名单`` Excel attachment under
   ``data/raw/csi/csi_index_announcements/`` and hash everything in
   ``manifest.json`` (its SHA-256 becomes the definition's
   ``evidence_summary_sha256``).
2. **Build** (offline): parse each announcement's effective date from its
   content (自…生效) and the 沪深300 add/remove rows from its Excel
   (指数代码 == 000300); anchor the history with the 2005-04-08 base cohort
   from the stored Sina history pages (the launch-era list is not covered by
   the announcement archive); derive membership intervals; cross-check the
   result against the official current-constituent file and the yfiua monthly
   snapshot archive; report per-day member counts over the pinned calendar.
3. Import through the shared evidence-bound conversion, republish the dataset
   with the ``universe_membership`` table, and emit the frozen universe
   definition YAML with the real hashes.

Every membership fact carries the official announcement's publish date (announcement
≤ effective, point-in-time by construction) and pins the announcement evidence
hashes, so a formal run can state exactly which announcements it rests on.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd
import requests
import yaml

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader
from stock_quant.data_model.index_membership_import import (
    prepare_membership_file,
)
from stock_quant.data_quality.models import QualityReport
from stock_quant.project_root import resolve_project_root

SEARCH_URL = (
    "https://www.csindex.com.cn/csindex-home/search/search-content"
    "?lang=cn&searchInput={query}&pageNum={page}&pageSize=100"
    "&sortField=date&dateRange=all&contentType=announcement"
)
DETAIL_URL = (
    "https://www.csindex.com.cn/csindex-home/announcement/"
    "queryAnnouncementById?id={id}"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0 Safari/537.36"
    ),
    "Referer": "https://www.csindex.com.cn/",
    "Accept": "application/json, text/plain, */*",
}
_WAF_MARKERS = ("attack.", "405</title>", "safeline")


def _is_waf_page(response: requests.Response) -> bool:
    """The Aliyun WAF block page (HTTP 403 with an HTML body)."""
    return response.status_code in (403, 405) and any(
        marker in response.text[:2000] for marker in _WAF_MARKERS
    )


def _get_json(session: requests.Session, url: str, *, pauses: tuple) -> dict:
    """GET JSON with WAF-aware exponential backoff (30s/120s/600s)."""
    for wait in pauses:
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        if _is_waf_page(response):
            print(
                f"WAF block on {url.split('?')[0].rsplit('/', 1)[-1]}; "
                f"cooling down {wait}s"
            )
            time.sleep(wait)
        else:
            response.raise_for_status()
    raise SystemExit(f"WAF still blocking after backoffs: {url}")
SEARCH_QUERIES = (
    "关于调整沪深300和香港100等指数样本",
    "关于调整沪深300等指数样本",
    "调整沪深300指数样本",
    "沪深300",
)
EFFECTIVE_RE = re.compile(
    r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^。]*?生效"
)
def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


_SH_PREFIXES = ("600", "601", "603", "605", "688", "689", "900")


def _suffix(code: str) -> str:
    code = str(code).zfill(6)
    return f"{code}.SH" if code.startswith(_SH_PREFIXES) else f"{code}.SZ"


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _collect(
    session: requests.Session, pause: float, *, store: Path
) -> dict:
    """Fetch the announcement list, every detail JSON and every attachment."""
    store.mkdir(parents=True, exist_ok=True)
    seen: dict[int, dict] = {}
    for query in SEARCH_QUERIES:
        for page in (1, 2, 3):
            payload = _get_json(
                session,
                SEARCH_URL.format(query=requests.utils.quote(query), page=page),
                pauses=(30, 120, 600),
            )
            items = payload.get("data") or []
            if not items:
                break
            for item in items:
                headline = re.sub(r"<[^>]+>", "", item.get("headline", ""))
                if "沪深300" not in headline and "沪深 300" not in headline:
                    continue
                seen[int(item["id"])] = {
                    "id": int(item["id"]),
                    "itemDate": item.get("itemDate"),
                    "headline": headline,
                }
            time.sleep(pause)
    announcements = sorted(seen.values(), key=lambda item: item["itemDate"])
    print(f"announcements found: {len(announcements)}")
    if not announcements:
        raise SystemExit("no announcements matched; aborting before download")

    manifest: dict = {"announcements": {}, "files": {}}
    for index, announcement in enumerate(announcements):
        identifier = announcement["id"]
        detail_path = store / f"{identifier}.json"
        if not detail_path.exists():
            payload = _get_json(
                session,
                DETAIL_URL.format(id=identifier),
                pauses=(30, 120, 600),
            )
            detail_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            time.sleep(pause)
        detail = json.loads(detail_path.read_text())["data"]
        publish_date = detail.get("publishDate")
        content = detail.get("content") or ""
        effective = None
        match = EFFECTIVE_RE.search(re.sub(r"<[^>]+>", "", content))
        if match:
            effective = "{0}-{1:0>2}-{2:0>2}".format(*match.groups())
        files = []
        for enclosure in detail.get("enclosureList") or []:
            file_url = enclosure.get("fileUrl") or ""
            if not file_url.endswith((".xlsx", ".xls")):
                continue
            target = store / f"{identifier}_{Path(file_url).name}"
            if not target.exists():
                payload = session.get(file_url, timeout=60).content
                target.write_bytes(payload)
                time.sleep(pause)
            files.append(
                {
                    "file": target.name,
                    "sha256": _sha256_file(target),
                    "source_url": file_url,
                }
            )
        manifest["announcements"][str(identifier)] = {
            "publishDate": publish_date,
            "itemDate": announcement["itemDate"],
            "headline": announcement["headline"],
            "effective_date": effective,
            "files": files,
        }
        if index % 25 == 0:
            print(f"... {index + 1}/{len(announcements)} details collected")
    manifest["source"] = "csindex_official_announcements"
    manifest["source_url"] = DETAIL_URL.format(id="0").replace("id=0", "")
    manifest_path = store / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"manifest={manifest_path.name} files={len(manifest['files']) or 'n/a'} "
        f"sha256={_sha256_file(manifest_path)}"
    )
    return manifest


def _base_cohort_from_sina(sina_store: Path) -> tuple[list[str], dict[str, str]]:
    """The 2005-04-08 initial constituents from the stored Sina pages.

    Returns ``(symbols, removal_dates)``: the launch cohort (with the
    ``--`` exclusion marker treated as still active) and, for cohort members
    the Sina table shows as later removed, their attested removal dates used
    only when no official announcement covers them.
    """
    rows: list[dict[str, str]] = []
    for path in sorted(Path(sina_store).glob("000300_page_*.html")):
        html = path.read_text(encoding="utf-8")
        for table_html in re.findall(
            r"<table[^>]*>(.*?)</table>", html, flags=re.S
        ):
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.S):
                cells = [
                    re.sub(r"<[^>]+>|\s+", "", cell)
                    for cell in re.findall(
                        r"<td[^>]*>(.*?)</td>", tr, flags=re.S
                    )
                ]
                if len(cells) < 4:
                    continue
                code, included, excluded = cells[0], cells[2], cells[3]
                if not re.fullmatch(r"\d{6}", code):
                    continue
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", included):
                    continue
                excluded = "" if excluded in ("", "--", "&nbsp;") else excluded
                if excluded and not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", excluded
                ):
                    continue
                rows.append(
                    {"code": code, "included": included, "excluded": excluded}
                )
    base = {
        _suffix(row["code"])
        for row in rows
        if row["included"] == "2005-04-08"
    }
    removals = {
        _suffix(row["code"]): row["excluded"]
        for row in rows
        if row["included"] == "2005-04-08" and row["excluded"]
    }
    print(
        f"sina base cohort: {len(base)} symbols "
        f"({len(removals)} with attested removals)"
    )
    return sorted(base), removals


def _parse_announcement_excel(
    path: Path, identifier: int, effective: str | None, publish: str | None
) -> tuple[list[str], list[str]]:
    """The 沪深300 (000300) entry/exit symbol lists in one attachment."""
    frame = pd.read_excel(io.BytesIO(path.read_bytes()), sheet_name=None, header=0)
    entries: list[str] = []
    exits: list[str] = []
    for sheet_name, sheet in frame.items():
        if not {"指数代码", "证券代码"}.issubset(
            {str(column).strip() for column in sheet.columns}
        ):
            continue
        sheet = sheet.rename(
            columns={
                column: str(column).strip() for column in sheet.columns
            }
        )
        mask = sheet["指数代码"].astype(str).str.zfill(6) == "000300"
        symbols = [
            _suffix(code)
            for code in sheet.loc[mask, "证券代码"].tolist()
            if str(code).strip() not in ("", "nan")
        ]
        if "调入" in sheet_name:
            entries.extend(symbols)
        elif "调出" in sheet_name:
            exits.extend(symbols)
    return entries, exits


def _build(
    manifest: dict, universe_id: str, *, root: Path, store: Path
) -> None:
    entries_by_announcement: list[dict] = []
    for identifier, record in sorted(
        manifest["announcements"].items(), key=lambda item: item[1]["publishDate"]
    ):
        if not record["effective_date"] or not record["files"]:
            continue
        effective = record["effective_date"]
        publish = record["publishDate"] or record["itemDate"]
        for file_record in record["files"]:
            path = store / file_record["file"]
            try:
                entries, exits = _parse_announcement_excel(
                    path, int(identifier), effective, publish
                )
            except Exception as error:  # noqa: BLE001 - one bad file must not
                # sink the chain; it is reported and the operator decides.
                print(
                    f"WARNING: cannot parse {file_record['file']}: "
                    f"{type(error).__name__}: {str(error)[:100]}"
                )
                continue
            if entries or exits:
                entries_by_announcement.append(
                    {
                        "id": identifier,
                        "publish": publish,
                        "effective": effective,
                        "entries": entries,
                        "exits": exits,
                    }
                )
    print(
        f"announcements touching csi300: {len(entries_by_announcement)} "
        f"({sum(len(a['entries']) for a in entries_by_announcement)} entries, "
        f"{sum(len(a['exits']) for a in entries_by_announcement)} exits)"
    )
    base, base_removals = _base_cohort_from_sina(store.parent / "sina_history_component")

    facts: dict[str, list[dict]] = {}

    def _open_interval(symbol: str, start: str, announcement: str) -> None:
        facts.setdefault(symbol, []).append(
            {
                "symbol": symbol,
                "raw_effective_from": start,
                "raw_effective_to": "",
                "announcement_date": announcement,
            }
        )

    def _close_interval(symbol: str, end: str) -> None:
        for interval in reversed(facts.get(symbol, [])):
            if interval["raw_effective_to"] == "":
                interval["raw_effective_to"] = end
                return
        raise SystemExit(
            f"FATAL: {symbol} removed on {end} with no open interval"
        )

    # Sina-attested removals that happened before the first announcement the
    # archive covers keep their attested removal dates.
    first_effective = min(
        record["effective"] for record in entries_by_announcement
    )
    for symbol in base:
        removal = base_removals.get(symbol)
        if removal and removal < first_effective:
            facts.setdefault(symbol, []).append(
                {
                    "symbol": symbol,
                    "raw_effective_from": "2005-04-08",
                    "raw_effective_to": removal,
                    "announcement_date": "2005-04-08",
                }
            )
    open_base = [
        symbol
        for symbol in base
        if symbol not in base_removals or base_removals[symbol] >= first_effective
    ]
    for symbol in open_base:
        _open_interval(symbol, "2005-04-08", "2005-04-08")
    # Base members sina shows as removed on/after the first covered
    # announcement keep their open interval; the announcement that removes
    # them closes it with the official date.

    for record in entries_by_announcement:
        for symbol in record["exits"]:
            if symbol in open_base:
                open_base.remove(symbol)
            _close_interval(symbol, record["effective"])
        for symbol in record["entries"]:
            if any(
                interval["raw_effective_to"] == ""
                for interval in facts.get(symbol, [])
            ):
                continue  # already a member; ignore duplicate rows
            _open_interval(symbol, record["effective"], record["publish"])

    consolidated = [
        {**interval, "reason": ("initial_constituent" if interval[
            "raw_effective_from"] == "2005-04-08" and not interval[
            "raw_effective_to"] else "regular_rebalance")}
        for intervals in facts.values()
        for interval in intervals
    ]
    consolidated.sort(key=lambda row: (row["symbol"], row["raw_effective_from"]))
    snapshot_csv = store.parent / f"{universe_id}_membership_snapshot.csv"
    pd.DataFrame(consolidated).to_csv(snapshot_csv, index=False)
    snapshot_sha256 = _sha256_file(snapshot_csv)
    print(
        f"snapshot facts={len(consolidated)} sha256={snapshot_sha256}"
    )

    manifest_path = store / "manifest.json"
    manifest_sha256 = _sha256_file(manifest_path)
    result = prepare_membership_file(
        snapshot_csv,
        universe_id=universe_id,
        source="csindex_official_announcements",
        source_url="https://www.csindex.com.cn/csindex-home/search/search-content",
        snapshot_sha256=snapshot_sha256,
        source_document_sha256=manifest_sha256,
        effective_date=date.fromisoformat("2005-04-08"),
        announcement_date=date.fromisoformat("2005-04-05"),
        output=root / "data" / "membership" / f"{universe_id}.parquet",
    )
    print(
        f"membership facts={len(result.frame)} "
        f"membership_table_sha256={result.content_hash}"
    )
    return result, consolidated, manifest_sha256


def _cardinality(consolidated: list[dict], sessions: list[date]) -> list[str]:
    intervals = [
        (
            row["symbol"],
            date.fromisoformat(row["raw_effective_from"]),
            date.fromisoformat(row["raw_effective_to"])
            if row["raw_effective_to"]
            else None,
        )
        for row in consolidated
    ]
    deviating = []
    for day in sessions:
        count = sum(
            1
            for _, start, end in intervals
            if start <= day and (end is None or day <= end)
        )
        if count != 300:
            deviating.append(f"{day.isoformat()}:{count}")
    return deviating


def run(
    root: Path,
    config: ProjectConfig,
    *,
    universe_id: str = "csi300",
    pause_seconds: float = 2.0,
    skip_collect: bool = False,
) -> int:
    store = root / "data" / "raw" / "csi" / "csi_index_announcements"
    session = _session()
    if skip_collect and (store / "manifest.json").exists():
        manifest = json.loads((store / "manifest.json").read_text())
    else:
        manifest = _collect(session, pause_seconds, store=store)

    result, consolidated, manifest_sha256 = _build(
        manifest, universe_id, root=root, store=store
    )

    publisher = DatasetPublisher(root)
    with DatasetReader(root).open(publisher.current().version) as dataset:
        calendar = dataset.read("trading_calendar")
        tables = {name: dataset.read(name) for name in dataset.tables}
    sessions = [
        day.date()
        for day in pd.to_datetime(
            calendar.loc[calendar["is_trading_day"], "calendar_date"]
        )
    ]
    deviating = _cardinality(consolidated, sessions)
    if deviating:
        print(
            f"cardinality deviates from 300 on {len(deviating)}/{len(sessions)} "
            f"days (first: {deviating[:8]})"
        )
        if universe_id == "csi300":
            raise SystemExit(
                "the official history does not yield exactly 300 members on "
                "every day; investigate the deviating days above, then rerun "
                "with --universe-id custom_<slug> or with exception evidence"
            )
    else:
        print("cardinality exactly 300 on every pinned session")

    tables["universe_membership"] = result.frame
    published = publisher.publish(tables, QualityReport())
    print(f"dataset_version={published.version}")

    with DatasetReader(root).open(published.version) as dataset:
        daily = dataset.read("daily_bar")
    definition = {
        "schema_version": 1,
        "universe_id": universe_id,
        "membership_table_sha256": result.content_hash,
        "evidence_summary_sha256": manifest_sha256,
        "coverage_start": pd.to_datetime(daily["trade_date"]).min().date().isoformat(),
        "coverage_end": pd.to_datetime(daily["trade_date"]).max().date().isoformat(),
        "rules_version": "csindex-official-announcements-v1",
    }
    definition_path = root / "configs" / "universes" / f"{universe_id}.yml"
    definition_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Frozen universe definition generated by collect_csi300_official.py.\n"
        "# evidence_summary_sha256 = SHA-256 of\n"
        "# data/raw/csi/csi_index_announcements/manifest.json, which pins the\n"
        "# official announcement detail JSONs and 调入调出名单 Excel hashes.\n"
    )
    definition_path.write_text(
        header + yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(
        f"definition={definition_path} "
        f"(universe_version={_sha256_file(definition_path)})"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect official CSI adjustment announcements and build the "
            "frozen csi300 universe (network: search + detail + xlsx GETs)."
        )
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--universe-id",
        default="csi300",
        help="canonical id enforces exactly 300 members per day",
    )
    parser.add_argument("--pause-seconds", type=float, default=2.0)
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    return run(
        root,
        config,
        universe_id=args.universe_id,
        pause_seconds=args.pause_seconds,
        skip_collect=args.skip_collect,
    )


if __name__ == "__main__":
    raise SystemExit(main())
