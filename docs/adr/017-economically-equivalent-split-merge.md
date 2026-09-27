---
status: accepted
date: 2026-09-27
decision: A cross-source pair whose record date, cash dividend, rights ratio and subscription price are identically equal, and whose 送股+转增 sums are exactly equal while the individual splits differ, books as one event under the label `cninfo+eastmoney(split_equiv)` carrying the official filer's rendering; the merge runs only after every arbitration channel (ADR-007, ADR-013) and the ADR-014 representation-floor merge have declined — never before them.
affects:
  - src/stock_quant/data_model/corporate_actions.py
  - src/stock_quant/data_pipeline.py
---

# 017 — Economically-equivalent 送股/转增 split merge

## Context

The 09-16 residual ledger registered 丙 class as a candidate rule, "经济等价即接受",
and left it unadopted. Its one measured instance is `002269.SZ` 2015-05-12:
CNINFO states 送 6.0 + 转 9.0 per ten, Eastmoney 送 5.0 + 转 10.0 — the same
15.0 total, the same record date, no cash — and `_same_facts` compares the
fields individually, so the pair quarantines as `cross_source_conflict`.

No existing channel can separate this shape, measured:

- **TDX declines.** Its xdxr row states 送转 as one combined number, so it
  corroborates *both* sides' totals and names neither (ADR-007 decision 5).
- **The price lane declines.** `1 + bonus + capitalization` is identical on
  both sides, so `expected_factor` is identical and `settle` returns `None`
  by its own separability rule (ADR-013 spec D5).
- **The floor merge declines.** 6.0 vs 5.0 is far outside float32's grid;
  ADR-014 merges renderings of one ratio, not different splits.

Meanwhile every downstream computation reads `1 + bonus + capitalization`
alone — `expected_factor`, and `adjusted_bar` through it. The split difference
is not evidence any consumer of this table reads.

## Decision

1. **The merge condition is exact, not a tolerance.** Record date, cash,
   rights and subscription price must be *identically* equal (the same
   exactness `_same_facts` applies), and the two sides'
   `bonus + capitalization` sums must be exactly equal in `Decimal`. Any other
   disagreement fails the merge and keeps its quarantine.
2. **Chain position: last before quarantine.** Exact agreement books first;
   then the arbiters (TDX, then price observation); then the ADR-014 floor
   merge; then this merge. A channel's verdict is never pre-empted — a pair
   the arbiter names books the arbiter's side and label even when the sums
   happen to be equal.
3. **The label tells the truth about what was corroborated.** The row books
   as `cninfo+eastmoney(split_equiv)`, not `cninfo+eastmoney`: the sides
   corroborated the economics, not each other's split.
4. **The official filer's rendering carries.** CNINFO's bonus/capitalization
   values book as-is; the merge does not average or prefer longer renderings,
   because unlike ADR-014 this is not one ratio in two renderings.
5. **甲 and 乙 classes cannot pass.** An omitted same-day event (甲) changes
   the combined cash or the combined total; a real amount disagreement (乙)
   changes cash. Both fail condition 1 and keep their quarantine.

## Consequences

- `002269.SZ`'s 2015-05-12 pair flips `UNTRUSTED / SOURCE_CONFLICT` to booked
  on the next rebuild; the acceptance `corporate_action_evidence` count drops
  by one. Published versions are immutable — the flip lands in a new dataset
  version, verified by rebuild rather than assumed here.
- Future 丙-shaped conflicts resolve automatically instead of waiting for an
  owner review.
- The split recorded in the row remains single-source (CNINFO's). Any future
  consumer that needs 送 vs 转 *as separate facts* must not read the
  `split_equiv` label as cross-confirmation of the split.

## Rejected alternatives

- **Relax `_same_facts`.** ADR-007 §4 keeps exact per-field equality as the
  basis on which 甲/乙/丁 are detected at all; folding equivalence into it
  would blur every class boundary. A separate, later-stage merge was chosen
  instead.
- **Prefer CNINFO without evidence.** Refuted as a general rule by the 丁
  class, where TDX sides with Eastmoney once (ADR-007).
- **A tolerance on the sum.** The economics either match exactly or they do
  not; a near-match is an 乙-class amount disagreement in disguise.

## Risk this decision accepts

- The booked split is corroborated by neither side. The label says so, and
  the economics — the only thing downstream reads — is corroborated by both.
- A same-day pair whose omitted event is bonus-only and whose remaining
  events sum identically merges as equivalent. That is correct on the
  economics: the booked cash and share total are corroborated by both sides,
  and only the split narrative differs.

## Evidence

- The registered candidate and its measured instance:
  `docs/operations/2026-09-16-corporate-action-residual-attribution.md` §2.
- Field-by-field reconciliation of the 25 conflicts (ADR-007 appendix);
  002269 is the pair no channel separated.
- This merge's guard tests: `tests/unit/test_corporate_action_normalize.py`
  (equivalent pair books, non-equivalent cash/record/total pairs quarantine,
  an arbiter's verdict outranks the merge) and the updated
  `tests/unit/test_tdx_arbiter.py::test_no_arbitration_when_the_total_matches_both_sides`.
