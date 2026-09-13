"""Build auditable per-scenario order-level execution-divergence diagnostics.

Goal #3 schema (spec section 8): for each cost scenario of the latest
experiment, writes ``execution_diagnostics.json`` — a flat per-scenario object
with order-level counts (read from the reconciled ``order_diffs.parquet``) plus
notional priced at the signal-day close.  ``planned_*`` / ``unfilled_notional``
are anchored to the signal-day close of each order's symbol (spec section 8);
``actual_gross_notional`` uses the real fill prices.  The rich report reads
this JSON and degrades to "无" until the writer runs against a Goal-3 run.

Under account-aware rebalancing there is no top-level ``orders.parquet``: the
price tape is built from the union of the per-scenario ``order_diffs``
symbols.  A run without per-scenario ``order_diffs.parquet`` (pre-dating the
fixed-signal-day design) is skipped with a note: re-run the research
experiment to emit order-level diagnostics.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from stock_quant.config import ProjectConfig, load_project_config
from stock_quant.project_root import resolve_project_root

#: Spec-section-8 key order for the per-scenario flat object.
SUMMARY_KEYS = (
    "scenario",
    "planned_gross_notional",
    "actual_gross_notional",
    "unfilled_notional",
    "execution_deviation_ratio",
    "planned_order_count",
    "filled_order_count",
    "partial_order_count",
    "rejected_order_count",
    "unfilled_reason_counts",
    "end_cash",
    "cash_ratio",
    "stale_asset_ratio",
)

_ORDER_DIFF_COLUMNS = (
    "signal_date",
    "execution_date",
    "order_id",
    "side",
    "symbol",
    "planned_quantity",
    "filled_quantity",
    "rejected_quantity",
    "reason",
    "status",
)
_STATUS_FILLED = "FILLED"


def _latest_experiment_dir(experiments_dir: Path) -> Path:
    experiments = [p for p in experiments_dir.iterdir() if p.is_dir()]
    if not experiments:
        sys.exit(f"no experiments under {experiments_dir}")
    return max(experiments, key=lambda p: p.name)


def _price_tape(
    root: Path, dataset_version: str, symbols: set[str]
) -> pd.DataFrame:
    """Per-(date, symbol) signal-day close, restricted to the order symbols.

    Returns a frame with datetime64 ``date``, ``symbol`` and float ``close``,
    sorted by ``date`` so it can back a backward ``merge_asof``.
    """
    path = root / "data" / "standardized" / dataset_version / "daily_bar.parquet"
    if not path.is_file():
        sys.exit(f"dataset daily_bar missing for {dataset_version}: {path}")
    daily = pd.read_parquet(path)
    daily = daily[daily["symbol"].isin(symbols)]
    daily = daily[daily["close"].notna() & (daily["close"] > 0)]
    tape = daily[["trade_date", "symbol", "close"]].copy()
    tape["date"] = pd.to_datetime(tape["trade_date"])
    tape = tape.sort_values("date")
    return tape[["date", "symbol", "close"]]


def scenario_summary(
    *,
    name: str,
    diff: pd.DataFrame,
    fills: pd.DataFrame,
    equity: pd.DataFrame,
    tape: pd.DataFrame,
) -> dict[str, object]:
    """One scenario's spec-section-8 object from its reconciled ledgers.

    ``diff`` is the scenario ``order_diffs.parquet`` (one row per planned
    order, status in FILLED/REJECTED/PARTIAL, reason non-empty unless FILLED);
    ``fills`` the scenario fills; ``equity`` the daily equity; ``tape`` the
    symbol-filtered signal-day close tape (see ``_price_tape``).  Prices every
    planned/unfilled order at its symbol's close on the signal date (backward
    asof fallback to the most recent prior close), per spec section 8.
    """
    priced = diff.copy()
    priced["date"] = pd.to_datetime(priced["signal_date"])
    priced = priced.sort_values("date")
    priced = pd.merge_asof(
        priced, tape, on="date", by="symbol", direction="backward"
    )
    missing = priced["close"].isna()
    if missing.any():
        symbols = sorted(priced.loc[missing, "symbol"].unique())
        raise ValueError(
            "no signal-day close for orders of: " + ", ".join(symbols)
        )

    planned_gross = float((priced["planned_quantity"] * priced["close"]).sum())
    unfilled_notional = float(
        (
            (priced["planned_quantity"] - priced["filled_quantity"])
            * priced["close"]
        ).sum()
    )
    if len(fills):
        actual_gross = float(
            (
                pd.to_numeric(fills["quantity"])
                * pd.to_numeric(fills["price"])
            ).sum()
        )
    else:
        actual_gross = 0.0

    statuses = diff["status"].value_counts()
    planned_order_count = int(len(diff))
    filled_order_count = int(statuses.get(_STATUS_FILLED, 0))
    partial_order_count = int(statuses.get("PARTIAL", 0))
    rejected_order_count = int(statuses.get("REJECTED", 0))
    unfilled = diff[diff["status"] != _STATUS_FILLED]
    reason_counts = {
        str(reason): int(count)
        for reason, count in unfilled["reason"].value_counts().items()
        if str(reason)
    }

    last = equity.sort_values("trade_date").iloc[-1]
    total_equity = float(last["total_equity"])
    end_cash = round(float(last["cash"]), 2)
    cash_ratio = (
        round(float(last["cash"]) / total_equity, 6) if total_equity > 0 else 0.0
    )
    stale_asset_ratio = (
        round(float(last["stale_market_value"]) / total_equity, 6)
        if total_equity > 0
        else 0.0
    )
    deviation_ratio = (
        round(unfilled_notional / planned_gross, 6) if planned_gross > 0 else 0.0
    )

    summary: dict[str, object] = {
        "scenario": name,
        "planned_gross_notional": round(planned_gross, 2),
        "actual_gross_notional": round(actual_gross, 2),
        "unfilled_notional": round(unfilled_notional, 2),
        "execution_deviation_ratio": deviation_ratio,
        "planned_order_count": planned_order_count,
        "filled_order_count": filled_order_count,
        "partial_order_count": partial_order_count,
        "rejected_order_count": rejected_order_count,
        "unfilled_reason_counts": reason_counts,
        "end_cash": end_cash,
        "cash_ratio": cash_ratio,
        "stale_asset_ratio": stale_asset_ratio,
    }
    assert list(summary) == list(SUMMARY_KEYS)
    return summary


def run(root: Path, config: ProjectConfig) -> None:
    experiment_dir = _latest_experiment_dir(root / "data" / "experiments")
    metrics = json.loads(
        (experiment_dir / "metrics.json").read_text(encoding="utf-8")
    )
    meta = metrics["meta"]
    run_id = str(meta["run_id"])
    run = root / "data" / "runs" / run_id
    scenarios = tuple(
        str(item) for item in (meta.get("spec") or {}).get("cost_scenarios", ())
    )
    # No top-level plan ledger exists under account-aware rebalancing: build
    # the price tape from the union of the per-scenario order_diffs symbols.
    diffs_by_scenario: dict[str, pd.DataFrame] = {}
    symbols: set[str] = set()
    for name in scenarios:
        diff_path = run / "backtest" / name / "order_diffs.parquet"
        if not diff_path.is_file():
            sys.exit(
                f"{diff_path} missing for scenario {name!r}; re-run the "
                "research experiment to write order-level diagnostics"
            )
        diff = pd.read_parquet(diff_path)
        if "signal_date" not in diff.columns or "symbol" not in diff.columns:
            sys.exit(
                f"run {run_id} predates the reconciled order_diffs contract "
                f"({diff_path} lacks signal_date/symbol); re-run the research "
                "experiment to write order-level diagnostics"
            )
        diffs_by_scenario[name] = diff
        symbols |= set(diff["symbol"])
    tape = _price_tape(root, str(meta["dataset_version"]), symbols)

    for name in scenarios:
        folder = run / "backtest" / name
        summary = scenario_summary(
            name=name,
            diff=diffs_by_scenario[name],
            fills=pd.read_parquet(folder / "fills.parquet"),
            equity=pd.read_parquet(folder / "daily_equity.parquet"),
            tape=tape,
        )
        (folder / "execution_diagnostics.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        # One-row parquet of the same flat object; the reason map is stored as
        # a JSON string so the object survives a parquet round-trip.
        row = {
            key: (
                json.dumps(value, ensure_ascii=False)
                if key == "unfilled_reason_counts"
                else value
            )
            for key, value in summary.items()
        }
        pd.DataFrame([row]).to_parquet(
            folder / "execution_diagnostics.parquet", index=False
        )
        lines = [f"# {name} 执行偏离诊断", ""]
        for key, value in summary.items():
            lines.append(f"- {key}: {value}")
        (folder / "execution_diagnostics.md").write_text("\n".join(lines) + "\n")
        print(json.dumps(summary, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = resolve_project_root(args.root)
    config = load_project_config(root)
    run(root, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
