---
status: accepted
date: 2026-09-27
decision: "The three channels that pay one supplier session per request every round — the xingyao validation daily lane, the ADR-009 backward-factor channel and the tdx arbitration prefetch — move to batched transport: xingyao daily and backward_factor serve a chunk with one multi-code supplier call and the tdx arbiter asks a whole candidate pool in one session with lazy per-symbol fallback, while every per-symbol invariant stays — one logical request per symbol with its own request_key snapshot, three-state outcome judgement in the adapter parent process, the reuse-before-network contract, and a new content-addressed BatchRequestEvidence registry recording the real batch transmissions the per-symbol manifests cannot hold."
affects:
  - src/stock_quant/data_sources/xingyao.py
  - src/stock_quant/data_sources/xingyao_factor.py
  - src/stock_quant/data_sources/base.py
  - src/stock_quant/data_sources/batch_evidence_store.py
  - src/stock_quant/data_model/batch_evidence.py
  - src/stock_quant/data_model/call_ledger.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/config.py
  - project/drift_audit.py
  - project/configs/sources.yml
---

# 020 — The batched validation channel

## Context

Three channels pay one supplier session per request, every round:

1. **The xingyao validation daily lane.** Under the per-symbol wiring, each
   fetch is a new worker: one login plus one full trading-calendar session per
   symbol. Probe 5 (ADR-016 decision 11) measured the session overhead bound
   as F < 0.0025 quota units/session — its true value is unresolvable from the
   counter's 0.01-unit granularity — so 661 symbols extrapolate to ≈45–60
   minutes of pure login wait per round and the round's total cost lands
   anywhere in ≈0.8%–167% of the weekly quota; the worst case is infeasible,
   and the decisive unknown cannot be pinned from counter readings alone.
2. **The ADR-009 backward-factor channel**, one `get_backward_factor` call per
   symbol, pays the same session overhead per ask.
3. **The tdx arbitration prefetch**, whose `get_xdxr` has no multi-code
   capability, pays one session per symbol it is asked about.

The SDK's `query_kline` natively accepts a code list (the Phase 0 probes
themselves fetched 1000 symbols per batch), and ADR-016 decision 11 records a
batch channel as the enabling condition for `xingyao.enabled: true` — enabling
remained a config change only, but the batch channel had to exist and be
accepted first. This record is that channel.

## Decision

**Batched transport for the session-per-request channels, behind the unchanged
per-symbol evidence contract.**

1. **D1 — Batch boundary.** One session plus one multi-code supplier call
   serves a chunk. One symbol = one logical request = one evidence record
   stays an invariant, and the evidence must bind a reconstructable real batch
   request.
2. **D2 — Empty-response semantics.** `empty` means only "the supplier
   returned a zero-row object": that object is stored as the request's raw
   snapshot, it is not counted a failure, it does not suppress the source
   status, and it never enters reuse. "The answer contains no such code"
   defaults fail-closed to `refused`, unless the supplier contract or a
   distinguishable return flag proves that a missing key means legitimately no
   data. ADR-009 and this decision are different layers: ADR-009 governs reuse
   eligibility (unchanged — an empty frame is never served back), this
   decision governs failure accounting (new).
3. **D3 — Three failure layers, never flattened into one.** Batch-level
   failures are recorded per chunk and never fabricated into per-symbol
   refusals; a terminal chunk failure sets the source's `ok=False` with
   `reason_code="batch_fetch_failure"`. A per-symbol `refused` comes only from
   a named refusal or a missing key under a successful batch answer. Only
   `TimeoutError`/`ServerError` on a chunk whose retries are exhausted may
   bisect (at most 1 → 2 → 4 pieces); authentication, configuration,
   whole-answer contract and rate-limit failures do not bisect.
4. **D4 — Gate relation.** This ADR satisfies the precondition of ADR-016
   decision 11. `xingyao.enabled: true` remains a separate enabling change
   with its own review.
5. **D5 — tdx and the factor channel apply the same principle.** Candidate-pool
   prefetch plus lazy fallback, and no session is established for an empty
   pool; the candidate set is taken from reconcile's *input* frames, so there
   is no circular dependency. The cost is recorded honestly: the tdx adapter
   layer has no multi-code query capability (`_fetch_xdxr` runs
   `for symbol in symbols: await getter(symbol)`), so prefetch takes the
   *session* count N→1 while the code-query count stays N — and genuinely
   *increases*, because the candidate superset ⊇ the symbols actually
   consumed. The xingyao factor endpoint is probed and configured
   independently, and its gain is the chunk count, not unconditionally 1.
6. **D6 — The batch path keeps ADR-015's reuse-before-network contract.**
   Reuse hits never enter a batch; only misses do. The parent process counts
   `sessions` and `code_queries` by *attempted* transport operations —
   failures, retries and bisection calls all count — and the ledger is
   persisted at every terminal state of an update. The per-request
   `{reused, fetched}` semantics do not change.
7. **D7 — Zero-row frame validation is relaxed, narrowly.**
   `validate_supplier_frame` on a zero-row frame still requires the mandatory
   symbol/date columns and still refuses a `truncated` marker; it skips the
   returned-symbol-set equality and date-value checks, which have no values to
   judge on zero rows. Non-empty frames pass the original checks unchanged.
8. **D8 — Two-layer request provenance.** The per-symbol
   `request_parameters`/`request_key` remain the logical request. An
   independent, content-addressed `BatchRequestEvidence` records the complete
   ordered real transmission (endpoint, symbols, window, params, timestamps)
   plus each symbol's outcome and snapshot identity, so raw-manifest dedup
   cannot swallow the batch metadata. Versions bind their evidence hashes;
   drift audit replays a recorded batch in its original shape; new batch
   evidence must not degrade into per-symbol re-asks, because missing keys,
   truncation and returned shapes can depend on the batch composition.

## What this does not change

- **`xingyao.enabled` stays `false`** (D4). The batch channel is the satisfied
  precondition, not the enablement; enabling is a separate action and ADR-016
  decision 11's gate applies to that action.
- **The single-symbol `fetch` keeps its strict semantics.** An empty frame
  from a per-symbol request is still a `ContractError`. The new "empty is an
  answer" mapping belongs to the batch path only (`fetch_batch` maps a
  supplier-returned zero-row object to `empty`). ADR-009's "empty is absence,
  not an answer" still holds for the single request.
- **The `RawSnapshot` path layout and the `request_key` algorithm.** Every
  `ok`/`empty` symbol still gets its own snapshot under
  `<source>/<endpoint>/<transport_id>/<request_key>/<sha>` with its own
  per-symbol `request_metadata`.
- **`REUSABLE_CHANNELS`** and its admission invariant — the admitted set
  remains exactly equal to the `reuse=True` call sites.
- **The ADR-013 arbitration chain** (tdx, then price observation): the
  prefetch changes when tdx is asked, not what its answer is worth.
- **ADR-009's reuse-eligibility criteria**: the lazy channel is still asked
  lazily, fails closed, and asserts nothing when absent; empty frames are
  never reusable.

## Implementation notes

- **`fetch_batch` returns a `BatchResult`, not the bare outcome list spec §5
  sketched.** The result carries the outcomes — positionally aligned with the
  requests, which is what lets the reuse partition hand back only the misses —
  plus the `BatchTransmission` records. The reason is evidence: per-symbol
  response timestamps exist only inside the adapter, and the sub-chunks a
  bisection produces each carry their own transmission. The lane therefore
  records raw exactly like the per-symbol path, `self._record_raw(outcome.result)`,
  because the outcome already carries the assembled `FetchResult` with
  `request_key`, `request_metadata` and the request/response timestamps.
- **The subprocess returns only transportable data, and judges nothing.** It
  hands back frames as-is and folds any non-frame value into an
  `UnreadableFrame` marker, because a fork's return value must cross the
  process boundary and SDK-private objects are not guaranteed serializable.
  The per-symbol three-state judgement (`ok`/`empty`/`refused`) belongs to the
  adapter parent process, so `validate_supplier_frame` stays in one place.
- **Batch evidence lives outside the raw tree.** `RawStore.save` dedups by
  `request_key/file_sha256`, so batch metadata appended to a per-symbol
  manifest would be swallowed by whichever snapshot saved that frame first.
  The `BatchRequestEvidence` records land in their own content-addressed
  registry, `data/raw_batch_requests/<source>/<endpoint>/<transport_id>/<batch_id>/<sha>.json`.
- **The adapter's process-boundary timeout for `fetch_batch` is
  `batch_timeout_seconds`, falling back to `timeout_seconds` when the batch
  pair is unset.** The lane guarantees the pair is configured (one pair per
  endpoint, both fields or neither — `SourceConfig` rejects a half pair), so
  the lane never relies on the fallback; direct callers — unit tests and the
  drift-audit replay — do not crash on an unconfigured pair.
- **`render_call_ledger` renders the union of sources/reused/transport names.**
  A source that appears only in the counters — the run never got to record
  `calls` for it — still gets a row with `calls: 0`, so consumed quota is
  never dropped from the accounting, and the row shape is stable across rounds.

## Consequences

- **tdx's `code_queries` genuinely increase.** The multi-code capability does
  not exist; N→N refers to the session count, not the queries — the prefetch
  replaces one session per symbol asked with one session over the candidate
  pool, and the query count grows with the pool because the superset covers
  symbols the round may never consume (D5).
- **Batch evidence does not enter dataset identity.** It binds to a published
  version only by hash, through `build_config.batch_request_evidence`; the
  per-symbol snapshots keep carrying the dataset's raw evidence.
- **The daily batch pair is frozen from measurement; the factor pair is not.**
  The 2026-09-27 probes (`docs/operations/2026-09-27-batched-channel-probes.md`,
  run under owner authorization) measured a 329-code daily chunk accepted in
  one call (max full-chunk wall clock 6.46s over three clean repeats), frozen
  as `batch_size: 329` / `batch_timeout_seconds: 20` in both `sources.yml`
  files. The backward-factor probes could not measure a chunk: the endpoint
  answers a multi-code call with a single wide frame (one column per code,
  full-history index), not a mapping keyed by code, so the D5 factor shard
  assumption does not hold — `factor_batch_*` stay unset, the factor channel
  keeps its per-symbol fallback, and the wide-table follow-up awaits an owner
  ruling (ops record §六-1). Probe 2 measured the suspension shape: a symbol
  with no trading day in the whole window comes back as a present key holding
  an unframe-able value — `refused`, never a zero-row object — so per-symbol
  `refused` noise for long-suspended symbols is the accepted fail-closed
  behaviour at enablement time, and the `empty` branch stays (fail-safe; no
  zero-row object was ever observed, which one probe window cannot generalize
  into "never").
- **The `SourceStatus` vocabulary gains `batch_fetch_failure`** — a
  non-blocking, chunk-level failure code alongside the per-symbol
  `partial_fetch_failure`. Without it a whole-chunk failure would pass as
  "zero symbols, zero warnings", a shape the per-symbol wiring cannot produce.

## Rejected alternatives

- **Stuffing the batch metadata into the per-symbol manifests.** `RawStore.save`
  reuses an existing snapshot directory for the same `request_key/file_sha256`,
  so the batch fields would be silently lost whenever a single-symbol request
  saved the frame first; the per-symbol manifest is also the wrong layer for a
  fact about a multi-symbol transmission.
- **Mapping "the answer has no such code" to `empty`.** It would keep
  `validation_present` unchanged but corrupt the health signal — warnings,
  `SourceStatus.ok` and `reason_code` — for an absence whose cause (truncation,
  silently dropped codes, supplier miss) cannot be distinguished. Fail-closed
  `refused` is the honest default until a supplier contract proves otherwise.
- **Bisecting on rate limits.** A 429 answered by slicing the chunk smaller is
  still the same demand on the same quota; the backoff retry policy answers it,
  and bisection is reserved for time-shaped failures on an already-retried
  chunk.
- **Replacing `fetch` with `fetch_batch` and relaxing its strictness.** The
  per-symbol path's `ContractError`-on-empty is pinned by tests and reused by
  the drift audit; widening it to make one lane convenient would change
  semantics everywhere `fetch` is already the contract.

## Evidence

- Batch-channel probes, run 2026-09-27 under owner authorization
  (`docs/operations/2026-09-27-batched-channel-probes.md`, `.evidence.json`
  alongside it, `_status: measured`): daily chunk ceiling 329 codes (a
  659-code call is refused because a whole-window-suspended code comes back
  unusable, not because of size), full-chunk latency max 6.463s, lane counter
  model confirmed (1 session / 1 code query / attempt code count 329), the
  absence shape and the factor wide-table finding, and the two items awaiting
  an owner ruling (§六).
- Design record with the batch boundary, failure semantics, interface and
  probe plan: `docs/superpowers/specs/2026-09-27-batched-validation-channel-design.md`.
- The precondition this record satisfies: ADR-016 decision 11 and its probe 5
  session-cost bound (`docs/operations/2026-09-26-xingyao-phase0-probes.md`).
