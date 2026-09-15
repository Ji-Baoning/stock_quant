"""Explanatory diagnostics for the PIT momentum engineering run.

Status: diagnostic.

Answers "why did the strategy make/lose money" on top of the existing
"how much" report, over one frozen debug/research run directory:

1. yearly / board-group rank IC and quintile long-short layered returns
   (signal close -> 5 trading days later, adjusted closes);
2. portfolio composition: board mix, concentration (HHI / top-3 /
   effective N / cash residual) and style tilts (momentum, 60d
   volatility, 20d traded amount percentiles vs the same-day candidate
   pool), plus yearly beta vs the two benchmarks;
3. liquidity: per-fill participation rate against the same day's traded
   amount (full-cost scenario) and exit-day capacity;
4. delayed-execution pressure: adverse 5-day move of every rejected
   order, notional weighted, grouped by rejection reason;
5. cost attribution: per-year scenario returns with commission/stamp
   from the fill ledgers and slippage measured fill-by-fill against the
   pre-slippage reference price.

Read-only on datasets and run artifacts; writes one JSON + one markdown
summary under ``data/runs/debug/explanatory_diagnostics_<run8>/``.
Not a performance claim: the source run is an UNTRUSTED engineering
diagnostic on a hand-picked 28-name pool.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from stock_quant.project_root import resolve_project_root

_FWD_DAYS = 5  # one weekly rebalance period
_QUANTILES = 5
_BENCHMARKS = ("000300.SH", "000905.SH")
_SCENARIOS = ("zero_cost", "commission_tax", "full_cost")


def _board_of(symbol: str) -> str:
    if symbol.startswith("688"):
        return "star"
    if symbol.startswith(("300", "301", "302")):
        return "chinext"
    if symbol.startswith(("000", "001", "002", "003")):
        return "sz_main"
    if symbol.startswith("60"):
        return "sh_main"
    return "other"


def _pct_rank(series: pd.Series) -> pd.Series:
    return series.rank(pct=True)


def _yearly_groups(dates: pd.Series) -> pd.Series:
    return pd.to_datetime(dates).dt.year


def _safe_mean(values) -> float | None:
    arr = np.asarray([v for v in values if pd.notna(v)], dtype=float)
    if arr.size == 0:
        return None
    return float(arr.mean())


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman correlation without scipy: Pearson over ranks."""
    ra, rb = a.rank(), b.rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra.to_numpy(dtype=float), rb.to_numpy(dtype=float))[0, 1])


def _ic_table(
    factor: pd.DataFrame, fwd: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-signal-date Spearman IC, then year and year x board aggregates."""
    valid = factor[factor.is_valid][["trade_date", "symbol", "processed_value"]].copy()
    valid["trade_date"] = pd.to_datetime(valid.trade_date)
    merged = valid.merge(
        fwd.stack().rename("fwd").reset_index().rename(
            columns={"level_0": "trade_date", "level_1": "symbol"}),
        on=["trade_date", "symbol"], how="inner",
    )
    merged["board"] = merged.symbol.map(_board_of)

    def _ic(group: pd.DataFrame) -> pd.Series:
        if len(group) < 8:
            return pd.Series({"ic": np.nan, "n": len(group)})
        corr = _spearman(group.processed_value, group.fwd)
        return pd.Series({"ic": corr, "n": len(group)})

    daily = merged.groupby("trade_date").apply(_ic, include_groups=False).reset_index()
    daily["year"] = daily.trade_date.dt.year

    rows = []
    for year, grp in daily.groupby("year"):
        ic = grp.ic.dropna()
        rows.append({
            "year": int(year),
            "n_signal_dates": int(len(grp)),
            "ic_mean": float(ic.mean()) if len(ic) else None,
            "ic_std": float(ic.std(ddof=1)) if len(ic) > 1 else None,
            "ic_ir": (
                float(ic.mean() / ic.std(ddof=1))
                if len(ic) > 1 and ic.std(ddof=1) > 0 else None
            ),
            "ic_positive_rate": float((ic > 0).mean()) if len(ic) else None,
        })
    ic_year = pd.DataFrame(rows)

    board_rows = []
    board_daily = (
        merged.groupby(["trade_date", "board"]).apply(
            _ic, include_groups=False).reset_index()
    )
    board_daily["year"] = board_daily.trade_date.dt.year
    for (year, board), grp in board_daily.groupby(["year", "board"]):
        ic = grp.ic.dropna()
        if len(ic) < 6:
            continue
        board_rows.append({
            "year": int(year), "board": board, "n_signal_dates": int(len(ic)),
            "ic_mean": float(ic.mean()),
            "ic_positive_rate": float((ic > 0).mean()),
        })
    return ic_year, pd.DataFrame(board_rows)


def _quintile_table(factor: pd.DataFrame, fwd: pd.DataFrame) -> pd.DataFrame:
    """Equal-weight quintile forward returns per rebalance, aggregated by year."""
    valid = factor[factor.is_valid][["trade_date", "symbol", "processed_value"]].copy()
    valid["trade_date"] = pd.to_datetime(valid.trade_date)
    fwd_long = fwd.stack().rename("fwd").reset_index().rename(
        columns={"level_0": "trade_date", "level_1": "symbol"})
    merged = valid.merge(fwd_long, on=["trade_date", "symbol"], how="inner")

    def _quintiles(group: pd.DataFrame) -> pd.DataFrame | None:
        if len(group) < 10:
            return None
        ranked = group.sort_values("processed_value")
        ranked["bucket"] = pd.qcut(np.arange(len(ranked)), _QUANTILES, labels=False) + 1
        return ranked.groupby("bucket").fwd.mean().to_frame("mean_fwd")

    per_date = merged.groupby("trade_date").apply(_quintiles, include_groups=False)
    if per_date.empty:
        return pd.DataFrame()
    table = per_date.reset_index()
    table["year"] = pd.to_datetime(table.trade_date).dt.year
    pivot = table.pivot_table(
        index=["year", "trade_date"], columns="bucket",
        values="mean_fwd")
    yearly = pivot.groupby("year").mean()
    yearly["long_short_q5_q1_per_period_bp"] = (
        (yearly[float(_QUANTILES)] - yearly[float(1)]) * 1e4
    )
    return yearly.reset_index()


def _concentration_and_tilt(
    positions: pd.DataFrame, factor: pd.DataFrame, adj_close: pd.DataFrame,
    rets: pd.DataFrame, amount20: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Per-rebalance concentration, board mix and style tilts vs the pool."""
    pos = positions.copy()
    pos["trade_date"] = pd.to_datetime(pos.trade_date)
    pos["year"] = pos.trade_date.dt.year
    pos["board"] = pos.symbol.map(_board_of)

    vol60 = rets.rolling(60, min_periods=40).std() * np.sqrt(252.0)

    valid = factor[factor.is_valid][["trade_date", "symbol", "processed_value"]].copy()
    valid["trade_date"] = pd.to_datetime(valid.trade_date)

    conc_rows, board_rows, tilt_rows = [], [], []
    for day, group in pos.groupby("trade_date"):
        weights = group.set_index("symbol").target_weight
        gross = float(weights.sum())
        hhi = float((weights ** 2).sum() / gross ** 2) if gross > 0 else np.nan
        conc_rows.append({
            "trade_date": day, "year": int(day.year), "n_positions": int(len(group)),
            "gross_weight": gross,
            "hhi": hhi,
            "top3_share": (
                float(weights.nlargest(3).sum() / gross)
                if gross > 0 else np.nan
            ),
            "effective_n": float(1.0 / hhi) if hhi and hhi > 0 else np.nan,
        })
        board_share = (
            group.groupby("board").target_weight.sum() / gross
            if gross > 0 else pd.Series(dtype=float)
        )
        for board, share in board_share.items():
            board_rows.append({
                "trade_date": day, "year": int(day.year),
                "board": board, "share": float(share),
            })

        pool = valid[valid.trade_date == day].set_index("symbol")
        if pool.empty:
            continue
        chosen = set(weights.index) & set(pool.index)
        if not chosen:
            continue
        mom_pct = _pct_rank(pool.processed_value)
        day_vol = vol60.loc[day] if day in vol60.index else pd.Series(dtype=float)
        day_amt = amount20.loc[day] if day in amount20.index else pd.Series(dtype=float)
        vol_pct = _pct_rank(day_vol.reindex(pool.index))
        amt_pct = _pct_rank(day_amt.reindex(pool.index))
        tilt_rows.append({
            "trade_date": day, "year": int(day.year),
            "momentum_pct": float(mom_pct[list(chosen)].mean()),
            "vol_pct": _safe_mean(vol_pct[list(chosen)]),
            "amount_pct": _safe_mean(amt_pct[list(chosen)]),
        })

    conc = pd.DataFrame(conc_rows)
    conc_year = conc.groupby("year").agg(
        n_rebalances=("trade_date", "count"),
        mean_n_positions=("n_positions", "mean"),
        mean_hhi=("hhi", "mean"), max_hhi=("hhi", "max"),
        mean_top3_share=("top3_share", "mean"),
        mean_effective_n=("effective_n", "mean"),
        mean_gross_weight=("gross_weight", "mean"),
    ).reset_index()

    board_year = (
        pd.DataFrame(board_rows).groupby(["year", "board"]).share.mean().reset_index()
    )
    tilt_year = pd.DataFrame(tilt_rows).groupby("year").agg(
        momentum_pct=("momentum_pct", "mean"),
        vol_pct=("vol_pct", "mean"),
        amount_pct=("amount_pct", "mean"),
    ).reset_index()
    return conc_year, board_year, tilt_year


def _beta_table(equity: pd.DataFrame, bench_close: pd.DataFrame) -> pd.DataFrame:
    port = equity.set_index(
        pd.to_datetime(equity.trade_date)).total_equity.astype(float)
    port_ret = port.pct_change().dropna()
    rows = []
    for symbol in _BENCHMARKS:
        if symbol not in bench_close.columns:
            continue
        bench = bench_close[symbol].pct_change().dropna()
        joined = pd.concat([port_ret, bench], axis=1, join="inner").dropna()
        joined.columns = ["port", "bench"]
        joined["year"] = joined.index.year
        for year, grp in joined.groupby("year"):
            var = grp.bench.var()
            rows.append({
                "year": int(year), "benchmark": symbol,
                "beta": float(grp.port.cov(grp.bench) / var) if var > 0 else None,
                "corr": float(grp.port.corr(grp.bench)) if grp.port.std() > 0 else None,
            })
    return pd.DataFrame(rows)


def _participation(run_dir: Path, daily: pd.DataFrame) -> dict:
    fills = pd.read_parquet(run_dir / "backtest" / "full_cost" / "fills.parquet")
    fills["trade_date"] = pd.to_datetime(fills.trade_date)
    fills["notional"] = fills.quantity * fills.price
    amt = daily.set_index(["trade_date", "symbol"]).amount
    fills["day_amount"] = [
        amt.get((d, s), np.nan) for d, s in zip(fills.trade_date, fills.symbol)
    ]
    fills = fills[fills.day_amount > 0]
    fills["participation"] = fills.notional / fills.day_amount
    fills["year"] = fills.trade_date.dt.year

    def _stats(group: pd.DataFrame) -> pd.Series:
        p = group.participation
        return pd.Series({
            "n_fills": int(len(group)),
            "p50": float(p.quantile(0.50)), "p90": float(p.quantile(0.90)),
            "p99": float(p.quantile(0.99)), "max": float(p.max()),
            "over_1pct": int((p > 0.01).sum()),
            "over_5pct": int((p > 0.05).sum()),
        })

    yearly = fills.groupby("year").apply(_stats, include_groups=False).reset_index()
    overall = _stats(fills).rename("overall").to_frame().T
    return {
        "yearly": yearly.to_dict(orient="records"),
        "overall": overall.iloc[0].to_dict(),
    }


def _rejection_pressure(
    run_dir: Path, adj_close: pd.DataFrame,
) -> tuple[list[dict], dict]:
    rej = pd.read_parquet(run_dir / "backtest" / "full_cost" / "rejections.parquet")
    rej["trade_date"] = pd.to_datetime(rej.trade_date)
    rows = []
    for rec in rej.itertuples():
        if rec.symbol not in adj_close.columns:
            continue
        series = adj_close[rec.symbol].dropna()
        if rec.trade_date not in series.index:
            continue
        loc = series.index.get_loc(rec.trade_date)
        future = series.iloc[loc + 1: loc + 1 + _FWD_DAYS]
        if future.empty:
            continue
        fwd_ret = float(future.iloc[-1] / series.loc[rec.trade_date] - 1.0)
        adverse = fwd_ret if rec.side == "BUY" else -fwd_ret
        notional = float(rec.rejected_quantity) * float(
            series.loc[rec.trade_date])
        rows.append({
            "trade_date": rec.trade_date.isoformat(), "year": int(rec.trade_date.year),
            "symbol": rec.symbol, "side": rec.side, "reason": rec.reason,
            "rejected_quantity": int(rec.rejected_quantity),
            "reference_notional": notional,
            "fwd_5d_return": fwd_ret,
            "adverse_move": adverse,
            "adverse_cost": adverse * notional,
        })
    frame = pd.DataFrame(rows)
    by_reason = {}
    if not frame.empty:
        for reason, grp in frame.groupby("reason"):
            by_reason[reason] = {
                "n": int(len(grp)),
                "notional": float(grp.reference_notional.sum()),
                "adverse_cost_total": float(grp.adverse_cost.sum()),
            }
    yearly = (
        frame.groupby("year").adverse_cost.sum().rename("adverse_cost_total")
        .reset_index().to_dict(orient="records")
        if not frame.empty else []
    )
    return yearly, by_reason


def _cost_attribution(run_dir: Path) -> tuple[pd.DataFrame, dict]:
    curves, fees = {}, {}
    for sc in _SCENARIOS:
        eq = pd.read_parquet(run_dir / "backtest" / sc / "daily_equity.parquet")
        eq["trade_date"] = pd.to_datetime(eq.trade_date)
        curves[sc] = eq.set_index("trade_date").total_equity.astype(float)
        fl = pd.read_parquet(run_dir / "backtest" / sc / "fills.parquet")
        fl["trade_date"] = pd.to_datetime(fl.trade_date)
        fl["year"] = fl.trade_date.dt.year
        fees[sc] = fl.groupby("year").apply(
            lambda g: pd.Series({
                "commission": float(g.commission.sum()),
                "stamp_tax": float(g.stamp_tax.sum()),
                "traded_notional": float((g.quantity * g.price).sum()),
            }), include_groups=False,
        )

    index = curves[_SCENARIOS[0]].index
    eq = pd.DataFrame({sc: curves[sc].reindex(index) for sc in _SCENARIOS})
    eq["year"] = eq.index.year
    rows = []
    for year, grp in eq.groupby("year"):
        row: dict = {"year": int(year), "n_days": int(len(grp))}
        for sc in _SCENARIOS:
            first, last = grp[sc].iloc[0], grp[sc].iloc[-1]
            row[f"return_{sc}"] = float(last / first - 1.0)
        row["commission"] = float(fees["commission_tax"].commission.get(year, 0.0))
        row["stamp_tax"] = float(fees["commission_tax"].stamp_tax.get(year, 0.0))
        row["traded_notional"] = float(
            fees["commission_tax"].traded_notional.get(year, 0.0))
        # slippage paid per fill is embedded in price vs reference_price
        fl = pd.read_parquet(run_dir / "backtest" / "full_cost" / "fills.parquet")
        fl["trade_date"] = pd.to_datetime(fl.trade_date)
        sl = fl[fl.trade_date.dt.year == year]
        row["slippage_paid"] = float(
            ((sl.price - sl.reference_price) * sl.quantity * np.where(
                sl.side == "BUY", 1.0, -1.0)).sum()
        ) if len(sl) else 0.0
        row["drag_fees"] = row["commission"] + row["stamp_tax"]
        row["drag_slippage"] = row["slippage_paid"]
        row["drag_total_approx"] = row["drag_fees"] + row["drag_slippage"]
        row["equity_delta_zc_fc"] = float(
            grp.zero_cost.iloc[-1] - grp.full_cost.iloc[-1])
        rows.append(row)

    fills = pd.read_parquet(run_dir / "backtest" / "full_cost" / "fills.parquet")
    slip_rate = (fills.price - fills.reference_price).abs() / fills.reference_price
    summary = {
        "slippage_per_fill_mean_bp": float(slip_rate.mean() * 1e4),
        "slippage_per_fill_max_bp": float(slip_rate.max() * 1e4),
        "commission_total": float(fills.commission.sum()),
        "stamp_tax_total": float(fills.stamp_tax.sum()),
    }
    return pd.DataFrame(rows), summary


def run(root: Path, run_id: str) -> Path:
    run_dir = root / "data" / "runs" / run_id
    if not run_dir.is_dir():
        raise SystemExit(f"run not found: {run_dir}")
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    dataset_version = manifest["dataset_version"]
    dataset = root / "data" / "standardized" / dataset_version

    adj = pd.read_parquet(dataset / "adjusted_bar.parquet")
    adj["trade_date"] = pd.to_datetime(adj.trade_date)
    adj_close = adj.pivot(
        index="trade_date", columns="symbol", values="adjusted_close"
    ).sort_index()
    rets = adj_close.pct_change()
    fwd = adj_close.shift(-_FWD_DAYS) / adj_close - 1.0

    daily = pd.read_parquet(dataset / "daily_bar.parquet")
    daily["trade_date"] = pd.to_datetime(daily.trade_date)
    amount = daily.pivot(
        index="trade_date", columns="symbol", values="amount").sort_index()
    amount20 = amount.rolling(20, min_periods=10).mean()
    bench_close = daily[daily.symbol.isin(_BENCHMARKS)].pivot(
        index="trade_date", columns="symbol", values="close").sort_index()

    factor = pd.read_parquet(run_dir / "factor_results.parquet")
    positions = pd.read_parquet(run_dir / "target_positions.parquet")

    ic_year, ic_board = _ic_table(factor, fwd)
    quintiles = _quintile_table(factor, fwd)
    conc_year, board_year, tilt_year = _concentration_and_tilt(
        positions, factor, adj_close, rets, amount20)
    equity = pd.read_parquet(
        run_dir / "backtest" / "full_cost" / "daily_equity.parquet")
    beta_year = _beta_table(equity, bench_close)
    participation = _participation(run_dir, daily)
    rej_yearly, rej_by_reason = _rejection_pressure(run_dir, adj_close)
    cost_year, cost_summary = _cost_attribution(run_dir)

    out_dir = root / "data" / "runs" / "debug" / (
        f"explanatory_diagnostics_{run_id[4:12]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_run": run_id,
        "dataset_version": dataset_version,
        "forward_horizon_trading_days": _FWD_DAYS,
        "quintile_count": _QUANTILES,
        "ic_by_year": ic_year.to_dict(orient="records"),
        "ic_by_year_board": ic_board.to_dict(orient="records"),
        "quintile_returns_by_year": quintiles.to_dict(orient="records"),
        "concentration_by_year": conc_year.to_dict(orient="records"),
        "board_mix_by_year": board_year.to_dict(orient="records"),
        "style_tilt_by_year": tilt_year.to_dict(orient="records"),
        "beta_by_year": beta_year.to_dict(orient="records"),
        "participation": participation,
        "rejection_pressure_by_year": rej_yearly,
        "rejection_pressure_by_reason": rej_by_reason,
        "cost_attribution_by_year": cost_year.to_dict(orient="records"),
        "cost_summary": cost_summary,
        "notes": {
            "industry": (
                "行业分类不在数据集中：以 board"
                "（sh_main/sz_main/chinext/star）为唯一可得分组"
            ),
            "size": "无股本数据，市值不可算：以 20 日成交额分位作为流动性/规模代理",
            "ic_horizon": "信号日收盘 -> 5 个交易日后的复权收盘（与周频调仓周期一致）",
            "trust": "源运行是 UNTRUSTED 工程诊断（28 只人工池，幸存者偏差未消除）",
        },
    }
    (out_dir / "explanatory_diagnostics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    _write_summary(out_dir, payload)
    return out_dir


def _write_summary(out_dir: Path, payload: dict) -> None:
    lines = ["# 解释性诊断摘要", ""]
    lines.append(f"源运行：`{payload['source_run']}`（UNTRUSTED 工程诊断）")
    lines.append(f"数据集：`{payload['dataset_version'][:16]}…`；IC 视界 = 5 个交易日")
    lines.append("")

    lines.append("## 逐年 rank IC")
    lines.append("| 年 | 信号日数 | IC 均值 | IC IR | 正 IC 比率 |")
    lines.append("|---|---|---|---|---|")
    for r in payload["ic_by_year"]:
        lines.append(
            f"| {r['year']} | {r['n_signal_dates']} | {r['ic_mean']:.4f} "
            f"| {r['ic_ir']:.2f} | {r['ic_positive_rate']:.0%} |")
    lines.append("")

    lines.append("## 分五层多空（等权，每期均值，bp）")
    lines.append("| 年 | Q1(低动量) | Q3 | Q5(高动量) | Q5-Q1 |")
    lines.append("|---|---|---|---|---|")
    for r in payload["quintile_returns_by_year"]:
        lines.append(
            f"| {int(r['year'])} | {r.get(1.0, float('nan')) * 1e4:.0f} "
            f"| {r.get(3.0, float('nan')) * 1e4:.0f} "
            f"| {r.get(5.0, float('nan')) * 1e4:.0f} "
            f"| {r['long_short_q5_q1_per_period_bp']:.0f} |")
    lines.append("")

    lines.append("## 成本归因（full_cost 口径）")
    lines.append("| 年 | zero | comm_tax | full | 佣金 | 印花税 | 滑点 | 权益差 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in payload["cost_attribution_by_year"]:
        lines.append(
            f"| {int(r['year'])} | {r['return_zero_cost']:.1%} "
            f"| {r['return_commission_tax']:.1%} | {r['return_full_cost']:.1%} "
            f"| {r['commission'] / 1e4:.1f}万 | {r['stamp_tax'] / 1e4:.1f}万 "
            f"| {r['slippage_paid'] / 1e4:.1f}万"
            f" | {r['equity_delta_zc_fc'] / 1e4:.1f}万 |")
    lines.append("")

    lines.append("## 参与率与拒单")
    ov = payload["participation"]["overall"]
    lines.append(
        f"成交参与率（成交额/当日成交额）：p50={ov['p50']:.4%} "
        f"p90={ov['p90']:.4%} p99={ov['p99']:.4%} max={ov['max']:.2%}；"
        f">5% 共 {ov['over_5pct']} 笔")
    for reason, st in payload["rejection_pressure_by_reason"].items():
        lines.append(
            f"拒单 {reason}：{st['n']} 笔，参考名义 {st['notional'] / 1e4:.1f}万，"
            f"不利影响合计 {st['adverse_cost_total'] / 1e4:.1f}万")
    lines.append("")

    lines.append("## 集中度与风格")
    lines.append("| 年 | 持仓数 | HHI | Top3 | 有效N | 动量分位 | 波动分位 | 成交额分位 |")  # noqa: E501
    lines.append("|---|---|---|---|---|---|---|---|")
    conc = {r["year"]: r for r in payload["concentration_by_year"]}
    tilt = {r["year"]: r for r in payload["style_tilt_by_year"]}
    for year in sorted(conc):
        c, t = conc[year], tilt.get(year, {})
        lines.append(
            f"| {year} | {c['mean_n_positions']:.1f} | {c['mean_hhi']:.3f} "
            f"| {c['mean_top3_share']:.1%} | {c['mean_effective_n']:.1f} "
            f"| {t.get('momentum_pct', float('nan')):.2f} "
            f"| {t.get('vol_pct', float('nan')):.2f} "
            f"| {t.get('amount_pct', float('nan')):.2f} |")
    lines.append("")
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="run id under data/runs/")
    parser.add_argument("--root", default=".", help="project root")
    args = parser.parse_args(argv)
    root = resolve_project_root(Path(args.root))
    out = run(root, args.run)
    print(f"diagnostics written under {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
