Paths: src/stock_quant/config.py, src/stock_quant/bootstrap.py, src/stock_quant/cli.py, project/**, templates/project-config/**, RUNBOOK.md, and docs/operations/**.
Read first: README.md, RUNBOOK.md, docs/architecture/data-flow.md, docs/architecture/invariants.md, and the relevant dated operations record.

# Configuration and operations boundaries

Repository templates and a caller's project root are different scopes. Treat
operational notes as evidence of a dated run, not as permission to alter live
data or relax an acceptance gate.

## Invariants

- Resolve runtime paths from an explicit valid project root; do not fall back to
  an unrelated repository directory when a project root is required.
- Do not weaken validation, acceptance, or publication gates to make an
  operation finish. A failed or rejected publication remains recorded evidence.
- Never commit credentials, tokens, operator secrets, or production outputs.
- Do not edit source or configuration while a documented update or publication
  operation is running; preserve unrelated work in progress.

## Verify

```bash
pytest tests/integration/test_project_root_cli.py tests/integration/test_cli.py -q
pytest tests/integration/test_acceptance_cli.py tests/integration/test_data_pipeline.py -q
```

For a real operation, follow the command and explicit `--root` scope in
`RUNBOOK.md`; do not infer approval to run networked or publishing commands.
