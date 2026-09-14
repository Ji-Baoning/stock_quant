# Tushare 代理能力面设计

## 背景与问题

`https://pcd.mobcvb.cn/tushare/pro` 是一个 **GET 多源聚合前置**（自报名
`Tushare Relay v0.5.99`），已由 `src/stock_quant/data_sources/tushare_proxy.py`
接入：`TushareProxyClient` 镜像官方 SDK 的 `daily` / `index_daily` / `stock_basic`
三个方法，由 `TUSHARE_PROXY_URL` + `TUSHARE_PROXY_KEY` 环境变量启用，未配置时
回退官方 SDK。集成形态（GET + `X-API-Key`、日期窗口切片、退避重试、客户端范围
过滤、`tushare_proxy.*` provenance 标签）经实测**成立**，单测覆盖了已观测的
截断与抖动。

**首先要纠正"它是 Tushare 转发"这个说法。** 实测
`/tushare/upstreams/probe/{api_name}` 返回的上游链是
`tickflow` · `citydata` · `relay` · `eastmoney` · `sina-minute` · `sina`，
**没有一个名为 tushare**；而 catalog 中 224 个 `provider: "tushare"` 的接口
**全部**标着 `fallback_on_empty: true`（全量 244/298）。故 `provider` 指的是
**接口方言**（字段名与形状像 tushare），**不是数据来源**。响应体与响应头都不
暴露本次由哪个上游作答，只有 `x-cache: HIT/MISS` 与
`x-cache-layer: redis/upstream`。

（保留：`relay` 的报错文案为"备用上游响应异常"，故这 6 个可能只是**备用**链、
首选源未进探测列表。两种读法都成立这一条承重结论：`fallback_on_empty: true`
意味着**别的源可以替你作答且身份不外露**。）

在此前提下，实测发现四类问题（证据见末节"实测证据"）：

1. **能力被严重低估。** 代理是 FastAPI，`GET /openapi.json` 暴露完整路由；
   `GET /tushare/capabilities` 自述 **298 个接口**（259 个 `enabled`），
   而客户端只够得到 3 个。另有 `/tushare/capabilities/{api_name}`（轻量单接口
   能力查询）、`/tushare/pro/catalog`（含服务端策略）、
   `/tushare/upstreams/probe/{api_name}`（上游探测）。
2. **能力面确实解锁了此前够不到的接口。** 实测 `suspend_d` 返回
   `000333.SZ` 2016-05-18..05-31 —— 正是停牌回补方案里记载"外部来源全部
   受阻"的那段区间；`index_weight` 对 `000300.SH` 返回恰好 300 行且带
   `weight`，正对时点宇宙卡点；`adj_factor` / `trade_cal` / `daily_basic` /
   `dividend` 均可读。**但解锁的是"权限借道"，不是"独立数据源"** —— 见 §8。
3. **稳定性不解决，反而多一个不稳定面。** 详见 §9。
4. **四处缺陷**（详见 §5）：`sources.yml` 里 `tushare_proxy` 段是死配置；
   `.env.example` 有两处命名/归属问题；模块 docstring 的 quirk 记录部分失准；
   客户端完全没按服务端限速作答。

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
5. **把代理的角色边界写成显式约束**（§8）：它解决的是"权限受限"，不解决
   "稳定性"与"可信性"；任何把它当证据源的用法都在本方案里被明确排除。

## 非目标

- **不把代理升格为一等数据源。** 不进入 `_CONFIGURED_SOURCES` /
  `_REQUIRED_ROLE`，`source health` 与 `statuses` 不单独上报代理。
- **不把代理当证据源。** 具体地：不新增 `raw_checks` 供应商白名单标签；
  不用它充当 `corporate_action_evidence`（官方巨潮/东财 filings 不可替代）；
  不用 `index_weight` 顶替 csi300 的官方公告（月度成分快照 ≠ 官方公告，
  且不可溯源）；不用它满足 `cross_source_price_sample` 的"独立第二价格源"
  ——同一条不透明上游链不构成独立性（§8.2 给出理由与唯一例外）。
- **不把任何新接口写进数据管线。** `TushareSource` 的 endpoint 路由、
  数据契约、`_ACCEPTED_MISSING_CODES`、验收口径、策略配置一律不动。
- **不集成 datahubco RDS。** `.env.example` 里的 `BASIC_RDS_*` 是另一个服务
  （`datahubco.com/app-api/openapi/v1/tushare/stock-basic`，REST + `limit`
  参数 + `data.items` 载荷），本次只修命名与归属，不写消费者。它是目前
  唯一**真正独立**的第二价格源候选，留给后续独立方案评估。
- **不做停牌/公司行为的方案取向修订。** 只加"前提已过时"注记。
- 不实现并发（服务端 `max_concurrency: 4` 是上限，不是目标）。
- **不追求"稳定性提升"。** 本方案只保证不失去原有路径（官方 SDK 回退），
  不承诺代理比现状更稳。

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
- **预检只为"快速失败"，不构成任何可信性保证。** 它的作用是防拼错接口名、
  防打到 `enabled=false` 的接口、防参数不满足 `required_any`——在出站前把
  确定性错误挡住。**不要**把它读成"通过预检的数据是可信的"：catalog 本身
  已被证伪过一次（声明 `rate_limit_per_ip_per_minute: 60`，实测响应头为
  `x-ratelimit-ip-limit: 200`，见 §3），而 `fallback_on_empty` 这类字段更是
  在描述"另一个源可能替你作答"。预检读的是**接口的形状声明**，不是**数据的
  出身**。
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

**以响应头为准，不以 catalog 声明为准。** catalog 声明
`rate_limit_per_ip_per_minute: 60`、`rate_limit_per_minute: 1000`，而实测
响应头是 `x-ratelimit-ip-limit: 200` 与 `x-ratelimit-limit: 4000`（每分钟），
并逐请求回传 `x-ratelimit-ip-remaining` / `x-ratelimit-remaining`。
**声明与实测不一致，声明已被证伪**，因此：

- **节流依据改为响应头。** 每次响应读 `x-ratelimit-ip-remaining`；低于阈值
  时按剩余量退让（`Retry-After` 优先），而不是硬编码固定间隔。响应头缺失时
  回落到一个保守常量（`_FALLBACK_MIN_INTERVAL_SECONDS = 1`，即 60/min，
  取已证伪声明与实测 200/min 中更保守的那个）。
- 这是**共享 IP 预算**，与调用方自己的 token 配额无关：别的使用者会消耗
  同一额度。因此"我们请求不多"不是理由，必须按剩余量自适应。
- `429` 已在 `_TRANSIENT_HTTP_STATUS` 中参与退避，保留。
- 时钟与 sleeper 可注入，测试中不真等。
- 不实现并发（`max_concurrency: 4` 是上限，不是目标）。

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
  "windows": list[tuple[str, str]], "rows": int,
  "request_ids": list[str], "cache": list[str]}`。每次 `query()` 覆盖写入；
  `capability_checked` 是三态 —— `True` 预检通过、`False` 预检拉取失败走了
  fail-open、`None` 调用方显式跳过预检（未检查 ≠ 检查失败）。选实例属性而非
  新返回类型，是为了不动既有返回签名 —— 通用路径的调用方只需要数据帧。
  具名接口（`daily`/`index_daily`/`stock_basic`）不写这个属性；读它之前先
  确认最后一次调用是 `query()`。
- **`x-request-id` 与 `x-cache` 必须入 metadata。** 在"无法从响应得知哪个
  上游作答"的前提下（§8.1），`x-request-id` 是**唯一可用的事后追溯手段**
  ——出问题时凭它向代理方申诉与核对；`x-cache: HIT/MISS` 与
  `x-cache-layer: redis/upstream` 则是"这份数据是现取的还是缓存的"的
  唯一线索。这两个头目前被客户端完全丢弃，是本次要补上的最小可信性投入。

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
- **同时打印 `/tushare/upstreams/probe/{name}` 的上游链**（§8.1）——运维应当
  一眼看到"这条数据可能由哪几个源作答"，以及哪些源当前报错。
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

### 8. 角色边界：它能解决什么、不能解决什么

这一节是本方案对"新数据源能解决哪些问题"的正式回答，也是报告的主干。

#### 8.1 结构事实：上游不可溯源

| 观测 | 值 |
| --- | --- |
| `/tushare/upstreams/probe/{api_name}` 返回的上游 | 6 个：`tickflow` · `citydata` · `relay` · `eastmoney` · `sina-minute` · `sina`，**无 tushare** |
| 标注 `provider: "tushare"` 的接口数 | 224，**全部** `fallback_on_empty: true` |
| 全量 `fallback_on_empty: true` | 244 / 298 |
| 响应体中标识上游的字段 | 无 |
| 响应头中标识上游的字段 | 无（只有 `x-cache` / `x-cache-layer` / `x-request-id` / `x-service-mode`） |
| 第二入口（RDS）响应体中标识上游的字段 | 无 |
| 第二入口（RDS）响应头中标识上游的字段 | 无（`X-Data-Source` 只在 `redis` / `upstream` 之间区分**缓存层**，不区分上游；且无 `x-request-id`） |

故 `provider` 是**接口方言**标签，不是来源标签；`fallback_on_empty: true`
意味着**首选上游返回空时会静默换源**。合起来：**"这条数据是谁给的"在响应
层面不可回答。**

**换入口不改变这一点。** 实测第二入口与 promax 对同一事实逐位一致
（「同源对照」），而两者都不标识上游 —— 因此"多接一个入口"买到的是冗余与
速度，不是可归因性。下面的 8.2–8.4 对两个入口同等成立（仅 8.3 第 1 项里依赖
`x-request-id` 的那半句除外，RDS 没有该头）。

#### 8.2 三个问题，三个答案

| 问题 | 代理的作用 | 理由 |
| --- | --- | --- |
| **数据来源受限** | **真缓解，但性质是"权限借道"** | **证据**：仓库自己记载过本地取不到的接口（停牌方案的"`suspend_d` 无权限"、baostock 停机、`stock_tfp_em` 无历史覆盖），现在这些接口经代理可读。<br>**推断（未证实）**：机制是代理的出口 IP 与账号配额不同于本地。旁证是它的上游链里含 `eastmoney`，而本地的东财通路此前判定为不可用。这两种解释（权限差异 / 换了一条确实可用的上游）在结论上一致：**你获得的是"借道"，不是"新增独立源"**。 |
| **稳定性不强** | **不解决，且新增一个不稳定面**（§9） | 代理自身抖动与共享限速；唯一保证是"不失去原有路径"。 |
| **可信性** | **结构性下降** | 从"来源单一但已知"变成"来源多样但不可知"。`fallback_on_empty` 让"空"不再等于"确实为空"，让"非空"可能是 6 源中任意一个。加第二入口（RDS）也不改善：它对同一事实与 promax 逐位一致，只能说明两者同源，**不能互相担保**。 |

**唯一的翻案条件（当前不成立）。** 若代理将来暴露"本次由哪个上游作答"
（新增响应头或响应字段），则 `cross_source_price_sample` 的"独立第二价格源"
可在**排除 `relay` 之后**由 `citydata` / `sina` 等承担，独立性随之成立。当前
响应不暴露，故该条件不成立——这是**未来可改变的边界**，本方案不为其预留
代码路径，仅在报告里记为观察点。

#### 8.3 它能承担的角色（且仅此三项）

1. **权限借道的 transport** —— 取回 tushare 形状的数据，进 raw 快照、可复现、
   带 `x-request-id` 可追溯。**不因通过预检而获得额外信任。**
2. **同源自洽交叉校验** —— 具体可做的三件事：
   - `suspend_d` 独立检验那 714 条 `pre_close` 链合成的停牌行；
   - `trade_cal` 校验 2833 行交易日历；
   - `daily_basic.close` 与 `daily.close` 互查。
   价值真实但**有上界**：能抓接口级/合成级错误，**抓不到整条上游链的系统性
   错误**（同一个错误源可以同时污染两侧）。
3. **绝不作证据源** —— 非目标清单已给出四条具体禁止。

#### 8.4 一处必须点名的反直觉风险

`index_weight` 对 `000300.SH` 返回**恰好 300 行**，看上去正对 csi300 快照的
299-run 卡点。但这恰恰是最危险的地方：它是**月度成分快照**，不是**官方
公告**，且**不可溯源**。用它顶替官方证据，等于用"看起来对"替换"可证明对"。
它可以是**寻路的线索**，不能是**验收的依据**。

### 9. 稳定性：为什么这条不解决

代理自身就是一个新的不稳定面，实测（同日）：

| 观测 | 结果 |
| --- | --- |
| `index_weight` 2017-02 窗口 | 32.4s |
| `index_weight` 2019-06 窗口 | 120s 读超时 |
| `get_index_stocks` | 45s SSL 握手超时 |
| `/tushare/ready` | 90s 握手超时 |
| `/tushare/health` | 404（健康检查在根路径 `/health`） |
| `/tushare/capabilities`、`/tushare/pro/catalog` | mid-body stall（114688/134194 字节处断） |
| 错误面 | `upstream_pool_exhausted`、HTTP 502/503/504 均出现过 |

另有三条结构性弱点：

- **共享 IP 限速**：额度是 IP 级的，其他使用者会消耗同一预算（§3）。
- **单点**：单主机 + Caddy 反代，无第二入口。
- **声明不可信**：catalog 的限速声明已被证伪（§3），因此不能用它做容量规划。

一个正面事实：默认 TLS 校验可过（`ssl_verify=0`），故客户端不加
`verify=False` 是正确的 —— 这一点无需改动。注意 `probe_example` 与部分公开
示例使用 `verify=False`，**不要照抄**。

还有一条更根本的：**多入口同源不构成互证**（见「实测证据 · 同源对照」）。
已接入的第二个入口（datahubco RDS）与 promax 对同一事实逐位一致，这只说明
两者最终取自同一上游池，**不说明数据正确**。因此增加入口能买到**可用性**
（更快、有冗余），买不到**可信性** —— 后者只能靠官方披露（cninfo / eastmoney）
的独立证据链，不在这条路线能 reach 的范围内。

结论：本方案**只保证不失去原有路径**（`TUSHARE_PROXY_URL`/`KEY` 未配置时
回退官方 SDK，构造期决定），**不承诺比现状更稳，也不承诺更可信**。

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
| 限速（响应头路径） | `x-ratelimit-ip-remaining` 低时注入的 sleeper 被要求等待（测试不真等） |
| 限速（回落路径） | 响应头缺失时用保守常量 1s，不因缺头而放开 |
| 限速（`Retry-After`） | 存在时优先于剩余量推算 |
| provenance 头 | `last_query_metadata["request_ids"]` 收下每次出站的 `x-request-id`；`["cache"]` 收下 `x-cache`/`x-cache-layer` |
| GET-only 断言 | `query("p_save", ...)` 拒绝，零出站 |

既有 `tests/unit/test_tushare_proxy.py` **不修改**：3 个具名接口跳过预检，
`test_contract_failure_raises_without_retry` 等用例的响应队列不受影响。

## 已知残留风险

写进评估报告，不在本方案解决：

- **上游不可溯源（最重）。** 见 §8.1。本方案只能把 `x-request-id` 记进
  provenance 以备申诉，**不能**让响应自证出身。任何需要"证明这条数据来自某
  特定源"的用途，本方案都无能为力。第二入口（RDS）**连 `x-request-id` 都没有**，
  若将来接入，其 provenance 严格弱于 promax —— 这一点应作为接入时的前置条件
  重新评估，不能默认它与 promax 等价。
- **catalog 的声明不可信。** 限速声明已被证伪（§3）；`fallback_on_empty`、
  `enabled`、`max_limit`、`local_data_available` 都出自同一份不可信声明，
  **只能当形状提示**。具体后果：`get_trade_days` / `get_all_securities` 等
  "本地聚合"接口 `enabled=true`、`local_data_available=null`，实测返回
  **0 行**。通用读取面无法替调用方判断"这个接口到底有没有数据"。
- **慢与不稳是常态。** `index_weight` 实测 32.4s（2017-02 窗口），2019-06
  窗口 120s 读超时；`get_index_stocks` 45s SSL 握手超时；`/tushare/ready`
  90s 握手超时。退避能兜住瞬时失败，但按月拉十年 `index_weight` 是分钟级
  工程，调用方需自知。
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
| 代理的角色定位 | 权限借道 transport + 同源自洽交叉校验 + 永不作证据源（§8.3）；不承诺稳定性提升 |
| datahubco RDS（第二入口） | **不纳入本方案目标**。它买的是速度与名簿字段，不是能力；且缺 `x-request-id`，补不了 §4 的溯源。只把实测写进「同源对照」作为选型前提；接入等具体触发点再单独立项 |
| 退市名簿（339 只 + `delist_date`） | **不并入本方案**，留给股票池（csi300 PIT）那条线。名簿可用但退市股行情取不到（HTTP 400），2026-09-11 的 owner 决定（退市边界用合成夹具，不引入真实退市股）依然成立；名簿此刻的用途是**量化并披露**幸存者偏差，不是修复 |
| 限速依据 | 以响应头为准，不用 catalog 声明（声明已被证伪） |
| 预检的性质 | 只为快速失败，不构成可信性保证 |

## 实测证据（2026-09-12）

除「同源对照」一节直连 `http://datahubco.com` 外，其余全部为当日直连
`https://pcd.mobcvb.cn` 的只读 GET。两处均为只读，未触发任何写接口。

### 能力面

| 观测 | 结果 |
| --- | --- |
| `GET /openapi.json` | 200，`Tushare Relay v0.5.99`；路由 `/health`、`/ready`、`/tushare/pro/catalog`、`/tushare/capabilities`、`/tushare/capabilities/{api_name}`、`/tushare/upstreams/probe/{api_name}`、`/tushare/pro/{api_name}`（GET/POST/DELETE） |
| `/tushare/capabilities` | `count: 298`；provider 分布 tushare 224 / external 58 / aggregate 8 / clickhouse 4 / portfolio 4；`enabled` 259 |
| `/tushare/pro/catalog`（**声明，非保证**） | `allow_unregistered_apis: false`、`rate_limit_per_minute: 1000`、`rate_limit_per_ip_per_minute: 60`、`max_concurrency: 4` |
| `/tushare/capabilities/{api_name}` | 轻量单接口（实测 0.7–3.0s），含 `required` / `required_any` / `max_limit` / `enabled` |
| `p_save` / `p_delete` | 存在且 `enabled=true` —— 通用面须限定 GET-only |

### 上游、限速与溯源

| 观测 | 结果 |
| --- | --- |
| `/tushare/upstreams/probe/daily` | 6 个上游：`tickflow` · `citydata` · `relay` · `eastmoney` · `sina-minute` · `sina`，**无 tushare** |
| 同上，`suspend_d` / `index_weight` / `dividend` | 同一条链；`relay` 报 `fallback_error` / `fallback_timeout`（文案为"备用上游响应异常"） |
| `fallback_on_empty` | `true` 244 / `false` 54；**224 个 `provider: "tushare"` 全部为 `true`** |
| 响应体/响应头标识上游 | **无**。头里只有 `x-cache`（HIT/MISS）、`x-cache-layer`（redis/upstream）、`x-request-id`、`x-service-mode`、`x-ratelimit-*`、`via: 1.1 Caddy` |
| 同请求重复两次 | 两次均 `x-cache: HIT` / `x-cache-layer: redis`（第二次仍是 HIT，命中 Redis 层） |
| 限速（**实测头**，与声明矛盾） | `x-ratelimit-ip-limit: 200`、`x-ratelimit-limit: 4000`、`x-ratelimit-ip-remaining` / `x-ratelimit-remaining` 逐请求递减 |
| TLS | 默认校验通过（`ssl_verify=0`，HTTP/2）—— 客户端不加 `verify=False` 是对的 |

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

### 同源对照：datahubco RDS（第二入口）

`http://datahubco.com/app-api/openapi/v1/tushare/{endpoint}`，kebab-case 路径、
`X-API-Key` 头、`limit`/`offset` 分页（`limit` 上限 **5000**，≥6000 直接 HTTP 400，
`has_more` 可信）。

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

RDS 在**速度**与**名簿字段**上确实补得了 promax；在**溯源**上补不了 ——
它恰好缺 `x-request-id`，而那正是 §4 要补的东西。

**结论：多入口同源，不构成互证。** 两个入口对同一事实逐位一致，只说明它们
最终取自同一上游（**推断，未证实**：正文与响应头都不标识上游），因此它们带来
的是**可用性**（速度、冗余），不是**可信性**。这条直接限定 §8 的角色边界：
再接入第三个源，也不会把"无法溯源"变成"可以溯源"。
