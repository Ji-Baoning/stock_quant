---
status: accepted
date: 2026-10-02
decision: A whole-window empty per-symbol daily response is a new, bounded evidence class -- when the symbol is an active member of the current universe, is not delisted, the carried baseline covers it to the boundary with a suspension tail (its last bar before the fetch window is a `tushare_suspend` bar on the last open day before the window's first open day), and the round's per-symbol `daily` response is truly empty (zero rows, no columns), the window's open days are materialized as carry-forward bars -- parity price at the boundary close, zero volume, the existing `tushare_suspend` label -- capped at `MAX_CARRY_FORWARD_DAYS = 10` open days. Any unmet condition or an over-bound run keeps the pre-existing fail-closed FATAL, and the adapter's empty-response ContractError contract is unchanged; ADR-020's two records stay accepted -- this ruling fills the space they reserved for the owner, it does not overturn them.
affects:
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_model/suspensions.py
  - src/stock_quant/data_sources/base.py
---

# 023 — Suspension carry-forward for proved tails

## Context

The first real `basic_factor` window (2026-09-25..2026-09-30) never published:
`601059.SH`'s per-symbol `daily` range request returned a *truly empty*
response -- zero rows, no columns (dated evidence:
`docs/operations/2026-10-02-basic-factor-first-real-window.md`, "第一轮真实
update"). `validate_supplier_frame` maps that shape to
`ContractError("supplier returned an empty response")`
(`data_sources/base.py`), `_dispatch` converts the required failure into a
FATAL `source_fetch_failed`, and the publication gate refused the round. The
symbol is a current CSI300 constituent whose baseline (through 2026-09-24)
carries `source="tushare_suspend"` parity zero-volume bars on 09-22/23/24 --
a halt in progress when coverage ended -- and the relay itself was healthy:
every symbol dispatched before it had answered.

The failure died at the adapter boundary, so the suspension materialization
that would have proved the window (ADR-020's seam, fed by
`_materialize_suspensions` from raw `pre_close` chains) was never reached: a
whole-window empty response has no resumption row, no `pre_close`, and no
chain to read. The response *shape change* is the point -- earlier rounds saw
zero-volume rows for this symbol's suspended sessions, this round saw
nothing at all -- so the existing evidence classes cannot classify it.

ADR-020 (`020-suspension-proof-grid-is-the-fetch-window-and-its-seam` and
`020-batched-validation-channel`) pinned the current behaviour as accepted
fail-closed: "no new tolerance, no new evidence class", one symbol's empty
response is a ContractError, and carry-forward-style rules were explicitly
**reserved for the owner's decision** (its rejected-alternatives section,
and its interior-gap consequence, which became the 2026-09-27 owner ruling
codified as `MAX_INTERIOR_PROOF_RUN_DAYS`). The owner has now ruled
(2026-10-02): a *bounded* carry-forward for the proved-tail shape is
adopted. This record decides the space that reservation left open; ADR-020's
own conclusions stand and are not superseded.

## Decision

1. **The evidence class.** A per-symbol whole-window empty `daily` response
   is proven -- not merely tolerated -- when **all** of the following hold
   for the symbol this round:
   - (a) the symbol is an *active* member of the current universe (a
     `status="active"` fact in the carried `universe_membership` table; a
     baseline without the table fails closed);
   - (b) the symbol is not delisted inside or before the window (the
     security master's `delist_date` is empty or strictly after the window
     end);
   - (c) the carried baseline covers it to the boundary with a suspension
     tail: the symbol's last baseline bar strictly before the fetch window's
     first open day sits on that last open day itself (no uncovered open day
     between the baseline and the window) and is itself a
     `tushare_suspend` bar (zero volume, carried reference price) -- the
     halt was in progress when coverage ended;
   - (d) the round's per-symbol `daily` response is truly empty (the
     empty-response ContractError itself; both empty shapes -- zero rows
     with no columns, and a rowless frame carrying columns -- raise the same
     ContractError at the adapter boundary and are indistinguishable at the
     seam, so the evidence class is keyed on the four proof conditions, not
     on the response shape).
2. **The materialization.** When (a)-(d) hold, each open day of the fetch
   window is materialized as a carry-forward bar in the same canonical shape
   the suspension proofs emit: OHLC at the boundary bar's close (parity),
   volume 0, amount 0, `adjustment="unadjusted"`, labelled
   `source="tushare_suspend"` (the existing `SUSPENSION_SOURCE`). The label
   is **reused, not new**: the only consumers of the column are the
   provenance allowlist (`data_quality/raw_checks.py` `KNOWN_SUPPLIERS`) and
   the momentum factor's source-family mapping
   (`factors/momentum.py` `_SOURCE_FAMILY`, `tushare_suspend → tushare`) --
   both already accept the label, and a new one would fail provenance checks
   and orphan the bars from the suspension family they belong to. The rows
   carry an INFO `suspension_row` issue with `kind="carry_forward"` so the
   report distinguishes them from chain-proved bars.
3. **The bound.** `MAX_CARRY_FORWARD_DAYS = 10` open days (module constant
   beside `MAX_INTERIOR_PROOF_RUN_DAYS`, same precedent): a window longer
   than ten open days is never materialized. The incident shape is three
   open days; ten bounds the fabrication to two trading weeks -- past that,
   a silent relay error over a resumption would silently flat-line a
   trading symbol for too long before any human looked. An over-bound run
   keeps the fail-closed FATAL.
4. **Fail-closed is not weakened.** A symbol meeting fewer than all four
   conditions -- no active membership fact, a delist at or before the window
   end, no suspension tail or an unconnected one, an over-bound window --
   gets exactly the pre-existing FATAL `source_fetch_failed` for the empty
   response. The adapter contract is unchanged: an empty response is still a
   ContractError at every supplier boundary
   (`test_adapters_map_empty_response_to_contract_error` stands untouched);
   only the *pipeline's* required-lane handling of that one error shape
   branches, and only when the caller opted in. Symbols whose responses
   carry rows keep their byte-identical path.
5. **Acceptance semantics.** The materialized rows are evidenced data: their
   proof chain is the baseline's own suspension tail plus the boundary, so
   `date_window_completeness` does not go red on them and the round
   publishes. A window whose proof is *not* available -- no suspension tail
   -- stays the acceptance gate's honest failure; nothing here invents rows
   for it.
6. **ADR-020's disposition.** Both 020 records keep `status: accepted`
   unchanged: this ruling adopts what 020 deliberately reserved (its
   "no new tolerance, no new evidence class" held *until an owner decision
   adds* a mechanism; the interior-gap ruling of 2026-09-27 is the shape
   precedent). The `suspension_rows` chain rule and
   `test_tail_run_without_anchor_is_not_materialized` are untouched -- that
   test's tail run has no suspension bar and no baseline tail, so it stays
   green under this ADR's semantics: carry-forward needs the baseline tail,
   a proof the chain rule never had.

## Consequences

- The 2026-09-25..2026-09-30 window shape now publishes: `601059.SH`'s
  empty response is proved by its baseline tail and materialized as three
  parity zero-volume bars, instead of blocking the round.
- A supplier that silently drops a *trading* symbol's whole window is still
  caught: no suspension tail, no membership fact, or a delist -- each keeps
  the FATAL. The residual risk is narrow by construction: a halt *plus* an
  empty relay answer *plus* a resumption inside ten open days whose first
  traded close differs from the halt price would be carried flat until the
  next round's fresh rows replace the window; the INFO issue names every
  carry-forward run for exactly that review.
- A whole-window empty response over a window with no open days remains the
  pre-existing fail-closed behaviour (a separate, older open item); this ADR
  does not reach it -- with no open day there is no boundary and no tail to
  prove.
- New integration guard tests pin the four conditions: the incident
  blueprint publishes, and the over-bound / no-tail / delisted shapes stay
  FATAL.

## Rejected alternatives

- **A new source label** (e.g. `carry_forward`): breaks the provenance
  allowlist, splits the suspension family in the momentum factor's mapping,
  and asserts a distinction (proved vs carried) the rows themselves cannot
  re-derive. The INFO issue's `kind` already carries the distinction.
- **Materializing from the boundary forward even when the baseline's last
  bar is not on the last open day before the window** (a "seam from wherever
  the baseline ends" rule): an unconnected tail hides an unknown span the
  round never fetched; fail closed.
- **Raising the bound or leaving it unbounded**: a halt is finite; a relay
  fault is not self-announcing. Ten open days keeps the unreviewed
  fabrication inside two weeks.
- **Suppressing the adapter's ContractError** (`allow_empty=True` on the
  lane): that reclassifies every empty response as acceptable evidence and
  rewires the pinned adapter contract; the ruling needs the opposite -- the
  error stays loud, and the *pipeline* decides what the proved subset means.
