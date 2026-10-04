// web/tests/strategies-page.spec.ts
import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import type { ApiClient } from "../src/api/client";
import type { ExperimentSummaryRow } from "../src/api/types";
import StrategiesPage from "../src/pages/StrategiesPage.vue";
import { fakeClient, mountAt } from "./helpers";

function summariesClient(rows: ExperimentSummaryRow[]): ApiClient {
  return fakeClient({ experimentSummaries: async () => ({ summaries: rows }) });
}

const WF_ROW = {
  experiment_id: "e".repeat(64),
  status: "ACCEPTED",
  dataset_version: "a".repeat(64),
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
};

describe("策略列表（BRAIN 模型：列表列即门槛）", () => {
  it("渲染结论徽章、假设、canonical 情景指标与数据版本链接", async () => {
    const client = summariesClient([WF_ROW]);
    const wrapper = await mountAt(StrategiesPage, client, "/strategies");
    await flushPromises();
    const row = wrapper.get('[data-testid="strategy-row"]');
    expect(row.text()).toContain("动量延续假设");
    expect(row.get('[data-testid="state-badge"]').text()).toBe("STABLE");
    expect(row.text()).toContain("12.00%");  // aggregate_return 0.12 → pct()
    expect(row.text()).toContain("1.4");     // sharpe_zero_rf → num()
    expect(row.text()).toContain("-8.00%");  // max_per_fold_drawdown -0.08 → pct()
    expect(row.text()).toContain("35.00%");  // mean_turnover 0.35 → pct()
    expect(row.find('a[href*="/versions/"]').exists()).toBe(true);
  });

  it("非 walk-forward 实验显示'无 walk-forward 结论'而非空白", async () => {
    const legacy = { ...WF_ROW, experiment_id: "d".repeat(64), stability_conclusion: null, aggregates: null, display_extremes: null, hypothesis: null };
    const wrapper = await mountAt(StrategiesPage, summariesClient([legacy]), "/strategies");
    await flushPromises();
    expect(wrapper.get('[data-testid="strategy-row"]').text()).toContain("无 walk-forward 结论");
  });

  it("注册表为空 → 空态引导（不造数据）", async () => {
    const wrapper = await mountAt(StrategiesPage, summariesClient([]), "/strategies");
    await flushPromises();
    expect(wrapper.get('[data-testid="strategy-empty"]').text()).toContain("尚无已发布实验");
  });
});
