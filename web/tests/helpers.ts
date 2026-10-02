import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import type { Component } from "vue";
import { apiClientKey, type ApiClient } from "../src/api/client";
import { createPortalRouter } from "../src/router";
import type {
  DatasetDetailResponse,
  DatasetListResponse,
  ExperimentsResponse,
  HealthResponse,
  QualityListResponse,
  TablePreviewResponse,
  UpdateJob,
  UpdateJobSummary,
} from "../src/api/types";

export function fakeClient(overrides: Partial<ApiClient> = {}): ApiClient {
  const unstubbed = async (): Promise<never> => {
    throw new Error("fake client: endpoint not stubbed");
  };
  const base: ApiClient = {
    health: unstubbed,
    listDatasets: unstubbed,
    getDataset: unstubbed,
    listQualityIssues: unstubbed,
    previewTable: unstubbed,
    listExperiments: unstubbed,
    probeExperimentReport: unstubbed,
    listUpdateJobs: unstubbed,
    getUpdateJob: unstubbed,
    startUpdateJob: unstubbed,
  };
  return Object.assign(base, overrides);
}

export function mountPage(component: Component, client: ApiClient): VueWrapper {
  return mount(component, {
    global: {
      plugins: [createPortalRouter()],
      provide: { [apiClientKey as symbol]: client },
    },
  });
}

export async function mountAt(
  component: Component,
  client: ApiClient,
  path: string,
): Promise<VueWrapper> {
  const router = createPortalRouter();
  await router.push(path);
  return mount(component, {
    global: { plugins: [router], provide: { [apiClientKey as symbol]: client } },
  });
}

export { flushPromises };

/** 假哈希（非真实数据；凭据零容忍）。 */
export const HASH_A = "a".repeat(64);
export const HASH_B = "b".repeat(64);

export function healthResponse(): HealthResponse {
  // 16 位十六进制，与 P3 `project_root_fingerprint` 的形态一致（pin I7）。
  return { status: "ok", project_root_fingerprint: "0123456789abcdef" };
}

export function datasetListResponse(
  current: string = HASH_A,
  versions: string[] = [HASH_A, HASH_B],
): DatasetListResponse {
  return {
    current,
    datasets: versions.map((version) => ({
      dataset_version: version,
      is_current: version === current,
      created_at: "2026-10-01T08:00:00+08:00",
      table_count: 4,
      quality: {
        by_severity: (version === HASH_A ? {} : { FATAL: 1, WARNING: 1 }) as Record<
          string,
          number
        >,
      },
      acceptance: {
        state: version === HASH_A ? "ACCEPTED" : "UNVERIFIED",
        has_valid_accepted_record: version === HASH_A,
        latest_verdict: version === HASH_A ? "ACCEPTED" : null,
        record_count: version === HASH_A ? 1 : 0,
      },
    })),
  };
}

/** 覆盖段在 manifest 里（pin I2），不在响应顶层。 */
function buildConfig(): Record<string, unknown> {
  return {
    baseline_version: null,
    table_fetch_coverage: {
      daily_bar: [
        {
          table: "daily_bar",
          kind: "fetched",
          window_start: "2026-09-01",
          window_end: "2026-09-30",
        },
      ],
      basic_factor: [
        {
          table: "basic_factor",
          kind: "carried",
          window_start: "2026-09-01",
          window_end: "2026-09-29",
        },
        {
          table: "basic_factor",
          kind: "not_fetched",
          window_start: "2026-09-30",
          window_end: "2026-09-30",
          // reason 非空时才出现；None 时整个键省略（fetch_coverage.py）。
          reason: "source_unavailable",
        },
      ],
    },
  };
}

export function datasetDetailResponse(version: string = HASH_A): DatasetDetailResponse {
  return {
    dataset_version: version,
    requested_version: version,
    manifest: { build_config: buildConfig() },
    tables: [
      { name: "daily_bar", row_count: 120, schema_version: "1" },
      { name: "basic_factor", row_count: 118, schema_version: "1" },
      { name: "basic_factor_coverage", row_count: 4, schema_version: "1" },
      { name: "daily_bar_coverage", row_count: 2, schema_version: "1" },
    ],
    quality: { by_severity: { WARNING: 1 } },
    acceptance: {
      state: "ACCEPTED",
      has_valid_accepted_record: true,
      latest_verdict: "REJECTED",
      record_count: 2,
    },
  };
}

export function qualityListResponse(version: string = HASH_A): QualityListResponse {
  return {
    dataset_version: version,
    requested_version: version,
    offset: 0,
    limit: 100,
    total: 2,
    issues: [
      {
        severity: "FATAL",
        code: "fetch_coverage_gap",
        table: "basic_factor",
        symbol: null,
        trade_date: "2026-09-30",
        details: { gap_start: "2026-09-30" },
      },
      {
        severity: "WARNING",
        code: "schema_mismatch",
        table: null,
        symbol: null,
        trade_date: null,
        details: null,
      },
    ],
  };
}

export function previewResponse(
  version: string = HASH_A,
  offset: number = 0,
  rowCount: number = 100,
): TablePreviewResponse {
  return {
    dataset_version: version,
    // 对账修正：P3 `TablePreviewResponse` 有顶层 `requested_version`（提交
    // 151286e86 契约测试补的），计划的 types.ts 漏了它。
    requested_version: version,
    table: "daily_bar",
    arguments: {
      requested_version: version,
      table: "daily_bar",
      columns: ["trade_date", "symbol", "close"],
      symbol: null,
      trade_date: null,
      offset,
      limit: 100,
    },
    columns: ["trade_date", "symbol", "close"],
    rows: Array.from({ length: rowCount }, (_, index) => ({
      trade_date: `2026-09-${String(((offset + index) % 30) + 1).padStart(2, "0")}`,
      symbol: "000001.SZ",
      close: offset + index,
    })),
  };
}

export function coveragePreviewResponse(version: string = HASH_A): TablePreviewResponse {
  return {
    dataset_version: version,
    // 对账修正：同 previewResponse，顶层 `requested_version`。
    requested_version: version,
    table: "corporate_action_coverage",
    arguments: {
      requested_version: version,
      table: "corporate_action_coverage",
      columns: ["trade_date", "symbol", "status", "reason"],
      symbol: null,
      trade_date: null,
      offset: 0,
      limit: 500,
    },
    columns: ["trade_date", "symbol", "status", "reason"],
    rows: [
      { trade_date: "2026-09-28", symbol: "600000.SH", status: "UNTRUSTED", reason: "FACTS_INCOMPLETE" },
      { trade_date: "2026-09-29", symbol: "600000.SH", status: "VERIFIED", reason: "" },
    ],
  };
}

export function verifiedOnlyCoveragePreview(version: string = HASH_A): TablePreviewResponse {
  const response = coveragePreviewResponse(version);
  return { ...response, rows: response.rows.filter((row) => row.status !== "UNTRUSTED") };
}

export function updateJobSummary(overrides: Partial<UpdateJobSummary> = {}): UpdateJobSummary {
  return {
    job_id: "job-0001",
    status: "RUNNING",
    created_at: "2026-10-01T08:00:00+08:00",
    updated_at: "2026-10-01T08:01:00+08:00",
    run_id: null,
    dataset_version: null,
    ...overrides,
  };
}

export function updateJob(overrides: Partial<UpdateJob> = {}): UpdateJob {
  return {
    ...updateJobSummary(),
    pid: 4321,
    boot_id: "2fdf3cf0-97bf-4896-bd6f-1a5b8841637d",
    heartbeat_at: "2026-10-01T08:01:00+08:00",
    exit_code: null,
    failure_reason: null,
    failure_detail: null,
    // 已脱敏的字符串（pin I10），不是 string[]。
    stdout_tail: "2026-10-01T08:00:01+08:00 update started",
    stderr_tail: "",
    ...overrides,
  };
}

export function experimentsResponse(): ExperimentsResponse {
  // pin I5：ExperimentSummary 的五个字段一个都不少（可空字段显式给 null）。
  return {
    experiments: [
      {
        experiment_id: "exp-2026q3",
        status: "SUCCEEDED",
        dataset_version: HASH_A,
        universe_version: "tw",
        evaluation_reason: null,
      },
      {
        experiment_id: "exp-2026q2",
        status: null,
        dataset_version: HASH_B,
        universe_version: null,
        evaluation_reason: "no_report",
      },
    ],
  };
}
