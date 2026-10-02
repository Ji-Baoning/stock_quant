# Data flow

## When to read this

Read this before changing anything that produces, gates, freezes or publishes
an artefact: ingestion, quality checks, dataset publication, acceptance, the
research pipeline, or reporting. The path rule for the area you are editing
names the invariant and the verification command that applies.

## 0. Root resolution

Every command starts by resolving an explicit project root through
`stock_quant.project_root.resolve_project_root`. The root must exist and carry
`configs/project.yml`, `configs/sources.yml` and `configs/costs.yml`. There is
no fallback to the repository root, to the `templates/project-config/`
template, or to the current working directory — a missing root or missing
config fails before any service is constructed. See
`docs/adr/005-explicit-project-root.md`.

## 1. Ingestion and raw provenance

`python -m stock_quant data update --root <ROOT>` orchestrates:

1. **Fetch** through the configured supplier adapters in
   `stock_quant.data_sources`. Each source writes into its own raw tree —
   `data/raw/tushare/`, `data/raw/akshare/`, `data/raw/baostock/`,
   `data/raw/csi/` — and never overwrites another source's records.
2. **Raw checks** (`data_quality.raw_checks`) against the supplier frames.
3. **Normalize** (`data_model.normalize`) into the canonical field set:
   `trade_date, symbol, open, high, low, close, volume, amount, source,
   ingested_at`.
4. **Quality checks** (`data_quality.compare`, `data_quality.gates`) producing
   one `QualityReport`. Cross-source conflicts are recorded, never averaged:
   the primary source stays authoritative for a field and the difference is
   kept as evidence.

A source-fetch failure blocks the update: no dataset version is published and
`CURRENT` is unchanged.

**Raw-snapshot reuse (ADR-015).** Before each per-symbol request on the four
`_dispatch` lanes (primary daily, head-anchor probe, benchmarks, validation
daily), the raw store is asked for the newest stored answer to an exactly
identical request; a candidate is served back only when its bytes still
re-verify and it is not empty, and a stored-but-rejected candidate is a
visible warning followed by the live request. ADR-020 adds the batch shape on
top of this: the xingyao validation lane chunks its misses into `fetch_batch`
calls — one multi-code supplier call per chunk, falling back to per-symbol
requests while the `batch_size`/`batch_timeout_seconds` pair is unset — the
ADR-009 factor channel prefetches its candidate pool chunk by chunk, and the
tdx arbiter asks the whole candidate pool in one session with lazy per-symbol
fallback, while the primary daily, head-anchor and benchmark lanes stay
per-request. The batched lanes keep the per-symbol evidence shape — every
symbol still gets its own `request_key` and raw-snapshot path, because that is
what a reuse match resolves against — and each real transmission is recorded
once as content-addressed `BatchRequestEvidence` under `data/raw_batch_requests/`.
The admitted channels are the
`REUSABLE_CHANNELS` constant in `data_sources/raw_store.py`; corporate
actions, the trading calendar, the security master and the lazy arbitration
channels are always fetched live. A reused snapshot joins the round's
`raw_snapshots` evidence like a fetched one, `build_config.raw_snapshot_reuse`
counts reused versus fetched per channel, the call ledger carries a `reused`
section, and `build_config.baseline_version` names the baseline version the
carried tables came from.

## 2. Publication — the `dataset` version

`stock_quant.data_model.dataset.DatasetPublisher.publish` is the only writer:

```text
staged tables ──> dataset_version = hash(table records, normalized build_config)
      │
      ├─ quality gate (`evaluate_publication`) ── blocked ──> PublicationBlocked, nothing published
      └─ passed ──> data/staging/<uuid>/ ──os.replace──> data/standardized/<dataset_version>/
                    and the CURRENT pointer is atomically replaced
```

Properties that callers depend on:

- A published **dataset** version is content-addressed and immutable. An
  identical re-publish is a no-op, not an edit; a changed input produces a new
  version beside the old one.
- Each version carries `dataset_manifest.json` (table paths, hashes, build
  config) and `quality_report.json`. Reading goes through a per-version
  read-only DuckDB catalog over the Parquet tables.
- Every successful update also rebuilds the point-in-time total-return series
  `adjusted_bar` (`adjustment=internal_total_return_v1`) from unadjusted closes
  plus verified cash-dividend/bonus/capitalization events, and republishes the
  `corporate_action_quarantine` audit table.
- `CURRENT` names the latest fully gated version. A run that pins a version
  keeps reading that version for its whole lifetime; it never follows `CURRENT`
  mid-run.

`data validate --version <HASH>` re-runs the contract checks on an existing
version without writing anything.

## 3. Point-in-time universe evidence

Membership facts are evidence-bound records, not a symbol list:

```text
official snapshot (data/raw/csi/...)
   └─ data index-membership prepare  →  attested-boundary membership facts
         └─ dataset republished carrying the membership table
               └─ configs/universes/<id>.yml pins the table content hash
                     └─ universe_version = that definition's content hash
```

At research time the pinned membership table is re-evaluated against the
pinned dataset's calendar and security master (`index_membership_evidence`). A
missing, ambiguous or cardinally wrong membership set fails the run before any
factor is computed; there is no override switch and no fallback to the
master's full symbol list. See `docs/adr/003-point-in-time-universe.md`.

## 4. Real-data acceptance

`data acceptance prepare / publish / show` manages an immutable,
content-addressed acceptance record for one pinned dataset version
(`policy_version=real-data-v1`). `publish` re-runs every automated check and
re-hashes every bound evidence file; if anything is not PASS the REJECTED
record is persisted **first**, reasons are printed, and the command exits
non-zero. A formal research run resolves `CURRENT_ACCEPTED` to the latest valid
ACCEPTED record and re-verifies its binding hash on every run. See
`docs/adr/002-real-data-acceptance.md`.

## 5. Formal research run

`python -m stock_quant research run --spec <SPEC> --root <ROOT>` is the only
formal publisher. Ordering is load-bearing:

```text
load spec
  └─ pin dataset version exactly once (CURRENT resolved here, or an explicit hash)
       └─ universe preflight (evidence gate)          ── fail ──> FAILED preflight manifest, no identity
            └─ real-data acceptance gate              ── fail ──> no factor/portfolio/backtest is constructed
                 └─ freeze spec + 3 research snapshots (strategy / experiment / data env)
                      └─ experiment_id (identity scheme v2)
                           └─ factors            (only PIT-available data)
                                └─ portfolio     (eligibility → risk → weights → bands)
                                     └─ backtest (T+1 account, cost scenarios; or walk_forward_oos_v1 folds)
                                          └─ publish to data/experiments/<experiment_id>/ (atomic)
```

- The run workspace lives under `data/runs/<run_id>/` and keeps the log,
  manifest and per-stage artefacts. `data/experiments/` is the formal registry.
- `execution_pipeline: walk_forward_oos_v1` splits the requested OOS range into
  non-overlapping annual folds, writes and hashes `fold_schedule.json` before
  any backtest, records outcomes in a schedule-hash-bound ledger, and keeps
  failed folds permanently. See `docs/adr/004-walk-forward-oos.md`.
- Cost scenarios share the same membership and theoretical weights; each
  scenario integer-shares its own signal-day equity.
- `python -m stock_quant backtest <spec> --engineering` is the diagnostic path:
  it publishes under `data/runs/debug`, is recorded UNTRUSTED, and can never
  produce an ACCEPTED experiment.

## 6. Reporting

`python -m stock_quant report build --experiment <ID> --root <ROOT>` renders a
self-contained static HTML report from already-committed artefacts
(`analytics.performance` for metrics, `reporting.html` for rendering). It
fetches no data and recomputes no strategy decision, so a report is always a
faithful view of the published experiment.

## 7. Read-only query surface

`python -m stock_quant.service --root <ROOT>` serves GET-only views over
already-published artefacts (ADR-021). Every data request resolves its
version exactly once — `current` or a full hash at the entry — and every
response echoes the resolved full `dataset_version` plus the request's
parsed arguments; a request never re-reads `CURRENT` mid-flight, so a
publication that lands during a request leaves it on the old complete
version. The service opens one read-only `DatasetReader` context per
request, binds `127.0.0.1` only (non-loopback configuration fails
startup), imports neither the data pipeline nor a publisher, and offers no
arbitrary SQL, file paths or downloads; table previews are bounded by the
version manifest's tables, the canonical schema whitelist, `limit ≤ 500`
and an independent query time budget. Acceptance is not automated here:
the service only displays the four read-only summary states
(`ACCEPTED`/`REJECTED`/`PENDING_CONFIRMATION`/`UNVERIFIED`), and no
endpoint can write an acceptance verdict, publish a dataset or start a
research run.

## Failure semantics

| Failure | What survives |
| --- | --- |
| Source fetch error | No new dataset version; `CURRENT` unchanged; raw records already stored remain |
| Quality gate refusal | `PublicationBlocked`; staging removed; no version |
| Universe preflight rejection | FAILED run manifest + redacted preflight record; no identity, no factor artefact |
| Acceptance gate failure | REJECTED acceptance record persisted first; no run artefacts |
| Mid-run exception | FAILED run manifest retained under `data/runs/`; completed folds kept |
| Publication while the service reads | The in-flight request keeps its resolved version; the next request may resolve the new one; no reader ever sees a half-published tree |

## Authority boundary

This file describes the current lifecycle and the ordering constraints that
make it auditable. The prohibitions it implies are stated normatively in
`invariants.md`; the operator procedure is `RUNBOOK.md`.

- 自动更新路径（P4）：timer → `operations update` →（job 记录 + 心跳）→
  `data update` 子进程（raw → normalize → gate → publish，不变）。验收不
  自动化：job SUCCEEDED 只表示发布门禁通过，ACCEPTED 仍需人工验收。
