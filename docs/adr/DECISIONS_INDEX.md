# Decision index

Adopted architecture decisions, newest concerns first. Read the index, then
open only the record you need — each one explains *why* a boundary exists and
what was rejected, not how the code works (that is `docs/architecture/`).

`date` is the date the decision was recorded here; several of these mechanisms
were already in place before they were written up as ADRs.

| ADR | Status | Date | Affected paths | Keywords | Read when |
| --- | --- | --- | --- | --- | --- |
| [001 Content-addressed, immutable publication](001-content-addressed-publication.md) | accepted | 2026-09-14 | `src/stock_quant/data_model/dataset.py`, `src/stock_quant/data_quality/gates.py` | content-addressed, immutable, atomic publish, CURRENT | You change how a dataset version is built, gated, written or pointed at. |
| [002 Real-data acceptance](002-real-data-acceptance.md) | accepted | 2026-09-14 | `src/stock_quant/research/acceptance/**` | acceptance, real-data-v1, REJECTED record, CURRENT_ACCEPTED | You touch the acceptance registry, its checks, or the research run gate. |
| [003 Point-in-time index universe](003-point-in-time-universe.md) | accepted | 2026-09-14 | `src/stock_quant/research/universe.py`, `src/stock_quant/data_model/universe_membership.py`, `configs/universes/**` | PIT, membership evidence, universe_version, no override | You change membership facts, universe resolution, or the preflight gate. |
| [004 Pre-frozen walk-forward OOS](004-walk-forward-oos.md) | accepted | 2026-09-14 | `src/stock_quant/research/walk_forward/**` | walk-forward, fold schedule, stability-v1, OOS | You change fold scheduling, fold metrics, or the stability verdict. |
| [005 Explicit project root](005-explicit-project-root.md) | accepted | 2026-09-14 | `src/stock_quant/project_root.py`, `src/stock_quant/cli.py`, `project/**` | --root, no fallback, config validation | You change how a command finds its configuration or data. |

## Not yet recorded

Decisions that are implemented but not yet written up: the one-time strategy
challenge / holdout consumption, the `buffered_risk_weighted` portfolio rule,
and the `adjusted_bar` total-return price basis. They are described factually
in `PROJECT_MEMORY.md` §8 and `docs/architecture/data-flow.md` until an ADR
supersedes that description.

## Maintenance

- An ADR is never rewritten to reach a different conclusion. Supersede it with
  a new ADR and set the old record's `status` to `superseded by NNN`.
- `status` values used here: `accepted`, `superseded by NNN`, `deprecated`,
  `proposed`.
