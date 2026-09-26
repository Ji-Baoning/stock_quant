---
status: accepted
date: 2026-09-26
decision: The four per-symbol dispatch lanes (primary daily, head-anchor backfill probe, benchmark history, validation daily) consult the immutable raw store before the network and serve back the newest stored answer to an exactly identical request once its bytes re-verify, gated by a REUSABLE_CHANNELS constant; a candidate that exists but cannot be trusted is a visible refusal that never falls back to an older digest, while corporate actions, the trading calendar, the security master and the lazy arbitration channels stay live every round.
affects:
  - src/stock_quant/data_sources/raw_store.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_model/call_ledger.py
---

# 015 — Raw-snapshot reuse for eligible channels

## Context

Spec D5 (2026-09-19) made the *window plan* incremental: `last_covered_plus_1`
narrowed each fetch window to what the baseline does not already carry, and
ADR-011 re-anchored the acceptance window so incremental versions stay
acceptable. But the *execution layer* still asked the supplier for every
symbol, every round: `RawStore.save` deduplicates storage, never fetches.

The asymmetry is most expensive in exactly the case increments were built
for. A round that dies halfway through its symbol loop (rate limit, network)
leaves every answered symbol's snapshot in the raw tree and publishes
nothing; the retry round plans the identical window, computes the identical
request keys, and re-pays the whole supplier cost — the baostock bounded
retries alone cost hours at 659 symbols.

Meanwhile the raw tree already holds, for each of those requests, a stored
answer with its exact request parameters, observation timestamps and two
content hashes. The tree is de facto the coverage state. What was missing was
the decision to *read* it.

## Decision

**The raw store answers the question "has this exact request already been
answered?" — under a channel allow-list, with re-verification, newest-only.**

1. **`REUSABLE_CHANNELS` is the first gate.** Only
   `("tushare", "daily")`, `("baostock", "daily")` and
   `("akshare", "index_history")` may be served from disk. These are exactly
   the four `_dispatch` lanes (the daily channel serves both the primary
   fetch and the head-anchor probe), which are exactly the channels whose
   windows the incremental plan narrows — a hit means the same question the
   supplier already answered this project. `resolve_reusable` returns `None`
   for any other channel regardless of what its caller asks, so wiring a new
   call site cannot silently widen the policy.

2. **The match is exact and defensive.** Candidates are found by request key
   and then their stored `request_parameters` are compared field by field
   against the live request. A manifest without a well-shaped parameters
   record — the lazy-arbiter shape — never matches. The comparison cannot
   raise; anything unreadable fails to "no match".

3. **Only the newest candidate is considered, and a refusal is a refusal.**
   Candidates order by `response_timestamp` (missing last, digest as tie
   break) and the newest one alone is re-verified through
   `verify_evidence` — the same dual-hash check acceptance uses, not a
   private re-implementation. Verification failure returns `None`; the
   method never falls back to an older digest. An older digest under the
   same request key is by definition a supplier-revised historical
   observation: silently replaying it would book a tamper signal as a
   successful reuse. Falling back to live is both fail-closed and
   self-healing — it produces a fresh, verifiable observation.

4. **An empty response is absence, not an answer** (the ADR-009 stance).
   The default refuses an empty frame; only the head-anchor backfill probe
   passes `allow_empty=True`, because its loop semantics are "empty chunk,
   walk back further", so a stored empty chunk is precisely its
   acceleration. Everywhere else a reusable empty frame would nail a
   not-yet-published session into the record permanently — a defect no
   later round could recover without hand-deleting evidence.

5. **A reused snapshot enters the round as ordinary evidence.** The fetch
   layer rebuilds its result from the stored parquet and stored (redacted)
   metadata, and appends the snapshot itself to the round's
   `raw_snapshots`, so normalization, quality checks, the published
   `raw_snapshots` evidence rows and offline re-verification all treat it
   exactly like a freshly fetched response. The upstream snapshot is never
   rewritten.

**The channels that stay live stay live for stated reasons.** The trading
calendar is the clock the update end is resolved from; the security master
defines the universe; corporate actions sit on a mandatory 90-day
disclosure lookback because revisions reach backwards (ADR-007/009/012/013
all exist because that channel revises); the lazy arbitration channels are
evidence channels whose value is being freshly answerable. Reuse for any of
them is a separate decision, not a refactor here.

**The reuse is visible.** `build_config.raw_snapshot_reuse` carries per
source × endpoint `{reused, fetched}` counters into the version hash, and
the call ledger (D5.5) always carries a `reused` section beside `calls` —
reused requests consumed no quota. `build_config.baseline_version` names
the immutable version a build carried its tables forward from (explicit
`null` when there was none, so "missing key" keeps its legacy-contract
meaning), closing the carried-segment chain to the manifest the carried
evidence actually lives in.

## What reuse does not change

- **Availability semantics.** `_require_available` checks configuration
  only; it performs no probe. A round in which every symbol hits reuse
  therefore makes zero calls *and zero probes* against that supplier. That
  is acceptable because every reused snapshot is itself the record of a
  live answer to the same request; but it means reuse is not gated by any
  reachability check, and the ADR states so rather than implying a
  mechanism that does not exist.
- **Fail-stop.** A symbol with no stored answer still requests live and
  fails by the existing rules. Reuse is never a substitute for an
  unanswerable request: a dead supplier cannot be published from disk
  alone.
- **`ingested_at`.** Rebuilt rows carry the original observation's
  `response_timestamp`, so a published `ingested_at` now means "when this
  observation was first obtained", not "when this version was built";
  within one version the column may be non-uniform. This is the honest
  reading and is recorded here so it is not "fixed" as a bug later.
- **Version identity.** Reuse only makes some evidence rows older. Builds
  were never deterministic across time (timestamps enter the manifest);
  this record neither weakens nor strengthens that.

## Compensating controls

- **Re-verification at the moment of use** (dual hash via
  `verify_evidence`) — a stored answer is trusted exactly as far as its
  bytes, never as far as its directory.
- **The drift audit (spec D5.4, `project/drift_audit.py`) is independent
  of reuse.** It re-fetches every snapshot bound by a published version and
  compares hashes (`classify_drift`); it does not consume sibling
  directories under a request key. Supplier-side byte drift therefore has
  a quarterly moment of certain visibility no matter how much a round
  reused.
- **`raw_snapshot_reuse` and the ledger's `reused` section** make the
  reuse/fetch split auditable per round, so "how much of this version was
  replayed" is a reading, not an inference.
- **The refusal path is loud**: a stored-but-rejected candidate logs a
  `reuse_candidate_rejected` warning and the symbol is fetched live.

## Consequences

- A retry round after a mid-loop failure pays supplier cost only for the
  symbols the failed round never answered; the answered prefix is served
  from disk. Same-day re-runs stay at zero calls (the whole lane was
  already skipped by D5.2 planning).
- The raw tree becomes the operative coverage state for the four lanes.
  There is no separate state file to drift out of sync — but also no
  invalidation switch: the recovery for "this window must be re-asked" is
  deleting the request's evidence directory, which RUNBOOK §4 gates behind
  a binding check because published manifests point into it.
- Legacy four-segment snapshots (pre-transport layout) are never matched
  by the reuse lookup, although `verify_evidence` can still read them.
  They are always re-fetched; that is deliberate conservatism, not an
  oversight.
- **Expected, correct cost of decision 4:** a symbol whose entire
  incremental window is suspended returns an empty primary frame; that
  frame is not reusable, so such a symbol costs one live call per retry
  round. This is the price of not nailing unpublished sessions into the
  record, it involves few symbols, and it is not a defect.

## Rejected alternatives

- **Reuse as a substitute for an unanswerable request** (publish from disk
  while the supplier is down). Reuse answers "ask this again more cheaply",
  not "publish without a source"; the required-source availability gate and
  fail-stop semantics stay untouched.
- **Silent fallback to an older candidate** when the newest fails
  re-verification. The older digest is the supplier's superseded
  observation; replaying it converts a tamper signal into a successful
  reuse and books unverified history as evidence.
- **Reuse for corporate actions, the calendar, the security master or the
  lazy arbitration channels.** Each is live for a stated reason (lookback
  obligation, clock, universe definition, evidence freshness); making any
  of them reusable would trade a bounded cost for a specific audit hole.
- **A persistent coverage state table** (per symbol × channel rows updated
  per round). It would be a second copy of a fact the raw tree already
  holds, able to disagree with it, and it would need its own
  rebuild-and-verify machinery. The store is the state; `resolve_reusable`
  derives the answer at the moment of use.

## Evidence

`tests/unit/test_raw_reuse.py` pins the admission rules (gate, newest-only
without fallback, defensive parameter match, empty-frame rule, legacy
layout, round-trip). `tests/integration/test_raw_snapshot_reuse.py` pins
the round behaviour: a retry round after a mid-loop failure makes zero
daily calls for the answered prefix while corporate-action endpoints stay
live; a tampered snapshot is refused, warned, re-fetched beside (two
digests under one request key) and the build records `reused`/`fetched`
counts; a reused answer equals the live answer it stands in for, value for
value, with the evidence row identical to the stored one. The pre-existing
calendar-failure semantics (`tests/integration/test_data_pipeline.py`,
"old raw bytes are never replayed as a calendar cache") are unchanged —
`trade_cal` is not an admitted channel.
