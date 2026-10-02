import { describe, expect, it, vi } from "vitest";
import { ApiError, conflictJobId, createApiClient } from "../src/api/client";
import { HASH_A, HASH_B, datasetDetailResponse, previewResponse, updateJob } from "./helpers";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("api client（pin I1–I11）", () => {
  it("GET /api/v1/datasets 返回版本列表并解析 acceptance 字段", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, {
      current: HASH_A,
      datasets: [
        {
          dataset_version: HASH_A,
          is_current: true,
          created_at: "2026-10-01T08:00:00+08:00",
          table_count: 1,
          quality: { by_severity: {} },
          acceptance: {
            state: "ACCEPTED",
            has_valid_accepted_record: true,
            latest_verdict: "ACCEPTED",
            record_count: 1,
          },
        },
      ],
    }));
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const response = await client.listDatasets();
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/datasets", undefined);
    expect(response.current).toBe(HASH_A);
    expect(response.datasets[0].acceptance.latest_verdict).toBe("ACCEPTED");
  });

  it("请求 current 别名；dataset_version 回显永远是完整哈希", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, datasetDetailResponse()));
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const detail = await client.getDataset("current");
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/datasets/current", undefined);
    expect(detail.dataset_version).toBe(HASH_A);
  });

  it("表预览按 pin I4 组装查询串（单值 trade_date，无区间），并回显请求参数", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, previewResponse()));
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    await client.previewTable(HASH_A, "daily_bar", {
      columns: ["trade_date", "close"],
      trade_date: "2026-09-30",
      symbol: "000001.SZ",
      offset: 100,
      limit: 100,
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      `/api/v1/datasets/${HASH_A}/tables/daily_bar?columns=trade_date%2Cclose&symbol=000001.SZ&trade_date=2026-09-30&offset=100&limit=100`,
      undefined,
    );
  });

  it("非 2xx 抛 ApiError：优先取信封稳定码，缺失时用 http_<status>", async () => {
    const withEnvelope = createApiClient({
      fetchImpl: vi.fn(async () =>
        jsonResponse(400, { error: { code: "query_time_budget_exceeded", message: "查询超时" } }),
      ) as unknown as typeof fetch,
    });
    await expect(withEnvelope.previewTable(HASH_A, "daily_bar", {
      columns: null, trade_date: null, symbol: null, offset: 0, limit: 100,
    })).rejects.toMatchObject({ status: 400, code: "query_time_budget_exceeded", safeMessage: "查询超时" });

    const bare = createApiClient({
      fetchImpl: vi.fn(async () => jsonResponse(500, "oops")) as unknown as typeof fetch,
    });
    await expect(bare.listDatasets()).rejects.toBeInstanceOf(ApiError);
    await expect(bare.listDatasets()).rejects.toMatchObject({ code: "http_500" });
  });

  it("409 冲突：conflictJobId 提取运行中 job id（pin I11，job_id 嵌在 error 里）", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(409, { error: { code: "update_already_running", job_id: "job-0001" } }),
    );
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const attempt = client.startUpdateJob({
      start: null, end: null, sources: null, disclosure_lookback_days: null,
    });
    await expect(attempt).rejects.toBeInstanceOf(ApiError);
    try {
      await client.startUpdateJob({ start: null, end: null, sources: null, disclosure_lookback_days: null });
    } catch (error) {
      expect(conflictJobId(error)).toBe("job-0001");
    }
    expect(conflictJobId(new Error("x"))).toBeNull();
  });

  it("POST /api/v1/update-jobs 携带 JSON 请求体；GET job 返回单对象", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/update-jobs" && init?.method === "POST") {
        expect(init.headers).toMatchObject({ "content-type": "application/json" });
        expect(JSON.parse(String(init.body))).toEqual({
          start: null, end: null, sources: null, disclosure_lookback_days: null,
        });
        // pin I11：201 只回 `{job_id, status}`，不是完整 job 对象。
        return jsonResponse(201, { job_id: "job-0002", status: "QUEUED" });
      }
      return jsonResponse(200, updateJob({ job_id: "job-0002" }));
    });
    const client = createApiClient({ fetchImpl: fetchImpl as unknown as typeof fetch });
    const created = await client.startUpdateJob({ start: null, end: null, sources: null, disclosure_lookback_days: null });
    expect(created.status).toBe("QUEUED");
    const fetched = await client.getUpdateJob("job-0002");
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/update-jobs/job-0002", undefined);
    expect(fetched.job_id).toBe("job-0002");
  });

  it("报告探测 200/404（pin I6）", async () => {
    const ok = createApiClient({
      fetchImpl: vi.fn(async () => jsonResponse(200, "<html></html>")) as unknown as typeof fetch,
    });
    const missing = createApiClient({
      fetchImpl: vi.fn(async () =>
        jsonResponse(404, { error: { code: "report_not_found", message: "" } }),
      ) as unknown as typeof fetch,
    });
    expect(await ok.probeExperimentReport("exp-1")).toBe(true);
    expect(await missing.probeExperimentReport("exp-2")).toBe(false);
  });

  it("操作面 503 信号可被上层用 ApiError.status 判别（pin I9）", async () => {
    const disabled = createApiClient({
      fetchImpl: vi.fn(async () =>
        jsonResponse(503, { error: { code: "operations_disabled" } }),
      ) as unknown as typeof fetch,
    });
    try {
      await disabled.listUpdateJobs();
      expect.unreachable("listUpdateJobs should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(503);
      expect((error as ApiError).code).toBe("operations_disabled");
    }
  });
});
