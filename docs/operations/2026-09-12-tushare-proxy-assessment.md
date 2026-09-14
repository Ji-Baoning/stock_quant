# Tushare 代理评估：一个新入口，不是一条新证据链

> **信任等级：数据源（transport）。不是证据源。** 本报告把"多接一个入口"
> 能买到什么、买不到什么写成结论：能买到**可达性**与**速度**，买不到
> **可归因性**与**更稳**。

## 0. 结论摘要

- **能力面扩张是真的。** 客户端能触达的接口从 3 个变成 **298 个**（enabled
  **259**），此前够不到的 `suspend_d` / `index_weight` / `adj_factor` /
  `trade_cal` / `daily_basic` / `dividend` 均可读。买到的是**可达性**。
- **上游不可溯源是结构性的，不是配置问题。** 代理自报的六上游链里**没有
  tushare**，224 个 `provider: "tushare"` 接口**全部** `fallback_on_empty: true`，
  响应体与响应头都**不标识**本次由哪个上游作答。买到的是**一个新的入口**，
  不是**一条可归因的证据链**。
- **稳定性不解决，反而多一个不稳定面。** 代理自身抖动、共享 IP 限速、单点、
  声明不可信（限速声明已被实测证伪）四项叠加，唯一保证是"不失去原有路径"。
- **可信性结构性下降。** 从"来源单一但已知"变成"来源多样但不可知"：
  `fallback_on_empty` 让"空"不再等于"确实为空"。
- **唯一翻案条件当前不成立**，且本方案不为其预留代码路径（见 §8）。

## 1. 能力面

代理是 FastAPI，`GET /openapi.json` 暴露完整路由。实测（2026-09-12）：

- `GET /tushare/capabilities` 自述 **298 个接口**，其中 `enabled` **259** 个。
  provider 分布：`tushare` **224** / `external` **58** / `aggregate` **8** /
  `clickhouse` **4** / `portfolio` **4**。
- `GET /tushare/capabilities/{api_name}` 是**轻量单接口**能力查询（实测
  0.7–3.0s，比整表轻）。整表 `capabilities()` 反而更重：响应会 mid-body
  stall（实测 114688/134194 字节处断，见 §9）。
- `GET /tushare/upstreams/probe/{api_name}` 返回该接口**自报**的上游链
  （是诊断，不是归因——见 §2）。
- `GET /tushare/pro/{api_name}` 同时暴露 **GET / POST / DELETE**：`p_save`
  （组合保存）、`p_delete`（组合删除）均**存在且 `enabled=true`**。因此通用
  读取面**必须限定 GET-only**，`query()` 对非 GET 语义的调用一律
  `ContractError`、零出站——这是要显式保留的安全属性。
- 目录里 298 个接口，客户端此前只镜像了 `daily` / `index_daily` /
  `stock_basic` 三个。本次把它扩成"受控通用读取面"：任意目录接口可读，读取前
  必过能力预检（`enabled` / `required` / `required_any` / GET-only 的形状检查）。

**预检是形状检查，不是信任信号。** 它防拼错接口名、防打到 `enabled=false`
的接口、防参数不满足 `required_any`；它读的是**接口的形状声明**，不是**数据的
出身**（§2）。catalog 本身已被证伪过一次——它声明
`rate_limit_per_ip_per_minute: 60`，而实测响应头是 `x-ratelimit-ip-limit: 200`
（§4）。

## 2. 上游与溯源：结构性不可归因

| 观测 | 值 |
| --- | --- |
| `/tushare/upstreams/probe/{api_name}` 返回的上游 | 6 个：`tickflow` · `citydata` · `relay` · `eastmoney` · `sina-minute` · `sina`，**无 tushare** |
| 标注 `provider: "tushare"` 的接口数 | **224**，**全部** `fallback_on_empty: true` |
| 全量 `fallback_on_empty: true` | **244 / 298** |
| 响应体中标识上游的字段 | **无** |
| 响应头中标识上游的字段 | **无**（只有 `x-cache` / `x-cache-layer` / `x-request-id` / `x-service-mode`） |

故 `provider` 是**接口方言**标签（字段名与形状像 tushare），**不是数据来源
标签**；`fallback_on_empty: true` 意味着**首选上游返回空时会静默换源**。合起来：
**"这条数据是谁给的"在响应层面不可回答。**

**本轮新增的最强证据——探测列表不是作答集合。** 2026-09-12 的探针全量运行里：

- 10 个仓库关心的接口（`suspend_d` / `adj_factor` / `trade_cal` /
  `daily_basic` / `index_weight` / `dividend` / `stk_limit` / `index_member` /
  `bak_basic` / `moneyflow`）**全部**报 `enabled=true methods=GET`；
- 而同一批接口的**六个**上游**全部**返回 `FAIL rows=0`——`tickflow` /
  `eastmoney` / `sina-minute` / `sina` 报 "does not support {api}"，
  `citydata` 报"参数不能为空"（或 `invalid api for CityData upstream`），
  `relay` 报 `备用上游响应异常`。

更早记在方案头部的那一条是同一个事实的独立复现：`/tushare/upstreams/probe/suspend_d`
显示六个上游**全部**报不支持该接口，而 `/tushare/pro/suspend_d` **确实返回数据**
（本轮 `--probe suspend_d` 仍以 `__probe=1` 取回 5 行样例）。故**探测列表与
作答集合是两回事**——"六选一"的说法本身也不成立，真正的作答方可能根本不在这
份列表里（`relay` 的报错文案为"备用上游响应异常"，暗示这 6 个可能只是**备用**
链、首选源未进探测列表；两种读法都不改变结论）。

**唯一的可事后追溯手段是 `x-request-id`。** 客户端已把它与 `x-cache` /
`x-cache-layer` 记进 `last_query_metadata`（`request_ids` / `cache`），供出问题
时向代理方申诉与核对。但这**只能追溯"哪一次请求"，不能追溯"哪一条上游"**。

## 3. 三个问题，三个答案

| 问题 | 代理的作用 | 理由 |
| --- | --- | --- |
| **数据来源受限** | **真缓解，但性质是"权限借道"** | **证据**：仓库自己记载过本地取不到的接口（停牌方案的"`suspend_d` 无权限"、baostock 停机、`stock_tfp_em` 无历史覆盖），现在这些接口经代理可读。<br>**推断（未证实）**：机制是代理的出口 IP 与账号配额不同于本地。旁证是它的上游链里含 `eastmoney`，而本地的东财通路此前判定为不可用。这两种解释（权限差异 / 换了一条确实可用的上游）在结论上一致：**你获得的是"借道"，不是"新增独立源"**。 |
| **稳定性不强** | **不解决，且新增一个不稳定面**（§9） | 代理自身抖动与共享限速；唯一保证是"不失去原有路径"。 |
| **可信性** | **结构性下降** | 从"来源单一但已知"变成"来源多样但不可知"。`fallback_on_empty` 让"空"不再等于"确实为空"，让"非空"可能是 6 源中任意一个。加第二入口（RDS）也不改善：它对同一事实与 promax 逐位一致，只能说明两者同源，**不能互相担保**。 |

## 4. 限速

**以响应头为准，不以 catalog 声明为准。**

- catalog 声明 `rate_limit_per_ip_per_minute: 60`、`rate_limit_per_minute: 1000`；
  实测响应头是 `x-ratelimit-ip-limit: 200`、`x-ratelimit-limit: 4000`（每分钟），
  并逐请求回传 `x-ratelimit-ip-remaining` / `x-ratelimit-remaining`。
  **声明与实测不一致，声明已被证伪**，因此不能用它做容量规划。
- **节流依据改为响应头。** 客户端每次响应读 `x-ratelimit-ip-remaining`；低于
  阈值（`_RATE_LIMIT_LOW_WATERMARK = 10`）时按剩余量退让（`Retry-After`
  优先），而不是硬编码固定间隔。响应头缺失时回落到保守常量
  `_FALLBACK_MIN_INTERVAL_SECONDS = 1`（即 60/min，取已证伪声明 60 与实测 200
  中**更保守**的那个）。一条从未见过限速头的响应不触发节流；一旦见过，
  之后缺头的响应回落保守值而非放开。
- **这是共享 IP 预算，与调用方自己的 token 配额无关**：别的使用者会消耗同一
  额度。因此"我们请求不多"不是理由，必须按剩余量自适应。
- `429` 已在 `_TRANSIENT_HTTP_STATUS` 中参与退避，保留。**不实现并发**——
  服务端 `max_concurrency: 4` 是上限，不是目标。

## 5. 缺陷与修复记录

对照 spec §5 的四行，逐行写"现状 → 处置 → 本次提交的结论"：

| # | 现状 | 处置 | 本次提交的结论 |
| --- | --- | --- | --- |
| 1 | `sources.yml` 的 `tushare_proxy` 段无消费者；`_CONFIGURED_SOURCES` 硬编码为 `("tushare","akshare","baostock")`，`_build_source` 不认这个名字。代理实际吃 `tushare` 段的 `timeout_seconds`/`max_retries`。 | 删掉该段；在 `tushare` 段注释与 `tushare_proxy.py` docstring 写明"代理 transport 继承 `tushare` 段的 `timeout_seconds`/`max_retries`"。 | 死配置段已删除，继承关系已写明。代理**不进入** `_CONFIGURED_SOURCES` / `_REQUIRED_ROLE`（非目标约束）。 |
| 2 | `.env.example` 里 `BASIC_RDS_RUL` 拼写错误（应为 `URL`），且 `BASIC_RDS_*` 全仓无消费者。 | 改正拼写；注明"由 workbuddy 使用，本仓库无消费者"。 | **已是 `BASIC_RDS_URL`**（提交 `0e71ae3` 修正），并在模板中注明本仓库无消费者、保留 URL 只为不丢失。 |
| 3 | 实测 `.env` 只有 `TUSHARE_PROXY_URL` + `TUSHARE_PROXY_KEY`，没有 `TUSHARE_TOKEN` 与 `BASIC_RDS_KEY`。 | 逐项核对 `.env` 是否齐备，缺哪个补哪个（`.env` 已被 `.gitignore` 忽略）。`BASIC_RDS_KEY` 不写进 `.env.example`；本仓库不消费它，保存只为不丢失。**写入密钥前先经 owner 确认**。 | 本轮核对结果（只记**齐备/缺失**，不记值）：`TUSHARE_PROXY_URL` **齐备**、`TUSHARE_PROXY_KEY` **齐备**、`BASIC_RDS_KEY` **齐备**（存在）；`TUSHARE_TOKEN` **缺失**——代理路径下它本就不需要（`from_env()` 两者未配置时才回退官方 SDK）。 |
| 4 | 模块 docstring 的 quirk 记录部分失准。 | 更正为实测结论。 | docstring 已改为实测口径：截断在 ~6000 行、共享 IP 预算按响应头节流、预检只读形状不读出身的说明均已落文。 |

**关于 `.env.example` 里的 token。** 本地 `HEAD` 版本的 `.env.example` 曾带一个
56 字符 `TUSHARE_TOKEN`；`origin/main` 上那版只有 10 字符（非当前 token），故
真实 token **未到过 GitHub**。本次提交的 `.env.example` 已清理为**空值模板**。
**推送前**仍建议 owner 复核该 token 并轮换。**不改写 git 历史。**

## 6. 可解锁卡点索引

| 卡点 | 出口 | 本报告对应的既有文档 |
| --- | --- | --- |
| 停牌回补的"外部来源全部受阻" | `suspend_d` 可读 | `plans/2026-09-12-suspension-backfill.md` |
| csi300 的 299-run | `index_weight` 可读（但**不是验收依据**，见下） | `plans/2026-09-10-index-constitution-csi300-snapshot.md` |
| 时点总收益 / 复权 | `adj_factor` 可读 | — |
| 交易日历第三方佐证 | `trade_cal` 可读 | — |
| 可交易过滤 / 因子 | `daily_basic` 可读 | — |
| 公司行为交叉核对 | `dividend` 可读（**不带**日期区间） | — |

**每一条都只解锁"能读到"，不解锁"能验收"。** 尤其 `index_weight` 是月度
成分快照、不可溯源，用它顶替官方公告等于用"看起来对"换"可证明对"。

逐条实测（2026-09-12，spec「缺口解锁」）：`suspend_d` 对 `000333.SZ`
2016-05-01..06-30 → 10 行 = `2016-05-18..05-31`；`index_weight` 对 `000300.SH`
2017-01、2017-02 各恰好 **300 行**，含 `weight`，2017-02 用时 **32.4s**；
`adj_factor` 对 `000333.SZ` 2016-05-01..05-10 → 6 行（8.1s）；`trade_cal` 对
`SSE` 2026-09-01..09-12 → 12 行（1.4s）；`daily_basic` 对 `000001.SZ`
2026-09-01..09-12 → 9 行（2.6s，含 `turnover_rate` / `total_share` /
`free_share` / `total_mv` / `pe_ttm` / `pb`）；`dividend` 不带日期区间 64 行，
**带区间 → HTTP 503**。

**一处必须点名的反直觉风险。** `index_weight` 对 `000300.SH` 返回**恰好 300
行**，看上去正对 csi300 快照的 299-run 卡点。但这恰恰是最危险的地方：它是
**月度成分快照**，不是**官方公告**，且**不可溯源**。用它顶替官方证据，等于用
"看起来对"替换"可证明对"。它可以是**寻路的线索**，不能是**验收的依据**。

## 7. 同源对照：datahubco RDS

第二入口 `http://datahubco.com/app-api/openapi/v1/tushare/{endpoint}` 是另一个
服务（REST + `X-API-Key` 头 + `limit`/`offset` 分页；`limit` 上限 **5000**，
≥6000 直接 HTTP 400，`has_more` 可信）。实测：

| 观测 | 结果 |
| --- | --- |
| 响应耗时 | 0.13–0.5s；命中 Redis 时 `X-Response-Time-Ms: 1.95`。同日 promax 同一请求 30–190s |
| 与 promax 对同一事实 | **逐位一致**：`daily` `000001.SZ` 2016-07 收盘两端同为 `8.81`/`8.81`/`8.71`；`index_weight` `000300.SH` 2017-02 两端同为 300 行、首行同为 `601318.SH 4.077`、权重合计同为 100.003 |
| 与本地快照 | RDS `list_status=L` = **5562** 只，与 `project/data/raw/tushare/stock_basic` 最新快照 5562 行同数 |
| 溯源头 | **无 `x-request-id`、无限速头**；有 `X-Data-Source: redis\|upstream`、`X-Data-Fetched-At` |
| 退市名簿 | `list_status=D` → **339** 只，`delist_date` **100% 覆盖**（1999-07-12～2026-07-30，近三年 147 只），`L ∩ D = ∅`，并集 5901 只，0.47s |
| 退市股行情 | `daily` 对 `000003.SZ`/`000005.SZ`/`600001.SH`/`T600018.SH` 一律 **HTTP 400**；`adj_factor` 0 行；promax 侧同样取不到（超时） |
| `list_status=P`（暂停上市） | 0 行 |
| 字段投影 | 默认投影**不含** `delist_date`/`list_status`，须显式 `fields=` 才返回 |

RDS 在**速度**与**名簿字段**上确实补得了 promax；在**溯源**上补不了——它恰好
缺 `x-request-id`，而那正是 §4 要补的东西。

**结论：多入口同源，不构成互证。** 两个入口对同一事实逐位一致，只说明它们
最终取自同一上游（**推断，未证实**：正文与响应头都不标识上游），因此它们带来
的是**可用性**（速度、冗余），不是**可信性**。这条直接限定 §2/§3 的角色边界：
再接入第三个源，也不会把"无法溯源"变成"可以溯源"。

## 8. 观察点（唯一可能翻案的条件）

**若代理将来暴露"本次由哪个上游作答"**（新增响应头或响应字段），则
`cross_source_price_sample` 的"独立第二价格源"可在**排除 `relay` 之后**由
`citydata` / `sina` 等承担，独立性随之成立。

**当前不成立。** 响应不暴露作答上游，故该条件无法满足——这是**未来可改变的
边界**。本方案**不为其预留代码路径**，仅在报告里记为观察点。届时需要重新评估
（并注意：RDS 连 `x-request-id` 都没有，其 provenance 严格弱于 promax，
若接入应作为前置条件单独立项）。

## 9. 残留风险

- **上游不可溯源（最重）。** 见 §2。本方案只能把 `x-request-id` 记进
  provenance 以备申诉，**不能**让响应自证出身。任何需要"证明这条数据来自某
  特定源"的用途，本方案都无能为力。第二入口（RDS）**连 `x-request-id` 都没有**，
  若将来接入，其 provenance 严格弱于 promax——这一点应作为接入时的前置条件
  重新评估，不能默认它与 promax 等价。
- **catalog 的声明不可信。** 限速声明已被证伪（§4）；`fallback_on_empty`、
  `enabled`、`max_limit`、`local_data_available` 都出自同一份不可信声明，
  **只能当形状提示**。具体后果：`get_trade_days` / `get_all_securities` 等
  "本地聚合"接口 `enabled=true`、`local_data_available=null`，实测返回
  **0 行**。通用读取面无法替调用方判断"这个接口到底有没有数据"。
- **慢与不稳是常态。** `index_weight` 实测 32.4s（2017-02 窗口），2019-06
  窗口 120s 读超时；`get_index_stocks` 45s SSL 握手超时；`/tushare/ready`
  90s 握手超时；`/tushare/health` 404（健康检查在根路径 `/health`）；
  `/tushare/capabilities`、`/tushare/pro/catalog` mid-body stall
  （114688/134194 字节处断）。错误面出现过 `upstream_pool_exhausted`、
  HTTP 502/503/504。退避能兜住瞬时失败，但按月拉十年 `index_weight` 是分钟级
  工程，调用方需自知。
- **整表静默截断的守卫仍有缺口。** 现有 5 年窗口启发式只对日期区间切片；
  单 `trade_date` 的全市场读取（如 `suspend_d` 按日）不在切片范围内，调用方
  自行循环。客户端无法可靠检测截断，靠有界窗口规避。
- **`suspend_d` 的 `suspend_type` 语义与既有 docstring 记载相反**（实测是
  多返回一行 `20160616`，不是丢行）；`dividend` 带日期区间是 503 而非
  "过滤成空"。

一个正面事实：默认 TLS 校验可过（`ssl_verify=0`，HTTP/2），故客户端不加
`verify=False` 是正确的——这一点无需改动。注意 `probe_example` 与部分公开
示例使用 `verify=False`，**不要照抄**。
