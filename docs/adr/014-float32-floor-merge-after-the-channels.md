---
status: accepted
date: 2026-09-22
decision: Same-fact comparison keeps exact equality, and a cross-source pair whose every ratio field agrees to within one float32 ULP of the other is one stated ratio in two renderings, booked as a single cninfo+eastmoney row carrying whichever rendering states more digits — but only after every evidence channel has declined to name a side, so a channel's verdict is never pre-empted.
affects:
  - src/stock_quant/data_model/corporate_actions.py
---

# 014 — The float32 floor merges, but only after the channels

## Context

ADR-007 detects a cross-source conflict by comparing supplier terms for one
`(symbol, ex_date)`: what agree exactly book as `cninfo+eastmoney`, what disagree
go to a third-party channel, and what the channel cannot adjudicate stays
quarantined for an owner. Its §4 rejects comparing by numeric tolerance, because
a width wide enough to admit one class of disagreement admits the others.

The ADR-013 design work revisited that, observing that supplier ratios reach us
through float32 while the comparison is decimal, so two sides *stating one
ratio* can land on adjacent float32 values. It specified a relative tolerance of
`2 ** -23` (≈ 1.19e-7) inside `_same_facts` — one ULP, and nothing wider.

That tolerance was implemented, and it merged exactly two published records:
`300124.SZ` 2016-05-18 (转增 9.998780 against 9.998781) and `301308.SZ`
2026-06-02 (cash 9.90744266 against 9.907442). Both are the 丁-class pairs
ADR-007 uses as its own evidence that TDX is not an echo of either source, and
both are exactly one float32 ULP apart.

Two things were wrong with where the tolerance sat, not with its width:

1. **It ran before the channel.** `_same_facts` is asked first, so a merged pair
   never reached the arbiter at all. The 丁 class stopped being arbitrated and
   became invisible to the very evidence ADR-007 built for it.
2. **It always kept CNINFO's value.** The merge path booked `cn_event`, so on
   `300124.SZ` the row carried CNINFO's 6-digit 9.998780 even though Eastmoney
   stated 7 digits. ADR-007 already rejected "prefer CNINFO" as a rule.

## Decision

**The channel is asked first.** `normalize_corporate_actions` consults the
arbiter before considering any tolerance, so an adjudicating channel always
books `<side>+<arbiter>` with that side's value. `_same_facts` is untouched and
stays exact: it is the basis on which the corroborated classes are detected, and
widening it was ADR-007's stated objection.

**Then the floor merges.** Only when no arbiter names a side does
`_merged_within_representation_floor` get a turn. It returns an event only when
*every* ratio field is either identical or within one ULP at its own magnitude;
one field further apart is a real disagreement and keeps its quarantine, so this
cannot widen into a general tolerance. The record date must match exactly.

**The merged row carries the longer rendering.** Digits are counted on the
`Decimal` the supplier stated (`len(value.as_tuple().digits)`, exact for these
values because they parse from `str(float)`), and each ratio field is taken from
whichever side states more of them. A tie keeps CNINFO's side — the same party
`_same_facts` favours by treating the pair as one fact at all.

**The merged row books `_BOTH_SOURCES`.** The two sides state one ratio, so the
choice between renderings is not a doubt about the event. This is ADR-007 §6's
`<origin>+<authority>` vocabulary unchanged: a value, not a schema change.

## Consequences

- **A channel can change the booked value, and on both published records it
  does.** With TDX the pairs book `cninfo+tdx` / `eastmoney+tdx` at terms whose
  float32 lands on TDX's own value; with no channel they book
  `cninfo+eastmoney` at the longer rendering. On `300124.SZ` those differ
  (9.998780 against 9.998781) and on `301308.SZ` they differ (9.907442 against
  9.90744266). The digit rule is therefore a *fallback for absent evidence*, not
  a second opinion about it, and a dataset rebuilt with a channel enabled can
  legitimately differ in those digits from one rebuilt without.
- **The shipped default stops spending operator review on these pairs.** With
  no channel configured, the 丁 class previously reached quarantine.
- **A merged row is indistinguishable from an exactly-agreeing row** by its
  `confirmed_by` alone. The disagreement is recoverable from the two suppliers'
  stored raw snapshots, which the content-addressed store already keeps, but it
  is not surfaced in the published row.
- The tolerance lives in `corporate_actions.py`, one call site, after the
  arbiter — not in `_same_facts`, which keeps its single meaning.

## Rejected alternatives

- **The tolerance inside `_same_facts`** (spec D4 as written). It pre-empts the
  arbiter, which is the defect that made this record necessary.
- **Always keeping CNINFO's value.** Wrong on `300124.SZ`, where CNINFO states
  6 digits and Eastmoney 7. ADR-007 rejects the rule by name.
- **A tolerance wider than one ULP.** `600989.SH` 2025-05-13 differs by ~1e-5,
  two orders above float32's grid, and stays quarantined: merging it would be
  the machine deciding a difference does not matter, which is the judgement this
  project keeps for its owner.
- **Comparing by digit count without the channel.** Would book both records
  against a live channel's verdict — correct in neither direction.

## Risk this decision accepts

- **The digit rule is a proxy for precision, not a measurement of it.** More
  digits is taken to mean more of the number stated. `300124.SZ` and
  `301308.SZ` are the only two records where it has been exercised, and on both
  of them it disagrees with a channel that can do better; the fallback is
  justified by the absence of evidence, not by its own accuracy.
- **A merged pair is silently one row.** Suppressing the second rendering
  removes a human's chance to notice it, which is why the raw snapshots of both
  suppliers remain the audit path.

## Evidence

The two published 丁-class records, arithmetically (per-ten scale; one ULP there
is 9.536743e-7):

| Conflict | CNINFO | Eastmoney | Digits C/E | No channel books | TDX books |
| --- | --- | --- | --- | --- | --- |
| `300124.SZ` 2016-05-18 转增 | 9.998780 | 9.998781 (0.9998781×10) | 6 / 7 | Eastmoney `9.998781` | CNINFO `cninfo+tdx` |
| `301308.SZ` 2026-06-02 cash | 9.90744266 | 9.907442 (0.9907442×10) | 9 / 7 | CNINFO `9.90744266` | Eastmoney `eastmoney+tdx` |

Both pairs sit exactly one float32 ULP apart, which is why the width is one ULP:
at `300124.SZ`'s magnitude, values one ULP apart re-emit as decimals differing in
the seventh significant digit.

Measured before this decision, on the implementation that put the tolerance in
`_same_facts`: `tests/unit/test_tdx_arbiter.py` fell from 31 passed to 29, both
failures being the 丁-class records booking `cninfo+eastmoney` instead of being
arbitrated. Forcing the tolerance to zero restored 31/31, which is how the
tolerance was identified as the sole cause rather than a coincidence.

The arity is unchanged by this record: ADR-007's reconciliation of all 25
conflicts against TDX (23 CNINFO, 1 Eastmoney, 1 unresolvable) still holds when
a channel is configured, because the channel is still asked first.
