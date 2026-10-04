<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import { num, pct } from "../format";
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

/** 渲染用展示行：格式化文本 + 原始行引用（插槽读结论/版本等原始字段）。 */
const displayRows = computed(() =>
  rows.value.map((row) => ({
    experiment_id: row.experiment_id, // row-key
    hypothesis: row.hypothesis ?? "（未发布 experiment_spec.yml）",
    aggregate: pct(aggregateOf(row)?.aggregate_return),
    sharpe: num(aggregateOf(row)?.sharpe_zero_rf),
    worst_drawdown: pct(row.display_extremes?.max_per_fold_drawdown),
    turnover: pct(row.display_extremes?.mean_turnover),
    dataset_version: row.dataset_version ?? "—",
    status: row.status ?? "—",
    raw: row, // 原始行：插槽读结论/版本用
  })),
);

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
        <!-- DataTable 的行类型是 Record<string, unknown>；断言只做类型收窄，不改变运行时渲染。 -->
        <StateBadge
          v-if="(row.raw as ExperimentSummaryRow).stability_conclusion !== null"
          kind="conclusion"
          :value="(row.raw as ExperimentSummaryRow).stability_conclusion as string"
        />
        <span v-else>无 walk-forward 结论</span>
      </template>
      <template #dataset_version="{ row }">
        <RouterLink
          v-if="(row.raw as ExperimentSummaryRow).dataset_version !== null"
          :to="`/versions/${(row.raw as ExperimentSummaryRow).dataset_version}`"
        >
          <code>{{ row.dataset_version }}</code>
        </RouterLink>
        <span v-else>—</span>
      </template>
    </DataTable>
  </section>
</template>
