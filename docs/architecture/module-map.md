# Module map

## When to read this

Read this when you need to know which package owns a behaviour, or which
neighbouring packages a change may reach. Then read the path rule for the
package you are editing — it names the narrower invariants and verification
commands for that area.

## Package responsibilities

| Package | Owns | Does not own |
| --- | --- | --- |
| `stock_quant.data_sources` | Supplier adapters (`tushare`, `tushare_relay`, `tushare_proxy`, `akshare`, `baostock`), the raw store, and transport selection (`tushare_transport`). Returning raw supplier frames and provenance unchanged. | Normalization, cleaning, quality verdicts. A provider-specific failure is never repaired here. |
| `stock_quant.data_model` | Canonical tables and their schemas: `schemas`, `normalize`, `clean`, `calendar`, `security_master`, `universe`, `universe_membership`, `index_membership_import`, `corporate_actions`, `adjusted_bar`, `suspensions`, `trading_rules`, `dataset`. | Supplier I/O; quality verdicts. |
| `stock_quant.data_quality` | `raw_checks` (raw-source checks), `gates` (the neutral publication gate), `compare` (cross-source comparison), `models` (`QualityReport`, issues). | Deciding to publish; mutating data. |
| `stock_quant.data_pipeline` | Orchestrating one data operation: fetch → raw checks → normalize → quality checks → merge over carried master/calendar → gate → publish. | Business rules inside the adapters or schemas. |
| `stock_quant.factors` | The `Factor` protocol, `FactorResult`/models, and concrete factors such as `momentum_60d`. | Portfolio construction, execution. |
| `stock_quant.research` | Freezing and running formal experiments: `spec`, `trust`, `universe`, `registry`, `runner`, `reconcile`, plus the `acceptance`, `walk_forward` and `strategy_challenge` subpackages. | The numerical backtest itself, and portfolio rules. |
| `stock_quant.backtest` | Chronological replay: `engine`, `execution`, `account`, `costs`, `rebalancer`, `weight_rebalancer`, `valuation`, `corporate_actions`. | Choosing what to hold. |
| `stock_quant.portfolio` | Turning eligible candidates into frozen target weights: `equal_weight`, `buffered_risk_weight`, `risk_estimation`, `rebalance_band`, `buffered_models`. | Fills, costs, or account state. |
| `stock_quant.analytics` | Performance metrics computed from published artefacts. | Data access, rendering. |
| `stock_quant.reporting` | Rendering self-contained static HTML from committed artefacts. | Fetching data, computing strategy decisions. |
| module-level | `config` (project config loading), `project_root` (root resolution), `bootstrap` (service wiring), `cli` (Typer surface), `safe_yaml`, `logging`. | Domain logic. |

`research` subpackages:

- `acceptance` — the real-data acceptance registry, checks, evidence binding
  and the operator worksheet. See `docs/adr/002-real-data-acceptance.md`.
- `walk_forward` — the fixed-calendar OOS pipeline: `schedule`, `runner`,
  `metrics`, `evaluation`, `policy`, `snapshots`.
- `strategy_challenge` — the one-time pre-registered baseline/challenger
  comparison: `models`, `registry`, `compare`, `service`, `reporting`.

## Dependency direction

Observed package-level imports (verified against the source tree):

```text
data_sources -> config
data_model   -> data_quality, safe_yaml
data_quality -> data_model
analytics    -> (no internal imports)
factors      -> data_model, data_quality
backtest     -> config, data_model, data_quality, portfolio
portfolio    -> backtest, factors, research
research     -> backtest, config, data_model, data_pipeline, data_quality,
                data_sources, factors, logging, portfolio, safe_yaml
reporting    -> analytics, data_quality
```

The intended direction is **inwards towards data**: supplier adapters know
nothing about research, and quality gates know nothing about strategies.
`research` is the composition root and may import anything; nothing imports
`research` except `portfolio` (which imports the walk-forward policy hash
helper) and `cli`.

Two package-level cycles exist today. They are narrow and factual, not
sanctioned layering — do not extend them:

- `data_model` ↔ `data_quality`: `data_model.dataset` imports
  `data_quality.gates`, while `data_quality.raw_checks` imports
  `data_model.calendar` and `data_model.universe_membership`.
- `backtest` ↔ `portfolio`: `backtest.weight_rebalancer` imports
  `portfolio.buffered_models` and `portfolio.rebalance_band`, while
  `portfolio.rebalance_band` imports `backtest.models` for `BUY`/`SELL`/
  `LOT_SIZE`.

A change that adds a *new* package-level import in either direction should be
raised rather than assumed acceptable.

## Before you modify

| Changing | Read first |
| --- | --- |
| A supplier adapter or the raw store | `.claude/rules/data.md`, `docs/architecture/data-flow.md` |
| A canonical schema, cleaning rule or `adjusted_bar` | `.claude/rules/data.md`, `docs/architecture/invariants.md`, `docs/adr/001-content-addressed-publication.md` |
| A publication or acceptance gate | `docs/adr/001-content-addressed-publication.md`, `docs/adr/002-real-data-acceptance.md` |
| Universe membership or resolution | `docs/adr/003-point-in-time-universe.md`, `docs/architecture/invariants.md` |
| The research runner, spec freeze or walk-forward pipeline | `.claude/rules/research.md`, `docs/adr/004-walk-forward-oos.md` |
| Portfolio rules or risk estimation | `.claude/rules/portfolio.md`, `docs/architecture/invariants.md` |
| CLI, `config`, `bootstrap` or anything under `project/` | `.claude/rules/config-and-operations.md`, `RUNBOOK.md`, `docs/adr/005-explicit-project-root.md` |
| Anything under `tests/` or `tools/` | `.claude/rules/tests.md` |

## Authority boundary

This map records where code lives and which direction imports flow. It does
not restate what each module guarantees — that is `invariants.md` — nor why a
boundary was chosen — that is `docs/adr/`.
