# Point-in-Time Index Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make formal Research consume a frozen, evidence-backed `csi300` historical membership set for every signal day instead of all `security_master` symbols.

**Architecture:** Immutable membership facts are added to the dataset. A resolver separately intersects them with master boundaries and announcement visibility, freezes an explicit definition version, and supplies daily members to the factor layer. Acceptance validates evidence, continuity and cardinality before factor work.

**Tech Stack:** Python 3.10, Pydantic 2, pandas, PyArrow, DuckDB, PyYAML, Typer, pytest, SHA-256

**Spec:** `docs/superpowers/specs/2026-09-09-point-in-time-index-universe-design.md`

## Global Constraints

- First formal definition: `csi300`; IDs must be `csi300|csi500|csi1000|sse50|sse180|szse100|custom_[a-z0-9_]+`.
- Facts contain qualifications only—never ST, suspension, prices, factor values, cash or orders.
- Raw/resolved effective dates are inclusive. Null raw end means no removal observed in this immutable version.
- Formal Research fails before factor calculation on evidence, cardinality, boundary, announcement or coverage error; Engineering remains UNTRUSTED.
- Resolution never overwrites source facts. Offline synthetic tests only.
- Before implementation, create an isolated worktree with `superpowers:using-git-worktrees` because the main checkout is dirty.

---

### Task 1: Membership fact contract and raw/resolved dates

**Files:**

- Create: `src/stock_quant/data_model/universe_membership.py`
- Modify: `src/stock_quant/data_model/schemas.py`
- Modify: `src/stock_quant/data_model/dataset.py`
- Test: `tests/unit/test_universe_membership.py`

**Interfaces:**

- Produces `MembershipStatus`, `MembershipReason`, `MembershipFact`, `ResolvedMembership`, `UniverseBoundaryError`, `membership_frame`, `membership_content_hash`, and `resolve_memberships`.
- Produces/registers `UNIVERSE_MEMBERSHIP_COLUMNS` and `UNIVERSE_MEMBERSHIP_SCHEMA`.

- [ ] **Step 1: Write failing tests**

```python
def test_regular_removal_has_an_inclusive_end_before_removal_day():
    value = MembershipFact.model_validate(make_fact(
        raw_effective_to=date(2020, 6, 14), status="removed", reason="regular_rebalance"
    ))
    assert value.raw_effective_to == date(2020, 6, 14)


def test_resolver_keeps_raw_start_and_applies_listing_start():
    resolved = resolve_memberships((fact(raw_effective_from=date(2019, 1, 1)),), master())[0]
    assert resolved.raw_effective_from == date(2019, 1, 1)
    assert resolved.effective_from == date(2020, 1, 2)
    assert resolved.boundary_adjustment_reason == "before_listing"


def test_ambiguous_delisting_boundary_is_rejected():
    with pytest.raises(UniverseBoundaryError, match="last_tradable_date"):
        resolve_memberships((fact(status="removed", reason="delisting"),), master(last_tradable_date=None))
```

- [ ] **Step 2: Verify the tests fail**

Run `pytest tests/unit/test_universe_membership.py -v`; expect import failure.

- [ ] **Step 3: Implement minimal strict models**

Use frozen Pydantic models. `MembershipFact` fields are `universe_id`, `symbol`, raw start/end, `announcement_date`, status, reason, source, URL, snapshot hash and document hash. Validate canonical symbols, IDs, 64-character lowercase hashes, date order, nonempty evidence, non-overlap, and status/reason consistency. Reasons are exactly `initial_constituent`, `regular_rebalance`, `temporary_adjustment`, `delisting`, `merger_or_reorganization`, `correction`.

Implement `ResolvedMembership` separately. It uses `max(raw_start, list_date)` and a finite `min(raw_end, last_tradable_date)` when both exist; preserves raw values and records `before_listing`/`after_delisting`. Never infer last trade date from a delisting notice.

- [ ] **Step 4: Add Arrow schema registration**

Add these fields in order: `universe_id`, `symbol`, `raw_effective_from`, `raw_effective_to`, `announcement_date`, `status`, `reason`, `source`, `source_url`, `snapshot_sha256`, `source_document_sha256`. Dates use `pa.date32()`, all other fields `pa.string()`. Register only the raw table.

- [ ] **Step 5: Verify and commit**

Run `pytest tests/unit/test_universe_membership.py tests/unit/test_normalize.py -v`; expect PASS. Commit only Task 1 paths with message `feat: add immutable index membership facts`.

### Task 2: Versioned definition and daily membership resolver

**Files:**

- Create: `src/stock_quant/research/universe.py`
- Create: `configs/universes/csi300.yml`
- Test: `tests/unit/test_research_universe.py`

**Interfaces:**

- Produces `UniverseDefinition`, `UniverseResolver`, `UniverseCoverageError`, `load_universe_definition`.
- `members_on(day) -> tuple[str, ...]`; `snapshot_for(day) -> str`.

- [ ] **Step 1: Write failing day-visibility tests**

```python
def test_member_is_hidden_until_announcement_date(resolver):
    assert resolver.members_on(date(2020, 6, 14)) == ()
    assert resolver.members_on(date(2020, 6, 15)) == ("600000.SH",)


def test_removed_member_is_absent_after_end(resolver):
    assert "600000.SH" in resolver.members_on(date(2020, 6, 14))
    assert "600000.SH" not in resolver.members_on(date(2020, 6, 15))


def test_daily_snapshot_ignores_source_row_order(resolver, reordered_resolver):
    assert resolver.snapshot_for(date(2020, 6, 15)) == reordered_resolver.snapshot_for(date(2020, 6, 15))
```

- [ ] **Step 2: Verify failure**

Run `pytest tests/unit/test_research_universe.py -v`; expect missing-module failure.

- [ ] **Step 3: Implement definition and resolver**

Definition fields: `schema_version=1`, ID, `rules_version`, membership-table SHA-256, coverage start/end, evidence-summary SHA-256. Its canonical JSON SHA-256 is the version. Validate table hash against pinned context. `members_on` applies only resolved date bounds and `announcement_date <= day`, returns sorted symbols, and raises outside coverage. It must not look at factor/market/execution state. `snapshot_for` hashes canonical ID, date and symbols.

- [ ] **Step 4: Add csi300 template**

Add a commented template with required actual hashes and coverage. State in comments that placeholders cannot be used by formal runs. Tests use temporary explicit definitions.

- [ ] **Step 5: Verify and commit**

Run `pytest tests/unit/test_research_universe.py -v`; expect PASS. Commit Task 2 paths as `feat: resolve index members by signal date`.

### Task 3: Membership import and acceptance gate

**Files:**

- Create: `project/refresh_index_membership.py`
- Modify: `src/stock_quant/data_pipeline.py`
- Modify: `src/stock_quant/data_quality/raw_checks.py`
- Modify: `src/stock_quant/research/acceptance/models.py`
- Modify: `src/stock_quant/research/acceptance/checks.py`
- Test: `tests/unit/test_index_membership_checks.py`
- Test: `tests/integration/test_data_pipeline.py`

**Interfaces:**

- Produces `validate_membership_facts(frame, *, calendar, expected_sizes) -> list[Issue]`.
- Produces required acceptance result `index_membership_evidence`.

- [ ] **Step 1: Write failing fatal-check tests**

```python
def test_csi300_cardinality_mismatch_is_fatal(calendar, valid_facts):
    issues = validate_membership_facts(valid_facts.iloc[:-1], calendar=calendar, expected_sizes={"csi300": 300})
    assert ("UNIVERSE_MEMBER_COUNT_MISMATCH", "FATAL") in {
        (issue.code, issue.severity.value) for issue in issues
    }


def test_missing_snapshot_evidence_is_fatal(calendar, valid_facts):
    valid_facts.loc[0, "snapshot_sha256"] = ""
    assert "UNIVERSE_EVIDENCE_MISSING" in {
        issue.code for issue in validate_membership_facts(valid_facts, calendar=calendar, expected_sizes={"csi300": 300})
    }
```

- [ ] **Step 2: Verify failure**

Run `pytest tests/unit/test_index_membership_checks.py -v`; expect missing validator.

- [ ] **Step 3: Implement conversion and pure checks**

The refresh script requires explicit universe ID, source input, source/document snapshot hashes, effective date and announcement date. It normalizes to facts and has no network dependency. Validator rejects missing evidence, unknown symbols, overlap, announcement-before-use, empty master intersection, uncertain delisting endpoint and coverage gaps. It checks 300 members on every trading day or stable interval unless an immutable official exception evidence record exists.

- [ ] **Step 4: Extend real-data acceptance**

Make `index_membership_evidence` mandatory. Deterministic details contain coverage, counts, hashes and error codes only. It fails on missing table, mismatched definition hash, unlocatable evidence or any FATAL issue. Update acceptance fixtures with this required synthetic pass result.

- [ ] **Step 5: Verify and commit**

Run `pytest tests/unit/test_index_membership_checks.py tests/unit/test_acceptance_models.py tests/integration/test_data_pipeline.py -v`; expect PASS. Commit as `feat: gate research on index membership evidence`.

### Task 4: Freeze definition and preflight Research

**Files:**

- Modify: `src/stock_quant/research/spec.py`
- Modify: `src/stock_quant/research/runner.py`
- Modify: `src/stock_quant/research/registry.py`
- Modify: `src/stock_quant/reporting/html.py`
- Modify: `configs/experiments/momentum_60d.yml`
- Test: `tests/unit/test_experiment_spec.py`
- Test: `tests/integration/test_research_runner.py`

**Interfaces:**

- `CURRENT` resolves through a `UniverseDefinition`, not legacy `configs/universe.yml`.
- Artifacts persist definition identity plus all signal-day snapshots.

- [ ] **Step 1: Write failing preflight tests**

```python
def test_definition_version_changes_experiment_id(make_spec):
    assert compute_experiment_id(make_spec(universe_version="a" * 64)) != compute_experiment_id(
        make_spec(universe_version="b" * 64)
    )


def test_invalid_membership_fails_before_factor_artifact(runner, formal_spec):
    result = runner.run(formal_spec)
    assert result.exit_code != 0
    assert result.failed_stage == "universe_acceptance"
    assert not result.factor_artifact_exists
```

- [ ] **Step 2: Verify failure**

Run `pytest tests/integration/test_research_runner.py -k 'membership or definition' -v`; expect Runner’s current static-master behavior to fail the assertion.

- [ ] **Step 3: Implement definition pinning/preflight**

Pin dataset and data acceptance, open the fixed context, load and validate the selected definition, then freeze `universe_version`. On failure write a redacted preflight manifest with `failed_stage="universe_acceptance"`; do not produce factors or fall back to master symbols. Persist universe ID/version/table hash/rules version and a `{ISO date: SHA-256}` daily snapshot map in manifests, metrics and report context.

- [ ] **Step 4: Update formal config**

Add `universe_definition: csi300` while retaining `universe_version: CURRENT`. Legacy YAML is engineering-only. Build temporary definitions in integration fixtures.

- [ ] **Step 5: Verify and commit**

Run `pytest tests/unit/test_experiment_spec.py tests/integration/test_research_runner.py tests/integration/test_experiment_registry.py -v`; expect PASS. Commit as `feat: freeze index universe in research identity`.

### Task 5: Membership-first factor filtering

**Files:**

- Modify: `src/stock_quant/research/runner.py`
- Modify: `src/stock_quant/factors/base.py`
- Modify: `src/stock_quant/factors/momentum.py`
- Test: `tests/unit/test_momentum.py`
- Test: `tests/integration/test_factor_no_lookahead.py`

**Interfaces:**

- Factor context receives `members_on(day)` and `membership_snapshot_for(day)`.
- Every newly generated portfolio target must be a member on its signal date.

- [ ] **Step 1: Write failing order tests**

```python
def test_non_member_with_best_factor_is_not_signaled(dataset, context):
    result = Momentum60().compute(dataset, context)
    assert "999999.SH" not in result.loc[result.trade_date.eq(date(2020, 3, 31)), "symbol"].tolist()


def test_removed_member_makes_no_new_signal(dataset, context):
    result = Momentum60().compute(dataset, context)
    assert "600000.SH" not in result.loc[result.trade_date.eq(date(2020, 6, 15)), "symbol"].tolist()
```

- [ ] **Step 2: Verify failure**

Run `pytest tests/unit/test_momentum.py tests/integration/test_factor_no_lookahead.py -k member -v`; expect failure with the static symbol adapter.

- [ ] **Step 3: Implement exact filtering order**

Keep the full fixed price surface. On each signal day, first filter to `set(context.members_on(day))`; only then apply factor missing/quality/minimum-history filters; leave tradability to the existing execution path. Include the daily snapshot hash in factor-stage metadata. Assert all new targets are day members. Keep existing out-of-index holdings for normal execution/exit accounting; removal is not a forced sell.

- [ ] **Step 4: Verify and commit**

Run `pytest tests/unit/test_momentum.py tests/integration/test_factor_no_lookahead.py tests/integration/test_research_runner.py -v`; expect PASS. Commit as `feat: filter factors by point-in-time index members`.

### Task 6: Operator workflow and full verification

**Files:**

- Modify: `README.md`
- Modify: `RUNBOOK.md`
- Modify: `docs/operations/phase-one-validation.md`
- Modify: `PROJECT_MEMORY.md`
- Test: `tests/integration/test_cli.py`

**Interfaces:** Documents source snapshot → import → dataset publication → definition hash → acceptance → Research; no bypass flag.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_membership_preflight_returns_nonzero_without_factor(cli, bad_spec):
    result = cli.run(["research", "run", "--spec", str(bad_spec)])
    assert result.exit_code != 0
    assert "universe_acceptance" in result.output


def test_membership_import_requires_snapshot_hash(cli, source_file):
    result = cli.run(["data", "index-membership", "prepare", "--input", str(source_file)])
    assert result.exit_code != 0
    assert "--snapshot-sha256" in result.output
```

- [ ] **Step 2: Verify failure**

Run `pytest tests/integration/test_cli.py -k membership -v`; expect failure until CLI output/help exists.

- [ ] **Step 3: Document explicit operating procedure**

Require official/corroborated source documents, raw snapshot storage, explicit converter arguments, immutable dataset publication, explicit definition hashes, acceptance publication, then Research. Explicitly state that missing proof, wrong count and ambiguous delisting boundaries stop work and require correction; never bypass them. Do not commit data payloads or credentials.

- [ ] **Step 4: Verify and commit**

Run `pytest -q` and `ruff check src tests project`; expect PASS. Run `git diff --check` and inspect `git status --short`; commit only Task 6 paths as `docs: operate point-in-time index universe`.

## Self-Review

- Tasks 1–2 cover facts, raw/resolved ranges, identities, naming and announcement visibility.
- Task 3 covers evidence, cardinality and loud acceptance failure.
- Task 4 freezes and audits universe identity; Task 5 enforces membership before factor/trading filtering; Task 6 makes the live workflow explicit and verifies offline behavior.
- Interfaces are ordered: facts → resolved facts → resolver → factor context → frozen Research artifacts.
