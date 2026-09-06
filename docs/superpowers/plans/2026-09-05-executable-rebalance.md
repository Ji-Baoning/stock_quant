# 可执行调仓约束 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每个成本情景中，以真实账户与当日行情把理想目标仓位投影为可成交订单。

**Architecture:** 新增独立投影器，由回测引擎在调仓日、公司行为入账后调用。投影器读取当日可执行行情、账户与成本模型，输出卖单优先、现金受限的买单及约束审计；研究运行器为每个情景保存动态订单与审计结果。

**Tech Stack:** Python 3.10、pandas、现有 `Account`、`ExecutionSimulator`、`BacktestEngine`。

**Spec:** `docs/superpowers/specs/2026-09-05-executable-rebalance-design.md`

## Global Constraints

- 不读取调仓日之后的市场数据。
- 买卖均为 100 股整手，买入成本必须使用情景 `CostModel`。
- 执行器校验保持不变，投影结果必须可审计。

---

### Task 1: 建立可执行性投影器与单元测试

**Files:**
- Create: `src/stock_quant/backtest/rebalance.py`
- Create: `tests/unit/test_rebalance.py`

**Interfaces:**
- Produces: `project_rebalance(target_quantities, account, bars, trade_date, cost_model, rule_book) -> RebalanceProjection`
- `RebalanceProjection` carries `sells`, `buys`, `adjustments` and `executable_target_quantities`.

- [ ] 写失败测试：卖单被截断到 `Account.sellable_quantity`，买单按费用后现金缩减为整手，停牌/涨跌停订单不提交。
- [ ] 运行 `pytest tests/unit/test_rebalance.py -q`，确认缺少投影器而失败。
- [ ] 实现最小不可变投影记录与投影器。
- [ ] 重跑同一测试，确认通过。

### Task 2: 将投影器接入回测日循环

**Files:**
- Modify: `src/stock_quant/backtest/engine.py`
- Modify: `tests/integration/test_backtest_engine.py`

**Interfaces:**
- `BacktestRequest` optionally carries按日期的目标数量；引擎在公司行为入账后调用投影器，替代静态 `OrderDay`。
- `BacktestResult` adds projection adjustment ledger and submitted order ledger.

- [ ] 写失败集成测试：静态理想目标在现金不足、T+1、停牌和涨停时被预先调整，执行结果无对应拒单。
- [ ] 运行目标测试确认失败。
- [ ] 接入引擎并保存动态订单/调整流水。
- [ ] 重跑目标测试确认通过。

### Task 3: 研究运行器按成本情景生成并保存投影产物

**Files:**
- Modify: `src/stock_quant/research/runner.py`
- Modify: `src/stock_quant/research/models.py`
- Modify: `tests/integration/test_cli.py`

**Interfaces:**
- 输入：`target_positions.parquet` 的信号日目标。
- 输出：每情景 `submitted_orders.parquet`、`rebalance_adjustments.parquet` 与可成交目标流水。

- [ ] 写失败测试：相同理想目标在费用情景下的提交买入数量不大于零成本情景，产物均存在。
- [ ] 运行测试确认失败。
- [ ] 将理想目标转为按执行日的 target schedule，并写出情景产物。
- [ ] 重跑测试确认通过。

### Task 4: 扩展诊断与全链路验证

**Files:**
- Modify: `project/execution_diagnostics.py`
- Modify: `src/stock_quant/reporting/html.py`
- Modify: `src/stock_quant/reporting/templates/experiment.html.j2`

- [ ] 写失败报告测试：执行报告区分“调仓前约束调整”与“执行拒单”。
- [ ] 运行报告测试确认失败。
- [ ] 呈现约束原因、缩减金额和可成交目标偏离。
- [ ] 运行 `pytest tests/unit/test_rebalance.py tests/integration/test_backtest_engine.py tests/integration/test_cli.py tests/integration/test_reports.py -q` 及真实项目研究运行，检查拒单统计与逐日诊断。
