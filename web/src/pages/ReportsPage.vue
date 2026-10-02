<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { ExperimentSummary } from "../api/types";

const client = useApiClient();
const experiments = ref<ExperimentSummary[]>([]);
const reportAvailable = ref<Record<string, boolean>>({});
const loaded = ref(false);
const error = ref<DisplayError | null>(null);

/** §10.3"不在 Web 里的动作"：只显示下一步去哪，不提供按钮。 */
const NOT_IN_WEB_NOTE =
  "acceptance confirm/publish、research run 与失败证据清理不在 Web 中执行；"
  + "本页只链接既有静态报告，不在线重算。等价命令见 RUNBOOK。";

onMounted(async () => {
  try {
    experiments.value = (await client.listExperiments()).experiments;
    for (const experiment of experiments.value) {
      reportAvailable.value[experiment.experiment_id] =
        await client.probeExperimentReport(experiment.experiment_id);
    }
    loaded.value = true;
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});
</script>

<template>
  <section>
    <h1>报告</h1>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <p v-else-if="!loaded" data-testid="loading">加载中</p>
    <template v-else>
      <table data-testid="experiment-list">
        <thead>
          <tr><th>实验</th><th>dataset version</th><th>报告</th></tr>
        </thead>
        <tbody>
          <tr v-for="experiment in experiments" :key="experiment.experiment_id" data-testid="experiment-row">
            <td>{{ experiment.experiment_id }}</td>
            <td><code>{{ experiment.dataset_version }}</code></td>
            <td>
              <a
                v-if="reportAvailable[experiment.experiment_id]"
                :href="`/api/v1/experiments/${experiment.experiment_id}/report`"
                target="_blank"
                rel="noopener"
                data-testid="report-link"
              >
                打开静态报告
              </a>
              <span v-else data-testid="report-missing">
                报告不存在：尚无已生成的静态报告（报告由 CLI 生成，不在 Web 重算）
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <p data-testid="not-in-web-note">{{ NOT_IN_WEB_NOTE }}</p>
    </template>
  </section>
</template>
