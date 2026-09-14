---
status: accepted
date: 2026-09-15
decision: A quarantined corporate action counts against a symbol/window only when one of its known dates falls inside that window; a pre-window announcement on an implemented record is excluded, which supersedes the filter's "keep an implemented record with a missing ex-date" stance for that case.
affects:
  - src/stock_quant/data_model/corporate_actions.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_quality/models.py
---

# 006 — Corporate-action window scope

## Context

`_coverage_verdict` writes one coverage row per `(symbol, window)` and answers
that row's question with the symbol's *whole* quarantine history. The two
scopes disagree: a 1998 plan the supplier reports without any date is not an
event in 2015-2026, yet it marked that entire window `UNTRUSTED`.

The repository already disagreed with itself about this. The break layer,
`adjusted_bar._merge_quarantine_breaks`, skips a row whose `ex_date` is `None`
(`continue`) — a dateless record is not a transition and cannot break any
window. The coverage layer meanwhile painted the whole `[start, end]` as a
break. Aligning the two is a consistency fix, not a relaxation: the same
evidence now decides both layers.

Measured on the published dataset (`CURRENT = 1d6e43b4…`, 2026-09-14): 71
symbols read `UNTRUSTED`, and 46 of them are poisoned *only* by records whose
every known date lies before 2015. 45 of those 46 hold dated in-window actions
in the published `corporate_action` table, i.e. the supplier does report their
in-window implementations as separate, dated rows.

## Decision

`quarantine_row_out_of_window_reason(row, start, end)` decides, in order, on
the first date the row actually knows:

1. **`ex_date` known** — inside the window it counts, outside it does not.
   A derivation: the ex-date *is* the transition.
2. **`record_date` known** (no ex-date) — the same. Also a derivation on this
   data, because a completed settlement's ex-date is never earlier than its
   record date (measured: 6,872 / 6,872 accepted facts carry both dates, lag
   1-13 days, zero negatives), so a record date outside the window places the
   ex-date outside it too.
3. **Neither, but an `announcement_date` before `start` on a record whose
   `status` is `implemented`** — excluded. This *is* a conditional relaxation
   and it supersedes the stance written in
   `filter_corporate_actions_to_window`'s docstring ("an implemented record
   with a missing ex-date remains for reconciliation to flag as a genuine
   defect"). An announcement *after* the window does **not** qualify: the
   event it announces may still settle inside the window.
4. **No known date at all** — kept. Fail closed.

The filter narrows only the coverage verdict's input. The published
`corporate_action_quarantine` table still carries every row, and each excluded
row leaves an INFO `quarantine_out_of_window` issue naming its symbol, window
and branch.

## Consequences

- A window's coverage verdict now answers a question about that window, and the
  break layer and the coverage layer agree on what a dateless record means.
- The exclusion is auditable but **quiet**: the INFO issue is not in
  `PUBLICATION_BLOCKING_CODES`, so a suppressed record does not block a publish.
  Branch 3's risk below is therefore the one thing about this decision that a
  reader must not miss.
- Symbols that hold no accepted in-window fact still read `FACTS_INCOMPLETE`
  after the exclusion — 2 of the 46 flip only as far as that, which is the
  correct outcome, not a regression.

## Rejected alternatives

- **Mark the window `UNTRUSTED` for any historical quarantine row.** The
  current behaviour. It makes 46 symbols permanently unverifiable for reasons
  that have nothing to do with the window, and it contradicts the break layer.
- **Exclude any record whose *every* date lies outside the window.** Broader
  than the decision and wrong in one direction: a row announced after the
  window can still settle inside it, and excluding it would hide a transition
  inside the window.
- **Exclude branch 3 silently (no ADR, no INFO issue).** It would be the one
  place in this change where a gate-visible outcome is dropped without a
  trace, which invariant 3 forbids.
- **Resolve the stale records at the source instead.** Correct in principle and
  out of scope: it needs supplier-side facts or a signed review, which is data
  work, not a code change.

## Risk this decision accepts

Branch 3 is the only place this change *widens* what may pass. If a stale plan
really did settle inside the window while the supplier's dated record for it is
missing, excluding the dateless row leaves the series clean and **unmarked** —
exactly the silent substitution invariant 5 forbids. Two things bound the risk
without eliminating it: a dateless row can never enter the adjustment recursion
(`_standardize_source` keys candidates on `(symbol, ex_date)`), so it cannot
mask an in-window transition it is not itself part of; and 45 of the 46 symbols
carry dated in-window actions in the published table, so the supplier reports
their in-window settlements separately. The residual risk is recorded here
rather than assumed away.

## Evidence

`tests/unit/test_corporate_action_normalize.py`
(`test_quarantine_row_out_of_window_reason_decides_by_first_known_date`,
`test_quarantine_row_out_of_window_reason_reads_pandas_date_cells`),
`tests/integration/test_data_pipeline.py`
(`test_update_ignores_quarantine_rows_whose_dates_predate_the_window`).
Measured footing and the 46 / 25 split:
`docs/operations/2026-09-14-blocking-gap-root-cause.md`. Design:
`docs/superpowers/specs/2026-09-15-data-layer-residual-defects-design.md` §5.
