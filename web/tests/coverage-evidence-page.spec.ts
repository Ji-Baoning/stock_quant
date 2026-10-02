import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import CoverageEvidencePage from "../src/pages/CoverageEvidencePage.vue";
import {
  coveragePreviewResponse,
  datasetDetailResponse,
  datasetListResponse,
  fakeClient,
  HASH_A,
  mountPage,
  previewResponse,
  qualityListResponse,
  verifiedOnlyCoveragePreview,
} from "./helpers";

function evidenceClient() {
  return fakeClient({
    listDatasets: async () => datasetListResponse(),
    getDataset: async () => datasetDetailResponse(HASH_A),
    listQualityIssues: async () => qualityListResponse(HASH_A),
    previewTable: async (version: string, table: string) => {
      if (table === "basic_factor_coverage") return coveragePreviewResponse(version);
      if (table === "daily_bar_coverage") return verifiedOnlyCoveragePreview(version);
      return previewResponse(version);
    },
  });
}

describe("质量/覆盖证据页（§10.3 证据展示）", () => {
  it("展示覆盖段：kind、窗口与 not_fetched 尾段理由（source_unavailable 可见）", async () => {
    const wrapper = mountPage(CoverageEvidencePage, evidenceClient());
    await flushPromises();
    const segments = wrapper.get('[data-testid="coverage-segments"]').text();
    expect(segments).toContain("basic_factor");
    expect(segments).toContain("carried");
    expect(segments).toContain("not_fetched");
    expect(segments).toContain("source_unavailable");
    expect(segments).toContain("2026-09-30");
  });

  it("UNTRUSTED 行按 status == 'UNTRUSTED' 判定展示；无 UNTRUSTED 的表如实显示", async () => {
    const wrapper = mountPage(CoverageEvidencePage, evidenceClient());
    await flushPromises();
    const untrusted = wrapper.get('[data-testid="untrusted-basic_factor_coverage"]').text();
    expect(untrusted).toContain("UNTRUSTED");
    expect(untrusted).toContain("600000.SH");
    expect(untrusted).not.toContain("VERIFIED");
    expect(wrapper.get('[data-testid="untrusted-empty-daily_bar_coverage"]').text()).toContain(
      "无 UNTRUSTED 行",
    );
  });

  it("质量问题列表展示（coverage gap 码可见）", async () => {
    const wrapper = mountPage(CoverageEvidencePage, evidenceClient());
    await flushPromises();
    expect(wrapper.get('[data-testid="issue-list"]').text()).toContain("fetch_coverage_gap");
  });

  it("attested-boundary 近似与滞后上界可见（§7.1）", async () => {
    const wrapper = mountPage(CoverageEvidencePage, evidenceClient());
    await flushPromises();
    const note = wrapper.get('[data-testid="attested-boundary-note"]').text();
    expect(note).toContain("attested-boundary");
    expect(note).toContain("滞后上界");
    expect(note).toContain("不是官方公告日");
  });
});
