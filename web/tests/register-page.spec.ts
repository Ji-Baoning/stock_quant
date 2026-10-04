import { describe, expect, it } from "vitest";
import { flushPromises } from "@vue/test-utils";
import RegisterPage from "../src/pages/RegisterPage.vue";
import { fakeClient, mountAt } from "./helpers";

describe("注册台（S3a：生成草稿，人跑冻结命令）", () => {
  it("诚实边界文案固定显示（裁定 2）", async () => {
    const wrapper = await mountAt(RegisterPage, fakeClient(), "/strategies/register");
    expect(wrapper.get('[data-testid="honesty-note"]').text()).toContain("web 不写文件");
    expect(wrapper.get('[data-testid="honesty-note"]').text()).toContain("由人负责");
  });

  it("表单编辑实时更新 YAML 与命令预览", async () => {
    const wrapper = await mountAt(RegisterPage, fakeClient(), "/strategies/register");
    await wrapper.find('[data-testid="field-hypothesis"]').setValue("测试假设：动量延续");
    await flushPromises();
    expect(wrapper.get('[data-testid="yaml-preview"]').text()).toContain("测试假设：动量延续");
    // 默认档是 engineering → 命令走 debug 诊断入口，不是 research run。
    expect(wrapper.get('[data-testid="command-preview"]').text()).toContain(
      "backtest momentum_60d",
    );
    expect(wrapper.get('[data-testid="command-preview"]').text()).toContain("--engineering");
    // 切到 research 档后命令换入口（同一份 YAML 的两个合法入口，不得混淆）。
    await wrapper.find('[data-testid="field-trust-mode"]').setValue("research");
    await flushPromises();
    expect(wrapper.get('[data-testid="command-preview"]').text()).toContain("research run");
  });

  it("占位假设被拦截：form-errors 显示占位提示", async () => {
    const wrapper = await mountAt(RegisterPage, fakeClient(), "/strategies/register");
    await flushPromises();
    expect(wrapper.get('[data-testid="form-errors"]').text()).toContain("请填写真实假设");
  });
});
