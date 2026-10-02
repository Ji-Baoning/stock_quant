<script setup lang="ts">
import { onMounted } from "vue";
import { useApiClient } from "../api/client";
import { NAV_ITEMS } from "../router";
import {
  hasNewerCurrent,
  setCurrentVersion,
  setProjectFingerprint,
  versionPinState,
} from "../stores/version";

const client = useApiClient();

async function refreshCurrent() {
  try {
    const list = await client.listDatasets();
    setCurrentVersion(list.current);
  } catch {
    setCurrentVersion(null);
  }
}

async function copyResolvedVersion() {
  const value = versionPinState.resolvedVersion;
  if (value !== null && navigator.clipboard) {
    await navigator.clipboard.writeText(value);
  }
}

onMounted(async () => {
  try {
    const health = await client.health();
    setProjectFingerprint(health.project_root_fingerprint);
  } catch {
    setProjectFingerprint(null);
  }
  await refreshCurrent();
});
</script>

<template>
  <header class="top-bar" data-testid="top-bar">
    <span class="brand">Stock Quant 数据门户</span>
    <nav class="nav" data-testid="main-nav">
      <RouterLink v-for="item in NAV_ITEMS" :key="item.path" :to="item.path">{{ item.label }}</RouterLink>
    </nav>
    <span class="fingerprint" data-testid="project-fingerprint">
      项目指纹：<code>{{ versionPinState.projectFingerprint ?? "获取失败" }}</code>
    </span>
    <span class="resolved" data-testid="resolved-version">
      已解析版本：
      <code v-if="versionPinState.resolvedVersion !== null">{{ versionPinState.resolvedVersion }}</code>
      <span v-else>未解析</span>
      <button
        type="button"
        class="copy"
        data-testid="copy-version"
        :disabled="versionPinState.resolvedVersion === null"
        @click="copyResolvedVersion"
      >
        复制
      </button>
      <span
        v-if="
          versionPinState.resolvedVersion !== null &&
          versionPinState.resolvedVersion === versionPinState.currentVersion
        "
        class="badge"
        title="CURRENT 是指针标记，不是可信等级"
        data-testid="current-badge"
      >
        CURRENT
      </span>
      <button type="button" data-testid="refresh-current" @click="refreshCurrent">
        刷新 CURRENT
      </button>
    </span>
  </header>
  <p v-if="hasNewerCurrent" class="hint" data-testid="new-version-hint">
    有新版本：<code>{{ versionPinState.currentVersion }}</code>
    ；当前页面保持 <code>{{ versionPinState.resolvedVersion }}</code>
    ，不自动切换（<RouterLink to="/versions">查看版本面板</RouterLink>）
  </p>
</template>
