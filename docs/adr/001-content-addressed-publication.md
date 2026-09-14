---
status: accepted
date: 2026-09-14
decision: A published dataset version is content-addressed, immutably named, and written atomically behind a neutral quality gate; CURRENT is an atomically replaced pointer.
affects:
  - src/stock_quant/data_model/dataset.py
  - src/stock_quant/data_quality/gates.py
  - src/stock_quant/data_pipeline.py
---

# 001 — Content-addressed, immutable publication

## Context

Supplier data is mutable: an upstream vendor can restate a bar, revise a
corporate action, or add a suspended-day row. If the local copy followed the
supplier, then two runs of the same frozen experiment could disagree, and a
"reproducible" claim would be untestable. The project also needs to answer, for
any published result, exactly which data it consumed — without keeping a
per-run copy of that data.

Anything derived from a mutable "latest" directory inherits that mutability:
the experiment identity would be stable while its inputs drifted underneath it.

## Decision

One writer, `DatasetPublisher.publish`, and three properties:

1. **Content addressing.** The version name is a hash over the staged table
   records plus the normalized build config. Identical inputs therefore produce
   the identical version name; an identical re-publish is a no-op rather than an
   edit.
2. **Immutable, atomic publication.** Tables are staged under
   `data/staging/<uuid>/`, the manifest and quality report are written there,
   and the directory is moved into `data/standardized/<version>/` with a single
   `os.replace`. A published version directory is never rewritten. The `CURRENT`
   pointer is itself replaced atomically via a temporary file, so a reader never
   observes a half-written pointer.
3. **A neutral gate in front.** Publication requires
   `data_quality.gates.evaluate_publication(report)` to pass. A refusal raises
   `PublicationBlocked`, the staging directory is removed, and no version and no
   pointer change are produced. The gate is blind to strategy inputs — it
   answers "is this data supply usable", not "is this result good".

Each version carries `dataset_manifest.json` (table paths, hashes, build config)
and `quality_report.json`, and is read through a per-version read-only DuckDB
catalog over its Parquet tables.

## Consequences

- A frozen experiment pins a version name and keeps reading it; the experiment
  identity cannot drift even when a newer version is published minutes later.
  This is what makes the identity-scheme-v2 hashing meaningful.
- Storage grows with every changed input. Accepted: a superseded version is the
  audit trail, and reclaiming it would destroy the property being bought.
- A gate change alters which versions can exist, so it is a data-contract change
  and must be reviewed as one; the quality report is preserved per version so a
  past refusal stays diagnosable.
- Because the gate is neutral, cross-source disagreements that do not threaten
  supply are recorded as report-only findings rather than blocks. That is a
  deliberate limit of this decision, not an oversight.

## Rejected alternatives

- **A mutable `latest/` directory with a timestamped version name.** Timestamps
  are not content: a restated input would keep the same name or, worse, change
  the name of unchanged content. Rejected as untestable.
- **Edit-in-place correction of a published version.** Cheap and destroys the
  audit trail; any experiment that consumed the earlier state becomes
  unexplainable.
- **Publishing first and gating afterwards.** Would leave unusable versions
  indistinguishable from usable ones and push the decision into every reader.

## Evidence

`tests/integration/test_dataset_publish.py`,
`tests/integration/test_data_pipeline.py`,
`tests/unit/test_quality_checks.py`. Normative statement:
`docs/architecture/invariants.md` §2.
