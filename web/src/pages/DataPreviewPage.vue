<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import { resolveAndPin, versionPinState } from "../stores/version";
import type { DatasetSummary, TablePreviewResponse } from "../api/types";
import Card from "../components/Card.vue";
import DataTable from "../components/DataTable.vue";
import type { DataTableColumn } from "../components/DataTable.vue";

const client = useApiClient();
const LIMIT = 100;

const requestedVersion = ref("current");
const datasetOptions = ref<DatasetSummary[]>([]);
const resolvedVersion = ref<string | null>(null);
const tableOptions = ref<string[]>([]);
const selectedTable = ref<string | null>(null);
const availableColumns = ref<string[]>([]);
const selectedColumns = reactive(new Set<string>());
const filters = reactive({ trade_date: "", symbol: "" });
const offset = ref(0);
const preview = ref<TablePreviewResponse | null>(null);
const error = ref<DisplayError | null>(null);
const loading = ref(false);

function nullIfEmpty(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

async function resolveVersion() {
  error.value = null;
  const detail = await resolveAndPin(client, requestedVersion.value);
  resolvedVersion.value = detail.dataset_version;
  // detail.tables 是 TableMeta 对象数组（pin I2），取 name 而不是对象键。
  tableOptions.value = detail.tables.map((meta) => meta.name).sort();
  selectedTable.value = tableOptions.value[0] ?? null;
  selectedColumns.clear();
  availableColumns.value = [];
  offset.value = 0;
  preview.value = null;
}

async function loadPreview() {
  if (resolvedVersion.value === null || selectedTable.value === null) return;
  loading.value = true;
  error.value = null;
  try {
    const response = await client.previewTable(resolvedVersion.value, selectedTable.value, {
      columns: selectedColumns.size === 0 ? null : [...selectedColumns].sort(),
      // pin I4：单值 trade_date，没有区间参数。
      trade_date: nullIfEmpty(filters.trade_date),
      symbol: nullIfEmpty(filters.symbol),
      offset: offset.value,
      limit: LIMIT,
    });
    if (response.dataset_version !== resolvedVersion.value) {
      error.value = {
        code: "version_echo_mismatch",
        message: "响应回显的 dataset_version 与请求不一致",
      };
      preview.value = null;
      return;
    }
    preview.value = response;
    availableColumns.value = response.columns;
  } catch (cause) {
    error.value = toDisplayError(cause);
    preview.value = null;
  } finally {
    loading.value = false;
  }
}

function toggleColumn(column: string) {
  if (selectedColumns.has(column)) {
    selectedColumns.delete(column);
  } else {
    selectedColumns.add(column);
  }
}

/**
 * 列定义来自响应的 columns 动态映射；数值列右对齐以首行取值判定——只是渲染对齐，不是计算。
 */
const previewColumns = computed<DataTableColumn[]>(() => {
  const current = preview.value;
  if (current === null) return [];
  return current.columns.map((name) => ({
    key: name,
    label: name,
    mono: name !== "trade_date",
    align: typeof current.rows[0]?.[name] === "number" ? "right" : "left",
  }));
});

onMounted(async () => {
  try {
    datasetOptions.value = (await client.listDatasets()).datasets;
  } catch {
    // 版本选择器退化为仅 current；页面自身的加载错误仍会在下方如实显示
  }
  try {
    await resolveVersion();
    await loadPreview();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});

async function onVersionChange() {
  try {
    await resolveVersion();
    await loadPreview();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
}

async function onTableChange() {
  offset.value = 0;
  await loadPreview();
}

async function applyFilters() {
  offset.value = 0;
  await loadPreview();
}

async function previousPage() {
  offset.value = Math.max(0, offset.value - LIMIT);
  await loadPreview();
}

async function nextPage() {
  offset.value = offset.value + LIMIT;
  await loadPreview();
}
</script>

<template>
  <section>
    <h1>数据预览</h1>
    <p v-if="resolvedVersion !== null" data-testid="resolved-echo">
      已解析版本：<code data-testid="resolved-version">{{ resolvedVersion }}</code>
      <span
        v-if="resolvedVersion === versionPinState.currentVersion"
        class="badge"
        title="CURRENT 是指针标记，不是可信等级"
      >
        CURRENT
      </span>
    </p>
    <p v-else-if="error === null" data-testid="loading">加载中</p>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>

    <Card v-if="resolvedVersion !== null" title="查询条件">
      <form data-testid="preview-controls" @submit.prevent="applyFilters">
        <label>
          版本
          <select v-model="requestedVersion" data-testid="version-select" @change="onVersionChange">
            <option value="current">current（入口别名）</option>
            <option
              v-for="option in datasetOptions"
              :key="option.dataset_version"
              :value="option.dataset_version"
            >
              {{ option.dataset_version }}
            </option>
          </select>
        </label>
        <label>
          表
          <select v-model="selectedTable" data-testid="table-select" @change="onTableChange">
            <option v-for="table in tableOptions" :key="table" :value="table">{{ table }}</option>
          </select>
        </label>
        <!-- pin I4：服务端只有单值 trade_date，所以这里只提供一个日期输入。 -->
        <label>
          交易日
          <input type="date" v-model="filters.trade_date" data-testid="filter-trade-date" />
        </label>
        <label>symbol <input type="text" v-model="filters.symbol" data-testid="filter-symbol" /></label>
        <button type="submit" data-testid="apply-filters">查询</button>
      </form>

      <fieldset v-if="availableColumns.length > 0">
        <legend>列筛选（不选 = 全部白名单列）</legend>
        <label v-for="column in availableColumns" :key="column">
          <input
            type="checkbox"
            :value="column"
            :checked="selectedColumns.has(column)"
            @change="toggleColumn(column)"
          />
          {{ column }}
        </label>
      </fieldset>
    </Card>

    <p v-if="preview !== null" data-testid="pager">
      行 {{ offset }}–{{ offset + preview.rows.length }}（limit {{ LIMIT }}）
      <button type="button" data-testid="prev-page" :disabled="offset === 0" @click="previousPage">
        上一页
      </button>
      <!-- 表预览响应没有 total（pin I4）；"还有下一页"只能按 rows.length 判。 -->
      <button
        type="button"
        data-testid="next-page"
        :disabled="preview.rows.length < LIMIT"
        @click="nextPage"
      >
        下一页
      </button>
      <span v-if="loading">加载中…</span>
    </p>

    <DataTable
      v-if="preview !== null"
      testid="preview-table"
      :columns="previewColumns"
      :rows="preview.rows"
    />
  </section>
</template>
