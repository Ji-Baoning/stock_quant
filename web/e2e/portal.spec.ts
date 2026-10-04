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

/** S1 策略详情：/experiments/{id}/results 的 WF 产物形状（对齐真实产物的
     小写折状态枚举与情景聚合字段；与 tests/strategy-detail-page.spec.ts 夹具同源）。 */
function resultsBody() {
  const fold = "f".repeat(64);
  return {
    experiment_id: "exp-2026q3",
    manifest: {
      experiment_id: "exp-2026q3",
      status: "ACCEPTED",
      dataset_version: HASH_A,
      code_commit: "c".repeat(40),
      strategy_snapshot_sha256: "1".repeat(64),
      experiment_snapshot_sha256: "2".repeat(64),
      data_environment_snapshot_sha256: "3".repeat(64),
      stability_policy_hash: "p".repeat(16),
      fold_schedule_sha256: "s".repeat(64),
      fold_outcomes_sha256: "o".repeat(64),
      artifacts: { "folds/x": "0".repeat(64) },
    },
    metrics: {
      meta: { spec: { hypothesis: "动量延续假设" }, benchmark_symbols: ["000300.SH"] },
      evaluation: { status: "ACCEPTED", reason: "stability STABLE" },
    },
    stability_report: {
      stability_conclusion: "STABLE",
      thresholds: { policy: "stability-v1" },
      fold_statuses: [{ fold_id: fold, status: "executed", reason_code: null }],
      scenario_aggregates: [
        {
          scenario: "full_cost",
          aggregate_return: 0.12,
          annualized_return: 0.12,
          annualized_volatility: 0.18,
          sharpe_zero_rf: 1.4,
          oos_return_observations: 100,
          annualization_observations: 250,
        },
      ],
      fold_metrics: [
        {
          fold_id: fold,
          scenario: "full_cost",
          fold_calendar_return: 0.02,
          per_fold_max_drawdown: -0.03,
          sharpe_zero_rf: 1.1,
          explicit_cost_drag: 0.004,
          net_return: 0.016,
          reject_rate: 0.01,
          turnover: 0.3,
          first_trading_day: "2026-01-05",
          last_trading_day: "2026-03-05",
        },
      ],
    },
  };
}

test("策略列表与详情（S1）", async ({ page }) => {
  const mock = state({});
  await installMockBackend(page, mock);
  await page.route("**/api/v1/experiments/summaries", (route) =>
    json(route, 200, { summaries: [] }),
  );
  await page.goto("/#/strategies");
  await expect(page.getByTestId("strategy-empty")).toContainText("尚无已发布实验");
  // 导航分组：策略组在、报告组不在（裁定 7）。
  await expect(page.getByTestId("main-nav")).toContainText("策略列表");
  await expect(page.getByTestId("main-nav")).not.toContainText("报告");

  // 详情页四个产物端点：mock 后端尚不认识，显式补 route（注册在兜底之后，
  // Playwright 后注册者优先）。/report 探测走兜底里既有的 reports 映射。
  const fold = "f".repeat(64);
  const benchmarkSearches: string[] = [];
  await page.route("**/api/v1/experiments/exp-2026q3/results", (route) =>
    json(route, 200, resultsBody()),
  );
  await page.route("**/api/v1/experiments/exp-2026q3/challenges", (route) =>
    json(route, 200, { experiment_id: "exp-2026q3", challenges: [] }),
  );
  await page.route("**/api/v1/experiments/exp-2026q3/folds/*/equity*", (route) =>
    json(route, 200, {
      experiment_id: "exp-2026q3",
      fold_id: fold,
      scenario: "full_cost",
      rows: [
        { trade_date: "2026-01-05", net_equity_after_cost: 1_000_000 },
        { trade_date: "2026-01-06", net_equity_after_cost: 1_010_000 },
      ],
    }),
  );
  await page.route(`**/api/v1/datasets/${HASH_A}/benchmark*`, (route) => {
    benchmarkSearches.push(new URL(route.request().url()).search);
    return json(route, 200, {
      dataset_version: HASH_A,
      requested_version: HASH_A,
      symbol: "000300.SH",
      rows: [
        { trade_date: "2026-01-05", close: 4000 },
        { trade_date: "2026-01-06", close: 4020 },
      ],
    });
  });
  await page.goto("/#/strategies/exp-2026q3");
  await expect(page.getByTestId("verdict-banner")).toContainText("STABLE");
  // 逐折净值钉在当前折上（禁止跨折拼接的可测形式）；图例是 DOM 文本，
  // "基准同图"在 DOM 层可验证。基准窗口 = 该折首末交易日，不是全期。
  await expect(page.getByTestId("chart-net-value")).toHaveAttribute("data-fold", fold);
  await expect(page.getByTestId("chart-net-value-legend")).toContainText("基准 000300.SH");
  expect(
    benchmarkSearches.some(
      (search) => search.includes("start=2026-01-05") && search.includes("end=2026-03-05"),
    ),
  ).toBe(true);

  // 挑战裁决区块：详情页常驻只读区块（spec 2026-10-04 §4.1）。
  await expect(page.getByTestId("challenge-block")).toBeVisible();
  await expect(page.getByTestId("challenge-empty")).toContainText("本实验尚无挑战裁决");
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

/** S2 块②结论卡：与 tests/helpers.ts 的 WF_SUMMARY 同源的最小行。
     run_started_at: null 是现实默认（已发布工件没有运行时间字段），页面
     须显式标注"注册表序"而不是假装最新。 */
function summaryRow() {
  return {
    experiment_id: "e".repeat(64),
    status: "ACCEPTED",
    dataset_version: HASH_A,
    universe_version: "u".repeat(64),
    evaluation_reason: null,
    hypothesis: "动量延续假设：60 日动量在 CSI300 内有正超额",
    stability_conclusion: "STABLE",
    stability_policy_hash: "p".repeat(16),
    research_status: "COMPLETED",
    canonical_scenario: "full_cost",
    aggregates: [
      {
        scenario: "full_cost",
        aggregate_return: 0.12,
        annualized_return: 0.12,
        annualized_volatility: 0.18,
        sharpe_zero_rf: 1.4,
        oos_return_observations: 750,
        annualization_observations: 250,
      },
    ],
    display_extremes: { max_per_fold_drawdown: -0.08, max_reject_rate: 0.02, mean_turnover: 0.35 },
    run_started_at: null,
  };
}

test("决策台：三块首屏（门禁措辞/结论卡/待办跳转）", async ({ page }) => {
  const mock = state({ jobs: [seedJob("job-0001", "RUNNING")] });
  await installMockBackend(page, mock);
  // 块②读 /experiments/summaries（store 已切换），兜底路由不认识它。
  await page.route("**/api/v1/experiments/summaries", (route) =>
    json(route, 200, { summaries: [summaryRow()] }),
  );
  await page.goto("/");
  await expect(page.getByTestId("block-data-trust")).toContainText("门禁通过");
  await expect(page.getByTestId("block-data-trust")).toContainText("质量问题 1 条");
  // 结论卡：徽章原文 / 最新实验跳转 / 注册表序标注（不假装最新）/ 计数并入来源行。
  const strategy = page.getByTestId("block-strategy");
  await expect(strategy.getByTestId("state-badge")).toHaveText("STABLE");
  await expect(page.getByTestId("strategy-latest-link")).toHaveAttribute(
    "href",
    `#/strategies/${"e".repeat(64)}`,
  );
  await expect(strategy).toContainText("（注册表序，无运行时间）");
  await expect(strategy).toContainText("共 1 个已发布实验");
  await expect(page.getByTestId("block-actions")).toContainText("job-0001");
  await expect(page.getByTestId("main-nav").locator("a").first()).toHaveAttribute("href", "#/");
});

test("决策台：实验注册表为空时块②空态", async ({ page }) => {
  await installMockBackend(page, state());
  await page.route("**/api/v1/experiments/summaries", (route) =>
    json(route, 200, { summaries: [] }),
  );
  await page.goto("/");
  await expect(page.getByTestId("block-strategy-empty")).toContainText("尚无已发布实验");
});

// S3a 收口：注册台端到端最小覆盖。页面本身零 API 触点（mock 只喂顶栏的
// health/datasets 探测）；诚实边界与冻结命令在真实路由上渲染。
test("注册台（S3a）：诚实边界与冻结命令预览", async ({ page }) => {
  await installMockBackend(page, state());
  await page.goto("/#/strategies/register");
  await expect(page.getByTestId("honesty-note")).toContainText("web 不写文件");
  await expect(page.getByTestId("command-preview")).toContainText("--engineering");
});
