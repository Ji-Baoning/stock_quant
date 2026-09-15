# Stock Quant

An engineering-validation MVP for reproducible A-share quantitative research.

The first phase uses fixed boundary samples, daily market data, a 60-trading-day
momentum baseline, and three cost scenarios. It is not investment advice, a
profitability claim, or a live-trading system.

## Where to look

- `docs/architecture/` — current architecture facts: positioning, module map,
  data flow, and the invariants that must not be broken.
- `docs/adr/DECISIONS_INDEX.md` — adopted architecture decisions, their
  consequences and what was rejected.
- [RUNBOOK.md](RUNBOOK.md) — the procedure for each operational command.
- `project/SCRIPTS.md` — status and boundaries for project-local helper scripts.
- `docs/operations/asset-retention.md` — retention rules for data, evidence, and local tooling assets.
- `PROJECT_MEMORY.md` — long-lived business context and current status.
- `AGENTS.md` / `CLAUDE.md` — how an agent should load context for a change.

## Setup

```bash
conda env create -f environment.yml
conda activate stock-quant
```

Copy `.env.example` to `.env` and replace its placeholder only when using the
Tushare source. Do not commit `.env` or credentials.

## Configuration

The repository root does not carry a runtime `configs/` tree. The committed
configuration template lives under `templates/project-config/`; to create a
working project, copy it into your project directory and run there, or pass an
explicit `--root`:

```bash
mkdir -p ~/my-project
cp -r templates/project-config ~/my-project/configs
cd ~/my-project          # run with --root . ...
python -m stock_quant data update --root .   # ... or use --root ~/my-project
```

`--root` must point at a directory containing at least
`configs/project.yml`, `configs/sources.yml`, and `configs/costs.yml`; every
command resolves and validates it before constructing any service, and there
is no fallback to the repository root or the template directory. Tushare's
token is read only from the `TUSHARE_TOKEN` environment variable and is never
stored in configuration files.

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
- An **experiment** is identified by a content hash of its frozen spec plus
  three frozen research snapshots (strategy, experiment, data environment;
  identity scheme v2). The same frozen inputs run twice publish the same
  experiment id and byte-identical artifacts. `data/runs/` keeps every run's
  workspace; `data/experiments/` is the formal, immutable registry. Runtime
  metadata (run id, output paths, timestamps, host, pid, worker count) never
  enters the identity.

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

## Walk-forward OOS stability (正式研究)

`execution_pipeline: walk_forward_oos_v1` (the committed `momentum_60d.yml`)
is the formal research workflow: it validates that one **frozen** strategy
performs stably over consecutive, isolated annual out-of-sample folds. It
never selects parameters, ranks frequencies, picks a preferred cost scenario
or drops a poorly performing fold.

**Workflow.** The runner pins the dataset, runs the real-data acceptance gate
and the point-in-time universe preflight *before* the experiment identity is
computed, freezes the three research snapshots, materializes the immutable
`fold_schedule.json` (written and hashed before any fold executes, never
modified afterwards), then executes each fold in isolation: a fresh account
with the identical fixed initial cash per fold, warmup (at least 3 calendar
years and >= 756 confirmed sessions — the window extends back in whole years
when an exchange's per-year session count cannot reach the floor, bounded so
a sparse calendar records a loud deficiency; plus 60 stable-history sessions
before the first OOS day) used only for factor history — never for orders or returns — and a
12-month non-overlapping OOS window on a January-1 anchor. Results go to the
separate, schedule-hash-bound `fold_outcomes.json`.

**Statuses and conclusions.** `research run` prints `research_status=` and
`stability_conclusion=`:

- `FAILED` (exit nonzero): any fold/system integrity failure — preflight,
  acceptance, universe coverage, warmup shortfall, missing benchmark closes,
  a missing portfolio return on a confirmed open day, or a declared cost
  scenario with incomplete artifacts. `stability_conclusion` stays `null`; a
  FAILED run can never be downgraded to INCONCLUSIVE and never publishes.
- `COMPLETED` (exit zero, exact label retained):
  - `INCONCLUSIVE` — the process is valid but the evidence is insufficient:
    fewer than five executed folds, or a legally skipped
    `skipped_not_tradeable` fold (only with versioned market-wide closure
    evidence covering the whole OOS window). This is *valid-but-insufficient*,
    never a statement about strategy quality.
  - `STABLE` — every predeclared cost scenario independently passes:
    positive-fold ratio >= 60% **and** worst fold calendar return > -10%.
    The final verdict is the conjunction over all scenarios.
  - `UNSTABLE` — not FAILED/INCONCLUSIVE and at least one scenario fails its
    thresholds.

**Published artifacts.** A completed formal experiment adds
`fold_schedule.json`, `fold_outcomes.json`, `walk_forward_manifest.json`
(binds both hashes plus the snapshot hashes), `stability_report.json`
(carries the mandatory `stability_policy_hash`, every scenario's inputs,
thresholds, statuses and reasons, per-scenario aggregate OOS returns and
per-fold metrics) and the content-hashed `folds/<fold_id>/` asset set
(`fold_manifest.json`, `signals.parquet`, `orders.parquet`, `fills.parquet`,
`equity.parquet`, `daily_returns.parquet`, `portfolio_construction.parquet`,
`metrics.json`, plus each scenario's
`backtest/<scenario>/rebalance_decisions.parquet`). Failed folds
remain in the schedule and the outcome ledger forever.

**Metric conventions** (spec 指标口径):

- One portfolio return per confirmed open OOS day; the fold's first-day
  return uses the fixed `initial_equity` as its predecessor, later returns
  the prior day's `net_equity_after_cost` (the engine's `total_equity` under
  its audit-facing name).
- `aggregate_return = product(1+r) - 1`;
  `annualized_return = product(1+r)^(252/N) - 1`; volatility and Sharpe are
  sample statistics (`ddof=1`, zero risk-free rate); undefined values are
  JSON `null`, never zero.
- `per_fold_max_drawdown` is computed only from the fold's own daily
  mark-to-market `net_equity_after_cost`. Cross-fold (global) max drawdown
  and Calmar are **forbidden** and appear in no artifact.
- Cost metrics (`gross_return_before_explicit_cost`, `explicit_cost_drag`,
  `slippage_impact`, `explicit_cost_ratio`, `reject_rate` with full/partial
  order counts and `unfilled_quantity_rate`, `turnover-v1`) are computed per
  fold per declared scenario; the same-path cost replay never alters the
  realized fill set, and `zero_cost` stays a separate path-changing
  counterfactual.

The legacy single-window pipeline remains available only under the explicit
`execution_pipeline: engineering_single_window` policy (the debug
`backtest momentum_60d` diagnostic); it cannot publish a formal stability
conclusion.

## Buffered risk-weighted momentum (`buffered_risk_weighted`)

The committed `momentum_60d.yml` selects the pre-registered
`buffered_risk_weighted` portfolio rule: the `momentum_60d` factor and the
weekly rebalance frequency are unchanged; only portfolio construction and
account reconciliation changed from the equal-weight baseline (which stays
available for baseline/engineering specs). The frozen first-run parameters
are `target_count=10`, `entry_rank=10`, `hold_rank=15`,
`risk_lookback_days=60`, `min_risk_observations=40`,
`volatility_floor_annualized=0.10`, `max_single_weight=0.15`,
`rebalance_band_absolute=0.02`, `gross_exposure=1.00`,
`weight_quantum=1e-12`, long-only, no leverage.

**Identity.** `portfolio_rule_version` is the SHA-256 of the rule's canonical
JSON — never a handwritten label. The same canonical content sits in the
strategy snapshot's `parameters_hash`, so any explicit parameter change is a
*new* experiment identity and a registered run can never be silently
re-parameterized. Read it from `folds/<fold_id>/fold_manifest.json`
(`portfolio_rule.portfolio_rule_version`) or from every
`portfolio_construction.parquet` row.

**Construction (one common target, scenario-free).** Per signal date:

1. `raw_momentum_rank` orders factor-valid candidates by descending
   processed momentum then ascending full symbol string (`000001.SZ` <
   `000002.SZ` < `600000.SH`); supplier row order never matters.
2. Symbols whose trusted risk input is invalid are removed and the order is
   renumbered continuously into `risk_eligible_rank` — a risk-invalid top
   name never consumes an entry slot.
3. Previous target members stay retained through `risk_eligible_rank <= 15`;
   the remaining seats fill only from `risk_eligible_rank <= 10`. Membership
   state crosses signal dates only inside one fold and resets at every fold
   boundary; it inherits frozen member codes only — never quantities, fills,
   cash or any scenario state.
4. Weights are capped inverse-volatility over the members: risk score
   `1 / max(applied_vol, 0.10)`, target exposure
   `min(1.00, count * 0.15)`, deterministic water-fill under the 15% cap,
   every weight quantized down to `1e-12` with the residual quanta
   redistributed in ascending symbol order. The unallocatable residue is
   cash, so `sum(target_weight) + cash_weight == 1.00` exactly.

**Trusted risk.** Each candidate's volatility uses the final 60 confirmed
sessions ending at the signal date, never a later one. A real close needs a
finite positive adjusted close with non-ERROR quality and no missing reason;
at least 40 real closes are required (`real_close_observations >= 40`).
A `suspended_verified` carry row contributes a zero path return and never
counts toward the 40; an unknown gap, quality ERROR, non-positive close or a
carry without a prior trusted close invalidates that symbol's risk input with
the stable reason in `risk_invalid_reason` — the candidate is excluded, the
fold does not fail.

**Common targets, scenario accounts.** Every declared cost scenario shares
the identical signal, members and theoretical target weights. Each scenario
converts the common weights into whole-lot quantities from its own
signal-close account equity (`target_quantity =
floor(target_weight * signal_close_equity / signal_price / 100) * 100`), so
quantities, orders, fills and cash may diverge — member selection never
depends on them.

**Reconcile band and suppressions.** A *continuing* position (in both the
previous and current common member sets with a positive target weight) is
rebalanced only when `|current_weight - target_weight| >= 0.02`; exactly 2%
rebalances, strictly below suppresses (`within_rebalance_band`). Entries and
zero-weight exits always reconcile; any difference below one lot emits
`below_one_lot` with no order. Both suppressions are recorded rows in the
scenario's `rebalance_decisions.parquet` (with both weights, the difference,
both quantities, the signal-close equity and the reason) — they are
pre-order portfolio decisions, **not execution rejections**; rejections stay
in `rejections.parquet`. Risk invalidity is a third, separate thing again:
an excluded candidate recorded in the construction audit.

**Artifacts.** Per fold: `folds/<fold_id>/portfolio_construction.parquet`
(one ordered audit row per factor-valid candidate plus every previous
target: both ranks, member status/reason, 60/40 risk counts, raw and applied
volatility, risk score, raw/capped/target weights, cash residue and the rule
version). Per scenario:
`folds/<fold_id>/backtest/<scenario>/rebalance_decisions.parquet` beside that
scenario's submitted orders/fills/rejections. The stability report and the
HTML report carry a `buffered` section: per-fold member changes
(`成员变化换手`), continuing-position rebalances (`连续持仓再平衡换手`),
band suppression (`带宽抑制金额`) and lot suppression (`手数抑制金额`).

**Operator checks.** Verify a fold's 60/40 counts from
`portfolio_construction.parquet` (`window_start/window_end`,
`real_close_observations >= 40`, `suspension_carry_days`); inspect
`member_status` (`retained|entered|exited|not_selected|risk_invalid`) and
`member_reason`; recompute any member's weight from
`applied_annualized_volatility` (score `1 / max(vol, 0.10)`, cap at 0.15,
quantize down to `1e-12`) and confirm `sum(target_weight) + cash_weight ==
1.00`; confirm every scenario's decisions reference only common members.
**Parameters cannot be changed after viewing fold results** — any change is
a new pre-registered identity that must be declared before its own run.

## One-time strategy challenge (一次性样本外挑战)

Out-of-sample evidence is consumed by every formal comparison, so a
challenge is a **pre-registered, one-shot** act. A
`ChallengeDeclaration` (see
`src/stock_quant/research/strategy_challenge/models.py`) is published and
the strategy-family/calendar holdout is **irreversibly consumed before any
challenger result artifact is opened**. The holdout consumption key is
exactly `strategy_family + fold_schedule_hash`: the complete four-field
universe identity (`universe_id`, `universe_version`,
`membership_table_sha256`, `evidence_summary_sha256`) is recorded and
hashed into every record and into the content-derived `challenge_id`, but a
new universe version never re-opens a consumed history as "unseen".

**Declaration.** The operator authors one JSON declaration binding the
baseline experiment id (`top_n_equal_weight`), the challenger strategy
snapshot hash (`buffered_risk_weighted`), the frozen
`StrategyComparisonPolicy` plus its recomputed hash, the fold schedule
hash, the complete universe identity and a UTC `declared_before_run_at`.
Every SHA-256 field must be 64 lowercase hex; the models are frozen with
`extra="forbid"` so no runtime path/pid/host/worker count can enter the
identity. Baseline and challenger must match on dataset/data-environment
snapshot, universe identity, fold schedule, initial equity, factor signal
(recomputed excluding the portfolio-rule hash), rebalance frequency and the
ordered cost scenarios — only the portfolio construction rule may differ.

**Run once.**

```bash
python -m stock_quant research challenge --declaration strategy_challenge.json --root <PROJECT_ROOT>
```

Ordering is auditable: the declaration is atomically published under
`data/strategy_challenges/declarations/<challenge_id>.json`
(`declaration_published`), the holdout is atomically consumed under an
`O_CREAT|O_EXCL` lock (`holdout_consumed`, the record is immutable and
survives crash/FAILED/REJECTED/INCONCLUSIVE), and only then are the
published experiments opened. A terminal `FAILED` exits nonzero;
`PROMOTED`, `REJECTED` and `INCONCLUSIVE_RESEARCH_ONLY` are completed
research outcomes that exit zero with the exact label.

**Outcomes.** `PROMOTED` — at least five unconsumed executed folds, no
legal market-wide skip, challenger walk-forward `STABLE`, and every declared
cost scenario passing every policy threshold (no preferred scenario exists).
`REJECTED` — research complete but at least one threshold failed; every
failed cell stays visible. `INCONCLUSIVE_RESEARCH_ONLY` — valid process,
insufficient evidence (fewer folds, a legal skip, an undefined required
metric, or a challenger walk-forward `INCONCLUSIVE`). `FAILED` — an
identity/registry/pairing/system error with a null conclusion and a
redacted error code.

**Artifacts** (under `data/strategy_challenges/results/<challenge_id>/`):
`strategy_challenge.json`, `holdout_consumption.json`,
`paired_fold_metrics.parquet` (per-pair baseline/challenger values, deltas,
thresholds and pass flags for every policy metric), `strategy_comparison.json`
(both experiment ids, all three snapshot hashes per side, schedule/policy
hashes, the consumption record and the result) and
`strategy_comparison_report.html`. Re-running the identical declaration is
an idempotent recovery; conflicting bytes under one id are never
overwritten. Changing a universe version, a parameter, a cost scenario or
having a failed result **never restores an already consumed historical
holdout**: formal promotion must wait for genuinely unseen history.

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

## Real data acceptance (数据验收)

Formal `research run` is gated by a **real data acceptance** record
(`policy_version=real-data-v1`): a content-addressed, immutable, operator
signed verdict that one pinned dataset version's supply quality was verified.
It is a *data supply* quality gate, not a strategy verdict: a published
experiment's performance credibility stays diagnostic-only (`UNTRUSTED`) in
engineering mode regardless of any acceptance record. Data acceptance and
strategy acceptance (绩效可信度) are separate decisions.

Operator flow (offline except the data itself; details in
`docs/operations/phase-one-validation.md`):

```bash
# 1. Prepare the checklist: fresh automated check verdicts; every manual row
#    starts as an explicit FAIL the operator must turn into PASS.
python -m stock_quant data acceptance prepare --version <VERSION_HASH> \
  --operator <OPERATOR_ID> --output checklist.yml --root <PROJECT_ROOT>

# 2. Edit the checklist by hand: flip each manual row to PASS with evidence.
#    Evidence files live inside the project (project-relative `reference` +
#    `sha256`); `external` references are never fetched -- their sha256 pins
#    the stored UTF-8 summary text.

# 3. Publish: re-runs every automated check and re-hashes all bound evidence.
#    All PASS -> ACCEPTED, exit 0.  Anything else -> the REJECTED record is
#    persisted first (immutable audit), reasons printed, exit 1.
python -m stock_quant data acceptance publish --checklist checklist.yml --root <PROJECT_ROOT>

# 4. Inspect the version's history (oldest first, with reason= lines; a
#    corrupted record prints corrupt acceptance_id=<id> and exits nonzero).
python -m stock_quant data acceptance show --version <VERSION_HASH> --root <PROJECT_ROOT>
```

Semantics formal research relies on:

- A spec's `data_acceptance_id` may be the placeholder `CURRENT_ACCEPTED`
  (resolves to the newest valid ACCEPTED record for the pinned dataset at run
  time, re-verified against the live evidence every run) or an explicit
  64-hex id (pins exactly one record). A frozen spec always carries the
  concrete resolved id, never the placeholder.
- REJECTED records are persisted and immutable but never selectable: with no
  valid ACCEPTED record the research run fails before any factor or backtest
  work and leaves a FAILED preflight manifest.
- Datasets published before the acceptance evidence existed (bootstrap seeds,
  legacy versions) fail the automated checks that need `build_config`
  provenance — run a full `data update` to publish a compatible dataset.
  Old experiments stay readable; engineering-mode runs need no acceptance but
  are recorded `UNVERIFIED` and stay `UNTRUSTED`.
- Every experiment report renders the 真实数据验收 section from
  `metrics.json["data_acceptance"]`; a run without an accepted record shows a
  prominent `UNVERIFIED` alert and the report never infers `ACCEPTED`.

The mechanism is implemented and offline-tested, but no operator has yet run
the acceptance flow on real data.

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
