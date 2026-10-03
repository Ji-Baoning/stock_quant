<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import { setCurrentVersion } from "../stores/version";
import { blockingIssueCount, totalIssueCount } from "../api/quality";
import type { AcceptanceSummary, DatasetSummary, QualitySummary } from "../api/types";
import Card from "../components/Card.vue";
import DataTable from "../components/DataTable.vue";
import StateBadge from "../components/StateBadge.vue";
import type { DataTableColumn } from "../components/DataTable.vue";

const client = useApiClient();
const datasets = ref<DatasetSummary[]>([]);
const loaded = ref(false);
const error = ref<DisplayError | null>(null);

onMounted(async () => {
  try {
    const response = await client.listDatasets();
    datasets.value = response.datasets;
    setCurrentVersion(response.current);
    loaded.value = true;
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});

/** 列定义照搬旧表头；不传 sortable，本页不新增排序行为。 */
const columns: DataTableColumn[] = [
  { key: "dataset_version", label: "dataset version", mono: true },
  { key: "is_current", label: "CURRENT" },
  { key: "created_at", label: "创建时间" },
  { key: "table_count", label: "表计数", align: "right" },
  { key: "quality", label: "质量摘要" },
  { key: "accepted", label: "有效 accepted record" },
  { key: "latest_verdict", label: "最近 verdict" },
];
</script>

<template>
  <section>
    <h1>版本面板</h1>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <p v-else-if="!loaded" data-testid="loading">加载中</p>
    <Card v-else title="数据集版本">
      <DataTable
        testid="dataset-list"
        row-key="dataset_version"
        row-testid="dataset-row"
        :columns="columns"
        :rows="datasets"
      >
        <template #dataset_version="{ row }">
          <RouterLink :to="`/versions/${row.dataset_version}`">
            <code>{{ row.dataset_version }}</code>
          </RouterLink>
        </template>
        <template #is_current="{ row }">
          <span
            v-if="row.is_current"
            class="badge"
            title="CURRENT 是指针标记，不是可信等级"
            data-testid="current-mark"
          >CURRENT</span>
          <span v-else>—</span>
        </template>
        <template #quality="{ row }">
          <span data-testid="quality-summary">
            <!-- DataTable 的行类型是 Record<string, unknown>；断言只做类型收窄，不改变运行时渲染。 -->
            <span v-if="(row.acceptance as AcceptanceSummary).state === 'ACCEPTED'">门禁通过</span>
            <span v-else class="warn">门禁阻断（{{ blockingIssueCount(row.quality as QualitySummary) }} 项）</span>
            <span>；质量问题 {{ totalIssueCount(row.quality as QualitySummary) }} 条</span>
          </span>
        </template>
        <template #accepted="{ row }">
          <span data-testid="accepted-record">
            {{ (row.acceptance as AcceptanceSummary).has_valid_accepted_record ? "是" : "否" }}
          </span>
        </template>
        <template #latest_verdict="{ row }">
          <span data-testid="latest-verdict">
            <StateBadge
              v-if="(row.acceptance as AcceptanceSummary).latest_verdict !== null"
              kind="acceptance"
              :value="(row.acceptance as AcceptanceSummary).latest_verdict as string"
            />
            <span v-else>{{ (row.acceptance as AcceptanceSummary).state }}</span>
          </span>
        </template>
      </DataTable>
    </Card>
  </section>
</template>
