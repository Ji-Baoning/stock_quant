# Architecture overview

## When to read this

Read this before an unfamiliar cross-module change, or when you need to know
which package owns a decision. For a change confined to one package, the path
rule for that package (`.claude/rules/`) is the narrower and preferred entry.
For the lifecycle of one artefact, read `data-flow.md` instead.

## Positioning

Stock Quant is a **reproducible** engineering MVP for A-share quantitative
research. It exists to prove that the full chain — supplier data, quality
gating, point-in-time universe, frozen experiment, chronological backtest,
portfolio construction and reporting — runs end-to-end and can be audited.

Phase one deliberately does **not** claim strategy validity, profitability, or
live-trading readiness. The published metrics are engineering evidence, not an
investment conclusion. `PROJECT_MEMORY.md` holds the long-lived business
context; this file holds only the current technical facts.

## Runtime shape

One local, single-process CLI — no server, no scheduler, no database. Every
command is invoked as `python -m stock_quant <group> <command>`, resolves a
project root explicitly, and is manual and idempotent:

```text
stock_quant.cli (Typer)
  └── bootstrap / services per command group
        ├── data       → data_pipeline, data_sources, data_model, data_quality
        ├── research   → research pipeline (freeze → universe → factors → portfolio → backtest)
        ├── backtest   → engineering-only single-window replay
        └── report     → reporting from already-published artefacts
```

All state lives on disk under the given project root (`data/raw`,
`data/standardized`, `data/acceptances`, `data/runs`, `data/experiments`).
There is no hidden global state and no fallback root; see
`invariants.md`.

## Core technologies

| Technology | Role |
| --- | --- |
| Python 3.10+ | Runtime. `pyproject.toml` declares `requires-python = ">=3.10"`; `environment.yml` pins the conda environment `stock-quant` to 3.10. |
| pandas | The unit of exchange between stages: every table read, normalized, gated and written is a `DataFrame`. |
| PyArrow | The on-disk interchange format. Published dataset tables and every published artefact are Parquet. |
| DuckDB | Read-only query layer over a dataset version's Parquet tables. Not a dependency of `pyproject.toml` — it is installed by `environment.yml`. |
| Pydantic v2 | The contract layer: frozen experiment specs, acceptance records, manifests, membership facts. Validation happens at load, not at use. |
| Typer | CLI surface and argument parsing. |

Reporting renders static, self-contained HTML through Jinja2 and Plotly; it
never fetches data at render time.

## Component relationship

```text
             data_sources  (supplier adapters, raw store)
                   |
                   v
             data_model    (canonical schemas, dataset publication)
                   ^
                   v
             data_quality  (neutral gates, cross-source checks)
                   |
                   v
  factors  ->  research   (spec freeze, universe preflight, acceptance gate)
                 |  \
                 |   v
                 |  backtest  <->  portfolio   (replay, costs, account)
                 v
             reporting / analytics  (static HTML, performance metrics)
```

The arrows are the observed import direction, not a claim of strict layering —
see `module-map.md`, which names the two narrow package-level cycles that
exist today.

Reproducibility is the property that binds these together: the same frozen
spec plus the same dataset version plus the same code commit produce the same
`experiment_id` and byte-identical artefacts. Runtime metadata (run id,
timestamps, host, pid, worker count) never enters an identity hash.

## Authority boundary

- This file and its three siblings under `docs/architecture/` state **current,
  verified facts**. When a fact changes, correct it in the same change that
  changes the code.
- Decisions and their rejected alternatives live in `docs/adr/`, not here.
- Procedures live in `RUNBOOK.md`; dated run evidence lives in
  `docs/operations/`; historical design lives in `docs/superpowers/`.
- Where this file and an ADR disagree, the ADR governs the decision and this
  file is stale — fix it rather than citing both.
