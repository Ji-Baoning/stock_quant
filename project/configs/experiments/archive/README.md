# Retired experiment specs

Specs here are **not** part of the active set. Each one binds the retired
`custom_csi300_ic_tradable` universe definition (see
`../../universes/archive/README.md`), whose pinned `membership_table_sha256`
matches no published dataset — so every run bound to it fails at the universe
preflight before any factor or backtest work.

Unlike `../../universes/archive/`, this directory has no swap procedure.
Nothing here is waiting to be moved back up: the successor specs listed below
already cover the same role against the live `custom_csi300_tw_tradable`
definition.

## Why a subdirectory is safe

`configs/experiments/` is never globbed — specs are resolved only from the
explicit path a caller passes to `--spec` (relative to the project root, e.g.
`configs/experiments/momentum_60d.yml`). The one directory scan in this area
(`src/stock_quant/cli.py:859`) walks the *published* registry under
`data/experiments/`, not this tree. A spec moved here is therefore simply not
reachable by any run that does not name its new path.

Note that the same is **not** true of a definition: `_config_hashes` resolves
`configs/universes/<universe_definition>.yml` from the top level
(`src/stock_quant/research/runner.py:847`), so a universe definition has to be
moved back up to be usable. That asymmetry is why the two archives carry
different rules.

## The retired specs

| Spec | Bound definition | Live successor |
| --- | --- | --- |
| `momentum_60d_pit_tradable.yml` | `custom_csi300_ic_tradable` | `momentum_60d_pit_official.yml` |
| `momentum_60d_wf_real_baseline.yml` | `custom_csi300_ic_tradable` | `momentum_60d_wf_tw_baseline.yml` |
| `momentum_60d_wf_real_challenger.yml` | `custom_csi300_ic_tradable` | `momentum_60d_wf_tw_challenger.yml` |

Each successor records the substitution in its own header comment:
`momentum_60d_pit_official.yml:3` ("与 momentum_60d_pit_tradable.yml 相同的信任
语义，但 universe_definition 换成 …") and `momentum_60d_wf_tw_baseline.yml:3-4`
("派生自 momentum_60d_wf_real_baseline.yml：原规格绑定的
custom_csi300_ic_tradable … 已无任何已发布数据集").

## Challenger gap — closed 2026-09-15

`momentum_60d_wf_real_challenger.yml` is the **challenger** half of the
pre-registered one-time strategy challenge (`PROJECT_MEMORY.md` §8.3). Its
baseline half was rebound to the live universe first; the challenger half was
left unbound at retirement, so for a time the challenge had no runnable
challenger spec.

That binding decision has now been made:
`configs/experiments/momentum_60d_wf_tw_challenger.yml` mirrors the live
baseline, binds `custom_csi300_tw_tradable`, and — verified by loading both
specs and diffing every field — differs from it in nothing but
`portfolio_rule` and `hypothesis`, which is the pairing the challenge service
requires.

The pinned hash was checked against the published dataset rather than assumed:
`custom_csi300_tw_tradable.yml` pins `membership_table_sha256` `5f5bf476…`,
which equals the `membership_content_hash` of the 766-row
`universe_membership` table of the dataset CURRENT pointed at on 2026-09-15
(`1d6e43b4…`). The definition's own `version` `1d6a8c8f…` is what that
dataset's manifest records under `universe_coverage_definition_hashes`.

Authoring a spec consumes nothing: the holdout registry is untouched, and the
challenger snapshot hash is still frozen at the challenge's own run, not here.
