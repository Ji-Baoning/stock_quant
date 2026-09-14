# Invariants

## When to read this

Read this before any change that could weaken a boundary — publication,
acceptance, point-in-time evidence, research freezing, credentials, or root
resolution. These are the constraints that outrank convenience: if a change
makes an invariant harder to satisfy, the change is wrong, not the invariant.

Each invariant names where it is enforced. Prefer that enforcement point over
restating the rule in new code.

## 1. No look-ahead

- Signals, universe membership, eligibility and calibration **MUST NOT** use
  information that was not available at the decision time. Financial-statement
  data enters at its disclosure time, not its report period.
- **MUST NOT** follow `CURRENT` after a run has pinned its dataset version. A
  version is resolved exactly once, at pin time.
- **MUST NOT** extend a warmup window forwards or use post-decision bars to
  stabilize an earlier factor value.
- Enforced in `research/runner.py` (pin-then-read), `research/universe.py`,
  `factors/`. Verified by `tests/integration/test_factor_no_lookahead.py`.

## 2. Immutable, content-addressed publication

- A published dataset version, acceptance record, experiment, challenge
  declaration and fold schedule **MUST** be immutable. A published artefact is
  never edited in place; a correction is a new version beside it.
- Artefact identity **MUST** be derived from content: the frozen spec plus the
  frozen research snapshots plus the code commit. Runtime metadata (run id,
  timestamps, host, pid, worker count, output paths) **MUST NOT** enter an
  identity hash.
- **MUST NOT** permit a published version's directory to be replaced, deleted
  or rewritten by a later operation.
- Enforced in `data_model/dataset.py`, `research/registry.py`,
  `research/walk_forward/schedule.py`. Verified by
  `tests/integration/test_dataset_publish.py`,
  `tests/integration/test_experiment_registry.py`.

## 3. Failed and rejected outcomes are preserved

- A blocked, failed or rejected operation **MUST** leave its evidence behind.
  A rejected acceptance record is persisted *before* the process exits
  non-zero; a FAILED run manifest, a failed fold and a rejected candidate are
  retained, never cleaned up to make a later run look clean.
- **MUST NOT** delete, overwrite or hide a failed fold, a rejection reason, or
  a suppressed/rejected portfolio decision.
- **MUST NOT** weaken a validation, acceptance or publication gate so an
  operation can finish. A gate that blocks is reporting a fact.
- Enforced in `research/acceptance/service.py`,
  `research/walk_forward/runner.py`, `portfolio/`. Verified by
  `tests/integration/test_acceptance_cli.py`.

## 4. Formal research requires accepted, point-in-time evidence

- A formal `research run` **MUST** resolve and re-verify a valid acceptance
  record for its pinned dataset version, and **MUST** re-check the binding
  hash on every run rather than trusting a cached verdict.
- A run **MUST NOT** compute a single factor before the universe preflight and
  the acceptance gate have both passed.
- **MUST NOT** fall back to the security master's full symbol list, or to an
  unverified membership table, when point-in-time membership evidence is
  missing, ambiguous or cardinally wrong. Failure is the intended outcome.
- **MUST NOT** represent an engineering diagnostic as formal research. A run
  without an acceptance record is UNTRUSTED by construction and can never
  publish an ACCEPTED experiment.
- Enforced in `research/runner.py`, `research/acceptance/`. Verified by
  `tests/integration/test_research_runner.py`,
  `tests/integration/test_walk_forward_runner.py`.

## 5. Price basis

- `adjusted_bar` is derived total-return **evidence**, valid only with
  `adjustment=internal_total_return_v1`. It **MUST NOT** be used for order
  pricing, execution, price-limit checks or valuation.
- Execution and valuation **MUST** use clean unadjusted daily-bar prices.
- An untrusted corporate-action transition invalidates every momentum window
  crossing it. The system **MUST NOT** silently substitute an unadjusted close.
- Enforced in `data_model/adjusted_bar.py`, `research/trust.py`,
  `backtest/`. Verified by `tests/unit/test_adjusted_bar.py`,
  `tests/unit/test_research_trust.py`.

## 6. Frozen identity is not retuned

- Rule parameters and the resulting `portfolio_rule_version` **MUST** stay tied
  to the frozen experiment identity. **MUST NOT** tune them after examining
  held-out results, and **MUST NOT** edit a frozen fold schedule or a
  pre-registered challenge declaration.
- A holdout consumption **MUST** be atomic and permanent: consumed stays
  consumed regardless of how the comparison ends.
- Enforced in `portfolio/buffered_risk_weight.py`,
  `research/strategy_challenge/`. Verified by
  `tests/integration/test_holdout_registry.py`.

## 7. Credentials and secrets

- Credentials and supplier tokens **MUST NOT** appear in code, committed
  configuration, fixtures, logs, reports or error messages. Tokens are read
  from the environment (for example `TUSHARE_TOKEN`) only.
- Logs and error text **MUST** pass through the redaction path before being
  written or printed.
- Enforced in `config.py`, `logging.py`, `research/runner.py` (`redact_text`).
  Verified by `tests/unit/test_logging.py`,
  `tests/integration/test_project_root_cli.py`.

## 8. Explicit project root, no fallback

- A project root **MUST** be resolved from an explicit `--root` (or `.` when
  omitted) and validated to carry `configs/project.yml`, `configs/sources.yml`
  and `configs/costs.yml`.
- **MUST NOT** fall back to the repository root, to `templates/project-config/`
  or to the current working directory when the given root is invalid. A
  mis-specified root **MUST** fail loudly rather than read the wrong data.
- Enforced in `project_root.py`. Verified by `tests/unit/test_project_root.py`,
  `tests/integration/test_project_root_cli.py`.

## 9. Repository hygiene

- **MUST NOT** commit credentials, market data, published datasets, experiment
  artefacts or operator secrets.
- **MUST NOT** edit source or configuration while a documented update or
  publication operation is running.
- **MUST NOT** overwrite, revert, stage, reformat or delete an unrelated change
  in progress.

## Authority boundary

Stated here as prohibitions; the mechanics are in `data-flow.md`, the rationale
and rejected alternatives in `docs/adr/`, and the operator procedure in
`RUNBOOK.md`. If a test named here no longer exists, treat this file as stale
and fix it in the same change that removed or renamed the test.
