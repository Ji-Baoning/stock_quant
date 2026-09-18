---
status: accepted
date: 2026-09-16
decision: A third-party market-data channel (TDX, via pytdxdata) may arbitrate a cross-source corporate-action conflict by picking which official filing to adopt; it is never a source, never enters the endpoint or availability registries, is off by default, and is compared by replicating TDX's own float32 arithmetic rather than by a numeric tolerance.
affects:
  - src/stock_quant/data_model/corporate_actions.py
  - src/stock_quant/data_sources/tdx.py
  - src/stock_quant/data_pipeline.py
  - project/configs/sources.yml
  - templates/project-config/sources.yml
---

# 007 — Third-party arbitration for cross-source corporate-action conflicts

## Context

`normalize_corporate_actions` accepts a row only when CNINFO and Eastmoney agree
exactly (`_same_facts`, exact Decimal equality, no tolerance). On the published
dataset (`CURRENT = d490c637…`, window 2015-01-05..2026-08-28) 17 symbols hold
25 `cross_source_conflict` ex-dates, and each is unbookable: both sides are
quarantined. They are the whole of the `SOURCE_CONFLICT` verdict, and 16 of the
17 have no second way to be resolved — `_reject_reason` needs an official
filing's dates, and no other configured source carries them.

The 25 conflicts are not one phenomenon. Measured, they are:

| Class | Symbols / events | Disagreement |
| --- | --- | --- |
| 甲 | 11 / 14 | Eastmoney omits one same-day event CNINFO reports |
| 乙 | 3 / 8 | Both report one event, with different amounts |
| 丙 | 1 / 1 | Same total, different 送股/转增 split |
| 丁 | 2 / 2 | Same to 6 digits, differing in the 7th |

TDX (通达信) exposes `get_xdxr` over its standard channel: per ex-date, one
category-1 record carrying dividend, 送转, and allotment. It is one row per
ex-date, pre-aggregated — which is exactly why it can corroborate a *total* but
never an event count or a split.

Measured on the 25 conflicts, TDX's record for the ex-date decides:

- **23 events pick CNINFO**, **1 picks Eastmoney** (`301308.SZ`, 2026-06-02),
  **1 picks neither** (`002269.SZ`, 2015-05-12 — both sides match the total).
- → **16 of the 17 symbols become resolvable.** The 丁 class is what makes the
  arbiter credible: it votes against CNINFO once and for CNINFO once, so it is
  not a echo of either side and the rule cannot be reduced to "prefer CNINFO".

Field semantics were measured, not assumed: `fenhong` is the cash dividend *per
share*, `songzhuangu` is 送股+转增 **combined** per share, and the record's single
`date` is the ex-date. `002269.SZ` proves the aggregation: CNINFO's 送6转9 and
Eastmoney's 送5转10 both yield `songzhuangu = 1.5`, so TDX confirms the total and
cannot arbitrate the split.

## Decision

**TDX is an arbiter, not a source.** It decides which official filing to adopt;
it never supplies the row.

1. **Not a source.** TDX does not enter `CORPORATE_ACTION_ENDPOINTS`, does not
   add a name to `_CONFIGURED_SOURCES`, and never appears alone in `source`. The
   registry comment already states this rule for the Tushare relay
   (`project/configs/sources.yml`); TDX follows it. A source's empty answer feeds
   `all empty → VERIFIED_EMPTY`, and TDX cannot make that claim: it carries no
   announcement date, no record date, and no plan status, so `_reject_reason`
   would return `REASON_INCOMPLETE` for every row it ever produced.
2. **Consulted only on failure.** TDX is queried only where
   `normalize_corporate_actions` finds a pair whose `_same_facts` is false. The
   accepted path is untouched.
3. **Indexed by ex-date.** The candidate record is the `category == 1` TDX row
   whose `date` equals the conflict's `ex_date`.
4. **Compare by replicating TDX's arithmetic, not by a tolerance.** TDX
   transports the *per-ten-share* value as float32 and divides by 10 in double.
   A side matches when, field by field, `float32(side_per_10) / 10.0` equals the
   TDX value **exactly**.
5. **Exactly one side must match.** Both matching (丙) or neither matching
   (no TDX record, or a disagreement TDX does not corroborate) leaves both rows
   quarantined, unchanged. Fail closed.
6. **The winner is recorded.** `source` becomes `<winner>+tdx`
   (`cninfo+tdx` / `eastmoney+tdx`). `source` is already a *confirmation set*,
   not a single origin: on the published dataset 6,807 of 6,872 rows read
   `cninfo+eastmoney` and 3 read `cninfo+reviewed`. `+tdx` is a third instance
   of an existing compound form — a value, not a schema change, and the
   arbitration is not hidden.

7. **A signed review outranks it.** A conflict whose `(symbol, ex_date)` an
   owner has already reviewed is never arbitrated.
   `apply_corporate_action_reviews` resolves a conflict by requiring exactly one
   quarantined row for the reviewed source, and *raises* when it finds none — a
   raise the pipeline turns into an empty dividend lane for that symbol.
   Arbitration runs before the review, so without this rule a reviewed conflict
   would be booked, leave the review nothing to match, and cost the symbol every
   fact it had. (No key in `corporate_action_reviews.yml` collides with the 16
   resolvable symbols today; the rule exists so that one cannot.)
8. **Its failure is never worse than its absence.** A missing package, an
   unreachable server or a raising arbiter leaves every conflict exactly where
   it was and records one WARNING. An automated third opinion must not be able
   to discard a symbol's *agreed* facts on its way to a disagreement.

**Switch.** `project/configs/sources.yml` gains a `tdx:` segment with
`enabled: false`. With it off, every verdict is byte-identical to today. A
rebuild that explicitly enables it **must** record the TDX responses as raw
snapshots, so the arbitration is reproducible from content-addressed bytes and
not from whatever the server happens to answer later. The same segment is added
to `templates/project-config/sources.yml`, so the switch is discoverable from a
new project root rather than only from this repository's copy.

## Consequences

- With the switch enabled, a rebuild flips **16 symbols** from
  `UNTRUSTED / SOURCE_CONFLICT` to `VERIFIED`, and the `corporate_action_evidence`
  acceptance failures drop **27 → 11**. `002269.SZ`, the 8 戊-class symbols, and
  the 2 ADR-006 fail-closed residues are unaffected.
- It requires a **new dataset version**; the current one is immutable.
- A TDX outage, a missing ex-date, a decode failure, or a third value that
  matches neither side all leave the conflict exactly as it is today. The change
  cannot make a window *less* trusted than it already is, and it cannot turn a
  symbol's agreed facts into an empty lane: a broken arbiter degrades to the
  off state and records a WARNING.
- The residual FAIL after this lands is 11 symbols, attributed in
  `docs/operations/2026-09-16-corporate-action-residual-attribution.md`.

## Rejected alternatives

- **Add TDX as a third endpoint.** Its no-event answer would enter
  `VERIFIED_EMPTY`, and it cannot produce a bookable row at all. It would also
  make a market-data aggregator an *evidence* source, which the sources.yml
  contract forbids for the evidence chains.
- **A numeric tolerance instead of replicated arithmetic.** The measured float32
  noise is 1e-9..4e-8, while 丁-class disagreements are ~1e-6, so a tolerance
  would have to sit in a narrow band. Worse, the obvious formulation is simply
  wrong: `float32(1.4) ≠ 14.0/10.0`, because the quantization happens on the
  per-ten-share value, not per share. Replicating TDX's own arithmetic needs no
  constant and separates the 丁 cases exactly.
- **"Prefer CNINFO".** Refuted by `301308.SZ`, where TDX sides with Eastmoney.
- **Relax `_same_facts`.** It is the basis on which 甲/乙/丁 are all detected;
  loosening it to admit 丙 would also admit 甲.
- **Resolve the 10 `FACTS_INCOMPLETE` symbols this way.** TDX cannot: the 8 戊
  symbols lack an announcement date and a record date, and the 2 己 symbols are
  ADR-006's documented fail-closed residue, which is a policy question, not a
  fact question.

## Risk this decision accepts

- **TDX warrants nothing.** Its own documentation reads 不保证实时性与准确性 and
  仅供技术研究与教育用途. It is accepted here precisely because it never becomes
  the evidence: it only selects *which official filing* is adopted, and it must
  match that filing exactly on every field.
- **A float32 resolution floor.** TDX separates candidates down to roughly one
  float32 ULP at the value's magnitude (≈ 9.5e-7 at 10/share). The 丁 class sits
  at almost exactly that floor. A future disagreement finer than it would be
  unresolvable by TDX and would stay quarantined — the failure direction is
  safe, but it is a real ceiling.
- **TDX is one row per ex-date.** It can corroborate totals only. Any conflict
  whose resolution depends on *which* events or *how* they split is outside its
  reach by construction.

## Out of scope

TDX also offers adjusted (QFQ/HFQ) and unadjusted K-lines, an index K-line, a
security list, and a finance endpoint. Four further roles were identified for
this project — an independent second implementation for `adjusted_bar`
validation, `daily_bar` sampling reconciliation, a second trading-calendar
source, and `security_master` reconciliation. **None is decided here.** They
have different evidence semantics from arbitration and would each need their
own record.

## Evidence

Reconciliation of all 25 conflicts against TDX, field by field
(`fenhong` and `songzhuangu`, compared as `float32(per_10)/10.0` for exact
equality): 23 CNINFO, 1 Eastmoney, 1 unresolvable.

The 丁-class pair is the evidence that TDX is not an echo of either side, and
its two members arbitrate on **different fields**:

| Conflict | What the sides disagree on | TDX field | TDX value | Books |
| --- | --- | --- | --- | --- |
| `300124.SZ` 2016-05-18 | 转增 9.998780 vs 9.998781 per ten (cash agrees, 4.99939) | `songzhuangu` | `float32(9.998780)/10 = 0.9998780250549316` | CNINFO |
| `301308.SZ` 2026-06-02 | cash 9.90744266 vs 9.907442 per ten (送转 agrees, 0) | `fenhong` | `float32(9.907442)/10 = 0.9907442092895508` | Eastmoney |

Eastmoney's figures quantise to `0.9998781204223632` and CNINFO's to
`0.9907443046569824` respectively — neither matches — so each side is separated
by exactly the float32 boundary the decision replicates.

Field semantics were confirmed against a live read of the channel on
2026-09-16: `300124.SZ` 2016-05-18 returns `fenhong = 0.4999390125274658`,
`songzhuangu = 0.9998780250549316`.

Attribution ledger, per-symbol worklist, and the reproducibility script:
`docs/operations/2026-09-16-corporate-action-residual-attribution.md`.
