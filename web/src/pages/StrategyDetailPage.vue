<!-- web/src/pages/StrategyDetailPage.vue —— S1 Task 8：策略详情（tearsheet）。
     本页只读已发布产物并逐字展示；归一化与逐点回撤是 §7.4 允许的**渲染变换**
     （作用于单条已发布序列），不做任何跨序列/跨折计算或拼接。 -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useApiClient } from "../api/client";
import { toDisplayError, type DisplayError } from "../api/errors";
import type { FoldEquityResponse } from "../api/types";
import BarChart from "../components/BarChart.vue";
import Card from "../components/Card.vue";
import DataTable, { type DataTableColumn } from "../components/DataTable.vue";
import EmptyState from "../components/EmptyState.vue";
import KpiCard from "../components/KpiCard.vue";
import LineChart, { type LineSeries } from "../components/LineChart.vue";
import Skeleton from "../components/Skeleton.vue";
import StateBadge from "../components/StateBadge.vue";

interface FoldMetricRow {
  fold_id: string;
  scenario: string;
  fold_calendar_return: number | null;
  per_fold_max_drawdown: number | null;
  sharpe_zero_rf: number | null;
  explicit_cost_drag: number | null;
  net_return: number | null;
  reject_rate: number | null;
  turnover: number | null;
  first_trading_day: string | null;
  last_trading_day: string | null;
}

const client = useApiClient();
const route = useRoute();
const experimentId = String(route.params.experimentId);

const manifest = ref<Record<string, unknown> | null>(null);
const metrics = ref<Record<string, unknown> | null>(null);
const report = ref<Record<string, unknown> | null>(null);
const loaded = ref(false);
const error = ref<DisplayError | null>(null);
const hasReportHtml = ref<boolean | null>(null);

const selectedFold = ref<string>("");
const scenario = ref<string>("");

const equity = ref<FoldEquityResponse | null>(null);
const benchmarkRows = ref<Array<{ trade_date: string; close: number }>>([]);
const seriesError = ref<DisplayError | null>(null);

const conclusion = computed(
  () => (report.value?.stability_conclusion as string | undefined) ?? null,
);
const hypothesis = computed(() => {
  const spec = (metrics.value?.meta as Record<string, unknown> | undefined)?.spec as
    | Record<string, unknown>
    | undefined;
  const value = spec?.hypothesis;
  return typeof value === "string" ? value : null;
});
const evaluation = computed(
  () => (metrics.value?.evaluation as Record<string, unknown> | undefined) ?? null,
);
const evaluationStatus = computed(() => {
  const value = evaluation.value?.status;
  return typeof value === "string" ? value : null;
});
const evaluationReason = computed(() => {
  const value = evaluation.value?.reason;
  return typeof value === "string" ? value : null;
});
const policyHash = computed(() => {
  const fromReport = report.value?.stability_policy_hash;
  if (typeof fromReport === "string" && fromReport !== "") return fromReport;
  const fromManifest = manifest.value?.stability_policy_hash;
  return typeof fromManifest === "string" ? fromManifest : null;
});
const thresholdsSummary = computed(() => {
  const thresholds = report.value?.thresholds;
  if (thresholds === null || thresholds === undefined || typeof thresholds !== "object") {
    return "—";
  }
  return Object.entries(thresholds as Record<string, unknown>)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
});

// 真实产物的折状态是小写枚举（schedule.py FoldOutcomeStatus）；折选择器只列
// executed 折——失败折没有净值产物，进去只会 404。
const foldStatuses = computed(() =>
  ((report.value?.fold_statuses as Array<Record<string, unknown>> | undefined) ?? []).filter(
    (entry) => entry.status === "executed",
  ),
);
const foldMetrics = computed(() => (report.value?.fold_metrics as FoldMetricRow[] | undefined) ?? []);
// 情景页签来自 fold_metrics 行的场景集合（有逐折数据的情景才有证据可看）。
const scenarios = computed(() => [...new Set(foldMetrics.value.map((row) => row.scenario))]);
const aggregates = computed(
  () => (report.value?.scenario_aggregates as Array<Record<string, unknown>> | undefined) ?? [],
);
const scenarioFolds = computed(() =>
  foldMetrics.value.filter((row) => row.scenario === scenario.value),
);
const currentAggregate = computed(
  () => aggregates.value.find((entry) => entry.scenario === scenario.value) ?? null,
);

const benchmarkSymbol = computed(() => {
  const meta = metrics.value?.meta as Record<string, unknown> | undefined;
  const symbols = meta?.benchmark_symbols;
  const first = Array.isArray(symbols) ? symbols[0] : undefined;
  return typeof first === "string" && first !== "" ? first : "000300.SH";
});

function pct(value: unknown): string {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—";
}
function num(value: unknown): string {
  return typeof value === "number" ? value.toFixed(2) : "—";
}
function short(value: string | null, width: number): string {
  return value === null ? "—" : value.slice(0, width);
}
function manifestString(key: string): string | null {
  const value = manifest.value?.[key];
  return typeof value === "string" ? value : null;
}
function aggregate(field: string): unknown {
  return currentAggregate.value?.[field];
}
const oosCompare = computed(
  () => `OOS 观测 ${currentAggregate.value?.oos_return_observations ?? "—"}`,
);

/** §7.4 渲染变换：单条已发布净值序列的归一化与逐点回撤（不是指标计算，
     不与基准或其它折做任何运算）。 */
const normalized = computed(() => {
  const rows = equity.value?.rows ?? [];
  const base = rows[0]?.net_equity_after_cost;
  if (base === undefined || Number(base) === 0) return [] as number[];
  return rows.map((row) => Number(row.net_equity_after_cost) / Number(base));
});
const benchmarkNormalized = computed(() => {
  if (benchmarkRows.value.length === 0) return [] as number[];
  const base = benchmarkRows.value[0].close;
  return benchmarkRows.value.map((row) => row.close / base);
});
const underwater = computed(() => {
  const series = normalized.value;
  let peak = Number.NEGATIVE_INFINITY;
  return series.map((value) => {
    peak = Math.max(peak, value);
    return value / peak - 1;
  });
});

const equityDates = computed(() =>
  (equity.value?.rows ?? []).map((row) => String(row.trade_date)),
);
const netValueSeries = computed<LineSeries[]>(() => [
  { name: "策略（费后）", data: normalized.value },
  // 基准是另一条已发布序列的独立归一化（各自 ÷ 各自首点），虚线表达；
  // 两条线共用横轴但互不运算——没有相对收益、没有重基。
  { name: `基准 ${benchmarkSymbol.value}`, data: benchmarkNormalized.value, dashed: true },
]);
const underwaterSeries = computed<LineSeries[]>(() => [
  { name: "逐点回撤", data: underwater.value },
]);
const foldBarLabels = computed(() =>
  scenarioFolds.value.map((row) => row.fold_id.slice(0, 8)),
);
const foldBarSeries = computed(() => [
  { name: "日历收益", data: scenarioFolds.value.map((row) => row.fold_calendar_return) },
  { name: "逐折回撤", data: scenarioFolds.value.map((row) => row.per_fold_max_drawdown) },
]);

const FOLD_COLUMNS: DataTableColumn[] = [
  { key: "fold_id", label: "折" },
  { key: "scenario", label: "情景" },
  { key: "calendar", label: "日历收益", align: "right" },
  { key: "drawdown", label: "逐折回撤", align: "right" },
  { key: "sharpe", label: "Sharpe", align: "right" },
  { key: "cost_drag", label: "成本拖累", align: "right" },
  { key: "net_return", label: "净收益", align: "right" },
  { key: "reject_rate", label: "拒单率", align: "right" },
  { key: "turnover", label: "换手", align: "right" },
];
const foldTableRows = computed(() =>
  scenarioFolds.value.map((row) => ({
    fold_id: row.fold_id,
    scenario: row.scenario,
    calendar: pct(row.fold_calendar_return),
    drawdown: pct(row.per_fold_max_drawdown),
    sharpe: num(row.sharpe_zero_rf),
    cost_drag: pct(row.explicit_cost_drag),
    net_return: pct(row.net_return),
    reject_rate: pct(row.reject_rate),
    turnover: pct(row.turnover),
  })),
);

const artifactCount = computed(() => {
  const artifacts = manifest.value?.artifacts;
  return artifacts !== null && artifacts !== undefined && typeof artifacts === "object"
    ? Object.keys(artifacts as Record<string, unknown>).length
    : 0;
});
const reportUrl = computed(() => `/api/v1/experiments/${encodeURIComponent(experimentId)}/report`);

/** 竞态护栏：换折/换情景连点时只有最后一次请求能落盘，过期响应被丢弃。 */
let seriesToken = 0;
async function loadFoldSeries() {
  if (selectedFold.value === "" || scenario.value === "") return;
  const token = ++seriesToken;
  seriesError.value = null;
  equity.value = null;
  benchmarkRows.value = [];
  try {
    const equityResponse = await client.foldEquity(experimentId, selectedFold.value, scenario.value);
    if (token !== seriesToken) return;
    equity.value = equityResponse;
    // 基准窗口 = 该折 fold_metrics 行的首/末交易日（不是全期）。
    const foldRow = scenarioFolds.value.find((row) => row.fold_id === selectedFold.value);
    const start = foldRow?.first_trading_day ?? undefined;
    const end = foldRow?.last_trading_day ?? undefined;
    if (start !== undefined && end !== undefined) {
      const benchmark = await client.datasetBenchmark(
        String(manifest.value?.dataset_version ?? "current"),
        { start, end },
      );
      if (token !== seriesToken) return;
      benchmarkRows.value = benchmark.rows;
    }
  } catch (cause) {
    if (token === seriesToken) {
      seriesError.value = toDisplayError(cause);
    }
  }
}

/** 初次取数由 onMounted 直接驱动；watch 只服务之后的折/情景切换（避免初始化双取）。 */
let bootstrapped = false;
watch([selectedFold, scenario], () => {
  if (!bootstrapped) return;
  void loadFoldSeries();
});

onMounted(async () => {
  try {
    const results = await client.experimentResults(experimentId);
    manifest.value = results.manifest;
    metrics.value = results.metrics;
    report.value = results.stability_report;
    hasReportHtml.value = await client.probeExperimentReport(experimentId);
    selectedFold.value = String(foldStatuses.value[0]?.fold_id ?? "");
    scenario.value = scenarios.value.includes("full_cost")
      ? "full_cost"
      : (scenarios.value[0] ?? "");
    loaded.value = true;
    await loadFoldSeries();
    bootstrapped = true;
  } catch (cause) {
    error.value = toDisplayError(cause);
  }
});
</script>

<template>
  <section>
    <h1>策略详情</h1>
    <p v-if="error !== null" class="error">
      错误 {{ error.code }}：{{ error.message === "" ? "无安全摘要" : error.message }}
    </p>
    <Skeleton v-else-if="!loaded" :rows="6" />
    <!-- 非 walk-forward 实验：显式缺失态，不渲染任何结论图表。 -->
    <Card v-else-if="report === null">
      <div data-testid="detail-empty">
        <EmptyState
          title="未发布 walk-forward 产物"
          description="该实验没有已发布的 stability_report——请看静态报告或 CLI 产物。"
        />
      </div>
    </Card>
    <template v-else>
      <Card testid="verdict-banner" title="结论">
        <p class="verdict-line">
          <StateBadge v-if="conclusion !== null" kind="conclusion" :value="conclusion" />
          <span v-else>无结论</span>
          <strong>{{ hypothesis ?? "（实验未声明假设）" }}</strong>
        </p>
        <p class="resolved">
          验收 {{ evaluationStatus ?? "—" }}<template v-if="evaluationReason !== null">：{{ evaluationReason }}</template>
          · 冻结政策 <code :title="policyHash ?? undefined">{{ short(policyHash, 8) }}</code>
          · 门槛 <code>{{ thresholdsSummary }}</code>
        </p>
      </Card>

      <!-- 指标卡逐字读当前情景聚合，只有 pct/num 格式化，无派生指标。 -->
      <section class="kpi-grid" data-testid="metric-cards" aria-label="当前情景聚合指标">
        <KpiCard label="聚合收益" :value="pct(aggregate('aggregate_return'))" :compare="oosCompare" />
        <KpiCard label="年化收益" :value="pct(aggregate('annualized_return'))" />
        <KpiCard label="年化波动" :value="pct(aggregate('annualized_volatility'))" />
        <KpiCard label="Sharpe（零无风险）" :value="num(aggregate('sharpe_zero_rf'))" />
      </section>

      <Card title="逐折证据">
        <div class="fold-controls">
          <label>折
            <select data-testid="fold-selector" v-model="selectedFold">
              <option
                v-for="(entry, index) in foldStatuses"
                :key="String(entry.fold_id)"
                :value="String(entry.fold_id)"
              >
                折 {{ index + 1 }}（{{ short(String(entry.fold_id), 8) }}）
              </option>
            </select>
          </label>
          <div class="scenario-tabs" data-testid="scenario-tabs" role="tablist" aria-label="成本情景">
            <button
              v-for="name in scenarios"
              :key="name"
              type="button"
              :class="{ 'tab-active': name === scenario }"
              @click="scenario = name"
            >
              {{ name }}
            </button>
          </div>
        </div>
        <p v-if="seriesError !== null" class="error">
          折序列加载失败 {{ seriesError.code }}：{{ seriesError.message === "" ? "无安全摘要" : seriesError.message }}
        </p>
        <LineChart
          testid="chart-net-value"
          :fold="selectedFold"
          :labels="equityDates"
          :series="netValueSeries"
        />
        <!-- ECharts 画在 canvas 上，series.name 不进 DOM：图例由页面渲染成元素，
             "基准同图"在 DOM 层才可验证、对读屏可见。 -->
        <ul class="chart-legend" data-testid="chart-net-value-legend">
          <li>策略（费后）</li>
          <li>基准 {{ benchmarkSymbol }}（虚线）</li>
        </ul>
        <LineChart
          testid="chart-underwater"
          :fold="selectedFold"
          :labels="equityDates"
          :series="underwaterSeries"
        />
        <BarChart
          testid="chart-fold-bars"
          color-by-sign
          :labels="foldBarLabels"
          :series="foldBarSeries"
        />
      </Card>

      <Card title="逐折指标">
        <DataTable
          testid="fold-table"
          row-testid="fold-row"
          row-key="fold_id"
          :rows="foldTableRows"
          :columns="FOLD_COLUMNS"
        >
          <template #fold_id="{ row }">
            <code :title="String(row.fold_id)">{{ String(row.fold_id).slice(0, 8) }}</code>
          </template>
        </DataTable>
      </Card>

      <Card title="静态报告">
        <p v-if="hasReportHtml === true">
          <a data-testid="report-link" :href="reportUrl" target="_blank" rel="noreferrer">查看静态报告</a>
          <span class="resolved">（摘要表：_DefaultReport，无图表）</span>
        </p>
        <p v-else-if="hasReportHtml === false" data-testid="report-link">无已发布报告</p>
      </Card>

      <p class="provenance" data-testid="provenance-strip">
        溯源：code_commit
        <code :title="manifestString('code_commit') ?? undefined">{{ short(manifestString('code_commit'), 12) }}</code>
        · 策略快照
        <code :title="manifestString('strategy_snapshot_sha256') ?? undefined">{{ short(manifestString('strategy_snapshot_sha256'), 12) }}</code>
        · 实验快照
        <code :title="manifestString('experiment_snapshot_sha256') ?? undefined">{{ short(manifestString('experiment_snapshot_sha256'), 12) }}</code>
        · 数据环境快照
        <code :title="manifestString('data_environment_snapshot_sha256') ?? undefined">{{ short(manifestString('data_environment_snapshot_sha256'), 12) }}</code>
        · 折调度
        <code :title="manifestString('fold_schedule_sha256') ?? undefined">{{ short(manifestString('fold_schedule_sha256'), 12) }}</code>
        · 折结果
        <code :title="manifestString('fold_outcomes_sha256') ?? undefined">{{ short(manifestString('fold_outcomes_sha256'), 12) }}</code>
        · 产物 {{ artifactCount }} 项
      </p>
    </template>
  </section>
</template>
