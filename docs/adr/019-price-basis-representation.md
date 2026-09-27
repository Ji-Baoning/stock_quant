---
status: accepted
date: 2026-09-27
decision: "A 重整转增 row refused as `non_distributive_restructuring` that states an ex-date books the exchange-reference-implied holder ratio — never the announcement's capital expansion — when four legs agree: the supplier's ex-date row states a reference price (pre_close) differing from the last traded close, TDX's category-1 record attests an ex-rights event at the date, the no-adjustment hypothesis is rejected from the symbol's own traded limit band (the reference outside it, the ex-date's trades inside the band around the reference), and — when a factor channel is configured — the series' cumulative step across the date agrees to 1e-5. The booked row reads `<origin>+price_basis`; the refusal is consumed for the round and stays in carried quarantine history; every weaker shape fails closed."
affects:
  - src/stock_quant/data_sources/price_basis.py
  - src/stock_quant/data_model/corporate_actions.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_sources/xingyao_factor.py
---

# 019 — The price-basis representation for refused restructuring rows

## Context

The 2026-09-27 rebuild left exactly two `UNTRUSTED` symbols — `600515.SH` and
`600518.SH` — held there by ADR-012's evidence-conditional demotion: TDX's
category-1 record attests an ex-rights event at each row's stated ex-date, so
the classification reads `stated+adjustment_observed` and the 重整转增
refusal's exemption yields.

The owner's audit then established, from bytes already in the repository:

- **ADR-009's non-adjudication is decidable.** Its row-5 finding — three
  channels stating three different values, "this record does not adjudicate"
  — resolves for these rows: the two *price-adjacent* channels agree to
  ~3e-7. The stored baostock cumulative-factor series steps
  36.113495 → 38.465059 (= 1.065116) across 600518's 2021-12-15 and
  1.927730 → 2.847260 (= 1.477002) across 600515's 2021-12-22, while the
  exchange reference implied by the traded prices (4.58 → 4.30; 8.67 → 5.87)
  gives the same factors. The announcement (1 + r = 2.80000 / 2.92387) and
  TDX's 转股 (1.58 / 1.74775) describe the *share expansion* — capital that
  went to the reorganisation, not to holders — and are not price evidence.
- **The demotion is classification-driven, not value-driven.** `market_view`
  asks only whether the probe date sits in a channel's event-date set; no
  magnitude is read. Adjudicating "which value is the market factor" could
  never unlock these rows — only a representation that books the attested
  adjustment can.
- **The stakes are acceptance-level.** Both symbols sit in the fixed
  engineering universe (`configs/universe.yml`), `_check_corporate_actions`
  gates on it with no tolerance, and the project window now crosses both
  ex-dates — ADR-008's "no research window contains these dates" premise is
  stale. They were the sole remaining automated failures.

## Decision

1. **The representation, not a value pick.** A refused 重整转增 row with a
   stated ex-date books the *exchange-reference-implied holder ratio* —
   `prev_close / pre_close − 1` per share, from the exchange's own 除权参考价
   the supplier carries — as `capitalization_ratio`, with cash/bonus/rights
   absent. The announcement's capital ratio is never booked: booking 2.8
   where the exchange adjusted 1.065 is ADR-008's phantom. The booked row
   reads `<origin>+price_basis`, and the announcement's capital story remains
   in the quarantine table's carried history.
2. **Four legs, every one required or explicitly skipped.**
   - *Stated reference*: the supplier's ex-date row carries `pre_close`
     differing from the last traded close. The last close may sit more than
     one open day back — the halt days carry no rows in the per-symbol
     response — because a zero-trade halt leaves the price the exchange's
     reference formula consumed.
   - *Event attestation*: TDX's category-1 record at the ex-date. Required;
     an absent TDX channel refuses the settlement.
   - *Limit-band falsification*: the reference must lie outside
     `[prev × (1−L), prev × (1+L)]` and the ex-date's traded close inside
     `[pre_close × (1−L), pre_close × (1+L)]`, where `L` is the largest daily
     move the symbol actually traded in a 180-day calibration window ending
     at the ex-date (the ex-date's own bar excluded — it moves by the
     adjustment under test). Fewer than 20 traded rows refuse the settlement.
     This is the in-instance verification whose absence made ADR-013 decline
     the shape: for 600518 the reference implies −6.11% against a traded
     maximum of ~5.07%, and 4.09 = the reference's own limit-down; for
     600515 the gap is −32.3%.
   - *Factor-series corroboration*: when an adjustment-factor channel is
     configured, its cumulative step across the ex-date must agree within
     1e-5 relative — the measured agreement is ~3e-7. A disagreement fails
     closed; a missing channel (ADR-016 decision 11 ships xingyao disabled)
     skips the leg and records the absence.
3. **The booking consumes the refusal for the round.** The fresh reconcile
   emits the booked row and no quarantine row, so no demotion fires and the
   window reads `VERIFIED`. Carried quarantine history is untouched — the
   published quarantine table keeps every refusal ever recorded.
4. **The settler is a best-effort evidence path**, mirroring the arbiter
   channels: every failure degrades to "no booking", one fetch failure is
   remembered for the symbol, and the calibration response is snapshotted so
   the booking recomputes from stored bytes.

## What this confronts

- **ADR-013 decision 6** ("the suspension form fails closed"). These two rows
  are exactly that shape — the halt day before the ex-date carries no row in
  the per-symbol response, so the adjacency check ADR-013 relies on cannot
  pass. This record automates the shape anyway, because the two grounds for
  ADR-013's refusal are answered in the instance: the reference is *stated*
  by the exchange (not inferred from trades), and its meaning is *verified*
  by the limit-band falsification plus the TDX event — the verification
  ADR-013 recorded as missing ("pre_close's meaning in that shape is
  unverified"). The channel ADR-013 built remains untouched; the settler is
  a separate evidence path with its own, stricter gates.
- **ADR-009 decision 6** ("no representation exists for 'shares transfer, the
  price does not adjust'"). The representation is deliberately narrower: it
  books the *price* dimension the exchange itself publishes, and books
  nothing for the capital dimension. 承诺补偿 (ADR-018's ground) still books
  nothing — its rows attest no exchange adjustment at all.

## Consequences

- On the next deep reconcile, 600515 and 600518 book
  `cninfo+price_basis` rows (ratios 0.477002 and 0.065116 per share), their
  windows read `VERIFIED`, and `corporate_action_evidence`'s automated
  failures drop to zero — verified by rebuild, not assumed here.
- `adjusted_bar` reproduces the exchange's own adjustment exactly: the booked
  factor is the reference ratio, not an approximation of it.
- Other refused restructuring rows are unaffected: 600654/600666 carry no TDX
  category-1 record at their ex-dates (ADR-009's measurement) and 600157's
  reference equals its last close — all three legs fail closed for them.
- The stale-premise debt ADR-009/008 carry (windows now cross these dates) is
  retired by the booking rather than by editing those records.

## Rejected alternatives

- **Booking the announcement's capital ratio.** The phantom ADR-008 exists to
  refuse.
- **Extending `corporate_action_reviews.yml` to sign these rows.** The review
  channel matches only `cross_source_conflict` rows by construction
  (`apply_corporate_action_reviews` requires exactly one quarantined row for
  the reviewed source); a YAML entry cannot reach a single-source type
  refusal. If an owner later wants a manual veto over price-basis bookings,
  that is a new decision — the automated rule is deliberately evidence-bound,
  not owner-bound, like ADR-007's arbiter.
- **Requiring the factor-series leg unconditionally.** Under ADR-016
  decision 11 no factor channel is configured, so the rule would be inert and
  the two symbols would stay `UNTRUSTED` indefinitely. The series is the
  strongest corroboration but not the attestation; the exchange's own
  reference is.
- **Reading the stored baostock factor snapshots as runtime evidence.** The
  snapshots that adjudicated the value question are decision evidence, not a
  runtime channel; making stored bytes of a dormant supplier an evidence leg
  would create a new epistemic class this record does not need.

## Risk this decision accepts

- The booked ratio carries the reference price's 2-decimal rounding (~1e-7
  relative); the factor series' agreement bounds the error.
- A future row whose reference survives the falsification but is genuinely a
  data defect would book wrongly — accepted because all four legs must
  coincide: an attested exchange event, a reference outside every traded
  band, trades hugging the reference, and (when configured) a vendor series
  stepping by the same factor.
- The booked row's capital story is single-source by design; any consumer
  reading 送转 as *capital* facts must not read `price_basis` rows as
  capital evidence — the label says the ratio is price-attested.

## Evidence

- The stored factor snapshots:
  `project/data/raw/baostock/adjust_factor/` — `sh.600518` (23 rows,
  2001-03-19 → 2021-12-15; step 1.065116 across the ex-date) and `sh.600515`
  (7 rows, 2002-08-06 → 2026-07-21; step 1.477001).
- The live supplier read (2026-09-27): tushare daily for `600518.SH`
  2021-12-15 `pre_close = 4.30` after a 4.58 halt close, and `600515.SH`
  2021-12-22 `pre_close = 5.87` after 8.67; the published bars' resumption
  ladders (4.09 → 2.67; 5.58 → 4.52) step at the ST limit from those
  references.
- The traded limit measurement: max |daily move| ≈ 5.17% across
  2021-11..2022-03 for both symbols (pure tick rounding of the 5% regime).
- Guard tests: `tests/unit/test_price_basis_settler.py` (eleven shapes) and
  the booking/refusal tests in `tests/unit/test_corporate_action_normalize.py`.
