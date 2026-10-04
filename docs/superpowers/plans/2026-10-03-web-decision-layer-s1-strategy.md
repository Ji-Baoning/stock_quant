# Web 决策层 · S1 策略层实施计划（端点 + 策略列表/详情 + ECharts + /reports 下线）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 v3 规格 §7.2 的四个只读端点与策略列表/详情两页：决策者在 web 内直接看到稳定性结论、OOS 指标、逐折净值曲线（含基准同图）与全部溯源哈希；`/reports` 下线（裁定 7）。**零发布产物变更、零指标计算**——端点只读已发布产物，前端只渲染。

**Architecture:** 后端四个 GET 全部挂在既有只读服务（ADR-021：GET-only、环回、错误信封 pin I8）：`/experiments/{id}/results` 聚合三种已发布 JSON、`/experiments/{id}/folds/{fold_id}/equity` 只服务 manifest 声明图内的逐折净值 parquet、`/experiments/summaries` 聚合列表行（含从已发布 `experiment_spec.yml` 读 `hypothesis`，零 manifest 契约变更）、`/datasets/{v}/benchmark` 从 pin 住的数据集版本读 `daily_bar` 的基准行（**基准不是独立表**，是 `daily_bar` 里 `symbol` ∈ benchmark_symbols 的行）。前端新增 ECharts 按需引入（唯一运行时依赖增量，裁定 3）与两个页面；图表纪律：**逐折曲线、绝不拼接、正式实验无"全期策略净值"**（walk-forward 设计 121 行禁令的 UI 版）。

**Tech Stack:** 后端：FastAPI/pydantic 既有栈零新依赖（pandas 已是服务依赖，`service/tables.py:20` 先例）。前端：echarts（按需 `echarts/core` + Line/Bar + Canvas，精确锁版，实施日以 `npm view echarts version` 为准）。

**Spec:** [2026-10-03-web-portal-decision-layer-design.md](../specs/2026-10-03-web-portal-decision-layer-design.md)（v3 定稿）§5.2、§5.3、§7（全部）、§8 S1 行、§9 不变量对照、§10 裁定 2/3/7。前置计划：[2026-10-03-web-decision-layer-first-batch.md](2026-10-03-web-decision-layer-first-batch.md)（组件库 + 决策台①③；本计划消费其 DataTable/StateBadge/KpiCard/EmptyState/ProvenanceStrip 类组件）。

## 开工前置条件（硬门，未满足不得开工）

1. **rights-issue 已落地**（[2026-10-03-rights-issue-booking-implementation.md](2026-10-03-rights-issue-booking-implementation.md) 全部完成）；
2. **首个正式实验已发布**：`data/experiments/<64-hex>/` 存在且含 `experiment_manifest.json`、`metrics.json`、`stability_report.json`、`folds/<fold_id>/…`——Task 1 Step 1 的"实例对账"以此真实实例为对象（**契约 ≠ 实例** 缺口在此闭合，owner 复验 2026-10-03 第 3 点）；
3. **S0b Task 1（DataTable 列插槽 + `rowTestid`）已合入**：Task 7 的策略列表页统一用 DataTable + 列插槽渲染，禁止回落原生 `<table>`——该扩展是 S0b 的正式交付物（owner 审核 2026-10-03 从括号附注提升为正式交付物）。**先 S0b 后 S1 是真实顺序约束，不是建议**；鉴于本计划的硬门（第 1、2 条）耗时更长，S0b 有充分时间先行。

## Global Constraints

- **零发布产物变更**：不改 `src/stock_quant/research/`、`data_model/` 的任何产物契约（`REQUIRED_ARTIFACTS`/`FOLD_ARTIFACTS`/`_assert_artifact_tree` 一概不动）；Task 9 以 diff 证明。
- **服务边界（ADR-021）**：新端点全部 GET、只读、环回；服务不 import research/data_pipeline（`tests/service/test_dependency_boundary.py` 会守住）；不新增服务依赖。
- **pin 纪律**：pin I5（`/experiments` 五字段原样不动）、I6（报告探测语义迁移不改变）、I8（新错误码走既有信封：`results_not_found`/`fold_not_found`/`artifact_not_found`）、I4(a)（表预览参数不动——基准端点是**新端点自带窗口参数**，规格 §7.2 明示）。
- **前端零金融计算（规格 §7.4）**：允许的渲染变换只有"单条已发布净值序列的归一化（÷initial_equity）与逐点回撤"；一切指标数字逐字来自端点；禁止跨序列计算与拼接。
- **图表禁令**：无任何跨折拼接曲线；正式 walk-forward 实验不渲染全期策略净值（数据不存在）；逐折图表一次只画一折一情景。
- **ECharts 是唯一依赖增量**；`package.json` 锁精确版本（无 `^`/`~`），lock 入仓。
- 展示纪律（P5 §10.2 延续）：真实状态词、错误只走 `toDisplayError()`、空态诚实（"无 walk-forward 结论"对非 WF 实验显式标注）、不发明投资建议词汇。
- 每任务一提交，英文提交信息 + `Co-Authored-By: Claude Code <noreply@anthropic.com>`；开工 `git status` 保护 WIP。

## 开工前必须知道的实现形态（2026-10-03 实查，全部已核）

1. **已发布 JSON 的真实结构（S1 的契约层锚点）**：
   - `experiment_manifest.json`（`registry.py:91`）：`experiment_id, status(ACCEPTED|REJECTED), dataset_version, universe_version, universe_id?, universe_rules_version?, universe_membership_table_sha256?, data_acceptance_id?, code_commit?, strategy_snapshot_sha256, experiment_snapshot_sha256, data_environment_snapshot_sha256, stability_conclusion?, stability_policy_hash?, fold_schedule_sha256?, fold_outcomes_sha256?, evaluation_reason?, artifacts: {相对路径: sha256}`。
   - `metrics.json`（`runner.py:2165`）：`meta{run_id, experiment_id, spec(冻结 spec dump，含 hypothesis 与 cost_scenarios), dataset_version, universe_version, universe, code_commit, config_file_hashes, run_input_digest, initial_cash, benchmark_symbols, execution_pipeline}` + `walk_forward{research_status, stability_conclusion, stability_policy_hash, schedule, scenario_aggregates[], buffered?}` + `corporate_action_trust` + `evaluation{status, reason}`（ENGINEERING 信任模式的 evaluation.status=UNTRUSTED）。
   - `stability_report.json`（`runner.py:2076`）：`research_status, stability_conclusion, stability_policy_hash, stability_policy_version, thresholds, integrity_failures[], skipped_fold_ids[], reasons[], schedule{requested_start, requested_end, fold_count, boundary_count, boundaries[], fold_schedule_sha256, fold_outcomes_sha256}, fold_statuses[{fold_id, status, reason_code}], scenario_results[](=FoldMetrics dump), scenario_aggregates[{scenario, …AggregateOOSMetrics}], fold_metrics[], experiment_id, dataset_version, universe_version, buffered?`。
   - `FoldMetrics`（`walk_forward/metrics.py:110`）字段全表：`fold_id, scenario, artifact_complete, first_trading_day, last_trading_day, observation_count, fold_calendar_return, annualized_volatility, sharpe_zero_rf, per_fold_max_drawdown, gross_return_before_explicit_cost, net_return, explicit_cost_drag, slippage_impact, total_explicit_cost, initial_equity, explicit_cost_ratio, submitted_order_count, reject_rate, fully_rejected_order_count, partially_filled_order_count, unfilled_quantity_rate, slippage_estimate, turnover, turnover_version, turnover_numerator, turnover_denominator`。
   - `AggregateOOSMetrics`（metrics.py:158）：`aggregate_return, annualized_return, annualized_volatility, sharpe_zero_rf, oos_return_observations, annualization_observations`（**无任何跨折回撤/Calmar 字段，模型层已禁**）。
   - 逐折净值：`folds/<64hex>/equity.parquet` 列 = `trade_date, initial_equity, net_equity_after_cost`（canonical）；`folds/<64hex>/backtest/<scenario>/equity.parquet` 列 = `trade_date, cash, market_value, net_equity_after_cost`。
   - `experiment_spec.yml` 是已声明根产物（含 `hypothesis`，`spec.py:159`）——服务读它不需要 research import。
2. **服务模式**：路由挂 `prefix="/api/v1"`；错误类继承 `ServiceError`（`errors.py`：`status_code` + `code` 类属性）；响应是 pydantic BaseModel；`request.app.state.project_root` 定位 project；JSON 读取用 `datasets.read_json_or_fail(path, ErrorClass, what)`；`app.py:122-125` 注册新 router 需加一行。`UnknownExperiment(404 experiment_not_found)` 已存在，`ReportNotFound(404 report_not_found)` 已存在。
3. **服务测试模式**（`tests/service/`）：conftest 提供 `service_project`（真实 DatasetPublisher 发布的 11 表数据集 + `publish_experiment()` 发布实验）。现有 `publish_experiment` 只写 5 字段 manifest + report.html——Task 1 扩展它支持 walk-forward 产物集（stability_report/metrics/folds 声明树）。`tests/service/test_dependency_boundary.py` 与 `test_contract_security.py` 自动守住"不 import research"与契约面。
4. **基准事实**：基准 = `daily_bar` 表中 `symbol` ∈ benchmark_symbols（默认 000300.SH）的行（`runner._load_market` 同一读法）；表预览端点已有 symbol 过滤先例（`tables.py`）。
5. **前端既有件**：组件库（第一批 Task 2–5：StateBadge/KpiCard/DataTable/EmptyState/Skeleton/Card）、决策台与导航分组（NAV_GROUPS，"策略"组此前不渲染）；`api/client.ts` 10 方法 + provide/inject；测试离线 fake client（`tests/helpers.ts`）+ e2e `page.route` mock。
6. **`/reports` 现状**：路由 `/reports` + `ReportsPage.vue`（68 行链接农场）+ `tests/reports-page.spec.ts` + e2e reports 用例——下线动作 = 路由删除、页面删除、pin I6 探测断言迁移到策略详情页测试、e2e 用例改写。
7. **canonical 情景规则**：`full_cost` 优先、否则最后一个声明情景（`models.py CANONICAL_SCENARIO` 注释）。

## 文件结构

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `src/stock_quant/service/results.py` | 新建 | `/experiments/{id}/results`、`/experiments/summaries`、`/experiments/{id}/folds/{fold_id}/equity` 三个路由 |
| `src/stock_quant/service/benchmarks.py` | 新建 | `/datasets/{v}/benchmark` 路由（daily_bar 基准行 + 窗口参数） |
| `src/stock_quant/service/errors.py` | 修改 | 新增 `ResultsNotFound`/`FoldNotFound`/`ArtifactNotFound`（404） |
| `src/stock_quant/service/app.py` | 修改 | 注册两个新 router |
| `tests/service/conftest.py` | 修改 | `publish_experiment` 支持 walk-forward 产物集 |
| `tests/service/test_results_endpoints.py` | 新建 | 三个实验端点契约测试 |
| `tests/service/test_benchmark_endpoint.py` | 新建 | 基准端点契约测试 |
| `web/package.json` | 修改 | +echarts（唯一依赖增量） |
| `web/src/api/types.ts` | 修改（只增） | 四端点响应类型 |
| `web/src/api/client.ts` | 修改（只增） | 四个新方法 |
| `web/src/components/LineChart.vue`、`BarChart.vue` | 新建 | ECharts 薄封装（按需注册） |
| `web/src/pages/StrategiesPage.vue` | 新建 | 策略列表 |
| `web/src/pages/StrategyDetailPage.vue` | 新建 | 策略详情 |
| `web/src/router.ts` | 修改 | 策略组上线、`/reports` 下线 |
| `web/src/pages/ReportsPage.vue`、`web/tests/reports-page.spec.ts` | 删除 | 裁定 7（断言迁移进新测试） |
| `web/tests/strategies-page.spec.ts`、`strategy-detail-page.spec.ts`、`charts.spec.ts` | 新建 | 页面/组件测试 |
| `web/e2e/portal.spec.ts` | 修改 | reports 用例改写为 strategies 用例 |

---

### Task 1: `/experiments/{id}/results` 聚合端点

**Files:**
- Create: `src/stock_quant/service/results.py`（本任务先建模块与 results 端点；Task 2/3 扩同模块）
- Modify: `src/stock_quant/service/errors.py`、`app.py`
- Test: `tests/service/conftest.py`（扩展）、`tests/service/test_results_endpoints.py`

**Interfaces:**
- Produces: `GET /api/v1/experiments/{id}/results` → 200 `{experiment_id, manifest: dict, metrics: dict|null, stability_report: dict|null}`（manifest 必有；metrics/stability_report 缺文件时为 null——"以声明图为准，缺什么显什么缺失"）；404 `experiment_not_found`（未知 id）/`results_not_found`（目录存在但 manifest 缺）；错误信封 pin I8。Task 6/8 消费。

- [ ] **Step 1: 实例对账（硬门的核心步骤，先于一切代码）**

对真实已发布实验执行只读对账并记录结论（作为 fixture 的字段依据）：

```bash
python - <<'EOF'
import json, pathlib
root = pathlib.Path("project/data/experiments")
for d in sorted(root.iterdir()):
    m = json.loads((d/"experiment_manifest.json").read_text())
    print(d.name, sorted(m["artifacts"])[:8], "stability" in str(m))
    for name in ("metrics.json", "stability_report.json"):
        p = d/name
        if p.is_file():
            payload = json.loads(p.read_text())
            print(" ", name, sorted(payload)[:12])
    break
EOF
```

期望：与"开工前"第 1 条的结构逐字段一致；**任何差异**（字段名/嵌套/可空性）修正本计划 Task 6 的 TS 类型与 Task 1 Step 3 的响应模型，并以提交信息记录"实例对账修正"。

- [ ] **Step 2: 扩展 fixture 并写失败测试**

`tests/service/conftest.py` 的 `publish_experiment` 增加参数 `walk_forward: bool = False`；为 True 时额外写（结构与 Step 1 实例一致的最小合成载荷，`experiment_id` 用 `"e"*64`）：

```python
    if walk_forward:
        (directory / "experiment_spec.yml").write_text(
            "hypothesis: fixture hypothesis\n", encoding="utf-8"
        )
        (directory / "metrics.json").write_text(
            json.dumps({
                "meta": {"experiment_id": experiment_id, "spec": {"hypothesis": "fixture hypothesis", "cost_scenarios": ["zero_cost", "full_cost"]}, "benchmark_symbols": ["000300.SH"], "dataset_version": dataset_version},
                "walk_forward": {"research_status": "COMPLETED", "stability_conclusion": "STABLE", "stability_policy_hash": "p"*16, "schedule": {}, "scenario_aggregates": [{"scenario": "full_cost", "aggregate_return": 0.1, "annualized_return": 0.1, "annualized_volatility": 0.2, "sharpe_zero_rf": 1.5, "oos_return_observations": 100, "annualization_observations": 250}]},
                "evaluation": {"status": "ACCEPTED", "reason": "fixture"},
            }),
            encoding="utf-8",
        )
        (directory / "stability_report.json").write_text(
            json.dumps({
                "research_status": "COMPLETED", "stability_conclusion": "STABLE",
                "stability_policy_hash": "p"*16, "stability_policy_version": "stability-v1",
                "thresholds": {}, "reasons": [],
                "fold_statuses": [{"fold_id": "f"*64, "status": "EXECUTED", "reason_code": None}],
                "scenario_results": [], "scenario_aggregates": [{"scenario": "full_cost", "aggregate_return": 0.1, "annualized_return": 0.1, "annualized_volatility": 0.2, "sharpe_zero_rf": 1.5, "oos_return_observations": 100, "annualization_observations": 250}],
                # canonical 情景至少一条 fold_metrics：summaries 的
                # display_extremes 是从这里做展示级 max/mean 的（Task 3 断言
                # 它非 null，空表会得到 null）。
                "fold_metrics": [{
                    "fold_id": "f" * 64, "scenario": "full_cost",
                    "per_fold_max_drawdown": -0.08, "reject_rate": 0.02, "turnover": 0.35,
                }],
                "experiment_id": experiment_id,
                "dataset_version": dataset_version, "universe_version": "u"*64,
            }),
            encoding="utf-8",
        )
        fold_dir = directory / "folds" / ("f" * 64)
        (fold_dir / "backtest" / "full_cost").mkdir(parents=True)
        pd.DataFrame({
            "trade_date": [date(2026, 1, 5), date(2026, 1, 6)],
            "cash": [1_000_000.0, 999_000.0],
            "market_value": [0.0, 1_000.0],
            "net_equity_after_cost": [1_000_000.0, 1_000_000.0],
        }).to_parquet(fold_dir / "backtest" / "full_cost" / "equity.parquet", index=False)
        pd.DataFrame({
            "trade_date": [date(2026, 1, 5), date(2026, 1, 6)],
            "initial_equity": [1_000_000.0, 1_000_000.0],
            "net_equity_after_cost": [1_000_000.0, 1_000_000.0],
        }).to_parquet(fold_dir / "equity.parquet", index=False)
```

（conftest 顶部已 import 的 `json`/`pd`/`date` 复用；manifest 的 `artifacts` 映射由各测试按需补写以覆盖声明图校验。）

`tests/service/test_results_endpoints.py`：

```python
"""Contract tests for the S1 read-only experiment results endpoints."""

from pathlib import Path

from fastapi.testclient import TestClient

from .conftest import publish_experiment


def test_results_aggregates_the_published_json_payloads(service_project: Path, client: TestClient):
    publish_experiment(service_project, "v1", walk_forward=True)
    response = client.get("/api/v1/experiments/" + "e" * 64 + "/results")
    assert response.status_code == 200
    body = response.json()
    assert body["experiment_id"] == "e" * 64
    assert body["manifest"]["status"] == "ACCEPTED"
    assert body["metrics"]["walk_forward"]["stability_conclusion"] == "STABLE"
    assert body["stability_report"]["stability_policy_hash"] == "p" * 16


def test_results_returns_null_for_artifacts_the_experiment_never_published(service_project, client):
    publish_experiment(service_project, "v1")  # 无 walk_forward 产物
    response = client.get("/api/v1/experiments/" + "e" * 64 + "/results")
    assert response.status_code == 200
    assert response.json()["metrics"] is None
    assert response.json()["stability_report"] is None


def test_results_unknown_experiment_is_the_stable_envelope(service_project, client):
    response = client.get("/api/v1/experiments/" + "9" * 64 + "/results")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "experiment_not_found"
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/service/test_results_endpoints.py -q`
Expected: FAIL——路由不存在（404 无信封或 AttributeError）。

- [ ] **Step 4: 实现端点**

`errors.py` 追加：

```python
class ResultsNotFound(ServiceError):
    status_code = 404
    code = "results_not_found"


class FoldNotFound(ServiceError):
    status_code = 404
    code = "fold_not_found"


class ArtifactNotFound(ServiceError):
    status_code = 404
    code = "artifact_not_found"
```

`results.py`：

```python
"""Read-only aggregation over an experiment's published JSON artifacts (v3 §7.2).

The service never recomputes anything: it reads the files a published
experiment actually carries and reports absent artifacts as ``null``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from pydantic import BaseModel

from stock_quant.service.datasets import read_json_or_fail
from stock_quant.service.errors import (
    ExperimentManifestUnreadable,
    ResultsNotFound,
    UnknownExperiment,
)

_EXPERIMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ExperimentResultsResponse(BaseModel):
    experiment_id: str
    manifest: dict[str, Any]
    metrics: dict[str, Any] | None = None
    stability_report: dict[str, Any] | None = None


def _experiment_dir(request: Request, experiment_id: str) -> Path:
    root = (Path(request.app.state.project_root) / "data" / "experiments").resolve()
    directory = (root / experiment_id).resolve()
    if directory.parent != root or not directory.is_dir():
        raise UnknownExperiment(f"no published experiment {experiment_id!r}")
    return directory


def _read_optional_json(directory: Path, name: str) -> dict[str, Any] | None:
    """Read one published JSON artifact; ``None`` when it was never published.

    A file that exists but cannot be read is *not* "absent": it fails closed
    with the same error envelope the manifest read uses, so a corrupt
    artifact never silently degrades into a "missing" claim in the UI.
    """
    path = directory / name
    if not path.is_file():
        return None
    payload = read_json_or_fail(path, ExperimentManifestUnreadable, name)
    return payload if isinstance(payload, dict) else None


router = APIRouter(prefix="/api/v1", tags=["experiment-results"])


@router.get("/experiments/{experiment_id}/results", response_model=ExperimentResultsResponse)
def experiment_results(
    experiment_id: Annotated[str, PathParam(pattern=_EXPERIMENT_ID_RE.pattern)],
    request: Request,
) -> ExperimentResultsResponse:
    directory = _experiment_dir(request, experiment_id)
    manifest = _read_optional_json(directory, "experiment_manifest.json")
    if manifest is None:
        raise ResultsNotFound(
            f"experiment {experiment_id!r} has no readable experiment_manifest.json"
        )
    return ExperimentResultsResponse(
        experiment_id=experiment_id,
        manifest=manifest,
        metrics=_read_optional_json(directory, "metrics.json"),
        stability_report=_read_optional_json(directory, "stability_report.json"),
    )
```

`app.py` 在 `app.include_router(experiments.router, …)` 之后追加两行（benchmarks router 在 Task 4 建好后同样注册）：

```python
    app.include_router(results.router, responses=error_responses)
```

（import 区补 `from stock_quant.service import results`。）

- [ ] **Step 5: 运行确认通过（含边界守卫）**

Run: `pytest tests/service/test_results_endpoints.py tests/service/test_dependency_boundary.py tests/service/test_contract_security.py -q`
Expected: PASS——新端点契约绿；依赖边界（服务不 import research）与安全契约不破。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/service/results.py src/stock_quant/service/errors.py src/stock_quant/service/app.py tests/service/conftest.py tests/service/test_results_endpoints.py
git commit -m "feat(service): aggregate published experiment JSON for the strategy UI

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: `/experiments/{id}/folds/{fold_id}/equity` 逐折净值端点

**Files:**
- Modify: `src/stock_quant/service/results.py`（追加路由）
- Test: `tests/service/test_results_endpoints.py`（追加）

**Interfaces:**
- Produces: `GET /api/v1/experiments/{id}/folds/{fold_id}/equity?scenario=` → 200 `{experiment_id, fold_id, scenario, rows: [{trade_date, …列白名单}]}`；`scenario` 缺省 = canonical（`full_cost` 优先，否则 manifest `metrics.meta.spec.cost_scenarios` 末位）；**只服务 manifest.artifacts 声明图内的文件**（`folds/{fold_id}/equity.parquet` 或 `folds/{fold_id}/backtest/{scenario}/equity.parquet`），越界一律 404（`fold_not_found`/`artifact_not_found`）；列白名单 = `trade_date/initial_equity/cash/market_value/net_equity_after_cost`（文件里实际有的列）。Task 8 消费。

- [ ] **Step 1: 写失败测试**

```python
def test_fold_equity_serves_only_declared_artifacts(service_project, client):
    # 该折在声明图里“存在”（声明了别的折产物），但没有声明任何 equity 文件：
    # 磁盘上 fixture 写好的 equity.parquet 不属于声明图 → 404 artifact_not_found。
    # （若连 folds/<id>/ 都没声明，语义是“没有这一折”，走 fold_not_found——
    # 那条路径由下面第三个用例覆盖。）
    directory = publish_experiment(service_project, "v1", walk_forward=True)
    import json as _json
    fold = "f" * 64
    key = f"folds/{fold}/backtest/full_cost/rebalance_decisions.parquet"
    manifest_path = directory / "experiment_manifest.json"
    manifest = _json.loads(manifest_path.read_text())
    manifest["artifacts"] = {key: "0" * 64}
    manifest_path.write_text(_json.dumps(manifest))
    response = client.get(f"/api/v1/experiments/{'e' * 64}/folds/{fold}/equity")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_found"


def test_fold_equity_serves_a_declared_scenario_artifact(service_project, client):
    directory = publish_experiment(service_project, "v1", walk_forward=True)
    import hashlib
    import json as _json
    manifest_path = directory / "experiment_manifest.json"
    manifest = _json.loads(manifest_path.read_text())
    key = f"folds/{'f' * 64}/backtest/full_cost/equity.parquet"
    manifest["artifacts"] = {key: hashlib.sha256((directory / key).read_bytes()).hexdigest()}
    manifest_path.write_text(_json.dumps(manifest))
    response = client.get(f"/api/v1/experiments/{'e' * 64}/folds/{'f' * 64}/equity?scenario=full_cost")
    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] == "full_cost"
    assert [row["net_equity_after_cost"] for row in body["rows"]] == [1_000_000.0, 1_000_000.0]
    assert set(body["rows"][0]) == {"trade_date", "cash", "market_value", "net_equity_after_cost"}


def test_fold_equity_unknown_fold_is_fold_not_found(service_project, client):
    publish_experiment(service_project, "v1", walk_forward=True)
    response = client.get(f"/api/v1/experiments/{'e' * 64}/folds/{'a' * 64}/equity")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "fold_not_found"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/service/test_results_endpoints.py -q -k fold_equity`
Expected: FAIL——路由不存在。

- [ ] **Step 3: 实现路由**

`results.py` 追加：

```python
import hashlib

import pandas as pd
from fastapi import Query

from stock_quant.service.errors import ArtifactNotFound, FoldNotFound

_EQUITY_COLUMNS = (
    "trade_date",
    "initial_equity",
    "cash",
    "market_value",
    "net_equity_after_cost",
)


class FoldEquityResponse(BaseModel):
    experiment_id: str
    fold_id: str
    scenario: str | None
    rows: list[dict[str, Any]]


def _canonical_scenario(directory: Path, manifest: dict[str, Any]) -> str | None:
    metrics = _read_optional_json(directory, "metrics.json")
    scenarios: list[str] = []
    if metrics is not None:
        spec = metrics.get("meta", {}).get("spec", {})
        scenarios = [str(name) for name in spec.get("cost_scenarios", [])]
    if "full_cost" in scenarios:
        return "full_cost"
    return scenarios[-1] if scenarios else None


def _json_safe(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


@router.get(
    "/experiments/{experiment_id}/folds/{fold_id}/equity",
    response_model=FoldEquityResponse,
)
def fold_equity(
    experiment_id: Annotated[str, PathParam(pattern=_EXPERIMENT_ID_RE.pattern)],
    fold_id: Annotated[str, PathParam(pattern=r"^[0-9a-f]{64}$")],
    request: Request,
    scenario: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_]+$")] = None,
) -> FoldEquityResponse:
    directory = _experiment_dir(request, experiment_id)
    manifest = _read_optional_json(directory, "experiment_manifest.json") or {}
    declared: dict[str, str] = {
        str(key): str(value) for key, value in (manifest.get("artifacts") or {}).items()
    }
    chosen = scenario if scenario is not None else _canonical_scenario(directory, manifest)
    if chosen is None:
        raise ArtifactNotFound(
            f"experiment {experiment_id!r} declares no cost scenario to serve"
        )
    relative = f"folds/{fold_id}/backtest/{chosen}/equity.parquet"
    if relative not in declared:
        # 退回 canonical 文件也必须在声明图内（fixture 校验同一规则）。
        relative = f"folds/{fold_id}/equity.parquet"
        if relative not in declared:
            if not any(key.startswith(f"folds/{fold_id}/") for key in declared):
                raise FoldNotFound(f"experiment has no declared fold {fold_id!r}")
            raise ArtifactNotFound(
                f"fold {fold_id!r} has no declared equity artifact for {chosen!r}"
            )
    # 双保险：`relative` 只可能来自声明图的键，但仍显式确认它落在该折目录内
    # （`fold_id` 与 `chosen` 都已过 64-hex / [A-Za-z0-9_]+ 的路径参数正则）。
    path = (directory / relative).resolve()
    fold_root = (directory / "folds" / fold_id).resolve()
    if fold_root not in path.parents:
        raise ArtifactNotFound(f"refusing a path outside fold {fold_id!r}")
    if not path.is_file():
        raise ArtifactNotFound(f"declared artifact {relative} is missing on disk")
    if hashlib.sha256(path.read_bytes()).hexdigest() != declared[relative]:
        raise ArtifactNotFound(f"declared hash mismatch for {relative}")
    frame = pd.read_parquet(path)
    rows = [
        {key: _json_safe(record[key]) for key in _EQUITY_COLUMNS if key in record}
        for record in frame.to_dict("records")
    ]
    served_scenario = chosen if f"backtest/{chosen}" in relative else None
    return FoldEquityResponse(
        experiment_id=experiment_id,
        fold_id=fold_id,
        scenario=served_scenario,
        rows=rows,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/service/test_results_endpoints.py tests/service/test_dependency_boundary.py -q`
Expected: PASS（含"声明图外文件 404"与哈希校验路径）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/service/results.py tests/service/test_results_endpoints.py
git commit -m "feat(service): serve declared per-fold equity paths only

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: `/experiments/summaries` 列表聚合端点

**Files:**
- Modify: `src/stock_quant/service/results.py`（追加路由）
- Test: `tests/service/test_results_endpoints.py`（追加）

**Interfaces:**
- Produces: `GET /api/v1/experiments/summaries` → `{summaries: [ExperimentSummaryRow]}`；每行 = pin I5 五字段 + `hypothesis: str|null`（读已发布 `experiment_spec.yml` 的 `hypothesis` 键，yaml.safe_load；文件缺失/无键 → null——零 manifest 契约变更，规格 §7.3）+ `stability_conclusion/stability_policy_hash/research_status: str|null`（metrics.json 的 walk_forward 块）+ `canonical_scenario: str|null` + `aggregates: [{scenario, aggregate_return, annualized_return, annualized_volatility, sharpe_zero_rf, oos_return_observations, annualization_observations}]|null`（metrics.json walk_forward.scenario_aggregates 原样）+ `display_extremes: {max_per_fold_drawdown: float|null, max_reject_rate: float|null, mean_turnover: float|null}|null`（对 canonical 情景的 stability_report.fold_metrics 做展示级 max/mean——只聚合已发布原子值，与 `/datasets` 汇总 manifest 同性质）。Task 7 消费。

- [ ] **Step 1: 写失败测试**

```python
def test_summaries_carry_verdict_hypothesis_and_aggregates(service_project, client):
    publish_experiment(service_project, "v1", walk_forward=True)
    publish_experiment(service_project, "v1", experiment_id="d" * 64)  # 非 WF
    response = client.get("/api/v1/experiments/summaries")
    assert response.status_code == 200
    rows = response.json()["summaries"]
    assert len(rows) == 2
    by_id = {row["experiment_id"]: row for row in rows}
    wf = by_id["e" * 64]
    assert wf["hypothesis"] == "fixture hypothesis"
    assert wf["stability_conclusion"] == "STABLE"
    assert wf["canonical_scenario"] == "full_cost"
    assert wf["aggregates"][0]["sharpe_zero_rf"] == 1.5
    assert wf["display_extremes"] is not None
    legacy = by_id["d" * 64]
    assert legacy["hypothesis"] is None  # 未发布 experiment_spec.yml
    assert legacy["stability_conclusion"] is None
    assert legacy["aggregates"] is None
    assert legacy["display_extremes"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/service/test_results_endpoints.py -q -k summaries`
Expected: FAIL——路由不存在。

- [ ] **Step 3: 实现路由**

`results.py` 追加（import 区补 `import yaml`——项目既有依赖）：

```python
class AggregateRow(BaseModel):
    scenario: str
    aggregate_return: float | None = None
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    sharpe_zero_rf: float | None = None
    oos_return_observations: int | None = None
    annualization_observations: int | None = None


class DisplayExtremes(BaseModel):
    max_per_fold_drawdown: float | None = None
    max_reject_rate: float | None = None
    mean_turnover: float | None = None


class ExperimentSummaryRow(BaseModel):
    experiment_id: str
    status: str | None = None
    dataset_version: str | None = None
    universe_version: str | None = None
    evaluation_reason: str | None = None
    hypothesis: str | None = None
    stability_conclusion: str | None = None
    stability_policy_hash: str | None = None
    research_status: str | None = None
    canonical_scenario: str | None = None
    aggregates: list[AggregateRow] | None = None
    display_extremes: DisplayExtremes | None = None


class ExperimentSummariesResponse(BaseModel):
    summaries: list[ExperimentSummaryRow]


def _hypothesis_of(directory: Path) -> str | None:
    path = directory / "experiment_spec.yml"
    if not path.is_file():
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("hypothesis")
    return str(value) if isinstance(value, str) and value.strip() else None


def _display_extremes(
    directory: Path, canonical: str | None
) -> DisplayExtremes | None:
    if canonical is None:
        return None
    report = _read_optional_json(directory, "stability_report.json")
    if report is None:
        return None
    records = [
        record
        for record in report.get("fold_metrics", [])
        if record.get("scenario") == canonical
    ]
    if not records:
        return None
    drawdowns = [r["per_fold_max_drawdown"] for r in records if r.get("per_fold_max_drawdown") is not None]
    rejects = [r["reject_rate"] for r in records if r.get("reject_rate") is not None]
    turnovers = [r["turnover"] for r in records if r.get("turnover") is not None]
    return DisplayExtremes(
        max_per_fold_drawdown=max(drawdowns, default=None),
        max_reject_rate=max(rejects, default=None),
        mean_turnover=(sum(turnovers) / len(turnovers)) if turnovers else None,
    )


@router.get("/experiments/summaries", response_model=ExperimentSummariesResponse)
def experiment_summaries(request: Request) -> ExperimentSummariesResponse:
    root = Path(request.app.state.project_root) / "data" / "experiments"
    rows: list[ExperimentSummaryRow] = []
    children = sorted(root.iterdir()) if root.is_dir() else []
    for child in children:
        if not child.is_dir() or not _EXPERIMENT_ID_RE.fullmatch(child.name):
            continue
        manifest = _read_optional_json(child, "experiment_manifest.json") or {}
        metrics = _read_optional_json(child, "metrics.json")
        walk_forward = (metrics or {}).get("walk_forward") or {}
        canonical = _canonical_scenario(child, manifest)
        aggregates = [
            AggregateRow(scenario=str(entry.get("scenario")), **{
                key: entry.get(key)
                for key in (
                    "aggregate_return",
                    "annualized_return",
                    "annualized_volatility",
                    "sharpe_zero_rf",
                    "oos_return_observations",
                    "annualization_observations",
                )
            })
            for entry in walk_forward.get("scenario_aggregates", [])
            if isinstance(entry, dict)
        ] or None
        rows.append(
            ExperimentSummaryRow(
                experiment_id=child.name,
                status=manifest.get("status"),
                dataset_version=manifest.get("dataset_version"),
                universe_version=manifest.get("universe_version"),
                evaluation_reason=manifest.get("evaluation_reason"),
                hypothesis=_hypothesis_of(child),
                stability_conclusion=walk_forward.get("stability_conclusion"),
                stability_policy_hash=walk_forward.get("stability_policy_hash"),
                research_status=walk_forward.get("research_status"),
                canonical_scenario=canonical,
                aggregates=aggregates,
                display_extremes=_display_extremes(child, canonical),
            )
        )
    return ExperimentSummariesResponse(summaries=rows)
```

（路由顺序：实测 `service/experiments.py` 只有 `GET /experiments` 与 `GET /experiments/{experiment_id}/report`（`experiments.py:65,86`）——**不存在** `GET /experiments/{experiment_id}` 这类两段式动态路由，`/experiments/summaries` 与它们段数不同，不构成遮蔽。仍把本路由声明在同模块 `/{experiment_id}/…` 路由之前（零成本的排序纪律），并用上面 Step 1 的用例钉住 `summaries` 不会被当成 experiment_id。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/service/test_results_endpoints.py -q`
Expected: PASS（summaries + 既有 results/fold 用例全绿）。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/service/results.py tests/service/test_results_endpoints.py
git commit -m "feat(service): list-level experiment summaries with verdict and aggregates

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: `/datasets/{version}/benchmark` 基准行端点

**Files:**
- Create: `src/stock_quant/service/benchmarks.py`
- Modify: `src/stock_quant/service/app.py`（注册）
- Test: `tests/service/test_benchmark_endpoint.py`

**Interfaces:**
- Produces: `GET /api/v1/datasets/{v}/benchmark?symbol=000300.SH&start&end` → 200 `{dataset_version, requested_version, symbol, rows: [{trade_date, close}]}`；`symbol` 默认 `000300.SH`（`PRIMARY_BENCHMARK_SYMBOL` 同值，服务不 import analytics——常量本地复述并注释来源）；`start/end` 可选 ISO 日（**本端点自带窗口参数，不改 pin I4(a) 的表预览**）；行数上限 8000 防御；404 `dataset_not_found`/`unknown_table`（daily_bar 不在版本 manifest）复用既有错误；`version_echo` 与 datasets 端点同规则（解析 `current` 回显完整哈希——复用 `datasets.py` 既有解析 helper，以文件内现行函数名为准）。Task 8 消费。

- [ ] **Step 1: 写失败测试**

```python
"""Contract tests for the benchmark rows endpoint (v3 §7.2)."""

from pathlib import Path

from fastapi.testclient import TestClient


def test_benchmark_serves_daily_bar_rows_for_the_symbol(service_project: Path, client: TestClient):
    response = client.get(
        "/api/v1/datasets/current/benchmark",
        params={"symbol": "000300.SH", "start": "2026-01-05", "end": "2026-01-08"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "000300.SH"
    assert body["requested_version"] == body["dataset_version"]
    assert len(body["rows"]) >= 1
    assert set(body["rows"][0]) == {"trade_date", "close"}


def test_benchmark_window_filters_rows(service_project, client):
    full = client.get("/api/v1/datasets/current/benchmark").json()["rows"]
    narrow = client.get(
        "/api/v1/datasets/current/benchmark",
        params={"start": "2026-01-06", "end": "2026-01-06"},
    ).json()["rows"]
    assert 0 < len(narrow) < len(full)


def test_benchmark_unknown_version_uses_the_envelope(service_project, client):
    response = client.get("/api/v1/datasets/" + "9" * 64 + "/benchmark")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"
```

（fixture 数据集的 daily_bar 是否含 000300.SH 行由 conftest 的 SYMBOLS 决定——若 fixture 未写基准行，在测试内先经 `service_project` 的 standardized 目录补行不可行（已发布不可变）；此时改用 conftest SYMBOLS 中已有的某 symbol 作参数并放宽 `symbol` 断言，**端点行为不变**。以 conftest 现行 `SYMBOLS`/daily_bar 写入逻辑为准对齐参数。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/service/test_benchmark_endpoint.py -q`
Expected: FAIL——路由不存在。

- [ ] **Step 3: 实现端点**

`benchmarks.py`（读取复用 `datasets.py` 的版本解析与 `tables.py` 的 DuckDB 白名单模式；两处以文件内现行 helper 名为准——`PinnedDataset`/`pinned_dataset` 依赖与 `STANDARDIZED_SCHEMAS` 白名单）：

```python
"""Benchmark closes from one pinned dataset version (v3 §7.2).

The benchmark is not a separate table: it is ``daily_bar`` rows whose symbol
is a benchmark symbol (default 000300.SH — PRIMARY_BENCHMARK_SYMBOL's value,
restated locally so the service keeps importing neither research nor
analytics).  The window parameters belong to this dedicated endpoint only;
the table-preview contract (pin I4) is untouched.
"""

from __future__ import annotations

from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from stock_quant.service.datasets import PinnedDataset, pinned_dataset

#: Mirrors ``analytics.performance.PRIMARY_BENCHMARK_SYMBOL`` (no import).
DEFAULT_BENCHMARK_SYMBOL = "000300.SH"
_BENCHMARK_TABLE = "daily_bar"
_MAX_ROWS = 8000


class BenchmarkRow(BaseModel):
    trade_date: str
    close: float


class BenchmarkResponse(BaseModel):
    dataset_version: str
    requested_version: str
    symbol: str
    rows: list[BenchmarkRow]


router = APIRouter(prefix="/api/v1", tags=["benchmark"])


@router.get(
    "/datasets/{version}/benchmark", response_model=BenchmarkResponse
)
def benchmark_closes(
    pinned: Annotated[PinnedDataset, Depends(pinned_dataset)],
    request: Request,
    symbol: Annotated[str, Query(pattern=r"^[0-9]{6}\.(SH|SZ)$")] = DEFAULT_BENCHMARK_SYMBOL,
    start: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    end: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
) -> BenchmarkResponse:
    conditions = ["symbol = ?", "table_name = ?"]
    parameters: list[object] = [symbol, _BENCHMARK_TABLE]
    if start is not None:
        conditions.append("trade_date >= ?")
        parameters.append(start)
    if end is not None:
        conditions.append("trade_date <= ?")
        parameters.append(end)
    query = (
        "SELECT trade_date, close FROM daily_bar WHERE "
        + " AND ".join(conditions)
        + " ORDER BY trade_date LIMIT ?"
    )
    parameters.append(_MAX_ROWS)
    relation = duckdb.sql(query, params=list(parameters))
    rows = [
        BenchmarkRow(trade_date=str(record[0])[:10], close=float(record[1]))
        for record in relation.fetchall()
    ]
    return BenchmarkResponse(
        dataset_version=pinned.version,
        requested_version=pinned.requested,
        symbol=symbol,
        rows=rows,
    )
```

（`pinned.version`/`pinned.requested` 的真实属性名以 `datasets.py:53` 的 `PinnedDataset` 定义为准对齐；`table_name` 过滤若该 parquet 目录按表分文件则改为直接读 `daily_bar` 文件路径——以 `tables.py` 现行 DuckDB 读法为准，保持同一模式。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/service/test_benchmark_endpoint.py tests/service/test_tables.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/stock_quant/service/benchmarks.py src/stock_quant/service/app.py tests/service/test_benchmark_endpoint.py
git commit -m "feat(service): benchmark closes from a pinned dataset version

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: ECharts 按需引入与图表薄封装

**Files:**
- Modify: `web/package.json`（+echarts 精确锁版）
- Create: `web/src/components/LineChart.vue`、`web/src/components/BarChart.vue`
- Modify: `web/src/styles.css`（图容器样式）
- Test: `web/tests/charts.spec.ts`

**Interfaces:**
- Produces:
  - `LineChart`：props `{ labels: string[]; series: Array<{ name: string; data: number[]; dashed?: boolean }>; testid?: string; fold?: string }`；根 `data-testid` 取 `props.testid ?? "line-chart"`；有 `fold` 时渲染 `data-fold` 属性（**逐折证据**：断言"一次只画一折"依赖它）。
  - `BarChart`：props `{ labels: string[]; series: Array<{ name: string; data: Array<number|null> }>; testid?: string }`。
  - 两者都不做任何金融计算；红涨绿跌由调用方在数据序里表达（着色 prop `colorBySign?: boolean` 仅 BarChart 支持，按值正负取 `--color-up/--color-down`）。

- [ ] **Step 1: 安装依赖（精确锁版）**

Run（在 `web/`）: `npm view echarts version` → 记下版本号 X.Y.Z，然后 `npm install --save-exact echarts@X.Y.Z`
Expected: `package.json` 出现 `"echarts": "X.Y.Z"`（无 `^`）。

- [ ] **Step 2: 写失败测试**

```ts
// web/tests/charts.spec.ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPortalRouter } from "../src/router";
import LineChart from "../src/components/LineChart.vue";
import BarChart from "../src/components/BarChart.vue";

function mountWithRouter(component: Parameters<typeof mount>[0], props: Record<string, unknown> = {}) {
  return mount(component, { props, global: { plugins: [createPortalRouter()] } });
}

describe("图表薄封装（ECharts）", () => {
  it("LineChart 渲染容器并携带逐折证据属性", () => {
    const wrapper = mountWithRouter(LineChart, {
      labels: ["2026-01-05", "2026-01-06"],
      series: [
        { name: "策略（费后）", data: [1.0, 1.01] },
        { name: "基准 000300.SH", data: [1.0, 0.99], dashed: true },
      ],
      testid: "chart-net-value",
      fold: "f".repeat(64),
    });
    expect(wrapper.get('[data-testid="chart-net-value"]').attributes("data-fold")).toBe("f".repeat(64));
    expect(wrapper.props("series")).toHaveLength(2);
  });

  it("BarChart 支持按正负着色（红涨绿跌）", () => {
    const wrapper = mountWithRouter(BarChart, {
      labels: ["fold-0", "fold-1"],
      series: [{ name: "折收益", data: [0.02, -0.01] }],
      colorBySign: true,
    });
    expect(wrapper.get('[data-testid="bar-chart"]').exists()).toBe(true);
  });
});
```

- [ ] **Step 3: 运行确认失败**

Run: `npm test -- charts.spec`
Expected: FAIL——组件不存在。

- [ ] **Step 4: 实现两个封装**

```vue
<!-- web/src/components/LineChart.vue -->
<script lang="ts">
// 类型必须导出给消费方（Task 8 的详情页），而 `<script setup>` 内禁止
// ES 模块导出（Vue 编译错误）——所以类型放独立 script 块，与第一批各组件同款式。
export interface LineSeries {
  name: string;
  data: number[];
  dashed?: boolean;
}
</script>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { LineChart as EChartsLine } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([EChartsLine, GridComponent, TooltipComponent, CanvasRenderer]);

const props = withDefaults(
  defineProps<{
    labels: string[];
    series: LineSeries[];
    testid?: string;
    fold?: string;
  }>(),
  {},
);

const container = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

function render() {
  if (container.value === null) return;
  chart ??= echarts.init(container.value);
  chart.setOption({
    grid: { left: 48, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: props.labels },
    yAxis: { type: "value", scale: true },
    series: props.series.map((line) => ({
      name: line.name,
      type: "line" as const,
      data: line.data,
      showSymbol: false,
      lineStyle: line.dashed === true ? { type: "dashed" as const } : undefined,
    })),
  });
}

onMounted(render);
watch(() => [props.labels, props.series], render, { deep: true });
onBeforeUnmount(() => {
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div
    ref="container"
    class="chart-canvas"
    :data-testid="props.testid ?? 'line-chart'"
    :data-fold="props.fold"
  />
</template>
```

```vue
<!-- web/src/components/BarChart.vue -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { BarChart as EChartsBar } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([EChartsBar, GridComponent, TooltipComponent, CanvasRenderer]);

const UP = "var(--color-up)";
const DOWN = "var(--color-down)";

const props = withDefaults(
  defineProps<{
    labels: string[];
    series: Array<{ name: string; data: Array<number | null> }>;
    colorBySign?: boolean;
    testid?: string;
  }>(),
  {},
);

const container = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;

function itemColor(value: number | null): string {
  if (props.colorBySign !== true || value === null) return "var(--color-primary)";
  return value >= 0 ? UP : DOWN;
}

function render() {
  if (container.value === null) return;
  chart ??= echarts.init(container.value);
  chart.setOption({
    grid: { left: 48, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: props.labels },
    yAxis: { type: "value" },
    series: props.series.map((bars) => ({
      name: bars.name,
      type: "bar" as const,
      data: props.colorBySign
        ? bars.data.map((value) => ({
            value,
            itemStyle: { color: itemColor(value) },
          }))
        : bars.data,
    })),
  });
}

onMounted(render);
watch(() => [props.labels, props.series], render, { deep: true });
onBeforeUnmount(() => {
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div ref="container" class="chart-canvas" :data-testid="props.testid ?? 'bar-chart'" />
</template>
```

`styles.css` 追加：

```css
/* 图表容器（Task 5）：固定高度，容器缺失时不出图。 */
.chart-canvas { width: 100%; height: 260px; }
```

- [ ] **Step 5: 运行确认通过 + typecheck**

Run: `npm test -- charts.spec && npm run typecheck`
Expected: PASS（happy-dom 下 echarts.init 可用 canvas stub；若 init 抛错则测试退化为只断言容器与 props——以实际行为调整断言并保留 `data-fold` 证据断言）。

- [ ] **Step 6: 提交**

```bash
git add web/package.json web/package-lock.json web/src/components/LineChart.vue web/src/components/BarChart.vue web/src/styles.css web/tests/charts.spec.ts
git commit -m "feat(web): add ECharts line/bar wrappers with per-fold evidence

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: 前端契约层（types + client 四方法）

**Files:**
- Modify: `web/src/api/types.ts`（只增）
- Modify: `web/src/api/client.ts`（只增）
- Test: `web/tests/api-client.spec.ts`（追加）

**Interfaces:**
- Produces（TS 类型，与 Task 1–4 响应模型逐字段对齐；实例对账差异在此修正）:

```ts
/** S1：/experiments/summaries 行（pin I5 五字段 + 结论层）。 */
export interface ExperimentSummaryRow {
  experiment_id: string;
  status: string | null;
  dataset_version: string | null;
  universe_version: string | null;
  evaluation_reason: string | null;
  hypothesis: string | null;
  stability_conclusion: string | null;
  stability_policy_hash: string | null;
  research_status: string | null;
  canonical_scenario: string | null;
  aggregates: ScenarioAggregate[] | null;
  display_extremes: {
    max_per_fold_drawdown: number | null;
    max_reject_rate: number | null;
    mean_turnover: number | null;
  } | null;
}

export interface ScenarioAggregate {
  scenario: string;
  aggregate_return: number | null;
  annualized_return: number | null;
  annualized_volatility: number | null;
  sharpe_zero_rf: number | null;
  oos_return_observations: number | null;
  annualization_observations: number | null;
}

export interface ExperimentResultsResponse {
  experiment_id: string;
  manifest: Record<string, unknown>;
  metrics: Record<string, unknown> | null;
  stability_report: Record<string, unknown> | null;
}

export interface FoldEquityResponse {
  experiment_id: string;
  fold_id: string;
  scenario: string | null;
  rows: Array<{ trade_date: string } & Record<string, number | string | null>>;
}

export interface BenchmarkResponse {
  dataset_version: string;
  requested_version: string;
  symbol: string;
  rows: Array<{ trade_date: string; close: number }>;
}
```

client 新方法（`ApiClient` 接口追加四个成员 + 实现）：

```ts
  experimentSummaries(): Promise<{ summaries: ExperimentSummaryRow[] }>;
  experimentResults(experimentId: string): Promise<ExperimentResultsResponse>;
  foldEquity(experimentId: string, foldId: string, scenario: string | null): Promise<FoldEquityResponse>;
  datasetBenchmark(version: string, params: { symbol?: string; start?: string; end?: string }): Promise<BenchmarkResponse>;
```

实现（`requestJson` 复用；查询串用既有 `query` helper）：

```ts
    experimentSummaries() {
      return requestJson<{ summaries: ExperimentSummaryRow[] }>("/api/v1/experiments/summaries");
    },
    experimentResults(experimentId: string) {
      return requestJson<ExperimentResultsResponse>(
        `/api/v1/experiments/${encodeURIComponent(experimentId)}/results`,
      );
    },
    foldEquity(experimentId: string, foldId: string, scenario: string | null) {
      return requestJson<FoldEquityResponse>(
        `/api/v1/experiments/${encodeURIComponent(experimentId)}/folds/${encodeURIComponent(foldId)}/equity${query({ scenario })}`,
      );
    },
    datasetBenchmark(version: string, params: { symbol?: string; start?: string; end?: string }) {
      return requestJson<BenchmarkResponse>(
        `/api/v1/datasets/${encodeURIComponent(version)}/benchmark${query({
          symbol: params.symbol ?? null,
          start: params.start ?? null,
          end: params.end ?? null,
        })}`,
      );
    },
```

- [ ] **Step 1: 写失败测试**（追加到 `web/tests/api-client.spec.ts`，模式照抄既有用例：fake fetch → 断言 URL 与解析）

```ts
  it("S1 四端点：URL 组装与响应解析", async () => {
    const calls: string[] = [];
    const client = createApiClient({
      fetchImpl: (async (input: RequestInfo | URL) => {
        calls.push(String(input));
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }) as typeof fetch,
    });
    await client.experimentSummaries();
    await client.experimentResults("e".repeat(64));
    await client.foldEquity("e".repeat(64), "f".repeat(64), "full_cost");
    await client.datasetBenchmark("current", { start: "2026-01-05", end: "2026-01-08" });
    expect(calls).toEqual([
      "/api/v1/experiments/summaries",
      `/api/v1/experiments/${"e".repeat(64)}/results`,
      `/api/v1/experiments/${"e".repeat(64)}/folds/${"f".repeat(64)}/equity?scenario=full_cost`,
      "/api/v1/datasets/current/benchmark?start=2026-01-05&end=2026-01-08",
    ]);
  });

  it("S1 端点错误走嵌套信封（pin I8）", async () => {
    const client = createApiClient({
      fetchImpl: (async () =>
        new Response(
          JSON.stringify({ error: { code: "results_not_found", message: "no metrics" } }),
          { status: 404 },
        )) as typeof fetch,
    });
    await expect(client.experimentResults("e".repeat(64))).rejects.toMatchObject({
      status: 404,
      code: "results_not_found",
    });
  });
```

- [ ] **Step 2: 运行确认失败** → FAIL（方法不存在）。
- [ ] **Step 3: 实现 types + client 四方法**（上方代码）。
- [ ] **Step 4: 运行确认通过** → `npm test -- api-client.spec` PASS。
- [ ] **Step 5: 提交**

```bash
git add web/src/api/types.ts web/src/api/client.ts web/tests/api-client.spec.ts
git commit -m "feat(web): typed client for the four strategy endpoints

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: 策略列表页 + 策略组上线 + `/reports` 下线

**Files:**
- Create: `web/src/pages/StrategiesPage.vue`
- Modify: `web/src/router.ts`（策略组、删 `/reports` 路由）
- Delete: `web/src/pages/ReportsPage.vue`、`web/tests/reports-page.spec.ts`
- Test: `web/tests/strategies-page.spec.ts`（新；吸收 pin I6 探测断言的迁移属于 Task 8 详情页）

**Interfaces:**
- Consumes: Task 6 `experimentSummaries()`；第一批组件。
- Produces: 路由 `/strategies`；页面 testid：`strategy-list`、`strategy-row`、`strategy-empty`；导航"策略"组上线（`NAV_GROUPS` 插入 `{label:"策略", items:[{label:"策略列表", path:"/strategies"}]}` 于"概览"之后）；`/reports` 路由与页面删除（裁定 7）。

- [ ] **Step 1: 写失败测试**

```ts
// web/tests/strategies-page.spec.ts
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import type { ApiClient } from "../src/api/client";
import StrategiesPage from "../src/pages/StrategiesPage.vue";
import { fakeClient, mountAt } from "./helpers";

function summariesClient(rows: Array<Record<string, unknown>>): ApiClient {
  return fakeClient({ experimentSummaries: async () => ({ summaries: rows }) });
}

const WF_ROW = {
  experiment_id: "e".repeat(64),
  status: "ACCEPTED",
  dataset_version: "a".repeat(64),
  universe_version: "u".repeat(64),
  evaluation_reason: null,
  hypothesis: "动量延续假设：60 日动量在 CSI300 内有正超额",
  stability_conclusion: "STABLE",
  stability_policy_hash: "p".repeat(16),
  research_status: "COMPLETED",
  canonical_scenario: "full_cost",
  aggregates: [
    {
      scenario: "full_cost",
      aggregate_return: 0.12,
      annualized_return: 0.12,
      annualized_volatility: 0.18,
      sharpe_zero_rf: 1.4,
      oos_return_observations: 750,
      annualization_observations: 250,
    },
  ],
  display_extremes: { max_per_fold_drawdown: -0.08, max_reject_rate: 0.02, mean_turnover: 0.35 },
};

describe("策略列表（BRAIN 模型：列表列即门槛）", () => {
  it("渲染结论徽章、假设、canonical 情景指标与数据版本链接", async () => {
    const client = summariesClient([WF_ROW]);
    const wrapper = await mountAt(StrategiesPage, client, "/strategies");
    await flushPromises();
    const row = wrapper.get('[data-testid="strategy-row"]');
    expect(row.text()).toContain("动量延续假设");
    expect(row.get('[data-testid="state-badge"]').text()).toBe("STABLE");
    expect(row.text()).toContain("12.00%");  // aggregate_return 0.12 → pct()
    expect(row.text()).toContain("1.4");     // sharpe_zero_rf → num()
    expect(row.text()).toContain("-8.00%");  // max_per_fold_drawdown -0.08 → pct()
    expect(row.text()).toContain("35.00%");  // mean_turnover 0.35 → pct()
    expect(row.get('a[href*="/versions/"]').exists()).toBe(true);
  });

  it("非 walk-forward 实验显示'无 walk-forward 结论'而非空白", async () => {
    const legacy = { ...WF_ROW, experiment_id: "d".repeat(64), stability_conclusion: null, aggregates: null, display_extremes: null, hypothesis: null };
    const wrapper = await mountAt(StrategiesPage, summariesClient([legacy]), "/strategies");
    await flushPromises();
    expect(wrapper.get('[data-testid="strategy-row"]').text()).toContain("无 walk-forward 结论");
  });

  it("注册表为空 → 空态引导（不造数据）", async () => {
    const wrapper = await mountAt(StrategiesPage, summariesClient([]), "/strategies");
    await flushPromises();
    expect(wrapper.get('[data-testid="strategy-empty"]').text()).toContain("尚无已发布实验");
  });
});
```

- [ ] **Step 2: 运行确认失败** → FAIL（页面不存在）。
- [ ] **Step 3: 实现页面与路由**

`StrategiesPage.vue`：

```vue
<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { ExperimentSummaryRow, ScenarioAggregate } from "../api/types";
import Card from "../components/Card.vue";
import DataTable from "../components/DataTable.vue";
import EmptyState from "../components/EmptyState.vue";
import Skeleton from "../components/Skeleton.vue";
import StateBadge from "../components/StateBadge.vue";

const client = useApiClient();
const rows = ref<ExperimentSummaryRow[]>([]);
const loaded = ref(false);
const error = ref<DisplayError | null>(null);

function aggregateOf(row: ExperimentSummaryRow): ScenarioAggregate | null {
  if (row.aggregates === null) return null;
  const canonical = row.canonical_scenario;
  return row.aggregates.find((entry) => entry.scenario === canonical) ?? row.aggregates[0];
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}

onMounted(async () => {
  try {
    rows.value = (await client.experimentSummaries()).summaries;
    loaded.value = true;
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});
</script>

<template>
  <section>
    <h1>策略列表</h1>
    <p v-if="error !== null" class="error">错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}</p>
    <Skeleton v-else-if="!loaded" :rows="5" />
    <Card v-else-if="rows.length === 0">
      <div data-testid="strategy-empty">
        <EmptyState
          title="尚无已发布实验"
          description="data/experiments 为空；经 CLI 发起 research run 并发布实验后，结论将在此展示。"
        />
      </div>
    </Card>
    <DataTable
      v-else
      testid="strategy-list"
      row-testid="strategy-row"
      row-key="experiment_id"
      :rows="displayRows"
      :columns="[
        { key: 'hypothesis', label: '假设' },
        { key: 'conclusion', label: '结论' },
        { key: 'aggregate', label: 'OOS 聚合收益', align: 'right' },
        { key: 'sharpe', label: 'Sharpe（零无风险）', align: 'right' },
        { key: 'worst_drawdown', label: '逐折最差回撤', align: 'right' },
        { key: 'turnover', label: '换手（均值）', align: 'right' },
        { key: 'dataset_version', label: '数据版本', mono: true },
        { key: 'status', label: '状态' },
      ]"
    >
      <template #hypothesis="{ row }">
        <RouterLink :to="`/strategies/${row.experiment_id}`">
          {{ row.hypothesis }}
        </RouterLink>
      </template>
      <template #conclusion="{ row }">
        <StateBadge
          v-if="row.raw.stability_conclusion !== null"
          kind="conclusion"
          :value="row.raw.stability_conclusion"
        />
        <span v-else>无 walk-forward 结论</span>
      </template>
      <template #dataset_version="{ row }">
        <RouterLink
          v-if="row.raw.dataset_version !== null"
          :to="`/versions/${row.raw.dataset_version}`"
        >
          <code>{{ row.dataset_version }}</code>
        </RouterLink>
        <span v-else>—</span>
      </template>
    </DataTable>
  </section>
</template>
```

页面脚本侧（`displayRows` 是渲染用的字符串表 + 原始行引用，保证插槽里既能显示文本又能读原始字段）：

```ts
const displayRows = computed(() =>
  rows.value.map((row) => ({
    experiment_id: row.experiment_id,               // row-key
    hypothesis: row.hypothesis ?? "（未发布 experiment_spec.yml）",
    aggregate: pct(aggregateOf(row)?.aggregate_return),
    sharpe: num(aggregateOf(row)?.sharpe_zero_rf),
    worst_drawdown: pct(row.display_extremes?.max_per_fold_drawdown),
    turnover: pct(row.display_extremes?.mean_turnover),
    dataset_version: row.dataset_version ?? "—",
    status: row.status ?? "—",
    raw: row,                                        // 原始行：插槽读结论/版本用
  })),
);
```

（**实现注意**：本页的链接列/徽章列依赖 `DataTable` 的**列名作用域插槽**与 `rowTestid` prop——这两个由 S0b 计划的 Task 1 定义（`2026-10-03-web-decision-layer-s0b-reskin.md`）。**若 S0b 尚未落地**，本任务先做那一个文件的最小扩展（组件契约照 S0b Task 1 写，两份计划只允许存在一份实现，先到先得），再实现本页；不要改用原生 `<table>`——那会让同一批页面出现两套表格实现。
`row` 插槽的 `value` 是 `unknown`，页面自行窄化；`row.raw` 是本页额外塞进展示行的原始行，`DataTable` 只透传不解释。）

`router.ts`：`NAV_GROUPS` 在"概览"独立项之后插入：

```ts
  {
    label: "策略",
    items: [{ label: "策略列表", path: "/strategies" }],
  },
```

routes 增加 `{ path: "/strategies", name: "strategies", component: StrategiesPage }` 与 `{ path: "/strategies/:experimentId", name: "strategy-detail", component: StrategyDetailPage }`（详情组件在 Task 8；本任务先建占位文件 `StrategyDetailPage.vue` 输出 `<section><h1>策略详情</h1></section>`，Task 8 重写——**避免路由悬空**）；**删除** `/reports` 路由、`ReportsPage` import、NAV_GROUPS 的报告独立项。删除 `web/src/pages/ReportsPage.vue` 与 `web/tests/reports-page.spec.ts`。

- [ ] **Step 4: 运行确认通过 + 修正受影响测试**

Run: `npm test`
Expected: 策略列表新用例 PASS；`app-shell.spec` 若因导航组变化（报告项消失、策略组出现）失败 → 按新 `NAV_ITEMS` 派生序更新期望（断言读 `NAV_ITEMS`，理论自动跟随；组标签断言同步"数据/运维/策略"）；e2e 的 `/reports` 用例在 Task 9 改写。

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/StrategiesPage.vue web/src/pages/StrategyDetailPage.vue web/src/router.ts web/tests/strategies-page.spec.ts web/src/components
git rm web/src/pages/ReportsPage.vue web/tests/reports-page.spec.ts
git commit -m "feat(web): strategy list page; retire the reports link farm (ruling 7)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: 策略详情页（结论横幅 / 指标卡 / 逐折图表 / 溯源 / 报告入口）

**Files:**
- Modify: `web/src/pages/StrategyDetailPage.vue`（重写占位）
- Test: `web/tests/strategy-detail-page.spec.ts`（含 pin I6 探测断言迁移）

**Interfaces:**
- Consumes: `experimentResults`/`foldEquity`/`datasetBenchmark`/`probeExperimentReport`；LineChart/BarChart（Task 5）；StateBadge/KpiCard/Card/DataTable/EmptyState/ProvenanceStrip 类（第一批；ProvenanceStrip 若第一批未建，本任务以简单 `<p class="provenance">` + `<code>` 列表实现，testid `provenance-strip`）。
- Produces: 页面 testid：`verdict-banner`、`metric-cards`、`fold-selector`、`scenario-tabs`、`chart-net-value`、`chart-net-value-legend`、`chart-underwater`、`chart-fold-bars`、`fold-table`、`provenance-strip`、`report-link`、`detail-empty`。

- [ ] **Step 1: 写失败测试（关键钉子）**

```ts
// web/tests/strategy-detail-page.spec.ts
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import type { ApiClient } from "../src/api/client";
import StrategyDetailPage from "../src/pages/StrategyDetailPage.vue";
import { fakeClient, mountAt } from "./helpers";

const EX = "e".repeat(64);
const FOLD = "f".repeat(64);

function resultsPayload(): Record<string, unknown> {
  return {
    experiment_id: EX,
    manifest: {
      experiment_id: EX,
      status: "ACCEPTED",
      dataset_version: "a".repeat(64),
      universe_version: "u".repeat(64),
      code_commit: "c".repeat(40),
      strategy_snapshot_sha256: "1".repeat(64),
      experiment_snapshot_sha256: "2".repeat(64),
      data_environment_snapshot_sha256: "3".repeat(64),
      stability_policy_hash: "p".repeat(16),
      artifacts: { "folds/x": "0".repeat(64) },
    },
    metrics: {
      meta: { spec: { hypothesis: "动量延续假设", cost_scenarios: ["zero_cost", "full_cost"] }, benchmark_symbols: ["000300.SH"] },
      walk_forward: { stability_conclusion: "STABLE", stability_policy_hash: "p".repeat(16) },
      evaluation: { status: "ACCEPTED", reason: "stability STABLE" },
    },
    stability_report: {
      stability_conclusion: "STABLE",
      reasons: [],
      thresholds: { policy: "stability-v1" },
      fold_statuses: [{ fold_id: FOLD, status: "EXECUTED", reason_code: null }],
      scenario_aggregates: [
        { scenario: "full_cost", aggregate_return: 0.12, annualized_return: 0.12, annualized_volatility: 0.18, sharpe_zero_rf: 1.4, oos_return_observations: 100, annualization_observations: 250 },
      ],
      fold_metrics: [
        { fold_id: FOLD, scenario: "full_cost", fold_calendar_return: 0.02, per_fold_max_drawdown: -0.03, sharpe_zero_rf: 1.1, explicit_cost_drag: 0.004, net_return: 0.016, reject_rate: 0.01, turnover: 0.3, first_trading_day: "2026-01-05", last_trading_day: "2026-03-05" },
      ],
    },
  };
}

function detailClient(overrides: Partial<ApiClient> = {}): ApiClient {
  return fakeClient({
    experimentResults: async () => resultsPayload(),
    foldEquity: async () => ({
      experiment_id: EX,
      fold_id: FOLD,
      scenario: "full_cost",
      rows: [
        { trade_date: "2026-01-05", net_equity_after_cost: 1_000_000 },
        { trade_date: "2026-01-06", net_equity_after_cost: 1_010_000 },
      ],
    }),
    datasetBenchmark: async () => ({
      dataset_version: "a".repeat(64),
      requested_version: "a".repeat(64),
      symbol: "000300.SH",
      rows: [
        { trade_date: "2026-01-05", close: 4000.0 },
        { trade_date: "2026-01-06", close: 4020.0 },
      ],
    }),
    probeExperimentReport: async () => true,
    ...overrides,
  } as ApiClient);
}

describe("策略详情（tearsheet 骨架 + 仓库纪律）", () => {
  it("结论横幅第一屏：STABLE 徽章 + 政策哈希 + 假设", async () => {
    const wrapper = await mountAt(StrategyDetailPage, detailClient(), `/strategies/${EX}`);
    await flushPromises();
    const banner = wrapper.get('[data-testid="verdict-banner"]');
    expect(banner.get('[data-testid="state-badge"]').text()).toBe("STABLE");
    expect(banner.text()).toContain("动量延续假设");
    expect(banner.text()).toContain("p".repeat(16).slice(0, 8));
  });

  it("逐折证据：图表 data-fold 等于当前选中折（禁止拼接的可测形式）", async () => {
    const wrapper = await mountAt(StrategyDetailPage, detailClient(), `/strategies/${EX}`);
    await flushPromises();
    const chart = wrapper.get('[data-testid="chart-net-value"]');
    expect(chart.attributes("data-fold")).toBe(FOLD);
    // 基准同图：基准端点被请求过（窗口 = 折首末交易日），且图例是 DOM
    // 文本而非 canvas 像素——ECharts 是 canvas 渲染，series.name 不会
    // 出现在 wrapper.text() 里，所以图例必须由页面自己渲染成元素。
    expect(wrapper.get('[data-testid="chart-net-value-legend"]').text()).toContain(
      "基准 000300.SH",
    );
  });

  it("非 walk-forward 实验：显式'未发布该产物'，不渲染结论图表", async () => {
    const client = detailClient({
      experimentResults: async () => ({
        experiment_id: EX,
        manifest: { experiment_id: EX, status: "ACCEPTED" },
        metrics: null,
        stability_report: null,
      }),
    });
    const wrapper = await mountAt(StrategyDetailPage, client, `/strategies/${EX}`);
    await flushPromises();
    expect(wrapper.get('[data-testid="detail-empty"]').text()).toContain("未发布 walk-forward 产物");
    expect(wrapper.find('[data-testid="chart-net-value"]').exists()).toBe(false);
  });

  it("报告入口：pin I6 探测 200 → 链接并如实标注摘要表", async () => {
    const wrapper = await mountAt(StrategyDetailPage, detailClient(), `/strategies/${EX}`);
    await flushPromises();
    const link = wrapper.get('[data-testid="report-link"]');
    expect(link.attributes("href")).toContain(`/api/v1/experiments/${EX}/report`);
    expect(wrapper.text()).toContain("摘要表");
  });

  it("报告缺失（pin I6 探测 404）→ 显式缺失态", async () => {
    const wrapper = await mountAt(
      StrategyDetailPage,
      detailClient({ probeExperimentReport: async () => false }),
      `/strategies/${EX}`,
    );
    await flushPromises();
    expect(wrapper.text()).toContain("无已发布报告");
  });
});
```

- [ ] **Step 2: 运行确认失败** → FAIL（页面是占位）。
- [ ] **Step 3: 实现页面**

页面结构（完整语义；归一化与逐点回撤是 §7.4 允许的渲染变换，逐字注释）：

```vue
<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { FoldEquityResponse } from "../api/types";
import Card from "../components/Card.vue";
import EmptyState from "../components/EmptyState.vue";
import KpiCard from "../components/KpiCard.vue";
import LineChart from "../components/LineChart.vue";
import BarChart from "../components/BarChart.vue";
import Skeleton from "../components/Skeleton.vue";
import StateBadge from "../components/StateBadge.vue";

interface FoldMetricRow {
  fold_id: string;
  scenario: string;
  fold_calendar_return: number | null;
  per_fold_max_drawdown: number | null;
  sharpe_zero_rf: number | null;
  explicit_cost_drag: number | null;
  net_return: number | null;
  reject_rate: number | null;
  turnover: number | null;
  first_trading_day: string | null;
  last_trading_day: string | null;
}

const client = useApiClient();
const route = useRoute();
const experimentId = String(route.params.experimentId);

const manifest = ref<Record<string, unknown> | null>(null);
const metrics = ref<Record<string, unknown> | null>(null);
const report = ref<Record<string, unknown> | null>(null);
const loaded = ref(false);
const error = ref<DisplayError | null>(null);
const hasReportHtml = ref<boolean | null>(null);

const selectedFold = ref<string>("");
const scenario = ref<string>("");

const equity = ref<FoldEquityResponse | null>(null);
const benchmarkRows = ref<Array<{ trade_date: string; close: number }>>([]);

const conclusion = computed(
  () => (report.value?.stability_conclusion as string | undefined) ?? null,
);
const hypothesis = computed(() => {
  const spec = (metrics.value?.meta as Record<string, unknown> | undefined)?.spec as
    | Record<string, unknown>
    | undefined;
  const value = spec?.hypothesis;
  return typeof value === "string" ? value : null;
});
const foldStatuses = computed(
  () =>
    (report.value?.fold_statuses as Array<Record<string, unknown>> | undefined)?.filter(
      (entry) => entry.status === "EXECUTED",
    ) ?? [],
);
const foldMetrics = computed(
  () => ((report.value?.fold_metrics as FoldMetricRow[] | undefined) ?? []),
);
const scenarios = computed(() => {
  const names = new Set(foldMetrics.value.map((row) => row.scenario));
  return [...names];
});
const aggregates = computed(
  () => (report.value?.scenario_aggregates as Array<Record<string, unknown>> | undefined) ?? [],
);
const currentFoldMetrics = computed(() =>
  foldMetrics.value.filter(
    (row) => row.fold_id === selectedFold.value && row.scenario === scenario.value,
  ),
);
const currentAggregate = computed(
  () => aggregates.value.find((entry) => entry.scenario === scenario.value) ?? null,
);

function pct(value: unknown): string {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
}
function num(value: unknown): string {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

/** §7.4 渲染变换：单条已发布净值序列的归一化与逐点回撤（不是指标计算）。 */
const normalized = computed(() => {
  const rows = equity.value?.rows ?? [];
  const base = rows[0]?.net_equity_after_cost;
  if (base === undefined || base === 0) return [] as number[];
  return rows.map((row) => Number(row.net_equity_after_cost) / Number(base));
});
const benchmarkNormalized = computed(() => {
  if (benchmarkRows.value.length === 0) return [] as number[];
  const base = benchmarkRows.value[0].close;
  return benchmarkRows.value.map((row) => row.close / base);
});
const underwater = computed(() => {
  const series = normalized.value;
  let peak = Number.NEGATIVE_INFINITY;
  return series.map((value) => {
    peak = Math.max(peak, value);
    return value / peak - 1;
  });
});

async function loadFoldSeries() {
  if (selectedFold.value === "" || scenario.value === "") return;
  equity.value = null;
  benchmarkRows.value = [];
  equity.value = await client.foldEquity(experimentId, selectedFold.value, scenario.value);
  const foldRow = currentFoldMetrics.value[0];
  const start = foldRow?.first_trading_day ?? undefined;
  const end = foldRow?.last_trading_day ?? undefined;
  if (start !== undefined && end !== undefined) {
    benchmarkRows.value = (
      await client.datasetBenchmark(String(manifest.value?.dataset_version ?? "current"), {
        start,
        end,
      })
    ).rows;
  }
}

onMounted(async () => {
  try {
    const results = await client.experimentResults(experimentId);
    manifest.value = results.manifest;
    metrics.value = results.metrics;
    report.value = results.stability_report;
    hasReportHtml.value = await client.probeExperimentReport(experimentId);
    selectedFold.value = String(foldStatuses.value[0]?.fold_id ?? "");
    scenario.value =
      scenarios.value.includes("full_cost") ? "full_cost" : (scenarios.value[0] ?? "");
    loaded.value = true;
    await loadFoldSeries();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});

watch([selectedFold, scenario], () => {
  void loadFoldSeries();
});
</script>
```

（模板：`verdict-banner` = Card（StateBadge conclusion + hypothesis + evaluation.status/reason + `stability_policy_hash` 前 8 位 + thresholds 摘要）；`metric-cards` = `.kpi-grid`（当前情景 `aggregate_return/annualized_return/annualized_volatility/sharpe_zero_rf` 四张 KpiCard，compare 行写"OOS 观测 N"）；图表区 = `fold-selector`（select，选项 foldStatuses）+ `scenario-tabs`（button 组）+ `chart-net-value`（LineChart：labels=equity dates、series=[策略归一化, 基准归一化 dashed]、`:fold="selectedFold"`）**外加一段 DOM 图例** `chart-net-value-legend`（`<ul>`：`策略（费后）` 与 `基准 {{ manifest.meta.benchmark_symbols[0] ?? '000300.SH' }}`，用真实 symbol 文本）——ECharts 画在 canvas 上，series 名不进 DOM，图例必须由页面渲染，否则"基准同图"在 DOM 层不可验证也不可见）+ `chart-underwater`（LineChart 单序列）+ `chart-fold-bars`（BarChart：labels=fold 短 id、series=[fold_calendar_return 红涨绿跌, per_fold_max_drawdown]、colorBySign）；`fold-table` = **DataTable**（与列表页同一实现、同一列插槽扩展）列 = 折/情景/日历收益/逐折回撤/Sharpe/成本拖累/净收益/拒单率/换手，首列用 `#fold_id` 插槽渲染 fold 短 id + 全哈希 `title`；`provenance-strip` = manifest 三快照 + code_commit + fold_schedule_sha256/fold_outcomes_sha256 + artifacts 计数；`report-link` = `hasReportHtml===true` 时外链 + "（摘要表：_DefaultReport，无图表）"标注，false 时"无已发布报告"，null 时省略；非 WF（report 为 null）→ `detail-empty` EmptyState"未发布 walk-forward 产物——请看静态报告或 CLI 产物"。加载/错误态复用第一批模式。）

- [ ] **Step 4: 运行确认通过**

Run: `npm test -- strategy-detail-page.spec && npm run typecheck`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/StrategyDetailPage.vue web/tests/strategy-detail-page.spec.ts
git commit -m "feat(web): strategy detail page with per-fold evidence and provenance

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: e2e 与批次收口

**Files:**
- Modify: `web/e2e/portal.spec.ts`（reports 用例改写为 strategies）
- 无新文件

- [ ] **Step 1: e2e 改写**

删除/替换既有 `/reports` 用例（`page.goto("/#/reports")` 一组）为：

```ts
test("策略列表与详情（S1）", async ({ page }) => {
  const mock = state({});
  await installMockBackend(page, mock);
  await page.route("**/api/v1/experiments/summaries", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ summaries: [] }),
    }),
  );
  await page.goto("/#/strategies");
  await expect(page.getByTestId("strategy-empty")).toContainText("尚无已发布实验");
  // 导航分组：策略组在、报告组不在（裁定 7）。
  await expect(page.getByTestId("main-nav")).toContainText("策略列表");
  await expect(page.getByTestId("main-nav")).not.toContainText("报告");
});
```

（installMockBackend 若未覆盖 `/experiments/summaries` 会 404——上面显式补 route；详情页 e2e 以同样模式 mock `/experiments/{id}/results`、`/folds/.../equity`、`/datasets/{v}/benchmark`，断言 `verdict-banner` 与 `chart-net-value` 的 `data-fold`。）

- [ ] **Step 2: 全量回归**

Run: `npm test && npm run typecheck && npx playwright test`
Expected: 全绿。

- [ ] **Step 3: 验收勾稽（规格 §8 S1 行）**

- 零发布产物变更：`git diff --stat <开工提交> -- src/stock_quant/research src/stock_quant/data_model` 输出为空；
- 依赖增量唯 echarts：`git diff --stat <开工提交> -- web/package.json` 仅一行 echarts；
- pin I5/I6/I8 原测试不变绿：`tests/service/` 既有用例 + `api-client.spec` 既有用例全绿（已在 Step 2 覆盖）；
- 无跨折拼接/无全期净值：`strategy-detail-page.spec` 的 `data-fold` 钉子 + 页面无任何"全期/累计净值"文案（人工走查清单勾选）；
- 两口径不混：列表与详情指标全部来自 `ScenarioMetrics`/`AggregateOOSMetrics` 字段名；`PerformanceMetrics` 不出现在本批任何类型（`grep -R "PerformanceMetrics" web/src` 为空）。

- [ ] **Step 4: 提交**

```bash
git add web/e2e/portal.spec.ts
git commit -m "test(web): e2e for the strategy pages after retiring /reports

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

## Self-Review 记录（2026-10-03）

**1. 规格覆盖（v3 §5.2/§5.3/§7/§8-S1 → 任务）**

| 规格条目 | 任务 |
| --- | --- |
| §7.2 四端点（含错误码、声明图护栏、窗口参数属新端点） | Task 1–4 |
| §7.3 hypothesis 来自已发布 experiment_spec.yml（零 manifest 变更） | Task 3 |
| §5.2 列表（结论徽章/OOS 指标/逐折最差回撤/换手/数据版本链接/空态） | Task 7 |
| §5.3a 结论横幅 | Task 8 |
| §5.3b 指标卡（AggregateOOSMetrics 口径；单窗口 PerformanceMetrics 形态本批不实现——现实注册表只有正式 WF 实验，工程/遗留形态留待真实实例出现时按声明图分支扩展，已在 §5.3b 保留分支说明） | Task 8 |
| §5.3c 图表（逐折净值+基准同图、水下回撤、分布条形、禁拼接） | Task 5+8 |
| §5.3d 逐折明细表 | Task 8 |
| §5.3e 溯源条 | Task 8 |
| §5.3f 报告入口（pin I6 语义迁移 + 摘要表如实标注） | Task 8 |
| §4/裁定 7 `/reports` 下线、策略组上线 | Task 7 |
| §8 S1 验收标准 | Task 9 Step 3 |

**2. 占位符扫描**：Task 4 有一处"以文件内现行 helper/模式为准"的对齐指令（`PinnedDataset` 属性名与 DuckDB 读法）——已给出确定取舍（benchmarks 按 `tables.py` 现行模式）；Task 7 的表格实现已**定死为 DataTable + 列插槽**（复用 S0b Task 1 的扩展，不再给"原生表格"的二选一）；Task 8 模板以结构描述 + 关键 testid 给出，脚本部分完整。

**3. 类型一致性**：`ExperimentSummaryRow`/`ScenarioAggregate`/`ExperimentResultsResponse`/`FoldEquityResponse`/`BenchmarkResponse`（Task 6 TS）与 Task 1–4 pydantic 模型逐字段对应；`foldEquity(experimentId, foldId, scenario)` 签名在 Task 2（query 参数）与 Task 6/8 一致；`LineChart` props `fold` 在 Task 5 定义、Task 8 消费、Task 8 测试断言。

**4. 已知风险**：①FastAPI 路由遮蔽——实测**不存在**两段式 `GET /experiments/{experiment_id}`（只有 `/experiments` 与 `/experiments/{id}/report`），`/experiments/summaries` 无遮蔽面；排序纪律与测试仍保留（Task 3 Step 3 已更正措辞）；②happy-dom 下 echarts.init 的 canvas 行为不确定（Task 5 Step 5 已给出降级断言路径，保 `data-fold` 证据断言；Task 8 的"基准同图"断言已改为 DOM 图例，不再依赖 canvas 内容）；③实例对齐（Task 1 Step 1）发现的任何字段差异以真实实例为准修正本计划类型——这是硬门的用意，不是可选项。
