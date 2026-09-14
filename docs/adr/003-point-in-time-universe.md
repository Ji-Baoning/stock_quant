---
status: accepted
date: 2026-09-14
decision: Index membership is an immutable, evidence-bound fact table; a universe definition pins the table's content hash as universe_version, and a preflight gate fails the run before any factor is computed when the evidence is missing, ambiguous or cardinally wrong.
affects:
  - src/stock_quant/data_model/universe_membership.py
  - src/stock_quant/research/universe.py
  - src/stock_quant/research/runner.py
  - configs/universes/**
---

# 003 — Point-in-time index universe

## Context

Using today's index constituents over a historical window is one of the most
reliable ways to manufacture a fake edge: the members that survived to today are
disproportionately the ones that did well. The bias is invisible in the output —
it looks like alpha.

The correct input is the constituent set **as it was on each decision date**,
which requires dated evidence: official monthly snapshots, announcement dates,
and a rule for what happens between snapshots. That evidence is imperfect and
sometimes genuinely unavailable. The design question is therefore not "how do we
always get a universe" but "what do we do when the evidence is not good enough".

## Decision

Membership is treated as evidence, not as a symbol list:

- A membership **fact** is an immutable record carrying its window, its source
  snapshot and the snapshot/document SHA-256 it is attested against. Raw
  snapshots are stored under `data/raw/csi/` and never rewritten.
- The fact table is published **inside a dataset version**, so membership shares
  the dataset's content-addressing and immutability.
- A universe definition (`configs/universes/<id>.yml`) pins the fact table's
  content hash. That hash **is** `universe_version`, and it enters the experiment
  identity.
- `index_membership_evidence` runs as a **preflight**, after the dataset is
  pinned and before experiment identity or any factor work: it re-checks
  evidence binding, boundaries, coverage and cardinality against the pinned
  calendar and security master.
- On any failure the run **fails closed**. It does not fall back to the security
  master's full symbol list, does not widen the window, and there is **no bypass
  switch**. The rejection leaves a redacted preflight record naming stable error
  codes only.
- Correction is possible only as a new, evidence-backed fact version — never as
  an edit to an accepted one.

## Consequences

- A factor result can be traced to the exact constituent set used on each signal
  day, which is what makes the point-in-time claim checkable rather than
  asserted.
- The universe is only as good as its evidence. Where official evidence is
  unavailable, the honest outcome is no run at all — the project accepts being
  blocked over being quietly biased.
- A `custom_` universe id may relax the "exactly 300 members on every day"
  cardinality rule (monthly snapshots lag mid-month rebalances by up to about a
  month, and that lag is documented), but it does **not** relax any evidence
  requirement.
- Because `universe_version` is content-derived, re-importing the same evidence
  reproduces the same version and the same experiment identity.

## Rejected alternatives

- **Backfill today's constituents across the whole history.** The cheapest
  possible universe and the precise source of survivorship bias; rejected
  outright.
- **Use `configs/universe.yml`'s fixed engineering sample for formal research.**
  It is a fixed 30-symbol boundary sample with no dated evidence at all — fine
  for engineering diagnostics, meaningless as a point-in-time universe.
- **Add an override flag to proceed with partial evidence.** Any such flag would
  be used exactly when the evidence is worst, which is when the bias is largest.
- **Interpolate or infer membership between snapshots instead of failing.** Would
  fabricate constituent facts and make the evidence hash attest to something the
  source never said.
- **Treat a constituent leaving the index as a forced sale.** Membership governs
  eligibility at signal time; it is not an execution instruction.

## Evidence

`tests/unit/test_index_membership_checks.py`,
`tests/unit/test_csi300_universe_build.py`,
`tests/unit/test_raw_snapshot_binding.py`,
`tests/integration/test_research_runner.py`. Normative statement:
`docs/architecture/invariants.md` §4. Operational chain: `RUNBOOK.md` stage 5.
