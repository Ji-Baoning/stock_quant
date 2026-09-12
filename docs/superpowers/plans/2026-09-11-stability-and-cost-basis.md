# 成本口径修正与稳定性结论 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `project/configs/costs.yml` 的印花税口径按真实费率历史修正、一次性量化其影响并留证据，然后在两侧（等权基线 + 缓冲式风险加权挑战者）上产出 walk-forward 稳定性结论。

**Architecture:** 成本修正是一处生产配置编辑——`CostModel._select` 已经是"生效日不晚于交易日的最新一条"，分段天然被支持，**不需要改任何 `src/` 代码**。稳定性走已存在的 `walk_forward_oos_v1` 管线：先 ENGINEERING 诊断（debug registry，永不进 `data/experiments`），方案二落地后再走正式 RESEARCH。**不执行一次性策略挑战**——holdout 注册表必须保持为空。

**Tech Stack:** Python 3 / pydantic v2（`CostConfig`/`CostRate` 为 `extra="forbid"`）/ typer CLI (`python -m stock_quant`) / pytest。

## Global Constraints

- **只改一处生产配置**：`project/configs/costs.yml`。不改 `src/` 下任何代码——`CostModel._select` 已支持生效日分段。唯一的测试改动在 `tests/unit/test_costs.py`。
- **不打一次性策略挑战**：`python -m stock_quant research challenge` 永不执行；holdout 注册表必须保持为空（Task 6 有验证步骤）。
- **不生成 canonical `csi300`**；不改动已封存的 `custom_csi300_ic` 正典。
- **`project/data/` 整个被 `.gitignore`**：debug 运行产物、metrics、stability 报告都不是提交物；提交的只有 `docs/` 与 `project/configs/`。
- **报告必须标注 UNTRUSTED**，并写明**幸存者偏差仍然存在**（股票池来自 `security_master`，非时点无偏）。
- **具名测试，永不跑全量**：只跑 `tests/unit/test_costs.py`。集成测试约 18.5 分钟，本计划不跑。
- **运行环境**：机器约 7 GB 总内存 / 3 GB 可用，曾发生 exit 137 OOM。两侧 wf 运行必须**各起一个独立进程**，不要并行。
- **对话用简体中文，代码/标识符用英文。**

---

## 前提与依赖（已实测，不是假设）

| 事实 | 证据 |
| --- | --- |
| `project/` 是 CLI 的 `--root`（配置在 `project/configs/`） | `load_project_config(root)` 读 `root/configs/project.yml`；`cli.py` 默认 `--root .` |
| `costs.yml` **参与实验身份** | `runner.py:2295-2300` 的 `_config_hashes` 含 `costs.yml` → `ExperimentSnapshot.config_hashes` → `SnapshotBundle` → experiment id |
| 因此改 `costs.yml` 会产生**新实验 id**，不会覆盖既有实验 | `_config_hashes` 同上 |
| `ab378f9d…` 的产出规格是 `momentum_60d_offline_real_extended.yml` | 该规格 `date_range 2015-01-01..2026-08-21`、`engineering_single_window`（默认值）与 `ab378f9d/experiment_spec.yml` 逐字一致 |
| `CURRENT` = `af5799ae…` = `ab378f9d` 用的数据集 | `project/data/standardized/CURRENT` 内容即 `af5799ae…`；`ab378f9d/experiment_spec.yml` 的 `dataset_version` 同 |
| **walk-forward 必须有 `universe_definition`** | `runner.py:1362-1367`：`self._universe_preflight is None` 时 schedule 阶段 `raise ValueError` |
| 所以两侧诊断 wf 运行**依赖方案一** | 同上；当前 `project/configs/universes/` 只有 `custom_csi300_ic.yml` |
| 正式 RESEARCH 还需验收记录 | `runner.py:682-692`：RESEARCH 模式或显式 `data_acceptance_id` → `_resolve_acceptance` |

### `ab378f9d` 基线数值（Task 2 的对照基准）

`project/data/runs/debug/ab378f9d23509b5d2415a81ac7aa36eb070cf0293487346071b3e5d52f04606d/metrics.json`：

| 情景 | end_equity | total_return | n_fills | commission | stamp_tax |
| --- | --- | --- | --- | --- | --- |
| `zero_cost` | 204,024.30 | 1.040243 | 1,852 | 0.00 | 0.00 |
| `commission_tax` | 192,067.29 | 0.9206729 | 1,852 | 9,260.00 | 2,697.01 |
| `full_cost` | 181,286.83 | 0.8128683 | 1,852 | 9,260.00 | 2,694.47 |

`initial_cash = 100000`，`project.yml` 的 `benchmark_symbols: ['000300.SH','000905.SH']`。

### 文件结构

| 文件 | 责任 | 动作 |
| --- | --- | --- |
| `project/configs/costs.yml` | 三个成本情景的生效日费率表 | **修改**（印花税分段） |
| `tests/unit/test_costs.py` | `CostModel` 的单元测试 + 生产配置镜像 | **修改**（镜像分段 + 锁定生产配置的新测试） |
| `docs/operations/2026-09-11-cost-basis.md` | 官方费率来源、过户费量化、"不建模"决策 | 新建（Task 3） |
| `project/configs/experiments/momentum_60d_wf_real_baseline.yml` | 等权基线 wf 规格 | 修改（Task 4：头部事实 + `universe_definition`） |
| `project/configs/experiments/momentum_60d_wf_real_challenger.yml` | 缓冲式挑战者 wf 规格 | 修改（Task 5：同上） |
| `docs/operations/2026-09-11-stability-and-cost-basis.md` | 成本影响实测 + 逐 fold 稳定性结论 | 新建（Task 5/6） |

---

### Task 1: 成本口径按真实费率历史修正

**Files:**
- Modify: `project/configs/costs.yml`（全文件）
- Test: `tests/unit/test_costs.py:42-73`（镜像）、`:159-197`（参数化期望）、文件末尾（新增 3 个测试）

**Interfaces:**
- Consumes: `stock_quant.config.load_project_config(root: Path) -> ProjectConfig`；`CostModel.from_config(config: CostConfig, name: str) -> CostModel`；`CostModel.stamp_tax_rate(trade_date: date) -> Decimal`、`.commission_rate(date) -> Decimal`、`.slippage(date) -> Decimal`
- Produces: 修正后的 `project/configs/costs.yml`（Task 2 的重跑消费它，Task 3 的文档引用它）

**背景**：`costs.yml` 头部自述"印花税沿用文件既有口径（0.5‰ 卖出，未按 2023-08-28 前的 1‰ 区分）"。真实历史：**1‰ 至 2023-08-27，2023-08-28 起减半为 0.5‰**。

**陷阱（必须处理）**：现文件每个情景都有 `2015-01-01` 与 `2020-01-01` 两行**数值完全相同**的行（`costs.yml:8-41`）。若只把 `2015-01-01` 行的印花税改成 `0.001` 而保留 `2020-01-01` 行，`_select` 会在 2020-01-01 起选中那条 0.0005 的行，**1‰ 段被完全遮蔽**，2020-2023 仍然算错。`2020-01-01` 这个分界没有真实含义（费率相同），必须**删除**，让费率表只剩两个真实分段：`2015-01-01`（1‰）与 `2023-08-28`（0.5‰）。

- [x] **Step 1: 写失败测试——锁定生产配置的印花税时间轴**

在 `tests/unit/test_costs.py` 顶部 import 段补两行：

```python
from pathlib import Path

from stock_quant.config import CostConfig, CostRate, CostScenario, load_project_config
```

（原来的 `from stock_quant.config import CostConfig, CostRate, CostScenario` 替换为上行的四符号版本。`date`、`Decimal`、`pytest` 已在文件顶部导入。）

在 `tests/unit/test_costs.py` 文件**末尾**追加：

```python
# --------------------------------------------------------------------------- #
# The production configs/costs.yml itself
# --------------------------------------------------------------------------- #

#: ``tests/unit/test_costs.py`` -> repo root -> ``project/``.
_PROJECT_ROOT = Path(__file__).resolve().parents[2] / "project"


def test_production_costs_yml_splits_stamp_tax_at_the_2023_cut():
    """The real costs.yml carries the official stamp-tax history.

    Stamp tax was 1 per mille on sells until 2023-08-27 and 0.5 per mille from
    2023-08-28.  A dataset window starting 2015 must therefore price the two
    regimes from one dated schedule, and the boundary day itself must already
    select the reduced rate.
    """
    config = load_project_config(_PROJECT_ROOT)
    for scenario in ("commission_tax", "full_cost"):
        model = CostModel.from_config(config, scenario)
        assert model.stamp_tax_rate(date(2015, 1, 5)) == Decimal("0.001")
        assert model.stamp_tax_rate(date(2023, 8, 25)) == Decimal("0.001")
        assert model.stamp_tax_rate(date(2023, 8, 28)) == Decimal("0.0005")
        assert model.stamp_tax_rate(date(2026, 1, 5)) == Decimal("0.0005")


def test_production_costs_yml_zero_scenario_is_date_invariant():
    """``zero_cost`` is all-zero on every date in the window."""
    config = load_project_config(_PROJECT_ROOT)
    model = CostModel.from_config(config, "zero_cost")
    for day in (date(2015, 1, 5), date(2023, 8, 28), date(2026, 1, 5)):
        assert model.commission_rate(day) == Decimal("0")
        assert model.minimum_commission(day) == Decimal("0")
        assert model.stamp_tax_rate(day) == Decimal("0")
        assert model.slippage(day) == Decimal("0")


def test_production_costs_yml_rejects_dates_before_the_window():
    """No rate is effective before the schedule starts; it fails loudly."""
    config = load_project_config(_PROJECT_ROOT)
    model = CostModel.from_config(config, "full_cost")
    with pytest.raises(ValueError, match="no cost rate is effective"):
        model.stamp_tax_rate(date(2014, 12, 31))
```

- [x] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_costs.py -q`
Expected: 3 个新测试 FAIL。`..._splits_stamp_tax_at_the_2023_cut` 在 `date(2023, 8, 25)` 断言处失败（现配置给 0.0005，期望 0.001）。

- [x] **Step 3: 改写 `project/configs/costs.yml`**

全文件替换为（注意：删除了三行无意义的 `2020-01-01` 重复段，这是**修正的一部分**，不是顺手清理）：

```yaml
# 成本情景费率表（官方费率历史 + 操作者申报假设）。
#
# 印花税（官方，卖出单边）：1‰ 至 2023-08-27；2023-08-28 起减半为 0.5‰。
#   来源见 docs/operations/2026-09-11-cost-basis.md 的"官方费率"段。
# 过户费（官方，双向）：0.02‰ 自 2015-08-01，2022-04-29 起 0.01‰。本表**不建模**
#   过户费——CostRate 目前没有该字段；在扩展窗口配置上量化为初始资金的 0.171%
#   （印花税修正量的 1/11），决策记录见同一文档的"过户费量化"段。
# 佣金 0.3‰ / 最低 ¥5、滑点 0.1%：**操作者申报假设，非官方费率**。实测该配置下
#   1,852 笔成交每一笔都命中 ¥5 最低值，故 commission_rate 对结果零影响。
#
# 生效日语义：CostModel._select 取"生效日不晚于交易日的最新一条"，因此每个情景
# 只需声明费率真正发生变化的日期。2020-01-01 曾用作分界但两侧数值相同，已删除。
scenarios:
  - name: zero_cost
    rates:
      - effective_from: 2015-01-01
        commission_rate: 0.0
        minimum_commission: 0.0
        stamp_tax_sell_rate: 0.0
        slippage_rate: 0.0
  - name: commission_tax
    rates:
      - effective_from: 2015-01-01
        commission_rate: 0.0003
        minimum_commission: 5
        stamp_tax_sell_rate: 0.001
        slippage_rate: 0.0
      - effective_from: 2023-08-28
        commission_rate: 0.0003
        minimum_commission: 5
        stamp_tax_sell_rate: 0.0005
        slippage_rate: 0.0
  - name: full_cost
    rates:
      - effective_from: 2015-01-01
        commission_rate: 0.0003
        minimum_commission: 5
        stamp_tax_sell_rate: 0.001
        slippage_rate: 0.001
      - effective_from: 2023-08-28
        commission_rate: 0.0003
        minimum_commission: 5
        stamp_tax_sell_rate: 0.0005
        slippage_rate: 0.001
```

- [x] **Step 4: 更新测试里的配置镜像**

把 `tests/unit/test_costs.py:42-73` 的 `_cost_config()` 整体替换为：

```python
def _cost_config() -> CostConfig:
    """Mirror of ``configs/costs.yml`` (stamp tax split at the 2023-08-28 cut)."""

    def commission_tax_rates() -> list[CostRate]:
        return [
            rate(effective_from=date(2015, 1, 1), commission=0.0003,
                 minimum=5.0, stamp=0.001, slippage=0.0),
            rate(effective_from=date(2023, 8, 28), commission=0.0003,
                 minimum=5.0, stamp=0.0005, slippage=0.0),
        ]

    def full_cost_rates() -> list[CostRate]:
        return [
            rate(effective_from=date(2015, 1, 1), commission=0.0003,
                 minimum=5.0, stamp=0.001, slippage=0.001),
            rate(effective_from=date(2023, 8, 28), commission=0.0003,
                 minimum=5.0, stamp=0.0005, slippage=0.001),
        ]

    return CostConfig(
        scenarios=[
            CostScenario(
                name="zero_cost",
                rates=[rate(effective_from=date(2015, 1, 1))],
            ),
            CostScenario(name="commission_tax", rates=commission_tax_rates()),
            CostScenario(name="full_cost", rates=full_cost_rates()),
        ]
    )
```

- [x] **Step 5: 更新参数化测试的印花税期望**

`test_three_cost_scenarios_price_and_tax_exactly` 的 `trade_date = date(2020, 1, 2)` 现在落在 **1‰ 段**。把参数表（`tests/unit/test_costs.py:159-197`）里 `commission_tax` 与 `full_cost` 两个 case 的 `sell_stamp` 从 `Decimal("0.50")` 改为 `Decimal("1.00")`（100 股 × ¥10.00 × 0.001 = ¥1.00）。`zero_cost` 保持 `Decimal("0.00")`。

- [x] **Step 6: 补一个减半段的定价测试**

在同一文件的参数化测试之后追加：

```python
def test_full_cost_prices_the_reduced_stamp_tax_from_the_cut_day():
    """From 2023-08-28 the sell stamp tax is 0.5 per mille, not 1."""
    model = CostModel.from_config(_cost_config(), "full_cost")
    cut_day = model.calculate(SELL, 100, Decimal("10.00"), date(2023, 8, 28))
    day_before = model.calculate(SELL, 100, Decimal("10.00"), date(2023, 8, 27))
    assert cut_day.stamp_tax == Decimal("0.50")
    assert day_before.stamp_tax == Decimal("1.00")
```

- [x] **Step 7: 跑测试确认全绿**

Run: `python -m pytest tests/unit/test_costs.py -q`
Expected: PASS，无失败。

- [x] **Step 8: 确认没有别的测试直接依赖被删的 2020-01-01 行**

Run: `grep -rn "2020-01-01\|2020, 1, 1" tests/unit/test_costs.py`
Expected: 只剩 `rate()` 的默认参数值与 `test_picks_the_latest_rate_...`、`test_no_rate_effective_...` 这两个显式用 `date(2020, 1, 1)` 的、各自构造独立 `CostRate` 的测试——它们不读 `costs.yml`，不受影响。

- [x] **Step 9: 提交**

```bash
cd /home/ji/work/program/stock
git add project/configs/costs.yml tests/unit/test_costs.py
git commit -m "fix: price the sell stamp tax from the official 2023-08-28 rate cut"
```

---

### Task 2: 一次性成本影响测量（重跑扩展窗口单窗口回测）

**Files:**
- 无文件改动。产出一个 **debug** 运行目录（`project/data/runs/debug/<new_id>/`，被 gitignore），读取两个 `metrics.json` 做对照。

**Interfaces:**
- Consumes: Task 1 修正后的 `project/configs/costs.yml`；基线 `project/data/runs/debug/ab378f9d23509b5d2415a81ac7aa36eb070cf0293487346071b3e5d52f04606d/metrics.json`
- Produces: 一组"旧口径 → 新口径"的三情景对照数字，写进 Task 3 的文档

**为什么是一次性测量**：`costs.yml` 参与实验身份（见"前提"），所以这次重跑会得到**新实验 id**；它落在 debug registry（`backtest` 命令的 `_DebugRegistry`），**永不进入 `data/experiments`**，因此不是长期产物。数字进报告，运行目录是 scratch，可随时清理。

**为什么对照有效**：规格、数据集（`CURRENT` = `af5799ae…`）、初始资金、种子全都没变，唯一变量是 `costs.yml`。**`zero_cost` 情景是内置对照组**——它的费率在新旧口径下都是全零，若它的 `end_equity` 发生变化，说明除成本外还有别的变量在动，测量作废，必须停下来查。

- [x] **Step 1: 记录重跑前的 debug registry 状态**

```bash
cd /home/ji/work/program/stock
python -c "import pandas as pd; print(pd.read_parquet('project/data/runs/debug/registry.parquet')['experiment_id'].tolist())"
```

Expected: `['a64b961afa…', 'ab378f9d235…']`（两个已知 id）。

- [x] **Step 2: 用修正后的成本口径重跑**

```bash
cd /home/ji/work/program/stock
python -m stock_quant backtest momentum_60d \
  --spec configs/experiments/momentum_60d_offline_real_extended.yml \
  --engineering --root project
```

Expected: 三行输出 `experiment_id=<40+ 位十六进制>`、`debug=project/data/runs/debug/<同一个 id>`、`trust=UNTRUSTED`。
**记下这个新的 `experiment_id`**——下面两步要用。

若 `trust=TRUSTED` 或命令非零退出：停止，报告实际输出。这条诊断路径应当恒为 UNTRUSTED。

- [x] **Step 3: 对照基线与新口径**

把 `<new_id>` 替成 Step 2 打印的 id：

```bash
cd /home/ji/work/program/stock
python - <<'PY'
import json, pathlib
base = pathlib.Path("project/data/runs/debug")
old = json.loads((base / "ab378f9d23509b5d2415a81ac7aa36eb070cf0293487346071b3e5d52f04606d" / "metrics.json").read_text())
new = json.loads((base / "<new_id>" / "metrics.json").read_text())
for name in ("zero_cost", "commission_tax", "full_cost"):
    o, n = old["scenarios"][name], new["scenarios"][name]
    print(f"{name:16s} n_fills {o['n_fills']} -> {n['n_fills']}")
    print(f"{'':16s} end_equity {o['end_equity']:>12,.2f} -> {n['end_equity']:>12,.2f}"
          f"  (Δ {n['end_equity'] - o['end_equity']:>+10,.2f})")
    print(f"{'':16s} stamp_tax  {o['stamp_tax']:>12,.2f} -> {n['stamp_tax']:>12,.2f}"
          f"  (Δ {n['stamp_tax'] - o['stamp_tax']:>+10,.2f})")
    print(f"{'':16s} commission {o['commission']:>12,.2f} -> {n['commission']:>12,.2f}")
PY
```

Expected（预测量级，实际以输出为准）：
- `zero_cost`：`end_equity` **逐分不变**（204,024.30 → 204,024.30），Δ = 0.00。**这是对照通过的条件。**
- `commission_tax`：`stamp_tax` 从 2,697.01 上升到约 4,573（约 +1,876），`end_equity` 下降同量级。
- `full_cost`：`stamp_tax` 从 2,694.47 上升到约 4,571（约 +1,877，占初始资金 1.877%），`end_equity` 从 181,286.83 下降。
- `n_fills` 三情景**都保持 1,852**（成本不改变成交路径，只改变现金）。
- `commission` 三情景保持 9,260.00（每笔 ¥5 下限）。

- [x] **Step 4: 判读并停下**

若 `zero_cost` 的 Δ ≠ 0.00 或 `n_fills` 有变：**测量作废**。说明 `CURRENT` 数据集或代码自 `ab378f9d` 以后发生了变化，先查清再继续，不要把它当作成本影响报告出去。

若对照通过：把 Step 3 的完整输出记下来，供 Task 3 引用。**不要为这次重跑补提交任何文件**——它是一次性测量。

---

### Task 3: 成本口径证据文档

**Files:**
- Create: `docs/operations/2026-09-11-cost-basis.md`

**Interfaces:**
- Consumes: Task 2 Step 3 的实测输出
- Produces: 供 Task 5 报告引用的费率来源与决策记录

- [x] **Step 1: 写文档**

内容须覆盖全部六节（缺一不可）：

1. **官方费率——印花税**：卖出单边，1‰ 至 2023-08-27，2023-08-28 起 0.5‰。逐条附可审计 http(s) 来源 URL 与生效日。至少两条不同来源。
2. **官方费率——过户费**：双向 0.02‰（2015-08-01 起），2022-04-29 起 0.01‰。同样附 http(s) 来源 URL 与生效日。**注意 `CostRate` 没有过户费字段**（`src/stock_quant/config.py:20-27`），这一点要在文档里明写。
3. **申报假设（非证据）**：佣金 0.3‰ / 最低 ¥5、滑点 0.1%。明确标注为操作者申报的简化假设，**不是**官方费率。
4. **过户费量化与"不建模"决策**：在 `ab378f9d` 的 1,852 笔成交上量化过户费；列出数值与占初始资金比例，与印花税修正量对比；给出**不建模**的结论与理由（量级、`CostRate` 无字段、改动面）。注明该量化**条件于该配置**（初始资金 10 万），资金规模变化时等比缩放。
5. **¥5 最低佣金发现**：该配置下 1,852 笔成交每笔都恰好命中 ¥5 下限（合计 9,260.00），因此 `commission_rate: 0.0003` 对结果零影响——汇报时必须说明，否则读者会误以为佣金率经过验证。
6. **修正影响的实测**：Task 2 Step 3 的三情景对照表，并注明 `zero_cost` 未变动即对照通过、`n_fills` 不变。

文档不得包含 token、绝对路径、账号。

- [x] **Step 2: 自审——数字与代码一致**

逐条核对：文中的官方费率与 `project/configs/costs.yml` 的生效日/数值**逐字一致**；引用的行号（如 `src/stock_quant/config.py:20-27`）在当前代码中确实是 `CostRate` 定义。发现不一致就地改掉。

- [x] **Step 3: 提交**

```bash
cd /home/ji/work/program/stock
git add docs/operations/2026-09-11-cost-basis.md
git commit -m "docs: record the official cost rates and the transfer-fee decision"
```

---

### Task 4: 两侧 walk-forward 工程诊断运行

**前置（硬性）**：方案一已执行完，`project/configs/universes/` 下已存在时点宇宙定义文件。
**若无此文件，本任务无法开始**——`runner.py:1362-1367` 会在 schedule 阶段直接 `raise ValueError`。

**Files:**
- Modify: `project/configs/experiments/momentum_60d_wf_real_baseline.yml`
- Modify: `project/configs/experiments/momentum_60d_wf_real_challenger.yml`

**Interfaces:**
- Consumes: 方案一产出的 `project/configs/universes/<tradable>.yml`；Task 1 的 `costs.yml`
- Produces: 两侧各一份 debug 运行目录，内含 `stability_report.json`

- [x] **Step 1: 确认前置宇宙定义存在**

```bash
cd /home/ji/work/program/stock
ls project/configs/universes/
```

Expected: 至少含 `custom_csi300_ic.yml` **和**方案一产出的时点过滤定义（设计中的 id 是 `custom_csi300_ic_tradable`）。
若第二个文件不存在或名字不同：**停止**，把 `ls` 输出报给 owner，用实际文件名继续。

- [x] **Step 2: 修正两份规格的过期头部**

两份规格（`momentum_60d_wf_real_baseline.yml:29-31`、`momentum_60d_wf_real_challenger.yml:24-26`）都写着"本规格在证据到位并完成 RUNBOOK 阶段 5 之前无法运行"。把这段替换为事实陈述：

```yaml
# walk_forward_oos_v1 要求冻结的时点宇宙定义（universe_definition）来提供
# 逐 fold 的时点成分；缺失时 runner 在 schedule 阶段直接失败（runner.py 中
# "a formal walk-forward run requires a frozen universe definition"）。
# 本规格绑定方案一产出的时点过滤定义，ENGINEERING 诊断模式下不绑定真实
# 数据验收记录。
```

- [x] **Step 3: 给两份规格加上宇宙定义绑定**

在两份规格的 `universe_version: CURRENT` 之后各加一行（id 用 Step 1 确认的实际值）：

```yaml
universe_definition: custom_csi300_ic_tradable
```

`trust_mode: engineering` 与 `data_acceptance_id: null` **保持不变**——诊断不绑定验收记录。

- [ ] **Step 4: 跑等权基线的诊断**

```bash
cd /home/ji/work/program/stock
python -m stock_quant backtest momentum_60d \
  --spec configs/experiments/momentum_60d_wf_real_baseline.yml \
  --engineering --root project
```

Expected: `experiment_id=<id>`、`debug=project/data/runs/debug/<id>`、`trust=UNTRUSTED`，退出码 0。
**记下 id。** 该命令不打印稳定性结论，下一步直接读文件。

- [ ] **Step 5: 读基线的稳定性结论**

```bash
cd /home/ji/work/program/stock
python -c "
import json; d=json.load(open('project/data/runs/debug/<baseline_id>/stability_report.json'))
print('research_status=', d.get('research_status'))
print('stability_conclusion=', d.get('stability_conclusion'))
"
```

Expected: `research_status` 与 `stability_conclusion` **都非空**。`stability_conclusion` ∈ `{STABLE, UNSTABLE, INCONCLUSIVE}`（`FAILED` 时 `research_status=FAILED` 且结论为 `null`）。

- [ ] **Step 6: 跑缓冲式挑战者的诊断**

先关掉上一个进程再跑（3 GB 可用内存，不要并行）：

```bash
cd /home/ji/work/program/stock
python -m stock_quant backtest momentum_60d \
  --spec configs/experiments/momentum_60d_wf_real_challenger.yml \
  --engineering --root project
```

Expected: 同 Step 4 的三行输出，退出码 0。**记下 id**，重复 Step 5 读它的 `stability_report.json`。

- [x] **Step 7: 提交规格改动**

```bash
cd /home/ji/work/program/stock
git add project/configs/experiments/momentum_60d_wf_real_baseline.yml \
        project/configs/experiments/momentum_60d_wf_real_challenger.yml
git commit -m "feat: bind the walk-forward specs to the point-in-time universe definition"
```

（运行产物在 `project/data/` 下，被 gitignore，不提交。）

---

### Task 5: 诊断稳定性结论报告

**Files:**
- Create: `docs/operations/2026-09-11-stability-and-cost-basis.md`

**Interfaces:**
- Consumes: Task 2 的成本对照数字；Task 4 两侧的 `stability_report.json`
- Produces: 诊断级结论记录；Task 6 会在末尾追加正式结论

- [ ] **Step 1: 提取两侧的逐 fold 明细**

```bash
cd /home/ji/work/program/stock
python - <<'PY'
import json, pathlib
base = pathlib.Path("project/data/runs/debug")
for label, exp in (("baseline", "<baseline_id>"), ("challenger", "<challenger_id>")):
    d = json.loads((base / exp / "stability_report.json").read_text())
    print(f"=== {label} {exp} ===")
    print("status:", d.get("research_status"), "conclusion:", d.get("stability_conclusion"))
    print(json.dumps(d, ensure_ascii=False, indent=2)[:4000])
    print()
PY
```

- [ ] **Step 2: 写报告**

报告须包含：

1. **成本口径修正的影响**：Task 2 的三情景对照表；`zero_cost` 未变动（对照通过）与 `n_fills` 不变的事实；指向 `2026-09-11-cost-basis.md`。
2. **两侧诊断的稳定性结论**：`research_status` 与 `stability_conclusion` 逐一并列。
3. **逐 fold、逐情景的判定依据**：`stability-v1` 的三条阈值——≥5 个已执行 fold、正收益 fold 比例 ≥ 0.60、最差 fold 自然年收益 > −0.10——**逐情景**列出实测值与是否通过。STABLE 是全部已声明情景（`zero_cost`/`commission_tax`/`full_cost`）的**合取**。
4. **明确的信任级别**：本次是 **ENGINEERING 诊断，UNTRUSTED**；**不是**可信绩效声明。
5. **幸存者偏差声明**：股票池来自 `security_master` 的现存符号，**幸存者偏差仍然存在**，walk-forward 不消除它。
6. **一次性挑战未执行的记录**：holdout 保留给配置定稿后；给出再启动条件（两侧结论稳定 + 成本口径已修正）。
7. **现金拖累说明**：等权 Top-10 按整手（`lot_size: 100`）下单，闲置现金拖累收益；该效应在两侧同时存在。

报告不得包含 token、绝对路径、账号。

- [ ] **Step 3: 提交**

```bash
cd /home/ji/work/program/stock
git add docs/operations/2026-09-11-stability-and-cost-basis.md
git commit -m "docs: report the walk-forward engineering stability conclusions"
```

---

### Task 6: 两侧正式 walk-forward 运行（方案二完成后）

**前置（硬性）**：方案二已执行完，`project/data/acceptance/` 下已存在 dataset `af5799ae…`（或当时的 `CURRENT`）的 ACCEPTED 验收记录。
**若无 ACCEPTED 记录，本任务无法开始**——`runner.py:682-692` 会在 RESEARCH 模式下走 `_resolve_acceptance` 并失败。

**Files:**
- Modify: `project/configs/experiments/momentum_60d_wf_real_baseline.yml`、`momentum_60d_wf_real_challenger.yml`（绑定 `data_acceptance_id`）

**Interfaces:**
- Consumes: 方案二产出的 `acceptance_id`；Task 4 已验证可运行的两份规格
- Produces: `data/experiments/<id>/stability_report.json` × 2（正式、可发布）

- [ ] **Step 1: 确认验收记录存在并取 id**

```bash
cd /home/ji/work/program/stock
python -m stock_quant data acceptance show --version af5799ae4e62f94210e6751473fed8e14e38252fd03613baff4f6bb3af4a6b70 --root project
```

（若 `CURRENT` 已不是 `af5799ae…`，用当时的 `CURRENT` 版本。）
Expected: 至少一行以 `ACCEPTED` 结尾。记下该行的 `acceptance_id`。
若输出 `UNACCEPTED` 或只有 `REJECTED`：**停止**，方案二尚未落地。

- [ ] **Step 2: 绑定验收记录**

在每份规格里把 `data_acceptance_id: null` 改为 Step 1 记下的 id。

- [ ] **Step 3: 跑等权基线的正式运行**

```bash
cd /home/ji/work/program/stock
python -m stock_quant research run \
  --spec configs/experiments/momentum_60d_wf_real_baseline.yml --root project
```

Expected: `experiment_id=<id>`、`published=project/data/experiments/<id>`、`research_status=<...>`、`stability_conclusion=<STABLE|UNSTABLE|INCONCLUSIVE>`、`trust=TRUSTED`，退出码 0。

- [ ] **Step 4: 跑缓冲式挑战者的正式运行**

（同样一次只跑一个进程。）

```bash
cd /home/ji/work/program/stock
python -m stock_quant research run \
  --spec configs/experiments/momentum_60d_wf_real_challenger.yml --root project
```

Expected: 同 Step 3，`trust=TRUSTED`，退出码 0。

- [ ] **Step 5: 确认 holdout 仍然为空（一次性挑战未被执行）**

```bash
cd /home/ji/work/program/stock
find project/data -iname "*challenge*" -o -iname "*holdout*" | head -20
```

Expected: **没有任何输出**（或只有不相关的既有文件）。若出现 challenge/holdout 注册表条目，说明一次性挑战被触发过——立即停止并报告 owner。

- [ ] **Step 6: 把正式结论追加进报告**

在 `docs/operations/2026-09-11-stability-and-cost-basis.md` 追加"正式结论"一节：两侧的 `experiment_id`、`research_status`、`stability_conclusion`、`trust=TRUSTED`，并明确区分于前一节的诊断级 UNTRUSTED 结论。幸存者偏差声明仍然适用。

- [ ] **Step 7: 提交**

```bash
cd /home/ji/work/program/stock
git add project/configs/experiments/momentum_60d_wf_real_baseline.yml \
        project/configs/experiments/momentum_60d_wf_real_challenger.yml \
        docs/operations/2026-09-11-stability-and-cost-basis.md
git commit -m "feat: run the formal walk-forward stability studies on both sides"
```

---

## 自审记录

**规格覆盖**：设计稿六节 → Task 1（①成本修正）、Task 2+3（②证据文档 + 验收标准 3）、Task 4（③诊断）、Task 6（④正式）、Task 5（⑤报告）、Global Constraints（非目标：不打挑战 / 不改 policy / 不建模过户费 / 不修资金规模）。

**与设计稿的偏差（已在上文"前提与依赖"修正）**：设计稿称诊断"仅需成本修正，`runner.py:844` 表明可跑"——**错**。`runner.py:1362-1367` 在无冻结宇宙定义时于 schedule 阶段硬失败，故 Task 4 依赖方案一。此处以代码为准。

**占位符扫描**：无 TBD/TODO。Task 2/4/5/6 中的 `<new_id>`、`<baseline_id>`、`<challenger_id>` 是**运行期才产生的实验 id**，各自的前一步骤显式要求"记下 id"，不是未填的占位符。

**类型一致性**：`load_project_config`、`CostModel.from_config`、`stamp_tax_rate`/`commission_rate`/`slippage` 的签名与 `runner.py`/`costs.py`/`config.py` 的实际定义一致。`_PROJECT_ROOT` 的 `parents[2]` 对应 `tests/unit/test_costs.py` → repo root。

**风险**：Task 2 的重跑若 `zero_cost` 出现非零变动，即判定测量作废——该情景是内置对照组，不通过就不报告数字。
