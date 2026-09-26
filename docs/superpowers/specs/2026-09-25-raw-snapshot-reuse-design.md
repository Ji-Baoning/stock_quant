# 数据更新复用已有原始快照（增量取数收尾）· 设计

- 日期：2026-09-25
- 状态：**owner 已复核**（2026-09-25）——§7 三处决策位已按建议值定调，正文即为冻结口径
- 上游：[2026-09-19-data-type-expansion-architecture-design.md](2026-09-19-data-type-expansion-architecture-design.md)
  的 D5 / B2 批次
- 关系：本设计只收尾 D5 的**执行层**缺口，不改变 D5 已落地的表级增量窗口语义

## 0. 摘要

| 编号 | 缺口 | 改动层次 | 预期效果 |
| --- | --- | --- | --- |
| R1 | 执行层每轮对每个符号真实发起网络调用；`RawStore.save` 只做内容寻址去重，从不跳过抓取 | 存储层 `resolve_reusable` + 抓取层接线 | 失败续跑轮已覆盖符号零调用 |
| R2 | 「哪些符号的本轮请求已有可用答案」不可见 | build_config + 调用账本 + manifest | 复用/实时计数进版本哈希，可审计 |
| R3 | carried 段无法回溯到基线 manifest | build_config `baseline_version` | 拼接链闭环 |

预期效果均为**推算**。R2/R3 的计数与哈希以实际发布版本的 `build_config` 为准。

## 1. 背景与现状结论（2026-09-25 逐条核实）

**已落地、本次不动。** 表级增量窗口（spec D5 / B2 批次）：`fetch_windows.py`
的 `last_covered_plus_1` 让 `daily_bar` 只拉 `[基线覆盖终点+1, 最新开市日]`；
公司行为走 `disclosure_calendar` 90 天回看、每轮强制重拉；`_merge_daily` 实现
「基线结转 + 窗口内新行替换」的表级拼接
（[data_pipeline.py:2600-2624](../../../src/stock_quant/data_pipeline.py#L2600-L2624)）。
同日重跑确实零调用：daily/benchmark 整块包在 `if not daily_skipped:` 内
（[data_pipeline.py:906-912](../../../src/stock_quant/data_pipeline.py#L906-L912)）。

**真正缺的一块。** 执行层每轮对每个符号真实发起网络调用。`RawStore.save`
（[raw_store.py:70-101](../../../src/stock_quant/data_sources/raw_store.py#L70-L101)）
只在 `snapshot_path.exists()` 时跳过**落盘**，从不跳过**抓取**。最痛的场景是失败
续跑：一轮在若干符号后失败（限速/网络），已抓符号的快照留在 raw 树里但什么都没
发布；重试轮计划出完全相同的窗口 → 相同 `request_key` → 659 只全部重新走网络。

**既有测试语义有意保留。** `test_update_keeps_current_and_raw_when_the_calendar_fetch_fails`
（[test_data_pipeline.py:1245-1265](../../../tests/integration/test_data_pipeline.py#L1245-L1265)）
断言「日历绝不回放缓存」——日历是时钟，本次改动保留该语义，测试不需要改
（已核实：日历失败发生在 daily 之前，首轮不会留下 daily 快照，第二轮 `trade_cal`
仍是两次实时调用）。

**漂移审计（D5.4）已存在，但机制与直觉不同。**
[project/drift_audit.py](../../../project/drift_audit.py)（status: diagnostic）是按
`build_config.raw_snapshots` **逐条重取再比对哈希**（`classify_drift`），并不消费
同一 `request_key` 下的兄弟目录。补偿控制因此与复用程度无关——它每次独立重取。

### 1.1 行号与实现事实（核实结论，替换原稿行号）

| 符号 | 实际位置 | 原稿 |
| --- | --- | --- |
| `_fetch_primary_stock` | [:1591](../../../src/stock_quant/data_pipeline.py#L1591) | 1609（是循环体行） |
| `_deepen_head_anchors` | [:1650](../../../src/stock_quant/data_pipeline.py#L1650) | 1710（是 `_dispatch` 调用行） |
| `_fetch_benchmarks` | [:1792](../../../src/stock_quant/data_pipeline.py#L1792) | 1810（同上） |
| `_fetch_validation_daily` | [:2452](../../../src/stock_quant/data_pipeline.py#L2452) | 2473（同上） |
| `_dispatch` | [:2523](../../../src/stock_quant/data_pipeline.py#L2523) | ✓ |
| `dataset_build_config` | [:471](../../../src/stock_quant/data_pipeline.py#L471) | ✓ |
| `_plan_skips_fetch` | [:457](../../../src/stock_quant/data_pipeline.py#L457) | ✓ |

其余被本设计依赖的事实：

- **接线边界是构造性的。** `self._dispatch(` 只有四个调用点（1610 / 1710 / 1810 /
  2473）。其余写 raw 的路径全部绕过它：日历 [:1926](../../../src/stock_quant/data_pipeline.py#L1926)、
  证券主档 [:2066](../../../src/stock_quant/data_pipeline.py#L2066)、公司行为
  `_fetch_one` [:2144](../../../src/stock_quant/data_pipeline.py#L2144)、懒仲裁通道
  [:3502](../../../src/stock_quant/data_pipeline.py#L3502)/[:3585](../../../src/stock_quant/data_pipeline.py#L3585)。
  所以「不碰公司行为/日历/主档/懒通道」无需额外断言。
- `_record_raw` 就是 `RawStore.save`（[:2591-2598](../../../src/stock_quant/data_pipeline.py#L2591-L2598)），
  **没有空帧判断**：空响应照常落盘。
- `_require_available`（[:1563-1589](../../../src/stock_quant/data_pipeline.py#L1563-L1589)）
  只检查「配置里是否启用」，**不做任何网络探测**。
- `_manifest_for` 对缺失字段有缺省（[raw_store.py:161-176](../../../src/stock_quant/data_sources/raw_store.py#L161-L176)）：
  `request_parameters` → `{}`、`response_timestamp` → `None`。懒仲裁通道写的 manifest
  正是这种形状（只带 `transport_id`）。
- 缺失 bar 只发 **WARNING**，且网格取自 akshare 基准日期而非日历
  （[:2637](../../../src/stock_quant/data_pipeline.py#L2637)）；唯一进
  `PUBLICATION_BLOCKING_CODES` 的 `unexplained_primary_gap` 要求缺口**两侧都有 bar**
  （[suspensions.py:165-181](../../../src/stock_quant/data_model/suspensions.py#L165-L181)），
  窗口尾部的缺失日不在其射程内。
- `ingested_at` 是**已发布**的 `daily_bar` 列（[normalize.py:121](../../../src/stock_quant/data_model/normalize.py#L121)）。

## 2. 设计

### 2.1 raw 树即状态表，不另建状态文件

每个快照目录的 `manifest.json` 已自描述（`request_parameters`、
`response_timestamp`、双哈希）。覆盖状态按请求即时解析：查找 + 哈希重验，永远
从证据派生，不可能与证据脱节。**不引入持久状态表文件**（见 §2.6 被否决项）。

### 2.2 复用判定：`RawStore.resolve_reusable`

`src/stock_quant/data_sources/raw_store.py` 新增：

```python
def resolve_reusable(
    self,
    source: str,
    endpoint: str,
    request: DataRequest,
    *,
    allow_empty: bool = False,
) -> tuple[RawSnapshot, pd.DataFrame] | None:
    """The newest stored answer to exactly this request, or None."""
```

1. 按 `_root/<source>/<endpoint>/*/<request_key>/*/manifest.json` 查找候选
   （`transport_id` 通配；`_root` 即 `<项目根>/data/raw`）。
2. 返回 `(RawSnapshot, frame)` 而不是裸 frame：接线要求把**快照对象** append 进
   `raw_snapshots`，`RawSnapshotEvidence.from_snapshot` 需要 path 与 manifest（后者
   用于算 `manifest_sha256` 并取 `transport_id`）。重建 `FetchResult` 的 metadata
   取自 `snapshot.manifest["metadata"]`（见 §2.4）。
3. 防御性核对 manifest 的 `request_parameters` 与本次请求逐字段一致
   （`symbols`/`start_date`/`end_date`/`params`）。缺失或形状不对 → **判为不匹配**，
   绝不崩溃。
4. 候选按 `(response_timestamp 降序，缺失排末位，file_sha256 次级)` 排序，**只取
   最新一个**：
   - 重验通过 → 返回。
   - 重验不通过 → 返回 `None`，并由调用方记一条 issue。
   **不「跳过该候选继续找更旧的」。** 同一 `request_key` 下更旧的目录按定义是供应
   商改过的历史观测；静默回退等于把一个篡改信号降级成一次成功复用。返回 `None`
   走实时，既 fail-closed，又拿到一份可验证的新观测。
5. 空帧规则（**对原稿的修正，见 §7 决策位 A**）：默认**空帧不可复用**；只有显式
   `allow_empty=True` 的调用点（head-anchor 回溯探针）才接受空帧。理由与 ADR-009
   同源：「absent channel asserts nothing」——空响应是缺席，不是答案。
6. 重验**直接调用 `verify_evidence`**（构造 `RawSnapshotEvidence`），不另写一份
   双哈希比对：否则「同 `verify_evidence` 严格度」这句承诺会随两处代码漂移。
7. `verify_evidence` 支持四段 legacy 布局，本方法的 glob 只覆盖五段
   （`<source>/<endpoint>/<transport_id>/<request_key>/<file_sha256>`）。**legacy 树
   永不命中是有意的保守行为**，ADR 写一句，避免后人以为复用会命中老树。
8. 同一 `request_key` 下多个 `file_sha256` 目录 = 供应商改过历史：旧目录留存即
   证据（供审计/人读），选择规则见第 4 条。
9. 准入常量 `REUSABLE_CHANNELS`（`frozenset[tuple[str, str]]`）是复用的第一道门：
   `(source, endpoint)` 不在集合内直接返回 `None`。§2.3 的「复用」列即该常量的
   内容；调用点的开关只决定「这里是否尝试复用」。

### 2.3 通道准入策略（代码常量，ADR 记录）

| 通道 | 复用 | 允许空帧 | 理由 |
| --- | --- | --- | --- |
| tushare `daily`（主抓） | 可复用 | 否 | 窗口由 `last_covered_plus_1` 收窄，命中即同一问题的已获答案 |
| tushare `daily`（head-anchor 回溯探针） | 可复用 | **是** | 探针循环本来就「空块就继续回溯」，复用空块正是它的加速来源 |
| baostock `daily`（校验） | 可复用 | 否 | 校验角色，空响应会固化一次假的「缺席」 |
| akshare `index_history` | 可复用 | 否 | required 基准通道 |
| tushare `trade_cal` | 永远实时 | — | 时钟，各 1-2 次调用 |
| tushare `stock_basic` | 永远实时 | — | 宇宙定义 |
| akshare 公司行为三端点 | 永远实时 | — | 易修订通道，保持 90 天回看重拉 |
| 懒仲裁通道（tdx / adjust_factor / price_observed） | 永远实时 | — | 仲裁证据，本次不动（ADR 记为后续可议） |

`allow_empty` 是**调用点参数**而非通道属性：同一 `(tushare, daily)` 端点上的主抓
与探针取不同值。

### 2.4 接线（`data_pipeline.py`）

- `_dispatch`（[:2523](../../../src/stock_quant/data_pipeline.py#L2523)）增加
  `reuse` / `allow_empty` 两个关键字（默认关闭）；命中时跳过 `fetch_with_retry`。
- 用快照重建结果：`frame = read_parquet(...)`；
  **`metadata` 取自 `manifest["metadata"]`**（`_manifest_for` 落的脱敏副本），不要
  手搓子集——`_ingest_time`（[:2719-2725](../../../src/stock_quant/data_pipeline.py#L2719-L2725)）
  读 `response_timestamp`，`_normalize_index`（[:1823](../../../src/stock_quant/data_pipeline.py#L1823)）
  也消费 metadata，手搓会让复用行的 provenance 与实时行不一致。
- `response_timestamp` 沿用原观测时间。**语义变更要记录**：`ingested_at` 因此表示
  「该观测首次获得的时刻」而非「本轮构建时刻」，同一版本内 `ingested_at` 可能不
  均匀。这是更诚实的口径，但 ADR 要写明，否则会被后人当 bug 修掉。
- 快照对象照常 append 进本轮 `raw_snapshots`，下游 normalize / 质检 / 发布零改动。
- 四个调用点启用：`_fetch_primary_stock`、`_deepen_head_anchors`（同端点，
  `allow_empty=True`）、`_fetch_benchmarks`、`_fetch_validation_daily`。
  `_fetch_one`（公司行为用）不动。

**可用性门口径（对原稿的修正，见 §7 决策位 B）。** `_require_available` 只判配置
启用，不做网络探测；`_fetch_one`/懒通道的实时性由「不经 `_dispatch`」保证。真实
边界是：**全部符号都命中复用的一轮，对该供应商是零调用零探测**。这依然合理——
每个被复用的快照本身就是同一个请求的实时答案——但 ADR 必须这么写，不能说成
「复用受可用性门约束」。整轮 fail-stop 不变：缺快照的符号照常发起实时请求并按
现行语义失败。

### 2.5 可见性 / manifest 标注

- `dataset_build_config`（[:471](../../../src/stock_quant/data_pipeline.py#L471)）新增
  `raw_snapshot_reuse`：形状 `{source: {endpoint: {"reused": n, "fetched": m}}}`，
  排序确定、脱敏，与 `table_fetch_coverage` 同类（provenance，非运行时噪声），进
  版本哈希。
- 新增 `baseline_version`：本轮结转自的 CURRENT 版本号。**取值时点是发布前**（发布
  会推进 CURRENT）；**无基线时写显式 `null`，不要省略键**。写 `null` 而非省略是对
  `calendar_coverage` 既有约定（`validate_build_calendar_evidence` 靠缺键判 legacy）
  的**类比**，不是该函数的约束——它不读 `baseline_version`；理由是保持「缺键 = 旧
  契约」这条仓库通例的单一含义。键名对齐既有模块级常量约定，用
  `BASELINE_VERSION_KEY`。已核实这不是冗余信息：`FetchSegment.to_dict`
  （[fetch_coverage.py:58-68](../../../src/stock_quant/data_model/fetch_coverage.py#L58-L68)）
  不带任何版本指针。
- 版本身份说明：复用只让被复用条目的 `response_timestamp`/`manifest_sha256` 更
  「旧」。**不新引入确定性破坏**——同一逻辑输入本就会因时间戳产出不同版本 id，
  本设计不承诺也不暗示更强的不变量。
- 调用账本（[call_ledger.py](../../../src/stock_quant/data_model/call_ledger.py)）：
  `render_call_ledger(sources, reused=None)`，**总是写出 `reused` 段**（可为空映射），
  与 `calls`/`endpoints` 并列。
  **这会改变既有网关测试的期望形状**：
  [test_pipeline_fetch_coverage.py:329-332](../../../tests/integration/test_pipeline_fetch_coverage.py#L329-L332)
  对 `payload` 做的是**精确相等**断言（`== {name: {"calls": 0, "endpoints": {}} ...}`），
  因此该测试必须同步更新为 `{"calls": 0, "endpoints": {}, "reused": {}}`，并列入 §6
  的改动清单。这是账本契约的**扩展**（新增一段并列信息），不是语义破坏——§6 的
  「不碰既有测试语义」为此开明确例外，除此之外既有断言不动。

### 2.6 治理文档

新增 `docs/adr/015-raw-snapshot-reuse-for-eligible-channels.md`（分面：`status`/
`date`/`decision`/`affects`；正文英文，与 013/014 一致；≤400 行）。必须记录：

- 决策、§2.3 准入表（含 allow_empty 列）、§2.2 的「只取最新候选、不静默回退」，
  以及 `REUSABLE_CHANNELS` 是复用的第一道门。
- 补偿控制：复用时哈希重验、D5.4 漂移审计（**按 `classify_drift` 的独立重取口径
  描述，不要说成消费兄弟目录**）、账本/build_config 标注。
- legacy 四段布局不命中；`ingested_at` 语义变更；复用轮的零调用零探测边界。
- **期望管理**：决策 A 的代价——主抓返回空帧的符号（整窗停牌）每次重试仍发一次实时
  调用，是正确取舍而非缺陷（§4 末）。
- 被否决项：把复用当作**无法应答请求**的替代品（宕机供应商纯靠磁盘发版）、公司
  行为/日历/证券主档复用、持久状态表文件、静默回退到更旧候选。

它取代「eligible 通道每轮重问供应商」的旧非正式立场。`DECISIONS_INDEX.md` 加行
（注意索引里 `date` 是**记录日期**而非决策日期）。`docs/architecture/data-flow.md`
§1「Ingestion and raw provenance」增补一段复用语义。
`docs/architecture/invariants.md` 无需改动（原始溯源保留、内容寻址发布不动）。

**落笔后必跑**：`python tools/check_context_governance.py --root .` 与
`pytest tests/unit/test_context_governance_docs.py -v`——ADR 行数上限（400）、索引
链接可解析、frontmatter 由它们把关。

### 2.7 运行手册

`RUNBOOK.md` 增加**必选**一节（不是原稿的可选「如有一条则补」）：复用把 raw 树变成
覆盖状态的唯一来源，因此「重试轮拿不到某个窗口的新数据」时的恢复动作就是删除该
请求的证据目录，然后重跑：

```
<项目根>/data/raw/<source>/<endpoint>/<transport_id>/<request_key>/
```

**但删除是不可逆的，且会波及已发布版本的验收复验。** 已发布版本的
`dataset_manifest.json` 里 `build_config.raw_snapshots` 的行指向
`(transport_id, request_key, file_sha256)`，而验收 `_check_raw_snapshots`
（[checks.py:369-398](../../../src/stock_quant/research/acceptance/checks.py#L369-L398)）
是**逐条读盘 `verify_evidence`**；字节一旦删除，该版本此后任何验收复核都会以
`snapshot_unverifiable` 失败——若研究仍钉在那一版，正式链就断了。因此 RUNBOOK 这一
节必须写成三步，顺序不可颠倒：

1. **先查绑定**：扫描 `<项目根>/data/standardized/*/dataset_manifest.json` 的
   `build_config.raw_snapshots`，按 **`(source, endpoint, request_key)`** 匹配——
   删除单位是整个 `request_key` 目录，会一并带走该请求下的**所有** `file_sha256`
   变体，所以比对键不能带 `file_sha256`；命中的行把各自绑定的 `file_sha256` 列出，
   供操作者判断。（`RawStore.verify_evidence` 只能证明「存在且一致」，**证明不了
   「没人还在用」**，所以留档不能替代这一步。）
2. **未被绑定** → 删除目录、重跑。
3. **已被绑定** → 写明后果并给替代路径：删除即令该版本不可再验收（
   `snapshot_unverifiable`），须尽快以新数据重发布 + 重新验收；操作记录里记下这是
   有意的取舍。

本设计**不提供**「跳过复用重取一次」的开关（`cli.py` 不在范围内），所以第 3 步的
取舍是二选一，不要写成有第三种选择：要么删目录并接受该版本验收失效，要么保留目录
并让被钉住的窗口继续被复用。把证据目录复制到别处**不算**保留——`verify_evidence`
按项目根下的路径解析，副本无法让已发布版本可复验。

## 3. 已核实为正确、无需改动

- `validate_build_calendar_evidence`
  （[calendar_coverage.py:395-405](../../../src/stock_quant/data_model/calendar_coverage.py#L395-L405)）
  只拒绝 `REMOVED_FALLBACK_KEY`，新增 build_config 键不会误伤。
- `_check_raw_snapshots`（[checks.py:369-398](../../../src/stock_quant/research/acceptance/checks.py#L369-L398)）
  逐条 `verify_evidence`；被复用快照在同一内容寻址路径上必然可验，验收链路不受影响。
- `DATASET_BUILD_CONTRACT_VERSION` 保持 `1`，符合其 docstring 的 shape-marker 约定。
- §1 的日历测试语义不变；§1.1 的构造性接线边界不需要额外断言。
- ADR 编号 015 空闲（现有至 014）。

## 4. 测试

**新 `tests/unit/test_raw_reuse.py`**（原稿 + 补充）：

1. 精确命中。
2. 多摘要取最新响应，**且断言更旧候选不被返回**。
3. **最新候选被篡改 → 返回 `None`（不静默回退到更旧候选）**，调用方记 issue。
4. 篡改字节（唯一候选）→ 跳过候选人回落实时。
5. `request_parameters` 不符 → `None`；**manifest 缺 `request_parameters`（懒通道形状）
   → `None` 而非异常**。
6. `save → resolve` 往返后 frame 与原始 `FetchResult.frame` 逐值相等（parquet index
   往返）。
7. **准入常量逐对断言**：`REUSABLE_CHANNELS` 的 `(source, endpoint)` → 复用与否，
   含 `("tushare","daily")` 与 `("akshare","index_history")` 命中，
   `("tushare","trade_cal")`/`("tushare","stock_basic")`/akshare 公司行为三端点/
   `("tushare","tdx_xdxr")`/`("baostock","adjust_factor")` 不命中；`allow_empty`
   在两个调用点取不同值。
8. **空帧**：`allow_empty=False` 时返回 `None`，`True` 时返回空帧。
9. **legacy 四段布局目录不命中**。

**新 `tests/integration/test_raw_snapshot_reuse.py`**（仿
[test_pipeline_fetch_coverage.py](../../../tests/integration/test_pipeline_fetch_coverage.py)
独立成文，不进慢文件）：

1. 失败续跑：stub 在第 N 只失败 → 本轮阻断、前缀符号快照已落盘；重试轮用记录型
   stub → 已覆盖符号**零 daily 适配器调用**、其余照常；发布成功；manifest 含复用
   证据行 + `raw_snapshot_reuse` 计数正确；账本标 reused。
2. 公司行为三端点在重试轮照常发起调用（不复用）。
3. 篡改已存快照字节 → 该符号回落实时抓取且发布成功，且**新快照与旧快照在同一
   `request_key` 下并存**（两个 `file_sha256` 目录，即漂移证据留存）。
4. **等价性**（两条独立断言，不要混在一句里）：
   a. 同一请求走实时 vs 走复用，`normalize_daily` 后的 `valid` **帧**逐值相同，
      仅 `ingested_at` 一列除外；
   b. build_config 里那条 evidence **行**逐字段相同（`source`/`endpoint`/
      `request_key`/`file_sha256`/`manifest_sha256`/`transport_id`）——比的是证据
      行，不含 `ingested_at`。
5. **账本 `reused` 段与实时 `calls` 段互不污染**。

**改 `tests/integration/test_pipeline_fetch_coverage.py`**：`test_update_writes_call_ledger`
的精确相等断言补上 `"reused": {}`（见 §2.5）。

**扩 `tests/unit/test_call_ledger.py`**：`reused` 段渲染（含空映射时的形状）。

**建议补一条防回归断言**：断言准入常量 `REUSABLE_CHANNELS` 恰好等于四个调用点所用
的 `(source, endpoint)` 对——**结构化断言，不要去数 `_dispatch` 的调用点个数**（数行
号的断言一改代码就脆）。这样以后有人把懒通道接到 `_dispatch` 上、或往准入常量里加
通道，测试都会响。

**期望管理（写进 ADR，避免被当缺陷）**：决策 A 意味着「整个增量窗口都在停牌」的符号
（主抓返回空帧）每次重试仍会发一次实时调用——这是空帧不可复用的正确代价，涉及符号
数极少，且换来的是「当日未发布数据不会被空快照永久钉死」。

## 5. 验证命令

```bash
pytest tests/unit/test_raw_reuse.py tests/unit/test_call_ledger.py \
       tests/integration/test_raw_snapshot_reuse.py -q
pytest tests/integration/test_data_pipeline.py \
       tests/integration/test_pipeline_fetch_coverage.py \
       tests/unit/test_raw_store.py tests/unit/test_context_governance_docs.py -q
# ADR-015 落笔后（行数 ≤400 / 索引链接 / frontmatter 由它把关）
python tools/check_context_governance.py --root .
# 最后按 RUNBOOK 口径
conda run -n py310 python -m pytest -q
```

## 6. 文件清单

**改动：**

- `src/stock_quant/data_sources/raw_store.py`（`resolve_reusable` + 模块级 `allow_empty` 语义）
- `src/stock_quant/data_pipeline.py`（`_dispatch` 开关 + 四个调用点 + build_config 两项）
- `src/stock_quant/data_model/call_ledger.py`（`reused` 段）
- `docs/architecture/data-flow.md`（§1 复用语义）
- `docs/adr/DECISIONS_INDEX.md`（加 015 行）
- `RUNBOOK.md`（§2.7 三步恢复动作，**必改**）
- `tests/unit/test_call_ledger.py`（`reused` 段渲染）
- `tests/integration/test_pipeline_fetch_coverage.py`
  （`test_update_writes_call_ledger` 的精确相等期望补 `"reused": {}`，见 §2.5——
  这是账本契约的扩展，属**唯一**允许改动的既有断言）

**新增：**

- `docs/adr/015-raw-snapshot-reuse-for-eligible-channels.md`
- `tests/unit/test_raw_reuse.py`
- `tests/integration/test_raw_snapshot_reuse.py`

**不碰：** 项目配置、`project/` 下真实数据与脚本、未跟踪的 `tools/` 评估工作、公司
行为/日历/主档/懒通道行为、`cli.py`。既有测试语义不动，**唯一例外**是上面那条账本
期望形状（契约扩展，非语义破坏）；日历测试
（[test_data_pipeline.py:1245-1265](../../../tests/integration/test_data_pipeline.py#L1245-L1265)）
及其余既有断言保持原样。

## 7. 决策位（2026-09-25 已确认）

三处均已由 owner 定调，正文即冻结口径；实施时不得偏离，改判需新决策记录。

| 位 | 问题 | 定调 | 被否决的选项与理由 |
| --- | --- | --- | --- |
| A | 空响应算不算「已获答案」 | **不算**：主/基准/校验通道 `allow_empty=False`，仅 head-anchor 探针为 `True`。§2.2 第 5 条 | 否决「空帧也可复用」：当日行情未发布的窗口会在重试轮永久拿不到该日，等于把网络可恢复的缺陷换成只能人工删目录的死结 |
| B | 「可用性门保护复用」的表述 | **按事实改写**：`_require_available` 只判配置启用、不做网络探测；全命中轮对该供应商零调用零探测。§2.4 | 否决沿用原稿措辞：会让 ADR 引用一个不存在的机制，后人据它做错误推理 |
| C | 同 `request_key` 下最新候选重验失败时 | **返回 `None` 走实时**，不静默回退更旧候选。§2.2 第 4 条 | 否决「跳过该候选继续找」：更旧候选按定义是被供应商改过的历史观测，静默回退等于把篡改信号降级成一次成功复用 |

