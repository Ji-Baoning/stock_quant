# Walk-Forward OOS Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, audit-ready fixed-calendar Walk-Forward workflow that evaluates locked strategies over isolated annual OOS folds and produces a policy-bound stability conclusion.

**Architecture:** Strict policy and snapshot models freeze all research-relevant inputs before identity is computed. An immutable schedule is generated once, individual folds run through isolated accounts, outcomes are written separately from the schedule, and pure metrics/evaluation modules aggregate only valid OOS returns. The existing `ResearchRunner` orchestrates these components without selecting parameters, rewriting calendars, or bypassing data/universe acceptance.

**Tech Stack:** Python 3.10, Pydantic 2, pandas 2+, NumPy, PyArrow 14+, DuckDB, PyYAML 6+, pytest, Jinja2, SHA-256

**Spec:** `docs/superpowers/specs/2026-09-09-walk-forward-oos-stability-design.md`

## Global Constraints

- Prerequisite: execute only after the point-in-time total-return, real-data acceptance registry, and point-in-time index-universe implementations are present on the integration base. If `adjusted_bar`, acceptance preflight, or `UniverseResolver` is absent, stop and integrate those approved plans first; do not recreate or bypass them here.
- Mode is exactly `fixed_calendar_oos_v1`: three calendar years of warmup, at least 756 confirmed warmup sessions, 60 stable-history sessions, 12-month non-overlapping OOS folds, January 1 anchor, and account reset per fold.
- `fold_schedule.json` is written and hashed before fold execution and is never modified. Runtime states belong only in `fold_outcomes.json`.
- A fold/system integrity failure makes the run `FAILED` with `stability_conclusion=null`; it can never become `INCONCLUSIVE`.
- Individual suspension and ordinary order rejection are observed strategy results, not missing portfolio-return days or system failures.
- Every confirmed open OOS day has exactly one portfolio return; first-day return uses `initial_equity` as its predecessor.
- Stability thresholds apply independently to every predeclared cost scenario; the final STABLE result is their conjunction.
- Cross-fold maximum drawdown and Calmar are forbidden. Per-fold drawdown uses daily mark-to-market `net_equity_after_cost`.
- Default tests are fully offline and deterministic. Runtime paths, timestamps, host/process identity, and worker count do not enter experiment identity.
- Use TDD and commit only files named by each task. Work in an isolated worktree created with `superpowers:using-git-worktrees`.

---

### Task 1: Strict policy models, frozen snapshots, and experiment identity

**Files:**

- Create: `src/stock_quant/research/walk_forward/__init__.py`
- Create: `src/stock_quant/research/walk_forward/policy.py`
- Create: `src/stock_quant/research/walk_forward/snapshots.py`
- Modify: `src/stock_quant/research/spec.py`
- Modify: `src/stock_quant/research/registry.py`
- Test: `tests/unit/test_walk_forward_policy.py`
- Test: `tests/unit/test_walk_forward_snapshots.py`
- Modify: `tests/unit/test_experiment_spec.py`
- Modify: `tests/integration/test_experiment_registry.py`

**Interfaces:**

- Produces `WalkForwardPolicy`, `StabilityPolicy`, `StrategySnapshot`, `ExperimentSnapshot`, `DataEnvironmentSnapshot`, `SnapshotBundle`, and `canonical_sha256(value) -> str`.
- Produces `build_snapshot_bundle(*, spec, dataset_manifest, universe_definition, config_hashes) -> SnapshotBundle`.
- Changes `compute_experiment_id(spec, snapshots) -> str` and `ExperimentIdentity.of(spec, snapshots)` so both full snapshot payloads and their three hashes enter the canonical identity payload.

- [ ] **Step 1: Write failing strict-policy tests**

```python
def test_default_policy_is_the_approved_fixed_calendar_contract():
    policy = WalkForwardPolicy()
    assert policy.mode == "fixed_calendar_oos_v1"
    assert (policy.warmup_years, policy.warmup_unit) == (3, "calendar_years")
    assert policy.min_warmup_trading_days == 756
    assert policy.required_stable_history_days_before_s == 60
    assert (policy.oos_months, policy.step_months) == (12, 12)
    assert policy.account_reset is True
    assert policy.global_drawdown_aggregation == "forbidden"


def test_policy_rejects_overlapping_oos_folds():
    with pytest.raises(ValidationError, match="step_months"):
        WalkForwardPolicy(oos_months=12, step_months=6)
```

- [ ] **Step 2: Run policy tests and confirm failure**

Run: `pytest tests/unit/test_walk_forward_policy.py -v`

Expected: FAIL because `research.walk_forward.policy` does not exist.

- [ ] **Step 3: Implement strict policy models**

```python
class WalkForwardPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["fixed_calendar_oos_v1"] = "fixed_calendar_oos_v1"
    calendar_anchor: Literal["jan_1"] = "jan_1"
    warmup_years: Literal[3] = 3
    warmup_unit: Literal["calendar_years"] = "calendar_years"
    min_warmup_trading_days: Literal[756] = 756
    required_stable_history_days_before_s: Literal[60] = 60
    oos_months: Literal[12] = 12
    step_months: Literal[12] = 12
    account_reset: Literal[True] = True
    aggregate: Literal["oos_only"] = "oos_only"
    return_aggregation: Literal["concatenate_oos_daily_returns"] = "concatenate_oos_daily_returns"
    global_drawdown_aggregation: Literal["forbidden"] = "forbidden"
    per_fold_drawdown: Literal["required"] = "required"
    partial_boundary_policy: Literal["record_not_evaluated"] = "record_not_evaluated"
    fold_status_policy: Literal["strict_market_calendar_v1"] = "strict_market_calendar_v1"


class StabilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_version: Literal["stability-v1"] = "stability-v1"
    minimum_executed_folds: Literal[5] = 5
    minimum_positive_fold_ratio: Literal[0.60] = 0.60
    worst_fold_calendar_return_floor: Literal[-0.10] = -0.10
    annualization_sessions: Literal[252] = 252
    risk_free_rate: Literal[0.0] = 0.0
```

- [ ] **Step 4: Write failing identity-boundary tests**

```python
def test_strategy_parameter_change_changes_only_strategy_hash(bundle_factory):
    left = bundle_factory(parameters_hash="a" * 64)
    right = bundle_factory(parameters_hash="b" * 64)
    assert left.strategy_hash != right.strategy_hash
    assert left.experiment_hash == right.experiment_hash
    assert left.data_environment_hash == right.data_environment_hash


def test_runtime_metadata_does_not_change_identity(frozen_spec, snapshots):
    first = compute_experiment_id(frozen_spec, snapshots)
    second = compute_experiment_id(frozen_spec, snapshots)
    assert first == second
```

- [ ] **Step 5: Implement snapshot contracts and identity integration**

`StrategySnapshot` contains strategy/factor/portfolio versions, rebalance frequency and parameter hash. `ExperimentSnapshot` contains universe/corporate-action content hashes, ordered cost scenarios and both policies. `DataEnvironmentSnapshot` contains price, fundamental and calendar table hashes; set fundamental to the exact sentinel `NOT_USED` when the strategy consumes none. Sort mappings and reject duplicate cost scenarios. `SnapshotBundle` stores each full object and its recomputed SHA-256; validation rejects a supplied hash that differs from content.

Update identity/manifest validation to require the bundle. Do not hash `run_id`, output paths, timestamps, host name, PID or worker count. Increment the experiment identity scheme version because canonical identity semantics changed.

- [ ] **Step 6: Run identity regressions and commit**

Run: `pytest tests/unit/test_walk_forward_policy.py tests/unit/test_walk_forward_snapshots.py tests/unit/test_experiment_spec.py tests/integration/test_experiment_registry.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/walk_forward src/stock_quant/research/spec.py src/stock_quant/research/registry.py tests && git commit -m "feat: freeze walk-forward research identity"`

### Task 2: Immutable fold schedule and separate outcome ledger

**Files:**

- Create: `src/stock_quant/research/walk_forward/schedule.py`
- Test: `tests/unit/test_walk_forward_schedule.py`

**Interfaces:**

- Produces `ScheduleDisposition`, `FoldOutcomeStatus`, `FoldWindow`, `BoundaryWindow`, `FoldSchedule`, `FoldOutcome`, `FoldOutcomeLedger`, `FoldOutcomeLedger.for_schedule(schedule, outcomes)`, `materialize_schedule(...) -> FoldSchedule`, and `write_canonical_json(path, model) -> str` returning the written SHA-256.
- `FoldSchedule` contains only pre-execution facts. `FoldOutcomeLedger.schedule_sha256` binds results to the immutable schedule.

- [ ] **Step 1: Write failing calendar-boundary tests**

```python
def test_annual_fold_uses_real_trading_boundaries(calendar):
    schedule = materialize_schedule(
        requested_start=date(2020, 1, 1), requested_end=date(2020, 12, 31),
        calendar=calendar, policy=WalkForwardPolicy(), membership_snapshots={},
    )
    fold = schedule.folds[0]
    assert (fold.calendar_start, fold.calendar_end) == (date(2020, 1, 1), date(2020, 12, 31))
    assert (fold.first_trading_day, fold.last_trading_day) == (date(2020, 1, 2), date(2020, 12, 31))


def test_partial_tail_is_recorded_not_silently_dropped(calendar):
    schedule = materialize_schedule(
        requested_start=date(2020, 1, 1), requested_end=date(2021, 6, 30),
        calendar=calendar, policy=WalkForwardPolicy(), membership_snapshots={},
    )
    assert schedule.boundaries[-1].disposition == "not_evaluated_boundary"
    assert schedule.boundaries[-1].calendar_start == date(2021, 1, 1)
```

- [ ] **Step 2: Run schedule tests and confirm failure**

Run: `pytest tests/unit/test_walk_forward_schedule.py -v`

Expected: FAIL because `schedule.py` does not exist.

- [ ] **Step 3: Implement deterministic materialization**

Generate complete January–December folds fully contained in the requested range. Derive first/last open session from the pinned calendar. When the complete OOS calendar range has no open session, retain the fold with null trading boundaries so Task 5 can require market-wide closure evidence; absence of that evidence becomes FAILED. Compute `warmup_calendar_start`, `warmup_calendar_end`, `warmup_session_count`, and deterministic `fold_id = sha256(canonical fold input)`. Record incomplete leading/trailing ranges as `BoundaryWindow(disposition="not_evaluated_boundary")`. Reject overlapping fold windows, partial calendar coverage, or duplicate IDs.

- [ ] **Step 4: Write failing immutability/outcome tests**

```python
def test_outcome_does_not_mutate_schedule(tmp_path, schedule):
    before = write_canonical_json(tmp_path / "fold_schedule.json", schedule)
    ledger = FoldOutcomeLedger(schedule_sha256=before, outcomes=(
        FoldOutcome(fold_id=schedule.folds[0].fold_id, status="failed_preflight", reason_code="DATA_GAP"),
    ))
    write_canonical_json(tmp_path / "fold_outcomes.json", ledger)
    assert sha256_file(tmp_path / "fold_schedule.json") == before


def test_outcome_must_reference_every_planned_fold(schedule):
    with pytest.raises(ValidationError, match="fold ids"):
        FoldOutcomeLedger.for_schedule(schedule, outcomes=())
```

- [ ] **Step 5: Implement canonical persistence and outcome coverage**

Write JSON with sorted keys, compact UTF-8, ISO dates and no NaN. Refuse overwriting an existing schedule with different bytes. Require one outcome for every scheduled fold and no unknown/duplicate fold IDs. Keep status/reason out of the schedule.

- [ ] **Step 6: Verify and commit**

Run: `pytest tests/unit/test_walk_forward_schedule.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/walk_forward/schedule.py tests/unit/test_walk_forward_schedule.py && git commit -m "feat: materialize immutable fold schedules"`

### Task 3: OOS return, risk, execution-quality, and same-path cost metrics

**Files:**

- Create: `src/stock_quant/research/walk_forward/metrics.py`
- Test: `tests/unit/test_walk_forward_metrics.py`
- Modify: `src/stock_quant/analytics/performance.py`
- Modify: `tests/unit/test_performance.py`

**Interfaces:**

- Produces `FoldMetrics`, `ScenarioMetrics`, `AggregateOOSMetrics`, `build_daily_returns`, `compute_fold_metrics`, `compute_same_path_costs`, and `aggregate_oos_returns`.
- Fold equity artifact has canonical columns `trade_date`, `initial_equity`, and `net_equity_after_cost`; the last is derived from the engine’s current `total_equity` without changing its economic meaning.

- [ ] **Step 1: Write failing daily-return tests**

```python
def test_first_oos_return_uses_initial_equity():
    equity = pd.DataFrame({
        "trade_date": [date(2020, 1, 2), date(2020, 1, 3)],
        "net_equity_after_cost": [101.0, 99.99],
    })
    result = build_daily_returns(equity, initial_equity=100.0, expected_open_days=(date(2020, 1, 2), date(2020, 1, 3)))
    assert result["daily_return"].tolist() == pytest.approx([0.01, -0.01])


def test_missing_market_open_day_is_integrity_failure():
    with pytest.raises(OOSIntegrityError, match="2020-01-03"):
        build_daily_returns(one_day_equity(), initial_equity=100.0,
                            expected_open_days=(date(2020, 1, 2), date(2020, 1, 3)))
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `pytest tests/unit/test_walk_forward_metrics.py -k 'first_oos or missing_market' -v`

Expected: FAIL because metric functions do not exist.

- [ ] **Step 3: Implement returns and per-fold risk**

Require exactly one finite, positive equity row per expected open session. Compute first return from `initial_equity`, later returns from prior fold equity. Compute `fold_calendar_return`, sample volatility/Sharpe with `ddof=1`, and drawdown only from `net_equity_after_cost`. Represent undefined volatility, Sharpe and zero-denominator rates as JSON null, never zero or NaN.

- [ ] **Step 4: Write failing cost and reject-rate tests**

```python
def test_partial_order_counts_in_reject_rate():
    value = compute_fold_metrics(equity(), fills(), submitted_orders=three_orders(), order_diffs=one_partial_one_full())
    assert value.reject_rate == pytest.approx(2 / 3)
    assert value.fully_rejected_order_count == 1
    assert value.partially_filled_order_count == 1


def test_same_path_cost_replay_never_changes_fills():
    result = compute_same_path_costs(equity(), fills_with_costs(), initial_equity=1_000_000)
    assert result.fill_count == len(fills_with_costs())
    assert result.total_explicit_cost == pytest.approx(
        fills_with_costs()[["commission", "stamp_tax"]].to_numpy().sum()
    )
```

- [ ] **Step 5: Implement execution/cost metrics**

Count unique submitted order IDs. `reject_rate` counts orders with any rejected quantity; also emit full/partial counts and rejected/requested quantity ratio. Preserve the current turnover formula as version `turnover-v1`: numerator `(buy_notional + sell_notional) / 2`, denominator mean daily `net_equity_after_cost`; persist both values beside the ratio. Same-path gross replay uses the exact realized fill IDs, quantities and prices and removes explicit commissions/taxes only. Slippage impact uses recorded reference price and realized quantity. Keep the existing `zero_cost` scenario as a separate path-changing counterfactual; never use it for explicit cost drag.

- [ ] **Step 6: Implement aggregate OOS metrics**

Sort and concatenate executed-fold daily returns; reject duplicate dates and any date outside its fold. Let `N` be the row count. Compute product return, `product ** (252/N) - 1`, sample volatility and zero-rate Sharpe. Emit both observation counts as `N`. Do not define aggregate max drawdown or Calmar fields.

- [ ] **Step 7: Verify and commit**

Run: `pytest tests/unit/test_walk_forward_metrics.py tests/unit/test_performance.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/walk_forward/metrics.py src/stock_quant/analytics/performance.py tests && git commit -m "feat: compute auditable oos stability metrics"`

### Task 4: Terminal failure and policy-bound stability evaluation

**Files:**

- Create: `src/stock_quant/research/walk_forward/evaluation.py`
- Test: `tests/unit/test_walk_forward_evaluation.py`

**Interfaces:**

- Reuses `research.models.RunStatus`; produces `StabilityConclusion`, `StabilityEvaluation`, and `evaluate_stability(*, policy, declared_scenarios, scenario_metrics, outcomes, integrity_failures) -> StabilityEvaluation`.
- `StabilityEvaluation.stability_policy_hash` is mandatory whenever conclusion is non-null.

- [ ] **Step 1: Write failing precedence tests**

```python
def test_failure_is_terminal_and_has_no_conclusion(policy, passing_metrics):
    value = evaluate_stability(policy=policy, scenario_metrics=passing_metrics,
                               outcomes=executed_outcomes(5), integrity_failures=("DATA_GAP",))
    assert value.research_status == "FAILED"
    assert value.stability_conclusion is None


def test_less_than_five_valid_folds_is_inconclusive(policy, passing_metrics):
    value = evaluate_stability(policy=policy, scenario_metrics=passing_metrics[:4],
                               outcomes=executed_outcomes(4), integrity_failures=())
    assert value.stability_conclusion == "INCONCLUSIVE"
```

- [ ] **Step 2: Verify failure**

Run: `pytest tests/unit/test_walk_forward_evaluation.py -v`

Expected: FAIL because evaluation models are missing.

- [ ] **Step 3: Implement strict ordered evaluation**

Evaluate in this order: any integrity failure, `failed_preflight`, missing declared scenario, or incomplete scenario artifact → FAILED/null; any legal skipped fold or fewer than five executed folds → COMPLETED/INCONCLUSIVE; otherwise evaluate every declared scenario independently. A scenario passes only if positive fold ratio is at least 0.60 and worst calendar return is greater than -0.10. All scenarios must pass for STABLE; otherwise UNSTABLE. Include per-scenario inputs/reasons and the recomputed policy hash.

- [ ] **Step 4: Add adversarial scenario tests**

```python
def test_one_bad_locked_cost_scenario_makes_result_unstable(policy):
    value = evaluate_stability(policy=policy,
        scenario_metrics=(passing_scenario("zero_cost"), failing_scenario("full_cost")),
        outcomes=executed_outcomes(5), integrity_failures=())
    assert value.stability_conclusion == "UNSTABLE"


def test_missing_policy_hash_cannot_validate_formal_result(valid_evaluation):
    payload = valid_evaluation.model_dump(mode="json")
    payload["stability_policy_hash"] = None
    with pytest.raises(ValidationError):
        StabilityEvaluation.model_validate(payload)
```

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/unit/test_walk_forward_evaluation.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/walk_forward/evaluation.py tests/unit/test_walk_forward_evaluation.py && git commit -m "feat: evaluate locked stability policy"`

### Task 5: Isolated fold runner and resumable artifact contract

**Files:**

- Create: `src/stock_quant/research/walk_forward/runner.py`
- Test: `tests/integration/test_walk_forward_runner.py`
- Modify: `src/stock_quant/research/models.py`

**Interfaces:**

- Produces `FoldRunRequest`, `FoldRunResult`, `WalkForwardRunResult`, `WalkForwardRunFailed`, and `WalkForwardRunner.run(request) -> WalkForwardRunResult`.
- Consumes existing point-in-time `UniverseResolver`, accepted dataset context, factor/portfolio factories, `BacktestEngine`, policies, schedule and snapshot bundle.
- Produces the exact fold artifacts and root `fold_outcomes.json`, with content hashes suitable for the experiment registry.

- [ ] **Step 1: Write failing isolation tests**

```python
def test_each_fold_constructs_a_fresh_account(walk_forward_runner, request):
    result = walk_forward_runner.run(request)
    assert [fold.initial_cash for fold in result.executed_folds] == [1_000_000.0] * 2
    assert result.executed_folds[1].opening_positions == {}


def test_warmup_produces_no_orders_or_returns(walk_forward_runner, request):
    result = walk_forward_runner.run(request)
    for fold in result.executed_folds:
        assert fold.orders.trade_date.min() >= fold.first_trading_day
        assert fold.daily_returns.trade_date.min() >= fold.first_trading_day
```

- [ ] **Step 2: Run integration test and confirm failure**

Run: `pytest tests/integration/test_walk_forward_runner.py -k 'fresh_account or warmup' -v`

Expected: FAIL because no fold runner exists.

- [ ] **Step 3: Implement one-fold execution**

Read warmup plus OOS factor inputs but suppress portfolio/order production before `first_trading_day`. Instantiate a new `BacktestRequest` and account state for every fold with identical fixed initial cash. Restrict engine valuation/output to expected OOS open days, map current `total_equity` to explicit fold artifact column `net_equity_after_cost`, and compute metrics through Task 3 functions.

- [ ] **Step 4: Write failing audit/failure tests**

```python
def test_failed_preflight_keeps_schedule_and_writes_outcome(walk_forward_runner, bad_request):
    with pytest.raises(WalkForwardRunFailed) as caught:
        walk_forward_runner.run(bad_request)
    assert caught.value.schedule_path.is_file()
    assert caught.value.outcomes[0].status == "failed_preflight"
    assert caught.value.stability_conclusion is None


def test_ordinary_order_rejection_is_not_system_failure(walk_forward_runner, rejected_order_request):
    result = walk_forward_runner.run(rejected_order_request)
    assert result.outcomes[0].status == "executed"
    assert result.scenario_metrics[0].reject_rate > 0
```

- [ ] **Step 5: Implement preflight/outcome persistence and resume**

Before accounts exist, persist schedule and its hash. Run dataset acceptance, universe coverage, warmup/session, benchmark and ledger preflights per fold. Write one outcome for every planned fold. On failure, leave schedule unchanged, atomically write outcomes and a FAILED manifest with null conclusion, then raise. Stage-completion hashes must include fold ID, scenario, schedule hash and snapshot hashes so resume cannot reuse another fold’s artifacts.

- [ ] **Step 6: Verify and commit**

Run: `pytest tests/integration/test_walk_forward_runner.py tests/integration/test_backtest_engine.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research/walk_forward/runner.py src/stock_quant/research/models.py tests/integration/test_walk_forward_runner.py && git commit -m "feat: run isolated walk-forward folds"`

### Task 6: ResearchRunner, registry, report, and CLI integration

**Files:**

- Modify: `src/stock_quant/research/runner.py`
- Modify: `src/stock_quant/research/registry.py`
- Modify: `src/stock_quant/research/models.py`
- Modify: `src/stock_quant/reporting/html.py`
- Modify: `src/stock_quant/reporting/templates/experiment.html.j2`
- Modify: `src/stock_quant/cli.py`
- Modify: `configs/experiments/momentum_60d.yml`
- Modify: `tests/integration/test_research_runner.py`
- Modify: `tests/integration/test_reports.py`
- Modify: `tests/integration/test_cli.py`

**Interfaces:**

- `ResearchRunner.run` builds snapshots and schedule before experiment identity, delegates fold execution, and publishes only a completed audit set.
- Formal experiment artifacts add `walk_forward_manifest.json`, `fold_schedule.json`, `fold_outcomes.json`, `stability_report.json`, and content-hashed `folds/` files.

- [ ] **Step 1: Write failing end-to-end identity/artifact tests**

```python
def test_formal_walk_forward_publishes_complete_audit_chain(runner, walk_forward_spec):
    published = runner.run(walk_forward_spec)
    root = published.path
    assert (root / "fold_schedule.json").is_file()
    assert (root / "fold_outcomes.json").is_file()
    report = json.loads((root / "stability_report.json").read_text())
    assert report["stability_policy_hash"]
    assert "aggregate_max_drawdown" not in report


def test_runtime_metadata_is_not_accepted_as_snapshot_content(snapshot_payload):
    snapshot_payload["worker_count"] = 4
    with pytest.raises(ValidationError, match="extra"):
        SnapshotBundle.model_validate(snapshot_payload)
```

- [ ] **Step 2: Run integration tests and confirm failure**

Run: `pytest tests/integration/test_research_runner.py -k walk_forward -v`

Expected: FAIL because the current runner has only one factor/portfolio/backtest/report pipeline.

- [ ] **Step 3: Integrate orchestration and registry validation**

Resolve accepted dataset and point-in-time universe first, build the three snapshots, compute identity, materialize schedule, then invoke `WalkForwardRunner`. Expand the artifact manifest from a flat exact-name set to a deterministic map that admits only declared root files plus `folds/<fold_id>/<declared-name>`; reject extra/missing files and verify every hash. Preserve the current single-window engineering path only under its explicit engineering policy; it cannot publish a formal stability conclusion.

- [ ] **Step 4: Integrate HTML/CLI output**

Report schedule coverage, boundary exclusions, fold statuses, per-scenario aggregate returns, per-fold calendar return/drawdown/execution-quality/cost metrics, policy thresholds/hash and final conclusion. Do not display global drawdown or select a preferred scenario. CLI prints research status and nullable conclusion; FAILED exits nonzero, STABLE/UNSTABLE/INCONCLUSIVE completed research exits zero while retaining the exact label.

- [ ] **Step 5: Add report/CLI regression assertions**

Verify HTML contains all fold IDs, all cost scenarios, `stability_policy_hash`, observation counts and per-fold drawdowns. Verify it contains no “global max drawdown” field. Verify a failed fold returns nonzero and the published experiment registry receives no incomplete experiment.

- [ ] **Step 6: Verify and commit**

Run: `pytest tests/integration/test_research_runner.py tests/integration/test_experiment_registry.py tests/integration/test_reports.py tests/integration/test_cli.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research src/stock_quant/reporting src/stock_quant/cli.py configs/experiments/momentum_60d.yml tests/integration && git commit -m "feat: publish walk-forward stability research"`

### Task 7: Documentation, project status, and full verification

**Files:**

- Modify: `README.md`
- Modify: `RUNBOOK.md`
- Modify: `docs/operations/phase-one-validation.md`
- Modify: `PROJECT_MEMORY.md`

**Interfaces:**

- Documents exact Research workflow, statuses, non-bypassable failures, artifact meanings and metric formulas.
- Records this stability item as complete only after every command below succeeds.

- [ ] **Step 1: Update operator documentation**

Document: required prerequisite acceptance/universe versions; valid policy fields; how date range becomes schedule; how boundary exclusions differ from skips; why failed folds remain; how to inspect schedule/outcomes/fold assets; all metric formulas; all scenario conjunction; and why INCONCLUSIVE is valid-but-insufficient rather than weak performance.

- [ ] **Step 2: Add an audit checklist**

In `docs/operations/phase-one-validation.md`, require verification of schedule hash before/after run, exact outcome coverage, distinct account IDs/initial equity per fold, one return per market-open OOS day, no duplicate OOS dates, null conclusion on failure, policy hash on completed conclusion, no global drawdown, and all declared scenarios in evaluation.

- [ ] **Step 3: Run focused Walk-Forward suite**

Run: `pytest tests/unit/test_walk_forward_policy.py tests/unit/test_walk_forward_snapshots.py tests/unit/test_walk_forward_schedule.py tests/unit/test_walk_forward_metrics.py tests/unit/test_walk_forward_evaluation.py tests/integration/test_walk_forward_runner.py -v`

Expected: PASS.

- [ ] **Step 4: Run full project verification**

Run: `pytest -q`

Expected: PASS.

Run: `ruff check src tests project`

Expected: PASS.

Run: `git diff --check`

Expected: no output and exit 0.

- [ ] **Step 5: Update project memory and commit**

Only after Steps 3–4 pass, set the Walk-Forward stability item in `PROJECT_MEMORY.md` to completed with the verification commands and artifact names. Commit only the documentation paths with message `docs: operate walk-forward stability research`.

## Self-Review

- Spec coverage: Task 1 covers strict policies, three snapshots, hash scope and identity. Task 2 resolves immutable schedule versus mutable-run-result contradiction. Task 3 covers first-day returns, exact OOS observation count, suspension days, per-fold drawdown, same-path costs and order-quality denominators. Task 4 encodes terminal failure and all-scenario stability. Tasks 5–6 implement isolated execution, audit artifacts, publication and reporting. Task 7 closes operational evidence.
- Placeholder scan: all tasks name exact files, public interfaces, failing tests, commands, expected results and commit boundaries; no deferred behavior or bypass remains.
- Type consistency: `SnapshotBundle` feeds identity and manifests; `FoldSchedule` hash feeds `FoldOutcomeLedger`; fold artifacts feed `ScenarioMetrics`; scenario metrics/outcomes feed `StabilityEvaluation`; the evaluation and both hashes feed final publication.
- Dependency check: the plan consumes, but does not duplicate, adjusted-bar, acceptance-registry and point-in-time-universe interfaces. Execution must wait until those implementations share one integration base.
