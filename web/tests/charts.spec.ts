// web/tests/charts.spec.ts —— Task 5：图表薄封装（ECharts）。
// happy-dom 无真实 canvas：断言以容器 + props + data-fold 逐折证据为主，
// canvas 真实渲染属于浏览器端行为，不在单测覆盖范围。
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPortalRouter } from "../src/router";
import LineChart from "../src/components/LineChart.vue";
import BarChart from "../src/components/BarChart.vue";

describe("图表薄封装（ECharts）", () => {
  it("LineChart 渲染容器并携带逐折证据属性", () => {
    const wrapper = mount(LineChart, {
      props: {
        labels: ["2026-01-05", "2026-01-06"],
        series: [
          { name: "策略（费后）", data: [1.0, 1.01] },
          { name: "基准 000300.SH", data: [1.0, 0.99], dashed: true },
        ],
        testid: "chart-net-value",
        fold: "f".repeat(64),
      },
      global: { plugins: [createPortalRouter()] },
    });
    expect(wrapper.get('[data-testid="chart-net-value"]').attributes("data-fold")).toBe("f".repeat(64));
    expect(wrapper.props("series")).toHaveLength(2);
  });

  it("BarChart 支持按正负着色（红涨绿跌）", () => {
    const wrapper = mount(BarChart, {
      props: {
        labels: ["fold-0", "fold-1"],
        series: [{ name: "折收益", data: [0.02, -0.01] }],
        colorBySign: true,
      },
      global: { plugins: [createPortalRouter()] },
    });
    expect(wrapper.find('[data-testid="bar-chart"]').exists()).toBe(true);
  });
});
