# 数据源角色分工设计（jiaoch 主传输 + 双轴证伪）

## 背景与问题

### 现状角色模型

`DataPipeline` 只有三个源位（`data_pipeline.py:9-20`、`configs/sources.yml`）：

| 源位 | 角色 | 供给 | 失败后果 |
| --- | --- | --- | --- |
| `tushare` | 必需主源 | 个股日线（未复权）+ `stock_basic` | 阻塞发布 |
| `akshare` | 必需参考源 | 基准指数 `index_history`（000300/000905）+ 公司行为（cninfo/eastmoney，best-effort） | 指数失败阻塞；公司行为仅 WARNING |
| `baostock` | 可选校验源 | 个股日线对照 | 仅 WARNING |

### 三个实测问题

**问题 1：覆盖率不足，"很多数据取不到"。** 官方免费 token 坐不住主源位：

| 接口 | 官方 token 实测 | 后果 |
| --- | --- | --- |
| `index_weight` | 无权限 | universe_membership 的活水断流 |
| `trade_cal` | 1 次/小时（40203） | 日历无法批量校验 |
| `index_daily` | 1 次/小时 | 基准指数无法按标批量取 |
| bulk 个股日线 | 按秒级配额 | 全池抓取不现实 |

叠加：baostock 停机、EastMoney IP 级封禁、公司行为只有 akshare 爬虫一条 best-effort 通路。

**问题 2：证伪能力实际是空的。** 证伪有两条轴，互相不可替代：

- **传输保真轴**：官方直连 ⟷ 中转，同接口逐位比对 → 只证明"中转没篡改数据"。
  这正是当初抓出 promax 按窗口丢行的那把刀。
- **上游真值轴**：tushare 家族 ⟷ 真正独立的厂商 → 才能证伪"tushare 本身错了"。

第二条轴唯一的现实通路是 `tushare daily ⟷ baostock daily`，而 **baostock 已停机**；
`AkShareSource` 只接了 `index_history` 与公司行为，**接不了个股日线**（`akshare.py:43-71`）。
所以现有数据集事实上只有一条证伪轴，且那一条只是"中转没篡改"。

**问题 3：出处标注可能是假的。** `_supplier_endpoint()`（`tushare.py:62-65`）
按客户端**类型**硬编码标签：`TushareProxyClient` → `tushare_proxy.*`，否则 →
`tushare.pro.*`。它**区分不了"官方直连"与"SDK 的 `_DataApi__http_url` 被改写成中转"**
（后者就是一个普通 `pro_api` 对象，`isinstance` 判定与官方完全相同）。

实测盘点 `project/data/raw/` 的 389 份快照：

| 家族 | 总数 | SDK 版本分布 | 时间跨度 |
| --- | --- | --- | --- |
| tushare | 126 | `1.4.24` × 63（= 本机版本）· `1.4.29` × 63 | 1.4.24: 09-05..09-09；1.4.29: 09-10..09-12 |
| akshare | 263 | `1.18.23` × 127（= 本机版本）· `1.18.88` × 136 | — |

本机扫过的四个 conda 环境里，只有 `py310` 装了这两个包，
版本组合正是 `1.4.24 + 1.18.23`；`1.4.29` / `1.18.88` 不在任何本机环境，
也不在 uv 缓存里。**即有一批数据的产出环境不在这台机器上**，
而它们全部标着 `tushare.pro.*`。

**但要把话说准**：126 份 tushare 快照**没有任何一份**标 `tushare_proxy.*`，
这与"管线从不加载 `.env`、因此一直走官方 SDK"完全自洽（见 §1）。
所以这一条的结论是 **"无法排除被污染"，不是"已证实被污染"** ——
`tushare.pro.*` 这个标签**既证明不了是官方、也证明不了不是官方**。
问题不在"已知有假数据"，而在**这个标签本身不具备判别力**，而我们将要
依赖它来支撑"以后换源"这件事。

## 与既有方案的关系

### 与 `2026-09-12-tushare-proxy-capability-surface-design` 的边界

那份方案（下称"promax 方案"）的 §8 结论是：`pcd.mobcvb.cn`（下称 **promax**）
的角色仅限「权限借道 transport + 同源自洽交叉校验 + **绝不作证据源**」，
理由是它的上游链 `tickflow · citydata · relay · eastmoney · sina-minute · sina`
**不含 tushare**，且其 224 个 `provider: "tushare"` 接口**全部** `fallback_on_empty: true`
——数据由谁作答在响应层面不可回答。

**本方案的主传输是 jiaoch，不是 promax，两者的信任边界不同，不能互相引用结论。**
差别的实测依据：

| 观测 | promax | jiaoch |
| --- | --- | --- |
| 协议 | 自建 GET `/pro/{endpoint}` + `X-API-Key` | 官方 tushare SDK 协议（`api_name`/`token`/`params`/`fields`） |
| 对官方直连逐位比对 | 未做（上游链不含 tushare） | **4/4 逐位一致**：`daily`/`index_daily`/`adj_factor`/`daily_basic`，列集、行数、值全同 |
| 未知接口名错误串 | — | 与官方**逐字相同**（`请指定正确的接口名`，tushare 服务端专有文案） |
| 权限层级 | 高（借道） | 高（`index_weight` 可读，而本仓库 token 无权限） |
| 窗口语义缺陷 | 有（按窗口静默丢行） | 未观测到（`trade_cal` 稳定、`pretrade_date` 正常、破折号日期可用） |

**但要诚实标注证据的上界**：以上证明的是「jiaoch 的输出与官方直连不可区分」，
**不是**「jiaoch 的上游必然是 tushare 官方」。反例路径存在：一个聚合层若把请求
转发给另一个聚合层，而后者恰好从 tushare 取数，也会得到逐位一致的载荷与
逐字相同的错误文案。因此本方案：

- 把 jiaoch 记为 **`tushare_relay.<host>`，永不记成 `tushare.pro`**（第 2 节），
  这样"它到底是不是官方"永远不需要在证据里下结论；
- 新增**静默换源探针**（第 3 节 ③），专门检测 jiaoch 是否像 promax 一样在
  空结果时换源作答——这是目前唯一未验证、且最像 promax 的失效通道。

### 与 `2026-09-11-trusted-data-chain-design` 的交互

那份方案的验收要求 `build_config.origin == "data_update"` 且 `raw_snapshots[]` 非空。
本方案会改变 `supplier_endpoint` → `dataset_build_config` 哈希变 → **数据集版本 bump**，
即使字节完全相同。这是正确行为（provenance 不同即不同版本），但意味着：

- 验收记录（`data/acceptance/`）**按数据集版本绑定**，旧版本的记录不会自动适用于新版本；
- 两份方案若并行推进，需明确**谁先发布**。本方案的第 1、2 节应先落地，
  否则 `data update` 会用旧标注产生一个"看起来是官方直连"的数据集。

## 目标

1. **覆盖率**：把 jiaoch 接为 tushare 位主传输，使 `index_weight`、`trade_cal`、
   `index_daily` 等受额度/权限限制的接口可用。
2. **正确性**：`supplier_endpoint` 记录真实 provider + host，堵死"URL 改写冒充官方"。
3. **可证伪性**：两条轴都接回来——轴 1 做成可重复的抽样比对，轴 2 恢复跨厂商对照。
4. **稳定性**：解开基准指数的单点（它现在唯一供给是 akshare）。
5. **可替换性**：传输选择是**可读的一行配置**，换源不改调用代码。

## 非目标

- **不做运行时多源仲裁/投票。** 生产仍是"一个接口一个主供给"，证伪在离线/发布前做。
  与设计规格 §14 一致：对基准验证，不对单一供应商信任。
- **不实现全接口 source policy 表**（逐接口声明传输）。这是方案三的内容，
  本轮只做"全局传输选择 + 诚实标注"。
- **不集成公司行为第三源**（tushare `dividend`）。涉及对账重设计，单独立项。
- **不改 `_ACCEPTED_MISSING_CODES`、验收口径、数据契约。**
- **不删 promax 的代码路径。** 它的问题只在窗口语义，代码本身可用（见选型）。
- **不改 `_CONFIGURED_SOURCES`，也不改 `_REQUIRED_ROLE` 的取值。** jiaoch 是
  `tushare` 源的 transport，不是新源位；akshare 的 `required` 语义是否仍准确，
  留给验收口径那条线一并处理（见 §4）。

## 选型

### 被排除的路线

| 路线 | 排除理由 |
| --- | --- |
| A. 官方直连继续做主传输 | 覆盖率为零解：`index_weight` 无权限、`trade_cal` 1 次/小时、bulk 不可行。且 owner 已确认 published 允许中转供给。 |
| B. promax 升为主传输 | 已证按窗口静默丢行（`trade_cal` 首行 `pretrade_date` 恒空、`index_weight` 窄窗返回 0 行）。静默丢行是最危险的失效类别，且它的上游链不含 tushare。 |
| C. 只做离线脚本，不动管线 | 覆盖率目标达不成；"很多数据取不到"原样存在。 |
| D. 隐式 env 优先级（设了 relay 就自动走） | 换源变成"哪个 env 恰好被设了"，不可读、不可审计，违背可替换性目标。 |
| E. 基准指数保留 akshare 主供 + tushare 兜底 | 主供给仍是会封 IP 的那个源。且兜底触发时数值一样会变，只是变得**不可控**——不如显式换主供、把变化摆在台面上。 |

### 采用的路线

**jiaoch 作为 `tushare` 源的主 transport（显式可配置）+ 基准指数换 tushare 主供
+ 双轴证伪常态化 + promax 退役出主链路。**

## 设计

### 1. 传输层

`TushareSource.__init__` 现在是一条隐式链（`tushare.py:38-60`）：
`TushareProxyClient.from_env()` → 官方 SDK。改为显式解析：

```
client 参数（测试注入，最高优先）
  → TUSHARE_TRANSPORT 显式指定（"relay" | "proxy" | "official"）
  → 未指定时的自动序：relay → proxy → official
```

- **relay 分支**：用 `TushareRelayClient`（`data_sources/tushare_relay.py`）构造
  **一个 `_DataApi__http_url` 被改写过的官方 `pro_api`**。
  关键性质：它与 official 产出**同一个客户端类型**，因此
  `fetch()` 里的 `self._client.daily(...)` / `index_daily(...)` / `stock_basic(...)`
  **一行都不用改**，shape 与契约不变，差异只进证据。
- **显式配置优先于环境变量存在性**（采纳 D 的反面）。`TUSHARE_TRANSPORT` 未设时
  才走自动序，且解析结果**必须在启动日志里打印一次**。
- **传输选择读的是进程环境（`os.environ`），不是 `.env`。** 管线侧
  （`src/stock_quant/cli.py`）只读 `os.environ`，从不加载 `.env`；
  自带 `_load_env` 的全是离线脚本（`project/` 下的
  `crosscheck_calendar_relay.py`、`collect_index_weight_membership.py`、
  `check_data_sources.py`、`verify_update_readiness.py` 等）。
  实测印证：`.env` 里 `TUSHARE_PROXY_*` 一直有值，但 126 份 tushare 快照
  **全部标 `tushare.pro.*`** —— 说明管线实际走的是官方 SDK，
  `.env` 的 proxy 配置从未生效。
  **因此"换到 relay"不是改一行代码就自动发生的**：它要求运行环境真正导出
  `TUSHARE_RELAY_*`（或显式 `TUSHARE_TRANSPORT=relay`）。
  这一点不写清楚，"jiaoch 上位"会是个假动作。
- **这条也解释了离线脚本与管线可能走不同传输**：前者加载 `.env`、后者不加载。
  同一批数据可能一半来自 relay、一半来自 official，而标注此前区分不了。
  修 §2 的标注正是为了让这种分叉**可见**。
- **显式指定而凭据缺失 → `AuthenticationError` 快速失败，不静默回退。**
  `TUSHARE_TRANSPORT=relay` 但 `TUSHARE_RELAY_URL`/`KEY` 未设，必须报错而不是
  悄悄降级去官方——静默降级会让标注与事实再次脱节。
- **promax 保留分支但默认不参与自动序**：要它必须显式写
  `TUSHARE_TRANSPORT=proxy`。它仍是唯一已知可用的第三方传输，
  问题只在窗口语义，删掉分支是净损失。
- 顺带修正：`.env` 里 relay 两项写成了 `TUSHARE_RELAY_URL = <值>`（`=` 两侧有空格），
  本仓库的 `_load_env` 能容忍，但 shell `source` 会失败。统一成无空格写法。

### 2. 证据标注（正确性的地基）

`_supplier_endpoint()` 改为**由 transport 对象报告**，而不是由客户端类型推断：

| transport | `supplier_endpoint` | 说明 |
| --- | --- | --- |
| relay | `tushare_relay.<host>.<endpoint>`，如 `tushare_relay.jiaoch.top.daily` | host 取真实值，取不到则填 `unknown` |
| proxy | `tushare_proxy.<endpoint>` | 不变 |
| official | `tushare.pro.<endpoint>` | **仅当确实打到 `api.waditu.com` 时才允许打此标** |

判定机制（替代"按类型 `isinstance`"）：transport 解析时就**记下实际会打到的
base URL**（官方 SDK 为 `_DataApi__http_url`，默认
`http://api.waditu.com/dataapi`；relay 为 `TUSHARE_RELAY_URL`），
标签由这个 URL 派生。因此 URL 被改写过就**不可能**再打出 `tushare.pro.*`。

同时 `sdk_version` 保持"SDK 版本"语义不变（不掺 host），host 只进
`supplier_endpoint`，避免一个字段担两种含义。

**代价（必须认）**：`dataset_build_config` 哈希变 → 数据集版本 bump，
即使字节完全相同。这意味着本轮要重发布一次数据集。

### 3. 证伪体系

**① 轴 1 · 传输保真 · 新脚本 `project/verify_transport_fidelity.py`**

对 `daily` / `index_daily` / `adj_factor` / `daily_basic` 抽样，官方直连 ⟷ relay
逐位比对（列集、行数、值全等），差异落盘。这是把
`project/crosscheck_calendar_relay.py` 的做法从"只对日历"扩到四接口。

- 纳入**发布前检查**：不阻塞发布，但差异必须显式记录（有差异 → 进运维报告并告警）。
- **抽样而非全量**：官方 token 限速严，全量不可行；抽样覆盖"每种接口 + 每个
  数据形态（正常行 / 空窗口 / 边界日期）"。

**② 轴 2 · 上游真值 · 给 `AkShareSource` 加个股日线 handler**

新增 `stock_daily` endpoint（内部走 akshare 自己的 fallback 链），
把 `tushare daily ⟷ akshare daily` 这条唯一的跨厂商对照接回来。

- **只做对照，绝不供 published 数据**：akshare 上游是 EastMoney（有 IP 级封禁史）。
  对照失败只产生 WARNING。
- 精度差异需显式处理：指数类 akshare 是 3dp、tushare 是 4dp，
  对照必须用相对误差阈值而不是相等（现有 `FieldDifference` 已是相对误差口径）。

**③ 静默换源探针（新增，本轮最重要的一条）**

promax 的教训是 `fallback_on_empty` 让"空"不再等于"确实为空"。jiaoch 上
**这条尚未验证**。设计一个探针：

- 取官方**合法返回空**的请求（如对已退市/不存在代码的 `daily`、超出区间的窗口），
  对比 jiaoch 是否也返回空，还是返回了非空（= 换源作答）。
- 取官方**明确报错**的请求，对比错误串（已知未知接口名逐字相同）。
- 结果写进运维报告。**若 jiaoch 出现换源作答，信任边界必须重估**——
  这条探针是"jiaoch 可信"这个决定的可撤销依据。

**判据（写进设计，避免误读）**：**同族一致是弱证据，跨族一致才是强证据。**
jiaoch ⟷ 官方只证明中转没篡改；tushare ⟷ akshare 才证明上游本身没错。

### 4. 覆盖缺口与基准单点

**基准指数（000300.SH / 000905.SH，`configs/project.yml`）**

现在唯一供给是 `akshare.index_history`，还占着**必需位**——EastMoney 一封就阻塞发布。
改为 **tushare `index_daily`(relay) 主供 + akshare 降为对照源**。

- 需要补一个 **tushare 形状的规范化器**：现有
  `_canonicalise_akshare_index`（`data_pipeline.py:1900`）只认 akshare 形状。
- **副作用（必须认）**：指数数值从 akshare 的 3dp 换成 tushare 的 4dp →
  **历史实验结论要重跑**。
- **akshare 的状态行要跟着搬**：现在 `statuses["akshare"]` 是在
  `_fetch_benchmarks` 里设置的（`data_pipeline.py:1244-1285`）。基准改由 tushare
  供给后，akshare 的状态必须改由公司行为路径设置，且语义是 best-effort
  （恒 `ok`，失败只记 WARNING）——否则会留下一个永远不更新、却参与
  `source_role_health` 判定的状态位。
- `_REQUIRED_ROLE["akshare"]` **本轮保持 `True`**。此时它事实上恒 `ok`，
  因此 `_require_available` 不会因它阻塞；这是刻意的：改这个取值会动
  验收口径，属于 `2026-09-11-trusted-data-chain-design` 的范围，不在本轮。

**`index_weight`**：把 `project/collect_index_weight_membership.py` 接到 relay。
它现在**两种配置都跑不通，但失败原因不同**（已核实）：

| 配置 | 失败点 |
| --- | --- |
| 走官方 SDK | `DataApi.__getattr__` 返回 `partial(self.query, name)`，任意接口名都能发出请求，因此不是 AttributeError——而是服务端**权限错误**（本仓库 token 无 `index_weight` 权限） |
| 走 promax（`TUSHARE_PROXY_*` 已导出时） | `TushareProxyClient` 是普通类、**没有 `__getattr__`**，只有 `daily`/`index_daily`/`stock_basic` 三个具名方法 → 访问 `index_weight` 抛 **AttributeError** |

relay 路径两者都不是：它是 URL 改写过的官方 `pro_api`，`__getattr__` 在，
且挂的是有权限的账号。

### 5. 出处审计（一次性）

`project/audit_raw_provenance.py`：对现有 389 份快照，产出
`supplier_endpoint` + `sdk_version` + 时间戳的盘点报告。

**明确这次审计能得出什么、不能得出什么**：

- **不能**判定每份快照的真实 provider —— 历史快照没有留下判别依据，
  本方案不假装能追溯。
- **能**判定的是**产出环境的分布**：哪些 SDK 版本组合出现在本机、哪些不出现。
  这直接决定"这些数据集能否在本机复现"。
- **能**给出的是后续动作的依据：`1.4.29` / `1.18.88` 那 199 份标 `unknown`，
  不猜、不重标；是否需要用新标注重建，留给报告结论而不是脚本自动决定。

**这不是"给出正确答案"，是"把不确定性写下来"。**

## 落地顺序

本方案含四个可独立验收的交付物，**顺序不可颠倒**：

| 阶段 | 交付物 | 为什么是这个位置 |
| --- | --- | --- |
| 1 | §1 传输层 + §2 证据标注 | 必须最先：否则 `data update` 会用旧标注产出一个"看起来是官方直连"的数据集，后续全部白做 |
| 2 | §5 出处审计 | 紧接着做：它给阶段 1 的新标注提供基线对照 |
| 3 | §3 证伪体系 | 主传输换好之后才有意义；探针结论可能推翻阶段 1 的信任前提 |
| 4 | §4 覆盖缺口与基准 | 影响历史实验结论（重跑），放在最后、单独一轮，避免与前面混在一起 |

阶段 1+2 是一个自然的提交边界，3、4 各自独立成轮。若阶段 3 的静默换源探针
给出"jiaoch 会换源作答"的结论，**阶段 4 停止**，回到信任边界重新评估。

## 测试

| 层 | 用例 |
| --- | --- |
| relay 传输（单元，不联网） | 沿用 `tests/unit/test_tushare_relay.py` 的 `FakeSdk`；新增 `TushareSource` 在 `TUSHARE_TRANSPORT=relay` 下选中 relay 客户端、且 `fetch()` 行为与 official 分支一致 |
| 传输解析顺序 | 显式指定优先于自动序；自动序 relay → proxy → official；promax 在自动序中被跳过 |
| 证据标注 | relay 下 `supplier_endpoint` 含真实 host；official 下仍为 `tushare.pro.*`；proxy 下仍为 `tushare_proxy.*` |
| 基准换源 | tushare `index_daily` 形状的规范化器单测（含 4dp 与空窗口） |
| 空结果守卫 | 采集器层：空表必须带列名，否则拒收（relay 空表无列名已实测） |
| 外部契约 | 一条 live 契约测试覆盖 relay（可跳过） |

**注意**：relay 空表无列名这一条，**今天不咬管线**——`validate_supplier_frame`
对空表一律 `raise ContractError`（`base.py:155-156`），两种传输走同一分支。
它只对新采集器（`index_weight`）是隐患，所以在采集器层加守卫，不在 `base.py` 改语义。

## 已知残留风险

1. **jiaoch 的上游身份未证实（最重）。** 逐位一致只能证明"输出与官方不可区分"。
   第 3 节 ③ 的探针是唯一检测手段，且它只能证伪、不能证实。
2. **jiaoch 是外部单点。** 单主机、第三方运营、有账号封停风险。
   缓解：官方 token 路径完整保留（`TUSHARE_TRANSPORT=official` 一行切回），
   但**切换会造成数据集版本变化**（标注不同）。
3. **基准换源会改数值。** 3dp → 4dp，历史实验结论重跑。这是本轮最大的
   隐性成本，必须在报告里显式陈述，不能悄悄改。
4. **出处债无法完全清偿。** 199 份快照的 provider 永久不可知。
5. **akshare 对照链路脆弱。** EastMoney 封 IP 时轴 2 静默失效——
   所以对照脚本必须区分"一致"与"未能对照"，不能把失败当通过。
6. **官方 token 轮换仍悬空。** 见记忆 `tushare-token-exposed-on-remote`，
   与本方案正交，但轮换后 `TUSHARE_TRANSPORT=official` 的抽查路径需要重新验证。

## 验收标准

1. `TUSHARE_TRANSPORT=relay` 下 `data update` 产出的数据集，其
   `build_config` 中 `supplier_endpoint` 全部含真实 relay host，**无一为 `tushare.pro.*`**；
2. `TUSHARE_TRANSPORT=official` 时行为与改造前**完全一致**（回归测试）；
   显式指定 `relay` 而凭据缺失时抛 `AuthenticationError`，**不静默回退**；
3. `verify_transport_fidelity.py` 对四接口全部报一致，或有差异且已落盘；
4. 静默换源探针有结论，并写入运维报告（无论结论是否为"未换源"）；
5. 基准指数由 tushare 主供、akshare 对照，且规范化器单测通过；
6. `collect_index_weight_membership.py` 经 relay 成功采集（不再 AttributeError）；
7. `audit_raw_provenance.py` 产出报告：给出产出环境分布，
   反查不出的显式标 `unknown`，**不对历史 provider 下结论**；
8. 运维报告明确陈述"基准换数导致历史结论需重跑"。

## 决策记录

- owner 于 2026-09-12 确认：**published 数据允许由第三方中转供给**。
- owner 于 2026-09-12 确认：最看重 **正确性/可证伪性** 与 **稳定性/低成本**；
  且"很多数据取不到"是不可接受的 —— 覆盖率是硬要求。
- owner 于 2026-09-12 确认：**jiaoch 按 tushare 中转处理、其结果可信**，
  待出现其他源时再考虑替换。
- owner 于 2026-09-12 确认：采用**方案二**（jiaoch 上位 + 双轴证伪常态化），
  接受"基准换数 → 历史实验重跑"这一副作用。
- owner 于 2026-09-12 确认：传输**显式可配置**（非隐式 env 优先级）。
- owner 于 2026-09-12 确认：**promax 保留代码分支但默认不参与自动序**。
- owner 于 2026-09-12 确认：基准指数**换主供**（而非只加兜底）。

## 实测证据（2026-09-12）

| 观测 | 结果 |
| --- | --- |
| jiaoch ⟷ 官方直连 | `daily`/`index_daily`/`adj_factor`/`daily_basic` **4/4 列集相同、行数相同、值逐位相同** |
| 未知接口名错误串 | 两边**逐字相同**：`请指定正确的接口名` |
| `index_weight` 权限 | 官方 token **无权限**；jiaoch 正常返回 |
| `trade_cal` 形状 | jiaoch 稳定、`pretrade_date` 正常、破折号日期可用（promax 三缺陷全无） |
| jiaoch 限速 | ~65 req/min，响应无 `x-ratelimit-*` 头 |
| 反证 promax | `index_weight(000300.SH, 20230101..20230131)` promax 给 1 个快照、jiaoch 给 2 个；窄窗 `0101..0105` promax 给 **0 行**；宽窗 `20221201..20230228` 两家**完全一致**（6 快照 + 1800 行 + 权重和 99.9991）→ promax 按窗口丢快照 |
| 日历对账 | `crosscheck_calendar_relay.py`：存储日历 2833 个开市日（2015-01-05..2026-08-28）与 relay 开市日集合**完全一致**，`pretrade_date` 链完整，退出码 0 |
| 快照出处盘点 | 389 份 = tushare 126（`1.4.24`×63 + `1.4.29`×63）+ akshare 263（`1.18.23`×127 + `1.18.88`×136）；`1.4.29` / `1.18.88` **不在本机任何 conda 环境，也不在 uv 缓存** |
| 空结果 schema | 官方返回带列名的空表；relay 返回**无列**空表（`validate_supplier_frame` 对空表一律 `ContractError`，故今日不影响管线） |
