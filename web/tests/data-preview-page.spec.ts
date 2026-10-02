import { describe, expect, it, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";
import DataPreviewPage from "../src/pages/DataPreviewPage.vue";
import {
  datasetDetailResponse,
  datasetListResponse,
  fakeClient,
  HASH_A,
  mountPage,
  previewResponse,
} from "./helpers";
import type { TablePreviewParams } from "../src/api/types";

describe("数据预览页（§10.1 页面 2 / §10.2 分页纪律）", () => {
  it("默认解析 current 并钉住完整哈希；翻页携带同一 resolved version 与递增 offset", async () => {
    const previewTable = vi.fn(
      async (version: string, _table: string, params: TablePreviewParams) =>
        previewResponse(version, params.offset),
    );
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable,
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    expect(previewTable.mock.calls[0]![0]).toBe(HASH_A);
    await wrapper.get('[data-testid="next-page"]').trigger("click");
    await flushPromises();
    expect(previewTable.mock.calls[1]![0]).toBe(HASH_A);
    expect(previewTable.mock.calls[1]![2].offset).toBe(100);
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain(HASH_A);
  });

  it("CURRENT 变化只提示不自动切换：翻页仍用旧 resolved 哈希", async () => {
    let current = HASH_A;
    const previewTable = vi.fn(
      async (version: string, _table: string, params: TablePreviewParams) =>
        previewResponse(version, params.offset),
    );
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(current),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable,
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    current = "b".repeat(64);
    await wrapper.get('[data-testid="next-page"]').trigger("click");
    await flushPromises();
    expect(previewTable.mock.calls.at(-1)![0]).toBe(HASH_A);
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain(HASH_A);
  });

  it("列筛选与单日/symbol 过滤进入请求参数（pin I4：只有单值 trade_date）", async () => {
    const previewTable = vi.fn(
      async (version: string, _table: string, params: TablePreviewParams) =>
        previewResponse(version, params.offset),
    );
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable,
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    await wrapper.get('[data-testid="filter-trade-date"]').setValue("2026-09-30");
    await wrapper.get('[data-testid="filter-symbol"]').setValue("000001.SZ");
    const checkbox = wrapper
      .findAll('fieldset input[type="checkbox"]')
      .find((input) => input.attributes("value") === "close");
    await checkbox!.setValue(true);
    await wrapper.get('[data-testid="apply-filters"]').trigger("submit");
    await flushPromises();
    const params = previewTable.mock.calls.at(-1)![2];
    expect(params.columns).toEqual(["close"]);
    expect(params.trade_date).toBe("2026-09-30");
    expect(params.symbol).toBe("000001.SZ");
    expect(params.offset).toBe(0);
  });

  it("第一页禁用上一页；返回行数少于 limit 时禁用下一页（响应无 total，只能按行数判）", async () => {
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable: async (version, _table, params) => previewResponse(version, params.offset, 40),
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    expect(wrapper.get('[data-testid="prev-page"]').attributes("disabled")).toBeDefined();
    // 契约的表预览响应没有 total 字段——"有没有下一页"只能靠 `rows.length < limit`。
    expect(wrapper.get('[data-testid="next-page"]').attributes("disabled")).toBeDefined();
  });

  it("响应回显的 dataset_version 与请求不一致：显示稳定码 version_echo_mismatch 并清空数据", async () => {
    const client = fakeClient({
      listDatasets: async () => datasetListResponse(),
      getDataset: async () => datasetDetailResponse(HASH_A),
      previewTable: async () => previewResponse("c".repeat(64)),
    });
    const wrapper = mountPage(DataPreviewPage, client);
    await flushPromises();
    expect(wrapper.get('[data-testid="error"]').text()).toContain("version_echo_mismatch");
    expect(wrapper.find('[data-testid="preview-table"]').exists()).toBe(false);
  });
});
