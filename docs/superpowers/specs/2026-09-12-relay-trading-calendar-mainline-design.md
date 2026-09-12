# Relay 交易日历主线化设计

## 目标

将 jiaoch relay 已验证可用的 Tushare `trade_cal` 纳入每一次
`data update`。日历与本轮行情、企业行为在同一原子发布中绑定，且日历
请求、解析或连续性校验失败时不得推进 `CURRENT`。

本设计解决 bootstrap 的工作日近似日历不能自行向历史扩展的问题，并保持
bootstrap 能在无网络环境下创建首个可更新的数据集。

## 非目标

- 不从当前自然日、当前自然日前若干日或其他时间启发式推断更新终点。
- 不把 raw store 当作日历缓存；失败后的后续运行不得复用旧响应。
- 不改变既有 Tushare transport 政策：relay 为正常发布路径，official 仅可
  在既有 break-glass 条件满足时显式使用，绝不自动回退。
- 不在本次把 bootstrap 改成强制联网操作。

## 术语

- **有效窗口**：`data update` 最终采用的闭区间 `[start, end]`。
- **物化窗口**：与有效窗口相同；只有该区间的日历事实可以替换标准化表。
- **校验 halo**：供应商请求的 `[start - 1 天, end + 1 天]`。halo 只用于
  验证边界，绝不扩大物化窗口。
- **coverage span**：manifest 中连续的自然日区间及其日历来源证据。
- **全历史验收起点**：发布时从所有已启用 universe 定义计算出的最早
  `coverage_start`。它不是 bootstrap 的 `project.start_date`；前者是研究可用
  历史的边界，后者允许包含为启动管线而保留的更早种子日历。

## 更新流程

1. 读取当前数据集及其日历 coverage。bootstrap 种子数据集也必须带有一条
   `bootstrap_seed` coverage span。
2. 解析 `end`：
   - 显式 `--end` 原样使用；
   - 未传 `--end` 时，只能使用已发布 `trading_calendar.calendar_date` 的
     最大值；
   - 已发布日历为空时产生 FATAL，要求操作员传入 `--end`。
3. 计算有效窗口，并向 relay 的 `trade_cal` 分别请求 SSE 与 SZSE 的校验
   halo。每一响应均按普通数据源响应保存到 raw store。
4. 验证两市原始响应；构建候选日历和 coverage spans，并把本次计算的
   `full_history_acceptance_start` 及
   `universe_coverage_definition_hashes` 写入 manifest。
5. 只有日历步骤成功，才继续本轮日线、基准和企业行为抓取。所有表与
   `calendar_coverage` 一起一次性发布。

首次 bootstrap 仍可生成工作日近似种子；带显式全历史窗口的首次成功
`data update` 会用 relay 事实覆盖该窗口。若窗口外仍残留种子，必须在
manifest 中明确可见；操作员要消除它，必须提交覆盖全历史的更新。

## 数据源接口与原始证据

`TushareSource` 新增 `trade_cal` endpoint，参数为 `exchange`、`start_date`
和 `end_date`，不接受 symbol。它通过当前已解析的 Tushare transport 调用
SDK，并和其他 endpoint 一样记录 source、supplier endpoint、transport id、
request parameters、时间戳和内容哈希。

每轮日历刷新必有两份独立 raw snapshot：SSE 与 SZSE。请求参数是 halo，
而 manifest coverage 的 `start_date` / `end_date` 是物化窗口；两者不得混淆。

未发布的运行也保留已成功写入 raw store 的 `trade_cal` 快照，以便审计失败。
管线每次都必须重新调用供应商；不得搜索、选择或回放同窗口的旧 raw snapshot。

## 原始响应校验

对 SSE、SZSE 各自要求：

- 必有 `cal_date`、`is_open`、`pretrade_date`；日期可解析。
- halo 中每一个自然日恰有一行，不允许缺日或重复。
- `is_open` 只能是 `0` 或 `1`。
- 两市的日期集合都是完整 halo 后，才逐个 halo 自然日比较 `is_open` 与
  `pretrade_date`；任何差异表示本项目不能安全产出单一交易日历，必须阻断。

校验顺序固定为“各市 schema → 各市 halo 日期集合/行数/唯一性 → 各市字段值
合法性 → 两市逐日比较”。因此某一市缺日时报告缺日，而不会含混报告为两市
不一致。

标准化 `trading_calendar` 继续只存开市日。候选表先删除当前表中所有
`calendar_date ∈ [start, end]` 的行，无论这些行原本来自 relay 还是
`bootstrap_seed`；再插入供应商在物化窗口报告的开市日。插入后必须按日期排序
并验证唯一性，窗口外行保持不变。

## 跨窗口连续性

候选合并表必须通过以下发布前校验：对有效窗口内每个原始日历行，以及
`end + 1 天` 的 halo 行，`pretrade_date` 必须等于**合并后的完整候选表**中
该日期前最近的开市日；它不是只在本轮供应商响应内查找。休市日同样比较其
`pretrade_date`，即该休市日前最近的开市日；`end + 1 天` 无论开市或休市都
按同一规则比较。

纯校验函数接受显式 `coverage_start` 参数。只有当 `pretrade_date` 严格早于
该参数、且候选表中没有更早覆盖行时，才返回明确的 `allowed_pre_coverage`
边界结果；其他找不到前序开市日的情况都是断链，不得靠“找不到就放过”。

该规则同时防止两类错误：窗口内删掉交易日、以及窗口外相邻日仍通过
`pretrade_date` 引用被删交易日。任一不连续均为 FATAL，表和 manifest 均不
发布。

## Manifest 覆盖证据

`build_config.calendar_coverage` 是按日期排序、无重叠、无空洞、可合并的 span
列表。每一项至少包含：

- `start_date`、`end_date`（自然日闭区间）；
- `source`：`bootstrap_seed`、`tushare_relay` 或
  `tushare_official_break_glass`；
- `snapshot_sha256s`：relay / break-glass span 按 `SSE`、`SZSE` 分组的 raw
  snapshot 内容哈希数组；种子 span 没有该字段。

更新时先将既有 spans 在物化窗口处分割，再以一个 relay span 替换窗口；
窗口外 span 原样保留。相邻、同来源且日期连续的非种子 spans 必须合并，
并按 exchange 对 `snapshot_sha256s` 做去重排序后的并集；不得为了合并而
丢弃旧快照证据。`bootstrap_seed` 与非种子 span 永不合并。替换和合并后重新
校验排序、无重叠、无空洞。新版本 manifest 必须能说明每一日历区间的来源。

`data validate` 和接受链校验 span 的格式、排序、无重叠及对标准化日历范围
的覆盖。`data update` 将当时计算的 `full_history_acceptance_start` 与
`universe_coverage_definition_hashes` 写入 manifest；`data validate` 和接受链
只使用这份版本绑定的判据，不因后来配置变化追溯改变旧版本结论。全历史验收
要求从该起点到已发布日历最大日期的 coverage 中不存在 `bootstrap_seed`。
因此，bootstrap 在该起点之前保留的种子不会伪装成 relay 事实，也不会阻断从
首个研究可用日期开始的全历史验收。

## 失败语义

以下任一情况均产生 FATAL、保留旧 `CURRENT`，且本轮 raw 已保存的响应保留：

- transport / endpoint 请求失败；
- 原始 schema、自然日覆盖、日期唯一性或 `is_open` 校验失败；
- SSE 与 SZSE 不一致；
- 跨窗口 `pretrade_date` 连续性失败；
- 缺省 end 时没有已发布日历。

失败运行不得写标准化临时日历、不得产生部分 coverage span、不得自动使用
旧 raw 响应完成下一次运行。

## 测试要求

单元测试覆盖 endpoint 参数和原始 schema 解析，并以离线 fixture 覆盖：

- SSE/SZSE 一致的正常窗口；
- 两市差异、缺日、重复、非法 `is_open`；
- 窗口开始和结束的 `pretrade_date` 边界断裂；
- 窗口替换与窗口外保留；
- coverage span 分割、合并与 bootstrap 标记；
- halo 内两市逐日比较的顺序，以及同源 span 合并后保留两轮快照哈希；
- 发布时固定全历史验收起点，随后变更 universe 定义也不改变旧版本验证；
- 无 `--end` 的最大已发布日历规则、空日历要求显式 `--end`；
- 日历失败时 raw 保留而 `CURRENT` 不变，重跑仍实际发起新的供应商请求。

集成测试以 stub Tushare transport 跑完整 `data update`，断言 manifest 的
raw evidence 与 `calendar_coverage`。真实 relay 只做独立小窗口 smoke probe，
不作为单元或集成测试前提。
