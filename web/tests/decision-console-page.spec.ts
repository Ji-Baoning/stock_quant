import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import DecisionConsolePage from "../src/pages/DecisionConsolePage.vue";
import { consoleState } from "../src/stores/console";
import { versionPinState } from "../src/stores/version";
import { ApiError, type ApiClient } from "../src/api/client";
import {
  datasetListResponse,
  experimentsResponse,
  fakeClient,
  mountAt,
  updateJobSummary,
} from "./helpers";

function resetState() {
  consoleState.datasets = [];
  consoleState.current = null;
  consoleState.experiments = [];
  consoleState.jobs = [];
  consoleState.operationsEnabled = true;
  consoleState.loaded = false;
  consoleState.error = null;
  versionPinState.resolvedVersion = null;
  versionPinState.currentVersion = null;
}

describe("决策台（v3 规格 §5.1：三问首屏）", () => {
  it("块①：CURRENT 版本卡——门禁措辞沿用 P5 语义 + 验收徽章 + 链接", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    const block = wrapper.get('[data-testid="block-data-trust"]');
    expect(block.text()).toContain("门禁通过");
    expect(block.text()).toContain("质量问题 0 条");
    expect(block.text()).toContain("有效 accepted record：是");
    expect(block.get('[data-testid="state-badge"]').text()).toBe("ACCEPTED");
    expect(block.find('a[href*="/versions/"]').exists()).toBe(true);
  });

  it("块②：注册表为空 → 诚实空态与到达路径（不渲染任何指标）", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    const block = wrapper.get('[data-testid="block-strategy"]');
    expect(block.get('[data-testid="block-strategy-empty"]').text()).toContain("尚无已发布实验");
    expect(block.text()).toContain("research run");
    expect(block.find(".kpi").exists()).toBe(false);
  });

  it("块②：注册表非空 → 只报计数一句话（结论卡属 S2②，不提前渲染）", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => experimentsResponse(),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    expect(wrapper.get('[data-testid="block-strategy-count"]').text()).toContain("已发布实验 2 个");
  });

  it("块③：待办列表（验收待确认 + 运行中任务），每条带跳转；无待办显'无待办'", async () => {
    resetState();
    const pending = datasetListResponse();
    pending.datasets[1].acceptance = {
      state: "PENDING_CONFIRMATION",
      has_valid_accepted_record: false,
      latest_verdict: "PENDING_CONFIRMATION",
      record_count: 0,
    };
    const client: ApiClient = fakeClient({
      listDatasets: async () => pending,
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [updateJobSummary({ job_id: "job-0001", status: "RUNNING" })] }),
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    const actions = wrapper.findAll('[data-testid="action-item"]');
    expect(actions).toHaveLength(2);
    expect(actions[0].text()).toContain("验收待确认");
    expect(actions[0].get("a").attributes("href")).toBe(`#/versions/${"b".repeat(64)}`);
    expect(actions[1].text()).toContain("job-0001");

    resetState();
    const quietClient: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const quiet = await mountAt(DecisionConsolePage, quietClient, "/");
    await flushPromises();
    expect(quiet.get('[data-testid="block-actions"]').text()).toContain("无待办");
  });

  it("pin I9：操作面禁用时块③给出真实说明而非错误", async () => {
    resetState();
    const client: ApiClient = fakeClient({
      listDatasets: async () => datasetListResponse(),
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => {
        throw new ApiError(503, "operations_disabled", "");
      },
    });
    const wrapper = await mountAt(DecisionConsolePage, client, "/");
    await flushPromises();
    const block = wrapper.get('[data-testid="block-actions"]');
    expect(block.text()).toContain("操作面未启用");
    expect(block.text()).toContain("无待办");
  });

  it("加载中骨架与失败错误码（唯一错误通道 toDisplayError）", async () => {
    resetState();
    const slow: ApiClient = fakeClient({
      listDatasets: () => new Promise(() => {}),
      listExperiments: () => new Promise(() => {}),
      listUpdateJobs: () => new Promise(() => {}),
    });
    const loading = await mountAt(DecisionConsolePage, slow, "/");
    expect(loading.find('[data-testid="console-loading"]').exists()).toBe(true);

    resetState();
    const failing: ApiClient = fakeClient({
      listDatasets: async () => {
        throw new ApiError(500, "internal_error", "");
      },
      listExperiments: async () => ({ experiments: [] }),
      listUpdateJobs: async () => ({ jobs: [] }),
    });
    const errored = await mountAt(DecisionConsolePage, failing, "/");
    await flushPromises();
    expect(errored.get('[data-testid="console-error"]').text()).toContain("internal_error");
  });
});
