# 2026-10-02 basic_factor first real window: update blocked by supplier empty response

## Status: NOT COMPLETED (no new version; dataset unchanged)

The authorized real minimal window did not produce a new dataset version. The
first real fetch round failed at the supplier boundary (tushare relay returned
an empty response for the `daily` endpoint) and the publication gate rejected
the run. Per the task discipline ("do not widen, do not repeat real rounds to
spend quota"), no second update round and no same-window re-run were executed.

## Authorization

- Authorized by: owner, this session ("授权真实最小窗口").
- Scope: one minimal legal window `data update` + one `data validate` + a
  same-window re-run against the real dataset root `project/` — two update
  rounds and one validate round in total; no additional real network calls
  beyond these commands.
- Window: `2026-09-25..2026-09-25` (first trading day after the baseline
  `published_end` 2026-09-24; 2026-09-25 is a Friday).
- Credentials: existence checked only (`grep -c` / key-name listing); zero
  credential values read, printed, or recorded.

## Baseline (read-only, before any real command)

- `project/data/standardized/CURRENT` =
  `99f8ff28cdde53250fb315f2afcf478a84d084f61dfaf3d4602965c33d392f38`
- Table set: 9 tables (old shape, no `basic_factor` / `basic_factor_coverage`):
  `adjusted_bar`, `corporate_action`, `corporate_action_coverage`,
  `corporate_action_quarantine`, `daily_bar`, `security_master`,
  `security_master_coverage`, `trading_calendar`, `universe_membership`.
- `daily_bar`: rows 1,721,796, actual date span 2015-01-05 .. 2026-09-24
  (matches expected baseline `published_end` 2026-09-24).
- Acceptance anchor: `project/acceptance-99f8ff28.yml`
  (`policy_version: real-data-v1`, `dataset_version` matches CURRENT).

## Commands executed (verbatim; no credential or environment values)

Baseline round — failed before any network I/O (missing pipeline precondition):

```text
TUSHARE_TRANSPORT unset; then:
/home/ji/miniconda3/envs/sq312/bin/python -m stock_quant data update \
  --root project --start 2026-09-25 --end 2026-09-25
```

Real round (after setting the pipeline-required explicit transport mode
`TUSHARE_TRANSPORT=relay` — an enumerated transport selector demanded by the
error message itself, not a credential and not a gate weakening):

```text
TUSHARE_TRANSPORT=relay /home/ji/miniconda3/envs/sq312/bin/python \
  -m stock_quant data update --root project --start 2026-09-25 --end 2026-09-25
```

Validation (offline, read-only over the published version):

```text
/home/ji/miniconda3/envs/sq312/bin/python -m stock_quant data validate --root project
```

## Results

### Precondition round (no quota spent)

Transport initialization failed before any request; dataset unchanged. The run
left `run_id=data_update_54cf47ea308f`.

```text
ERROR=0 FATAL=1 INFO=0 WARNING=0
blocking issue: severity=FATAL code=source_fetch_failed table=data_update symbol=- trade_date=-
details={"endpoint": "trade_cal", "message": "cannot initialise source 'tushare': TUSHARE_TRANSPORT must be set explicitly for a published build (expected 'relay'); this path never falls back", "source": "tushare"}
FAILED: publication gate did not pass; dataset unchanged
```

### Real update round 1 (the only round with real network I/O)

Exit code 1. Relay transport initialized successfully
(`kind=relay`, `sdk_version=1.4.24`). The fetch failed on the first `daily`
request; `run_id=data_update_b653dd5a6a22`.

```text
run_id=data_update_b653dd5a6a22
resolved_end_date=2026-09-25
ERROR=0 FATAL=1 INFO=0 WARNING=0
source tushare: not_ok(source_fetch_failed)
source akshare: not_ok(not_run)
source baostock: not_ok(not_run)
source xingyao: not_ok(not_run)
blocking issue: severity=FATAL code=source_fetch_failed table=data_update symbol=- trade_date=-
details={"endpoint": "daily", "message": "supplier returned an empty response", "source": "tushare", "symbol": "000001.SZ"}
FAILED: publication gate did not pass; dataset unchanged
```

Classification: a supplier-side empty response (vendor-level), not a
connection-level failure and not a sandbox restriction, so the single
sandbox-retry allowance does not apply. Per the no-quota-burn discipline the
same-window re-run (update round 2) was NOT executed.

### data validate (baseline version, after the failed update)

Exit code 0 — baseline still healthy, corroborating "dataset unchanged":

```text
version=99f8ff28cdde53250fb315f2afcf478a84d084f61dfaf3d4602965c33d392f38
ERROR=0 FATAL=0 INFO=0 WARNING=0
PASS
```

## Post-state

- `project/data/standardized/CURRENT` unchanged:
  `99f8ff28cdde53250f...` (verified after the failed round).
- No new dataset version, no `basic_factor` table yet; therefore the planned
  semantic observations (basic_factor row counts, history prefix
  `[anchor, 2026-09-24]` vs fetched segment, coverage UNTRUSTED/VERIFIED row
  counts, `table_lineage` transport values) are not observable this round.
- Re-run hash expectation (fd794ed12 test docstring): update-level re-runs get
  a new version id because `run_id` is random and identity-bearing; hash
  invariance holds at the content-addressed publication boundary. Not exercised
  this round — no successful first round to re-run.

## Quota spent

Exactly one real round reached the supplier: at least one `daily` request for
`000001.SZ` answered with an empty body. The run's `call_ledger.json` records
`tushare.calls = 0` (empty responses are not counted as completed calls). The
precondition round issued no network traffic.

## Follow-up

Retry the same minimal window `2026-09-25..2026-09-25` with
`TUSHARE_TRANSPORT=relay` once the relay supplier serves the `daily` endpoint
again (check the relay operator / probe with a read-only endpoint first), then
resume the authorized sequence: update → validate → same-window re-run →
observe basic_factor semantics in the new version manifest.
