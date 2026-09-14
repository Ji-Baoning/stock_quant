Paths: src/stock_quant/data_sources/**, src/stock_quant/data_model/**, src/stock_quant/data_quality/**, src/stock_quant/data_pipeline.py, and data-focused tests.
Read first: docs/architecture/data-flow.md, docs/architecture/invariants.md, README.md, and the applicable source contract or quality test.

# Data boundaries

Keep supplier adapters, canonical data models, and quality gates separate.
Follow the existing source contract rather than making a provider-specific
failure look like usable data.

## Invariants

- Published datasets are content-addressed and immutable; publish a new version
  rather than editing a published version in place.
- Preserve raw provenance and quality findings. Do not silently turn missing,
  malformed, or error-quality observations into clean observations.
- `adjusted_bar` is derived total-return evidence; execution and valuation use
  clean unadjusted daily-bar prices where the existing contracts require them.
- Keep credentials and supplier tokens out of committed configuration, fixtures,
  logs, and reports.

## Verify

Run the smallest affected unit tests first, for example:

```bash
pytest tests/unit/test_clean.py tests/unit/test_adjusted_bar.py -q
pytest tests/integration/test_data_pipeline.py -q
```

For an explicitly scoped project-root data operation, use the documented
`--root <PROJECT_ROOT>` command in `RUNBOOK.md`; do not mutate a dataset while
an operation is running.
