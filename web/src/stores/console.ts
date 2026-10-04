import { computed, reactive, type ComputedRef } from "vue";
import { ApiError, type ApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { DatasetSummary, ExperimentSummaryRow, UpdateJobSummary } from "../api/types";

/** 决策台行动项：每条必有跳转（v3 规格 §5.1 ③）。 */
export interface ConsoleAction {
  key: string;
  kind: "pending_acceptance" | "running_job";
  label: string;
  to: string;
}

export interface ConsoleState {
  datasets: DatasetSummary[];
  experiments: ExperimentSummaryRow[];
  jobs: UpdateJobSummary[];
  /** pin I9：503 = 操作面默认禁用，是正常态不是错误。 */
  operationsEnabled: boolean;
  loaded: boolean;
  error: DisplayError | null;
}

export const consoleState = reactive<ConsoleState>({
  datasets: [],
  experiments: [],
  jobs: [],
  operationsEnabled: true,
  loaded: false,
  error: null,
});

export const currentDataset: ComputedRef<DatasetSummary | null> = computed(
  () => consoleState.datasets.find((dataset) => dataset.is_current) ?? null,
);

export const pendingConfirmations: ComputedRef<DatasetSummary[]> = computed(() =>
  consoleState.datasets.filter(
    (dataset) => dataset.acceptance.state === "PENDING_CONFIRMATION",
  ),
);

export const activeJobs: ComputedRef<UpdateJobSummary[]> = computed(() =>
  consoleState.jobs.filter(
    (job) => job.status === "QUEUED" || job.status === "RUNNING",
  ),
);

export const consoleActions: ComputedRef<ConsoleAction[]> = computed(() => [
  ...pendingConfirmations.value.map((dataset) => ({
    key: `pending-${dataset.dataset_version}`,
    kind: "pending_acceptance" as const,
    label: `验收待确认：${dataset.dataset_version.slice(0, 8)}`,
    to: `/versions/${dataset.dataset_version}`,
  })),
  ...activeJobs.value.map((job) => ({
    key: `job-${job.job_id}`,
    kind: "running_job" as const,
    label: `更新任务运行中：${job.job_id}`,
    to: "/jobs",
  })),
]);

export async function loadConsoleData(client: ApiClient): Promise<void> {
  consoleState.error = null;
  try {
    const [datasets, summaries] = await Promise.all([
      client.listDatasets(),
      client.experimentSummaries(),
    ]);
    consoleState.datasets = datasets.datasets;
    consoleState.experiments = summaries.summaries;
    // 操作面独立容忍：503 = 默认禁用（pin I9），其余失败照常走错误通道。
    consoleState.operationsEnabled = true;
    try {
      consoleState.jobs = (await client.listUpdateJobs()).jobs;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 503) {
        consoleState.operationsEnabled = false;
        consoleState.jobs = [];
      } else {
        throw cause;
      }
    }
    consoleState.loaded = true;
  } catch (cause) {
    consoleState.error = toDisplayError(cause);
  }
}
