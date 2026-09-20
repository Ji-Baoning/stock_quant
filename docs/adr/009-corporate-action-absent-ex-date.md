---
status: accepted
date: 2026-09-19
decision: A supplier's absent ex-date is classified on two axes — whether the supplier states one, and what the market's price-event record shows at the event date — before any rule refuses the row or exempts its quarantine reason from withholding trust; "no market adjustment exists" may only be asserted from a channel whose price-event list brackets the date and is empty at it. No behaviour changes today and ADR-008's decision stands.
affects:
  - src/stock_quant/data_model/corporate_actions.py
  - src/stock_quant/data_pipeline.py
  - project/configs/sources.yml
---

# 009 — Classifying an absent ex-date

## Context

ADR-008 refuses a CNINFO `分红类型 == 重整转增` row by type and exempts that one
refusal from withholding coverage trust. Its Context grounds the refusal in
"no exchange ex-date adjustment exists for it", and it deliberately leaves
`承诺补偿` as a blocking known gap. Both readings rest on an absent `除权日`
having one meaning.

Measured on 2026-09-19 against the raw corpora already on disk — and against a
live baostock call, the first since that channel went down — the absence has
more than one meaning, and ADR-008's sentence is not uniformly true.

Two axes, not one. The supplier either states an ex-date or does not; the
market's price-event record either shows an adjustment at the event date,
shows none, or is silent. All six combinations occur in the 14 typed rows:

| # | Supplier ex-date | Market at the date | Rows |
| --- | --- | --- | --- |
| 1 | absent | no adjustment (bracketed, empty) | 002131, 600733 (承诺补偿); 002608 (重整转增) |
| 2 | absent | **adjustment observed** | 000793 |
| 3 | absent | unknown — no channel brackets the date | 000656, 000792, 002157, 002310 |
| 4 | stated | no adjustment (bracketed, empty) | 600157 |
| 5 | stated | **adjustment observed, different magnitude** | 600515, 600518, 600654, 600666 |
| 6 | stated | unknown | 600221 |

Two readings worth isolating, because they are what a two-layer framing of
"missing vs nonexistent" would miss:

- **000793.SZ** (row 2) has no supplier ex-date at all, yet the market moved on
  a date consistent with the event. Its CNINFO record date is 2026-06-18;
  baostock's adjustment-factor series changes on 2026-06-22 (ratio 1.047809),
  and TDX records 转配股上市 that same day. Whether that adjustment belongs to
  this 重整转增 is not resolved here. What is resolved is that "no adjustment
  exists near this event" cannot be asserted for it.
- **600515 / 600518 / 600654 / 600666** (row 5) all carry a supplier ex-date and
  were booked by ADR-008's defect 2. At the supplier's own ex-date:

  | Symbol | Supplier ex | Supplier `1 + r` | baostock factor ratio | TDX category-1 that day |
  | --- | --- | --- | --- | --- |
  | 600515.SH | 2021-12-22 | 2.92387 | series begins at that date | 转 0.7477 per share |
  | 600518.SH | 2021-12-15 | 2.80000 | 1.065116 | 转 0.5800 per share |
  | 600654.SH | 2022-12-23 | 2.19014 | 1.003507 | none |
  | 600666.SH | 2023-02-20 | 2.50000 | 1.586592 | none |

  The date is a market event for all four; the supplier's ratio is not the
  market's adjustment factor. Three channels state three different values and
  this record does not adjudicate between them. What it fixes is the ground:
  refusing them is right because booking 2.8 where the market applied 1.065 is
  ADR-008's phantom, **not** because no adjustment happened.

Neither channel alone can carry an absence claim:

- **TDX's category-1 list is not a complete record of market adjustments.**
  000793 is the counterexample: an adjustment with no 除权除息 row.
- **baostock's dividend table cannot carry one at all.** `sh.600733` returns
  **zero** rows for 2015-2026 while the symbol holds a real, currently-booked
  10转25 (ex 2018-09-19) and a factor row for it.
- **A channel that only emits a row when something happens cannot prove nothing
  happened after its last row.** The distinction that works is *bracketing*: a
  date strictly between two reported price events is covered by the vendor's
  list, so an empty bracket is informative. A date after the last reported
  event is not covered by anything. Applying that test is what separates rows
  1 and 4 from row 3 — five rows (000656, 000792, 002157, 002310, 600221) have
  no bracketing channel at all and cannot be classified either way.
- baostock's factor series also carries no-change rows as a systemic artifact
  (2019-07-19 in 002131, 600518, 600654 and 600666): a row is not by itself an
  event, and a missing row is not by itself an absence.

**Counting basis in ADR-008.** Its line "the corpus holds exactly 12 重整转增
events" counts deduplicated events; its later "股改分红 46 / 90, 重整转增 18 / 18"
counts undeduplicated rows across the 1,991 snapshots. Both are correct on
their own basis and the bases are not stated. Re-measured: 股改分红 136 rows =
46 + 90; 重整转增 36 rows = 18 + 18; deduplicated 12 events; 承诺补偿 6 rows /
2 events / 0 with an ex-date.

## Decision

1. A corporate-action row whose ex-date is absent is **classified on the two
   axes above before any rule acts on it**. The classification is per row, not
   per type and not per symbol: 600733 holds both a row-1 event and a correctly
   booked ordinary ex-date event in the same series.
2. **"No market adjustment exists at this date" may only be asserted from a
   channel whose price-event list brackets the date and is empty at it.** A
   date after a channel's last event is not covered, and a channel that emits
   rows only on events cannot establish absence alone. A row that no channel
   brackets is `unknown` and is treated as blocking, which is the fail-closed
   direction.
3. An observed adjustment at the date overrides an absence claim, whatever the
   supplier says. TDX category-1 records and baostock's adjustment-factor
   series are the two admissible price-event channels; baostock's dividend
   table is not admissible for absence.
4. **ADR-008's rule is the only such rule adopted today, and it is carried
   forward unchanged.** It is keyed on `分红类型 == 重整转增` — a type test that
   reads no classification — so it stands as published. The requirement binds
   rules adopted from here on: any new rule that refuses a row, or that exempts
   a quarantine reason from withholding coverage trust, **must name which
   classification it relies on**, and the exemption list stays a deny-list
   (ADR-008 decision 3). Rewriting ADR-008's rule to read the classification is
   a separate decision, because it moves verdicts that are already published.
5. **ADR-008's decision is unchanged.** Its Context sentence "no exchange
   ex-date adjustment exists for it" is narrowed to the measurement: for the
   four rows in that table the ground is a supplier ratio that is not the
   market's factor, and only for 600157 is the supplier's stated ex-date itself
   not a market event.
6. **承诺补偿 stays a blocking known gap.** Its two rows are row 1, which
   licenses neither booking nor a non-blocking exemption: it means "not a price
   event", not "bookable". No representation exists in this dataset for "the
   shares transfer, the price does not adjust", and the absence of that
   representation is what keeps the rows `incomplete` → `UNTRUSTED`. Adding
   such a representation is a separate decision.

## Consequences

- **No behaviour changes.** No code, configuration, data or published dataset
  is touched; no verdict moves. ADR-008's decision, its deny-list and the
  current `corpus` verdicts stand exactly as published.
- 002131 / 600733 stay `UNTRUSTED` / `FACTS_INCOMPLETE`, as ADR-008 recorded.
  Their absence is now *positively established* (both channels bracket and are
  empty) rather than assumed — a firmer footing for an owner's sign-off, and
  still not a sign-off.
- The exemption ADR-008 grants is keyed on type and does not read these
  classifications. A 重整转增 in row 2 (000793 is the measured candidate) keeps
  its refusal non-blocking, so a window containing an unbooked market
  adjustment can read `VERIFIED`. This is an accepted residual, bounded by
  ADR-008's own finding that no research window contains one of these ex-dates.
- For the four row-5 rows, refusing to book removes ADR-008's phantom but
  leaves a residual of the opposite sign, since the market's factor goes
  unbooked: measured, 600518's date now reads −10.70% where the market factor
  gives about −4.88%, and 600666's reads −33.80% where the market factor gives
  about +5.0%. Magnitudes only; the reason the market factor is smaller than
  the supplier's ratio is not resolved.
- Five of the 14 rows stay `unknown`. They are not failures of this record —
  they are the rows for which the corpus holds no bracketing channel, and they
  remain blocking exactly as before.

## Rejected alternatives

- **Keep ADR-008's reading and amend it in place with a clause about absent
  ex-dates.** The sentence it would sit next to is contradicted by measurement
  for four rows; per `DECISIONS_INDEX.md` a decision is superseded rather than
  rewritten, and a clause would have carried the same two-layer framing into
  the record that this one shows is incomplete. ADR-008 keeps
  `status: accepted`.
- **Key the rule on "the row has no ex-date".** Re-opens ADR-008's rejected
  alternative and misclassifies in both directions: 600157 (row 4) states an
  ex-date that is not a market event, and 000793 (row 2) states none while the
  market moved.
- **Make row 1 non-blocking for 承诺补偿.** Nothing in the dataset can express
  a share transfer that reaches holders without a price adjustment, so the
  window would read complete while a real transfer went unbooked.
- **Use one channel as the absence test** — TDX alone, or baostock's dividend
  table alone. Measured counterexamples above (000793; `sh.600733`).
- **Treat a baostock factor row as proof that the event had an ex-date.** The
  series carries no-change rows (2019-07-19), so a row is not an event.
- **Treat "no row after date D" as absence.** That is the tail case, and it is
  indistinguishable from a vendor that stopped updating; it is why five rows
  stay `unknown` instead of being resolved by assumption.

## Risk this decision accepts

- **The bracketing test is sound only if a vendor's list is complete between
  two of its own reported events.** That is a weaker assumption than trusting
  the tail, but it is still an assumption, and nothing here verifies it.
- **baostock is an optional channel, and this record makes it load-bearing for
  evidence.** It was disabled in `sources.yml` ("down since 2026-09-05") when
  this record was written and is enabled again as of 2026-09-19; that reversal
  changes no measurement above, since those values were read from the live
  service either way, and enabling it changes no published fact. What remains
  is structural: the channel is optional by role (`_REQUIRED_ROLE` false in
  `data_pipeline.py`), so a standing rule naming it as admissible makes a
  channel whose outage is tolerated load-bearing for evidence — not for any
  gate, since no gate reads it. If it is unreachable, absence cannot be
  asserted at all, which is fail-closed.
- **Nothing enforces this today.** No test, gate or code path reads the
  classification; it binds the next change that touches a corporate-action
  refusal or the exemption list. Until that change exists, this ADR is a
  constraint on future work rather than a described property of the system.
- **Three channels disagree on the same rows** and the disagreement is recorded
  rather than explained. If a future change relies on any of these values as
  the market's factor, it must resolve the disagreement first.

## Evidence

Raw corpora read on 2026-09-19 (read-only; nothing written):

- CNINFO dividend snapshots, `project/data/raw/akshare/cninfo_corporate_actions/`
  — 1,991 snapshots holding rows, 34,475 rows, 654 symbols; symbol identity
  from each snapshot's `manifest.json`, since the frame carries no code column.
- TDX xdxr snapshots, `project/data/raw/tdx/tdx_xdxr/` — 659 snapshots, 33,456
  rows, 659 symbols; category `1` is 除权除息, `5` 股本变化, `9` 转配股上市.
- baostock 0.9.30, `query_adjust_factor` / `query_dividend_data`, 2015-01-01 ..
  2026-09-19, for the 14 symbols above.

No test changes: no behaviour changed, and the classification is not yet read by
any code path. The nearest existing tests are ADR-008's, listed in that record.

The four rows in ADR-008's defect-2 table that are not in the table above are
600157 (row 4 here) and 600221 (row 6).
