<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import { isBlockingSeverity } from "../api/quality";
import { resolveAndPin, versionPinState } from "../stores/version";
import type {
  DatasetDetailResponse,
  DatasetSummary,
  FetchCoverageSegmentRecord,
  QualityIssueRecord,
  TablePreviewResponse,
} from "../api/types";
import Card from "../components/Card.vue";
import DataTable from "../components/DataTable.vue";
import type { DataTableColumn } from "../components/DataTable.vue";

const client = useApiClient();

const requestedVersion = ref("current");
const datasetOptions = ref<DatasetSummary[]>([]);
const detail = ref<DatasetDetailResponse | null>(null);
const issues = ref<QualityIssueRecord[]>([]);
const untrustedRows = ref<Record<string, Record<string, string | number | null>[]>>({});
const error = ref<DisplayError | null>(null);

/** §7.1：滞后上界必须成文并在 UI 可见。 */
const ATTESTED_BOUNDARY_NOTE =
  "attested-boundary 近似：announcement_date = 证实该边界的快照日（与 raw_effective_from 同日），"
  + "不是官方公告日；快照节奏为月度时，月中调整可能被记到快照边界，滞后上界约一个快照周期。"
  + "对生效日精度敏感的研究必须显式接受该近似。";

/** 列定义照搬旧表头（窗口拆成 start/end 两列）；不传 sortable，本页不新增排序行为。 */
const segmentColumns: DataTableColumn[] = [
  { key: "table", label: "表", mono: true },
  { key: "kind", label: "kind" },
  { key: "window_start", label: "window_start", mono: true },
  { key: "window_end", label: "window_end", mono: true },
  { key: "reason", label: "理由" },
];

/** 列定义照搬旧表头；severity 着色见 #severity 插槽，文案原样。 */
const issueColumns: DataTableColumn[] = [
  { key: "code", label: "code" },
  { key: "severity", label: "severity" },
  { key: "table", label: "表" },
  { key: "trade_date", label: "日期" },
  { key: "details", label: "details" },
];

/**
 * kind → 三态徽章：页面级纯展示映射（kind 不是 StateBadge 的三域之一，
 * 不扩其领域）；徽章内仍渲染 kind 原词。
 */
const KIND_BADGE_CLASS: Record<string, string> = {
  fetched: "badge-state--pass",
  carried: "badge-state--pending",
  not_fetched: "badge-state--block",
};

function kindBadgeClass(kind: unknown): string {
  return KIND_BADGE_CLASS[String(kind)] ?? "badge-state--neutral";
}

/** 阻断级 severity 用 --color-block，其余 --color-text-secondary；只是着色，不改文案。 */
function severityColor(severity: unknown): string {
  return isBlockingSeverity(typeof severity === "string" ? severity : null)
    ? "var(--color-block)"
    : "var(--color-text-secondary)";
}

/** 覆盖段行：拍平 segment 并带复合 row-key（table+kind+窗口），供 DataTable 使用。 */
type SegmentRow = {
  key: string;
  table: string;
  kind: FetchCoverageSegmentRecord["kind"];
  window_start: string;
  window_end: string;
  reason: string | null;
};

const segments = computed<SegmentRow[]>(() => {
  if (detail.value === null) return [];
  const rows: SegmentRow[] = [];
  for (const [table, tableSegments] of Object.entries(coverageByTable.value)) {
    for (const segment of tableSegments) {
      rows.push({
        key: `${table}-${segment.kind}-${segment.window_start}-${segment.window_end}`,
        table,
        kind: segment.kind,
        window_start: segment.window_start,
        window_end: segment.window_end,
        reason: segment.reason ?? null,
      });
    }
  }
  return rows;
});

/** pin I2：覆盖段在 manifest.build_config 里，不在响应顶层。 */
const coverageByTable = computed<Record<string, FetchCoverageSegmentRecord[]>>(() => {
  const manifest = detail.value?.manifest as
    | { build_config?: { table_fetch_coverage?: Record<string, FetchCoverageSegmentRecord[]> } }
    | undefined;
  return manifest?.build_config?.table_fetch_coverage ?? {};
});

const coverageTables = computed<string[]>(() => {
  if (detail.value === null) return [];
  // detail.tables 是 TableMeta 对象数组（pin I2）。
  return detail.value.tables
    .map((meta) => meta.name)
    .filter((name) => name.endsWith("_coverage"))
    .sort();
});

async function loadEvidence() {
  error.value = null;
  const resolved = await resolveAndPin(client, requestedVersion.value);
  detail.value = resolved;
  issues.value = (await client.listQualityIssues(resolved.dataset_version, 0, 100)).issues;
  const collected: Record<string, Record<string, string | number | null>[]> = {};
  for (const table of coverageTables.value) {
    const preview: TablePreviewResponse = await client.previewTable(
      resolved.dataset_version,
      table,
      { columns: null, symbol: null, trade_date: null, offset: 0, limit: 500 },
    );
    if (preview.dataset_version !== resolved.dataset_version) {
      throw new Error("version_echo_mismatch");
    }
    collected[table] = preview.rows.filter((row) => row.status === "UNTRUSTED");
  }
  untrustedRows.value = collected;
}

onMounted(async () => {
  try {
    datasetOptions.value = (await client.listDatasets()).datasets;
  } catch {
    // 选择器退化为仅 current；错误在下方如实显示
  }
  try {
    await loadEvidence();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});

async function onVersionChange() {
  try {
    await loadEvidence();
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
}
</script>

<template>
  <section>
    <h1>质量/覆盖证据</h1>
    <p v-if="detail !== null" data-testid="resolved-echo">
      已解析版本：<code data-testid="resolved-version">{{ detail.dataset_version }}</code>
      <span
        v-if="detail.dataset_version === versionPinState.currentVersion"
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

    <Card v-if="detail !== null">
      <form @submit.prevent="onVersionChange">
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
      </form>
    </Card>

    <h2>覆盖段（table_fetch_coverage）</h2>
    <DataTable
      v-if="detail !== null"
      testid="coverage-segments"
      row-key="key"
      :columns="segmentColumns"
      :rows="segments"
    >
      <template #kind="{ value }">
        <span class="badge-state" :class="kindBadgeClass(value)">{{ value }}</span>
      </template>
    </DataTable>

    <h2>UNTRUSTED 行（按 status == "UNTRUSTED" 判定）</h2>
    <div
      v-for="table in coverageTables"
      :key="table"
      :data-testid="`untrusted-${table}`"
    >
      <h3>{{ table }}</h3>
      <table v-if="(untrustedRows[table] ?? []).length > 0">
        <thead>
          <tr><th>trade_date</th><th>symbol</th><th>status</th><th>reason</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in untrustedRows[table]" :key="index">
            <td>{{ row.trade_date }}</td>
            <td>{{ row.symbol }}</td>
            <td>{{ row.status }}</td>
            <td>{{ row.reason }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else :data-testid="`untrusted-empty-${table}`">无 UNTRUSTED 行</p>
    </div>

    <h2>质量问题</h2>
    <DataTable
      v-if="detail !== null"
      testid="issue-list"
      :columns="issueColumns"
      :rows="issues"
    >
      <template #severity="{ value }">
        <span :style="{ color: severityColor(value) }">{{ value }}</span>
      </template>
      <!-- 契约没有 summary 字段（pin I3）；details 保持 JSON 序列化文本。 -->
      <template #details="{ value }">
        {{ value === null ? "—" : JSON.stringify(value) }}
      </template>
    </DataTable>

    <h2>attested-boundary 近似标注</h2>
    <blockquote data-testid="attested-boundary-note">{{ ATTESTED_BOUNDARY_NOTE }}</blockquote>
  </section>
</template>
