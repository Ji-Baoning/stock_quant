// web/tests/state-badge.spec.ts
import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPortalRouter } from "../src/router";
import StateBadge, { type BadgeKind } from "../src/components/StateBadge.vue";

function mountBadge(kind: BadgeKind, value: string) {
  return mount(StateBadge, {
    props: { kind, value },
    global: { plugins: [createPortalRouter()] },
  });
}

describe("StateBadge（语义状态徽章）", () => {
  it("验收四态映射（真实状态词原文显示）", () => {
    const cases: Array<[string, string]> = [
      ["ACCEPTED", "pass"],
      ["REJECTED", "block"],
      ["PENDING_CONFIRMATION", "pending"],
      ["UNVERIFIED", "neutral"],
    ];
    for (const [value, tone] of cases) {
      const wrapper = mountBadge("acceptance", value);
      expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain(`badge-state--${tone}`);
      expect(wrapper.get('[data-testid="state-badge"]').text()).toBe(value);
    }
  });

  it("walk-forward 三态结论映射", () => {
    const cases: Array<[string, string]> = [
      ["STABLE", "pass"],
      ["UNSTABLE", "block"],
      ["INCONCLUSIVE", "pending"],
    ];
    for (const [value, tone] of cases) {
      const wrapper = mountBadge("conclusion", value);
      expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain(`badge-state--${tone}`);
    }
  });

  it("任务状态映射", () => {
    const cases: Array<[string, string]> = [
      ["SUCCEEDED", "pass"],
      ["FAILED", "block"],
      ["QUEUED", "pending"],
      ["RUNNING", "pending"],
      ["CANCELLED_BY_SHUTDOWN", "neutral"],
    ];
    for (const [value, tone] of cases) {
      const wrapper = mountBadge("job", value);
      expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain(`badge-state--${tone}`);
    }
  });

  it("挑战裁决四态映射（FAILED 与 REJECTED 同为阻断红）", () => {
    const cases: Array<[string, string]> = [
      ["PROMOTED", "pass"],
      ["REJECTED", "block"],
      ["INCONCLUSIVE_RESEARCH_ONLY", "pending"],
      ["FAILED", "block"],
    ];
    for (const [value, tone] of cases) {
      const wrapper = mountBadge("challenge", value);
      expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain(
        `badge-state--${tone}`,
      );
      expect(wrapper.get('[data-testid="state-badge"]').text()).toBe(value);
    }
  });

  it("未知值诚实回落：neutral + 原文显示（不翻译、不隐藏）", () => {
    const wrapper = mountBadge("acceptance", "SOMETHING_NEW");
    expect(wrapper.get('[data-testid="state-badge"]').classes()).toContain("badge-state--neutral");
    expect(wrapper.get('[data-testid="state-badge"]').text()).toBe("SOMETHING_NEW");
  });

  it("title 默认回退到值本身，可显式覆盖", () => {
    const plain = mountBadge("job", "RUNNING");
    expect(plain.get('[data-testid="state-badge"]').attributes("title")).toBe("RUNNING");
    const titled = mount(StateBadge, {
      props: { kind: "job", value: "RUNNING", title: "任务仍在运行" },
      global: { plugins: [createPortalRouter()] },
    });
    expect(titled.get('[data-testid="state-badge"]').attributes("title")).toBe("任务仍在运行");
  });
});
