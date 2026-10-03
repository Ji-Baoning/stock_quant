import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPortalRouter } from "../src/router";
import EmptyState from "../src/components/EmptyState.vue";
import Skeleton from "../src/components/Skeleton.vue";

function mountWithRouter(component: Parameters<typeof mount>[0], props: Record<string, unknown> = {}) {
  return mount(component, { props, global: { plugins: [createPortalRouter()] } });
}

describe("EmptyState（空态是一级场景，诚实引导）", () => {
  it("渲染标题与原因说明", () => {
    const wrapper = mountWithRouter(EmptyState, {
      title: "尚无已发布实验",
      description: "data/experiments 为空；发起一次 research run 并发布后，结论将在此展示。",
    });
    expect(wrapper.get('[data-testid="empty-state"]').text()).toContain("尚无已发布实验");
    expect(wrapper.get('[data-testid="empty-state"]').text()).toContain("data/experiments 为空");
  });

  it("给出下一步动作链接（label 与 to 成对才渲染）", () => {
    const wrapper = mountWithRouter(EmptyState, {
      title: "尚无已发布实验",
      actionLabel: "查看报告页",
      actionTo: "/reports",
    });
    const action = wrapper.get('[data-testid="empty-state-action"]');
    expect(action.text()).toBe("查看报告页");
    expect(action.attributes("href")).toBe("#/reports");
  });

  it("没有动作时不渲染链接", () => {
    const wrapper = mountWithRouter(EmptyState, { title: "无待办" });
    expect(wrapper.find('[data-testid="empty-state-action"]').exists()).toBe(false);
  });
});

describe("Skeleton", () => {
  it("按 rows 渲染骨架行，aria-busy 标记加载中", () => {
    const wrapper = mountWithRouter(Skeleton, { rows: 4 });
    expect(wrapper.get('[data-testid="skeleton"]').attributes("aria-busy")).toBe("true");
    expect(wrapper.findAll(".skeleton-row")).toHaveLength(4);
  });

  it("rows 缺省为 3", () => {
    const wrapper = mountWithRouter(Skeleton);
    expect(wrapper.findAll(".skeleton-row")).toHaveLength(3);
  });
});
