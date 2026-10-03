// web/tests/tokens.spec.ts
// 说明：vitest 5 默认 css:false，其 vitest:css-disable 插件会把任何 .css id
// （包括 .css?raw 这类带查询的 id）在 pre-transform 置空，`?raw` 实际拿到
// 空字符串；因此改用 fs 读取源码文本。另注意 happy-dom 环境里 new URL 产出
// 跨 realm 对象不能直接交给 node:fs，须先转成字符串路径。断言逻辑与规格一致。
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(String(import.meta.url)));
const tokensSource = readFileSync(resolve(here, "../src/styles/tokens.css"), "utf8");
const stylesSource = readFileSync(resolve(here, "../src/styles.css"), "utf8");

const REQUIRED_TOKENS = [
  "--color-bg",
  "--color-surface",
  "--color-surface-muted",
  "--color-border",
  "--color-border-strong",
  "--color-text-secondary",
  "--color-text",
  "--color-primary",
  "--color-pass",
  "--color-block",
  "--color-pending",
  "--color-info",
  "--color-up",
  "--color-down",
  "--color-benchmark",
  "--color-inverse-bg",
  "--color-inverse-text",
  "--color-block-bg",
  "--color-block-border",
  "--color-pending-bg",
  "--color-pending-border",
  "--space-1",
  "--space-4",
  "--radius-card",
  "--shadow-card",
  "--font-size-body",
  "--font-size-kpi",
  "--content-max-width",
  "--sidebar-width",
];

describe("设计 token 层（规格 §6.1）", () => {
  it("tokens.css 定义全部必需 token", () => {
    for (const token of REQUIRED_TOKENS) {
      expect(tokensSource, `缺少 ${token}`).toContain(`${token}:`);
    }
  });

  it("dark 主题只预留同名变量结构（裁定 4：无切换入口）", () => {
    expect(tokensSource).toContain('[data-theme="dark"]');
    // 结构性预留：dark 块里没有切换/媒体查询逻辑。
    expect(tokensSource).not.toContain("prefers-color-scheme");
  });

  it("全局样式不硬编码十六进制颜色（只消费 token）", () => {
    const hexColors = stylesSource.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [];
    expect(hexColors, `styles.css 残留硬编码颜色：${hexColors.join(",")}`).toEqual([]);
  });
});
