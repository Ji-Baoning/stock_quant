import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import { mount } from "@vue/test-utils";
import { ApiError, apiClientKey } from "../src/api/client";
import { createPortalRouter } from "../src/router";
import UpdateJobsPage from "../src/pages/UpdateJobsPage.vue";
import { fakeClient, HASH_B, updateJob, updateJobSummary } from "./helpers";
import type { ApiClient } from "../src/api/client";
import type { UpdateJobRequest } from "../src/api/types";

function mountJobsPage(client: ApiClient) {
  return mount(UpdateJobsPage, {
    global: {
      plugins: [createPortalRouter()],
      provide: { [apiClientKey as symbol]: client },
    },
  });
}

async function settle(milliseconds: number) {
  await vi.advanceTimersByTimeAsync(milliseconds);
  await nextTick();
}

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("更新任务页（§10.3 数据更新流程 / §10.4 四终态）", () => {
  it("操作面关闭（503）：页面只读、说明原因并给出等价 CLI 命令", async () => {
    const client = fakeClient({
      listUpdateJobs: async () => {
        // pin I9：禁用是 503 operations_disabled，不是 404——路由始终注册着。
        throw new ApiError(503, "operations_disabled", "");
      },
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    expect(wrapper.get('[data-testid="operations-disabled"]').text()).toContain("操作面未启用");
    expect(wrapper.get('[data-testid="equivalent-cli"]').text()).toContain(
      "python -m stock_quant operations update --root",
    );
    expect(wrapper.find('[data-testid="update-form"]').exists()).toBe(false);
  });

  it("提交允许参数（默认空 = 常规增量）并轮询到 SUCCEEDED：显示 run id 与完整 dataset version 并链接版本详情", async () => {
    const queue = [
      updateJob({ status: "RUNNING" }),
      updateJob({ status: "SUCCEEDED", run_id: "run-77", dataset_version: HASH_B }),
    ];
    const startUpdateJob = vi.fn(async (request: UpdateJobRequest) => {
      expect(request).toEqual({
        start: null,
        end: null,
        sources: null,
        disclosure_lookback_days: null,
      });
      // pin I11：201 只回 {job_id, status}。
      return { job_id: "job-0001", status: "QUEUED" as const };
    });
    const getUpdateJob = vi.fn(async () => queue.shift() ?? queueFallback());
    function queueFallback() {
      return updateJob({ status: "SUCCEEDED", run_id: "run-77", dataset_version: HASH_B });
    }
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [] }),
      startUpdateJob,
      getUpdateJob,
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(1500);
    await settle(1500);
    expect(wrapper.find('[data-testid="job-succeeded"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="job-run-id"]').text()).toBe("run-77");
    expect(wrapper.get('[data-testid="job-dataset-version"]').text()).toBe(HASH_B);
    expect(wrapper.get('[data-testid="job-version-link"]').attributes("href")).toBe(
      `#/versions/${HASH_B}`,
    );
    expect(startUpdateJob).toHaveBeenCalledTimes(1);
  });

  it("FAILED 终态：显示稳定失败原因、安全摘要与脱敏日志；不自动重试", async () => {
    const queue = [
      updateJob({ status: "RUNNING" }),
      updateJob({
        status: "FAILED",
        // pin I10：失败原因叫 failure_reason（不是 error_code）；
        // 日志是已脱敏的字符串（不是 logs_tail: string[]）。
        failure_reason: "missing_registered_table",
        failure_detail: "缺表 daily_bar",
        stdout_tail: "2026-10-01T08:00:01+08:00 update started\n2026-10-01T08:04:59+08:00 gate blocked",
        stderr_tail: "",
      }),
    ];
    const startUpdateJob = vi.fn(async () => ({ job_id: "job-0001", status: "QUEUED" as const }));
    const getUpdateJob = vi.fn(async () =>
      queue.shift() ?? updateJob({ status: "FAILED", failure_reason: "missing_registered_table" }),
    );
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [] }),
      startUpdateJob,
      getUpdateJob,
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(1500);
    await settle(1500);
    expect(wrapper.find('[data-testid="job-failed"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="job-failure-reason"]').text()).toBe("missing_registered_table");
    expect(wrapper.get('[data-testid="job-failed"]').text()).toContain("缺表 daily_bar");
    expect(wrapper.get('[data-testid="job-stdout"]').text()).toContain("gate blocked");
    expect(wrapper.get('[data-testid="job-failed"]').text()).toContain("不自动重试");
    await settle(1500);
    await settle(1500);
    expect(startUpdateJob).toHaveBeenCalledTimes(1); // 不自动重试：无新 POST
  });

  it("409 终态：显示'已有任务运行中'并链接到该 job", async () => {
    const startUpdateJob = vi.fn(async () => {
      // pin I11：job_id 嵌在 error 里，不是顶层字段。
      throw new ApiError(409, "update_already_running", "", {
        error: { code: "update_already_running", job_id: "job-0001" },
      });
    });
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [updateJobSummary({ job_id: "job-0001", status: "RUNNING" })] }),
      startUpdateJob,
      getUpdateJob: async () => updateJob({ job_id: "job-0001", status: "RUNNING" }),
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(0);
    expect(wrapper.get('[data-testid="conflict"]').text()).toContain("已有任务运行中");
    expect(wrapper.get('[data-testid="conflict-job-link"]').text()).toBe("job-0001");
    expect(wrapper.get('[data-testid="job-detail"]').text()).toContain("job-0001");
  });

  it("CANCELLED_BY_SHUTDOWN 终态可渲染", async () => {
    const queue = [
      updateJob({ status: "RUNNING" }),
      updateJob({ status: "CANCELLED_BY_SHUTDOWN" }),
    ];
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [] }),
      startUpdateJob: async () => ({ job_id: "job-0001", status: "QUEUED" as const }),
      getUpdateJob: async () => queue.shift() ?? updateJob({ status: "CANCELLED_BY_SHUTDOWN" }),
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(1500);
    await settle(1500);
    expect(wrapper.find('[data-testid="job-cancelled"]').exists()).toBe(true);
  });

  it("lookback 非法输入被前端拒绝并显示稳定码，不发起请求", async () => {
    const startUpdateJob = vi.fn(async () => ({ job_id: "job-0001", status: "QUEUED" as const }));
    const client = fakeClient({
      listUpdateJobs: async () => ({ jobs: [] }),
      startUpdateJob,
    });
    const wrapper = mountJobsPage(client);
    await settle(0);
    await wrapper.get('input[name="lookback"]').setValue("-3");
    await wrapper.get('[data-testid="update-form"]').trigger("submit");
    await settle(0);
    expect(wrapper.get('[data-testid="submit-error"]').text()).toContain("invalid_lookback_days");
    expect(startUpdateJob).not.toHaveBeenCalled();
  });
});
