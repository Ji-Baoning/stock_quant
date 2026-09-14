---
status: accepted
date: 2026-09-14
decision: Out-of-sample stability is judged on a fold schedule written and hashed before any backtest runs, with per-fold outcomes bound to that hash, failed folds retained forever, and a versioned verdict that never downgrades an integrity failure to an inconclusive result.
affects:
  - src/stock_quant/research/walk_forward/**
  - src/stock_quant/research/runner.py
---

# 004 — Pre-frozen walk-forward OOS

## Context

A single out-of-sample window answers one question about one market regime. The
temptation afterwards is to move the boundary, lengthen the warmup, or drop the
fold that behaved strangely — at which point the "out-of-sample" window has
become a training set and the result means nothing.

The stability question ("does this hold up across periods?") therefore needs a
schedule that cannot respond to its own results, and a verdict that is
computed from the schedule rather than chosen after seeing it.

A second, subtler problem: metrics like a global drawdown or Calmar computed
across fold boundaries are not meaningful, because folds are separate accounts
with separate initial equity.

## Decision

`execution_pipeline: walk_forward_oos_v1`:

- **Schedule first.** The requested OOS range is cut into non-overlapping
  annual folds (1 January anchored). `fold_schedule.json` is written and hashed
  **before any backtest runs** and is never modified afterwards.
- **Bounded warmup.** Each fold gets at least three whole calendar years and at
  least 756 confirmed trading days of history (extending further back, bounded,
  when the calendar is sparse), plus 60 stable history days before its first OOS
  day. The warmup feeds factor history only; it is never an evaluation window.
- **Bound outcomes.** Results go to `fold_outcomes.json`, bound to the schedule
  hash. A failed fold is retained permanently; nothing is pruned to tidy a run.
- **Honest preflight.** A fold whose sparse calendar cannot meet the warmup
  floor is reported as insufficient and fails its own preflight rather than
  being silently shortened.
- **Versioned, hashed verdict** (`stability-v1`): any fold or system integrity
  failure → FAILED, with the conclusion permanently `null` — never downgraded to
  INCONCLUSIVE. Fewer than 5 executed folds, or any legitimate market-level
  skip → COMPLETED/INCONCLUSIVE (valid but insufficient evidence). Otherwise each
  pre-declared cost scenario is judged independently (share of positive-return
  folds ≥ 60% and worst fold annual return > −10%), and STABLE is the
  conjunction across all scenarios.
- **Metric discipline.** Exactly one portfolio return per confirmed trading day
  (the fold's first day uses `initial_equity` as its predecessor); per-fold max
  drawdown uses only that fold's `net_equity_after_cost` mark-to-market. **Global
  cross-fold drawdown and Calmar are forbidden** and are not computed.
- Cost scenarios replay the same paths without changing the fill set;
  `turnover-v1` documents its numerator and denominator.

## Consequences

- The schedule is a pre-registration: any later change is a new experiment, not
  an adjustment to this one.
- Fold count is a real constraint. With annual folds, a 1–12 month request
  cannot reach 5 folds, so the verdict is legitimately INCONCLUSIVE rather than
  forced into a stable/unstable claim.
- Retaining failed folds keeps the failure visible in every later reading of the
  experiment.
- The pipeline runs offline and is fully tested, but a *stability conclusion*
  about a strategy still requires a real accepted dataset; the mechanism being
  complete is not evidence about any strategy.

## Rejected alternatives

- **A single train/test split.** One regime, one answer, and a boundary that is
  trivially movable after the fact.
- **Choosing fold boundaries or warmup after seeing fold results.** Destroys the
  pre-registration property while still being called walk-forward.
- **Dropping folds that fail for internal reasons.** Turns an integrity failure
  into a smaller, better-looking sample.
- **Reporting global cross-fold drawdown / Calmar.** Arithmetically meaningless
  across independent fold accounts; rejected as a metric category, not merely
  deprecated.
- **Collapsing FAILED into INCONCLUSIVE.** Would let a broken run be reported
  with the same label as an honest underpowered one.

## Evidence

`tests/integration/test_walk_forward_runner.py`,
`tests/unit/test_walk_forward_schedule.py`, `tests/unit/test_walk_forward_metrics.py`,
`tests/unit/test_walk_forward_policy.py`, `tests/unit/test_walk_forward_snapshots.py`,
`tests/unit/test_walk_forward_evaluation.py`. Normative statement:
`docs/architecture/invariants.md` §6. Operator procedure: `RUNBOOK.md` stage 6.
