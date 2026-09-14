---
status: accepted
date: 2026-09-14
decision: Every command resolves an explicit, validated project root and has no fallback; a missing or invalid root fails loudly instead of reading the wrong configuration or data.
affects:
  - src/stock_quant/project_root.py
  - src/stock_quant/bootstrap.py
  - src/stock_quant/cli.py
  - project/**
---

# 005 — Explicit project root

## Context

The repository ships a configuration **template** under
`templates/project-config/`, while real runs read configuration and data from a
caller-supplied project directory. Those are different scopes and they look
alike.

Any convenience fallback — "if `--root` is missing, use the repository root",
"walk up until a `configs/` directory is found", "default to the current working
directory" — has the same failure mode: a command that was pointed at the wrong
place silently succeeds against the wrong tree. It then writes a dataset,
publishes an experiment, or records an acceptance against data the operator
never chose. The error is silent, persistent, and hard to attribute afterwards.

## Decision

`stock_quant.project_root.resolve_project_root` is the single source of truth:

- The root comes from `--root` on the command line (or `.` when omitted). It is
  expanded and symlink-resolved.
- The resolved directory must exist and contain `configs/project.yml`,
  `configs/sources.yml` and `configs/costs.yml`.
- A missing directory raises `ProjectRootPathError`; missing config files raise
  `ProjectRootConfigError` naming exactly which files are absent and reporting
  both the raw and the resolved path.
- **There is no fallback.** The repository root, a `project/` sibling, and the
  current working directory are never guessed. Every command resolves and
  validates the root before constructing any service.
- Tushare's token is read only from the `TUSHARE_TOKEN` environment variable, and
  no command prints a token or a raw supplier response.

## Consequences

- Every operational command in `RUNBOOK.md` carries an explicit `--root`, and a
  typo fails immediately with a precise message rather than producing a
  plausible-looking artifact in the wrong place.
- Running from a fresh clone requires creating a project directory first (copy
  the template, or point at an existing project). This is a deliberate step, not
  friction to be optimized away.
- Symlink resolution means the error message can distinguish "you typed the
  wrong path" from "the path resolved somewhere unexpected".
- Because resolution happens before service construction, no partial state is
  created by a root failure.

## Rejected alternatives

- **Fall back to the repository root.** Would make `python -m stock_quant data
  update` work by accident against whatever happens to be checked out.
- **Auto-discover a root by walking parent directories.** Ambiguous when nested
  projects exist, and it makes the effective root invisible in the command that
  ran.
- **Default to the current working directory.** Couples the outcome to the
  operator's shell state, so the same command means different things in
  different terminals.
- **Fall back to `templates/project-config/`.** A template is not a project; it
  carries placeholder values that must never be treated as configuration.

## Evidence

`tests/unit/test_project_root.py`, `tests/integration/test_project_root_cli.py`,
`tests/integration/test_cli.py`. Normative statement:
`docs/architecture/invariants.md` §8. User-facing description: `README.md`
"Configuration".
