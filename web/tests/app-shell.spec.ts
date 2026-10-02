import { describe, expect, it } from "vitest";
import App from "../src/App.vue";
import { NAV_ITEMS } from "../src/router";
import { fakeClient, mountPage } from "./helpers";

describe("应用骨架", () => {
  it("顶栏按 §10.3 固定顺序渲染五个导航入口", async () => {
    const wrapper = mountPage(App, fakeClient());
    await new Promise((resolve) => setTimeout(resolve, 0));
    const links = wrapper.get('[data-testid="main-nav"]').findAll("a");
    expect(links.map((link) => link.text())).toEqual(NAV_ITEMS.map((item) => item.label));
    expect(links.map((link) => link.attributes("href"))).toEqual(
      NAV_ITEMS.map((item) => `#${item.path}`),
    );
  });
});
