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

# 密钥只从仓库根的 .env 读入（该文件已被 gitignore）。.env.example 是模板、
# 值恒为空，**绝不把真实值写进任何被跟踪的文件**；KEY=value 且等号两侧不留空格。
cd ~/work/program/stock
set -a; . ./.env; set +a

cd ~/work/program/stock/project
cp -r ~/work/program/stock/templates/project-config configs   # 若无 configs（仓库根不再有 configs/）
```

> `.env` 只在当前 shell 生效。**每次发布前都要重新 source 一次并显式导出
> `TUSHARE_TRANSPORT=relay`** —— 见阶段 4。别把 token 写成 `export TUSHARE_TOKEN=...`
> 打进 shell 历史里。

**必查 configs/project.yml**：
- `start_date`/`end_date` = 你想拉的数据区间。
- `benchmark_symbols` 只放**指数** `000300.SH`、`000905.SH`。放个股会让 data update
  用 akshare 的**指数**接口去取个股 → 取数失败 → 门禁 BLOCK。
- `publication_time: "15:00"`（已配）。

## 阶段 1 · 离线自检（无网络/Token）

```bash
cd ~/work/program/stock
conda run -n py310 python -m ruff check src tests
conda run -n py310 python -m pytest -q        # 433 过；external/smoke 默认剔除
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
set -a; . ./.env; set +a
export TUSHARE_TRANSPORT=relay
conda run -n py310 python -m pytest -m smoke -v -o log_cli=true --log-cli-level=INFO
```

> smoke 那条跑的是 `DataPipeline.update` —— 一条**发布路径**，所以同样要
> `TUSHARE_TRANSPORT=relay`。缺了它不会报错退出，而是 tushare 源初始化失败、
> 门禁读作 `BLOCK`，而该测试对 `BLOCK` 是放行的 —— 于是这条 smoke **看着绿、
> 实际没验到联网**。判据是两条：输出里有 `kind=relay` 那行（上面的
> `log_cli` 就是为它开的），且这次是以 `PASS` 结束（`BLOCK` 只说明"没通过"，
> 分不清是数据问题还是传输没起来）。

## 阶段 4 · 正式拉数 + 质量门禁

**建议先小窗口试一次**（半天拉得动、问题暴露快），确认门禁能过再铺全区间：

```bash
cd ~/work/program/stock
set -a; . ./.env; set +a
export TUSHARE_TRANSPORT=relay        # 发布必须显式声明传输，缺了必失败（见下）
cd ~/work/program/stock/project
python -m stock_quant data update --start 2024-01-01 --end 2024-03-31 --root .
python -m stock_quant data validate --root .
# PASS  → data/standardized/<版本哈希>/ 新增不可变版本（CURRENT 指向它）
# BLOCK → 数据集不变；看上方 redacted 原因行（ERROR/FATAL 计数、source 状态）
```

- **`TUSHARE_TRANSPORT=relay` 是发布的硬前提。** 不导出它就退出码 1、报
  `TUSHARE_TRANSPORT must be set explicitly for a published build`，数据集不变。
  这是设计而非故障：发布路径上的 `resolve_transport` 永不回退。反向自检就是
  「把 `TUSHARE_TRANSPORT` 摘掉，同一条命令必须失败且 `data/standardized/CURRENT`
  不变」，完整三步人工验收见 `docs/operations/relay-publish-runbook.md`。

- 门禁与策略无关：非正价格/schema 冲突/必需源不可用 → BLOCK；跨源价差、复权缺失
  只记录不阻断（设计 §13.5）。
- **`data update` 必须先刷新 tushare `stock_basic` 全市场快照**：该必需步骤刷新
  `security_master` 的上市事实并发布 `security_master_coverage`（每标的一行 = 研究冻结
  的证据）；拉取失败或快照缺某股票池标的 → 阻断发布。
- `data update` 不带 `--end` 时，终点只能取已发布 `trading_calendar.calendar_date`
  的最大值；已发布日历为空时必须显式传 `--end`。
- 每次 unmarked `data update` 都会向 relay 分别请求 SSE / SZSE 的
  `trade_cal`（halo `[start-1, end+1]`），两份响应都进 raw store。任一请求、
  原始校验、两市比对或 `pretrade_date` 连续性失败都会 FATAL，`CURRENT` 不动。
- 全历史验收：从 manifest 绑定的 `full_history_acceptance_start` 到已发布日历
  最大日期之间，不允许出现 `bootstrap_seed` span。种子只允许留在该起点之前。
- 消除 bootstrap 种子日历的唯一方式：提交一次覆盖全历史的更新窗口，例如
  `data update --start 2015-01-05 --end <已发布日历最大日期>`；窗口必须覆盖
  整个已发布日历范围，否则发布被 `calendar_coverage_gap` / `calendar_uncovered`
  阻断，错误详情里会给出需要覆盖的 `first_open_day` / `last_open_day`。
- **公司行为复核只看本轮窗口。** 只把 `ex_date` 落在本轮 `[start, end]` 的 review
  传给复核器；窗口外的已审历史事实由当前数据集保留，不要求供应商本轮重现 ——
  所以小窗口试用（如上面的 2024Q1）不会因为 2018 年的已审冲突而失败。窗口**内**
  仍严格 fail-closed：缺冲突行、来源或经济字段变化都继续阻断。
- 若出现 `FAILED: reviewed corporate action <symbol>#<ex_date> has 0 matching
  <source> conflicts`（`<source>` 是该条 review 的 `selected_source`：`cninfo` 或
  `eastmoney`）：这是**窗口问题，不是传输或数据问题** —— 该 review 的 `ex_date` 落在
  了你请求的 `[start, end]` 内，但供应商那两行冲突没取到。先核对你请求的窗口，
  **不要**去改 `configs/corporate_action_reviews.yml`，也不要放宽闸门。
- **怎么读「这次刷新从哪天开始」**：`data/standardized/<版本>/dataset_manifest.json`
  的 `build_config` 里，`requested_start_date` 是命令行写的（没写就是 `null`），
  `effective_start_date` 是配置回退后的**实际起点**。别把 `requested_start_date:
  null` 误读成"没有起点"。改造前发布的旧版本没有 `effective_start_date` 字段。

## 阶段 4.5 · 数据验收与 ACCEPTED 发布（正式研究前必须完成）

`data validate` 的 PASS 只说明数据集通过自动质量检查；它**不**产生正式研究所需的
验收记录。对阶段 4 刚发布的 `dataset_version`，必须先生成并人工填写验收清单，再发布
`ACCEPTED` 记录：

```bash
# <VERSION> 是阶段 4 data update 输出的 dataset_version；不要改写已有版本。
# prepare 会写出清单 YAML，并在 data/acceptance-evidence/<VERSION>/ 下生成六份
# 确定性证据。六项可机械化人工项（source_row_count_sample、missing_reason_sample、
# corporate_action_sample、benchmark_sample、security_master_sample、secret_scan）
# 已指向这些证据，但仍须逐项审阅确认；另外三项外部佐证（exchange_calendar_sample、
# cross_source_price_sample、trading_rule_effective_dates）只能由审核者补齐。
python -m stock_quant data acceptance prepare --version <VERSION> \
  --operator <OPERATOR_ID> --output checklist.yml --root .

# 审核者逐项审阅六项证据、补齐三项外部佐证，然后把九项 manual 全部改为 PASS
# （九项均 PASS 且每项 evidence 可校验）后才 publish。
python -m stock_quant data acceptance publish --checklist checklist.yml --root .

# 可选：确认当前版本的 ACCEPTED 记录与每项结果。
python -m stock_quant data acceptance show --version <VERSION> --root .
```

`prepare` 产出的九项 manual **全部是待确认**（`PENDING_CONFIRMATION`）：它只生成
证据、不做签署，所以未确认的清单直接 publish 只会得到 `REJECTED`（原因形如
`manual_<code>_pending_confirmation`）。命令本身不会把任何 manual 项标成 PASS——
签署永远是审核者的动作。`publish` 只在自动与人工检查均 PASS 时写入不可变的
`ACCEPTED` 记录；否则会写入 `REJECTED` 记录并以非零退出。不要通过改验收规则或
使用 engineering 模式绕过失败。阶段 5 的 `research run` 会选择该数据版本的
`CURRENT_ACCEPTED`；没有有效的 `ACCEPTED` 记录就不得进入正式研究。

`data/acceptance-evidence/<VERSION>/` 虽在只追加的 `data/acceptances/` 注册表之外，
却是已接受记录绑定的一部分：阶段 5 的 `research run` 预检会在运行时重新校验整份证据
包。备份或恢复 `data/acceptances/` 时必须一并保留同名的证据包，否则后续
`research run` 会因 `evidence_missing` 失败。

## 阶段 5 · 正式研究（唯一发布者）

```bash
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root .
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root .   # 复跑验证可复现
# 两次 experiment_id 相同 → data/experiments/<id>/: metrics.json + report.html + 因子/组合/回测帧
```

REJECTED 实验也会完整发布并留原因；跑挂只留 `data/runs/` 审计、不发布半成品。

正式研究受**公司行为可信门禁**约束：执行窗口内每只股票池标的都必须持有
`VERIFIED` / `VERIFIED_EMPTY` 的公司行为覆盖证据，否则实验在回测前即被 REJECTED
并打印不可信原因（如 `SOURCE_NOT_REQUESTED` / `SOURCE_FETCH_FAILED`）。
`research run` **没有任何降级绕过开关**（`--help` 里无 `--engineering`）——正式
结论永远不降低自己的证据标准。

核验覆盖证据（人工抽查）：
- 证据表 = 数据集内的 `corporate_action_coverage` 表，位于
  `data/standardized/<dataset_version>/corporate_action_coverage.parquet`
  （`data/standardized/CURRENT` 指向当前版本，`manifest.json` 列出全部表）；
  每行含 symbol、window_start/end、status（`VERIFIED`/`VERIFIED_EMPTY`/
  `UNTRUSTED`）、reason、sources、snapshot_hashes、checked_at。
- 或核对冻结实验本身：`data/experiments/<experiment_id>/metrics.json` 的
  `metrics.corporate_action_trust` 记录 {mode、dataset_version、window_start/end、
  trusted、reasons:[{code,symbol}]}。

## 阶段 6 · 报表 + 人工核查

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
