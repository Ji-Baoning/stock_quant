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
| 未知接口名错误串 | — | 2026-09-12 上午与官方**逐字相同**；**当天晚间起不再相同**（见下方修正） |
| 权限层级 | 高（借道） | 高（`index_weight` 可读，而本仓库 token 无权限） |
| 窗口语义缺陷 | 有（按窗口静默丢行） | 未观测到（`trade_cal` 稳定、`pretrade_date` 正常、破折号日期可用） |

**但要诚实标注证据的上界**：以上证明的是「jiaoch 的输出与官方直连不可区分」，
**不是**「jiaoch 的上游必然是 tushare 官方」。反例路径存在：一个聚合层若把请求
转发给另一个聚合层，而后者恰好从 tushare 取数，载荷仍会逐位一致 —— 而且它完全
可以在自己的网关上答掉一部分请求（jiaoch 今天就对不认识的 `api_name` 这么做了，
见「修正：未知接口名错误串」），逐位一致因此更不足以证明上游。因此本方案：

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
- **不改 `_ACCEPTED_MISSING_CODES`、验收口径。**
- **不 bump `DATASET_BUILD_CONTRACT_VERSION`。** 见 §2.3：bump 会立刻判所有
  已发布数据集 FAIL；`transport_id` 按可选增量字段处理。
- **不删 promax 的代码路径。** 它的问题只在窗口语义，代码本身可用（见选型）。
- **不改 `_CONFIGURED_SOURCES`**（不新增源位）。jiaoch 是 `tushare` 源的
  transport，不是新源位。
- **`_REQUIRED_ROLE` 只改 akshare 一项**（`True` → `False`，见 §4）。
  `tushare` 保持 `True`，`baostock` 保持 `False`。这是 §4 的角色变更的
  必要组成部分，不是顺手改动。

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

#### 1.1 请求客户端与来源描述分离（owner 修正 3）

现在 `TushareSource` 持有一个**既当客户端、又当来源标识**的对象：`self._client`
既要能被 `fetch()` 当作 SDK 调用（`.daily(...)` / `.index_daily(...)` /
`.stock_basic(...)`），又要回答"你是谁"。两个职责分开：

- `self._client` —— **请求客户端**，永远是真正有那三个方法的对象：
  - relay / official → 官方 `DataApi` 实例。`DataApi.__getattr__` 返回
    `partial(self.query, name)`，所以任意接口名都可调用；relay 与 official 的
    区别**只在 `_DataApi__http_url` 的取值**。
  - proxy → `TushareProxyClient`（普通类，只有三个具名方法，无 `__getattr__`）。
- `self._transport` —— **来源描述符**，只管 provenance：`label(endpoint)`、
  `sdk_version`、`kind`。`TushareSource` 不再用 `isinstance(self._client, ...)`
  去猜来源。

**上一版设计在这里是错的，必须记下来**：它写"`fetch()` 一行都不用改"，
理由是"relay 与 official 产出同一个客户端类型"。**这不成立** ——
`TushareRelayClient` 目前只有 `.query()`，`fetch()` 的
`self._client.daily(...)` 会直接 AttributeError。正确说法是：**relay 的请求
客户端就是官方 `DataApi`**（即 `TushareRelayClient` 内部持有的 `self._api`），
把它交给 `_client`，`fetch()` 才确实不用改；而 `TushareRelayClient` 自身
降级为**工厂 + 描述符**，不再被 `fetch()` 直接调用。

#### 1.2 发布环境禁止自动选择 transport（owner 修正 1）

管线不加载 `.env`，所以"未设置就走自动序"在凭据没导出时会**静默走 official**，
让"jiaoch 已上位"停留在设计上。规则改为：

| 场景 | 行为 |
| --- | --- |
| published 构建（`data update` 及一切会写数据集的路径）· 正常路径 | **必须显式设置 `TUSHARE_TRANSPORT=relay`**；未设置 → 直接失败，不回退 |
| published 构建 · `TUSHARE_TRANSPORT=proxy` | **禁止**。proxy（promax）窗口语义已知不可靠（capability-surface spec §8 判"绝不作证据源"），published 只允许 relay |
| published 构建 · break-glass 走官方 | `TUSHARE_TRANSPORT=official` 单独使用 → **失败**；只有同时设 `TUSHARE_ALLOW_OFFICIAL_PUBLISH=1` 才放行，且**留痕三处**：运行日志、`supplier_endpoint`（自然变为 `tushare.pro.*`）、运维报告。用于 relay 全面不可用时的应急 |
| 显式指定但凭据缺失或**半配置**（只设 URL 或只设 KEY）或初始化失败 | 直接失败，**不回退** |
| 自动序 **`relay → official`** | **只允许开发与诊断脚本**使用，且须显式传 `allow_auto_transport=True`（默认 `False`）；**proxy 不在自动序内** |

- 解析结果**必须打印进运行日志**，并作为 `dataset_build_config` 的一部分
  （经 `supplier_endpoint`）落盘 —— 验收要能**同时从日志与证据两头**证明走了 jiaoch。
- **promax 保留分支但不参与自动序，也不允许用于 published**：要它必须显式写
  `TUSHARE_TRANSPORT=proxy`，且只在开发/诊断脚本里。它仍是唯一已知可用的
  第三方传输，问题只在窗口语义，删掉分支是净损失。
- break-glass 的开关刻意做成**第二个变量**而非 `=official` 一个值：官方直连是
  本方案唯一允许 published 离开 relay 的口子，它必须是"刻意多敲一个变量"的动作，
  不可能因为拼错 transport 值而意外发生。

#### 1.3 传输选择读的是进程环境，不是 `.env`

管线侧（`src/stock_quant/cli.py`）只读 `os.environ`，**从不加载 `.env`**；
自带 `_load_env` 的全是离线脚本（`project/` 下的 `crosscheck_calendar_relay.py`、
`collect_index_weight_membership.py`、`check_data_sources.py`、
`verify_update_readiness.py` 等）。实测印证：`.env` 里 `TUSHARE_PROXY_*` 一直有值，
但 126 份 tushare 快照**全部标 `tushare.pro.*`** —— 说明管线实际走的是官方 SDK，
`.env` 的 proxy 配置从未生效。

**两个后果**：

1. "换到 relay"**不是改一行代码就自动发生的**：运行环境必须真正导出
   `TUSHARE_RELAY_*`。这正是 §1.2 要求显式指定的原因。
2. 离线脚本与管线可能走**不同**传输（前者加载 `.env`、后者不加载）。
   同一批数据可能一半来自 relay、一半来自 official，而旧标注区分不了。
   §2 的标注修正是为了让这种分叉**可见**。

顺带修正：`.env` 里 relay 两项写成了 `TUSHARE_RELAY_URL = <值>`（`=` 两侧有空格），
本仓库的 `_load_env` 能容忍，但 shell `source` 会失败。统一成无空格写法。

### 2. 证据标注（正确性的地基）

#### 2.1 标签由描述符报告

`_supplier_endpoint()` 改为**由 `self._transport` 报告**，不再用 `isinstance` 猜：

| transport | `supplier_endpoint` |
| --- | --- |
| relay | `tushare_relay.<host>.<endpoint>`，如 `tushare_relay.jiaoch.top.daily`（URL 解析不出 host → **报错**，不写 `unknown`，理由同 §2.3） |
| proxy | `tushare_proxy.<endpoint>`（不变） |
| official | `tushare.pro.<endpoint>`（**仅当确实打到 `api.waditu.com` 时才允许打此标**） |

判定机制：transport 解析时**记下实际会打到的 base URL**（官方 SDK 为
`_DataApi__http_url`，默认 `http://api.waditu.com/dataapi`；relay 为
`TUSHARE_RELAY_URL`），标签由该 URL 派生 —— URL 被改写过就**不可能**再打出
`tushare.pro.*`。`sdk_version` 保持"SDK 版本"语义，不掺 host，避免一个字段
担两种含义。

#### 2.2 标签怎么进入数据集版本（链条，别记错）

**`supplier_endpoint` 本身不进 `build_config`。**
`RawSnapshotEvidence`（`raw_store.py:27-40`）只带 `source` / `endpoint` /
`request_key` / `file_sha256` / `manifest_sha256`，注释写死
"never local paths, **supplier metadata** or reason prose"。

真正的链条是：

```
supplier_endpoint          （raw manifest.json 里的 metadata 字段）
  → manifest.json 内容变化
    → manifest_sha256 变化
      → build_config.raw_snapshots[].manifest_sha256 变化
        → dataset_version 变化
          （_dataset_version 哈希 files + schema_versions + build_config）
```

版本确实会 bump，但**不是**因为标签被直接哈希 —— 是因为它住在 manifest 里。
验收要引用标签时，路径是
`build_config.raw_snapshots[].manifest_sha256` → raw manifest → `supplier_endpoint`。

#### 2.3 内容寻址去重会让新标签被丢弃（本轮必须处理）

`RawStore.save`（`raw_store.py:59-88`）按**文件内容哈希**寻址，且
**命中已存在路径时直接复用旧 manifest、不重写**：

```python
snapshot_path = destination_parent / file_sha256
if snapshot_path.exists():
    manifest = json.loads((snapshot_path / "manifest.json").read_text())  # 旧标签
```

**后果**：relay 与 official 已实测 4/4 逐位一致，所以字节完全相同是常态而非
例外。此时 `manifest_sha256` 不变 → **数据集版本不变，且新标签被静默丢弃**：
数据集带着 `tushare.pro.*` 的旧标签，数据却取自 relay。

**这正是本方案要堵的洞，却在去重逻辑里原样复现了一遍。** 处置（已定案）：

**新增一层 `transport_id` 路径分量，并把它加进 `RawSnapshotEvidence`；
`request_key` 保持"纯请求的幂等键"语义，不被污染。**

- 路径形状 `data/raw/<source>/<endpoint>/<request_key>/<file_sha256>/`
  → `data/raw/<source>/<endpoint>/<transport_id>/<request_key>/<file_sha256>/`。
  内容寻址只在这**一层之内**生效：同源同请求、不同传输 = 两条独立快照，
  字节相同也各存各的。
- `transport_id` 是**路径安全**的**实际作答方**标识，由各源适配器在 `fetch()`
  时给出，与 `supplier_endpoint`（§2.1）同源，一并进 `FetchResult.metadata`；
  `_manifest_for` 把它提升为 manifest 顶层字段，`from_snapshot` 读回。
- **`transport_id` 是全局约束，不是 tushare 专属 —— 这是上一版最实质的漏洞。**
  上一版只定义了 tushare 的 descriptor，其余源落到默认值 `unknown`。但
  **akshare 的 `index_history` 本身就在 EastMoney / Sina / Tencent 之间切换**
  （§3 ② 的回退链）：两个上游若返回相同字节，`unknown` 会把它们压进同一条
  `.../unknown/<request_key>/<file_sha256>` 路径，`supplier_endpoint` 照旧被复用
  —— **本方案要堵的洞在 akshare 上原样保留**。取值规则：

  | 源 | `transport_id` 取自 |
  | --- | --- |
  | `tushare` | 实际 base URL 的 host：`jiaoch.top` / `api.waditu.com` / promax host |
  | `akshare` | **本次实际作答的 endpoint**：`eastmoney` / `sina` / `tencent`（回退链的胜出者） |
  | `baostock` | `baostock`（单一供应商，标识其自身） |
  | 未来新源位 | 其实际作答方；**不允许留空、不允许填一个源位级常量糊弄** |

- **`unknown` 不是"取不到时的兜底值"，而是保留字**：它只表示
  "历史快照未记录传输身份"。因此：
  - `RawStore.save` 遇到 `transport_id` 缺失 / 空串 / `unknown` → **抛错拒存**
    （`data_pipeline.py:1687` 是唯一入库口，拦住它就拦住了全部 published 路径）；
  - 目录名 `unknown` 因此**永不出现**，不会与"未记录"的历史布局混淆；
  - `verify_evidence` 只在**读取**缺字段的历史证据时回落到四段旧路径。
- **为什么不用方案 A（把 transport 并进 `request_key`）**：`request_key` 现在是
  请求的幂等键（`_path_component(result.request_key, "request key")`），
  把来源塞进去会让"同一个请求"在不同传输下变成两个不同请求，
  幂等语义与来源标注混为一谈。方案 B（复用时校验标签）只堵了静默沿用，
  两份同字节、不同来源的证据仍然**无法共存** —— 后到的会覆盖前者的证据行。

**必须同步改的点（漏一个就白改）**：

| 位置 | 改动 |
| --- | --- |
| `raw_store.py:27` `RawSnapshotEvidence` | 新增 `transport_id` 字段；`from_snapshot` 从 manifest 读 |
| `raw_store.py:59` `RawStore.save` | 路径插入 `transport_id` 分量；**校验非空且非保留字 `unknown`，否则抛错拒存** |
| `raw_store.py:89` `verify_evidence` | 按新路径解析；manifest 一致性回环里加入 `transport_id` |
| `raw_store.py:122` `_manifest_for` | 顶层写 `transport_id`；**缺失即抛错**，不写 `unknown` 兜底 |
| `data_pipeline.py:255` `_raw_snapshot_evidence_rows` | 去重键加入 `transport_id`（现为 4 元组） |
| `acceptance/models.py:218` `RawSnapshotBinding` | 加**可选**字段（`extra="forbid"`，不加就解析失败）；文档串"five-field"同步改 |
| `tushare.py` / `akshare.py` / `baostock.py` 的 `fetch()` | 各适配器产出 `transport_id`；akshare 用**回退链实际胜出的 endpoint** |
| `tests/unit/test_raw_store.py` / `tests/integration/conftest.py` | 现有夹具的 `FetchResult` 必须补上 `transport_id`，否则全部拒存 |

**向后兼容（必须显式处理，不能靠运气）**：存量 389 份快照在旧路径上，
已发布数据集的 `build_config.raw_snapshots[]` 是五字段记录。
`transport_id` 因此**必须带默认值**（缺省 = 旧布局，语义是字段不存在，
**不是**字符串 `unknown`），`verify_evidence` 在缺省时回落到四段旧路径。
否则改完代码读不了任何历史数据集。
必配一条测试：**旧五字段记录仍能解析并解析到旧路径**。

必配的第二条测试：**同一 `DataRequest` 先经 official、再经 relay，
两份证据共存且各自标签正确**（不是"后者覆盖前者"）。

必配的第三条测试（针对本节的 akshare 漏洞）：**同一 akshare 请求先由
EastMoney 作答、再由 Sina 作答，即使字节相同也落在两条路径上**；
以及 `RawStore.save` 对空 / `unknown` 的 `transport_id` **抛错拒存**。

**`DATASET_BUILD_CONTRACT_VERSION` 保持 `1`，不 bump —— 这是有意的，不是漏掉。**
`checks.py:399-401` 是 `contract != DATASET_BUILD_CONTRACT_VERSION → FAIL`，
所以 bump 到 2 会让**每一份已发布数据集**在 `source_role_health` 上立刻失败
（旧验收记录按版本绑定、不受影响，但历史数据集从此无法重跑验收）。
`transport_id` 是对既有 payload 的**可选增量字段**（缺省即旧布局），
读取侧两种形状都能解析，故按增量而非破坏性变更处理。
代价要写明：**同一版本号下从此存在两种 payload 形状**，判据是
`raw_snapshots[]` 行里有没有 `transport_id`。若将来需要区分，再单独 bump 并
同时给出历史数据集的处置方案（重发布或豁免），不在本轮顺手做。

#### 2.4 代价

数据集版本会 bump（经 §2.2 的链条），即使数据字节相同 —— 这是正确行为。
但意味着本轮要重发布一次数据集，且**验收记录按版本绑定**（见前文）。

### 3. 证伪体系

**① 轴 1 · 传输保真 · 新脚本 `project/verify_transport_fidelity.py`**

对 `daily` / `index_daily` / `adj_factor` / `daily_basic` 抽样，官方直连 ⟷ relay
逐位比对（列集、行数、值全等），差异落盘。这是把
`project/crosscheck_calendar_relay.py` 的做法从"只对日历"扩到四接口。

- **抽样而非全量**：官方 token 限速严，全量不可行；抽样覆盖"每种接口 + 每个
  数据形态（正常行 / 空窗口 / 边界日期）"。
- 结果**三种态**，不是两种：`AGREE` / `DIFFER` / `UNAVAILABLE`（官方取不到）。
  第三态在 §3 ④ 里有独立的阻断语义。

**② 轴 2 · 上游真值 · 给 `AkShareSource` 加个股日线 handler（owner 修正 5）**

新增 `stock_daily` endpoint，把 `tushare daily ⟷ akshare daily` 这条唯一的
跨厂商对照接回来。**只做对照，绝不供 published 数据**（akshare 上游是
EastMoney，有 IP 级封禁史）。**口径不写死就会被误报成数据错误**，故契约如下。

**接口与 fallback 顺序**（沿用 `_INDEX_FALLBACKS` 的 eastmoney → sina → tencent 模式）：

| 顺位 | 接口 | symbol 形态 | 不复权 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | `stock_zh_a_hist`（东财） | `000001`（6 位） | `adjust=""` | 列：日期/开盘/收盘/最高/最低/成交量/成交额/… |
| 2 | `stock_zh_a_daily`（新浪） | `sz000001` | `adjust=""` | 列：date/open/high/low/close/volume/amount；官方 docstring 自陈"大量抓取容易封 IP" |
| 3 | `stock_zh_a_hist_tx`（腾讯） | `sz000001` | `adjust=""` | 列：date/open/close/high/low/**amount** —— **没有 volume**，故只能比价格与 amount，**成交量对照必须跳过** |

**符号与日期映射**：tushare `000001.SZ` 的后缀 `.SZ`/`.SH`/`.BJ` → `sz`/`sh`/`bj`
前缀，6 位码即 akshare 的 `symbol`。北交所在 akshare 侧覆盖不稳，**遇到即记
`UNSUPPORTED`，不算差异**。日期统一转 `YYYY-MM-DD` 后比较（tushare 给
`YYYYMMDD` 字符串，`stock_zh_a_hist` 给 `datetime.date`）。

**单位与容差必须由夹具钉死，不能靠推断。** 本地 docstring 只描述字段名、
**不描述单位**；tushare `vol` 单位是手、`amount` 单位是千元，而 akshare 三个
上游互不相同，腾讯连 volume 字段都没有。单位弄错会被误报成"数据错误"，所以：

- 实施时先落一份**已人工核对的夹具**：每个接口一条真实响应 + 确认过的归一化
  期望值，提交进仓库；
- 归一化对齐 tushare `daily`（`vol` 手 / `amount` 千元），**换算因子写在夹具里**，
  不散落在代码里；
- 容差只有**一个判据**（组合式，不是"两者分别满足"）：

  ```
  |actual - expected| <= abs_tol + rel_tol * |expected|
  ```

  下表**只列参数，不再列"容差"**，避免与判据本身打架：

  | 字段 | `abs_tol`（暂定） | `rel_tol`（暂定） | 参数取这个量级的理由 |
  | --- | --- | --- | --- |
  | 开 / 收 / 高 / 低 | 1e-3 | 1e-4 | 3dp vs 4dp 的**最大**绝对舍入差约 `5e-4`（两侧各半格），故 `abs_tol` 必须 ≥ `5e-4`；`1e-4` 不够 |
  | vol / amount | 1e-3 | 1e-3 | 单位换算 + 不同源的舍入与汇总口径；`amount` 量级在 1e5~1e7，此格实际由 `rel_tol` 主导 |

- **数值是暂定，最终由真实夹具校准**：上面的数不是从文档推出来的，是给出量级
  并说明为什么不能更小。夹具落地后若实测需要调整，改的是夹具里的常量，
  **不改本节的判据形式**。
- **组合式的必要性**：低价股（1 元股票上 0.01 元的差 = 1% 相对误差）与接近零的
  成交额（`amount ≈ 0` 时相对误差发散）都会被纯相对判据误判；而单纯用绝对容差
  又管不住高价股的相对漂移。两者相加，谁在主导由量级自然决定。
- 夹具里必须包含**一对边界样本**（"刚好通过"与"刚好不通过"），否则容差参数
  改错了不会被任何测试发现。

**边界情形必须显式分类，不能一律当差异**：

| 情形 | 分类 | 是否算差异 |
| --- | --- | --- |
| 停牌日 | `ABSENT_EXPECTED` | 否 |
| 退市 / 未上市 | `UNSUPPORTED` 或 `ABSENT_EXPECTED` | 否 |
| 两侧都空 | `AGREE_EMPTY` | 否（**必须与"未取到"区分开**） |
| 接口失败（含封 IP） | `UNAVAILABLE` | **绝不算通过** |
| 超容差 | `DIFFER` | 是 |

**`supplier_endpoint` 记录**：fallback 链的最终作答者必须落在那三个具体端点上，
**不能用一个默认值掩盖实际走了谁**（现有 `_first_valid_index` 已返回该 endpoint，
照用即可）；这点与 §2.1 的要求一致。

**③ 静默换源探针（本轮最重要的一条）**

promax 的教训是 `fallback_on_empty` 让"空"不再等于"确实为空"。jiaoch 上
**这条尚未验证**。探针设计：

- 取官方**合法返回空**的请求（如对不存在代码的 `daily`、超出区间的窗口），
  对比 jiaoch 是返回空、还是返回了非空（= 换源作答）。
- 取官方**明确报错**的请求，对比错误串。原本指望"未知接口名逐字相同"，
  但 2026-09-12 晚的实测推翻了这个预期：jiaoch 的网关对不认识的 `api_name`
  自己作答。该差异已由 owner 显式豁免（见「修正：未知接口名错误串」），
  该用例保留，因为它继续充当"relay 有没有自己的答话路径"的探针。
- 结论写进运维报告，并驱动 §3 ④ 的阻断动作。**最小版本即阶段 0**，
  必须在阶段 1 首次真实发布之前跑完并留痕。

**④ 发现异常后的强制动作（owner 修正 4）**

**不能让"写入报告并继续发布"成为差异的唯一后果。** 差异要么被解释并被批准，
要么阻断发布：

| 情形 | 动作 |
| --- | --- |
| 官方不可用 / 限流（`UNAVAILABLE`） | 记 `UNAVAILABLE`，**不等于校验通过**；发布可继续，但报告必须写明"传输保真本轮未校验" |
| relay 与官方出现**可复现**差异 | **停止本次发布**；或由 owner 显式人工豁免（豁免须留痕、可审计） |
| 探针发现 relay 对"官方合法空结果"返回非空 | **立即禁用 relay 主供**，回到信任边界重新评估 |
| 一次性的偶发差异（重试后不可复现） | 允许继续，但必须记录重试次数与最终判定 |

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
- **`_REQUIRED_ROLE["akshare"]` 改为 `False`（owner 修正 2）。**
  上一版把它保持 `True` 是错的：`_require_available`（`data_pipeline.py:1094-1104`）
  在源**未启用**时就判 `required_source_disabled` 并阻塞发布 ——
  把状态设成恒 `ok` 解决不了"禁用 akshare 就发不出去"。akshare 既然只剩
  best-effort 职责，就不该再持有发布否决权，否则它是个**隐藏的发布单点**。
  - 验收侧**无需改代码**（已核实）：`_check_source_roles`（`checks.py:386-415`）
    读的是**记录在 `build_config.source_status` 里的 `required` 标志**，
    而该标志来自 `_REQUIRED_ROLE` —— 改常量即自动生效。同理
    `_unbound_required_sources`（`checks.py:737-752`）不再要求 akshare
    绑定 raw 快照。
  - 影响面：`build_config` 内容变化 → 数据集版本变化（本来就要重发布）。
  - 旧数据集（`akshare.required=true`）仍按旧口径判定 —— 验收记录按版本绑定，
    这是预期行为，不是回归。

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
- **能**给出的是后续动作的依据：`1.4.29` / `1.18.88` 那 199 份的**产出环境**记
  `unknown`（这是报告里的一个结论字段，**与 §2.3 的保留字 `transport_id` 无关**），
  不猜、不重标；是否需要用新标注重建，留给报告结论而不是脚本自动决定。

**这不是"给出正确答案"，是"把不确定性写下来"。**

## 落地顺序

本方案含五个可独立验收的交付物（阶段 0 是前置闸门），**顺序不可颠倒**：

| 阶段 | 交付物 | 为什么是这个位置 |
| --- | --- | --- |
| 0 | **最小静默换源探针**（§3 ③ 的最小版本） | **必须在阶段 1 首次真实发布之前跑。** 阶段 1 一发布，数据集就指向 relay；若探针之后才否掉 relay，那份数据已经被污染了。探针只查"官方合法空结果 relay 是否返回非空"，不依赖本方案任何代码改动，**今天就能跑** |
| 1 | §1 传输层 + §2 证据标注 | 必须最先：否则 `data update` 会用旧标注产出一个"看起来是官方直连"的数据集，后续全部白做 |
| 2 | §5 出处审计 | 紧接着做：它给阶段 1 的新标注提供基线对照 |
| 3 | §3 证伪体系（阶段 0 探针的完整版） | 主传输换好之后才有意义；常态化后**阻断语义继续生效，仍是阶段 4 的放行闸门** |
| 4 | §4 覆盖缺口与基准 | **仅在阶段 3 探针未发现换源时开始**；影响历史实验结论（重跑），单独一轮 |

阶段 1+2 是一个自然的提交边界，3、4 各自独立成轮。

**阶段 0 是阶段 1 的硬闸门**：探针一旦命中"jiaoch 对官方合法空结果返回非空"，
**不进入阶段 1**，relay 主供的决策回炉重新评估（§3 ④ 第一行阻断语义）。
把探针提到这里，是因为现在的顺序会让"最重要的一条验证"发生在"已经用了它"
之后 —— 那就是先上车后验证。

**阶段 1 的验收必须包含一次真实发布**：显式设 `TUSHARE_TRANSPORT=relay` 跑通
`data update`，再从运行日志与 `build_config` 两侧证明 jiaoch 确实被使用
（§2.2 的链）；不设该变量时同一命令必须失败。这两条要在阶段 1 就落地，
不能推迟到最后。

**阶段 3 把阶段 0 的探针常态化**（扩到轴 1 四接口 + akshare 跨族对照 + §3 ④
的完整阻断动作表）。此后任何时候命中阻断条件，**立即禁用 relay 主供**，
阶段 4 不得开始，回到信任边界重新评估。这不是"写进报告再继续"。

## 测试

| 层 | 用例 |
| --- | --- |
| relay 传输（单元，不联网） | 沿用 `tests/unit/test_tushare_relay.py` 的 `FakeSdk`；`TushareSource` 在 `TUSHARE_TRANSPORT=relay` 下把官方 `DataApi` 交给 `_client`，`fetch()` 行为与 official 分支一致（§1.1） |
| 传输解析 · 严格模式 | published 路径未设 `TUSHARE_TRANSPORT` → 失败；设为 `proxy` → 失败；设为 `official` 而无 `TUSHARE_ALLOW_OFFICIAL_PUBLISH=1` → 失败；设为 `relay` 但凭据缺失 / 半配置 / init 失败 → 失败且**零回退** |
| 传输解析 · break-glass | `official` + `TUSHARE_ALLOW_OFFICIAL_PUBLISH=1` → published 放行，证据标签为 `tushare.pro.*` 且日志留痕 |
| 传输解析 · 开发模式 | `allow_auto_transport=True` 时自动序 **relay → official**；**proxy 不参与自动序** |
| 证据标注 | relay 下 `supplier_endpoint` 含真实 host；official 仍 `tushare.pro.*`；proxy 仍 `tushare_proxy.*` |
| 传输身份寻址 | 同一 `DataRequest` 先 official 后 relay → 两份快照共存于各自 `transport_id` 目录，标签各自正确（§2.3） |
| 旧证据兼容 | 五字段 `RawSnapshotBinding` 记录仍能解析并回落到四段旧路径；`request_key` 仍是纯请求幂等键 |
| build_config 两种形状 | 五键（旧）与六键（新）`raw_snapshots[]` 行都能解析；`pipeline_contract_version` 仍为 `1` |
| transport_id 约束 | 缺失 / 空串 / `unknown` → `RawStore.save` 抛错拒存（`unknown` 目录永不出现） |
| akshare 回退身份 | 同一请求先由 EastMoney 作答、再由 Sina 作答 → 两条路径，字节相同也不复用 |
| akshare 对照契约 | 六类结果各一条夹具；单位换算因子由夹具钉死；容差用 `abs_tol + rel_tol` 且配一对边界样本；腾讯源跳过成交量；`.BJ` 记 `UNSUPPORTED` |
| 基准换源 | tushare `index_daily` 形状的规范化器单测（含 4dp 与空窗口） |
| required 角色 | `_REQUIRED_ROLE["akshare"] is False`；禁用 akshare 的发布**不被** `_require_available` 阻塞（stub 适配器，不联网） |
| 空结果守卫 | 采集器层：空表必须带列名，否则拒收（relay 空表无列名已实测） |
| 外部契约 | 一条 live 契约测试覆盖 relay（可跳过） |

**注意**：relay 空表无列名这一条，**今天不咬管线**——`validate_supplier_frame`
对空表一律 `raise ContractError`（`base.py:155-156`），两种传输走同一分支。
它只对新采集器（`index_weight`）是隐患，所以在采集器层加守卫，不在 `base.py` 改语义。

## 已知残留风险

1. **jiaoch 的上游身份未证实（最重）。** 逐位一致只能证明"输出与官方不可区分"。
   第 3 节 ③ 的探针是唯一检测手段，且它只能证伪、不能证实。
2. **jiaoch 是外部单点。** 单主机、第三方运营、有账号封停风险。
   缓解：官方直连路径完整保留，作为**留痕的 break-glass**
   （`TUSHARE_TRANSPORT=official` + `TUSHARE_ALLOW_OFFICIAL_PUBLISH=1`，
   正常发布禁止）。**切换会造成数据集版本变化**（标注变为 `tushare.pro.*`），
   且应急发布期间轴 1 的传输保真校验失去意义（两边同一路径）。
3. **基准换源会改数值。** 3dp → 4dp，历史实验结论重跑。这是本轮最大的
   隐性成本，必须在报告里显式陈述，不能悄悄改。
4. **出处债无法完全清偿。** 199 份快照的 provider 永久不可知。
5. **akshare 对照链路脆弱。** EastMoney 封 IP 时轴 2 静默失效——
   所以对照脚本必须区分"一致"与"未能对照"，不能把失败当通过。
6. **官方 token 轮换仍悬空。** 见记忆 `tushare-token-exposed-on-remote`，
   与本方案正交，但轮换后 `TUSHARE_TRANSPORT=official` 的抽查路径需要重新验证。

## 验收标准

1. **阶段 0 先行**：首次 relay 真实发布**之前**，"官方合法空结果"探针已跑完并
   留痕；若命中换源作答，relay 主供已被禁用，且**未发生任何 relay 发布**；
2. **发布必须显式指定 transport，且只允许 relay**：未设置 `TUSHARE_TRANSPORT`
   时 published 构建**失败**；设为 `relay` 而凭据缺失 / 半配置 / 初始化失败时
   **失败且不回退**；设为 `proxy` 时**失败**；设为 `official` 而未设
   `TUSHARE_ALLOW_OFFICIAL_PUBLISH=1` 时**失败**，break-glass 放行时
   日志与 `supplier_endpoint` 两处都必须显示走的是官方直连；
3. **证据链能自证用了 jiaoch**：由
   `build_config.raw_snapshots[].manifest_sha256` 解析到 raw manifest，
   其 `supplier_endpoint` 全部为 `tushare_relay.<host>.*`，**无一为 `tushare.pro.*`**；
   运行日志打印同一结论（标签不直接进 `build_config`，故走 manifest 解析，见 §2.2）；
4. **传输身份进入快照寻址，且不出现 `unknown`**：同一 `DataRequest` 先经
   official 再经 relay，两份快照在
   `data/raw/<source>/<endpoint>/<transport_id>/...` 下**共存**，各自 manifest
   标签正确，`request_key` 仍是纯请求幂等键；新快照的 `transport_id`
   **非空且不等于保留字 `unknown`**，缺失 / 空 / `unknown` 一律抛错拒存；
   akshare 的 `transport_id` 是**回退链实际胜出者**（§2.3）；
5. **向后兼容不被破坏**：存量五字段 `RawSnapshotBinding` 记录仍能解析并回落到
   四段旧路径（§2.3）；开发/诊断路径走 official 时行为与改造前**完全一致**
   （回归测试）；
6. `verify_transport_fidelity.py` 四接口全部 `AGREE`；出现 `DIFFER` 时必须
   **已解释并获人工批准（留痕）**，否则**发布失败**；`UNAVAILABLE` 允许发布，
   但报告必须写明"本轮未校验"；
7. 静默换源探针有结论并驱动 §3 ④ 的阻断动作；若发现换源作答，
   relay 主供已被禁用；
8. akshare 个股日线对照跑通，六类结果（`AGREE` / `DIFFER` / `UNAVAILABLE` /
   `ABSENT_EXPECTED` / `AGREE_EMPTY` / `UNSUPPORTED`）均有夹具覆盖，
   **单位换算与 abs_tol+rel_tol 容差均由夹具钉死**而非推断；
9. `_REQUIRED_ROLE["akshare"] is False`，且**禁用 akshare 不再阻塞发布**
   （用 stub 适配器验证）；
10. 基准指数由 tushare 主供、akshare 对照，规范化器单测通过；
11. `collect_index_weight_membership.py` 经 relay 成功采集；
12. `audit_raw_provenance.py` 产出报告：给出产出环境分布，反查不出的显式标
    `unknown`，**不对历史 provider 下结论**；
13. 运维报告明确陈述"基准换数导致历史结论需重跑"。

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

### 2026-09-12 复审修正（owner，五条）

owner 复审初稿后提出五条修正，**均不推翻"jiaoch 作为 tushare 主源"**，
但不修则不能实施：

1. **发布环境禁止自动选择 transport**（→ §1.2）。published 构建必须显式
   `TUSHARE_TRANSPORT=relay`；缺失 / 半配置 / init 失败一律直接失败、零回退；
   自动序只留给开发与诊断脚本。原本"隐式优先级链"的设计作废。
2. **修正 akshare 的 required 矛盾**（→ §4）。既已把 akshare 降为对照角色，
   `_REQUIRED_ROLE["akshare"]` 就必须是 `False`；把状态设成恒 `ok`
   **不解决** `_require_available()` 的阻塞，只会留下一个隐藏的发布单点。
   已验证 `_check_source_roles` 读的是 `build_config.source_status` 里记录的
   `required` 标志，故改常量不需要改验收代码。
3. **明确 relay client 的接口形态**（→ §1.1）。初稿称"`fetch()` 一行都不用改"
   是**错的**：`TushareRelayClient` 只有 `.query()`，没有 `.daily()` /
   `.index_daily()` / `.stock_basic()`。采纳 owner 的第 2 种形态 ——
   `TushareSource` 内部持有官方 `DataApi` 作为真正的请求客户端，
   transport descriptor 只负责来源描述与凭据解析。
4. **补上发现异常后的强制动作**（→ §3 ④）。官方不可用/限流 → 记 `UNAVAILABLE`，
   **不等于校验通过**；可复现差异 → 停止发布或要求人工豁免并留痕；
   探针发现 jiaoch 对官方合法空结果返回非空 → **立即禁用 relay 主供**并重新评估。
   验收标准中相应一条改为"差异已解释并批准，否则发布失败"（现第 6 条）。
5. **补全 AkShare 个股日线对照契约**（→ §3 ②）。原稿只写"用 akshare 对照"，
   缺少接口与回退序、复权口径、单位换算、代码/日期映射、停牌退市空窗处理、
   容差与 `supplier_endpoint` 的实际记录方式 —— 缺任何一项都无法实施。

### 2026-09-12 二审修正（owner，六条）

owner 对上一版再审，提出三条高/中优先级问题与三处文字契约问题，全部采纳：

1. **RawStore 去重方案定案**（→ §2.3）。上一版仍留 A/B 二选一，且两条都不成立：
   方案 A 把 transport 并入 `request_key` 会毁掉它的纯幂等语义，方案 B 只堵了
   静默沿用、同字节不同来源的两份证据仍无法共存。**定案为新增 `transport_id`
   路径分量并加入 `RawSnapshotEvidence`**，`request_key` 不动；同时列全必须同步
   改的六处（含 `acceptance/models.py:218` 那个 `extra="forbid"` 的
   `RawSnapshotBinding`）与存量五字段记录的向后兼容约束。
2. **首次 relay 发布挪到探针之后**（→ 落地顺序新增阶段 0）。上一版让阶段 1
   先真实发布、阶段 3 才跑"本轮最重要"的探针 —— 探针若随后否掉 relay，
   那份数据集已被污染。**阶段 0 因此成为阶段 1 的硬闸门**，且它不需要本方案
   任何代码改动，今天就能跑。
3. **published 只允许 relay，official 收窄为留痕 break-glass**（→ §1.2、
   残留风险 2、验收 2）。上一版"发布必须 relay"与"可 `=official` 应急切回"
   自相矛盾。**proxy 亦明确禁止用于 published**。
4. **自动序笔误**（→ §1.2）。上一版写 `relay → proxy → official`，下一句却说
   promax 不参与自动序。**统一为 `relay → official`**。
5. **容差改为 `abs_tol + rel_tol` 并用**（→ §3 ②）。纯相对误差会误判低价股与
   接近零的成交额，两种容差都由夹具验证。
6. **验收文案笔误**（→ 验收 8）。上一版写"五类结果"却列了六类，已改。
7. **（自查补充）`DATASET_BUILD_CONTRACT_VERSION` 明确不 bump**（→ §2.3）。
   `checks.py:399-401` 是相等判据，bump 会让所有历史数据集在
   `source_role_health` 上立刻 FAIL，故按可选增量字段处理；
   代价是同一版本号下存在两种 payload 形状，已在 §2.3 写明判据。

### 2026-09-12 三审修正（owner，两条）

1. **`transport_id` 的默认 `unknown` 会让其他源继续碰撞**（→ §2.3）。上一版只定义了
   tushare 的 descriptor，其余源落到 `unknown`；而 **akshare 的 `index_history` 本身
   就在 EastMoney / Sina / Tencent 之间切换**，两个上游返回相同字节时仍会压进同一条
   `unknown/...` 路径、复用旧 `supplier_endpoint` —— **本方案要堵的洞在 akshare 上
   原样保留**。改为：`transport_id` 是**全局约束**（列出四个源位的取值来源），
   `unknown` 降级为**保留字**（只表示"历史快照未记录"），
   `RawStore.save` 遇缺失 / 空 / `unknown` **抛错拒存**，目录名 `unknown` 因此永不出现。
2. **容差表与判据自相矛盾，且价格 `abs_tol` 不足**（→ §3 ②）。表格写"绝对与相对
   分别满足"，正文用组合式，两者不是同一判据；且若要覆盖 3dp vs 4dp 舍入，
   最大绝对差约 `5e-4`，原 `1e-4` 不够。改为：**组合式为唯一判据**，表格只列
   `abs_tol` / `rel_tol` 两个参数并给出量级理由（价格 `abs_tol` 提到 `1e-3`），
   **具体数值由真实夹具校准**，夹具须含一对"刚好通过 / 刚好不通过"的边界样本。

## 实测证据（2026-09-12）

| 观测 | 结果 |
| --- | --- |
| jiaoch ⟷ 官方直连 | `daily`/`index_daily`/`adj_factor`/`daily_basic` **4/4 列集相同、行数相同、值逐位相同** |
| 未知接口名错误串 | 上午两边**逐字相同**：`请指定正确的接口名`；**晚间起被推翻**，见「修正：未知接口名错误串」一节 |
| `index_weight` 权限 | 官方 token **无权限**；jiaoch 正常返回 |
| `trade_cal` 形状 | jiaoch 稳定、`pretrade_date` 正常、破折号日期可用（promax 三缺陷全无） |
| jiaoch 限速 | ~65 req/min，响应无 `x-ratelimit-*` 头 |
| 反证 promax | `index_weight(000300.SH, 20230101..20230131)` promax 给 1 个快照、jiaoch 给 2 个；窄窗 `0101..0105` promax 给 **0 行**；宽窗 `20221201..20230228` 两家**完全一致**（6 快照 + 1800 行 + 权重和 99.9991）→ promax 按窗口丢快照 |
| 日历对账 | `crosscheck_calendar_relay.py`：存储日历 2833 个开市日（2015-01-05..2026-08-28）与 relay 开市日集合**完全一致**，`pretrade_date` 链完整，退出码 0 |
| 快照出处盘点 | 389 份 = tushare 126（`1.4.24`×63 + `1.4.29`×63）+ akshare 263（`1.18.23`×127 + `1.18.88`×136）；`1.4.29` / `1.18.88` **不在本机任何 conda 环境，也不在 uv 缓存** |
| 空结果 schema | 官方返回带列名的空表；relay 返回**无列**空表（`validate_supplier_frame` 对空表一律 `ContractError`，故今日不影响管线） |

### 修正：未知接口名错误串（2026-09-12 晚，阶段 0 探针实测）

上表"上午两边逐字相同"的观测**当天晚间已不成立**，此处按实测修正。

`TushareRelayClient` 对不认识的 `api_name` 发请求，jiaoch 回：

```
token不对，您传过来的是<KEY>请确认        # <KEY> 为回显的 relay key，探针已脱敏
```

官方直连对同一请求回 `请指定正确的接口名`。三个不同的假接口名
（`not_a_real_tushare_endpoint` / `foo_bar_baz` / `daily_`）**全部复现同一文案**，
因此是稳定行为而非偶发。看形状，jiaoch 的网关对不认识的 `api_name` 在转发上游之前
就挡掉了，并用它自己的鉴权文案作答；`<KEY>` 处回显的是我们提交的 relay key。

**处置（owner，2026-09-12）**：按 §3 ④ 的"owner 显式人工豁免"记为**已豁免差异**，
阶段 0 闸门放行、进入阶段 1。判定依据：

- 这是网关的**输入校验路径**，不是换源作答 —— 它从不返回数据；
- 阶段 0 探针的核心问题（"官方合法空结果处 relay 是否返回非空"）三类用例全部
  `AGREE_EMPTY`；
- 填充数据此前有 4/4 逐位一致的记录。

**豁免的作用域只有这一例的这条差异**，不把退出码 `1` 一般化为"可以继续"：
`SUBSTITUTION`、以及任何新的或不同的 `ERROR_DIFFERS`，仍是硬阻断。

**本修正同时收窄一条证据**：原先把"错误串逐字相同"当作 jiaoch 与官方不可区分的
证据之一，这条证据现已失效。剩下支撑"输出与官方不可区分"的是逐位比对、日历对账
与权限层级，证据上界的结论不变。
