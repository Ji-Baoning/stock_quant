# 时点化指数股票池与历史成分边界设计

## 目标

把正式 Research 的候选证券集合从静态工程样本或 `security_master` 全量证券，改为可核验、按信号日解析的指数历史成分股。首期正式股票池是 `csi300`（沪深 300）；模型、命名和数据管线必须可无分叉扩展至 `csi500` 等指数。

它解决幸存者偏差、指数调样前视和股票池边界不可审计的问题；不建设全市场选股池、指数复制、实时调仓服务或投资建议。

## 不可违反的原则

1. **成分事实是资格记录，不是交易指令。** 它只回答“某证券在某日是否属于该指数”；停牌、ST、涨跌停、上市天数、价格缺失、资金与持仓由因子和执行层独立处理，绝不反向写入成分表。
2. **证据驱动，而非供应商驱动。** 供应商记录只有绑定可核验的原始公告或快照才能用于正式研究；无证据记录不可用。
3. **失败要大声。** 数量异常、证据缺失、边界不确定、来源冲突或覆盖断裂必须在因子计算前让 Research 失败。`UNTRUSTED` 是停止调查的状态，而非正式结论的降级继续信号。
4. **知识时点优先于生效时点。** 即使调整在日期 `E` 生效，信号日只能使用 `announcement_date <= T` 的事实。

## 范围与命名

`universe_id` 使用小写稳定标识：`csi300`、`csi500`、`csi1000`、`sse50`、`sse180`、`szse100`。自定义池必须为 `custom_<slug>`，且定义中包含组成规则、来源和内容版本。

首期导入 `csi300` 已取得证据的完整历史，目标为 2005 年至今。数据集清单记录实际最早/最晚覆盖日，不能把目标当作已达成覆盖。中证 500 的接入只新增定义和事实数据，不复制研究主流程。

## 成分事实模型

新增不可变标准表 `universe_membership`：

```text
universe_id, symbol,
raw_effective_from, raw_effective_to,
announcement_date, status, reason,
source, source_url, snapshot_sha256, source_document_sha256
```

日期区间均为闭区间。`raw_effective_to=null` 只表示截至该数据版本尚未观察到移除，绝不表示永久有效。

`status` 取 `active|removed`；`reason` 是严格枚举：`initial_constituent`、`regular_rebalance`、`temporary_adjustment`、`delisting`、`merger_or_reorganization`、`correction`。常规调出终点为调出生效日前一天，使用 `removed/regular_rebalance`；退市移除终点为退市生效日前一天，使用 `removed/delisting`。`status` 描述该区间的已知终止状态，某日是否为成员始终由日期区间判断。

同一 `universe_id/symbol` 的事实区间不得重叠。每条记录必须绑定原始快照、原始文档和 SHA-256；`source_url` 是可审计定位符，不得含凭证。更正只能通过带 `correction` 和独立证据的新数据版本发布，不能原地改写已发布事实。

## 原始区间与实际资格区间

原始事实永不被主数据静默改写。`UniverseResolver` 基于固定事实、`security_master` 和明确版本的主数据边界产生单独的解析记录：

```text
universe_id, symbol,
raw_effective_from, raw_effective_to,
effective_from, effective_to,
boundary_adjustment_reason,
announcement_date, membership_snapshot_sha256
```

解析规则：指数纳入早于上市，实际起点取上市日，理由 `before_listing`；调出晚于退市，实际终点取最后可交易日，理由 `after_delisting`；IPO 解禁等限制不改变资格区间，留给交易层；无法证明最后可交易日则正式 Research 拒绝；相交为空时保留事实、标记不可用并使覆盖验收失败。`security_master.delist_date` 仅在来源明确语义为最后可交易日时可使用；只有终止上市公告日时不得猜测。

## 信号日与过滤顺序

信号日 `T` 的资格集合仅包含：`effective_from <= T <= effective_to`（空终点视为数据版本内开放）、`announcement_date <= T` 且 `universe_id` 等于冻结定义的成员。初始成分可使用历史基线公告，但它必须在研究窗口首次使用前可证实。

每个信号日严格按此顺序处理：

```text
全市场因子数据
  → 时点化指数成分资格
  → 因子缺失值、质量和最小历史窗口过滤
  → 交易条件过滤（停牌、ST、上市天数、涨跌停等）
  → 最终横截面、排序与组合目标
```

后置过滤不得带回非成员，也不得改变事实。调出只禁止调出后产生新开仓；既有持仓的退出由组合/执行层决定。退市后的价格和公司行为处置遵循既有门禁。

## 定义、冻结与产物

`UniverseDefinition` 包含 `universe_id`、规则版本、成分表内容哈希、覆盖范围和证据摘要；其规范 JSON 的 SHA-256 即 `universe_version`。`CURRENT` 在数据集和真实数据验收固定后解析为已发布定义的显式版本，进入实验 ID。定义缺失、损坏或与指定数据集的成分表哈希不符都直接失败。

run manifest、experiment manifest、metrics 与 HTML 报告记录：`universe_id`、`universe_version`、事实表哈希、规则版本、覆盖范围，以及每个信号日的成员数量和成员快照哈希。相同冻结输入重跑必须得到同一股票池序列和实验 ID。

## 摄取、验收和错误语义

优先使用中证指数公司可核验公告/下载快照作为最终证据。开源 `index-constitution` 类项目可用于采集或交叉核对，但不能作为无官方证据时的唯一正式依据。首次批量导入全量已获证据；每次半年度调样或临时调整仅追加新事实。

真实数据验收注册表增加自动检查，验证 schema、事实/文档哈希、区间无重叠、主数据相交、公告前视、覆盖连续性和成分数量。应对每个交易日或成分稳定区间验证 `csi300` 正常为 300 只；官方允许的临时例外必须带规则版本与例外证据。任一失败阻断 Research；Engineering 只能保留 UNTRUSTED 诊断，不能发布 ACCEPTED 结论。

## 组件边界

- `data_model/universe_membership.py`：事实契约、枚举、内容哈希和区间相交。
- `schemas.py`、`dataset.py`：Arrow schema/表注册。
- `data_pipeline.py`：已验证事实与来源证据进入不可变数据集。
- `research/universe.py`：固定定义与固定数据集的信号日成员解析；不含因子或执行逻辑。
- `research/acceptance/checks.py`：纯读取验收；不联网、不修改数据。
- `research/runner.py`：定义冻结、preflight、审计持久化；不自行解析原始来源。

## 测试与验收

单元测试覆盖区间闭合、常规调出/退市区分、哈希稳定、公告门禁、主数据相交、未知终点拒绝和成员快照稳定。集成测试覆盖发布/读取、缺证据或数量异常在因子前失败、调入调出边界、稳定重跑、`csi500` 合成定义复用同一流程，以及 `security_master` 全量证券不再直接构成因子池。

任一正式实验必须可从产物定位冻结定义和逐日成员快照，再追溯至原始证据与证券主数据边界；篡改任何成分、日期或证据哈希后，新 Research 必须拒绝。
