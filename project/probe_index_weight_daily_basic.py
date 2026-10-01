#!/usr/bin/env python
"""Probe the tushare index_weight / daily_basic endpoints (spec §6.4).

``render`` is offline and prints every request shape the network subcommands
would issue.  ``index-weight``/``daily-basic`` hit real transports and consume
quota: NEVER run them without the owner's explicit authorization.  Conclusions
are dated evidence, not architecture facts.  Credentials are read only by
``build_transport`` from the environment; never read, printed or written here.
Exit codes: 0 completed reading, 1 call failure, 2 no transport, 3 unexpected.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from stock_quant.config import SourceConfig
from stock_quant.data_sources.base import (
    AuthenticationError,
    ContractError,
    ServerError,
)
from stock_quant.data_sources.tushare_transport import PROXY, RELAY, build_transport

RECORD_BASENAME = "2026-10-01-endpoint-probe-evidence"
DEFAULT_EVIDENCE = Path("docs/operations") / f"{RECORD_BASENAME}.evidence.json"
#: panda's codes are probe STARTING POINTS only, never evidence (spec §6.2).
DEFAULT_INDEX_CODES = ("399300.SZ", "000905.SH", "000852.SH")
DEFAULT_REFERENCE_SYMBOL = "000001.SZ"
FIELDS = "ts_code,trade_date,total_mv,turnover_rate"
#: Bounded plan: 3 codes x (4 cadence months + 4 history probes) + 1 empty
#: shape for index_weight; by-day + by-range + 4 history for daily_basic; x2.
MAX_REQUESTS = 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _years(spec: str) -> list[str]:
    return [part.strip() for part in spec.split(",")]


def _transports():
    for kind in (RELAY, PROXY):
        try:
            yield kind, build_transport(kind, SourceConfig()).client
        except AuthenticationError as error:
            print(f"transport {kind}: unavailable ({error})")


def _read(client, budget: list, endpoint: str, label: str, **params):
    budget[0] -= 1
    if budget[0] < 0:
        raise SystemExit(f"request budget exhausted before {label}")
    method = getattr(client, endpoint, None)
    return method(**params) if method else client.query(endpoint, **params)


def _shape(label: str, frame: pd.DataFrame, reference: str) -> dict[str, object]:
    """One reading: rows, columns, nulls, cadence days, weight/value shape."""
    entry: dict[str, object] = {
        "label": label, "rows": int(len(frame)),
        "columns": [str(c) for c in frame.columns],
        "null_counts": {str(c): int(frame[c].isna().sum())
                        for c in frame.columns}}
    if len(frame) and "trade_date" in frame.columns:
        entry["snapshot_days"] = len({str(d) for d in frame["trade_date"]})
    if len(frame) and {"con_code", "weight"} <= set(frame.columns):
        weights = pd.to_numeric(frame["weight"], errors="coerce")
        entry["shape"] = {"con_codes": int(frame["con_code"].nunique()),
                          "weight_dtype": str(frame["weight"].dtype),
                          "weight_min": float(weights.min()),
                          "weight_max": float(weights.max())}
    if len(frame) and {"ts_code", "total_mv", "turnover_rate"} <= set(
            frame.columns):
        hit = frame[frame["ts_code"] == reference]
        if len(hit):
            entry["magnitude_reference"] = {
                "symbol": reference,
                "trade_date": str(hit.iloc[0]["trade_date"]),
                "raw_total_mv": str(hit.iloc[0]["total_mv"]),
                "raw_turnover_rate": str(hit.iloc[0]["turnover_rate"])}
    return entry


def _index_weight_calls(codes, cadence_start, cadence_months, years):
    months = [str(p).replace("-", "") for p in pd.period_range(
        cadence_start, periods=cadence_months, freq="M")]
    calls = []
    for code in codes:  # per code, not just the first
        calls += [(f"{code} cadence {m}", "index_weight",
                   {"index_code": code, "start_date": f"{m}01",
                    "end_date": f"{m}31"}) for m in months]
        calls += [(f"{code} history {y}01", "index_weight",
                   {"index_code": code, "start_date": f"{y}0101",
                    "end_date": f"{y}0131"}) for y in years]
    calls.append(("empty-shape", "index_weight",  # pre-coverage window
                  {"index_code": codes[0], "start_date": "19900101",
                   "end_date": "19901231"}))
    return calls


def _daily_basic_calls(trade_date: str, reference: str, years):
    start = (datetime.strptime(trade_date, "%Y%m%d")
             - timedelta(days=9)).strftime("%Y%m%d")
    return ([("by-day", "daily_basic",
              {"trade_date": trade_date, "fields": FIELDS}),
             ("by-range", "daily_basic",
              {"ts_code": reference, "start_date": start,
               "end_date": trade_date})]
            + [(f"history {y}01", "daily_basic",
                {"ts_code": reference, "start_date": f"{y}0101",
                 "end_date": f"{y}0131"}) for y in years])


def render(args: argparse.Namespace) -> None:
    shapes = {"index_weight": [
        {"transport": kind, "endpoint": "index_weight",
         "params": {"index_code": code, "start_date": "YYYYMM01",
                    "end_date": "YYYYMM31"}}
        for kind in (RELAY, PROXY) for code in args.index_codes]}
    shapes["daily_basic"] = [
        {"transport": kind, "endpoint": "daily_basic", "params": params}
        for kind in (RELAY, PROXY)
        for params in ({"trade_date": "YYYYMMDD", "fields": FIELDS},
                       {"ts_code": args.reference_symbol,
                        "start_date": "YYYYMMDD", "end_date": "YYYYMMDD"})]
    print(json.dumps(shapes, indent=1, sort_keys=True))


def _first_client():
    for _kind, client in _transports():
        return client
    raise SystemExit("no transport configured; cannot probe")


def _run_probe(section, calls, evidence: Path | None, reference: str) -> None:
    budget = [MAX_REQUESTS]
    findings: dict[str, object] = {}
    for kind, client in _transports():
        readings = []
        for label, endpoint, params in calls:
            try:
                frame = _read(client, budget, endpoint,
                              f"{kind} {label}", **params)
            except (ServerError, ContractError) as error:
                readings.append({"label": label,
                                 "error": type(error).__name__})
                continue
            readings.append(_shape(label, frame, reference))
        findings[kind] = {"observed_at_utc": _utc_now(), "readings": readings}
    print(json.dumps(findings, ensure_ascii=False, indent=1, sort_keys=True))
    if not any(entry["readings"] for entry in findings.values()):
        raise SystemExit("no transport produced a reading; refusing to write "
                         "evidence or flip _status to 'measured'")
    if evidence is not None:
        _write_evidence(evidence, section, findings)


def _daily_bar_diff(root: Path, trade_date: str, ts_codes: set[str]) -> object:
    from stock_quant.data_model.dataset import DatasetPublisher, DatasetReader

    version = DatasetPublisher(root).current().version
    with DatasetReader(root).open(version) as dataset:
        if "daily_bar" not in dataset.tables:
            return {"error": "no daily_bar table"}
        bar = dataset.read("daily_bar")
    day = bar[pd.to_datetime(bar["trade_date"]).dt.strftime("%Y%m%d") == trade_date]
    symbols = {str(s) for s in day["symbol"]}
    return {"daily_bar_symbols": len(symbols),
            "daily_basic_symbols": len(ts_codes),
            "only_in_daily_bar": sorted(symbols - ts_codes)[:20],
            "only_in_daily_basic": sorted(ts_codes - symbols)[:20]}


def _write_evidence(path: Path, section: str, payload: dict[str, object]) -> None:
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SystemExit(f"evidence file {path} is not valid JSON ({error})")
        if not isinstance(document.get("probes"), dict):
            raise SystemExit(f"evidence file {path} is not probe-evidence shaped")
    else:
        document = {"record": f"docs/operations/{RECORD_BASENAME}.md",
                    "probes": {}}
    document["probes"][section] = payload
    if any(entry.get("readings")
           for entry in document["probes"].values()
           if isinstance(entry, dict)):
        document["_status"] = "measured"
    document["last_updated_utc"] = _utc_now()
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"evidence: section {section!r} written to {path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_index_weight_daily_basic.py",
        description=("Probe the tushare index_weight / daily_basic endpoints "
                     "(spec §6.4). The index-weight and daily-basic subcommands "
                     "hit real transports and consume quota -- never run them "
                     "without the owner's explicit authorization."))
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    sub = parser.add_subparsers(dest="probe", required=True)
    for name in ("render", "index-weight", "daily-basic"):
        probe = sub.add_parser(name)
        probe.add_argument("--index-codes", default=",".join(DEFAULT_INDEX_CODES))
        probe.add_argument("--reference-symbol", default=DEFAULT_REFERENCE_SYMBOL)
        probe.add_argument("--cadence-start", default="202604")
        probe.add_argument("--cadence-months", type=int, default=4)
        probe.add_argument("--history-years", default="2005,2010,2015,2020")
    sub.choices["daily-basic"].add_argument(
        "--trade-date", type=date.fromisoformat, required=True)
    sub.choices["daily-basic"].add_argument(
        "--root", type=Path, default=None, help="project root, for the daily_bar diff")
    args = parser.parse_args(argv)
    args.index_codes = tuple(c.strip() for c in args.index_codes.split(","))
    years = _years(args.history_years)
    try:
        if args.probe == "render":
            render(args)
        elif args.probe == "index-weight":
            _run_probe("index_weight", _index_weight_calls(
                args.index_codes, args.cadence_start,
                args.cadence_months, years), args.evidence,
                args.reference_symbol)
        else:
            trade_date = args.trade_date.strftime("%Y%m%d")
            _run_probe("daily_basic", _daily_basic_calls(
                trade_date, args.reference_symbol, years), args.evidence,
                args.reference_symbol)
            if args.root is not None:  # symbol-set diff vs the published bar
                by_code = _read(_first_client(), [MAX_REQUESTS],
                                "daily_basic", "diff by-day",
                                trade_date=trade_date, fields="ts_code")
                _write_evidence(args.evidence, "daily_basic_bar_diff", {
                    "trade_date": trade_date,
                    "diff": _daily_bar_diff(
                        args.root, trade_date,
                        {str(c) for c in by_code["ts_code"]})})
    except AuthenticationError as error:
        print(f"no usable transport: {error}")
        return 2
    except (ServerError, ContractError) as error:
        print(f"supplier call failed: {type(error).__name__}: {error}")
        return 1
    except Exception as error:  # noqa: BLE001 - report, never traceback-leak
        print(f"unexpected failure ({type(error).__name__}): {error}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
