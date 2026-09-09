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
conda run -n py310 python -m pytest -q        # 645 过；external/smoke 默认剔除
```

## 阶段 2 · 引导基线数据集（一次性；让 data update 可启动）

```bash
cd ~/work/program/stock/project
python -m stock_quant data bootstrap --root .   # 发布 security_master + 日历 + 空日线/公司行为
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

- 门禁与策略无关：非正价格/schema 冲突/必需源不可用 → BLOCK；跨源价差、复权缺失
  只记录不阻断（设计 §13.5）。
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

## 阶段 6 · 正式研究（唯一发布者）

```bash
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root .
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root .   # 复跑验证可复现
# 两次 experiment_id 相同 → data/experiments/<id>/: metrics.json + report.html + 因子/组合/回测帧
```

REJECTED 实验也会完整发布并留原因；跑挂只留 `data/runs/` 审计、不发布半成品。

正式研究受**两道预检门禁**约束，均在回测前生效、均无降级开关：

- **公司行为可信门禁**：执行窗口内每只股票池标的都必须持有 `VERIFIED` /
  `VERIFIED_EMPTY` 的公司行为覆盖证据，否则实验在回测前即被 REJECTED 并打印
  不可信原因（如 `SOURCE_NOT_REQUESTED` / `SOURCE_FETCH_FAILED`）。
- **指数成分证据门禁（universe_acceptance）**：见阶段 5 —— 定义缺失、哈希与
  数据集成分表不符、证据/数量/边界/覆盖异常都在因子之前失败并留下 redacted
  预检清单。

`research run` **没有任何降级绕过开关**（`--help` 里无 `--engineering`）——正式
结论永远不降低自己的证据标准。

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
# 打开 data/reports/*.html 核对：来源/局限、三成本场景、两基准、免责声明
```

按 `docs/operations/phase-one-validation.md` §4 收尾：源行数、跨源最大差、复权抽查、
公司行为冲突、密钥扫描、无盈利宣称、单次 run 计时（<600s）。

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
4. **复权序列未发布/未消费**：动量跑未复权（adjusted_close=close）。
5. **无盈利/实盘就绪声明**：本 MVP 是工程链路验收，不是投资建议。
6. **`configs/universes/csi300.yml` 是占位模板，不是可用定义**：正式研究必须先
   走完阶段 5，用真实证据哈希与实际覆盖区间填写该文件；占位定义在
   `universe_acceptance` 处必然失败（无绕过）。成分证据缺失、数量不对、退市边界
   不确定 = 停止工作并更正，永不降级继续。
