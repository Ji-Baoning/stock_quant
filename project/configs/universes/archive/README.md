# Parked universe definitions

Definitions in this directory are **not** part of the live criterion set:
`load_universe_coverage_criterion` scans `configs/universes/*.yml`
non-recursively (`src/stock_quant/research/universe.py:325`), so files one
level down are invisible to it, and the research runner resolves
`configs/universes/<universe_definition>.yml` by exact path
(`src/stock_quant/research/runner.py:847`).

They are kept here, byte-for-byte, because a definition is only loadable from
the top level and a parked one has to be *moved* back up to be used.

## Why a parked definition cannot simply stay in place

Two definitions with the same `universe_id` in the scanned directory make every
publication fail with `UniverseCoverageError: duplicate universe_id ...`
(`universe.py:344-347`). The `enabled: false` escape hatch does **not** work for
a definition that is also referenced by name: the criterion loader strips
`enabled` before validating (`universe.py:334-336`), but
`load_universe_definition` passes the raw document to a model declared
`extra="forbid"` (`universe.py:357-367`, `UniverseDefinition` at
`universe.py:109`), so the same file would fail to load in a research run.

## `custom_csi300_tw_tradable_28.yml`

Re-pins, byte-for-byte, the definition that
`configs/universes/custom_csi300_tw_tradable.yml` carried while the dataset was
`b0e36345…` (a 30-row / 28-symbol tradable intersection). The extension of the
security master to the full membership re-ran `trim_universe_membership.py` and
re-pinned the live file to the 766-row table of `1709eddb…`, which changed that
file's `version`.

Its purpose is the *short unblock path* recorded in
`docs/operations/2026-09-14-wf-oos-stage-result-and-diagnostics.md`: binding a
walk-forward run to the already-accepted `b0e36345…` dataset without re-running
any publishing step. To use it, **move** it up one level (it cannot co-exist
with the live file):

```sh
mv project/configs/universes/custom_csi300_tw_tradable.yml \
   project/configs/universes/archive/custom_csi300_tw_tradable.yml.live
mv project/configs/universes/archive/custom_csi300_tw_tradable_28.yml \
   project/configs/universes/custom_csi300_tw_tradable.yml
```

**Invariant:** while swapped in, `version` must equal
`c211b85cc86f395a9aa699223c9fb059f45824f05b05e7f6d99910bbdfb4fe58` (the value
frozen into experiment/run `46f8b74e…`). Any other value means this file was
reconstructed wrongly and must not be used — check with:

```sh
python -c "import sys; sys.path.insert(0,'src'); \
from stock_quant.research.universe import load_universe_definition; \
print(load_universe_definition('project/configs/universes/custom_csi300_tw_tradable.yml').version)"
```

The swap is reversible: two files on the top level is the one state the loader
rejects.
