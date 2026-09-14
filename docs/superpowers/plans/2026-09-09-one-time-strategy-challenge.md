# One-Time Strategy Challenge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an immutable, predeclared, one-time holdout challenge that compares the buffered risk-weighted momentum strategy with its equal-weight baseline on identical Walk-Forward inputs and returns an auditable `PROMOTED`, `REJECTED`, `INCONCLUSIVE_RESEARCH_ONLY`, or terminal `FAILED` result.

**Architecture:** A strict challenge declaration binds both experiments, comparison policy, fold schedule, and complete universe identity before challenger results are read. An atomic registry consumes the strategy-family/calendar holdout once, while pure pairing/evaluation code compares every locked fold and cost scenario. The orchestrator publishes immutable comparison artifacts and HTML only after identity, registry, artifact, and pairing checks pass.

**Tech Stack:** Python 3.10, Pydantic 2, pandas 2+, PyArrow 14+, Jinja2, Typer, pytest, SHA-256, atomic filesystem rename/lock files

**Spec:** `docs/superpowers/specs/2026-09-09-buffered-risk-weighted-momentum-design.md`

## Global Constraints

- Prerequisite: execute only after the Walk-Forward plan and `2026-09-09-buffered-risk-weighted-momentum.md` are fully integrated and verified.
- The declaration is durably published before any challenger result artifact is opened.
- The holdout consumption key remains exactly `strategy_family + fold_schedule_hash`; universe version is recorded and hashed but never creates another unconsumed slot for the same history.
- `universe_definition` contains exactly `universe_id`, `universe_version`, `membership_table_sha256`, and `evidence_summary_sha256`; every field participates in `challenge_id` and idempotent recovery.
- Baseline and challenger must match dataset/data-environment snapshot, universe definition, fold schedule, initial equity, factor signal, rebalance frequency, and ordered cost scenarios. Only portfolio construction rules may differ.
- Comparison pairs are unique by `fold_id + cost_scenario`; missing or duplicate pairs make the challenge FAILED.
- At least five unconsumed executed folds and no legal market-wide skip are required for promotion/rejection evidence. Otherwise the valid result is `INCONCLUSIVE_RESEARCH_ONLY`.
- Every declared cost scenario must pass every threshold for `PROMOTED`; no preferred scenario may be selected afterward.
- Consumed records are never deleted or overwritten. Crash, FAILED, REJECTED, and INCONCLUSIVE runs still consume the holdout.
- Default tests are offline and deterministic. Use TDD and task-scoped commits from an isolated worktree.

---

### Task 1: Challenge declaration, policy, and identity contracts

**Files:**

- Create: `src/stock_quant/research/strategy_challenge/__init__.py`
- Create: `src/stock_quant/research/strategy_challenge/models.py`
- Test: `tests/unit/test_strategy_challenge_models.py`

**Interfaces:**

- Produces `UniverseIdentity`, `StrategyComparisonPolicy`, `ChallengeDeclaration`, `ChallengeConclusion`, `ChallengeResult`, `canonical_challenge_sha256(value) -> str`, and `compute_challenge_id(declaration) -> str`.
- `ChallengeDeclaration` contains baseline experiment ID, challenger strategy hash, policy plus hash, schedule hash, universe identity, declaration timestamp, strategy family, and identity-scheme version.

- [ ] **Step 1: Write failing strict-identity tests**

```python
def test_complete_universe_identity_is_required():
    with pytest.raises(ValidationError, match="membership_table_sha256"):
        ChallengeDeclaration.model_validate(valid_declaration_dict() | {
            "universe_definition": {
                "universe_id": "csi300", "universe_version": "a" * 64,
                "evidence_summary_sha256": "b" * 64,
            }
        })


@pytest.mark.parametrize("field", ["universe_id", "universe_version",
                                    "membership_table_sha256", "evidence_summary_sha256"])
def test_each_universe_field_changes_challenge_id(declaration, field):
    changed = declaration.model_copy(update={
        "universe_definition": mutate_universe(declaration.universe_definition, field)
    })
    assert compute_challenge_id(changed) != compute_challenge_id(declaration)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_strategy_challenge_models.py -v`

Expected: FAIL because the strategy-challenge package does not exist.

- [ ] **Step 3: Implement immutable declaration and exact policy**

```python
class StrategyComparisonPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_version: Literal["strategy-comparison-v1"] = "strategy-comparison-v1"
    minimum_unconsumed_executed_folds: Literal[5] = 5
    aggregate_sharpe_delta_floor: Decimal = Decimal("0.10")
    aggregate_annualized_return_delta_floor: Decimal = Decimal("-0.02")
    positive_fold_ratio_delta_floor: Decimal = Decimal("0.00")
    worst_fold_calendar_return_delta_floor: Decimal = Decimal("-0.02")
    median_abs_max_drawdown_delta_ceiling: Decimal = Decimal("0.00")
    median_turnover_ratio_ceiling: Decimal = Decimal("0.85")
    median_explicit_cost_ratio_delta_ceiling: Decimal = Decimal("0.00")
    median_reject_rate_delta_ceiling: Decimal = Decimal("0.02")
    median_invested_exposure_floor: Decimal = Decimal("0.90")
```

Validate all SHA-256 values as 64 lowercase hexadecimal characters and all policy Decimals as finite values. Recompute and validate `comparison_policy_hash`; exclude runtime path, PID, host, and worker count. Serialize UTC declaration time only as evidence metadata, not as a mutable fallback for missing identity.

- [ ] **Step 4: Verify and commit**

Run: `pytest tests/unit/test_strategy_challenge_models.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/strategy_challenge tests/unit/test_strategy_challenge_models.py && git commit -m "feat: define immutable strategy challenges"`

### Task 2: Atomic holdout consumption registry

**Files:**

- Create: `src/stock_quant/research/strategy_challenge/registry.py`
- Test: `tests/integration/test_holdout_registry.py`

**Interfaces:**

- Produces `HoldoutConsumption`, `HoldoutRegistry`, `HoldoutAlreadyConsumed`, `HoldoutIdentityConflict`, and `HoldoutRegistry.consume(declaration: ChallengeDeclaration) -> HoldoutConsumption`.
- Registry paths are `data/strategy_challenges/declarations/<challenge_id>.json`, `data/strategy_challenges/holdout_registry.parquet`, `data/strategy_challenges/consumptions/<challenge_id>.json`, and `data/strategy_challenges/.holdout.lock` beneath an injected project root.

- [ ] **Step 1: Write failing consumption and recovery tests**

```python
def test_first_consumer_atomically_consumes_family_schedule(tmp_path, declaration):
    record = HoldoutRegistry(tmp_path).consume(declaration)
    assert record.status == "consumed"
    assert record.consumption_key == f"{declaration.strategy_family}:{declaration.fold_schedule_hash}"


def test_changed_universe_cannot_reconsume_same_history(tmp_path, declaration):
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    changed = declaration_with_other_universe_version(declaration)
    with pytest.raises(HoldoutAlreadyConsumed):
        registry.consume(changed)


def test_same_challenge_id_recovers_idempotently(tmp_path, declaration):
    registry = HoldoutRegistry(tmp_path)
    assert registry.consume(declaration) == registry.consume(declaration)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/integration/test_holdout_registry.py -v`

Expected: FAIL because the registry does not exist.

- [ ] **Step 3: Implement single-writer immutable consumption**

Acquire a lock with `O_CREAT|O_EXCL`, re-read registry state while locked, and compute the consumption key only from strategy family and schedule hash. If the key exists, return only when challenge ID and every declaration hash match; otherwise raise without modifying files. For a first consumer, write the immutable JSON to a temporary sibling, `fsync`, rename atomically, rebuild the Parquet index from consumption JSON files, and release the lock in `finally`. Persist the complete universe identity and declaration hash in both JSON and Parquet.

- [ ] **Step 4: Add concurrency/crash tests**

```python
def test_two_concurrent_consumers_yield_one_record(tmp_path, declaration):
    results = run_two_consumers(tmp_path, declaration, changed_strategy_hash(declaration))
    assert sum(getattr(result, "status", None) == "consumed" for result in results) == 1
    assert sum(isinstance(result, HoldoutAlreadyConsumed) for result in results) == 1


def test_consumption_survives_challenge_failure(tmp_path, declaration):
    registry = HoldoutRegistry(tmp_path)
    registry.consume(declaration)
    assert registry.lookup(declaration.strategy_family, declaration.fold_schedule_hash).status == "consumed"
```

Inject the failure point after atomic consumption in the test; never implement rollback or deletion.

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/integration/test_holdout_registry.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/strategy_challenge/registry.py tests/integration/test_holdout_registry.py && git commit -m "feat: consume strategy holdouts atomically"`

### Task 3: Identity matching, paired metrics, and comparison evaluation

**Files:**

- Create: `src/stock_quant/research/strategy_challenge/compare.py`
- Test: `tests/unit/test_strategy_challenge_compare.py`

**Interfaces:**

- Produces `ChallengeIntegrityError`, `assert_comparable_manifests(baseline: Mapping, challenger: Mapping, declaration: ChallengeDeclaration) -> None`, `pair_fold_metrics(...) -> pd.DataFrame`, and `evaluate_challenge(*, declaration, consumption, baseline_manifest, challenger_manifest, baseline_metrics, challenger_metrics) -> ChallengeResult`.
- Paired output columns include identity keys, both values, delta/ratio, threshold, and pass flag for every policy metric.

- [ ] **Step 1: Write failing identity/pairing tests**

```python
def test_universe_mismatch_is_terminal_integrity_error(declaration, manifests):
    baseline, challenger = manifests
    challenger["universe_definition"]["evidence_summary_sha256"] = "f" * 64
    with pytest.raises(ChallengeIntegrityError, match="universe_definition"):
        assert_comparable_manifests(baseline, challenger, declaration)


def test_missing_fold_scenario_pair_fails(declaration, complete_metrics):
    challenger = complete_metrics.challenger.iloc[:-1]
    with pytest.raises(ChallengeIntegrityError, match="missing pair"):
        pair_fold_metrics(complete_metrics.baseline, challenger, declaration.comparison_policy)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_strategy_challenge_compare.py -k 'mismatch or missing_fold' -v`

Expected: FAIL because comparison functions do not exist.

- [ ] **Step 3: Implement strict identity and unique pairing**

Require exact equality for data environment hash, universe identity block, fold schedule hash, factor/strategy input hash excluding portfolio-rule hash, initial equity, rebalance frequency, and ordered cost scenarios. Require baseline rule `top_n_equal_weight`, challenger rule `buffered_risk_weighted`, and challenger Walk-Forward conclusion bound to its `stability_policy_hash`. Missing or duplicate executed pairs are integrity errors. Pair only executed folds; preserve legal market-wide skips as evidence-insufficiency records so evaluation returns `INCONCLUSIVE_RESEARCH_ONLY` rather than treating them as corrupt pairs.

- [ ] **Step 4: Write failing conclusion-policy tests**

```python
def test_all_scenarios_must_pass_for_promotion(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(one_failing_scenario=True))
    assert result.conclusion == "REJECTED"
    assert "full_cost" in result.failed_scenarios


def test_four_valid_folds_is_inconclusive(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(executed_folds=4))
    assert result.conclusion == "INCONCLUSIVE_RESEARCH_ONLY"


def test_nonstable_challenger_cannot_be_promoted(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(challenger_stability="UNSTABLE"))
    assert result.conclusion == "REJECTED"


def test_inconclusive_challenger_keeps_challenge_inconclusive(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(challenger_stability="INCONCLUSIVE"))
    assert result.conclusion == "INCONCLUSIVE_RESEARCH_ONLY"


def test_integrity_failure_has_null_comparison_conclusion(challenge_inputs):
    result = evaluate_challenge(**challenge_inputs(corrupt_pair=True))
    assert result.status == "FAILED"
    assert result.conclusion is None
```

- [ ] **Step 5: Implement metrics and ordered conclusion semantics**

For each scenario compute challenger-minus-baseline aggregate Sharpe, annualized return, positive-fold ratio, worst-fold return, median absolute per-fold drawdown, median reject-rate delta, median invested exposure, and baseline-relative turnover; require challenger median explicit-cost ratio no greater than baseline. Apply evaluation order: identity/registry/pairing error or challenger Walk-Forward FAILED → FAILED/null; fewer than five unconsumed executed folds, legal skip, undefined required metric, or challenger Walk-Forward INCONCLUSIVE → INCONCLUSIVE; challenger Walk-Forward UNSTABLE → REJECTED; otherwise STABLE plus all scenario/threshold cells true → PROMOTED, and any failed threshold → REJECTED. Preserve every failed cell in the result.

- [ ] **Step 6: Verify and commit**

Run: `pytest tests/unit/test_strategy_challenge_compare.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/strategy_challenge/compare.py tests/unit/test_strategy_challenge_compare.py && git commit -m "feat: evaluate paired strategy challenges"`

### Task 4: Consume-before-read orchestration and immutable artifacts

**Files:**

- Create: `src/stock_quant/research/strategy_challenge/service.py`
- Create: `src/stock_quant/research/strategy_challenge/reporting.py`
- Create: `src/stock_quant/reporting/templates/strategy_challenge.html.j2`
- Modify: `src/stock_quant/cli.py`
- Test: `tests/integration/test_strategy_challenge_service.py`
- Modify: `tests/integration/test_cli.py`

**Interfaces:**

- Produces `StrategyChallengeService.run(declaration_path: Path) -> ChallengeResult`.
- CLI command: `stock-quant research challenge --declaration <strategy_challenge.json>`.
- Publishes under `data/strategy_challenges/results/<challenge_id>/`: `strategy_challenge.json`, `holdout_consumption.json`, `paired_fold_metrics.parquet`, `strategy_comparison.json`, and `strategy_comparison_report.html`.

- [ ] **Step 1: Write failing consume-before-read test**

```python
def test_service_consumes_before_opening_challenger_artifacts(tmp_path, declaration_file, spying_loader):
    service = StrategyChallengeService(tmp_path, experiment_loader=spying_loader)
    service.run(declaration_file)
    assert spying_loader.events[:3] == [
        "declaration_published", "holdout_consumed", "challenger_opened"
    ]
```

The service emits the first event after atomically publishing the immutable declaration, the registry emits the second after consumption rename, and the experiment loader emits the third on the first challenger file open.

- [ ] **Step 2: Run service tests and confirm failure**

Run: `pytest tests/integration/test_strategy_challenge_service.py tests/integration/test_cli.py -k challenge -v`

Expected: FAIL because the service and CLI command are absent.

- [ ] **Step 3: Implement orchestration and immutable publication**

Load and validate only the declaration first, atomically publish its canonical bytes under `declarations/<challenge_id>.json`, consume the holdout, persist the consumption copy, and only then open baseline/challenger manifests and metrics. An existing declaration path is reusable only when its bytes match; conflicting bytes under one ID are an identity failure. On any later error, atomically publish a FAILED `strategy_comparison.json` with null conclusion and the redacted error code; retain consumption. On success, write paired metrics and result into a staging directory, hash every artifact, and atomically rename. An existing result is reusable only when all bytes and declared hashes match.

- [ ] **Step 4: Implement complete reporting and CLI behavior**

Render declaration time/hash, full universe identity, schedule/policy hashes, consumption key/status, both experiment IDs and snapshot hashes, fold/scenario pairs, every threshold/result, failed items, and nullable conclusion. Do not recommend another parameter set. CLI exits nonzero only for FAILED; PROMOTED, REJECTED, and INCONCLUSIVE are completed research outcomes and exit zero with the exact label.

- [ ] **Step 5: Add immutable/result regression tests**

```python
def test_failed_challenge_publishes_null_conclusion_and_keeps_consumption(service, corrupt_inputs):
    result = service.run(corrupt_inputs.declaration_path)
    assert result.status == "FAILED" and result.conclusion is None
    assert corrupt_inputs.consumption_path.is_file()


def test_report_contains_every_failed_threshold(rendered_report, expected_failures):
    assert all(name in rendered_report for name in expected_failures)
    assert "下一组参数" not in rendered_report
```

- [ ] **Step 6: Verify and commit**

Run: `pytest tests/integration/test_strategy_challenge_service.py tests/integration/test_cli.py -k challenge -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/strategy_challenge src/stock_quant/reporting/templates/strategy_challenge.html.j2 src/stock_quant/cli.py tests/integration/test_strategy_challenge_service.py tests/integration/test_cli.py && git commit -m "feat: publish one-time strategy challenges"`

### Task 5: Operator documentation, project memory, and full verification

**Files:**

- Modify: `README.md`
- Modify: `RUNBOOK.md`
- Modify: `docs/operations/phase-one-validation.md`
- Modify: `PROJECT_MEMORY.md`

**Interfaces:**

- Documents declaration, irreversible consumption, result interpretation, recovery, and audit commands.
- Marks the challenge subsystem complete only after focused and full verification succeed.

- [ ] **Step 1: Document the one-time workflow**

Document how to choose an unconsumed schedule; obtain baseline experiment ID and challenger strategy hash; copy exact universe and policy identities; publish the declaration; run the command once; inspect consumption before results; recover only the identical challenge ID; and interpret all four outcomes. Explicitly state that changing a universe version, parameter, cost scenario, or failed result never restores an already consumed historical holdout.

- [ ] **Step 2: Add an audit checklist**

Require declaration timestamp to precede challenger artifact reads, exact consumption-key uniqueness, full universe identity in all four JSON/registry surfaces, exact paired row count `executed_folds * cost_scenarios`, all-scenario conjunction, null conclusion for FAILED, and no hidden failed thresholds.

- [ ] **Step 3: Run the focused challenge suite**

Run: `pytest tests/unit/test_strategy_challenge_models.py tests/unit/test_strategy_challenge_compare.py tests/integration/test_holdout_registry.py tests/integration/test_strategy_challenge_service.py -v`

Expected: PASS.

- [ ] **Step 4: Run full project verification**

Run: `pytest -q`

Expected: PASS.

Run: `ruff check src tests project`

Expected: PASS.

Run: `git diff --check`

Expected: no output and exit 0.

- [ ] **Step 5: Update project memory and commit**

After Steps 3–4 pass, record the one-time challenge implementation as complete with artifact names and verification commands. Do not mark the challenger `PROMOTED` unless an actual formal challenge produces that conclusion.

Commit: `git add README.md RUNBOOK.md docs/operations/phase-one-validation.md PROJECT_MEMORY.md && git commit -m "docs: operate one-time strategy challenges"`

## Self-Review

- Spec coverage: Task 1 covers immutable predeclaration, exact policy, four-field universe identity, and hashes. Task 2 enforces irreversible family/calendar consumption without version-reset loopholes. Task 3 covers identity equality, unique fold/scenario pairing, exact thresholds, all-scenario conjunction, evidence sufficiency, and terminal failures. Task 4 guarantees consume-before-read ordering, immutable artifacts, report completeness, and CLI semantics. Task 5 closes operational auditability.
- Placeholder scan: every task includes concrete files, public interfaces, executable tests, expected failures/passes, and commit boundaries; no deferred behavior remains.
- Type consistency: `ChallengeDeclaration` feeds the registry and service; `HoldoutConsumption` plus both experiment manifests feed comparison; paired metrics feed `ChallengeResult`; all four identity surfaces and artifacts share the same `UniverseIdentity` serialization.
- Dependency check: this plan consumes immutable Walk-Forward and buffered-strategy experiment artifacts. It does not rerun, tune, or alter either strategy while comparing them.
