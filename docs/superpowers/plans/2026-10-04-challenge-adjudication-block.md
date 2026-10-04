# 挑战裁决区块实施计划（Challenge Adjudication Block）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在策略详情页挂上只读的挑战裁决区块，把已发布的一次性策略挑战证据（声明 / holdout 消费 / 裁决）逐字呈现给决策者。

**Architecture:** 新增只读服务模块 `src/stock_quant/service/strategy_challenges.py`，直读 `data/strategy_challenges/results/<challenge_id>/strategy_comparison.json`，按身份等式匹配"本实验"（baseline id 或 challenger 策略快照哈希），投影成类型化 pydantic 视图；前端新增 `ChallengeBlock.vue` 组件自取数自渲染，挂在 `StrategyDetailPage.vue` 主内容之后（`report === null` 分支也渲染）。全程零写面、零派生、零新发布产物。

**Tech Stack:** Python 3.12 / FastAPI / pydantic v2 / pytest；Vue 3 + TypeScript + vitest + @vue/test-utils + Playwright。

**Spec:** [docs/superpowers/specs/2026-10-04-challenge-adjudication-block-design.md](../specs/2026-10-04-challenge-adjudication-block-design.md)

## Global Constraints

- **ADR-021**：只读服务不得 import `stock_quant.research` / `stock_quant.data_pipeline`，直读已发布 JSON（`test_service_boundary.py` 有自动门禁与 import 探针）。
- **pin I8**：所有非 2xx 走嵌套信封 `{"error": {"code", "message", "dataset_version"}}`，码为稳定脱敏串，不含路径/堆栈。
- **投影纪律**（spec §3.1）：逐字读取，零派生、零改写、决策层证据零丢失；未知字段由 pydantic 默认 `extra="ignore"` 丢弃；只允许对单值的显示层格式化。
- **零写面**（spec §1）：不新增任何触发/重跑/删除入口、不新增发布产物、不改 `research/` 产物契约、不改 `_assert_artifact_tree` 闭集。
- **`web/src/api/types.ts` 只增不改**；`client.ts` 只加只读 GET 方法。
- **pin I5/I6/I8 既有测试必须保持绿色**。
- 提交信息英文、以 `Co-Authored-By: Claude Code <noreply@anthropic.com>` 结尾。
- 测试只跑命名的目标文件，不跑裸 `pytest`（仓库纪律：`pytest <file> -q`）。

---

## File Structure

| 文件 | 责任 | 动作 |
| --- | --- | --- |
| `src/stock_quant/service/errors.py` | 稳定错误词表 | 加 `ChallengeUnreadable` |
| `src/stock_quant/service/strategy_challenges.py` | 挑战结果的只读视图端点（模型、匹配、排序、投影、护栏） | 新建 |
| `src/stock_quant/service/app.py` | 路由注册 | 加 import + `include_router` |
| `tests/service/test_challenge_endpoints.py` | 端点契约与越界护栏测试 | 新建 |
| `tests/service/test_service_boundary.py` | 服务架构边界 | 加一条 import 探针 |
| `web/src/api/types.ts` | 前端响应类型 | 追加 challenge 类型块 |
| `web/src/api/client.ts` | API 客户端 | 加 `listExperimentChallenges` |
| `web/src/components/StateBadge.vue` | 状态徽章语义映射 | 加 `challenge` 组 |
| `web/tests/helpers.ts` | 测试夹具 | `fakeClient` 基座补新方法 |
| `web/tests/state-badge.spec.ts` | 徽章单测 | 加 challenge 映射用例 |
| `web/src/components/ChallengeBlock.vue` | 详情页挑战裁决区块 | 新建 |
| `web/tests/challenge-block.spec.ts` | 区块单测（消费三分支、空态、错误） | 新建 |
| `web/src/pages/StrategyDetailPage.vue` | 详情页 | 挂载区块 |
| `web/tests/strategy-detail-page.spec.ts` | 详情页测试 | 补 stub + 一条挂载断言 |
| `web/e2e/portal.spec.ts` | 端到端主流程 | 补挑战路由 mock 与断言 |

---

### Task 1: 只读端点 — 身份匹配与排序

**Files:**
- Create: `src/stock_quant/service/strategy_challenges.py`
- Modify: `src/stock_quant/service/errors.py`（在 `ExperimentManifestUnreadable` 之后插入）
- Modify: `src/stock_quant/service/app.py:21`、`app.py:119-123`
- Test: `tests/service/test_challenge_endpoints.py`（新建）

**Interfaces:**
- Consumes: `stock_quant.service.datasets.read_json_or_fail(path, error_class, what)`；`stock_quant.service.errors.UnknownExperiment` / `ExperimentManifestUnreadable`。
- Produces（Task 2/3 依赖）：
  - `strategy_challenges.router`：`APIRouter(prefix="/api/v1", tags=["strategy-challenges"])`
  - `GET /api/v1/experiments/{experiment_id}/challenges` → `ExperimentChallengesResponse{experiment_id: str, challenges: list[ChallengeView]}`
  - `ChallengeView{challenge_id: str, role: Literal["baseline", "challenger"], declaration: ChallengeDeclarationView, consumption: ChallengeConsumptionView | None, result: ChallengeResultView}`
  - `ChallengeDeclarationView{strategy_family, baseline_experiment_id, challenger_strategy_hash, fold_schedule_hash, comparison_policy_hash, declared_before_run_at, universe_definition}`
  - `ChallengeConsumptionView{status, consumption_key, consumed_at, universe_definition}`
  - `ChallengeResultView{status, conclusion, challenger_stability_conclusion, challenger_experiment_id, executed_fold_count, declared_scenario_count, skipped_fold_ids, failed_scenarios, reasons, scenario_results, error_code}`
  - `ScenarioResultView{scenario, executed_fold_count, passed, cells: list[MetricCellView]}`；`MetricCellView{metric, baseline, challenger, delta, threshold, passed}`
  - `UniverseIdentityView{universe_id, universe_version, membership_table_sha256, evidence_summary_sha256}`
  - `errors.ChallengeUnreadable`：`status_code = 500`, `code = "challenge_comparison_unreadable"`

- [ ] **Step 1: 写失败的端点契约测试**

新建 `tests/service/test_challenge_endpoints.py`：

```python
"""Contract tests for the challenge adjudication endpoint (spec 2026-10-04).

One published experiment (``service_project``) plus challenge results written
straight into ``data/strategy_challenges/results/`` in the *published*
``strategy_comparison.json`` shape (identity + consumption + result), so the
read-only projection is pinned against the real artifact form.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

EXPERIMENT_ID = "e" * 64
CHALLENGER_ID = "c" * 64
CHALLENGE_ID = "a" * 64
OTHER_CHALLENGE_ID = "b" * 64
BASELINE_HASH = "1" * 64
CHALLENGER_HASH = "2" * 64
UNIVERSE = {
    "universe_id": "custom_csi300_tw_tradable",
    "universe_version": "3" * 64,
    "membership_table_sha256": "4" * 64,
    "evidence_summary_sha256": "5" * 64,
}
POLICY_HASH = "6" * 64
FOLD_SCHEDULE_HASH = "f" * 64


def write_experiment_manifest(
    root: Path, experiment_id: str, strategy_hash: str
) -> None:
    """Overwrite the fixture experiment manifest with the identity field."""
    directory = root / "data" / "experiments" / experiment_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "status": "ACCEPTED",
                "dataset_version": "d" * 64,
                "universe_version": "u" * 64,
                "strategy_snapshot_sha256": strategy_hash,
            }
        ),
        encoding="utf-8",
    )


def consumption_record(challenge_id: str = CHALLENGE_ID) -> dict:
    return {
        "status": "consumed",
        "consumption_key": f"buffered_risk_weighted_momentum:{FOLD_SCHEDULE_HASH}",
        "challenge_id": challenge_id,
        "strategy_family": "buffered_risk_weighted_momentum",
        "fold_schedule_hash": FOLD_SCHEDULE_HASH,
        "declaration_sha256": "7" * 64,
        "comparison_policy_hash": POLICY_HASH,
        "baseline_experiment_id": EXPERIMENT_ID,
        "challenger_strategy_hash": CHALLENGER_HASH,
        "universe_definition": dict(UNIVERSE),
        "consumed_at": "2026-10-01T00:00:01+00:00",
    }


def comparison_payload(
    *,
    challenge_id: str = CHALLENGE_ID,
    baseline_experiment_id: str = EXPERIMENT_ID,
    challenger_strategy_hash: str = CHALLENGER_HASH,
    declared_before_run_at: str = "2026-10-01T00:00:00+00:00",
    status: str = "COMPLETED",
    conclusion: str | None = "PROMOTED",
    consumption: dict | None = None,
    error_code: str | None = None,
    reasons: tuple[str, ...] = (),
) -> dict:
    """The published ``strategy_comparison.json`` shape, verbatim."""
    completed = status == "COMPLETED"
    return {
        "challenge_id": challenge_id,
        "status": status,
        "conclusion": conclusion,
        "declaration": {
            "identity_scheme_version": "strategy-challenge-v1",
            "strategy_family": "buffered_risk_weighted_momentum",
            "baseline_experiment_id": baseline_experiment_id,
            "challenger_strategy_hash": challenger_strategy_hash,
            "fold_schedule_hash": FOLD_SCHEDULE_HASH,
            "universe_definition": dict(UNIVERSE),
            "comparison_policy": {"policy_version": "strategy-comparison-v1"},
            "comparison_policy_hash": POLICY_HASH,
            "declared_before_run_at": declared_before_run_at,
        },
        "comparison_policy_hash": POLICY_HASH,
        "fold_schedule_hash": FOLD_SCHEDULE_HASH,
        "universe_definition": dict(UNIVERSE),
        "holdout_consumption": consumption,
        "baseline_experiment_id": baseline_experiment_id,
        "challenger_experiment_id": CHALLENGER_ID if completed else None,
        "snapshot_hashes": {"baseline": None, "challenger": None},
        "result": {
            "challenge_id": challenge_id,
            "strategy_family": "buffered_risk_weighted_momentum",
            "status": status,
            "conclusion": conclusion,
            "baseline_experiment_id": baseline_experiment_id,
            "challenger_experiment_id": CHALLENGER_ID if completed else None,
            "challenger_stability_conclusion": "STABLE" if completed else None,
            "comparison_policy_hash": POLICY_HASH,
            "executed_fold_count": 6 if completed else None,
            "declared_scenario_count": 3 if completed else None,
            "skipped_fold_ids": [],
            "failed_scenarios": [],
            "scenario_results": (
                [
                    {
                        "scenario": "full_cost",
                        "executed_fold_count": 6,
                        "passed": True,
                        "cells": [
                            {
                                "metric": "aggregate_sharpe_delta",
                                "baseline": 1.0,
                                "challenger": 1.3,
                                "delta": 0.3,
                                "threshold": ">= 0.10",
                                "passed": True,
                            }
                        ],
                    }
                ]
                if completed
                else []
            ),
            "reasons": list(reasons),
            "error_code": error_code,
        },
        "artifacts": {"strategy_comparison.json": "9" * 64},
    }


def write_challenge(
    root: Path, payload: dict, challenge_id: str = CHALLENGE_ID
) -> Path:
    directory = root / "data" / "strategy_challenges" / "results" / challenge_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "strategy_comparison.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return directory


def test_empty_root_returns_200_and_an_empty_list(client: TestClient) -> None:
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 200
    assert response.json() == {"experiment_id": EXPERIMENT_ID, "challenges": []}


def test_empty_results_tree_returns_an_empty_list(
    client: TestClient, service_project: Path
) -> None:
    (service_project / "data" / "strategy_challenges" / "results").mkdir(
        parents=True
    )
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 200
    assert response.json()["challenges"] == []


def test_unknown_hex64_experiment_is_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/experiments/{'9' * 64}/challenges")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "experiment_not_found"


def test_non_hex_experiment_id_is_422_invalid_request(client: TestClient) -> None:
    response = client.get("/api/v1/experiments/not-a-hash/challenges")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_baseline_side_match_is_projected_verbatim(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert len(body["challenges"]) == 1
    view = body["challenges"][0]
    assert view["challenge_id"] == CHALLENGE_ID
    assert view["role"] == "baseline"
    declaration = view["declaration"]
    assert declaration["strategy_family"] == "buffered_risk_weighted_momentum"
    assert declaration["baseline_experiment_id"] == EXPERIMENT_ID
    assert declaration["challenger_strategy_hash"] == CHALLENGER_HASH
    assert declaration["fold_schedule_hash"] == FOLD_SCHEDULE_HASH
    assert declaration["comparison_policy_hash"] == POLICY_HASH
    assert declaration["declared_before_run_at"] == "2026-10-01T00:00:00+00:00"
    assert declaration["universe_definition"] == UNIVERSE
    assert view["consumption"]["consumption_key"] == (
        f"buffered_risk_weighted_momentum:{FOLD_SCHEDULE_HASH}"
    )
    assert view["consumption"]["consumed_at"] == "2026-10-01T00:00:01+00:00"
    assert view["consumption"]["universe_definition"] == UNIVERSE
    result = view["result"]
    assert result["status"] == "COMPLETED"
    assert result["conclusion"] == "PROMOTED"
    assert result["challenger_experiment_id"] == CHALLENGER_ID
    assert result["executed_fold_count"] == 6
    assert result["scenario_results"][0]["cells"][0]["delta"] == 0.3
    assert result["scenario_results"][0]["cells"][0]["threshold"] == ">= 0.10"
    # 投影省略的校验层字段不得出现在响应里（spec §3.1 (b)）。
    assert "snapshot_hashes" not in view
    assert "artifacts" not in view
    assert "declaration_sha256" not in view["consumption"]


def test_challenger_side_match_uses_the_manifest_snapshot_hash(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, CHALLENGER_HASH)
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["role"] for view in body["challenges"]] == ["challenger"]


def test_baseline_equality_wins_the_role_when_both_hold(
    client: TestClient, service_project: Path
) -> None:
    # 合法挑战两侧必为不同实验，这里只钉住判定顺序（先 baseline 后 challenger）。
    write_experiment_manifest(service_project, EXPERIMENT_ID, CHALLENGER_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            challenger_strategy_hash=CHALLENGER_HASH,
            consumption=consumption_record(),
        ),
    )
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["role"] for view in body["challenges"]] == ["baseline"]


def test_unrelated_challenge_is_excluded(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            baseline_experiment_id="8" * 64,
            challenger_strategy_hash="7" * 64,
            consumption=consumption_record(),
        ),
    )
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert body["challenges"] == []


def test_non_hex_names_and_plain_files_are_skipped(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    results = service_project / "data" / "strategy_challenges" / "results"
    (results / "not-a-challenge").mkdir(parents=True)
    (results / ".staging-deadbeef").mkdir(parents=True)
    (results / ("d" * 64)).write_text("i am a file, not a directory", encoding="utf-8")
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["challenge_id"] for view in body["challenges"]] == [CHALLENGE_ID]


def test_challenges_are_sorted_by_declaration_instant(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            challenge_id=OTHER_CHALLENGE_ID,
            declared_before_run_at="2026-10-02T00:00:00+00:00",
            consumption=consumption_record(OTHER_CHALLENGE_ID),
        ),
        challenge_id=OTHER_CHALLENGE_ID,
    )
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["challenge_id"] for view in body["challenges"]] == [
        CHALLENGE_ID,
        OTHER_CHALLENGE_ID,
    ]


def test_same_instant_falls_back_to_challenge_id_order(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            challenge_id=OTHER_CHALLENGE_ID,
            consumption=consumption_record(OTHER_CHALLENGE_ID),
        ),
        challenge_id=OTHER_CHALLENGE_ID,
    )
    write_challenge(service_project, comparison_payload(consumption=consumption_record()))
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    # 同一 instant 下 "a"*64 < "b"*64。
    assert [view["challenge_id"] for view in body["challenges"]] == [
        CHALLENGE_ID,
        OTHER_CHALLENGE_ID,
    ]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/service/test_challenge_endpoints.py -q`
Expected: 全部 FAIL（404 `not_found`，因为路由还不存在）。

- [ ] **Step 3: 加错误类**

在 `src/stock_quant/service/errors.py` 的 `ExperimentManifestUnreadable` 之后插入：

```python
class ChallengeUnreadable(ServiceError):
    """A published ``strategy_comparison.json`` exists but cannot be projected.

    Fail closed (spec §3.2): silently skipping it would hide a challenge that
    already consumed the holdout -- the one thing this surface must never do.
    """

    status_code = 500
    code = "challenge_comparison_unreadable"
```

- [ ] **Step 4: 写服务模块**

新建 `src/stock_quant/service/strategy_challenges.py`：

```python
"""Read-only view over published one-time strategy-challenge results.

The service never recomputes a challenge: it reads the immutable
``data/strategy_challenges/results/<challenge_id>/strategy_comparison.json``
artifacts actually on disk and projects the decision-layer evidence verbatim
(spec 2026-10-04 §3.1). The mutable holdout registry
(``holdout_registry.parquet``, ``.holdout.lock``) is deliberately not read --
it is locked operational state, not published evidence (spec §2.2).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, Field, field_validator

from stock_quant.service.datasets import read_json_or_fail
from stock_quant.service.errors import (
    ChallengeUnreadable,
    ExperimentManifestUnreadable,
    UnknownExperiment,
)

_EXPERIMENT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
#: Only a hex64-named *directory* under ``results/`` is a published challenge.
_CHALLENGE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_COMPARISON_NAME = "strategy_comparison.json"
_MANIFEST_NAME = "experiment_manifest.json"


class UniverseIdentityView(BaseModel):
    """The four-field universe identity block, verbatim (spec §3.1)."""

    universe_id: str
    universe_version: str
    membership_table_sha256: str
    evidence_summary_sha256: str


class ChallengeDeclarationView(BaseModel):
    """The pre-registered identity fields, verbatim; constants omitted."""

    strategy_family: str
    baseline_experiment_id: str
    challenger_strategy_hash: str
    fold_schedule_hash: str
    comparison_policy_hash: str
    declared_before_run_at: str
    universe_definition: UniverseIdentityView

    @field_validator("declared_before_run_at")
    @classmethod
    def _iso_instant(cls, value: str) -> str:
        """Kept verbatim, but an unparseable instant is not a valid artifact."""
        try:
            datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(
                f"declared_before_run_at is not an ISO instant: {value!r}"
            ) from error
        return value


class ChallengeConsumptionView(BaseModel):
    """The holdout consumption fact kept where declaration/result do not
    already carry it (spec §3.1 (a)); ``declaration_sha256`` is omitted as
    verification-layer evidence, not decision-layer evidence."""

    status: str
    consumption_key: str
    consumed_at: str
    universe_definition: UniverseIdentityView


class MetricCellView(BaseModel):
    """One policy metric's paired values, threshold expression and verdict."""

    metric: str
    baseline: float | None = None
    challenger: float | None = None
    delta: float | None = None
    threshold: str
    passed: bool


class ScenarioResultView(BaseModel):
    scenario: str
    executed_fold_count: int
    passed: bool
    cells: list[MetricCellView] = Field(default_factory=list)


class ChallengeResultView(BaseModel):
    status: str
    conclusion: str | None = None
    challenger_stability_conclusion: str | None = None
    challenger_experiment_id: str | None = None
    executed_fold_count: int | None = None
    declared_scenario_count: int | None = None
    skipped_fold_ids: list[str] = Field(default_factory=list)
    failed_scenarios: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    scenario_results: list[ScenarioResultView] = Field(default_factory=list)
    error_code: str | None = None


class ChallengeView(BaseModel):
    challenge_id: str
    #: Which side of the pairing this experiment occupies (spec §2.3). A
    #: challenge never puts one experiment on both sides.
    role: Literal["baseline", "challenger"]
    declaration: ChallengeDeclarationView
    consumption: ChallengeConsumptionView | None = None
    result: ChallengeResultView


class ExperimentChallengesResponse(BaseModel):
    experiment_id: str
    challenges: list[ChallengeView]


router = APIRouter(prefix="/api/v1", tags=["strategy-challenges"])


def _experiment_dir(request: Request, experiment_id: str) -> Path:
    """The published experiment directory, else ``UnknownExperiment``.

    Mirrors ``results.py::_experiment_dir`` so the read-only surface keeps one
    trust rule per router without cross-router coupling.
    """
    root = (Path(request.app.state.project_root) / "data" / "experiments").resolve()
    directory = (root / experiment_id).resolve()
    if directory.parent != root or not directory.is_dir():
        raise UnknownExperiment(f"no published experiment {experiment_id!r}")
    return directory


def _strategy_snapshot_hash(directory: Path) -> str | None:
    """The manifest's top-level ``strategy_snapshot_sha256`` (spec §2.3).

    This is the exact field ``PublishedExperimentLoader.resolve`` scans, so a
    challenger-side match here agrees with the run that produced the result.
    An unreadable manifest fails closed with the ``list_experiments`` envelope.
    """
    path = directory / _MANIFEST_NAME
    if not path.is_file():
        return None
    payload = read_json_or_fail(path, ExperimentManifestUnreadable, _MANIFEST_NAME)
    if not isinstance(payload, dict):
        return None
    value = payload.get("strategy_snapshot_sha256")
    return value if isinstance(value, str) else None


def _load_comparison(path: Path) -> dict[str, Any]:
    """Read one published ``strategy_comparison.json`` as a JSON object."""
    payload = read_json_or_fail(path, ChallengeUnreadable, _COMPARISON_NAME)
    if not isinstance(payload, dict):
        raise ChallengeUnreadable(f"{_COMPARISON_NAME} is not a JSON object")
    return payload


def _project(
    payload: dict[str, Any],
    challenge_id: str,
    role: Literal["baseline", "challenger"],
) -> ChallengeView:
    """Project the published evidence verbatim (zero derivation, spec §3.1)."""
    consumption = payload.get("holdout_consumption")
    return ChallengeView(
        challenge_id=challenge_id,
        role=role,
        declaration=ChallengeDeclarationView.model_validate(payload["declaration"]),
        consumption=(
            ChallengeConsumptionView.model_validate(consumption)
            if isinstance(consumption, dict)
            else None
        ),
        result=ChallengeResultView.model_validate(payload["result"]),
    )


def _sort_key(view: ChallengeView) -> tuple[datetime, str]:
    """Ascending by declaration instant, ``challenge_id`` as the tiebreak."""
    return (
        datetime.fromisoformat(view.declaration.declared_before_run_at),
        view.challenge_id,
    )


@router.get(
    "/experiments/{experiment_id}/challenges",
    response_model=ExperimentChallengesResponse,
)
def experiment_challenges(
    experiment_id: Annotated[str, PathParam(pattern=_EXPERIMENT_ID_RE.pattern)],
    request: Request,
) -> ExperimentChallengesResponse:
    """Every published challenge this experiment takes part in (spec §2.3).

    Read-only and zero-derivation: membership is the two identity equalities
    -- baseline by experiment id, challenger by the manifest's strategy
    snapshot hash.
    """
    directory = _experiment_dir(request, experiment_id)
    strategy_hash = _strategy_snapshot_hash(directory)
    results_root = (
        Path(request.app.state.project_root)
        / "data"
        / "strategy_challenges"
        / "results"
    )
    views: list[ChallengeView] = []
    children = sorted(results_root.iterdir()) if results_root.is_dir() else []
    for child in children:
        if not child.is_dir() or not _CHALLENGE_ID_RE.fullmatch(child.name):
            continue
        payload = _load_comparison(child / _COMPARISON_NAME)
        declaration = payload["declaration"]
        is_baseline = declaration.get("baseline_experiment_id") == experiment_id
        is_challenger = strategy_hash is not None and (
            declaration.get("challenger_strategy_hash") == strategy_hash
        )
        if not is_baseline and not is_challenger:
            continue
        views.append(
            _project(payload, child.name, "baseline" if is_baseline else "challenger")
        )
    views.sort(key=_sort_key)
    return ExperimentChallengesResponse(experiment_id=experiment_id, challenges=views)
```

- [ ] **Step 5: 注册路由**

`src/stock_quant/service/app.py` 第 21 行改为：

```python
from stock_quant.service import (
    benchmarks,
    datasets,
    experiments,
    results,
    strategy_challenges,
    tables,
)
```

并在 `app.include_router(results.router, responses=error_responses)` 之后加：

```python
    app.include_router(strategy_challenges.router, responses=error_responses)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/service/test_challenge_endpoints.py -q`
Expected: 12 passed。

- [ ] **Step 7: 跑相邻回归**

Run: `pytest tests/service/test_results_endpoints.py tests/service/test_app_datasets.py -q`
Expected: 全绿。

- [ ] **Step 8: 提交**

```bash
git add src/stock_quant/service/strategy_challenges.py \
        src/stock_quant/service/errors.py \
        src/stock_quant/service/app.py \
        tests/service/test_challenge_endpoints.py
git commit -m "$(cat <<'EOF'
feat(service): read-only challenge adjudication endpoint

Project published strategy_challenge results verbatim and match them to an
experiment by baseline id or by the manifest's strategy snapshot hash.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 失败闭合与 FAILED 三形态

**Files:**
- Modify: `src/stock_quant/service/strategy_challenges.py`（`_load_comparison`、`_project`）
- Modify: `tests/service/test_service_boundary.py`（末尾追加探针）
- Test: `tests/service/test_challenge_endpoints.py`（追加用例）

**Interfaces:**
- Consumes: Task 1 的 `_load_comparison` / `_project` / `ChallengeUnreadable`。
- Produces: 契约收敛 —— 任一 hex64 结果目录里的坏文件都以 `500 challenge_comparison_unreadable` 失败，而不是 `internal_error` 或静默跳过。

- [ ] **Step 1: 写失败的测试**

在 `tests/service/test_challenge_endpoints.py` 末尾追加：

```python
def test_missing_comparison_file_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    # hex64 目录但产物缺失：仍是"本挑战存在且不可读"，不是"本挑战不存在"。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    directory = service_project / "data" / "strategy_challenges" / "results" / CHALLENGE_ID
    directory.mkdir(parents=True)
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_non_json_comparison_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    directory = write_challenge(
        service_project, comparison_payload(consumption=consumption_record())
    )
    (directory / "strategy_comparison.json").write_text("{not json", encoding="utf-8")
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_non_object_comparison_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    directory = write_challenge(
        service_project, comparison_payload(consumption=consumption_record())
    )
    (directory / "strategy_comparison.json").write_text("[1, 2, 3]", encoding="utf-8")
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_missing_declaration_or_result_block_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    payload = comparison_payload(consumption=consumption_record())
    payload.pop("result")
    write_challenge(service_project, payload)
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_payload_violating_the_declared_subset_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    # 决策层字段缺失 => 不是本模型声明的形状：失败闭合，不半截展示。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    payload = comparison_payload(consumption=consumption_record())
    payload["declaration"]["universe_definition"].pop("evidence_summary_sha256")
    write_challenge(service_project, payload)
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_non_iso_declaration_instant_fails_closed(
    client: TestClient, service_project: Path
) -> None:
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            declared_before_run_at="yesterday afternoon",
            consumption=consumption_record(),
        ),
    )
    response = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "challenge_comparison_unreadable"


def test_refused_consumption_projects_a_null_record(
    client: TestClient, service_project: Path
) -> None:
    # FAILED 形态一：注册表在消费前拒绝。consumption 为 null 且这里确实没消费。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            status="FAILED",
            conclusion=None,
            error_code="HOLDOUT_CONSUMPTION_REFUSED",
            reasons=("holdout already consumed",),
            consumption=None,
        ),
    )
    view = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()[
        "challenges"
    ][0]
    assert view["role"] == "baseline"
    assert view["consumption"] is None
    assert view["result"]["status"] == "FAILED"
    assert view["result"]["conclusion"] is None
    assert view["result"]["error_code"] == "HOLDOUT_CONSUMPTION_REFUSED"
    assert view["result"]["reasons"] == ["holdout already consumed"]


def test_experiment_load_error_projects_a_null_record(
    client: TestClient, service_project: Path
) -> None:
    # FAILED 形态二：消费已成功，但 _publish_failure 恒传 consumption=None。
    # 服务层必须原样透传 null —— 消费与否由 error_code 与记录两轴共同判定。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            status="FAILED",
            conclusion=None,
            error_code="EXPERIMENT_LOAD_ERROR",
            reasons=("baseline artifacts unreadable",),
            consumption=None,
        ),
    )
    view = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()[
        "challenges"
    ][0]
    assert view["consumption"] is None
    assert view["result"]["error_code"] == "EXPERIMENT_LOAD_ERROR"


def test_evaluation_failure_keeps_the_real_consumption_record(
    client: TestClient, service_project: Path
) -> None:
    # FAILED 形态三：评估期完整性失败，消费记录随结果一并发布。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            status="FAILED",
            conclusion=None,
            error_code="IDENTITY_MISMATCH",
            reasons=("manifest identity mismatch",),
            consumption=consumption_record(),
        ),
    )
    view = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()[
        "challenges"
    ][0]
    assert view["consumption"] is not None
    assert view["consumption"]["status"] == "consumed"
    assert view["consumption"]["consumption_key"].endswith(FOLD_SCHEDULE_HASH)
    assert view["result"]["error_code"] == "IDENTITY_MISMATCH"


def test_a_failed_challenge_appears_on_its_baseline_page_only(
    client: TestClient, service_project: Path
) -> None:
    # FAILED 恒无 challenger 实验 id；best-effort 归属只在 baseline 侧成立。
    write_experiment_manifest(service_project, EXPERIMENT_ID, BASELINE_HASH)
    write_challenge(
        service_project,
        comparison_payload(
            status="FAILED",
            conclusion=None,
            error_code="PAIRING_INCOMPLETE",
            consumption=consumption_record(),
        ),
    )
    body = client.get(f"/api/v1/experiments/{EXPERIMENT_ID}/challenges").json()
    assert [view["role"] for view in body["challenges"]] == ["baseline"]
```

在 `tests/service/test_service_boundary.py` 末尾追加：

```python
def test_importing_the_service_never_pulls_in_the_challenge_subsystem() -> None:
    probe = (
        "import sys, stock_quant.service; "
        "print('stock_quant.research.strategy_challenge' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/service/test_challenge_endpoints.py tests/service/test_service_boundary.py -q`
Expected:
- **3 条 FAIL**：`test_missing_declaration_or_result_block_fails_closed`（KeyError → 泛化 500 `internal_error`）、`test_payload_violating_the_declared_subset_fails_closed`、`test_non_iso_declaration_instant_fails_closed`（两者都是 pydantic ValidationError 逃逸成 `internal_error`），断言的都是 `challenge_comparison_unreadable`。
- **3 条已 PASS**（钉成回归）：`test_missing_comparison_file_fails_closed`、`test_non_json_comparison_fails_closed`、`test_non_object_comparison_fails_closed` —— Task 1 的 `read_json_or_fail` 与 `isinstance(payload, dict)` 已覆盖。
- **4 条投影用例已 PASS**（FAILED 三形态只是投影，Task 1 已能透传）。
- import 探针已 PASS（Task 1 就没引入 research）。

- [ ] **Step 3: 收紧 `_load_comparison`**

把 `src/stock_quant/service/strategy_challenges.py` 的 `_load_comparison` 换成：

```python
def _load_comparison(path: Path) -> dict[str, Any]:
    """Read and shape-check one published ``strategy_comparison.json``.

    A file that exists but is not a JSON object, or that lacks the
    declaration/result blocks every challenge always carries, fails closed
    (spec §3.2): a silent skip would hide a challenge that consumed the
    holdout.
    """
    payload = read_json_or_fail(path, ChallengeUnreadable, _COMPARISON_NAME)
    if not isinstance(payload, dict):
        raise ChallengeUnreadable(f"{_COMPARISON_NAME} is not a JSON object")
    if not isinstance(payload.get("declaration"), dict) or not isinstance(
        payload.get("result"), dict
    ):
        raise ChallengeUnreadable(
            f"{_COMPARISON_NAME} carries no declaration/result block"
        )
    return payload
```

- [ ] **Step 4: 收紧 `_project`**

把 `_project` 换成（并把 `from pydantic import BaseModel, Field, field_validator` 改为 `from pydantic import BaseModel, Field, ValidationError, field_validator`）：

```python
def _project(
    payload: dict[str, Any],
    challenge_id: str,
    role: Literal["baseline", "challenger"],
) -> ChallengeView:
    """Project the published evidence verbatim (zero derivation, spec §3.1).

    Unknown fields are dropped by the models' default ``extra="ignore"``; a
    payload that does not satisfy the declared subset fails closed instead of
    being shown partially.
    """
    consumption = payload.get("holdout_consumption")
    try:
        return ChallengeView(
            challenge_id=challenge_id,
            role=role,
            declaration=ChallengeDeclarationView.model_validate(
                payload["declaration"]
            ),
            consumption=(
                ChallengeConsumptionView.model_validate(consumption)
                if isinstance(consumption, dict)
                else None
            ),
            result=ChallengeResultView.model_validate(payload["result"]),
        )
    except ValidationError as error:
        raise ChallengeUnreadable(
            f"{_COMPARISON_NAME} of {challenge_id} does not project: {error}"
        ) from error
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/service/test_challenge_endpoints.py tests/service/test_service_boundary.py -q`
Expected: 全绿（端点 12 + 追加 10 = 22 条，边界 6 条）。

- [ ] **Step 6: 提交**

```bash
git add src/stock_quant/service/strategy_challenges.py \
        tests/service/test_challenge_endpoints.py \
        tests/service/test_service_boundary.py
git commit -m "$(cat <<'EOF'
feat(service): fail closed on unreadable challenge comparisons

A malformed strategy_comparison.json now returns the stable
challenge_comparison_unreadable envelope instead of a generic 500, and the
three FAILED holdout-consumption forms are pinned by tests.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Web 接线 — 类型、客户端、徽章语义

**Files:**
- Modify: `web/src/api/types.ts`（文件末尾追加）
- Modify: `web/src/api/client.ts:33-63`（接口）、`client.ts:189-197` 之后（实现）
- Modify: `web/src/components/StateBadge.vue:3`、`StateBadge.vue:17-36`
- Modify: `web/tests/helpers.ts:20-35`
- Test: `web/tests/state-badge.spec.ts`（追加）

**Interfaces:**
- Consumes: Task 1 的端点响应形状（字段名逐一对齐）。
- Produces（Task 4/5 依赖）：
  - `ApiClient.listExperimentChallenges(experimentId: string): Promise<ExperimentChallengesResponse>`
  - `BadgeKind` 增加 `"challenge"`
  - TS 类型：`ExperimentChallengesResponse`、`ChallengeView`、`ChallengeDeclarationView`、`ChallengeConsumptionView`、`ChallengeUniverseIdentity`、`ChallengeResultView`、`ChallengeScenarioResult`、`ChallengeMetricCell`

- [ ] **Step 1: 写失败的徽章测试**

`web/tests/state-badge.spec.ts` 第 1-11 行改为：

```ts
// web/tests/state-badge.spec.ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPortalRouter } from "../src/router";
import StateBadge, { type BadgeKind } from "../src/components/StateBadge.vue";

function mountBadge(kind: BadgeKind, value: string) {
  return mount(StateBadge, {
    props: { kind, value },
    global: { plugins: [createPortalRouter()] },
  });
}
```

在 `describe` 块内、"未知值诚实回落"用例之前插入：

```ts
  it("挑战裁决四态映射（FAILED 与 REJECTED 同为阻断红）", () => {
    const cases: Array<[string, string]> = [
      ["PROMOTED", "pass"],
      ["REJECTED", "block"],
      ["INCONCLUSIVE_RESEARCH_ONLY", "pending"],
      ["FAILED", "block"],
    ];
    for (const [value, tone] of cases) {
      const wrapper = mountBadge("challenge", value);
      expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain(
        `badge-state--${tone}`,
      );
      expect(wrapper.get('[data-testid="state-badge"]').text()).toBe(value);
    }
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm run test -- tests/state-badge.spec.ts`
Expected: FAIL — `challenge` 不在 `BadgeKind` 里，且 `TONES.challenge` 为 `undefined`。

- [ ] **Step 3: 加徽章语义映射**

`web/src/components/StateBadge.vue` 第 3 行改为：

```ts
export type BadgeKind = "acceptance" | "conclusion" | "job" | "challenge";
```

在 `TONES` 的 `job` 组之后追加：

```ts
  challenge: {
    PROMOTED: "pass",
    REJECTED: "block",
    INCONCLUSIVE_RESEARCH_ONLY: "pending",
    FAILED: "block",
  },
```

- [ ] **Step 4: 加 TS 类型**

在 `web/src/api/types.ts` 末尾追加：

```ts
/** S4 前增量：详情页挑战裁决区块（spec 2026-10-04 §3.1）。逐字投影，零派生。 */
export interface ChallengeUniverseIdentity {
  universe_id: string;
  universe_version: string;
  membership_table_sha256: string;
  evidence_summary_sha256: string;
}

export interface ChallengeDeclarationView {
  strategy_family: string;
  baseline_experiment_id: string;
  challenger_strategy_hash: string;
  fold_schedule_hash: string;
  comparison_policy_hash: string;
  declared_before_run_at: string;
  universe_definition: ChallengeUniverseIdentity;
}

export interface ChallengeConsumptionView {
  status: string;
  consumption_key: string;
  consumed_at: string;
  universe_definition: ChallengeUniverseIdentity;
}

export interface ChallengeMetricCell {
  metric: string;
  baseline: number | null;
  challenger: number | null;
  delta: number | null;
  threshold: string;
  passed: boolean;
}

export interface ChallengeScenarioResult {
  scenario: string;
  executed_fold_count: number;
  passed: boolean;
  cells: ChallengeMetricCell[];
}

export interface ChallengeResultView {
  /** "COMPLETED" | "FAILED"（服务侧是裸 str，前端不新增枚举约束）。 */
  status: string;
  conclusion: string | null;
  challenger_stability_conclusion: string | null;
  challenger_experiment_id: string | null;
  executed_fold_count: number | null;
  declared_scenario_count: number | null;
  skipped_fold_ids: string[];
  failed_scenarios: string[];
  reasons: string[];
  scenario_results: ChallengeScenarioResult[];
  error_code: string | null;
}

export interface ChallengeView {
  challenge_id: string;
  role: "baseline" | "challenger";
  declaration: ChallengeDeclarationView;
  consumption: ChallengeConsumptionView | null;
  result: ChallengeResultView;
}

export interface ExperimentChallengesResponse {
  experiment_id: string;
  challenges: ChallengeView[];
}
```

- [ ] **Step 5: 加客户端方法**

`web/src/api/client.ts`：在 import 列表里按现有的大小写不敏感字典序插入 `ExperimentChallengesResponse,`（落在 `DatasetListResponse,` 与 `ExperimentResultsResponse,` 之间），在 `ApiClient` 接口的 `datasetBenchmark(...)` 之后加：

```ts
  listExperimentChallenges(experimentId: string): Promise<ExperimentChallengesResponse>;
```

在 `createApiClient` 返回对象的 `datasetBenchmark(...)` 之后加：

```ts
    listExperimentChallenges(experimentId: string) {
      return requestJson<ExperimentChallengesResponse>(
        `/api/v1/experiments/${encodeURIComponent(experimentId)}/challenges`,
      );
    },
```

- [ ] **Step 6: 补测试夹具基座**

`web/tests/helpers.ts` 的 `fakeClient` 基座里，在 `datasetBenchmark: unstubbed,` 之后加：

```ts
    listExperimentChallenges: unstubbed,
```

- [ ] **Step 7: 跑测试与类型检查**

Run: `cd web && npm run test -- tests/state-badge.spec.ts && npm run typecheck`
Expected: 徽章 6 passed；`vue-tsc` 无错误。

- [ ] **Step 8: 提交**

```bash
git add web/src/api/types.ts web/src/api/client.ts \
        web/src/components/StateBadge.vue \
        web/tests/helpers.ts web/tests/state-badge.spec.ts
git commit -m "$(cat <<'EOF'
feat(web): type and client plumbing for challenge results

Add the read-only listExperimentChallenges client, the response types, and
the challenge badge tone mapping.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `ChallengeBlock` 组件

**Files:**
- Create: `web/src/components/ChallengeBlock.vue`
- Test: `web/tests/challenge-block.spec.ts`

**Interfaces:**
- Consumes: Task 3 的 `ApiClient.listExperimentChallenges`、`ExperimentChallengesResponse`、`ChallengeView`、`ChallengeScenarioResult`、`StateBadge` 的 `"challenge"`、`Card`/`DataTable`/`EmptyState`/`Skeleton`/`toDisplayError`。
- Produces（Task 5 依赖）：`ChallengeBlock` 默认导出，prop `experimentId: string`；`data-testid`：`challenge-block` / `challenge-honesty` / `challenge-error` / `challenge-empty` / `challenge-card` / `challenge-role` / `challenge-copy-id` / `challenge-verdict` / `challenge-identity` / `challenge-reasons` / `challenge-error-code` / `challenge-failed-scenarios` / `challenge-skipped-folds` / `challenge-scenario` / `challenge-cells` / `challenge-consumption` / `challenge-consumed-warning`。区块内的 `<button>` 只有"复制 challenge_id"一个（§4.2-1 的 provenance 复制），没有任何触发/重跑入口（§4.3）。

- [ ] **Step 1: 写失败的组件测试**

新建 `web/tests/challenge-block.spec.ts`：

```ts
import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { ApiError, apiClientKey, type ApiClient } from "../src/api/client";
import { createPortalRouter } from "../src/router";
import ChallengeBlock from "../src/components/ChallengeBlock.vue";
import { fakeClient, flushPromises } from "./helpers";
import type { ChallengeView, ExperimentChallengesResponse } from "../src/api/types";

const EX = "e".repeat(64);
const CHALLENGE_ID = "a".repeat(64);
const CHALLENGER_HASH = "2".repeat(64);
const UNIVERSE = {
  universe_id: "custom_csi300_tw_tradable",
  universe_version: "3".repeat(64),
  membership_table_sha256: "4".repeat(64),
  evidence_summary_sha256: "5".repeat(64),
};

function challengeView(overrides: Partial<ChallengeView> = {}): ChallengeView {
  const base: ChallengeView = {
    challenge_id: CHALLENGE_ID,
    role: "baseline",
    declaration: {
      strategy_family: "buffered_risk_weighted_momentum",
      baseline_experiment_id: EX,
      challenger_strategy_hash: CHALLENGER_HASH,
      fold_schedule_hash: "f".repeat(64),
      comparison_policy_hash: "6".repeat(64),
      declared_before_run_at: "2026-10-01T00:00:00+00:00",
      universe_definition: { ...UNIVERSE },
    },
    consumption: {
      status: "consumed",
      consumption_key: `buffered_risk_weighted_momentum:${"f".repeat(64)}`,
      consumed_at: "2026-10-01T00:00:01+00:00",
      universe_definition: { ...UNIVERSE },
    },
    result: {
      status: "COMPLETED",
      conclusion: "PROMOTED",
      challenger_stability_conclusion: "STABLE",
      challenger_experiment_id: "c".repeat(64),
      executed_fold_count: 6,
      declared_scenario_count: 3,
      skipped_fold_ids: [],
      failed_scenarios: [],
      reasons: [],
      scenario_results: [
        {
          scenario: "full_cost",
          executed_fold_count: 6,
          passed: true,
          cells: [
            {
              metric: "aggregate_sharpe_delta",
              baseline: 1.0,
              challenger: 1.3,
              delta: 0.3,
              threshold: ">= 0.10",
              passed: true,
            },
          ],
        },
      ],
      error_code: null,
    },
  };
  return { ...base, ...overrides };
}

function payload(challenges: ChallengeView[]): ExperimentChallengesResponse {
  return { experiment_id: EX, challenges };
}

function mountBlock(client: ApiClient, experimentId: string = EX) {
  return mount(ChallengeBlock, {
    props: { experimentId },
    global: {
      plugins: [createPortalRouter()],
      provide: { [apiClientKey as symbol]: client },
    },
  });
}

function clientFor(challenges: ChallengeView[]): ApiClient {
  return fakeClient({
    listExperimentChallenges: async () => payload(challenges),
  });
}

describe("ChallengeBlock（挑战裁决区块）", () => {
  it("空态：标题、原因与 CLI 引导", async () => {
    const wrapper = mountBlock(clientFor([]));
    await flushPromises();
    const empty = wrapper.get('[data-testid="challenge-empty"]');
    expect(empty.text()).toContain("本实验尚无挑战裁决");
    expect(empty.text()).toContain("尚未作为 baseline 或 challenger");
    expect(empty.text()).toContain("research challenge --declaration");
    // 诚实文案常驻（§4.3）。
    expect(wrapper.get('[data-testid="challenge-honesty"]').text()).toContain(
      "一经消耗即不可恢复",
    );
  });

  it("COMPLETED：角色、徽章、身份摘要、阈值表逐字渲染", async () => {
    const wrapper = mountBlock(clientFor([challengeView()]));
    await flushPromises();
    const card = wrapper.get('[data-testid="challenge-card"]');
    expect(card.get('[data-testid="challenge-role"]').text()).toContain(
      "本实验为 baseline",
    );
    expect(card.get('[data-testid="challenge-verdict"]').get('[data-testid="state-badge"]').text())
      .toBe("PROMOTED");
    const identity = card.get('[data-testid="challenge-identity"]').text();
    expect(identity).toContain("buffered_risk_weighted_momentum");
    expect(identity).toContain("2".repeat(8));
    const table = card.get('[data-testid="challenge-cells"]');
    expect(table.text()).toContain("aggregate_sharpe_delta");
    expect(table.text()).toContain(">= 0.10");
    expect(table.text()).toContain("0.3000");
  });

  it("consumption 非 null：展示消费键/时刻/universe 四字段 + 已消耗文案", async () => {
    const wrapper = mountBlock(clientFor([challengeView()]));
    await flushPromises();
    const block = wrapper.get('[data-testid="challenge-consumption"]');
    expect(block.text()).toContain("本次消耗了 holdout");
    expect(block.text()).toContain("buffered_risk_weighted_momentum:");
    expect(block.text()).toContain("2026-10-01T00:00:01+00:00");
    expect(block.text()).toContain("custom_csi300_tw_tradable");
    expect(block.text()).toContain("4".repeat(8));
    expect(block.text()).toContain("5".repeat(8));
    expect(wrapper.find('[data-testid="challenge-consumed-warning"]').exists()).toBe(false);
  });

  it("FAILED + HOLDOUT_CONSUMPTION_REFUSED：未消费文案，且不得出现‘已消耗’", async () => {
    const view = challengeView({
      consumption: null,
      result: {
        ...challengeView().result,
        status: "FAILED",
        conclusion: null,
        error_code: "HOLDOUT_CONSUMPTION_REFUSED",
        reasons: ["holdout already consumed"],
        scenario_results: [],
      },
    });
    const wrapper = mountBlock(clientFor([view]));
    await flushPromises();
    const block = wrapper.get('[data-testid="challenge-consumption"]');
    expect(block.text()).toContain("消费被拒：本挑战未取得 holdout 消费");
    expect(block.text()).not.toContain("已消耗");
    expect(wrapper.find('[data-testid="challenge-consumed-warning"]').exists()).toBe(false);
    // FAILED 的 error_code 与 reasons 必须显式渲染（§4.2-7）。
    expect(wrapper.get('[data-testid="challenge-error-code"]').text()).toContain(
      "HOLDOUT_CONSUMPTION_REFUSED",
    );
    expect(wrapper.get('[data-testid="challenge-reasons"]').text()).toContain(
      "holdout already consumed",
    );
    expect(wrapper.get('[data-testid="challenge-verdict"]').get('[data-testid="state-badge"]').text())
      .toBe("FAILED");
  });

  it("FAILED + EXPERIMENT_LOAD_ERROR：null 记录但必须说‘已消耗但未附消费记录’", async () => {
    const view = challengeView({
      consumption: null,
      result: {
        ...challengeView().result,
        status: "FAILED",
        conclusion: null,
        error_code: "EXPERIMENT_LOAD_ERROR",
        reasons: ["baseline artifacts unreadable"],
        scenario_results: [],
      },
    });
    const wrapper = mountBlock(clientFor([view]));
    await flushPromises();
    const block = wrapper.get('[data-testid="challenge-consumption"]');
    expect(block.text()).toContain("已被本次挑战消耗，但失败结果未附消费记录");
    expect(wrapper.get('[data-testid="challenge-consumed-warning"]').text()).toContain(
      "仍已消耗 holdout",
    );
  });

  it("FAILED + IDENTITY_MISMATCH：展示真实消费记录，并标注仍已消耗", async () => {
    const view = challengeView({
      result: {
        ...challengeView().result,
        status: "FAILED",
        conclusion: null,
        error_code: "IDENTITY_MISMATCH",
        reasons: ["manifest identity mismatch"],
        scenario_results: [],
      },
    });
    const wrapper = mountBlock(clientFor([view]));
    await flushPromises();
    const block = wrapper.get('[data-testid="challenge-consumption"]');
    expect(block.text()).toContain("本次消耗了 holdout");
    expect(block.text()).toContain("buffered_risk_weighted_momentum:");
    expect(wrapper.get('[data-testid="challenge-consumed-warning"]').text()).toContain(
      "仍已消耗 holdout",
    );
  });

  it("failed_scenarios / skipped_fold_ids 非空时标注", async () => {
    const view = challengeView({
      result: {
        ...challengeView().result,
        failed_scenarios: ["zero_cost"],
        skipped_fold_ids: ["9".repeat(64)],
      },
    });
    const wrapper = mountBlock(clientFor([view]));
    await flushPromises();
    expect(wrapper.get('[data-testid="challenge-failed-scenarios"]').text()).toContain(
      "zero_cost",
    );
    expect(wrapper.get('[data-testid="challenge-skipped-folds"]').text()).toContain(
      "9".repeat(8),
    );
  });

  it("challenge_id 的复制按钮写入剪贴板，且区块内没有第二个按钮（§4.2-1 / §4.3）", async () => {
    const wrapper = mountBlock(clientFor([challengeView()]));
    await flushPromises();
    const writeText = vi.fn(async () => undefined);
    // happy-dom 20 的 navigator.clipboard 是 getter-only，用 defineProperty 注入假实现。
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const buttons = wrapper.findAll('[data-testid="challenge-block"] button');
    expect(buttons.map((button) => button.text())).toEqual(["复制"]);
    await wrapper.get('[data-testid="challenge-copy-id"]').trigger("click");
    expect(writeText).toHaveBeenCalledWith(CHALLENGE_ID);
  });

  it("加载失败：渲染稳定 code 与安全摘要，不渲染堆栈", async () => {
    const client = fakeClient({
      listExperimentChallenges: async () => {
        throw new ApiError(500, "challenge_comparison_unreadable", "comparison unreadable");
      },
    });
    const wrapper = mountBlock(client);
    await flushPromises();
    const error = wrapper.get('[data-testid="challenge-error"]');
    expect(error.text()).toContain("challenge_comparison_unreadable");
    expect(error.text()).toContain("comparison unreadable");
    expect(wrapper.text()).not.toContain("stack");
  });

  it("初始渲染骨架，取数完成前不渲染区块内容", async () => {
    let release!: (value: ExperimentChallengesResponse) => void;
    const client = fakeClient({
      listExperimentChallenges: () =>
        new Promise<ExperimentChallengesResponse>((resolve) => {
          release = resolve;
        }),
    });
    const wrapper = mountBlock(client);
    await flushPromises();
    expect(wrapper.find('[data-testid="skeleton"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="challenge-honesty"]').exists()).toBe(false);
    release(payload([]));
    await flushPromises();
    expect(wrapper.find('[data-testid="skeleton"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="challenge-empty"]').exists()).toBe(true);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm run test -- tests/challenge-block.spec.ts`
Expected: FAIL — `Failed to resolve import "../src/components/ChallengeBlock.vue"`。

- [ ] **Step 3: 写组件**

新建 `web/src/components/ChallengeBlock.vue`：

```vue
<!-- web/src/components/ChallengeBlock.vue —— 详情页挑战裁决区块（spec 2026-10-04）。
     只读已发布的 strategy_comparison.json 投影：逐字展示，零派生、零重算。
     消费事实按 error_code × consumption 两轴三分支判定（§4.2-6/7）。 -->
<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type {
  ChallengeScenarioResult,
  ChallengeView,
  ExperimentChallengesResponse,
} from "../api/types";
import Card from "./Card.vue";
import DataTable, { type DataTableColumn } from "./DataTable.vue";
import EmptyState from "./EmptyState.vue";
import Skeleton from "./Skeleton.vue";
import StateBadge from "./StateBadge.vue";

const props = defineProps<{ experimentId: string }>();
const client = useApiClient();

// 复制模式沿用顶栏 copy-version / 注册台 copy-command：navigator.clipboard
// 存在才可点（happy-dom 下是 getter-only，测试用 defineProperty 注入假实现）。
// 复制只是 provenance 便利，不是动作入口（spec §4.3）。
const canCopy = typeof navigator !== "undefined" && Boolean(navigator.clipboard);

async function copyChallengeId(value: string) {
  if (canCopy) {
    await navigator.clipboard.writeText(value);
  }
}

const payload = ref<ExperimentChallengesResponse | null>(null);
const loaded = ref(false);
const error = ref<DisplayError | null>(null);

const CELL_COLUMNS: DataTableColumn[] = [
  { key: "metric", label: "指标" },
  { key: "baseline", label: "baseline", align: "right" },
  { key: "challenger", label: "challenger", align: "right" },
  { key: "delta", label: "Δ", align: "right" },
  { key: "threshold", label: "门槛" },
  { key: "passed", label: "判定" },
];

/** 显示层格式化：单值定宽，绝不回写契约、不重算判定。 */
function fmt(value: number | null): string {
  return value === null ? "—" : value.toFixed(4);
}

function cellRows(result: ChallengeScenarioResult): Array<Record<string, unknown>> {
  return result.cells.map((cell) => ({
    metric: cell.metric,
    baseline: fmt(cell.baseline),
    challenger: fmt(cell.challenger),
    delta: fmt(cell.delta),
    threshold: cell.threshold,
    passed: cell.passed ? "通过" : "未通过",
  }));
}

function shortHash(value: string): string {
  return value.slice(0, 8);
}

/** §4.2-6：三分支。只看 consumption == null 会把 REFUSED 与 LOAD_ERROR 说成同一件事。 */
function consumptionNote(view: ChallengeView): string {
  if (view.consumption !== null) return "本次消耗了 holdout。";
  if (view.result.error_code === "HOLDOUT_CONSUMPTION_REFUSED") {
    return "消费被拒：本挑战未取得 holdout 消费。";
  }
  return "holdout 已被本次挑战消耗，但失败结果未附消费记录。";
}

/** §4.2-7：只有真正发生过消费的 FAILED 才加这句；REFUSED 一支不得出现。 */
function showsConsumedWarning(view: ChallengeView): boolean {
  return (
    view.result.status === "FAILED" &&
    (view.consumption !== null ||
      view.result.error_code !== "HOLDOUT_CONSUMPTION_REFUSED")
  );
}

onMounted(async () => {
  try {
    payload.value = await client.listExperimentChallenges(props.experimentId);
  } catch (cause) {
    error.value = toDisplayError(cause);
  } finally {
    loaded.value = true;
  }
});
</script>

<template>
  <Card title="挑战裁决" testid="challenge-block">
    <p v-if="error !== null" class="error" data-testid="challenge-error">
      挑战裁决加载失败 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <Skeleton v-else-if="!loaded" :rows="4" />
    <template v-else>
      <!-- 断言敏感的句子不跨行：Vue 模板空白压缩会把模板内换行保留成 \n，
           拆行会破坏 text() 的 toContain（同 RegisterPage 的诚实边界注释）。 -->
      <p class="challenge-honesty" data-testid="challenge-honesty">
        挑战是一次性持有集（holdout）的预注册配对比较：一笔 holdout 一经消耗即不可恢复，
        改变数据集版本、参数、成本情景或失败结果都不会恢复它。本区块是既有裁决的证据展示——
        web 不发起挑战、不能重跑（对已消费的 holdout 重复声明会被
        <code>HOLDOUT_CONSUMPTION_REFUSED</code> 拒绝）。
      </p>
      <div v-if="(payload?.challenges.length ?? 0) === 0" data-testid="challenge-empty">
        <EmptyState
          title="本实验尚无挑战裁决"
          description="该实验尚未作为 baseline 或 challenger 参与任何一次性挑战。发起挑战：python -m stock_quant research challenge --declaration <declaration.json> --root <root>"
        />
      </div>
      <article
        v-for="view in payload?.challenges ?? []"
        :key="view.challenge_id"
        class="challenge-card"
        data-testid="challenge-card"
      >
        <p class="challenge-role" data-testid="challenge-role">
          {{ view.role === "baseline" ? "本实验为 baseline" : "本实验为 challenger" }}
          · <code :title="view.challenge_id">{{ shortHash(view.challenge_id) }}</code>
          <button
            type="button"
            data-testid="challenge-copy-id"
            :disabled="!canCopy"
            @click="copyChallengeId(view.challenge_id)"
          >
            复制
          </button>
        </p>
        <p class="challenge-verdict" data-testid="challenge-verdict">
          <StateBadge
            kind="challenge"
            :value="view.result.conclusion ?? view.result.status"
          />
          <span class="challenge-time">{{ view.declaration.declared_before_run_at }}</span>
        </p>
        <p class="resolved" data-testid="challenge-identity">
          家族 {{ view.declaration.strategy_family }}
          · 挑战方策略快照
          <code :title="view.declaration.challenger_strategy_hash">{{ shortHash(view.declaration.challenger_strategy_hash) }}</code>
          · 冻结政策
          <code :title="view.declaration.comparison_policy_hash">{{ shortHash(view.declaration.comparison_policy_hash) }}</code>
          · 折调度
          <code :title="view.declaration.fold_schedule_hash">{{ shortHash(view.declaration.fold_schedule_hash) }}</code>
        </p>
        <ul
          v-if="view.result.reasons.length > 0"
          class="challenge-reasons"
          data-testid="challenge-reasons"
        >
          <li v-for="(reason, index) in view.result.reasons" :key="index">{{ reason }}</li>
        </ul>
        <p
          v-if="view.result.error_code !== null"
          class="challenge-error-code"
          data-testid="challenge-error-code"
        >
          失败码 <code>{{ view.result.error_code }}</code>
        </p>
        <p
          v-if="view.result.failed_scenarios.length > 0"
          class="challenge-line"
          data-testid="challenge-failed-scenarios"
        >
          未通过情景：{{ view.result.failed_scenarios.join("、") }}
        </p>
        <p
          v-if="view.result.skipped_fold_ids.length > 0"
          class="challenge-line"
          data-testid="challenge-skipped-folds"
        >
          跳过折：
          <span v-for="fold in view.result.skipped_fold_ids" :key="fold">
            <code :title="fold">{{ shortHash(fold) }}</code>
          </span>
        </p>
        <section
          v-for="scenario in view.result.scenario_results"
          :key="scenario.scenario"
          class="challenge-scenario"
          data-testid="challenge-scenario"
        >
          <p class="challenge-scenario-head">
            情景 <strong>{{ scenario.scenario }}</strong>
            · 执行折 {{ scenario.executed_fold_count }}
            · {{ scenario.passed ? "全部门槛通过" : "存在未通过门槛" }}
          </p>
          <DataTable
            testid="challenge-cells"
            row-testid="challenge-cell"
            :rows="cellRows(scenario)"
            :columns="CELL_COLUMNS"
          />
        </section>
        <div class="challenge-consumption" data-testid="challenge-consumption">
          <p>{{ consumptionNote(view) }}</p>
          <template v-if="view.consumption !== null">
            <p>
              消费键 <code>{{ view.consumption.consumption_key }}</code>
              · 消费于 {{ view.consumption.consumed_at }}
            </p>
            <p class="resolved">
              universe {{ view.consumption.universe_definition.universe_id }}
              · 成员表
              <code :title="view.consumption.universe_definition.membership_table_sha256">{{ shortHash(view.consumption.universe_definition.membership_table_sha256) }}</code>
              · 证据摘要
              <code :title="view.consumption.universe_definition.evidence_summary_sha256">{{ shortHash(view.consumption.universe_definition.evidence_summary_sha256) }}</code>
            </p>
          </template>
          <p
            v-if="showsConsumedWarning(view)"
            class="challenge-consumed-warning"
            data-testid="challenge-consumed-warning"
          >
            该挑战仍已消耗 holdout。
          </p>
        </div>
      </article>
    </template>
  </Card>
</template>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npm run test -- tests/challenge-block.spec.ts && npm run typecheck`
Expected: 10 passed；`vue-tsc` 无错误。

- [ ] **Step 5: 提交**

```bash
git add web/src/components/ChallengeBlock.vue web/tests/challenge-block.spec.ts
git commit -m "$(cat <<'EOF'
feat(web): challenge adjudication block component

Render the published challenge evidence verbatim, with the three holdout
consumption branches decided by error_code x consumption rather than by the
null check alone.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 挂载到详情页

**Files:**
- Modify: `web/src/pages/StrategyDetailPage.vue:17`（import）、`StrategyDetailPage.vue:404-405`（挂载点）
- Modify: `web/tests/strategy-detail-page.spec.ts:77-93`（`detailClient` 基座）
- Modify: `web/e2e/portal.spec.ts:397` 之后（详情页路由 mock）
- Test: `web/tests/strategy-detail-page.spec.ts`（追加）

**Interfaces:**
- Consumes: Task 4 的 `ChallengeBlock`（prop `experimentId: string`）。
- Produces: 详情页主内容之后常驻挑战裁决区块，`report === null` 分支也渲染（spec §4.1）。

- [ ] **Step 1: 写失败的页面测试**

`web/tests/strategy-detail-page.spec.ts` 的 `detailClient` 中，在 `probeExperimentReport: async () => true,` 之前加：

```ts
    // 挑战区块自带取数；此处给空态，页面测试彼此独立（区块自身有专门单测）。
    listExperimentChallenges: async () => ({ experiment_id: EX, challenges: [] }),
```

在 `describe` 块末尾追加：

```ts
  it("挑战裁决区块常驻：即使未发布 walk-forward 产物也渲染（spec §4.1）", async () => {
    const client = detailClient({
      experimentResults: async () => ({
        experiment_id: EX,
        manifest: {},
        metrics: null,
        stability_report: null,
      }),
    });
    const wrapper = await mountAt(StrategyDetailPage, client, `/strategies/${EX}`);
    await flushPromises();
    // 主内容仍是"未发布产物"显式缺失态……
    expect(wrapper.find('[data-testid="detail-empty"]').exists()).toBe(true);
    // ……但挑战区块照常挂载并给出空态。
    expect(wrapper.find('[data-testid="challenge-block"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="challenge-empty"]').exists()).toBe(true);
    // 区块只读：诚实文案在，且没有任何触发/重跑入口（复制按钮由空态下的
    // 无卡片渲染自然缺席，专门的按钮断言在 challenge-block.spec.ts 里）。
    expect(wrapper.get('[data-testid="challenge-honesty"]').text()).toContain(
      "web 不发起挑战、不能重跑",
    );
    expect(wrapper.find('[data-testid="challenge-block"] button').exists()).toBe(false);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npm run test -- tests/strategy-detail-page.spec.ts`
Expected: 新用例 FAIL — `challenge-block` 不存在。

- [ ] **Step 3: 挂载组件**

`web/src/pages/StrategyDetailPage.vue`：在 import 块里按字母序加一行（`BarChart` 之前）：

```ts
import ChallengeBlock from "../components/ChallengeBlock.vue";
```

把模板第 400-407 行的收尾（溯源行的 `</p>` 到 `</section>`）从

```html
        · 折结果
        <code :title="manifestString('fold_outcomes_sha256') ?? undefined">{{ short(manifestString('fold_outcomes_sha256'), 12) }}</code>
        · 产物 {{ artifactCount }} 项
      </p>
    </template>
  </section>
```

改为

```html
        · 折结果
        <code :title="manifestString('fold_outcomes_sha256') ?? undefined">{{ short(manifestString('fold_outcomes_sha256'), 12) }}</code>
        · 产物 {{ artifactCount }} 项
      </p>

      <ChallengeBlock :experiment-id="experimentId" />
    </template>
  </section>
```

把上面第 284-291 行的"未发布 walk-forward 产物"分支从

```html
    <Card v-else-if="report === null">
      <div data-testid="detail-empty">
        <EmptyState
          title="未发布 walk-forward 产物"
          description="该实验没有已发布的 stability_report——请看静态报告或 CLI 产物。"
        />
      </div>
    </Card>
```

改为

```html
    <template v-else-if="report === null">
      <Card>
        <div data-testid="detail-empty">
          <EmptyState
            title="未发布 walk-forward 产物"
            description="该实验没有已发布的 stability_report——请看静态报告或 CLI 产物。"
          />
        </div>
      </Card>
      <!-- spec §4.1：区块不依赖主数据可读性，空态分支也必须挂。 -->
      <ChallengeBlock :experiment-id="experimentId" />
    </template>
```

（两条分支各挂一次是 spec §4.1 的显式要求：挑战匹配只看身份哈希，与 walk-forward 产物是否可读无关。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npm run test -- tests/strategy-detail-page.spec.ts && npm run typecheck`
Expected: 全绿；`vue-tsc` 无错误。

- [ ] **Step 5: 补 e2e 路由 mock**

`web/e2e/portal.spec.ts` 的策略详情 e2e 用例里，在 `await page.route("**/api/v1/experiments/exp-2026q3/results", ...)` 之后加：

```ts
  await page.route("**/api/v1/experiments/exp-2026q3/challenges", (route) =>
    json(route, 200, { experiment_id: "exp-2026q3", challenges: [] }),
  );
```

并在该用例末尾（`benchmarkSearches` 断言之后）追加：

```ts
  // 挑战裁决区块：详情页常驻只读区块（spec 2026-10-04 §4.1）。
  await expect(page.getByTestId("challenge-block")).toBeVisible();
  await expect(page.getByTestId("challenge-empty")).toContainText("本实验尚无挑战裁决");
```

- [ ] **Step 6: 跑整组 web 测试**

Run: `cd web && npm run test`
Expected: 全绿（含既有 pin I5/I6 用例）。

- [ ] **Step 7: 跑服务侧整组**

Run: `pytest tests/service -q`
Expected: 全绿。

- [ ] **Step 8: 提交**

```bash
git add web/src/pages/StrategyDetailPage.vue \
        web/tests/strategy-detail-page.spec.ts \
        web/e2e/portal.spec.ts
git commit -m "$(cat <<'EOF'
feat(web): mount the challenge block on the strategy detail page

The block renders after the main content and also on the no-walk-forward
branch, since challenge membership only needs the identity hashes.

Co-Authored-By: Claude Code <noreply@anthropic.com>
EOF
)"
```

---

## 收尾核对（全部任务完成后）

- [ ] `pytest tests/service -q` 全绿。
- [ ] `cd web && npm run test && npm run typecheck` 全绿。
- [ ] 零研究产物契约改动（spec §6 纪律）：把 `<BASE>` 取成本计划 Task 1 首个提交的父提交，
      `git diff <BASE>..HEAD --stat -- src/stock_quant/research/` 输出为空。
- [ ] `types.ts` 只增不改：`git diff <BASE>..HEAD -- web/src/api/types.ts` 全为 `+` 行，无 `-` 行。
- [ ] `cd web && npx playwright test`（或 `npm run e2e`）通过。
