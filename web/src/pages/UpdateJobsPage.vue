<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { ApiError, conflictJobId, useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { UpdateJob, UpdateJobRequest, UpdateJobSummary } from "../api/types";

const client = useApiClient();

type OperationsMode = "probing" | "enabled" | "disabled";
const mode = ref<OperationsMode>("probing");
const probeError = ref<DisplayError | null>(null);

// pin I10：列表项是 UpdateJobSummary（六个字段），不是完整 UpdateJob。
const jobs = ref<UpdateJobSummary[]>([]);
const selectedJob = ref<UpdateJob | null>(null);
const conflictJobIdValue = ref<string | null>(null);
const submitError = ref<DisplayError | null>(null);
const submitting = ref(false);

const form = ref({ start: "", end: "", sources: "", lookback: "" });

const POLL_INTERVAL_MS = 1500;
const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELLED_BY_SHUTDOWN"]);
let pollTimer: ReturnType<typeof setInterval> | null = null;

/** §10.3：操作面禁用时给出等价 CLI 命令（operations update 是 Web 的等价入口，§9.1）。 */
const EQUIVALENT_CLI =
  "python -m stock_quant operations update --root <project-root> " +
  "[--start YYYY-MM-DD] [--end YYYY-MM-DD] [--sources s1,s2] [--disclosure-lookback-days N]";

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function pollJob(jobId: string) {
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const job = await client.getUpdateJob(jobId);
      selectedJob.value = job;
      if (TERMINAL_STATUSES.has(job.status)) {
        stopPolling();
      }
    } catch (cause) {
      stopPolling();
      submitError.value = toDisplayError(cause);
    }
  }, POLL_INTERVAL_MS);
}

async function selectJob(jobId: string) {
  try {
    const job = await client.getUpdateJob(jobId);
    selectedJob.value = job;
    if (!TERMINAL_STATUSES.has(job.status)) {
      await pollJob(jobId);
    }
  } catch (cause) {
    submitError.value = toDisplayError(cause);
  }
}

function buildRequest(): UpdateJobRequest {
  const lookbackText = form.value.lookback.trim();
  const lookback = lookbackText === "" ? null : Number(lookbackText);
  if (lookback !== null && (!Number.isInteger(lookback) || lookback < 0)) {
    throw new Error("invalid_lookback_days");
  }
  const sources =
    form.value.sources.trim() === ""
      ? null
      : form.value.sources
          .split(",")
          .map((item) => item.trim())
          .filter((item) => item !== "");
  return {
    start: form.value.start === "" ? null : form.value.start,
    end: form.value.end === "" ? null : form.value.end,
    sources,
    disclosure_lookback_days: lookback,
  };
}

async function submit() {
  submitError.value = null;
  conflictJobIdValue.value = null;
  let request: UpdateJobRequest;
  try {
    request = buildRequest();
  } catch {
    submitError.value = {
      code: "invalid_lookback_days",
      message: "lookback 必须是非负整数（留空 = 常规增量）",
    };
    return;
  }
  submitting.value = true;
  try {
    const created = await client.startUpdateJob(request);
    // pin I11：201 只回 `{job_id, status}`——完整 job 必须另取一次，
    // 不能把响应体当成 UpdateJob 塞进列表。
    await selectJob(created.job_id);
    await refreshJobs();
  } catch (cause) {
    const runningJobId = conflictJobId(cause);
    if (runningJobId !== null) {
      conflictJobIdValue.value = runningJobId;
      await selectJob(runningJobId);
    } else {
      submitError.value = toDisplayError(cause);
    }
  } finally {
    submitting.value = false;
  }
}

async function refreshJobs() {
  // pin I10：列表响应的外层是对象 `{jobs: [...]}`，不是裸数组。
  jobs.value = (await client.listUpdateJobs()).jobs;
}

onMounted(async () => {
  try {
    await refreshJobs();
    mode.value = "enabled";
  } catch (cause) {
    // pin I9：禁用是 503 operations_disabled（路由始终注册着，不是 404）。
    if (cause instanceof ApiError && cause.status === 503) {
      mode.value = "disabled";
    } else {
      mode.value = "enabled";
      probeError.value = toDisplayError(cause);
    }
  }
});
onUnmounted(stopPolling);
</script>

<template>
  <section>
    <h1>更新任务</h1>
    <p v-if="mode === 'probing'" data-testid="probing">探测操作面状态中</p>

    <div v-else-if="mode === 'disabled'" data-testid="operations-disabled">
      <h2>操作面未启用</h2>
      <p>
        操作 API 默认禁用（独立 router/process；启用后仍只绑定环回地址）。
        本页面只读，不提供提交入口。
      </p>
      <p>等价 CLI 命令（默认空参数 = 常规增量）：</p>
      <pre><code data-testid="equivalent-cli">{{ EQUIVALENT_CLI }}</code></pre>
    </div>

    <template v-else>
      <p v-if="probeError !== null" class="error" data-testid="probe-error">
        错误 {{ probeError.code }}：{{ probeError.message === "" ? "无安全摘要" : probeError.message }}
      </p>

      <form data-testid="update-form" @submit.prevent="submit">
        <label>start <input type="date" v-model="form.start" name="start" /></label>
        <label>end <input type="date" v-model="form.end" name="end" /></label>
        <label>
          sources
          <input type="text" v-model="form.sources" name="sources" placeholder="逗号分隔；留空 = 全部" />
        </label>
        <label>
          lookback
          <input type="number" v-model="form.lookback" name="lookback" min="0" step="1" placeholder="留空 = 常规增量" />
        </label>
        <button type="submit" data-testid="submit-update" :disabled="submitting">
          提交更新（默认空 = 常规增量）
        </button>
      </form>

      <p v-if="submitError !== null" class="error" data-testid="submit-error">
        错误 {{ submitError.code }}：{{ submitError.message === "" ? "无安全摘要" : submitError.message }}
      </p>

      <p v-if="conflictJobIdValue !== null" class="warn" data-testid="conflict">
        已有任务运行中（update_already_running）：
        <button type="button" data-testid="conflict-job-link" @click="selectJob(conflictJobIdValue)">
          {{ conflictJobIdValue }}
        </button>
      </p>

      <div v-if="selectedJob !== null" class="job-detail" data-testid="job-detail">
        <h2>任务 {{ selectedJob.job_id }}</h2>
        <p data-testid="job-status">状态：{{ selectedJob.status }}</p>

        <div v-if="selectedJob.status === 'SUCCEEDED'" data-testid="job-succeeded">
          <p>run id：<code data-testid="job-run-id">{{ selectedJob.run_id ?? "—" }}</code></p>
          <p>
            dataset version：<code data-testid="job-dataset-version">{{ selectedJob.dataset_version ?? "—" }}</code>
            <RouterLink
              v-if="selectedJob.dataset_version !== null"
              :to="`/versions/${selectedJob.dataset_version}`"
              data-testid="job-version-link"
            >
              版本详情
            </RouterLink>
          </p>
        </div>

        <div v-else-if="selectedJob.status === 'FAILED'" data-testid="job-failed">
          <p>稳定失败原因：<code data-testid="job-failure-reason">{{ selectedJob.failure_reason ?? "—" }}</code></p>
          <p>安全摘要：{{ selectedJob.failure_detail ?? "无安全摘要" }}</p>
          <p>不自动重试：如需再次更新，请提交新任务（将产生新 job id）。</p>
        </div>

        <div v-else-if="selectedJob.status === 'CANCELLED_BY_SHUTDOWN'" data-testid="job-cancelled">
          <p>任务被停机取消；日志保留可查。</p>
        </div>

        <h3>日志尾部（服务端已脱敏的字符串）</h3>
        <pre data-testid="job-stdout"><code>{{ selectedJob.stdout_tail }}</code></pre>
        <pre v-if="selectedJob.stderr_tail !== ''" data-testid="job-stderr"><code>{{ selectedJob.stderr_tail }}</code></pre>
      </div>

      <h2>持久化任务</h2>
      <table data-testid="job-list">
        <thead>
          <tr><th>job id</th><th>状态</th><th>创建</th><th>更新</th></tr>
        </thead>
        <tbody>
          <tr v-for="job in jobs" :key="job.job_id">
            <td><button type="button" @click="selectJob(job.job_id)">{{ job.job_id }}</button></td>
            <td>{{ job.status }}</td>
            <td>{{ job.created_at }}</td>
            <td>{{ job.updated_at }}</td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>
