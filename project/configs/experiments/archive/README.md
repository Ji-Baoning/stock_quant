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
| `momentum_60d_wf_real_challenger.yml` | `custom_csi300_ic_tradable` | **none** |

Each successor records the substitution in its own header comment:
`momentum_60d_pit_official.yml:3` ("与 momentum_60d_pit_tradable.yml 相同的信任
语义，但 universe_definition 换成 …") and `momentum_60d_wf_tw_baseline.yml:3-4`
("派生自 momentum_60d_wf_real_baseline.yml：原规格绑定的
custom_csi300_ic_tradable … 已无任何已发布数据集").

## Known gap

`momentum_60d_wf_real_challenger.yml` is the **challenger** half of the
pre-registered one-time strategy challenge (`PROJECT_MEMORY.md` §8.3). Its
baseline half was rebound to the live universe, but no challenger spec was:
after this retirement the challenge has no runnable challenger spec.

Closing that gap is a separate decision — it requires choosing which dataset
and definition to bind, and the challenger snapshot hash has to be frozen
before its own run. It is recorded here rather than filled in.
