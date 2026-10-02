import { describe, expect, it, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";
import VersionDetailPage from "../src/pages/VersionDetailPage.vue";
import {
  datasetDetailResponse,
  fakeClient,
  HASH_A,
  mountAt,
  qualityListResponse,
} from "./helpers";

describe("版本详情页（§10.3 结果展示流程）", () => {
  it("请求 current 在入口解析一次：页面展示回显的完整哈希", async () => {
    const getDataset = vi.fn(async () => datasetDetailResponse(HASH_A));
    const client = fakeClient({
      getDataset,
      listQualityIssues: async () => qualityListResponse(HASH_A),
    });
    const wrapper = await mountAt(VersionDetailPage, client, "/versions/current");
    await flushPromises();
    expect(getDataset).toHaveBeenCalledWith("current");
    expect(wrapper.get('[data-testid="full-version"]').text()).toBe(HASH_A);
    expect(client.listQualityIssues).toBeDefined();
  });

  it("验收状态：'有效 accepted record'与'最近 verdict'分开显示，REJECTED 不遮蔽 accepted", async () => {
    const detail = datasetDetailResponse(HASH_A);
    // 契约里只有 state/has_valid_accepted_record/latest_verdict/record_count，
    // 没有 latest_verdict_at——时间戳不在这条响应里。
    detail.acceptance = {
      state: "ACCEPTED",
      has_valid_accepted_record: true,
      latest_verdict: "REJECTED",
      record_count: 2,
    };
    const client = fakeClient({
      getDataset: async () => detail,
      listQualityIssues: async () => qualityListResponse(HASH_A),
    });
    const wrapper = await mountAt(VersionDetailPage, client, `/versions/${HASH_A}`);
    await flushPromises();
    expect(wrapper.get('[data-testid="accepted-record-block"]').text()).toContain("存在");
    expect(wrapper.get('[data-testid="latest-verdict-value"]').text()).toContain("REJECTED");
  });

  it("表计数、质量摘要与质量问题真实展示（含阻断形态）", async () => {
    const detail = datasetDetailResponse(HASH_A);
    // 门禁状态读 acceptance.state；阻断项数与质量条数从 by_severity 派生
    // （契约没有 passed/blocking_reasons/issue_count 三个字段）。
    detail.acceptance = { ...detail.acceptance, state: "REJECTED" };
    detail.quality = { by_severity: { FATAL: 1, WARNING: 1 } };
    const client = fakeClient({
      getDataset: async () => detail,
      listQualityIssues: async () => qualityListResponse(HASH_A),
    });
    const wrapper = await mountAt(VersionDetailPage, client, `/versions/${HASH_A}`);
    await flushPromises();
    expect(wrapper.get('[data-testid="quality-summary"]').text()).toContain("门禁阻断（1 项）");
    expect(wrapper.get('[data-testid="quality-summary"]').text()).toContain("质量问题 2 条");
    // 阻断明细只能从质量问题列表拿（按 severity 过滤），不从摘要拿。
    expect(wrapper.get('[data-testid="blocking-issues"]').text()).toContain("fetch_coverage_gap");
    expect(wrapper.findAll('[data-testid="quality-issues"] tbody tr')).toHaveLength(2);
    expect(wrapper.get('[data-testid="table-meta"]').text()).toContain("daily_bar");
    expect(wrapper.get('[data-testid="table-meta"]').text()).toContain("120");
  });
});
