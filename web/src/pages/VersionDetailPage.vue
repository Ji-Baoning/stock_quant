<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import {
  blockingIssueCount,
  totalIssueCount,
  isBlockingSeverity,
} from "../api/quality";
import { resolveAndPin, versionPinState } from "../stores/version";
import type { DatasetDetailResponse, QualityIssueRecord } from "../api/types";
import Card from "../components/Card.vue";
import DataTable from "../components/DataTable.vue";
import StateBadge from "../components/StateBadge.vue";
import type { DataTableColumn } from "../components/DataTable.vue";

const route = useRoute();
const client = useApiClient();
const requestedVersion = String(route.params.version ?? "current");
const detail = ref<DatasetDetailResponse | null>(null);
const issues = ref<QualityIssueRecord[]>([]);
const error = ref<DisplayError | null>(null);

// 门禁状态读 acceptance.state；两项计数从 quality.by_severity 派生——契约里
// 没有 passed/blocking_reasons/issue_count 可用。
const blockingCount = computed(() =>
  detail.value === null ? 0 : blockingIssueCount(detail.value.quality),
);
const totalCount = computed(() =>
  detail.value === null ? 0 : totalIssueCount(detail.value.quality),
);
// 阻断明细只能从质量问题列表按 severity 过滤（本页只取前 100 条）。
const blockingIssues = computed(() =>
  issues.value.filter((issue) => isBlockingSeverity(issue.severity)),
);

/** 列定义照搬旧表头；不传 sortable，本页不新增排序行为。 */
const tableMetaColumns: DataTableColumn[] = [
  { key: "name", label: "表", mono: true },
  { key: "row_count", label: "行数", align: "right" },
  { key: "schema_version", label: "schema 版本" },
];
const qualityIssueColumns: DataTableColumn[] = [
  { key: "code", label: "code" },
  { key: "severity", label: "severity" },
  { key: "table", label: "表" },
  { key: "trade_date", label: "日期" },
  { key: "details", label: "details" },
];

onMounted(async () => {
  try {
    detail.value = await resolveAndPin(client, requestedVersion);
    const quality = await client.listQualityIssues(detail.value.dataset_version, 0, 100);
    issues.value = quality.issues;
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});
</script>

<template>
  <section>
    <h1>版本详情</h1>
    <p v-if="error !== null" class="error" data-testid="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <template v-else-if="detail !== null">
      <Card>
        <p data-testid="resolved-echo">
          已解析版本（请求 <code>{{ requestedVersion }}</code>）：
          <code data-testid="full-version">{{ detail.dataset_version }}</code>
          <span
            v-if="versionPinState.currentVersion === detail.dataset_version"
            class="badge"
            title="CURRENT 是指针标记，不是可信等级"
          >
            CURRENT
          </span>
        </p>
      </Card>
      <!-- 详情响应里没有 created_at，发布时间证据只在列表页（DatasetSummary.created_at）。 -->
      <h2>表</h2>
      <!-- detail.tables 是 TableMeta 对象数组，不是映射。 -->
      <DataTable
        testid="table-meta"
        row-key="name"
        :columns="tableMetaColumns"
        :rows="detail.tables"
      />
      <Card title="质量摘要">
        <p data-testid="quality-summary">
          <span v-if="detail.acceptance.state === 'ACCEPTED'">门禁通过</span>
          <span v-else class="warn">门禁阻断（{{ blockingCount }} 项）</span>
          ；质量问题 {{ totalCount }} 条
        </p>
        <!-- 阻断明细来自质量问题列表（按 severity 过滤），摘要里没有 blocking_reasons。 -->
        <ul v-if="blockingIssues.length > 0" data-testid="blocking-issues">
          <li v-for="issue in blockingIssues" :key="`${issue.code}-${issue.table}-${issue.trade_date}`">
            {{ issue.severity }} {{ issue.code }}
          </li>
        </ul>
      </Card>
      <Card title="验收状态（分开显示）">
        <div class="acceptance-columns">
          <div data-testid="accepted-record-block">
            <h3>有效 accepted record</h3>
            <p>{{ detail.acceptance.has_valid_accepted_record ? "存在" : "不存在" }}</p>
          </div>
          <div data-testid="latest-verdict-block">
            <h3>最近 verdict</h3>
            <!-- 契约的 AcceptanceSummary 没有 latest_verdict_at，时间戳不在详情响应里。 -->
            <p>
              <span data-testid="latest-verdict-value">
                <StateBadge
                  kind="acceptance"
                  :value="detail.acceptance.latest_verdict ?? detail.acceptance.state"
                />
              </span>
            </p>
          </div>
        </div>
      </Card>
      <h2>质量问题（前 100 条）</h2>
      <!-- 契约没有 summary 字段（pin I3）；details 是安全的结构化载荷。 -->
      <DataTable testid="quality-issues" :columns="qualityIssueColumns" :rows="issues">
        <template #details="{ value }">
          <dl v-if="value !== null && typeof value === 'object'" class="kv">
            <template v-for="(item, key) in value" :key="String(key)">
              <dt><code>{{ key }}</code></dt>
              <dd>{{ typeof item === "object" ? JSON.stringify(item) : String(item) }}</dd>
            </template>
          </dl>
          <span v-else>—</span>
        </template>
      </DataTable>
    </template>
    <p v-else data-testid="loading">加载中</p>
  </section>
</template>
