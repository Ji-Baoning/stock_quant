---
status: accepted
date: 2026-09-27
decision: The daily suspension materialization's proof grid becomes the fetch window plus the carried/fresh seam — the open days after a symbol's last carried bar, with that bar prepended to the pre_close chain as the before-anchor — instead of the whole validation window; the head-anchor deepening probe keys to the same fetch window. A supplier day-hole at the seam (2026-09-22, four halted symbols) is thereby provable from the fresh resumption row's reference price; gaps interior to the covered span remain unprovable by design and stay the acceptance gate's honest failures pending an owner decision.
affects:
  - src/stock_quant/data_pipeline.py
---

# 020 — The suspension proof grid is the fetch window and its seam

## Context

The 2026-09-27 acceptance readback of `f68df633…` failed `date_window_
completeness` on four symbols' 2026-09-22 bars, and the edge-advancing
rebuild (`10cc8c4c…`) recorded **659** `suspension_run_unverified` warnings
and spent half its runtime in reuse-served probe calls. Both trace to one
structural mismatch:

- The daily fetch is incremental (`last_covered_plus_1`): a steady-state
  round fetches only the uncovered tail — three days.
- The suspension materialization's proof grid was the **validation window**
  (`project.yml`'s start through the resolved end) — five years.
- Each symbol's proof chain was therefore the round's three-row response
  against a five-year grid: every open day before the fetch window read as
  an absent "run", the head-anchor deepener probed every symbol backwards
  towards its listing date, and every run was (correctly) refused as
  unverifiable.

The trigger for the investigation: tushare's per-symbol response has **no
row on 2026-09-22** for four halted symbols (`601059.SH`, `601198.SH`,
`601238.SH`, `601995.SH` — a supplier-side day hole inside a halt that
produced zero-volume rows on 09-15..09-21 and 09-23/24), while the recorded
fetch span claims the day covered, so no later round would ever re-fetch it.
The acceptance gate failed on the hole — honestly, per its own design
("suspensions and unexplained gaps are flagged, never accepted").

## Decision

1. **The proof grid is the fetch window plus the seam.** For each symbol the
   grid is the open days after its last *carried* bar through the round's
   end; the carried bar itself is prepended to the chain as the before-
   anchor. A normally-trading symbol (carried bar on the day before the
   fetch window) has an empty seam and an empty absent set — no warnings, no
   proofs, identical output to a full-window round that has nothing to prove.
2. **The seam is proved by the existing rule, nothing weaker.** The fresh
   row's `pre_close` must chain to the carried close (and any ex-event in
   the run must explain the difference, per `suspension_rows`): 601995's
   resumption (09-23, `pre_close` = 31.8 = the carried 09-21 close) proves
   09-22; the three still-halted symbols' 09-23 zero-volume rows chain to
   their carried closes the same way. No new tolerance, no new evidence
   class — the carried bar is the same supplier's previously snapshotted
   answer, and the published table is content-addressed.
3. **The deepening probe keys to the fetch window's first open day**, not
   the validation window's: only a symbol whose fetch window opens inside a
   suspension probes backwards.
4. **Gaps interior to the covered span stay unprovable.** The seam reaches
   the boundary between the carried table and the fetch window; a supplier
   hole deeper inside the covered span (which the 09-22 hole becomes the
   moment the next round carries 09-23..09-25) has no after-anchor inside
   any round's chain and no operator path re-fetches a covered span. The
   acceptance gate keeps failing on it — the fail-closed direction — until
   an owner decision adds an interior-gap mechanism (a bounded zero-volume-
   run rule, or a suspension-calendar attestation channel).

## Consequences

- Steady-state rounds stop emitting 659 unverified-run warnings and stop
  probing every symbol back to its listing date; the materialization proves
  exactly the days the round fetched plus the seam.
- The next fetch-bearing round materializes a proven 09-22 seam bar for
  halted symbols whose hole sits at the then-boundary; the 2026-09-22 hole
  itself is now interior (09-23..09-25 are carried) and remains an
  acceptance failure for `601059`/`601198`/`601238` until that owner
  decision — three symbols, one day, each with the supplier's own zero-
  volume rows on both sides.
- The unit suite gains the seam proofs (`test_suspensions.py`): heal,
  no-op, and honest-tail shapes.

## Rejected alternatives

- **Keep the validation-window grid.** The 659-warning noise and the probe
  storm recur on every steady-state round, and the four-symbol hole stays
  unproven anyway.
- **Trust the carried table as the after-anchor for interior runs** (no
  fresh data at all). The published table has no `pre_close` column, so the
  chain rule has nothing to compare; a rule keyed on unchanged zero-volume
  closes alone would be weaker than any rule this record adopts, and is
  reserved for the owner's interior-gap decision.
- **Re-fetch the covered span to reach the hole.** No operator path exists
  (`last_covered_plus_1` skips covered days; F1 refuses explicit windows
  that deviate), and the supplier's response for the hole days is itself
  empty — a re-fetch proves nothing.

## Evidence

- The hole: tushare per-symbol responses read 2026-09-27 — `601238.SH`
  [2026-09-18..09-26] returns 09-18, 09-21, 09-23, 09-24; no 09-22 row,
  while the published table carries zero-volume rows for 09-15..09-21 and
  the whole-market snapshot counts are 661/661/657 rows on 09-18/21/22.
- The probe storm: `10cc8c4c…`'s quality report — 659
  `suspension_run_unverified` warnings with runs of the shape
  `2021-01-04..2026-09-22`.
- Guard tests: `tests/unit/test_suspensions.py` (seam heal, normal-symbol
  no-op, still-halted tail stays unverified).
