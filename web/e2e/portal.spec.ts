import { expect, test, type Page, type Route } from "@playwright/test";
import type { UpdateJobStatus } from "../src/api/types";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

interface MockJob {
  job_id: string;
  status: UpdateJobStatus;
  created_at: string;
  updated_at: string;
  pid: number | null;
  boot_id: string | null;
  heartbeat_at: string | null;
  exit_code: number | null;
  failure_reason: string | null;
  failure_detail: string | null;
  stdout_tail: string;
  stderr_tail: string;
  run_id: string | null;
  dataset_version: string | null;
}

interface MockState {
  current: string;
  operationsEnabled: boolean;
  jobs: MockJob[];
  postCount: number;
  conflictOnce: boolean;
  terminal: "SUCCEEDED" | "FAILED" | "CANCELLED_BY_SHUTDOWN";
  requests: { method: string; path: string }[];
  reports: Record<string, boolean>;
}

function state(overrides: Partial<MockState> = {}): MockState {
  return {
    current: HASH_A,
    operationsEnabled: true,
    jobs: [],
    postCount: 0,
    conflictOnce: false,
    terminal: "SUCCEEDED",
    requests: [],
    reports: { "exp-2026q3": true, "exp-2026q2": false },
    ...overrides,
  };
}

function seedJob(jobId: string, status: UpdateJobStatus): MockJob {
  return {
    job_id: jobId,
    status,
    created_at: "2026-10-01T07:00:00+08:00",
    updated_at: "2026-10-01T07:01:00+08:00",
    pid: 4321,
    boot_id: "2fdf3cf0-97bf-4896-bd6f-1a5b8841637d",
    heartbeat_at: "2026-10-01T07:01:00+08:00",
    exit_code: null,
    failure_reason: null,
    failure_detail: null,
    stdout_tail: "2026-10-01T07:00:01+08:00 update started",
    stderr_tail: "",
    run_id: null,
    dataset_version: null,
  };
}

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function detailBody() {
  // 与 P3 的 DatasetDetailResponse 逐字段同形：tables 是对象数组、覆盖段在
  // manifest.build_config 里、没有 is_current/published_at_evidence/quality_summary。
  return {
    dataset_version: HASH_A,
    requested_version: HASH_A,
    manifest: {
      build_config: {
        table_fetch_coverage: {
          daily_bar: [
            { table: "daily_bar", kind: "fetched", window_start: "2026-09-01", window_end: "2026-09-30" },
          ],
        },
      },
    },
    tables: [
      { name: "daily_bar", row_count: 120, schema_version: "1" },
      { name: "daily_bar_coverage", row_count: 2, schema_version: "1" },
    ],
    quality: { by_severity: { FATAL: 1 } },
    acceptance: {
      state: "ACCEPTED",
      has_valid_accepted_record: true,
      latest_verdict: "REJECTED",
      record_count: 2,
    },
  };
}

async function installMockBackend(page: Page, mock: MockState) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    mock.requests.push({ method: request.method(), path });

    if (path === "/api/v1/health") {
      return json(route, 200, { status: "ok", project_root_fingerprint: "0123456789abcdef" });
    }
    if (path === "/api/v1/datasets") {
      return json(route, 200, {
        current: mock.current,
        datasets: [HASH_A, HASH_B].map((version) => ({
          dataset_version: version,
          is_current: version === mock.current,
          created_at: "2026-10-01T08:00:00+08:00",
          table_count: 2,
          quality: { by_severity: version === HASH_A ? { FATAL: 1 } : {} },
          acceptance:
            version === HASH_A
              ? { state: "ACCEPTED", has_valid_accepted_record: true, latest_verdict: "ACCEPTED", record_count: 1 }
              : { state: "UNVERIFIED", has_valid_accepted_record: false, latest_verdict: null, record_count: 0 },
        })),
      });
    }
    if (path === "/api/v1/datasets/current" || path === `/api/v1/datasets/${HASH_A}`) {
      return json(route, 200, detailBody());
    }
    if (path === `/api/v1/datasets/${HASH_A}/quality`) {
      return json(route, 200, {
        dataset_version: HASH_A,
        requested_version: HASH_A,
        offset: 0,
        limit: 100,
        total: 1,
        issues: [
          {
            severity: "FATAL",
            code: "fetch_coverage_gap",
            table: "basic_factor",
            symbol: null,
            trade_date: "2026-09-30",
            details: { gap_start: "2026-09-30" },
          },
        ],
      });
    }
    if (path.startsWith(`/api/v1/datasets/${HASH_A}/tables/`)) {
      return json(route, 200, {
        dataset_version: HASH_A,
        table: "daily_bar",
        arguments: {
          requested_version: HASH_A,
          table: "daily_bar",
          columns: ["trade_date", "symbol", "close"],
          symbol: null,
          trade_date: null,
          offset: 0,
          limit: 100,
        },
        columns: ["trade_date", "symbol", "close"],
        rows: Array.from({ length: 100 }, (_, index) => ({
          trade_date: "2026-09-28",
          symbol: "000001.SZ",
          close: index,
        })),
      });
    }
    if (path === "/api/v1/experiments") {
      return json(route, 200, {
        experiments: [
          { experiment_id: "exp-2026q3", status: "SUCCEEDED", dataset_version: HASH_A, universe_version: "tw", evaluation_reason: null },
          { experiment_id: "exp-2026q2", status: null, dataset_version: HASH_A, universe_version: null, evaluation_reason: null },
        ],
      });
    }
    const report = /^\/api\/v1\/experiments\/([^/]+)\/report$/.exec(path);
    if (report) {
      if (mock.reports[report[1]!] === true) {
        return route.fulfill({
          status: 200,
          contentType: "text/html",
          body: "<html><body>static report</body></html>",
        });
      }
      return json(route, 404, { error: { code: "report_not_found", message: "该实验尚无已生成的静态报告" } });
    }
    if (path === "/api/v1/update-jobs" && request.method() === "GET") {
      if (!mock.operationsEnabled) {
        // pin I9：禁用是 503 operations_disabled，不是 404。
        return json(route, 503, { error: { code: "operations_disabled" } });
      }
      // pin I10：列表外层是对象。
      return json(route, 200, { jobs: mock.jobs.map(toSummary) });
    }
    if (path === "/api/v1/update-jobs" && request.method() === "POST") {
      mock.postCount += 1;
      if (mock.conflictOnce) {
        mock.conflictOnce = false;
        // pin I11：job_id 嵌在 error 里。
        return json(route, 409, {
          error: { code: "update_already_running", job_id: mock.jobs[0]!.job_id },
        });
      }
      const job = seedJob(`job-${mock.postCount}`, "QUEUED");
      mock.jobs.unshift(job);
      // pin I11：201 只回两个字段，详情要另取。
      return json(route, 201, { job_id: job.job_id, status: job.status });
    }
    const jobMatch = /^\/api\/v1\/update-jobs\/(.+)$/.exec(path);
    if (jobMatch) {
      const job = mock.jobs.find((item) => item.job_id === jobMatch[1]);
      if (job === undefined) {
        return json(route, 404, { error: { code: "job_not_found", job_id: jobMatch[1] } });
      }
      if (job.status === "QUEUED") {
        job.status = "RUNNING";
      } else if (job.status === "RUNNING") {
        job.status = mock.terminal;
        job.updated_at = "2026-10-01T08:05:00+08:00";
        job.stdout_tail = `${job.stdout_tail}\n2026-10-01T08:04:59+08:00 update finished`;
        if (mock.terminal === "SUCCEEDED") {
          job.run_id = "run-e2e";
          job.dataset_version = HASH_B;
          mock.current = HASH_B;
        } else if (mock.terminal === "FAILED") {
          job.failure_reason = "missing_registered_table";
          job.failure_detail = "缺表 daily_bar";
        }
      }
      return json(route, 200, job);
    }
    return json(route, 404, { error: { code: "route_not_found", path } });
  });
}

/** pin I10：列表只回摘要六字段。 */
function toSummary(job: MockJob) {
  return {
    job_id: job.job_id,
    status: job.status,
    created_at: job.created_at,
    updated_at: job.updated_at,
    run_id: job.run_id,
    dataset_version: job.dataset_version,
  };
}

test("§10.4 版本切换提示 + 分页保持版本", async ({ page }) => {
  const mock = state();
  await installMockBackend(page, mock);
  await page.goto("/#/preview");
  await expect(page.getByTestId("preview-table")).toBeVisible();
  const tableRequests = () =>
    mock.requests.filter((item) => item.path.startsWith(`/api/v1/datasets/${HASH_A}/tables/`));
  expect(tableRequests().length).toBeGreaterThan(0);

  await page.getByTestId("next-page").click();
  await expect(page.getByTestId("preview-table")).toBeVisible();
  expect(tableRequests().length).toBe(2);

  mock.current = HASH_B;
  await page.getByTestId("refresh-current").click();
  await expect(page.getByTestId("new-version-hint")).toBeVisible();
  await expect(page.getByTestId("resolved-version")).toContainText(HASH_A);
  expect(
    tableRequests().every((item) => item.path.startsWith(`/api/v1/datasets/${HASH_A}/tables/`)),
  ).toBe(true);
});

test("§10.4 UNVERIFIED 展示（版本面板）", async ({ page }) => {
  const mock = state();
  await installMockBackend(page, mock);
  await page.goto("/#/versions");
  const rows = page.getByTestId("dataset-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(1).getByTestId("latest-verdict")).toContainText("UNVERIFIED");
  await expect(rows.nth(1).getByTestId("accepted-record")).toHaveText("否");
  await expect(rows.nth(0).getByTestId("accepted-record")).toHaveText("是");
  await expect(rows.nth(0).getByTestId("current-mark")).toBeVisible();
});

test("§10.4 验收四态分栏显示（版本详情：有效 accepted record 与最近 verdict 分开）", async ({ page }) => {
  await installMockBackend(page, state());
  await page.goto("/#/versions/current");
  await expect(page.getByTestId("accepted-record-block")).toContainText("存在");
  await expect(page.getByTestId("latest-verdict-block")).toContainText("REJECTED");
  await expect(page.getByTestId("full-version")).toHaveText(HASH_A);
});

test("§10.4 更新 409：显示'已有任务运行中'并链接该 job", async ({ page }) => {
  const mock = state({ conflictOnce: true });
  mock.jobs.push(seedJob("job-0", "RUNNING"));
  await installMockBackend(page, mock);
  await page.goto("/#/jobs");
  await expect(page.getByTestId("update-form")).toBeVisible();
  await page.getByTestId("submit-update").click();
  await expect(page.getByTestId("conflict")).toBeVisible();
  await expect(page.getByTestId("conflict")).toContainText("job-0");
  await expect(page.getByTestId("job-detail")).toContainText("job-0");
});

test("§10.4 更新失败日志：稳定失败原因 + 脱敏日志尾部", async ({ page }) => {
  const mock = state({ terminal: "FAILED" });
  await installMockBackend(page, mock);
  await page.goto("/#/jobs");
  await page.getByTestId("submit-update").click();
  await expect(page.getByTestId("job-failed")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("job-failure-reason")).toHaveText("missing_registered_table");
  await expect(page.getByTestId("job-failed")).toContainText("缺表 daily_bar");
  await expect(page.getByTestId("job-stdout")).toContainText("update finished");
});

test("§10.4 操作面关闭：只读 + 等价 CLI 命令", async ({ page }) => {
  await installMockBackend(page, state({ operationsEnabled: false }));
  await page.goto("/#/jobs");
  await expect(page.getByTestId("operations-disabled")).toBeVisible();
  await expect(page.getByTestId("equivalent-cli")).toContainText(
    "python -m stock_quant operations update --root",
  );
  await expect(page.getByTestId("update-form")).toHaveCount(0);
});

test("§10.4 报告不存在：缺失态与既有链接并存", async ({ page }) => {
  await installMockBackend(page, state());
  await page.goto("/#/reports");
  const rows = page.getByTestId("experiment-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0).getByTestId("report-link")).toHaveAttribute(
    "href",
    "/api/v1/experiments/exp-2026q3/report",
  );
  await expect(rows.nth(1).getByTestId("report-missing")).toContainText("报告不存在");
});

test("§10.4 E2E 主流程：触发更新 → 看到新版本；成功终态显示完整 dataset version 并链接版本详情；不生成 acceptance PASS、不自动运行研究", async ({ page }) => {
  const mock = state({ terminal: "SUCCEEDED" });
  await installMockBackend(page, mock);
  await page.goto("/#/jobs");
  await page.getByTestId("submit-update").click();
  await expect(page.getByTestId("job-succeeded")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("job-run-id")).toHaveText("run-e2e");
  await expect(page.getByTestId("job-dataset-version")).toHaveText(HASH_B);
  await expect(page.getByTestId("job-version-link")).toHaveAttribute("href", `#/versions/${HASH_B}`);

  await page.getByRole("link", { name: "版本面板" }).click();
  await expect(page.getByTestId("dataset-list")).toBeVisible();
  const newRow = page.getByTestId("dataset-row").filter({ hasText: HASH_B });
  await expect(newRow.getByTestId("current-mark")).toBeVisible();

  // 不生成 acceptance PASS、不自动运行研究：除一次 update POST 外全部只读 GET；
  // 门户不发起任何 acceptance/research 请求（§14 第 6 条在交互层的表达）。
  expect(mock.postCount).toBe(1);
  expect(mock.requests.filter((item) => item.method !== "GET")).toEqual([
    { method: "POST", path: "/api/v1/update-jobs" },
  ]);
  expect(
    mock.requests.some(
      (item) => item.path.includes("acceptance") || item.path.includes("research"),
    ),
  ).toBe(false);
});
