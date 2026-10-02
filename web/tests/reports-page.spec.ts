import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { ApiError } from "../src/api/client";
import ReportsPage from "../src/pages/ReportsPage.vue";
import { experimentsResponse, fakeClient, HASH_A, mountPage } from "./helpers";

describe("报告页（§10.1 页面 4：链接既有静态报告，不在线重算）", () => {
  it("列出实验与其 dataset version；报告存在给链接、缺失显示'报告不存在'", async () => {
    const client = fakeClient({
      listExperiments: async () => ({
        // pin I5：五个字段一个不少。
        experiments: [
          { experiment_id: "exp-2026q3", status: "SUCCEEDED", dataset_version: HASH_A, universe_version: "tw", evaluation_reason: null },
          { experiment_id: "exp-2026q2", status: null, dataset_version: HASH_A, universe_version: null, evaluation_reason: null },
        ],
      }),
      probeExperimentReport: async (experimentId: string) => experimentId === "exp-2026q3",
    });
    const wrapper = mountPage(ReportsPage, client);
    await flushPromises();
    const rows = wrapper.findAll('[data-testid="experiment-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].get('[data-testid="report-link"]').attributes("href")).toBe(
      "/api/v1/experiments/exp-2026q3/report",
    );
    expect(rows[1].get('[data-testid="report-missing"]').text()).toContain("报告不存在");
    expect(rows[0].text()).toContain(HASH_A);
  });

  it("'不在 Web 里的动作'说明可见（指向 RUNBOOK 等价命令，无按钮）", async () => {
    const client = fakeClient({
      listExperiments: async () => experimentsResponse(),
      probeExperimentReport: async () => true,
    });
    const wrapper = mountPage(ReportsPage, client);
    await flushPromises();
    const note = wrapper.get('[data-testid="not-in-web-note"]').text();
    expect(note).toContain("acceptance confirm");
    expect(note).toContain("research run");
    expect(note).toContain("RUNBOOK");
  });

  it("列表失败显示稳定错误码", async () => {
    const client = fakeClient({
      listExperiments: async () => {
        throw new ApiError(503, "service_unavailable", "");
      },
    });
    const wrapper = mountPage(ReportsPage, client);
    await flushPromises();
    expect(wrapper.get('[data-testid="error"]').text()).toContain("service_unavailable");
  });
});
