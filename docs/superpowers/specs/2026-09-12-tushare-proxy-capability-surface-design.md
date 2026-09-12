# Tushare 代理能力面设计

## 背景与问题

`https://pcd.mobcvb.cn/tushare/pro` 是一个 Tushare 兼容的 GET 聚合前置（自报名
`Tushare Relay v0.5.99`），已由 `src/stock_quant/data_sources/tushare_proxy.py`
接入：`TushareProxyClient` 镜像官方 SDK 的 `daily` / `index_daily` / `stock_basic`
三个方法，由 `TUSHARE_PROXY_URL` + `TUSHARE_PROXY_KEY` 环境变量启用，未配置时
回退官方 SDK。集成形态（GET + `X-API-Key`、日期窗口切片、退避重试、客户端范围
过滤、`tushare_proxy.*` provenance 标签）经实测**成立**，单测覆盖了已观测的
截断与抖动。

但实测发现三类问题（证据见末节"实测证据"）：

1. **能力被严重低估。** 代理是 FastAPI，`GET /openapi.json` 暴露完整路由；
   `GET /tushare/capabilities` 自述 **298 个接口**（259 个 `enabled`），
   而客户端只够得到 3 个。另有 `/tushare/capabilities/{api_name}`（轻量单接口
   能力查询）、`/tushare/pro/catalog`（含服务端策略）、
   `/tushare/upstreams/probe/{api_name}`（上游探测）。
2. **能力面正是当前被卡住缺口的解锁点。** 实测 `suspend_d` 返回
   `000333.SZ` 2016-05-18..05-31 —— 正是停牌回补方案里记载"外部来源全部
   受阻"的那段区间；`index_weight` 对 `000300.SH` 返回恰好 300 行且带
   `weight`，正对时点宇宙卡点；`adj_factor` / `trade_cal` / `daily_basic` /
   `dividend` 均可读。
3. **四处缺陷**（详见 §5）：`sources.yml` 里 `tushare_proxy` 段是死配置；
   `.env.example` 有两处命名/归属问题；模块 docstring 的 quirk 记录部分失准；
   客户端完全没考虑服务端 60 次/分钟/IP 的限速。

## 本方案在总盘中的位置

本方案**不解决任何数据缺口**，只把"够得着"这件事做实。它解锁的缺口各自
仍需要独立方案（见"可解锁卡点索引"），本方案不预判那些方案的取向。

## 目标

1. 把 `TushareProxyClient` 从"3 个方法的转发壳"变成**受控通用读取面**：
   298 个目录接口中任意一个都能读，且读取前必过能力校验。
2. 收口 §5 列出的四处缺陷，使配置与文档不再误导。
3. 提供只读探针，把"代理现在到底能做什么"变成可复现的一条命令。
4. 把评估结论、实测证据、可解锁卡点索引写成运维报告，并给受影响的既有
   plan/spec 加"前提已过时"注记。

## 非目标

- **不把代理升格为一等数据源。** 不进入 `_CONFIGURED_SOURCES` /
  `_REQUIRED_ROLE`，`source health` 与 `statuses` 不单独上报代理。
- **不把任何新接口写进数据管线。** `TushareSource` 的 endpoint 路由、
  数据契约、`_ACCEPTED_MISSING_CODES`、验收口径、策略配置一律不动。
- **不集成 datahubco RDS。** `.env.example` 里的 `BASIC_RDS_*` 是另一个服务
  （`datahubco.com/app-api/openapi/v1/tushare/stock-basic`，REST + `limit`
  参数 + `data.items` 载荷），本次只修命名与归属，不写消费者。
- **不做停牌/公司行为的方案取向修订。** 只加"前提已过时"注记。
- 不改 `raw_checks` 的供应商白名单（不新增标签）。
- 不实现并发（服务端 `max_concurrency: 4` 是上限，不是目标）。

## 选型

### 被排除的路线

| 路线 | 排除理由 |
| --- | --- |
| A. 客户端内部隐式预检（`query()` 内部自动查 capability，调用方无感） | 既有单测全部用 `FakeSession` 的响应队列驱动，隐式预检会给每个用例多排一个响应；且 capability 端点自身会 mid-body stall（实测 114688/134194 字节处断），每个新接口首读要多付 0.7–3.0s。 |
| C. 目录离线快照 + 启动期校验（298 条抓成仓库内带 sha256 的快照，`query()` 只查快照） | 完全确定、零往返、可审计，但快照会漂移：服务端改 `enabled` 或加接口时不自动跟随，需要一套刷新机制。可作为 B 的后续刷新产物，不必现在做。 |
| 只给当前受阻的 6 个接口加具名方法 | 一接口一策略写死，另外 292 个仍然够不着；"更大用处"的问题原样存在。 |
| 把代理升格为数据源以给超时/重试独立配置 | 会改 `_CONFIGURED_SOURCES` 与可用性关卡语义，超出本方案范围（Q3 决策：删段 + 写明继承）。 |

### 采用的路线

**B. 显式能力面 + 可注入预检策略。** 公开 `capabilities()` / `capability(name)`
（带 TTL 缓存，复用现有传输层重试），`query()` 增加 `verify_capability` 策略；
**对已具名的 3 个接口跳过预检** —— 它们已在代码里编译期确定，预检只作用于
新发现的接口。这样既有测试一行不用改，新接口又拿到完整保护。

## 设计

### 1. 传输层：能力发现

- `capabilities() -> pd.DataFrame`：读 `/tushare/capabilities`（298 行，
  列即 `name` / `category` / `provider` / `cache_ttl` / `required` /
  `required_any` / `max_limit` / `description` / `enabled` / `methods` /
  `fallback_on_empty` / `probe_supported` / `probe_example` /
  `local_data_available` / `local_latest_time` / `requires_params`）。
- `capability(name) -> Mapping`：读 `/tushare/capabilities/{name}`（实测
  0.7–3.0s，比整表轻）。
- 两者**复用现有 `_query` 的传输与退避**：capability/catalog 端点自身也会
  静默停在中途，必须与数据读取走同一套重试。
- 进程内 TTL 缓存。两层 TTL：
  - 整表 `capabilities()` 用客户端常量 `_CATALOG_TTL_SECONDS = 21600`
    （6h，取 298 个接口里占主导的 `cache_ttl` 值）；
  - 单接口 `capability(name)` 用该接口自身行里的 `cache_ttl`（实测分布
    21600 占 181 个，另有 5s/300s/900s/86400/604800/2592000 等）。
  缓存可注入，便于测试与显式刷新。

### 2. `query()` 受控化

```
query(endpoint, *, verify_capability: Literal["live", "none"] = "live",
      **params) -> pd.DataFrame
```

- **GET-only 断言**：代理同时暴露 POST/DELETE（`p_save` 组合保存、
  `p_delete` 组合删除）。非 GET 语义的调用一律 `ContractError`，不发出请求。
  这是要显式保留的安全属性。
- **预检规则**（`verify_capability="live"` 时对非具名接口生效）：
  - `enabled is False` → `ContractError`，确定性拒绝、零重试；
  - `required_any` 非空 → 至少一组备选参数齐备，否则 `ContractError`；
  - `required` 非空 → 全部齐备，否则 `ContractError`；
  - `max_limit` 非空 → 作为窗口上界参考（实测 `suspend_d`=100000；
    `daily` / `index_weight` 为 `null` → 沿用现有 5 年窗口启发式）。
- **预检拉取本身瞬时失败**（重试耗尽）→ **fail-open**，读取继续，但记
  `capability_checked=false`（见 §4）。理由：服务端
  `allow_unregistered_apis: false` 意味着它才是最终守门人，不能让一个元数据
  端点的抖动挡住数据读取；同时 fail-open 必须留下可审计痕迹。
- `verify_capability="none"` 完全跳过预检（供离线脚本与测试的确定性路径）。
- 窗口切片与客户端范围强制沿用现有语义 —— 日期参数上游行为不一致，实测
  `dividend` 带 `start_date`/`end_date` 直接 **HTTP 503**（不带区间则 64 行）。
- 具名接口 `daily` / `index_daily` / `stock_basic` 内部固定传
  `verify_capability="none"`，因此既有调用方与既有测试完全不受影响 ——
  这三个名字由代码保证，不需要运行时校验。

### 3. 限速

服务端策略来自 `/tushare/pro/catalog`：`rate_limit_per_minute: 1000`、
`rate_limit_per_ip_per_minute: 60`、`max_concurrency: 4`。当前客户端完全没
考虑这些。

- 加主动节流：两次出站请求间隔 ≥1s（单调时钟令牌桶，clock/sleeper 可注入，
  测试中不真等）。
- `429` 已在 `_TRANSIENT_HTTP_STATUS` 中参与退避，保留。
- 不实现并发。

### 4. Provenance

分两条互不干扰的路径，**都不改数据契约、不新增供应商白名单标签**：

- **管线路径（具名接口）不变。** `TushareSource.fetch()` 只接受
  `daily` / `index_daily` / `stock_basic`（`tushare.py:72`），其
  `FetchResult.metadata` 继续打 `supplier_endpoint = "tushare_proxy.{endpoint}"`、
  `sdk_version = "tushare_proxy-1.0"`。本方案不碰这条路径。
- **通用路径（`query()`）不走 `TushareSource`。** 它服务于离线脚本与探查，
  因此没有 `FetchResult`。审计痕迹落在客户端实例上的
  `self.last_query_metadata: dict`：
  `{"endpoint": str, "capability_checked": bool | None,
  "windows": list[tuple[str, str]], "rows": int}`。每次 `query()` 覆盖写入；
  `capability_checked` 是三态 —— `True` 预检通过、`False` 预检拉取失败走了
  fail-open、`None` 调用方显式跳过预检（未检查 ≠ 检查失败）。选实例属性而非
  新返回类型，是为了不动既有返回签名 —— 通用路径的调用方只需要数据帧。
  具名接口（`daily`/`index_daily`/`stock_basic`）不写这个属性；读它之前先
  确认最后一次调用是 `query()`。

`p_save` / `p_delete` 这类写接口不在通用面的可达范围内（§2 的 GET-only
断言），因此不需要写路径的 provenance 设计。

### 5. 缺陷收口

| # | 现状 | 处置 |
| --- | --- | --- |
| 1 | `sources.yml` 的 `tushare_proxy` 段无消费者；`_CONFIGURED_SOURCES` 硬编码为 `("tushare","akshare","baostock")`（`data_pipeline.py:159`），`_build_source` 不认这个名字（`data_pipeline.py:1865`）。代理实际吃 `tushare` 段的 `timeout_seconds`/`max_retries`。 | 删掉该段；在 `tushare` 段注释与 `tushare_proxy.py` docstring 写明"代理 transport 继承 `tushare` 段的 `timeout_seconds`/`max_retries`"。 |
| 2 | `.env.example` 里 `BASIC_RDS_RUL` 拼写错误（应为 `URL`），且 `BASIC_RDS_*` 全仓无消费者。 | 改正拼写；注明"由 workbuddy 使用，本仓库无消费者"。 |
| 3 | 实测 `.env` 只有 `TUSHARE_PROXY_URL` + `TUSHARE_PROXY_KEY`，没有 `TUSHARE_TOKEN` 与 `BASIC_RDS_KEY`。 | 逐项核对 `.env` 是否齐备，缺哪个补哪个（`.env` 已被 `.gitignore` 忽略）。`BASIC_RDS_KEY` 不写进 `.env.example`；本仓库不消费它，保存只为不丢失。**写入密钥前先经 owner 确认**。 |
| 4 | 模块 docstring 的 quirk 记录部分失准。 | 更正为实测结论（见"已知残留风险"与末节证据）。 |

本地 `HEAD` 版本的 `.env.example` 仍带一个 56 字符 `TUSHARE_TOKEN`；
`origin/main` 上那版只有 10 字符（非当前 token），故真实 token **未到过
GitHub**。本方案只保证本次提交的 `.env.example` 干净（现已为空值模板），
并在报告中提示推送前处理/轮换该 token。**不改写 git 历史。**

### 6. 只读探针 `project/probe_tushare_proxy.py`

- 拉 `/tushare/pro/catalog` + 逐接口 `/tushare/capabilities/{name}`，按
  `category` 汇总。
- 标出仓库关心的接口（`suspend_d` / `adj_factor` / `trade_cal` /
  `daily_basic` / `index_weight` / `dividend` / `stk_limit` /
  `index_member` / `bak_basic` / `moneyflow` 等）的 `enabled` /
  `required_any` / `max_limit`。
- 可选 `--probe NAME` 用 `__probe=1` 打冒烟。
- **只报事实、不做断言**：catalog 是声明不是保证（`get_trade_days` /
  `get_all_securities` 实测 `enabled=true` 但返回 0 行）。
- 只读、无写、输出可粘贴。

### 7. 文档

- 新增 `docs/operations/2026-09-12-tushare-proxy-assessment.md`：评估结论、
  实测证据表、能力面摘要、缺陷与修复记录、**可解锁卡点索引**。
- 既有文档顶部加"前提已过时"注记（指向评估报告，不改任务内容）：
  - `docs/superpowers/plans/2026-09-12-suspension-backfill.md`
    （"停牌证据外部来源全部受阻：`suspend_d` 无权限"已不成立）；
  - `docs/superpowers/plans/2026-09-10-index-constitution-csi300-snapshot.md`
    （2017-02-13~2019-06-14 的 299-run 卡点出现 `index_weight` 新路）；
  - `docs/operations/2026-09-11-trusted-data-chain.md`
    （`cross_source_price_sample` 的第二价格源候选）。

## 测试

新增 `tests/unit/test_tushare_proxy_capabilities.py`（TDD，先红后绿）：

| 用例 | 断言 |
| --- | --- |
| `capability()` 缓存命中 | 第二次调用不新增出站请求 |
| TTL 过期 | 注入时钟推过 TTL 后重新出站 |
| `enabled=False` | `ContractError` 且零重试（出站请求数不增） |
| `required_any` 不满足 | `ContractError` 且零重试 |
| 未注册名字 | 服务端 4xx → `ContractError` |
| 预检瞬时失败 | fail-open，读取成功，`last_query_metadata["capability_checked"] is False` |
| 具名接口不发预检 | `daily`/`index_daily`/`stock_basic` 的出站请求里没有 capability 查询（这条保证既有测试不改） |
| `verify_capability="none"` | 零能力查询，`capability_checked` 记 `None`（未检查，非失败） |
| 限速 | 注入的 sleeper 收到 ≥1s 的等待请求（测试不真等） |
| GET-only 断言 | `query("p_save", ...)` 拒绝，零出站 |

既有 `tests/unit/test_tushare_proxy.py` **不修改**：3 个具名接口跳过预检，
`test_contract_failure_raises_without_retry` 等用例的响应队列不受影响。

## 已知残留风险

写进评估报告，不在本方案解决：

- **慢与不稳是常态。** `index_weight` 实测 32.4s（2017-02 窗口），2019-06
  窗口 120s 读超时；`get_index_stocks` 45s SSL 握手超时；`/tushare/ready`
  90s 握手超时。退避能兜住瞬时失败，但按月拉十年 `index_weight` 是分钟级
  工程，调用方需自知。
- **catalog 是声明不是保证。** `get_trade_days` / `get_all_securities`
  等"本地聚合"接口 `enabled=true`、`local_data_available=null`，实测返回
  **0 行**。通用读取面无法替调用方判断"这个接口有没有数据"。
- **整表静默截断的守卫仍有缺口。** 现有 5 年窗口启发式只对日期区间切片；
  单 `trade_date` 的全市场读取（如 `suspend_d` 按日）不在切片范围内，调用方
  自行循环。客户端无法可靠检测截断，靠有界窗口规避。
- **`suspend_d` 的 `suspend_type` 语义与既有 docstring 记载相反**（实测是
  多返回一行 `20160616`，不是丢行）；`dividend` 带日期区间是 503 而非
  "过滤成空"。

## 决策记录

| 问题 | 决策 |
| --- | --- |
| 交付范围 | 评估 + 修缺陷 + 扩能力面；不做缺口闭环 |
| 能力面开多大 | 受控通用读取面（298 个接口任意读，GET-only + 能力预检） |
| `sources.yml` 死配置 | 删段，写明继承 `tushare` 段 |
| 既有文档过时前提 | 评估报告 + 既有 plan/spec 加注记，不改方案取向 |
| 预检架构 | B（显式能力面 + 可注入策略，具名接口跳过预检） |
| 安全事项 | 密钥轮换/模板清理无论选哪条都做；不改 git 历史 |

## 实测证据（2026-09-12）

全部为当日直连 `https://pcd.mobcvb.cn` 的只读 GET。

### 能力面

| 观测 | 结果 |
| --- | --- |
| `GET /openapi.json` | 200，`Tushare Relay v0.5.99`；路由 `/health`、`/ready`、`/tushare/pro/catalog`、`/tushare/capabilities`、`/tushare/capabilities/{api_name}`、`/tushare/upstreams/probe/{api_name}`、`/tushare/pro/{api_name}`（GET/POST/DELETE） |
| `/tushare/capabilities` | `count: 298`；provider 分布 tushare 224 / external 58 / aggregate 8 / clickhouse 4 / portfolio 4；`enabled` 259 |
| `/tushare/pro/catalog` | `allow_unregistered_apis: false`、`rate_limit_per_minute: 1000`、`rate_limit_per_ip_per_minute: 60`、`max_concurrency: 4` |
| `/tushare/capabilities/{api_name}` | 轻量单接口（实测 0.7–3.0s），含 `required` / `required_any` / `max_limit` / `enabled` |
| `p_save` / `p_delete` | 存在且 `enabled=true` —— 通用面须限定 GET-only |

### 缺口解锁

| 接口 | 实测 | 对应卡点 |
| --- | --- | --- |
| `suspend_d` | `000333.SZ` 2016-05-01..06-30 → 10 行 = `2016-05-18..05-31` | 停牌回补方案的"外部来源全部受阻" |
| `index_weight` | `000300.SH` 2017-01、2017-02 各恰好 **300 行**，含 `weight`；2017-02 用时 32.4s | csi300 快照的 299-run 卡点 |
| `adj_factor` | `000333.SZ` 2016-05-01..05-10 → 6 行（8.1s） | 时点总收益 / 复权 |
| `trade_cal` | `SSE` 2026-09-01..09-12 → 12 行（1.4s） | 交易日历第三方佐证 |
| `daily_basic` | `000001.SZ` 2026-09-01..09-12 → 9 行（2.6s），含 `turnover_rate` / `total_share` / `free_share` / `total_mv` / `pe_ttm` / `pb` | 可交易过滤、因子 |
| `dividend` | 不带日期区间 64 行；**带区间 → HTTP 503** | 公司行为交叉核对 |

### 反向证据

| 观测 | 结果 |
| --- | --- |
| `get_trade_days` | `enabled=true` → **0 行** |
| `get_all_securities` | `enabled=true` → **0 行** |
| `get_index_stocks` | 45s SSL 握手超时 |
| `/tushare/ready` | 90s SSL 握手超时 |
| `/tushare/health` | 404（健康检查在根路径 `/health`，非 `/tushare/health`） |
| `suspend_d` + `suspend_type=S` | 11 行（比不带参数**多** `20160616`）—— 与既有 docstring 的"丢行"记载相反 |
