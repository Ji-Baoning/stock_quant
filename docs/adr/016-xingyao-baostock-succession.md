---
status: accepted
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

星耀数智 passed a full quality evaluation on 2026-09-25 (14 checks: 11 pass, 2
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
   This config change ships only after both xingyao successor lanes are ready;
   the two implementation batches may be tested separately but are one release.

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

8. **The compensating control extends to both new endpoints.** `drift_audit`
   dispatches by `(source, endpoint)`: xingyao daily uses the daily adapter and
   `backward_factor` uses a minimal `XingyaoFactorSource` implementing the same
   `DataSource.fetch(DataRequest) -> FetchResult` boundary. The factor adapter
   exists only so the audit can reproduce the original request and snapshot
   shape; it is not added to `_CONFIGURED_SOURCES`. Unknown endpoints,
   unverifiable snapshots and fetch failures count as audit failures and make
   the command exit non-zero; zero drift can no longer hide zero completed
   comparisons.

9. **The two false comments are corrected**, with this record as the reason.

10. **The test fixtures follow the runtime.** `_fixture_sources_yaml` had been
    force-enabling every supplier segment so that fixtures exercise every
    optional source rather than inheriting an operator toggle. That exemption
    is withdrawn for baostock: fixtures now set `baostock.enabled: false`,
    matching the shipped runtime. Leaving it enabled would run a lane the
    project no longer runs in every ordinary fixture update, and would put a
    dispatched baostock row in the call ledger that production never produces
    — a fixture reproducing a shape the runtime cannot reach misleads exactly
    the assertions this record changes. The dormant lane is *not* deleted:
    tests that need it opt in with `write_sources(baostock=True)`, and one
    such test is the sole guard on the `reuse=False` wiring. The template
    `sources.yml` ships `enabled: false` too, so a newly scaffolded project
    does not start out re-running this cleanup.

11. **The Phase 0 probes fix the record's open constants and gate
    enablement.** All six probes completed on 2026-09-27 without a BLOCKER
    (`docs/operations/2026-09-26-xingyao-phase0-probes.md`, with
    `.evidence.json`), and their findings are part of this decision:

    - **Suspended days come back as absent rows, not zero-volume rows.** 601238.SH's nine
      suspension days (2026-09-14..09-24) return no daily rows at all, where
      the primary source (tushare) books zero-volume rows and baostock's
      documented shape was present `tradestatus=0` rows. The missing-row flip
      therefore never fires for a suspension day — both sides are absent —
      and suspension days keep classifying as `unknown_or_suspended`. The
      claim that xingyao carries the baostock lane item for item is narrowed
      accordingly: the flip separates unexplained primary-source gaps, not
      supplier-suspension shapes, and the residual row-shape difference
      surfaces only in the existence comparison layer (measured: 44 and 26
      symbol-days in the two probe windows, all published-side zero-volume
      suspension rows).
    - **Volume/amount units are measured (1, 1)** (probe 6: per-day volumes
      integer-exact against the published set, amounts equal at display
      precision to ≤±0.07 yuan/day, and a tushare relay cross-check exact
      field for field). The promotion gate on units (spec §4.5/§2.4) is
      closed; `_UNIT_FACTORS["xingyao"] = (1, 1)` is a measurement, not an
      assumption.
    - **Daily-bar depth starts 2013-01-04** (probe 4): the promotion
      condition on history depth is satisfiable against the
      `full_history_acceptance_start` = 2015-01-05 anchor, but nothing before
      2013-01-04 is obtainable from this channel (a §3.3 promotion
      constraint), post-2013 delistings are covered up to delisting, and
      pre-2013 delistings are not.
    - **Factor snapshots are clipped to the recorded `end`** (owner ruling
      2026-09-26): the wide backward-factor frame is cut at the request's
      `end` before storage, so a drift-audit re-ask of the same request
      reproduces the stored bytes exactly.
    - **The per-symbol validation wiring costs one login+calendar session per
      symbol per round** (probe 5). The measured session bound is
      F < 0.0025 quota units/session (its true value is unresolvable from the
      counter's 0.01-unit granularity); 661 symbols extrapolate to ≈45-60
      minutes of pure login wait per round; and the round's total cost lands
      anywhere in ≈0.8%-167% of the weekly quota depending on the
      unmeasurable F — the worst case is infeasible, and the decisive unknown
      cannot be pinned from counter readings alone.

    Therefore **xingyao ships `enabled: false`.** A batch channel — the SDK's
    `query_kline` natively accepts a code list, and the Phase 0 probes
    themselves fetched 1000 symbols per batch — is the recorded enabling
    condition for `enabled: true`; enabling remains a config change only.

## What this does not change

- **The ADR-013 arbitration chain** (TDX, then price observation) is untouched.
  星耀's factor amplitude is not booked into any verdict.
- **No new published table.** Validation rows remain existence-only: they never
  enter `compare_daily_sources` and never enter a publication.
- **ADR-009's channel semantics** are preserved: the factor channel is asked
  lazily, fails closed, and asserts nothing when absent.
- **Hard timeout semantics.** tgw broker calls run behind a terminable process
  boundary because the repository's requests timeout cannot bound this TCP
  callback SDK; expiry terminates the worker and enters the ordinary transient
  retry path.
- **Published history.** Records already published with a `"baostock"` factor
  channel label keep that label; published datasets are immutable and their
  evidence is not rewritten.
- **`_REQUIRED_ROLE["baostock"]` stays `False`** and baostock stays in the
  registry, so a symbol's history can still be re-verified from its snapshots.

## Consequences

- While baostock is down, the missing classification degrades to
  `unknown_or_suspended` — a WARNING, and not a publication blocker. The
  Phase 0 probes (decision 11) measured the answer for 星耀: suspended days
  are absent rows on both sides, so the flip does not restore for suspension
  days and `unknown_or_suspended` remains their classification; what the
  xingyao lane can still flip are unexplained primary-source absences that
  are not suspension-shaped.
- **xingyao ships disabled (decision 11), so the two successor lanes are
  absent at runtime.** With `enabled: false` the source is never constructed:
  missing-row classifications stay `unknown_or_suspended` and the ADR-009
  factor channel is absent — fail-closed, which is operationally the same
  state the dead baostock service left, with every lane, admission slot and
  audit path wired for a one-switch enable. That is the honest shape of this
  release: the succession's capabilities exist in code and tests, not yet in
  rounds, and they start running only when the batch-channel prerequisite
  lands.
- A newly registered source widens every reading that iterates the configured
  set: `source_status` and `build_config.source_status` gain a row, and every
  "all sources ok" assertion acquires a new participant. Combined with
  decision 10 the call ledger is unaffected: it only registers sources that
  actually dispatched, and baostock no longer does. The design record
  enumerates the existing assertions this touches; the cost is test churn, not
  behaviour.
- **The dormant lane loses its default coverage** (decision 10). Nothing in
  the ordinary fixture path exercises baostock's validation call site any more,
  so its `reuse=False` wiring is guarded by exactly one opt-in test. The
  alternative — keeping the fixture exemption — would have covered it by
  default while making every other fixture assertion describe a runtime the
  project does not have. The coverage is now deliberate and named, and if that
  test is ever deleted the wiring has no guard at all.
- The call ledger's per-source `calls`/`endpoints` counters are zero for every
  source in the repository (no adapter defines `calls`); only the `reused`
  section is real. The new source's ledger row therefore carries presence and
  `reused`, and asserting more would be asserting a mechanism that does not
  exist.
- One live supplier's bytes leave the quarterly drift audit's reach, and a new
  one's enter it; the audit's own coverage is the thing to keep honest.

## Rejected alternatives

- **Keep the fixtures' force-enable exemption for baostock** (the pre-decision
  shape: fixtures run every supplier, so baostock's lane keeps executing with
  `reuse=False`). It covers the dormant call site by default, which is a real
  benefit; but it costs the fixture's likeness to the runtime — the ledger
  grows a baostock row production cannot produce, and "all sources ok" is
  asserted over a source that is retired. Likeness of the fixture to the
  shipped behaviour was preferred over incidental coverage, and the coverage
  was bought back explicitly instead (decision 10).
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
- Phase 0 probe measurements and their sha256 evidence file:
  `docs/operations/2026-09-26-xingyao-phase0-probes.md`
  (`.evidence.json` alongside it).
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
