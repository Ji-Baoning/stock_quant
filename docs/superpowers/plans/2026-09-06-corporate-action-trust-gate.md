# Corporate-Action Trust Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Use focused tests per task and run the entire suite once at the end.

**Goal:** Block untrusted company-action data from formal research while allowing clearly labelled engineering diagnostics.

**Architecture:** Add immutable corporate_action_coverage evidence beside action facts. The data pipeline records per-symbol endpoint outcomes; ResearchRunner checks pinned evidence before account construction. Formal research rejects untrusted input; engineering runs only as UNTRUSTED.

**Tech Stack:** Python 3.10, pandas, PyArrow/Parquet, DuckDB, Pydantic v2, Typer, pytest.

**Spec:** docs/superpowers/specs/2026-09-06-corporate-action-trust-gate-design.md

## Efficiency Rules

- Preserve uncommitted work and stage only files owned by a task.
- Reuse existing source stubs and fixtures; do not create a parallel fixture framework.
- Per task: add all related failures, run one focused selection, implement, rerun once.
- Run pytest -q and Ruff only at the final checkpoint.
- Empty facts are trusted only when endpoint success creates explicit VERIFIED_EMPTY evidence.

---

### Task 1: Publish coverage evidence

**Files:** Create src/stock_quant/data_model/corporate_action_coverage.py and tests/unit/test_corporate_action_coverage.py. Modify src/stock_quant/data_model/schemas.py, src/stock_quant/data_model/dataset.py, src/stock_quant/data_pipeline.py, and tests/integration/test_data_pipeline.py.

**Produces:** CoverageStatus (VERIFIED, VERIFIED_EMPTY, UNTRUSTED); CoverageReason (SOURCE_FETCH_FAILED, SOURCE_NOT_REQUESTED, COVERAGE_INCOMPLETE, FACTS_INCOMPLETE, SOURCE_CONFLICT, UNSUPPORTED_ACTION); coverage_frame(records) with symbol, window_start, window_end, status, reason, sources, snapshot_hashes, checked_at; registered table corporate_action_coverage.

- [ ] **Step 1: Add failing unit and pipeline tests**

~~~python
def test_coverage_distinguishes_verified_empty_from_failure():
    frame = coverage_frame([
        coverage_record("600000.SH", date(2024, 1, 1), date(2024, 12, 31),
                        CoverageStatus.VERIFIED_EMPTY),
        coverage_record("600001.SH", date(2024, 1, 1), date(2024, 12, 31),
                        CoverageStatus.UNTRUSTED, CoverageReason.SOURCE_FETCH_FAILED),
    ])
    assert list(frame.columns) == CORPORATE_ACTION_COVERAGE_COLUMNS
    assert frame.status.tolist() == ["VERIFIED_EMPTY", "UNTRUSTED"]

def test_update_records_empty_success_and_fetch_failure(project):
    ok = DataPipeline(project.root, sources=_successful_empty_action_sources()).update(_request())
    bad = DataPipeline(project.root, sources=_one_failing_action_endpoint()).update(_request())
    with DatasetReader(project.root).open(ok.dataset_ref.version) as context:
        assert set(context.read("corporate_action_coverage").status) == {"VERIFIED_EMPTY"}
    with DatasetReader(project.root).open(bad.dataset_ref.version) as context:
        assert "SOURCE_FETCH_FAILED" in set(context.read("corporate_action_coverage").reason)
~~~

- [ ] **Step 2: Verify failure once**

Run: pytest tests/unit/test_corporate_action_coverage.py tests/integration/test_data_pipeline.py -k 'coverage or corporate_action' -v

Expected: FAIL; the evidence model/table does not exist.

- [ ] **Step 3: Implement and verify once**

Create enums, deterministic frame builder and Arrow schema; register it in STANDARDIZED_SCHEMAS. Make the action refresh return facts plus per-symbol/window evidence. Store successful raw snapshot hashes; only both successful no-event endpoint responses yield VERIFIED_EMPTY. Everything else yields UNTRUSTED with a stable reason.

Run: pytest tests/unit/test_corporate_action_coverage.py tests/integration/test_data_pipeline.py -k 'coverage or corporate_action' -v

Expected: PASS.

- [ ] **Step 4: Commit**

~~~bash
git add src/stock_quant/data_model/corporate_action_coverage.py src/stock_quant/data_model/schemas.py src/stock_quant/data_model/dataset.py src/stock_quant/data_pipeline.py tests/unit/test_corporate_action_coverage.py tests/integration/test_data_pipeline.py
git commit -m "feat: publish corporate action coverage evidence"
~~~

### Task 2: Gate research on pinned trust evidence

**Files:** Create src/stock_quant/research/trust.py and tests/unit/test_research_trust.py. Modify src/stock_quant/research/spec.py, src/stock_quant/research/models.py, src/stock_quant/research/runner.py, tests/integration/test_research_runner.py, and tests/integration/test_experiment_registry.py.

**Produces:** DataTrustMode.RESEARCH and DataTrustMode.ENGINEERING; CorporateActionTrustDecision(trusted, reasons); evaluate_corporate_action_trust(coverage, symbols, window_start, window_end); frozen spec/run-manifest trust fields; ExperimentEvaluation.UNTRUSTED.

- [ ] **Step 1: Add all related failing tests**

~~~python
def test_trust_requires_full_verified_coverage():
    decision = evaluate_corporate_action_trust(
        coverage=coverage_frame([verified("600000.SH", date(2024, 1, 1), date(2024, 6, 30))]),
        symbols={"600000.SH"}, window_start=date(2024, 1, 1), window_end=date(2024, 12, 31),
    )
    assert not decision.trusted
    assert decision.reasons[0].code == "COVERAGE_INCOMPLETE"

def test_research_rejects_untrusted_but_engineering_is_untrusted(env):
    _publish_dataset_with_untrusted_coverage(env.root)
    runner = ResearchRunner(env.root, config_root=_REPO_ROOT)
    with pytest.raises(ResearchRunFailed, match="corporate action trust"):
        runner.run(_SPEC)
    debug = runner.run(_SPEC, trust_mode=DataTrustMode.ENGINEERING)
    assert json.loads((debug.path / "metrics.json").read_text())["evaluation"]["status"] == "UNTRUSTED"

def test_research_accepts_verified_empty_coverage(env):
    _publish_dataset_with_verified_empty_coverage(env.root)
    assert ResearchRunner(env.root, config_root=_REPO_ROOT).run(_SPEC).manifest.status in ("ACCEPTED", "REJECTED")
~~~

- [ ] **Step 2: Verify failure once**

Run: pytest tests/unit/test_research_trust.py tests/integration/test_research_runner.py tests/integration/test_experiment_registry.py -k 'trust or untrusted or verified_empty' -v

Expected: FAIL; no evaluator or mode exists.

- [ ] **Step 3: Implement and verify once**

Require VERIFIED or VERIFIED_EMPTY coverage spanning the complete execution window for every possible holding. Freeze/persist mode and decision. In RESEARCH, fail before BacktestEngine construction and create no experiment directory. In ENGINEERING, run but force UNTRUSTED; the formal registry rejects it while debug retains diagnostics.

Run: pytest tests/unit/test_research_trust.py tests/integration/test_research_runner.py tests/integration/test_experiment_registry.py -k 'trust or untrusted or verified_empty' -v

Expected: PASS.

- [ ] **Step 4: Commit**

~~~bash
git add src/stock_quant/research/trust.py src/stock_quant/research/spec.py src/stock_quant/research/models.py src/stock_quant/research/runner.py tests/unit/test_research_trust.py tests/integration/test_research_runner.py tests/integration/test_experiment_registry.py
git commit -m "feat: gate research on corporate action trust"
~~~

### Task 3: Expose the boundary and make one final verification pass

**Files:** Modify src/stock_quant/cli.py, src/stock_quant/reporting/html.py, src/stock_quant/reporting/templates/experiment.html.j2, tests/integration/test_cli.py, tests/integration/test_reports.py, tests/integration/test_end_to_end.py, RUNBOOK.md, and docs/operations/phase-one-validation.md.

**Produces:** Formal research always selects RESEARCH with no bypass. Debug backtest accepts --engineering. Reports read the frozen decision and show trusted or untrusted state.

- [ ] **Step 1: Add focused public-surface failures**

~~~python
def test_formal_research_has_no_bypass(cli_runner):
    assert "--engineering" not in cli_runner.invoke(app, ["research", "run", "--help"]).output

def test_debug_and_report_show_untrusted_reason(cli_runner, root):
    result = cli_runner.invoke(app, ["backtest", "momentum_60d", "--root", str(root), "--engineering"])
    assert result.exit_code == 0 and "trust=UNTRUSTED" in result.output
    html = render_experiment_report(_experiment_input(
        corporate_action_trust={"trusted": False, "reasons": [
            {"symbol": "600000.SH", "code": "SOURCE_FETCH_FAILED"}
        ]}
    ))
    assert "数据可信度未通过" in html and "600000.SH" in html

def test_dataset_fixture_publishes_coverage(project):
    result = DataPipeline(project.root, sources=_all_stubs()).update(_request())
    with DatasetReader(project.root).open(result.dataset_ref.version) as context:
        assert not context.read("corporate_action_coverage").empty
~~~

- [ ] **Step 2: Verify failure once**

Run: pytest tests/integration/test_cli.py tests/integration/test_reports.py tests/integration/test_end_to_end.py -k 'trust or engineering or coverage' -v

Expected: FAIL; no CLI/report trust surface and fixtures lack explicit evidence.

- [ ] **Step 3: Implement, verify, then run final checks**

Keep formal research fixed to RESEARCH. Add --engineering only to debug. Print trust=<state>. Render an untrusted banner/table with symbol, date range, reason, and snapshot hash; render a positive verified state otherwise. Update stubs to return explicit successful no-event responses and document coverage inspection plus diagnostic-only engineering.

Run: pytest tests/integration/test_cli.py tests/integration/test_reports.py tests/integration/test_end_to_end.py -k 'trust or engineering or coverage' -v

Expected: PASS.

Run: pytest -q && ruff check src tests && ruff format --check src tests

Expected: PASS; no external-network tests.

- [ ] **Step 4: Commit**

~~~bash
git add src/stock_quant/cli.py src/stock_quant/reporting/html.py src/stock_quant/reporting/templates/experiment.html.j2 tests/integration/test_cli.py tests/integration/test_reports.py tests/integration/test_end_to_end.py RUNBOOK.md docs/operations/phase-one-validation.md
git commit -m "feat: expose corporate action trust boundary"
~~~

## Plan Self-Review

- Each requirement has one owning task: immutable evidence, strict gate, engineering diagnostics, public visibility and operational use.
- Each task has exactly one focused red/green cycle; full verification occurs only once.
- The plan reuses current fixtures and avoids network validation and repeated exploratory reads.

