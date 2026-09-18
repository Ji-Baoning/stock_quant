---
status: accepted
date: 2026-09-19
decision: A bankruptcy-reorganisation share transfer (CNINFO 分红类型 = 重整转增) is refused as its own quarantine reason and never booked, and that reason alone is non-blocking for the coverage verdict; every other reason, named or not, still withholds trust.
affects:
  - src/stock_quant/data_model/corporate_actions.py
  - src/stock_quant/data_pipeline.py
---

# 008 — Non-distributive corporate events

## Context

CNINFO types every 分红 row with `分红类型`. Three of its values name a share
transfer that never reaches a pre-event holder:

| 分红类型 | What it is | Reaches holders? |
| --- | --- | --- |
| 重整转增 | Shares created in a bankruptcy reorganisation and issued to creditors / the reorganisation investor | No |
| 承诺补偿 | A controlling shareholder's share transfer to public holders as performance compensation | Yes, but total capital is unchanged |
| 股改分红 | A split-share-reform distribution | Yes, and it is a real ex-date event |

深交所《上市公司自律监管指引第14号——破产重整等事项》§39 states that 重整转增
is not distributed to existing shareholders, which is why no exchange ex-date
adjustment exists for it.

Two independent defects followed from not knowing this. Both are measured on
the published dataset `CURRENT = 01c74bee…` (window 2015-01-05..2026-08-28,
6,896 accepted / 118 quarantined rows, 11 `UNTRUSTED` coverage rows) and on the
raw CNINFO snapshots it was built from (1,991 snapshots, 34,475 rows, 654
symbols).

**Defect 1 — a real event read as a gap.** Ten in-window quarantined rows are
blocking the coverage verdict. Joined to their supplier-declared type:

| 分红类型 | Rows | Symbols |
| --- | --- | --- |
| 重整转增 | 6 | 000656, 000792, 000793, 002157, 002310, 002608 |
| 承诺补偿 | 2 | 002131, 600733 |
| 年度分红 | 2 | 002269 |

Every 重整转增 row was quarantined as `incomplete` — because the supplier
reports no 除权日 for it — which is a factually wrong reading: the row is a
complete report of an event that has no ex-date. Its effect was to mark six
symbols `UNTRUSTED` for the whole window and block holdings in them, even
though each of the six holds 3-8 accepted, cross-confirmed in-window events.

**Defect 2 — a phantom return.** The corpus holds exactly 12 重整转增 events, and
six of them *did* carry a supplier ex-date and were therefore booked. All six are
single-source `cninfo` rows. `adjusted_bar` is a backward-adjusted series, so
each applied its own `1 + capitalization_ratio` factor at the ex-date:

| Symbol | ex_date | Booked `capitalization_ratio` | `adjusted_close` across ex | Raw close across ex | Phantom |
| --- | --- | --- | --- | --- | --- |
| 600157.SH | 2020-12-29 | 0.78800 | +72.46% | −3.55% | **+76.01 pts** |
| 600221.SH | 2021-12-06 | 1.00000 | +90.24% | −4.88% | **+95.12 pts** |
| 600515.SH | 2021-12-22 | 1.92387 | +88.18% | −35.64% | **+123.82 pts** |
| 600518.SH | 2021-12-15 | 1.80000 | +150.04% | −10.70% | **+160.74 pts** |
| 600654.SH | 2022-12-23 | 1.19014 | +107.53% | −5.24% | **+112.77 pts** |
| 600666.SH | 2023-02-20 | 1.50000 | +65.49% | −33.80% | **+99.30 pts** |

Ordinary distributions on the same symbols show a phantom of +0.55 / +0.14 /
+0.67 pts — the ordinary forward-fill residual of the adjustment. The
attribution is exact rather than statistical: `applied_action_ids` names
`"600515.SH#2021-12-22"`, and the ratio between the phantom's factor and its own
control equals `1 + capitalization_ratio` to the digit (600518:
5.85999 / 2.09285 = 2.80000 = 1 + 1.8).

The defect is in `adjusted_bar` — a published table and the project's
total-return basis — rather than in a factor already computed. The factor is a
permanent level shift from the ex-date forward, so it also inflates every later
date of the series: 600221 is a `custom_csi300_tw_tradable` member again from
2026-06-30 and 600515 since 2023-12-29, so both carry the inflated level today.
It does **not** follow that a research result was distorted: a constant factor
cancels in a return, and the distortion is confined to a window *containing* the
ex-date. On the published membership no ex-date of the six falls inside an active
membership period, and no 60-day window containing one overlaps a membership
either — so no research return spanned a phantom.

An earlier finding is recorded here so it is not re-opened: 002269.SZ's
`cross_source_conflict` is a **口径** difference, not an error. The real plan is
5 shares of 红股 plus 1 share of 盈余公积转增 plus 9 of 资本公积转增; CNINFO books
the 盈余公积 tranche under 送股 because it is taxed as a dividend, while
Eastmoney books it under 转增. The totals agree (15 per 10 shares), and
`apply_corporate_action` sums `bonus + capitalization`, so the project is
unaffected. It is untouched by this ADR.

## Decision

1. `_FIELD_ALIASES` gains `distribution_type` → `分红类型`, and the field is
   **optional in both lanes**. Only CNINFO's dividend interface publishes it;
   CNINFO's legacy layout, the Eastmoney fallback and the allotment lane do not
   have the column at all, and an absent column must read as "ordinary
   distribution" rather than withhold every row of the frame.
2. `_reject_reason` refuses `分红类型 == 重整转增` with a new reason,
   `non_distributive_restructuring`. The branch runs **before** the
   date-completeness check, because a 重整转增 is never a price event: its
   supplier ex-date is spurious and its absent one is expected. Letting it fall
   through would either book it (defect 2) or report a complete event as an
   incomplete record (defect 1).
3. `_coverage_verdict` exempts exactly that one reason from withholding trust,
   via a named deny-list. The exempt set is `{non_distributive_restructuring}`
   and the test is `reasons - _NON_BLOCKING_QUARANTINE_REASONS`, so a reason
   added later blocks unless it is deliberately named. The refusal itself is
   unchanged: the row is still quarantined and still never booked.

The fix keys on CNINFO alone, and that is sufficient: the Eastmoney fallback
carries no `分红类型` column and — measured across the nine affected symbols —
reports **none** of these events at all (zero Eastmoney rows share any of the
six in-window events' dates or the six phantoms' ex-dates). CNINFO is the
only source that reports a 重整转增, so no booking can arrive from the other
lane untyped.

Only 重整转增 is refused. 承诺补偿 is deliberately left as a known gap: the
transferee really does receive shares, so booking it is economically wrong but
not a share-count error, and total capital does not change. 股改分红 must stay
bookable — 600733's 10转25 (ex 2018-09-19) is a correct, currently-booked
ex-date event, and 000629's 股改分红 10转3 has no ex-date at all — so a rule
keyed on "non-standard type" would break the former to fix nothing. Measured
over the whole corpus, 股改分红 is split 46 with an ex-date / 90 without, and
重整转增 18 / 18: neither type implies "no ex-date", which is why the rule keys
on the type and not on the missing date.

## Consequences

- 000656 / 000792 / 000793 / 002157 / 002310 / 002608: `UNTRUSTED` →
  `VERIFIED`. `has_accepted` is already true for all six, so no verdict branch
  was added for that case.
- 600157 / 600221 / 600515 / 600518 / 600654 / 600666: the phantom factor is
  removed from `adjusted_bar` — 76.01 to 160.74 points of spurious return each.
  Coverage for all six stays `VERIFIED` either way: the refused rows were being
  accepted, not quarantined, so no verdict ever depended on them, and
  `has_accepted` stays true because each symbol still holds other accepted
  in-window events.
- 002131 / 600733 (承诺补偿) and 002269.SZ (口径) are unchanged.
- 000503.SZ and 000629.SZ stay `UNTRUSTED` / `FACTS_INCOMPLETE`. They have **no
  blocking quarantine row at all** — their only quarantine row is excluded by
  ADR-006's `announcement_pre_window_implemented` / `record_date_out_of_window`
  branch, and they hold no accepted in-window event. ADR-006 §Consequences
  already records this as the correct outcome, and this ADR does not touch it.
- `RECONCILED_COLUMNS`, `QUARANTINE_COLUMNS`, the `CoverageReason` enum and
  `research/trust.py` are unchanged. `distribution_type` is consumed at
  reconciliation and not added to any published schema.
- The published dataset is content-addressed, so this lands as a **new
  version**; `CURRENT` is re-pointed, never edited.

## Rejected alternatives

- **Book 重整转增 as a distribution.** This is defect 2. Inventing a
  `1 + capitalization_ratio` factor for shares that go to creditors turns a
  −35.64% quarter into +88.18%.
- **Key the refusal on "the row has no ex-date", not on its type.** It would
  also swallow the genuinely incomplete rows (000656's 2025-09-09 event is
  unusable for an unrelated reason), and it restates the symptom instead of the
  cause. The type is the cause.
- **Refuse every non-standard type (adding 承诺补偿 and 股改分红).** It breaks
  600733's correctly booked 股改分红, and it refuses a 承诺补偿 that is merely
  economically mis-specified rather than a share-count error.
- **Add a new `CoverageReason` value so the window reads `UNTRUSTED` with a
  clearer label.** The window is not untrusted: nothing in it is unaccounted
  for. A new enum value would also change the published coverage schema for a
  row that is, by construction, correctly reported.
- **Allow-list the blocking reasons instead of deny-listing the exempt one.**
  An allow-list fails open: a quarantine reason added later would silently stop
  withholding trust. The deny-list keeps the invariant that a new reason blocks
  until someone names it here.
- **Leave it as `incomplete` and accept the six unverifiable symbols.** The
  label is wrong, and the cost is six symbols permanently unverifiable for a
  reason that has nothing to do with data quality.

## Risk this decision accepts

The exemption widens what may pass a coverage gate, so the widening is bounded
here rather than assumed away.

**The exemption is keyed on a supplier string.** If CNINFO ever typed a genuine
distribution as 重整转增, this rule would refuse it and — because the refusal is
non-blocking — the affected window would read `VERIFIED` while a real
transition went unbooked. Three things bound this: the refusal still always
happens, so nothing is silently booked either way; the string is a supplier
classification of a legally defined event, not free text; and a row so typed
still cannot enter the adjustment recursion unless it carries an ex-date, in
which case it is refused before it gets one.

**The six symbols become verifiable without a human review.** They were
`UNTRUSTED` partly because a human had not yet ruled on the 重整转增 rows. This
ADR replaces that review with a rule. The rule is grounded in 深交所 §39 and was
checked across the whole raw corpus (1,991 CNINFO snapshots, 34,475 rows, 654
symbols): of the 69 standard-type rows that lack an ex-date, **none** was
announced inside the window, so refusing 重整转增 cannot be masking a
standard-type gap. But it is a rule, not a signed review.

**A real in-window transition could hide behind the exemption.** If a symbol's
only in-window evidence were a 重整转增, the exemption would leave it
`UNTRUSTED` / `FACTS_INCOMPLETE` anyway, because `has_accepted` is false — the
exemption cannot manufacture trust, it can only stop a correctly-reported event
from destroying trust that other accepted rows already established.

## Evidence

`tests/unit/test_corporate_action_normalize.py`
(`test_restructuring_capitalization_is_quarantined_not_booked`,
`test_restructuring_capitalization_without_ex_date_is_not_incomplete`,
`test_share_reform_capitalization_is_still_booked`,
`test_ordinary_distribution_is_unaffected_by_its_declared_type`,
`test_absent_type_column_is_treated_as_an_ordinary_distribution`);
`tests/unit/test_corporate_action_coverage.py`
(`test_refused_non_distributive_event_does_not_untrust_the_window`,
`test_unaccounted_quarantine_reasons_still_untrust_the_window`).

Measured footing: `docs/research/2026-09-19-data-supply-capability-report.md`
(source survey) and the published `corporate_action_quarantine` /
`corporate_action_coverage` / `adjusted_bar` tables of `CURRENT = 01c74bee…`.

**Blast radius, corpus-wide.** Re-normalizing all 654 symbols from all 1,991 raw
CNINFO snapshots, before and after this change, moves exactly **six** accepted
ex-dates — the six phantom rows above — and newly accepts **none**. The
comparison is a `git worktree` at the change's parent commit, run with
`PYTHONPATH` pinned to that tree (an editable install otherwise resolves to the
working tree and silently makes the two runs identical). Thirty-six further
ex-dates differ between a CNINFO-only re-normalization and the published table
either way; they are the ordinary single-source-vs-cross-source difference and
are unchanged by this ADR.
