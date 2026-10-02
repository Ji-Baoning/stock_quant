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

## 第三轮:ADR-023 物化后的换窗重跑(2026-09-25..2026-09-30,owner 放行)

Owner 第三次放行后的执行轮。授权范围:恰好两轮真实 update(初始 + 同窗
重跑)+ 一轮 validate,窗口固定 2026-09-25..2026-09-30 不扩大;除授权命令
外零真实网络调用;不跑 pytest。实际结果:**第一轮真实 update 仍 FATAL,但
失败点已从 `601059.SH` 后移到 `601198.SH`——ADR-023 的 carry-forward
分支在真实窗口首次触发并为 `601059.SH` 完成物化判定(INFO=1),随后
`601198.SH` 因不属于 active membership 走 fail-closed FATAL。按停止纪律
未执行重跑轮;数据集未变。**

### 授权与基线(执行前后核验,只读)

- 基线/执行后 `project/data/standardized/CURRENT` 均为
  `99f8ff28cdde53250fb315f2afcf478a84d084f61dfaf3d4602965c33d392f38`
  (9 张旧形态 parquet 表,无 `basic_factor`/`basic_factor_coverage`;
  `daily_bar` 1,721,796 行,2015-01-05..2026-09-24)。执行后核验未变。
- 重跑口径(owner 已确认):update 级重跑因 `run_id` 随机预计出新版本号,
  哈希不变性在内容寻址发布边界成立。——本轮未走到(第一轮即 FATAL)。

### 命令原文(无凭据)

```text
set -a; . ./.env; set +a
TUSHARE_TRANSPORT=relay /home/ji/miniconda3/envs/sq312/bin/python \
  -m stock_quant data update --root project --start 2026-09-25 --end 2026-09-30
```

```text
/home/ji/miniconda3/envs/sq312/bin/python -m stock_quant data validate --root project
```

### 第一轮真实 update:退出码 1,FATAL(失败点后移至 601198.SH)

`run_id=data_update_f15731edfbe8`,`resolved_end_date=2026-09-30`,运行窗
13:41:0x..13:41:43(+0800)。relay transport 正常初始化(`kind=relay`,
host `jiaoch.top`,`sdk_version=1.4.24`)。控制台完整摘要:

```text
run_id=data_update_f15731edfbe8
resolved_end_date=2026-09-30
ERROR=0 FATAL=1 INFO=1 WARNING=0
source tushare: not_ok(source_fetch_failed)
source akshare: not_ok(not_run)
source baostock: not_ok(not_run)
source xingyao: not_ok(not_run)
blocking issue: severity=FATAL code=source_fetch_failed table=data_update symbol=- trade_date=-
details={"endpoint": "daily", "message": "supplier returned an empty response", "source": "tushare", "symbol": "601198.SH"}
FAILED: publication gate did not pass; dataset unchanged
```

门禁正确拒绝发布;CURRENT 执行后核验仍为 `99f8ff28cdde…`。无新版本号。

### INFO=1 的归因(观察事实 vs 推导,分列)

观察事实:摘要 `INFO=1`;CLI 对被拒发布只渲染 blocking 行
(`_echo_blocking_issues` 只输出 FATAL/ERROR),INFO 事件体未输出;失败运行
的 run 目录只持久化 `call_ledger.json`(无 `quality_report.json`),事件体
不可从工件直读。推导链:沿本轮实际执行路径穷举 INFO 发射点——日历通道的
`calendar_pre_coverage_boundary` 仅当合并后开市日表为空才发(本轮有携带
日历,不成立);CA 通道因 fetch 停止未运行;`_read_baseline`/
`_universe_master_issues` 不发 INFO——唯一到达的 INFO 发射点是 ADR-023
`carry_forward_rows`(`kind="carry_forward"`)。结合本轮空响应集合恰为
{601059.SH(判定 proved), 601198.SH(走 FATAL 分支,不发 INFO)},该
INFO=1 即 `601059.SH` 的 carry-forward 物化事件。此为强支撑推断,非工件
直读,特此标注。

### ADR-023 触发情况(核心语义结果)

两票基线尾部**均为** `tushare_suspend` 至 2026-09-24(条件 (c) 对两票都
成立),判定真正的 discriminator 是条件 (a) membership 状态:

| 条件 | 601059.SH 信达证券 | 601198.SH 东兴证券 |
| --- | --- | --- |
| (a) universe membership active | 是(`custom_csi300_tw_tradable`,`initial_constituent`,active) | **否**(`status=removed`,`raw_effective_to` 2021-06-29) |
| (b) security_master 无退市 | `delist_date=None` | `delist_date=None` |
| (c) 边界 bar 为 09-24 `tushare_suspend` | 是,close=15.56,volume=0 | 是,close=13.06,volume=0 |
| (d) 窗口开市日 ≤ `MAX_CARRY_FORWARD_DAYS=10` | 3 天(09-28/29/30) | 3 天 |
| 判定 | **proved** → carry-forward 三根(09-28/29/30 @15.56、零量、`tushare_suspend`)+ INFO,进程内物化 | **unproved** → fail-closed FATAL(与 ADR-023 之前行为完全一致) |

- `601059.SH`:ADR-023 证据类四条件全部成立,carry-forward 行与
  `suspension_row(kind=carry_forward)` INFO 在进程内完成;因随后
  `601198.SH` FATAL、门禁拒绝发布,未落任何版本。
- `601198.SH`:空响应整窗,按 fail-closed 保持既有 FATAL 原文(见上)。
  该票虽在基线中同为停牌尾,但已于 2021-06-29 移出 universe,不是
  active fact——证据类如设计地只携带**被指数携带**的停牌票。
- 本轮迭代按 symbol 序进行,fetch 在 `601198.SH` 处停止;其后是否还有
  同类空响应票,本轮未探测,未知。

### relay 窗口供数观察与配额(raw store 只读取证)

- 本轮 `call_ledger.json`:`tushare.calls=0`,`reused.daily=485`(ledger
  口径照录;raw store 证据表明该计数不含逐票 daily 派发,与既有"空响应
  不计 completed calls"口径并存,语义以 raw store 为准)。
- raw store 本轮新持久化 24 个逐票窗口响应(48 个文件,13:41:11..13:41:42
  +0800):`601066.SH..601186.SH` 连续区间,**每票 `row_count=3`**——恰为
  开市日 09-28/29/30。即 relay `daily` 已对本窗普通在市股正常供数,空响应
  局限于停牌票。
- 本轮到达 relay 的真实请求合计 26 次:24 次成功逐票 `daily` + 2 次空响应
  (`601059.SH`、`601198.SH`,空响应不持久化故无 manifest);`trade_cal`
  走 raw store 复用,零请求。无其它网络调用。
- 485 个复用响应来自更早轮次写入的 raw store(ADR-015 复用),本轮零网络。

### 停止纪律执行情况

- 定性:供应商侧单票空响应,transport 正常初始化、正常往返、返回空体,
  非 connection/timeout/rate-limit → 关沙箱重试 allowance 不适用、未使用。
- 同窗重跑(update 第二轮)**未执行**;无新版本,"两轮 version 哈希"口径
  未获 exercised;validate 对未变基线离线只读执行:

```text
version=99f8ff28cdde53250fb315f2afcf478a84d084f61dfaf3d4602965c33d392f38
ERROR=0 FATAL=0 INFO=0 WARNING=0
PASS
```

退出码 0。基线健康,佐证 "dataset unchanged"。

### 语义观察五项(对新版本均不可观察)

无新版本,§7.4 五项落地证据(11 张表键集、`basic_factor`/`basic_factor_coverage`
行数、`table_fetch_coverage` 段形态、coverage status 计数、`table_lineage`
transport)本轮不可观察。只读核验基线:9 张旧形态表;无
`basic_factor*` 表;`daily_bar` 1,721,796 行、末日 2026-09-24;
`601059.SH` 尾四根 09-21..09-24 均 `tushare_suspend`/15.56/0 量。

### Follow-up(待 owner 决定;不自动续跑)

停牌且**不属于 active membership** 的票(如 `601198.SH`,removed 停牌中)
整窗空响应会按 fail-closed FATAL 拒绝整轮发布——这是 ADR-023 有意收窄的
证据类边界,不是回归。候选路径:(a) owner 裁决是否把证据类扩展到
"removed 但停牌尾可证明"的票(ADR 变更);(b) 挂起等待 relay 对这些票
恢复供行;(c) 原样重跑,待 relay 供行后自然通过(485+24 已缓存,重跑
网络开销趋零)。窗口在任何路径下保持 2026-09-25..2026-09-30。未经 owner
确认前不执行;本节不改代码、未跑 pytest。credentials 仅按惯例 source,
零读取、零打印、零记录。在途 WIP(RUNBOOK.md、cli.py、bootstrap.py、
reporting 模板、相关 tests 及未跟踪 plans/ 等)一律未动;本次仅追加并
提交本 evidence 文件。

## 第四轮:补注条件 (a) 后的重跑(2026-09-25..2026-09-30,owner 放行)

Owner 第四次放行后的执行轮。授权范围:恰好两轮真实 update(初始 + 同窗
重跑)+ 一轮 validate;窗口固定 2026-09-25..2026-09-30 不扩大;除授权命令
外零真实网络调用;不跑 pytest。**结果:两轮 update 均退出码 0 且 PASS,
各发布一个新内容寻址版本(`2650eaab…`、`3d172132…`);validate PASS;
ADR-023 补注后的条件 (a)("security master carried 且 delist_date 为空
或晚于窗口末,master 缺股 fail-closed")在真实窗口首次对 `601059.SH` 与
`601198.SH` 同时完成 carry-forward 物化。§7.4 真实小窗口条件已闭环。**

### 命令原文(无凭据)

两轮 update 命令完全相同:

```text
set -a; . ./.env; set +a
TUSHARE_TRANSPORT=relay /home/ji/miniconda3/envs/sq312/bin/python \
  -m stock_quant data update --root project --start 2026-09-25 --end 2026-09-30
```

validate(离线只读,授权恰好一轮,按步骤序在第一轮 update 之后执行):

```text
/home/ji/miniconda3/envs/sq312/bin/python -m stock_quant data validate --root project
```

### 第一轮真实 update:退出码 0,PASS,发布 2650eaab…

`run_id=data_update_f75a814a16f0`,`resolved_end_date=2026-09-30`。relay
transport 正常初始化(`kind=relay`,host `jiaoch.top`,
`sdk_version=1.4.24`)。控制台完整摘要:

```text
run_id=data_update_f75a814a16f0
resolved_end_date=2026-09-30
ERROR=0 FATAL=0 INFO=64 WARNING=2
source tushare: ok
source akshare: ok
source baostock: not_ok(not_run)
source xingyao: not_ok(partial_fetch_failure)
dataset_version=2650eaab2ed70df8d19f10e841537caa991a269a3961118d03c0005ec57d15de
PASS
```

退出码 0;执行后 `project/data/standardized/CURRENT` =
`2650eaab2ed70df8d19f10e841537caa991a269a3961118d03c0005ec57d15de`。

### validate:PASS(授权的一轮)

```text
version=2650eaab2ed70df8d19f10e841537caa991a269a3961118d03c0005ec57d15de
ERROR=0 FATAL=0 INFO=0 WARNING=0
PASS
```

退出码 0。按授权步骤序(validate 位于两轮 update 之间),第二轮重跑发布
的 `3d172132…` 未再单独跑 validate 命令——其发布前门禁在 update 进程内
以同一套检查执行并通过(见下),且授权恰好一轮 validate,未加跑。

### 第二轮同窗重跑:退出码 0,PASS,发布 3d172132…,零网络

`run_id=data_update_552b31a5c28a`,`resolved_end_date=2026-09-30`。摘要:

```text
run_id=data_update_552b31a5c28a
resolved_end_date=2026-09-30
ERROR=0 FATAL=0 INFO=61 WARNING=0
source tushare: not_ok(not_run)
source akshare: not_ok(not_run)
source baostock: not_ok(not_run)
source xingyao: not_ok(not_run)
dataset_version=3d172132fb8629b3d27eeaef8e24e70b082909229553f00c74c7e0aa0d55d2b6
PASS
```

重跑口径(owner 已确认)成立证据:第二轮 `build_config.baseline_version`
= 第一轮版本 `2650eaab…`;四源全部 `not_run`(零网络,`raw_snapshot_reuse`
为 null,call_ledger 仅含 akshare 空条目);但因 `run_id` 随机进入
`build_config`,内容寻址版本号变为 `3d172132…`。两版本逐 parquet 文件
SHA256 对比:**11 张表中 10 张字节级完全一致**;唯一不同的
`corporate_action_coverage.parquet` 逐列核对后仅 `checked_at`(墙钟)
一列不同(07:30:51Z vs 08:01:15Z),`symbol/window/status/reason/
sources/snapshot_hashes` 全部相等(status 计数同为 VERIFIED 1,349 /
VERIFIED_EMPTY 1,287);`dataset_manifest.json` 与 `quality_report.json`
不同(run_id、时间戳等 run 元数据)。即:**数据层逐字节等价,哈希差异
全部落在发布边界的 run 元数据上,哈希不变性口径在内容寻址发布边界成立。**

### 语义观察五项(只读,对 2650eaab… 观察;3d172132… 逐项同值)

1. **表键集**:11 张 parquet——`adjusted_bar`、`basic_factor`、
   `basic_factor_coverage`、`corporate_action`、`corporate_action_coverage`、
   `corporate_action_quarantine`、`daily_bar`、`security_master`、
   `security_master_coverage`、`trading_calendar`、`universe_membership`
   (旧 9 张 + 新 2 张,符合预期)。
2. **行数**:`basic_factor` 16,667 行(列 `trade_date, symbol, market_cap,
   turnover_rate, source, ingested_at`,值域 2026-09-28..2026-09-30);
   `basic_factor_coverage` 5,567 行(列 `symbol, window_start, window_end,
   status, reason, sources, snapshot_hashes, checked_at`)。
3. **`table_fetch_coverage` 段形态**:`basic_factor` 与
   `basic_factor_coverage` 均为两段——`{kind: not_fetched, reason:
   history_begins_after_anchor, 2015-01-05..2026-09-27}`(history 前缀)+
   `{kind: fetched, 2026-09-28..2026-09-30}`(本窗)。第二轮同窗确认形态:
   单段 `{kind: not_fetched, reason: operator_explicit_window,
   2026-09-25..2026-09-30}`(整窗已被 baseline 覆盖,无新抓取,故第二轮
   `build_config` 无 `table_lineage` 键)。
4. **coverage status 计数**:`basic_factor_coverage` status = VERIFIED
   5,564 / UNTRUSTED 3;3 条 UNTRUSTED 恰为三张停牌物化票
   (`601059.SH`、`601198.SH`、`601238.SH`),reason=`FACTS_INCOMPLETE`,
   sources=`[{"endpoint": "daily_basic", "outcome": "success_with_events"}]`
   ——对停牌票 basic_factor 事实不完整按 fail-closed 标记,语义自洽。
5. **lineage**:`build_config.table_lineage["basic_factor"]["transport"]`
   = `tushare:relay`;其 `raw_snapshot` = `{endpoint: daily_basic,
   source: tushare, transport_id: jiaoch.top}`(`basic_factor_coverage`
   同)。附:`daily_bar` 全表 1,723,779 行(基线 1,721,796 + 1,983),
   值域 2015-01-05..**2026-09-30**(末日符合预期)。

### ADR-023 触发情况(两票物化对照,核心语义结果)

第一轮 INFO=64 的构成(quality_report 直读,本轮起事件体可从发布版本
`quality_report.json` 的 `issues` 直读,不再依赖推导):INFO
`quarantine_out_of_window` ×61(CA 通道隔离分支,55 个 symbol,窗口
2026-09-25..2026-09-30,如 `record_date_out_of_window` /
`announcement_pre_window_implemented`);INFO `suspension_row_materialized`
×3;WARNING `optional_source_failure` ×2。**两票对照:**

| 项 | 601059.SH 信达证券 | 601198.SH 东兴证券 |
| --- | --- | --- |
| INFO 事件 | `suspension_row_materialized`,details `{"kind": "carry_forward", "run": "2026-09-28..2026-09-30", "days": 3}` | 同左,`kind=carry_forward`、days=3 |
| daily_bar 09-28/29/30 | 三根均 `tushare_suspend`,OHLC=15.56,`volume=0`、`amount=0`(与 09-24 收盘平价) | 三根均 `tushare_suspend`,OHLC=13.06,`volume=0`、`amount=0`(与 09-24 收盘平价) |
| 第三轮行为 | proved → 进程内物化(未发布) | unproved → fail-closed FATAL |
| 本轮行为 | **物化并随发布落盘** | **物化并随发布落盘** |

即:补注把条件 (a) 从 "universe membership active" 放宽为 "security
master carried 且 delist_date 为空或晚于窗口末" 后,`601198.SH`(removed
但仍在 master、未退市)从第三轮的 FATAL 变为本轮的 carry-forward 三根,
与 `601059.SH` 对称落盘;76851131d 的实现(suspension carry-forward 改按
master listing 而非 membership 判定)在真实窗口得到验证。第三票
`601238.SH` 的 INFO(details 无 `kind` 字段,`days=1`、
`action_ex_dates=[]`)对应其 09-28 单日停牌行(平价 5.09、零量),
09-29/30 恢复正常交易(`tushare` 真实行,量 8,135,031 / 199,466,750)。

窗口行构成(只读核对):新窗 1,983 行 = 661 symbol × 3 开市日(09-28/29/30
每日恰 661 行);source 分布 `tushare` 1,970、`tushare_suspend` 7(上述
3+3+1)、`akshare` 6(指数 `000300.SH`/`000905.SH` 各 3 根,对应 akshare
`index_history` fetched=2 的两个快照)。

### 异常与 WARNING 原文(非阻断,如实记录)

第一轮 WARNING `optional_source_failure` ×2(xingyao 为 optional source,
不阻断发布;对应摘要行 `source xingyao: not_ok(partial_fetch_failure)`):

```text
{"source": "xingyao", "endpoint": "daily", "symbol": "601059.SH", "message": "the supplier's answer for this code is not a frame"}
{"source": "xingyao", "endpoint": "daily", "symbol": "601198.SH", "message": "the supplier's answer for this code is not a frame"}
```

两轮均无 ERROR/FATAL;第二轮无 WARNING(xingyao 未被调用)。注:xingyao
第三方 SDK(TGW)在 update 进程 stdout 打印了含 session Token 的登录
json——按凭据零容忍,本 evidence 不复录其任何值;发布工件与 raw store
中均无该等凭据。

### 配额消耗概况(build_config.raw_snapshot_reuse + call_ledger 只读取证)

- 第一轮(唯一有真实网络的轮):tushare `daily` fetched=149 /
  reused=509(485 更早轮次 + 24 第三轮,ADR-015 复用);tushare
  `daily_basic` fetched=3(reused=0,即 basic_factor 本窗 3 个开市日各一
  快照,本窗首次);akshare `index_history` fetched=2;xingyao `daily`
  fetched=657(call_ledger 口径:sessions=3、code_queries=3,批量按码
  查询,2 码返回非 frame → 上述 WARNING)。
- 第二轮:四源全部 `not_run`,零网络、零新增快照。
- 沙箱关停重试 allowance:未触发、未使用(两轮 update 与 validate 均
  一次成功,无连接级失败)。

### §7.4 判定

**§7.4 真实小窗口条件已闭环**:真实小窗口(2026-09-25..2026-09-30)
update 成功发布(两轮,均 PASS)、validate PASS(退出码 0、零
ERROR/FATAL)、basic_factor/basic_factor_coverage 以预期形态落盘并通过
离线 validate,同窗重跑零网络且数据层逐字节等价。CURRENT 执行后为
`3d172132fb8629b3d27eeaef8e24e70b082909229553f00c74c7e0aa0d55d2b6`。

### 纪律执行

窗口未扩大;未跑 pytest;未改任何代码或门禁。credentials 仅按惯例
source,零读取、零打印、零记录。在途 WIP(RUNBOOK.md、
docs/superpowers/specs/2026-09-29-panda-data-loop-grafting-design.md、
src/stock_quant/cli.py、src/stock_quant/reporting/html.py、
src/stock_quant/reporting/templates/experiment.html.j2、
tests/integration/test_cli.py、tests/integration/test_reports.py、
tests/integration/test_factor_no_lookahead.py、src/stock_quant/bootstrap.py
及未跟踪 plans/ 等)一律未动;本次仅追加并提交本 evidence 文件。
