---
status: accepted
date: 2026-09-14
decision: Formal research is gated by an immutable, content-addressed acceptance record for one pinned dataset version, signed off by an operator; a rejected record is persisted before the command exits non-zero.
affects:
  - src/stock_quant/research/acceptance/**
  - src/stock_quant/research/runner.py
---

# 002 — Real-data acceptance

## Context

Automated checks can prove that a supply is internally consistent. They cannot
prove that it is the *right* supply: that a cross-source price sample really
was inspected, that a supplier's silent coverage gap was noticed, that a
snapshot hash matches the official document it claims to come from. Those are
human judgements over evidence.

Without a place to record that judgement, the project had only two states —
"automatic checks passed" and "no idea" — and a formal run could not distinguish
them. The failure mode is quiet: a run completes on supply nobody ever verified
and publishes with the same authority as a verified one.

## Decision

Introduce a **real-data acceptance** record (`policy_version=real-data-v1`):

- The record is a content-addressed, immutable verdict bound to **one** pinned
  dataset version and to the sha256 of every evidence file it cites. Evidence
  lives inside the project (project-relative `reference` + `sha256`); `external`
  references are never fetched at publish time — their hash pins stored text.
- `data acceptance prepare` generates a checklist where every manual row starts
  as an explicit **FAIL** that the operator must turn into PASS with evidence.
  Nothing is assumed verified, and automated verdicts are freshly recomputed.
- `data acceptance publish` re-runs every automated check and re-hashes all bound
  evidence. All PASS → ACCEPTED; anything else → the **REJECTED record is
  persisted before the command prints reasons and exits non-zero**, so a refusal
  is auditable rather than a silent no-op.
- A formal `research run` resolves `CURRENT_ACCEPTED` to the latest valid ACCEPTED
  record for its pinned dataset and **re-verifies the binding hash on every run**.
  A run with no acceptance record is UNTRUSTED by construction.
- Acceptance is a *data supply* gate, not a strategy verdict. It says nothing
  about profitability and must never be presented as one.

## Consequences

- The operator is on the critical path for every formal run. This is the intended
  cost: it is the only step that cannot be automated away.
- The gate is implemented and offline-tested, but a formal walk-forward run still
  waits on a real acceptance being executed on real supply. "Mechanism ready" is
  not "acceptance done", and the project records the difference explicitly.
- An acceptance record is bound to a dataset version, so publishing a corrected
  dataset invalidates the acceptance for that purpose and requires a new one. It
  never silently transfers.
- Engineering diagnostics remain available to unblock offline work, and are
  marked UNTRUSTED end-to-end so a diagnostic can never be mistaken for an
  accepted result.

## Rejected alternatives

- **Treat a passing automated check suite as acceptance.** Cheapest option, and
  it silently equates "internally consistent" with "verified supply" — the exact
  conflation this ADR exists to prevent.
- **Let the operator mark the whole checklist PASS without per-row evidence.**
  Produces a signature with nothing behind it; the per-row evidence hash is what
  makes the verdict falsifiable later.
- **Reuse one acceptance across dataset versions.** A restated input could pass
  under an acceptance issued for different content.
- **Block diagnostics too.** Would stall all offline engineering work behind a
  human judgement that is not needed for a diagnostic — hence the separate,
  clearly-labelled UNTRUSTED path instead.

## Evidence

`tests/integration/test_acceptance_cli.py`,
`tests/integration/test_acceptance_checks.py`,
`tests/unit/test_acceptance_service.py`, `tests/unit/test_acceptance_models.py`.
Normative statement: `docs/architecture/invariants.md` §4. Operator procedure:
`RUNBOOK.md` stage 5b.
