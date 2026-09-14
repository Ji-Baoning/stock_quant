Paths: tests/**, tools/check_context_governance.py, pytest configuration, and test-related documentation.
Read first: the changed behavior's nearest test, pyproject.toml, AGENTS.md, and the path rule governing the production area.

# Test boundaries

Tests protect observable contracts. Keep fixtures small, deterministic, and
free of credentials or production data; use integration tests when a boundary
cannot be meaningfully exercised in isolation.

## Invariants

- Assert behavior, outputs, errors, or side effects—not a direct snapshot of
  source prose or private implementation structure.
- Add a regression test before changing implementation behavior, observe it
  fail for the intended reason, then make the smallest change that passes.
- Do not weaken or delete a test merely to accommodate an unrelated change.
- Preserve unrelated test and fixture WIP; stage only files belonging to the
  requested task.

## Verify

```bash
pytest <closest-affected-test> -q
pytest tests/unit/test_context_governance_docs.py -v
pytest -q
```

Run the focused command first and use the full suite before completion when the
task requests it or the changed boundary has broad effects.
