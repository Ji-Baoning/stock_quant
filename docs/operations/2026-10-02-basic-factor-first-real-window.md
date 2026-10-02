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

## 甄别性调用(2026-10-02,owner 批准)

Owner approved exactly one read-only diagnostic relay read to discriminate
transient vs systematic empty for the failed `daily` request above. This is a
diagnostic call, NOT an update round: no pipeline run, no dataset write, no
second read, no other endpoint/date/symbol. The one-shot script lived in
`/tmp` (never entered the repository); credentials were sourced from `.env`
per RUNBOOK convention and never read, printed, or recorded.

### Call shape (identical to the pipeline's failing request)

Built with the repository's own transport layer
(`build_transport(RELAY, SourceConfig())` → `TushareRelayClient.api`, the
official `DataApi` with its base URL rewritten to the relay — same class and
same construction the update lane uses), mirroring
`TushareSource._fetch_symbol_series`
(`src/stock_quant/data_sources/tushare.py`) verbatim:

```text
client.daily(ts_code="000001.SZ", start_date="20260925", end_date="20260925")
```

Transport initialized identically to the failed round: `kind=relay`,
host `jiaoch.top`, `sdk_version=1.4.24`. No `fields` parameter, no
`trade_date` — exactly the parameters the pipeline sent.

### Raw return

- `observed_at_utc`: `2026-10-02T02:21:42+00:00`
- Type: `pandas.DataFrame` (normal return, no exception)
- Rows: **0**; Columns: **`[]`** (empty frame with no field schema at all —
  the relay answered with an empty payload, consistent with the FATAL
  round's `"supplier returned an empty response"`)
- attrs: `{}`; head: `[]`
- Exception: none. Not a connection/timeout/rate-limit failure, so the
  sandbox-retry allowance never applied and was not used.

### Verdict: systematic empty (系统性空), per the owner's decision tree

The transport answered normally (successful initialization, normal HTTP
round-trip, a DataFrame came back) but `data` was empty: rows=0 with no
column schema. Under the owner's tree this is the "normal return with
rows=0 → systematic empty" branch, not the transient branch. Basis: the raw
return above — a later-in-the-day re-ask of the identical request shape that
FATALed earlier the same day still returns an empty body.

Scope caveat (recorded, not resolved): one data point cannot separate
"relay's `daily` endpoint is empty for every date" from "2026-09-25 is
unavailable on the relay". Discriminating those would need a further
diagnostic read against a baseline-known date (e.g. 2026-09-24) — out of
scope here and NOT executed.

### Recommendation for the real-window follow-up (owner to confirm)

Per the systematic-empty branch: switch the window or hold (挂起). The
"retry the same window" advice in the Follow-up section above is now
contradicted by evidence — a same-window re-run would most likely FATAL the
same way and spend another update round. Options for owner confirmation,
in order of information value: (a) authorize one more diagnostic read at a
baseline-known date to split endpoint-wide vs date-specific emptiness;
(b) pick a different minimal window for the real update; (c) hold and ask
the relay operator. No action was taken after the diagnostic call.

### Quota accounting

Exactly 1 diagnostic request reached the relay (the one read above). No
update round, no re-run, no other network call. Empty responses are not
counted as completed calls by the pipeline's ledger convention, but for this
diagnostic the accounting is stated directly: 1 request issued, 0 rows
returned.

### Second diagnostic read (2026-10-02, owner-approved): baseline-known date 2026-09-24

Owner approved exactly one more read-only diagnostic relay read (still no
update, no second round, no other endpoint/date/symbol) to split the scope
caveat above: endpoint-level failure vs date-level gap. The request shape is
byte-identical to the first diagnostic read — the repository's own transport
layer (`build_transport(RELAY, SourceConfig())` → `TushareRelayClient.api`,
mirroring `TushareSource._fetch_symbol_series` verbatim) — with only the
date changed to **2026-09-24**, a date the baseline CURRENT `daily_bar`
covers with 661 symbols:

```text
client.daily(ts_code="000001.SZ", start_date="20260924", end_date="20260924")
```

Transport initialized identically: `kind=relay`, host `jiaoch.top`,
`sdk_version=1.4.24`. The one-shot script stayed in `/tmp` (parameterized
copy of the first script, never entered the repository); credentials were
sourced from `.env` per RUNBOOK convention and never read, printed, or
recorded.

#### Raw return

- `observed_at_utc`: `2026-10-02T02:32:38+00:00`
- Type: `pandas.DataFrame` (normal return, no exception)
- Rows: **1**; Columns: **11** — `ts_code, trade_date, open, high, low,
  close, pre_close, change, pct_chg, vol, amount`
- attrs: `{}`; head (1 row): `{"ts_code": "000001.SZ", "trade_date":
  "20260924", "open": 11.35, "high": 11.47, "low": 11.29, "close": 11.3,
  "pre_close": 11.35, "change": -0.05, "pct_chg": -0.4405, "vol":
  1043818.72, "amount": 1186736.8957}`
- Exception: none. Not a connection/timeout/rate-limit failure, so the
  sandbox-retry allowance never applied and was not used.

#### Comparison of the two diagnostic reads

| Read | Date | Request shape | Result | Observed (UTC) |
| --- | --- | --- | --- | --- |
| 1st | 2026-09-25 | `client.daily(ts_code="000001.SZ", start_date="20260925", end_date="20260925")` | Normal return, rows=0, no column schema | 2026-10-02T02:21:42+00:00 |
| 2nd | 2026-09-24 | `client.daily(ts_code="000001.SZ", start_date="20260924", end_date="20260924")` | Normal return, rows=1, 11-column schema, populated values | 2026-10-02T02:32:38+00:00 |

#### Verdict: date-level gap (日期级缺口), per the owner's decision tree

The relay `daily` endpoint is alive and serving well-formed, populated data
for a baseline-known date eleven minutes after the same endpoint answered
empty for 2026-09-25. Under the owner's tree this is the "has data rows →
date-level gap" branch: 2026-09-25 data is missing / not yet ready on the
relay side, not an endpoint-level outage. Basis: the two reads above —
identical transport, identical request shape, only the date differs, and
only the 2026-09-25 ask comes back empty.

#### Recommendation for the real window (owner to confirm; no auto-follow-up)

Candidate window is the most recent trade day the relay demonstrably
serves. This read proves **2026-09-24 is obtainable** from the relay;
availability of 2026-09-28 / 2026-09-29 / 2026-09-30 is NOT proven by this
call. Two options for owner confirmation: (a) before the next real window,
issue one more diagnostic read targeted at the intended trade date, or
(b) switch the window directly and let the pipeline's own gates judge
naturally during the run. The same-window 2026-09-25 retry remains
contradicted by evidence and is not recommended.

#### Quota accounting (cumulative)

Exactly 1 diagnostic request reached the relay for this second read.
Cumulative for the two diagnostic reads: **2 requests** (1 + 1), no update
round, no re-run, no other network call.

## 定性修正(2026-10-02):09-25 为中秋节休市日

本节为 dated evidence 的追加修正,不改写上文任何已录原文。上文两处判定——
"Verdict: systematic empty (系统性空), per the owner's decision tree" 与
"Verdict: date-level gap (日期级缺口)"——的定性由本节修正:判定树推演时
默认 2026-09-25 是交易日,而它不是。

### 休市事实(上交所公告)

2026-09-25(星期五)为中秋节,A 股休市日。上海证券交易所公告《关于2026年
中秋节、国庆节休市安排的公告》(sse.com.cn 公告 `c_20260915_10832273`):
2026-09-25(星期五)至 2026-09-27(星期日)休市,2026-09-28(星期一)起照常
开市;2026-10-01 至 2026-10-07 休市,2026-10-08 起照常开市。

### 对两次甄别读数的重新定性

| 甄别 | 请求日期 | 当时判定 | 本节修正后的定性 |
| --- | --- | --- | --- |
| 第 1 次 | 2026-09-25 | systematic empty(系统性空) | 空 = **正确的休市行为**(该日无交易、无 `daily` 行),不是供应商数据缺口,更不是端点故障 |
| 第 2 次 | 2026-09-24 | date-level gap(日期级缺口) | 与休市事实互洽:开市日返回 1 行完整数据,证实 relay `daily` **端点健康**;09-25 并非"数据未就绪",而是该日本就不存在交易数据 |

两次读数合起来即是完整的解释:同一请求形态,开市日(09-24)有数据、
休市日(09-25)为空——relay 的 `daily` 端点行为正常。

### 真实窗口 FATAL 的机理

`TushareSource._fetch_symbol_series`
(`src/stock_quant/data_sources/tushare.py:136`)按**日期范围**逐票请求
(`start_date`/`end_date` 直接取自 update 窗口)。窗口 2026-09-25..2026-09-25
只含休市日 → 范围响应为空 → 管线 FATAL `source_fetch_failed`(即上文
"Real update round 1" 所录原文)。这是**既有管线行为**:仅含休市日的单日窗
会 FATAL,本应作 no-op 轮处理;它被本次"最小窗口"的选择撞出,**不是 P2c
回归**。含开市日的多日窗(如 2026-09-25..2026-09-30)范围响应含开市日行,
非空,可正常推进。

### 待办登记(离线处理,不阻塞真实窗口)

仅含休市日的单日 update 窗应作 no-op 而非 FATAL——属既有管线行为,留待
后续批次离线处理。本节不改代码、不跑测试;该待办不阻塞换窗与真实窗口推进。

### 换窗建议(待 owner 二次确认;不自动续跑)

最小合法真实窗口 = **2026-09-25..2026-09-30**:与基线
`published_end`=2026-09-24 日历连续;含休市日(09-25..09-27)与开市日
09-28/09-29/09-30;止于国庆休市(2026-10-01 起)前;`daily_basic`
@2026-09-30 已由 P1 探针证实可得。上文两节 "Recommendation" 中的换窗/
加甄别读选项由本条取代。正式 update 仍需 owner 二次确认后方可执行;在获得
确认前不自动续跑。

## 换窗重跑(2026-09-25..2026-09-30,owner 二次确认)

Owner 二次确认后的正式换窗执行轮。授权范围:恰好两轮真实 update(初始 +
同窗重跑)+ 一轮 validate,窗口固定 2026-09-25..2026-09-30 不扩大;除授权
命令外零真实网络调用;不跑 pytest。实际结果:**第一轮真实 update 即 FATAL,
按停止纪律未执行重跑轮与后续语义观察;数据集未变。**

### 授权与基线(执行前核验)

- 基线 `project/data/standardized/CURRENT` =
  `99f8ff28cdde53250fb315f2afcf478a84d084f61dfaf3d4602965c33d392f38`
  (9 张旧形态表,`daily_bar` 至 2026-09-24)。
- 预期(未达成):新版本 11 张表;`basic_factor` 首发空基线 →
  `supported_start`=2026-09-28;history 前缀段 `[锚, 09-27]` +
  fetched `[09-28, 09-30]`;`basic_factor_coverage` 同窗;`table_lineage`
  transport=`tushare:relay`。
- 重跑口径(owner 已确认):update 级重跑因 `run_id` 随机预计出新版本号;
  哈希不变性在内容寻址发布边界成立。——本轮未走到,见下。

### 命令原文(无凭据)

```text
set -a; . ./.env; set +a
TUSHARE_TRANSPORT=relay /home/ji/miniconda3/envs/sq312/bin/python \
  -m stock_quant data update --root project --start 2026-09-25 --end 2026-09-30
```

### 第一轮真实 update:退出码 1,FATAL

`run_id=data_update_f0a8784beb7b`,`resolved_end_date=2026-09-30`。relay
transport 正常初始化(`kind=relay`,host `jiaoch.top`,
`sdk_version=1.4.24`),失败发生在供应商响应边界:

```text
run_id=data_update_f0a8784beb7b
resolved_end_date=2026-09-30
ERROR=0 FATAL=1 INFO=0 WARNING=0
source tushare: not_ok(source_fetch_failed)
source akshare: not_ok(not_run)
source baostock: not_ok(not_run)
source xingyao: not_ok(not_run)
blocking issue: severity=FATAL code=source_fetch_failed table=data_update symbol=- trade_date=-
details={"endpoint": "daily", "message": "supplier returned an empty response", "source": "tushare", "symbol": "601059.SH"}
FAILED: publication gate did not pass; dataset unchanged
```

门禁正确拒绝发布,数据集未变。CURRENT 执行后核验仍为
`99f8ff28cdde…`。

### 定性与停止纪律

- 定性:供应商侧**单票**空响应——`601059.SH`(财达证券)在
  `daily` @窗口 2026-09-25..2026-09-30 的范围请求返回空体。与上文
  "单休市日空响应 FATAL" 机理不同:本窗含开市日 09-28/09-29/09-30,
  范围响应本应含行;空响应是**逐票请求**级别(`_fetch_symbol_series`
  按日期范围逐票请求),不是窗口级。一个待验证(未验证,不消耗网络调用)
  的候选解释:该票全窗停牌 → 范围内无任何行 → 既有管线行为类
  "全窗无行单票 FATAL",与"仅含休市日的单日窗 FATAL"同类,均非 P2c 回归。
- 非连接级失败:transport 正常初始化、正常往返、返回空体,无
  connection/timeout/rate-limit 迹象 → 关沙箱重试 allowance 不适用。
- 按停止纪律("不修、不放宽、不反复重试"):**同窗重跑(update 第二轮)
  未执行**,validate 未对"更新后的 CURRENT"执行(不存在新版本)。
  重跑口径(新版本号/哈希边界不变性)本轮未获 exercised。

### validate(对未变基线,离线只读)

```text
/home/ji/miniconda3/envs/sq312/bin/python -m stock_quant data validate --root project
version=99f8ff28cdde53250fb315f2afcf478a84d084f61dfaf3d4602965c33d392f38
ERROR=0 FATAL=0 INFO=0 WARNING=0
PASS
```

退出码 0。基线健康,佐证 "dataset unchanged"。

### 语义观察(全部不可观察)

无新版本,故 §7.4 "真实小窗口 raw→新 version→validate 通过" 条件的五项
落地证据(11 张表键集、`basic_factor`/`basic_factor_coverage` 行数、
`table_fetch_coverage` 段形态、coverage status 计数、
`table_lineage` transport)本轮**均不可观察**。只读核验基线未变:

- tables = 9(旧形态,无 `basic_factor`/`basic_factor_coverage`);
- `daily_bar` 1,721,796 行,max `trade_date` = 2026-09-24。

### 配额消耗

恰好一轮真实 update 到达供应商:至少一次 `601059.SH` @
2026-09-25..2026-09-30 的 `daily` 范围请求,返回空体(空响应不计入管线
ledger 的 completed calls,与前轮口径一致)。无第二轮、无重跑、无其它
网络调用;离线 validate 零网络。

### Follow-up(待 owner 决定;不自动续跑)

单票全窗空响应 FATAL 是否应按"停牌/无行单票 no-op"处理,属既有管线行为
待办(与"仅休市日单日窗 no-op"待办同类),本节不改代码。真实窗口推进的
候选路径:(a) 先做一次针对 `601059.SH` 或目标开市日的只读甄别读(owner
批准后);(b) 换窗避开该票无行的情形;(c) 挂起并询问 relay 运营方
`601059.SH` 在 09-28..09-30 的 `daily` 可得性。未经 owner 确认前不执行。
