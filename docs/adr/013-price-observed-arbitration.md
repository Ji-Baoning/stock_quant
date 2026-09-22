---
status: accepted
date: 2026-09-22
decision: The exchange's own ex-rights reference price becomes a fourth evidence channel for cross-source corporate-action conflicts, implemented as a CorporateActionArbiter and chained behind TDX by first-answer, booking `<side>+price_observed` only when one side's implied factor sits within W = 2 ticks of the observed factor and the other clears L = 3 ticks, where a tick is one 0.01-yuan move over the previous close, and declining rather than picking the nearer side when separability is insufficient.
affects:
  - src/stock_quant/data_sources/price_observed.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_model/corporate_actions.py
  - project/configs/corporate_action_reviews.yml
---

# 013 — The exchange reference price arbitrates a cross-source conflict

## Context

ADR-007 gave this project one channel that can outvote a cross-source
corporate-action conflict: TDX, a third-party market-data feed. On the
published `e732b191…` corpus the quarantine table holds 50 rows over 25
`(symbol, ex_date)` conflicts — CNINFO and Eastmoney state different terms for
one event and neither outweighs the other — and TDX reconciles 24 of them
(23 CNINFO, 1 Eastmoney), leaving `002269.SZ` 2015-05-12 unresolvable because
its two sides state the same total as different splits.

A separate piece of evidence was already on disk and unused: tushare's `daily`
snapshots carry `pre_close`, and on an ex-date that column is the ex-rights
reference price the exchange published for the day. 3703 snapshots,
1,732,474 rows over 659 symbols, 2005-01-07..2026-09-18.

It can judge a conflict that nothing else in this project can, for one reason:
it does not pass through our own booked actions. `adjusted_bar`'s
`adjustment_factor` is *derived* from the actions this project accepted, so
using it to decide a conflict it has not accepted is circular; the exchange's
reference price is computed by the exchange from the issuer's announcement, so
it carries an opinion our data has no part in. What it must never be mistaken
for is a *third party* — see decision 7.

The design work is `docs/superpowers/specs/2026-09-22-price-observed-conflict-arbitration-design.md`
(owner-reviewed 2026-09-22); the measurement that calibrated it and the replay
that verified it are in `docs/operations/2026-09-22-price-arbitration-replay.md`.

## Decision

1. **A fourth channel, not a new protocol.** `PriceObservedArbiter` implements
   the existing `CorporateActionArbiter` — one method, `arbitrate(cninfo,
   eastmoney) -> str | None`, `None` meaning "keep the quarantine". No
   quarantine reason is added, no `confirmed_by` shape is added: the label stays
   `<origin>+<authority>` and reads `<side>+price_observed`. It joins the chain
   `FirstAnsweringArbiter` after TDX and the whole chain is handed to the
   existing `_GuardedArbiter`, so both guardrails are inherited rather than
   re-implemented: a `(symbol, ex_date)` an owner has already ruled on is never
   arbitrated, and any exception the channel raises degrades to "no verdict".
   Arbitration still runs *before* the human review channel, so an owner keeps
   the final veto.

2. **The rule is two independent thresholds.** With `d_side = |obs −
   expected_side|` and `tick = 0.01 / prev_close` (both the previous close and
   the reference price are stated to 2 decimals, so one tick is this symbol's
   rounding quantum):

   | Condition | Result |
   | --- | --- |
   | the two sides imply the same factor (within 1e-12) | no verdict — nothing to separate |
   | `d_X ≤ W × tick` **and** `d_other > L × tick` | book X |
   | anything else | `None` — keep the quarantine |

   **`W = 2`, `L = 3`.** They stay independent rather than collapsing into one
   scale `tol × safety`: the winner tolerance asks "is one side consistent with
   the observation at all" (it must absorb the double rounding of a 2-decimal
   previous close and a 2-decimal reference price), while the loser bar asks
   "is the other side far enough away to be told apart". One shared scale
   over-relaxes low-priced symbols — 4.96-yuan `601828.SH` reads as
   indistinguishable at `tol × 4 = 8` ticks, while the split thresholds settle
   it. `tick` is price-adaptive: 3.66-yuan `600025.SH` gets ≈2.7e-3, 505.8-yuan
   `301308.SZ` gets ≈2e-5.

3. **A side carrying rights terms is refused.** The factor
   `(prev_close − cash) / (prev_close × (1 + bonus + cap))` has no term for a
   subscription, so a conflict either side states rights terms for is not
   modelled here and settles nothing rather than being modelled badly.

4. **Every settlement is recomputable from stored bytes.** A settlement records
   the previous close and its date, the reference price, both implied factors,
   both tick deviations, `tick`, the chosen side, and the SHA-256 of the raw
   snapshot the reference price came from — as an INFO quality issue. The daily
   bars are fetched through the same content-addressed raw store as every
   supplier, in the lazy per-symbol shape ADR-012's `adjust_factor` channel
   established, and `_check_raw_snapshots` covers them. An auditor can
   recompute each verdict without re-fetching anything.

5. **The channel is asked only for a conflict, and fails closed everywhere
   else.** It is touched lazily per conflicted symbol and adds no name to
   `_CONFIGURED_SOURCES`; it is gated on the existing `tushare` segment rather
   than a key of its own, because it reads that source's own `daily` endpoint
   through the same adapter, and a second switch for one channel would be a
   second name for one thing. An unavailable channel asserts nothing and leaves
   the conflict quarantined.

6. **Adjacency is proven, so the suspension form fails closed.** The row before
   the ex-date must sit on the open day the published calendar says immediately
   precedes it. A symbol suspended *into* its ex-date therefore settles nothing,
   because `pre_close`'s meaning in that shape is unverified — the one form
   spec §6 deliberately leaves unconfirmed. `601088.SH`'s human rationale
   (last close before the halt against the resumption reference) is the shape
   this rule declines to automate.

7. **The independence limit, in the design's own words.** tushare's
   `pre_close` is the exchange's reference price and shares the issuer's
   announcement as its origin with CNINFO. It is an **independent verification
   path** — it does not pass through this project's bookkeeping and cannot be
   forged by our own data — but it is **not an independent third opinion** the
   way TDX is. A settlement must never be read as "two independent sources
   agree". This is the most important limit in this record.

8. **The direction distribution is part of the evidence.** Each settlement's
   side is recorded, and the distribution across settlements must stay
   auditable and be monitored. On the measured corpus it is 16 CNINFO / 0
   Eastmoney.

## Consequences

- **On the corpus, the channel alone settles 16, folds 2, and holds 7.** All 16
  go to CNINFO; the 2 are the 丁-class float32 pairs (ADR-014); the 7 held are
  listed under Evidence.
- **Under the shipped configuration the published outcome of the 25 does not
  change.** `project/configs/sources.yml` and the template both enable `tdx`,
  and the chain asks TDX first, so the lane is consulted only for the one
  conflict TDX declines — `002269.SZ` — where this channel declines too. The
  lane is therefore a **fallback and a second path, not a replacement**: what it
  buys is that a project running without TDX still settles 16 of 25
  mechanically, with arithmetic that does not depend on pytdxdata's float32
  replication, and that a TDX outage no longer means "arbitrate nothing".
  ADR-014's arity claim is unchanged by this record: the channel is still asked
  first, so ADR-007's 23 CNINFO / 1 Eastmoney / 1 unresolvable still holds when
  both lanes are configured.
- **The 7 held rows change how they are reviewed, not whether.** Six of them
  TDX already settles under the shipped configuration; `002269.SZ` remains
  genuinely unsettleable by any channel built so far (the split is
  mathematically unseparable from the price factor) and belongs in
  `project/configs/corporate_action_reviews.yml` with the other 3 owner
  rulings.
- **The rule takes effect from the next `data update`.** Published dataset
  versions are immutable; the current version carries the conflicts as they
  were, and nothing here rewrites it.
- **The run's own evidence grows.** Each settlement is an INFO issue carrying
  the arithmetic and the snapshot hash, so a rebuild's quality report shows
  which conflicts were settled by price and on what numbers.

## Rejected alternatives

- **"Prefer CNINFO".** ADR-007 already rejects the rule by name, and the
  measured 16/0 is exactly why it must not be smuggled back in as a tie-break:
  the channel earns its settlements from the arithmetic, and a side it does not
  corroborate stays quarantined even when it looks nearer.
- **One shared scale instead of two thresholds** (`W = L`). Over-relaxes
  low-priced symbols on the measured corpus (`601828.SH`) and changes the
  verdict distribution as it is widened (see the parameter plateau under
  Evidence).
- **A tolerance wide enough to fold the remaining float32-adjacent pairs.**
  ADR-014's floor is one ULP and no wider; `600989.SH` 2025-05-13's 1.66e-5
  difference stays quarantined because the price channel gives both of its
  sides an identical implied factor. Merging it would be the machine deciding
  a difference does not matter — the judgement this project keeps for its
  owner.
- **Letting the lane fetch its own way around the raw store.** A settlement
  whose reference price cannot be re-resolved from bytes is an assertion, not
  evidence; D3 exists so that it is evidence.
- **Giving the lane a config key of its own.** It reads one supplier's endpoint
  through that supplier's adapter; a separate switch would let a project claim
  the lane is on while the source it reads is off.

## Risk this decision accepts

- **The independence is weaker than TDX's, and the label does not say so.**
  `cninfo+price_observed` and `cninfo+tdx` look alike in the published row.
  The distinction lives in this record and in the settlement's own issue, not
  in the data.
- **The direction is 100% one-sided on every measurement so far.** A channel
  that always picks the same side is not arbitrating between two feeds, it is
  grading their completeness — consistent with the known fact that Eastmoney's
  feed drops components (the human rationales already say so), but it means the
  channel's value degrades if that stops being the reason. Hence decision 8:
  the distribution is recorded per settlement so a future corpus can show the
  skew rather than hide it.
- **`tick` is coupled to supplier quote precision.** A supplier moving to
  higher-precision quotes, or a change in how the reference price is computed,
  moves what one tick means. The recorded per-settlement arithmetic (decision
  4) is the compensating control: the thresholds can be re-derived from stored
  bytes rather than from the current code.
- **The suspension-into-ex-date form is unverified and fail-closed.** Every
  conflict on the measured corpus traded normally into its ex-date, so this
  branch has never been exercised on real data — only on the refusal.
- **The thresholds sit on a plateau, not on a knife edge.** They were chosen
  from the measured plateau (`L = 2` and `L = 3` give the same 16/2/7, as do
  `W = 1` and `W = 2`); a corpus shaped differently need not have one, and
  nothing here guarantees the plateau moves with the data.

## Evidence

Replay over the immutable `e732b191…` corpus, 2026-09-22 (script
`project/replay_price_arbitration.py`, record
`docs/operations/2026-09-22-price-arbitration-replay.md`, two runs
byte-identical, read-only):

```
conflicts=25 {'held': 7, 'cninfo': 16, 'merged (D4)': 2}
```

The 7 held, with the tick deviation each side showed (`tick = 0.01/prev_close`):

| symbol | ex_date | why it held | measured |
| --- | --- | --- | --- |
| `002269.SZ` | 2015-05-12 | both sides state the same total as different splits; the price factor cannot separate them | both sides 0.0 tick |
| `600900.SH` | 2016-07-19 | the winner's own deviation exceeds `W` | CNINFO 4.0 tick |
| `600989.SH` | 2021-05-20 | the loser is too close to distinguish | Eastmoney 1.5 tick |
| `600989.SH` | 2022-05-12 | as above | Eastmoney 1.5 tick |
| `600989.SH` | 2022-12-27 | as above | Eastmoney 1.8 tick |
| `600989.SH` | 2025-05-13 | both sides imply an identical factor; no separability | both sides 0.0 tick |
| `601966.SH` | 2025-07-10 | the loser is too close to distinguish | Eastmoney 1.0 tick |

Every one matches the design's §2.1 table, and the §8 appendix samples
(`600188.SH` 2021-07-23 / 2022-07-14 / 2023-07-17, `601898.SH` 2024-08-20,
`600900.SH` 2016-07-19, `002269.SZ` 2015-05-12, `300124.SZ` 2016-05-18)
reproduce their numbers exactly. No conflict on the corpus reached the result
"no stored price": all 25 had bars on disk, so every hold above is a rule
verdict rather than a missing-evidence one.

Threshold robustness, measured on the same corpus: `W ∈ {1, 2}` and `L ∈ {2, 3}`
all give 16/2/7; `L = 4` gives 15/2/8 and `L = 5` gives 12/2/11.

Chain order, verified in code rather than assumed: `FirstAnsweringArbiter`
iterates the chain and returns the first non-`None` answer, so with TDX
configured the lane's settlements cannot pre-empt a TDX verdict. The same
ordering is what ADR-014 relies on for its own claim.
