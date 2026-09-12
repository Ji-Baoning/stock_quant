# Buffered Risk-Weighted Momentum Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the formal `momentum_60d` Top-10 equal-weight portfolio rule with a deterministic buffered, inverse-volatility-weighted target that preserves one common strategy target across cost scenarios and emits complete construction/rebalance audit artifacts.

**Architecture:** Pure portfolio modules calculate trusted 60-session risk, two-stage ranks, buffered membership, and capped decimal weights without account or execution state. A scenario-local rebalancer converts the common frozen weights to whole-lot quantities from signal-close account state, applies the 2-percentage-point band only to continuing positions, and submits the remaining difference to the existing execution simulator. `ResearchRunner` selects the rule from the frozen experiment spec and persists common construction plus scenario-specific sizing decisions.

**Tech Stack:** Python 3.10, Pydantic 2, pandas 2+, NumPy, PyArrow 14+, `decimal.Decimal`, pytest, SHA-256

**Spec:** `docs/superpowers/specs/2026-09-09-buffered-risk-weighted-momentum-design.md`

## Global Constraints

- Prerequisite: execute only after the point-in-time total-return, real-data acceptance, point-in-time index-universe, and fixed-calendar Walk-Forward plans are integrated on one base. Stop if `adjusted_bar`, accepted dataset preflight, `UniverseResolver`, or isolated fold execution is absent.
- The `momentum_60d` factor and rebalance frequency remain unchanged; this plan changes only portfolio construction and account reconciliation.
- Fixed parameters are `target_count=10`, `entry_rank=10`, `hold_rank=15`, `risk_lookback_days=60`, `min_risk_observations=40`, `volatility_floor_annualized=0.10`, `max_single_weight=0.15`, `rebalance_band_absolute=0.02`, `gross_exposure=1.00`, `weight_quantum=1e-12`, long-only, no leverage.
- Rank by descending processed momentum then ascending full symbol string. Never depend on supplier row order.
- Trusted suspension carry days produce zero path return but do not count toward the 40 real closes. Unknown missing rows invalidate that symbol's risk input.
- Membership state crosses signal dates only inside one fold and resets at every fold boundary. Scenario fills, rejections, cash, and holdings never affect member selection.
- All scenarios share common members and theoretical weights; scenario accounts may produce different quantities, orders, fills, and band decisions.
- The execution-date provider must not read execution open, suspension, limit, or future data while forming orders. Those facts remain solely in `ExecutionSimulator`.
- Default tests are offline and deterministic. Use TDD and commit only the paths named by each task from an isolated worktree.

---

### Task 1: Strict portfolio policy and experiment identity

**Files:**

- Create: `src/stock_quant/portfolio/buffered_models.py`
- Modify: `src/stock_quant/research/spec.py`
- Modify: `configs/experiments/momentum_60d.yml`
- Test: `tests/unit/test_buffered_portfolio_policy.py`
- Modify: `tests/unit/test_experiment_spec.py`

**Interfaces:**

- Produces `BufferedRiskWeightedPolicy`, `PortfolioConstructionResult`, `WeightTargetPeriod`, and ordered audit-column constants.
- Replaces the single-literal `PortfolioRule` with the discriminated union `EqualWeightPortfolioRule | BufferedRiskWeightedPortfolioRule` while preserving existing equal-weight YAML compatibility.
- `BufferedRiskWeightedPortfolioRule.policy() -> BufferedRiskWeightedPolicy`; its canonical content participates in `parameters_hash`, `portfolio_rule_version`, strategy snapshot, and experiment ID through the Walk-Forward snapshot builder.

- [ ] **Step 1: Write failing strict-policy and identity tests**

```python
def test_buffered_policy_defaults_are_frozen_contract():
    rule = BufferedRiskWeightedPortfolioRule(name="buffered_risk_weighted")
    assert rule.policy().model_dump() == {
        "target_count": 10, "entry_rank": 10, "hold_rank": 15,
        "risk_lookback_days": 60, "min_risk_observations": 40,
        "volatility_floor_annualized": Decimal("0.10"),
        "max_single_weight": Decimal("0.15"),
        "rebalance_band_absolute": Decimal("0.02"),
        "gross_exposure": Decimal("1.00"), "long_only": True,
        "leverage": False, "weight_quantum": Decimal("0.000000000001"),
    }


def test_portfolio_parameter_change_changes_experiment_identity(make_spec, snapshots_for):
    left = make_spec(portfolio_rule={"name": "buffered_risk_weighted"})
    right = make_spec(portfolio_rule={"name": "buffered_risk_weighted", "hold_rank": 14})
    assert compute_experiment_id(left, snapshots_for(left)) != compute_experiment_id(right, snapshots_for(right))
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `pytest tests/unit/test_buffered_portfolio_policy.py tests/unit/test_experiment_spec.py -k 'buffered or portfolio_parameter' -v`

Expected: FAIL because the buffered policy/rule does not exist.

- [ ] **Step 3: Implement frozen models and spec union**

```python
class BufferedRiskWeightedPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_count: int = Field(default=10, ge=1)
    entry_rank: int = Field(default=10, ge=1)
    hold_rank: int = Field(default=15, ge=1)
    risk_lookback_days: int = Field(default=60, ge=2)
    min_risk_observations: int = Field(default=40, ge=2)
    volatility_floor_annualized: Decimal = Decimal("0.10")
    max_single_weight: Decimal = Decimal("0.15")
    rebalance_band_absolute: Decimal = Decimal("0.02")
    gross_exposure: Decimal = Decimal("1.00")
    long_only: bool = True
    leverage: bool = False
    weight_quantum: Decimal = Decimal("0.000000000001")
```

Add validators requiring finite positive Decimals, `entry_rank <= hold_rank`, `target_count == entry_rank`, `min_risk_observations <= risk_lookback_days`, `long_only is True`, and `leverage is False`. The formal first-run YAML contains exactly the approved defaults; later explicit parameter changes are permitted only as new strategy identities and never mutate that registered run. Define `portfolio_rule_version` from canonical policy JSON rather than a handwritten label. Update the formal YAML to select `name: buffered_risk_weighted`; keep equal weight available only for baseline/engineering specs.

- [ ] **Step 4: Verify compatibility and commit**

Run: `pytest tests/unit/test_buffered_portfolio_policy.py tests/unit/test_experiment_spec.py tests/integration/test_experiment_registry.py -v`

Expected: PASS, including existing equal-weight specs.

Commit: `git add src/stock_quant/portfolio/buffered_models.py src/stock_quant/research/spec.py configs/experiments/momentum_60d.yml tests/unit/test_buffered_portfolio_policy.py tests/unit/test_experiment_spec.py && git commit -m "feat: freeze buffered portfolio policy"`

### Task 2: Trusted 60-session risk estimation

**Files:**

- Create: `src/stock_quant/portfolio/risk_estimation.py`
- Test: `tests/unit/test_risk_estimation.py`

**Interfaces:**

- Produces `RISK_ESTIMATE_COLUMNS`, `RiskInputError`, and `estimate_risk(*, signal_date: date, symbols: Sequence[str], observations: pd.DataFrame, market_sessions: Sequence[date], policy: BufferedRiskWeightedPolicy) -> pd.DataFrame`.
- Input columns are exactly `trade_date`, `symbol`, `adjusted_close`, `quality_severity`, `missing_reason`; trusted carry rows use `missing_reason="suspended_verified"` and a positive carried `adjusted_close`.
- Output contains `symbol`, `window_start`, `window_end`, `real_close_observations`, `suspension_carry_days`, `risk_is_valid`, `risk_invalid_reason`, `raw_annualized_volatility`, and `applied_annualized_volatility` in symbol order.

- [ ] **Step 1: Write failing boundary and suspension tests**

```python
def test_exactly_forty_real_closes_is_valid(sixty_sessions, risk_rows):
    frame = mark_last_twenty_as_verified_suspension(risk_rows)
    result = estimate_risk(signal_date=sixty_sessions[-1], symbols=("000001.SZ",),
                           observations=frame, market_sessions=sixty_sessions,
                           policy=BufferedRiskWeightedPolicy())
    row = result.iloc[0]
    assert row.real_close_observations == 40
    assert row.suspension_carry_days == 20
    assert row.risk_is_valid


def test_unknown_missing_row_invalidates_risk(sixty_sessions, risk_rows):
    frame = risk_rows.drop(risk_rows.index[30])
    result = estimate_risk(signal_date=sixty_sessions[-1], symbols=("000001.SZ",),
                           observations=frame, market_sessions=sixty_sessions,
                           policy=BufferedRiskWeightedPolicy())
    assert result.iloc[0].risk_invalid_reason == "untrusted_missing_observation"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_risk_estimation.py -v`

Expected: FAIL because `risk_estimation.py` does not exist.

- [ ] **Step 3: Implement no-lookahead risk paths**

Select the final 60 confirmed sessions ending at `signal_date`; reject duplicate symbol/date rows and any observation after the signal date. A normal row counts as real only when it has a finite positive close, non-ERROR quality, and no missing reason. A `suspended_verified` row requires a prior trusted close, contributes a zero daily return, and does not increment real observations. Any other gap or quality error sets a stable invalid reason. Compute sample daily standard deviation with `ddof=1`, multiply by `sqrt(252)`, and apply the 10% floor only after preserving the raw value.

```python
raw_vol = Decimal(str(np.std(path_returns, ddof=1) * np.sqrt(252)))
applied_vol = max(raw_vol, policy.volatility_floor_annualized)
```

- [ ] **Step 4: Add future-row and determinism regressions**

```python
def test_appending_future_prices_cannot_change_signal_risk(request):
    before = estimate_risk(**request)
    request["observations"] = pd.concat([request["observations"], future_rows()])
    assert_frame_equal(estimate_risk(**request), before)


def test_input_row_order_does_not_change_output(request):
    shuffled = {**request, "observations": request["observations"].sample(frac=1, random_state=7)}
    assert_frame_equal(estimate_risk(**shuffled), estimate_risk(**request))
```

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/unit/test_risk_estimation.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/portfolio/risk_estimation.py tests/unit/test_risk_estimation.py && git commit -m "feat: estimate trusted portfolio risk"`

### Task 3: Two-stage ranking, buffered membership, and capped weights

**Files:**

- Create: `src/stock_quant/portfolio/buffered_risk_weight.py`
- Test: `tests/unit/test_buffered_risk_weight.py`
- Modify: `src/stock_quant/portfolio/__init__.py`

**Interfaces:**

- Produces `build_buffered_target(*, factors: FactorResult, risks: pd.DataFrame, previous_target_members: Sequence[str], policy: BufferedRiskWeightedPolicy) -> PortfolioConstructionResult`.
- Produces `allocate_capped_inverse_volatility(risks: Mapping[str, Decimal], policy: BufferedRiskWeightedPolicy) -> tuple[dict[str, Decimal], Decimal]`.
- `PortfolioConstructionResult` exposes immutable `target_members`, `target_weights`, `cash_weight`, and `audit_frame`; it never contains quantities, account values, or scenario fields.

- [ ] **Step 1: Write failing rank/buffer tests**

```python
def test_ties_sort_by_full_symbol_string(factor_result, valid_risks):
    result = build_buffered_target(factors=factor_result(equal_values=True), risks=valid_risks,
                                   previous_target_members=(), policy=BufferedRiskWeightedPolicy())
    assert result.audit_frame.query("raw_momentum_rank <= 3").symbol.tolist() == [
        "000001.SZ", "000002.SZ", "600000.SH"
    ]


def test_valid_incumbent_at_rank_fifteen_is_retained(factor_result, valid_risks):
    result = build_buffered_target(factors=factor_result(16), risks=valid_risks,
                                   previous_target_members=("000015.SZ",),
                                   policy=BufferedRiskWeightedPolicy())
    assert "000015.SZ" in result.target_members
    assert len(result.target_members) == 10


def test_invalid_top_rank_does_not_consume_entry_slot(factor_result, risks_with_invalid_first):
    result = build_buffered_target(factors=factor_result(12), risks=risks_with_invalid_first,
                                   previous_target_members=(), policy=BufferedRiskWeightedPolicy())
    assert len(result.target_members) == 10
    assert "000011.SZ" in result.target_members
```

- [ ] **Step 2: Run rank/buffer tests and confirm failure**

Run: `pytest tests/unit/test_buffered_risk_weight.py -k 'ties or incumbent or entry_slot' -v`

Expected: FAIL because the builder does not exist.

- [ ] **Step 3: Implement ranking and fold-local membership state**

Build `raw_momentum_rank` over factor-valid rows using `processed_value DESC, symbol ASC`. Left-join risk output, remove risk-invalid rows, and assign continuous `risk_eligible_rank` with the same order. Retain eligible previous targets through rank 15, then fill only from eligible ranks 1–10. Emit one audit row for every factor-valid candidate plus every previous target, with `member_status` from `retained|entered|exited|not_selected|risk_invalid` and an explicit reason. The caller supplies an empty `previous_target_members` on each fold's first signal.

- [ ] **Step 4: Write failing capped-allocation tests**

```python
def test_capped_simplex_hits_exposure_without_exceeding_cap():
    weights, cash = allocate_capped_inverse_volatility(
        {"000001.SZ": Decimal("0.05"), "000002.SZ": Decimal("0.20"),
         "600000.SH": Decimal("0.30"), "600001.SH": Decimal("0.40"),
         "600002.SH": Decimal("0.50"), "600003.SH": Decimal("0.60"),
         "600004.SH": Decimal("0.70")}, BufferedRiskWeightedPolicy())
    assert sum(weights.values()) + cash == Decimal("1.00")
    assert max(weights.values()) <= Decimal("0.15")


def test_six_members_leave_exactly_ten_percent_cash():
    weights, cash = allocate_capped_inverse_volatility(six_equal_risks(), BufferedRiskWeightedPolicy())
    assert set(weights.values()) == {Decimal("0.15")}
    assert cash == Decimal("0.10")
```

- [ ] **Step 5: Implement deterministic decimal allocation**

Set target exposure to `min(gross_exposure, count * max_single_weight)`. Iteratively cap overweight names and redistribute only across uncapped names by inverse applied volatility. Quantize down to `1e-12`; distribute remaining quanta in ascending symbol order while respecting the cap. Return unallocatable residue as cash and assert exact `sum(weights) + cash == 1.00` after quantization.

- [ ] **Step 6: Verify and commit**

Run: `pytest tests/unit/test_buffered_risk_weight.py tests/unit/test_buffered_portfolio_policy.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/portfolio/buffered_risk_weight.py src/stock_quant/portfolio/__init__.py tests/unit/test_buffered_risk_weight.py && git commit -m "feat: build buffered risk targets"`

### Task 4: Scenario-local sizing and 2% rebalance band

**Files:**

- Create: `src/stock_quant/portfolio/rebalance_band.py`
- Create: `src/stock_quant/backtest/weight_rebalancer.py`
- Test: `tests/unit/test_rebalance_band.py`
- Test: `tests/unit/test_weight_rebalancer.py`

**Interfaces:**

- Produces `RebalanceDecision`, `apply_rebalance_band(*, symbol, is_continuing, current_weight, target_weight, current_quantity, target_quantity, policy) -> RebalanceDecision`.
- Produces `WeightTargetRebalancer(periods: Mapping[date, WeightTargetPeriod], lot_size: int)` with `orders_for(day: date, account: Account) -> tuple[Order, ...]`, `signal_date_by_execution_day`, and `decision_frame()`.
- `WeightTargetPeriod` fixes signal date, target weights, signal-close valuation prices, previous/current common member sets, and `net_equity_prices`; it contains no execution-day data.

- [ ] **Step 1: Write failing band boundary tests**

```python
def test_difference_below_two_percent_is_suppressed():
    decision = apply_rebalance_band(symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.131"), target_weight=Decimal("0.150"),
        current_quantity=800, target_quantity=900, policy=BufferedRiskWeightedPolicy())
    assert not decision.should_order
    assert decision.reason == "within_rebalance_band"


def test_exactly_two_percent_rebalances():
    decision = apply_rebalance_band(symbol="000001.SZ", is_continuing=True,
        current_weight=Decimal("0.130"), target_weight=Decimal("0.150"),
        current_quantity=800, target_quantity=900, policy=BufferedRiskWeightedPolicy())
    assert decision.should_order


def test_entry_and_exit_never_use_band():
    assert apply_rebalance_band(symbol="000001.SZ", is_continuing=False,
        current_weight=Decimal("0"), target_weight=Decimal("0.01"),
        current_quantity=0, target_quantity=100, policy=BufferedRiskWeightedPolicy()).should_order
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `pytest tests/unit/test_rebalance_band.py tests/unit/test_weight_rebalancer.py -v`

Expected: FAIL because both modules are absent.

- [ ] **Step 3: Implement frozen-price scenario sizing**

On an execution day, value the scenario account using only the period's signal-close price map plus approved carried marks. Compute `target_quantity = floor(target_weight * signal_close_equity / signal_price / lot_size) * lot_size`. Compute current weights from the same equity and prices. Apply the band only when the symbol belongs to both previous and current common target-member sets and has positive target weight. New entries and zero-weight exits always reconcile; differences below one lot emit `below_one_lot` with no order. Generate deterministic sells then buys, symbol ascending, and record all decisions before returning orders.

```python
symbols = sorted(set(account_symbols) | set(period.target_weights))
target_lots = int((period.target_weights.get(symbol, ZERO) * equity / price) // lot_size)
target_quantity = target_lots * lot_size
```

- [ ] **Step 4: Add no-execution-data and scenario-divergence tests**

```python
def test_execution_open_change_does_not_change_submitted_orders(make_request):
    left = run_with_open(make_request(), symbol="000001.SZ", value=Decimal("10"))
    right = run_with_open(make_request(), symbol="000001.SZ", value=Decimal("12"))
    assert_frame_equal(left.submitted_orders, right.submitted_orders)
    assert not left.fills.equals(right.fills)


def test_same_common_weights_allow_scenario_quantities_to_diverge(periods, two_accounts):
    left, right = two_accounts
    rebalancer_a = WeightTargetRebalancer(periods, lot_size=100)
    rebalancer_b = WeightTargetRebalancer(periods, lot_size=100)
    assert rebalancer_a.orders_for(EXECUTION_DAY, left) != rebalancer_b.orders_for(EXECUTION_DAY, right)
```

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/unit/test_rebalance_band.py tests/unit/test_weight_rebalancer.py tests/integration/test_backtest_engine.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/portfolio/rebalance_band.py src/stock_quant/backtest/weight_rebalancer.py tests/unit/test_rebalance_band.py tests/unit/test_weight_rebalancer.py tests/integration/test_backtest_engine.py && git commit -m "feat: reconcile scenario weight targets"`

### Task 5: Fold runner integration and audit artifacts

**Files:**

- Modify: `src/stock_quant/research/walk_forward/runner.py`
- Modify: `src/stock_quant/research/runner.py`
- Modify: `src/stock_quant/research/models.py`
- Modify: `src/stock_quant/reporting/html.py`
- Modify: `src/stock_quant/reporting/templates/experiment.html.j2`
- Test: `tests/integration/test_buffered_strategy_runner.py`
- Modify: `tests/integration/test_reports.py`

**Interfaces:**

- Portfolio selection returns a sequence of `WeightTargetPeriod` objects and common `portfolio_construction.parquet` rows per fold.
- Each scenario writes `rebalance_decisions.parquet` beside its submitted orders/fills/rejections.
- Fold artifacts include `folds/<fold_id>/portfolio_construction.parquet`; scenario artifacts include `folds/<fold_id>/backtest/<scenario>/rebalance_decisions.parquet`.

- [ ] **Step 1: Write failing end-to-end construction tests**

```python
def test_fold_first_signal_resets_buffer_state(buffered_runner):
    result = buffered_runner.run(two_fold_request())
    first_rows = [fold.construction.iloc[0] for fold in result.executed_folds]
    assert all(row.previous_target_member is False for row in first_rows)


def test_all_scenarios_share_members_and_weights(buffered_runner):
    result = buffered_runner.run(request_with_three_cost_scenarios())
    common = result.portfolio_construction[["signal_date", "symbol", "target_weight"]]
    for scenario in result.scenarios:
        assert set(scenario.rebalance_decisions.symbol) <= set(common.symbol)


def test_runner_persists_complete_construction_audit(published_experiment):
    frame = pd.read_parquet(published_experiment / "folds/fold-2020/portfolio_construction.parquet")
    assert {"raw_momentum_rank", "risk_eligible_rank", "real_close_observations",
            "member_status", "raw_weight", "target_weight", "cash_weight",
            "portfolio_rule_version"} <= set(frame.columns)
```

- [ ] **Step 2: Run integration tests and confirm failure**

Run: `pytest tests/integration/test_buffered_strategy_runner.py -v`

Expected: FAIL because the Walk-Forward runner only builds equal-weight quantity targets.

- [ ] **Step 3: Integrate pure common targets before scenario replay**

For each fold, pass an empty previous-member tuple to its first signal and then only the prior common target members. Read risk observations from the frozen adjusted-price dataset, call Tasks 2–3, and materialize all common periods before starting any cost scenario. Build one `WeightTargetRebalancer` per scenario so decision state cannot cross accounts. Add every new artifact hash to the fold manifest; absence, duplicate signal/symbol rows, or a target outsider makes the fold FAILED rather than silently falling back to equal weight.

- [ ] **Step 4: Extend report assertions**

```python
def test_buffered_report_explains_turnover_sources(rendered_html):
    assert "成员变化换手" in rendered_html
    assert "连续持仓再平衡换手" in rendered_html
    assert "带宽抑制金额" in rendered_html
    assert "手数抑制金额" in rendered_html
```

Render common policy/hash, per-fold member changes, risk-invalid counts, achieved gross exposure, cash residue, and scenario band/lot suppression. Do not claim that suppressed turnover is an execution rejection.

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/integration/test_buffered_strategy_runner.py tests/integration/test_research_runner.py tests/integration/test_reports.py -v`

Expected: PASS.

Commit: `git add src/stock_quant/research src/stock_quant/reporting tests/integration/test_buffered_strategy_runner.py tests/integration/test_research_runner.py tests/integration/test_reports.py && git commit -m "feat: publish buffered strategy artifacts"`

### Task 6: Operator documentation and full verification

**Files:**

- Modify: `README.md`
- Modify: `RUNBOOK.md`
- Modify: `docs/operations/phase-one-validation.md`
- Modify: `PROJECT_MEMORY.md`

**Interfaces:**

- Documents the frozen policy, common-target/scenario-account boundary, artifact locations, and failure semantics.
- Marks the strategy implementation complete only after all verification commands pass.

- [ ] **Step 1: Document the operating and audit procedure**

Document how to identify `portfolio_rule_version`; verify 60/40 risk counts; inspect retained/entered/exited members; recompute capped weights and cash; compare common targets across scenarios; and distinguish `within_rebalance_band`, `below_one_lot`, execution rejection, and risk invalidity. State that parameters cannot be changed after viewing fold results.

- [ ] **Step 2: Run the focused strategy suite**

Run: `pytest tests/unit/test_buffered_portfolio_policy.py tests/unit/test_risk_estimation.py tests/unit/test_buffered_risk_weight.py tests/unit/test_rebalance_band.py tests/unit/test_weight_rebalancer.py tests/integration/test_buffered_strategy_runner.py -v`

Expected: PASS.

- [ ] **Step 3: Run full project verification**

Run: `pytest -q`

Expected: PASS.

Run: `ruff check src tests project`

Expected: PASS.

Run: `git diff --check`

Expected: no output and exit 0.

- [ ] **Step 4: Update project memory and commit**

After Steps 2–3 pass, mark only the buffered portfolio implementation complete in `PROJECT_MEMORY.md`; leave the one-time challenge item pending until its separate plan passes.

Commit: `git add README.md RUNBOOK.md docs/operations/phase-one-validation.md PROJECT_MEMORY.md && git commit -m "docs: operate buffered momentum strategy"`

## Self-Review

- Spec coverage: Tasks 1–3 cover fixed identity, symbol tie-breaks, dual ranks, 60/40 trusted risk, buffering, decimal capped weights, caps and residual cash. Task 4 covers common weights, scenario quantities, signal-close valuation, strict band boundary, lot suppression, and execution-data isolation. Task 5 covers fold reset, artifacts, reporting, and failure behavior. Task 6 closes operating evidence.
- Placeholder scan: every task names concrete files, interfaces, test examples, commands, expected outcomes, and commit paths; no deferred implementation instruction remains.
- Type consistency: `BufferedRiskWeightedPolicy` feeds risk, allocation, and rebalance modules; `PortfolioConstructionResult` feeds `WeightTargetPeriod`; one period map feeds each scenario's `WeightTargetRebalancer`; common and scenario audit frames feed fold manifests and reporting.
- Dependency check: the plan consumes the approved Walk-Forward fold and snapshot interfaces and does not recreate them. The separate challenge plan consumes this plan's immutable published artifacts.
