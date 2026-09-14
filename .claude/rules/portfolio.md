Paths: src/stock_quant/portfolio/**, portfolio construction in src/stock_quant/research/**, and portfolio-focused tests.
Read first: docs/architecture/invariants.md, README.md, RUNBOOK.md, and the applicable portfolio or walk-forward ADR.

# Portfolio boundaries

Portfolio construction converts eligible research candidates into frozen target
weights. Keep it separate from execution simulation and preserve the decision
evidence needed to audit a rebalance.

## Invariants

- Do not bypass point-in-time eligibility, risk-observation, lot, exposure, or
  rebalance-band checks to force a holding into a portfolio.
- Keep rule parameters and the resulting `portfolio_rule_version` tied to the
  frozen experiment identity; do not tune them after examining held-out results.
- Preserve rejected or suppressed decisions as evidence. Execution costs and
  fills remain execution concerns, not reasons to rewrite construction history.
- Keep the existing long-only, no-leverage constraints where the selected rule
  declares them.

## Verify

```bash
pytest tests/unit/test_equal_weight.py tests/unit/test_risk_estimation.py -q
pytest tests/unit/test_buffered_risk_weight.py tests/unit/test_rebalance_band.py -q
pytest tests/integration/test_buffered_strategy_runner.py -q
```

If a portfolio change touches research orchestration, also follow
`.claude/rules/research.md`.
