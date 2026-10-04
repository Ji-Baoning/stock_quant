# Web 决策层 · S2② 决策台结论卡实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把决策台块②从"诚实空态/计数一句话"升级为**结论卡**（v3 规格 §5.1 ② 的完整形态）：最新已发布实验的结论徽章、裁决摘要、canonical 情景 OOS 聚合收益与 Sharpe、政策哈希短码、跳转策略详情——数据全部来自 S1 的 `/api/v1/experiments/summaries`。

**Architecture:** 一个小的服务端增量（summaries 行补 `run_started_at`，读已发布 `run_manifest.json`——"最新"需要时间序，而 manifest 本体刻意不含运行时元数据）+ 前端块②改造。无新端点、无新产物、前端零金融计算。

**Tech Stack:** 既有栈零新增。

**Spec:** [2026-10-03-web-portal-decision-layer-design.md](../specs/2026-10-03-web-portal-decision-layer-design.md) §5.1 ②、§8 S2② 行。前置：第一批（决策台①③）与 S1（summaries 端点 + `/strategies` 路由）均已交付。

## Global Constraints

- `run_started_at` 是**展示用时间序**，来自已发布 `run_manifest.json` 的运行时元数据（不进任何身份哈希——它本来就是"runtime metadata never enters an identity hash"的那类字段）；Task 1 Step 1 与真实实例对账字段名。
- 块②渲染零派生：徽章/文本/数字逐字来自 summaries 行；无实验时回到既有空态（文案不变）。
- pin I5/I8 不动；错误走 `toDisplayError`；每任务一提交（英文 + Co-Authored-By: Claude Code）。

---

### Task 1: summaries 行补 `run_started_at`

**Files:**
- Modify: `src/stock_quant/service/results.py`（`ExperimentSummaryRow` + 填充逻辑）
- Test: `tests/service/test_results_endpoints.py`（追加）

- [ ] **Step 1: 实例对账**（对真实已发布实验）：

```bash
python -c "import json,pathlib; d=next(pathlib.Path('project/data/experiments').iterdir()); m=json.loads((d/'run_manifest.json').read_text()); print(sorted(m)); print({k:v for k,v in m.items() if 'time' in k or 'at' in k or 'start' in k})"
```

以真实时间字段名（预期形如 `started_at`/`created_at`）为准实现；对账差异记进提交信息。

- [ ] **Step 2: 失败测试**

```python
def test_summaries_carry_run_started_at_from_run_manifest(service_project, client):
    directory = publish_experiment(service_project, "v1", walk_forward=True)
    import json as _json
    (directory / "run_manifest.json").write_text(
        _json.dumps({"run_id": "r" * 32, "started_at": "2026-10-03T09:00:00+08:00"}),
        encoding="utf-8",
    )
    row = next(
        row
        for row in client.get("/api/v1/experiments/summaries").json()["summaries"]
        if row["experiment_id"] == "e" * 64
    )
    assert row["run_started_at"] == "2026-10-03T09:00:00+08:00"


def test_summaries_run_started_at_null_without_run_manifest(service_project, client):
    publish_experiment(service_project, "v1", walk_forward=True)
    row = client.get("/api/v1/experiments/summaries").json()["summaries"][0]
    assert row["run_started_at"] is None
```

- [ ] **Step 3: 实现**：`ExperimentSummaryRow` 加 `run_started_at: str | None = None`；填充处读 `run_manifest.json`（`_read_optional_json` 复用），取对账出的时间字段（字符串原样透传，不解析不格式化）。

- [ ] **Step 4: 通过 + 提交**

```bash
pytest tests/service/test_results_endpoints.py -q
git add src/stock_quant/service/results.py tests/service/test_results_endpoints.py
git commit -m "feat(service): carry run_started_at on experiment summaries

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: 决策台块②结论卡

**Files:**
- Modify: `web/src/api/types.ts`（`ExperimentSummaryRow` + `run_started_at: string | null`——只增一字段）
- Modify: `web/src/stores/console.ts`（`experiments` 载荷换 `experimentSummaries()`；类型换 `ExperimentSummaryRow[]`；503 容忍逻辑不动）
- Modify: `web/src/pages/DecisionConsolePage.vue`（块②改造）
- Test: `web/tests/decision-console-page.spec.ts`（既有空态/计数用例更新 + 新用例）

- [ ] **Step 1: 失败测试**（更新既有"计数一句话"用例为结论卡语义，新增）：

```ts
it("块②：结论卡——最新实验的徽章/摘要/指标与跳转", async () => {
  const client: ApiClient = fakeClient({
    listDatasets: async () => datasetListResponse(),
    listUpdateJobs: async () => ({ jobs: [] }),
    experimentSummaries: async () => ({
      summaries: [
        { ...WF_SUMMARY, experiment_id: "a".repeat(64), run_started_at: "2026-10-01T09:00:00+08:00" },
        { ...WF_SUMMARY, experiment_id: "b".repeat(64), run_started_at: "2026-10-03T09:00:00+08:00",
          stability_conclusion: "UNSTABLE" },
      ],
    }),
  });
  const wrapper = await mountAt(DecisionConsolePage, client, "/");
  await flushPromises();
  const block = wrapper.get('[data-testid="block-strategy"]');
  // 最新 = run_started_at 最大者（b…，UNSTABLE）。
  expect(block.get('[data-testid="state-badge"]').text()).toBe("UNSTABLE");
  expect(block.get('[data-testid="strategy-latest-link"]').attributes("href")).toBe(
    `#/strategies/${"b".repeat(64)}`,
  );
  expect(block.text()).toContain("Sharpe");
});

it("块②：无时间序信息时显式标注（注册表序），不假装最新", async () => {
  const client: ApiClient = fakeClient({
    listDatasets: async () => datasetListResponse(),
    listUpdateJobs: async () => ({ jobs: [] }),
    experimentSummaries: async () => ({
      summaries: [{ ...WF_SUMMARY, run_started_at: null }],
    }),
  });
  const wrapper = await mountAt(DecisionConsolePage, client, "/");
  await flushPromises();
  expect(wrapper.get('[data-testid="block-strategy"]').text()).toContain("（注册表序，无运行时间）");
});

it("块②：非 walk-forward 实验显式'无 walk-forward 结论'，不渲染指标卡", async () => {
  const client: ApiClient = fakeClient({
    listDatasets: async () => datasetListResponse(),
    listUpdateJobs: async () => ({ jobs: [] }),
    experimentSummaries: async () => ({
      summaries: [{ ...WF_SUMMARY, stability_conclusion: null, aggregates: null, display_extremes: null }],
    }),
  });
  const wrapper = await mountAt(DecisionConsolePage, client, "/");
  await flushPromises();
  const block = wrapper.get('[data-testid="block-strategy"]');
  expect(block.get('[data-testid="strategy-no-conclusion"]').text()).toBe("无 walk-forward 结论");
  expect(block.find('[data-testid="state-badge"]').exists()).toBe(false);
  expect(block.find(".kpi-grid").exists()).toBe(false);
});
```

（`WF_SUMMARY` 常量从 `strategies-page.spec.ts` 提到 `helpers.ts` 共享——含 pin I5 五字段 + 结论层字段的最小行。空注册表用例不变：`block-strategy-empty` 文案保持"尚无已发布实验"。）

- [ ] **Step 2: 实现**

块②模板（console store 的 `experiments` 已是 `ExperimentSummaryRow[]`）：

```vue
      <Card title="② 策略结论是什么？" testid="block-strategy">
        <div v-if="latestExperiment !== null" class="strategy-latest">
          <p>
            <StateBadge
              v-if="latestExperiment.stability_conclusion !== null"
              kind="conclusion"
              :value="latestExperiment.stability_conclusion"
            />
            <!-- 与策略列表页同一措辞：不得用 '—' 冒充结论（诚实空态纪律）。 -->
            <span v-else class="hint-inline" data-testid="strategy-no-conclusion">无 walk-forward 结论</span>
            <RouterLink
              :to="`/strategies/${latestExperiment.experiment_id}`"
              data-testid="strategy-latest-link"
            >
              <code>{{ latestExperiment.experiment_id.slice(0, 8) }}</code>
            </RouterLink>
            <span v-if="!hasTimeOrder" class="hint-inline">（注册表序，无运行时间）</span>
          </p>
          <p v-if="latestExperiment.hypothesis !== null">{{ latestExperiment.hypothesis }}</p>
          <div class="kpi-grid" v-if="latestAggregate !== null">
            <KpiCard label="OOS 聚合收益" :value="pct(latestAggregate.aggregate_return)" />
            <KpiCard label="Sharpe（零无风险）" :value="num(latestAggregate.sharpe_zero_rf)"
              :compare="`OOS 观测 ${latestAggregate.oos_return_observations ?? '—'}`" />
            <KpiCard label="逐折最差回撤"
              :value="pct(latestExperiment.display_extremes?.max_per_fold_drawdown)" />
          </div>
          <p class="provenance-inline">
            政策 <code>{{ (latestExperiment.stability_policy_hash ?? "").slice(0, 8) }}</code>
            ；共 {{ consoleState.experiments.length }} 个已发布实验（<RouterLink to="/strategies">策略列表</RouterLink>）
          </p>
        </div>
        <div v-else data-testid="block-strategy-empty">
          <EmptyState title="尚无已发布实验"
            description="data/experiments 为空；经 CLI 发起 research run 并发布实验后，结论将在此展示。" />
        </div>
      </Card>
```

脚本侧 computed：

```ts
const latestExperiment = computed(() => {
  const rows = consoleState.experiments;
  if (rows.length === 0) return null;
  const withTime = rows.filter((row) => row.run_started_at !== null);
  if (withTime.length === rows.length) {
    return [...withTime].sort((a, b) =>
      String(a.run_started_at).localeCompare(String(b.run_started_at)),
    ).at(-1) ?? null;
  }
  return rows[0]; // 有行缺时间：不假装最新，页面标注"注册表序"
});
const hasTimeOrder = computed(
  () => consoleState.experiments.length > 0
    && consoleState.experiments.every((row) => row.run_started_at !== null),
);
const latestAggregate = computed(() => {
  const row = latestExperiment.value;
  if (row === null || row.aggregates === null) return null;
  const canonical = row.canonical_scenario;
  return row.aggregates.find((entry) => entry.scenario === canonical) ?? row.aggregates[0];
});
```

（`pct`/`num` 格式化函数从 StrategiesPage 提到共享模块 `web/src/format.ts`——两页共用，避免漂移。）

- [ ] **Step 3: 通过 + 提交**

Run: `npm test -- decision-console-page.spec strategies-page.spec && npm run typecheck`

```bash
git add web/src/api/types.ts web/src/stores/console.ts web/src/pages/DecisionConsolePage.vue web/src/format.ts web/tests/decision-console-page.spec.ts web/tests/helpers.ts
git commit -m "feat(web): decision-console strategy conclusion card

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: 收口

- [ ] `npm test && npm run typecheck && npx playwright test` 全绿（e2e 决策台用例的块②断言同步——`/` 页 mock 需补 `/experiments/summaries` route）。
- [ ] 勾稽：summaries 响应模型只增一字段（`git diff` 证明）；块②无实验空态文案与第一批逐字一致。
- [ ] 收口提交。

## Self-Review 记录

规格 §5.1②（最新实验卡：徽章/摘要/超额/挑战状态）→ Task 2；"挑战状态"列仍按 v3 §5.2/裁定 5 推迟（挑战区块需先立设计——结论卡留位不造假）。占位符扫描：`pct`/`num`/`WF_SUMMARY` 的共享提取在同一任务内完成；`run_started_at` 字段名经 Task 1 实例对账。类型一致性：`ExperimentSummaryRow.run_started_at`（Task 1 pydantic ↔ Task 2 TS）逐字对应。
