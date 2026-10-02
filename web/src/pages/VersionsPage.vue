<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import { setCurrentVersion } from "../stores/version";
import { blockingIssueCount, totalIssueCount } from "../api/quality";
import type { DatasetSummary } from "../api/types";

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
</script>

<template>
  <section>
    <h1>版本面板</h1>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <p v-else-if="!loaded" data-testid="loading">加载中</p>
    <table v-else data-testid="dataset-list">
      <thead>
        <tr>
          <th>dataset version</th>
          <th>CURRENT</th>
          <th>创建时间</th>
          <th>表计数</th>
          <th>质量摘要</th>
          <th>有效 accepted record</th>
          <th>最近 verdict</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in datasets" :key="item.dataset_version" data-testid="dataset-row">
          <td>
            <RouterLink :to="`/versions/${item.dataset_version}`">
              <code>{{ item.dataset_version }}</code>
            </RouterLink>
          </td>
          <td>
            <span
              v-if="item.is_current"
              class="badge"
              title="CURRENT 是指针标记，不是可信等级"
              data-testid="current-mark"
            >
              CURRENT
            </span>
            <span v-else>—</span>
          </td>
          <td>{{ item.created_at ?? "—" }}</td>
          <td>{{ item.table_count }}</td>
          <td data-testid="quality-summary">
            <span v-if="item.acceptance.state === 'ACCEPTED'">门禁通过</span>
            <span v-else class="warn">门禁阻断（{{ blockingIssueCount(item.quality) }} 项）</span>
            <span>；质量问题 {{ totalIssueCount(item.quality) }} 条</span>
          </td>
          <td data-testid="accepted-record">
            {{ item.acceptance.has_valid_accepted_record ? "是" : "否" }}
          </td>
          <td data-testid="latest-verdict">
            {{ item.acceptance.latest_verdict ?? item.acceptance.state }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
