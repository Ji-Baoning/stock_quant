---
status: accepted
date: 2026-09-27
decision: A CNINFO 分红类型 == 承诺补偿 row is refused with the dedicated reason `compensatory_share_transfer` — never booked, never `incomplete` — and that reason joins the ADR-012 evidence-conditional exemption deny-list, so a correctly reported compensatory transfer stops withholding coverage trust from its symbol unless an admissible channel observes a market adjustment at its probe date. In the same act, baostock's corporate-action application is deleted outright — the module ADR-016 decision 3 had kept in the tree (`baostock_factor.py`) is removed, leaving xingyao as the ADR-009 factor channel's one implementation.
affects:
  - src/stock_quant/data_model/corporate_actions.py
  - src/stock_quant/data_pipeline.py
  - src/stock_quant/data_model/ex_date_classification.py
  - src/stock_quant/data_sources/baostock_factor.py (deleted)
---

# 018 — The 承诺补偿 representation, and baostock leaves the corporate-action chain

## Context

ADR-009 decision 6 left 承诺补偿 as "a blocking known gap" and reserved the
fix: "No representation exists in this dataset for 'the shares transfer, the
price does not adjust' … Adding such a representation is a separate
decision." This is that decision, taken on the owner's 2026-09-27 instruction
to reduce corporate-action problems by any non-overfitting method.

Measured before deciding:

- **The missing ex-date is the filing's fact, not a dropped field.** The raw
  CNINFO snapshot for `002131.SZ` (fetched 2026-09-13) holds the 2017-12-08
  承诺补偿 row with 实施方案公告日期 and 股权登记日 populated and 除权日
  empty, while the *same frame's* ordinary 2017-06-06 年度分红 row carries a
  populated 除权日. The supplier populates the column when the plan has one.
- **The market confirms no price event.** Both admissible channels bracket
  the probe date and are empty there (ADR-009 row 1: 002131, 600733).
- **The economics leave nothing to book.** A 承诺补偿 transfers shares the
  promisor already owns to holders: total share capital is unchanged, no
  value leaves the company, and the exchange schedules no price adjustment.
  A price×factor total-return model cannot represent a holder-level share
  grant that never moves the price — booking one would invent an adjustment,
  exactly the phantom ADR-008 refused for 重整转增.
- **The cost of leaving it `incomplete`.** 002131 and 600733 each hold one
  such row beside a correctly booked ordinary history; the row withheld
  coverage trust from every window containing it (the 2026-09-25
  `e1db8328` readback: both symbols `UNTRUSTED / FACTS_INCOMPLETE` on
  windows 1 and 3).

## Decision

1. **Refusal is type-keyed, before the date gate.** `_reject_reason` returns
   `compensatory_share_transfer` for `分红类型 == 承诺补偿` at the same
   position as the 重整转增 rule: the ex-date requirement is unmeetable *as
   a fact*, so letting the row fall to the date gate would misname it
   `incomplete` forever. A supplier-published spurious ex-date on such a row
   is refused identically.
2. **Never booked.** ADR-009 decision 6's clause carries: the refusal "means
   'not a price event', not 'bookable'". The row stays in
   `corporate_action_quarantine` as auditable evidence about a real,
   correctly reported event.
3. **The exemption is inherited, not reinvented.** The reason joins
   `_NON_BLOCKING_QUARANTINE_REASONS`, so ADR-012's conditionality binds it
   automatically: an `adjustment_observed` classification at its probe date
   (the announcement anchor — these rows state no ex-date) demotes it back
   to blocking; bracketed-empty and unknown keep the exemption, and unknown
   stays fail-closed.
4. **股改分红 stays bookable.** It can carry a real, exchange-published
   ex-date (600733's 10转25, ex 2018-09-19, correctly booked); the refusal
   keys on 承诺补偿 alone.
5. **baostock's corporate-action application is deleted, not dormant.**
   `baostock_factor.py` is removed from the tree. ADR-016 decision 3 kept it
   as the dormant predecessor of xingyao's factor channel; the owner's
   instruction supersedes that clause for this module — the lane has read
   xingyao since the succession and nothing in the runtime constructs the
   baostock one. The baostock *daily* adapter is untouched: it holds no
   corporate-action role.

## Consequences

- On the next rebuild, 002131's and 600733's 承诺补偿 rows quarantine under
  the new reason and stop withholding trust; their windows read `VERIFIED`
  or `VERIFIED_EMPTY` per their other rows, subject to no channel observing
  an adjustment at the probes. Published versions keep their `incomplete`
  rows and verdicts unchanged (immutable history).
- The ADR-009 absent-ex-date recorder skips these rows (they no longer read
  `incomplete`), and the demotion path probes them at the announcement
   anchor like every other exempted reason.
- ADR-016 decision 3's sentence "The adapter and `baostock_factor.py` stay
  in the tree" is superseded for `baostock_factor.py` by this record; the
  decision's other clauses (dormancy of the daily lanes, the reuse
  substitution) stand unchanged.

## Rejected alternatives

- **Book it as a non-price share grant.** Would corrupt `adjusted_bar`: the
  model derives total return from prices and verified price events, and a
  transfer between holders moves neither.
- **Keep it blocking.** The previous state: a correctly reported fact
  withholding trust from unrelated holdings, with no path to resolution.
- **Cross-fill the missing dates from a third supplier.** Breaks row-level
  provenance — `source` is a confirmation set, and a row's facts must be
  one supplier's statement, not a composite. (The xingyao dividend table was
  evaluated for this and declined for the same reason; see the 2026-09-27
  operations record.)
- **Exempt `incomplete` rows by type check at coverage time.** Duplicates
  the refusal logic in a second place and loses the dedicated reason's
  audit trail.

## Risk this decision accepts

- A 承诺补偿 whose probe date later reads `adjustment_observed` demotes to
  blocking — the ADR-012 bound, working as designed.
- The share-count effect of the transfer (e.g. 10送0.039859) is deliberately
  unrepresented in `adjusted_bar`. It is a holder-level grant with no price
  dimension; the residual is bounded by the transfer's size (0.4% once, for
  the measured rows) and is the same order as the tick-level noise the
  exchange itself does not adjust for.

## Evidence

- Raw snapshot readback: `project/data/raw/akshare/cninfo_corporate_actions/`
  — `002131.SZ` frame 2026-09-13, rows 2017-06-06 (年度分红, 除权日 populated)
  and 2017-12-08 (承诺补偿, 除权日 `None`).
- ADR-009 row 1: both channels bracketed-empty for 002131/600733.
- Coverage recompute on `e1db8328`: both symbols `UNTRUSTED/FACTS_INCOMPLETE`.
- Guard tests: `tests/unit/test_corporate_action_normalize.py` (refusal by
  type with and without a stated ex-date) and
  `tests/unit/test_corporate_action_coverage.py` (exemption and its ADR-012
  demotion).
