# Stock Quant

An engineering-validation MVP for reproducible A-share quantitative research.

The first phase uses fixed boundary samples, daily market data, a 60-trading-day
momentum baseline, and three cost scenarios. It is not investment advice, a
profitability claim, or a live-trading system.

## Setup

```bash
conda env create -f environment.yml
conda activate stock-quant
```

Copy `.env.example` to `.env` and replace its placeholder only when using the
Tushare source. Do not commit `.env` or credentials.

## Configuration

Project, source, and cost settings are stored under `configs/`. Tushare's token
is read only from the `TUSHARE_TOKEN` environment variable and is never stored
in configuration files.

## Offline data model

- A **dataset** is immutable and content-addressed. One update fetches a window
  into the raw-store, normalizes and quality-checks it, merges it over the
  carried master/calendar tables, and only publishes when the quality gate
  passes. Every successful update also rebuilds the point-in-time total-return
  series `adjusted_bar` (`adjustment=internal_total_return_v1`) from unadjusted
  closes and verified corporate actions, and republishes the
  `corporate_action_quarantine` audit table. Reads are pinned to a version
  hash; `CURRENT` points at the latest fully gated version. Data and
  experiments live under `data/`.
- An **experiment** is identified by a content hash of its frozen spec. The
  same spec run twice publishes the same experiment id and byte-identical
  artifacts (`metrics.json`, `report.html`, factor/portfolio/backtest frames).
  `data/runs/` keeps every run's workspace; `data/experiments/` is the formal,
  immutable registry.

## Command line

```bash
python -m stock_quant data update --start 2024-01-01 --end 2024-12-31 --root <PROJECT_ROOT>
python -m stock_quant data validate --version <VERSION_HASH> --root <PROJECT_ROOT>

# The only formal publisher: run a frozen spec end-to-end and publish it.
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root <PROJECT_ROOT>

# Scratch backtest that publishes under data/runs/debug (never data/experiments).
python -m stock_quant backtest momentum_60d --root <PROJECT_ROOT>

# Re-render the richer self-contained experiment report from committed artifacts.
python -m stock_quant report build --experiment <EXPERIMENT_ID> --root <PROJECT_ROOT>
```

Every command exits non-zero and prints `FAILED: ...` on failure; a failed or
blocked run never changes the published dataset or the experiments registry. No
command prints a token or a raw supplier response.

## Reproducibility check

Run the same frozen spec twice against any project that holds a published
dataset (one produced by `data update`, or the synthetic project built by
`tests/integration/conftest.py`):

```bash
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root <PROJECT_ROOT>
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root <PROJECT_ROOT>
# The experiment_id printed by both runs is identical (content-addressed identity).
```

## Factor price basis

- `momentum_60d` v2 consumes the immutable `adjusted_bar` table with
  `adjustment=internal_total_return_v1`. The table is derived from unadjusted
  closes and verified cash-dividend/bonus/capitalization events.
- Orders, fills, price-limit checks and account valuation continue to use
  unadjusted `daily_bar` prices.
- Datasets created before `adjusted_bar` remain auditable but cannot run a v2
  Research experiment. Run a full data update to publish a compatible dataset.
- An untrusted corporate-action transition invalidates every momentum window
  that crosses it; the system never substitutes unadjusted close silently.

Every published experiment records this basis in `metrics.json`
(`metrics["factor_input"]`) and renders it in the report's 因子价格口径
section, including the ERROR break count of the pinned dataset.

Status: 复权/公司行为一致性已实现，等待真实数据验收。

## Notebook

`notebooks/01_momentum_baseline.ipynb` is a thin, fully offline walkthrough over
a published dataset: it opens `CURRENT`, replays the momentum spec through the
CLI twice (reproducibility), and inspects the published metrics per cost
scenario. Point `PROJECT_ROOT` at any project that already holds a published
dataset.

## Quality gates

Dataset updates and published experiments carry a shared `QualityReport` with a
neutral gate. Blocking update conditions include non-positive prices, schema
violations, duplicate conflicts, source-fetch failures and unavailable required
sources; optional validation failures and best-effort corporate-action gaps
produce warnings, never blocks.

## Known limitations

Phase-one boundaries (report-only, not defects); the same list appears in every
experiment report's 已知限制 section.

- Cross-source stock close differences above tolerance are recorded as ERROR in
  the quality report but are report-only at this phase: the Tushare primary
  close series is authoritative for factors and backtests, and the publication
  gate does not depend on strategy inputs (design §13.5).
  跨源收盘价差异超过容差时，仅在质量报告中记录为 ERROR，本阶段不阻断发布：因子与
  回测以 Tushare 主源收盘序列为准，发布门禁与策略输入无关（设计 §13.5）。
