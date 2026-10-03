// web/tests/card-kpi.spec.ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import Card from "../src/components/Card.vue";
import KpiCard from "../src/components/KpiCard.vue";

describe("Card", () => {
  it("渲染标题与默认插槽；无标题时不渲染标题元素", () => {
    const withTitle = mount(Card, { props: { title: "① 数据现在可信吗？", testid: "block-data-trust" }, slots: { default: "<p>内容</p>" } });
    expect(withTitle.get('[data-testid="block-data-trust"]').find("h2").text()).toBe("① 数据现在可信吗？");
    expect(withTitle.get('[data-testid="block-data-trust"]').text()).toContain("内容");

    const noTitle = mount(Card, { slots: { default: "<p>内容</p>" } });
    expect(noTitle.get('[data-testid="card"]').find("h2").exists()).toBe(false);
  });
});

describe("KpiCard", () => {
  it("渲染 label/value/compare，value 用等宽数字类", () => {
    const wrapper = mount(KpiCard, {
      props: { label: "质量问题", value: "3 条", compare: "基准 000300.SH" },
    });
    expect(wrapper.get('[data-testid="kpi-card"]').text()).toContain("质量问题");
    expect(wrapper.get('[data-testid="kpi-card"]').text()).toContain("3 条");
    expect(wrapper.get(".kpi-value").classes()).toContain("kpi-num");
    expect(wrapper.find(".kpi-compare").exists()).toBe(true);
  });

  it("tone 由调用方显式给出（组件不做正负判定）", () => {
    const up = mount(KpiCard, { props: { label: "超额", value: "+2.1%", tone: "up" } });
    expect(up.get(".kpi-value").classes()).toContain("kpi-value--up");
    const none = mount(KpiCard, { props: { label: "换手", value: "4.3" } });
    expect(none.get(".kpi-value").classes()).not.toContain("kpi-value--up");
  });

  it("compare 缺省时不渲染对照行", () => {
    const wrapper = mount(KpiCard, { props: { label: "换手", value: "4.3" } });
    expect(wrapper.find(".kpi-compare").exists()).toBe(false);
  });
});
