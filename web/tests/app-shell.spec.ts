import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import App from "../src/App.vue";
import { NAV_ITEMS, createPortalRouter } from "../src/router";
import { fakeClient } from "./helpers";

describe("应用骨架", () => {
  it("顶栏按 §10.3 固定顺序渲染五个导航入口", async () => {
    // 最终版 App 自建默认 client 会遮蔽注入；以 prop 传 fake client 保持全离线。
    const wrapper = mount(App, {
      props: { client: fakeClient() },
      global: { plugins: [createPortalRouter()] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const links = wrapper.get('[data-testid="main-nav"]').findAll("a");
    expect(links.map((link) => link.text())).toEqual(NAV_ITEMS.map((item) => item.label));
    expect(links.map((link) => link.attributes("href"))).toEqual(
      NAV_ITEMS.map((item) => `#${item.path}`),
    );
  });
});
