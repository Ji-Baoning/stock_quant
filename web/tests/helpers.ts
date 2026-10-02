import { mount, type VueWrapper } from "@vue/test-utils";
import type { Component } from "vue";
import { createPortalRouter } from "../src/router";

export function mountPage(component: Component, client: unknown): VueWrapper {
  return mount(component, {
    global: { plugins: [createPortalRouter()] },
  });
}
