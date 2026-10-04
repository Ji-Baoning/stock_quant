// web/tests/strategy-detail-page.spec.ts —— Task 8：策略详情页（tearsheet）。
// 夹具按真实产物 74caa956… 的形状：折状态是小写枚举（schedule.py
// FoldOutcomeStatus：executed/failed_preflight），WF manifest 携带
// fold_schedule_sha256/fold_outcomes_sha256，情景名小写（zero_cost/full_cost）。
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import type { ApiClient } from "../src/api/client";
import type {
  BenchmarkResponse,
  ExperimentResultsResponse,
  FoldEquityResponse,
} from "../src/api/types";
import StrategyDetailPage from "../src/pages/StrategyDetailPage.vue";
import { fakeClient, mountAt } from "./helpers";

const EX = "e".repeat(64);
const FOLD = "f".repeat(64);
const SECOND_FOLD = "b".repeat(64);
const SKIPPED_FOLD = "9".repeat(64);

function resultsPayload(): ExperimentResultsResponse {
  return {
    experiment_id: EX,
    manifest: {
      experiment_id: EX,
      status: "ACCEPTED",
      dataset_version: "a".repeat(64),
      universe_version: "u".repeat(64),
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
      meta: {
        spec: { hypothesis: "动量延续假设", cost_scenarios: ["zero_cost", "full_cost"] },
        benchmark_symbols: ["000300.SH"],
      },
      walk_forward: { stability_conclusion: "STABLE", stability_policy_hash: "p".repeat(16) },
      evaluation: { status: "ACCEPTED", reason: "stability STABLE" },
    },
    stability_report: {
      stability_conclusion: "STABLE",
      reasons: [],
      thresholds: { policy: "stability-v1" },
      // 对账修正：真实产物折状态是小写；failed_preflight 折不进折选择器。
      fold_statuses: [
        { fold_id: FOLD, status: "executed", reason_code: null },
        { fold_id: SKIPPED_FOLD, status: "failed_preflight", reason_code: "universe_empty" },
      ],
      scenario_aggregates: [
        { scenario: "full_cost", aggregate_return: 0.12, annualized_return: 0.12, annualized_volatility: 0.18, sharpe_zero_rf: 1.4, oos_return_observations: 100, annualization_observations: 250 },
      ],
      fold_metrics: [
        { fold_id: FOLD, scenario: "full_cost", fold_calendar_return: 0.02, per_fold_max_drawdown: -0.03, sharpe_zero_rf: 1.1, explicit_cost_drag: 0.004, net_return: 0.016, reject_rate: 0.01, turnover: 0.3, first_trading_day: "2026-01-05", last_trading_day: "2026-03-05" },
      ],
    },
  };
}

function benchmarkResponse(): BenchmarkResponse {
  return {
    dataset_version: "a".repeat(64),
    requested_version: "a".repeat(64),
    symbol: "000300.SH",
    rows: [
      { trade_date: "2026-01-05", close: 4000.0 },
      { trade_date: "2026-01-06", close: 4020.0 },
    ],
  };
}

function detailClient(overrides: Partial<ApiClient> = {}): ApiClient {
  return fakeClient({
    experimentResults: async () => resultsPayload(),
    foldEquity: async () => ({
      experiment_id: EX,
      fold_id: FOLD,
      scenario: "full_cost",
      rows: [
        { trade_date: "2026-01-05", net_equity_after_cost: 1_000_000 },
        { trade_date: "2026-01-06", net_equity_after_cost: 1_010_000 },
      ],
    }),
    datasetBenchmark: async () => benchmarkResponse(),
    probeExperimentReport: async () => true,
    ...overrides,
  } as ApiClient);
}

describe("策略详情（tearsheet 骨架 + 仓库纪律）", () => {
  it("结论横幅第一屏：STABLE 徽章 + 政策哈希 + 假设", async () => {
    const wrapper = await mountAt(StrategyDetailPage, detailClient(), `/strategies/${EX}`);
    await flushPromises();
    const banner = wrapper.get('[data-testid="verdict-banner"]');
    expect(banner.get('[data-testid="state-badge"]').text()).toBe("STABLE");
    expect(banner.text()).toContain("动量延续假设");
    expect(banner.text()).toContain("p".repeat(16).slice(0, 8));
  });

  it("逐折证据：图表 data-fold 等于当前选中折（禁止拼接的可测形式）", async () => {
    // 基准同图：基准端点被请求过（窗口 = 折首末交易日，来自 fold_metrics 行）。
    const benchmarkCalls: Array<{ start?: string; end?: string }> = [];
    const client = detailClient({
      datasetBenchmark: async (_version, params) => {
        benchmarkCalls.push(params);
        return benchmarkResponse();
      },
    });
    const wrapper = await mountAt(StrategyDetailPage, client, `/strategies/${EX}`);
    await flushPromises();
    const chart = wrapper.get('[data-testid="chart-net-value"]');
    expect(chart.attributes("data-fold")).toBe(FOLD);
    expect(benchmarkCalls.some((call) => call.start === "2026-01-05" && call.end === "2026-03-05")).toBe(true);
    // 基准同图：图例是 DOM 文本而非 canvas 像素——ECharts 是 canvas 渲染，
    // series.name 不会出现在 wrapper.text() 里，所以图例必须由页面自己渲染
    // 成元素。
    expect(wrapper.get('[data-testid="chart-net-value-legend"]').text()).toContain(
      "基准 000300.SH",
    );
  });

  it("bootstrap 次序：初取在途时换折 → watch 立即请求新折（data-fold 不说谎）", async () => {
    // 竞态：bootstrapped 必须先于初取 await 置位。若置位在 await 之后，
    // 初取在途时换折会被 watch 守卫吞掉——foldCalls 里永远不会有新折，
    // 完成的初取会把旧折数据填进新折的 data-fold 图表。
    const payload = resultsPayload();
    const report = payload.stability_report as Record<string, unknown>;
    report.fold_statuses = [
      { fold_id: FOLD, status: "executed", reason_code: null },
      { fold_id: SECOND_FOLD, status: "executed", reason_code: null },
    ];
    const foldCalls: string[] = [];
    let resolveBootstrap!: (value: FoldEquityResponse) => void;
    const client = detailClient({
      experimentResults: async () => payload,
      foldEquity: async (_experimentId, foldId, foldScenario) => {
        foldCalls.push(foldId);
        if (foldCalls.length === 1) {
          // 初取挂在手动 promise 上，模拟慢响应，留出换折窗口。
          return new Promise<FoldEquityResponse>((resolve) => {
            resolveBootstrap = resolve;
          });
        }
        return {
          experiment_id: EX,
          fold_id: foldId,
          scenario: foldScenario,
          rows: [
            { trade_date: "2026-01-05", net_equity_after_cost: 1_000_000 },
            { trade_date: "2026-01-06", net_equity_after_cost: 1_010_000 },
          ],
        };
      },
    });
    const wrapper = await mountAt(StrategyDetailPage, client, `/strategies/${EX}`);
    await flushPromises(); // 初取仍挂起：loaded 已 true，选择器已渲染。
    expect(wrapper.find('[data-testid="fold-selector"]').exists()).toBe(true);
    await wrapper.find('[data-testid="fold-selector"]').setValue(SECOND_FOLD);
    resolveBootstrap({
      experiment_id: EX,
      fold_id: FOLD,
      scenario: "full_cost",
      rows: [{ trade_date: "2026-01-05", net_equity_after_cost: 1_000_000 }],
    });
    await flushPromises();
    // 调用顺序：bootstrap 调用（旧折）在前，watch 触发的调用（新折）在后；
    // 过期的初取响应被 seriesToken 丢弃，落盘的是新折数据。
    expect(foldCalls).toEqual([FOLD, SECOND_FOLD]);
    expect(wrapper.get('[data-testid="chart-net-value"]').attributes("data-fold")).toBe(
      SECOND_FOLD,
    );
  });

  it("基准符号保真：datasetBenchmark 请求携带图例的符号并逐字渲染", async () => {
    // 图例画 metrics.meta.benchmark_symbols[0]；请求不带 symbol 时后端回
    // 默认 000300.SH——会把另一个指数画在它的名字下面。
    const payload = resultsPayload();
    const meta = (payload.metrics as Record<string, unknown>).meta as Record<string, unknown>;
    meta.benchmark_symbols = ["000905.SH"];
    const benchmarkCalls: Array<{ symbol?: string; start?: string; end?: string }> = [];
    const client = detailClient({
      experimentResults: async () => payload,
      datasetBenchmark: async (_version, params) => {
        benchmarkCalls.push(params);
        return { ...benchmarkResponse(), symbol: "000905.SH" };
      },
    });
    const wrapper = await mountAt(StrategyDetailPage, client, `/strategies/${EX}`);
    await flushPromises();
    expect(benchmarkCalls.length).toBeGreaterThan(0);
    expect(benchmarkCalls[0]?.symbol).toBe("000905.SH");
    expect(wrapper.get('[data-testid="chart-net-value-legend"]').text()).toContain(
      "基准 000905.SH",
    );
  });

  it("非 walk-forward 实验：显式'未发布该产物'，不渲染结论图表", async () => {
    const client = detailClient({
      experimentResults: async () => ({
        experiment_id: EX,
        manifest: { experiment_id: EX, status: "ACCEPTED" },
        metrics: null,
        stability_report: null,
      }),
    });
    const wrapper = await mountAt(StrategyDetailPage, client, `/strategies/${EX}`);
    await flushPromises();
    expect(wrapper.get('[data-testid="detail-empty"]').text()).toContain("未发布 walk-forward 产物");
    expect(wrapper.find('[data-testid="chart-net-value"]').exists()).toBe(false);
  });

  it("报告入口：pin I6 探测 200 → 链接并如实标注摘要表", async () => {
    const wrapper = await mountAt(StrategyDetailPage, detailClient(), `/strategies/${EX}`);
    await flushPromises();
    const link = wrapper.get('[data-testid="report-link"]');
    expect(link.attributes("href")).toContain(`/api/v1/experiments/${EX}/report`);
    expect(wrapper.text()).toContain("摘要表");
  });

  it("报告缺失（pin I6 探测 404）→ 显式缺失态", async () => {
    const wrapper = await mountAt(
      StrategyDetailPage,
      detailClient({ probeExperimentReport: async () => false }),
      `/strategies/${EX}`,
    );
    await flushPromises();
    expect(wrapper.text()).toContain("无已发布报告");
  });

  it("tearsheet 结构：指标卡/折选择器/情景页签/折表/回撤与逐折图/溯源条", async () => {
    const wrapper = await mountAt(StrategyDetailPage, detailClient(), `/strategies/${EX}`);
    await flushPromises();

    // 指标卡：逐字读当前情景聚合（只有 pct/num 格式化）。
    const cards = wrapper.get('[data-testid="metric-cards"]');
    expect(cards.text()).toContain("12.00%");
    expect(cards.text()).toContain("18.00%");
    expect(cards.text()).toContain("1.40");
    expect(cards.text()).toContain("OOS 观测 100");

    // 折选择器：只列 executed 折（小写比较），failed_preflight 不进。
    const options = wrapper.findAll('[data-testid="fold-selector"] option');
    expect(options).toHaveLength(1);
    expect((options[0].element as HTMLSelectElement).value).toBe(FOLD);

    // 情景页签来自 fold_metrics 情景集合，canonical full_cost 首选且激活。
    const tabs = wrapper.get('[data-testid="scenario-tabs"]');
    expect(tabs.text()).toContain("full_cost");
    expect(tabs.findAll("button")).toHaveLength(1);

    // 回撤图同样钉在当前折上；逐折柱状图存在。
    expect(wrapper.get('[data-testid="chart-underwater"]').attributes("data-fold")).toBe(FOLD);
    expect(wrapper.find('[data-testid="chart-fold-bars"]').exists()).toBe(true);

    // 折表：首列短 id + 全哈希 title；逐字段的 pct/num 格式化值。
    const table = wrapper.get('[data-testid="fold-table"]');
    const foldCell = table.get(`code[title="${FOLD}"]`);
    expect(foldCell.text()).toBe("f".repeat(8));
    expect(table.text()).toContain("2.00%");
    expect(table.text()).toContain("-3.00%");
    expect(table.text()).toContain("1.10");
    expect(table.text()).toContain("0.40%");
    expect(table.text()).toContain("1.60%");
    expect(table.text()).toContain("1.00%");
    expect(table.text()).toContain("30.00%");

    // 溯源条：三快照 + code_commit + 折调度/折结果哈希 + 产物计数。
    const strip = wrapper.get('[data-testid="provenance-strip"]');
    for (const hash of [
      "c".repeat(40),
      "1".repeat(64),
      "2".repeat(64),
      "3".repeat(64),
      "s".repeat(64),
      "o".repeat(64),
    ]) {
      expect(strip.find(`code[title="${hash}"]`).exists()).toBe(true);
    }
    expect(strip.text()).toContain("产物 1 项");
  });
});
