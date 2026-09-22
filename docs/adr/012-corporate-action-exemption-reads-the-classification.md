---
status: accepted
date: 2026-09-21
decision: The ADR-008 deny-list exemption becomes evidence-conditional — a quarantined row it would exempt stops exempting for its symbol when an admissible ADR-009 price-event channel observes a market adjustment at the row's probe date (the stated ex-date, else the announcement anchor); and a window whose every reported event is accounted for (accepted, refused as non-price evidence, or provably about another period) reads `VERIFIED_EMPTY`, not an unaccounted gap. The baostock adjustment-factor series is wired as the second admissible channel this rule reads.
affects:
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_sources/baostock_factor.py
  - src/stock_quant/data_sources/tdx.py
---

# 012 — The corporate-action exemption reads the classification

## Context

ADR-009 classified an absent ex-date on two axes and bound "the next change
that touches a corporate-action refusal or the exemption list": any such rule
must name the classification it relies on, and the exemption list stays a
deny-list. Nothing read the classification yet. This record is that next
change, and it reads the classification in two places.

Measured on the published dataset `b01f10c2…` (window 2015-01-05..2026-09-18):

- **Twelve symbols hold one 重整转增 row each inside the window and read
  `VERIFIED`.** Six state a supplier ex-date (600157, 600221, 600515, 600518,
  600654, 600666); six do not (000656, 000792, 000793, 002157, 002310,
  002608). ADR-009's row-5 measurement says four of the stated ones — 600515,
  600518, 600654, 600666 — sit on dates where the market *did* adjust
  (baostock's factor changes; TDX category-1 records 转股 on two of them).
  Those windows read accounted-for while holding a known, unbooked market
  adjustment: 600518's date reads −10.70% where the market factor gives about
  −4.88% (ADR-009, Consequences). The exemption ADR-008 keyed on type cannot
  see this; a rule keyed on the classification can.
- **000503 and 000629 read `FACTS_INCOMPLETE` through the fallback.** Each
  supplier holds exactly one event for the symbol, refused `incomplete`, and
  every known date of it lies outside the window (ADR-006's branches 2/3).
  ADR-006 says such a row "is evidence about another period and must not mark
  the window UNTRUSTED" — but it reaches the verdict transitively: the
  endpoint outcome stays `success_with_events` (the row is kept precisely so
  reconciliation can mark it), so the no-accepted-facts fallback fires. The
  principle is stated; the fallback defeats it.
- The channels the rule needs are now wired: TDX category-1 frames through
  the lazy arbiter (its 0.6.0 API adaptation), and baostock's
  adjustment-factor series through a new lazy channel
  (`data_sources/baostock_factor.py`) that records its snapshots in the same
  content-addressed store. ADR-009 decision 3 admitted both channels; only
  TDX was reachable before this change.

## Decision

1. **The exemption is evidence-conditional.** A row whose reason is in
   `_NON_BLOCKING_QUARANTINE_REASONS` stops exempting its symbol when the
   classification `classify_ex_date` assigns it reads
   `adjustment_observed` on the market axis — probed at the supplier's stated
   ex-date, else at the announcement anchor — from the two admissible
   channels. The demoting classification, named per ADR-009 decision 4, is
   `stated+adjustment_observed` / `absent+adjustment_observed`. A demoted
   reason blocks its symbol's window (reason code `FACTS_INCOMPLETE`), like
   any blocking reason. `bracketed-empty` keeps the exemption (that silence
   is ADR-008's original ground, now positively measured); `unknown` keeps it
   and stays fail-closed.
2. **The settled fallback reads `VERIFIED_EMPTY`.** When every endpoint
   answered, at least one returned events, no quarantined row can affect the
   window, and nothing was accepted — every reported event is either refused
   non-price evidence or provably about another period — the window reads
   `VERIFIED_EMPTY`. "Nothing happened here" is then positively supported by
   the same bytes that used to produce `FACTS_INCOMPLETE`.
3. **承诺补偿 stays blocking.** ADR-009 decision 6 stands: its rows are
   `incomplete` (a blocking reason this record never exempts), and no
   representation for "the shares transfer, the price does not adjust" is
   added here.
4. **The deny-list form is unchanged.** No reason is added to or removed from
   `_NON_BLOCKING_QUARANTINE_REASONS`; the rule conditions an exemption on
   evidence rather than widening the list.

## Consequences

- **Verdicts move, in both directions.** On the measured corpus: the row-5
  four (600515, 600518, 600654, 600666) flip `VERIFIED` → `UNTRUSTED`;
  000503 and 000629 flip `UNTRUSTED / FACTS_INCOMPLETE` → `VERIFIED_EMPTY`.
  Together with the ADR-007 arbiter's arbitration (which a rebuild with the
  adapted channel resolves for 16 of the 17 conflicted symbols), the
  acceptance failure set goes from 21 attributed symbols to a hard core of
  about seven: 002269 (TDX corroborates the total but cannot arbitrate the
  split), 002131 and 600733 (承诺补偿 / evidence-chain residue), and the four
  row-5 demotions — every one with a named, ADR-grounded reason.
- **The 000793 residual narrows but survives.** Its market adjustment
  (2026-06-22) lands days *after* its announcement anchor (2026-06-12), and
  TDX carries it as a category-9 转配股上市, not category-1 — so the at-anchor
  probe reads bracketed-empty and the exemption holds. What ADR-008 accepted
  as "the type test cannot see the market" now survives only where the
  channels cannot see it *at the anchor*; a window containing a row the
  channels do observe is no longer clean.
- **A rebuild is required before any of this is published.** The current
  dataset version is immutable; the rules apply from the next `data update`.
- **The quality report records the demotion's inputs.** The classifications
  come from the same lazy channels the recorder logs (INFO
  `absent_ex_date_classified` for absent-ex-date rows), and each channel read
  is a raw snapshot; an auditor can recompute every demotion from stored
  bytes.

## Rejected alternatives

- **Exempt `incomplete` rows whose classification reads bracketed-empty.**
  The tempting loosening fails on the unknown ex-date: bracketing certifies
  the anchor date, not the event's unknown true ex-date, which may carry a
  real adjustment later in the same window. Exempting would trade a fail-closed
  gap for a silent one. (ADR-009 decision 6 already refuses the sibling move
  for 承诺补偿.)
- **Probe a window ("near" the anchor) instead of the anchor date.** It would
  catch 000793's 06-22 adjustment, but at the cost of a second, arbitrary
  probe notion (how many days?) alongside the recorder's — two meanings of
  "at the date" in one vocabulary. The at-anchor reading stays; the residual
  is documented instead.
- **Keep ADR-008's type test untouched.** It leaves four windows reading
  `VERIFIED` over known unbooked market adjustments — the exact cost ADR-009
  measured and flagged. A rule that cannot see the classification it depends
  on is what ADR-009 decision 4 forecloses for new rules; this record applies
  that to the existing one.
- **Read `VERIFIED` in the settled fallback** (rather than `VERIFIED_EMPTY`).
  `VERIFIED` means reconciled facts exist; a symbol with none must not claim
  them. `VERIFIED_EMPTY`'s published meaning ("every endpoint answered and
  returned no events") extends honestly: the events the endpoints returned
  are accounted for outside this window.

## Risk this decision accepts

- **The demotion is only as good as the channels.** A supplier adjustment
  neither TDX category-1 nor the baostock factor series records at the probe
  date leaves the exemption standing (000793). Both channels are optional by
  role; an unreachable channel asserts nothing and keeps the exemption —
  fail-closed for absence claims, but silent for demotion.
- **Four windows lose `VERIFIED`.** Any frozen experiment whose execution
  window crosses 600515/600518/600654/600666 ex-dates and pinned a pre-012
  dataset will fail its trust gate on re-run against a post-012 build. That
  is the point — those windows read clean today only because the type test
  could not see the market — but it is a visible regression for anyone
  comparing acceptance failures across versions.
- **`VERIFIED_EMPTY` widens.** Datasets published before this record carry
  `FACTS_INCOMPLETE` rows that a post-012 rebuild would render
  `VERIFIED_EMPTY`; cross-version comparisons of that status mix two
  semantics at the boundary date.

## Evidence

- `b01f10c2…` quarantine/coverage read, 2026-09-21: 12 in-window
  `non_distributive_restructuring` rows over 12 `VERIFIED` symbols; the six
  stated ex-dates are 600157 2020-12-29, 600221 2021-12-06, 600515
  2021-12-22, 600518 2021-12-15, 600654 2022-12-23, 600666 2023-02-20;
  000503 and 000629 hold one refused out-of-window row each with zero
  accepted facts.
- ADR-009's row-5 channel table (baostock factor ratios and TDX category-1
  records at the four stated ex-dates), confirmed by the live channel reads
  recorded there.
- Live pytdxdata 0.6.0 read, 2026-09-21: `300124.SZ` 2016-05-18 returns
  `fenhong 0.499939…`, `songzhuangu 0.999878…` — matching ADR-007's measured
  values bit for bit after the API adaptation.
