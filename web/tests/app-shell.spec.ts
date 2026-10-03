import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import App from "../src/App.vue";
import { NAV_GROUPS, NAV_ITEMS, createPortalRouter } from "../src/router";
import { fakeClient } from "./helpers";

describe("应用骨架（侧边栏分组导航，v3 规格 §4）", () => {
  it("侧边栏渲染分组与独立项；扁平顺序 = NAV_ITEMS", async () => {
    const wrapper = mount(App, {
      props: { client: fakeClient() },
      global: { plugins: [createPortalRouter()] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const nav = wrapper.get('[data-testid="main-nav"]');
    const links = nav.findAll("a");
    expect(links.map((link) => link.text())).toEqual(NAV_ITEMS.map((item) => item.label));
    expect(links.map((link) => link.attributes("href"))).toEqual(
      NAV_ITEMS.map((item) => `#${item.path}`),
    );
    // 分组标签存在且顺序固定（规格 §4：数据 → 运维；报告为独立项直到 S1 下线）。
    const groupLabels = nav.findAll(".side-group-label").map((label) => label.text());
    expect(groupLabels).toEqual(NAV_GROUPS.filter((g) => g.label !== null).map((g) => g.label));
  });

  it("根路由渲染决策台，导航第一项为决策台", async () => {
    const wrapper = mount(App, {
      props: { client: fakeClient() },
      global: { plugins: [createPortalRouter()] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.get('[data-testid="main-nav"]').find("a").attributes("href")).toBe("#/");
  });

  it("顶栏不再承载导航，但保留指纹与已解析版本区", async () => {
    const wrapper = mount(App, {
      props: { client: fakeClient() },
      global: { plugins: [createPortalRouter()] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.get('[data-testid="top-bar"]').find("nav").exists()).toBe(false);
    expect(wrapper.find('[data-testid="project-fingerprint"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="top-resolved-version"]').exists()).toBe(true);
  });
});
