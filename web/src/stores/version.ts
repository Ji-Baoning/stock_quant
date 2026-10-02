import { computed, reactive } from "vue";
import type { ApiClient } from "../api/client";
import type { DatasetDetailResponse } from "../api/types";

/** 全门户共享的版本 pin 状态（§4 第 4 条：每请求解析一次，回显完整哈希）。 */
export const versionPinState = reactive({
  resolvedVersion: null as string | null,
  currentVersion: null as string | null,
  projectFingerprint: null as string | null,
});

/** 是否出现比当前页面解析结果更新的 CURRENT 指针（只提示，不自动切换）。 */
export const hasNewerCurrent = computed(
  () =>
    versionPinState.resolvedVersion !== null &&
    versionPinState.currentVersion !== null &&
    versionPinState.resolvedVersion !== versionPinState.currentVersion,
);

export function setCurrentVersion(version: string | null) {
  versionPinState.currentVersion = version;
}

export function setProjectFingerprint(fingerprint: string | null) {
  versionPinState.projectFingerprint = fingerprint;
}

export function clearResolvedVersion() {
  versionPinState.resolvedVersion = null;
}

/** 以请求别名（current 或完整哈希）解析一次并钉住回显的完整哈希。 */
export async function resolveAndPin(
  client: ApiClient,
  requestedVersion: string,
): Promise<DatasetDetailResponse> {
  const detail = await client.getDataset(requestedVersion);
  versionPinState.resolvedVersion = detail.dataset_version;
  return detail;
}
