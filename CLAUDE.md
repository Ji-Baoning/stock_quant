# Stock Quant agent protocol

This is the thin, shared entry point for work in this repository. Load only
the material relevant to the requested change; detailed module facts belong in
the linked documents, not here.

## Priority and conflicts

用户即时指令 > 安全与平台指令 > 路径规则 > 根协议层 > 架构不变量 > ADR > 功能规格 > 运维记录 > 历史资料。

更具体的路径规则优先. At the same priority, prefer the narrower applicable
path. If two equally specific instructions conflict, stop and report the
conflict instead of choosing an interpretation. Historical plans and notes
explain prior work; they are not default current-fact authority.

## Load by task

| Change area | Read first |
| --- | --- |
| Data source, model, quality, or publication | `.claude/rules/data.md`; `docs/architecture/data-flow.md`; `docs/architecture/invariants.md` |
| Research or backtest | `.claude/rules/research.md`; `docs/architecture/data-flow.md`; applicable ADRs |
| Portfolio construction | `.claude/rules/portfolio.md`; `docs/architecture/invariants.md`; applicable ADRs |
| Project config, CLI, scripts, or operations | `.claude/rules/config-and-operations.md`; `RUNBOOK.md`; relevant `docs/operations/` entry |
| Tests or governance checks | `.claude/rules/tests.md`; the changed behavior and its nearest tests |

Read `docs/architecture/overview.md` before an unfamiliar cross-module change,
then use `docs/adr/DECISIONS_INDEX.md` to select only the decision records that
apply. Read a feature specification before implementing that feature.

## Working constraints

- Make the smallest coherent change; do not mix unrelated refactors into it.
- Add or update tests for changed behavior and run the relevant tests before
  claiming success; run the full suite when the task requires it.
- Preserve unrelated work in progress: never overwrite, revert, stage,
  reformat, or delete another change just to simplify this task.
- Do not change data, credentials, generated outputs, or project configuration
  unless the request explicitly scopes them in.
- Keep credentials out of code, configuration, logs, fixtures, and reports.

## Documentation lifecycle

- Architecture documents describe verified current facts and change with those
  facts.
- ADRs preserve an adopted decision; replace a decision with a superseding ADR,
  not a silent rewrite.
- Feature specifications define implementation boundaries and acceptance
  criteria; operations records are dated evidence; history remains traceable
  background rather than current authority.
- Keep `README.md` user-facing, `RUNBOOK.md` procedural, and `PROJECT_MEMORY.md`
  long-lived business context. Link to the fact or decision layer rather than
  duplicating its contract.
