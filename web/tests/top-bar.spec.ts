import { describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import AppTopBar from "../src/components/AppTopBar.vue";
import { apiClientKey } from "../src/api/client";
import { createPortalRouter } from "../src/router";
import {
  datasetListResponse,
  fakeClient,
  healthResponse,
  HASH_A,
  HASH_B,
} from "./helpers";
import {
  resolveAndPin,
  setCurrentVersion,
  setProjectFingerprint,
  versionPinState,
} from "../src/stores/version";

function resetStore() {
  versionPinState.resolvedVersion = null;
  versionPinState.currentVersion = null;
  versionPinState.projectFingerprint = null;
}

function mountTopBar(client: ReturnType<typeof fakeClient>) {
  return mount(AppTopBar, {
    global: {
      plugins: [createPortalRouter()],
      provide: { [apiClientKey as symbol]: client },
    },
  });
}

describe("共享顶栏（§10.3 页面地图）", () => {
  it("展示 project-root 指纹；未解析版本显示真实状态'未解析'", async () => {
    resetStore();
    const client = fakeClient({
      health: async () => healthResponse(),
      listDatasets: async () => datasetListResponse(),
    });
    const wrapper = mountTopBar(client);
    await flushPromises();
    expect(wrapper.get('[data-testid="project-fingerprint"]').text()).toContain("0123456789abcdef");
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain("未解析");
    expect(wrapper.find('[data-testid="current-badge"]').exists()).toBe(false);
  });

  it("展示解析出的完整哈希（可复制）与 CURRENT 指针徽标", async () => {
    resetStore();
    const client = fakeClient({
      health: async () => healthResponse(),
      listDatasets: async () => datasetListResponse(HASH_A),
      getDataset: async () => (await import("./helpers")).datasetDetailResponse(HASH_A),
    });
    const wrapper = mountTopBar(client);
    await flushPromises();
    await resolveAndPin(client, "current");
    setCurrentVersion(HASH_A);
    await flushPromises();
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain(HASH_A);
    expect(wrapper.get('[data-testid="current-badge"]').attributes("title")).toBe(
      "CURRENT 是指针标记，不是可信等级",
    );
    const writeText = vi.fn(async () => undefined);
    // happy-dom 20 的 navigator.clipboard 是 getter-only，用 defineProperty 注入假实现。
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    await wrapper.get('[data-testid="copy-version"]').trigger("click");
    expect(writeText).toHaveBeenCalledWith(HASH_A);
  });

  it("刷新 CURRENT 后若有新版本：只提示，不自动切换", async () => {
    resetStore();
    let current = HASH_A;
    const client = fakeClient({
      health: async () => healthResponse(),
      listDatasets: async () => datasetListResponse(current),
      getDataset: async () => (await import("./helpers")).datasetDetailResponse(HASH_A),
    });
    const wrapper = mountTopBar(client);
    await flushPromises();
    await resolveAndPin(client, "current");
    expect(wrapper.find('[data-testid="new-version-hint"]').exists()).toBe(false);
    current = HASH_B;
    await wrapper.get('[data-testid="refresh-current"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="new-version-hint"]').text()).toContain("有新版本");
    expect(wrapper.get('[data-testid="resolved-version"]').text()).toContain(HASH_A);
  });

  it("health 失败时显示真实状态'获取失败'，不显示成功文案", async () => {
    resetStore();
    const client = fakeClient({
      health: async () => {
        throw new Error("down");
      },
      listDatasets: async () => datasetListResponse(),
    });
    const wrapper = mountTopBar(client);
    await flushPromises();
    expect(wrapper.get('[data-testid="project-fingerprint"]').text()).toContain("获取失败");
  });
});
