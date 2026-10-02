import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { ApiError } from "../src/api/client";
import VersionsPage from "../src/pages/VersionsPage.vue";
import { datasetListResponse, fakeClient, HASH_A, mountPage } from "./helpers";
import type { DatasetListResponse } from "../src/api/types";

describe("版本面板（§10.1 页面 1）", () => {
  it("列出全部版本：CURRENT 标记、发布时间证据、表计数、质量摘要真实展示", async () => {
    const client = fakeClient({ listDatasets: async () => datasetListResponse() });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    const rows = wrapper.findAll('[data-testid="dataset-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].find('[data-testid="current-mark"]').exists()).toBe(true);
    expect(rows[1].find('[data-testid="current-mark"]').exists()).toBe(false);
    expect(rows[0].text()).toContain("门禁通过");
    expect(rows[0].text()).toContain("质量问题 0 条");
    expect(rows[1].text()).toContain("门禁阻断（1 项）");
    // 发布时间证据 = DatasetSummary.created_at（pin I1：没有 job/published_at_evidence 字段）。
    expect(rows[0].text()).toContain("2026-10-01T08:00:00+08:00");
    expect(rows[0].text()).toContain("4");
  });

  it("验收四态按'存在有效 accepted record'与'最近 verdict'分栏显示（REJECTED 不遮蔽 accepted）", async () => {
    const list: DatasetListResponse = datasetListResponse();
    list.datasets[0].acceptance = {
      state: "ACCEPTED",
      has_valid_accepted_record: true,
      latest_verdict: "REJECTED",
      record_count: 2,
    };
    list.datasets[1].acceptance = {
      state: "PENDING_CONFIRMATION",
      has_valid_accepted_record: false,
      latest_verdict: "PENDING_CONFIRMATION",
      record_count: 0,
    };
    const client = fakeClient({ listDatasets: async () => list });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    const rows = wrapper.findAll('[data-testid="dataset-row"]');
    expect(rows[0].get('[data-testid="accepted-record"]').text()).toBe("是");
    expect(rows[0].get('[data-testid="latest-verdict"]').text()).toContain("REJECTED");
    expect(rows[1].get('[data-testid="accepted-record"]').text()).toBe("否");
    expect(rows[1].get('[data-testid="latest-verdict"]').text()).toContain("PENDING_CONFIRMATION");
  });

  it("UNVERIFIED（无 verdict）如实显示为 UNVERIFIED，不显示成功文案", async () => {
    const list = datasetListResponse();
    list.datasets[1].acceptance = {
      state: "UNVERIFIED",
      has_valid_accepted_record: false,
      latest_verdict: null,
      record_count: 0,
    };
    const client = fakeClient({ listDatasets: async () => list });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    expect(wrapper.findAll('[data-testid="dataset-row"]')[1].get('[data-testid="latest-verdict"]').text()).toContain("UNVERIFIED");
  });

  it("列表加载失败显示稳定错误码，不显示堆栈或路径", async () => {
    const client = fakeClient({
      listDatasets: async () => {
        throw new ApiError(503, "service_unavailable", "查询面不可用");
      },
    });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    const text = wrapper.get('[data-testid="error"]').text();
    expect(text).toContain("service_unavailable");
    expect(text).not.toMatch(/\/home\/|\.py|stack/i);
  });

  it("每行链接到以完整哈希为参数的版本详情", async () => {
    const client = fakeClient({ listDatasets: async () => datasetListResponse() });
    const wrapper = mountPage(VersionsPage, client);
    await flushPromises();
    const href = wrapper.findAll('[data-testid="dataset-row"]')[0].find("a").attributes("href");
    expect(href).toBe(`#/versions/${HASH_A}`);
  });
});
