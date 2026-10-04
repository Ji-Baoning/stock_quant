import { describe, expect, it } from "vitest";
import { ApiError } from "../src/api/client";
import type { ApiClient } from "../src/api/client";
import {
  activeJobs,
  consoleActions,
  consoleState,
  currentDataset,
  loadConsoleData,
  pendingConfirmations,
} from "../src/stores/console";
import {
  datasetListResponse,
  fakeClient,
  updateJobSummary,
  WF_SUMMARY,
} from "./helpers";

function resetState() {
  consoleState.datasets = [];
  consoleState.experiments = [];
  consoleState.jobs = [];
  consoleState.operationsEnabled = true;
  consoleState.loaded = false;
  consoleState.error = null;
}

function listWithPending(): ReturnType<typeof datasetListResponse> {
  const response = datasetListResponse();
  response.datasets[1].acceptance = {
    state: "PENDING_CONFIRMATION",
    has_valid_accepted_record: false,
    latest_verdict: "PENDING_CONFIRMATION",
    record_count: 0,
  };
  return response;
}

describe("决策台数据组装（stores/console.ts）", () => {
  it("加载三源并派生：CURRENT 版本、待确认验收、活跃任务、行动列表", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => listWithPending(),
      experimentSummaries: async () => ({ summaries: [WF_SUMMARY] }),
      listUpdateJobs: async () => ({ jobs: [updateJobSummary({ job_id: "job-0001", status: "RUNNING" })] }),
    });
    await loadConsoleData(client);
    expect(consoleState.loaded).toBe(true);
    expect(currentDataset.value?.dataset_version).toBe("a".repeat(64));
    expect(pendingConfirmations.value.map((d) => d.dataset_version)).toEqual(["b".repeat(64)]);
    expect(activeJobs.value.map((j) => j.job_id)).toEqual(["job-0001"]);
    expect(consoleActions.value).toEqual([
      {
        key: `pending-${"b".repeat(64)}`,
        kind: "pending_acceptance",
        label: `验收待确认：${"b".repeat(64).slice(0, 8)}`,
        to: `/versions/${"b".repeat(64)}`,
      },
      { key: "job-job-0001", kind: "running_job", label: "更新任务运行中：job-0001", to: "/jobs" },
    ]);
  });

  it("pin I9：操作面 503 是正常态——置 operationsEnabled=false，不产生错误", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      experimentSummaries: async () => ({ summaries: [WF_SUMMARY] }),
      listUpdateJobs: async () => {
        throw new ApiError(503, "operations_disabled", "");
      },
    });
    await loadConsoleData(client);
    expect(consoleState.error).toBeNull();
    expect(consoleState.operationsEnabled).toBe(false);
    expect(consoleState.loaded).toBe(true);
    expect(activeJobs.value).toEqual([]);
  });

  it("数据源失败走 toDisplayError（稳定 code），loaded 不置位", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => {
        throw new ApiError(500, "internal_error", "boom");
      },
      experimentSummaries: async () => ({ summaries: [WF_SUMMARY] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    await loadConsoleData(client);
    expect(consoleState.error?.code).toBe("internal_error");
    expect(consoleState.loaded).toBe(false);
  });

  it("无待办：两个派生列表为空，行动列表为空", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      experimentSummaries: async () => ({ summaries: [WF_SUMMARY] }),
      listUpdateJobs: async () => ({ jobs: [updateJobSummary({ status: "SUCCEEDED" })] }),
    });
    await loadConsoleData(client);
    expect(pendingConfirmations.value).toEqual([]);
    expect(activeJobs.value).toEqual([]);
    expect(consoleActions.value).toEqual([]);
  });
});
