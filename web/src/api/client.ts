import { inject, provide, type InjectionKey } from "vue";
import type {
  DatasetDetailResponse,
  DatasetListResponse,
  ExperimentsResponse,
  HealthResponse,
  QualityListResponse,
  TablePreviewParams,
  TablePreviewResponse,
  UpdateConflictBody,
  UpdateJob,
  UpdateJobCreated,
  UpdateJobRequest,
  UpdateJobsResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly safeMessage: string,
    readonly body: unknown = null,
  ) {
    super(`[${status}] ${code}: ${safeMessage}`);
    this.name = "ApiError";
  }
}

export interface ApiClient {
  health(): Promise<HealthResponse>;
  listDatasets(): Promise<DatasetListResponse>;
  getDataset(version: string): Promise<DatasetDetailResponse>;
  listQualityIssues(
    version: string,
    offset: number,
    limit: number,
  ): Promise<QualityListResponse>;
  previewTable(
    version: string,
    table: string,
    params: TablePreviewParams,
  ): Promise<TablePreviewResponse>;
  listExperiments(): Promise<ExperimentsResponse>;
  probeExperimentReport(experimentId: string): Promise<boolean>;
  listUpdateJobs(): Promise<UpdateJobsResponse>;
  getUpdateJob(jobId: string): Promise<UpdateJob>;
  startUpdateJob(request: UpdateJobRequest): Promise<UpdateJobCreated>;
}

export const apiClientKey: InjectionKey<ApiClient> = Symbol("stock-web-api-client");

export function provideApiClient(client: ApiClient) {
  provide(apiClientKey, client);
}

export function useApiClient(): ApiClient {
  const client = inject(apiClientKey);
  if (!client) {
    throw new Error("api client not provided");
  }
  return client;
}

/**
 * pin I8：**所有**非 2xx 的错误体是**嵌套**的 `{error: {code, message?, ...}}`，
 * 不是扁平的 `{code, message}`。信封缺失或不可解析时退到 `http_<status>`。
 */
interface ErrorEnvelope {
  error?: { code?: unknown; message?: unknown };
}

export function createApiClient(
  options: { baseUrl?: string; fetchImpl?: typeof fetch } = {},
): ApiClient {
  const baseUrl = options.baseUrl ?? "";
  const doFetch = options.fetchImpl ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init));

  async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await doFetch(`${baseUrl}${path}`, init);
    if (!response.ok) {
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      const envelope = (body ?? {}) as ErrorEnvelope;
      const detail = envelope.error;
      const code =
        detail && typeof detail.code === "string"
          ? detail.code
          : `http_${response.status}`;
      const message =
        detail && typeof detail.message === "string" ? detail.message : "";
      throw new ApiError(response.status, code, message, body);
    }
    return (await response.json()) as T;
  }

  function query(params: Record<string, string | number | null>): string {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== null && value !== "") {
        search.set(key, String(value));
      }
    }
    const text = search.toString();
    return text === "" ? "" : `?${text}`;
  }

  return {
    health() {
      return requestJson<HealthResponse>("/api/v1/health");
    },
    listDatasets() {
      return requestJson<DatasetListResponse>("/api/v1/datasets");
    },
    getDataset(version: string) {
      return requestJson<DatasetDetailResponse>(`/api/v1/datasets/${version}`);
    },
    listQualityIssues(version: string, offset: number, limit: number) {
      return requestJson<QualityListResponse>(
        `/api/v1/datasets/${version}/quality${query({ offset, limit })}`,
      );
    },
    previewTable(version: string, table: string, params: TablePreviewParams) {
      return requestJson<TablePreviewResponse>(
        `/api/v1/datasets/${version}/tables/${encodeURIComponent(table)}${query({
          columns: params.columns === null ? null : params.columns.join(","),
          symbol: params.symbol,
          // pin I4：日期过滤是单值 trade_date，没有 date_start/date_end 区间。
          trade_date: params.trade_date,
          offset: params.offset,
          limit: params.limit,
        })}`,
      );
    },
    listExperiments() {
      return requestJson<ExperimentsResponse>("/api/v1/experiments");
    },
    async probeExperimentReport(experimentId: string) {
      const response = await doFetch(
        `${baseUrl}/api/v1/experiments/${encodeURIComponent(experimentId)}/report`,
      );
      return response.ok;
    },
    async listUpdateJobs() {
      return requestJson<UpdateJobsResponse>("/api/v1/update-jobs");
    },
    getUpdateJob(jobId: string) {
      return requestJson<UpdateJob>(`/api/v1/update-jobs/${encodeURIComponent(jobId)}`);
    },
    startUpdateJob(request: UpdateJobRequest) {
      // pin I11：201 只回 `{job_id, status}`，完整 job 详情要另取。
      return requestJson<UpdateJobCreated>("/api/v1/update-jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      });
    },
  };
}

/** §10.3 409 终态：从冲突错误体提取运行中 job id（pin I11：`job_id` 嵌在 `error` 里）。 */
export function conflictJobId(error: unknown): string | null {
  if (error instanceof ApiError && error.status === 409) {
    const body = error.body as { error?: UpdateConflictBody } | null;
    const detail = body === null || typeof body !== "object" ? null : body.error;
    if (detail && typeof detail.job_id === "string") {
      return detail.job_id;
    }
  }
  return null;
}
