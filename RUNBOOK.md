# 阶段一 完整使用流程（RUNBOOK）

面向**本工程**（30 只固定样本 + 沪深300/中证500）的端到端操作顺序。代码在
`~/work/program/stock/.worktrees/phase-one-quant-system`，工程根（configs/ + data/）
在 `~/work/program/stock/project`；CLI 已 editable 装入 conda env **py310**。

> 与运维验收清单的关系：这里是"怎么做"；`docs/operations/phase-one-validation.md`
> 是"每条命令要核对什么、合法诊断结论长什么样"（checklist §4、记录模板 §6）。

## 阶段 0 · 准备（一次性）

```bash
conda activate py310
# 包安装（已做）：
python -m pip install -e ~/work/program/stock/.worktrees/phase-one-quant-system
cd ~/work/program/stock/project
cp -r ~/work/program/stock/.worktrees/phase-one-quant-system/configs .   # 若无 configs
export TUSHARE_TOKEN='<你的轮换后token>'      # 只进环境变量，绝不入库
```

**必查 configs/project.yml**：
- `start_date`/`end_date` = 你想拉的数据区间。
- `benchmark_symbols` 只放**指数** `000300.SH`、`000905.SH`。放个股会让 data update
  用 akshare 的**指数**接口去取个股 → 取数失败 → 门禁 BLOCK。
- `publication_time: "15:00"`（已配）。

## 阶段 1 · 离线自检（无网络/Token）

```bash
cd ~/work/program/stock/.worktrees/phase-one-quant-system
conda run -n py310 python -m ruff check src tests
conda run -n py310 python -m pytest -q        # 373 过；external/smoke 默认剔除
```

## 阶段 2 · 引导基线数据集（一次性；让 data update 可启动）

```bash
cd ~/work/program/stock/project
python bootstrap_seed.py            # 发布 security_master + 日历 + 空日线/公司行为
# 官方交易日历（可选，替代周历近似）：
# python bootstrap_seed.py --calendar-csv official_calendar.txt
python -m stock_quant data validate --root .   # 应 PASS（空数据集自检）
```

## 阶段 3 · 实时小窗口验收（先验证联网/契约，再上正式量）

```bash
cd ~/work/program/stock/.worktrees/phase-one-quant-system
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
- 全区间再跑：`--start 2021-01-01 --end 2026-08-30`（= configs 范围；也可不给 --end，
  由"最新完整交易日 + 发布时间 15:00"规则自动发现）。

## 阶段 5 · 正式研究（唯一发布者）

```bash
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root .
python -m stock_quant research run --spec configs/experiments/momentum_60d.yml --root .   # 复跑验证可复现
# 两次 experiment_id 相同 → data/experiments/<id>/: metrics.json + report.html + 因子/组合/回测帧
```

REJECTED 实验也会完整发布并留原因；跑挂只留 `data/runs/` 审计、不发布半成品。

## 阶段 6 · 报表 + 人工核查

```bash
python -m stock_quant report build --root .        # 最新实验 HTML + 当前数据质量 HTML
# 打开 data/reports/*.html 核对：来源/局限、三成本场景、两基准、免责声明
```

按 `docs/operations/phase-one-validation.md` §4 收尾：源行数、跨源最大差、复权抽查、
公司行为冲突、密钥扫描、无盈利宣称、单次 run 计时（<600s）。

## 已知边界（务必记住，不是 bug）

1. **首个基线无 CLI**：`data update` 只扩展已有数据集；`security_master`/
   `trading_calendar` 用 `bootstrap_seed.py` 发布（合成周历近似 + 合成 list_date，
   官方日历用 `--calendar-csv`，操作侧 §4 核对）。
2. **B2 akshare 实时缺口**：akshare 基准接口实时返回无 symbol 列 → 阶段 4 基准角色
   可能 BLOCK。先按运维文档 §4.6 操作侧对账，或决定改适配器（上游改动，离线不可验）。
3. **`backtest momentum_60d` 只进 `data/runs/debug`**，正式结论看 `research run`。
4. **复权序列未发布/未消费**：动量跑未复权（adjusted_close=close）。
5. **无盈利/实盘就绪声明**：本 MVP 是工程链路验收，不是投资建议。
