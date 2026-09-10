# 阶段一 完整使用流程（RUNBOOK）

面向**本工程**（30 只固定样本 + 沪深300/中证500）的端到端操作顺序。代码在
`~/work/program/stock`，工程根（configs/ + data/）
在 `~/work/program/stock/project`；CLI 已 editable 装入 conda env **py310**。

> 与运维验收清单的关系：这里是"怎么做"；`docs/operations/phase-one-validation.md`
> 是"每条命令要核对什么、合法诊断结论长什么样"（checklist §4、记录模板 §6）。

## 阶段 0 · 准备（一次性）

```bash
conda activate py310
# 包安装（已做）：
python -m pip install -e ~/work/program/stock
cd ~/work/program/stock/project
cp -r ~/work/program/stock/configs .   # 若无 configs
export TUSHARE_TOKEN='<你的轮换后token>'      # 只进环境变量，绝不入库
```

**必查 configs/project.yml**：
- `start_date`/`end_date` = 你想拉的数据区间。
- `benchmark_symbols` 只放**指数** `000300.SH`、`000905.SH`。放个股会让 data update
  用 akshare 的**指数**接口去取个股 → 取数失败 → 门禁 BLOCK。
- `publication_time: "15:00"`（已配）。

## 阶段 1 · 离线自检（无网络/Token）

```bash
cd ~/work/program/stock
conda run -n py310 python -m ruff check src tests
conda run -n py310 python -m pytest -q        # external/smoke 默认剔除；全量通过
```

## 阶段 2 · 引导基线数据集（一次性；让 data update 可启动）

```bash
cd ~/work/program/stock/project
python -m stock_quant data bootstrap --root .   # 发布 security_master + 日历 + 空日线/公司行为/复权表
# 等价直调脚本（与上面 CLI 相同效果，额外打印 benchmark_symbols 提示）：
# python bootstrap_seed.py --root .
# 官方交易日历（可选，替代周历近似）；CLI 与直调脚本都接受：
# python -m stock_quant data bootstrap --root . --calendar-csv official_calendar.txt
# python bootstrap_seed.py --root . --calendar-csv official_calendar.txt
python -m stock_quant data validate --root .   # 应 PASS（空数据集自检）
```

## 阶段 3 · 实时小窗口验收（先验证联网/契约，再上正式量）

```bash
cd ~/work/program/stock
conda run -n py310 python -m pytest -m external -v    # 三供应商原始帧契约
conda run -n py310 python -m pytest -m smoke -v       # 600000.SH 小窗口真实 update
```

## 阶段 4 · 正式拉数 + 质量门禁

**建议先小窗口试一次**（半天拉得动、问题暴露快），确认门禁能过再铺全区间：

```bash
cd ~/work/program/stock/project
python -m stock_quant data update --start 2024-01-01 --end 2024-03-31 --root .
python -m stock_quant data validate --root .
# PASS  → data/dataset/<版本哈希>/ 新增不可变版本
# BLOCK → 数据集不变；看上方 redacted 原因行（ERROR/FATAL 计数、source 状态）
```

- 门禁与策略无关：非正价格/schema 冲突/必需源不可用 → BLOCK；跨源价差、公司行为
  隔离断点只记录不阻断（设计 §13.5）。每次成功更新都会重建 `adjusted_bar`
  （`adjustment=internal_total_return_v1`）并连同 `corporate_action_quarantine`
  一起发布；不可信公司行为不会阻断发布，但会在 `adjusted_bar` 上留下 ERROR 断点。
- **`data update` 必须先刷新 tushare `stock_basic` 全市场快照**：该必需步骤刷新
  `security_master` 的上市事实并发布 `security_master_coverage`（每标的一行 = 研究冻结
  的证据）；拉取失败或快照缺某股票池标的 → 阻断发布。
- 全区间再跑：`--start 2021-01-01 --end 2026-08-30`（= configs 范围；也可不给 --end，
  由"最新完整交易日 + 发布时间 15:00"规则自动发现）。

## 阶段 5 · 指数成分（csi300）证据导入与定义冻结（正式研究的前置）

正式 `research run` 不再使用 `security_master` 全量证券：因子在每个信号日先按
冻结的 `csi300` 时点成分过滤。这条链的每一步都是**显式**的，任何一步证据不足即停：

1. **原始来源**：取得中证指数公司官方（或可交叉核对的官方转载）成分公告，把
   **原始快照文件**存进 `data/raw/csi/...`（不入库），记录文件 SHA-256。
   `source_url` 必须是无凭证、可审计的 http(s) 定位符。
2. **导入（离线，无网络）**：CLI 与 `project/refresh_index_membership.py`
   脚本共用同一套转换逻辑；快照/文档哈希是**必填参数**，缺任一个都是 usage error：

   ```bash
   python -m stock_quant data index-membership prepare \
       --universe-id csi300 \
       --input data/raw/csi/members_2005.csv \
       --snapshot-sha256 <64位HEX> --source-document-sha256 <64位HEX> \
       --source csi_index_announcement \
       --source-url https://www.csindex.com.cn/announcement.pdf \
       --effective-date 2005-01-04 --announcement-date 2005-01-04 \
       --output data/membership/universe_membership.parquet
   # 输出 membership_table_sha256=... → 定义文件要钉住的内容哈希
   ```

3. **数据集发布**：把准备好的帧作为 `universe_membership` 表并入下一个数据集
   版本一次性发布（与 `refresh_corporate_action_coverage.py` 相同的
   "读全表→并新表→重发布" 技法，见
   `tests/integration/test_data_pipeline.py::_publish_baseline_with_membership`）。
   此后每次 `data update` 原样携带该表；`data validate` 会复审它，篡改证据 =
   FATAL。
4. **定义哈希**：用**真实值**填写 `configs/universes/csi300.yml`：已发布事实表
   内容哈希、证据摘要哈希、数据集**实际**覆盖区间（不是目标区间）。仓库里的该
   文件是占位模板，正式运行必然拒绝；只有持有真实证据的操作者能把它变成可用定义。
5. **验收与研究**：`research run` 在任何因子计算之前对冻结定义做
   `index_membership_evidence` 预检（证据、边界、覆盖、成分数量），通过后把
   `universe_version`（定义内容哈希）冻进实验身份，并把每个信号日的成员快照
   哈希持久化进产物。

**没有绕过开关**。证据缺失、成分数量不对（`csi300` 每个交易日应为 300 只，除非
存在官方例外证据）、退市边界不确定（无法证明最后可交易日）——这三种情况一律
**停止工作**：运行以 `universe_acceptance` 失败告终，只在 `data/runs/` 留下
redacted 的 `universe_preflight.json`（仅含状态/失败阶段/错误码），不产出任何
因子，绝不回退到全量 master 标的；更正必须作为带证据的新事实版本发布，不得原地
改写。调出指数只禁止之后的新开仓，既有持仓的退出仍由组合/执行层决定。

## 阶段 5b · 真实数据验收（正式研究的前置门禁）

`research run` 只消费**已通过真实数据验收**的数据集版本（规则版本
`real-data-v1`）。验收是**数据供给侧**的质量门禁，与“策略验收/绩效可信度”
是两个独立决定：工程模式即使引用有效 ACCEPTED 记录，评价仍恒为 UNTRUSTED。

```bash
cd ~/work/program/stock/project
# 1) 准备清单：自动检查当场离线重跑；每条人工项初始为显式 FAIL
python -m stock_quant data acceptance prepare \
  --version <数据版本哈希> --operator <操作者ID> \
  --output acceptance-<数据版本哈希>.yml --root .

# 2) 操作者手工编辑清单：把每条人工项（交易所日历抽查、来源行数抽查、
#    缺失原因抽查、跨源价格抽查、公司行为抽查、基准抽查、交易规则生效日、
#    证券主数据抽查、秘密扫描）改为 PASS，并附证据：
#    - local 证据 = 项目内的相对路径 + sha256 + 摘要（绝不写绝对路径，
#      越出项目根/软链跳板会被拒）；
#    - external 证据永不抓取：sha256 只钉住清单里的 UTF-8 摘要文本。

# 3) 发布：发布方不信任准备结果，自动检查与全部哈希当场重算。
#    全部通过 → decision=ACCEPTED，退出码 0；
#    任一失败 → 先原子落盘 REJECTED 记录（不可变留档），打印
#    acceptance_id 与逐条 reason=，再以退出码 1 结束。
python -m stock_quant data acceptance publish \
  --checklist acceptance-<数据版本哈希>.yml --root .

# 4) 查询该版本的验收历史（按时间升序，附结论与 reason= 行）；
#    无记录打印 UNACCEPTED；损坏记录打印 corrupt acceptance_id=<id>，
#    其余记录照常列出，但命令以非零退出——损坏绝不解释为通过。
python -m stock_quant data acceptance show --version <数据版本哈希> --root .
```

规格与冻结语义：实验规格的 `data_acceptance_id` 可写占位符
`CURRENT_ACCEPTED`（运行时解析为该数据版本最新的有效 ACCEPTED 记录，并**每次
运行重新复核**全部绑定哈希）或显式 64 位十六进制 id（只钉住该条记录）；冻结
规格回写解析后的具体 id，绝不保留占位符。REJECTED 记录永久留档但**永不可被
研究选中**；没有有效 ACCEPTED 记录时，正式研究在任何因子/组合/回测计算前
失败，只留 `data/runs/preflight_acceptance_<uuid>/` 的 FAILED preflight。

> 注意：验收**机制已实现并离线测试覆盖**，但截至本文档更新尚无操作者在真实
> 数据上执行过完整验收流程。人工核验的逐条要点见
> `docs/operations/phase-one-validation.md` §4/§7。


## 阶段 6 · 正式研究（唯一发布者）

```bash
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root .
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root .   # 复跑验证可复现
# 两次 experiment_id 相同 → data/experiments/<id>/
```

`momentum_60d.yml` 声明 `execution_pipeline: walk_forward_oos_v1`：正式研究走
**固定日历 Walk-Forward 样本外稳定性** 管线。`date_range` 是请求的 OOS 评估
范围（不含预热）：范围被切成完整的 1–12 月非重叠年度 fold，每年 1 月 1 日锚定；
每个 fold 独立账户、独立持仓、相同的固定初始资金，三年日历预热（≥756 个确认
交易日 + 首个 OOS 日前 60 个稳定历史日）只供因子历史，绝不产生订单或收益。
首尾不成完整年度的日期在 schedule 中记录为 `not_evaluated_boundary`（记录但
不评估，不是 skipped）。运行输出打印 `research_status=` 与
`stability_conclusion=`：FAILED 非零退出且结论恒为 null（绝不降级为
INCONCLUSIVE）；COMPLETED 的 STABLE/UNSTABLE/INCONCLUSIVE 均为零退出并保留
确切标签。`stability_report.json` 必含 `stability_policy_hash`；逐预锁定成本
情景独立判定，最终 STABLE 是全部情景的合取——没有“主情景”择优。

产物（发布于 `data/experiments/<id>/`）：`fold_schedule.json`（运行前写入并
哈希，之后绝不修改）、`fold_outcomes.json`（按 fold_id 与 schedule 哈希绑定
的独立结果账本，失败 fold 永久保留）、`walk_forward_manifest.json`（固定两个
哈希与三类快照哈希）、`stability_report.json`（判定阈值/理由/逐情景聚合/
逐 fold 指标）与逐 fold 的 `folds/<fold_id>/` 资产集（fold_manifest/signals/
orders/fills/equity/daily_returns/metrics）。逐 fold 最大回撤只用该 fold 自己
的 `net_equity_after_cost` 逐日 mark-to-market 计算；**跨 fold 拼接的最大回撤
与 Calmar 被政策禁止**，任何产物都不存在该字段。

REJECTED 实验也会完整发布并留原因；跑挂只留 `data/runs/` 审计、不发布半成品。
运行前置：该数据版本必须持有有效 ACCEPTED 验收记录（阶段 5），否则在计算前
即失败。验收身份写入 run manifest、`metrics.json["data_acceptance"]`、实验
manifest 与 HTML 报告的**真实数据验收**小节；无验收的 run（工程模式）在报告中
显示 UNVERIFIED，绝不推断为 ACCEPTED。

正式研究使用 **`momentum_60d` v2**：它只消费不可变 `adjusted_bar` 表
（`adjustment=internal_total_return_v1`，由未复权收盘与已核验的现金分红/送股/转增
事件导出）。在 `adjusted_bar` 发布之前创建的数据集仍可审计，但不能运行 v2 研究
实验——先跑一次完整 `data update` 发布兼容数据集。动量 v2 的研究输入只来自
`adjusted_bar`，没有任何回退到未复权收盘的路径；下单、成交、涨跌停判断与账户
估值继续使用未复权 `daily_bar` 价格。

正式研究受**两道预检门禁**约束，均在回测前生效、均无降级开关：

- **公司行为可信门禁**：执行窗口内每只股票池标的都必须持有 `VERIFIED` /
  `VERIFIED_EMPTY` 的公司行为覆盖证据，否则实验在回测前即被 REJECTED 并打印
  不可信原因（如 `SOURCE_NOT_REQUESTED` / `SOURCE_FETCH_FAILED`）。
- **指数成分证据门禁（universe_acceptance）**：见阶段 5 —— 定义缺失、哈希与
  数据集成分表不符、证据/数量/边界/覆盖异常都在因子之前失败并留下 redacted
  预检清单。

`research run` **没有任何降级绕过开关**（`--help` 里无 `--engineering`）——正式
结论永远不降低自己的证据标准。遗留的单窗口管线只保留给
`execution_pipeline: engineering_single_window` 的显式工程政策（`backtest
momentum_60d` 调试诊断），它不能发布正式稳定性结论。

核验覆盖证据（人工抽查）：
- 证据表 = 数据集内的 `corporate_action_coverage` 表，位于
  `data/standardized/<dataset_version>/corporate_action_coverage.parquet`
  （`data/standardized/CURRENT` 指向当前版本，`manifest.json` 列出全部表）；
  每行含 symbol、window_start/end、status（`VERIFIED`/`VERIFIED_EMPTY`/
  `UNTRUSTED`）、reason、sources、snapshot_hashes、checked_at。
- 成分证据表 = 同目录的 `universe_membership.parquet`；冻结实验本身可复核：
  `data/experiments/<id>/metrics.json` 的 `metrics.meta.universe` 记录
  {universe_id、universe_version、membership_table_sha256、coverage}，
  `universe_daily_snapshots` 记录每个信号日的成员快照哈希。
- 或核对冻结实验本身：`data/experiments/<experiment_id>/metrics.json` 的
  `metrics.corporate_action_trust` 记录 {mode、dataset_version、window_start/end、
  trusted、reasons:[{code,symbol}]}。

## 阶段 7 · 报表 + 人工核查

```bash
python -m stock_quant report build --root .        # 最新实验 HTML + 当前数据质量 HTML
# 打开 data/reports/*.html 核对：来源/局限、三成本场景、两基准、因子价格口径、
# 真实数据验收（结论/规则/验收 ID/操作者/时间）、免责声明
```

按 `docs/operations/phase-one-validation.md` §4 收尾：源行数、跨源最大差、复权抽查
（adjusted_bar 口径）、公司行为冲突、密钥扫描、无盈利宣称、单次 run 计时（<600s）。

## 阶段 7b · 缓冲式风险加权组合的运行与审计（buffered_risk_weighted）

`configs/experiments/momentum_60d.yml` 的 `portfolio_rule` 已选择
`buffered_risk_weighted`（首期唯一一组预注册参数）。它只改变组合构建与账户
对账：`momentum_60d` 因子与周频调仓不变。本节是操作者核对已发布产物的程序。

### 1. 识别 portfolio_rule_version

```bash
python - <<'PY'
import json, pathlib
manifest = json.loads(pathlib.Path(
    "data/experiments/<experiment_id>/folds/<fold_id>/fold_manifest.json"
).read_text())
print(manifest["portfolio_rule"])
# {"name": "buffered_risk_weighted", "portfolio_rule_version": "<64-hex>", "policy": {...}}
PY
```

`portfolio_rule_version` 是规则参数规范 JSON 的 SHA-256（不是手写标签）。
核对它等于任意 `portfolio_construction.parquet` 行的 `portfolio_rule_version`
列，也等于 `metrics.json` 顶层 `meta.spec.portfolio_rule`（与
`experiment_spec.yml` 的 `portfolio_rule` 同一规范内容）的规范 JSON 哈希；
同一规范内容还进入策略快照的 `parameters_hash` 与实验 ID。任何参数修改都是
新的实验身份，必须在运行前重新预注册；**看过 fold 结果之后不允许再改参数**。

### 2. 核对 60/40 风险计数

```python
import pandas as pd
frame = pd.read_parquet(
    "data/experiments/<experiment_id>/folds/<fold_id>/portfolio_construction.parquet"
)
# 每行：window_start/window_end（60 个确认交易日的窗口起止）、
# real_close_observations（真实有效收盘数，有效候选须 >= 40）、
# suspension_carry_days（可信停牌前值日，零收益且不计入 40）
signal = frame[frame.signal_date == frame.signal_date.iloc[0]]
print(signal[["symbol", "window_start", "window_end",
              "real_close_observations", "suspension_carry_days",
              "risk_is_valid", "risk_invalid_reason"]])
```

`risk_invalid_reason` 只会是 `untrusted_missing_observation`（不明缺价、质量
ERROR、无前值）或 `insufficient_real_close_observations`（窗口内真实收盘不足
40）。风险无效只淘汰该候选并留原因，不是 fold 失败。

### 3. 检查保留/新入/退出成员

```python
print(signal[["symbol", "previous_target_member", "raw_momentum_rank",
              "risk_eligible_rank", "member_status", "member_reason"]])
```

`member_status` 取值固定为 `retained|entered|exited|not_selected|risk_invalid`。
保留成员的 `risk_eligible_rank <= 15`，新入成员必须来自 `risk_eligible_rank
<= 10`，任何成员数不超过 10。fold 的第一个信号日 `previous_target_member`
全为 False（fold 边界状态完全重置）；fold 内上一期状态只继承冻结目标成员代码。

### 4. 重算封顶权重与现金残余

```python
from decimal import Decimal
vol = {row.symbol: Decimal(str(row.applied_annualized_volatility))
       for row in signal.itertuples() if row.risk_is_valid}
scores = {s: Decimal(1) / max(v, Decimal("0.10")) for s, v in vol.items()}
total = sum(scores.values())
exposure = min(Decimal("1.00"), Decimal(len(scores)) * Decimal("0.15"))
raw = {s: exposure * w / total for s, w in scores.items()}   # 封顶前
# 超过 0.15 的固定为 0.15，其余按 score 比例重分；再向下量化到 1e-12
weights = signal[signal.target_weight > 0].set_index("symbol").target_weight
cash = signal.cash_weight.iloc[0]
assert sum(Decimal(str(w)) for w in weights) + Decimal(str(cash)) == Decimal("1.00")
```

成员数不足 7 只时目标暴露为 `成员数 × 0.15`，差额留作现金；绝不提高上限、
绝不使用杠杆。

### 5. 比较统一目标与各成本情景

```python
common = frame[["signal_date", "symbol", "target_weight"]].drop_duplicates()
for scenario in ("zero_cost", "commission_tax", "full_cost"):
    decisions = pd.read_parquet(
        f"data/experiments/<experiment_id>/folds/<fold_id>/backtest/"
        f"{scenario}/rebalance_decisions.parquet"
    )
    ordered = decisions[decisions.order_quantity > 0]
    assert set(ordered.symbol) <= set(common[common.target_weight > 0].symbol)
```

三个情景共享完全相同的成员与理论权重；订单、成交、现金与带宽决策可以分化。
下一期的成员选择只来自统一目标成员，任何情景的拒单都不能改变它。

### 6. 区分四类“没有成交/没有入选”

- `within_rebalance_band`：继续持有且权重差绝对值 < 0.02（恰好 0.02 要调仓），
  组合决策抑制，记录于 `rebalance_decisions.parquet`；
- `below_one_lot`：数量差不足一手（100 股），组合决策抑制，同样留档；
- 执行拒单：涨跌停/停牌/现金不足等执行层结果，只在 `rejections.parquet`
  与逐 fold `reject_rate` 中，属于策略结果而非系统失败；
- 风险无效（`risk_invalid` / `risk_invalid_reason`）：候选因 60/40 或数据可信
  问题被排除在构建层，与上面三者互不相干。

带宽与手数抑制发生在下单之前，**不是执行拒单**。报告的“缓冲式组合构建审计”
小节分别给出成员变化换手、连续持仓再平衡换手、带宽抑制金额与手数抑制金额。

### 7. 纪律

首期只有这一组预注册参数；不在 fold 之间调整、不依据结果选择参数、成本情景
或调仓频率。要改任何参数，先写新的预注册规格（新实验身份），再跑新的运行。

## 阶段 8 · 一次性样本外挑战（strategy challenge，不可撤销）

样本外证据会被任何一次正式比较消耗，因此挑战是**预注册、一次性**的动作。
代码在 `src/stock_quant/research/strategy_challenge/`（models / registry /
compare / service / reporting），CLI 命令为
`python -m stock_quant research challenge --declaration <strategy_challenge.json>`。
**在做第 5 步之前不要打开任何挑战者的结果产物**（`fold_outcomes.json`、
`stability_report.json`、`metrics.json` 等）——先看结果后补声明，就是数据窥视。

### 1. 选一个未消费的 fold 日历

消费键是 `strategy_family + fold_schedule_hash`。先查注册表确认该组合从未被
消费过：

```python
from stock_quant.research.strategy_challenge.registry import HoldoutRegistry
print(HoldoutRegistry("<PROJECT_ROOT>").lookup("momentum_60d", "<fold_schedule_hash>"))
# None = 未消费；HoldoutConsumption = 已被 <challenge_id> 消费，换日历或等新历史
```

股票池版本**不参与**消费键：换 universe 版本不会产生新的"未消费"槽位。

### 2. 取得两侧身份并发布声明

- 基线：等权（`top_n_equal_weight`）正式实验的 `experiment_id`
  （`research run` 输出或 `data/experiments/` 目录）。
- 挑战者：缓冲式（`buffered_risk_weighted`）已发布实验清单中的
  `strategy_snapshot_sha256`（64 位十六进制）。
- fold 日历：`walk_forward_manifest.json` 的 `fold_schedule_sha256`。
- 股票池四元组（`universe_id`/`universe_version`/`membership_table_sha256`/
  `evidence_summary_sha256`）从挑战者 `metrics.json` 的 `meta.universe`
  **原样复制**，不得手抄改写；比较政策用默认冻结值
  （`StrategyComparisonPolicy`，`strategy-comparison-v1`），其
  `comparison_policy_hash` 由代码重算校验。
- `declared_before_run_at` 必须是 UTC 时刻。基线与挑战者必须在数据集/数据环境
  快照、股票池身份、fold 日历、初始资金、因子信号（不含组合规则的策略输入哈希）、
  调仓频率与成本情景顺序上完全一致；**唯一允许不同的只有组合构建规则**。

### 3. 一次性运行

```bash
python -m stock_quant research challenge --declaration strategy_challenge.json --root .
# challenge_id=<64-hex>
# challenge_status=COMPLETED|FAILED
# challenge_conclusion=PROMOTED|REJECTED|INCONCLUSIVE_RESEARCH_ONLY|none
# COMPLETED 退出码 0（保留确切标签）；FAILED 退出码非 0。
```

执行顺序由服务固定并被事件审计：原子发布声明 → 原子消费 holdout →
**之后**才读取两侧已发布实验。消费记录不可改写：崩溃、FAILED、REJECTED、
INCONCLUSIVE 都已经消费该 holdout，没有任何回滚。

### 4. 先核对消费，再看结果

```bash
ls data/strategy_challenges/declarations/<challenge_id>.json      # 声明已发布
cat data/strategy_challenges/consumptions/<challenge_id>.json     # 消费记录（完整股票池身份+声明哈希）
python -c "import pandas as pd; print(pd.read_parquet('data/strategy_challenges/holdout_registry.parquet'))"
```

然后才打开 `data/strategy_challenges/results/<challenge_id>/` 下的
`strategy_comparison.json` 与 `strategy_comparison_report.html`，按
`docs/operations/phase-one-validation.md` §10 审计清单核对。

### 5. 结论解读与恢复

- `PROMOTED`：≥5 个未消费已执行 fold、无合法市场级跳过、挑战者 STABLE，
  且**每个**预锁定成本情景通过**每条**阈值（无主情景）。
- `REJECTED`：研究完整但任一阈值失败；全部失败项在结果与报告中可见，
  不自动生成新参数。
- `INCONCLUSIVE_RESEARCH_ONLY`：流程有效但证据不足（fold 不足、合法跳过、
  指标未定义或挑战者 walk-forward INCONCLUSIVE）；不代表接近晋级或拒绝。
- `FAILED`：身份/注册/配对/系统完整性错误；比较结论为 null，含脱敏错误码。
- 幂等恢复：只有**完全相同**的 `challenge_id`（即全部声明哈希一致）可以重入；
  结果产物逐字节一致才可复用。**改变股票池版本、参数、成本情景或遭遇失败，
  都不会归还已消费的历史 holdout**：正式晋级必须等待真正未见的新历史 fold。

## 已知边界（务必记住，不是 bug）

1. **`data bootstrap` 发布首个基线；`data update` 只扩展**：首个基线由
   `python -m stock_quant data bootstrap --root .` 发布（`bootstrap_seed.py` 是
   与它等价的直调脚本）；`data update` 只能扩展已有数据集。基线用合成周历近似 +
   合成 list_date，官方日历用 `--calendar-csv`，操作侧 §4 核对。
2. **B2 akshare 实时缺口**：akshare 基准接口实时返回无 symbol 列 → 阶段 4 基准角色
   可能 BLOCK。先按运维文档 §4.6 操作侧对账，或决定改适配器（上游改动，离线不可验）。
3. **`backtest momentum_60d` 只进 `data/runs/debug`**，正式结论看 `research run`。
   `--engineering` 是**纯诊断，永不发布 ACCEPTED**：即使覆盖证据可信，该 run 的评价
   也恒为 UNTRUSTED（实验清单 REJECTED，理由为 diagnostic-only）；覆盖证据不足时理由
   改为点明数据 UNTRUSTED 与原因。stdout 的 `trust=` 反映**数据**可信度——可信数据在
   工程模式下仍打印 `trust=TRUSTED`，但该 run 仍非正式结论。仅限排障，永不构成可信
   绩效；正式研究没有该开关。
4. **复权口径（momentum_60d v2）**：动量只消费不可变 `adjusted_bar` 表
   （`adjustment=internal_total_return_v1`，由未复权收盘与已核验现金分红/送股/转增
   事件导出）；订单、成交、涨跌停判断与账户估值继续用未复权 `daily_bar` 价格。
   在 `adjusted_bar` 之前创建的数据集仍可审计，但不能运行 v2 研究实验——跑一次完整
   `data update` 发布兼容数据集。不可信公司行为断点使跨越它的每个动量窗口无效；
   系统绝不静默回退到未复权收盘价。
5. **`configs/universes/csi300.yml` 是占位模板，不是可用定义**：正式研究必须先
   走完阶段 5，用真实证据哈希与实际覆盖区间填写该文件；占位定义在
   `universe_acceptance` 处必然失败（无绕过）。成分证据缺失、数量不对、退市边界
   不确定 = 停止工作并更正，永不降级继续。
6. **真实数据验收的边界**：bootstrap 空种子与在验收证据（`build_config` 溯源）
   出现之前发布的数据集，其自动检查（如 `raw_snapshot_traceability`）必然
   FAIL——跑一次完整 `data update` 重新生成溯源后再验收；旧数据集与旧实验
   仍可读取审计。工程模式无需验收即可运行，但产物恒记 UNVERIFIED/UNTRUSTED。
   验收证据路径只允许项目内相对路径（绝对路径、目录穿越、软链越界一律拒
   绝）；`external` 证据永不抓取。已发布的验收记录不可修改：拒绝记录永久
   留档但永不可被研究选中。
7. **无盈利/实盘就绪声明**：本 MVP 是工程链路验收，不是投资建议。
8. **一次性挑战的 holdout 不可逆**：`strategy_family + fold_schedule_hash`
   一旦被消费即永久消费；失败、崩溃、拒绝结论都不会归还。挑战者结果产物
   必须在声明发布与消费完成之后再打开；PROMOTED 只表示研究晋级，不表示
   自动部署或投资许可。
