<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useApiClient } from "../api/client";
import { blockingIssueCount, totalIssueCount } from "../api/quality";
import { num, pct } from "../format";
import { hasNewerCurrent } from "../stores/version";
import {
  consoleActions,
  consoleState,
  currentDataset,
  loadConsoleData,
  pendingConfirmations,
} from "../stores/console";
import Card from "../components/Card.vue";
import EmptyState from "../components/EmptyState.vue";
import KpiCard from "../components/KpiCard.vue";
import Skeleton from "../components/Skeleton.vue";
import StateBadge from "../components/StateBadge.vue";

const client = useApiClient();
onMounted(() => {
  void loadConsoleData(client);
});

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
</script>

<template>
  <section>
    <h1>决策台</h1>

    <p
      v-if="consoleState.error !== null"
      class="error"
      data-testid="console-error"
    >
      错误 {{ consoleState.error.code }}：{{ consoleState.error.message === "" ? "无安全摘要" : consoleState.error.message }}
    </p>
    <div v-else-if="!consoleState.loaded" data-testid="console-loading">
      <Skeleton :rows="6" />
    </div>

    <template v-else>
      <Card title="① 数据现在可信吗？" testid="block-data-trust">
        <template v-if="currentDataset !== null">
          <p>
            <StateBadge
              kind="acceptance"
              :value="currentDataset.acceptance.state"
              title="验收四态原文；门禁通过与阻断读 acceptance.state（P5 语义）"
            />
            <span v-if="currentDataset.acceptance.state === 'ACCEPTED'">门禁通过</span>
            <span v-else class="warn">
              门禁阻断（{{ blockingIssueCount(currentDataset.quality) }} 项）
            </span>
            <span>；质量问题 {{ totalIssueCount(currentDataset.quality) }} 条</span>
          </p>
          <p>
            创建时间：{{ currentDataset.created_at ?? "—" }}；
            有效 accepted record：{{ currentDataset.acceptance.has_valid_accepted_record ? "是" : "否" }}
          </p>
          <p v-if="hasNewerCurrent" class="hint">
            CURRENT 指针已前移（顶栏提示不自动切换）
          </p>
          <RouterLink :to="`/versions/${currentDataset.dataset_version}`">
            查看版本详情 <code>{{ currentDataset.dataset_version.slice(0, 8) }}</code>
          </RouterLink>
        </template>
        <EmptyState
          v-else
          title="尚无 CURRENT 数据集版本"
          description="数据尚未发布或 CURRENT 指针为空。"
        />
      </Card>

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
          <EmptyState
            title="尚无已发布实验"
            description="data/experiments 为空；经 CLI 发起一次 research run 并发布实验后，结论将在此展示。"
          />
        </div>
      </Card>

      <Card title="③ 需要我做什么？" testid="block-actions">
        <ul v-if="consoleActions.length > 0" class="action-list">
          <li v-for="action in consoleActions" :key="action.key" data-testid="action-item">
            <RouterLink :to="action.to">{{ action.label }}</RouterLink>
          </li>
        </ul>
        <template v-else>
          <p v-if="!consoleState.operationsEnabled" class="hint">
            操作面未启用（默认禁用），任务状态不可查；等价 CLI 见更新任务页。
          </p>
          <EmptyState title="无待办" />
        </template>
        <p v-if="pendingConfirmations.length > 0" class="hint">
          验收确认不在 web 内执行：按 RUNBOOK 的 acceptance confirm 流程处理。
        </p>
      </Card>
    </template>
  </section>
</template>
