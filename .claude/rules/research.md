Paths: src/stock_quant/research/**, src/stock_quant/backtest/**, and research/backtest integration tests.
Read first: docs/architecture/data-flow.md, docs/architecture/invariants.md, README.md, RUNBOOK.md, and the applicable research ADR.

# Research and backtest boundaries

Treat formal research and engineering diagnostics as distinct trust modes. Keep
research orchestration, chronological replay, and reporting evidence explicit
and auditable.

## Invariants

- Formal research requires the applicable accepted data evidence and
  point-in-time universe evidence before factor, portfolio, or backtest work.
- Signals, membership, and eligibility must use only information available at
  the relevant decision time; never introduce look-ahead access.
- Freeze the declared specification and experiment inputs before producing
  results; preserve failed folds and rejection evidence rather than hiding them.
- Engineering backtests are diagnostic-only and must not be represented as
  formal accepted research.

## Verify

Run the closest affected tests, then the cross-boundary checks when applicable:

```bash
pytest tests/integration/test_factor_no_lookahead.py tests/integration/test_research_runner.py -q
pytest tests/integration/test_backtest_engine.py tests/integration/test_walk_forward_runner.py -q
```

Use the explicit project root in operational commands. Read the relevant
`docs/operations/` record before repeating a real-data or acceptance workflow.
