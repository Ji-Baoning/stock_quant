import { describe, expect, it } from "vitest";
import {
  clearResolvedVersion,
  hasNewerCurrent,
  resolveAndPin,
  setCurrentVersion,
  versionPinState,
} from "../src/stores/version";
import { datasetDetailResponse, fakeClient, HASH_A, HASH_B } from "./helpers";

function resetStore() {
  versionPinState.resolvedVersion = null;
  versionPinState.currentVersion = null;
  versionPinState.projectFingerprint = null;
}

describe("版本 pin store", () => {
  it("resolveAndPin 以响应回显的完整哈希为准钉住（请求 current）", async () => {
    resetStore();
    const client = fakeClient({ getDataset: async () => datasetDetailResponse(HASH_A) });
    const detail = await resolveAndPin(client, "current");
    expect(detail.dataset_version).toBe(HASH_A);
    expect(versionPinState.resolvedVersion).toBe(HASH_A);
  });

  it("hasNewerCurrent：resolved 与 current 不同才提示", async () => {
    resetStore();
    const client = fakeClient({ getDataset: async () => datasetDetailResponse(HASH_A) });
    await resolveAndPin(client, HASH_A);
    expect(hasNewerCurrent.value).toBe(false);
    setCurrentVersion(HASH_B);
    expect(hasNewerCurrent.value).toBe(true);
    expect(versionPinState.resolvedVersion).toBe(HASH_A); // 不自动切换
    setCurrentVersion(HASH_A);
    expect(hasNewerCurrent.value).toBe(false);
    clearResolvedVersion();
    expect(hasNewerCurrent.value).toBe(false);
  });
});
