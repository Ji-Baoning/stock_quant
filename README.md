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
  passes. Reads are pinned to a version hash; `CURRENT` points at the latest
  fully gated version. Data and experiments live under `data/`.
- An **experiment** is identified by a content hash of its frozen spec. The
  same spec run twice publishes the same experiment id and byte-identical
  artifacts (`metrics.json`, `report.html`, factor/portfolio/backtest frames).
  `data/runs/` keeps every run's workspace; `data/experiments/` is the formal,
  immutable registry.

## Command line

```bash
python -m stock_quant data update --start 2024-01-01 --end 2024-12-31 --root <PROJECT_ROOT>
python -m stock_quant data validate --version <VERSION_HASH> --root <PROJECT_ROOT>

# Offline first step of the membership workflow: bind one already-stored
# official snapshot to its evidence hashes and emit the canonical facts frame.
python -m stock_quant data index-membership prepare \
    --universe-id csi300 \
    --input data/raw/csi/members_2005.csv \
    --snapshot-sha256 <64-HEX> --source-document-sha256 <64-HEX> \
    --source csi_index_announcement \
    --source-url https://www.csindex.com.cn/announcement.pdf \
    --effective-date 2005-01-04 --announcement-date 2005-01-04 \
    --output data/membership/universe_membership.parquet

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

## Point-in-time index universe (csi300)

Formal research no longer ranks all `security_master` symbols: the momentum
factor first filters its candidates to the frozen `csi300` membership of each
signal day, as resolved from immutable, evidence-backed membership facts. The
operator chain that produces and freezes those facts is:

1. **Source documents** — obtain the official (or officially corroborated)
   index-constitution announcements and store them together with the raw
   membership snapshot under `data/raw/...`; record each file's SHA-256. The
   `source_url` must be a credential-free, auditable locator. Never commit
   data payloads or credentials.
2. **Import (`data index-membership prepare`, or the equivalent
   `project/refresh_index_membership.py` script)** — offline; both surfaces
   share one implementation and require explicit `--snapshot-sha256`,
   `--source-document-sha256`, source, dates and reason arguments. A missing
   hash is a usage error, not a warning. The output prints
   `membership_table_sha256=`, the content hash of the prepared facts.
3. **Dataset publication** — publish the prepared frame as the immutable
   `universe_membership` table of the next dataset version; every later
   `data update` carries it byte-for-byte and `data validate` re-audits it
   (tampered evidence is FATAL).
4. **Definition hash** — fill `configs/universes/csi300.yml` with the REAL
   values: the published table's content hash, the evidence-summary hash and
   the dataset's actual coverage window. The committed file is a placeholder
   template that formal runs always reject; only the operator, holding the
   real evidence, can turn it into a usable definition.
5. **Acceptance and research** — `research run` preflights the definition
   against the pinned dataset (`index_membership_evidence` gate) before any
   factor computation, then freezes `universe_version` (the definition's
   content hash) into the experiment identity and persists every signal day's
   member snapshot hash in the artifacts.

The gates have **no bypass flag**. Missing proof, a wrong member count
(`csi300` is 300 on every trading day unless an official exception record
exists) and ambiguous delisting boundaries **stop work** (`universe_acceptance`
failure with a redacted preflight manifest under `data/runs/`; no factors, no
fallback to master symbols) and require a correction published as new,
evidence-backed facts. Removal from the index never force-sells an existing
holding; it only stops new signals.

## Reproducibility check

Run the same frozen spec twice against any project that holds a published
dataset (one produced by `data update`, or the synthetic project built by
`tests/integration/conftest.py`):

```bash
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root <PROJECT_ROOT>
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root <PROJECT_ROOT>
# The experiment_id printed by both runs is identical (content-addressed identity).
```

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
- No adjusted (复权) daily series is published or consumed at the factor layer
  in phase one; momentum runs on the unadjusted series (adjusted_close=close).
  BaoStock adjusted data is fetched only for optional continuity/cross-checks,
  never for factors.
  本阶段不发布、也不消费复权日线：动量基于未复权序列计算（adjusted_close=close）；
  BaoStock 复权数据仅用于可选的延续性与交叉核对，不参与因子。
