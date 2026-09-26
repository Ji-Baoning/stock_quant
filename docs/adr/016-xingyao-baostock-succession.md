---
status: proposed
date: 2026-09-26
decision: baostock leaves the runtime — its supplier is unavailable and two of the three roles it was documented as holding were never real — and 星耀数智 (xingyao) takes over the two that were, the per-round validation daily lane and the ADR-009 lazy factor channel, as a newly registered optional source; because a lane that never runs must not hold an admission slot, ADR-015's allow-list substitutes ("xingyao","daily") for ("baostock","daily") rather than appending, so the allow-list stays exactly equal to the set of reuse=True call sites and the ADR-015 invariant is untouched.
affects:
  - src/stock_quant/data_sources/xingyao.py
  - src/stock_quant/data_sources/xingyao_factor.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_sources/raw_store.py
  - src/stock_quant/data_model/normalize.py
  - src/stock_quant/data_quality/raw_checks.py
  - project/configs/sources.yml
  - project/drift_audit.py
---

# 016 — 星耀数智 succeeds baostock

## Context

baostock's data server (`:10030`) was unreachable from 2026-09-05 and answered
again briefly on 2026-09-19. The owner reports it unavailable again as of
2026-09-26 and directs that it be disabled by default. It held three registered
runtime roles, and the record needs to state which of them were real:

1. **A per-round validation daily lane** (`_fetch_validation_daily`). Real, but
   narrower than its description. The lane fetches unadjusted daily bars for the
   current universe over the same incremental window as the primary fetch, and
   its only consumer is the missing-classification flip: a symbol-day absent
   from the primary source but present in this lane classifies as
   `primary_source_missing` instead of `unknown_or_suspended`. Both are
   WARNINGs; neither blocks publication.
2. **The ADR-009 lazy factor channel** (`_LazyFactorChannel` →
   `factor_event_dates`). Real. Asked only for a symbol that has an isolated
   row with no ex-date, it supplies a second price-event channel as the
   date-only sequence of adjacent adjust-factor changes.
3. **A suspension-evidence role and a `compare_daily_sources` role, both
   asserted by the sources.yml comment and neither true.** `tradestatus`
   appears exactly once in `src/` — as a field declaration in the adapter — and
   nothing interprets it; the supplier's own zero-OHLC/zero-volume rows are
   never read as suspension evidence, and the project's actual suspension
   facts come from tushare's own `pre_close` chain. Separately, the production
   consumer of `compare_daily_sources` is acceptance, reading a published
   `daily_bar` price sample; validation rows are never fed to it. The comment
   at `project/configs/sources.yml:21-25` states both non-facts today.

星耀数智 passed a full quality evaluation on 2026-09-25 (13 checks: 11 pass, 2
warn, 1 fail; prices, financials and yields day-by-day identical to independent
sources), and its backward-adjustment factor is a per-symbol full-history daily
series — the same semantics as baostock's `adjustFactor`, from a source that is
answering.

The decision gap: baostock is leaving, and its two real roles are the only
reason two classifications and one evidence channel exist. Retiring the roles
outright would silently widen `unknown_or_suspended`; the replacement has to be
decided, and so does what happens to the channel ADR-015 admitted for it.

## Decision

**星耀数智 becomes the successor source for baostock's two real roles, and
baostock's ADR-015 admission is substituted rather than retained.**

1. **xingyao is registered as an ordinary optional source.** It joins
   `_CONFIGURED_SOURCES`, `_REQUIRED_ROLE` (as `False`), `_build_source` and
   `KNOWN_SUPPLIERS`. Missing private SDK packages degrade exactly as baostock's
   do: optional-path WARNING, no block. Credentials come from the `AD_*`
   environment variables only.

2. **The validation lane is a new, independent xingyao lane.** It carries the
   semantics of the baostock lane item for item — same incremental window, same
   symbol set, `required=False`, existence-only consumption — because the
   successor's job is to be a second answer to the same question.

3. **baostock's lanes go dormant, not deleted.** `enabled: false` in
   `project/configs/sources.yml` means the source is never constructed and its
   lanes never execute. The adapter and `baostock_factor.py` stay in the tree.

4. **ADR-015 is amended, not superseded, and by substitution.** Its allow-list
   becomes `("tushare","daily")`, `("xingyao","daily")`,
   `("akshare","index_history")`. Substituting keeps the list exactly equal to
   the set of `_dispatch(reuse=True)` call sites, which is the invariant
   ADR-015 Decision 1 rests on; appending would have forced that invariant to
   relax to a subset relation and weakened the guard that a new call site
   cannot silently widen the policy. A dormant lane therefore must not keep its
   slot: the baostock call sites stay in code with `reuse=False`.

5. **Consequence, stated rather than discovered later:** restoring baostock's
   reuse is a re-admission (a code change with its own record), not a config
   flip. Restoring the *lane* remains a config change; restoring its *reuse*
   does not.

6. **The factor channel follows in a second batch.** `factor_event_dates`
   keeps its date-only contract; the factor amplitude stays unused and outside
   the ADR-013 arbitration chain, which does not change.

7. **星耀 stays a candidate for the `daily_bar` primary, not a promoted one.**
   This record does not change `data_contracts.py`, adds no broker transport
   kind, and builds no runtime failover. Promotion has its own conditions.

8. **The compensating control extends to the new source.** `drift_audit`'s
   `_source_for` maps raw-tree prefixes to sources and raises for anything it
   does not know; that exception is caught as `fetch_failed`, which is not
   counted as drift and does not affect the exit code. Left alone, the
   quarterly drift audit would report success over an audit it never performed.
   The prefix is added with the source.

9. **The two false comments are corrected**, with this record as the reason.

## What this does not change

- **The ADR-013 arbitration chain** (TDX, then price observation) is untouched.
  星耀's factor amplitude is not booked into any verdict.
- **No new published table.** Validation rows remain existence-only: they never
  enter `compare_daily_sources` and never enter a publication.
- **ADR-009's channel semantics** are preserved: the factor channel is asked
  lazily, fails closed, and asserts nothing when absent.
- **Published history.** Records already published with a `"baostock"` factor
  channel label keep that label; published datasets are immutable and their
  evidence is not rewritten.
- **`_REQUIRED_ROLE["baostock"]` stays `False`** and baostock stays in the
  registry, so a symbol's history can still be re-verified from its snapshots.

## Consequences

- While baostock is down and 星耀's suspension-day row shape is unmeasured, the
  missing classification degrades to `unknown_or_suspended` — a WARNING, and
  not a publication blocker. Whether 星耀 restores the flip depends on whether
  its suspended sessions appear as zero-volume rows or as absent rows; that is
  a pre-implementation probe, not an assumption.
- A newly registered source widens every reading that iterates the configured
  set: `source_status`, `build_config.source_status` and the call ledger all
  gain a row, and every "all sources ok" assertion acquires a new participant.
  The design record enumerates the seven existing assertions this touches; the
  cost is test churn, not behaviour.
- The call ledger's per-source `calls`/`endpoints` counters are zero for every
  source in the repository (no adapter defines `calls`); only the `reused`
  section is real. The new source's ledger row therefore carries presence and
  `reused`, and asserting more would be asserting a mechanism that does not
  exist.
- One live supplier's bytes leave the quarterly drift audit's reach, and a new
  one's enter it; the audit's own coverage is the thing to keep honest.

## Rejected alternatives

- **Keep `("baostock","daily")` in the allow-list** so that the reuse semantics
  return with the service. It buys a hypothetical future config-only recovery
  at the price of holding an admission slot for a lane that never runs, and of
  relaxing ADR-015's central invariant from equality to containment — the exact
  guard that keeps a new call site from widening reuse policy by accident.
- **Parameterise the existing validation lane with a source name** instead of
  adding a lane. Fewer lines, but the lane count would then disagree with the
  configured-source count, and the per-source shape of `source_status` and the
  call ledger would describe a source that no lane accounts for.
- **Retire the roles with baostock** (delete the validation lane and the factor
  channel; accept `unknown_or_suspended` permanently). Cheapest and honest, but
  it discards a working capability for the duration of one supplier outage,
  when a validated successor is available.
- **Silently edit ADR-015's channel list.** The amendment is the point; a
  reader must be able to find out why the list changed and what it costs.

## Evidence

- 星耀 quality evaluation: `docs/research/2026-09-25-xingyao-data-quality-evaluation.md`.
- Design record with the implementation boundaries, the seven affected
  assertions and the Phase 0 probes:
  `docs/superpowers/specs/2026-09-26-xingyao-baostock-succession-design.md`.
- Falsifications of the sources.yml claims: `tradestatus` occurs once in
  `src/` (`baostock.py`); `suspension_rows` materialises suspension facts from
  the tushare `pre_close` chain; `compare_daily_sources`'s only production
  consumer reads a published `daily_bar` sample.
- Admission rules pinned by `tests/unit/test_raw_reuse.py`; the baostock lane
  keyed by `tests/integration/test_raw_snapshot_reuse.py` and
  `tests/integration/test_pipeline_fetch_coverage.py`.
